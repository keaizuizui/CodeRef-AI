# -*- coding: utf-8 -*-
"""
GovPipeline v1.0 —— CodeRef 5.2 治理自动化连接线

把"工作项治理 → 生成任务卡 → 复验 → 归档"串成一条可追踪流水线，
补上 5.1"自动化的价值在日常运营，不是写代码"这一环：运维只负责往流水线
里丢确认过的工作项，推进与达标判定自动完成（执行修复仍交给编程 AI）。

打通：Detected / Confirmed → Fixing →（生成任务卡 + 复验）→ Verified
全程写 issue_event 活动日志，状态机约束仍由 governance_store 强制。
"""

import json
from typing import Dict, List, Optional, Any

from loguru import logger

from core.governance_store import (
    GovernanceStore, gap_key, STATUS_CONFIRMED, STATUS_DETECTED,
    STATUS_FIXING, STATUS_VERIFIED,
)


class GovPipeline:
    """治理自动化流水线（治理工作项 → 任务卡 → 复验 → 归档）。"""

    def __init__(self, project_path: str):
        self.project_path = project_path
        self.store = GovernanceStore(project_path)

    # ------------------------------------------------------------

    def run(self, issue_ids: List[str], changed_files: Optional[List[str]] = None,
            auto_verified: bool = True) -> Dict[str, Any]:
        """对一批工作项执行治理流水线。

        Args:
            issue_ids: 治理工作项 id 列表（须为 Confirmed/Fixing 的在途项；Detected 需先人工确认）。
            changed_files: 本次已执行的改动文件集合（增量复验）；None = 全量复验。
            auto_verified: 达标判定通过后是否自动流转 Verified。

        Returns:
            results / summary。
        """
        results = []
        for iid in issue_ids or []:
            results.append(self._process_one(iid, changed_files, auto_verified))

        ok = [r for r in results if r.get("ok")]
        verified = [r for r in results if r.get("verified")]
        failed = [r for r in results if not r.get("ok")]
        return {
            "ok": True,
            "project_path": self.project_path,
            "tool": "coderef_gov_pipeline",
            "total": len(results),
            "processed": len(ok),
            "verified": len(verified),
            "failed": len(failed),
            "results": results,
            "summary": {
                "total": len(results),
                "processed": len(ok),
                "verified": len(verified),
                "failed": len(failed),
                "message": ("达标判定基于复验后该差距不再复现；已达标自动"
                            " Verified（auto_verified），未达标保持 Fixing 附缺口。"),
            },
        }

    # ------------------------------------------------------------

    def _process_one(self, issue_id: str, changed_files: Optional[List[str]],
                     auto_verified: bool) -> Dict[str, Any]:
        iss = self.store.get_issue(issue_id)
        if iss is None:
            return {"issue_id": issue_id, "ok": False, "message": "工作项不存在"}
        old = iss["status"]
        if old not in (STATUS_CONFIRMED, STATUS_FIXING):
            return {"issue_id": issue_id, "ok": False,
                    "message": f"状态 {old} 不在治理流水线内（仅限 Confirmed/Fixing；Detected 需先人工确认）"}

        # 1. 进入 Fixing（Confirmed → Fixing）
        if old == STATUS_CONFIRMED:
            ok, msg = self.store.transition(issue_id, STATUS_FIXING,
                                            actor="pipeline",
                                            detail="进入治理流水线")
            if not ok:
                return {"issue_id": issue_id, "ok": False,
                        "message": f"无法进入 Fixing: {msg}"}

        # 2. 凭差距快照生成任务卡（供编程 AI 执行）
        gap = self._load_gap(iss)
        task_pack = self._make_task(gap)

        # 3. 复验达标判定
        verifier = self._verifier()
        verdict = self._judge(gap, verifier, changed_files)

        verified = verdict["met"] and auto_verified
        if verified:
            ok, msg = self.store.transition(issue_id, STATUS_VERIFIED,
                                            actor="pipeline",
                                            detail="复验达标自动流向 Verified")
            if not ok:
                verified = False
                verdict["reason"] = f"{verdict['reason']}；状态流转被拒: {msg}"

        return {
            "issue_id": issue_id,
            "ok": True,
            "gap_type": gap.get("type"),
            "module": gap.get("module", ""),
            "status": STATUS_VERIFIED if verified else STATUS_FIXING,
            "verified": verified,
            "task": task_pack,
            "verdict": verdict,
        }

    # ------------------------------------------------------------

    def _load_gap(self, iss: Dict[str, Any]) -> Dict[str, Any]:
        try:
            snap = json.loads(iss.get("snapshot") or "{}")
            return snap if isinstance(snap, dict) else {}
        except Exception:  # noqa: BLE001
            return {}

    def _make_task(self, gap: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """为单条差距生成任务卡（确定性）。"""
        if not gap:
            return None
        try:
            from core.refactor_task_generator import RefactorTaskGenerator
            result = RefactorTaskGenerator().generate(
                self.project_path, gap_result={"gaps": [gap]})
            tasks = result.get("tasks") or []
            return tasks[0] if tasks else {"message": "该差距无可执行任务卡"}
        except Exception as e:  # noqa: BLE001
            return {"message": f"任务卡生成失败: {e}"}

    def _verifier(self):
        from core.arch_alignment_verifier import ArchAlignmentVerifier
        return ArchAlignmentVerifier()

    def _judge(self, gap: Dict[str, Any], verifier, changed_files) -> Dict[str, Any]:
        """达标判定：复验复检清单里已无本差距，且（可选）对齐度未劣化。"""
        try:
            verify = verifier.verify(self.project_path,
                                     changed_files=changed_files)
        except Exception as e:  # noqa: BLE001
            return {"met": False, "reason": f"复验异常: {e}", "score": None}
        if not verify.get("ok"):
            return {"met": False, "reason": verify.get("message", "复验失败"),
                    "score": None}
        key = gap_key(gap) if gap else None
        reappeared = []
        if key:
            reappeared = [vg.get("type") for vg in (verify.get("gaps") or [])
                          if gap_key(vg) == key]
        met = not reappeared
        return {
            "met": met,
            "reason": ("该差距已不复现" if met
                       else f"该差距仍复现: {reappeared}"),
            "score": verify.get("score"),
            "dimensions": verify.get("dimensions"),
            "remaining": verify.get("remaining"),
        }

    def __del__(self):
        try:
            self.store.close()
        except Exception:  # noqa: BLE001
            pass