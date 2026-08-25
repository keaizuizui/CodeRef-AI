# -*- coding: utf-8 -*-
"""
FreeCanvas — 自由布局画布引擎（5.4）

参考 smart-flow（GPL-3.0，仅参考交互理念、不拷贝代码）的自由布局画布能力，
自研轻量实现：纯 HTML/CSS/JS + SVG 自包含，零外部依赖（离线可用）。

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
from typing import Any, Dict, List, Optional

from loguru import logger

# 默认画布尺寸（世界坐标）
DEFAULT_WIDTH = 3200
DEFAULT_HEIGHT = 2200

# 节点默认尺寸
_DEF_W = 180
_DEF_H = 60


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
    unpositioned = [n for n in nodes if n["x"] == 0 and n["y"] == 0]
    if not unpositioned:
        return data
    if mode == "force":
        _layout_force(nodes, edges)
    else:
        _layout_layered(nodes, edges)
    return data


def _layout_layered(nodes: List[Dict], edges: List[Dict],
                    layer_gap: float = 150, node_gap: float = 28,
                    margin: float = 80) -> None:
    """分层布局：按 layer 分组，层间垂直排列，层内水平排列。"""
    # 层分组（保持节点出现顺序）
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
        total_w = sum(n.get("w", _DEF_W) for n in items) + node_gap * (len(items) - 1)
        x = margin + max(0, (DEFAULT_WIDTH - 2 * margin - total_w) / 2)
        row_h = 0
        for n in items:
            n["x"] = x
            n["y"] = y
            x += n.get("w", _DEF_W) + node_gap
            row_h = max(row_h, n.get("h", _DEF_H))
        y += row_h + layer_gap


def _layout_force(nodes: List[Dict], edges: List[Dict],
                  iterations: int = 150, w: float = DEFAULT_WIDTH,
                  h: float = DEFAULT_HEIGHT) -> None:
    """简单力导向布局：斥力（所有节点对）+ 弹簧力（边）。"""
    import random
    rng = random.Random(42)
    for n in nodes:
        n["x"] = rng.uniform(100, w - 100)
        n["y"] = rng.uniform(100, h - 100)

    # 邻接表
    adj: Dict[str, List[str]] = {}
    for e in edges:
        adj.setdefault(e["from"], []).append(e["to"])
        adj.setdefault(e["to"], []).append(e["from"])

    for _ in range(iterations):
        # 斥力
        for i in range(len(nodes)):
            for j in range(i + 1, len(nodes)):
                a, b = nodes[i], nodes[j]
                dx = b["x"] - a["x"]
                dy = b["y"] - a["y"]
                d2 = max(dx * dx + dy * dy, 1e-4)
                d = d2 ** 0.5
                f = 12000 / d2
                fx, fy = f * dx / d, f * dy / d
                a["x"] -= fx
                a["y"] -= fy
                b["x"] += fx
                b["y"] += fy
        # 弹簧力
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
            a["x"] += fx
            a["y"] += fy
            b["x"] -= fx
            b["y"] -= fy
        # 边界约束
        for n in nodes:
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
#edgeLayer .edge-label { font-size:10px; fill:#94A3B8; pointer-events:none; }
#nodeLayer { position:absolute; top:0; left:0; width:0; height:0; }
.node { position:absolute; background:#1E293B; border:2px solid #334155; border-radius:10px; display:flex; flex-direction:column; align-items:center; justify-content:center; cursor:move; user-select:none; transition:box-shadow .15s; }
.node:hover { box-shadow:0 0 0 1px rgba(59,130,246,.4); }
.node.selected { box-shadow:0 0 0 2px #3B82F6; }
.node .node-icon { font-size:18px; line-height:1; }
.node .node-label { font-size:11px; color:#E2E8F0; text-align:center; padding:2px 6px; word-break:break-all; max-height:36px; overflow:hidden; }
.node .node-sub { font-size:9px; color:#64748B; max-width:90%; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
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
</style>
</head>
<body>
<div id="toolbar">
  <h1>🗺 __TITLE__</h1>
  <span class="stats" id="stats"></span>
  <span class="spacer"></span>
  <button onclick="zoomBy(1.2)" title="放大 (Ctrl++)">➕ 放大</button>
  <button onclick="zoomBy(1/1.2)" title="缩小 (Ctrl+-)">➖ 缩小</button>
  <button onclick="fitView()" title="适应画布">📐 适应</button>
  <button onclick="resetView()" title="重置视图">🔄 重置</button>
  <span class="sep"></span>
  <button onclick="layoutLayered()" title="分层自动布局">📊 分层布局</button>
  <button onclick="layoutForce()" title="力导向自动布局">🕸 力导向</button>
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

const wrap = document.getElementById('canvasWrap');
const viewport = document.getElementById('viewport');
const nodeLayer = document.getElementById('nodeLayer');
const edgeLayer = document.getElementById('edgeLayer');
const mmCanvas = document.getElementById('mmCanvas');
const mmCtx = mmCanvas.getContext('2d');

function esc(s){ return String(s==null?'':s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;'); }

// ═══════════ 视口变换 ═══════════
function toWorld(sx, sy){ return {x:(sx - view.x)/view.scale, y:(sy - view.y)/view.scale}; }
function applyView(){
  viewport.style.transform = `translate(${view.x}px, ${view.y}px) scale(${view.scale})`;
  renderMiniMap();
}
function zoomAt(cx, cy, factor){
  const w = toWorld(cx, cy);
  view.scale = Math.max(0.2, Math.min(3, view.scale * factor));
  view.x = cx - w.x * view.scale;
  view.y = cy - w.y * view.scale;
  applyView();
}
function zoomBy(factor){ zoomAt(wrap.clientWidth/2, wrap.clientHeight/2, factor); }
function fitView(){
  if (!nodes.length) return;
  let minX=Infinity, minY=Infinity, maxX=-Infinity, maxY=-Infinity;
  nodes.forEach(n => { minX=Math.min(minX,n.x); minY=Math.min(minY,n.y); maxX=Math.max(maxX,n.x+n.w); maxY=Math.max(maxY,n.y+n.h); });
  const pad = 80;
  minX-=pad; minY-=pad; maxX+=pad; maxY+=pad;
  const w = maxX-minX, h = maxY-minY;
  view.scale = Math.min(wrap.clientWidth/w, wrap.clientHeight/h, 1.5);
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
    const el = document.createElement('div');
    el.className = 'node' + (selected.has(n.id) ? ' selected' : '');
    el.style.left = n.x+'px'; el.style.top = n.y+'px';
    el.style.width = n.w+'px'; el.style.height = n.h+'px';
    el.style.borderColor = n.color || '#334155';
    el.dataset.id = n.id;
    const sub = n.props && (n.props.file || n.props.desc || '');
    el.innerHTML = `<div class="node-icon">${esc(n.icon||'')}</div>
      <div class="node-label">${esc(n.label)}</div>
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
    if (!from || !to) return;
    const p1 = portPos(from, e.fromPort || autoPort(from,to).fromPort);
    const p2 = portPos(to, e.toPort || autoPort(from,to).toPort);
    const path = document.createElementNS('http://www.w3.org/2000/svg','path');
    path.setAttribute('d', bezierPath(p1,p2));
    path.setAttribute('stroke', e.color || '#94A3B8');
    path.setAttribute('stroke-width', e.selected ? 3 : 2);
    path.setAttribute('fill','none');
    if (e.dashed) path.setAttribute('stroke-dasharray','6 4');
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
function renderStats(){
  const s = (DATA.meta && DATA.meta.summary) || {};
  let html = `节点 ${nodes.length} | 连线 ${edges.length}`;
  if (s.total != null) html += ` | 差距 ${s.total} (高${s.high||0}/中${s.medium||0}/低${s.low||0})`;
  document.getElementById('stats').innerHTML = html;
}
function render(){
  renderNodes(); renderEdges(); renderStats(); renderMiniMap();
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
    mmCtx.fillStyle = n.color || '#3B82F6';
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
        edges.push({id:eid, from:connectFrom.nodeId, to:toNode.id,
                    fromPort: connectFrom.port || ap.fromPort, toPort: ap.toPort,
                    label:'', type:'edge', color:'#94A3B8', dashed:false});
        pushHistory();
        toast(`已连线 ${from.label} → ${toNode.label}`);
      }
    }
    clearTempEdge();
    connectFrom = null;
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
  pushHistory();
  render();
  toast('已保存');
}
function closeProps(){ document.getElementById('propPanel').classList.remove('open'); propTarget = null; }
function deleteSelected(){
  if (!propTarget) return;
  const {kind, id} = propTarget;
  if (kind === 'node'){
    nodes = nodes.filter(n => n.id !== id);
    edges = edges.filter(e => e.from !== id && e.to !== id);
  } else {
    edges = edges.filter(e => e.id !== id);
  }
  selected.clear();
  pushHistory();
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
  nodes.push({id:nid, type:src.type, label:src.label + ' (副本)', x:src.x+30, y:src.y+30,
              w:src.w, h:src.h, color:src.color, icon:src.icon, layer:src.layer,
              props:Object.assign({}, src.props), ports:src.ports.slice()});
  pushHistory();
  render();
  toast('已复制节点');
}
function menuAddNode(){
  if (!ctxTarget || ctxTarget.kind !== 'canvas') return;
  const nid = 'n' + Date.now();
  nodes.push({id:nid, type:'node', label:'新节点', x:ctxTarget.wx, y:ctxTarget.wy,
              w:180, h:60, color:'#3B82F6', icon:'📌', layer:'default', props:{}, ports:['top','right','bottom','left']});
  pushHistory();
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
      nodes = nodes.filter(n => !ids.has(n.id));
      edges = edges.filter(e => !ids.has(e.from) && !ids.has(e.to));
      selected.clear();
      pushHistory();
      render();
      toast('已删除选中');
    }
  } else if (e.key.startsWith('Arrow')){
    if (!selected.size) return;
    e.preventDefault();
    const dx = e.key === 'ArrowLeft' ? -10 : e.key === 'ArrowRight' ? 10 : 0;
    const dy = e.key === 'ArrowUp' ? -10 : e.key === 'ArrowDown' ? 10 : 0;
    nodes.forEach(n => { if (selected.has(n.id)){ n.x += dx; n.y += dy; } });
    render();
  } else if (e.ctrlKey && (e.key === '+' || e.key === '=')){
    e.preventDefault(); zoomBy(1.2);
  } else if (e.ctrlKey && e.key === '-'){
    e.preventDefault(); zoomBy(1/1.2);
  }
});

// ═══════════ 撤销 / 重做 ═══════════
function snapshot(){ return JSON.stringify({nodes, edges}); }
function pushHistory(){
  history.push(snapshot());
  if (history.length > 100) history.shift();
  redoStack = [];
}
function undo(){
  if (!history.length) return;
  redoStack.push(snapshot());
  const s = JSON.parse(history.pop());
  nodes = s.nodes; edges = s.edges;
  selected.clear();
  render();
  toast('已撤销');
}
function redo(){
  if (!redoStack.length) return;
  history.push(snapshot());
  const s = JSON.parse(redoStack.pop());
  nodes = s.nodes; edges = s.edges;
  selected.clear();
  render();
  toast('已重做');
}

// ═══════════ 自动布局（JS 端） ═══════════
function layoutLayered(){
  const order = [];
  const layers = {};
  nodes.forEach(n => {
    const l = n.layer || n.type || 'default';
    if (!order.includes(l)) order.push(l);
    (layers[l] = layers[l] || []).push(n);
  });
  let y = 80;
  order.forEach(l => {
    const items = layers[l];
    const total = items.reduce((s,n) => s + n.w, 0) + 28*(items.length-1);
    let x = Math.max(80, (CANVAS.width - total)/2);
    let rowH = 0;
    items.forEach(n => { n.x = x; n.y = y; x += n.w + 28; rowH = Math.max(rowH, n.h); });
    y += rowH + 150;
  });
  pushHistory();
  render();
  fitView();
  toast('已分层布局');
}
function layoutForce(){
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
  pushHistory();
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
      if (d.nodes) nodes = d.nodes.map(n => Object.assign({}, n));
      if (d.edges) edges = d.edges.map(e => Object.assign({}, e));
      pushHistory();
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

// ═══════════ 初始化 ═══════════
render();
fitView();
</script>
</body>
</html>
"""
