# -*- coding: utf-8 -*-
"""5.19.7 纪律复盘（dispatcher remind）的单元测试。

覆盖：
- 无事实时如实报告 forgotten / unknown，不把未知伪装成通过
- 补齐 recover / change_guard / 图谱后 forgotten 清空
- dispatcher 的 remind 链为进程内 check 步骤，不调用 MCP 工具
- 经 Server 入口 execute(remind) → dispatch_report 可拿到信封

运行方式：
    python -m unittest tests.test_discipline_recall
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from core import discipline_recall
from core import dispatcher


def _write(project, name, content):
    path = os.path.join(project, name)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    return path


def _git(project, *args):
    subprocess.run(["git", *args], cwd=project, check=True,
                   capture_output=True, text=True)


class RecallTest(unittest.TestCase):
    def test_reports_forgotten_and_unknown(self):
        with tempfile.TemporaryDirectory() as project:
            _write(project, "a.py", "x = 1\n")
            out = discipline_recall.recall(project)
            ids = {f["id"] for f in out["forgotten"]}
            self.assertIn("memory_recovered", ids)
            self.assertIn("knowledge_graph", ids)
            self.assertEqual({u["id"] for u in out["unknown"]}, {"change_guard"})
            self.assertFalse(out["envelope"]["usable_for_decisions"])
            self.assertIn("你忘了", out["verdict"])

    def test_passes_after_actions(self):
        with tempfile.TemporaryDirectory() as project:
            _write(project, ".gitignore", ".coderef/\ncache/\n")
            _write(project, "a.py", "x = 1\n")
            _git(project, "init")
            _git(project, "-c", "user.name=CodeRef",
                 "-c", "user.email=coderef@local", "add", ".")
            _git(project, "-c", "user.name=CodeRef",
                 "-c", "user.email=coderef@local", "commit", "-m", "init")

            from core.session_guard import (git_fingerprint, mark_fact,
                                            record_guard)
            fp = git_fingerprint(project)
            record_guard(project, fp, {"summary": "ok", "findings": []})
            mark_fact(project, "memory_recovered_at")
            from core.code_knowledge_graph import CodeKnowledgeGraph
            kg = CodeKnowledgeGraph(project)
            kg.build()
            kg.close()

            out = discipline_recall.recall(project)
            ids = {f["id"] for f in out["forgotten"]}
            self.assertNotIn("memory_recovered", ids)
            self.assertNotIn("change_guard", ids)
            self.assertNotIn("knowledge_graph", ids)
            self.assertTrue(out["envelope"]["usable_for_decisions"])


class RemindChainTest(unittest.TestCase):
    def test_plan_and_execute_check_step(self):
        with tempfile.TemporaryDirectory() as project:
            card = dispatcher.plan("remind", project)
            self.assertEqual(card["status"], "ok")
            step = card["steps"][0]
            self.assertEqual(step["check"], "discipline")
            self.assertEqual(step["tool"], "internal:discipline")

            def runner(tool, args):
                raise AssertionError("check 步骤不应调用 MCP 工具")

            res = dispatcher.execute(card, runner, project, task_id="tremind")
            self.assertEqual(res["status"], "completed")
            rep = dispatcher.report(project, "tremind")
            self.assertEqual(rep["status"], "ok")
            self.assertEqual(rep["step_index"][0]["summary"]["status"], "ok")
            self.assertIn("forgotten", rep["step_index"][0]["summary"])
            self.assertIn("envelope", rep)

    def test_server_execute_remind(self):
        from core.mcp_server import Server
        with tempfile.TemporaryDirectory() as project:
            s = Server()
            out = json.loads(s._run("coderef_execute", {
                "project_path": project, "intent": "remind"}))
            self.assertEqual(out["status"], "completed")
            rep = json.loads(s._run("coderef_dispatch_report", {
                "project_path": project, "task_id": out["task_id"]}))
            self.assertEqual(rep["status"], "ok")
            self.assertIn("verdict", rep["envelope"])


if __name__ == "__main__":
    unittest.main()
