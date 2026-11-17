# -*- coding: utf-8 -*-
"""v5.16.0 业务架构大白话图画布 · 新增用例（独立回归落位复跑）

开发方回报（交接记录 v5.16.0 段）新增 5 项用例，本文件为其独立仓落位：
1. TestArchFrameworkShape —— build_framework 返回 {project, framework}；framework 根含
   id/name/modules/children，主功能节点含 id/title/modules/children，子功能含 steps；
   入口模块为 main/cli 启发集合（过滤 test/__init__）。
2. TestArchFrameworkNoLLM —— build_framework_enhanced(with_llm=False) 静态兜底 desc
   非空不报错；LLM 路径异常（构造失败）仍返回骨架不阻断。
3. TestArchViewContractV516 —— build_arch_data 返回 framework + modules_graph
   （roles/modules/violations/gaps）+ summary（v5.16 新契约）。
4. TestArchServeIndex —— _serve_arch_static 根路径返回 200 text/html 引导页
   （含"打开最新架构视图"），文件名 html.escape 防注入。
5. TestFrameworkRootId —— 根节点 id="__root__"；_merge_llm_result 能覆盖根节点
   title/desc（CodeRabbit 防回归）。

数据构造策略：mock 补丁 locate_kg_db/load_graph/analyze_gap 提供可控图谱，
patch build_framework_enhanced 防止单测真实外呼 LLM（运行态 LLM 链路另行实测）。

运行：
    cd coderef-src && python -m unittest discover -s ..\独立测试\tests -p "test_arch_framework_v5160.py" -v
"""
import json
import os
import sys
import tempfile
import unittest
import urllib.request
from unittest import mock

PROJECT_ROOT = os.environ.get("CODEREF_SRC", os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from core import arch_framework
from core import arch_view
from core import handlers_arch


FAKE_PP = r"D:\__fake_fw_proj__"


def make_nodes(pp):
    """KG 模块节点：入口 + 业务域（agents/pipelines 两域，域内子组）。"""
    mods = [
        "app/main_agent",        # 入口（main 启发）
        "app/cli_tool",          # 入口（cli 启发）
        "app/agents/screenwriter",
        "app/agents/storyboard",
        "app/pipelines/idea2video",
        "app/pipelines/script2video",
        "app/tests/test_x",      # 测试模块（应被过滤）
        "app/__init__",          # __init__（应被过滤）
    ]
    return {
        f"n:mod/{m.replace('/', '#')}": {
            "type": "module", "file_path": os.path.join(pp, *m.split("/")) + ".py",
            "name": m,
        } for m in mods
    }


def make_adj():
    return {}


class TestArchFrameworkShape(unittest.TestCase):
    """开发方用例 1：build_framework 树形契约 + 入口启发 + 噪音过滤。"""

    @mock.patch("core.arch_framework.load_graph")
    @mock.patch("core.arch_framework.locate_kg_db")
    def test_framework_shape(self, locate, lgraph):
        f = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        f.close()
        try:
            locate.return_value = f.name
            lgraph.return_value = (make_nodes(FAKE_PP), make_adj())
            d = arch_framework.build_framework(FAKE_PP, {"project": "fake"})
            # 顶层契约
            self.assertIn("project", d)
            self.assertIn("framework", d)
            self.assertEqual(d["project"], "fake")
            fw = d["framework"]
            for k in ("id", "name", "modules", "children"):
                self.assertIn(k, fw, f"framework 根应含 {k}")
            # 入口启发：main_agent + cli_tool 归入口层，业务域不混入
            self.assertEqual(set(fw["modules"]),
                             {"app/main_agent", "app/cli_tool"})
            # 主功能节点契约（按域分组：agents / pipelines）
            domains = {c["id"]: c for c in fw["children"]}
            self.assertIn("agents", domains)
            self.assertIn("pipelines", domains)
            for c in fw["children"]:
                for k in ("id", "title", "modules", "children"):
                    self.assertIn(k, c, f"主功能节点应含 {k}")
            # 子功能节点契约（域内下一级分组）+ steps 字段
            agents = domains["agents"]
            subs = {s["id"]: s for s in agents["children"]}
            self.assertEqual(set(subs), {"agents:screenwriter", "agents:storyboard"})
            for s in agents["children"]:
                self.assertIn("steps", s, "子功能节点应含 steps")
                self.assertEqual(set(s["modules"]),
                                 {f"app/agents/{s['id'].split(':')[1]}"})
            # 测试模块 / __init__ 被过滤
            all_mods = set(fw["modules"])
            for c in fw["children"]:
                all_mods.update(c["modules"])
                for s in c["children"]:
                    all_mods.update(s["modules"])
            self.assertNotIn("app/tests/test_x", all_mods)
            self.assertNotIn("app/__init__", all_mods)
        finally:
            os.remove(f.name)

    @mock.patch("core.arch_framework.load_graph")
    @mock.patch("core.arch_framework.locate_kg_db")
    def test_no_graph_returns_empty_skeleton(self, locate, lgraph):
        locate.return_value = None
        lgraph.return_value = ({}, {})
        d = arch_framework.build_framework(FAKE_PP, {"project": "fake"})
        fw = d["framework"]
        self.assertEqual(fw["modules"], [])
        self.assertEqual(fw["children"], [])


class TestArchFrameworkNoLLM(unittest.TestCase):
    """开发方用例 2：静态兜底 desc 非空 + LLM 异常不阻断。"""

    def _build(self):
        with mock.patch("core.arch_framework.load_graph"), \
             mock.patch("core.arch_framework.locate_kg_db") as locate:
            locate.return_value = None
            return arch_framework.build_framework(FAKE_PP, {"project": "fake"})

    def test_static_fallback_desc_nonempty(self):
        d = arch_framework.build_framework_enhanced(FAKE_PP, {"project": "fake"},
                                                    with_llm=False)
        fw = d["framework"]
        # 静态兜底：根 + 所有后代 desc 均非空（空骨架根=「暂无归属模块」）
        def walk(n):
            self.assertTrue(n.get("desc"), f"节点 {n.get('id')} desc 应非空")
            for c in n.get("children", []):
                walk(c)
        walk(fw)

    def test_llm_exception_returns_skeleton(self):
        # LLM 构造异常 → except → 静态兜底，不阻断业务树输出
        with mock.patch("core.llm_integration.LLMIntegration",
                        side_effect=RuntimeError("boom")):
            d = arch_framework.enhance_framework_with_llm(self._build())
        self.assertIn("framework", d)
        self.assertTrue(d["framework"].get("desc"))


class TestArchViewContractV516(unittest.TestCase):
    """开发方用例 3：build_arch_data v5.16 契约（framework + modules_graph + summary）。"""

    def setUp(self):
        self.file = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.file.close()

    def tearDown(self):
        if os.path.exists(self.file.name):
            os.remove(self.file.name)

    def _build(self):
        with mock.patch("core.arch_view.analyze_gap") as agap, \
             mock.patch("core.arch_view.load_graph") as lgraph, \
             mock.patch("core.arch_view.locate_kg_db") as locate, \
             mock.patch("core.arch_framework.build_framework_enhanced") as bfwe:
            locate.return_value = self.file.name
            lgraph.return_value = ({
                "n:1": {"type": "module", "file_path": os.path.join(FAKE_PP, "app/order.py"),
                        "name": "app/order"}}, {})
            agap.return_value = {"gaps": [], "summary": {}, "alignment": {}}
            bfwe.return_value = {"framework": {"id": "__root__", "name": "入口层",
                                               "desc": "d", "modules": [], "children": []}}
            return arch_view.build_arch_data(FAKE_PP, {
                "version": "5.0", "project": "fake",
                "tech_roles": [{"id": "r1", "name": "角色1",
                                "target_modules": ["app/order"]}],
                "business_flows": []}), bfwe

    def test_contract_v516(self):
        d, _ = self._build()
        for k in ("project", "summary", "framework", "modules_graph", "meta"):
            self.assertIn(k, d, f"v5.16 契约应含顶层 {k}")
        mg = d["modules_graph"]
        for k in ("roles", "flows", "modules", "violations", "gaps"):
            self.assertIn(k, mg, f"modules_graph 应含 {k}")
        # v5.15 顶层字段不再出现在顶层（降级进 modules_graph）
        for k in ("roles", "modules", "flows", "violations"):
            self.assertNotIn(k, d, f"{k} 应降级进 modules_graph 而非顶层")
        # summary 字段
        for k in ("alignment", "module_total", "gap_total", "high"):
            self.assertIn(k, d["summary"])
        # build_framework_enhanced 被调用（framework 为业务树数据）
        self.assertEqual(d["framework"]["id"], "__root__")


class TestArchServeIndex(unittest.TestCase):
    """开发方用例 4：open_server 根路径引导页 + 文件名 escape。"""

    def _serve(self, d):
        return handlers_arch._serve_arch_static(d, host="127.0.0.1", port=0)

    def test_root_returns_guide_page(self):
        with tempfile.TemporaryDirectory() as d:
            # 两个产物（旧→新），另放一个特殊字符文件验证 escape
            open(os.path.join(d, "arch_canvas_20260101_000000_000001.html"), "w").close()
            open(os.path.join(d, "arch_canvas_20260921_120000_000002.html"), "w").close()
            open(os.path.join(d, "arch_canvas_a&b.html"), "w").close()
            try:
                r = self._serve(d)
                self.assertTrue(r["ok"])
                with urllib.request.urlopen(r["url"], timeout=5) as resp:
                    self.assertEqual(resp.status, 200)
                    ctype = resp.headers.get("Content-Type", "")
                    self.assertIn("text/html", ctype)
                    body = resp.read().decode("utf-8")
                self.assertIn("打开最新架构视图", body, "引导页应含最新直达按钮")
                # 最新产物 = 排序最后（20260921 > 20260101 > a&b）
                self.assertIn("arch_canvas_20260921_120000_000002.html", body)
                # 文件名 escape：& → &amp;（html.escape 防注入）
                self.assertIn("arch_canvas_a&amp;b.html", body)
                self.assertNotIn('href="arch_canvas_a&b.html"', body,
                                 "未转义 href 不应出现")
            finally:
                self._shutdown(d)

    def test_empty_dir_shows_placeholder(self):
        with tempfile.TemporaryDirectory() as d:
            try:
                r = self._serve(d)
                with urllib.request.urlopen(r["url"], timeout=5) as resp:
                    body = resp.read().decode("utf-8")
                self.assertEqual(resp.status, 200)
                self.assertIn("暂无产物", body)
            finally:
                self._shutdown(d)

    @staticmethod
    def _shutdown(d):
        h = handlers_arch._ARCH_SERVE.pop(d, None)
        if h:
            try:
                h["httpd"].shutdown()
            except Exception:
                pass


class TestFrameworkRootId(unittest.TestCase):
    """开发方用例 5：根节点 __root__ 稳定 id + LLM merge 覆盖入口层。"""

    def test_root_id_is_stable(self):
        with mock.patch("core.arch_framework.load_graph"), \
             mock.patch("core.arch_framework.locate_kg_db") as locate:
            locate.return_value = None
            d = arch_framework.build_framework(FAKE_PP, {"project": "fake"})
        self.assertEqual(d["framework"]["id"], "__root__")

    def test_merge_llm_result_overrides_root(self):
        fw = {"framework": {
            "id": "__root__", "name": "入口层", "desc": "", "modules": ["app/main"],
            "children": [{"id": "agents", "title": "智能体", "desc": "",
                          "modules": ["app/agents/a"], "children": [], "steps": []}]}}
        result = {"__root__": {"title": "项目总入口",
                               "desc": "统一入口，负责启动与调度",
                               "steps": ["启动", "调度"]}}
        out = arch_framework._merge_llm_result(fw, result)
        root = out["framework"]
        self.assertEqual(root["name"], "入口层")  # name 不覆盖（title 语义并入 name? 见注）
        # merge 通过 walk 覆盖 title/desc —— 根节点 title/desc 被 LLM 结果覆盖
        # 注：_merge_llm_result 写 node["title"]/node["desc"]，根节点 title 亦然
        self.assertIn("title", root)
        self.assertEqual(root["title"], "项目总入口")
        self.assertEqual(root["desc"], "统一入口，负责启动与调度")
        # 非 leaf 节点 steps 不覆盖
        self.assertNotIn("steps", root)


if __name__ == "__main__":
    unittest.main()
