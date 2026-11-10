# -*- coding: utf-8 -*-
"""product_eval — F 轨·业务契合度评估（v5.18.0）。

主轴：逐能力核对业务逻辑是否支撑真实决策（而非界面好不好看）。

  - F1：能力 × 五问矩阵（支撑哪个决策 / 输入现实性 / 输出可行动性 /
    数据诚实度 / 频率匹配）；输入现实性一律标 `proxy=true`。
  - F2：链路核验**优先确定性**（知识图谱 CALLS 边），图谱跑不出才降级推断。
  - B 轨：业务模式（该不该做）条件触发，营销六维降级为 B 轨内容。
  - 结论：Go / Validate-first / No-go + 待验证假设清单。

诚实边界：
  - **无 LLM Key 只回证据包 + `status="SKIP"`，不产出三档结论**；
  - 硬禁止市场/竞品数字，LLM 输出含此类数字即整份拒收；
  - E4（模拟推断）必须进待验证假设清单，禁止当事实；
  - 能力枚举可能遗漏，不宣称全量覆盖。
"""

import json
import os
from datetime import datetime
from typing import Dict, List, Optional

from loguru import logger

from core.eval_common import (
    anchor_exists as _anchor_exists,
    parse_json_object as _parse_json,
    rel as _rel,
    write_json_result,
)
from core.product_evidence import (
    FAKE_DATA_MARKERS,
    INPUT_HINTS,
    MARKET_NUMBER_RE,
    OUTPUT_HINTS,
    build_evidence,
    save_stage,
)
from core.version import __version__

_VERDICTS = ("Go", "Validate-first", "No-go")
_STAGES = ("concept", "pmf", "expansion")


def _module_text(project_path: str, module: str) -> str:
    mod = str(module or "").strip().replace("\\", "/")
    for ext in (".py", ".ts", ".tsx", ".js", ".jsx", ".vue"):
        fp = os.path.join(project_path, mod + ext)
        if os.path.isfile(fp):
            try:
                with open(fp, "r", encoding="utf-8", errors="replace") as f:
                    return f.read()
            except OSError:
                return ""
    return ""


def _hint_in_modules(modules: List[str], hints: tuple) -> bool:
    for m in modules:
        low = str(m).lower()
        if any(h in low for h in hints):
            return True
    return False


# ────────────────────────────────────────────────────────────────
# F1 五问矩阵（确定性预填，LLM 只做语义确认与结论）
# ────────────────────────────────────────────────────────────────

def _q(answer: str, status: str, level: str = "E2", proxy: bool = False) -> dict:
    q = {"answer": answer, "status": status, "evidence_level": level}
    if proxy:
        q["proxy"] = True
    return q


def _matrix(project_path: str, capabilities: List[dict],
            claims: List[dict]) -> dict:
    rows: List[dict] = []
    for cap in capabilities:
        mods = cap.get("modules") or []
        matched = [c["text"][:80] for c in claims
                   if cap["name"] in (c.get("matched_capabilities") or [])]
        decision = _q(matched[0] if matched else "未能从文档确证该能力支撑哪个决策",
                      "known" if matched else "unknown",
                      "E3" if matched else "E4")

        # ② 输入现实性：代码只能推「有无上游数据源」，不能推「用户手上有什么」
        has_src = _hint_in_modules(mods, INPUT_HINTS) or cap["source"] == "frontend"
        input_q = _q(
            "存在上游数据源候选（模块命名/前端表单），仍需人工确证用户是否持有该数据"
            if has_src else
            "未在代码中发现上游数据源候选；不代表用户一定没有该数据",
            "has_source_candidate" if has_src else "no_source_candidate",
            "E2", proxy=True)

        # ③ 输出可行动性：是否有用户可见/可导出的产出线索
        has_out = _hint_in_modules(mods, OUTPUT_HINTS) or cap["source"] == "frontend"
        output_q = _q(
            "存在产出/展示线索，需人工确证产出是否可被直接用于决策"
            if has_out else "未发现明确产出线索",
            "has_output_candidate" if has_out else "unknown",
            "E2", proxy=True)

        # ④ 数据诚实度：代码里是否有演示/假数据标记
        text = "\n".join(_module_text(project_path, m) for m in mods[:2]).lower()
        fake = any(k in text for k in FAKE_DATA_MARKERS) if text else False
        honesty_q = _q(
            "检测到演示/示例数据标记，需人工确认线上是否仍使用" if fake else
            "未检测到演示/示例数据标记（不构成「数据真实」的证明）",
            "suspicious_demo_data" if fake else "unknown",
            "E2" if fake else "E4")

        # ⑤ 频率匹配：能力形态 → 使用频率（代码可观察）
        freq = {"cli": "batch_or_command", "route": "request_driven",
                "frontend": "interactive"}.get(cap["source"], "unknown")
        freq_q = _q({"batch_or_command": "命令/批处理形态，按需触发",
                     "request_driven": "请求驱动，频率取决于调用方",
                     "interactive": "交互式，频率取决于用户任务"}.get(freq, "无法从代码判定使用频率"),
                    freq, "E2" if freq != "unknown" else "E4")

        rows.append({
            "capability_id": cap["id"],
            "name": cap["name"],
            "source": cap["source"],
            "anchors": cap["anchors"],
            "modules": mods[:6],
            "q1_decision": decision,
            "q2_input": input_q,
            "q3_output": output_q,
            "q4_honesty": honesty_q,
            "q5_frequency": freq_q,
        })

    answered = sum(1 for r in rows
                   if r["q1_decision"]["status"] == "known"
                   and r["q3_output"]["status"] != "unknown")
    blocked = sum(1 for r in rows if r["q2_input"]["status"] == "no_source_candidate")
    redlines = sum(1 for r in rows
                   if r["q2_input"]["status"] == "no_source_candidate"
                   and r["q1_decision"]["status"] == "unknown")
    total = len(rows)
    return {
        "rows": rows,
        "capability_count": total,
        "fit_rate": round(answered / total, 2) if total else None,
        "blocked_fields": blocked,
        "redlines": redlines,
        "note": ("② 输入现实性与 ③ 输出可行动性均为 proxy（代码侧线索），"
                 "不能替代「用户是否真的持有数据 / 产出是否真的可行动」的人工确证。"),
    }


# ────────────────────────────────────────────────────────────────
# F2 链路核验（确定性优先：知识图谱 CALLS 边）
# ────────────────────────────────────────────────────────────────

def _chain_check(project_path: str, capabilities: List[dict],
                 claims: List[dict]) -> dict:
    claim_rows = [{
        "anchor": c["anchor"],
        "anchor_exists": _anchor_exists(project_path, c["anchor"]),
        "verified": c.get("verified", "unknown"),
        "evidence_level": "E2" if _anchor_exists(project_path, c["anchor"]) else "E3",
    } for c in claims]
    try:
        from core.arch_audit import locate_kg_db, module_of
        from core.graph_closure import load_graph
        db = locate_kg_db(project_path)
        nodes, adj = load_graph(db) if db else ({}, {})
    except Exception as e:  # noqa: BLE001
        nodes, adj, db = {}, {}, None
        logger.warning(f"[product_eval] 知识图谱读取失败，链路核验降级: {e}")

    if not db or not nodes:
        return {
            "has_graph": False,
            "broken_chains": None,
            "capabilities": [],
            "claims": claim_rows,
            "note": ("无知识图谱（请先运行 coderef_architecture），F2 链路核验仅退化为"
                     "「锚点文件是否存在」，未做上下游消费关系核验。"),
        }

    # 模块 → 图谱节点
    mod_nodes: Dict[str, set] = {}
    for nid, n in nodes.items():
        m = module_of(n, project_path)
        mod_nodes.setdefault(m, set()).add(nid)

    cap_by_mod: Dict[str, str] = {}
    for cap in capabilities:
        for m in cap.get("modules") or []:
            cap_by_mod.setdefault(m, cap["id"])

    rows: List[dict] = []
    for cap in capabilities:
        ids: set = set()
        for m in cap.get("modules") or []:
            ids |= mod_nodes.get(m, set())
        targets: Dict[str, int] = {}
        for src in ids:
            for tgt in adj.get(src, []):
                tm = module_of(nodes.get(tgt, {}), project_path)
                other = cap_by_mod.get(tm)
                if other and other != cap["id"]:
                    targets[other] = targets.get(other, 0) + 1
        has_output = _hint_in_modules(cap.get("modules") or [], OUTPUT_HINTS)
        status = ("connected" if targets else
                  ("isolated" if ids else "no_graph_node"))
        rows.append({
            "capability_id": cap["id"],
            "name": cap["name"],
            "graph_nodes": len(ids),
            "downstream_capabilities": sorted(targets),
            "status": status,
            # 有产出线索却没有任何下游能力消费：按「疑似断链」记，不判死
            "suspected_broken": bool(has_output and not targets),
        })

    broken = sum(1 for r in rows if r["suspected_broken"])
    return {
        "has_graph": True,
        "graph": _rel(project_path, db),
        "broken_chains": broken,
        "capabilities": rows,
        "claims": claim_rows,
        "note": ("静态 CALLS 边不含动态分发/反射，isolated 不等于真的断链；"
                 "suspected_broken 仅表示「有产出线索但无下游能力消费」。"),
    }


# ────────────────────────────────────────────────────────────────
# B 轨条件触发
# ────────────────────────────────────────────────────────────────

def _b_track_trigger(evidence: dict, matrix: dict, chain: dict) -> dict:
    reasons: List[str] = []
    if not evidence.get("claims"):
        reasons.append("未提取到产品声称，F0 方向对齐存疑")
    if chain.get("broken_chains"):
        reasons.append(f"F2 疑似断链 {chain['broken_chains']} 处")
    if matrix.get("redlines"):
        reasons.append(f"红线能力 {matrix['redlines']} 个（既无上游数据源又说不清决策）")
    if any(r["q4_honesty"]["status"] == "suspicious_demo_data"
           for r in matrix.get("rows") or []):
        reasons.append("检测到演示数据标记，方向/交付可信度需复核")
    return {"triggered": bool(reasons), "reasons": reasons,
            "note": ("B 轨为条件触发；触发时由 LLM 补「产品成型卡/欲望三角/痛点覆盖/"
                     "董事会视角」并给 Go/Validate-first/No-go 方向建议。")}


# ────────────────────────────────────────────────────────────────
# LLM 结论（严格 JSON、硬禁市场数字、E4 必进假设清单）
# ────────────────────────────────────────────────────────────────

def _prompt(evidence: dict, matrix: dict, chain: dict, b_track: dict) -> List[dict]:
    payload = {
        "stage": evidence.get("stage"),
        "capabilities": [{"id": c["id"], "name": c["name"], "source": c["source"],
                          "anchors": c["anchors"][:2], "modules": c["modules"][:3]}
                         for c in evidence.get("capabilities") or []][:60],
        "claims": [{"text": c["text"], "anchor": c["anchor"],
                    "verified": c["verified"]} for c in evidence.get("claims") or []][:40],
        "first_value_path": evidence.get("first_value_path"),
        "signals": evidence.get("signals"),
        "deterministic_matrix": matrix,
        "chain": {k: v for k, v in chain.items() if k != "capabilities"},
        "b_track": b_track,
        "unknowns": evidence.get("unknowns"),
    }
    system = (
        "你是产品业务契合度评估器。只按 F 轨（业务契合度）判断，不评价界面美观。\n"
        "硬规则：\n"
        "1) 禁止编造任何市场/竞品数字、份额、增长率、用户规模；没有证据就写 unknown。\n"
        "2) 证据分级 E1 实测 / E2 代码可核 / E3 文档声明 / E4 模拟推断；"
        "E4 必须逐条进入 assumptions（待验证假设）。\n"
        "3) 输入现实性只能表述为「有无上游数据源」的 proxy，不得断言用户手上有什么数据。\n"
        "4) 阶段感知：概念期/PMF 搜索期/扩张期，禁止用成熟期指标判原型。\n"
        "5) 业务逻辑不支撑决策的能力进 cut_or_change（砍/改清单），不进修复清单。\n"
        "只输出 JSON：{\"stage\":{\"phase\":\"concept|pmf|expansion\",\"reason\":\"...\"},"
        "\"f1\":[{\"capability_id\":\"...\",\"q1_decision\":{...},\"q2_input\":{...},"
        "\"q3_output\":{...},\"q4_honesty\":{...},\"q5_frequency\":{...},"
        "\"evidence_level\":\"E1|E2|E3|E4\"}],"
        "\"fit_rate\":0-1,\"broken_chains\":整数,\"blocked_fields\":整数,\"redlines\":整数,"
        "\"cut_or_change\":[{\"capability_id\":\"...\",\"reason\":\"...\"}],"
        "\"b_track\":{\"needed\":true/false,\"content\":{...}} 或 {\"needed\":false},"
        "\"conclusion\":{\"verdict\":\"Go|Validate-first|No-go\",\"reason\":\"...\","
        "\"conditions\":[\"...\"]},"
        "\"assumptions\":[{\"text\":\"...\",\"evidence_level\":\"E4\"}],"
        "\"unknowns\":[\"...\"]}。unknowns 不得为空。")
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
    ]


def _validate_llm(out: dict) -> str:
    """返回空串表示通过；否则返回拒收原因。"""
    if MARKET_NUMBER_RE.search(json.dumps(out, ensure_ascii=False)):
        return "LLM 输出含市场/竞品数字，按硬红线整份拒收"
    concl = out.get("conclusion")
    if not isinstance(concl, dict) or concl.get("verdict") not in _VERDICTS:
        return "LLM 结论缺少合法 verdict（Go / Validate-first / No-go）"
    if not isinstance(out.get("unknowns"), list) or not out["unknowns"]:
        return "LLM 输出未给出 unknowns（未知项必须非空）"
    if not isinstance(out.get("f1"), list) or not out["f1"]:
        return "LLM 输出没有能力矩阵（f1 为空），拒收"
    e4 = [r for r in (out.get("f1") or [])
          if isinstance(r, dict) and str(r.get("evidence_level")) == "E4"]
    assumptions = out.get("assumptions") or []
    if e4 and not assumptions:
        return "LLM 输出含 E4 推断却未给出待验证假设清单"
    st = out.get("stage")
    if st is not None and (not isinstance(st, dict) or st.get("phase") not in _STAGES):
        return "LLM 输出的阶段判定非法（应为 concept/pmf/expansion）"
    return ""


# ────────────────────────────────────────────────────────────────
# 顶层入口
# ────────────────────────────────────────────────────────────────

def _filter_scope(capabilities: List[dict], scope) -> tuple:
    if not scope:
        return capabilities, []
    want = {str(s).strip().lower() for s in scope if str(s).strip()}
    picked = []
    for c in capabilities:
        keys = {c["id"].lower(), c["name"].lower()}
        if keys & want:
            picked.append(c)
    seen = {k for c in picked for k in (c["id"].lower(), c["name"].lower())}
    missing = sorted(w for w in want if w not in seen)
    return picked, missing


def _result(project_path: str, status: str, evidence: dict, matrix: dict,
            chain: dict, b_track: dict, extra: Optional[dict] = None,
            llm_available: bool = False) -> dict:
    base = {
        "status": status,
        "version": __version__,
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "project": os.path.basename(os.path.abspath(project_path).rstrip(os.sep)),
        "stage": evidence.get("stage"),
        "evidence": evidence,
        "f1_deterministic": matrix,
        "chain": chain,
        "b_track": b_track,
        "llm_available": llm_available,
        "simulated": False,
    }
    if extra:
        base.update(extra)
    return base


def write_result(project_path: str, result: dict) -> str:
    """落盘 `.coderef/product_fit.json`（CI 与 AI 可读旁路产物）。"""
    return write_json_result(project_path, "product_fit.json", result)


def _degrade(pp: str, result: dict, reason: str) -> dict:
    """LLM 不可用/失败/输出非法时如实降级为 SKIP 并落盘。"""
    result["conclusion"]["reason"] = reason
    write_result(pp, result)
    return result


def _call_llm(prompt: List[dict]) -> tuple:
    """调用 LLM；返回 (raw, error, available)，不抛异常。"""
    try:
        from core.llm_integration import LLMIntegration
        llm = LLMIntegration()
        if not llm.is_available():
            return None, "", False
        # 大项目能力矩阵（最多 60 项 × 五问）输出较长，4000 tokens 容易被截断
        # 导致 JSON 解析失败；放大到 8000，仍受主 LLM 调用预算约束。
        # 产品评估的合法 JSON 可能超过通用 12000 字符输出封顶（紧凑重试实测
        # 12 项能力约 12k 字符），这里只为本实例放宽，避免把完整 JSON 截断。
        if getattr(llm, "output_cap_chars", 0) < 24000:
            llm.output_cap_chars = 24000
        raw = llm.chat_completion(prompt, response_format={"type": "json_object"},
                                  max_tokens=8000)
        return raw, "", True
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[product_eval] LLM 调用异常，降级 SKIP: {e}")
        return None, f"LLM 调用异常，已降级为 SKIP: {e}", True


def _merge_llm(out: dict, evidence: dict, matrix: dict, chain: dict,
               b_track: dict, extra: dict, pp: str) -> dict:
    """把 LLM 的语义判断合进确定性结果：只补五问，不覆盖身份字段与计数。"""
    merged = _result(pp, "OK", evidence, matrix, chain, b_track,
                     extra, llm_available=True)
    st = out.get("stage")
    merged["stage"] = st if isinstance(st, dict) else evidence.get("stage")

    llm_rows = {r.get("capability_id"): r for r in (out.get("f1") or [])
                if isinstance(r, dict) and r.get("capability_id")}
    f1_rows = []
    for det in matrix["rows"]:
        row = dict(det)
        llm_row = llm_rows.get(det["capability_id"]) or {}
        for qk, qv in llm_row.items():
            if not qk.startswith("q") or not isinstance(qv, dict):
                continue
            merged_q = dict(row.get(qk) or {})
            for k, v in qv.items():
                if k != "proxy":  # proxy 口径由本模块强制，不接受 LLM 覆盖
                    merged_q[k] = v
            row[qk] = merged_q
        for qk in ("q2_input", "q3_output"):
            if isinstance(row.get(qk), dict):
                row[qk]["proxy"] = True
        f1_rows.append(row)
    merged["f1"] = f1_rows
    merged["fit_rate"] = matrix["fit_rate"]
    merged["broken_chains"] = chain.get("broken_chains")
    merged["blocked_fields"] = matrix["blocked_fields"]
    merged["redlines"] = matrix["redlines"]
    merged["cut_or_change"] = out.get("cut_or_change") or []
    merged["b_track"] = out.get("b_track") or b_track
    merged["conclusion"] = out["conclusion"]
    merged["assumptions"] = out.get("assumptions") or []
    merged["unknowns"] = out.get("unknowns") or evidence.get("unknowns") or []
    merged["evidence_note"] = evidence.get("evidence_note", "")
    return merged


def evaluate(project_path: str, stage: Optional[str] = None,
             scope: Optional[List[str]] = None,
             use_llm: bool = True) -> dict:
    """F 轨评估：确定性证据包 + 五问矩阵 +（可选）LLM 语义确认与三档结论。

    Args:
        project_path: 目标项目。
        stage: 确认门的阶段（concept/pmf/expansion）；传入即写回 eval_config.json。
        scope: 能力 id / 名称子集（确认门的范围）；不传为全部已枚举能力。
        use_llm: 测试或 CI 可显式关闭（关闭即 SKIP）。
    """
    pp = os.path.abspath(project_path)
    if not os.path.isdir(pp):
        return {"status": "ERROR", "error": f"项目路径不存在: {pp}"}

    if stage:
        if stage not in _STAGES:
            return {"status": "ERROR",
                    "error": f"未知阶段: {stage!r}（可选: {', '.join(_STAGES)}）"}
        save_stage(pp, stage)

    evidence = build_evidence(pp)
    caps, missing_scope = _filter_scope(evidence.get("capabilities") or [], scope)
    if scope:
        evidence["capabilities"] = caps
    matrix = _matrix(pp, caps, evidence.get("claims") or [])
    chain = _chain_check(pp, caps, evidence.get("claims") or [])
    b_track = _b_track_trigger(evidence, matrix, chain)

    extra = {"scope_missing": missing_scope} if missing_scope else {}
    result = _result(pp, "SKIP", evidence, matrix, chain, b_track, extra)
    result["conclusion"] = {
        "verdict": None,
        "reason": "未配置 LLM API Key：只产出确定性证据包与五问预填，不产出三档结论",
    }
    result["assumptions"] = []
    result["cut_or_change"] = []

    # 没有任何可评估能力时，不允许 LLM 基于空证据给出方向性结论
    if not caps:
        reason = ("未枚举到任何可评估能力（业务树/前端/路由/CLI 均为空，"
                  "或 scope 指定的能力全部缺失），不产出三档结论")
        result["unknowns"] = list(dict.fromkeys(
            list(evidence.get("unknowns") or []) + [reason]))
        return _degrade(pp, result, reason)

    if not use_llm:
        write_result(pp, result)
        return result

    raw, err, available = _call_llm(_prompt(evidence, matrix, chain, b_track))
    if err:
        return _degrade(pp, result, err)
    if not available:
        write_result(pp, result)
        return result

    from core.llm_integration import LLMIntegration
    if LLMIntegration.is_error_response(raw):
        return _degrade(pp, result, f"LLM 调用失败，已降级为 SKIP: {raw[:200]}")

    out = _parse_json(raw)
    if out is None:
        # 大项目能力矩阵可能超出模型单次输出上限，整段 JSON 被截断。
        # 退化为「前 12 个能力」的紧凑重试：拿到语义确认即可，其余能力
        # 保留确定性矩阵结果，并在 assumptions 里如实标注覆盖范围。
        trim_evidence = dict(evidence)
        trim_evidence["capabilities"] = (evidence.get("capabilities") or [])[:12]
        trim_matrix = dict(matrix)
        trim_matrix["rows"] = (matrix.get("rows") or [])[:12]
        raw2, err2, available2 = _call_llm(
            _prompt(trim_evidence, trim_matrix, chain, b_track))
        if err2 or not available2:
            return _degrade(pp, result,
                            err2 or "LLM 不可用，已降级为 SKIP（不编造结论）")
        out = _parse_json(raw2)
        if out is None:
            return _degrade(pp, result,
                            "LLM 返回无法解析为 JSON（含紧凑重试），"
                            "已降级为 SKIP（不编造结论）")
        out.setdefault("assumptions", []).append({
            "text": "LLM 语义确认仅覆盖前 12 个能力，其余能力保留确定性矩阵结果",
            "evidence_level": "E4",
        })
        extra = dict(extra or {})
        extra["llm_scope"] = "compact_retry:12"
    bad = _validate_llm(out)
    if bad:
        return _degrade(pp, result, bad)

    merged = _merge_llm(out, evidence, matrix, chain, b_track, extra, pp)
    write_result(pp, merged)
    return merged
