# -*- coding: utf-8 -*-
"""结果级新鲜度硬戳 + 图谱 stale 感知（5.19.1）。

痛点：新鲜度快报只挂在 coderef_version 上，是「AI 应该主动查」的可选动作。
本模块把判定下沉到依赖派生产物的工具返回里，并给 coderef_query 加 stale
感知：代码比图谱新时，结果自带 stale=true 与后台增量 sync 的 task_id。

职责边界：
  - 只读盘点（复用 core.artifact_freshness 的统一扫描口径），不重算结论；
  - 只读写 <project>/.coderef/guard_config.json（可选）与进程内缓存；
  - 阈值/行为支持 warn（警告不阻塞）与 block（阻塞等待刷新）两档。

作者: CodeRef-AI Team
版本: v1.0
"""

import json
import os
import threading
import time
from datetime import datetime
from typing import Dict, List, Optional

from loguru import logger


# 产物清单（key 与 core.artifact_freshness._ARTIFACTS 同源）
DEFAULT_QUERY_CONFIG = {
    "stale_minutes": 30,       # 代码最后修改晚于图谱构建超过该分钟数即视为陈旧
    "stale_file_threshold": 3,  # 或代码变更文件数达到该阈值
    "mode": "warn",            # warn=返回 stale 并后台刷新；block=同步刷新后重查
}

# 盘点结果进程内缓存（秒）：高频工具每次调用都全量 walk 会拖慢查询，
# 60 秒内的重复盘点复用同一份结果（文件变化最多延迟 60 秒被发现）。
_REPORT_TTL = 60

_report_cache: Dict[str, tuple] = {}
_cache_lock = threading.Lock()

# 正在后台增量同步的项目（防止 stale 查询并发触发多次 sync）
_syncing = set()

CONFIG_FILE = os.path.join(".coderef", "guard_config.json")


def _norm(project_path: str) -> str:
    try:
        return os.path.normcase(os.path.abspath(str(project_path)))
    except Exception:  # noqa: BLE001
        return str(project_path)


def _iso(ts: Optional[float]) -> str:
    try:
        return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")
    except (TypeError, ValueError, OSError):
        return ""


def _iso_ts(value: str) -> Optional[float]:
    """把盘点报告里的 ISO 时间还原成时间戳（解析失败返回 None）。"""
    try:
        return datetime.strptime(value, "%Y-%m-%d %H:%M:%S").timestamp()
    except (TypeError, ValueError):
        return None


def guard_config(project_path: str) -> dict:
    """读取项目级守护配置（缺省/损坏时回退默认值，绝不抛异常）。"""
    cfg = {"query": dict(DEFAULT_QUERY_CONFIG), "preconditions": {"mode": "warn"}}
    path = os.path.join(os.path.abspath(project_path), CONFIG_FILE)
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        return cfg
    except Exception as e:  # noqa: BLE001 —— 配置损坏按默认值处理
        logger.debug(f"[freshness] 守护配置读取失败，按默认值: {e}")
        return cfg
    if not isinstance(data, dict):
        return cfg
    for section in ("query", "preconditions"):
        value = data.get(section)
        if isinstance(value, dict):
            cfg[section].update({k: v for k, v in value.items() if v is not None})
    # 阈值做类型收敛，避免字符串/负数导致比较异常
    try:
        cfg["query"]["stale_minutes"] = max(1, int(cfg["query"]["stale_minutes"]))
    except (TypeError, ValueError):
        cfg["query"]["stale_minutes"] = DEFAULT_QUERY_CONFIG["stale_minutes"]
    try:
        cfg["query"]["stale_file_threshold"] = max(1, int(cfg["query"]["stale_file_threshold"]))
    except (TypeError, ValueError):
        cfg["query"]["stale_file_threshold"] = DEFAULT_QUERY_CONFIG["stale_file_threshold"]
    if cfg["query"].get("mode") not in ("warn", "block"):
        cfg["query"]["mode"] = "warn"
    if cfg["preconditions"].get("mode") not in ("warn", "block"):
        cfg["preconditions"]["mode"] = "warn"
    return cfg


def artifact_report(project_path: str, ttl: int = _REPORT_TTL) -> dict:
    """带进程内短缓存的产物盘点（避免高频工具重复全量 walk）。"""
    key = _norm(project_path)
    now = time.time()
    with _cache_lock:
        hit = _report_cache.get(key)
        if hit and now - hit[0] <= ttl:
            return hit[1]
    from core.artifact_freshness import report
    try:
        rep = report(project_path)
    except Exception as e:  # noqa: BLE001 —— 盘点失败降级为空报告
        logger.debug(f"[freshness] 产物盘点失败: {e}")
        rep = {"project_path": project_path, "latest_code_mtime": "",
               "artifacts": [], "note": f"盘点失败: {e}"}
    with _cache_lock:
        _report_cache[key] = (now, rep)
    return rep


def freshness_block(project_path: str, artifacts: List[str]) -> dict:
    """构造工具信封里的 freshness 块。

    artifacts 为本轮结论实际引用的产物 key（见 artifact_freshness._ARTIFACTS）：
    target_arch / governance_db / audit_findings / scorecard / architecture_report /
    wiki / product_fit / ux_fit / business_semantics / kg_db。
    """
    rep = artifact_report(project_path)
    by_key = {i.get("key"): i for i in rep.get("artifacts", [])}
    used = []
    missing, newer, aged = [], [], []
    refresh_action = ""
    for key in artifacts:
        item = by_key.get(key)
        # 图谱条目始终取实时磁盘值：盘点缓存可能落后于另一个进程的建图，
        # 导致「raw 查询有图谱、freshness 说缺少产物」的同响应矛盾。
        if key == "kg_db":
            item = _live_kg_item(project_path, _iso_ts(rep.get("latest_code_mtime", "")))
        if not item:
            continue
        used.append({
            "key": key,
            "name": item.get("name", ""),
            "exists": bool(item.get("exists")),
            "path": item.get("path", ""),
            "modified_at": item.get("modified_at", ""),
            "age_days": item.get("age_days"),
            "stale_by_age": bool(item.get("stale_by_age")),
            "code_newer": bool(item.get("code_newer")),
            "refresh_hint": item.get("refresh_hint", ""),
        })
        if not item.get("exists"):
            missing.append(item.get("name") or key)
        else:
            if item.get("code_newer"):
                newer.append(item.get("name") or key)
            if item.get("stale_by_age"):
                aged.append(item.get("name") or key)
        if (not refresh_action and item.get("refresh_hint")
                and (not item.get("exists") or item.get("code_newer")
                     or item.get("stale_by_age"))):
            refresh_action = item.get("refresh_hint", "")

    reasons = []
    if missing:
        reasons.append("缺少产物: " + "、".join(missing))
    if newer:
        reasons.append("代码比产物新: " + "、".join(newer))
    if aged:
        reasons.append("产物超过建议时效: " + "、".join(aged))
    return {
        "artifacts_used": used,
        "code_newer": bool(newer),
        "stale_reason": "；".join(reasons),
        "refresh_action": refresh_action,
        "latest_code_mtime": rep.get("latest_code_mtime", ""),
    }


def attach_freshness(envelope: dict, project_path: str,
                     artifacts: List[str]) -> dict:
    """把 freshness 块挂到已构造好的信封上（非 dict 原样返回）。"""
    if not isinstance(envelope, dict) or not project_path:
        return envelope
    try:
        envelope["freshness"] = freshness_block(project_path, artifacts)
    except Exception as e:  # noqa: BLE001 —— 新鲜度是增强项，失败不影响主结论
        envelope["freshness"] = {"error": str(e)}
    return envelope


# ── 图谱 stale 感知 ─────────────────────────────────────────────

def _kg_built_ts(kg_db: str) -> Optional[float]:
    """从图谱 meta 读取构建时间戳（秒）。"""
    if not kg_db:
        return None
    try:
        import sqlite3
        conn = sqlite3.connect(kg_db)
        try:
            row = conn.execute(
                "SELECT value FROM meta WHERE key='built_at'").fetchone()
            return float(row[0]) if row and row[0] else None
        finally:
            conn.close()
    except Exception as e:  # noqa: BLE001
        logger.debug(f"[freshness] 读取图谱 built_at 失败: {e}")
        return None


def _code_mtimes(project_path: str) -> List[float]:
    """项目代码文件 mtime 列表（复用 CodeAnalyzer 的统一扫描范围，30s 缓存）。"""
    key = _norm(project_path)
    now = time.time()
    with _cache_lock:
        hit = _mtimes_cache.get(key)
        if hit and now - hit[0] <= _STALE_TTL:
            return hit[1]
    try:
        from core.code_analyzer import CodeAnalyzer
        files = CodeAnalyzer().scan_directory(project_path)
    except Exception as e:  # noqa: BLE001
        logger.debug(f"[freshness] 代码 mtime 探测失败: {e}")
        return []
    out = []
    for fp in files:
        try:
            out.append(os.path.getmtime(fp))
        except OSError:
            continue
    with _cache_lock:
        _mtimes_cache[key] = (now, out)
    return out


def invalidate(project_path: str) -> None:
    """清空某项目的盘点/陈旧度缓存（同步完成后调用，让下一次判定看到新图）。"""
    key = _norm(project_path)
    with _cache_lock:
        _report_cache.pop(key, None)
        _stale_cache.pop(key, None)
        _mtimes_cache.pop(key, None)


_mtimes_cache: Dict[str, tuple] = {}


def query_staleness(project_path: str) -> dict:
    """判断知识图谱是否落后于代码（查询类工具执行前调用）。

    结果带 30 秒进程内缓存：query 本身是轻量工具，每次调用都全量 walk 会把
    它变成重工具；缓存在同一轮「判定 + 标注」两次调用间复用。
    """
    project_path = os.path.abspath(project_path)
    key = _norm(project_path)
    now = time.time()
    with _cache_lock:
        hit = _stale_cache.get(key)
        if hit and now - hit[0] <= _STALE_TTL:
            return dict(hit[1])
    info = _compute_staleness(project_path)
    with _cache_lock:
        _stale_cache[key] = (now, dict(info))
    return info


# 图谱 stale 判定缓存（秒）
_STALE_TTL = 30
_stale_cache: Dict[str, tuple] = {}


def _live_kg_item(project_path: str, code_mtime: Optional[float] = None) -> dict:
    """直接取实时的 kg_db 条目（绕过 artifact_report 的 60s 盘点缓存）。"""
    try:
        from core.artifact_freshness import live_kg_item
        return live_kg_item(project_path, code_mtime)
    except Exception as e:  # noqa: BLE001
        logger.debug(f"[freshness] 图谱实时条目读取失败: {e}")
        return {"key": "kg_db", "name": "知识图谱", "exists": False,
                "path": "", "refresh_hint": "coderef_memory(action=sync)"}


def _compute_staleness(project_path: str) -> dict:
    cfg = guard_config(project_path)["query"]
    mtimes = _code_mtimes(project_path)
    latest = max(mtimes) if mtimes else None
    # 图谱是否存在以「实时磁盘」为准：同一响应里的 freshness 块也走实时条目，
    # 避免 60s 盘点缓存与 raw 查询结果自相矛盾（测试方 D-1 复发）。
    item = _live_kg_item(project_path, latest)
    kg_db = ""
    if item.get("exists"):
        rel = str(item.get("path") or "")
        if rel:
            kg_db = rel if os.path.isabs(rel) else os.path.join(project_path, rel)
            if not os.path.isfile(kg_db):
                kg_db = ""
    built_ts = _kg_built_ts(kg_db)
    changed = len([m for m in mtimes if built_ts is not None and m > built_ts])

    info = {
        "stale": False,
        "stale_reason": "",
        "kg_built_at": _iso(built_ts) if built_ts else "",
        "latest_code_mtime": _iso(latest) if latest else "",
        "changed_files_since_kg": changed,
        "stale_minutes": cfg["stale_minutes"],
        "stale_file_threshold": cfg["stale_file_threshold"],
        "mode": cfg["mode"],
    }
    if not kg_db:
        info["stale"] = True
        info["stale_reason"] = ("知识图谱文件不可读" if item.get("exists")
                                else "知识图谱不存在")
        return info
    if built_ts is None:
        info["stale"] = True
        info["stale_reason"] = "知识图谱缺少构建时间标记"
        # 产物存在但 meta 缺 built_at：至少把磁盘时间作为可核验的构建时间。
        info["kg_built_at"] = str(item.get("modified_at") or "")
        return info
    if latest is None:
        info["stale_reason"] = "未扫描到代码文件"
        return info

    elapsed_minutes = round((latest - built_ts) / 60, 1)
    info["elapsed_minutes"] = elapsed_minutes
    if elapsed_minutes > cfg["stale_minutes"]:
        info["stale"] = True
        info["stale_reason"] = (
            f"代码最后修改晚于图谱构建 {elapsed_minutes} 分钟"
            f"（阈值 {cfg['stale_minutes']} 分钟）")
    elif changed >= cfg["stale_file_threshold"]:
        info["stale"] = True
        info["stale_reason"] = (
            f"图谱构建后已有 {changed} 个代码文件变更"
            f"（阈值 {cfg['stale_file_threshold']} 个）")
    return info


def annotate_query_result(raw: str, project_path: str,
                          sync_task_id: str = "", refreshed: bool = False) -> str:
    """给 coderef_query 结果补 stale 标记与 freshness 块（尽力而为）。"""
    try:
        data = json.loads(raw)
    except Exception:  # noqa: BLE001
        return raw
    if not isinstance(data, dict):
        return raw
    try:
        info = query_staleness(project_path)
        fresh = freshness_block(project_path, ["kg_db"])
        fresh_kg = next((i for i in (fresh.get("artifacts_used") or [])
                         if i.get("key") == "kg_db"), None)
        # 两个缓存（stale 30s / artifact 60s）可能短暂分叉；同一响应内以
        # artifact 盘点为准重算一次，避免 freshness.exists=true 却报「不存在」。
        if (fresh_kg and fresh_kg.get("exists")
                and (info.get("stale_reason") == "知识图谱不存在"
                     or not info.get("kg_built_at"))):
            info = _compute_staleness(project_path)
            with _cache_lock:
                _stale_cache[_norm(project_path)] = (time.time(), dict(info))
        data["stale"] = info["stale"] if not refreshed else False
        data["kg_freshness"] = {
            "stale_reason": info["stale_reason"],
            "kg_built_at": info["kg_built_at"],
            "latest_code_mtime": info["latest_code_mtime"],
            "changed_files_since_kg": info["changed_files_since_kg"],
            "mode": info["mode"],
            "refresh_action": "coderef_memory(action=sync, mode=incr)",
        }
        data["freshness"] = fresh
        if refreshed:
            data["refreshed"] = True
        if sync_task_id:
            data["sync_task_id"] = sync_task_id
            data["sync_hint"] = (
                f"已触发后台增量同步；coderef_task_status(task_id='{sync_task_id}') "
                "查询完成后重跑本次查询")
    except Exception as e:  # noqa: BLE001
        logger.debug(f"[freshness] 查询结果标注失败: {e}")
    try:
        return json.dumps(data, ensure_ascii=False)
    except Exception:  # noqa: BLE001
        return raw


def claim_sync(project_path: str) -> bool:
    """认领一次后台同步；已有同步在跑时返回 False（避免并发重复建图）。"""
    key = _norm(project_path)
    with _cache_lock:
        if key in _syncing:
            return False
        _syncing.add(key)
        return True


def release_sync(project_path: str) -> None:
    with _cache_lock:
        _syncing.discard(_norm(project_path))
