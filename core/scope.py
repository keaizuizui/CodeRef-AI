# -*- coding: utf-8 -*-
"""统一范围层（5.19.2）—— .coderef/scope.json 作为项目边界的单一事实源。

背景：范围控制此前散在 project_scope（目录判定）、pr_whitelist（白名单条目）、
shared_filter（共享过滤）三处，用户「忽略这个第三方库」的意图没有持久化载体，
靠 AI 每次调用记得传参，于是反复出现「说忽略又不忽略」。

本模块只做四件事：
  1. 读写 <project>/.coderef/scope.json（原子写，可提交进版本库共享给团队）；
  2. apply_scope(files)：扫描型工具的统一过滤入口（include_override 优先）；
  3. is_excluded()：供 CodeAnalyzer / 图谱等扫描器按相对路径判定；
  4. verify()：add 规则后的生效回执（排除数、按工具计数、可疑误伤）。

边界（写进文档与 skill）：scope.json 只作用于扫描与检索，不作用于
change_guard 的回归防护范围——忽略的是「审计噪音」，不是「安全责任」。

作者: CodeRef-AI Team
版本: v1.0
"""

import fnmatch
import json
import os
import threading
from datetime import datetime
from typing import Dict, List

from loguru import logger


SCOPE_REL = os.path.join(".coderef", "scope.json")
SCOPE_VERSION = 1
KINDS = ("exclude_glob", "include_override", "ignore_lib")

_cache: Dict[str, tuple] = {}
_cache_lock = threading.Lock()


def scope_path(project_path: str) -> str:
    return os.path.join(os.path.abspath(project_path), SCOPE_REL)


def _empty() -> dict:
    return {"version": SCOPE_VERSION, "rules": []}


def load_scope(project_path: str) -> dict:
    """读取 scope.json（带 mtime 缓存；不存在/损坏时返回空规则，绝不抛异常）。"""
    path = scope_path(project_path)
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        return _empty()
    key = os.path.normcase(os.path.abspath(path))
    with _cache_lock:
        hit = _cache.get(key)
        if hit and hit[0] == mtime:
            return hit[1]
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:  # noqa: BLE001
        logger.debug(f"[scope] 读取失败，按空规则处理: {e}")
        data = _empty()
    if not isinstance(data, dict):
        data = _empty()
    rules = data.get("rules")
    if not isinstance(rules, list):
        rules = []
    data = {"version": data.get("version", SCOPE_VERSION), "rules": rules}
    with _cache_lock:
        _cache[key] = (mtime, data)
    return data


def save_scope(project_path: str, data: dict) -> None:
    """原子写入 scope.json（tmp + os.replace）。"""
    path = scope_path(project_path)
    tmp = path + ".tmp"
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)
    with _cache_lock:
        _cache.pop(os.path.normcase(path), None)


def _norm_rel(project_path: str, path: str) -> str:
    p = str(path or "")
    if os.path.isabs(p):
        try:
            p = os.path.relpath(p, os.path.abspath(project_path))
        except Exception:  # noqa: BLE001
            pass
    p = p.replace("\\", "/").strip("/")
    while p.startswith("./"):
        p = p[2:]
    return p


def add_rule(project_path: str, kind: str, pattern: str,
             reason: str = "", added_by: str = "user") -> dict:
    """新增一条范围规则；重复（同 kind+pattern）不重复写入。"""
    kind = str(kind or "").strip()
    pattern = str(pattern or "").replace("\\", "/").strip().strip("/")
    if kind not in KINDS:
        raise ValueError(f"未知规则类型 {kind!r}，支持: {'/'.join(KINDS)}")
    if not pattern:
        raise ValueError("pattern 不能为空")
    data = load_scope(project_path)
    rules = list(data.get("rules") or [])
    for r in rules:
        if (r.get("kind") == kind and r.get("pattern") == pattern):
            return {"added": 0, "duplicate": True, "rule": r,
                    "total": len(rules)}
    rule = {"kind": kind, "pattern": pattern,
            "reason": str(reason or ""),
            "added_by": str(added_by or "user"),
            "added_at": datetime.now().isoformat(timespec="seconds")}
    rules.append(rule)
    save_scope(project_path, {"version": SCOPE_VERSION, "rules": rules})
    return {"added": 1, "duplicate": False, "rule": rule, "total": len(rules)}


def add_rules(project_path: str, rules: List[dict],
              added_by: str = "user") -> dict:
    """批量新增规则；返回逐条结果与最终总数。"""
    results = []
    for item in rules or []:
        if not isinstance(item, dict):
            continue
        try:
            results.append(add_rule(
                project_path, item.get("kind", "exclude_glob"),
                item.get("pattern", ""), item.get("reason", ""),
                item.get("added_by") or added_by))
        except ValueError as e:
            results.append({"added": 0, "error": str(e)})
    total = len(load_scope(project_path).get("rules") or [])
    return {"results": results,
            "added": sum(1 for r in results if r.get("added")),
            "total": total}


def remove_rule(project_path: str, kind: str = "", pattern: str = "") -> dict:
    """删除匹配 kind+pattern 的规则；kind 为空时按 pattern 删除全部匹配。"""
    pattern = str(pattern or "").replace("\\", "/").strip().strip("/")
    data = load_scope(project_path)
    rules = list(data.get("rules") or [])
    kept = [r for r in rules
            if not ((not kind or r.get("kind") == kind)
                    and (not pattern or r.get("pattern") == pattern))]
    removed = len(rules) - len(kept)
    if removed:
        save_scope(project_path, {"version": SCOPE_VERSION, "rules": kept})
    return {"removed": removed, "total": len(kept)}


def list_scope(project_path: str) -> dict:
    data = load_scope(project_path)
    rules = data.get("rules") or []
    counts = {k: sum(1 for r in rules if r.get("kind") == k) for k in KINDS}
    return {"scope_path": scope_path(project_path), "version": data.get("version"),
            "rules": rules, "counts": counts,
            "note": "exclude_glob/ignore_lib 只作用于扫描与检索；"
                    "change_guard 不受 scope 影响"}


def _matches(rel: str, pattern: str) -> bool:
    """路径模式匹配：支持 glob、目录前缀、裸目录名任意层级。"""
    if not pattern:
        return False
    if fnmatch.fnmatch(rel, pattern):
        return True
    if fnmatch.fnmatch(os.path.basename(rel), pattern):
        return True
    p = pattern.strip("/")
    if "/" not in p:
        return p in rel.split("/")
    return rel == p or rel.startswith(p + "/")


def is_excluded(project_path: str, path: str) -> bool:
    """路径是否被 scope 排除（include_override 优先找回）。"""
    rel = _norm_rel(project_path, path)
    if not rel:
        return False
    rules = load_scope(project_path).get("rules") or []
    for r in rules:
        if r.get("kind") == "include_override" and _matches(rel, r.get("pattern", "")):
            return False
    for r in rules:
        if r.get("kind") in ("exclude_glob", "ignore_lib") \
                and _matches(rel, r.get("pattern", "")):
            return True
    return False


def apply_scope(project_path: str, files: List[str]) -> dict:
    """扫描型工具的统一过滤入口：返回 kept / excluded 两组文件。"""
    kept, excluded = [], []
    for fp in files or []:
        (excluded if is_excluded(project_path, fp) else kept).append(fp)
    return {"kept": kept, "excluded": excluded,
            "excluded_count": len(excluded), "total": len(files or [])}


def verify(project_path: str) -> dict:
    """生效验证回执：add 规则后立即返回，杜绝「以为忽略了其实没生效」。"""
    from core.code_analyzer import CodeAnalyzer
    analyzer = CodeAnalyzer()
    # 临时关闭 scope 过滤，拿到"若不过滤会扫到哪些文件"，再统计会被排除多少
    analyzer._disable_scope = True
    try:
        files = analyzer.scan_directory(project_path)
    except Exception as e:  # noqa: BLE001
        return {"error": f"范围验证失败: {e}"}
    finally:
        analyzer._disable_scope = False
    applied = apply_scope(project_path, files)
    kept_names = {os.path.basename(f) for f in applied["kept"]}
    suspicious = [os.path.relpath(f, project_path).replace("\\", "/")
                  for f in applied["excluded"]
                  if os.path.basename(f) in kept_names]
    totals = len(files)
    return {
        "scope_path": scope_path(project_path),
        "total_files": totals,
        "excluded_files": applied["excluded_count"],
        "kept_files": len(applied["kept"]),
        "by_tool": {
            "audit": applied["excluded_count"],
            "graph": applied["excluded_count"],
            "sca": applied["excluded_count"],
        },
        "suspicious": suspicious[:20],
        "suspicious_count": len(suspicious),
        "sample_excluded": [
            os.path.relpath(f, project_path).replace("\\", "/")
            for f in applied["excluded"][:20]],
        "note": ("by_tool 为同一 scope 作用于各扫描型工具的口径（统一入口）；"
                 "suspicious 为与保留文件同名的被排除项，请确认是否误伤；"
                 "scope 不作用于 change_guard。"),
    }
