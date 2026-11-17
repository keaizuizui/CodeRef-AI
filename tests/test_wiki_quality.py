# -*- coding: utf-8 -*-
"""Wiki 质量门禁回归（外部第七轮反馈）。

- 模块文件收集不能被 LLM 采样上限截断（327 文件的模块曾被写成 31 个）
- 模型把「思考过程」当正文返回时不得落盘
- 图谱统计需披露常量判定口径
"""
import os
import sys
import tempfile
import unittest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)


class CollectFilesNotCappedTest(unittest.TestCase):
    def test_collect_keeps_all_files_beyond_sample_limit(self):
        """收集阶段必须全量：file_count 失真会把 327 文件的模块写成 31 个。"""
        from core.wiki_generator import WikiGenerator
        gen = WikiGenerator()
        with tempfile.TemporaryDirectory() as tmp:
            pkg = os.path.join(tmp, "pkg")
            os.makedirs(pkg)
            for i in range(60):
                with open(os.path.join(pkg, f"m{i}.py"), "w", encoding="utf-8") as f:
                    f.write("x = 1\n")
            files = gen._collect_py_files(pkg, tmp)
            self.assertEqual(len(files), 60)


class MetaTalkGateTest(unittest.TestCase):
    def test_meta_talk_rejected(self):
        from core.wiki_generator import _looks_like_meta_talk
        self.assertTrue(_looks_like_meta_talk(
            "用户要求我为这个模块写文档，让我仔细看看现有代码。嗯，这有点矛盾。"))
        self.assertTrue(_looks_like_meta_talk("我需要为这个目录生成说明。"))

    def test_normal_doc_accepted(self):
        from core.wiki_generator import _looks_like_meta_talk
        self.assertFalse(_looks_like_meta_talk("# 模块文档\n\n正文第一段。"))
        self.assertFalse(_looks_like_meta_talk("这是一个普通段落，没有元话语。"))


class ConstantScopeTest(unittest.TestCase):
    def test_stats_declares_constant_scope(self):
        from core.code_knowledge_graph import CodeKnowledgeGraph
        with tempfile.TemporaryDirectory() as tmp:
            kg = CodeKnowledgeGraph(tmp)
            kg._init_schema()
            stats = kg.get_stats()
            scope = stats.get("constant_scope") or {}
            self.assertIn("rule", scope)
            self.assertIn("excluded", scope)
            self.assertEqual(scope.get("counted"), 0)
            kg.close()


class SampleKeepsTrueCountTest(unittest.TestCase):
    def test_sampling_does_not_rewrite_file_count(self):
        """采样只能挑 LLM 样本，不能改写真实文件总数（外部 D-1）。"""
        from core.wiki_generator import WikiGenerator, WikiModule
        gen = WikiGenerator()
        mods = [WikiModule(name="big", path="d:/x",
                           py_files=[f"d:/x/f{i}.py" for i in range(300)],
                           file_count=300, is_core=True)]
        gen._sample_large_repo(mods)
        self.assertEqual(mods[0].file_count, 300)
        self.assertEqual(len(mods[0].py_files), 300)
        self.assertEqual(len(mods[0].sample_files), 20)


class EmptyEmitTest(unittest.TestCase):
    def test_empty_content_writes_placeholder(self):
        """生成失败要写明确占位，而不是不留文件（外部 N-2 / N-3）。"""
        from core.wiki_generator import WikiGenerator
        gen = WikiGenerator()
        with tempfile.TemporaryDirectory() as tmp:
            gen._emit([], tmp, "X.md", "")
            path = os.path.join(tmp, "X.md")
            self.assertTrue(os.path.exists(path))
            with open(path, "r", encoding="utf-8") as f:
                self.assertIn("生成失败", f.read())
            self.assertIn("X.md", gen._failed_docs)


class SanitizeMetaTalkTest(unittest.TestCase):
    def test_leaked_line_removed_body_kept(self):
        from core.wiki_generator import _sanitize_meta_talk
        text = "# 流程\n\n" + ("这是一段正常正文。" * 40) + "\n我们作为校对专家，只能猜测。\n结尾。"
        out = _sanitize_meta_talk(text)
        self.assertNotIn("我们作为", out)
        self.assertIn("这是一段正常正文", out)

    def test_short_result_becomes_empty(self):
        from core.wiki_generator import _sanitize_meta_talk
        self.assertEqual(_sanitize_meta_talk("# t\nHmm. we can remove this."), "")

    def test_clean_text_untouched(self):
        from core.wiki_generator import _sanitize_meta_talk
        text = "# 架构\n\n服务监听 8080 端口，提供 HTTP API。"
        self.assertEqual(_sanitize_meta_talk(text), text)

    def test_mixed_case_hmm_line_removed(self):
        """外部 v5.21.22 实测：`Hmm.` 与业务内容同行时按大小写敏感比较会漏清洗。"""
        from core.wiki_generator import _sanitize_meta_talk
        text = ("# 调研引擎\n\n" + ("这是正常业务说明。" * 40)
                + "\nHmm. I need to check the module list first.\n结尾。")
        out = _sanitize_meta_talk(text)
        self.assertNotIn("Hmm.", out)
        self.assertIn("这是正常业务说明", out)

    def test_mid_doc_chinese_self_talk_removed(self):
        """Brooks-Lint：中文自我对话在正文中段也必须被行级清洗。"""
        from core.wiki_generator import _sanitize_meta_talk
        text = ("# 文档\n\n" + ("这是正常业务说明。" * 40)
                + "\n我注意到这个模块没有依赖。\n结尾。")
        out = _sanitize_meta_talk(text)
        self.assertNotIn("我注意到", out)
        self.assertIn("这是正常业务说明", out)


class FinalizeCleanupGateTest(unittest.TestCase):
    """旧页清理只允许发生在全量路径（外部 v5.21.22：增量运行删掉了子项目与 USAGE）。"""

    @staticmethod
    def _write_page(wiki, rel, front_matter=True, body="old page"):
        path = os.path.join(wiki, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            if front_matter:
                # 带 CodeRef 生成签名（type + confidence + generated_at）
                f.write("---\ntype: module\nconfidence: \"high\"\n"
                        "generated_at: \"2026-01-01T00:00:00\"\n---\n")
            f.write(body)
        return path

    def test_incremental_keeps_unwritten_pages(self):
        from core.wiki_generator import WikiGenerator, WikiResult
        gen = WikiGenerator()
        with tempfile.TemporaryDirectory() as tmp:
            wiki = os.path.join(tmp, "wiki")
            os.makedirs(wiki)
            usage = self._write_page(wiki, "USAGE.md")
            sub = self._write_page(wiki, os.path.join("subprojects", "a", "README.md"))
            res = WikiResult(project_path=tmp, project_name="p", output_dir=wiki)
            gen._written_paths = set()
            gen._finalize_generation(tmp, wiki, False, False, None, res,
                                     full_run=False, include_subprojects=True)
            self.assertTrue(os.path.exists(usage))
            self.assertTrue(os.path.exists(sub))
            self.assertFalse(any("清理" in w for w in res.warnings))

    def test_full_run_cleans_stale_pages_but_keeps_handwritten(self):
        from core.wiki_generator import WikiGenerator, WikiResult
        gen = WikiGenerator()
        with tempfile.TemporaryDirectory() as tmp:
            wiki = os.path.join(tmp, "wiki")
            os.makedirs(wiki)
            stale = self._write_page(wiki, "USAGE.md")
            handwritten = self._write_page(wiki, "NOTES.md", front_matter=False)
            # 用户自己的 YAML front matter（无 CodeRef 生成签名）不得被删
            manual_fm = self._write_page(wiki, "POST.md", front_matter=False,
                                         body="---\ntitle: my post\n---\n正文")
            sub = self._write_page(wiki, os.path.join("subprojects", "a", "README.md"))
            res = WikiResult(project_path=tmp, project_name="p", output_dir=wiki)
            gen._written_paths = set()
            gen._finalize_generation(tmp, wiki, False, False, None, res,
                                     full_run=True, include_subprojects=False)
            self.assertFalse(os.path.exists(stale))
            self.assertTrue(os.path.exists(handwritten))
            self.assertTrue(os.path.exists(manual_fm))
            # 关闭子项目更新不等于删除既有子项目 Wiki
            self.assertTrue(os.path.exists(sub))
            self.assertTrue(any("清理" in w for w in res.warnings))

    def test_failed_and_unwritten_errors_are_distinct(self):
        from core.wiki_generator import WikiGenerator, WikiResult
        gen = WikiGenerator()
        with tempfile.TemporaryDirectory() as tmp:
            wiki = os.path.join(tmp, "wiki")
            os.makedirs(wiki)
            gen._failed_docs = ["USAGE.md"]
            gen._unwritten_docs = ["API.md"]
            res = WikiResult(project_path=tmp, project_name="p", output_dir=wiki)
            gen._finalize_generation(tmp, wiki, False, False, None, res,
                                     full_run=False)
            joined = " ".join(res.errors)
            self.assertIn("已写失败占位", joined)
            self.assertIn("未能落盘", joined)


class DocsModeParamTest(unittest.TestCase):
    """coderef_docs 必须能用 mode 显式触发全量，不再靠删 wiki 目录（外部 v5.21.22）。"""

    def test_schema_exposes_mode(self):
        from core.tools_manifest import BUILTIN_TOOLS
        tool = next(t for t in BUILTIN_TOOLS if t.get("name") == "coderef_docs")
        mode = ((tool.get("inputSchema") or {}).get("properties") or {}).get("mode") or {}
        self.assertEqual(mode.get("enum"), ["auto", "full", "incremental"])

    def test_generate_accepts_mode(self):
        import inspect
        from core.wiki_generator import WikiGenerator
        self.assertIn("mode", inspect.signature(WikiGenerator.generate).parameters)


class TruncationGuardTest(unittest.TestCase):
    """缺陷记录 U-82：尾句截断不得静默落盘。"""

    def test_truncated_outputs_detected(self):
        from core.wiki_generator import _looks_truncated
        self.assertTrue(_looks_truncated("# 文档\n\n```python\nprint(1)"))
        self.assertTrue(_looks_truncated("# 文档\n\n下面按 `chonggou"))
        self.assertTrue(_looks_truncated("# 文档\n\n第一步："))

    def test_normal_outputs_not_flagged(self):
        from core.wiki_generator import _looks_truncated
        self.assertFalse(_looks_truncated("# 文档\n\n完整结尾。"))
        self.assertFalse(_looks_truncated("# 文档\n\n| a | b |\n|---|---|"))
        # 以闭合代码围栏/行内代码结尾是完整文档（CodeRabbit major）
        self.assertFalse(_looks_truncated("# 文档\n\n```python\nprint(1)\n```"))
        self.assertFalse(_looks_truncated("# 文档\n\n运行 `python main.py`"))

    def test_unclosed_structures_detected(self):
        """未闭合围栏/引号/括号/加粗必须判截断（旧判据只数反引号奇偶，漏判）。"""
        from core.wiki_generator import _looks_truncated
        # 围栏未闭合，但围栏内行内反引号让总数变偶数 → 旧判据漏判
        self.assertTrue(_looks_truncated("# 文档\n\n```\na ` b\n"))
        self.assertTrue(_looks_truncated("# 文档\n\n需要确保“"))
        self.assertTrue(_looks_truncated("# 文档\n\n参考 [链接"))
        self.assertTrue(_looks_truncated("# 文档\n\n**调研相关入口"))

    def test_empty_block_and_narrative_tail_detected(self):
        """空列表项/空标题/叙事半句都是正文被切断的确定性特征。"""
        from core.wiki_generator import _looks_truncated
        self.assertTrue(_looks_truncated("# 文档\n\n1."))
        self.assertTrue(_looks_truncated("# 文档\n\n## 三、功能\n\n###"))
        self.assertTrue(_looks_truncated("# 文档\n\n实证记录显示"))
        self.assertTrue(_looks_truncated("# 文档\n\n先要一个概览和"))

    def test_list_item_half_sentence_detected(self):
        """列表/编号行剥前缀后仍断在半句必须判截断（缺陷记录 U-82 2026-11-16）。"""
        from core.wiki_generator import _looks_truncated
        self.assertTrue(_looks_truncated("# 文档\n\n2. 里面并"))
        self.assertTrue(_looks_truncated(
            "# 文档\n\n- **传入（❓推测）：** 同样的"))
        self.assertTrue(_looks_truncated("# 文档\n\n- 已经能一眼能"))
        self.assertTrue(_looks_truncated("# 文档\n\n- 明细；`测试`"))
        # 短列表项本身可以无句号，不能误判
        self.assertFalse(_looks_truncated("# 文档\n\n- 支持自定义"))

    def test_structural_endings_not_flagged(self):
        """列表项正文、引用、斜体/加粗闭合结尾都是完整文档，不得误判。"""
        from core.wiki_generator import _looks_truncated
        self.assertFalse(_looks_truncated("# 文档\n\n- 支持自定义"))
        self.assertFalse(_looks_truncated("# 文档\n\n> 引用说明。"))
        self.assertFalse(_looks_truncated("# 文档\n\n*共 2 次调用。*"))
        self.assertFalse(_looks_truncated("# 文档\n\n**总结**"))

    def test_code_edges_not_flagged(self):
        """闭合 ~~~ 围栏、行内代码里的孤立括号、方位词收尾都不得误判。"""
        from core.wiki_generator import _looks_truncated
        self.assertFalse(_looks_truncated("# 文档\n\n~~~python\nprint(1)\n~~~"))
        self.assertFalse(_looks_truncated("# 文档\n\n使用 `[` 表示左方括号。"))
        self.assertFalse(_looks_truncated("# 文档\n\n- 所有任务均在运行中"))
        self.assertFalse(_looks_truncated("# 文档\n\n- 仅在测试环境中"))

    def test_emit_replaces_truncated_with_placeholder(self):
        """落盘门禁：后处理后的截断正文必须改写失败占位并披露，不得静默落盘。"""
        from core.wiki_generator import WikiGenerator, WikiResult
        gen = WikiGenerator()
        result = WikiResult(project_path="p", project_name="p", output_dir="o")
        with tempfile.TemporaryDirectory() as tmp:
            docs = []
            gen._emit(docs, tmp, "README.md", "# 文档\n\n## 三、功能\n\n下面按 `chonggou")
            written = open(os.path.join(tmp, "README.md"), encoding="utf-8").read()
            self.assertIn("生成失败", written)
            self.assertEqual(gen._truncated_docs, ["README.md"])
            gen._summarize_failures(result)
            self.assertTrue(any("截断" in e for e in result.errors))


class CiteFixPromptEchoTest(unittest.TestCase):
    """缺陷记录 U-80：校对提示词被当正文回传时必须保留原文。"""

    def test_prompt_echo_keeps_original(self):
        from core.wiki_generator import WikiGenerator
        gen = WikiGenerator()
        original = "# 文档\n\n" + "正常正文。" * 60
        gen._llm_ask = lambda sp, up: (
            "我们需要回答中文。任务：文档校对专家，修正下文中出现的代码标识符错误。\n"
            "不存在的标识符 1 个: /api/x\n输出修正后全文。")
        out = gen._cite_fix(original, "X.md", ["/api/x"], None)
        self.assertEqual(out, original)


class ModuleCountAndCiteRepairTest(unittest.TestCase):
    """U-93：索引页不计入模块数；U-94：路径片段不误报、修复后复验。"""

    def test_module_count_excludes_index(self):
        from core.wiki_generator import WikiGenerator
        docs = ["/p/MODULES/_index.md", "/p/MODULES/a.md", "/p/README.md"]
        self.assertEqual(WikiGenerator._module_doc_count(docs), 1)

    @staticmethod
    def _meta():
        from core.wiki_generator import (
            ProjectCodeMetadata, ModuleCodeMetadata, CodeFileMetadata)
        f = CodeFileMetadata(
            rel_path="chonggou/上架分析/_skills/research_engine.py")
        return ProjectCodeMetadata(
            project_path="d:/proj",
            modules=[ModuleCodeMetadata(name="chonggou", path="chonggou",
                                        files=[f])],
            total_files=1)

    def test_path_fragments_and_prefixes_accepted(self):
        from core.wiki_generator import WikiGenerator
        gen = WikiGenerator()
        meta = self._meta()
        text = "见 `_skills` 与 `research_`，模块 `chonggou`。"
        self.assertEqual(gen._cite_verify(text, meta, "X.md"), [])
        self.assertEqual(gen._cite_verify("拼错 `chongou`。", meta, "X.md"),
                         ["chongou"])

    def test_cite_repair_reverifies_after_fix(self):
        from core.wiki_generator import WikiGenerator
        gen = WikiGenerator()
        meta = self._meta()
        original = "拼错 `chongou`。" + "正文。" * 60
        gen._cite_fix = lambda text, name, uv, m: text  # 修复无效
        warnings = []
        out = gen._cite_repair(original, "X.md", meta, warnings)
        self.assertEqual(out, original)
        self.assertTrue(any("仍有" in w and "chongou" in w for w in warnings))

    def test_cite_repair_rejects_truncated_fix(self):
        from core.wiki_generator import WikiGenerator
        gen = WikiGenerator()
        meta = self._meta()
        original = "拼错 `chongou`。" + "正文。" * 60
        gen._cite_fix = lambda text, name, uv, m: (
            "拼错 `chongou`。" + "正文。" * 40 + "\n- 已经能")
        warnings = []
        out = gen._cite_repair(original, "X.md", meta, warnings)
        self.assertEqual(out, original)
        self.assertTrue(any("截断" in w for w in warnings))


class LiteralAndLastErrorTest(unittest.TestCase):
    """U-94 字符串常量误判 / U-96 中间失败残留 errors。"""

    def test_string_literals_not_flagged(self):
        import os
        from core.wiki_generator import (
            WikiGenerator, ProjectCodeMetadata, ModuleCodeMetadata,
            CodeFileMetadata)
        with tempfile.TemporaryDirectory() as tmp:
            mod_path = os.path.join(tmp, "chonggou")
            os.makedirs(mod_path)
            with open(os.path.join(mod_path, "scorecard.py"),
                      "w", encoding="utf-8") as f:
                f.write('DIMENSIONS = {"keyword_completeness": 1, '
                        '"title_coverage": 2}\n')
            meta = ProjectCodeMetadata(
                project_path=tmp,
                modules=[ModuleCodeMetadata(
                    name="chonggou", path=mod_path,
                    files=[CodeFileMetadata(
                        rel_path="scorecard.py")])],
                total_files=1)
            gen = WikiGenerator()
            text = "维度 `keyword_completeness` 与 `title_coverage`。"
            self.assertEqual(gen._cite_verify(text, meta, "X.md"), [])

    def test_retry_success_clears_last_error(self):
        from core.wiki_generator import WikiGenerator

        class _OkLLM:
            @staticmethod
            def chat_completion(*a, **k):
                return "# 文档\n\n完整正文。"

            @staticmethod
            def is_error_response(t):
                return False

        gen = WikiGenerator(llm_client=_OkLLM())
        gen._last_llm_error = "LLM 输出疑似截断，重试两次后仍不完整，已丢弃"
        out = gen._retry_llm([], lambda t: True, "fail")
        self.assertEqual(out, "# 文档\n\n完整正文。")
        self.assertEqual(gen._last_llm_error, "")

    def test_intermediate_error_not_in_errors_when_all_docs_ok(self):
        from core.wiki_generator import WikiGenerator, WikiResult
        gen = WikiGenerator()
        gen._last_llm_error = "中间失败"
        gen._failed_docs = []
        gen._truncated_docs = []
        gen._unwritten_docs = []
        result = WikiResult(project_path="p", project_name="p", output_dir="o")
        gen._summarize_failures(result)
        self.assertEqual(result.errors, [])


    def test_truncation_uses_two_retry_rounds(self):
        """U-97：前两次截断、第三次完整时，必须用上压缩重试并采用完整内容。"""
        from core.wiki_generator import WikiGenerator

        class _SeqLLM:
            def __init__(self):
                self.calls = 0

            def chat_completion(self, *a, **k):
                self.calls += 1
                if self.calls < 3:
                    return "# 文档\n\n- 已经能"
                return "# 文档\n\n完整正文。" + "正文。" * 60

            @staticmethod
            def is_error_response(t):
                return False

        llm = _SeqLLM()
        gen = WikiGenerator(llm_client=llm)
        out = gen._llm_ask("sys", "user")
        self.assertIn("完整正文", out)
        self.assertEqual(llm.calls, 3)

    def test_truncation_all_retries_fail_returns_empty(self):
        """U-97 对照：两轮重试都截断才判失败，且错误如实保留。"""
        from core.wiki_generator import WikiGenerator

        class _BadLLM:
            def __init__(self):
                self.calls = 0

            def chat_completion(self, *a, **k):
                self.calls += 1
                return "# 文档\n\n- 已经能"

            @staticmethod
            def is_error_response(t):
                return False

        llm = _BadLLM()
        gen = WikiGenerator(llm_client=llm)
        self.assertEqual(gen._llm_ask("s", "u"), "")
        self.assertEqual(llm.calls, 3)
        self.assertIn("两次", gen._last_llm_error)


class CleanupSignatureTest(unittest.TestCase):
    """只删除带 CodeRef front matter 签名的旧页，正文示例不算签名。"""

    def test_body_example_does_not_authorize_deletion(self):
        import os
        from core.wiki_generator import WikiGenerator, WikiResult
        gen = WikiGenerator()
        with tempfile.TemporaryDirectory() as tmp:
            user = os.path.join(tmp, "guide.md")
            with open(user, "w", encoding="utf-8") as f:
                f.write("---\ntitle: 用户指南\ntype: page\n---\n\n"
                        "示例：confidence: high\n\ngenerated_at: 2026\n")
            old = os.path.join(tmp, "old.md")
            with open(old, "w", encoding="utf-8") as f:
                f.write('---\ntype: "readme"\ntitle: "x"\nconfidence: "high"\n'
                        'generated_at: "2026-01-01"\n---\n# old\n')
            gen._written_paths = set()
            result = WikiResult(project_path=tmp, project_name="p",
                                output_dir=tmp)
            gen._cleanup_stale_docs(tmp, False, result)
            self.assertTrue(os.path.exists(user))
            self.assertFalse(os.path.exists(old))


class ModuleDocWithoutDescriptionTest(unittest.TestCase):
    """缺陷记录 U-74：描述缺失也必须出模块文档，不得静默缺件。"""

    def test_empty_description_still_emits_module_doc(self):
        from core.wiki_generator import WikiGenerator, WikiModule
        gen = WikiGenerator()
        gen._llm_ask = lambda sp, up: "# 模块\n\n" + "正文。" * 80
        with tempfile.TemporaryDirectory() as tmp:
            mod = WikiModule(name="m", path=tmp, py_files=[], file_count=0,
                             is_core=True)
            docs = gen._generate_module_docs([mod], {}, tmp)
            self.assertTrue(any(d.endswith("m.md") for d in docs))
            self.assertTrue(os.path.exists(os.path.join(tmp, "MODULES", "m.md")))


class Stage2CacheInvalidationTest(unittest.TestCase):
    """缺陷记录 U-86：源码/元数据变化后描述缓存必须失效。"""

    def test_metadata_change_invalidates_description(self):
        from core.wiki_generator import (WikiGenerator, ProjectCodeMetadata,
                                         ModuleCodeMetadata, CodeFileMetadata)
        gen = WikiGenerator()
        calls = []

        def _fake_ask(sp, up):
            calls.append(up)
            return "模块描述。" * 80

        gen._llm_ask = _fake_ask
        with tempfile.TemporaryDirectory() as tmp:
            gen._project_cache_path = lambda p: tmp

            def _meta(rel):
                mod = ModuleCodeMetadata(name="m", path=tmp,
                                         files=[CodeFileMetadata(rel_path=rel)],
                                         total_files=1)
                return ProjectCodeMetadata(project_path=tmp, modules=[mod],
                                           total_files=1)

            gen._generate_module_descriptions(_meta("a.py"), "comprehensive")
            self.assertEqual(len(calls), 1)
            # 元数据未变 → 复用缓存，不再调用 LLM
            gen._generate_module_descriptions(_meta("a.py"), "comprehensive")
            self.assertEqual(len(calls), 1)
            # 元数据变化 → 描述必须重算
            gen._generate_module_descriptions(_meta("b.py"), "comprehensive")
            self.assertEqual(len(calls), 2)
            # 显式 invalidate → 即使指纹未变也必须重算（全量路径契约）
            gen._generate_module_descriptions(_meta("b.py"), "comprehensive",
                                              invalidate=["m"])
            self.assertEqual(len(calls), 3)


class MetadataTotalAndEntryTest(unittest.TestCase):
    def test_metadata_text_carries_true_total(self):
        from core.wiki_generator import WikiGenerator, ModuleCodeMetadata, CodeFileMetadata
        gen = WikiGenerator()
        mod = ModuleCodeMetadata(name="m", path="d:/x",
                                 files=[CodeFileMetadata(rel_path="a.py")],
                                 total_files=302)
        text = gen._module_metadata_to_text(mod)
        self.assertIn("文件总数: 302", text)
        self.assertIn("样本", text)

    def test_sample_metadata_keeps_entry_files(self):
        from core.wiki_generator import WikiGenerator, ProjectCodeMetadata, ModuleCodeMetadata, CodeFileMetadata
        gen = WikiGenerator()
        files = [CodeFileMetadata(rel_path=f"m{i}.py") for i in range(40)]
        files.append(CodeFileMetadata(rel_path="web_gui.py", is_entry_point=True))
        mod = ModuleCodeMetadata(name="m", path="d:/x", files=files, total_files=41)
        meta = ProjectCodeMetadata(project_path="d:/x", modules=[mod], total_files=41)
        gen._sample_metadata(meta)
        kept = [f.rel_path for f in meta.modules[0].files]
        self.assertIn("web_gui.py", kept)
        self.assertLessEqual(len(kept), gen.METADATA_MAX_FILES)


class MetaTalkEnglishTest(unittest.TestCase):
    def test_english_reasoning_rejected(self):
        from core.wiki_generator import _looks_like_meta_talk
        self.assertTrue(_looks_like_meta_talk(
            "# 架构\n\nWe shouldn't claim the module is a platform."))
        self.assertTrue(_looks_like_meta_talk(
            "# 模块\n\nMust only use class/function names from data."))
        self.assertTrue(_looks_like_meta_talk(
            "# 安装\n\n作为校对专家，我们应先核对版本。"))

    def test_normal_english_doc_accepted(self):
        from core.wiki_generator import _looks_like_meta_talk
        self.assertFalse(_looks_like_meta_talk(
            "# Architecture\n\nThe service exposes an HTTP API on port 8080."))


if __name__ == "__main__":
    unittest.main()
