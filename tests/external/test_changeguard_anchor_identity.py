# -*- coding: utf-8 -*-
"""#3 · v5.13.10 锚点阻塞解除回归（开发方回报，独立回归落位 coderef-src）。

- 复现缺陷记录 #3：无持久化 git 身份仓库上 `anchor_health_baseline` 因
  `Committer identity unknown` 打不了 coderef-health-* tag。
- 修复：模块级 `_HEALTH_IDENTITY` 用临时身份 `-c user.name/email` 执行
  commit/tag，不写 git config（不违反「NEVER update git config」铁律）。

运行：python -m unittest tests.test_changeguard_anchor_identity -v
（依赖真实 git 可执行文件；用临时目录，不触碰任何现有仓库。）
"""
import os
import shutil
import subprocess
import tempfile
import unittest

from core.change_guard import ChangeGuard

GANCHOR = ["-c", "user.name=CodeRef-AI", "-c", "user.email=coderef@local"]


def _git(d, *args):
    return subprocess.run(["git", *args], cwd=d,
                          capture_output=True, text=True)


def _init_repo(d):
    subprocess.run(["git", "init", "-q"], cwd=d, check=True)
    with open(os.path.join(d, "a.txt"), "w") as f:
        f.write("healthy\n")
    subprocess.run(["git", "add", "-A"], cwd=d, check=True)
    r = _git(d, *GANCHOR, "commit", "-q", "-m", "init")
    assert r.returncode == 0, r.stderr


def _tags(d):
    r = _git(d, "tag", "-l", "coderef-health*")
    return [t for t in r.stdout.splitlines() if t.strip()]


class ChangeGuardAnchorIdentityTest(unittest.TestCase):
    """无持久化 git 身份仓库上 anchor 成功打 coderef-health-* tag（#3 场景）。"""

    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.d, ignore_errors=True)

    def test_anchor_succeeds_without_persisted_identity(self):
        _init_repo(self.d)
        # 复现前置：该仓库确实无持久化 user identity
        name = _git(self.d, "config", "user.name").stdout.strip()
        self.assertEqual(name, "")
        res = ChangeGuard().anchor_health_baseline(self.d, allow_autocommit=False)
        self.assertTrue(res["ok"], res["message"])
        self.assertTrue(res["tag"].startswith("coderef-health-"))
        self.assertEqual(res["committed"], 0)
        self.assertIn(res["tag"], _tags(self.d))

    def test_anchor_second_time_appends_suffix(self):
        _init_repo(self.d)
        cg = ChangeGuard()
        first = cg.anchor_health_baseline(self.d, allow_autocommit=False)
        second = cg.anchor_health_baseline(self.d, allow_autocommit=False)
        self.assertTrue(first["ok"])
        self.assertTrue(second["ok"])
        tags = _tags(self.d)
        # 二次锚定自动追加序号，不覆盖既有基线
        self.assertEqual(len(tags), 2)
        self.assertNotEqual(first["tag"], second["tag"])
        self.assertTrue(second["tag"].endswith("-2"))

    def test_anchor_autocommit_uses_temp_identity(self):
        _init_repo(self.d)
        # 工作区新增未提交改动，allow_autocommit=True 应带临时身份自动提交后打 tag
        with open(os.path.join(self.d, "a2.txt"), "w") as f:
            f.write("dirty\n")
        cg = ChangeGuard()
        res = cg.anchor_health_baseline(self.d, allow_autocommit=True)
        self.assertTrue(res["ok"], res["message"])
        self.assertEqual(res["committed"], 1)
        self.assertIn(res["tag"], _tags(self.d))
        # 且不应写持久化 config
        self.assertEqual(_git(self.d, "config", "user.name").stdout.strip(), "")


if __name__ == "__main__":
    unittest.main()