# -*- coding: utf-8 -*-
"""Test for core.memory_digest —— 操作记忆摘要与一次注入控制。

作者：CodeRef-AI Team
"""

import unittest
from unittest.mock import patch

from core import memory_digest


_RECOVER_OK = {
    "status": "ok",
    "tool": "recover",
    "project_path": "d:/demo",
    "last_sync": "2026-11-09T10:00:00",
    "llm_available": True,
    "env_tools": [{"name": "git", "path": "/usr/bin/git",
                   "location": "env", "note": "版本控制"}],
    "decisions": [{"summary": "统一使用 loguru 记录日志", "source": "docs"}],
    "conventions": [{"summary": "提交身份用临时 git -c 指定", "source": "AGENTS.md"}],
    "pitfalls": [],
    "pending_items": [{"category": "decision", "summary": "待补充发布流程约定"}],
    "pending_counts": {"decision": 1, "convention": 0, "pitfall": 0},
    "hint": "先看 env_tools",
}


class MemoryDigestTest(unittest.TestCase):
    def setUp(self):
        # 进程内注入状态是模块级的，逐用例清空避免相互影响
        with memory_digest._injected_lock:
            memory_digest._injected.clear()

    # 1. 正常返回结构化键 → 有 items 且 available=True
    def test_build_digest_from_structured_keys(self):
        with patch.object(memory_digest.operation_memory, "recover",
                          return_value=dict(_RECOVER_OK)):
            digest = memory_digest.build_digest("d:/demo")
        self.assertTrue(digest["available"])
        self.assertEqual(digest["recovered_from"], "operation_memory")
        self.assertEqual(digest["updated_at"], "2026-11-09T10:00:00")
        self.assertTrue(digest["items"])
        self.assertEqual(digest["error"], "")
        texts = [it["text"] for it in digest["items"]]
        self.assertIn("统一使用 loguru 记录日志", texts)
        self.assertIn("提交身份用临时 git -c 指定", texts)
        self.assertIn("待补充发布流程约定", texts)
        self.assertTrue(all(isinstance(it["kind"], str) for it in digest["items"]))

    # 2. recover 抛异常 → available=False、error 非空、不抛
    def test_build_digest_swallows_exception(self):
        with patch.object(memory_digest.operation_memory, "recover",
                          side_effect=RuntimeError("磁盘读取失败")):
            digest = memory_digest.build_digest("d:/demo")
        self.assertFalse(digest["available"])
        self.assertEqual(digest["items"], [])
        self.assertIn("磁盘读取失败", digest["error"])

    # 2b. 记忆未建立（status=error）→ available=False、error 带原因
    def test_build_digest_uninitialized_memory(self):
        with patch.object(memory_digest.operation_memory, "recover",
                          return_value={"status": "error",
                                        "message": "操作记忆尚未同步"}):
            digest = memory_digest.build_digest("d:/demo")
        self.assertFalse(digest["available"])
        self.assertEqual(digest["items"], [])
        self.assertIn("尚未同步", digest["error"])

    # 2b. source=dream 时语义字段由梦摘要注入，recover 兼容字段不得重复
    def test_dream_source_skips_duplicate_semantic_items(self):
        data = dict(_RECOVER_OK)
        data["source"] = "dream"
        with patch.object(memory_digest.operation_memory, "recover",
                          return_value=data):
            digest = memory_digest.build_digest("d:/demo")
        texts = [it["text"] for it in digest["items"]]
        self.assertNotIn("统一使用 loguru 记录日志", texts)
        self.assertNotIn("提交身份用临时 git -c 指定", texts)
        # 环境事实仍然保留
        self.assertTrue(any("git" in t for t in texts))

    # 3. 长文本被截断且 truncated=True
    def test_long_text_truncated(self):
        long_summary = "很长的约定" * 100
        data = dict(_RECOVER_OK)
        data["conventions"] = [{"summary": long_summary} for _ in range(3)]
        with patch.object(memory_digest.operation_memory, "recover",
                          return_value=data):
            digest = memory_digest.build_digest("d:/demo", max_tokens=100)
        self.assertTrue(digest["truncated"])
        self.assertTrue(digest["items"])
        for item in digest["items"]:
            self.assertLessEqual(len(item["text"]), 200)
        # 预算 max_chars = max(200, 100*3) = 300，总字符数不应超预算
        total = sum(len(it["text"]) for it in digest["items"])
        self.assertLessEqual(total, 300)

    # 4. inject_once 第一次返回 digest，第二次 None
    def test_inject_once_only_first_time(self):
        with patch.object(memory_digest.operation_memory, "recover",
                          return_value=dict(_RECOVER_OK)):
            first = memory_digest.inject_once("d:/demo")
            second = memory_digest.inject_once("d:/demo")
        self.assertIsNotNone(first)
        self.assertTrue(first["available"])
        self.assertIsNone(second)

    # 4b. 记忆为空时首次仍返回 available=False 的摘要，而不是 None
    def test_inject_once_empty_memory_returns_honest_digest(self):
        with patch.object(memory_digest.operation_memory, "recover",
                          return_value={"status": "error",
                                        "message": "操作记忆尚未同步"}):
            digest = memory_digest.inject_once("d:/empty")
        self.assertIsNotNone(digest)
        self.assertFalse(digest["available"])
        self.assertTrue(digest["error"])
        self.assertFalse(memory_digest.should_inject("d:/empty"))

    # 5. 不同项目互不影响
    def test_projects_isolated(self):
        self.assertTrue(memory_digest.should_inject("d:/p1"))
        self.assertTrue(memory_digest.should_inject("d:/p2"))
        memory_digest.mark_injected("d:/p1")
        self.assertFalse(memory_digest.should_inject("d:/p1"))
        self.assertTrue(memory_digest.should_inject("d:/p2"))
        with patch.object(memory_digest.operation_memory, "recover",
                          return_value=dict(_RECOVER_OK)):
            self.assertIsNone(memory_digest.inject_once("d:/p1"))
            self.assertIsNotNone(memory_digest.inject_once("d:/p2"))


if __name__ == "__main__":
    unittest.main()
