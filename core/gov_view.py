# -*- coding: utf-8 -*-
"""
GovView v1.0 —— CodeRef 5.1 治理工作项预置视图

借鉴 plane 的 View（保存过滤条件的"智能书签"），提供固定查询入口，
降低使用门槛。实际过滤逻辑实现在 GovernanceStore.list_issues，
本模块是视图目录 + 薄委托。
"""

from typing import Dict, Any

from core.governance_store import GovernanceStore


GOV_VIEWS = {
    "open": "未闭环：所有非 Archived / 非 Rejected 的在途治理项",
    "all": "全部治理工作项（含已归档/已豁免）",
    "high": "高严重级在途项（按 severity=high 过滤）",
    "recent": "最近出现的治理项（按 last_seen 倒序）",
    "recurred": "复发的治理项（曾归档又出现，重点复核）",
    "rejected": "已豁免项（Rejected，含豁免原因）",
    "archived": "已归档项（Archived）",
    "overdue": "已过截止日且未闭环的项",
    "assigned": "已分配负责人的项",
}


class GovView:
    """治理视图目录与查询委托。"""

    def __init__(self, store: GovernanceStore):
        self.store = store

    def available(self) -> Dict[str, Any]:
        return {"view_names": list(GOV_VIEWS),
                "descriptions": GOV_VIEWS}

    def query(self, view: str = "open", cycle_id: str = "",
              assignee: str = "", limit: int = 500) -> Dict[str, Any]:
        if view not in GOV_VIEWS and view not in ("", "open"):
            return {"ok": False,
                    "message": f"未知视图 {view}，可用: {list(GOV_VIEWS)}"}
        v = view or "open"
        # high 视图是 open 基础 + severity=high，交给 store 的 status 过滤子集
        if v == "high":
            issues = [i for i in self.store.list_issues(
                cycle_id=cycle_id, view="open", assignee=assignee, limit=limit)
                if i.get("severity") == "high"]
            return {"ok": True, "view": v, "count": len(issues),
                    "issues": issues}
        issues = self.store.list_issues(cycle_id=cycle_id, view=v,
                                        assignee=assignee, limit=limit)
        return {"ok": True, "view": v, "count": len(issues),
                "issues": issues}