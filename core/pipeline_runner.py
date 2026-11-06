# -*- coding: utf-8 -*-
"""
Pipeline Runner v2.0 — 三模式管线

  coderef_audit        → 11 审计工具 一次产出
  coderef_architecture  → 架构分析图谱
  coderef_docs          → 项目文档探查

All modes share: single AST scan + checkpoint resume.

上帝模块拆解（第 6 批）：本文件为瘦身主文件，保留 TaskCancelled / OM 自动同步 /
批处理去重（_burst_merge/_dedup_adjacent）/ run_single / docs / render_report /
Pipe 类；数据模型、kg_query 家族、白名单家族、报告渲染、checkpoint、管线阶段
分别迁至 core/pr_models.py、core/pr_kg.py、core/pr_whitelist.py、core/pr_render.py、
core/pr_ckpt.py、core/pr_stages.py，并经下方 import 全部 re-export，对外符号不变。

兼容层约定：下方 re-export 只为既有调用方（含仓外测试）保持 `core.pipeline_runner.X`
可达；**新代码请直接从 `pr_*` 子模块导入，不要往本文件的符号面里加新名字**。
"""

import os, json, time, threading
import hashlib, importlib  # 符号面兼容保留（见 __all__，拆前即在本模块命名空间）
from datetime import datetime
from loguru import logger
from dataclasses import dataclass, field
from typing import Dict, List, Optional
from enum import Enum
from core import tool_registry

# ══ 子模块 re-export（第 6 批上帝模块拆解）══
from core.pr_models import Tier, Finding, PipeResult
from core.pr_kg import (
    _kg_with_meta, _kg_nodes_result, _kgq_stats, _kgq_entity, _kgq_callers,
    _kgq_callees, _kgq_impact, _kgq_relations, _kgq_file_entities, _kgq_search,
    _kgq_call_graph, _KG_QUERY_HANDLERS, _kg_query_dispatch, kg_query,
)
from core.pr_whitelist import (
    _match_whitelist, _xval, _latest_report, _workflow, _findings_json_path,
    project_findings_json_path,
    _finding_from_dict, _finding_to_dict, _count_md, _detect_wiki_dir,
    docs_read, _select_tools, list_single_tools, _build_kg, _build_scope_text,
    _scan, core_rules_reset, core_rules_set, core_rules_get, whitelist_clear,
    whitelist_list, whitelist_add, _whitelist_path, _cdir, _phash,
    _compute_health, _tier_for,
)
from core.pr_render import (
    NOISE_RULES, _render_html, _collect_dimension_states, _match_noise_rule, _fmt,
)
from core.pr_ckpt import _ckpt, _save, _load, _load_whitelist
from core.pr_stages import _gov, _sca, _inn, _simp, _matu, _wiki, _denoise

# ══ 全量符号面声明：拆解后本模块对外符号与拆前一致（含 import 兼容层）══
__all__ = [
    # 标准库 / 三方 / 项目依赖（拆前即在本模块命名空间）
    "os", "json", "time", "threading", "hashlib", "importlib",
    "datetime", "logger", "dataclass", "field", "Enum", "Dict", "List", "Optional",
    "tool_registry",
    # 数据模型（pr_models）
    "Tier", "Finding", "PipeResult",
    # 知识图谱查询（pr_kg）
    "_kg_with_meta", "_kg_nodes_result", "_kgq_stats", "_kgq_entity",
    "_kgq_callers", "_kgq_callees", "_kgq_impact", "_kgq_relations",
    "_kgq_file_entities", "_kgq_search", "_kgq_call_graph", "_KG_QUERY_HANDLERS",
    "_kg_query_dispatch", "kg_query",
    # 白名单家族 + core_rules（pr_whitelist）
    "_match_whitelist", "_xval", "_latest_report", "_workflow",
    "_findings_json_path", "_finding_from_dict", "_finding_to_dict", "_count_md",
    "_detect_wiki_dir", "docs_read", "_select_tools", "list_single_tools",
    "_build_kg", "_build_scope_text", "_scan", "core_rules_reset",
    "core_rules_set", "core_rules_get", "whitelist_clear", "whitelist_list",
    "whitelist_add", "_whitelist_path", "_cdir", "_phash", "_compute_health",
    "_tier_for",
    # 报告渲染（pr_render）
    "NOISE_RULES", "_render_html", "_collect_dimension_states",
    "_match_noise_rule", "_fmt",
    # checkpoint（pr_ckpt）
    "_ckpt", "_save", "_load", "_load_whitelist",
    # 管线阶段（pr_stages）
    "_gov", "_sca", "_inn", "_simp", "_matu", "_wiki", "_denoise",
    # 主文件保留
    "TaskCancelled", "_auto_sync_om_on_gov", "_OM_GOV_SYNC_MIN_INTERVAL",
    "_OM_GOV_SYNC_LAST", "_OM_GOV_SYNC_LOCK", "_burst_merge", "_dedup_adjacent",
    "run_single", "docs", "render_report", "_dump_findings_json",
    "_load_findings_json", "Pipe",
]


class TaskCancelled(Exception):
    """ 协作式取消专用异常（定义于被依赖方，供 MCP 层与各管线共享）。

    后台任务被 coderef_task_cancel 请求取消后，progress 回调抛此异常；各管线
    （audit/docs/wiki）的 except Exception 必须 re-raise 本异常，避免取消信号被
    吞掉后 daemon 线程继续跑到底（CodeRabbit major）。
    """
    pass


def _auto_sync_om_on_gov(project_path: str) -> None:
    """方向 B：治理流程收尾自动增量同步操作记忆（后台线程，不阻塞主流程）。

    让"记忆始终新鲜"（解决存），供后续 recover/query 取到最新约定/工具定位。
    best-effort：尊重 OMEM_AUTO_SYNC_ON_GOV 开关，失败仅记日志，绝不破坏主流程。
    用 daemon 线程延迟执行，Tool 返回不受同步耗时/跨进程锁重试影响；同一项目
    _OM_GOV_SYNC_MIN_INTERVAL 秒内只排一次，避免高频 coderef_scan 反复触发重扫。
    """
    try:
        from config import settings
        if not getattr(settings, "OMEM_AUTO_SYNC_ON_GOV", True):
            return
        now = time.time()
        with _OM_GOV_SYNC_LOCK:
            last = _OM_GOV_SYNC_LAST.get(project_path, 0.0)
            if now - last < _OM_GOV_SYNC_MIN_INTERVAL:
                return
            _OM_GOV_SYNC_LAST[project_path] = now
    except Exception:
        return

    def _run():
        try:
            from core.operation_memory import operation_memory
            operation_memory.sync(project_path, mode="incr", with_llm=False)
        except Exception as e:
            logger.warning(f"[gov] 操作记忆自动收尾同步跳过: {e}")

    threading.Thread(target=_run, daemon=True, name="om-gov-sync").start()


# 治理收尾自动同步的最小间隔（秒）与去重状态；线程安全
_OM_GOV_SYNC_MIN_INTERVAL = 30.0
_OM_GOV_SYNC_LAST: Dict[str, float] = {}
_OM_GOV_SYNC_LOCK = threading.Lock()


def _burst_merge(findings: List[Finding]) -> List[Finding]:
    """同 tool + category 超过阈值 → 保留 1 条 + 统计摘要

    聚合时保留组内**最高** tier/severity（而非无条件降级为 LOW），
    并把真实数量与全部位置写入 count/locations，避免严重度失真、
    位置丢失、数量低估（对应证据审计缺陷 1/2/3）。

    分组键在 (tool, category) 之上叠加 title 中的 [risk_id]（如 agent 的
    [AGENT-SEC-30]、gov 的 [GOV-xxx]），使不同风险类型独立成条，避免
    高价值缺陷被同 category 的 flood 合并吞掉顶层位置（目标项目 agent
    的 Go 风险全部归入 tool_misuse，若不细分会被 326 条 Python 噪声
    合并成 1 条，顶层 file/line 被 clawbot 占据）。

    分组键再叠加 severity：同一规则的 high/low 必须分开聚合。
    否则"7 条 high + 63 条已降级 low"会被合并成一条 count=70、
    severity=high 的条目——单 severity 字段无法反映组内分层，
    加权统计（按 tier 分组求和 count）会把 63 条 low 全部计入
    HIGH，修复效果被聚合层掩盖（自审实测：SEC-08 检测器已把
    63 条降级 low，聚合后报告仍显示 70 条 high）。
    """
    def _risk_key(f: Finding) -> str:
        t = (f.title or "").lstrip()
        if t.startswith("[") and "]" in t:
            rid = t[1:t.index("]")]
            # dead_code 各子类型细分到 TITLE 级（含函数/导入名），使每个死函数独立
            # finding，避免同类别超过 BURST_THRESHOLD 被爆发合并成 1 条（r3 P1-A：
            # 12 个死函数被合并为 1 条 count=12，ARC-04/05 无独立 finding）。
            # 分组键再叠加 file:line：title 非唯一标识，不同文件的同名死函数
            # （如两个文件都有 never_used()）与 generic [DEAD-COMMENT] 标题
            # 仍会被误合并（CodeRabbit 复审 4541664 minor）
            if rid.startswith("DEAD-"):
                return f"{t}|{f.file_path}:{f.line}"
            return rid
        return ""

    by_key = {}
    for f in findings:
        k = (f.tool, f.category, _risk_key(f),
             (f.severity or "medium").lower())
        by_key.setdefault(k, []).append(f)

    result = []
    for k, group in by_key.items():
        if len(group) <= Pipe.BURST_THRESHOLD:
            result.extend(group)
        else:
            # 保留第 1 条 + 摘要
            first = group[0]
            # 缺陷 1：保留组内最高 tier（HIGH > MEDIUM > LOW），不无条件降级
            _tier_order = {Tier.HIGH: 0, Tier.MEDIUM: 1, Tier.LOW: 2}
            max_tier = min(group, key=lambda f: _tier_order.get(f.tier, 9)).tier
            # 保留组内最高 severity 文本（critical > high > medium > low）
            _sev_order = {"critical": 0, "blocker": 0, "high": 1,
                          "medium": 2, "low": 3}
            max_sev = min(group, key=lambda f: _sev_order.get(
                (f.severity or "medium").lower(), 9)).severity
            first.tier = max_tier
            first.severity = max_sev
            # 缺陷 2/3：记录真实数量与全部位置
            # 用组内 count 加权和而非 len(group)：_dedup_adjacent 已把邻行
            # 违规合并进单条的 count，len 会把这部分重新丢掉。
            first.count = sum(getattr(f, "count", 1) for f in group)
            # locations 与 count 同源：逐成员并入其全部原始位置——
            # _dedup_adjacent 合并过的成员，其 locations 已含被并邻行的精确行号；
            # 未合并成员（locations 为空）补自身 file:line。不去重：
            # 1 次命中对应 1 个位置，len(locations) == count（file_path 为空的除外）。
            locs: List[str] = []
            for f in group:
                if not f.file_path:
                    continue
                sub = getattr(f, "locations", None) or []
                if sub:
                    locs.extend(sub)
                else:
                    locs.append(f"{f.file_path}:{f.line}")
            first.locations = locs
            first.title = f"[共 {first.count} 条] {first.title}"
            loc_preview = ", ".join(locs[:20])
            if len(locs) > 20:
                loc_preview += f" 等 {len(locs)} 处"
            # 缺陷 5：保留原始 detail（含 gov 附加的"命中代码"，供符号级证据核验），
            # 在其后追加爆发式统计，避免覆盖 line_content 导致 L3 符号提取失效。
            # detail 数字与 count 同源（此前用 len(group) 导致 count=70/detail=60 口径分裂）。
            base_detail = first.detail or ""
            first.detail = (f"{base_detail}（此项为爆发式重复，共 {first.count} 次，涉及 "
                            f"{len(set(f.file_path for f in group))} 个文件，"
                            f"组内最高严重度: {max_sev}。全部位置见 locations 字段"
                            f"（{len(locs)} 处，未去重），示例: {loc_preview}）")
            result.append(first)
    return result

def _dedup_adjacent(findings: List[Finding]) -> List[Finding]:
    """同文件 + 同规则 + 邻行 → 合并为 1 条"""
    if not findings:
        return findings
    # 提取 title 中的 [risk_id]（与 _burst_merge 分组键一致，避免不同风险类型被误合并）
    def _rk(f: Finding) -> str:
        t = (f.title or "").lstrip()
        if t.startswith("[") and "]" in t:
            rid = t[1:t.index("]")]
            # 与 _burst_merge._risk_key 保持一致：dead_code 子类型细分到 TITLE 级，
            # 并叠加 file:line 保证同名死函数/相邻注释块不被误合并
            if rid.startswith("DEAD-"):
                return f"{t}|{f.file_path}:{f.line}"
            return rid
        return ""
    # 排序键叠加 risk_id：同文件同规则同风险类型才相邻
    findings.sort(key=lambda f: (
        f.file_path, f.tool, f.category, _rk(f), f.line
    ))
    result = []
    for f in findings:
        if result:
            prev = result[-1]
            if (f.file_path == prev.file_path
                    and f.tool == prev.tool
                    and f.category == prev.category
                    and _rk(f) == _rk(prev)
                    and f.line - prev.line <= Pipe.ADJACENT_LINE_WINDOW):
                # 合并相邻行：记录真实行号区间，标题保持明确，不再退化为"(等多行)"
                if not prev.line_start:
                    prev.line_start = prev.line
                prev.line_end = max(prev.line_end, f.line)
                prev.line = prev.line_start
                # 累加 count：合并掉的每条违规不能在计数层静默丢失
                #（内容经 detail 拼接保留，数量也必须保留）。
                prev.count = getattr(prev, "count", 1) + 1
                # 记录被合并成员的精确行号：count 与 locations 必须同源，
                # 否则 _burst_merge 聚合后 count > len(locations)（口径分裂）。
                if not prev.locations:
                    prev.locations = [f"{prev.file_path}:{prev.line_start}"]
                prev.locations.append(f"{f.file_path}:{f.line}")
                if f.detail and f.detail not in prev.detail:
                    prev.detail += " | " + f.detail
                continue
        result.append(f)
    return result


def run_single(pipe: "Pipe", project_path: str, tool: str) -> PipeResult:
    """单独运行某一个审计工具 —— AI 写代码时的实时安全带。

    相比 audit 全量管线，只跑一个工具：不构建知识图谱、不生成 dashboard，
    只对该维度做 AST 扫描 + 检测 + 降噪，快速返回该维度的 findings，
    供 AI 在写完一个模块后即时自查（客观第二意见）。
    """
    tool = (tool or "").lower()
    if tool not in tool_registry.SINGLE_TOOLS:
        raise ValueError(
            f"未知工具 '{tool}'，支持: {', '.join(sorted(tool_registry.SINGLE_TOOLS))}")
    pipe._t0 = time.time()
    r = PipeResult(project_path=project_path)
    try:
        tf, tl, analysis = _scan(project_path)
        r.total_files, r.total_lines = tf, tl
        label, method = tool_registry.SINGLE_TOOLS[tool]
        fn = getattr(pipe, method)
        # 全新 done 集合，不读 checkpoint，保证单工具独立、可复现
        fn(project_path, r, set())
        if r.findings:
            # 缺陷 4：单维度扫描也走交叉验证（_xval），保持与全量 audit 管线一致。
            # 单维度下所有 finding 同属一个 tool，_xval 的"多工具命中同一位置→HIGH"
            # 不会误升级（len(tools)>=2 不成立），因此无副作用；若未来单维度
            # 支持多工具则自动生效。交叉验证的局限在 MCP 描述中如实声明。
            _xval(r)
            _denoise(r)
        r.elapsed = round(time.time() - pipe._t0, 1)
    except Exception as e:
        r.errors.append(str(e))
    r.health_score = _compute_health(r)
    r.elapsed = round(time.time() - pipe._t0, 1)
    _auto_sync_om_on_gov(project_path)
    return r

def docs(project_path: str, output_dir: str = None,
         resume: bool = False,
         wiki_style: str = "comprehensive",
         include_subprojects: bool = True,
         enable_agent_pointer: bool = False,
         cross_verify: bool = True,
         cross_entry_spec: str = "class:pipeline_runner:Pipe",
         progress_cb=None) -> PipeResult:
    """文档探查管线：Wiki

    wiki_style: Wiki 风格 (comprehensive / reference / tutorial / plain)
    include_subprojects: 是否同时为子项目生成独立 Wiki
    enable_agent_pointer: 是否在项目根维护 AGENTS.md 的 CodeRef Wiki 指针区块（R7）
    cross_verify: 是否对模块描述做静态交叉验证（确证徽章）
    cross_entry_spec: 交叉验证的入口（入口调用闭包为确证依据）
    """
    t0 = time.time()
    r = PipeResult(project_path=project_path)
    d = _load(project_path) if resume else set()

    # 进度包装：非取消的 progress_cb 异常仅记录不阻断 docs（与 audit._prog 一致，
    # CodeRabbit major）；TaskCancelled 必须透传给上层以协作式收尾。
    def _cb(stage, done, total, detail=None):
        if progress_cb:
            try:
                progress_cb(stage, done, total, detail)
            except TaskCancelled:
                raise
            except Exception:
                pass

    try:
        tf, tl, analysis = _scan(project_path)
        r.total_files, r.total_lines = tf, tl
        _cb("扫描", 1, 3, f"{tf} 文件 · {tl} 行")

        # 构建知识图谱
        _build_kg(project_path, analysis)
        _cb("知识图谱", 2, 3, "构建调用图")

        _wiki(project_path, r, d, output_dir,
                   wiki_style=wiki_style,
                   include_subprojects=include_subprojects,
                   enable_agent_pointer=enable_agent_pointer,
                   cross_verify=cross_verify,
                   cross_entry_spec=cross_entry_spec,
                   progress_cb=progress_cb)

        r.report = _fmt(r, "文档探查报告")
        # 报告目录按项目隔离（U-51）：docs 也渲染 index.html，落共享目录会覆盖
        # 其他项目的评估台入口。
        out = output_dir or _default_report_root(project_path)
        os.makedirs(out, exist_ok=True)

        # 渲染 HTML 报告目录（Wiki 为主；若图谱/审计产物存在则一并纳入）
        html_out = os.path.join(out, "html")
        _render_html(project_path, r, kg_stats=None,
                     output_dir=html_out)
    except TaskCancelled:
        raise
    except Exception as e:
        r.errors.append(str(e))

    r.elapsed = round(time.time() - t0, 1)
    return r

def _default_report_root(project_path: str) -> str:
    """报告产物根目录（项目侧隔离：<project>/.coderef/report，见 U-51）。

    延迟导入 report_renderer，避免与渲染层形成模块级循环依赖。
    """
    from core.report_renderer import default_report_root
    return default_report_root(project_path)


def render_report(project_path: str,
                  output_dir: Optional[str] = None) -> tuple:
    """重渲染既有产物为 HTML 报告目录（coderef_report 的核心调度）。

    只聚合已落盘的产物（知识图谱 + Wiki），不重跑代码扫描，适合"已有产物只要 HTML"。
    返回 (PipeResult, has_artifacts)；has_artifacts=False 表示无既有图谱产物，
    调用方应回退为跑一次全量审计。
    """
    r = PipeResult(project_path=project_path)
    try:
        from core.code_knowledge_graph import load_knowledge_graph
        kg = load_knowledge_graph(project_path)
        kg_stats = kg.get_stats() if kg is not None else None
        if kg is not None:
            kg.close()
        if kg_stats is None:
            r.errors.append("未检测到既有知识图谱产物，已回退为一次全量审计")
            return r, False
        # 补全 has_artifacts 判定：kg 与 审计 findings 均存在才走重渲染。
        # 若只有图谱没有审计 findings，产物残缺，audit.html 与 index 审计卡片
        # 会缺失真实数据，回退到一次全量审计更完整（社区反馈的"全 0/暂无"bug 根源）。
        # findings 可能落在项目侧 `.coderef/`（权威副本）或调用方输出目录，
        # 只要任一处存在即可重渲染（U-49：外部项目曾因只找输出目录而永久失败）。
        audit_candidates = [
            _findings_json_path(project_path, output_dir),
            project_findings_json_path(project_path),
        ]
        if not any(os.path.isfile(p) for p in dict.fromkeys(audit_candidates)):
            r.errors.append("未检测到审计 findings 产物（audit_findings.json），"
                            "已回退为一次全量审计以产出完整报告")
            return r, False
        # 检测各维度执行状态，供 HTML 透明化展示（避免"审计未执行"被当成"暂无发现"）
        r.dimension_states = _collect_dimension_states(project_path, kg_stats, output_dir)
        # 恢复上次审计的 findings 与统计，避免"重渲染既有产物"时 index 审计卡片
        # 与 audit.html 明细全部为 0 / 空（社区反馈的聚合 HTML 全 0 bug）。
        _load_findings_json(project_path, r, output_dir)
        # 若已有 Wiki 产物，挂到 wiki_result 供 HTML 渲染聚合 Wiki 页
        wiki_dir = _detect_wiki_dir(project_path)
        if wiki_dir:
            class _WikiRef:
                output_dir = wiki_dir
            r.wiki_result = _WikiRef()
        hr = _render_html(project_path, r, kg_stats=kg_stats,
                               output_dir=output_dir)
        if not hr.get("ok"):
            r.errors.append(hr.get("error", "渲染失败"))
        return r, True
    except Exception as e:
        r.errors.append(f"render_report: {e}")
        return r, False

def _dump_findings_json(r: PipeResult, out: str) -> None:
    """把 audit 的 findings 与统计序列化落盘，供 coderef_report 重渲染复用。

    落两处：项目侧 `<project>/.coderef/`（权威副本，与 scorecard 同目录）与调用方
    `out`（兼容既有共享输出目录的消费方）。缺项目侧副本时，外部项目的重渲染会
    因找不到产物而静默失败（U-49 根因之一）。
    """
    try:
        data = {
            "version": 1,
            "scan_ts": r.scan_ts or "",
            "kg_built_at": r.kg_built_at or "",
            "total_files": r.total_files,
            "total_lines": r.total_lines,
            "scope_text": r.scope_text or "",
            "findings": [_finding_to_dict(f) for f in r.findings],
        }
        targets = [project_findings_json_path(r.project_path)]
        if out:
            targets.append(_findings_json_path(r.project_path, out))
        for fp in dict.fromkeys(targets):
            os.makedirs(os.path.dirname(fp), exist_ok=True)
            tmp = fp + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            os.replace(tmp, fp)
    except Exception as e:
        r.errors.append(f"dump_findings_json: {e}")

def _load_findings_json(project_path: str, r: PipeResult,
                        out: Optional[str] = None) -> bool:
    """从落盘的 findings JSON 恢复统计与 findings 到 r；无可用 JSON 返回 False。"""
    candidates = [
        project_findings_json_path(project_path),
        _findings_json_path(project_path, out),
        _findings_json_path(project_path),
        os.path.join(project_path, ".coderef",
                     f"audit_findings_{Pipe._phash(project_path)}.json"),
        os.path.join(project_path, "coderef-report",
                     f"audit_findings_{Pipe._phash(project_path)}.json"),
        # 向后兼容：旧版全局单文件（无项目标识）
        os.path.join(project_path, "coderef-report", "audit_findings.json"),
    ]
    for fp in candidates:
        if not os.path.isfile(fp):
            continue
        try:
            with open(fp, "r", encoding="utf-8") as f:
                data = json.load(f)
            r.total_files = int(data.get("total_files", 0) or 0)
            r.total_lines = int(data.get("total_lines", 0) or 0)
            r.scope_text = data.get("scope_text", "") or ""
            r.scan_ts = data.get("scan_ts", "") or ""
            r.kg_built_at = data.get("kg_built_at", "") or ""
            r.findings = [_finding_from_dict(d) for d in data.get("findings", [])]
            return True
        except Exception as e:
            logger.warning(f"加载管线缓存候选失败，继续尝试下一个: {e}")
            continue
    return False


class Pipe:

    def __init__(self):
        self._t0 = 0.0

    @staticmethod
    def _tier_for(severity):
        return _tier_for(severity)

    @staticmethod
    def _compute_health(r):
        return _compute_health(r)

    @staticmethod
    def _phash(p):
        return _phash(p)

    @staticmethod
    def _cdir():
        return _cdir()

    # ─── checkpoint ───

    def _ckpt(self, p):
        return _ckpt(p)

    def _save(self, p, done):
        return _save(p, done)

    def _load(self, p):
        return _load(p)

    # ─── AI 白名单（编程 AI 补充意见持久化）───

    @staticmethod
    def _whitelist_path(project_path):
        return _whitelist_path(project_path)

    @staticmethod
    def whitelist_add(project_path, entries):
        return whitelist_add(project_path, entries)

    @staticmethod
    def whitelist_list(project_path):
        return whitelist_list(project_path)

    @staticmethod
    def whitelist_clear(project_path):
        return whitelist_clear(project_path)

    def _load_whitelist(self, project_path):
        return _load_whitelist(project_path)

    # ─── 核心模块规则管理（AI 可追加入口文件名/核心模块名/阈值）───

    @staticmethod
    def core_rules_get(project_path):
        return core_rules_get(project_path)

    @staticmethod
    def core_rules_set(project_path, rules):
        return core_rules_set(project_path, rules)

    @staticmethod
    def core_rules_reset(project_path):
        return core_rules_reset(project_path)

    # ─── shared AST ───

    def _scan(self, p, file_cb=None):
        return _scan(p, file_cb)

    @staticmethod
    def _build_scope_text(project_path, total_files):
        return _build_scope_text(project_path, total_files)

    # ─── knowledge graph ───

    def _build_kg(self, project_path, analysis):
        return _build_kg(project_path, analysis)

    # ─── HTML 报告渲染（功能①：把审计/图谱/Wiki 聚合成前端可读的 HTML 报告目录）───

    def _render_html(self, project_path, r, kg_stats=None, output_dir=None):
        return _render_html(project_path, r, kg_stats, output_dir)

    # ═══════════════════════════════════
    # 三大管线
    # ═══════════════════════════════════

    def _resolve_audit_strategy(self, project_path, r, strategy):
        """审计策略判定：None 时调用 ReviewAdvisor 自动判定，否则记录显式标记。"""
        if strategy is None:
            try:
                from core.review_strategy import review_advisor
                advise = review_advisor.advise(project_path)
                r.review_strategy = advise
                return advise.get("strategy", "full")
            except Exception as e:
                r.errors.append(f"review_strategy: {e}")
                return "full"
        # 显式指定策略：跳过自动判定（避免重复 advise 开销），记录显式标记。
        # 保留与 advise() 一致的结构骨架（changes/impact/kg/dimensions_focus），
        # 避免下游报告/功能审查读到空键时渲染出"变更 0、波及 0、图谱 None"的误导结论。
        r.review_strategy = {
            "strategy": strategy,
            "explicit": True,
            "reason": "显式指定审计策略，跳过自动判定",
            "changes": {"has_prev_snapshot": False, "changed": [],
                        "added": [], "deleted": [], "total": 0},
            "impact": {"count": 0, "depth": 0, "nodes": []},
            "kg": {"exists": False, "built_at": "", "stale": True},
            "dimensions_focus": [],
        }
        return strategy

    def _try_reuse_no_change(self, effective_strategy, project_path, r, out, _prog):
        """no_change 策略复用既有结论；无可复用时降级为 full。
        返回 (可提前返回的 PipeResult 或 None, 生效策略)。"""
        if effective_strategy != "no_change":
            return None, effective_strategy
        if self._reuse_no_change(project_path, r, out, _prog):
            r.elapsed = round(time.time() - self._t0, 1)
            return r, effective_strategy
        # 无可复用结论 → 降级为全量，记录提示
        r.errors.append(
            "no_change 策略下未找到可复用的既有结论，已降级为 full 重新审计")
        r.audit_strategy = "full"
        return None, "full"

    def _snapshot_files(self, analysis) -> dict:
        """本次实际扫描文件的 mtime+size 快照，作为"审计覆盖了哪些文件"的证据。"""
        try:
            snapshot = {}
            for cf in getattr(analysis, "files", []) or []:
                fp = getattr(cf, "file_path", "")
                if not fp:
                    continue
                try:
                    st = os.stat(fp)
                    snapshot[fp] = {"mtime": st.st_mtime, "size": st.st_size}
                except Exception as e:
                    logger.warning(f"读取文件状态用于快照失败，跳过 {fp}: {e}")
            return snapshot
        except Exception:
            return {}

    def _execute_audit_tools(self, effective_strategy, tools, total_stages,
                             project_path, r, d, _prog):
        """顺序执行工具集并回传阶段进度；相对全量裁剪时记录日志。"""
        for i, (name, fn) in enumerate(tools, start=2):
            fn(project_path, r, d)
            _prog(name, i, total_stages)
        if len(tools) < len(Pipe.ALL_AUDIT_TOOLS):
            logger.info(
                f"[audit] 策略={effective_strategy}，裁剪 "
                f"{len(Pipe.ALL_AUDIT_TOOLS) - len(tools)} 个重型全量工具，"
                f"实际运行 {len(tools)} 个")

    def _sync_memory_layer(self, project_path, r):
        """图谱增量 patch：策略为增量且图谱存在时，用 memory_layer 增量同步更新图谱，
        避免每次都全量重建（图谱增量复用，符合功能②B"图谱增量 patch"）。"""
        try:
            from core.memory_layer import memory_layer
            strata = getattr(r, "audit_strategy", "full") or "full"
            memory_layer.sync(project_path,
                              mode="incr" if strata == "incr" else "full")
        except Exception as e:
            r.errors.append(f"memory_sync: {e}")

    def _run_functional_review(self, project_path, r, kg_stats):
        """功能②：LLM 功能审查增强（创新传播/结构复杂度/回归一致性等语义维度）。"""
        try:
            from core.functional_review import functional_reviewer
            r.functional_review = functional_reviewer.review(
                project_path, r.review_strategy or {},
                pipe_result=r, kg_stats=kg_stats)
        except Exception as e:
            r.errors.append(f"functional_review: {e}")

    def _write_audit_report(self, project_path, r, out):
        """报告落盘：Markdown 报告 + 结构化 findings JSON。"""
        os.makedirs(out, exist_ok=True)
        fn = (f"coderef_audit_{self._phash(project_path)}_"
              f"{datetime.now().strftime('%Y%m%d_%H%M%S')}.md")
        r.report_path = os.path.join(out, fn)
        with open(r.report_path, "w", encoding="utf-8") as f:
            f.write(r.report)
        # 结构化 findings 落盘：供 coderef_report 重渲染复用（避免聚合 HTML 审计全 0）
        self._dump_findings_json(r, out)

    def _build_dashboard(self, project_path, r, kg_stats):
        """生成健康仪表盘。"""
        try:
            from core.health_dashboard import HealthDashboard
            dashboard = HealthDashboard(project_path)
            dashboard_path = dashboard.build(r, kg_stats)
            r.dashboard_path = dashboard_path
        except Exception as e:
            r.errors.append(f"dashboard: {e}")

    def audit(self, project_path: str, output_dir: str = None,
              resume: bool = False, progress_cb=None,
              strategy: Optional[str] = None,
              with_dashboard: bool = False) -> PipeResult:
        """安全审计管线：11 工具（按策略裁剪）

        progress_cb: 可选回调 progress_cb(stage:str, done:int, total:int)
        用于后台执行时向调用方回传阶段进度。

        strategy: 可选，显式指定审计策略（"full"/"incr"/"no_change"）。
        为 None 时自动调用 ReviewAdvisor 判定（变更信号 + 影响闭包），
        并据此裁剪工具集 —— 动态兜底：首次/无基线→全量，增量→裁剪重型工具。

        with_dashboard: 是否额外产出独立健康仪表盘 HTML。默认关闭——
        健康读数已并入「项目评估台」的记分卡，重复产出会让非技术读者面对两份口径。
        """
        self._t0 = time.time()
        r = PipeResult(project_path=project_path)
        d = self._load(project_path) if resume else set()
        out = output_dir or _default_report_root(project_path)

        def _prog(stage, done, total, detail=None):
            if progress_cb:
                try: progress_cb(stage, done, total, detail)
                except TaskCancelled: raise
                except Exception: pass

        try:
            # 审计证据：记录本次扫描开始时间，供调用方区分"本次结果"与历史缓存
            r.scan_ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

            # 审计策略判定（增量 vs 全量）—— 动态兜底第一步：在跑任何阶段前算出策略，
            # 据此裁剪工具集并确定统一进度分母，避免"算完不用 / 进度分母漂移"。
            effective_strategy = self._resolve_audit_strategy(project_path, r, strategy)
            r.audit_strategy = effective_strategy

            # 策略为 no_change 时：复用既有结论，不重扫（行为与描述一致）
            early, effective_strategy = self._try_reuse_no_change(
                effective_strategy, project_path, r, out, _prog)
            if early is not None:
                return early

            # 按策略选择工具子集（动态兜底核心：裁剪重型全量工具）
            tools = [
                (n, getattr(self, m)) for n, m in self._select_tools(effective_strategy)
            ]
            # 统一进度分母：扫描 + 构建知识图谱 + 各工具 + 生成报告
            total_stages = 3 + len(tools)

            # 扫描阶段桥接文件级进度：长阶段能实时看到"已扫描文件/总文件"
            def _scan_file_prog(done, total):
                _prog("扫描代码", 0, total_stages, detail=f"已扫描 {done}/{total} 个文件")
            tf, tl, analysis = self._scan(project_path, file_cb=_scan_file_prog)
            r.total_files, r.total_lines = tf, tl
            r.scope_text = self._build_scope_text(project_path, tf)
            r.file_snapshot = self._snapshot_files(analysis)
            _prog("扫描代码", 0, total_stages)

            # 构建知识图谱（持久化项目记忆）
            kg_stats = self._build_kg(project_path, analysis)
            r.kg_built_at = str(kg_stats.get("built_at", "")) if isinstance(kg_stats, dict) else ""
            _prog("构建知识图谱", 1, total_stages)

            # 执行工具
            self._execute_audit_tools(effective_strategy, tools, total_stages,
                                      project_path, r, d, _prog)

            self._xval(r)
            self._denoise(r)

            # 图谱增量 patch
            self._sync_memory_layer(project_path, r)

            # 功能②：LLM 功能审查增强
            self._run_functional_review(project_path, r, kg_stats)

            r.report = self._fmt(r, "审计报告")
            _prog("生成报告", total_stages - 1, total_stages)

            self._write_audit_report(project_path, r, out)
            # 健康仪表盘默认不再自动产出：评估台记分卡是唯一健康读数来源。
            if with_dashboard:
                self._build_dashboard(project_path, r, kg_stats)

            # 渲染 HTML 报告目录（审计 + 图谱；若已有 wiki 产物则一并纳入）
            self._render_html(project_path, r, kg_stats=kg_stats,
                              output_dir=os.path.join(out, "html"))

            if os.path.exists(self._ckpt(project_path)):
                os.remove(self._ckpt(project_path))
        except TaskCancelled:
            # CodeRabbit major：TaskCancelled 继承 Exception，须在宽 handler 前显式
            # 透传到任务 owner，使取消状态可被 _bg/_tsk 识别，而非退化为普通 error。
            raise
        except Exception as e:
            r.errors.append(str(e))

        r.health_score = self._compute_health(r)
        r.elapsed = round(time.time() - self._t0, 1)
        _auto_sync_om_on_gov(project_path)
        return r

    # ─── 单工具运行（供 MCP: coderef_scan_* 调用）───
    # 工具注册表与选择策略已抽到 tool_registry，此处仅保留 Pipe.* 别名，
    # 保持对外接口（mcp_server / 测试）不变。

    SINGLE_TOOLS = tool_registry.SINGLE_TOOLS
    ALL_AUDIT_TOOLS = tool_registry.ALL_AUDIT_TOOLS
    INCR_SKIP_TOOLS = tool_registry.INCR_SKIP_TOOLS

    @staticmethod
    def list_single_tools():
        return list_single_tools()

    @staticmethod
    def _select_tools(strategy):
        return _select_tools(strategy)

    def run_single(self, project_path, tool):
        return run_single(self, project_path, tool)

    def architecture(self, project_path: str, output_dir: str = None,
                     resume: bool = False, insight_llm: bool = False) -> PipeResult:
        """架构图管线：GitNexus + Workflow"""
        self._t0 = time.time()
        r = PipeResult(project_path=project_path)
        # 报告默认落 project_path/coderef-report/（对齐 audit/docs），不再落 MCP 进程 cwd：
        # 避免真实多项目/跨仓协作时把 coderef_arch_*.md 写进对方主仓（r6 红线段落）
        out = output_dir or os.path.join(project_path, "coderef-report")

        try:
            tf, tl, analysis = self._scan(project_path)
            r.total_files, r.total_lines = tf, tl

            # 构建知识图谱（同步执行，返回 stats 含 db_path）
            kg_stats = self._build_kg(project_path, analysis)

            self._workflow(project_path, r)

            r.report = self._fmt(r, "架构分析报告")
            #  架构洞察（管线/真身/重复，静态为主，LLM 可选）：插入到报告尾部 HTML 路径之前，
            # 让 coderef_architecture 不再只是"790B 壳"，自动产出人话结构化结论。
            # 复用刚构建的图谱 db_path 直喂 insight，避免 insight 内二次 ensure_kg 探测/重建竞态
            # （r8 实测：MCP 长驻进程下二次探测时序不稳会拿不到节点 → 洞察空 → 报告壳）。
            try:
                from core.arch_insight import insight_markdown
                kg_db = (kg_stats or {}).get("db_path")
                insight = insight_markdown(project_path, db_path=kg_db, use_llm=insight_llm)
                if insight:
                    tail = f"---\n{r.report_path or ''}"
                    if r.report.endswith(tail):
                        r.report = r.report[:-len(tail)] + insight + "\n" + tail
                    else:
                        r.report += "\n" + insight
                else:
                    r.errors.append(f"insight: 洞察为空（图谱 {kg_db or '未知'} 不可用，未产出人话结论）")
            except Exception as e:
                r.errors.append(f"insight: {e}")
                r.report += f"\n\n## 🧭 架构洞察（）\n\n> 洞察生成失败：{e}\n"
            # 追加生成可视化画布：coderef_architecture 承诺"交互式模块画布(HTML)"，
            # 不再让 arch_canvas 成为孤儿入口——同一次调用一并产出，并把画布路径
            # 写进 md 报告尾部，编程 AI 读完就知道架构图在哪儿（外部反馈：AI 连续两次
            # "没看到架构图"，根因即 architecture 产物与 arch_canvas 分离）。
            try:
                from core.canvas_generator import ArchCanvas
                canvas = ArchCanvas().generate(project_path=project_path, output_dir=None)
                if canvas:
                    r.arch_canvas = canvas
                    r.report += (
                        f"\n\n## 🖼 架构画布\n\n"
                        f"> 可视化架构画布（带角色归属，三层布局，可拖拽）：\n\n"
                        f"- 文件：`{canvas}`\n"
                        f"- 浏览器本地打开即可交互编辑、导出目标架构 JSON。\n")
            except Exception as e:
                r.errors.append(f"canvas: {e}")
            os.makedirs(out, exist_ok=True)
            fn = f"coderef_arch_{datetime.now().strftime('%Y%m%d_%H%M%S')}.md"
            r.report_path = os.path.join(out, fn)
            with open(r.report_path, "w", encoding="utf-8") as f:
                f.write(r.report)
        except Exception as e:
            r.errors.append(str(e))

        r.elapsed = round(time.time() - self._t0, 1)
        return r

    def docs(self, project_path, output_dir=None, resume=False, wiki_style="comprehensive",
             include_subprojects=True, enable_agent_pointer=False, cross_verify=True,
             cross_entry_spec="class:pipeline_runner:Pipe", progress_cb=None):
        return docs(project_path, output_dir, resume, wiki_style, include_subprojects, enable_agent_pointer, cross_verify, cross_entry_spec, progress_cb)

    def docs_read(self, project_path, doc=None, output_dir=None, max_chars=20000):
        return docs_read(project_path, doc, output_dir, max_chars)

    @staticmethod
    def _detect_wiki_dir(project_path):
        return _detect_wiki_dir(project_path)

    @staticmethod
    def _count_md(wiki_dir):
        return _count_md(wiki_dir)

    def _collect_dimension_states(self, project_path, kg_stats, output_dir=None):
        return _collect_dimension_states(project_path, kg_stats, output_dir)

    def render_report(self, project_path, output_dir=None):
        return render_report(project_path, output_dir)

    # ─── 审计 findings 结构化落盘 / 恢复 ─────────────────────────────
    # 背景：coderef_report 走 render_report 重渲染时，若用空 PipeResult 聚合，
    #   index.html 的审计卡片与 audit.html 明细会全部为 0 / "暂无发现"（社区反馈）。
    # 方案：audit() 落盘 markdown 的同时，把 findings 与统计序列化为 JSON；
    #   render_report 优先读取该 JSON 恢复到 PipeResult，再渲染 HTML，
    #   保证"重渲染既有产物"时审计内容完整，而不依赖对 markdown 的脆弱解析。

    @staticmethod
    def _finding_to_dict(f):
        return _finding_to_dict(f)

    @staticmethod
    def _finding_from_dict(d):
        return _finding_from_dict(d)

    @staticmethod
    def _findings_json_path(project_path, out=None):
        return _findings_json_path(project_path, out)

    def _dump_findings_json(self, r, out):
        return _dump_findings_json(r, out)

    def _load_findings_json(self, project_path, r, out=None):
        return _load_findings_json(project_path, r, out)

    # ═══════════════════════════════════
    # 检测器
    # ═══════════════════════════════════

    def _gov(self, p, r, done):
        return _gov(p, r, done)

    def _agent(self, p: str, r: PipeResult, done: set):
        if "agent" in done: return
        try:
            from core.agent_security_auditor import AgentSecurityAuditor
            a = AgentSecurityAuditor(); a.audit(p)
            for s in getattr(a, "risks", []):
                r.findings.append(Finding(id=f"agent-{len(r.findings)}", tool="agent",
                    category=getattr(s,"category",""), severity=getattr(s,"severity","medium"),
                    file_path=getattr(s,"file_path",""), line=getattr(s,"line_number",0),
                    title=f"[{getattr(s,'risk_id','')}] {getattr(s,'risk_name','')}",
                    detail=getattr(s,"detail",""), suggestion=getattr(s,"suggestion",""),
                    tier=self._tier_for(getattr(s,"severity","medium"))))
            done.add("agent"); self._save(p, list(done))
        except Exception as e: r.errors.append(f"agent: {e}")

    def _sca(self, p, r, done):
        return _sca(p, r, done)

    def _td(self, p: str, r: PipeResult, done: set):
        if "td" in done: return
        try:
            from core.tech_debt_detector import TechDebtDetector
            d = TechDebtDetector(); d.detect(p)
            for x in getattr(d, "debts", []):
                r.findings.append(Finding(id=f"td-{len(r.findings)}", tool="td",
                    category=getattr(x,"category",""), severity=getattr(x,"severity","medium"),
                    file_path=getattr(x,"file_path",""), line=getattr(x,"line",0),
                    title=getattr(x,"description",""), detail=getattr(x,"detail",getattr(x,"description","")),
                    suggestion=getattr(x,"suggestion",""), tier=self._tier_for(getattr(x,"severity","medium"))))
            done.add("td"); self._save(p, list(done))
        except Exception as e: r.errors.append(f"td: {e}")

    def _integ(self, p: str, r: PipeResult, done: set):
        if "integ" in done: return
        try:
            from core.integrity_checker import IntegrityChecker
            c = IntegrityChecker(); c.check(p)
            for s in getattr(c, "issues", []):
                r.findings.append(Finding(id=f"integ-{len(r.findings)}", tool="integ",
                    category=getattr(s,"category",""), severity=getattr(s,"severity","medium"),
                    file_path=getattr(s,"file_path",""), line=getattr(s,"line",0),
                    title=getattr(s,"content",""), detail=getattr(s,"content",""),
                    suggestion=getattr(s,"suggestion",""), tier=self._tier_for(getattr(s,"severity","medium"))))
            done.add("integ"); self._save(p, list(done))
        except Exception as e: r.errors.append(f"integ: {e}")

    def _blind(self, p: str, r: PipeResult, done: set):
        if "blind" in done: return
        try:
            from core.blind_spot_detector import BlindSpotDetector
            d = BlindSpotDetector(); d.detect(p)
            for s in getattr(d, "spots", []):
                r.findings.append(Finding(id=f"bs-{len(r.findings)}", tool="blind",
                    category=getattr(s,"category",""),
                    severity=getattr(s,"risk_level","medium"),
                    file_path=getattr(s,"file_path",""),
                    title=getattr(s,"item",""), detail=getattr(s,"detail",""),
                    suggestion=getattr(s,"user_should_know",""), tier=self._tier_for(getattr(s,"risk_level","medium"))))
            done.add("blind"); self._save(p, list(done))
        except Exception as e: r.errors.append(f"blind: {e}")

    def _inn(self, p, r, done):
        return _inn(p, r, done)

    def _junk(self, p: str, r: PipeResult, done: set):
        if "junk" in done: return
        try:
            from core.junk_detector import JunkDetector
            d = JunkDetector(); d.detect(p)
            for s in getattr(d, "_items", []):
                r.findings.append(Finding(id=f"junk-{len(r.findings)}", tool="junk",
                    category=getattr(s,"category",""), severity="low",
                    file_path=getattr(s,"file_path",""), line=0,
                    title=f"{getattr(s,'category','')}: {getattr(s,'reason','')}",
                    detail=getattr(s,"reason",""), suggestion="可安全删除" if getattr(s,"safe_to_delete",True) else "需人工确认",
                    tier=Tier.LOW))
            done.add("junk"); self._save(p, list(done))
        except Exception as e: r.errors.append(f"junk: {e}")

    def _resgap(self, p: str, r: PipeResult, done: set):
        if "resgap" in done: return
        try:
            from core.resource_gap_detector import ResourceGapDetector
            d = ResourceGapDetector(); d.detect(p)
            for s in getattr(d, "_gaps", []):
                r.findings.append(Finding(id=f"resgap-{len(r.findings)}", tool="resgap",
                    category=getattr(s,"category",""), severity=getattr(s,"severity","medium"),
                    file_path=getattr(s,"file_path",""), line=0,
                    title=getattr(s,"item",""), detail=getattr(s,"detail",""),
                    suggestion=getattr(s,"suggestion",""), tier=self._tier_for(getattr(s,"severity","medium"))))
            done.add("resgap"); self._save(p, list(done))
        except Exception as e: r.errors.append(f"resgap: {e}")

    def _simp(self, p, r, done):
        return _simp(p, r, done)

    def _matu(self, p, r, done):
        return _matu(p, r, done)

    def _workflow(self, p, r):
        return _workflow(p, r)

    def _wiki(self, p, r, done, output_dir=None, wiki_style="comprehensive",
              include_subprojects=True, enable_agent_pointer=False, cross_verify=True,
              cross_entry_spec="class:pipeline_runner:Pipe"):
        return _wiki(p, r, done, output_dir, wiki_style, include_subprojects, enable_agent_pointer, cross_verify, cross_entry_spec)

    @staticmethod
    def _latest_report(out, project_path=""):
        return _latest_report(out, project_path)

    def _reuse_no_change(self, project_path: str, r: PipeResult,
                         out: str, _prog) -> bool:
        """no_change 策略：复用既有图谱与最近审计报告，不重扫代码。

        返回 True 表示复用成功（调用方应直接返回）；False 表示无既有结论可复用。
        """
        try:
            from core.code_knowledge_graph import load_knowledge_graph
            # U-49：只有图谱、没有可复用的审计结论时不得短路复用，
            # 否则既不落 findings JSON，又渲染出"审计未执行"的页面，
            # 后续每次重渲染都因找不到产物而静默失败。
            if not _load_findings_json(project_path, r, out):
                return False
            kg = load_knowledge_graph(project_path)
            if kg is None:
                return False
            stats = kg.get_stats()
            kg.close()
            r.kg_built_at = str(stats.get("built_at", ""))
            r.audit_strategy = "no_change"
            # 复用最近一份审计报告 markdown（若存在），否则生成"复用结论"说明文案
            report_path = self._latest_report(out, project_path)
            if report_path:
                try:
                    with open(report_path, "r", encoding="utf-8") as f:
                        r.report = f.read()
                    r.report_path = report_path
                except Exception:
                    r.report = self._fmt(r, "审计报告（复用既有结论）")
            else:
                r.report = self._fmt(r, "审计报告（复用既有结论）")
            # 渲染 HTML（聚合既有图谱 + Wiki）
            self._render_html(project_path, r, kg_stats=stats,
                              output_dir=os.path.join(out, "html"))
            _prog("复用既有结论", 1, 1)
            logger.info("[audit] 策略=no_change，复用既有结论，未重扫")
            return True
        except Exception as e:
            r.errors.append(f"reuse_no_change: {e}")
            return False

    # ═══════════════════════════════════
    # 交叉验证 + 格式化
    # ═══════════════════════════════════

    def _xval(self, r):
        return _xval(r)

    # ═══════════════════════════════════
    # 自动降噪（零 LLM / 零白名单）
    # ═══════════════════════════════════

    # 降噪规则库（v4.9.5 存量债治理：定义移至模块级，类属性保持向后兼容引用）
    NOISE_RULES = NOISE_RULES

    # 爆发式重复阈值：同 tool + category > N → 合并
    BURST_THRESHOLD = 8
    # 邻近行合并窗口：同 file + tool + category 的行号差 < N → 合并
    ADJACENT_LINE_WINDOW = 5

    def _denoise(self, r):
        return _denoise(r)

    @staticmethod
    def _match_whitelist(f, wl):
        return _match_whitelist(f, wl)

    def _match_noise_rule(self, f):
        return _match_noise_rule(f)

    @staticmethod
    def _dedup_adjacent(findings):
        return _dedup_adjacent(findings)

    @staticmethod
    def _burst_merge(findings):
        return _burst_merge(findings)

    def _fmt(self, r: PipeResult, title: str) -> str:
        return _fmt(r, title, self._t0)

    # ═══════════════════════════════════
    # 知识图谱查询（供 MCP Server 调用）
    # ═══════════════════════════════════

    @staticmethod
    def kg_query(project_path, query_type, **kwargs):
        return kg_query(project_path, query_type, **kwargs)
