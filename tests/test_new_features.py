# -*- coding: utf-8 -*-
"""新增功能模块的单元测试（标准库 unittest，无第三方依赖）。

覆盖：
- core.review_strategy: compute_impact_closure(多跳 BFS) / _dimension_focus(维度建议) / advise(策略判定)
- core.report_renderer: md_to_html / _safe_link / _inline

运行方式：
    python -m unittest tests.test_new_features
    或
    python tests/test_new_features.py
"""
import os
import sys
import tempfile
import unittest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from core.review_strategy import compute_impact_closure, ReviewAdvisor
from core.report_renderer import md_to_html, _safe_link

# 实例方法通过类直接调用（self 传 None，方法体内不依赖实例状态）
_dimension_focus = ReviewAdvisor._dimension_focus


class _Edge:
    def __init__(self, src, tgt, etype):
        self.source = src
        self.target = tgt
        self.type = etype


class ImpactClosureTest(unittest.TestCase):
    def test_multi_hop_backward(self):
        # A -> B -> C 链路；改 C 应波及 B、A，以及引用 A 的 D（沿 backward 多跳）
        edges = [(1, _Edge("A", "B", "CALLS")),
                 (2, _Edge("B", "C", "CALLS")),
                 (3, _Edge("D", "A", "IMPORTS"))]
        closure, depth = compute_impact_closure(None, {"C"}, edges)
        self.assertIn("B", closure)
        self.assertIn("A", closure)
        self.assertIn("D", closure)
        self.assertGreaterEqual(depth, 2)

    def test_max_depth(self):
        edges = [(i, _Edge(f"N{i}", f"N{i - 1}", "CALLS")) for i in range(1, 20)]
        closure, depth = compute_impact_closure(None, {"N0"}, edges, max_depth=3)
        self.assertLessEqual(depth, 3)

    def test_edge_type_filter(self):
        # 只认 CALLS/IMPORTS/REFERENCES/INHERITS；无关边不参与影响扩散
        edges = [(1, _Edge("X", "Y", "CALLS")),
                 (2, _Edge("W", "Y", "DATA_FLOW"))]
        closure, _ = compute_impact_closure(None, {"Y"}, edges)
        self.assertIn("X", closure)   # CALLS 边参与，X 被波及
        self.assertNotIn("W", closure)  # DATA_FLOW 边被过滤，W 不被波及


class DimensionFocusTest(unittest.TestCase):
    def _chg(self, changed=0, added=0, deleted=0, has_prev=True):
        return {"changed": [f"f{i}" for i in range(changed)],
                "added": [f"a{i}" for i in range(added)],
                "deleted": [f"d{i}" for i in range(deleted)],
                "has_prev_snapshot": has_prev}

    def test_impact_high_focus_innovation_and_arch(self):
        focus = _dimension_focus(None, self._chg(), impact_count=100, stale=False)
        dims = [d["dimension"] for d in focus]
        self.assertIn("innovation_propagation", dims)
        self.assertIn("architecture_complexity", dims)

    def test_small_change_focus_regression(self):
        focus = _dimension_focus(None, self._chg(changed=2), impact_count=5, stale=False)
        dims = [d["dimension"] for d in focus]
        self.assertIn("regression_risk", dims)

    def test_no_change_marked(self):
        focus = _dimension_focus(None, self._chg(), impact_count=0, stale=False)
        dims = [d["dimension"] for d in focus]
        self.assertIn("no_change", dims)


class AdviseTest(unittest.TestCase):
    def test_first_audit_full(self):
        # 无记忆基线的新目录 → 首审计应为全量
        advisor = ReviewAdvisor()
        with tempfile.TemporaryDirectory() as td:
            r = advisor.advise(td)
            self.assertEqual(r["strategy"], "full")
            self.assertFalse(r["kg"]["exists"])


class MdToHtmlTest(unittest.TestCase):
    def test_headings(self):
        out = md_to_html("# 标题一\n## 标题二")
        self.assertIn("<h1>", out)
        self.assertIn("<h2>", out)

    def test_table(self):
        out = md_to_html("| a | b |\n|---|---|\n| 1 | 2 |")
        self.assertIn("<table>", out)
        self.assertIn("<th>a</th>", out)

    def test_fence_code(self):
        out = md_to_html("```py\nx = 1\n```")
        self.assertIn("<pre><code>", out)
        self.assertIn("x = 1", out)

    def test_ordered_list_kept(self):
        out = md_to_html("1. 第一\n2. 第二")
        self.assertIn("<ol>", out)
        self.assertIn("<li>第一</li>", out)
        self.assertIn("<li>第二</li>", out)

    def test_unordered_list(self):
        out = md_to_html("- 一\n- 二")
        self.assertIn("<ul>", out)

    def test_heading_escaped(self):
        out = md_to_html("# <script>alert(1)</script>")
        self.assertNotIn("<script>", out)


class SafeLinkTest(unittest.TestCase):
    def test_normal_link(self):
        self.assertEqual(_safe_link("官网", "https://a.com/x"),
                         '<a href="https://a.com/x" rel="noopener">官网</a>')

    def test_javascript_blocked(self):
        self.assertEqual(_safe_link("x", "javascript:alert(1)"), "x")

    def test_data_html_blocked(self):
        self.assertEqual(_safe_link("x", "data:text/html,<b>x</b>"), "x")


class SelectToolsTest(unittest.TestCase):
    """Pipe._select_tools —— 动态兜底的核心裁剪逻辑"""

    def test_full_returns_all(self):
        from core.pipeline_runner import Pipe
        tools = Pipe._select_tools("full")
        self.assertEqual(len(tools), len(Pipe.ALL_AUDIT_TOOLS))
        methods = {m for _, m in tools}
        self.assertEqual(methods, {m for _, m in Pipe.ALL_AUDIT_TOOLS})

    def test_incr_skips_heavy(self):
        from core.pipeline_runner import Pipe
        tools = Pipe._select_tools("incr")
        methods = {m for _, m in tools}
        # 重型全量工具被裁剪
        self.assertTrue(Pipe.INCR_SKIP_TOOLS.isdisjoint(methods))
        # 轻量/变更相关维度保留（治理、Agent 安全等）
        self.assertIn("_gov", methods)
        self.assertIn("_agent", methods)
        self.assertIn("_td", methods)
        # 裁剪后数量少于全量
        self.assertLess(len(tools), len(Pipe.ALL_AUDIT_TOOLS))

    def test_no_change_falls_back_to_full(self):
        from core.pipeline_runner import Pipe
        tools = Pipe._select_tools("no_change")
        self.assertEqual(len(tools), len(Pipe.ALL_AUDIT_TOOLS))

    def test_unknown_falls_back_to_full(self):
        from core.pipeline_runner import Pipe
        tools = Pipe._select_tools("bogus")
        self.assertEqual(len(tools), len(Pipe.ALL_AUDIT_TOOLS))

    def test_none_falls_back_to_full(self):
        from core.pipeline_runner import Pipe
        tools = Pipe._select_tools(None)
        self.assertEqual(len(tools), len(Pipe.ALL_AUDIT_TOOLS))


class FunctionalReviewPromptTest(unittest.TestCase):
    """FunctionalReviewer._build_prompt —— 回归 f-string 大括号转义 + 显式策略提示不误导"""

    @classmethod
    def setUpClass(cls):
        from core.functional_review import FunctionalReviewer
        cls.fr = FunctionalReviewer()

    def test_prompt_builds_without_error(self):
        # 回归：f-string 内 JSON 示例的字面大括号必须转义 {{ }}，否则运行时抛 Invalid format specifier
        strategy = {"strategy": "full", "reason": "r", "changes": {},
                    "impact": {}, "kg": {}, "dimensions_focus": []}
        ctx = self.fr._collect_context("PROJ", strategy, {})
        prompt = self.fr._build_prompt("PROJ", ctx, [])
        self.assertIn("请返回 JSON 对象", prompt)
        self.assertIn('"dimension_reviews"', prompt)

    def test_explicit_strategy_not_misleading(self):
        # 显式指定策略：应如实说明"未做自动判定"，不得渲染成"未检测到代码变更"
        strategy = {
            "strategy": "incr", "explicit": True,
            "reason": "显式指定审计策略，跳过自动判定",
            "changes": {"has_prev_snapshot": False, "changed": [],
                        "added": [], "deleted": [], "total": 0},
            "impact": {"count": 0, "depth": 0, "nodes": []},
            "kg": {"exists": False, "built_at": "", "stale": True},
            "dimensions_focus": [],
        }
        ctx = self.fr._collect_context("PROJ", strategy, {})
        prompt = self.fr._build_prompt("PROJ", ctx, [])
        self.assertIn("未做自动变更与影响闭包判定", prompt)
        self.assertNotIn("未检测到代码变更", prompt)

    def test_auto_strategy_shows_changes(self):
        # 自动判定路径：仍正常展示变更统计，不被显式分支误伤
        strategy = {
            "strategy": "full", "reason": "项目尚无记忆层基线（首次审计），需全量审查建立基线。",
            "changes": {"has_prev_snapshot": False,
                        "changed": ["a.py"], "added": [], "deleted": [], "total": 1},
            "impact": {"count": 3, "depth": 2, "nodes": []},
            "kg": {"exists": True, "built_at": "x", "stale": False},
            "dimensions_focus": [],
        }
        ctx = self.fr._collect_context("PROJ", strategy, {})
        prompt = self.fr._build_prompt("PROJ", ctx, [])
        self.assertIn("变更 1、新增 0、删除 0 个文件", prompt)


class FunctionalScreenTest(unittest.TestCase):
    """FunctionalReviewer 逐条粗筛 —— LLM 三分类 + 建议白名单条目"""

    @classmethod
    def setUpClass(cls):
        from core.functional_review import FunctionalReviewer
        cls.fr = FunctionalReviewer()

    def _findings(self):
        """构造两条基于真实误报形态的 findings（含 detail/suggestion）。"""
        return [
            {
                "tier": "medium", "tool": "blind", "category": "dynamic_path",
                "title": "sys.path 动态修改",
                "file": "core/mcp_server.py",
                "detail": "第 16 行: sys.path.insert(0, os.path.dirname(...))",
                "suggestion": "代码在运行时动态修改模块搜索路径",
            },
            {
                "tier": "low", "tool": "td", "category": "naming_convention",
                "title": "函数 setUpClass() 使用了驼峰命名",
                "file": "tests/test_new_features.py",
                "detail": "setUpClass 使用驼峰",
                "suggestion": "重命名为 set_up_class",
            },
            {
                "tier": "high", "tool": "gov", "category": "security",
                "title": "硬编码密钥",
                "file": "core/config_loader.py",
                "detail": "疑似硬编码密钥",
                "suggestion": "切换到环境变量",
            },
        ]

    def test_screen_prompt_contains_detail(self):
        # 粗筛 prompt 必须携带 detail/suggestion，供 LLM 判断而非仅凭标题
        prompt = self.fr._build_screen_prompt("PROJ", self._findings())
        self.assertIn("sys.path 动态修改", prompt)
        self.assertIn("core/mcp_server.py", prompt)
        self.assertIn("重命名为 set_up_class", prompt)
        self.assertIn("suspected_fp", prompt)

    def test_screen_prompt_escaped_braces(self):
        # 回归：f-string 内 JSON 示例大括号必须转义，否则抛 Invalid format specifier
        prompt = self.fr._build_screen_prompt("PROJ", self._findings())
        self.assertIn('"verdicts"', prompt)
        self.assertIn('"suspect_whitelist"', prompt)

    def test_find_key(self):
        self.assertEqual(self.fr._find_key({}, 3), "f3")

    def test_suggest_whitelist_entry_respects_empty(self):
        # 空字段不参与白名单条目的 AND 匹配
        entry = self.fr._suggest_whitelist_entry({})
        self.assertEqual(entry, {})

    def test_suggest_whitelist_entry_slices_path_and_rule(self):
        entry = self.fr._suggest_whitelist_entry({
            "file": "C:/Users/x/proj/core/mcp_server.py",
            "title": "sys.path 动态修改（运行时注入）",
            "category": "dynamic_path",
        })
        # file 取最后两段，rule 取前 24 字符，避免绝对路径过宽
        self.assertEqual(entry.get("file"), "core/mcp_server.py")
        self.assertLessEqual(len(entry.get("rule", "")), 24)
        self.assertEqual(entry.get("category"), "dynamic_path")

    def test_empty_screen_structure(self):
        s = self.fr._empty_screen()
        self.assertFalse(s["llm_available"])
        self.assertFalse(s["ran"])
        self.assertEqual(s["summary"], {"suspected_fp": 0, "needs_review": 0, "confirmed": 0})

    def test_screen_available_without_llm_returns_empty(self):
        # LLM 不可用（无 API Key）时粗筛返回空，不抛异常
        s = self.fr._screen_findings("PROJ", self._findings())
        self.assertFalse(s["ran"])
        self.assertFalse(s["llm_available"])

    def test_screen_empty_findings_returns_empty(self):
        s = self.fr._screen_findings("PROJ", [])
        self.assertFalse(s["ran"])

    def test_review_degraded_includes_screen(self):
        # 降级路径也应携带 screen 字段（空粗筛），保证调用方结构一致
        strategy = {"strategy": "full", "reason": "r", "changes": {},
                    "impact": {}, "kg": {}, "dimensions_focus": []}
        r = self.fr.review("PROJ", strategy, pipe_result=None)
        self.assertIn("screen", r)
        self.assertFalse(r["screen"]["ran"])

    def test_screen_accepts_bracket_index_keys(self):
        # 回归：真实 LLM 按 prompt 返回 "[0]" 键，必须正确归一化，不能全部落到 needs_review
        from unittest import mock
        fr = FunctionalScreenTest.fr
        verdicts = {"[0]": "suspected_fp", "[1]": "suspected_fp", "[2]": "confirmed"}
        with mock.patch("core.functional_review._llm_available", return_value=True), \
             mock.patch.object(fr, "_call_llm",
                               return_value={"verdicts": verdicts, "reasons": {}}):
            s = fr._screen_findings("PROJ", FunctionalScreenTest._findings(self))
        self.assertTrue(s["ran"])
        self.assertEqual(s["summary"]["suspected_fp"], 2)
        self.assertEqual(s["summary"]["confirmed"], 1)
        self.assertEqual(s["summary"]["needs_review"], 0)


class DocsReadTest(unittest.TestCase):
    """Pipe.docs_read —— 按需返回文档正文，解决 AI 无法 fs 访问外部文件夹"""

    def _make_wiki(self, td):
        """构造一个含 README.md 与 MODULES/ 子文档的 wiki 目录"""
        wiki = os.path.join(td, "docs", "wiki")
        os.makedirs(os.path.join(wiki, "MODULES"), exist_ok=True)
        with open(os.path.join(wiki, "README.md"), "w", encoding="utf-8") as f:
            f.write("# 项目说明\n\n这是正文。")
        with open(os.path.join(wiki, "MODULES", "core.md"), "w", encoding="utf-8") as f:
            f.write("# core 模块\n\n核心实现。")
        return wiki

    def test_list_documents(self):
        from core.pipeline_runner import Pipe
        with tempfile.TemporaryDirectory() as td:
            self._make_wiki(td)
            r = Pipe().docs_read(td)
            self.assertEqual(r["status"], "ok")
            self.assertIn("README.md", r["documents"])
            self.assertIn("MODULES/core.md", r["documents"])

    def test_read_root_doc(self):
        from core.pipeline_runner import Pipe
        with tempfile.TemporaryDirectory() as td:
            self._make_wiki(td)
            r = Pipe().docs_read(td, doc="README.md")
            self.assertEqual(r["status"], "ok")
            self.assertIn("项目说明", r["content"])

    def test_read_submodule_doc(self):
        from core.pipeline_runner import Pipe
        with tempfile.TemporaryDirectory() as td:
            self._make_wiki(td)
            r = Pipe().docs_read(td, doc="MODULES/core.md")
            self.assertEqual(r["status"], "ok")
            self.assertIn("核心实现", r["content"])

    def test_explicit_output_dir(self):
        # 显式 output_dir 时优先使用，而非默认 docs/wiki 探测
        from core.pipeline_runner import Pipe
        with tempfile.TemporaryDirectory() as td:
            custom = os.path.join(td, "custom_wiki")
            os.makedirs(custom, exist_ok=True)
            with open(os.path.join(custom, "API.md"), "w", encoding="utf-8") as f:
                f.write("# API\n\n接口文档。")
            r = Pipe().docs_read(td, doc="API.md", output_dir=custom)
            self.assertEqual(r["status"], "ok")
            self.assertIn("接口文档", r["content"])
            self.assertEqual(r["output_dir"], custom)

    def test_truncation(self):
        from core.pipeline_runner import Pipe
        with tempfile.TemporaryDirectory() as td:
            self._make_wiki(td)
            r = Pipe().docs_read(td, doc="README.md", max_chars=5)
            self.assertEqual(r["status"], "ok")
            self.assertTrue(r["truncated"])
            self.assertLessEqual(len(r["content"]), 5)

    def test_path_traversal_blocked(self):
        from core.pipeline_runner import Pipe
        with tempfile.TemporaryDirectory() as td:
            self._make_wiki(td)
            # 越界到 wiki 目录之外
            secret = os.path.join(td, "secret.txt")
            with open(secret, "w", encoding="utf-8") as f:
                f.write("机密")
            r = Pipe().docs_read(td, doc="../secret.txt")
            self.assertEqual(r["status"], "error")

    def test_non_md_blocked(self):
        from core.pipeline_runner import Pipe
        with tempfile.TemporaryDirectory() as td:
            self._make_wiki(td)
            r = Pipe().docs_read(td, doc="README.txt")
            self.assertEqual(r["status"], "error")

    def test_missing_doc(self):
        from core.pipeline_runner import Pipe
        with tempfile.TemporaryDirectory() as td:
            self._make_wiki(td)
            r = Pipe().docs_read(td, doc="NOPE.md")
            self.assertEqual(r["status"], "not_found")


class _FakeAnalysis:
    """最小可用的 analysis 替身：只带 files 属性，供 audit mock 使用。"""
    def __init__(self):
        self.files = []


class ToolParityTest(unittest.TestCase):
    """审查修复回归：工具行为与描述一致
    - 差异1：docs 透传 wiki_style/include_subprojects
    - 差异2：render_report 无产物时返回 has_artifacts=False
    - 差异3：audit 的 no_change 短路复用 / 无结论降级 full
    """

    def test_wiki_passes_style_and_subprojects(self):
        # 差异1：_wiki 把 wiki_style/include_subprojects 透传给 WikiGenerator
        from unittest import mock
        from core.pipeline_runner import Pipe, PipeResult
        with tempfile.TemporaryDirectory() as td:
            r = PipeResult(project_path=td)
            with mock.patch("core.wiki_generator.WikiGenerator.generate") as mgen:
                mgen.return_value = None
                Pipe()._wiki(td, r, set(), output_dir=td,
                             wiki_style="tutorial", include_subprojects=False)
            mgen.assert_called_once()
            _, kwargs = mgen.call_args
            self.assertEqual(kwargs.get("wiki_style"), "tutorial")
            self.assertIs(kwargs.get("include_subprojects"), False)

    def test_docs_has_style_params(self):
        # 差异1：docs() 签名暴露并透传两个参数
        from core.pipeline_runner import Pipe
        self.assertIn("wiki_style", Pipe.docs.__code__.co_varnames)
        self.assertIn("include_subprojects", Pipe.docs.__code__.co_varnames)

    def test_detect_wiki_dir(self):
        # 差异2：wiki 目录探测
        from core.pipeline_runner import Pipe
        with tempfile.TemporaryDirectory() as td:
            wiki = os.path.join(td, "docs", "wiki")
            os.makedirs(wiki, exist_ok=True)
            self.assertEqual(Pipe._detect_wiki_dir(td), wiki)
        with tempfile.TemporaryDirectory() as td:
            self.assertIsNone(Pipe._detect_wiki_dir(td))

    def test_latest_report_picks_newest(self):
        # 差异3：取最近一份审计报告
        import time
        from core.pipeline_runner import Pipe
        with tempfile.TemporaryDirectory() as td:
            old = os.path.join(td, "coderef_audit_20260101_000000.md")
            new = os.path.join(td, "coderef_audit_20260102_000000.md")
            with open(old, "w", encoding="utf-8") as f:
                f.write("旧")
            with open(new, "w", encoding="utf-8") as f:
                f.write("新")
            now = time.time()
            os.utime(old, (now - 60, now - 60))
            os.utime(new, (now, now))
            self.assertEqual(Pipe._latest_report(td), new)
        with tempfile.TemporaryDirectory() as td:
            self.assertIsNone(Pipe._latest_report(td))

    def test_reuse_no_change_no_kg_returns_false(self):
        # 差异3：无既有图谱时 no_change 无法复用 → 返回 False（触发降级）
        from unittest import mock
        from core.pipeline_runner import Pipe, PipeResult
        with mock.patch("core.code_knowledge_graph.load_knowledge_graph",
                        return_value=None):
            ok = Pipe()._reuse_no_change("X", PipeResult(project_path="X"),
                                         "out", lambda *a, **k: None)
        self.assertFalse(ok)

    def test_audit_no_change_reuses_without_scan(self):
        # 差异3：有可复用结论时短路，不触发扫描
        from unittest import mock
        from core.pipeline_runner import Pipe
        with tempfile.TemporaryDirectory() as td:
            pipe = Pipe()
            with mock.patch.object(pipe, "_reuse_no_change", return_value=True), \
                 mock.patch.object(pipe, "_scan") as ms:
                r = pipe.audit(td, strategy="no_change")
            ms.assert_not_called()
            self.assertEqual(r.audit_strategy, "no_change")

    def test_audit_no_change_falls_back_full(self):
        # 差异3：无可复用结论时降级为 full，并记录提示
        from unittest import mock
        from core.pipeline_runner import Pipe
        with tempfile.TemporaryDirectory() as td:
            pipe = Pipe()
            with mock.patch.object(pipe, "_reuse_no_change", return_value=False), \
                 mock.patch.object(pipe, "_select_tools", return_value=[]), \
                 mock.patch.object(pipe, "_scan",
                                   return_value=(0, 0, _FakeAnalysis())), \
                 mock.patch.object(pipe, "_build_kg", return_value={}), \
                 mock.patch.object(pipe, "_render_html",
                                   return_value={"ok": True}):
                r = pipe.audit(td, strategy="no_change")
            self.assertEqual(r.audit_strategy, "full")
            self.assertTrue(any("降级为 full" in e for e in r.errors))

    def test_render_report_no_artifacts(self):
        # 差异2：无图谱产物时 render_report 报告 has_artifacts=False
        from core.pipeline_runner import Pipe
        with tempfile.TemporaryDirectory() as td:
            r, has = Pipe().render_report(td)
            self.assertFalse(has)


class TestChangeGuardDynamicFallback(unittest.TestCase):
    """coderef_change_guard 动态兜底：无 diff/baseline_dir 时从 git 提取基线，或明确反馈。"""

    def test_guard_diff_source(self):
        # 提供 diff → source=diff，正常检测
        from core.change_guard import ChangeGuard
        diff = (
            "diff --git a/a.py b/a.py\n"
            "index 111..222 100644\n"
            "--- a/a.py\n"
            "+++ b/a.py\n"
            "@@ -1,3 +1,3 @@\n"
            "-def f():\n"
            "-    return 1\n"
            "+def f():\n"
            "+    return 2\n"
        )
        with tempfile.TemporaryDirectory() as td:
            with open(os.path.join(td, "a.py"), "w", encoding="utf-8") as f:
                f.write("def f():\n    return 2\n")
            r = ChangeGuard().guard(td, diff=diff)
            self.assertEqual(r["source"], "diff")

    def test_guard_baseline_dir_source(self):
        # 提供有效 baseline_dir → source=baseline_dir
        from core.change_guard import ChangeGuard
        with tempfile.TemporaryDirectory() as td:
            base = os.path.join(td, "base")
            os.makedirs(base)
            with open(os.path.join(base, "a.py"), "w", encoding="utf-8") as f:
                f.write("def f():\n    assert x\n    return 1\n")
            with open(os.path.join(td, "a.py"), "w", encoding="utf-8") as f:
                f.write("def f():\n    return 1\n")  # 校验链被删
            r = ChangeGuard().guard(td, baseline_dir=base)
            self.assertEqual(r["source"], "baseline_dir")

    def test_guard_no_diff_no_baseline_no_git(self):
        # 无 diff、无 baseline、且 git 不可用/无历史 → source=no-baseline，明确反馈
        from unittest import mock
        from core.change_guard import ChangeGuard
        g = ChangeGuard()
        with tempfile.TemporaryDirectory() as td:
            with mock.patch.object(g, "_auto_git_diff", return_value=""):
                r = g.guard(td)
            self.assertEqual(r["source"], "no-baseline")
            self.assertIn("退化检测未执行", r["summary"])
            self.assertFalse(r["degraded"])

    def test_guard_dynamic_git_fallback(self):
        # 无 diff/baseline，但 git 能提取基线 → source=git-auto，走真实退化检测
        from unittest import mock
        from core.change_guard import ChangeGuard
        g = ChangeGuard()
        auto_diff = (
            "diff --git a/a.py b/a.py\n"
            "--- a/a.py\n"
            "+++ b/a.py\n"
            "@@ -1,3 +1,3 @@\n"
            "-def f():\n"
            "-    validate(x)\n"
            "+def f():\n"
            "+    return 1\n"
        )
        with tempfile.TemporaryDirectory() as td:
            with open(os.path.join(td, "a.py"), "w", encoding="utf-8") as f:
                f.write("def f():\n    return 1\n")
            with mock.patch.object(g, "_auto_git_diff", return_value=auto_diff):
                r = g.guard(td)
            self.assertEqual(r["source"], "git-auto")
            self.assertTrue(r["degraded"])  # 校验链疑似被删 → high

    def test_auto_git_diff_returns_nonempty(self):
        # _auto_git_diff 在真实 git 仓库 + 有改动时应返回非空 diff
        import subprocess
        from core.change_guard import ChangeGuard
        git = "git"
        try:
            subprocess.run([git, "--version"], capture_output=True, timeout=10)
        except Exception:
            self.skipTest("git 不可用，跳过")
        with tempfile.TemporaryDirectory() as td:
            # 初始化 git 仓库并提交基线
            subprocess.run([git, "-C", td, "init"], capture_output=True, timeout=30)
            subprocess.run([git, "-C", td, "config", "user.email", "t@t"],
                           capture_output=True, timeout=30)
            subprocess.run([git, "-C", td, "config", "user.name", "t"],
                           capture_output=True, timeout=30)
            with open(os.path.join(td, "a.py"), "w", encoding="utf-8") as f:
                f.write("def f():\n    return 1\n")
            subprocess.run([git, "-C", td, "add", "."], capture_output=True, timeout=30)
            subprocess.run([git, "-C", td, "commit", "-m", "init"],
                           capture_output=True, timeout=30)
            # 工作区修改（未提交）
            with open(os.path.join(td, "a.py"), "w", encoding="utf-8") as f:
                f.write("def f():\n    return 2\n")
            d = ChangeGuard()._auto_git_diff(td)
            self.assertTrue(d)

    def test_auto_git_diff_uses_timeout_default(self):
        # 未传 timeout 时使用模块级 DEFAULT_GIT_TIMEOUT
        from unittest import mock
        import core.change_guard as cg
        g = cg.ChangeGuard()
        with mock.patch("subprocess.run", return_value=type("R", (), {
            "returncode": 0, "stdout": "diff", "stderr": ""})()) as mr:
            res = g._auto_git_diff("x", timeout=None)
            self.assertEqual(res, "diff")
            _, kwargs = mr.call_args
            self.assertEqual(kwargs["timeout"], cg.DEFAULT_GIT_TIMEOUT)

    def test_auto_git_diff_uses_explicit_timeout(self):
        # 显式传 timeout 时使用该值
        from unittest import mock
        from core.change_guard import ChangeGuard
        g = ChangeGuard()
        with mock.patch("subprocess.run", return_value=type("R", (), {
            "returncode": 0, "stdout": "diff", "stderr": ""})()) as mr:
            res = g._auto_git_diff("x", timeout=60)
            self.assertEqual(res, "diff")
            _, kwargs = mr.call_args
            self.assertEqual(kwargs["timeout"], 60)

    def test_guard_passes_git_timeout(self):
        # guard() 把 git_timeout 透传给 _auto_git_diff
        from unittest import mock
        from core.change_guard import ChangeGuard
        g = ChangeGuard()
        with mock.patch.object(g, "_auto_git_diff", return_value="diff") as auto, \
             mock.patch.object(g, "_guard_diff", return_value=[]):
            g.guard("x", git_timeout=45)
            auto.assert_called_once_with("x", timeout=45)


def _build_kg(path, functions, calls):
    """构造最小知识图谱：functions=[(id,name,file)]，calls=[(src,tgt)]（CALLS 边）。"""
    import sqlite3 as _sq
    con = _sq.connect(path)
    con.executescript("""
        CREATE TABLE nodes(id TEXT PRIMARY KEY, type TEXT, name TEXT,
            file_path TEXT, start_line INTEGER, end_line INTEGER, props TEXT);
        CREATE TABLE edges(id INTEGER PRIMARY KEY AUTOINCREMENT,
            source TEXT, target TEXT, type TEXT, props TEXT);
    """)
    for fid, name, f in functions:
        con.execute("INSERT INTO nodes VALUES(?,?,?,?,?,?,?)",
                    (fid, "function", name, f, 1, 5, '{}'))
    for src, tgt in calls:
        con.execute("INSERT INTO edges(source,target,type,props) VALUES(?,?,?,?)",
                    (src, tgt, "CALLS", '{}'))
    con.commit()
    con.close()


class FlowVerifyTest(unittest.TestCase):
    """core.flow_verify —— 非编程人员流程合规验证（静态、确定性、不依赖 LLM）"""

    def _kg(self, td):
        """构造标准测试图谱，返回 db 路径。
        入口 main -> a/b/c（并行兄弟）；a -> a1；z 在入口闭包之外。
        """
        db = os.path.join(td, "kg.db")
        _build_kg(
            db,
            [("func:main", "main", "app/main.py"),
             ("func:a", "a", "app/a.py"),
             ("func:a1", "a1", "app/a.py"),
             ("func:b", "b", "app/b.py"),
             ("func:c", "c", "app/c.py"),
             ("func:z", "z", "other/z.py")],
            [("func:main", "func:a"), ("func:main", "func:b"),
             ("func:main", "func:c"), ("func:a", "func:a1")],
        )
        return db

    def test_ordered_serial_chain(self):
        # 串行链路 a -> a1 可从入口逐步确证顺序
        from core.flow_verify import FlowVerifier
        with tempfile.TemporaryDirectory() as td:
            v = FlowVerifier(self._kg(td))
            r = v.verify("main", ["a", "a1"])
        self.assertTrue(r["ok"])
        self.assertEqual([s["status"] for s in r["steps"]],
                         ["ordered", "ordered"])

    def test_parallel_sibling_honest_in_pipeline(self):
        # 并行兄弟 b：在入口闭包内，但从已确证上一步 a 推不出顺序 → 诚实标 in_pipeline
        from core.flow_verify import FlowVerifier
        with tempfile.TemporaryDirectory() as td:
            v = FlowVerifier(self._kg(td))
            r = v.verify("main", ["a", "b"])
        self.assertTrue(r["ok"])  # in_pipeline 不判为失败
        self.assertEqual(r["steps"][1]["status"], "in_pipeline")
        self.assertIn("推不出先后顺序", r["steps"][1]["reason"])

    def test_outside_not_in_closure(self):
        # z 不在入口调用闭包 → outside（可能并行/动态调用），ok 判失败
        from core.flow_verify import FlowVerifier
        with tempfile.TemporaryDirectory() as td:
            v = FlowVerifier(self._kg(td))
            r = v.verify("main", ["a", "z"])
        self.assertFalse(r["ok"])
        self.assertEqual(r["steps"][1]["status"], "outside")

    def test_missing_symbol(self):
        # 项目内无对应符号 → missing
        from core.flow_verify import FlowVerifier
        with tempfile.TemporaryDirectory() as td:
            v = FlowVerifier(self._kg(td))
            r = v.verify("main", ["no_such_symbol"])
        self.assertFalse(r["ok"])
        self.assertEqual(r["steps"][0]["status"], "missing")

    def test_find_entry_module_disambiguation(self):
        # 同名函数用 模块.函数 限定消除歧义
        from core.flow_verify import FlowVerifier
        with tempfile.TemporaryDirectory() as td:
            db = os.path.join(td, "kg.db")
            _build_kg(
                db,
                [("func:mod_a:audit", "audit", "mod_a.py"),
                 ("func:mod_b:audit", "audit", "mod_b.py")],
                [],
            )
            v = FlowVerifier(db)
            self.assertEqual(v.find_entry("mod_a.audit"), "func:mod_a:audit")
            self.assertEqual(v.find_entry("mod_b.audit"), "func:mod_b:audit")

    def test_verify_flow_no_kg_reports_clearly(self):
        # 图谱不存在（未构建）→ 明确反馈需先构建，不静默
        from core.flow_verify import verify_flow
        with tempfile.TemporaryDirectory() as td:
            r = verify_flow(td, "main", ["a"], db_path=os.path.join(td, "nope.db"))
        self.assertFalse(r["ok"])
        self.assertFalse(r["graph_stats"]["has_kg"])
        self.assertIn("知识图谱不存在", r["summary"])

    def test_verify_flow_auto_locate_db(self):
        # 自动定位项目图谱 db：db_path 缺省时通过 CodeKnowledgeGraph 定位
        from core.flow_verify import verify_flow
        with tempfile.TemporaryDirectory() as td:
            db = os.path.join(td, "kg.db")
            _build_kg(db, [("func:main", "main", "main.py"),
                           ("func:step1", "step1", "s.py")],
                      [("func:main", "func:step1")])
            r = verify_flow(td, "main", ["step1"], db_path=db)
        self.assertTrue(r["ok"])
        self.assertEqual(r["steps"][0]["status"], "ordered")

    def test_render_html_contains_statuses(self):
        # HTML 报告包含四种状态图例，供非编程人员阅读
        from core.flow_verify import FlowVerifier, render_html
        with tempfile.TemporaryDirectory() as td:
            v = FlowVerifier(self._kg(td))
            r = v.verify("main", ["a", "b", "z", "no_such"])
            html = render_html(r)
        for label in ("确证", "在管线", "存疑", "缺失"):
            self.assertIn(label, html)


class ArchAuditTest(unittest.TestCase):
    """core.arch_audit —— 架构腐化诊断（模块级静态，复用知识图谱 CALLS 边）"""

    def _db(self, td, funcs, calls):
        db = os.path.join(td, "kg.db")
        _build_kg(db, funcs, calls)
        return db

    def test_cycle_detection(self):
        # a↔b 互相调用 → 模块级 CALLS 图形成环 → 检测到循环依赖
        from core.arch_audit import audit
        with tempfile.TemporaryDirectory() as td:
            db = self._db(td,
                [("func:a", "a", "core/a.py"), ("func:b", "b", "core/b.py")],
                [("func:a", "func:b"), ("func:b", "func:a")])
            r = audit(td, db_path=db)
        self.assertTrue(r["ok"])
        self.assertGreaterEqual(r["summary"]["cycles"], 1)
        self.assertTrue(any({"a", "b"} <= set(c) for c in r["cycles"]))

    def test_clean_graph_no_cycle(self):
        # 单向 a→b 无环 → cycles=0，健康度优秀
        from core.arch_audit import audit
        with tempfile.TemporaryDirectory() as td:
            db = self._db(td,
                [("func:a", "a", "core/a.py"), ("func:b", "b", "core/b.py")],
                [("func:a", "func:b")])
            r = audit(td, db_path=db)
        self.assertTrue(r["ok"])
        self.assertEqual(r["summary"]["cycles"], 0)
        self.assertGreaterEqual(r["summary"]["health"], 8.5)

    def test_god_module_high_fanout(self):
        # hub 扇出 20 > 阈值 15 → 上帝模块
        from core.arch_audit import audit
        with tempfile.TemporaryDirectory() as td:
            leaves = [("func:d%d" % i, "d%d" % i, "core/leaf%d.py" % i)
                      for i in range(20)]
            db = self._db(td,
                [("func:hub", "hub", "core/hub.py")] + leaves,
                [("func:hub", "func:d%d" % i) for i in range(20)])
            r = audit(td, db_path=db, fan_out_threshold=15)
        self.assertTrue(r["ok"])
        self.assertIn("hub", [g["module"] for g in r["god_modules"]])

    def test_layer_violation(self):
        # config(基础层) 依赖 core(引擎层) → 下层依赖上层 → 分层违例
        from core.arch_audit import audit
        with tempfile.TemporaryDirectory() as td:
            db = self._db(td,
                [("func:cfg", "cfg", "config/cfg.py"),
                 ("func:core_mod", "core_mod", "core/x.py")],
                [("func:cfg", "func:core_mod")])
            r = audit(td, db_path=db)
        self.assertTrue(r["ok"])
        self.assertGreaterEqual(r["summary"]["layer_violations"], 1)

    def test_no_kg_reports_clearly(self):
        # 图谱未构建 → 明确反馈需先构建，不静默
        from core.arch_audit import audit
        with tempfile.TemporaryDirectory() as td:
            r = audit(td, db_path=os.path.join(td, "nope.db"))
        self.assertFalse(r["ok"])
        self.assertIn("知识图谱不存在", r["summary"])


class ExploitabilityGateTest(unittest.TestCase):
    """core.sca_checker —— 组件级利用面过滤（社区反馈:CVE-2024-2965 误报）"""

    def _vuln(self, cve="CVE-2024-2965"):
        from core.sca_checker import DependencyVulnerability
        return DependencyVulnerability(
            package="langchain-community", version="0.2.0",
            cve_id=cve, severity="medium",
            summary="SitemapLoader infinite recursion DoS", source="OSV")

    def _project(self, td, content):
        p = os.path.join(td, "app.py")
        with open(p, "w", encoding="utf-8") as f:
            f.write(content)
        return td

    def test_unused_component_downgraded(self):
        # 项目未 import SitemapLoader → CVE 降级为 low 并附"潜在风险"说明
        from core.sca_checker import SCAChecker
        with tempfile.TemporaryDirectory() as td:
            self._project(td, "from langchain_community.llms import OpenAI\nprint('ok')")
            out = SCAChecker()._apply_exploitability_gates(td, [self._vuln()])
        self.assertEqual(out[0].severity, "low")
        self.assertIn("潜在风险", out[0].summary)

    def test_used_component_kept(self):
        # 项目实际使用 SitemapLoader → 保留原判级
        from core.sca_checker import SCAChecker
        with tempfile.TemporaryDirectory() as td:
            self._project(td,
                "from langchain_community.document_loaders.sitemap import SitemapLoader\n"
                "loader = SitemapLoader('https://x/sitemap.xml')")
            out = SCAChecker()._apply_exploitability_gates(td, [self._vuln()])
        self.assertEqual(out[0].severity, "medium")
        self.assertNotIn("潜在风险", out[0].summary)

    def test_non_gate_cve_untouched(self):
        # 未命中利用面规则的 CVE 不受影响
        from core.sca_checker import SCAChecker
        v = self._vuln(cve="CVE-2023-45999")
        with tempfile.TemporaryDirectory() as td:
            self._project(td, "print('ok')")
            out = SCAChecker()._apply_exploitability_gates(td, [v])
        self.assertEqual(out[0].severity, "medium")
        self.assertNotIn("潜在风险", out[0].summary)


class ArchAuditFalsePositiveTest(unittest.TestCase):
    """core.arch_audit —— 同名符号去重 / 单向边不误判为环（社区反馈误报）"""

    def _db(self, td, funcs, calls):
        db = os.path.join(td, "kg.db")
        _build_kg(db, funcs, calls)
        return db

    def test_same_name_diff_dir_not_merged(self):
        # db/base.py 与 utils/base.py 同名不同目录 → 模块名应区分，不合并计数
        from core.arch_audit import audit
        with tempfile.TemporaryDirectory() as td:
            db = self._db(td,
                [("f1", "base", "src/db/base.py"),
                 ("f2", "base", "src/utils/base.py")],
                [("f1", "f2")])
            r = audit(td, db_path=db)
        self.assertTrue(r["ok"])
        # 两个同名模块不应被合并为单个 "base"：cycle 应为 0（单向边）
        self.assertEqual(r["summary"]["cycles"], 0)

    def test_one_way_edge_not_cycle_across_dir(self):
        # dialogue→kb_chat 单向调用（跨目录）→ 不应误判为循环依赖
        from core.arch_audit import audit
        with tempfile.TemporaryDirectory() as td:
            db = self._db(td,
                [("fa", "dialogue", "src/dialogue/x.py"),
                 ("fb", "kb_chat", "src/kb_chat/y.py")],
                [("fa", "fb")])
            r = audit(td, db_path=db)
        self.assertTrue(r["ok"])
        self.assertEqual(r["summary"]["cycles"], 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)