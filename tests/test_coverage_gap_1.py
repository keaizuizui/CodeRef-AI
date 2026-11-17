# -*- coding: utf-8 -*-
"""覆盖率第 1 批补用例（coverage-gap）：标准库 unittest，零真实 LLM / 外部服务。

覆盖（对照 generate/coverage_gap_report.md 第 1 批清单）：
1. core/output_evaluator.py   → TestOutputEvaluator   （mock _call_llm / is_available）
2. core/verify_findings.py    → TestEvidenceLabeler / TestSymbolExtraction / TestFindingsVerifier
3. core/governance_store.py   → TestGovernanceStore   （临时 SQLite，tearDown 清理）
4. core/llm_integration.py 纯函数 → TestLLMJsonRepair / TestLLMConfigLoading

运行方式：
    python -m pytest tests/test_coverage_gap_1.py -q
"""
import json
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from core.output_evaluator import (
    OutputEvaluator,
    _safe_threshold, _normalize_metrics, _normalize_items,
    _extract_score, _compact_text,
    MAX_EVAL_TEXT_CHARS, MAX_EVAL_CONTEXT_CHARS,
)
from core.verify_findings import (
    EvidenceLabeler, FindingsVerifier, verify_findings,
    extract_symbols, _is_symbol_like, render_report, render_html,
    VERDICT_CONFIRMED, VERDICT_REFUTED, VERDICT_PARTIAL, VERDICT_UNVERIFIABLE,
    VERDICT_FILE_ONLY,
)
from core.governance_store import (
    GovernanceStore, gap_key, gap_snapshot, gov_db_path,
    ALL_STATUSES,
)
from core.llm_integration import (
    LLMIntegration, LLMConfig, LLMProvider,
    _safe_float, _safe_int, _complete_bare_token,
    _repair_truncated_json, _normalize_nonstandard_json,
    _load_llm_config_from_settings,
)


# ═══════════════════════════════════════════════════════════════════
# 1. core/output_evaluator.py
# ═══════════════════════════════════════════════════════════════════

class TestOutputEvaluator(unittest.TestCase):
    """产出物语义评估：纯函数 + mock LLM 的判定链（零真实网络）。"""

    # 按指标 prompt 模板关键词区分返回值的默认 LLM 桩
    def _default_llm(self, messages):
        user = messages[1]["content"]
        if "结构" in user and "连贯" in user:
            return json.dumps({"score": 0.9, "reason": "结构清晰",
                               "breakdown": {"structure": "清晰", "self_contradiction": False}})
        if "相关程度" in user:
            return json.dumps({"score": 0.9, "reason": "切题",
                               "breakdown": {"topic_coverage": "高", "off_topic": False}})
        if "忠实" in user:
            return json.dumps({"score": 0.9, "reason": "忠实",
                               "breakdown": {"claims_checked": 5, "claims_supported": 5,
                                             "hallucinated": 0}})
        if "虚构" in user:
            return json.dumps({"score": 0.9, "reason": "无虚构",
                               "breakdown": {"claims_checked": 5, "claims_supported": 5,
                                             "hallucinated": 0}})
        return json.dumps({"score": 0.9, "reason": "ok", "breakdown": {}})

    def setUp(self):
        # 默认视为 LLM 可用（防开发机真实环境变量/占位 key 干扰判定分支）
        self._avail = mock.patch.object(LLMIntegration, "is_available", return_value=True)
        self._avail.start()
        self.addCleanup(self._avail.stop)
        self.ev = OutputEvaluator()
        self._llm = mock.patch.object(self.ev, "_call_llm", side_effect=self._default_llm)
        self._llm.start()
        self.addCleanup(self._llm.stop)

    # ── 纯函数 ──
    def test_safe_threshold_defaults(self):
        self.assertEqual(_safe_threshold(None), 0.7)
        self.assertEqual(_safe_threshold("abc"), 0.7)
        self.assertEqual(_safe_threshold(1.5), 0.7)
        self.assertEqual(_safe_threshold(-0.1), 0.7)
        self.assertEqual(_safe_threshold(0.85), 0.85)
        self.assertEqual(_safe_threshold("0.6"), 0.6)

    def test_normalize_metrics_variants(self):
        self.assertEqual(_normalize_metrics(None), [])
        self.assertEqual(_normalize_metrics(""), [])
        self.assertEqual(_normalize_metrics("  "), [])
        self.assertEqual(_normalize_metrics("faithfulness"), ["faithfulness"])
        self.assertEqual(_normalize_metrics(["faithfulness", "coherence", "", None]),
                         ["faithfulness", "coherence"])
        self.assertEqual(_normalize_metrics(("hallucination",)), ["hallucination"])
        self.assertEqual(_normalize_metrics(123), ["123"])

    def test_normalize_items_variants(self):
        self.assertEqual(_normalize_items(None), [])
        self.assertEqual(_normalize_items("hello"), ["hello"])
        self.assertEqual(_normalize_items(["a", "b", "", None]), ["a", "b"])
        self.assertEqual(_normalize_items(()), [])
        self.assertEqual(_normalize_items(42), ["42"])

    def test_extract_score_clamp(self):
        self.assertEqual(_extract_score({"score": 0.9}), (0.9, False))
        self.assertEqual(_extract_score({"score": 1.2}), (1.0, True))
        self.assertEqual(_extract_score({"score": -0.3}), (0.0, True))
        self.assertEqual(_extract_score({"score": "0.5"}), (0.5, False))
        self.assertEqual(_extract_score({"score": "abc"}), (0.0, False))
        self.assertEqual(_extract_score({"score": None}), (0.0, False))
        self.assertEqual(_extract_score({}), (0.0, False))

    def test_compact_text_empty_and_trim(self):
        self.assertEqual(_compact_text(""), "")
        self.assertEqual(_compact_text(None), "")
        self.assertEqual(_compact_text("a\n  b\tc"), "a b c")
        long_text = "x" * 500
        self.assertEqual(len(_compact_text(long_text)), 300)

    # ── evaluate 参数护栏 ──
    def test_evaluate_unknown_action(self):
        r = self.ev.evaluate(text="hi", metric="coherence", action="banana")
        self.assertEqual(r["status"], "error")
        self.assertIn("未知 action", r["error"])

    def test_evaluate_unknown_metric(self):
        r = self.ev.evaluate(text="hi", metric="nonsense")
        self.assertEqual(r["status"], "error")
        self.assertIn("未知指标 'nonsense'", r["error"])

    def test_evaluate_empty_metric(self):
        r = self.ev.evaluate(text="hi", metric="")
        self.assertEqual(r["status"], "error")
        self.assertIn("metric 不能为空", r["error"])

    def test_evaluate_empty_text(self):
        r = self.ev.evaluate(text="", metric="coherence")
        self.assertEqual(r["status"], "error")
        self.assertIn("text 不能为空", r["error"])

    def test_evaluate_assert_rejects_multi_text(self):
        r = self.ev.evaluate(text=["a", "b"], metric="coherence", action="assert")
        self.assertEqual(r["status"], "error")
        self.assertIn("action=assert 时 text 必须是单个字符串", r["error"])

    def test_evaluate_skip_no_api_key(self):
        with mock.patch.object(LLMIntegration, "is_available", return_value=False):
            r = self.ev.evaluate(text="hi", metric="coherence")
        self.assertEqual(r["status"], "SKIP")
        self.assertEqual(r["verdict"], "SKIP")
        self.assertIn("硬阻断", r["reason"])

    def test_evaluate_missing_context(self):
        r = self.ev.evaluate(text="hi", metric="faithfulness", context=None)
        self.assertEqual(r["status"], "error")
        self.assertIn("需要提供 context", r["error"])
        # 不需要 context 的软指标不拦截
        r2 = self.ev.evaluate(text="hi", metric="coherence")
        self.assertEqual(r2["status"], "completed")

    # ── 单指标 ──
    def test_evaluate_single_metric_pass(self):
        r = self.ev.evaluate(text="产出物", metric="coherence", threshold=0.7)
        self.assertEqual(r["status"], "completed")
        self.assertEqual(r["metric"], "coherence")
        self.assertEqual(r["score"], 0.9)
        self.assertEqual(r["verdict"], "PASS")
        self.assertFalse(r["hard_failed"])
        self.assertIsInstance(r["breakdown"], dict)
        self.assertEqual(r["reason"], "结构清晰")

    def test_evaluate_single_metric_fail_soft(self):
        with mock.patch.object(self.ev, "_call_llm", return_value=json.dumps(
                {"score": 0.4, "reason": "离题", "breakdown": {}})):
            r = self.ev.evaluate(text="产出物", metric="coherence")
        self.assertEqual(r["verdict"], "FAIL")
        self.assertEqual(r["score"], 0.4)
        self.assertFalse(r["hard_failed"])  # 软指标失分不单独否决

    def test_evaluate_single_metric_hard_failed(self):
        with mock.patch.object(self.ev, "_call_llm", return_value=json.dumps(
                {"score": 0.5, "reason": "编造", "breakdown": {}})):
            r = self.ev.evaluate(text="产出物", metric="faithfulness", context="ctx")
        self.assertEqual(r["verdict"], "FAIL")
        self.assertTrue(r["hard_failed"])  # 硬指标挂 → 一票否决

    def test_compose_result_clamped_disclosed(self):
        with mock.patch.object(self.ev, "_call_llm", return_value=json.dumps(
                {"score": 1.5, "reason": "越界", "breakdown": {}})):
            r = self.ev.evaluate(text="产出物", metric="coherence")
        self.assertEqual(r["score"], 1.0)  # 夹取
        self.assertEqual(r["verdict"], "PASS")
        self.assertIn("score_clamped", r["breakdown"])
        self.assertEqual(r["breakdown"]["score_clamped"]["model_score"], 1.5)
        self.assertEqual(r["breakdown"]["score_clamped"]["clamped_to"], 1.0)

    # ── 多指标软硬聚合 ──
    def test_evaluate_multi_metric_hard_veto(self):
        def fake(messages):
            user = messages[1]["content"]
            if "忠实" in user:
                return json.dumps({"score": 0.4, "reason": "不忠实",
                                   "breakdown": {"claims_checked": 2, "claims_supported": 1,
                                                 "hallucinated": 1}})
            return json.dumps({"score": 0.9, "reason": "连贯", "breakdown": {}})

        with mock.patch.object(self.ev, "_call_llm", side_effect=fake):
            r = self.ev.evaluate(text="产出物", metric=["faithfulness", "coherence"],
                                 context="ctx")
        self.assertEqual(r["status"], "completed")
        self.assertEqual(r["metric"], ["faithfulness", "coherence"])
        self.assertEqual(r["score"], 0.65)  # (0.4+0.9)/2
        self.assertTrue(r["hard_failed"])
        self.assertEqual(r["verdict"], "FAIL")  # 硬指标一票否决

    def test_evaluate_multi_metric_soft_avg(self):
        def fake(messages):
            user = messages[1]["content"]
            if "相关程度" in user:
                return json.dumps({"score": 0.5, "reason": "部分切题",
                                   "breakdown": {"topic_coverage": "中", "off_topic": True}})
            return json.dumps({"score": 0.9, "reason": "连贯", "breakdown": {}})

        with mock.patch.object(self.ev, "_call_llm", side_effect=fake):
            r = self.ev.evaluate(text="产出物", metric=["answer_relevancy", "coherence"],
                                 context="参考主题")
        self.assertEqual(r["score"], 0.7)  # (0.5+0.9)/2 达标
        self.assertFalse(r["hard_failed"])
        self.assertEqual(r["verdict"], "PASS")  # 软指标失分被高分拉回

    # ── 降级护栏 ──
    def test_evaluate_hard_degraded_degrades_all(self):
        with mock.patch.object(self.ev, "_call_llm",
                               side_effect=lambda messages: "LLM调用错误: boom"):
            r = self.ev.evaluate(text="产出物", metric=["faithfulness", "coherence"],
                                 context="ctx")
        self.assertEqual(r["status"], "degraded")
        self.assertEqual(r["verdict"], "SKIP")
        self.assertFalse(r["hard_failed"])
        self.assertIn("faithfulness", r["degraded_metrics"])
        self.assertEqual(r["metric"], ["faithfulness", "coherence"])

    def test_evaluate_all_metrics_degraded(self):
        with mock.patch.object(self.ev, "_call_llm",
                               side_effect=lambda messages: "LLM调用错误: boom"):
            r = self.ev.evaluate(text="产出物", metric=["answer_relevancy", "coherence"],
                                 context="参考主题")
        self.assertEqual(r["status"], "degraded")
        self.assertEqual(r["verdict"], "SKIP")
        self.assertEqual(r["degraded_metrics"], ["answer_relevancy", "coherence"])

    def test_evaluate_soft_degraded_excluded_from_avg(self):
        def fake(messages):
            user = messages[1]["content"]
            if "结构" in user and "连贯" in user:
                return "LLM调用错误: 连不上"  # 软指标降级
            return json.dumps({"score": 0.9, "reason": "切题",
                               "breakdown": {"topic_coverage": "高", "off_topic": False}})

        with mock.patch.object(self.ev, "_call_llm", side_effect=fake):
            r = self.ev.evaluate(text="产出物", metric=["answer_relevancy", "coherence"],
                                 context="参考主题")
        self.assertEqual(r["status"], "completed")
        self.assertEqual(r["score"], 0.9)  # 降级软指标排除出平均分
        self.assertEqual(r["verdict"], "PASS")
        self.assertEqual(r["degraded_metrics"], ["coherence"])

    # ── 截断披露 / 重试→降级 / 异常包装 ──
    def test_evaluate_truncation_disclosed(self):
        long_text = "x" * (MAX_EVAL_TEXT_CHARS + 1000)
        long_ctx = "y" * (MAX_EVAL_CONTEXT_CHARS + 1000)
        r = self.ev.evaluate(text=long_text, metric="coherence", context=long_ctx)
        self.assertEqual(r["status"], "completed")
        self.assertEqual(r["truncated"]["text"]["original_chars"], len(long_text))
        self.assertEqual(r["truncated"]["text"]["evaluated_chars"], MAX_EVAL_TEXT_CHARS)
        self.assertEqual(r["truncated"]["context"]["original_chars"], len(long_ctx))
        self.assertEqual(r["truncated"]["context"]["evaluated_chars"], MAX_EVAL_CONTEXT_CHARS)

    def test_parse_retry_then_degraded(self):
        with mock.patch.object(self.ev, "_call_llm",
                               side_effect=["我先思考一下……", "还是没给出 JSON"]) as m:
            r = self.ev.evaluate(text="产出物", metric="coherence")
        self.assertEqual(m.call_count, 2)  # 首次 + 强制重试一次
        self.assertEqual(r["status"], "degraded")
        self.assertEqual(r["verdict"], "SKIP")
        self.assertIn("不包含合法 JSON", r["reason"])

    def test_call_llm_exception_wrapped(self):
        ev = OutputEvaluator()
        with mock.patch.object(ev.llm, "chat_completion",
                               side_effect=RuntimeError("boom")):
            out = ev._call_llm([{"role": "user", "content": "hi"}])
        self.assertEqual(out, "LLM调用错误: boom")

    # ── JSON 解析拒绝路径 ──
    def test_parse_eval_json_rejects(self):
        p = self.ev._parse_eval_json
        self.assertIsNone(p(None))
        self.assertIsNone(p(""))
        self.assertIsNone(p("LLM调用错误: x"))
        self.assertIsNone(p("不是 JSON 的散文"))
        self.assertIsNone(p('"just a string"'))       # 非 dict
        self.assertIsNone(p('{"score": true}'))        # bool 是 int 子类，拒绝
        self.assertIsNone(p('{"score": "high"}'))      # 非数值
        self.assertIsNone(p('{"score": NaN}'))         # 非有限值
        self.assertIsNone(p('{"score": Infinity}'))
        ok = p('{"score": 0.9, "reason": "r", "breakdown": {}}')
        self.assertEqual(ok["score"], 0.9)

    # ── score 批量报告 ──
    def test_build_score_report(self):
        def fake(messages):
            user = messages[1]["content"]
            if "item_alpha" in user:
                return json.dumps({"score": 0.9, "reason": "好",
                                   "breakdown": {"claims_checked": 3, "claims_supported": 3,
                                                 "hallucinated": 0}})
            return json.dumps({"score": 0.3, "reason": "差",
                               "breakdown": {"claims_checked": 3, "claims_supported": 1,
                                             "hallucinated": 2}})

        with mock.patch.object(self.ev, "_call_llm", side_effect=fake):
            r = self.ev.evaluate(text=["item_alpha", "item_beta"],
                                 metric="faithfulness", context="ctx", action="score")
        self.assertEqual(r["status"], "completed")
        self.assertEqual(r["action"], "score")
        self.assertEqual(r["n"], 2)
        self.assertEqual(r["avg_score"], 0.6)
        self.assertEqual(r["pass_count"], 1)
        self.assertEqual(r["hard_failed_count"], 1)
        self.assertEqual(len(r["items"]), 2)


# ═══════════════════════════════════════════════════════════════════
# 2. core/verify_findings.py —— 判定层纯函数
# ═══════════════════════════════════════════════════════════════════

class TestEvidenceLabeler(unittest.TestCase):
    """诚实话标签判定层：全部为纯函数，零 mock。"""

    def _decide(self, **kw):
        args = dict(has_ref=True, file_found=None, symbols_found=[],
                    symbols_missing=[], in_pipeline=None, entry=None, graph_exists=True)
        args.update(kw)
        return EvidenceLabeler.decide(**args)

    def test_decide_graph_missing(self):
        verdict, reason = self._decide(graph_exists=False)
        self.assertEqual(verdict, VERDICT_UNVERIFIABLE)
        self.assertIn("知识图谱不存在", reason)

    def test_decide_no_ref(self):
        verdict, _ = self._decide(has_ref=False)
        self.assertEqual(verdict, VERDICT_UNVERIFIABLE)

    def test_decide_file_refuted(self):
        verdict, reason = self._decide(file_found=False)
        self.assertEqual(verdict, VERDICT_REFUTED)
        self.assertIn("不存在", reason)

    def test_decide_symbols_confirmed(self):
        verdict, _ = self._decide(symbols_found=["a"], symbols_missing=[])
        self.assertEqual(verdict, VERDICT_CONFIRMED)

    def test_decide_symbols_partial(self):
        verdict, reason = self._decide(symbols_found=["a"], symbols_missing=["b"])
        self.assertEqual(verdict, VERDICT_PARTIAL)
        self.assertIn("1 个确证", reason)
        self.assertIn("1 个未找到", reason)

    def test_decide_symbols_missing_file_exists(self):
        verdict, reason = self._decide(symbols_found=[], symbols_missing=["a"],
                                       file_found=True)
        self.assertEqual(verdict, VERDICT_PARTIAL)
        self.assertIn("图谱可能不完整", reason)

    def test_decide_symbols_missing_unverifiable(self):
        verdict, _ = self._decide(symbols_found=[], symbols_missing=["a"], file_found=None)
        self.assertEqual(verdict, VERDICT_UNVERIFIABLE)

    def test_decide_file_only_not_confirmed(self):
        # 只有文件存在、无可核验符号：只能给 file_exists_only，不得标 confirmed
        verdict, reason = self._decide(symbols_found=[], symbols_missing=[],
                                       file_found=True)
        self.assertEqual(verdict, VERDICT_FILE_ONLY)
        self.assertIn("文件在项目中存在", reason)

    def test_decide_outside_pipeline(self):
        verdict, reason = self._decide(symbols_found=["a"], symbols_missing=[],
                                       in_pipeline=False, entry="mod.main")
        self.assertEqual(verdict, VERDICT_PARTIAL)
        self.assertIn("不在入口 'mod.main' 的静态调用管线内", reason)

    def test_decide_in_pipeline_confirmed(self):
        verdict, _ = self._decide(symbols_found=["a"], symbols_missing=[],
                                  in_pipeline=True)
        self.assertEqual(verdict, VERDICT_CONFIRMED)

    def test_make(self):
        out = EvidenceLabeler.make(VERDICT_CONFIRMED, "存在", {"symbols": []})
        self.assertEqual(out["verdict"], VERDICT_CONFIRMED)
        self.assertEqual(out["label_zh"], "确证")
        self.assertEqual(out["rank"], 3)
        self.assertEqual(out["reason"], "存在")
        # 未知 verdict 兜底为存疑、rank 0
        out2 = EvidenceLabeler.make("weird", "x", {})
        self.assertEqual(out2["label_zh"], "存疑")
        self.assertEqual(out2["rank"], 0)

    def test_has_verifiable_ref(self):
        self.assertFalse(EvidenceLabeler.has_verifiable_ref("", []))
        self.assertFalse(EvidenceLabeler.has_verifiable_ref("  ", None))
        self.assertTrue(EvidenceLabeler.has_verifiable_ref("a.py", []))
        self.assertTrue(EvidenceLabeler.has_verifiable_ref("", ["sym"]))


class TestSymbolExtraction(unittest.TestCase):
    """符号启发式提取：纯函数。"""

    def test_is_symbol_like(self):
        self.assertTrue(_is_symbol_like("snake_case"))       # 下划线
        self.assertTrue(_is_symbol_like("getUserById"))      # 驼峰
        self.assertTrue(_is_symbol_like("MAX_LIMIT"))        # 常量宏
        self.assertTrue(_is_symbol_like("HTTP"))             # 全大写宏
        self.assertFalse(_is_symbol_like("a"))               # 过短
        self.assertFalse(_is_symbol_like("the"))             # 停用词
        self.assertFalse(_is_symbol_like("def"))             # Python 关键字（小写命中）
        self.assertFalse(_is_symbol_like("class"))
        # 真实行为：关键字集合元素为大写（"True"），low 小写不命中，驼峰规则接管
        self.assertTrue(_is_symbol_like("True"))
        self.assertFalse(_is_symbol_like("ok"))              # 无特征短词

    def test_extract_symbols_priority(self):
        self.assertEqual(extract_symbols(""), [])
        self.assertEqual(extract_symbols(None), [])
        # 带括号调用
        self.assertEqual(extract_symbols("调用 process_data(x) 处理"), ["process_data"])
        # 普通标识符
        self.assertEqual(extract_symbols("变量 user_count 未使用"), ["user_count"])
        # 驼峰类名
        self.assertEqual(extract_symbols("模块 AppService 入口"), ["AppService"])
        # 去重
        self.assertEqual(extract_symbols("a_b a_b a_b"), ["a_b"])
        # 停用词全过滤 → 空
        self.assertEqual(extract_symbols("the function data"), [])

    def test_extract_symbols_limit(self):
        out = extract_symbols(" ".join(["sym_%d" % i for i in range(20)]), limit=12)
        self.assertEqual(len(out), 12)

    def test_extract_symbols_dotted_highest_priority(self):
        out = extract_symbols("请检查 module_a.func_b() 中的问题", limit=12)
        self.assertIn("module_a.func_b", out)


class TestFindingsVerifier(unittest.TestCase):
    """核验引擎：无图谱分支（db_path 指向不存在文件）+ 路径穿越 + 渲染。"""

    def setUp(self):
        self._tmp = tempfile.mkdtemp(prefix="coderef_vf_")
        self.missing_db = os.path.join(self._tmp, "kg_missing.db")

    def tearDown(self):
        shutil.rmtree(self._tmp, ignore_errors=True)

    def _fv(self):
        return FindingsVerifier(self._tmp, db_path=self.missing_db)

    def test_normalize_symbols_variants(self):
        n = FindingsVerifier._normalize_symbols
        self.assertEqual(n({"symbols": "a, b;c"}, "t", "d"), ["a", "b", "c"])
        self.assertEqual(n({"symbols": ["x", " y "]}, "t", "d"), ["x", "y"])
        self.assertEqual(n({"symbols": ["x", 123, None]}, "t", "d"), ["x"])
        self.assertEqual(n({"symbols": ("t",)}, "t", "d"), ["t"])
        # 非合法形态 → 回退文本启发式提取
        self.assertEqual(n({"symbols": 123}, "调用 process_data 处理", ""), ["process_data"])
        self.assertEqual(n({}, "调用 process_data 处理", ""), ["process_data"])
        self.assertEqual(n({"symbols": None}, "调用 process_data 处理", ""), ["process_data"])

    def test_verify_no_graph(self):
        out = self._fv().verify([{"title": "t1", "symbols": ["a"]}])
        self.assertFalse(out["ok"])
        self.assertFalse(out["graph_stats"]["has_kg"])
        self.assertEqual(out["results"], [])
        self.assertIn("知识图谱不存在", out["summary"])

    def test_verify_top_level_function(self):
        out = verify_findings(self._tmp, [{"title": "t"}], db_path=self.missing_db)
        self.assertFalse(out["ok"])
        self.assertFalse(out["graph_stats"]["has_kg"])

    def test_verify_one_no_graph_all_unverifiable(self):
        fv = self._fv()
        r = fv.verify_one({"title": "t", "file": "x.py", "symbols": ["a"]})
        self.assertEqual(r["verdict"], VERDICT_UNVERIFIABLE)
        self.assertFalse(r["evidence"]["graph_exists"])

    def test_verify_one_graph_exists_file_only_confirmed(self):
        with open(os.path.join(self._tmp, "exists.py"), "w", encoding="utf-8") as f:
            f.write("x = 1\n")
        fv = self._fv()
        fv.graph_exists = True  # 手动声明有图谱（kg 为 None，符号查不到）
        r = fv.verify_one({"title": "t", "file": "exists.py"})
        self.assertEqual(r["verdict"], VERDICT_FILE_ONLY)   # 仅文件存在
        self.assertTrue(r["evidence"]["file_found"])
        r2 = fv.verify_one({"title": "t", "file": "not_exist.py"})
        self.assertEqual(r2["verdict"], VERDICT_REFUTED)    # 文件证伪
        r3 = fv.verify_one({"title": "t", "symbols": ["definitely_not_a_sym"]})
        self.assertEqual(r3["verdict"], VERDICT_UNVERIFIABLE)  # 符号查不到且无文件

    def test_file_found_rejects_traversal(self):
        proj_sub = os.path.join(self._tmp, "sub")
        os.makedirs(proj_sub, exist_ok=True)
        inside = os.path.join(proj_sub, "inside.py")
        with open(inside, "w", encoding="utf-8") as f:
            f.write("x = 1\n")
        outside = os.path.join(self._tmp, "outside.txt")
        with open(outside, "w", encoding="utf-8") as f:
            f.write("x = 1\n")
        fv2 = FindingsVerifier(proj_sub, db_path=self.missing_db)
        # 项目内相对路径 → True
        self.assertTrue(fv2._file_found("inside.py"))
        # 项目外绝对路径 → False（不在项目根内）
        self.assertFalse(fv2._file_found(outside))
        # ../ 穿越到项目外 → False
        self.assertFalse(fv2._file_found("../outside.txt"))
        # 空串 → None
        self.assertIsNone(fv2._file_found(""))

    def test_file_found_kg_entity_fallback(self):
        fv = self._fv()
        fv.kg = mock.Mock()
        fv.kg.query_file_entities.return_value = mock.Mock(total=1)
        # 文件系统不存在但图谱有实体（相对路径）→ True
        self.assertTrue(fv._file_found("virtual_file.py"))
        # 文件系统不存在且图谱无实体 → False
        fv.kg.query_file_entities.return_value = mock.Mock(total=0)
        self.assertFalse(fv._file_found("ghost.py"))

    def test_verify_tally(self):
        fv = self._fv()
        fv.graph_exists = True

        def fake_probe(symbol, file_hint=""):
            return {"symbol": symbol, "found": symbol == "ok_sym",
                    "callers": 0, "callees": 0, "node": None}

        def fake_file(file):
            return {"bad.py": False, "ok.py": True}.get(file)

        with mock.patch.object(fv, "_symbol_probe", side_effect=fake_probe), \
             mock.patch.object(fv, "_file_found", side_effect=fake_file):
            out = fv.verify([
                {"title": "确证", "symbols": ["ok_sym"]},
                {"title": "证伪", "file": "bad.py"},
                {"title": "部分", "symbols": ["ok_sym", "no_sym"]},
                {"title": "存疑", "symbols": ["no_sym"]},
            ])
        self.assertTrue(out["ok"])
        self.assertTrue(out["graph_stats"]["has_kg"])
        self.assertEqual(out["count"], 4)
        self.assertEqual(out["tally"], {"confirmed": 1, "refuted": 1,
                                        "partially_confirmed": 1,
                                        "file_exists_only": 0,
                                        "unverifiable": 1})
        self.assertIn("共核验 4 条论断", out["summary"])
        verdicts = [r["verdict"] for r in out["results"]]
        self.assertEqual(verdicts, [VERDICT_CONFIRMED, VERDICT_REFUTED,
                                    VERDICT_PARTIAL, VERDICT_UNVERIFIABLE])

    def _full_result(self):
        return {
            "ok": True,
            "graph_stats": {"has_kg": True, "db": "d:/x/kg.db"},
            "count": 2,
            "tally": {"confirmed": 1, "refuted": 0,
                      "partially_confirmed": 1, "unverifiable": 0},
            "summary": "共核验 2 条论断：确证 1，证伪 0，部分确证 1，无法核验 0。",
            "results": [
                {"verdict": VERDICT_CONFIRMED, "label_zh": "确证", "reason": "存在",
                 "title": "t1", "file": "a.py", "line": 3,
                 "evidence": {"symbols": [{"symbol": "s1", "found": True}]}},
                {"verdict": VERDICT_PARTIAL, "label_zh": "部分确证", "reason": "部分",
                 "title": "t2", "file": None, "line": None,
                 "evidence": {"symbols": [{"symbol": "s2", "found": False}]}},
            ],
        }

    def test_render_report_no_graph(self):
        result = {"ok": False, "graph_stats": {"has_kg": False, "db": ""},
                  "summary": "知识图谱不存在，无法核验", "results": []}
        text = render_report(result)
        self.assertIn("论断核验报告", text)
        self.assertIn("知识图谱不存在", text)

    def test_render_report_with_results(self):
        text = render_report(self._full_result())
        self.assertIn("[OK] #1「t1」 确证", text)
        self.assertIn("[~] #2「t2」 部分确证", text)
        self.assertIn("确证符号: s1", text)
        self.assertIn("图例: [OK]=确证", text)

    def test_render_html(self):
        html = render_html(self._full_result())
        self.assertIn("<!DOCTYPE html>", html)
        self.assertIn("#1DC981", html)   # confirmed 徽标色
        self.assertIn("确证", html)
        self.assertIn("部分确证", html)
        html2 = render_html({"ok": False, "graph_stats": {"has_kg": False, "db": ""},
                             "summary": "知识图谱不存在", "results": []})
        self.assertIn("知识图谱不存在", html2)


# ═══════════════════════════════════════════════════════════════════
# 3. core/governance_store.py
# ═══════════════════════════════════════════════════════════════════

class TestGovernanceStore(unittest.TestCase):
    """治理运营库：临时 SQLite，tearDown 清理。"""

    def setUp(self):
        self._tmp = tempfile.mkdtemp(prefix="coderef_gov_")
        self.db_path = os.path.join(self._tmp, "sub", "gov.db")
        self.store = GovernanceStore(self._tmp, db_path=self.db_path)
        self._n = 0

    def tearDown(self):
        self.store.close()
        shutil.rmtree(self._tmp, ignore_errors=True)

    def _gap(self, gtype="missing", module=None, **kw):
        self._n += 1
        gap = {"type": gtype, "module": module or ("mod_%d" % self._n)}
        gap.update(kw)
        return gap

    def _upsert(self, **kw):
        iid, action = self.store.upsert_issue(self._gap(**kw))
        return iid, action

    def _archive(self, iid):
        self.store.transition(iid, "Confirmed")
        self.store.transition(iid, "Fixing")
        self.store.transition(iid, "Verified")
        ok, msg = self.store.transition(iid, "Archived")
        self.assertTrue(ok, msg)

    # ── 键与快照 ──
    def test_gap_key_variants(self):
        self.assertEqual(gap_key({"type": "cycle", "modules": ["b", "a", "c"]}),
                         "cycle:3:a")                      # 排序取最小成员
        self.assertEqual(gap_key({"type": "cycle"}), "cycle:0:?")
        self.assertEqual(gap_key({"type": "business_gap", "flow_id": "f1",
                                  "step_id": "s1"}), "business_gap:f1:s1")
        self.assertEqual(gap_key({"type": "dependency_violation",
                                  "from_module": "m1", "to_module": "m2"}),
                         "dependency_violation:m1->m2")
        self.assertEqual(gap_key({"type": "missing", "module": "mod"}), "missing:mod")
        self.assertEqual(gap_key({"type": "missing", "role_id": "r1"}), "missing:r1")
        self.assertEqual(gap_key({"type": "missing"}), "missing:?")
        # 反斜杠归一
        self.assertEqual(gap_key({"type": "cycle", "modules": ["a\\b"]}), "cycle:1:a/b")

    def test_gap_snapshot_norm(self):
        g = {"type": "missing", "module": "a/b", "detail": "x"}
        snap = gap_snapshot(g)
        self.assertEqual(json.loads(snap), g)  # sort_keys 序列化可逆

    def test_gov_db_path_default(self):
        self.assertEqual(gov_db_path(self._tmp),
                         os.path.join(os.path.abspath(self._tmp), ".coderef", "governance.db"))

    # ── 体检周期 ──
    def test_create_get_cycle(self):
        cyc = self.store.create_cycle("体检1", description="desc")
        self.assertTrue(cyc["id"].startswith("cyc_"))
        self.assertEqual(cyc["name"], "体检1")
        self.assertEqual(cyc["description"], "desc")
        self.assertEqual(cyc["status"], "open")
        self.assertTrue(cyc["start_date"])
        back = self.store.get_cycle(cyc["id"])
        self.assertEqual(back["name"], "体检1")
        self.assertIsNone(self.store.get_cycle("cyc_nope"))

    def test_open_close_list_cycles(self):
        c1 = self.store.create_cycle("c1")
        opened = self.store.open_cycle()          # 唯一 open → 返回它
        self.assertEqual(opened["id"], c1["id"])
        self.assertIsNone(self.store.close_cycle("cyc_nope"))
        closed = self.store.close_cycle(c1["id"], note="收尾")
        self.assertEqual(closed["status"], "closed")
        self.assertTrue(closed["end_date"])
        self.assertEqual(closed["note"], "收尾")
        c2 = self.store.create_cycle("c2")
        opened2 = self.store.open_cycle()         # c1 已 closed，只剩 c2 open
        self.assertEqual(opened2["id"], c2["id"])
        self.assertEqual(len(self.store.list_cycles()), 2)

    # ── 工作项导入 ──
    def test_upsert_issue_created(self):
        iid, action = self._upsert()
        self.assertEqual(action, "created")
        iss = self.store.get_issue(iid)
        self.assertEqual(iss["status"], "Detected")
        self.assertEqual(iss["gap_type"], "missing")
        events = self.store.issue_events(iid)
        self.assertEqual(events[0]["action"], "created")

    def test_upsert_issue_kept(self):
        iid, _ = self._upsert(module="same_mod")
        iid2, action = self.store.upsert_issue(self._gap(module="same_mod"))
        self.assertEqual(iid, iid2)
        self.assertEqual(action, "kept")
        self.assertEqual(self.store.get_issue(iid)["status"], "Detected")  # 状态不动

    def test_upsert_issue_recurred(self):
        iid, _ = self._upsert(module="re_mod")
        self._archive(iid)
        self.assertEqual(self.store.get_issue(iid)["status"], "Archived")
        iid2, action = self.store.upsert_issue(self._gap(module="re_mod"))
        self.assertEqual(action, "recurred")
        iss = self.store.get_issue(iid2)
        self.assertEqual(iss["status"], "Detected")
        self.assertEqual(iss["recurred"], 1)

    def test_upsert_issue_reactivate(self):
        iid, _ = self._upsert(module="react_mod")
        ok, msg = self.store.reject(iid, reason="wontfix")
        self.assertTrue(ok, msg)
        # 证据变化（severity 加入）→ 重新激活
        iid2, action = self.store.upsert_issue(
            self._gap(module="react_mod", severity="high"))
        self.assertEqual(action, "reactivate")
        self.assertEqual(self.store.get_issue(iid2)["status"], "Detected")

    def test_upsert_issue_skipped(self):
        iid, _ = self._upsert(module="skip_mod")
        self.store.reject(iid, reason="wontfix")
        iid2, action = self.store.upsert_issue(self._gap(module="skip_mod"))
        self.assertEqual(action, "skipped")
        self.assertEqual(self.store.get_issue(iid2)["status"], "Rejected")

    # ── 状态流转 ──
    def test_transition_valid(self):
        iid, _ = self._upsert()
        ok, msg = self.store.transition(iid, "Confirmed")
        self.assertTrue(ok)
        self.assertIn("Detected → Confirmed", msg)
        self.assertEqual(self.store.get_issue(iid)["status"], "Confirmed")

    def test_transition_illegal(self):
        iid, _ = self._upsert()
        ok, msg = self.store.transition(iid, "Archived")   # Detected 直归档
        self.assertFalse(ok)
        self.assertIn("非法流转", msg)
        ok2, msg2 = self.store.transition(iid, "Nope")      # 未知状态
        self.assertFalse(ok2)
        self.assertIn("未知状态", msg2)
        ok3, msg3 = self.store.transition("iss_nope", "Confirmed")  # 不存在
        self.assertFalse(ok3)
        self.assertIn("工作项不存在", msg3)
        self.store.transition(iid, "Confirmed")
        ok4, msg4 = self.store.transition(iid, "Confirmed")  # 同状态
        self.assertFalse(ok4)
        self.assertIn("已是 Confirmed", msg4)

    def test_transition_archived_requires_verified(self):
        # 非 Verified 直归档被拒（Detected → Archived 属非法流转路径）
        iid, _ = self._upsert()
        ok, msg = self.store.transition(iid, "Archived")
        self.assertFalse(ok)
        self.assertIn("非法流转", msg)

    def test_reject(self):
        iid, _ = self._upsert()
        ok, msg = self.store.reject(iid, reason="已人工确认无需改")
        self.assertTrue(ok)
        iss = self.store.get_issue(iid)
        self.assertEqual(iss["status"], "Rejected")
        self.assertEqual(iss["note"], "已人工确认无需改")
        ok2, msg2 = self.store.reject(iid, reason="again")   # 重复豁免
        self.assertFalse(ok2)
        self.assertIn("已是豁免状态", msg2)
        ok3, msg3 = self.store.reject("iss_nope")             # 不存在
        self.assertFalse(ok3)
        self.assertIn("工作项不存在", msg3)

    def test_set_issue_meta(self):
        iid, _ = self._upsert()
        ok, msg = self.store.set_issue_meta(iid, priority="high", assignee="alice",
                                            due_date="2026-10-01", note="n1")
        self.assertTrue(ok)
        iss = self.store.get_issue(iid)
        self.assertEqual(iss["priority"], "high")
        self.assertEqual(iss["assignee"], "alice")
        self.assertEqual(iss["due_date"], "2026-10-01")
        self.assertEqual(iss["note"], "n1")
        ok2, msg2 = self.store.set_issue_meta(iid)            # 全空
        self.assertTrue(ok2)
        self.assertEqual(msg2, "无字段更新")
        ok3, msg3 = self.store.set_issue_meta("iss_nope")     # 不存在
        self.assertFalse(ok3)

    # ── 查询视图 ──
    def test_list_issues_views(self):
        i_hi, _ = self._upsert(severity="high")
        i_as, _ = self._upsert()
        self.store.set_issue_meta(i_as, assignee="bob")
        i_od, _ = self._upsert()
        self.store.set_issue_meta(i_od, due_date="2000-01-01")
        i_ar, _ = self._upsert(module="ar_mod")
        self._archive(i_ar)
        i_rj, _ = self._upsert(module="rj_mod")
        self.store.reject(i_rj, reason="x")
        i_rc, _ = self._upsert(module="rc_mod")
        self._archive(i_rc)
        self.store.upsert_issue(self._gap(module="rc_mod"))   # 复发

        ids = lambda rows: {r["id"] for r in rows}
        self.assertIn(i_hi, ids(self.store.list_issues(view="high")))
        self.assertNotIn(i_ar, ids(self.store.list_issues(view="high")))
        self.assertIn(i_as, ids(self.store.list_issues(view="assigned")))
        self.assertIn(i_od, ids(self.store.list_issues(view="overdue")))
        self.assertIn(i_rj, ids(self.store.list_issues(view="rejected")))
        self.assertIn(i_ar, ids(self.store.list_issues(view="archived")))
        self.assertIn(i_rc, ids(self.store.list_issues(view="recurred")))
        # 默认 open：排除终态
        all_open = ids(self.store.list_issues())
        self.assertNotIn(i_ar, all_open)
        self.assertNotIn(i_rj, all_open)
        self.assertIn(i_hi, all_open)
        # limit 生效
        self.assertEqual(len(self.store.list_issues(limit=1)), 1)

    def test_issue_events(self):
        iid, _ = self._upsert(module="ev_mod")
        self.store.transition(iid, "Confirmed")
        events = self.store.issue_events(iid)
        self.assertEqual(len(events), 2)
        self.assertEqual(events[0]["action"], "Detected->Confirmed")  # 倒序
        self.assertEqual(len(self.store.issue_events(iid, limit=1)), 1)

    # ── 统计 ──
    def test_status_counts(self):
        counts = self.store.status_counts()
        self.assertEqual(counts, {s: 0 for s in ALL_STATUSES})
        iid, _ = self._upsert()
        self.store.transition(iid, "Confirmed")
        self._upsert(module="c2_mod")
        counts = self.store.status_counts()
        self.assertEqual(counts["Detected"], 1)
        self.assertEqual(counts["Confirmed"], 1)

    def test_cycle_summary(self):
        empty = self.store.cycle_summary("cyc_empty")
        self.assertEqual(empty["total"], 0)
        self.assertEqual(empty["completion_rate"], 0.0)
        self.assertEqual(empty["remaining"], 0)
        cyc = self.store.create_cycle("c1")
        iid1, _ = self.store.upsert_issue(self._gap(), cycle_id=cyc["id"])
        iid2, _ = self.store.upsert_issue(self._gap(), cycle_id=cyc["id"])
        iid3, _ = self.store.upsert_issue(self._gap(), cycle_id=cyc["id"])
        self._archive(iid1)
        self.store.reject(iid2, reason="x")
        s = self.store.cycle_summary(cyc["id"])
        self.assertEqual(s["total"], 3)
        self.assertEqual(s["done"], 1)
        self.assertEqual(s["rejected"], 1)
        self.assertEqual(s["remaining"], 1)
        self.assertEqual(s["completion_rate"], round(1 / 3, 2))
        # Verified 视为已达标
        self.store.transition(iid3, "Confirmed")
        self.store.transition(iid3, "Fixing")
        self.store.transition(iid3, "Verified")
        s2 = self.store.cycle_summary(cyc["id"])
        self.assertEqual(s2["done"], 2)

    def test_trend(self):
        c_open = self.store.create_cycle("open_c")
        c_closed = self.store.create_cycle("closed_c")
        self.store.upsert_issue(self._gap(), cycle_id=c_closed["id"])
        self.store.close_cycle(c_closed["id"])
        trend = self.store.trend()
        self.assertEqual(len(trend), 1)                    # 仅 closed 入趋势
        self.assertEqual(trend[0]["cycle_id"], c_closed["id"])
        self.assertIn("done", trend[0])
        self.assertIn("completion_rate", trend[0])
        self.assertNotIn(c_open["id"], [t["cycle_id"] for t in trend])


# ═══════════════════════════════════════════════════════════════════
# 4. core/llm_integration.py 纯函数
# ═══════════════════════════════════════════════════════════════════

class TestLLMJsonRepair(unittest.TestCase):
    """JSON 修复 / 解析纯函数（无需客户端）。"""

    def test_complete_bare_token(self):
        self.assertEqual(_complete_bare_token("tr"), "true")
        self.assertEqual(_complete_bare_token("tru"), "true")
        self.assertEqual(_complete_bare_token("true"), "true")
        self.assertEqual(_complete_bare_token("fa"), "false")
        self.assertEqual(_complete_bare_token("fals"), "false")
        self.assertEqual(_complete_bare_token("nu"), "null")
        self.assertEqual(_complete_bare_token("nul"), "null")
        self.assertEqual(_complete_bare_token("3.14"), "3.14")   # 数字原样
        self.assertEqual(_complete_bare_token("-2e5"), "-2e5")
        self.assertEqual(_complete_bare_token("abc"), "abc")     # 无匹配原样

    def test_repair_truncated_string(self):
        out = _repair_truncated_json('{"a": "unfin')
        self.assertEqual(out, '{"a": "unfin"}')
        self.assertEqual(json.loads(out)["a"], "unfin")

    def test_repair_truncated_bare_token(self):
        out = _repair_truncated_json('{"verified": tr')
        self.assertEqual(out, '{"verified": true}')
        self.assertTrue(json.loads(out)["verified"])

    def test_repair_unclosed_braces(self):
        out = _repair_truncated_json('[{"x":1')
        self.assertEqual(out, '[{"x":1}]')
        self.assertEqual(json.loads(out), [{"x": 1}])

    def test_repair_no_struct_start(self):
        self.assertEqual(_repair_truncated_json(""), "")
        self.assertEqual(_repair_truncated_json("hello world"), "hello world")
        self.assertEqual(_repair_truncated_json('"a": 1'), '"a": 1')

    def test_repair_drops_prefix_junk(self):
        out = _repair_truncated_json('Note: 结果如下 {"a": 1')
        self.assertEqual(out, '{"a": 1}')

    def test_normalize_single_quotes(self):
        self.assertEqual(_normalize_nonstandard_json("{'a': 'x'}"), '{"a": "x"}')
        self.assertEqual(_normalize_nonstandard_json("{'a': 'it\\'s'}"), '{"a": "it\'s"}')
        # 单引号字符串内部的双引号被转义
        self.assertEqual(_normalize_nonstandard_json("{'a': 'say \"hi\"'}"),
                         '{"a": "say \\"hi\\""}')
        # 独立双引号字符串原样复制
        self.assertEqual(_normalize_nonstandard_json("{'a': \"b\"}"), '{"a": "b"}')

    def test_normalize_python_literals(self):
        self.assertEqual(_normalize_nonstandard_json("[True, False, None]"),
                         "[true, false, null]")
        self.assertEqual(_normalize_nonstandard_json('{"ok": True}'), '{"ok": true}')

    def test_normalize_no_change(self):
        text = '{"a": "b"}'
        self.assertEqual(_normalize_nonstandard_json(text), text)

    def test_parse_json_response_variants(self):
        p = LLMIntegration.parse_json_response
        self.assertEqual(p('{"a": 1}'), {"a": 1})
        self.assertEqual(p('```json\n{"a": 1}\n```'), {"a": 1})     # 代码块包裹
        self.assertEqual(p('```\n[1, 2]\n```'), [1, 2])
        self.assertEqual(p('{"score": 0.9, "reason": "ok'),          # 截断修复
                         {"score": 0.9, "reason": "ok"})
        self.assertEqual(p("{'score': 0.9}"), {"score": 0.9})        # 单引号
        self.assertEqual(p('前面有文字 {"a": 1} 后面也有'), {"a": 1})  # 平衡片段
        self.assertEqual(p('Here is the list: [1, 2, 3] 结尾'), [1, 2, 3])
        self.assertEqual(p('{"a": tr'), {"a": True})                 # 截断裸 token
        self.assertIsNone(p("无法解析的内容"))
        self.assertIsNone(p(""))
        self.assertIsNone(p(None))


class TestLLMConfigLoading(unittest.TestCase):
    """配置加载纯函数 + 成本封顶（全部 mock，零网络）。"""

    def test_safe_float_int(self):
        self.assertEqual(_safe_float(None, 0.7), 0.7)
        self.assertEqual(_safe_float("", 0.7), 0.7)
        self.assertEqual(_safe_float("abc", 0.7), 0.7)
        self.assertEqual(_safe_float("0.5", 0.7), 0.5)
        self.assertEqual(_safe_int(None, 5), 5)
        self.assertEqual(_safe_int("", 5), 5)
        self.assertEqual(_safe_int("x", 5), 5)
        self.assertEqual(_safe_int("10", 5), 10)
        self.assertEqual(_safe_int(3.7, 5), 3)

    def test_load_config_from_env(self):
        with mock.patch.dict(os.environ, {
            "CODEREF_API_KEY": "sk-env-123",
            "CODEREF_PROVIDER": "openai",
            "CODEREF_BASE_URL": "https://env.example",
            "CODEREF_MODEL": "env-model",
            "CODEREF_TEMPERATURE": "0.3",
            "CODEREF_MAX_TOKENS": "1024",
        }, clear=True):
            cfg = _load_llm_config_from_settings()
        self.assertEqual(cfg.provider, LLMProvider.OPENAI)
        self.assertEqual(cfg.api_key, "sk-env-123")
        self.assertEqual(cfg.base_url, "https://env.example")
        self.assertEqual(cfg.model, "env-model")
        self.assertEqual(cfg.temperature, 0.3)
        self.assertEqual(cfg.max_tokens, 1024)

    def test_load_config_env_bad_numbers_and_provider(self):
        with mock.patch.dict(os.environ, {
            "CODEREF_API_KEY": "sk-env-123",
            "CODEREF_PROVIDER": "unknown",
            "CODEREF_TEMPERATURE": "abc",
            "CODEREF_MAX_TOKENS": "xyz",
        }, clear=True):
            cfg = _load_llm_config_from_settings()
        self.assertEqual(cfg.provider, LLMProvider.DEEPSEEK)  # 未知名回退
        self.assertEqual(cfg.temperature, 0.7)                # 非法回退默认
        self.assertEqual(cfg.max_tokens, 4096)

    def test_load_config_from_config_json(self):
        cfg_json = {"llm_api_key": "sk-cfg-key-123", "llm_provider": "custom",
                    "llm_base_url": "https://cfg.example", "llm_model": "cfg-model",
                    "llm_temperature": 0.4, "llm_max_tokens": 1500}
        with mock.patch.dict(os.environ, {}, clear=True), \
             mock.patch("core.llm_integration.os.path.exists", return_value=True), \
             mock.patch("builtins.open", mock.mock_open(read_data=json.dumps(cfg_json))):
            cfg = _load_llm_config_from_settings()
        self.assertEqual(cfg.provider, LLMProvider.CUSTOM)
        self.assertEqual(cfg.api_key, "sk-cfg-key-123")
        self.assertEqual(cfg.base_url, "https://cfg.example")
        self.assertEqual(cfg.model, "cfg-model")
        self.assertEqual(cfg.temperature, 0.4)
        self.assertEqual(cfg.max_tokens, 1500)

    def test_load_config_config_json_placeholder_skipped(self):
        # api_key 为占位符 → 跳过 config.json，落到默认
        with mock.patch.dict(os.environ, {}, clear=True), \
             mock.patch("core.llm_integration.os.path.exists", return_value=True), \
             mock.patch("builtins.open",
                        mock.mock_open(read_data=json.dumps({"llm_api_key": "ollama"}))):
            cfg = _load_llm_config_from_settings()
        self.assertEqual(cfg.api_key, "")
        self.assertEqual(cfg.provider, LLMProvider.DEEPSEEK)

    def test_load_config_defaults(self):
        with mock.patch.dict(os.environ, {}, clear=True), \
             mock.patch("core.llm_integration.os.path.exists", return_value=False):
            cfg = _load_llm_config_from_settings()
        self.assertEqual(cfg.provider, LLMProvider.DEEPSEEK)
        self.assertEqual(cfg.api_key, "")
        self.assertEqual(cfg.base_url, "https://api.deepseek.com")
        self.assertEqual(cfg.model, "deepseek-flash")

    def test_validate_analysis_dict(self):
        v = LLMIntegration._validate_analysis_dict
        self.assertTrue(v({"file_purpose": "p", "key_functions": []}))
        self.assertFalse(v({"file_purpose": "p"}))           # 缺 key_functions
        self.assertFalse(v({"file_purpose": 1, "key_functions": []}))
        self.assertFalse(v({"file_purpose": "p", "key_functions": "x"}))
        self.assertFalse(v("str"))
        self.assertFalse(v(None))

    def test_validate_reference_points(self):
        v = LLMIntegration._validate_reference_points
        self.assertTrue(v([]))
        self.assertTrue(v([{"title": "t", "description": "d"}]))
        self.assertFalse(v([{"title": "t"}]))                # 缺 description
        self.assertFalse(v([{"title": 1, "description": "d"}]))
        self.assertFalse(v([{"title": "t", "description": "d"}, "bad"]))
        self.assertFalse(v("x"))
        self.assertFalse(v(None))

    def _inst(self, api_key, provider=LLMProvider.DEEPSEEK):
        with mock.patch.object(LLMIntegration, "_init_client"):
            inst = LLMIntegration(LLMConfig(provider=provider, api_key=api_key))
        return inst

    def test_cap_output_budget(self):
        inst = self._inst("sk-ok-123")
        self.assertFalse(inst.last_output_truncated())
        short = inst._cap_output("hello")
        self.assertEqual(short, "hello")
        self.assertFalse(inst.last_output_truncated())
        long_text = "x" * (inst.output_cap_chars + 50)
        capped = inst._cap_output(long_text)
        self.assertTrue(inst.last_output_truncated())
        self.assertIn("<!-- truncated", capped)
        self.assertLess(len(capped), len(long_text))
        self.assertEqual(inst.budget_remaining(), inst.call_budget)
        inst.reset_budget()
        self.assertEqual(inst._call_count, 0)

    def test_chat_completion_budget_exhausted(self):
        inst = self._inst("sk-ok-123")
        inst._call_count = inst.call_budget
        out = inst.chat_completion([{"role": "user", "content": "hi"}])
        self.assertTrue(LLMIntegration.is_error_response(out))
        self.assertIn("调用预算上限", out)

    def test_chat_completion_no_api_key(self):
        inst = self._inst("")
        out = inst.chat_completion([{"role": "user", "content": "hi"}])
        self.assertTrue(LLMIntegration.is_error_response(out))
        self.assertIn("未设置API Key", out)

    def test_chat_completion_placeholder_key(self):
        inst = self._inst("sk-xxx")
        inst.client = mock.Mock()
        out = inst.chat_completion([{"role": "user", "content": "hi"}])
        self.assertTrue(LLMIntegration.is_error_response(out))
        inst.client.chat.completions.create.assert_not_called()


if __name__ == "__main__":
    unittest.main(verbosity=2)
