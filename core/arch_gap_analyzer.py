# -*- coding: utf-8 -*-
"""
arch_gap_analyzer — 架构差距分析器（5.0 Phase 0 核心）

输入：现状知识图谱 + 目标架构 JSON
输出：结构化差距清单（确定性，不依赖 LLM）

差距类型：
  missing               职责缺失：目标角色声明的 target_modules 在项目中不存在
  dependency_violation  依赖违例：模块依赖违反角色间约束（constraints）
  cycle                 循环依赖：复用 arch_audit 模块级 SCC 检测
  business_gap          业务断链：业务步骤关联的所有角色都无有效代码实现
  unassigned            游离模块：代码模块不在任何角色的 target_modules 中
  god_module            上帝模块：复用 arch_audit 扇出/扇入+规模判定
  large_module          异常规模：复用 arch_audit 符号数阈值
  duplicate             同构重复（孪生）：同名实现跨目录高相似度，复用 arch_insight P0-C
  directory_duplicate   目录级重复：整个目录与其他目录同构，复用 arch_insight P0-C

设计原则：
- 确定性优先：全部来自静态图谱，不依赖 LLM。
- 复用不重写：cycle/god_module/large_module 直接调用 arch_audit.audit()。
- 模块名匹配：相对路径精确匹配优先，basename 宽松匹配兜底。
"""

import os
from collections import Counter, defaultdict
from typing import Any, Dict, List, Optional, Set

from core.arch_audit import (
    audit as arch_audit,
    build_module_graph,
    locate_kg_db,
    module_of,
)
from core.arch_insight import duplicate_insight
from core.graph_closure import load_graph

# 各差距类型严重级
SEVERITY = {
    "missing": "high",
    "dependency_violation": "high",
    "cycle": "high",
    "business_gap": "high",
    "unassigned": "medium",
    "god_module": "medium",
    "large_module": "low",
    "duplicate": "medium",
    "directory_duplicate": "medium",
    "twin_identity": "medium",
}

# 游离模块默认报出上限（避免刷屏淹没 high 级差距）
DEFAULT_MAX_UNASSIGNED = 50


def _is_exempt_module(module_name: str) -> bool:
    """判断模块是否属于治理豁免噪声：vendor / 压缩产物 / __init__ / dist / build。

    CodeRabbit 评审修订：按**完整路径段精确匹配**（而非子串包含）——避免真实现模块
    如 src/vendor_management.py、src/rebuild_tasks.py 因名称含 vendor/build 被误豁免漏掉。

    规则：
      - 目录段精确命中 {vendor, node_modules, dist, build} 之一 → 豁免
      - 文件名（basename）为 __init__ / __init__.py → 豁免
      - 文件名以 .min.js / .min.css 结尾 → 豁免
    豁免仅作用于游离报出与治理库排队，不改变知识图谱构建与统计口径。
    """
    parts = [p.lower() for p in (module_name or "").replace("\\", "/").split("/") if p]
    if not parts:
        return False
    base = parts[-1]
    if base.endswith((".min.js", ".min.css")):
        return True
    return (
        bool({"vendor", "node_modules", "dist", "build"} & set(parts))
        or base in {"__init__", "__init__.py"}
    )


def _is_test_module(module_name: str) -> bool:
    """判断模块是否属于测试代码（架构对齐的目标是生产代码，测试默认排除）。

    判定规则：相对路径含 /tests/ 或 tests/ 开头；模块名以 test_ 开头或 _test 结尾。
    """
    m = (module_name or "").replace("\\", "/")
    if "/tests/" in m or m.startswith("tests/"):
        return True
    base = m.split("/")[-1]
    return base.startswith("test_") or base.endswith("_test")


def _norm_spec(spec: str) -> str:
    """规范化目标模块路径：正斜杠、去 .py 扩展名。"""
    s = (spec or "").strip().replace("\\", "/")
    if s.endswith(".py"):
        s = s[:-3]
    return s


def _match_module_ids(nodes: Dict[str, dict], project_path: str,
                      specs: List[str]) -> Set[str]:
    """把 target_modules specs 匹配到知识图谱 mod 节点 id 集合。

    匹配规则：相对路径精确匹配优先（module_of 结果），basename 宽松匹配兜底。
    """
    matched: Set[str] = set()
    for spec in specs:
        ns = _norm_spec(spec)
        if not ns:
            continue
        base = ns.split("/")[-1]
        for nid, n in nodes.items():
            if n.get("type") != "module":
                continue
            if module_of(n, project_path) == ns or n.get("name") == base:
                matched.add(nid)
    return matched


def _module_exists(project_path: str, spec: str, nodes: Dict[str, dict]) -> bool:
    """判断目标模块 spec 在项目中是否有实现。

    判定依据：文件系统存在（project_path/spec.py 或目录）或知识图谱已有匹配模块。
    """
    ns = _norm_spec(spec)
    if not ns:
        return False
    for cand in (ns + ".py", ns, ns + "/__init__.py"):
        p = os.path.join(project_path, cand.replace("/", os.sep))
        if os.path.isfile(p) or os.path.isdir(p):
            return True
    for nid, n in nodes.items():
        if n.get("type") != "module" and n.get("type") != "go_func":
            continue
        if module_of(n, project_path) == ns or n.get("name") == ns.split("/")[-1]:
            return True
    return False


def _detect_missing(roles: List[dict], project_path: str,
                    nodes: Dict[str, dict]) -> List[dict]:
    """职责缺失：角色声明的 target_modules 在项目中不存在。"""
    gaps = []
    for role in roles:
        rid = role.get("id", "")
        rname = role.get("name", rid)
        for spec in role.get("target_modules", []):
            if not _module_exists(project_path, spec, nodes):
                gaps.append({
                    "type": "missing",
                    "severity": SEVERITY["missing"],
                    "role_id": rid,
                    "role_name": rname,
                    "module": _norm_spec(spec),
                    "detail": f"目标角色 '{rname}' 声明的模块 {_norm_spec(spec)} 在项目中不存在",
                })
    return gaps


def _detect_unassigned(nodes: Dict[str, dict], adj: Dict[str, List[str]],
                       project_path: str, assigned_ids: Set[str],
                       max_n: int) -> tuple:
    """游离模块：不在任何角色 target_modules 中的代码模块（建议书 P0③ 增强）。

    区分两类：
      - monitored=free（真游离）：模块无任何调用者（fan_in=0），代码孤儿，治理候选，
        排在最前。
      - monitored=unmodeled（未建模）：模块被真实调用（跨模块 fan_in>0）但 target_modules
        未覆盖——本质是「目标架构覆盖不足」而非真游离，排在 free 之后；说明文案引导
        去 define-target 补 target_modules，而非当成孤儿删除。

    豁免噪声（vendor/*.min.js/__init__/dist/build 等）自动排除，避免刷屏淹没真游离。

    返回 (报出的差距列表, 游离模块总数, free 计数, unmodeled 计数)。
    """
    # 模块 → 跨模块被调次数（fan_in 近似）：用 CALLS 边被调侧统计
    called_mods: Dict[str, int] = {}
    for src, targets in adj.items():
        src_n = nodes.get(src, {})
        src_mod = module_of(src_n, project_path) if src_n.get("type") != "module" else (src_n.get("name") or "")
        for t in targets:
            t_n = nodes.get(t, {})
            t_mod = module_of(t_n, project_path) if t_n.get("type") != "module" else (t_n.get("name") or "")
            if not src_mod or not t_mod or src_mod == t_mod:
                continue
            called_mods[t_mod] = called_mods.get(t_mod, 0) + 1

    unassigned = []
    for nid, n in nodes.items():
        if n.get("type") != "module":
            continue
        if nid in assigned_ids:
            continue
        m = module_of(n, project_path) or n.get("name", "?")
        # 测试模块默认排除（对齐目标是生产代码）
        if _is_test_module(m):
            continue
        # 豁免噪声：vendor / 压缩产物 / __init__ / dist / build
        if _is_exempt_module(m):
            continue
        fan_in = called_mods.get(m, 0)
        unassigned.append({
            "module": m,
            "file_path": n.get("file_path", ""),
            "fan_in": fan_in,
            "monitored": "free" if fan_in == 0 else "unmodeled",
        })
    unassigned.sort(key=lambda x: (x["monitored"] != "free", x["module"]))
    total = len(unassigned)
    free_cnt = sum(1 for u in unassigned if u["monitored"] == "free")
    unmodeled_cnt = total - free_cnt
    shown = unassigned[:max_n]
    gaps = []
    for u in shown:
        if u["monitored"] == "free":
            detail = f"代码模块 {u['module']} 无任何调用者（fan_in=0），真游离，治理候选"
        else:
            detail = (f"代码模块 {u['module']} 被调用（fan_in={u['fan_in']}）但 target_modules "
                      f"未覆盖——属「未建模」而非真游离，请在 define-target 补 target_modules")
        gaps.append({
            "type": "unassigned",
            "severity": SEVERITY["unassigned"],
            "module": u["module"],
            "monitored": u["monitored"],
            "fan_in": u["fan_in"],
            "detail": detail,
        })
    return gaps, total, free_cnt, unmodeled_cnt


def _detect_dependency_violations(nodes: Dict[str, dict],
                                  adj: Dict[str, List[str]],
                                  project_path: str,
                                  role_of: Dict[str, str],
                                  constraints: List[dict]) -> List[dict]:
    """依赖违例：模块依赖违反角色间约束（constraints 的 no_dependency）。"""
    mod_adj, _ = build_module_graph(nodes, adj, project_path)
    # 模块 → 角色映射（用 module_of 相对路径结果）
    mod_role: Dict[str, str] = {}
    for nid, n in nodes.items():
        if n.get("type") != "module":
            continue
        m = module_of(n, project_path)
        rid = role_of.get(nid)
        if m and rid:
            mod_role[m] = rid
    # 约束 → 禁止依赖方向 {from_role: {to_role}}
    forbidden: Dict[str, Set[str]] = {}
    for c in constraints:
        if c.get("rule") == "no_dependency":
            forbidden.setdefault(c.get("from", ""), set()).add(c.get("to", ""))
    gaps = []
    for src, targets in mod_adj.items():
        rs = mod_role.get(src)
        if not rs:
            continue
        for tgt in targets:
            rt = mod_role.get(tgt)
            if rt and rt in forbidden.get(rs, set()):
                gaps.append({
                    "type": "dependency_violation",
                    "severity": SEVERITY["dependency_violation"],
                    "from_module": src,
                    "to_module": tgt,
                    "from_role": rs,
                    "to_role": rt,
                    "detail": (f"模块 {src}（角色 {rs}）依赖模块 {tgt}"
                               f"（角色 {rt}），违反约束 {rs}→{rt} no_dependency"),
                })
    return gaps


def _detect_business_gaps(flows: List[dict],
                          role_has_impl: Dict[str, bool]) -> List[dict]:
    """业务断链：业务步骤关联的所有角色都无有效代码实现。"""
    gaps = []
    for flow in flows:
        fid = flow.get("id", "")
        for step in flow.get("steps", []):
            roles = step.get("tech_roles") or []
            if not roles:
                continue
            valid = [r for r in roles if role_has_impl.get(r)]
            if not valid:
                gaps.append({
                    "type": "business_gap",
                    "severity": SEVERITY["business_gap"],
                    "flow_id": fid,
                    "step_id": step.get("id", ""),
                    "step_name": step.get("name", ""),
                    "roles": roles,
                    "detail": (f"业务步骤 '{step.get('name', '')}' 关联的角色 "
                               f"{roles} 均无有效代码实现"),
                })
    return gaps


def _detect_duplicates(project_path: str, db_path: str,
                       parts: Dict[str, bool],
                       ds: Optional[dict] = None) -> List[dict]:
    """同构重复 / 目录级重复差距（建议书 P0①，复用 arch_insight P0-C）。

    复用 duplicate_insight() 的同一切词/相似度/通用名过滤逻辑，避免重复实现：
      - clusters[kind=duplicate]（函数体相似度 ≥60% 的跨模块同构孪生）→ type=duplicate
      - dir_isomorph（整个目录与其他目录同构）→ type=directory_duplicate
    默认与 architecture 同口径（跨目录才算重复），过滤通用方法名噪音与测试文件。

    parts 控制是否纳入：{"duplicate": True, "directory_duplicate": True}，
    供调用方按需裁剪（不影响图谱与确定性）。

    ds 可选：已调用的 duplicate_insight 结果（供孪生判定复用，避免重复计算）。
    """
    if ds is None:
        ds = duplicate_insight(project_path, db_path=db_path)
    if not ds.get("ok"):
        return []

    gaps: List[dict] = []
    # 1) 同构重复簇（跨模块同名高相似度实现）
    if parts.get("duplicate", True):
        for c in ds.get("clusters") or []:
            if c.get("kind") != "duplicate":
                continue
            copies = c.get("copies") or []
            mods = ",".join(sorted({cp.get("mod", "?") for cp in copies}))
            locs = [f"{cp.get('mod','?')}:{cp.get('file','')}:{cp.get('line',0)}"
                    for cp in copies]
            gaps.append({
                "type": "duplicate",
                "severity": SEVERITY["duplicate"],
                "symbol": c.get("name", ""),
                "copies": locs,
                "similarity": c.get("max_sim", 0.0),
                "detail": (f"同构重复（孪生）: 符号 {c.get('name','')} 在 {mods} 跨目录重复实现，"
                           f"相似度 {c.get('max_sim', 0.0):.0%}（建议收敛）"),
            })
    # 2) 目录级重复（整个目录与其他目录同构）
    if parts.get("directory_duplicate", True):
        for iso in ds.get("dir_isomorph") or []:
            gaps.append({
                "type": "directory_duplicate",
                "severity": SEVERITY["directory_duplicate"],
                "dir_a": iso.get("dir_a") or "",
                "dir_b": iso.get("dir_b") or "",
                "file_sim": iso.get("file_sim", 0.0),
                "func_sim": iso.get("func_sim", 0.0),
                "detail": (f"目录级重复: {iso.get('dir_a','?')} 与 {iso.get('dir_b','?')} 目录同构，"
                           f"文件相似度 {iso.get('file_sim',0.0):.0%}、"
                           f"函数签名相似度 {iso.get('func_sim',0.0):.0%}（建议收敛合并）"),
            })
    return gaps


def _detect_twin_identity(nodes: Dict[str, dict], adj: Dict[str, List[str]],
                          project_path: str,
                          ds: Optional[dict] = None) -> List[dict]:
    """孪生模块真身/孤本标注（③）：目录级同构对中同名模块按 fan_in 判真身/孤本。

    复用 duplicate_insight 的 dir_isomorph（目录级同构）识别孪生目录对
    （目录 A 有同名子模块清单、目录 B 也有一份，即目录级同构），对每对目录中
    basename 相同的模块按跨模块 fan_in 标注：
      - 真身：fan_in 最高且 >0（活跃实现，治理保留）
      - 孤本：fan_in=0（无调用者，治理收敛候选）
      - 活跃副本：fan_in>0 但非最高（被部分调用，待收编）
    仅报"有真身且有孤本"的组（有收敛价值），避免把无关同名模块误报。

    ds 可选：已调用的 duplicate_insight 结果（复用 dir_isomorph）。
    """
    if ds is None:
        ds = duplicate_insight(project_path)
    isos = ds.get("dir_isomorph") or []
    if not isos:
        return []

    # 模块级跨模块 fan_in（与 _detect_unassigned 同口径）
    called_mods: Dict[str, int] = {}
    for src, targets in adj.items():
        src_n = nodes.get(src, {})
        src_mod = module_of(src_n, project_path) if src_n.get("type") != "module" else (src_n.get("name") or "")
        for t in targets:
            t_n = nodes.get(t, {})
            t_mod = module_of(t_n, project_path) if t_n.get("type") != "module" else (t_n.get("name") or "")
            if not src_mod or not t_mod or src_mod == t_mod:
                continue
            called_mods[t_mod] = called_mods.get(t_mod, 0) + 1

    mods: Set[str] = set()
    for nid, n in nodes.items():
        if n.get("type") != "module":
            continue
        m = module_of(n, project_path) or n.get("name", "?")
        if not _is_test_module(m):
            mods.add(m)

    results: List[dict] = []
    for iso in isos:
        da, db_ = iso.get("dir_a", ""), iso.get("dir_b", "")
        if not da or not db_:
            continue
        a_mods = sorted(m for m in mods if m == da or m.startswith(da + "/"))
        b_mods = sorted(m for m in mods if m == db_ or m.startswith(db_ + "/"))
        a_by_base = {m.split("/")[-1]: m for m in a_mods}
        b_by_base = {m.split("/")[-1]: m for m in b_mods}
        common = sorted(set(a_by_base) & set(b_by_base))
        if not common:
            continue
        copies: List[dict] = []
        for base in common:
            ma, mb = a_by_base[base], b_by_base[base]
            fa, fb = called_mods.get(ma, 0), called_mods.get(mb, 0)
            if fa == 0 and fb == 0:
                continue  # 双方都无调用者，无真身可判
            if fa > fb:
                va, vb = "真身", ("孤本" if fb == 0 else "活跃副本")
            elif fb > fa:
                va, vb = ("孤本" if fa == 0 else "活跃副本"), "真身"
            else:
                va, vb = "活跃副本", "活跃副本"
            copies.append({"module": ma, "fan_in": fa, "verdict": va})
            copies.append({"module": mb, "fan_in": fb, "verdict": vb})
        has_true = any(c["verdict"] == "真身" for c in copies)
        has_orphan = any(c["verdict"] == "孤本" for c in copies)
        if has_true and has_orphan:
            results.append({
                "dir_a": da, "dir_b": db_,
                "file_sim": iso.get("file_sim", 0.0),
                "func_sim": iso.get("func_sim", 0.0),
                "copies": copies,
            })
    return results


def _domain_flow_model(nodes: Dict[str, dict], adj: Dict[str, List[str]],
                       project_path: str, scope: Optional[dict] = None) -> Dict[str, Any]:
    """域间业务流量透视（②深化）：向 define-target 提供真实业务主干，且零项目名硬编码。

    '谁是可清理的技术底座'是项目语义（working 中 gptr_service 与真实业务终点
    创业咨询在纯调用拓扑上同构），工具不擅自下结论，仅做**可解释的结构透视**，
    把需要项目语义的排除经 `business_flow.scope` 配置注入。

    三层输出：
      - edges          如实层：全部跨域调用（仅剔自环/测试/纯叶子接收域），保留权重与证据
      - hubs           结构层：逐域标 in_src_count / biz_out_count / role（共享层/双向枢纽/
                       被共同依赖/业务编排源/纯上游入口/叶子/待定），全程无项目名
      - shared_layers  动态判定的共享层（被 ≥50% 源域引用的域，如各项目的公共依赖层）
      - suggestions    业务骨架层：去掉共享层源/目标 + 叶子 + scope 排除后，按调用量排序的主干
                       跨域对（调用数 ≥ 3），供 define-target 校验是否枚举真实主干业务流

    scope（可选，默认空）：{"exclude_domains": ["gptr_service"], "exclude_suffixes": [".coderef"]}
      exclude_domains   精确排除的域（项目语义：项目自认为是技术底座/噪声的域）
      exclude_suffixes  按域名字后缀排除（如 ".coderef" 内部目录、"_service" 服务层）
    """

    mod_adj: Dict[str, Set[str]] = defaultdict(set)
    for src, targets in adj.items():
        src_n = nodes.get(src, {})
        sm = module_of(src_n, project_path) if src_n.get("type") != "module" else (src_n.get("name") or "")
        if not sm or _is_test_module(sm):
            continue
        for t in targets:
            t_n = nodes.get(t, {})
            tm = module_of(t_n, project_path) if t_n.get("type") != "module" else (t_n.get("name") or "")
            if not tm or tm == sm or _is_test_module(tm):
                continue
            mod_adj[sm].add(tm)

    cross: Counter = Counter()
    evidence: Dict[tuple, List[str]] = defaultdict(list)
    in_src: Dict[str, Set[str]] = defaultdict(set)
    out_tgt: Dict[str, Set[str]] = defaultdict(set)
    source_domains: Set[str] = set()
    for sm, tgts in mod_adj.items():
        d1 = sm.split("/")[0]
        source_domains.add(d1)
        for tm in tgts:
            d2 = tm.split("/")[0]
            if d1 == d2:
                continue
            cross[(d1, d2)] += 1
            in_src[d2].add(d1)
            out_tgt[d1].add(d2)
            if len(evidence[(d1, d2)]) < 5:
                evidence[(d1, d2)].append(f"{sm} → {tm}")

    total_src = len(source_domains)

    # 共享层：被 ≥ 50% 不同源域引用的域（被几乎所有业务共用，通常是公共依赖层）
    shared_layers = sorted(
        d for d, srcs in in_src.items() if total_src and len(srcs) / total_src >= 0.5)

    def biz_out(d: str) -> Set[str]:
        if d in shared_layers:
            return set()
        return {t for t in out_tgt.get(d, set()) if t != d}

    # 角色分派（in/out 二维，可解释，无项目名）
    def role_of(d: str) -> str:
        ni = len(in_src.get(d, set()))
        no = len(biz_out(d))
        if d in shared_layers:
            return "共享层"
        if no == 0:
            return "被共同依赖" if ni > 0 else "叶子"
        if ni == 0:
            return "纯上游入口"
        if ni >= 6 and no >= 3:
            return "双向枢纽"
        if no >= 3:
            return "业务编排源"
        return "待定"

    # ── edges（如实层）：仅剔"目标域无跨域出边"的纯叶子（不删 shared 作目标的真实依赖）──
    edges = []
    for (d1, d2), cnt in cross.most_common():
        if out_tgt.get(d2):
            edges.append({"from_domain": d1, "to_domain": d2, "call_count": cnt,
                          "evidence": evidence[(d1, d2)]})

    # ── hubs（结构层）──
    hubs = []
    for d in sorted(set(in_src) | set(out_tgt)):
        hubs.append({"domain": d,
                     "in_src_count": len(in_src.get(d, set())),
                     "biz_out_count": len(biz_out(d)),
                     "role": role_of(d)})

    # ── suggestions（业务骨架层）：去共享层源/目标 + 叶子 + scope 排除 ──
    exclude_domains = {d for d in (scope or {}).get("exclude_domains", []) if d}
    exclude_suffixes = [s for s in (scope or {}).get("exclude_suffixes", []) if s]

    def is_excluded(d: str) -> bool:
        return d in shared_layers or d in exclude_domains or any(
            d.endswith(s) for s in exclude_suffixes)

    suggestions = []
    for (d1, d2), cnt in cross.most_common():
        if is_excluded(d1) or is_excluded(d2):
            continue
        if not biz_out(d2):
            continue  # 目标是纯叶子接收域，无业务语义
        if cnt < 3:
            continue
        suggestions.append({
            "from_domain": d1,
            "to_domain": d2,
            "call_count": cnt,
            "evidence": evidence[(d1, d2)],
        })
    return {
        "edges": edges,
        "hubs": hubs,
        "shared_layers": shared_layers,
        "suggestions": suggestions,
    }


def analyze_gap(project_path: str, target_arch: Dict[str, Any],
                max_unassigned: int = DEFAULT_MAX_UNASSIGNED,
                db_path: Optional[str] = None) -> dict:
    """架构差距分析主入口。

    Args:
        project_path: 目标项目路径（自动定位知识图谱）。
        target_arch: 目标架构 JSON（dict，须先经 target_arch_schema 校验）。
        max_unassigned: 游离模块报出上限。
        db_path: 知识图谱 db（缺省自动定位）。

    Returns:
        结构化差距清单：gaps / summary / alignment / graph_stats。
    """
    db = db_path or locate_kg_db(project_path)
    result = {
        "project_path": project_path,
        "tool": "coderef_arch_gap",
        "ok": False,
        "gaps": [],
        "summary": {},
        "alignment": {},
        "graph_stats": {"has_kg": False},
    }
    if not db or not os.path.exists(db):
        result["summary"] = "知识图谱不存在，需先构建（coderef_audit / coderef_memory_sync）"
        return result

    nodes, adj = load_graph(db)
    result["graph_stats"] = {
        "has_kg": True,
        "nodes": len(nodes),
        "calls_edges": sum(len(v) for v in adj.values()),
    }

    # 现状症状（复用 arch_audit）
    arch = arch_audit(project_path, db_path=db)

    roles = target_arch.get("tech_roles") or []
    flows = target_arch.get("business_flows") or []
    constraints = target_arch.get("constraints") or []

    # 目标归属映射
    role_of: Dict[str, str] = {}          # mod 节点 id → 角色 id
    role_has_impl: Dict[str, bool] = {}   # 角色 id → 是否有有效实现
    assigned_ids: Set[str] = set()
    for role in roles:
        rid = role.get("id", "")
        specs = role.get("target_modules", [])
        matched = _match_module_ids(nodes, project_path, specs)
        for nid in matched:
            role_of[nid] = rid
        assigned_ids |= matched
        role_has_impl[rid] = any(
            _module_exists(project_path, s, nodes) for s in specs)

    gaps: List[dict] = []

    # 1) 职责缺失
    gaps.extend(_detect_missing(roles, project_path, nodes))

    # 2) 游离模块（真游离 free 优先，未建模 unmodeled 次之；豁免 vendor/产物噪声）
    unassigned_gaps, unassigned_total, unassigned_free, unassigned_unmodeled = _detect_unassigned(
        nodes, adj, project_path, assigned_ids, max_unassigned)
    gaps.extend(unassigned_gaps)

    # 3) 依赖违例
    gaps.extend(_detect_dependency_violations(
        nodes, adj, project_path, role_of, constraints))

    # 3.5) 同构重复 / 目录级重复（建议书 P0①，复用 arch_insight P0-C）
    #       ds 一次调用，供重复差距与孪生判定复用，避免重复计算
    ds = duplicate_insight(project_path, db_path=db)
    gaps.extend(_detect_duplicates(project_path, db, {"duplicate": True,
                                                      "directory_duplicate": True},
                                   ds=ds))

    # 3.6) 孪生模块真身/孤本标注（③）：目录级同构对中同名模块按 fan_in 判真身/孤本
    for twin in _detect_twin_identity(nodes, adj, project_path, ds=ds):
        true_mods = [c["module"] for c in twin["copies"] if c["verdict"] == "真身"]
        orphan_mods = [c["module"] for c in twin["copies"] if c["verdict"] == "孤本"]
        gaps.append({
            "type": "twin_identity",
            "severity": SEVERITY["twin_identity"],
            "dir_a": twin["dir_a"],
            "dir_b": twin["dir_b"],
            "file_sim": twin.get("file_sim", 0.0),
            "func_sim": twin.get("func_sim", 0.0),
            "copies": twin["copies"],
            "detail": (f"孪生目录 {twin['dir_a']} 与 {twin['dir_b']} 同构"
                       f"（文件相似度 {twin['file_sim']:.0%}），同名模块按 fan_in 判真身/孤本："
                       f"{'、'.join(true_mods)} 为真身，{'、'.join(orphan_mods)} 为孤本，"
                       f"建议收敛（G1 优先）"),
        })

    # 4) 循环依赖（复用 arch_audit，过滤纯测试模块组成的环）
    for cyc in arch.get("cycles", []):
        prod = [m for m in cyc if not _is_test_module(m)]
        if not prod:
            continue  # 环完全由测试模块组成，对齐目标不关注
        gaps.append({
            "type": "cycle",
            "severity": SEVERITY["cycle"],
            "modules": cyc,
            "detail": f"循环依赖: {' → '.join(cyc)}",
        })

    # 5) 上帝模块（复用 arch_audit，过滤测试模块）
    for g in arch.get("god_modules", []):
        if _is_test_module(g.get("module", "")):
            continue
        gaps.append({
            "type": "god_module",
            "severity": SEVERITY["god_module"],
            "module": g.get("module", ""),
            "detail": (f"上帝模块 {g.get('module', '')}: "
                       f"扇出 {g.get('fan_out', 0)}, 扇入 {g.get('fan_in', 0)}"),
        })

    # 6) 异常规模（复用 arch_audit，过滤测试模块）
    for lm in arch.get("large_modules", []):
        if _is_test_module(lm.get("module", "")):
            continue
        gaps.append({
            "type": "large_module",
            "severity": SEVERITY["large_module"],
            "module": lm.get("module", ""),
            "detail": f"异常规模模块 {lm.get('module', '')}: {lm.get('symbols', 0)} 个符号",
        })

    # 7) 业务断链
    gaps.extend(_detect_business_gaps(flows, role_has_impl))

    # 汇总
    by_sev = {"high": 0, "medium": 0, "low": 0}
    for g in gaps:
        by_sev[g.get("severity", "low")] = by_sev.get(g.get("severity", "low"), 0) + 1
    result["gaps"] = gaps
    dup_cnt = sum(1 for g in gaps if g.get("type") == "duplicate")
    dir_dup_cnt = sum(1 for g in gaps if g.get("type") == "directory_duplicate")
    twin_cnt = sum(1 for g in gaps if g.get("type") == "twin_identity")
    result["summary"] = {
        "total": len(gaps),
        "high": by_sev["high"],
        "medium": by_sev["medium"],
        "low": by_sev["low"],
        "duplicate": dup_cnt,
        "directory_duplicate": dir_dup_cnt,
        "twin_identity": twin_cnt,
        "unassigned_total": unassigned_total,
        "unassigned_shown": len(unassigned_gaps),
        "unassigned_free": unassigned_free,
        "unassigned_unmodeled": unassigned_unmodeled,
    }

    # 对齐度（Phase 0 简化：角色覆盖度 + 模块归属度）
    total_roles = len(roles)
    impl_roles = sum(1 for v in role_has_impl.values() if v)
    # 口径与 unassigned/`arch_verify` 对齐：total_mods 与 assigned_ids 均排除 test 模块
    total_mods = sum(
        1 for n in nodes.values()
        if n.get("type") == "module"
        and not _is_test_module(module_of(n, project_path) or n.get("name", "")))
    assigned_production = sum(
        1 for nid in assigned_ids
        if not _is_test_module(
            module_of(nodes[nid], project_path) or nodes[nid].get("name", "")))
    module_assigned = round(assigned_production / total_mods, 2) if total_mods else 1.0
    result["alignment"] = {
        "role_coverage": round(impl_roles / total_roles, 2) if total_roles else 1.0,
        "module_assigned": module_assigned,
        "note": "Phase 0 简化对齐度：role_coverage=已实现角色/总角色；module_assigned=已归属模块/总模块",
    }

    # 域间业务流量透视（②）：domain_flow 三层 + flow_suggestions 兼容简表
    flow_scope = (target_arch.get("business_flow") or {}).get("scope") or {}
    domain_flow = _domain_flow_model(nodes, adj, project_path, flow_scope)
    result["domain_flow"] = domain_flow
    result["flow_suggestions"] = domain_flow["suggestions"]

    # 覆盖引导（①）：target 覆盖面低或业务流不足时显式提示，防治理建在残缺图上
    guidance: List[str] = []
    empty_target_roles = [
        role.get("name", role.get("id", ""))
        for role in roles
        if not role.get("target_modules")
    ]
    if empty_target_roles:
        guidance.append(
            "以下角色未声明 target_modules：" + "、".join(empty_target_roles)
            + "。请在 define-target 阶段补全真实实现模块")
    if module_assigned < 0.3:
        guidance.append(
            f"target_modules 覆盖不完整（module_assigned={module_assigned}，"
            f"{unassigned_total} 个游离模块），建议在 define-target 阶段基于真实调用"
            f"补全主干模块，否则治理建在残缺图上")
    if len(flows) < 2:
        guidance.append(
            f"业务流仅 {len(flows)} 条，建议纳入多条主干业务流（可参考本轮 domain_flow "
            f"suggestions 列出的真实跨域主干），而非唯一定义单条链路")
    result["coverage_guidance"] = guidance

    result["ok"] = True
    return result
