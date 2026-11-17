# -*- coding: utf-8 -*-
"""5.19.4 LLM 意图层（形态 B）的单元测试。

覆盖：
- 无 Key：硬阻断并降级回形态 A（intent 枚举），不猜不编
- 有 Key：自然语言 → 链枚举，plan 卡标 mode=llm
- 低置信度：needs_confirmation + 候选，不擅自出计划
- 不可解析返回：诚实错误
- 确认门：intent_text 不能绕过 plan 直接 execute

运行方式：
    python -m unittest tests.test_intent_router
"""
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from core import dispatcher
from core import intent_router


class _FakeLLM:
    available = True
    response = '{"intent": "governance", "confidence": 0.92, "reason": "要治理架构"}'

    @classmethod
    def is_error_response(cls, text):
        return bool(text) and text.startswith("LLM调用错误:")

    def __init__(self, config=None):
        pass

    def is_available(self):
        return self.available

    def chat_completion(self, messages, **kwargs):
        return self.response


class IntentRouterTest(unittest.TestCase):
    def test_no_key_is_blocked_with_fallback(self):
        with mock.patch("core.llm_integration.LLMIntegration",
                        type("X", (_FakeLLM,), {"available": False})):
            out = intent_router.classify("帮我看看架构")
        self.assertEqual(out["status"], "blocked")
        self.assertEqual(out["mode"], "rule")
        self.assertIn("fallback", out)

    def test_ok(self):
        with mock.patch("core.llm_integration.LLMIntegration", _FakeLLM):
            out = intent_router.classify("帮我做架构治理")
        self.assertEqual(out["status"], "ok")
        self.assertEqual(out["intent"], "governance")

    def test_low_confidence_needs_confirmation(self):
        fake = type("Y", (_FakeLLM,), {
            "response": '{"intent": "asset", "confidence": 0.2}'})
        with mock.patch("core.llm_integration.LLMIntegration", fake):
            out = intent_router.classify("随便看看")
        self.assertEqual(out["status"], "needs_confirmation")
        self.assertTrue(out["candidates"])

    def test_invalid_json_is_honest_error(self):
        fake = type("Z", (_FakeLLM,), {"response": "我不知道"})
        with mock.patch("core.llm_integration.LLMIntegration", fake):
            out = intent_router.classify("???")
        self.assertEqual(out["status"], "error")
        self.assertIn("不可解析", out["error"])

    def test_plan_uses_llm_route(self):
        with tempfile.TemporaryDirectory() as project, \
                mock.patch("core.llm_integration.LLMIntegration", _FakeLLM):
            card = dispatcher.plan("probe", project, intent_text="治理一下架构")
            self.assertEqual(card["status"], "ok")
            self.assertEqual(card["intent"], "governance")
            self.assertEqual(card["mode"], "llm")
            self.assertEqual(card["router"]["status"], "ok")

    def test_plan_no_key_falls_back_without_plan(self):
        with tempfile.TemporaryDirectory() as project, \
                mock.patch("core.llm_integration.LLMIntegration",
                           type("X", (_FakeLLM,), {"available": False})):
            card = dispatcher.plan("probe", project, intent_text="治理一下架构")
            self.assertEqual(card["status"], "blocked")
            self.assertIn("fallback", card)

    def test_execute_requires_confirmed_plan(self):
        from core.mcp_server import Server
        with tempfile.TemporaryDirectory() as project:
            out = json.loads(Server()._run("coderef_execute", {
                "project_path": project, "intent_text": "治理架构"}))
            self.assertEqual(out["status"], "blocked")
            self.assertIn("coderef_plan", json.dumps(out, ensure_ascii=False))


if __name__ == "__main__":
    unittest.main()
