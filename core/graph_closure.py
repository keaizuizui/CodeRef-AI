# -*- coding: utf-8 -*-
"""
graph_closure — 知识图谱读取 + 下游闭包遍历（共享辅助）

背景：flow_verify（步骤级流程验证）与 wiki_cross_verify（目录级交叉验证）
各自实现了 load_graph / file_base / 下游闭包 BFS，逻辑高度相似。本模块把
这一共享的"静态确证"底座抽到一起，避免图谱 schema 变更时两处分叉。

设计原则：
- 纯静态、确定性：只读知识图谱 CALLS 边，不依赖 LLM。
- 零内部依赖：只依赖标准库，可被任何 core 模块安全复用。
"""

import json
import os
import sqlite3
from collections import defaultdict
from typing import Dict, List, Set, Tuple


def load_graph(db_path: str) -> Tuple[Dict[str, dict], Dict[str, List[str]]]:
    """返回 (nodes, adj)，adj 仅含 CALLS 边（source -> [targets]）。"""
    if not os.path.isfile(db_path):
        raise FileNotFoundError(f"知识图谱数据库不存在: {db_path}")
    nodes: Dict[str, dict] = {}
    adj: Dict[str, List[str]] = defaultdict(list)
    con = sqlite3.connect(db_path)
    try:
        con.row_factory = sqlite3.Row
        for r in con.execute("SELECT id,type,name,file_path,start_line,props FROM nodes"):
            d = dict(r)
            try:
                d["props"] = json.loads(d["props"] or "{}")
            except Exception:
                d["props"] = {}
            nodes[r["id"]] = d
        for r in con.execute("SELECT source,target FROM edges WHERE type='CALLS'"):
            adj[r["source"]].append(r["target"])
    finally:
        con.close()
    return nodes, adj


def file_base(n: dict) -> str:
    return os.path.basename(n.get("file_path") or "") or ""


def downstream(adj: Dict[str, List[str]], start_id: str, max_depth: int = 8) -> Set[str]:
    """从 start_id 出发，沿 CALLS 边遍历下游闭包（BFS，有深度上限）。"""
    seen = {start_id}
    frontier = {start_id}
    for _ in range(max_depth):
        nxt = set()
        for nid in frontier:
            for t in adj.get(nid, []):
                if t not in seen:
                    seen.add(t)
                    nxt.add(t)
        frontier = nxt
        if not frontier:
            break
    return seen