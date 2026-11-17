# -*- coding: utf-8 -*-
"""v5.21.17 定向回归：>50 同名精确核验 / frontend 占位口径 / wiki front matter。"""
import json
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

PROJECT_ROOT = os.environ.get("CODEREF_SRC",
                              os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from core.mcp_server import Server  # noqa: E402
from core.code_analyzer import CodeAnalyzer  # noqa: E402
from core.code_knowledge_graph import CodeKnowledgeGraph  # noqa: E402
from core.arch_audit import locate_kg_db  # noqa: E402
from core.frontend_inspector import FrontendInspector  # noqa: E402
from core.memory_layer import MemoryLayer  # noqa: E402
from core.report_renderer import md_to_html  # noqa: E402


def known(bug: str, why: str):
    # 已知缺陷均已修复（U-70 同步缺口已于 2026-11-12 解除）：默认即阻断，
    # 保留 bug 注释语义供追溯。
    return lambda fn: fn


def known_red(bug: str, why: str):
    """已修复缺陷（保留 RED 复现注释语义）：默认即阻断。"""
    return lambda fn: fn


class _Base(unittest.TestCase):

    def setUp(self):
        self.td = tempfile.mkdtemp(prefix="v5217_")
        self.server = Server()

    def tearDown(self):
        shutil.rmtree(self.td, ignore_errors=True)

    def write(self, p, rel, text):
        fp = os.path.join(p, rel)
        os.makedirs(os.path.dirname(fp), exist_ok=True)
        with open(fp, "w", encoding="utf-8") as f:
            f.write(text)
        return fp

    def call(self, name, args):
        return json.loads(self.server._run(name, dict(args, background=False)))


class ExactSymbolAfterFiftyTest(_Base):

    def test_file_hint_finds_node_beyond_fifty(self):
        p = os.path.join(self.td, "d2")
        for i in range(60):
            self.write(p, "mods/m%02d.py" % i,
                       "def main():\n    return %d\n" % i)
        # 目标文件：main 位于第 25 行，且按名字排序在 50 名之后
        target = "zz_target/run_all.py"
        body = "\n".join(["# pad %d" % i for i in range(24)] +
                         ["def main():", "    return 99"])
        self.write(p, target, body + "\n")
        analysis = CodeAnalyzer().analyze_project(p, force_reanalyze=True)
        CodeKnowledgeGraph(p).build(analysis=analysis, builder="test")
        r = self.call("coderef_verify_findings", {
            "project_path": p,
            "findings": [{"title": "main 存在", "file": target,
                          "symbols": ["main"]}]})
        item = (r.get("results") or [])[0]
        sym = (item.get("evidence") or {}).get("symbols", [{}])[0]
        self.assertEqual(item.get("verdict"), "confirmed",
                         f"文件内 main 应 confirmed: {item}")
        self.assertTrue(sym.get("matched_in_file"), sym)
        self.assertGreaterEqual(sym.get("same_name_elsewhere", 0), 50,
                                f"应报告大量文件外同名: {sym}")
        self.assertIn(":25", str(sym.get("node") or ""),
                      f"node 应指向第 25 行: {sym}")


class FrontendPlaceholderHonestyTest(_Base):

    def test_no_llm_partial_and_summary_not_claim_complete(self):
        p = os.path.join(self.td, "fe")
        buttons = "\n".join('<button id="b%d">B%d</button>' % (i, i)
                            for i in range(21))
        self.write(p, "index.html", "<html><body>%s</body></html>" % buttons)

        class _NoLLM:
            def __init__(self, *a, **k):
                pass
            def is_available(self):
                return False
            def chat_completion(self, *a, **k):
                return ""

        with mock.patch("core.frontend_inspector.LLMIntegration", _NoLLM):
            r = self.call("coderef_frontend", {"project_path": p,
                                               "mode": "static"})
        self.assertTrue(r.get("partial"),
                        f"无 LLM 时必须 partial=true: {list(r.keys())}")
        summary = str(r.get("summary") or "")
        self.assertNotIn("审查完成：发现", summary,
                         f"占位不得伪装成审查完成: {summary}")
        self.assertTrue(("未完成" in summary) or ("占位" in summary),
                        f"summary 应说明未完成/占位: {summary}")


class WikiFrontMatterTest(unittest.TestCase):

    def test_front_matter_stripped(self):
        html = md_to_html("---\ntype: rule\nconfidence: 0.8\n---\n# Doc\n正文\n")
        self.assertNotIn("type:", html)
        self.assertNotIn("confidence:", html)
        self.assertIn("正文", html)

    @known("U-70", "测试副本 report_renderer.py 未同步 _looks_like_yaml_meta：普通分割线区块仍被吞")
    def test_plain_divider_block_preserved(self):
        html = md_to_html("---\n# Warning\n正文\n---\n# Details\n")
        for word in ("Warning", "正文", "Details"):
            self.assertIn(word, html, f"普通分割线区块不得被吞: {html[:200]}")


class KgSnapshotStaleRebuildTest(unittest.TestCase):
    """CodeRabbit #1：建图后文件又被改，审计收尾 sync 必须改全量重建而非停在旧图。"""

    def test_audit_tail_sync_rebuilds_when_snapshot_stale(self):
        from types import SimpleNamespace
        from core.pipeline_runner import Pipe

        td = tempfile.mkdtemp(prefix="v5217kg_")
        try:
            p = os.path.join(td, "kg")
            a = os.path.join(p, "a.py")
            b = os.path.join(p, "b.py")
            os.makedirs(p, exist_ok=True)
            with open(a, "w", encoding="utf-8") as f:
                f.write("def a():\n    return 1\n\ndef a2():\n    return 2\n")
            with open(b, "w", encoding="utf-8") as f:
                f.write("def b():\n    return 1\n")

            # 模拟审计管线：先建全量图，并记录建图时刻的文件快照
            analysis = CodeAnalyzer().analyze_project(p, force_reanalyze=True)
            CodeKnowledgeGraph(p).build(analysis=analysis, builder="audit_pipeline")
            snap_at_build = MemoryLayer._compute_snapshot([a, b])

            # 建图之后源码又被改过（新增函数 → size 变化）
            with open(a, "w", encoding="utf-8") as f:
                f.write("def a():\n    return 1\n\ndef a2():\n    return 2\n"
                        "def a3():\n    return 3\n")

            # 走真实审计收尾链路：skip_kg=True + 建图快照
            r = SimpleNamespace(audit_strategy="incr",
                                kg_built_at="2026-11-12T00:00:00",
                                file_snapshot=snap_at_build, errors=[])
            Pipe()._sync_memory_layer(p, r)
            self.assertEqual(r.errors, [], f"收尾同步不得报错: {r.errors}")

            stats = CodeKnowledgeGraph(p).get_stats()
            # 审计收尾会显式传 builder="audit_sync"，因此 built_by 不一定是
            # memory_sync_kg_stale；判据看是否真的重建（新函数 a3 已入图）。
            self.assertIn(stats.get("built_by"),
                          ("audit_sync", "memory_sync_kg_stale"),
                          f"应走审计收尾重建链路: {stats}")
            self.assertEqual(stats.get("built_mode"), "full", stats)
            self.assertGreaterEqual(
                int((stats.get("node_types") or {}).get("function") or 0), 4,
                f"新函数 a3 应已入图（建图前仅 3 个函数）: {stats}")
        finally:
            shutil.rmtree(td, ignore_errors=True)


class FrontendRuntimeDegradeTest(unittest.TestCase):
    """CodeRabbit #4：runtime 两个降级分支必须显式 partial=True。"""

    def _inspector(self):
        ins = FrontendInspector()
        # 静态结论保持干净（partial=False、无“待确认”），只让 runtime 分支决定 partial
        ins._inspect_static = lambda *a, **k: {
            "buttons": [], "menu_tree": [], "findings": [],
            "summary": "静态结论", "partial": False,
        }
        return ins

    def test_rejected_url_sets_partial(self):
        ins = self._inspector()
        ins._validate_runtime_url = lambda url: (False, "host 不在白名单")
        r = ins._inspect_runtime("p", None, "http://evil.example/", [1, 2, 3])
        self.assertTrue(r.get("partial"), f"URL 被拒必须 partial=true: {r}")
        self.assertIn("未通过白名单校验", str(r.get("summary") or ""))

    def test_browser_unavailable_sets_partial(self):
        ins = self._inspector()
        ins._validate_runtime_url = lambda url: (True, "")

        def _boom(_url):
            raise RuntimeError("browser not available")

        ins._runtime_review = _boom
        r = ins._inspect_runtime("p", None, "http://127.0.0.1:8080/", [1, 2, 3])
        self.assertTrue(r.get("partial"), f"浏览器不可用必须 partial=true: {r}")
        self.assertIn("浏览器自动化不可用", str(r.get("summary") or ""))


class ExactSymbolOverThousandTest(_Base):
    """CodeRabbit #5：同名节点 >1000 时精确查询不得被上限截断。"""

    def test_target_node_found_beyond_thousand(self):
        p = os.path.join(self.td, "big")
        for i in range(1001):
            self.write(p, "mods/m%04d.py" % i,
                       "def main():\n    return %d\n" % i)
        target = "zz_target/run_all.py"
        body = "\n".join(["# pad %d" % i for i in range(24)] +
                         ["def main():", "    return 99"])
        self.write(p, target, body + "\n")
        analysis = CodeAnalyzer().analyze_project(p, force_reanalyze=True)
        CodeKnowledgeGraph(p).build(analysis=analysis, builder="test")
        r = self.call("coderef_verify_findings", {
            "project_path": p,
            "findings": [{"title": "main 存在", "file": target,
                          "symbols": ["main"]}]})
        item = (r.get("results") or [])[0]
        sym = (item.get("evidence") or {}).get("symbols", [{}])[0]
        self.assertEqual(item.get("verdict"), "confirmed",
                         f">1000 同名时文件内 main 仍应 confirmed: {item}")
        self.assertTrue(sym.get("matched_in_file"), sym)
        self.assertIn(":25", str(sym.get("node") or ""),
                      f"node 应指向第 25 行: {sym}")


class CaseVariantKgPathTest(unittest.TestCase):
    """U-71（运行态 2026-11-12 实测）：Windows 盘符大小写不同 → 同一项目两套图谱库，
    `locate_kg_db` 按原始路径定位不到图 → G-B 误报「知识图谱不存在」。"""

    def setUp(self):
        self.td = tempfile.mkdtemp(prefix="v5217case_")
        self.p = os.path.join(self.td, "proj")
        os.makedirs(self.p, exist_ok=True)
        self.variant = self.p[0].lower() + self.p[1:]
        self.assertNotEqual(self.p, self.variant, "需构造大小写变体（非 C: 盘时可用）")

    def tearDown(self):
        shutil.rmtree(self.td, ignore_errors=True)

    @known_red("U-71", "_phash 未 normcase：大小写路径落到不同 db 文件")
    def test_same_project_same_db_path(self):
        a = CodeKnowledgeGraph(self.p).db_path
        b = CodeKnowledgeGraph(self.variant).db_path
        self.assertEqual(os.path.basename(a), os.path.basename(b),
                         f"同一项目的图谱库名应一致: {a} vs {b}")
        self.assertEqual(os.path.normcase(a), os.path.normcase(b),
                         f"同一项目的图谱库路径应一致: {a} vs {b}")

    @known_red("U-71", "locate_kg_db 按原始路径 hash 定位，大小写变体定位不到已建图谱")
    def test_locate_finds_db_for_case_variant(self):
        db = CodeKnowledgeGraph(self.p).db_path
        os.makedirs(os.path.dirname(db), exist_ok=True)
        with open(db, "w", encoding="utf-8") as f:
            f.write("")
        self.assertIsNotNone(locate_kg_db(self.variant),
                             f"大小写变体应能定位到同一库: {db}")


class PathKeyNormalizationTest(unittest.TestCase):
    """v5.21.18：项目路径标识统一走 core.path_key.project_key（防止库名分裂）。"""

    def test_project_key_normcase(self):
        from core.path_key import project_key
        a = project_key(r"d:\本地工作区\demo")
        b = project_key(r"D:\本地工作区\demo")
        self.assertEqual(a, b, "同一路径的大小写变体必须得到同一项目 key")
        self.assertEqual(a, a.strip(), "key 不应带首尾空白")


class KgStatsTruthTest(unittest.TestCase):
    """v5.21.18：build 返回的统计应为落库真值（不是内存累加器）。"""

    def test_stats_equal_db_counts(self):
        td = tempfile.mkdtemp(prefix="v5218kg_")
        try:
            p = os.path.join(td, "kg")
            os.makedirs(p, exist_ok=True)
            for name in ("a.py", "b.py"):
                with open(os.path.join(p, name), "w", encoding="utf-8") as f:
                    f.write("def %s():\n    return 1\n" % name[:-3])
            analysis = CodeAnalyzer().analyze_project(p, force_reanalyze=True)
            kg = CodeKnowledgeGraph(p)
            kg.build(analysis=analysis, builder="test")
            stats = kg.get_stats()
            sql_nodes = kg._conn.execute(
                "SELECT COUNT(*) FROM nodes").fetchone()[0]
            sql_edges = kg._conn.execute(
                "SELECT COUNT(*) FROM edges").fetchone()[0]
            self.assertEqual(int(stats["node_count"]), int(sql_nodes),
                             f"nodes 统计应为落库真值: {stats} vs {sql_nodes}")
            self.assertEqual(int(stats["edge_count"]), int(sql_edges),
                             f"edges 统计应为落库真值: {stats} vs {sql_edges}")
        finally:
            shutil.rmtree(td, ignore_errors=True)


if __name__ == "__main__":
    unittest.main(verbosity=2)
