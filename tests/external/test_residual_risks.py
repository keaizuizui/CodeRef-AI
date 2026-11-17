# -*- coding: utf-8 -*-
"""残余风险：大文件内容哈希采样盲区（>512KB 只哈希首尾 8KB）。

小文件对照必须检出；大文件中部等尺寸改写 + 恢复 mtime 属已知盲区（U-66）。
"""
import os
import shutil
import sys
import tempfile
import unittest

PROJECT_ROOT = os.environ.get("CODEREF_SRC",
                              os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from core.code_analyzer import (  # noqa: E402
    CodeAnalyzer, _HASH_FULL_LIMIT, _content_hash,
)

STRICT = os.environ.get("CODEREF_STRICT_KNOWN") == "1"


def known(bug: str, why: str):
    # 已知缺陷均已修复：默认即阻断（保留 bug 注释语义）
    return lambda fn: fn


class LargeFileHashSamplingTest(unittest.TestCase):

    def setUp(self):
        self.td = tempfile.mkdtemp(prefix="coderef_hash_")

    def tearDown(self):
        shutil.rmtree(self.td, ignore_errors=True)

    def _prime(self, p):
        ca = CodeAnalyzer()
        snap_path = ca._cache_snapshot_path(p)
        cache_path = ca._cache_path(p)
        os.makedirs(os.path.dirname(snap_path), exist_ok=True)
        with open(snap_path, "w", encoding="utf-8") as f:
            import json
            json.dump(ca._compute_file_snapshot(p), f)
        with open(cache_path, "w", encoding="utf-8") as f:
            f.write("{}")
        return ca

    def test_small_file_middle_change_detected(self):
        p = os.path.join(self.td, "small")
        os.makedirs(p, exist_ok=True)
        fp = os.path.join(p, "a.py")
        body = b"x" * 200 + b"MIDDLE" + b"y" * 200
        with open(fp, "wb") as f:
            f.write(body)
        ca = self._prime(p)
        st = os.stat(fp)
        with open(fp, "wb") as f:
            f.write(b"x" * 200 + b"MIDDLe" + b"y" * 200)  # 同尺寸，中部 1 字节
        os.utime(fp, (st.st_atime, st.st_mtime))
        self.assertIn(os.path.abspath(fp), ca._snapshot_drift(p, [fp]))
        self.assertFalse(ca._is_cache_valid(p))

    @known("U-66", ">512KB 文件只哈希首尾 8KB；中部等尺寸改写+恢复 mtime 会漏检")
    def test_large_file_middle_change_detected(self):
        p = os.path.join(self.td, "large")
        os.makedirs(p, exist_ok=True)
        fp = os.path.join(p, "big.py")
        size = _HASH_FULL_LIMIT + 100000
        with open(fp, "wb") as f:
            f.write(b"a" * (size // 2) + b"MIDDLE" + b"b" * (size // 2))
        self.assertGreater(os.path.getsize(fp), _HASH_FULL_LIMIT)

        ca = self._prime(p)
        st = os.stat(fp)
        before = _content_hash(fp, os.path.getsize(fp))
        with open(fp, "r+b") as f:
            f.seek(size // 2)
            f.write(b"MIDDLe")   # 中部同尺寸 1 字节变化
        os.utime(fp, (st.st_atime, st.st_mtime))
        after = _content_hash(fp, os.path.getsize(fp))

        self.assertIn(os.path.abspath(fp), ca._snapshot_drift(p, [fp]),
                      f"大文件中部变化漏检（hash {before[:8]} == {after[:8]}）")
        self.assertFalse(ca._is_cache_valid(p))


if __name__ == "__main__":
    unittest.main(verbosity=2)
