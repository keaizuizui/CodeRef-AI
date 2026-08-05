# -*- coding: utf-8 -*-
"""
回归测试 —— 防止"多数工具结果被静默丢弃"的缺陷再次出现。

背景：core/pipeline_runner.py 的 Pipe.audit(project_path) 会调用 11 个检测工具
（gov, agent, sca, td, integ, blind, inn, junk, resgap, simp, matu），并把各自
结果 append 到返回的 PipeResult.findings（每个 finding 有 .tool 字段）。
本测试对项目自身路径运行完整 audit，断言 11 个工具的结果全部都被收集到
findings 中，从而防止"多数工具结果被静默丢弃"的回归缺陷。

说明：
- 仅使用 unittest 标准库，不依赖 pytest。
- 在文件顶部把项目根目录插入 sys.path，确保可独立运行。
- 对可能联网/依赖外部 CLI 的检测器做运行时降级（通过 mock 打补丁，不改源码）：
    * innovation_propagation_detector：强制 use_llm=False，走纯结构对比模式，
      避免 LLM/网络依赖（openai 未安装时原本也会自动降级，这里显式保证）。
    * sca_checker：保持 pipeline 默认的在线模式。其内部对 OSV 网络失败已用
      try/except 容忍（5s 超时后降级为本地库），故联网不会导致测试崩溃；
      而强制 offline 反而可能因本地库无命中使 sca 产出 0 条 findings 造成误报。
- 运行时依赖缺失（如 openai、tree_sitter_languages 未安装）会被底层 try/except
  容忍，不会导致测试失败。
"""

import os
import sys
import time
import unittest
from unittest import mock

# ── 使测试可独立运行：把项目根目录插入 sys.path ──
PROJECT_ROOT = r"C:\Users\30822\Desktop\1111\Coderef-Ai\Coderef-Ai-master"
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)
if os.getcwd() != PROJECT_ROOT:
    try:
        os.chdir(PROJECT_ROOT)
    except OSError:
        pass

# 11 个审计工具名（与 pipeline_runner.audit 的调用顺序一致）
EXPECTED_TOOLS = [
    "gov", "agent", "sca", "td", "integ", "blind",
    "inn", "junk", "resgap", "simp", "matu",
]


class TestPipelineRunner(unittest.TestCase):
    """回归测试：Pipe.audit 必须收集到全部 11 个工具的结果"""

    @classmethod
    def setUpClass(cls):
        """运行一次完整 audit，两个测试法共享结果以节省时间。

        同时在此处施加运行时降级补丁，避免联网 / LLM / 外部 CLI 依赖。
        """
        cls._patchers = []

        # 1) 创新传播检测器：强制 use_llm=False（纯结构对比），避免 LLM/网络
        try:
            from core import innovation_propagation_detector as _ipd
        except Exception:
            _ipd = None
        if _ipd is not None:
            _orig_detect = _ipd.InnovationPropagationDetector.detect

            def _force_structural(self, project_path, use_llm=True, max_llm_rounds=20):
                # 等价于给 detect 传 use_llm=False（pipeline_runner 内部传的是 True）
                return _orig_detect(self, project_path, use_llm=False,
                                    max_llm_rounds=max_llm_rounds)

            p1 = mock.patch.object(_ipd.InnovationPropagationDetector,
                                   "detect", _force_structural)
            p1.start()
            cls._patchers.append(p1)

        # 2) SCA 检查器：按要求允许其联网查询 OSV（pipeline 默认即在线模式）。
        #    不强制 offline —— 因为若强制离线且本地漏洞库无命中，sca 会产出 0 条
        #    findings，从而在"必须包含全部 11 工具"的断言下误报回归。
        #    sca_checker 内部对网络失败已用 try/except 容忍（5s 超时后降级为本地库），
        #    故联网不会导致测试崩溃。

        # 运行完整 audit
        from core.pipeline_runner import Pipe
        cls._pipe = Pipe()
        _t0 = time.time()
        cls.result = cls._pipe.audit(PROJECT_ROOT)
        cls.elapsed = round(time.time() - _t0, 1)
        print(f"\n[TestPipelineRunner] audit 实际耗时: {cls.elapsed}s "
              f"(文件数={cls.result.total_files}, 行数={cls.result.total_lines})")

    @classmethod
    def tearDownClass(cls):
        for p in cls._patchers:
            try:
                p.stop()
            except Exception:
                pass

    # ── 测试 1：11 个工具的结果全部被收集 ──
    def test_audit_collects_all_11_tools(self):
        r = self.result
        tools = {f.tool for f in r.findings}
        missing = [t for t in EXPECTED_TOOLS if t not in tools]

        self.assertEqual(
            missing, [],
            "回归缺陷重现：以下工具的结果被静默丢弃，未出现在 PipeResult.findings 中: "
            f"{missing}。实际 findings 中的 tools: {sorted(tools)}。"
            f"errors: {r.errors}")

        # 额外保护：findings 不应为空
        self.assertGreater(len(r.findings), 0,
                           "findings 为空，11 个工具均未产出任何结果")

    # ── 测试 2：错误被正确记录，且包含工具名 ──
    def test_pipeline_errors_are_recorded(self):
        r = self.result
        if not r.errors:
            # 正常情况下 11 个工具都能跑，errors 为空即满足断言
            self.skipTest("所有 11 个工具均成功运行，r.errors 为空，无需验证错误记录")
            return

        # errors 非空时：每条错误信息的工具名前缀必须是合法工具名
        for e in r.errors:
            prefix = e.split(":", 1)[0].strip()
            self.assertIn(
                prefix, EXPECTED_TOOLS,
                f"错误信息 {e!r} 的工具名前缀 {prefix!r} 不在 11 个工具名中，"
                "无法据此定位是哪个工具失败")


if __name__ == "__main__":
    unittest.main()