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
from typing import List, Optional, Tuple
from core.graph_closure import load_graph, file_base, downstream


# ═══════════════════════════════════════════════════════════════════
# 知识图谱定位
# ═══════════════════════════════════════════════════════════════════

def locate_kg_db(project_path: str) -> Optional[str]:
    """根据项目路径定位知识图谱 db（复用 code_knowledge_graph 的路径算法）。"""
    from core.code_knowledge_graph import CodeKnowledgeGraph
    db = CodeKnowledgeGraph(project_path).db_path
    return db if os.path.exists(db) else None


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
                       entry_spec: str, max_depth: int = 8) -> dict:
        """对一组 wiki 模块（目录名）做交叉验证。

        Args:
            wiki_modules: wiki 模块名列表（目录名，如 ['core','utils','config']）
            entry_spec: 入口，如 'pipeline_runner.audit' / 'class:pipeline_runner:Pipe'
        """
        entry = self._find_entry(entry_spec)
        if not entry:
            return {"entry": {"spec": entry_spec, "found": False},
                    "modules": [], "ok": False}

        root_reach = self._downstream(entry, max_depth=max_depth)

        results = []
        for mod_name in wiki_modules:
            syms = self._symbols_in_dir(mod_name)
            if not syms:
                # 目录无函数/方法符号：区分"图谱收录但纯配置/常量"与"真缺失"
                if self._dir_in_graph(mod_name):
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


