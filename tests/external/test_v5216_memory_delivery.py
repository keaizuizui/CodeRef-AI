# -*- coding: utf-8 -*-
"""v5.21.16 记忆投递层回归：G-E 闸门 + 规则安装/幂等/卸载。"""
import json
import os
import shutil
import sys
import tempfile
import unittest

PROJECT_ROOT = os.environ.get("CODEREF_SRC",
                              os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from core.mcp_server import Server  # noqa: E402

MARKER = "CODEREF-MEMORY-RULE"


class _Base(unittest.TestCase):

    def setUp(self):
        self.td = tempfile.mkdtemp(prefix="coderef_v5216_")
        self.p = os.path.join(self.td, "p")
        os.makedirs(os.path.join(self.p, ".git"), exist_ok=True)
        os.makedirs(os.path.join(self.p, "core"), exist_ok=True)
        with open(os.path.join(self.p, "requirements.txt"), "w",
                  encoding="utf-8") as f:
            f.write("requests\n")
        with open(os.path.join(self.p, "core", "app.py"), "w",
                  encoding="utf-8") as f:
            f.write("def run():\n    return 1\n")
        self.server = Server()

    def tearDown(self):
        shutil.rmtree(self.td, ignore_errors=True)

    def call(self, name, args):
        return json.loads(self.server._run(name, dict(args, background=False)))


class GateGEUntilRecoverTest(_Base):

    def test_change_guard_blocked_until_recover(self):
        first = self.call("coderef_change_guard", {"project_path": self.p})
        self.assertEqual(first.get("status"), "blocked",
                         f"已装契约未 recover 应被 G-E 拦截: {first}")
        self.assertEqual(first.get("gate"), "G-E")
        actions = json.dumps(first.get("owner_actions") or {}, ensure_ascii=False)
        self.assertIn("recover", actions)

        rec = self.call("coderef_operation_memory",
                        {"project_path": self.p, "action": "recover"})
        self.assertEqual(rec.get("status"), "ok", f"recover 应成功: {rec}")
        self.assertTrue(rec.get("procedure_card"), "recover 应返回入口卡")

        again = self.call("coderef_change_guard", {"project_path": self.p})
        self.assertNotEqual(again.get("status"), "blocked",
                            f"recover 后不应再被 G-E 拦截: {again}")


class SetupRulesLifecycleTest(_Base):

    def setUp(self):
        super().setUp()
        self.agents = os.path.join(self.p, "AGENTS.md")
        with open(self.agents, "w", encoding="utf-8") as f:
            f.write("# 手写规则\nKEEP-ME\n")

    def test_install_idempotent_status_and_uninstall(self):
        r1 = self.call("coderef_operation_memory",
                       {"project_path": self.p, "action": "setup_install",
                        "targets": "project"})
        self.assertEqual(r1.get("status"), "ok", f"setup_install 失败: {r1}")
        self.assertIn("recover", r1.get("activation_line", ""),
                      "旧会话激活语应包含 recover 指令")
        with open(self.agents, encoding="utf-8") as f:
            text = f.read()
        self.assertIn("KEEP-ME", text, "手写内容必须保留")
        self.assertIn(MARKER, text, "应写入 marker 块")

        r2 = self.call("coderef_operation_memory",
                       {"project_path": self.p, "action": "setup_install",
                        "targets": "project"})
        self.assertEqual(r2.get("status"), "ok", f"二次安装失败: {r2}")
        self.assertEqual(list(r2.get("written") or []), [],
                         f"重复安装应 0 写入: {r2}")

        st = self.call("coderef_operation_memory",
                       {"project_path": self.p, "action": "setup_status"})
        self.assertEqual(st.get("status"), "ok", f"setup_status 失败: {st}")
        self.assertGreaterEqual(int(st.get("installed_count") or 0), 1,
                                f"setup_status 应报告已安装: {st}")
        self.assertFalse(st.get("needs_setup"), f"已安装后 needs_setup 应为 false: {st}")

        un = self.call("coderef_operation_memory",
                       {"project_path": self.p, "action": "setup_uninstall"})
        self.assertEqual(un.get("status"), "ok", f"setup_uninstall 失败: {un}")
        with open(self.agents, encoding="utf-8") as f:
            after = f.read()
        self.assertIn("KEEP-ME", after, "卸载不得删手写内容")
        self.assertNotIn(MARKER, after, "卸载应只删 marker 块")


if __name__ == "__main__":
    unittest.main(verbosity=2)
