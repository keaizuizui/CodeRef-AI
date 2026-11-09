# -*- coding: utf-8 -*-
"""纪律复盘（discipline recall）——回答「你忘了什么」。

定位：不是给主 AI 增加一条上下文规则，而是 CodeRef 子代理（dispatcher）内部
的确定性检查步骤。主 AI 只需发起 `coderef_execute(intent=remind)`，由本模块
读取会话事实、git 指纹、图谱新鲜度与产物状态，返回「忘了什么 + 该补什么」。

诚实边界：
  - 只报能由落盘事实证实的状态；无法判定的（如非 git 仓库）进 unknown，
    不伪装成 forgotten 或 passed；
  - 不猜测主 AI 的意图，也不代替主 AI 执行补齐动作。

作者: CodeRef-AI Team
版本: v1.0
"""

import os
from datetime import datetime
from typing import Dict, List


def _fact(project_path: str, key: str, max_age_hours: float = 24) -> bool:
    try:
        from core.session_guard import get_fact
        return get_fact(project_path, key, max_age_hours=max_age_hours) is not None
    except Exception:  # noqa: BLE001
        return False


def _guard_state(project_path: str) -> Dict[str, str]:
    """返回 ("ok"|"forgotten"|"unknown", 证据)。"""
    try:
        from core.session_guard import git_fingerprint, load_state
    except Exception as e:  # noqa: BLE001
        return "unknown", f"会话守卫不可用: {e}"
    fp = git_fingerprint(project_path)
    if fp.get("error"):
        return "unknown", f"无法判定 git 状态: {fp['error']}"
    if not fp.get("dirty"):
        return "ok", "工作区干净，没有待守护的未提交变更"
    last = load_state(project_path).get("last_guard_fingerprint")
    if not isinstance(last, dict):
        return "forgotten", "工作区有未提交变更，但没有 change_guard 运行记录"
    same = (last.get("head") == fp.get("head")
            and bool(last.get("dirty")) == bool(fp.get("dirty"))
            and last.get("changed_files") == fp.get("changed_files"))
    if same:
        return "ok", "最近一次 change_guard 的指纹与当前工作区一致"
    return "forgotten", "工作区在最近一次 change_guard 之后又发生了变化"


def _dream_recall(project_path: str) -> dict:
    """梦里提醒过什么（5.20.0）：只读 verified 笔记，不把梦当事实。"""
    try:
        from core.dream import digest_meta, read
        meta = digest_meta(project_path)
        if not meta.get("available"):
            return {"available": False, "last_dream_at": meta.get("last_dream_at", ""),
                    "notes": [], "reason": "尚无已验证的梦"}
        data = read(project_path, limit=5, mark_hit=False)
        return {
            "available": True,
            "last_dream_at": meta.get("last_dream_at", ""),
            "notes": [{"kind": n.get("kind"), "text": n.get("text")}
                      for n in data.get("notes") or []],
            "guardrail": "梦是假设，不是事实；引用前先核验 evidence",
        }
    except Exception as e:  # noqa: BLE001 —— 梦不可用不影响纪律复盘
        return {"available": False, "error": str(e), "notes": []}


def recall(project_path: str) -> dict:
    """执行纪律复盘，返回 forgotten / passed / unknown 三类事实。"""
    project_path = os.path.abspath(project_path)
    forgotten: List[dict] = []
    passed: List[dict] = []
    unknown: List[dict] = []

    def _add(bucket, cid, title, evidence, actions=None):
        bucket.append({"id": cid, "title": title, "evidence": evidence,
                       "owner_actions": list(actions or [])})

    # 1. 操作记忆
    if _fact(project_path, "memory_recovered_at"):
        _add(passed, "memory_recovered", "操作记忆已恢复",
             "会话事实 memory_recovered_at 在 24 小时内")
    else:
        _add(forgotten, "memory_recovered", "未恢复操作记忆",
             "会话状态中没有 24 小时内的 memory_recovered_at",
             ["coderef_operation_memory(action=recover)"])

    # 2. 变更守卫
    state, evidence = _guard_state(project_path)
    if state == "ok":
        _add(passed, "change_guard", "变更守卫已覆盖当前工作区", evidence)
    elif state == "forgotten":
        _add(forgotten, "change_guard", "变更守卫未覆盖当前工作区", evidence,
             ["coderef_change_guard"])
    else:
        _add(unknown, "change_guard", "无法判定变更守卫", evidence)

    # 3. 知识图谱新鲜度
    try:
        from core.freshness_guard import query_staleness
        info = query_staleness(project_path)
        if info.get("stale"):
            _add(forgotten, "knowledge_graph", "知识图谱落后于代码",
                 info.get("stale_reason") or "stale=true",
                 ["coderef_memory(action=sync, mode=incr)"])
        else:
            _add(passed, "knowledge_graph", "知识图谱与代码同步", "stale=false")
    except Exception as e:  # noqa: BLE001
        _add(unknown, "knowledge_graph", "无法判定图谱新鲜度", str(e))

    # 4. 审计产物（只对已产出但过期的报告提醒；从未产出不算"忘了"）
    try:
        from core.freshness_guard import freshness_block
        fresh = freshness_block(project_path, ["audit_findings", "scorecard"])
        stale = [i for i in fresh.get("artifacts_used") or []
                 if i.get("exists") and (i.get("stale_by_age") or i.get("code_newer"))]
        if stale:
            names = "、".join(i.get("name") or i.get("key") for i in stale)
            _add(forgotten, "artifact_refresh", "审计产物已过期",
                 f"已产出但过期/落后于代码: {names}",
                 ["coderef_audit"])
        else:
            _add(passed, "artifact_refresh", "审计产物无需刷新",
                 "未发现已产出且过期的审计产物")
    except Exception as e:  # noqa: BLE001
        _add(unknown, "artifact_refresh", "无法判定产物新鲜度", str(e))

    titles = "、".join(f["title"] for f in forgotten)
    verdict = (f"你忘了 {len(forgotten)} 件事：{titles}" if forgotten
               else "没有发现被遗忘的纪律动作")
    from core.result_envelope import envelope
    env = envelope(
        verdict,
        usable=not forgotten,
        blocked_by=[{"id": f["id"], "owner": "consumer",
                     "human": f["title"]} for f in forgotten],
        owner_actions={"consumer": [a for f in forgotten
                                    for a in f["owner_actions"]]},
        cannot_conclude=["未检查到的会话行为", "主 AI 的主观意图"],
        guardrails=["本结果来自落盘事实与 git 状态；unknown 表示无法判定，"
                    "不是通过或失败"],
    )
    return {
        "status": "ok",
        "tool": "coderef_recall",
        "project_path": project_path,
        "checked_at": datetime.now().isoformat(timespec="seconds"),
        "verdict": verdict,
        "forgotten": forgotten,
        "passed": passed,
        "unknown": unknown,
        "dream": _dream_recall(project_path),
        "envelope": env,
        "note": "确定性复盘：forgotten=有事实依据的遗漏；unknown=无法判定，不美化",
    }
