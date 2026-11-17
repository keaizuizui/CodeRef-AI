# -*- coding: utf-8 -*-
"""v5.14.3 回归测试 · 独立回归唯一维护（遵守 tests 归属约定）

覆盖 v5.14.3 四事项 + 外部审查修复的 7 条建议回归清单：
1. TestVersionConsistencyTest —— 版本一致（pyproject dynamic == core.version == PKG_VERSION）
2. TestMcpSetupToolCountTest —— MCP_SETUP 工具数 52 == BUILTIN_TOOLS 实际数
3. TestGitNexusNoShellTest —— 无 shell=True 残留
4. TestNoDeadCodeStdlibModulesTest —— 无 `_get_stdlib_modules() if False` 死代码残留
5. TestOversizedFileDetectTest —— 上帝模块（文件过大）检测命中 + severity 分级
6. TestCheckVersionScriptTest —— check_version.py 版本一致退 0 /（漂移退非 0 由真实回路验证）
7. TestCoverageScriptTest —— coverage_report.py 运行退 0

运行：
    cd coderef-src && python -m unittest discover -s tests
"""
import os
import subprocess
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

PROJECT_ROOT = os.environ.get("CODEREF_SRC", os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from core import version as core_version
from core.mcp_server import BUILTIN_TOOLS, PKG_VERSION
from core.tech_debt_detector import TechDebtDetector
from core.version import __version__ as TRUTH_VERSION


class TestVersionConsistencyTest(unittest.TestCase):
    """事项 1：版本号收敛单一真源 core/version.py，防漂移。"""

    def test_core_version_is_semver(self):
        # 版本号必须是 X.Y.Z 三段语义化格式（不写死具体值，升版改真源即可）
        self.assertEqual(core_version.__version__, TRUTH_VERSION)
        self.assertRegex(TRUTH_VERSION, r"^\d+\.\d+\.\d+$")

    def test_mcp_pkg_version_equals_truth(self):
        # mcp_server 直接 import core.version，废弃文本解析
        self.assertEqual(PKG_VERSION, TRUTH_VERSION)

    def test_pyproject_dynamic_version_matches_truth(self):
        # pyproject 用 [tool.setuptools.dynamic] version={attr=...}，无写死版本号
        pyp = Path(os.path.join(PROJECT_ROOT, "pyproject.toml")).read_text(encoding="utf-8")
        self.assertIn("dynamic = [\"version\"]", pyp)
        self.assertIn("version = { attr = \"core.version.__version__\" }", pyp)
        # 不应再有写死的 X.Y.Z 顶层 version 字段
        for line in pyp.splitlines():
            s = line.strip()
            if s.startswith("version ="):  # 动态 attr 行是不等式，排除 attr 形式
                self.assertIn("core.version.__version__", line)


class TestMcpSetupToolCountTest(unittest.TestCase):
    """外部审查 ⑥：MCP_SETUP 的工具数表述与 tools/list 实际工具数一致。

    工具数随版本演进（52 → 59），断言改为「文档数字 == manifest 实际数」，
    不再钉死 52，避免每次新增工具都产生假红。
    """

    def test_actual_tools_count_matches_doc(self):
        import re
        names = [t.get("name") for t in BUILTIN_TOOLS if t.get("name")]
        mcp = Path(os.path.join(PROJECT_ROOT, "MCP_SETUP.md")).read_text(encoding="utf-8")
        m = re.search(r"(\d+)\s*个工具", mcp)
        self.assertIsNotNone(m, "MCP_SETUP 未找到「N 个工具」表述")
        self.assertEqual(len(names), int(m.group(1)),
                         "MCP_SETUP 工具数与 BUILTIN_TOOLS 实际数不一致")


class TestGitNexusNoShellTest(unittest.TestCase):
    """外部审查 ⑧：阿 gitnexus_client.py 无 shell=True 残留（收敛为列表参数 + shell=False）。"""

    def _src(self):
        return Path(os.path.join(PROJECT_ROOT, "core", "gitnexus_client.py")).read_text(encoding="utf-8")

    def test_no_shell_true_argument(self):
        # 静态断言：不得出现实际的 shell=True（允许注释/字符串里提到）
        src = self._src()
        import ast
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if isinstance(node, ast.keyword) and node.arg == "shell":
                val = node.value
                if isinstance(val, ast.Constant) and val.value is True:
                    self.fail("发现 shell=True 实参残留")
        # 列表参数 + shell=False 应存在
        self.assertIn("shell=False", src)


class TestNoDeadCodeStdlibModulesTest(unittest.TestCase):
    """外部审查 ②：ast_parser.py 清理 `_get_stdlib_modules() if False` 死代码残留。"""

    def test_no_dead_code_guard(self):
        src = Path(os.path.join(PROJECT_ROOT, "core", "ast_parser.py")).read_text(encoding="utf-8")
        self.assertNotIn("if False", src)
        self.assertNotIn("_get_stdlib_modules() if False", src)


class TestOversizedFileDetectTest(unittest.TestCase):
    """事项 2：上帝模块（文件过大）检测维度 + severity 分级。"""

    def _detect(self, lang, content):
        det = TechDebtDetector()
        analysis = SimpleNamespace(files=[
            SimpleNamespace(language=lang, file_path="/tmp/fake_mod.py",
                            raw_content=content, classes=[], functions=[]),
        ])
        return det._detect_oversized_files(analysis)

    def test_large_file_hit_medium(self):
        # 1001 行 → 命中（>1000），medium
        content = "\n".join(["# line"] * 1001)
        debts = self._detect("Python", content)
        self.assertEqual(len(debts), 1)
        self.assertEqual(debts[0].category, "oversized_file")
        self.assertEqual(debts[0].severity, "medium")

    def test_2001_lines_high(self):
        content = "\n".join(["# line"] * 2001)
        debts = self._detect("Python", content)
        self.assertEqual(debts[0].severity, "high")

    def test_3001_lines_critical(self):
        content = "\n".join(["# line"] * 3001)
        debts = self._detect("Python", content)
        self.assertEqual(debts[0].severity, "critical")

    def test_small_file_no_hit(self):
        content = "\n".join(["# line"] * 500)
        debts = self._detect("Python", content)
        self.assertEqual(debts, [])

    def test_boundary_1000_not_hit_1001_hit(self):
        # 边界：1000 行不命中（strict >）、1001 命中
        self.assertEqual(self._detect("Python", "\n".join(["#"] * 1000)), [])
        self.assertEqual(len(self._detect("Python", "\n".join(["#"] * 1001))), 1)


class TestCheckVersionScriptTest(unittest.TestCase):
    """事项 1：check_version.py 发布前版本一致性校验（当前一致 → 退 0）。"""

    def test_check_version_exit_zero(self):
        r = subprocess.run([sys.executable, "tools/check_version.py"],
                           cwd=PROJECT_ROOT, capture_output=True, text=True,
                           timeout=120)
        self.assertEqual(r.returncode, 0, msg=r.stdout + r.stderr)
        self.assertIn("版本一致", r.stdout)


class TestCoverageScriptTest(unittest.TestCase):
    """事项 4：覆盖率监控基建存在且可驱动的子集不求全递归。

    注意：不 subprocess 直接调 tools/coverage_report.py —— 其内部 `pytest tests`
    会扫描整个 tests/，若在 tests 下存在本文件再递归调用将无限嵌套。故改用：
      ① 静态断言脚本命令契约 + pyproject fail_under 配置存在；
      ② 子进程跑 pytest-cov 于一个不含递归的既有测试文件，验证 cov 链路可用。
    """

    def test_script_declares_pytest_cov(self):
        script = Path(os.path.join(PROJECT_ROOT, "tools", "coverage_report.py")).read_text(encoding="utf-8")
        self.assertIn("tests", script)
        self.assertIn("--cov=core", script)
        self.assertIn("--cov-branch", script)
        self.assertIn("--cov-report=term-missing", script)

    def test_pyproject_fail_under_configured(self):
        pyp = Path(os.path.join(PROJECT_ROOT, "pyproject.toml")).read_text(encoding="utf-8")
        self.assertIn("fail_under", pyp)

    def test_pytest_cov_runs_on_subset_without_recursion(self):
        # 避开本文件（会递归），跑一个纯静态既有测试文件验证 cov 链路可用；
        # 单文件子集覆盖率远低于整体 fail_under=15，故 --cov-fail-under=0 仅证链路可用，
        # 不过度约束该子集达到项目级阈值（阈值由 coverage_report 全量测，见 test_pyproject_fail_under_configured）
        target = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                              "test_changeguard_anchor_identity.py")
        self.assertTrue(os.path.exists(target), msg=f"target missing: {target}")
        r = subprocess.run(
            [sys.executable, "-m", "pytest", target, "-q",
             "--cov=core", "--cov-branch", "--cov-report=term", "--cov-fail-under=0"],
            cwd=PROJECT_ROOT, capture_output=True, text=True, timeout=180)
        self.assertEqual(r.returncode, 0, msg=(r.stdout + r.stderr)[-1500:])
        self.assertIn("TOTAL", r.stdout)


if __name__ == "__main__":
    unittest.main()