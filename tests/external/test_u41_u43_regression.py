# -*- coding: utf-8 -*-
"""U-41 / U-43 回归测试（开发方 v5.13.8 / v5.13.9 回报，独立回归落位 coderef-src）。

- U-41（v5.13.8）：arch_verify health 维度恒 0.5/raw=null 的修复——
  _score_health 改读 audit["summary"]["health"]（0-10）归一 + raw 透传。
- U-43（v5.13.9）：registry design 登记禁止写入未经核验的采用数声明
  （_ADOPTION_DECL_RE 守卫），防「登记假设当事实」。

运行：python -m unittest tests.test_u41_u43_regression -v
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

from core.arch_alignment_verifier import ArchAlignmentVerifier
from core.design_registry import DesignRegistry, _ADOPTION_DECL_RE


# ═══════════════════════════════════════════════════════════════
# U-41 · arch_verify health 透传
# ═══════════════════════════════════════════════════════════════

class ArchVerifyHealthU41Test(unittest.TestCase):
    """_score_health 读 audit 真实健康(0-10)归一 + raw 透传；verify 10% 权重计入总分。"""

    def _score_health(self, health_or_summary):
        with mock.patch("core.arch_audit.audit") as m:
            if isinstance(health_or_summary, dict):
                m.return_value = health_or_summary
            else:
                m.return_value = {"summary": {"health": health_or_summary}}
            return ArchAlignmentVerifier()._score_health("__proj__", "__db__")

    def test_score_health_returns_real_normalized(self):
        # audit 健康分(0-10) → (归一默认, 原始raw) 三态
        self.assertEqual(self._score_health(2.0), (0.2, 2.0))
        self.assertEqual(self._score_health(5.0), (0.5, 5.0))
        self.assertEqual(self._score_health(10.0), (1.0, 10.0))

    def test_score_health_no_health_conservative_default(self):
        # 无 health / no_code：raw=None，score 落保守默认 0.5
        self.assertEqual(self._score_health({"summary": {}}), (0.5, None))
        self.assertEqual(self._score_health({"summary": None}), (0.5, None))

    def test_verify_health_dimension_reflects_audit(self):
        # 隔离 mock 使 verify 走通：health 维度透传 raw + 10% 权重计入总分
        v = ArchAlignmentVerifier()
        dims = {
            "responsibility": {"score": 1.0, "weight": 0.4, "assigned": 0, "total_modules": 0},
            "dependency": {"score": 1.0, "weight": 0.3, "violations": 0, "module_edges": 0},
            "business": {"score": 1.0, "weight": 0.2, "implemented_roles": 0, "total_roles": 0},
            "health": {"score": 0.2, "weight": 0.1, "arch_health_raw": 2.0},
        }
        with mock.patch.object(v, "_load_stored_arch", return_value={"layers": []}), \
                mock.patch.object(v, "_score_dimensions", return_value=dims), \
                mock.patch("core.arch_alignment_verifier.locate_kg_db", return_value="x.db"), \
                mock.patch("os.path.exists", return_value=True), \
                mock.patch("core.arch_alignment_verifier.load_graph", return_value=({}, {})), \
                mock.patch("core.arch_alignment_verifier.analyze_gap", return_value={"gaps": []}):
            r = v.verify("__proj__")
        self.assertEqual(r["dimensions"]["health"]["score"], 0.2)
        self.assertEqual(r["dimensions"]["health"]["arch_health_raw"], 2.0)
        # 其余三维 1.0、health 0.2 且权重 0.1：total = .4+.3+.2+.02 = 0.92
        self.assertAlmostEqual(r["score"], 0.92)


# ═══════════════════════════════════════════════════════════════
# U-43 · registry 采用数声明守卫
# ═══════════════════════════════════════════════════════════════

class DesignRegistryAdoptionGuardTest(unittest.TestCase):
    """design add 登记描述含「N workflow 采用」即拒绝，防登记假设当事实。"""

    def setUp(self):
        fd, self._tmp = tempfile.mkstemp(suffix=".json")
        os.close(fd)
        self.reg = DesignRegistry(registry_path=self._tmp)
        self.addCleanup(lambda: os.path.exists(self._tmp) and os.remove(self._tmp))

    def test_rejects_unverified_adoption_decl(self):
        with self.assertRaises(ValueError) as cm:
            self.reg.manage(
                project_path="__proj__", action="add",
                name="bad_design", description="多源路由：主源→次源→回退，被 ≥2 workflow 采用。",
            )
        self.assertIn("采用", str(cm.exception))

    def test_accepts_factual_description(self):
        r = self.reg.manage(
            project_path="__proj__", action="add",
            name="fact_design",
            description="多数据源降级路由：主源→次源→回退，直到任一源成功。",
        )
        self.assertTrue(r.get("ok"))
        d = self.reg.get_design("fact_design")
        self.assertIsNotNone(d)
        self.assertEqual(d["canonical"], "fact_design")

    def test_regex_variants(self):
        self.assertTrue(_ADOPTION_DECL_RE.search("该模式被 3 个 workflow 采用"))
        self.assertTrue(_ADOPTION_DECL_RE.search("已被 5 个工作流采用"))
        self.assertTrue(_ADOPTION_DECL_RE.search("≥2 workflow采用"))
        self.assertTrue(_ADOPTION_DECL_RE.search("被 2 个 workflow 采用"))
        self.assertFalse(_ADOPTION_DECL_RE.search("多源降级路由：主→次→回退"))
        self.assertFalse(_ADOPTION_DECL_RE.search("用于多数据源降级"))

    def test_existing_entry_no_decl(self):
        # 现有 multi_source_route 描述不应含禁用声明（读默认注册表，缺失则跳过）
        reg = DesignRegistry()
        d = reg.get_design("multi_source_route")
        if d is None:
            self.skipTest("默认注册表无 multi_source_route")
        self.assertFalse(_ADOPTION_DECL_RE.search(d.get("description", "")))


if __name__ == "__main__":
    unittest.main()