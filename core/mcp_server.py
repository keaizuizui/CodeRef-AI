# -*- coding: utf-8 -*-
"""
CodeRef MCP Server v4.6 — 四大引擎 + 工具
  审计引擎     → coderef_audit / coderef_scan / coderef_scan_list / architecture / docs / query / review / frontend / whitelist / task_status
  记忆引擎     → coderef_memory（5.12.2 合并 sync/query/status/quality）/ coderef_operation_memory（5.12.3 合并 6 工具）
  创新识别引擎 → coderef_innovation / asset / replicate / replicate_apply / asset_blueprint / registry
  变更守护引擎 → coderef_change_guard / change_report
  OWASP 合规   → coderef_owasp
  Prompt 治理  → coderef_prompt_governance（4.6 合并原 prompt_mgmt / prompt_audit）
  人话解读     → coderef_interpret
"""

import json, sys, os, logging, traceback, threading, time, uuid
from typing import Dict, List, Any
from contextlib import contextmanager

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# 指标清单同源引用：coderef_eval 的 schema enum 与 core.output_evaluator.VALID_METRICS
# 保持唯一真源（新增指标只改 output_evaluator 一处，schema 自动同步，防双处漂移）
from core.output_evaluator import VALID_METRICS as _EVAL_METRICS
# 工具面 profile：只控制 tools/list 暴露面，不删工具与 handler
from core.tool_profile import current_profile, filter_tools

# 统一取包版本号，避免 serverInfo 与 __init__.py / README 版本漂移。
# 版本号唯一真源在 core/version.py，直接 import 而非文本解析（解析易受格式漂移影响）。
def _pkg_version() -> str:
    try:
        from core.version import __version__
        return __version__
    except Exception:
        # 版本模块不可导入时回退默认版本号
        pass
    return "4.0.0"

PKG_VERSION: str = _pkg_version()

logging.basicConfig(level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler(sys.stderr)])
logger = logging.getLogger("coderef")


# ─── 后台任务超时兜底常量 ─────────────────────────────────────
# 超大项目上单个后台工具（如 coderef_docs 全量生成、coderef_scan 的 gov 全量盘点）
# 一次调用可能远超 host 层轮询上限（真实案例 poll_timeout=900 用尽整步失败）。
# 后台线程为 daemon 会一直跑下去，_tsk 原只判 thread.is_alive()，永远返回 running，
# 导致外层 AI 在轮询用尽后拿到空结果。此处定义超时阈值：线程运行超过该秒数后，
# _tsk 不再无限返回 running，而是返回"部分完成 + 未完成提示"的诚实结构化结果，
# 让外层 AI 在放弃轮询前能拿到非空、不误导的部分结论（宁可部分完成，不要静默失败）。
# 取略低于常见 host 轮询上限（900s），保证 partial 状态在轮询用尽前可被读到。
MAX_BG_TASK_SECONDS = 860
# 分片提示门槛：后台任务运行超过该秒数时，在 partial 结果中建议调用方改用分片/增量
# 方式继续（如 coderef_audit incr、对子项目/维度逐个扫描），提示分片为更稳妥的路径。
BG_PARTIAL_SUGGEST_SECONDS = 300

# ：协作式取消专用异常（定义于 core.pipeline_runner，供 docs/audit/wiki 管线
# re-raise 穿透；_bg 内延迟 import 复用，避免顶部加载整条管线）。
# 用户在阶段汇报点请求取消时，_bg 的 progress 回调抛此异常让后台线程尽早收尾，
# 并单独标记为"用户取消"而非普通 error。


# ─── 工具可靠性清单 ─────────────────────────────────────────────
# 汇总工具的可信度与使用边界，注入到 MCP 元数据（initialize.serverInfo.reliability +
# tools/list 各工具 description），让外层 LLM 在选工具时主动避开误报、改用更合适的工具。
# 依据：对多轮真实审计结果的人工复核（区分工具误报 vs 代码真问题）。
RELIABILITY_GUIDE = (
    "CodeRef 工具可靠性清单（供外层 AI 选工具时参考）：\n"
    "【可靠，可放心日常使用】coderef_audit / coderef_scan / coderef_owasp / "
    "coderef_architecture / coderef_change_guard / coderef_change_report / coderef_query / "
    "coderef_review / coderef_memory / coderef_operation_memory / coderef_task_status / coderef_verify_findings / "
    "coderef_prompt_governance / coderef_flow_verify / coderef_arch_audit。\n"
    "【有使用边界，需注意场景】\n"
    "  - coderef_frontend：仅静态枚举按钮/菜单'是否存在'，不确证'交互逻辑正确'；SPA 组件逻辑改用 mode=runtime 浏览器抽查。\n"
    "  - coderef_scan/audit 的 sca 维度（CVE 扫描）：不可全信。会把 poetry 配置 priority='primary' 误当依赖、"
    "把 >= 范围约束当固定版本。CVE 类发现需人工对照真实 CVE 库复核，勿直接采信。\n"
    "  - coderef_review：规则级 + LLM 审查，语义级缺陷（XSS/死代码/跨平台）覆盖有限；"
    "持有 LLM/CodeRabbit 论断时先用 coderef_verify_findings 做确定性核验再采信。\n"
    "【误报已修复】标准库 import 不再被 memory_quality 判为孤儿边；PII 拼接不再误报 api_address 等技术变量；"
    "测试目录默认不参与 agent 安全审计。\n"
    "【协作建议】收到 CVE/安全类发现时先人工复核再决定是否修复；超大文件/结构债属改进建议非缺陷。"
)

# 逐工具边界提示（追加到 tools/list 对应工具的 description 末尾）
TOOL_BOUNDARY_NOTES = {
    "coderef_scan": (
        "\n\n[可靠性] sca 维度(CVE)有已知误报：会把 poetry 配置 priority='primary' 误当依赖、"
        "把 >= 范围约束当固定版本。CVE 类结果需人工复核，勿直接采信。"
    ),
    "coderef_frontend": (
        "\n\n[可靠性] 仅静态扫描 HTML 按钮/菜单树，不适合 React/Vue SPA 组件逻辑；"
        "SPA 请改用 mode=runtime 做浏览器抽查。"
    ),
    "coderef_audit": (
        "\n\n[可靠性] 依赖扫描(CVE)子项有已知误报（见 coderef_scan 提示），"
        "CVE 类发现请人工复核后再决定是否修复。"
    ),
    "coderef_owasp": (
        "\n\n[可靠性] 其 CVE 检测复用 SCA，存在与 coderef_scan 相同的误报边界，违规项需人工复核。"
    ),
    "coderef_verify_findings": (
        "\n\n[可靠性] verdict 由确定性逻辑打出，只核验'引用目标是否存在/是否在管线内'，"
        "不核验'论断的语义结论是否正确'；图谱对动态调用/反射不完整，未确证不代表一定不存在。"
    ),
    "coderef_prompt_governance": (
        "\n\n[可靠性] 纯编排 + 确定性规则（含资产生命周期、合规审计与跨模块一致性，"
        "4.6 已合并原 coderef_prompt_mgmt / coderef_prompt_audit 全部能力），不引入 LLM；"
        "各维度如实标注是否已执行，不把'未审计'渲染成'无风险'；跨模块漂移是风险提示而非已发生故障。"
    ),
    "coderef_replicate": (
        "\n\n[可靠性] 缺口判定为确定性签名比对，只报告'有/没有'，不臆断'该不该采用'；"
        "复刻指引是铺排建议，不自动改代码；template_code 缺失会标注待补全。"
    ),
    "coderef_replicate_apply": (
        "\n\n[可靠性] 只落地'确定性可给'的骨架与说明，不自动接入目标源码；"
        "默认不覆盖已存在文件（冲突如实标注），overwrite=true 才允许覆盖。"
    ),
    "coderef_asset_blueprint": (
        "\n\n[可靠性] 仅写回确定性可填字段（entry_points / verified_findings），"
        "不臆断 steps；蓝图完整性取决于已固化资产质量。"
    ),
    "coderef_interpret": (
        "\n\n[可靠性] 人话解读全部来自确定性原语（健康分/审计/图谱/合规/论断核验），"
        "不引入 LLM 给结论；健康分只在确实审计过时给出，未审计绝不臆断；"
        "Wiki 等依赖 LLM 的能力在无 API Key 时诚实阻断。"
    ),
}

# ── 工具注册数据外置（core.tools_manifest）──────────────────────
# BUILTIN_TOOLS 描述表与注册表同源常量（_SINGLE_TOOL_LABELS/WHITELIST_ACTIONS）
# 已整体外置，此处 re-export 保持符号面与 Server.__init__ 引用不变。
from core.tools_manifest import BUILTIN_TOOLS, _SINGLE_TOOL_LABELS, WHITELIST_ACTIONS

# 工具名 → inputSchema：协议入口统一校验 required/enum 的单一事实源
# （U-64：此前 required 缺失/枚举非法只靠各 handler 自行处理，覆盖不一致）
_SCHEMAS = {
    t.get("name"): t.get("inputSchema") or {}
    for t in BUILTIN_TOOLS if isinstance(t, dict) and t.get("name")
}


def _schema_error(n, a):
    """按 inputSchema 校验必填与 enum；返回结构化错误或 None。"""
    schema = _SCHEMAS.get(n)
    if not isinstance(schema, dict) or not isinstance(a, dict):
        return None
    for req in schema.get("required") or []:
        if req not in a or a.get(req) is None:
            return {"status": "error", "tool": n,
                    "error": f"缺少必填参数: {req}", "field": req}
    props = schema.get("properties") or {}
    for key, spec in props.items():
        if key not in a or not isinstance(spec, dict):
            continue
        allowed = spec.get("enum")
        if isinstance(allowed, list) and a[key] not in allowed:
            return {"status": "error", "tool": n,
                    "error": f"参数 {key} 非法: {a[key]!r}",
                    "field": key, "allowed": allowed}
    return None

# ── 模块级 handler 外置（core.handlers_*）────────────────────────
# handler 函数体按领域搬至 handlers_arch/engine/misc，此处重新绑定；
# Server 类 _handlers 分发表与同名 wrapper 原封不动，引用语义不变。
from core.handlers_arch import (
    _arch, _arch_audit, _arch_canvas, _arch_gap, _arch_verify,
    _coerce_target_arch, _dynamic_probe, _flow_canvas, _flow_verify,
    _gov_board, _gov_close, _gov_issues, _gov_pipeline, _gov_report,
    _gov_schedule, _gov_start, _gov_transition, _gov_workspace,
    _load_target_arch, _refactor_plan, _role_boundary, _target_adopt,
    _target_arch_get, _target_arch_path, _target_arch_set,
    _validate_project_path,
)
from core.handlers_engine import (
    _asset, _asset_blueprint, _change_guard, _change_report, _govern,
    _innovation, _innovation_review, _interpret, _memory,
    _operation_memory, _owasp, _prompt_audit, _prompt_mgmt, _registry,
    _replicate, _replicate_apply,
)
from core.handlers_misc import (
    _advisor, _dispatch_metrics, _docs_read, _dream, _eval, _frontend,
    _query, _report, _review, _scan_list, _scan_tool, _scope,
    _verify_findings, _version, _wl,
)
# 5.19.1 结果级新鲜度硬戳：信封统一挂 freshness 块（best-effort，失败不影响结论）
from core.freshness_guard import attach_freshness as _env_with_freshness

# 对外符号面（re-export 兼容层）：拆解后主文件保留全部既有符号，__all__ 仅用于
# 声明"重导出符号"以通过 pyflakes 未使用 import 检查（无 star import 引用方，语义不变）。
__all__ = [
    # 常量
    "PKG_VERSION", "RELIABILITY_GUIDE", "TOOL_BOUNDARY_NOTES",
    "MAX_BG_TASK_SECONDS", "BG_PARTIAL_SUGGEST_SECONDS",
    "_SINGLE_TOOL_LABELS", "WHITELIST_ACTIONS", "BUILTIN_TOOLS",
    "_EVAL_METRICS", "_MCP_OUT_MAX", "_MCP_OUT_TTL", "_MCP_OUT_MAX_FILES", "_WAIT_TIMEOUT",
    # typing 引入（拆解后符号面保留）
    "List",
    # 版本
    "_pkg_version",
    # 辅助
    "_evict_finished_tasks", "_prune_mcp_out", "_ok",
    # 模块级 handler（handlers_arch / engine / misc 重新绑定）
    "_scan_tool", "_scan_list", "_version", "_eval",
    "_change_guard", "_change_report", "_memory", "_operation_memory",
    "_prompt_mgmt", "_prompt_audit", "_owasp", "_innovation", "_asset",
    "_registry", "_replicate", "_replicate_apply", "_innovation_review",
    "_asset_blueprint", "_govern", "_interpret", "_docs_read",
    "_flow_verify", "_arch_audit", "_target_arch_set", "_target_arch_get",
    "_target_adopt", "_arch_gap", "_arch_canvas", "_flow_canvas",
    "_refactor_plan", "_arch_verify", "_gov_start", "_gov_close",
    "_gov_issues", "_gov_transition", "_gov_report", "_gov_pipeline",
    "_dynamic_probe", "_gov_board", "_gov_workspace", "_gov_schedule",
    "_role_boundary", "_verify_findings", "_review", "_frontend",
    "_report", "_advisor", "_arch", "_wl", "_query", "_scope",
    "_dispatch_metrics", "_dream",
    # arch 系辅助
    "_target_arch_path", "_load_target_arch", "_coerce_target_arch",
    "_validate_project_path",
    # 类与入口
    "Server", "main",
]

def _evict_finished_tasks(tasks: Dict[str, Any], max_age: int = 3600, max_size: int = 50):
    """清除已完成且过期的后台任务，避免 _tasks 无限增长。

    策略：1) 超过 max_age 秒的已完成任务直接清除；
          2) 已完成任务数超过 max_size 时，按 finished_at 从旧到新清除。
    """
    import time
    now = time.time()
    # 清除超时的已完成任务
    expired = [
        tid for tid, t in tasks.items()
        if t.get("finished_at") is not None and now - t["finished_at"] > max_age
    ]
    for tid in expired:
        del tasks[tid]
    # 如果已完成任务仍过多，按时间从旧到新清除
    finished = sorted(
        [(tid, t) for tid, t in tasks.items() if t.get("finished_at") is not None],
        key=lambda x: x[1]["finished_at"],
    )
    while len(finished) > max_size:
        tid, _ = finished.pop(0)
        if tid in tasks:
            del tasks[tid]



_MCP_OUT_MAX = 10 * 1024  # 大结果落盘阈值：超过则写入文件，避免 MCP text 转义膨胀/超限
_MCP_OUT_TTL = 3600       # 落盘结果保留时长（秒），过期文件在下次写入时清理
_MCP_OUT_MAX_FILES = 200  # 落盘结果目录内最多保留文件数，超出按最旧淘汰
_WAIT_TIMEOUT = 300       # wait=true 阻塞等待上限（秒），超时返回 task_id 供轮询


def _prune_mcp_out(out_dir: str):
    """有界保留落盘结果：清理过期文件，超出数量上限时按最旧淘汰。"""
    try:
        now = time.time()
        files = []
        for fn in os.listdir(out_dir):
            fp = os.path.join(out_dir, fn)
            if not os.path.isfile(fp):
                continue
            try:
                age = now - os.path.getmtime(fp)
            except Exception:
                age = _MCP_OUT_TTL + 1
            if age > _MCP_OUT_TTL:
                try:
                    os.remove(fp)
                except Exception:
                    pass
            else:
                files.append(fp)
        if len(files) > _MCP_OUT_MAX_FILES:
            files.sort(key=lambda p: os.path.getmtime(p))
            for fp in files[:-_MCP_OUT_MAX_FILES]:
                try:
                    os.remove(fp)
                except Exception:
                    pass
    except Exception:
        pass


def _ok(rid, text, tool=None):
    # 大 JSON 结果落盘：合法 JSON 且超过阈值时，写入 cache/mcp_out/ 并返回
    # 文件路径 + 摘要，调用方用 Read 读文件而非解析层层转义的字符串。
    # coderef_docs_read 返回文档正文（可能恰好是 JSON 大文本），保留直接
    # content 字段不被落盘替换（CodeRabbit major）。
    if (isinstance(text, str) and len(text) > _MCP_OUT_MAX
            and tool != "coderef_docs_read"):
        try:
            parsed = json.loads(text)
        except Exception:
            parsed = None
        if parsed is not None:
            try:
                import hashlib
                out_dir = os.path.join(os.path.dirname(os.path.dirname(
                    os.path.abspath(__file__))), "cache", "mcp_out")
                os.makedirs(out_dir, exist_ok=True)
                _prune_mcp_out(out_dir)
                h = hashlib.md5(text.encode("utf-8")).hexdigest()[:12]
                fp = os.path.join(out_dir, f"result_{h}.json")
                tmp = f"{fp}.{os.getpid()}.tmp"
                with open(tmp, "w", encoding="utf-8") as f:
                    f.write(text)
                os.replace(tmp, fp)  # 原子替换，避免半写文件被读到
                summary = {
                    "note": "结果较大，已落盘（避免 MCP 转义膨胀）。请用 Read 读取文件内容。",
                    "file_path": fp,
                    "chars": len(text),
                }
                text = json.dumps(summary, ensure_ascii=False)
            except Exception:
                pass  # 落盘失败则原样返回，不阻断
    return {"jsonrpc":"2.0","id":rid,"result":{"content":[{"type":"text","text":text}]}}


def _merge_session_context(raw: str, ctx: dict) -> str:
    """把会话上下文（自动守卫 / 记忆摘要）合入工具 JSON 返回。

    非 JSON 或非对象返回原样透传；合并失败不丢原结果（best-effort）。
    """
    try:
        data = json.loads(raw)
    except Exception:  # noqa: BLE001
        return raw
    if not isinstance(data, dict):
        return raw
    existing = data.get("context")
    if isinstance(existing, dict):
        existing.update(ctx)
    else:
        data["context"] = ctx
    try:
        return json.dumps(data, ensure_ascii=False)
    except Exception:  # noqa: BLE001
        return raw


# ─── 5.19.5 观测：从工具返回与会话事实提取审计字段（best-effort） ───

def _audit_gate(gate: dict, checked: bool) -> dict:
    """闸门判定：hit（命中）/pass（检查且放行）/miss（未检查）。"""
    hits = [h.get("id") for h in (gate.get("hits") or []) if h.get("id")]
    if hits:
        return {"result": "hit", "hits": hits,
                "mode": gate.get("mode", "warn")}
    return {"result": "pass" if checked else "miss", "hits": [],
            "mode": gate.get("mode", "")}


def _audit_freshness(raw: str) -> dict:
    """从返回体提取 freshness 判定（信封内或 query 顶层）。"""
    try:
        data = json.loads(raw)
    except Exception:  # noqa: BLE001
        return {}
    if not isinstance(data, dict):
        return {}
    fresh = data.get("freshness")
    if not isinstance(fresh, dict):
        env = data.get("envelope")
        fresh = env.get("freshness") if isinstance(env, dict) else None
    if not isinstance(fresh, dict):
        return {}
    return {"code_newer": bool(fresh.get("code_newer")),
            "stale_reason": str(fresh.get("stale_reason") or "")[:200],
            "refresh_action": str(fresh.get("refresh_action") or "")[:200]}


def _audit_scope(project_path: str) -> dict:
    try:
        from core.scope import load_scope
        rules = load_scope(project_path).get("rules") or []
        return {"rules": len(rules)} if rules else {}
    except Exception:  # noqa: BLE001
        return {}


class Server:

    def __init__(self):
        self._tools = list(BUILTIN_TOOLS)
        # ── 单维度审计工具（coderef_scan）：11 个维度合并为 1 个工具
        #    实时安全带 + 客观第二意见：AI 写完一个模块即可按需调用单维度自查，
        #    无需跑全量 coderef_audit。用 tool 参数选维度，避免 11 个同构工具
        #    撑爆工具列表、增加 AI 选择负担。复用 pipeline_runner.run_single()。
        # 维度注册表提升为模块级常量 _SINGLE_TOOL_LABELS（BUILTIN_TOOLS 中
        # coderef_scan 的 enum 同源引用），此处仅做引用，避免重复字面量漂移。
        self._SINGLE_TOOL_LABELS = _SINGLE_TOOL_LABELS
        # ── 流程合规验证：非编程人员验证项目是否按期望流程执行 ──
        # ── 架构腐化诊断：非编程人员验证工程结构是否健康 ──
        # ── 诚实话解读护栏：确定性核验 LLM / CodeRabbit 论断 ──
        # ── 引擎四 · 变更守护：AI 代码退化检测 + 人话版变更报告 ──
        # ── 引擎一 · 记忆层（M1/M2/M3）──────────────────────────────
        # ── 引擎 · 操作记忆层（4.8）──────────────────────────────
        # 与 memory_layer（记忆"代码是什么"）互补：操作记忆记忆"东西在哪儿、
        # 从哪儿来、到哪儿去、过去的规范是什么"，应对对话过多后上下文丢失。
        # 引擎 · Prompt 治理（4.6 合并收敛：原 coderef_prompt_mgmt / coderef_prompt_audit
        # 已并入 coderef_prompt_governance 唯一入口，见下方 governance 工具定义）
        # ── 引擎三 · OWASP LLM 合规（M4）────────────────────────────
        # ── 引擎二 · 创新识别 + 资产沉淀（M6/M7）────────────────────
        self._tasks: Dict[str, Any] = {}
        # 并发保护：多 Agent 后台任务可能同时读写 _tasks，用可重入锁保证一致性
        self._lock = threading.RLock()

        # ─── 统一工具分发映射 ─────────────────────────────────────────
        # 工具名 -> handler。收敛 _call 里散落的 if/elif，让所有工具都能复用统一的
        # background 后台化逻辑。audit/docs 的 handler 额外接收 progress_cb 上报阶段进度。
        self._handlers = {
            "coderef_whitelist": self._wl,
            "coderef_scope": self._scope,
            "coderef_plan": self._dispatch_plan,
            "coderef_execute": self._dispatch_execute,
            "coderef_dispatch_status": self._dispatch_status,
            "coderef_dispatch_report": self._dispatch_report,
            "coderef_dispatch_metrics": self._dispatch_metrics,
            "coderef_dream": self._dream,
            "coderef_architecture": self._arch,
            "coderef_docs_read": self._docs_read,
            "coderef_query": self._query,
            "coderef_review": self._review,
            "coderef_frontend": self._frontend,
            "coderef_report": self._report,
            "coderef_audit_advisor": self._advisor,
            "coderef_scan": self._scan_tool,
            "coderef_scan_list": lambda a: self._scan_list(),
            "coderef_flow_verify": self._flow_verify,
            "coderef_arch_audit": self._arch_audit,
            "coderef_target_arch_set": self._target_arch_set,
            "coderef_target_arch_get": self._target_arch_get,
            "coderef_target_adopt": self._target_adopt,
            "coderef_arch_gap": self._arch_gap,
            "coderef_arch_canvas": self._arch_canvas,
            "coderef_flow_canvas": self._flow_canvas,
            "coderef_refactor_plan": self._refactor_plan,
            "coderef_arch_verify": self._arch_verify,
            "coderef_gov_start": self._gov_start,
            "coderef_gov_close": self._gov_close,
            "coderef_gov_issues": self._gov_issues,
            "coderef_gov_transition": self._gov_transition,
            "coderef_gov_report": self._gov_report,
            "coderef_gov_pipeline": self._gov_pipeline,
            "coderef_dynamic_probe": self._dynamic_probe,
            "coderef_gov_board": self._gov_board,
            "coderef_gov_workspace": self._gov_workspace,
            "coderef_gov_schedule": self._gov_schedule,
            "coderef_role_boundary": self._role_boundary,
            "coderef_verify_findings": self._verify_findings,
            "coderef_change_guard": self._change_guard,
            "coderef_change_report": self._change_report,
            "coderef_memory": self._memory,
            "coderef_operation_memory": self._operation_memory,
            # 4.6 兼容层：coderef_prompt_mgmt / coderef_prompt_audit 已从 tools/list 移除
            #（收敛到 coderef_prompt_governance 唯一入口），此处保留 handler 供旧调用向后兼容转发。
            "coderef_prompt_mgmt": self._prompt_mgmt,
            "coderef_prompt_audit": self._prompt_audit,
            "coderef_owasp": self._owasp,
            "coderef_innovation": self._innovation,
            "coderef_asset": self._asset,
            "coderef_replicate": self._replicate,
            "coderef_replicate_apply": self._replicate_apply,
            "coderef_asset_blueprint": self._asset_blueprint,
            "coderef_innovation_review": self._innovation_review,
            "coderef_registry": self._registry,
            "coderef_prompt_governance": self._govern,
            "coderef_interpret": self._interpret,
            "coderef_version": self._version,
            "coderef_eval": self._eval,
        }

        # ─── 重型工具：默认后台执行 ───────────────────────────────────
        # 解决 MCP 宿主层（Trae / Claude Desktop / Cursor 等任意客户端）对单次
        # tools/call 的超时限制：大项目同步全量必然超时（如 memory_sync 全量扫描
        # Python+Vue+Go，曾触发 Trae 的 REQUEST_TIMEOUT）。后台化后调用立即返回
        # running + task_id，由外层 AI 轮询 coderef_task_status 取结果，不再撞超时。
        # 轻量工具（query / scan_list / whitelist / docs_read 等）不在此列，保持同步
        # 快速返回。调用方可用 background=false 强制同步（小项目想立即拿结果时）。
        self.HEAVY_TOOLS = {
            "coderef_audit", "coderef_docs", "coderef_review", "coderef_frontend",
            "coderef_report", "coderef_audit_advisor", "coderef_architecture",
            "coderef_arch_canvas", "coderef_flow_canvas",
            "coderef_memory",
            "coderef_operation_memory",
            "coderef_owasp",
            "coderef_innovation", "coderef_asset", "coderef_change_guard",
            "coderef_change_report", "coderef_verify_findings",
            "coderef_replicate", "coderef_replicate_apply", "coderef_asset_blueprint",
            "coderef_innovation_review",
            "coderef_prompt_governance", "coderef_interpret",
            # coderef_scan 单维度在超大项目上也会全量盘点（如 gov 全项目治理审计，
            # 真实案例 目标项目 跑 131.5s 撞 rpc 层单次调用超时）。加入重工具集使其
            # 默认后台执行，单次 tools/call 立即返回 task_id，避免同步撞超时。
            "coderef_scan",
            # coderef_eval 是 LLM-as-judge 调用（单次评估多次 LLM 往返：多指标聚合
            # 逐指标打分，DeepSeek 单次可达数十秒）。入重工具集使其默认后台执行 +
            # 自动暴露 wait 参数（阻塞等待直接返回结果），避免同步撞 MCP 宿主超时。
            "coderef_eval",
            # dispatcher 执行链：默认后台（返回 task_id 轮询），避免同步撞宿主超时
            "coderef_execute",
            # 梦：action=run 会调主 LLM，默认后台；status/seed/read 见 MERGE_SYNC_ACTIONS
            "coderef_dream",
        }

        # ─── 合并工具内保持同步的轻量 action ───────────────────────────
        # B 方案（5.12.2/5.12.3）：记忆簇收敛为 coderef_memory / coderef_operation_memory
        # 后，整工具进 HEAVY_TOOLS 会让轻量 action 一并后台化。此集合内的 action 保持同步
        # （尤其 operation_memory 的 recover 是 workflow E 上下文丢失后「一次调用拿回」的
        # 强制 gate，不可后台化须先拿 task_id 再轮询）。行为与合并前完全一致。
        self.MERGE_SYNC_ACTIONS = {
            "coderef_memory": {"query"},
            "coderef_operation_memory": {"query", "find", "status", "recover", "export"},
            "coderef_dream": {"status", "seed", "read"},
        }

    def _should_background(self, n: str, a: Dict) -> bool:
        """决定工具是否后台执行：
        - background=true 显式要求 → 后台；background=false 显式要求 → 同步
        - 未指定 → 重型工具默认后台（合并工具内轻量 action 例外保持同步），轻量工具同步
        """
        explicit = a.get("background")
        if explicit is True:
            return True
        if explicit is False:
            return False
        # 梦的默认 action 是 status（schema 默认值），只有 run 才需要后台
        if n == "coderef_dream":
            return str(a.get("action") or "status") == "run"
        if n in self.MERGE_SYNC_ACTIONS and a.get("action", "sync") in self.MERGE_SYNC_ACTIONS[n]:
            return False
        return n in self.HEAVY_TOOLS

    @contextmanager
    def _locked_tasks(self):
        """加锁访问运行中的任务状态字典，保证并发下读写一致。

        用法: with self._locked_tasks() as tasks: ... 
        所有对 self._tasks 的读写都通过该访问器完成，避免散落裸锁。
        """
        with self._lock:
            yield self._tasks

    def _evict_finished_tasks(self, tasks: Dict[str, Any], max_age: int = 3600, max_size: int = 50):
        return _evict_finished_tasks(tasks, max_age, max_size)

    # ─── request ───

    def _handle(self, req: Dict) -> Dict:
        m, rid = req.get("method",""), req.get("id")
        if m == "initialize":
            # 工具面 profile：full（默认）暴露全部工具，lite 只暴露 12 个入口
            profile_info = self._tool_profile()
            exposed = len(filter_tools(self._tools, profile_info["profile"]))
            return {"jsonrpc":"2.0","id":rid,"result":{
                "protocolVersion":"2024-11-05","capabilities":{"tools":{}},
                "serverInfo":{
                    "name":"coderef-ai","version":PKG_VERSION,
                    "reliability": RELIABILITY_GUIDE,
                    "toolProfile": profile_info["profile"],
                    "toolsExposed": exposed,
                    "toolsHidden": len(self._tools) - exposed,
                }}}
        if m == "notifications/initialized": return None
        if m == "tools/list":
            # profile 过滤在追加边界提示/wait 参数之前，保持 full 行为不变
            tools = []
            for t in filter_tools(self._tools):
                note = TOOL_BOUNDARY_NOTES.get(t["name"], "")
                if note:
                    t = dict(t)
                    t["description"] = (t.get("description", "") + note).strip()
                # 重型工具统一暴露 wait 参数（wait=true 阻塞等待直接返回结果，
                # 免轮询 coderef_task_status），与 _call 的实际行为对齐（CodeRabbit minor）
                if t["name"] in self.HEAVY_TOOLS:
                    t = dict(t)
                    schema = t.get("inputSchema") or {}
                    props = schema.get("properties")
                    if isinstance(props, dict) and "wait" not in props:
                        props = dict(props)
                        props["wait"] = {"type": "boolean",
                                         "description": "阻塞等待任务完成，直接返回最终结果（免轮询 coderef_task_status）；默认 False 返回 task_id",
                                         "default": False}
                        t["inputSchema"] = dict(schema)
                        t["inputSchema"]["properties"] = props
                tools.append(t)
            return {"jsonrpc":"2.0","id":rid,"result":{"tools":tools}}
        if m == "tools/call":
            return self._call(rid, req.get("params",{}))
        return {"jsonrpc":"2.0","id":rid,"error":{"code":-32601,"message":f"未知: {m}"}}

    def _call(self, rid, params):
        n, a = params.get("name",""), params.get("arguments",{})
        try:
            # 状态查询/取消永远同步返回，不后台化
            if n == "coderef_task_status":
                return self._ok(rid, self._tsk(a), tool=n)
            if n == "coderef_task_cancel":
                return self._ok(rid, self._task_cancel(a), tool=n)
            # 未知工具提前拦截，避免落入 _run 才报"未知"
            if n not in self._handlers and n not in ("coderef_audit", "coderef_docs"):
                return {"jsonrpc":"2.0","id":rid,"error":{"code":-32602,"message":f"未知工具: {n}"}}
            # U-64：协议入口先按 inputSchema 校验，再决定是否后台化；
            # 避免非法调用先拿 task_id、后台线程里才报错。
            invalid = _schema_error(n, a)
            if invalid:
                return self._ok(rid, json.dumps(invalid, ensure_ascii=False), tool=n)
            # 统一后台化决策：重型工具默认后台，轻量工具同步；background 显式参数可覆盖
            if self._should_background(n, a):
                tid = str(uuid.uuid4())[:8]; rc = {}
                a = dict(a)  # 不修改调用方字典
                # dispatcher 需要知道自己的 task_id 才能把轨迹落到同一文件名
                a["_task_id"] = tid
                t = threading.Thread(target=lambda: self._bg(rc, n, a), daemon=True)
                t.start()
                with self._locked_tasks() as tasks:
                    tasks[tid] = {"thread":t,"result":rc,"tool":n,
                                  "started_at":time.time()}
                logger.info(f"后台: {tid} {n}")
                # wait=True → 阻塞等待任务完成，直接返回最终结果（免轮询）。
                # 有界超时：挂起的审计/LLM/文件操作不能无限阻塞串行 MCP 请求循环
                # （CodeRabbit major），超时返回 task_id 供调用方轮询。
                if a.get("wait"):
                    t.join(timeout=_WAIT_TIMEOUT)
                    if t.is_alive():
                        return self._ok(rid, json.dumps(
                            {"status": "running", "task_id": tid,
                             "note": "等待超时，任务仍在后台运行，请用 coderef_task_status 查询"},
                            ensure_ascii=False), tool=n)
                    with self._locked_tasks() as tasks:
                        tasks.pop(tid, None)
                    if rc.get("error"):
                        return self._ok(rid, json.dumps(
                            {"status": "error", "error": rc["error"]},
                            ensure_ascii=False), tool=n)
                    return self._ok(rid, rc.get("result") or json.dumps(
                        {"status": "done", "task_id": tid}, ensure_ascii=False), tool=n)
                return self._ok(rid, json.dumps({"status":"running","task_id":tid,
                    "message":f"已启动。coderef_task_status(task_id='{tid}') 查询进度"}, ensure_ascii=False), tool=n)
            return self._ok(rid, self._run(n, a), tool=n)
        except Exception as e:
            return {"jsonrpc":"2.0","id":rid,"error":{"code":-32000,"message":str(e)}}

    def _bg(self, rc, n, a):
        import time
        from core.pipeline_runner import TaskCancelled as _TaskCancelled
        try:
            # progress 回调：每个阶段完成后写入共享 rc，_tsk 据此回传进度；
            # 协作式取消（）：收到 coderef_task_cancel 后 rc.cancelled=True，
            # 下一个阶段汇报点抛出 TaskCancelled 让任务尽早收尾而非无限跑到底。
            def prog(stage, done, total, detail=None):
                if rc.get("cancelled"):
                    raise _TaskCancelled()
                rc["progress"] = {"stage": stage, "done": done, "total": total, "detail": detail}
            rc["result"] = self._run(n, a, progress_cb=prog)
        except _TaskCancelled:
            rc["cancelled_at"] = time.time()   # 用户取消，非错误
        except Exception as e: rc["error"] = str(e); rc["tb"] = traceback.format_exc()
        finally:
            # 任务终态标记完成时间（无论成败），供 _tsk / 调用方判断终态
            rc["finished_at"] = time.time()

    def _scan_tool(self, a: dict):
        return _scan_tool(a)

    def _scan_list(self):
        return _scan_list()

    def _tool_profile(self) -> dict:
        return current_profile()

    def _version(self, a: dict):
        raw = _version(a)
        # 版本探针附加工具面 profile；解析失败时原样返回，不破坏探针
        try:
            data = json.loads(raw)
            if not isinstance(data, dict):
                return raw
            info = self._tool_profile()
            exposed = len(filter_tools(self._tools, info.get("profile")))
            data["tool_profile"] = {
                "profile": info.get("profile", "full"),
                "source": info.get("source", "default"),
                "error": info.get("error", ""),
                "tools_exposed": exposed,
                "tools_hidden": len(self._tools) - exposed,
            }
            return json.dumps(data, ensure_ascii=False)
        except Exception:  # noqa: BLE001
            return raw

    def _eval(self, a: dict):
        return _eval(a)

    def _change_guard(self, a: dict):
        return _change_guard(a)

    def _change_report(self, a: dict):
        return _change_report(a)

    # ── 引擎一 · 记忆层 ─────────────────────────────────────────────
    def _memory(self, a: dict):
        return _memory(a)

    def _operation_memory(self, a: dict):
        return _operation_memory(a)

    def _prompt_mgmt(self, a: dict):
        return _prompt_mgmt(a)

    def _prompt_audit(self, a: dict):
        return _prompt_audit(a)

    # ── 引擎三 · OWASP 合规 ────────────────────────────────────────
    def _owasp(self, a: dict):
        return _owasp(a)

    # ── 引擎二 · 创新识别 + 资产沉淀 ───────────────────────────────
    def _innovation(self, a: dict):
        return _innovation(a)

    def _asset(self, a: dict):
        return _asset(a)

    def _registry(self, a: dict):
        return _registry(a)

    def _replicate(self, a: dict):
        return _replicate(a)

    def _replicate_apply(self, a: dict):
        return _replicate_apply(a)

    def _innovation_review(self, a: dict):
        return _innovation_review(a)

    def _asset_blueprint(self, a: dict):
        return _asset_blueprint(a)

    def _govern(self, a: dict):
        return _govern(a)

    def _interpret(self, a: dict):
        return _interpret(a)

    def _docs_read(self, a: dict):
        return _docs_read(a)

    def _flow_verify(self, a: dict):
        return _flow_verify(a)

    def _arch_audit(self, a: dict):
        return _arch_audit(a)

    def _target_arch_set(self, a: dict):
        return _target_arch_set(a)

    def _target_arch_get(self, a: dict):
        return _target_arch_get(a)

    def _target_adopt(self, a: dict):
        return _target_adopt(a)

    def _arch_gap(self, a: dict):
        return _arch_gap(a)

    def _arch_canvas(self, a: dict):
        return _arch_canvas(a)

    def _flow_canvas(self, a: dict):
        return _flow_canvas(a)

    def _refactor_plan(self, a: dict):
        return _refactor_plan(a)

    def _arch_verify(self, a: dict):
        return _arch_verify(a)

    def _gov_start(self, a: dict):
        return _gov_start(a)

    def _gov_close(self, a: dict):
        return _gov_close(a)

    def _gov_issues(self, a: dict):
        return _gov_issues(a)

    def _gov_transition(self, a: dict):
        return _gov_transition(a)

    def _gov_report(self, a: dict):
        return _gov_report(a)

    def _gov_pipeline(self, a: dict):
        return _gov_pipeline(a)

    def _dynamic_probe(self, a: dict):
        return _dynamic_probe(a)

    def _gov_board(self, a: dict):
        return _gov_board(a)

    def _gov_workspace(self, a: dict):
        return _gov_workspace(a)

    def _gov_schedule(self, a: dict):
        return _gov_schedule(a)

    def _role_boundary(self, a: dict):
        return _role_boundary(a)

    def _verify_findings(self, a: dict):
        return _verify_findings(a)

    def _validate_project_path(self, tool: str, p: str):
        return _validate_project_path(tool, p)

    def _run(self, n, a, progress_cb=None) -> str:
        """工具执行入口：precondition 闸门 → 执行 → 按需附加会话上下文。

        5.19.x：把「改完代码要跑变更守卫」「先恢复记忆」「先 sync 图谱」从
        skill 文字变成服务端自动行为——闸门命中按配置返回 blocked 或
        gate_warnings；重工具返回体自带 `context.guard` 与
        `context.memory_digest`。5.19.5 起各防护点判定落盘 audit_log。
        """
        invalid = _schema_error(n, a)
        if invalid:
            return json.dumps(invalid, ensure_ascii=False)
        p = a.get("project_path") or ""
        gate = {}
        audit = None
        if p:
            try:
                from core import audit_log as audit
            except Exception:  # noqa: BLE001
                audit = None
        if p:
            try:
                from core.preconditions import blocked_result, evaluate
                gate = evaluate(n, p, a)
                if gate.get("blocked"):
                    blocked = blocked_result(n, gate.get("blocking") or {})
                    if audit is not None:
                        audit.record(p, {
                            "tool": n,
                            "gate": _audit_gate(
                                gate, checked=bool(gate.get("checked"))),
                            "blocked": {
                                "gate": (gate.get("blocking") or {}).get("id", ""),
                                "reason": (gate.get("blocking") or {}).get("reason", "")},
                            "scope": _audit_scope(p),
                        })
                    return json.dumps(blocked, ensure_ascii=False)
            except Exception as e:  # noqa: BLE001 —— 闸门故障不拦调用
                logger.debug(f"[preconditions] 闸门检查失败（放行）: {e}")
                gate = {}

        raw = self._run_inner(n, a, progress_cb)
        if not p:
            return raw
        entry = {"tool": n,
                 "gate": _audit_gate(gate, checked=bool(gate.get("checked"))),
                 "scope": _audit_scope(p)}
        try:
            from core.session_guard import HEAVY_TOOLS as GUARD_TOOLS, guard_if_needed
        except Exception:  # noqa: BLE001
            if audit is not None:
                audit.record(p, entry)
            return raw
        # U-63：注入判定与后台化重工具取并集。此前只看 session_guard 的 7 个
        # 守卫工具，architecture/change_guard/docs 等 20 个后台重工具首调拿不到
        # memory_digest/procedure_card；两处集合不再分叉。
        if n not in GUARD_TOOLS and n not in getattr(self, "HEAVY_TOOLS", ()):
            entry["freshness"] = _audit_freshness(raw)
            if audit is not None:
                audit.record(p, entry)
            return raw
        ctx = {}
        if gate.get("hits"):
            ctx["gate_warnings"] = gate["hits"]
        if n in GUARD_TOOLS:
            try:
                guard = guard_if_needed(p)
                if guard:
                    ctx["guard"] = guard
            except Exception as e:  # noqa: BLE001
                logger.debug(f"[session] 自动守卫失败: {e}")
        try:
            from core.memory_digest import inject_once
            digest = inject_once(p)
            if digest is not None:
                ctx["memory_digest"] = digest
                entry["memory_digest"] = {
                    "injected": True,
                    "available": bool(digest.get("available")),
                    "items": len(digest.get("items") or []),
                }
                # G-A 的事实来源：只有操作记忆 recover 成功才算完成 recover；
                # 梦单独可用（recovered_from=dream）不代表操作记忆已恢复。
                if "operation_memory" in str(digest.get("recovered_from") or ""):
                    from core.session_guard import mark_fact
                    mark_fact(p, "memory_recovered_at",
                              {"items": len(digest.get("items") or [])})
        except Exception as e:  # noqa: BLE001
            logger.debug(f"[session] 记忆摘要注入失败: {e}")
        entry["freshness"] = _audit_freshness(raw)
        if audit is not None:
            audit.record(p, entry)
        if not ctx:
            return raw
        return _merge_session_context(raw, ctx)

    def _run_inner(self, n, a, progress_cb=None) -> str:
        from core.pipeline_runner import Pipe
        # 部分工具（如 coderef_scan_list）不依赖 project_path，用容错读取避免误抛 KeyError
        p, o = a.get("project_path", ""), a.get("output_dir")
        # P1高-3：project_path 校验与规范化（coderef_scan_list / coderef_version
        # 不依赖路径，跳过；coderef_gov_workspace 用 projects 数组而非 project_path，也跳过）。
        # 无效路径返回结构化错误而非空成功；相对路径（../空串）拒绝，禁止越权扫描。
        if n not in ("coderef_scan_list", "coderef_gov_workspace", "coderef_version"):
            p = self._validate_project_path(n, p)
            a["project_path"] = p
            # 5.20.0 下次启动补梦：首次带项目路径的调用触发后台线程，
            # 不阻塞本次工具响应；每进程每项目只尝试一次。
            if n != "coderef_dream":
                try:
                    from core.dream import trigger_if_due
                    trigger_if_due(p)
                except Exception:  # noqa: BLE001 —— 补梦失败不影响主流程
                    pass
        logger.info(f"[{n}] {p}")
        if n == "coderef_audit":
            strat = a.get("strategy", "auto")
            # P2-2：审计策略严格枚举校验（大小写敏感）。非法策略此前静默按默认
            # (full) 执行，调用方无法区分显式传错与未指定；现改为结构化错误。
            if strat not in ("auto", "full", "incr", "no_change"):
                raise ValueError(
                    f"coderef_audit: 未知审计策略 '{strat}'，支持(大小写敏感): "
                    f"auto/full/incr/no_change")
            strat = None if strat == "auto" else strat
            r = Pipe().audit(p, output_dir=o, progress_cb=progress_cb,
                             strategy=strat,
                             with_dashboard=bool(a.get("with_dashboard", False)))
            # 审计刚写出 findings/kg：清空新鲜度盘点缓存，避免信封引用旧盘点（5.19.6）
            try:
                from core.freshness_guard import invalidate as _invalidate_freshness
                _invalidate_freshness(p)
            except Exception:  # noqa: BLE001
                pass
            d = getattr(r, 'dashboard_path', '')
            from core.result_envelope import for_audit
            # 结构化返回：携带落盘路径与明细统计，避免调用方只看得到摘要
            return json.dumps({
                "status": "completed",
                "tool": n,
                "project_path": p,
                "report": r.report,
                "report_path": r.report_path or "",
                "dashboard_path": d or "",
                "strategy": getattr(r, "audit_strategy", "full"),
                "evidence": {
                    "scan_ts": getattr(r, "scan_ts", ""),          # 本次扫描时间戳
                    "kg_built_at": getattr(r, "kg_built_at", ""),   # 知识图谱构建时间
                    "file_snapshot": getattr(r, "file_snapshot", {}),  # 本次扫描文件快照
                    "note": "evidence.scan_ts 为本次审计实际扫描时间；kg_built_at 为知识图谱构建时间，两者不一致时说明图谱可能滞后于代码，查询类结果请以重建后为准。统计口径仅覆盖 file_snapshot 所列文件，不代表修复状态。",
                },
                "summary": {
                    "total_files": getattr(r, "total_files", 0),
                    "total_lines": getattr(r, "total_lines", 0),
                    "findings": len(r.findings),
                    "high": sum(1 for f in r.findings if f.tier.value == "high"),
                    "medium": sum(1 for f in r.findings if f.tier.value == "medium"),
                    "low": sum(1 for f in r.findings if f.tier.value == "low"),
                    "elapsed": getattr(r, "elapsed", 0),
                },
                "errors": getattr(r, "errors", []),
                # 结论信封：调用方 AI 可直接转述给人（含归属/限制/不可下结论项）
                "envelope": _env_with_freshness(
                    for_audit(len(r.findings), getattr(r, "errors", [])),
                    p, ["kg_db", "audit_findings"]),
            }, ensure_ascii=False)
        elif n == "coderef_docs":
            # 与 coderef_audit 的 strategy 一致：非法枚举显式拒绝（结构化错误），
            # 不静默回落默认值，避免调用方误以为用上了自定义风格。
            try:
                from core.wiki_generator import WikiGenerator
                _ws = getattr(WikiGenerator, "WIKI_STYLES", {}) or {}
            except Exception:
                _ws = {}
            # 仅在 key 完全缺失时才回落默认值；显式传入空串/非字符串/非法枚举
            # 一律结构化拒绝，避免调用方误以为用上了目标风格
            ws = a["wiki_style"] if "wiki_style" in a else "comprehensive"
            if not isinstance(ws, str) or ws not in _ws:
                return json.dumps({
                    "status": "error",
                    "tool": n,
                    "error": (f"非法 wiki_style: {ws!r}；允许的取值为 "
                              "comprehensive / reference / tutorial / plain"),
                }, ensure_ascii=False)
            r = Pipe().docs(p, output_dir=o,
                            wiki_style=ws,
                            include_subprojects=True if a.get("include_subprojects", True) else False,
                            enable_agent_pointer=True if a.get("enable_agent_pointer", False) else False,
                            cross_verify=True if a.get("cross_verify", True) else False,
                            cross_entry_spec=a.get("cross_entry_spec") or "class:pipeline_runner:Pipe",
                            # ：docs 也透传阶段进度回调，取消检查点覆盖扫描/图谱/wiki 生成阶段
                            progress_cb=progress_cb)
            wr = getattr(r, "wiki_result", None)
            # 结构化返回：携带输出目录/文档清单/失败明细，让调用方能区分"全量成功"与"部分失败"，
            # 避免部分文档生成失败时仍被当作 fully completed。
            return json.dumps({
                "status": "completed" if not getattr(r, "errors", []) else "partial_failed",
                "tool": n,
                "project_path": p,
                "report": getattr(r, "report", ""),
                "errors": getattr(r, "errors", []),
                "wiki": {
                    "output_dir": getattr(wr, "output_dir", "") if wr else "",
                    "documents": getattr(wr, "documents", []) if wr else [],
                    "module_count": getattr(wr, "module_count", 0) if wr else 0,
                    "subprojects": getattr(wr, "subproject_results", []) if wr else [],
                },
            }, ensure_ascii=False)
        elif n == "coderef_review":
            return self._review(a)
        elif n == "coderef_frontend":
            return self._frontend(a)
        elif n == "coderef_report":
            return self._report(a)
        elif n == "coderef_audit_advisor":
            return self._advisor(a)
        elif n == "coderef_execute":
            return self._dispatch_execute(a, progress_cb)
        # 其余工具统一走 _handlers 分发（query / memory_* / owasp / innovation /
        # asset / change_* / scan / whitelist / registry / docs_read 等），
        # 使后台线程与同步路径都能执行任意工具，避免各自散落 if/elif。
        handler = self._handlers.get(n)
        if handler is not None:
            return handler(a)
        return "未知工具: " + n

    def _review(self, a):
        return _review(a)

    def _frontend(self, a):
        return _frontend(a)

    def _report(self, a):
        return _report(a)

    def _advisor(self, a):
        return _advisor(a)

    def _arch(self, a):
        return _arch(a)

    def _wl(self, a):
        return _wl(a)

    def _scope(self, a):
        return _scope(a)

    # ─── dispatcher（5.19.3 形态 A：规则引擎调度） ───

    def _dispatch_plan(self, a):
        from core import dispatcher
        return json.dumps(
            dispatcher.plan(a.get("intent", "probe"), a["project_path"],
                            intent_text=a.get("intent_text", "")),
            ensure_ascii=False)

    def _dispatch_execute(self, a, progress_cb=None):
        from core import dispatcher
        pp = a["project_path"]
        plan_id = a.get("plan_id") or ""
        # 形态 B 的确认门：自然语言意图必须先经 coderef_plan 出计划卡，再 execute
        if a.get("intent_text") and not plan_id:
            return json.dumps({
                "status": "blocked", "tool": "coderef_execute",
                "reason": "自然语言意图需先 coderef_plan 生成计划卡并确认后再执行",
                "owner_actions": {"consumer": [
                    "coderef_plan(intent_text=...) 查看计划卡",
                    "coderef_execute(plan_id=...)"],
                },
            }, ensure_ascii=False)
        card = (dispatcher.load_plan(pp, plan_id) if plan_id
                else dispatcher.plan(a.get("intent") or "probe", pp))
        if plan_id and card is None:
            return json.dumps({
                "status": "error", "tool": "coderef_execute",
                "error": f"计划不存在或 ID 非法: {plan_id}",
            }, ensure_ascii=False)
        if not isinstance(card, dict) or card.get("status") != "ok":
            return json.dumps(
                card if isinstance(card, dict)
                else {"status": "error", "error": "计划卡无效"},
                ensure_ascii=False)
        res = dispatcher.execute(
            card, lambda tool, args: self._run(tool, args, None), pp,
            task_id=a.get("_task_id") or "", progress_cb=progress_cb)
        return json.dumps(res, ensure_ascii=False)

    def _dispatch_status(self, a):
        from core import dispatcher
        return json.dumps(
            dispatcher.status(a["project_path"], a.get("task_id", "")),
            ensure_ascii=False)

    def _dispatch_report(self, a):
        from core import dispatcher
        return json.dumps(
            dispatcher.report(a["project_path"], a.get("task_id", "")),
            ensure_ascii=False)

    def _dispatch_metrics(self, a):
        return _dispatch_metrics(a)

    def _dream(self, a):
        return _dream(a)

    def _tsk(self, a) -> str:
        import time
        tid = a.get("task_id","")
        if not tid:
            with self._locked_tasks() as tasks:
                self._evict_finished_tasks(tasks)
                return json.dumps({"tasks":list(tasks.keys())})
        with self._locked_tasks() as tasks:
            self._evict_finished_tasks(tasks)
            t = tasks.get(tid)
            if not t: return json.dumps({"error":f"不存在: {tid}"})
            if t["thread"].is_alive():
                # ：收到 coderef_task_cancel 后，即便线程仍在收尾也返回可定位的
                # cancelled 状态，避免外层 AI 继续以 running 无限轮询/误判悬置。
                if t.get("cancelled"):
                    return json.dumps({"status": "cancelled", "task_id": tid,
                                       "message": "任务已请求取消，正在收尾，可稍后重查最终状态"},
                                      ensure_ascii=False)
                # 超时兜底：超大项目单次后台任务可能远超 host 层轮询上限而一直 running，
                # 导致外层 AI 轮询（poll_timeout）用尽后整步失败、结果被丢弃。运行超过
                # 阈值时改返回"部分完成 + 未完成提示"的诚实结构化结果，让调用方在放弃
                # 轮询前能拿到非空、不误导的部分结论（宁可部分完成，不要静默失败）。
                elapsed = time.time() - t.get("started_at") if t.get("started_at") else 0.0
                if elapsed > MAX_BG_TASK_SECONDS:
                    rc = t["result"]
                    prog = rc.get("progress")
                    # 结果已完整产出（线程仅在收尾）→ 直接按完成返回，带上真实 content，
                    # 避免把"已完成数据"错装成空壳 partial 让调用方误判未完成而丢弃。
                    # （coderef_scan 这类整段 run_single 完成后才一次性产出 findings。）
                    if rc.get("result"):
                        return json.dumps({
                            "status": "completed", "task_id": tid,
                            "elapsed": round(elapsed, 1),
                            "content": rc["result"],
                        }, ensure_ascii=False)
                    # 超时兜底仅在确有部分结果可交换时才返回 partial；否则（既无 result
                    # 也无 progress）直接返回 running + 超时说明，避免调用方把空壳 partial
                    # 当终态、丢弃之后才完成的完整结果。coderef_docs 等有增量产物的工具
                    # 仍保留 partial。
                    if not prog:
                        return json.dumps({
                            "status": "running", "task_id": tid,
                            "elapsed": round(elapsed, 1),
                            "message": (
                                f"任务已运行 {round(elapsed,1)}s，超过后台兜底阈值 "
                                f"({MAX_BG_TASK_SECONDS}s)，仍在后台继续执行，可稍后重查；"
                                "当前尚无部分结果可先行使用。"
                            ),
                        }, ensure_ascii=False)
                    partial = {
                        "status": "partial",
                        "task_id": tid,
                        "elapsed": round(elapsed, 1),
                        "partial": True,
                        "message": (
                            f"任务已运行 {round(elapsed,1)}s，超过后台兜底阈值 "
                            f"({MAX_BG_TASK_SECONDS}s)，仍在后台继续执行，可稍后重查；"
                            "本次返回当前已处理部分，未完成清单见下。"
                        ),
                    }
                    if prog:
                        partial["progress"] = prog
                        partial["progress_text"] = (
                            f"已完成 {prog['done']}/{prog['total']} 阶段，当前: {prog['stage']}"
                            + (f"（{prog.get('detail')}）" if prog.get("detail") else "")
                        )
                    if elapsed > BG_PARTIAL_SUGGEST_SECONDS:
                        partial["suggestion"] = (
                            "超大项目建议改用分片/增量方式：coderef_audit 用 incr 策略，"
                            "或对子项目/维度逐个扫描，"
                            "避免单次全量超时。已生成的产物（文档/报告）已按模块落盘，可先行使用。"
                        )
                    return json.dumps(partial, ensure_ascii=False)
                # 运行中：回传当前阶段进度（若有）
                rc = t["result"]
                prog = rc.get("progress")
                if prog:
                    detail = prog.get("detail")
                    base = f"已完成 {prog['done']}/{prog['total']} 阶段，当前: {prog['stage']}"
                    if detail:
                        base += f"（{detail}）"
                    return json.dumps({
                        "status": "running", "task_id": tid,
                        "progress": prog,
                        "progress_text": base,
                    }, ensure_ascii=False)
                return json.dumps({"status":"running","task_id":tid})
            rc = t["result"]
            # 标记完成时间戳，不再首次读取即删除；由 _evict_finished_tasks 按年龄/大小清除
            if "finished_at" not in t:
                t["finished_at"] = time.time()
            # ：取消后终态（协作式收尾，非 error）
            if rc.get("cancelled"):
                return json.dumps({"status":"cancelled","task_id":tid,
                                   "message":"任务已取消","content":rc.get("result","")}, ensure_ascii=False)
            if "error" in rc:
                return json.dumps({"status":"error","task_id":tid,"error":rc["error"]})
            r = rc.get("result","")
        return json.dumps({"status":"completed","task_id":tid,"content":r}, ensure_ascii=False)

    def _task_cancel(self, a) -> str:
        """：取消一个运行中的后台任务（状态可定位，不再把悬置任务误报 running）。"""
        tid = a.get("task_id", "")
        if not tid:
            return json.dumps({"error": "缺少 task_id"}, ensure_ascii=False)
        with self._locked_tasks() as tasks:
            self._evict_finished_tasks(tasks)
            t = tasks.get(tid)
            if not t:
                return json.dumps({"error": f"不存在: {tid}"}, ensure_ascii=False)
            # 线程已结束：非取消目标，如实返回其终态（若曾取消则保持 cancelled，勿退化为 completed）
            if not t["thread"].is_alive():
                rc = t["result"]
                if rc.get("cancelled"):
                    return json.dumps({"status": "cancelled", "task_id": tid,
                                       "message": "任务已取消"}, ensure_ascii=False)
                status = "error" if "error" in rc else "completed"
                return json.dumps({"status": status, "task_id": tid,
                                   "message": "任务已结束，无需取消"}, ensure_ascii=False)
            if t.get("cancelled"):
                return json.dumps({"status": "cancelled", "task_id": tid,
                                   "message": "该任务已请求取消，可稍后重查最终状态"}, ensure_ascii=False)
            # 标记取消：写入共享 rc，供 _bg 的 progress 回调在下一阶段点协作式收尾
            t["cancelled"] = True
            t["result"]["cancelled"] = True
        return json.dumps({"status": "cancelled", "task_id": tid,
                           "message": ("已请求取消；任务在下一个阶段汇报点协作式收尾，"
                                       "可稍后用 coderef_task_status 查询最终结果")}, ensure_ascii=False)

    def _start_sync_task(self, project_path: str) -> str:
        """为陈旧图谱排入后台增量同步，返回 task_id。

        5.19.1：coderef_query 发现 stale 时自动触发，调用方只需轮询一次
        coderef_task_status；同一项目已有同步在跑时不重复建图（返回空串）。
        """
        try:
            from core.freshness_guard import claim_sync, release_sync
        except Exception:  # noqa: BLE001
            return ""
        if not claim_sync(project_path):
            return ""
        tid = str(uuid.uuid4())[:8]
        rc = {}
        args = {"project_path": project_path, "action": "sync", "mode": "incr"}

        def _run_sync():
            try:
                self._bg(rc, "coderef_memory", args)
            finally:
                release_sync(project_path)

        t = threading.Thread(target=_run_sync, daemon=True)
        t.start()
        with self._locked_tasks() as tasks:
            tasks[tid] = {"thread": t, "result": rc, "tool": "coderef_memory",
                          "started_at": time.time()}
        logger.info(f"后台增量同步: {tid} {project_path}")
        return tid

    def _query(self, a):
        raw = _query(a)
        pp = a.get("project_path") or ""
        if not pp:
            return raw
        try:
            from core import freshness_guard as fg
        except Exception:  # noqa: BLE001
            return raw
        try:
            info = fg.query_staleness(pp)
            # block 档：同步刷新图谱后重查，调用方拿到的就是新图结果
            if info.get("stale") and info.get("mode") == "block":
                from core.memory_layer import memory_layer
                memory_layer.sync(pp, mode="incr")
                raw = _query(a)
                return fg.annotate_query_result(raw, pp, refreshed=True)
            tid = self._start_sync_task(pp) if info.get("stale") else ""
            return fg.annotate_query_result(raw, pp, sync_task_id=tid)
        except Exception as e:  # noqa: BLE001 —— stale 感知失败不阻断原查询
            logger.debug(f"[freshness] 查询 stale 感知失败: {e}")
            return raw

    def _ok(self, rid, text, tool=None):
        return _ok(rid, text, tool=tool)

    def run(self):
        # 强制 stdin/stdout 为 UTF-8，解决 Windows 下中文参数/输出乱码
        # （TRAE 经 stdio 发送的 JSON 是 UTF-8 字节，stdin 若按 GBK 解码，
        #   中文 output_dir 等参数会变成乱码目录名，见 ）
        import io
        if hasattr(sys.stdout, 'reconfigure'):
            sys.stdout.reconfigure(encoding='utf-8')
        else:
            sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
        if hasattr(sys.stdin, 'reconfigure'):
            sys.stdin.reconfigure(encoding='utf-8')
        else:
            sys.stdin = io.TextIOWrapper(sys.stdin.buffer, encoding='utf-8')
        logger.info(f"CodeRef MCP v{PKG_VERSION} (audit|arch|docs) 启动")
        try:
            # 手动 readline 阻塞读，替代 `for line in sys.stdin`：
            # 迭代器在后台守护线程并发场景下会提前返回 EOF（即使 stdin 仍打开、fd0
            # 仍有效），导致 server 主线程提前退出、后台任务被丢弃。
            while True:
                line = sys.stdin.readline()
                if not line:
                    break
                line = line.strip()
                if not line:
                    continue
                try:
                    req = json.loads(line)
                    resp = self._handle(req)
                    if resp is not None:
                        sys.stdout.write(json.dumps(resp, ensure_ascii=False)+"\n")
                        sys.stdout.flush()
                except json.JSONDecodeError: pass
                except Exception as e:
                    sys.stdout.write(json.dumps({"jsonrpc":"2.0","id":None,
                        "error":{"code":-32000,"message":str(e)}}, ensure_ascii=False)+"\n")
                    sys.stdout.flush()
        except Exception as e:
            logger.error(f"[RUN-EXC] {type(e).__name__}: {e}")
            import traceback; logger.error(traceback.format_exc())
        return

def main(): Server().run()
if __name__ == "__main__": main()
