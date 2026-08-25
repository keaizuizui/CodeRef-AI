# -*- coding: utf-8 -*-
"""
ArchCanvas — 可视化架构画布生成器（5.4 自由布局版）

把"人定义正轨"从 JSON 编辑提升为可视化拖拽。基于 FreeCanvas 自由布局引擎，
三层布局（业务层 → 技术层 → 代码层），支持：
  - 节点自由拖拽、任意连线成流（端口拖出连线）
  - 画布平移 / 缩放 / 缩略图导航
  - 对齐吸附、右键菜单、快捷键、属性面板
  - 自动布局（分层 / 力导向）
  - 差距高亮：游离模块灰底 / 循环依赖黄框 / 缺失角色红虚线 / 依赖违例红连线
  - 导出 JSON：前端生成目标架构 JSON 并下载/复制，再经 coderef_target_arch_set 落盘

技术选型：纯 HTML/CSS/JS + SVG 自包含，零外部依赖（离线可用）。
数据层完全复用 arch_gap_analyzer + 知识图谱，画布只负责渲染与交互。

用法：
    from core.canvas_generator import ArchCanvas
    path = ArchCanvas().generate(project_path="...", target_arch={...})
"""

import os
import json
from datetime import datetime
from typing import Any, Dict, List, Optional

from loguru import logger

from core.arch_gap_analyzer import (
    _is_test_module,
    _match_module_ids,
    analyze_gap,
)
from core.arch_audit import locate_kg_db, module_of
from core.canvas_engine import auto_layout, render_canvas
from core.graph_closure import load_graph


class ArchCanvas:
    """可视化架构画布生成器（5.4 自由布局版）"""

    def generate(
        self,
        project_path: str,
        target_arch: Optional[Dict[str, Any]] = None,
        output_dir: Optional[str] = None,
        title: str = "架构推回正轨 · 可视化画布",
    ) -> str:
        """生成自包含 HTML 画布。

        Args:
            project_path: 目标项目路径。
            target_arch: 目标架构 JSON（缺省读取已存储的 <project>/.coderef/target_arch.json）。
            output_dir: 输出目录（默认 <project>/.coderef/）。
            title: 画布标题。

        Returns:
            HTML 文件路径。
        """
        # 1. 目标架构（缺省读已存储）
        if target_arch is None:
            target_arch = self._load_stored_arch(project_path)
        if target_arch is None:
            target_arch = {"version": "5.0", "project": os.path.basename(project_path)}

        # 2. 知识图谱 + 差距分析
        db = locate_kg_db(project_path)
        has_kg = bool(db and os.path.exists(db))
        gap_result = None
        if has_kg:
            gap_result = analyze_gap(project_path, target_arch)
        nodes, adj = (load_graph(db) if has_kg else ({}, {}))

        # 3. 组装自由布局画布数据
        data = self._build_free_canvas_data(
            project_path, target_arch, nodes, adj, gap_result, has_kg)

        # 4. 自动布局 + 渲染 HTML
        data = auto_layout(data, mode="layered")
        html_content = render_canvas(data, title=title)

        # 5. 写入文件
        if not output_dir:
            output_dir = os.path.join(project_path, ".coderef")
        os.makedirs(output_dir, exist_ok=True)
        filename = f"arch_canvas_{datetime.now().strftime('%Y%m%d_%H%M%S')}.html"
        filepath = os.path.join(output_dir, filename)
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(html_content)
        logger.info(f"[ArchCanvas] 画布已生成: {filepath} ({len(html_content):,} bytes)")
        return filepath

    # ────────────────────────────────────────────────
    # 数据组装（自由布局画布）
    # ────────────────────────────────────────────────

    def _load_stored_arch(self, project_path: str) -> Optional[dict]:
        path = os.path.join(project_path, ".coderef", "target_arch.json")
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        return None

    def _build_free_canvas_data(
        self,
        project_path: str,
        target_arch: dict,
        nodes: Dict[str, dict],
        adj: Dict[str, List[str]],
        gap_result: Optional[dict],
        has_kg: bool,
    ) -> dict:
        roles = target_arch.get("tech_roles") or []
        flows = target_arch.get("business_flows") or []

        canvas_nodes: List[dict] = []
        canvas_edges: List[dict] = []
        mod_node_id: Dict[str, str] = {}  # 模块名 → 画布节点 id

        # ── 1. 业务步骤节点（业务层）──
        for f in flows:
            for st in f.get("steps", []):
                nid = f"step:{f.get('id', '')}:{st.get('id', '')}"
                canvas_nodes.append({
                    "id": nid, "type": "step",
                    "label": st.get("name", st.get("id", "")),
                    "icon": "📈", "color": "#10B981", "layer": "业务层",
                    "props": {
                        "flow": f.get("name", f.get("id", "")),
                        "tech_roles": st.get("tech_roles", []),
                    },
                })

        # ── 2. 角色节点（技术层）+ 角色→模块归属 ──
        role_modules: Dict[str, List[str]] = {}
        for role in roles:
            rid = role.get("id", "")
            matched = _match_module_ids(nodes, project_path, role.get("target_modules", []))
            mods = sorted(
                module_of(nodes[nid], project_path) or nodes[nid].get("name", "?")
                for nid in matched
            )
            role_modules[rid] = mods
            canvas_nodes.append({
                "id": f"role:{rid}", "type": "role",
                "label": role.get("name", rid),
                "icon": "🏷", "color": "#8B5CF6", "layer": "技术层",
                "props": {"modules": mods, "missing": False},
            })

        # ── 3. 模块节点（代码层）──
        for nid, n in nodes.items():
            if n.get("type") != "module":
                continue
            m = module_of(n, project_path) or n.get("name", "?")
            if _is_test_module(m):
                continue
            mod_node_id[m] = f"mod:{m}"
            canvas_nodes.append({
                "id": f"mod:{m}", "type": "module",
                "label": m, "icon": "🧩", "color": "#3B82F6", "layer": "代码层",
                "props": {"file": n.get("file_path", "")},
            })

        # ── 4. 边 ──
        # 业务步骤 → 角色（mapping）
        for f in flows:
            for st in f.get("steps", []):
                for rid in st.get("tech_roles", []):
                    canvas_edges.append({
                        "id": f"e:{f.get('id', '')}:{st.get('id', '')}:{rid}",
                        "from": f"step:{f.get('id', '')}:{st.get('id', '')}",
                        "to": f"role:{rid}",
                        "label": "", "type": "mapping",
                        "color": "#10B981", "dashed": True,
                    })
        # 角色 → 模块（belong）
        for rid, mods in role_modules.items():
            for m in mods:
                if m in mod_node_id:
                    canvas_edges.append({
                        "id": f"e:role:{rid}:{m}",
                        "from": f"role:{rid}", "to": mod_node_id[m],
                        "label": "", "type": "belong",
                        "color": "#8B5CF6", "dashed": False,
                    })
        # 模块 → 模块（依赖，来自图谱 adj）
        seen_dep: set = set()
        for src, targets in adj.items():
            s_mod = module_of(nodes.get(src, {}), project_path) if src in nodes else ""
            if not s_mod or s_mod not in mod_node_id:
                continue
            for tgt in targets:
                t_mod = module_of(nodes.get(tgt, {}), project_path) if tgt in nodes else ""
                if not t_mod or t_mod not in mod_node_id or t_mod == s_mod:
                    continue
                key = (s_mod, t_mod)
                if key in seen_dep:
                    continue
                seen_dep.add(key)
                canvas_edges.append({
                    "id": f"e:dep:{s_mod}:{t_mod}",
                    "from": mod_node_id[s_mod], "to": mod_node_id[t_mod],
                    "label": "", "type": "dependency",
                    "color": "#94A3B8", "dashed": False,
                })

        # ── 5. 差距高亮 ──
        gaps = (gap_result or {}).get("gaps", [])
        unassigned = set()
        cycle = set()
        missing_roles = set()
        violation_pairs = set()
        for g in gaps:
            t = g.get("type", "")
            if t == "unassigned":
                unassigned.add(g.get("module", ""))
            elif t == "cycle":
                cycle.update(g.get("modules", []))
            elif t == "missing":
                missing_roles.add(g.get("role_id", ""))
            elif t == "dependency_violation":
                violation_pairs.add((g.get("from_module", ""), g.get("to_module", "")))

        for n in canvas_nodes:
            if n["type"] == "module":
                if n["label"] in cycle:
                    n["color"] = "#F59E0B"
                elif n["label"] in unassigned:
                    n["color"] = "#64748B"
            elif n["type"] == "role" and n["id"].startswith("role:"):
                rid = n["id"].split(":", 1)[1]
                if rid in missing_roles:
                    n["props"]["missing"] = True
                    n["color"] = "#EF4444"

        # 依赖违例边 → 红色
        for e in canvas_edges:
            if e["type"] == "dependency":
                fm = e["from"].replace("mod:", "")
                to = e["to"].replace("mod:", "")
                if (fm, to) in violation_pairs:
                    e["color"] = "#EF4444"

        # ── 6. meta ──
        legend = [
            {"color": "#10B981", "label": "业务步骤"},
            {"color": "#8B5CF6", "label": "技术角色"},
            {"color": "#3B82F6", "label": "代码模块"},
            {"color": "#64748B", "label": "游离模块"},
            {"color": "#F59E0B", "label": "循环依赖"},
            {"color": "#EF4444", "label": "缺失/违例"},
        ]
        return {
            "canvas": {
                "type": "arch",
                "title": "架构推回正轨 · 可视化画布",
                "width": 3200,
                "height": 2200,
            },
            "nodes": canvas_nodes,
            "edges": canvas_edges,
            "meta": {
                "summary": (gap_result or {}).get("summary", {}),
                "alignment": (gap_result or {}).get("alignment", {}),
                "legend": legend,
                "has_kg": has_kg,
                "target_arch": target_arch,
            },
        }
