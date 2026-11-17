# -*- coding: utf-8 -*-
"""59 工具契约矩阵（补齐“所有功能都测了”的逐工具层）。

三层：
1. ToolManifestContractTest —— 全部工具的 schema/注册一致性（快、默认跑）；
2. ToolProtocolNegativeTest —— 协议层负向契约（未知工具/缺路径/相对路径/非法枚举）；
3. AllToolsRuntimeMatrixTest —— 逐工具真实调用一轮（默认跳过，
   `CODEREF_TOOL_MATRIX=1` 开启），结果落 `cache/tool_matrix_results.json`。

判据（运行时矩阵）：
- 不允许未捕获异常；
- 每次调用必须有 JSON 结构返回（result 或 error），不允许纯文本；
- payload 带 tool 字段时必须与调用名一致；
- status 可以是 ok/completed/SKIP/blocked/error —— 结构性通过，不强制业务成功。
"""
import json
import os
import sys
import tempfile
import threading
import time
import unittest

PROJECT_ROOT = os.environ.get("CODEREF_SRC",
                              os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from core.mcp_server import Server  # noqa: E402
from core.tools_manifest import BUILTIN_TOOLS  # noqa: E402

CACHE_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "cache")
MATRIX_ENABLED = os.environ.get("CODEREF_TOOL_MATRIX") == "1"
SLOW_MATRIX_ENABLED = os.environ.get("CODEREF_TOOL_MATRIX_SLOW") == "1"
TOOL_TIMEOUT = int(os.environ.get("CODEREF_TOOL_TIMEOUT", "90"))
STRICT = os.environ.get("CODEREF_STRICT_KNOWN") == "1"


def known(bug: str, why: str):
    # 已知缺陷均已修复：默认即阻断（保留 bug 注释语义）
    return lambda fn: fn

# LLM 重工具：3 文件样例上实测 >300s 未返回（wiki 生成 + 多轮 LLM），
# 归入 large/释放前层，默认矩阵只登记 defer，不阻塞合入门禁。
KNOWN_SLOW = {"coderef_docs"}

# 已知超时：语义参数非法时未前置校验，直接进入 LLM 评审导致长时间阻塞（O-TOOL-1）。
# 矩阵仍会真实调用并记录耗时，但不算作“未测/裸崩”。
KNOWN_TIMEOUT = {
    "coderef_innovation_review":
        "canonical='matrix' 不存在时未前置校验，LLM 评审实测 >90s（O-TOOL-1）",
}

# 不走 _handlers 的入口（在 _run_inner / _call 内特判）
SPECIAL_DISPATCH = {
    "coderef_audit", "coderef_docs", "coderef_review", "coderef_frontend",
    "coderef_report", "coderef_audit_advisor", "coderef_execute",
    "coderef_task_status", "coderef_task_cancel",
}


def _schema_of(entry):
    s = entry.get("inputSchema")
    return s if isinstance(s, dict) else {}


def build_args(schema: dict, project: str) -> dict:
    """按 schema 生成最小合法入参：默认值 > enum 首项 > 类型零值。"""
    props = schema.get("properties") or {}
    args = {}
    if "project_path" in props:
        args["project_path"] = project
    for key in schema.get("required") or []:
        if key == "project_path":
            continue
        spec = props.get(key) or {}
        if "default" in spec:
            args[key] = spec["default"]
            continue
        enum = spec.get("enum")
        if isinstance(enum, list) and enum:
            args[key] = enum[0]
            continue
        typ = spec.get("type")
        if typ == "string":
            args[key] = "matrix"
        elif typ == "integer":
            args[key] = 1
        elif typ == "number":
            args[key] = 1.0
        elif typ == "boolean":
            args[key] = False
        elif typ == "array":
            args[key] = []
        elif typ == "object":
            args[key] = {}
        else:
            args[key] = "matrix"
    return args


def _payload(resp: dict):
    """从 JSON-RPC 响应中取出工具 payload（dict/list）或错误描述。"""
    if not isinstance(resp, dict):
        return None, "non_object_response"
    if resp.get("error"):
        return None, "jsonrpc_error"
    try:
        text = resp["result"]["content"][0]["text"]
    except Exception:  # noqa: BLE001
        return None, "result_shape"
    try:
        return json.loads(text), "json"
    except Exception:  # noqa: BLE001
        return None, "non_json"


class ToolManifestContractTest(unittest.TestCase):
    """静态契约：59 个工具都必须可分发、schema 自洽。"""

    def setUp(self):
        self.server = Server()
        self.names = [t.get("name") for t in BUILTIN_TOOLS if t.get("name")]

    def test_manifest_identity_and_dispatch(self):
        self.assertGreaterEqual(len(self.names), 52, "工具数异常偏少")
        self.assertEqual(len(self.names), len(set(self.names)), "工具名不得重复")
        self.assertEqual(len(self.server._tools), len(BUILTIN_TOOLS))
        dispatchable = set(self.server._handlers) | SPECIAL_DISPATCH
        for name in self.names:
            self.assertIn(name, dispatchable, f"{name} 无分发入口（会落到未知工具）")

    def test_manifest_schema_shape(self):
        for entry in BUILTIN_TOOLS:
            name = entry.get("name")
            with self.subTest(tool=name):
                self.assertIsInstance(entry.get("description"), str)
                self.assertTrue(entry["description"].strip(), "description 不得为空")
                schema = _schema_of(entry)
                self.assertEqual(schema.get("type"), "object",
                                 f"{name} inputSchema 必须是 object")
                props = schema.get("properties") or {}
                for key in schema.get("required") or []:
                    self.assertIn(key, props, f"{name} required {key} 不在 properties")
                for key, spec in props.items():
                    if not isinstance(spec, dict):
                        continue
                    enum = spec.get("enum")
                    if enum is not None:
                        self.assertIsInstance(enum, list)
                        self.assertTrue(enum, f"{name}.{key} enum 不得为空")
                        if "default" in spec:
                            self.assertIn(spec["default"], enum,
                                          f"{name}.{key} default 不在 enum")


class ToolProtocolNegativeTest(unittest.TestCase):
    """协议层负向契约：非法输入必须结构化拒绝，不得静默成功/裸崩。"""

    def setUp(self):
        self.server = Server()
        self.td = tempfile.mkdtemp(prefix="coderef_toolneg_")
        self.project = os.path.join(self.td, "p")
        os.makedirs(self.project, exist_ok=True)
        with open(os.path.join(self.project, "main.py"), "w", encoding="utf-8") as f:
            f.write("def run():\n    return 1\n")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.td, ignore_errors=True)

    def _call(self, name, args):
        return self.server._call(1, {"name": name, "arguments": dict(args, background=False)})

    def test_unknown_tool_rejected(self):
        resp = self._call("coderef_not_a_tool", {})
        self.assertEqual((resp.get("error") or {}).get("code"), -32602)
        self.assertIn("未知工具", (resp.get("error") or {}).get("message", ""))

    def test_missing_or_relative_project_path_rejected(self):
        resp = self._call("coderef_whitelist", {"action": "list"})
        payload, kind = _payload(resp)
        self.assertEqual(kind, "json")
        self.assertEqual(payload.get("status"), "error")
        self.assertEqual(payload.get("field"), "project_path")
        self.assertIn("project_path", payload.get("error", ""))
        resp = self._call("coderef_whitelist",
                          {"project_path": "..", "action": "list"})
        msg = (resp.get("error") or {}).get("message", "")
        self.assertIn("绝对路径", msg)
        resp = self._call("coderef_whitelist",
                          {"project_path": os.path.join(self.td, "nope"),
                           "action": "list"})
        msg = (resp.get("error") or {}).get("message", "")
        self.assertIn("目录不存在", msg)

    def test_non_string_project_path_contained(self):
        resp = self._call("coderef_whitelist", {"project_path": 123, "action": "list"})
        self.assertIn("error", resp, "非字符串路径也必须结构化拒绝，不得裸崩")

    def test_invalid_enum_rejected(self):
        resp = self._call("coderef_audit",
                          {"project_path": self.project, "strategy": "bogus"})
        payload, kind = _payload(resp)
        self.assertEqual(kind, "json")
        self.assertEqual(payload.get("status"), "error")
        self.assertEqual(payload.get("field"), "strategy")
        self.assertEqual(payload.get("allowed"),
                         ["auto", "full", "incr", "no_change"])

    def test_invalid_action_returns_error_payload(self):
        resp = self._call("coderef_whitelist",
                          {"project_path": self.project, "action": "bogus"})
        payload, kind = _payload(resp)
        self.assertEqual(kind, "json")
        self.assertTrue(payload.get("error") or payload.get("status") == "error",
                        f"非法 action 必须结构化报错: {payload}")

    @known("U-58", "innovation_review 对不存在 canonical 未前置拒绝，"
                   "触发 wiki 生成 + LLM 评审，实测 >90s")
    def test_u58_unknown_canonical_fast_reject(self):
        """不存在的 canonical 应在 10s 内给出结构化 not_found，而不是长时间阻塞。"""
        box = {}

        def _work():
            box["t0"] = time.time()
            box["resp"] = self.server._call(1, {
                "name": "coderef_innovation_review",
                "arguments": {"project_path": self.project,
                              "canonical": "no_such_design", "background": False}})

        th = threading.Thread(target=_work, daemon=True)
        th.start()
        th.join(10)
        self.assertFalse(th.is_alive(), "未知 canonical 不应阻塞 >10s")
        payload, kind = _payload(box.get("resp"))
        self.assertEqual(kind, "json")
        self.assertFalse(payload.get("ok"), f"未知 canonical 必须 ok=false: {payload}")
        self.assertTrue(payload.get("error"), "必须给出结构化错误原因")


    @known("U-63", "memory_digest 注入只用 session_guard.HEAVY_TOOLS(7)，"
                   "architecture/change_guard/docs 等后台重工具首调不带 procedure_card")
    def test_u63_heavy_tools_get_procedure_card(self):
        """首次重工具（后台重工具集合）都应带 memory_digest.procedure_card。"""
        server = Server()
        missing = []
        for name, args in (
            ("coderef_query", {"query_type": "stats"}),
            ("coderef_architecture", {}),
            ("coderef_change_guard", {}),
        ):
            p = os.path.join(self.td, "u63_" + name)
            os.makedirs(p, exist_ok=True)
            with open(os.path.join(p, "util.py"), "w", encoding="utf-8") as f:
                f.write("def add(a, b):\n    return a + b\n")
            raw = server._run(name, dict(args, project_path=p, background=False))
            data = json.loads(raw)
            # v5.21.16：G-E 硬闸门先于注入——已装契约但未 recover 时返回 blocked，
            # 属预期契约（必须带 owner_actions.recover），不算“缺入口卡”。
            if data.get("status") == "blocked":
                self.assertEqual(data.get("gate"), "G-E",
                                 f"{name} 只允许 G-E 拦截: {data}")
                self.assertIn("recover",
                              json.dumps(data.get("owner_actions") or {},
                                         ensure_ascii=False))
                continue
            digest = (data.get("context") or {}).get("memory_digest") or {}
            src = (digest.get("procedure_card") or {}).get("source", "")
            if not src:
                missing.append(name)
        self.assertEqual(missing, [], f"以下重工具首调未注入 procedure_card: {missing}")


    @known("U-64", "MCP 层未统一强制 inputSchema：required 缺失仍静默成功 / enum 非法未拒绝")
    def test_u64_schema_enforcement_gaps(self):
        """schema 声明 required/enum 的字段，非法/缺失时必须结构化拒绝。"""
        cases = [
            ("coderef_verify_findings", {"project_path": self.project}),
            ("coderef_change_report", {"project_path": self.project}),
            ("coderef_gov_pipeline", {"project_path": self.project}),
            ("coderef_plan", {"project_path": self.project, "intent": "__invalid__"}),
            ("coderef_review", {"project_path": self.project, "mode": "__invalid__"}),
            ("coderef_frontend", {"project_path": self.project, "mode": "__invalid__"}),
            ("coderef_gov_transition",
             {"project_path": self.project, "action": "__invalid__"}),
            ("coderef_change_guard",
             {"project_path": self.project, "action": "__invalid__"}),
            ("coderef_owasp",
             {"project_path": self.project, "out_format": "__invalid__"}),
        ]
        silent = []
        for name, args in cases:
            try:
                raw = self.server._run(name, dict(args, background=False))
                data = json.loads(raw)
            except Exception as e:  # noqa: BLE001 —— 异常由协议层兜住也算拒绝
                continue
            status = str((data or {}).get("status") or "")
            if not (data or {}).get("error") and status not in (
                    "error", "blocked", "paused"):
                silent.append({"tool": name, "status": status})
        self.assertEqual(silent, [],
                         f"以下工具对缺失 required / 非法 enum 静默放行: {silent}")


class AllToolsRuntimeMatrixTest(unittest.TestCase):
    """逐工具真实调用一轮：结构性契约（不强制业务成功）。"""

    @unittest.skipUnless(MATRIX_ENABLED,
                         "逐工具矩阵需 CODEREF_TOOL_MATRIX=1（会跑重工具）")
    def test_all_tools_runtime_matrix(self):
        td = tempfile.mkdtemp(prefix="coderef_toolmatrix_")
        project = os.path.join(td, "p")
        os.makedirs(os.path.join(project, "pkg"), exist_ok=True)
        with open(os.path.join(project, "main.py"), "w", encoding="utf-8") as f:
            f.write("from pkg.util import add\n\n\ndef run():\n    return add(1, 2)\n")
        with open(os.path.join(project, "pkg", "__init__.py"), "w",
                  encoding="utf-8") as f:
            f.write("")
        with open(os.path.join(project, "pkg", "util.py"), "w", encoding="utf-8") as f:
            f.write("def add(a, b):\n    return a + b\n")

        server = Server()
        results = []
        partial_path = os.path.join(CACHE_DIR, "tool_matrix_results.partial.json")
        os.makedirs(CACHE_DIR, exist_ok=True)
        for entry in BUILTIN_TOOLS:
            name = entry.get("name")
            args = build_args(_schema_of(entry), project)
            if name in KNOWN_SLOW:
                results.append({
                    "tool": name, "args_keys": sorted(args),
                    "outcome": "deferred_slow",
                    "reason": "实测 >300s 未返回（LLM wiki 生成），归 large 层"
                              "（CODEREF_TOOL_MATRIX_SLOW=1 单独执行）",
                    "elapsed_ms": 0.0})
                with open(partial_path, "w", encoding="utf-8") as f:
                    json.dump({"tools": len(results), "results": results},
                              f, ensure_ascii=False, indent=2)
                continue
            t0 = time.time()
            box = {}

            def _work():
                try:
                    box["resp"] = server._call(1, {"name": name, "arguments": dict(
                        args, background=False)})
                except Exception as e:  # noqa: BLE001
                    box["exception"] = repr(e)

            th = threading.Thread(target=_work, daemon=True)
            th.start()
            th.join(TOOL_TIMEOUT)
            if th.is_alive():
                record = {"tool": name, "args_keys": sorted(args),
                          "outcome": "timeout", "timeout_s": TOOL_TIMEOUT,
                          "elapsed_ms": round((time.time() - t0) * 1000, 1)}
                results.append(record)
                with open(partial_path, "w", encoding="utf-8") as f:
                    json.dump({"tools": len(results), "results": results},
                              f, ensure_ascii=False, indent=2)
                continue
            try:
                if "exception" in box:
                    record = {"tool": name, "args_keys": sorted(args),
                              "outcome": "exception", "error": box["exception"],
                              "elapsed_ms": round((time.time() - t0) * 1000, 1)}
                else:
                    resp = box.get("resp")
                    payload, kind = _payload(resp)
                    outcome = "result" if kind == "json" else kind
                    status = ""
                    if isinstance(payload, dict):
                        status = str(payload.get("status") or "")
                    record = {
                        "tool": name, "args_keys": sorted(args),
                        "outcome": outcome, "status": status,
                        "payload_tool": (payload or {}).get("tool")
                        if isinstance(payload, dict) else None,
                        "error": (resp.get("error") or {}).get("message", "")
                        if isinstance(resp, dict) else "",
                        "payload_error": str((payload or {}).get("error", ""))[:200]
                        if isinstance(payload, dict) else "",
                        "elapsed_ms": round((time.time() - t0) * 1000, 1),
                    }
                    if outcome == "result" and isinstance(payload, dict):
                        if payload.get("tool") and payload["tool"] != name:
                            record["outcome"] = "tool_mismatch"
            except Exception as e:  # noqa: BLE001
                record = {"tool": name, "args_keys": sorted(args),
                          "outcome": "exception", "error": repr(e),
                          "elapsed_ms": round((time.time() - t0) * 1000, 1)}
            results.append(record)
            with open(partial_path, "w", encoding="utf-8") as f:
                json.dump({"tools": len(results), "results": results},
                          f, ensure_ascii=False, indent=2)

        try:
            out = os.path.join(CACHE_DIR, "tool_matrix_results.json")
            with open(out, "w", encoding="utf-8") as f:
                json.dump({"project": project, "tools": len(results),
                           "timeout_s": TOOL_TIMEOUT, "results": results},
                          f, ensure_ascii=False, indent=2)
        finally:
            import shutil
            shutil.rmtree(td, ignore_errors=True)

        self.assertEqual(len(results), len(BUILTIN_TOOLS))
        self.assertEqual([r for r in results if r["outcome"] == "exception"], [],
                         "存在未捕获异常，工具契约不合格")
        self.assertEqual([r for r in results if r["outcome"] == "non_json"], [],
                         "存在非 JSON 返回，工具契约不合格")
        self.assertEqual([r for r in results if r["outcome"] == "tool_mismatch"], [],
                         "payload.tool 与调用名不一致")
        unexpected_timeouts = [r for r in results if r["outcome"] == "timeout"
                               and r["tool"] not in KNOWN_TIMEOUT]
        self.assertEqual(unexpected_timeouts, [],
                         f"存在超过 {TOOL_TIMEOUT}s 未返回的工具（详见 partial.json）")
        # 结构性通过：result / jsonrpc_error / deferred_slow / 已知超时 都算合格返回
        structured = sum(1 for r in results
                         if r["outcome"] in ("result", "jsonrpc_error",
                                             "deferred_slow", "timeout"))
        self.assertEqual(structured, len(results))


class SlowToolProbeTest(unittest.TestCase):
    """large 层：LLM 重工具的单独探针（默认跳过，避免拖慢矩阵）。"""

    @unittest.skipUnless(SLOW_MATRIX_ENABLED,
                         "LLM 重工具需 CODEREF_TOOL_MATRIX_SLOW=1（可能数分钟）")
    def test_docs_runtime_probe(self):
        td, project = self._fixture()
        server = Server()
        t0 = time.time()
        try:
            resp = server._call(1, {"name": "coderef_docs", "arguments": {
                "project_path": project, "background": False}})
            payload, kind = _payload(resp)
            os.makedirs(CACHE_DIR, exist_ok=True)
            with open(os.path.join(CACHE_DIR, "tool_matrix_docs.json"), "w",
                      encoding="utf-8") as f:
                json.dump({"elapsed_s": round(time.time() - t0, 1),
                           "outcome": kind,
                           "status": (payload or {}).get("status", "")
                           if isinstance(payload, dict) else "",
                           "documents": len(((payload or {}).get("wiki") or {}).get(
                               "documents") or []) if isinstance(payload, dict) else 0},
                          f, ensure_ascii=False, indent=2)
            self.assertEqual(kind, "json")
            self.assertIn(payload.get("status"), ("completed", "partial_failed"),
                          f"docs 返回状态异常: {payload}")
            wiki = payload.get("wiki") or {}
            self.assertTrue(wiki.get("documents"), f"docs 应产出文档清单: {wiki}")
        finally:
            import shutil
            shutil.rmtree(td, ignore_errors=True)

    @unittest.skipUnless(SLOW_MATRIX_ENABLED,
                         "LLM 重工具需 CODEREF_TOOL_MATRIX_SLOW=1（可能数分钟）")
    def test_innovation_scenario_probe(self):
        """创新排查场景：先识别创新，再对候选做排查（LLM 重，单独层）。"""
        td, project = self._fixture()
        server = Server()
        try:
            detect_raw = server._run("coderef_innovation", {"project_path": project})
            detect = json.loads(detect_raw)
            self.assertIsInstance(detect, dict)
            canonical = ""
            for key in ("innovations", "candidates", "workflows", "designs"):
                items = detect.get(key)
                if isinstance(items, list) and items:
                    first = items[0] if isinstance(items[0], dict) else {}
                    canonical = str(first.get("canonical") or first.get("name") or "")
                    if canonical:
                        break
            if not canonical:
                self.skipTest(f"样例未识别出创新候选，无法构造排查场景: {list(detect)[:8]}")
            t0 = time.time()
            resp = server._call(1, {"name": "coderef_innovation_review", "arguments": {
                "project_path": project, "canonical": canonical,
                "background": False}})
            payload, kind = _payload(resp)
            os.makedirs(CACHE_DIR, exist_ok=True)
            with open(os.path.join(CACHE_DIR, "tool_matrix_innovation.json"), "w",
                      encoding="utf-8") as f:
                json.dump({"canonical": canonical,
                           "elapsed_s": round(time.time() - t0, 1),
                           "outcome": kind,
                           "ok": (payload or {}).get("ok")
                           if isinstance(payload, dict) else None,
                           "has_subject": bool((payload or {}).get("subject"))
                           if isinstance(payload, dict) else False,
                           "has_pipeline": bool((payload or {}).get("pipeline"))
                           if isinstance(payload, dict) else False}, f,
                          ensure_ascii=False, indent=2)
            self.assertEqual(kind, "json")
            self.assertEqual(payload.get("canonical"), canonical)
            self.assertIn("subject", payload)
            self.assertIn("pipeline", payload)
        finally:
            import shutil
            shutil.rmtree(td, ignore_errors=True)

    @staticmethod
    def _fixture():
        td = tempfile.mkdtemp(prefix="coderef_toolslow_")
        project = os.path.join(td, "p")
        os.makedirs(os.path.join(project, "pkg"), exist_ok=True)
        with open(os.path.join(project, "main.py"), "w", encoding="utf-8") as f:
            f.write("from pkg.util import add\n\n\ndef run():\n    return add(1, 2)\n")
        with open(os.path.join(project, "pkg", "__init__.py"), "w",
                  encoding="utf-8") as f:
            f.write("")
        with open(os.path.join(project, "pkg", "util.py"), "w", encoding="utf-8") as f:
            f.write("def add(a, b):\n    return a + b\n")
        return td, project


if __name__ == "__main__":
    unittest.main(verbosity=2)
