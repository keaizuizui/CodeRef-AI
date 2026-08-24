# -*- coding: utf-8 -*-
"""
GovDashboard v1.0 —— CodeRef 5.1 体检报告与跨期趋势

默认 JSON 由 HealthCycle.report 提供；本模块负责把人看的自包含 HTML 报告
渲染出来（复用 health_dashboard 的自包含离线模式，内联 SVG 趋势图，零 CDN）。

报告健康度分级：
  · score >= 90  优（绿色）
  · score >= 70  良（黄色）
  · else         差（红色）
"""

import html
import json
from datetime import datetime
from typing import Dict, Any

from loguru import logger


def _esc(s: Any) -> str:
    return html.escape(str(s if s is not None else ""))


def _health_color(rate: float) -> str:
    if rate >= 0.9:
        return "#22c55e"
    if rate >= 0.7:
        return "#eab308"
    return "#ef4444"


def _svg_spark(pts: list, w: int = 520, h: int = 140) -> str:
    """基于 (x_index, value) 序列生成内联 SVG 折线。"""
    if not pts:
        return "<p style='color:#94a3b8'>暂无跨期数据（至少需要一个已关闭的体检周期）</p>"
    vmax = max(pts) or 1
    step = w / max(len(pts) - 1, 1)
    coords = [(i * step, h - 12 - (v / vmax) * (h - 24)) for i, v in enumerate(pts)]
    poly = " ".join(f"{x:.1f},{y:.1f}" for x, y in coords)
    dots = "".join(
        f"<circle cx='{x:.1f}' cy='{y:.1f}' r='3.5' fill='#3b82f6'/>"
        for x, y in coords)
    labels = "".join(
        f"<text x='{x:.1f}' y='{h}' fill='#94a3b8' font-size='10' "
        f"text-anchor='middle'>{i + 1}</text>"
        for i, (x, _y) in enumerate(coords))
    return (
        f"<svg width='{w}' height='{h}' viewBox='0 0 {w} {h}'>"
        f"<polyline points='{poly}' fill='none' stroke='#3b82f6' stroke-width='2.5'/>"
        f"{dots}{labels}</svg>")


def report_to_html(report: Dict[str, Any], title: str = "架构治理体检报告") -> str:
    cyc = report.get("active_cycle") or {}
    summary = report.get("summary") or {}
    counts = report.get("status_counts") or {}
    trend = report.get("trend") or []

    rate = summary.get("completion_rate", 0) if summary else 0

    rows = "".join(
        "<tr><td>" + _esc("".join(
            ["cycle_id:", i.get("id", "")])) + "</td><td>" + _esc(
            i.get("gap_type", "")) + "</td><td>" + _esc(
            i.get("module", "")) + "</td><td>" + _esc(
            i.get("severity", "")) + "</td><td><b>" + _esc(
            i.get("status", "")) + "</b></td></tr>"
        for i in report.get("_issues", [])[:200])

    css = """
    body{font-family:-apple-system,'Segoe UI',Roboto,sans-serif;background:#f8fafc;color:#0f172a;margin:24px}
    .card{background:#fff;border:1px solid #e2e8f0;border-radius:12px;padding:20px;margin-bottom:18px}
    h1{font-size:22px} h2{font-size:16px;color:#334155}
    .kv{display:flex;gap:18px;flex-wrap:wrap} .kv div{flex:1;min-width:120px}
    .kv .v{font-size:26px;font-weight:700} .kv .k{font-size:12px;color:#64748b}
    table{border-collapse:collapse;width:100%;font-size:13px}
    th,td{border:1px solid #e2e8f0;padding:6px 10px;text-align:left}
    th{background:#f1f5f9}
    .pill{display:inline-block;padding:2px 10px;border-radius:999px;color:#fff;font-size:12px}
    """
    return f"""<!DOCTYPE html>
<html lang="zh"><head><meta charset="utf-8">
<title>{html.escape(title)}</title><style>{css}</style></head>
<body>
<h1>{html.escape(title)}</h1>
<p style="color:#64748b">CodeRef-AI 5.1 周期性架构治理体检</p>

<div class="card"><h2>本期体检 {_esc(cyc.get('name',''))} ({_esc(cyc.get('id',''))})</h2>
<div class="kv">
  <div><div class="v" style="color:{_health_color(rate)}">{rate:.0%}</div><div class="k">完成率</div></div>
  <div><div class="v">{_esc(summary.get('remaining', summary.get('total', 0) - summary.get('done', 0)))}</div><div class="k">剩余</div></div>
  <div><div class="v">{_esc(summary.get('total', 0))}</div><div class="k">本期总数</div></div>
  <div><div class="v" style="color:{_health_color(1 - (summary.get('recurred', 0) / max(summary.get('total', 1), 1)))}">{_esc(summary.get('recurred', 0))}</div><div class="k">复发</div></div>
</div></div>

<div class="card"><h2>在途状态分布</h2><div class="kv">
{''.join(f"<div><div class='v'>{v}</div><div class='k'>{_esc(k)}</div></div>" for k, v in counts.items())}
</div></div>

<div class="card"><h2>跨期趋势（已完成体检周期的整改量）</h2>
{_svg_spark([x.get('done', 0) for x in trend])}
<p style="color:#64748b;font-size:12px">横轴为第 1..N 个已关闭周期，纵轴为该期完成(归档+复验达标)数量。</p>
</div>

<div class="card"><h2>在途治理项（前 200 条）</h2>
<table><thead><tr><th>ID</th><th>类型</th><th>模块</th><th>严重级</th><th>状态</th></tr></thead>
<tbody>{rows}</tbody></table>
</div>

<div class="card"><p style="color:#94a3b8;font-size:12px">
生成时间 {_esc(datetime.now().strftime('%Y-%m-%d %H:%M'))}</p></div>
</body></html>"""


def render_report(project_path: str, output_dir: str = "", cid: str = "",
                  include_issues: bool = True) -> Dict[str, Any]:
    """生成体检报告并（可选）写出 HTML，返回路径与 JSON 结构。"""
    from core.healthcycle import HealthCycle
    hc = HealthCycle(project_path)
    report = hc.report(cid=cid)
    if include_issues:
        report["_issues"] = hc.store.list_issues(cycle_id=(cid or
            (report.get("active_cycle") or {}).get("id", "")), view="open",
            limit=500)
    html_str = report_to_html(report)
    import os
    out_dir = output_dir or os.path.join(os.path.abspath(project_path),
                                         ".coderef")
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, "gov_report.html")
    with open(path, "w", encoding="utf-8") as f:
        f.write(html_str)
    logger.info(f"体检报告已写出: {path}")
    report["report_html"] = path
    report["tool"] = "coderef_gov_report"
    return report