# -*- coding: utf-8 -*-
"""core.session_guard 的单元测试（标准库 unittest，无第三方依赖）。

覆盖：
- 非 git 目录：git_fingerprint 返回 error，should_guard 为 False
- 首次 dirty：自动跑 ChangeGuard 并落盘会话状态
- 指纹不变：第二次不再重复跑守卫
- 指纹变化（head 变）：再次自动跑守卫
- ChangeGuard 抛异常：返回 status=error 且不抛出
- save/load 损坏 JSON：load_state 返回 {}

运行方式：
    python -m unittest tests.test_session_guard
    或
    python -m pytest tests/test_session_guard.py -q
"""
import os
import sys
import tempfile
import unittest
from unittest import mock

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from core import session_guard


class _GitStub:
    """可控的 git 命令桩：status/diff 返回工作区状态，其余返回 HEAD。"""

    def __init__(self, head="head-1", status=" M core/demo.py\n", diff="diff-v1"):
        self.head = head
        self.status = status
        self.diff = diff

    def __call__(self, args, cwd):
        if args and args[0] == "status":
            return 0, self.status, ""
        if args and args[0] == "diff":
            return 0, self.diff, ""
        return 0, self.head, ""


def _report(findings, degraded=True, source="git-auto", summary="检测到退化"):
    return {
        "findings": findings,
        "degraded": degraded,
        "source": source,
        "summary": summary,
    }


class NonGitDirTest(unittest.TestCase):
    def test_non_git_dir_returns_error(self):
        with tempfile.TemporaryDirectory() as project:
            fp = session_guard.git_fingerprint(project)
            self.assertTrue(fp["error"])
            self.assertEqual(fp["head"], "")
            self.assertFalse(fp["dirty"])
            self.assertEqual(fp["changed_files"], 0)
            self.assertFalse(session_guard.should_guard(project))


class GuardIfNeededTest(unittest.TestCase):
    def test_first_dirty_runs_guard_and_persists_state(self):
        with tempfile.TemporaryDirectory() as project:
            stub = _GitStub(status=" M core/demo.py\n")
            report = _report([{"tier": "high"}, {"tier": "medium"}])
            with mock.patch("core.session_guard._git", stub), \
                    mock.patch("core.change_guard.ChangeGuard") as guard_cls:
                guard_cls.return_value.guard.return_value = report
                result = session_guard.guard_if_needed(project)
                guard_cls.return_value.guard.assert_called_once_with(project)

            self.assertIsNotNone(result)
            self.assertEqual(result["status"], "ok")
            self.assertEqual(result["findings"], 2)
            self.assertEqual(result["high"], 1)
            self.assertTrue(result["degraded"])
            self.assertEqual(result["source"], "git-auto")
            self.assertTrue(result["at"])

            state = session_guard.load_state(project)
            self.assertEqual(state["last_guard"]["findings"], 2)
            self.assertEqual(state["last_guard"]["high"], 1)
            self.assertEqual(state["last_guard_fingerprint"]["head"], "head-1")
            self.assertEqual(state["last_guard"]["fingerprint"]["head"], "head-1")

    def test_unchanged_fingerprint_returns_none(self):
        with tempfile.TemporaryDirectory() as project:
            stub = _GitStub()
            with mock.patch("core.session_guard._git", stub), \
                    mock.patch("core.change_guard.ChangeGuard") as guard_cls:
                guard_cls.return_value.guard.return_value = _report([])
                first = session_guard.guard_if_needed(project)
                second = session_guard.guard_if_needed(project)
                self.assertEqual(guard_cls.return_value.guard.call_count, 1)

            self.assertIsNotNone(first)
            self.assertIsNone(second)

    def test_changed_head_runs_guard_again(self):
        with tempfile.TemporaryDirectory() as project:
            stub = _GitStub()
            with mock.patch("core.session_guard._git", stub), \
                    mock.patch("core.change_guard.ChangeGuard") as guard_cls:
                guard_cls.return_value.guard.return_value = _report([])
                first = session_guard.guard_if_needed(project)
                stub.head = "head-2"
                second = session_guard.guard_if_needed(project)
                self.assertEqual(guard_cls.return_value.guard.call_count, 2)

            self.assertIsNotNone(first)
            self.assertIsNotNone(second)
            state = session_guard.load_state(project)
            self.assertEqual(state["last_guard_fingerprint"]["head"], "head-2")

    def test_guard_exception_returns_error_without_raising(self):
        with tempfile.TemporaryDirectory() as project:
            stub = _GitStub()
            with mock.patch("core.session_guard._git", stub), \
                    mock.patch("core.change_guard.ChangeGuard") as guard_cls:
                guard_cls.return_value.guard.side_effect = RuntimeError("守卫炸了")
                result = session_guard.guard_if_needed(project)
                # 失败也记录指纹，第二次不再重复失败
                again = session_guard.guard_if_needed(project)

            self.assertEqual(result["status"], "error")
            self.assertIn("守卫炸了", result["error"])
            self.assertFalse(result["degraded"])
            self.assertIsNone(again)

            state = session_guard.load_state(project)
            self.assertIn("守卫炸了", state["last_guard"]["error"])
            self.assertEqual(state["last_guard_fingerprint"]["head"], "head-1")


class FingerprintDigestTest(unittest.TestCase):
    def test_same_status_but_new_diff_changes_fingerprint(self):
        with tempfile.TemporaryDirectory() as project:
            stub = _GitStub()
            with mock.patch("core.session_guard._git", stub), \
                    mock.patch("core.change_guard.ChangeGuard") as guard_cls:
                guard_cls.return_value.guard.return_value = _report([])
                first = session_guard.guard_if_needed(project)
                # 同一 HEAD、同样 1 个脏文件，但文件内容再次变化 → 必须再守卫
                stub.diff = "diff-v2"
                self.assertTrue(session_guard.should_guard(project))
                second = session_guard.guard_if_needed(project)
                self.assertEqual(guard_cls.return_value.guard.call_count, 2)
                # 无变化 → 不再触发
                self.assertFalse(session_guard.should_guard(project))
                self.assertIsNone(session_guard.guard_if_needed(project))

            self.assertIsNotNone(first)
            self.assertIsNotNone(second)

    def test_old_state_without_digest_triggers_guard(self):
        with tempfile.TemporaryDirectory() as project:
            session_guard.save_state(project, {
                "last_guard_fingerprint": {"head": "head-1", "dirty": True,
                                           "changed_files": 1}})
            with mock.patch("core.session_guard._git", _GitStub()):
                self.assertTrue(session_guard.should_guard(project))

    def test_untracked_file_content_change_changes_digest(self):
        with tempfile.TemporaryDirectory() as project:
            foo = os.path.join(project, "new.py")
            with open(foo, "w", encoding="utf-8") as f:
                f.write("print(1)\n")
            stub = _GitStub(status="?? new.py\n", diff="")
            with mock.patch("core.session_guard._git", stub):
                before = session_guard.git_fingerprint(project)
                with open(foo, "w", encoding="utf-8") as f:
                    f.write("print(123456)\n")
                after = session_guard.git_fingerprint(project)
            self.assertEqual(before["changed_files"], after["changed_files"])
            self.assertNotEqual(before["digest"], after["digest"])


class StateIOTest(unittest.TestCase):
    def test_corrupt_json_returns_empty_dict(self):
        with tempfile.TemporaryDirectory() as project:
            state_dir = os.path.join(project, ".coderef")
            os.makedirs(state_dir)
            with open(os.path.join(state_dir, "session_state.json"), "w",
                      encoding="utf-8") as f:
                f.write("{not valid json")
            self.assertEqual(session_guard.load_state(project), {})

    def test_save_load_roundtrip(self):
        with tempfile.TemporaryDirectory() as project:
            session_guard.save_state(project, {"hello": "世界"})
            self.assertEqual(session_guard.load_state(project), {"hello": "世界"})


if __name__ == "__main__":
    unittest.main()
