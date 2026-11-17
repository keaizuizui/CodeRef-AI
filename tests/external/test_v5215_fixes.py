# -*- coding: utf-8 -*-
"""v5.21.15 定向回归：D-1 建图确定性 / D-2 file 限定 / D-3 whitelist / D-5 重定标。"""
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import unittest
from unittest import mock

PROJECT_ROOT = os.environ.get("CODEREF_SRC",
                              os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from core.mcp_server import Server  # noqa: E402
from core.code_analyzer import CodeAnalyzer  # noqa: E402
from core.code_knowledge_graph import CodeKnowledgeGraph  # noqa: E402
from core.pipeline_runner import whitelist_add, whitelist_clear  # noqa: E402


class _Base(unittest.TestCase):

    def setUp(self):
        self.td = tempfile.mkdtemp(prefix="v5215_")
        self.server = Server()

    def tearDown(self):
        shutil.rmtree(self.td, ignore_errors=True)

    def call(self, name, args):
        return json.loads(self.server._run(name, dict(args, background=False)))

    def write(self, p, rel, text):
        fp = os.path.join(p, rel)
        os.makedirs(os.path.dirname(fp), exist_ok=True)
        with open(fp, "w", encoding="utf-8") as f:
            f.write(text)
        return fp


class BuildDeterminismTest(_Base):

    def test_twice_build_identical_and_provenance(self):
        p = os.path.join(self.td, "kg")
        self.write(p, "core/a.py", "def add(a, b):\n    return a + b\n")
        self.write(p, "core/b.py",
                   "from core.a import add\n\ndef run():\n    return add(1, 2)\n")
        analysis = CodeAnalyzer().analyze_project(p, force_reanalyze=True)
        kg = CodeKnowledgeGraph(p)
        kg.build(analysis=analysis, builder="first")

        def counts():
            con = sqlite3.connect(kg.db_path)
            try:
                nodes = con.execute("SELECT COUNT(*) FROM nodes").fetchone()[0]
                edges = con.execute("SELECT COUNT(*) FROM edges").fetchone()[0]
                calls = con.execute(
                    "SELECT COUNT(*) FROM edges WHERE type='CALLS'").fetchone()[0]
                built_by = dict(con.execute(
                    "SELECT key,value FROM meta").fetchall()).get("built_by", "")
            finally:
                con.close()
            return nodes, edges, calls, built_by

        c1 = counts()
        kg.build(analysis=analysis, builder="second")
        c2 = counts()
        self.assertEqual(c1[:3], c2[:3],
                         f"两次全量建图 nodes/edges/calls 不一致: {c1} vs {c2}")
        self.assertEqual(c2[3], "second",
                         f"built_by 应指向最后一次建图者: {c2}")
        kg.close()


class VerifyFindingsFileConstraintTest(_Base):

    def test_same_name_outside_file_not_confirmed(self):
        p = os.path.join(self.td, "d2")
        self.write(p, "core/main.py", "def main():\n    return 1\n")
        self.write(p, "docs/legacy/old.py", "def main():\n    return 2\n")
        analysis = CodeAnalyzer().analyze_project(p, force_reanalyze=True)
        CodeKnowledgeGraph(p).build(analysis=analysis, builder="test")
        r = self.call("coderef_verify_findings", {
            "project_path": p,
            "findings": [{"title": "main 存在", "file": "core/main.py",
                          "symbols": ["main"]}]})
        self.assertTrue(r.get("ok"), r)
        item = (r.get("results") or [])[0]
        sym = (item.get("evidence") or {}).get("symbols", [{}])[0]
        self.assertGreaterEqual(sym.get("same_name_elsewhere", 0), 1,
                                f"应报告文件外同名: {sym}")
        self.assertTrue(sym.get("file_constrained"),
                        f"必须按 file 限定: {sym}")
        self.assertTrue(sym.get("matched_in_file"),
                        f"core/main.py 内的 main 应命中: {sym}")


class OwaspWhitelistDirTest(_Base):

    def test_whitelist_dir_excluded_from_owasp(self):
        p = os.path.join(self.td, "d3")
        self.write(p, "_refactor_backup/bad.py",
                   "API_KEY = 'sk-test-1234567890abcdef'\n"
                   "import os\nos.system('rm -rf /')\n")
        self.write(p, "core/ok.py", "def add(a, b):\n    return a + b\n")
        whitelist_clear(p)
        whitelist_add(p, [{"rule": "dir", "dir": "_refactor_backup"}])
        try:
            r = self.call("coderef_owasp", {"project_path": p})
        finally:
            whitelist_clear(p)
        self.assertEqual(r.get("status"), "completed", r)
        raw = json.dumps(r, ensure_ascii=False)
        self.assertNotIn("_refactor_backup", raw,
                         "whitelist dir 应被 OWASP 扫描排除")


class ArchGapRecalibrationTest(_Base):

    def test_missing_ratio_drops_and_stale_clears(self):
        p = os.path.join(self.td, "d5")
        self.write(p, "new_dir/a.py", "def run():\n    return 1\n")
        analysis = CodeAnalyzer().analyze_project(p, force_reanalyze=True)
        CodeKnowledgeGraph(p).build(analysis=analysis, builder="test")

        def gap(mod):
            arch = {"version": "1", "project": "p", "tech_roles": [
                {"id": "code", "name": "代码层", "target_modules": [mod]}]}
            r = self.call("coderef_arch_gap",
                          {"project_path": p, "target_arch": arch})
            th = r.get("target_health") or {}
            return float(th.get("missing_ratio") or 0), bool(th.get("stale"))

        old_ratio, old_stale = gap("old_dir")
        new_ratio, new_stale = gap("new_dir")
        self.assertGreater(old_ratio, new_ratio,
                           f"重定标后 missing_ratio 应下降: {old_ratio}->{new_ratio}")
        self.assertTrue(old_stale, "旧定标应标 stale")
        self.assertFalse(new_stale, "新定标不应 stale")


class NoLlmRecoverTest(_Base):

    def test_recover_without_llm_still_returns_memory(self):
        p = os.path.join(self.td, "rec")
        self.write(p, "core/app.py", "def run():\n    return 1\n")
        with mock.patch("core.dream._build_llm", return_value=None):
            r = self.call("coderef_operation_memory",
                          {"project_path": p, "action": "recover"})
        self.assertEqual(r.get("status"), "ok", f"无 LLM 时 recover 应仍可用: {r}")
        self.assertTrue(r.get("procedure_card") or r.get("env_facts")
                        or r.get("notes"),
                        f"recover 应返回环境/规程记忆: {list(r.keys())}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
