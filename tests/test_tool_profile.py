# -*- coding: utf-8 -*-
"""5.21.0 工具面 profile 的单元测试。

覆盖：
- lite：tools/list 只暴露 12 个、initialize 报告 12/47
- full（默认/显式）：暴露 59 个
- 非法值回退 full 且 current_profile()["error"] 非空
- 隐藏工具仍可按名直接调用（coderef_scan_list）
- Server._version 报告 tool_profile / tools_exposed

运行方式：
    python -m pytest tests/test_tool_profile.py -q
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

from core.mcp_server import Server
from core.tool_profile import LITE_TOOLS, PROFILE_ENV, current_profile, filter_tools
from core.tools_manifest import BUILTIN_TOOLS

TOTAL_TOOLS = len(BUILTIN_TOOLS)


def _tool_names(resp):
    return [t["name"] for t in resp["result"]["tools"]]


class LiteProfileTest(unittest.TestCase):
    def test_tools_list_lite(self):
        with mock.patch.dict(os.environ, {PROFILE_ENV: "lite"}, clear=False):
            names = _tool_names(Server()._handle({"method": "tools/list", "id": 1}))
        self.assertEqual(len(names), 12)
        self.assertEqual(set(names), set(LITE_TOOLS))

    def test_initialize_lite_counts(self):
        with mock.patch.dict(os.environ, {PROFILE_ENV: "lite"}, clear=False):
            resp = Server()._handle({"method": "initialize", "id": 1})
        info = resp["result"]["serverInfo"]
        self.assertEqual(info["toolProfile"], "lite")
        self.assertEqual(info["toolsExposed"], 12)
        self.assertEqual(info["toolsHidden"], TOTAL_TOOLS - 12)

    def test_filter_tools_preserves_order(self):
        with mock.patch.dict(os.environ, {PROFILE_ENV: "LITE"}, clear=False):
            kept = filter_tools([{"name": n} for n in
                                 ["coderef_whitelist", "coderef_version", "coderef_scan"]])
        self.assertEqual([t["name"] for t in kept],
                         ["coderef_version"])

    def test_filter_tools_explicit_profile_skips_config_read(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop(PROFILE_ENV, None)
            with mock.patch("core.tool_profile.current_profile") as read:
                kept = filter_tools([{"name": n} for n in
                                     ["coderef_whitelist", "coderef_version"]],
                                    "lite")
        read.assert_not_called()
        self.assertEqual([t["name"] for t in kept], ["coderef_version"])


class FullProfileTest(unittest.TestCase):
    def test_full_explicit(self):
        with mock.patch.dict(os.environ, {PROFILE_ENV: "full"}, clear=False):
            names = _tool_names(Server()._handle({"method": "tools/list", "id": 1}))
        self.assertEqual(len(names), TOTAL_TOOLS)

    def test_full_default(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop(PROFILE_ENV, None)
            with mock.patch("core.tool_profile._config_candidates", return_value=[]):
                got = current_profile()
                names = _tool_names(Server()._handle({"method": "tools/list", "id": 1}))
        self.assertEqual(got["profile"], "full")
        self.assertEqual(got["source"], "default")
        self.assertEqual(got["error"], "")
        self.assertEqual(len(names), TOTAL_TOOLS)


class InvalidProfileTest(unittest.TestCase):
    def test_invalid_falls_back_to_full(self):
        with mock.patch.dict(os.environ, {PROFILE_ENV: "turbo"}, clear=False):
            got = current_profile()
            names = _tool_names(Server()._handle({"method": "tools/list", "id": 1}))
        self.assertEqual(got["profile"], "full")
        self.assertNotEqual(got["error"], "")
        self.assertEqual(len(names), TOTAL_TOOLS)


class ConfigFileProfileTest(unittest.TestCase):
    def test_config_file_lite(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "config.json")
            with open(path, "w", encoding="utf-8") as f:
                json.dump({"tool_profile": "lite"}, f)
            with mock.patch.dict(os.environ, {}, clear=False):
                os.environ.pop(PROFILE_ENV, None)
                os.environ["CODEREF_CONFIG_PATH"] = path
                got = current_profile()
        self.assertEqual(got["profile"], "lite")
        self.assertEqual(got["source"], "config")
        self.assertEqual(got["error"], "")


class HiddenToolCallableTest(unittest.TestCase):
    def test_hidden_tool_still_callable(self):
        with mock.patch.dict(os.environ, {PROFILE_ENV: "lite"}, clear=False):
            raw = Server()._run("coderef_scan_list", {})
        data = json.loads(raw)
        # _scan_list 实际返回 "completed"（历史契约），重点是隐藏工具仍可调用
        self.assertIn(data.get("status"), ("ok", "completed"))
        self.assertEqual(data.get("tool"), "coderef_scan_list")


class VersionReportTest(unittest.TestCase):
    def test_version_reports_profile(self):
        with tempfile.TemporaryDirectory() as project:
            with mock.patch.dict(os.environ, {PROFILE_ENV: "lite"}, clear=False):
                data = json.loads(Server()._version({"project_path": project}))
        self.assertEqual(data["tool_profile"]["profile"], "lite")
        self.assertEqual(data["tool_profile"]["tools_exposed"], 12)
        self.assertEqual(data["tool_profile"]["tools_hidden"], TOTAL_TOOLS - 12)


if __name__ == "__main__":
    unittest.main()
