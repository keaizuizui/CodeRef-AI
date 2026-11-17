# -*- coding: utf-8 -*-
"""真跨进程并发：dream.seed 追加与 dream.run 合并（不是线程模拟）。

判据：
- 8 进程 × 5 条 seed 并发 → 40 行、40 个唯一 id；
- 2 进程并发 run（各自新增）→ 两条笔记都保留。
"""
import glob
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

PROJECT_ROOT = os.environ.get("CODEREF_SRC",
                              os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

STRICT = os.environ.get("CODEREF_STRICT_KNOWN") == "1"


def known(bug: str, why: str):
    # 已知缺陷均已修复：默认即阻断（保留 bug 注释语义）
    return lambda fn: fn

RUNNER = r"""
import json
import sys
import time
sys.path.insert(0, %(root)r)
from core import dream

p = sys.argv[1]
mode = sys.argv[2]
if mode == "seed":
    for i in range(int(sys.argv[4])):
        dream.seed(p, sys.argv[3] + "-" + str(i))
else:
    text = sys.argv[3]
    slow = mode == "slowrun"
    dream.seed(p, "mp-" + text)
    sid = dream._read_seeds(p)[-1]["id"]

    class FakeCfg:
        max_tokens = 4096
        provider = None
        model = "fake"

    class FakeLLM:
        def __init__(self):
            self.config = FakeCfg()
        def is_available(self):
            return True
        def chat_completion(self, *a, **k):
            if slow:
                time.sleep(2.0)
            return json.dumps({"notes": [{
                "text": text, "kind": "rule", "claim_type": "fact",
                "evidence": ["seed:" + sid], "confidence": 0.6}],
                "unknowns": []}, ensure_ascii=False)

    r = dream.run(p, job="pitfall", llm=FakeLLM())
    print(json.dumps({"status": r.get("status", ""),
                      "error": r.get("error", ""),
                      "reason": r.get("reason", "")}, ensure_ascii=False))
"""


class MultiProcessConcurrencyTest(unittest.TestCase):

    def setUp(self):
        self.td = tempfile.mkdtemp(prefix="coderef_mp_")
        self.p = os.path.join(self.td, "p")
        os.makedirs(self.p, exist_ok=True)
        with open(os.path.join(self.p, "util.py"), "w", encoding="utf-8") as f:
            f.write("def add(a, b):\n    return a + b\n")
        self.code = RUNNER % {"root": PROJECT_ROOT}

    def tearDown(self):
        shutil.rmtree(self.td, ignore_errors=True)

    def _spawn(self, *args):
        return subprocess.Popen(
            [sys.executable, "-c", self.code, self.p] + list(args),
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8", errors="replace")

    @known("U-67", "并发锁只在进程内生效：跨进程 seed 丢行 / notes 写失败")
    def test_multiprocess_seed_no_loss(self):
        procs = [self._spawn("seed", "mpseed", "5") for _ in range(8)]
        for proc in procs:
            out, err = proc.communicate(timeout=180)
            self.assertEqual(proc.returncode, 0, f"子进程失败: {err[-300:]}")
        sp = os.path.join(self.p, ".coderef", "dream", "seeds.jsonl")
        with open(sp, encoding="utf-8") as f:
            lines = [ln for ln in f.read().splitlines() if ln.strip()]
        self.assertEqual(len(lines), 40, f"跨进程 seed 丢行: {len(lines)}/40")
        ids = {json.loads(ln).get("id") for ln in lines}
        self.assertEqual(len(ids), 40, "跨进程 seed 出现重复 id")

    @known("U-67", "并发锁只在进程内生效：跨进程 run 丢更新 / notes 写失败")
    def test_multiprocess_run_keeps_both(self):
        procs = [self._spawn("run", "跨进程规则A"),
                 self._spawn("run", "跨进程规则B")]
        for proc in procs:
            out, err = proc.communicate(timeout=180)
            self.assertEqual(proc.returncode, 0, f"子进程失败: {err[-300:]}")
            data = json.loads((out or "").strip() or "{}")
            self.assertEqual(data.get("status"), "ok",
                             f"子进程未成功: {out}{err[-300:]}")
        from core import dream
        texts = {n.get("text") for n in dream._load_notes(self.p)}
        self.assertEqual(texts, {"跨进程规则A", "跨进程规则B"},
                         f"跨进程并发 run 丢更新: {texts}")

        state_path = os.path.join(self.p, ".coderef", "dream", "state.json")
        with open(state_path, encoding="utf-8") as f:
            state = json.load(f)
        consumed = int((state.get("cursor") or {}).get("seeds_consumed") or 0)
        self.assertGreaterEqual(consumed, 2,
                                f"并发 run 后游标回退/少算: {state.get('cursor')}")
        self.assertGreaterEqual(
            len(glob.glob(os.path.join(self.p, ".coderef", "dream", "runs", "*.json"))),
            2, "并发 run 后 runs 记录少于 2 份（疑似文件名碰撞覆盖）")

    def test_seed_not_blocked_by_slow_run(self):
        """v5.21.14：run 跑 LLM 期间 seed 仍应立即返回（不持整轮锁）。"""
        slow = self._spawn("slowrun", "慢跑规则")
        time.sleep(0.7)  # 等它进入 LLM 阶段
        t0 = time.time()
        r = subprocess.run(
            [sys.executable, "-c", self.code, self.p, "seed", "旁路种子", "1"],
            capture_output=True, text=True, encoding="utf-8", timeout=60)
        elapsed = time.time() - t0
        self.assertEqual(r.returncode, 0, f"seed 子进程失败: {r.stderr[-300:]}")
        self.assertLess(elapsed, 1.8, f"seed 被 run 的整轮锁阻塞: {elapsed:.2f}s")
        out, err = slow.communicate(timeout=180)
        self.assertEqual(slow.returncode, 0, f"slowrun 失败: {err[-300:]}")
        self.assertEqual(json.loads(out.strip() or "{}").get("status"), "ok")


if __name__ == "__main__":
    unittest.main(verbosity=2)
