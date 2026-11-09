# -*- coding: utf-8 -*-
"""dispatcher 形态 B：LLM 意图层（5.19.4）。

职责只有一个：把自然语言意图翻译成链枚举（probe/single_audit/recover/
governance/asset）。它不影响任何审计结论——结论仍全部来自规则引擎与静态
分析，LLM 只决定「选哪条链」。

红线：
  - 未配置有效 API Key → 硬阻断（status=blocked），明确降级回形态 A
    （让调用方改用 intent 枚举），绝不猜、绝不编造；
  - 置信度低于阈值 / LLM 返回不可解析 → status=needs_confirmation，
    列出候选让用户选，不擅自执行；
  - 任何异常都收敛为结构化状态，不抛出。

作者: CodeRef-AI Team
版本: v1.0
"""

import json
import re
from typing import Optional

from loguru import logger


# 与 dispatcher.INTENTS 同源（此处不 import 避免潜在循环依赖）
INTENTS = ("probe", "single_audit", "recover", "governance", "asset")
INTENT_LABELS = {
    "probe": "L1 变更探查（提交前快速自查）",
    "single_audit": "单次全量审计",
    "recover": "上下文恢复（找回工具位置与既有约定）",
    "governance": "L2 架构治理（捋管线→诊断→定标→治理→验证→体检）",
    "asset": "L3 资产沉淀（创新识别→登记→固化）",
}
CONFIDENCE_THRESHOLD = 0.6

_SYSTEM_PROMPT = (
    "你是 CodeRef-AI 的意图分类器。只做一件事：把用户目标归到下列枚举之一，"
    "不执行任何工具、不编造结论。\n"
    + "\n".join(f"- {k}: {v}" for k, v in INTENT_LABELS.items())
    + "\n只输出 JSON：{\"intent\": \"<枚举>\", \"confidence\": <0-1>, "
      "\"reason\": \"<一句话>\"}。无法判断时 intent 为 null、confidence 为 0。"
)


def _parse_json(text: str) -> Optional[dict]:
    if not text:
        return None
    cleaned = text.strip()
    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
    cleaned = re.sub(r"\s*```$", "", cleaned)
    try:
        data = json.loads(cleaned)
        return data if isinstance(data, dict) else None
    except Exception:  # noqa: BLE001
        m = re.search(r"\{.*\}", cleaned, re.DOTALL)
        if not m:
            return None
        try:
            data = json.loads(m.group(0))
            return data if isinstance(data, dict) else None
        except Exception:  # noqa: BLE001
            return None


def classify(text: str) -> dict:
    """自然语言 → 意图枚举。返回结构化状态，绝不抛异常。"""
    text = (text or "").strip()
    if not text:
        return {"status": "error", "mode": "llm",
                "error": "intent_text 不能为空"}
    try:
        from core.llm_integration import LLMIntegration
        llm = LLMIntegration()
        if not llm.is_available():
            return {
                "status": "blocked",
                "mode": "rule",
                "reason": "未配置可用的 LLM API Key，形态 B 不可用；已降级为规则模式",
                "fallback": "改用 coderef_plan(intent=<枚举>) 选择链，"
                            "或先配置 LLM Key 后重试",
                "supported_intents": list(INTENTS),
            }
        raw = llm.chat_completion(
            [{"role": "system", "content": _SYSTEM_PROMPT},
             {"role": "user", "content": text}],
            temperature=0.0)
        if LLMIntegration.is_error_response(raw):
            return {"status": "error", "mode": "llm",
                    "error": str(raw)[:300],
                    "fallback": "改用 intent 枚举（规则模式）"}
        data = _parse_json(raw)
        if not data:
            return {"status": "error", "mode": "llm",
                    "error": "LLM 返回不可解析（不是 JSON）",
                    "raw": str(raw)[:300],
                    "fallback": "改用 intent 枚举（规则模式）"}
        intent = data.get("intent")
        try:
            confidence = float(data.get("confidence") or 0)
        except (TypeError, ValueError):
            confidence = 0.0
        if intent not in INTENTS:
            return {"status": "needs_confirmation", "mode": "llm",
                    "reason": "LLM 无法确定意图",
                    "confidence": confidence,
                    "candidates": list(INTENTS),
                    "raw": str(raw)[:300]}
        if confidence < CONFIDENCE_THRESHOLD:
            return {"status": "needs_confirmation", "mode": "llm",
                    "reason": f"意图置信度 {confidence:.2f} 低于阈值 "
                              f"{CONFIDENCE_THRESHOLD}",
                    "intent": intent, "confidence": confidence,
                    "candidates": list(INTENTS)}
        return {"status": "ok", "mode": "llm", "intent": intent,
                "confidence": confidence,
                "reason": str(data.get("reason") or "")[:300],
                "note": "LLM 只选择链，不参与任何审计结论"}
    except Exception as e:  # noqa: BLE001 —— 意图层故障不阻断，降级回规则模式
        logger.debug(f"[intent_router] 意图识别失败: {e}")
        return {"status": "error", "mode": "llm",
                "error": f"意图识别失败: {e}",
                "fallback": "改用 intent 枚举（规则模式）"}
