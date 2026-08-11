# -*- coding: utf-8 -*-
"""
arch_audit — 架构腐化诊断（coderef_arch_audit）

背景：MCP 审计引擎（coderef_scan 的 11 个维度）检测单元是文件/函数/依赖，
看不到跨模块的架构症状：循环依赖、上帝模块、分层违例、异常模块规模。
本模块把"架构诊断层"补上，复用知识图谱 CALLS 边做模块级静态诊断。

定位：静态优先、确定性优先。数据只来自知识图谱 CALLS 边，不依赖 LLM。
它是"非编程人员验证工程结构是否健康"的入口，也是编程 AI 的客观参考。

诊断维度（模块 = 文件，file_path 的 basename 去 .py）：
  cycles        模块 CALLS 图强连通分量（SCC）尺寸 ≥2 或自环 → 循环依赖
  god_modules   模块扇出超过阈值（依赖过多下游）→ 上帝模块
  layer_viol    低层模块依赖高层模块（如 config 依赖 core）→ 分层违例
  large_modules 单模块符号数超阈值 → 异常模块规模
以上聚合为 0-10 架构健康度。

设计原则：
- 复用 core.graph_closure.load_graph（与 flow_verify / wiki_cross_verify 同源底座）。
- 模块级 CALLS 图：跨模块边聚合、剔除自环，避免符号级噪声。
- 低风险：只读图谱，无副作用。
"""

import os
from collections import defaultdict
from typing import Dict, List

from config.settings import (
    ARCH_SCC_CYCLE_MIN_SIZE,
    ARCH_GOD_FAN_OUT_THRESHOLD,
    ARCH_LARGE_MODULE_SYMBOL_THRESHOLD,
    ARCH_HEALTH_WEIGHT_CYCLE,
    ARCH_HEALTH_WEIGHT_GOD,
    ARCH_HEALTH_WEIGHT_LAYER,
    ARCH_HEALTH_WEIGHT_LARGE,
)
from core.graph_closure import load_graph


# 目录 → 分层（3=应用层 2=引擎层 1=基础层）；未知目录保守视为引擎层
_LAYER_ORDER = {"demo-app": 3, "app": 3, "frontend": 3,
                "core": 2, "engine": 2,
                "config": 1, "utils": 1, "common": 1, "lib": 1}
_LAYER_NAME = {3: "应用层", 2: "引擎层", 1: "基础层"}
_DEFAULT_LAYER = 2


def locate_kg_db(project_path: str):
    """根据项目路径定位知识图谱 db（复用 code_knowledge_graph 的路径算法）。"""
    from core.code_knowledge_graph import CodeKnowledgeGraph
    db = CodeKnowledgeGraph(project_path).db_path
    return db if os.path.exists(db) else None


def module_of(node: dict, project_path: str = "") -> str:
    """从节点 file_path 提取模块名（相对项目路径，去 .py）。

    用相对路径而非 basename，避免不同目录下同名文件（如 db/base.py、utils/base.py）
    被合并成同一模块名"base"，导致 fan_in/fan_out 虚高、同名符号重复计数、
    甚至把非循环的跨目录调用误判成循环依赖（社区反馈的误报根因）。
    未传 project_path 或无法归到项目内时，回退到 basename。
    """
    fp = (node.get("file_path") or "").replace("\\", "/")
    base = os.path.basename(fp)
    if fp and project_path:
        try:
            rel = os.path.relpath(fp, project_path).replace("\\", "/")
            if not rel.startswith(".."):
                base = rel
        except Exception:
            pass
    root, _ = os.path.splitext(base)
    return root if root else (node.get("name") or "?")


def layer_of(node: dict) -> int:
    """节点所属目录的分层层级。"""
    fp = (node.get("file_path") or "").replace("\\", "/")
    parent = os.path.basename(os.path.dirname(fp))
    return _LAYER_ORDER.get(parent, _DEFAULT_LAYER)


def build_module_graph(nodes: Dict[str, dict],
                       adj: Dict[str, List[str]],
                       project_path: str = "") -> Dict[str, List[str]]:
    """把符号级 CALLS 边聚合为模块级依赖图（剔除自环）。

    返回 {模块名: [下游模块, ...]（去重、排序）}。
    模块名用相对路径，区分不同目录下的同名文件，避免边被错误合并。
    """
    mod_adj: Dict[str, set] = defaultdict(set)
    for src, targets in adj.items():
        ms = module_of(nodes.get(src, {}), project_path)
        if not ms:
            continue
        for tgt in targets:
            mt = module_of(nodes.get(tgt, {}), project_path)
            if mt and mt != ms:
                mod_adj[ms].add(mt)
    return {m: sorted(t) for m, t in mod_adj.items()}


def find_sccs(adj: Dict[str, List[str]]) -> List[List[str]]:
    """在模块依赖图上求强连通分量（Kosaraju）。返回各分量内模块名列表。"""
    all_nodes = set(adj.keys())
    for tars in adj.values():
        all_nodes.update(tars)
    if not all_nodes:
        return []
    # 正向 DFS 记录完成顺序（迭代栈，避免大项目 RecursionError）
    visited = set()
    order: List[str] = []

    for v in all_nodes:
        if v in visited:
            continue
        stack = [(v, False)]
        while stack:
            node, processed = stack.pop()
            if processed:
                order.append(node)
                continue
            if node in visited:
                continue
            visited.add(node)
            stack.append((node, True))
            for w in adj.get(node, []):
                if w not in visited:
                    stack.append((w, False))
    # 反向图
    radj: Dict[str, List[str]] = defaultdict(list)
    for v, tars in adj.items():
        for w in tars:
            radj[w].append(v)
    # 按完成逆序收集分量（迭代栈）
    visited2 = set()
    comps: List[List[str]] = []

    for v in reversed(order):
        if v in visited2:
            continue
        comp: List[str] = []
        stack = [v]
        visited2.add(v)
        while stack:
            node = stack.pop()
            comp.append(node)
            for w in radj.get(node, []):
                if w not in visited2:
                    visited2.add(w)
                    stack.append(w)
        comps.append(comp)
    return comps


def audit(project_path: str, db_path: str = None,
          fan_out_threshold: int = None,
          large_symbol_threshold: int = None,
          scc_min_size: int = None) -> dict:
    """架构腐化诊断主入口。

    Args:
        project_path: 目标项目路径
        db_path: 知识图谱 db（缺省自动定位）
        fan_out_threshold: 上帝模块扇出阈值（缺省取 settings）
        large_symbol_threshold: 异常模块规模符号阈值（缺省取 settings）
        scc_min_size: 循环依赖 SCC 最小尺寸（缺省取 settings）

    Returns:
        结构化诊断 dict：graph_stats / cycles / god_modules / fan_top /
        layer_violations / large_modules / summary（含 health 0-10）。
    """
    fo_t = fan_out_threshold or ARCH_GOD_FAN_OUT_THRESHOLD
    ls_t = large_symbol_threshold or ARCH_LARGE_MODULE_SYMBOL_THRESHOLD
    sc_min = scc_min_size or ARCH_SCC_CYCLE_MIN_SIZE

    db = db_path or locate_kg_db(project_path)
    result = {"project_path": project_path, "tool": "coderef_arch_audit",
              "graph_stats": {"has_kg": False}, "summary": {}, "ok": False}
    if not db or not os.path.exists(db):
        result["summary"] = "知识图谱不存在，需先构建（coderef_audit / coderef_memory_sync）"
        return result

    nodes, adj = load_graph(db)
    result["graph_stats"] = {
        "has_kg": True, "nodes": len(nodes),
        "calls_edges": sum(len(v) for v in adj.values()),
    }

    mod_adj = build_module_graph(nodes, adj, project_path)
    result["graph_stats"]["modules"] = len(mod_adj)

    # 1) 循环依赖（模块级 SCC）
    cycles = []
    for comp in find_sccs(mod_adj):
        is_cycle = len(comp) >= sc_min
        if len(comp) == 1:
            is_cycle = comp[0] in mod_adj.get(comp[0], [])
        if is_cycle:
            cycles.append(comp)
    result["cycles"] = cycles

    # 2) 扇出 / 扇入
    fan_out = {m: len(mod_adj.get(m, [])) for m in mod_adj}
    fan_in: Dict[str, int] = defaultdict(int)
    for m, tars in mod_adj.items():
        for t in tars:
            fan_in[t] += 1
    all_mods = set(fan_out) | set(fan_in.keys())
    result["god_modules"] = sorted(
        ({"module": m, "fan_out": fan_out.get(m, 0), "fan_in": fan_in.get(m, 0)}
         for m in all_mods if fan_out.get(m, 0) > fo_t),
        key=lambda x: -x["fan_out"])
    result["fan_top"] = sorted(
        ({"module": m, "fan_out": fan_out.get(m, 0), "fan_in": fan_in.get(m, 0)}
         for m in all_mods),
        key=lambda x: -x["fan_out"])[:10]

    # 3) 分层违例（模块所属层取该模块下节点层的最大值）
    mod_layer: Dict[str, int] = {}
    for nid, n in nodes.items():
        m = module_of(n, project_path)
        if m:
            mod_layer[m] = max(mod_layer.get(m, 0), layer_of(n))
    layer_viol = []
    for m, tars in mod_adj.items():
        lm = mod_layer.get(m, _DEFAULT_LAYER)
        for t in tars:
            lt = mod_layer.get(t, _DEFAULT_LAYER)
            if lm < lt:
                layer_viol.append({
                    "from": m, "to": t,
                    "reason": f"{_LAYER_NAME.get(lm, '?')} 依赖 {_LAYER_NAME.get(lt, '?')}（下层依赖上层）",
                })
    result["layer_violations"] = layer_viol

    # 4) 异常模块规模（函数/方法/类符号数）
    mod_symbols: Dict[str, int] = defaultdict(int)
    for nid, n in nodes.items():
        if n.get("type") in ("function", "method", "class"):
            mod_symbols[module_of(n, project_path)] += 1
    result["large_modules"] = sorted(
        ({"module": m, "symbols": c} for m, c in mod_symbols.items()
         if c > ls_t),
        key=lambda x: -x["symbols"])

    # 5) 架构健康度（0-10）
    score = 10.0
    score -= min(6.0, len(cycles) * ARCH_HEALTH_WEIGHT_CYCLE)
    score -= min(2.0, len(result["god_modules"]) * ARCH_HEALTH_WEIGHT_GOD)
    score -= min(2.0, len(layer_viol) * ARCH_HEALTH_WEIGHT_LAYER)
    score -= min(2.0, len(result["large_modules"]) * ARCH_HEALTH_WEIGHT_LARGE)
    health = round(max(0.0, score), 1)
    label = ("优秀" if health >= 8.5 else "良好" if health >= 7.0 else
             "中等" if health >= 5.0 else "堪忧")
    result["summary"] = {
        "health": health, "health_label": label,
        "cycles": len(cycles), "god_modules": len(result["god_modules"]),
        "layer_violations": len(layer_viol),
        "large_modules": len(result["large_modules"]),
    }
    result["ok"] = True
    return result