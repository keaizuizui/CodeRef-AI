# -*- coding: utf-8 -*-
"""评估台三个内容面板：产品（F 轨）/ UX（U 轨）/ 技术。

拆出目的：面板只依赖「项目路径 + 快照数据」，不依赖渲染器实例的其它状态；
渲染器只保留编排与页面外壳。快照来自被分析项目（可能是克隆仓库），
所有字段一律按不可信内容转义。
"""

import json
import os
from typing import Optional

from core.html_util import attr as _attr, esc as _esc


def eval_snapshot(project_path: str, name: str) -> dict:
    """读取 `.coderef/<name>` 快照；缺失返回空字典，结构非法返回 error 字段。"""
    path = os.path.join(project_path, ".coderef", name)
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {"error": f"{name} 结构非法"}
    except FileNotFoundError:
        return {}
    except Exception as e:  # noqa: BLE001
        return {"error": f"{name} 读取失败: {e}"}


def eval_metrics(items) -> str:
    return '<div class="cards">' + "".join(
        f'<div class="card"><div class="lbl">{_esc(k)}</div>'
        f'<div class="val">{_esc(str(v if v is not None else "N/A"))}</div></div>'
        for k, v in items) + '</div>'


def pct(value) -> str:
    """契合率等 0~1 数值按百分比展示；未知保持 N/A（不当 0）。"""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return "N/A"
    return f"{round(float(value) * 100)}%"


def stamp(data: dict) -> str:
    """数据生成时间：让读者能判断"结论是不是比页面旧"（U-49 观感防线）。"""
    ts = str(data.get("generated_at") or "")
    return f'数据生成 {ts}' if ts else '数据生成时间未记录'


def q_cell(q) -> str:
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


def eval_button(scene: str, label: str) -> str:
    return ('<button class="tab-btn" onclick="runEval(\'' + _attr(scene) + '\')">'
            + _esc(label) + '</button>'
            '<span class="sc-note" id="eval-status-' + _attr(scene) + '">'
            '本地服务可用时点按钮即评估；离线请用 coderef_report open_server=true</span>')


def source_cell(source) -> str:
    """能力来源：目录兜底是最弱证据，必须显式标注，避免被当成业务能力。"""
    src = str(source or "")
    if src == "directory":
        return ('<span class="badge badge-neutral">directory</span>'
                '<br><span class="sc-note">目录兜底（弱证据）</span>')
    return f'<span class="badge badge-neutral">{_esc(src)}</span>'


def product_panel(project_path: str) -> str:
    """产品评估：F 轨主轴（业务契合度），不评价界面美观。"""
    data = eval_snapshot(project_path, "product_fit.json")
    head = ('<section><h2>产品评估 · F 轨业务契合度'
            '<span class="badge badge-ok">主轴</span></h2>')
    if not data:
        return (head + '<p class="empty">尚未运行产品评估。F 轨逐能力核对业务逻辑是否'
                '支撑真实决策；证据不足时只回证据包（SKIP），不产出三档结论。</p>'
                + eval_button("product", "运行产品评估") + '</section>')
    if data.get("error"):
        return (head + '<p class="empty">' + _esc(data["error"]) + '</p>'
                + eval_button("product", "运行产品评估") + '</section>')

    status = str(data.get("status") or "")
    badge = {"OK": "badge-ok", "SKIP": "badge-missing"}.get(status, "badge-neutral")
    stage = data.get("stage") or {}
    concl = data.get("conclusion") or {}
    parts = [head.replace("</h2>", f'<span class="badge {badge}">{_esc(status)}</span></h2>')]
    # 阶段行：decided_by 为空时不留 " · ）" 残渣；来源翻译成人话
    decided = {"auto": "自动判定", "user": "用户确认"}.get(
        str(stage.get("decided_by") or ""), str(stage.get("decided_by") or ""))
    stage_tail = " · ".join(x for x in (str(stage.get("reason") or ""), decided) if x)
    parts.append(f'<p class="sc-note">阶段：{_esc(str(stage.get("phase") or "-"))}'
                 + (f'（{_esc(stage_tail)}）' if stage_tail else '') + '</p>')
    parts.append(f'<p class="sc-note">{_esc(stamp(data))}</p>')
    parts.append(eval_metrics([
        ("契合率", pct(data.get("fit_rate"))),
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
                f'<td>{source_cell(r.get("source"))}</td>'
                f'<td>{q_cell(r.get("q1_decision"))}</td>'
                f'<td>{q_cell(r.get("q2_input"))}</td>'
                f'<td>{q_cell(r.get("q3_output"))}</td>'
                f'<td>{q_cell(r.get("q4_honesty"))}</td>'
                f'<td>{q_cell(r.get("q5_frequency"))}</td>'
                f'<td><code>{_esc("、".join(r.get("anchors") or []))}</code></td></tr>')
        parts.append('<h3 style="margin:14px 0 8px">F1 能力 × 五问矩阵</h3>'
                     '<div class="scroll-x"><table><thead><tr><th>能力</th><th>来源</th><th>①支撑决策</th>'
                     '<th>②输入现实性</th><th>③输出可行动</th><th>④数据诚实</th>'
                     '<th>⑤频率匹配</th><th>证据锚点</th></tr></thead><tbody>'
                     + "".join(body) + '</tbody></table></div>')
        if len(rows) > 60:
            parts.append(f'<p class="sc-note">仅展示前 60 条，共 {len(rows)} 条能力；'
                         '完整明细见 .coderef/product_fit.json。</p>')

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
    parts.append(eval_button("product", "运行产品评估"))
    parts.append('</section>')
    return "".join(parts)


def ux_panel(project_path: str) -> str:
    """UX 人格走查：只判阻断，不产出体验评分。"""
    data = eval_snapshot(project_path, "ux_fit.json")
    head = ('<section><h2>UX 人格走查 · U 轨'
            '<span class="badge badge-ok">只判阻断</span></h2>')
    if not data:
        return (head + '<p class="empty">尚未运行人格走查。U 轨只回答「呈现问题是否阻断'
                '任务完成」，不产出体验分数；无锚点的发现不输出。</p>'
                + eval_button("ux", "运行 UX 走查") + '</section>')
    if data.get("error"):
        return (head + '<p class="empty">' + _esc(data["error"]) + '</p>'
                + eval_button("ux", "运行 UX 走查") + '</section>')

    status = str(data.get("status") or "")
    persona = data.get("persona") or {}
    verdict = str(data.get("verdict") or "")
    vbadge = {"blocking": "badge-high", "partial": "badge-missing",
              "non_blocking": "badge-ok"}.get(verdict, "badge-missing")
    vlabel = ({"blocking": "阻断", "partial": "部分阻断",
               "non_blocking": "不阻断"}.get(verdict) or "未产出结论")
    parts = [head.replace(
        "</h2>",
        f'<span class="badge {vbadge}">{_esc(vlabel)}</span>'
        f'<span class="badge badge-neutral">{_esc(status or "-")}</span></h2>')]
    parts.append('<p class="warn-line">模拟推断（E4）：以下结论来自人格模拟，'
                 '非真实用户实测；每条发现可回到 file:line（E2）。</p>')
    parts.append(f'<p class="sc-note">人格：{_esc(str(persona.get("name", "-")))}'
                 f'（{_esc(str(persona.get("identity", "")))}）</p>')
    parts.append(f'<p class="sc-note">旅程：{_esc(str(data.get("journey", "")))} · '
                 'simulated=true · 结论 E4（锚点为 E2）</p>')
    parts.append(f'<p class="sc-note">{_esc(stamp(data))}</p>')
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
                     '<div class="scroll-x"><table><thead><tr><th>级别</th><th>问题</th><th>愤怒</th>'
                     '<th>任务中断</th><th>业务后果</th><th>证据锚点</th></tr></thead><tbody>'
                     + "".join(rows) + '</tbody></table></div>')
        if len(findings) > 80:
            parts.append(f'<p class="sc-note">仅展示前 80 条，共 {len(findings)} 条；'
                         '完整明细见 .coderef/ux_fit.json。</p>')
    else:
        parts.append('<p class="empty">'
                     + ('未产出阻断结论（无页面可走查或证据不足）。'
                        if status == "SKIP" else '未发现带锚点的阻断项。')
                     + '</p>')

    unknowns = data.get("unknowns") or []
    if unknowns:
        parts.append('<h3 style="margin:14px 0 8px">未知项</h3><ul>' + "".join(
            f'<li>{_esc(str(u))}</li>' for u in unknowns[:20]) + '</ul>')
    vp = data.get("viewports") or {}
    parts.append(f'<p class="sc-note">视口：{_esc(str(vp.get("evaluated", "")))}'
                 f' · {_esc(str(vp.get("note", "")))}</p>')
    parts.append(eval_button("ux", "运行 UX 走查"))
    parts.append('</section>')
    return "".join(parts)


def _raw_text(value) -> str:
    """原始分项的展示文本：字典压成 k:v，避免渲染出 Python 字典字面量。"""
    if isinstance(value, dict):
        return ",".join(f"{k}:{v}" for k, v in value.items())
    return str(value)


def tech_panel(project_path: str, card: Optional[dict], layout_link: str = "") -> str:
    """技术视图：技术评分明细 + 技术分区图。

    定位：面向架构师 / 技术负责人。非技术读者看「概览」即可，
    这里只把口径、原始分项、证据锚点与技术分区布局摊开，不做业务解释。
    """
    parts = []
    if card and card.get("dimensions"):
        rows = []
        for d in card.get("dimensions") or []:
            raw_items = [f'{k}={_raw_text(v)}' for k, v in (d.get("raw") or {}).items()
                         if v not in ("", None)][:10]
            if d.get("status") == "ok":
                score = str(d.get("score"))
            elif d.get("status") == "not_applicable":
                score = "不适用"
            else:
                score = "证据不足"
            rows.append(
                f'<tr><td>{_esc(d.get("name", d.get("id", "")))}</td>'
                f'<td>{_esc(str(d.get("weight", "")))}</td>'
                f'<td>{_esc(score)}</td>'
                f'<td>{_esc(" · ".join(raw_items) or "-")}</td>'
                f'<td>{_esc("、".join(d.get("evidence") or []) or "-")}</td></tr>')
        gate = card.get("gate") or {}
        parts.append(
            '<section><h2>技术评分明细 <span class="badge badge-neutral">面向技术</span></h2>'
            '<p class="sc-note">同一份记分卡的原始分项与证据锚点；口径、权重与阈值见'
            '「概览 · 评估标准」。</p>'
            '<div class="scroll-x"><table><thead><tr><th>维度</th><th>权重</th><th>得分</th>'
            '<th>原始分项</th><th>证据锚点</th></tr></thead><tbody id="tech-rows">'
            + "".join(rows) + '</tbody></table></div>'
            f'<p class="sc-note" style="margin-top:10px">置信度 '
            f'{_esc(card.get("confidence", "-"))} · 证据覆盖 '
            f'{int(round(float(card.get("coverage") or 0) * 100))}% · 阈值 '
            f'{_esc(str(gate.get("min_score", "")))}（CI 默认只告警）</p></section>')
    else:
        parts.append('<section><h2>技术评分明细 <span class="badge badge-missing">未生成</span></h2>'
                     '<p class="empty">暂无记分卡数据，请先运行 coderef_report。</p></section>')

    if layout_link:
        parts.append(
            '<section><h2>技术分区图 <span class="badge badge-neutral">面向技术</span></h2>'
            '<p class="sc-note">按技术角色（入口 / 接口 / 业务 / 数据 / 基础）划分的模块布局，'
            '用于排查分层与依赖，不回答业务问题。</p>'
            # 与被分析项目的画布隔离，避免读取父页 token
            f'<iframe src="{_attr(layout_link)}" sandbox="allow-scripts allow-popups" '
            'style="width:100%;height:78vh;min-height:560px;border:1px solid #2a2d3a;'
            'border-radius:8px;background:#0f1117"></iframe></section>')
    else:
        parts.append('<section><h2>技术分区图 <span class="badge badge-missing">未生成</span></h2>'
                     '<p class="empty">尚无技术分区图。运行 '
                     '<code>coderef_architecture</code> 生成（产物前缀 arch_layout_）。</p></section>')
    return "".join(parts)
