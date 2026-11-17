# -*- coding: utf-8 -*-
"""5.20.0 梦（Dream）的单元测试。

覆盖：
- seed / status / read 的基本契约；
- 无 Key → SKIP（不写 DREAM.md、不编造）；
- LLM 提议 + 确定性核验：verified / hypothesis / rejected 三态；
- DREAM.md 的 800 token 预算与五节结构；
- TTL 衰减、去重、memory_digest 梦优先、Server 工具入口。

运行方式：
    python -m unittest tests.test_dream
"""
import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from unittest import mock

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from core import dream


def _cfg(**over):
    cfg = dict(dream.DEFAULT_CONFIG)
    cfg.update({
        "enabled": True, "run_on_startup": True, "max_notes": 40,
        "ttl_days": 30, "min_interval_hours": 0, "config_source": "test",
    })
    cfg.update(over)
    return cfg


class _FakeLLM:
    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    def is_available(self):
        return True

    def chat_completion(self, messages, **kwargs):
        self.calls.append(messages)
        return (self.payload if isinstance(self.payload, str)
                else json.dumps(self.payload, ensure_ascii=False))


class DreamTest(unittest.TestCase):
    def setUp(self):
        with dream._start_lock:
            dream._started.clear()

    def _project(self, tmp):
        return tmp

    def _seed(self, project, text="改了代码没跑 change_guard", kind="pitfall"):
        res = dream.seed(project, text, kind=kind, source="test")
        self.assertEqual(res["status"], "ok")
        return res["seed"]["id"]

    # 1. seed → status 能看到待消化数量，但还没有梦
    def test_seed_and_status(self):
        with tempfile.TemporaryDirectory() as project, \
                mock.patch.object(dream, "load_config", return_value=_cfg()), \
                mock.patch.object(dream, "_build_llm", return_value=None):
            seed_id = self._seed(project)
            self.assertTrue(seed_id.startswith("seed-"))
            st = dream.status(project)
            self.assertEqual(st["status"], "ok")
            self.assertEqual(st["seeds"]["total"], 1)
            self.assertEqual(st["seeds"]["pending"], 1)
            self.assertFalse(st["dream_md"]["exists"])
            self.assertTrue(st["due"])

    # 2. 无 Key / 未配置 → SKIP，不写 DREAM.md
    def test_run_skips_without_key(self):
        with tempfile.TemporaryDirectory() as project, \
                mock.patch.object(dream, "load_config", return_value=_cfg()), \
                mock.patch.object(dream, "_build_llm", return_value=None):
            self._seed(project)
            res = dream.run(project, trigger="manual")
            self.assertEqual(res["status"], "SKIP")
            self.assertIn("API Key", res["reason"])
            self.assertFalse(os.path.exists(dream._path(project, dream.DREAM_MD)))
            self.assertEqual(dream._load_state(project)["last_dream_at"], "")
            self.assertEqual(dream._load_state(project)["last_status"], "SKIP")

    # 3. 证据可核验 → verified，写进 DREAM.md 的「已验证规则」
    def test_run_verified_note_written_to_md(self):
        with tempfile.TemporaryDirectory() as project, \
                mock.patch.object(dream, "load_config", return_value=_cfg()):
            seed_id = self._seed(project)
            payload = {
                "goal": "把 5.20.0 的梦做完",
                "notes": [{
                    "kind": "rule", "text": "改完代码先跑 change_guard 再提交",
                    "evidence": [f"seed:{seed_id}"], "confidence": 0.9,
                }],
                "open_questions": ["梦的模型选型待定"],
            }
            res = dream.run(project, trigger="manual", llm=_FakeLLM(payload))
            self.assertEqual(res["status"], "ok")
            self.assertEqual(res["notes"]["verified"], 1)
            md = open(dream._path(project, dream.DREAM_MD),
                      encoding="utf-8").read()
            self.assertIn("## 已验证规则", md)
            self.assertIn("改完代码先跑 change_guard 再提交", md)
            self.assertIn("## 未决问题", md)
            self.assertIn("梦是假设", md)
            notes = dream._load_notes(project)
            self.assertEqual(notes[0]["status"], "verified")

    # 4. 证据核验失败 → rejected，不进入 DREAM.md
    def test_run_rejects_bad_evidence(self):
        with tempfile.TemporaryDirectory() as project, \
                mock.patch.object(dream, "load_config", return_value=_cfg()):
            self._seed(project)
            payload = {"notes": [{
                "kind": "pitfall", "text": "这条引用不存在",
                "evidence": ["file:not-exists-xyz.txt"], "confidence": 0.9,
            }]}
            res = dream.run(project, trigger="manual", llm=_FakeLLM(payload))
            self.assertEqual(res["status"], "ok")
            self.assertEqual(res["notes"]["verified"], 0)
            self.assertEqual(res["notes"]["rejected"], 1)
            md = open(dream._path(project, dream.DREAM_MD),
                      encoding="utf-8").read()
            self.assertNotIn("这条引用不存在", md)
            self.assertEqual(dream._load_notes(project)[0]["status"], "rejected")

    # 5. 无 evidence → hypothesis，只在「未决问题」标注为假设
    def test_run_hypothesis_not_in_verified_rules(self):
        with tempfile.TemporaryDirectory() as project, \
                mock.patch.object(dream, "load_config", return_value=_cfg()):
            self._seed(project)
            payload = {"notes": [{
                "kind": "association", "text": "当 X 出现时先想到 Y",
                "evidence": [], "confidence": 0.4,
            }]}
            res = dream.run(project, trigger="manual", llm=_FakeLLM(payload))
            self.assertEqual(res["notes"]["verified"], 0)
            md = open(dream._path(project, dream.DREAM_MD),
                      encoding="utf-8").read()
            rules = md.split("## 已验证规则", 1)[1].split("## 坑", 1)[0]
            self.assertNotIn("当 X 出现时先想到 Y", rules)
            self.assertIn("（假设，未验证）当 X 出现时先想到 Y", md)

    # 6. DREAM.md 不超过 800 token 预算（约 2400 字符）
    def test_dream_md_budget(self):
        with tempfile.TemporaryDirectory() as project, \
                mock.patch.object(dream, "load_config", return_value=_cfg()):
            seed_id = self._seed(project)
            notes = [{
                "kind": "rule", "text": f"规则{i}-" + "很长的经验" * 50,
                "evidence": [f"seed:{seed_id}"], "confidence": 0.8,
            } for i in range(8)]
            res = dream.run(project, trigger="manual", llm=_FakeLLM({
                "notes": notes,
                "open_questions": ["待定问题" * 50 for _ in range(5)],
            }))
            self.assertEqual(res["status"], "ok")
            md = open(dream._path(project, dream.DREAM_MD),
                      encoding="utf-8").read()
            self.assertLessEqual(len(md), dream.DREAM_MD_MAX_CHARS + 80)
            self.assertIn("truncated", md)

    # 7. TTL：过期未命中的笔记下沉到 archive
    def test_ttl_decay(self):
        with tempfile.TemporaryDirectory() as project, \
                mock.patch.object(dream, "load_config", return_value=_cfg()):
            seed_id = self._seed(project)
            old = {
                "id": "dream-old", "kind": "rule", "text": "旧规则",
                "evidence": [f"seed:{seed_id}"], "confidence": 0.9,
                "status": "verified", "created_at":
                    (datetime.now() - timedelta(days=40)).isoformat(timespec="seconds"),
                "last_hit_at": "", "ttl_days": 30,
            }
            dream._save_notes(project, [old])
            payload = {"notes": [{
                "kind": "pitfall", "text": "新坑", "evidence": [f"seed:{seed_id}"],
                "confidence": 0.8,
            }]}
            res = dream.run(project, trigger="manual", llm=_FakeLLM(payload))
            self.assertEqual(res["notes"]["decayed"], 1)
            texts = [n.get("text") for n in dream._load_notes(project)]
            self.assertNotIn("旧规则", texts)
            archive = dream._path(project, dream.ARCHIVE_DIR)
            self.assertTrue(os.listdir(archive))

    # 8. 同一文本去重，不重复堆笔记
    def test_dedup_by_text(self):
        with tempfile.TemporaryDirectory() as project, \
                mock.patch.object(dream, "load_config", return_value=_cfg()):
            seed_id = self._seed(project)
            payload = {"notes": [{
                "kind": "rule", "text": "先 recover 再做结论",
                "evidence": [f"seed:{seed_id}"], "confidence": 0.8,
            }]}
            dream.run(project, "manual", llm=_FakeLLM(payload))
            self._seed(project, "第二件显著事件", kind="pitfall")
            res = dream.run(project, "manual", llm=_FakeLLM(payload))
            self.assertEqual(res["notes"]["added"], 0)
            self.assertEqual(len(dream._load_notes(project)), 1)

    # 9. memory_digest 梦优先；操作记忆不可用时仍能注入梦
    def test_memory_digest_dream_first(self):
        from core import memory_digest
        with tempfile.TemporaryDirectory() as project, \
                mock.patch.object(dream, "load_config", return_value=_cfg()), \
                mock.patch.object(memory_digest.operation_memory, "recover",
                                  return_value={"status": "error",
                                                "message": "操作记忆尚未同步"}):
            seed_id = self._seed(project)
            payload = {"notes": [{
                "kind": "rule", "text": "提交前先跑 change_guard",
                "evidence": [f"seed:{seed_id}"], "confidence": 0.9,
            }]}
            dream.run(project, "manual", llm=_FakeLLM(payload))
            digest = memory_digest.build_digest(project)
            self.assertTrue(digest["available"])
            self.assertEqual(digest["recovered_from"], "dream")
            self.assertTrue(digest["items"][0]["kind"].startswith("梦"))
            self.assertIn("提交前先跑 change_guard", digest["items"][0]["text"])
            self.assertTrue(digest["dream"]["available"])
            self.assertEqual(digest["operation_memory_error"], "操作记忆尚未同步")

    # 10. Server / handlers 入口：status / seed / read
    def test_tool_handler_entry(self):
        from core.handlers_misc import _dream
        with tempfile.TemporaryDirectory() as project, \
                mock.patch.object(dream, "load_config", return_value=_cfg()), \
                mock.patch.object(dream, "_build_llm", return_value=None):
            st = json.loads(_dream({"project_path": project, "action": "status"}))
            self.assertEqual(st["tool"], "coderef_dream")
            self.assertEqual(st["status"], "ok")
            seeded = json.loads(_dream({
                "project_path": project, "action": "seed",
                "text": "主 AI 显式投喂", "kind": "pitfall"}))
            self.assertEqual(seeded["status"], "ok")
            read = json.loads(_dream({"project_path": project, "action": "read"}))
            self.assertEqual(read["status"], "ok")

    # 11. 复用主 LLM：不再读取 dream 自己的 api_key/provider/model
    def test_run_uses_main_llm_config(self):
        with tempfile.TemporaryDirectory() as project, \
                mock.patch.object(dream, "load_config", return_value=_cfg()), \
                mock.patch.object(dream, "_build_llm",
                                  return_value=_FakeLLM({"notes": []})) as build:
            seed_id = self._seed(project)
            self.assertTrue(seed_id)
            res = dream.run(project, trigger="manual")
            self.assertEqual(res["status"], "ok")
            build.assert_called_once_with()
            self.assertEqual(res["llm"]["source"], "主 LLM 配置")

    # 12. 后台化决策：默认 status 同步，只有 run 后台
    def test_background_decision(self):
        from core.mcp_server import Server
        s = Server()
        self.assertFalse(s._should_background("coderef_dream", {}))
        self.assertFalse(s._should_background("coderef_dream", {"action": "status"}))
        self.assertFalse(s._should_background("coderef_dream", {"action": "read"}))
        self.assertTrue(s._should_background("coderef_dream", {"action": "run"}))

    # 13. 启动补梦每进程每项目只触发一次
    def test_trigger_if_due_once(self):
        with tempfile.TemporaryDirectory() as project, \
                mock.patch.object(dream, "load_config", return_value=_cfg()), \
                mock.patch.object(dream, "_llm_available", return_value=True), \
                mock.patch.object(dream, "_build_llm", return_value=object()), \
                mock.patch.object(dream, "_safe_run") as safe_run:
            self.assertTrue(dream.trigger_if_due(project))
            self.assertFalse(dream.trigger_if_due(project))
            # 线程是后台的，等待其执行（最多 1 秒）
            for _ in range(20):
                if safe_run.called:
                    break
                import time
                time.sleep(0.05)
            self.assertTrue(safe_run.called)

    # 14. job 缺省仍是 pitfall（提示词与笔记 job 均保持旧行为）
    def test_default_job_is_pitfall(self):
        with tempfile.TemporaryDirectory() as project, \
                mock.patch.object(dream, "load_config", return_value=_cfg()):
            seed_id = self._seed(project)
            llm = _FakeLLM({"notes": [{
                "kind": "rule", "text": "先看 operation_memory",
                "evidence": [f"seed:{seed_id}"], "confidence": 0.8,
            }]})
            res = dream.run(project, trigger="manual", llm=llm)
            self.assertEqual(res["job"], "pitfall")
            self.assertEqual(dream._load_notes(project)[0]["job"], "pitfall")
            self.assertEqual(dream._load_notes(project)[0]["claim_type"], "fact")
            prompt = llm.calls[0][1]["content"]
            self.assertNotIn("本次 job=", prompt)
            self.assertIn("kind", prompt)
            # 默认 job 提示词直接拼接、不经 .format()，JSON 示例必须是单花括号
            self.assertNotIn("{{", prompt)
            self.assertNotIn("}}", prompt)
            self.assertIn("\"notes\": [", prompt)

    # 15. recap 的事实写入「当前目标」，不进「已验证规则」
    def test_recap_fact_goes_to_current_goal(self):
        with tempfile.TemporaryDirectory() as project, \
                mock.patch.object(dream, "load_config", return_value=_cfg()):
            seed_id = self._seed(project)
            payload = {
                "goal": "完成 5.21.0 梦 job 契约",
                "notes": [{
                    "claim_type": "fact", "kind": "context",
                    "text": "上次停在写 triage 测试",
                    "evidence": [f"seed:{seed_id}"], "confidence": 0.8,
                }],
                "open_questions": ["triage 排序是否需要人工校准"],
            }
            res = dream.run(project, trigger="manual", job="recap",
                            llm=_FakeLLM(payload))
            self.assertEqual(res["status"], "ok")
            self.assertEqual(res["job"], "recap")
            md = open(dream._path(project, dream.DREAM_MD),
                      encoding="utf-8").read()
            target = md.split("## 当前目标", 1)[1].split("## 本次开工建议", 1)[0]
            self.assertIn("上次停在写 triage 测试", target)
            rules = md.split("## 已验证规则", 1)[1].split("## 坑", 1)[0]
            self.assertNotIn("上次停在写 triage 测试", rules)
            note = dream._load_notes(project)[0]
            self.assertEqual(note["job"], "recap")
            self.assertEqual(note["claim_type"], "fact")
            self.assertEqual(note["status"], "verified")

    # 16. triage 的 advice 进「本次开工建议」且永远是 hypothesis
    def test_triage_advice_is_hypothesis(self):
        from core import memory_digest
        with tempfile.TemporaryDirectory() as project:
            with open(os.path.join(project, "a.py"), "w", encoding="utf-8") as f:
                f.write("x = 1\n")
            with mock.patch.object(dream, "load_config", return_value=_cfg()):
                self._seed(project)
                notes = [{
                    "claim_type": "advice", "kind": "association",
                    "text": f"先看 a.py，因为第{i}处被阻断",
                    "evidence": ["file:a.py"], "confidence": 0.6,
                } for i in range(4)]
                res = dream.run(project, trigger="manual", job="triage",
                                llm=_FakeLLM({"notes": notes}))
                self.assertEqual(res["status"], "ok")
                self.assertEqual(res["notes"]["verified"], 0)
                saved = dream._load_notes(project)
                self.assertEqual(len(saved), 3)  # triage 最多 3 条
                self.assertTrue(all(n["status"] == "hypothesis" for n in saved))
                self.assertTrue(all(n["claim_type"] == "advice" for n in saved))
                md = open(dream._path(project, dream.DREAM_MD),
                          encoding="utf-8").read()
                advice = md.split("## 本次开工建议", 1)[1].split(
                    "## 已验证规则", 1)[0]
                self.assertIn("先看 a.py，因为第0处被阻断", advice)
                self.assertIn("（假设）", advice)
                rules = md.split("## 已验证规则", 1)[1].split("## 坑", 1)[0]
                self.assertNotIn("先看 a.py", rules)
            with mock.patch.object(memory_digest.operation_memory, "recover",
                                   return_value={"status": "error",
                                                 "message": "操作记忆尚未同步"}):
                digest = memory_digest.build_digest(project)
            self.assertEqual(digest["items"][0]["kind"], "梦·开工建议")
            self.assertIn("先看 a.py", digest["items"][0]["text"])

    # 17. routing 的 advice 进「联想」并标（假设），不 verified
    def test_routing_advice_in_association(self):
        with tempfile.TemporaryDirectory() as project, \
                mock.patch.object(dream, "load_config", return_value=_cfg()):
            seed_id = self._seed(project)
            payload = {"notes": [{
                "claim_type": "advice", "kind": "association",
                "text": "当测试失败，先用 coderef_audit 复核",
                "evidence": [f"seed:{seed_id}"], "confidence": 0.7,
            }]}
            res = dream.run(project, trigger="manual", job="routing",
                            llm=_FakeLLM(payload))
            self.assertEqual(res["notes"]["verified"], 0)
            note = dream._load_notes(project)[0]
            self.assertEqual(note["job"], "routing")
            self.assertEqual(note["claim_type"], "advice")
            self.assertEqual(note["status"], "hypothesis")
            md = open(dream._path(project, dream.DREAM_MD),
                      encoding="utf-8").read()
            assoc = md.split("## 联想（当 X，先想到 Y）", 1)[1].split(
                "## 未决问题", 1)[0]
            self.assertIn("当测试失败，先用 coderef_audit 复核", assoc)
            self.assertIn("（假设）", assoc)
            rules = md.split("## 已验证规则", 1)[1].split("## 坑", 1)[0]
            self.assertNotIn("当测试失败", rules)

    # 18. advice + 坏 evidence → rejected
    def test_advice_with_bad_evidence_rejected(self):
        with tempfile.TemporaryDirectory() as project, \
                mock.patch.object(dream, "load_config", return_value=_cfg()):
            self._seed(project)
            payload = {"notes": [{
                "claim_type": "advice", "kind": "association",
                "text": "先看 ghost.py，因为不存在",
                "evidence": ["file:ghost.py"], "confidence": 0.9,
            }]}
            res = dream.run(project, trigger="manual", job="triage",
                            llm=_FakeLLM(payload))
            self.assertEqual(res["notes"]["rejected"], 1)
            self.assertEqual(dream._load_notes(project)[0]["status"], "rejected")
            md = open(dream._path(project, dream.DREAM_MD),
                      encoding="utf-8").read()
            self.assertNotIn("先看 ghost.py", md)

    # 19. 非法 job → error，不静默回退
    def test_invalid_job_returns_error(self):
        with tempfile.TemporaryDirectory() as project, \
                mock.patch.object(dream, "load_config", return_value=_cfg()):
            res = dream.run(project, trigger="manual",
                            job="bogus_job", llm=_FakeLLM({"notes": []}))
            self.assertEqual(res["status"], "error")
            self.assertIn("未知 job", res["error"])
            self.assertIn("change_narrative", res["error"])
            self.assertIn("review_draft", res["error"])
            self.assertFalse(os.path.exists(dream._path(project, dream.NOTES_FILE)))

    # 20. read 支持按 job 过滤；status 报告 jobs / last_job / by_job
    def test_read_filter_by_job_and_status(self):
        with tempfile.TemporaryDirectory() as project, \
                mock.patch.object(dream, "load_config", return_value=_cfg()):
            seed_id = self._seed(project)
            triage = {"notes": [{
                "claim_type": "advice", "kind": "association",
                "text": "先看 DREAM.md，因为刚改过",
                "evidence": [f"seed:{seed_id}"], "confidence": 0.5,
            }]}
            dream.run(project, trigger="manual", job="triage",
                      llm=_FakeLLM(triage))
            only = dream.read(project, job="triage", include_hypothesis=True)
            self.assertTrue(only["notes"])
            self.assertTrue(all(n["job"] == "triage" for n in only["notes"]))
            other = dream.read(project, job="recap", include_hypothesis=True)
            self.assertEqual(other["notes"], [])
            st = dream.status(project)
            self.assertEqual(st["jobs"], list(dream.JOBS))
            self.assertEqual(st["last_job"], "triage")
            self.assertEqual(st["notes"]["by_job"].get("triage"), 1)

    # 21. change_narrative：fact/context 事实进「当前目标」，最多 3 条
    def test_change_narrative_fact_in_current_goal(self):
        with tempfile.TemporaryDirectory() as project, \
                mock.patch.object(dream, "load_config", return_value=_cfg()):
            seed_id = self._seed(project)
            notes = [{
                "claim_type": "fact", "kind": "context",
                "text": f"上次改动第{i}步", "evidence": [f"seed:{seed_id}"],
                "confidence": 0.8,
            } for i in range(4)]
            res = dream.run(project, trigger="manual", job="change_narrative",
                            llm=_FakeLLM({"goal": "推进 5.21.1", "notes": notes}))
            self.assertEqual(res["status"], "ok")
            self.assertEqual(res["job"], "change_narrative")
            saved = dream._load_notes(project)
            self.assertEqual(len(saved), 3)  # 最多 3 条
            self.assertTrue(all(n["job"] == "change_narrative" for n in saved))
            self.assertTrue(all(n["claim_type"] == "fact" for n in saved))
            self.assertTrue(all(n["kind"] == "context" for n in saved))
            md = open(dream._path(project, dream.DREAM_MD),
                      encoding="utf-8").read()
            target = md.split("## 当前目标", 1)[1].split("## 本次开工建议", 1)[0]
            self.assertIn("上次改动第0步", target)
            rules = md.split("## 已验证规则", 1)[1].split("## 坑", 1)[0]
            self.assertNotIn("上次改动第0步", rules)

    # 21b. change_narrative 允许无显著原料，只要有 git 上下文
    def test_change_narrative_context_without_salient(self):
        with tempfile.TemporaryDirectory() as project, \
                mock.patch.object(dream, "load_config", return_value=_cfg()), \
                mock.patch.object(dream, "_collect", return_value={
                    "audit": [], "audit_all": [], "dispatch": [], "seeds": [],
                    "facts": {},
                    "last_guard": {"findings": 0},
                    "git": {"available": True, "head": "abc1234",
                            "commits": [{"commit": "abc1234", "subject": "修 dream"}],
                            "changed_files": []},
                }):
            with open(os.path.join(project, "a.py"), "w", encoding="utf-8") as f:
                f.write("x = 1\n")
            payload = {"notes": [{
                "claim_type": "fact", "kind": "context",
                "text": "最近提交：修 dream", "evidence": ["file:a.py"],
                "confidence": 0.8,
            }]}
            res = dream.run(project, trigger="manual", job="change_narrative",
                            llm=_FakeLLM(payload))
            self.assertEqual(res["status"], "ok")
            md = open(dream._path(project, dream.DREAM_MD),
                      encoding="utf-8").read()
            self.assertIn("最近提交：修 dream", md)

    # 22. failure_postmortem：advice/pitfall → hypothesis，进「未决问题」，不 verified
    def test_failure_postmortem_is_hypothesis(self):
        with tempfile.TemporaryDirectory() as project, \
                mock.patch.object(dream, "load_config", return_value=_cfg()):
            seed_id = self._seed(project)
            payload = {"notes": [{
                "claim_type": "advice", "kind": "pitfall",
                "text": "根因是超时未重试，下次先查超时",
                "evidence": [f"seed:{seed_id}"], "confidence": 0.7,
            }]}
            res = dream.run(project, trigger="manual", job="failure_postmortem",
                            llm=_FakeLLM(payload))
            self.assertEqual(res["status"], "ok")
            self.assertEqual(res["job"], "failure_postmortem")
            note = dream._load_notes(project)[0]
            self.assertEqual(note["claim_type"], "advice")
            self.assertEqual(note["kind"], "pitfall")
            self.assertEqual(note["status"], "hypothesis")
            md = open(dream._path(project, dream.DREAM_MD),
                      encoding="utf-8").read()
            pitfalls = md.split("## 坑", 1)[1].split("## 联想", 1)[0]
            self.assertNotIn("根因是超时未重试", pitfalls)
            questions = md.split("## 未决问题", 1)[1]
            self.assertIn("（假设，未验证）根因是超时未重试", questions)

    # 23. review_draft：只写 drafts/，坏证据进 rejected，next_tools 服务端固定
    def test_review_draft_writes_only_drafts(self):
        with tempfile.TemporaryDirectory() as project, \
                mock.patch.object(dream, "load_config", return_value=_cfg()):
            with open(os.path.join(project, "a.py"), "w", encoding="utf-8") as f:
                f.write("x = 1\n")
            self._seed(project)
            payload = {
                "items": [
                    {"text": "检查 a.py 的边界条件",
                     "evidence": ["file:a.py"], "confidence": 0.7},
                    {"text": "检查 ghost.py 的边界条件",
                     "evidence": ["file:ghost.py"], "confidence": 0.6},
                ],
                "open_questions": ["是否需要兼容旧格式"],
                "next_tools": ["coderef_audit"],
            }
            res = dream.run(project, trigger="manual", job="review_draft",
                            llm=_FakeLLM(payload))
            self.assertEqual(res["status"], "ok")
            self.assertEqual(res["job"], "review_draft")
            self.assertTrue(res["requires_confirmation"])
            self.assertEqual(res["next_tools"],
                             ["coderef_review", "coderef_verify_findings"])
            self.assertEqual(res["items"], 1)
            self.assertEqual(res["rejected"], 1)
            self.assertTrue(os.path.isfile(res["draft_path"]))
            with open(res["draft_path"], encoding="utf-8") as f:
                draft = json.load(f)
            self.assertEqual(draft["schema_version"], dream.SCHEMA_VERSION)
            self.assertEqual(draft["job"], "review_draft")
            self.assertTrue(draft["requires_confirmation"])
            self.assertEqual(draft["next_tools"],
                             ["coderef_review", "coderef_verify_findings"])
            self.assertEqual(len(draft["items"]), 1)
            self.assertEqual(len(draft["rejected"]), 1)
            self.assertEqual(draft["items"][0]["claim_type"], "advice")
            self.assertEqual(draft["items"][0]["status"], "hypothesis")
            # 草稿不写 notes.json / DREAM.md
            self.assertFalse(os.path.exists(dream._path(project, dream.NOTES_FILE)))
            self.assertFalse(os.path.exists(dream._path(project, dream.DREAM_MD)))
            self.assertFalse(res["envelope"]["usable_for_decisions"])
            self.assertIn("coderef_review",
                          res["envelope"]["owner_actions"]["consumer"])

    # 24. refactor_draft / asset_candidate / noise_proposal：各自写草稿，next_tools 固定
    def test_other_draft_jobs_write_files(self):
        cases = (
            ("refactor_draft", ["coderef_arch_verify", "coderef_target_adopt"]),
            ("asset_candidate", ["coderef_asset", "coderef_registry"]),
            ("noise_proposal", ["coderef_scope", "coderef_whitelist"]),
        )
        with tempfile.TemporaryDirectory() as project, \
                mock.patch.object(dream, "load_config", return_value=_cfg()):
            for job, expected in cases:
                # 草稿会推进 seed 游标：每个 job 各自投喂一条新 seed
                seed_id = self._seed(project, f"{job} 的显著事件",
                                     kind="pitfall")
                payload = {
                    "items": [{"text": f"{job} 候选一条",
                               "evidence": [f"seed:{seed_id}"],
                               "confidence": 0.5}],
                    "next_tools": ["bogus_tool"],
                }
                res = dream.run(project, trigger="manual", job=job,
                                llm=_FakeLLM(payload))
                self.assertEqual(res["status"], "ok")
                self.assertEqual(res["job"], job)
                self.assertEqual(res["next_tools"], expected)
                self.assertTrue(os.path.isfile(res["draft_path"]))
                with open(res["draft_path"], encoding="utf-8") as f:
                    draft = json.load(f)
                self.assertEqual(draft["job"], job)
                self.assertEqual(draft["next_tools"], expected)
                self.assertEqual(len(draft["items"]), 1)
            self.assertEqual(len(dream._draft_files(project)), 3)

    # 25. action=drafts：按 job 过滤、按 draft_id 读全文、非法参数报错
    def test_list_drafts_filter_and_read(self):
        from core.handlers_misc import _dream
        with tempfile.TemporaryDirectory() as project, \
                mock.patch.object(dream, "load_config", return_value=_cfg()):
            seed_id = self._seed(project, "审查草稿的显著事件", kind="pitfall")
            review = dream.run(project, trigger="manual", job="review_draft",
                               llm=_FakeLLM({"items": [{
                                   "text": "审查清单一条",
                                   "evidence": [f"seed:{seed_id}"],
                                   "confidence": 0.6}]}))
            seed_id = self._seed(project, "重构草稿的显著事件", kind="pitfall")
            refactor = dream.run(project, trigger="manual", job="refactor_draft",
                                 llm=_FakeLLM({"items": [{
                                     "text": "重构步骤一条",
                                     "evidence": [f"seed:{seed_id}"],
                                     "confidence": 0.6}]}))
            self.assertEqual(review["status"], "ok")
            self.assertEqual(refactor["status"], "ok")

            all_drafts = dream.list_drafts(project)
            self.assertEqual(all_drafts["status"], "ok")
            self.assertTrue(all_drafts["available"])
            self.assertEqual(len(all_drafts["drafts"]), 2)
            only = dream.list_drafts(project, job="review_draft")
            self.assertEqual(len(only["drafts"]), 1)
            self.assertEqual(only["drafts"][0]["job"], "review_draft")

            full = dream.list_drafts(project, draft_id=review["draft_id"])
            self.assertEqual(full["status"], "ok")
            self.assertEqual(full["draft"]["job"], "review_draft")
            self.assertEqual(len(full["draft"]["items"]), 1)

            bad_job = dream.list_drafts(project, job="pitfall")
            self.assertEqual(bad_job["status"], "error")
            bad_id = dream.list_drafts(project, draft_id="../evil")
            self.assertEqual(bad_id["status"], "error")
            missing = dream.list_drafts(project, draft_id="review_draft-missing")
            self.assertEqual(missing["status"], "error")

            via_handler = json.loads(_dream({
                "project_path": project, "action": "drafts",
                "job": "refactor_draft"}))
            self.assertEqual(via_handler["status"], "ok")
            self.assertEqual(via_handler["action"], "drafts")
            self.assertEqual(len(via_handler["drafts"]), 1)

    # 26. status 报告 drafts；草稿不进入 digest_items
    def test_status_drafts_and_digest_excludes_drafts(self):
        with tempfile.TemporaryDirectory() as project, \
                mock.patch.object(dream, "load_config", return_value=_cfg()):
            self._seed(project)
            res = dream.run(project, trigger="manual", job="review_draft",
                            llm=_FakeLLM({"items": [{
                                "text": "草稿里的审查建议",
                                "evidence": ["audit:coderef_dream"],
                                "confidence": 0.5}]}))
            self.assertEqual(res["status"], "ok")
            st = dream.status(project)
            self.assertEqual(st["drafts"]["total"], 1)
            self.assertEqual(st["drafts"]["by_job"].get("review_draft"), 1)
            self.assertTrue(st["drafts"]["last_at"])
            self.assertEqual(dream.digest_items(project), [])
            self.assertFalse(dream.digest_meta(project)["available"])

            seed_id = self._seed(project, "另一件显著事件", kind="pitfall")
            dream.run(project, trigger="manual", job="pitfall",
                      llm=_FakeLLM({"notes": [{
                          "kind": "rule", "text": "先看 a.py",
                          "evidence": [f"seed:{seed_id}"],
                          "confidence": 0.8}]}))
            digest = dream.digest_items(project)
            self.assertTrue(digest)
            self.assertFalse(any("草稿里的审查建议" in i["text"]
                                 for i in digest))


    # 27. false_memory_check：证据引用失效 → rejected，且不调用 LLM
    def test_false_memory_check_rejects_stale_evidence(self):
        class _BoomLLM:
            def __init__(self):
                self.calls = 0

            def is_available(self):
                return True

            def chat_completion(self, *args, **kwargs):
                self.calls += 1
                raise AssertionError("false_memory_check 不应调用 LLM")

        with tempfile.TemporaryDirectory() as project, \
                mock.patch.object(dream, "load_config", return_value=_cfg()):
            note = {
                "id": "dream-stale", "kind": "rule", "job": "pitfall",
                "claim_type": "fact", "text": "这条规则引用已失效",
                "evidence": ["file:missing.txt"], "confidence": 0.9,
                "status": "verified", "created_at": dream._now(),
                "last_hit_at": "",
            }
            dream._save_notes(project, [note])
            dream._write_text(
                dream._path(project, dream.DREAM_MD),
                dream._render_md("旧目标", [note], []))

            llm = _BoomLLM()
            res = dream.run(project, trigger="manual",
                            job="false_memory_check", llm=llm)
            self.assertEqual(res["status"], "ok")
            self.assertEqual(res["job"], "false_memory_check")
            self.assertGreaterEqual(res["rejected"], 1)
            self.assertEqual(llm.calls, 0)

            saved = dream._load_notes(project)
            self.assertEqual(saved[0]["status"], "rejected")
            self.assertIn("false_memory_check", saved[0]["reason"])
            md = open(dream._path(project, dream.DREAM_MD),
                      encoding="utf-8").read()
            rules = md.split("## 已验证规则", 1)[1].split("## 坑", 1)[0]
            self.assertNotIn("这条规则引用已失效", rules)
            # 维护不是做梦：last_dream_at 不变
            state = dream._load_state(project)
            self.assertEqual(state["last_dream_at"], "")
            self.assertEqual(state["last_status"], "ok")
            self.assertEqual(state["last_job"], "false_memory_check")

    # 28. false_memory_check：证据仍存在 → 保留 verified
    def test_false_memory_check_keeps_valid_evidence(self):
        with tempfile.TemporaryDirectory() as project, \
                mock.patch.object(dream, "load_config", return_value=_cfg()):
            with open(os.path.join(project, "a.py"), "w", encoding="utf-8") as f:
                f.write("x = 1\n")
            note = {
                "id": "dream-ok", "kind": "rule", "job": "pitfall",
                "claim_type": "fact", "text": "这条规则证据仍存在",
                "evidence": ["file:a.py"], "confidence": 0.9,
                "status": "verified", "created_at": dream._now(),
                "last_hit_at": "",
            }
            dream._save_notes(project, [note])
            md_path = dream._path(project, dream.DREAM_MD)
            dream._write_text(md_path, dream._render_md("旧目标", [note], []))
            before = open(md_path, encoding="utf-8").read()
            res = dream.run(project, trigger="manual",
                            job="false_memory_check")
            self.assertEqual(res["status"], "ok")
            self.assertEqual(res["rejected"], 0)
            self.assertEqual(res["kept"], 1)
            self.assertEqual(res["unknown"], 0)
            saved = dream._load_notes(project)
            self.assertEqual(saved[0]["status"], "verified")
            self.assertTrue(saved[0]["checked_at"])
            # 没有 rejected 时不重写 DREAM.md，避免无意义覆盖
            self.assertEqual(open(md_path, encoding="utf-8").read(), before)

    # 28b. audit: 证据超出 7 天窗口不算失效（审计日志会轮转）
    def test_false_memory_check_audit_window_is_unknown(self):
        with tempfile.TemporaryDirectory() as project, \
                mock.patch.object(dream, "load_config", return_value=_cfg()):
            note = {
                "id": "dream-audit", "kind": "rule", "job": "pitfall",
                "claim_type": "fact", "text": "审计证据在窗口外",
                "evidence": ["audit:coderef_change_guard"], "confidence": 0.9,
                "status": "verified", "created_at": dream._now(),
                "last_hit_at": "",
            }
            dream._save_notes(project, [note])
            res = dream.run(project, trigger="manual",
                            job="false_memory_check")
            self.assertEqual(res["status"], "ok")
            self.assertEqual(res["rejected"], 0)
            self.assertEqual(dream._load_notes(project)[0]["status"], "verified")
            # 窗口外 audit 证据不判失效，但必须如实记为 unknown
            self.assertEqual(res["unknown"], 1)
            self.assertEqual(res["unknown_items"][0]["ref"],
                             "audit:coderef_change_guard")
            self.assertTrue(any("7 天窗口" in x
                                for x in res["envelope"]["cannot_conclude"]))
            runs_dir = dream._path(project, dream.RUNS_DIR)
            names = [n for n in os.listdir(runs_dir) if n.endswith(".json")]
            self.assertEqual(len(names), 1)
            with open(os.path.join(runs_dir, names[0]), encoding="utf-8") as f:
                record = json.load(f)
            self.assertEqual(record["unknown"], 1)
            self.assertEqual(record["unknown_items"][0]["ref"],
                             "audit:coderef_change_guard")

    # 28c. 旧 state 无 open_questions/goal 时，false_memory_check 不得清空 DREAM.md
    def test_false_memory_check_preserves_md_open_questions(self):
        with tempfile.TemporaryDirectory() as project, \
                mock.patch.object(dream, "load_config", return_value=_cfg()):
            stale = {
                "id": "dream-stale2", "kind": "rule", "job": "pitfall",
                "claim_type": "fact", "text": "失效规则",
                "evidence": ["file:missing.txt"], "confidence": 0.9,
                "status": "verified", "created_at": dream._now(),
                "last_hit_at": "",
            }
            dream._save_notes(project, [stale])
            md_path = dream._path(project, dream.DREAM_MD)
            dream._write_text(md_path, dream._render_md(
                "旧目标", [stale], ["梦的模型选型待定"]))
            res = dream.run(project, trigger="manual",
                            job="false_memory_check")
            self.assertEqual(res["rejected"], 1)
            md = open(md_path, encoding="utf-8").read()
            self.assertIn("旧目标", md)
            self.assertIn("梦的模型选型待定", md)
            self.assertNotIn("失效规则", md)

    # 32. seed 游标按 MAX_SEEDS 分批推进，不静默丢料
    def test_seed_cursor_advances_in_batches(self):
        with tempfile.TemporaryDirectory() as project, \
                mock.patch.object(dream, "load_config", return_value=_cfg()):
            for i in range(25):
                dream.seed(project, f"显著事件{i}", kind="pitfall",
                           source="test")
            empty = {"notes": []}
            first = dream.run(project, trigger="manual", job="pitfall",
                              llm=_FakeLLM(empty))
            self.assertEqual(first["status"], "ok")
            self.assertEqual(first["seeds_pending"], 5)
            self.assertEqual(
                dream._load_state(project)["cursor"]["seeds_consumed"], 20)
            second = dream.run(project, trigger="manual", job="pitfall",
                               llm=_FakeLLM(empty))
            self.assertEqual(second["status"], "ok")
            self.assertEqual(second["seeds_pending"], 0)
            self.assertEqual(
                dream._load_state(project)["cursor"]["seeds_consumed"], 25)

    # 29. eval_replay：无 LLM 仍 ok，写 evals/，只出确定性指标
    def test_eval_replay_without_llm(self):
        with tempfile.TemporaryDirectory() as project, \
                mock.patch.object(dream, "load_config", return_value=_cfg()), \
                mock.patch.object(dream, "_build_llm", return_value=None):
            dream._save_notes(project, [{
                "id": "dream-a", "kind": "rule", "job": "pitfall",
                "claim_type": "fact", "text": "一条笔记",
                "evidence": [], "confidence": 0.5,
                "status": "verified", "created_at": dream._now(),
                "last_hit_at": "",
            }])
            res = dream.run(project, trigger="manual", job="eval_replay",
                            llm=None)
            self.assertEqual(res["status"], "ok")
            self.assertFalse(res["llm_available"])
            self.assertEqual(res["metrics"]["notes_total"], 1)
            self.assertTrue(os.path.isfile(res["eval_path"]))
            evals = [n for n in os.listdir(dream._path(project, dream.EVALS_DIR))
                     if n.endswith(".json")]
            self.assertEqual(len(evals), 1)
            with open(res["eval_path"], encoding="utf-8") as f:
                data = json.load(f)
            self.assertEqual(data["schema_version"], dream.SCHEMA_VERSION)
            self.assertEqual(data["job"], "eval_replay")
            self.assertFalse(data["llm_available"])
            self.assertEqual(data["metrics"]["notes_total"], 1)
            self.assertEqual(data["summary"], "")
            self.assertEqual(data["issues"], [])
            state = dream._load_state(project)
            self.assertEqual(state["last_dream_at"], "")
            self.assertEqual(state["last_job"], "eval_replay")

    # 30. eval_replay：有 LLM 时追加软摘要（summary/issues 落盘）
    def test_eval_replay_with_llm_summary(self):
        with tempfile.TemporaryDirectory() as project, \
                mock.patch.object(dream, "load_config", return_value=_cfg()):
            res = dream.run(project, trigger="manual", job="eval_replay",
                            llm=_FakeLLM({"summary": "ok", "issues": ["x"]}))
            self.assertEqual(res["status"], "ok")
            self.assertTrue(res["llm_available"])
            self.assertEqual(res["summary"], "ok")
            self.assertEqual(res["issues"], ["x"])
            with open(res["eval_path"], encoding="utf-8") as f:
                data = json.load(f)
            self.assertTrue(data["llm_available"])
            self.assertEqual(data["summary"], "ok")
            self.assertEqual(data["issues"], ["x"])

    # 31. action=evals：列出摘要、按 eval_id 读全文、非法 ID 报错
    def test_list_evals_via_handler(self):
        from core.handlers_misc import _dream
        with tempfile.TemporaryDirectory() as project, \
                mock.patch.object(dream, "load_config", return_value=_cfg()), \
                mock.patch.object(dream, "_build_llm", return_value=None):
            run = dream.run(project, trigger="manual", job="eval_replay")
            self.assertEqual(run["status"], "ok")

            listed = json.loads(_dream({"project_path": project,
                                        "action": "evals"}))
            self.assertEqual(listed["status"], "ok")
            self.assertEqual(listed["action"], "evals")
            self.assertEqual(len(listed["evals"]), 1)

            full = json.loads(_dream({"project_path": project,
                                      "action": "evals",
                                      "eval_id": run["eval_id"]}))
            self.assertEqual(full["status"], "ok")
            self.assertEqual(full["eval"]["job"], "eval_replay")
            self.assertIn("metrics", full["eval"])

            bad = json.loads(_dream({"project_path": project,
                                     "action": "evals",
                                     "eval_id": "../evil"}))
            self.assertEqual(bad["status"], "error")
            missing = dream.list_evals(project, eval_id="20000101-000000-abcd")
            self.assertEqual(missing["status"], "error")


if __name__ == "__main__":
    unittest.main()
