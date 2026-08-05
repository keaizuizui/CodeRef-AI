# -*- coding: utf-8 -*-
"""
回归测试 —— core/code_review.py 代码审查模块。

覆盖：
1. parse_diff：正确解析 git diff 文本，提取变更单元 / hunk 行号 / 变更行集合。
2. CodeReviewer.review（mode=diff）：给定 diff 或 changed_files，产出结构化评论。
3. CodeReviewer.review（mode=full）：全量扫描 + 分块 batching。
4. 维度过滤：dimensions 参数只返回对应维度的评论。
5. 降级路径：LLM 不可用 / 返回非法 JSON 时，产生 pending-human 待人工确认评论，
   绝不静默吞掉。

说明：
- 仅使用 unittest 标准库，不依赖 pytest。
- LLM 通过 mock 打补丁（不联网、不消耗 token）。
- 在文件顶部把项目根目录插入 sys.path，确保可独立运行。
"""

import os
import sys
import unittest
from types import SimpleNamespace
from unittest import mock

# ── 使测试可独立运行：把项目根目录插入 sys.path ──
PROJECT_ROOT = r"C:\Users\30822\Desktop\1111\Coderef-Ai\Coderef-Ai-master"
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)
if os.getcwd() != PROJECT_ROOT:
    try:
        os.chdir(PROJECT_ROOT)
    except OSError:
        pass


SAMPLE_DIFF = """diff --git a/core/foo.py b/core/foo.py
index 1234567..89abcde 100644
--- a/core/foo.py
+++ b/core/foo.py
@@ -298,7 +302,8 @@ def process():
     data = load()
-    return data[0]
+    if not data:
+        return None
+    return data[0]
diff --git a/README.md b/README.md
new file mode 100644
index 0000000..1111111
--- /dev/null
+++ b/README.md
@@ -0,0 +1,3 @@
+# Demo
+示例项目
"""


class FakeLLM:
    """Mock LLM：返回可控的 JSON 评论数组，用于验证解析与结构。"""

    def __init__(self, payload=None, available=True):
        self.payload = payload
        self.available = available
        # 模拟已配置 API Key 的 LLM 客户端（code_review 的可用性检查同时要求 client 与 api_key）
        self.config = SimpleNamespace(api_key="test-key" if available else "")

    @property
    def client(self):
        return object() if self.available else None

    def chat_completion(self, messages, **kwargs):
        import json
        return json.dumps(self.payload, ensure_ascii=False) if self.payload is not None else ""

    def _try_parse_json(self, text):
        import json as _json
        try:
            return _json.loads(text) if text else None
        except Exception:
            return None


class TestParseDiff(unittest.TestCase):
    """parse_diff 解析 git diff"""

    def test_parses_hunks_and_changed_lines(self):
        from core.code_review import parse_diff
        units = parse_diff(SAMPLE_DIFF)
        # 两个文件 → 两个变更单元
        self.assertEqual(len(units), 2)
        by_file = {u["file"]: u for u in units}
        self.assertIn("core/foo.py", by_file)
        self.assertIn("README.md", by_file)
        foo = by_file["core/foo.py"]
        # 变更行集合包含所有新增行：303（if not data）、304（return None）、305（return data[0]）
        self.assertEqual(foo["changed_lines"], {303, 304, 305})
        # hunk 新文件起始行号正确
        self.assertTrue(any(h["new_start"] == 302 for h in foo["hunks"]))

    def test_empty_diff_returns_empty(self):
        from core.code_review import parse_diff
        self.assertEqual(parse_diff(""), [])


class TestCodeReviewer(unittest.TestCase):
    """CodeReviewer.review 主流程"""

    def test_diff_mode_with_mock_llm(self):
        from core.code_review import CodeReviewer
        payload = [{
            "file": "core/foo.py", "line": 305, "severity": "high",
            "dimension": "bug", "title": "索引越界风险",
            "detail": "空列表时访问 [0]", "suggestion": "先判空", "evidence": "pending-human",
        }]
        reviewer = CodeReviewer(llm=FakeLLM(payload=payload))
        r = reviewer.review(PROJECT_ROOT, mode="diff", diff=SAMPLE_DIFF)
        self.assertEqual(r["mode"], "diff")
        # SAMPLE_DIFF 含 2 个变更单元，每个单元各产出一条评论
        self.assertEqual(len(r["comments"]), 2)
        c = r["comments"][0]
        self.assertEqual(c["file"], "core/foo.py")
        self.assertEqual(c["severity"], "high")
        self.assertEqual(c["dimension"], "bug")
        # 评论结构字段完整
        for field in ("title", "detail", "suggestion", "evidence"):
            self.assertIn(field, c)

    def test_dimensions_filter(self):
        from core.code_review import CodeReviewer
        payload = [
            {"file": "a.py", "line": 1, "severity": "low", "dimension": "bug", "title": "t1"},
            {"file": "a.py", "line": 2, "severity": "low", "dimension": "security", "title": "t2"},
        ]
        reviewer = CodeReviewer(llm=FakeLLM(payload=payload))
        r = reviewer.review(PROJECT_ROOT, mode="diff", diff=SAMPLE_DIFF,
                            dimensions=["security"])
        dims = {c["dimension"] for c in r["comments"]}
        self.assertEqual(dims, {"security"})

    def test_full_mode_returns_structure(self):
        from core.code_review import CodeReviewer
        payload = [
            {"file": "core/code_review.py", "line": 1, "severity": "medium",
             "dimension": "maintainability", "title": "t1"},
        ]
        reviewer = CodeReviewer(llm=FakeLLM(payload=payload))
        r = reviewer.review(PROJECT_ROOT, mode="full")
        self.assertEqual(r["mode"], "full")
        self.assertIn("comments", r)
        self.assertIn("summary", r)

    def test_llm_unavailable_degrades(self):
        from core.code_review import CodeReviewer
        reviewer = CodeReviewer(llm=FakeLLM(available=False))
        r = reviewer.review(PROJECT_ROOT, mode="diff", diff=SAMPLE_DIFF)
        # summary 明确说明 LLM 不可用
        self.assertIn("LLM", r["summary"])
        # 仍产出一条待人工确认占位评论，不静默吞掉
        self.assertTrue(len(r["comments"]) >= 1)
        self.assertEqual(r["comments"][0]["evidence"], "pending-human")

    def test_llm_invalid_json_degrades_per_unit(self):
        from core.code_review import CodeReviewer
        reviewer = CodeReviewer(llm=FakeLLM(payload=None))  # 返回空串 → 解析失败
        r = reviewer.review(PROJECT_ROOT, mode="diff", diff=SAMPLE_DIFF)
        # 每个变更单元都应有降级评论，且 evidence=pending-human
        self.assertTrue(len(r["comments"]) >= 1)
        for c in r["comments"]:
            self.assertEqual(c["evidence"], "pending-human")


if __name__ == "__main__":
    unittest.main()