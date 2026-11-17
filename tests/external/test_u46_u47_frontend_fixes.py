# -*- coding: utf-8 -*-
"""U-46 / U-47 回归用例（独立回归落位，v5.17.0 运行态复测配套）。

U-46：arch_canvas 报告在空图谱/无代码态必须走 no_code 口径——
      `summary.no_code=True`、alignment/role_coverage 置 None，
      不得输出"0 模块 + 对齐度 100% + 系统整体健康"。
U-47：gov_report open_server 根路径必须直回看板 HTML（与 /api/transition 同源），
      非 GET 才走 404 JSON。

覆盖口径说明：
  - U-46 的 no_code 分支用**真实空目录**（无 KG）走到；
    "有模块不误判"用 mock 图谱（stub，验证整形判定，不复跑真实建图）。
  - U-47 用**真实回环 HTTP**（gov_webdash.serve + http.client）。

运行：cd coderef-src && python -m unittest ..\\独立测试\\tests\\test_u46_u47_frontend_fixes.py
"""
import os
import shutil
import sys
import tempfile
import unittest
from http.client import HTTPConnection
from unittest import mock

PROJECT_ROOT = os.environ.get("CODEREF_SRC", os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from core import arch_view, gov_webdash


def _fake_framework():
    return {"framework": {"domains": []}}


class _TmpCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="u46u47_")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)


# ────────────────────────────────────────────────────────────────
# U-46 · arch_canvas 空图谱/无代码态 no_code 口径
# ────────────────────────────────────────────────────────────────

class ArchViewNoCodeTest(_TmpCase):
    def test_empty_project_is_no_code(self):
        with mock.patch("core.arch_framework.build_framework_enhanced",
                        return_value=_fake_framework()):
            data = arch_view.build_arch_data(self.tmp)
        s = data["summary"]
        self.assertTrue(s["no_code"], "无非测试代码模块时应标记 no_code")
        self.assertIsNone(s["alignment"], "空态不得给出对齐度（含 100%/1.0）")
        self.assertIsNone(s["role_coverage"])
        self.assertEqual(s["module_total"], 0)
        self.assertFalse(data["meta"]["has_kg"])

    def test_modules_present_is_not_no_code(self):
        nodes = {
            "mod:core/engine": {
                "type": "module", "name": "engine",
                "file_path": os.path.join(self.tmp, "core", "engine.py"),
            },
            "mod:core/api": {
                "type": "module", "name": "api",
                "file_path": os.path.join(self.tmp, "core", "api.py"),
            },
        }
        gap = {"summary": {"high": 1, "medium": 2, "low": 0},
               "alignment": {"module_assigned": 0.5, "role_coverage": 0.5},
               "gaps": []}
        db = os.path.join(self.tmp, "fake_kg.db")
        with open(db, "w", encoding="utf-8") as f:
            f.write("")
        with mock.patch.object(arch_view, "locate_kg_db", return_value=db), \
                mock.patch.object(arch_view, "load_graph", return_value=(nodes, {})), \
                mock.patch.object(arch_view, "analyze_gap", return_value=gap), \
                mock.patch("core.arch_framework.build_framework_enhanced",
                           return_value=_fake_framework()):
            data = arch_view.build_arch_data(self.tmp)
        s = data["summary"]
        self.assertFalse(s["no_code"], "有生产模块时不得误判为无代码")
        self.assertGreater(s["module_total"], 0)
        self.assertEqual(s["alignment"], 0.5)
        self.assertTrue(data["meta"]["has_kg"])


# ────────────────────────────────────────────────────────────────
# U-47 · gov_report open_server 根路径直回 HTML
# ────────────────────────────────────────────────────────────────

class GovRootHtmlRouteTest(_TmpCase):
    def setUp(self):
        super().setUp()
        self.srv = gov_webdash.serve(self.tmp)
        self.port = self.srv["port"]

    def tearDown(self):
        pp = os.path.abspath(self.tmp)
        rec = gov_webdash._SERVERS.pop(pp, None)
        if rec:
            try:
                rec["httpd"].shutdown()
                rec["httpd"].server_close()
            except Exception:
                pass
        super().tearDown()

    def _req(self, method, path):
        c = HTTPConnection("127.0.0.1", self.port, timeout=5)
        try:
            c.request(method, path)
            r = c.getresponse()
            return r.status, r.getheader("Content-Type", ""), r.read().decode("utf-8")
        finally:
            c.close()

    def test_root_returns_html_not_json(self):
        code, ctype, body = self._req("GET", "/")
        self.assertEqual(code, 200)
        self.assertIn("text/html", ctype)
        self.assertIn("<html", body.lower())
        self.assertNotIn('"ok": true', body, "根路径不应再返回 JSON 提示")

    def test_alias_paths_return_html(self):
        for path in ("/board.html", "/overview.html", "/board", "/index.html"):
            code, ctype, _ = self._req("GET", path)
            self.assertEqual(code, 200, path)
            self.assertIn("text/html", ctype, path)

    def test_non_get_root_is_404_json(self):
        code, ctype, body = self._req("POST", "/")
        self.assertEqual(code, 404)
        self.assertIn("application/json", ctype)
        self.assertIn("404", body)


if __name__ == "__main__":
    unittest.main()
