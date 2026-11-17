# -*- coding: utf-8 -*-
"""极限方案 T06-T15：输入边界、证据、编码、新鲜度、信封、调度链契约。

已知缺陷用 `known()` 标注：默认 skip（保持门禁绿），
`CODEREF_STRICT_KNOWN=1` 可强制运行复现 RED。
"""
import hashlib
import json
import os
import shutil
import sys
import tempfile
import time
import unittest
from datetime import datetime, timedelta
from unittest import mock

PROJECT_ROOT = os.environ.get("CODEREF_SRC",
                              os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from core import scope  # noqa: E402
from core import dream  # noqa: E402
from core import dispatcher as dsp  # noqa: E402
from core.ast_parser import AstParser  # noqa: E402
from core.code_analyzer import CodeAnalyzer  # noqa: E402
from core.artifact_freshness import _artifact_item, report as freshness_report  # noqa: E402
from core.interpretation_platform import (  # noqa: E402
    _load_audit_findings, _is_empty_shell_audit, _stale_audit_note,
)
from core.result_envelope import envelope, for_audit, KNOWN_LIMITATIONS  # noqa: E402
from core.change_guard import ChangeGuard  # noqa: E402
from core.tools_manifest import BUILTIN_TOOLS  # noqa: E402
from core.dispatch_intents import INTENTS  # noqa: E402

STRICT = os.environ.get("CODEREF_STRICT_KNOWN") == "1"


def known(bug: str, why: str):
    # 已知缺陷均已修复：默认即阻断（保留 bug 注释语义）
    return lambda fn: fn


class _FakeCfg:
    max_tokens = 4096
    provider = None
    model = "fake"


class FakeLLM:
    def __init__(self, payload="{}"):
        self.payload = payload
        self.config = _FakeCfg()

    def is_available(self):
        return True

    def chat_completion(self, *a, **k):
        return self.payload


class InputsAndContractsTest(unittest.TestCase):

    def setUp(self):
        self.td = tempfile.mkdtemp(prefix="coderef_t06_")

    def tearDown(self):
        shutil.rmtree(self.td, ignore_errors=True)

    def _p(self, name="p"):
        p = os.path.join(self.td, name)
        os.makedirs(p, exist_ok=True)
        with open(os.path.join(p, "app.py"), "w", encoding="utf-8") as f:
            f.write("def hello():\n    return 1\n")
        return p

    # ── T06 scope 脏配置 ────────────────────────────────────────
    def test_t06a_corrupt_scope_degrades_safely(self):
        p = self._p()
        os.makedirs(os.path.dirname(scope.scope_path(p)), exist_ok=True)
        with open(scope.scope_path(p), "w", encoding="utf-8") as f:
            f.write("{not-json")
        self.assertEqual(scope.list_scope(p)["rules"], [])
        self.assertFalse(scope.is_excluded(p, "app.py"))

    def test_t06b_rule_input_validation(self):
        p = self._p()
        with self.assertRaises(ValueError):
            scope.add_rule(p, "exclude_glob", "")
        with self.assertRaises(ValueError):
            scope.add_rule(p, "bogus", "x")
        first = scope.add_rule(p, "exclude_glob", "gen/*")
        second = scope.add_rule(p, "exclude_glob", "gen/*")
        self.assertEqual(first["added"], 1)
        self.assertTrue(second["duplicate"])
        self.assertEqual(second["added"], 0)

    @known("U-54", "scope.json 规则条目未做 schema 清洗，脏条目让 is_excluded 抛异常")
    def test_t06c_dirty_rule_entries_do_not_crash(self):
        p = self._p()
        os.makedirs(os.path.dirname(scope.scope_path(p)), exist_ok=True)
        with open(scope.scope_path(p), "w", encoding="utf-8") as f:
            json.dump({"version": 1, "rules": [
                "bad", 123, None, {"kind": "exclude_glob", "pattern": "gen/*"}]}, f)
        self.assertTrue(scope.is_excluded(p, "gen/a.py"),
                        "合法规则仍应生效")
        self.assertFalse(scope.is_excluded(p, "app.py"),
                         "脏条目应被清洗，不得让扫描入口抛异常")

    # ── T07 dispatcher ID 校验 ─────────────────────────────────
    def test_t07a_traversal_ids_rejected(self):
        for bad in ("..", "../../evil", "a/b", "a\\b", "", "A" * 65, "ａ"):
            with self.assertRaises(ValueError, msg=bad):
                dsp._safe_id(bad)
        self.assertIsNone(dsp.load_plan(self._p(), "../../evil"))
        tp = os.path.normpath(dsp._trace_path(self._p(), "ok_1"))
        self.assertIn(os.path.normpath(os.path.join(self._p(),
                                                    ".coderef", "dispatch")), tp)

    @known("U-55", "_safe_id 用 match 而非 fullmatch，尾随换行可绕过")
    def test_t07b_control_chars_rejected(self):
        for bad in ("abc\n", "abc\t", "abc\x00"):
            with self.assertRaises(ValueError, msg=repr(bad)):
                dsp._safe_id(bad)

    # ── T08 证据文法 ───────────────────────────────────────────
    def _ctx(self, p):
        return {"project_path": p, "audit_tools": set(), "seed_ids": set()}

    def test_t08a_check_ref_matrix(self):
        p = self._p()
        from core.dream import _check_ref
        cases = {
            "file:app.py": True,
            "file:../../etc/passwd": False,
            "file:C:/Windows/System32/drivers/etc/hosts": False,
            "file:": None,
            "git:../../evil": False,
            "git:abcd": False,
            "audit:coderef_audit": False,
            "seed:not-exist": False,
            "dispatch:../evil": False,
            "trace:ok_1": False,
            "bogus:x": None,
        }
        for ref, expected in cases.items():
            ok, detail = _check_ref(ref, self._ctx(p))
            if expected is True:
                self.assertTrue(ok, f"{ref}: {detail}")
            elif expected is False:
                self.assertIs(ok, False, f"{ref}: {detail}")
            else:
                self.assertIsNone(ok, f"{ref}: {detail}")

    @known("U-52", "false_memory_check 只拒绝 ok=False，无法核验的证据保留 verified")
    def test_t08b_unverifiable_evidence_downgraded(self):
        p = self._p()
        dd = os.path.join(p, ".coderef", "dream")
        os.makedirs(dd, exist_ok=True)
        with open(os.path.join(dd, "notes.json"), "w", encoding="utf-8") as f:
            json.dump({"version": 1, "notes": [{
                "id": "n-empty", "kind": "rule", "job": "pitfall",
                "claim_type": "fact", "text": "空证据样本",
                "evidence": ["file:"], "confidence": 0.9,
                "status": "verified", "created_at": "2026-11-09T19:20:00",
                "last_hit_at": ""}]}, f)
        r = dream.run(p, job="false_memory_check")
        self.assertEqual(r.get("status"), "ok")
        note = dream._load_notes(p)[0]
        self.assertNotEqual(note.get("status"), "verified",
                            "无法核验证据的笔记必须降级，不能继续当已验证规则")

    # ── T09 编码边界 ───────────────────────────────────────────
    def test_t09a_encodings_degrade_predictably(self):
        p = self._p("enc")
        cases = {
            "gbk.py": b"# \xc4\xe3\xba\xc3\ndef f():\n    return 1\n",
            "bin.dat": b"\x89PNG\r\n\x1a\n\x00\x01\xff\xfe",
            "zero.py": b"",
            "zw.py": "def a\u200b():\n    pass\n".encode("utf-8"),
            "emoji.py": "x = '😀'\ndef f():\n    return x\n".encode("utf-8"),
            "crlf.py": b"def f():\r\n    return 1\r\n",
        }
        for name, data in cases.items():
            with open(os.path.join(p, name), "wb") as f:
                f.write(data)
        parser = AstParser()
        self.assertEqual(len(parser.parse(os.path.join(p, "gbk.py")).functions), 1)
        self.assertIsNone(parser.parse(os.path.join(p, "bin.dat")))
        self.assertEqual(len(parser.parse(os.path.join(p, "zero.py")).functions), 0)
        self.assertIsNone(parser.parse(os.path.join(p, "zw.py")))
        self.assertEqual(len(parser.parse(os.path.join(p, "emoji.py")).functions), 1)
        self.assertEqual(len(parser.parse(os.path.join(p, "crlf.py")).functions), 1)

    @known("U-57", "UTF-8 BOM 未剥离：AST 降级为正则，首行 import 静默丢失")
    def test_t09b_bom_file_keeps_first_import(self):
        p = self._p("bom")
        src = ("import os\nfrom collections import defaultdict\n\n"
               "def f():\n    return 1\n")
        with open(os.path.join(p, "normal.py"), "w", encoding="utf-8") as f:
            f.write(src)
        with open(os.path.join(p, "bom.py"), "wb") as f:
            f.write(b"\xef\xbb\xbf" + src.encode("utf-8"))
        ca = CodeAnalyzer()
        normal = ca.parse_file(os.path.join(p, "normal.py"))
        bom = ca.parse_file(os.path.join(p, "bom.py"))
        self.assertEqual(sorted(normal.imports), ["collections", "os"])
        self.assertEqual(sorted(bom.imports), sorted(normal.imports),
                         "BOM 文件的首行 import 不应丢失")

    # ── T10 迁移/复制后的产物归属一致性 ─────────────────────────
    @staticmethod
    def _audit_payload(scan_ts="2026-11-09 10:00:00"):
        return {"total_files": 3, "findings": [{"id": "f1", "tier": "high"}],
                "scan_ts": scan_ts}

    def test_t10a_hashed_artifact_agrees(self):
        p = self._p("mig-ok")
        # v5.21.18：产物文件名 hash 统一走 core.path_key.project_key（normcase）
        from core.path_key import project_key
        phash = hashlib.md5(project_key(p).encode()).hexdigest()[:12]
        os.makedirs(os.path.join(p, ".coderef"), exist_ok=True)
        with open(os.path.join(p, ".coderef",
                               "audit_findings_%s.json" % phash), "w",
                  encoding="utf-8") as f:
            json.dump(self._audit_payload(), f)
        fr = freshness_report(p)
        item = next(x for x in fr["artifacts"] if x["key"] == "audit_findings")
        self.assertTrue(item["exists"])
        self.assertIsNotNone(_load_audit_findings(p))

    @known("U-53", "freshness 用 glob 判存在，interpret 用精确哈希判归属，同一响应自相矛盾")
    def test_t10b_stale_hash_not_credited(self):
        p = self._p("mig-bad")
        os.makedirs(os.path.join(p, ".coderef"), exist_ok=True)
        with open(os.path.join(p, ".coderef",
                               "audit_findings_deadbeef0000.json"), "w",
                  encoding="utf-8") as f:
            json.dump(self._audit_payload(), f)
        fr = freshness_report(p)
        item = next(x for x in fr["artifacts"] if x["key"] == "audit_findings")
        self.assertFalse(item["exists"] and _load_audit_findings(p) is None,
                         "旧哈希产物不得被新鲜度报告当作本项目产物")

    # ── T11 时间边界 ───────────────────────────────────────────
    def test_t11a_stale_note_48h_boundary(self):
        fmt = "%Y-%m-%d %H:%M:%S"
        fresh = (datetime.now() - timedelta(hours=47)).strftime(fmt)
        stale = (datetime.now() - timedelta(hours=49)).strftime(fmt)
        self.assertEqual(_stale_audit_note({"scan_ts": fresh}), "")
        self.assertTrue(_stale_audit_note({"scan_ts": stale}))

    def test_t11b_artifact_age_boundary(self):
        p = self._p("age")
        fp = os.path.join(p, ".coderef", "audit_findings_x.json")
        os.makedirs(os.path.dirname(fp), exist_ok=True)
        with open(fp, "w", encoding="utf-8") as f:
            f.write("{}")
        now = time.time()
        for hours, expect_stale in ((6.9 * 24, False), (7.1 * 24, True)):
            os.utime(fp, (now - hours * 3600, now - hours * 3600))
            item = _artifact_item("audit_findings", "审计",
                                  (".coderef/audit_findings_x.json",), 7, "", p, None)
            self.assertEqual(item["stale_by_age"], expect_stale,
                             f"{hours/24:.1f} 天边界判定错误")
        os.utime(fp, (now + 2 * 86400, now + 2 * 86400))  # 未来时间
        item = _artifact_item("audit_findings", "审计",
                              (".coderef/audit_findings_x.json",), 7, "", p, None)
        self.assertFalse(item["stale_by_age"], "未来 mtime 不应被判过期")

    # ── T12 产物篡改/空壳 ───────────────────────────────────────
    def test_t12_tampered_artifact_not_accepted(self):
        p = self._p("tamper")
        phash = hashlib.md5(p.encode()).hexdigest()[:12]
        dd = os.path.join(p, ".coderef")
        os.makedirs(dd, exist_ok=True)
        # 空壳（截断）审计冒充当前：interpret 必须拒绝
        with open(os.path.join(dd, "audit_findings_%s.json" % phash), "w",
                  encoding="utf-8") as f:
            json.dump({"total_files": 0, "findings": []}, f)
        self.assertTrue(_is_empty_shell_audit({"total_files": 0, "findings": []}))
        self.assertFalse(_is_empty_shell_audit(
            {"total_files": 3, "findings": [{"id": "f"}]}))
        self.assertIsNone(_load_audit_findings(p))
        # 旧 scan_ts 内容配新文件时间：必须给出"数据较旧"警示，不得静默当现状
        old = (datetime.now() - timedelta(days=60)).strftime("%Y-%m-%d %H:%M:%S")
        self.assertTrue(_stale_audit_note({"scan_ts": old}))

    # ── T13 信封不变量 ─────────────────────────────────────────
    def test_t13a_kl_contract(self):
        self.assertEqual(envelope("x", limitations=["KL-01", "KL-99"])["limitations"],
                         ["KL-01"])
        self.assertEqual(len({k["id"] for k in KNOWN_LIMITATIONS}),
                         len(KNOWN_LIMITATIONS))
        audit_bad = for_audit(3, errors=["boom"])
        self.assertFalse(audit_bad["usable_for_decisions"])
        self.assertTrue(audit_bad["blocked_by"])
        audit_ok = for_audit(3)
        self.assertTrue(audit_ok["usable_for_decisions"])
        self.assertEqual(audit_ok["blocked_by"], [])

    @known("O-ENV-1/2", "envelope limitations 参数不校验类型：字符串静默丢、整数抛 TypeError")
    def test_t13b_limitation_input_types(self):
        with self.subTest("str"):
            self.assertIn("KL-01", envelope("x", limitations="KL-01")["limitations"])
        with self.subTest("int"):
            self.assertEqual(envelope("x", limitations=123)["limitations"], [])

    # ── T14 scope 不得削弱变更防护 ───────────────────────────────
    def test_t14_scope_does_not_disable_change_guard(self):
        p = self._p("guard")
        os.makedirs(os.path.join(p, "src"), exist_ok=True)
        base = os.path.join(self.td, "guard_base")
        os.makedirs(os.path.join(base, "src"), exist_ok=True)
        baseline_src = (
            "def parse(x):\n"
            "    if not isinstance(x, str):\n"
            "        raise ValueError('x must be str')\n"
            "    return int(x)\n")
        current_src = "def parse(x):\n    return int(x)\n"
        with open(os.path.join(base, "src", "degraded.py"), "w",
                  encoding="utf-8") as f:
            f.write(baseline_src)
        with open(os.path.join(p, "src", "degraded.py"), "w",
                  encoding="utf-8") as f:
            f.write(current_src)
        scope.add_rule(p, "exclude_glob", "src/degraded.py")
        self.assertTrue(scope.is_excluded(p, os.path.join(p, "src", "degraded.py")))

        res = ChangeGuard().guard(p, baseline_dir=base)
        self.assertEqual(res.get("source"), "baseline_dir")
        self.assertTrue(res.get("degraded"),
                        f"被 scope 排除的文件仍必须受变更防护，实测={res}")
        self.assertTrue(any("degraded" in json.dumps(f, ensure_ascii=False)
                            for f in res.get("findings") or []),
                        f"findings 未指向 degraded.py：{res.get('findings')}")

    # ── T15 调度链三方一致 ─────────────────────────────────────
    def test_t15_dispatch_chains_integrity(self):
        chains = dsp.load_chains()
        self.assertNotIn("error", chains)
        names = {str(t.get("name")) for t in BUILTIN_TOOLS if t.get("name")}
        self.assertEqual(set(INTENTS) - set(chains["chains"]), set())
        for intent in INTENTS:
            chain = chains["chains"][intent]
            self.assertIsInstance(chain.get("steps"), list)
            for step in chain["steps"]:
                self.assertIn(step.get("fail"), (None, "stop", "continue"))
                if step.get("check"):
                    continue
                tool = step.get("tool")
                self.assertTrue(tool, f"{intent} 步骤缺 tool/check: {step}")
                self.assertIn(tool, names, f"{intent} 引用了未注册工具 {tool}")
        card = dsp.plan("probe", self._p("chain"))
        self.assertEqual(card.get("status"), "ok")
        self.assertGreaterEqual(len(card.get("steps") or []), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
