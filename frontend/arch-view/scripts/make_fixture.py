# -*- coding: utf-8 -*-
"""
make_fixture.py — 生成前端联调用真实样本数据 fixtures/vimax.json。

复用 core 数据管线（analyze_gap + 知识图谱），按 v5.15.0 数据契约
（docs/5.15.0-plan.md §4.4）整形为视图友好结构。P3 阶段此整形逻辑
迁移进 core/arch_view.py 正式接入，本脚本仅保留作前端开发期再生成用。
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
sys.path.insert(0, ROOT)

from core.arch_audit import locate_kg_db, module_of
from core.arch_gap_analyzer import _is_test_module, _match_module_ids, analyze_gap
from core.graph_closure import load_graph

VIMAX = r"D:\CodeRef-GoodJob\Coderef-Test\coderef-positive-test\samples\workspace\vimax"
OUT = os.path.join(HERE, "..", "src", "fixtures", "vimax.json")


def main() -> None:
    ta_path = os.path.join(VIMAX, ".coderef", "target_arch.json")
    with open(ta_path, "r", encoding="utf-8") as f:
        ta = json.load(f)

    db = locate_kg_db(VIMAX)
    nodes, adj = load_graph(db)
    gap_result = analyze_gap(VIMAX, ta)
    gaps = gap_result.get("gaps", [])

    # ── gap 标记集合 ──
    unassigned, cycle_mods, twin = set(), set(), {}
    violation_pairs = []  # (from, to, detail)
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
    roles = []
    module_role = {}  # 全路径模块名 → 角色 id
    for role in ta.get("tech_roles", []):
        rid = role.get("id", "")
        matched = _match_module_ids(nodes, VIMAX, role.get("target_modules", []))
        mods = sorted(
            m for m in (
                module_of(nodes[nid], VIMAX) or nodes[nid].get("name", "?")
                for nid in matched
            ) if not _is_test_module(m)
        )
        for m in mods:
            module_role[m] = rid
        roles.append({
            "id": rid,
            "name": role.get("name", rid),
            "modules": mods,
            "missing": False,
            "desc": "",
        })

    # ── 全量模块（含归属/标记/调用）──
    module_names = []
    for nid, n in nodes.items():
        if n.get("type") != "module":
            continue
        m = module_of(n, VIMAX) or n.get("name", "?")
        if _is_test_module(m):
            continue
        module_names.append(m)

    known = set(module_names)
    call_out = {}
    call_in = {}
    for m in module_names:
        outs = []
        for tgt in adj.get("mod:" + m, []) or []:
            t_mod = module_of(nodes.get(tgt, {}), VIMAX) if tgt in nodes else ""
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
    violations = []
    for fm, to, detail in violation_pairs:
        if fm in known and to in known:
            violations.append({"from": fm, "to": to, "kind": "cross_layer",
                               "detail": detail})
    seen = set()
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
    } for f in ta.get("business_flows", [])]

    # ── summary / gaps 透传 ──
    summary = {
        "alignment": (gap_result.get("alignment") or {}).get("module_assigned", 0),
        "role_coverage": (gap_result.get("alignment") or {}).get("role_coverage", 0),
        "module_total": len(modules),
        "gap_total": len(gaps),
        "cycle_total": len(cycle_mods),
        "high": (gap_result.get("summary") or {}).get("high", 0),
        "medium": (gap_result.get("summary") or {}).get("medium", 0),
        "low": (gap_result.get("summary") or {}).get("low", 0),
    }
    report_gaps = [
        {k: g.get(k) for k in ("type", "severity", "detail", "module") if k in g}
        for g in gaps
    ]

    data = {
        "project": ta.get("project", os.path.basename(VIMAX)),
        "summary": summary,
        "roles": roles,
        "flows": flows,
        "modules": modules,
        "violations": violations,
        "gaps": report_gaps,
        "meta": {
            "generated_at": "",
            "tool": "arch_view_fixture",
            "note": "开发期联调数据，生产由 coderef_arch_canvas 注入",
        },
    }

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"fixture written: {OUT}")
    print(f"roles={len(roles)} modules={len(modules)} flows={len(flows)} "
          f"gaps={len(gaps)} violations={len(violations)}")
    print(f"cycle_mods={sorted(cycle_mods)} unassigned={sorted(unassigned)}")


if __name__ == "__main__":
    main()
