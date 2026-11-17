# -*- coding: utf-8 -*-
"""5.19.3 dispatcher 形态 A 的单元测试。

覆盖：
- 计划卡：意图枚举、步骤/预计数、落盘 plan 文件、非法意图/缺失链的诚实错误
- 执行：步骤推进、轨迹落盘、最终摘要信封与上下文节省比
- 闸门：命中自动补齐（G-A 先 recover）、G-C 暂停等用户决策
- 降级：dispatcher 不可用时返回回退建议，不影响裸工具
- Server 入口：coderef_plan / coderef_dispatch_status 可调用

运行方式：
    python -m unittest tests.test_dispatcher
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


def _ok(tool):
    return json.dumps({
        "status": "completed", "tool": tool,
        "envelope": {"verdict": f"{tool} ok", "usable_for_decisions": True,
                     "limitations": ["KL-03"],
                     "freshness": {"code_newer": False, "stale_reason": ""}},
    }, ensure_ascii=False)


class PlanTest(unittest.TestCase):
    def test_plan_probe(self):
        with tempfile.TemporaryDirectory() as project:
            card = dispatcher.plan("probe", project)
            self.assertEqual(card["status"], "ok")
            self.assertEqual(card["intent"], "probe")
            self.assertGreaterEqual(card["estimated_tool_count"], 3)
            self.assertTrue(os.path.exists(
                dispatcher._plan_path(project, card["plan_id"])))

    def test_unknown_intent(self):
        with tempfile.TemporaryDirectory() as project:
            card = dispatcher.plan("boom", project)
            self.assertEqual(card["status"], "error")
            self.assertIn("未知意图", card["error"])

    def test_chain_file_missing_fallback(self):
        with tempfile.TemporaryDirectory() as project, \
                mock.patch.object(dispatcher, "CHAINS_FILE",
                                  os.path.join(project, "missing.yaml")):
            card = dispatcher.plan("probe", project)
            self.assertEqual(card["status"], "error")
            self.assertIn("fallback", card)


class ExecuteTest(unittest.TestCase):
    def test_execute_and_report(self):
        with tempfile.TemporaryDirectory() as project:
            card = dispatcher.plan("probe", project)
            calls = []

            def runner(tool, args):
                calls.append(tool)
                return _ok(tool)

            with mock.patch("core.preconditions.evaluate",
                            return_value={"hits": [], "blocked": False}):
                res = dispatcher.execute(card, runner, project, task_id="t1")
            self.assertEqual(res["status"], "completed")
            self.assertEqual(calls, [s["tool"] for s in card["steps"]])
            trace = json.load(open(dispatcher._trace_path(project, "t1"),
                                   encoding="utf-8"))
            self.assertEqual(trace["status"], "completed")
            self.assertEqual(len(trace["steps"]), len(card["steps"]))

            rep = dispatcher.report(project, "t1")
            self.assertEqual(rep["status"], "ok")
            self.assertIn("KL-03", rep["envelope"]["limitations"])
            self.assertEqual(rep["envelope"]["freshness"]["code_newer"], False)
            self.assertGreaterEqual(rep["context"]["raw_chars_total"], 0)

    def test_gate_auto_remediation(self):
        with tempfile.TemporaryDirectory() as project:
            card = {"plan_id": "p", "intent": "probe",
                    "steps": [{"id": "version", "tool": "coderef_version"}]}
            calls = []

            def runner(tool, args):
                calls.append(tool)
                return _ok(tool)

            gate = {"hits": [{"id": "G-A", "title": "未恢复操作记忆",
                              "reason": "no recover",
                              "owner_actions": ["coderef_operation_memory(action=recover)"]}],
                    "blocked": False}
            with mock.patch("core.preconditions.evaluate", return_value=gate):
                res = dispatcher.execute(card, runner, project, task_id="t2")
            self.assertEqual(res["status"], "completed")
            self.assertEqual(calls, ["coderef_operation_memory",
                                     "coderef_version"])
            trace = json.load(open(dispatcher._trace_path(project, "t2"),
                                   encoding="utf-8"))
            self.assertEqual(trace["gates"][0]["gate"], "G-A")

    def test_gate_c_pauses_for_user(self):
        with tempfile.TemporaryDirectory() as project:
            card = {"plan_id": "p", "intent": "governance",
                    "steps": [{"id": "verify", "tool": "coderef_arch_verify"}]}
            gate = {"hits": [{"id": "G-C", "title": "尚未设置目标架构",
                              "reason": "no target_arch", "owner_actions": []}],
                    "blocked": False}
            with mock.patch("core.preconditions.evaluate", return_value=gate):
                res = dispatcher.execute(card, lambda t, a: _ok(t), project,
                                         task_id="t3")
            self.assertEqual(res["status"], "paused")
            self.assertEqual(res["paused"]["gate"], "G-C")
            rep = dispatcher.report(project, "t3")
            self.assertEqual(rep["status"], "paused")
            self.assertFalse(rep["envelope"]["usable_for_decisions"])

    def test_requires_user_step_pauses(self):
        with tempfile.TemporaryDirectory() as project:
            card = {"plan_id": "p", "intent": "governance",
                    "steps": [{"id": "gap", "tool": "coderef_arch_gap",
                               "requires_user": True, "decision": "确认定标"}]}
            with mock.patch("core.preconditions.evaluate",
                            return_value={"hits": [], "blocked": False}):
                res = dispatcher.execute(card, lambda t, a: _ok(t), project,
                                         task_id="t4")
            self.assertEqual(res["status"], "paused")
            self.assertEqual(res["paused"]["step"], "gap")

    def test_fail_continue_step_marks_chain_unusable(self):
        with tempfile.TemporaryDirectory() as project:
            card = {"plan_id": "p", "intent": "probe",
                    "steps": [
                        {"id": "ok_step", "tool": "coderef_version"},
                        {"id": "bad_step", "tool": "coderef_scan",
                         "fail": "continue"},
                    ]}

            def runner(tool, args):
                if tool == "coderef_scan":
                    return json.dumps({"status": "error", "error": "扫描失败"},
                                      ensure_ascii=False)
                return _ok(tool)

            with mock.patch("core.preconditions.evaluate",
                            return_value={"hits": [], "blocked": False}):
                res = dispatcher.execute(card, runner, project, task_id="tfail")
            # fail:continue 不改变 trace 状态，但 report 必须诚实标注失败步骤
            self.assertEqual(res["status"], "completed")
            rep = dispatcher.report(project, "tfail")
            self.assertFalse(rep["envelope"]["usable_for_decisions"])
            self.assertIn("失败步骤", rep["envelope"]["verdict"])
            self.assertIn("bad_step", rep["envelope"]["verdict"])

    def test_paused_chain_verdict_is_chain_level(self):
        with tempfile.TemporaryDirectory() as project:
            trace = {
                "status": "paused", "intent": "probe",
                "steps": [{"id": "s1", "status": "completed",
                           "summary": {"verdict": "步骤结论不应冒充链结论"}},
                          {"id": "s2", "status": "paused"}],
            }
            dispatcher._write_json(dispatcher._trace_path(project, "tp"), trace)
            rep = dispatcher.report(project, "tp")
            self.assertFalse(rep["envelope"]["usable_for_decisions"])
            self.assertIn("调度暂停", rep["envelope"]["verdict"])
            self.assertNotIn("步骤结论不应冒充链结论",
                             rep["envelope"]["verdict"])

    def test_missing_trace_is_honest_error(self):
        with tempfile.TemporaryDirectory() as project:
            st = dispatcher.status(project, "nope")
            self.assertEqual(st["status"], "error")
            rep = dispatcher.report(project, "nope")
            self.assertEqual(rep["status"], "error")

    def test_invalid_ids_rejected(self):
        with tempfile.TemporaryDirectory() as project:
            self.assertIsNone(dispatcher.load_plan(project, "../evil"))
            self.assertEqual(dispatcher.status(project, "../evil")["status"],
                             "error")
            self.assertEqual(dispatcher.report(project, "../evil")["status"],
                             "error")

    def test_plan_carries_args_and_project_path(self):
        with tempfile.TemporaryDirectory() as project:
            card = dispatcher.plan("probe", project)
            for step in card["steps"]:
                self.assertIn("args", step)
            received = {}

            def runner(tool, args):
                received[tool] = dict(args)
                return _ok(tool)

            with mock.patch("core.preconditions.evaluate",
                            return_value={"hits": [], "blocked": False,
                                          "checked": True}):
                res = dispatcher.execute(card, runner, project,
                                         task_id="targs")
            self.assertEqual(res["status"], "completed")
            self.assertEqual(len(received), len(card["steps"]))
            for tool, args in received.items():
                self.assertEqual(args.get("project_path"),
                                 os.path.abspath(project), tool)
            self.assertEqual(received["coderef_audit"].get("strategy"), "incr")
            self.assertEqual(received["coderef_scan"].get("tool"), "agent")

    def test_governance_has_no_unconditional_decision(self):
        with tempfile.TemporaryDirectory() as project:
            card = dispatcher.plan("governance", project)
            self.assertEqual(card["decision_points"], [])
            self.assertFalse(any(s.get("requires_user")
                                 for s in card["steps"]))


class ServerEntryTest(unittest.TestCase):
    def test_plan_and_status_via_server(self):
        from core.mcp_server import Server
        with tempfile.TemporaryDirectory() as project:
            s = Server()
            card = json.loads(s._run("coderef_plan", {
                "project_path": project, "intent": "recover"}))
            self.assertEqual(card["status"], "ok")
            st = json.loads(s._run("coderef_dispatch_status", {
                "project_path": project, "task_id": "nope"}))
            self.assertEqual(st["status"], "error")


if __name__ == "__main__":
    unittest.main()
