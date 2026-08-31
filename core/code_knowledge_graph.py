# -*- coding: utf-8 -*-
"""
CodeKnowledgeGraph v1.0 —— 持久化项目知识图谱

为编程 AI 提供结构化、可检索的项目记忆层。
SQLite 存储：节点（函数/类/模块/配置/常量/路由）+ 边（CALLS/IMPORTS/CONTAINS/INHERITS/REFERENCES/ROUTES_TO）

数据源：
  1. CodeAnalyzer.analyze_project() → CodeFile（函数/类/导入）
  2. AstParser.parse() → AstFileResult（调用关系/赋值/配置）
  3. GitNexus CSV → relations/community/process（调用链/集群/执行流）

存储路径：cache/kg/{project_md5}.db
"""

import os, sys, json, hashlib, sqlite3, csv, time, re
from datetime import datetime
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple, Any

from loguru import logger


# ═══════════════════════════════════════════════════════════════════
# 数据结构
# ═══════════════════════════════════════════════════════════════════

@dataclass
class KGNode:
    """知识图谱节点"""
    id: str
    type: str          # function / class / method / module / config / constant / route
    name: str
    file_path: str = ""
    start_line: int = 0
    end_line: int = 0
    props: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "id": self.id, "type": self.type, "name": self.name,
            "file_path": self.file_path, "start_line": self.start_line,
            "end_line": self.end_line, "props": self.props
        }


@dataclass
class KGEdge:
    """知识图谱边"""
    source: str
    target: str
    type: str         # CALLS / IMPORTS / CONTAINS / INHERITS / REFERENCES / ROUTES_TO
    props: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {"source": self.source, "target": self.target,
                "type": self.type, "props": self.props}


@dataclass
class KGQueryResult:
    """知识图谱查询结果"""
    nodes: List[KGNode] = field(default_factory=list)
    edges: List[KGEdge] = field(default_factory=list)
    total: int = 0
    query_type: str = ""


# ═══════════════════════════════════════════════════════════════════
# 模块级纯函数 —— CodeKnowledgeGraph._build_from_analysis 拆分出的
# 节点构造与 id 预收集逻辑（不依赖 self 状态）
# ═══════════════════════════════════════════════════════════════════

def _module_key(project_path: str, rel: str) -> str:
    """模块 id 前缀：相对 project_path 的路径去扩展名（正斜杠）。

    用相对路径而非 basename 作 id 前缀，避免跨目录同名文件（如
    业务工具/engine.py 与 分析中心/engine.py）生成相同 id 被
    INSERT OR REPLACE 互相覆盖，导致图谱漏扫（真实存量工程治理中发现的问题）。
    """
    if not rel:
        return ""
    if project_path and os.path.isabs(rel):
        try:
            rel = os.path.relpath(rel, project_path)
        except Exception:
            pass
    rel = rel.replace("\\", "/")
    return os.path.splitext(rel)[0]


def _collect_analysis_ids(analysis):
    """预收集项目内模块节点 id 与「类名→类节点 id」映射。

    mod_ids 供 IMPORTS 边过滤：仅当 import 目标是项目内真实存在的模块才建边，
    排除标准库/第三方导入，避免 memory_quality 把 `import os` 这类指向不存在
    节点的边反复报为孤儿边。
    class_ids_by_name 供 INHERITS 边做与 IMPORTS 一致的目标存在性过滤：仅当
    基类是项目内已注册的类才建边，排除 str/Enum/unittest.TestCase/HTMLParser
    等标准库或第三方基类，避免指向不存在节点的孤儿 INHERITS 边。
    """
    proj = getattr(analysis, "project_path", "") or ""
    mod_ids = {
        f"mod:{_module_key(proj, getattr(cf, 'file_path', ''))}"
        for cf in getattr(analysis, "files", [])
        if getattr(cf, "file_path", "")
    }
    class_ids_by_name: Dict[str, str] = {}
    for cf in getattr(analysis, "files", []):
        _rel = getattr(cf, "file_path", "")
        if not _rel:
            continue
        _key = _module_key(proj, _rel)
        for _cls in getattr(cf, "classes", []):
            class_ids_by_name.setdefault(
                _cls.name, f"class:{_key}:{_cls.name}")
    return mod_ids, class_ids_by_name


def _kg_module_node(cf, rel: str, module_key: str, module_name: str) -> KGNode:
    """构造模块节点（mod:<相对路径>，name 保留 basename）"""
    return KGNode(
        id=f"mod:{module_key}", type="module", name=module_name,
        file_path=rel, props={"language": getattr(cf, "language", "")})


def _kg_function_node(rel: str, module_key: str, func) -> KGNode:
    """构造函数节点（func:<相对路径>:<函数名>）"""
    fid = f"func:{module_key}:{func.name}"
    return KGNode(
        id=fid, type="function", name=func.name,
        file_path=rel,
        start_line=getattr(func, "start_line", 0),
        end_line=getattr(func, "end_line", 0),
        props={"params": getattr(func, "parameters", []),
               "doc": (getattr(func, "docstring", "") or "")[:200],
               "return_type": getattr(func, "return_type", "") or ""})


def _kg_class_node(rel: str, module_key: str, cls) -> KGNode:
    """构造类节点（class:<相对路径>:<类名>）"""
    cid = f"class:{module_key}:{cls.name}"
    return KGNode(
        id=cid, type="class", name=cls.name,
        file_path=rel,
        start_line=getattr(cls, "start_line", 0),
        end_line=getattr(cls, "end_line", 0),
        props={"bases": getattr(cls, "base_classes", []),
               "doc": (getattr(cls, "docstring", "") or "")[:200]})


def _kg_method_node(rel: str, module_key: str, cls, m) -> KGNode:
    """构造方法节点（method:<相对路径>:<类名>.<方法名>）"""
    mid = f"method:{module_key}:{cls.name}.{m.name}"
    return KGNode(
        id=mid, type="method", name=f"{cls.name}.{m.name}",
        file_path=rel,
        start_line=getattr(m, "start_line", 0),
        end_line=getattr(m, "end_line", 0),
        props={"params": getattr(m, "parameters", []),
               "doc": (getattr(m, "docstring", "") or "")[:200]})


def _resolve_import_target(imp: str, mod_ids: set) -> str:
    """解析 import 语句的项目内目标模块 id；未命中返回空串。

    先按点分路径转斜杠逐段精确匹配（如 `data_loader.engine` →
    `mod:data_loader/engine`），再按最后一段模糊匹配跨目录同名模块
    （如 `import engine` 命中 `mod:data_loader/engine`）。避免标准库/
    第三方导入产生孤儿边。
    """
    parts = [p for p in imp.split(".") if p]
    for i in range(len(parts), 0, -1):
        key = "/".join(parts[:i])
        if f"mod:{key}" in mod_ids:
            return f"mod:{key}"
    if parts:
        tail = parts[-1]
        matches = [
            mid for mid in mod_ids
            if mid == f"mod:{tail}" or mid.endswith(f"/{tail}")
        ]
        if len(matches) == 1:
            return matches[0]
    return ""


def _go_receiver_type(recv: str) -> str:
    """解析 Go 方法定义的 Receiver 类型名（如 Indexer）；无 Receiver 返回空串。

    value receiver 形如 `(i Indexer)`（含右括号），需允许尾部 `)` 才能正确
    取到 `Indexer`；pointer receiver `(*Recv)` 走第一分支。
    """
    if not recv:
        return ""
    rm = re.search(
        r'\*\s*([A-Za-z_]\w*)\s*\)?\s*$'
        r'|(?:^|\s)([A-Za-z_]\w*)\s*\)?\s*$',
        recv.strip())
    if rm:
        return rm.group(1) or rm.group(2)
    return ""


def _go_func_kgnode(fp: str, module_key: str, node_name: str,
                    start_line: int, end_line: int, params, body: str) -> KGNode:
    """构造 Go 函数节点（gofunc:<相对路径>:<名称>），函数体截断存入 props['doc']"""
    nid = f"gofunc:{module_key}:{node_name}"
    return KGNode(
        id=nid, type="go_func", name=node_name,
        file_path=fp, start_line=start_line, end_line=end_line,
        props={"language": "go", "params": params, "doc": body[:1000]})


def _go_calls_edge(nid: str, tgt: str, start_line: int, body: str, cm) -> KGEdge:
    """构造 Go 函数体内调用的 CALLS 边（line 为调用点在原文件中的行号）"""
    return KGEdge(
        source=nid, target=tgt, type="CALLS",
        props={"line": start_line - 1 + body[:cm.start()].count('\n'),
               "full_name": cm.group(0).rstrip('(')})


# ═══════════════════════════════════════════════════════════════════
# 知识图谱引擎
# ═══════════════════════════════════════════════════════════════════

class CodeKnowledgeGraph:
    """持久化项目知识图谱"""

    SCHEMA_VERSION = 1

    def __init__(self, project_path: str, db_path: Optional[str] = None):
        self.project_path = os.path.abspath(project_path)
        self._phash = hashlib.md5(self.project_path.encode()).hexdigest()[:12]
        # 显式 db_path 优先（供 verify_findings 等跨项目复用图谱），缺省按 project_path 生成
        self._db_path = os.path.abspath(db_path) if db_path else self._make_db_path()
        self._conn: Optional[sqlite3.Connection] = None

    # ─── 路径 ───

    @staticmethod
    def _kg_dir() -> str:
        # 缺省图谱库跟随被检项目自身（project_path/cache/kg），而非安装根：
        # 避免多项目/跨仓协作时把 9.7MB+ 图谱库写进对方 cwd（真实红线段落，r6）。
        # 静态法无法拿 project_path，故仅由 _make_db_path 覆写，此处保留兜底。
        d = os.path.join(os.getcwd(), "cache", "kg")
        os.makedirs(d, exist_ok=True)
        return d

    def _make_db_path(self) -> str:
        # 图谱库落在被检项目内，随项目走、不污染其他仓库；先建父目录避免 sqlite 打不开
        d = os.path.join(self.project_path, "cache", "kg")
        os.makedirs(d, exist_ok=True)
        return os.path.join(d, f"{self._phash}.db")

    @property
    def db_path(self) -> str:
        return self._db_path

    # ─── 连接 ───

    def _connect(self):
        if self._conn is None:
            self._conn = sqlite3.connect(self._db_path)
            self._conn.row_factory = sqlite3.Row
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA synchronous=NORMAL")

    def close(self):
        if self._conn:
            self._conn.close()
            self._conn = None

    def __enter__(self):
        self._connect()
        return self

    def __exit__(self, *args):
        self.close()

    # ─── 建表 ───

    def _init_schema(self):
        self._connect()
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS nodes (
                id TEXT PRIMARY KEY,
                type TEXT NOT NULL,
                name TEXT NOT NULL,
                file_path TEXT DEFAULT '',
                start_line INTEGER DEFAULT 0,
                end_line INTEGER DEFAULT 0,
                props TEXT DEFAULT '{}'
            );
            CREATE INDEX IF NOT EXISTS idx_nodes_type ON nodes(type);
            CREATE INDEX IF NOT EXISTS idx_nodes_name ON nodes(name);
            CREATE INDEX IF NOT EXISTS idx_nodes_file ON nodes(file_path);

            CREATE TABLE IF NOT EXISTS edges (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source TEXT NOT NULL,
                target TEXT NOT NULL,
                type TEXT NOT NULL,
                props TEXT DEFAULT '{}'
            );
            CREATE INDEX IF NOT EXISTS idx_edges_source ON edges(source);
            CREATE INDEX IF NOT EXISTS idx_edges_target ON edges(target);
            CREATE INDEX IF NOT EXISTS idx_edges_type ON edges(type);

            CREATE TABLE IF NOT EXISTS meta (
                key TEXT PRIMARY KEY,
                value TEXT DEFAULT ''
            );
        """)
        self._conn.commit()

    # ─── 是否存在 / 是否过期 ───

    def exists(self) -> bool:
        return os.path.exists(self._db_path)

    def is_stale(self, max_age_hours: int = 24) -> bool:
        """检查知识图谱是否过期（超过 max_age_hours 小时）"""
        if not self.exists():
            return True
        try:
            self._connect()
            row = self._conn.execute(
                "SELECT value FROM meta WHERE key='built_at'").fetchone()
            if row:
                built_at = float(row[0])
                return (time.time() - built_at) > max_age_hours * 3600
        except: pass
        return True

    def get_built_at(self) -> Optional[str]:
        """获取构建时间"""
        if not self.exists():
            return None
        try:
            self._connect()
            row = self._conn.execute(
                "SELECT value FROM meta WHERE key='built_at'").fetchone()
            if row:
                return datetime.fromtimestamp(float(row[0])).strftime("%Y-%m-%d %H:%M:%S")
        except: pass
        return None

    # ═══════════════════════════════════════════════════════════════════
    # 构建
    # ═══════════════════════════════════════════════════════════════════

    def build(self, analysis=None, ast_results=None, gitnexus_dir=None) -> dict:
        """
        构建知识图谱。

        Args:
            analysis: CodeAnalyzer.analyze_project() 返回值（ProjectAnalysis）
            ast_results: AstParser 批量解析结果 Dict[str, AstFileResult]
            gitnexus_dir: .gitnexus/csv/ 目录路径

        Returns:
            {"nodes": N, "edges": M, "errors": [...]}
        """
        self._init_schema()
        self._clear()
        stats = {"nodes": 0, "edges": 0, "errors": []}

        try:
            if analysis:
                self._build_from_analysis(analysis, stats)
            if ast_results:
                self._build_from_ast(ast_results, stats)
            if gitnexus_dir and os.path.isdir(gitnexus_dir):
                self._build_from_gitnexus(gitnexus_dir, stats)
            # 扫描 Go 文件（含 Go 组件的项目流程断链检测补盲；纯 Python 项目零开销）
            self._build_from_go(self.project_path, stats)
        except Exception as e:
            stats["errors"].append(str(e))
            logger.error(f"[KG] 构建失败: {e}")

        self._set_meta("built_at", str(time.time()))
        self._set_meta("project_path", self.project_path)
        self._set_meta("schema_version", str(self.SCHEMA_VERSION))
        self._conn.commit()
        logger.info(f"[KG] 构建完成: {stats['nodes']} 节点, {stats['edges']} 边")
        return stats

    def _clear(self):
        self._conn.execute("DELETE FROM nodes")
        self._conn.execute("DELETE FROM edges")
        self._conn.execute("DELETE FROM meta")

    def _set_meta(self, key: str, value: str):
        self._conn.execute(
            "INSERT OR REPLACE INTO meta(key,value) VALUES(?,?)", (key, value))

    # ─── 从 CodeAnalyzer 构建 ───

    def _build_from_analysis(self, analysis, stats: dict):
        """从 CodeAnalyzer.analyze_project() 的 ProjectAnalysis 构建节点

        拆分说明：id 预收集与各类节点构造提取为模块级 _collect_analysis_ids /
        _kg_*_node / _resolve_import_target 纯函数，本方法仅作编排，
        节点/边内容与建边过滤语义与拆分前逐字段一致。
        """
        mod_ids, class_ids_by_name = _collect_analysis_ids(analysis)
        proj = getattr(analysis, "project_path", "") or ""
        n = 0
        for cf in getattr(analysis, "files", []):
            rel = getattr(cf, "file_path", "")
            if not rel:
                continue

            # 模块节点（id 前缀用相对路径，跨目录同名文件不冲突）
            module_name = os.path.splitext(os.path.basename(rel))[0]
            module_key = _module_key(proj, rel)
            module_id = f"mod:{module_key}"
            self._upsert_node(_kg_module_node(cf, rel, module_key, module_name))
            n += 1

            # 函数节点 + CONTAINS 边
            for func in getattr(cf, "functions", []):
                fid = f"func:{module_key}:{func.name}"
                self._upsert_node(_kg_function_node(rel, module_key, func))
                n += 1
                self._upsert_edge(KGEdge(source=module_id, target=fid, type="CONTAINS"))

            # 类节点 + 方法节点 + CONTAINS/INHERITS 边
            for cls in getattr(cf, "classes", []):
                cid = f"class:{module_key}:{cls.name}"
                self._upsert_node(_kg_class_node(rel, module_key, cls))
                n += 1
                self._upsert_edge(KGEdge(source=module_id, target=cid, type="CONTAINS"))

                for m in getattr(cls, "methods", []):
                    mid = f"method:{module_key}:{cls.name}.{m.name}"
                    self._upsert_node(_kg_method_node(rel, module_key, cls, m))
                    n += 1
                    self._upsert_edge(KGEdge(source=cid, target=mid, type="CONTAINS"))

                # 继承边：基类须是项目内已注册的类节点才建边（对齐 IMPORTS 的
                # 目标存在性过滤）。基类名可能带模块前缀（如 unittest.TestCase），
                # 取最末段标识符参与匹配；标准库/第三方基类不建边，避免孤儿边。
                for base in getattr(cls, "base_classes", []):
                    base_id = class_ids_by_name.get(base.split(".")[-1])
                    if not base_id:
                        continue
                    self._upsert_edge(KGEdge(source=cid, target=base_id, type="INHERITS"))

            # 导入边：仅当 import 目标是项目内真实存在的模块才建边
            for imp in getattr(cf, "imports", []):
                target_mod = _resolve_import_target(imp, mod_ids)
                if not target_mod:
                    continue
                self._upsert_edge(KGEdge(
                    source=module_id, target=target_mod, type="IMPORTS",
                    props={"full": imp}))

        stats["nodes"] += n
        stats["edges"] += n  # 每个节点至少一条 CONTAINS 边

    # ─── 从 AstParser 构建 ───

    def _build_from_ast(self, ast_results: dict, stats: dict):
        """从 AstParser 批量解析结果构建调用关系和配置节点"""
        n = 0
        for file_path, ar in ast_results.items():
            module_key = _module_key(self.project_path, file_path)
            rel = file_path

            # 调用关系 → CALLS 边
            for call in getattr(ar, "calls", []):
                # 尝试找到调用所在的函数
                caller_id = self._find_containing_node(rel, call.line)
                if not caller_id:
                    caller_id = f"mod:{module_key}"

                # 被调用者解析优先级（ / CodeRabbit major）：
                #   1) 全名精确匹配（可命中带类/模块前缀的方法节点，如 Bot.run_bot）；
                #   2) self/cls 调用 → 按调用者所在模块+类构造完整方法 id 精确匹配
                #      （self.run_bot → method:<调用者mod>:<调用者类>.run_bot），避免与
                #      其他模块同名类方法或顶层同名函数撞 CALLS 边；
                #   3) 回退短名模糊匹配（漏建则 callers 查询返空）。
                callee_id = self._find_node_by_name(call.func_name)
                if not callee_id:
                    first, _, rest = call.func_name.partition(".")
                    if first in ("self", "cls") and rest:
                        mid = self._caller_method_id(caller_id, rest)
                        if mid and self._node_exists(mid):
                            callee_id = mid
                if not callee_id:
                    callee_name = call.func_name.split(".")[-1]
                    callee_id = self._find_node_by_name(callee_name)
                if callee_id:
                    self._upsert_edge(KGEdge(
                        source=caller_id, target=callee_id, type="CALLS",
                        props={"line": call.line, "full_name": call.func_name,
                               "keyword_args": list(getattr(call, "keyword_args", []))}))
                    n += 1

            # 赋值语句 → Config / Constant 节点
            for assign in getattr(ar, "assignments", []):
                cat = assign.category
                if cat in ("constant", "config", "hardcoded"):
                    node_type = "config" if cat in ("config", "hardcoded") else "constant"
                    aid = f"{node_type}:{module_key}:{assign.target}"
                    self._upsert_node(KGNode(
                        id=aid, type=node_type, name=assign.target,
                        file_path=rel, start_line=assign.line,
                        props={"value": assign.value_repr[:200],
                               "category": cat}))
                    n += 1

                    # REFERENCES 边（从所在函数引用此配置/常量）
                    container = self._find_containing_node(rel, assign.line)
                    if container:
                        self._upsert_edge(KGEdge(
                            source=container, target=aid, type="REFERENCES"))
                        n += 1

        stats["nodes"] += n

    # ─── 从 Go 文件构建（flow_verify 跨语言补盲） ───

    # Go 函数/方法定义：`func Name(` 或 `func (r *Recv) Name(`
    _GO_FUNC_RE = re.compile(
        r'^\s*func\s+(?:(\([^)]*\))\s+)?([A-Za-z_]\w*)\s*\(', re.MULTILINE)
    # Go 函数/方法调用：`obj.Method(` / `pkg.Func(` / `Func(`（取最末一段标识符）
    _GO_CALL_RE = re.compile(
        r'(?<![\w.\[])(?:[A-Za-z_]\w*\.)*([A-Za-z_]\w*)\s*\(')

    def _build_from_go(self, project_path, stats: dict):
        """扫描 .go 文件，用轻量确证性正则把函数/方法与调用关系纳入知识图谱。

        背景：知识图谱此前只覆盖 Python AST，含 Go 组件的项目（如 目标项目 的
        augmented/ 增强检索）流程断链检测漏报。此方法用确证性正则（不装完整 Go
        AST parser，零外部依赖）为每个 Go 函数/方法生成 go_func 节点，并把函数体
        写入 props['doc']（供 flow_verify 的 keyword/doc 检索命中），同时提取函数
        体内调用生成 CALLS 边。对纯 Python 项目安全（无 .go 文件即零开销）。
        """
        n_nodes = 0
        n_edges = 0
        # 第一趟：遍历所有 .go 文件，注册每个函数节点，同时收集函数体/位置信息
        # 供第二趟统一建边。两趟分离使 CALLS 边解析不依赖 os.walk 顺序——
        # 被调用函数即便位于更晚遍历的文件中也已先注册，避免漏建跨文件边。
        go_calls: List[tuple] = []
        for root, dirs, files in os.walk(project_path):
            dirs[:] = [d for d in dirs if not d.startswith('.') and d not in
                       ('node_modules', 'venv', '.venv', 'env', '.git', 'dist',
                        'build', 'vendor')]
            for fn in files:
                if not fn.endswith('.go'):
                    continue
                fp = os.path.join(root, fn)
                try:
                    with open(fp, 'r', encoding='utf-8', errors='ignore') as fh:
                        content = fh.read()
                except Exception as e:
                    stats["errors"].append(f"Go 读取失败 {fp}: {e}")
                    continue
                lines = content.splitlines()
                funcs = list(self._GO_FUNC_RE.finditer(content))
                module_key = _module_key(project_path, fp)
                for i, m in enumerate(funcs):
                    recv = m.group(1)
                    name = m.group(2)
                    start_line = content[:m.start()].count('\n') + 1
                    # 函数体：从定义行到下一个函数定义（或文件末尾）
                    end_line = (content[:funcs[i + 1].start()].count('\n')
                                if i + 1 < len(funcs) else len(lines))
                    body = "\n".join(lines[start_line - 1:end_line])
                    # 节点名：方法带 Receiver 类型前缀，如 Indexer.FullRebuild
                    recv_type = _go_receiver_type(recv)
                    node_name = f"{recv_type}.{name}" if recv_type else name
                    go_node = _go_func_kgnode(
                        fp, module_key, node_name, start_line, end_line,
                        self._go_params(content, m.end()), body)
                    self._upsert_node(go_node)
                    n_nodes += 1
                    go_calls.append((go_node.id, body, start_line))
        # 第二趟：所有 go_func 节点已注册，再解析函数体内调用 → CALLS 边
        for nid, body, start_line in go_calls:
            for cm in self._GO_CALL_RE.finditer(body):
                callee = cm.group(1)
                # Go 关键字 / 内置函数：_GO_CALL_RE 会命中 `if(`、`len(` 等
                if callee in self._GO_KEYWORDS:
                    continue
                tgt = self._find_go_callee(callee)
                if tgt and tgt != nid:
                    self._upsert_edge(_go_calls_edge(nid, tgt, start_line, body, cm))
                    n_edges += 1
        stats["nodes"] += n_nodes
        stats["edges"] += n_edges

    # Go 语言关键字 / 内置函数：_GO_CALL_RE 会命中 `if(`、`len(` 等，需在建边前跳过
    _GO_KEYWORDS = frozenset({
        "if", "for", "switch", "select", "go", "defer", "func", "return",
        "len", "cap", "append", "copy", "delete", "make", "new", "panic",
        "recover", "close", "clear", "min", "max", "print", "println",
        "range", "case", "default", "break", "continue", "fallthrough",
    })

    def _find_go_callee(self, name: str) -> Optional[str]:
        """按 Go 被调名定位图谱节点：先精确匹配名，再匹配 `Receiver.Method` 后缀。

        仅匹配 go_func 节点，避免跨语言同名节点（如 Python 函数）被误连。
        """
        row = self._conn.execute(
            "SELECT id FROM nodes WHERE type='go_func' AND name=? LIMIT 1",
            (name,)).fetchone()
        if row:
            return row["id"]
        row = self._conn.execute(
            "SELECT id FROM nodes WHERE type='go_func' AND name LIKE ? LIMIT 1",
            (f"%.{name}",)).fetchone()
        return row["id"] if row else None

    @staticmethod
    def _go_params(content: str, pos: int) -> List[str]:
        """从函数名 `(` 之后浅析取逗号分隔的形参名（仅展示用，非完整解析）。"""
        depth = 0
        i = pos
        parts = []
        start = pos
        while i < len(content):
            c = content[i]
            if c == '(':
                depth += 1
            elif c == ')':
                if depth == 0:
                    parts.append(content[start:i])
                    break
                depth -= 1
            elif c == ',' and depth == 1:
                parts.append(content[start:i])
                start = i + 1
            i += 1
        names = []
        for p in parts:
            pm = re.match(r'\s*([A-Za-z_]\w*)', p)
            if pm:
                names.append(pm.group(1))
        return names

    # ─── 从 GitNexus CSV 构建 ───

    def _build_from_gitnexus(self, csv_dir: str, stats: dict):
        """从 GitNexus CSV 加载 relations 和 community"""
        n = 0

        # relations.csv
        rel_path = os.path.join(csv_dir, "relations.csv")
        if os.path.exists(rel_path):
            try:
                with open(rel_path, "r", encoding="utf-8") as f:
                    reader = csv.DictReader(f)
                    for row in reader:
                        src = row.get("from", "")
                        tgt = row.get("to", "")
                        rtype = row.get("type", "")
                        if src and tgt:
                            # GitNexus 的 ID 可能含路径，我们尝试匹配
                            src_id = self._find_or_create_ref(src)
                            tgt_id = self._find_or_create_ref(tgt)
                            self._upsert_edge(KGEdge(
                                source=src_id, target=tgt_id, type=rtype,
                                props={"confidence": row.get("confidence", ""),
                                       "reason": row.get("reason", "")}))
                            n += 1
            except Exception as e:
                stats["errors"].append(f"GitNexus relations: {e}")

        # community.csv → 更新节点 props
        comm_path = os.path.join(csv_dir, "community.csv")
        if os.path.exists(comm_path):
            try:
                with open(comm_path, "r", encoding="utf-8") as f:
                    reader = csv.DictReader(f)
                    for row in reader:
                        label = row.get("label", "")
                        cohesion = row.get("cohesion", "")
                        if label:
                            # 更新所有匹配节点的 community 属性
                            self._conn.execute(
                                "UPDATE nodes SET props = json_set(props, '$.community', ?) "
                                "WHERE name = ? OR file_path LIKE ?",
                                (label, label, f"%{label}%"))
            except Exception as e:
                stats["errors"].append(f"GitNexus community: {e}")

        stats["edges"] += n

    # ═══════════════════════════════════════════════════════════════════
    # 查询
    # ═══════════════════════════════════════════════════════════════════

    def _row_to_node(self, row) -> KGNode:
        return KGNode(
            id=row["id"], type=row["type"], name=row["name"],
            file_path=row["file_path"] or "",
            start_line=row["start_line"] or 0,
            end_line=row["end_line"] or 0,
            props=json.loads(row["props"] or "{}"))

    def _row_to_edge(self, row) -> KGEdge:
        return KGEdge(
            source=row["source"], target=row["target"],
            type=row["type"],
            props=json.loads(row["props"] or "{}"))

    def _upsert_node(self, node: KGNode):
        self._conn.execute(
            """INSERT OR REPLACE INTO nodes(id,type,name,file_path,start_line,end_line,props)
               VALUES(?,?,?,?,?,?,?)""",
            (node.id, node.type, node.name, node.file_path,
             node.start_line, node.end_line, json.dumps(node.props, ensure_ascii=False)))

    def _upsert_edge(self, edge: KGEdge):
        self._conn.execute(
            """INSERT OR IGNORE INTO edges(source,target,type,props)
               VALUES(?,?,?,?)""",
            (edge.source, edge.target, edge.type,
             json.dumps(edge.props, ensure_ascii=False)))

    def _find_node_by_name(self, name: str) -> Optional[str]:
        row = self._conn.execute(
            "SELECT id FROM nodes WHERE name=? LIMIT 1", (name,)).fetchone()
        if row:
            return row["id"]
        # 模糊回退 ：方法调用侧 name 常带类/模块前缀（如 Bot.run_bot），
        # 精确匹配 name='run_bot' 会失败而漏建 CALLS 边。仅当 LIKE 候选唯一时
        # 选中，避免多类同名方法跨类误归属；多候选则放弃。
        candidates = self._conn.execute(
            "SELECT id FROM nodes WHERE name LIKE ?", (f"%{name}%",)).fetchall()
        if len(candidates) == 1:
            return candidates[0]["id"]
        return None

    def _node_exists(self, node_id: str) -> bool:
        """节点 id 是否已注册（精确主键查询，避免跨模块歧义时误用 LIMIT 1 无序行）。"""
        if not node_id:
            return False
        row = self._conn.execute(
            "SELECT 1 FROM nodes WHERE id=? LIMIT 1", (node_id,)).fetchone()
        return row is not None

    def _caller_method_id(self, node_id: str, method_name: str) -> str:
        """从调用者节点 id 构造同模块方法目标 id（method:<mod>:<类>.<方法>）。

        self/cls 调用须解析为调用者所在模块+类的完整方法 id（self.run_bot →
        method:<调用者mod>:<调用者类>.run_bot），再精确主键匹配，避免与项目内其他
        模块的同名类方法或顶层同名函数撞 CALLS 边（CodeRabbit major）。非方法节点
        返回空串。
        """
        if not node_id or not node_id.startswith("method:"):
            return ""
        body = node_id[len("method:"):]  # '<mod>:<类>.<方法>'（module_key 可能含 ':'，用 rpartition 取末冒号）
        mod, _, cls_dot = body.rpartition(":")
        if not cls_dot:
            return ""
        cls = cls_dot.split(".", 1)[0]
        return f"method:{mod}:{cls}.{method_name}"

    def _find_containing_node(self, file_path: str, line: int) -> Optional[str]:
        """找到包含指定行号的函数/方法/类节点"""
        row = self._conn.execute(
            """SELECT id FROM nodes
               WHERE file_path=? AND start_line <= ? AND end_line >= ?
               AND type IN ('function','method','class')
               ORDER BY (end_line - start_line) ASC LIMIT 1""",
            (file_path, line, line)).fetchone()
        return row["id"] if row else None

    def _find_or_create_ref(self, name: str) -> str:
        """查找或创建引用节点（用于 GitNexus 关系）；引用名称精确匹配，
        不做子串模糊回退——避免 ref 端点为既有节点后缀（如 foo_service 命中 foo）
        而被误归属存假边（CodeRabbit major）。"""
        row = self._conn.execute(
            "SELECT id FROM nodes WHERE name=? LIMIT 1", (name,)).fetchone()
        if row:
            return row["id"]
        nid = f"ref:{name}"
        self._upsert_node(KGNode(id=nid, type="ref", name=name))
        return nid

    # ─── 公共查询 API ───

    def query_entity(self, name: str, entity_type: str = None) -> KGQueryResult:
        """按名称查询实体"""
        self._connect()
        sql = "SELECT * FROM nodes WHERE name LIKE ?"
        params = [f"%{name}%"]
        if entity_type:
            sql += " AND type = ?"
            params.append(entity_type)
        rows = self._conn.execute(sql + " LIMIT 50", params).fetchall()
        nodes = [self._row_to_node(r) for r in rows]
        return KGQueryResult(nodes=nodes, total=len(nodes), query_type="entity")

    def query_callers(self, func_name: str) -> KGQueryResult:
        """查询调用者：谁调用了这个函数"""
        self._connect()
        target = self._find_node_by_name(func_name)
        if not target:
            # 模糊匹配
            row = self._conn.execute(
                "SELECT id FROM nodes WHERE name LIKE ? LIMIT 1",
                (f"%{func_name}%",)).fetchone()
            if not row:
                return KGQueryResult(total=0, query_type="callers")
            target = row["id"]

        # 反向追踪 CALLS 边
        rows = self._conn.execute(
            """SELECT n.* FROM nodes n
               JOIN edges e ON e.source = n.id
               WHERE e.target = ? AND e.type = 'CALLS'
               LIMIT 50""", (target,)).fetchall()
        nodes = [self._row_to_node(r) for r in rows]
        return KGQueryResult(nodes=nodes, total=len(nodes), query_type="callers")

    def query_callees(self, func_name: str) -> KGQueryResult:
        """查询被调用者：这个函数调用了谁"""
        self._connect()
        source = self._find_node_by_name(func_name)
        if not source:
            row = self._conn.execute(
                "SELECT id FROM nodes WHERE name LIKE ? LIMIT 1",
                (f"%{func_name}%",)).fetchone()
            if not row:
                return KGQueryResult(total=0, query_type="callees")
            source = row["id"]

        rows = self._conn.execute(
            """SELECT n.* FROM nodes n
               JOIN edges e ON e.target = n.id
               WHERE e.source = ? AND e.type = 'CALLS'
               LIMIT 50""", (source,)).fetchall()
        nodes = [self._row_to_node(r) for r in rows]
        return KGQueryResult(nodes=nodes, total=len(nodes), query_type="callees")

    def query_impact(self, file_path: str) -> KGQueryResult:
        """修改影响分析：修改某个文件会影响哪些模块"""
        self._connect()
        # 找到文件中的所有节点
        nodes = self._conn.execute(
            "SELECT id FROM nodes WHERE file_path LIKE ?",
            (f"%{file_path}%",)).fetchall()
        if not nodes:
            return KGQueryResult(total=0, query_type="impact")

        node_ids = [n["id"] for n in nodes]

        # 正向追踪：谁导入了这个模块？
        affected = set()
        for nid in node_ids:
            # 查找所有引用此节点的边
            refs = self._conn.execute(
                """SELECT DISTINCT e.source FROM edges e
                   WHERE e.target = ? AND e.type IN ('CALLS','IMPORTS','REFERENCES')""",
                (nid,)).fetchall()
            for r in refs:
                affected.add(r["source"])

        # 加载受影响节点
        if affected:
            placeholders = ",".join("?" * len(affected))
            rows = self._conn.execute(
                f"SELECT * FROM nodes WHERE id IN ({placeholders}) LIMIT 50",
                list(affected)).fetchall()
            result_nodes = [self._row_to_node(r) for r in rows]
        else:
            result_nodes = []

        return KGQueryResult(
            nodes=result_nodes, total=len(result_nodes), query_type="impact")

    def query_relations(self, node_id: str) -> KGQueryResult:
        """查询节点的所有关系"""
        self._connect()
        edges = []
        rows = self._conn.execute(
            "SELECT * FROM edges WHERE source=? OR target=? LIMIT 100",
            (node_id, node_id)).fetchall()
        edges = [self._row_to_edge(r) for r in rows]

        # 收集相关节点
        node_ids = set()
        for e in edges:
            node_ids.add(e.source)
            node_ids.add(e.target)

        nodes = []
        if node_ids:
            placeholders = ",".join("?" * len(node_ids))
            rows = self._conn.execute(
                f"SELECT * FROM nodes WHERE id IN ({placeholders})",
                list(node_ids)).fetchall()
            nodes = [self._row_to_node(r) for r in rows]

        return KGQueryResult(
            nodes=nodes, edges=edges, total=len(edges), query_type="relations")

    def query_file_entities(self, file_path: str) -> KGQueryResult:
        """查询文件中的所有实体"""
        self._connect()
        rows = self._conn.execute(
            "SELECT * FROM nodes WHERE file_path LIKE ? ORDER BY start_line LIMIT 100",
            (f"%{file_path}%",)).fetchall()
        nodes = [self._row_to_node(r) for r in rows]
        return KGQueryResult(nodes=nodes, total=len(nodes), query_type="file_entities")

    def get_stats(self) -> dict:
        """获取知识图谱统计信息"""
        self._connect()
        node_count = self._conn.execute("SELECT COUNT(*) as c FROM nodes").fetchone()["c"]
        edge_count = self._conn.execute("SELECT COUNT(*) as c FROM edges").fetchone()["c"]

        type_counts = {}
        for row in self._conn.execute(
                "SELECT type, COUNT(*) as c FROM nodes GROUP BY type").fetchall():
            type_counts[row["type"]] = row["c"]

        edge_type_counts = {}
        for row in self._conn.execute(
                "SELECT type, COUNT(*) as c FROM edges GROUP BY type").fetchall():
            edge_type_counts[row["type"]] = row["c"]

        built_at = self.get_built_at()

        return {
            "node_count": node_count,
            "edge_count": edge_count,
            "node_types": type_counts,
            "edge_types": edge_type_counts,
            "built_at": built_at,
            "project_path": self.project_path,
            "db_path": self._db_path,
        }

    def get_node_ids(self) -> List[str]:
        """返回全部节点 ID（供完整性校验等上层模块使用，避免直接访问私有 _conn）。"""
        self._connect()
        return [r["id"] for r in self._conn.execute("SELECT id FROM nodes").fetchall()]

    def get_all_edges(self) -> List[Tuple[int, KGEdge]]:
        """返回全部边（含主键 id 与 source/target/type/props），供完整性校验遍历。
        返回 [(edge_id, KGEdge), ...]。"""
        self._connect()
        return [(r["id"], self._row_to_edge(r))
                for r in self._conn.execute("SELECT * FROM edges").fetchall()]

    def delete_orphan_edges(self, edge_ids: List[int]) -> int:
        """按主键删除边（用于清除孤儿边）。返回删除条数。"""
        if not edge_ids:
            return 0
        self._connect()
        rows = self._conn.executemany(
            "DELETE FROM edges WHERE id=?", [(eid,) for eid in edge_ids])
        self._conn.commit()
        return rows.rowcount if rows.rowcount != -1 else len(edge_ids)

    def search(self, keyword: str, limit: int = 30) -> KGQueryResult:
        """全文搜索：名称、文件路径、docstring"""
        self._connect()
        pattern = f"%{keyword}%"
        rows = self._conn.execute(
            """SELECT * FROM nodes
               WHERE name LIKE ? OR file_path LIKE ? OR props LIKE ?
               LIMIT ?""",
            (pattern, pattern, pattern, limit)).fetchall()
        nodes = [self._row_to_node(r) for r in rows]
        return KGQueryResult(nodes=nodes, total=len(nodes), query_type="search")

    def get_call_graph(self, func_name: str, depth: int = 2) -> KGQueryResult:
        """获取调用链子图（BFS 遍历指定深度）"""
        self._connect()
        start = self._find_node_by_name(func_name)
        if not start:
            row = self._conn.execute(
                "SELECT id FROM nodes WHERE name LIKE ? LIMIT 1",
                (f"%{func_name}%",)).fetchone()
            if not row:
                return KGQueryResult(total=0, query_type="call_graph")
            start = row["id"]

        visited_nodes = {start}
        visited_edges = set()
        frontier = {start}

        for _ in range(depth):
            next_frontier = set()
            for nid in frontier:
                rows = self._conn.execute(
                    "SELECT * FROM edges WHERE (source=? OR target=?) AND type='CALLS'",
                    (nid, nid)).fetchall()
                for r in rows:
                    e = self._row_to_edge(r)
                    ek = (e.source, e.target, e.type)
                    if ek not in visited_edges:
                        visited_edges.add(ek)
                        visited_nodes.add(e.source)
                        visited_nodes.add(e.target)
                        if e.source == nid:
                            next_frontier.add(e.target)
                        else:
                            next_frontier.add(e.source)
            frontier = next_frontier
            if not frontier:
                break

        # 加载节点
        nodes = []
        if visited_nodes:
            placeholders = ",".join("?" * len(visited_nodes))
            rows = self._conn.execute(
                f"SELECT * FROM nodes WHERE id IN ({placeholders})",
                list(visited_nodes)).fetchall()
            nodes = [self._row_to_node(r) for r in rows]

        # 加载边
        edges = []
        for ek in visited_edges:
            edges.append(KGEdge(source=ek[0], target=ek[1], type=ek[2]))

        return KGQueryResult(
            nodes=nodes, edges=edges, total=len(edges), query_type="call_graph")


# ═══════════════════════════════════════════════════════════════════
# 便捷函数
# ═══════════════════════════════════════════════════════════════════

def build_knowledge_graph(project_path: str,
                          analysis=None,
                          ast_results=None,
                          gitnexus_dir=None) -> CodeKnowledgeGraph:
    """构建并返回知识图谱实例"""
    kg = CodeKnowledgeGraph(project_path)
    kg.build(analysis=analysis, ast_results=ast_results, gitnexus_dir=gitnexus_dir)
    return kg


def load_knowledge_graph(project_path: str) -> Optional[CodeKnowledgeGraph]:
    """加载已有的知识图谱（不存在则返回 None）"""
    kg = CodeKnowledgeGraph(project_path)
    if kg.exists():
        return kg
    return None