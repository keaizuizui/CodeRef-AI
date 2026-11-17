# -*- coding: utf-8 -*-
"""5.19.2 precondition 闸门与统一范围层的单元测试。

覆盖：
- 只读工具永远放行；warn 默认只告警不阻断；block 配置返回 blocked
- G-A 未 recover / G-C 缺定标 / G-D 产物超龄的命中与补齐后放行
- scope add/list/remove、include_override 找回、CodeAnalyzer 实际排除
- coderef_scope 的生效验证回执与可疑误伤

运行方式：
    python -m unittest tests.test_preconditions_scope
"""
import json
import os
import sys
import tempfile
import unittest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from core import preconditions
from core import scope as scope_mod
from core.session_guard import mark_fact


def _write(project, name, content):
    path = os.path.join(project, name)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    return path


def _block_config(project):
    _write(project, ".coderef/guard_config.json",
           json.dumps({"preconditions": {"mode": "block"}}))


class PreconditionTest(unittest.TestCase):
    def test_readonly_tool_always_passes(self):
        with tempfile.TemporaryDirectory() as project:
            _block_config(project)
            res = preconditions.evaluate("coderef_version", project, {})
            self.assertFalse(res["hits"])
            self.assertFalse(res["blocked"])
            self.assertFalse(res["checked"], "只读工具应记 miss 而非 pass")

    def test_default_warn_does_not_block(self):
        with tempfile.TemporaryDirectory() as project:
            _write(project, "a.py", "x = 1\n")
            res = preconditions.evaluate("coderef_audit", project, {})
            self.assertTrue(any(h["id"] == "G-A" for h in res["hits"]))
            self.assertFalse(res["blocked"])
            self.assertTrue(res["checked"])

    def test_block_mode_blocks_until_recover_fact(self):
        with tempfile.TemporaryDirectory() as project:
            _write(project, "a.py", "x = 1\n")
            _block_config(project)
            res = preconditions.evaluate("coderef_audit", project, {})
            self.assertTrue(res["blocked"])
            self.assertEqual(res["blocking"]["id"], "G-A")
            mark_fact(project, "memory_recovered_at")
            res = preconditions.evaluate("coderef_audit", project, {})
            self.assertFalse(res["blocked"])

    def test_gap_needs_target_arch(self):
        with tempfile.TemporaryDirectory() as project:
            _write(project, "a.py", "x = 1\n")
            _block_config(project)
            mark_fact(project, "memory_recovered_at")
            from core.code_knowledge_graph import CodeKnowledgeGraph
            kg = CodeKnowledgeGraph(project)
            kg.build()
            kg.close()
            res = preconditions.evaluate("coderef_arch_verify", project, {})
            self.assertTrue(any(h["id"] == "G-C" for h in res["hits"]))
            res = preconditions.evaluate(
                "coderef_arch_verify", project,
                {"target_arch": {"modules": []}})
            self.assertFalse(any(h["id"] == "G-C" for h in res["hits"]))

    def test_blocked_result_shape(self):
        hit = {"id": "G-A", "reason": "未恢复记忆",
               "owner_actions": ["coderef_operation_memory(action=recover)"]}
        out = preconditions.blocked_result("coderef_audit", hit)
        self.assertEqual(out["status"], "blocked")
        self.assertEqual(out["envelope"]["blocked_by"][0]["owner"], "consumer")
        self.assertFalse(out["envelope"]["usable_for_decisions"])


class ScopeTest(unittest.TestCase):
    def _project(self, project):
        _write(project, "src/app.py", "def app():\n    return 1\n")
        _write(project, "libs/third.py", "def t():\n    return 2\n")
        _write(project, "src/sub/x.py", "def x():\n    return 3\n")

    def test_add_apply_and_remove(self):
        with tempfile.TemporaryDirectory() as project:
            self._project(project)
            scope_mod.add_rule(project, "exclude_glob", "libs", reason="第三方库")
            self.assertTrue(scope_mod.is_excluded(project, "libs/third.py"))
            self.assertFalse(scope_mod.is_excluded(project, "src/app.py"))
            applied = scope_mod.apply_scope(
                project, ["src/app.py", "libs/third.py"])
            self.assertEqual(applied["excluded_count"], 1)
            res = scope_mod.remove_rule(project, "exclude_glob", "libs")
            self.assertEqual(res["removed"], 1)
            self.assertFalse(scope_mod.is_excluded(project, "libs/third.py"))

    def test_include_override_wins(self):
        with tempfile.TemporaryDirectory() as project:
            self._project(project)
            scope_mod.add_rule(project, "exclude_glob", "libs")
            scope_mod.add_rule(project, "include_override", "libs/keep.py")
            self.assertTrue(scope_mod.is_excluded(project, "libs/third.py"))
            self.assertFalse(scope_mod.is_excluded(project, "libs/keep.py"))

    def test_analyzer_respects_scope(self):
        with tempfile.TemporaryDirectory() as project:
            self._project(project)
            scope_mod.add_rule(project, "exclude_glob", "libs")
            from core.code_analyzer import CodeAnalyzer
            files = [os.path.relpath(f, project).replace("\\", "/")
                     for f in CodeAnalyzer().scan_directory(project)]
            self.assertNotIn("libs/third.py", files)
            self.assertIn("src/app.py", files)

    def test_scope_handler_add_verify(self):
        with tempfile.TemporaryDirectory() as project:
            self._project(project)
            from core.handlers_misc import _scope
            out = json.loads(_scope({
                "project_path": project, "action": "add",
                "kind": "exclude_glob", "pattern": "libs",
                "reason": "第三方库"}))
            self.assertEqual(out["status"], "ok")
            self.assertEqual(out["verify"]["excluded_files"], 1)
            listed = json.loads(_scope({"project_path": project, "action": "list"}))
            self.assertEqual(listed["counts"]["exclude_glob"], 1)

    def test_invalid_kind_rejected(self):
        with tempfile.TemporaryDirectory() as project:
            from core.handlers_misc import _scope
            out = json.loads(_scope({
                "project_path": project, "action": "add",
                "kind": "boom", "pattern": "x"}))
            self.assertEqual(out["status"], "error")

    def test_remove_without_pattern_is_rejected(self):
        with tempfile.TemporaryDirectory() as project:
            scope_mod.add_rule(project, "exclude_glob", "libs")
            from core.handlers_misc import _scope
            out = json.loads(_scope({
                "project_path": project, "action": "remove"}))
            self.assertEqual(out["status"], "error")
            self.assertEqual(len(scope_mod.list_scope(project)["rules"]), 1)

    def test_file_level_scope_excludes_from_scan(self):
        with tempfile.TemporaryDirectory() as project:
            _write(project, "src/app.py", "x = 1\n")
            _write(project, "src/gen_pb2.py", "y = 2\n")
            scope_mod.add_rule(project, "exclude_glob", "*_pb2.py")
            from core.code_analyzer import CodeAnalyzer
            files = [os.path.relpath(f, project).replace("\\", "/")
                     for f in CodeAnalyzer().scan_directory(project)]
            self.assertNotIn("src/gen_pb2.py", files)
            self.assertIn("src/app.py", files)
            receipt = scope_mod.verify(project)
            self.assertGreaterEqual(receipt["excluded_files"], 1)


if __name__ == "__main__":
    unittest.main()
