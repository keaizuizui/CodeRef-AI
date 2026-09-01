# -*- coding: utf-8 -*-
"""
ProjectOverview —— CodeRef 项目总览报告（gov_report action=overview）

把 CodeRef-AI 散落的五样高价值产物聚合成一份自包含 HTML，一屏讲清项目：
  ① 一句话体检结论（健康分 + 治理差距/豁免结论，顶栏大字）
  ② 架构图（iframe 引用 arch_canvas_*.html；未生成则诚实占位）
  ③ 项目 wiki 介绍（WIKI_INDEX/READM/OVERVIEW 核心内联 + 全库链接）
  ④ 人话解读摘要（高危清单 + 分项计数 + 确定性总结）
  ⑤ 治理工作项（表格含完整标题列 + 详情内联展开 + 全量统计）

设计原则：
  - 静态自包含：全部数据内联 window.__DATA__，详情展开不做 fetch；
    file:// 打开即有全部信息（根治 gov_board 静态打不开的缺陷）。
  - 确定性诚实：结论全部来自确定性原语（健康分/审计/图谱/差距/豁免），
    缺失数据源显示"需先运行 XX"占位，不臆造、不空转。
  - 复用不重造：只读取既有模块（interpretation_platform / gov_webdash /
    wiki_generator / canvas_generator）的产出，不重复实现分析逻辑。
  - 零依赖：单文件 HTML + 内联 CSS/JS，无 CDN/工程化依赖。
"""

import html
import json
import os
import re
from datetime import datetime
from typing import Any, Dict, List, Optional

from loguru import logger


def _esc(s: Any) -> str:
    return html.escape(str(s if s is not None else ""))


def _health_color(score: Optional[int]) -> str:
    if score is None:
        return "#94a3b8"
    if score >= 90:
        return "#22c55e"
    if score >= 70:
        return "#eab308"
    return "#ef4444"


# ────────────────────────────────────────────────────────────────
# 轻量 Markdown → HTML（仅覆盖 wiki 常用子集，不引第三方）
# ────────────────────────────────────────────────────────────────

_CODE_FENCE = re.compile(r"```(\w*)\n(.*?)```", re.S)


def _safe_href(url: str) -> bool:
    """Markdown 链接仅允许相对路径与 http/https，拒绝 javascript:/data: 等。"""
    if not url or "://" in url:
        return url.lower().startswith(("http://", "https://")) if "://" in url else False
    if ":" in url:
        return False
    return True


def _link_repl(m: "re.Match") -> str:
    label, url = m.group(1), m.group(2)
    if not _safe_href(url):
        return m.group(0)
    return f'<a href="{url}" style="color:#2563eb">{label}</a>'


def _inline_md(text: str) -> str:
    """行内 Markdown：code/bold/italic/链接（链接 scheme 白名单）。"""
    text = re.sub(r"`([^`]+)`", r"<code>\1</code>", text)
    text = re.sub(r"\*\*([^*]+)\*\*", r"<b>\1</b>", text)
    text = re.sub(r"(?<!\*)\*([^*\n]+)\*(?!\*)", r"<i>\1</i>", text)
    text = re.sub(r"\[([^\]]+)\]\(([^)\s]+)\)", _link_repl, text)
    return text


def _md_lines_to_html(line: str) -> str:
    """把单个 md 行转成 html（标题/列表/引用/分隔/普通段，含行内格式）。"""
    line = line.rstrip()
    if line.startswith("### "):
        return f"<h4>{_inline_md(_esc(line[4:]))}</h4>"
    if line.startswith("## "):
        return f"<h3>{_inline_md(_esc(line[3:]))}</h3>"
    if line.startswith("# "):
        return f"<h2>{_inline_md(_esc(line[2:]))}</h2>"
    if re.match(r"^\s*[-*+]\s+", line):
        return f"<li>{_inline_md(_esc(re.sub(r'^\s*[-*+]\s+', '', line)))}</li>"
    if line.strip() in ("---", "***"):
        return "<hr>"
    if line.startswith("> "):
        return f"<blockquote>{_inline_md(_esc(line[2:]))}</blockquote>"
    if not line.strip():
        return ""
    return f"<p>{_inline_md(_esc(line))}</p>"


_TABLE_SEP = re.compile(r"^\s*\|[\s:\-|]+\|\s*$")


def _table_html(table_lines: List[str]) -> str:
    """把连续的 markdown 表格行转成 html table。"""
    rows = [ln.strip().strip("|") for ln in table_lines]
    cells = [[c.strip() for c in r.split("|")] for r in rows]
    # 第二行若为分隔行 [|:--|--|] 则跳过
    if (len(cells) >= 2 and cells[1]
            and all(re.fullmatch(r"[\s\-:]+", c) for c in cells[1])):
        head, body = cells[0], cells[2:]
    else:
        head, body = cells[0] if cells else [], cells[1:]
    thead = "<tr>" + "".join(
        "<td style='background:#f1f5f9;font-weight:600'>"
        f"{_inline_md(_esc(h))}</td>" for h in head) + "</tr>"
    tbody = "".join(
        "<tr>" + "".join(f"<td>{_inline_md(_esc(c))}</td>" for c in r)
        + "</tr>" for r in body)
    if not body:
        tbody = ("<tr><td style='color:#94a3b8' colspan='"
                 + str(max(len(head), 1)) + "'>（空表）</td></tr>")
    return ("<table style='border-collapse:collapse;font-size:13px'>"
            + thead + tbody + "</table>")


def _md_to_html(md: str) -> str:
    """轻量 Markdown → HTML：代码块 → pre、表格 → table、其余逐行块级。

    自动跳过 YAML front matter；行内支持 code/bold/italic/链接。
    代码块用占位符替换避免被行级处理包裹进 <p>。
    """
    if not md:
        return "<p style='color:#94a3b8'>（无内容）</p>"

    placeholders: Dict[str, str] = {}

    def _fence_to_placeholder(m: "re.Match") -> str:
        body = m.group(2)
        html = ("<pre style='background:#0f172a;color:#e2e8f0;border-radius:8px;"
                "padding:10px;overflow:auto;font-size:12px'>"
                f"{_esc(body)}</pre>")
        key = f"\x00FENCE_{len(placeholders):X}\x00"
        placeholders[key] = html
        return key

    md = _CODE_FENCE.sub(_fence_to_placeholder, md)
    lines = md.splitlines()
    # 跳过 YAML front matter
    if lines and lines[0].strip() == "---":
        for i in range(1, len(lines)):
            if lines[i].strip() == "---":
                lines = lines[i + 1:]
                break
    out: List[str] = []
    in_list = False
    i = 0
    while i < len(lines):
        ln = lines[i]
        stripped = ln.strip()
        if stripped in placeholders:
            if in_list:
                out.append("</ul>")
                in_list = False
            out.append(placeholders[stripped])
            i += 1
            continue
        if stripped.startswith("|") and stripped.endswith("|"):
            tbl = []
            while (i < len(lines) and lines[i].strip().startswith("|")
                   and lines[i].strip().endswith("|")):
                tbl.append(lines[i])
                i += 1
            out.append(_table_html(tbl))
            continue
        block = _md_lines_to_html(ln)
        if block.startswith("<li>"):
            if not in_list:
                out.append("<ul style='padding-left:18px;margin:6px 0'>")
                in_list = True
            out.append(block)
        else:
            if in_list:
                out.append("</ul>")
                in_list = False
            if block:
                out.append(block)
        i += 1
    if in_list:
        out.append("</ul>")
    return "".join(out)


# ────────────────────────────────────────────────────────────────
# 数据源聚合
# ────────────────────────────────────────────────────────────────

def _health_verdict(project_path: str) -> Dict[str, Any]:
    """健康 + 人话解读摘要（可复用：① 体检结论 + ④ 人话解读）。"""
    from core.interpretation_platform import InterpretationPlatform
    try:
        return InterpretationPlatform().interpret(project_path, action="health")
    except Exception:  # noqa: BLE001
        logger.warning(f"overview: interpret(health) 失败: {project_path}")
        return {"ok": False, "action": "health", "score": None,
                "summary": "人话解读不可用。"}


def _govern_data(project_path: str, cid: str = "") -> Dict[str, Any]:
    """治理数据（周期/计数/工作项），复用 gov_webdash 聚合，补充详情内联。"""
    from core.gov_webdash import _build_payload, _issue_detail
    payload = _build_payload(project_path, cid)
    issues = payload.get("issues", [])
    details: Dict[str, Dict[str, Any]] = {}
    for it in issues:
        d = _issue_detail(project_path, it["id"])
        if d.get("ok"):
            # 事件只取近 20 条，控制内联体积
            d["events"] = d.get("events", [])[-20:]
            details[it["id"]] = d
    payload["issue_details"] = details
    return payload


def _find_arch_canvas(project_path: str) -> Optional[str]:
    """找到最新生成的架构画布**绝对路径**；无则 None。"""
    cfg = os.path.join(project_path, ".coderef")
    if not os.path.isdir(cfg):
        return None
    cands = [f for f in os.listdir(cfg)
             if f.startswith("arch_canvas_") and f.endswith(".html")]
    if not cands:
        return None
    cands.sort(key=lambda f: os.path.getmtime(os.path.join(cfg, f)),
               reverse=True)
    return os.path.join(cfg, cands[0])


def _wiki_href(wiki_abs: str, out_dir: str) -> str:
    """wiki 文件相对产物的 href；跨盘符时回退为 file:// 绝对路径。"""
    try:
        rel = os.path.relpath(wiki_abs, out_dir).replace(os.sep, "/")
        return _esc(rel)
    except ValueError:  # 跨盘符无相对路径
        return _esc("file:///" + wiki_abs.replace(os.sep, "/"))


def _wiki_sections(project_path: str) -> Dict[str, Any]:
    """聚合 wiki 概览：读 WIKI_INDEX + README/OVERVIEW，其余列链接。"""
    wiki_dir = os.path.join(project_path, "docs", "wiki")
    if not os.path.isdir(wiki_dir):
        return {"available": False, "index_html": "",
                "overview_html": "", "files": []}

    def _read(name: str, max_chars: int = 4000) -> str:
        p = os.path.join(wiki_dir, name)
        if not os.path.isfile(p):
            return ""
        try:
            with open(p, "r", encoding="utf-8") as f:
                return f.read(max_chars + 2000)
        except Exception:  # noqa: BLE001
            return ""

    index_html = _md_to_html(_read("WIKI_INDEX.md"))
    # 概述优先 OVERVIEW.md，回退 README.md
    overview_md = _read("OVERVIEW.md") or _read("README.md")
    overview_html = _md_to_html(overview_md[:4000])
    files = sorted(f for f in os.listdir(wiki_dir) if f.endswith(".md"))
    return {"available": True, "index_html": index_html,
            "overview_html": overview_html, "files": files,
            "directory": "docs/wiki/"}


def _resolve_verdict_text(payload: Dict[str, Any]) -> str:
    """顶栏一句治理结论：优先取当前周期描述，回退说明。"""
    cyc = (payload.get("active_cycle") or {})
    desc = (cyc.get("description") or "").strip()
    if desc:
        return desc
    return "未建立治理周期项目，运行 coderef_gov_start 建立体检基线。"


def render_overview(project_path: str, output_dir: str = "",
                    cid: str = "", interactive: bool = True) -> Dict[str, Any]:
    """生成并写出自包含项目总览 HTML，返回路径 + 各区块就绪状态。"""
    payload = _govern_data(project_path, cid)
    health = _health_verdict(project_path)
    wiki = _wiki_sections(project_path)
    arch = _find_arch_canvas(project_path)
    payload["health"] = health
    payload["wiki"] = wiki
    payload["arch_canvas"] = os.path.basename(arch) if arch else None
    payload["interactive"] = interactive
    payload["generated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # 输出目录前置计算（wiki 全库链接需相对最终产物所在目录）
    out_dir = output_dir or os.path.join(os.path.abspath(project_path), ".coderef")
    os.makedirs(out_dir, exist_ok=True)

    data_json = (json.dumps(payload, ensure_ascii=False, default=str)
                 .replace("<", "\\u003c").replace(">", "\\u003e")
                 .replace("&", "\\u0026"))

    score = health.get("score")
    tally = health.get("tally") or {}
    cycle = payload.get("active_cycle") or {}
    cycle_label = f"{_esc(cycle.get('name',''))} ({_esc(cycle.get('id',''))})" \
        if cycle.get("id") else "（无活动周期）"
    status_opts = "".join(
        f"<option>{_esc(_s)}</option>"
        for _s in ["Detected", "Confirmed", "Fixing", "Verified",
                   "Archived", "Rejected"])

    mode_note = ('<span style="color:#22c55e">交互已启用 · 流转写回治理库</span>'
                 if interactive else
                 '<span style="color:#64748b">只读模式（interactive=false）</span>')

    # ── ① 顶栏结论 ──
    verdict = _resolve_verdict_text(payload)
    vc = payload.get("views_counts") or {}
    score_html = (f"<span style='color:{_health_color(score)}'>"
                  f"{_esc(score)}</span>/100" if score is not None
                  else "<span style='color:#94a3b8'>未审计</span>")
    score_label = health.get("score_label") or "未审计"
    tally_html = " · ".join(
        f"{_esc(k)} {int(tally.get(k, 0))}"
        for k in ("high", "medium", "low") if tally.get(k))

    # ── ② 架构图 ──
    if arch:
        arch_basename = os.path.basename(arch)
        arch_html = (
            f"<iframe src='{_wiki_href(arch, out_dir)}' title='架构画布' "
            "style='width:100%;height:520px;border:1px solid #e2e8f0;"
            "border-radius:12px;background:#fff'></iframe>"
            f"<p style='color:#94a3b8;font-size:12px'>画布 {_esc(arch_basename)}；"
            "浏览器打开可拖拽编辑、导出目标架构 JSON。</p>")
    else:
        arch_html = ("<p style='color:#64748b'>尚未生成架构画布。"
                     "运行 <code>coderef_arch_canvas</code> 生成交互式架构图后，"
                     "此区块将自动内嵌。</p>")

    # ── ③ wiki ──
    if wiki.get("available"):
        wiki_dir = os.path.join(project_path, "docs", "wiki")
        file_links = "".join(
            f"<a href='{_wiki_href(os.path.join(wiki_dir, f), out_dir)}' "
            f"target='_blank' style='margin-right:12px;font-size:12px;color:#2563eb'>"
            f"{_esc(f)}</a>" for f in wiki.get("files", []))
        wiki_html = (
            f"<div style='display:flex;gap:16px;flex-wrap:wrap'>"
            f"<div style='flex:1;min-width:280px'>{wiki['overview_html']}</div>"
            f"<div style='flex:1;min-width:220px'>"
            f"<div style='font-size:13px;font-weight:600;color:#334155'>索引</div>"
            f"{wiki['index_html'] or '<p style=\"color:#94a3b8;font-size:12px\">（无索引）</p>'}"
            f"</div></div>"
            f"<div style='margin-top:10px'>{file_links}</div>")
    else:
        wiki_html = ("<p style='color:#64748b'>尚未生成项目 wiki。"
                     "运行 <code>coderef_docs</code> 生成 wiki 文档后，"
                     "此区块将展示项目介绍与模块索引。</p>")

    # ── ④ 人话解读摘要 ──
    risks = health.get("top_risks") or []
    risk_html = "".join(
        f"<li>{_esc(r)}</li>" for r in risks[:8]) or \
        "<li style='color:#94a3b8'>无高危项或无审计数据</li>"
    health_summary = health.get("summary") or ""

    html_str = f"""<!DOCTYPE html><html lang="zh"><head><meta charset="utf-8">
<title>项目总览 · {_esc(payload.get('project_path',''))}</title>
<style>
body{{font-family:-apple-system,'Segoe UI',Roboto,sans-serif;background:#f6f8fb;
     color:#0f172a;margin:0;padding:24px}}
.wrap{{max-width:1180px;margin:0 auto}}
.card{{background:#fff;border:1px solid #e2e8f0;border-radius:14px;padding:18px;
      margin:14px 0;box-shadow:0 1px 2px rgba(15,23,42,.04)}}
h1{{font-size:24px;margin:0 0 4px}} h2{{font-size:15px;color:#334155;margin:0 0 12px}}
.verdict{{font-size:14px;color:#334155;background:#eef2ff;border-radius:10px;
         padding:12px 14px;margin-top:12px}}
.kpi{{display:flex;gap:14px;flex-wrap:wrap}}
.kpi .box{{flex:1;min-width:120px;background:#fff;border:1px solid #e2e8f0;
          border-radius:12px;padding:14px;text-align:center}}
.kpi .v{{font-size:26px;font-weight:800}} .kpi .k{{font-size:12px;color:#64748b}}
.toolbar{{display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin-bottom:10px}}
select,input{{font:inherit;padding:5px 8px;border:1px solid #cbd5e1;border-radius:8px}}
button{{border:1px solid #cbd5e1;background:#fff;border-radius:8px;padding:5px 12px;
       cursor:pointer;font-size:12px}}
button.t{{font-size:11px;padding:2px 8px;margin-right:4px;border-radius:999px}}
button.t:hover{{background:#eef2ff}}
.pill{{display:inline-block;padding:2px 10px;border-radius:999px;color:#fff;font-size:12px}}
.st-Detected{{background:#64748b}}.st-Confirmed{{background:#2563eb}}
.st-Fixing{{background:#d97706}}.st-Verified{{background:#16a34a}}
.st-Archived{{background:#94a3b8}}.st-Rejected{{background:#dc2626}}
.sev-high{{color:#dc2626;font-weight:700}}.sev-medium{{color:#d97706}}.sev-low{{color:#64748b}}
table{{border-collapse:collapse;width:100%;font-size:13px}}
th,td{{border:1px solid #e2e8f0;padding:6px 10px;text-align:left;vertical-align:top}}
th{{background:#f1f5f9;position:sticky;top:0}}
tr.click{{cursor:pointer}} tr.click:hover{{background:#f8fafc}}
.row-exp td{{background:#f1f5f9;border-top:2px solid #cbd5e1}}
.snap{{font-size:12px;color:#475569;background:#eef2ff;border-radius:8px;padding:8px;
       white-space:pre-wrap;word-break:break-word;max-height:180px;overflow:auto}}
.ev{{font-size:12px;color:#334155;margin:3px 0;border-left:3px solid #cbd5e1;padding-left:8px}}
#toast{{position:fixed;right:18px;top:18px;background:#0f172a;color:#fff;padding:10px 16px;
       border-radius:10px;font-size:13px;opacity:0;transition:.25s;z-index:9}}
ul{{margin:6pt 0;padding-left:20px}} li{{font-size:13px;margin:3px 0}}
.note{{color:#94a3b8;font-size:12px}}
</style></head><body>
<script>window.__DATA__ = {data_json};</script>
<div id="toast"></div>
<div class="wrap">
<h1>项目总览 · {_esc(payload.get('project_path',''))}</h1>
<p style="color:#64748b">CodeRef-AI 5.13 项目总览 · 健康 · 架构 · Wiki · 人话解读 · 治理工作项 · {mode_note}</p>

<div class="card"><h2>① 一句话体检结论</h2>
<div class="kpi">
  <div class="box"><div class="v">{score_html}</div><div class="k">健康分 · {_esc(score_label)}</div></div>
  <div class="box"><div class="v">{tally_html or '-'}</div><div class="k">高危/中危/低危</div></div>
  <div class="box"><div class="v">{int(vc.get('open', 0))}</div><div class="k">在途工作项</div></div>
  <div class="box"><div class="v" style="color:#22c55e">{int(vc.get('rejected', 0))}</div><div class="k">已豁免</div></div>
</div>
<div class="verdict">{_esc(verdict)}</div>
<p class="note" style="margin-top:10px">{_esc(health_summary)}</p>
</div>

<div class="card"><h2>② 项目架构图</h2>{arch_html}</div>

<div class="card"><h2>③ 项目 Wiki 介绍</h2>{wiki_html}</div>

<div class="card"><h2>④ 人话解读摘要 · 高危清单</h2>
<ul>{risk_html}</ul>
</div>

<div class="card"><h2>⑤ 治理工作项 <span style="color:#94a3b8;font-size:12px">已筛 <b id="cnt"></b></span></h2>
<div class="toolbar">
  <span style="color:#334155;font-size:13px">当前周期 <b>{cycle_label}</b></span>
  <label>状态 <select id="f-status" onchange="render()"><option value="">全部</option>{status_opts}</select></label>
  <label>严重级 <select id="f-sev" onchange="render()">
    <option value="">全部</option><option>high</option><option>medium</option><option>low</option>
  </select></label>
</div>
<table><thead><tr><th>ID</th><th>类型</th><th>标题</th><th>模块/角色</th><th>严重级</th>
<th>状态</th><th>优先级</th><th>负责人</th>{'<th>流转</th>' if interactive else ''}</tr></thead>
<tbody id="board-body"></tbody></table>
<p class="note" style="margin-top:6px">点击行展开详情（差距快照 + 活动日志，已内联静态可看）；{'流转按钮写回治理库（仅本机 127.0.0.1 服务）' if interactive else '只读模式，不提供流转'}</p>
</div>

<p class="note">生成时间 {_esc(payload.get('generated_at',''))}</p>
</div>
<script>
const DATA = window.__DATA__;
let current = DATA.issues;
function esc(s){{return String(s??'').replace(/[&<>"]/g,
  c=>({{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}}[c]));}}
function toast(m,ok){{const t=document.getElementById('toast');
  t.textContent=m;t.style.opacity=1;t.style.background=ok?'#16a34a':'#dc2626';
  setTimeout(()=>t.style.opacity=0,2200);}}
function nextBtn(id,st){{
  const allowed=(DATA.allowed_next||{{}})[st]||[];
  if(!DATA.interactive||!allowed.length) return '';
  return allowed.map(t=>`<button class="t" data-to="${{esc(t)}}"
    data-id="${{esc(id)}}" onclick="event.stopPropagation();act(this)">→${{esc(t)}}</button>`).join('');}}
function rows(list){{
  return list.map((i,idx)=>`<tr class="click" data-idx="${{idx}}" onclick="toggle(this)">
    <td>${{esc(i.id.slice(0,8))}}…</td><td class="sev-${{esc(i.severity)}}">${{esc(i.gap_type)}}</td>
    <td style="max-width:340px">${{esc(i.title)}}</td>
    <td>${{esc(i.module||i.role_id||'-')}}</td><td class="sev-${{esc(i.severity)}}">${{esc(i.severity)}}</td>
    <td><span class="pill st-${{esc(i.status)}}">${{esc(i.status)}}</span></td>
    <td>${{esc(i.priority)}}</td><td>${{esc(i.assignee||'-')}}</td>
    ${{DATA.interactive?`<td>${{nextBtn(i.id,i.status)}}</td>`:''}}</tr>
    <tr class="row-exp" style="display:none"><td colspan="${{DATA.interactive?9:8}}"></td></tr>`)
    .join('');}}
function render(){{
  const st=document.getElementById('f-status').value,sv=document.getElementById('f-sev').value;
  current=DATA.issues.filter(i=>(!st||i.status===st)&&(!sv||i.severity===sv));
  document.getElementById('board-body').innerHTML=rows(current);
  document.getElementById('cnt').textContent=current.length+' / '+DATA.issues.length;}}
function snapHtml(detail){{
  const snap=JSON.stringify(detail.snapshot,null,1);
  const evs=(detail.events||[]).map(e=>`<div class="ev">[${{esc(e.at)}}]
    ${{esc(e.action)}} · ${{esc(e.actor||'-')}} — ${{esc(e.detail||'')}}</div>`).join('')
    ||'<div class="ev">暂无活动日志</div>';
  return `<h3 style="font-size:13px;margin:4px 0">详情 · ${{esc(detail.issue?detail.issue.id:'')}}</h3>
    <p style="font-size:12px;color:#334155">差距证据快照：</p><pre class="snap">${{esc(snap)}}</pre>
    <p style="font-size:12px;color:#334155">活动日志：</p>${{evs}}`;}}
function toggle(tr){{
  const i=current[tr.dataset.idx];
  const exp=tr.nextElementSibling;
  if(!i) return;
  if(exp.style.display!=='none'){{exp.style.display='none';return;}}
  exp.style.display='';
  const detail=(DATA.issue_details||{{}})[i.id];
  exp.innerHTML=detail?snapHtml(detail):'<p style="color:#94a3b8">暂无详情</p>';}}
async function act(btn){{
  const id=btn.dataset.id, to=btn.dataset.to;
  try{{const r=await fetch('/api/transition',{{method:'POST',
    headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{issue_id:id,to_state:to,actor:'board'}})}});
    const j=await r.json();
    if(j&&j.ok){{toast('→ '+to+' ✓',true);setTimeout(()=>location.reload(),500);}}
    else toast((j&&j.message)||'流转失败',false);}}
  catch(e){{toast('需启动本地服务才能流转: '+e,false);}}}}
document.addEventListener('DOMContentLoaded',render);
</script></body></html>"""

    path = os.path.join(out_dir, "project_overview.html")
    with open(path, "w", encoding="utf-8") as f:
        f.write(html_str)
    logger.info(f"项目总览已写出: {path}")

    payload["tool"] = "coderef_gov_report"
    payload["overview_html"] = path
    payload["sections"] = {
        "health": bool(health.get("score") is not None),
        "arch_canvas": bool(arch),
        "wiki": wiki.get("available", False),
        "work_items": len(payload.get("issues", [])),
    }
    return payload