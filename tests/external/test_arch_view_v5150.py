# -*- coding: utf-8 -*-
"""v5.15.0 架构可视化层重写 · 新增用例（独立回归落位复跑）

开发方回报（交接记录 v5.15.0 段）新增 5 项用例，本文件为其独立仓落位：
1. TestArchViewDataShape —— build_arch_data 返回契约六字段（project/summary/roles/flows/modules/
   violations/gaps + meta），modules 含 role/in_cycles/unassigned/call_out/call_in；无图谱项目
   （空 target_arch / 无 kg db）返回空视图不崩溃（CodeRabbit critical 防回归）。
2. TestArchViewInject —— render_arch_view 产物含 `window.__ARCH_DATA__ = {...}` 可 JSON 解析、
   无占位符残留、`<title>` 已 escape（CodeRabbit XSS 防回归）。
3. TestArchViewRoleMissing —— target_modules 为空的角色 missing=True（CodeRabbit minor 防回归）。
4. TestArchViewFilenameUnique —— 同秒两次生成文件名不同（微秒防覆盖）。
5. TestArchCanvasServeContract（扩展 v5.14.5）—— coderef_arch_canvas 工具 schema 保留 open_server，
   且描述已切换为 5.15 分区画布/决策者报告语义。

数据构造策略：mock 补丁 `core.arch_view.locate_kg_db / load_graph / analyze_gap` 提供可控图谱，
完全离线自包含；不依赖 MCP 实例或第三方站点草图。

运行：
    cd coderef-src && python -m unittest discover -s ..\独立测试\tests -p "test_arch_view_v5150.py" -v
"""
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

PROJECT_ROOT = os.environ.get("CODEREF_SRC", os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from core import arch_view
from core.arch_view import build_arch_data, render_arch_view


FAKE_PP = r"D:\__fake_proj__"


def make_nodes(pp):
    """KG 模块节点：file_path 相对 pp 可被 module_of 解析为模块名。"""
    return {
        "n:mod/app/order.py": {"type": "module", "file_path": os.path.join(pp, "app/order.py"), "name": "app/order"},
        "n:mod/app/cart.py": {"type": "module", "file_path": os.path.join(pp, "app/cart.py"), "name": "app/cart"},
        "n:mod/app/tests/test_order.py": {"type": "module", "file_path": os.path.join(pp, "app/tests/test_order.py"), "name": "app/tests/test_order"},
    }


def make_adj(nodes):
    # app/order -> app/cart 一条调用边（call_out/call_in 验证）
    return {"mod:app/order": ["n:mod/app/cart.py"]}


def make_gap_result(**overrides):
    base = {
        "summary": {"high": 1, "medium": 1, "low": 1},
        "alignment": {"module_assigned": 1, "role_coverage": 0.5},
        "gaps": [
            {"type": "unassigned", "module": "app/order", "severity": "low",
             "detail": "app/order 无角色归属"},
            {"type": "dependency_violation", "from_module": "app/order",
             "to_module": "app/cart", "severity": "high", "detail": "跨层依赖违规"},
        ],
    }
    base.update(overrides)
    return base


def target_arch(**overrides):
    base = {
        "version": "5.0", "project": "fake",
        "tech_roles": [{"id": "r1", "name": "角色1", "target_modules": ["app/order"]}],
        "business_flows": [{"id": "f1", "name": "下单流程", "steps": [
            {"id": "s1", "name": "下单"}, {"id": "s2", "name": "结算"}]}],
    }
    base.update(overrides)
    return base


class TestArchViewDataShape(unittest.TestCase):
    """开发方用例 1：build_arch_data 契约六字段 + modules 附加字段 + 无图谱不崩溃。"""

    def setUp(self):
        self.file = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.file.close()

    def tearDown(self):
        if os.path.exists(self.file.name):
            os.remove(self.file.name)

    @mock.patch("core.arch_framework.build_framework_enhanced")
    @mock.patch("core.arch_view.analyze_gap")
    @mock.patch("core.arch_view.load_graph")
    @mock.patch("core.arch_view.locate_kg_db")
    def test_contract_six_fields_and_module_attrs(self, locate, lgraph, agap, bfwe):
        locate.return_value = self.file.name
        lgraph.return_value = (make_nodes(FAKE_PP), make_adj(make_nodes(FAKE_PP)))
        agap.return_value = make_gap_result()
        bfwe.return_value = {"framework": {"id": "__root__", "name": "入口层",
                                            "desc": "d", "modules": [], "children": []}}
        d = build_arch_data(FAKE_PP, target_arch())
        # v5.16 契约：顶层 = project/summary/framework/modules_graph/meta，
        # v5.15 顶层 roles/flows/modules/violations/gaps 降级进 modules_graph
        for k in ("project", "summary", "framework", "modules_graph", "meta"):
            self.assertIn(k, d, f"build_arch_data 应含顶层字段 {k}")
        mg = d["modules_graph"]
        for k in ("roles", "flows", "modules", "violations", "gaps"):
            self.assertIn(k, mg, f"modules_graph 应含 {k}")
        mods = [m for m in mg["modules"] if m["name"] == "app/order"]
        self.assertTrue(mods, "modules 应含 app/order")
        m = mods[0]
        for attr in ("role", "in_cycles", "unassigned", "call_out", "call_in"):
            self.assertIn(attr, m, f"module 应含 {attr}")
        self.assertEqual(m["call_out"], ["app/cart"], "app/order 的 call_out 应含 app/cart")
        # 测试模块被排除
        names = {mm["name"] for mm in mg["modules"]}
        self.assertNotIn("app/tests/test_order", names)
        # 角色归属
        self.assertEqual(m["role"], "r1")
        self.assertTrue(m["unassigned"], "unassigned gap 模块应被标记")
        # flows 透传
        self.assertEqual(mg["flows"][0]["steps"], ["下单", "结算"])
        # summary 计数
        self.assertEqual(d["summary"]["gap_total"], 2)
        # violations
        self.assertTrue(mg["violations"], "应含违规边")

    @mock.patch("core.arch_framework.build_framework_enhanced")
    @mock.patch("core.arch_view.locate_kg_db")
    def test_no_graph_returns_empty_view(self, locate, bfwe):
        locate.return_value = None
        bfwe.return_value = {"framework": {"id": "__root__", "name": "入口层",
                                           "desc": "d", "modules": [], "children": []}}
        d = build_arch_data(FAKE_PP, target_arch())
        # 无图谱：不崩溃，返回空视图。target_arch 定义的角色仍会列出（modules 空/missing=True），
        # 但模块/差距/调用全空（CodeRabbit critical 防崩溃点）。
        mg = d["modules_graph"]
        self.assertEqual(mg["modules"], [])
        self.assertEqual(mg["gaps"], [])
        self.assertEqual(mg["violations"], [])
        self.assertIs(False, d["meta"]["has_kg"])
        self.assertTrue(mg["roles"], "角色仍列出（源自 target_arch）")
        for r in mg["roles"]:
            self.assertEqual(r["modules"], [])
            self.assertTrue(r["missing"])


class TestArchViewRoleMissing(unittest.TestCase):
    """开发方用例 3/4：target_modules 为空的角色 missing=True。"""

    @mock.patch("core.arch_framework.build_framework_enhanced")
    @mock.patch("core.arch_view.analyze_gap")
    @mock.patch("core.arch_view.load_graph")
    @mock.patch("core.arch_view.locate_kg_db")
    def test_role_with_empty_modules_is_missing(self, locate, lgraph, agap, bfwe):
        file = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        file.close()
        try:
            locate.return_value = file.name
            lgraph.return_value = (make_nodes(FAKE_PP), make_adj(make_nodes(FAKE_PP)))
            agap.return_value = make_gap_result()
            bfwe.return_value = {"framework": {"id": "__root__", "name": "入口层",
                                               "desc": "d", "modules": [], "children": []}}
            ta = target_arch(tech_roles=[
                {"id": "r-empty", "name": "空角色",
                 "target_modules": ["app/does_not_exist"]}])
            d = build_arch_data(FAKE_PP, ta)
            role = next(r for r in d["modules_graph"]["roles"] if r["id"] == "r-empty")
            self.assertTrue(role["missing"], "target_modules 无匹配时应 missing=True")
        finally:
            os.remove(file.name)


class TestArchViewInject(unittest.TestCase):
    """开发方用例 2：注入产物含可解析 JSON、无占位符残留、title 已 escape。"""

    def _render(self, title="架构总览"):
        # render_arch_view 返回 HTML 文件路径，需读回内容再断言；
        # patch framework 防单测真实外呼 LLM（运行态 LLM 链路另行实测）
        with mock.patch("core.arch_framework.build_framework_enhanced") as bfwe, \
                tempfile.TemporaryDirectory() as out:
            bfwe.return_value = {"framework": {"id": "__root__", "name": "入口层",
                                               "desc": "d", "modules": [], "children": []}}
            filepath = render_arch_view(FAKE_PP, target_arch(), output_dir=out, title=title)
            with open(filepath, "r", encoding="utf-8") as f:
                return f.read()

    def test_window_data_json_parsable_and_no_placeholder(self):
        html = self._render()
        self.assertIn("window.__ARCH_DATA__ = {", html,
                      "产物应含 window.__ARCH_DATA__ = {...}")
        self.assertNotIn("__ARCH_DATA_PLACEHOLDER__", html,
                         "产物不应残留占位符")
        # 提取 window.__ARCH_DATA__ = {...}; 中 JSON 部分并解析
        marker = "window.__ARCH_DATA__ = "
        start = html.index(marker) + len(marker)
        end = html.index(";", start)
        payload = html[start:end]
        data = json.loads(payload)
        # v5.16 契约：framework + modules_graph 在顶层
        self.assertIn("framework", data)
        self.assertIn("modules_graph", data)
        self.assertIn("modules", data["modules_graph"])
        self.assertIn("roles", data["modules_graph"])

    def test_title_escaped(self):
        html = self._render(title="<script>alert(1)</script>")
        self.assertIn("&lt;script&gt;", html, "<title> 内容应转义防 XSS")
        self.assertNotIn("<title><script>alert(1)</script></title>", html,
                         "原始未转义 title 不应出现")


class TestArchViewFilenameUnique(unittest.TestCase):
    """开发方用例 4：同秒两次生成文件名不同（微秒防覆盖）。"""

    def test_same_second_two_runs_different_filename(self):
        with mock.patch("core.arch_framework.build_framework_enhanced") as bfwe, \
                tempfile.TemporaryDirectory() as out:
            bfwe.return_value = {"framework": {"id": "__root__", "name": "入口层",
                                               "desc": "d", "modules": [], "children": []}}
            f1 = render_arch_view(FAKE_PP, target_arch(), output_dir=out)
            f2 = render_arch_view(FAKE_PP, target_arch(), output_dir=out)
            self.assertNotEqual(f1, f2, "同秒两次生成文件名应不同（微秒防覆盖）")
            self.assertTrue(os.path.exists(f1))
            self.assertTrue(os.path.exists(f2))


class TestArchCanvasServeContract(unittest.TestCase):
    """开发方用例 5（扩展 v5.14.5）：open_server schema 保留 + 5.16 业务树语义描述（D1 守门）。"""

    def test_open_server_schema_kept(self):
        from core.tools_manifest import BUILTIN_TOOLS
        tool = next(t for t in BUILTIN_TOOLS if t.get("name") == "coderef_arch_canvas")
        props = tool["inputSchema"]["properties"]
        for k in ("open_server", "host", "port"):
            self.assertIn(k, props)
        desc = tool.get("description", "")
        # D1 修复后描述已切 5.16 业务架构大白话图语义（默认为报告 + 「查看业务架构图」业务树）
        self.assertIn("5.16", desc)
        self.assertIn("业务架构图", desc)
        # 5.15 分区画布旧文案不得残留（D1 防回归）
        self.assertNotIn("分区画布", desc)
        self.assertNotIn("深看架构画布", desc)


if __name__ == "__main__":
    unittest.main()