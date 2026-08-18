# -*- coding: utf-8 -*-
"""flow_verify 的 AST 静态信号扫描。

知识图谱 CALLS 边只能证明"谁调用了谁"，无法回答流程/参数/错误处理层面的问题：
- 异常是否被静默吞掉（错误处理质量）
- 辅助函数是否定义了却从未使用（死代码/字段混用线索）
- 调用点是否漏传关键尺寸/坐标参数（参数透传缺失）
- 批次目录命名是否一致（目录契约断裂）

全部基于 AST 静态分析，确定性、可复现，不依赖 LLM。
输出为 flow_verify 结果的 static_signals 字段（提示性信号，不置 ok=False）。
"""

import ast
import os
from typing import Dict, List, Optional, Set
import logging

logger = logging.getLogger(__name__)

# 日志/告警调用名（静默吞掉判定）
_LOG_CALLS = {"print", "log", "logger", "logging", "warn", "warning", "error",
              "exception", "critical", "info", "debug", "traceback"}
# 错误收集方法名：append/extend/add/insert 到列表/集合变量（如 errors.append）
_COLLECT_METHODS = {"append", "extend", "add", "insert"}
# 错误集合变量名前缀：调用名以这些前缀开头视为错误收集（保守识别，宁缺勿滥）
_COLLECT_NAME_PREFIXES = ("errors", "issues", "problems", "failures")
# 关键尺寸/坐标参数名提示（参数透传缺失判定）
_SIZE_PARAM_HINTS = ("canvas_", "width", "height", "logo_x", "logo_y",
                     "brand_text_x", "brand_text_y")
# 扫描时跳过的噪声目录
_SKIP_DIRS = {".git", ".venv", "venv", "__pycache__", "node_modules",
              ".tox", ".idea", "dist", "build", ".mypy_cache", ".pytest_cache"}


def _iter_py_files(project_path: str):
    for root, dirs, files in os.walk(project_path):
        dirs[:] = [d for d in dirs if d not in _SKIP_DIRS]
        for fn in files:
            if fn.endswith(".py"):
                yield os.path.join(root, fn)


def _parse(path: str):
    try:
        with open(path, "r", encoding="utf-8") as f:
            src = f.read()
    except (UnicodeDecodeError, OSError):
        try:
            with open(path, "r", encoding="gbk") as f:
                src = f.read()
        except Exception as e:
            # utf-8/gbk 双编码读取均失败，无法解析该文件
            logger.warning(f"读取源文件失败（双编码回退后），跳过解析 {path}: {e}")
            return None
    try:
        return ast.parse(src)
    except SyntaxError:
        # 语法错误文件无法解析，按无信号处理
        return None


def _rel(project_path: str, path: str) -> str:
    try:
        return os.path.relpath(path, project_path).replace("\\", "/")
    except Exception:
        return path.replace("\\", "/")


def _is_log_call(node) -> bool:
    """判断语句是否为日志/输出/告警调用。"""
    if isinstance(node, ast.Expr) and isinstance(node.value, ast.Call):
        f = node.value.func
        if isinstance(f, ast.Name):
            return f.id in _LOG_CALLS
        if isinstance(f, ast.Attribute):
            return f.attr in _LOG_CALLS
    return False


def _is_collect_call(node) -> bool:
    """判断语句是否为"错误收集"调用。

    两种保守模式（宁缺勿滥，避免把真静默吞掉误判为已收集）：
    1. 表达式调用 append/extend/add/insert 到某个列表/集合变量
       （如 errors.append(...) / self.errors.append(...)）；
    2. 调用名（函数名或方法名）以 errors/issues/problems/failures
       等错误集合名为前缀（如 collect_errors(...) / record_issues(...)）。
    """
    if not (isinstance(node, ast.Expr) and isinstance(node.value, ast.Call)):
        return False
    f = node.value.func
    if isinstance(f, ast.Attribute):
        # errors.append(...) / self.errors.extend(...)
        if f.attr in _COLLECT_METHODS:
            return True
        name = f.attr
    elif isinstance(f, ast.Name):
        name = f.id
    else:
        return False
    low = name.lower()
    return any(low.startswith(p) for p in _COLLECT_NAME_PREFIXES)


def detect_silent_except(tree, rel_path) -> List[dict]:
    """检测 except 块内无日志/无 raise/无 return 的静默吞掉。"""
    out: List[dict] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.ExceptHandler):
            continue
        body = list(node.body)
        if body and isinstance(body[-1], ast.Return):
            body = body[:-1]
        stmts = [s for s in body
                 if not isinstance(s, ast.Pass)
                 and not (isinstance(s, ast.Expr) and isinstance(s.value, ast.Constant)
                          and isinstance(s.value.value, str))]
        if not stmts:
            # 空体（仅 pass/docstring）是最典型的静默吞掉，直接报告
            exc_name = ""
            if node.type:
                if isinstance(node.type, ast.Name):
                    exc_name = node.type.id
                elif isinstance(node.type, ast.Attribute):
                    exc_name = node.type.attr
            out.append({
                "signal": "silent_except",
                "file": rel_path,
                "line": node.lineno,
                "exc": exc_name or "Exception",
                "detail": f"except {exc_name or 'Exception'} 块内仅 pass/docstring，异常被完全静默吞掉",
            })
            continue
        if any(_is_log_call(s) for s in stmts):
            continue
        if any(isinstance(s, ast.Raise) for s in stmts):
            continue
        exc_name = ""
        if node.type:
            if isinstance(node.type, ast.Name):
                exc_name = node.type.id
            elif isinstance(node.type, ast.Attribute):
                exc_name = node.type.attr
        # 错误收集模式：异常被 append/extend 等收进错误列表统一处理
        #（典型如 ci_compile_check 的 errors.append 决定 CI 退出码），
        # 错误信息并未丢失，仅缺显式日志 → 换文案，不再报"完全丢失"。
        if any(_is_collect_call(s) for s in stmts):
            out.append({
                "signal": "silent_except",
                "file": rel_path,
                "line": node.lineno,
                "exc": exc_name or "Exception",
                "detail": f"except {exc_name or 'Exception'} 块内 {len(stmts)} 条语句无日志/无 raise，"
                          f"异常未显式记录日志（已收集到错误列表统一处理）",
            })
            continue
        out.append({
            "signal": "silent_except",
            "file": rel_path,
            "line": node.lineno,
            "exc": exc_name or "Exception",
            "detail": f"except {exc_name or 'Exception'} 块内 {len(stmts)} 条语句无日志/无 raise，"
                      f"异常被静默吞掉，错误信息完全丢失",
        })
    return out


def _collect_called_names(tree) -> Set[str]:
    """收集文件内所有被调用的函数名（含 self.xxx / module.xxx 的 attr）。"""
    called: Set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            f = node.func
            if isinstance(f, ast.Name):
                called.add(f.id)
            elif isinstance(f, ast.Attribute):
                called.add(f.attr)
    return called


def detect_unused_helpers(tree, rel_path, called_global: Set[str]) -> List[dict]:
    """检测 _ 开头私有函数定义但从未被调用（跨文件调用名集合）。"""
    defined: List[tuple] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.name.startswith("_") and not node.name.startswith("__"):
                defined.append((node.name, node.lineno))
    out: List[dict] = []
    for name, line in defined:
        if name not in called_global:
            out.append({
                "signal": "unused_helper",
                "file": rel_path,
                "line": line,
                "func": name,
                "detail": f"私有辅助函数 {name} 定义后从未被调用（可能是字段混用/死代码，"
                          f"或调用点绕过了该函数）",
            })
    return out


def _call_name(node) -> Optional[str]:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def _is_size_param(name: str) -> bool:
    low = name.lower()
    return any(h in low for h in _SIZE_PARAM_HINTS)


def _sig_of(node) -> Dict[str, bool]:
    """提取函数/方法签名参数: name -> has_default。"""
    args = node.args
    params: Dict[str, bool] = {}
    n_defaults = len(args.defaults)
    pos_names = [a.arg for a in args.args]
    for i, name in enumerate(pos_names):
        params[name] = i >= len(pos_names) - n_defaults
    for a in args.kwonlyargs:
        params[a.arg] = True
    return params


def _collect_func_signatures(project_path: str) -> Dict[str, dict]:
    """跨文件收集项目内函数签名: 限定名 -> {params, file, line}。

    类方法用 `ClassName.method` 限定名，模块级函数用裸名，避免同名函数
    （不同类同名方法 / 类方法与模块函数同名）在签名表里互相覆盖，导致
    detect_missing_param_pass 用任意一个签名误判调用点。
    """
    sigs: Dict[str, dict] = {}
    for path in _iter_py_files(project_path):
        tree = _parse(path)
        if tree is None:
            continue
        rel = _rel(project_path, path)
        # 仅模块级 FunctionDef/AsyncFunctionDef 用裸名；类方法用 `Class.method`
        # 限定名；嵌套函数不注册（避免以裸名覆盖模块级同名函数）
        for node in tree.body:
            if isinstance(node, ast.ClassDef):
                for sub in node.body:
                    if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        key = f"{node.name}.{sub.name}"
                        sigs.setdefault(key, {"params": _sig_of(sub),
                                              "file": rel, "line": sub.lineno})
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                sigs.setdefault(node.name, {"params": _sig_of(node),
                                            "file": rel, "line": node.lineno})
    return sigs


def _iter_with_owner(node, owner=None):
    """深度优先遍历，为每个节点附带其所属类名（位于类内时）。"""
    for child in ast.iter_child_nodes(node):
        cur = owner
        if isinstance(child, ast.ClassDef):
            cur = child.name
        yield child, cur
        yield from _iter_with_owner(child, cur)


def _call_keys(node, owner) -> List[str]:
    """把调用目标解析为签名表限定名候选（限定名优先，裸名兜底）。

    限定名既避免同名覆盖误配，又通过裸名兜底保留对模块级/裸函数调用的命中，
    防止把 `convert_text_layers(...)` 这类非 self 调用漏检。
    """
    f = node.func
    if isinstance(f, ast.Name):
        return [f.id]
    if isinstance(f, ast.Attribute):
        cands: List[str] = []
        if isinstance(f.value, ast.Name) and f.value.id == "self" and owner:
            cands.append(f"{owner}.{f.attr}")
        elif isinstance(f.value, ast.Name):
            cands.append(f"{f.value.id}.{f.attr}")
        cands.append(f.attr)
        return cands
    return []


def detect_missing_param_pass(tree, rel_path, sigs: Dict[str, dict]) -> List[dict]:
    """检测调用点未传关键尺寸/坐标参数（被调函数签名有带默认值的该参数）。"""
    out: List[dict] = []
    for node, owner in _iter_with_owner(tree):
        if not isinstance(node, ast.Call):
            continue
        # 用限定名在签名表里定位，miss 时跳过该调用（避免用任意同名签名误判）
        params = None
        callee = None
        for cand in _call_keys(node, owner):
            if cand in sigs:
                params = sigs[cand]["params"]
                callee = cand
                break
        if params is None:
            continue
        names = list(params.keys())
        # 方法调用（限定名命中，如 self.render(width, height)）时第一个形参是
        # self/cls，不占用实参位；非方法函数保持原映射
        if callee and "." in callee and names and names[0] in ("self", "cls"):
            names = names[1:]
        passed: Set[str] = set()
        for i in range(len(node.args)):
            if i < len(names):
                passed.add(names[i])
        for kw in node.keywords:
            if kw.arg:
                passed.add(kw.arg)
        for pname, has_default in params.items():
            if not has_default or pname in passed:
                continue
            if not _is_size_param(pname):
                continue
            out.append({
                "signal": "missing_param_pass",
                "file": rel_path,
                "line": node.lineno,
                "callee": callee,
                "param": pname,
                "detail": f"调用 {callee} 未传关键尺寸/坐标参数 {pname}（回落默认值，"
                          f"非默认画布/坐标下定位可能错误）",
            })
    return out


def _is_test_file(rel_path: str) -> bool:
    """判断文件是否属于测试代码（unused_helper 统计生产调用时排除）。"""
    low = rel_path.lower()
    if "/tests/" in low or low.startswith("tests/"):
        return True
    base = os.path.basename(low)
    return base.startswith("test_") or base.endswith("_test.py")


def _collect_dir_templates(tree, rel_path) -> List[dict]:
    """收集批次目录/输出路径拼接模板（f-string 或 strftime 变量）。"""
    out: List[dict] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.JoinedStr):
            parts = []
            for v in node.values:
                if isinstance(v, ast.FormattedValue):
                    parts.append("{" + _expr_name(v.value) + "}")
                elif isinstance(v, ast.Constant):
                    parts.append(str(v.value))
            s = "".join(parts)
            if any(k in s for k in ("{ts", "{batch", "{slug", "{stem")):
                out.append({"file": rel_path, "line": node.lineno, "template": s})
    return out


def _expr_name(node) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return _expr_name(node.value) + "." + node.attr
    if isinstance(node, ast.Call):
        return _call_name(node.func) or "?"
    return "?"


def detect_dir_contract_break(project_path: str) -> List[dict]:
    """跨文件检测批次目录命名不一致（目录契约断裂）。

    收集所有批次目录/输出路径拼接模板（f-string 含 {ts}/{batch}/{slug}），
    若同时存在"含 slug 后缀"与"不含 slug"两种模板，报告目录契约断裂——
    上游按 {ts}_{slug} 建目录、下游按 {batch_id} 拼路径时必然指向不存在的目录。
    """
    templates: List[dict] = []
    for path in _iter_py_files(project_path):
        tree = _parse(path)
        if tree is None:
            continue
        templates.extend(_collect_dir_templates(tree, _rel(project_path, path)))
    has_slug = [t for t in templates if "{slug" in t["template"]]
    no_slug = [t for t in templates if "{slug" not in t["template"]]
    if not has_slug or not no_slug:
        return []
    out: List[dict] = []
    for t in has_slug[:3]:
        out.append({
            "signal": "dir_contract_break",
            "file": t["file"],
            "line": t["line"],
            "template": t["template"],
            "detail": f"批次目录按模板 {t['template']} 创建（含 slug），"
                      f"而另一处按 {no_slug[0]['template']} 拼接（不含 slug），"
                      f"两套目录命名不一致，manifest 的 psd_path/output 字段可能指向不存在的批次目录",
        })
    return out


def scan_project(project_path: str) -> List[dict]:
    """扫描项目内所有 Python 文件，返回 static_signals 列表（稳定排序）。"""
    signals: List[dict] = []
    sigs = _collect_func_signatures(project_path)
    called_global: Set[str] = set()
    trees: List[tuple] = []
    for path in _iter_py_files(project_path):
        tree = _parse(path)
        if tree is None:
            continue
        rel = _rel(project_path, path)
        trees.append((tree, rel))
        # 生产调用集合排除测试文件：辅助函数仅被测试引用时仍属"生产未使用"
        if not _is_test_file(rel):
            called_global |= _collect_called_names(tree)
    for tree, rel in trees:
        signals.extend(detect_silent_except(tree, rel))
        signals.extend(detect_unused_helpers(tree, rel, called_global))
        signals.extend(detect_missing_param_pass(tree, rel, sigs))
    signals.extend(detect_dir_contract_break(project_path))
    signals.sort(key=lambda s: (s["file"], s["line"], s["signal"]))
    return signals
