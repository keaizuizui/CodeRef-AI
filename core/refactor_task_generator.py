# -*- coding: utf-8 -*-
"""
RefactorTaskGenerator — 重构任务卡生成器（5.0 Phase 2）

把差距清单（arch_gap_analyzer）转化为编程 AI 可执行的重构任务卡，
每张任务卡含具体操作、影响范围、验证标准。

任务类型映射（差距 → 任务卡）：
  missing          → create_module    新建缺失模块
  dependency_violation → fix_dependency  移除违例依赖
  cycle            → break_cycle      拆环
  business_gap     → implement_flow   实现业务步骤
  unassigned       → move_module      移动模块入角色
  god_module       → split_module     拆分上帝模块
  large_module     → split_module     拆分异常规模模块

确定性：影响范围来自知识图谱调用闭包（file_base 匹配文件级调用者/被调用者），
不依赖 LLM。
"""

import os
from typing import Any, Dict, List, Optional
from core.arch_audit import locate_kg_db
from core.graph_closure import load_graph, file_base

# 差距类型 → 任务类型映射
GAP_TO_TASK = {
    "missing": "create_module",
    "dependency_violation": "fix_dependency",
    "cycle": "break_cycle",
    "business_gap": "implement_flow",
    "unassigned": "move_module",
    "god_module": "split_module",
    "large_module": "split_module",
}

PRIORITY_ORDER = {"high": 0, "medium": 1, "low": 2}

_TASK_EXEC_ORDER = {
    "break_cycle": 0, "fix_dependency": 1, "create_module": 2,
    "implement_flow": 3, "move_module": 4, "split_module": 5,
}


class RefactorTaskGenerator:
    """重构任务卡生成器（5.0 Phase 2）"""

    def generate(
        self,
        project_path: str,
        gap_result: Optional[dict] = None,
        target_arch: Optional[dict] = None,
        db_path: Optional[str] = None,
    ) -> dict:
        """生成重构任务卡列表。

        Args:
            project_path: 目标项目路径。
            gap_result: 差距清单（coderef_arch_gap 输出）。缺省时内部自动分析。
            target_arch: 目标架构（gap_result 缺省时需要）。
            db_path: 知识图谱 db（缺省自动定位）。

        Returns:
            tasks / summary 结构。
        """
        db = db_path or locate_kg_db(project_path)
        if not db or not os.path.exists(db):
            return {
                "ok": False,
                "project_path": project_path,
                "message": "知识图谱不存在，需先构建（coderef_audit / coderef_memory(action=sync)）",
                "tasks": [],
                "summary": {"total": 0},
            }

        if gap_result is None:
            from core.arch_gap_analyzer import analyze_gap
            if target_arch is None:
                target_arch = self._load_stored_arch(project_path)
            if target_arch is None:
                return {"ok": False, "project_path": project_path,
                        "message": "缺少目标架构，请先设置或传入 target_arch",
                        "tasks": [], "summary": {"total": 0}}
            gap_result = analyze_gap(project_path, target_arch, db_path=db)

        nodes, adj = load_graph(db)
        tasks = []
        for i, g in enumerate(gap_result.get("gaps", []), 1):
            task = self._build_task(i, g, nodes, adj)
            if task:
                tasks.append(task)

        # 排序：优先任务类型（拆环→修依赖→建缺失→实现流程→移动模块→拆分），
        # 其内按严重级 high→mid→low。
        tasks.sort(key=lambda t: (_TASK_EXEC_ORDER.get(t["type"], 9),
                                  PRIORITY_ORDER.get(t["priority"], 9)))

        return {
            "ok": True,
            "project_path": project_path,
            "tool": "coderef_refactor_plan",
            "tasks": tasks,
            "summary": {
                "total": len(tasks),
                "by_type": self._count_by_type(tasks),
                "high": sum(1 for t in tasks if t["priority"] == "high"),
                "medium": sum(1 for t in tasks if t["priority"] == "medium"),
                "low": sum(1 for t in tasks if t["priority"] == "low"),
                "message": ("按执行顺序排序（拆环修复→移动归属→拆分）。"
                           "每张任务卡含 operations/impact/verify，可直接交编程 AI 执行"),
            },
        }

    # ────────────────────────────────────────────────
    # 内部方法
    # ────────────────────────────────────────────────

    def _load_stored_arch(self, project_path: str) -> Optional[dict]:
        import json
        path = os.path.join(project_path, ".coderef", "target_arch.json")
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        return None

    @staticmethod
    def _count_by_type(tasks: List[dict]) -> dict:
        out = {}
        for t in tasks:
            out[t["type"]] = out.get(t["type"], 0) + 1
        return out

    def _build_task(self, tid: int, g: dict, nodes: dict,
                    adj: Dict[str, List[str]]) -> Optional[dict]:
        gtype = g.get("type", "")
        task_type = GAP_TO_TASK.get(gtype)
        if not task_type:
            return None
        severity = g.get("severity", "low")

        if task_type == "move_module":
            mod = g.get("module", "")
            rid = g.get("role_id", "") or ""
            title = (f"将模块 {mod} 归属到角色 {rid}"
                     if rid else f"决定模块 {mod} 的目标角色（当前游离）")
            operations = [{"action": "assign", "file": mod,
                           "target_role": rid,
                           "note": "从游离模块中为该文件选择目标技术角色并移入对应目录"}]
            impact = self._impact_of_module(mod, nodes, adj)
        elif task_type == "create_module":
            mod = g.get("module", "")
            title = f"创建缺失模块 {mod}"
            operations = [{"action": "create", "file": mod,
                           "note": f"按角色 {g.get('role_name','')} 职责补充实现"}]
            impact = {"callers": [], "callees": [], "files": 0}
        elif task_type == "fix_dependency":
            fm, tm = g.get("from_module", ""), g.get("to_module", "")
            title = f"修复违例依赖: {fm} → {tm}"
            operations = [{"action": "remove_dependency", "from": fm, "to": tm,
                           "note": "移除或调整归属以满足角色约束"}]
            impact = self._impact_of_module(fm, nodes, adj)
        elif task_type == "break_cycle":
            cyc = g.get("modules", [])
            title = f"打破循环依赖: {g.get('detail','')[:40]}"
            operations = [{"action": "break_cycle", "modules": cyc,
                           "note": "提取公共层或依赖倒置，消除环"}]
            impact = {"callers": [], "callees": [], "files": len(cyc), "cycle_modules": cyc}
        elif task_type == "split_module":
            mod = g.get("module", "")
            title = f"拆分模块 {mod}"
            operations = [{"action": "split", "file": mod,
                           "note": "按职责拆分为多个小模块，降低扇出/规模"}]
            impact = self._impact_of_module(mod, nodes, adj)
        elif task_type == "implement_flow":
            title = f"实现业务步骤 {g.get('step_name','')}（{g.get('flow_id','')}）"
            operations = [{"action": "implement", "flow": g.get("flow_id", ""),
                           "step": g.get("step_id", ""), "roles": g.get("roles", []),
                           "note": "为业务步骤关联的角色创建有效代码实现"}]
            impact = {"callers": [], "callees": [], "files": 0, "roles": g.get("roles", [])}
        else:
            return None

        return {
            "id": f"T{tid:03d}",
            "type": task_type,
            "gap_type": gtype,
            "priority": severity,
            "title": title,
            "gap_ref": f"{gtype}:{g.get('module', g.get('step_id', ''))}",
            "operations": operations,
            "impact": impact,
            "verify": {
                "arch": "重构后执行 coderef_arch_gap，确认本差距消失",
                "functional": "执行 coderef_flow_verify，确认覆盖的业务流程不退化",
            },
            "dependencies": [],
        }

    def _impact_of_module(self, module: str, nodes: dict,
                          adj: Dict[str, List[str]]) -> dict:
        """模块影响范围：图谱闭包文件级 caller/callee（决定性，不依赖 LLM）。

        通过 file_base 匹配节点归属文件，再沿 CALLS 边统计：
          在 caller 集合中，凡调用目标文件中任意符号的节点所在文件 → caller
          目标文件内符号调用的其他文件 → callee
        """
        if not module:
            return {"callers": [], "callees": [], "files": 0}
        base_target = module.split("/")[-1]
        if "/" in module:
            base_target = module.rsplit("/", 1)[-1]

        target_ids = {
            nid for nid, n in nodes.items()
            if file_base(n) == base_target
        }
        if not target_ids:
            return {"callers": [], "callees": [], "files": 0}

        callers, callees = set(), set()
        for src, targets in adj.items():
            if src in target_ids:
                for t in targets:
                    if t not in target_ids:
                        callees.add(file_base(nodes[t]) or t)
        for src, targets in adj.items():
            if any(t in target_ids for t in targets) and src not in target_ids:
                callers.add(file_base(nodes[src]) or src)

        callers.discard("")
        callees.discard("")
        return {
            "callers": sorted(callers),
            "callees": sorted(callees),
            "files": len(callers) + len(callees) + 1 if target_ids else 0,
        }