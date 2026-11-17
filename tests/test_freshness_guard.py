# -*- coding: utf-8 -*-
"""5.19.1 新鲜度硬戳与图谱文件级增量的单元测试。

覆盖：
- freshness_block：代码比产物新时 code_newer=true，缺产物时给出 stale_reason
- query_staleness：图谱缺失/构建时间过旧时 stale=true；阈值配置生效
- guard_config：warn/block 两档与非法值回退
- 图谱文件级增量：修改/删除/重命名后，增量结果与全量重建逐行一致

运行方式：
    python -m unittest tests.test_freshness_guard
"""
import json
import os
import sqlite3
import sys
import tempfile
import time
import unittest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from core import freshness_guard as fg
from core.code_analyzer import CodeAnalyzer
from core.ast_parser import AstParser
from core.code_knowledge_graph import CodeKnowledgeGraph


def _write(project, name, content):
    path = os.path.join(project, name)
    os.makedirs(os.path.dirname(path), exist_ok=True) if os.path.dirname(path) else None
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    return path


def _rows(kg):
    nodes = [tuple(r) for r in kg._conn.execute(
        "SELECT id,type,name,start_line,end_line FROM nodes ORDER BY id")]
    edges = [tuple(r) for r in kg._conn.execute(
        "SELECT source,target,type FROM edges ORDER BY source,target,type")]
    return nodes, edges


def _full_build(project):
    analyzer = CodeAnalyzer()
    analysis = analyzer.analyze_project(project, force_reanalyze=True)
    parser = AstParser()
    # Go 等非 Python 文件不做 AST 解析（AstParser 会返回 None），由 _build_from_go 覆盖
    asts = {fp: parser.parse(fp) for fp in analyzer.scan_directory(project)
            if fp.lower().endswith(".py")}
    kg = CodeKnowledgeGraph(project)
    kg.build(analysis=analysis, ast_results=asts)
    return kg, analysis, asts


class FreshnessBlockTest(unittest.TestCase):
    def test_code_newer_marks_stale(self):
        with tempfile.TemporaryDirectory() as project:
            code = _write(project, "demo.py", "def f():\n    return 1\n")
            artifact = _write(project, ".coderef/audit_findings_x.json", "{}")
            old = time.time() - 3600
            os.utime(artifact, (old, old))
            os.utime(code, None)  # 代码比产物新
            block = fg.freshness_block(project, ["audit_findings"])
            self.assertTrue(block["code_newer"])
            self.assertIn("代码比产物新", block["stale_reason"])
            self.assertTrue(block["refresh_action"])

    def test_missing_artifact_reason(self):
        with tempfile.TemporaryDirectory() as project:
            _write(project, "demo.py", "x = 1\n")
            block = fg.freshness_block(project, ["target_arch"])
            self.assertIn("缺少产物", block["stale_reason"])
            self.assertEqual(len(block["artifacts_used"]), 1)
            self.assertFalse(block["artifacts_used"][0]["exists"])


class QueryStalenessContradictionTest(unittest.TestCase):
    def test_cached_missing_does_not_override_existing_kg(self):
        """D-1：stale 缓存说缺失时，同响应 freshness 已见图谱则必须重算。"""
        with tempfile.TemporaryDirectory() as project:
            _write(project, "demo.py", "def f():\n    return 1\n")
            kg = CodeKnowledgeGraph(project)
            kg.build()
            kg.close()
            key = fg._norm(project)
            fake = {
                "stale": True, "stale_reason": "知识图谱不存在",
                "kg_built_at": "", "latest_code_mtime": "",
                "changed_files_since_kg": 0, "stale_minutes": 30,
                "stale_file_threshold": 3, "mode": "warn",
            }
            with fg._cache_lock:
                fg._stale_cache[key] = (time.time(), dict(fake))
                fg._report_cache.pop(key, None)
            raw = fg.annotate_query_result(
                json.dumps({"query_type": "stats"}), project)
            data = json.loads(raw)
            self.assertTrue(
                data["freshness"]["artifacts_used"][0]["exists"])
            self.assertNotEqual(
                data["kg_freshness"]["stale_reason"], "知识图谱不存在")
            self.assertTrue(data["kg_freshness"]["kg_built_at"])

    def test_cached_missing_report_does_not_hide_existing_kg(self):
        """artifact_report 缓存说图谱缺失时，实时定位必须纠正 freshness 与 stale。"""
        with tempfile.TemporaryDirectory() as project:
            _write(project, "demo.py", "def f():\n    return 1\n")
            kg = CodeKnowledgeGraph(project)
            kg.build()
            kg.close()
            key = fg._norm(project)
            rep = fg.artifact_report(project)
            poisoned = dict(rep)
            poisoned["artifacts"] = [
                dict(i, exists=False, path="", modified_at="")
                if i.get("key") == "kg_db" else i
                for i in rep.get("artifacts") or []
            ]
            with fg._cache_lock:
                fg._report_cache[key] = (time.time(), poisoned)
                fg._stale_cache.pop(key, None)
            block = fg.freshness_block(project, ["kg_db"])
            used = block["artifacts_used"][0]
            self.assertTrue(used["exists"])
            self.assertTrue(used["path"].endswith(".db"))
            self.assertNotIn("缺少产物", block["stale_reason"])
            info = fg.query_staleness(project)
            self.assertNotEqual(info["stale_reason"], "知识图谱不存在")


class QueryStalenessTest(unittest.TestCase):
    def test_missing_kg_is_stale(self):
        with tempfile.TemporaryDirectory() as project:
            _write(project, "demo.py", "x = 1\n")
            info = fg.query_staleness(project)
            self.assertTrue(info["stale"])
            self.assertIn("不存在", info["stale_reason"])

    def test_old_built_at_is_stale(self):
        with tempfile.TemporaryDirectory() as project:
            _write(project, "demo.py", "x = 1\n")
            kg = CodeKnowledgeGraph(project)
            kg.build()
            # 把构建时间改成 2 小时前，并放宽阈值到 1 分钟
            conn = sqlite3.connect(kg.db_path)
            conn.execute("UPDATE meta SET value=? WHERE key='built_at'",
                         (str(time.time() - 7200),))
            conn.commit()
            conn.close()
            kg.close()
            _write(project, ".coderef/guard_config.json",
                   json.dumps({"query": {"stale_minutes": 1,
                                         "stale_file_threshold": 99}}))
            info = fg.query_staleness(project)
            self.assertTrue(info["stale"])
            self.assertIn("分钟", info["stale_reason"])

    def test_config_block_mode(self):
        with tempfile.TemporaryDirectory() as project:
            _write(project, ".coderef/guard_config.json",
                   json.dumps({"query": {"mode": "block", "stale_minutes": 5}}))
            cfg = fg.guard_config(project)
            self.assertEqual(cfg["query"]["mode"], "block")
            self.assertEqual(cfg["query"]["stale_minutes"], 5)

    def test_config_invalid_mode_falls_back(self):
        with tempfile.TemporaryDirectory() as project:
            _write(project, ".coderef/guard_config.json",
                   json.dumps({"query": {"mode": "boom", "stale_minutes": "x"}}))
            cfg = fg.guard_config(project)
            self.assertEqual(cfg["query"]["mode"], "warn")
            self.assertEqual(cfg["query"]["stale_minutes"], 30)

    def test_invalidate_refreshes_artifact_report(self):
        with tempfile.TemporaryDirectory() as project:
            _write(project, "demo.py", "x = 1\n")
            before = fg.freshness_block(project, ["audit_findings"])
            self.assertFalse(before["artifacts_used"][0]["exists"])
            _write(project, ".coderef/audit_findings_x.json", "{}")
            # 60 秒缓存内仍返回旧盘点（模拟 audit 前的 version 调用）
            cached = fg.freshness_block(project, ["audit_findings"])
            self.assertFalse(cached["artifacts_used"][0]["exists"])
            fg.invalidate(project)
            after = fg.freshness_block(project, ["audit_findings"])
            self.assertTrue(after["artifacts_used"][0]["exists"])


class IncrementalGraphTest(unittest.TestCase):
    """增量结果必须与全量重建逐行一致（含修改/删除/重命名）。"""

    def _assert_same(self, incremental_rows, full_rows):
        self.assertEqual(incremental_rows[0], full_rows[0], "节点集合不一致")
        self.assertEqual(incremental_rows[1], full_rows[1], "边集合不一致")

    def test_modify_file(self):
        with tempfile.TemporaryDirectory() as project:
            a = _write(project, "a.py", "def foo():\n    return 1\n")
            _write(project, "b.py",
                   "from a import foo\n\ndef bar():\n    return foo()\n")
            kg, analysis, asts = _full_build(project)
            _write(project, "a.py",
                   "def foo():\n    return 2\n\ndef baz():\n    return foo()\n")
            analysis2 = CodeAnalyzer().analyze_project(project)
            asts2 = {fp: AstParser().parse(fp)
                     for fp in CodeAnalyzer().scan_directory(project)}
            kg.build(analysis=analysis2, ast_results=asts2,
                     incremental={"files": [a,
                                            os.path.join(project, "b.py")]})
            inc_rows = _rows(kg)
            kg.build(analysis=analysis2, ast_results=asts2)
            full_rows = _rows(kg)
            self._assert_same(inc_rows, full_rows)
            kg.close()

    def test_delete_file(self):
        with tempfile.TemporaryDirectory() as project:
            a = _write(project, "a.py", "def foo():\n    return 1\n")
            b = _write(project, "b.py",
                       "from a import foo\n\ndef bar():\n    return foo()\n")
            kg, _, _ = _full_build(project)
            os.remove(a)
            analysis2 = CodeAnalyzer().analyze_project(project)
            asts2 = {fp: AstParser().parse(fp)
                     for fp in CodeAnalyzer().scan_directory(project)}
            kg.build(analysis=analysis2, ast_results=asts2,
                     incremental={"files": [a, b]})
            inc_rows = _rows(kg)
            kg.build(analysis=analysis2, ast_results=asts2)
            full_rows = _rows(kg)
            self._assert_same(inc_rows, full_rows)
            kg.close()

    def test_rename_file(self):
        with tempfile.TemporaryDirectory() as project:
            a = _write(project, "a.py", "def foo():\n    return 1\n")
            b = _write(project, "b.py",
                       "from a import foo\n\ndef bar():\n    return foo()\n")
            kg, _, _ = _full_build(project)
            c = os.path.join(project, "c.py")
            os.rename(a, c)
            analysis2 = CodeAnalyzer().analyze_project(project)
            asts2 = {fp: AstParser().parse(fp)
                     for fp in CodeAnalyzer().scan_directory(project)}
            kg.build(analysis=analysis2, ast_results=asts2,
                     incremental={"files": [a, c, b]})
            inc_rows = _rows(kg)
            kg.build(analysis=analysis2, ast_results=asts2)
            full_rows = _rows(kg)
            self._assert_same(inc_rows, full_rows)
            kg.close()

    def test_go_file_incremental_consistency(self):
        """Go 文件变更时增量需整体重建 go_func，跨文件 CALLS 与全量一致。"""
        with tempfile.TemporaryDirectory() as project:
            a = _write(project, "a.go",
                       "package main\n\nfunc Foo() int {\n\treturn 1\n}\n")
            _write(project, "b.go",
                   "package main\n\nfunc Bar() int {\n\treturn Foo()\n}\n")
            kg, _, _ = _full_build(project)
            _write(project, "a.go",
                   "package main\n\nfunc Foo() int {\n\treturn 2\n}\n")
            analysis2 = CodeAnalyzer().analyze_project(project)
            asts2 = {fp: AstParser().parse(fp)
                     for fp in CodeAnalyzer().scan_directory(project)
                     if fp.lower().endswith(".py")}
            kg.build(analysis=analysis2, ast_results=asts2,
                     incremental={"files": [a]})
            inc_rows = _rows(kg)
            kg.build(analysis=analysis2, ast_results=asts2)
            full_rows = _rows(kg)
            self._assert_same(inc_rows, full_rows)
            kg.close()


if __name__ == "__main__":
    unittest.main()
