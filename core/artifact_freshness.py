# -*- coding: utf-8 -*-
"""派生产物新鲜度快报（coderef_version 的扩展输出）。

背景：外部审计反复出现「把旧报告/旧快照当现状」的陈旧输入问题。本模块只做
只读盘点——列出各派生产物是否存在、生成于何时，并与代码最后修改时间比较，
把「要不要重跑」交给调用方判断。不评分、不改产物、不做内容指纹。

作者: CodeRef-AI Team
版本: v1.0
"""

import glob
import hashlib
import os
import re
from datetime import datetime

from loguru import logger


# (key, 中文名, 候选相对路径（支持 glob）, 建议过期天数, 刷新方式)
_ARTIFACTS = (
    ("target_arch", "目标架构定标", (".coderef/target_arch.json",), 90,
     "coderef_target_arch_set（代码已大幅演化时重新定标）"),
    ("governance_db", "治理库", (".coderef/governance.db", "governance.db"), 30,
     "coderef_gov_report"),
    ("audit_findings", "审计 findings", (".coderef/audit_findings_*.json",), 7,
     "coderef_audit"),
    ("scorecard", "健康记分卡", (".coderef/scorecard.json",), 1,
     "coderef_interpret(action=dashboard)"),
    ("architecture_report", "评估台入口", (".coderef/report/html/index.html",), 7,
     "coderef_report"),
    ("wiki", "Wiki 文档", (".coderef/wiki", "docs/wiki", "coderef-wiki"), 30,
     "coderef_docs"),
    ("product_fit", "产品契合评估", (".coderef/product_fit.json",), 7,
     "coderef_eval（重新跑产品评估）"),
    ("ux_fit", "UX 人格走查", (".coderef/ux_fit.json",), 7,
     "coderef_eval（重新跑 UX 走查）"),
    ("business_semantics", "业务语义标注", (".coderef/business_semantics.json",), 7,
     "coderef_architecture（含业务流程视图）"),
)


def _latest_code_mtime(project_path: str):
    """项目代码最后修改时间（复用 CodeAnalyzer 的统一扫描范围）。"""
    try:
        from core.code_analyzer import CodeAnalyzer
        files = CodeAnalyzer().scan_directory(project_path)
        mtimes = []
        for fp in files:
            try:
                mtimes.append(os.path.getmtime(fp))
            except OSError:
                continue
        return max(mtimes) if mtimes else None
    except Exception as e:  # noqa: BLE001
        logger.debug(f"[freshness] 代码修改时间探测失败: {e}")
        return None


def _mtime_of(path: str):
    """返回 (mtime, 额外计数)；目录取内部 .md 最新修改时间与文档数。"""
    if os.path.isdir(path):
        latest = None
        count = 0
        for root, _, files in os.walk(path):
            for f in files:
                if not f.endswith(".md"):
                    continue
                count += 1
                try:
                    m = os.path.getmtime(os.path.join(root, f))
                    latest = m if latest is None else max(latest, m)
                except OSError:
                    continue
        return latest, count
    try:
        return os.path.getmtime(path), None
    except OSError:
        return None, None


def _iso(mtime) -> str:
    try:
        return datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M:%S")
    except (TypeError, ValueError, OSError):
        return ""


def _find_path(project_path: str, patterns: tuple):
    """按候选模式定位实际存在的产物（glob 命中取多个时，调用方再取最新）。"""
    hits = []
    for pat in patterns:
        if any(ch in pat for ch in "*?["):
            hits.extend(glob.glob(os.path.join(project_path, pat)))
        else:
            fp = os.path.join(project_path, pat)
            if os.path.exists(fp):
                hits.append(fp)
    return hits


def _artifact_item(key: str, name: str, patterns: tuple, stale_days: int,
                   refresh: str, project_path: str, code_mtime,
                   strict_hash: bool = False) -> dict:
    """单个产物的新鲜度条目（不存在时也返回条目，便于调用方看到应产出什么）。

    strict_hash=True 时，audit_findings 只认本项目哈希命名的产物，避免把其他
    项目残留的 audit_findings_<旧哈希>.json 当成本项目产物（interpret 侧是精确
    哈希归属，新鲜度侧必须同口径）。
    """
    candidates = _find_path(project_path, patterns)
    if strict_hash and key == "audit_findings" and candidates:
        phash = hashlib.md5(project_path.encode()).hexdigest()[:12]
        kept = []
        for c in candidates:
            m = re.match(r"^audit_findings_([0-9a-f]{12})\.json$",
                         os.path.basename(c))
            # 只排除「其他项目哈希」的产物；audit_findings.json 与手工命名的
            # audit_findings_x.json 保持原有兼容语义。
            if m and m.group(1) != phash:
                continue
            kept.append(c)
        candidates = kept
    if not candidates:
        return {"key": key, "name": name, "exists": False,
                "path": patterns[0], "refresh_hint": refresh}
    # glob 命中多个：取修改时间最新的那个
    best = None
    for cand in candidates:
        m, extra = _mtime_of(cand)
        if m is None:
            continue
        if best is None or m > best[0]:
            best = (m, cand, extra)
    if best is None:
        return {"key": key, "name": name, "exists": False,
                "path": patterns[0], "refresh_hint": refresh}

    mtime, path, extra = best
    age_days = round((datetime.now().timestamp() - mtime) / 86400, 1)
    item = {
        "key": key,
        "name": name,
        "exists": True,
        "path": os.path.relpath(path, project_path).replace(os.sep, "/"),
        "modified_at": _iso(mtime),
        "age_days": age_days,
        "suggested_max_age_days": stale_days,
        "stale_by_age": age_days > stale_days,
        # 代码比产物更新 = 产物可能没覆盖最新代码（启发式，供调用方判断）
        "code_newer": bool(code_mtime and mtime < code_mtime),
        "refresh_hint": refresh,
    }
    if extra is not None:
        item["md_count"] = extra
    return item


def _kg_item(project_path: str, code_mtime) -> dict:
    """知识图谱条目（db 路径由 arch_audit 统一解析，缺失时给出刷新方式）。"""
    try:
        from core.arch_audit import locate_kg_db
        kg_db = locate_kg_db(project_path)
    except Exception as e:  # noqa: BLE001
        logger.debug(f"[freshness] 图谱定位失败: {e}")
        kg_db = None
    if not kg_db:
        return {"key": "kg_db", "name": "知识图谱", "exists": False,
                "path": "", "refresh_hint": "coderef_memory(action=sync)"}
    mtime, _ = _mtime_of(kg_db)
    if mtime is None:
        return {"key": "kg_db", "name": "知识图谱", "exists": False,
                "path": "", "refresh_hint": "coderef_memory(action=sync)"}
    age_days = round((datetime.now().timestamp() - mtime) / 86400, 1)
    return {
        "key": "kg_db", "name": "知识图谱", "exists": True,
        "path": os.path.relpath(kg_db, project_path).replace(os.sep, "/"),
        "modified_at": _iso(mtime), "age_days": age_days,
        "suggested_max_age_days": 7,
        "stale_by_age": age_days > 7,
        "code_newer": bool(code_mtime and mtime < code_mtime),
        "refresh_hint": "coderef_memory(action=sync) 或重跑 coderef_audit",
    }


def report(project_path: str) -> dict:
    """盘点派生产物新鲜度（只读、秒级）。"""
    project_path = os.path.abspath(project_path)
    if not os.path.isdir(project_path):
        return {"project_path": project_path, "latest_code_mtime": "",
                "artifacts": [],
                "note": "项目路径不存在或不可读，未盘点任何产物"}
    code_mtime = _latest_code_mtime(project_path)
    items = [_artifact_item(key, name, patterns, stale_days, refresh,
                            project_path, code_mtime,
                            strict_hash=(key == "audit_findings"))
             for key, name, patterns, stale_days, refresh in _ARTIFACTS]
    items.append(_kg_item(project_path, code_mtime))

    return {
        "project_path": project_path,
        "latest_code_mtime": _iso(code_mtime) if code_mtime else "",
        "artifacts": items,
        "note": ("只读盘点，不评分：stale_by_age=超过建议时效；code_newer=代码最后修改"
                 "晚于产物（启发式，代码被 touch/切分支也会命中）。引用任何历史结论前"
                 "请先看本清单，必要时重跑 refresh_hint 中的工具。"),
    }
