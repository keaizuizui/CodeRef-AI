# -*- coding: utf-8 -*-
"""
ArchCanvas — 可视化架构画布生成器（5.0 Phase 1）

把"人定义正轨"从 JSON 编辑提升为可视化拖拽。三层布局：
  业务层（业务流程步骤）→ 技术层（技术角色容器）→ 代码层（代码模块节点）

交互：
  - 代码模块节点可拖拽到技术角色容器 → 定义目标归属
  - 业务步骤可连线到技术角色 → 定义业务→技术映射
  - 差距高亮：游离模块灰底 / 依赖违例红连线 / 缺失角色红虚线 / 循环依赖黄框
  - 导出 JSON：前端生成目标架构 JSON 并下载/复制，再经 coderef_target_arch_set 落盘

技术选型：纯 HTML/CSS/JS + SVG 自包含，零外部依赖（离线可用）。
数据层完全复用 arch_gap_analyzer + 知识图谱，画布只负责渲染与交互。

用法：
    from core.canvas_generator import ArchCanvas
    path = ArchCanvas().generate(project_path="...", target_arch={...})
"""

import os
import json
import html
from datetime import datetime
from typing import Any, Dict, List, Optional

from loguru import logger

from core.arch_gap_analyzer import (
    _is_test_module,
    _match_module_ids,
    analyze_gap,
)
from core.arch_audit import locate_kg_db, module_of
from core.graph_closure import load_graph


class ArchCanvas:
    """可视化架构画布生成器（5.0 Phase 1）"""

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

        # 3. 组装画布数据
        data = self._build_canvas_data(
            project_path, target_arch, nodes, adj, gap_result, has_kg)

        # 4. 渲染 HTML
        data_json = json.dumps(data, ensure_ascii=False)
        html_content = self._render_html(title, data_json)

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
    # 数据组装
    # ────────────────────────────────────────────────

    def _load_stored_arch(self, project_path: str) -> Optional[dict]:
        path = os.path.join(project_path, ".coderef", "target_arch.json")
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        return None

    def _build_canvas_data(
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

        # 角色 → 已匹配模块（复用差距分析器的匹配逻辑）
        role_modules: Dict[str, List[str]] = {}
        for role in roles:
            matched = _match_module_ids(nodes, project_path, role.get("target_modules", []))
            role_modules[role.get("id", "")] = sorted(
                module_of(nodes[nid], project_path) or nodes[nid].get("name", "?")
                for nid in matched
            )

        # 代码层模块列表（过滤测试模块）
        modules = []
        for nid, n in nodes.items():
            if n.get("type") != "module":
                continue
            m = module_of(n, project_path) or n.get("name", "?")
            if _is_test_module(m):
                continue
            modules.append({
                "id": nid,
                "name": m,
                "file_path": n.get("file_path", ""),
                "role": self._find_role_of(nid, roles, nodes, project_path),
            })
        modules.sort(key=lambda x: x["name"])

        # 差距高亮数据
        gaps = (gap_result or {}).get("gaps", [])
        gap_modules = set()
        for g in gaps:
            if g.get("type") == "unassigned":
                gap_modules.add(g.get("module", ""))
        cycle_modules = set()
        for g in gaps:
            if g.get("type") == "cycle":
                cycle_modules.update(g.get("modules", []))
        missing_roles = set()
        for g in gaps:
            if g.get("type") == "missing":
                missing_roles.add(g.get("role_id", ""))
        violation_edges = []
        for g in gaps:
            if g.get("type") == "dependency_violation":
                violation_edges.append({
                    "from": g.get("from_module", ""),
                    "to": g.get("to_module", ""),
                })

        return {
            "project_path": project_path,
            "has_kg": has_kg,
            "target_arch": target_arch,
            "roles": [
                {
                    "id": role.get("id", ""),
                    "name": role.get("name", role.get("id", "")),
                    "modules": role_modules.get(role.get("id", ""), []),
                    "missing": role.get("id", "") in missing_roles,
                }
                for role in roles
            ],
            "flows": flows,
            "modules": modules,
            "gaps": gaps,
            "summary": (gap_result or {}).get("summary", {}),
            "alignment": (gap_result or {}).get("alignment", {}),
            "highlight": {
                "unassigned": sorted(gap_modules),
                "cycle": sorted(cycle_modules),
                "violations": violation_edges,
            },
        }

    def _find_role_of(self, nid: str, roles: List[dict], nodes: dict,
                      project_path: str) -> str:
        """返回模块所属角色 id（未归属返回空串）。"""
        for role in roles:
            matched = _match_module_ids(nodes, project_path, role.get("target_modules", []))
            if nid in matched:
                return role.get("id", "")
        return ""

    # ────────────────────────────────────────────────
    # HTML 渲染（纯自包含，占位符替换避免 f-string 大括号转义）
    # ────────────────────────────────────────────────

    def _render_html(self, title: str, data_json: str) -> str:
        return _HTML_TEMPLATE.replace("__TITLE__", html.escape(title)).replace(
            "__DATA_JSON__", data_json)


_HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>__TITLE__</title>
<style>
* { margin:0; padding:0; box-sizing:border-box; }
body { font-family:"Segoe UI","Noto Sans CJK SC",sans-serif; background:#0F172A; color:#E2E8F0; min-height:100vh; }
#toolbar { position:sticky; top:0; z-index:100; background:rgba(15,23,42,.97); backdrop-filter:blur(10px); padding:10px 16px; display:flex; align-items:center; gap:12px; border-bottom:1px solid #1E293B; flex-wrap:wrap; }
#toolbar h1 { font-size:15px; font-weight:600; white-space:nowrap; }
#toolbar .stats { font-size:12px; color:#94A3B8; }
#toolbar .spacer { flex:1; }
#toolbar button { background:#1E293B; color:#E2E8F0; border:1px solid #334155; padding:6px 12px; border-radius:6px; cursor:pointer; font-size:12px; transition:all .15s; }
#toolbar button:hover { background:#334155; border-color:#475569; }
#toolbar button.primary { background:#3B82F6; border-color:#3B82F6; }
#toolbar button.primary:hover { background:#2563EB; }
.badge { display:inline-block; padding:2px 8px; border-radius:10px; font-size:11px; font-weight:600; }
.badge.high { background:#EF4444; color:#fff; }
.badge.medium { background:#F59E0B; color:#111; }
.badge.low { background:#64748B; color:#fff; }
.layer { margin:14px 16px; border:1px solid #1E293B; border-radius:10px; overflow:hidden; }
.layer-head { padding:8px 14px; font-size:13px; font-weight:600; display:flex; align-items:center; gap:8px; }
.layer-body { padding:12px 14px; display:flex; flex-wrap:wrap; gap:10px; min-height:52px; }
.lb { background:#1E293B; }
.lt { background:#312E81; }
.lc { background:#0F172A; }
.biz-step { background:#1E293B; border:1px solid #334155; border-radius:8px; padding:8px 12px; font-size:12px; cursor:pointer; transition:all .15s; position:relative; }
.biz-step:hover { border-color:#3B82F6; }
.biz-step .flow-name { color:#64748B; font-size:10px; display:block; }
.biz-step.role-linked { border-color:#10B981; }
.role-box { background:#1E293B; border:1px solid #334155; border-radius:8px; padding:8px 12px; min-width:150px; min-height:64px; transition:all .15s; }
.role-box.drag-over { border-color:#3B82F6; background:#1E3A5F; }
.role-box.missing { border-color:#EF4444; border-style:dashed; }
.role-name { font-size:12px; font-weight:600; color:#60A5FA; margin-bottom:6px; }
.role-modules { display:flex; flex-wrap:wrap; gap:4px; }
.role-mod { background:#0F172A; border:1px solid #334155; border-radius:4px; padding:2px 6px; font-size:10px; color:#94A3B8; font-family:monospace; }
.module-grid { display:flex; flex-wrap:wrap; gap:8px; }
.module-node { background:#1E293B; border:1px solid #334155; border-radius:6px; padding:6px 10px; font-size:11px; font-family:monospace; cursor:grab; transition:all .15s; }
.module-node:hover { border-color:#3B82F6; }
.module-node.assigned { border-color:#10B981; }
.module-node.unassigned { border-color:#64748B; color:#94A3B8; }
.module-node.cycle { border-color:#F59E0B; box-shadow:0 0 0 1px #F59E0B; }
.module-node.dragging { opacity:.5; }
#gapPanel { position:fixed; right:0; top:60px; bottom:0; width:340px; background:rgba(15,23,42,.97); border-left:1px solid #1E293B; padding:14px; overflow-y:auto; transform:translateX(100%); transition:transform .25s; z-index:90; }
#gapPanel.open { transform:translateX(0); }
#gapPanel h3 { font-size:14px; margin-bottom:10px; color:#60A5FA; }
.gap-item { background:#1E293B; border-radius:6px; padding:8px 10px; margin-bottom:8px; font-size:12px; border-left:3px solid #64748B; }
.gap-item.high { border-left-color:#EF4444; }
.gap-item.medium { border-left-color:#F59E0B; }
.gap-item.low { border-left-color:#64748B; }
.gap-item .g-type { font-weight:600; }
.gap-item .g-detail { color:#94A3B8; font-size:11px; margin-top:3px; }
#toast { position:fixed; bottom:20px; left:50%; transform:translateX(-50%); background:#1E293B; border:1px solid #334155; padding:10px 18px; border-radius:8px; font-size:13px; opacity:0; transition:opacity .3s; z-index:200; }
#toast.show { opacity:1; }
#svgLayer { position:fixed; top:0; left:0; width:100%; height:100%; pointer-events:none; z-index:50; }
#svgLayer line { stroke:#EF4444; stroke-width:2; stroke-dasharray:6 4; }
.empty-hint { color:#475569; font-size:12px; width:100%; text-align:center; padding:12px 0; }
</style>
</head>
<body>
<div id="toolbar">
  <h1>🗺 __TITLE__</h1>
  <span class="stats" id="stats"></span>
  <span class="spacer"></span>
  <button onclick="toggleGapPanel()">📋 差距清单</button>
  <button onclick="copyJSON()">📋 复制目标架构 JSON</button>
  <button class="primary" onclick="downloadJSON()">⬇ 导出目标架构 JSON</button>
</div>
<div id="gapPanel"><h3>差距清单</h3><div id="gapList"></div></div>
<div id="toast"></div>
<svg id="svgLayer"></svg>

<div class="layer lb">
  <div class="layer-head">📈 业务层 <span class="stats" style="font-size:11px;color:#94A3B8">（点击步骤 → 再点技术角色建立映射）</span></div>
  <div class="layer-body" id="bizLayer"></div>
</div>
<div class="layer lt">
  <div class="layer-head">🏷 技术层 <span class="stats" style="font-size:11px;color:#94A3B8">（把代码模块拖入角色容器定义归属）</span></div>
  <div class="layer-body" id="techLayer"></div>
</div>
<div class="layer lc">
  <div class="layer-head">🧩 代码层 <span class="stats" style="font-size:11px;color:#94A3B8">（绿=已归属 灰=游离 黄=循环依赖）</span></div>
  <div class="layer-body" id="codeLayer"></div>
</div>

<script>
const DATA = __DATA_JSON__;
let selectedStep = null;

function esc(s){ return String(s==null?'':s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;'); }

function renderStats(){
  const s = DATA.summary || {};
  const a = DATA.alignment || {};
  let html = `差距: <span class="badge high">${s.high||0}高</span> <span class="badge medium">${s.medium||0}中</span> <span class="badge low">${s.low||0}低</span>`;
  if (a.role_coverage!=null) html += ` | 对齐度: 职责${Math.round(a.role_coverage*100)}% 归属${Math.round(a.module_assigned*100)}%`;
  document.getElementById('stats').innerHTML = html;
}

function renderBiz(){
  const box = document.getElementById('bizLayer');
  const flows = DATA.flows || [];
  if (!flows.length){ box.innerHTML = '<div class="empty-hint">尚未定义业务流程（可在目标架构 JSON 的 business_flows 中补充）</div>'; return; }
  let html = '';
  flows.forEach(f => {
    (f.steps||[]).forEach(st => {
      const linked = (st.tech_roles||[]).length > 0;
      html += `<div class="biz-step ${linked?'role-linked':''}" data-flow="${esc(f.id)}" data-step="${esc(st.id)}" onclick="selectStep(this)">
        <span class="flow-name">${esc(f.name||f.id)}</span>${esc(st.name||st.id)}
        <span style="color:#64748B;font-size:10px">${(st.tech_roles||[]).join(',')}</span>
      </div>`;
    });
  });
  box.innerHTML = html;
}

function renderTech(){
  const box = document.getElementById('techLayer');
  const roles = DATA.roles || [];
  if (!roles.length){ box.innerHTML = '<div class="empty-hint">尚未定义技术角色（可在目标架构 JSON 的 tech_roles 中补充）</div>'; return; }
  let html = '';
  roles.forEach(r => {
    const mods = (r.modules||[]).map(m => `<span class="role-mod">${esc(m)}</span>`).join('');
    html += `<div class="role-box ${r.missing?'missing':''}" data-role="${esc(r.id)}"
        ondragover="event.preventDefault();this.classList.add('drag-over')"
        ondragleave="this.classList.remove('drag-over')"
        ondrop="dropToRole(event,'${esc(r.id)}')"
        onclick="clickRole('${esc(r.id)}')">
      <div class="role-name">${esc(r.name)} ${r.missing?'<span style="color:#EF4444;font-size:10px">(缺实现)</span>':''}</div>
      <div class="role-modules">${mods || '<span style="color:#475569;font-size:10px">空</span>'}</div>
    </div>`;
  });
  box.innerHTML = html;
}

function renderCode(){
  const box = document.getElementById('codeLayer');
  const mods = DATA.modules || [];
  if (!mods.length){ box.innerHTML = '<div class="empty-hint">'+(DATA.has_kg?'未发现代码模块':'知识图谱不存在，请先构建（coderef_audit / coderef_memory_sync）')+'</div>'; return; }
  const hl = DATA.highlight || {};
  const unassigned = new Set(hl.unassigned||[]);
  const cycle = new Set(hl.cycle||[]);
  let html = '';
  mods.forEach(m => {
    let cls = m.role ? 'assigned' : 'unassigned';
    if (cycle.has(m.name)) cls = 'cycle';
    html += `<div class="module-node ${cls}" draggable="true" data-module="${esc(m.name)}"
        ondragstart="dragStart(event,'${esc(m.name)}')" ondragend="this.classList.remove('dragging')"
        title="${esc(m.file_path)}">${esc(m.name)}</div>`;
  });
  box.innerHTML = html;
}

function renderGaps(){
  const box = document.getElementById('gapList');
  const gaps = DATA.gaps || [];
  if (!gaps.length){ box.innerHTML = '<div class="empty-hint">暂无差距，架构已对齐 🎉</div>'; return; }
  box.innerHTML = gaps.map(g => `<div class="gap-item ${esc(g.severity)}">
    <span class="g-type">[${esc(g.type)}] ${esc(g.severity)}</span>
    <div class="g-detail">${esc(g.detail)}</div>
  </div>`).join('');
}

function renderViolations(){
  const svg = document.getElementById('svgLayer');
  svg.innerHTML = '';
  const hl = DATA.highlight || {};
  (hl.violations||[]).forEach(v => {
    const from = document.querySelector(`.module-node[data-module="${CSS.escape(v.from)}"]`);
    const to = document.querySelector(`.module-node[data-module="${CSS.escape(v.to)}"]`);
    if (!from || !to) return;
    const r1 = from.getBoundingClientRect(), r2 = to.getBoundingClientRect();
    const line = document.createElementNS('http://www.w3.org/2000/svg','line');
    line.setAttribute('x1', r1.left + r1.width/2); line.setAttribute('y1', r1.top + r1.height/2);
    line.setAttribute('x2', r2.left + r2.width/2); line.setAttribute('y2', r2.top + r2.height/2);
    svg.appendChild(line);
  });
}

function dragStart(e, name){ e.dataTransfer.setData('text/plain', name); e.target.classList.add('dragging'); }
function dropToRole(e, roleId){
  e.preventDefault(); e.currentTarget.classList.remove('drag-over');
  const mod = e.dataTransfer.getData('text/plain');
  if (!mod) return;
  const role = (DATA.roles||[]).find(r => r.id === roleId);
  if (!role) return;
  if (!role.modules.includes(mod)) role.modules.push(mod);
  toast(`已把 ${mod} 归属到角色 ${role.name}`);
  renderTech();
}
function selectStep(el){
  document.querySelectorAll('.biz-step').forEach(s => s.style.borderColor='');
  el.style.borderColor = '#3B82F6';
  selectedStep = { flow: el.dataset.flow, step: el.dataset.step, el };
  toast('已选中业务步骤，点击技术角色建立映射');
}
function clickRole(roleId){
  if (!selectedStep) return;
  const flow = (DATA.flows||[]).find(f => f.id === selectedStep.flow);
  const step = flow && (flow.steps||[]).find(s => s.id === selectedStep.step);
  if (!step) return;
  const roles = step.tech_roles = step.tech_roles || [];
  if (!roles.includes(roleId)) roles.push(roleId);
  toast(`已把 ${step.name} 映射到角色 ${roleId}`);
  renderBiz(); selectedStep = null;
}
function buildTargetArch(){
  const roles = (DATA.roles||[]).map(r => {
    const orig = (DATA.target_arch.tech_roles||[]).find(o => o.id === r.id) || {};
    return { id:r.id, name:r.name, target_modules:r.modules.slice().sort(),
             depends_on:orig.depends_on||[], depended_by:orig.depended_by||[] };
  });
  const flows = (DATA.flows||[]).map(f => ({
    id:f.id, name:f.name,
    steps:(f.steps||[]).map(s => ({ id:s.id, name:s.name, tech_roles:s.tech_roles||[] }))
  }));
  return { version:'5.0', project:DATA.target_arch.project||'', business_flows:flows,
           tech_roles:roles, constraints:DATA.target_arch.constraints||[] };
}
function downloadJSON(){
  const blob = new Blob([JSON.stringify(buildTargetArch(), null, 2)], {type:'application/json'});
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = 'target_arch.json';
  a.click();
  toast('已导出 target_arch.json，可用 coderef_target_arch_set 落盘');
}
function copyJSON(){
  navigator.clipboard.writeText(JSON.stringify(buildTargetArch(), null, 2)).then(
    () => toast('目标架构 JSON 已复制到剪贴板'));
}
function toggleGapPanel(){ document.getElementById('gapPanel').classList.toggle('open'); }
function toast(msg){
  const t = document.getElementById('toast');
  t.textContent = msg; t.classList.add('show');
  clearTimeout(t._h); t._h = setTimeout(() => t.classList.remove('show'), 2200);
}
window.addEventListener('resize', renderViolations);

renderStats(); renderBiz(); renderTech(); renderCode(); renderGaps(); renderViolations();
</script>
</body>
</html>
"""
