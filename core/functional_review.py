# -*- coding: utf-8 -*-
"""
FunctionalReviewer v1.1 —— LLM 功能审查增强 + 逐条粗筛（审计的"AI 沟通"层）

背景：用户提出审计时，若只机械跑代码审计，无法把"全功能审查"（创新传播、
代码结构复杂度、回归一致性等）交给 AI 做语义判断。本模块在审计基础上叠加一层
"功能审查"：
  1. 接收 ReviewAdvisor 的策略建议（增量/全量 + 重点维度）；
  2. 收集图谱统计、11 工具 findings 摘要、变更闭包作为 LLM 上下文；
  3. 让 LLM 对重点功能维度做语义审查，给出"该维度是否健康 + 应重点看什么"；
  4. LLM 不可用时优雅降级：基于静态信号给出结构化结论（不依赖大模型也能用）。

v1.1 新增：逐条粗筛（_screen_findings）
  - 把 findings（含 detail/suggestion）交给 LLM 判三分类：
    疑似误报 / 需确认 / 真问题；
  - 疑似误报条目附带"建议白名单条目"（file/rule/category），供外层用户 AI
    在真实项目上下文里核实后，通过 coderef_whitelist 反馈入白名单；
  - 粗筛只产出"建议标记"，不自动过滤 findings，避免 LLM 误判吞掉真问题；
  - LLM 不可用时返回空粗筛（ran=False），不影响功能审查降级。

设计约束：
  - 纯标准库 + 复用 core/llm_integration.py，不引入第三方新依赖；
  - 复用 CodeKnowledgeGraph / ReviewAdvisor，不重复造轮子；
  - 所有面向使用者的可读文本一律中文；
  - LLM 调用带显式超时与容错；永久性错误（无 API Key）不重试，临时性错误（网络/超时）可重试；
  - magic number 集中定义为模块级常量。

作者: CodeRef-AI Team
版本: v1.1
"""

import json
import time
from typing import Dict, List, Optional

from loguru import logger

from core.llm_integration import LLMIntegration


# ═══════════════════════════════════════════════════════════════════
# 模块级常量（集中管理 magic number）
# ═══════════════════════════════════════════════════════════════════

# LLM 调用超时（秒）
_LLM_TIMEOUT = 120
# 临时性错误重试次数
_LLM_RETRIES = 2
# 重试间隔（秒）
_LLM_RETRY_DELAY = 2.0

# LLM 审查输出最大 token
_MAX_TOKENS = 2048

# 提交给 LLM 的 findings 摘要上限（按 tier 高优先截断）
_MAX_SUMMARY_FINDINGS = 40

# 逐条粗筛单次提交的 findings 上限（防止 single prompt 超长）
_MAX_SCREEN_BATCH = 40

# 粗筛三分类标记
_VERDICT_SUSPECTED_FP = "suspected_fp"   # 疑似误报
_VERDICT_NEEDS_REVIEW = "needs_review"   # 需人工确认
_VERDICT_CONFIRMED = "confirmed"         # 真问题

# 维度健康度阈值：静态信号超阈值视为"需关注"
_COMPLEXITY_NODE_THRESHOLD = 80   # 图谱节点数
_CROSS_MODULE_EDGE_THRESHOLD = 60  # 跨模块边数

# 功能维度定义（label + 检查要点）
FUNCTIONAL_DIMENSIONS = {
    "innovation_propagation": {
        "label": "创新设计传播",
        "check": "高频复用模式是否在新增/变更模块中保持一致传播，是否存在应统一而未统一的风格漂移。",
    },
    "architecture_complexity": {
        "label": "代码结构复杂度",
        "check": "模块耦合度、函数长度、依赖结构是否健康，变更是否引入结构性债务。",
    },
    "regression_risk": {
        "label": "回归与一致性",
        "check": "变更是否破坏既有契约、命名风格、跨模块调用一致性，是否存在回归风险。",
    },
    "security_hygiene": {
        "label": "安全卫生",
        "check": "是否存在敏感信息、注入、弱加密等安全卫生问题（与静态工具对照）。",
    },
    "dependency_health": {
        "label": "依赖健康",
        "check": "依赖版本、漏洞修复状态是否健康。",
    },
}


def _llm_available(llm: LLMIntegration) -> bool:
    if llm is None:
        return False
    client = getattr(llm, "client", None)
    config = getattr(llm, "config", None)
    api_key = getattr(config, "api_key", "") if config is not None else ""
    return bool(client) and bool(api_key)


class FunctionalReviewer:
    """功能审查增强器：把审计从"代码维度"提升到"功能/语义维度"。"""

    def __init__(self, llm: Optional[LLMIntegration] = None):
        self.llm = llm if llm is not None else LLMIntegration()

    # ─── 上下文收集（静态信号，供 LLM 与降级共用） ───

    def _collect_context(self, project_path: str, strategy: dict,
                         kg_stats: Optional[dict]) -> dict:
        """收集功能审查所需的静态上下文。"""
        ctx: dict = {
            "strategy": strategy.get("strategy", "full"),
            "strategy_reason": strategy.get("reason", ""),
            "explicit_strategy": strategy.get("explicit", False),
            "dimensions_focus": strategy.get("dimensions_focus", []),
            "changes": strategy.get("changes", {}),
            "impact_count": strategy.get("impact", {}).get("count", 0),
            "kg": strategy.get("kg", {}),
            "kg_stats": kg_stats or {},
        }

        # 静态复杂度信号（不依赖 LLM）
        signals: dict = {}
        node_count = (kg_stats or {}).get("node_count", 0)
        edge_count = (kg_stats or {}).get("edge_count", 0)
        signals["node_count"] = node_count
        signals["edge_count"] = edge_count
        signals["complexity_warning"] = node_count > _COMPLEXITY_NODE_THRESHOLD
        cross = 0
        for etype, cnt in ((kg_stats or {}).get("edge_types", {}) or {}).items():
            if etype in ("CALLS", "IMPORTS"):
                cross += cnt
        signals["cross_module_edges"] = cross
        signals["cross_module_warning"] = cross > _CROSS_MODULE_EDGE_THRESHOLD
        ctx["signals"] = signals
        return ctx

    def _summarize_findings(self, pipe_result) -> List[dict]:
        """把 11 工具 findings 汇总为精简列表（按 tier 高优先截断）。

        相比早期版本，额外携带 detail / suggestion，使 LLM 粗筛能看到
        "这条为什么可能是误报"的判断依据，而非仅凭标题下结论。
        """
        findings = getattr(pipe_result, "findings", []) or []
        ordered = sorted(findings, key=lambda f: (
            {"high": 0, "medium": 1, "low": 2}.get(
                getattr(f, "tier", None).value if getattr(f, "tier", None) else "low", 3)))
        out = []
        for f in ordered[:_MAX_SUMMARY_FINDINGS]:
            out.append({
                "tier": getattr(f, "tier", None).value if getattr(f, "tier", None) else "low",
                "tool": getattr(f, "tool", ""),
                "category": getattr(f, "category", ""),
                "title": getattr(f, "title", ""),
                "file": getattr(f, "file_path", ""),
                "detail": getattr(f, "detail", "") or "",
                "suggestion": getattr(f, "suggestion", "") or "",
            })
        return out

    # ─── LLM 审查 ───

    def _build_prompt(self, project_path: str, ctx: dict, findings: List[dict]) -> str:
        dims = ctx.get("dimensions_focus", [])
        dim_lines = []
        for d in dims:
            spec = FUNCTIONAL_DIMENSIONS.get(d.get("dimension", ""), {})
            dim_lines.append(f"- {d.get('label', d.get('dimension', ''))}：{spec.get('check', d.get('reason', ''))}")
        if not dim_lines:
            dim_lines.append("- 综合评估项目整体功能健康度")

        change_lines = ctx.get("changes", {})
        changed = change_lines.get("changed", [])
        added = change_lines.get("added", [])
        deleted = change_lines.get("deleted", [])
        if ctx.get("explicit_strategy"):
            # 显式指定策略：未做自动变更/影响闭包判定，如实说明，避免 LLM 把空结构误判为"无变更"
            change_desc = "用户显式指定审计策略，未做自动变更与影响闭包判定"
        else:
            change_desc = (
                f"变更 {len(changed)}、新增 {len(added)}、删除 {len(deleted)} 个文件"
                if (changed or added or deleted) else "未检测到代码变更"
            )

        find_desc = "\n".join(
            f"  [{f['tier']}] {f['tool']}/{f['category']}: {f['title']} @ {f['file']}"
            for f in findings) or "  （无发现）"

        return f"""请对以下项目做一次"功能层面"审查（不是逐行代码审查，而是判断各功能维度是否健康）。

项目路径: {project_path}
审计策略建议: {ctx['strategy']}（{ctx['strategy_reason']}）
变更情况: {change_desc}
影响闭包节点数: {ctx['impact_count']}
知识图谱: 存在={ctx['kg'].get('exists')}, 构建时间={ctx['kg'].get('built_at') or '无'}, 是否过期={ctx['kg'].get('stale')}

## 静态复杂度信号
节点数: {ctx['signals']['node_count']}, 边数: {ctx['signals']['edge_count']}
跨模块边数: {ctx['signals']['cross_module_edges']}
复杂度告警: {"是" if ctx['signals']['complexity_warning'] else "否"}
跨模块耦合告警: {"是" if ctx['signals']['cross_module_warning'] else "否"}

## 本次审计重点功能维度
{chr(10).join(dim_lines)}

## 静态工具发现摘要（供你参考，勿逐条复述）
{find_desc}

## 输出要求
请返回 JSON 对象，字段如下：
- "dimension_reviews": 数组，每个元素为对"本次重点维度"的审查结论：
  {{
    "dimension": "维度标识",
    "label": "维度名称",
    "verdict": "healthy" 或 "attention",
    "summary": "一句话结论（中文，≤40字）",
    "detail": "具体判断依据（中文）",
    "suggestion": "一句可执行建议（中文）"
  }}
- "overall": {{ "verdict": "healthy"/"attention", "summary": "一句话整体结论" }}
- "recommendation": "给用户的下一步建议（中文，≤60字）"

严格只返回 JSON 对象，不要输出任何其它内容。
"""

    def _call_llm(self, prompt: str) -> Optional[dict]:
        """调用 LLM 并解析 JSON；带显式超时与临时性错误重试。"""
        messages = [
            {
                "role": "system",
                "content": "你是一位资深软件架构与功能审查专家，擅长从功能维度评估代码健康度。你只返回 JSON 对象。",
            },
            {"role": "user", "content": prompt},
        ]
        last_err = None
        for attempt in range(_LLM_RETRIES + 1):
            try:
                response = self.llm.chat_completion(
                    messages, max_tokens=_MAX_TOKENS, temperature=0.2,
                    timeout=_LLM_TIMEOUT)
            except Exception as e:
                last_err = e
                # 临时性错误才重试；永久性错误（无 API Key）不重试
                if self.llm._is_retryable_llm_error(e):
                    logger.warning(f"[FunctionalReview] LLM 临时错误，重试 {attempt+1}/{_LLM_RETRIES}: {e}")
                    time.sleep(_LLM_RETRY_DELAY)
                    continue
                logger.error(f"[FunctionalReview] LLM 永久性错误，不重试: {e}")
                return None
            if response.startswith("LLM调用错误"):
                logger.warning(f"[FunctionalReview] LLM 调用失败: {response[:200]}")
                return None
            data = self.llm._try_parse_json(response)
            if isinstance(data, dict):
                return data
            logger.warning(f"[FunctionalReview] LLM 返回非 JSON 对象，尝试修复后仍失败")
            return None
        logger.error(f"[FunctionalReview] LLM 调用最终失败: {last_err}")
        return None

    def _degrade(self, ctx: dict, findings: List[dict]) -> dict:
        """LLM 不可用时的静态降级结论（不依赖大模型）。"""
        signals = ctx.get("signals", {})
        dim_reviews = []
        for d in ctx.get("dimensions_focus", []):
            dim = d.get("dimension", "")
            if dim == "architecture_complexity":
                warn = signals.get("complexity_warning") or signals.get("cross_module_warning")
                verdict = "attention" if warn else "healthy"
                summary = "结构复杂度偏高，建议关注耦合" if warn else "结构复杂度在可接受范围"
                detail = (f"节点 {signals.get('node_count')}、跨模块边 {signals.get('cross_module_edges')}"
                          if warn else "未触发复杂度/耦合告警阈值")
                dim_reviews.append({
                    "dimension": dim, "label": d.get("label", dim),
                    "verdict": verdict, "summary": summary,
                    "detail": detail,
                    "suggestion": "优先做结构重构/拆分高耦合模块" if warn else "维持现状",
                })
            elif dim in ("innovation_propagation",):
                dim_reviews.append({
                    "dimension": dim, "label": d.get("label", dim),
                    "verdict": "attention",
                    "summary": "创新传播需人工结合设计文档核验",
                    "detail": "创新传播依赖语义判断，LLM 不可用时降级为人工核验。",
                    "suggestion": "结合创新设计文档人工核对传播一致性",
                })
            elif dim == "regression_risk":
                dim_reviews.append({
                    "dimension": dim, "label": d.get("label", dim),
                    "verdict": "attention",
                    "summary": "变更需重点核验回归风险",
                    "detail": "小范围变更时回归/一致性风险最需关注，建议结合 diff 逐项复核。",
                    "suggestion": "对变更文件及其调用方做回归核对",
                })
        # 整体结论
        high = sum(1 for f in findings if f.get("tier") == "high")
        overall = {
            "verdict": "attention" if (high or any(r["verdict"] == "attention" for r in dim_reviews)) else "healthy",
            "summary": f"静态发现 HIGH {high} 条" + (f"，{len(dim_reviews)} 个维度需关注" if dim_reviews else ""),
        }
        return {
            "llm_available": False,
            "dimension_reviews": dim_reviews,
            "overall": overall,
            "recommendation": "LLM 不可用，已降级为静态功能审查。建议配置 API Key 后重跑以获得语义级功能结论。",
            "degraded": True,
        }

    # ─── 逐条粗筛（LLM 对 findings 三分类） ───

    @staticmethod
    def _empty_screen() -> dict:
        """无 LLM / 无 findings 时的空粗筛结果。"""
        return {
            "llm_available": False,
            "ran": False,
            "verdicts": {},          # find_key -> verdict
            "reasons": {},           # find_key -> reason
            "candidates": [],        # 疑似误报的建议白名单条目（供用户 AI 反馈）
            "summary": {"suspected_fp": 0, "needs_review": 0, "confirmed": 0},
        }

    @staticmethod
    def _find_key(f: dict, idx: int) -> str:
        """生成 findings 的稳定键（用于把 LLM 分类结果对应回原条目）。"""
        return f"f{idx}"

    @staticmethod
    def _suggest_whitelist_entry(f: dict) -> dict:
        """从一条疑似误报中构造建议白名单条目（file/rule/category AND 逻辑）。

        仅回填非空字段，白名单条目为空字段不参与 AND 匹配。
        """
        entry = {}
        fp = f.get("file") or ""
        tl = f.get("title") or ""
        cat = f.get("category") or ""
        # 文件短化：取 project 相对路径的 basename 附近，避免整条绝对路径过宽
        if fp:
            # 取最后两段（如 core/ast_parser.py），兼顾跨目录与叶子文件
            parts = [p for p in fp.replace("\\", "/").split("/") if p]
            entry["file"] = "/".join(parts[-2:]) if len(parts) >= 2 else fp
        if tl:
            # 标题取前 24 字符作为 rule 子串，避免整句过长导致误伤其它规则
            entry["rule"] = tl[:24]
        if cat:
            entry["category"] = cat
        return entry

    def _build_screen_prompt(self, project_path: str, findings: List[dict]) -> str:
        """构造粗筛 prompt：让 LLM 对每条 finding 判三分类。

        设计要点：
          - findings 逐条带 detail/suggestion，供 LLM 判断"是否听起来像误报"；
          - 输出严格三分类，且对 suspected_fp 给出一句 reason + 建议白名单条目；
          - 强调"拿不准就 needs_review"，避免 LLM 过度自信把真问题当误报。
        """
        rows = []
        for i, f in enumerate(findings):
            rows.append(
                f"[{i}] tier={f.get('tier', 'low')} tool={f.get('tool', '')}"
                f" category={f.get('category', '')}\n"
                f"    file={f.get('file', '')}\n"
                f"    title={f.get('title', '')}\n"
                f"    detail={f.get('detail', '')}\n"
                f"    suggestion={f.get('suggestion', '')}"
            )
        find_text = "\n".join(rows) or "  （无发现）"
        return f"""请对以下静态审计 findings 做一次"粗筛"：把每条判定为下列三分类之一。

项目路径: {project_path}

## 判定标准
- {_VERDICT_CONFIRMED}（真问题）：该条描述与代码实际行为匹配，是真实缺陷/风险，应保留。
- {_VERDICT_NEEDS_REVIEW}（需人工确认）：拿不准、或依赖项目上下文才能判断，交给用户核实。
- {_VERDICT_SUSPECTED_FP}（疑似误报）：从描述看很可能是审计工具自身的启发式误报，或属正常设计
  （如 sys.path 动态注入、unittest 标准命名、检测规则自身正则、正常防御性 except 等）。

## 原则
- 你只能依据 findings 自带信息判断，无法访问源码；因此**拿不准时一律判 {_VERDICT_NEEDS_REVIEW}**，
  不要把不确定的条目判成 {_VERDICT_SUSPECTED_FP} 或 {_VERDICT_CONFIRMED}。
- 只有"从描述几乎可以确定是工具误报/正常设计"的条目才判 {_VERDICT_SUSPECTED_FP}。

## findings
{find_text}

## 输出要求
严格只返回 JSON 对象，字段：
{{
  "verdicts": {{"[序号]": "{_VERDICT_CONFIRMED}|{_VERDICT_NEEDS_REVIEW}|{_VERDICT_SUSPECTED_FP}", ...}},
  "reasons": {{"[序号]": "对该条为何如此判定的中文依据（≤40字）", ...}},
  "suspect_whitelist": [
    {{"index": "[序号]", "file": "建议 file 子串", "rule": "建议 rule 子串", "category": "建议 category 子串"}}
  ]
}}
只对疑似误报条目提供 suspect_whitelist 条目，其余省略。不要输出任何其它内容。
"""

    def _screen_findings(self, project_path: str, findings: List[dict]) -> dict:
        """对 findings 做 LLM 逐条粗筛；LLM 不可用则返回空粗筛（不降级功能审查）。"""
        if not findings:
            return self._empty_screen()
        if not _llm_available(self.llm):
            return self._empty_screen()

        prompt = self._build_screen_prompt(project_path, findings)
        data = self._call_llm(prompt)
        if data is None:
            return self._empty_screen()

        verdicts = data.get("verdicts") or {}
        reasons = data.get("reasons") or {}
        if not isinstance(verdicts, dict):
            verdicts = {}

        # 归一化 verdict → 白名单候选
        candidates = []
        summary = {"suspected_fp": 0, "needs_review": 0, "confirmed": 0}
        norm_reasons = {}
        for idx, f in enumerate(findings):
            key = self._find_key(f, idx)
            # 兼容 LLM 返回的三种键格式：f0（内部键）/ 0（裸序号）/ [0]（prompt 展示的方括号序号），
            # 避免 prompt 与解析键不一致导致粗筛全部落到 needs_review 默认值。
            v = (verdicts.get(key) or verdicts.get(str(idx)) or verdicts.get(f"[{idx}]")
                 or _VERDICT_NEEDS_REVIEW)
            if v not in (_VERDICT_CONFIRMED, _VERDICT_NEEDS_REVIEW, _VERDICT_SUSPECTED_FP):
                v = _VERDICT_NEEDS_REVIEW
            verdicts[key] = v
            r = (reasons.get(key) or reasons.get(str(idx))
                 or reasons.get(f"[{idx}]") or "")
            if r:
                norm_reasons[key] = r
            summary[v] = summary.get(v, 0) + 1
            if v == _VERDICT_SUSPECTED_FP:
                candidates.append(self._suggest_whitelist_entry(f))

        return {
            "llm_available": True,
            "ran": True,
            "verdicts": verdicts,
            "reasons": norm_reasons,
            "candidates": candidates,
            "summary": summary,
        }

    # ─── 主入口 ───

    def review(self, project_path: str, strategy: dict,
               pipe_result=None, kg_stats: Optional[dict] = None) -> dict:
        """执行功能审查增强。

        Args:
            project_path: 项目绝对路径
            strategy: ReviewAdvisor.advise() 的返回
            pipe_result: 可选，审计管线结果（用于 findings 摘要）
            kg_stats: 可选，知识图谱统计

        Returns:
            {"llm_available": bool, "dimension_reviews": [...],
             "overall": {...}, "recommendation": str, "degraded": bool}
        """
        ctx = self._collect_context(project_path, strategy, kg_stats)
        findings = self._summarize_findings(pipe_result) if pipe_result is not None else []

        # 逐条粗筛：LLM 对 findings 三分类（疑似误报 / 需确认 / 真问题）
        screen = self._screen_findings(project_path, findings)

        if not _llm_available(self.llm):
            logger.info("[FunctionalReview] LLM 不可用，降级为静态功能审查")
            degraded = self._degrade(ctx, findings)
            degraded["screen"] = screen
            return degraded

        prompt = self._build_prompt(project_path, ctx, findings)
        data = self._call_llm(prompt)
        if data is None:
            logger.info("[FunctionalReview] LLM 调用失败，降级为静态功能审查")
            degraded = self._degrade(ctx, findings)
            degraded["screen"] = screen
            return degraded

        # 归一化 LLM 输出
        dim_reviews = data.get("dimension_reviews", [])
        if not isinstance(dim_reviews, list):
            dim_reviews = []
        overall = data.get("overall", {})
        if not isinstance(overall, dict):
            overall = {}
        return {
            "llm_available": True,
            "dimension_reviews": dim_reviews,
            "overall": overall,
            "recommendation": str(data.get("recommendation", "") or ""),
            "degraded": False,
            "screen": screen,
        }


# 全局单例
functional_reviewer = FunctionalReviewer()