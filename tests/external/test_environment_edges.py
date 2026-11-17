# -*- coding: utf-8 -*-
"""环境异常时的诚实行为回归（临时目录 + 真实 ACL / mock）。

覆盖 6 类边界：
1. 项目目录只读 → sync / docs 必须如实报错或降级，不得崩溃、不得静默成功；
2. 输出目录只读 → 渲染必须报错，不得静默丢产物；
3. 路径含空格 / 中文 / 特殊字符 → 全链路可用；
4. 超长路径（接近 260 字符）→ 不得崩；
5. 跨天 / 时区：产物时间戳落在昨天 → freshness 必须判 stale_by_age 或 code_newer；
6. 反向：正常环境下上述降级标记必须全为 False。

约束：
- 只读只施加在 tempfile 临时目录上（`_deny_write` 内有 tempdir 归属断言，
  绝不可能误设真实项目或 coderef-src 为只读）；
- 权限一律 try/finally（tearDown 兜底）恢复；
- memory_layer 的状态/报表目录重定向到临时目录，避免污染 coderef-src。

运行：
    cd coderef-src
    python -m unittest discover -s "..\\独立测试\\tests" -p "test_environment_edges.py" -v
"""
import hashlib
import json
import os
import shutil
import subprocess
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

from core import memory_layer as ml  # noqa: E402
from core.memory_layer import memory_layer  # noqa: E402
from core.pr_models import PipeResult  # noqa: E402
from core.report_renderer import HtmlReportRenderer  # noqa: E402
from core.artifact_freshness import report as freshness_report  # noqa: E402
from core.output_paths import default_report_dir  # noqa: E402
from core.pipeline_runner import _dump_findings_json, docs  # noqa: E402
from core.path_key import project_key  # noqa: E402


def _phash(project_path: str) -> str:
    return hashlib.md5(project_key(project_path).encode()).hexdigest()[:12]


def _whoami() -> str:
    user = os.environ.get("USERNAME", "")
    domain = os.environ.get("USERDOMAIN", "")
    return f"{domain}\\{user}" if domain and user else user


def _icacls(*args):
    """调用 icacls；不可用/超时返回 None（调用方据此如实 SKIP）。"""
    try:
        return subprocess.run(["icacls", *args], capture_output=True,
                              text=True, errors="ignore", timeout=30)
    except (OSError, subprocess.SubprocessError):
        return None


class _EnvBase(unittest.TestCase):
    """临时目录 + 状态目录重定向 + 权限恢复兜底。"""

    def setUp(self):
        self.td = tempfile.mkdtemp(prefix="coderef_env_")
        self._denied = []
        self._orig_state = ml._MEMORY_STATE_DIR
        self._orig_report = ml._REPORT_DIR
        self._state_dir = os.path.join(self.td, "_mem_state")
        self._report_dir = os.path.join(self.td, "_mem_report")
        os.makedirs(self._state_dir, exist_ok=True)
        os.makedirs(self._report_dir, exist_ok=True)
        ml._MEMORY_STATE_DIR = self._state_dir
        ml._REPORT_DIR = self._report_dir

    def tearDown(self):
        for item in self._denied:
            self._restore_write(item)
        self._denied = []
        ml._MEMORY_STATE_DIR = self._orig_state
        ml._REPORT_DIR = self._orig_report
        shutil.rmtree(self.td, ignore_errors=True)

    # ── 项目构造 ──
    def project(self, name="p"):
        p = os.path.join(self.td, name)
        os.makedirs(os.path.join(p, "pkg"), exist_ok=True)
        self._write(os.path.join(p, "main.py"),
                    "from pkg.util import add\n\n\ndef run():\n    return add(1, 2)\n")
        self._write(os.path.join(p, "pkg", "__init__.py"), "")
        self._write(os.path.join(p, "pkg", "util.py"),
                    "def add(a, b):\n    return a + b\n")
        return p

    @staticmethod
    def _write(fp, text):
        os.makedirs(os.path.dirname(fp), exist_ok=True)
        with open(fp, "w", encoding="utf-8") as f:
            f.write(text)
        return fp

    # ── 只读权限（仅限 tempdir） ──
    def _deny_write(self, path):
        """对临时目录施加 Deny-Write；返回是否施加成功。"""
        real = os.path.realpath(path)
        real_td = os.path.realpath(self.td)
        if not (real == real_td or real.startswith(real_td + os.sep)):
            raise AssertionError(f"拒绝在临时目录之外施加只读: {path}")
        who = _whoami()
        if not who:
            return False
        # 只拒「写数据/追加/写属性」四个具体权限，不用泛型 (W)：
        # 泛型 W 映射还会带上 SYNCHRONIZE/READ_CONTROL，导致目录枚举
        # 直接 PermissionError，扫描静默变成 0 文件，测试就失真了。
        r = _icacls(path, "/deny", f"{who}:(OI)(CI)(WD,AD,WEA,WA)")
        if r is None or r.returncode != 0:
            return False
        self._denied.append((path, who))
        return True

    def _restore_write(self, item):
        path, who = item
        _icacls(path, "/remove:d", who, "/T")

    def _assert_effective_deny(self, path, what: str):
        """施加后必须实测写不进去；写不进去才算真实只读，否则诚实 SKIP。"""
        probe = os.path.join(path, ".acl_probe")
        try:
            with open(probe, "w", encoding="utf-8") as f:
                f.write("x")
        except OSError:
            return
        try:
            os.remove(probe)
        except OSError:
            pass
        self.skipTest(f"{what}：icacls Deny 未生效（可能为高权限/所有者），"
                      f"Windows ACL 无法构造真实只读，如实跳过")


class ReadOnlyProjectTest(_EnvBase):
    """1) 项目只读：sync / docs 必须诚实报错或降级。"""

    def test_readonly_project_sync_not_silent(self):
        if os.name != "nt":
            self.skipTest("仅 Windows 可构造 ACL 只读")
        p = self.project()
        if not self._deny_write(p):
            self.skipTest("icacls 不可用，无法构造真实只读项目")
        self._assert_effective_deny(p, "项目只读")

        res = memory_layer.sync(p, mode="full")
        self.assertIsInstance(res, dict, f"sync 不得崩溃/返回非 dict: {res}")
        kg = res.get("kg") or {}
        surfaced = (res.get("status") != "ok") or bool(kg.get("errors"))
        self.assertTrue(
            surfaced,
            f"只读项目 sync 不得静默成功（status=ok 且无 kg.errors）: {res}")
        self.assertGreater(res.get("total_files", 0), 0,
                           f"只读只影响写盘，读扫描不应变成 0 文件: {res}")
        db = os.path.join(p, "cache", "kg", f"{_phash(p)}.db")
        self.assertFalse(os.path.exists(db),
                         "只读项目下不应凭空产生知识图谱库")

    def test_readonly_project_docs_records_error(self):
        if os.name != "nt":
            self.skipTest("仅 Windows 可构造 ACL 只读")
        p = self.project()
        if not self._deny_write(p):
            self.skipTest("icacls 不可用，无法构造真实只读项目")
        self._assert_effective_deny(p, "项目只读")

        def _stub_wiki(project_path, r, done, output_dir=None, **kw):
            # 真实写盘（不 mock 落盘行为），只跳过依赖 LLM 的正文生成
            d = os.path.join(project_path, ".coderef", "wiki")
            os.makedirs(d, exist_ok=True)
            with open(os.path.join(d, "README.md"), "w", encoding="utf-8") as f:
                f.write("# wiki\n")

        with mock.patch("core.pipeline_runner._wiki", _stub_wiki):
            r = docs(p, include_subprojects=False, cross_verify=False,
                     mode="full")
        self.assertTrue(r.errors,
                        f"只读项目 docs 必须把失败写进 errors: {r.errors}")
        index = os.path.join(p, ".coderef", "report", "html", "index.html")
        self.assertFalse(os.path.isfile(index),
                         "只读项目下不应产出报告入口")


class ReadOnlyOutputDirTest(_EnvBase):
    """2) 输出目录只读：必须报错，不得静默丢产物。"""

    def test_readonly_output_dir_returns_error(self):
        if os.name != "nt":
            self.skipTest("仅 Windows 可构造 ACL 只读")
        p = self.project()
        out = os.path.join(self.td, "ro_out", "html")
        os.makedirs(out, exist_ok=True)
        if not self._deny_write(out):
            self.skipTest("icacls 不可用，无法构造只读输出目录")
        self._assert_effective_deny(out, "输出目录只读")

        r = PipeResult(project_path=p)
        res = HtmlReportRenderer(p).render(r, kg_stats=None, output_dir=out)
        self.assertFalse(res.get("ok"),
                         f"只读输出目录不得返回成功: {res}")
        self.assertTrue(str(res.get("error") or "").strip(),
                        f"只读输出目录必须给出错误信息: {res}")
        self.assertEqual(res.get("files") or [], [],
                         "写入失败时不得声称已写出文件")
        self.assertFalse(os.path.isfile(os.path.join(out, "index.html")),
                         "只读输出目录不应出现 index.html")


class SpecialCharPathTest(_EnvBase):
    """3) 路径含空格 / 中文 / 特殊字符：全链路可用。"""

    NAME = "项目 A (v2) #1"

    def test_full_chain_usable(self):
        p = self.project(self.NAME)
        res = memory_layer.sync(p, mode="full")
        self.assertEqual(res.get("status"), "ok", f"特殊字符路径 sync 失败: {res}")
        kg = res.get("kg") or {}
        self.assertFalse(kg.get("errors"), f"特殊字符路径图谱构建报错: {kg}")
        self.assertGreater(kg.get("nodes", 0), 0, f"图谱节点应为正数: {kg}")

        db = os.path.join(p, "cache", "kg", f"{_phash(p)}.db")
        self.assertTrue(os.path.isfile(db), "图谱库应落在项目 cache/kg 下")

        r = PipeResult(project_path=p)
        r.total_files = res.get("total_files", 0)
        out = os.path.join(p, "out")
        _dump_findings_json(r, out)
        self.assertFalse(r.errors, f"findings 落盘失败: {r.errors}")
        self.assertTrue(os.path.isfile(
            os.path.join(p, ".coderef", f"audit_findings_{_phash(p)}.json")),
            "特殊字符路径下 findings 产物路径异常")

        res2 = HtmlReportRenderer(p).render(
            PipeResult(project_path=p), kg_stats=kg,
            output_dir=default_report_dir(p))
        self.assertTrue(res2.get("ok"), f"特殊字符路径渲染失败: {res2}")
        self.assertTrue(os.path.isfile(os.path.join(
            default_report_dir(p), "index.html")))

        rep = freshness_report(p)
        audit = next(a for a in rep["artifacts"] if a["key"] == "audit_findings")
        self.assertTrue(audit["exists"], f"特殊字符路径 freshness 未识别产物: {audit}")


class LongPathTest(_EnvBase):
    """4) 超长路径（接近 260 字符）不得崩。"""

    def test_near_max_path_no_crash(self):
        path = self.td
        seg = "long_dir_segment"
        # 目标：项目根 ~240+ 字符，项目内文件/图谱库会越过 260 上限
        while len(os.path.join(path, seg, "proj")) <= 245:
            path = os.path.join(path, seg)
        p = os.path.join(path, "proj")
        try:
            self.project(os.path.relpath(p, self.td))
        except OSError as e:
            self.skipTest(f"当前系统未启用长路径支持，无法创建近 260 字符目录: {e}")
        self.assertGreaterEqual(len(p), 200, "构造的路径应接近上限")

        # 不崩溃即可：成功走全量，失败也必须显式降级（kg.errors）
        res = memory_layer.sync(p, mode="full")
        self.assertIsInstance(res, dict, f"超长路径 sync 不得崩溃: {res}")
        if res.get("status") == "ok":
            kg = res.get("kg") or {}
            self.assertTrue(
                kg.get("nodes", 0) > 0 or kg.get("errors"),
                f"超长路径 sync 成功却既无节点也无错误披露: {kg}")

        rep = freshness_report(p)
        self.assertEqual(rep.get("project_path"), os.path.abspath(p))


class _ShiftedDatetime(datetime):
    """模拟跨天：now() 比真实时间晚 2 天（保留 fromtimestamp 等行为）。"""

    @classmethod
    def now(cls, tz=None):
        return datetime.now(tz) + timedelta(days=2)


class CrossDayFreshnessTest(_EnvBase):
    """5) 时区 / 跨天：产物仍是「昨天」时必须判 stale，不得继续显示新鲜。"""

    def test_yesterday_artifact_judged_stale(self):
        p = self.project()
        fp = self._write(os.path.join(p, ".coderef", "scorecard.json"),
                         json.dumps({"overall": 90}))
        yesterday = time.time() - 86400
        os.utime(fp, (yesterday, yesterday))

        # mock 时间推进到「明天」，产物即落在更早的跨天区间
        with mock.patch("core.artifact_freshness.datetime", _ShiftedDatetime):
            rep = freshness_report(p)

        item = next(a for a in rep["artifacts"] if a["key"] == "scorecard")
        self.assertTrue(item["exists"], f"产物应被识别: {item}")
        self.assertGreater(item["age_days"], item["suggested_max_age_days"],
                           f"跨天后 age_days 必须超过建议时效: {item}")
        self.assertTrue(
            item["stale_by_age"] or item["code_newer"],
            f"昨天的产物不得继续判为新鲜（stale_by_age/code_newer 全 False）: {item}")


class NormalEnvReverseTest(_EnvBase):
    """6) 反向：正常环境跑完，所有降级标记必须为 False。"""

    def test_normal_env_no_degrade_flags(self):
        p = self.project()
        res = memory_layer.sync(p, mode="full")
        self.assertEqual(res.get("status"), "ok", f"正常环境 sync 失败: {res}")
        kg = res.get("kg") or {}
        self.assertFalse(kg.get("errors"), f"正常环境不应有图谱错误: {kg}")
        self.assertGreater(kg.get("nodes", 0), 0, f"正常环境图谱应有节点: {kg}")

        r = PipeResult(project_path=p)
        r.total_files = res.get("total_files", 0)
        _dump_findings_json(r, os.path.join(p, "out"))
        self.assertFalse(r.errors, f"正常环境 findings 落盘不应报错: {r.errors}")

        render = HtmlReportRenderer(p).render(
            r, kg_stats=kg, output_dir=default_report_dir(p))
        self.assertTrue(render.get("ok"), f"正常环境渲染应成功: {render}")
        self.assertEqual(r.errors, [], f"正常环境渲染不应产生错误: {r.errors}")

        # 代码早于产物：把代码 mtime 前移 1 小时，避免 code_newer 误命中
        older = time.time() - 3600
        for root, _dirs, files in os.walk(p):
            for fn in files:
                if fn.endswith(".py"):
                    os.utime(os.path.join(root, fn), (older, older))

        rep = freshness_report(p)
        by_key = {a["key"]: a for a in rep["artifacts"]}
        for key in ("audit_findings", "architecture_report", "kg_db"):
            item = by_key.get(key)
            if not item or not item.get("exists"):
                continue
            self.assertFalse(item.get("stale_by_age"),
                             f"正常环境下 {key} 不应判超龄: {item}")
            self.assertFalse(item.get("code_newer"),
                             f"正常环境下 {key} 不应判代码更新: {item}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
