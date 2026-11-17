# -*- coding: utf-8 -*-
"""Test for core.project_overview —— 项目总览 report(action=overview) 聚合渲染。"""

import os
import tempfile
import unittest
from unittest.mock import patch

from core.project_overview import _md_to_html, _wiki_href, render_overview


def _mk(fields: dict) -> dict:
    base = {
        "issues": [{"id": "iss_1", "gap_type": "cycle", "title": "循环依赖 A→B",
                    "module": "m", "severity": "high", "status": "Detected",
                    "priority": "P1", "assignee": "u1"}],
        "cycles": [{"id": "cyc_1", "name": "基线"}],
        "summary": {}, "status_counts": {}, "trend": [],
        "active_cycle": {"id": "cyc_1", "name": "基线",
                         "description": "对齐度0.9,管线100%合规"},
        "views_counts": {"open": 24, "rejected": 17},
        "issue_details": {"iss_1": {"ok": True, "snapshot": {"x": 1},
                                    "events": [{"at": "t", "action": "detect"}]}},
        "project_path": "d:/p",
    }
    base.update(fields)
    return base


class MdToHtmlTest(unittest.TestCase):
    def test_frontmatter_skipped(self):
        md = "---\ntype: overview\ntitle: X\n---\n# 标题\n正文"
        out = _md_to_html(md)
        self.assertNotIn("type:", out)
        self.assertIn("<h2>标题</h2>", out)

    def test_table_rendered(self):
        md = "| 文档 | 内容 |\n|------|------|\n| OVERVIEW | 概览 |"
        out = _md_to_html(md)
        self.assertIn("<table", out)
        self.assertIn("OVERVIEW", out)
        self.assertNotIn("| OVERVIEW", out)

    def test_inline_bold_link(self):
        md = "这是 **加粗**，看 [链接](a.md)。"
        out = _md_to_html(md)
        self.assertIn("<b>加粗</b>", out)
        self.assertIn("<a href=\"a.md\"", out)

    def test_unsafe_link_scheme_rejected(self):
        for url in ("javascript:alert(1)", "data:text/html;base64,xxx"):
            out = _md_to_html(f"点 [x]({url})")
            self.assertNotIn(f"href=\"{url}\"", out)
            self.assertIn("[x]", out)          # 保留纯文本，不生成 <a>

    def test_safe_absolute_link_allowed(self):
        out = _md_to_html("看 [文档](https://example.com/x.md)")
        self.assertIn('<a href="https://example.com/x.md"', out)

    def test_code_block_rendered_as_pre_not_escaped(self):
        out = _md_to_html("```py\nprint(1)\n```")
        self.assertIn("<pre", out)
        self.assertNotIn("<p><pre", out)      # 不被行级处理包裹进 <p>
        self.assertIn("print(1)", out)

    def test_wiki_href_same_drive_returns_relative(self):
        rel = _wiki_href("d:/p/docs/wiki/README.md", "d:/p/.coderef")
        self.assertNotIn("file:///", rel)
        self.assertIn("/docs/wiki/README.md", rel)

    def test_wiki_href_cross_drive_falls_back_to_uri(self):
        rel = _wiki_href("d:/p/docs/wiki/README.md", "c:/tmp")
        self.assertTrue(rel.startswith("file:///"))


class RenderOverviewTest(unittest.TestCase):
    def test_sections_and_html_with_mocks(self):
        health = {"ok": True, "action": "health", "score": 88,
                  "score_label": "良", "tally": {"high": 1, "medium": 2},
                  "top_risks": ["风险A"], "summary": "确定性总结"}
        wiki = {"available": True, "index_html": "<p>idx</p>",
                "overview_html": "<p>ov</p>", "files": ["README.md"],
                "directory": "docs/wiki/"}
        with tempfile.TemporaryDirectory() as tmp:
            pp = os.path.join(tmp, "prj")
            os.makedirs(os.path.join(pp, "docs", "wiki"), exist_ok=True)
            os.makedirs(os.path.join(pp, ".coderef"), exist_ok=True)
            with patch("core.project_overview._govern_data",
                       return_value=_mk({})), \
                 patch("core.project_overview._health_verdict",
                       return_value=health), \
                 patch("core.project_overview._wiki_sections",
                       return_value=wiki), \
                 patch("core.project_overview._find_arch_canvas",
                       return_value=None):
                r = render_overview(pp, output_dir=os.path.join(pp, ".coderef"))
            self.assertTrue(os.path.isfile(
                os.path.join(pp, ".coderef", "project_overview.html")))
            self.assertEqual(r["sections"]["health"], True)
            self.assertEqual(r["sections"]["wiki"], True)
            self.assertEqual(r["sections"]["arch_canvas"], False)
            with open(os.path.join(pp, ".coderef", "project_overview.html"),
                      encoding="utf-8") as f:
                html_str = f.read()
            self.assertIn("项目总览", html_str)
            self.assertIn("88", html_str)          # 健康分内联
            self.assertIn("循环依赖 A→B", html_str)  # 完整标题内联
            self.assertIn("尚未生成架构图", html_str)  # 诚实占位（无画布且无 workflow_graph）

    def test_fallback_to_workflow_graph_when_no_canvas(self):
        health = {"ok": False, "score": None, "summary": ""}
        wiki = {"available": False}
        with tempfile.TemporaryDirectory() as tmp:
            pp = os.path.join(tmp, "prj")
            os.makedirs(os.path.join(pp, ".gitnexus"), exist_ok=True)
            wf_html = os.path.join(pp, ".gitnexus", "workflow_graph_now.html")
            with open(wf_html, "w", encoding="utf-8") as f:
                f.write("<html><body>调用图内容</body></html>")
            # _find_arch_canvas 返回 None（无 .coderef/arch_canvas），
            # _find_workflow_graph 即降级命中 .gitnexus 下的 workflow_graph
            with patch("core.project_overview._govern_data",
                       return_value=_mk({})), \
                 patch("core.project_overview._health_verdict",
                       return_value=health), \
                 patch("core.project_overview._wiki_sections",
                       return_value=wiki):
                r = render_overview(pp, output_dir=os.path.join(pp, ".coderef"))
            self.assertTrue(r["arch_fallback"].startswith("workflow_graph_"))
            self.assertEqual(r["sections"]["arch_canvas"], False)
            self.assertEqual(r["sections"]["workflow_graph"], True)
            with open(os.path.join(pp, ".coderef", "project_overview.html"),
                      encoding="utf-8") as f:
                html_str = f.read()
            self.assertIn("workflow_graph_now.html", html_str)
            self.assertIn("工作流调用图", html_str)

    def test_interactive_false_hides_transition(self):
        payload = _mk({})
        payload["issues"][0]["status"] = "Verified"
        with tempfile.TemporaryDirectory() as tmp:
            with patch("core.project_overview._govern_data",
                       return_value=payload), \
                 patch("core.project_overview._health_verdict",
                       return_value={"ok": False, "score": None,
                                     "summary": ""}), \
                 patch("core.project_overview._wiki_sections",
                       return_value={"available": False}), \
                 patch("core.project_overview._find_arch_canvas",
                       return_value=None):
                r = render_overview(tmp, interactive=False)
            self.assertFalse(r.get("interactive"))
            with open(r["overview_html"], encoding="utf-8") as f:
                html_str = f.read()
            self.assertIn("只读模式", html_str)


if __name__ == "__main__":
    unittest.main()