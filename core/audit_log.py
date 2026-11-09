# -*- coding: utf-8 -*-
"""观测层（5.19.5）—— 审计日志与聚合指标。

把防护点的命中/放行/绕过落盘为 JSONL（按日轮转），为后续 eval 扩展与
对抗测试提供数据地基。日志只记录判定事实，不改任何结论。

日志 schema（每行一个 JSON）：
  ts        ISO 时间
  session   进程级会话 ID（同一 MCP 进程内一致）
  tool      工具名
  gate      {"result": "hit|pass|miss", "hits": ["G-A"], "mode": "warn|block"}
  memory_digest  {"injected": bool, "available": bool, "items": int}
  freshness {"code_newer": bool, "stale_reason": str, "refresh_action": str}
  scope     {"rules": int}
  blocked   {"gate": str, "reason": str}

公开 API：
  - record(project_path, entry) -> None（绝不抛异常）
  - read(project_path, days=7) -> list[dict]
  - metrics(project_path, days=7) -> dict

作者: CodeRef-AI Team
版本: v1.0
"""

import json
import os
import threading
import uuid
from collections import Counter
from datetime import datetime, timedelta
from typing import List

from loguru import logger


AUDIT_DIR = os.path.join(".coderef", "audit_log")
SESSION_ID = str(uuid.uuid4())[:8]

_write_lock = threading.Lock()


def _audit_dir(project_path: str) -> str:
    return os.path.join(os.path.abspath(project_path), AUDIT_DIR)


def record(project_path: str, entry: dict) -> None:
    """追加一条审计日志（按日文件，原子追加）；任何失败静默。"""
    if not project_path or not isinstance(entry, dict):
        return
    try:
        data = {"ts": datetime.now().isoformat(timespec="seconds"),
                "session": SESSION_ID}
        data.update(entry)
        day = datetime.now().strftime("%Y%m%d")
        path = os.path.join(_audit_dir(project_path), f"{day}.jsonl")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        line = json.dumps(data, ensure_ascii=False)
        with _write_lock:
            with open(path, "a", encoding="utf-8") as f:
                f.write(line + "\n")
    except Exception as e:  # noqa: BLE001 —— 观测失败不能影响主流程
        logger.debug(f"[audit_log] 写入失败: {e}")


def read(project_path: str, days: int = 7) -> List[dict]:
    """读取最近 days 天（含今日）的日志；损坏行跳过。"""
    try:
        days = max(1, int(days))
    except (TypeError, ValueError):
        days = 7
    out: List[dict] = []
    for i in range(days):
        day = (datetime.now() - timedelta(days=i)).strftime("%Y%m%d")
        path = os.path.join(_audit_dir(project_path), f"{day}.jsonl")
        if not os.path.exists(path):
            continue
        try:
            with open(path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        item = json.loads(line)
                    except Exception:  # noqa: BLE001
                        continue
                    if isinstance(item, dict):
                        out.append(item)
        except Exception as e:  # noqa: BLE001
            logger.debug(f"[audit_log] 读取失败 {path}: {e}")
    return out


def _top(counter: Counter, n: int = 5) -> List[dict]:
    return [{"key": k, "count": v} for k, v in counter.most_common(n)]


def _dispatcher_stats(project_path: str) -> dict:
    """扫描 .coderef/dispatch 轨迹，统计调度任务结局（只读）。"""
    base = os.path.join(os.path.abspath(project_path), ".coderef", "dispatch")
    stats = Counter()
    if not os.path.isdir(base):
        return {"total": 0, "by_status": {}}
    for fn in os.listdir(base):
        if not fn.endswith(".json"):
            continue
        try:
            with open(os.path.join(base, fn), "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                stats[str(data.get("status") or "unknown")] += 1
        except Exception:  # noqa: BLE001
            continue
    return {"total": sum(stats.values()), "by_status": dict(stats)}


def metrics(project_path: str, days: int = 7) -> dict:
    """聚合指标：闸门命中率、违规 Top、记忆命中率、新鲜度事件、调度结局。"""
    entries = read(project_path, days=days)
    gate_checked = gate_hits = 0
    gate_ids = Counter()
    blocked = Counter()
    blocked_reasons = Counter()
    memory_injected = memory_available = 0
    fresh_stale = fresh_code_newer = 0
    refresh_actions = Counter()
    scope_calls = 0
    scope_rules_max = 0
    tools = Counter()

    for e in entries:
        tools[str(e.get("tool") or "")] += 1
        gate = e.get("gate") if isinstance(e.get("gate"), dict) else {}
        if gate.get("result") in ("hit", "pass"):
            gate_checked += 1
            if gate.get("result") == "hit":
                gate_hits += 1
                for gid in gate.get("hits") or []:
                    gate_ids[str(gid)] += 1
        blocked_info = e.get("blocked") if isinstance(e.get("blocked"), dict) else {}
        if blocked_info:
            blocked[str(blocked_info.get("gate") or "unknown")] += 1
            reason = str(blocked_info.get("reason") or "")[:120]
            if reason:
                blocked_reasons[reason] += 1
        mem = e.get("memory_digest") if isinstance(e.get("memory_digest"), dict) else {}
        if mem.get("injected"):
            memory_injected += 1
            if mem.get("available"):
                memory_available += 1
        fresh = e.get("freshness") if isinstance(e.get("freshness"), dict) else {}
        if fresh.get("stale_reason"):
            fresh_stale += 1
        if fresh.get("code_newer"):
            fresh_code_newer += 1
        if fresh.get("refresh_action"):
            refresh_actions[str(fresh["refresh_action"])[:120]] += 1
        sc = e.get("scope") if isinstance(e.get("scope"), dict) else {}
        if sc.get("rules"):
            scope_calls += 1
            scope_rules_max = max(scope_rules_max, int(sc.get("rules") or 0))

    return {
        "status": "ok",
        "tool": "coderef_dispatch_metrics",
        "project_path": os.path.abspath(project_path),
        "window_days": max(1, int(days or 7)),
        "total_calls": len(entries),
        "top_tools": _top(tools),
        "gate": {
            "checked": gate_checked,
            "hits": gate_hits,
            "hit_rate": round(gate_hits / gate_checked, 3) if gate_checked else 0.0,
            "by_gate": dict(gate_ids),
            "top": _top(gate_ids),
        },
        "blocked": {
            "count": sum(blocked.values()),
            "by_gate": dict(blocked),
            "top_reasons": _top(blocked_reasons, 5),
        },
        "memory": {
            "injected": memory_injected,
            "available": memory_available,
            "hit_rate": round(memory_available / memory_injected, 3)
            if memory_injected else 0.0,
            "note": "hit_rate = 注入且可用的会话次数 / 注入次数；为空说明记忆层尚无内容",
        },
        "freshness": {
            "stale_calls": fresh_stale,
            "code_newer_calls": fresh_code_newer,
            "refresh_events": sum(refresh_actions.values()),
            "top_refresh_actions": _top(refresh_actions, 5),
        },
        "scope": {
            "calls_with_rules": scope_calls,
            "max_rules": scope_rules_max,
        },
        "dispatcher": _dispatcher_stats(project_path),
        "note": ("只统计 .coderef/audit_log 实际落盘记录；"
                 "unknown/missing 是事实，不做推断补齐"),
    }
