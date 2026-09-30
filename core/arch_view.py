# -*- coding: utf-8 -*-
"""
arch_view — 架构可视化视图渲染器（决策者报告 + 分区画布，5.15 重写版）。

数据管线复用 arch_gap_analyzer（差距/对齐度）+ 知识图谱 + canvas_generator 的
角色归属口径，整形为「视图友好结构」（见 docs/5.15.0-plan.md §4.4），注入
core/web/arch_view.html（React 单文件构建产物，占位符 __ARCH_DATA_PLACEHOLDER__），
输出自包含 HTML。前端纯渲染：报告首屏（叙事式）+ 分区画布（角色泳道/主干流/违规边）。
"""

import json
import os
from datetime import datetime
from html import escape
from typing import Dict, List, Optional

from loguru import logger

from core.arch_audit import locate_kg_db, module_of
from core.arch_gap_analyzer import _is_test_module, _match_module_ids, analyze_gap
from core.graph_closure import load_graph

_TEMPLATE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         "web", "arch_view.html")
# 占位符含尾随 null：构建产物中模板为 `window.__ARCH_DATA__ = /*p*/null;`，
# 整体替换为 data_json 后得到合法的 `window.__ARCH_DATA__ = {...};`。
_PLACEHOLDER = "/*__ARCH_DATA_PLACEHOLDER__*/null"


def _load_stored_arch(project_path: str) -> Optional[dict]:
    path = os.path.join(project_path, ".coderef", "target_arch.json")
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return None


def build_arch_data(
    project_path: str,
    target_arch: Optional[dict] = None,
) -> dict:
    """整形成视图友好数据（§4.4 契约）。复用 gap 分析 + 图谱，业务计算全在 Python 侧。

    Returns:
        {project, summary, roles, flows, modules, violations, gaps, meta}
    """
    if target_arch is None:
        target_arch = _load_stored_arch(project_path) or {
            "version": "5.0", "project": os.path.basename(project_path)}

    db = locate_kg_db(project_path)
    has_kg = bool(db and os.path.exists(db))
    gap_result = analyze_gap(project_path, target_arch) if has_kg else None
    nodes, adj = (load_graph(db) if has_kg else ({}, {}))

    gaps = (gap_result or {}).get("gaps", [])

    # ── gap 标记集合 ──
    unassigned: set = set()
    cycle_mods: set = set()
    twin: Dict[str, str] = {}
    violation_pairs: List[tuple] = []  # (from, to, detail)
    for g in gaps:
        t = g.get("type", "")
        if t == "unassigned":
            unassigned.add(g.get("module", ""))
        elif t == "cycle":
            cycle_mods.update(g.get("modules", []))
        elif t == "dependency_violation":
            violation_pairs.append(
                (g.get("from_module", ""), g.get("to_module", ""), g.get("detail", "")))
        elif t == "twin_identity":
            for c in g.get("copies", []):
                twin[c.get("module", "")] = c.get("verdict", "")

    # ── 角色 → 归属模块（口径：过滤测试模块，与 canvas_generator 一致）──
    roles: List[dict] = []
    module_role: Dict[str, str] = {}
    for role in target_arch.get("tech_roles") or []:
        rid = role.get("id", "")
        matched = _match_module_ids(nodes, project_path,
                                    role.get("target_modules", []))
        mods = sorted(
            m for m in (
                module_of(nodes[nid], project_path) or nodes[nid].get("name", "?")
                for nid in matched
            ) if not _is_test_module(m)
        )
        for m in mods:
            module_role[m] = rid
        roles.append({
            "id": rid,
            "name": role.get("name", rid),
            "modules": mods,
            "missing": len(mods) == 0,
            "desc": "",
        })

    # ── 全量模块（含归属/标记/调用）──
    module_names: List[str] = []
    for nid, n in nodes.items():
        if n.get("type") != "module":
            continue
        m = module_of(n, project_path) or n.get("name", "?")
        if _is_test_module(m):
            continue
        module_names.append(m)

    known = set(module_names)
    call_out: Dict[str, List[str]] = {}
    call_in: Dict[str, List[str]] = {}
    for m in module_names:
        outs: List[str] = []
        for tgt in adj.get("mod:" + m, []) or []:
            t_mod = module_of(nodes.get(tgt, {}), project_path) if tgt in nodes else ""
            if t_mod and t_mod in known and t_mod != m and t_mod not in outs:
                outs.append(t_mod)
        call_out[m] = outs
        for o in outs:
            call_in.setdefault(o, []).append(m)

    modules = [{
        "name": m,
        "short": m.split("/")[-1] if "/" in m else m,
        "role": module_role.get(m, None),
        "is_test": False,
        "in_cycles": m in cycle_mods,
        "unassigned": m in unassigned,
        "twin": twin.get(m, None),
        "call_out": call_out.get(m, []),
        "call_in": call_in.get(m, []),
    } for m in module_names]

    # ── 违规边：dependency_violation + cycle 内部调用边 ──
    violations: List[dict] = []
    for fm, to, detail in violation_pairs:
        if fm in known and to in known:
            violations.append({"from": fm, "to": to, "kind": "cross_layer",
                               "detail": detail})
    seen: set = set()
    for m in sorted(cycle_mods):
        if m not in known:
            continue
        for o in call_out.get(m, []):
            if o in cycle_mods and (m, o) not in seen:
                seen.add((m, o))
                violations.append({"from": m, "to": o, "kind": "cycle",
                                   "detail": "循环依赖成员"})

    # ── flows（业务主干流）──
    flows = [{
        "name": f.get("name", f.get("id", "")),
        "steps": [st.get("name", st.get("id", "")) for st in f.get("steps", [])],
        "desc": "",
    } for f in target_arch.get("business_flows") or []]

    # ── summary / gaps 透传（无图谱时 gap_result 为 None，回退空字典）──
    s = (gap_result or {}).get("summary") or {}
    a = (gap_result or {}).get("alignment") or {}
    # U-46：空图谱/无代码态走 no_code 口径（对齐 arch_audit no_code 分支）——
    # 无非测试代码模块时不得按"0 模块 + 对齐度 100% + 整体健康"渲染，
    # alignment/role_coverage 置 None 表示不适用，前端据此抑制百分比与健康表述。
    no_code = len(module_names) == 0
    summary = {
        "no_code": no_code,
        "alignment": None if no_code else a.get("module_assigned", 0),
        "role_coverage": None if no_code else a.get("role_coverage", 0),
        "module_total": len(modules),
        "gap_total": len(gaps),
        "cycle_total": len(cycle_mods),
        "high": s.get("high", 0),
        "medium": s.get("medium", 0),
        "low": s.get("low", 0),
    }
    report_gaps = [
        {k: g.get(k) for k in ("type", "severity", "detail", "module") if k in g}
        for g in gaps
    ]

    # v5.16：业务架构树（自动归纳 + LLM 大白话，缺 key 静态兜底）——画布主数据
    from core.arch_framework import build_framework_enhanced
    fw = build_framework_enhanced(project_path, target_arch)

    return {
        "project": target_arch.get("project", os.path.basename(project_path)),
        "summary": summary,
        "framework": fw["framework"],
        "modules_graph": {
            "roles": roles,
            "flows": flows,
            "modules": modules,
            "violations": violations,
            "gaps": report_gaps,
        },
        "meta": {
            "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "tool": "coderef_arch_canvas",
            "has_kg": has_kg,
        },
    }


def render_arch_view(
    project_path: str,
    target_arch: Optional[dict] = None,
    output_dir: Optional[str] = None,
    title: str = "架构总览",
) -> str:
    """生成自包含 HTML 架构视图（报告首屏 + 分区画布）。

    Args:
        project_path: 目标项目路径。
        target_arch: 目标架构 JSON（缺省读取已存储的 <project>/.coderef/target_arch.json）。
        output_dir: 输出目录（默认 <project>/.coderef/）。
        title: 页面标题。

    Returns:
        HTML 文件路径。
    """
    data = build_arch_data(project_path, target_arch)
    # `<` 转义为 \u003c（JS/JSON 合法），防 detail 等文本含 `</script>` 提前闭合脚本
    data_json = json.dumps(data, ensure_ascii=False).replace("<", "\\u003c")

    with open(_TEMPLATE, "r", encoding="utf-8") as f:
        template = f.read()
    if _PLACEHOLDER not in template:
        raise ValueError(
            "arch_view 模板缺少数据占位符 %s，请重新构建 frontend/arch-view 并同步 core/web/arch_view.html"
            % _PLACEHOLDER)
    html = template.replace(_PLACEHOLDER, data_json).replace(
        "<title>架构总览</title>", f"<title>{escape(title)}</title>")

    if not output_dir:
        output_dir = os.path.join(project_path, ".coderef")
    os.makedirs(output_dir, exist_ok=True)
    filename = f"arch_canvas_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}.html"
    filepath = os.path.join(output_dir, filename)
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(html)
    logger.info(f"[arch_view] 架构视图已生成: {filepath} ({len(html):,} bytes)")
    return filepath
