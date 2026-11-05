# -*- coding: utf-8 -*-
"""
报告渲染与降噪规则（core/pipeline_runner 拆解子模块）：NOISE_RULES / _render_html /
_collect_dimension_states / _match_noise_rule / _fmt。

原为 pipeline_runner.py 774~1130 行；拆出后由主文件 re-export，对外符号不变。
依赖方向：本模块依赖 pr_models 与 pr_whitelist（_findings_json_path/_detect_wiki_dir/_count_md），
供主文件与 pr_stages（_match_noise_rule）使用。
"""

import os, json, time
from datetime import datetime
from typing import Dict, Optional

from core.pr_models import Finding, PipeResult, Tier
from core.pr_whitelist import _findings_json_path, _detect_wiki_dir, _count_md


# 降噪规则库：每个 rule 检测是否为已知误报模式
NOISE_RULES = {
    # MD5 for project hashing, not crypto — IRON-SEC-10 in governance。
    # 仅抑制"纯路径/文件名哈希"误报；命中代码含 sign/签名/token/密钥/认证等
    # 安全场景词（如 目标项目 的 tool.MD5(body+timestamp+nonce+CodeRunKey) 签名）
    # 时豁免，避免真实弱加密缺陷被误抑制。
    "md5_for_hashing": {
        "tools": {"gov"},
        "category_keywords": {"security"},
        "title_keywords": {"iron-sec-10", "弱加密", "不安全的加密"},
        "detail_exclude_keywords": ["sign", "签名", "token", "密钥", "认证",
                                    "密码", "nonce", "credential", "口令",
                                    "key", "api_key", "secret", "password", "auth"],
        "action": "suppress",
        "reason": "MD5 用于项目路径哈希，非安全场景",
    },
    # exec/eval in developer tooling
    "exec_in_tooling": {
        "tools": {"gov"},
        "category_keywords": {"security"},
        "title_keywords": {"代码注入", "exec(", "eval(", "subprocess"},
        "action": "downgrade",
        "reason": "开发工具中动态导入非安全漏洞",
    },
    # Magic URL/path in config files
    "config_url": {
        "tools": {"td"},
        "category_keywords": {"magic_value"},
        "file_keywords": {"config", "settings", ".yaml", ".toml", ".ini"},
        "title_keywords": {"http://", "https://", "localhost", "DB_"},
        "action": "suppress",
        "reason": "配置文件中的 URL/路径是正常配置",
    },
    # Blind spot "missing dependency" where module is sibling dir
    "sibling_import": {
        "tools": {"blind"},
        "category_keywords": {"missing_dependency"},
        "action": "downgrade",
        "reason": "同项目内跨目录 import 被误判为缺失依赖",
    },
    # doc_blindspot flood — 每个目录都说"没有 docs/"
    "doc_blindspot_flood": {
        "tools": {"blind"},
        "category_keywords": {"doc_blindspot"},
        "action": "downgrade",
        "reason": "目录缺少 docs/ 是普遍现象非真实盲区",
    },
}

# ══ 模块级函数（v4.9.5 存量债治理第二波提取） ══
def _render_html(project_path: str, r: PipeResult,
                 kg_stats: Optional[dict] = None,
                 output_dir: Optional[str] = None) -> dict:
    """调用 report_renderer 渲染 HTML 报告目录，结果写入 r.html_report。

    失败不阻塞主流程：渲染异常记录到 r.errors，避免 HTML 前端问题拖垮审计结果。
    """
    try:
        from core.report_renderer import HtmlReportRenderer
        # 未显式传入图谱统计时，尝试加载已有知识图谱，避免 HTML 图谱页显示"不可用"
        if kg_stats is None:
            try:
                from core.code_knowledge_graph import load_knowledge_graph
                existing = load_knowledge_graph(project_path)
                if existing is not None:
                    kg_stats = existing.get_stats()
                    existing.close()
            except Exception:
                kg_stats = None
        # 实时管线（audit/docs）未预置维度状态时，依据真实产物自动补全，
        # 避免已构建的图谱在 HTML 中被误标为"未执行"（重渲染路径 render_report 已预置）。
        if not getattr(r, "dimension_states", None):
            try:
                # findings 落盘于 HTML 输出目录的父目录（out/html → out），据此探测 audit 维度
                findings_dir = os.path.dirname(os.path.abspath(output_dir)) if output_dir else None
                r.dimension_states = _collect_dimension_states(
                    project_path, kg_stats, findings_dir)
            except Exception:
                r.dimension_states = None
        wiki_dir = None
        wr = getattr(r, "wiki_result", None)
        if wr is not None:
            wiki_dir = getattr(wr, "output_dir", None) or None
        renderer = HtmlReportRenderer(project_path)
        result = renderer.render(
            r, kg_stats=kg_stats, wiki_dir=wiki_dir,
            output_dir=output_dir,
            dimension_states=getattr(r, "dimension_states", None))
        r.html_report = result
        if not result.get("ok"):
            r.errors.append(f"html_report: {result.get('error', '渲染失败')}")
        return result
    except Exception as e:
        r.errors.append(f"html_report: {e}")
        r.html_report = {"ok": False, "error": str(e), "files": []}
        return r.html_report

def _collect_dimension_states(project_path: str,
                              kg_stats: Optional[dict],
                              output_dir: Optional[str] = None) -> dict:
    """检测各产出维度（审计/图谱/Wiki）的实际执行状态，供 HTML 报告透明化展示。

    目的：报告聚合不再"缺省放行"（有就展示、没有就静默为空/全 0），而是显式标注
    每个维度是否真正执行过。未执行的维度给出明确指引，避免把"未审计"伪装成"没问题"。

    注意：审计 findings 的探测路径必须与 has_artifacts 判定一致（跟随 output_dir），
    否则自定义输出目录时，判定"存在"却检测"未执行"，出现矛盾展示。
    """
    states: Dict[str, dict] = {}

    # ── 审计维度 ──
    # 与 has_artifacts 判定一致：findings 可能落在调用方输出目录，也可能落在
    # 项目侧 `.coderef/`（权威副本）。只看一处会出现"加载到了发现却显示未执行"。
    audit_fp = _findings_json_path(project_path, output_dir)
    if not os.path.isfile(audit_fp):
        try:
            from core.pr_whitelist import project_findings_json_path
            candidate = project_findings_json_path(project_path)
            if os.path.isfile(candidate):
                audit_fp = candidate
        except Exception:  # noqa: BLE001
            pass
    if os.path.isfile(audit_fp):
        try:
            with open(audit_fp, "r", encoding="utf-8") as f:
                data = json.load(f)
            findings = data.get("findings", []) or []
            states["audit"] = {
                "status": "done",
                "ts": data.get("scan_ts", "") or "",
                "count": len(findings),
                "hint": f"已执行审计，共 {len(findings)} 条发现",
            }
        except Exception:
            states["audit"] = {
                "status": "missing", "ts": "", "count": 0,
                "hint": "审计结果文件损坏，请重新运行 coderef_audit",
            }
    else:
        states["audit"] = {
            "status": "missing", "ts": "", "count": 0,
            "hint": "尚未执行审计，请先运行 coderef_audit",
        }

    # ── 知识图谱维度 ──
    if kg_stats:
        states["kg"] = {
            "status": "done",
            "ts": kg_stats.get("built_at", "") or "",
            "nodes": kg_stats.get("node_count", 0),
            "hint": f"知识图谱已构建，{kg_stats.get('node_count', 0)} 节点",
        }
    else:
        states["kg"] = {
            "status": "missing", "ts": "", "nodes": 0,
            "hint": "尚未构建知识图谱，请先运行 coderef_audit 或构建图谱",
        }

    # ── Wiki 维度 ──
    wiki_dir = _detect_wiki_dir(project_path)
    if wiki_dir:
        md_count = _count_md(wiki_dir)
        try:
            ts = datetime.fromtimestamp(
                os.path.getmtime(wiki_dir)).strftime("%Y-%m-%d %H:%M:%S")
        except Exception:
            ts = ""
        states["wiki"] = {
            "status": "done", "ts": ts, "docs": md_count,
            "hint": f"Wiki 已生成，{md_count} 篇文档",
        }
    else:
        states["wiki"] = {
            "status": "missing", "ts": "", "docs": 0,
            "hint": "尚未生成 Wiki，请先运行 coderef_docs",
        }

    # 图谱滞后提示：审计扫描晚于图谱构建时，图谱可能滞后于代码
    ats = states.get("audit", {}).get("ts", "")
    kts = states.get("kg", {}).get("ts", "")
    if states.get("kg", {}).get("status") == "done" and ats and kts and ats > kts:
        states["kg"]["hint"] = states["kg"].get("hint", "") + "（图谱构建早于审计，可能滞后于代码）"

    return states

def _match_noise_rule(f: Finding) -> dict:
    """f 是否匹配任一降噪规则"""
    title_lower = f.title.lower()
    cat_lower = f.category.lower()
    file_lower = os.path.basename(f.file_path).lower() if f.file_path else ""

    for name, rule in NOISE_RULES.items():
        # 工具过滤
        if rule.get("tools") and f.tool not in rule["tools"]:
            continue
        # 分类关键词
        if rule.get("category_keywords"):
            if not any(kw in cat_lower for kw in rule["category_keywords"]):
                continue
        # 标题关键词
        if rule.get("title_keywords"):
            if not any(kw in title_lower for kw in rule["title_keywords"]):
                continue
        # 文件关键词
        if rule.get("file_keywords"):
            if not any(kw in file_lower for kw in rule["file_keywords"]):
                continue
        # 详情排除关键词：detail 含任一关键词 → 不匹配该规则（真实安全场景豁免，
        # 如 IRON-SEC-10 弱加密用于签名/密钥而非纯路径哈希）
        if rule.get("detail_exclude_keywords"):
            det_lower = (f.detail or "").lower()
            if any(kw in det_lower for kw in rule["detail_exclude_keywords"]):
                continue
        # 目录关键词
        if rule.get("dir_keywords") and f.file_path:
            dir_lower = os.path.dirname(f.file_path).lower()
            if not any(kw in dir_lower for kw in rule["dir_keywords"]):
                continue
        return rule
    return {}

def _fmt(r: PipeResult, title: str, t0: float = 0.0) -> str:
    # 修复：报告头时间取自错误变量，导致 elapsed 显示 0.0s。
    # 若调用方尚未写入 r.elapsed（在 _fmt 之后才赋值），此处实时兜底计算，
    # 保证报告头展示真实耗时。
    if r.elapsed == 0.0 and t0:
        r.elapsed = round(time.time() - t0, 1)
    # 把"建议项"(kind=advice) 与"缺陷项"(defect) 分开统计/展示，
    # 建议项不计入 HIGH/MEDIUM/LOW 缺陷表，避免工程化改进建议被误报为缺陷。
    # 统计按 count 加权：爆发式合并项（count>1）代表 N 条真实违规，
    # 不能按 1 条计，否则 len(findings) 与真实违规数严重不符（证据审计缺陷 3）。
    def _weighted(fl):
        return sum(getattr(f, "count", 1) for f in fl)
    def _row_desc(f, max_len):
        desc = f.title
        if hasattr(f, "count") and f.count > 1:
            desc += f" (共 {f.count} 条)"
        if f.detail and "生物合成" in f.detail:
            desc += " [含生物合成关键词]"
        return desc[:max_len]
    h = [f for f in r.findings if f.tier == Tier.HIGH and f.kind != "advice"]
    m = [f for f in r.findings if f.tier == Tier.MEDIUM and f.kind != "advice"]
    l = [f for f in r.findings if f.tier == Tier.LOW and f.kind != "advice"]
    adv = [f for f in r.findings if f.kind == "advice"]
    lines = [
        f"# CodeRef {title}",
        f"项目: `{r.project_path}` | 文件: {r.total_files} | 行: {r.total_lines} | {r.elapsed}s",
        f"时间: {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        "",
        "## 统计口径",
        f"- **本次扫描时间**: {r.scan_ts or datetime.now().strftime('%Y-%m-%d %H:%M:%S')}（本次审计实际扫描代码的时刻）",
        f"- **知识图谱构建时间**: {r.kg_built_at or '本次未重建图谱'}（图谱可能滞后于代码，若两者不一致请以重建后为准）",
        f"- **统计范围**: 下表仅覆盖本次扫描的 {r.total_files} 个文件（详见 `file_snapshot`），均为**审计发现**，不代表任何修复状态；修复状态需对照 git 提交单独核实。",
        "",
        "## 置信度",
        "| 🔴 HIGH | 🟡 MEDIUM | ⚪ LOW | 💡 建议 |",
        "|----------|------------|---------|---------|",
        f"| {_weighted(h)} | {_weighted(m)} | {_weighted(l)} | {_weighted(adv)} |",
        "",
    ]
    if r.scope_text:
        lines.append(f"> 📋 审计范围: {r.scope_text}")
        lines.append("")
    ns = getattr(r, 'noise_suppressed', 0)
    nd = getattr(r, 'noise_downgraded', 0)
    wl = getattr(r, 'wl_suppressed', 0)
    dx = getattr(r, 'dir_excluded', 0)
    if ns or nd or wl or dx:
        parts = []
        if ns: parts.append(f"抑制 {ns} 条已知误报")
        if nd: parts.append(f"降级 {nd} 条低置信度")
        if wl: parts.append(f"白名单过滤 {wl} 条")
        if dx: parts.append(f"白名单目录排除 {dx} 条")
        lines.append(f"> 🤖 自动降噪: {'; '.join(parts)}")
        lines.append("")
    if r.errors:
        lines.append("## ⚠️ 检测器异常")
        lines.append(f"> 共 **{len(r.errors)}** 个错误，以下工具执行失败，对应发现可能缺失或不全，请优先排查。")
        lines.append("")
        # 按工具归类，快速定位失败的工具与异常摘要（"tool: message" 格式）
        from collections import OrderedDict
        grouped = OrderedDict()
        for e in r.errors:
            tool, sep, rest = e.partition(":")
            key = tool.strip() if sep else "unknown"
            grouped.setdefault(key, []).append(rest.strip() if sep else e)
        lines.append("| 工具 | 失败数 | 异常摘要 |")
        lines.append("|------|--------|----------|")
        for tool, errs in grouped.items():
            lines.append(f"| `{tool}` | {len(errs)} | `{errs[0][:80]}` |")
        lines.append("")
        lines.append("**错误明细：**")
        for tool, errs in grouped.items():
            for err in errs:
                lines.append(f"- `{tool}: {err}`")
        lines.append("")
        lines.append("> 提示：失败的工具不会写入 checkpoint 完成集合，`resume=true` 重新运行时将自动重试这些工具，异常不会被断点续跑永久隐藏。")
        lines.append("")
    if h:
        lines.append("## 🔴 HIGH（多工具交叉验证）");
        lines.append("|工具|分类|程度|位置|描述|");
        lines.append("|---|---|---|---|---|")
        for f in h[:30]:
            xv = f" ×{','.join(f.xval_by)}" if f.xval_by else ""
            lines.append(f"|{f.tool}|{f.category}|{f.severity}|{f.line_label}|{_row_desc(f, 60)}{xv}|")
        lines.append("")
    if m:
        lines.append("## 🟡 MEDIUM");
        lines.append("|工具|分类|程度|位置|描述|")
        lines.append("|---|---|---|---|---|")
        for f in m[:20]: lines.append(f"|{f.tool}|{f.category}|{f.severity}|{f.line_label}|{_row_desc(f, 80)}|")
        lines.append("")
    if adv:
        lines.append("## 💡 建议（工程化改进项，非缺陷）")
        lines.append("|工具|分类|程度|位置|描述|")
        lines.append("|---|---|---|---|---|")
        for f in adv[:20]:
            lines.append(f"|{f.tool}|{f.category}|{f.severity}|{f.line_label}|{f.title[:80]}|")
        lines.append("")

    # 功能②：审计策略建议 + LLM 功能审查结论（若有）
    strat = getattr(r, "review_strategy", None)
    if strat:
        lines.append("## 🧭 审计策略建议")
        if strat.get("explicit"):
            # 显式指定策略：未做自动判定，仅展示策略与原因，避免渲染空判定字段误导
            lines.append(f"- **建议策略**: `{strat.get('strategy', 'full')}`（显式指定）")
            lines.append(f"- **原因**: {strat.get('reason', '')}")
        else:
            lines.append(f"- **建议策略**: `{strat.get('strategy', 'full')}`")
            lines.append(f"- **原因**: {strat.get('reason', '')}")
            ch = strat.get("changes", {})
            lines.append(f"- **变更**: 变更 {len(ch.get('changed', []))}、新增 {len(ch.get('added', []))}、删除 {len(ch.get('deleted', []))} 个文件")
            imp = strat.get("impact", {})
            lines.append(f"- **影响闭包**: 波及 {imp.get('count', 0)} 个节点（深度 {imp.get('depth', 0)}）")
            kg = strat.get("kg", {})
            lines.append(f"- **知识图谱**: 存在={kg.get('exists')}，构建={kg.get('built_at') or '无'}，过期={kg.get('stale')}")
        lines.append("")
    fr = getattr(r, "functional_review", None)
    if fr:
        lines.append("## 🎯 功能审查（LLM 语义增强）")
        lines.append(f"- **LLM**: {'可用' if fr.get('llm_available') else '不可用（静态降级）'}")
        overall = fr.get("overall", {})
        lines.append(f"- **整体结论**: {overall.get('verdict', '')} - {overall.get('summary', '')}")
        for dr in fr.get("dimension_reviews", []):
            lines.append(f"- **{dr.get('label', dr.get('dimension', ''))}** [{dr.get('verdict', '')}]: "
                         f"{dr.get('summary', '')}（{dr.get('detail', '')}）")
        if fr.get("recommendation"):
            lines.append(f"- **建议**: {fr['recommendation']}")
        # 逐条粗筛结果（v1.1）：疑似误报建议反馈白名单，不自动过滤
        screen = fr.get("screen") or {}
        if screen.get("ran"):
            sm = screen.get("summary", {})
            lines.append(
                f"- **逐条粗筛**: 疑似误报 {sm.get('suspected_fp', 0)} 条、"
                f"需确认 {sm.get('needs_review', 0)} 条、真问题 {sm.get('confirmed', 0)} 条"
            )
            cands = screen.get("candidates") or []
            if cands:
                lines.append("  - **疑似误报（待用户 AI 核实后可反馈白名单）**:")
                for c in cands[:10]:
                    parts = " / ".join(f"{k}={v}" for k, v in c.items() if v)
                    lines.append(f"    - `{parts}`")
                lines.append("    - 确认无误后调用 `coderef_whitelist(action=add)` 反馈，下次自动过滤。")
        lines.append("")
    lines.append(f"---\n{r.report_path or ''}")
    return "\n".join(lines)
