# -*- coding: utf-8 -*-
"""会话级变更守卫 —— 把「改完代码必须跑守卫」变成服务端自动行为。

痛点：调用方 AI 经常忘记在改完代码后调用 change_guard，导致能力退化无人拦截。
本模块在审计/查询等重工具被调用前，比对 git 工作区与上次守卫时的指纹：
指纹变化才自动跑一次 ChangeGuard，并把结果落到项目侧会话状态，
避免每次重工具调用都重复执行守卫，也避免守卫异常拖垮审计主流程。

核心行为：
  1. load_state / save_state：读写 <project>/.coderef/session_state.json（原子写）；
  2. git_fingerprint：用 git rev-parse HEAD + git status --porcelain 生成轻量指纹；
  3. should_guard：指纹有变化（且 git 可用）时才需要再跑守卫；
  4. record_guard：把守卫结果写入会话状态，供下次比对与审计回溯；
  5. guard_if_needed：需要时调用 ChangeGuard.guard，异常一律吞掉并记录。

设计约束：
  - 只读写 .coderef/session_state.json，不产生其他文件；
  - ChangeGuard 在函数内延迟导入，杜绝模块级循环依赖；
  - 所有异常 best-effort 吞掉并记录日志，绝不让审计调用失败。

作者: CodeRef-AI Team
版本: v1.0
"""

import json
import os
import subprocess
from datetime import datetime
from typing import Any, Dict, Optional, Tuple

from loguru import logger


# ═══════════════════════════════════════════════════════════════════════
# 模块级常量（集中管理 magic number）
# ═══════════════════════════════════════════════════════════════════════

# 触发自动守卫的重工具（审计/查询类，改完代码后最该先跑守卫）
HEAVY_TOOLS = frozenset({
    "coderef_audit", "coderef_scan", "coderef_query", "coderef_report",
    "coderef_interpret", "coderef_arch_gap", "coderef_arch_verify",
})

# 会话状态文件位置（相对项目根目录）
STATE_DIR = ".coderef"
STATE_FILE = "session_state.json"

# git 命令超时（秒）。指纹只需轻量查询，超时压到 10s 以内，避免拖慢审计。
GIT_TIMEOUT = 10


# ═══════════════════════════════════════════════════════════════════════
# 会话状态读写
# ═══════════════════════════════════════════════════════════════════════

def _state_path(project_path: str) -> str:
    """会话状态文件的绝对路径。"""
    return os.path.join(project_path, STATE_DIR, STATE_FILE)


def load_state(project_path: str) -> dict:
    """读取会话状态；文件不存在 / 损坏 / 读取失败均返回空 dict，绝不抛异常。"""
    try:
        with open(_state_path(project_path), "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except FileNotFoundError:
        return {}
    except Exception as e:  # noqa: BLE001 —— 损坏 JSON 等一律降级为空状态
        logger.debug(f"[session_guard] 会话状态读取失败: {e}")
        return {}


def save_state(project_path: str, state: dict) -> None:
    """原子写入会话状态（tmp + os.replace）；失败静默，绝不抛异常。"""
    target = _state_path(project_path)
    tmp = target + ".tmp"
    try:
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
        os.replace(tmp, target)
    except Exception as e:  # noqa: BLE001 —— 状态落盘失败不应影响主流程
        logger.debug(f"[session_guard] 会话状态写入失败: {e}")
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except OSError:
            pass


# ═══════════════════════════════════════════════════════════════════════
# git 指纹
# ═══════════════════════════════════════════════════════════════════════

def _git(args: list, cwd: str) -> Tuple[int, str, str]:
    """执行 git 命令，返回 (returncode, stdout, stderr)。绝不抛异常。

    所有 git 调用都集中在此，便于测试统一 patch。
    """
    try:
        r = subprocess.run(
            ["git"] + list(args), cwd=cwd, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=GIT_TIMEOUT,
            stdin=subprocess.DEVNULL,
        )
        return r.returncode, (r.stdout or "").strip(), (r.stderr or "").strip()
    except Exception as e:  # noqa: BLE001 —— git 不可用/超时统一降级
        logger.debug(f"[session_guard] git 命令执行失败: {args} → {e}")
        return -1, "", str(e)


def git_fingerprint(project_path: str) -> dict:
    """生成项目工作区的轻量指纹。

    返回 {"head": str, "dirty": bool, "changed_files": int, "error": str}。
    非 git 仓库 / git 不可用时 head=""、dirty=False、changed_files=0，error 说明原因。
    """
    empty = {"head": "", "dirty": False, "changed_files": 0}
    if not os.path.isdir(project_path):
        return dict(empty, error=f"项目目录不存在: {project_path}")

    rc, head, err = _git(["rev-parse", "HEAD"], project_path)
    if rc != 0:
        return dict(empty, error=err or "非 git 仓库或仓库尚无提交")

    rc, status_out, err = _git(["status", "--porcelain"], project_path)
    if rc != 0:
        return dict(empty, head=head, error=err or "git status 执行失败")

    lines = [line for line in status_out.splitlines() if line.strip()]
    return {
        "head": head,
        "dirty": bool(lines),
        "changed_files": len(lines),
        "error": "",
    }


def _same_fingerprint(a: dict, b: dict) -> bool:
    """两个指纹是否描述同一工作区状态（head / dirty / changed_files）。"""
    if not isinstance(a, dict):
        return False
    return (a.get("head") == b.get("head")
            and bool(a.get("dirty")) == bool(b.get("dirty"))
            and a.get("changed_files") == b.get("changed_files"))


def _needs_guard(fingerprint: dict, state: dict) -> bool:
    """根据当前指纹与会话状态判断是否还需要跑守卫。"""
    if fingerprint.get("error"):
        return False
    last = state.get("last_guard_fingerprint")
    return not _same_fingerprint(last, fingerprint)


def should_guard(project_path: str) -> bool:
    """当前是否应跑一次变更守卫。

    - git 指纹带 error → False（没有 git 就无法判断变化）；
    - 与上次守卫指纹一致 → False（没变化不重复跑）；
    - 其余 → True。
    """
    return _needs_guard(git_fingerprint(project_path), load_state(project_path))


# ═══════════════════════════════════════════════════════════════════════
# 守卫结果记录
# ═══════════════════════════════════════════════════════════════════════

def _now_iso() -> str:
    """当前时间的 ISO 字符串。"""
    return datetime.now().isoformat(timespec="seconds")


def _summarize_findings(report: dict) -> Tuple[int, int]:
    """从守卫报告提取 (findings 数量, high 高危数量)。"""
    findings = report.get("findings") if isinstance(report, dict) else None
    if not isinstance(findings, list):
        return 0, 0
    high = sum(1 for f in findings
               if isinstance(f, dict) and str(f.get("tier", "")).lower() == "high")
    return len(findings), high


def record_guard(project_path: str, fingerprint: dict, report: dict) -> None:
    """把一次守卫结果写入会话状态（保留其他 state 键）。"""
    findings, high = _summarize_findings(report)
    state = load_state(project_path)
    state["last_guard"] = {
        "at": _now_iso(),
        "fingerprint": fingerprint,
        "findings": findings,
        "high": high,
        "summary": str(report.get("summary") or "") if isinstance(report, dict) else "",
    }
    state["last_guard_fingerprint"] = fingerprint
    save_state(project_path, state)


def _record_failure(project_path: str, fingerprint: dict, at: str, err: str) -> None:
    """守卫执行失败也落盘指纹，避免每次重工具调用都重复失败。"""
    state = load_state(project_path)
    state["last_guard"] = {
        "at": at,
        "fingerprint": fingerprint,
        "findings": 0,
        "high": 0,
        "summary": f"变更守卫执行失败: {err}",
        "error": err,
    }
    state["last_guard_fingerprint"] = fingerprint
    save_state(project_path, state)


# ═══════════════════════════════════════════════════════════════════════
# 自动守卫入口
# ═══════════════════════════════════════════════════════════════════════

def guard_if_needed(project_path: str) -> Optional[Dict[str, Any]]:
    """工作区相对上次守卫有变化时自动跑一次 ChangeGuard。

    不需要 → None；否则返回统一结构（含 findings/high/summary/at）。
    ChangeGuard 的任何异常都被吞掉并返回 status="error"，绝不让审计调用失败。
    """
    fingerprint = git_fingerprint(project_path)
    if not _needs_guard(fingerprint, load_state(project_path)):
        return None

    try:
        from core.change_guard import ChangeGuard  # 延迟导入，避免循环依赖

        report = ChangeGuard().guard(project_path) or {}
        findings, high = _summarize_findings(report)
        record_guard(project_path, fingerprint, report)
        return {
            "status": "ok",
            "degraded": bool(report.get("degraded")),
            "source": str(report.get("source") or ""),
            "findings": findings,
            "high": high,
            "summary": str(report.get("summary") or ""),
            "at": _now_iso(),
        }
    except Exception as e:  # noqa: BLE001 —— 守卫失败不能拖垮审计主流程
        logger.warning(f"[session_guard] 变更守卫执行异常（已降级）: {e}")
        at = _now_iso()
        _record_failure(project_path, fingerprint, at, str(e))
        return {
            "status": "error",
            "error": str(e),
            "degraded": False,
            "source": "",
            "findings": 0,
            "high": 0,
            "summary": "",
            "at": at,
        }


# ═══════════════════════════════════════════════════════════════════════
# 会话事实（precondition 闸门的判定依据，5.19.2）
# ═══════════════════════════════════════════════════════════════════════

def mark_fact(project_path: str, key: str, value: Any = None) -> None:
    """记录一条会话事实（如 memory_recovered_at / kg_synced_at）。

    写入 state["facts"][key] = {"at": ISO 时间, "value": value}；
    任何失败静默，绝不影响主流程。
    """
    if not key:
        return
    try:
        state = load_state(project_path)
        facts = state.get("facts")
        if not isinstance(facts, dict):
            facts = {}
        facts[key] = {"at": _now_iso(), "value": value}
        state["facts"] = facts
        save_state(project_path, state)
    except Exception as e:  # noqa: BLE001
        logger.debug(f"[session_guard] 会话事实写入失败: {e}")


def get_fact(project_path: str, key: str,
             max_age_hours: Optional[float] = None) -> Optional[dict]:
    """读取会话事实；不存在/已超龄/损坏时返回 None。"""
    try:
        facts = load_state(project_path).get("facts") or {}
        item = facts.get(key)
        if not isinstance(item, dict):
            return None
        if max_age_hours is not None:
            at = item.get("at") or ""
            try:
                age = (datetime.now() - datetime.fromisoformat(at)).total_seconds()
            except (TypeError, ValueError):
                return None
            if age > float(max_age_hours) * 3600:
                return None
        return item
    except Exception as e:  # noqa: BLE001
        logger.debug(f"[session_guard] 会话事实读取失败: {e}")
        return None
