# -*- coding: utf-8 -*-
"""arch_flow — 业务流程画布（v5.18.0；5.21.12 起改为 actor × stage 汇总画布）。

把 `business_semantics` 的标注结果渲染成**确定性**泳道画布：

    X 轴 = 流程阶段（触发 → 准入校验 → 执行 → 落库 → 通知 → 交付 → 未归类）
    Y 轴 = 泳道（用户界面 / 接口层 / 业务执行 / 数据存储 / 支付 / 通知 / 定时 / 基础）
    格子 = 该 actor × stage 下的模块汇总卡（模块数 + 样例 + 交接/交付标记）
    边   = 跨格子的调用聚合；交接点用琥珀色虚线标出

不再把 300 个模块逐个铺进格子（那会退化成「士兵方阵」）；未归类格子也只出一张
汇总卡并显示数量，不假装已经归类。位置完全由标注决定，同一份标注必然渲染同一张图。
"""

import os
from datetime import datetime
from typing import Dict, List

from core.canvas_engine import render_canvas

_CELL_W = 150
_CELL_H = 64
_GAP_X = 18
_GAP_Y = 16
_X0 = 120
_Y0 = 110


def _collect_grid(nodes: List[dict], edges: List[dict],
                  stages: List[dict], actors: List[dict]) -> tuple:
    """按 actor×stage 聚合模块，并返回有内容的泳道/阶段。

    返回 (grid, module_cell, active_actors, active_stages, handoff_modules)。
    """
    handoff_modules = {e.get("from") for e in edges if e.get("handoff")}
    grid: Dict[tuple, List[dict]] = {}
    module_cell: Dict[str, tuple] = {}
    for n in nodes:
        key = (n.get("actor") or "unlabeled", n.get("stage") or "unlabeled")
        grid.setdefault(key, []).append(n)
        module_cell[n.get("module", "")] = key

    active_actors = [a for a in actors
                     if any(grid.get((a["id"], s["id"])) for s in stages)]
    active_stages = [s for s in stages
                     if any(grid.get((a["id"], s["id"])) for a in actors)]
    return grid, module_cell, active_actors, active_stages, handoff_modules


def _cell_nodes(grid: Dict[tuple, List[dict]], active_actors: List[dict],
                active_stages: List[dict], stage_x: Dict[str, int],
                actor_y: Dict[str, int], handoff_modules) -> tuple:
    """每个非空格子生成一张汇总卡；返回 (nodes, cell_id 映射)。"""
    canvas_nodes: List[dict] = []
    cell_id: Dict[tuple, str] = {}
    for a in active_actors:
        for s in active_stages:
            items = grid.get((a["id"], s["id"])) or []
            if not items:
                continue
            nid = f"cell:{a['id']}:{s['id']}"
            cell_id[(a["id"], s["id"])] = nid
            value = sum(1 for i in items if i.get("value_node"))
            handoff = any(i.get("module") in handoff_modules for i in items)
            samples = [i.get("title") or i.get("module", "").split("/")[-1]
                       for i in items[:2]]
            canvas_nodes.append({
                "id": nid, "type": "cell",
                "label": f"{len(items)} 个模块",
                "icon": "✅" if value else ("🔁" if handoff else "📦"),
                "color": "#10B981" if value else ("#F59E0B" if handoff else "#3B82F6"),
                "x": stage_x[s["id"]], "y": actor_y[a["id"]] + 8,
                "w": _CELL_W, "h": _CELL_H,
                "props": {
                    "count": len(items), "samples": samples,
                    "desc": " · ".join(samples),
                    "actor": a["id"], "stage": s["id"],
                },
            })
    return canvas_nodes, cell_id


def _aggregate_edges(edges: List[dict], module_cell: Dict[str, tuple],
                     cell_id: Dict[tuple, str]) -> List[dict]:
    """跨格子调用聚合成一条边；交接点用琥珀色虚线。"""
    agg: Dict[tuple, dict] = {}
    for e in edges:
        a = module_cell.get(e.get("from", ""))
        b = module_cell.get(e.get("to", ""))
        if not a or not b or a == b:
            continue
        f, t = cell_id.get(a), cell_id.get(b)
        if not f or not t:
            continue
        rec = agg.setdefault((f, t), {"count": 0, "handoff": False})
        rec["count"] += 1
        rec["handoff"] = rec["handoff"] or bool(e.get("handoff"))
    return [{
        "id": f"e:{f}->{t}", "from": f, "to": t,
        "label": str(rec["count"]) if rec["count"] > 1 else "",
        "type": "flow", "rel": "",
        "color": "#F59E0B" if rec["handoff"] else "#3B82F6",
        "dashed": bool(rec["handoff"]),
    } for (f, t), rec in agg.items()]


def _build_bands(grid: Dict[tuple, List[dict]], active_actors: List[dict],
                 active_stages: List[dict], stage_x: Dict[str, int],
                 actor_y: Dict[str, int]) -> tuple:
    """泳道带与阶段表头（位置与格子坐标同源）。"""
    lanes = [{
        "id": a["id"], "name": a.get("name", a["id"]),
        "y": actor_y[a["id"]] - 8, "h": _CELL_H + 16,
        "count": sum(len(grid.get((a["id"], s["id"])) or []) for s in active_stages),
    } for a in active_actors]
    stage_bands = [{
        "id": s["id"], "name": s.get("name", s["id"]),
        "x": stage_x[s["id"]], "w": _CELL_W,
        "count": sum(len(grid.get((a["id"], s["id"])) or []) for a in active_actors),
    } for s in active_stages]
    return lanes, stage_bands


def _build_canvas_data(data: dict) -> dict:
    """把 business_semantics 的 307 个模块压成非空格子汇总卡 + 聚合边。"""
    stages = data.get("stages") or []
    actors = data.get("actors") or []
    nodes = data.get("nodes") or []
    edges = data.get("edges") or []
    summary = data.get("summary") or {}

    grid, module_cell, active_actors, active_stages, handoff_modules = _collect_grid(
        nodes, edges, stages, actors)
    stage_x = {s["id"]: _X0 + i * (_CELL_W + _GAP_X)
               for i, s in enumerate(active_stages)}
    actor_y = {a["id"]: _Y0 + j * (_CELL_H + _GAP_Y)
               for j, a in enumerate(active_actors)}

    canvas_nodes, cell_id = _cell_nodes(
        grid, active_actors, active_stages, stage_x, actor_y, handoff_modules)
    canvas_edges = _aggregate_edges(edges, module_cell, cell_id)
    lanes, stage_bands = _build_bands(
        grid, active_actors, active_stages, stage_x, actor_y)

    return {
        "canvas": {"type": "flow", "title": "业务流程画布",
                   "width": _X0 + len(active_stages) * (_CELL_W + _GAP_X) + 120,
                   "height": _Y0 + len(active_actors) * (_CELL_H + _GAP_Y) + 120},
        "nodes": canvas_nodes,
        "edges": canvas_edges,
        "meta": {
            "summary": summary,
            "legend": [
                {"label": "交接点（跨泳道/跨阶段）", "color": "#F59E0B"},
                {"label": "交付点（写库/导出/发布/扣款）", "color": "#10B981"},
                {"label": "普通格子", "color": "#3B82F6"},
            ],
        },
        "lanes": lanes,
        "stage_bands": stage_bands,
    }


def render_html(data: dict, title: str = "业务流程画布") -> str:
    """按 actor × stage 渲染汇总画布（确定性、可平移缩放）。"""
    return render_canvas(_build_canvas_data(data), title=title)


def render_file(project_path: str, data: dict,
                output_dir: str = None, title: str = "业务流程画布") -> str:
    out_dir = output_dir or os.path.join(os.path.abspath(project_path), ".coderef")
    os.makedirs(out_dir, exist_ok=True)
    text = render_html(data, title=title)
    name = f"arch_flow_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}.html"
    fp = os.path.join(out_dir, name)
    with open(fp, "w", encoding="utf-8") as f:
        f.write(text)
    return fp
