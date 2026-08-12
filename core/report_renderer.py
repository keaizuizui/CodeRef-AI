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

import os
import re
import html as _html
from datetime import datetime
from typing import Dict, List, Optional


# ═══════════════════════════════════════════════════════════════════
# 轻量 markdown 子集转换（标题/粗体/行内代码/列表/表格/围栏代码块/链接）
# ═══════════════════════════════════════════════════════════════════

_FENCE_RE = re.compile(r"```(\w*)\n(.*?)```", re.DOTALL)


def md_to_html(md_text: str) -> str:
    """把 markdown 子集转成 HTML（HTML 转义处理，防止注入）。"""
    if not md_text:
        return "<p class='empty'>（空文档）</p>"

    # 1. 提取围栏代码块，占位保护
    blocks: List[str] = []
    def _hold(m):
        blocks.append(m.group(2))
        return f"\x00CODEBLOCK{len(blocks)-1}\x00"
    text = _FENCE_RE.sub(_hold, md_text)

    out_lines: List[str] = []
    i = 0
    lines = text.splitlines()
    in_table = False
    table_rows: List[str] = []

    def _flush_table():
        nonlocal in_table, table_rows
        if not in_table:
            return
        in_table = False
        rows = table_rows[:]
        table_rows = []
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

    while i < len(lines):
        raw = lines[i]
        line = raw.rstrip()
        stripped = line.strip()

        # 围栏代码块占位还原
        m = re.match(r"^\x00CODEBLOCK(\d+)\x00$", stripped)
        if m:
            _flush_table()
            code = blocks[int(m.group(1))]
            out_lines.append("<pre><code>{}</code></pre>".format(_esc(code)))
            i += 1
            continue

        # 表格
        if stripped.startswith("|"):
            cells = [c for c in stripped.strip("|").split("|")]
            # 分隔行 |---|---|：丢弃该行，保留表头行继续累计，
            # 等表体行到达后与表头一起 flush（避免表头被提前丢弃）
            if all(re.fullmatch(r":?-{2,}:?", c.strip()) for c in cells if c.strip()):
                i += 1
                continue
            if not in_table:
                _flush_table()
                in_table = True
                table_rows = []
            table_rows.append(cells)
            i += 1
            continue

        _flush_table()

        # 标题
        hm = re.match(r"^(#{1,6})\s+(.+)$", stripped)
        if hm:
            lvl = len(hm.group(1))
            out_lines.append(f"<h{lvl}>{_inline(hm.group(2))}</h{lvl}>")
            i += 1
            continue

        # 引用
        if stripped.startswith(">"):
            out_lines.append("<blockquote>{}</blockquote>".format(_inline(stripped[1:].strip())))
            i += 1
            continue

        # 无序列表
        if re.match(r"^[-*]\s+", stripped):
            out_lines.append("<li class='ul'>{}</li>".format(_inline(re.sub(r"^[-*]\s+", "", stripped))))
            i += 1
            continue

        # 有序列表
        om = re.match(r"^\d+\.\s+(.+)$", stripped)
        if om:
            out_lines.append("<li class='ol'>{}</li>".format(_inline(om.group(1))))
            i += 1
            continue

        # 分割线
        if re.match(r"^-{3,}$", stripped) or re.match(r"^\*{3,}$", stripped):
            out_lines.append("<hr>")
            i += 1
            continue

        # 空行
        if not stripped:
            i += 1
            continue

        # 普通段落
        out_lines.append("<p>{}</p>".format(_inline(stripped)))
        i += 1

    _flush_table()

    # 把连续同类 <li> 分别包进 <ul>（无序）/ <ol>（有序）
    html_body = "\n".join(out_lines)
    html_body = re.sub(
        r"((?:<li class='ol'>.*?</li>\n?)+)",
        lambda m: "<ol>" + re.sub(r"<li class='ol'>(.*?)</li>", r"<li>\1</li>", m.group(1)) + "</ol>",
        html_body)
    html_body = re.sub(
        r"((?:<li class='ul'>.*?</li>\n?)+)",
        lambda m: "<ul>" + re.sub(r"<li class='ul'>(.*?)</li>", r"<li>\1</li>", m.group(1)) + "</ul>",
        html_body)
    return html_body


def _esc(s: str) -> str:
    return _html.escape(s, quote=False)


def _attr(s: str) -> str:
    """转义 HTML 属性值（引号也转义），用于 href/id 等属性上下文。"""
    return _html.escape(s or "", quote=True)


def _safe_link(text: str, url: str) -> str:
    """生成 <a>，过滤 javascript:/vbscript:/data:text/html 等危险协议，防链接注入。"""
    low = (url or "").strip().lower()
    if low.startswith(("javascript:", "vbscript:", "data:text/html")):
        return text
    return f'<a href="{_attr(_html.unescape(url))}" rel="noopener">{text}</a>'


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


def _page(title: str, body: str, nav_links: str, project_name: str) -> str:
    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{_esc(title)} - {_esc(project_name)}</title>
<style>{_BASE_CSS}</style>
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
               dimension_states: Optional[dict] = None) -> dict:
        """渲染完整报告目录。

        Args:
            pipe_result: PipeResult（含 findings / report / errors / 统计）
            kg_stats: CodeKnowledgeGraph.get_stats() 返回值（可选）
            wiki_dir: Wiki .md 输出目录（可选，存在则渲染 wiki.html）
            output_dir: 报告输出目录；默认 {项目上级}/coderef-report/html
            dimension_states: 各维度（audit/kg/wiki）执行状态，用于透明化展示
                              （未执行的维度标注"未执行"而非静默为空/全 0）

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
            results["index.html"] = self._render_index(pipe_result, kg_stats, wiki_dir, dimension_states)
        except Exception as e:
            return {"ok": False, "error": str(e), "files": []}

        written = []
        for fname, content in results.items():
            fp = os.path.join(out, fname)
            with open(fp, "w", encoding="utf-8") as f:
                f.write(content)
            written.append(fp)

        return {"ok": True, "index": os.path.join(out, "index.html"),
                "files": written}

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

    def _render_index(self, pr, kg_stats, wiki_dir, dimension_states=None) -> str:
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
        kgn = kg_d.get("nodes", 0) if kg_status == "done" else (kg_stats or {}).get("node_count", 0)
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
                secs.append(f'<section><h2>审计发现 <span class="badge badge-ok">已执行</span></h2>'
                            f'<p>审计已执行，未发现任何问题。详见 <a href="audit.html">审计发现</a>。</p></section>')
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

        # 业务视角优先：首页先展示业务概览摘要，技术统计降级为支撑信息
        body = ""
        overview_summary = self._read_overview_summary(wiki_dir)
        if overview_summary:
            body += (f'<section class="biz-hero"><h2>业务视角 <span class="badge badge-ok">优先阅读</span></h2>'
                     f'<p>{_esc(overview_summary)}…</p>'
                     f'<p><a href="wiki.html#grp-业务视角">前往业务概览 →</a></p></section>')

        # 公共入口 / 数据流清单（分层人话版的入口级 L1、数据流级 L2）
        entry_links = self._list_wiki_group(wiki_dir, "ENTRIES")
        flow_links = self._list_wiki_group(wiki_dir, "FLOWS")
        if entry_links:
            body += (f'<section><h2>公共入口 <span class="badge badge-ok">{len(entry_links)} 个</span></h2>'
                     f'<p>每个入口的流程人话版，直接回答"这个入口是做什么、怎么做"。</p>'
                     f'{"".join(entry_links)}</section>')
        if flow_links:
            body += (f'<section><h2>模块数据流 <span class="badge badge-ok">{len(flow_links)} 条</span></h2>'
                     f'<p>模块之间如何传递数据，直接回答"谁向谁要东西"。</p>'
                     f'{"".join(flow_links)}</section>')

        body += '<section><h2>报告章节</h2>' + "".join(secs) + "</section>"
        body += '<section><h2>技术概览</h2><p class="empty">以下为代码级统计，业务理解请以上方业务视角为主。</p>' \
                + cards + '</section>' + note
        return _page("CodeRef 审计报告", body, _nav("index.html", self.project_name), self.project_name)

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
            body = (f'<section><h2>审计发现 <span class="badge badge-ok">已执行</span></h2>'
                    f'<p>审计已执行，未发现任何问题。</p></section>')
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