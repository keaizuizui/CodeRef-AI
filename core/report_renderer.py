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


def _safe_link(text: str, url: str) -> str:
    """生成 <a>，过滤 javascript:/vbscript:/data:text/html 等危险协议，防链接注入。"""
    low = (url or "").strip().lower()
    if low.startswith(("javascript:", "vbscript:", "data:text/html")):
        return text
    return f'<a href="{url}" rel="noopener">{text}</a>'


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
.empty { color:#6b7280; font-style:italic; }
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
               output_dir: Optional[str] = None) -> dict:
        """渲染完整报告目录。

        Args:
            pipe_result: PipeResult（含 findings / report / errors / 统计）
            kg_stats: CodeKnowledgeGraph.get_stats() 返回值（可选）
            wiki_dir: Wiki .md 输出目录（可选，存在则渲染 wiki.html）
            output_dir: 报告输出目录；默认 {项目上级}/coderef-report/html

        Returns:
            {"ok": bool, "index": 绝对路径, "files": [..], "error": str?}
        """
        out = output_dir or os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "coderef-report", "html")
        os.makedirs(out, exist_ok=True)

        results = {}
        try:
            results["audit.html"] = self._render_audit(pipe_result)
            results["kg.html"] = self._render_kg(kg_stats)
            results["wiki.html"] = self._render_wiki(wiki_dir)
            results["index.html"] = self._render_index(pipe_result, kg_stats, wiki_dir)
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

    def _render_index(self, pr, kg_stats, wiki_dir) -> str:
        findings = getattr(pr, "findings", []) or []
        h = sum(1 for f in findings if getattr(f, "tier", None) and f.tier.value == "high")
        m = sum(1 for f in findings if getattr(f, "tier", None) and f.tier.value == "medium")
        lo = sum(1 for f in findings if getattr(f, "tier", None) and f.tier.value == "low")
        adv = sum(1 for f in findings if getattr(f, "kind", "") == "advice")
        kgn = (kg_stats or {}).get("node_count", 0)
        kge = (kg_stats or {}).get("edge_count", 0)
        has_wiki = bool(wiki_dir and os.path.isdir(wiki_dir))

        cards = f"""<div class="cards">
          <div class="card"><div class="lbl">扫描文件</div><div class="val">{getattr(pr, 'total_files', 0)}</div></div>
          <div class="card"><div class="lbl">代码行</div><div class="val">{getattr(pr, 'total_lines', 0)}</div></div>
          <div class="card"><div class="lbl">HIGH</div><div class="val" style="color:#fca5a5">{h}</div></div>
          <div class="card"><div class="lbl">MEDIUM</div><div class="val" style="color:#fcd34d">{m}</div></div>
          <div class="card"><div class="lbl">LOW</div><div class="val" style="color:#93c5fd">{lo}</div></div>
          <div class="card"><div class="lbl">建议</div><div class="val" style="color:#6ee7b7">{adv}</div></div>
          <div class="card"><div class="lbl">图谱节点</div><div class="val">{kgn}</div></div>
          <div class="card"><div class="lbl">图谱边</div><div class="val">{kge}</div></div>
        </div>"""

        links = []
        links.append(f'<h2>审计发现</h2><p>共 {len(findings)} 条发现，'
                     f'详见 <a href="audit.html">审计发现</a>。</p>')
        links.append('<h2>知识图谱</h2><p>项目结构图谱统计，详见 <a href="kg.html">知识图谱</a>。</p>')
        if has_wiki:
            links.append('<h2>Wiki 文档</h2><p>项目文档，详见 <a href="wiki.html">Wiki 文档</a>。</p>')

        # 统计口径透明化：披露扫描时间与图谱时间，避免旧图谱被当作本次结果
        ts = getattr(pr, "scan_ts", "") or ""
        kgt = getattr(pr, "kg_built_at", "") or (kg_stats or {}).get("built_at", "")
        scope = getattr(pr, "scope_text", "") or ""
        note = "<section><h2>统计口径</h2>"
        note += f"<p>本次扫描时间：<code>{ts or '未记录'}</code></p>"
        note += f"<p>知识图谱构建：<code>{kgt or '未重建'}</code>（图谱可能滞后于代码）</p>"
        if scope:
            note += f"<p>审计范围：{_esc(scope)}</p>"
        note += "</section>"

        body = cards + "<section><h2>报告章节</h2>" + "".join(links) + "</section>" + note
        return _page("CodeRef 审计报告", body, _nav("index.html", self.project_name), self.project_name)

    def _render_audit(self, pr) -> str:
        findings = getattr(pr, "findings", []) or []
        if not findings:
            body = '<section><h2>审计发现</h2><p class="empty">暂无发现</p></section>'
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

    def _render_kg(self, kg_stats) -> str:
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

    def _render_wiki(self, wiki_dir) -> str:
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

        sections = []
        for fp in md_files:
            rel = os.path.relpath(fp, wiki_dir)
            try:
                with open(fp, "r", encoding="utf-8", errors="replace") as f:
                    content = f.read()
            except Exception as e:
                sections.append(f'<section><h2>{_esc(rel)}</h2><p class="empty">读取失败: {_esc(str(e))}</p></section>')
                continue
            if not content.strip():
                continue
            sections.append(f'<section><h2 id="{_esc(rel)}">{_esc(rel)}</h2>{md_to_html(content)}</section>')
        if not sections:
            sections.append('<section><h2>Wiki 文档</h2><p class="empty">所有文档均为空</p></section>')
        body = "\n".join(sections)
        return _page("Wiki 文档", body, _nav("wiki.html", self.project_name), self.project_name)