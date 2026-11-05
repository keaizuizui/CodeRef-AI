# -*- coding: utf-8 -*-
"""business_semantics — 业务叙事标注层（v5.18.0）。

问题背景：静态分析只能产出「谁调用谁」，布局器拿不到叙事信号时只能按目录顺序
铺开，画布看起来就是一格一格的方阵。本模块在「图谱事实」与「画布布局」之间补一层
**业务语义标注**：

    actor      这个模块服务于谁（泳道）
    stage      这件事发生在业务流程的哪一步
    handoff    跨泳道 / 跨阶段的调用 = 交接点
    value_node 关键交付点（写库、导出、发布、扣款）

分工（与全仓「确定性为锚、LLM 只做叙事」一致）：
  - 静态启发式提供**事实锚点**：模块路径、调用边、跨泳道/跨阶段关系；
  - LLM 只对静态判不出的节点做**命名与归类**（输出结构化标签，不是散文）；
  - 无 LLM Key 时保留静态结果，推不出的进 `unlabeled` 泳道并如实计数，
    **不阻断出图、不编造语义**。

产物：`.coderef/business_semantics.json`（供画布与下游 AI 消费）。
"""

import json
import os
import re
from datetime import datetime
from typing import Dict, List, Optional, Tuple

from loguru import logger

from core.version import __version__

# ── 阶段（按业务流程顺序，顺序即画布 X 轴） ──────────────────────────
STAGES: List[Tuple[str, str]] = [
    ("trigger", "触发"),
    ("validate", "准入校验"),
    ("execute", "执行"),
    ("persist", "落库"),
    ("notify", "通知"),
    ("deliver", "交付"),
]
STAGE_IDS = [s[0] for s in STAGES]
UNLABELED_STAGE = "unlabeled"

# ── 泳道（顺序即画布 Y 轴） ─────────────────────────────────────
ACTORS: List[Tuple[str, str]] = [
    ("user_ui", "用户界面"),
    ("api", "接口层"),
    ("core_logic", "业务执行"),
    ("storage", "数据存储"),
    ("payment", "支付结算"),
    ("notify_ext", "通知外发"),
    ("scheduler", "定时任务"),
    ("infra", "基础设施"),
]
ACTOR_IDS = [a[0] for a in ACTORS]
UNLABELED_ACTOR = "unlabeled"

# 关键字 → actor（按顺序匹配，先命中先归属；词边界匹配路径）
_ACTOR_RULES: List[Tuple[str, Tuple[str, ...]]] = [
    ("user_ui", ("web", "frontend", "ui", "views", "pages", "components", "templates")),
    ("api", ("api", "routes", "router", "handlers", "handler", "controllers",
             "controller", "endpoints", "interface", "interfaces")),
    ("payment", ("pay", "payment", "billing", "checkout", "refund")),
    ("notify_ext", ("notify", "notification", "message", "messaging", "email",
                    "sms", "webhook", "push")),
    ("scheduler", ("scheduler", "cron", "jobs", "job", "worker", "queue", "celery")),
    ("storage", ("db", "database", "repository", "repo", "dao", "store", "storage",
                 "models", "orm", "migrations")),
    ("core_logic", ("service", "services", "agent", "agents", "pipeline", "pipelines",
                    "engine", "engines", "workflow", "workflows", "domain",
                    "application", "logic", "runtime", "task", "tasks",
                    # 常见业务动作型模块名（数据/产物处理都属于业务执行）
                    "ingest", "reader", "import", "importer", "parser", "parse",
                    "collect", "collector", "sync", "analytics", "analysis",
                    "report", "reports", "export", "exporter", "generator",
                    "generators", "extract", "transformer", "builder",
                    "dispatcher", "orchestrator", "selector", "scorer", "filter")),
    ("infra", ("config", "configs", "utils", "util", "common", "shared", "lib",
               "infra", "infrastructure", "logging", "logger", "monitor", "metrics",
               "tools", "tool", "helpers", "helper")),
]

# 关键字 → stage（按顺序匹配；先命中先归属）
_STAGE_RULES: List[Tuple[str, Tuple[str, ...]]] = [
    ("trigger", ("entry", "main", "cli", "start", "bootstrap", "init",
                 "trigger", "schedule", "cron", "webhook_receive")),
    ("validate", ("valid", "validator", "check", "verify", "guard", "auth",
                  "permission", "sanitize", "schema", "rule", "policy")),
    ("persist", ("save", "store", "persist", "write", "dao", "repository",
                 "repo", "db", "migration", "upsert")),
    ("notify", ("notify", "send", "message", "email", "sms", "push", "webhook_send")),
    ("deliver", ("export", "report", "render", "download", "publish", "output",
                 "generate", "print", "share")),
    ("execute", ("pipeline", "process", "processor", "engine", "service",
                 "handler", "executor", "worker", "agent", "task", "runner")),
]

# 价值交付点：写操作 / 对外发布 / 生成产物
_VALUE_HINTS = ("save", "write", "persist", "export", "publish", "download",
                "generate", "create", "update", "delete", "pay", "charge", "refund")

_MAX_LLM_MODULES = 60


def _load_graph(project_path: str):
    try:
        from core.arch_audit import locate_kg_db, module_of
        from core.graph_closure import load_graph
        db = locate_kg_db(project_path)
        if not db:
            return None, {}, {}, module_of
        nodes, adj = load_graph(db)
        return db, nodes, adj, module_of
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[business_semantics] 知识图谱读取失败: {e}")
        return None, {}, {}, None


def _hit(haystack: str, needles: Tuple[str, ...]) -> bool:
    """按路径段/词边界匹配关键字，避免 core 命中 scorecard 这类误判。"""
    for n in needles:
        if re.search(r"(?<![a-z0-9])" + re.escape(n) + r"(?![a-z0-9])", haystack):
            return True
    return False


def _classify_actor(module: str) -> Optional[str]:
    low = module.lower().replace("\\", "/")
    for actor, words in _ACTOR_RULES:
        if _hit(low, words):
            return actor
    return None


def _classify_stage(module: str) -> Optional[str]:
    low = module.lower().replace("\\", "/")
    for stage, words in _STAGE_RULES:
        if _hit(low, words):
            return stage
    return None


def _is_value_node(module: str) -> bool:
    low = module.lower().replace("\\", "/")
    return _hit(low, _VALUE_HINTS)


def _module_anchors(project_path: str, modules: List[str]) -> Dict[str, str]:
    """模块 → `rel.py#L1` 锚点（供画布点击回到源码）。"""
    out: Dict[str, str] = {}
    for m in modules:
        for ext in (".py", ".ts", ".tsx", ".js", ".jsx", ".vue"):
            fp = os.path.join(project_path, m + ext)
            if os.path.isfile(fp):
                out[m] = f"{m}{ext}#L1"
                break
    return out


def _llm_refine(nodes: List[dict]) -> Tuple[List[dict], str]:
    """对未判定节点做一次 LLM 命名归类；不可用/失败原样返回并给出原因。"""
    pending = [n for n in nodes
               if n["actor"] == UNLABELED_ACTOR or n["stage"] == UNLABELED_STAGE]
    if not pending:
        return nodes, ""
    try:
        from core.llm_integration import LLMIntegration
        llm = LLMIntegration()
        if not llm.is_available():
            return nodes, "未配置 LLM API Key，未做业务语义命名"
    except Exception as e:  # noqa: BLE001
        return nodes, f"LLM 初始化失败: {e}"

    payload = [{"module": n["module"], "value_node": n["value_node"]}
               for n in pending[:_MAX_LLM_MODULES]]
    prompt = (
        "你是业务流程标注助手。下面是从代码里自动提取的模块清单（只有路径事实）。\n"
        "请为每个模块判断两个标签，只能从给定枚举里选：\n"
        f"- actor ∈ {ACTOR_IDS}\n"
        f"- stage ∈ {STAGE_IDS}\n"
        "规则：只根据模块名/路径推断，判断不了就回 null，不要猜。\n"
        "只输出 JSON：{\"模块名\": {\"actor\": \"...或null\", \"stage\": \"...或null\"}}。\n"
        f"模块清单：{json.dumps(payload, ensure_ascii=False)}")
    try:
        raw = llm.chat_completion([{"role": "user", "content": prompt}],
                                  response_format={"type": "json_object"},
                                  max_tokens=2000)
        if LLMIntegration.is_error_response(raw):
            return nodes, f"LLM 调用失败: {raw[:120]}"
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", (raw or "").strip())
        start, end = text.find("{"), text.rfind("}")
        if start < 0 or end <= start:
            return nodes, "LLM 返回无法解析为 JSON"
        out = json.loads(text[start:end + 1])
        if not isinstance(out, dict):
            return nodes, "LLM 返回结构不是对象"
    except Exception as e:  # noqa: BLE001
        return nodes, f"LLM 调用异常: {e}"

    refined = 0
    for n in nodes:
        got = out.get(n["module"])
        if not isinstance(got, dict):
            continue
        touched = False
        if n["actor"] == UNLABELED_ACTOR and got.get("actor") in ACTOR_IDS:
            n["actor"] = got["actor"]
            n["evidence_level"] = "E4"
            touched = True
        if n["stage"] == UNLABELED_STAGE and got.get("stage") in STAGE_IDS:
            n["stage"] = got["stage"]
            n["evidence_level"] = "E4"
            touched = True
        if touched:
            refined += 1
    return nodes, ("" if refined else "LLM 未能给出可用标签")


def analyze(project_path: str, use_llm: bool = True) -> dict:
    """构建业务叙事标注（确定性骨架 + 可选 LLM 补标注）。"""
    pp = os.path.abspath(project_path)
    db, nodes_raw, adj, module_of = _load_graph(pp)

    modules: List[str] = []
    if nodes_raw and module_of:
        for n in nodes_raw.values():
            if n.get("type") != "module":
                continue
            m = module_of(n, pp) or ""
            if not m or m.split("/")[-1] == "__init__":
                continue
            if m.startswith(".coderef"):
                continue
            modules.append(m)
    modules = sorted(dict.fromkeys(modules))

    anchors = _module_anchors(pp, modules)
    rows: List[dict] = []
    for m in modules:
        actor = _classify_actor(m) or UNLABELED_ACTOR
        stage = _classify_stage(m) or UNLABELED_STAGE
        rows.append({
            "module": m,
            "title": m.split("/")[-1].replace("_", " "),
            "actor": actor,
            "stage": stage,
            "value_node": _is_value_node(m),
            "anchor": anchors.get(m, ""),
            "evidence_level": "E2",
        })

    # 模块级调用边（跨泳道/跨阶段 = 交接点）
    mod_adj: Dict[str, List[str]] = {}
    by_mod = {r["module"]: r for r in rows}
    for src, tgts in (adj or {}).items():
        sn = nodes_raw.get(src) or {}
        sm = module_of(sn, pp) if module_of else ""
        if not sm or sm not in by_mod:
            continue
        for t in tgts:
            tn = nodes_raw.get(t) or {}
            tm = module_of(tn, pp) if module_of else ""
            if tm and tm != sm and tm in by_mod:
                mod_adj.setdefault(sm, [])
                if tm not in mod_adj[sm]:
                    mod_adj[sm].append(tm)

    edges: List[dict] = []
    handoff_count = 0
    for sm in sorted(mod_adj):
        for tm in sorted(mod_adj[sm]):
            a, b = by_mod[sm], by_mod[tm]
            cross_actor = a["actor"] != b["actor"]
            cross_stage = a["stage"] != b["stage"]
            handoff = cross_actor or cross_stage
            if handoff:
                handoff_count += 1
            edges.append({"from": sm, "to": tm, "handoff": handoff,
                          "cross_actor": cross_actor, "cross_stage": cross_stage})

    llm_note = ""
    if use_llm and rows:
        rows, llm_note = _llm_refine(rows)

    # 归类口径：Y 轴（actor/泳道）是主要叙事维度，X 轴（stage/阶段）允许缺席。
    # 「已归类」= actor 已判定；stage 未定的模块仍留在自己的泳道，
    # 只是落在「未归类阶段」列，不因此丢失角色信息。
    actor_labeled = sum(1 for r in rows if r["actor"] != UNLABELED_ACTOR)
    stage_labeled = sum(1 for r in rows if r["stage"] != UNLABELED_STAGE)
    unknowns: List[str] = [
        "actor/stage 由路径与调用边启发式推断，不是业务方确认的口径",
        "跨泳道边只代表代码调用，不代表真实业务交接（可能由编排层代劳）",
    ]
    if not db:
        unknowns.append("未找到知识图谱：只能按模块名归类，无法识别交接点")
    if rows and actor_labeled < len(rows):
        unknowns.append(
            f"{len(rows) - actor_labeled} 个模块未判定所属角色，已归入「未归类」泳道")
    if rows and stage_labeled < len(rows):
        unknowns.append(
            f"{len(rows) - stage_labeled} 个模块未判定流程阶段，已归入「未归类阶段」列")
    if llm_note:
        unknowns.append(llm_note)

    return {
        "version": __version__,
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "project": os.path.basename(pp.rstrip(os.sep)),
        "stages": [{"id": i, "name": n} for i, n in STAGES] + [
            {"id": UNLABELED_STAGE, "name": "未归类阶段"}],
        "actors": [{"id": i, "name": n} for i, n in ACTORS] + [
            {"id": UNLABELED_ACTOR, "name": "未归类"}],
        "nodes": rows,
        "edges": edges,
        "summary": {
            "modules": len(rows),
            "actor_labeled": actor_labeled,
            "stage_labeled": stage_labeled,
            "unlabeled_actor": len(rows) - actor_labeled,
            "unlabeled_stage": len(rows) - stage_labeled,
            "handoffs": handoff_count,
            "value_nodes": sum(1 for r in rows if r["value_node"]),
            "has_kg": bool(db),
        },
        "unknowns": unknowns,
    }


def write_result(project_path: str, data: dict) -> str:
    from core.scorecard import _atomic_write_json
    out = os.path.join(os.path.abspath(project_path), ".coderef",
                       "business_semantics.json")
    _atomic_write_json(out, data)
    return out
