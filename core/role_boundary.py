# -*- coding: utf-8 -*-
"""
role_boundary —— 符号级职责越界检测（5.2 升格实现，解决 5.0 §6.2 遗留）

背景：模块归属（arch_gap_analyzer）正确，不代表模块内每个符号都守住了自己
角色的边界。典型场景：`waiter.py`（角色 waiter）里定义/调用了本属 `chef`
职责的 `cook()`。本模块把"符号级职责越界"做成确定性、可复现的静态检测。

检测原理（确定性，零 LLM）：
  1. 模块 → 角色归属：按目标架构 tech_roles.target_modules 精确/basename 匹配。
  2. 命名语义（definition 越界）：在归属角色 R 的模块 m 里，符号名（含类内方法）
     命中"其他角色 X（X≠R）"的 role_keywords → 该符号疑似本属角色 X，却在 R 的
     模块里实现。
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
    return [t.lower() for t in re.split(r"[^a-z0-9]+", s) if t]


def _keyword_tokens(kw: str) -> List[str]:
    """把 role_keywords 拆成 token（支持 'handle_order' / 'prepare dish' 写法）。"""
    return _tokens(kw)


def _hits_keyword(sym_tokens: List[str], kw_tokens: List[str]) -> bool:
    """判断符号 token 是否命中角色关键词 token。

    命中规则（防误报，要求足够明确的词根）：
      - 关键词首 token 与符号某 token 相等；或
      - 关键词首 token 长于等于 3 个字符且为符号某 token 的前缀（cook→cooking）。
    """
    if not sym_tokens or not kw_tokens:
        return False
    head = kw_tokens[0]
    return any((t == head) or (len(head) >= 3 and t.startswith(head))
               for t in sym_tokens)


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
    # basename 宽松匹配兜底
    for spec, role in spec_map.items():
        if spec.split("/")[-1] == base:
            return role["id"], role["name"]
    return "", ""


def _match_suspected(sym_tokens: List[str], roles: List[Dict[str, Any]],
                     module_role_id: str) -> List[Tuple[str, Dict[str, Any], List[str]]]:
    """返回 (疑似角色id, 角色, 命中的关键词列表)。module_role 已剔除。"""
    hits = []
    for role in roles:
        rid = role["id"]
        if rid == module_role_id:        # 本角色自己的关键词不算越界
            continue
        kw_matched = []
        for kw_tokens in role["kw_tokens"]:
            if _hits_keyword(sym_tokens, kw_tokens):
                kw_matched.append(kw_tokens[0])
        if kw_matched:
            hits.append((rid, role, kw_matched[:3]))
    return hits


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
                    "boundary_issues": 0, "by_role": {}},
        "boundary_issues": [],
        "semantic_note": None,
    }
    if not db or not os.path.exists(db):
        result["summary"]["message"] = ("知识图谱不存在，需先构建"
                                        "（coderef_audit / coderef_memory_sync）")
        return result
    result["has_kg"] = True

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
        syms = _scan_symbols(tree)
        if not syms:
            continue
        modules_scanned += 1
        for sym in syms:
            symbols_scanned += 1
            # definition 越界：符号定义名命中其他角色关键词
            def_hits = _match_suspected(sym.keywords_defined, roles,
                                        module_role_id)
            # call 越界提示：符号体内调用名命中其他角色关键词
            call_hits = _match_suspected(list(sym.keywords_called), roles,
                                         module_role_id)
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
            issue = {
                "symbol": sym.name,
                "kind": sym.kind,
                "module": mod_path,
                "module_role_id": module_role_id,
                "module_role_name": module_role_name or "UNASSIGNED",
                "type": ("definition" if "keyword_definition" in signals
                         else "call"),
                "suspected_role_id": suspected_id,
                "suspected_role_name": suspected_name,
                "matched_keywords": matched,
                "signals": signals,
                "uncertainty": "high" if not semantic else "medium",
                "file_path": rel,
                "line": sym.line,
                "detail": (f"符号 {sym.name} 定义于 {rel}:{sym.line}（角色 "
                           f"{module_role_name or '未归属'}），其职责关键词命中角色 "
                           f"{suspected_name}（{matched}），疑似职责越界"),
            }
            if len(issues) >= max_issues:
                break
            issues.append(issue)
        if len(issues) >= max_issues:
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
    result["summary"] = {
        "modules_scanned": modules_scanned,
        "symbols_scanned": symbols_scanned,
        "boundary_issues": len(issues),
        "by_role": by_role,
        "message": ("符号级职责越界：符号定义/调用名命中非本模块角色的 "
                    "role_keywords 即报出；semantic=False 时仅静态信号（"
                    "uncertainty=high），不硬阻断。"),
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