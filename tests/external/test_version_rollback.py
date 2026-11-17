# -*- coding: utf-8 -*-
"""版本升级 / 回退时「旧 key 产物」的读取边界回归。

背景（5.21.18 路径归一）：产物文件名里的项目哈希从「裸 abspath 的 md5」
改为「normcase(abspath) 的 md5」。升级后旧哈希命名的产物不会被自动读取，
但也绝不能被误读成当前项目结论、更不能被删除。

覆盖：
1. 旧 key 产物（.coderef/audit_findings_<旧hash>.json、cache/kg/<旧hash>.db、
   记忆状态 <旧hash>.json、白名单 wl_<旧hash>.json）→ 按不存在处理；
2. coderef_version 的 upgrade_note 必须含 must_rebuild 且列出白名单/图谱/记忆；
3. 反向：当前 key 的正确产物必须被正常读取；
4. 旧产物不得被删除（只读不删，用户数据保护）。

约束：全部在临时目录内；记忆状态目录与白名单目录重定向到临时目录，
不写 coderef-src。

运行：
    cd coderef-src
    python -m unittest discover -s "..\\独立测试\\tests" -p "test_version_rollback.py" -v
"""
import hashlib
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import unittest

PROJECT_ROOT = os.environ.get("CODEREF_SRC",
                              os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from core import memory_layer as ml  # noqa: E402
from core import pr_whitelist  # noqa: E402
from core.arch_audit import locate_kg_db  # noqa: E402
from core.artifact_freshness import report as freshness_report  # noqa: E402
from core.interpretation_platform import _load_audit_findings  # noqa: E402
from core.mcp_server import PKG_VERSION, Server  # noqa: E402
from core.memory_layer import MemoryLayer  # noqa: E402
from core.path_key import project_key  # noqa: E402
from core.pipeline_runner import _load_findings_json  # noqa: E402
from core.pr_models import PipeResult  # noqa: E402
from core.pr_whitelist import whitelist_list  # noqa: E402


def _hash(path: str) -> str:
    return hashlib.md5(project_key(path).encode()).hexdigest()[:12]


def _legacy_hash(path: str) -> str:
    """5.21.18 之前的算法：裸 abspath（无 normcase）。"""
    return hashlib.md5(os.path.abspath(path).encode()).hexdigest()[:12]


def _audit_payload(total_files=99):
    return {"version": 1, "total_files": total_files,
            "total_lines": 100, "scan_ts": "2026-01-01 00:00:00",
            "kg_built_at": "", "scope_text": "",
            "findings": [{"id": "legacy-1", "tool": "x", "category": "c",
                          "severity": "high", "tier": "high",
                          "file_path": "a.py", "line": 1, "title": "旧结论",
                          "detail": "", "suggestion": ""}]}


class _Base(unittest.TestCase):

    def setUp(self):
        self.td = tempfile.mkdtemp(prefix="coderef_ver_")
        self.p = os.path.join(self.td, "proj")
        os.makedirs(os.path.join(self.p, "pkg"), exist_ok=True)
        self._write(os.path.join(self.p, "main.py"),
                    "from pkg.util import add\n\n\ndef run():\n    return add(1, 2)\n")
        self._write(os.path.join(self.p, "pkg", "__init__.py"), "")
        self._write(os.path.join(self.p, "pkg", "util.py"),
                    "def add(a, b):\n    return a + b\n")

        self.cur = _hash(self.p)
        self.old = _legacy_hash(self.p)
        if self.cur == self.old:
            shutil.rmtree(self.td, ignore_errors=True)
            self.skipTest("当前盘符无法构造「旧哈希 != 新哈希」的升级场景")

        # 产物目录重定向，避免写 coderef-src
        self._state_dir = os.path.join(self.td, "_mem_state")
        self._wl_dir = os.path.join(self.td, "_wl")
        os.makedirs(self._state_dir, exist_ok=True)
        os.makedirs(self._wl_dir, exist_ok=True)
        self._orig_state = ml._MEMORY_STATE_DIR
        self._orig_report = ml._REPORT_DIR
        ml._MEMORY_STATE_DIR = self._state_dir
        ml._REPORT_DIR = os.path.join(self.td, "_mem_report")
        os.makedirs(ml._REPORT_DIR, exist_ok=True)
        self._orig_cdir = pr_whitelist._cdir
        pr_whitelist._cdir = lambda: self._wl_dir

    def tearDown(self):
        pr_whitelist._cdir = self._orig_cdir
        ml._MEMORY_STATE_DIR = self._orig_state
        ml._REPORT_DIR = self._orig_report
        shutil.rmtree(self.td, ignore_errors=True)

    @staticmethod
    def _write(fp, text):
        os.makedirs(os.path.dirname(fp), exist_ok=True)
        with open(fp, "w", encoding="utf-8") as f:
            f.write(text)
        return fp

    def _seed_legacy(self):
        """构造旧 key 命名的四类产物，返回 {kind: path}。"""
        files = {
            "findings": self._write(
                os.path.join(self.p, ".coderef",
                             f"audit_findings_{self.old}.json"),
                json.dumps(_audit_payload(), ensure_ascii=False)),
            "state": self._write(
                os.path.join(self._state_dir, f"{self.old}.json"),
                json.dumps({"project_path": self.p, "total_files": 99,
                            "last_sync": "2020-01-01T00:00:00"})),
            "whitelist": self._write(
                os.path.join(self._wl_dir, f"wl_{self.old}.json"),
                json.dumps([{"dir": "legacy_skip"}])),
        }
        kg_dir = os.path.join(self.p, "cache", "kg")
        os.makedirs(kg_dir, exist_ok=True)
        kg_path = os.path.join(kg_dir, f"{self.old}.db")
        con = sqlite3.connect(kg_path)
        try:
            con.execute("CREATE TABLE meta (key TEXT, value TEXT)")
            con.execute("INSERT INTO meta VALUES ('built_at', '1600000000')")
            con.commit()
        finally:
            con.close()
        files["kg"] = kg_path
        return files

    def _seed_current(self):
        """构造当前 key 的正确产物，返回 {kind: path}。"""
        files = {
            "findings": self._write(
                os.path.join(self.p, ".coderef",
                             f"audit_findings_{self.cur}.json"),
                json.dumps(_audit_payload(total_files=3), ensure_ascii=False)),
        }
        kg_dir = os.path.join(self.p, "cache", "kg")
        os.makedirs(kg_dir, exist_ok=True)
        kg_path = os.path.join(kg_dir, f"{self.cur}.db")
        con = sqlite3.connect(kg_path)
        try:
            con.execute("CREATE TABLE meta (key TEXT, value TEXT)")
            con.execute("INSERT INTO meta VALUES ('built_at', ?)",
                        (str(int(__import__("time").time())),))
            con.commit()
        finally:
            con.close()
        files["kg"] = kg_path
        return files


class LegacyArtifactNotReadTest(_Base):
    """1) 旧 key 产物不得被当作当前项目产物。"""

    def test_legacy_findings_not_credited(self):
        self._seed_legacy()
        item = next(a for a in freshness_report(self.p)["artifacts"]
                    if a["key"] == "audit_findings")
        self.assertFalse(item["exists"],
                         f"旧哈希 findings 不得计入本项目新鲜度: {item}")
        self.assertIsNone(_load_audit_findings(self.p),
                          "旧哈希 findings 不得被 interpret 读取")

        r = PipeResult(project_path=self.p)
        self.assertFalse(_load_findings_json(self.p, r),
                         "旧哈希 findings 不得被重渲染恢复")
        self.assertEqual(r.total_files, 0)

    def test_legacy_kg_not_located(self):
        self._seed_legacy()
        self.assertIsNone(locate_kg_db(self.p),
                          "旧哈希图谱不得被当成本项目图谱")
        kg_item = next(a for a in freshness_report(self.p)["artifacts"]
                       if a["key"] == "kg_db")
        self.assertFalse(kg_item["exists"],
                         f"旧哈希图谱不得计入新鲜度: {kg_item}")

    def test_legacy_memory_and_whitelist_not_read(self):
        self._seed_legacy()
        self.assertEqual(MemoryLayer()._load_state(self.p), {},
                         "旧哈希记忆状态不得被读取")
        self.assertEqual(whitelist_list(self.p), [],
                         "旧哈希白名单不得被读取")


class UpgradeNoteTest(_Base):
    """2) coderef_version 必须给出 must_rebuild 且覆盖白名单/图谱/记忆。"""

    def test_upgrade_note_declares_rebuild_targets(self):
        out = json.loads(Server()._run(
            "coderef_version", {"project_path": self.p, "background": False}))
        self.assertEqual(out.get("version"), PKG_VERSION)
        note = out.get("upgrade_note") or {}
        self.assertIn("must_rebuild", note,
                      f"upgrade_note 必须含 must_rebuild: {note}")
        joined = "".join(str(x) for x in note["must_rebuild"])
        for word in ("白名单", "图谱", "记忆"):
            self.assertIn(word, joined,
                          f"must_rebuild 未列出 {word}: {note['must_rebuild']}")
        self.assertTrue(str(note.get("tell_your_ai") or "").strip(),
                        f"upgrade_note 应可转述给编程 AI: {note}")


class CurrentKeyReadTest(_Base):
    """3) 反向：当前 key 的正确产物必须被正常读取。"""

    def test_current_artifacts_read_normally(self):
        cur = self._seed_current()
        item = next(a for a in freshness_report(self.p)["artifacts"]
                    if a["key"] == "audit_findings")
        self.assertTrue(item["exists"], f"当前哈希 findings 应被识别: {item}")

        data = _load_audit_findings(self.p)
        self.assertIsNotNone(data, "当前哈希 findings 应被 interpret 读取")
        self.assertEqual(data.get("total_files"), 3)

        r = PipeResult(project_path=self.p)
        self.assertTrue(_load_findings_json(self.p, r),
                        "当前哈希 findings 应被重渲染恢复")
        self.assertEqual(r.total_files, 3)

        self.assertEqual(os.path.abspath(locate_kg_db(self.p) or ""),
                         os.path.abspath(cur["kg"]),
                         "当前哈希图谱应被定位")


class LegacyArtifactPreservedTest(_Base):
    """4) 旧产物不得被删除（只读不删，用户数据保护）。"""

    def test_legacy_files_survive_reads_and_sync(self):
        legacy = self._seed_legacy()
        before = {}
        for kind, fp in legacy.items():
            with open(fp, "rb") as f:
                before[kind] = (os.stat(fp).st_mtime_ns, f.read())

        # 读路径 + 一次真实 sync（会写当前图谱/状态，但不得清理旧产物）
        freshness_report(self.p)
        locate_kg_db(self.p)
        _load_audit_findings(self.p)
        ml.memory_layer.sync(self.p, mode="full")

        for kind, fp in legacy.items():
            # U-92（v5.21.22 起）：旧 key 图谱会被移入同目录 legacy/（只移动不删除）；
            # findings/state/whitelist 等其他旧产物仍原位保留。
            moved = os.path.join(os.path.dirname(fp), "legacy",
                                 os.path.basename(fp))
            target = moved if os.path.isfile(moved) else fp
            self.assertTrue(os.path.isfile(target),
                            f"旧 {kind} 产物被删除: {fp}（legacy/ 也无）")
            with open(target, "rb") as f:
                now = (os.stat(target).st_mtime_ns, f.read())
            self.assertEqual(now, before[kind],
                             f"旧 {kind} 产物被改写/截断: {target}")

        # sync 只应新增当前 key 的图谱，不应动旧 key
        self.assertTrue(os.path.isfile(
            os.path.join(self.p, "cache", "kg", f"{self.cur}.db")),
            "sync 应产出当前 key 的图谱")
        old_db = os.path.join(self.p, "cache", "kg", f"{self.old}.db")
        old_legacy = os.path.join(self.p, "cache", "kg", "legacy",
                                  f"{self.old}.db")
        self.assertTrue(os.path.isfile(old_db) or os.path.isfile(old_legacy),
                        "当前 key 建图后旧图谱仍应保留（原位或 legacy/）")


if __name__ == "__main__":
    unittest.main(verbosity=2)
