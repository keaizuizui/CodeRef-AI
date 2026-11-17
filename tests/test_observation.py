# -*- coding: utf-8 -*-
"""5.19.5 观测层与违规注入的单元测试。

覆盖：
- audit_log 落盘/读取/指标聚合（gate / blocked / memory / freshness / scope / dispatcher）
- 违规注入：block 模式下未 recover 直接 audit → blocked 且日志记录 G-A
- 重工具调用落盘 gate 判定与记忆注入事实
- coderef_dispatch_metrics 工具可调用且字段完整

运行方式：
    python -m unittest tests.test_observation
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

from core import audit_log


def _write(project, name, content):
    path = os.path.join(project, name)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    return path


class AuditLogTest(unittest.TestCase):
    def test_record_read_metrics(self):
        with tempfile.TemporaryDirectory() as project:
            audit_log.record(project, {
                "tool": "coderef_audit",
                "gate": {"result": "hit", "hits": ["G-A"], "mode": "warn"},
                "memory_digest": {"injected": True, "available": True, "items": 3},
                "freshness": {"code_newer": True, "stale_reason": "代码比产物新",
                              "refresh_action": "coderef_audit"},
                "scope": {"rules": 2},
            })
            audit_log.record(project, {
                "tool": "coderef_query",
                "gate": {"result": "pass", "hits": [], "mode": "warn"},
                "blocked": {"gate": "G-A", "reason": "未恢复记忆"},
            })
            _write(project, ".coderef/dispatch/t1.json",
                   json.dumps({"status": "completed"}))
            _write(project, ".coderef/dispatch/t2.json",
                   json.dumps({"status": "paused"}))
            self.assertEqual(len(audit_log.read(project)), 2)
            m = audit_log.metrics(project)
            self.assertEqual(m["total_calls"], 2)
            self.assertEqual(m["gate"]["checked"], 2)
            self.assertEqual(m["gate"]["hits"], 1)
            self.assertEqual(m["gate"]["hit_rate"], 0.5)
            self.assertEqual(m["blocked"]["by_gate"], {"G-A": 1})
            self.assertEqual(m["memory"]["hit_rate"], 1.0)
            self.assertEqual(m["freshness"]["code_newer_calls"], 1)
            self.assertEqual(m["scope"]["max_rules"], 2)
            self.assertEqual(m["dispatcher"]["by_status"],
                             {"completed": 1, "paused": 1})

    def test_metrics_empty_is_honest(self):
        with tempfile.TemporaryDirectory() as project:
            m = audit_log.metrics(project)
            self.assertEqual(m["total_calls"], 0)
            self.assertEqual(m["gate"]["hit_rate"], 0.0)
            self.assertIn("missing", m["note"])


class ViolationInjectionTest(unittest.TestCase):
    def test_blocked_call_is_logged(self):
        from core.mcp_server import Server
        with tempfile.TemporaryDirectory() as project:
            _write(project, "a.py", "x = 1\n")
            _write(project, ".coderef/guard_config.json",
                   json.dumps({"preconditions": {"mode": "block"}}))
            out = json.loads(Server()._run(
                "coderef_audit", {"project_path": project}))
            self.assertEqual(out["status"], "blocked")
            self.assertEqual(out["envelope"]["blocked_by"][0]["owner"], "consumer")
            entries = audit_log.read(project)
            self.assertEqual(len(entries), 1)
            self.assertEqual(entries[0]["gate"]["result"], "hit")
            self.assertIn("G-A", entries[0]["gate"]["hits"])
            self.assertEqual(entries[0]["blocked"]["gate"], "G-A")

    def test_heavy_call_logs_gate_and_digest(self):
        from core.mcp_server import Server
        with tempfile.TemporaryDirectory() as project:
            _write(project, "a.py", "x = 1\n")
            s = Server()
            with mock.patch.object(
                    s, "_run_inner",
                    return_value=json.dumps({"status": "completed",
                                             "envelope": {"freshness": {
                                                 "code_newer": True,
                                                 "stale_reason": "代码比产物新"}}})):
                s._run("coderef_query", {"project_path": project,
                                         "query_type": "stats"})
            entries = audit_log.read(project)
            self.assertEqual(len(entries), 1)
            e = entries[0]
            self.assertEqual(e["tool"], "coderef_query")
            self.assertEqual(e["gate"]["result"], "hit")
            self.assertTrue(e["memory_digest"]["injected"])
            self.assertTrue(e["freshness"]["code_newer"])

    def test_light_tool_logs_scope(self):
        from core.mcp_server import Server
        from core import scope as scope_mod
        with tempfile.TemporaryDirectory() as project:
            _write(project, "a.py", "x = 1\n")
            scope_mod.add_rule(project, "exclude_glob", "libs")
            Server()._run("coderef_version", {"project_path": project})
            entries = audit_log.read(project)
            self.assertEqual(entries[0]["tool"], "coderef_version")
            self.assertEqual(entries[0]["scope"]["rules"], 1)
            self.assertEqual(entries[0]["gate"]["result"], "miss",
                             "只读工具应记录 miss，不应伪造成 pass")

    def test_metrics_tool_via_server(self):
        from core.mcp_server import Server
        with tempfile.TemporaryDirectory() as project:
            audit_log.record(project, {"tool": "coderef_audit",
                                       "gate": {"result": "pass", "hits": []}})
            out = json.loads(Server()._run(
                "coderef_dispatch_metrics", {"project_path": project}))
            self.assertEqual(out["status"], "ok")
            self.assertEqual(out["total_calls"], 1)
            self.assertEqual(out["tool"], "coderef_dispatch_metrics")


if __name__ == "__main__":
    unittest.main()
