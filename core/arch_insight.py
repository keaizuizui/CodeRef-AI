# -*- coding: utf-8 -*-
"""
架构洞察（，v5.3.0）：管线梳理 / 真身判定 / 重复识别 —— 人话结构化结论

背景（测试 ，P0 级）：coderef_architecture 报告此前只是"790B 壳"（项目/文件/行数
+ 一行 HTML 画布路径），给不出业务管线流向、子系统真身/入口、重复实现簇这类可读结论，
测试被迫人工 grep 完成梳理。

本模块以纯静态、确定性方式（复用知识图谱 CALLS 边 + FlowVerifier）自动产出三段人话结论：
- P0-A 管线梳理：自动发现入口，沿 CALLS 归纳阶段序管线（x→y→z 带文件/行号/说明），
  输出 Markdown 表格；另附跨模块业务数据流。
- P0-B 真身/入口判定：同名多目录实现（如 check_plan_coverage 同时存在于多个子系统），
  报告各副本被谁引用 / 是否活跃 / 哪个是生产入口候选。
- P0-C 重复/同构识别：同名函数跨模块实现，标记重复实现簇。

LLM 可选：use_llm=True 且配置了 API Key 时，对三段静态结果生成一段"人话总结"；
未配置时静态结果完整可用，不降级。

用法：
    from core.arch_insight import insight_markdown
    md = insight_markdown(project_path="d:/x/proj", use_llm=False)
"""

import os
import re
from collections import defaultdict
from typing import Dict, List, Optional

from loguru import logger

from core.graph_closure import filter_excluded


# ═══════════════════════════════════════════════════════════════════
# 公共：图谱就绪 + FlowVerifier
# ═══════════════════════════════════════════════════════════════════

def _verifier(project_path: str, db_path: Optional[str] = None):
    """确保图谱就绪并返回 FlowVerifier；图谱不可用返回 None（调用方诚实降级）。"""
    from core.flow_verify import FlowVerifier, ensure_kg
    db = ensure_kg(project_path, db_path)
    if not os.path.isfile(db):
        logger.warning(f"[arch_insight] 知识图谱不存在: {db}，洞察跳过")
        return None
    try:
        return FlowVerifier(db)
    except Exception as e:  # pragma: no cover
        logger.warning(f"[arch_insight] 加载图谱失败: {e}")
        return None


def _mod_of(fp: str) -> str:
    """文件所在目录名（模块归属），兼容相对/绝对路径。"""
    d = os.path.dirname(os.path.normpath(fp or ""))
    return os.path.basename(d) or ""


def _abs_path(project_path: str, fp: str) -> str:
    """图谱节点 file_path 可能是相对路径，拼回绝对路径读取源码。"""
    if not fp:
        return ""
    if os.path.isabs(fp):
        return fp
    return os.path.join(project_path, fp)


def _norm_body(fp: str, start: int, end: int) -> str:
    """读取并规范化函数体源码（去注释/字符串/空白），供相似度比较。"""
    try:
        with open(fp, encoding="utf-8", errors="ignore") as f:
            lines = f.readlines()
        body = "".join(lines[start - 1:end])
    except Exception:
        return ""
    body = re.sub(r"#.*", "", body)
    body = re.sub(r'"[^"]*"|\'[^\']*\'', "", body)
    body = re.sub(r"\s+", "", body)
    return body


def _jaccard(a: str, b: str) -> float:
    """字符 bigram 集合 Jaccard 相似度（0~1）。"""
    if not a or not b:
        return 0.0
    sa = {a[i:i + 2] for i in range(len(a) - 1)}
    sb = {b[i:i + 2] for i in range(len(b) - 1)}
    if not sa and not sb:
        return 1.0
    union = sa | sb
    return len(sa & sb) / len(union) if union else 0.0


def _contract_compatible(a: Dict, b: Dict) -> bool:
    """契约兼容：参数列表 + 返回类型（非空时）差异显著视为契约不同。

    外部反馈：同名方法（如 DeepSeekClient.chat 调 API vs DiscussionEngine.chat
    管理会话）参数/返回契约完全不同，仅凭方法名+结构会被误报真重复。
    契约不兼容的副本不聚入同一重复簇（降权为"仅同名、契约不同"候选）。
    图谱无 params/return_type（旧图谱/非 Python）时不阻断。
    """
    pa = [p for p in (a.get("params") or []) if p != "self"]
    pb = [p for p in (b.get("params") or []) if p != "self"]
    if len(pa) != len(pb):
        return False
    if pa and pb:
        # 保序比较：逐位置一致率（set 会丢失参数顺序，如 (a,b) vs (b,a) 被误判一致）
        pos_hit = sum(1 for x, y in zip(pa, pb) if x == y)
        if pos_hit / len(pa) < 0.6:
            return False
    ra = (a.get("return_type") or "").strip()
    rb = (b.get("return_type") or "").strip()
    if ra and rb and ra != rb:
        return False
    return True


def _partition_copies(copies: List[Dict], sim_threshold: float):
    """把副本按函数体相似度 ≥ 阈值贪心聚类。

    返回 (dup_clusters, singles)：
    - dup_clusters: [[copy, ...], ...]，每个簇 ≥2 副本且簇内成员与簇中某成员相似度 ≥ 阈值
    - singles: [copy, ...]，未配对（与任何已聚簇副本相似度 < 阈值）的副本
    """
    clusters: List[List[Dict]] = []
    for c in copies:
        best_idx, best_sim = -1, 0.0
        for i, cl in enumerate(clusters):
            # 候选须与簇内所有成员契约兼容，防止经单个兼容成员混入契约不同的副本
            if not all(_contract_compatible(c, m) for m in cl):
                continue
            for m in cl:
                s = _jaccard(c["body"], m["body"])
                if s > best_sim:
                    best_sim, best_idx = s, i
        if best_idx >= 0 and best_sim >= sim_threshold:
            clusters[best_idx].append(c)
        else:
            clusters.append([c])
    dup_clusters = [cl for cl in clusters if len(cl) >= 2]
    singles = [cl[0] for cl in clusters if len(cl) == 1]
    return dup_clusters, singles


# ═══════════════════════════════════════════════════════════════════
# ：业务级判定辅助（通用名过滤 / 测试文件 / 集合相似度 / 相对目录）
# ═══════════════════════════════════════════════════════════════════

# Python 通用方法名（构造/序列化/执行/渲染/CRUD 等噪音），P0-B/P0-C 聚合时过滤，
# 聚焦业务级类名/函数名（如 FusionResearchBot / BrandMasterEngine / CatchphraseEngine）。
_GENERIC_NAMES = {
    # dunder
    "__init__", "__new__", "__repr__", "__str__", "__eq__", "__ne__",
    "__hash__", "__lt__", "__le__", "__gt__", "__ge__", "__bool__",
    "__len__", "__getitem__", "__setitem__", "__delitem__", "__contains__",
    "__iter__", "__next__", "__call__", "__enter__", "__exit__",
    "__getattr__", "__setattr__", "__delattr__", "__getattribute__",
    "__add__", "__sub__", "__mul__", "__truediv__", "__floordiv__",
    "__mod__", "__pow__", "__and__", "__or__", "__xor__", "__lshift__",
    "__rshift__", "__iadd__", "__isub__", "__imul__", "__itruediv__",
    "__neg__", "__pos__", "__abs__", "__invert__", "__int__", "__float__",
    "__bytes__", "__format__", "__sizeof__", "__reduce__", "__reduce_ex__",
    "__copy__", "__deepcopy__", "__init_subclass__", "__class_getitem__",
    "__del__", "__dir__", "__slots__", "__subclasshook__",
    "__instancecheck__", "__subclasscheck__",
    # 通用方法名（测试  清单 + 常见）
    "to_dict", "from_dict", "to_json", "from_json", "to_markdown",
    "from_markdown", "to_string", "from_string", "to_list", "from_list",
    "execute", "render", "search", "get", "set", "save", "clear", "load",
    "main", "run", "start", "stop", "close", "open", "read", "write",
    "setup", "teardown", "set_up", "tear_down", "get_stats", "available",
    "generate", "process", "parse", "format", "validate", "check",
    "update", "delete", "add", "remove", "create", "init", "reset",
    "print", "log", "info", "copy", "clone", "equals", "hash_code",
    "name", "value", "items", "keys", "values", "get_item", "set_item",
    "handle", "dispatch", "register", "unregister", "notify", "subscribe",
    "on_click", "on_change", "on_event", "callback", "handler",
    "apply", "call", "invoke", "emit", "trigger", "fire",
    "configure", "config", "default", "defaults", "initialize", "finalize",
    "is_valid", "is_empty", "is_none", "has_key", "contains",
    "append", "extend", "insert", "pop", "index", "count", "reverse",
    "sort", "upper", "lower", "strip", "replace", "split", "join",
    "find", "rfind", "startswith", "endswith", "encode", "decode",
    "test", "tests", "assert", "assert_equal", "entry", "main_entry",
    # unittest 标准方法（setUp/tearDown/assert* 等，测试类噪音）
    "setUp", "tearDown", "setUpClass", "tearDownClass", "setUpModule",
    "tearDownModule", "runTest", "assertEqual", "assertNotEqual",
    "assertTrue", "assertFalse", "assertIs", "assertIsNot", "assertIsNone",
    "assertIsNotNone", "assertIn", "assertNotIn", "assertIsInstance",
    "assertNotIsInstance", "assertRaises", "assertRaisesRegex",
    "assertAlmostEqual", "assertNotAlmostEqual", "assertGreater",
    "assertGreaterEqual", "assertLess", "assertLessEqual", "assertRegex",
    "assertNotRegex", "assertCountEqual", "assertMultiLineEqual",
    "assertSequenceEqual", "assertListEqual", "assertTupleEqual",
    "assertSetEqual", "assertDictEqual", "assertLogs", "assertWarns",
    "assertWarnsRegex", "assertNoLogs", "addCleanup", "addClassCleanup",
    "addModuleCleanup", "doCleanups", "doClassCleanups", "doModuleCleanups",
    "skipTest", "subTest", "debug", "shortDescription", "id",
    "assert_", "fail", "failIf", "failUnless", "failUnlessEqual",
    "failIfEqual", "failUnlessRaises", "failUnlessAlmostEqual",
    "failIfAlmostEqual", "failUnlessIs", "failIfIs", "failUnlessIsNone",
    "failIfIsNone", "failUnlessIn", "failIfIn", "failUnlessIsInstance",
    "failIfIsInstance", "failUnlessNotEqual", "failIfNotEqual",
    # 下划线前缀通用（_parse_json/_format_markdown 等）
    "_parse_json", "_format_markdown", "_load", "_save", "_read", "_write",
    "_get", "_set", "_init", "_reset", "_process", "_handle", "_run",
    "_main", "_start", "_stop", "_close", "_open", "_check", "_validate",
    "_update", "_delete", "_create", "_build", "_make", "_prepare",
    "_extract", "_merge", "_split", "_join", "_filter", "_sort",
    "_convert", "_transform", "_normalize", "_clean", "_trim",
    "_to_dict", "_from_dict", "_to_json", "_from_json", "_to_markdown",
    "_execute", "_render", "_search", "_generate", "_parse", "_format",
    "_apply", "_call", "_invoke", "_emit", "_trigger", "_fire",
    "_configure", "_initialize", "_finalize", "_is_valid", "_is_empty",
    "_append", "_extend", "_insert", "_pop", "_index", "_count",
    "_setup", "_teardown", "_cleanup", "_reset", "_clear",
    "_get_stats", "_available", "_copy", "_clone",
}


def _is_generic_name(name: str) -> bool:
    """判断是否为通用方法名噪音（构造/序列化/执行/渲染等），供 P0-B/P0-C 过滤。"""
    if name.startswith("__") and name.endswith("__"):
        return True
    return name in _GENERIC_NAMES


# 通用类名（配置/结果/测试/数据载体等），P0-B 真身判定时过滤，
# 聚焦业务级类名（FusionResearchBot / BrandMasterEngine / CatchphraseEngine）。
# 仅完全匹配才过滤，避免误伤 LLMClient / ZhihuAdapter / DAGScheduler 等业务类。
_GENERIC_CLASS_NAMES = {
    "Config", "Configuration", "Settings", "Options", "Setting",
    "Result", "Results", "Response", "Request",
    "Test", "Tests", "TestCase", "TestResult", "TestReport", "TestSuite",
    "Base", "BaseClass", "Object", "Model", "Models", "Data", "Item",
    "Error", "Exception", "State", "Status", "Event", "Context",
    "Manager", "Service", "Controller", "Handler", "Helper", "Util", "Utils",
    "Info", "Meta", "Metadata", "Constants", "Constant", "Colors", "Color",
    "Node", "Edge", "Graph", "Tree", "List", "Dict", "Map", "Set",
    "DTO", "VO", "Entity", "Schema", "Record", "Entry",
}


# 通用类名后缀（配置/结果/状态/测试/数据载体等），P0-B 真身判定时过滤，
# 聚焦业务级类名（FusionResearchBot / BrandMasterEngine / CatchphraseEngine）。
# 仅对明确通用的后缀做匹配，避免误伤 LLMClient / ZhihuAdapter / DAGScheduler 等业务类。
_GENERIC_CLASS_SUFFIXES = (
    "Config", "Configuration", "Settings", "Options",
    "Result", "Results", "Response", "Request",
    "Context", "Status", "State", "Event",
    "TestCase", "TestResult", "TestReport", "TestSuite",
    "DTO", "VO", "Entity", "Schema", "Record",
    "Node", "Edge", "Graph", "Tree",
    "Error", "Exception", "Data", "Item", "Info", "Meta",
)


def _is_generic_class_name(name: str) -> bool:
    """判断是否为通用类名噪音（Config/Result/Test 等），供 P0-B 真身判定过滤。"""
    if name in _GENERIC_CLASS_NAMES:
        return True
    return name.endswith(_GENERIC_CLASS_SUFFIXES)


# 业务类名后缀：P0-B 真身判定排序时优先展示业务级类（FusionResearchBot /
# BrandMasterEngine / CatchphraseEngine 等），避免 2 副本双真身被 3+ 副本
# 通用类挤出 top 列表。仅保留强业务后缀（Bot/Engine/Workflow 等），
# Adapter/Client/Store 等宽泛后缀不参与优先（避免 LLMClient x4 类噪音压过双真身）。
_BUSINESS_CLASS_SUFFIXES = (
    "Bot", "Engine", "Workflow", "Generator", "Extractor", "Master",
    "Analyzer", "Planner", "Scheduler", "Simulator", "Agent", "Pipeline",
    "Compiler", "Scraper", "Crawler", "Collector", "Evaluator",
    "Optimizer", "Validator", "Detector", "Auditor", "Verifier",
    "Processor", "Renderer", "Exporter", "Importer", "Loader", "Parser",
    "Fetcher", "Tracker", "Monitor", "Builder", "Factory", "Strategy",
)


def _business_class_score(name: str) -> int:
    """业务类名相关度：含业务后缀返回 1，否则 0（供真身判定优先展示）。"""
    return 1 if name.endswith(_BUSINESS_CLASS_SUFFIXES) else 0


def _is_test_file(fp: str) -> bool:
    """判断文件是否测试文件（test_/tests/测试 等），供"生产入口"判定排除测试引用。"""
    fp = (fp or "").replace("\\", "/")
    base = os.path.basename(fp).lower()
    if base.startswith("test_") or base.startswith("test-") or base.startswith("测试"):
        return True
    segs = [s for s in fp.split("/") if s]
    return any(s in ("test", "tests", "测试") for s in segs[:-1])


def _jaccard_set(a: set, b: set) -> float:
    """集合 Jaccard 相似度（0~1）。"""
    if not a and not b:
        return 1.0
    u = a | b
    return len(a & b) / len(u) if u else 0.0


def _rel_dir(project_path: str, fp: str) -> str:
    """文件相对 project_path 的目录（相对路径，避免不同父目录下同名子目录被合并）。"""
    fp = (fp or "").replace("\\", "/")
    if os.path.isabs(fp):
        try:
            fp = os.path.relpath(fp, project_path).replace("\\", "/")
        except Exception:
            pass
    return os.path.dirname(fp) or ""


# ═══════════════════════════════════════════════════════════════════
# P0-A 管线梳理
# ═══════════════════════════════════════════════════════════════════

_ENTRY_HINTS = ("main", "run", "start", "entry", "index", "app", "serve", "launch")


def _entry_score(name: str) -> int:
    """入口启发式评分：main/run/start 等典型入口名优先。"""
    nl = name.lower()
    if nl in _ENTRY_HINTS:
        return 4
    if any(k in nl for k in _ENTRY_HINTS):
        return 2
    return 0


def pipeline_insight(project_path: str, db_path: Optional[str] = None,
                     max_entries: int = 6, max_depth: int = 6) -> Dict:
    """P0-A：管线自动梳理。

    返回 {"entries": [{entry, steps:[{name,file,line,doc}]}], "flows": [{source,target,funcs,count}]}。
    入口自动发现：无被调用方的函数（root）+ 启发式排序；每入口沿 CALLS 下游归纳阶段序。
    """
    fv = _verifier(project_path, db_path)
    if fv is None:
        return {"ok": False, "entries": [], "flows": []}

    roots = fv.root_functions()
    ranked = sorted(roots, key=_entry_score, reverse=True)

    entries = []
    for spec in ranked[:max_entries]:
        chain = fv.entry_chain(spec, max_depth=max_depth)
        if len(chain) >= 2:  # 至少 2 步才算管线
            entries.append({"entry": spec, "steps": chain})

    flows = fv.cross_module_flows()
    return {"ok": True, "entries": entries, "flows": flows[:20]}


def _pipeline_markdown(data: Dict) -> str:
    lines = ["## 🧭 管线梳理（P0-A）"]
    if not data.get("ok"):
        lines.append("> 知识图谱不可用，管线梳理跳过。")
        return "\n".join(lines) + "\n"

    entries = data.get("entries") or []
    flows = data.get("flows") or []
    if not entries and not flows:
        lines.append("> 未发现可归纳的管线（无 ≥2 步的入口调用链，也无跨模块数据流）。")
        return "\n".join(lines) + "\n"

    if entries:
        lines.append("### 入口管线（沿 CALLS 归纳阶段序）")
        for e in entries:
            lines.append(f"**入口 `{e['entry']}`**（{len(e['steps'])} 步）")
            lines.append("| 阶段 | 符号 | 文件 | 行 | 说明 |")
            lines.append("|------|------|------|----|------|")
            for i, s in enumerate(e["steps"], 1):
                doc = (s.get("doc") or "").replace("\n", " ").strip()[:60]
                lines.append(f"| {i} | `{s['name']}` | `{s['file']}` | {s.get('line', 0)} | {doc} |")
            lines.append("")

    if flows:
        lines.append("### 跨模块业务数据流")
        lines.append("| 源模块 | 目标模块 | 调用函数 | 次数 |")
        lines.append("|--------|----------|----------|------|")
        for f in flows[:20]:
            funcs = ", ".join(f"`{x}`" for x in (f.get("funcs") or [])[:5])
            lines.append(f"| `{f['source']}` | `{f['target']}` | {funcs} | {f.get('count', 0)} |")
        lines.append("")

    return "\n".join(lines) + "\n"


# ═══════════════════════════════════════════════════════════════════
# P0-B 真身/入口判定
# ═══════════════════════════════════════════════════════════════════

def identity_insight(project_path: str, db_path: Optional[str] = None,
                     max_items: int = 20) -> Dict:
    """P0-B：真身/入口判定（ 业务级增强）。

    同名多目录实现（>1 个文件），对每个副本统计 inbound CALLS（被谁引用）、活跃度，
    报告各副本的引用方（文件:行 + 符号名），判定哪个是生产入口候选。
    聚合范围：业务级类名（type=class，聚焦同名业务类双真身，如 FusionResearchBot；
    方法名/函数名噪音由 P0-C 重复识别承载，不在此聚合，避免 chat x24 类噪音淹没类名）。
    返回 {"ok", "items":[{name, copies:[{file,line,mod,callers,active,is_root,refs,is_test,verdict}]}]}。
    """
    fv = _verifier(project_path, db_path)
    if fv is None:
        return {"ok": False, "items": []}

    by_name: Dict[str, List[str]] = defaultdict(list)
    for nid, n in fv.nodes.items():
        if n.get("type") == "class":
            nm = n["name"].split(".")[-1]
            if _is_generic_class_name(nm):
                continue
            by_name[nm].append(nid)

    items = []
    for name, ids in by_name.items():
        if len(ids) < 2:
            continue
        copies = []
        for nid in ids:
            n = fv.nodes[nid]
            inbound = [src for src, tgts in fv.adj.items() if nid in tgts]
            # 引用方详情：文件:行 + 符号名（排除测试文件引用）
            refs = []
            for src in inbound:
                sn = fv.nodes.get(src, {})
                sfile = (sn.get("file_path") or "").replace("\\", "/")
                if _is_test_file(sfile):
                    continue
                refs.append({
                    "file": sfile,
                    "line": sn.get("start_line", 0),
                    "sym": sn.get("name", ""),
                })
            copies.append({
                "id": nid,
                "file": (n.get("file_path") or "").replace("\\", "/"),
                "line": n.get("start_line", 0),
                "mod": _mod_of(n.get("file_path") or ""),
                "callers": len(inbound),
                "active": len(inbound) > 0,
                "is_root": len(inbound) == 0,
                "refs": refs[:6],
                "is_test": _is_test_file(n.get("file_path") or ""),
            })
        copies.sort(key=lambda c: (-c["callers"], c["file"]))
        roots = [c for c in copies if c["is_root"]]
        # 判定：生产入口通常无图内调用者（root），故仅 root 副本可标"生产入口候选"；
        # 有生产引用（非测试）的副本标"活跃真身"；仅测试引用的副本标"仅测试引用"。
        for c in copies:
            prod_refs = [r for r in c["refs"] if not _is_test_file(r["file"])]
            if c["is_root"]:
                if c["is_test"]:
                    c["verdict"] = "仅测试文件（无被调用者）"
                else:
                    c["verdict"] = "生产入口候选（无被调用者）"
            elif prod_refs:
                c["verdict"] = "活跃真身"
            elif c["callers"] > 0:
                c["verdict"] = "仅测试引用"
            else:
                c["verdict"] = "无引用（死/备选）"
        if not roots and copies and copies[0]["callers"] > 0:
            copies[0]["verdict"] = "被引用最多的副本"
        items.append({"name": name, "copies": copies})

    items.sort(key=lambda it: (-_business_class_score(it["name"]),
                               -len(it["copies"])))
    return {"ok": True, "items": items[:max_items]}


def _identity_markdown(data: Dict) -> str:
    lines = ["## 🎯 真身/入口判定（P0-B）"]
    if not data.get("ok"):
        lines.append("> 知识图谱不可用，真身判定跳过。")
        return "\n".join(lines) + "\n"

    items = data.get("items") or []
    if not items:
        lines.append("> 未发现同名多目录实现（每个符号仅一处定义）。")
        return "\n".join(lines) + "\n"

    lines.append("> 聚焦业务级同名类（已过滤 `__init__`/`to_dict`/`execute` 等通用方法名噪音）。")
    for it in items:
        lines.append(f"**`{it['name']}`**（{len(it['copies'])} 处实现）")
        lines.append("| 判定 | 模块 | 文件 | 行 | 被引用 | 引用方（文件:行 符号） |")
        lines.append("|------|------|------|----|--------|------------------------|")
        for c in it["copies"]:
            refs = "、".join(
                f"`{r['file']}:{r['line']} {r['sym']}`" for r in c.get("refs") or []) or "—"
            lines.append(f"| {c['verdict']} | `{c['mod']}` | `{c['file']}` | {c['line']} | {c['callers']} | {refs} |")
        lines.append("")

    return "\n".join(lines) + "\n"


# ═══════════════════════════════════════════════════════════════════
# P0-C 重复/同构识别
# ═══════════════════════════════════════════════════════════════════

def _rel_parts(project_path: str, fp: str) -> List[str]:
    """文件相对项目根的路径段列表（含文件名）。"""
    fp = (fp or "").replace("\\", "/")
    if os.path.isabs(fp):
        try:
            rel = os.path.relpath(fp, project_path)
        except ValueError:
            rel = fp
        rel = rel.replace("\\", "/")
    else:
        rel = fp
    return [p for p in rel.split("/") if p]


# 分支名命中以下片段 → 疑似废弃/备份目录，不判设计并存（防死复制误判）
_DEAD_DIR_HINTS = ("legacy", "old", "bak", "backup", "archive", "deprecated",
                   "废弃", "旧版", "备份", "_v1", "_v2")

# 重复识别：归一化后方法体最小长度（短文本 bigram 相似度虚高，如 return x vs return y）
_MIN_BODY_LEN = 20


def _is_parallel_structure(project_path: str, copies: List[Dict]) -> bool:
    """平行管线/设计并存信号（ 语义分层）：副本目录在共同分支点后对称。

    分支点后每侧目录层级数相同、分支名不同（且非废弃/备份目录）→ 判定为
    同一设计模板的两个平行实例，如 目标项目 的
    alone_doc/doc-to-skill/scripts vs alone_web/web-to-skill/scripts
    （文档转技能 vs 网页转技能，有意并存的产品线）。设计并存不应机械收敛。

    共同前缀允许为 0（平行管线可从项目根直接分叉）；分支名及其后的所有目录段
    任一段命中废弃/备份目录提示（legacy/old/bak/backup/archive/deprecated/
    废弃/旧版/备份/_v1/_v2，大小写不敏感）→ 视为死复制而非设计并存。
    """
    paths = []
    for c in copies:
        parts = _rel_parts(project_path, c.get("file") or "")
        if len(parts) < 3:  # 至少 分支名/子目录/文件名
            return False
        paths.append(parts)
    if len(paths) < 2:
        return False
    p0 = paths[0]
    common = 0
    for i, seg in enumerate(p0[:-1]):
        if all(len(p) > i and p[i] == seg for p in paths):
            common = i + 1
        else:
            break
    depths = {len(p) - common - 1 for p in paths}
    if len(depths) != 1 or next(iter(depths)) < 1:
        return False
    branches = {p[common] for p in paths if len(p) > common}
    if len(branches) < 2:
        return False

    def _hits_dead_hint(seg: str) -> bool:
        low = seg.lower()
        return any(h in low for h in _DEAD_DIR_HINTS)

    # 分支名 + 分支后各目录段（不含文件名）任一段命中废弃提示 → 非设计并存
    for p in paths:
        for seg in p[common:-1]:
            if _hits_dead_hint(seg):
                return False
    return True


def _dir_inventory(project_path: str, fv) -> Dict[str, Dict]:
    """按相对目录聚合：文件 basename 集 + 函数签名集（供目录级同构比对）。"""
    dirs: Dict[str, Dict] = defaultdict(lambda: {"files": set(), "funcs": set()})
    for nid, n in fv.nodes.items():
        fp = (n.get("file_path") or "").replace("\\", "/")
        d = _rel_dir(project_path, fp)
        if not d:
            continue
        dirs[d]["files"].add(os.path.basename(fp))
        if n.get("type") in ("function", "method"):
            dirs[d]["funcs"].add(n["name"].split(".")[-1])
    return dict(dirs)


def _dir_isomorph_insight(project_path: str, fv, max_pairs: int = 10,
                          file_threshold: float = 0.5,
                          func_threshold: float = 0.5) -> List[Dict]:
    """目录级同构比对（）：文件清单相似度 + 函数签名相似度。

    对目录两两比较，双指标均 ≥ 阈值视为"同构重复候选"（如 业务工具/engine vs
    主服务/engine 全目录同构、batch2/batch2_v2/batch2_fast 多版本并存）。
    返回 [{dir_a, dir_b, file_sim, func_sim, n_files_a, n_files_b}]，按综合相似度降序。
    """
    dirs = _dir_inventory(project_path, fv)
    names = sorted(dirs.keys())
    pairs = []
    for i in range(len(names)):
        for j in range(i + 1, len(names)):
            a, b = names[i], names[j]
            fa, fb = dirs[a]["files"], dirs[b]["files"]
            sa, sb = dirs[a]["funcs"], dirs[b]["funcs"]
            if not sa or not sb:  # 任一侧无函数签名时跳过，避免空集 Jaccard=1.0 误判同构
                continue
            file_sim = _jaccard_set(fa, fb)
            func_sim = _jaccard_set(sa, sb)
            if file_sim >= file_threshold and func_sim >= func_threshold:
                pairs.append({
                    "dir_a": a, "dir_b": b,
                    "file_sim": round(file_sim, 2), "func_sim": round(func_sim, 2),
                    "n_files_a": len(fa), "n_files_b": len(fb),
                })
    pairs.sort(key=lambda p: -(p["file_sim"] + p["func_sim"]))
    return pairs[:max_pairs]


def duplicate_insight(project_path: str, db_path: Optional[str] = None,
                      max_clusters: int = 20, sim_threshold: float = 0.6,
                      exclude_dirs: Optional[List[str]] = None) -> Dict:
    """P0-C：重复/同构识别（ 业务级增强）。

    同名函数/方法跨模块（不同目录）实现 → 先按函数体相似度分区：
    - 相似度 ≥ sim_threshold 的副本聚成独立"重复实现簇"（kind=duplicate，推荐收敛）
    - 未配对（与任何副本相似度 < 阈值）的副本归入"同名候选"（kind=candidate，
      仅同名、契约可能不同，不推荐合并）
    过滤通用方法名噪音（__init__/to_dict 等），并附加目录级同构比对
    （dir_isomorph: 文件清单 + 函数签名相似度，识别全目录同构重复）。
    exclude_dirs: 需排除的目录相对路径列表（whitelist dir 实时生效，
        加载图谱后过滤，避免旧图谱未排除的备份目录噪声进入重复识别）。
    返回 {"ok", "clusters":[...], "dir_isomorph":[...]}。
    """
    fv = _verifier(project_path, db_path)
    if fv is None:
        return {"ok": False, "clusters": [], "dir_isomorph": []}
    if exclude_dirs:
        fv.nodes, _ = filter_excluded(fv.nodes, fv.adj, project_path, exclude_dirs)
        # 重建名称索引，保证过滤后索引与节点集一致
        fv.name_index = defaultdict(list)
        for nid, n in fv.nodes.items():
            fv.name_index[n["name"].lower()].append(nid)
            if "." in n["name"]:
                fv.name_index[n["name"].split(".")[-1].lower()].append(nid)

    by_name: Dict[str, List[str]] = defaultdict(list)
    for nid, n in fv.nodes.items():
        if n.get("type") not in ("function", "method"):
            continue
        if n.get("type") == "method":
            # method 保留宿主类全名（类名.方法名）：不同宿主类的同名方法（如
            # DeepSeekClient.chat 调 API vs DiscussionEngine.chat 管理会话）契约不同，
            # 是正常多态而非重复，按短名聚合会误报真重复（外部反馈）。
            nm = n["name"]
        else:
            nm = n["name"].split(".")[-1]
        if _is_generic_name(nm.split(".")[-1]):
            continue
        by_name[nm].append(nid)

    clusters = []
    for name, ids in by_name.items():
        if len(ids) < 2:
            continue
        copies = []
        dirs = set()
        for nid in ids:
            n = fv.nodes[nid]
            fp = (n.get("file_path") or "").replace("\\", "/")
            mod = _mod_of(n.get("file_path") or "")
            props = n.get("props") or {}
            copies.append({
                "file": fp, "line": n.get("start_line", 0), "mod": mod,
                "body": _norm_body(_abs_path(project_path, fp),
                                   n.get("start_line", 0), n.get("end_line", 0)),
                "params": props.get("params") or [],
                "return_type": props.get("return_type") or "",
            })
            # 跨目录判定用相对路径目录（避免 apps/worker 与 legacy/worker 同名
            # basename 被 _mod_of 合并误判同目录）；_mod_of 仅用于报告展示
            dirs.add(_rel_dir(project_path, n.get("file_path") or ""))
        if len(dirs) < 2:  # 跨目录才算"重复/同名候选"
            continue
        # 短方法体过滤：归一化后过短的副本不参与相似度聚类（短文本 bigram 虚高，
        # 如 return x vs return y 契约不同却被算 0.7+ 相似），降为同名候选
        long_copies = [c for c in copies if len(c["body"]) >= _MIN_BODY_LEN]
        short_copies = [c for c in copies if len(c["body"]) < _MIN_BODY_LEN]
        # 按函数体相似度分区：相似度 ≥ 阈值且契约兼容的副本聚成独立重复簇，
        # 未配对/契约不同/短方法体副本作同名候选
        dup_clusters, singles = _partition_copies(long_copies, sim_threshold)
        singles = singles + short_copies
        for cl in dup_clusters:
            max_sim = 0.0
            for i in range(len(cl)):
                for j in range(i + 1, len(cl)):
                    s = _jaccard(cl[i]["body"], cl[j]["body"])
                    if s > max_sim:
                        max_sim = s
            for c in cl:
                c.pop("body", None)
            clusters.append({"name": name, "kind": "duplicate",
                             "semantic_kind": ("designed_parallel"
                                               if _is_parallel_structure(project_path, cl)
                                               else "true_duplicate"),
                             "max_sim": round(max_sim, 2), "copies": cl})
        if singles:
            for c in singles:
                c.pop("body", None)
            clusters.append({"name": name, "kind": "candidate",
                             "max_sim": 0.0, "copies": singles})

    clusters.sort(key=lambda c: (-len(c["copies"]), c["kind"] != "duplicate"))
    dir_isomorph = _dir_isomorph_insight(project_path, fv)
    return {"ok": True, "clusters": clusters[:max_clusters],
            "dir_isomorph": dir_isomorph}


def _contract_desc(c: Dict) -> str:
    """生成副本契约描述（签名 + 返回类型），供 duplicate 结果区分理由。"""
    params = [p for p in (c.get("params") or []) if p != "self"]
    sig = "(" + ", ".join(params) + ")"
    rt = (c.get("return_type") or "").strip()
    return f"{sig} → {rt}" if rt else sig


def _duplicate_markdown(data: Dict) -> str:
    lines = ["## 🔍 重复/同构识别（P0-C）"]
    if not data.get("ok"):
        lines.append("> 知识图谱不可用，重复识别跳过。")
        return "\n".join(lines) + "\n"

    clusters = data.get("clusters") or []
    dir_isomorph = data.get("dir_isomorph") or []
    if not clusters and not dir_isomorph:
        lines.append("> 未发现跨模块同名实现或目录级同构（每个符号仅一处定义）。")
        return "\n".join(lines) + "\n"

    lines.append("> 聚焦业务级同名函数（已过滤 `__init__`/`to_dict`/`execute` 等通用方法名噪音）。")
    dup = [c for c in clusters if c.get("kind") == "duplicate"]
    cand = [c for c in clusters if c.get("kind") != "duplicate"]
    true_dup = [c for c in dup if c.get("semantic_kind") != "designed_parallel"]
    parallel = [c for c in dup if c.get("semantic_kind") == "designed_parallel"]
    if true_dup:
        lines.append("### 重复实现簇（真重复，建议收敛/抽公共工具）")
        lines.append("| 符号 | 实现数 | 相似度 | 模块 | 文件:行 |")
        lines.append("|------|--------|--------|------|---------|")
        for c in true_dup:
            first = c["copies"][0]
            rest = c["copies"][1:]
            mods = "、".join(f"`{x['mod']}`" for x in c["copies"])
            locs = f"`{first['file']}:{first['line']}`"
            if rest:
                locs += " 等 " + "、".join(f"`{x['file']}:{x['line']}`" for x in rest[:4])
            lines.append(f"| `{c['name']}` | {len(c['copies'])} | {c.get('max_sim', 0)} | {mods} | {locs} |")
        lines.append("")
    if parallel:
        lines.append("### 平行管线/设计并存（有意并存的产品线/多实现，保留，不建议收敛）")
        lines.append("| 符号 | 实现数 | 相似度 | 模块 | 文件:行 |")
        lines.append("|------|--------|--------|------|---------|")
        for c in parallel:
            first = c["copies"][0]
            rest = c["copies"][1:]
            mods = "、".join(f"`{x['mod']}`" for x in c["copies"])
            locs = f"`{first['file']}:{first['line']}`"
            if rest:
                locs += " 等 " + "、".join(f"`{x['file']}:{x['line']}`" for x in rest[:4])
            lines.append(f"| `{c['name']}` | {len(c['copies'])} | {c.get('max_sim', 0)} | {mods} | {locs} |")
        lines.append("")
    if cand:
        lines.append("### 同名候选（仅同名、契约可能不同，不推荐合并）")
        lines.append("| 符号 | 实现数 | 相似度 | 契约（签名 → 返回） | 模块 |")
        lines.append("|------|--------|--------|-------------------|------|")
        for c in cand:
            mods = "、".join(f"`{x['mod']}`" for x in c["copies"])
            contracts = " / ".join(f"`{_contract_desc(x)}`" for x in c["copies"][:4])
            lines.append(f"| `{c['name']}` | {len(c['copies'])} | {c.get('max_sim', 0)} | {contracts} | {mods} |")
        lines.append("")
    if dir_isomorph:
        lines.append("### 目录级同构重复（文件清单 + 函数签名相似度，可合并候选）")
        lines.append("| 目录 A | 目录 B | 文件相似度 | 函数相似度 | 文件数 A/B |")
        lines.append("|--------|--------|-----------|-----------|-----------|")
        for p in dir_isomorph:
            lines.append(f"| `{p['dir_a']}` | `{p['dir_b']}` | {p['file_sim']} | {p['func_sim']} | {p['n_files_a']}/{p['n_files_b']} |")
        lines.append("")

    return "\n".join(lines) + "\n"


# ═══════════════════════════════════════════════════════════════════
# LLM 可选：人话总结
# ═══════════════════════════════════════════════════════════════════

def _llm_summary(project_path: str, data: Dict) -> str:
    """对三段静态结果生成一段人话总结；LLM 不可用返回空串（不降级静态结果）。"""
    try:
        from core.llm_integration import LLMIntegration
        llm = LLMIntegration()
        if not llm.is_available():
            return ""
        n_entries = len(data.get("pipeline", {}).get("entries", []))
        n_flows = len(data.get("pipeline", {}).get("flows", []))
        n_identity = len(data.get("identity", {}).get("items", []))
        n_dup = len(data.get("duplicate", {}).get("clusters", []))
        n_dir = len(data.get("duplicate", {}).get("dir_isomorph", []))
        prompt = (
            f"项目 {project_path} 的架构静态洞察：\n"
            f"- 管线梳理：{n_entries} 条入口管线，{n_flows} 条跨模块数据流\n"
            f"- 真身判定：{n_identity} 组同名业务级多实现\n"
            f"- 重复识别：{n_dup} 组跨模块重复实现簇，{n_dir} 组目录级同构重复\n"
            f"请用 3-5 句话概括该项目的架构健康状况与治理重点（通俗中文，面向维护中大型代码的工程师）。"
        )
        return llm.chat_completion([
            {"role": "system", "content": "你是一位资深架构治理工程师，请用通俗中文给出简洁的架构洞察总结。"},
            {"role": "user", "content": prompt},
        ]).strip()
    except Exception as e:  # pragma: no cover
        logger.warning(f"[arch_insight] LLM 总结失败: {e}")
        return ""


# ═══════════════════════════════════════════════════════════════════
# 组合入口
# ═══════════════════════════════════════════════════════════════════

def insight_markdown(project_path: str, db_path: Optional[str] = None,
                     use_llm: bool = False) -> str:
    """生成  三段洞察 Markdown（管线/真身/重复），供 architecture 报告追加。

    静态结果始终完整产出；use_llm=True 且 LLM 可用时追加一段人话总结。
    """
    data = {
        "pipeline": pipeline_insight(project_path, db_path),
        "identity": identity_insight(project_path, db_path),
        "duplicate": duplicate_insight(project_path, db_path),
    }
    parts = [
        _pipeline_markdown(data["pipeline"]),
        _identity_markdown(data["identity"]),
        _duplicate_markdown(data["duplicate"]),
    ]
    if use_llm:
        summary = _llm_summary(project_path, data)
        if summary:
            parts.insert(0, f"## 💬 架构洞察总结（LLM）\n\n{summary}\n")
    return "\n".join(parts).strip()


def main() -> None:  # pragma: no cover
    """CLI 冒烟：python -m core.arch_insight <project_path> [--llm]"""
    import argparse
    ap = argparse.ArgumentParser(description="架构洞察（管线/真身/重复）")
    ap.add_argument("project_path")
    ap.add_argument("--llm", action="store_true", help="启用 LLM 总结")
    args = ap.parse_args()
    print(insight_markdown(args.project_path, use_llm=args.llm))


if __name__ == "__main__":  # pragma: no cover
    main()
