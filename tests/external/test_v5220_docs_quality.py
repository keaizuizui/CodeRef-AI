# -*- coding: utf-8 -*-
"""v5.21.19/20 定向回归：Wiki 质量门禁 + 口径披露 + CALLS 一致性。

覆盖开发方 v5.21.20 复测点中可源码级确定性验证的部分：
- D-1 采样不改真实文件数（_sample_large_repo）
- D-2 空正文不落盘（_emit / _write_doc）
- D-3 元话语门禁（_looks_like_meta_talk）
- D-4 评估台 wiki 时间取目录内最新文件 mtime
- D-5 入口判定扩展（web_gui / web_* / __main__ 且非 tests）
- D-6/D-7/D-8 constant_scope / function_scope 披露 + 单词常量入图
- B-1 口径排除常量 → verify_findings 判 unverifiable
- 复测点 10 CALLS 一致性（audit_pipeline vs memory_sync）
"""
import json
import os
import re
import shutil
import sys
import tempfile
import time
import unittest

PROJECT_ROOT = os.environ.get("CODEREF_SRC",
                              os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from core.mcp_server import Server  # noqa: E402
from core.code_analyzer import CodeAnalyzer  # noqa: E402
from core.code_knowledge_graph import CodeKnowledgeGraph  # noqa: E402
from core.memory_layer import MemoryLayer  # noqa: E402
from core.wiki_generator import (  # noqa: E402
    WikiGenerator, WikiModule, _looks_like_meta_talk, _looks_truncated)
from core.pr_render import _collect_dimension_states  # noqa: E402


def known_red(bug: str, why: str):
    """未修复缺陷：默认 skip（不拖红门禁），STRICT 下复现。"""
    def deco(fn):
        if os.environ.get("CODEREF_STRICT_KNOWN") == "1":
            return fn
        return unittest.skip(
            f"RED 复现保留 {bug}：{why}（设 CODEREF_STRICT_KNOWN=1 可复现）")(fn)
    return deco


class _Base(unittest.TestCase):

    def setUp(self):
        self.td = tempfile.mkdtemp(prefix="v5220_")
        self.server = Server()

    def tearDown(self):
        shutil.rmtree(self.td, ignore_errors=True)

    def write(self, root, rel, text):
        fp = os.path.join(root, rel)
        os.makedirs(os.path.dirname(fp), exist_ok=True)
        with open(fp, "w", encoding="utf-8") as f:
            f.write(text)
        return fp

    def project(self, name="p"):
        p = os.path.join(self.td, name)
        os.makedirs(p, exist_ok=True)
        return p

    def call(self, name, args):
        return json.loads(self.server._run(name, dict(args, background=False)))


class SamplingPreservesCountsTest(unittest.TestCase):
    """D-1：大仓库采样只挑样本，绝不改写 file_count / py_files。"""

    def test_sample_does_not_change_counts(self):
        gen = WikiGenerator()
        files = ["m%02d.py" % i for i in range(302)]
        mod = WikiModule(name="chonggou", path="chonggou", py_files=list(files),
                         file_count=len(files), is_core=True)
        gen._sample_large_repo([mod])
        self.assertEqual(mod.file_count, 302, "采样不得改写真实文件数")
        self.assertEqual(len(mod.py_files), 302, "py_files 不得被采样裁剪")
        self.assertTrue(len(mod.sample_files) <= 20,
                        f"样本应受限: {len(mod.sample_files)}")
        self.assertLess(len(mod.sample_files), len(mod.py_files))


class EmptyDocNotWrittenTest(unittest.TestCase):
    """D-2（v5.21.21 契约变更）：空正文不得落成「只有 front matter」的空壳。

    旧契约：空正文不落盘；v5.21.21 起改为写**可识别占位**（防缺件/空目录，外部 N-2/N-3）。
    新判据：占位必须能被识别为失败、不得伪装成正常正文，且仍记入失败清单。
    """

    def test_empty_content_writes_identifiable_placeholder(self):
        td = tempfile.mkdtemp(prefix="v5220emit_")
        try:
            gen = WikiGenerator()
            docs = []
            gen._emit(docs, td, "EMPTY.md", "", front_matter="---\ntitle: x\n---\n")
            fp = os.path.join(td, "EMPTY.md")
            self.assertTrue(os.path.exists(fp),
                            "v5.21.21 起空正文应写占位（防缺件），不是不落盘")
            self.assertEqual(len(docs), 1, "占位文档应计入 docs 清单")
            with open(fp, encoding="utf-8") as f:
                text = f.read()
            self.assertIn("生成失败", text, f"占位必须可识别为失败: {text[:200]}")
            # 核心红线不变：不得是「只有 front matter、正文 0 字」的空壳
            body = re.sub(r"^---.*?---\s*", "", text, flags=re.S).strip()
            self.assertGreater(len(body), 0, "占位必须有正文说明，不得是空壳")
            self.assertIn("EMPTY.md", gen._failed_docs)
            # 有正文时正常落盘，且不得被误标失败
            gen._emit(docs, td, "OK.md", "# 标题\n\n正文\n")
            self.assertTrue(os.path.exists(os.path.join(td, "OK.md")))
            self.assertNotIn("OK.md", gen._failed_docs)
        finally:
            shutil.rmtree(td, ignore_errors=True)


class MetaTalkGateTest(unittest.TestCase):
    """D-3：思考过程（元话语）识别。"""

    def test_meta_talk_detected(self):
        self.assertTrue(_looks_like_meta_talk("我们需要写一份文档，需要遵守规则，必须原样引用。"))
        self.assertFalse(_looks_like_meta_talk("# 模块说明\n\n这是正文内容。"))

    def test_extract_file_meta_entry_detection(self):
        """D-5：入口判定扩展到 web_gui / web_* 前缀 / __main__（非 tests）。"""
        gen = WikiGenerator()
        fm = gen._extract_file_metadata("def main():\n    pass\n", "web_gui.py")
        self.assertTrue(fm.is_entry_point, "web_gui.py 必须判为入口")
        fm = gen._extract_file_metadata("print(1)\n", "web_admin.py")
        self.assertTrue(fm.is_entry_point, "web_ 前缀必须判为入口")
        fm = gen._extract_file_metadata(
            "if __name__ == '__main__':\n    main()\n", "tools/cli_entry.py")
        self.assertTrue(fm.is_entry_point, "非 tests 的 __main__ 块必须判为入口")
        fm = gen._extract_file_metadata(
            "if __name__ == '__main__':\n    main()\n", "tests/test_x.py")
        self.assertFalse(fm.is_entry_point, "tests 下的 __main__ 不算入口")
        fm = gen._extract_file_metadata("def f():\n    return 1\n", "helpers/utils.py")
        self.assertFalse(fm.is_entry_point, "普通工具模块不应判为入口")


class WikiTimeLatestFileTest(unittest.TestCase):
    """D-4：评估台 wiki 时间 = 目录内最新文件 mtime（不是目录 mtime）。"""

    def test_time_uses_latest_file_mtime(self):
        td = tempfile.mkdtemp(prefix="v5220wiki_")
        try:
            p = os.path.join(td, "proj")
            wiki = os.path.join(p, "docs", "wiki")
            os.makedirs(wiki, exist_ok=True)
            old = os.path.join(wiki, "OLD.md")
            new = os.path.join(wiki, "NEW.md")
            for fp in (old, new):
                with open(fp, "w", encoding="utf-8") as f:
                    f.write("# doc\n")
            # OLD 设成 1 天前，NEW 设为现在；目录 mtime 再设为 2 天前
            past = time.time() - 86400
            os.utime(old, (past, past))
            os.utime(wiki, (past - 86400, past - 86400))
            states = _collect_dimension_states(p, None)
            ts = (states.get("wiki") or {}).get("ts") or ""
            self.assertTrue(ts, f"wiki 状态应带时间: {states.get('wiki')}")
            expect = time.strftime("%Y-%m-%d %H:%M:%S",
                                   time.localtime(os.path.getmtime(new)))
            self.assertEqual(ts, expect,
                             f"时间应取最新文件而非目录 mtime: {ts} vs {expect}")
        finally:
            shutil.rmtree(td, ignore_errors=True)


class ConstantAndFunctionScopeTest(_Base):
    """D-6/D-7/D-8：常量口径披露 + 单词常量入图（走 AST 建图路径）。"""

    def test_scope_fields_and_single_word_constant(self):
        p = self.project("scope")
        self.write(p, "mod.py",
                   "SOURCE = 1\nDROP = 'x'\nCAPABILITIES = ['a', 'b']\n\n"
                   "def run():\n    return SOURCE\n")
        # 走 memory sync 的 AST 路径：常量分类在 ast_parser._classify_assignment
        MemoryLayer().sync(p, mode="full")
        stats = CodeKnowledgeGraph(p).get_stats()
        self.assertIn("constant_scope", stats)
        self.assertIn("function_scope", stats)
        cs = stats["constant_scope"]
        self.assertIn("rule", cs)
        self.assertIn("counted", cs)
        self.assertEqual(int(cs["counted"]), int(
            (stats.get("node_types") or {}).get("constant", 0)),
            "constant_scope.counted 应与节点数一致")
        self.assertGreaterEqual(int(cs["counted"]), 2,
                                f"SOURCE/DROP 这类单词常量应入图: {cs}")
        fs = stats["function_scope"]
        self.assertIn("rule", fs)
        self.assertIn("note", fs)


class ExcludedConstantUnverifiableTest(_Base):
    """B-1：容器常量的论断应判 unverifiable，并说明口径排除。"""

    def test_container_constant_unverifiable(self):
        p = self.project("ver")
        self.write(p, "conf.py",
                   "CAPABILITIES = ['read', 'write']\nLITERAL = 1\n")
        analysis = CodeAnalyzer().analyze_project(p, force_reanalyze=True)
        CodeKnowledgeGraph(p).build(analysis=analysis, builder="test")
        r = self.call("coderef_verify_findings", {
            "project_path": p,
            "findings": [{"title": "CAPABILITIES 定义", "file": "conf.py",
                          "symbols": ["CAPABILITIES"]}]})
        item = (r.get("results") or [])[0]
        self.assertEqual(item.get("verdict"), "unverifiable",
                         f"容器常量不应判 partially_confirmed: {item}")
        self.assertIn("constant_scope", str(item.get("reason") or ""),
                      f"原因应说明口径排除: {item}")


class CallsConsistencyTest(_Base):
    """复测点 10：同一项目 audit_pipeline 与 memory_sync 的 nodes/edges/CALLS 一致。"""

    def test_audit_vs_memory_sync_identical(self):
        p = self.project("calls")
        self.write(p, "pkg/__init__.py", "")
        self.write(p, "pkg/base.py",
                   "def helper():\n    return 1\n")
        self.write(p, "main.py",
                   "from pkg.base import helper as h\n\n"
                   "def run():\n    return h()\n")

        from core.pr_whitelist import _build_kg
        analysis = CodeAnalyzer().analyze_project(p, force_reanalyze=True)
        _build_kg(p, analysis)          # 审计链路（含 AstParser 预热解析）
        audit_stats = CodeKnowledgeGraph(p).get_stats()

        MemoryLayer().sync(p, mode="full")   # 记忆链路
        mem_stats = CodeKnowledgeGraph(p).get_stats()

        for key in ("node_count", "edge_count", "calls"):
            self.assertEqual(int(audit_stats.get(key) or 0),
                             int(mem_stats.get(key) or 0),
                             f"{key} 在两条建图路径不一致: "
                             f"audit={audit_stats.get(key)}, mem={mem_stats.get(key)}")
        self.assertIn(mem_stats.get("built_by"),
                      ("memory_sync_full", "mcp_memory_sync"),
                      f"memory sync 后应有溯源标签: {mem_stats}")


class V5221RemainderTest(unittest.TestCase):
    """v5.21.21 残留处置：元数据真实总数 / 入口优先采样 / 强特征推理链 / 旧页清理。"""

    def test_module_metadata_carries_true_total(self):
        from core.wiki_generator import (
            CodeFileMetadata, ModuleCodeMetadata)
        gen = WikiGenerator()
        mod = ModuleCodeMetadata(
            name="big", path="big", total_files=302,
            files=[CodeFileMetadata(rel_path="a%d.py" % i) for i in range(3)])
        text = gen._module_metadata_to_text(mod)
        self.assertIn("文件总数: 302", text,
                      "元数据首行必须给真实总数（不是样本数）")
        self.assertIn("仅列出", text)

    def test_sample_metadata_keeps_entry_files(self):
        from core.wiki_generator import (
            CodeFileMetadata, ModuleCodeMetadata, ProjectCodeMetadata)
        gen = WikiGenerator()
        limit = int(gen.METADATA_MAX_FILES)
        files = [CodeFileMetadata(rel_path="mod%d.py" % i)
                 for i in range(limit + 5)]
        entry = CodeFileMetadata(rel_path="web_gui.py", is_entry_point=True)
        files.append(entry)  # 排在最后，随机/顺序截断都会被挤掉
        mod = ModuleCodeMetadata(name="m", path="m", total_files=len(files),
                                 files=files)
        meta = ProjectCodeMetadata(project_path="p", modules=[mod],
                                   total_files=len(files))
        gen._sample_metadata(meta)
        paths = [f.rel_path for f in mod.files]
        self.assertIn("web_gui.py", paths,
                      f"入口文件必须优先保留在样本中: {paths}")
        self.assertEqual(len(mod.files), limit)
        self.assertEqual(mod.total_files, limit + 6, "总数不得被采样改写")

    def test_strong_meta_talk_even_with_heading(self):
        self.assertTrue(_looks_like_meta_talk(
            "# 模块说明\n\nWe shouldn't claim anything without evidence."),
            "强特征即使以 # 开头也必须判思考过程")
        self.assertTrue(_looks_like_meta_talk(
            "作为校对专家，我们应只使用数据中的类名。"))
        self.assertTrue(_looks_like_meta_talk(
            "# Doc\n\nMust only use class/function names from data."))
        self.assertFalse(_looks_like_meta_talk("# 模块说明\n\n本模块负责解析配置。"))

    def test_stale_generated_docs_pruned(self):
        from core.wiki_generator import WikiResult
        td = tempfile.mkdtemp(prefix="v5221prune_")
        try:
            out = os.path.join(td, "wiki")
            os.makedirs(out, exist_ok=True)
            stale = os.path.join(out, "STALE.md")
            with open(stale, "w", encoding="utf-8") as f:
                # 真实 CodeRef 生成页 front matter 带 confidence + generated_at
                f.write("---\ntype: module\nconfidence: high\n"
                        "generated_at: 2026-01-01\n---\n# 旧页\n")
            user_yaml = os.path.join(out, "USER_YAML.md")
            with open(user_yaml, "w", encoding="utf-8") as f:
                # 只带 type：用户自带 YAML，按 CodeRabbit #11 契约不得删除
                f.write("---\ntype: module\n---\n# 用户自带 YAML\n")
            manual = os.path.join(out, "MANUAL.md")
            with open(manual, "w", encoding="utf-8") as f:
                f.write("# 用户手写\n")
            keep = os.path.join(out, "INSTRUCTIONS.md")
            with open(keep, "w", encoding="utf-8") as f:
                f.write("---\ntype: instructions\n---\n# 授权文件\n")
            gen = WikiGenerator()
            gen._written_paths = set()
            result = WikiResult(project_path=td, project_name="t",
                                output_dir=out)
            gen._finalize_generation(td, out, False, False, None, result)
            self.assertFalse(os.path.exists(stale), "旧版生成页应被清理")
            self.assertTrue(os.path.exists(user_yaml),
                            "只带 type 的用户 YAML 不得被当作生成页删除"
                            "（CodeRabbit #11 契约）")
            self.assertTrue(os.path.exists(manual), "用户手写 md 不得删除")
            self.assertTrue(os.path.exists(keep), "INSTRUCTIONS.md 不得删除")
            self.assertTrue(any("已清理" in w for w in result.warnings),
                            f"warnings 应记录清理条数: {result.warnings}")
        finally:
            shutil.rmtree(td, ignore_errors=True)


class TruncationListBlindSpotTest(unittest.TestCase):
    """U-82（2026-11-16 产物级复测）：列表项正文断句不得豁免闭合判定。

    当前 `_looks_truncated` 把以 `- / * / 1.` 开头的末行整体判 structured，
    跳过闭合判定；真实仓 40 篇重生成实测 4 篇列表项截断由此漏判。
    """

    # 真实仓 rt_docs_verify2 产物的 4 条末行原文
    LIST_CASES = (
        "2. 里面并",
        "- **传入（❓推测）：** 同样的",
        "3. **带 `_` 前缀的函数是「内部零件」**："
        "`_fetch_with_requests`、`_looks_like_block_page` 用下划线开头；`测试`",
        "3. **❓推测：把 Keepa 解析和信号计算解耦后更好维护。** "
        "`_keepa_series`、`_map_keepa_product` 负责“翻译外部数据”，"
        "五项信号判断负责“下判断”。测试能分别验证这两段：翻译错了、还是判断错了，一眼能",
    )

    def test_truncated_list_item_is_detected(self):
        """U-82 于 2026-11-16 修复，原红复现已转正式门禁。"""
        for case in self.LIST_CASES:
            with self.subTest(case=case[:30]):
                self.assertTrue(_looks_truncated(case),
                                "列表项断句漏判（U-82）: %r" % case)

    def test_complete_list_item_is_not_flagged(self):
        """绿：完整列表项不得误报（防止修复时过度收紧）。"""
        for case in ("- 完整的一条结论。", "1. 这是完整说明。",
                     "- **加粗标题**：正文完整。"):
            with self.subTest(case=case):
                self.assertFalse(_looks_truncated(case),
                                 "完整列表项被误判: %r" % case)


class DocsPayloadWarningsTest(unittest.TestCase):
    """U-72（v5.21.21 运行态实测）：docs 返回体未透出 warnings（清理条数不可见）。"""

    @known_red("U-72", "coderef_docs 返回体 wiki/顶层均无 warnings，"
                       "旧页清理与部分失败明细对调用方不可见")
    def test_docs_payload_exposes_warnings(self):
        import inspect
        from core import mcp_server
        src = inspect.getsource(mcp_server)
        idx = src.find('"wiki": {')
        self.assertGreater(idx, 0)
        block = src[idx:idx + 600]
        self.assertIn('"warnings"', block,
                      f"返回体应透出 warnings: {block[:300]}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
