# -*- coding: utf-8 -*-
"""外部用户反馈两处缺陷的回归测试（标准库 unittest，无第三方依赖）。

覆盖（反馈稿《CodeRef-缺陷反馈稿_v1.md》）：
- 反馈1 duplicate 同名方法误报：宿主类区分 + 契约（参数/返回）兼容 + 短方法体过滤
- 反馈2 cycle 判定过粗：最小真环路径 + 关键逆向边 + 大环提示

新增（用户试用反馈）：
- 反馈3 gov_start 不吃 whitelist 目录排除：filter_excluded / _is_excluded_path /
  _whitelist_exclude_dirs 实时过滤备份目录噪声
- 反馈4 flow_verify 对 self.method() 调用边覆盖不全：_find_containing_node
  同区间 method 优先于 function，保证调用边落到方法节点

运行方式：
    python -m unittest tests.test_feedback_fixes
    或
    python tests/test_feedback_fixes.py
"""
import json
import os
import sys
import tempfile
import time
import unittest
from types import SimpleNamespace
from unittest import mock

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from core.arch_insight import _contract_compatible, _partition_copies, _MIN_BODY_LEN
from core.arch_audit import _min_cycle_path, _key_edges, _find_cycles
from core.mcp_server import _version, PKG_VERSION
from core.graph_closure import filter_excluded
from core.code_knowledge_graph import (
    _is_excluded_path, CodeKnowledgeGraph, KGNode, _module_key,
)
from core.arch_gap_analyzer import _whitelist_exclude_dirs, _detect_duplicates
from core.ast_parser import AstParser
from core.llm_integration import LLMIntegration, LLMConfig, LLMProvider
from core.wiki_generator import _is_wl_excluded as wiki_is_wl_excluded
from core.governance_audit import _is_wl_excluded as gov_is_wl_excluded
from core.governance_audit import _apply_whitelist_exclude, GovernanceAuditor
from core.innovation_propagation_detector import _is_wl_excluded as ipd_is_wl_excluded


# ═══════════════════════════════════════════════════════════════════
# 反馈1 · duplicate 同名方法误报
# ═══════════════════════════════════════════════════════════════════

class ContractCompatibleTest(unittest.TestCase):
    """契约兼容：参数列表 + 返回类型作为区分子，跨类同名不误报真重复。"""

    def setUp(self):
        # 反馈稿复现：DeepSeekClient.chat → str（单次 LLM API 调用）
        self.ds_chat = {
            "params": ["self", "system_prompt", "user_prompt", "temperature", "max_tokens"],
            "return_type": "str",
        }
        # DiscussionEngine.chat → Dict（会话管理 RAG+记忆）
        self.de_chat = {
            "params": ["self", "session_id", "user_message", "enable_rag", "enable_learning"],
            "return_type": "Dict",
        }
        # FakeDS.chat → 空/假返回（测试桩）
        self.fake_chat = {
            "params": ["self", "system_prompt", "user_prompt", "temperature", "max_tokens"],
            "return_type": "",
        }

    def test_diff_params_not_compatible(self):
        # 参数列表不同（长度不同）→ 契约不兼容，不应聚入同一重复簇
        self.assertFalse(_contract_compatible(self.ds_chat, self.de_chat))

    def test_diff_return_type_not_compatible(self):
        # 参数相同但返回类型不同（str vs Dict）→ 契约不兼容
        other = dict(self.ds_chat, return_type="Dict")
        self.assertFalse(_contract_compatible(self.ds_chat, other))

    def test_same_contract_compatible(self):
        # 参数相同、返回类型一致 → 契约兼容（真重复候选）
        self.assertTrue(_contract_compatible(self.ds_chat, self.fake_chat))

    def test_empty_return_not_blocking(self):
        # 一侧返回类型为空（旧图谱/测试桩）不阻断兼容判定
        self.assertTrue(_contract_compatible(self.fake_chat, self.ds_chat))

    def test_self_param_excluded(self):
        # self 不计入参数比对：一侧显式含 self、另一侧不含 → 视为同契约
        a = {"params": ["self", "x", "y"], "return_type": "int"}
        b = {"params": ["x", "y"], "return_type": "int"}
        self.assertTrue(_contract_compatible(a, b))


class PartitionCopiesTest(unittest.TestCase):
    """契约不兼容的副本不聚入同一重复簇，降为同名候选。"""

    def _copy(self, body, params, rt):
        return {"body": body, "params": params, "return_type": rt}

    def test_contract_incompatible_not_clustered(self):
        # 两个契约相同的长方法 + 一个契约不同的长方法
        body = "def chat(self, a, b):\n    return self._call(a, b)\n" * 5
        copies = [
            self._copy(body, ["self", "a", "b"], "str"),
            self._copy(body, ["self", "a", "b"], "str"),
            self._copy(body, ["self", "x", "y", "z"], "Dict"),
        ]
        clusters, singles = _partition_copies(copies, 0.6)
        # 契约相同的两个聚成簇，契约不同的落单
        self.assertEqual(len(clusters), 1)
        self.assertEqual(len(clusters[0]), 2)
        self.assertEqual(len(singles), 1)
        self.assertIs(singles[0], copies[2])

    def test_short_body_filtered_by_caller(self):
        # 短方法体（归一化后 < _MIN_BODY_LEN）由调用方过滤，不参与聚类
        self.assertGreaterEqual(_MIN_BODY_LEN, 20)
        short = "return x"
        self.assertLess(len(short), _MIN_BODY_LEN)


# ═══════════════════════════════════════════════════════════════════
# 反馈2 · cycle 判定过粗
# ═══════════════════════════════════════════════════════════════════

class MinCyclePathTest(unittest.TestCase):
    """最小真环：SCC 内提取最短闭环，而非超长无序列表。"""

    def test_shortest_cycle_found(self):
        # 分层开放引用网络：app(3) → core(2) → utils(1) → app(3)
        graph = {
            "app/main": ["core/scheduler"],
            "core/scheduler": ["utils/helper"],
            "utils/helper": ["app/main"],
        }
        comp = ["app/main", "core/scheduler", "utils/helper"]
        cyc = _min_cycle_path(graph, comp)
        self.assertIsNotNone(cyc)
        # 首尾相同构成闭环
        self.assertEqual(cyc[0], cyc[-1])
        # 最小环只含 3 个不同节点 + 回到起点
        self.assertEqual(len(cyc), 4)
        self.assertEqual(set(cyc), set(comp))

    def test_larger_scc_still_min_cycle(self):
        # 大 SCC 中仍提取最短闭环，而非全量节点
        nodes = [f"m{i}" for i in range(20)]
        graph = {nodes[i]: [nodes[(i + 1) % 20]] for i in range(20)}
        # 额外加一条捷径 m0 → m2，形成更短环 m0→m2→m3→...→m0
        graph["m0"] = ["m1", "m2"]
        cyc = _min_cycle_path(graph, nodes)
        self.assertIsNotNone(cyc)
        # 全量环为 21 元素（20 节点 + 起点）；捷径环为 20 元素，应更短
        self.assertLess(len(cyc), 21)


class KeyEdgesTest(unittest.TestCase):
    """关键边：标注跨层逆向边（低层依赖上层），与 layer_viol 同口径。"""

    def test_reverse_edge_low_to_high(self):
        # app(3) → core(2) → utils(1) → app(3)：utils→app 是低层依赖上层 → 逆向
        min_cycle = ["app/main", "core/scheduler", "utils/helper", "app/main"]
        edges = _key_edges(min_cycle)
        self.assertEqual(len(edges), 3)
        rev = [e for e in edges if e["reverse"]]
        self.assertEqual(len(rev), 1)
        self.assertEqual(rev[0]["source"], "utils/helper")
        self.assertEqual(rev[0]["target"], "app/main")

    def test_high_to_low_not_reverse(self):
        # 同层环内无跨层逆向边（层相同不标注）
        min_cycle = ["app/main", "app/other", "app/main"]
        edges = _key_edges(min_cycle)
        self.assertEqual(len(edges), 2)
        self.assertFalse(any(e["reverse"] for e in edges))


class FindCyclesTest(unittest.TestCase):
    """cycle_details：最小环 + 关键边 + 大环提示。"""

    def test_cycle_details_fields(self):
        mod_adj = {
            "app/main": ["core/scheduler"],
            "core/scheduler": ["utils/helper"],
            "utils/helper": ["app/main"],
        }
        cycles, self_loops, details = _find_cycles(mod_adj, set(), 2)
        self.assertEqual(len(cycles), 1)
        self.assertEqual(len(details), 1)
        cd = details[0]
        self.assertIn("min_cycle", cd)
        self.assertIn("key_edges", cd)
        self.assertIn("hint_large_scc", cd)
        self.assertEqual(cd["size"], 3)
        self.assertFalse(cd["hint_large_scc"])
        self.assertGreaterEqual(len(cd["min_cycle"]), 2)
        self.assertGreaterEqual(len(cd["key_edges"]), 1)

    def test_large_scc_hint(self):
        # 节点数超过阈值（12）的环提示"整个子图被圈为强连通分量"
        nodes = [f"m{i}" for i in range(15)]
        mod_adj = {nodes[i]: [nodes[(i + 1) % 15]] for i in range(15)}
        cycles, _, details = _find_cycles(mod_adj, set(), 2)
        self.assertEqual(len(cycles), 1)
        self.assertTrue(details[0]["hint_large_scc"])

    def test_self_loop_not_cycle(self):
        # 模块内自环不当作模块间循环
        mod_adj = {"app/main": ["app/main"]}
        cycles, self_loops, details = _find_cycles(mod_adj, {"app/main"}, 2)
        self.assertEqual(cycles, [])
        self.assertEqual(self_loops, ["app/main"])
        self.assertEqual(details, [])


class VersionProbeTest(unittest.TestCase):
    """版本探针（缺陷记录 §4 建议事项）：一行调用断言 版本==target。"""

    def test_version_matches_pkg(self):
        # 返回的 version 必须等于当前进程加载的 PKG_VERSION（杜绝结果字段反推误判）
        out = json.loads(_version({}))
        self.assertEqual(out["tool"], "coderef_version")
        self.assertEqual(out["name"], "coderef-ai")
        self.assertEqual(out["status"], "completed")
        self.assertEqual(out["version"], PKG_VERSION)

    def test_no_project_path_needed(self):
        # 空参数即可调用（无需 project_path，零副作用）
        out = json.loads(_version({}))
        self.assertIn("version", out)
        self.assertTrue(out["version"])

    def test_via_run_skips_path_validation(self):
        """_run 层空参数调用 coderef_version 应返回版本，不被路径校验拦截。

        缺陷记录 U-38 观察项：现场调用 coderef_version 返回空——
        根因是 _run 开头 project_path 校验豁免名单漏了 coderef_version，
        空参数调用 p="" → _validate_project_path 抛错 → 客户端看到"返回空"。
        修复：把 coderef_version 加入豁免名单（与 coderef_scan_list 同列）。
        """
        from core.mcp_server import Server
        s = Server()
        result = s._run("coderef_version", {})
        self.assertIsInstance(result, str)
        out = json.loads(result)
        self.assertEqual(out["status"], "completed")
        self.assertEqual(out["tool"], "coderef_version")
        self.assertEqual(out["version"], PKG_VERSION)


class MemoryLayerKgExcludeTest(unittest.TestCase):
    """缺陷记录 U-38 观察项：memory_layer 重建图谱时 _refactor_backup 仍入图。

    根因：MemoryLayer._update_knowledge_graph 调用 kg.build() 没传
    exclude_dirs，导致 coderef_memory(action=sync, mode=full) 路径下
    备份目录文件全入图，污染符号级真身/循环/重复判定。
    修复：_update_knowledge_graph 读取 whitelist dir 条目并传入
    exclude_dirs，与 pipeline_runner._build_kg 口径一致。

    U-39 修正：真实白名单 dir 排除条目形态是 `{"dir": "_refactor_backup"}`
    （无 rule 字段，以 dir 字段标志）。v5.13.5 曾误加 `rule=="dir"` 判断致
    exclude_dirs 恒空；测试构造已改为真实形态，防止再次假绿。
    """

    def test_update_kg_passes_whitelist_dirs(self):
        from core.memory_layer import MemoryLayer
        with tempfile.TemporaryDirectory() as tmp:
            pp = os.path.join(tmp, "prj")
            os.makedirs(os.path.join(pp, "core"), exist_ok=True)
            with open(os.path.join(pp, "core", "main.py"), "w",
                      encoding="utf-8") as f:
                f.write("def entry():\n    pass\n")

            from core.pipeline_runner import whitelist_add, whitelist_clear
            whitelist_clear(pp)
            # 真实形态条目（无 rule 字段）：只有 dir 字段标志。
            whitelist_add(pp, [{"dir": "_refactor_backup"}])

            captured = {}

            def fake_build(self, analysis=None, ast_results=None,
                           gitnexus_dir=None, exclude_dirs=None, builder="unknown"):
                captured["exclude_dirs"] = list(exclude_dirs or [])
                return {"nodes": 1, "edges": 0, "errors": []}

            try:
                with mock.patch("core.code_knowledge_graph.CodeKnowledgeGraph.build",
                                fake_build):
                    MemoryLayer._update_knowledge_graph(pp, None, None)
                self.assertIn("_refactor_backup", captured["exclude_dirs"],
                              "whitelist dir 条目应作为 exclude_dirs 传入 kg.build")
            finally:
                whitelist_clear(pp)


# ═══════════════════════════════════════════════════════════════════
# 反馈3 · gov_start 不吃 whitelist 目录排除
# ═══════════════════════════════════════════════════════════════════

class IsExcludedPathTest(unittest.TestCase):
    """路径判定：排除目录下的相对/绝对路径命中，前缀边界防误伤。"""

    def test_relative_path_hit(self):
        # 相对路径位于排除目录下 → 命中
        self.assertTrue(_is_excluded_path("backup/2026-09-01/app.py", "d:/proj", ["backup"]))
        self.assertTrue(_is_excluded_path("backup/app.py", "d:/proj", ["backup"]))

    def test_exact_dir_hit(self):
        # 路径恰好等于排除目录本身 → 命中
        self.assertTrue(_is_excluded_path("backup", "d:/proj", ["backup"]))

    def test_absolute_path_hit(self):
        # 绝对路径（Windows 风格）位于排除目录下 → 命中
        self.assertTrue(_is_excluded_path(
            "d:/proj/backup/2026-09-01/app.py", "d:/proj", ["backup"]))

    def test_prefix_boundary_no_false_hit(self):
        # 前缀边界：排除 backup 不得误伤 backup_notes / backup2（防前缀误伤）
        self.assertFalse(_is_excluded_path("backup_notes/app.py", "d:/proj", ["backup"]))
        self.assertFalse(_is_excluded_path("backup2/app.py", "d:/proj", ["backup"]))

    def test_dot_slash_prefix_stripped(self):
        # ./ 前缀剥离：./backup/app.py 与 backup/app.py 同判命中
        self.assertTrue(_is_excluded_path("./backup/app.py", "d:/proj", ["backup"]))

    def test_empty_exclude_dirs(self):
        # 无排除目录 → 永不命中
        self.assertFalse(_is_excluded_path("backup/app.py", "d:/proj", []))


class FilterExcludedTest(unittest.TestCase):
    """实时过滤：加载图谱后剔除排除目录节点及其 CALLS 边（旧图不重建也生效）。"""

    def _graph(self):
        nodes = {
            "mod:src/app": {"id": "mod:src/app", "type": "module",
                            "name": "app", "file_path": "src/app.py"},
            "func:src/app:run": {"id": "func:src/app:run", "type": "function",
                                 "name": "run", "file_path": "src/app.py"},
            "mod:backup/app": {"id": "mod:backup/app", "type": "module",
                               "name": "app", "file_path": "backup/app.py"},
            "func:backup/app:run": {"id": "func:backup/app:run", "type": "function",
                                    "name": "run", "file_path": "backup/app.py"},
        }
        adj = {
            "func:src/app:run": ["func:backup/app:run"],
            "func:backup/app:run": ["mod:backup/app"],
        }
        return nodes, adj

    def test_excluded_nodes_removed(self):
        nodes, adj = self._graph()
        nodes2, adj2 = filter_excluded(nodes, adj, "d:/proj", ["backup"])
        # 排除目录下节点被剔除
        self.assertNotIn("mod:backup/app", nodes2)
        self.assertNotIn("func:backup/app:run", nodes2)
        # 保留目录下节点仍在
        self.assertIn("func:src/app:run", nodes2)

    def test_edges_to_excluded_removed(self):
        nodes, adj = self._graph()
        _, adj2 = filter_excluded(nodes, adj, "d:/proj", ["backup"])
        # 指向被剔除节点的边被移除：源节点不再有任何保留目标（键被整体丢弃）
        self.assertNotIn("func:src/app:run", adj2)
        # 被剔除节点作为源头的边整体消失
        self.assertNotIn("func:backup/app:run", adj2)

    def test_no_exclude_dirs_identity(self):
        nodes, adj = self._graph()
        nodes2, adj2 = filter_excluded(nodes, adj, "d:/proj", [])
        self.assertIs(nodes2, nodes)
        self.assertIs(adj2, adj)

    def test_no_match_identity(self):
        # 排除目录无命中（如排除不存在目录）→ 原样返回，不重建
        nodes, adj = self._graph()
        nodes2, adj2 = filter_excluded(nodes, adj, "d:/proj", ["nope"])
        self.assertIs(nodes2, nodes)
        self.assertIs(adj2, adj)


class WhitelistExcludeDirsTest(unittest.TestCase):
    """whitelist dir 条目 → 排除目录列表（备份/镜像目录不污染差距分析）。"""

    def test_dir_entries_collected(self):
        with mock.patch("core.pr_whitelist.whitelist_list") as m:
            m.return_value = [
                {"dir": "backup"}, {"dir": "mirror"}, {"kind": "file", "path": "x.py"},
            ]
            self.assertEqual(_whitelist_exclude_dirs("d:/proj"), ["backup", "mirror"])

    def test_no_dir_entries_empty(self):
        with mock.patch("core.pr_whitelist.whitelist_list") as m:
            m.return_value = [{"kind": "file", "path": "x.py"}]
            self.assertEqual(_whitelist_exclude_dirs("d:/proj"), [])

    def test_missing_module_fallback_empty(self):
        # whitelist_list 不可用（异常）→ 诚实降级为空列表，不阻断差距分析
        with mock.patch("core.pr_whitelist.whitelist_list",
                        side_effect=ImportError("no module")):
            self.assertEqual(_whitelist_exclude_dirs("d:/proj"), [])


# ═══════════════════════════════════════════════════════════════════
# 反馈4 · flow_verify 对 self.method() 调用边覆盖不全
# ═══════════════════════════════════════════════════════════════════

class FindContainingNodeTest(unittest.TestCase):
    """_find_containing_node 同区间 method 优先：self.method() 调用边落到方法节点。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.db = os.path.join(self._tmp.name, "kg.db")
        self.kg = CodeKnowledgeGraph(self._tmp.name, db_path=self.db)
        self.kg._connect()
        self.kg._init_schema()

    def tearDown(self):
        self.kg.close()
        self._tmp.cleanup()

    def _seed(self, nodes):
        for n in nodes:
            self.kg._upsert_node(KGNode(**n))

    def test_method_preferred_over_function_same_range(self):
        # 类方法同时注册为 func 与 method（行区间相同）→ 命中 method 节点
        self._seed([
            dict(id="func:src/app:run", type="function", name="run",
                 file_path="src/app.py", start_line=10, end_line=30),
            dict(id="method:src/app:Bot.run", type="method", name="Bot.run",
                 file_path="src/app.py", start_line=10, end_line=30),
        ])
        nid = self.kg._find_containing_node("src/app.py", 15)
        self.assertEqual(nid, "method:src/app:Bot.run")

    def test_function_fallback_when_no_method(self):
        # 无 method 节点时回退到 function 节点
        self._seed([
            dict(id="func:src/app:run", type="function", name="run",
                 file_path="src/app.py", start_line=10, end_line=30),
        ])
        nid = self.kg._find_containing_node("src/app.py", 15)
        self.assertEqual(nid, "func:src/app:run")

    def test_class_fallback_when_no_function(self):
        # 仅类节点覆盖该行 → 回退到 class 节点
        self._seed([
            dict(id="class:src/app:Bot", type="class", name="Bot",
                 file_path="src/app.py", start_line=1, end_line=50),
        ])
        nid = self.kg._find_containing_node("src/app.py", 20)
        self.assertEqual(nid, "class:src/app:Bot")

    def test_no_containing_node(self):
        # 行号不在任何节点区间 → None（不建假边）
        self._seed([
            dict(id="func:src/app:run", type="function", name="run",
                 file_path="src/app.py", start_line=10, end_line=30),
        ])
        self.assertIsNone(self.kg._find_containing_node("src/app.py", 99))
        # 不同文件不串扰
        self.assertIsNone(self.kg._find_containing_node("src/other.py", 15))


# ═══════════════════════════════════════════════════════════════════
# 反馈5 · innovation_review/interpret 无 LLM Key 时卡 pending 空转
# ═══════════════════════════════════════════════════════════════════

class LLMPlaceholderKeyTest(unittest.TestCase):
    """无有效 LLM Key 快速阻断：占位符/示例 Key 不算有效凭据，不发起真实请求空转。"""

    def test_placeholder_key_detected(self):
        # 空 / 占位符 / 示例 Key → 判定为占位符
        self.assertTrue(LLMIntegration._is_placeholder_key(""))
        self.assertTrue(LLMIntegration._is_placeholder_key("ollama"))
        self.assertTrue(LLMIntegration._is_placeholder_key("sk-xxx"))
        self.assertTrue(LLMIntegration._is_placeholder_key("sk-your-key"))
        self.assertTrue(LLMIntegration._is_placeholder_key("your-api-key"))
        # 真实形态 Key → 判定为有效
        self.assertFalse(LLMIntegration._is_placeholder_key("sk-real-key-123abc"))

    def test_ollama_placeholder_key_provider_aware(self):
        # OLLAMA provider 的 "ollama" 占位凭据不算无效（本地服务无需真实 Key）
        self.assertFalse(LLMIntegration._is_placeholder_key("ollama", LLMProvider.OLLAMA))
        # 其余 provider 下 "ollama" 仍是占位符
        self.assertTrue(LLMIntegration._is_placeholder_key("ollama", LLMProvider.DEEPSEEK))
        self.assertTrue(LLMIntegration._is_placeholder_key("ollama", LLMProvider.OPENAI))

    def _inst(self, api_key, provider=LLMProvider.DEEPSEEK):
        with mock.patch.object(LLMIntegration, "_init_client"):
            inst = LLMIntegration(LLMConfig(provider=provider, api_key=api_key))
            inst.client = object()  # 模拟客户端已初始化（不校验 Key 有效性）
            return inst

    def test_is_available_false_for_placeholder(self):
        # 占位符 Key 即使 client 已初始化，也不视为可用（避免误判后发起真实请求）
        self.assertFalse(self._inst("ollama").is_available())
        self.assertFalse(self._inst("sk-xxx").is_available())

    def test_is_available_true_for_real_key(self):
        self.assertTrue(self._inst("sk-real-key-123abc").is_available())

    def test_is_available_true_for_ollama_provider(self):
        # OLLAMA provider + "ollama" 占位凭据 → 可用（本地服务无需真实 Key）
        self.assertTrue(self._inst("ollama", LLMProvider.OLLAMA).is_available())

    def test_chat_completion_blocks_placeholder_without_request(self):
        # 占位符 Key → 立即返回结构化错误，且不发起任何 LLM 请求
        inst = self._inst("ollama")
        inst.client = mock.Mock()
        out = inst.chat_completion([{"role": "user", "content": "hi"}])
        self.assertTrue(LLMIntegration.is_error_response(out))
        inst.client.chat.completions.create.assert_not_called()


# ═══════════════════════════════════════════════════════════════════
# 反馈6 · whitelist dir 排除未覆盖全校工具（innovation/wiki/gov）
# ═══════════════════════════════════════════════════════════════════

class WhitelistExcludeAllToolsTest(unittest.TestCase):
    """whitelist dir 排除覆盖 innovation/wiki/gov：与 arch_* 共享同一排除口径。"""

    def test_is_excluded_path_nested_dirname(self):
        # 目录名任意层级匹配：排除 _refactor_backup，嵌套子目录下同名目录也命中
        self.assertTrue(_is_excluded_path(
            "core/_refactor_backup/x.py", "d:/proj", ["_refactor_backup"]))
        self.assertTrue(_is_excluded_path(
            "_refactor_backup/x.py", "d:/proj", ["_refactor_backup"]))
        # 前缀边界：refactor_backup（无下划线）不误伤
        self.assertFalse(_is_excluded_path(
            "core/refactor_backup/x.py", "d:/proj", ["_refactor_backup"]))

    def test_wiki_wl_excluded(self):
        self.assertTrue(wiki_is_wl_excluded(
            "d:/proj/core/_refactor_backup", "d:/proj", ["_refactor_backup"]))
        self.assertFalse(wiki_is_wl_excluded(
            "d:/proj/core/app.py", "d:/proj", ["_refactor_backup"]))
        self.assertFalse(wiki_is_wl_excluded(
            "d:/proj/core/_refactor_backup", "d:/proj", []))

    def test_gov_wl_excluded(self):
        self.assertTrue(gov_is_wl_excluded(
            "d:/proj/_refactor_backup", "d:/proj", ["_refactor_backup"]))
        self.assertFalse(gov_is_wl_excluded(
            "d:/proj/src/app.py", "d:/proj", ["_refactor_backup"]))

    def test_ipd_wl_excluded(self):
        self.assertTrue(ipd_is_wl_excluded(
            "d:/proj/_refactor_backup", "d:/proj", ["_refactor_backup"]))
        self.assertFalse(ipd_is_wl_excluded(
            "d:/proj/src/app.py", "d:/proj", ["_refactor_backup"]))


# ═══════════════════════════════════════════════════════════════════
# 反馈7 · 规则层审计（gov 规则扫描）仍被备份目录污染
#   根因：whitelist 的 dir 排除此前只作用于图谱符号级分析与 governance 的
#   secret/doc 扫描；`audit()` 的 analysis.files（CodeAnalyzer.analyze_project
#   返回）不读该排除 → PITFALL/security/quality/architecture 编程规则全部
#   落到备份目录文件，「图谱干净 ≠ 审计干净」。修复：audit() 边界统一
#   _apply_whitelist_exclude 过滤 + 重算 total_files/total_lines。
# ═══════════════════════════════════════════════════════════════════
class RuleAuditWhitelistExcludeTest(unittest.TestCase):
    """规则层审计接入 whitelist dir 排除，备份目录不产生任何编程规则违规。"""

    def _analysis(self):
        from core.code_analyzer import ProjectAnalysis
        from core.code_models import CodeFile
        files = [
            CodeFile(file_path="d:/p/src/app.py", language="python", raw_content="a=1\n"),
            CodeFile(file_path="d:/p/_refactor_backup/old.py", language="python", raw_content="x=1\n"),
            CodeFile(file_path="d:/p/core/_refactor_backup/nested.py", language="python", raw_content="y=1\n"),
        ]
        return ProjectAnalysis(project_path="d:/p", total_files=3,
                               total_lines=5, files=files)

    def test_no_exclude_dirs_keeps_all(self):
        analysis = self._analysis()
        out = _apply_whitelist_exclude(analysis, "d:/p", wl_exclude=[])
        self.assertEqual(out.total_files, 3)
        self.assertEqual([os.path.basename(f.file_path) for f in out.files],
                         ["app.py", "old.py", "nested.py"])

    def test_backup_files_dropped_and_totals_recomputed(self):
        analysis = self._analysis()
        out = _apply_whitelist_exclude(analysis, "d:/p",
                                       wl_exclude=["_refactor_backup"])
        self.assertEqual(out.total_files, 1)
        self.assertEqual(len(out.files), 1)
        self.assertEqual(os.path.basename(out.files[0].file_path), "app.py")
        # total_lines 同步重算（仅保留文件，"a=1\n" 计 2 行）
        self.assertEqual(out.total_lines, 2)

    def test_default_reads_whitelist_dirs(self):
        analysis = self._analysis()
        with mock.patch("core.pipeline_runner.whitelist_list",
                        return_value=[{"dir": "_refactor_backup"}]):
            out = _apply_whitelist_exclude(analysis, "d:/p")
        self.assertEqual(out.total_files, 1)
        self.assertEqual(os.path.basename(out.files[0].file_path), "app.py")

    def test_audit_end_to_end_clean_of_backup(self):
        """真实 audit()：备份目录中的违规文件完全不进入结果。"""
        tmp = tempfile.mkdtemp(prefix="coderef_rule_audit_")
        try:
            src = os.path.join(tmp, "src")
            os.makedirs(src)
            bkp = os.path.join(tmp, "_refactor_backup")
            os.makedirs(bkp)
            good = os.path.join(src, "app.py")
            with open(good, "w", encoding="utf-8") as fh:
                fh.write("import os\n\n\ndef run():\n    return os.getcwd()\n")
            leak = os.path.join(bkp, "leak.py")
            # 裸 except 会让 PITFALL-01 命中，且硬编码密钥命中 IRON-SEC-01，
            # 若排除失效则污染结果 —— 用于反向验证
            with open(leak, "w", encoding="utf-8") as fh:
                fh.write("def f():\n    try:\n        pass\n"
                         "    except:\n        pass\n")
            with mock.patch("core.pipeline_runner.whitelist_list",
                            return_value=[{"dir": "_refactor_backup"}]):
                report = GovernanceAuditor().audit(tmp)
            self.assertNotIn("_refactor_backup", report)
        finally:
            import shutil
            shutil.rmtree(tmp, ignore_errors=True)


# ═══════════════════════════════════════════════════════════════════
# 反馈8 · arch_gap 不消费白名单 rule=duplicate 豁免（用户试用反馈）
#   根因：_detect_duplicates 仅按 semantic_kind（designed_parallel/ture）过滤，
#   不读取 whitelist 中 rule=duplicate 条目 → 已人工豁免的"设计并存"符号仍被
#   重新点名收敛。期望：豁免条目对应的符号不再产出 duplicate gap。
# ═══════════════════════════════════════════════════════════════════
class ArchGapDuplicateExemptionTest(unittest.TestCase):
    """问题1 · arch_gap 应消费白名单 rule=duplicate 豁免，已豁免符号不再报 true_duplicate。"""

    def _ds(self, name, semantic="true_duplicate"):
        return {"ok": True, "clusters": [{
            "kind": "duplicate", "name": name,
            "copies": [{"mod": "技能库", "file": "smart_data_hub.py", "line": 365},
                       {"mod": "数据接入", "file": "api_clients.py", "line": 343}],
            "max_sim": 0.63, "semantic_kind": semantic,
        }]}

    def _exempt_whitelist(self):
        # 真实白名单 schema：file + rule=duplicate + category（category 前缀为符号名）
        return [{"file": "smart_data_hub.py", "rule": "duplicate",
                 "category": "search_products 非真重复"}]

    def test_exempted_symbol_not_reported(self):
        # 白名单登记 rule=duplicate 豁免 search_products → 不应再产出 true_duplicate gap
        with mock.patch("core.pipeline_runner.whitelist_list") as m:
            m.return_value = self._exempt_whitelist()
            gaps = _detect_duplicates(
                "d:/kuajing", "d:/kuajing/kg.db",
                {"duplicate": True, "directory_duplicate": True},
                ds=self._ds("search_products"))
        self.assertNotIn("search_products", [g["symbol"] for g in gaps])

    def test_exempt_hits_when_copy_has_dir_prefix(self):
        # 真实形态：豁免 file 为裸文件名，而 copies[].file 带目录前缀（技能库/）
        # 精确 in 集合比较永不命中 → 豁免失效。须尾段匹配（缺陷记录 U-37① RED 留证）
        ds = {"ok": True, "clusters": [{
            "kind": "duplicate", "name": "search_products",
            "copies": [{"mod": "技能库", "file": "技能库/smart_data_hub.py", "line": 365},
                       {"mod": "数据接入", "file": "数据接入/api_clients.py", "line": 343}],
            "max_sim": 0.63, "semantic_kind": "true_duplicate",
        }]}
        with mock.patch("core.pipeline_runner.whitelist_list") as m:
            m.return_value = self._exempt_whitelist()
            gaps = _detect_duplicates(
                "d:/kuajing", "d:/kuajing/kg.db",
                {"duplicate": True, "directory_duplicate": True}, ds=ds)
        self.assertNotIn("search_products", [g["symbol"] for g in gaps])

    def test_exempt_hits_with_leading_dir_and_slash_normalize(self):
        # 防御：副本 backslash 路径 + 豁免自身带目录前缀，同样命中（路径统一正斜杠）
        ds = {"ok": True, "clusters": [{
            "kind": "duplicate", "name": "search_products",
            "copies": [{"mod": "技能库", "file": "技能库\\smart_data_hub.py", "line": 365},
                       {"mod": "数据接入", "file": "数据接入\\api_clients.py", "line": 343}],
            "max_sim": 0.63, "semantic_kind": "true_duplicate",
        }]}
        with mock.patch("core.pipeline_runner.whitelist_list") as m:
            m.return_value = [{"file": "技能库/smart_data_hub.py", "rule": "duplicate",
                               "category": "search_products 非真重复"}]
            gaps = _detect_duplicates(
                "d:/kuajing", "d:/kuajing/kg.db",
                {"duplicate": True, "directory_duplicate": True}, ds=ds)
        self.assertNotIn("search_products", [g["symbol"] for g in gaps])

    def test_unexempted_true_duplicate_still_reported(self):
        # 未纳入任何豁免的真重复仍应报出（修复不得误杀非豁免项）
        with mock.patch("core.pipeline_runner.whitelist_list") as m:
            m.return_value = [{"dir": "_refactor_backup"}]
            gaps = _detect_duplicates(
                "d:/kuajing", "d:/kuajing/kg.db",
                {"duplicate": True, "directory_duplicate": True},
                ds=self._ds("fetch_orders"))
        self.assertIn("fetch_orders", [g["symbol"] for g in gaps])

    def test_designed_parallel_still_reported_without_exempt(self):
        # 无豁免时 designed_parallel 平行管线仍保留语义（不受豁免逻辑破坏）
        with mock.patch("core.pipeline_runner.whitelist_list") as m:
            m.return_value = []
            gaps = _detect_duplicates(
                "d:/kuajing", "d:/kuajing/kg.db",
                {"duplicate": True, "directory_duplicate": True},
                ds=self._ds("search_products", semantic="designed_parallel"))
        self.assertIn("search_products", [g["symbol"] for g in gaps])


# ═══════════════════════════════════════════════════════════════════
# 反馈9 · flow_verify 对「方法体内局部 import + 实例化对象方法调用」建边缺口
#   复现 orchestrator.py：_run_tool_research 内
#       from tool_research import ResearchTool
#       svc = ResearchTool()
#       return svc.run(...)
#   语义上 svc 宿主类型为 ResearchTool，CALLS 边应指向 ResearchTool.run。
#   根因：_build_from_ast 仅按名字串（全名精确 / self-cls / 短名 LIKE 回退）解析，
#   无法从局部 import + 实例化推导变量宿主类 → 边漏建 → flow_verify 判 outside。
# ═══════════════════════════════════════════════════════════════════
class InstanceCallResolveTest(unittest.TestCase):
    """问题2 · 实例化对象方法调用应解析到宿主类方法并建 CALLS 边。"""

    SRC = (
        "class AIOrchestrator:\n"
        "    def _run_tool_research(self, params):\n"
        "        from tool_research import ResearchTool\n"
        "        svc = ResearchTool()\n"
        "        return svc.run(params, market='au')\n"
    )

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.proj = self._tmp.name
        orch = os.path.join(self.proj, "调度与指令")
        os.makedirs(orch, exist_ok=True)
        with open(os.path.join(orch, "orchestrator.py"), "w",
                  encoding="utf-8") as fh:
            fh.write(self.SRC)
        self.db = os.path.join(self.proj, "kg.db")
        self.kg = CodeKnowledgeGraph(self.proj, db_path=self.db)
        self.kg._connect()
        self.kg._init_schema()
        self.kg._exclude_dirs = []  # 手动建边不走 build() 的排除目录初始化
        self.entry = ("method:%s:AIOrchestrator._run_tool_research"
                      % _module_key(self.proj, "调度与指令/orchestrator.py"))
        self.research = ("method:%s:ResearchTool.run"
                         % _module_key(self.proj, "tools/tool_research.py"))
        # 模拟 analysis 已建节点：调用者方法 + 被调类方法（多个 .run 制造短名回退歧义）
        self.kg._upsert_node(KGNode(
            id=self.entry, type="method", name="AIOrchestrator._run_tool_research",
            file_path="调度与指令/orchestrator.py", start_line=2, end_line=5))
        self.kg._upsert_node(KGNode(
            id=self.research, type="method", name="ResearchTool.run",
            file_path="tools/tool_research.py", start_line=2, end_line=3))
        # 同宿主类多个方法：使「实例化 ResearchTool()」的 LIKE 回退多候选放弃，
        # 消除「把构造调用误匹配成方法调用」的巧合，逼出真实的变量宿主类型推导缺口
        self.kg._upsert_node(KGNode(
            id="method:%s:ResearchTool.search"
               % _module_key(self.proj, "tools/tool_research.py"),
            type="method", name="ResearchTool.search",
            file_path="tools/tool_research.py", start_line=4, end_line=5))
        self.dissect = ("method:%s:DissectTool.run"
                        % _module_key(self.proj, "tools/tool_dissect.py"))
        self.kg._upsert_node(KGNode(
            id=self.dissect,
            type="method", name="DissectTool.run",
            file_path="tools/tool_dissect.py", start_line=2, end_line=3))

    def tearDown(self):
        self.kg.close()
        self._tmp.cleanup()

    def _build(self):
        rel = "调度与指令/orchestrator.py"
        parser = AstParser(project_root=self.proj)
        r = parser.parse(os.path.join(self.proj, "调度与指令", "orchestrator.py"))
        self.assertIsNotNone(r)
        self.kg._build_from_ast({rel: r}, {"nodes": 0, "edges": 0})

    def _calls(self):
        return {(e[1].source, e[1].target)
                for e in self.kg.get_all_edges() if e[1].type == "CALLS"}

    def test_instance_call_edge_resolves_to_host_class(self):
        # 实例化对象方法调用 svc.run() 应建 CALLS 边到宿主类方法 ResearchTool.run
        self._build()
        self.assertIn((self.entry, self.research), self._calls())

    def test_verify_step_in_pipeline_for_instance_call(self):
        # 边建对后，ResearchTool.run 落在入口下游（in_pipeline/ordered），而非 outside/missing
        self._build()
        self.kg._conn.commit()
        from core.flow_verify import FlowVerifier
        fv = FlowVerifier(self.db)
        out = fv.verify("AIOrchestrator._run_tool_research", ["ResearchTool.run"])
        self.assertTrue(out["entry"]["found"])
        statuses = [s["status"] for s in out["steps"]]
        self.assertNotIn("outside", statuses)
        self.assertNotIn("missing", statuses)

    def test_instance_call_not_bound_to_wrong_class(self):
        # svc.run() 只应绑定宿主类 ResearchTool.run，不得误建边到跨类同名 DissectTool.run
        self._build()
        calls = self._calls()
        self.assertIn((self.entry, self.research), calls)
        self.assertNotIn((self.entry, self.dissect), calls)


class SelfSiblingCallResolveTest(unittest.TestCase):
    """问题2 · 同一类内 self.method() 兄弟调用应建 CALLS 边（反馈点名的两类边之一）。

    复现 orchestrator.py：_dispatch 内 self._run_tool_research(...) 兄弟调用 →
    CALLS 边应建立到同类的 _run_tool_research 方法（而非漏建致下游闭包断裂）。
    """

    SRC = (
        "class AIOrchestrator:\n"
        "    def _dispatch(self, params):\n"
        "        return self._run_tool_research(params)\n"
        "    def _run_tool_research(self, params):\n"
        "        return params\n"
    )

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.proj = self._tmp.name
        with open(os.path.join(self.proj, "orchestrator.py"), "w",
                  encoding="utf-8") as fh:
            fh.write(self.SRC)
        self.db = os.path.join(self.proj, "kg.db")
        self.kg = CodeKnowledgeGraph(self.proj, db_path=self.db)
        self.kg._connect()
        self.kg._init_schema()
        self.kg._exclude_dirs = []
        mod = _module_key(self.proj, "orchestrator.py")
        self.dispatch = f"method:{mod}:AIOrchestrator._dispatch"
        self.run_research = f"method:{mod}:AIOrchestrator._run_tool_research"
        self.kg._upsert_node(KGNode(
            id=self.dispatch, type="method", name="AIOrchestrator._dispatch",
            file_path="orchestrator.py", start_line=2, end_line=3))
        self.kg._upsert_node(KGNode(
            id=self.run_research, type="method",
            name="AIOrchestrator._run_tool_research",
            file_path="orchestrator.py", start_line=4, end_line=5))

    def tearDown(self):
        self.kg.close()
        self._tmp.cleanup()

    def test_self_sibling_call_builds_edge(self):
        # self._run_tool_research() 在 _dispatch 内 → CALLS 边 `_dispatch` → `_run_tool_research`
        parser = AstParser(project_root=self.proj)
        r = parser.parse(os.path.join(self.proj, "orchestrator.py"))
        self.kg._build_from_ast({"orchestrator.py": r}, {"nodes": 0, "edges": 0})
        calls = {(e[1].source, e[1].target)
                 for e in self.kg.get_all_edges() if e[1].type == "CALLS"}
        self.assertIn((self.dispatch, self.run_research), calls)


class ModuleLevelImportInstanceCallTest(unittest.TestCase):
    """问题2 · 真实 orchestrator.py 场景：模块级 import + 兄弟方法 + 带参实例化调用。

    与 InstanceCallResolveTest 的区别（覆盖互补）：
    - InstanceCallResolveTest 把 import 写在方法体内（局部 import）。
    - 而真实 orchestrator.py 顶部是模块级 `from tool_research import ResearchTool`，
      方法体内才做 `tool = ResearchTool(cfg, ...)` 带参实例化 + `tool.run(...)`。
    模块级与局部 import 在 AST 中位置不同，静态建边解析路径不同，须分别覆盖。

    复现 orchestrator.py 典型闭环：
        _run
          └─ self._run_tool_research(params)          → 兄弟方法调用
               ├─ tool = ResearchTool(cfg)            → 模块级 import 化的带参实例化
               └─ tool.run(params)                    → 实例化对象方法调用
    期望：两条 CALLS 边（_run→_run_tool_research、_run_tool_research→ResearchTool.run）
          全部建立，且 verify 不判 outside/missing。
    """

    SRC = (
        "from tool_research import ResearchTool\n"
        "\n"
        "class AIOrchestrator:\n"
        "    def __init__(self, config):\n"
        "        self.config = config\n"
        "    def _run(self, params):\n"
        "        return self._run_tool_research(params)\n"
        "    def _run_tool_research(self, params):\n"
        "        tool = ResearchTool(self.config, cache=None)\n"
        "        return tool.run(params, market='au')\n"
    )

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.proj = self._tmp.name
        orch = os.path.join(self.proj, "调度与指令")
        os.makedirs(orch, exist_ok=True)
        with open(os.path.join(orch, "orchestrator.py"), "w",
                  encoding="utf-8") as fh:
            fh.write(self.SRC)
        self.db = os.path.join(self.proj, "kg.db")
        self.kg = CodeKnowledgeGraph(self.proj, db_path=self.db)
        self.kg._connect()
        self.kg._init_schema()
        self.kg._exclude_dirs = []
        mod = _module_key(self.proj, "调度与指令/orchestrator.py")
        res = _module_key(self.proj, "tools/tool_research.py")
        self.entry_run = f"method:{mod}:AIOrchestrator._run"
        self.run_research = f"method:{mod}:AIOrchestrator._run_tool_research"
        self.research_run = f"method:{res}:ResearchTool.run"
        for nid, name in ((self.entry_run, "AIOrchestrator._run"),
                          (self.run_research, "AIOrchestrator._run_tool_research")):
            self.kg._upsert_node(KGNode(
                id=nid, type="method", name=name,
                file_path="调度与指令/orchestrator.py", start_line=2, end_line=7))
        self.kg._upsert_node(KGNode(
            id=self.research_run, type="method", name="ResearchTool.run",
            file_path="tools/tool_research.py", start_line=2, end_line=3))
        self.kg._upsert_node(KGNode(
            id=f"method:{res}:ResearchTool.search", type="method",
            name="ResearchTool.search",
            file_path="tools/tool_research.py", start_line=4, end_line=5))

    def tearDown(self):
        self.kg.close()
        self._tmp.cleanup()

    def _build(self):
        rel = "调度与指令/orchestrator.py"
        parser = AstParser(project_root=self.proj)
        r = parser.parse(os.path.join(self.proj, "调度与指令", "orchestrator.py"))
        self.assertIsNotNone(r)
        self.kg._build_from_ast({rel: r}, {"nodes": 0, "edges": 0})
        self.kg._conn.commit()

    def _calls(self):
        return {(e[1].source, e[1].target)
                for e in self.kg.get_all_edges() if e[1].type == "CALLS"}

    def test_module_import_instance_call_builds_edge(self):
        # 模块级 import 化实例化 tool.run() → CALLS 边到宿主类 ResearchTool.run
        self._build()
        self.assertIn((self.run_research, self.research_run), self._calls())

    def test_sibling_and_instance_call_both_edge(self):
        # 反馈点名的两类边组合：兄弟调用边 + 实例化调用边 缺一不可
        self._build()
        calls = self._calls()
        self.assertIn((self.entry_run, self.run_research), calls)
        self.assertIn((self.run_research, self.research_run), calls)

    def test_module_import_instance_not_bound_to_wrong_class(self):
        # 实例化调用只绑定宿主类 ResearchTool.run，不得误绑跨类同名
        self._build()
        calls = self._calls()
        self.assertNotIn((self.entry_run, self.research_run), calls)
        dissect = f"method:{_module_key(self.proj, 'tools/tool_dissect.py')}:DissectTool.run"
        self.assertNotIn((self.run_research, dissect), calls)

    def test_verify_sibling_then_instance_not_outside(self):
        # 完整闭环：entry=_run → 兄弟 _run_tool_research → 实例 ResearchTool.run
        # 链路内任何一环不得报 outside/missing
        self._build()
        from core.flow_verify import FlowVerifier
        fv = FlowVerifier(self.db)
        out = fv.verify("AIOrchestrator._run",
                        ["AIOrchestrator._run_tool_research", "ResearchTool.run"])
        self.assertTrue(out["entry"]["found"])
        statuses = [s["status"] for s in out["steps"]]
        self.assertNotIn("outside", statuses)
        self.assertNotIn("missing", statuses)


class ArchitectureCanvasProbeTest(unittest.TestCase):
    """coderef_architecture 末尾追加生成 arch_canvas 画布（方案 A）。

    外部反馈：AI 连续两次"没看到架构图"——architecture 产物分离导致画布成了
    孤儿入口。修复后 architecture 同一次调用即产出画布，并通过 r.arch_canvas
    回传路径。本测试 patch 掉管线重活，仅保留架构画布生成段验证。
    """

    def test_architecture_appends_canvas_and_sets_path(self):
        from core import pipeline_runner
        from core.pipeline_runner import Pipe, PipeResult

        with tempfile.TemporaryDirectory() as tmp:
            pp = os.path.join(tmp, "prj")
            os.makedirs(os.path.join(pp, ".coderef"), exist_ok=True)
            os.makedirs(os.path.join(pp, "src"), exist_ok=True)
            with open(os.path.join(pp, "src", "main.py"), "w",
                      encoding="utf-8") as f:
                f.write("def main():\n    pass\n")

            # 轻量 stub：跳过扫描/图谱/工作流/洞察，真实执行画布生成段
            with mock.patch.object(Pipe, "_scan",
                                   return_value=(1, 1, {})), \
                 mock.patch.object(Pipe, "_build_kg",
                                   return_value={"db_path": None}), \
                 mock.patch.object(Pipe, "_workflow",
                                   return_value=None), \
                 mock.patch("core.arch_insight.insight_markdown",
                            return_value=""):
                r = Pipe().architecture(pp, output_dir=os.path.join(pp, "coderef-report"))

            self.assertTrue(r.arch_canvas, "architecture 应产出 arch_canvas 画布路径")
            self.assertTrue(os.path.isfile(r.arch_canvas),
                            f"画布文件应存在: {r.arch_canvas}")
            self.assertTrue(r.arch_canvas.startswith(
                os.path.join(pp, ".coderef")),
                "画布应落在 <proj>/.coderef/")
            # md 报告尾部应含画布段落，方便编程 AI 读完即知架构图在哪儿
            self.assertIn("架构画布", r.report)
            self.assertIn(r.arch_canvas, r.report)

    def test_architecture_canvas_failure_does_not_break_report(self):
        from core.pipeline_runner import Pipe
        with tempfile.TemporaryDirectory() as tmp:
            pp = os.path.join(tmp, "prj")
            os.makedirs(os.path.join(pp, "src"), exist_ok=True)
            with open(os.path.join(pp, "src", "m.py"), "w",
                      encoding="utf-8") as f:
                f.write("x = 1\n")
            with mock.patch.object(Pipe, "_scan", return_value=(1, 1, {})), \
                 mock.patch.object(Pipe, "_build_kg",
                                   return_value={"db_path": None}), \
                 mock.patch.object(Pipe, "_workflow", return_value=None), \
                 mock.patch("core.arch_insight.insight_markdown",
                            return_value=""), \
                 mock.patch("core.canvas_generator.ArchCanvas.generate",
                            side_effect=RuntimeError("canvas boom")):
                r = Pipe().architecture(pp, output_dir=os.path.join(pp, "coderef-report"))
            self.assertEqual(r.arch_canvas, "")
            self.assertTrue(any("canvas" in e for e in r.errors))
            # 画布失败不阻断报告生成
            self.assertIn("架构分析报告", r.report)


# ═══════════════════════════════════════════════════════════════════
# 缺陷记录 U-40 · workflow_graph 链路接入白名单 dir 排除（备份目录不入图）
# ═══════════════════════════════════════════════════════════════════

class WorkflowGraphExcludeTest(unittest.TestCase):

    def test_drops_backup_dir_nodes_and_edges(self):
        from core.workflow_graph import WorkflowGraph
        wg = WorkflowGraph()
        with tempfile.TemporaryDirectory() as tmp:
            pp = os.path.join(tmp, "prj")
            core_dir = os.path.join(pp, "core")
            back_dir = os.path.join(pp, "_refactor_backup")
            os.makedirs(core_dir, exist_ok=True)
            os.makedirs(back_dir, exist_ok=True)
            nodes = [
                {"id": "core_main", "filePath": os.path.join(core_dir, "main.py")},
                {"id": "backup_x", "filePath": os.path.join(back_dir, "x.py")},
                {"id": "core_util", "filePath": os.path.join(core_dir, "util.py")},
            ]
            edges = [{"from": "core_main", "to": "backup_x"},
                     {"from": "core_main", "to": "core_util"}]
            from core.pipeline_runner import whitelist_add, whitelist_clear
            whitelist_clear(pp)
            # 真实形态白名单条目（无 rule 字段，以 dir 字段标志）
            whitelist_add(pp, [{"dir": "_refactor_backup"}])
            try:
                kept_nodes, kept_edges = wg._apply_whitelist_exclude(
                    pp, nodes, edges)
            finally:
                whitelist_clear(pp)
            ids = {n["id"] for n in kept_nodes}
            self.assertIn("core_main", ids)
            self.assertIn("core_util", ids)
            self.assertNotIn("backup_x", ids)
            self.assertEqual(kept_edges,
                             [{"from": "core_main", "to": "core_util"}])

    def test_no_whitelist_keeps_backup_nodes(self):
        from core.workflow_graph import WorkflowGraph
        wg = WorkflowGraph()
        with tempfile.TemporaryDirectory() as tmp:
            pp = os.path.join(tmp, "prj")
            back_dir = os.path.join(pp, "_refactor_backup")
            os.makedirs(back_dir, exist_ok=True)
            nodes = [{"id": "b", "filePath": os.path.join(back_dir, "x.py")}]
            edges = []
            from core.pipeline_runner import whitelist_clear
            whitelist_clear(pp)
            kept_nodes, kept_edges = wg._apply_whitelist_exclude(pp, nodes, edges)
        self.assertEqual(len(kept_nodes), 1)
        self.assertEqual(kept_edges, [])

    def test_whitelist_load_failure_propagates(self):
        # U-40 / CodeRabbit major：白名单加载失败必须传播而非静默转空排除
        # （fail-open 会让备份目录无过滤入图）。
        from core.workflow_graph import WorkflowGraph
        wg = WorkflowGraph()
        with tempfile.TemporaryDirectory() as tmp:
            pp = os.path.join(tmp, "prj")
            os.makedirs(pp, exist_ok=True)
            nodes = [{"id": "a", "filePath": os.path.join(pp, "a.py")}]
            with mock.patch("core.pipeline_runner.whitelist_list",
                            side_effect=RuntimeError("wl broken")):
                with self.assertRaises(RuntimeError):
                    wg._apply_whitelist_exclude(pp, nodes, [])


# ═══════════════════════════════════════════════════════════════════
# 缺陷记录 U-50 · 含 `..` 的原始路径未在规范化前拒绝
# 缺陷记录 U-51 · 共享输出目录 last-writer-wins（报告目录按项目隔离）
# ═══════════════════════════════════════════════════════════════════

def _raw_get(port: int, path: str) -> int:
    """按原始 request-target 发 GET（不经 urllib 的路径规范化）。"""
    import http.client
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    conn.request("GET", path, headers={"Host": f"127.0.0.1:{port}"})
    resp = conn.getresponse()
    resp.read()
    code = resp.status
    conn.close()
    return code


class UnsafePathGuardTest(unittest.TestCase):
    """U-50：路径规范化**前**按原始 path 判 `..`（含 URL 编码），命中即 404。"""

    def test_raw_and_encoded_dotdot_rejected(self):
        from core.report_console import has_unsafe_path
        for p in ("/.coderef/../index.html",
                  "/.coderef/%2e%2e/index.html",
                  "/.coderef/%2E%2E/index.html",
                  "/.coderef/%252e%252e/index.html"):
            self.assertTrue(has_unsafe_path(p), p)

    def test_normal_paths_allowed(self):
        from core.report_console import has_unsafe_path
        for p in ("/", "/index.html", "/audit.html",
                  "/.coderef/arch_canvas_x.html", "/api/scorecard?refresh=1"):
            self.assertFalse(has_unsafe_path(p), p)

    def test_report_console_route(self):
        from core.report_console import serve, _SERVERS
        with tempfile.TemporaryDirectory() as tmp:
            pp = os.path.join(tmp, "prj")
            os.makedirs(pp, exist_ok=True)
            srv = serve(pp)
            try:
                port = srv["port"]
                self.assertEqual(_raw_get(port, "/.coderef/../index.html"), 404)
                self.assertEqual(_raw_get(port, "/.coderef/%2e%2e/index.html"), 404)
                self.assertEqual(_raw_get(port, "/"), 200)
                # 评估台 iframe 用同目录相对路径，服务必须放行报告目录里的画布副本
                od = srv["output_dir"]
                with open(os.path.join(od, "arch_canvas_x.html"), "w",
                          encoding="utf-8") as f:
                    f.write("<html><body>canvas</body></html>")
                self.assertEqual(_raw_get(port, "/arch_canvas_x.html"), 200)
                self.assertEqual(_raw_get(port, "/arch_canvas_../x.html"), 404)
                # 旧 `/.coderef/<canvas>` 引用仍需兼容（从项目 .coderef/ 读取）
                cd = os.path.join(pp, ".coderef")
                os.makedirs(cd, exist_ok=True)
                with open(os.path.join(cd, "arch_flow_x.html"), "w",
                          encoding="utf-8") as f:
                    f.write("<html><body>flow</body></html>")
                self.assertEqual(_raw_get(port, "/.coderef/arch_flow_x.html"), 200)
            finally:
                h = _SERVERS.pop(pp, None)
                if h:
                    h["httpd"].shutdown()
                    h["httpd"].server_close()

    def test_arch_view_handler_route(self):
        from core.handlers_arch import _serve_arch_static, _ARCH_SERVE
        with tempfile.TemporaryDirectory() as tmp:
            cd = os.path.join(tmp, ".coderef")
            os.makedirs(cd, exist_ok=True)
            with open(os.path.join(cd, "arch_canvas_x.html"), "w",
                      encoding="utf-8") as f:
                f.write("<html><body>x</body></html>")
            srv = _serve_arch_static(cd)
            try:
                port = srv["port"]
                self.assertEqual(_raw_get(port, "/.coderef/../index.html"), 404)
                self.assertEqual(_raw_get(port, "/.coderef/%2e%2e/index.html"), 404)
                self.assertEqual(_raw_get(port, "/"), 200)
                self.assertEqual(_raw_get(port, "/arch_canvas_x.html"), 200)
            finally:
                h = _ARCH_SERVE.pop(cd, None)
                if h:
                    h["httpd"].shutdown()
                    h["httpd"].server_close()


class ReportIsolationTest(unittest.TestCase):
    """U-51：报告目录按项目隔离，多项目交替 report 不互相覆盖入口。"""

    def test_default_dirs_are_project_scoped(self):
        from core.report_renderer import default_report_root, default_report_dir
        a = os.path.abspath(default_report_dir(os.path.join("tmp", "projA")))
        b = os.path.abspath(default_report_dir(os.path.join("tmp", "projB")))
        self.assertNotEqual(a, b)
        self.assertTrue(a.endswith(os.path.join(".coderef", "report", "html")), a)
        self.assertTrue(default_report_root("tmp").endswith(
            os.path.join(".coderef", "report")))

    def test_two_projects_render_independently(self):
        from core.report_renderer import HtmlReportRenderer, default_report_dir
        from core.pr_models import PipeResult
        from core.interpretation_platform import InterpretationPlatform

        def render(pp):
            res = HtmlReportRenderer(pp).render(PipeResult(project_path=pp))
            self.assertTrue(res.get("ok"), res)
            self.assertEqual(
                os.path.abspath(os.path.dirname(res["index"])),
                os.path.abspath(default_report_dir(pp)))
            with open(res["index"], "r", encoding="utf-8") as f:
                return res["index"], f.read(3000)

        with tempfile.TemporaryDirectory() as tmp:
            pa = os.path.join(tmp, "projA")
            pb = os.path.join(tmp, "projB")
            os.makedirs(pa, exist_ok=True)
            os.makedirs(pb, exist_ok=True)
            idx_a, head_a = render(pa)
            idx_b, head_b = render(pb)
            self.assertIn("项目评估台 - projA", head_a)
            self.assertIn("项目评估台 - projB", head_b)
            # B 渲染后 A 的入口仍在且未被改成 B
            idx_a2, head_a2 = render(pa)
            self.assertEqual(head_a, head_a2)
            self.assertTrue(os.path.isfile(idx_b))
            # interpret(dashboard) 各自指向本项目的 preferred_entry
            ip = InterpretationPlatform()
            for pp, idx in ((pa, idx_a2), (pb, idx_b)):
                entry = ip._action_dashboard(pp).get("preferred_entry") or ""
                self.assertEqual(os.path.abspath(entry), os.path.abspath(idx))


class EvalCommonParityTest(unittest.TestCase):
    """5.18.2：F 轨与 U 轨共用同一套公共规则（相对路径 / 锚点 / JSON / 落盘）。"""

    def test_rel_delegates_to_common(self):
        from core import product_eval, ux_eval
        from core.eval_common import rel
        for p in ("core/a.py", "src/b.js"):
            full = os.path.join("proj", p)
            want = rel("proj", full)
            self.assertEqual(product_eval._rel("proj", full), want)
            self.assertEqual(ux_eval._rel("proj", full), want)

    def test_parse_delegates_to_common(self):
        from core import product_eval, ux_eval
        from core.eval_common import parse_json_object
        raw = "```json\n{\"a\": 1}\n```"
        self.assertEqual(product_eval._parse_json(raw), parse_json_object(raw))
        self.assertEqual(ux_eval._parse_json(raw), parse_json_object(raw))
        self.assertIsNone(product_eval._parse_json("not json"))

    def test_anchor_exists_shared(self):
        from core import product_eval, ux_eval
        with tempfile.TemporaryDirectory() as tmp:
            fp = os.path.join(tmp, "m.py")
            open(fp, "w", encoding="utf-8").close()
            self.assertTrue(product_eval._anchor_exists(tmp, "m.py#L3"))
            self.assertTrue(ux_eval._anchor_exists(tmp, "m.py#L3"))
            self.assertFalse(ux_eval._anchor_exists(tmp, "nope.py#L1"))

    def test_write_result_keeps_own_filename(self):
        from core import product_eval, ux_eval
        with tempfile.TemporaryDirectory() as tmp:
            p = product_eval.write_result(tmp, {"a": 1})
            u = ux_eval.write_result(tmp, {"b": 2})
            self.assertTrue(p.endswith("product_fit.json"), p)
            self.assertTrue(u.endswith("ux_fit.json"), u)
            self.assertTrue(os.path.isfile(p) and os.path.isfile(u))


class ExternalAuditFixTest(unittest.TestCase):
    """外部审计反馈（kuajingdianshang）对应修复的回归用例。"""

    def test_verify_findings_file_only_verdict(self):
        from core.verify_findings import EvidenceLabeler, VERDICT_FILE_ONLY
        verdict, reason = EvidenceLabeler.decide(
            has_ref=True, file_found=True, symbols_found=[],
            symbols_missing=[], graph_exists=True)
        self.assertEqual(verdict, VERDICT_FILE_ONLY, reason)

    def test_change_guard_ignores_non_code_diff(self):
        from core.change_guard import ChangeGuard
        diff = (
            "diff --git a/README.md b/README.md\n"
            "index 0000000..1111111 100644\n"
            "--- a/README.md\n+++ b/README.md\n@@ -1,1 +1,1 @@\n"
            "-validate(input)\n+plain text\n")
        with tempfile.TemporaryDirectory() as tmp:
            open(os.path.join(tmp, "README.md"), "w", encoding="utf-8").write("plain text\n")
            res = ChangeGuard().guard(tmp, diff=diff)
            self.assertEqual(res["findings"], [], res["summary"])

    def test_frontend_excludes_tool_output_html(self):
        from core.frontend_inspector import FrontendInspector
        with tempfile.TemporaryDirectory() as tmp:
            for rel in (".coderef/panel.html", "src/page.html"):
                p = os.path.join(tmp, rel)
                os.makedirs(os.path.dirname(p), exist_ok=True)
                open(p, "w", encoding="utf-8").write("<html><body><button>x</button></body></html>")
            files = FrontendInspector()._find_html_files(tmp, None)
            self.assertEqual(files, [os.path.join(tmp, "src", "page.html")], files)

    def test_interpret_prefers_project_side_findings(self):
        import hashlib
        from core.interpretation_platform import _load_audit_findings
        from core.path_key import project_key
        with tempfile.TemporaryDirectory() as tmp:
            # 产物名与实现同源：走 project_key 归一（U-71：盘符/大小写变体同库）
            ph = hashlib.md5(project_key(tmp).encode()).hexdigest()[:12]
            os.makedirs(os.path.join(tmp, ".coderef"), exist_ok=True)
            os.makedirs(os.path.join(tmp, "coderef-report"), exist_ok=True)
            new = os.path.join(tmp, ".coderef", f"audit_findings_{ph}.json")
            old = os.path.join(tmp, "coderef-report", f"audit_findings_{ph}.json")
            open(new, "w", encoding="utf-8").write(
                '{"scan_ts":"2026-11-09 09:00:00","total_files":10,"findings":[]}')
            open(old, "w", encoding="utf-8").write(
                '{"scan_ts":"2026-09-09 11:05:37","total_files":10,"findings":[]}')
            data = _load_audit_findings(os.path.abspath(tmp))
            self.assertIn(new.replace("/", os.sep), data["_source_path"].replace("/", os.sep))

    def test_llm_config_supports_user_level_path(self):
        from core.llm_integration import _load_llm_config_from_settings
        with tempfile.TemporaryDirectory() as tmp:
            cfg = os.path.join(tmp, "config.json")
            open(cfg, "w", encoding="utf-8").write(
                '{"llm_api_key":"sk-test-only","llm_provider":"deepseek"}')
            old = os.environ.get("CODEREF_API_KEY")
            os.environ.pop("CODEREF_API_KEY", None)
            os.environ["CODEREF_CONFIG_PATH"] = cfg
            try:
                conf = _load_llm_config_from_settings()
                self.assertEqual(conf.api_key, "sk-test-only")
            finally:
                os.environ.pop("CODEREF_CONFIG_PATH", None)
                if old is not None:
                    os.environ["CODEREF_API_KEY"] = old


# ═══════════════════════════════════════════════════════════════════
# 外部审计第二轮（v5.18.7）：D-24 假边 / D-25 私有函数 / UI P0 / D-23 gitignore
# ═══════════════════════════════════════════════════════════════════
class PrivateTopLevelFunctionTest(unittest.TestCase):
    """D-25 · 模块级私有函数必须进 CodeFile.functions（进而进图谱）。"""

    SRC = (
        "def _run_capability_job(params):\n"
        "    return params\n"
        "\n"
        "def public_job():\n"
        "    return _run_capability_job(1)\n"
        "\n"
        "def __dunder_helper__():\n"
        "    return 1\n"
        "\n"
        "class Handler:\n"
        "    def _handle_capability_run(self):\n"
        "        return 1\n"
    )

    def test_private_top_level_function_collected(self):
        from core.ca_parse import _parse_python_file_content
        with tempfile.TemporaryDirectory() as tmp:
            fp = os.path.join(tmp, "web_gui.py")
            cf = _parse_python_file_content(self.SRC, fp, tmp)
        names = {f.name for f in cf.functions}
        # 私有顶层函数必须收录（外部实测 600 个全丢）
        self.assertIn("_run_capability_job", names)
        self.assertIn("public_job", names)
        # dunder 不是业务符号；私有方法只应出现在 classes 里，不重复进 functions
        self.assertNotIn("__dunder_helper__", names)
        self.assertNotIn("_handle_capability_run", names)
        handler = [c for c in cf.classes if c.name == "Handler"][0]
        self.assertIn("_handle_capability_run", {m.name for m in handler.methods})


class CommonNameFakeCallTest(unittest.TestCase):
    """D-24 · params.get / json.dump 这类点号调用不得吸到同名 helper 函数。"""

    SRC = (
        "import json\n"
        "\n"
        "\n"
        "class Runner:\n"
        "    def handle(self, params):\n"
        "        params.get('x')\n"
        "        json.dump({}, None)\n"
        "        self._step()\n"
        "\n"
        "    def _step(self):\n"
        "        return 1\n"
    )

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.proj = self._tmp.name
        with open(os.path.join(self.proj, "main.py"), "w", encoding="utf-8") as fh:
            fh.write(self.SRC)
        # 两个"吸边磁铁"：模块级同名 helper（外部实测 check 7,799 / get 5,730）
        with open(os.path.join(self.proj, "helpers.py"), "w", encoding="utf-8") as fh:
            fh.write("def get(k):\n    return k\n\ndef dump(v):\n    return v\n")
        self.db = os.path.join(self.proj, "kg.db")
        self.kg = CodeKnowledgeGraph(self.proj, db_path=self.db)
        self.kg._connect()
        self.kg._init_schema()
        self.kg._exclude_dirs = []
        main_mod = _module_key(self.proj, "main.py")
        help_mod = _module_key(self.proj, "helpers.py")
        self.handle = f"method:{main_mod}:Runner.handle"
        self.step = f"method:{main_mod}:Runner._step"
        self.get_fn = f"func:{help_mod}:get"
        self.dump_fn = f"func:{help_mod}:dump"
        self.kg._upsert_node(KGNode(
            id=self.handle, type="method", name="Runner.handle",
            file_path="main.py", start_line=5, end_line=9))
        self.kg._upsert_node(KGNode(
            id=self.step, type="method", name="Runner._step",
            file_path="main.py", start_line=10, end_line=11))
        self.kg._upsert_node(KGNode(
            id=self.get_fn, type="function", name="get",
            file_path="helpers.py", start_line=1, end_line=2))
        self.kg._upsert_node(KGNode(
            id=self.dump_fn, type="function", name="dump",
            file_path="helpers.py", start_line=4, end_line=5))

    def tearDown(self):
        self.kg.close()
        self._tmp.cleanup()

    def test_common_attribute_calls_build_no_fake_edge(self):
        parser = AstParser(project_root=self.proj)
        r = parser.parse(os.path.join(self.proj, "main.py"))
        self.kg._build_from_ast({"main.py": r}, {"nodes": 0, "edges": 0})
        calls = {(e[1].source, e[1].target)
                 for e in self.kg.get_all_edges() if e[1].type == "CALLS"}
        # self._step() 仍要解析到同类方法（不能为了降假边把真边也丢光）
        self.assertIn((self.handle, self.step), calls)
        # params.get / json.dump 不许连到 helpers.py 的同名函数
        self.assertNotIn((self.handle, self.get_fn), calls)
        self.assertNotIn((self.handle, self.dump_fn), calls)


class ScorecardHonestyTest(unittest.TestCase):
    """UI P0 · 治理/对齐读数不得再用「旧库满分」「旧定标 1 分」冒充现状。"""

    def _fake_governance(self, counts):
        store = mock.Mock()
        store.list_issues.side_effect = lambda **kw: []
        store.status_counts.return_value = counts
        return store

    def test_all_rejected_governance_is_unknown(self):
        from core.scorecard import _dim_governance
        with tempfile.TemporaryDirectory() as tmp:
            db = os.path.join(tmp, "governance.db")
            open(db, "w", encoding="utf-8").write("")
            with mock.patch("core.governance_store.gov_db_path", return_value=db), \
                    mock.patch("core.governance_store.GovernanceStore",
                               return_value=self._fake_governance({"Rejected": 24})):
                got = _dim_governance(tmp)
        self.assertEqual(got["status"], "unknown")
        self.assertIsNone(got["score"])
        self.assertEqual(got["raw"]["rejected"], 24)

    def test_stale_governance_db_is_unknown(self):
        from core.scorecard import _dim_governance
        with tempfile.TemporaryDirectory() as tmp:
            db = os.path.join(tmp, "governance.db")
            open(db, "w", encoding="utf-8").write("")
            old = 40 * 86400
            os.utime(db, (time.time() - old, time.time() - old))
            with mock.patch("core.governance_store.gov_db_path", return_value=db), \
                    mock.patch("core.governance_store.GovernanceStore",
                               return_value=self._fake_governance({"Archived": 1})):
                got = _dim_governance(tmp)
        self.assertEqual(got["status"], "unknown")
        self.assertGreater(got["raw"]["db_age_days"], 30)

    def test_stale_target_arch_alignment_is_unknown(self):
        from core.scorecard import _dim_alignment
        with tempfile.TemporaryDirectory() as tmp:
            os.makedirs(os.path.join(tmp, ".coderef"), exist_ok=True)
            with open(os.path.join(tmp, ".coderef", "target_arch.json"), "w",
                      encoding="utf-8") as fh:
                fh.write('{"modules": ["a"]}')
            fake = {"alignment": {"module_assigned": 0.03, "role_coverage": 0.0},
                    "gaps": [], "summary": "ok",
                    "target_health": {"specs_total": 41, "specs_missing": 41,
                                      "missing_ratio": 1.0, "stale": True,
                                      "warning": "定标可能已过期"}}
            with mock.patch("core.arch_gap_analyzer.analyze_gap", return_value=fake):
                got = _dim_alignment(tmp)
        self.assertEqual(got["status"], "unknown")
        self.assertIsNone(got["score"])
        self.assertTrue(got["raw"]["target_stale"])


class RendererStaleCanvasTest(unittest.TestCase):
    """UI P0 · 概览不得把 7 天前的旧画布当成"当前架构"链接。"""

    def test_old_canvas_not_linked(self):
        from core.report_renderer import HtmlReportRenderer
        with tempfile.TemporaryDirectory() as tmp:
            os.makedirs(os.path.join(tmp, ".coderef"), exist_ok=True)
            old = os.path.join(tmp, ".coderef", "arch_canvas_20260918_101500.html")
            open(old, "w", encoding="utf-8").write("<html></html>")
            stale = time.time() - 10 * 86400
            os.utime(old, (stale, stale))
            out = os.path.join(tmp, ".coderef", "report", "html")
            os.makedirs(out, exist_ok=True)
            link = HtmlReportRenderer(tmp)._latest_arch_link(out)
        self.assertEqual(link, "")

    def test_fresh_canvas_is_linked(self):
        from core.report_renderer import HtmlReportRenderer
        with tempfile.TemporaryDirectory() as tmp:
            os.makedirs(os.path.join(tmp, ".coderef"), exist_ok=True)
            fp = os.path.join(tmp, ".coderef", "arch_canvas_20261109_100000.html")
            open(fp, "w", encoding="utf-8").write("<html></html>")
            out = os.path.join(tmp, ".coderef", "report", "html")
            os.makedirs(out, exist_ok=True)
            link = HtmlReportRenderer(tmp)._latest_arch_link(out)
        self.assertTrue(link.endswith("arch_canvas_20261109_100000.html"), link)


class CoderefGitignoreTest(unittest.TestCase):
    """D-23 · 首次产出报告时自动建 .coderef/.gitignore，运行时产物默认不跟踪。"""

    def test_gitignore_created_once_and_not_overwritten(self):
        from core.report_renderer import ensure_coderef_gitignore
        with tempfile.TemporaryDirectory() as tmp:
            d = os.path.join(tmp, ".coderef")
            os.makedirs(d, exist_ok=True)
            fp = os.path.join(d, ".gitignore")
            # 用户已有自定义 .gitignore → 不覆盖
            open(fp, "w", encoding="utf-8").write("# mine\n")
            ensure_coderef_gitignore(tmp)
            self.assertEqual(open(fp, encoding="utf-8").read(), "# mine\n")

        with tempfile.TemporaryDirectory() as tmp2:
            ensure_coderef_gitignore(tmp2)
            fp2 = os.path.join(tmp2, ".coderef", ".gitignore")
            self.assertTrue(os.path.isfile(fp2))
            body = open(fp2, encoding="utf-8").read()
            self.assertIn("report/", body)
            self.assertIn("scorecard.json", body)
            # 设计/配置类产物刻意保留（随项目提交）
            self.assertNotIn("target_arch.json", body)
            self.assertNotIn("whitelist", body)


class ScanScopeUnificationTest(unittest.TestCase):
    """v5.18.8 · 扫描口径统一：whitelist dir 生效 + 扁平/命名空间导入不再误报缺失。"""

    def test_path_hits_excluded_dir_segments(self):
        from core.pr_whitelist import path_hits_excluded_dir
        self.assertTrue(path_hits_excluded_dir(
            "_refactor_backup/a.py", ["_refactor_backup"]))
        self.assertTrue(path_hits_excluded_dir(
            "chonggou/_refactor_backup/a.py", ["_refactor_backup"]))
        # 同名子串不是命中（backup_service 不等于 _refactor_backup）
        self.assertFalse(path_hits_excluded_dir(
            "src/_refactor_backup_service/a.py", ["_refactor_backup"]))
        # 多段排除按相对前缀匹配
        self.assertTrue(path_hits_excluded_dir(
            "主干/v1_工作台_archive/技能库/a.py", ["v1_工作台_archive/技能库"]))

    def _make_project(self, tmp: str):
        # 子工程目录 + 无 __init__.py 的命名空间包
        os.makedirs(os.path.join(tmp, "chonggou", "入口与启动"), exist_ok=True)
        open(os.path.join(tmp, "chonggou", "入口与启动", "web_gui.py"),
             "w", encoding="utf-8").write("def run():\n    return 1\n")
        os.makedirs(os.path.join(tmp, "chonggou", "市场层"), exist_ok=True)
        open(os.path.join(tmp, "chonggou", "市场层", "market_registry.py"),
             "w", encoding="utf-8").write("def load():\n    return 2\n")
        os.makedirs(os.path.join(tmp, "chonggou", "测试"), exist_ok=True)
        # 扁平导入（子目录加入 sys.path 后 import web_gui）+ 命名空间包导入
        open(os.path.join(tmp, "chonggou", "测试", "diag.py"),
             "w", encoding="utf-8").write(
                 "import web_gui\nimport 市场层.market_registry\n")
        # 备份目录里的坏 import 不应被计入（whitelist dir 排除）
        os.makedirs(os.path.join(tmp, "_refactor_backup"), exist_ok=True)
        open(os.path.join(tmp, "_refactor_backup", "old.py"),
             "w", encoding="utf-8").write("import totally_missing_mod\n")

    def test_resource_gap_resolves_flat_and_namespace_imports(self):
        from core.resource_gap_detector import ResourceGapDetector
        with tempfile.TemporaryDirectory() as tmp:
            self._make_project(tmp)
            with mock.patch("core.pr_whitelist.whitelist_exclude_dirs",
                            return_value=["_refactor_backup"]):
                det = ResourceGapDetector()
                det.detect(tmp)
                miss = [g.item for g in det._gaps if g.category == "missing_module"]
        self.assertEqual(miss, [], miss)

    def test_blind_spot_resolves_flat_imports(self):
        from core.blind_spot_detector import BlindSpotDetector
        with tempfile.TemporaryDirectory() as tmp:
            self._make_project(tmp)
            with mock.patch("core.pr_whitelist.whitelist_exclude_dirs",
                            return_value=["_refactor_backup"]):
                det = BlindSpotDetector()
                det.detect(tmp)
                deps = [s.item for s in det.spots
                        if s.category == "missing_dependency"]
        self.assertEqual(deps, [], deps)

    def test_prompt_extractor_skips_whitelist_dirs(self):
        from core.prompt_extractor import PromptExtractor
        with tempfile.TemporaryDirectory() as tmp:
            os.makedirs(os.path.join(tmp, "prompts"), exist_ok=True)
            open(os.path.join(tmp, "prompts", "system.md"),
                 "w", encoding="utf-8").write("# system\nYou are a helpful bot.\n")
            os.makedirs(os.path.join(tmp, "_refactor_backup"), exist_ok=True)
            open(os.path.join(tmp, "_refactor_backup", "old.md"),
                 "w", encoding="utf-8").write("# old\nYou are a legacy bot.\n")
            with mock.patch("core.pr_whitelist.whitelist_exclude_dirs",
                            return_value=["_refactor_backup"]):
                r = PromptExtractor().extract_from_project(tmp)
        self.assertEqual(r.total_files_scanned, 1)
        self.assertFalse([p for p in r.prompts
                          if "_refactor_backup" in (p.file_path or "")])


class ArchAuditScopeLabelTest(unittest.TestCase):
    """v5.18.8 · D-9 模块数口径显式化（保留旧键，新增可复核口径）。"""

    def test_scope_labels_present(self):
        from core.arch_audit import audit
        from core.code_knowledge_graph import KGEdge
        with tempfile.TemporaryDirectory() as tmp:
            db = os.path.join(tmp, "kg.db")
            kg = CodeKnowledgeGraph(tmp, db_path=db)
            kg._connect()
            kg._init_schema()
            mods = [("mod:a", "a"), ("mod:b", "b"), ("mod:c", "c")]
            for nid, name in mods:
                kg._upsert_node(KGNode(id=nid, type="module", name=name,
                                       file_path=f"{name}.py", start_line=1, end_line=1))
            kg._upsert_node(KGNode(id="func:a:f", type="function", name="f",
                                   file_path="a.py", start_line=1, end_line=2))
            kg._upsert_node(KGNode(id="func:b:g", type="function", name="g",
                                   file_path="b.py", start_line=1, end_line=2))
            kg._upsert_edge(KGEdge(source="func:a:f", target="func:b:g", type="CALLS"))
            kg._conn.commit()
            kg.close()
            r = audit(tmp, db_path=db)
        gs = r["graph_stats"]
        self.assertIn("modules_total", gs)
        self.assertIn("modules_in_call_graph", gs)
        self.assertIn("scope_note", gs)
        # a/b 参与调用，c 无调用 → total 应大于 call-graph 口径
        self.assertEqual(gs["modules_total"], 3)
        self.assertLess(gs["modules"], gs["modules_total"])


class VersionFreshnessReportTest(unittest.TestCase):
    """v5.18.9 · coderef_version 可选返回派生产物新鲜度快报（陈旧输入防呆）。"""

    def test_version_without_path_stays_minimal(self):
        from core.handlers_misc import _version
        out = json.loads(_version({}))
        self.assertEqual(out["tool"], "coderef_version")
        self.assertNotIn("artifacts_report", out)

    def test_artifacts_report_flags_old_artifact_and_newer_code(self):
        from core.artifact_freshness import report
        with tempfile.TemporaryDirectory() as tmp:
            os.makedirs(os.path.join(tmp, ".coderef"), exist_ok=True)
            ta = os.path.join(tmp, ".coderef", "target_arch.json")
            open(ta, "w", encoding="utf-8").write("{}")
            old = time.time() - 10 * 86400
            os.utime(ta, (old, old))
            os.makedirs(os.path.join(tmp, "app"), exist_ok=True)
            open(os.path.join(tmp, "app", "main.py"), "w",
                 encoding="utf-8").write("x = 1\n")
            r = report(tmp)
        item = [i for i in r["artifacts"] if i["key"] == "target_arch"][0]
        self.assertTrue(item["exists"])
        self.assertGreater(item["age_days"], 9)
        # 代码比产物新 → 产物可能没覆盖最新代码
        self.assertTrue(item["code_newer"])
        self.assertTrue(r["latest_code_mtime"])

    def test_version_handler_includes_artifacts_when_path_given(self):
        from core.handlers_misc import _version
        with tempfile.TemporaryDirectory() as tmp:
            os.makedirs(os.path.join(tmp, ".coderef"), exist_ok=True)
            open(os.path.join(tmp, ".coderef", "scorecard.json"), "w",
                 encoding="utf-8").write("{}")
            out = json.loads(_version({"project_path": tmp}))
        self.assertIn("artifacts_report", out)
        keys = {i["key"] for i in out["artifacts_report"]["artifacts"]}
        self.assertIn("scorecard", keys)
        self.assertIn("kg_db", keys)

    def test_missing_project_path_returns_empty_report(self):
        from core.artifact_freshness import report
        missing = os.path.join(tempfile.gettempdir(),
                               "coderef_no_such_%d" % int(time.time() * 1000))
        r = report(missing)
        self.assertEqual(r["artifacts"], [])
        self.assertIn("不存在", r["note"])


class ReviewRound2FixTest(unittest.TestCase):
    """v5.18.10 · 三路复审 7 条缺陷的回归。"""

    def test_scorecard_findings_stale_uses_json_path(self):
        """fp 不得被循环变量覆盖：最后一条发现无路径时仍能判断过期。"""
        from core.scorecard import _dim_findings
        with tempfile.TemporaryDirectory() as tmp:
            open(os.path.join(tmp, "app.py"), "w", encoding="utf-8").write("x = 1\n")
            jp = os.path.join(tmp, "audit.json")
            open(jp, "w", encoding="utf-8").write(json.dumps({
                "findings": [{"file_path": "", "tier": "high"}],
                "total_files": 1, "scan_ts": "2026-11-01 00:00:00"}))
            old = time.time() - 10 * 86400
            os.utime(jp, (old, old))
            with mock.patch("core.scorecard.find_findings_json", return_value=jp):
                d = _dim_findings(tmp)
        self.assertTrue(d["raw"]["stale"], d["raw"])

    def test_scorecard_keeps_findings_in_cache_subpackage(self):
        """路径过滤按段判定：src/cache 是真实包，不能被子串 cache/ 误排除。"""
        from core.scorecard import _dim_findings
        with tempfile.TemporaryDirectory() as tmp:
            jp = os.path.join(tmp, "audit.json")
            open(jp, "w", encoding="utf-8").write(json.dumps({
                "findings": [
                    {"file_path": "src/cache/hit.py", "tier": "high"},
                    {"file_path": ".coderef/report/x.py", "tier": "high"},
                ],
                "total_files": 2, "scan_ts": "2026-11-09 00:00:00"}))
            with mock.patch("core.scorecard.find_findings_json", return_value=jp):
                d = _dim_findings(tmp)
        self.assertEqual(d["raw"]["high"], 1, d["raw"])

    def test_interpret_unknown_age_is_not_fresh(self):
        """scan_ts 缺失时 data_stale 必须为 None（未知），不能报告为新鲜。"""
        from core.interpretation_platform import InterpretationPlatform
        with tempfile.TemporaryDirectory() as tmp:
            data = {"findings": [], "scan_ts": None, "total_files": 1}
            with mock.patch("core.interpretation_platform._load_audit_findings",
                            return_value=data):
                d = InterpretationPlatform()._action_health(tmp)
        self.assertIsNone(d.get("data_age_days"))
        self.assertIsNone(d.get("data_stale"))

    def test_maturity_keeps_nested_generic_dir_and_skips_whitelist(self):
        """lib/data/cache 只在项目根排除；whitelist dir 内的内容不参与扫描。"""
        from core.project_maturity_checker import ProjectMaturityChecker
        with tempfile.TemporaryDirectory() as tmp:
            os.makedirs(os.path.join(tmp, "src", "data"), exist_ok=True)
            open(os.path.join(tmp, "src", "data", "health.py"), "w",
                 encoding="utf-8").write("def health_check():\n    return 1\n")
            os.makedirs(os.path.join(tmp, "cache"), exist_ok=True)
            open(os.path.join(tmp, "cache", "old.py"), "w",
                 encoding="utf-8").write("def health_check():\n    return 1\n")
            os.makedirs(os.path.join(tmp, "_refactor_backup"), exist_ok=True)
            open(os.path.join(tmp, "_refactor_backup", "old.py"), "w",
                 encoding="utf-8").write("def health_check():\n    return 1\n")
            with mock.patch("core.pr_whitelist.whitelist_exclude_dirs",
                            return_value=["_refactor_backup"]):
                ck = ProjectMaturityChecker()
                nested = ck._scan_content(tmp, r"def\s+health_check")
                # 重置预算后再单独验证根目录与备份目录
                ck._scan_deadline = None
        self.assertTrue(nested)
        with tempfile.TemporaryDirectory() as tmp2:
            os.makedirs(os.path.join(tmp2, "cache"), exist_ok=True)
            open(os.path.join(tmp2, "cache", "old.py"), "w",
                 encoding="utf-8").write("def health_check():\n    return 1\n")
            ck = ProjectMaturityChecker()
            self.assertFalse(ck._scan_content(tmp2, r"def\s+health_check"))

    def test_llm_config_skips_broken_file(self):
        """坏掉的用户级配置不应终止后续候选（工具仓/家目录）的查找。"""
        from core.llm_integration import _load_llm_config_from_settings
        with tempfile.TemporaryDirectory() as tmp:
            bad = os.path.join(tmp, "bad.json")
            open(bad, "w", encoding="utf-8").write("{not json")
            home = os.path.join(tmp, "home")
            os.makedirs(os.path.join(home, ".coderef"), exist_ok=True)
            good = os.path.join(home, ".coderef", "config.json")
            open(good, "w", encoding="utf-8").write(
                '{"llm_api_key":"sk-good","llm_provider":"deepseek"}')
            old_env = os.environ.get("CODEREF_API_KEY")
            os.environ.pop("CODEREF_API_KEY", None)
            os.environ["CODEREF_CONFIG_PATH"] = bad
            try:
                with mock.patch("core.llm_integration.os.path.expanduser",
                                return_value=home):
                    conf = _load_llm_config_from_settings()
            finally:
                os.environ.pop("CODEREF_CONFIG_PATH", None)
                if old_env is not None:
                    os.environ["CODEREF_API_KEY"] = old_env
        self.assertEqual(conf.api_key, "sk-good")

    def test_project_root_name_is_not_local_module(self):
        """仓库名与依赖同名时，不能把依赖误判为本地模块。"""
        from core.blind_spot_detector import BlindSpotDetector
        from core.resource_gap_detector import ResourceGapDetector
        with tempfile.TemporaryDirectory() as tmp:
            proj = os.path.join(tmp, "zz_fake_pkg_local")
            os.makedirs(proj, exist_ok=True)
            open(os.path.join(proj, "app.py"), "w", encoding="utf-8").write(
                "import zz_fake_pkg_local\n")
            det = BlindSpotDetector()
            det.detect(proj)
            deps = [s.item for s in det.spots if s.category == "missing_dependency"]
            rg = ResourceGapDetector()
            rg.detect(proj)
            miss = [g.item for g in rg._gaps if g.category == "missing_module"]
        self.assertIn("zz_fake_pkg_local", deps, deps)
        self.assertIn("zz_fake_pkg_local", miss, miss)

    def test_output_paths_is_neutral_module(self):
        """路径规则可在编排层直接使用，不再经渲染器转发。"""
        from core.output_paths import default_report_root, ensure_coderef_gitignore
        with tempfile.TemporaryDirectory() as tmp:
            ensure_coderef_gitignore(tmp)
            fp = os.path.join(tmp, ".coderef", ".gitignore")
            self.assertTrue(os.path.isfile(fp))
        self.assertTrue(default_report_root(tmp).replace(os.sep, "/")
                        .endswith(".coderef/report"))


class ExternalRound3FixTest(unittest.TestCase):
    """v5.18.11 · 外部第三轮：CALLS 作用域、iframe 404、定标 stale、口径解释。"""

    SRC = (
        "from datetime import datetime\n"
        "\n"
        "class FakeDatetime:\n"
        "    @staticmethod\n"
        "    def now():\n"
        "        return 1\n"
        "\n"
        "def check(x):\n"
        "    return x\n"
        "\n"
        "def run():\n"
        "    check(1)\n"
        "    datetime.now()\n"
        "\n"
        "def run2():\n"
        "    from helpers import check as hcheck\n"
        "    hcheck(2)\n"
    )

    def _build_kg(self, tmp):
        db = os.path.join(tmp, "kg.db")
        kg = CodeKnowledgeGraph(tmp, db_path=db)
        kg._connect()
        kg._init_schema()
        kg._exclude_dirs = []
        for name, path in (("main", "main.py"), ("helpers", "helpers.py")):
            open(os.path.join(tmp, path), "w", encoding="utf-8").write(
                self.SRC if name == "main" else "def check(x):\n    return x\n")
            kg._upsert_node(KGNode(id=f"mod:{name}", type="module", name=name,
                                   file_path=path, start_line=1, end_line=1))
        nodes = [
            ("func:main:check", "function", "check", "main.py", 8, 9),
            ("func:main:run", "function", "run", "main.py", 11, 13),
            ("func:main:run2", "function", "run2", "main.py", 15, 17),
            ("func:helpers:check", "function", "check", "helpers.py", 1, 2),
            ("method:main:FakeDatetime.now", "method", "FakeDatetime.now",
             "main.py", 4, 6),
        ]
        for nid, typ, name, fp, s, e in nodes:
            kg._upsert_node(KGNode(id=nid, type=typ, name=name, file_path=fp,
                                   start_line=s, end_line=e))
        return kg

    def test_calls_are_import_scoped(self):
        from core.ast_parser import AstParser
        with tempfile.TemporaryDirectory() as tmp:
            kg = self._build_kg(tmp)
            try:
                ar = AstParser(project_root=tmp).parse(os.path.join(tmp, "main.py"))
                kg._build_from_ast({"main.py": ar}, {"nodes": 0, "edges": 0})
                calls = {(e[1].source, e[1].target)
                         for e in kg.get_all_edges() if e[1].type == "CALLS"}
            finally:
                kg.close()
        # 同模块 check 优先；绝不连到 helpers 的同名 check
        self.assertIn(("func:main:run", "func:main:check"), calls)
        self.assertNotIn(("func:main:run", "func:helpers:check"), calls)
        # datetime.now 是标准库属性，不能连到项目里的 FakeDatetime.now
        self.assertNotIn(("func:main:run", "method:main:FakeDatetime.now"), calls)
        # 明确 from helpers import check as hcheck 才允许跨模块建边
        self.assertIn(("func:main:run2", "func:helpers:check"), calls)

    def test_canvas_copied_into_report_dir_for_http(self):
        from core.report_renderer import HtmlReportRenderer
        with tempfile.TemporaryDirectory() as tmp:
            os.makedirs(os.path.join(tmp, ".coderef"), exist_ok=True)
            fp = os.path.join(tmp, ".coderef", "arch_canvas_20261109_100000.html")
            open(fp, "w", encoding="utf-8").write("<html></html>")
            out = os.path.join(tmp, ".coderef", "report", "html")
            link = HtmlReportRenderer(tmp)._latest_arch_link(out)
            self.assertEqual(link, "arch_canvas_20261109_100000.html")
            self.assertTrue(os.path.isfile(os.path.join(out, link)))

    def test_arch_verify_marks_stale_target(self):
        from core.arch_alignment_verifier import ArchAlignmentVerifier
        with tempfile.TemporaryDirectory() as tmp:
            db = os.path.join(tmp, "kg.db")
            open(db, "w", encoding="utf-8").write("")
            with mock.patch("core.arch_alignment_verifier.locate_kg_db",
                            return_value=db), \
                    mock.patch("core.arch_alignment_verifier.analyze_gap",
                               return_value={"gaps": [],
                                             "target_health": {"stale": True,
                                                               "warning": "定标过期"}}), \
                    mock.patch("core.arch_alignment_verifier.load_graph",
                               return_value=({}, {})), \
                    mock.patch.object(ArchAlignmentVerifier, "_score_dimensions",
                                      return_value={
                                          "responsibility": {"score": 1},
                                          "dependency": {"score": 1},
                                          "business": {"score": 1},
                                          "health": {"score": 1}}):
                r = ArchAlignmentVerifier().verify(tmp, target_arch={"roles": []})
        self.assertEqual(r["score_status"], "stale_target")
        self.assertIn("定标过期", r["score_note"])

    def test_scorecard_structure_exposes_module_total(self):
        from core.scorecard import _dim_structure
        with tempfile.TemporaryDirectory() as tmp:
            fake = {"graph_stats": {"has_kg": True, "modules": 10,
                                    "modules_total": 20},
                    "summary": {"health": 3.0, "cycles": 0, "god_modules": 0,
                                "layer_violations": 0, "large_modules": 0,
                                "function_recursions": 0}}
            with mock.patch("core.arch_audit.audit", return_value=fake), \
                    mock.patch("core.scorecard._exclude_dirs", return_value=[]), \
                    mock.patch("core.arch_audit.locate_kg_db", return_value=None):
                d = _dim_structure(tmp)
        self.assertEqual(d["raw"]["modules_total"], 20)


class ResultEnvelopeTest(unittest.TestCase):
    """v5.18.12 · 结论信封：归属/限制/不可下结论项（给调用方 AI 转述用）。"""

    def test_version_returns_consumer_contract(self):
        from core.handlers_misc import _version
        out = json.loads(_version({}))
        cc = out.get("consumer_contract") or {}
        self.assertEqual(len(cc.get("must_read") or []), 3)
        ids = {x["id"] for x in cc.get("known_limitations") or []}
        self.assertIn("KL-01", ids)
        self.assertIn("KL-03", ids)
        self.assertIn("envelope_fields", cc)

    def test_gap_stale_envelope_assigns_consumer(self):
        from core.result_envelope import for_arch_gap
        env = for_arch_gap({"target_health": {"stale": True,
                                              "warning": "定标可能已过期"}})
        self.assertFalse(env["usable_for_decisions"])
        self.assertEqual(env["blocked_by"][0]["owner"], "consumer")
        self.assertIn("coderef_target_arch_set", env["owner_actions"]["consumer"])
        self.assertTrue(env["cannot_conclude"])
        self.assertIn("KL-01", env["limitations"])

    def test_verify_stale_status_envelope(self):
        from core.result_envelope import for_arch_verify
        env = for_arch_verify({"score_status": "stale_target",
                               "score_note": "定标过期"})
        self.assertFalse(env["usable_for_decisions"])
        self.assertEqual(env["blocked_by"][0]["id"], "stale_target_arch")

    def test_health_unknown_age_not_usable(self):
        from core.result_envelope import for_health
        env = for_health({"data_source": "project", "data_stale": None,
                          "score": 80, "score_label": "良好"})
        self.assertFalse(env["usable_for_decisions"])
        self.assertEqual(env["blocked_by"][0]["owner"], "consumer")
        fresh = for_health({"data_source": "project", "data_stale": False,
                            "score": 80, "score_label": "良好"})
        self.assertTrue(fresh["usable_for_decisions"])

    def test_audit_errors_mark_unusable(self):
        from core.result_envelope import for_audit
        env = for_audit(10, ["sca: boom"])
        self.assertFalse(env["usable_for_decisions"])
        self.assertEqual(env["blocked_by"][0]["owner"], "coderef")

    def test_gap_without_alignment_is_unusable(self):
        from core.result_envelope import for_arch_gap
        env = for_arch_gap({"alignment": {}, "summary": "无知识图谱"})
        self.assertFalse(env["usable_for_decisions"])
        self.assertEqual(env["blocked_by"][0]["id"], "gap_not_run")
        self.assertTrue(env["cannot_conclude"])

    def test_verify_not_run_is_unusable(self):
        from core.result_envelope import for_arch_verify
        env = for_arch_verify({"ok": False, "score": 0.0,
                               "message": "知识图谱不存在"})
        self.assertFalse(env["usable_for_decisions"])
        self.assertEqual(env["blocked_by"][0]["id"], "verify_not_run")

    def test_scan_and_report_errors_mark_unusable(self):
        from core.result_envelope import for_report, for_scan
        self.assertFalse(for_scan(0, "sec", ["boom"])["usable_for_decisions"])
        self.assertFalse(for_report(True, "", ["boom"])["usable_for_decisions"])
        # 空错误串不能产生"失败："的空原因
        self.assertIn("未知错误", for_report(False, "")["verdict"])


class ReviewRound3FixTest(unittest.TestCase):
    """v5.18.13 · CodeRabbit 复审 4 条：whitelist 绕过 / 配置数值 / 信封错误态。"""

    def test_nested_generic_dir_still_honors_whitelist(self):
        from core.project_maturity_checker import ProjectMaturityChecker
        with tempfile.TemporaryDirectory() as tmp:
            os.makedirs(os.path.join(tmp, "src", "build"), exist_ok=True)
            open(os.path.join(tmp, "src", "build", "x.py"), "w",
                 encoding="utf-8").write("def health_check():\n    return 1\n")
            ck = ProjectMaturityChecker()
            self.assertTrue(ck._scan_content(tmp, r"def\s+health_check"))
            ck._scan_deadline = None
            with mock.patch("core.pr_whitelist.whitelist_exclude_dirs",
                            return_value=["src/build"]):
                self.assertFalse(ck._scan_content(tmp, r"def\s+health_check"))

    def test_bad_numeric_values_do_not_abort_config_search(self):
        from core.llm_integration import _load_llm_config_from_settings
        with tempfile.TemporaryDirectory() as tmp:
            cfg = os.path.join(tmp, "config.json")
            open(cfg, "w", encoding="utf-8").write(json.dumps({
                "llm_api_key": "sk-good",
                "llm_temperature": "not-a-number",
                "llm_max_tokens": "also-bad",
            }))
            old_env = os.environ.get("CODEREF_API_KEY")
            os.environ.pop("CODEREF_API_KEY", None)
            os.environ["CODEREF_CONFIG_PATH"] = cfg
            try:
                conf = _load_llm_config_from_settings()
            finally:
                os.environ.pop("CODEREF_CONFIG_PATH", None)
                if old_env is not None:
                    os.environ["CODEREF_API_KEY"] = old_env
        self.assertEqual(conf.api_key, "sk-good")
        self.assertEqual(conf.max_tokens, 4096)

    def test_non_dict_config_is_skipped(self):
        from core.llm_integration import _load_llm_config_from_settings
        with tempfile.TemporaryDirectory() as tmp:
            bad = os.path.join(tmp, "bad.json")
            open(bad, "w", encoding="utf-8").write('["not", "an", "object"]')
            home = os.path.join(tmp, "home")
            os.makedirs(os.path.join(home, ".coderef"), exist_ok=True)
            open(os.path.join(home, ".coderef", "config.json"), "w",
                 encoding="utf-8").write('{"llm_api_key":"sk-good"}')
            old_env = os.environ.get("CODEREF_API_KEY")
            os.environ.pop("CODEREF_API_KEY", None)
            os.environ["CODEREF_CONFIG_PATH"] = bad
            try:
                with mock.patch("core.llm_integration.os.path.expanduser",
                                return_value=home):
                    conf = _load_llm_config_from_settings()
            finally:
                os.environ.pop("CODEREF_CONFIG_PATH", None)
                if old_env is not None:
                    os.environ["CODEREF_API_KEY"] = old_env
        self.assertEqual(conf.api_key, "sk-good")


class SessionContextInjectionTest(unittest.TestCase):
    """5.19.x 地基：重工具返回自动附带变更守卫与记忆摘要。"""

    def test_merge_session_context_keeps_existing(self):
        from core.mcp_server import _merge_session_context
        raw = json.dumps({"status": "ok", "context": {"a": 1}})
        out = json.loads(_merge_session_context(raw, {"guard": {"findings": 0}}))
        self.assertEqual(out["context"]["a"], 1)
        self.assertIn("guard", out["context"])
        # 非 JSON / 非对象原样返回
        self.assertEqual(_merge_session_context("not-json", {"x": 1}), "not-json")
        self.assertEqual(_merge_session_context("[1,2]", {"x": 1}), "[1,2]")

    def test_heavy_tool_auto_attaches_guard_and_digest(self):
        from core.mcp_server import Server
        from types import SimpleNamespace
        dummy = SimpleNamespace(
            _run_inner=lambda n, a, progress_cb=None: json.dumps({"status": "ok"}))
        with mock.patch("core.session_guard.HEAVY_TOOLS", {"coderef_audit"}), \
                mock.patch("core.session_guard.guard_if_needed",
                           return_value={"status": "ok", "findings": 2, "high": 1}), \
                mock.patch("core.memory_digest.inject_once",
                           return_value={"available": True, "items": []}):
            out = json.loads(Server._run(
                dummy, "coderef_audit", {"project_path": "d:/proj"}))
        self.assertEqual(out["context"]["guard"]["findings"], 2)
        self.assertTrue(out["context"]["memory_digest"]["available"])

    def test_light_tool_not_injected(self):
        from core.mcp_server import Server
        from types import SimpleNamespace
        dummy = SimpleNamespace(
            _run_inner=lambda n, a, progress_cb=None: json.dumps({"status": "ok"}))
        with mock.patch("core.session_guard.HEAVY_TOOLS", {"coderef_audit"}):
            out = json.loads(Server._run(
                dummy, "coderef_scan_list", {"project_path": "d:/proj"}))
        self.assertNotIn("context", out)


class ScorecardFindingsAccountingTest(unittest.TestCase):
    def test_excluded_breakdown_is_explicit(self):
        """720 vs 736 类差异必须显式列出排除项，不能静默少算。"""
        from core.scorecard import _dim_findings
        with tempfile.TemporaryDirectory() as project:
            os.makedirs(os.path.join(project, ".coderef"))
            findings = [
                {"kind": "defect", "tier": "high", "file_path": "src/a.py", "line": 1},
                {"kind": "advice", "tier": "low", "file_path": "src/b.py", "line": 2},
                {"kind": "defect", "tier": "low", "file_path": ".venv/lib/x.py", "line": 3},
            ]
            with open(os.path.join(project, ".coderef", "audit_findings.json"),
                      "w", encoding="utf-8") as f:
                json.dump({"findings": findings, "total_files": 3,
                           "scan_ts": "2026-01-01 00:00:00"}, f)
            raw = _dim_findings(project)["raw"]
            self.assertEqual(raw["counted"], 1)
            self.assertEqual(raw["total"], 3)
            self.assertEqual(raw["excluded"], {"advice": 1, "runtime_path": 1})


class WikiStateConsistencyTest(unittest.TestCase):
    def test_user_docs_are_not_reported_as_wiki(self):
        """概览的 Wiki 状态必须与记分卡同口径：用户 docs/ 不算 Wiki 产物。"""
        from core.pr_render import _collect_dimension_states
        with tempfile.TemporaryDirectory() as project:
            os.makedirs(os.path.join(project, "docs"))
            with open(os.path.join(project, "docs", "guide.md"),
                      "w", encoding="utf-8") as f:
                f.write("# guide\n")
            states = _collect_dimension_states(project, None)
            self.assertEqual(states["wiki"]["status"], "missing")
            os.makedirs(os.path.join(project, "docs", "wiki"))
            with open(os.path.join(project, "docs", "wiki", "a.md"),
                      "w", encoding="utf-8") as f:
                f.write("# a\n")
            states = _collect_dimension_states(project, None)
            self.assertEqual(states["wiki"]["status"], "done")
            self.assertEqual(states["wiki"]["docs"], 1)


class CanvasGroupLayoutTest(unittest.TestCase):
    def test_large_layer_is_grouped(self):
        """大层必须按模块目录分组，不能让 100+ 节点排成无差别方阵。"""
        from core.canvas_engine import auto_layout
        nodes = []
        for i in range(80):
            nodes.append({
                "id": f"mod:g{i % 4}/m{i}", "type": "module",
                "label": f"m{i}", "x": 0, "y": 0,
                "props": {"full_label": f"root/g{i % 4}/m{i}"},
            })
        data = auto_layout({"canvas": {}, "nodes": nodes, "edges": []},
                           mode="layered")
        groups = data["groups"]
        self.assertGreaterEqual(len(groups), 4)
        self.assertEqual(sum(g["count"] for g in groups), 80)
        by_id = {n["id"]: n for n in data["nodes"]}
        for g in groups:
            for nid in g["ids"]:
                n = by_id[nid]
                self.assertGreaterEqual(n["x"], g["x"])
                self.assertGreaterEqual(n["y"], g["y"])
                self.assertLessEqual(n["x"], g["x"] + g["w"])
                self.assertLessEqual(n["y"], g["y"] + g["h"])


class BusinessFlowCanvasTest(unittest.TestCase):
    def test_flow_canvas_summarizes_cells_not_modules(self):
        """业务流程画布必须一格一卡，不能把 300 个模块逐个铺成方阵。"""
        from core.arch_flow import _build_canvas_data
        stages = [{"id": "trigger", "name": "触发"},
                  {"id": "unlabeled", "name": "未归类阶段"}]
        actors = [{"id": "user_ui", "name": "用户界面"},
                  {"id": "unlabeled", "name": "未归类"}]
        nodes = [{
            "module": f"m{i}", "title": f"m{i}",
            "actor": "user_ui" if i % 2 else "unlabeled",
            "stage": "trigger" if i < 25 else "unlabeled",
        } for i in range(50)]
        data = _build_canvas_data({
            "stages": stages, "actors": actors, "nodes": nodes,
            "edges": [{"from": "m0", "to": "m1", "handoff": True}],
        })
        self.assertEqual(len(data["nodes"]), 4)   # 2 actor × 2 stage 各一张汇总卡
        self.assertLessEqual(len(data["nodes"]), len(stages) * len(actors))
        self.assertEqual(len(data["lanes"]), 2)
        self.assertEqual(len(data["stage_bands"]), 2)


class CanvasImportGeometryTest(unittest.TestCase):
    """U-69：导入 JSON 的 lanes / stage_bands 必须先校验几何字段。

    旧实现直接整体替换，缺 y/h 或 x/w 时 SVG 会写出 undefined / NaN 属性。
    修复后坏文件在改动任何状态前被拒绝，并 toast 具体字段。
    """

    def _html(self):
        from core.canvas_engine import render_canvas
        return render_canvas({"canvas": {"title": "t"}, "nodes": [], "edges": []})

    def test_import_validates_band_geometry_before_state_change(self):
        html = self._html()
        start = html.index("function importJSON(ev)")
        body = html[start:start + 2500]
        self.assertIn("bandGeometryError(d.lanes, ['y','h'], 'lanes')", body)
        self.assertIn("bandGeometryError(d.stage_bands, ['x','w'], 'stage_bands')", body)
        # 校验必须早于 recordPre / DATA 替换，坏 JSON 不污染撤销栈与画布
        self.assertLess(body.index("bandGeometryError(d.lanes"),
                        body.index("recordPre();"))

    def test_malformed_bands_rejected_with_reason(self):
        html = self._html()
        self.assertIn("function bandGeometryError(", html)
        self.assertIn("缺少数值字段", html)
        self.assertIn("'导入失败：' + bad", html)


class DocGovernanceTest(unittest.TestCase):
    """规程单一真源：AGENTS.md 是唯一必读，不再维护 CODEREF.md。

    历史失败：AGENTS.md 只写「见 CODEREF.md」，但注入层只给 AGENTS，AI 从不跳转；
    同时 CODEREF / AGENTS / procedure-card 三处双写漂移（CodeRabbit 路径、测试归属、
    版本源各不相同）。这里用测试锁住单一入口与已修正的事实。
    """

    def _read(self, rel):
        with open(os.path.join(PROJECT_ROOT, rel), encoding="utf-8") as f:
            return f.read()

    @unittest.skipUnless(
        os.path.isfile(os.path.join(PROJECT_ROOT, "AGENTS.md")),
        "AGENTS.md 是本地开发规程，不在公开仓")
    def test_agents_is_single_entry(self):
        self.assertTrue(os.path.isfile(os.path.join(PROJECT_ROOT, "AGENTS.md")))
        self.assertFalse(os.path.isfile(os.path.join(PROJECT_ROOT, "CODEREF.md")),
                         "CODEREF.md 已合并进 AGENTS.md，不得复活第二本必读")
        text = self._read("AGENTS.md")
        self.assertIn("## 0. 规程索引", text)
        self.assertIn("Windows 原生版", text)
        self.assertNotIn("WSL `~/.local/bin/coderabbit", text)
        self.assertIn("测试方回执", text)
        self.assertIn("core/version.py", text)

    def test_procedure_card_matches_agents_entry(self):
        card = self._read(os.path.join("docs", "procedure-card.md"))
        self.assertIn("## 0. 规程索引", card)
        self.assertNotIn("WSL `~/.local/bin/coderabbit", card)
        self.assertIn("可 push", card)


class DreamMemoryMergeTest(unittest.TestCase):
    """5.21.15：记忆引擎整体并入梦（环境索引 + 语义笔记）。"""

    def _project(self, tmp):
        with open(os.path.join(tmp, "app.py"), "w", encoding="utf-8") as f:
            f.write("def run():\n    return 1\n")
        ddir = os.path.join(tmp, ".coderef", "dream")
        os.makedirs(ddir, exist_ok=True)
        notes = [
            {"id": "n1", "job": "pitfall", "kind": "pitfall",
             "claim_type": "fact", "status": "verified",
             "text": "坑A：画布别嵌套小 svg", "evidence": ["file:app.py"]},
            {"id": "n2", "job": "pitfall", "kind": "convention",
             "claim_type": "fact", "status": "verified",
             "text": "约定B：提交带临时身份", "evidence": ["git:abc"]},
            {"id": "n3", "job": "routing", "kind": "rule",
             "claim_type": "advice", "status": "hypothesis",
             "text": "假设C：先看 AGENTS.md", "evidence": ["seed:s1"]},
            {"id": "n4", "job": "pitfall", "kind": "pitfall",
             "claim_type": "fact", "status": "rejected",
             "text": "已拒绝D：WSL coderabbit", "evidence": []},
        ]
        with open(os.path.join(ddir, "notes.json"), "w", encoding="utf-8") as f:
            json.dump({"version": 1, "notes": notes}, f, ensure_ascii=False)

    def test_sync_writes_dream_index_without_llm(self):
        from core.dream_memory import dream_memory
        with tempfile.TemporaryDirectory() as tmp:
            self._project(tmp)
            fake = {"git": [], "dependency": [], "model": [], "api": [],
                    "tool": [], "env_tool": [{"name": "git", "path": "/bin/git"}],
                    "doc": [], "report": []}
            with mock.patch("core.operation_memory.ResourceScanner.scan",
                            return_value=fake), \
                 mock.patch("core.operation_memory._probe_side_dirs",
                            return_value=[]):
                r = dream_memory.sync(tmp)
            self.assertEqual(r["status"], "ok")
            self.assertEqual(r["source"], "dream")
            self.assertTrue(os.path.isfile(
                os.path.join(tmp, ".coderef", "dream", "memory_index.json")))
            self.assertEqual(r["resources"]["env_tool"], 1)

    def test_recover_maps_notes_and_excludes_rejected(self):
        from core.dream_memory import dream_memory
        with tempfile.TemporaryDirectory() as tmp:
            self._project(tmp)
            fake = {"env_tool": [{"name": "git", "path": "/bin/git",
                                  "location": "env", "note": "版本控制"}]}
            with mock.patch("core.operation_memory.ResourceScanner.scan",
                            return_value=fake), \
                 mock.patch("core.operation_memory._probe_side_dirs",
                            return_value=[]):
                r = dream_memory.recover(tmp)
            self.assertEqual(r["status"], "ok")
            self.assertEqual(r["source"], "dream")
            self.assertEqual(r["env_tools"][0]["name"], "git")
            self.assertEqual([x["summary"] for x in r["pitfalls"]],
                             ["坑A：画布别嵌套小 svg"])
            self.assertEqual([x["summary"] for x in r["conventions"]],
                             ["约定B：提交带临时身份"])
            self.assertEqual(r["decisions"], [])
            self.assertEqual([x["summary"] for x in r["pending_items"]],
                             ["假设C：先看 AGENTS.md"])
            texts = [n["summary"] for n in r["notes"]]
            self.assertNotIn("已拒绝D：WSL coderabbit", texts)

    def test_query_and_find_use_dream_notes(self):
        from core.dream_memory import dream_memory
        with tempfile.TemporaryDirectory() as tmp:
            self._project(tmp)
            fake = {"env_tool": []}
            with mock.patch("core.operation_memory.ResourceScanner.scan",
                            return_value=fake), \
                 mock.patch("core.operation_memory._probe_side_dirs",
                            return_value=[]):
                q = dream_memory.query(tmp, query_type="convention",
                                       keyword="临时身份")
                f = dream_memory.find(tmp, name="画布")
            self.assertEqual(q["total"], 1)
            self.assertEqual(q["results"][0]["summary"], "约定B：提交带临时身份")
            self.assertEqual(f["status"], "ok")
            self.assertTrue(any(h.get("side") == "knowledge" for h in f["results"]))

    def test_handler_recover_routes_to_dream(self):
        from core.handlers_engine import _operation_memory
        with tempfile.TemporaryDirectory() as tmp:
            self._project(tmp)
            fake = {"env_tool": [{"name": "git", "path": "/bin/git",
                                  "location": "env", "note": ""}]}
            with mock.patch("core.operation_memory.ResourceScanner.scan",
                            return_value=fake), \
                 mock.patch("core.operation_memory._probe_side_dirs",
                            return_value=[]):
                out = json.loads(_operation_memory(
                    {"project_path": tmp, "action": "recover"}))
            self.assertEqual(out["tool"], "coderef_operation_memory")
            self.assertEqual(out["source"], "dream")
            self.assertEqual(out["env_tools"][0]["name"], "git")


class SetupRulesTest(unittest.TestCase):
    """记忆契约安装器：marker 块、手写内容保留、幂等、可卸载。"""

    def test_install_preserves_manual_content_and_is_idempotent(self):
        from core import setup_rules
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "AGENTS.md")
            with open(path, "w", encoding="utf-8") as f:
                f.write("# 手写规则\n\n- 原有内容\n")
            first = setup_rules.install(tmp)
            self.assertEqual(first["status"], "ok")
            text = open(path, encoding="utf-8").read()
            self.assertIn("原有内容", text)
            self.assertIn("CODEREF-MEMORY-RULE:START", text)
            self.assertIn("coderef_operation_memory(action=recover)", text)
            self.assertTrue(setup_rules.memory_rule_installed(tmp))
            self.assertFalse(setup_rules.status(
                tmp, include_global=False)["needs_setup"])
            second = setup_rules.install(tmp)
            self.assertEqual(second["written"], [])
            setup_rules.uninstall(tmp)
            after = open(path, encoding="utf-8").read()
            self.assertIn("原有内容", after)
            self.assertNotIn("CODEREF-MEMORY-RULE:START", after)

    def test_install_creates_agents_when_missing(self):
        from core import setup_rules
        with tempfile.TemporaryDirectory() as tmp:
            setup_rules.install(tmp)
            self.assertTrue(os.path.isfile(os.path.join(tmp, "AGENTS.md")))
            self.assertTrue(setup_rules.memory_rule_installed(tmp))

    def test_global_target_created_when_editor_dir_exists(self):
        from core import setup_rules
        with tempfile.TemporaryDirectory() as home:
            claude = os.path.join(home, ".claude")
            os.makedirs(claude)
            target = os.path.join(claude, "CLAUDE.md")
            with mock.patch("core.setup_rules._global_targets",
                            return_value=[target]):
                setup_rules.install(home, include_global=True)
            self.assertTrue(os.path.isfile(target))
            self.assertIn("CODEREF-MEMORY-RULE:START",
                          open(target, encoding="utf-8").read())

    def test_setup_status_action_routes_to_installer(self):
        from core.handlers_engine import _operation_memory
        with tempfile.TemporaryDirectory() as tmp:
            out = json.loads(_operation_memory(
                {"project_path": tmp, "action": "setup_status"}))
            self.assertEqual(out["tool"], "coderef_operation_memory")
            self.assertEqual(out["status"], "ok")
            self.assertIn("activation_line", out)
            self.assertTrue(out["needs_setup"])


class AutoSetupGateTest(unittest.TestCase):
    """首次调用自动 setup + G-E 硬闸门。"""

    def test_auto_setup_writes_rule_on_first_call(self):
        from core.mcp_server import _AUTO_SETUP_DONE, _maybe_auto_setup
        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, "app.py"), "w", encoding="utf-8") as f:
                f.write("def run():\n    return 1\n")
            with open(os.path.join(tmp, "AGENTS.md"), "w", encoding="utf-8") as f:
                f.write("# x\n")
            _AUTO_SETUP_DONE.discard(os.path.normcase(os.path.abspath(tmp)))
            res = _maybe_auto_setup(tmp)
            self.assertTrue(res and res.get("auto"))
            text = open(os.path.join(tmp, "AGENTS.md"), encoding="utf-8").read()
            self.assertIn("CODEREF-MEMORY-RULE:START", text)

    def test_gate_blocks_when_rule_installed_without_recover(self):
        from core import preconditions, setup_rules
        from core.session_guard import mark_fact
        with tempfile.TemporaryDirectory() as tmp:
            setup_rules.install(tmp)
            hit = preconditions.evaluate("coderef_audit", tmp, {})
            self.assertTrue(hit["blocked"])
            self.assertEqual(hit["blocking"]["id"], "G-E")
            mark_fact(tmp, "memory_recovered_at")
            passed = preconditions.evaluate("coderef_audit", tmp, {})
            self.assertFalse(any(h["id"] == "G-E" for h in passed["hits"]))

    def test_gate_not_active_without_rule(self):
        from core import preconditions
        with tempfile.TemporaryDirectory() as tmp:
            res = preconditions.evaluate("coderef_audit", tmp, {})
            self.assertFalse(any(h["id"] == "G-E" for h in res["hits"]))


class VersionMemoryRuleTest(unittest.TestCase):
    def test_version_exposes_memory_contract(self):
        from core.mcp_server import _version
        with tempfile.TemporaryDirectory() as tmp:
            out = json.loads(_version({"project_path": tmp}))
            rule = out.get("memory_rule") or {}
            self.assertEqual(rule.get("required_call"),
                             "coderef_operation_memory(action=recover)")
            self.assertTrue(rule.get("needs_setup"))
            self.assertIn("coderef_operation_memory", rule.get("activation_line", ""))

    def test_initialize_injects_memory_contract(self):
        from core.mcp_server import Server
        res = Server()._handle({"jsonrpc": "2.0", "id": 1, "method": "initialize"})
        instructions = res["result"].get("instructions", "")
        self.assertIn("coderef_operation_memory(action=recover)", instructions)
        self.assertIn("不得假装有记忆", instructions)


class SixthRoundFixesTest(unittest.TestCase):
    """外部第六轮：CALLS 漂移 / frontend 假完成 / verify 截断 / wiki front matter。"""

    def test_exact_entity_query_not_truncated_at_50(self):
        from core.code_knowledge_graph import CodeKnowledgeGraph
        with tempfile.TemporaryDirectory() as tmp:
            kg = CodeKnowledgeGraph(tmp)
            kg._init_schema()
            for i in range(60):
                kg._conn.execute(
                    "INSERT INTO nodes(id,type,name,file_path,start_line) "
                    "VALUES(?,?,?,?,?)",
                    (f"n{i}", "function", "main", f"f{i}.py", i + 1))
            kg._conn.commit()
            res = kg.query_entities_exact("main")
            self.assertEqual(res.total, 60)
            self.assertTrue(any(n.file_path == "f59.py" for n in res.nodes))
            kg.close()

    def test_probe_uses_exact_query_for_file_hint(self):
        from core.verify_findings import FindingsVerifier
        node = SimpleNamespace(name="main", file_path="run_all.py",
                               start_line=25, id="n1")

        class KG:
            def query_entities_exact(self, name):
                return SimpleNamespace(nodes=[node], total=1)

            def query_entity(self, name):
                raise AssertionError("不应使用 LIMIT 50 的模糊查询")

            def query_call_counts(self, nid):
                return {"callers": 0, "callees": 0}

        fv = FindingsVerifier("d:/proj")
        fv.kg = KG()
        out = fv._symbol_probe("main", file_hint="chonggou/测试/前端行为/run_all.py")
        self.assertTrue(out["found"])
        self.assertTrue(out["matched_in_file"])

    def test_frontend_pending_is_not_completed(self):
        from core.frontend_inspector import FrontendInspector
        fi = FrontendInspector(llm=SimpleNamespace(client=None))
        pending = fi._pending_finding("按钮", "LLM 不可用，未执行 AI 审查")
        summary = fi._build_static_summary([], [], [], [pending], pending_count=1)
        self.assertIn("未完成", summary)
        self.assertNotIn("审查完成", summary)
        self.assertIn("不计为问题数", summary)

    def test_wiki_md_to_html_strips_front_matter(self):
        from core.report_renderer import md_to_html
        html = md_to_html('---\ntype: "overview"\nconfidence: "high"\n---\n\n# Title\n\nBody')
        self.assertNotIn("type:", html)
        self.assertNotIn("confidence:", html)
        self.assertIn("Title", html)
        self.assertIn("Body", html)

    def test_md_to_html_keeps_plain_hr_section(self):
        """普通 Markdown 的 `---` 分割线区块不是元数据，不能被当成 front matter 吞掉。"""
        from core.report_renderer import md_to_html
        html = md_to_html("---\n# Warning\nImportant instructions\n---\n# Details\n")
        self.assertIn("Warning", html)
        self.assertIn("Important instructions", html)
        self.assertIn("Details", html)

    def test_exact_entity_query_has_no_cap(self):
        """同名节点超过 1000 时也必须全量返回，否则按 file 核验仍会假阴性。"""
        from core.code_knowledge_graph import CodeKnowledgeGraph
        with tempfile.TemporaryDirectory() as tmp:
            kg = CodeKnowledgeGraph(tmp)
            kg._init_schema()
            for i in range(1200):
                kg._conn.execute(
                    "INSERT INTO nodes(id,type,name,file_path,start_line) "
                    "VALUES(?,?,?,?,?)",
                    (f"n{i}", "function", "main", f"f{i}.py", i + 1))
            kg._conn.commit()
            res = kg.query_entities_exact("main")
            self.assertEqual(res.total, 1200)
            self.assertTrue(any(n.file_path == "f1199.py" for n in res.nodes))
            kg.close()

    def test_runtime_url_rejected_marks_partial(self):
        """运行时降级分支追加待确认后，partial 必须同步置真（不能沿用静态结果）。"""
        from core.frontend_inspector import FrontendInspector
        fi = FrontendInspector(llm=SimpleNamespace(client=None))
        static = {"ok": True, "applicable": True, "buttons": [], "menu_tree": [],
                  "findings": [], "summary": "静态结论", "partial": False}
        with mock.patch.object(fi, "_validate_runtime_url",
                               return_value=(False, "host 不在白名单")), \
                mock.patch.object(fi, "_inspect_static", return_value=dict(static)):
            out = fi._inspect_runtime("d:/proj", None, "http://evil.example", [1])
        self.assertTrue(out["partial"])

    def test_snapshots_match_normalizes_paths(self):
        from core.memory_layer import MemoryLayer
        with tempfile.TemporaryDirectory() as tmp:
            p = os.path.join(tmp, "a.py")
            a = {p: {"mtime": 1000.0, "size": 10}}
            b = {p.replace(os.sep, "/"): {"mtime": 1000.0, "size": 10}}
            self.assertTrue(MemoryLayer._snapshots_match(a, b))
            b_stale = {p.replace(os.sep, "/"): {"mtime": 1000.0, "size": 11}}
            self.assertFalse(MemoryLayer._snapshots_match(a, b_stale))
        self.assertFalse(MemoryLayer._snapshots_match(a, {}))

    def test_sync_rebuilds_kg_when_snapshot_stale(self):
        """建图后文件又被改：skip_kg 不能真的跳过，否则图谱永久 stale。"""
        from core.memory_layer import MemoryLayer
        ml = MemoryLayer()
        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, "a.py"), "w", encoding="utf-8") as f:
                f.write("x = 1\n")
            with mock.patch.object(ml, "_load_state", return_value={}), \
                    mock.patch.object(ml, "_classify_changed",
                                      return_value=([], ["a.py"])), \
                    mock.patch.object(ml, "_extract_ast_cache", return_value={}), \
                    mock.patch.object(ml, "_analyze_structure", return_value=None), \
                    mock.patch.object(ml, "_persist_state"), \
                    mock.patch.object(ml, "_index_kb", return_value={}), \
                    mock.patch.object(ml, "_kg_exists", return_value=True), \
                    mock.patch.object(ml, "_kg_counts",
                                      return_value={"nodes": 1}), \
                    mock.patch.object(ml, "_update_knowledge_graph",
                                      return_value={"nodes": 2}) as upd, \
                    mock.patch("core.code_analyzer.CodeAnalyzer") as ca:
                ca.return_value.scan_directory.return_value = [
                    os.path.join(tmp, "a.py")]
                out = ml.sync(tmp, mode="incr", skip_kg=True,
                              kg_snapshot={os.path.join(tmp, "a.py"):
                                           {"mtime": 1.0, "size": 1}})
        self.assertEqual(out["kg"], {"nodes": 2})
        upd.assert_called_once()

    def test_sync_applies_whitelist_dir(self):
        """U-78：sync 的文件清单必须与 audit 同源排除备份目录。"""
        from core.memory_layer import MemoryLayer
        ml = MemoryLayer()
        with tempfile.TemporaryDirectory() as tmp:
            os.makedirs(os.path.join(tmp, "_refactor_backup"))
            keep = os.path.join(tmp, "a.py")
            bak = os.path.join(tmp, "_refactor_backup", "old.py")
            for p in (keep, bak):
                with open(p, "w", encoding="utf-8") as f:
                    f.write("x = 1\n")
            with mock.patch.object(ml, "_load_state", return_value={}), \
                    mock.patch.object(ml, "_extract_ast_cache", return_value={}), \
                    mock.patch.object(ml, "_analyze_structure", return_value=None), \
                    mock.patch.object(ml, "_persist_state"), \
                    mock.patch.object(ml, "_index_kb", return_value={}), \
                    mock.patch.object(ml, "_update_knowledge_graph",
                                      return_value={"nodes": 1}), \
                    mock.patch("core.pr_whitelist.whitelist_exclude_dirs",
                               return_value=["_refactor_backup"]), \
                    mock.patch("core.code_analyzer.CodeAnalyzer") as ca:
                ca.return_value.scan_directory.return_value = [keep, bak]
                out = ml.sync(tmp, mode="full")
            self.assertEqual(out["total_files"], 1)
            self.assertNotIn(bak, out.get("changed_files", []))

    def test_sync_kg_failure_sets_dirty_marker(self):
        """U-96/CodeRabbit：建图失败后 incr 必须强制重试，不能停在 no-change。"""
        from core.memory_layer import MemoryLayer
        ml = MemoryLayer()
        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, "a.py"), "w", encoding="utf-8") as f:
                f.write("x = 1\n")
            marker = ml._state_path(tmp) + ".kg_dirty"
            with mock.patch.object(ml, "_load_state", return_value={}), \
                    mock.patch.object(ml, "_extract_ast_cache", return_value={}), \
                    mock.patch.object(ml, "_analyze_structure", return_value=None), \
                    mock.patch.object(ml, "_persist_state"), \
                    mock.patch.object(ml, "_index_kb", return_value={}), \
                    mock.patch.object(ml, "_update_knowledge_graph",
                                      return_value={"nodes": 0,
                                                    "errors": ["boom"]}), \
                    mock.patch("core.code_analyzer.CodeAnalyzer") as ca:
                ca.return_value.scan_directory.return_value = [
                    os.path.join(tmp, "a.py")]
                out = ml.sync(tmp, mode="full")
            self.assertEqual(out["status"], "partial_failed")
            self.assertTrue(os.path.exists(marker))
            # 下一次 incr 读到 dirty 标记 → 强制 full，成功后清标记
            with mock.patch.object(ml, "_load_state", return_value={}), \
                    mock.patch.object(ml, "_extract_ast_cache", return_value={}), \
                    mock.patch.object(ml, "_analyze_structure", return_value=None), \
                    mock.patch.object(ml, "_persist_state"), \
                    mock.patch.object(ml, "_index_kb", return_value={}), \
                    mock.patch.object(ml, "_update_knowledge_graph",
                                      return_value={"nodes": 1}), \
                    mock.patch("core.code_analyzer.CodeAnalyzer") as ca:
                ca.return_value.scan_directory.return_value = [
                    os.path.join(tmp, "a.py")]
                out2 = ml.sync(tmp, mode="incr")
            self.assertEqual(out2["mode"], "full")
            self.assertFalse(os.path.exists(marker))

    def test_pipeline_sync_skips_kg_after_audit_build(self):
        from core.memory_layer import memory_layer
        from core.pipeline_runner import Pipe
        snap = {"d:/proj/a.py": {"mtime": 1.0, "size": 1}}
        r = SimpleNamespace(audit_strategy="incr", kg_built_at="x", errors=[],
                            file_snapshot=snap)
        with mock.patch.object(memory_layer, "sync") as m:
            Pipe._sync_memory_layer(object(), "d:/proj", r)
        self.assertTrue(m.call_args.kwargs["skip_kg"])
        self.assertEqual(m.call_args.kwargs["kg_snapshot"], snap)


class ProcedureCardTest(unittest.TestCase):
    def test_recover_card_reads_agents_section(self):
        """recover 必须把 AGENTS.md 的规程入口卡一起带回，避免 CODEREF 找不到。"""
        import os
        import tempfile
        from core.operation_memory import _read_procedure_card
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "AGENTS.md"), "w", encoding="utf-8") as f:
                f.write("# A\n\n## 0. 规程索引\n- 发布 SOP: x\n"
                        "- CodeRabbit: WSL\n\n## 1. 其他\n- 不应出现\n")
            card = _read_procedure_card(d)
            self.assertIsNotNone(card)
            self.assertIn("发布 SOP", card["text"])
            self.assertIn("CodeRabbit", card["text"])
            self.assertNotIn("## 1.", card["text"])

    def test_missing_agents_falls_back_to_coderef_card(self):
        """外部项目无 AGENTS.md / 无项目公开卡时，回退 CodeRef 自带公开卡。"""
        import tempfile
        from core.operation_memory import _read_procedure_card
        with tempfile.TemporaryDirectory() as d:
            card = _read_procedure_card(d)
            self.assertIsNotNone(card)
            self.assertEqual(card["source"], "CodeRef:docs/procedure-card.md")
            self.assertIn("发布 SOP", card["text"])
            self.assertIn("CodeRabbit", card["text"])

    def test_generic_agents_skipped_for_project_card(self):
        """通用 AGENTS.md 没有规程索引时必须跳过，不能吞掉项目公开卡。"""
        import os
        import tempfile
        from core.operation_memory import _read_procedure_card
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.join(d, "docs"))
            with open(os.path.join(d, "AGENTS.md"), "w", encoding="utf-8") as f:
                f.write("# 项目规则\n\n## 1. 编码规范\n- 不应被当成规程卡\n")
            with open(os.path.join(d, "docs", "procedure-card.md"),
                      "w", encoding="utf-8") as f:
                f.write("# 公开卡\n\n## 0. 规程索引\n- 发布 SOP: x\n")
            card = _read_procedure_card(d)
            self.assertIsNotNone(card)
            self.assertEqual(card["source"], "docs/procedure-card.md")
            self.assertIn("发布 SOP", card["text"])
            self.assertNotIn("编码规范", card["text"])

    def test_public_procedure_card_fallback(self):
        """没有 AGENTS.md 时，回退公开 docs/procedure-card.md。"""
        import os
        import tempfile
        from core.operation_memory import _read_procedure_card
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.join(d, "docs"))
            with open(os.path.join(d, "docs", "procedure-card.md"),
                      "w", encoding="utf-8") as f:
                f.write("# 公开卡\n\n## 0. 规程索引\n- 发布 SOP: x\n"
                        "\n## 1. 说明\n- 不应出现\n")
            card = _read_procedure_card(d)
            self.assertIsNotNone(card)
            self.assertEqual(card["source"], "docs/procedure-card.md")
            self.assertIn("发布 SOP", card["text"])
            self.assertNotIn("## 1.", card["text"])

    def test_agents_card_preferred_over_public(self):
        """项目自己的 AGENTS.md 优先于公开 fallback。"""
        import os
        import tempfile
        from core.operation_memory import _read_procedure_card
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.join(d, "docs"))
            with open(os.path.join(d, "AGENTS.md"), "w", encoding="utf-8") as f:
                f.write("## 0. 规程索引\n- 项目自己的卡\n")
            with open(os.path.join(d, "docs", "procedure-card.md"),
                      "w", encoding="utf-8") as f:
                f.write("## 0. 规程索引\n- 公开卡\n")
            card = _read_procedure_card(d)
            self.assertEqual(card["source"], "AGENTS.md#规程索引")
            self.assertIn("项目自己的卡", card["text"])

    def test_inline_marker_not_treated_as_heading(self):
        """正文里提到 `## 0.` 的句子不能被当成规程索引标题。"""
        import os
        import tempfile
        from core.operation_memory import _read_procedure_card
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "AGENTS.md"), "w", encoding="utf-8") as f:
                f.write("# A\n\n> 读取优先级：`## 0. 规程索引` → 公开卡\n\n"
                        "## 0. 规程索引\n- 发布 SOP: x\n")
            card = _read_procedure_card(d)
            self.assertIsNotNone(card)
            self.assertIn("发布 SOP", card["text"])
            self.assertNotIn("读取优先级", card["text"])

    def test_indented_heading_recognized(self):
        """Markdown 允许最多 3 个空格缩进，缩进标题也要能识别。"""
        import os
        import tempfile
        from core.operation_memory import _read_procedure_card
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "AGENTS.md"), "w", encoding="utf-8") as f:
                f.write("# A\n\n  ## 0. 规程索引\n- 发布 SOP: x\n\n"
                        "  ## 1. 其他\n- 不应出现\n")
            card = _read_procedure_card(d)
            self.assertIsNotNone(card)
            self.assertIn("发布 SOP", card["text"])
            self.assertNotIn("不应出现", card["text"])

    def test_fenced_headings_ignored(self):
        """围栏代码块里的标题不能当成规程索引的起点或终点。"""
        import os
        import tempfile
        from core.operation_memory import _read_procedure_card
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "AGENTS.md"), "w", encoding="utf-8") as f:
                f.write("# A\n\n```markdown\n## 0. 规程索引\n- 假卡\n```\n\n"
                        "## 0. 规程索引\n- 发布 SOP: x\n\n"
                        "```markdown\n## 示例\n```\n\n- 后续指令: y\n")
            card = _read_procedure_card(d)
            self.assertIsNotNone(card)
            self.assertIn("发布 SOP", card["text"])
            self.assertIn("后续指令", card["text"])
            self.assertNotIn("假卡", card["text"])

    def test_short_fence_does_not_close_longer_fence(self):
        """四反引号块内的三反引号行不能提前闭合围栏。"""
        import os
        import tempfile
        from core.operation_memory import _read_procedure_card
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "AGENTS.md"), "w", encoding="utf-8") as f:
                f.write("# A\n\n## 0. 规程索引\n- 发布 SOP: x\n\n"
                        "````markdown\n```\n## 示例\n```\n````\n\n"
                        "- 后续指令: y\n")
            card = _read_procedure_card(d)
            self.assertIsNotNone(card)
            self.assertIn("发布 SOP", card["text"])
            self.assertIn("后续指令", card["text"])

    def test_digest_carries_card_without_ledger(self):
        """没有操作记忆 ledger 时，首次重工具摘要也必须带规程入口卡。"""
        import os
        import tempfile
        from core.memory_digest import build_digest
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "AGENTS.md"), "w", encoding="utf-8") as f:
                f.write("## 0. 规程索引\n- 发布 SOP: x\n- CodeRabbit: WSL\n")
            digest = build_digest(d)
            self.assertIn("发布 SOP", digest["procedure_card"]["text"])
            self.assertIn("CodeRabbit", digest["procedure_card"]["text"])
            self.assertIn("AGENTS.md", digest["procedure_hint"])


# ═══════════════════════════════════════════════════════════════════
# 反馈10 · v5.21.14 外部第五轮（kuajingdianshang）回归
#   D-1 CALLS 漂移溯源 / D-2 verify_findings 文件限定 / D-3 OWASP 备份目录
#   D-4 arch_canvas favicon / D-5 定标前缀容错 / C10 docs 零产物
# ═══════════════════════════════════════════════════════════════════

class AgentSecurityWhitelistDirTest(unittest.TestCase):
    """D-3 · Agent 安全扫描必须与图谱同口径排除 whitelist dir（OWASP 58% 噪声）。"""

    def test_backup_dir_not_scanned(self):
        from core.asa_scan import _collect_target_files
        with tempfile.TemporaryDirectory() as tmp:
            os.makedirs(os.path.join(tmp, "src"), exist_ok=True)
            os.makedirs(os.path.join(tmp, "_refactor_backup"), exist_ok=True)
            open(os.path.join(tmp, "src", "app.py"), "w",
                 encoding="utf-8").write("def run():\n    return 1\n")
            open(os.path.join(tmp, "_refactor_backup", "old.py"), "w",
                 encoding="utf-8").write("def old():\n    return 2\n")
            with mock.patch("core.pr_whitelist.whitelist_exclude_dirs",
                            return_value=["_refactor_backup"]):
                files = _collect_target_files(tmp)
        self.assertTrue(any(f.endswith("app.py") for f in files), files)
        self.assertFalse([f for f in files if "_refactor_backup" in f], files)

    def test_without_whitelist_backup_kept(self):
        from core.asa_scan import _collect_target_files
        with tempfile.TemporaryDirectory() as tmp:
            os.makedirs(os.path.join(tmp, "_refactor_backup"), exist_ok=True)
            open(os.path.join(tmp, "_refactor_backup", "old.py"), "w",
                 encoding="utf-8").write("x = 1\n")
            with mock.patch("core.pr_whitelist.whitelist_exclude_dirs",
                            return_value=[]):
                files = _collect_target_files(tmp)
        self.assertTrue(any("_refactor_backup" in f for f in files), files)


class VerifyFindingsFileConstraintTest(unittest.TestCase):
    """D-2 · 提供 file 时以文件为第一约束，不能按全仓同名符号判确证。"""

    SRC = "def main():\n    return 1\n"
    OTHER = "def other():\n    return 2\n"

    def _build(self, tmp):
        for name, src in (("a.py", self.SRC), ("b.py", self.SRC), ("c.py", self.OTHER)):
            open(os.path.join(tmp, name), "w", encoding="utf-8").write(src)
        from core.code_analyzer import CodeAnalyzer
        analysis = CodeAnalyzer().analyze_project(tmp, force_reanalyze=True)
        ast_results = {}
        for name in ("a.py", "b.py", "c.py"):
            fp = os.path.join(tmp, name)
            ast_results[fp] = AstParser(project_root=tmp).parse(fp)
        db = os.path.join(tmp, "kg.db")
        kg = CodeKnowledgeGraph(tmp, db_path=db)
        kg.build(analysis=analysis, ast_results=ast_results, exclude_dirs=[],
                 builder="test")
        kg.close()
        return db

    def _verify(self, tmp, finding):
        from core.verify_findings import FindingsVerifier
        v = FindingsVerifier(tmp, db_path=self._build(tmp))
        return v.verify([finding])["results"][0]

    def test_symbol_resolved_inside_given_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = self._verify(tmp, {"title": "x", "file": "a.py",
                                   "symbols": ["main"]})
        self.assertEqual(r["verdict"], "confirmed", r)
        self.assertIn("a.py", r["evidence"]["symbols"][0]["node"])

    def test_symbol_only_elsewhere_is_not_confirmed(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = self._verify(tmp, {"title": "x", "file": "c.py",
                                   "symbols": ["main"]})
        self.assertEqual(r["verdict"], "partially_confirmed", r)
        self.assertIn("文件外", r["reason"])
        self.assertGreater(r["evidence"]["symbols"][0]["same_name_elsewhere"], 0)


class KgBuildStampTest(unittest.TestCase):
    """D-1 · 建图必须可溯源且同输入可复现（CALLS 不再因并发/路径漂移）。"""

    SRC = (
        "def helper():\n    return 1\n\n"
        "def run():\n    return helper()\n"
    )

    def test_build_is_stamped_and_deterministic(self):
        with tempfile.TemporaryDirectory() as tmp:
            open(os.path.join(tmp, "app.py"), "w",
                 encoding="utf-8").write(self.SRC)
            from core.code_analyzer import CodeAnalyzer
            analysis = CodeAnalyzer().analyze_project(tmp, force_reanalyze=True)
            fp = os.path.join(tmp, "app.py")
            ast_results = {fp: AstParser(project_root=tmp).parse(fp)}
            kg = CodeKnowledgeGraph(tmp, db_path=os.path.join(tmp, "kg.db"))
            try:
                kg.build(analysis=analysis, ast_results=ast_results,
                         exclude_dirs=[], builder="audit_pipeline")
                s1 = kg.get_stats()
                kg.build(analysis=analysis, ast_results=ast_results,
                         exclude_dirs=[], builder="audit_pipeline")
                s2 = kg.get_stats()
            finally:
                kg.close()
        self.assertEqual(s1["built_by"], "audit_pipeline")
        self.assertEqual(s1["built_mode"], "full")
        self.assertEqual(s1["ast_files"], "1")
        self.assertEqual(s1["calls"], s2["calls"])
        self.assertEqual((s1["node_count"], s1["edge_count"]),
                         (s2["node_count"], s2["edge_count"]))


class ArchGapSuffixMatchTest(unittest.TestCase):
    """D-5 · 子工程前缀容错：唯一后缀命中不得判缺失，多候选仍判缺失。"""

    def test_unique_suffix_is_found(self):
        from core.arch_gap_analyzer import _module_exists
        with tempfile.TemporaryDirectory() as tmp:
            nodes = {"mod:x": {
                "type": "module", "name": "web_gui",
                "file_path": os.path.join(tmp, "chonggou", "入口与启动", "web_gui.py"),
            }}
            self.assertTrue(_module_exists(tmp, "入口与启动/web_gui", nodes))

    def test_ambiguous_suffix_is_missing(self):
        from core.arch_gap_analyzer import _module_exists
        with tempfile.TemporaryDirectory() as tmp:
            nodes = {
                "mod:p1": {"type": "module", "name": "web_gui",
                           "file_path": os.path.join(tmp, "p1", "x", "web_gui.py")},
                "mod:p2": {"type": "module", "name": "web_gui",
                           "file_path": os.path.join(tmp, "p2", "x", "web_gui.py")},
            }
            self.assertFalse(_module_exists(tmp, "x/web_gui", nodes))


class WikiStreamingWriteTest(unittest.TestCase):
    """C10 · 模块文档必须逐篇落盘，取消可在两篇之间生效（不再 15 分钟 0 文件）。"""

    class _FakeLLM:
        def is_available(self):
            return True

        def reset_budget(self):
            pass

        def is_error_response(self, text):
            return False

        def chat_completion(self, messages, max_tokens=0, temperature=0):
            return "# doc\n\ncontent"

    def _gen(self):
        from core.wiki_generator import WikiGenerator
        gen = WikiGenerator(llm_client=self._FakeLLM())
        gen._failed_docs = []
        gen._last_llm_error = ""
        gen._instructions = {}
        gen._wl_exclude_dirs = []
        gen._wl_exclude_loaded = True
        return gen

    def _modules(self):
        from core.wiki_generator import WikiModule
        return [WikiModule(name=f"m{i}", path="", py_files=[], file_count=1,
                           is_core=True) for i in range(3)]

    def test_docs_written_per_completion(self):
        with tempfile.TemporaryDirectory() as tmp:
            gen = self._gen()
            mods = self._modules()
            gen._generate_module_docs(mods, {m.name: "desc" for m in mods}, tmp)
            written = [f for f in os.listdir(os.path.join(tmp, "MODULES"))
                       if f.endswith(".md") and f != "_index.md"]
        self.assertEqual(len(written), 3, written)

    def test_cancel_keeps_already_written_docs(self):
        with tempfile.TemporaryDirectory() as tmp:
            gen = self._gen()
            mods = self._modules()
            calls = {"n": 0}

            def cb(stage, cur, total, detail=""):
                # 预算阶段（准备模块文档）不是取消检查点，只对写盘阶段计次
                if "写入模块文档" not in detail:
                    return
                calls["n"] += 1
                if calls["n"] >= 2:
                    raise RuntimeError("cancelled")

            with self.assertRaises(RuntimeError):
                gen._generate_module_docs(
                    mods, {m.name: "desc" for m in mods}, tmp, progress_cb=cb)
            written = [f for f in os.listdir(os.path.join(tmp, "MODULES"))
                       if f.endswith(".md") and f != "_index.md"]
        self.assertGreaterEqual(len(written), 1, written)


class AstImportAsnamesTest(unittest.TestCase):
    """外部第七轮：memory(sync) 与 audit 建图 CALLS 不一致。

    根因是 AstCodeImport.asnames（from x import y as z 的别名映射）没有
    序列化，状态还原后别名丢失，依赖别名的调用边整批解析不到。
    """

    def test_import_alias_survives_roundtrip(self):
        from core.ast_parser import AstFileResult, AstCodeImport
        from core.memory_layer import _ast_to_dict, _ast_from_dict
        ar = AstFileResult(file_path="a.py")
        ar.imports.append(AstCodeImport(
            module="core.audit", names=["audit"], is_from_import=True,
            line=1, asnames={"audit": "arch_audit"}))
        rt = _ast_from_dict(_ast_to_dict(ar))
        self.assertEqual(rt.imports[0].asnames, {"audit": "arch_audit"})

    def test_memory_path_matches_audit_path_calls(self):
        from core.ast_parser import AstParser
        from core.code_analyzer import CodeAnalyzer
        from core.code_knowledge_graph import build_knowledge_graph
        from core.memory_layer import _ast_to_dict, _ast_from_dict, _strip_code
        with tempfile.TemporaryDirectory() as tmp:
            pkg = os.path.join(tmp, "pkg")
            os.makedirs(pkg)
            open(os.path.join(pkg, "__init__.py"), "w").close()
            with open(os.path.join(pkg, "util.py"), "w", encoding="utf-8") as f:
                f.write("def helper():\n    return 1\n")
            with open(os.path.join(pkg, "main.py"), "w", encoding="utf-8") as f:
                f.write("from pkg.util import helper as h\n\n\ndef run():\n    return h()\n")
            analysis = CodeAnalyzer().analyze_project(tmp, force_reanalyze=True)
            fresh = {cf.file_path: AstParser().parse(cf.file_path)
                     for cf in analysis.files if cf.file_path.endswith(".py")}
            restored = {fp: _ast_from_dict(_strip_code(_ast_to_dict(ar)))
                        for fp, ar in fresh.items() if ar}

            def _calls(asts, tag):
                kg = build_knowledge_graph(tmp, analysis=analysis,
                                           ast_results=asts, builder=tag)
                data = kg.get_stats()
                kg.close()
                return data["calls"]

            self.assertEqual(_calls(fresh, "audit"), _calls(restored, "memory"))


class ConstantScopeVerifyTest(unittest.TestCase):
    """外部 v5.21.19：单词全大写常量被漏收；口径排除符号误判为部分确证。"""

    def test_single_word_upper_is_constant(self):
        import ast
        from core.ast_parser import AstParser
        p = AstParser()
        val = lambda src: ast.parse(src).body[0].value
        self.assertEqual(p._classify_assignment("SOURCE", val("SOURCE = 'x'")), "constant")
        self.assertEqual(p._classify_assignment("DROP", val("DROP = 0.3")), "constant")
        self.assertEqual(
            p._classify_assignment("CAPABILITIES", val("CAPABILITIES = []")),
            "expression")

    def test_excluded_constant_detected(self):
        from core.verify_findings import FindingsVerifier
        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, "m.py"), "w", encoding="utf-8") as f:
                f.write("CAPABILITIES = ['a']\nSOURCE = 'x'\n")
            fv = FindingsVerifier(tmp)
            self.assertTrue(fv._is_excluded_constant("CAPABILITIES", "m.py"))
            self.assertFalse(fv._is_excluded_constant("SOURCE", "m.py"))


class MethodNodeDedupeTest(unittest.TestCase):
    """U-81：同一 def 不得同时产出 func: 与 method: 节点，裸名 callers 要聚合。"""

    def test_method_not_duplicated_and_bare_name_callers_aggregate(self):
        from core.code_models import CodeFile, CodeClass, CodeFunction
        from core.code_knowledge_graph import build_knowledge_graph, KGEdge
        with tempfile.TemporaryDirectory() as tmp:
            fp = os.path.join(tmp, "a.py")
            with open(fp, "w", encoding="utf-8") as f:
                f.write("class ListingFactory:\n"
                        "    def generate_listing(self):\n"
                        "        return 1\n\n"
                        "def caller():\n"
                        "    return 1\n")
            method = CodeFunction(name="generate_listing", start_line=2, end_line=3)
            caller = CodeFunction(name="caller", start_line=5, end_line=6)
            cf = CodeFile(file_path=fp, language="python",
                          functions=[method, caller],
                          classes=[CodeClass(name="ListingFactory", start_line=1,
                                             end_line=3, methods=[method])])
            analysis = SimpleNamespace(project_path=tmp, files=[cf],
                                       total_files=1, total_lines=6)
            kg = build_knowledge_graph(tmp, analysis=analysis)
            try:
                rows = kg._conn.execute(
                    "SELECT id, type FROM nodes WHERE name LIKE '%generate_listing%'"
                ).fetchall()
                ids = [r["id"] for r in rows]
                self.assertEqual(len(ids), 1, f"同一 def 不应双份节点: {ids}")
                self.assertTrue(ids[0].startswith("method:"), ids[0])
                caller_row = kg._conn.execute(
                    "SELECT id FROM nodes WHERE name='caller'").fetchone()
                self.assertIsNotNone(caller_row)
                kg._upsert_edge(KGEdge(source=caller_row["id"], target=ids[0],
                                       type="CALLS"))
                out = kg.query_callers("generate_listing")
                self.assertEqual(out.total, 1, "裸名 callers 必须命中方法节点")
                # 裸名 exact 查询同样要能定位方法节点（verify_findings 用）
                ent = kg.query_entities_exact("generate_listing")
                self.assertTrue(any(n.name.endswith(".generate_listing")
                                    for n in ent.nodes), [n.name for n in ent.nodes])
                # LIKE 通配符必须转义：Cache.get_data 不能匹配 Cache.getXdata
                kg._upsert_node(KGNode(id="method:a:Cache.getXdata",
                                       type="method", name="Cache.getXdata",
                                       file_path=fp))
                self.assertIsNone(kg._find_node_by_name("Cache.get_data"))
            finally:
                kg.close()

    def test_suffix_like_escapes_wildcards(self):
        """CodeRabbit major：方法名里的 `_` 不能被 SQL LIKE 当通配符。"""
        from core.code_knowledge_graph import CodeKnowledgeGraph
        self.assertEqual(CodeKnowledgeGraph._suffix_like("get_data"),
                         "%.get\\_data")
        self.assertEqual(CodeKnowledgeGraph._suffix_like("a%b"),
                         "%.a\\%b")


class SensitiveConstantOrderTest(unittest.TestCase):
    """CodeRabbit major：全大写敏感名（PASSWORD）不得被常量分支吞掉。"""

    def test_uppercase_password_is_hardcoded(self):
        import ast
        from core.ast_parser import AstParser
        p = AstParser()
        node = ast.parse('PASSWORD = "example-secret"\n').body[0]
        self.assertEqual(p._classify_assignment("PASSWORD", node.value),
                         "hardcoded")
        node2 = ast.parse("TIMEOUT = 30\n").body[0]
        self.assertEqual(p._classify_assignment("TIMEOUT", node2.value),
                         "constant")


class WhitelistDirScanTest(unittest.TestCase):
    """U-78：audit 最外层文件统计必须应用白名单 dir 排除。"""

    def test_backup_dir_excluded_from_totals(self):
        from core import pr_whitelist as pw
        with tempfile.TemporaryDirectory() as tmp:
            keep = os.path.join(tmp, "src", "a.py")
            bak = os.path.join(tmp, "_refactor_backup", "old.py")
            files = [SimpleNamespace(file_path=keep, raw_content="x = 1\n"),
                     SimpleNamespace(file_path=bak, raw_content="y = 2\nz = 3\n")]
            analysis = SimpleNamespace(files=files, total_files=2, total_lines=3)
            with mock.patch.object(pw, "whitelist_list",
                                   return_value=[{"dir": "_refactor_backup"}]):
                pw._apply_whitelist_dirs(tmp, analysis)
            self.assertEqual(analysis.total_files, 1)
            self.assertEqual(analysis.total_lines, 1)
            self.assertEqual([f.file_path for f in analysis.files], [keep])


class SameFileNanoTest(unittest.TestCase):
    """U-87：0.1s 内同尺寸改写必须被检出。"""

    def test_fast_same_size_rewrite_detected(self):
        from core.memory_layer import MemoryLayer
        old = {"mtime": 100.0, "mtime_ns": 100_000_000_000, "size": 10}
        cur = {"mtime": 100.05, "mtime_ns": 100_050_000_000, "size": 10}
        self.assertFalse(MemoryLayer._same_file(old, cur))
        self.assertTrue(MemoryLayer._same_file(old, dict(old)))

    def test_legacy_snapshot_fallback_keeps_tolerance(self):
        from core.memory_layer import MemoryLayer
        old = {"mtime": 100.0, "size": 10}
        cur = {"mtime": 100.05, "size": 10}
        self.assertTrue(MemoryLayer._same_file(old, cur))


class DegradedReasonsTest(unittest.TestCase):
    """U-89：`Request timed out.` 必须被判为超时。"""

    def test_timed_out_phrase_detected(self):
        from core.mcp_server import _degraded_reasons
        out = _degraded_reasons(["LLM 调用异常: Request timed out."])
        self.assertTrue(out["timeout_or_budget"])
        self.assertFalse(_degraded_reasons([])["timeout_or_budget"])

    def test_placeholder_counts_as_content_dropped(self):
        """内容被丢弃但写了占位时，content_dropped 也必须为 true。"""
        from core.mcp_server import _degraded_reasons
        out = _degraded_reasons(
            ["以下 1 个文档生成失败（LLM 返回空内容），已写失败占位: USAGE.md"])
        self.assertTrue(out["content_dropped"])


class RoleBoundaryDegradedTest(unittest.TestCase):
    """U-83 / U-84 / U-85：语义层缺失显式降级，空 target 不得静默成功。"""

    @staticmethod
    def _arch(entry_mods=("entry/main",), business_mods=("business/ops",)):
        return {
            "version": "5.0", "project": "rb-fix",
            "tech_roles": [
                {"id": "entry", "name": "入口层",
                 "target_modules": list(entry_mods),
                 "role_keywords": ["launcher", "cli", "server"]},
                {"id": "business", "name": "业务层",
                 "target_modules": list(business_mods),
                 "role_keywords": ["profit", "order"]},
            ],
            "business_flows": [], "constraints": [],
        }

    def _build(self, files, arch):
        import shutil
        root = tempfile.mkdtemp(prefix="coderef_rb_fix_")
        self.addCleanup(shutil.rmtree, root, True)
        for rel, src in files.items():
            p = os.path.join(root, rel.replace("/", os.sep))
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with open(p, "w", encoding="utf-8") as f:
                f.write(src)
        arch_dir = os.path.join(root, ".coderef")
        os.makedirs(arch_dir, exist_ok=True)
        with open(os.path.join(arch_dir, "target_arch.json"), "w",
                  encoding="utf-8") as f:
            json.dump(arch, f, ensure_ascii=False)
        return root

    def test_no_llm_marks_degraded_and_high_uncertainty(self):
        from core.role_boundary import detect
        src = "def calculate_profit(amount):\n    return amount\n"
        root = self._build({"entry/main.py": src, "business/ops.py": src},
                           self._arch())
        with mock.patch("core.llm_integration.get_llm_client",
                        return_value=None, create=True):
            r = detect(root, db_path=os.path.join(root, "no_kg.db"),
                       semantic=True)
        self.assertTrue(r["boundary_issues"], "夹具应产生越界候选")
        self.assertEqual(r.get("status"), "DEGRADED")
        bad = [c["symbol"] for c in r["boundary_issues"] + r["call_hints"]
               if c["uncertainty"] != "high"]
        self.assertEqual(bad, [], "语义未生效时不得提高置信")

    def test_empty_target_modules_is_no_target(self):
        from core.role_boundary import detect
        root = self._build({"entry/main.py": "def f():\n    pass\n"},
                           self._arch(entry_mods=(), business_mods=()))
        r = detect(root, db_path=os.path.join(root, "no_kg.db"))
        self.assertTrue(r.get("no_target"))
        self.assertEqual(r.get("status"), "SKIP")
        self.assertIn("no_target", r["summary"]["message"])


class CanvasEmptyStateTest(unittest.TestCase):
    """U-90：空项目画布必须有空态文案。"""

    def test_empty_canvas_has_empty_state(self):
        from core.canvas_engine import render_canvas
        html = render_canvas({"nodes": [], "edges": [],
                              "meta": {"summary": {}}}, title="空项目")
        self.assertIn("emptyState", html)
        self.assertIn("暂无架构数据", html)


if __name__ == "__main__":
    unittest.main(verbosity=2)
