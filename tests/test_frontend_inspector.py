# -*- coding: utf-8 -*-
"""
回归测试 —— core/frontend_inspector.py 前端交互审查模块。

覆盖：
1. 静态枚举按钮：<button>、<a class=btn>、<input type=submit>、onclick 元素，
   记录 text / file / line / events / form / has_confirm / disabled。
2. 菜单树：L1→L5 层级嵌套 <ul>/<li> 递归构建，check_levels 裁剪。
3. LLM 审查：合法 JSON 被解析为 findings；非法响应降级为 pending finding。
4. 降级路径：LLM 不可用 / 无 url 的 runtime 模式，均有明确降级输出。

说明：
- 仅使用 unittest 标准库，不依赖 pytest。
- 用临时目录构造 HTML 样本，避免依赖 demo-app。
- LLM 通过 mock 打补丁（不联网、不消耗 token）。
- 在文件顶部把项目根目录插入 sys.path，确保可独立运行。
"""

import os
import sys
import tempfile
import unittest

# ── 使测试可独立运行：把项目根目录插入 sys.path ──
PROJECT_ROOT = r"C:\Users\30822\Desktop\1111\Coderef-Ai\Coderef-Ai-master"
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)
if os.getcwd() != PROJECT_ROOT:
    try:
        os.chdir(PROJECT_ROOT)
    except OSError:
        pass


SAMPLE_HTML = """<!DOCTYPE html>
<html>
<head><title>测试页</title></head>
<body>
  <form id="mainForm">
    <input type="text" name="name">
    <input type="submit" value="提交表单">
    <button onclick="deleteItem(1)">删除</button>
    <button onclick="goSave()" disabled>禁用保存</button>
    <button onclick="if(confirm('确定?')){doIt()}">确认删除</button>
    <a class="btn" href="/save" onclick="saveLink()">链接保存</a>
  </form>
  <ul>
    <li><a href="/">系统管理</a>
      <ul>
        <li><a href="/users">用户中心</a>
          <ul>
            <li><a href="/users/list">用户管理</a>
              <ul>
                <li><a href="/roles">角色权限</a>
                  <ul>
                    <li><a href="/roles/view">查看权限</a></li>
                    <li><a href="/roles/edit">编辑权限</a></li>
                  </ul>
                </li>
              </ul>
            </li>
          </ul>
        </li>
      </ul>
    </li>
  </ul>
  <a href="#" onclick="dead()">死链项</a>
</body>
</html>
"""


class FakeLLM:
    """Mock LLM：返回可控 JSON findings，用于验证解析与结构。"""

    def __init__(self, payload=None, available=True):
        self.payload = payload
        self.available = available

    @property
    def client(self):
        return object() if self.available else None

    def chat_completion(self, messages, **kwargs):
        import json
        return json.dumps(self.payload, ensure_ascii=False) if self.payload is not None else ""

    def _try_parse_json(self, text):
        import json as _json
        try:
            return _json.loads(text) if text else None
        except Exception:
            return None


def _temp_project(html: str) -> str:
    """在临时目录创建 index.html，返回目录路径"""
    d = tempfile.mkdtemp(prefix="coderef_fe_")
    with open(os.path.join(d, "index.html"), "w", encoding="utf-8") as f:
        f.write(html)
    return d


class TestFrontendInspector(unittest.TestCase):
    """FrontendInspector 静态枚举 + 审查"""

    def setUp(self):
        self.project = _temp_project(SAMPLE_HTML)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.project, ignore_errors=True)

    def test_static_enum_buttons(self):
        from core.frontend_inspector import FrontendInspector
        llm = FakeLLM(available=False)  # 只测静态枚举，不触发 LLM
        r = FrontendInspector(llm=llm).inspect(self.project, mode="static")
        buttons = r["buttons"]
        texts = [b.get("text", b.get("label", "")) for b in buttons]
        # 应枚举到：提交表单(input)、删除(button)、禁用保存(button)、确认删除(button)、链接保存(a.btn)
        self.assertIn("提交表单", texts)
        self.assertIn("删除", texts)
        self.assertIn("禁用保存", texts)
        self.assertIn("链接保存", texts)
        # 确认弹窗标记：确认删除 应 has_confirm=True
        confirm_btn = next(b for b in buttons if "确认删除" in (b.get("text", b.get("label", ""))))
        self.assertTrue(confirm_btn.get("has_confirm"))
        # 禁用标记
        disabled_btn = next(b for b in buttons if "禁用保存" in (b.get("text", b.get("label", ""))))
        self.assertTrue(disabled_btn.get("disabled"))

    def test_menu_tree_l1_to_l5(self):
        from core.frontend_inspector import FrontendInspector
        llm = FakeLLM(available=False)
        r = FrontendInspector(llm=llm).inspect(self.project, mode="static", check_levels=[1, 2, 3, 4, 5])
        tree = r["menu_tree"]
        self.assertTrue(len(tree) >= 1)
        # 找到系统管理节点
        root = tree[0]
        self.assertEqual(root.get("label"), "系统管理")
        self.assertEqual(root.get("level"), 1)
        # 递归找到 L5 节点
        l5 = _collect_level(root, 5)
        self.assertTrue(len(l5) >= 2, "应存在两个 L5 叶子节点")

    def test_check_levels_crop(self):
        from core.frontend_inspector import FrontendInspector
        llm = FakeLLM(available=False)
        r = FrontendInspector(llm=llm).inspect(self.project, mode="static", check_levels=[1, 2])
        tree = r["menu_tree"]
        # 裁剪到 L2 后，不应出现 L3 节点
        self.assertEqual(_max_level(tree), 2)

    def test_llm_findings_parsed(self):
        from core.frontend_inspector import FrontendInspector
        payload = [{"category": "交互正确性", "finding": "删除无确认", "severity": "high",
                    "suggestion": "加 confirm", "verified": False}]
        llm = FakeLLM(payload=payload)
        r = FrontendInspector(llm=llm).inspect(self.project, mode="static")
        self.assertTrue(len(r["findings"]) >= 1)
        f = r["findings"][0]
        self.assertEqual(f["category"], "交互正确性")
        self.assertEqual(f["severity"], "high")

    def test_llm_invalid_degrades(self):
        from core.frontend_inspector import FrontendInspector
        llm = FakeLLM(payload=None)  # 返回空串 → 解析失败
        r = FrontendInspector(llm=llm).inspect(self.project, mode="static")
        # 有降级结论，category 为待确认
        self.assertTrue(len(r["findings"]) >= 1)
        self.assertIn("确认", r["findings"][0].get("category", ""))
        self.assertFalse(r["findings"][0].get("verified", True))

    def test_runtime_without_url_degrades_to_static(self):
        from core.frontend_inspector import FrontendInspector
        llm = FakeLLM(available=False)
        r = FrontendInspector(llm=llm).inspect(self.project, mode="runtime", url=None)
        # runtime 无 url 应降级为静态，summary 说明
        self.assertIn("buttons", r)
        self.assertIn("menu_tree", r)
        self.assertIn("静态", r["summary"])


def _collect_level(node, level):
    """收集指定层级的节点"""
    out = []
    if node.get("level") == level:
        out.append(node)
    for child in node.get("children", []):
        out.extend(_collect_level(child, level))
    return out


def _max_level(nodes):
    """返回菜单树中出现的最大层级（nodes 为根节点列表）"""
    m = 0
    for node in nodes:
        m = max(m, node.get("level", 0))
        for child in node.get("children", []):
            m = max(m, _max_level([child]))
    return m


if __name__ == "__main__":
    unittest.main()