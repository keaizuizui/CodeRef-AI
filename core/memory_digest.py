# -*- coding: utf-8 -*-
"""
MemoryDigest v1.0 —— 操作记忆摘要（供服务端首次重工具调用时注入）

背景：调用方 AI 常在上下文丢失后忘记先调用 operation_memory.recover，
导致重复满 PATH 找工具、无视既有约定。本模块把 recover 的结果压缩成
一段可注入的摘要，供上层在首次调用重工具时附加到返回体。

职责边界：
- 只读 core.operation_memory.operation_memory.recover 的返回值，不写任何文件。
- 只生成摘要，不做注入；注入时机由调用方经 should_inject / inject_once 控制。
- 进程内每项目只注入一次（模块级 set + 线程锁），不落盘、不跨进程共享。

公开 API：
- build_digest(project_path, max_tokens=800) -> dict
- should_inject(project_path) -> bool
- mark_injected(project_path) -> None
- inject_once(project_path, max_tokens=800) -> dict | None

摘要结构：
{"available": bool, "recovered_from": str, "updated_at": str,
 "items": [{"kind": str, "text": str}], "truncated": bool, "error": str}

作者：CodeRef-AI Team
"""

import os
import threading
from typing import List, Set, Tuple

# ─── 日志（复用 loguru，若缺失则回退到内置 logging） ───
try:
    from loguru import logger as _logger
except Exception:  # pragma: no cover
    import logging
    _logger = logging.getLogger("memory_digest")
    if not _logger.handlers:
        _logger.addHandler(logging.StreamHandler())
        _logger.setLevel(logging.INFO)
logger = _logger

from core.operation_memory import operation_memory

# recover 时每次取回的条数（与记忆层默认值保持一致）
RECOVER_LIMIT = 8
# 摘要条目上限
MAX_ITEMS = 12
# 单条摘要文本上限（字符）
ITEM_MAX_CHARS = 200
# 预算下限（字符），避免 max_tokens 过小导致摘要完全不可用
MIN_BUDGET_CHARS = 200
# 1 token 粗略折算的中文字符数（仅作预算，不追求精确分词）
CHARS_PER_TOKEN = 3

# 隐性知识类别 → 中文标签
_KIND_LABELS = {"decision": "决策", "convention": "约定", "pitfall": "踩坑"}

# 内容来源优先级：活跃任务 → 关键结论 → 未决事项，其次工具定位；
# 键名以 operation_memory.recover 的实际返回为准，同时兼容常见的任务/结论别名。
_PRIMARY_SOURCES = (
    ("active_tasks", "活跃任务"),
    ("tasks", "活跃任务"),
    ("decisions", "决策"),
    ("conclusions", "关键结论"),
    ("conventions", "约定"),
    ("pitfalls", "踩坑"),
    ("pending_items", "未决"),
    ("env_tools", "工具"),
)

# recover 返回体的元信息键，兜底提取时跳过
_META_KEYS = {
    "status", "tool", "project_path", "last_sync", "llm_available",
    "pending_counts", "hint",
}

# 已注入过的项目（归一化路径），仅进程内有效
_injected: Set[str] = set()
_injected_lock = threading.Lock()


def _empty_digest() -> dict:
    """构造空摘要（不可用状态）"""
    return {
        "available": False,
        "recovered_from": "",
        "updated_at": "",
        "items": [],
        "truncated": False,
        "error": "",
    }


def _project_key(project_path: str) -> str:
    """项目路径 → 进程内归一化键（同一项目的不同写法视为同一个）"""
    try:
        return os.path.normcase(os.path.abspath(str(project_path)))
    except Exception:
        return str(project_path)


def _as_list(value) -> List:
    """把任意值统一成列表，便于逐条遍历"""
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return list(value)
    return [value]


def _entry_text(entry) -> str:
    """从 recover 的任意条目中提取一行候选文本"""
    if isinstance(entry, str):
        return entry
    if isinstance(entry, dict):
        for key in ("summary", "text", "title", "message", "name", "detail"):
            val = entry.get(key)
            if isinstance(val, str) and val.strip():
                if key == "name" and entry.get("path"):
                    return f"{val}：{entry['path']}"
                return val
        return ""
    if isinstance(entry, (list, tuple)):
        parts = [p for p in (_entry_text(x) for x in entry) if p]
        return "；".join(parts)
    return str(entry)


def _candidates(recover_data: dict) -> List[Tuple[str, str]]:
    """按优先级把 recover 返回值摊平成 (kind, text) 候选序列"""
    out: List[Tuple[str, str]] = []
    consumed: Set[str] = set()

    for key, label in _PRIMARY_SOURCES:
        if key not in recover_data:
            continue
        consumed.add(key)
        for entry in _as_list(recover_data.get(key)):
            text = _entry_text(entry)
            if not text:
                continue
            kind = label
            if key == "pending_items" and isinstance(entry, dict):
                kind = _KIND_LABELS.get(str(entry.get("category", "")), label)
            out.append((kind, text))

    # 兜底：若记忆层返回了未列出的任务/结论等结构化键，按返回顺序提取
    for key, value in recover_data.items():
        if key in consumed or key in _META_KEYS:
            continue
        for entry in _as_list(value):
            text = _entry_text(entry)
            if text:
                out.append((str(key), text))
    return out


def _clean_text(value) -> str:
    """去换行、压缩连续空白（保留单空格分隔）"""
    if value is None:
        return ""
    try:
        return " ".join(str(value).split())
    except Exception:
        return ""


def build_digest(project_path: str, max_tokens: int = 800) -> dict:
    """把 operation_memory.recover 的结果压成可注入摘要。

    任何异常都收敛为 available=False 的摘要，绝不向调用方抛出。
    truncated=True 表示因条数上限或字符预算丢弃 / 截短了内容。
    """
    digest = _empty_digest()
    try:
        data = operation_memory.recover(project_path, limit=RECOVER_LIMIT)
    except Exception as exc:
        logger.warning(f"[MemoryDigest] 读取操作记忆失败: {exc}")
        digest["error"] = f"读取操作记忆失败: {exc}"
        return digest

    if not isinstance(data, dict):
        logger.warning("[MemoryDigest] 操作记忆返回格式异常")
        digest["error"] = "操作记忆返回格式异常"
        return digest

    if data.get("status") != "ok":
        message = data.get("message") or "操作记忆未建立"
        logger.debug(f"[MemoryDigest] 操作记忆不可用: {message}")
        digest["error"] = str(message)
        return digest

    digest["recovered_from"] = "operation_memory"
    digest["updated_at"] = str(data.get("last_sync") or "")

    try:
        budget_tokens = int(max_tokens)
    except (TypeError, ValueError):
        budget_tokens = 800
    max_chars = max(MIN_BUDGET_CHARS, budget_tokens * CHARS_PER_TOKEN)

    used = 0
    truncated = False
    for kind, raw in _candidates(data):
        if len(digest["items"]) >= MAX_ITEMS:
            truncated = True
            break
        text = _clean_text(raw)
        if not text:
            continue
        clipped = False
        if len(text) > ITEM_MAX_CHARS:
            text = text[:ITEM_MAX_CHARS]
            clipped = True
        remaining = max_chars - used
        if remaining <= 0:
            truncated = True
            break
        if len(text) > remaining:
            text = text[:remaining]
            clipped = True
        if clipped:
            truncated = True
        digest["items"].append({"kind": kind, "text": text})
        used += len(text)

    digest["truncated"] = truncated
    if not digest["items"]:
        digest["error"] = "操作记忆为空"
        return digest
    digest["available"] = True
    return digest


def should_inject(project_path: str) -> bool:
    """该项目在本次进程内是否尚未注入过记忆摘要"""
    return _project_key(project_path) not in _injected


def mark_injected(project_path: str) -> None:
    """标记该项目已注入（进程内，不落盘）"""
    with _injected_lock:
        _injected.add(_project_key(project_path))


def inject_once(project_path: str, max_tokens: int = 800):
    """首次调用返回摘要，之后返回 None。

    即使记忆为空也返回 available=False 的摘要（诚实状态），不返回 None
    冒充"没有这项"；只有"已注入过"才返回 None。
    """
    key = _project_key(project_path)
    with _injected_lock:
        if key in _injected:
            return None
        _injected.add(key)
    try:
        return build_digest(project_path, max_tokens=max_tokens)
    except Exception as exc:  # build_digest 已兜底，这里再防一手
        logger.warning(f"[MemoryDigest] 生成摘要失败: {exc}")
        digest = _empty_digest()
        digest["error"] = f"生成摘要失败: {exc}"
        return digest
