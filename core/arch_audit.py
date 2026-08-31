# -*- coding: utf-8 -*-
"""
arch_audit — 架构腐化诊断（coderef_arch_audit）

背景：MCP 审计引擎（coderef_scan 的 11 个维度）检测单元是文件/函数/依赖，
看不到跨模块的架构症状：循环依赖、上帝模块、分层违例、异常模块规模。
本模块把"架构诊断层"补上，复用知识图谱 CALLS 边做模块级静态诊断。

定位：静态优先、确定性优先。数据只来自知识图谱 CALLS 边，不依赖 LLM。
它是"非编程人员验证工程结构是否健康"的入口，也是编程 AI 的客观参考。

诊断维度（模块 = 文件，file_path 的 basename 去 .py）：
  cycles        模块 CALLS 图强连通分量（SCC）尺寸 ≥2 → 模块间循环依赖（架构腐化）
  self_loops    单模块 SCC 且模块内存在符号互调/自环 → 模块内自环（透出参考，不扣健康分）
  god_modules   模块扇出超过阈值（依赖过多下游）→ 上帝模块
  layer_viol    低层模块依赖高层模块（如 config 依赖 core）→ 分层违例
  large_modules 单模块符号数超阈值 → 异常模块规模
以上聚合为 0-10 架构健康度。

设计原则：
- 复用 core.graph_closure.load_graph（与 flow_verify / wiki_cross_verify 同源底座）。
- 模块级 CALLS 图：跨模块边聚合、剔除自环，避免符号级噪声。
- 低风险：只读图谱，无副作用。
"""

import ast
import os
from collections import defaultdict, deque
from typing import Dict, List, Optional

from config.settings import (
    ARCH_SCC_CYCLE_MIN_SIZE,
    ARCH_GOD_FAN_OUT_THRESHOLD,
    ARCH_LARGE_MODULE_SYMBOL_THRESHOLD,
    ARCH_HEALTH_WEIGHT_CYCLE,
    ARCH_HEALTH_WEIGHT_GOD,
    ARCH_HEALTH_WEIGHT_LAYER,
    ARCH_HEALTH_WEIGHT_LARGE,
    ARCH_INFRA_DIRS,
)
from core.graph_closure import load_graph


# 目录 → 分层（3=应用层 2=引擎层 1=基础层 0=基础设施层）；未知目录保守视为引擎层
# O-C2：新增最低的"基础设施层"(0)，把跨切面基础设施目录归入该层。公共层(1)
# 依赖基础设施层(0)时，因 0 是最低层、1<0 不成立，不再被误判为"下层依赖上层"。
_LAYER_ORDER = {"demo-app": 3, "app": 3, "frontend": 3,
                "core": 2, "engine": 2,
                "config": 1, "utils": 1, "common": 1, "lib": 1}
for _infra_dir in ARCH_INFRA_DIRS:
    _LAYER_ORDER.setdefault(_infra_dir, 0)
_LAYER_NAME = {3: "应用层", 2: "引擎层", 1: "基础层", 0: "基础设施层"}
_DEFAULT_LAYER = 2

# cycle 大环提示阈值：SCC 节点数超过该值提示"整个子图被圈为强连通分量"（外部反馈）
_CYCLE_HINT_THRESHOLD = 12


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
            # relpath 失败（跨盘符等），保留绝对路径兜底
            pass
    root, _ = os.path.splitext(base)
    return root if root else (node.get("name") or "?")


def layer_of(node: dict) -> int:
    """节点所属目录的分层层级。"""
    fp = (node.get("file_path") or "").replace("\\", "/")
    parent = os.path.basename(os.path.dirname(fp))
    return _LAYER_ORDER.get(parent, _DEFAULT_LAYER)


def _is_test_path(rel_path: str) -> bool:
    """判断模块相对路径是否位于顶层测试目录（test/ 或 tests/）。

    O-C1：测试代码属于"验证面"而非"运行时业务/架构腐化面"，不应参与循环依赖、
    上帝模块、分层违例、异常规模等腐化判定。这里只比较**顶层目录片段**，
    且只匹配 test/tests，绝不依赖文件名含 "test" 字符，避免误杀
    src/utils 这类带子串的目录或正常模块。相对路径为空返回 False 保守放行。
    """
    if not rel_path:
        return False
    top = rel_path.replace("\\", "/").split("/", 1)[0]
    return top in ("test", "tests")


def _parent_package(mod: str) -> str:
    """模块相对路径的直接父包（business/manage/http_tools → business/manage、
    route/gin → route）；无层级（纯顶层模块）时返回自身。

    package_cycles（包内环分类）用「直接父包」而非「顶层第一段」作分拣键：
    Go 等标准布局下全部代码在 internal 之下，顶层第一段恒为 internal，会把
    business/manage/http_tools ↔ common/http_tools 这类跨业务/公共层的环
    误判为"包内环"（目标项目 实测观察点）。
    """
    parts = mod.replace("\\", "/").split("/")
    if len(parts) <= 1:
        return mod
    return "/".join(parts[:-1])


def _has_non_test_module(nodes: Dict[str, dict], project_path: str) -> bool:
    """是否至少存在一个非测试模块（顶层 test/tests 目录之外）。

    Minor(CodeRabbit)：no_code 若只看原始节点数，图谱里只剩 test/tests 节点时仍是
    空转（架构指标已全排除测试），会误给满分 10.0 而非"无代码可评"。这里按"是否存在
    非 test 模块"判定，与 build_module_graph 的测试排除口径对齐。
    """
    for n in nodes.values():
        m = module_of(n, project_path)
        if m and not _is_test_path(m):
            return True
    return False


def build_module_graph(nodes: Dict[str, dict],
                       adj: Dict[str, List[str]],
                       project_path: str = "") -> Dict[str, List[str]]:
    """把符号级 CALLS 边聚合为模块级依赖图（剔除自环）。

    返回 (mod_adj, self_edges)：
      mod_adj    {模块名: [下游模块, ...]（去重、排序）}
      self_edges 存在模块内递归（自环）调用的模块名集合。
    模块名用相对路径，区分不同目录下的同名文件，避免边被错误合并。

    O-C1：位于顶层 test/tests 目录下的测试模块（源或目标）直接不放入
    mod_adj/self_edges，使 downstream 的 cycles/god/layer/large 天然干净。
    """
    mod_adj: Dict[str, set] = defaultdict(set)
    self_edges: set = set()
    # Major(CodeRabbit)：模块内"符号级"调用也可能是线性（a()→b()→c()）而非环，
    # 不能把模块内任意调用都当自环。单独按符号建模块内子图，仅在模块内存在符号
    # 直接自调用、或符号图形成环（SCC≥2）时，才标记为"模块内自环"。
    intra_self: set = set()            # 模块名集合：存在符号直接自调用
    intra_adj: Dict[str, Dict[str, set]] = defaultdict(lambda: defaultdict(set))
    for src, targets in adj.items():
        ms = module_of(nodes.get(src, {}), project_path)
        if not ms or _is_test_path(ms):
            continue
        for tgt in targets:
            mt = module_of(nodes.get(tgt, {}), project_path)
            if not mt or _is_test_path(mt):
                continue
            if mt != ms:
                mod_adj[ms].add(mt)
            elif tgt == src:
                intra_self.add(ms)              # 符号直接调用自身 → 自环
            else:
                intra_adj[ms][src].add(tgt)     # 模块内符号级调用（剔除自调用边）
    for m, s_adj in intra_adj.items():
        if _has_symbol_cycle(s_adj):
            self_edges.add(m)
    self_edges |= intra_self
    return {m: sorted(t) for m, t in mod_adj.items()}, self_edges


def _has_symbol_cycle(s_adj: Dict[str, set]) -> bool:
    """模块内符号级有向图是否存在环（已剔自调用边）。Kahn 拓扑排序，剩余节点>0 即有环。"""
    indeg = {v: 0 for v in s_adj}
    for targets in s_adj.values():
        for t in targets:
            indeg[t] = indeg.get(t, 0) + 1
    queue = [v for v, d in indeg.items() if d == 0]
    removed = 0
    while queue:
        v = queue.pop()
        removed += 1
        for t in s_adj.get(v, ()):
            indeg[t] -= 1
            if indeg[t] == 0:
                queue.append(t)
    return removed < len(indeg)


def _dfs_finish_order(adj: Dict[str, List[str]], all_nodes: set) -> List[str]:
    """正向迭代 DFS，返回节点完成顺序（后序）。栈模拟避免大项目递归溢出。"""
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
    return order


def _reverse_graph(adj: Dict[str, List[str]]) -> Dict[str, List[str]]:
    radj: Dict[str, List[str]] = defaultdict(list)
    for v, tars in adj.items():
        for w in tars:
            radj[w].append(v)
    return radj


def _collect_components(radj: Dict[str, List[str]], order: List[str]) -> List[List[str]]:
    """按完成逆序在反向图上迭代 DFS，每个连通栈即一个强连通分量。"""
    visited = set()
    comps: List[List[str]] = []
    for v in reversed(order):
        if v in visited:
            continue
        comp: List[str] = []
        stack = [v]
        visited.add(v)
        while stack:
            node = stack.pop()
            comp.append(node)
            for w in radj.get(node, []):
                if w not in visited:
                    visited.add(w)
                    stack.append(w)
        comps.append(comp)
    return comps


def find_sccs(adj: Dict[str, List[str]]) -> List[List[str]]:
    """在模块依赖图上求强连通分量（Kosaraju）。返回各分量内模块名列表。"""
    all_nodes = set(adj.keys())
    for tars in adj.values():
        all_nodes.update(tars)
    if not all_nodes:
        return []
    order = _dfs_finish_order(adj, all_nodes)
    return _collect_components(_reverse_graph(adj), order)


def _module_layer(module_name: str) -> int:
    """从模块相对路径首段推断分层（与 layer_of 同口径，供环边逆向标注）。"""
    parts = (module_name or "").replace("\\", "/").split("/")
    parent = parts[0] if parts else ""
    return _LAYER_ORDER.get(parent, _DEFAULT_LAYER)


def _min_cycle_path(graph: Dict[str, List[str]], comp: List[str]) -> Optional[List[str]]:
    """在强连通分量内找最短闭环（BFS，逐起点求回到自身的最短路径）。

    外部反馈：cycle 只回超长模块列表，无法判断真伪。返回一条最小真环
    （模块名序列，首尾相同），供使用者一眼定位环的构成。
    """
    comp_set = set(comp)
    best = None
    for start in comp:
        queue = deque([(start, [start])])
        seen = {start}
        while queue:
            node, path = queue.popleft()
            for nxt in graph.get(node, []):
                if nxt not in comp_set:
                    continue
                if nxt == start:
                    if best is None or len(path) + 1 < len(best):
                        best = path + [start]
                    continue
                if nxt not in seen:
                    seen.add(nxt)
                    queue.append((nxt, path + [nxt]))
    return best


def _key_edges(min_cycle: List[str]) -> List[Dict]:
    """环路径上的边清单（起点/终点）；层可区分且方向异常（低→高）标注逆向。

    与 _layer_violations 同口径：下层依赖上层（lm < lt）才是分层违例/逆向边，
    上层依赖下层是正常依赖方向，不标注。
    """
    edges = []
    for i in range(len(min_cycle) - 1):
        a, b = min_cycle[i], min_cycle[i + 1]
        la, lb = _module_layer(a), _module_layer(b)
        edges.append({"source": a, "target": b, "reverse": la < lb})
    return edges


def _find_cycles(mod_adj: Dict[str, List[str]], self_edges: set, sc_min: int):
    """模块级 SCC 中区分「模块间真循环」与「模块内自环」。

    返回 (module_cycles, self_loops, cycle_details)：
      module_cycles  多模块 SCC（尺寸 ≥ sc_min）→ 模块间循环依赖，属架构腐化，参与健康分扣分
      self_loops     单模块分量且模块内存在符号互调/自环 → 模块内自环，
                     是大型单体正常协作形态（如 core/role_boundary 内部函数互调），
                     不当作循环依赖、不扣健康分（ 复核：原口径把模块内互调
                     全计入 cycles，致大型单体 health 被压到 0.0 过度悲观）
      cycle_details  每个环的最小真环路径 + 关键边 + 大环提示（外部反馈：环报得太粗
                     无法判断真伪，需展示最小环与具体边）
    """
    module_cycles: List[List[str]] = []
    self_loops: List[str] = []
    cycle_details: List[Dict] = []
    # 自环模块即使无跨模块边也不在 mod_adj，需并入 SCC 节点集才能被识别
    scc_adj = dict(mod_adj)
    for module in self_edges:
        scc_adj.setdefault(module, [])
    for comp in find_sccs(scc_adj):
        if len(comp) >= 2 and len(comp) >= sc_min:
            module_cycles.append(comp)
            min_cycle = _min_cycle_path(mod_adj, comp)
            cycle_details.append({
                "modules": comp,
                "min_cycle": min_cycle,
                "key_edges": _key_edges(min_cycle) if min_cycle else [],
                "size": len(comp),
                "hint_large_scc": len(comp) > _CYCLE_HINT_THRESHOLD,
            })
        elif len(comp) == 1 and comp[0] in self_edges:
            self_loops.append(comp[0])
    return module_cycles, self_loops, cycle_details


def _module_symbol_counts(nodes: dict, project_path: str) -> Dict[str, int]:
    """各模块的函数/方法/类符号数（上帝模块判定与异常规模共用的规模度量）。"""
    mod_symbols: Dict[str, int] = defaultdict(int)
    for nid, n in nodes.items():
        if n.get("type") in ("function", "method", "class"):
            ms = module_of(n, project_path)
            # O-C1：测试模块不计入规模，避免测试文件占据 large_modules 头部
            if ms and _is_test_path(ms):
                continue
            mod_symbols[ms] += 1
    return dict(mod_symbols)


def _fan_stats(mod_adj: Dict[str, List[str]], fo_t: int,
               mod_symbols: Dict[str, int] = None,
               fi_t: int = 2, ratio_t: float = 0.25) -> tuple:
    """计算各模块扇出/扇入，返回 (god_modules, fan_top10)。

    上帝模块判定（保守，结合模块规模与扇入/扇出综合判断）：
    1. 高扇出：fan_out > fo_t（依赖过多下游，原有标准）；
    2. 高扇入 + 相对规模大：fan_in >= fi_t 且 符号数占比 >= ratio_t
       （被多个模块依赖，且相对项目规模功能庞杂 → 上帝模块）。
    符号数占比以项目内全部函数/方法/类符号数为基准，避免小项目
    绝对符号数阈值失真、大项目正常工具模块被误判。
    """
    fan_out = {m: len(mod_adj.get(m, [])) for m in mod_adj}
    fan_in: Dict[str, int] = defaultdict(int)
    for m, tars in mod_adj.items():
        for t in tars:
            fan_in[t] += 1
    all_mods = set(fan_out) | set(fan_in.keys())
    total = sum((mod_symbols or {}).values()) or 1
    row = lambda m: {"module": m, "fan_out": fan_out.get(m, 0), "fan_in": fan_in.get(m, 0)}
    god = []
    for m in all_mods:
        if fan_out.get(m, 0) > fo_t:
            god.append(row(m))
            continue
        if mod_symbols and fan_in.get(m, 0) >= fi_t:
            ratio = mod_symbols.get(m, 0) / total
            if ratio >= ratio_t:
                god.append(row(m))
    god.sort(key=lambda x: -x["fan_out"])
    top = sorted((row(m) for m in all_mods), key=lambda x: -x["fan_out"])[:10]
    return god, top


def _layer_violations(nodes: dict, mod_adj: Dict[str, List[str]], project_path: str) -> list:
    """下层模块依赖上层模块的违例清单（模块层取其下节点层的最大值）。"""
    mod_layer: Dict[str, int] = {}
    for nid, n in nodes.items():
        m = module_of(n, project_path)
        if not m or _is_test_path(m):
            # O-C1：测试模块不参与分层判定
            continue
        mod_layer[m] = max(mod_layer.get(m, 0), layer_of(n))
    viol = []
    for m, tars in mod_adj.items():
        if _is_test_path(m):
            continue
        lm = mod_layer.get(m, _DEFAULT_LAYER)
        for t in tars:
            if _is_test_path(t):
                continue
            lt = mod_layer.get(t, _DEFAULT_LAYER)
            # O-C2：基础设施层(0)是跨切面层，进出两侧都不作分层违例（公共库
            # 依赖日志/国际化等基础设施是平级装配，反之基础设施引用业务也属
            # 切面横切，均非"下层依赖上层"的腐化信号）。
            if lm == 0 or lt == 0:
                continue
            if lm < lt:
                viol.append({
                    "from": m, "to": t,
                    "reason": f"{_LAYER_NAME.get(lm, '?')} 依赖 {_LAYER_NAME.get(lt, '?')}（下层依赖上层）",
                })
    return viol


def _large_modules(nodes: dict, project_path: str, ls_t: int) -> list:
    """符号数超过阈值的异常规模模块。"""
    mod_symbols = _module_symbol_counts(nodes, project_path)
    return sorted(
        ({"module": m, "symbols": c} for m, c in mod_symbols.items() if c > ls_t),
        key=lambda x: -x["symbols"])


def _health_summary(cycles: list, god: list, layer_viol: list, large: list,
                    no_code: bool = False) -> dict:
    """架构健康度（0-10）：循环/上帝模块/分层违例/异常规模扣分。

    O-D2：当项目无任何代码模块（知识图谱 nodes=0，纯文档+少量脚本项目
    常见）时健康度"不适用"。此时 health 置 None、health_label 置"无代码"，
    并附加 status 提示"无代码可评"，避免"空项目得分满分 10.0"掩盖
    "项目为空 / 尚未完成代码建图"的真实信息。该分支不影响 nodes>0 的
    正常评分逻辑（no_code=False 时行为与原实现完全一致）。
    """
    if no_code:
        return {
            "health": None, "health_label": "无代码",
            "cycles": len(cycles), "god_modules": len(god),
            "layer_violations": len(layer_viol), "large_modules": len(large),
            # Minor(CodeRabbit 复审)：no_code 也可能源于图谱只剩 test/tests 模块（nodes>0），
            # 不能声称 nodes=0。统一表述为"无非测试代码模块（测试模块除外）"。
            "status": "无代码可评（无非测试代码模块；健康度不适用）",
        }
    score = 10.0
    score -= min(6.0, len(cycles) * ARCH_HEALTH_WEIGHT_CYCLE)
    score -= min(2.0, len(god) * ARCH_HEALTH_WEIGHT_GOD)
    score -= min(2.0, len(layer_viol) * ARCH_HEALTH_WEIGHT_LAYER)
    score -= min(2.0, len(large) * ARCH_HEALTH_WEIGHT_LARGE)
    health = round(max(0.0, score), 1)
    label = ("优秀" if health >= 8.5 else "良好" if health >= 7.0 else
             "中等" if health >= 5.0 else "堪忧")
    return {
        "health": health, "health_label": label,
        "cycles": len(cycles), "god_modules": len(god),
        "layer_violations": len(layer_viol), "large_modules": len(large),
    }


# ═══════════════════════════════════════════════════════════════════
# 函数级递归检测（ARC-08）：AST 扫描源码，构建函数调用图，检测环
# ═══════════════════════════════════════════════════════════════════
# 背景：模块级 CALLS 图只能报告"模块间"循环依赖，无法回答"函数 A 是否
# 直接/间接调用自身"（直接递归 A→A、间接递归 A→B→A）。无限递归是运行时
# 崩溃级缺陷，需在函数粒度单独检测。本检测直接扫描项目源码（AST），
# 复用模块级 SCC 算法检测函数调用图上的环，输出 finding。

# 扫描时跳过的噪声目录（与 ast_signals 保持一致）
_FUNC_SKIP_DIRS = {".git", ".venv", "venv", "__pycache__", "node_modules",
                   ".tox", ".idea", "dist", "build", ".mypy_cache", ".pytest_cache"}
# 函数级递归扫描的文件数上限（与 AstProjectParser 一致，防超大项目卡死）
_FUNC_MAX_FILES = 5000


def _iter_py_files(project_path: str):
    """遍历项目内所有 .py 文件（跳过噪声目录）。"""
    for root, dirs, files in os.walk(project_path):
        dirs[:] = [d for d in dirs if d not in _FUNC_SKIP_DIRS]
        for fn in files:
            if fn.endswith(".py"):
                yield os.path.join(root, fn)


def _parse_py(path: str):
    """读取并解析 Python 源码，失败返回 None（utf-8/gbk 双编码回退）。"""
    try:
        with open(path, "r", encoding="utf-8") as f:
            src = f.read()
    except (UnicodeDecodeError, OSError):
        try:
            with open(path, "r", encoding="gbk") as f:
                src = f.read()
        except Exception:
            return None
    try:
        return ast.parse(src)
    except SyntaxError:
        return None


def _rel_path(project_path: str, path: str) -> str:
    """项目内相对路径（正斜杠）。"""
    try:
        return os.path.relpath(path, project_path).replace("\\", "/")
    except Exception:
        return path.replace("\\", "/")


def _collect_imports(tree) -> Dict[str, tuple]:
    """收集模块级导入映射 {别名: (模块路径, 符号名)}。

    - import a / import a as b → {a: ("a", None), b: ("a", None)}
    - from a import a1 / from a import a1 as x → {a1: ("a", "a1"), x: ("a", "a1")}
    符号名为 None 表示"别名指向模块"；非 None 表示"别名指向模块内符号"。
    """
    imports: Dict[str, tuple] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                asname = alias.asname or alias.name.split(".")[0]
                imports[asname] = (alias.name, None)
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            for alias in node.names:
                if alias.name == "*":
                    continue
                asname = alias.asname or alias.name
                imports[asname] = (module, alias.name)
    return imports


def _module_to_file(project_path: str, module_path: str):
    """把模块路径（如 core.helper）映射到文件路径（如 core/helper.py）。"""
    rel = module_path.replace(".", "/") + ".py"
    candidate = os.path.join(project_path, rel)
    if os.path.isfile(candidate):
        return candidate
    pkg = os.path.join(project_path, module_path.replace(".", "/"), "__init__.py")
    if os.path.isfile(pkg):
        return pkg
    return None


def _resolve_call_target(func_node, rel: str, imports: Dict[str, tuple],
                         funcs: Dict[tuple, dict], project_path: str) -> list:
    """解析调用目标，返回 [(rel, name)] 列表（仅项目内已定义模块级函数）。

    只解析三类确定性的调用：
    - 直接调用 ast.Name（如 helper_aux()）：同文件内查找同名函数；
    - from-import 符号直接调用（from a import a1 后 a1()）：解析到 a.py 的 a1；
    - 模块属性调用 ast.Attribute（如 helper.helper_aux()）：经导入映射
      解析模块别名 → 目标文件 → 查找属性名函数。
    忽略方法调用（data.decode() / self.method()）与标准库/第三方调用，
    避免把字符串方法等误判为函数递归。
    """
    if isinstance(func_node, ast.Name):
        name = func_node.id
        if (rel, name) in funcs:
            return [(rel, name)]
        # from a import a1 → a1() 调用 a.py 的 a1
        entry = imports.get(name)
        if entry:
            module_path, symbol = entry
            if symbol and symbol == name:
                target_file = _module_to_file(project_path, module_path)
                if target_file:
                    target_rel = _rel_path(project_path, target_file)
                    if (target_rel, symbol) in funcs:
                        return [(target_rel, symbol)]
    elif isinstance(func_node, ast.Attribute):
        if isinstance(func_node.value, ast.Name):
            mod_alias = func_node.value.id
            attr = func_node.attr
            entry = imports.get(mod_alias)
            if entry:
                module_path, symbol = entry
                # 别名可能指向模块（import a）或模块内符号（from a import b，
                # b 可能是子模块也可能是符号）——按候选模块路径逐一尝试
                candidates = []
                if symbol is None:
                    candidates.append(module_path)
                else:
                    candidates.append(f"{module_path}.{symbol}")
                    candidates.append(module_path)
                for mp in candidates:
                    target_file = _module_to_file(project_path, mp)
                    if target_file:
                        target_rel = _rel_path(project_path, target_file)
                        if (target_rel, attr) in funcs:
                            return [(target_rel, attr)]
    return []


def _extract_cycle(graph: Dict[str, List[str]], comp: List[str]):
    """从强连通分量中提取一条环路径（首尾相同），失败返回 None。"""
    if len(comp) == 1:
        n = comp[0]
        if n in graph.get(n, []):
            return [n, n]
        return None
    start = comp[0]
    stack = [(start, [start])]
    while stack:
        node, path = stack.pop()
        for nxt in graph.get(node, []):
            if nxt not in comp:
                continue
            if nxt == start:
                return path + [start]
            if nxt not in path:
                stack.append((nxt, path + [nxt]))
    return None


def _scan_function_recursion(project_path: str) -> list:
    """扫描项目源码，检测函数级递归调用（直接/间接），返回 finding 列表。

    用 AST 解析每个模块级函数的调用（直接调用 + 模块属性调用），
    构建函数调用图，检测环（直接递归 A→A、间接递归 A→B→A）。
    输出遵循 finding 结构：severity / category / file / line / message / chain。
    """
    # 1) 收集模块级函数定义 + 导入映射
    funcs: Dict[tuple, dict] = {}            # (rel, name) -> {"file": rel, "line": lineno}
    imports: Dict[str, Dict[str, str]] = {}  # rel -> {alias: module_path}
    count = 0
    for path in _iter_py_files(project_path):
        if count >= _FUNC_MAX_FILES:
            break
        count += 1
        tree = _parse_py(path)
        if tree is None:
            continue
        rel = _rel_path(project_path, path)
        imports[rel] = _collect_imports(tree)
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                funcs[(rel, node.name)] = {"file": rel, "line": node.lineno}

    # 2) 构建函数调用图
    graph: Dict[tuple, set] = defaultdict(set)
    count = 0
    for path in _iter_py_files(project_path):
        if count >= _FUNC_MAX_FILES:
            break
        count += 1
        tree = _parse_py(path)
        if tree is None:
            continue
        rel = _rel_path(project_path, path)
        for node in tree.body:
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            caller = (rel, node.name)
            for call_node in ast.walk(node):
                if isinstance(call_node, ast.Call):
                    for tgt in _resolve_call_target(
                            call_node.func, rel, imports.get(rel, {}),
                            funcs, project_path):
                        graph[caller].add(tgt)

    # 3) 检测环（复用模块级 SCC 算法）
    str_graph = {f"{r}:{n}": sorted(f"{tr}:{tn}" for tr, tn in tgts)
                 for (r, n), tgts in graph.items()}
    findings = []
    for comp in find_sccs(str_graph):
        is_cycle = len(comp) >= 2
        if len(comp) == 1:
            n = comp[0]
            is_cycle = n in str_graph.get(n, [])
        if not is_cycle:
            continue
        chain = _extract_cycle(str_graph, comp)
        if not chain:
            continue
        rel, _, name = chain[0].rpartition(":")
        info = funcs.get((rel, name), {})
        # 直接递归（A→A）可能是正常的树遍历/解析递归，需人工确认终止条件；
        # 间接递归（A→B→A）通常是逻辑错误，判 high。
        direct = len(chain) == 2 and chain[0] == chain[1]
        if direct:
            severity, message = "medium", (
                f"函数直接递归调用自身：{rel}:{name}（需人工确认是否有终止条件，避免无限递归）")
        else:
            severity, message = "high", (
                "函数级递归调用链：" + " → ".join(chain) + "（可能无限递归）")
        findings.append({
            "severity": severity,
            "category": "recursion",
            "file": rel,
            "line": info.get("line", 0),
            "message": message,
            "chain": chain,
        })
    return findings


def _identity_briefing(project_path: str, db_path: str) -> list:
    """真身/孤本摘要：复用 arch_insight P0-B identity_insight，
    让 arch_audit 直接透出"同名多目录实现里谁是活跃真身/谁是孤本"。

    返回紧凑摘要列表：[{class_name, copies: 总数, active: 活跃真身副本数,
    roots: 无调用者副本数, verdicts: 各副本 verdict, 优先展示来源文件}]。
    纯静态、不依赖 LLM；缺图谱时返回空列表（不阻断健康度）。
    """
    try:
        from core.arch_insight import identity_insight
        ins = identity_insight(project_path, db_path=db_path, max_items=30)
    except Exception:
        return []
    if not ins.get("ok"):
        return []

    briefing = []
    for item in ins.get("items") or []:
        copies = item.get("copies") or []
        if not copies:
            continue
        active = [c for c in copies if c.get("verdict") == "活跃真身"]
        roots = [c for c in copies if c.get("callers", 0) == 0]
        # 展示优先：活跃真身最前 -> 生产入口候选 -> 孤本，附来源文件简化形式
        prio = (active or roots or copies)[:1]
        src = prio[0].get("file", "") if prio else ""
        briefing.append({
            "class_name": item.get("name", ""),
            "copies": len(copies),
            "active": len(active),
            "roots": len(roots),
            "verdicts": [c.get("verdict", "?") for c in copies][:6],
            "lead_src": src,
        })
    return briefing


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
    fo_t = fan_out_threshold if fan_out_threshold is not None else ARCH_GOD_FAN_OUT_THRESHOLD
    ls_t = large_symbol_threshold if large_symbol_threshold is not None else ARCH_LARGE_MODULE_SYMBOL_THRESHOLD
    sc_min = scc_min_size if scc_min_size is not None else ARCH_SCC_CYCLE_MIN_SIZE

    db = db_path or locate_kg_db(project_path)
    result = {"project_path": project_path, "tool": "coderef_arch_audit",
              "graph_stats": {"has_kg": False}, "summary": {}, "ok": False}
    if not db or not os.path.exists(db):
        result["summary"] = "知识图谱不存在，需先构建（coderef_audit / coderef_memory(action=sync)）"
        return result

    nodes, adj = load_graph(db)
    result["graph_stats"] = {
        "has_kg": True, "nodes": len(nodes),
        "calls_edges": sum(len(v) for v in adj.values()),
    }

    mod_adj, self_edges = build_module_graph(nodes, adj, project_path)
    result["graph_stats"]["modules"] = len(mod_adj)

    # 1) 循环依赖（模块级 SCC：模块间真循环 + 模块内自环分流，自环不扣健康分）
    module_cycles, self_loops, cycle_details = _find_cycles(mod_adj, self_edges, sc_min)
    # O-C3 + Major(CodeRabbit 复审)：同直接父包的子包互引（如 route/gin↔route/client_side）
    #  在业务上是同一模块/层内部的组件纠缠，单独透出为 package_cycles 作补充观察；
    #  但它们仍是文件级的真实循环依赖，不能因同包就完全不扣健康分。因此把全部
    #  module_cycles（含包内环）计入 cycles 参与扣分，package_cycles 仅作分类透出。
    package_cycles = [comp for comp in module_cycles
                      if len({_parent_package(m) for m in comp}) == 1]
    result["cycles"] = module_cycles
    result["cycle_details"] = cycle_details
    result["package_cycles"] = package_cycles
    result["self_loops"] = self_loops

    # 2) 扇出 / 扇入 + 上帝模块（结合模块规模综合判定）
    mod_symbols = _module_symbol_counts(nodes, project_path)
    result["god_modules"], result["fan_top"] = _fan_stats(mod_adj, fo_t, mod_symbols)

    # 3) 分层违例
    layer_viol = _layer_violations(nodes, mod_adj, project_path)
    result["layer_violations"] = layer_viol

    # 4) 异常模块规模（函数/方法/类符号数）
    result["large_modules"] = _large_modules(nodes, project_path, ls_t)

    # 5) 函数级递归（ARC-08）：AST 扫描源码，检测函数级递归调用（直接/间接）
    result["function_recursions"] = _scan_function_recursion(project_path)

    # 5.5) 真身/孤本摘要（P0-B）：复用 arch_insight P0-B identity_insight，
    #      让 arch_audit 直接透出"同名多目录实现里谁是活跃真身/谁是孤本"，
    #      不再埋在 architecture 报告内（Skill 只看 arch_audit 健康度也不会漏真身判定）。
    result["identity"] = _identity_briefing(project_path, db)

    # 6) 架构健康度（0-10）
    # O-D2：图谱无任何代码节点（nodes=0）时健康度"不适用"，summary 标注"无代码可评"，
    #       health=null 而非满分 10.0，避免空项目得高分掩盖"项目为空/未建图"的真实信息。
    result["summary"] = _health_summary(
        result["cycles"], result["god_modules"], layer_viol, result["large_modules"],
        no_code=not _has_non_test_module(nodes, project_path))
    result["summary"]["function_recursions"] = len(result["function_recursions"])
    result["summary"]["self_loops"] = len(self_loops)
    result["identity_count"] = len(result["identity"])
    result["ok"] = True
    return result