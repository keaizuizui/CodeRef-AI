# -*- coding: utf-8 -*-
"""
HtmlReportRenderer -- 把审计报告 / 知识图谱 / Wiki 文档统一渲染成自包含 HTML 报告站点。

背景：Coderef 的审计报告与 Wiki 都是 .md 文本，缺少"有效的前端"承载。本渲染器
把一次审计的全部产物（PipeResult.findings、图谱 get_stats()、Wiki 生成的 .md 文档）
聚合成一个可离线打开的 HTML 报告目录：

    coderef-report/
      index.html         ← 入口：概览 + 导航（含各章节锚点）
      audit.html         ← 审计发现明细（HIGH/MEDIUM/LOW/建议 表格）
      kg.html            ← 知识图谱统计 + 社区/边类型分布
      wiki.html          ← Wiki 文档（markdown 转 HTML）

设计约束：
  - 纯标准库，不新增第三方依赖（轻量 markdown 子集转换器自实现）；
  - 自包含：单目录内复制即可打开，不依赖外网 CDN；
  - 面向使用者的可读文本一律中文；
  - 异常不静默吞掉：渲染失败返回错误信息，外层可感知；
  - 视觉风格与 health_dashboard 保持一致（深色面板 + 卡片）。

作者: CodeRef-AI Team
版本: v1.0
"""

import json
import os
import re
import html as _html
from datetime import datetime
from typing import List, Optional


# ═══════════════════════════════════════════════════════════════════
# 轻量 markdown 子集转换（标题/粗体/行内代码/列表/表格/围栏代码块/链接）
# ═══════════════════════════════════════════════════════════════════

_FENCE_RE = re.compile(r"```(\w*)\n(.*?)```", re.DOTALL)
_MD_CODEBLOCK_PLACEHOLDER_RE = re.compile(r"^\x00CODEBLOCK(\d+)\x00$")
_MD_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+)$")
_MD_UL_ITEM_RE = re.compile(r"^[-*]\s+")
_MD_OL_ITEM_RE = re.compile(r"^\d+\.\s+(.+)$")
_MD_HR_DASH_RE = re.compile(r"^-{3,}$")
_MD_HR_STAR_RE = re.compile(r"^\*{3,}$")
_MD_TABLE_SEP_CELL_RE = re.compile(r":?-{2,}:?")


def _extract_code_blocks(md_text: str) -> tuple:
    """提取围栏代码块并替换为占位符，返回 (占位文本, 代码块列表)。"""
    blocks: List[str] = []

    def _hold(m):
        blocks.append(m.group(2))
        return f"\x00CODEBLOCK{len(blocks)-1}\x00"

    text = _FENCE_RE.sub(_hold, md_text)
    return text, blocks


def _md_flush_table(out_lines: List[str], table_rows: List[list]) -> None:
    """把累计的表格行渲染成 <table>（不足 2 行时丢弃，保持原逻辑）。"""
    if not table_rows:
        return
    rows = table_rows[:]
    table_rows.clear()
    if len(rows) < 2:
        return
    head = rows[0]
    body = rows[1:]
    out_lines.append("<table>")
    out_lines.append("<thead><tr>{}</tr></thead>".format(
        "".join(f"<th>{_esc(c.strip())}</th>" for c in head)))
    out_lines.append("<tbody>")
    for row in body:
        out_lines.append("<tr>{}</tr>".format(
            "".join(f"<td>{_inline(c.strip())}</td>" for c in row)))
    out_lines.append("</tbody></table>")


def _md_consume_table_line(stripped: str, i: int, table_rows: List[list]) -> int:
    """消费一行表格行，返回推进后的行号。

    分隔行 |---|---|：丢弃该行，保留表头行继续累计，
    等表体行到达后与表头一起 flush（避免表头被提前丢弃）。
    """
    cells = [c for c in stripped.strip("|").split("|")]
    if all(_MD_TABLE_SEP_CELL_RE.fullmatch(c.strip()) for c in cells if c.strip()):
        return i + 1
    table_rows.append(cells)
    return i + 1


def _md_render_plain_line(stripped: str, out_lines: List[str]) -> bool:
    """渲染标题/引用/列表/分割线等单行结构；命中返回 True。"""
    # 标题
    hm = _MD_HEADING_RE.match(stripped)
    if hm:
        lvl = len(hm.group(1))
        out_lines.append(f"<h{lvl}>{_inline(hm.group(2))}</h{lvl}>")
        return True

    # 引用
    if stripped.startswith(">"):
        out_lines.append("<blockquote>{}</blockquote>".format(_inline(stripped[1:].strip())))
        return True

    # 无序列表
    if _MD_UL_ITEM_RE.match(stripped):
        out_lines.append("<li class='ul'>{}</li>".format(_inline(re.sub(r"^[-*]\s+", "", stripped))))
        return True

    # 有序列表
    om = _MD_OL_ITEM_RE.match(stripped)
    if om:
        out_lines.append("<li class='ol'>{}</li>".format(_inline(om.group(1))))
        return True

    # 分割线
    if _MD_HR_DASH_RE.match(stripped) or _MD_HR_STAR_RE.match(stripped):
        out_lines.append("<hr>")
        return True

    return False


def _md_render_lines(text: str, blocks: List[str]) -> List[str]:
    """逐行渲染占位文本，返回 HTML 行列表（表格状态在内部维护）。"""
    out_lines: List[str] = []
    i = 0
    lines = text.splitlines()
    table_rows: List[list] = []

    while i < len(lines):
        raw = lines[i]
        line = raw.rstrip()
        stripped = line.strip()

        # 围栏代码块占位还原
        m = _MD_CODEBLOCK_PLACEHOLDER_RE.match(stripped)
        if m:
            _md_flush_table(out_lines, table_rows)
            code = blocks[int(m.group(1))]
            out_lines.append("<pre><code>{}</code></pre>".format(_esc(code)))
            i += 1
            continue

        # 表格
        if stripped.startswith("|"):
            i = _md_consume_table_line(stripped, i, table_rows)
            continue

        _md_flush_table(out_lines, table_rows)

        if _md_render_plain_line(stripped, out_lines):
            i += 1
            continue

        # 空行
        if not stripped:
            i += 1
            continue

        # 普通段落
        out_lines.append("<p>{}</p>".format(_inline(stripped)))
        i += 1

    _md_flush_table(out_lines, table_rows)
    return out_lines


def _wrap_list_items(html_body: str) -> str:
    """把连续同类 <li> 分别包进 <ul>（无序）/ <ol>（有序）。"""
    html_body = re.sub(
        r"((?:<li class='ol'>.*?</li>\n?)+)",
        lambda m: "<ol>" + re.sub(r"<li class='ol'>(.*?)</li>", r"<li>\1</li>", m.group(1)) + "</ol>",
        html_body)
    html_body = re.sub(
        r"((?:<li class='ul'>.*?</li>\n?)+)",
        lambda m: "<ul>" + re.sub(r"<li class='ul'>(.*?)</li>", r"<li>\1</li>", m.group(1)) + "</ul>",
        html_body)
    return html_body


def md_to_html(md_text: str) -> str:
    """把 markdown 子集转成 HTML（HTML 转义处理，防止注入）。"""
    if not md_text:
        return "<p class='empty'>（空文档）</p>"

    # 1. 提取围栏代码块，占位保护
    text, blocks = _extract_code_blocks(md_text)

    # 2. 逐行渲染（标题/引用/列表/表格/分割线/段落）
    out_lines = _md_render_lines(text, blocks)

    # 3. 把连续同类 <li> 分别包进 <ul>（无序）/ <ol>（有序）
    html_body = "\n".join(out_lines)
    return _wrap_list_items(html_body)


def _esc(s: str) -> str:
    return _html.escape(s, quote=False)


def _attr(s: str) -> str:
    """转义 HTML 属性值（引号也转义），用于 href/id 等属性上下文。"""
    return _html.escape(s or "", quote=True)


# _safe_link 允许的绝对 URL scheme 白名单（其余 scheme 一律拒绝为纯文本）
_SAFE_LINK_SCHEMES = frozenset({"http", "https", "mailto", "tel"})


def _safe_link(text: str, url: str) -> str:
    """生成 <a>，只允许相对 URL 与显式安全 scheme 白名单，防链接注入。

    先 HTML unescape 再校验，避免 `java&#115;cript:` 类混淆绕过；去除前导
    空白与 HTML 控制/空白字符后做 scheme 判断。javascript:/vbscript:/data:
    及其它非白名单 scheme 一律返回纯文本，保留安全相对链接与允许的绝对链接。
    """
    raw = _html.unescape(url or "").strip()
    # HTML 空白/控制字符（如 0x00-0x20）可能夹在 scheme 前后用于绕过，归一化处理
    norm = re.sub(r'[\x00-\x20]+', '', re.sub(r'&#x?[0-9a-fA-F]+;', '', raw))
    low = norm.lower()
    if low.startswith(("javascript:", "vbscript:", "data:")):
        return text
    # 仅允许显式安全 scheme 或相对 URL（无 scheme）
    if re.match(r'^[a-zA-Z][a-zA-Z0-9+.\-]*:', low):
        if low.split(":", 1)[0] not in _SAFE_LINK_SCHEMES:
            return text
    return f'<a href="{_attr(raw)}" rel="noopener">{text}</a>'


def _inline(s: str) -> str:
    """行内转换：`code`、**bold**、[text](url)。"""
    s = _esc(s)
    s = re.sub(r"`([^`]+)`", r"<code>\1</code>", s)
    s = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", s)
    s = re.sub(r"\[([^\]]+)\]\(([^)]+)\)",
               lambda m: _safe_link(m.group(1), m.group(2)), s)
    return s


# ═══════════════════════════════════════════════════════════════════
# HTML 外壳
# ═══════════════════════════════════════════════════════════════════

_BASE_CSS = """
* { margin:0; padding:0; box-sizing:border-box; }
body {
  font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC",
    "Microsoft YaHei","Helvetica Neue",Arial,sans-serif;
  background:#0f1117; color:#e5e7eb; line-height:1.6;
}
.wrap { max-width:1200px; margin:0 auto; padding:24px; }
.topbar {
  display:flex; justify-content:space-between; align-items:center;
  padding:18px 24px; margin-bottom:20px;
  background:#1a1d2e; border:1px solid #2a2d3a; border-radius:12px;
}
.topbar h1 { font-size:20px; font-weight:700; color:#f1f5f9; }
.topbar .meta { font-size:12px; color:#6b7280; }
nav { margin-bottom:20px; }
nav a {
  display:inline-block; margin:0 6px 6px 0; padding:6px 14px;
  background:#1a1d2e; border:1px solid #2a2d3a; border-radius:8px;
  color:#c9cdd4; text-decoration:none; font-size:13px;
}
nav a:hover { border-color:#3b82f6; color:#f1f5f9; }
section { margin-bottom:24px; background:#1a1d2e; border:1px solid #2a2d3a; border-radius:12px; padding:20px 24px; }
section h2 { font-size:16px; color:#f1f5f9; margin-bottom:14px; padding-bottom:10px; border-bottom:1px solid #2a2d3a; }
.cards { display:grid; grid-template-columns:repeat(auto-fit,minmax(150px,1fr)); gap:12px; margin-bottom:16px; }
.card { background:#14161f; border:1px solid #2a2d3a; border-radius:10px; padding:14px 16px; }
.card .lbl { font-size:12px; color:#6b7280; margin-bottom:4px; }
.card .val { font-size:24px; font-weight:700; color:#f1f5f9; }
table { width:100%; border-collapse:collapse; font-size:13px; }
th, td { text-align:left; padding:8px 10px; border-bottom:1px solid #2a2d3a; vertical-align:top; }
th { color:#9ca3af; font-weight:600; background:#14161f; }
tr:hover td { background:#1e2234; }
code { background:#14161f; border:1px solid #2a2d3a; border-radius:4px; padding:1px 5px; font-size:12px; color:#93c5fd; }
pre { background:#14161f; border:1px solid #2a2d3a; border-radius:8px; padding:14px; overflow-x:auto; font-size:12px; margin:8px 0; }
pre code { border:none; background:transparent; color:#e5e7eb; }
.badge { display:inline-block; padding:2px 8px; border-radius:6px; font-size:11px; font-weight:600; }
.badge-high { background:#3b1d1d; color:#fca5a5; }
.badge-medium { background:#3b2f1d; color:#fcd34d; }
.badge-low { background:#1d2b3b; color:#93c5fd; }
.badge-advice { background:#1d3b33; color:#6ee7b7; }
.badge-ok { background:#1d3b33; color:#6ee7b7; }
.badge-missing { background:#3b2f1d; color:#fcd34d; }
.empty { color:#6b7280; font-style:italic; }
.wiki-nav { display:flex; gap:8px; flex-wrap:wrap; margin-bottom:18px; }
.wiki-nav a { margin:0; padding:6px 14px; background:#1a1d2e; border:1px solid #2a2d3a; border-radius:8px; color:#c9cdd4; text-decoration:none; font-size:13px; }
.wiki-nav a:hover { border-color:#3b82f6; color:#f1f5f9; }
.wikigroup section h3 { font-size:14px; color:#c9cdd4; margin:0 0 10px; padding-bottom:6px; border-bottom:1px dashed #2a2d3a; }
ul { padding-left:20px; margin:6px 0; }
blockquote { border-left:3px solid #3b82f6; padding:4px 12px; color:#9ca3af; margin:8px 0; background:#14161f; border-radius:0 8px 8px 0; }
a { color:#60a5fa; }
hr { border:none; border-top:1px solid #2a2d3a; margin:14px 0; }
.footer { text-align:center; color:#6b7280; font-size:12px; padding:16px 0; }
"""

# ── 评估台（v5.17）专用样式：四 Tab + 记分卡 ──
_CONSOLE_CSS = """
.tabs { display:flex; gap:8px; flex-wrap:wrap; margin-bottom:18px; }
.tab-btn { cursor:pointer; padding:8px 18px; background:#1a1d2e; border:1px solid #2a2d3a;
  border-radius:8px; color:#c9cdd4; font-size:13px; font-family:inherit; }
.tab-btn:hover { border-color:#3b82f6; color:#f1f5f9; }
.tab-btn.active { border-color:#3b82f6; background:#1e2234; color:#f1f5f9; }
.tab-panel { display:none; }
.tab-panel.active { display:block; }
.sc-hero { display:flex; align-items:center; gap:28px; flex-wrap:wrap; }
.sc-num { font-size:44px; font-weight:800; color:#f1f5f9; line-height:1; }
.sc-grade { font-size:22px; font-weight:700; padding:6px 18px; border-radius:10px;
  background:#1e2234; border:1px solid #2a2d3a; color:#6ee7b7; }
.sc-meta { font-size:12px; color:#9ca3af; }
.sc-note { font-size:12px; color:#9ca3af; }
"""

# ── 评估台交互：Tab 切换 + 记分卡刷新（离线时如实提示，不假装成功） ──
_CONSOLE_JS = """
<script>
(function(){
  var btns = document.querySelectorAll('.tab-btn[data-tab]');
  function show(k){
    btns.forEach(function(b){ b.classList.toggle('active', b.getAttribute('data-tab') === k); });
    document.querySelectorAll('.tab-panel').forEach(function(p){
      p.classList.toggle('active', p.id === 'panel-' + k);
    });
  }
  btns.forEach(function(b){
    b.addEventListener('click', function(){ show(b.getAttribute('data-tab')); });
  });
  // 评估完成会刷新页面：恢复用户所在 Tab，避免被重置回「概览」
  try {
    var t = sessionStorage.getItem('coderef-tab');
    if (t) { sessionStorage.removeItem('coderef-tab'); show(t); }
  } catch (e) {}
})();
function scEsc(s){
  return String(s === null || s === undefined ? '' : s).replace(/[&<>"']/g, function(c){
    return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c];
  });
}
function scApply(j){
  var ov = document.getElementById('sc-overall');
  if (ov) ov.innerHTML = (j.overall === null || j.overall === undefined) ? 'N/A'
    : scEsc(j.overall) + '<span style="font-size:14px;color:#6b7280">/100</span>';
  var g = document.getElementById('sc-grade');
  if (g) g.textContent = j.grade || 'N/A';
  var m = document.getElementById('sc-meta');
  if (m) m.textContent = '置信度 ' + (j.confidence || '-') + ' · 证据覆盖 '
    + Math.round((j.coverage || 0) * 100) + '%';
  var rows = (j.dimensions || []).map(function(d){
    var s = d.status === 'ok' ? ('<strong>' + scEsc(d.score) + '</strong>')
      : (d.status === 'not_applicable'
        ? '<span class="badge badge-missing">不适用</span>'
        : '<span class="badge badge-missing">证据不足</span>');
    var raw = Object.keys(d.raw || {}).map(function(k){
      return scEsc(k) + '=' + scEsc(d.raw[k]);
    }).join(' · ');
    var ev = (d.evidence || []).map(scEsc).join('、');
    return '<tr><td>' + scEsc(d.name) + '</td><td>' + scEsc(d.weight) + '</td><td>' + s
      + '</td><td>' + raw + '</td><td>' + ev + '</td><td>' + scEsc(d.source_tool || '') + '</td></tr>';
  }).join('');
  var rb = document.getElementById('sc-rows');
  if (rb) rb.innerHTML = rows;
  var nb = document.getElementById('sc-notes');
  if (nb) nb.innerHTML = (j.notes || []).map(function(n){
    return '<p class="sc-note">' + scEsc(n) + '</p>';
  }).join('');
  var stamp = document.getElementById('sc-stamp');
  if (stamp) stamp.textContent = '评估时间 ' + (j.generated_at || '');
}
function scRefresh(){
  var st = document.getElementById('sc-status');
  fetch('/api/scorecard?refresh=1', {cache:'no-store'}).then(function(r){
    if (!r.ok) throw new Error('HTTP ' + r.status);
    return r.json();
  }).then(function(j){
    scApply(j);
    if (st) st.textContent = '已重新评估（本地服务）';
  }).catch(function(){
    if (st) st.textContent = '离线快照：未连接本地评估服务，无法重新评估（用 coderef_report open_server=true 打开）';
  });
}
function runEval(scene){
  var meta = document.querySelector('meta[name="coderef-token"]');
  var token = meta ? meta.getAttribute('content') : '';
  var st = document.getElementById('eval-status-' + scene);
  if (!token){
    if (st) st.textContent = '离线页面：请用 coderef_report open_server=true 打开后再评估';
    return;
  }
  // 同一场景在途时忽略重复点击（服务端也会以 409 BUSY 兜底）
  window._evalBusy = window._evalBusy || {};
  if (window._evalBusy[scene]) return;
  window._evalBusy[scene] = true;
  if (st) st.textContent = '评估中…（可能需要数分钟，请勿关闭页面）';
  fetch('/api/evaluate', {
    method: 'POST',
    headers: {'Content-Type': 'application/json', 'X-CodeRef-Token': token},
    body: JSON.stringify({scene: scene})
  }).then(function(r){
    return r.json().then(function(j){ return {ok: r.ok, j: j}; });
  }).then(function(x){
    if (!x.ok || (x.j && x.j.status === 'ERROR')){
      throw new Error((x.j && (x.j.error || x.j.message)) || '评估失败');
    }
    if (st) st.textContent = '评估完成（' + ((x.j && x.j.status) || 'OK') + '），正在刷新…';
    try { sessionStorage.setItem('coderef-tab', scene); } catch (e) {}
    setTimeout(function(){ location.reload(); }, 400);
  }).catch(function(e){
    window._evalBusy[scene] = false;
    if (st) st.textContent = '评估失败：' + e.message;
  });
}
</script>
"""


def _page(title: str, body: str, nav_links: str, project_name: str) -> str:
    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{_esc(title)} - {_esc(project_name)}</title>
<style>{_BASE_CSS}{_CONSOLE_CSS}</style>
</head>
<body>
<div class="wrap">
  <div class="topbar">
    <h1>{_esc(title)}</h1>
    <div class="meta">{_esc(project_name)} · {datetime.now().strftime('%Y-%m-%d %H:%M')}</div>
  </div>
  <nav>{nav_links}</nav>
  {body}
  <div class="footer">由 CodeRef-AI 生成 · 自包含 HTML 报告</div>
</div>
</body>
</html>"""


def _nav(active: str, project_name: str) -> str:
    items = [
        ("index.html", "概览", "概览"),
        ("audit.html", "审计发现", "审计发现"),
        ("kg.html", "知识图谱", "知识图谱"),
        ("wiki.html", "Wiki 文档", "Wiki"),
    ]
    links = []
    for href, label, _ in items:
        cls = ' class="active"' if href == active else ""
        links.append(f'<a href="{href}"{cls}>{label}</a>')
    return "".join(links)


# ═══════════════════════════════════════════════════════════════════
# 渲染器
# ═══════════════════════════════════════════════════════════════════

class HtmlReportRenderer:
    """把审计结果 / 图谱 / Wiki 聚合成自包含 HTML 报告目录。"""

    def __init__(self, project_path: str):
        self.project_path = os.path.abspath(project_path)
        self.project_name = os.path.basename(self.project_path.rstrip(os.sep))

    # ─── 主入口 ───

    def render(self, pipe_result, kg_stats: Optional[dict] = None,
               wiki_dir: Optional[str] = None,
               output_dir: Optional[str] = None,
               dimension_states: Optional[dict] = None,
               scorecard: Optional[dict] = None) -> dict:
        """渲染完整报告目录。

        Args:
            pipe_result: PipeResult（含 findings / report / errors / 统计）
            kg_stats: CodeKnowledgeGraph.get_stats() 返回值（可选）
            wiki_dir: Wiki .md 输出目录（可选，存在则渲染 wiki.html）
            output_dir: 报告输出目录；默认 {项目上级}/coderef-report/html
            dimension_states: 各维度（audit/kg/wiki）执行状态，用于透明化展示
                              （未执行的维度标注"未执行"而非静默为空/全 0）
            scorecard: 记分卡数据（可选）。缺省读 <project>/.coderef/scorecard.json 快照；
                       渲染阶段**不计算、不写盘**，计算由 coderef_report 入口显式触发。

        Returns:
            {"ok": bool, "index": 绝对路径, "files": [..], "error": str?}
        """
        out = output_dir or os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "coderef-report", "html")
        os.makedirs(out, exist_ok=True)

        results = {}
        try:
            results["audit.html"] = self._render_audit(pipe_result, dimension_states)
            results["kg.html"] = self._render_kg(kg_stats, dimension_states)
            results["wiki.html"] = self._render_wiki(wiki_dir, dimension_states)
            # 记分卡：默认只读已落盘快照（不重跑扫描、不写盘）；由 coderef_report
            # 入口先算好再落盘，其他渲染路径（审计/docs 管线）只消费快照。
            sc = scorecard if scorecard is not None else self._load_scorecard_snapshot()
            results["index.html"] = self._render_index(
                pipe_result, kg_stats, wiki_dir, dimension_states,
                scorecard=sc, arch_link=self._latest_arch_link(out))
        except Exception as e:
            return {"ok": False, "error": str(e), "files": []}

        written = []
        try:
            for fname, content in results.items():
                fp = os.path.join(out, fname)
                with open(fp, "w", encoding="utf-8") as f:
                    f.write(content)
                written.append(fp)
        except Exception as e:
            return {"ok": False, "error": str(e), "files": []}

        return {"ok": True, "index": os.path.join(out, "index.html"),
                "files": written}

    # ─── 评估台支撑 ───

    def _load_scorecard_snapshot(self) -> dict:
        """读取已落盘的记分卡快照（不做扫描、不写盘；缺失时回空字典由页面诚实占位）。"""
        path = os.path.join(self.project_path, ".coderef", "scorecard.json")
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, dict):
                return {}
            dims = data.get("dimensions")
            # 快照可能来自被分析项目（克隆仓库）：非法结构降级为错误占位，
            # 不能因为一个坏快照让整站四页全部渲染失败。
            if dims is not None and (
                    not isinstance(dims, list)
                    or not all(isinstance(d, dict) and isinstance(d.get("raw") or {}, dict)
                               for d in dims)
                    or not isinstance(data.get("coverage") or 0, (int, float))):
                return {"error": "记分卡快照结构非法"}
            return data
        except FileNotFoundError:
            return {}
        except Exception as e:  # noqa: BLE001
            return {"error": f"记分卡快照读取失败: {e}"}

    def _latest_arch_link(self, out_dir: str) -> str:
        """定位最新一张业务架构画布，返回相对 index.html 的链接（无则空串）。"""
        d = os.path.join(self.project_path, ".coderef")
        try:
            files = sorted(f for f in os.listdir(d)
                           if f.startswith("arch_canvas_") and f.endswith(".html"))
        except Exception:  # noqa: BLE001
            return ""
        if not files:
            return ""
        try:
            rel = os.path.relpath(os.path.join(d, files[-1]), out_dir)
            return rel.replace(os.sep, "/")
        except Exception:  # noqa: BLE001
            return ""

    # ─── 各类渲染 ───

    def _read_overview_summary(self, wiki_dir: Optional[str]) -> str:
        """读取 OVERVIEW.md 首段业务摘要，供 index.html 业务视角优先展示。

        业务概览优先原则：首页应先让用户看到"项目在业务上是做什么的"，
        技术统计（扫描文件/代码行/缺陷数）降级为支撑信息。
        """
        if not wiki_dir or not os.path.isdir(wiki_dir):
            return ""
        fp = os.path.join(wiki_dir, "OVERVIEW.md")
        if not os.path.isfile(fp):
            return ""
        try:
            with open(fp, "r", encoding="utf-8", errors="replace") as f:
                content = f.read()
        except Exception:
            return ""
        for line in content.splitlines():
            line = line.strip()
            if line and not line.startswith("#") and not line.startswith(">"):
                return line[:220]
        return ""

    def _list_wiki_group(self, wiki_dir: Optional[str],
                         subdir: str) -> List[str]:
        """列出 wiki 子目录（ENTRIES/FLOWS）下的文档链接，供首页入口清单展示。"""
        if not wiki_dir or not os.path.isdir(wiki_dir):
            return []
        d = os.path.join(wiki_dir, subdir)
        if not os.path.isdir(d):
            return []
        links = []
        for fn in sorted(os.listdir(d)):
            if fn.endswith(".md"):
                rel = f"{subdir}/{fn}"
                name = fn[:-3]
                links.append(
                    f'<p><a href="wiki.html#{_attr(rel)}">{_esc(name)}</a></p>')
        return links

    def _render_index(self, pr, kg_stats, wiki_dir, dimension_states=None,
                      scorecard=None, arch_link="") -> str:
        """评估台首页：只做面板编排，内容组装见各自面板方法。"""
        overview = self._overview_panel(pr, kg_stats, wiki_dir, dimension_states, scorecard)
        arch = self._arch_panel(arch_link)
        # 快照来自可能不可信的项目仓库：单个面板渲染失败只降级该面板，
        # 不让一份坏 JSON 导致四页全部失败（与概览记分卡口径一致）。
        product = self._safe_panel(self._product_panel, "产品评估")
        ux = self._safe_panel(self._ux_panel, "UX 人格走查")
        body = self._console_shell(overview, arch, product, ux)
        return _page("项目评估台", body, _nav("index.html", self.project_name), self.project_name)

    def _overview_panel(self, pr, kg_stats, wiki_dir, dimension_states,
                        scorecard) -> str:
        """概览面板：记分卡 + 评估标准 + 业务摘要 + 入口/数据流 + 报告章节 + 技术统计。"""
        dims = dimension_states or {}
        audit_d = dims.get("audit") or {}
        kg_d = dims.get("kg") or {}
        wiki_d = dims.get("wiki") or {}
        audit_status = audit_d.get("status", "missing")
        kg_status = kg_d.get("status", "missing")
        wiki_status = wiki_d.get("status", "missing")

        findings = getattr(pr, "findings", []) or []
        # 审计计数：仅当维度"已执行"时展示真实数字，否则显示 "--" 避免把"未审计"当"0 发现"
        if audit_status == "done":
            h = sum(1 for f in findings if getattr(f, "tier", None) and f.tier.value == "high")
            m = sum(1 for f in findings if getattr(f, "tier", None) and f.tier.value == "medium")
            lo = sum(1 for f in findings if getattr(f, "tier", None) and f.tier.value == "low")
            adv = sum(1 for f in findings if getattr(f, "kind", "") == "advice")
        else:
            h = m = lo = adv = "--"
        kgn = kg_d.get("nodes") if kg_status == "done" else None
        if not kgn:
            # done 但 kg_d 缺 nodes，或非 done 时：回退到 kg_stats.node_count
            kgn = (kg_stats or {}).get("node_count", 0)
        kge = (kg_stats or {}).get("edge_count", 0)

        def _card(lbl, val, color=None):
            style = f' style="color:{color}"' if color else ""
            return f'<div class="card"><div class="lbl">{lbl}</div><div class="val"{style}>{val}</div></div>'

        cards = '<div class="cards">'
        cards += _card("扫描文件", getattr(pr, "total_files", 0))
        cards += _card("代码行", getattr(pr, "total_lines", 0))
        cards += _card("HIGH", h, "#fca5a5" if audit_status == "done" else "#fcd34d")
        cards += _card("MEDIUM", m, "#fcd34d" if audit_status == "done" else "#fcd34d")
        cards += _card("LOW", lo, "#93c5fd" if audit_status == "done" else "#fcd34d")
        cards += _card("建议", adv, "#6ee7b7" if audit_status == "done" else "#fcd34d")
        cards += _card("图谱节点", kgn)
        cards += _card("图谱边", kge)
        cards += '</div>'

        # 报告章节：按维度分区，每区带执行状态徽章 + 一句话摘要 + 链接
        secs = []
        if audit_status == "done":
            if findings:
                secs.append(f'<section><h2>审计发现 <span class="badge badge-ok">已执行</span></h2>'
                            f'<p>共 <strong>{len(findings)}</strong> 条发现，详见 <a href="audit.html">审计发现</a>。</p></section>')
            else:
                secs.append('<section><h2>审计发现 <span class="badge badge-ok">已执行</span></h2>'
                            '<p>审计已执行，未发现任何问题。详见 <a href="audit.html">审计发现</a>。</p></section>')
        else:
            hint = _esc(audit_d.get("hint", "尚未执行审计，请先运行 coderef_audit"))
            secs.append(f'<section><h2>审计发现 <span class="badge badge-missing">未执行</span></h2>'
                        f'<p class="empty">{hint}</p></section>')

        if kg_status == "done":
            secs.append(f'<section><h2>知识图谱 <span class="badge badge-ok">已执行</span></h2>'
                        f'<p>{_esc(kg_d.get("hint", ""))}，详见 <a href="kg.html">知识图谱</a>。</p></section>')
        else:
            secs.append(f'<section><h2>知识图谱 <span class="badge badge-missing">未执行</span></h2>'
                        f'<p class="empty">{_esc(kg_d.get("hint", "尚未构建知识图谱"))}</p></section>')

        if wiki_status == "done":
            secs.append(f'<section><h2>Wiki 文档 <span class="badge badge-ok">已执行</span></h2>'
                        f'<p>{_esc(wiki_d.get("hint", ""))}，详见 <a href="wiki.html">Wiki 文档</a>。</p></section>')
        else:
            secs.append(f'<section><h2>Wiki 文档 <span class="badge badge-missing">未执行</span></h2>'
                        f'<p class="empty">{_esc(wiki_d.get("hint", "尚未生成 Wiki，请先运行 coderef_docs"))}</p></section>')

        # 统计口径透明化：披露各维度时间，避免旧产物被当作本次结果
        ts = getattr(pr, "scan_ts", "") or ""
        kgt = getattr(pr, "kg_built_at", "") or kg_d.get("ts", "") or (kg_stats or {}).get("built_at", "")
        scope = getattr(pr, "scope_text", "") or ""
        note = "<section><h2>统计口径</h2>"
        note += f"<p>本次扫描时间：<code>{_esc(ts or '未记录')}</code></p>"
        note += f"<p>知识图谱构建：<code>{_esc(kgt or audit_d.get('ts', '') or '未记录')}</code>（图谱可能滞后于代码）</p>"
        if scope:
            note += f"<p>审计范围：{_esc(scope)}</p>"
        note += "</section>"

        # ── 概览面板：记分卡（首位）+ 评估标准 + 既有业务/技术概览 ──
        # 快照来自可能不可信的项目仓库：面板构建失败只降级记分卡，不让整站四页一起失败
        try:
            overview = self._scorecard_html(scorecard) + self._method_html(scorecard)
        except Exception as e:  # noqa: BLE001
            overview = self._scorecard_html({"error": f"记分卡快照渲染失败: {e}"})
        overview_summary = self._read_overview_summary(wiki_dir)
        if overview_summary:
            overview += (f'<section class="biz-hero"><h2>业务视角 <span class="badge badge-ok">优先阅读</span></h2>'
                         f'<p>{_esc(overview_summary)}…</p>'
                         f'<p><a href="wiki.html#grp-业务视角">前往业务概览 →</a></p></section>')

        # 公共入口 / 数据流清单（分层人话版的入口级 L1、数据流级 L2）
        entry_links = self._list_wiki_group(wiki_dir, "ENTRIES")
        flow_links = self._list_wiki_group(wiki_dir, "FLOWS")
        if entry_links:
            overview += (f'<section><h2>公共入口 <span class="badge badge-ok">{len(entry_links)} 个</span></h2>'
                         f'<p>每个入口的流程人话版，直接回答"这个入口是做什么、怎么做"。</p>'
                         f'{"".join(entry_links)}</section>')
        if flow_links:
            overview += (f'<section><h2>模块数据流 <span class="badge badge-ok">{len(flow_links)} 条</span></h2>'
                         f'<p>模块之间如何传递数据，直接回答"谁向谁要东西"。</p>'
                         f'{"".join(flow_links)}</section>')

        overview += '<section><h2>报告章节</h2>' + "".join(secs) + "</section>"
        overview += '<section><h2>技术概览</h2><p class="empty">以下为代码级统计，业务理解请以上方业务视角为主。</p>' \
                    + cards + '</section>' + note

        return overview

    @staticmethod
    def _arch_panel(arch_link: str) -> str:
        """架构面板：iframe 复用既有业务树画布（未生成则诚实占位）。"""
        if arch_link:
            return ('<section><h2>业务架构视图 <span class="badge badge-ok">已生成</span></h2>'
                    '<p>业务树大白话图：入口层 → 主功能 → 子功能 → 步骤链；模块依赖只作为编程 AI 的数据。</p>'
                    f'<iframe src="{_attr(arch_link)}" style="width:100%;height:78vh;'
                    'border:1px solid #2a2d3a;border-radius:8px;background:#0f1117"></iframe></section>')
        return ('<section><h2>业务架构视图 <span class="badge badge-missing">未生成</span></h2>'
                '<p class="empty">尚无架构画布产物：请先调用 coderef_arch_canvas（open_server=true 可直接推送），'
                '生成后刷新本页即可内嵌。</p></section>')

    # ─── 评估台：产品（F 轨）/ UX（U 轨） ───

    @staticmethod
    def _safe_panel(builder, title: str) -> str:
        """面板渲染失败只降级该面板（快照可能来自被分析项目的不可信仓库）。"""
        try:
            return builder()
        except Exception as e:  # noqa: BLE001
            return ('<section><h2>' + _esc(title)
                    + ' <span class="badge badge-missing">渲染失败</span></h2>'
                    + f'<p class="empty">评估快照结构非法：{_esc(str(e))}</p></section>')

    def _eval_snapshot(self, name: str) -> dict:
        path = os.path.join(self.project_path, ".coderef", name)
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, dict) else {"error": f"{name} 结构非法"}
        except FileNotFoundError:
            return {}
        except Exception as e:  # noqa: BLE001
            return {"error": f"{name} 读取失败: {e}"}

    @staticmethod
    def _eval_metrics(items) -> str:
        return '<div class="cards">' + "".join(
            f'<div class="card"><div class="lbl">{_esc(k)}</div>'
            f'<div class="val">{_esc(str(v if v is not None else "N/A"))}</div></div>'
            for k, v in items) + '</div>'

    @staticmethod
    def _q_cell(q) -> str:
        if not isinstance(q, dict):
            return '<span class="badge badge-missing">未判定</span>'
        status = str(q.get("status") or "unknown")
        level = str(q.get("evidence_level") or "")
        proxy = " · proxy" if q.get("proxy") else ""
        ok = status in ("known", "has_source_candidate",
                        "has_output_candidate", "interactive",
                        "batch_or_command", "request_driven")
        badge = "badge-ok" if ok else "badge-missing"
        return (f'<span class="badge {badge}">{_esc(status)}</span> '
                f'<span class="sc-note">{_esc(level + proxy)}</span><br>'
                f'<span class="sc-note">{_esc(str(q.get("answer") or ""))}</span>')

    @staticmethod
    def _eval_button(scene: str, label: str) -> str:
        return ('<button class="tab-btn" onclick="runEval(\'' + _attr(scene) + '\')">'
                + _esc(label) + '</button>'
                '<span class="sc-note" id="eval-status-' + _attr(scene) + '">'
                '本地服务可用时点按钮即评估；离线请用 coderef_report open_server=true</span>')

    def _product_panel(self) -> str:
        """产品评估：F 轨主轴（业务契合度），不评价界面美观。"""
        data = self._eval_snapshot("product_fit.json")
        head = ('<section><h2>产品评估 · F 轨业务契合度'
                '<span class="badge badge-ok">主轴</span></h2>')
        if not data:
            return (head + '<p class="empty">尚未运行产品评估。F 轨逐能力核对业务逻辑是否'
                    '支撑真实决策；证据不足时只回证据包（SKIP），不产出三档结论。</p>'
                    + self._eval_button("product", "运行产品评估") + '</section>')
        if data.get("error"):
            return (head + '<p class="empty">' + _esc(data["error"]) + '</p>'
                    + self._eval_button("product", "运行产品评估") + '</section>')

        status = str(data.get("status") or "")
        badge = {"OK": "badge-ok", "SKIP": "badge-missing"}.get(status, "badge-missing")
        stage = data.get("stage") or {}
        concl = data.get("conclusion") or {}
        parts = [head.replace("</h2>", f'<span class="badge {badge}">{_esc(status)}</span></h2>')]
        parts.append(f'<p class="sc-note">阶段：{_esc(stage.get("phase", "-"))}'
                     f'（{_esc(stage.get("reason", ""))} · {_esc(stage.get("decided_by", ""))}）</p>')
        parts.append(self._eval_metrics([
            ("契合率", data.get("fit_rate")),
            ("断链数", data.get("broken_chains")),
            ("卡死字段", data.get("blocked_fields")),
            ("红线", data.get("redlines")),
        ]))
        verdict = concl.get("verdict")
        if verdict:
            vbadge = {"Go": "badge-ok", "Validate-first": "badge-missing",
                      "No-go": "badge-high"}.get(str(verdict), "badge-missing")
            parts.append(f'<h3 style="margin:14px 0 8px">结论 '
                         f'<span class="badge {vbadge}">{_esc(str(verdict))}</span></h3>'
                         f'<p>{_esc(str(concl.get("reason") or ""))}</p>')
            conds = concl.get("conditions") or []
            if conds:
                parts.append('<ul>' + "".join(f'<li>{_esc(str(c))}</li>' for c in conds) + '</ul>')
        else:
            parts.append(f'<p class="empty">{_esc(str(concl.get("reason") or "未产出结论"))}</p>')

        rows = (data.get("f1") or (data.get("f1_deterministic") or {}).get("rows") or [])
        if rows:
            body = []
            for r in rows[:60]:
                if not isinstance(r, dict):
                    continue
                body.append(
                    f'<tr><td>{_esc(str(r.get("name", r.get("capability_id", ""))))}</td>'
                    f'<td>{_esc(str(r.get("source", "")))}</td>'
                    f'<td>{self._q_cell(r.get("q1_decision"))}</td>'
                    f'<td>{self._q_cell(r.get("q2_input"))}</td>'
                    f'<td>{self._q_cell(r.get("q3_output"))}</td>'
                    f'<td>{self._q_cell(r.get("q4_honesty"))}</td>'
                    f'<td>{self._q_cell(r.get("q5_frequency"))}</td>'
                    f'<td><code>{_esc("、".join(r.get("anchors") or []))}</code></td></tr>')
            parts.append('<h3 style="margin:14px 0 8px">F1 能力 × 五问矩阵</h3>'
                         '<table><thead><tr><th>能力</th><th>来源</th><th>①支撑决策</th>'
                         '<th>②输入现实性</th><th>③输出可行动</th><th>④数据诚实</th>'
                         '<th>⑤频率匹配</th><th>证据锚点</th></tr></thead><tbody>'
                         + "".join(body) + '</tbody></table>')

        cut = data.get("cut_or_change") or []
        if cut:
            parts.append('<h3 style="margin:14px 0 8px">砍 / 改清单</h3><ul>' + "".join(
                f'<li><strong>{_esc(str(c.get("capability_id", "")))}</strong>：'
                f'{_esc(str(c.get("reason", "")))}</li>'
                for c in cut if isinstance(c, dict)) + '</ul>')
        assumptions = data.get("assumptions") or []
        if assumptions:
            parts.append('<h3 style="margin:14px 0 8px">待验证假设（E4）</h3><ul>' + "".join(
                f'<li>{_esc(str(a.get("text", a) if isinstance(a, dict) else a))}</li>'
                for a in assumptions[:20]) + '</ul>')
        unknowns = data.get("unknowns") or (data.get("evidence") or {}).get("unknowns") or []
        if unknowns:
            parts.append('<h3 style="margin:14px 0 8px">未知项</h3><ul>' + "".join(
                f'<li>{_esc(str(u))}</li>' for u in unknowns[:20]) + '</ul>')
        parts.append('<p class="sc-note">' + _esc(str((data.get("chain") or {}).get("note", ""))) + '</p>')
        parts.append(self._eval_button("product", "运行产品评估"))
        parts.append('</section>')
        return "".join(parts)

    def _ux_panel(self) -> str:
        """UX 人格走查：只判阻断，不产出体验评分。"""
        data = self._eval_snapshot("ux_fit.json")
        head = ('<section><h2>UX 人格走查 · U 轨'
                '<span class="badge badge-ok">只判阻断</span></h2>')
        if not data:
            return (head + '<p class="empty">尚未运行人格走查。U 轨只回答「呈现问题是否阻断'
                    '任务完成」，不产出体验分数；无锚点的发现不输出。</p>'
                    + self._eval_button("ux", "运行 UX 走查") + '</section>')
        if data.get("error"):
            return (head + '<p class="empty">' + _esc(data["error"]) + '</p>'
                    + self._eval_button("ux", "运行 UX 走查") + '</section>')

        status = str(data.get("status") or "")
        persona = data.get("persona") or {}
        verdict = str(data.get("verdict") or "")
        vbadge = {"blocking": "badge-high", "partial": "badge-missing",
                  "non_blocking": "badge-ok"}.get(verdict, "badge-missing")
        vlabel = {"blocking": "阻断", "partial": "部分阻断",
                  "non_blocking": "不阻断"}.get(verdict, verdict)
        parts = [head.replace(
            "</h2>",
            f'<span class="badge {vbadge}">{_esc(vlabel)}</span>'
            f'<span class="badge badge-missing">{_esc(status)}</span></h2>')]
        parts.append(f'<p class="sc-note">人格：{_esc(str(persona.get("name", "-")))}'
                     f'（{_esc(str(persona.get("identity", "")))}）</p>')
        parts.append(f'<p class="sc-note">旅程：{_esc(str(data.get("journey", "")))} · '
                     'simulated=true · 结论 E4（锚点为 E2）</p>')
        three = data.get("three_second") or {}
        if three:
            parts.append('<h3 style="margin:14px 0 8px">3 秒生存</h3><ul>' + "".join(
                f'<li>{_esc(k)}：{_esc(str(v))}</li>' for k, v in three.items()) + '</ul>')

        findings = data.get("findings") or []
        if findings:
            rows = []
            for f in findings[:80]:
                if not isinstance(f, dict):
                    continue
                sev = str(f.get("severity") or "P2")
                sb = {"P0": "badge-high", "P1": "badge-medium",
                      "P2": "badge-low"}.get(sev, "badge-low")
                rows.append(
                    f'<tr><td><span class="badge {sb}">{_esc(sev)}</span></td>'
                    f'<td>{_esc(str(f.get("problem", "")))}</td>'
                    f'<td>{_esc(str(f.get("rage", "")))}</td>'
                    f'<td>{_esc("是" if f.get("task_interrupted") else "否")}</td>'
                    f'<td>{_esc(str(f.get("business_consequence", "")))}</td>'
                    f'<td><code>{_esc(str(f.get("anchor", "")))}</code></td></tr>')
            parts.append('<h3 style="margin:14px 0 8px">Rage Map（含业务后果）</h3>'
                         '<table><thead><tr><th>级别</th><th>问题</th><th>愤怒</th>'
                         '<th>任务中断</th><th>业务后果</th><th>证据锚点</th></tr></thead><tbody>'
                         + "".join(rows) + '</tbody></table>')
        else:
            parts.append('<p class="empty">未发现带锚点的阻断项。</p>')

        unknowns = data.get("unknowns") or []
        if unknowns:
            parts.append('<h3 style="margin:14px 0 8px">未知项</h3><ul>' + "".join(
                f'<li>{_esc(str(u))}</li>' for u in unknowns[:20]) + '</ul>')
        vp = data.get("viewports") or {}
        parts.append(f'<p class="sc-note">视口：{_esc(str(vp.get("evaluated", "")))}'
                     f' · {_esc(str(vp.get("note", "")))}</p>')
        parts.append(self._eval_button("ux", "运行 UX 走查"))
        parts.append('</section>')
        return "".join(parts)

    # ─── 评估台：记分卡 / 评估标准 / Tab 外壳 ───

    def _scorecard_html(self, card: Optional[dict]) -> str:
        card = card or {}
        if card.get("error"):
            return ('<section><h2>健康记分卡 <span class="badge badge-missing">计算失败</span></h2>'
                    f'<p class="empty">{_esc(card["error"])}</p></section>')
        if not card.get("dimensions"):
            return ('<section><h2>健康记分卡 <span class="badge badge-missing">未生成</span></h2>'
                    '<p class="empty">暂无记分卡数据。</p></section>')

        overall = card.get("overall")
        # 快照来自被分析的项目（可能是克隆仓库），所有字段都按不可信内容转义
        num = ('N/A' if overall is None else
               f'{_esc(str(overall))}<span style="font-size:14px;color:#6b7280">/100</span>')
        rows = []
        for d in card.get("dimensions") or []:
            status = d.get("status")
            if status == "ok":
                s = f'<strong>{_esc(str(d.get("score")))}</strong>'
            elif status == "not_applicable":
                s = '<span class="badge badge-missing">不适用</span>'
            else:
                s = '<span class="badge badge-missing">证据不足</span>'
            raw_items = [f'{k}={v}' for k, v in (d.get("raw") or {}).items()
                         if v not in ("", None)][:6]
            rows.append(
                f'<tr><td>{_esc(d.get("name", d.get("id", "")))}</td>'
                f'<td>{_esc(str(d.get("weight", "")))}</td><td>{s}</td>'
                f'<td>{_esc(" · ".join(raw_items))}</td>'
                f'<td>{_esc("、".join(d.get("evidence") or []))}</td>'
                f'<td>{_esc(d.get("source_tool", ""))}</td></tr>')
        notes = "".join(f'<p class="sc-note">{_esc(n)}</p>' for n in card.get("notes") or [])
        return (
            '<section><h2>健康记分卡 <span class="badge badge-ok">确定性聚合</span></h2>'
            '<div class="sc-hero">'
            f'<div class="sc-num" id="sc-overall">{num}</div>'
            f'<div class="sc-grade" id="sc-grade">{_esc(card.get("grade", "N/A"))}</div>'
            '<div><div class="sc-meta" id="sc-meta">'
            f'置信度 {_esc(card.get("confidence", "-"))} · 证据覆盖 {int(round((card.get("coverage") or 0) * 100))}%'
            '</div>'
            f'<div class="sc-meta" id="sc-stamp">评估时间 {_esc(card.get("generated_at", ""))}</div></div>'
            '<button class="tab-btn" onclick="scRefresh()">重新评估</button>'
            '<span class="sc-note" id="sc-status">离线快照（重新评估需经 coderef_report open_server 打开）</span>'
            '</div>'
            '<table style="margin-top:18px"><thead><tr>'
            '<th>维度</th><th>权重</th><th>得分</th><th>原始分项</th><th>证据锚点</th><th>来源工具</th>'
            '</tr></thead><tbody id="sc-rows">' + "".join(rows) + '</tbody></table>'
            f'<div id="sc-notes" style="margin-top:12px">{notes}</div></section>')

    def _method_html(self, card: Optional[dict]) -> str:
        """评估标准面板：把"按什么标准评、权重多少"摊开，避免黑箱打分。"""
        dims = (card or {}).get("dimensions") or []
        if not dims:
            return ""
        rows = "".join(
            f'<tr><td>{_esc(d.get("name", ""))}</td><td>{_esc(str(d.get("weight", "")))}</td>'
            f'<td>{_esc(d.get("desc", ""))}</td><td>{_esc(d.get("source_tool", ""))}</td></tr>'
            for d in dims)
        gate = (card or {}).get("gate") or {}
        return (
            '<section><h2>评估标准 <span class="badge badge-ok">可调</span></h2>'
            '<p class="sc-note">权重默认写在 core/scorecard_weights.json；项目级覆盖写 '
            '<code>.coderef/eval_config.json</code>。证据不足的维度标"证据不足"，'
            '不计入总分，也不会被当作 0 分。</p>'
            '<table><thead><tr><th>维度</th><th>权重</th><th>口径</th><th>来源工具</th></tr></thead>'
            f'<tbody>{rows}</tbody></table>'
            f'<p class="sc-note" style="margin-top:10px">阈值：最低分 '
            f'{_esc(str(gate.get("min_score", "")))}（CI 默认只告警，显式开启才阻断）</p></section>')

    @staticmethod
    def _console_shell(overview: str, arch: str, product: str, ux: str) -> str:
        tabs = [("overview", "概览"), ("arch", "架构"), ("product", "产品"), ("ux", "UX")]
        bar = "".join(
            f'<button class="tab-btn{" active" if i == 0 else ""}" data-tab="{k}">{label}</button>'
            for i, (k, label) in enumerate(tabs))
        panels = (f'<div class="tab-panel active" id="panel-overview">{overview}</div>'
                  f'<div class="tab-panel" id="panel-arch">{arch}</div>'
                  f'<div class="tab-panel" id="panel-product">{product}</div>'
                  f'<div class="tab-panel" id="panel-ux">{ux}</div>')
        return f'<div class="tabs">{bar}</div>{panels}{_CONSOLE_JS}'

    def _render_audit(self, pr, dimension_states=None) -> str:
        aname = self.project_name
        dims = dimension_states or {}
        audit_d = dims.get("audit") or {}
        # 审计未执行：显式标注，而非把"未审计"渲染成"暂无发现"
        if audit_d.get("status") == "missing":
            hint = audit_d.get("hint", "尚未执行审计，请先运行 coderef_audit")
            body = (f'<section><h2>审计发现 <span class="badge badge-missing">未执行</span></h2>'
                    f'<p class="empty">{_esc(hint)}</p></section>')
            return _page("审计发现", body, _nav("audit.html", aname), aname)

        findings = getattr(pr, "findings", []) or []
        if not findings:
            body = ('<section><h2>审计发现 <span class="badge badge-ok">已执行</span></h2>'
                    '<p>审计已执行，未发现任何问题。</p></section>')
        else:
            ordered = sorted(findings, key=lambda f: (
                {"high": 0, "medium": 1, "low": 2}.get(
                    getattr(f, "tier", None).value if getattr(f, "tier", None) else "low", 3),
                getattr(f, "severity", ""), getattr(f, "file_path", ""), getattr(f, "line", 0)))
            rows = []
            for f in ordered:
                tier = getattr(f, "tier", None)
                tval = tier.value if tier else "low"
                badge = {"high": "badge-high", "medium": "badge-medium",
                         "low": "badge-low"}.get(tval, "badge-low")
                lbl = {"high": "HIGH", "medium": "MEDIUM", "low": "LOW"}.get(tval, tval)
                if getattr(f, "kind", "") == "advice":
                    badge, lbl = "badge-advice", "建议"
                loc = getattr(f, "line_label", "") or getattr(f, "file_path", "")
                xv = (" ×" + ",".join(getattr(f, "xval_by", []))) if getattr(f, "xval_by", []) else ""
                rows.append(
                    f'<tr><td><span class="badge {badge}">{lbl}</span></td>'
                    f'<td>{_esc(getattr(f, "tool", ""))}</td>'
                    f'<td>{_esc(getattr(f, "category", ""))}</td>'
                    f'<td><code>{_esc(loc)}</code></td>'
                    f'<td>{_esc(getattr(f, "title", ""))}{_esc(xv)}</td></tr>')
            body = f"""<section><h2>审计发现明细</h2>
                <p>共 <strong>{len(findings)}</strong> 条发现（含建议项）。</p>
                <table><thead><tr><th>程度</th><th>工具</th><th>分类</th><th>位置</th><th>描述</th></tr></thead>
                <tbody>{''.join(rows)}</tbody></table></section>"""

        errs = getattr(pr, "errors", []) or []
        if errs:
            erows = "".join(f"<li>{_esc(e)}</li>" for e in errs)
            body += f'<section><h2>检测器异常</h2><ul>{erows}</ul></section>'
        return _page("审计发现", body, _nav("audit.html", self.project_name), self.project_name)

    def _render_kg(self, kg_stats, dimension_states=None) -> str:
        aname = self.project_name
        dims = dimension_states or {}
        kg_d = dims.get("kg") or {}
        # 图谱未执行：显式标注
        if kg_d.get("status") == "missing":
            hint = kg_d.get("hint", "尚未构建知识图谱，请先运行 coderef_audit 或构建图谱")
            body = (f'<section><h2>知识图谱 <span class="badge badge-missing">未执行</span></h2>'
                    f'<p class="empty">{_esc(hint)}</p></section>')
            return _page("知识图谱", body, _nav("kg.html", aname), aname)
        if not kg_stats or "error" in kg_stats:
            body = '<section><h2>知识图谱</h2><p class="empty">知识图谱数据不可用</p></section>'
        else:
            node_count = kg_stats.get("node_count", 0)
            edge_count = kg_stats.get("edge_count", 0)
            built_at = kg_stats.get("built_at", "")
            node_types = kg_stats.get("node_types", {})
            edge_types = kg_stats.get("edge_types", {})

            cards = f"""<div class="cards">
                <div class="card"><div class="lbl">节点</div><div class="val">{node_count}</div></div>
                <div class="card"><div class="lbl">边</div><div class="val">{edge_count}</div></div>
                <div class="card"><div class="lbl">构建时间</div><div class="val small" style="font-size:16px">{_esc(built_at or '未记录')}</div></div>
            </div>"""

            def _table(types: dict, title: str) -> str:
                if not types:
                    return ""
                rows = "".join(
                    f"<tr><td>{_esc(k)}</td><td>{v}</td></tr>"
                    for k, v in sorted(types.items(), key=lambda kv: -kv[1]))
                return f"<h3 style='margin:14px 0 8px'>{title}</h3><table><thead><tr><th>类型</th><th>数量</th></tr></thead><tbody>{rows}</tbody></table>"

            body = (cards + _table(node_types, "节点类型分布")
                    + _table(edge_types, "边类型分布"))
        return _page("知识图谱", body, _nav("kg.html", self.project_name), self.project_name)

    def _render_wiki(self, wiki_dir, dimension_states=None) -> str:
        aname = self.project_name
        dims = dimension_states or {}
        wiki_d = dims.get("wiki") or {}
        # Wiki 未执行：显式标注
        if wiki_d.get("status") == "missing":
            hint = wiki_d.get("hint", "尚未生成 Wiki，请先运行 coderef_docs")
            body = (f'<section><h2>Wiki 文档 <span class="badge badge-missing">未执行</span></h2>'
                    f'<p class="empty">{_esc(hint)}</p></section>')
            return _page("Wiki 文档", body, _nav("wiki.html", aname), aname)
        if not wiki_dir or not os.path.isdir(wiki_dir):
            body = '<section><h2>Wiki 文档</h2><p class="empty">未提供 Wiki 目录</p></section>'
            return _page("Wiki 文档", body, _nav("wiki.html", self.project_name), self.project_name)

        # 收集 .md 文件
        md_files = []
        for root, dirs, files in os.walk(wiki_dir):
            dirs[:] = [d for d in dirs if not d.startswith(".")]
            for fn in sorted(files):
                if fn.endswith(".md"):
                    md_files.append(os.path.join(root, fn))
        md_files.sort()

        if not md_files:
            body = '<section><h2>Wiki 文档</h2><p class="empty">目录下无 .md 文档</p></section>'
            return _page("Wiki 文档", body, _nav("wiki.html", self.project_name), self.project_name)

        # 分组：业务视角优先，入口/数据流次之，技术文档，模块文档最后
        _TECH = {"README.md", "ARCHITECTURE.md", "INSTALLATION.md",
                 "USAGE.md", "API.md", "WIKI_INDEX.md"}
        groups = {"业务视角": [], "入口流程": [], "数据流": [],
                  "技术文档": [], "模块文档": []}
        for fp in md_files:
            rel = os.path.relpath(fp, wiki_dir).replace("\\", "/")
            base = os.path.basename(rel)
            if base == "OVERVIEW.md":
                groups["业务视角"].append(fp)
            elif rel.startswith("ENTRIES/") or "/ENTRIES/" in rel:
                groups["入口流程"].append(fp)
            elif rel.startswith("FLOWS/") or "/FLOWS/" in rel:
                groups["数据流"].append(fp)
            elif rel.startswith("MODULES/") or "/MODULES/" in rel:
                groups["模块文档"].append(fp)
            else:
                groups["技术文档"].append(fp)

        # 顶部按分组的锚点导航
        nav = '<div class="wiki-nav">'
        for gname, files in groups.items():
            if files:
                nav += f'<a href="#grp-{_attr(gname)}">{_esc(gname)} ({len(files)})</a>'
        nav += '</div>'

        # 按分组渲染（业务视角组置前）
        group_html = []
        for gname, files in groups.items():
            if not files:
                continue
            inner = []
            for fp in files:
                rel = os.path.relpath(fp, wiki_dir).replace("\\", "/")
                try:
                    with open(fp, "r", encoding="utf-8", errors="replace") as f:
                        content = f.read()
                except Exception as e:
                    inner.append(f'<section><h3>{_esc(rel)}</h3>'
                                 f'<p class="empty">读取失败: {_esc(str(e))}</p></section>')
                    continue
                if not content.strip():
                    continue
                inner.append(f'<section><h3 id="{_attr(rel)}">{_esc(rel)}</h3>{md_to_html(content)}</section>')
            if inner:
                group_html.append(f'<section class="wikigroup" id="grp-{_attr(gname)}">'
                                  f'<h2>{_esc(gname)} <span class="badge badge-ok">{len(inner)} 篇</span></h2>'
                                  f'{"".join(inner)}</section>')

        if not group_html:
            group_html.append('<section><h2>Wiki 文档</h2><p class="empty">所有文档均为空</p></section>')
        body = nav + "\n".join(group_html)
        return _page("Wiki 文档", body, _nav("wiki.html", self.project_name), self.project_name)