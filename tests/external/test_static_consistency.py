# -*- coding: utf-8 -*-
"""静态一致性矩阵：单一事实源不再分叉（默认快跑）。

覆盖：
- 工具枚举 ↔ 注册表常量（scan 维度 / whitelist action）
- 文档工具数 ↔ manifest 实际数（README / MCP_SETUP）
"""
import os
import re
import sys
import unittest

PROJECT_ROOT = os.environ.get("CODEREF_SRC",
                              os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from core.tools_manifest import (  # noqa: E402
    BUILTIN_TOOLS, _SINGLE_TOOL_LABELS, WHITELIST_ACTIONS,
)
from core import canvas_engine  # noqa: E402

STRICT = os.environ.get("CODEREF_STRICT_KNOWN") == "1"


def known(bug: str, why: str):
    # 已知缺陷均已修复：默认即阻断（保留 bug 注释语义）
    return lambda fn: fn


def _schema(entry):
    return entry.get("inputSchema") if isinstance(entry.get("inputSchema"), dict) else {}


def _entry(name):
    for e in BUILTIN_TOOLS:
        if e.get("name") == name:
            return e
    return {}


class ToolEnumConsistencyTest(unittest.TestCase):
    """schema 里的 enum 必须与代码常量同源，防止加维度/加 action 时漏改一处。"""

    def test_scan_enum_matches_labels(self):
        schema = _schema(_entry("coderef_scan"))
        enum = ((schema.get("properties") or {}).get("tool") or {}).get("enum") or []
        labels = [k for k, _ in _SINGLE_TOOL_LABELS]
        self.assertEqual(list(enum), labels,
                         "coderef_scan 的 tool enum 与 _SINGLE_TOOL_LABELS 不一致")

    def test_whitelist_enum_matches_actions(self):
        schema = _schema(_entry("coderef_whitelist"))
        enum = ((schema.get("properties") or {}).get("action") or {}).get("enum") or []
        self.assertEqual(list(enum), list(WHITELIST_ACTIONS),
                         "coderef_whitelist action enum 与 WHITELIST_ACTIONS 不一致")


class DocsToolCountTest(unittest.TestCase):
    """对外文档声称的工具数必须等于 manifest 实际数（防文档漂移）。"""

    def test_docs_counts_match_manifest(self):
        names = [t.get("name") for t in BUILTIN_TOOLS if t.get("name")]
        expected = len(names)
        files = [
            os.path.join(PROJECT_ROOT, "README.md"),
            os.path.join(PROJECT_ROOT, "MCP_SETUP.md"),
        ]
        checked = 0
        for fp in files:
            if not os.path.isfile(fp):
                continue
            with open(fp, encoding="utf-8", errors="ignore") as f:
                text = f.read()
            counts = re.findall(r"(\d+)\s*个工具", text)
            if not counts:
                continue
            checked += 1
            for c in counts:
                self.assertEqual(int(c), expected,
                                 f"{os.path.basename(fp)} 声称 {c} 个工具，"
                                 f"manifest 实际 {expected}")
        self.assertGreater(checked, 0, "README/MCP_SETUP 未找到工具数声明，测试失效")


class CanvasFaviconTest(unittest.TestCase):
    """画布模板必须带空 favicon，避免直开页面请求 /favicon.ico 报 404。"""

    @known("U-68", "canvas_engine 模板未带空 favicon，arch_flow 直开页 console 报 favicon 404")
    def test_canvas_template_has_empty_favicon(self):
        tpl = canvas_engine._HTML_TEMPLATE
        self.assertIn('rel="icon"', tpl)
        self.assertIn("data:,", tpl)


if __name__ == "__main__":
    unittest.main(verbosity=2)
