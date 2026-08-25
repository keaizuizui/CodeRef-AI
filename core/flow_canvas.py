# -*- coding: utf-8 -*-
"""
FlowCanvas — 交互式流程画布生成器（5.4）

从代码自动提取业务管线与跨模块数据流，渲染为可自由拖拽的流程图。
基于 FreeCanvas 自由布局引擎，纯 HTML/CSS/JS + SVG 自包含，零外部依赖。

数据源（纯静态、确定性，复用 arch_insight + flow_verify）：
  - pipeline_insight（P0-A）：入口管线（沿 CALLS 归纳阶段序）
  - cross_module_flows（FlowVerifier）：跨模块业务数据流

交互（完整）：节点拖拽 / 任意连线成流 / 平移缩放 / 对齐吸附 / 缩略图 /
右键菜单 / 快捷键 / 属性面板 / 自动布局 / 导出导入 JSON。

用法：
    from core.flow_canvas import FlowCanvas
    path = FlowCanvas().generate(project_path="d:/x/proj")
"""

import os
from datetime import datetime
from typing import Any, Dict, List, Optional

from loguru import logger

from core.arch_insight import pipeline_insight
from core.canvas_engine import auto_layout, render_canvas


class FlowCanvas:
    """交互式流程画布生成器（5.4）"""

    def generate(
        self,
        project_path: str,
        output_dir: Optional[str] = None,
        title: str = "项目流程画布",
        max_entries: int = 6,
        max_depth: int = 6,
    ) -> str:
        """生成自包含 HTML 流程画布。

        Args:
            project_path: 目标项目路径（自动定位知识图谱）。
            output_dir: 输出目录（默认 <project>/.coderef/）。
            title: 画布标题。
            max_entries: 最多归纳的入口管线数。
            max_depth: 调用链搜索深度。

        Returns:
            HTML 文件路径。
        """
        # 1. 管线梳理 + 跨模块数据流
        pipeline = pipeline_insight(project_path, max_entries=max_entries,
                                    max_depth=max_depth)
        flows = pipeline.get("flows") or []

        # 2. 组装自由布局画布数据
        data = self._build_free_canvas_data(pipeline, flows)

        # 3. 自动布局 + 渲染
        data = auto_layout(data, mode="layered")
        html_content = render_canvas(data, title=title)

        # 4. 写入文件
        if not output_dir:
            output_dir = os.path.join(project_path, ".coderef")
        os.makedirs(output_dir, exist_ok=True)
        filename = f"flow_canvas_{datetime.now().strftime('%Y%m%d_%H%M%S')}.html"
        filepath = os.path.join(output_dir, filename)
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(html_content)
        logger.info(f"[FlowCanvas] 流程画布已生成: {filepath} ({len(html_content):,} bytes)")
        return filepath

    # ────────────────────────────────────────────────
    # 数据组装（自由布局画布）
    # ────────────────────────────────────────────────

    def _build_free_canvas_data(self, pipeline: dict, flows: List[dict]) -> dict:
        canvas_nodes: List[dict] = []
        canvas_edges: List[dict] = []
        entries = pipeline.get("entries") or []

        # ── 1. 入口管线（每条管线一个 layer，步骤水平排列）──
        for ei, entry in enumerate(entries):
            steps = entry.get("steps", [])
            layer = f"管线 {ei + 1} · {entry.get('entry', '')}"
            for si, s in enumerate(steps):
                nid = f"pipe:{ei}:{si}"
                canvas_nodes.append({
                    "id": nid, "type": "step",
                    "label": s.get("name", ""),
                    "icon": "🚀" if si == 0 else "⚙",
                    "color": "#3B82F6", "layer": layer,
                    "props": {
                        "file": s.get("file", ""),
                        "line": s.get("line", 0),
                        "doc": (s.get("doc") or "").strip()[:120],
                    },
                })
                if si > 0:
                    canvas_edges.append({
                        "id": f"pe:{ei}:{si}",
                        "from": f"pipe:{ei}:{si - 1}", "to": nid,
                        "label": "", "type": "flow",
                        "color": "#3B82F6", "dashed": False,
                    })

        # ── 2. 跨模块业务数据流（模块节点 + 数据流边）──
        mod_ids: Dict[str, str] = {}
        for fi, f in enumerate(flows):
            src, tgt = f.get("source", ""), f.get("target", "")
            if not src or not tgt:
                continue
            for m in (src, tgt):
                if m not in mod_ids:
                    mod_ids[m] = f"mod:{m}"
                    canvas_nodes.append({
                        "id": mod_ids[m], "type": "module",
                        "label": m, "icon": "🧩", "color": "#10B981",
                        "layer": "跨模块数据流",
                        "props": {},
                    })
            canvas_edges.append({
                "id": f"fe:{fi}",
                "from": mod_ids[src], "to": mod_ids[tgt],
                "label": str(f.get("count", "")),
                "type": "dataflow",
                "color": "#10B981", "dashed": False,
            })

        # ── 3. meta ──
        legend = [
            {"color": "#3B82F6", "label": "管线步骤"},
            {"color": "#10B981", "label": "跨模块数据流"},
        ]
        return {
            "canvas": {
                "type": "flow",
                "title": "项目流程画布",
                "width": 3200,
                "height": 2200,
            },
            "nodes": canvas_nodes,
            "edges": canvas_edges,
            "meta": {
                "summary": {
                    "total": len(entries),
                    "high": 0, "medium": 0, "low": 0,
                },
                "legend": legend,
                "pipeline_ok": pipeline.get("ok", False),
            },
        }
