# -*- coding: utf-8 -*-
"""多项目隔离回归（S10）：同时使用两个项目时，图谱/状态/缓存/审计产物互不污染。

判据：
1. A 图谱不含 beta_ 符号，B 图谱不含 alpha_（直接读 sqlite nodes）；
2. A/B 的 project_key 不同，图谱 db 文件不同且各自落在项目内；
3. 交替调用（A→B→A）后 A 的 stats 数值不变；
4. A/B 的审计 findings 文件名 hash 不同，且互相读不到；
5. 全局缓存 data/memory_state、data/analysis_cache 中 A/B 条目互不覆盖；
6. 反向：同一项目盘符大小写变体必须落到同一 db（U-71 回归）。
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

from core.memory_layer import MemoryLayer, _MEMORY_STATE_DIR  # noqa: E402
from core.code_knowledge_graph import CodeKnowledgeGraph  # noqa: E402
from core.code_analyzer import CodeAnalyzer  # noqa: E402
from core.path_key import project_key  # noqa: E402
from core.pr_whitelist import project_findings_json_path  # noqa: E402
from core.scorecard import find_findings_json  # noqa: E402
from core.pipeline_runner import PipeResult, _dump_findings_json  # noqa: E402
from core.pr_models import Finding, Tier  # noqa: E402

_A_TEMPLATE = '''"""阿尔法模块 {i}"""


def alpha_func_{i}(x):
    """阿尔法函数 {i}"""
    return x + {i}


class AlphaClass_{i}:
    """阿尔法类 {i}"""

    def alpha_method_{i}(self):
        return alpha_func_{i}({i})
'''

_B_TEMPLATE = '''"""贝塔模块 {i}"""


def beta_func_{i}(x):
    """贝塔函数 {i}"""
    return x - {i}


class BetaClass_{i}:
    """贝塔类 {i}"""

    def beta_method_{i}(self):
        return beta_func_{i}({i})
'''


def _write_project(root, template, count=3):
    os.makedirs(root, exist_ok=True)
    for i in range(count):
        name = "mod%d" % i
        with open(os.path.join(root, name + ".py"), "w", encoding="utf-8") as f:
            f.write(template.format(i=i))
    return root


def _disable_ollama():
    """本机 Ollama 的 embed 端点 404（每次 3×2s 重试），指向不可达回环让
    is_available 快速为假，走既有「关键词检索」降级路径；不影响隔离语义。"""
    old = os.environ.get("OLLAMA_BASE_URL")
    os.environ["OLLAMA_BASE_URL"] = "http://127.0.0.1:1"
    return old


def _restore_ollama(old):
    if old is None:
        os.environ.pop("OLLAMA_BASE_URL", None)
    else:
        os.environ["OLLAMA_BASE_URL"] = old


def _nodes(project_path):
    """直接读 sqlite nodes 表（id/type/name/file_path/props 全文）。"""
    kg = CodeKnowledgeGraph(project_path)
    try:
        kg._connect()
        rows = kg._conn.execute(
            "SELECT id, type, name, file_path, props FROM nodes").fetchall()
        return [tuple(r) for r in rows]
    finally:
        kg.close()


def _leaks(rows, marker):
    """返回包含 marker 的节点行（跨项目符号泄漏证据）。"""
    return [r for r in rows if marker in (r[0] + r[2] + r[3] + (r[4] or ""))]


def _under(path, root):
    p = os.path.normcase(os.path.abspath(str(path)))
    r = os.path.normcase(os.path.abspath(str(root)))
    return p == r or p.startswith(r + os.sep)


class MultiProjectIsolationTest(unittest.TestCase):
    """两个项目共用同一 CodeRef 安装目录时的数据隔离。"""

    @classmethod
    def setUpClass(cls):
        cls._old_ollama = _disable_ollama()
        cls.td = tempfile.mkdtemp(prefix="coderef_iso_")
        cls.A = _write_project(os.path.join(cls.td, "proj_alpha"), _A_TEMPLATE)
        cls.B = _write_project(os.path.join(cls.td, "proj_beta"), _B_TEMPLATE)
        cls.ml = MemoryLayer()
        ra = cls.ml.sync(cls.A, mode="full")
        rb = cls.ml.sync(cls.B, mode="full")
        assert ra.get("status") == "ok", ra
        assert rb.get("status") == "ok", rb

    @classmethod
    def tearDownClass(cls):
        for p in (cls.A, cls.B):
            _cleanup_project_caches(p)
        shutil.rmtree(cls.td, ignore_errors=True)
        _restore_ollama(cls._old_ollama)

    # ── 1. 图谱符号不串项目 ──
    def test_a_graph_has_no_b_symbols(self):
        a_rows = _nodes(self.A)
        b_rows = _nodes(self.B)
        self.assertTrue(_leaks(a_rows, "alpha_"),
                        "A 图谱应包含 alpha_ 符号（前提校验）: %r" % (a_rows[:3],))
        leak_in_a = _leaks(a_rows, "beta_")
        self.assertEqual(leak_in_a, [],
                         "A 项目符号出现在 B 图谱? 反向核对：B 的 beta_ 泄漏进 A 图谱，"
                         "命中 %d 条: %r" % (len(leak_in_a), leak_in_a[:5]))
        leak_in_b = _leaks(b_rows, "alpha_")
        self.assertEqual(leak_in_b, [],
                         "B 项目图谱出现 A 项目符号 alpha_，命中 %d 条: %r"
                         % (len(leak_in_b), leak_in_b[:5]))

    # ── 2. project_key / db 文件隔离 ──
    def test_project_key_and_db_are_distinct(self):
        ka, kb = project_key(self.A), project_key(self.B)
        self.assertNotEqual(ka, kb, "A/B 的 project_key 不应相同: %r" % (ka,))
        db_a = CodeKnowledgeGraph(self.A).db_path
        db_b = CodeKnowledgeGraph(self.B).db_path
        self.assertNotEqual(os.path.normcase(db_a), os.path.normcase(db_b),
                            "A/B 落到同一图谱库（跨项目覆盖）: %s vs %s" % (db_a, db_b))
        self.assertTrue(os.path.isfile(db_a), "A 图谱库不存在: %s" % db_a)
        self.assertTrue(os.path.isfile(db_b), "B 图谱库不存在: %s" % db_b)
        self.assertTrue(_under(db_a, self.A), "A 图谱库应落在 A 项目内: %s" % db_a)
        self.assertTrue(_under(db_b, self.B), "B 图谱库应落在 B 项目内: %s" % db_b)

    # ── 3. 交替调用后 A 的 stats 不变 ──
    def test_interleaved_calls_keep_a_stats(self):
        before = self.ml.query(self.A, query_type="stats")
        self.assertEqual(before.get("status"), "ok", before)
        self.ml.sync(self.B, mode="full")   # A → B
        after = self.ml.query(self.A, query_type="stats")  # → A
        self.assertEqual(after.get("status"), "ok", after)
        for key in ("node_count", "edge_count"):
            self.assertEqual(
                int(before.get(key) or 0), int(after.get(key) or 0),
                "交替调用 B 后 A 的 %s 发生变化（图谱被跨项目污染）: %r -> %r"
                % (key, before.get(key), after.get(key)))
        self.ml.sync(self.A, mode="full")   # 再回到 A 重建
        again = self.ml.query(self.A, query_type="stats")
        self.assertEqual(int(again.get("node_count") or 0),
                         int(before.get("node_count") or 0),
                         "A 自身重建后节点数漂移: %r -> %r"
                         % (before.get("node_count"), again.get("node_count")))

    # ── 4. 审计 findings 文件名 hash 与可读性隔离 ──
    def test_audit_findings_are_isolated(self):
        pa = project_findings_json_path(self.A)
        pb = project_findings_json_path(self.B)
        self.assertNotEqual(os.path.basename(pa), os.path.basename(pb),
                            "A/B findings 文件名 hash 相同，会互相覆盖: %s"
                            % os.path.basename(pa))
        self.assertTrue(_under(pa, self.A), "A findings 应落在 A 项目内: %s" % pa)
        self.assertTrue(_under(pb, self.B), "B findings 应落在 B 项目内: %s" % pb)

        _dump_findings_json(self._result(self.A, "alpha"), "")
        self.assertTrue(os.path.isfile(pa), "A findings 未落盘: %s" % pa)
        self.assertIsNone(find_findings_json(self.B),
                          "只写过 A 的 findings，B 却读到了审计产物: %s"
                          % find_findings_json(self.B))
        data_a = json.load(open(pa, encoding="utf-8"))
        paths = [f.get("file_path", "") for f in data_a.get("findings") or []]
        self.assertTrue(paths, "A findings 内容为空，判据无法成立")
        bad = [p for p in paths if not _under(p, self.A)]
        self.assertEqual(bad, [], "B 项目的 findings 混入 A 产物: %r" % bad[:5])

        _dump_findings_json(self._result(self.B, "beta"), "")
        self.assertEqual(os.path.normcase(find_findings_json(self.A) or ""),
                         os.path.normcase(pa),
                         "A 读 findings 时定位错文件（可能读到 B 的）")
        self.assertEqual(os.path.normcase(find_findings_json(self.B) or ""),
                         os.path.normcase(pb),
                         "B 读 findings 时定位错文件（可能读到 A 的）")
        data_b = json.load(open(pb, encoding="utf-8"))
        bad_b = [f.get("file_path", "") for f in data_b.get("findings") or []
                 if not _under(f.get("file_path", ""), self.B)]
        self.assertEqual(bad_b, [], "A 项目的 findings 混入 B 产物: %r" % bad_b[:5])

    # ── 5. 全局缓存条目互不覆盖 ──
    def test_global_cache_entries_do_not_overwrite(self):
        state_a = self.ml._state_path(self.A)
        state_b = self.ml._state_path(self.B)
        self.assertNotEqual(state_a, state_b, "A/B 记忆状态落到同一文件: %s" % state_a)
        self.assertTrue(os.path.isfile(state_a), "A 记忆状态不存在: %s" % state_a)
        self.assertTrue(os.path.isfile(state_b), "B 记忆状态不存在: %s" % state_b)
        ja = json.load(open(state_a, encoding="utf-8"))
        jb = json.load(open(state_b, encoding="utf-8"))
        self.assertEqual(os.path.normcase(ja.get("project_path", "")),
                         os.path.normcase(os.path.abspath(self.A)),
                         "A 的状态文件内容指向了别的项目: %r" % ja.get("project_path"))
        self.assertEqual(os.path.normcase(jb.get("project_path", "")),
                         os.path.normcase(os.path.abspath(self.B)),
                         "B 的状态文件内容指向了别的项目: %r" % jb.get("project_path"))
        self.assertEqual(ja.get("hash"), self.ml._project_hash(self.A),
                         "A 状态 hash 与路径哈希不一致")
        snap_paths = list((ja.get("snapshot") or {}).keys())
        self.assertTrue(snap_paths, "A 状态缺少文件快照，无法验证归属")
        wrong = [p for p in snap_paths if not _under(p, self.A)]
        self.assertEqual(wrong, [], "A 状态快照混入其他项目文件: %r" % wrong[:5])

        # 语义知识库（memory_state 下的 .kb.db）按 hash 分文件
        kb_a, kb_b = self.ml._kb_path(self.A), self.ml._kb_path(self.B)
        self.assertNotEqual(kb_a, kb_b, "A/B 语义库落到同一文件: %s" % kb_a)
        self.assertTrue(os.path.isfile(kb_a), "A 语义库不存在: %s" % kb_a)
        self.assertTrue(os.path.isfile(kb_b), "B 语义库不存在: %s" % kb_b)

        # 结构分析缓存按归一化路径 md5 分文件
        an = CodeAnalyzer()
        cache_a, cache_b = an._cache_path(self.A), an._cache_path(self.B)
        self.assertNotEqual(os.path.normcase(cache_a), os.path.normcase(cache_b),
                            "A/B 分析缓存落到同一文件: %s" % cache_a)
        self.assertTrue(os.path.isfile(cache_a), "A 分析缓存不存在: %s" % cache_a)
        self.assertTrue(os.path.isfile(cache_b), "B 分析缓存不存在: %s" % cache_b)
        da = json.load(open(cache_a, encoding="utf-8"))
        self.assertEqual(os.path.normcase(da.get("project_path", "")),
                         os.path.normcase(os.path.abspath(self.A)),
                         "A 分析缓存内容指向了别的项目: %r" % da.get("project_path"))
        files = [f.get("file_path", "") for f in da.get("files") or []]
        wrong_files = [p for p in files if not _under(p, self.A)]
        self.assertEqual(wrong_files, [],
                         "A 分析缓存混入 B 项目文件: %r" % wrong_files[:5])

    # ── 6. 反向：大小写路径变体同一 db（U-71） ──
    def test_case_variant_paths_share_one_db(self):
        p = self.A
        variant = p[0].lower() + p[1:]
        if variant == p:
            self.skipTest("临时目录不在盘符路径上，无法构造大小写变体: %s" % p)
        self.assertEqual(project_key(p), project_key(variant),
                         "同一项目的大小写变体得到不同 project_key: %r vs %r"
                         % (project_key(p), project_key(variant)))
        self.assertEqual(
            os.path.normcase(CodeKnowledgeGraph(p).db_path),
            os.path.normcase(CodeKnowledgeGraph(variant).db_path),
            "大小写变体落到不同图谱库（U-71 回归）: %s vs %s"
            % (CodeKnowledgeGraph(p).db_path, CodeKnowledgeGraph(variant).db_path))
        self.assertEqual(self.ml._state_path(p), self.ml._state_path(variant),
                         "大小写变体落到不同记忆状态文件（U-71 回归）")

        self.ml.sync(variant, mode="full")
        kg_dir = os.path.join(p, "cache", "kg")
        dbs = [f for f in os.listdir(kg_dir) if f.endswith(".db")]
        self.assertEqual(len(dbs), 1,
                         "大小写变体各建一套图谱库（同一项目分裂）: %r" % dbs)
        rows = _nodes(variant)
        leak = _leaks(rows, "beta_")
        self.assertEqual(leak, [], "变体路径建图混入 B 项目符号: %r" % leak[:5])
        self.assertTrue(_leaks(rows, "alpha_"), "变体路径应仍指向 A 项目图谱")

    # ── 辅助 ──
    @staticmethod
    def _result(project, marker):
        r = PipeResult(project_path=project)
        r.scan_ts = "2026-01-01 00:00:00"
        r.total_files = 3
        r.total_lines = 30
        r.scope_text = marker
        r.findings = [Finding(
            id=marker + "-1", tool=marker + "_tool", category="isolation",
            severity="low", file_path=os.path.join(project, marker + "_hit.py"),
            line=1, title=marker + " finding", detail="", suggestion="",
            tier=Tier.LOW)]
        return r


def _cleanup_project_caches(project_path):
    """清理本测试在全局缓存目录留下的条目（不改产品逻辑，只回收用例产物）。"""
    try:
        key = project_key(project_path)
        h12 = hashlib.md5(key.encode("utf-8")).hexdigest()[:12]
        h32 = hashlib.md5(key.encode("utf-8")).hexdigest()
    except Exception:  # noqa: BLE001
        return
    try:
        names = os.listdir(_MEMORY_STATE_DIR)
    except OSError:
        names = []
    for fn in names:
        if fn.startswith(h12):
            try:
                os.remove(os.path.join(_MEMORY_STATE_DIR, fn))
            except OSError:
                pass
    try:
        cache_dir = CodeAnalyzer()._cache_dir
        names = os.listdir(cache_dir)
    except Exception:  # noqa: BLE001
        names, cache_dir = [], ""
    for fn in names:
        if fn.startswith(h32) or fn.startswith(
                hashlib.md5(os.path.abspath(str(project_path)).encode(
                    "utf-8")).hexdigest()):
            try:
                os.remove(os.path.join(cache_dir, fn))
            except OSError:
                pass


if __name__ == "__main__":
    unittest.main(verbosity=2)
