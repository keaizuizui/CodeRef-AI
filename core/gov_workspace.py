# -*- coding: utf-8 -*-
"""
GovWorkspace v1.0 —— CodeRef 5.2 多代码库聚合治理

对应 plane 的 Workspace：把多个代码库的治理状态聚合成一个跨仓视图，
给出跨仓汇总与 TOP 风险，便于在"公司/团队"尺度上把握整体架构健康。

复用 governance_store（每个仓一份 governance.db）；本模块只做聚合，不改治理库。
"""

from typing import Any, Dict, List

from loguru import logger

SEV_ORDER = {"high": 0, "medium": 1, "low": 2}


def _repo_report(project_path: str) -> Dict[str, Any]:
    from core.healthcycle import HealthCycle
    try:
        hc = HealthCycle(project_path)
        report = hc.report()
        cycl = report.get("active_cycle") if report.get("active_cycle") else None
        open_issues = hc.store.list_issues(view="open", limit=1000)
        closed_cnt = (report.get("status_counts") or {}).get("Archived", 0)
        high = [it for it in open_issues if it["severity"] == "high"]
        hc.store.close()
        return {
            "project": project_path,
            "ok": True,
            "open_total": len(open_issues),
            "open_high": len(high),
            "open_medium": sum(1 for it in open_issues
                               if it["severity"] == "medium"),
            "open_low": sum(1 for it in open_issues if it["severity"] == "low"),
            "recurred": (report.get("summary") or {}).get("recurred", 0),
            "archived_total": closed_cnt,
            "last_cycle": (cycl or {}).get("id", ""),
            "last_cycle_name": (cycl or {}).get("name", ""),
            "top_high": [{"id": it["id"], "gap_type": it["gap_type"],
                          "module": it["module"], "title": it["title"][:60]}
                          for it in high[:5]],
        }
    except Exception as e:  # noqa: BLE001
        return {"project": project_path, "ok": False, "error": str(e)}


def aggregate(projects: List[str]) -> Dict[str, Any]:
    """跨代码库聚合治理报告。"""
    repos = [_repo_report(p) for p in (projects or [])]
    oks = [r for r in repos if r.get("ok")]
    total_open = sum(r.get("open_total", 0) for r in oks)
    total_high = sum(r.get("open_high", 0) for r in oks)
    total_recurred = sum(r.get("recurred", 0) for r in oks)
    total_archived = sum(r.get("archived_total", 0) for r in oks)
    return {
        "ok": True,
        "tool": "coderef_gov_workspace",
        "repo_count": len(projects),
        "ok_count": len(oks),
        "aggregate": {
            "open_total": total_open,
            "open_high": total_high,
            "open_medium": sum(r.get("open_medium", 0) for r in oks),
            "open_low": sum(r.get("open_low", 0) for r in oks),
            "recurred_total": total_recurred,
            "archived_total": total_archived,
            "health_hint": ("良好" if total_high == 0 else
                            "关注高优先级风险(open_high>0)"),
        },
        "repos_sorted": sorted(oks,
                               key=lambda r: (r.get("open_high", 0),
                                              r.get("open_total", 0)),
                               reverse=True),
        "repos": repos,
        "summary": {
            "repo_count": len(projects),
            "total_open": total_open,
            "total_high": total_high,
            "message": ("跨仓聚合：open 工作项 / 高危项 / 复发数 / 已归档数。"
                        "repos_sorted 为按高危数降序的各仓明细。"),
        },
    }