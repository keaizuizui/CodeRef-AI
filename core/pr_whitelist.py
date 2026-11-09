# -*- coding: utf-8 -*-
"""
白名单家族 + 核心模块规则 + 共享 AST/图谱辅助（core/pipeline_runner 拆解子模块）。

原为 pipeline_runner.py 370~770 行（含 _match_whitelist/_xval/_latest_report/_workflow/
_findings_json_path/_finding_*/_count_md/_detect_wiki_dir/docs_read/_select_tools/
list_single_tools/_build_kg/_build_scope_text/_scan/core_rules_*/whitelist_*/
_whitelist_path/_cdir/_phash/_compute_health/_tier_for）；拆出后由主文件 re-export，
对外符号不变（whitelist_list 被 8 个生产模块 import，必须经主文件 re-export 保住）。
依赖方向：本模块依赖 pr_models，供主文件 / pr_render / pr_ckpt / pr_stages 使用。
"""

import os, json, hashlib
from loguru import logger
from typing import List, Optional

from core import tool_registry
from core.pr_models import Finding, PipeResult, Tier


def _match_whitelist(f: Finding, wl: list) -> bool:
    """f 是否匹配白名单条目（AND 逻辑）"""
    fl = f.file_path.lower()
    tl = f.title.lower()
    cl = f.category.lower()
    for entry in wl:
        if entry.get("file") and entry["file"] not in fl:
            continue
        if entry.get("rule") and entry["rule"] not in tl:
            continue
        if entry.get("category") and entry["category"] not in cl:
            continue
        return True
    return False

def _xval(r: PipeResult):
    """多工具命中同一位置 → HIGH"""
    by = {}
    for f in r.findings:
        k = (f.file_path, f.line, f.category)
        by.setdefault(k, []).append(f)
    for fl in by.values():
        tools = list(set(f.tool for f in fl))
        if len(tools) >= 2:
            for f in fl:
                f.xval_by = [t for t in tools if t != f.tool]
                f.tier = Tier.HIGH

def _latest_report(out: str, project_path: str = "") -> Optional[str]:
    """返回输出目录下最近一份审计报告（coderef_audit_*.md），无则 None。

    project_path 非空时按项目哈希前缀过滤，避免共享输出目录下跨项目串扰
    （旧版仅按 mtime 取最新，会把其他项目的报告误当成本项目结论）。
    """
    try:
        if not os.path.isdir(out):
            return None
        prefix = (f"coderef_audit_{_phash(project_path)}_"
                  if project_path else "coderef_audit_")
        cands = [os.path.join(out, f) for f in os.listdir(out)
                 if f.startswith(prefix) and f.endswith(".md")]
        if not cands:
            return None
        return max(cands, key=os.path.getmtime)
    except Exception:
        # 报告文件定位失败视为无历史报告
        return None

def _workflow(p: str, r: PipeResult):
    try:
        from core.workflow_graph import WorkflowGraph
        html = WorkflowGraph().generate(project_path=p)
        r.report_path = html
    except Exception as e: r.errors.append(f"workflow: {e}")

def _findings_json_path(project_path: str, out: Optional[str] = None) -> str:
    """审计 findings JSON 的固定落盘路径（优先调用方 out，其次标准 coderef-report）。

    文件名带项目哈希前缀，避免共享输出目录下多项目互相覆盖（跨项目串扰）。
    """
    fname = f"audit_findings_{_phash(project_path)}.json"
    if out:
        return os.path.join(out, fname)
    return os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "coderef-report", fname)


def project_findings_json_path(project_path: str) -> str:
    """项目侧 findings JSON 路径（`<project>/.coderef/`）。

    与 scorecard / product_fit / ux_fit 同目录：分析外部项目时不再依赖工具仓库的
    共享输出目录，避免「审计写一处、重渲染读另一处」导致的静默失败（U-49）。
    """
    fname = f"audit_findings_{_phash(project_path)}.json"
    return os.path.join(os.path.abspath(project_path), ".coderef", fname)

def _finding_from_dict(d: dict) -> "Finding":
    tier = Tier(d.get("tier", "low")) if d.get("tier") else Tier.LOW
    return Finding(
        id=d.get("id", ""), tool=d.get("tool", ""), category=d.get("category", ""),
        severity=d.get("severity", "medium"), file_path=d.get("file_path", ""),
        line=d.get("line", 0), title=d.get("title", ""), detail=d.get("detail", ""),
        suggestion=d.get("suggestion", ""), tier=tier,
        xval_by=list(d.get("xval_by", []) or []),
        line_start=d.get("line_start", 0), line_end=d.get("line_end", 0),
        kind=d.get("kind", "defect"),
        count=int(d.get("count", 1) or 1),
        locations=list(d.get("locations", []) or []))

def _finding_to_dict(f: "Finding") -> dict:
    return {
        "id": f.id, "tool": f.tool, "category": f.category,
        "severity": f.severity, "file_path": f.file_path, "line": f.line,
        "title": f.title, "detail": f.detail, "suggestion": f.suggestion,
        "tier": f.tier.value if f.tier else "low",
        "xval_by": list(f.xval_by or []),
        "line_start": f.line_start, "line_end": f.line_end,
        "kind": f.kind,
        # 爆发式合并后保留计数与全部位置，避免 round-trip 丢聚合信息
        "count": int(getattr(f, "count", 1) or 1),
        "locations": list(getattr(f, "locations", None) or []),
    }

def _count_md(wiki_dir: str) -> int:
    """统计目录（含子目录）下 .md 文档数。"""
    n = 0
    for root, _, files in os.walk(wiki_dir):
        n += sum(1 for f in files if f.endswith(".md"))
    return n

def _detect_wiki_dir(project_path: str) -> Optional[str]:
    """探测已生成的 Wiki 输出目录（第一个存在的），供重渲染聚合使用。"""
    candidates = [
        os.path.join(project_path, "docs", "wiki"),
        os.path.join(project_path, "docs"),
        os.path.join(project_path, "txt"),
    ]
    for c in candidates:
        if os.path.isdir(c):
            return c
    return None


def detect_wiki_output_dir(project_path: str) -> Optional[str]:
    """只认真正的 Wiki 产物目录（用于覆盖率评分）。

    与 `_detect_wiki_dir` 的区别：不回退到通用 `docs/` / `txt/`——那里是用户
    自己的文档，把它算成「Wiki 覆盖率」会出现「仓库里随便几个 .md 就得 2 分」
    这种无法复核的读数（外部审计 UI 复审 P0-3）。
    """
    candidates = [
        os.path.join(project_path, "docs", "wiki"),
        os.path.join(project_path, ".coderef", "wiki"),
        os.path.join(project_path, "coderef-wiki"),
    ]
    for c in candidates:
        if os.path.isdir(c):
            return c
    return None

def docs_read(project_path: str, doc: Optional[str] = None,
              output_dir: Optional[str] = None,
              max_chars: int = 20000) -> dict:
    """按需读取已生成的 Wiki 文档 —— 解决编程 AI 无法 fs 访问外部文件夹的问题。

    docs 生成后正文落在磁盘，而 MCP 返回的只是路径引用，AI 拿不到内容。
    本方法把文档正文作为返回值直接交给调用方（AI），无需 fs 访问。

    探测输出目录（取第一个存在的）：
      1. 显式 output_dir；
      2. {project_path}/docs/wiki/（wiki_generator 默认）；
      3. {project_path}/docs/（docs 根目录）；
      4. {project_path}/txt（MCP 未指定 output_dir 时的回退）。

    doc 为 None 时列出全部文档（相对路径）；否则读取指定文档正文。
    安全：仅允许读取 .md 且 resolve 后位于输出目录内的文件，防路径穿越。
    """
    project_path = os.path.abspath(project_path)
    candidates = []
    if output_dir:
        candidates.append(os.path.abspath(output_dir))
    candidates += [
        os.path.join(project_path, "docs", "wiki"),
        os.path.join(project_path, "docs"),
        os.path.join(project_path, "txt"),
    ]
    base = None
    for c in candidates:
        if os.path.isdir(c):
            base = c
            break
    if base is None:
        return {"status": "not_found", "error": "未找到 Wiki 文档目录",
                "searched": candidates}

    # 收集该目录下所有 .md（含 MODULES/ 子目录）
    md_files = []
    for root, _, files in os.walk(base):
        for f in files:
            if f.endswith(".md"):
                rel = os.path.relpath(os.path.join(root, f), base)
                md_files.append(rel.replace("\\", "/"))
    md_files.sort()

    if not doc:
        return {"status": "ok", "output_dir": base,
                "documents": md_files, "count": len(md_files)}

    # 读取指定文档（含路径穿越防护）
    target = os.path.normpath(os.path.join(base, doc))
    real_base = os.path.realpath(base)
    real_target = os.path.realpath(target)
    if not (doc.endswith(".md") and real_target.startswith(real_base + os.sep)):
        return {"status": "error", "error": "非法文档引用（仅允许 .md 且位于输出目录内）"}
    if not os.path.isfile(real_target):
        return {"status": "not_found", "doc": doc, "error": "文档不存在",
                "available": md_files[:50]}
    try:
        with open(real_target, "r", encoding="utf-8") as f:
            content = f.read()
    except Exception as e:
        return {"status": "error", "doc": doc, "error": f"读取失败: {e}"}
    truncated = len(content) > max_chars
    return {"status": "ok", "doc": doc, "output_dir": base,
            "file_path": real_target,
            "chars": len(content),
            "truncated": truncated,
            "content": content[:max_chars]}

def _select_tools(strategy: Optional[str]) -> list:
    """按审计策略选择要运行的工具子集 —— 委托 tool_registry.select_tools

    规则定义见 tool_registry.select_tools 的 docstring。
    """
    return tool_registry.select_tools(strategy)

def list_single_tools() -> list:
    """列出所有可单独运行的审计工具（短名 + 展示名）"""
    return tool_registry.list_single_tools()

def _build_kg(project_path: str, analysis) -> dict:
    """构建知识图谱（异步，不影响主流程）"""
    try:
        from core.code_knowledge_graph import build_knowledge_graph
        from core.ast_parser import AstParser
        gx = os.path.join(project_path, ".gitnexus", "csv")
        if not os.path.isdir(gx):
            gx = None

        # 白名单中的 dir 条目 → 图谱排除目录（备份/镜像目录不污染符号级判定）
        exclude_dirs = [e["dir"] for e in whitelist_list(project_path)
                        if e.get("dir")]

        # 批量 AST 解析 Python 文件
        ast_results = {}
        py_files = [cf for cf in getattr(analysis, "files", [])
                    if getattr(cf, "file_path", "").endswith(".py")]
        parsed_count = 0
        for cf in py_files:
            file_path = cf.file_path
            try:
                ar = AstParser().parse(file_path)
                if ar:
                    ast_results[file_path] = ar
                    parsed_count += 1
            except Exception as e:
                # 预热解析失败，跳过该文件
                logger.warning(f"AST 预热解析失败 {file_path}: {e}")

        total_calls = sum(
            len(getattr(ar, "calls", [])) for ar in ast_results.values())
        total_assigns = sum(
            len(getattr(ar, "assignments", [])) for ar in ast_results.values())
        logger.info(
            f"[KG] AST 解析: {parsed_count}/{len(py_files)} 个 Python 文件, "
            f"提取 {total_calls} 条 CALLS 边, {total_assigns} 个 Config/Constant 节点"
        )

        kg = build_knowledge_graph(
            project_path, analysis=analysis,
            ast_results=ast_results, gitnexus_dir=gx,
            exclude_dirs=exclude_dirs)
        return kg.get_stats()
    except Exception as e:
        return {"error": str(e)}

def _build_scope_text(project_path: str, total_files: int) -> str:
    """生成审计范围说明：披露被排除目录，避免"为何只统计 N 文件"的疑问。

    复用 ProjectScope 的跳过规则（虚拟环境/vendored库/数据目录/运行时等），
    将跳过目录数与原因分布写进报告，保证统计透明。
    """
    try:
        from core.project_scope import ProjectScope
        scope = ProjectScope(project_path)
        scope.analyze()
        stats = scope.get_stats()
        skip_count = stats.get("skip_dir_count", 0)
        reasons = stats.get("skip_reasons", {})
        parts = [f"分析 {total_files} 个代码文件"]
        if skip_count:
            parts.append(f"按规则排除 {skip_count} 个目录")
            top = sorted(reasons.items(), key=lambda x: -x[1])[:6]
            if top:
                detail = "；".join(f"{name}×{cnt}" for name, cnt in top)
                parts.append(f"（{detail}）")
        return "；".join(parts)
    except Exception as e:
        return f"分析 {total_files} 个代码文件（范围统计失败: {e}）"

def _scan(p: str, file_cb=None) -> tuple:
    from core.code_analyzer import CodeAnalyzer
    a = CodeAnalyzer().analyze_project(p, file_progress_cb=file_cb)
    return (getattr(a, "total_files", 0) or 0, getattr(a, "total_lines", 0) or 0, a)

def core_rules_reset(project_path: str) -> dict:
    """重置为核心模块判定默认规则"""
    from core.wiki_generator import WikiGenerator
    default = {
        "entry_files": list(WikiGenerator.DEFAULT_ENTRY_FILES),
        "core_names": [],
        "min_files": WikiGenerator.DEFAULT_MIN_FILES,
    }
    ok = WikiGenerator.save_core_rules(project_path, default)
    return {"saved": ok, "rules": default} if ok else {"error": "保存失败"}

def core_rules_set(project_path: str, rules: dict) -> dict:
    """设置核心模块判定规则

    rules 可含:
      - entry_files: ["main.py", "app.py", ...]  入口文件名列表
      - core_names: ["业务工具", "shared", ...]  强制核心模块名
      - min_files: 10                             文件数阈值
    未指定的字段保持默认值。
    """
    from core.wiki_generator import WikiGenerator
    current = WikiGenerator.get_core_rules(project_path)
    if "entry_files" in rules:
        current["entry_files"] = rules["entry_files"]
    if "core_names" in rules:
        current["core_names"] = rules["core_names"]
    if "min_files" in rules:
        current["min_files"] = rules["min_files"]
    ok = WikiGenerator.save_core_rules(project_path, current)
    return {"saved": ok, "rules": current} if ok else {"error": "保存失败"}

def core_rules_get(project_path: str) -> dict:
    """查看当前核心模块判定规则"""
    from core.wiki_generator import WikiGenerator
    return WikiGenerator.get_core_rules(project_path)

def whitelist_clear(project_path: str) -> int:
    """清空白名单，返回被删除的条目数"""
    path = _whitelist_path(project_path)
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            n = len(json.load(f))
        os.remove(path)
        return n
    return 0

def whitelist_list(project_path: str) -> list:
    """查看当前白名单"""
    path = _whitelist_path(project_path)
    if os.path.exists(path):
        try:
            return json.load(open(path, "r", encoding="utf-8"))
        except Exception as e:  # noqa: BLE001
            logger.debug(f"[whitelist] 读取白名单失败，按空处理: {e}")
    return []

def whitelist_add(project_path: str, entries: List[dict]) -> int:
    """AI 可调用：添加白名单条目。返回新增数量。

    每个 entry 可含：file（匹配 file_path 子串）、rule（匹配 title 子串）、
    category（匹配 category 子串）、dir（排除目录相对路径，作用于知识图谱
    符号级分析——目录下文件不参与真身判定/循环/重复匹配）。file/rule/category
    三者 AND 逻辑；dir 独立生效。
    """
    path = _whitelist_path(project_path)
    existing = []
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                existing = json.load(f)
        except Exception as e:  # noqa: BLE001
            # 既有条目读不出时按空处理（本次新增条目仍会写入），需在日志可追溯
            logger.warning(f"[whitelist] 既有条目读取失败，按空处理: {e}")
    added = 0
    for e in entries:
        entry = {}
        for k in ("file", "rule", "category", "dir"):
            v = e.get(k)
            if v:
                # dir 是目录相对路径，保留原始大小写（Linux 区分大小写）；
                # file/rule/category 为匹配子串，统一小写归一
                entry[k] = str(v) if k == "dir" else str(v).lower()
        if entry and entry not in existing:
            existing.append(entry)
            added += 1
    with open(path, "w", encoding="utf-8") as f:
        json.dump(existing, f, ensure_ascii=False, indent=1)
    return added

def _whitelist_path(project_path: str) -> str:
    h = _phash(project_path)
    return os.path.join(_cdir(), f"wl_{h}.json")

def _cdir() -> str:
    d = os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), "cache", "pipeline")
    os.makedirs(d, exist_ok=True)
    return d

def _phash(p: str) -> str:
    return hashlib.md5(p.encode()).hexdigest()[:12]

def _compute_health(r: 'PipeResult') -> Optional[int]:
    """统一健康分计算入口（0-100）。空项目（0 代码文件）返回 None（N/A）。

    口径与 health_dashboard._calc_score 一致（基于 findings 的 tier 扣分，
    HIGH -5 / MEDIUM -1 / LOW -0.2，最低 0），保证单维度 run_single 与全量
    audit 的健康分一致（证据审计缺陷 10）。空项目不返回 100，返回 None 表示
    "审计无意义/N/A"，避免"静默健康满分"误导（缺陷 9）。
    """
    if getattr(r, "total_files", 0) == 0:
        return None
    if not r.findings:
        return 100
    score = 100.0
    for f in r.findings:
        if f.tier == Tier.HIGH:
            score -= 5
        elif f.tier == Tier.MEDIUM:
            score -= 1
        elif f.tier == Tier.LOW:
            score -= 0.2
    return max(0, int(score))

def _tier_for(severity: str) -> Tier:
    """按严重程度严格映射置信度等级，修复分级归表混入问题。

    旧实现各检测器硬编码 tier（如 _td/_blind/_resgap 一律 Tier.MEDIUM），
    导致 severity=low 的条目被塞进 MEDIUM 表。现在 tier 始终跟随 severity：
      critical/high/blocker → HIGH
      medium/warning/info   → MEDIUM
      low/其余              → LOW
    """
    s = (severity or "").lower().strip()
    if s in ("critical", "high", "blocker"):
        return Tier.HIGH
    if s in ("medium", "warning", "info"):
        return Tier.MEDIUM
    return Tier.LOW


# ── 公开导出（聚合层如 core.scorecard 复用；内部实现保持私有命名）──
# 放在文件末尾：确保被引用的私有函数均已定义（_phash 定义在本文件后段）。
project_hash = _phash
count_md = _count_md
detect_wiki_dir = _detect_wiki_dir
