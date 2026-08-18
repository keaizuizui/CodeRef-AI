# -*- coding: utf-8 -*-
"""
Pipeline Runner v2.0 — 三模式管线

  coderef_audit        → 11 审计工具 一次产出
  coderef_architecture  → 架构分析图谱
  coderef_docs          → 项目文档探查

All modes share: single AST scan + checkpoint resume.
"""

import os, sys, json, time, hashlib, traceback, importlib
from datetime import datetime
from loguru import logger
from dataclasses import dataclass, field
from typing import Dict, List, Optional
from enum import Enum
from core import tool_registry

class Tier(Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"

@dataclass
class Finding:
    id: str; tool: str; category: str; severity: str = "medium"
    file_path: str = ""; line: int = 0; title: str = ""
    detail: str = ""; suggestion: str = ""
    tier: Tier = Tier.LOW
    xval_by: List[str] = field(default_factory=list)
    # 相邻行合并后记录真实行号区间，避免标题退化为 "(等多行)"
    line_start: int = 0
    line_end: int = 0
    # 类型标记：defect=缺陷/缺失；advice=工程化改进建议（非缺陷）。
    # 汇总层据此区分"建议项"与"缺陷项"，避免把工程化 warn 等建议误报为缺陷。
    # 带默认值，向后兼容所有不含 kind 的现有 Finding 构造调用。
    kind: str = "defect"
    # 爆发式合并（_burst_merge）后保留的真实数量与全部位置。
    # count=1 表示未聚合（单条）；>1 表示该 finding 代表 N 条同 tool+category 违规。
    # locations 记录被合并的全部 file:line，避免"标题写共 N 条却无法定位其余 N-1 条"。
    # 带默认值，向后兼容所有不含 count/locations 的现有 Finding 构造调用。
    count: int = 1
    locations: List[str] = field(default_factory=list)

    @property
    def line_label(self) -> str:
        """可读的行号定位：单行显示 Ln，区间显示 Ln~Lm"""
        if self.line_end and self.line_end > self.line:
            return f"{self.file_path}:{self.line}~{self.line_end}"
        if self.line:
            return f"{self.file_path}:{self.line}"
        return self.file_path or ""

@dataclass
class PipeResult:
    project_path: str; total_files: int = 0; total_lines: int = 0
    findings: List[Finding] = field(default_factory=list)
    report: str = ""; errors: List[str] = field(default_factory=list)
    elapsed: float = 0.0; report_path: str = ""
    scope_text: str = ""  # 审计范围说明（披露被排除的目录，保证统计透明）
    # 审计证据字段：让调用方能区分"本次扫描结果"与"历史缓存/修复状态"，
    # 避免外层把旧知识图谱或记忆当成本次审计结论。
    scan_ts: str = ""              # 本次扫描开始时间（ISO 时间戳）
    file_snapshot: dict = field(default_factory=dict)  # 本次被扫描文件的 mtime+size 快照
    kg_built_at: str = ""          # 本次审计构建的知识图谱时间（若为历史图谱则为旧时间）
    wiki_result: Optional[object] = None  # coderef_docs 的 WikiResult，供 MCP 层返回结构化文档统计
    html_report: dict = field(default_factory=dict)  # HTML 报告渲染结果（见 report_renderer.render 返回值）
    # 功能②：审计策略判定 + LLM 功能审查增强结果
    review_strategy: dict = field(default_factory=dict)     # ReviewAdvisor.advise() 返回值
    functional_review: dict = field(default_factory=dict)   # FunctionalReviewer.review() 返回值
    # 动态兜底：本次审计实际采用的策略（full/incr/no_change），由 audit() 在跑工具前判定
    audit_strategy: str = "full"
    # 统一健康分（0-100）。空项目（0 代码文件）为 None（N/A）。
    # 由 _compute_health 统一计算，保证 run_single 与 audit 两入口一致（缺陷 10）。
    health_score: Optional[int] = None

class Pipe:

    def __init__(self):
        self._t0 = 0.0

    @staticmethod
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

    @staticmethod
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

    @staticmethod
    def _phash(p: str) -> str:
        return hashlib.md5(p.encode()).hexdigest()[:12]

    @staticmethod
    def _cdir() -> str:
        d = os.path.join(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__))), "cache", "pipeline")
        os.makedirs(d, exist_ok=True)
        return d

    # ─── checkpoint ───

    def _ckpt(self, p: str) -> str:
        return os.path.join(self._cdir(), f"{self._phash(p)}.ckpt.json")

    def _save(self, p: str, done: List[str]):
        try:
            cp = self._ckpt(p)
            tmp = cp + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump({"done": done, "ts": datetime.now().isoformat()}, f)
            os.replace(tmp, cp)  # 原子替换，避免多 Agent 并发时读到半写入的检查点
        except: pass

    def _load(self, p: str) -> set:
        try:
            cp = self._ckpt(p)
            if not os.path.exists(cp):
                return set()
            with open(cp, "r", encoding="utf-8") as f:
                return set(json.load(f).get("done", []))
        except Exception: return set()

    # ─── AI 白名单（编程 AI 补充意见持久化）───

    @staticmethod
    def _whitelist_path(project_path: str) -> str:
        h = Pipe._phash(project_path)
        return os.path.join(Pipe._cdir(), f"wl_{h}.json")

    @staticmethod
    def whitelist_add(project_path: str, entries: List[dict]) -> int:
        """AI 可调用：添加白名单条目。返回新增数量。

        每个 entry 可含：file（匹配 file_path 子串）、rule（匹配 title 子串）、
        category（匹配 category 子串）。三者 AND 逻辑。
        """
        path = Pipe._whitelist_path(project_path)
        existing = []
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    existing = json.load(f)
            except: pass
        added = 0
        for e in entries:
            entry = {k: str(v).lower() for k, v in e.items() if v}
            if entry and entry not in existing:
                existing.append(entry)
                added += 1
        with open(path, "w", encoding="utf-8") as f:
            json.dump(existing, f, ensure_ascii=False, indent=1)
        return added

    @staticmethod
    def whitelist_list(project_path: str) -> list:
        """查看当前白名单"""
        path = Pipe._whitelist_path(project_path)
        if os.path.exists(path):
            try:
                return json.load(open(path, "r", encoding="utf-8"))
            except: pass
        return []

    @staticmethod
    def whitelist_clear(project_path: str) -> int:
        """清空白名单，返回被删除的条目数"""
        path = Pipe._whitelist_path(project_path)
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                n = len(json.load(f))
            os.remove(path)
            return n
        return 0

    def _load_whitelist(self, project_path: str) -> list:
        """加载 AI 白名单（供 _denoise 使用）"""
        return self.whitelist_list(project_path)

    # ─── 核心模块规则管理（AI 可追加入口文件名/核心模块名/阈值）───

    @staticmethod
    def core_rules_get(project_path: str) -> dict:
        """查看当前核心模块判定规则"""
        from core.wiki_generator import WikiGenerator
        return WikiGenerator.get_core_rules(project_path)

    @staticmethod
    def core_rules_set(project_path: str, rules: dict) -> dict:
        """设置核心模块判定规则

        rules 可含:
          - entry_files: ["main.py", "app.py", ...]  入口文件名列表
          - core_names: ["洞察工具", "shared", ...]  强制核心模块名
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

    @staticmethod
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

    # ─── shared AST ───

    def _scan(self, p: str, file_cb=None) -> tuple:
        from core.code_analyzer import CodeAnalyzer
        a = CodeAnalyzer().analyze_project(p, file_progress_cb=file_cb)
        return (getattr(a, "total_files", 0) or 0, getattr(a, "total_lines", 0) or 0, a)

    @staticmethod
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

    # ─── knowledge graph ───

    def _build_kg(self, project_path: str, analysis) -> dict:
        """构建知识图谱（异步，不影响主流程）"""
        try:
            from core.code_knowledge_graph import build_knowledge_graph
            from core.ast_parser import AstParser
            gx = os.path.join(project_path, ".gitnexus", "csv")
            if not os.path.isdir(gx):
                gx = None

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
                ast_results=ast_results, gitnexus_dir=gx)
            return kg.get_stats()
        except Exception as e:
            return {"error": str(e)}

    # ─── HTML 报告渲染（功能①：把审计/图谱/Wiki 聚合成前端可读的 HTML 报告目录）───

    def _render_html(self, project_path: str, r: PipeResult,
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
                    r.dimension_states = self._collect_dimension_states(
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

    # ═══════════════════════════════════
    # 三大管线
    # ═══════════════════════════════════

    def audit(self, project_path: str, output_dir: str = None,
              resume: bool = False, progress_cb=None,
              strategy: Optional[str] = None) -> PipeResult:
        """安全审计管线：11 工具（按策略裁剪）

        progress_cb: 可选回调 progress_cb(stage:str, done:int, total:int)
        用于后台执行时向调用方回传阶段进度。

        strategy: 可选，显式指定审计策略（"full"/"incr"/"no_change"）。
        为 None 时自动调用 ReviewAdvisor 判定（变更信号 + 影响闭包），
        并据此裁剪工具集 —— 动态兜底：首次/无基线→全量，增量→裁剪重型工具。
        """
        self._t0 = time.time()
        r = PipeResult(project_path=project_path)
        d = self._load(project_path) if resume else set()
        out = output_dir or os.path.join(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__))), "coderef-report")

        def _prog(stage, done, total, detail=None):
            if progress_cb:
                try: progress_cb(stage, done, total, detail)
                except Exception: pass

        try:
            # 审计证据：记录本次扫描开始时间，供调用方区分"本次结果"与历史缓存
            r.scan_ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

            # 审计策略判定（增量 vs 全量）—— 动态兜底第一步：在跑任何阶段前算出策略，
            # 据此裁剪工具集并确定统一进度分母，避免"算完不用 / 进度分母漂移"。
            effective_strategy = strategy
            if effective_strategy is None:
                try:
                    from core.review_strategy import review_advisor
                    advise = review_advisor.advise(project_path)
                    r.review_strategy = advise
                    effective_strategy = advise.get("strategy", "full")
                except Exception as e:
                    r.errors.append(f"review_strategy: {e}")
                    effective_strategy = "full"
            else:
                # 显式指定策略：跳过自动判定（避免重复 advise 开销），记录显式标记。
                # 保留与 advise() 一致的结构骨架（changes/impact/kg/dimensions_focus），
                # 避免下游报告/功能审查读到空键时渲染出"变更 0、波及 0、图谱 None"的误导结论。
                r.review_strategy = {
                    "strategy": effective_strategy,
                    "explicit": True,
                    "reason": "显式指定审计策略，跳过自动判定",
                    "changes": {"has_prev_snapshot": False, "changed": [],
                                "added": [], "deleted": [], "total": 0},
                    "impact": {"count": 0, "depth": 0, "nodes": []},
                    "kg": {"exists": False, "built_at": "", "stale": True},
                    "dimensions_focus": [],
                }
            r.audit_strategy = effective_strategy

            # 策略为 no_change 时：复用既有结论，不重扫（行为与描述一致）
            if effective_strategy == "no_change":
                reused_ok = self._reuse_no_change(project_path, r, out, _prog)
                if reused_ok:
                    r.elapsed = round(time.time() - self._t0, 1)
                    return r
                # 无可复用结论 → 降级为全量，记录提示
                r.errors.append(
                    "no_change 策略下未找到可复用的既有结论，已降级为 full 重新审计")
                effective_strategy = "full"
                r.audit_strategy = "full"

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
            # 本次实际扫描文件的 mtime+size 快照，作为"审计覆盖了哪些文件"的证据
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
                r.file_snapshot = snapshot
            except Exception:
                r.file_snapshot = {}
            _prog("扫描代码", 0, total_stages)

            # 构建知识图谱（持久化项目记忆）
            kg_stats = self._build_kg(project_path, analysis)
            r.kg_built_at = str(kg_stats.get("built_at", "")) if isinstance(kg_stats, dict) else ""
            _prog("构建知识图谱", 1, total_stages)

            # 执行工具
            for i, (name, fn) in enumerate(tools, start=2):
                fn(project_path, r, d)
                _prog(name, i, total_stages)
            if len(tools) < len(Pipe.ALL_AUDIT_TOOLS):
                logger.info(
                    f"[audit] 策略={effective_strategy}，裁剪 "
                    f"{len(Pipe.ALL_AUDIT_TOOLS) - len(tools)} 个重型全量工具，"
                    f"实际运行 {len(tools)} 个")

            self._xval(r)
            self._denoise(r)

            # 图谱增量 patch：若策略为增量且图谱存在，用 memory_layer 增量同步更新图谱，
            # 避免每次都全量重建（图谱增量复用，符合功能②B"图谱增量 patch"）。
            try:
                from core.memory_layer import memory_layer
                strata = getattr(r, "audit_strategy", "full") or "full"
                memory_layer.sync(project_path,
                                  mode="incr" if strata == "incr" else "full")
            except Exception as e:
                r.errors.append(f"memory_sync: {e}")

            # 功能②：LLM 功能审查增强（创新传播/结构复杂度/回归一致性等语义维度）
            try:
                from core.functional_review import functional_reviewer
                r.functional_review = functional_reviewer.review(
                    project_path, r.review_strategy or {},
                    pipe_result=r, kg_stats=kg_stats)
            except Exception as e:
                r.errors.append(f"functional_review: {e}")

            r.report = self._fmt(r, "审计报告")
            _prog("生成报告", total_stages - 1, total_stages)

            os.makedirs(out, exist_ok=True)
            fn = (f"coderef_audit_{self._phash(project_path)}_"
                  f"{datetime.now().strftime('%Y%m%d_%H%M%S')}.md")
            r.report_path = os.path.join(out, fn)
            with open(r.report_path, "w", encoding="utf-8") as f:
                f.write(r.report)
            # 结构化 findings 落盘：供 coderef_report 重渲染复用（避免聚合 HTML 审计全 0）
            self._dump_findings_json(r, out)

            # 生成健康仪表盘
            try:
                from core.health_dashboard import HealthDashboard
                dashboard = HealthDashboard(project_path)
                dashboard_path = dashboard.build(r, kg_stats)
                r.dashboard_path = dashboard_path
            except Exception as e:
                r.errors.append(f"dashboard: {e}")

            # 渲染 HTML 报告目录（审计 + 图谱；若已有 wiki 产物则一并纳入）
            self._render_html(project_path, r, kg_stats=kg_stats,
                              output_dir=os.path.join(out, "html"))

            if os.path.exists(self._ckpt(project_path)):
                os.remove(self._ckpt(project_path))
        except Exception as e:
            r.errors.append(str(e))

        r.health_score = self._compute_health(r)
        r.elapsed = round(time.time() - self._t0, 1)
        return r

    # ─── 单工具运行（供 MCP: coderef_scan_* 调用）───
    # 工具注册表与选择策略已抽到 tool_registry，此处仅保留 Pipe.* 别名，
    # 保持对外接口（mcp_server / 测试）不变。

    SINGLE_TOOLS = tool_registry.SINGLE_TOOLS
    ALL_AUDIT_TOOLS = tool_registry.ALL_AUDIT_TOOLS
    INCR_SKIP_TOOLS = tool_registry.INCR_SKIP_TOOLS

    @staticmethod
    def list_single_tools() -> list:
        """列出所有可单独运行的审计工具（短名 + 展示名）"""
        return tool_registry.list_single_tools()

    @staticmethod
    def _select_tools(strategy: Optional[str]) -> list:
        """按审计策略选择要运行的工具子集 —— 委托 tool_registry.select_tools

        规则定义见 tool_registry.select_tools 的 docstring。
        """
        return tool_registry.select_tools(strategy)

    def run_single(self, project_path: str, tool: str) -> PipeResult:
        """单独运行某一个审计工具 —— AI 写代码时的实时安全带。

        相比 audit 全量管线，只跑一个工具：不构建知识图谱、不生成 dashboard，
        只对该维度做 AST 扫描 + 检测 + 降噪，快速返回该维度的 findings，
        供 AI 在写完一个模块后即时自查（客观第二意见）。
        """
        tool = (tool or "").lower()
        if tool not in self.SINGLE_TOOLS:
            raise ValueError(
                f"未知工具 '{tool}'，支持: {', '.join(sorted(self.SINGLE_TOOLS))}")
        self._t0 = time.time()
        r = PipeResult(project_path=project_path)
        try:
            tf, tl, analysis = self._scan(project_path)
            r.total_files, r.total_lines = tf, tl
            label, method = self.SINGLE_TOOLS[tool]
            fn = getattr(self, method)
            # 全新 done 集合，不读 checkpoint，保证单工具独立、可复现
            fn(project_path, r, set())
            if r.findings:
                # 缺陷 4：单维度扫描也走交叉验证（_xval），保持与全量 audit 管线一致。
                # 单维度下所有 finding 同属一个 tool，_xval 的"多工具命中同一位置→HIGH"
                # 不会误升级（len(tools)>=2 不成立），因此无副作用；若未来单维度
                # 支持多工具则自动生效。交叉验证的局限在 MCP 描述中如实声明。
                self._xval(r)
                self._denoise(r)
            r.elapsed = round(time.time() - self._t0, 1)
        except Exception as e:
            r.errors.append(str(e))
        r.health_score = self._compute_health(r)
        r.elapsed = round(time.time() - self._t0, 1)
        return r

    def architecture(self, project_path: str, output_dir: str = None,
                     resume: bool = False) -> PipeResult:
        """架构图管线：GitNexus + Workflow"""
        self._t0 = time.time()
        r = PipeResult(project_path=project_path)
        out = output_dir or os.path.join(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__))), "coderef-report")

        try:
            tf, tl, analysis = self._scan(project_path)
            r.total_files, r.total_lines = tf, tl

            # 构建知识图谱
            self._build_kg(project_path, analysis)

            self._workflow(project_path, r)

            r.report = self._fmt(r, "架构分析报告")
            os.makedirs(out, exist_ok=True)
            fn = f"coderef_arch_{datetime.now().strftime('%Y%m%d_%H%M%S')}.md"
            r.report_path = os.path.join(out, fn)
            with open(r.report_path, "w", encoding="utf-8") as f:
                f.write(r.report)
        except Exception as e:
            r.errors.append(str(e))

        r.elapsed = round(time.time() - self._t0, 1)
        return r

    def docs(self, project_path: str, output_dir: str = None,
             resume: bool = False,
             wiki_style: str = "comprehensive",
             include_subprojects: bool = True,
             enable_agent_pointer: bool = False,
             cross_verify: bool = True,
             cross_entry_spec: str = "class:pipeline_runner:Pipe") -> PipeResult:
        """文档探查管线：Wiki

        wiki_style: Wiki 风格 (comprehensive / reference / tutorial / plain)
        include_subprojects: 是否同时为子项目生成独立 Wiki
        enable_agent_pointer: 是否在项目根维护 AGENTS.md 的 CodeRef Wiki 指针区块（R7）
        cross_verify: 是否对模块描述做静态交叉验证（确证徽章）
        cross_entry_spec: 交叉验证的入口（入口调用闭包为确证依据）
        """
        self._t0 = time.time()
        r = PipeResult(project_path=project_path)
        d = self._load(project_path) if resume else set()

        try:
            tf, tl, analysis = self._scan(project_path)
            r.total_files, r.total_lines = tf, tl

            # 构建知识图谱
            self._build_kg(project_path, analysis)

            self._wiki(project_path, r, d, output_dir,
                       wiki_style=wiki_style,
                       include_subprojects=include_subprojects,
                       enable_agent_pointer=enable_agent_pointer,
                       cross_verify=cross_verify,
                       cross_entry_spec=cross_entry_spec)

            r.report = self._fmt(r, "文档探查报告")
            os.makedirs(output_dir or os.path.join(os.path.dirname(os.path.dirname(
                os.path.abspath(__file__))), "coderef-report"), exist_ok=True)

            # 渲染 HTML 报告目录（Wiki 为主；若图谱/审计产物存在则一并纳入）
            html_out = os.path.join(output_dir or os.path.join(os.path.dirname(
                os.path.dirname(os.path.abspath(__file__))), "coderef-report"), "html")
            self._render_html(project_path, r, kg_stats=None,
                              output_dir=html_out)
        except Exception as e:
            r.errors.append(str(e))

        r.elapsed = round(time.time() - self._t0, 1)
        return r

    def docs_read(self, project_path: str, doc: Optional[str] = None,
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

    @staticmethod
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

    @staticmethod
    def _count_md(wiki_dir: str) -> int:
        """统计目录（含子目录）下 .md 文档数。"""
        n = 0
        for root, _, files in os.walk(wiki_dir):
            n += sum(1 for f in files if f.endswith(".md"))
        return n

    def _collect_dimension_states(self, project_path: str,
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
        audit_fp = self._findings_json_path(project_path, output_dir)
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
        wiki_dir = self._detect_wiki_dir(project_path)
        if wiki_dir:
            md_count = self._count_md(wiki_dir)
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

    def render_report(self, project_path: str,
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
            audit_fp = self._findings_json_path(project_path, output_dir)
            if not os.path.isfile(audit_fp):
                r.errors.append("未检测到审计 findings 产物（audit_findings.json），"
                                "已回退为一次全量审计以产出完整报告")
                return r, False
            # 检测各维度执行状态，供 HTML 透明化展示（避免"审计未执行"被当成"暂无发现"）
            r.dimension_states = self._collect_dimension_states(project_path, kg_stats, output_dir)
            # 恢复上次审计的 findings 与统计，避免"重渲染既有产物"时 index 审计卡片
            # 与 audit.html 明细全部为 0 / 空（社区反馈的聚合 HTML 全 0 bug）。
            self._load_findings_json(project_path, r, output_dir)
            # 若已有 Wiki 产物，挂到 wiki_result 供 HTML 渲染聚合 Wiki 页
            wiki_dir = self._detect_wiki_dir(project_path)
            if wiki_dir:
                class _WikiRef:
                    output_dir = wiki_dir
                r.wiki_result = _WikiRef()
            hr = self._render_html(project_path, r, kg_stats=kg_stats,
                                   output_dir=output_dir)
            if not hr.get("ok"):
                r.errors.append(hr.get("error", "渲染失败"))
            return r, True
        except Exception as e:
            r.errors.append(f"render_report: {e}")
            return r, False

    # ─── 审计 findings 结构化落盘 / 恢复 ─────────────────────────────
    # 背景：coderef_report 走 render_report 重渲染时，若用空 PipeResult 聚合，
    #   index.html 的审计卡片与 audit.html 明细会全部为 0 / "暂无发现"（社区反馈）。
    # 方案：audit() 落盘 markdown 的同时，把 findings 与统计序列化为 JSON；
    #   render_report 优先读取该 JSON 恢复到 PipeResult，再渲染 HTML，
    #   保证"重渲染既有产物"时审计内容完整，而不依赖对 markdown 的脆弱解析。

    @staticmethod
    def _finding_to_dict(f: "Finding") -> dict:
        return {
            "id": f.id, "tool": f.tool, "category": f.category,
            "severity": f.severity, "file_path": f.file_path, "line": f.line,
            "title": f.title, "detail": f.detail, "suggestion": f.suggestion,
            "tier": f.tier.value if f.tier else "low",
            "xval_by": list(f.xval_by or []),
            "line_start": f.line_start, "line_end": f.line_end,
            "kind": f.kind,
        }

    @staticmethod
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

    @staticmethod
    def _findings_json_path(project_path: str, out: Optional[str] = None) -> str:
        """审计 findings JSON 的固定落盘路径（优先调用方 out，其次标准 coderef-report）。

        文件名带项目哈希前缀，避免共享输出目录下多项目互相覆盖（跨项目串扰）。
        """
        fname = f"audit_findings_{Pipe._phash(project_path)}.json"
        if out:
            return os.path.join(out, fname)
        return os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "coderef-report", fname)

    def _dump_findings_json(self, r: PipeResult, out: str) -> None:
        """把 audit 的 findings 与统计序列化落盘，供 coderef_report 重渲染复用。"""
        try:
            data = {
                "version": 1,
                "scan_ts": r.scan_ts or "",
                "kg_built_at": r.kg_built_at or "",
                "total_files": r.total_files,
                "total_lines": r.total_lines,
                "scope_text": r.scope_text or "",
                "findings": [self._finding_to_dict(f) for f in r.findings],
            }
            fp = self._findings_json_path(r.project_path, out)
            os.makedirs(os.path.dirname(fp), exist_ok=True)
            tmp = fp + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            os.replace(tmp, fp)
        except Exception as e:
            r.errors.append(f"dump_findings_json: {e}")

    def _load_findings_json(self, project_path: str, r: PipeResult,
                            out: Optional[str] = None) -> bool:
        """从落盘的 findings JSON 恢复统计与 findings 到 r；无可用 JSON 返回 False。"""
        candidates = [
            self._findings_json_path(project_path, out),
            self._findings_json_path(project_path),
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
                r.findings = [self._finding_from_dict(d) for d in data.get("findings", [])]
                return True
            except Exception as e:
                logger.warning(f"加载管线缓存候选失败，继续尝试下一个: {e}")
                continue
        return False

    # ═══════════════════════════════════
    # 检测器
    # ═══════════════════════════════════

    def _gov(self, p: str, r: PipeResult, done: set):
        if "gov" in done: return
        try:
            importlib.invalidate_caches()
            pc = os.path.join(os.path.dirname(__file__), "__pycache__")
            if os.path.exists(pc):
                for f in os.listdir(pc):
                    if "governance_audit" in f or "shared_filter" in f:
                        os.remove(os.path.join(pc, f))
            from core import governance_audit as g, shared_filter as sf
            importlib.reload(sf); importlib.reload(g)
            a = g.GovernanceAuditor(); a.audit(p)
            ro = getattr(a, "report", None)
            if ro:
                for v in ro.violations:
                    # 缺陷 5：gov violations 的 title/detail 缺少数符号引用，导致
                    # extract_symbols(title+detail) 返回空、符号级证据核验(L3)失效。
                    # 把命中的代码行（line_content，含函数调用等符号）附加到 detail，
                    # 使符号核验可提取到具体函数/模块名。
                    hit_line = getattr(v, "line_content", "") or ""
                    det = v.detail
                    if hit_line:
                        det = f"{det}｜命中代码: {hit_line}"
                    r.findings.append(Finding(id=f"gov-{len(r.findings)}", tool="gov",
                        category=v.category, severity=v.severity,
                        file_path=v.file_path, line=v.line_number,
                        title=f"[{v.rule_id}] {v.rule_name}", detail=det,
                        suggestion=v.suggestion, tier=Tier.MEDIUM))
            done.add("gov"); self._save(p, list(done))
        except Exception as e: r.errors.append(f"gov: {e}")

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

    def _sca(self, p: str, r: PipeResult, done: set):
        if "sca" in done: return
        try:
            from core.sca_checker import SCAChecker
            c = SCAChecker(); c.scan(p)
            rep = getattr(c, "report", None)
            for dep in getattr(rep, "dependencies", []):
                for v in getattr(dep, "vulnerabilities", []):
                    r.findings.append(Finding(id=f"sca-{len(r.findings)}", tool="sca",
                        category="dependency_vuln", severity=getattr(v,"severity","medium"),
                        file_path=getattr(dep,"source_file",""), line=getattr(dep,"source_line",0),
                        title=f"{getattr(dep,'package','')} {getattr(dep,'version','')} - {getattr(v,'cve_id','')}",
                        detail=getattr(v,"summary",""),
                        suggestion=f"升级到 {(getattr(v,'fixed_version',None) or '最新可用版本')} 修复漏洞",
                        tier=self._tier_for(getattr(v,"severity","medium"))))
            # 无漏洞时也追加一条"扫描完成"汇总 finding，确保 sca 结果不被静默丢弃
            if not any(f.tool == "sca" for f in r.findings):
                scanned = getattr(rep, "scanned_deps", 0)
                r.findings.append(Finding(id=f"sca-{len(r.findings)}", tool="sca",
                    category="dependency_scan", severity="low",
                    file_path="", line=0,
                    title=f"依赖扫描完成：扫描 {scanned} 个依赖，未发现已知漏洞",
                    detail="SCA 工具已执行，本项目依赖未命中本地/OSV 已知漏洞库。",
                    suggestion="", tier=Tier.LOW))
            # 非 CVE 类供应链风险（供应链安装 / 依赖未锁定 / 弃用依赖）
            for risk in getattr(rep, "supply_chain_risks", []):
                r.findings.append(Finding(id=f"sca-{len(r.findings)}", tool="sca",
                    category="supply_chain_install", severity="high",
                    file_path=risk.get("file", ""), line=risk.get("line", 0),
                    title=f"供应链安装风险：运行时自动安装第三方包（{risk.get('file','')}:{risk.get('line',0)}）",
                    detail=risk.get("detail", ""),
                    suggestion="运行时禁止自动安装第三方包；改为启动前显式安装并锁定版本/hash 校验，包名来源需经人工确认",
                    tier=Tier.HIGH))
            for risk in getattr(rep, "unpinned_deps", []):
                r.findings.append(Finding(id=f"sca-{len(r.findings)}", tool="sca",
                    category="unpinned_dependency", severity="medium",
                    file_path=risk.get("source_file", ""), line=risk.get("source_line", 0),
                    title=f"依赖未锁定：{risk.get('package','')} {risk.get('version','')}",
                    detail=risk.get("detail", ""),
                    suggestion="为依赖固定精确版本并生成 lock 文件（如 requirements.lock/poetry.lock），保证构建可复现",
                    tier=Tier.MEDIUM))
            for risk in getattr(rep, "deprecated_deps", []):
                r.findings.append(Finding(id=f"sca-{len(r.findings)}", tool="sca",
                    category="deprecated_dependency", severity="medium",
                    file_path=risk.get("source_file", ""), line=risk.get("source_line", 0),
                    title=f"弃用依赖：{risk.get('package','')}（迁移至 {risk.get('migration','')}）",
                    detail=risk.get("detail", ""),
                    suggestion=f"迁移至官方替代 {risk.get('migration','')}",
                    tier=Tier.MEDIUM))
            done.add("sca"); self._save(p, list(done))
        except Exception as e: r.errors.append(f"sca: {e}")

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

    def _inn(self, p: str, r: PipeResult, done: set):
        if "inn" in done: return
        try:
            from core.innovation_propagation_detector import InnovationPropagationDetector
            # 实战修复：缺口检测 _generate_suggestion 对每个缺口无条件调用 LLM 且无上限，
            # 在含 vendored 库的大项目上会挂起数十分钟。审计管线改用纯结构对比（无 LLM）。
            d = InnovationPropagationDetector(); d.detect(p, use_llm=False)
            for s in getattr(d, "gaps", []):
                r.findings.append(Finding(id=f"inn-{len(r.findings)}", tool="inn",
                    category="propagation_gap", severity="medium",
                    file_path=getattr(s,"target_file",""), line=0,
                    title=f"{getattr(s,'target_module','')} 缺少 {getattr(getattr(s,'pattern',None),'pattern_name','')} 模式",
                    detail=getattr(s,"suggestion",""), suggestion=getattr(s,"suggestion",""),
                    tier=Tier.MEDIUM))
            done.add("inn"); self._save(p, list(done))
        except Exception as e: r.errors.append(f"inn: {e}")

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

    def _simp(self, p: str, r: PipeResult, done: set):
        if "simp" in done: return
        try:
            from core.code_simplifier import CodeSimplifier
            c = CodeSimplifier(); c.analyze(p)
            for s in getattr(c, "last_items", []) or []:
                lr = s.get("line_range", [0, 0]) if isinstance(s, dict) else [0, 0]
                line = lr[0] if isinstance(lr, (list, tuple)) and lr else 0
                r.findings.append(Finding(id=f"simp-{len(r.findings)}", tool="simp",
                    category=(s.get("category","") if isinstance(s, dict) else getattr(s,"category","")),
                    severity=(s.get("severity","medium") if isinstance(s, dict) else getattr(s,"severity","medium")),
                    file_path=(s.get("file_path","") if isinstance(s, dict) else getattr(s,"file_path","")),
                    line=line,
                    title=(s.get("title","") if isinstance(s, dict) else getattr(s,"title","")),
                    detail=(s.get("current","") if isinstance(s, dict) else getattr(s,"current","")),
                    suggestion=(s.get("suggestion","") if isinstance(s, dict) else getattr(s,"suggestion","")),
                    tier=self._tier_for((s.get("severity","medium") if isinstance(s, dict) else getattr(s,"severity","medium")))))
            done.add("simp"); self._save(p, list(done))
        except Exception as e: r.errors.append(f"simp: {e}")

    def _matu(self, p: str, r: PipeResult, done: set):
        if "matu" in done: return
        try:
            from core.project_maturity_checker import ProjectMaturityChecker
            c = ProjectMaturityChecker()
            rep = c.check(p)
            for s in getattr(rep, "checks", []):
                if getattr(s, "status", "") == "pass":
                    continue  # 只上报未通过的成熟度检查
                # 成熟度检查含两类：缺陷/缺失(kind=defect) 与 工程化改进建议(kind=suggestion)。
                # 建议项(a.k.a 工程化 warn)不以安全缺陷同级别的 severity 进入缺陷汇总，
                # 通过 kind="advice" + severity="info" 标记，供汇总/报告层区分"建议项"与"缺陷项"。
                # getattr 兜底，即使 detector 未提供 kind 也保持向后兼容。
                mk = getattr(s, "kind", "defect")
                is_advice = mk == "suggestion"
                sev = "info" if is_advice else "medium"
                r.findings.append(Finding(id=f"matu-{len(r.findings)}", tool="matu",
                    category=getattr(s,"category",""), severity=sev,
                    file_path="", line=0,
                    title=f"[{getattr(s,'check_id','')}] {getattr(s,'name','')} - {getattr(s,'status','')}",
                    detail=getattr(s,"detail",""), suggestion=getattr(s,"suggestion",""),
                    kind="advice" if is_advice else "defect",
                    tier=self._tier_for(sev)))
            done.add("matu"); self._save(p, list(done))
        except Exception as e: r.errors.append(f"matu: {e}")

    def _workflow(self, p: str, r: PipeResult):
        try:
            from core.workflow_graph import WorkflowGraph
            html = WorkflowGraph().generate(project_path=p)
            r.report_path = html
        except Exception as e: r.errors.append(f"workflow: {e}")

    def _wiki(self, p: str, r: PipeResult, done: set, output_dir: str = None,
              wiki_style: str = "comprehensive",
              include_subprojects: bool = True,
              enable_agent_pointer: bool = False,
              cross_verify: bool = True,
              cross_entry_spec: str = "class:pipeline_runner:Pipe"):
        if "wiki" in done: return
        try:
            from core.wiki_generator import WikiGenerator
            # 尊重调用方指定的输出目录；未指定时回退到默认 txt/
            wo = output_dir or os.path.join(os.path.dirname(os.path.dirname(
                os.path.abspath(__file__))), "txt")
            wg = WikiGenerator()
            gres = wg.generate(p, output_dir=wo, wiki_style=wiki_style,
                               include_subprojects=include_subprojects,
                               enable_agent_pointer=enable_agent_pointer,
                               cross_verify=cross_verify,
                               cross_entry_spec=cross_entry_spec)
            # 把 Wiki 生成失败明细带入管线结果，让 _fmt / MCP 层能感知"部分文档生成失败"，
            # 避免部分阶段失败却仍对外标记为 fully completed。
            for e in getattr(gres, "errors", []) or []:
                r.errors.append(f"wiki: {e}")
            r.wiki_result = gres
            done.add("wiki"); self._save(p, list(done))
        except Exception as e:
            r.errors.append(f"wiki: {e}")

    @staticmethod
    def _latest_report(out: str, project_path: str = "") -> Optional[str]:
        """返回输出目录下最近一份审计报告（coderef_audit_*.md），无则 None。

        project_path 非空时按项目哈希前缀过滤，避免共享输出目录下跨项目串扰
        （旧版仅按 mtime 取最新，会把其他项目的报告误当成本项目结论）。
        """
        try:
            if not os.path.isdir(out):
                return None
            prefix = (f"coderef_audit_{Pipe._phash(project_path)}_"
                      if project_path else "coderef_audit_")
            cands = [os.path.join(out, f) for f in os.listdir(out)
                     if f.startswith(prefix) and f.endswith(".md")]
            if not cands:
                return None
            return max(cands, key=os.path.getmtime)
        except Exception:
            # 报告文件定位失败视为无历史报告
            return None

    def _reuse_no_change(self, project_path: str, r: PipeResult,
                         out: str, _prog) -> bool:
        """no_change 策略：复用既有图谱与最近审计报告，不重扫代码。

        返回 True 表示复用成功（调用方应直接返回）；False 表示无既有结论可复用。
        """
        try:
            from core.code_knowledge_graph import load_knowledge_graph
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
            logger.info(f"[audit] 策略=no_change，复用既有结论，未重扫")
            return True
        except Exception as e:
            r.errors.append(f"reuse_no_change: {e}")
            return False

    # ═══════════════════════════════════
    # 交叉验证 + 格式化
    # ═══════════════════════════════════

    def _xval(self, r: PipeResult):
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

    # ═══════════════════════════════════
    # 自动降噪（零 LLM / 零白名单）
    # ═══════════════════════════════════

    # 降噪规则库：每个 rule 检测是否为已知误报模式
    NOISE_RULES = {
        # MD5 for project hashing, not crypto — IRON-SEC-10 in governance。
        # 仅抑制"纯路径/文件名哈希"误报；命中代码含 sign/签名/token/密钥/认证等
        # 安全场景词（如 chatwiki 的 tool.MD5(body+timestamp+nonce+CodeRunKey) 签名）
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

    # 爆发式重复阈值：同 tool + category > N → 合并
    BURST_THRESHOLD = 8
    # 邻近行合并窗口：同 file + tool + category 的行号差 < N → 合并
    ADJACENT_LINE_WINDOW = 5

    def _denoise(self, r: PipeResult):
        """自动降噪：AI白名单 + 规则去重 + 抑制 + 降级"""
        if not r.findings:
            return

        suppressed = 0
        downgraded = 0
        wl_suppressed = 0
        kept = []

        # 加载 AI 白名单
        wl = self._load_whitelist(r.project_path)

        # ── 第一轮：AI 白名单 + 规则匹配 ──
        for f in r.findings:
            if self._match_whitelist(f, wl):
                wl_suppressed += 1
                continue
            matched_rule = self._match_noise_rule(f)
            if matched_rule:
                action = matched_rule["action"]
                if action == "suppress":
                    suppressed += 1
                    continue
                elif action == "downgrade":
                    f.tier = Tier.LOW
                    downgraded += 1
            kept.append(f)

        r.findings = kept

        # ── 第二轮：邻近行合并（同 file + tool + category) ──
        r.findings = self._dedup_adjacent(r.findings)

        # ── 第三轮：爆发式合并（同 tool + category > 阈值 → 保留 1 条 + 摘要）──
        r.findings = self._burst_merge(r.findings)

        # 记录降噪统计
        if suppressed or downgraded or wl_suppressed:
            setattr(r, 'noise_suppressed', suppressed)
            setattr(r, 'noise_downgraded', downgraded)
            setattr(r, 'wl_suppressed', wl_suppressed)

    @staticmethod
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

    def _match_noise_rule(self, f: Finding) -> dict:
        """f 是否匹配任一降噪规则"""
        title_lower = f.title.lower()
        cat_lower = f.category.lower()
        file_lower = os.path.basename(f.file_path).lower() if f.file_path else ""

        for name, rule in self.NOISE_RULES.items():
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

    @staticmethod
    def _dedup_adjacent(findings: List[Finding]) -> List[Finding]:
        """同文件 + 同规则 + 邻行 → 合并为 1 条"""
        if not findings:
            return findings
        # 提取 title 中的 [risk_id]（与 _burst_merge 分组键一致，避免不同风险类型被误合并）
        def _rk(f: Finding) -> str:
            t = (f.title or "").lstrip()
            if t.startswith("[") and "]" in t:
                return t[1:t.index("]")]
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

    @staticmethod
    def _burst_merge(findings: List[Finding]) -> List[Finding]:
        """同 tool + category 超过阈值 → 保留 1 条 + 统计摘要

        聚合时保留组内**最高** tier/severity（而非无条件降级为 LOW），
        并把真实数量与全部位置写入 count/locations，避免严重度失真、
        位置丢失、数量低估（对应证据审计缺陷 1/2/3）。

        分组键在 (tool, category) 之上叠加 title 中的 [risk_id]（如 agent 的
        [AGENT-SEC-30]、gov 的 [GOV-xxx]），使不同风险类型独立成条，避免
        高价值缺陷被同 category 的 flood 合并吞掉顶层位置（chatwiki agent
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
                return t[1:t.index("]")]
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

    def _fmt(self, r: PipeResult, title: str) -> str:
        # 修复：报告头时间取自错误变量，导致 elapsed 显示 0.0s。
        # 若调用方尚未写入 r.elapsed（在 _fmt 之后才赋值），此处实时兜底计算，
        # 保证报告头展示真实耗时。
        if r.elapsed == 0.0 and getattr(self, "_t0", None):
            r.elapsed = round(time.time() - self._t0, 1)
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
            f"| 🔴 HIGH | 🟡 MEDIUM | ⚪ LOW | 💡 建议 |",
            f"|----------|------------|---------|---------|",
            f"| {_weighted(h)} | {_weighted(m)} | {_weighted(l)} | {_weighted(adv)} |",
            "",
        ]
        if r.scope_text:
            lines.append(f"> 📋 审计范围: {r.scope_text}")
            lines.append("")
        ns = getattr(r, 'noise_suppressed', 0)
        nd = getattr(r, 'noise_downgraded', 0)
        wl = getattr(r, 'wl_suppressed', 0)
        if ns or nd or wl:
            parts = []
            if ns: parts.append(f"抑制 {ns} 条已知误报")
            if nd: parts.append(f"降级 {nd} 条低置信度")
            if wl: parts.append(f"白名单过滤 {wl} 条")
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

    # ═══════════════════════════════════
    # 知识图谱查询（供 MCP Server 调用）
    # ═══════════════════════════════════

    @staticmethod
    def kg_query(project_path: str, query_type: str, **kwargs) -> dict:
        """查询项目知识图谱。

        query_type:
          - stats: 获取统计信息
          - entity: 按名称搜索实体 (name, type?)
          - callers: 查询调用者 (func_name)
          - callees: 查询被调用者 (func_name)
          - impact: 修改影响分析 (file_path)
          - relations: 查询节点关系 (node_id)
          - file_entities: 查询文件实体 (file_path)
          - search: 全文搜索 (keyword)
          - call_graph: 调用链子图 (func_name, depth?)
        """
        from core.code_knowledge_graph import load_knowledge_graph
        kg = load_knowledge_graph(project_path)
        if not kg:
            return {"error": "知识图谱不存在，请先运行 coderef_audit/coderef_docs/coderef_architecture"}

        # 元信息：图谱构建时间，供调用方识别数据新旧，避免把旧图谱当成本次审计结果
        kg_built_at = kg.get_built_at()
        meta = {"kg_built_at": kg_built_at,
                "kg_note": f"知识图谱构建于 {kg_built_at}，仅当本次运行 coderef_audit/coderef_docs/coderef_architecture 后才会重建；若代码有改动，请先重建图谱再查询。" if kg_built_at else "知识图谱缺少构建时间标记"}

        def _wrap(data: dict) -> dict:
            data.update(meta)
            return data

        try:
            if query_type == "stats":
                return _wrap(kg.get_stats())
            elif query_type == "entity":
                r = kg.query_entity(kwargs.get("name", ""), kwargs.get("type"))
                return _wrap({"nodes": [n.to_dict() for n in r.nodes], "total": r.total})
            elif query_type == "callers":
                r = kg.query_callers(kwargs.get("func_name", ""))
                return _wrap({"nodes": [n.to_dict() for n in r.nodes], "total": r.total})
            elif query_type == "callees":
                r = kg.query_callees(kwargs.get("func_name", ""))
                return _wrap({"nodes": [n.to_dict() for n in r.nodes], "total": r.total})
            elif query_type == "impact":
                r = kg.query_impact(kwargs.get("file_path", ""))
                return _wrap({"nodes": [n.to_dict() for n in r.nodes], "total": r.total})
            elif query_type == "relations":
                r = kg.query_relations(kwargs.get("node_id", ""))
                return _wrap({"nodes": [n.to_dict() for n in r.nodes],
                              "edges": [e.to_dict() for e in r.edges], "total": r.total})
            elif query_type == "file_entities":
                r = kg.query_file_entities(kwargs.get("file_path", ""))
                return _wrap({"nodes": [n.to_dict() for n in r.nodes], "total": r.total})
            elif query_type == "search":
                r = kg.search(kwargs.get("keyword", ""), kwargs.get("limit", 30))
                return _wrap({"nodes": [n.to_dict() for n in r.nodes], "total": r.total})
            elif query_type == "call_graph":
                r = kg.get_call_graph(kwargs.get("func_name", ""), kwargs.get("depth", 2))
                return _wrap({"nodes": [n.to_dict() for n in r.nodes],
                              "edges": [e.to_dict() for e in r.edges], "total": r.total})
            else:
                return {"error": f"未知查询类型: {query_type}，支持: stats/entity/callers/callees/impact/relations/file_entities/search/call_graph"}
        finally:
            kg.close()
