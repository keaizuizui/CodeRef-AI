# -*- coding: utf-8 -*-
"""U-44 / U-45 回归用例（独立回归补位落盘，v5.13.11）。

背景：开发方交接记录回报新增用例 DenoiseDirExcludeTest / LoadAuditFindingsLatestTest /
OverviewHealthTimestampTest 并称"已同步 coderef-src"，实际未落盘（本轮由独立回归按
tests 归属约定接管补齐）。功能修复已由 MCP 真实工具复测确认，本文件补充回归保护：

- U-44 全量审计 agent/td 维度不消费白名单 dir 排除 →
  修复定位 pipeline_runner._denoise 第〇轮：以白名单条目携带 dir 字段为准（对齐
  U-39 教训，不用 rule=="dir" 假条件），复用图谱层 _is_excluded_path 过滤全维度发现。
- U-45 overview 健康分引用旧审计缓存 →
  修复定位 interpretation_platform._load_audit_findings：多个哈希候选中按
  _scan_ts_key 取 scan_ts 最近一次，跳过空壳审计，附 _source_path/_source_kind。

运行：python -m unittest tests.test_u44_u45_regression
"""
import os
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

PROJECT_ROOT = os.environ.get("CODEREF_SRC", os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from core import pipeline_runner
from core import pr_stages
from core.interpretation_platform import (
    AUDIT_FINDINGS_FILE, _load_audit_findings, _scan_ts_key,
    _is_empty_shell_audit, _stale_audit_note, _describe_data_source,
)


def _finding(project_path: str, rel: str) -> SimpleNamespace:
    return SimpleNamespace(file_path=os.path.join(project_path, rel))


# ═══════════════════════════════════════════════════════════════════
# U-44 · 白名单 dir 级排除在所有审计维度一致生效
# ═══════════════════════════════════════════════════════════════════

class DenoiseDirExcludeTest(unittest.TestCase):
    """pipeline_runner._denoise 第〇轮：以 dir 字段为准过滤备份目录发现。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="u44_")
        self.backup = _finding(self.tmp, "_refactor_backup")
        self.core = _finding(self.tmp, "core")
        self.r = SimpleNamespace(project_path=self.tmp,
                                 findings=[self.backup, self.core])

    def _run_denoise(self, wl):
        # v5.14.4 拆解后 _denoise 位于 core.pr_stages，其内部直接绑定
        # pr_whitelist/pr_render/pr_ckpt 的符号（非经 pipeline_runner re-export），
        # 故 *_match_whitelist/_load_whitelist/_match_noise_rule 需 patch 在 pr_stages；
        # _dedup_adjacent/_burst_merge 仍由 pr_stages 内延迟 from pipeline_runner import → 留在主文件。
        with mock.patch.object(pr_stages, "_load_whitelist",
                               return_value=wl), \
             mock.patch.object(pr_stages, "_match_whitelist",
                               return_value=False), \
             mock.patch.object(pr_stages, "_match_noise_rule",
                               return_value=None), \
             mock.patch.object(pipeline_runner, "_dedup_adjacent",
                               side_effect=lambda x: x), \
             mock.patch.object(pipeline_runner, "_burst_merge",
                               side_effect=lambda x: x):
            pr_stages._denoise(self.r)

    def test_dir_exclude_removes_backup_findings(self):
        # 白名单条目仅携带 dir 字段（无 rule 字段，真实形态），备份目录发现须被过滤
        self._run_denoise([{"dir": "_refactor_backup"}])
        paths = [f.file_path for f in self.r.findings]
        self.assertNotIn(self.backup.file_path, paths)
        self.assertIn(self.core.file_path, paths)
        self.assertEqual(getattr(self.r, "dir_excluded", 0), 1)

    def test_without_dir_exclude_keeps_all(self):
        # 白名单无 dir 排除时，不得误删任何发现
        self._run_denoise([{"file": "core/*.py", "rule": "noise",
                            "category": "xxx"}])
        self.assertEqual(len(self.r.findings), 2)
        self.assertEqual(getattr(self.r, "dir_excluded", 0), 0)

    def test_empty_exclude_dirs_is_noop(self):
        # 排除目录为空列表 → 原样保留
        self._run_denoise([{"file": "core/*.py"}])
        self.assertEqual(len(self.r.findings), 2)


# ═══════════════════════════════════════════════════════════════════
# U-45 · overview 健康分加载最近一次审计缓存
# ═══════════════════════════════════════════════════════════════════

def _audit(scan_ts: str, total: int = 5):
    return {"scan_ts": scan_ts, "total_files": total, "findings": [{"x": 1}]}


class LoadAuditFindingsLatestTest(unittest.TestCase):
    """_load_audit_findings 在多个哈希候选中按 scan_ts 取最近一次。

    v5.18（U-51）新增项目侧隔离落点后，哈希候选由 2 个变 3 个：
    `<project>/.coderef/` → `<project>/coderef-report/` → 工具仓 `coderef-report/`。
    """

    def _patch_reader(self, seq):
        return mock.patch.object(
            sys.modules["core.interpretation_platform"], "_read_audit_json",
            side_effect=seq)

    def test_hashed_latest_wins(self):
        # 候选1(旧)先出现、候选3(新)后出现 → 应取新的（按 scan_ts 而非文件顺序）
        old = _audit("2026-09-01 08:00:24")
        new = _audit("2026-09-08 22:48:39")
        with self._patch_reader([old, None, new]):
            got = _load_audit_findings("any/proj")
        self.assertIsNotNone(got)
        self.assertEqual(got["scan_ts"], "2026-09-08 22:48:39")
        self.assertEqual(got["_source_kind"], "hashed")

    def test_skips_empty_shell_takes_valid(self):
        # 最新候选是空壳（total_files=0/findings=[]）→ 跳过，取有效旧候选
        shell = {"scan_ts": "2026-09-08 22:48:39",
                 "total_files": 0, "findings": []}
        valid = _audit("2026-09-01 08:00:24")
        with self._patch_reader([shell, None, valid]):
            got = _load_audit_findings("any/proj")
        self.assertIsNotNone(got)
        self.assertEqual(got["scan_ts"], "2026-09-01 08:00:24")

    def test_project_side_candidate_first(self):
        # 项目侧隔离落点优先级最高（U-51）；同一 scan_ts 时应取项目侧副本
        # （三个独立副本：代码会把 _source_path 写回各候选 dict，复用同一对象会互相覆盖）
        same = _audit("2026-09-08 22:48:39")
        with self._patch_reader([dict(same), dict(same), dict(same)]):
            got = _load_audit_findings("any/proj")
        self.assertIn(".coderef", got["_source_path"].replace("\\", "/"))

    def test_no_valid_returns_none(self):
        with mock.patch.object(
            sys.modules["core.interpretation_platform"], "_read_audit_json",
                side_effect=lambda fp: None):
            self.assertIsNone(_load_audit_findings("any/proj"))

    def test_empty_shell_detected(self):
        self.assertTrue(_is_empty_shell_audit(
            {"total_files": 0, "findings": [], "scope_text": "0 个代码文件"}))
        self.assertFalse(_is_empty_shell_audit(
            {"total_files": 5, "findings": [{"x": 1}], "scope_text": ""}))


class OverviewHealthTimestampTest(unittest.TestCase):
    """U-45 呈现层：健康区块的时间戳来源标注与旧缓存警示。"""

    def test_latest_source_exposed_as_hashed_path(self):
        data = {"scan_ts": "2026-09-08 22:48:39", "findings": [1],
                "_source_kind": "hashed",
                "_source_path": r"D:\proj\coderef-report\audit_findings_a.json"}
        self.assertEqual(_describe_data_source(data),
                         r"D:\proj\coderef-report\audit_findings_a.json")

    def test_fallback_surfaces_as_fallback_global(self):
        data = {"_source_kind": "fallback_global"}
        self.assertEqual(_describe_data_source(data), "fallback_global")

    def test_no_stale_warning_for_fresh(self):
        # v5.18.9 起「陈旧输入防呆」：任何来源过期都警示，不再只限 fallback。
        # 因此这里改用「新鲜快照」（当前时间）断言无警示。
        from datetime import datetime
        fresh = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        data = {"scan_ts": fresh, "findings": [1], "_source_kind": "hashed"}
        self.assertEqual(_stale_audit_note(data), "")

    def test_stale_warning_for_old_hashed(self):
        # 新语义保护：哈希源但快照过旧，同样要警示（防"读旧数据当现状"）
        data = {"scan_ts": "2026-09-08 22:48:39", "findings": [1],
                "_source_kind": "hashed"}
        self.assertIn("审计数据较旧", _stale_audit_note(data))

    def test_stale_warning_only_for_old_fallback(self):
        # 兜底全局缓存 scan_ts 距今过久 → 追加旧数据警示
        data = {"scan_ts": "2020-01-01 00:00:00", "findings": [1],
                "_source_kind": "fallback_global"}
        note = _stale_audit_note(data)
        self.assertIn("审计数据较旧", note)


if __name__ == "__main__":
    unittest.main(verbosity=2)