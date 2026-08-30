# -*- coding: utf-8 -*-
"""
MemoryLayer v1.0 —— AI 代码记忆层（认知记忆层）

为 MCP 工具 coderef_memory(action=sync/query/status)
提供统一的"项目认知记忆"能力：

1. sync(project_path, mode="full"|"incr")
   - 全量/增量同步：把项目代码结构记忆进知识图谱（CodeKnowledgeGraph）与
     语义知识库（CodeKnowledgeBase）。
   - 增量模式用 mtime+size 快照判断文件是否变更，只对变更文件重新做 AST 提取，
     未变更文件复用记忆层自身缓存的 AST，再统一更新图谱。
   - 运行时快照以原子写（temp + os.replace）持久化到 data/memory_state/{hash}.json。

2. query(...)
   - 语义检索走 CodeKnowledgeBase.search（Ollama 不可用时自动降级为关键词匹配）。
   - 结构查询走 CodeKnowledgeGraph 的 query_entity/query_callers/query_impact/search 等，
     与 coderef_query 风格一致。
   - 全部返回结构化 dict。

3. status(project_path)
   - 计算认知覆盖度（已索引/语义化模块比例）、每模块置信度高/中/低、盲区（联动
     BlindSpotDetector），并生成一张复用 health_dashboard 渲染风格的认知地图 HTML。

工程约定：
- 纯标准库 + 复用底座（不修改任何底座文件）。
- 所有用户可读文本使用中文。
- magic number 定义为模块级常量（不修改 config/settings.py）。
- LLM / Ollama 缺失时优雅降级，不抛异常。
- 统一返回结构化 JSON dict。
- 不做任何 MCP 注册（由上层统一接线）。
"""

import os
import sys
import json
import time
import hashlib
import tempfile
from datetime import datetime
from typing import Dict, List, Optional, Any

# ─── 日志（复用 loguru，若缺失则回退到内置 logging） ───
try:
    from loguru import logger as _logger
except Exception:  # pragma: no cover
    import logging
    _logger = logging.getLogger("memory_layer")
    if not _logger.handlers:
        _logger.addHandler(logging.StreamHandler(sys.stdout))
        _logger.setLevel(logging.INFO)
logger = _logger


# ═══════════════════════════════════════════════════════════════════
# 模块级常量（magic number 集中定义，不修改 config/settings.py）
# ═══════════════════════════════════════════════════════════════════

# 项目根目录（Coderef-Ai-master）
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 记忆状态 / 语义知识库存放目录
_MEMORY_STATE_DIR = os.path.join(_PROJECT_ROOT, "data", "memory_state")
# 认知地图 HTML 输出目录（与 health_dashboard 输出目录一致）
_REPORT_DIR = os.path.join(_PROJECT_ROOT, "coderef-report")

# 快照 mtime 容差（秒），与底座 code_analyzer 保持一致
_MTIME_TOLERANCE = 0.1

# 查询默认与上限
_DEFAULT_LIMIT = 10
_MAX_LIMIT = 100
_DEFAULT_DEPTH = 2

# 置信度阈值（函数/docstring 覆盖率）
_DOC_RATIO_HIGH = 0.6
_DOC_RATIO_MEDIUM = 0.3
# 置信等级
_CONF_HIGH = "high"
_CONF_MEDIUM = "medium"
_CONF_LOW = "low"
# 置信等级中文标签
_CONF_LABEL = {_CONF_HIGH: "高", _CONF_MEDIUM: "中", _CONF_LOW: "低"}

# 语义查询类型标识
_SEMANTIC_TYPES = ("semantic", "text", "keyword", "natural", "nl")

# 盲区分类中文标签
_BLINDSPOT_LABEL = {
    "doc_blindspot": "文档盲区",
    "missing_dependency": "缺失依赖",
    "dynamic_path": "动态路径注入",
    "unindexed_symbol": "符号索引盲区",
    "empty_file": "空文件",
}

# 置信度加权（整体置信度 = 0.5*doc 覆盖率 + 0.5*解析成功率）
_W_DOC = 0.5
_W_PARSE = 0.5

# 认知地图 HTML 中单条代码预览长度
_CODE_PREVIEW_LEN = 2000


# ═══════════════════════════════════════════════════════════════════
# AST 结果序列化辅助（用于增量复用未变更文件的解析结果）
# ═══════════════════════════════════════════════════════════════════

def _ast_func_to_dict(f) -> dict:
    return {
        "name": f.name, "start_line": f.start_line, "end_line": f.end_line,
        "parameters": list(f.parameters), "return_type": f.return_type,
        "docstring": f.docstring, "code": f.code,
        "decorators": list(f.decorators), "is_async": f.is_async,
        "is_method": f.is_method, "parent_class": f.parent_class,
    }


def _ast_class_to_dict(c) -> dict:
    return {
        "name": c.name, "start_line": c.start_line, "end_line": c.end_line,
        "methods": [_ast_func_to_dict(m) for m in c.methods],
        "base_classes": list(c.base_classes), "docstring": c.docstring,
        "decorators": list(c.decorators),
    }


def _ast_to_dict(ar) -> dict:
    """把 AstFileResult 序列化为可持久化的 dict（状态文件原子写入用）"""
    return {
        "file_path": ar.file_path,
        "language": ar.language,
        "imports": [
            {"module": i.module, "names": list(i.names),
             "is_from_import": i.is_from_import, "line": i.line,
             "category": i.category}
            for i in ar.imports
        ],
        "functions": [_ast_func_to_dict(f) for f in ar.functions],
        "classes": [_ast_class_to_dict(c) for c in ar.classes],
        "calls": [
            {"func_name": c.func_name, "line": c.line,
             "is_method_call": c.is_method_call, "args_count": c.args_count,
             "keyword_args": list(getattr(c, "keyword_args", []))}
            for c in ar.calls
        ],
        "assignments": [
            {"target": a.target, "value_repr": a.value_repr,
             "line": a.line, "category": a.category}
            for a in ar.assignments
        ],
        "total_lines": ar.total_lines,
        "module_docstring": ar.module_docstring,
    }


def _strip_code(d: dict) -> dict:
    """持久化前剔除函数体（code 字段），避免状态文件膨胀。

    还原时 _ast_from_dict 会用空字符串填充 code，不影响结构分析。
    """
    if not d:
        return d
    out = dict(d)
    out["functions"] = [{**f, "code": ""} for f in d.get("functions", [])]
    out["classes"] = [
        {**c, "methods": [{**m, "code": ""} for m in c.get("methods", [])]}
        for c in d.get("classes", [])
    ]
    return out


def _ast_from_dict(d: dict):
    """从 dict 还原 AstFileResult dataclass 对象（供 CodeKnowledgeGraph.build 使用）"""
    from core.ast_parser import (
        AstFileResult, AstCodeImport, AstCodeCall, AstCodeAssignment,
        AstCodeFunction, AstCodeClass,
    )

    def _func(fd):
        return AstCodeFunction(
            name=fd.get("name", ""), start_line=fd.get("start_line", 0),
            end_line=fd.get("end_line", 0),
            parameters=list(fd.get("parameters", [])),
            return_type=fd.get("return_type"),
            docstring=fd.get("docstring"),
            code=fd.get("code", ""),
            decorators=list(fd.get("decorators", [])),
            is_async=fd.get("is_async", False),
            is_method=fd.get("is_method", False),
            parent_class=fd.get("parent_class"),
        )

    def _class(cd):
        return AstCodeClass(
            name=cd.get("name", ""), start_line=cd.get("start_line", 0),
            end_line=cd.get("end_line", 0),
            methods=[_func(m) for m in cd.get("methods", [])],
            base_classes=list(cd.get("base_classes", [])),
            docstring=cd.get("docstring"),
            decorators=list(cd.get("decorators", [])),
        )

    result = AstFileResult(file_path=d.get("file_path", ""),
                           language=d.get("language", "python"))
    result.imports = [
        AstCodeImport(module=i["module"], names=list(i.get("names", [])),
                      is_from_import=i.get("is_from_import", False),
                      line=i.get("line", 0), category=i.get("category", "unknown"))
        for i in d.get("imports", [])
    ]
    result.calls = [
        AstCodeCall(func_name=c["func_name"], line=c.get("line", 0),
                    is_method_call=c.get("is_method_call", False),
                    args_count=c.get("args_count", 0),
                    keyword_args=list(c.get("keyword_args", [])))
        for c in d.get("calls", [])
    ]
    result.assignments = [
        AstCodeAssignment(target=a["target"], value_repr=a.get("value_repr", ""),
                          line=a.get("line", 0), category=a.get("category", "expression"))
        for a in d.get("assignments", [])
    ]
    result.functions = [_func(f) for f in d.get("functions", [])]
    result.classes = [_class(c) for c in d.get("classes", [])]
    result.total_lines = d.get("total_lines", 0)
    result.module_docstring = d.get("module_docstring")
    return result


# ═══════════════════════════════════════════════════════════════════
# MemoryLayer
# ═══════════════════════════════════════════════════════════════════

class MemoryLayer:
    """AI 代码记忆层 —— 全量/增量同步、语义/结构查询、认知状态报告"""

    def __init__(self):
        os.makedirs(_MEMORY_STATE_DIR, exist_ok=True)
        os.makedirs(_REPORT_DIR, exist_ok=True)

    # ─── 路径 / 哈希 ───

    @staticmethod
    def _project_hash(project_path: str) -> str:
        return hashlib.md5(os.path.abspath(project_path).encode("utf-8")).hexdigest()[:12]

    def _state_path(self, project_path: str) -> str:
        return os.path.join(_MEMORY_STATE_DIR, f"{self._project_hash(project_path)}.json")

    def _kb_path(self, project_path: str) -> str:
        return os.path.join(_MEMORY_STATE_DIR, f"{self._project_hash(project_path)}.kb.db")

    # ─── 状态读写（原子写 temp + os.replace） ───

    def _save_state(self, project_path: str, data: dict) -> str:
        path = self._state_path(project_path)
        fd, tmp = tempfile.mkstemp(dir=_MEMORY_STATE_DIR, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False)
            os.replace(tmp, path)
        except Exception:
            try:
                os.unlink(tmp)
            except OSError:
                # 临时文件清理尽力而为，随后原样抛出
                pass
            raise
        return path

    def _load_state(self, project_path: str) -> dict:
        path = self._state_path(project_path)
        if not os.path.exists(path):
            return {}
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.warning(f"[MemoryLayer] 读取状态失败，按空状态处理: {e}")
            return {}

    # ─── 快照计算（mtime + size） ───

    @staticmethod
    def _compute_snapshot(files: List[str]) -> Dict[str, Dict[str, float]]:
        snap = {}
        for fp in files:
            try:
                snap[fp] = {"mtime": os.path.getmtime(fp), "size": os.path.getsize(fp)}
            except OSError as e:
                logger.warning(f"读取文件快照失败，跳过 {fp}: {e}")
        return snap

    @staticmethod
    def _same_file(old: dict, cur: dict) -> bool:
        if not old or not cur:
            return False
        return abs(old.get("mtime", 0) - cur.get("mtime", 0)) <= _MTIME_TOLERANCE and \
            old.get("size") == cur.get("size")

    # ═══════════════════════════════════════════════════════════════
    # 1. sync
    # ═══════════════════════════════════════════════════════════════

    def _classify_changed(self, mode: str, files: List[str],
                          current_snapshot: Dict[str, Dict[str, float]],
                          prev_snapshot: Dict[str, Dict[str, float]]):
        """按 mode 与快照把文件分为（变更, 未变更）两组。"""
        changed: List[str] = []
        unchanged: List[str] = []
        for fp in files:
            if mode == "full":
                changed.append(fp)
            elif self._same_file(prev_snapshot.get(fp), current_snapshot.get(fp)):
                unchanged.append(fp)
            else:
                changed.append(fp)
        return changed, unchanged

    @staticmethod
    def _extract_ast_cache(changed: List[str], unchanged: List[str],
                           old_ast: Dict[str, Optional[dict]]):
        """增量 AST 提取：只对变更文件重新解析，未变更复用状态里的缓存。"""
        from core.ast_parser import AstParser
        parser = AstParser()
        new_ast: Dict[str, Optional[dict]] = {}
        for fp in changed:
            try:
                ar = parser.parse(fp)
                new_ast[fp] = _ast_to_dict(ar) if ar else None
            except Exception:
                new_ast[fp] = None
        for fp in unchanged:
            new_ast[fp] = old_ast.get(fp)
        return new_ast

    @staticmethod
    def _analyze_structure(analyzer, project_path: str, mode: str):
        """结构分析（复用 CodeAnalyzer 自身缓存；full 强制重建）。"""
        try:
            if analyzer is None:
                from core.code_analyzer import CodeAnalyzer
                analyzer = CodeAnalyzer()
            return analyzer.analyze_project(project_path,
                                            force_reanalyze=(mode == "full"))
        except Exception as e:
            logger.warning(f"[MemoryLayer] 结构分析失败，图谱退化为仅 AST: {e}")
            return None

    @staticmethod
    def _update_knowledge_graph(project_path: str, analysis,
                                ast_results) -> dict:
        """更新知识图谱（结构记忆）。"""
        try:
            from core.code_knowledge_graph import CodeKnowledgeGraph
            kg = CodeKnowledgeGraph(project_path)
            kg_stats = kg.build(analysis=analysis, ast_results=ast_results)
            kg.close()
            return kg_stats
        except Exception as e:
            logger.warning(f"[MemoryLayer] 图谱更新失败: {e}")
            return {"nodes": 0, "edges": 0, "errors": [str(e)]}

    def _persist_state(self, project_path: str, mode: str,
                       current_snapshot, new_ast, kg_stats: dict,
                       kb_status: dict) -> None:
        """持久化新的记忆状态（原子写）。"""
        new_state = {
            "project_path": project_path,
            "hash": self._project_hash(project_path),
            "mode": mode,
            "last_sync": datetime.now().isoformat(),
            "snapshot": current_snapshot,
            "ast_cache": {fp: _strip_code(d) if d else None for fp, d in new_ast.items()},
            "kg_stats": kg_stats,
            "kb_status": kb_status,
        }
        try:
            self._save_state(project_path, new_state)
        except Exception as e:
            logger.warning(f"[MemoryLayer] 记忆状态写入失败: {e}")

    def sync(self, project_path: str, mode: str = "full") -> dict:
        """全量或增量同步项目到代码记忆层。

        Args:
            project_path: 项目绝对路径
            mode: "full" 全量重建；"incr" 增量（基于 mtime+size 快照）

        Returns:
            {"status", "mode", "synced_files", "changed_files", "unchanged_files",
             "total_files", "coverage", "confidence", "kg", "kb"}
        """
        project_path = os.path.abspath(project_path)
        if mode not in ("full", "incr"):
            mode = "full"
        if not os.path.isdir(project_path):
            return {"status": "error", "mode": mode,
                    "message": "项目路径不存在", "total_files": 0,
                    "coverage": 0.0, "confidence": 0.0}

        analyzer = None
        try:
            from core.code_analyzer import CodeAnalyzer
            analyzer = CodeAnalyzer()
            files = analyzer.scan_directory(project_path)
        except Exception as e:
            return {"status": "error", "mode": mode,
                    "message": f"扫描项目失败: {e}", "total_files": 0,
                    "coverage": 0.0, "confidence": 0.0}

        total_files = len(files)
        prev = self._load_state(project_path)
        current_snapshot = self._compute_snapshot(files)
        prev_snapshot = prev.get("snapshot", {})

        # 分类：变更 / 未变更
        changed, unchanged = self._classify_changed(mode, files,
                                                     current_snapshot, prev_snapshot)

        # 增量 AST 提取：只对变更文件重新解析，未变更复用状态里的缓存
        new_ast = self._extract_ast_cache(changed, unchanged,
                                          prev.get("ast_cache", {}))

        # 还原为 AstFileResult 对象供 CodeKnowledgeGraph.build 使用
        ast_results = {fp: _ast_from_dict(d)
                       for fp, d in new_ast.items() if d}

        # 结构分析（复用 CodeAnalyzer 自身缓存；full 强制重建）
        analysis = self._analyze_structure(analyzer, project_path, mode)

        # 更新知识图谱（结构记忆）
        kg_stats = self._update_knowledge_graph(project_path, analysis, ast_results)

        # 更新语义知识库（语义记忆，Ollama 缺失自动降级）
        kb_status = self._index_kb(project_path)

        # 覆盖度 / 置信度
        indexed = len(ast_results)
        coverage = round(indexed / total_files * 100, 1) if total_files else 0.0
        confidence = self._overall_confidence(new_ast, total_files)

        # 持久化新的记忆状态（原子写）
        self._persist_state(project_path, mode, current_snapshot, new_ast,
                            kg_stats, kb_status)

        return {
            "status": "ok",
            "mode": mode,
            "synced_files": len(changed),
            "changed_files": changed,
            "unchanged_files": len(unchanged),
            "total_files": total_files,
            "coverage": coverage,
            "confidence": confidence,
            "kg": kg_stats,
            "kb": kb_status,
        }

    def _index_kb(self, project_path: str) -> dict:
        """索引/重建语义知识库（best-effort，失败不影响主流程）"""
        db_path = self._kb_path(project_path)
        try:
            from core.code_knowledge_base import CodeKnowledgeBase
            kb = CodeKnowledgeBase(db_path)
            count = kb.index_project(project_path)
            stats = {}
            try:
                stats = kb.stats()
            except Exception as e:
                # 统计不可用时返回空结果
                logger.warning(f"读取知识库统计失败，返回空统计: {e}")
            return {"db_path": db_path, "chunks": count, "stats": stats}
        except Exception as e:
            logger.warning(f"[MemoryLayer] 语义知识库索引失败: {e}")
            return {"db_path": db_path, "error": str(e)}

    # ═══════════════════════════════════════════════════════════════
    # 2. query
    # ═══════════════════════════════════════════════════════════════

    def query(self, project_path: str, query_type: str = "semantic",
              keyword: str = "", name: str = "", func_name: str = "",
              file_path: str = "", limit: int = _DEFAULT_LIMIT) -> dict:
        """查询代码记忆。

        语义检索（query_type ∈ semantic/text/keyword/natural/nl）：
            走 CodeKnowledgeBase.search，Ollama 缺失自动降级关键词匹配。
        结构查询（stats/entity/callers/callees/impact/relations/
                  file_entities/search/call_graph）：
            走 CodeKnowledgeGraph，与 coderef_query 风格一致。

        Returns:
            结构化 dict，诸如:
            {"status","query_type","engine","total","results",...}
        """
        project_path = os.path.abspath(project_path)
        try:
            limit = int(limit or _DEFAULT_LIMIT)
        except (TypeError, ValueError):
            limit = _DEFAULT_LIMIT
        limit = max(1, min(limit, _MAX_LIMIT))

        if query_type in _SEMANTIC_TYPES:
            return self._semantic_query(project_path, keyword, limit)
        return self._graph_query(project_path, query_type, keyword, name,
                                 func_name, file_path, limit)

    def _semantic_query(self, project_path: str, keyword: str, limit: int) -> dict:
        db_path = self._kb_path(project_path)
        try:
            from core.code_knowledge_base import CodeKnowledgeBase
            kb = CodeKnowledgeBase(db_path)
            results = kb.search(keyword or "", top_k=limit)
            out = []
            for r in results:
                c = r.chunk
                out.append({
                    "file_path": c.file_path,
                    "type": c.chunk_type,
                    "name": c.name,
                    "score": round(float(r.score), 4),
                    "rank": r.rank,
                    "code": (c.code or "")[:_CODE_PREVIEW_LEN],
                    "docstring": c.docstring,
                    "start_line": c.start_line,
                    "end_line": c.end_line,
                })
            return {"status": "ok", "query_type": "semantic",
                    "engine": "knowledge_base", "keyword": keyword,
                    "total": len(out), "results": out}
        except Exception as e:
            return {"status": "error", "query_type": "semantic",
                    "engine": "knowledge_base", "keyword": keyword,
                    "message": str(e), "total": 0, "results": []}

    def _graph_query(self, project_path: str, query_type: str, keyword: str,
                     name: str, func_name: str, file_path: str, limit: int) -> dict:
        try:
            from core.code_knowledge_graph import load_knowledge_graph
        except Exception as e:
            return {"status": "error", "engine": "graph",
                    "query_type": query_type, "message": str(e), "total": 0}

        kg = load_knowledge_graph(project_path)
        if kg is None:
            return {"status": "error", "engine": "graph",
                    "query_type": query_type,
                    "message": "知识图谱不存在，请先执行 memory_sync(full)",
                    "total": 0, "results": []}

        try:
            if query_type == "stats":
                return {"status": "ok", "engine": "graph", "query_type": "stats",
                        **kg.get_stats()}
            if query_type == "entity":
                r = kg.query_entity(name or "")
                return {"status": "ok", "engine": "graph", "query_type": "entity",
                        "total": r.total, "results": [n.to_dict() for n in r.nodes]}
            if query_type == "callers":
                r = kg.query_callers(func_name or "")
                return {"status": "ok", "engine": "graph", "query_type": "callers",
                        "total": r.total, "results": [n.to_dict() for n in r.nodes]}
            if query_type == "callees":
                r = kg.query_callees(func_name or "")
                return {"status": "ok", "engine": "graph", "query_type": "callees",
                        "total": r.total, "results": [n.to_dict() for n in r.nodes]}
            if query_type == "impact":
                r = kg.query_impact(file_path or "")
                return {"status": "ok", "engine": "graph", "query_type": "impact",
                        "total": r.total, "results": [n.to_dict() for n in r.nodes]}
            if query_type == "relations":
                r = kg.query_relations(keyword or "")  # keyword 复用为 node_id
                return {"status": "ok", "engine": "graph", "query_type": "relations",
                        "total": r.total,
                        "results": [n.to_dict() for n in r.nodes],
                        "edges": [e.to_dict() for e in r.edges]}
            if query_type == "file_entities":
                r = kg.query_file_entities(file_path or "")
                return {"status": "ok", "engine": "graph", "query_type": "file_entities",
                        "total": r.total, "results": [n.to_dict() for n in r.nodes]}
            if query_type == "search":
                r = kg.search(keyword or "", limit)
                return {"status": "ok", "engine": "graph", "query_type": "search",
                        "keyword": keyword, "total": r.total,
                        "results": [n.to_dict() for n in r.nodes]}
            if query_type == "call_graph":
                r = kg.get_call_graph(func_name or "", _DEFAULT_DEPTH)
                return {"status": "ok", "engine": "graph", "query_type": "call_graph",
                        "total": r.total,
                        "results": [n.to_dict() for n in r.nodes],
                        "edges": [e.to_dict() for e in r.edges]}
            return {"status": "error", "engine": "graph", "query_type": query_type,
                    "message": f"未知查询类型: {query_type}，支持 semantic/entity/"
                               f"callers/callees/impact/relations/file_entities/"
                               f"search/call_graph/stats",
                    "total": 0, "results": []}
        finally:
            kg.close()

    # ═══════════════════════════════════════════════════════════════
    # 3. status
    # ═══════════════════════════════════════════════════════════════

    def status(self, project_path: str) -> dict:
        """生成认知状态报告与认知地图 HTML。

        Returns:
            {"status","coverage","confidence_map","blindspots",
             "dashboard_path","html"}
        """
        project_path = os.path.abspath(project_path)
        phash = self._project_hash(project_path)
        state = self._load_state(project_path)

        # 重新扫描以获得最新的文件清单
        files: List[str] = []
        try:
            from core.code_analyzer import CodeAnalyzer
            files = CodeAnalyzer().scan_directory(project_path)
        except Exception as e:
            logger.warning(f"[MemoryLayer] 状态扫描失败: {e}")

        total_files = len(files)
        ast_cache = state.get("ast_cache", {})
        file_set = set(files)
        indexed = [fp for fp, d in ast_cache.items() if d and fp in file_set]
        coverage = round(len(indexed) / total_files * 100, 1) if total_files else 0.0

        confidence_map = self._module_confidence(ast_cache, files)

        # 盲区联动
        blindspots = self._detect_blindspots(project_path)

        # 认知地图 HTML
        dashboard_path, html = self._render_dashboard(
            project_path, coverage, confidence_map, blindspots,
            total_files, len(indexed))

        return {
            "status": "ok",
            "project_path": project_path,
            "coverage": coverage,
            "confidence_map": confidence_map,
            "blindspots": blindspots,
            "dashboard_path": dashboard_path,
            "html": html,
        }

    def _detect_blindspots(self, project_path: str) -> List[dict]:
        """联动 BlindSpotDetector，返回结构化盲区列表（失败时优雅降级）"""
        try:
            from core.blind_spot_detector import BlindSpotDetector
            detector = BlindSpotDetector()
            detector.detect(project_path)
            spots: List[dict] = []
            for s in getattr(detector, "spots", []) or []:
                cat = getattr(s, "category", "")
                spots.append({
                    "category": cat,
                    "category_label": _BLINDSPOT_LABEL.get(cat, cat),
                    "item": getattr(s, "item", ""),
                    "detail": getattr(s, "detail", ""),
                    "file_path": getattr(s, "file_path", ""),
                    "risk_level": getattr(s, "risk_level", "low"),
                    "user_should_know": getattr(s, "user_should_know", ""),
                })
            return spots
        except Exception as e:
            logger.warning(f"[MemoryLayer] 盲区检测失败: {e}")
            return [{"category": "error", "detail": f"盲区检测失败: {e}"}]

    # ─── 置信度 / 覆盖度计算 ───

    def _overall_confidence(self, ast_cache: Dict[str, Optional[dict]],
                            total_files: int) -> float:
        """整体置信度（0-100）：adoc 覆盖率与解析成功率加权"""
        parsed = [d for d in ast_cache.values() if d]
        total_func = 0
        doc_func = 0
        for d in parsed:
            funcs = list(d.get("functions", []))
            total_func += len(funcs)
            doc_func += sum(1 for f in funcs if (f.get("docstring") or "").strip())
            for c in d.get("classes", []):
                for m in c.get("methods", []):
                    total_func += 1
                    doc_func += 1 if (m.get("docstring") or "").strip() else 0

        if total_files <= 0:
            return 0.0
        parse_ratio = len(parsed) / total_files
        doc_ratio = doc_func / total_func if total_func else (1.0 if parsed else 0.0)
        return round(100 * (_W_DOC * doc_ratio + _W_PARSE * parse_ratio), 1)

    def _module_confidence(self, ast_cache: Dict[str, Optional[dict]],
                           files: List[str]) -> Dict[str, str]:
        """按顶层模块计算置信度高/中/低"""
        # 按顶层目录分组
        modules: Dict[str, List[str]] = {}
        # 公共前缀只计算一次，避免在每文件循环内重复调用（O(n²)）
        try:
            common = os.path.commonpath(files) if files else ""
        except ValueError:
            common = ""
        for fp in files:
            rel = fp
            if common:
                try:
                    rel = os.path.relpath(fp, common)
                except ValueError:
                    # relpath 失败（跨盘符），保留绝对路径
                    pass
            parts = rel.replace(os.sep, "/").split("/")
            mod = parts[0] if len(parts) > 1 else "__root__"
            modules.setdefault(mod, []).append(fp)

        conf_map: Dict[str, str] = {}
        for mod, mod_files in modules.items():
            doc_files = 0
            total_func = 0
            doc_func = 0
            for fp in mod_files:
                d = ast_cache.get(fp)
                if not d:
                    continue
                if (d.get("module_docstring") or "").strip():
                    doc_files += 1
                for f in d.get("functions", []):
                    total_func += 1
                    doc_func += 1 if (f.get("docstring") or "").strip() else 0
                for c in d.get("classes", []):
                    for m in c.get("methods", []):
                        total_func += 1
                        doc_func += 1 if (m.get("docstring") or "").strip() else 0

            if total_func:
                ratio = doc_func / total_func
            else:
                # 无函数：以模块 docstring 覆盖度近似
                ratio = doc_files / len(mod_files) if mod_files else 0.0

            if ratio >= _DOC_RATIO_HIGH:
                conf_map[mod] = _CONF_HIGH
            elif ratio >= _DOC_RATIO_MEDIUM:
                conf_map[mod] = _CONF_MEDIUM
            else:
                conf_map[mod] = _CONF_LOW
        return conf_map

    # ─── 认知地图 HTML（复用 health_dashboard 渲染风格） ───

    def _render_dashboard(self, project_path: str, coverage: float,
                          confidence_map: Dict[str, str], blindspots: List[dict],
                          total_files: int, indexed_files: int) -> tuple:
        project_name = os.path.basename(project_path.rstrip(os.sep)) or project_path
        build_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        # 覆盖度进度条
        cov_pct = min(100.0, coverage)
        cov_color = "#10b981" if coverage >= 80 else ("#3b82f6" if coverage >= 60
                                                      else ("#f59e0b" if coverage >= 40 else "#ef4444"))

        # 每模块置信度进度条
        mod_rows = []
        for mod, conf in sorted(confidence_map.items()):
            if conf == _CONF_HIGH:
                pct, color = 90, "#10b981"
            elif conf == _CONF_MEDIUM:
                pct, color = 55, "#f59e0b"
            else:
                pct, color = 25, "#ef4444"
            mod_rows.append(f'''<div class="tool-row">
                <div class="tool-label">
                    <span class="tool-name">{self._esc(mod)}</span>
                    <span class="tool-count">{_CONF_LABEL.get(conf, conf)} 置信</span>
                </div>
                <div class="progress-bar">
                    <div class="seg" style="width:{pct}%;background:{color}"></div>
                </div>
            </div>''')
        mods_html = "\n".join(mod_rows) if mod_rows else \
            '<div class="empty-state">暂无模块置信度数据，请先执行 memory_sync</div>'

        # 盲区列表
        bs_rows = []
        for s in blindspots if blindspots else []:
            risk = s.get("risk_level", "low")
            sev_cls = "sev-" + (risk if risk in ("critical", "high", "medium", "low") else "medium")
            cat = self._esc(s.get("category_label") or s.get("category", "未知"))
            item = s.get("item", "")
            detail = self._esc(s.get("detail", "") or "")
            bs_rows.append(f'''<div class="risk-item">
                <span class="risk-severity {sev_cls}">{self._esc(risk)}</span>
                <div class="risk-info">
                    <div class="risk-title">{cat} · {self._esc(item)}</div>
                    <div class="risk-meta">{detail}</div>
                </div>
            </div>''')
        bs_html = "\n".join(bs_rows) if bs_rows else \
            '<div class="empty-state">未检测到认知盲区</div>'

        html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>AI 代码认知记忆地图 - {self._esc(project_name)}</title>
<style>
* {{ margin:0; padding:0; box-sizing:border-box; }}
body {{
    font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC",
                 "Microsoft YaHei","Helvetica Neue",Arial,sans-serif;
    background:#0f1117; color:#e5e7eb; line-height:1.6; min-height:100vh;
}}
.container {{ max-width:1100px; margin:0 auto; padding:24px; }}
.header {{
    display:flex; justify-content:space-between; align-items:center;
    margin-bottom:24px; padding:20px 28px;
    background:linear-gradient(135deg,#1a1d2e 0%,#1e2130 100%);
    border-radius:12px; border:1px solid #2a2d3a;
}}
.header h1 {{ font-size:24px; font-weight:700; color:#f1f5f9; }}
.header .timestamp {{ font-size:13px; color:#6b7280; }}
.overview {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(200px,1fr)); gap:16px; margin-bottom:24px; }}
.card {{ background:#1a1d2e; border-radius:10px; padding:18px 20px; border:1px solid #2a2d3a; }}
.card .label {{ font-size:12px; color:#6b7280; letter-spacing:.5px; margin-bottom:6px; }}
.card .value {{ font-size:28px; font-weight:700; color:#f1f5f9; }}
.card .value.small {{ font-size:18px; }}
.card .sub {{ font-size:12px; color:#6b7280; margin-top:4px; }}
.columns {{ display:grid; grid-template-columns:1fr 1fr; gap:16px; margin-bottom:24px; }}
@media (max-width:900px) {{ .columns {{ grid-template-columns:1fr; }} }}
.panel {{ background:#1a1d2e; border-radius:10px; border:1px solid #2a2d3a; overflow:hidden; }}
.panel-header {{ padding:14px 20px; border-bottom:1px solid #2a2d3a; font-size:14px; font-weight:600; color:#d1d5db; }}
.panel-body {{ padding:16px 20px; }}
.tool-row {{ margin-bottom:14px; }}
.tool-row:last-child {{ margin-bottom:0; }}
.tool-label {{ display:flex; justify-content:space-between; align-items:center; margin-bottom:4px; font-size:13px; }}
.tool-name {{ color:#c9cdd4; font-weight:500; }}
.tool-count {{ color:#6b7280; font-size:12px; }}
.progress-bar {{ height:10px; background:#2a2d3a; border-radius:5px; overflow:hidden; }}
.progress-bar .seg {{ height:100%; transition:width .3s; }}
.risk-list {{ max-height:420px; overflow-y:auto; }}
.risk-item {{ padding:10px 0; border-bottom:1px solid #252836; display:flex; gap:10px; align-items:flex-start; }}
.risk-item:last-child {{ border-bottom:none; }}
.risk-severity {{ display:inline-block; padding:2px 8px; border-radius:4px; font-size:11px; font-weight:600; white-space:nowrap; flex-shrink:0; }}
.sev-critical,.sev-high {{ background:#7f1d1d; color:#fca5a5; }}
.sev-medium {{ background:#78350f; color:#fcd34d; }}
.sev-low {{ background:#1f2937; color:#9ca3af; }}
.risk-info {{ flex:1; min-width:0; }}
.risk-title {{ font-size:13px; color:#e5e7eb; font-weight:500; }}
.risk-meta {{ font-size:11px; color:#6b7280; margin-top:2px; }}
.empty-state {{ text-align:center; color:#6b7280; padding:30px 0; font-size:14px; }}
.footer {{ text-align:center; padding:20px; color:#4b5563; font-size:12px; border-top:1px solid #1f2937; margin-top:24px; }}
::-webkit-scrollbar {{ width:6px; }}
::-webkit-scrollbar-track {{ background:#14171f; }}
::-webkit-scrollbar-thumb {{ background:#374151; border-radius:3px; }}
</style>
</head>
<body>
<div class="container">
<div class="header">
    <h1>AI 代码认知记忆地图</h1>
    <span class="timestamp">生成时间: {build_time}</span>
</div>
<div class="overview">
    <div class="card">
        <div class="label">项目名称</div>
        <div class="value small">{self._esc(project_name)}</div>
        <div class="sub">{self._esc(project_path)}</div>
    </div>
    <div class="card">
        <div class="label">代码文件总数</div>
        <div class="value">{total_files}</div>
        <div class="sub">已索引 {indexed_files} 个</div>
    </div>
    <div class="card">
        <div class="label">认知覆盖率</div>
        <div class="value" style="color:{cov_color}">{coverage:.1f}%</div>
        <div class="sub">已索引/语义化模块比例</div>
    </div>
    <div class="card">
        <div class="label">认知盲区</div>
        <div class="value">{len(blindspots)}</div>
        <div class="sub">需要关注的认知缺口</div>
    </div>
</div>
<div class="columns">
    <div class="panel">
        <div class="panel-header">认知覆盖度</div>
        <div class="panel-body">
            <div class="tool-row">
                <div class="tool-label">
                    <span class="tool-name">整体索引覆盖</span>
                    <span class="tool-count">{coverage:.1f}%</span>
                </div>
                <div class="progress-bar">
                    <div class="seg" style="width:{cov_pct}%;background:{cov_color}"></div>
                </div>
            </div>
        </div>
    </div>
    <div class="panel">
        <div class="panel-header">模块置信度</div>
        <div class="panel-body">
            {mods_html}
        </div>
    </div>
</div>
<div class="panel">
    <div class="panel-header">认知盲区清单（共 {len(blindspots)} 条）</div>
    <div class="panel-body">
        <div class="risk-list">
            {bs_html}
        </div>
    </div>
</div>
<div class="footer">
    CodeRef MemoryLayer 认知记忆地图 &middot; 由 CodeRef-AI 自动生成
</div>
</div>
</body>
</html>"""

        # 输出到 coderef-report
        os.makedirs(_REPORT_DIR, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"memory_cognitive_map_{self._project_hash(project_path)}_{ts}.html"
        filepath = os.path.join(_REPORT_DIR, filename)
        try:
            with open(filepath, "w", encoding="utf-8") as f:
                f.write(html)
        except Exception as e:
            logger.warning(f"[MemoryLayer] 认知地图写入失败: {e}")
            filepath = ""

        return filepath, html

    @staticmethod
    def _esc(text: Any) -> str:
        """HTML 转义"""
        if text is None:
            return ""
        return (str(text).replace("&", "&amp;").replace("<", "&lt;")
                .replace(">", "&gt;").replace('"', "&quot;"))


# 全局单例
memory_layer = MemoryLayer()