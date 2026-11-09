# -*- coding: utf-8 -*-
"""scorecard — 项目健康记分卡（v5.17.0，确定性聚合，无 LLM）。

把分散在 arch_audit / arch_gap / audit_findings / governance.db / Wiki 产物里的
结论聚合成「一句话结论 + 一页分数」，供：
  - 评估台页面「概览」Tab 直接展示；
  - `<project>/.coderef/scorecard.json` 作为机器可读旁路，供 CI 与编程 AI 消费。

诚实边界（与 coderef_eval 的 SKIP 硬阻断风格一致）：
  - 证据不足的维度标 `status="unknown"`、`score=None`，**不写 0、不给满分**；
  - 无代码项目（U-46 口径）整体 `overall=None`、`grade="N/A"`；
  - 权重来自 `core/scorecard_weights.json`，项目级 `.coderef/eval_config.json` 可覆盖；
  - 同输入两次结果一致（`generated_at` 不参与判等）。
"""

import argparse
import json
import math
import os
import sys
import tempfile
from datetime import datetime
from typing import Any, Dict, List, Optional

from loguru import logger

from core.version import __version__

_WEIGHTS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                             "scorecard_weights.json")

# 兜底只保留「无法从别处推导」的最小信息：维度 id/名称与默认权重。
# 口径说明与来源工具以 core/scorecard_weights.json 为唯一真源，避免双份文案漂移。
_FALLBACK_CONFIG: Dict[str, Any] = {
    "version": "5.17.0",
    "dimensions": [
        {"id": "structure", "name": "结构健康", "weight": 0.25},
        {"id": "alignment", "name": "架构对齐", "weight": 0.20},
        {"id": "findings", "name": "审计发现", "weight": 0.25},
        {"id": "governance", "name": "治理闭环", "weight": 0.15},
        {"id": "docs", "name": "文档覆盖", "weight": 0.15},
    ],
    "thresholds": {"min_score": 60, "fail_on_below": False},
    "grades": {"A": 85, "B": 70, "C": 55},
}


# ────────────────────────────────────────────────────────────────
# 配置加载与校验
# ────────────────────────────────────────────────────────────────

def _load_json(path: str) -> Optional[dict]:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _normalize_config(raw: dict) -> dict:
    """校验并归一配置；非法权重/维度直接抛 ValueError（不静默吞掉）。"""
    dims = raw.get("dimensions")
    if not isinstance(dims, list) or not dims:
        raise ValueError("配置缺少 dimensions 列表")
    out_dims: List[dict] = []
    total = 0.0
    for d in dims:
        if not isinstance(d, dict) or not d.get("id"):
            raise ValueError("dimension 缺少 id")
        w = d.get("weight")
        if (not isinstance(w, (int, float)) or isinstance(w, bool)
                or not math.isfinite(w) or w < 0):
            raise ValueError(f"dimension {d.get('id')} 的 weight 非法: {w!r}")
        total += float(w)
        out_dims.append({
            "id": str(d["id"]),
            "name": str(d.get("name") or d["id"]),
            "weight": float(w),
            "desc": str(d.get("desc") or ""),
            "source_tool": str(d.get("source_tool") or ""),
        })
    if total <= 0:
        raise ValueError("所有维度权重之和为 0，无法计算总分")

    th = raw.get("thresholds") or {}
    min_score = th.get("min_score", 60)
    if (not isinstance(min_score, (int, float)) or isinstance(min_score, bool)
            or not math.isfinite(min_score) or not 0 <= min_score <= 100):
        raise ValueError(f"thresholds.min_score 非法: {min_score!r}")
    grades = raw.get("grades") or {}
    for k in ("A", "B", "C"):
        v = grades.get(k)
        if v is not None and (not isinstance(v, (int, float)) or isinstance(v, bool)
                              or not math.isfinite(v) or not 0 <= v <= 100):
            raise ValueError(f"grades.{k} 非法: {v!r}")

    return {
        "version": str(raw.get("version") or "5.17.0"),
        "dimensions": out_dims,
        "thresholds": {
            "min_score": float(min_score),
            "fail_on_below": bool(th.get("fail_on_below", False)),
        },
        "grades": {k: float(grades.get(k, _FALLBACK_CONFIG["grades"][k]))
                   for k in ("A", "B", "C")},
    }


def default_config() -> dict:
    """入库默认配置；文件缺失/损坏时用内置兜底（不阻断）。"""
    raw = _load_json(_WEIGHTS_FILE)
    if raw is None:
        logger.warning("[scorecard] scorecard_weights.json 不可用，使用内置兜底权重")
        return _normalize_config(_FALLBACK_CONFIG)
    try:
        return _normalize_config(raw)
    except Exception as e:
        logger.warning(f"[scorecard] 默认权重非法，使用内置兜底: {e}")
        return _normalize_config(_FALLBACK_CONFIG)


def _config_path(project_path: str) -> str:
    return os.path.join(os.path.abspath(project_path), ".coderef", "eval_config.json")


def _atomic_write_json(path: str, obj: Any) -> None:
    """原子写 JSON：同目录唯一临时文件 + os.replace（并发写不共享 tmp 路径）。"""
    d = os.path.dirname(path) or "."
    os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=d, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def load_config(project_path: str) -> dict:
    """项目级覆盖 > 入库默认 > 内置兜底。覆盖只按维度 id 改权重/阈值，不改维度集合。"""
    cfg = default_config()
    over = _load_json(_config_path(project_path))
    if not isinstance(over, dict):
        return cfg
    weights = over.get("weights")
    if isinstance(weights, dict):
        by_id = {d["id"]: d for d in cfg["dimensions"]}
        for did, w in weights.items():
            if (did in by_id and isinstance(w, (int, float)) and not isinstance(w, bool)
                    and math.isfinite(w) and w >= 0):
                by_id[did]["weight"] = float(w)
    th = over.get("thresholds")
    if isinstance(th, dict):
        ms = th.get("min_score")
        if (isinstance(ms, (int, float)) and not isinstance(ms, bool)
                and math.isfinite(ms) and 0 <= ms <= 100):
            cfg["thresholds"]["min_score"] = float(ms)
        if isinstance(th.get("fail_on_below"), bool):
            cfg["thresholds"]["fail_on_below"] = th["fail_on_below"]
    return cfg


def save_config(project_path: str, payload: dict) -> dict:
    """保存项目级覆盖（只写 weights/thresholds）；非法输入抛 ValueError。"""
    if not isinstance(payload, dict):
        raise ValueError("配置必须是 JSON 对象")
    weights = payload.get("weights") or {}
    thresholds = payload.get("thresholds") or {}
    if not isinstance(weights, dict) or not isinstance(thresholds, dict):
        raise ValueError("weights / thresholds 必须是对象")

    cfg = default_config()
    known = {d["id"] for d in cfg["dimensions"]}
    clean_w: Dict[str, float] = {}
    for did, w in weights.items():
        if did not in known:
            raise ValueError(f"未知维度 id: {did!r}（可选: {sorted(known)}）")
        if (not isinstance(w, (int, float)) or isinstance(w, bool)
                or not math.isfinite(w) or w < 0):
            raise ValueError(f"维度 {did} 的 weight 非法: {w!r}")
        clean_w[did] = float(w)
    clean_th: Dict[str, Any] = {}
    if "min_score" in thresholds:
        ms = thresholds["min_score"]
        if (not isinstance(ms, (int, float)) or isinstance(ms, bool)
                or not math.isfinite(ms) or not 0 <= ms <= 100):
            raise ValueError(f"min_score 非法: {ms!r}")
        clean_th["min_score"] = float(ms)
    if "fail_on_below" in thresholds:
        if not isinstance(thresholds["fail_on_below"], bool):
            raise ValueError("fail_on_below 必须是布尔值")
        clean_th["fail_on_below"] = thresholds["fail_on_below"]

    # 与已保存的项目级覆盖合并（只写本次传入的键会把手改的其它项抹掉）
    path = _config_path(project_path)
    prev = _load_json(path)
    prev = prev if isinstance(prev, dict) else {}
    prev_w = prev.get("weights") if isinstance(prev.get("weights"), dict) else {}
    prev_th = prev.get("thresholds") if isinstance(prev.get("thresholds"), dict) else {}
    all_w = {k: float(v) for k, v in prev_w.items()
             if k in known and isinstance(v, (int, float)) and not isinstance(v, bool)
             and math.isfinite(v) and v >= 0}
    all_w.update(clean_w)
    all_th: Dict[str, Any] = {}
    prev_ms = prev_th.get("min_score")
    if (isinstance(prev_ms, (int, float)) and not isinstance(prev_ms, bool)
            and math.isfinite(prev_ms) and 0 <= prev_ms <= 100):
        all_th["min_score"] = float(prev_ms)
    if isinstance(prev_th.get("fail_on_below"), bool):
        all_th["fail_on_below"] = prev_th["fail_on_below"]
    all_th.update(clean_th)

    # 合并后的权重合计必须为正，否则后续聚合无意义（单维度允许为 0）
    merged_weights = {d["id"]: d["weight"] for d in cfg["dimensions"]}
    merged_weights.update(all_w)
    if sum(merged_weights.values()) <= 0:
        raise ValueError("权重合计为 0，至少保留一个正权重")

    merged = {"weights": all_w, "thresholds": all_th}
    _atomic_write_json(path, merged)
    return {"saved": True, "path": path, "config": merged}


# ────────────────────────────────────────────────────────────────
# 维度计算（全部确定性，不依赖 LLM）
# ────────────────────────────────────────────────────────────────

def _dim(dim_id: str, score: Optional[float], status: str,
         raw: Optional[dict] = None, evidence: Optional[list] = None,
         reason: str = "") -> dict:
    d = {
        "id": dim_id,
        "score": None if score is None else int(round(score)),
        "status": status,
        "raw": raw or {},
        "evidence": evidence or [],
    }
    if reason:
        d["reason"] = reason
    return d


def _unknown(dim_id: str, reason: str, raw: Optional[dict] = None,
             evidence: Optional[list] = None) -> dict:
    return _dim(dim_id, None, "unknown", raw, evidence, reason)


def _not_applicable(dim_id: str, reason: str, raw: Optional[dict] = None) -> dict:
    return _dim(dim_id, None, "not_applicable", raw, [], reason)


def _rel(project_path: str, path: str) -> str:
    try:
        return os.path.relpath(path, project_path).replace(os.sep, "/")
    except Exception:
        return path


def _findings_names(project_path: str) -> List[str]:
    """审计产物的候选文件名：同时覆盖「原样路径」与「绝对路径」两种哈希口径。

    写端（pipeline_runner）按调用方传入的原始路径哈希；本模块内部统一用 abspath，
    因此两种形态都要试，避免同一项目因路径写法不同而找不到产物。
    """
    names: List[str] = []
    try:
        from core.pr_whitelist import project_hash
        for pp in dict.fromkeys([project_path, os.path.abspath(project_path)]):
            names.append(f"audit_findings_{project_hash(pp)}.json")
    except Exception:  # noqa: BLE001
        pass
    names.append("audit_findings.json")
    return list(dict.fromkeys(names))


def find_findings_json(project_path: str) -> Optional[str]:
    """定位落盘的审计 findings JSON（项目侧，优先带项目哈希的版本）。"""
    for d in ("coderef-report", ".coderef"):
        for n in _findings_names(project_path):
            p = os.path.join(project_path, d, n)
            if os.path.isfile(p):
                return p
    return None


def _exclude_dirs(project_path: str) -> List[str]:
    """白名单里的目录级排除项（与 arch_gap / arch_view 口径一致）。"""
    try:
        from core.pipeline_runner import whitelist_list
        return [e["dir"] for e in whitelist_list(project_path) if e.get("dir")]
    except Exception:  # noqa: BLE001
        return []


def _dim_structure(project_path: str) -> dict:
    from core.arch_audit import audit, locate_kg_db

    try:
        a = audit(project_path, exclude_dirs=_exclude_dirs(project_path))
    except Exception as e:
        return _unknown("structure", f"架构诊断执行失败: {e}")
    gs = a.get("graph_stats") or {}
    if not gs.get("has_kg"):
        return _unknown("structure",
                        "未找到知识图谱，请先运行 coderef_architecture 或 coderef_memory(action=sync)")
    s = a.get("summary") or {}
    raw = {
        "modules": gs.get("modules", 0),
        "cycles": s.get("cycles", 0),
        "god_modules": s.get("god_modules", 0),
        "layer_violations": s.get("layer_violations", 0),
        "large_modules": s.get("large_modules", 0),
        "function_recursions": s.get("function_recursions", 0),
    }
    if s.get("health") is None:
        return _not_applicable("structure",
                              "无代码可评（无非测试代码模块；健康度不适用）", raw)
    db = locate_kg_db(project_path)
    return _dim("structure", float(s["health"]) * 10, "ok", raw,
                [_rel(project_path, db)] if db else [])


def _dim_alignment(project_path: str) -> dict:
    from core.arch_gap_analyzer import analyze_gap

    ta = _load_json(os.path.join(project_path, ".coderef", "target_arch.json"))
    if not ta:
        return _unknown("alignment", "未定义目标架构，请先运行 coderef_target_arch_set")
    try:
        r = analyze_gap(project_path, ta)
    except Exception as e:
        return _unknown("alignment", f"差距分析执行失败: {e}")
    al = r.get("alignment")
    if not isinstance(al, dict) or not al:
        # 无知识图谱时 analyze_gap 返回空 alignment（不是 0 分），必须标 unknown
        return _unknown("alignment", str(r.get("summary") or "差距分析未产出对齐度"))
    ma = float(al.get("module_assigned", 0) or 0)
    rc = float(al.get("role_coverage", 0) or 0)
    gaps = r.get("gaps") or []
    high = sum(1 for g in gaps
               if str(g.get("severity") or "").lower() == "high")
    th = r.get("target_health") or {}
    raw = {"module_assigned": round(ma, 2), "role_coverage": round(rc, 2),
           "gaps": len(gaps), "high_gaps": high,
           "target_stale": bool(th.get("stale")),
           "specs_missing": th.get("specs_missing", 0),
           "specs_total": th.get("specs_total", 0)}
    # 定标过期时，"对齐度 1 分"是拿旧目标量今天的代码，不是代码问题 → 不给分
    if th.get("stale"):
        return _unknown(
            "alignment",
            th.get("warning") or "target_arch 已过期，请先重新定标后再评估对齐度",
            raw, [".coderef/target_arch.json"])
    return _dim("alignment", (ma * 0.6 + rc * 0.4) * 100, "ok", raw,
                [".coderef/target_arch.json"])


_SRC_SUFFIXES = (".py", ".js", ".jsx", ".ts", ".tsx", ".vue", ".java", ".go", ".rs")
_SKIP_DIRS = {".git", ".coderef", ".venv", "venv", "__pycache__", "node_modules",
              "dist", "build", "coderef-report", "data", "tests"}
_MAX_STAT_FILES = 20000


def _latest_source_mtime(project_path: str) -> float:
    """项目内源码文件的最新修改时间（用于判断审计产物是否过期）。"""
    latest = 0.0
    seen = 0
    for dirpath, dirnames, filenames in os.walk(project_path):
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]
        for fn in filenames:
            if not fn.endswith(_SRC_SUFFIXES):
                continue
            seen += 1
            if seen > _MAX_STAT_FILES:
                return latest
            try:
                latest = max(latest, os.path.getmtime(os.path.join(dirpath, fn)))
            except OSError:
                continue
    return latest


def _finding_anchor(project_path: str, f: dict) -> str:
    """把审计发现的 file_path 归一成项目相对锚点（`rel#L行号`），目录级/项目外跳过。"""
    fp = str(f.get("file_path") or "")
    if not fp:
        return ""
    if os.path.isabs(fp) and os.path.isdir(fp):
        return ""  # 目录级发现没有行锚点
    rel = _rel(project_path, fp) if os.path.isabs(fp) else fp.replace("\\", "/")
    if rel.startswith(".."):
        return ""
    try:
        ln = int(f.get("line") or 0)
    except (TypeError, ValueError):
        ln = 0
    return f"{rel}#L{ln}" if ln > 0 else rel


def _dim_findings(project_path: str) -> dict:
    fp = find_findings_json(project_path)
    if not fp:
        return _unknown("findings", "未找到 audit_findings 产物，请先运行 coderef_audit")
    data = _load_json(fp)
    if not isinstance(data, dict):
        return _unknown("findings", "audit_findings 产物无法解析")
    findings = data.get("findings") or []
    h = m = lo = 0
    evidence: List[str] = []
    for f in findings:
        if not isinstance(f, dict) or str(f.get("kind") or "") == "advice":
            continue
        # 运行时/第三方产物里的发现不计入健康分：旧快照可能把它们算进分母
        # （外部实测证据锚点前三条全是 .ms-playwright 的 Chromium 脚本）。
        # 按路径段判定，避免子串匹配把 src/cache 这类真实包误排除。
        f_path = str(f.get("file_path") or "").replace("\\", "/")
        segments = set(f_path.split("/"))
        if segments & {".ms-playwright", ".coderef", ".venv", "site-packages",
                       "_refactor_backup"}:
            continue
        tier = str(f.get("tier") or "low").lower()
        if tier == "high":
            h += 1
        elif tier == "medium":
            m += 1
        else:
            lo += 1
        if tier in ("high", "medium") and len(evidence) < 3:
            anchor = _finding_anchor(project_path, f)
            if anchor:
                evidence.append(anchor)
    score = max(0, 100 - h * 8 - m * 3 - lo * 1)
    # 新鲜度：源码比审计产物新超过 1 天 → 标记过期，避免把旧结论当当前健康度
    # fp 保持 findings JSON 路径，不得被循环变量覆盖（外部复审 major）。
    try:
        stale = _latest_source_mtime(project_path) > os.path.getmtime(fp) + 86400
    except OSError:
        stale = None  # 产物 mtime 读不到 → 新鲜度未知，不冒充"新鲜"
    raw = {"high": h, "medium": m, "low": lo,
           "total_files": data.get("total_files", 0),
           "scan_ts": data.get("scan_ts", ""),
           "stale": stale}
    return _dim("findings", score, "ok", raw, evidence)


def _dim_governance(project_path: str) -> dict:
    from core.governance_store import GovernanceStore, gov_db_path

    db = gov_db_path(project_path)
    if not os.path.exists(db):
        return _unknown("governance", "未建立治理库，请先运行 coderef_gov_start")
    store = None
    counts = {}
    try:
        store = GovernanceStore(project_path)
        issues = store.list_issues(view="open", limit=500)
        overdue = store.list_issues(view="overdue", limit=500)
        counts = store.status_counts()
    except Exception as e:
        return _unknown("governance", f"治理库读取失败: {e}")
    finally:
        if store is not None:
            store.close()
    high = sum(1 for i in issues if str(i.get("severity") or "").lower() == "high")
    score = max(0, 100 - high * 10 - (len(issues) - high) * 3 - len(overdue) * 2)

    # 治理库是否可信：全部为人工驳回 = 没有闭环成果；长期未更新 = 历史快照。
    # 两种情况下 0 open 都不代表"治理满分"（外部审计实测 24 条全 Rejected 被读成 100）。
    total = sum(counts.values())
    archived = counts.get("Archived", 0)
    rejected = counts.get("Rejected", 0)
    try:
        age_days = (datetime.now() -
                    datetime.fromtimestamp(os.path.getmtime(db))).days
    except OSError:
        age_days = None  # 读不到库 mtime → 新鲜度未知，不据此判定"未过期"
    raw = {"open": len(issues), "high": high, "overdue": len(overdue),
           "archived": archived, "rejected": rejected,
           "db_age_days": age_days}
    if total and archived == 0 and rejected == total:
        return _unknown(
            "governance",
            "治理库条目全部为人工驳回（没有有效闭环），不能据此给出闭环分",
            raw, [_rel(project_path, db)])
    if age_days is None:
        return _unknown(
            "governance",
            "治理库最后更新时间不可读，无法判断是否过期，请重跑 coderef_gov_report",
            raw, [_rel(project_path, db)])
    if age_days > 30:
        return _unknown(
            "governance",
            f"治理库已过期（最后更新 {age_days} 天前），请重跑 coderef_gov_report 后再评分",
            raw, [_rel(project_path, db)])
    return _dim("governance", score, "ok", raw, [_rel(project_path, db)])


def _dim_docs(project_path: str, module_total: int) -> dict:
    from core.pr_whitelist import count_md, detect_wiki_output_dir

    # 只看真正的 Wiki 产物目录，不回退到通用 docs/（避免把用户文档算成 Wiki 覆盖）
    wiki_dir = detect_wiki_output_dir(project_path)
    if not wiki_dir:
        return _unknown("docs", "未生成 Wiki 文档（未发现 docs/wiki 或 .coderef/wiki），请先运行 coderef_docs")
    md = count_md(wiki_dir)
    if md <= 0:
        return _unknown("docs", "Wiki 目录存在但没有 Markdown 文档")
    if module_total <= 0:
        return _unknown("docs", "缺少代码模块数（需先建知识图谱），无法计算文档覆盖率",
                        {"md_files": md})
    score = min(100, md / module_total * 100)
    raw = {"md_files": md, "modules": module_total}
    return _dim("docs", score, "ok", raw, [_rel(project_path, wiki_dir)])


# ────────────────────────────────────────────────────────────────
# 聚合
# ────────────────────────────────────────────────────────────────

def _grade(overall: Optional[float], grades: dict) -> str:
    if overall is None:
        return "N/A"
    if overall >= grades["A"]:
        return "A"
    if overall >= grades["B"]:
        return "B"
    if overall >= grades["C"]:
        return "C"
    return "D"


def _aggregate(project_path: str, cfg: dict, results: Dict[str, dict]) -> dict:
    """把各维度结果聚合成总分/等级/置信度/说明（不落盘）。"""
    meta = {d["id"]: d for d in cfg["dimensions"]}
    dims: List[dict] = []
    known: List[dict] = []
    total_w = 0.0
    for did, m in meta.items():
        d = results.get(did) or _unknown(did, "维度未实现")
        d["name"] = m["name"]
        d["desc"] = m["desc"]
        d["source_tool"] = m["source_tool"]
        d["weight"] = m["weight"]
        dims.append(d)
        total_w += m["weight"]
        if d["status"] == "ok" and d["score"] is not None:
            known.append(d)

    no_code = (results.get("structure") or {}).get("status") == "not_applicable"
    coverage = (sum(d["weight"] for d in known) / total_w) if total_w else 0.0
    known_w = sum(d["weight"] for d in known)
    if no_code or not known or known_w <= 0:
        # 已知维度权重合计为 0 视为「没有可用证据」，避免除以 0 抛异常
        overall = None
    else:
        overall = round(sum(d["score"] * d["weight"] for d in known) / known_w)

    if overall is None:
        confidence = "low"
    elif coverage >= 0.8:
        confidence = "high"
    elif coverage >= 0.5:
        confidence = "medium"
    else:
        confidence = "low"

    notes: List[str] = []
    if no_code:
        notes.append("无代码可评：按 U-46 口径不给整体健康分（N/A）")
    for d in dims:
        if d["status"] == "unknown":
            notes.append(f"{d['name']}：{d.get('reason', '证据不足')}")
        elif (d.get("raw") or {}).get("stale"):
            notes.append(f"{d['name']}：审计产物已过期（代码比产物更新），请重跑 coderef_audit")
    if overall is not None and coverage < 0.8:
        notes.append(f"证据覆盖率 {coverage:.0%}，总分只代表已知维度")

    th = cfg["thresholds"]
    return {
        "version": __version__,
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "project": os.path.basename(project_path.rstrip(os.sep)),
        "no_code": no_code,
        "overall": overall,
        "grade": _grade(overall, cfg["grades"]),
        "confidence": confidence,
        "coverage": round(coverage, 2),
        "dimensions": dims,
        "gate": {"min_score": th["min_score"],
                 "fail_on_below": th["fail_on_below"],
                 "pass": None if overall is None else overall >= th["min_score"]},
        "notes": notes,
        "weights": {d["id"]: d["weight"] for d in dims},
    }


def compute(project_path: str, config: Optional[dict] = None) -> dict:
    """计算记分卡（确定性、不落盘）。落盘请显式调用 write_card()。"""
    project_path = os.path.abspath(project_path)
    cfg = config or load_config(project_path)
    structure = _dim_structure(project_path)
    module_total = int((structure.get("raw") or {}).get("modules") or 0)
    results = {
        "structure": structure,
        "alignment": _dim_alignment(project_path),
        "findings": _dim_findings(project_path),
        "governance": _dim_governance(project_path),
        "docs": _dim_docs(project_path, module_total),
    }
    return _aggregate(project_path, cfg, results)


def write_card(project_path: str, card: dict) -> str:
    """把记分卡落盘到 `<project>/.coderef/scorecard.json`（机器可读旁路产物）。"""
    out = os.path.join(os.path.abspath(project_path), ".coderef", "scorecard.json")
    _atomic_write_json(out, card)
    return out


# ────────────────────────────────────────────────────────────────
# CLI（CI 门禁用）
# ────────────────────────────────────────────────────────────────

def _summary_text(card: dict) -> str:
    if card["overall"] is None:
        head = f"{card['project']}：健康分不适用（{card['grade']}）"
    else:
        head = (f"{card['project']}：{card['overall']}/100（{card['grade']} 级，"
                f"置信度 {card['confidence']}，证据覆盖 {card['coverage']:.0%}）")
    lines = [head]
    for d in card["dimensions"]:
        if d["status"] == "ok":
            lines.append(f"  - {d['name']}: {d['score']}（权重 {d['weight']}）")
        else:
            lines.append(f"  - {d['name']}: {d['status']}"
                         + (f"（{d.get('reason')}）" if d.get("reason") else ""))
    if card["notes"]:
        lines.append("  注意: " + "；".join(card["notes"]))
    return "\n".join(lines)


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        prog="python -m core.scorecard",
        description="项目健康记分卡（确定性聚合；CI 门禁用）")
    ap.add_argument("--project", default=".", help="目标项目路径（默认当前目录）")
    ap.add_argument("--json", action="store_true", help="以 JSON 输出")
    ap.add_argument("--out", default="", help="scorecard.json 落盘路径（默认 <project>/.coderef/scorecard.json）")
    ap.add_argument("--min-score", type=float, default=None, help="覆盖配置里的阈值")
    ap.add_argument("--fail-on-below", action="store_true",
                    help="低于阈值时退出码 1（默认 warn：只提示不阻断）")
    args = ap.parse_args(argv)

    pp = os.path.abspath(args.project)
    if not os.path.isdir(pp):
        print(f"[scorecard] 项目路径不存在: {pp}", file=sys.stderr)
        return 2
    try:
        card = compute(pp)
    except Exception as e:  # noqa: BLE001
        print(f"[scorecard] 计算失败: {e}", file=sys.stderr)
        # warn 模式（默认）不应因计算失败而阻断构建；仅显式阻断时才返回非 0
        try:
            blocking = args.fail_on_below or bool(
                (load_config(pp).get("thresholds") or {}).get("fail_on_below"))
        except Exception:  # noqa: BLE001
            blocking = args.fail_on_below
        return 2 if blocking else 0

    if args.out:
        out = os.path.abspath(args.out)
    else:
        out = os.path.join(pp, ".coderef", "scorecard.json")
    try:
        _atomic_write_json(out, card)
    except Exception as e:  # noqa: BLE001
        print(f"[scorecard] 落盘失败: {e}", file=sys.stderr)

    if args.json:
        print(json.dumps(card, ensure_ascii=False, indent=2))
    else:
        print(_summary_text(card))

    min_score = args.min_score
    if min_score is None:
        min_score = card["gate"]["min_score"]
    overall = card["overall"]
    if overall is None:
        return 0
    if overall < min_score:
        msg = f"[scorecard] 低于阈值 {min_score}（当前 {overall}）"
        print(msg, file=sys.stderr)
        blocking = args.fail_on_below or bool((card.get("gate") or {}).get("fail_on_below"))
        return 1 if blocking else 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
