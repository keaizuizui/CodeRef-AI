# -*- coding: utf-8 -*-
"""S24 测试套件质量自审（借 brooks-test 视角，2026-11-13 固化）。

审计对象 = **测试用例库自身**（`独立测试\\tests`），不是 CodeRef 产品；
判据全部为 AST 静态分析，确定性、零外部依赖：

1. 无空测试：不存在只有 `pass` / 空体的 `test_*` 方法；
2. 断言语义：每个 `test_*` 方法至少 1 个 `self.assert*`（含 assertRaises/fail）；
3. 隔离性：使用 `tempfile.mkdtemp` / `TemporaryDirectory` 的用例文件占比 ≥ 60%
   （防写死路径导致跨用例污染）；
4. 命名规范：`test_*` 方法名长度 ≥ 8 且不得是 `test1` / `test_a` 这类无语义名；
5. 无固定外部依赖：默认回归（不设环境变量）不得直接引用真实大仓路径
   （如 kuajingdianshang 样例仓）；真实路径只能作为 `os.environ.get(...)` 的默认值
   且模块有 skip 门控；
6. skip 可解释：所有 skip / skipIf / skipUnless / skipTest 必须带非空 reason
   （字面量或可解析的模块级字符串常量）；
7. 无重复用例名：同一文件内 `test_*` 方法不得重名（Python 后者覆盖前者 = 静默丢用例），
   且全库同名方法需人工确认后登记。

运行：
    cd coderef-src
    python -m unittest discover -s "..\\独立测试\\tests" -p "test_suite_selfaudit.py" -v
"""
import ast
import glob
import os
import re
import unittest

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))

# 真实项目路径：默认回归禁止直接依赖（只能走环境变量 + skip）
_REAL_PATH_RE = re.compile(
    r"[A-Za-z]:[\\/]{1,2}(?:kuajingdianshang|测试用例|CodeRef-Ai-master)",
    re.IGNORECASE,
)
_SKIP_DECORATORS = {"skip", "skipIf", "skipUnless"}
_SHORT_NAME_RE = re.compile(r"^test_?\d+$|^test_[a-z]$")
_MIN_TMP_RATIO = 0.60
_MIN_NAME_LEN = 8


def _test_files():
    """测试仓顶层的用例文件（不递归 fixtures / __pycache__）。"""
    return sorted(glob.glob(os.path.join(TESTS_DIR, "test_*.py")))


def _parse(path):
    with open(path, "r", encoding="utf-8") as f:
        src = f.read()
    return src, ast.parse(src, filename=path)


def _iter_test_methods(tree):
    """产出 (所属类, 独立回归法) —— 只认类内 `test_*`（unittest 口径）。"""
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        for item in node.body:
            if (isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))
                    and item.name.startswith("test")):
                yield node, item


def _docstring_const_ids(tree):
    """模块/类/函数 docstring 的常量节点 id（文档示例里的路径不算硬依赖）。"""
    ids = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = getattr(node, "body", [])
            if (body and isinstance(body[0], ast.Expr)
                    and isinstance(body[0].value, ast.Constant)
                    and isinstance(body[0].value.value, str)):
                ids.add(id(body[0].value))
    return ids


def _const_text(node, consts):
    """把字符串表达式尽量静态解析成文本；解析不出返回空串。"""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.Name):
        return consts.get(node.id, "")
    if isinstance(node, ast.JoinedStr):
        return "".join(
            _const_text(v, consts) for v in node.values if isinstance(v, ast.Constant))
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Mod):
        left = _const_text(node.left, consts)
        right = _const_text(node.right, consts)
        for token in ("%s", "%d", "%r"):
            left = left.replace(token, right)
        return left
    if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
            and node.func.attr == "format"):
        return _const_text(node.func.value, consts)
    return ""


def _module_str_consts(tree):
    """模块级字符串常量名 → 值（支持拼接/格式化模板，用于解析 skip reason 常量名）。"""
    raw = {}
    for node in tree.body:
        if isinstance(node, ast.Assign):
            targets, value = node.targets, node.value
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            targets, value = [node.target], node.value
        else:
            continue
        for t in targets:
            if isinstance(t, ast.Name):
                raw[t.id] = value
    consts = {k: v.value for k, v in raw.items()
              if isinstance(v, ast.Constant) and isinstance(v.value, str)}
    for _ in range(3):   # 解析引用其它常量的模板（防循环引用，限轮次）
        for k, v in raw.items():
            if k in consts:
                continue
            text = _const_text(v, consts)
            if text:
                consts[k] = text
    return consts


def _decorator_name(dec):
    """装饰器名字：`unittest.skip` → skip；`skip` → skip。"""
    func = dec.func if isinstance(dec, ast.Call) else dec
    if isinstance(func, ast.Attribute):
        return func.attr
    if isinstance(func, ast.Name):
        return func.id
    return ""


def _reason_text(expr, module_consts):
    """把 skip reason 表达式解析成文本；无法静态解析返回空串。"""
    return (_const_text(expr, module_consts) or "").strip()


def _call_reason(call, module_consts):
    """skip/skipIf/skipUnless/skipTest 的 reason 参数。"""
    name = _decorator_name(call)
    for kw in call.keywords:
        if kw.arg == "reason":
            return _reason_text(kw.value, module_consts)
    idx = 0 if name in ("skip", "skipTest") else 1
    if len(call.args) > idx:
        return _reason_text(call.args[idx], module_consts)
    return ""


def _has_assertion(fn):
    for node in ast.walk(fn):
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Attribute) and (
                    func.attr.startswith("assert") or func.attr == "fail"):
                return True
    return False


class TestSuiteSelfAudit(unittest.TestCase):
    """测试用例库自身质量：7 条结构性判据。"""

    @classmethod
    def setUpClass(cls):
        cls.files = _test_files()
        assert cls.files, f"未找到任何用例文件: {TESTS_DIR}"
        cls.parsed = {p: _parse(p) for p in cls.files}

    # ── 1. 无空测试 ─────────────────────────────────────────────
    def test_no_empty_test_methods(self):
        problems = []
        for path, (_, tree) in self.parsed.items():
            for cls, fn in _iter_test_methods(tree):
                body = [b for b in fn.body
                        if not (isinstance(b, ast.Expr)
                                and isinstance(b.value, ast.Constant)
                                and isinstance(b.value.value, str))]
                if not body or all(isinstance(b, ast.Pass) for b in body):
                    problems.append(
                        f"{os.path.basename(path)}::{cls.name}.{fn.name}:{fn.lineno}")
        self.assertEqual(
            problems, [],
            "存在空测试（空体或只有 pass，等于没测）:\n" + "\n".join(problems))

    # ── 2. 断言语义 ─────────────────────────────────────────────
    def test_every_test_has_assertion(self):
        problems = []
        for path, (_, tree) in self.parsed.items():
            for cls, fn in _iter_test_methods(tree):
                if _has_assertion(fn):
                    continue
                if any(isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                       and n.func.attr == "skipTest" for n in ast.walk(fn)):
                    continue
                problems.append(
                    f"{os.path.basename(path)}::{cls.name}.{fn.name}:{fn.lineno}")
        self.assertEqual(
            problems, [],
            "独立回归法无任何 self.assert*（无法判定通过/失败）:\n" + "\n".join(problems))

    # ── 3. 隔离性 ───────────────────────────────────────────────
    def test_isolation_uses_tempfile(self):
        isolated = []
        for path in self.files:
            src = self.parsed[path][0]
            if "tempfile" in src and ("mkdtemp" in src or "TemporaryDirectory" in src):
                isolated.append(path)
        ratio = len(isolated) / len(self.files)
        non_isolated = [os.path.basename(p) for p in self.files if p not in isolated]
        self.assertGreaterEqual(
            ratio, _MIN_TMP_RATIO,
            f"隔离性不足：仅 {len(isolated)}/{len(self.files)} = {ratio:.1%} 使用 tempfile "
            f"(判据 ≥ {_MIN_TMP_RATIO:.0%})；未隔离文件: {non_isolated}")

    # ── 4. 命名规范 ─────────────────────────────────────────────
    def test_test_method_names_are_descriptive(self):
        problems = []
        for path, (_, tree) in self.parsed.items():
            for cls, fn in _iter_test_methods(tree):
                bad = len(fn.name) < _MIN_NAME_LEN or _SHORT_NAME_RE.match(fn.name)
                if bad:
                    problems.append(
                        f"{os.path.basename(path)}::{cls.name}.{fn.name} "
                        f"(长度 {len(fn.name)}):{fn.lineno}")
        self.assertEqual(
            problems, [],
            "方法名无法表达意图（须 ≥ 8 字符且非 test1/test_a）:\n" + "\n".join(problems))

    # ── 5. 无固定外部依赖 ───────────────────────────────────────
    def test_no_hardcoded_real_project_paths_by_default(self):
        problems = []
        for path, (_, tree) in self.parsed.items():
            name = os.path.basename(path)
            docstrings = _docstring_const_ids(tree)
            guarded_ids = set()
            for node in ast.walk(tree):
                if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                        and node.func.attr == "get"
                        and isinstance(node.func.value, ast.Attribute)
                        and node.func.value.attr == "environ"):
                    for arg in node.args[1:]:
                        for sub in ast.walk(arg):
                            guarded_ids.add(id(sub))
            bare = []
            for node in ast.walk(tree):
                if (isinstance(node, ast.Constant) and isinstance(node.value, str)
                        and id(node) not in docstrings
                        and _REAL_PATH_RE.search(node.value)):
                    if id(node) not in guarded_ids:
                        bare.append((node.lineno, node.value[:80]))
            failures = [f"{name}:{lineno} 直接引用真实路径 {val!r}"
                        for lineno, val in bare]
            problems.extend(failures)
            # 真实路径作为 env 默认值时，模块必须有 skip 门控（否则默认就跑真实项目）
            has_guarded_path = any(
                isinstance(node, ast.Constant) and isinstance(node.value, str)
                and id(node) in guarded_ids and _REAL_PATH_RE.search(node.value)
                for node in ast.walk(tree))
            has_skip = any(
                _decorator_name(dec) in _SKIP_DECORATORS
                for node in ast.walk(tree)
                if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
                for dec in node.decorator_list)
            if has_guarded_path and not has_skip:
                problems.append(f"{name}: 引用真实路径默认值但无 skip 门控")
        self.assertEqual(
            problems, [],
            "默认回归依赖固定外部路径（应走环境变量 + skip）:\n" + "\n".join(problems))

    # ── 6. skip 可解释 ──────────────────────────────────────────
    def test_all_skips_have_explanatory_reason(self):
        problems = []
        for path, (_, tree) in self.parsed.items():
            name = os.path.basename(path)
            consts = _module_str_consts(tree)
            for node in ast.walk(tree):
                if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                    for dec in node.decorator_list:
                        if isinstance(dec, ast.Call) and _decorator_name(dec) in _SKIP_DECORATORS:
                            if not _call_reason(dec, consts):
                                problems.append(
                                    f"{name}:{dec.lineno} {_decorator_name(dec)} 缺非空 reason")
                if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                        and node.func.attr == "skipTest"):
                    if not _call_reason(node, consts):
                        problems.append(f"{name}:{node.lineno} skipTest 缺非空 reason")
        self.assertEqual(
            problems, [],
            "skip 无解释（SKIP 必须写明原因，便于判断是否长期失效）:\n" + "\n".join(problems))

    # ── 7. 无重复用例名 ─────────────────────────────────────────
    def test_no_duplicate_test_method_names(self):
        per_file = {}
        per_name = {}
        for path, (_, tree) in self.parsed.items():
            name = os.path.basename(path)
            for cls, fn in _iter_test_methods(tree):
                per_file.setdefault((name, fn.name), []).append(f"{cls.name}:{fn.lineno}")
                per_name.setdefault(fn.name, []).append(name)
        dup_in_file = [f"{name}::{method} 重复定义于 {locs}"
                       for (name, method), locs in per_file.items() if len(locs) > 1]
        dup_cross = [f"{method} 出现于 {sorted(set(files))}"
                     for method, files in per_name.items() if len(files) > 1]
        self.assertEqual(
            dup_in_file, [],
            "同一文件内独立回归法重名（后者覆盖前者，用例被静默丢弃）:\n" + "\n".join(dup_in_file))
        self.assertEqual(
            dup_cross, [],
            "全库独立回归法重名（无法按名字定位失败用例，需人工登记）:\n" + "\n".join(dup_cross))


if __name__ == "__main__":
    unittest.main(verbosity=2)
