# -*- coding: utf-8 -*-
"""coderef-dispatcher 形态 A（5.19.3）——把「编排 58 个工具」降级为「调用 4 个调度工具」。

定位：零 LLM 的规则引擎。链定义来自 `core/dispatch_chains.yaml`（唯一事实源），
执行轨迹落盘 `.coderef/dispatch/<task_id>.json` 供审计与排查；当前版本重新执行
会从链首开始（不支持断点续跑）。与层1 的 blocked 语义不同：dispatcher 的执行器
是自己人，跳步可以当场补齐（缺 recover 先 recover、图旧先 sync），主 AI 只收
计划卡与最终摘要。

公开 API：
  - plan(intent, project_path) -> dict 计划卡（纯规划，落盘 plan）
  - load_plan(project_path, plan_id) -> dict
  - execute(plan, runner, task_id, project_path, progress_cb) -> dict 执行并落轨迹
  - status(project_path, task_id) -> dict
  - report(project_path, task_id) -> dict 最终摘要信封

降级：YAML 缺失/损坏时 plan 返回 status=error + 回退建议（手工链或裸工具）；
dispatcher 不可用不影响 58 个裸工具照常使用。

作者: CodeRef-AI Team
版本: v1.0
"""

import json
import os
import re
import time
import uuid
from datetime import datetime
from typing import Callable, Dict, List, Optional

from core.dispatch_intents import INTENTS


CHAINS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "dispatch_chains.yaml")
DISPATCH_DIR = os.path.join(".coderef", "dispatch")
PLANS_DIR = os.path.join(DISPATCH_DIR, "plans")

# id 只允许生成格式（uuid 片段/下划线/短横线），防止调用方拼出路径穿越
_ID_RE = re.compile(r"^[0-9A-Za-z_-]{1,64}$")

# 闸门命中时的自动补齐动作（G-C 需要用户给目标架构，无法自动补）
REMEDY = {
    "G-A": ("coderef_operation_memory", {"action": "recover", "limit": 8}),
    "G-B": ("coderef_memory", {"action": "sync", "mode": "incr"}),
    "G-D": ("coderef_audit", {"strategy": "incr"}),
}


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _dispatch_dir(project_path: str) -> str:
    return os.path.join(os.path.abspath(project_path), DISPATCH_DIR)


def _safe_id(value: str) -> str:
    """校验 task_id/plan_id 只含生成格式字符，防止路径穿越。"""
    if not isinstance(value, str) or not _ID_RE.match(value):
        raise ValueError(f"非法 id: {value!r}（只允许字母、数字、下划线、短横线）")
    return value


def _trace_path(project_path: str, task_id: str) -> str:
    return os.path.join(_dispatch_dir(project_path), f"{_safe_id(task_id)}.json")


def _plan_path(project_path: str, plan_id: str) -> str:
    return os.path.join(os.path.abspath(project_path), PLANS_DIR,
                        f"{_safe_id(plan_id)}.json")


def _write_json(path: str, data: dict) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def _read_json(path: str) -> Optional[dict]:
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else None
    except Exception:  # noqa: BLE001
        return None


def load_chains() -> dict:
    """读取链定义；缺失/损坏时返回 {"error": 原因}（不猜不编）。"""
    try:
        import yaml
        with open(CHAINS_FILE, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
        if not isinstance(data, dict) or not isinstance(data.get("chains"), dict):
            return {"error": "链定义格式不合法（缺少 chains）"}
        return data
    except FileNotFoundError:
        return {"error": f"链定义缺失: {CHAINS_FILE}"}
    except Exception as e:  # noqa: BLE001
        return {"error": f"链定义加载失败: {e}"}


def _fallback_hint() -> dict:
    return {"fallback": "dispatcher 不可用时，全部裸工具照常使用；"
                        "也可按 skills/ 的 coderef-mcp 手工编排"}


def plan(intent: str, project_path: str, intent_text: str = "") -> dict:
    """生成计划卡（纯规划不执行），并落盘到 .coderef/dispatch/plans/。

    5.19.4：传 intent_text 时先走 LLM 意图层（形态 B）选链；无 Key 硬阻断并
    提示改用 intent 枚举；置信度不足时不擅自执行，返回候选让用户确认。
    """
    route = None
    mode = "rule"
    if intent_text:
        from core.intent_router import classify
        route = classify(intent_text)
        if route.get("status") != "ok":
            return {"status": route.get("status", "error"),
                    "tool": "coderef_plan",
                    "project_path": os.path.abspath(project_path),
                    "intent_text": intent_text,
                    "mode": route.get("mode", "llm"),
                    "router": route,
                    **_fallback_hint()}
        intent = route.get("intent") or intent
        mode = "llm"

    chains = load_chains()
    if chains.get("error"):
        return {"status": "error", "tool": "coderef_plan",
                "intent": intent, "error": chains["error"], **_fallback_hint()}
    if intent not in INTENTS:
        return {"status": "error", "tool": "coderef_plan",
                "error": f"未知意图 {intent!r}，支持: {'/'.join(INTENTS)}"}
    chain = (chains.get("chains") or {}).get(intent)
    if not isinstance(chain, dict):
        return {"status": "error", "tool": "coderef_plan", "intent": intent,
                "error": f"意图 {intent!r} 未定义链", **_fallback_hint()}

    steps = []
    decision_points = []
    for step in chain.get("steps") or []:
        if not isinstance(step, dict) or not step.get("tool"):
            continue
        item = {"id": step.get("id") or step["tool"],
                "tool": step["tool"],
                "args": dict(step.get("args") or {}),
                "fail": step.get("fail", "stop")}
        if step.get("requires_user"):
            item["requires_user"] = True
            item["decision"] = step.get("decision", "")
            decision_points.append({"step": item["id"],
                                    "decision": item.get("decision", "")})
        steps.append(item)

    plan_id = str(uuid.uuid4())[:8]
    card = {
        "status": "ok",
        "tool": "coderef_plan",
        "plan_id": plan_id,
        "intent": intent,
        "title": chain.get("title", intent),
        "project_path": os.path.abspath(project_path),
        "steps": steps,
        "estimated_tool_count": len(steps),
        "estimated_seconds": int(chain.get("estimate_seconds") or 0),
        "decision_points": decision_points,
        "mode": mode,
        "intent_text": intent_text,
        "router": route or {"mode": "rule", "note": "枚举意图（形态 A）"},
        "note": "纯规划不执行；确认后用 coderef_execute(plan_id=...) 执行",
        "chain_source": CHAINS_FILE,
    }
    try:
        _write_json(_plan_path(project_path, plan_id), card)
    except Exception as e:  # noqa: BLE001 —— 计划落盘失败仍可返回卡片
        card["plan_persist_error"] = str(e)
    return card


def load_plan(project_path: str, plan_id: str) -> Optional[dict]:
    try:
        return _read_json(_plan_path(project_path, plan_id))
    except ValueError:
        return None  # 非法 id 与"不存在"同样返回 None，由调用方给结构化错误


def _render_args(template: dict, project_path: str) -> dict:
    # 默认注入 project_path：即使链定义漏写占位符，也不会把空路径传给工具
    out = {"project_path": os.path.abspath(project_path)}
    for k, v in (template or {}).items():
        if isinstance(v, str) and "{project_path}" in v:
            out[k] = v.replace("{project_path}", os.path.abspath(project_path))
        else:
            out[k] = v
    return out


def _summarize(raw: str) -> dict:
    """把工具返回压成摘要（上下文隔离：中间结果不进主 AI）。"""
    summary = {"raw_chars": len(raw or "")}
    try:
        data = json.loads(raw)
    except Exception:  # noqa: BLE001
        summary["status"] = "text"
        return summary
    if not isinstance(data, dict):
        summary["status"] = "non_object"
        return summary
    summary["status"] = data.get("status") or ("error" if data.get("error") else "ok")
    for key in ("tool", "verdict", "total", "errors", "envelope"):
        if key in data:
            summary[key] = data.get(key)
    if isinstance(data.get("findings"), list):
        summary["findings"] = len(data["findings"])
    if isinstance(data.get("summary"), dict):
        summary["summary"] = data.get("summary")
    if data.get("error"):
        summary["error"] = str(data.get("error"))[:300]
    return summary


def _failed(summary: dict) -> bool:
    status = str(summary.get("status") or "").lower()
    return status in ("error", "blocked", "failed", "partial_failed") \
        or (bool(summary.get("error")) and status not in ("ok", "completed", "text"))


def _gate_remediation(hits: List[dict], runner: Callable, project_path: str,
                      trace: dict, step_id: str) -> Optional[dict]:
    """对闸门命中做自动补齐；返回暂停决策（G-C）或 None。

    补齐动作本身失败也继续（记录事实，不阻断整链）——dispatcher 的原则是
    「能补就补，补不了如实记录」，而不是把整条链停死。
    """
    for hit in hits or []:
        gate = hit.get("id")
        entry = {"step": step_id, "gate": gate,
                 "title": hit.get("title", ""),
                 "reason": hit.get("reason", ""), "action": "", "result": ""}
        remedy = REMEDY.get(gate)
        if not remedy:
            entry["action"] = "pause_for_user"
            entry["result"] = "需要用户决策，链已暂停"
            trace.setdefault("gates", []).append(entry)
            return {"gate": gate, "reason": hit.get("reason", ""),
                    "owner_actions": hit.get("owner_actions") or []}
        tool, args = remedy
        args = dict(args, project_path=project_path)
        entry["action"] = tool
        try:
            raw = runner(tool, args)
            entry["result"] = _summarize(raw).get("status", "ok")
        except Exception as e:  # noqa: BLE001
            entry["result"] = f"补齐失败: {e}"
        trace.setdefault("gates", []).append(entry)
    return None


class _Progress:
    """把 dispatcher 步骤进度转成 _bg 的 progress 回调格式。"""

    def __init__(self, cb, total):
        self.cb = cb
        self.total = total
        self.done = 0

    def step(self, step_id, status):
        self.done += 1
        if not self.cb:
            return
        try:
            self.cb(f"dispatch:{step_id}", self.done, self.total, status)
        except Exception:  # noqa: BLE001 —— 进度上报不影响执行
            pass


def execute(plan_card: dict, runner: Callable, project_path: str,
            task_id: str = "", progress_cb=None) -> dict:
    """执行计划卡：逐步过闸门、违规自动补齐、轨迹落盘。"""
    if not isinstance(plan_card, dict) or not plan_card.get("steps"):
        return {"status": "error", "error": "计划卡无效（缺少 steps）"}
    from core.preconditions import evaluate

    task_id = task_id or str(uuid.uuid4())[:8]
    try:
        _safe_id(task_id)
    except ValueError as e:
        return {"status": "error", "error": str(e)}
    trace = {
        "task_id": task_id,
        "plan_id": plan_card.get("plan_id", ""),
        "intent": plan_card.get("intent", ""),
        "project_path": os.path.abspath(project_path),
        "status": "running",
        "current_step": "",
        "started_at": _now(),
        "finished_at": "",
        "steps": [],
        "gates": [],
        "paused": None,
        "raw_chars_total": 0,
    }
    steps = plan_card.get("steps") or []
    progress = _Progress(progress_cb, len(steps))
    _write_json(_trace_path(project_path, task_id), trace)

    for step in steps:
        sid = step.get("id") or step.get("tool")
        trace["current_step"] = sid
        entry = {"id": sid, "tool": step.get("tool", ""),
                 "started_at": _now(), "elapsed": 0.0, "status": "running"}
        # 需要用户决策的步骤：暂停并浮出，不替用户决策
        if step.get("requires_user"):
            entry["status"] = "pending_user"
            entry["decision"] = step.get("decision", "")
            trace["steps"].append(entry)
            trace["status"] = "paused"
            trace["paused"] = {"step": sid, "decision": step.get("decision", ""),
                               "owner_actions": [
                                   "请先完成该决策；当前版本重新执行会从链首开始"
                                   "（不支持断点续跑）"]}
            _write_json(_trace_path(project_path, task_id), trace)
            trace["finished_at"] = _now()
            _write_json(_trace_path(project_path, task_id), trace)
            progress.step(sid, "pending_user")
            return {"status": "paused", "task_id": task_id,
                    "paused": trace["paused"], "trace_path":
                    _trace_path(project_path, task_id)}

        args = _render_args(step.get("args") or {}, project_path)
        t0 = time.time()
        try:
            gate = evaluate(step.get("tool", ""), project_path, args)
            paused = _gate_remediation(gate.get("hits") or [], runner,
                                       project_path, trace, sid)
            if paused:
                entry["status"] = "pending_user"
                entry["elapsed"] = round(time.time() - t0, 2)
                trace["steps"].append(entry)
                trace["status"] = "paused"
                trace["paused"] = dict(paused, step=sid)
                _write_json(_trace_path(project_path, task_id), trace)
                trace["finished_at"] = _now()
                _write_json(_trace_path(project_path, task_id), trace)
                progress.step(sid, "pending_user")
                return {"status": "paused", "task_id": task_id,
                        "paused": trace["paused"], "trace_path":
                        _trace_path(project_path, task_id)}

            raw = runner(step.get("tool", ""), args)
            summary = _summarize(raw)
            trace["raw_chars_total"] += len(raw or "")
            entry["status"] = "failed" if _failed(summary) else "completed"
            entry["summary"] = summary
        except Exception as e:  # noqa: BLE001 —— 单步异常按失败策略处理
            entry["status"] = "failed"
            entry["error"] = str(e)
        entry["elapsed"] = round(time.time() - t0, 2)
        trace["steps"].append(entry)
        _write_json(_trace_path(project_path, task_id), trace)
        progress.step(sid, entry["status"])
        if entry["status"] == "failed" and step.get("fail", "stop") == "stop":
            trace["status"] = "failed"
            break

    if trace["status"] == "running":
        trace["status"] = "completed"
    trace["current_step"] = ""
    trace["finished_at"] = _now()
    _write_json(_trace_path(project_path, task_id), trace)
    return {"status": trace["status"], "task_id": task_id,
            "trace_path": _trace_path(project_path, task_id),
            "steps": len(trace["steps"]), "gates": len(trace.get("gates") or [])}


def status(project_path: str, task_id: str) -> dict:
    try:
        trace = _read_json(_trace_path(project_path, task_id))
    except ValueError as e:
        return {"status": "error", "tool": "coderef_dispatch_status",
                "error": str(e)}
    if trace is None:
        return {"status": "error", "tool": "coderef_dispatch_status",
                "error": f"不存在 dispatch 任务: {task_id}"}
    return {
        "status": trace.get("status", ""),
        "tool": "coderef_dispatch_status",
        "task_id": task_id,
        "intent": trace.get("intent", ""),
        "current_step": trace.get("current_step", ""),
        "started_at": trace.get("started_at", ""),
        "finished_at": trace.get("finished_at", ""),
        "paused": trace.get("paused"),
        "steps": [
            {"id": s.get("id"), "tool": s.get("tool"),
             "status": s.get("status"), "elapsed": s.get("elapsed"),
             "error": s.get("error", ""),
             "summary_status": (s.get("summary") or {}).get("status", "")}
            for s in trace.get("steps") or []],
        "gates": trace.get("gates") or [],
        "trace_path": _trace_path(project_path, task_id),
    }


def report(project_path: str, task_id: str) -> dict:
    """最终摘要信封：结论 + freshness + KL + owner_actions + 中间步骤索引。"""
    from core.result_envelope import envelope

    trace = None
    try:
        trace = _read_json(_trace_path(project_path, task_id))
    except ValueError as e:
        return {"status": "error", "tool": "coderef_dispatch_report",
                "error": str(e)}
    if trace is None:
        return {"status": "error", "tool": "coderef_dispatch_report",
                "error": f"不存在 dispatch 任务: {task_id}"}

    envs = []
    limitations: List[str] = []
    blocked: List[dict] = []
    owner_actions: Dict[str, List[str]] = {"coderef": [], "consumer": []}
    freshness = None
    for s in trace.get("steps") or []:
        env = (s.get("summary") or {}).get("envelope")
        if not isinstance(env, dict):
            continue
        envs.append(env)
        for lim in env.get("limitations") or []:
            if lim not in limitations:
                limitations.append(lim)
        blocked.extend(env.get("blocked_by") or [])
        for owner in ("coderef", "consumer"):
            for act in (env.get("owner_actions") or {}).get(owner) or []:
                if act not in owner_actions[owner]:
                    owner_actions[owner].append(act)
        if not freshness and isinstance(env.get("freshness"), dict):
            freshness = env["freshness"]

    status_text = trace.get("status", "")
    usable = status_text == "completed" and not blocked
    verdict = {
        "completed": f"调度完成：{trace.get('intent')} 链共 "
                     f"{len(trace.get('steps') or [])} 步",
        "paused": "调度暂停：存在需要用户决策的步骤，链未继续",
        "failed": "调度失败：链在中途停止，请按步骤索引排查",
    }.get(status_text, f"调度状态：{status_text}")
    env = envelope(verdict, usable=usable, blocked_by=blocked,
                   owner_actions=owner_actions,
                   cannot_conclude=["未执行步骤的任何结论"],
                   guardrails=["中间步骤结果默认折叠在 step_index，"
                               "引用结论前先看 envelope.freshness"],
                   limitations=limitations)
    if freshness:
        env["freshness"] = freshness

    report_chars = len(json.dumps(env, ensure_ascii=False))
    raw_total = int(trace.get("raw_chars_total") or 0)
    return {
        "status": "ok" if status_text == "completed" else status_text,
        "tool": "coderef_dispatch_report",
        "task_id": task_id,
        "intent": trace.get("intent", ""),
        "started_at": trace.get("started_at", ""),
        "finished_at": trace.get("finished_at", ""),
        "envelope": env,
        "paused": trace.get("paused"),
        "gates": trace.get("gates") or [],
        "step_index": [
            {"id": s.get("id"), "tool": s.get("tool"),
             "status": s.get("status"), "elapsed": s.get("elapsed"),
             "summary": s.get("summary", {}) if s.get("status") != "running" else {}}
            for s in trace.get("steps") or []],
        "context": {
            "raw_chars_total": raw_total,
            "report_chars": report_chars,
            "saved_ratio": round(1 - report_chars / raw_total, 3) if raw_total else 0.0,
            "note": "中间工具返回不逐条进主 AI；step_index 可追溯，默认折叠",
        },
        "trace_path": _trace_path(project_path, task_id),
    }
