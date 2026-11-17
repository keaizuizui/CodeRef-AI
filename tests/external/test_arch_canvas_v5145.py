# -*- coding: utf-8 -*-
"""v5.14.5 画布可视化层重做 · 新增用例（独立回归落位复跑）

开发方回报（交接记录 v5.14.5 段）新增 4 项画布用例，本文件为其独立仓落位：
1. TestArchCanvasGapHighlight —— 命中 cycle/unassigned/twin 的模块节点标色正确（黄 #F59E0B / 灰 #64748B / 真身绿 #22C55E 等），
   保护 `props.full_label` 全路径比对不被短名化回归破坏（CodeRabbit a43037b 防回归）。
2. TestArchCanvasRoleModulesFilter —— role.props.modules 不含 test_ 前缀模块，与 meta.summary 模块计数口径一致
   （防"角色卡 114 vs KPI 83"复发；vimax 实测 114→82 与 KPI 83 自洽）。
3. TestArchCanvasMetaGaps —— meta.gaps 每条含 type/severity/detail 三字段，与 analyze_gap gaps 白名单透传一致。
4. TestArchServeStatic（可选·运行态）—— coderef_arch_canvas open_server=true 返回 server.url 可 HTTP GET 200；
   同目录重复调用不泄漏旧句柄（eed98a6 防回归）。逻辑层以 stub 通道覆盖参数契约，真实运行态放 E2E 手测。

运行：
    cd coderef-src && python -m unittest discover -s ..\独立测试\tests -p "test_arch_canvas_v5145.py" -v
"""
import os
import sys
import unittest

PROJECT_ROOT = os.environ.get("CODEREF_SRC", os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from core.canvas_generator import ArchCanvas


def make_nodes(project_path):
    """构造轻量 KG 模块节点：文件名/路径可直接驱动的 module_of 结果。"""
    return {
        "n:mod/app/order.py": {"type": "module", "file_path": os.path.join(project_path, "app/order.py"), "name": "app/order"},
        "n:mod/app/tests/test_order.py": {"type": "module", "file_path": os.path.join(project_path, "app/tests/test_order.py"), "name": "app/tests/test_order"},
        "n:mod/libs/shared.py": {"type": "module", "file_path": os.path.join(project_path, "libs/shared.py"), "name": "libs/shared"},
        "n:mod/order.py": {"type": "module", "file_path": os.path.join(project_path, "order.py"), "name": "order"},
    }


def make_gap_result(**overrides):
    base = {
        "summary": {"quadrants": "x", "module_count": 3},
        "alignment": {"modules": {}},
        "gaps": [
            {"type": "cycle", "modules": ["app/order"], "severity": "medium", "module": "app/order",
             "detail": "app/order 与 libs/shared 成环"},
            {"type": "unassigned", "module": "libs/shared", "severity": "low",
             "detail": "libs/shared 无角色归属"},
            {"type": "twin_identity", "copies": [{"module": "order", "verdict": "真身"}], "severity": "low",
             "detail": "order 与 app/order 为孪生，order 系真身"},
        ],
    }
    base.update(overrides)
    return base


class TestArchCanvasGapHighlight(unittest.TestCase):
    """开发方用例 1：差距高亮标色 + full_label 全路径比对（短名化防回归）。"""

    def setUp(self):
        self.canvas = ArchCanvas()
        self.project_path = r"D:\__fake_proj__"
        self.target_arch = {
            "version": "5.0", "project": "fake",
            "tech_roles": [{"id": "r1", "name": "角色1", "target_modules": []}],
            "business_flows": [],
        }

    def _data(self):
        return self.canvas._build_free_canvas_data(
            self.project_path, self.target_arch,
            make_nodes(self.project_path), {}, make_gap_result(), has_kg=True)

    def test_cycle_module_highlighted_yellow(self):
        d = self._data()
        nodes = {n["props"].get("full_label") or n["label"]: n for n in d["nodes"] if n["type"] == "module"}
        # app/order 命中 cycle → 黄 #F59E0B（full_label 全路径命中）
        self.assertEqual(nodes["app/order"]["color"], "#F59E0B")
        # 同名 basename 的另一模块 order.py 不误标黄（全路径比对），且不被孪生标注误混
        self.assertNotEqual(nodes["order"]["color"], "#F59E0B")

    def test_unassigned_module_highlighted_gray(self):
        d = self._data()
        nodes = {n["props"].get("full_label") or n["label"]: n for n in d["nodes"] if n["type"] == "module"}
        self.assertEqual(nodes["libs/shared"]["color"], "#64748B")

    def test_twin_verdict_overrides_with_green(self):
        d = self._data()
        nodes = {n["props"].get("full_label") or n["label"]: n for n in d["nodes"] if n["type"] == "module"}
        # order 被判孪生真身 → 绿 #22C55E（独立差距色，回退优先于黄/灰）
        self.assertEqual(nodes["order"]["props"].get("twin_verdict"), "真身")
        self.assertEqual(nodes["order"]["color"], "#22C55E")

    def test_test_module_excluded_from_nodes(self):
        d = self._data()
        module_labels = {n["props"].get("full_label") or n["label"] for n in d["nodes"] if n["type"] == "module"}
        self.assertNotIn("app/tests/test_order", module_labels)


class TestArchCanvasRoleModulesFilter(unittest.TestCase):
    """开发方用例 2：role.props.modules 排除测试模块，与 meta 计数口径一致。"""

    def setUp(self):
        self.canvas = ArchCanvas()
        self.project_path = r"D:\__fake_proj__"

    def test_role_modules_exclude_test_prefix(self):
        target_arch = {
            "version": "5.0", "project": "fake",
            "tech_roles": [{"id": "r1", "name": "角色1",
                            "target_modules": ["app/order", "app/tests/test_order"]}],
            "business_flows": [],
        }
        d = self.canvas._build_free_canvas_data(
            self.project_path, target_arch, make_nodes(self.project_path), {},
            make_gap_result(), has_kg=True)
        role = next(n for n in d["nodes"] if n["type"] == "role")
        self.assertIn("app/order", role["props"]["modules"])
        self.assertTrue(all(not m.startswith("test_") and "/tests/" not in m
                            for m in role["props"]["modules"]),
                        f"role.props.modules 不应含测试模块: {role['props']['modules']}")

    def test_meta_summary_has_module_count(self):
        d = self.canvas._build_free_canvas_data(
            self.project_path, {"version": "5.0", "project": "fake",
                                "tech_roles": [], "business_flows": []},
            make_nodes(self.project_path), {}, make_gap_result(), has_kg=True)
        self.assertIn("module_count", d["meta"]["summary"])


class TestArchCanvasMetaGaps(unittest.TestCase):
    """开发方用例 3：meta.gaps 每条含 type/severity/detail 三字段。"""

    def setUp(self):
        self.canvas = ArchCanvas()
        self.project_path = r"D:\__fake_proj__"

    def test_each_gap_has_three_fields(self):
        d = self.canvas._build_free_canvas_data(
            self.project_path, {"version": "5.0", "project": "fake",
                                "tech_roles": [], "business_flows": []},
            make_nodes(self.project_path), {}, make_gap_result(), has_kg=True)
        gaps = d["meta"]["gaps"]
        self.assertTrue(gaps, "meta.gaps 不应为空")
        for g in gaps:
            self.assertIn("type", g)
            self.assertIn("severity", g)
            self.assertIn("detail", g)


class TestArchServeStaticContract(unittest.TestCase):
    """开发方用例 4（可选·运行态契约 stub）：open_server 参数面存在 + 契约字段。

    真实 HTTP 200 断言属运行态 E2E（需 MCP 加载 v5.14.5），逻辑层仅守参数契约，
    运行态实测放在重启 TRAE 后的 E2E 段（探针 5.14.5）。"""

    def test_arch_canvas_open_server_schema_present(self):
        from core.tools_manifest import BUILTIN_TOOLS
        tool = next(t for t in BUILTIN_TOOLS if t.get("name") == "coderef_arch_canvas")
        props = tool["inputSchema"]["properties"]
        self.assertIn("open_server", props)
        self.assertIn("host", props)
        self.assertIn("port", props)


if __name__ == "__main__":
    unittest.main()