# -*- coding: utf-8 -*-
"""
FreeCanvas — 自由布局画布引擎（5.4）

自研轻量实现：纯 HTML/CSS/JS + SVG 自包含，零外部依赖（离线可用）。
交互能力（节点拖拽/任意连线/平移缩放/缩略图/自动布局等）为业界自由/流程画布
工具的通用交互范式，本实现为独立编写的自有代码，不复制或翻译任何第三方实现。

核心交互（完整）：
  - 节点自由拖拽（绝对定位 + 网格/节点对齐吸附）
  - 画布平移（空白拖拽）/ 缩放（滚轮 + 按钮 + 快捷键）
  - 节点间任意连线成流（端口拖出连线，自动选端口）
  - 缩略图导航（mini-map，点击/拖拽跳转视口）
  - 右键菜单（添加/删除/复制节点、连线样式、自动布局、导出 JSON）
  - 快捷键（Ctrl+Z 撤销 / Ctrl+Shift+Z 重做 / Delete 删除 / Ctrl+A 全选 / 方向键微调）
  - 属性面板（点击节点/边编辑 label、颜色、props）
  - 自动布局（分层 / 力导向）
  - 导出/导入 JSON（画布状态持久化）

数据结构（canvas_data）：
    {
      "canvas": {"type": "arch|flow", "title": "...", "width": 3000, "height": 2000},
      "nodes": [
        {"id": "n1", "type": "module", "label": "...", "x": 100, "y": 200,
         "w": 180, "h": 60, "color": "#3B82F6", "icon": "🧩",
         "props": {"file": "...", "desc": "..."}}
      ],
      "edges": [
        {"id": "e1", "from": "n1", "to": "n2", "fromPort": "right", "toPort": "left",
         "label": "...", "type": "call", "color": "#94A3B8", "dashed": false}
      ],
      "meta": {"summary": {...}, "legend": [...]}
    }

用法：
    from core.canvas_engine import render_canvas, auto_layout
    html = render_canvas(canvas_data, title="架构画布")
"""

import json
import html
from collections import defaultdict, deque
from typing import Any, Dict, List


# 默认画布尺寸（世界坐标）
DEFAULT_WIDTH = 3200
DEFAULT_HEIGHT = 2200

# 节点默认尺寸
_DEF_W = 180
_DEF_H = 60

# 布局兜底（：单层/超宽时按 DAG 深度子行二维展开 + 适应最小可辨尺寸）
# 每行节点数上限：避免单层节点横排成一条超宽线
MAX_NODES_PER_ROW = 12
# 每行总宽上限（占画布可用宽）
MAX_LAYER_WIDTH = DEFAULT_WIDTH - 160
# 「适应」缩放后节点最小可辨像素下限
MIN_NODE_READABLE_PX = 24
# ：三层泳道标题区高度（泳道顶部预留标题渲染空间）
_LANE_TITLE_H = 44


# ═══════════════════════════════════════════════════════════════════
# 数据规范化
# ═══════════════════════════════════════════════════════════════════

def _norm_node(n: Dict[str, Any]) -> Dict[str, Any]:
    """补齐节点默认字段。"""
    return {
        "id": str(n.get("id", "")),
        "type": n.get("type", "node"),
        "label": str(n.get("label", n.get("id", ""))),
        "x": float(n.get("x", 0)),
        "y": float(n.get("y", 0)),
        "w": float(n.get("w", _DEF_W)),
        "h": float(n.get("h", _DEF_H)),
        "color": n.get("color", "#3B82F6"),
        "icon": n.get("icon", ""),
        "layer": n.get("layer", n.get("type", "default")),
        "tier": n.get("tier", ""),          #  泳道：business|service|code；空=不区分层
        "props": n.get("props") or {},
        "ports": n.get("ports") or ["top", "right", "bottom", "left"],
    }


def _norm_edge(e: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id": str(e.get("id", "")),
        "from": str(e.get("from", "")),
        "to": str(e.get("to", "")),
        "fromPort": e.get("fromPort", ""),
        "toPort": e.get("toPort", ""),
        "label": e.get("label", ""),
        "type": e.get("type", "edge"),
        "rel": e.get("rel", ""),            #  边类型：flow|align|land|depends
        "color": e.get("color", "#94A3B8"),
        "dashed": bool(e.get("dashed", False)),
    }


def normalize(canvas_data: Dict[str, Any]) -> Dict[str, Any]:
    """规范化画布数据，补齐默认字段。"""
    canvas = dict(canvas_data.get("canvas") or {})
    canvas.setdefault("type", "arch")
    canvas.setdefault("title", "画布")
    canvas.setdefault("width", DEFAULT_WIDTH)
    canvas.setdefault("height", DEFAULT_HEIGHT)
    nodes = [_norm_node(n) for n in (canvas_data.get("nodes") or [])]
    edges = [_norm_edge(e) for e in (canvas_data.get("edges") or [])]
    return {
        "canvas": canvas,
        "nodes": nodes,
        "edges": edges,
        "meta": canvas_data.get("meta") or {},
        "tier_order": canvas_data.get("tier_order") or None,
    }


# ═══════════════════════════════════════════════════════════════════
# 自动布局（Python 端生成初始位置；JS 端另有"重新布局"按钮）
# ═══════════════════════════════════════════════════════════════════

def auto_layout(canvas_data: Dict[str, Any], mode: str = "layered") -> Dict[str, Any]:
    """自动布局：layered（分层）/ force（力导向）。就地修改节点坐标。

    仅对未显式指定坐标的节点生效（x/y 均为 0 视为未布局），
    避免覆盖调用方已排好的位置。
    """
    data = normalize(canvas_data)
    nodes = data["nodes"]
    edges = data["edges"]
    if not any(n["x"] == 0 and n["y"] == 0 for n in nodes):
        return data
    if mode == "force":
        _layout_force(nodes, edges)
    else:
        _layout_layered(nodes, edges)
    return data


def _is_unpositioned(n: Dict) -> bool:
    """是否未显式定位（x/y 均为 0 视为未布局）。"""
    return n["x"] == 0 and n["y"] == 0


def _node_depths(node_ids: List[str], edges: List[Dict]) -> Dict[str, int]:
    """按依赖 DAG 的最长链深度给节点编号层（Kahn 拓扑）。 兜底。"""
    indeg = {i: 0 for i in node_ids}
    succ = defaultdict(list)
    for e in edges:
        f, t = e.get("from"), e.get("to")
        if f in indeg and t in indeg and f != t:
            succ[f].append(t)
            indeg[t] += 1
    depth: Dict[str, int] = {}
    queue = deque([i for i in node_ids if indeg[i] == 0])
    for i in queue:
        depth[i] = 0
    while queue:
        u = queue.popleft()
        for v in succ[u]:
            depth[v] = max(depth.get(v, 0), depth[u] + 1)
            indeg[v] -= 1
            if indeg[v] == 0:
                queue.append(v)
    for i in node_ids:
        depth.setdefault(i, 0)  # 环内/孤立节点：归入浅层
    return depth


_NODE_GAP_DEFAULT = 28


def _split_layer_rows(items: List[Dict], edges: List[Dict],
                      topo_always: bool = False) -> List[List[Dict]]:
    """把某层节点拆成多行：先按拓扑深度浅→深排，再用行宽/行数上限截断。

    解决 ：全部节点归入同一层时，避免横排成一条超宽线；深度相近的
    依赖方优先同行，深一层自动换行，让 Y 维展开、首屏可读。
    topo_always=True（ 泳道）：即使节点少也按拓扑深度排序，保证泳道内
    业务 flow 链路（如 进门→点餐→吃饭→结账）从左到右可读，而不是回退按
    名字排序把链路打乱。
    """
    ids = [n["id"] for n in items]
    use_depth = topo_always or len(ids) > MAX_NODES_PER_ROW
    depth = _node_depths(ids, edges) if use_depth else {}
    ordered = sorted(items, key=lambda n: (depth.get(n["id"], 0), n.get("label", n["id"])))
    rows: List[List[Dict]] = []
    cur: List[Dict] = []
    for n in ordered:
        nw = n.get("w", _DEF_W)
        if cur and (len(cur) >= MAX_NODES_PER_ROW
                    or sum(x.get("w", _DEF_W) for x in cur) + _NODE_GAP_DEFAULT * (len(cur) - 1)
                    + nw + _NODE_GAP_DEFAULT > MAX_LAYER_WIDTH):
            rows.append(cur)
            cur = []
        cur.append(n)
    if cur:
        rows.append(cur)
    return rows


def _tier_order(nodes: List[Dict]) -> List[str]:
    """：tier 泳道顺序（business→service→code 优先，其余按出现顺序延后）。"""
    fixed = ("business", "service", "code")
    uniq: List[str] = []
    for n in nodes:
        t = n.get("tier") or ""
        if t and t not in uniq:
            uniq.append(t)
    return [t for t in fixed if t in uniq] + [t for t in uniq if t not in fixed]


def _layout_lanes(nodes: List[Dict], edges: List[Dict],
                  lane_gap: float = 200, node_gap: float = 28,
                  margin: float = 80) -> None:
    """：按 tier 分组为横向泳道（business 上 → service 中 → code 下）。

    泳道内横向多行排节点（沿用 _split_layer_rows），泳道顶部预留标题区
    （_LANE_TITLE_H），泳道间留大间距容纳泳道标题与跨层 align/land trace 边。
    仅布局未定位节点（x=0 且 y=0）。
    """
    by_tier: Dict[str, List[Dict]] = {}
    for n in nodes:
        if n["x"] != 0 or n["y"] != 0:
            continue
        by_tier.setdefault(n.get("tier") or "default", []).append(n)
    y = margin
    for t in _tier_order(nodes):
        items = by_tier.get(t)
        if not items:
            continue
        # 只取本 tier 内部边算拓扑深度，避免跨层 align/land 边污染泳道内排列，
        # 保证业务 flow 链路从左到右可读。
        ids_set = {n["id"] for n in items}
        interior = [e for e in edges if e.get("from") in ids_set and e.get("to") in ids_set]
        rows = _split_layer_rows(items, interior, topo_always=True)
        for row in rows:
            total_w = sum(n.get("w", _DEF_W) for n in row) + node_gap * (len(row) - 1)
            x = margin + max(0, (DEFAULT_WIDTH - 2 * margin - total_w) / 2)
            row_h = max((n.get("h", _DEF_H) for n in row), default=_DEF_H)
            for n in row:
                n["x"] = x
                n["y"] = y + _LANE_TITLE_H
                x += n.get("w", _DEF_W) + node_gap
            y += row_h + node_gap
        y += (lane_gap - node_gap) + _LANE_TITLE_H


def _layout_layered(nodes: List[Dict], edges: List[Dict],
                    layer_gap: float = 150, node_gap: float = 28,
                    margin: float = 80) -> None:
    """分层布局：按 tier 泳道（）或 layer 分组，主层间垂直排列，层内再按深度子行展开。

    仅给未定位节点（x=0 且 y=0）赋坐标；已显式定位的节点保留原位。
    单层内节点过多或过宽时，按 DAG 深度拆成多行（），避免 Y 轴坍缩成线。
    """
    if any(n.get("tier") for n in nodes):
        _layout_lanes(nodes, edges, node_gap=node_gap, margin=margin)
        return
    order: List[str] = []
    layers: Dict[str, List[Dict]] = {}
    for n in nodes:
        l = n.get("layer", "default")
        if l not in order:
            order.append(l)
        layers.setdefault(l, []).append(n)

    y = margin
    for l in order:
        items = layers[l]
        unpositioned = [n for n in items if n["x"] == 0 and n["y"] == 0]
        if not unpositioned:
            continue
        placed_bottom = margin
        for n in items:
            if n["x"] != 0 or n["y"] != 0:
                placed_bottom = max(placed_bottom, n["y"] + n.get("h", _DEF_H))
        y = max(y, placed_bottom + layer_gap)

        rows = _split_layer_rows(unpositioned, edges)
        for row in rows:
            total_w = sum(n.get("w", _DEF_W) for n in row) + node_gap * (len(row) - 1)
            x = margin + max(0, (DEFAULT_WIDTH - 2 * margin - total_w) / 2)
            row_h = max((n.get("h", _DEF_H) for n in row), default=_DEF_H)
            for n in row:
                n["x"] = x
                n["y"] = y
                x += n.get("w", _DEF_W) + node_gap
            y += row_h + layer_gap


def _layout_force(nodes: List[Dict], edges: List[Dict],
                  iterations: int = 150, w: float = DEFAULT_WIDTH,
                  h: float = DEFAULT_HEIGHT) -> None:
    """简单力导向布局：斥力（所有节点对）+ 弹簧力（边）。

    已显式定位的节点作为固定锚点，不参与移动；仅布局未定位节点（x=0 且 y=0）。
    """
    import random
    rng = random.Random(42)
    movable = [n for n in nodes if _is_unpositioned(n)]
    fixed = {id(n) for n in nodes if not _is_unpositioned(n)}
    if not movable:
        return
    for n in movable:
        n["x"] = rng.uniform(100, w - 100)
        n["y"] = rng.uniform(100, h - 100)

    for _ in range(iterations):
        # 斥力（固定锚点只推挤可动节点，自身不移位）
        for i, a in enumerate(nodes):
            if id(a) in fixed:
                continue
            for j, b in enumerate(nodes):
                if i == j:
                    continue
                dx = b["x"] - a["x"]
                dy = b["y"] - a["y"]
                d2 = max(dx * dx + dy * dy, 1e-4)
                d = d2 ** 0.5
                f = 12000 / d2
                a["x"] -= f * dx / d * 0.5
                a["y"] -= f * dy / d * 0.5
        # 弹簧力（只移动可动端点）
        for e in edges:
            a = next((n for n in nodes if n["id"] == e["from"]), None)
            b = next((n for n in nodes if n["id"] == e["to"]), None)
            if not a or not b:
                continue
            dx = b["x"] - a["x"]
            dy = b["y"] - a["y"]
            d = max((dx * dx + dy * dy) ** 0.5, 1e-4)
            f = 0.02 * (d - 200)
            fx, fy = f * dx / d, f * dy / d
            if id(a) not in fixed:
                a["x"] += fx * 0.5
                a["y"] += fy * 0.5
            if id(b) not in fixed:
                b["x"] -= fx * 0.5
                b["y"] -= fy * 0.5
        # 边界约束
        for n in movable:
            n["x"] = max(20, min(w - 20, n["x"]))
            n["y"] = max(20, min(h - 20, n["y"]))


# ═══════════════════════════════════════════════════════════════════
# 渲染入口
# ═══════════════════════════════════════════════════════════════════

def render_canvas(canvas_data: Dict[str, Any], title: str = "") -> str:
    """渲染自由布局画布 HTML（自包含单文件）。

    Args:
        canvas_data: 画布数据（nodes/edges/canvas/meta）。
        title: 覆盖画布标题（缺省用 canvas_data.canvas.title）。

    Returns:
        完整 HTML 字符串。
    """
    data = normalize(canvas_data)
    if not title:
        title = data["canvas"].get("title", "画布")
    data_json = json.dumps(data, ensure_ascii=False)
    safe_json = (
        data_json.replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("&", "\\u0026")
        .replace("\u2028", "\\u2028")
        .replace("\u2029", "\\u2029")
    )
    return _HTML_TEMPLATE.replace("__TITLE__", html.escape(title)).replace(
        "__DATA_JSON__", safe_json)


_HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>__TITLE__</title>
<style>
* { margin:0; padding:0; box-sizing:border-box; }
body { font-family:"Segoe UI","Noto Sans CJK SC",sans-serif; background:#0F172A; color:#E2E8F0; height:100vh; overflow:hidden; }
#toolbar { position:fixed; top:0; left:0; right:0; z-index:200; background:rgba(15,23,42,.97); backdrop-filter:blur(10px); padding:8px 14px; display:flex; align-items:center; gap:8px; border-bottom:1px solid #1E293B; flex-wrap:wrap; }
#toolbar h1 { font-size:14px; font-weight:600; white-space:nowrap; }
#toolbar .stats { font-size:11px; color:#94A3B8; }
#toolbar .spacer { flex:1; }
#toolbar button { background:#1E293B; color:#E2E8F0; border:1px solid #334155; padding:5px 10px; border-radius:6px; cursor:pointer; font-size:11px; transition:all .15s; }
#toolbar button:hover { background:#334155; border-color:#475569; }
#toolbar button.primary { background:#3B82F6; border-color:#3B82F6; }
#toolbar button.primary:hover { background:#2563EB; }
#toolbar button.active { background:#3B82F6; border-color:#3B82F6; }
#toolbar .sep { width:1px; height:20px; background:#334155; }
#canvasWrap { position:fixed; top:44px; left:0; right:0; bottom:0; overflow:hidden; cursor:grab; background:#0F172A; }
#canvasWrap.panning { cursor:grabbing; }
#viewport { position:absolute; top:0; left:0; transform-origin:0 0; }
#edgeLayer { position:absolute; top:0; left:0; width:0; height:0; overflow:visible; pointer-events:none; }
#edgeLayer path { pointer-events:stroke; cursor:pointer; }
#edgeLayer path:hover { stroke-width:4 !important; }
#edgeLayer path.hl { stroke:#FBBF24 !important; stroke-width:4 !important; }
#edgeLayer .edge-label { font-size:11px; fill:#94A3B8; pointer-events:none; }
#nodeLayer { position:absolute; top:0; left:0; width:0; height:0; }
#laneLayer { position:absolute; top:0; left:0; width:0; height:0; pointer-events:none; }
#laneLayer svg { overflow:visible; }
#laneLayer rect, #laneLayer text { pointer-events:none; user-select:none; }
.node.tier-business { border-color:#3B82F6; }
.node.tier-service { border-color:#F59E0B; }
.node.tier-code { border-color:#10B981; }
#nodeLayer.lod-state .node-label, #nodeLayer.lod-state .node-sub { display:none; }
.node { position:absolute; background:#1E293B; border:2px solid #334155; border-radius:10px; display:flex; flex-direction:column; align-items:center; justify-content:center; cursor:move; user-select:none; transition:box-shadow .15s; }
.node:hover { box-shadow:0 0 0 1px rgba(59,130,246,.4); }
.node.selected { box-shadow:0 0 0 2px #3B82F6; }
.node .node-icon { font-size:18px; line-height:1; }
.node .node-label { font-size:13px; color:#E2E8F0; text-align:center; padding:2px 6px; max-width:100%; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; cursor:help; }
.node .node-sub { font-size:11px; color:#94A3B8; max-width:90%; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.node .port { position:absolute; width:10px; height:10px; border-radius:50%; background:#0F172A; border:2px solid #64748B; opacity:0; transition:opacity .15s; cursor:crosshair; }
.node:hover .port { opacity:1; }
.node .port:hover { background:#3B82F6; border-color:#3B82F6; }
.node .port-top { top:-6px; left:50%; transform:translateX(-50%); }
.node .port-right { right:-6px; top:50%; transform:translateY(-50%); }
.node .port-bottom { bottom:-6px; left:50%; transform:translateX(-50%); }
.node .port-left { left:-6px; top:50%; transform:translateY(-50%); }
#miniMap { position:fixed; right:14px; bottom:14px; width:200px; height:140px; background:rgba(15,23,42,.92); border:1px solid #1E293B; border-radius:8px; z-index:150; overflow:hidden; }
#miniMap .mm-title { font-size:9px; color:#64748B; padding:3px 6px; }
#miniMap canvas { display:block; width:100%; height:calc(100% - 18px); cursor:pointer; }
#contextMenu { position:fixed; z-index:300; background:#1E293B; border:1px solid #334155; border-radius:8px; padding:4px; min-width:160px; display:none; box-shadow:0 8px 24px rgba(0,0,0,.4); }
#contextMenu .cm-item { padding:6px 12px; font-size:12px; cursor:pointer; border-radius:5px; }
#contextMenu .cm-item:hover { background:#334155; }
#contextMenu .cm-item.danger { color:#EF4444; }
#contextMenu .cm-sep { height:1px; background:#334155; margin:4px 6px; }
#propPanel { position:fixed; top:44px; right:0; bottom:0; width:300px; background:rgba(15,23,42,.97); border-left:1px solid #1E293B; padding:14px; overflow-y:auto; transform:translateX(100%); transition:transform .25s; z-index:180; }
#propPanel.open { transform:translateX(0); }
#propPanel h3 { font-size:13px; margin-bottom:10px; color:#60A5FA; }
#propPanel .field { margin-bottom:10px; }
#propPanel label { display:block; font-size:11px; color:#94A3B8; margin-bottom:3px; }
#propPanel input, #propPanel textarea, #propPanel select { width:100%; background:#0F172A; color:#E2E8F0; border:1px solid #334155; border-radius:6px; padding:5px 8px; font-size:12px; }
#propPanel textarea { min-height:60px; resize:vertical; font-family:monospace; }
#propPanel .props-json { font-family:monospace; font-size:10px; }
#propPanel .btn-row { display:flex; gap:6px; margin-top:6px; }
#propPanel button { flex:1; background:#1E293B; color:#E2E8F0; border:1px solid #334155; padding:6px; border-radius:6px; cursor:pointer; font-size:11px; }
#propPanel button:hover { background:#334155; }
#propPanel button.primary { background:#3B82F6; border-color:#3B82F6; }
#propPanel button.danger { background:#7F1D1D; border-color:#EF4444; color:#FCA5A5; }
#toast { position:fixed; bottom:20px; left:50%; transform:translateX(-50%); background:#1E293B; border:1px solid #334155; padding:8px 16px; border-radius:8px; font-size:12px; opacity:0; transition:opacity .3s; z-index:400; pointer-events:none; }
#toast.show { opacity:1; }
#tempEdge { position:absolute; pointer-events:none; }
.hint { position:fixed; bottom:14px; left:14px; z-index:150; font-size:10px; color:#475569; background:rgba(15,23,42,.8); padding:6px 10px; border-radius:6px; border:1px solid #1E293B; }
/* ═══ 决策者报告视图（arch 画布默认首屏，浅色）═══ */
#reportView { position:fixed; inset:0; background:#F8FAFC; color:#0F172A; overflow-y:auto; z-index:500; display:none; }
#reportView.show { display:block; }
.rp-wrap { max-width:1080px; margin:0 auto; padding:30px 24px 60px; font-family:-apple-system,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif; }
.rp-head { display:flex; justify-content:space-between; align-items:flex-start; flex-wrap:wrap; gap:12px; }
.rp-title h1 { font-size:26px; font-weight:700; }
.rp-title .sub { font-size:13px; color:#64748B; margin-top:4px; }
.rp-btn { background:#1E293B; color:#fff; border:none; padding:10px 18px; border-radius:8px; cursor:pointer; font-size:14px; white-space:nowrap; }
.rp-btn:hover { background:#334155; }
.rp-kpis { display:grid; grid-template-columns:repeat(auto-fit,minmax(150px,1fr)); gap:14px; margin:22px 0 26px; }
.rp-kpi { background:#fff; border:1px solid #E2E8F0; border-radius:12px; padding:16px 18px; box-shadow:0 1px 3px rgba(15,23,42,.06); }
.rp-kpi .lbl { font-size:13px; color:#64748B; }
.rp-kpi .val { font-size:30px; font-weight:700; margin-top:2px; }
.rp-kpi .val.ok { color:#059669; }
.rp-kpi .val.warn { color:#D97706; }
.rp-kpi .val.bad { color:#DC2626; }
.rp-kpi .sub { font-size:12px; color:#94A3B8; margin-top:2px; }
.rp-sec { background:#fff; border:1px solid #E2E8F0; border-radius:14px; padding:22px 24px; margin-bottom:20px; box-shadow:0 1px 3px rgba(15,23,42,.06); }
.rp-sec > h2 { font-size:18px; font-weight:700; margin-bottom:6px; }
.rp-sec > .desc { font-size:13px; color:#64748B; margin-bottom:16px; }
.rp-flow { margin-bottom:16px; }
.rp-flow h3 { font-size:15px; color:#1E293B; margin-bottom:10px; }
.rp-steps { display:flex; flex-wrap:wrap; align-items:center; gap:6px; row-gap:10px; }
.rp-step { background:#ECFDF5; border:1px solid #A7F3D0; color:#065F46; border-radius:8px; padding:8px 12px; font-size:14px; font-weight:500; }
.rp-step.phase { background:#DCFCE7; border-color:#86EFAC; font-weight:700; }
.rp-arrow { color:#94A3B8; font-size:16px; }
.rp-role { border-left:4px solid #8B5CF6; border-radius:8px; padding:12px 16px; margin-bottom:12px; background:#FAFAFC; }
.rp-role h3 { font-size:15px; margin-bottom:8px; display:flex; align-items:center; gap:8px; flex-wrap:wrap; }
.rp-role .cnt { background:#EEF2FF; color:#4338CA; border-radius:999px; padding:1px 10px; font-size:12px; font-weight:600; }
.rp-role .miss { background:#FEF2F2; color:#B91C1C; border-radius:999px; padding:1px 10px; font-size:12px; font-weight:600; }
.rp-mods { display:flex; flex-wrap:wrap; gap:6px; }
.rp-mod { background:#EFF6FF; border:1px solid #BFDBFE; color:#1D4ED8; border-radius:6px; padding:4px 10px; font-size:13px; }
.rp-mod.free { background:#F1F5F9; border-color:#CBD5E1; color:#475569; }
.rp-gapgrp { margin-bottom:18px; }
.rp-gapgrp > h3 { font-size:15px; margin-bottom:10px; }
.rp-gap { border:1px solid #E2E8F0; border-radius:10px; padding:12px 14px; margin-bottom:10px; display:flex; gap:10px; align-items:flex-start; background:#fff; }
.rp-gap .sev { flex-shrink:0; border-radius:6px; padding:3px 10px; font-size:12px; font-weight:700; color:#fff; margin-top:2px; }
.rp-gap .sev.high { background:#DC2626; }
.rp-gap .sev.medium { background:#D97706; }
.rp-gap .sev.low { background:#2563EB; }
.rp-gap .txt { font-size:14px; line-height:1.6; color:#1E293B; }
.rp-ok { background:#ECFDF5; border:1px solid #A7F3D0; color:#065F46; border-radius:10px; padding:16px; font-size:15px; }
.rp-empty { color:#64748B; font-style:italic; font-size:14px; }
.rp-foot { text-align:center; color:#94A3B8; font-size:12px; padding:18px 0; }
</style>
</head>
<body>
<div id="reportView"></div>
<div id="toolbar">
  <h1>🗺 __TITLE__</h1>
  <span class="stats" id="stats"></span>
  <span class="spacer"></span>
  <span id="reportGroup" style="display:none">
    <button onclick="showReport()" title="返回决策者报告总览（默认首屏）">📋 决策者报告</button>
  </span>
  <span class="sep" id="reportSep" style="display:none"></span>
  <button onclick="zoomBy(1.2)" title="放大 (Ctrl++)">➕ 放大</button>
  <button onclick="zoomBy(1/1.2)" title="缩小 (Ctrl+-)">➖ 缩小</button>
  <button onclick="fitView()" title="适应画布">📐 适应</button>
  <button onclick="resetView()" title="重置视图">🔄 重置</button>
  <span class="sep"></span>
  <button onclick="layoutLayered()" title="分层自动布局">📊 分层布局</button>
  <button onclick="layoutForce()" title="力导向自动布局">🕸 力导向</button>
  <span class="sep" id="navSep" style="display:none"></span>
  <span id="navGroup" style="display:none">
    <button onclick="setNav('L0')" id="navL0" title="L0 架构总览：先看这是什么业务/有哪些角色（先整体后局部）">🏠 L0 总览</button>
    <button onclick="setNav('L1')" id="navL1" title="L1 模块盘点：按层聚焦（业务/技术/代码）">🗂 L1 分层</button>
    <button onclick="setNav('L2')" id="navL2" title="L2 模块内逻辑：双击模块节点下钻其关联子图">🔍 L2 模块</button>
    <button onclick="setNav('L3')" id="navL3" title="L3 代码管线：只看代码层模块与依赖">🧩 L3 代码</button>
    <button onclick="setNav('all')" id="navAll" title="全量平铺（默认视图）">🌐 全量</button>
  </span>
  <span id="breadcrumb" style="display:none; font-size:11px; color:#94A3B8;"></span>
  <span class="sep" id="gapSep" style="display:none"></span>
  <button id="gapToggle" style="display:none" onclick="toggleGaps()" title="差距高亮开关：默认折叠，捋清 L0-L3 后再叠加">⚠ 差距:开</button>
  <span class="sep"></span>
  <button onclick="undo()" title="撤销 (Ctrl+Z)">↩ 撤销</button>
  <button onclick="redo()" title="重做 (Ctrl+Shift+Z)">↪ 重做</button>
  <span class="sep"></span>
  <button onclick="downloadJSON()" title="导出画布 JSON">⬇ 导出</button>
  <button onclick="document.getElementById('fileInput').click()" title="导入画布 JSON">⬆ 导入</button>
  <input type="file" id="fileInput" accept=".json" style="display:none" onchange="importJSON(event)">
</div>
<div id="canvasWrap">
  <div id="viewport">
    <svg id="laneLayer"></svg>
    <svg id="edgeLayer"></svg>
    <div id="nodeLayer"></div>
  </div>
</div>
<div id="miniMap"><div class="mm-title">导航</div><canvas id="mmCanvas"></canvas></div>
<div id="contextMenu"></div>
<div id="propPanel"></div>
<div id="toast"></div>
<div class="hint">拖拽节点移动 · 端口连线成流 · 空白拖拽平移 · 滚轮缩放 · 右键菜单 · Ctrl+Z 撤销</div>
<script>
const DATA = __DATA_JSON__;
const CANVAS = DATA.canvas || {};
let nodes = (DATA.nodes||[]).map(n => Object.assign({}, n));
let edges = (DATA.edges||[]).map(e => Object.assign({}, e));
let view = {x: 0, y: 0, scale: 1};
let selected = new Set();
let history = [];
let redoStack = [];
let drag = null;   // {mode:'node'|'pan'|'connect'|'marquee', ...}
let connectFrom = null;  // {nodeId, port}
let ctxTarget = null;    // 右键菜单目标
let propTarget = null;   // 属性面板目标 {kind:'node'|'edge', id}
// ：L0→L3 逐层下钻导航状态
let navLevel = 'all';    // 'all'|'L0'|'L1'|'L2'|'L3'
let navLayer = 'all';    // L1 分层过滤：'all'|'业务层'|'技术层'|'代码层'
let navFocus = null;     // L2 聚焦模块 id
let showGaps = true;     // 差距高亮开关（先捋清后治理）
const NAV_LAYERS = ['业务层', '技术层', '代码层'];
const GAP_NODE_COLORS = ['#64748B', '#F59E0B', '#EF4444'];  // 游离/循环/缺失角色
const GAP_EDGE_COLORS = ['#EF4444'];                        // 依赖违例

const wrap = document.getElementById('canvasWrap');
const viewport = document.getElementById('viewport');
const laneLayer = document.getElementById('laneLayer');
const nodeLayer = document.getElementById('nodeLayer');
const edgeLayer = document.getElementById('edgeLayer');
const mmCanvas = document.getElementById('mmCanvas');
const mmCtx = mmCanvas.getContext('2d');

function esc(s){ return String(s==null?'':s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;'); }

// ═══════════ 视口变换 ═══════════
function toLocal(cx, cy){
  const r = wrap.getBoundingClientRect();
  return {x: cx - r.left, y: cy - r.top};
}
function toWorld(cx, cy){
  const p = toLocal(cx, cy);
  return {x:(p.x - view.x)/view.scale, y:(p.y - view.y)/view.scale};
}
function applyView(){
  viewport.style.transform = `translate(${view.x}px, ${view.y}px) scale(${view.scale})`;
  // ：LOD 标签分级——缩放过小时隐藏文字只留色块/图标，避免马赛克不可读
  nodeLayer.classList.toggle('lod-state', view.scale < 0.30);
  renderMiniMap();
}
function zoomAt(cx, cy, factor){
  const p = toLocal(cx, cy);
  const w = toWorld(cx, cy);
  view.scale = Math.max(0.2, Math.min(3, view.scale * factor));
  view.x = p.x - w.x * view.scale;
  view.y = p.y - w.y * view.scale;
  applyView();
}
function zoomBy(factor){
  const r = wrap.getBoundingClientRect();
  zoomAt(r.left + wrap.clientWidth/2, r.top + wrap.clientHeight/2, factor);
}
function fitView(){
  if (!nodes.length) return;
  // ：导航激活时按当前可见节点计算边界，避免隐藏节点把聚焦视图缩得过小
  const vis = navLevel === 'all' ? nodes : nodes.filter(navVisible);
  if (!vis.length) return;
  let minX=Infinity, minY=Infinity, maxX=-Infinity, maxY=-Infinity;
  vis.forEach(n => { minX=Math.min(minX,n.x); minY=Math.min(minY,n.y); maxX=Math.max(maxX,n.x+n.w); maxY=Math.max(maxY,n.y+n.h); });
  const pad = 80;
  minX-=pad; minY-=pad; maxX+=pad; maxY+=pad;
  const w = maxX-minX, h = maxY-minY;
  view.scale = Math.min(wrap.clientWidth/w, wrap.clientHeight/h, 1.5);
  // ：适应后节点最小可辨尺寸下限，防止超大图把节点缩成不可辨
  const maxNW = Math.max(...vis.map(n=>n.w));
  const minScale = MIN_READABLE_PX / Math.max(maxNW, 1);
  view.scale = Math.max(view.scale, minScale);
  view.x = (wrap.clientWidth - w*view.scale)/2 - minX*view.scale;
  view.y = (wrap.clientHeight - h*view.scale)/2 - minY*view.scale;
  applyView();
}
function resetView(){ view = {x:0, y:0, scale:1}; applyView(); }

// ═══════════ 渲染 ═══════════
function portPos(n, port){
  const x=n.x, y=n.y, w=n.w, h=n.h;
  switch(port){
    case 'top': return {x:x+w/2, y:y};
    case 'right': return {x:x+w, y:y+h/2};
    case 'bottom': return {x:x+w/2, y:y+h};
    default: return {x:x, y:y+h/2};
  }
}
function autoPort(from, to){
  const cx1 = from.x+from.w/2, cy1 = from.y+from.h/2;
  const cx2 = to.x+to.w/2, cy2 = to.y+to.h/2;
  const dx = cx2-cx1, dy = cy2-cy1;
  const fp = Math.abs(dx)>Math.abs(dy) ? (dx>0?'right':'left') : (dy>0?'bottom':'top');
  const tp = Math.abs(dx)>Math.abs(dy) ? (dx>0?'left':'right') : (dy>0?'top':'bottom');
  return {fromPort:fp, toPort:tp};
}
function bezierPath(p1, p2){
  const dx = Math.abs(p2.x-p1.x), dy = Math.abs(p2.y-p1.y);
  const c = Math.max(40, Math.min(dx, dy)*0.5);
  if (dx > dy){
    const s1 = p2.x>p1.x ? c : -c;
    return `M ${p1.x} ${p1.y} C ${p1.x+s1} ${p1.y}, ${p2.x-s1} ${p2.y}, ${p2.x} ${p2.y}`;
  } else {
    const s1 = p2.y>p1.y ? c : -c;
    return `M ${p1.x} ${p1.y} C ${p1.x} ${p1.y+s1}, ${p2.x} ${p2.y-s1}, ${p2.x} ${p2.y}`;
  }
}
function renderNodes(){
  nodeLayer.innerHTML = '';
  nodes.forEach(n => {
    if (!navVisible(n)) return;
    const el = document.createElement('div');
    el.className = 'node' + (selected.has(n.id) ? ' selected' : '') + (n.tier ? ' tier-'+n.tier : '');
    el.style.left = n.x+'px'; el.style.top = n.y+'px';
    el.style.width = n.w+'px'; el.style.height = n.h+'px';
    el.style.borderColor = nodeColor(n);
    el.dataset.id = n.id;
    el.addEventListener('dblclick', ev => {
      ev.stopPropagation();
      if (navLevel === 'L2') drillFocus(n.id);
      else if (navLevel === 'L1') setLayer(n.layer);
    });
    // 悬停高亮相邻连线（含上下游路径），回答"这个模块连到哪"
    el.addEventListener('mouseenter', () => highlightEdges(n.id, true));
    el.addEventListener('mouseleave', () => highlightEdges(n.id, false));
    const p = n.props || {};
    const sub = p.twin_verdict || p.file || p.desc || '';
    el.innerHTML = `<div class="node-icon">${esc(n.icon||'')}</div>
      <div class="node-label" title="${esc(n.label)}">${esc(n.label)}</div>
      ${sub ? `<div class="node-sub" title="${esc(sub)}">${esc(sub)}</div>` : ''}`;
    (n.ports||['top','right','bottom','left']).forEach(p => {
      const port = document.createElement('div');
      port.className = 'port port-'+p;
      port.dataset.port = p;
      el.appendChild(port);
    });
    nodeLayer.appendChild(el);
  });
}
function renderEdges(){
  edgeLayer.innerHTML = '';
  // 箭头 marker
  const defs = document.createElementNS('http://www.w3.org/2000/svg','defs');
  const marker = document.createElementNS('http://www.w3.org/2000/svg','marker');
  marker.setAttribute('id','arrow');
  marker.setAttribute('markerWidth','8'); marker.setAttribute('markerHeight','8');
  marker.setAttribute('refX','7'); marker.setAttribute('refY','4');
  marker.setAttribute('orient','auto');
  const poly = document.createElementNS('http://www.w3.org/2000/svg','polygon');
  poly.setAttribute('points','0 0, 8 4, 0 8');
  poly.setAttribute('fill','#94A3B8');
  marker.appendChild(poly); defs.appendChild(marker);
  edgeLayer.appendChild(defs);
  const byId = {};
  nodes.forEach(n => byId[n.id] = n);
  edges.forEach(e => {
    const from = byId[e.from], to = byId[e.to];
    if (!from || !to || !navVisible(from) || !navVisible(to)) return;
    const p1 = portPos(from, e.fromPort || autoPort(from,to).fromPort);
    const p2 = portPos(to, e.toPort || autoPort(from,to).toPort);
    const path = document.createElementNS('http://www.w3.org/2000/svg','path');
    path.setAttribute('d', bezierPath(p1,p2));
    // ：边 rel 配色/线型——flow 同层流程 / align 业务→技术 / land 技术→代码 / depends 代码内部
    let stroke = edgeColor(e);
    let dash = e.dashed;
    if (e.rel==='align'){ stroke = '#F59E0B'; dash = true; }
    else if (e.rel==='land'){ stroke = '#10B981'; dash = true; }
    else if (e.rel==='flow'){ stroke = '#3B82F6'; }
    else if (e.rel==='depends'){ stroke = '#94A3B8'; }
    path.setAttribute('stroke', stroke);
    path.setAttribute('stroke-width', e.selected ? 3.5 : 2.5);
    path.setAttribute('fill','none');
    if (dash) path.setAttribute('stroke-dasharray', e.rel==='align'?'4 4':(e.rel==='land'?'2 4':'6 4'));
    path.setAttribute('marker-end','url(#arrow)');
    path.setAttribute('data-id', e.id);
    path.addEventListener('click', ev => { ev.stopPropagation(); selectEdge(e.id); });
    edgeLayer.appendChild(path);
    if (e.label){
      const mid = {x:(p1.x+p2.x)/2, y:(p1.y+p2.y)/2};
      const t = document.createElementNS('http://www.w3.org/2000/svg','text');
      t.setAttribute('x', mid.x); t.setAttribute('y', mid.y-4);
      t.setAttribute('text-anchor','middle');
      t.setAttribute('class','edge-label');
      t.textContent = e.label;
      edgeLayer.appendChild(t);
    }
  });
}
function tierOrder(){
  const fixed = ['business','service','code'];
  const uniq = [];
  nodes.forEach(n => { const t = n.tier||''; if (t && !uniq.includes(t)) uniq.push(t); });
  return fixed.filter(t => uniq.includes(t)).concat(uniq.filter(t => !fixed.includes(t)));
}
// 悬停节点时高亮其全部相邻连线（决策者/编辑者都能看清"谁连谁"）
function highlightEdges(nodeId, on){
  edgeLayer.querySelectorAll('path[data-id]').forEach(p => {
    const e = edges.find(x => x.id === p.getAttribute('data-id'));
    if (e && (e.from === nodeId || e.to === nodeId)) p.classList.toggle('hl', on);
  });
}
function tierTitle(t){
  return ({'business':'业务层 · 业务/用户旅程','service':'技术层 · 服务/技术角色','code':'代码层 · 落地构件'})[t] || t;
}
function renderLanes(){
  laneLayer.innerHTML = '';
  if (!nodes.some(n => n.tier)) return;
  const vis = nodes.filter(navVisible);
  if (!vis.length) return;
  const svgns = 'http://www.w3.org/2000/svg';
  const ns = document.createElementNS(svgns,'svg');
  ns.setAttribute('width','20000'); ns.setAttribute('height','20000');
  ns.style.position = 'absolute'; ns.style.left = '0'; ns.style.top = '0'; ns.style.overflow = 'visible';
  const xs = vis.map(n => n.x), xe = vis.map(n => n.x + n.w);
  const minX = Math.min.apply(null, xs), maxX = Math.max.apply(null, xe);
  const laneFill = {'business':'rgba(59,130,246,.08)','service':'rgba(245,158,11,.07)','code':'rgba(16,185,129,.07)'};
  const laneStroke = {'business':'#1D4ED8','service':'#B45309','code':'#047857'};
  tierOrder().forEach(t => {
    const items = nodes.filter(n => n.tier === t && navVisible(n));
    if (!items.length) return;
    const top = Math.min.apply(null, items.map(n => n.y)) - 26;
    const bottom = Math.max.apply(null, items.map(n => n.y + n.h)) + 16;
    const rect = document.createElementNS(svgns,'rect');
    rect.setAttribute('x', minX-80); rect.setAttribute('y', top);
    rect.setAttribute('width', maxX-minX+160); rect.setAttribute('height', bottom-top);
    rect.setAttribute('rx', 10);
    rect.setAttribute('fill', laneFill[t] || 'rgba(100,116,139,.06)');
    rect.setAttribute('stroke', laneStroke[t] || '#334155');
    rect.setAttribute('stroke-width', '1'); rect.setAttribute('stroke-dasharray','4 3');
    ns.appendChild(rect);
    const tt = document.createElementNS(svgns,'text');
    tt.setAttribute('x', minX-68); tt.setAttribute('y', top+18);
    tt.setAttribute('fill','#CBD5E1'); tt.setAttribute('font-size','13'); tt.setAttribute('font-weight','600');
    tt.textContent = tierTitle(t);
    ns.appendChild(tt);
  });
  laneLayer.appendChild(ns);
}
function renderStats(){
  // ：统计随层级导航联动——按当前 navVisible 过滤后的可见节点/连线计数
  const vis = navLevel === 'all' ? nodes : nodes.filter(navVisible);
  const visIds = new Set(vis.map(n => n.id));
  const visEdges = edges.filter(e => visIds.has(e.from) && visIds.has(e.to));
  const s = (DATA.meta && DATA.meta.summary) || {};
  let html = `节点 ${vis.length} | 连线 ${visEdges.length}`;
  if (navLevel === 'all'){
    // 全量视图保留整体差距摘要（含高中低分档）
    if (s.total != null) html += ` | 差距 ${s.total} (高${s.high||0}/中${s.medium||0}/低${s.low||0})`;
  } else {
    // 层级视图按当前可视范围内的差距节点计数，随视图切换更新
    const gapN = vis.filter(n => GAP_NODE_COLORS.includes(n.color)).length;
    html += ` | 差距 ${gapN}`;
  }
  document.getElementById('stats').innerHTML = html;
}

// ═══════════ ：L0→L3 逐层下钻导航 ═══════════
function hasTierLayers(){
  return nodes.some(n => NAV_LAYERS.includes(n.layer));
}
function initNav(){
  // 可重置：导入/撤销重做替换画布数据后重新配置导航（ 复审）
  navLevel = 'all'; navLayer = 'all'; navFocus = null;
  const on = hasTierLayers();
  ['navSep','navGroup','gapSep','gapToggle'].forEach(id => {
    const el = document.getElementById(id);
    if (el) el.style.display = on ? '' : 'none';
  });
  // arch 画布提供"决策者报告"入口（报告视图为默认首屏，可来回切换）
  const isArch = (CANVAS.type === 'arch');
  ['reportSep','reportGroup'].forEach(id => {
    const el = document.getElementById(id);
    if (el) el.style.display = isArch ? '' : 'none';
  });
  ['navL0','navL1','navL2','navL3','navAll'].forEach(id => {
    const el = document.getElementById(id);
    if (el) el.classList.toggle('active', id === 'navAll');
  });
  renderBreadcrumb();
}
function navVisible(n){
  if (navLevel === 'all') return true;
  if (navLevel === 'L0') return n.type === 'step' || n.type === 'role';   // 业务定位 + 角色总览
  if (navLevel === 'L1') return navLayer === 'all' || n.layer === navLayer;
  if (navLevel === 'L2'){
    if (navFocus){
      if (n.id === navFocus) return true;
      return edges.some(e => (e.from === navFocus && e.to === n.id) || (e.to === navFocus && e.from === n.id));
    }
    return n.type === 'module';   // 未聚焦：先看全部模块
  }
  if (navLevel === 'L3') return n.layer === '代码层' || n.tier === 'code';   // 代码管线：只看代码层模块与依赖
  return true;
}
function setNav(lv){
  navLevel = lv;
  navLayer = 'all';
  navFocus = null;
  selected.clear();   // 导航过滤变化：清除隐藏节点的选中，避免 Delete 误删
  ['navL0','navL1','navL2','navL3','navAll'].forEach(id => {
    document.getElementById(id).classList.toggle('active', id === 'nav' + lv.toUpperCase() || (lv === 'all' && id === 'navAll'));
  });
  renderBreadcrumb();
  render();
  if (lv !== 'all') fitView();
}
function drillFocus(id){
  if (navLevel !== 'L2') return;
  const n = nodes.find(x => x.id === id);
  if (!n) return;
  navFocus = id;
  selected.clear();   // 下钻切换聚焦模块：清除旧选中
  renderBreadcrumb();
  render();
  fitView();
}
function setLayer(layer){
  if (navLevel !== 'L1') return;
  navLayer = NAV_LAYERS.includes(layer) ? layer : 'all';
  selected.clear();   // 分层过滤变化：清除隐藏节点的选中
  renderBreadcrumb();
  render();
  fitView();
}
function navBack(){
  selected.clear();   // 回退改变可见范围：清除隐藏节点的选中
  if (navFocus){ navFocus = null; renderBreadcrumb(); render(); fitView(); return; }
  if (navLayer !== 'all'){ navLayer = 'all'; renderBreadcrumb(); render(); fitView(); return; }
  setNav('all');
}
function renderBreadcrumb(){
  const bc = document.getElementById('breadcrumb');
  if (navLevel === 'all'){ bc.style.display = 'none'; bc.innerHTML = ''; return; }
  bc.style.display = '';
  let s = '📍 ';
  if (navLevel === 'L0') s += 'L0 总览';
  else if (navLevel === 'L1') s += 'L1 分层' + (navLayer !== 'all' ? ' › ' + navLayer : '');
  else if (navLevel === 'L2') {
    const focused = nodes.find(x => x.id === navFocus);
    s += 'L2 模块' + (navFocus ? ' › ' + esc(focused ? focused.label : '') : '');
  }
  else s += 'L3 代码';
  if (navLevel !== 'all') s += ' <a href="javascript:navBack()" style="color:#60A5FA">[回退]</a>';
  bc.innerHTML = s;
}
function toggleGaps(){
  showGaps = !showGaps;
  document.getElementById('gapToggle').textContent = showGaps ? '⚠ 差距:开' : '⚠ 差距:关';
  render();
}
function nodeColor(n){
  if (!showGaps && GAP_NODE_COLORS.includes(n.color)) return '#3B82F6';   // 差距折叠：还原默认蓝
  return n.color || '#334155';
}
function edgeColor(e){
  if (!showGaps && GAP_EDGE_COLORS.includes(e.color)) return '#94A3B8';   // 差距折叠：还原默认灰
  return e.color || '#94A3B8';
}
function render(){
  renderNodes(); renderEdges(); renderLanes(); renderStats(); renderMiniMap();
}
function renderMiniMap(){
  if (!nodes.length) return;
  const cw = mmCanvas.clientWidth, ch = mmCanvas.clientHeight;
  mmCanvas.width = cw; mmCanvas.height = ch;
  mmCtx.clearRect(0,0,cw,ch);
  let minX=Infinity, minY=Infinity, maxX=-Infinity, maxY=-Infinity;
  nodes.forEach(n => { minX=Math.min(minX,n.x); minY=Math.min(minY,n.y); maxX=Math.max(maxX,n.x+n.w); maxY=Math.max(maxY,n.y+n.h); });
  const pad = 60;
  minX-=pad; minY-=pad; maxX+=pad; maxY+=pad;
  const sc = Math.min(cw/(maxX-minX), ch/(maxY-minY));
  const ox = (cw - (maxX-minX)*sc)/2 - minX*sc;
  const oy = (ch - (maxY-minY)*sc)/2 - minY*sc;
  nodes.forEach(n => {
    mmCtx.fillStyle = nodeColor(n);
    mmCtx.fillRect(n.x*sc+ox, n.y*sc+oy, Math.max(2,n.w*sc), Math.max(2,n.h*sc));
  });
  // 视口矩形
  const vx = -view.x/view.scale, vy = -view.y/view.scale;
  const vw = wrap.clientWidth/view.scale, vh = wrap.clientHeight/view.scale;
  mmCtx.strokeStyle = '#3B82F6'; mmCtx.lineWidth = 1.5;
  mmCtx.strokeRect(vx*sc+ox, vy*sc+oy, vw*sc, vh*sc);
  mmCanvas._transform = {sc, ox, oy};
}
mmCanvas.addEventListener('click', e => {
  const t = mmCanvas._transform; if (!t) return;
  const r = mmCanvas.getBoundingClientRect();
  const wx = (e.clientX - r.left - t.ox)/t.sc;
  const wy = (e.clientY - r.top - t.oy)/t.sc;
  view.x = wrap.clientWidth/2 - wx*view.scale;
  view.y = wrap.clientHeight/2 - wy*view.scale;
  applyView();
});

// ═══════════ 拖拽交互 ═══════════
wrap.addEventListener('mousedown', e => {
  if (e.button === 2) return;  // 右键交给 contextmenu
  const portEl = e.target.closest('.port');
  const nodeEl = e.target.closest('.node');
  if (portEl){
    const n = nodes.find(x => x.id === nodeEl.dataset.id);
    if (!n) return;
    connectFrom = {nodeId: n.id, port: portEl.dataset.port, node: n};
    drag = {mode:'connect', startX:e.clientX, startY:e.clientY};
    e.preventDefault();
    return;
  }
  if (nodeEl){
    const id = nodeEl.dataset.id;
    if (!e.shiftKey && !selected.has(id)){
      selected.clear();
    }
    selected.add(id);
    recordPre();
    renderNodes();
    drag = {mode:'node', id, startX:e.clientX, startY:e.clientY,
            orig: nodes.find(n => n.id===id) ? {x:nodes.find(n=>n.id===id).x, y:nodes.find(n=>n.id===id).y} : {x:0,y:0}};
    e.preventDefault();
    return;
  }
  // 空白：平移
  drag = {mode:'pan', startX:e.clientX, startY:e.clientY, vx:view.x, vy:view.y};
  wrap.classList.add('panning');
  if (!e.shiftKey) { selected.clear(); renderNodes(); }
});
window.addEventListener('mousemove', e => {
  if (!drag) return;
  const dx = e.clientX - drag.startX, dy = e.clientY - drag.startY;
  if (drag.mode === 'pan'){
    view.x = drag.vx + dx; view.y = drag.vy + dy;
    applyView();
  } else if (drag.mode === 'node'){
    const n = nodes.find(x => x.id === drag.id);
    if (!n) return;
    const nx = drag.orig.x + dx/view.scale, ny = drag.orig.y + dy/view.scale;
    const s = snapPos(nx, ny, n.w, n.h, drag.id);
    n.x = s.x; n.y = s.y;
    renderNodes(); renderEdges(); renderMiniMap();
  } else if (drag.mode === 'connect'){
    drawTempEdge(e.clientX, e.clientY);
  }
});
window.addEventListener('mouseup', e => {
  if (!drag) return;
  if (drag.mode === 'connect'){
    const target = document.elementFromPoint(e.clientX, e.clientY);
    const nodeEl = target ? target.closest('.node') : null;
    if (nodeEl && connectFrom){
      const toNode = nodes.find(x => x.id === nodeEl.dataset.id);
      if (toNode && toNode.id !== connectFrom.nodeId){
        const from = connectFrom.node;
        const ap = autoPort(from, toNode);
        const eid = 'e' + Date.now();
        recordPre();
        edges.push({id:eid, from:connectFrom.nodeId, to:toNode.id,
                    fromPort: connectFrom.port || ap.fromPort, toPort: ap.toPort,
                    label:'', type:'edge', color:'#94A3B8', dashed:false});
        commitChange();
        toast(`已连线 ${from.label} → ${toNode.label}`);
      }
    }
    clearTempEdge();
    connectFrom = null;
  } else if (drag.mode === 'node'){
    const n = nodes.find(x => x.id === drag.id);
    if (n && (n.x !== drag.orig.x || n.y !== drag.orig.y)) commitChange();
    else pendingPre = null;
  }
  drag = null;
  wrap.classList.remove('panning');
  render();
});
wrap.addEventListener('wheel', e => {
  e.preventDefault();
  const factor = e.deltaY < 0 ? 1.1 : 1/1.1;
  zoomAt(e.clientX, e.clientY, factor);
}, {passive:false});

// 对齐吸附
function snapPos(x, y, w, h, selfId){
  const grid = 20;
  let sx = Math.round(x/grid)*grid, sy = Math.round(y/grid)*grid;
  // 节点边缘对齐（左/右/上/下）
  const edges = [];
  nodes.forEach(n => {
    if (n.id === selfId) return;
    edges.push({v:n.x, type:'left'}, {v:n.x+n.w, type:'right'}, {v:n.y, type:'top'}, {v:n.y+n.h, type:'bottom'});
  });
  const candX = [x, x+w];
  const candY = [y, y+h];
  let bestX = null, bestY = null;
  edges.forEach(ed => {
    candX.forEach(cx => {
      const d = Math.abs(cx - ed.v);
      if (d < 8 && (bestX === null || d < bestX.d)) bestX = {d, dx: ed.v - cx};
    });
    candY.forEach(cy => {
      const d = Math.abs(cy - ed.v);
      if (d < 8 && (bestY === null || d < bestY.d)) bestY = {d, dy: ed.v - cy};
    });
  });
  if (bestX) sx += bestX.dx;
  if (bestY) sy += bestY.dy;
  return {x:sx, y:sy};
}

// 临时连线
function drawTempEdge(cx, cy){
  let el = document.getElementById('tempEdge');
  if (!el){
    el = document.createElementNS('http://www.w3.org/2000/svg','path');
    el.id = 'tempEdge';
    el.setAttribute('stroke','#3B82F6'); el.setAttribute('stroke-width','2');
    el.setAttribute('stroke-dasharray','6 4'); el.setAttribute('fill','none');
    edgeLayer.appendChild(el);
  }
  const from = connectFrom.node;
  const p1 = portPos(from, connectFrom.port);
  const w = toWorld(cx, cy);
  el.setAttribute('d', bezierPath(p1, w));
}
function clearTempEdge(){
  const el = document.getElementById('tempEdge');
  if (el) el.remove();
}

// ═══════════ 选择 / 属性面板 ═══════════
function selectEdge(id){
  edges.forEach(e => e.selected = (e.id === id));
  nodes.forEach(n => n.selected = false);
  selected.clear();
  openProps('edge', id);
  render();
}
function openProps(kind, id){
  propTarget = {kind, id};
  const panel = document.getElementById('propPanel');
  if (kind === 'node'){
    const n = nodes.find(x => x.id === id);
    if (!n) return;
    panel.innerHTML = `<h3>🧩 节点属性</h3>
      <div class="field"><label>ID</label><input value="${esc(n.id)}" disabled></div>
      <div class="field"><label>标签</label><input id="ppLabel" value="${esc(n.label)}"></div>
      <div class="field"><label>颜色</label><input id="ppColor" type="color" value="${esc(n.color||'#3B82F6')}" style="height:30px;padding:2px"></div>
      <div class="field"><label>图标</label><input id="ppIcon" value="${esc(n.icon||'')}"></div>
      <div class="field"><label>类型</label><input id="ppType" value="${esc(n.type)}"></div>
      <div class="field"><label>属性 JSON</label><textarea id="ppProps" class="props-json">${esc(JSON.stringify(n.props||{}, null, 2))}</textarea></div>
      <div class="btn-row">
        <button class="primary" onclick="saveProps()">保存</button>
        <button class="danger" onclick="deleteSelected()">删除</button>
        <button onclick="closeProps()">关闭</button>
      </div>`;
  } else {
    const e = edges.find(x => x.id === id);
    if (!e) return;
    panel.innerHTML = `<h3>🔗 连线属性</h3>
      <div class="field"><label>ID</label><input value="${esc(e.id)}" disabled></div>
      <div class="field"><label>标签</label><input id="ppLabel" value="${esc(e.label||'')}"></div>
      <div class="field"><label>颜色</label><input id="ppColor" type="color" value="${esc(e.color||'#94A3B8')}" style="height:30px;padding:2px"></div>
      <div class="field"><label>样式</label><select id="ppDashed">
        <option value="0" ${e.dashed?'':'selected'}>实线</option>
        <option value="1" ${e.dashed?'selected':''}>虚线</option>
      </select></div>
      <div class="btn-row">
        <button class="primary" onclick="saveProps()">保存</button>
        <button class="danger" onclick="deleteSelected()">删除</button>
        <button onclick="closeProps()">关闭</button>
      </div>`;
  }
  panel.classList.add('open');
}
function saveProps(){
  if (!propTarget) return;
  const label = document.getElementById('ppLabel').value;
  const color = document.getElementById('ppColor').value;
  recordPre();
  if (propTarget.kind === 'node'){
    const n = nodes.find(x => x.id === propTarget.id);
    if (!n) return;
    n.label = label; n.color = color;
    const icon = document.getElementById('ppIcon');
    if (icon) n.icon = icon.value;
    const type = document.getElementById('ppType');
    if (type) n.type = type.value;
    const props = document.getElementById('ppProps');
    if (props){
      try { n.props = JSON.parse(props.value); } catch(e){ toast('属性 JSON 解析失败'); return; }
    }
  } else {
    const e = edges.find(x => x.id === propTarget.id);
    if (!e) return;
    e.label = label; e.color = color;
    const dashed = document.getElementById('ppDashed');
    if (dashed) e.dashed = dashed.value === '1';
  }
  commitChange();
  render();
  toast('已保存');
}
function closeProps(){ document.getElementById('propPanel').classList.remove('open'); propTarget = null; }
function deleteSelected(){
  if (!propTarget) return;
  const {kind, id} = propTarget;
  recordPre();
  if (kind === 'node'){
    nodes = nodes.filter(n => n.id !== id);
    edges = edges.filter(e => e.from !== id && e.to !== id);
  } else {
    edges = edges.filter(e => e.id !== id);
  }
  selected.clear();
  commitChange();
  closeProps();
  render();
  toast('已删除');
}

// ═══════════ 右键菜单 ═══════════
wrap.addEventListener('contextmenu', e => {
  e.preventDefault();
  const nodeEl = e.target.closest('.node');
  const edgeEl = e.target.closest('path[data-id]');
  const w = toWorld(e.clientX, e.clientY);
  const menu = document.getElementById('contextMenu');
  let html = '';
  if (nodeEl){
    const id = nodeEl.dataset.id;
    ctxTarget = {kind:'node', id, wx:w.x, wy:w.y};
    html = `<div class="cm-item" onclick="menuEdit()">✏️ 编辑属性</div>
      <div class="cm-item" onclick="menuDuplicate()">📋 复制节点</div>
      <div class="cm-sep"></div>
      <div class="cm-item danger" onclick="menuDelete()">🗑 删除节点</div>`;
  } else if (edgeEl){
    const id = edgeEl.getAttribute('data-id');
    ctxTarget = {kind:'edge', id};
    html = `<div class="cm-item" onclick="menuEdit()">✏️ 编辑连线</div>
      <div class="cm-sep"></div>
      <div class="cm-item danger" onclick="menuDelete()">🗑 删除连线</div>`;
  } else {
    ctxTarget = {kind:'canvas', wx:w.x, wy:w.y};
    html = `<div class="cm-item" onclick="menuAddNode()">➕ 添加节点</div>
      <div class="cm-sep"></div>
      <div class="cm-item" onclick="layoutLayered()">📊 分层布局</div>
      <div class="cm-item" onclick="layoutForce()">🕸 力导向布局</div>
      <div class="cm-sep"></div>
      <div class="cm-item" onclick="downloadJSON()">⬇ 导出 JSON</div>`;
  }
  menu.innerHTML = html;
  menu.style.display = 'block';
  menu.style.left = Math.min(e.clientX, window.innerWidth - 180) + 'px';
  menu.style.top = Math.min(e.clientY, window.innerHeight - 200) + 'px';
});
document.addEventListener('click', () => {
  document.getElementById('contextMenu').style.display = 'none';
});
function menuEdit(){
  if (!ctxTarget) return;
  if (ctxTarget.kind === 'canvas') return;
  openProps(ctxTarget.kind, ctxTarget.id);
}
function menuDelete(){
  if (!ctxTarget) return;
  if (ctxTarget.kind === 'canvas') return;
  propTarget = ctxTarget;
  deleteSelected();
}
function menuDuplicate(){
  if (!ctxTarget || ctxTarget.kind !== 'node') return;
  const src = nodes.find(n => n.id === ctxTarget.id);
  if (!src) return;
  const nid = 'n' + Date.now();
  recordPre();
  nodes.push({id:nid, type:src.type, label:src.label + ' (副本)', x:src.x+30, y:src.y+30,
              w:src.w, h:src.h, color:src.color, icon:src.icon, layer:src.layer,
              props:Object.assign({}, src.props), ports:src.ports.slice()});
  commitChange();
  render();
  toast('已复制节点');
}
function menuAddNode(){
  if (!ctxTarget || ctxTarget.kind !== 'canvas') return;
  const nid = 'n' + Date.now();
  recordPre();
  nodes.push({id:nid, type:'node', label:'新节点', x:ctxTarget.wx, y:ctxTarget.wy,
              w:180, h:60, color:'#3B82F6', icon:'📌', layer:'default', props:{}, ports:['top','right','bottom','left']});
  commitChange();
  render();
  openProps('node', nid);
}

// ═══════════ 快捷键 ═══════════
document.addEventListener('keydown', e => {
  const tag = (e.target.tagName||'').toLowerCase();
  if (tag === 'input' || tag === 'textarea') return;
  if (e.ctrlKey && e.key.toLowerCase() === 'z'){
    e.preventDefault();
    if (e.shiftKey) redo(); else undo();
  } else if (e.ctrlKey && e.key.toLowerCase() === 'y'){
    e.preventDefault(); redo();
  } else if (e.ctrlKey && e.key.toLowerCase() === 'a'){
    e.preventDefault();
    selected = new Set(nodes.map(n => n.id));
    renderNodes();
  } else if (e.key === 'Delete' || e.key === 'Backspace'){
    if (selected.size){
      const ids = new Set(selected);
      recordPre();
      nodes = nodes.filter(n => !ids.has(n.id));
      edges = edges.filter(e => !ids.has(e.from) && !ids.has(e.to));
      selected.clear();
      commitChange();
      render();
      toast('已删除选中');
    }
  } else if (e.key.startsWith('Arrow')){
    if (!selected.size) return;
    e.preventDefault();
    const dx = e.key === 'ArrowLeft' ? -10 : e.key === 'ArrowRight' ? 10 : 0;
    const dy = e.key === 'ArrowUp' ? -10 : e.key === 'ArrowDown' ? 10 : 0;
    recordPre();
    nodes.forEach(n => { if (selected.has(n.id)){ n.x += dx; n.y += dy; } });
    commitChange();
    render();
  } else if (e.ctrlKey && (e.key === '+' || e.key === '=')){
    e.preventDefault(); zoomBy(1.2);
  } else if (e.ctrlKey && e.key === '-'){
    e.preventDefault(); zoomBy(1/1.2);
  }
});

// ═══════════ 撤销 / 重做 ═══════════
function snapshot(){ return JSON.stringify({nodes, edges}); }
// recordPre(): 在 mutation 之前调用，记录"修改前"快照（只记一次）。
// commitChange(): mutation 完成后调用，把记录的 pre 快照压入历史栈。
let pendingPre = null;
function recordPre(){ if (pendingPre === null) pendingPre = snapshot(); }
function commitChange(){
  if (pendingPre === null) return;
  history.push(pendingPre);
  pendingPre = null;
  if (history.length > 100) history.shift();
  redoStack = [];
}
function undo(){
  if (!history.length) return;
  redoStack.push(snapshot());
  const s = JSON.parse(history.pop());
  nodes = s.nodes; edges = s.edges;
  selected.clear();
  initNav();   // 历史恢复后重新配置导航（ 复审）
  render();
  toast('已撤销');
}
function redo(){
  if (!redoStack.length) return;
  history.push(snapshot());
  const s = JSON.parse(redoStack.pop());
  nodes = s.nodes; edges = s.edges;
  selected.clear();
  initNav();   // 历史恢复后重新配置导航（ 复审）
  render();
  toast('已重做');
}

// ═══════════ 自动布局（JS 端） ═══════════
const MIN_READABLE_PX = 24;   // ：「适应」缩放后节点最小可辨像素下限
const MAX_ROW_NODES = 12;     // ：单行节点数上限（与 Python 端 MAX_NODES_PER_ROW 一致）
function layoutLanes(){
  const TITLE_H = 44, GAP = 200, NGAP = 28;
  const by = {};
  nodes.forEach(n => { if (n.x===0 && n.y===0) (by[n.tier||'default'] = by[n.tier||'default']||[]).push(n); });
  let y = 80;
  tierOrder().forEach(t => {
    const items = by[t]; if (!items || !items.length) return;
    // 依赖 DAG 深度排序（Kahn）后按行宽/行数拆行——沿用  兜底逻辑
    const ids = new Set(items.map(n=>n.id));
    const indeg = {}, succ = {};
    items.forEach(n => { indeg[n.id]=0; succ[n.id]=[]; });
    edges.forEach(e => { const f=e.from, to=e.to; if(ids.has(f)&&ids.has(to)&&f!==to){ succ[f].push(to); indeg[to]=(indeg[to]||0)+1; } });
    const depth = {};
    const q = items.filter(n=>indeg[n.id]===0).map(n=>n.id);
    q.forEach(i=>depth[i]=0);
    while(q.length){ const u=q.shift(); (succ[u]||[]).forEach(v=>{ depth[v]=Math.max(depth[v]||0,(depth[u]||0)+1); if(--indeg[v]===0) q.push(v); }); }
    items.forEach(n => { if (depth[n.id]===undefined) depth[n.id]=0; });
    const sorted = items.slice().sort((a,b)=> (depth[a.id]-depth[b.id]) || (a.label<b.label?-1:a.label>b.label?1:0));
    const rows = [], cur = [];
    sorted.forEach(n => {
      const curW = cur.reduce((s,x)=>s+x.w,0) + NGAP*(cur.length-1);
      if (cur.length && (cur.length>=MAX_ROW_NODES || curW + n.w + NGAP > (CANVAS.width-160))){ rows.push(cur); cur.length=0; }
      cur.push(n);
    });
    if (cur.length) rows.push(cur);
    rows.forEach(row => {
      const total = row.reduce((s,x)=>s+x.w,0) + NGAP*(row.length-1);
      let x = Math.max(80,(CANVAS.width-total)/2);
      let rh = 0;
      row.forEach(n => { n.x=x; n.y=y+TITLE_H; x+=n.w+NGAP; rh=Math.max(rh,n.h); });
      y += rh + NGAP;
    });
    y += (GAP-NGAP) + TITLE_H;
  });
}
function layoutLayered(){
  if (nodes.some(n=>n.tier)){ layoutLanes(); commitChange(); render(); fitView(); toast('已分层布局(三层泳道)'); return; }
  const order = [];
  const layers = {};
  nodes.forEach(n => {
    const l = n.layer || n.type || 'default';
    if (!order.includes(l)) order.push(l);
    (layers[l] = layers[l] || []).push(n);
  });
  recordPre();
  let y = 80;
  order.forEach(l => {
    const placed = layers[l].filter(n => !(n.x===0 && n.y===0));
    const unpos = layers[l].filter(n => n.x===0 && n.y===0);
    if (!unpos.length) return;
    // 依赖 DAG 深度（Kahn）——：单层/超宽节点按深度浅→深排序再拆行
    const ids = new Set(unpos.map(n=>n.id));
    const indeg = {}, succ = {};
    unpos.forEach(n => { indeg[n.id]=0; succ[n.id]=[]; });
    edges.forEach(e => {
      const f=e.from, t=e.to;
      if (ids.has(f) && ids.has(t) && f!==t){ succ[f].push(t); indeg[t]=(indeg[t]||0)+1; }
    });
    const depth = {};
    const q = unpos.filter(n=>indeg[n.id]===0).map(n=>n.id);
    q.forEach(i=>depth[i]=0);
    while (q.length){
      const u = q.shift();
      (succ[u]||[]).forEach(v => { depth[v]=Math.max(depth[v]||0,(depth[u]||0)+1); if(--indeg[v]===0) q.push(v); });
    }
    unpos.forEach(n => { if (depth[n.id]===undefined) depth[n.id]=0; });
    const sorted = unpos.slice().sort((a,b)=> (depth[a.id]-depth[b.id]) || (a.label<b.label?-1:a.label>b.label?1:0));
    // 拆行：每行 ≤12 且总宽 ≤ 画布可用宽
    const rows = [], cur = [];
    sorted.forEach(n => {
      const curW = cur.reduce((s,x)=>s+x.w,0) + 28*(cur.length-1);
      if (cur.length && (cur.length>=MAX_ROW_NODES || curW + n.w + 28 > (CANVAS.width-160))){ rows.push(cur); cur.length=0; }
      cur.push(n);
    });
    if (cur.length) rows.push(cur);
    let placedBottom = 80;
    placed.forEach(n => placedBottom = Math.max(placedBottom, n.y + n.h));
    y = Math.max(y, placedBottom + 150);
    rows.forEach(row => {
      const total = row.reduce((s,n)=>s+n.w,0) + 28*(row.length-1);
      let x = Math.max(80,(CANVAS.width - total)/2);
      let rowH=0;
      row.forEach(n => { n.x=x; n.y=y; x+=n.w+28; rowH=Math.max(rowH,n.h); });
      y += rowH + 150;
    });
  });
  commitChange();
  render();
  fitView();
  toast('已分层布局');
}
function layoutForce(){
  recordPre();
  const rng = (() => { let s = 42; return () => (s = (s*9301+49297)%233280)/233280; })();
  nodes.forEach(n => { n.x = 100 + rng()*(CANVAS.width-200); n.y = 100 + rng()*(CANVAS.height-200); });
  const adj = {};
  edges.forEach(e => { (adj[e.from]=adj[e.from]||[]).push(e.to); (adj[e.to]=adj[e.to]||[]).push(e.from); });
  for (let it=0; it<150; it++){
    for (let i=0;i<nodes.length;i++) for (let j=i+1;j<nodes.length;j++){
      const a=nodes[i], b=nodes[j];
      let dx=b.x-a.x, dy=b.y-a.y;
      const d2=Math.max(dx*dx+dy*dy,1e-4), d=Math.sqrt(d2);
      const f=12000/d2;
      a.x -= f*dx/d; a.y -= f*dy/d; b.x += f*dx/d; b.y += f*dy/d;
    }
    edges.forEach(e => {
      const a=nodes.find(n=>n.id===e.from), b=nodes.find(n=>n.id===e.to);
      if (!a||!b) return;
      let dx=b.x-a.x, dy=b.y-a.y;
      const d=Math.max(Math.sqrt(dx*dx+dy*dy),1e-4);
      const f=0.02*(d-200);
      a.x += f*dx/d; a.y += f*dy/d; b.x -= f*dx/d; b.y -= f*dy/d;
    });
    nodes.forEach(n => { n.x=Math.max(20,Math.min(CANVAS.width-20,n.x)); n.y=Math.max(20,Math.min(CANVAS.height-20,n.y)); });
  }
  commitChange();
  render();
  fitView();
  toast('已力导向布局');
}

// ═══════════ 导出 / 导入 ═══════════
function buildJSON(){
  return {canvas: CANVAS, nodes, edges, meta: DATA.meta || {}};
}
function downloadJSON(){
  const blob = new Blob([JSON.stringify(buildJSON(), null, 2)], {type:'application/json'});
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = 'canvas.json';
  a.click();
  toast('已导出画布 JSON');
}
function importJSON(ev){
  const file = ev.target.files[0];
  if (!file) return;
  const reader = new FileReader();
  reader.onload = () => {
    try {
      const d = JSON.parse(reader.result);
      recordPre();
      if (d.nodes) nodes = d.nodes.map(n => Object.assign({}, n));
      if (d.edges) edges = d.edges.map(e => Object.assign({}, e));
      commitChange();
      selected.clear();
      initNav();   // 画布数据替换后重新配置导航（ 复审）
      render();
      fitView();
      toast('已导入画布 JSON');
    } catch(e){ toast('JSON 解析失败'); }
  };
  reader.readAsText(file);
  ev.target.value = '';
}

// ═══════════ 工具 ═══════════
let toastTimer = null;
function toast(msg){
  const t = document.getElementById('toast');
  t.textContent = msg; t.classList.add('show');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => t.classList.remove('show'), 2200);
}

// ═══════════ 决策者报告视图（arch 画布默认首屏）═══════════
// 一页讲清：这是什么系统 → 分几层 → 差距在哪。数据全部来自内嵌 DATA（与画布同源）。
function shortName(s){ const t = String(s||''); return t.indexOf('/') >= 0 ? t.split('/').pop() : t; }
function pct(x){ return (x == null) ? '--' : Math.round(x*100) + '%'; }
function kpiColor(x){ return x >= 0.8 ? 'ok' : (x >= 0.5 ? 'warn' : 'bad'); }

function buildReport(){
  const meta = DATA.meta || {};
  const s = meta.summary || {};
  const al = meta.alignment || {};
  const gaps = meta.gaps || [];
  const ta = meta.target_arch || {};
  const projectName = ta.project || esc(CANVAS.title || '');
  const steps = nodes.filter(n => n.type === 'step');
  const roles = nodes.filter(n => n.type === 'role');
  const mods  = nodes.filter(n => n.type === 'module');

  // ── 头部 ──
  let h = `<div class="rp-wrap">`;
  h += `<div class="rp-head"><div class="rp-title">
      <h1>📋 ${projectName} · 架构总览</h1>
      <div class="sub">给决策者的一页架构报告：这是什么系统 · 分几层 · 差距在哪（由 CodeRef-AI 静态分析生成）</div>
    </div><button class="rp-btn" onclick="showCanvas()">🔧 编辑画布 / 查看全图</button></div>`;

  // ── KPI 结论卡 ──
  const noKg = (meta.has_kg === false);
  h += `<div class="rp-kpis">
    <div class="rp-kpi"><div class="lbl">架构对齐度</div><div class="val ${kpiColor(al.module_assigned)}">${noKg ? '--' : pct(al.module_assigned)}</div><div class="sub">已归属模块 / 总模块</div></div>
    <div class="rp-kpi"><div class="lbl">角色覆盖</div><div class="val ${kpiColor(al.role_coverage)}">${noKg ? '--' : pct(al.role_coverage)}</div><div class="sub">已实现角色 / 总角色</div></div>
    <div class="rp-kpi"><div class="lbl">差距</div><div class="val ${s.total ? 'bad' : 'ok'}">${noKg ? '--' : (s.total || 0)}</div><div class="sub">高${s.high||0} · 中${s.medium||0} · 低${s.low||0}</div></div>
    <div class="rp-kpi"><div class="lbl">代码模块</div><div class="val">${mods.length}</div><div class="sub">${roles.length} 个技术角色</div></div>
    <div class="rp-kpi"><div class="lbl">游离模块</div><div class="val ${s.unassigned_free ? 'warn' : 'ok'}">${noKg ? '--' : (s.unassigned_free || 0)}</div><div class="sub">另 ${s.unassigned_unmodeled||0} 个未建模</div></div>
  </div>`;
  if (noKg){
    h += `<div class="rp-sec"><h2>⚠ 知识图谱未构建</h2><p class="rp-empty">本项目尚未构建知识图谱，架构差距与对齐度暂不可用。请先运行 coderef_audit 构建图谱后再生成画布。</p></div>`;
  }

  // ── §1 这系统做什么：业务流程 ──
  h += `<div class="rp-sec"><h2>① 这系统做什么</h2><div class="desc">目标架构定义的业务流程（用户旅程），绿色为阶段分组，普通为业务步骤。</div>`;
  const flowNames = [];
  const flowSteps = {};
  steps.forEach(n => {
    const f = (n.props && n.props.flow) || '未命名流程';
    if (!flowSteps[f]) { flowSteps[f] = []; flowNames.push(f); }
    flowSteps[f].push(n);
  });
  if (!flowNames.length){
    h += `<p class="rp-empty">目标架构未定义业务流程（business_flows）。</p>`;
  } else {
    flowNames.forEach(f => {
      h += `<div class="rp-flow"><h3>🎯 ${esc(f)}</h3><div class="rp-steps">`;
      flowSteps[f].forEach((n, i) => {
        if (i > 0) h += `<span class="rp-arrow">→</span>`;
        const isPhase = n.props && n.props.kind === 'phase';
        h += `<span class="rp-step${isPhase ? ' phase' : ''}" title="${esc(n.label)}">${esc(n.label)}</span>`;
      });
      h += `</div></div>`;
    });
  }
  h += `</div>`;

  // ── §2 系统怎么搭：三层结构 ──
  h += `<div class="rp-sec"><h2>② 系统怎么搭</h2><div class="desc">业务层（流程）→ 技术层（角色）→ 代码层（模块）。角色卡列出其管辖的代码模块；灰色为未归属模块。</div>`;
  // 技术层：角色卡
  if (!roles.length){
    h += `<p class="rp-empty">目标架构未定义技术角色（tech_roles）。</p>`;
  } else {
    roles.forEach(r => {
      const p = r.props || {};
      const ms = p.modules || [];
      const missing = p.missing;
      h += `<div class="rp-role"><h3>🏷 ${esc(r.label)}
        <span class="cnt">${ms.length} 个模块</span>
        ${missing ? '<span class="miss">⚠ 未落地</span>' : ''}</h3>`;
      if (ms.length){
        h += `<div class="rp-mods">` + ms.map(m =>
          `<span class="rp-mod" title="${esc(m)}">${esc(shortName(m))}</span>`).join('') + `</div>`;
      } else {
        h += `<p class="rp-empty">（目标角色声明的模块在代码中不存在，或尚未归属）</p>`;
      }
      h += `</div>`;
    });
  }
  // 代码层：未归属模块灰区
  const assigned = new Set();
  roles.forEach(r => ((r.props||{}).modules || []).forEach(m => assigned.add(m)));
  edges.forEach(e => { if (e.type === 'belong'){ const to = nodes.find(n => n.id === e.to); if (to) assigned.add((to.props||{}).full_label || to.label); } });
  const freeMods = mods.filter(m => !assigned.has((m.props||{}).full_label || m.label));
  h += `<div class="rp-flow"><h3>🧩 代码层 · 未归属模块（${freeMods.length}）</h3>`;
  if (freeMods.length){
    h += `<div class="rp-mods">` + freeMods.map(m =>
      `<span class="rp-mod free" title="${esc((m.props||{}).full_label || m.label)}">${esc(shortName(m.label))}${(m.props||{}).placeholder ? '（占位）' : ''}</span>`).join('') + `</div>`;
  } else {
    h += `<p class="rp-empty">全部模块均已归属角色，没有游离代码。</p>`;
  }
  h += `</div></div>`;

  // ── §3 哪里要修：差距清单 ──
  h += `<div class="rp-sec"><h2>③ 哪里要修</h2><div class="desc">现状与目标架构的差距，按严重度排列（红=高 / 橙=中 / 蓝=低）。修复后重新生成画布可复核。</div>`;
  if (noKg){
    h += `<p class="rp-empty">（图谱未构建，差距分析不可用）</p>`;
  } else if (!gaps.length){
    h += `<div class="rp-ok">✅ 架构与目标全部对齐，没有发现差距。</div>`;
  } else {
    const sevName = { high: '高优先级', medium: '中优先级', low: '低优先级' };
    ['high','medium','low'].forEach(sev => {
      const grp = gaps.filter(g => (g.severity || 'low') === sev);
      if (!grp.length) return;
      h += `<div class="rp-gapgrp"><h3>${sev === 'high' ? '🔴' : sev === 'medium' ? '🟠' : '🔵'} ${sevName[sev]}（${grp.length} 项）</h3>`;
      grp.forEach(g => {
        h += `<div class="rp-gap"><span class="sev ${sev}">${sevName[sev].slice(0,1)}</span>
          <span class="txt">${esc(g.detail || g.type || '未描述的差距')}</span></div>`;
      });
      h += `</div>`;
    });
  }
  h += `</div>`;
  h += `<div class="rp-foot">由 CodeRef-AI 生成 · 点右上「编辑画布」可拖拽调整架构、连线成流并导出目标架构 JSON</div></div>`;
  document.getElementById('reportView').innerHTML = h;
}

function showReport(){
  buildReport();
  document.getElementById('reportView').classList.add('show');
  // 画布层隐藏但保持布局（visibility 而非 display，避免 fitView 拿不到尺寸）
  ['toolbar','canvasWrap','miniMap'].forEach(id => {
    const el = document.getElementById(id);
    if (el) el.style.visibility = 'hidden';
  });
  const hint = document.querySelector('.hint');
  if (hint) hint.style.display = 'none';
  document.getElementById('reportView').scrollTop = 0;
}
function showCanvas(){
  document.getElementById('reportView').classList.remove('show');
  ['toolbar','canvasWrap','miniMap'].forEach(id => {
    const el = document.getElementById(id);
    if (el) el.style.visibility = '';
  });
  const hint = document.querySelector('.hint');
  if (hint) hint.style.display = '';
  fitView();
}

// ═══════════ 初始化 ═══════════
initNav();
render();
fitView();
// arch 画布默认打开决策者报告首屏（flow 流程画布保持画布形态）
if (CANVAS.type === 'arch' && nodes.length){ showReport(); }
</script>
</body>
</html>
"""
