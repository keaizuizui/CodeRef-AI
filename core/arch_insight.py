# -*- coding: utf-8 -*-
"""
架构洞察（，v5.3.0）：管线梳理 / 真身判定 / 重复识别 —— 人话结构化结论

背景（测试 ，P0 级）：coderef_architecture 报告此前只是"790B 壳"（项目/文件/行数
+ 一行 HTML 画布路径），给不出业务管线流向、子系统真身/入口、重复实现簇这类可读结论，
测试被迫人工 grep 完成梳理。

本模块以纯静态、确定性方式（复用知识图谱 CALLS 边 + FlowVerifier）自动产出三段人话结论：
- P0-A 管线梳理：自动发现入口，沿 CALLS 归纳阶段序管线（x→y→z 带文件/行号/说明），
  输出 Markdown 表格；另附跨模块业务数据流。
- P0-B 真身/入口判定：同名多目录实现（如 check_plan_coverage 同时存在于多个子系统），
  报告各副本被谁引用 / 是否活跃 / 哪个是生产入口候选。
- P0-C 重复/同构识别：同名函数跨模块实现，标记重复实现簇。

LLM 可选：use_llm=True 且配置了 API Key 时，对三段静态结果生成一段"人话总结"；
未配置时静态结果完整可用，不降级。

用法：
    from core.arch_insight import insight_markdown
    md = insight_markdown(project_path="d:/x/proj", use_llm=False)
"""

import os
import re
from collections import defaultdict
from typing import Dict, List, Optional

from loguru import logger


# ═══════════════════════════════════════════════════════════════════
# 公共：图谱就绪 + FlowVerifier
# ═══════════════════════════════════════════════════════════════════

def _verifier(project_path: str, db_path: Optional[str] = None):
    """确保图谱就绪并返回 FlowVerifier；图谱不可用返回 None（调用方诚实降级）。"""
    from core.flow_verify import FlowVerifier, ensure_kg
    db = ensure_kg(project_path, db_path)
    if not os.path.isfile(db):
        logger.warning(f"[arch_insight] 知识图谱不存在: {db}，洞察跳过")
        return None
    try:
        return FlowVerifier(db)
    except Exception as e:  # pragma: no cover
        logger.warning(f"[arch_insight] 加载图谱失败: {e}")
        return None


def _mod_of(fp: str) -> str:
    """文件所在目录名（模块归属），兼容相对/绝对路径。"""
    d = os.path.dirname(os.path.normpath(fp or ""))
    return os.path.basename(d) or ""


def _abs_path(project_path: str, fp: str) -> str:
    """图谱节点 file_path 可能是相对路径，拼回绝对路径读取源码。"""
    if not fp:
        return ""
    if os.path.isabs(fp):
        return fp
    return os.path.join(project_path, fp)


def _norm_body(fp: str, start: int, end: int) -> str:
    """读取并规范化函数体源码（去注释/字符串/空白），供相似度比较。"""
    try:
        with open(fp, encoding="utf-8", errors="ignore") as f:
            lines = f.readlines()
        body = "".join(lines[start - 1:end])
    except Exception:
        return ""
    body = re.sub(r"#.*", "", body)
    body = re.sub(r'"[^"]*"|\'[^\']*\'', "", body)
    body = re.sub(r"\s+", "", body)
    return body


def _jaccard(a: str, b: str) -> float:
    """字符 bigram 集合 Jaccard 相似度（0~1）。"""
    if not a or not b:
        return 0.0
    sa = {a[i:i + 2] for i in range(len(a) - 1)}
    sb = {b[i:i + 2] for i in range(len(b) - 1)}
    if not sa and not sb:
        return 1.0
    union = sa | sb
    return len(sa & sb) / len(union) if union else 0.0


# ═══════════════════════════════════════════════════════════════════
# P0-A 管线梳理
# ═══════════════════════════════════════════════════════════════════

_ENTRY_HINTS = ("main", "run", "start", "entry", "index", "app", "serve", "launch")


def _entry_score(name: str) -> int:
    """入口启发式评分：main/run/start 等典型入口名优先。"""
    nl = name.lower()
    if nl in _ENTRY_HINTS:
        return 4
    if any(k in nl for k in _ENTRY_HINTS):
        return 2
    return 0


def pipeline_insight(project_path: str, db_path: Optional[str] = None,
                     max_entries: int = 6, max_depth: int = 6) -> Dict:
    """P0-A：管线自动梳理。

    返回 {"entries": [{entry, steps:[{name,file,line,doc}]}], "flows": [{source,target,funcs,count}]}。
    入口自动发现：无被调用方的函数（root）+ 启发式排序；每入口沿 CALLS 下游归纳阶段序。
    """
    fv = _verifier(project_path, db_path)
    if fv is None:
        return {"ok": False, "entries": [], "flows": []}

    roots = fv.root_functions()
    ranked = sorted(roots, key=_entry_score, reverse=True)

    entries = []
    for spec in ranked[:max_entries]:
        chain = fv.entry_chain(spec, max_depth=max_depth)
        if len(chain) >= 2:  # 至少 2 步才算管线
            entries.append({"entry": spec, "steps": chain})

    flows = fv.cross_module_flows()
    return {"ok": True, "entries": entries, "flows": flows[:20]}


def _pipeline_markdown(data: Dict) -> str:
    lines = ["## 🧭 管线梳理（P0-A）"]
    if not data.get("ok"):
        lines.append("> 知识图谱不可用，管线梳理跳过。")
        return "\n".join(lines) + "\n"

    entries = data.get("entries") or []
    flows = data.get("flows") or []
    if not entries and not flows:
        lines.append("> 未发现可归纳的管线（无 ≥2 步的入口调用链，也无跨模块数据流）。")
        return "\n".join(lines) + "\n"

    if entries:
        lines.append("### 入口管线（沿 CALLS 归纳阶段序）")
        for e in entries:
            lines.append(f"**入口 `{e['entry']}`**（{len(e['steps'])} 步）")
            lines.append("| 阶段 | 符号 | 文件 | 行 | 说明 |")
            lines.append("|------|------|------|----|------|")
            for i, s in enumerate(e["steps"], 1):
                doc = (s.get("doc") or "").replace("\n", " ").strip()[:60]
                lines.append(f"| {i} | `{s['name']}` | `{s['file']}` | {s.get('line', 0)} | {doc} |")
            lines.append("")

    if flows:
        lines.append("### 跨模块业务数据流")
        lines.append("| 源模块 | 目标模块 | 调用函数 | 次数 |")
        lines.append("|--------|----------|----------|------|")
        for f in flows[:20]:
            funcs = ", ".join(f"`{x}`" for x in (f.get("funcs") or [])[:5])
            lines.append(f"| `{f['source']}` | `{f['target']}` | {funcs} | {f.get('count', 0)} |")
        lines.append("")

    return "\n".join(lines) + "\n"


# ═══════════════════════════════════════════════════════════════════
# P0-B 真身/入口判定
# ═══════════════════════════════════════════════════════════════════

def identity_insight(project_path: str, db_path: Optional[str] = None,
                     max_items: int = 20) -> Dict:
    """P0-B：真身/入口判定。

    同名多目录实现（>1 个文件），对每个副本统计 inbound CALLS（被谁引用）、活跃度，
    判定哪个是生产入口候选。返回 {"ok", "items":[{name, copies:[{file,line,callers,active}]}]}。
    """
    fv = _verifier(project_path, db_path)
    if fv is None:
        return {"ok": False, "items": []}

    by_name: Dict[str, List[str]] = defaultdict(list)
    for nid, n in fv.nodes.items():
        if n.get("type") not in ("function", "method"):
            continue
        by_name[n["name"].split(".")[-1]].append(nid)

    items = []
    for name, ids in by_name.items():
        if len(ids) < 2:
            continue
        copies = []
        for nid in ids:
            n = fv.nodes[nid]
            inbound = [src for src, tgts in fv.adj.items() if nid in tgts]
            copies.append({
                "id": nid,
                "file": (n.get("file_path") or "").replace("\\", "/"),
                "line": n.get("start_line", 0),
                "mod": _mod_of(n.get("file_path") or ""),
                "callers": len(inbound),
                "active": len(inbound) > 0,
                "is_root": len(inbound) == 0,
            })
        copies.sort(key=lambda c: (-c["callers"], c["file"]))
        roots = [c for c in copies if c["is_root"]]
        # 判定：生产入口通常无图内调用者（root），故仅 root 副本可标"生产入口候选"；
        # dunder 特殊方法（__init__/__repr__ 等）即使无调用者也不是业务入口，单独标注；
        # 无 root 副本时，被引用最多者只标"被引用最多的副本"（事实陈述，不推断入口）。
        is_dunder = name.startswith("__") and name.endswith("__")
        for c in copies:
            if c["is_root"]:
                if is_dunder:
                    c["verdict"] = "特殊方法（无被调用者）"
                else:
                    c["verdict"] = "生产入口候选（无被调用者）"
            elif c["callers"] > 0:
                c["verdict"] = "活跃副本"
            else:
                c["verdict"] = "无引用（死/备选）"
        if not roots and copies and copies[0]["callers"] > 0:
            copies[0]["verdict"] = "被引用最多的副本"
        items.append({"name": name, "copies": copies})

    items.sort(key=lambda it: -len(it["copies"]))
    return {"ok": True, "items": items[:max_items]}


def _identity_markdown(data: Dict) -> str:
    lines = ["## 🎯 真身/入口判定（P0-B）"]
    if not data.get("ok"):
        lines.append("> 知识图谱不可用，真身判定跳过。")
        return "\n".join(lines) + "\n"

    items = data.get("items") or []
    if not items:
        lines.append("> 未发现同名多目录实现（每个符号仅一处定义）。")
        return "\n".join(lines) + "\n"

    for it in items:
        lines.append(f"**`{it['name']}`**（{len(it['copies'])} 处实现）")
        lines.append("| 判定 | 模块 | 文件 | 行 | 被引用 |")
        lines.append("|------|------|------|----|--------|")
        for c in it["copies"]:
            lines.append(f"| {c['verdict']} | `{c['mod']}` | `{c['file']}` | {c['line']} | {c['callers']} |")
        lines.append("")

    return "\n".join(lines) + "\n"


# ═══════════════════════════════════════════════════════════════════
# P0-C 重复/同构识别
# ═══════════════════════════════════════════════════════════════════

def duplicate_insight(project_path: str, db_path: Optional[str] = None,
                      max_clusters: int = 20, sim_threshold: float = 0.6) -> Dict:
    """P0-C：重复/同构识别。

    同名函数/方法跨模块（不同目录）实现 → 先按函数体相似度区分：
    - 相似度 ≥ sim_threshold → "重复实现簇"（kind=duplicate，推荐收敛）
    - 相似度 < sim_threshold → "同名候选"（kind=candidate，仅同名、契约可能不同，不推荐合并）
    返回 {"ok", "clusters":[{name, kind, max_sim, copies:[{file,line,mod}]}]}。
    """
    fv = _verifier(project_path, db_path)
    if fv is None:
        return {"ok": False, "clusters": []}

    by_name: Dict[str, List[str]] = defaultdict(list)
    for nid, n in fv.nodes.items():
        if n.get("type") not in ("function", "method"):
            continue
        by_name[n["name"].split(".")[-1]].append(nid)

    clusters = []
    for name, ids in by_name.items():
        if len(ids) < 2:
            continue
        copies = []
        mods = set()
        for nid in ids:
            n = fv.nodes[nid]
            fp = (n.get("file_path") or "").replace("\\", "/")
            mod = _mod_of(n.get("file_path") or "")
            copies.append({
                "file": fp, "line": n.get("start_line", 0), "mod": mod,
                "body": _norm_body(_abs_path(project_path, fp),
                                   n.get("start_line", 0), n.get("end_line", 0)),
            })
            mods.add(mod)
        if len(mods) < 2:  # 跨模块才算"重复/同名候选"
            continue
        # 簇内两两函数体相似度，取最大值作为簇相似度
        max_sim = 0.0
        for i in range(len(copies)):
            for j in range(i + 1, len(copies)):
                s = _jaccard(copies[i]["body"], copies[j]["body"])
                if s > max_sim:
                    max_sim = s
        kind = "duplicate" if max_sim >= sim_threshold else "candidate"
        for c in copies:
            c.pop("body", None)
        clusters.append({"name": name, "kind": kind, "max_sim": round(max_sim, 2),
                         "copies": copies})

    clusters.sort(key=lambda c: (-len(c["copies"]), c["kind"] != "duplicate"))
    return {"ok": True, "clusters": clusters[:max_clusters]}


def _duplicate_markdown(data: Dict) -> str:
    lines = ["## 🔍 重复/同构识别（P0-C）"]
    if not data.get("ok"):
        lines.append("> 知识图谱不可用，重复识别跳过。")
        return "\n".join(lines) + "\n"

    clusters = data.get("clusters") or []
    if not clusters:
        lines.append("> 未发现跨模块同名实现（每个符号仅一处定义）。")
        return "\n".join(lines) + "\n"

    dup = [c for c in clusters if c.get("kind") == "duplicate"]
    cand = [c for c in clusters if c.get("kind") != "duplicate"]
    if dup:
        lines.append(f"### 重复实现簇（函数体相似度 ≥ 60%，建议收敛）")
        lines.append("| 符号 | 实现数 | 相似度 | 模块 | 文件:行 |")
        lines.append("|------|--------|--------|------|---------|")
        for c in dup:
            first = c["copies"][0]
            rest = c["copies"][1:]
            mods = "、".join(f"`{x['mod']}`" for x in c["copies"])
            locs = f"`{first['file']}:{first['line']}`"
            if rest:
                locs += " 等 " + "、".join(f"`{x['file']}:{x['line']}`" for x in rest[:4])
            lines.append(f"| `{c['name']}` | {len(c['copies'])} | {c.get('max_sim', 0)} | {mods} | {locs} |")
        lines.append("")
    if cand:
        lines.append("### 同名候选（仅同名、契约可能不同，不推荐合并）")
        lines.append("| 符号 | 实现数 | 相似度 | 模块 |")
        lines.append("|------|--------|--------|------|")
        for c in cand:
            mods = "、".join(f"`{x['mod']}`" for x in c["copies"])
            lines.append(f"| `{c['name']}` | {len(c['copies'])} | {c.get('max_sim', 0)} | {mods} |")
        lines.append("")

    return "\n".join(lines) + "\n"


# ═══════════════════════════════════════════════════════════════════
# LLM 可选：人话总结
# ═══════════════════════════════════════════════════════════════════

def _llm_summary(project_path: str, data: Dict) -> str:
    """对三段静态结果生成一段人话总结；LLM 不可用返回空串（不降级静态结果）。"""
    try:
        from core.llm_integration import LLMIntegration
        llm = LLMIntegration()
        if not llm.is_available():
            return ""
        n_entries = len(data.get("pipeline", {}).get("entries", []))
        n_flows = len(data.get("pipeline", {}).get("flows", []))
        n_identity = len(data.get("identity", {}).get("items", []))
        n_dup = len(data.get("duplicate", {}).get("clusters", []))
        prompt = (
            f"项目 {project_path} 的架构静态洞察：\n"
            f"- 管线梳理：{n_entries} 条入口管线，{n_flows} 条跨模块数据流\n"
            f"- 真身判定：{n_identity} 组同名多实现\n"
            f"- 重复识别：{n_dup} 组跨模块重复实现簇\n"
            f"请用 3-5 句话概括该项目的架构健康状况与治理重点（通俗中文，面向治理屎山代码的工程师）。"
        )
        return llm.chat_completion([
            {"role": "system", "content": "你是一位资深架构治理工程师，请用通俗中文给出简洁的架构洞察总结。"},
            {"role": "user", "content": prompt},
        ]).strip()
    except Exception as e:  # pragma: no cover
        logger.warning(f"[arch_insight] LLM 总结失败: {e}")
        return ""


# ═══════════════════════════════════════════════════════════════════
# 组合入口
# ═══════════════════════════════════════════════════════════════════

def insight_markdown(project_path: str, db_path: Optional[str] = None,
                     use_llm: bool = False) -> str:
    """生成  三段洞察 Markdown（管线/真身/重复），供 architecture 报告追加。

    静态结果始终完整产出；use_llm=True 且 LLM 可用时追加一段人话总结。
    """
    data = {
        "pipeline": pipeline_insight(project_path, db_path),
        "identity": identity_insight(project_path, db_path),
        "duplicate": duplicate_insight(project_path, db_path),
    }
    parts = [
        _pipeline_markdown(data["pipeline"]),
        _identity_markdown(data["identity"]),
        _duplicate_markdown(data["duplicate"]),
    ]
    if use_llm:
        summary = _llm_summary(project_path, data)
        if summary:
            parts.insert(0, f"## 💬 架构洞察总结（LLM）\n\n{summary}\n")
    return "\n".join(parts).strip()


def main() -> None:  # pragma: no cover
    """CLI 冒烟：python -m core.arch_insight <project_path> [--llm]"""
    import argparse
    ap = argparse.ArgumentParser(description="架构洞察（管线/真身/重复）")
    ap.add_argument("project_path")
    ap.add_argument("--llm", action="store_true", help="启用 LLM 总结")
    args = ap.parse_args()
    print(insight_markdown(args.project_path, use_llm=args.llm))


if __name__ == "__main__":  # pragma: no cover
    main()
