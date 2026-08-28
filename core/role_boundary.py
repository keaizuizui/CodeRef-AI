# -*- coding: utf-8 -*-
"""
role_boundary —— 符号级职责越界检测（5.2 升格实现，解决 5.0 §6.2 遗留）

背景：模块归属（arch_gap_analyzer）正确，不代表模块内每个符号都守住了自己
角色的边界。典型场景：`waiter.py`（角色 waiter）里定义/调用了本属 `chef`
职责的 `cook()`。本模块把"符号级职责越界"做成确定性、可复现的静态检测。

检测原理（确定性，零 LLM）：
  1. 模块 → 角色归属：按目标架构 tech_roles.target_modules 精确/basename 匹配。
  2. 命名语义（definition 越界）：在归属角色 R 的模块 m 里，顶层类/函数名（职责
     单元声明）命中"其他角色 X（X≠R）"的 role_keywords → 该符号疑似本属角色 X，
     却在 R 的模块里实现。方法名是行为描述（_llm_edit / _generate_candidates）
     而非职责单元声明，方法级撞词不判 definition（相关信息由 call_hints 承载）；
     本角色 role_keywords 全为中文等无法 token 化的职责词时（无法为英文符号提供
     锚点），判定全是跨语言撞词即噪音，同样不判 definition。
  3. 调用边界（call 越界提示）：模块 m(角色 R) 内符号调用了名字命中角色 X≠R
     关键词的函数 → 跨角色调用提示（比 definition 越界弱，标记为提示）。

语义判定接口（可选，缺 LLM 时只给静态信号 + uncertainty，不硬阻断）：
  在 top 候选上调用可选语义确认，返回越界/可接受及理由；LLM 不可用或未启用时
  返回空，不影响静态检测结果。

定位方式：AST 扫描生产 .py 文件（对符号全覆盖），叠加知识图谱确认项目已被索引
（has_kg）供元数据；不依赖图谱节点其本身的存在性，保证符号级覆盖率。
"""

import json
import os
import re
import ast
from typing import Any, Dict, List, Optional, Tuple

from loguru import logger

SEVERITY = "medium"  # 符号级越界整体为中严重级（低于结构级结构性问题）


def _esc(s: Any) -> str:
    return str(s if s is not None else "")


def _tokens(name: str) -> List[str]:
    """把标识符拆成语义 token（snake_case / camelCase / PascalCase → 小写 token）。"""
    s = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", name or "")
    s = re.sub(r"(_+)", "_", s)
    # 分隔符必须含大写：若用 [^a-z0-9]+，PascalCase 首字母会被当成分隔符吃掉
    # （TaskQueue → ['ask','ueue']），导致类名/大写词 token 损坏、角色关键词锚点失配。
    return [t.lower() for t in re.split(r"[^a-zA-Z0-9]+", s) if t]


def _keyword_tokens(kw: str) -> List[str]:
    """把 role_keywords 拆成 token（支持 'handle_order' / 'prepare dish' 写法）。"""
    return _tokens(kw)


def _hits_keyword(sym_tokens: List[str], kw_tokens: List[str]) -> bool:
    """判断符号 token 是否命中角色关键词 token（全 token 边界匹配）。

    命中规则（保守，防泛词误报）：
      - 关键词首 token 必须与符号的某个组成单元（token）整词相等。
      - 多 token 关键词（task_scheduler / structured_output / deep_scraper）
        要求"非首 token"也至少匹配一个，防只凭首 token 撞词
        （get_task 命中 task_scheduler 的 task、chat_structured 命中
        structured_output 的 structured 等误报）。
      - 不做子串 / 前缀匹配：token 切分（_ . 大写边界）已由 _tokens 完成，
        此处仅整词判等，避免泛词 app 命中 application、entry 命中 entrance
        等"同位但语义不同"的 token 造成误判。
    """
    if not sym_tokens or not kw_tokens:
        return False
    head = kw_tokens[0]
    if head not in sym_tokens:
        return False
    if len(kw_tokens) > 1 and not any(t in sym_tokens for t in kw_tokens[1:]):
        return False
    return True


# 内置泛词/token 黑名单：命名过于泛化，难以作为强越界信号。
# 命中这些 token 只作低置信度提示，不可硬判职责越界（保守，防误报）。
_GENERIC_KEYWORD_TOKENS = frozenset({
    "app", "apps", "main", "entry", "service", "services",
    "util", "utils", "helper", "helpers", "base", "core", "common",
    "manager", "handler", "handlers",
})


def _is_generic_token(tok: str) -> bool:
    """某 token 是否属于内置泛词黑名单。"""
    return tok in _GENERIC_KEYWORD_TOKENS


def _all_matched_generic(kw_list: Optional[List[str]]) -> bool:
    """命中的关键词是否全部为泛词（用于低置信度降级）。

    按完整关键词的所有 token 判断（service_client → [service, client] 含非泛词
    client → 不降级；仅单 token 的 service 等纯泛词才降级），避免只取首 token
    把 service_client 误判为纯泛词（CodeRabbit minor）。
    """
    if not kw_list:
        return False
    for kw in kw_list:
        toks = _tokens(kw)
        if not toks or not all(_is_generic_token(t) for t in toks):
            return False
    return True


# call 弱信号降权词：异常/日志等通用支撑词，任何层模块调用都属正常机制，
# 不构成"跨角色职责纠缠"提示（仅影响 call_hints，不影响 definition 判定）。
_CALL_DEWEIGHT_KEYWORDS = frozenset({"error", "logging"})


def _all_matched_deweight(kw_matched: Optional[List[str]]) -> bool:
    """命中的关键词是否全部为 call 降权支撑词（用于 call_hints 过滤）。

    按完整关键词的所有 token 判断（error_handler → [error, handler] 含非降权词
    handler → 不删除；仅 error / logging 纯支撑词才过滤），避免只取首 token
    把 error_handler 误从 call_hints 删除（CodeRabbit minor）。
    """
    if not kw_matched:
        return False
    for kw in kw_matched:
        toks = _tokens(kw)
        if not toks or not all(t in _CALL_DEWEIGHT_KEYWORDS for t in toks):
            return False
    return True


def _norm_spec(spec: str) -> str:
    s = (spec or "").strip().replace("\\", "/")
    if s.endswith(".py"):
        s = s[:-3]
    return s


def _is_test_module(rel: str) -> bool:
    """生产/测试判断：相对路径含 tests/ 或 test_/_test 前缀/后缀。"""
    r = (rel or "").replace("\\", "/")
    if "/tests/" in r or r.startswith("tests/") or "/test_" in r or r.startswith("test_"):
        return True
    base = r.split("/")[-1]
    return base.startswith("test_") or base.endswith("_test")


def _iter_py_files(project_path: str) -> List[str]:
    out = []
    for root, dirs, files in os.walk(project_path):
        # 跳过常见噪音目录
        dirs[:] = [d for d in dirs
                   if d not in (".git", ".coderef", "node_modules", "__pycache__",
                                "venv", ".venv", "env", ".env", "tests",
                                "coderef-report")]
        for f in files:
            if f.endswith(".py"):
                out.append(os.path.join(root, f))
    return out


class _Symbol:
    __slots__ = ("name", "kind", "line", "keywords_defined", "keywords_called")

    def __init__(self, name: str, kind: str, line: int):
        self.name = name
        self.kind = kind          # function / class / method
        self.line = line
        self.keywords_defined = list(_tokens(name))   # 定义名 token
        self.keywords_called = set()                  # 函数体/类体内调用名 token


def _scan_symbols(tree: ast.AST) -> List[_Symbol]:
    """提取模块顶层函数 / 类（含方法），并收集各符号体内被调用名 token。"""
    syms: List[_Symbol] = []
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            s = _Symbol(node.name, "function", node.lineno)
            s.keywords_called |= _call_tokens(node)
            syms.append(s)
        elif isinstance(node, ast.ClassDef):
            cls = _Symbol(node.name, "class", node.lineno)
            cls.keywords_called |= _call_tokens(node)
            syms.append(cls)
            for sub in ast.iter_child_nodes(node):
                if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    m = _Symbol(f"{node.name}.{sub.name}", "method", sub.lineno)
                    m.keywords_called |= _call_tokens(sub)
                    syms.append(m)
    return syms


def _call_tokens(node: ast.AST) -> set:
    """收集 AST 子树上所有 '被调用名' 的 token（Call 的 func 名/属性名）。"""
    toks: set = set()
    for c in ast.walk(node):
        if not isinstance(c, ast.Call):
            continue
        f = c.func
        name = None
        if isinstance(f, ast.Name):
            name = f.id
        elif isinstance(f, ast.Attribute):
            name = f.attr
        if name:
            toks |= set(_tokens(name))
    return toks


def _load_roles_with_keywords(target_arch: Dict[str, Any]) -> List[Dict[str, Any]]:
    """返回带 role_keywords 的角色列表；无关键词的角色不影响检测。"""
    roles = []
    for r in (target_arch.get("tech_roles") or []):
        if not isinstance(r, dict):
            continue
        kws = [k for k in (r.get("role_keywords") or []) if str(k).strip()]
        roles.append({
            "id": r.get("id", ""),
            "name": r.get("name", r.get("id", "")),
            "keywords": kws,
            "kw_texts": [str(k) for k in kws],   # 原始关键词文本（与 kw_tokens 索引对应）
            "kw_tokens": [_keyword_tokens(k) for k in kws],
            "target_modules": [x for x in (r.get("target_modules") or [])
                               if str(x).strip()],
        })
    return roles


def _module_role(rel: str, spec_map: Dict[str, Dict[str, Any]]) -> Tuple[str, str]:
    """把模块相对路径归属到角色。

    Returns (role_id, role_name)；未归属返回 ("", "")。
    """
    base = rel.split("/")[-1]
    # 精确相对路径匹配优先
    role = spec_map.get(rel)
    if role:
        return role["id"], role["name"]
    # basename 宽松兜底仅限"纯 basename spec（不含 /）"；带 / 的 spec 只精确匹配，
    # 防 gptr_service/main 因 basename=main 误配到 web/web端/main（与 arch_gap 同源修复）。
    for spec, role in spec_map.items():
        if "/" not in spec and spec == base:
            return role["id"], role["name"]
    return "", ""


def _match_suspected(sym_tokens: List[str], roles: List[Dict[str, Any]],
                     module_role_id: str) -> List[Tuple[str, Dict[str, Any], List[str]]]:
    """返回 (疑似角色id, 角色, 命中的完整关键词列表)。module_role 已剔除。

    命中关键词保留原始文本（service_client / error_handler 而非仅首 token），
    供调用方做泛词/降权判定时按完整关键词的 token 判断（CodeRabbit minor：
    只存首 token 会把 service_client 误判为纯泛词、把 error_handler 误判为
    纯 error 支撑词而误删 call_hints）。
    """
    hits = []
    for role in roles:
        rid = role["id"]
        if rid == module_role_id:        # 本角色自己的关键词不算越界
            continue
        kw_matched = []
        for kw_text, kw_tokens in zip(role["kw_texts"], role["kw_tokens"]):
            if _hits_keyword(sym_tokens, kw_tokens):
                kw_matched.append(kw_text)
        if kw_matched:
            hits.append((rid, role, kw_matched[:3]))
    return hits


def _hits_role_keywords(sym_tokens: List[str], role_id: str,
                        roles: List[Dict[str, Any]],
                        exclude_generic: bool = True) -> bool:
    """符号 token 是否命中指定角色（role_id）的任一 role_keywords。

    用于"本角色锚点优先"：符号名命中自己所属角色的职责关键词时，说明符号语义
    归属本角色；此时即便顺带命中其他角色同源词（task/config/llm 等高频通用词）
    也只是撞词，不再判 definition 越界（保守防误报）。类名锚定则传导到类内方法。
    exclude_generic=True（默认）：整个关键词全为泛词（service/manager/...）时不产生
    锚点——泛词命中是职责语义的最弱信号，若用它锚定（如本角色含 service 关键词，
    PaymentService 即被 anchored），会在泛词降级前就压掉真实越界判定（CodeRabbit major）。
    """
    for role in roles:
        if role["id"] != role_id:
            continue
        for kw_tokens in role["kw_tokens"]:
            if not kw_tokens:
                continue
            if exclude_generic and all(_is_generic_token(t) for t in kw_tokens):
                continue
            if _hits_keyword(sym_tokens, kw_tokens):
                return True
    return False


def detect(project_path: str, target_arch: Optional[Dict[str, Any]] = None,
           db_path: Optional[str] = None, max_issues: int = 200,
           semantic: bool = False) -> Dict[str, Any]:
    """符号级职责越界检测主入口。

    Args:
        project_path: 目标项目路径（须已构建知识图谱）。
        target_arch: 目标架构（dict；缺省读 <project>/.coderef/target_arch.json）。
        db_path: 知识图谱 db（缺省自动定位）。
        max_issues: 越界符号报出上限（防刷屏）。
        semantic: 是否启用可选语义判定（需要 LLM；缺省 False 只给静态信号）。

    Returns:
        结构化检测结果（boundary_issues / summary / semantic_note）。
    """
    from core.arch_audit import locate_kg_db
    from core.target_arch_schema import normalize_arch

    db = db_path or locate_kg_db(project_path)
    result = {
        "ok": False,
        "tool": "coderef_role_boundary",
        "project_path": project_path,
        "has_kg": False,
        "graph_stats": {},
        "arch": {"role_count": 0, "keyword_roles": []},
        "summary": {"modules_scanned": 0, "symbols_scanned": 0,
                    "boundary_issues": 0, "call_hints": 0, "by_role": {}},
        "boundary_issues": [],
        "call_hints": [],          # cross_role_call 弱信号（独立通道，不占越界主输出）
        "semantic_note": None,
    }
    # 图谱仅用于 graph_stats 元数据；符号级检测基于 AST，图谱缺失不阻断扫描
    result["has_kg"] = bool(db and os.path.exists(db))
    if not result["has_kg"]:
        result["kg_note"] = ("知识图谱不存在，仅缺少 graph_stats 元数据；"
                             "符号级检测基于 AST，仍照常执行")

    # —— 目标架构（含 role_keywords）——
    if target_arch is None:
        ta_path = os.path.join(project_path, ".coderef", "target_arch.json")
        if os.path.isfile(ta_path):
            try:
                with open(ta_path, encoding="utf-8") as f:
                    target_arch = json.load(f)
            except Exception as e:  # noqa: BLE001
                target_arch = {}
        else:
            target_arch = {}
    try:
        target_arch = normalize_arch(target_arch or {})
    except Exception:  # noqa: BLE001
        target_arch = {}

    roles = _load_roles_with_keywords(target_arch)
    result["arch"]["role_count"] = len(roles)
    result["arch"]["keyword_roles"] = [r["id"] for r in roles if r["keywords"]]

    if not any(r["keywords"] for r in roles):
        result["summary"]["message"] = ("目标架构未配置任何角色 role_keywords，"
                                        "无法做符号级职责判定；请在 tech_roles 中补 "
                                        "role_keywords（角色职责关键词表）")
        result["ok"] = True
        return result

    # —— 模块 → 角色归属（按 target_modules 相对路径）——
    spec_map: Dict[str, Dict[str, Any]] = {}
    for r in roles:
        for spec in r["target_modules"]:
            ns = _norm_spec(spec)
            if ns:
                spec_map[ns] = r

    # —— 图谱统计（复用 load_graph）——
    try:
        from core.graph_closure import load_graph
        nodes, adj = load_graph(db)
        result["graph_stats"] = {"nodes": len(nodes),
                                 "calls_edges": sum(len(v) for v in adj.values())}
    except Exception:  # noqa: BLE001
        result["graph_stats"] = {}

    # —— AST 扫描 ——
    issues: List[Dict[str, Any]] = []
    hints: List[Dict[str, Any]] = []   # cross_role_call 弱信号（独立通道，不占越界）
    modules_scanned = symbols_scanned = 0
    for path in _iter_py_files(project_path):
        rel = os.path.relpath(path, project_path).replace("\\", "/")
        if _is_test_module(rel):
            continue
        try:
            with open(path, encoding="utf-8", errors="replace") as f:
                src = f.read()
        except Exception:  # noqa: BLE001
            continue
        try:
            tree = ast.parse(src)
        except SyntaxError:
            continue
        mod_path = os.path.splitext(rel)[0]
        module_role_id, module_role_name = _module_role(mod_path, spec_map)
        # 目标架构未列出该模块时，模块归属本身就是差距（arch_gap_analyzer 已报），
        # 在此把符号当"职责越界"只会制造噪音，直接跳过未归属模块
        if not module_role_id:
            continue
        # 本角色 role_keywords 是否含可匹配英文符号的 token。若全为中文等无法
        # token 化的职责词（kw_tokens 全空），关键词体系无法为英文符号提供
        # "本角色锚点"，definition 越界判定全是跨语言撞词（如 business 模块类名
        # 含 engine/search 命中 service 关键词），报出即噪音 → 该模块不判 definition。
        module_role_obj = next((r for r in roles if r["id"] == module_role_id),
                               None)
        role_matchable = bool(module_role_obj
                              and any(kt for kt in module_role_obj["kw_tokens"]))
        syms = _scan_symbols(tree)
        if not syms:
            continue
        modules_scanned += 1
        # 类锚点：类名命中本角色职责关键词（BrandMasterEngine 命中 business「品牌」）
        # → 该类的方法视为本角色职责，锚点传导到方法（_init_llm 等不再误报越界）
        anchored_classes = {s.name for s in syms if s.kind == "class"
                            and _hits_role_keywords(s.keywords_defined,
                                                    module_role_id, roles)}
        for sym in syms:
            symbols_scanned += 1
            # 本角色锚点优先：符号名命中本角色职责关键词（或为锚定类的方法）
            # → 语义归属本角色；顺带命中其他角色同源词（task/config/llm）仅撞词
            anchored = _hits_role_keywords(sym.keywords_defined,
                                           module_role_id, roles)
            if sym.kind == "method" and sym.name.split(".")[0] in anchored_classes:
                anchored = True
            # definition 越界（真越界）：仅顶层类/函数——符号定义名命中其他角色
            # 关键词，且符号未命中本角色职责词锚点、本角色关键词可匹配英文符号。
            # 方法名是行为描述（_llm_edit / _generate_candidates）而非职责单元声明，
            # 方法级撞词几乎全是噪音，不判 definition（相关信息由 call_hints 承载）；
            # role_matchable=False（中文关键词角色）无法为英文符号提供本角色锚点，
            # 判定全是跨语言撞词即噪音，同样不判 definition。
            if sym.kind == "method":
                def_hits = []
            else:
                def_hits = ([] if anchored or not role_matchable
                            else _match_suspected(sym.keywords_defined, roles,
                                                  module_role_id))
            # call 越界提示：符号体内调用名命中其他角色关键词
            # （跨角色调用是分层协作常态，降级为独立 call_hints 弱信号，不进主越界；
            #   仅命中 error/logging 等支撑词的调用属通用机制，不构成提示，直接过滤）
            call_hits = [h for h in _match_suspected(list(sym.keywords_called),
                                                     roles, module_role_id)
                         if not _all_matched_deweight(h[2])]
            matched = []
            signals = []
            suspected_id = suspected_name = ""
            if def_hits:
                sid, srole, kws = sorted(def_hits,
                                         key=lambda x: len(x[2]),
                                         reverse=True)[0]
                suspected_id, suspected_name = sid, srole["name"]
                matched = kws
                signals.append("keyword_definition")
            elif call_hits:
                sid, srole, kws = sorted(call_hits,
                                         key=lambda x: len(x[2]),
                                         reverse=True)[0]
                suspected_id, suspected_name = sid, srole["name"]
                matched = kws
                signals.append("cross_role_call")
            if not signals:
                continue
            # 泛词关键词降级：命中的关键词若全部是泛词（app/main/...），
            # 只作低置信度提示，不硬判职责越界（保守，防误报）。
            generic_hint = bool(matched) and _all_matched_generic(matched)
            signals_out = list(signals)
            if generic_hint and "generic_keyword_hint" not in signals_out:
                signals_out.append("generic_keyword_hint")
            # 泛词命中一律高 uncertainty（本工具静态-only 口径下 high=不可靠），
            # 另用独立 confidence="low" 表达低置信提示，避免与现有 uncertainty
            # 语义相反、导致消费方把泛词命中当更高可信（CodeRabbit 二轮 minor）。
            uncertainty = "high" if (not semantic or generic_hint) else "medium"
            confidence = "low" if generic_hint else "high"
            generic_tail = ("；命中的均为泛词关键词，仅作低置信度提示，"
                            "非硬判职责越界" if generic_hint else "")
            is_definition = "keyword_definition" in signals
            kind_label = "定义名" if is_definition else "调用名"
            verdict = ("疑似职责越界" if is_definition
                       else "跨角色调用提示（弱信号，非硬判）")
            item = {
                "symbol": sym.name,
                "kind": sym.kind,
                "module": mod_path,
                "module_role_id": module_role_id,
                "module_role_name": module_role_name,
                "type": "definition" if is_definition else "call",
                "suspected_role_id": suspected_id,
                "suspected_role_name": suspected_name,
                "matched_keywords": matched,
                "signals": signals_out,
                "uncertainty": uncertainty,
                "confidence": confidence,
                "file_path": rel,
                "line": sym.line,
                "detail": (f"符号 {sym.name} 定义于 {rel}:{sym.line}（角色 "
                           f"{module_role_name or '未归属'}），其{kind_label}命中角色 "
                           f"{suspected_name}（{matched}），{verdict}{generic_tail}"),
            }
            if is_definition:
                if len(issues) < max_issues:
                    issues.append(item)
            elif len(hints) < max_issues:
                hints.append(item)
        # 仅当两个输出通道都达到各自上限才停：definition 满而 call_hints 未满时
        # 继续扫描以收齐调用提示，避免 definition 上限压掉后续 call 信号（CodeRabbit minor）
        if len(issues) >= max_issues and len(hints) >= max_issues:
            break

    # —— 语义判定（可选，缺 LLM 只给静态信号）——
    semantic_note = None
    if semantic and issues:
        semantic_note = _semantic_confirm(project_path, issues[:15])

    by_role: Dict[str, int] = {}
    for it in issues:
        rid = it["suspected_role_id"] or "?"
        by_role[rid] = by_role.get(rid, 0) + 1

    result["boundary_issues"] = issues
    result["call_hints"] = hints
    result["summary"] = {
        "modules_scanned": modules_scanned,
        "symbols_scanned": symbols_scanned,
        "boundary_issues": len(issues),
        "call_hints": len(hints),
        "by_role": by_role,
        "message": ("符号级职责越界：boundary_issues 仅报 definition 型真越界（顶层"
                    "类/函数定义名命中非本角色 role_keywords，且未命中本角色职责词"
                    "锚点、本角色关键词可匹配英文符号）；方法级撞词与跨角色调用是"
                    "分层协作常态，降级为独立 call_hints 弱信号（不占越界主输出）。"
                    "semantic=False 时仅静态信号（uncertainty=high），不硬阻断。"),
    }
    result["semantic_note"] = semantic_note
    result["ok"] = True
    return result


def _semantic_confirm(project_path: str, issues: List[Dict[str, Any]]):
    """可选语义判定接口：对 top 候选做语义确认。

    依赖 LLM；缺 API Key / LLM 不可用时返回 None（不硬阻断，保留静态信号）。
    """
    try:
        from core.llm_integration import get_llm_client
        client = get_llm_client()
    except Exception:  # noqa: BLE001
        logger.info("role_boundary: LLM 不可用，仅返回静态信号")
        return None
    if client is None:
        logger.info("role_boundary: 未配置 LLM API Key，仅返回静态信号")
        return None
    prompt = ("你是软件架构师。以下代码符号被判定为'职责越界'（定义于某角色的模块，"
              "但符号名命中另一角色的职责关键词）。请逐条判断：它是否确实越界（应在"
              "归属角色实现），还是可接受（命名歧义/工具函数/被授权调用）。只输出 "
              "JSON 数组，每项 {\"symbol\",\"adjudication\":\"越界|可接受|存疑\",\"reason\"}。\n"
              + json.dumps([{"symbol": i["symbol"], "module": i["module"],
                             "module_role": i["module_role_name"],
                             "suspected_role": i["suspected_role_name"],
                             "matched": i["matched_keywords"]} for i in issues],
                           ensure_ascii=False))
    try:
        resp = client.chat.completions.create(
            model=os.environ.get("CODEREF_MODEL", "deepseek-chat"),
            messages=[{"role": "user", "content": prompt}],
            temperature=0.0)
        out = resp.choices[0].message.content
        return {"enabled": True, "raw": out[:2000]}
    except Exception as e:  # noqa: BLE001
        logger.warning(f"role_boundary 语义判定失败: {e}")
        return {"enabled": True, "error": str(e)}