# -*- coding: utf-8 -*-
"""
产出物语义评估模块（coderef_eval）—— 给 CodeRef 自身的 LLM 产出物加"语义质量门禁"

像写单测一样断言"LLM 输出过不过阈值"（4 个语义指标 + 软硬判定），可进 CI、可沉淀。
设计参考 DeepEval 的 LLM-as-judge 思路与《给 Agent 打个分》的"软硬维度 + 一票否决"框架。

边界（与用户确认，不可逾越）：
  1. 只评 CodeRef 调用 LLM 生成的文本（审查评论、创新判断、报告摘要等）；
     用户项目运行时的 LLM/RAG/Agent 产出（动态执行）为禁区，不评、不承诺。
  2. 语义评分 = LLM-as-judge，一律标注"AI 判断"；只做软门禁，不阻断确定性结论。
  3. 缺 API key 硬阻断返回 SKIP，不降级编造。
  4. 零新依赖：纯 Python 标准库 + 复用 core/llm_integration.py。

判定设计（防"致命错误被平均分稀释"）：
  - metric 支持单个（string）或多个（数组）指标；多指标时按产出物聚合所有指标后再判定。
  - 硬指标（faithfulness / hallucination）：任一挂（score < threshold）→ 整条 FAIL（hard_failed=true），一票否决；
  - 软指标（answer_relevancy / coherence）：失分只降平均分，不单独否决（可由其他指标高分拉回平均分）；
  - 整体判定：verdict = PASS 当且仅当 硬指标全过 且 平均分 ≥ threshold。

失败护栏（沿用 code_review 教训）：
  - JSON 解析失败 → 一次"强制仅 JSON 输出"重试 → 仍失败走降级结果（status=degraded + 散文理由），不裸崩；
  - text / context 超长截断（防成本失控），并在结果中披露 truncated（原始/评估字符数），不静默；
  - action 非法 / assert 传多条 → 结构化错误，不静默丢弃。

作者: CodeRef-AI Team
"""

import json
import math
import re
from typing import Any, Dict, List, Optional, Tuple

from loguru import logger

from core.llm_integration import LLMIntegration


# ═══════════════════════════════════════════════════════════════════════
# 模块级常量（集中管理 magic number；VALID_METRICS 与 schema enum 同源引用）
# ═══════════════════════════════════════════════════════════════════════

# 合法指标集合（首批 4 个，够用不贪多）
VALID_METRICS: tuple = (
    "answer_relevancy",  # 软：产出物与主题/问题的相关程度
    "faithfulness",      # 硬：产出物是否忠实于参考上下文（逐 claim 核对）
    "hallucination",     # 硬：检出虚构/无依据陈述（反向指标）
    "coherence",         # 软：结构 / 逻辑连贯性（报告类产出适用）
)

# 硬指标（一票否决）：挂 → 整条 FAIL，即使其他指标满分、平均分达标
HARD_METRICS: tuple = ("faithfulness", "hallucination")

# 需要 context 的指标：faithfulness/hallucination 对照源材料；answer_relevancy 对照问题/主题
NEED_CONTEXT_METRICS: tuple = ("answer_relevancy", "faithfulness", "hallucination")

# 断言阈值默认值（0–1）
DEFAULT_THRESHOLD: float = 0.7

# 单次 prompt 中 text / context 的最大字符数（防成本失控，沿用 code_review 约束思路）
MAX_EVAL_TEXT_CHARS: int = 6000
MAX_EVAL_CONTEXT_CHARS: int = 6000

# 语义评分显式标注：AI 判断，非确定性事实；语义评分仅软门禁
NOTE_AI_JUDGEMENT: str = "AI 判断，非确定性事实；语义评分仅软门禁"

# LLM 结构化错误串前缀（复用 llm_integration 约定，用于判定调用失败）
_LLM_ERROR_PREFIX = "LLM调用错误"

# JSON 输出字段名（集中管理，供解析/组装共用）
_SCORE_KEY = "score"
_REASON_KEY = "reason"
_BREAKDOWN_KEY = "breakdown"
# 模型 score 越界被夹取时的披露键（breakdown 内）
_CLAMPED_KEY = "score_clamped"


# ═══════════════════════════════════════════════════════════════════════
# 指标 prompt 模板（模块级常量；__TEXT__ / __CONTEXT__ 占位符，避免 f-string 花括号冲突）
# ═══════════════════════════════════════════════════════════════════════

# 公共 system prompt：强制"只返回 JSON 对象"（沿用 code_review 的 JSON 硬约束教训）
_SYSTEM_PROMPT: str = (
    "你是 AI 产出物语义评估专家，负责给 LLM 生成的文本打语义分。\n"
    "你的输出会被程序直接解析，因此必须且只能输出一个合法的 JSON 对象。\n"
    "硬性要求（违反任一即解析失败）：\n"
    "1. 输出必须以 { 开头、以 } 结尾，除 JSON 对象外不得输出任何字符，"
    "包括 Markdown 代码块标记（```json、```）、思考过程、解释、叙述或前后缀文字。\n"
    "2. score 必须是 0 到 1 之间的数字（可保留两位小数）。\n"
    "3. 必须包含 reason 字段（一句简短理由，中文）。\n"
    "4. 必须包含 breakdown 字段（JSON 对象），字段随指标要求。"
)

# 指标：answer_relevancy（软）—— 产出物与主题/问题的相关程度
_PROMPT_RELEVANCY: str = (
    "请评估以下「AI 产出物」与参考主题/问题的相关程度，给出 0–1 分。\n\n"
    "参考主题/问题（context）：\n"
    "__CONTEXT__\n\n"
    "AI 产出物（text）：\n"
    "__TEXT__\n\n"
    "评分标准：\n"
    "- 1.0：完全切题，直接回答主题/问题，无离题内容。\n"
    "- 0.7：基本切题，有少量泛化或铺垫，不影响理解。\n"
    "- 0.4：部分切题，夹杂明显离题内容。\n"
    "- 0.0：完全离题。\n\n"
    "输出 JSON 对象，字段："
    "{\"score\": <0-1>, \"reason\": \"<一句理由>\", "
    "\"breakdown\": {\"topic_coverage\": \"<覆盖程度描述>\", \"off_topic\": <true/false>}}"
)

# 指标：faithfulness（硬）—— 产出物是否忠实于参考上下文（逐 claim 核对）
_PROMPT_FAITHFULNESS: str = (
    "请核对以下「AI 产出物」中的每一条陈述（claim）是否忠实于参考上下文（源材料），给出 0–1 分。\n\n"
    "参考上下文（源材料，context）：\n"
    "__CONTEXT__\n\n"
    "AI 产出物（text）：\n"
    "__TEXT__\n\n"
    "核对要求：\n"
    "1. 把 AI 产出物拆成可核对的陈述（claim），逐条判断："
    "源材料支持 → supported；源材料不支持或与源材料矛盾 → unsupported；"
    "源材料未提及 → 视为无依据（编造）。\n"
    "2. score = claims_supported 数 / claims_checked 数。\n"
    "3. 若 AI 产出物只是转述源材料且无新增事实，所有 claim 都算 supported；"
    "诚实拒答（如「暂未收录/不清楚」）不算无依据。\n\n"
    "输出 JSON 对象，字段："
    "{\"score\": <0-1>, \"reason\": \"<一句理由>\", "
    "\"breakdown\": {\"claims_checked\": <int>, \"claims_supported\": <int>, \"hallucinated\": <int>}}"
)

# 指标：hallucination（硬，反向指标）—— 检出虚构/无依据陈述
_PROMPT_HALLUCINATION: str = (
    "请检出以下「AI 产出物」中的虚构/无依据陈述，给出 0–1 反向分（分越高 = 越少虚构）。\n\n"
    "参考上下文（源材料，context）：\n"
    "__CONTEXT__\n\n"
    "AI 产出物（text）：\n"
    "__TEXT__\n\n"
    "检出要求：\n"
    "1. 逐 claim 标注：源材料支持 → 有依据；源材料未提及或与源材料矛盾 → 虚构/无依据。\n"
    "2. score = 1 - (hallucinated 数 / claims_checked 数)；全部无虚构 → 1.0。\n"
    "3. 诚实拒答（如「暂未收录/不清楚」）不算虚构。\n\n"
    "输出 JSON 对象，字段："
    "{\"score\": <0-1>, \"reason\": \"<一句理由>\", "
    "\"breakdown\": {\"claims_checked\": <int>, \"claims_supported\": <int>, \"hallucinated\": <int>}}"
)

# 指标：coherence（软）—— 结构 / 逻辑连贯性（报告类产出适用）
_PROMPT_COHERENCE: str = (
    "请评估以下「AI 产出物」的结构与逻辑连贯性，给出 0–1 分。\n\n"
    "AI 产出物（text）：\n"
    "__TEXT__\n\n"
    "评分标准：\n"
    "- 1.0：结构清晰、逻辑自洽、无自相矛盾。\n"
    "- 0.7：结构基本完整，少量衔接生硬。\n"
    "- 0.4：结构混乱或明显逻辑断裂。\n"
    "- 0.0：无法理解。\n\n"
    "输出 JSON 对象，字段："
    "{\"score\": <0-1>, \"reason\": \"<一句理由>\", "
    "\"breakdown\": {\"structure\": \"<结构描述>\", \"self_contradiction\": <true/false>}}"
)

# 指标名 → 用户 prompt 模板
_METRIC_PROMPTS: Dict[str, str] = {
    "answer_relevancy": _PROMPT_RELEVANCY,
    "faithfulness": _PROMPT_FAITHFULNESS,
    "hallucination": _PROMPT_HALLUCINATION,
    "coherence": _PROMPT_COHERENCE,
}


# ═══════════════════════════════════════════════════════════════════════
# 纯函数工具
# ═══════════════════════════════════════════════════════════════════════

def _safe_threshold(raw: Any) -> float:
    """安全转换阈值：非法/越界回退默认，避免调用方参数导致判定失真。"""
    try:
        t = float(raw)
    except (TypeError, ValueError):
        return DEFAULT_THRESHOLD
    if not 0.0 <= t <= 1.0:
        return DEFAULT_THRESHOLD
    return t


def _normalize_metrics(metric: Any) -> List[str]:
    """把 metric 归一为字符串列表：str → 单指标；list/tuple → 逐指标（过滤空串）。

    多指标（数组）用于"软硬聚合判定"：按产出物聚合所有指标分 → 平均分，
    硬指标全过 且 平均分 ≥ threshold 才 PASS（软指标失分可由其他指标高分拉回）。
    """
    if metric is None:
        return []
    if isinstance(metric, str):
        return [metric] if metric.strip() else []
    if isinstance(metric, (list, tuple)):
        out = []
        for m in metric:
            if isinstance(m, str) and m.strip():
                out.append(m)
        return out
    s = str(metric)
    return [s] if s.strip() else []


def _normalize_items(text: Any) -> List[str]:
    """把 text 归一为字符串列表：str → 单条；list/tuple → 逐条（过滤空串）；其余转 str。

    action=score 批量评估时，text 可传字符串数组；assert 单条时传字符串。
    """
    if text is None:
        return []
    if isinstance(text, str):
        return [text] if text.strip() else []
    if isinstance(text, (list, tuple)):
        out = []
        for x in text:
            if isinstance(x, str) and x.strip():
                out.append(x)
        return out
    s = str(text)
    return [s] if s.strip() else []


def _extract_score(data: Dict[str, Any]) -> Tuple[float, bool]:
    """从已校验的 JSON 中取 score，夹取 0–1；返回 (夹取后分值, 是否越界被夹取)。

    越界（模型返回 score <0 或 >1）本身是异常信号，须披露而非静默吞掉，
    供调用方追溯评估失真（Brooks-Lint：surface anomalies）。
    """
    try:
        s = float(data.get(_SCORE_KEY))
    except (TypeError, ValueError):
        return 0.0, False
    clamped = s < 0.0 or s > 1.0
    return max(0.0, min(1.0, s)), clamped


def _compact_text(text: str, limit: int = 300) -> str:
    """把 LLM 散文反馈压缩为单行片段（降级结果 detail 用）。"""
    if not text:
        return ""
    return re.sub(r"\s+", " ", text)[:limit]


# ═══════════════════════════════════════════════════════════════════════
# OutputEvaluator
# ═══════════════════════════════════════════════════════════════════════

class OutputEvaluator:
    """产出物语义评估器：单条/批量评估 + 单/多指标软硬判定 + JSON 重试→降级护栏。

    用法:
        from core.output_evaluator import OutputEvaluator
        r = OutputEvaluator().evaluate(
            text="...", metric="faithfulness", context="...", threshold=0.7)
        # 多指标聚合（软硬判定防"致命错误被平均分稀释"）：
        r = OutputEvaluator().evaluate(
            text="...", metric=["faithfulness", "coherence"], context="...")
    """

    def __init__(self) -> None:
        self.llm = LLMIntegration()

    # ── 对外入口 ──────────────────────────────────────────────────────
    def evaluate(self, text: Any, metric: Any = "",
                 context: Optional[str] = None,
                 threshold: Any = None,
                 action: str = "assert") -> Dict[str, Any]:
        """评估一条或多条产出物。

        action=assert（默认）：单条评估 → verdict PASS/FAIL（text 必须是单个字符串）；
        action=score：批量评估（text 可为字符串数组）→ 逐条明细 + 汇总报告。
        metric 传数组时按产出物聚合多指标分 → 平均分 + 软硬判定（忠实表达软硬契约）。
        """
        threshold = _safe_threshold(threshold)

        if action not in ("assert", "score"):
            return {
                "status": "error",
                "error": f"未知 action '{action}'，支持: assert / score",
            }

        metrics = _normalize_metrics(metric)
        for m in metrics:
            if m not in VALID_METRICS:
                return {
                    "status": "error",
                    "error": f"未知指标 '{m}'，支持: {', '.join(VALID_METRICS)}",
                }
        if not metrics:
            return {
                "status": "error",
                "error": f"metric 不能为空，支持: {', '.join(VALID_METRICS)}",
            }

        items = _normalize_items(text)
        if not items:
            return {"status": "error", "error": "text 不能为空，请传入待评估的产出物"}
        if action == "assert" and len(items) != 1:
            return {
                "status": "error",
                "error": "action=assert 时 text 必须是单个字符串；批量评估请用 action=score",
            }

        # 缺 key 硬阻断（不降级编造）
        if not self.llm.is_available():
            return {
                "status": "SKIP",
                "verdict": "SKIP",
                "metric": metrics[0] if len(metrics) == 1 else metrics,
                "threshold": threshold,
                "reason": "LLM API key 未配置（硬阻断，不降级编造）",
                "note": NOTE_AI_JUDGEMENT,
            }

        # 需要 context 的指标缺 context → 结构化错误（调用方契约问题，非 LLM 问题）
        need_ctx = [m for m in metrics if m in NEED_CONTEXT_METRICS]
        if need_ctx and not (context or "").strip():
            return {
                "status": "error",
                "error": f"指标 {', '.join(need_ctx)} 需要提供 context（源材料/主题），请传入后再评估",
            }

        results = [
            self._evaluate_item(item, metrics, context or "", threshold)
            for item in items
        ]

        if action == "score":
            return self._build_score_report(results, metrics, threshold)
        return results[0]

    # ── 单条评估（单/多指标分派） ─────────────────────────────────────
    def _evaluate_item(self, text: str, metrics: List[str],
                       context: str, threshold: float) -> Dict[str, Any]:
        """评估单条产出物：单指标 → 扁平单条结果；多指标 → 聚合平均分 + 软硬判定。"""
        if len(metrics) == 1:
            return self._evaluate_single_metric(text, metrics[0], context, threshold)

        per: Dict[str, Dict[str, Any]] = {}
        for m in metrics:
            per[m] = self._evaluate_single_metric(text, m, context, threshold)

        # 任一硬指标降级（LLM 调用失败）→ 整体降级，不误 PASS。
        # 防「硬指标评估失败被静默排除出平均分 → 硬伤被掩盖」：硬指标是
        # 一票否决的依据，它没得到合法分，整条判定即不可信，须诚实降级。
        degraded_hard = [m for m in metrics
                         if m in HARD_METRICS and per[m].get("status") == "degraded"]
        if degraded_hard:
            # 披露全部降级指标（含同期降级的软指标），不只看硬指标首个失败
            degraded_all = [m for m in metrics if per[m].get("status") == "degraded"]
            return {
                "status": "degraded",
                "metric": list(metrics),
                "threshold": threshold,
                "verdict": "SKIP",
                "hard_failed": False,
                "reason": (
                    f"硬指标评估失败（{', '.join(degraded_hard)}），"
                    "无法给出可信的软硬判定，已整体降级"
                ),
                "degraded_metrics": degraded_all,
                "metrics": per,
                "note": NOTE_AI_JUDGEMENT,
            }

        # 全部指标均降级（无硬指标，如全软指标）→ 整体降级，不谎报 PASS
        if all(per[m].get("status") == "degraded" for m in metrics):
            return {
                "status": "degraded",
                "metric": list(metrics),
                "threshold": threshold,
                "verdict": "SKIP",
                "hard_failed": False,
                "reason": "所有指标均未得到合法 JSON 评估结果（重试后仍失败）",
                "degraded_metrics": list(metrics),
                "metrics": per,
                "note": NOTE_AI_JUDGEMENT,
            }

        # 软指标降级：排除出平均分（无分可比），但披露 degraded_metrics 不静默
        degraded_soft = [m for m in metrics if per[m].get("status") == "degraded"]
        scores = [per[m].get(_SCORE_KEY) for m in metrics
                  if isinstance(per[m].get(_SCORE_KEY), (int, float))]
        avg = round(sum(scores) / len(scores), 3) if scores else 0.0
        # 硬指标任一挂 → 一票否决；软指标失分只降平均分，不单独否决
        hard_failed = any(per[m].get("hard_failed") for m in metrics if m in HARD_METRICS)
        verdict = "PASS" if (not hard_failed and avg >= threshold) else "FAIL"
        result: Dict[str, Any] = {
            "status": "completed",
            "metric": list(metrics),
            "score": avg,
            "avg_score": avg,
            "threshold": threshold,
            "verdict": verdict,
            "hard_failed": hard_failed,
            "metrics": per,
            "note": NOTE_AI_JUDGEMENT,
        }
        if degraded_soft:
            result["degraded_metrics"] = degraded_soft
        return result

    def _evaluate_single_metric(self, text: str, metric: str,
                                context: str, threshold: float) -> Dict[str, Any]:
        """评估单条产出物 + 单个指标：构造 prompt → 调用 LLM → 解析（失败重试一次→降级）。"""
        truncated: Dict[str, Any] = {}
        text_cut = text[:MAX_EVAL_TEXT_CHARS]
        if len(text) > MAX_EVAL_TEXT_CHARS:
            truncated["text"] = {"original_chars": len(text), "evaluated_chars": len(text_cut)}
        context_cut = context[:MAX_EVAL_CONTEXT_CHARS]
        if len(context) > MAX_EVAL_CONTEXT_CHARS:
            truncated["context"] = {"original_chars": len(context),
                                    "evaluated_chars": len(context_cut)}

        messages = self._build_messages(metric, text_cut, context_cut)
        response = self._call_llm(messages)
        data = self._parse_eval_json(response)
        if data is None:
            # 首次解析失败：强制仅 JSON 输出重试一次（控制成本，最多 1 次）
            logger.warning(
                f"coderef_eval 首次解析未得到 JSON 评估结果，强制重试；响应片段: {response[:200]}")
            retry_response = self._call_llm_retry(messages, response)
            data = self._parse_eval_json(retry_response)
            if data is None:
                # 重试仍失败：降级结果（带散文线索），不裸崩
                result = self._degraded_result(metric, threshold, retry_response or response)
                if truncated:
                    result["truncated"] = truncated
                return result
        result = self._compose_result(metric, threshold, data)
        if truncated:
            result["truncated"] = truncated
        return result

    # ── prompt 构造 ───────────────────────────────────────────────────
    def _build_messages(self, metric: str, text: str, context: str) -> List[Dict[str, str]]:
        template = _METRIC_PROMPTS[metric]
        user_prompt = template.replace("__TEXT__", text).replace("__CONTEXT__", context)
        return [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ]

    # ── LLM 调用 ──────────────────────────────────────────────────────
    def _call_llm(self, messages: List[Dict[str, str]]) -> str:
        """调用 LLM 语义打分；异常/错误统一转错误文本（由解析层判定）。"""
        try:
            return self.llm.chat_completion(
                messages, max_tokens=1024, temperature=0.2,
                response_format={"type": "json_object"},
            )
        except Exception as e:
            logger.error(f"coderef_eval LLM 调用抛出异常: {e}")
            return f"{_LLM_ERROR_PREFIX}: {e}"

    def _call_llm_retry(self, messages: List[Dict[str, str]],
                        first_response: str) -> str:
        """强制仅 JSON 输出重试（首响不当思考再抽取），沿用 code_review 模式。"""
        retry_messages = messages + [
            {"role": "assistant", "content": first_response},
            {
                "role": "user",
                "content": (
                    "你上一次的输出包含你的分析思考过程，但格式不是 JSON 对象。\n"
                    "现在请只输出一个合法的 JSON 对象（以 { 开头、以 } 结尾），"
                    "字段为 score / reason / breakdown。\n"
                    "严禁输出任何解释、叙述、Markdown 代码块标记（如 ```json 或 ```）或其他文字。"
                ),
            },
        ]
        return self._call_llm(retry_messages)

    # ── 解析与组装 ────────────────────────────────────────────────────
    def _parse_eval_json(self, text: str) -> Optional[Dict[str, Any]]:
        """解析 LLM 返回文本：必须为含合法 score 的 JSON 对象，否则返回 None。

        合法性含：score 可转有限 float 且非 bool（bool 是 int 子类会被 float()
        静默转 0.0/1.0）；NaN/Infinity 恒不在 [0,1] 内且比较异常，一并拒绝，
        让这类「模型输出异常值」走既有重试→降级路径，而非产出 PASS/夹取结果。
        """
        if not text or text.startswith(_LLM_ERROR_PREFIX):
            return None
        data = self.llm.parse_json_response(text)
        if not isinstance(data, dict):
            return None
        raw = data.get(_SCORE_KEY)
        if isinstance(raw, bool) or not isinstance(raw, (int, float)):
            return None
        try:
            s = float(raw)
        except (TypeError, ValueError):
            return None
        if not math.isfinite(s):
            return None
        return data

    def _compose_result(self, metric: str, threshold: float,
                        data: Dict[str, Any]) -> Dict[str, Any]:
        """组装单条单指标评估结果（软硬判定）。"""
        score, clamped = _extract_score(data)
        # 硬指标挂 → 一票否决；软指标失分 → 只降平均分，不否决
        hard_failed = metric in HARD_METRICS and score < threshold
        verdict = "FAIL" if (hard_failed or score < threshold) else "PASS"
        breakdown = data.get(_BREAKDOWN_KEY)
        if not isinstance(breakdown, dict):
            breakdown = {}
        if clamped:
            # 模型返回 score 越界（异常信号）：披露原始值，不静默夹取
            breakdown[_CLAMPED_KEY] = {
                "model_score": data.get(_SCORE_KEY),
                "clamped_to": score,
            }
        return {
            "status": "completed",
            "metric": metric,
            "score": score,
            "threshold": threshold,
            "verdict": verdict,
            "breakdown": breakdown,
            "hard_failed": hard_failed,
            "reason": str(data.get(_REASON_KEY) or ""),
            "note": NOTE_AI_JUDGEMENT,
        }

    def _degraded_result(self, metric: str, threshold: float,
                         llm_text: str) -> Dict[str, Any]:
        """JSON 解析重试仍失败时的降级结果（带散文线索，不裸崩、不谎报 PASS）。"""
        reason = "LLM 返回内容不包含合法 JSON 评估结果（重试后仍失败）"
        snippet = _compact_text(llm_text)
        if snippet:
            reason = f"{reason}；LLM 原始反馈：{snippet}"
        return {
            "status": "degraded",
            "metric": metric,
            "threshold": threshold,
            "verdict": "SKIP",
            "hard_failed": False,
            "reason": reason,
            "note": NOTE_AI_JUDGEMENT,
        }

    # ── score 批量报告 ────────────────────────────────────────────────
    def _build_score_report(self, results: List[Dict[str, Any]],
                            metrics: List[str], threshold: float) -> Dict[str, Any]:
        """把多条评估结果聚合成批量报告（逐条明细 + 平均分 + 达标/硬失败计数）。"""
        n = len(results)
        scores = [r.get(_SCORE_KEY) for r in results
                  if isinstance(r.get(_SCORE_KEY), (int, float))]
        avg = round(sum(scores) / len(scores), 3) if scores else 0.0
        pass_count = sum(1 for r in results if r.get("verdict") == "PASS")
        hard_failed_count = sum(1 for r in results if r.get("hard_failed"))
        return {
            "status": "completed",
            "action": "score",
            "metric": metrics[0] if len(metrics) == 1 else metrics,
            "threshold": threshold,
            "n": n,
            "avg_score": avg,
            "pass_count": pass_count,
            "hard_failed_count": hard_failed_count,
            "items": results,
            "note": NOTE_AI_JUDGEMENT,
        }
