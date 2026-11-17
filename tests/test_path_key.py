# -*- coding: utf-8 -*-
"""U-71 回归：项目路径大小写 / 分隔符变体必须落到同一份派生标识。

Windows 下 `D:\\proj` 与 `d:\\proj` 是同一项目。旧实现直接
`md5(abspath(path))`，两者会得到两个哈希 → 图谱库 / 审计发现 / 状态文件
被拆成两套，`locate_kg_db` 定位不到已建图并触发 G-B 误报。
"""
import os
import sys
import tempfile
import unittest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from core.path_key import project_key


class ProjectKeyTest(unittest.TestCase):
    def test_case_and_separator_variants_share_key(self):
        p = r"D:\CodeRef-GoodJob\Coderef-Ai-master"
        self.assertEqual(project_key(p), project_key(p.lower()))
        self.assertEqual(project_key(p), project_key(p.replace("\\", "/")))
        self.assertEqual(project_key(p), project_key(p + os.sep))

    def test_relative_path_normalized(self):
        cwd = os.getcwd()
        self.assertEqual(project_key("."), project_key(cwd))

    def test_home_tilde_expanded(self):
        """U-88：`~/proj` 必须展开为 home，而不是 <cwd>/~/proj。"""
        home = os.path.expanduser("~")
        self.assertEqual(project_key("~/proj"),
                         project_key(os.path.join(home, "proj")))


class DerivedArtifactNameTest(unittest.TestCase):
    def test_phash_stable_across_case(self):
        from core.pr_whitelist import _phash
        self.assertEqual(_phash(r"D:\proj"), _phash(r"d:\proj"))

    def test_memory_state_path_stable_across_case(self):
        from core.memory_layer import memory_layer
        from core.review_strategy import review_advisor
        a = memory_layer._state_path(r"D:\proj")
        b = memory_layer._state_path(r"d:\proj")
        self.assertEqual(a, b)
        sel = getattr(review_advisor, "_state_path", None)
        if sel is not None:
            self.assertEqual(sel(r"D:\proj"), sel(r"d:\proj"))


class KgDbCaseVariantTest(unittest.TestCase):
    def test_kg_db_name_identical_for_case_variants(self):
        from core.code_knowledge_graph import CodeKnowledgeGraph
        with tempfile.TemporaryDirectory() as tmp:
            upper = tmp.upper()
            lower = tmp.lower()
            if upper == lower:
                self.skipTest("当前平台大小写不敏感或无大小写差异")
            a = CodeKnowledgeGraph(upper).db_path
            b = CodeKnowledgeGraph(lower).db_path
            self.assertEqual(os.path.basename(a), os.path.basename(b))
            self.assertTrue(os.path.isdir(os.path.dirname(a)))

    def test_locate_kg_db_finds_variant_built_db(self):
        from core.arch_audit import locate_kg_db
        from core.code_knowledge_graph import CodeKnowledgeGraph
        with tempfile.TemporaryDirectory() as tmp:
            upper = tmp.upper()
            lower = tmp.lower()
            if upper == lower:
                self.skipTest("当前平台大小写不敏感或无大小写差异")
            kg = CodeKnowledgeGraph(upper)
            kg._init_schema()
            kg.close()
            self.assertEqual(
                os.path.basename(locate_kg_db(lower) or ""),
                os.path.basename(kg.db_path))


class LegacyIndexDbTest(unittest.TestCase):
    """U-92：`.coderef/index/coderef_kg_*.db` 历史库必须与现行库分目录，防误读。"""

    def test_legacy_kg_db_moved_into_legacy_dir(self):
        from core.code_knowledge_graph import CodeKnowledgeGraph
        with tempfile.TemporaryDirectory() as tmp:
            idx = os.path.join(tmp, ".coderef", "index")
            os.makedirs(idx)
            legacy = os.path.join(idx, "coderef_kg_aaaaaaaaaaaa.db")
            with open(legacy, "wb") as f:
                f.write(b"legacy")
            CodeKnowledgeGraph(tmp)
            self.assertFalse(os.path.exists(legacy))
            self.assertTrue(os.path.exists(
                os.path.join(idx, "legacy", "coderef_kg_aaaaaaaaaaaa.db")))
            # 现行库仍落在 cache/kg，不受迁移影响
            self.assertIn(os.path.join("cache", "kg"),
                          CodeKnowledgeGraph(tmp).db_path)

    def test_legacy_cache_kg_moved_current_kept(self):
        """U-92 扩展：cache/kg 旧 key 库也要归档，当前库自身不得被移动。"""
        import hashlib
        from core.code_knowledge_graph import CodeKnowledgeGraph
        with tempfile.TemporaryDirectory() as tmp:
            kgdir = os.path.join(tmp, "cache", "kg")
            os.makedirs(kgdir)
            cur = hashlib.md5(project_key(tmp).encode()).hexdigest()[:12]
            cur_db = os.path.join(kgdir, cur + ".db")
            old_db = os.path.join(kgdir, "b409b7992e01.db")
            for p in (cur_db, old_db):
                with open(p, "wb") as f:
                    f.write(b"x")
            CodeKnowledgeGraph(tmp)
            self.assertTrue(os.path.exists(cur_db))
            self.assertFalse(os.path.exists(old_db))
            self.assertTrue(os.path.exists(
                os.path.join(kgdir, "legacy", "b409b7992e01.db")))


    def test_canonical_db_protected_when_db_path_points_legacy(self):
        """显式 db_path 指向旧库时，默认落点的现行库不能被迁移。"""
        import hashlib
        from core.code_knowledge_graph import CodeKnowledgeGraph
        with tempfile.TemporaryDirectory() as tmp:
            kgdir = os.path.join(tmp, "cache", "kg")
            os.makedirs(kgdir)
            cur = hashlib.md5(project_key(tmp).encode()).hexdigest()[:12]
            canonical = os.path.join(kgdir, cur + ".db")
            legacy = os.path.join(kgdir, "b409b7992e01.db")
            for p in (canonical, legacy):
                with open(p, "wb") as f:
                    f.write(b"x")
            CodeKnowledgeGraph(tmp, db_path=legacy)
            self.assertTrue(os.path.exists(canonical))
            self.assertTrue(os.path.exists(legacy))


class UpgradeNoteTest(unittest.TestCase):
    def test_version_carries_one_line_upgrade_sentence(self):
        """用户规模小，不做自动迁移：version 直接给一句可转告编程 AI 的话。"""
        import json
        from core.mcp_server import _version
        out = json.loads(_version({}))
        line = (out.get("upgrade_note") or {}).get("tell_your_ai", "")
        self.assertIn("5.21.18", line)
        self.assertIn("coderef_audit", line)
        self.assertIn("coderef_operation_memory(action=sync", line)


class KgStatsHonestyTest(unittest.TestCase):
    def test_full_build_returns_db_counts(self):
        """全量构建返回的 nodes/edges 必须是落库真值，不能用内存累加器。"""
        from unittest import mock
        from core.code_knowledge_graph import CodeKnowledgeGraph
        with tempfile.TemporaryDirectory() as tmp:
            kg = CodeKnowledgeGraph(tmp)
            kg._init_schema()

            def _fake(analysis, stats, only):
                stats["nodes"] += 99
                stats["edges"] += 99

            with mock.patch.object(kg, "_build_from_analysis", side_effect=_fake):
                out = kg.build(analysis=object())
            self.assertEqual(out["nodes"], 0)
            self.assertEqual(out["edges"], 0)
            kg.close()


if __name__ == "__main__":
    unittest.main()
