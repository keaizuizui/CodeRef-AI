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
        # ：kind=="phase" 渲染为阶段分组（🎯 附阶段序号徽章），与普通 step(📈) 视觉区分
        session_step_counter: Dict[str, int] = {}
        for f in flows:
            fid = f.get("id", "")
            seq = 0
            for st in f.get("steps", []):
                sid = st.get("id", "")
                kind = st.get("kind", "")
                if kind == "phase":
                    seq += 1
                nid = f"step:{fid}:{sid}"
                canvas_nodes.append({
                    "id": nid, "type": "step",
                    "label": st.get("name", sid),
                    "icon": "🎯" if kind == "phase" else "📈",
                    "color": "#10B981" if kind == "phase" else "#B7E6CD",
                    "layer": "业务层",
                    "props": {
                        "flow": f.get("name", fid),
                        "tech_roles": st.get("tech_roles", []),
                        "kind": kind,
                        "phase_no": seq if kind == "phase" else 0,
                        "branches": st.get("branches", []),
                    },
                })
                session_step_counter[nid] = 1

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

        # ── 3.1 ：阶段成员矩阵强制纳入可视图 + member/branch 边 ──
        # sub_module_refs 引用的成员（子模块/适配器）沿其所属阶段被拉进可视图，
        # 即使图谱无独立模块节点，也补一个"成员占位"节点，消解"适配器命中 0"。
        for f in flows:
            fid = f.get("id", "")
            for st in f.get("steps", []):
                sid = st.get("id", "")
                step_nid = f"step:{fid}:{sid}"
                # 阶段→成员 membership 边（成员命中图谱模块或占位）
                for ref in st.get("sub_module_refs") or []:
                    if "group" in ref:  # 演进槽位：子分组本轮只透传，不展开渲染
                        for it in ref.get("items") or []:
                            self._emit_member_member(project_path, nodes, mod_node_id,
                                                     canvas_nodes, canvas_edges,
                                                     step_nid, fid, sid, it)
                    else:
                        self._emit_member_member(project_path, nodes, mod_node_id,
                                                 canvas_nodes, canvas_edges,
                                                 step_nid, fid, sid, ref)
                # 分支/回环条件边（step→step，虚线）
                for br in st.get("branches") or []:
                    to_sid = br.get("to", "")
                    to_nid = f"step:{fid}:{to_sid}"
                    if not to_sid or to_sid == sid:
                        continue
                    canvas_edges.append({
                        "id": f"e:branch:{fid}:{sid}:{br.get('type','loop')}:{to_sid}",
                        "from": step_nid, "to": to_nid,
                        "label": br.get("condition", ""),
                        "type": "branch", "color": "#F472B6", "dashed": True,
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
        twin_verdict: Dict[str, str] = {}  # 模块名 → 真身/孤本/活跃副本（③）
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
            elif t == "twin_identity":
                for c in g.get("copies", []):
                    twin_verdict[c.get("module", "")] = c.get("verdict", "")

        for n in canvas_nodes:
            if n["type"] == "module":
                if n["label"] in cycle:
                    n["color"] = "#F59E0B"
                elif n["label"] in unassigned:
                    n["color"] = "#64748B"
                # 孪生真身/孤本标注（③）：真身绿、孤本灰、活跃副本橙，独立于差距色不受折叠影响
                v = twin_verdict.get(n["label"])
                if v == "真身":
                    n["color"] = "#22C55E"
                    n["props"]["twin_verdict"] = "真身"
                elif v == "孤本":
                    n["color"] = "#A1A1AA"
                    n["props"]["twin_verdict"] = "孤本"
                elif v == "活跃副本":
                    n["color"] = "#F97316"
                    n["props"]["twin_verdict"] = "活跃副本"
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
            {"color": "#10B981", "label": "业务步骤（🎯=阶段分组）"},
            {"color": "#14B8A6", "label": "阶段成员挂载"},
            {"color": "#F472B6", "label": "分支/回环（条件边）"},
            {"color": "#8B5CF6", "label": "技术角色"},
            {"color": "#3B82F6", "label": "代码模块"},
            {"color": "#64748B", "label": "游离模块"},
            {"color": "#F59E0B", "label": "循环依赖"},
            {"color": "#EF4444", "label": "缺失/违例"},
            {"color": "#22C55E", "label": "孪生真身"},
            {"color": "#A1A1AA", "label": "孪生孤本"},
            {"color": "#F97316", "label": "孪生活跃副本"},
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

    def _emit_member_member(
        self,
        project_path: str,
        nodes: Dict[str, dict],
        mod_node_id: Dict[str, str],
        canvas_nodes: List[dict],
        canvas_edges: List[dict],
        step_nid: str,
        fid: str,
        sid: str,
        ref: dict,
    ) -> None:
        """阶段成员（子模块/适配器）→ 代码层节点 membership 边（）。

        ref = {"module": 相对路径, "role": optional, "alias": optional,
               "kind": "adapter"|"module", "note": optional}
        成员命中图谱模块则以 cite 连接；若 mod_node_id 无该模块（如适配器类
        无独立 module 节点），补一个"成员占位"节点强制纳入可视图，消解命中 0。
        """
        spec = (ref.get("module") or "").strip()
        if not spec:
            return
        # module spec（相对路径/basename）优先精确命中已建 mod 节点
        target_id = None
        for m, mid in mod_node_id.items():
            if m == spec or m.endswith("/" + spec) or spec.endswith("/" + m):
                target_id = mid
                break
        if target_id is None:
            # 图谱无该模块节点（适配器类常如此）→ 补占位节点，强制入视图
            placeholder = f"mod:{spec}"
            if placeholder not in mod_node_id.values():
                canvas_nodes.append({
                    "id": placeholder, "type": "module",
                    "label": ref.get("alias") or spec,
                    "icon": "🧩", "color": "#94A3B8", "layer": "代码层",
                    "props": {"placeholder": True, "desc": ref.get("note", "阶段成员（占位）")},
                })
                mod_node_id.setdefault(spec, placeholder)
            target_id = placeholder
        canvas_edges.append({
            "id": f"e:member:{fid}:{sid}:{spec}",
            "from": step_nid, "to": target_id,
            "label": ref.get("note") or ref.get("alias") or "",
            "type": "member", "color": "#14B8A6", "dashed": True,
        })
