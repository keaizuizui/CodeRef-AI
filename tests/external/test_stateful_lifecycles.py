# -*- coding: utf-8 -*-
"""状态机 / 生命周期回归（默认快跑）：多步功能不只看单点契约。

覆盖：
- scope add→list→remove→verify（含 remove 缺 pattern 的确定性拒绝）
- target_arch set→get→非法 schema 拒绝且不覆盖旧文件
- dispatcher plan→execute→status→report 全链
- 产物归属口径（当前哈希 / 他项目哈希）
- 分析快照漂移（U-60 修复后的增量路径）
"""
import hashlib
import json
import os
import shutil
import sys
import tempfile
import unittest

PROJECT_ROOT = os.environ.get("CODEREF_SRC",
                              os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from core import scope  # noqa: E402
from core import dispatcher as dsp  # noqa: E402
from core.mcp_server import Server  # noqa: E402
from core.code_analyzer import CodeAnalyzer  # noqa: E402
from core.code_knowledge_graph import CodeKnowledgeGraph  # noqa: E402
from core.artifact_freshness import report as freshness_report  # noqa: E402
from core.interpretation_platform import _load_audit_findings  # noqa: E402

STRICT = os.environ.get("CODEREF_STRICT_KNOWN") == "1"


def known(bug: str, why: str):
    # 已知缺陷均已修复：默认即阻断（保留 bug 注释语义）
    return lambda fn: fn


def _audit_payload(scan_ts="2026-11-10 10:00:00"):
    return {"total_files": 3,
            "findings": [{"id": "f1", "tier": "high"}],
            "scan_ts": scan_ts}


class _Base(unittest.TestCase):

    def setUp(self):
        self.td = tempfile.mkdtemp(prefix="coderef_life_")
        self.server = Server()

    def tearDown(self):
        shutil.rmtree(self.td, ignore_errors=True)

    def project(self, name="p"):
        p = os.path.join(self.td, name)
        os.makedirs(os.path.join(p, "pkg"), exist_ok=True)
        with open(os.path.join(p, "main.py"), "w", encoding="utf-8") as f:
            f.write("from pkg.util import add\n\n\ndef run():\n    return add(1, 2)\n")
        with open(os.path.join(p, "pkg", "__init__.py"), "w", encoding="utf-8") as f:
            f.write("")
        with open(os.path.join(p, "pkg", "util.py"), "w", encoding="utf-8") as f:
            f.write("def add(a, b):\n    return a + b\n")
        return p

    def call(self, name, args):
        return json.loads(self.server._run(name, dict(args, background=False)))


class ScopeLifecycleTest(_Base):

    def test_scope_add_list_remove_verify(self):
        p = self.project()
        r = self.call("coderef_scope", {"project_path": p, "action": "add",
                                        "kind": "exclude_glob", "pattern": "gen/*"})
        self.assertEqual(r.get("status"), "ok")
        self.assertEqual(r.get("added"), 1)
        self.assertIn("verify", r)

        r = self.call("coderef_scope", {"project_path": p, "action": "list"})
        self.assertEqual(r.get("status"), "ok")
        self.assertEqual(r["counts"]["exclude_glob"], 1)

        r = self.call("coderef_scope", {"project_path": p, "action": "remove"})
        self.assertEqual(r.get("status"), "error")
        self.assertIn("pattern", r.get("error", ""))

        r = self.call("coderef_scope", {"project_path": p, "action": "remove",
                                        "pattern": "gen/*"})
        self.assertEqual(r.get("status"), "ok")
        self.assertEqual(r.get("removed"), 1)

        r = self.call("coderef_scope", {"project_path": p, "action": "verify"})
        self.assertEqual(r.get("status"), "ok")
        self.assertIn("kept_files", r)

    def test_verify_survives_dirty_scope(self):
        p = self.project()
        os.makedirs(os.path.dirname(scope.scope_path(p)), exist_ok=True)
        with open(scope.scope_path(p), "w", encoding="utf-8") as f:
            json.dump({"version": 1, "rules": [
                "bad", 123, None,
                {"kind": "exclude_glob", "pattern": "pkg/*"}]}, f)
        r = self.call("coderef_scope", {"project_path": p, "action": "verify"})
        self.assertEqual(r.get("status"), "ok", f"脏 scope 不得让 verify 崩: {r}")
        self.assertTrue(scope.is_excluded(p, "pkg/util.py"))


class TargetArchLifecycleTest(_Base):

    ARCH = {"version": "1", "project": "p", "tech_roles": [
        {"id": "code", "name": "代码层", "target_modules": ["pkg"]}]}

    def test_set_get_and_invalid_not_overwrite(self):
        p = self.project()
        r = self.call("coderef_target_arch_set",
                      {"project_path": p, "target_arch": self.ARCH})
        self.assertIn(r.get("status"), ("ok", "completed"),
                      f"合法目标架构应落盘: {r}")
        fp = os.path.join(p, ".coderef", "target_arch.json")
        self.assertTrue(os.path.isfile(fp))
        with open(fp, "rb") as f:
            before = f.read()

        r = self.call("coderef_target_arch_get", {"project_path": p})
        self.assertNotEqual(r.get("status"), "not_set")

        r = self.call("coderef_target_arch_set",
                      {"project_path": p, "target_arch": {"version": 1}})
        self.assertEqual(r.get("status"), "error", f"非法 schema 必须拒绝: {r}")
        self.assertTrue(r.get("errors"))
        with open(fp, "rb") as f:
            after = f.read()
        self.assertEqual(after, before,
                         "校验失败不得覆盖已存目标架构")


class DispatcherLifecycleTest(_Base):

    def test_plan_execute_status_report(self):
        p = self.project()
        card = dsp.plan("probe", p)
        self.assertEqual(card.get("status"), "ok")

        def runner(tool, args):
            return json.dumps({"status": "ok", "tool": tool,
                               "envelope": {"verdict": "ok",
                                            "usable_for_decisions": True}},
                              ensure_ascii=False)

        ex = dsp.execute(card, runner, p, task_id="life_1")
        self.assertEqual(ex.get("status"), "completed")
        self.assertTrue(os.path.isfile(ex["trace_path"]))

        st = dsp.status(p, "life_1")
        self.assertEqual(st.get("status"), "completed")
        self.assertEqual(len(st.get("steps") or []), len(card.get("steps") or []))

        rep = dsp.report(p, "life_1")
        self.assertEqual(rep.get("status"), "ok")
        env = rep.get("envelope") or {}
        for key in ("verdict", "usable_for_decisions", "blocked_by",
                    "owner_actions", "cannot_conclude", "guardrails",
                    "limitations"):
            self.assertIn(key, env, f"dispatch_report envelope 缺 {key}")
        self.assertEqual(len(rep.get("step_index") or []),
                         len(card.get("steps") or []))


class IncrementalGraphEquivalenceTest(_Base):
    """增量重建必须与全量重建逐行一致（节点/边集合严格相等）。"""

    @staticmethod
    def _sets(db_path):
        import sqlite3
        con = sqlite3.connect(db_path)
        try:
            nodes = {tuple(r) for r in con.execute(
                "SELECT id,type,name,file_path,start_line,end_line FROM nodes")}
            edges = {tuple(r) for r in con.execute(
                "SELECT source,target,type FROM edges")}
        finally:
            con.close()
        return nodes, edges

    @known("U-65", "增量重建只重建变更文件，未变依赖方指向它的 IMPORTS 边被悬空清理后未回补")
    def test_incremental_equals_full(self):
        p = self.project("kg")
        ca = CodeAnalyzer()
        a1 = ca.analyze_project(p, force_reanalyze=True)

        full1 = os.path.join(self.td, "kg_full1.db")
        inc_db = os.path.join(self.td, "kg_inc.db")
        CodeKnowledgeGraph(p, db_path=full1).build(analysis=a1)
        CodeKnowledgeGraph(p, db_path=inc_db).build(analysis=a1)

        changed = os.path.join(p, "pkg", "util.py")
        with open(changed, "w", encoding="utf-8") as f:
            f.write("def add(a, b):\n    return a + b\n\n"
                    "def mul(a, b):\n    return a * b\n")
        a2 = ca.analyze_project(p, force_reanalyze=True)

        full2 = os.path.join(self.td, "kg_full2.db")
        CodeKnowledgeGraph(p, db_path=full2).build(analysis=a2)
        CodeKnowledgeGraph(p, db_path=inc_db).build(
            analysis=a2, incremental={"files": [changed]})

        nf, ef = self._sets(full2)
        ni, ei = self._sets(inc_db)
        self.assertEqual(nf, ni, "增量节点集合与全量不一致")
        self.assertEqual(ef, ei, "增量边集合与全量不一致")


class ArtifactOwnershipMatrixTest(_Base):
    """产物归属矩阵：当前哈希 / 他项目哈希的 freshness 与 interpret 口径。"""

    def _write(self, p, rel, payload):
        fp = os.path.join(p, rel)
        os.makedirs(os.path.dirname(fp), exist_ok=True)
        with open(fp, "w", encoding="utf-8") as f:
            json.dump(payload, f)
        return fp

    def test_current_hash_credited_consistently(self):
        p = self.project()
        # v5.21.18：产物文件名 hash 统一走 core.path_key.project_key（normcase）
        from core.path_key import project_key
        phash = hashlib.md5(project_key(p).encode()).hexdigest()[:12]
        self._write(p, ".coderef/audit_findings_%s.json" % phash,
                    _audit_payload())
        item = next(x for x in freshness_report(p)["artifacts"]
                    if x["key"] == "audit_findings")
        self.assertTrue(item["exists"])
        self.assertIsNotNone(_load_audit_findings(p))

    def test_other_hash_not_credited(self):
        p = self.project()
        self._write(p, ".coderef/audit_findings_deadbeef0000.json",
                    _audit_payload())
        item = next(x for x in freshness_report(p)["artifacts"]
                    if x["key"] == "audit_findings")
        self.assertFalse(item["exists"],
                         "其他项目哈希产物不得计入本项目新鲜度")
        self.assertIsNone(_load_audit_findings(p))

    def test_case_variant_path_shares_artifact(self):
        """U-71 回归：同一项目的大小写路径变体应归属同一份审计产物。"""
        p = self.project()
        variant = p[0].lower() + p[1:]
        if variant == p:
            self.skipTest("当前盘符无法构造大小写变体")
        from core.path_key import project_key
        phash = hashlib.md5(project_key(p).encode()).hexdigest()[:12]
        self._write(p, ".coderef/audit_findings_%s.json" % phash,
                    _audit_payload())
        item = next(x for x in freshness_report(variant)["artifacts"]
                    if x["key"] == "audit_findings")
        self.assertTrue(item["exists"], "大小写变体应读取到同一产物")
        self.assertIsNotNone(_load_audit_findings(variant))


class SnapshotDriftTest(_Base):

    def test_same_size_mtime_spoof_detected(self):
        """U-60 修复边界：同尺寸改写 + 恢复 mtime，增量漂移必须检出。"""
        p = self.project("drift")
        ca = CodeAnalyzer()
        snap_path = ca._cache_snapshot_path(p)
        cache_path = ca._cache_path(p)
        os.makedirs(os.path.dirname(snap_path), exist_ok=True)
        with open(snap_path, "w", encoding="utf-8") as f:
            json.dump(ca._compute_file_snapshot(p), f)
        with open(cache_path, "w", encoding="utf-8") as f:
            json.dump({}, f)

        fp = os.path.join(p, "pkg", "util.py")
        st = os.stat(fp)
        with open(fp, "w", encoding="utf-8") as f:
            f.write("def add(a, b):\n    return a - b\n")  # 与原文同尺寸
        os.utime(fp, (st.st_atime, st.st_mtime))

        self.assertIn(os.path.abspath(fp), ca._snapshot_drift(p, [fp]),
                      "增量漂移必须识别同尺寸内容变化")
        self.assertFalse(ca._is_cache_valid(p),
                         "缓存同样必须失效")


if __name__ == "__main__":
    unittest.main(verbosity=2)
