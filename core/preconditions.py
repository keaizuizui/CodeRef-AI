# -*- coding: utf-8 -*-
"""precondition 闸门（5.19.2）—— 把 skill 里的「先做什么再做什么」变成服务端检查。

MCP 协议本身是被动响应模型：工具无法拒绝顺序错误的调用，只能在结果里提示。
本模块在工具执行入口统一检查四类前置事实，未满足时给出 blocked 信封
（`status=blocked` + `envelope.blocked_by[].owner=consumer` + owner_actions），
而不是执行一个带病结论。

首批闸门：
  G-A 未恢复记忆即调用依赖记忆的工具 → 引导 coderef_operation_memory(action=recover)
  G-B 图谱过期即调用图谱查询类工具   → 引导 coderef_memory(action=sync, mode=incr)
  G-C 定标缺失即调用定标依赖工具     → 引导 coderef_target_arch_set
  G-D 审计产物超龄即调用健康解读     → 引导 coderef_audit

边界：
  - 只读探查工具（version / task_status / scan_list / scope 等）永远放行；
  - 默认 warn（只附 gate_warnings，不阻断），配置 `preconditions.mode=block`
    或按闸门单独配置后才会返回 blocked（灰度期先 warn 后 block）；
  - 闸门只拦「证据不足的结论路径」，不替调用方做业务决策。

作者: CodeRef-AI Team
版本: v1.0
"""

import os
from typing import List, Optional

from loguru import logger


# 永远放行的只读/状态类工具（不产生依赖前置事实的结论）
READONLY_TOOLS = frozenset({
    "coderef_version", "coderef_task_status", "coderef_task_cancel",
    "coderef_scan_list", "coderef_scope", "coderef_plan",
    "coderef_execute", "coderef_dispatch_status", "coderef_dispatch_report",
    "coderef_dispatch_metrics", "coderef_docs_read", "coderef_whitelist",
})

# G-A：依赖操作记忆的重工具
GATE_A_TOOLS = frozenset({
    "coderef_audit", "coderef_scan", "coderef_query", "coderef_report",
    "coderef_interpret", "coderef_arch_gap", "coderef_arch_verify",
    "coderef_change_guard", "coderef_change_report", "coderef_verify_findings",
    "coderef_owasp", "coderef_review",
})

# G-B：依赖知识图谱的查询/诊断类工具
GATE_B_TOOLS = frozenset({
    "coderef_query", "coderef_arch_gap", "coderef_arch_verify",
    "coderef_flow_verify", "coderef_verify_findings", "coderef_interpret",
})

# G-C：依赖定标（target_arch）的工具
GATE_C_TOOLS = frozenset({
    "coderef_arch_verify", "coderef_arch_gap", "coderef_arch_canvas",
    "coderef_refactor_plan", "coderef_target_adopt",
})

# G-D：依赖审计产物新鲜度的健康解读
GATE_D_TOOLS = frozenset({"coderef_interpret"})

GATE_META = {
    "G-A": {"title": "未恢复操作记忆",
            "actions": ["coderef_operation_memory(action=recover)"]},
    "G-B": {"title": "知识图谱落后于代码",
            "actions": ["coderef_memory(action=sync, mode=incr)"]},
    "G-C": {"title": "尚未设置目标架构",
            "actions": ["coderef_target_arch_set"]},
    "G-D": {"title": "审计产物超龄",
            "actions": ["coderef_audit"]},
}


def _mode_for(project_path: str, gate_id: str) -> str:
    """闸门生效模式：off / warn / block（默认 follow 全局 preconditions.mode）。"""
    try:
        from core.freshness_guard import guard_config
        cfg = guard_config(project_path).get("preconditions") or {}
    except Exception:  # noqa: BLE001
        return "warn"
    gates = cfg.get("gates") if isinstance(cfg.get("gates"), dict) else {}
    mode = str(gates.get(gate_id) or cfg.get("mode") or "warn").lower()
    return mode if mode in ("off", "warn", "block") else "warn"


def _fact_ok(project_path: str, key: str) -> bool:
    from core.session_guard import get_fact
    return get_fact(project_path, key, max_age_hours=24) is not None


def _hit(gate_id: str, reason: str) -> dict:
    return {"id": gate_id, "title": GATE_META[gate_id]["title"],
            "reason": reason, "owner": "consumer",
            "owner_actions": list(GATE_META[gate_id]["actions"])}


def evaluate(tool: str, project_path: str, args: Optional[dict] = None) -> dict:
    """评估一次调用的前置条件。

    返回 {"mode": str, "hits": [...], "blocked": bool, "blocking": dict|None,
          "checked": bool}。checked=False 表示该工具不适用任何闸门（只读/
    未知工具/评估异常），审计日志应记为 miss 而不是 pass。
    任何内部异常都收敛为"未检查且放行"，绝不因闸门自身故障拦住调用。
    """
    args = args or {}
    result = {"mode": "warn", "hits": [], "blocked": False,
              "blocking": None, "checked": False}
    if not tool or not project_path or tool in READONLY_TOOLS:
        return result
    applicable = (tool in GATE_A_TOOLS or tool in GATE_B_TOOLS
                  or tool in GATE_C_TOOLS or tool in GATE_D_TOOLS)
    if not applicable:
        return result
    result["checked"] = True
    try:
        hits: List[dict] = []

        if tool in GATE_A_TOOLS and not _fact_ok(project_path, "memory_recovered_at"):
            hits.append(_hit(
                "G-A", "本次会话尚未恢复操作记忆，先 recover 再产出结论"))

        if tool in GATE_B_TOOLS:
            from core.freshness_guard import query_staleness
            info = query_staleness(project_path)
            if info.get("stale"):
                hits.append(_hit(
                    "G-B", info.get("stale_reason") or "知识图谱落后于代码"))

        if tool in GATE_C_TOOLS and not args.get("target_arch"):
            path = os.path.join(project_path, ".coderef", "target_arch.json")
            if not os.path.exists(path):
                hits.append(_hit(
                    "G-C", "项目尚未设置目标架构，基于定标的指标不可用"))

        if tool in GATE_D_TOOLS and args.get("action", "health") == "health":
            from core.freshness_guard import freshness_block
            fresh = freshness_block(project_path, ["audit_findings", "scorecard"])
            used = fresh.get("artifacts_used") or []
            stale = [i for i in used if (not i.get("exists")
                                         or i.get("stale_by_age")
                                         or i.get("code_newer"))]
            if stale:
                names = "、".join(i.get("name") or i.get("key") for i in stale)
                hits.append(_hit("G-D", f"审计产物缺失或超龄：{names}"))

        result["hits"] = hits
        if hits:
            # 命中模式取最严格者：任一门配置 block 即阻断
            modes = [_mode_for(project_path, h["id"]) for h in hits]
            active = [h for h, m in zip(hits, modes) if m != "off"]
            result["hits"] = active
            if any(m == "block" for m in modes):
                result["blocked"] = True
                result["blocking"] = next(
                    h for h, m in zip(hits, modes) if m == "block")
                result["mode"] = "block"
            else:
                result["mode"] = "warn"
        return result
    except Exception as e:  # noqa: BLE001 —— 闸门自身故障不拦调用
        logger.debug(f"[preconditions] 闸门评估失败（放行）: {e}")
        return {"mode": "warn", "hits": [], "blocked": False,
                "blocking": None, "checked": False}


def blocked_result(tool: str, hit: dict) -> dict:
    """构造 blocked 返回体：状态 + 信封 + 双方动作，语义与层1 裸工具一致。"""
    from core.result_envelope import envelope
    reason = hit.get("reason") or hit.get("title") or "前置条件未满足"
    actions = list(hit.get("owner_actions") or [])
    env = envelope(
        f"调用未执行：{reason}",
        usable=False,
        blocked_by=[{"id": hit.get("id", "precondition"),
                     "owner": "consumer", "human": reason}],
        owner_actions={"consumer": actions},
        cannot_conclude=["本次调用的任何结论（工具未执行）"],
        guardrails=["blocked 是事实：先补齐前置动作再重试；不要把它当工具故障"],
    )
    return {
        "status": "blocked",
        "tool": tool,
        "gate": hit.get("id", ""),
        "reason": reason,
        "owner_actions": {"consumer": actions},
        "envelope": env,
    }
