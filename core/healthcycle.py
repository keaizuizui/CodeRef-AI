# -*- coding: utf-8 -*-
"""
HealthCycle v1.0 —— CodeRef 5.1 定期体检周期编排

架构治理的"运营壳"入口：把一次差距扫描变成一场可追踪、可回顾的体检周期。

- start_cycle：建档周期 + 把当前 gap 全量导入为 Detected 工作项
- import_gaps ：把任意一次扫描成果并入当前/常驻治理队列（去重/复发/豁免）
- close_cycle ：收尾并输出本期统计
- active_report：产出当前治理视图（供 MCP 消费）

差距分析复用 5.0 的 arch_gap_analyzer，本模块只做"治理运营"，不重写分析。
"""

import json
from datetime import datetime
from typing import Dict, List, Optional, Any

from loguru import logger

from core.governance_store import (
    GovernanceStore, gap_key, STATUS_DETECTED, STATUS_REJECTED)


def _load_target_arch(project_path: str) -> Optional[Dict[str, Any]]:
    """读取 <project>/.coderef/target_arch.json（缺省返回 None）。"""
    import os
    p = os.path.join(os.path.abspath(project_path), ".coderef", "target_arch.json")
    if not os.path.isfile(p):
        return None
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"读取目标架构失败: {e}")
        return None


def _run_gap(project_path: str, target_arch: Optional[Dict[str, Any]] = None,
             max_unassigned: int = 50) -> Dict[str, Any]:
    """调用 5.0 差距分析器（确定性，不依赖 LLM）。"""
    from core.arch_gap_analyzer import analyze_gap
    ta = target_arch or _load_target_arch(project_path)
    if ta is None:
        return {"ok": False, "gaps": [],
                "summary": {"error": "未找到目标架构，请先 coderef_target_arch_set"}}
    return analyze_gap(project_path, ta, max_unassigned=max_unassigned)


class HealthCycle:
    """体检周期管理（面向 MCP worker 的薄封装）。"""

    def __init__(self, project_path: str):
        self.project_path = project_path
        self.store = GovernanceStore(project_path)

    # ---------- 建档与导入 ----------

    def start_cycle(self, name: str, description: str = "",
                    end_date: str = "", target_arch: Optional[dict] = None,
                    max_unassigned: int = 50) -> Dict[str, Any]:
        """建档一个体检周期并把当前差距全量导入。"""
        cyc = self.store.create_cycle(
            name=name or f"体检 {datetime.now().strftime('%Y-%m-%d')}",
            description=description, end_date=end_date)
        gap_res = _run_gap(self.project_path, target_arch, max_unassigned)
        if not gap_res.get("ok"):
            self.store.close_cycle(cyc["id"], note="无目标架构，周期空跑关闭")
            # summary 可能是 dict 或字符串（arch_gap_analyzer 失败路径），类型容错
            _sum = gap_res.get("summary", {})
            _msg = (_sum.get("error", "差距分析失败") if isinstance(_sum, dict)
                    else str(_sum) or "差距分析失败")
            return {"ok": False, "cycle": cyc, "message": _msg}
        imported = self.import_gaps(gap_res, cycle_id=cyc["id"])
        cyc["imported_new"] = imported["new"]
        cyc["kept"] = imported["kept"]
        cyc["recurred"] = imported["recurred"]
        cyc["reactivated"] = imported["reactivated"]
        cyc["skipped"] = imported["skipped"]
        cyc["summary"] = gap_res.get("summary", {})
        cyc["graph_stats"] = gap_res.get("graph_stats", {})
        return {"ok": True, "cycle": cyc, **imported}

    def import_gaps(self, gap_result: Dict[str, Any],
                    cycle_id: str = "") -> Dict[str, Any]:
        """把一次差距分析成果并入治理队列（去重/复发/豁免生效）。"""
        stats = {"new": 0, "kept": 0, "recurred": 0,
                 "reactivated": 0, "skipped": 0}
        open_cyc = cycle_id or (
            self.store.open_cycle() or {}).get("id") or ""
        for g in gap_result.get("gaps") or []:
            _, action = self.store.upsert_issue(g, cycle_id=open_cyc)
            # upsert_issue 返回 "created"，映射到统计键 "new"
            if action == "created":
                action = "new"
            if action in stats:
                stats[action] += 1
        return stats

    # ---------- 工作项操作 ----------

    def issues(self, cycle_id: str = "", status: str = "", view: str = "",
               assignee: str = "", limit: int = 500) -> Dict[str, Any]:
        issues = self.store.list_issues(cycle_id=cycle_id, status=status,
                                        view=view, assignee=assignee,
                                        limit=limit)
        counts = self.store.status_counts(cycle_id=cycle_id)
        return {"ok": True, "view": view or "open",
                "count": len(issues), "status_counts": counts,
                "issues": issues}

    def transition_issue(self, issue_id: str, to_state: str, actor: str = "",
                         detail: str = "") -> Dict[str, Any]:
        ok, msg = self.store.transition(issue_id, to_state, actor, detail)
        iss = self.store.get_issue(issue_id)
        return {"ok": ok, "message": msg, "issue": iss}

    def reject_issue(self, issue_id: str, reason: str = "", actor: str = "",
                     detail: str = "") -> Dict[str, Any]:
        ok, msg = self.store.reject(issue_id, reason, actor, detail)
        iss = self.store.get_issue(issue_id)
        return {"ok": ok, "message": msg, "issue": iss}

    def set_issue_meta(self, issue_id: str, priority: str = None,
                       assignee: str = None, due_date: str = None,
                       note: str = None, actor: str = "") -> Dict[str, Any]:
        ok, msg = self.store.set_issue_meta(
            issue_id, priority=priority, assignee=assignee, due_date=due_date,
            note=note, actor=actor)
        iss = self.store.get_issue(issue_id)
        return {"ok": ok, "message": msg, "issue": iss}

    # ---------- 收尾与报告 ----------

    def close_cycle(self, cid: str, note: str = "") -> Dict[str, Any]:
        # cid 缺省时定位当前 open 周期（），与 report/issues 的缺省逻辑一致；
        # 直接拿空串去 store.close_cycle 会误报"周期不存在或已关闭"。
        if not cid:
            open_cyc = self.store.open_cycle()
            if not open_cyc:
                return {"ok": False, "message": "没有 open 周期可关闭，请先用 coderef_gov_start 建档"}
            cid = open_cyc["id"]
        cyc = self.store.close_cycle(cid, note=note)
        if cyc is None:
            return {"ok": False, "message": "周期不存在或已关闭"}
        summary = self.store.cycle_summary(cid)
        return {"ok": True, "cycle": cyc, "summary": summary}

    def report(self, cid: str = "") -> Dict[str, Any]:
        """单期报告 + 跨期趋势（供 MCP/HTML 呈现）。"""
        cyc = self.store.get_cycle(cid) if cid else self.store.open_cycle()
        summary = (self.store.cycle_summary(cyc["id"])
                   if cyc else None)
        return {
            "ok": True,
            "active_cycle": cyc,
            "summary": summary,
            "trend": self.store.trend(),
            "status_counts": self.store.status_counts(
                cyc["id"] if cyc else ""),
        }

    def __del__(self):
        try:
            self.store.close()
        except Exception:  # noqa: BLE001
            pass