# -*- coding: utf-8 -*-
"""
GovWebDash v2.0 —— CodeRef 5.2 治理 Web 看板（应用态增强）

在"自包含 HTML + 只读 http.server"之上，把看板推进为可交互应用态：
  - 交互能力：工作项表格支持按 周期/状态/严重级/角色 筛选；点击行展开详情
    （差距快照证据 + 活动日志）；状态流转动作按钮（Detected→Confirmed→Fixing→
    Verified、豁免 Rejected、Verified 后归档 Archived），写回治理库。
  - 数据回写：`/api/transition` POST（限 127.0.0.1）把前端流转写回 govern
    ance_store，与 HealthCycle.transition_issue / reject_issue 同语义，
    每次流转写 issue_event 审计轨迹。
  - 形态务实：仍为单文件自包含 HTML + 标准库 http.server，零 CDN/工程化依赖。

复用：HealthCycle 报告聚合 + gov_view 预置视图 + governance_store 状态机。
interactive=False 时退化为只读查看（保留筛选/详情），隐藏流转回写按钮。
"""

import html
import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, List
from urllib.parse import parse_qs

from loguru import logger

from core.governance_store import ALL_STATUSES
from core.healthcycle import HealthCycle

# 已启动的看板服务句柄注册表（key=project_path；句柄不参与 JSON 序列化）
_SERVERS: Dict[str, Dict[str, Any]] = {}

# 每个状态的合法流转目标（对齐 governance_store 状态机，供前端渲染流转按钮）
ALLOWED_NEXT: Dict[str, List[str]] = {
    "Detected": ["Confirmed", "Rejected"],
    "Confirmed": ["Fixing", "Rejected", "Detected"],
    "Fixing": ["Verified", "Rejected", "Confirmed"],
    "Verified": ["Archived", "Fixing", "Rejected"],
    "Archived": [],
    "Rejected": ["Detected", "Confirmed"],
}


def _esc(s: Any) -> str:
    return html.escape(str(s if s is not None else ""))


def _sorted_dedup(items: List[str]) -> List[str]:
    seen = set()
    out = []
    for x in items:
        if x and x not in seen:
            seen.add(x)
            out.append(x)
    return out


def _build_payload(project_path: str, cid: str = "") -> Dict[str, Any]:
    """聚合看板数据（周期 / 计数 / 各预置视图 / 趋势 / 周期列表 / 工作项）。"""
    hc = HealthCycle(project_path)
    report = hc.report(cid=cid)
    cyc = report.get("active_cycle") or {}
    cid_effective = cid or cyc.get("id", "")
    views = {}
    for view in ("open", "high", "recurred", "rejected", "archived", "overdue"):
        views[view] = hc.store.list_issues(view=view, limit=200)
    payload = {
        "project_path": project_path,
        "active_cycle": cyc,
        "summary": report.get("summary"),
        "status_counts": report.get("status_counts"),
        "trend": report.get("trend", []),
        "cycles": hc.store.list_cycles(),
        "allowed_next": ALLOWED_NEXT,
        "issues": [{
            "id": it["id"], "gap_type": it["gap_type"], "module": it["module"],
            "role_id": it["role_id"], "severity": it["severity"],
            "status": it["status"], "priority": it["priority"],
            "assignee": it["assignee"], "last_seen": it["last_seen"],
            "title": it["title"][:80],
        } for it in hc.store.list_issues(cycle_id=cid_effective, view="open",
                                         limit=500)],
        "views_counts": {v: len(items) for v, items in views.items()},
    }
    hc.store.close()
    return payload


def _issue_detail(project_path: str, issue_id: str) -> Dict[str, Any]:
    """单工作项详情：基础字段 + 差距快照证据 + 活动日志。"""
    hc = HealthCycle(project_path)
    iss = hc.store.get_issue(issue_id)
    if iss is None:
        hc.store.close()
        return {"ok": False, "message": "工作项不存在"}
    events = hc.store.issue_events(issue_id, limit=50)
    hc.store.close()
    try:
        snapshot = json.loads(iss.get("snapshot") or "{}")
    except Exception:  # noqa: BLE001
        snapshot = {}
    return {"ok": True, "issue": iss, "snapshot": snapshot, "events": events}


def _spark_svg(pts: list, w: int = 560, h: int = 150) -> str:
    if not pts:
        return "<p style='color:#94a3b8'>暂无跨期数据（至少需 1 个已关闭周期）</p>"
    vmax = max(pts) or 1
    step = w / max(len(pts) - 1, 1)
    coords = [(i * step, h - 16 - (v / vmax) * (h - 32)) for i, v in enumerate(pts)]
    poly = " ".join(f"{x:.1f},{y:.1f}" for x, y in coords)
    dots = "".join(f"<circle cx='{x:.1f}' cy='{y:.1f}' r='4' fill='#3b82f6'/>"
                   for x, y in coords)
    labels = "".join(
        f"<text x='{x:.1f}' y='{h}' fill='#94a3b8' font-size='10' "
        f"text-anchor='middle'>C{i + 1}</text>"
        for i, (x, _y) in enumerate(coords))
    return (f"<svg width='{w}' height='{h}' viewBox='0 0 {w} {h}'>"
            f"<polyline points='{poly}' fill='none' stroke='#3b82f6' "
            f"stroke-width='2.5'/>{dots}{labels}</svg>")


def render_board(project_path: str, output_dir: str = "",
                 cid: str = "", open_browser: bool = False,
                 host: str = "127.0.0.1", port: int = 0,
                 interactive: bool = True) -> Dict[str, Any]:
    """生成可交互看板 HTML；写出（可选）+ 附 JSON 载荷。

    interactive=True（默认）启用前端流转回写交互；False 退化为只读查看。
    """
    payload = _build_payload(project_path, cid)
    payload["interactive"] = interactive
    # 内联进 <script> 的 JSON 必须转义 < > &，防止治理数据里的 </script> 造成 XSS
    data_json = (json.dumps(payload, ensure_ascii=False, default=str)
                 .replace("<", "\\u003c").replace(">", "\\u003e")
                 .replace("&", "\\u0026"))
    cyc = payload.get("active_cycle") or {}
    summary = payload.get("summary") or {}
    counts = payload.get("status_counts") or {}
    trend = payload.get("trend") or []
    status_bars = "".join(
        f"<div style='width:{v / max(sum(counts.values()), 1) * 100:.1f}%'>"
        f"<span>{_esc(k)}</span> {v}</div>"
        for k, v in counts.items())
    cycle_opts = "".join(
        f"<option value='{_esc(cy.get('id', ''))}'"
        f"{' selected' if cy.get('id') == cyc.get('id') else ''}>"
        f"{_esc(cy.get('name', ''))} ({_esc(cy.get('id', ''))})</option>"
        for cy in payload.get("cycles", []))
    status_opts = "".join(f"<option>{_esc(_s)}</option>" for _s in ALL_STATUSES)
    role_opts = "".join(
        f"<option>{_esc(r)}</option>"
        for r in _sorted_dedup([i.get("role_id", "") for i in payload.get("issues", [])]))
    mode_note = ('<span style="color:#16a34a">交互已启用 · 流转写回治理库</span>'
                 if interactive else
                 '<span style="color:#64748b">只读模式（interactive=false）</span>')

    css = """
    body{font-family:-apple-system,'Segoe UI',Roboto,sans-serif;background:#f6f8fb;
         color:#0f172a;margin:0;padding:24px}
    .wrap{max-width:1180px;margin:0 auto}
    .card{background:#fff;border:1px solid #e2e8f0;border-radius:14px;padding:18px;
          margin:14px 0;box-shadow:0 1px 2px rgba(15,23,42,.04)}
    h1{font-size:24px;margin:0 0 4px} h2{font-size:15px;color:#334155;margin:0 0 12px}
    .kpi{display:flex;gap:14px;flex-wrap:wrap}
    .kpi .box{flex:1;min-width:120px;background:#fff;border:1px solid #e2e8f0;
              border-radius:12px;padding:14px;text-align:center}
    .kpi .v{font-size:30px;font-weight:800} .kpi .k{font-size:12px;color:#64748b}
    .bars div{border-radius:6px;background:#3b82f6;color:#fff;padding:3px 8px;
              margin:3px 0;font-size:12px;min-width:24px}
    .toolbar{display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin-bottom:10px}
    select,input{font:inherit;padding:5px 8px;border:1px solid #cbd5e1;border-radius:8px}
    button{border:1px solid #cbd5e1;background:#fff;border-radius:8px;padding:5px 12px;
           cursor:pointer;font-size:12px}
    button.t{font-size:11px;padding:2px 8px;margin-right:4px;border-radius:999px}
    button.t:hover{background:#eef2ff}
    .pill{display:inline-block;padding:2px 10px;border-radius:999px;color:#fff;
          font-size:12px}
    .st-Detected{background:#64748b}.st-Confirmed{background:#2563eb}
    .st-Fixing{background:#d97706}.st-Verified{background:#16a34a}
    .st-Archived{background:#94a3b8}.st-Rejected{background:#dc2626}
    .sev-high{color:#dc2626;font-weight:700}.sev-medium{color:#d97706}.sev-low{color:#64748b}
    table{border-collapse:collapse;width:100%;font-size:13px}
    th,td{border:1px solid #e2e8f0;padding:6px 10px;text-align:left}
    th{background:#f1f5f9;position:sticky;top:0}
    tr.click{cursor:pointer} tr.click:hover{background:#f8fafc}
    .row-exp td{background:#f1f5f9;border-top:2px solid #cbd5e1}
    .ev{font-size:12px;color:#334155;margin:3px 0;border-left:3px solid #cbd5e1;
        padding-left:8px}
    .snap{font-size:12px;color:#475569;background:#eef2ff;border-radius:8px;
          padding:8px;white-space:pre-wrap;word-break:break-word;max-height:180px;
          overflow:auto}
    #toast{position:fixed;right:18px;top:18px;background:#0f172a;color:#fff;
           padding:10px 16px;border-radius:10px;font-size:13px;opacity:0;
           transition:.25s;z-index:9}
    .grp{font-size:12px;color:#64748b;display:flex;gap:14px;flex-wrap:wrap}
    """

    js = """
    const DATA = window.__DATA__;
    let current = DATA.issues;
    function esc(s){return String(s??'').replace(/[&<>"]/g,
      c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));}
    function toast(m,ok){const t=document.getElementById('toast');
      t.textContent=m;t.style.opacity=1;t.style.background=ok?'#16a34a':'#dc2626';
      setTimeout(()=>t.style.opacity=0,2200);}
    function nextBtn(id,st){
      const allowed=(DATA.allowed_next||{})[st]||[];
      if(!DATA.interactive||!allowed.length) return '';
      return allowed.map(t=>`<button class="t" data-to="${esc(t)}"
        data-id="${esc(id)}" onclick="event.stopPropagation();act(this)">→${esc(t)}</button>`).join('');}
    function rows(list){
      return list.map((i,idx)=>`<tr class="click" data-idx="${idx}"
        onclick="toggle(this)">
        <td>${esc(i.id.slice(0,8))}…</td><td class="sev-${esc(i.severity)}">${esc(i.gap_type)}</td>
        <td>${esc(i.module||i.role_id||'-')}</td><td class="sev-${esc(i.severity)}">${esc(i.severity)}</td>
        <td><span class="pill st-${esc(i.status)}">${esc(i.status)}</span></td>
        <td>${esc(i.priority)}</td><td>${esc(i.assignee||'-')}</td>
        <td>${nextBtn(i.id,i.status)}</td></tr>
        <tr class="row-exp" style="display:none"><td colspan="8"></td></tr>`)
        .join('');}
    function render(){
      const st=document.getElementById('f-status').value,sv=document.getElementById('f-sev').value,
            role=document.getElementById('f-role').value;
      current=DATA.issues.filter(i=>(!st||i.status===st)&&(!sv||i.severity===sv)&&(!role||i.role_id===role));
      document.getElementById('board-body').innerHTML=rows(current);
      document.getElementById('cnt').textContent=current.length+' / '+DATA.issues.length;}
    async function act(btn){
      const id=btn.dataset.id, to=btn.dataset.to;
      const j=await post('/api/transition',{issue_id:id,to_state:to,actor:'board'});
      if(j&&j.ok){toast('→ '+to+' ✓',true);setTimeout(()=>location.reload(),500);}
      else toast((j&&j.message)||'流转失败',false);}
    async function post(url,obj){
      try{const r=await fetch(url,{method:'POST',headers:{'Content-Type':'application/json'},
        body:JSON.stringify(obj)});return await r.json();}
      catch(e){toast('请求失败: '+e,false);return null;}}
    async function toggle(tr){
      const i=current[tr.dataset.idx];
      const exp=tr.nextElementSibling;
      if(!i) return;
      if(exp.style.display!=='none'){exp.style.display='none';return;}
      exp.style.display='';exp.innerHTML='<p style="color:#94a3b8">加载详情…</p>';
      try{const d=await (await fetch('/api/issue/'+encodeURIComponent(i.id))).json();
        if(!d.ok){exp.innerHTML='<p>'+esc(d.message||'加载失败')+'</p>';return;}
        const snap=JSON.stringify(d.snapshot, null, 1);
        const evs=(d.events||[]).map(e=>`<div class="ev">[${esc(e.at)}]
          ${esc(e.action)} · ${esc(e.actor||'-')} — ${esc(e.detail||'')}</div>`).join('')
          ||'<div class="ev">暂无活动日志</div>';
        exp.innerHTML=`<h3 style="font-size:13px;margin:4px 0">详情 · ${esc(i.id)}</h3>
          <div class="grp"><span>模块 ${esc(i.module||'-')}</span><span>角色 ${esc(i.role_id||'-')}</span>
          <span>状态 <b>${esc(i.status)}</b></span><span>严重级 ${esc(i.severity)}</span>
          <span>标题 ${esc(i.title)}</span></div>
          <p style="font-size:12px;color:#334155">差距证据快照：</p><pre class="snap">${esc(snap)}</pre>
          <p style="font-size:12px;color:#334155">活动日志：</p>${evs}`;}
      catch(err){exp.innerHTML='<p>详情加载失败: '+esc(err)+'</p>';}}
    function pickCyc(){location.reload();}
    document.addEventListener('DOMContentLoaded',render);
    """

    html_str = f"""<!DOCTYPE html><html lang="zh"><head><meta charset="utf-8">
<title>架构治理看板 · {_esc(payload.get('project_path', ''))}</title>
<style>{css}</style></head><body>
<script>window.__DATA__ = {data_json};</script>
<div id="toast"></div>
<div class="wrap">
<h1>架构治理看板</h1>
<p style="color:#64748b">CodeRef-AI 5.2 定期体检 · 工作视图 · 跨期趋势 · {mode_note}</p>

<div class="card"><h2>本期体检 {_esc(cyc.get('name', ''))} ({_esc(cyc.get('id', ''))})</h2>
<div class="kpi">
  <div class="box"><div class="v">{summary.get('completion_rate', 0):.0%}</div>
    <div class="k">完成率</div></div>
  <div class="box"><div class="v">{summary.get('remaining', 0)}</div>
    <div class="k">剩余</div></div>
  <div class="box"><div class="v">{summary.get('total', 0)}</div>
    <div class="k">本期总数</div></div>
  <div class="box"><div class="v">{summary.get('recurred', 0)}</div>
    <div class="k">复发</div></div>
</div></div>

<div class="card"><h2>状态分布</h2><div class="bars">{status_bars}</div></div>

<div class="card"><h2>跨期趋势（各已关闭周期的整改量）</h2>
{_spark_svg([t.get('done', 0) for t in trend])}
<p style="color:#94a3b8;font-size:12px">横轴 C1..CN 为已关闭体检周期，纵轴为该期完成（归档+复验达标）数。</p>
</div>

<div class="card"><h2>治理工作项 <span style="color:#94a3b8;font-size:12px">已筛 <b id="cnt"></b></span></h2>
<div class="toolbar">
  <label>周期 <select id="f-cycle" onchange="pickCyc()">{cycle_opts}</select></label>
  <label>状态 <select id="f-status" onchange="render()"><option value="">全部</option>{status_opts}</select></label>
  <label>严重级 <select id="f-sev" onchange="render()">
    <option value="">全部</option><option>high</option><option>medium</option><option>low</option>
  </select></label>
  <label>角色 <select id="f-role" onchange="render()">
    <option value="">全部</option>{role_opts}</select></label>
</div>
<table><thead><tr><th>ID</th><th>类型</th><th>模块/角色</th><th>严重级</th>
<th>状态</th><th>优先级</th><th>负责人</th><th>流转</th></tr></thead>
<tbody id="board-body"></tbody></table>
<p style="color:#94a3b8;font-size:12px;margin-top:6px">点击行查看详情（差距证据 + 活动日志）；流转按钮写回治理库（仅本机 127.0.0.1）</p>
</div>

<div class="card"><p style="color:#94a3b8;font-size:12px">接口：
/api/report · /api/issues?view=… · /api/cycles · /api/issue/&lt;id&gt; · POST /api/transition</p></div>
</div>
<script>{js}</script></body></html>"""

    if output_dir:
        os.makedirs(output_dir, exist_ok=True)
        path = os.path.join(output_dir, "gov_board.html")
        with open(path, "w", encoding="utf-8") as f:
            f.write(html_str)
        payload["board_html"] = path
    payload["board_html_str"] = html_str

    if open_browser:
        payload["serve"] = serve(project_path, host, port)
    return payload


# ────────────────────────────────────────────────────────────────
# 只读 + 可交互服务
# ────────────────────────────────────────────────────────────────

def _make_handler(project_path: str):
    payload_cache: Dict[str, Any] = {}

    class Handler(BaseHTTPRequestHandler):
        def _json(self, obj, code=200):
            body = json.dumps(obj, ensure_ascii=False, default=str).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            try:
                self._route("GET")
            except Exception as e:  # noqa: BLE001
                logger.error(f"board api 异常: {e}")
                self._json({"ok": False, "error": str(e)}, 500)

        def do_POST(self):
            try:
                self._route("POST")
            except Exception as e:  # noqa: BLE001
                logger.error(f"board api 异常: {e}")
                self._json({"ok": False, "error": str(e)}, 500)

        def _host_allowed(self) -> bool:
            h = (self.client_address[0] or "").lower()
            return h in ("127.0.0.1", "::1", "localhost")

        def _read_body(self) -> Dict[str, Any]:
            try:
                ln = int(self.headers.get("Content-Length") or "0")
            except Exception:  # noqa: BLE001
                ln = 0
            raw = self.rfile.read(ln) if ln else b"{}"
            try:
                return json.loads(raw.decode("utf-8") or "{}")
            except Exception:  # noqa: BLE001
                return {}

        def _route(self, name):
            path = (self.path or "").split("?")[0].rstrip("/") or "/"
            qs = parse_qs((self.path or "").split("?", 1)[1]
                          if "?" in (self.path or "") else "")
            # 所有 API 端点（读+写）仅允许本机访问，防止治理数据暴露到局域网
            if path.startswith("/api/") and not self._host_allowed():
                return self._json({"ok": False,
                                   "message": "治理看板接口仅允许本机访问(127.0.0.1)"},
                                  403)
            if "api" not in payload_cache:
                payload_cache["api"] = _build_payload(project_path)
            api = payload_cache["api"]

            if name == "GET" and path == "/api/report":
                return self._json({"ok": True,
                                   "summary": api["summary"],
                                   "status_counts": api["status_counts"],
                                   "trend": api["trend"],
                                   "cycles": api["cycles"],
                                   "active_cycle": api["active_cycle"]})
            if name == "GET" and path == "/api/cycles":
                return self._json({"ok": True, "cycles": api["cycles"]})
            if name == "GET" and path == "/api/issues":
                view = (qs.get("view") or ["open"])[0]
                hc = HealthCycle(project_path)
                items = hc.store.list_issues(view=view, limit=200)
                hc.store.close()
                return self._json({"ok": True, "view": view, "count": len(items),
                                   "issues": items})
            if name == "GET" and path.startswith("/api/issue/"):
                iid = path[len("/api/issue/"):]
                return self._json(_issue_detail(project_path, iid))
            if name == "POST" and path == "/api/transition":
                if not self._host_allowed():
                    return self._json({"ok": False,
                                       "message": "流转回写仅允许本机(127.0.0.1)"}, 403)
                body = self._read_body()
                return self._json(_transition(project_path, body))
            if path in ("/", "/board", "/index.html"):
                return self._json({"ok": True,
                                   "message": ("看板 HTML 用 render_board(open_browser=True) "
                                               "生成，或直接用接口取数据"),
                                   "endpoints": ["/api/report", "/api/issues",
                                                 "/api/cycles", "/api/issue/<id>",
                                                 "POST /api/transition"]})
            return self._json({"ok": False, "error": f"404 {path}"}, 404)

        def log_message(self, *a):
            pass

    return Handler


def _transition(project_path: str, body: Dict[str, Any]) -> Dict[str, Any]:
    """把前端流转写回治理库，与 coderef_gov_transition 同语义。"""
    iid = body.get("issue_id") or ""
    if not iid:
        return {"ok": False, "message": "缺少 issue_id"}
    hc = HealthCycle(project_path)
    action = body.get("action", "transition")
    try:
        if action == "reject":
            r = hc.reject_issue(iid, reason=body.get("reason", "board 豁免"),
                                actor=body.get("actor", "board"))
        elif action == "meta":
            r = hc.set_issue_meta(iid, priority=body.get("priority"),
                                  assignee=body.get("assignee"),
                                  actor=body.get("actor", "board"))
        else:
            to_state = body.get("to_state") or ""
            if not to_state:
                return {"ok": False, "message": "transition 需提供 to_state"}
            r = hc.transition_issue(iid, to_state,
                                    actor=body.get("actor", "board"),
                                    detail=body.get("detail", "看板流转"))
    finally:
        hc.store.close()
    r["tool"] = "coderef_gov_board"
    return r


def serve(project_path: str, host: str = "127.0.0.1", port: int = 0) -> Dict[str, Any]:
    """启动可交互治理看板服务（线程守护），返回访问地址。

    仅允许回环绑定（127.0.0.1 / ::1 / localhost），防止治理数据暴露到局域网；
    服务句柄存入模块级 _SERVERS 注册表，返回值只含可 JSON 序列化元数据。
    """
    if host not in ("127.0.0.1", "::1", "localhost"):
        logger.warning(f"治理看板仅允许回环绑定，已强制 127.0.0.1（收到 host={host}）")
        host = "127.0.0.1"
    handler = _make_handler(project_path)
    httpd = ThreadingHTTPServer((host, port), handler)
    actual_port = httpd.server_address[1]
    url = f"http://{host}:{actual_port}"
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    logger.info(f"治理看板服务已启动: {url}")
    _SERVERS[project_path] = {"httpd": httpd, "thread": t, "url": url,
                              "port": actual_port}
    return {"ok": True, "url": url, "host": host, "port": actual_port,
            "interactive": True,
            "endpoints": ["/api/report", "/api/issues", "/api/cycles",
                          "/api/issue/<id>", "POST /api/transition"]}