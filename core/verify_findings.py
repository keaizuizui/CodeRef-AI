# -*- coding: utf-8 -*-
"""
verify_findings — LLM / CodeRabbit 论断的确定性核验（驾驭翼咽喉）

目标读者：编程 AI（调用方）与非编程人员（最终看到人话结论的人）。
核心问题：CodeRabbit / 大模型给出了一条"论断"（finding），它到底靠不靠得住？
本工具用知识图谱 + 静态原语去核验论断引用的代码目标是否真实存在、是否在调用
关系内、是否在指定入口管线内——把"语义找点"的成果，第一次接进确定性治理闭环。

定位：纯静态、确定性。不依赖 LLM。核验的是"论断引用的代码目标是否真实存在"
这一类可确证的事实，不是"论断的语义判断是否正确"（那需要 LLM 在真实上下文复核）。

诚实话解读护栏（本模块的立身之本）：
- 标签来源分离：verdict（确证/证伪/部分确证/无法核验）只由本模块的确定性逻辑
  计算，调用方 AI 只能提供论断文本与可选的 symbols 提示，无权改变 verdict。
- 诚实默认值：无确定性证据支持 → 一律 unverifiable / 存疑，绝不默认 confirmed。
- 缺失 ≠ 错误：静态查不到（图谱不完整/动态调用/反射）绝不误判为"代码不存在"，
  只标记"无法核验，需进一步核验"。
- 出口明确 disclaimer：confirmed 只代表"论断引用的代码目标真实存在"，不代表
  "论断的语义结论正确"。

集成方式：作为 MCP 工具 coderef_verify_findings 暴露。
"""

import os
import re
from typing import Dict, List, Optional, Set, Tuple

try:
    from loguru import logger
except Exception:
    logger = None


def _log(msg: str):
    if logger:
        logger.info(f"[verify_findings] {msg}")


# ═══════════════════════════════════════════════════════════════════
# 诚实话标签判定层（标签来源分离）
# ═══════════════════════════════════════════════════════════════════

# 合法的确定性标签集合（LLM 无权产生新标签，只能被此集合约束）
VERDICT_CONFIRMED = "confirmed"            # 确证：论断引用的代码目标真实存在
VERDICT_REFUTED = "refuted"                # 证伪：论断引用的代码目标在项目中不存在
VERDICT_PARTIAL = "partially_confirmed"    # 部分确证：部分符号存在
VERDICT_UNVERIFIABLE = "unverifiable"      # 无法核验：无引用/图谱缺失/静态查不到

VERDICT_LABEL_ZH = {
    VERDICT_CONFIRMED: "确证",
    VERDICT_REFUTED: "证伪",
    VERDICT_PARTIAL: "部分确证",
    VERDICT_UNVERIFIABLE: "存疑",
}

# 可信度序号（用于排序；unverifiable 永远最低，宁可低估不可高估）
VERDICT_RANK = {
    VERDICT_CONFIRMED: 3,
    VERDICT_PARTIAL: 2,
    VERDICT_REFUTED: 1,
    VERDICT_UNVERIFIABLE: 0,
}


class EvidenceLabeler:
    """确定性标签判定层 —— 让"诚实"成为结构，而不是自觉。

    本类只依赖确定性输入（文件存在性、符号存在性、调用关系、管线关系），
    产出统一的 verdict + reason。任何调用方（含未来的人话解读平台）都应
    通过本层获取标签，禁止绕开本层由 LLM 直接打标签。
    """

    # 可核验的引用类型（用于判定"是否有可核验目标"）
    @staticmethod
    def has_verifiable_ref(file: str, symbols: List[str]) -> bool:
        return bool(file and file.strip()) or bool(symbols)

    @staticmethod
    def decide(
        has_ref: bool,
        file_found: Optional[bool],
        symbols_found: List[str],
        symbols_missing: List[str],
        in_pipeline: Optional[bool] = None,
        entry: Optional[str] = None,
        graph_exists: bool = True,
    ) -> Tuple[str, str]:
        """综合判定 verdict + 中文 reason。

        判定顺序即诚实优先级的体现：
        1. 图谱缺失 → unverifiable（整个核验无依据）
        2. 无任何可核验引用 → unverifiable
        3. 文件被证伪 → refuted
        4. 符号证伪 → unverifiable（找不到目标，不臆断"不存在还是查不到"）
        5. 符号确证 → confirmed / partially_confirmed
        6. 入口管线外 → 降级 partially_confirmed 并说明
        """
        if not graph_exists:
            return VERDICT_UNVERIFIABLE, "知识图谱不存在，无法进行确定性核验（请先运行 coderef_audit 或 coderef_memory_sync 构建图谱）"
        if not has_ref:
            return VERDICT_UNVERIFIABLE, "论断未包含可静态核验的符号或文件引用，无法确证"
        if file_found is False:
            return VERDICT_REFUTED, "论断所指文件在项目中不存在"
        if symbols_found and not symbols_missing:
            base = (VERDICT_CONFIRMED, "论断引用的代码符号在项目中真实存在")
        elif symbols_found and symbols_missing:
            base = (VERDICT_PARTIAL, f"部分代码符号存在（{len(symbols_found)} 个确证，{len(symbols_missing)} 个未找到）")
        elif symbols_missing and not symbols_found:
            if file_found is True:
                # 文件真实存在是确定性事实，但符号未在图谱中核验到（图谱可能旧/不完整）
                return VERDICT_PARTIAL, "论断所指文件存在，但代码符号未能在静态图谱中核验到（图谱可能不完整，不代表符号不存在）"
            return VERDICT_UNVERIFIABLE, "未能找到论断所指的代码符号（静态图谱可能不完整，不代表一定不存在）"
        else:
            # 只有文件引用且文件存在，无符号可核验
            return VERDICT_CONFIRMED, "论断所指文件在项目中存在，但未提取到可进一步核验的符号"

        if in_pipeline is not None and not in_pipeline:
            entry_name = entry or "指定入口"
            return VERDICT_PARTIAL, f"{base[1]}，但引用的符号不在入口 '{entry_name}' 的静态调用管线内"
        return base

    @staticmethod
    def make(finding_verdict: str, reason: str, evidence: Dict) -> Dict:
        """把一个 verdict 组装为结构化核验结果（含人话标签，供下游使用）。"""
        return {
            "verdict": finding_verdict,
            "label_zh": VERDICT_LABEL_ZH.get(finding_verdict, VERDICT_LABEL_ZH[VERDICT_UNVERIFIABLE]),
            "rank": VERDICT_RANK.get(finding_verdict, 0),
            "reason": reason,
            "evidence": evidence,
        }


# ═══════════════════════════════════════════════════════════════════
# 符号启发式提取（确定性；仅作补充，显式 symbols 优先）
# ═══════════════════════════════════════════════════════════════════

# 常见停用词（英文高频词 + Python 关键字 + 论断常见废话词）
_STOPWORDS = {
    "the", "and", "but", "for", "with", "this", "that", "these", "those",
    "from", "into", "onto", "within", "which", "when", "where", "what",
    "while", "there", "here", "than", "then", "else", "also", "only",
    "already", "always", "never", "often", "should", "could", "would",
    "might", "must", "may", "can", "will", "are", "was", "were", "has",
    "have", "had", "does", "did", "do", "is", "are", "be", "been", "being",
    "code", "codes", "function", "functions", "method", "methods", "class",
    "classes", "module", "modules", "file", "files", "line", "lines",
    "error", "errors", "bug", "bugs", "issue", "issues", "risk", "risks",
    "potential", "possibly", "likely", "probably", "seems", "appears",
    "usage", "use", "used", "using", "call", "called", "calls", "return",
    "returns", "value", "values", "config", "configuration", "setting",
    "settings", "default", "example", "sample", "python", "object", "instance",
    "argument", "arguments", "parameter", "parameters", "result", "results",
    "output", "input", "data", "handle", "handles", "process", "processing",
    "system", "application", "project", "source", "target", "check", "checks",
    "block", "blocks", "statement", "statement", "expression", "cause", "leads",
    "not", "no", "yes", "if", "case", "cases", "log", "logs", "message", "type",
    "types", "term", "terms", "access", "security", "potential", "vulnerability",
    "vulnerabil", "exposure", "issue", "feature", "feature", "features",
}

# Python 保留字（不应作为符号）
_PY_KEYWORDS = {
    "and", "as", "assert", "async", "await", "break", "class", "continue",
    "def", "del", "elif", "else", "except", "finally", "for", "from", "global",
    "if", "import", "in", "is", "lambda", "nonlocal", "not", "or", "pass",
    "raise", "return", "try", "while", "with", "yield", "True", "False", "None",
}

_IDENT_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_CALL_RE = re.compile(r"([A-Za-z_][A-Za-z0-9_]*)\s*\(")
_DOTTED_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\.[A-Za-z_][A-Za-z0-9_]*")


def _is_symbol_like(word: str) -> bool:
    """判断一个词是否'像代码符号'：含下划线/驼峰拼接/全大写宏。"""
    if len(word) < 2:
        return False
    low = word.lower()
    if low in _PY_KEYWORDS or low in _STOPWORDS:
        return False
    # 含下划线：snake_case 或 CONSTANT
    if "_" in word:
        return True
    # 驼峰拼接（含大写字母且非纯大写单词）
    if word != low and sum(c.isupper() for c in word) >= 1 and len(word) >= 3:
        return True
    # 纯大写宏（长度>=2 全大写）
    if word.isupper() and len(word) >= 2:
        return True
    return False


def extract_symbols(text: str, limit: int = 12) -> List[str]:
    """从论断文本中启发式提取候选符号名（去重、保留顺序）。

    显式传入的 symbols 字段优先；本函数仅作 fallback，且只提取"明显像符号"的词，
    避免把自然语言误当符号。提取不全不报错——无符号时 verdict 会诚实标记 unverifiable。
    """
    if not text:
        return []
    out: List[str] = []
    seen: Set[str] = set()

    def _add(w: str):
        w = w.strip()
        if not w or w in seen:
            return
        if _is_symbol_like(w):
            seen.add(w)
            out.append(w)

    # 优先级 1：带点调用模块.函数（最高）
    for m in _DOTTED_RE.findall(text):
        _add(m)
    # 优先级 2：带括号调用 函数(...)
    for m in _CALL_RE.findall(text):
        _add(m)
    # 优先级 3：普通标识符
    for m in _IDENT_RE.findall(text):
        _add(m)
    return out[:limit]


# ═══════════════════════════════════════════════════════════════════
# 核验引擎
# ═══════════════════════════════════════════════════════════════════

def _kg_db_path(project_path: str) -> str:
    from core.code_knowledge_graph import CodeKnowledgeGraph
    try:
        return CodeKnowledgeGraph(project_path).db_path
    except Exception:
        return ""


def _ensure_trailing_slash(path: str) -> str:
    return path if path.endswith(("\\", "/")) else path + os.sep


class FindingsVerifier:
    """确定性核验器：加载图谱 + 静态原语，逐条核验论断。"""

    def __init__(self, project_path: str, db_path: Optional[str] = None):
        self.project_path = os.path.abspath(project_path)
        self.db_path = db_path or _kg_db_path(project_path)
        self.graph_exists = bool(self.db_path) and os.path.isfile(self.db_path)
        self.kg = None
        self.flow = None
        if self.graph_exists:
            try:
                from core.code_knowledge_graph import CodeKnowledgeGraph
                self.kg = CodeKnowledgeGraph(self.project_path, db_path=self.db_path)
            except Exception as exc:
                _log(f"图谱加载失败: {exc}")
                self.graph_exists = False

    # ─── 文件核验 ───
    def _file_found(self, file: str) -> Optional[bool]:
        if not file or not file.strip():
            return None
        raw = file.strip()
        # 构造候选：相对路径尝试拼接项目根；绝对路径直接使用
        candidates = [raw]
        if not os.path.isabs(raw):
            candidates.append(self.project_path + os.sep + raw)
        # 归一化并做项目根校验：拒绝项目目录之外的路径（含绝对路径与 ../ 穿越）
        project_root = os.path.realpath(self.project_path)
        norm = []
        for c in candidates:
            rp = os.path.realpath(os.path.normpath(c))
            try:
                inside = os.path.commonpath([rp, project_root]) == project_root
            except ValueError:
                # 不同盘符（如 C: vs D:）会抛 ValueError，按"项目外"处理
                inside = False
            norm.append((rp, inside))
        for rp, inside in norm:
            if inside and os.path.isfile(rp):
                return True
        # 图谱中存在该文件实体也算（同样限定在项目内才认）
        if self.kg is not None:
            try:
                res = self.kg.query_file_entities(file)
                if res.total > 0:
                    if not os.path.isabs(raw):
                        return True
                    # 绝对路径：仅当图谱实体文件落在项目根内才算存在
                    try:
                        inside = os.path.commonpath(
                            [os.path.realpath(raw), project_root]) == project_root
                    except ValueError:
                        inside = False
                    if inside:
                        return True
            except Exception:
                # 路径判断失败按"项目外"保守处理
                pass
        return False

    # ─── 符号核验 ───
    def _symbol_probe(self, symbol: str) -> Dict:
        """核验单个符号：存在性 + 调用者数 + 被调用者数。"""
        if self.kg is None:
            return {"symbol": symbol, "found": False, "callers": 0, "callees": 0,
                    "node": None}
        try:
            ent = self.kg.query_entity(symbol)
            found = ent.total > 0
            node = None
            if found and ent.nodes:
                n = ent.nodes[0]
                node = f"{getattr(n, 'name', symbol)} ({getattr(n, 'file_path', '')}:{getattr(n, 'start_line', 0)})"
            callers = self.kg.query_callers(symbol).total if found else 0
            callees = self.kg.query_callees(symbol).total if found else 0
            return {"symbol": symbol, "found": found, "callers": callers,
                    "callees": callees, "node": node}
        except Exception as exc:
            _log(f"符号核验失败 {symbol}: {exc}")
            return {"symbol": symbol, "found": False, "callers": 0, "callees": 0,
                    "node": None}

    # ─── 管线核验 ───
    def _in_pipeline(self, symbols: List[str], entry: str) -> Optional[bool]:
        """若指定 entry，判定符号是否在入口静态调用管线内。"""
        if not entry or not self.graph_exists:
            return None
        try:
            from core.flow_verify import FlowVerifier
            if self.flow is None:
                self.flow = FlowVerifier(self.db_path)
            reach_any = None
            for sym in symbols:
                hits = self.flow.match_step_nodes(sym)
                if not hits:
                    continue
                node = self.flow.find_entry(entry)
                if not node:
                    return None
                from core.graph_closure import downstream
                root_reach = downstream(self.flow.adj, node)
                in_pipe = any(h in root_reach for h in hits)
                if in_pipe:
                    return True
                reach_any = False
            return reach_any  # 有符号但都不在管线内 → False；无符号 → None
        except Exception as exc:
            _log(f"管线核验失败: {exc}")
            return None

    @staticmethod
    def _normalize_symbols(finding: Dict, title: str, detail: str) -> List[str]:
        """归一化显式 symbols；无合法符号时回退到文本启发式提取。

        健壮化：symbols 合法形态为「字符串」或「字符串列表」；数字/字典/None/
        含非字符串元素的集合一律视为无显式符号，回退到文本启发式提取，绝不抛异常。
        """
        raw_symbols = finding.get("symbols") or []
        if isinstance(raw_symbols, str):
            raw_symbols = [s.strip() for s in re.split(r"[,\s;]+", raw_symbols) if s.strip()]
        elif isinstance(raw_symbols, (list, tuple)):
            raw_symbols = [s.strip() for s in raw_symbols
                           if isinstance(s, str) and s.strip()]
        else:
            raw_symbols = []
        symbols_all = list(raw_symbols)
        if not symbols_all:
            symbols_all = extract_symbols(title + " " + detail)
        return symbols_all

    def _probe_symbols(self, symbols_all: List[str]):
        """逐符号核验，返回 (probes, 已找到符号, 未找到符号)。"""
        probes = [self._symbol_probe(s) for s in symbols_all]
        symbols_found = [p["symbol"] for p in probes if p["found"]]
        symbols_missing = [p["symbol"] for p in probes if not p["found"]]
        return probes, symbols_found, symbols_missing

    # ─── 单条核验 ───
    def verify_one(self, finding: Dict, entry: Optional[str] = None) -> Dict:
        title = str(finding.get("title") or "")
        detail = str(finding.get("detail") or "")
        file = str(finding.get("file") or "").strip()
        line = finding.get("line")

        # 显式 symbols 优先，否则启发式提取
        symbols_all = self._normalize_symbols(finding, title, detail)

        # 文件核验
        file_found = self._file_found(file) if file else None

        # 符号核验
        probes, symbols_found, symbols_missing = self._probe_symbols(symbols_all)

        # 管线核验
        in_pipeline = self._in_pipeline(symbols_found, entry) if entry else None

        # 诚实标签判定（来源分离的核心）
        has_ref = EvidenceLabeler.has_verifiable_ref(file, symbols_all)
        verdict, reason = EvidenceLabeler.decide(
            has_ref=has_ref,
            file_found=file_found,
            symbols_found=symbols_found,
            symbols_missing=symbols_missing,
            in_pipeline=in_pipeline,
            entry=entry,
            graph_exists=self.graph_exists,
        )

        evidence = {
            "graph_exists": self.graph_exists,
            "file": file or None,
            "file_found": file_found,
            "line": line,
            "symbols": probes,
            "entry": entry,
            "in_pipeline": in_pipeline,
        }
        return {
            "title": title,
            "rule": str(finding.get("rule") or finding.get("rule_id") or ""),
            "severity": str(finding.get("severity") or ""),
            "file": file or None,
            "line": line,
            **EvidenceLabeler.make(verdict, reason, evidence),
        }

    # ─── 资源释放（每次核验都关闭打开着的图谱/管线资源，避免连接泄漏）───
    def _close(self) -> None:
        """释放 CodeKnowledgeGraph（sqlite 连接）与 FlowVerifier 持有的资源。"""
        kg, self.kg = self.kg, None
        if kg is not None:
            try:
                close = getattr(kg, "close", None)
                if callable(close):
                    close()
            except Exception as exc:
                _log(f"关闭知识图谱资源失败: {exc}")
        self.flow = None

    # ─── 批量核验 ───
    def verify(self, findings: List[Dict], entry: Optional[str] = None) -> Dict:
        try:
            return self._verify(findings, entry=entry)
        finally:
            self._close()

    def _verify(self, findings: List[Dict], entry: Optional[str] = None) -> Dict:
        if not self.graph_exists:
            return {
                "ok": False,
                "graph_stats": {"has_kg": False, "db": self.db_path},
                "summary": (f"知识图谱不存在({self.db_path or '未定位'})，"
                            f"请先运行 coderef_audit 或 coderef_memory_sync 构建知识图谱后再核验"),
                "results": [],
            }
        results = [self.verify_one(f, entry=entry) for f in findings if isinstance(f, dict)]
        n_confirmed = sum(1 for r in results if r["verdict"] == VERDICT_CONFIRMED)
        n_refuted = sum(1 for r in results if r["verdict"] == VERDICT_REFUTED)
        n_partial = sum(1 for r in results if r["verdict"] == VERDICT_PARTIAL)
        n_unverifiable = sum(1 for r in results if r["verdict"] == VERDICT_UNVERIFIABLE)
        return {
            "ok": True,
            "graph_stats": {"has_kg": True, "db": self.db_path},
            "count": len(results),
            "tally": {
                "confirmed": n_confirmed,
                "refuted": n_refuted,
                "partially_confirmed": n_partial,
                "unverifiable": n_unverifiable,
            },
            "results": results,
            "summary": (
                f"共核验 {len(results)} 条论断：确证 {n_confirmed}，证伪 {n_refuted}，"
                f"部分确证 {n_partial}，无法核验 {n_unverifiable}。"
                f"确证仅代表论断引用的代码目标真实存在，不代表语义结论正确。"
            ),
        }


# ═══════════════════════════════════════════════════════════════════
# 顶层接口（MCP handler 调用）
# ═══════════════════════════════════════════════════════════════════

def verify_findings(project_path: str, findings: List[Dict],
                    entry: Optional[str] = None,
                    db_path: Optional[str] = None) -> Dict:
    """批量核验 LLM / CodeRabbit 论断。

    Args:
        project_path: 目标项目路径（自动定位知识图谱）。
        findings: 论断列表，每条含 title（必填）、detail/file/line/rule/severity/symbols（可选）。
        entry: 可选入口符号（模块.函数），用于核验符号是否在关键管线内。
        db_path: 显式指定知识图谱数据库（测试用）；缺省自动定位。

    Returns:
        结构化核验结果 dict（含 verdict 与证据链）。
    """
    return FindingsVerifier(project_path, db_path=db_path).verify(findings or [], entry=entry)


# ═══════════════════════════════════════════════════════════════════
# 渲染
# ═══════════════════════════════════════════════════════════════════

def render_report(result: Dict) -> str:
    """纯文本报告（终端/日志可读）。"""
    lines = []
    lines.append("论断核验报告" + "=" * 3)
    gs = result.get("graph_stats", {})
    if not gs.get("has_kg"):
        lines.append(result.get("summary", "知识图谱不存在，无法核验"))
        return "\n".join(lines)
    lines.append(f"图谱: {gs.get('db', '')}")
    lines.append(f"条目: {result.get('count', 0)} 条"
                 f" | 确证 {result.get('tally', {}).get('confirmed', 0)}"
                 f" | 证伪 {result.get('tally', {}).get('refuted', 0)}"
                 f" | 部分确证 {result.get('tally', {}).get('partially_confirmed', 0)}"
                 f" | 存疑 {result.get('tally', {}).get('unverifiable', 0)}")
    lines.append("")
    for i, r in enumerate(result.get("results", []), 1):
        mark = {"confirmed": "[OK]", "refuted": "[X]", "partially_confirmed": "[~]",
                "unverifiable": "[?]"}[r["verdict"]]
        lines.append(f"{mark} #{i}「{r['title']}」 {r['label_zh']}")
        lines.append(f"    原因: {r['reason']}")
        if r.get("file"):
            lines.append(f"    文件: {r['file']}"
                         f"{(':' + str(r['line'])) if r.get('line') is not None else ''}")
        ev = r.get("evidence", {})
        found = [p["symbol"] for p in ev.get("symbols", []) if p["found"]]
        if found:
            lines.append(f"    确证符号: {', '.join(found)}")
    lines.append("")
    lines.append("图例: [OK]=确证(引用目标真实存在); [X]=证伪(引用目标不存在); "
                 + "[~]=部分确证; [?]=存疑/无法核验")
    lines.append("注意: 确证只代表'论断引用的代码目标真实存在'，不代表'论断的语义结论正确'；"
                 + "静态图谱对动态调用/反射天然不完整，未确证不代表一定不存在。")
    return "\n".join(lines)


def render_html(result: Dict) -> str:
    """渲染非编程人员可读的 HTML 报告（自包含单文件）。"""
    from html import escape as _esc
    gs = result.get("graph_stats", {})
    tally = result.get("tally", {})

    def badge(verdict):
        return {
            VERDICT_CONFIRMED: ("#1DC981", "确证"),
            VERDICT_REFUTED: ("#E8463A", "证伪"),
            VERDICT_PARTIAL: ("#2E86DE", "部分确证"),
            VERDICT_UNVERIFIABLE: ("#EFAA17", "存疑"),
        }[verdict]

    rows = []
    for r in result.get("results", []):
        bg, label = badge(r["verdict"])
        ev = r.get("evidence", {})
        found = [p["symbol"] for p in ev.get("symbols", []) if p["found"]]
        loc = (_esc(r.get("file") or "") + ((":" + str(r["line"])) if r.get("line") is not None else ""))
        rows.append(
            f"<tr>"
            f"<td style='padding:10px 12px;border-bottom:1px solid #eee;'><span style='background:{bg};color:#fff;border-radius:999px;padding:2px 10px;font-size:12px;white-space:nowrap;'>{label}</span></td>"
            f"<td style='padding:10px 12px;border-bottom:1px solid #eee;font-weight:600;'>{_esc(r['title'])}</td>"
            f"<td style='padding:10px 12px;border-bottom:1px solid #eee;color:#555;font-size:13px;'>{_esc(loc) or '—'}</td>"
            f"<td style='padding:10px 12px;border-bottom:1px solid #eee;color:#555;font-size:13px;'>{_esc(r['reason'])}"
            f"{('<br>确证符号: ' + _esc(', '.join(found))) if found else ''}</td>"
            f"</tr>")

    if not gs.get("has_kg"):
        body = f"<div style='background:#fff;border-radius:14px;padding:28px;'>" \
               f"<h1 style='margin:0 0 12px;font-size:22px;'>论断核验报告</h1>" \
               f"<p style='color:#E8463A;'>{_esc(result.get('summary',''))}</p></div>"
    else:
        body = f"""<div style="background:#fff;border-radius:14px;padding:28px;box-shadow:0 1px 3px rgba(0,0,0,.06);">
    <h1 style="margin:0 0 4px;font-size:22px;">论断核验报告</h1>
    <div style="color:#888;font-size:13px;margin-bottom:16px;">
      共 {result.get('count',0)} 条 · 确证 {tally.get('confirmed',0)}
      · 证伪 {tally.get('refuted',0)} · 部分确证 {tally.get('partially_confirmed',0)}
      · 存疑 {tally.get('unverifiable',0)}
    </div>
    <table style="width:100%;border-collapse:collapse;font-size:14px;">
      <thead><tr style="text-align:left;color:#888;font-size:12px;border-bottom:2px solid #eee;">
        <th style="padding:8px 12px;">结论</th><th style="padding:8px 12px;">论断</th>
        <th style="padding:8px 12px;">位置</th><th style="padding:8px 12px;">核验依据</th>
      </tr></thead>
      <tbody>{''.join(rows)}</tbody>
    </table>
    <div style="margin-top:20px;font-size:12px;color:#999;line-height:1.8;">
      图例：<span style="color:#1DC981;">确证</span>=论断引用的代码目标真实存在；
      <span style="color:#E8463A;">证伪</span>=引用目标不存在；
      <span style="color:#2E86DE;">部分确证</span>=部分符号存在；<span style="color:#EFAA17;">存疑</span>=无法核验。<br>
      注意：确证只代表"论断引用的代码目标真实存在"，不代表"论断的语义结论正确"；
      静态图谱对动态调用/反射天然不完整，未确证不代表一定不存在。
    </div>
  </div>"""

    return f"""<!DOCTYPE html><html lang="zh"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>论断核验报告</title></head>
<body style="margin:0;font-family:'Segoe UI','PingFang SC','Microsoft YaHei',sans-serif;background:#f6f7f9;color:#222;">
<div style="max-width:960px;margin:0 auto;padding:32px 20px;">{body}</div></body></html>"""


def main():
    import argparse
    import json
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", required=True, help="项目路径（自动定位图谱）")
    ap.add_argument("--findings", required=True, help="论断 JSON 文件路径，或 JSON 数组字符串")
    ap.add_argument("--entry", help="可选：入口符号，核验符号是否在关键管线内")
    ap.add_argument("--html", help="可选：输出 HTML 报告路径")
    args = ap.parse_args()
    if os.path.isfile(args.findings):
        with open(args.findings, "r", encoding="utf-8") as f:
            findings = json.load(f)
    else:
        findings = json.loads(args.findings)
    result = verify_findings(args.project, findings, entry=args.entry)
    print(render_report(result))
    if args.html:
        with open(args.html, "w", encoding="utf-8") as f:
            f.write(render_html(result))
        _log(f"HTML 报告已写入: {args.html}")


if __name__ == "__main__":
    main()