# -*- coding: utf-8 -*-
"""LLM「有 Key 但行为异常」时的稳定性与诚实性（S-LLM-FAULT）。

为什么需要本套件
----------------
既有回归多在做「LLM 不可用 / 无 Key」的硬阻断，但真实事故更多来自
**有 Key、请求也发出去了，模型却返回畸形内容 / 空串 / 超时 / 限流 / 注入 HTML**。
此时工具若崩、静默返回空、或把「源不可用」当「结果为空」，调用方会拿到
看似成功实为空的产物。

本文件用 mock 注入 6 类故障，逐条固化判据：
  F1 畸形 JSON      → 降级结论 + 明确 error，不崩、不静默空
  F2 空字符串        → 写可识别失败占位 + 记失败清单，不产出空文档
  F3 超时            → 重试次数有界；产物需带 timeout 降级标记
  F4 限流 429        → 结构化错误（源不可用），可与「结果为空」区分
  F5 返回混入 <script> → wiki/报告 HTML 必须转义，不得原样输出
  F6 正常返回        → 全部降级标记必须为 false（不得常驻 true）

运行
----
    cd d:\\本地工作区\\测试副本\\coderef-src
    python -m unittest discover -s "..\\独立测试\\tests" -p "test_llm_faults.py" -v
"""
import json
import os
import shutil
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

_HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.environ.get("CODEREF_SRC") or os.path.abspath(
    os.path.join(_HERE, "..", "..", "coderef-src"))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import core.llm_integration as llm_mod  # noqa: E402
from core.llm_integration import LLMIntegration, LLMProvider  # noqa: E402
from core.code_review import CodeReviewer  # noqa: E402
from core.report_renderer import md_to_html  # noqa: E402


def _fake_llm_class(response="", exc=None):
    """构造替换 core.llm_integration.LLMIntegration 的假类。

    只实现 WikiGenerator 实际依赖的 4 个入口（is_available/reset_budget/
    is_error_response/chat_completion），避免 mock 面过大掩盖真实调用。
    """
    class _FakeLLM:
        config = SimpleNamespace(api_key="sk-real-key-123456", provider=None)

        def is_available(self):
            return True

        def reset_budget(self):
            pass

        def is_error_response(self, text):
            return LLMIntegration.is_error_response(text)

        def chat_completion(self, *args, **kwargs):
            if exc is not None:
                raise exc
            return response

    return _FakeLLM


class _FakeCompletions:
    """模拟 openai client.chat.completions.create 的异常出口。"""

    def __init__(self, exc):
        self.exc = exc
        self.calls = 0

    def create(self, **kwargs):
        self.calls += 1
        raise self.exc


class _RateLimited(Exception):
    status_code = 429


class _RealKeyConfig:
    api_key = "sk-real-key-123456"
    provider = LLMProvider.DEEPSEEK
    model = "fake-model"
    temperature = 0.7
    max_tokens = 128


class LLMFaultToleranceTest(unittest.TestCase):
    """每种 LLM 故障一个用例；临时项目只读、不触碰 coderef-src。"""

    def setUp(self):
        self.td = tempfile.mkdtemp(prefix="llmfault_")

    def tearDown(self):
        shutil.rmtree(self.td, ignore_errors=True)

    # ── 临时工程 / docs 调用辅助 ──────────────────────────────
    def _make_project(self, name="p"):
        p = os.path.join(self.td, name)
        os.makedirs(os.path.join(p, "pkg"), exist_ok=True)
        with open(os.path.join(p, "pkg", "__init__.py"), "w", encoding="utf-8") as f:
            f.write("")
        with open(os.path.join(p, "pkg", "svc.py"), "w", encoding="utf-8") as f:
            f.write("def run(x):\n    return x\n")
        with open(os.path.join(p, "app.py"), "w", encoding="utf-8") as f:
            f.write("def main():\n    return 1\n")
        return p

    def _run_docs(self, fake_cls, name="p"):
        """走 MCP 层 coderef_docs；返回 (project_path, 解析后的结果 dict)。

        G-E 记忆闸门要求先 recover，本套件对临时项目做一次 recover 后再调用，
        保证被测的是 docs 行为本身，而不是闸门拦截。
        """
        from core.mcp_server import Server

        p = self._make_project(name)
        srv = Server()
        srv._run("coderef_operation_memory", {"action": "recover", "project_path": p})
        with mock.patch.object(llm_mod, "LLMIntegration", fake_cls), \
                mock.patch("core.code_knowledge_base.OllamaEmbedder.is_available",
                           return_value=False):
            raw = srv._run("coderef_docs", {
                "project_path": p, "background": False, "mode": "full"})
        return p, json.loads(raw)

    def _read(self, path):
        with open(path, "r", encoding="utf-8") as f:
            return f.read()

    # ── F1 畸形 JSON ─────────────────────────────────────────
    def test_f1_malformed_json_degrades_with_explicit_error(self):
        """截断 JSON 不得崩，也不得静默返回空：必须有降级结论 + 明确原因。"""
        # 1) 解析层：截断 JSON 解析失败返回 None（不抛异常）
        self.assertIsNone(LLMIntegration._try_parse_json('{"a":'))

        # 2) 代码分析消费点：返回带 error 的降级结构，核心字段类型仍合法
        inst = LLMIntegration.__new__(LLMIntegration)
        inst.chat_completion = lambda *a, **k: '{"a":'
        degraded = inst.analyze_code_context("def f():\n    pass\n", "a.py")
        self.assertIsInstance(degraded, dict)
        self.assertTrue(degraded.get("error"), f"必须显式记录降级原因: {degraded}")
        self.assertIsInstance(degraded.get("key_functions"), list)

        # 3) 代码审查消费点：返回「待人工确认」降级评论，而不是空列表
        class _Stub:
            _try_parse_json = staticmethod(LLMIntegration._try_parse_json)

            def chat_completion(self, *a, **k):
                return '{"a":'

        comments = CodeReviewer(llm=_Stub())._call_llm("prompt", "a.py", set(), None)
        self.assertEqual(len(comments), 1, "解析失败必须降级为一条待确认评论")
        self.assertIn("LLM", comments[0]["title"])
        self.assertTrue(comments[0]["detail"])

    # ── F2 空字符串 ──────────────────────────────────────────
    def test_f2_empty_response_writes_placeholder_not_empty_doc(self):
        """空串 → 可识别失败占位 + 失败清单；不得产出 0 字节/只有 front matter 的空壳。"""
        p, result = self._run_docs(_fake_llm_class(""), "empty")

        self.assertEqual(result.get("status"), "partial_failed",
                         f"空响应必须落为部分失败: {result.get('errors')}")
        errs = " ".join(result.get("errors") or [])
        self.assertIn("生成失败", errs)
        self.assertIn("占位", errs)

        readme = os.path.join(p, "docs", "wiki", "README.md")
        self.assertTrue(os.path.isfile(readme), "空响应也必须留可识别占位文档")
        text = self._read(readme)
        self.assertIn("生成失败", text, "占位必须显式写明失败，不得伪装成正常正文")
        body = text.split("---", 2)[-1].strip() if text.startswith("---") else text.strip()
        self.assertTrue(body, "占位文档不得是空壳")

    # ── F3 超时 ─────────────────────────────────────────────
    def test_f3_timeout_retries_are_bounded(self):
        """超时不得无限重试：原始请求 + 最多 2 次重试（共 3 次）后返回结构化错误。"""
        comp = _FakeCompletions(TimeoutError("Request timed out."))
        inst = LLMIntegration(_RealKeyConfig())
        inst.client = SimpleNamespace(chat=SimpleNamespace(completions=comp))
        with mock.patch.object(llm_mod.time, "sleep", lambda *_: None):
            out = inst.chat_completion([{"role": "user", "content": "hi"}])
        self.assertEqual(comp.calls, 3, f"重试次数必须有界，实际 {comp.calls} 次")
        self.assertTrue(LLMIntegration.is_error_response(out))
        self.assertIn("timed out", out)

    def test_f3_timeout_marks_degraded_timeout_or_budget(self):
        """TODO-U: 超时（TimeoutError / 真实 APITimeoutError 文案）未置 timeout_or_budget。

        实测证据：errs = ['...生成失败（LLM 返回空内容）...',
                          'LLM 调用异常: Request timed out.']，
        而 mcp_server.coderef_docs 只按 "超时|timeout|预算|budget" 子串匹配；
        真实 openai.APITimeoutError 的文案是 "Request timed out."，不含 "timeout"，
        导致 degraded_reasons.timeout_or_budget=False。
        """
        _, result = self._run_docs(
            _fake_llm_class(exc=TimeoutError("Request timed out.")), "timeout")
        self.assertEqual(result.get("status"), "partial_failed")
        self.assertTrue((result.get("degraded_reasons") or {}).get("timeout_or_budget"),
                        f"超时必须标记 timeout_or_budget: {result.get('degraded_reasons')}")

    # ── F4 限流 429 ─────────────────────────────────────────
    def test_f4_rate_limit_is_source_error_not_empty_result(self):
        """429 必须作为「源不可用」结构化错误暴露，可与「结果为空」区分。"""
        # 1) 请求层：429 可重试且只重试到上限，最终返回结构化错误串
        comp = _FakeCompletions(_RateLimited("Rate limit exceeded (429)"))
        inst = LLMIntegration(_RealKeyConfig())
        inst.client = SimpleNamespace(chat=SimpleNamespace(completions=comp))
        with mock.patch.object(llm_mod.time, "sleep", lambda *_: None):
            out = inst.chat_completion([{"role": "user", "content": "hi"}])
        self.assertEqual(comp.calls, 3)
        self.assertTrue(LLMIntegration.is_error_response(out),
                        "429 不得被当成正常内容")
        self.assertIn("429", out)

        # 2) 产物层：限流会留下「LLM 调用异常: ...429」这一源不可用痕迹
        _, limited = self._run_docs(
            _fake_llm_class("LLM调用错误: Rate limit exceeded (429)"), "limited")
        limited_errs = " ".join(limited.get("errors") or [])
        self.assertEqual(limited.get("status"), "partial_failed")
        self.assertIn("429", limited_errs, "限流原因必须回传，不得吞掉")
        self.assertIn("LLM 调用异常", limited_errs)

        # 3) 对照：纯空响应必须能区别于源不可用（429）
        #    v5.21.22 起空内容会再重试一次，文案「LLM 返回空内容，重试后仍为空，
        #    已丢弃」；与 429 的区分点＝无 429/Rate limit、明确披露空内容。
        #    观察项：两者共享「LLM 调用异常」前缀，建议开发方为源错误与空结果
        #    使用不同前缀（degraded_reasons 已能区分：空内容 → content_dropped）。
        _, empty = self._run_docs(_fake_llm_class(""), "pure_empty")
        empty_errs = " ".join(empty.get("errors") or [])
        self.assertNotIn("429", empty_errs)
        self.assertNotIn("Rate limit", empty_errs, "空响应不得伪装成限流")
        self.assertIn("空内容", empty_errs, "空响应必须如实说明为空内容")

    # ── F5 返回内容混入脚本 ──────────────────────────────────
    def test_f5_script_in_llm_output_is_escaped_in_html(self):
        """LLM 返回 <script> 时，wiki.html 必须转义，不得形成可执行标签。"""
        evil = "# 模块说明\n\n<script>alert(1)</script>\n\n" + ("这是正文内容。" * 40)
        p, result = self._run_docs(_fake_llm_class(evil), "script")
        self.assertEqual(result.get("status"), "completed", result.get("errors"))

        wiki_html = os.path.join(p, ".coderef", "report", "html", "wiki.html")
        self.assertTrue(os.path.isfile(wiki_html), "docs 必须产出 wiki.html")
        html = self._read(wiki_html)
        self.assertNotIn("<script>alert(1)</script>", html,
                         "wiki.html 不得原样输出可执行脚本")
        self.assertIn("&lt;script&gt;", html, "脚本必须被转义为文本")

        # markdown→HTML 渲染器本身也不得放行原始标签
        rendered = md_to_html("<script>alert(1)</script>")
        self.assertNotIn("<script>alert(1)</script>", rendered)
        self.assertIn("&lt;script&gt;", rendered)

    # ── F6 反向：正常返回不得常驻降级标记 ────────────────────
    def test_f6_normal_response_has_no_degraded_flags(self):
        """正常内容 → completed 且所有降级标记为 false（防止标记常驻 true）。"""
        normal = "# 项目概述\n\n" + ("这是一段正常的项目说明文字，用于验证产出链路。" * 30)
        _, result = self._run_docs(_fake_llm_class(normal), "normal")
        self.assertEqual(result.get("status"), "completed", result.get("errors"))
        self.assertEqual(result.get("errors") or [], [])
        degraded = result.get("degraded_reasons") or {}
        self.assertFalse(degraded.get("timeout_or_budget"))
        self.assertFalse(degraded.get("content_dropped"))


    # ── F8 中间失败被重试覆盖后不得残留 errors（U-96 已修复） ──
    def test_f8_retry_success_must_not_leave_errors(self):
        """中间失败（被重试/门控覆盖）不得进入最终 errors（缺陷记录 U-96）。

        修复前 `_last_llm_error` 是「最后写入者胜」的单字段：中间失败写入后
        不清空，导致全部文档成功仍返回 errors，并让 `generate()` 跳过
        `.last-good` 备份与 `.last-update.json` 写入。
        修复后：`_retry_llm` 成功即清空 + `_summarize_failures` 只在存在
        最终失败文档时才追加诊断（2026-11-16 复测转绿，原 expectedFailure 已摘）。
        """

        class _FlakyOnce:
            """前两次给截断文本（让「模块描述」这类非文档调用最终失败），之后完整。"""

            def __init__(self, *a, **k):
                self._n = 0

            def is_available(self):
                return True

            def reset_budget(self):
                return None

            def is_error_response(self, text):
                return False

            def chat_completion(self, messages, **kwargs):
                self._n += 1
                if self._n <= 2:
                    return "## 概述\n\n这是被截断的半句"
                return ("## 概述\n\n完整正文说明。\n\n"
                        "## 关键实现\n\n- 入口完整可用。\n\n指纹：abc。\n")

        _, result = self._run_docs(_FlakyOnce, "flaky")
        self.assertEqual(result.get("errors") or [], [],
                         "重试成功后不得残留 errors: %r"
                         % (result.get("errors"),))

    # ── F9 截断重试轮数：承诺的 2 轮重试必须可达（U-97 已修复） ──
    def test_f9_truncation_uses_two_retry_rounds(self):
        """前两次返回截断、第三次完整 → 第 3 次调用必须发生（缺陷记录 U-97）。

        修复前 `_llm_ask` 的 `while ... rounds < 2` 在首轮 `_retry_llm` 失败后
        直接 `return ""`，第二轮（压缩提示）永不可达；只会调用 2 次，完整内容
        被丢弃。2026-11-16 二次整改后本轮失败不立即返回，两轮都失败才判失败
        （复测转绿，原 expectedFailure 已摘）。
        """
        from core.wiki_generator import WikiGenerator

        class _ThirdOk:
            def __init__(self):
                self.n = 0

            def is_available(self):
                return True

            def reset_budget(self):
                return None

            def is_error_response(self, t):
                return False

            def chat_completion(self, messages, **kwargs):
                self.n += 1
                if self.n <= 2:
                    return "## 概述\n\n这是被截断的半句"
                return "## 概述\n\n完整正文说明。\n"

        stub = _ThirdOk()
        gen = WikiGenerator(llm_client=stub)
        out = gen._llm_ask("sys", "user")
        self.assertGreaterEqual(stub.n, 3,
                                "截断应有第 3 次生成机会（压缩提示）")
        self.assertTrue(out, "第 3 次完整输出必须被采用")


if __name__ == "__main__":
    unittest.main(verbosity=2)
