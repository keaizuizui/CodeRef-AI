# -*- coding: utf-8 -*-
"""
Wiki Compare —— 架构快照比对（R8，借鉴 Archify 的 Before/Delta/After 理念）

在 wiki 生成前后各保存一次架构快照（.arch-snapshot.json），通过
compare_snapshots 精确给出五类变更收据：

  - added     新增节点/边
  - removed   删除节点/边
  - changed   属性变化（如 role / relation 改变）
  - moved     位置/归属变化（file_path 改变）
  - rerouted  边关系重定向（端点变化但关系类型不变）

键约定：
  - 节点以 id 为键（缺失时回退 name）
  - 边以 (source, target, relation) 为键

本模块是 viewer-only：只做变更报告，不做风险/影响推断（不声称"爆炸半径"）。
纯标准库实现，不依赖第三方包。
"""

import os
import json
import tempfile
from datetime import datetime
from typing import Dict, List, Any, Optional

from config import settings


# ═══════════════════════════════════════════════════════════════════
# 快照读写
# ═══════════════════════════════════════════════════════════════════

def snapshot_path(project_path: str) -> str:
    """返回项目快照文件的完整路径（<project_path>/.arch-snapshot.json）。"""
    return os.path.join(project_path, settings.WIKI_SNAPSHOT_FILE)


def load_snapshot(project_path: str) -> Optional[dict]:
    """读取项目架构快照。

    快照文件不存在 / 解析失败时返回 None（优雅降级，不抛异常）。
    """
    path = snapshot_path(project_path)
    try:
        if not os.path.exists(path):
            return None
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else None
    except (OSError, ValueError):
        return None


def _write_atomic(path: str, data: str) -> bool:
    """原子写（独占临时文件 + os.replace），避免写一半损坏或并发写串扰。

    参考项目 operation_memory._write_atomic 的做法：用 tempfile.mkstemp
    在目标文件同目录创建独占临时文件，写成功并 fsync 后再原子替换目标文件；
    所有失败路径都清理临时文件，避免堆积 .tmp。
    """
    dir_name = os.path.dirname(path) or "."
    try:
        fd, tmp = tempfile.mkstemp(
            dir=dir_name,
            prefix="." + os.path.basename(path) + ".",
            suffix=".tmp",
        )
    except OSError:
        return False
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
        return True
    except (OSError, UnicodeError):
        return False
    finally:
        try:
            os.remove(tmp)
        except OSError:
            # 临时文件清理尽力而为
            pass


def save_snapshot(project_path: str, snapshot: dict) -> bool:
    """原子写快照到 <project_path>/.arch-snapshot.json。

    目录不存在 / 快照非法 / 写入失败返回 False（优雅降级，不抛异常）。
    """
    if not isinstance(snapshot, dict) or not snapshot:
        return False
    if not os.path.isdir(project_path):
        return False
    try:
        data = json.dumps(snapshot, ensure_ascii=False, indent=2)
    except (TypeError, ValueError):
        return False
    return _write_atomic(snapshot_path(project_path), data)


def build_snapshot(nodes: List[dict], edges: List[dict],
                   entry_points: List[str]) -> dict:
    """构造架构快照结构。

    Args:
        nodes: 节点列表（约定含 id / file_path / role 等字段）
        edges: 边列表（约定含 source / target / relation 字段）
        entry_points: 入口点标识列表

    Returns:
        {"captured_at": ISO时间, "nodes": [...], "edges": [...], "entry_points": [...]}
    """
    return {
        "captured_at": datetime.now().isoformat(timespec="seconds"),
        "nodes": list(nodes or []),
        "edges": list(edges or []),
        "entry_points": list(entry_points or []),
    }


# ═══════════════════════════════════════════════════════════════════
# 快照比对
# ═══════════════════════════════════════════════════════════════════

def _node_id(node: dict, idx: int = 0) -> str:
    """节点唯一键：优先 id，其次 name，最后序号兜底。"""
    if isinstance(node, dict):
        for key in ("id", "name"):
            val = node.get(key)
            if isinstance(val, str) and val:
                return val
    return f"node_{idx}"


def _edge_relation(edge: dict) -> str:
    """边关系类型：优先 relation 字段，其次 type（兼容图谱 CALLS/IMPORTS 等）。"""
    if isinstance(edge, dict):
        val = edge.get("relation")
        if isinstance(val, str) and val:
            return val
        val = edge.get("type")
        if isinstance(val, str) and val:
            return val
    return ""


def _edge_key(edge: dict) -> tuple:
    """边唯一键：(source, target, relation)。"""
    if isinstance(edge, dict):
        return (str(edge.get("source", "")),
                str(edge.get("target", "")),
                _edge_relation(edge))
    return ("", "", "")


def _node_attrs(node: dict) -> dict:
    """节点"属性签名"：除 id / file_path 外的字段（用于 changed 判定）。"""
    if not isinstance(node, dict):
        return {}
    return {k: v for k, v in node.items() if k not in ("id", "file_path")}


def _edge_attrs(edge: dict) -> dict:
    """边"属性签名"：除 source / target / relation / type 外的字段（用于 changed 判定）。"""
    if not isinstance(edge, dict):
        return {}
    return {k: v for k, v in edge.items()
            if k not in ("source", "target", "relation", "type")}


def _changed_fields(before: dict, after: dict) -> List[str]:
    """返回前后属性签名中发生变化的字段名（排序去重）。"""
    return sorted(k for k in set(before) | set(after)
                  if before.get(k) != after.get(k))


def compare_snapshots(before: dict, after: dict) -> dict:
    """比对前后快照，给出五类变更收据。

    Args:
        before: 变更前快照（build_snapshot 的产物，或 load_snapshot 的读取结果）
        after:  变更后快照

    Returns:
        {
          "added": [...],      # 新增节点/边
          "removed": [...],    # 删除节点/边
          "changed": [...],    # 属性变化（role/relation 等）
          "moved": [...],      # 位置/归属变化（file_path 改变，仅节点）
          "rerouted": [...],   # 边关系重定向
          "stats": {"nodes_before": n, "nodes_after": n,
                    "edges_before": n, "edges_after": n},
        }

    viewer-only：只做变更报告，不做风险/影响推断。
    """
    before = before or {}
    after = after or {}
    b_nodes = before.get("nodes") or []
    a_nodes = after.get("nodes") or []
    b_edges = before.get("edges") or []
    a_edges = after.get("edges") or []

    # ── 节点索引与三类节点变更 ──
    b_node_map = {_node_id(n, i): n for i, n in enumerate(b_nodes)}
    a_node_map = {_node_id(n, i): n for i, n in enumerate(a_nodes)}

    added_nodes = [{"kind": "node", "id": nid, "data": anode}
                   for nid, anode in a_node_map.items() if nid not in b_node_map]
    removed_nodes = [{"kind": "node", "id": nid, "data": bnode}
                     for nid, bnode in b_node_map.items() if nid not in a_node_map]

    moved_nodes = []
    changed_nodes = []
    for nid in b_node_map.keys() & a_node_map.keys():
        bnode, anode = b_node_map[nid], a_node_map[nid]
        bfp = str(bnode.get("file_path", "")) if isinstance(bnode, dict) else ""
        afp = str(anode.get("file_path", "")) if isinstance(anode, dict) else ""
        name = anode.get("name", nid) if isinstance(anode, dict) else nid
        if bfp != afp:
            moved_nodes.append({
                "kind": "node", "id": nid, "name": name,
                "before_file": bfp, "after_file": afp,
            })
        b_attrs, a_attrs = _node_attrs(bnode), _node_attrs(anode)
        if b_attrs != a_attrs:
            changed_nodes.append({
                "kind": "node", "id": nid, "name": name,
                "before": b_attrs, "after": a_attrs,
                "changed_fields": _changed_fields(b_attrs, a_attrs),
            })

    # ── 边索引与新增/删除 ──
    b_edge_map = {_edge_key(e): e for e in b_edges}
    a_edge_map = {_edge_key(e): e for e in a_edges}

    added_edges = [{"kind": "edge", "key": list(k), "data": e}
                   for k, e in a_edge_map.items() if k not in b_edge_map]
    removed_edges = [{"kind": "edge", "key": list(k), "data": e}
                     for k, e in b_edge_map.items() if k not in a_edge_map]

    # ── rerouted：从 removed/added 中提取"端点变化但关系类型不变"的配对 ──
    # 旧边 (A→B, r) 变为 (A→C, r) 或 (C→B, r)：共享一个端点且关系类型不变，
    # 视为"重定向"而非简单的删除+新增，单独收据更贴合 code review 场景。
    rerouted = []
    used_removed: set = set()
    used_added: set = set()
    for ri, re_item in enumerate(removed_edges):
        re_key = tuple(re_item["key"])
        re_rel = re_key[2]
        for ai, ae_item in enumerate(added_edges):
            if ai in used_added:
                continue
            ae_key = tuple(ae_item["key"])
            if ae_key[2] == re_rel and (ae_key[0] == re_key[0] or ae_key[1] == re_key[1]):
                rerouted.append({
                    "kind": "edge",
                    "relation": re_rel,
                    "from": re_item["data"],
                    "to": ae_item["data"],
                })
                used_removed.add(ri)
                used_added.add(ai)
                break
    removed_edges = [it for i, it in enumerate(removed_edges) if i not in used_removed]
    added_edges = [it for i, it in enumerate(added_edges) if i not in used_added]

    # ── changed 边：键相同但属性不同 ──
    changed_edges = []
    for k in b_edge_map.keys() & a_edge_map.keys():
        be, ae = b_edge_map[k], a_edge_map[k]
        b_attrs, a_attrs = _edge_attrs(be), _edge_attrs(ae)
        if b_attrs != a_attrs:
            changed_edges.append({
                "kind": "edge",
                "source": k[0], "target": k[1], "relation": k[2],
                "before": b_attrs, "after": a_attrs,
                "changed_fields": _changed_fields(b_attrs, a_attrs),
            })

    return {
        "added": added_nodes + added_edges,
        "removed": removed_nodes + removed_edges,
        "changed": changed_nodes + changed_edges,
        "moved": moved_nodes,
        "rerouted": rerouted,
        "stats": {
            "nodes_before": len(b_node_map),
            "nodes_after": len(a_node_map),
            "edges_before": len(b_edge_map),
            "edges_after": len(a_edge_map),
        },
    }


# ═══════════════════════════════════════════════════════════════════
# 变更收据渲染
# ═══════════════════════════════════════════════════════════════════

def _compact(obj: Any, limit: int = 120) -> str:
    """把属性值压缩为单行字符串（超长截断），用于表格单元格。"""
    try:
        s = json.dumps(obj, ensure_ascii=False)
    except (TypeError, ValueError):
        s = str(obj)
    return s if len(s) <= limit else s[:limit] + "…"


def _edge_label(edge: dict) -> str:
    """边的可读标签：source → target (relation)。"""
    if not isinstance(edge, dict):
        return ""
    return (f"{edge.get('source', '')} → {edge.get('target', '')}"
            f" ({_edge_relation(edge)})")


def _render_added_removed(items: List[dict]) -> str:
    """渲染新增/删除明细表。"""
    if not items:
        return "_（无）_"
    rows = ["| 类型 | 标识 | 详情 |", "| --- | --- | --- |"]
    for it in items:
        if it["kind"] == "node":
            node = it["data"]
            name = node.get("name", "") if isinstance(node, dict) else ""
            fp = node.get("file_path", "") if isinstance(node, dict) else ""
            rows.append(f"| 节点 | `{it['id']}` | {name} · {fp} |")
        else:
            rows.append(f"| 边 | `{_edge_label(it['data'])}` | {_compact(it['data'])} |")
    return "\n".join(rows)


def _render_changed(items: List[dict]) -> str:
    """渲染属性变化明细表。"""
    if not items:
        return "_（无）_"
    rows = ["| 类型 | 标识 | 变化字段 | 变化前 | 变化后 |", "| --- | --- | --- | --- | --- |"]
    for it in items:
        if it["kind"] == "node":
            ident = f"`{it['id']}`"
        else:
            ident = f"`{it['source']} → {it['target']} ({it['relation']})`"
        rows.append(f"| {'节点' if it['kind'] == 'node' else '边'} | {ident} | "
                    f"{', '.join(it['changed_fields'])} | "
                    f"{_compact(it['before'])} | {_compact(it['after'])} |")
    return "\n".join(rows)


def _render_moved(items: List[dict]) -> str:
    """渲染位置/归属变化明细表。"""
    if not items:
        return "_（无）_"
    rows = ["| 标识 | 名称 | 原文件 | 新文件 |", "| --- | --- | --- | --- |"]
    for it in items:
        rows.append(f"| `{it['id']}` | {it['name']} | {it['before_file']} | {it['after_file']} |")
    return "\n".join(rows)


def _render_rerouted(items: List[dict]) -> str:
    """渲染边重定向明细表。"""
    if not items:
        return "_（无）_"
    rows = ["| 关系 | 原边 | 新边 |", "| --- | --- | --- |"]
    for it in items:
        rows.append(f"| {it['relation']} | `{_edge_label(it['from'])}` | `{_edge_label(it['to'])}` |")
    return "\n".join(rows)


def compare_to_markdown(before: dict, after: dict) -> str:
    """把 diff 收据渲染成 Markdown 报告（变更摘要 + 五类变更明细表）。

    供 code review 场景使用：变更前后快照各一份，输出人类可读的变更报告。
    """
    diff = compare_snapshots(before, after)
    stats = diff["stats"]
    total_changes = (len(diff["added"]) + len(diff["removed"])
                     + len(diff["changed"]) + len(diff["moved"])
                     + len(diff["rerouted"]))

    lines = [
        "# 架构快照变更报告",
        "",
        "> 本报告由 CodeRef Wiki Compare 生成，仅展示变更事实，不做风险/影响推断。",
        "",
        "## 变更摘要",
        "",
        "| 指标 | 变更前 | 变更后 |",
        "| --- | --- | --- |",
        f"| 节点数 | {stats['nodes_before']} | {stats['nodes_after']} |",
        f"| 边数 | {stats['edges_before']} | {stats['edges_after']} |",
        "",
        f"- 新增：{len(diff['added'])} 项",
        f"- 删除：{len(diff['removed'])} 项",
        f"- 属性变化：{len(diff['changed'])} 项",
        f"- 位置/归属变化：{len(diff['moved'])} 项",
        f"- 边重定向：{len(diff['rerouted'])} 项",
        "",
    ]

    if total_changes == 0:
        lines.append("**未检测到架构变更。**")
        return "\n".join(lines)

    lines.append("## 新增（added）")
    lines.append("")
    lines.append(_render_added_removed(diff["added"]))
    lines.append("")
    lines.append("## 删除（removed）")
    lines.append("")
    lines.append(_render_added_removed(diff["removed"]))
    lines.append("")
    lines.append("## 属性变化（changed）")
    lines.append("")
    lines.append(_render_changed(diff["changed"]))
    lines.append("")
    lines.append("## 位置/归属变化（moved）")
    lines.append("")
    lines.append(_render_moved(diff["moved"]))
    lines.append("")
    lines.append("## 边重定向（rerouted）")
    lines.append("")
    lines.append(_render_rerouted(diff["rerouted"]))
    return "\n".join(lines)


def compare_to_json(before: dict, after: dict) -> dict:
    """返回机器可读的变更收据（compare_snapshots 结果 + 生成时间）。"""
    diff = compare_snapshots(before, after)
    diff["generated_at"] = datetime.now().isoformat(timespec="seconds")
    return diff
