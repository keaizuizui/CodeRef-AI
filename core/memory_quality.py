# -*- coding: utf-8 -*-
"""
记忆质量体检 —— MemoryQuality

对项目的「代码记忆层」做三项体检，供 MCP 工具 coderef_memory_quality 调用：

1. 引用完整性(integrity) —— 校验知识图谱中的边所指向的节点是否真实存在，清理孤儿边；
2. 语义覆盖(coverage)   —— 对比源码函数/类与记忆摘要，找出未被记忆摘要覆盖的遗漏；
3. 偏差检测(bias)       —— 用 LLM 复核记忆摘要是否引入偏差；无 LLM 则标记 pending-human。

设计约定：
- 纯标准库 + 复用底座（PromptExtractor / CodeKnowledgeGraph）
- 中文可读文本
- magic number 收敛为模块级常量
- LLM 缺失自动降级（不抛异常）
- auto_fix=True 时自动补全缺失摘要并标注来源(source="auto-fix")

返回结构：
{
  "findings": [
    {"kind": "integrity|coverage|bias",
     "file": "", "line": 0,
     "title": "", "detail": "", "suggestion": "",
     "severity": "critical|high|medium|low|info",
     "status": "auto-fixed|pending-human|open"}   # 可选
  ],
  "coverage": {"covered": N, "total": M, "ratio": 0.0, "missing": [...]},
  "auto_fix_applied": {"integrity": N, "coverage": N, "bias": N},
  "summary": "..."
}
"""

import os
import ast
import json
import hashlib
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Optional, Any

from loguru import logger

from core.prompt_extractor import PromptExtractor
from core.code_knowledge_graph import CodeKnowledgeGraph


# ═══════════════════════════════════════════════════════════════
# 模块级常量（magic number 收敛）
# ═══════════════════════════════════════════════════════════════

# 各类体检的 kind 标识
KIND_INTEGRITY = "integrity"
KIND_COVERAGE = "coverage"
KIND_BIAS = "bias"

# 严重程度
SEVERITY_CRITICAL = "critical"
SEVERITY_HIGH = "high"
SEVERITY_MEDIUM = "medium"
SEVERITY_LOW = "low"
SEVERITY_INFO = "info"

# 状态
STATUS_OPEN = "open"
STATUS_AUTO_FIXED = "auto-fixed"
STATUS_PENDING_HUMAN = "pending-human"

# 引用完整性
MAX_ORPHAN_EDGES_REPORT = 100        # 最多报告的孤儿边条数
MAX_ORPHAN_NODES_REPORT = 100        # 最多报告的孤立节点条数

# 语义覆盖
MAX_SYMBOLS_REPORT = 200            # 覆盖体检最多纳入的符号数
MIN_DOCSTRING_LEN = 8               # 视为有效摘要的最小 docstring 长度
COVERAGE_WARN_THRESHOLD = 0.6       # 覆盖率低于该值视为偏低

# 摘要原文片段长度
SUMMARY_SNIPPET_LEN = 120           # 覆盖率判断时用于匹配的 docstring 片段长度

# 摘要补全存储
AUTO_SUMMARY_FILE = "memory_quality_summaries.json"

# 自动补全摘要来源标识
SOURCE_AUTO_FIX = "auto-fix"

# 跳过目录
SKIP_DIRS = ("__pycache__", ".venv", "venv", "node_modules", ".git", ".pytest_cache")


# ═══════════════════════════════════════════════════════════════
# 记忆质量体检器
# ═══════════════════════════════════════════════════════════════

class MemoryQuality:
    """项目代码记忆层质量体检"""

    def __init__(self, llm_client=None):
        # 未显式注入 LLM 时，懒加载全局 LLM（环境变量/QSettings/config.json 配置源），
        # 使经 MCP 调用（MemoryQuality() 无参）时偏差检测也能真正用上 LLM，
        # 而非恒降级 pending-human。无可用 client 时保持 None，走降级路径。
        if llm_client is None:
            try:
                from core.llm_integration import LLMIntegration
                _c = LLMIntegration()
                llm_client = _c if getattr(_c, "client", None) else None
            except Exception as e:
                logger.warning(f"[MemoryQuality] LLM 懒加载失败，偏差检测将降级 pending-human: {e}")
                llm_client = None
        self.llm = llm_client
        self.extractor = PromptExtractor(llm_client=llm_client)

    # ═══════════════════════════════════════════════════════════
    # 主入口
    # ═══════════════════════════════════════════════════════════

    def assess(self, project_path: str, auto_fix: bool = False) -> Dict[str, Any]:
        """
        对项目执行三项记忆质量体检。

        Args:
            project_path: 目标项目路径
            auto_fix: 是否自动修复（补全缺失摘要、清理孤儿边）

        Returns:
            {
              "findings": [...],
              "coverage": {...},
              "auto_fix_applied": {...},
              "summary": str
            }
        """
        project_path = os.path.abspath(project_path)

        # 1) 引用完整性
        integrity_findings, integrity_stats, applied_i = \
            self._check_integrity(project_path, auto_fix)

        # 2) 语义覆盖
        coverage_findings, coverage_stats, applied_c = \
            self._check_coverage(project_path, auto_fix)

        # 3) 偏差检测
        bias_findings, bias_status, applied_b = \
            self._check_bias(project_path)

        findings = integrity_findings + coverage_findings + bias_findings
        auto_fix_applied = {
            KIND_INTEGRITY: applied_i,
            KIND_COVERAGE: applied_c,
            KIND_BIAS: applied_b,
        }

        summary = self._build_summary(
            integrity_stats, coverage_stats, bias_status,
            len(findings), auto_fix_applied,
        )

        logger.info(f"[MemoryQuality] 体检完成: {len(findings)} 条发现, "
                    f"覆盖率 {coverage_stats['ratio']:.0%}, "
                    f"偏移检测状态={bias_status}")
        return {
            "findings": findings,
            "coverage": coverage_stats,
            "auto_fix_applied": auto_fix_applied,
            "summary": summary,
        }

    # ═══════════════════════════════════════════════════════════
    # 1) 引用完整性
    # ═══════════════════════════════════════════════════════════

    def _check_integrity(self, project_path: str, auto_fix: bool):
        """校验知识图谱边引用的节点是否真实存在，并清理孤儿。"""
        findings: List[Dict[str, Any]] = []
        applied = 0

        kg = CodeKnowledgeGraph(project_path)
        if not kg.exists():
            findings.append(self._make_finding(
                kind=KIND_INTEGRITY,
                title="知识图谱未构建",
                detail="cache/kg 下没有该项目的知识图谱，无法执行引用完整性校验。"
                       "请先调用 coderef_architecture / 知识图谱构建工具生成图谱。",
                suggestion="先构建知识图谱，再重跑本工具。",
                severity=SEVERITY_INFO,
            ))
            stats = {"graph_exists": False, "orphan_edges": 0, "orphan_nodes": 0}
            return findings, stats, applied

        node_ids = set(kg.get_node_ids())

        # 遍历所有边，找出指向不存在节点的孤儿边
        orphan_edges = []
        for eid, e in kg.get_all_edges():
            if e.source not in node_ids or e.target not in node_ids:
                missing = []
                if e.source not in node_ids:
                    missing.append(f"source={e.source}")
                if e.target not in node_ids:
                    missing.append(f"target={e.target}")
                orphan_edges.append((eid, e, "，".join(missing)))

        # 找出没有入边也没有出边的孤立节点
        referenced = set()
        for _eid, e in kg.get_all_edges():
            referenced.add(e.source)
            referenced.add(e.target)
        orphan_nodes = [
            node_ids - referenced
        ] if False else sorted(nid for nid in node_ids if nid not in referenced)

        # 报告孤儿边
        for eid, e, missing in orphan_edges[:MAX_ORPHAN_EDGES_REPORT]:
            findings.append(self._make_finding(
                kind=KIND_INTEGRITY,
                file="",
                line=0,
                title="孤儿边：引用了不存在的节点",
                detail=f"边 {e.source} --[{e.type}]--> {e.target} 存在失效引用（{missing}）。"
                       "引用完整性被破坏，可能影响查询/影响分析的准确性。",
                suggestion="删除该孤儿边，或补建缺失的目标节点。",
                severity=SEVERITY_MEDIUM,
            ))

        # 报告孤立节点
        for nid in orphan_nodes[:MAX_ORPHAN_NODES_REPORT]:
            findings.append(self._make_finding(
                kind=KIND_INTEGRITY,
                file="",
                line=0,
                title="孤立节点：没有任何关联边",
                detail=f"节点 {nid} 没有入边也没有出边，可能是死代码或构建残留。",
                suggestion="确认该节点是否仍需保留；若已失效请清理。",
                severity=SEVERITY_LOW,
            ))

        # auto_fix：删除孤儿边（孤立节点保留，交由人工确认）
        if auto_fix and orphan_edges:
            eids = [eid for eid, _, _ in orphan_edges]
            applied = kg.delete_orphan_edges(eids)
            # 把已修复的发现标记为已自动修复
            for f in findings[: len(eids)]:
                f["status"] = STATUS_AUTO_FIXED
                f["detail"] += " [已自动修复]"

        kg.close()
        stats = {
            "graph_exists": True,
            "orphan_edges": len(orphan_edges),
            "orphan_nodes": len(orphan_nodes),
        }
        return findings, stats, applied

    # ═══════════════════════════════════════════════════════════
    # 2) 语义覆盖
    # ═══════════════════════════════════════════════════════════

    def _check_coverage(self, project_path: str, auto_fix: bool):
        """对比源码符号与记忆摘要，找出未被摘要覆盖的遗漏。"""
        findings: List[Dict[str, Any]] = []
        applied = 0

        # 收集记忆摘要（已抽取的 prompt 即记忆摘要集合）
        try:
            result = self.extractor.extract_from_project(project_path)
            memory_texts = [p.content for p in result.prompts]
        except Exception as e:
            logger.warning(f"[MemoryQuality] Prompt 抽取失败: {e}")
            result = None
            memory_texts = []

        memory_blob = "\n".join(memory_texts)

        # 收集源码符号
        symbols = self._collect_symbols(project_path)
        symbols = symbols[:MAX_SYMBOLS_REPORT]

        missing = []
        covered = 0
        for sym in symbols:
            if self._is_symbol_covered(sym, memory_blob):
                covered += 1
            else:
                missing.append(sym)

        total = len(symbols)
        ratio = (covered / total) if total else 0.0

        # 生成覆盖缺口发现
        for sym in missing[:MAX_SYMBOLS_REPORT]:
            findings.append(self._make_finding(
                kind=KIND_COVERAGE,
                file=sym["file"],
                line=sym["line"],
                title="记忆摘要未覆盖该符号",
                detail=f"{sym['kind']}「{sym['name']}」"
                       + (f"，docstring：{sym['docstring'][:50]}..." if sym["docstring"] else "，无有效 docstring")
                       + "，未出现在当前记忆摘要中，存在语义覆盖遗漏。",
                suggestion="为该符号补充记忆摘要，或确认其是否属于记忆层应覆盖的范围。",
                severity=SEVERITY_MEDIUM,
            ))

        # 覆盖率偏低整体提示
        if total and ratio < COVERAGE_WARN_THRESHOLD and not findings:
            findings.append(self._make_finding(
                kind=KIND_COVERAGE,
                title="记忆覆盖率偏低",
                detail=f"当前记忆覆盖率仅 {ratio:.0%}（{covered}/{total}），低于阈值 {COVERAGE_WARN_THRESHOLD:.0%}。",
                suggestion="建议补齐关键模块的记忆摘要。",
                severity=SEVERITY_LOW,
            ))

        # auto_fix：为缺失摘要的符号自动补全并标注来源
        if auto_fix and missing:
            store = self._load_auto_summary_store(project_path)
            for sym in missing:
                store["entries"][self._symbol_key(sym)] = {
                    "name": sym["name"],
                    "kind": sym["kind"],
                    "file": sym["file"],
                    "line": sym["line"],
                    "summary": self._autogen_summary(sym),
                    "source": SOURCE_AUTO_FIX,
                    "created_at": datetime.now().isoformat(),
                }
                applied += 1
            self._save_auto_summary_store(project_path, store)
            # 标记已自动补全
            for f in findings[: len(missing)]:
                f["status"] = STATUS_AUTO_FIXED
                f["detail"] += " [已自动补全摘要]"

        stats = {
            "covered": covered,
            "total": total,
            "ratio": round(ratio, 4),
            "missing": [
                {"name": s["name"], "kind": s["kind"], "file": s["file"], "line": s["line"]}
                for s in missing
            ],
        }
        return findings, stats, applied

    # ── 语义覆盖辅助 ──

    def _collect_symbols(self, project_path: str) -> List[Dict[str, Any]]:
        """用 AST 收集项目中的函数/类/方法符号及其 docstring。"""
        symbols: List[Dict[str, Any]] = []

        for py_file in Path(project_path).rglob("*.py"):
            if any(skip in str(py_file) for skip in SKIP_DIRS):
                continue
            try:
                with open(py_file, "r", encoding="utf-8", errors="replace") as f:
                    source = f.read()
                tree = ast.parse(source)
            except Exception:
                continue

            # 只收集模块级与类级符号（函数/类/方法），避免重复统计
            for node in ast.iter_child_nodes(tree):
                if isinstance(node, ast.FunctionDef):
                    symbols.append(self._to_symbol(node, py_file, "function"))
                elif isinstance(node, ast.ClassDef):
                    symbols.append(self._to_symbol(node, py_file, "class"))
                    for sub in ast.iter_child_nodes(node):
                        if isinstance(sub, ast.FunctionDef):
                            symbols.append(self._to_symbol(sub, py_file, "method"))
        return symbols

    @staticmethod
    def _to_symbol(node, py_file: Path, kind: str) -> Dict[str, Any]:
        docstring = ast.get_docstring(node) or ""
        return {
            "name": node.name,
            "kind": kind,
            "file": str(py_file),
            "line": getattr(node, "lineno", 0),
            "docstring": docstring,
        }

    @staticmethod
    def _symbol_key(sym: Dict[str, Any]) -> str:
        return f"{sym['file']}::{sym['name']}"

    @staticmethod
    def _is_symbol_covered(sym: Dict[str, Any], memory_blob: str) -> bool:
        """判断一个符号是否已被记忆摘要覆盖。"""
        # 符号名匹配（如 func:module:name 会出现）
        if sym["name"] in memory_blob:
            return True
        # docstring 片段匹配（更可靠的覆盖信号）
        doc = sym["docstring"].strip()
        if doc and len(doc) >= MIN_DOCSTRING_LEN:
            snippet = doc[:SUMMARY_SNIPPET_LEN]
            if snippet in memory_blob:
                return True
        return False

    @staticmethod
    def _autogen_summary(sym: Dict[str, Any]) -> str:
        """自动生成一段摘要（无 LLM 时的降级方案）。"""
        doc = sym["docstring"].strip()
        if doc:
            return f"{sym['kind']}「{sym['name']}」：{doc[:200]}"
        return f"{sym['kind']}「{sym['name']}」：位于 {os.path.basename(sym['file'])}:{sym['line']}，暂无 docstring，请人工补充。".strip()

    # ── auto-summary 存储 ──

    def _auto_summary_path(self, project_path: str) -> str:
        data_dir = Path(__file__).parent.parent / "data"
        data_dir.mkdir(parents=True, exist_ok=True)
        return str(data_dir / AUTO_SUMMARY_FILE)

    def _load_auto_summary_store(self, project_path: str) -> Dict[str, Any]:
        """加载并返回该项目的 auto-summary 条目容器（含 entries）。"""
        path = self._auto_summary_path(project_path)
        data = {"projects": {}}
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    loaded = json.load(f)
                if isinstance(loaded, dict):
                    data = loaded
            except Exception:
                pass
        phash = hashlib.md5(project_path.encode("utf-8")).hexdigest()[:12]
        proj = data.get("projects", {}).get(phash)
        if proj is None:
            proj = {"path": project_path, "entries": {}}
            data["projects"][phash] = proj
        # 返回条目容器，同时暂存 data 供保存使用
        self._summary_store_root = data
        return proj

    def _save_auto_summary_store(self, project_path: str, proj: Dict[str, Any]):
        """把更新后的项目条目容器写回完整存储。"""
        data = getattr(self, "_summary_store_root", None)
        if data is None:
            data = {"projects": {}}
        phash = hashlib.md5(project_path.encode("utf-8")).hexdigest()[:12]
        data.setdefault("projects", {})[phash] = proj
        self._atomic_write_json(self._auto_summary_path(project_path), data)

    # ═══════════════════════════════════════════════════════════
    # 3) 偏差检测
    # ═══════════════════════════════════════════════════════════

    def _check_bias(self, project_path: str):
        """用 LLM 复核记忆摘要是否引入偏差；无 LLM 则标记 pending-human。"""
        findings: List[Dict[str, Any]] = []
        applied = 0

        if self.llm and hasattr(self.llm, "chat_completion"):
            try:
                result = self.extractor.extract_from_project(project_path)
                if not result.prompts:
                    return findings, "no_prompts", applied
                excerpts = [
                    {"var": p.variable_name, "file": p.file_path,
                     "content": p.content[:800]}
                    for p in result.prompts[:MAX_ORPHAN_EDGES_REPORT]
                ]
                prompt_text = (
                    "以下是项目记忆摘要（抽取自 prompt 模板）。请复核是否存在偏差：\n"
                    "包括：过度承诺、遗漏约束条件、内容与源码不符、幻觉等。\n"
                    f"{json.dumps(excerpts, ensure_ascii=False, indent=2)}\n"
                    "请返回 JSON 数组："
                    '[{"title":"偏差标题","detail":"具体偏差","suggestion":"修复建议",'
                    '"severity":"high|medium|low"}]；无偏差则返回 []。'
                )
                response = self.llm.chat_completion([
                    {"role": "system", "content": "你是严谨的记忆质量审计员，只返回 JSON。"},
                    {"role": "user", "content": prompt_text},
                ])
                parsed = self._parse_findings(response)
                if parsed:
                    for item in parsed[:MAX_ORPHAN_EDGES_REPORT]:
                        findings.append(self._make_finding(
                            kind=KIND_BIAS,
                            title=item.get("title", "记忆摘要存在偏差"),
                            detail=item.get("detail", ""),
                            suggestion=item.get("suggestion", ""),
                            severity=item.get("severity", SEVERITY_LOW),
                        ))
                    return findings, "llm", applied
            except Exception as e:
                logger.warning(f"[MemoryQuality] LLM 偏差检测失败: {e}")

        # 降级：无 LLM 或调用失败 → pending-human
        findings.append(self._make_finding(
            kind=KIND_BIAS,
            title="偏差检测待人工复核",
            detail="未提供可用的 LLM 客户端（或调用失败），无法自动复核记忆摘要是否存在"
                   "过度承诺、遗漏约束或内容失真等偏差。已标记 pending-human。",
            suggestion="接入 LLM 客户端后重跑 assess()，或由人工复核记忆摘要的客观与完整性。",
            severity=SEVERITY_INFO,
            status=STATUS_PENDING_HUMAN,
        ))
        return findings, "pending-human", applied

    @staticmethod
    def _parse_findings(response: str) -> List[Dict[str, Any]]:
        """从 LLM 返回文本中解析 JSON 数组。"""
        if not response:
            return []
        text = response.strip()
        # 去掉可能的 markdown 代码块包裹
        start = text.find("[")
        end = text.rfind("]")
        if start < 0 or end <= start:
            return []
        try:
            data = json.loads(text[start:end + 1])
            if isinstance(data, list):
                return [d for d in data if isinstance(d, dict)]
        except Exception:
            pass
        return []

    # ═══════════════════════════════════════════════════════════
    # 通用辅助
    # ═══════════════════════════════════════════════════════════

    @staticmethod
    def _make_finding(kind, title, detail="", suggestion="",
                      severity=SEVERITY_LOW, file="", line=0, status=STATUS_OPEN):
        finding = {
            "kind": kind,
            "file": file,
            "line": line,
            "title": title,
            "detail": detail,
            "suggestion": suggestion,
            "severity": severity,
        }
        if status != STATUS_OPEN:
            finding["status"] = status
        return finding

    @staticmethod
    def _build_summary(integrity_stats, coverage_stats, bias_status,
                       total_findings, auto_fix_applied) -> str:
        lines = ["# 记忆质量体检摘要"]
        lines.append(f"\n- 发现总数：{total_findings}")
        lines.append(f"- 引用完整性：孤儿边 {integrity_stats.get('orphan_edges', 0)} 条，"
                     f"孤立节点 {integrity_stats.get('orphan_nodes', 0)} 个")
        lines.append(f"- 语义覆盖率：{coverage_stats.get('ratio', 0):.0%}"
                     f"（{coverage_stats.get('covered', 0)}/{coverage_stats.get('total', 0)}）")
        lines.append(f"- 偏差检测：{bias_status}")
        lines.append(f"- 自动修复：完整性 {auto_fix_applied.get(KIND_INTEGRITY, 0)} 条，"
                     f"覆盖补全 {auto_fix_applied.get(KIND_COVERAGE, 0)} 条，"
                     f"偏差 {auto_fix_applied.get(KIND_BIAS, 0)} 条")
        return "\n".join(lines)

    @staticmethod
    def _atomic_write_json(path: str, data: Dict[str, Any]):
        """原子写 JSON：先写 .tmp 再 os.replace，避免并发读到半写入文件。"""
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        os.replace(tmp, path)


# ═══════════════════════════════════════════════════════════════
# 便捷函数
# ═══════════════════════════════════════════════════════════════

def assess_memory_quality(project_path: str, auto_fix: bool = False,
                          llm_client=None) -> Dict[str, Any]:
    """一键执行记忆质量体检。"""
    checker = MemoryQuality(llm_client=llm_client)
    return checker.assess(project_path, auto_fix=auto_fix)