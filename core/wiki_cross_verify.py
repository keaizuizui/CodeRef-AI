# -*- coding: utf-8 -*-
"""
WikiCrossVerify — 静态确证 ↔ 人话 wiki 的模块级交叉验证

背景：静态调用确证（符号级）对非技术人员不可读。非技术人员最终只能看人话版 wiki。
本模块把"静态确证"作为铁证，回贴到 wiki 的模块条目上，给每段人话描述打一颗
"确证徽章"，让非技术人员能分辨：
  - 这个模块真的在入口管线里被调用（有铁证背书）
  - 还是只是 LLM 在 wiki 里"吹"的（无铁证，需编程 AI 复核）

粒度桥接：
  wiki 模块 = 目录级（wiki_generator._discover_modules：core 目录算一个模块）
  图谱模块 = 文件级（code_knowledge_graph 的 mod:xxx 每个 py 一个）
  对齐键 = 图谱符号 file_path 的父目录名 == wiki 模块名

徽章判定（对每个 wiki 模块，统计其下符号在入口调用闭包内的覆盖度）：
  confirmed   全部符号在入口管线闭包内            → 绿 确证
  partial     部分在管线内（存在独立/未走主流程功能）  → 蓝 部分确证
  unverified  模块存在但符号不在管线内/纯配置常量    → 黄 存疑
  missing     图谱里根本找不到该模块             → 红 缺失

数据只来自知识图谱 CALLS 边，纯静态、确定性，不依赖 LLM。
"""

import os
import re
from typing import List, Optional, Tuple
from core.graph_closure import load_graph, file_base, downstream

from config.settings import WIKI_MERMAID_FALLBACK_MARK


# ═══════════════════════════════════════════════════════════════════
# 知识图谱定位
# ═══════════════════════════════════════════════════════════════════

def locate_kg_db(project_path: str) -> Optional[str]:
    """根据项目路径定位知识图谱 db（复用 code_knowledge_graph 的路径算法）。"""
    from core.code_knowledge_graph import CodeKnowledgeGraph
    db = CodeKnowledgeGraph(project_path).db_path
    return db if os.path.exists(db) else None


# ═══════════════════════════════════════════════════════════════════
# Mermaid 自愈（R9）
# ═══════════════════════════════════════════════════════════════════

# 括号配对检查用的闭合→开启映射
_MERMAID_BRACKET_PAIRS = {')': '(', ']': '[', '}': '{'}


def _strip_mermaid_fence(code: str, errors: list) -> str:
    """检查并剥离 mermaid fence：未闭合时记 MERMAID_FENCE_UNCLOSED。

    裸代码（无 fence）原样返回。
    """
    if not code.startswith("```"):
        return code
    if not code.endswith("```"):
        errors.append({"code": "MERMAID_FENCE_UNCLOSED",
                       "message": "Mermaid fence 未闭合（缺少结尾 ```）"})
    lines = code.splitlines()
    if lines and lines[0].strip().startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip() == "```":
        lines = lines[:-1]
    return "\n".join(lines).strip()


def _check_mermaid_node_ids(code: str, errors: list) -> None:
    """节点 id 合法性：`id[...]` / `id(...)` / `id{...}` 的 id 必须为合法标识符。"""
    for line in code.splitlines():
        line = line.strip()
        # 先捕获原始 token（允许任意非分隔/非空白字符），再校验是否
        # 是合法 Mermaid id——避免用预过滤后的字符类做二次匹配（那样永远成立）。
        # 覆盖普通节点 "id[label]" 与 subgraph 头 "subgraph id[标题]" 两种形态。
        m = re.match(r'^(?:subgraph\s+)?((?:[^\s\[({"]+)|(?:<[^>]*>))\s*[\[({]', line)
        if m:
            nid = m.group(1)
            if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', nid):
                errors.append({"code": "MERMAID_NODE_ID_INVALID",
                               "message": f"非法节点 id: {nid}"})


def _check_mermaid_brackets(code: str, errors: list) -> None:
    """括号配对：`[](){}` 在字符串字面量外必须配对闭合。"""
    stack = []
    in_string = False
    escape = False
    for ch in code:
        if in_string:
            if escape:
                escape = False
            elif ch == '\\':
                escape = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch in '([{':
            stack.append(ch)
        elif ch in ')]}':
            if not stack or stack[-1] != _MERMAID_BRACKET_PAIRS[ch]:
                errors.append({"code": "MERMAID_BRACKET_MISMATCH",
                               "message": f"括号不配对: {ch}"})
                break
            stack.pop()
    if stack:
        errors.append({"code": "MERMAID_BRACKET_UNCLOSED",
                       "message": "存在未闭合括号"})


def verify_mermaid(mermaid_code: str) -> dict:
    """对 Mermaid 代码做基础校验。

    检查项：
    1. fence 完整性：以 ```mermaid 开头必须有闭合 ```；裸代码（无 fence）视为合法；
    2. 节点 id 合法性：节点定义 `id[...]` / `id(...)` / `id{...}` 的 id 必须为合法标识符；
    3. 括号配对：`[](){}` 在字符串字面量外必须配对闭合。

    Returns:
        {"ok": bool, "errors": [{"code", "message"}, ...], "fallback": bool}
    """
    if not mermaid_code or not mermaid_code.strip():
        return {"ok": False,
                "errors": [{"code": "MERMAID_EMPTY",
                            "message": "Mermaid 代码为空"}],
                "fallback": True}

    code = mermaid_code.strip()
    errors = []

    # 1. fence 完整性（检查 + 剥离，后续检查针对剥离后的代码）
    code = _strip_mermaid_fence(code, errors)
    # 2. 节点 id 合法性
    _check_mermaid_node_ids(code, errors)
    # 3. 括号配对
    _check_mermaid_brackets(code, errors)

    return {"ok": not errors, "errors": errors, "fallback": bool(errors)}


def fallback_mermaid(mermaid_code: str, reason: str) -> str:
    """把校验失败的 Mermaid 代码降级为 text fence 并附加降级标记。

    降级产物以 WIKI_MERMAID_FALLBACK_MARK 注释开头（供下一轮修复定位），
    原内容以 ```text 包裹（渲染为纯文本，不触发 Mermaid 解析报错）。
    """
    code = (mermaid_code or "").strip()
    # 若本身带 mermaid fence，剥离后以 text fence 重新包裹
    if code.startswith("```"):
        lines = code.splitlines()
        if lines and lines[0].strip().startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        code = "\n".join(lines).strip()
    return (f"{WIKI_MERMAID_FALLBACK_MARK}\n"
            f"```text\n{code}\n```\n"
            f"<!-- mermaid 降级原因: {reason} -->")


# ═══════════════════════════════════════════════════════════════════
# 交叉验证器
# ═══════════════════════════════════════════════════════════════════

class ModuleCrossVerify:
    """把入口管线闭包对齐到 wiki 目录级模块，输出模块级确证徽章。"""

    def __init__(self, db_path: str):
        self.nodes, self.adj = load_graph(db_path)

    # ─── 下游闭包 ───

    def _downstream(self, start_id: str, max_depth: int = 8):
        return downstream(self.adj, start_id, max_depth=max_depth)

    # ─── 目录 → 文件级符号 对齐 ───

    def _symbols_in_dir(self, dir_name: str) -> List[Tuple[str, str, int]]:
        """返回该目录下所有函数/方法符号 (id, 名称, 起始行)。"""
        out = []
        for nid, n in self.nodes.items():
            if n["type"] not in ("function", "method"):
                continue
            fp = (n.get("file_path") or "").replace("\\", "/")
            parent = os.path.basename(os.path.dirname(fp))
            if parent == dir_name:
                out.append((nid, n["name"], n.get("start_line", 0)))
        return out

    def _dir_in_graph(self, dir_name: str) -> bool:
        """目录是否在图谱中被收录（存在该目录下任何节点，含常量/模块/类）。"""
        for nid, n in self.nodes.items():
            fp = (n.get("file_path") or "").replace("\\", "/")
            if os.path.basename(os.path.dirname(fp)) == dir_name:
                return True
        return False

    # ─── 入口定位 ───

    def _find_entry(self, spec: str):
        spec = spec.strip()
        if spec in self.nodes:
            return spec
        if "." in spec:
            mod, name = spec.rsplit(".", 1)
            for nid, n in self.nodes.items():
                if n["name"] == name and (mod in (n.get("file_path") or "").lower()
                                          or mod in n["name"].lower()):
                    return nid
            return None
        key = spec.lower()
        for nid, n in self.nodes.items():
            if key in n["name"].lower():
                return nid
        return None

    # ─── 模块级交叉验证 ───

    def verify_modules(self, wiki_modules: List[str],
                       entry_spec: str, max_depth: int = 8,
                       dir_aliases: Optional[dict] = None) -> dict:
        """对一组 wiki 模块（目录名）做交叉验证。

        Args:
            wiki_modules: wiki 模块名列表（目录名，如 ['core','utils','config']）
            entry_spec: 入口，如 'pipeline_runner.audit' / 'class:pipeline_runner:Pipe'
            dir_aliases: 模块名→实际目录名的映射（如 {"root": "项目名"}），
                        用于处理 root 伪模块等显示名与目录名不一致的情况。
        """
        entry = self._find_entry(entry_spec)
        if not entry:
            return {"entry": {"spec": entry_spec, "found": False},
                    "modules": [], "ok": False}

        root_reach = self._downstream(entry, max_depth=max_depth)

        results = []
        for mod_name in wiki_modules:
            # root 伪模块的目录名是项目根目录名，而非 "root"
            dir_name = (dir_aliases or {}).get(mod_name, mod_name)
            syms = self._symbols_in_dir(dir_name)
            if not syms:
                # 目录无函数/方法符号：区分"图谱收录但纯配置/常量"与"真缺失"
                if self._dir_in_graph(dir_name):
                    results.append({
                        "module": mod_name, "status": "unverified",
                        "reason": "该目录在图谱中已收录，但主要是配置/常量/类，无可验证的函数调用",
                        "total": 0, "in_pipe": 0,
                        "confirmed": [], "outside": [], "sample": [],
                    })
                else:
                    results.append({
                        "module": mod_name, "status": "missing",
                        "reason": "图谱中找不到该目录下的任何符号",
                        "total": 0, "in_pipe": 0,
                        "confirmed": [], "outside": [], "sample": [],
                    })
                continue

            in_pipe = [(nid, name, line) for (nid, name, line) in syms
                       if nid in root_reach]
            if len(in_pipe) == len(syms):
                status = "confirmed"
            elif len(in_pipe) > 0:
                status = "partial"
            else:
                status = "unverified"
            results.append({
                "module": mod_name, "status": status,
                "reason": {
                    "confirmed": "该模块全部符号都在入口管线闭包内，功能确被调用",
                    "partial": "部分符号在入口管线内，其余独立/未走主流程",
                    "unverified": "该模块符号存在，但都不在入口管线——可能经动态调用，或确实未走主流程",
                    "missing": "图谱中找不到该目录下的函数/方法符号",
                }[status],
                "total": len(syms), "in_pipe": len(in_pipe),
                "confirmed": [f"{name}@{file_base(self.nodes[nid])}:{line}"
                              for nid, name, line in in_pipe[:6]],
                "outside": [f"{name}@{file_base(self.nodes[nid])}:{line}"
                            for nid, name, line in syms if nid not in root_reach][:4],
                "sample": [f"{name}@{file_base(self.nodes[nid])}:{line}"
                           for nid, name, line in in_pipe[:2]],
            })

        return {
            "entry": {"spec": entry_spec, "found": True,
                      "node": f"{self.nodes[entry]['name']} ({file_base(self.nodes[entry])}:{self.nodes[entry]['start_line']})"},
            "modules": results,
            "ok": True,
            "graph_stats": {"nodes": len(self.nodes),
                            "calls_edges": sum(len(v) for v in self.adj.values())},
        }

    # ─── Mermaid 增强（R9）───

    def _module_mermaid(self, dir_name: str, display_name: str) -> str:
        """生成单个目录模块的 Mermaid 图（节点=目录内符号，边=符号间 CALLS）。

        无符号 / 无调用边时返回空串（由调用方决定是否渲染）。
        """
        from core.diagram_generator import generate_mermaid
        syms = self._symbols_in_dir(dir_name)
        if not syms:
            return ""
        nodes = [{"name": name, "filePath": file_base(self.nodes[nid])}
                 for nid, name, _line in syms]
        sym_ids = {nid for nid, _name, _line in syms}
        edges = []
        for nid, _name, _line in syms:
            for t in self.adj.get(nid, []):
                if t in sym_ids and t != nid:
                    edges.append({"source": self.nodes[nid]["name"],
                                  "target": self.nodes[t]["name"],
                                  "relation_type": "calls"})
        return generate_mermaid(nodes, edges, entry_point="", title=display_name)

    def verify_modules_with_mermaid(self, wiki_modules: List[str],
                                    entry_spec: str, max_depth: int = 8,
                                    dir_aliases: Optional[dict] = None,
                                    with_mermaid: bool = False) -> dict:
        """在 verify_modules() 基础上，为每个模块额外生成 Mermaid 图并校验。

        with_mermaid=False 时行为与 verify_modules() 完全一致（默认关闭，
        避免为每个模块生成图拖慢主流程）。开启后，每个模块的 result 会附加
        mermaid 字段：
            {"code": str, "verify": {"ok": bool, "errors": [...], "fallback": bool}}

        图校验失败时可用 fallback_mermaid() 降级为 text fence。
        """
        result = self.verify_modules(wiki_modules, entry_spec,
                                     max_depth=max_depth, dir_aliases=dir_aliases)
        if not with_mermaid:
            return result
        for mod in result.get("modules", []):
            dir_name = (dir_aliases or {}).get(mod["module"], mod["module"])
            code = self._module_mermaid(dir_name, mod["module"])
            mod["mermaid"] = {"code": code, "verify": verify_mermaid(code)}
        return result


