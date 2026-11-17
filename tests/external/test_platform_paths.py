# -*- coding: utf-8 -*-
"""跨平台路径逻辑（纯函数级验证，不实际启动 Linux）。

覆盖：
- Windows 语义：大小写 / 分隔符 / 尾部斜杠等价，且归一结果与 os.path.normcase 一致
- 相对路径、UNC 路径归一
- 静态扫描：core/*.py 不用 `"\\\\" + x` 拼路径（应 os.path.join）
- 平台自适应：把 os.path.normcase 换成恒等函数（模拟 Linux）后大小写敏感
- 幂等：project_key 多次归一结果不变
- 已知缺陷：`~` 未被展开（expectedFailure）
"""
import ast
import glob
import os
import sys
import unittest
from unittest import mock

PROJECT_ROOT = os.environ.get("CODEREF_SRC",
                              os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from core.path_key import project_key  # noqa: E402

CORE_DIR = os.path.join(PROJECT_ROOT, "core")
IS_WINDOWS = os.name == "nt"


def _backslash_sep_concat_hits():
    """core/*.py 里 `常量(单个反斜杠) + x` / `x + 常量` 的位置（路径分隔符硬编码）。"""
    hits = []
    for fp in sorted(glob.glob(os.path.join(CORE_DIR, "*.py"))):
        with open(fp, "r", encoding="utf-8", errors="replace") as f:
            tree = ast.parse(f.read(), filename=fp)
        for node in ast.walk(tree):
            if not (isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add)):
                continue
            for side in (node.left, node.right):
                if isinstance(side, ast.Constant) and side.value == "\\":
                    hits.append("%s:%d" % (os.path.basename(fp), node.lineno))
    return hits


def _join_call_count():
    total = 0
    for fp in glob.glob(os.path.join(CORE_DIR, "*.py")):
        with open(fp, "r", encoding="utf-8", errors="replace") as f:
            total += f.read().count("os.path.join(")
    return total


class WindowsPathKeyTest(unittest.TestCase):

    @unittest.skipUnless(IS_WINDOWS, "Windows 专有语义")
    def test_case_and_separator_equivalent(self):
        self.assertEqual(project_key(r"D:\a\b"), project_key("d:/a/b"),
                         "同一项目的盘符大小写 / 分隔符变体必须归一为同一 key")
        self.assertEqual(project_key(r"D:\a\b"), project_key("D:\\A\\B"),
                         "路径大小写变体必须归一为同一 key")
        self.assertEqual(project_key(r"D:\a\b"),
                         os.path.normcase(os.path.abspath(r"D:\a\b")),
                         "归一语义必须等于 normcase(abspath(·))，而非另造规则")
        self.assertEqual(project_key(r"D:\a\b\..\b"), project_key(r"D:\a\b"),
                         "冗余 .. 段必须折叠")

    def test_relative_and_trailing_slash_normalized(self):
        cwd_pkg = os.path.join(os.getcwd(), "pkg")
        self.assertEqual(project_key("pkg"), project_key(cwd_pkg),
                         "相对路径必须按 cwd 绝对化")
        self.assertEqual(project_key("./pkg/"), project_key(cwd_pkg),
                         "尾部斜杠与 ./ 前缀必须归一")
        self.assertEqual(project_key("."), project_key(os.getcwd()))
        self.assertEqual(project_key(cwd_pkg + os.sep), project_key(cwd_pkg),
                         "尾部分隔符必须归一")

    @unittest.skipUnless(IS_WINDOWS, "UNC 仅 Windows 有意义")
    def test_unc_path_normalized(self):
        a = project_key(r"\\server\share\proj")
        b = project_key(r"\\SERVER\Share\Proj")
        self.assertEqual(a, b, "UNC 路径大小写变体必须归一为同一 key")
        self.assertTrue(a.startswith("\\\\server\\share"),
                        f"UNC 双前缀必须保留: {a!r}")
        self.assertEqual(a, os.path.normcase(r"\\server\share\proj"),
                         "UNC 归一结果必须与 normcase 语义一致")

    def test_tilde_expands_to_home(self):
        # TODO-U: project_key 只做 abspath+normcase，不展开 `~`；`~/proj` 被
        # 错位成 <cwd>/~/proj，与真实 $HOME/proj 产生两个不同 key（同一项目分裂）。
        expected = project_key(os.path.join(os.path.expanduser("~"), "proj"))
        self.assertEqual(project_key("~/proj"), expected)


class PlatformAdaptiveTest(unittest.TestCase):

    def test_normcase_identity_makes_key_case_sensitive(self):
        """把 normcase 换成恒等函数（Linux 语义）后，大小写必须敏感。"""
        with mock.patch("os.path.normcase", new=lambda p: p):
            self.assertNotEqual(project_key("A"), project_key("a"),
                                "key 的大小写归一必须来自 os.path.normcase，"
                                "而不是硬编码 .lower()")
            self.assertEqual(project_key("A"), os.path.abspath("A"),
                             "无 normcase 时应退化为纯 abspath")

    def test_idempotent(self):
        cases = [r"D:\a\b", "d:/a/b", ".", "pkg", r"\\server\share\proj", ""]
        for p in cases:
            with self.subTest(path=p):
                once = project_key(p)
                self.assertEqual(project_key(once), once,
                                 f"{p!r} 二次归一必须幂等")


class NoHardcodedPathSeparatorTest(unittest.TestCase):

    def test_no_hardcoded_backslash_concat(self):
        hits = _backslash_sep_concat_hits()
        self.assertEqual(
            hits, [],
            "core 业务代码不得用 `+ \"\\\\\"` 拼路径，应使用 os.path.join: %s" % hits)

    def test_os_path_join_is_the_convention(self):
        count = _join_call_count()
        self.assertGreaterEqual(count, 10,
                                f"core 中 os.path.join 调用仅 {count} 处，"
                                "路径拼接约定疑似被绕过")


if __name__ == "__main__":
    unittest.main(verbosity=2)
