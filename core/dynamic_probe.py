# -*- coding: utf-8 -*-
"""
DynamicProbe v1.0 —— CodeRef 5.2 动态探针（Phase 3）

补全静态图谱盲区：挖掘"静态 AST 看不到、运行时才成立"的动态信号，
但**默认零执行**——不 import、不 subprocess 被检项目代码，纯 AST 静态提取，
防止副作用/危险改库。输出供人工核对或作为可选边并入后续分析，不直接污染
知识图谱重建产物。

动态信号类型：
  dynamic_imports  动态导入（importlib.import_module / __import__ 的参数字面量）
  registrations    装饰器注册（@app.route('...') / @registry.register('...')）
  indirect_lookups 间接索引（getattr(m, 'name') / globals()['name'] 字面量）
  entry_points     setuptools entry_points / console_scripts 里的模块路径
"""

import ast
import os
from typing import Any, Dict, List

from loguru import logger

# tomllib 为 Python 3.11+ 标准库；3.10 及以下用 tomli（若已安装），否则降级跳过
try:
    import tomllib
except ImportError:  # pragma: no cover - Python < 3.11
    try:
        import tomli as tomllib
    except ImportError:
        tomllib = None

# 会被当成动态导入/注册/索引的典型符号（命中即高置信信号）
_IMPORT_FUNCS = {"import_module", "__import__", "importlib.import_module"}
_REGISTRY_HINT = {"register", "route", "add_url_rule", "handler", "add_task",
                  "listen", "on", "bind"}
_LOOKUP_HINTS = {"getattr", "globals", "locals", "vars"}

# 默认排除目录（与仓库惯例一致，避免扫描依赖/虚拟环境/测试）
_IGNORE_DIRS = {".venv", "venv", "node_modules", ".git", "__pycache__",
                "site-packages", "dist", "build", ".coderef", "tests"}


def _rel_path(root: str, path: str) -> str:
    return os.path.relpath(path, root).replace("\\", "/")


def _collect_py_files(root: str) -> List[str]:
    files = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in _IGNORE_DIRS]
        for fn in filenames:
            if fn.endswith(".py") and not fn.startswith("test_"):
                files.append(os.path.join(dirpath, fn))
    return files


class _Visitor(ast.NodeVisitor):
    """AST 遍历器：提取动态信号（纯静态，不执行）。"""

    def __init__(self):
        self.imports: List[dict] = []
        self.registrations: List[dict] = []
        self.indirect: List[dict] = []

    def _arg_literal(self, node) -> str:
        """取 AST 节点的字符串常量（仅字面量，避免求值）。"""
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return node.value
        if isinstance(node, ast.JoinedStr):  # f-string —— 动态，标注为不确定
            return ""
        return ""

    def visit_Call(self, node: ast.Call):
        self.generic_visit(node)
        name = ""
        dotted = ""
        if isinstance(node.func, ast.Name):
            name = node.func.id
            dotted = name
        elif isinstance(node.func, ast.Attribute):
            dotted = self._dotted(node.func)
            name = node.func.attr
        # 动态导入
        if name in _IMPORT_FUNCS or "import_module" in dotted:
            arg = self._arg_literal(node.args[0]) if node.args else ""
            if arg:
                self.imports.append({
                    "file": "", "module_ref": dotted, "target": arg,
                    "line": getattr(node, "lineno", 0)})
        # 装饰器式注册（Call 的 func 是含 string 常量的调用）
        if name in _REGISTRY_HINT:
            targets = [self._arg_literal(a)
                       for a in node.args if isinstance(a, ast.Constant)]
            targets = [t for t in targets if t]
            if targets:
                self.registrations.append({
                    "file": "", "module_ref": dotted, "targets": targets,
                    "line": getattr(node, "lineno", 0)})
        # 间接索引
        if name in _LOOKUP_HINTS and node.args:
            arg = self._arg_literal(node.args[0]) if node.args else ""
            if name == "getattr" and len(node.args) >= 2:
                arg = self._arg_literal(node.args[1]) if len(node.args) > 1 else ""
            if arg:
                self.indirect.append({
                    "file": "", "module_ref": dotted, "symbol": arg,
                    "line": getattr(node, "lineno", 0)})

    def _dotted(self, n) -> str:
        if isinstance(n, ast.Name):
            return n.id
        if isinstance(n, ast.Attribute):
            base = self._dotted(n.value)
            return f"{base}.{n.attr}" if base else n.attr
        return ""


def _scan_entry_points(root: str) -> List[dict]:
    """从 setup.py / pyproject.toml 提取 entry_points 里的模块路径。"""
    out = []
    pyproject = os.path.join(root, "pyproject.toml")
    if os.path.isfile(pyproject) and tomllib is not None:
        try:
            with open(pyproject, "rb") as f:
                data = tomllib.load(f)
        except Exception:  # noqa: BLE001
            data = {}
        eps = (data.get("project", {}) or {}).get("entry-points", {}) or {}
        for group, entries in eps.items():
            if not isinstance(entries, dict):
                continue
            for key, val in entries.items():
                out.append({"group": group, "name": key, "module_path": str(val)})
    setup = os.path.join(root, "setup.py")
    if os.path.isfile(setup):
        try:
            tree = ast.parse(open(setup, encoding="utf-8").read())
        except Exception:  # noqa: BLE001
            tree = None
        if tree:
            for kw in ast.walk(tree):
                if not (isinstance(kw, ast.keyword)
                        and kw.arg in ("entry_points", "console_scripts")):
                    continue
                # 尽量提取 dict 字面量里的 module 字符串（模糊匹配 . 分隔）
                if isinstance(kw.value, ast.Dict):
                    for v in kw.value.values:
                        if isinstance(v, ast.Constant) and isinstance(v.value, str):
                            out.append({"module_path": v.value})
    return out


def probe(project_path: str, include_tests: bool = False) -> Dict[str, Any]:
    """动态探针主入口（静态层，零执行）。"""
    root = os.path.abspath(project_path)
    files = _collect_py_files(root)
    vis = _Visitor()
    imports, regs, indirect = [], [], []
    for f in files:
        try:
            src = open(f, encoding="utf-8", errors="replace").read()
            tree = ast.parse(src)
        except Exception:  # noqa: BLE001
            continue
        # 记录当前文件相对路径，供各 visit 结果填充
        rel = _rel_path(root, f)
        v = _Visitor()
        try:
            v.visit(tree)
        except Exception:  # noqa: BLE001
            continue
        for it in v.imports:
            it["file"] = rel
            imports.append(it)
        for rg in v.registrations:
            rg["file"] = rel
            regs.append(rg)
        for ind in v.indirect:
            ind["file"] = rel
            indirect.append(ind)

    entries = _scan_entry_points(root)

    hints = [f"{it['file']}:{it['line']} 动态导入 {it['module_ref']} -> {it['target']}"
             for it in imports[:50]]
    return {
        "ok": True,
        "project_path": root,
        "tool": "coderef_dynamic_probe",
        "executed": False,  # 显式声明零执行
        "files": len(files),
        "counts": {
            "dynamic_imports": len(imports),
            "registrations": len(regs),
            "indirect_lookups": len(indirect),
            "entry_points": len(entries),
        },
        "dynamic_imports": imports[:200],
        "registrations": regs[:200],
        "indirect_lookups": indirect[:200],
        "entry_points": entries[:200],
        "hints": hints,
        "summary": {
            "total": len(imports) + len(regs) + len(indirect) + len(entries),
            "message": ("静态层动态信号（零执行）。可按需人工核对后作为额外边"
                        "参与分析；默认不写入知识图谱。"),
        },
    }


def analyze_gap(project_path: str, target_arch: Dict[str, Any],
                max_unassigned: int = 50, db_path: str = None) -> dict:
    """供差距分析器可选复用：把动态信号合成额外差距的薄封装。"""
    from core.arch_gap_analyzer import analyze_gap as _base
    return _base(project_path, target_arch, max_unassigned=max_unassigned,
                 db_path=db_path)