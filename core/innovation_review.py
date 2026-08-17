# -*- coding: utf-8 -*-
"""
innovation_review — 创新复刻的 LLM 协助排查（4.7 收口）

背景：创新识别（coderef_innovation）与复刻铺排/落地（coderef_replicate /
coderef_replicate_apply）都是确定性分析，只回答"哪些模块有/没有该设计、入口符号
是否存在"，回答不了需要语义判断的问题：
  - 这到底是不是一个真正的创新 workflow？（还是已知/常见模式、静态能力标签误命中）
  - 复刻之前，它真实的管线设计是怎么跑的？（调用链从哪进、经过哪些模块）
  - 复刻到目标项目是否合理、与目标项目文档/现状是否一致？

本模块让 LLM 阅读源项目的【管线设计（知识图谱调用链）+ wiki 文档】，对创新确认
与复刻排查给出 AI 判断。供 MCP 工具 coderef_innovation_review 调用。

诚实话纪律（与 interpretation_platform / verify_findings / replicate_engine 同源）：
  - 确定性管线摘要照常给出（图谱调用链闭包、采用模块、入口），不依赖 LLM；
  - LLM 结论明确标注"Ai 判断，非确定性事实"，不下"必须复刻"指令；
  - wiki 来源"生成+兜底"：优先读已有，无则自动生成再排查；
  - 无 API Key 时硬阻断（is_available() 判定），只给确定性管线摘要，不产出降级/占位判断；
  - 不臆断"是否该复刻"——只判"是否是创新、管线/wiki 是否一致、复刻有何适配点/风险"。

设计约束：纯标准库 + 复用 core（llm_integration / design_registry /
innovation_propagation_detector / graph_closure / wiki_cross_verify / wiki_generator /
pipeline_runner），不引入第三方新依赖；面向使用者的可读文本一律中文；
magic number 集中定义为模块级常量；异常不静默吞掉。

作者: CodeRef-AI Team
版本: v1.0
"""

import os
import json
from typing import Dict, List, Optional, Any

from loguru import logger


# ═══════════════════════════════════════════════════════════════════
# 模块级常量（集中管理 magic number）
# ═══════════════════════════════════════════════════════════════════

# 入口符号最多取多少个
MAX_ENTRY_SYMBOLS = 20

# 从 adopters 提取入口时，最多取前几个模块
MAX_ADOPTERS_FOR_ENTRY = 5

# 调用链闭包 BFS 深度上限
CALL_CHAIN_MAX_DEPTH = 8

# 调用链单条最多展示多少项
CALL_CHAIN_DISPLAY = 60

# wiki 正文读取上限（总 / 单篇）
WIKI_READ_MAX_TOTAL = 60000
WIKI_READ_MAX_SINGLE = 20000
WIKI_READ_MAX_DOCS = 12

# LLM 单次最大输入字符数（避免 token 超限）
LLM_MAX_INPUT_CHARS = 60000

# LLM 判断输出参数
LLM_MAX_TOKENS = 2200
LLM_TEMPERATURE = 0.2


# ═══════════════════════════════════════════════════════════════════
# 创新复刻排查引擎
# ═══════════════════════════════════════════════════════════════════

class InnovationReviewer:
    """创新复刻排查 —— 让 LLM 阅读管线设计 + wiki，对创新确认与复刻排查给判断。"""

    def __init__(self):
        from core.llm_integration import LLMIntegration
        from core.design_registry import DesignRegistry
        from core.innovation_propagation_detector import InnovationPropagationDetector
        self.llm = LLMIntegration()
        self.registry = DesignRegistry()
        self.detector = InnovationPropagationDetector()

    # ─── 主体解析：canonical → 资产/采用模块/入口 ───────────────

    def _resolve_subject(self, project_path: str, canonical: str) -> Dict[str, Any]:
        """把 canonical（资产 canonical / 别名 / workflow 模块名）解析为排查主体。

        兼容两种情况：
          - 已固化的创新资产（DesignRegistry.assets）→ 用其 adopters / blueprint；
          - 尚未固化的 workflow 模块名 → 用能力签名（DESIGN_CAPABILITY_TAG）找采用模块。
        """
        from core.innovation_engine import DESIGN_CAPABILITY_TAG
        resolved = self.registry.resolve(canonical)
        asset = self.registry.get_asset(resolved) if resolved else None

        # 能力签名（确定性底层）
        self.detector.prepare_analysis()
        signatures = list(self.detector.collect_signatures(project_path))

        # 采用模块：资产自带 adopters 优先；否则按能力标签找
        adopters: List[str] = []
        if asset and asset.get("adopters"):
            adopters = [str(a) for a in asset["adopters"] if str(a).strip()]
        else:
            tag = DESIGN_CAPABILITY_TAG.get(resolved)
            if tag:
                adopters = [s.module_name for s in signatures if tag in s.tags]

        # workflow 签名：canonical 若匹配某模块名/文件名，则该 workflow 是排查对象
        wf_sig = None
        for s in signatures:
            base = os.path.splitext(os.path.basename(s.file_path or ""))[0]
            if s.module_name == canonical or s.module_name == resolved or base == canonical:
                wf_sig = s
                break

        # 入口符号：蓝图 entry_points 优先；否则从采用模块源码提取真实顶层符号
        entry_symbols = self._resolve_entry_symbols(project_path, asset, adopters)

        # 设计描述
        description = ""
        if asset:
            description = str(asset.get("description") or "")
        elif wf_sig is not None:
            description = f"workflow 模块 `{wf_sig.module_name}`（{wf_sig.file_path}）"

        return {
            "canonical": resolved or canonical,
            "raw": canonical,
            "asset": asset,
            "adopters": adopters,
            "workflow_sig": wf_sig,
            "entry_symbols": entry_symbols[:MAX_ENTRY_SYMBOLS],
            "description": description,
            "signature_count": len(signatures),
        }

    def _resolve_entry_symbols(self, project_path: str, asset: Optional[Dict[str, Any]],
                               adopters: List[str]) -> List[str]:
        """入口符号：真实源码顶层符号优先，资产蓝图入口符号仅作补充。

        资产蓝图 entry_points 可能是「理想模板入口」（如 with_retry），源项目未必真实存在，
        若优先采用会到图谱里查不到、调用链闭包为空。因此先从采用模块源码提取真实顶层符号
        （这些能命中图谱，是确定性铁证），再补充蓝图声明的入口符号（去重）。
        """
        entries: List[str] = []
        seen: set = set()

        # 1. 真实源码顶层符号优先（纯 AST，不执行）
        for mod in adopters[:MAX_ADOPTERS_FOR_ENTRY]:
            mp = self._locate_module(project_path, mod)
            if not mp:
                continue
            for name in self._top_level_symbols(mp):
                if name not in seen:
                    seen.add(name)
                    entries.append(name)
                if len(entries) >= MAX_ENTRY_SYMBOLS:
                    return entries

        # 2. 蓝图声明的入口符号补充（去重；可能为理想模板名，图谱未必命中）
        if asset:
            for e in (asset.get("blueprint") or {}).get("entry_points", []):
                b = str(e).strip()
                if b and b not in seen:
                    seen.add(b)
                    entries.append(b)
                if len(entries) >= MAX_ENTRY_SYMBOLS:
                    return entries

        return entries

    def _locate_module(self, project_path: str, module_name: str) -> Optional[str]:
        """在项目内定位模块源码文件（module_name → .py 文件）。"""
        if not module_name:
            return None
        direct = os.path.join(project_path, f"{module_name}.py")
        if os.path.isfile(direct):
            return direct
        parts = module_name.split(".")
        for i in range(len(parts), 0, -1):
            rel = os.path.join(project_path, *parts[:i]) + ".py"
            if os.path.isfile(rel):
                return rel
        for root, _dirs, files in os.walk(project_path):
            if f"{parts[-1]}.py" in files:
                return os.path.join(root, f"{parts[-1]}.py")
        return None

    def _top_level_symbols(self, file_path: str) -> List[str]:
        """提取文件顶层函数 / 类名（作为候选入口）。纯 AST，不执行。"""
        try:
            import ast
            with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                tree = ast.parse(f.read())
        except Exception:
            return []
        names = []
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                names.append(node.name)
        return names

    # ─── 管线设计摘要（确定性，图谱调用链） ─────────────────────

    def _extract_pipeline(self, project_path: str, entry_symbols: List[str],
                          adopters: List[str]) -> Dict[str, Any]:
        """从知识图谱提取入口 → 下游调用链闭包，作为『管线设计』的确定性铁证。

        无图谱时诚实标注"未构建"，不臆断调用链。
        """
        from core.wiki_cross_verify import locate_kg_db, ModuleCrossVerify
        from core.graph_closure import downstream, file_base
        base = {"entry_symbols": entry_symbols, "adopters": adopters}
        db = locate_kg_db(project_path)
        if not db:
            return {"kg_built": False,
                    "reason": "知识图谱未构建，无法提取确定性调用链（可先运行 coderef_architecture 或 coderef_docs）。",
                    **base}
        try:
            cv = ModuleCrossVerify(db)
        except Exception as exc:
            return {"kg_built": False, "reason": f"加载知识图谱失败：{exc}", **base}
        nodes, adj = cv.nodes, cv.adj
        pipelines: List[Dict[str, Any]] = []
        found: List[str] = []
        for spec in entry_symbols:
            try:
                nid = cv._find_entry(spec)
            except Exception:
                nid = None
            if not nid:
                continue
            found.append(spec)
            closure = downstream(adj, nid, max_depth=CALL_CHAIN_MAX_DEPTH)
            chain = []
            for cid in sorted(closure):
                n = nodes.get(cid)
                if not n:
                    continue
                chain.append(f"{file_base(n)}:{n.get('name')}@{n.get('start_line', 0)}")
            chain.sort()
            pipelines.append({
                "entry": spec,
                "node": f"{nodes[nid].get('name')} ({file_base(nodes[nid])}:{nodes[nid].get('start_line', 0)})",
                "call_chain_count": len(chain),
                "call_chain": chain[:CALL_CHAIN_DISPLAY],
            })
        return {
            "kg_built": True,
            "db": db,
            "entries_found": found,
            "graph_stats": {"nodes": len(nodes), "calls_edges": sum(len(v) for v in adj.values())},
            "pipeline": pipelines,
            "adopters": adopters,
        }

    # ─── wiki 上下文（生成+兜底） ───────────────────────────────

    def _collect_wiki(self, project_path: str, subject: Dict[str, Any],
                      allow_generate: bool) -> Dict[str, Any]:
        """收集源项目 wiki 人话上下文。

        优先读已有（pipe.docs_read）；无已有且 allow_generate 时用 WikiGenerator 生成兜底。
        正文只读关键文档（OVERVIEW / ARCHITECTURE / 相关模块 / FLOWS），并截断总量。
        """
        from core.pipeline_runner import Pipe
        pipe = Pipe()

        # 1. 读已有
        r = self._safe_docs_read(pipe, project_path)
        if r and r.get("status") == "ok":
            docs = r.get("documents", [])
            content = self._read_key_wiki(pipe, project_path, docs, subject)
            return {"source": "existing", "output_dir": r.get("output_dir"),
                    "doc_count": len(docs), "content": content}

        # 2. 无已有 → 生成兜底（仅当允许生成）
        if not allow_generate:
            return {"source": "none",
                    "reason": "无已有 wiki（确定性管线摘要已附上；因 LLM 不可用未生成兜底 wiki）。",
                    "content": ""}
        try:
            from core.wiki_generator import WikiGenerator
            wg = WikiGenerator()
            if not wg.llm.is_available():
                return {"source": "none",
                        "reason": "无已有 wiki，且 LLM 不可用无法生成兜底 wiki（与主判断一并阻断）。",
                        "content": ""}
            res = wg.generate(project_path)
            r2 = self._safe_docs_read(pipe, project_path, output_dir=getattr(res, "output_dir", None))
            if r2 and r2.get("status") == "ok":
                docs = r2.get("documents", [])
                content = self._read_key_wiki(pipe, project_path, docs, subject,
                                              output_dir=r2.get("output_dir"))
                return {"source": "generated", "output_dir": r2.get("output_dir"),
                        "doc_count": len(docs),
                        "wiki_errors": list(getattr(res, "errors", [])),
                        "content": content}
            return {"source": "generated", "reason": "已生成 wiki 但读取失败",
                    "wiki_errors": list(getattr(res, "errors", [])), "content": ""}
        except Exception as exc:
            logger.warning(f"[innovation_review] wiki 生成/读取失败: {exc}")
            return {"source": "none", "reason": f"wiki 生成/读取失败：{exc}", "content": ""}

    def _safe_docs_read(self, pipe, project_path: str, output_dir: Optional[str] = None,
                        doc: Optional[str] = None, max_chars: int = WIKI_READ_MAX_SINGLE):
        """包装 docs_read 的异常，返回 None 表示读取失败。"""
        try:
            return pipe.docs_read(project_path, doc=doc, output_dir=output_dir, max_chars=max_chars)
        except Exception as exc:
            logger.warning(f"[innovation_review] docs_read 失败: {exc}")
            return None

    def _read_key_wiki(self, pipe, project_path: str, docs: List[str],
                       subject: Dict[str, Any], output_dir: Optional[str] = None,
                       max_total: int = WIKI_READ_MAX_TOTAL) -> str:
        """读取关键 wiki 文档正文，按优先级挑并截断总量。"""
        wanted: List[str] = []
        for name in ("OVERVIEW.md", "ARCHITECTURE.md", "WIKI_INDEX.md", "README.md"):
            if name in docs and name not in wanted:
                wanted.append(name)
        for ad in (subject.get("adopters") or [])[:MAX_ADOPTERS_FOR_ENTRY]:
            for cand in (f"MODULES/{ad}.md", f"{ad}.md"):
                if cand in docs and cand not in wanted:
                    wanted.append(cand)
                    break
        flows = [d for d in docs if d.startswith("FLOWS/")][:3]
        for f in flows:
            if f not in wanted:
                wanted.append(f)
        if not wanted:
            wanted = docs[:5]

        parts: List[str] = []
        total = 0
        for d in wanted[:WIKI_READ_MAX_DOCS]:
            rr = self._safe_docs_read(pipe, project_path, output_dir=output_dir, doc=d,
                                      max_chars=WIKI_READ_MAX_SINGLE)
            if not rr or rr.get("status") != "ok":
                continue
            body = rr.get("content") or ""
            parts.append(f"### 文档: {d}\n{body}")
            total += len(body)
            if total >= max_total:
                parts.append(f"[已截断：文档正文合计超过 {max_total} 字符]")
                break
        return "\n\n".join(parts)

    # ─── LLM 判断 ───────────────────────────────────────────────

    def _llm_judge(self, project_path: str, subject: Dict[str, Any],
                   pipeline: Dict[str, Any], wiki: Dict[str, Any],
                   target: str) -> Dict[str, Any]:
        """组装 prompt 让 LLM 判断：是否创新 / 管线与 wiki 是否一致 / 复刻是否合理。"""
        system = (
            "你是 CodeRef 的『创新复刻排查助手』。基于给定的确定性管线摘要、wiki 人话文档、"
            "资产蓝图，对以下三件事给出 AI 判断（诚实、克制、不写代码）：\n"
            "1. is_innovation：该设计是否确属一个『创新 workflow』（区别于已知/常见模式，"
            "或静态能力标签误命中）。给出判断与具体依据。\n"
            "2. pipeline_consistency：wiki 人话描述与静态确证调用链是否一致，指出哪些一致、"
            "哪些不一致或缺失。\n"
            "3. replicate_advice：是否值得/如何合理复刻到目标项目（若提供了 target），给出"
            "关键适配点与风险提示。\n"
            "诚实话纪律：你是审计协助，只做判断与依据说明，不下『必须复刻』指令；"
            "无法判断时如实说明，不编造。\n"
            "严格输出一个 JSON 对象，字段：is_innovation(bool), innovation_reason(string), "
            "pipeline_consistency(string), replicate_advice(string)。不要输出 JSON 以外的内容。"
        )
        user = json.dumps({
            "project_path": project_path,
            "canonical": subject["canonical"],
            "description": subject["description"],
            "adopters": subject["adopters"],
            "entry_symbols": subject["entry_symbols"],
            "asset_blueprint": (subject.get("asset") or {}).get("blueprint"),
            "pipeline": pipeline,
            "wiki_summary": wiki.get("content", ""),
            "target_project": target or None,
        }, ensure_ascii=False)[:LLM_MAX_INPUT_CHARS]
        messages = [{"role": "system", "content": system},
                    {"role": "user", "content": user}]
        try:
            raw = self.llm.chat_completion(messages, max_tokens=LLM_MAX_TOKENS,
                                           temperature=LLM_TEMPERATURE)
        except Exception as exc:
            logger.warning(f"[innovation_review] LLM 调用失败: {exc}")
            return {"called": True, "error": f"LLM 调用失败：{exc}", "raw": ""}
        return {"called": True, "raw": raw, "judgement": self._parse_llm_json(raw)}

    def _parse_llm_json(self, raw: str) -> Optional[Dict[str, Any]]:
        """解析 LLM JSON（复用 llm_integration 的截断容错机制）。"""
        if not raw:
            return None
        repaired = self.llm._repair_truncated_json(raw)
        parsed = self.llm._try_parse_json(repaired)
        if parsed is None:
            parsed = self.llm._try_parse_json(raw)
        if isinstance(parsed, dict):
            return {
                "is_innovation": bool(parsed.get("is_innovation")),
                "innovation_reason": str(parsed.get("innovation_reason") or ""),
                "pipeline_consistency": str(parsed.get("pipeline_consistency") or ""),
                "replicate_advice": str(parsed.get("replicate_advice") or ""),
            }
        return None

    # ─── 主入口 ──────────────────────────────────────────────────

    def review(self, project_path: str, canonical: str, target: str = "",
               out_format: str = "json") -> Dict[str, Any]:
        """创新复刻排查：让 LLM 阅读源项目管线设计 + wiki，对创新确认与复刻排查给判断。

        Args:
            project_path: 源项目路径（创新所在项目）。
            canonical: 要复查的创新设计（workflow 名或资产 canonical/别名）。
            target: 目标项目路径；提供时追加"复刻到目标是否合理"的排查。
            out_format: json / text / html。

        Returns:
            结构化 dict：确定性管线摘要 + wiki 状态 + LLM 判断（若有）。
        """
        project_path = os.path.abspath(project_path)
        subject = self._resolve_subject(project_path, canonical)
        pipeline = self._extract_pipeline(project_path, subject["entry_symbols"],
                                          subject["adopters"])
        llm_available = self.llm.is_available()
        wiki = self._collect_wiki(project_path, subject, allow_generate=llm_available)

        # 确定性摘要（无 LLM 也照常给出）
        base: Dict[str, Any] = {
            "ok": True,
            "tool": "coderef_innovation_review",
            "project_path": project_path,
            "canonical": subject["canonical"],
            "subject": {
                "raw": subject["raw"],
                "description": subject["description"],
                "adopters": subject["adopters"],
                "workflow_module": subject["workflow_sig"].module_name
                if subject["workflow_sig"] is not None else None,
                "signature_count": subject["signature_count"],
            },
            "pipeline": self._pipeline_brief(pipeline),
            "wiki": {k: v for k, v in wiki.items() if k != "content"},
            "llm_available": llm_available,
        }

        # 无 LLM → 硬阻断（诚实告知，只给确定性摘要，不下结论）
        if not llm_available:
            base["ok"] = False
            base["error"] = (
                "创新复刻排查需要 LLM，但当前未配置有效 API Key。请配置 API Key 后再运行。"
                "确定性管线摘要与 wiki 状态已附上（供人工/其他工具参考）。"
            )
            base["summary"] = (
                f"需要 LLM；无 API Key 时硬阻断，只给确定性摘要，不下'是否创新/复刻'结论。"
                f"该设计对应 {len(subject['adopters'])} 个采用模块"
                f"（管线 {'已提取' if pipeline.get('kg_built') else '未提取：'+pipeline.get('reason','')}）。"
            )
            return base

        # LLM 判断
        judgement = self._llm_judge(project_path, subject, pipeline, wiki, target)
        base["judgement"] = judgement
        base["summary"] = self._summary(subject, pipeline, judgement, target)
        return base

    def _pipeline_brief(self, pipeline: Dict[str, Any]) -> Dict[str, Any]:
        """去掉图谱内部 db 路径等内部字段，只留可读摘要。"""
        return {k: v for k, v in pipeline.items() if k != "db"}

    def _summary(self, subject: Dict[str, Any], pipeline: Dict[str, Any],
                 judgement: Dict[str, Any], target: str) -> str:
        """生成人话摘要。"""
        parts = [f"对设计「{subject['canonical']}」的排查："]
        if judgement and judgement.get("judgement"):
            j = judgement["judgement"]
            verdict = "确属创新 workflow" if j.get("is_innovation") else "是否创新存疑/更像常规模式"
            parts.append(f"{verdict}（依据：{(j.get('innovation_reason') or '')[:40]}…）")
            if j.get("pipeline_consistency"):
                parts.append(f"管线/wiki 一致性：{(j.get('pipeline_consistency') or '')[:40]}…")
            if target and j.get("replicate_advice"):
                parts.append(f"复刻建议：{(j.get('replicate_advice') or '')[:40]}…")
        else:
            parts.append("LLM 未返回可解析判断（见 judgement.raw）。")
        if pipeline.get("kg_built"):
            if pipeline.get("entries_found"):
                parts.append(f"确定性管线：已提取 {len(pipeline.get('pipeline', []))} 条入口调用链。")
            else:
                parts.append("确定性管线：知识图谱已构建，但入口符号未在图谱中命中，调用链为空（可能因蓝图理想入口名或 AST 解析受限）。")
        else:
            parts.append(f"确定性管线：{pipeline.get('reason', '未提取')}。")
        parts.append(f"采用模块 {len(subject['adopters'])} 个。")
        return "；".join(parts)


# ═══════════════════════════════════════════════════════════════════
# 顶层接口（MCP handler 调用）
# ═══════════════════════════════════════════════════════════════════

def review_innovation(project_path: str, canonical: str, target: str = "",
                      out_format: str = "json") -> Dict[str, Any]:
    """创新复刻排查入口。"""
    return InnovationReviewer().review(project_path, canonical, target=target,
                                       out_format=out_format)


# ═══════════════════════════════════════════════════════════════════
# 渲染
# ═══════════════════════════════════════════════════════════════════

def render_report(result: Dict[str, Any]) -> str:
    """纯文本报告（终端/日志可读）。"""
    lines = ["创新复刻排查报告", "=" * 3]
    lines.append(f"设计: {result.get('canonical', '')}")
    if not result.get("ok"):
        lines.append(result.get("error", result.get("summary", "排查失败")))
        return "\n".join(lines)
    sub = result.get("subject", {})
    if sub.get("description"):
        lines.append(f"描述: {sub['description']}")
    lines.append(f"采用模块: {', '.join(sub.get('adopters', [])) or '无'}")
    pipe = result.get("pipeline", {})
    if pipe.get("kg_built"):
        lines.append("管线调用链:")
        for p in pipe.get("pipeline", []):
            lines.append(f"  入口 {p['entry']} → 节点 {p['node']}（{p['call_chain_count']} 项）")
            for c in p.get("call_chain", [])[:8]:
                lines.append(f"    · {c}")
    else:
        lines.append(f"管线: {pipe.get('reason', '未提取')}")
    j = (result.get("judgement") or {}).get("judgement")
    if j:
        lines.append("")
        lines.append("LLM 判断（AI 判断，非确定性事实）:")
        lines.append(f"  是否创新: {'是' if j.get('is_innovation') else '否/存疑'}")
        if j.get("innovation_reason"):
            lines.append(f"  依据: {j['innovation_reason']}")
        if j.get("pipeline_consistency"):
            lines.append(f"  管线/wiki 一致性: {j['pipeline_consistency']}")
        if j.get("replicate_advice"):
            lines.append(f"  复刻建议: {j['replicate_advice']}")
    return "\n".join(lines)


def render_html(result: Dict[str, Any]) -> str:
    """渲染非编程人员可读的 HTML 报告（自包含单文件）。"""
    from html import escape as _esc

    def _esc_s(v: Any) -> str:
        return _esc(str(v or ""))

    if not result.get("ok"):
        body = (f"<div style='background:#fff;border-radius:14px;padding:28px;'>"
                f"<h1 style='margin:0 0 12px;font-size:22px;'>创新复刻排查报告</h1>"
                f"<p style='color:#E8463A;'>{_esc_s(result.get('error', result.get('summary', '')))}</p></div>")
        return _wrap_html(body)

    sub = result.get("subject", {})
    pipe = result.get("pipeline", {})

    pipe_html = ""
    if pipe.get("kg_built"):
        rows = "".join(
            f"<tr><td style='padding:8px;border-bottom:1px solid #eee;font-family:monospace;'>"
            f"{_esc_s(p['entry'])}</td>"
            f"<td style='padding:8px;border-bottom:1px solid #eee;font-family:monospace;color:#555;'>"
            f"{_esc_s(p['node'])}</td>"
            f"<td style='padding:8px;border-bottom:1px solid #eee;color:#888;font-size:12px;'>"
            f"{p.get('call_chain_count', 0)} 项</td></tr>"
            for p in pipe.get("pipeline", [])
        )
        pipe_html = f"""<h3 style='margin:20px 0 8px;font-size:16px;'>管线调用链（确定性）</h3>
        <table style='width:100%;border-collapse:collapse;font-size:14px;'>
          <thead><tr style='text-align:left;color:#888;font-size:12px;border-bottom:2px solid #eee;'>
            <th style='padding:8px;'>入口</th><th style='padding:8px;'>节点</th>
            <th style='padding:8px;'>调用链</th></tr></thead><tbody>{rows}</tbody></table>"""
    else:
        pipe_html = (f"<p style='color:#EFAA17;'>{_esc_s(pipe.get('reason', '未提取'))}</p>")

    j = (result.get("judgement") or {}).get("judgement")
    judge_html = ""
    if j:
        badge = "#1DC981" if j.get("is_innovation") else "#EFAA17"
        judge_html = f"""<h3 style='margin:20px 0 8px;font-size:16px;'>LLM 判断（AI 判断，非确定性事实）</h3>
        <div style='display:flex;align-items:center;gap:12px;margin-bottom:12px;'>
          <span style='background:{badge};color:#fff;border-radius:999px;padding:4px 14px;font-size:14px;'>
            {'确属创新 workflow' if j.get('is_innovation') else '是否创新存疑'}</span>
        </div>
        <div style='background:#fafbfc;border:1px solid #eee;border-radius:10px;padding:14px 16px;line-height:1.8;color:#444;font-size:14px;'>
          <div><b>依据：</b>{_esc_s(j.get('innovation_reason'))}</div>
          <div style='margin-top:8px;'><b>管线/wiki 一致性：</b>{_esc_s(j.get('pipeline_consistency'))}</div>
          <div style='margin-top:8px;'><b>复刻建议：</b>{_esc_s(j.get('replicate_advice'))}</div>
        </div>"""

    body = f"""<div style="background:#fff;border-radius:14px;padding:28px;box-shadow:0 1px 3px rgba(0,0,0,.06);">
    <h1 style="margin:0 0 4px;font-size:22px;">创新复刻排查报告</h1>
    <div style="color:#888;font-size:13px;margin-bottom:16px;">设计 {_esc_s(result.get('canonical'))}</div>
    <div style="color:#555;font-size:14px;line-height:1.7;margin-bottom:12px;">{_esc_s(sub.get('description'))}</div>
    <div style="color:#555;font-size:14px;">采用模块：{_esc_s('、'.join(sub.get('adopters', [])) or '无')}</div>
    {pipe_html}
    {judge_html}
    <div style="margin-top:20px;font-size:12px;color:#999;line-height:1.8;">
      说明：管线调用链为确定性静态确证；LLM 判断为 AI 意见，仅作排查参考，不构成"必须复刻"指令。
    </div>
  </div>"""
    return _wrap_html(body)


def _wrap_html(body: str) -> str:
    return f"""<!DOCTYPE html><html lang="zh"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>创新复刻排查报告</title></head>
<body style="margin:0;font-family:'Segoe UI','PingFang SC','Microsoft YaHei',sans-serif;background:#f6f7f9;color:#222;">
<div style="max-width:960px;margin:0 auto;padding:32px 20px;">{body}</div></body></html>"""


if __name__ == "__main__":
    import argparse
    import pprint
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", required=True, help="源项目路径")
    ap.add_argument("--canonical", required=True, help="要复查的创新设计 canonical / workflow 名")
    ap.add_argument("--target", default="", help="目标项目路径（可选）")
    ap.add_argument("--out_format", default="text", choices=["json", "text", "html"])
    args = ap.parse_args()
    r = review_innovation(args.project, args.canonical, target=args.target)
    if args.out_format == "text":
        print(render_report(r))
    elif args.out_format == "html":
        print(render_html(r))
    else:
        pprint.pprint(r, width=130, sort_dicts=False)