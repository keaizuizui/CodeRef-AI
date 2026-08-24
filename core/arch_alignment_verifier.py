# -*- coding: utf-8 -*-
"""
ArchAlignmentVerifier — 架构对齐验证器（5.0 Phase 2）

重构后确定性验证现状与目标架构的对齐度，输出四维评分 + 差距复检。

对齐度评分（0–100）：
  职责对齐 40%  已归属模块数 / 总模块数（游离模块惩罚）
  依赖健康 30%  1 - 违例依赖边 / 总模块依赖边
  业务覆盖 20%  有实现角色数 / 总角色数
  代码健康 10%  复用 arch_audit 健康度归一化（0–10 → 0–1）

增量验证：传入本次改动文件集合时，只对受影响子图（含改动文件的调用闭包）
复检，快速反馈单张任务卡是否达标。
"""

import os
from typing import Any, Dict, List, Optional
from core.arch_gap_analyzer import analyze_gap, _is_test_module, _match_module_ids
from core.arch_audit import locate_kg_db, module_of
from core.graph_closure import load_graph, file_base

WEIGHTS = {
    "responsibility": 0.40,
    "dependency": 0.30,
    "business": 0.20,
    "health": 0.10,
}


class ArchAlignmentVerifier:
    """架构对齐验证器（5.0 Phase 2）"""

    def verify(
        self,
        project_path: str,
        target_arch: Optional[dict] = None,
        changed_files: Optional[List[str]] = None,
        db_path: Optional[str] = None,
    ) -> dict:
        """架构对齐验证主入口。

        Args:
            project_path: 目标项目路径。
            target_arch: 目标架构（缺省读取已存储）。
            changed_files: 本次改动文件集合（增量验证时传入；None = 全量）。
            db_path: 知识图谱 db（缺省自动定位）。

        Returns:
            score 各维度 + 总对齐度 + 差距复检。
        """
        db = db_path or locate_kg_db(project_path)
        if not db or not os.path.exists(db):
            return {"ok": False, "project_path": project_path,
                    "message": "知识图谱不存在，需先构建", "score": 0.0,
                    "dimensions": {}, "gaps": []}

        if target_arch is None:
            target_arch = self._load_stored_arch(project_path)
        if target_arch is None:
            return {"ok": False, "project_path": project_path,
                    "message": "缺少目标架构，请先设置或传入", "score": 0.0,
                    "dimensions": {}, "gaps": []}

        # 1. 全量差距（供评分）
        gap_result = analyze_gap(project_path, target_arch, db_path=db)

        nodes, adj = load_graph(db)

        # 2. 子图裁剪（增量验证）：只对改动文件影响的差距复检
        if changed_files:
            focus = self._focus_set(changed_files, nodes)
            gaps = self._filter_gaps_in_focus(gap_result.get("gaps", []), focus)
        else:
            gaps = gap_result.get("gaps", [])

        # 3. 四维评分
        dimensions = self._score_dimensions(project_path, target_arch,
                                            nodes, adj, gap_result, db)
        total = round(
            dimensions["responsibility"]["score"] * WEIGHTS["responsibility"]
            + dimensions["dependency"]["score"] * WEIGHTS["dependency"]
            + dimensions["business"]["score"] * WEIGHTS["business"]
            + dimensions["health"]["score"] * WEIGHTS["health"],
            2,
        )

        return {
            "ok": True,
            "project_path": project_path,
            "tool": "coderef_arch_verify",
            "mode": "incremental" if changed_files else "full",
            "changed_files": changed_files or [],
            "score": total,
            "dimensions": dimensions,
            "gaps": gaps,
            "remaining": {
                "total": len(gaps),
                "high": sum(1 for g in gaps if g["severity"] == "high"),
                "medium": sum(1 for g in gaps if g["severity"] == "medium"),
                "low": sum(1 for g in gaps if g["severity"] == "low"),
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

    def _score_dimensions(self, project_path, target_arch, nodes, adj,
                          gap_result, db) -> dict:
        roles = target_arch.get("tech_roles") or []
        total_mods = sum(1 for n in nodes.values()
                         if n.get("type") == "module"
                         and not _is_test_module(module_of(n, project_path) or n.get("name", "")))
        assigned_ids = set()
        for role in roles:
            assigned_ids |= _match_module_ids(nodes, project_path,
                                              role.get("target_modules", []))
        responsibility = len(assigned_ids) / total_mods if total_mods else 1.0

        # 依赖健康：违例边 / 文件级模块依赖边
        module_edges = self._count_module_edges(nodes, adj, project_path)
        violation_edges = 0
        for g in gap_result.get("gaps", []):
            if g.get("type") == "dependency_violation":
                violation_edges += 1
            elif g.get("type") == "cycle":
                # 环中边近似 = 环长-1（保守）
                violation_edges += max(1, len(g.get("modules", [])) - 1)
        dependency = max(0.0, 1.0 - violation_edges / max(1, module_edges))

        business = self._score_business(target_arch, nodes, project_path)

        health = self._score_health(project_path, db)

        return {
            "responsibility": {
                "score": round(responsibility, 2),
                "weight": WEIGHTS["responsibility"],
                "assigned": len(assigned_ids),
                "total_modules": total_mods,
            },
            "dependency": {
                "score": round(dependency, 2),
                "weight": WEIGHTS["dependency"],
                "violations": violation_edges,
                "module_edges": module_edges,
            },
            "business": {
                "score": round(business["ratio"], 2),
                "weight": WEIGHTS["business"],
                "implemented_roles": business["implemented"],
                "total_roles": business["total"],
            },
            "health": {
                "score": round(health, 2),
                "weight": WEIGHTS["health"],
                "arch_health_raw": None,
            },
        }

    def _count_module_edges(self, nodes: dict, adj: Dict[str, list],
                            project_path: str) -> int:
        """统计文件级模块依赖边（去重 pair，排除测试模块）。"""
        pairs = set()
        for nid, targets in adj.items():
            src_mod = module_of(nodes.get(nid, {}), project_path) or ""
            if not src_mod or _is_test_module(src_mod):
                continue
            for t in targets:
                dst_mod = module_of(nodes.get(t, {}), project_path) or ""
                if not dst_mod or _is_test_module(dst_mod) or dst_mod == src_mod:
                    continue
                pairs.add((src_mod, dst_mod))
        return len(pairs)

    def _score_business(self, target_arch, nodes, project_path) -> dict:
        roles = target_arch.get("tech_roles") or []
        implemented = 0
        for role in roles:
            specs = role.get("target_modules", [])
            if any(self._module_exists(project_path, s, nodes) for s in specs):
                implemented += 1
        total = len(roles)
        return {"implemented": implemented, "total": total,
                "ratio": implemented / total if total else 1.0}

    def _module_exists(self, project_path: str, spec: str, nodes: dict) -> bool:
        from core.arch_gap_analyzer import _module_exists
        return _module_exists(project_path, spec, nodes)

    def _score_health(self, project_path: str, db: str) -> float:
        try:
            from core.arch_audit import audit as arch_audit
            r = arch_audit(project_path, db_path=db)
            health_score = r.get("health_score")
            if isinstance(health_score, (int, float)):
                return min(1.0, health_score / 10.0)
        except Exception:
            pass
        return 0.5  # 保守默认（未计入健康维度）

    def _focus_set(self, changed_files: List[str], nodes: dict) -> set:
        """扩展本次改动文件为"受影响文件闭包"（文件级）。"""
        bases = {os.path.basename(f).replace(".py", "") for f in changed_files if f}
        focus = set(bases)
        for nid, n in nodes.items():
            base = file_base(n)
            b = (os.path.splitext(base)[0] if base else "")
            if b in bases:
                focus.add(base)
        return focus

    def _filter_gaps_in_focus(self, gaps: List[dict], focus: set) -> List[dict]:
        """增量：只保留差距涉及的目标模块在受影响闭包内的差距。"""
        if not focus:
            return gaps
        out = []
        for g in gaps:
            mod = g.get("module") or ""
            base = mod.split("/")[-1] if mod else ""
            match = (base in focus) or (
                g.get("type") == "cycle"
                and any((m.split("/")[-1]) in focus for m in g.get("modules", []))
            )
            if match:
                out.append(g)
        return out