# -*- coding: utf-8 -*-
"""arch_flow — 业务流程泳道图渲染（v5.18.0）。

把 `business_semantics` 的标注结果渲染成**确定性**泳道画布：

    X 轴 = 流程阶段（触发 → 准入校验 → 执行 → 落库 → 通知 → 交付）
    Y 轴 = 泳道（用户界面 / 接口层 / 业务执行 / 数据存储 / 支付 / 通知 / 定时 / 基础）
    格子 = 模块卡片；跨泳道或跨阶段的调用 = 交接点

不做力导向、不做自动布局：格子位置由标注决定，因此同一份标注必然渲染出同一张图
（可复现）。未判定的模块进「未归类」泳道 / 「未归类阶段」列，并显示数量，
不假装已经归类。

纯静态自包含 HTML（无 CDN、无 JS 框架），离线可打开。
"""

import html as _html
import os
from datetime import datetime
from typing import Dict, List

_CSS = """
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC",
"Microsoft YaHei",sans-serif;background:#0f1117;color:#e5e7eb;line-height:1.55}
.wrap{padding:20px}
.top{display:flex;justify-content:space-between;align-items:flex-start;
gap:16px;flex-wrap:wrap;margin-bottom:14px}
h1{font-size:18px;color:#f1f5f9}
.meta{font-size:12px;color:#6b7280}
.legend{display:flex;gap:14px;flex-wrap:wrap;font-size:12px;color:#9ca3af;margin:10px 0 16px}
.dot{display:inline-block;width:9px;height:9px;border-radius:3px;margin-right:5px;
vertical-align:middle}
.dot-handoff{background:#fcd34d}
.dot-value{background:#6ee7b7}
.dot-plain{background:#4b5563}
.board{display:grid;gap:8px;min-width:1080px}
.head{font-size:12px;font-weight:700;color:#9ca3af;background:#14161f;
border:1px solid #2a2d3a;border-radius:8px;padding:8px 10px;text-align:center}
.lane{font-size:12px;font-weight:700;color:#c9cdd4;background:#14161f;
border:1px solid #2a2d3a;border-radius:8px;padding:8px 10px;
display:flex;align-items:center;gap:6px}
.lane .cnt{font-weight:400;color:#6b7280}
.cell{background:#14161f;border:1px dashed #232733;border-radius:8px;
padding:6px;min-height:56px;display:flex;flex-direction:column;gap:6px}
.card{background:#1a1d2e;border:1px solid #2a2d3a;border-left:3px solid #4b5563;
border-radius:6px;padding:6px 8px;font-size:12px}
.card.value{border-left-color:#6ee7b7}
.card.handoff{border-left-color:#fcd34d}
.card .t{color:#e5e7eb;font-weight:600;word-break:break-all}
.card .a{color:#6b7280;font-size:11px;word-break:break-all}
.pill{display:inline-block;font-size:10px;padding:1px 6px;border-radius:5px;
margin-top:3px;margin-right:3px}
.pill-v{background:#1d3b33;color:#6ee7b7}
.pill-h{background:#3b2f1d;color:#fcd34d}
.pill-u{background:#3b1d1d;color:#fca5a5}
.scroll{overflow-x:auto}
.note{font-size:12px;color:#9ca3af;margin-top:14px}
.warn{font-size:12px;color:#fcd34d;margin-top:8px}
"""


def _esc(s) -> str:
    return _html.escape(str(s or ""), quote=False)


def _render_cards(nodes: List[dict], handoff_modules: set) -> str:
    cards = []
    for n in nodes:
        cls = "card"
        if n.get("value_node"):
            cls += " value"
        elif n["module"] in handoff_modules:
            cls += " handoff"
        pills = ""
        if n.get("value_node"):
            pills += '<span class="pill pill-v">交付点</span>'
        if n["module"] in handoff_modules:
            pills += '<span class="pill pill-h">交接点</span>'
        if str(n.get("evidence_level")) == "E4":
            pills += '<span class="pill pill-u">LLM 推断</span>'
        cards.append(
            f'<div class="{cls}"><div class="t">{_esc(n.get("title"))}</div>'
            f'<div class="a">{_esc(n["module"])}</div>{pills}</div>')
    return "".join(cards)


def render_html(data: dict, title: str = "业务流程视图") -> str:
    """按 actor × stage 渲染泳道画布（纯静态、确定性）。"""
    stages = [s["id"] for s in data.get("stages") or []]
    actors = [a["id"] for a in data.get("actors") or []]
    nodes = data.get("nodes") or []
    edges = data.get("edges") or []

    # 交接点：作为出边的模块打标（读者沿列向右看就知道要交接给谁）
    handoff_modules = {e["from"] for e in edges if e.get("handoff")}
    grid: Dict[tuple, List[dict]] = {}
    for n in nodes:
        grid.setdefault((n["actor"], n["stage"]), []).append(n)

    stage_names = {s["id"]: s["name"] for s in data.get("stages") or []}
    actor_names = {a["id"]: a["name"] for a in data.get("actors") or []}
    counts_by_actor: Dict[str, int] = {}
    for n in nodes:
        counts_by_actor[n["actor"]] = counts_by_actor.get(n["actor"], 0) + 1

    cols = f"180px repeat({len(stages)}, minmax(200px,1fr))"
    head_cells = '<div class="head">泳道 / 阶段</div>' + "".join(
        f'<div class="head">{_esc(stage_names.get(s, s))}</div>' for s in stages)
    rows = []
    for a in actors:
        cnt = counts_by_actor.get(a, 0)
        if a == "unlabeled" and cnt == 0:
            continue  # 未归类泳道为空时不占位
        lane = (f'<div class="lane">{_esc(actor_names.get(a, a))}'
                f'<span class="cnt">{cnt}</span></div>')
        cells = []
        for s in stages:
            items = grid.get((a, s)) or []
            if s == "unlabeled" and not items:
                cells.append('<div class="cell"></div>')
                continue
            cells.append('<div class="cell">'
                         + _render_cards(items, handoff_modules) + '</div>')
        rows.append(lane + "".join(cells))

    s = data.get("summary") or {}
    unknowns = data.get("unknowns") or []
    note = "".join(f'<div class="warn">· {_esc(u)}</div>' for u in unknowns)

    return f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="UTF-8">
<title>{_esc(title)} - {_esc(data.get("project"))}</title>
<style>{_CSS}</style></head><body><div class="wrap">
<div class="top">
  <div><h1>{_esc(title)}</h1>
  <div class="meta">{_esc(data.get("project"))} · 生成 {_esc(data.get("generated_at"))}
   · 模块 {s.get("modules", 0)} · 角色已判 {s.get("actor_labeled", 0)}
   · 阶段已判 {s.get("stage_labeled", 0)} · 交接点 {s.get("handoffs", 0)}
   · 交付点 {s.get("value_nodes", 0)}</div></div>
</div>
<div class="legend">
  <span><span class="dot dot-handoff"></span>交接点（跨泳道/跨阶段调用）</span>
  <span><span class="dot dot-value"></span>交付点（写库/导出/发布/扣款）</span>
  <span><span class="dot dot-plain"></span>普通模块</span>
</div>
<div class="scroll"><div class="board" style="grid-template-columns:{cols}">
{head_cells}
{"".join(rows)}
</div></div>
<div class="note">读法：从左到右＝流程推进（触发 → 校验 → 执行 → 落库 → 通知 → 交付）；
从上到下＝不同角色/系统。含「交接点」标记的卡片表示它会把流程交给别的泳道或下一阶段。
actor/stage 为启发式/LLM 标注，不是业务方确认口径。</div>
{note}
</div></body></html>"""


def render_file(project_path: str, data: dict,
                output_dir: str = None, title: str = "业务流程视图") -> str:
    out_dir = output_dir or os.path.join(os.path.abspath(project_path), ".coderef")
    os.makedirs(out_dir, exist_ok=True)
    text = render_html(data, title=title)
    name = f"arch_flow_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}.html"
    fp = os.path.join(out_dir, name)
    with open(fp, "w", encoding="utf-8") as f:
        f.write(text)
    return fp
