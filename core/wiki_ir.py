# -*- coding: utf-8 -*-
"""
WikiIR — 架构事实中间表示（JSON-IR 分离，R4）

范式：LLM 先输出结构化架构事实 JSON → JSON Schema 校验 → 再渲染为
Mermaid / Markdown。借鉴 Archify 的"先事实后渲染"设计：
  - LLM 只负责产出可校验的结构化事实（nodes / edges / entry_points），
    不直接产出文档正文；
  - 校验通过后才进入渲染层（Mermaid 图 / Markdown 段落）；
  - LLM 不可用 / 校验失败时，可用 extract_ir_from_kg 从知识图谱
    确定性提取 IR 兜底，保证 wiki 架构图始终有据可依。

纯标准库实现，不依赖 LLM 模块（避免与并行修改的 llm_integration 冲突，
容错 JSON 解析在此独立实现，思路与 LLMIntegration._try_parse_json 同构）。
"""

import json
import os
import re
from typing import Any, Dict, List, Optional

from config.settings import WIKI_IR_SCHEMA_VERSION


# ═══════════════════════════════════════════════════════════════════
# IR 数据结构
# ═══════════════════════════════════════════════════════════════════
#
# WikiIR = {
#   "schema_version": 1,
#   "project_name": str,
#   "nodes": [{"id": str, "name": str, "type": str, "role": str,
#              "file_path": str, "trust_boundary": str}],
#   "edges": [{"source": str, "target": str, "relation": str}],
#   "entry_points": [str],
# }
#
# 约定：
#   - nodes[].id 全局唯一，是 edges / entry_points 引用的键
#   - edges[].source / target 必须指向已存在的节点 id
#   - entry_points 必须指向已存在的节点 id

IR_REQUIRED_FIELDS = ("schema_version", "project_name", "nodes", "edges", "entry_points")

# 稳定错误码（供上层按 code 定位，不依赖文案）
IR_MISSING_FIELD = "IR_MISSING_FIELD"          # 必需字段缺失
IR_SCHEMA_VERSION = "IR_SCHEMA_VERSION"        # schema_version 不匹配
IR_NODES_EMPTY = "IR_NODES_EMPTY"              # nodes 为空
IR_NODE_ID_MISSING = "IR_NODE_ID_MISSING"      # 节点缺 id
IR_NODE_ID_DUPLICATE = "IR_NODE_ID_DUPLICATE"  # 节点 id 重复
IR_NODE_FIELD_TYPE = "IR_NODE_FIELD_TYPE"      # 节点字段类型错误
IR_EDGE_DANGLING = "IR_EDGE_DANGLING"          # 边引用的节点不存在
IR_ENTRY_UNKNOWN = "IR_ENTRY_UNKNOWN"          # 入口点节点不存在


# ═══════════════════════════════════════════════════════════════════
# 容错 JSON 解析（独立实现，思路与 LLMIntegration._try_parse_json 同构）
# ═══════════════════════════════════════════════════════════════════

def _strip_code_block(text: str) -> str:
    """剥离 LLM 返回的 Markdown 代码块包裹（```json ... ``` / ``` ... ```）。

    未命中代码块时原样返回。
    """
    if not text or "```" not in text:
        return text
    m = re.search(r"```(?:json)?\s*\n", text, re.IGNORECASE)
    if not m:
        return text
    end = text.find("```", m.end())
    if end < 0:
        return text
    return text[m.end():end].strip()


def _extract_balanced_json_fragment(text: str, open_char: str = '{',
                                    close_char: str = '}') -> str:
    """从文本中提取括号平衡的 JSON 片段（正确处理字符串字面量与转义）。"""
    start = text.find(open_char)
    if start < 0:
        return ""
    depth = 0
    in_string = False
    escape = False
    for i in range(start, len(text)):
        ch = text[i]
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
        elif ch == open_char:
            depth += 1
        elif ch == close_char:
            depth -= 1
            if depth == 0:
                return text[start:i + 1]
    return ""


def _complete_bare_token(tok: str) -> str:
    """补全被截断的裸 token：tr/tru → true，fa/fal/fals → false，nu/nul → null。"""
    t = tok.strip()
    low = t.lower()
    for prefix, full in (("true", "true"), ("false", "false"), ("null", "null")):
        if prefix.startswith(low) and low:
            return full
    if re.fullmatch(r"-?\d*\.?\d*(?:[eE][+-]?\d*)?", t):
        return t
    return tok


def _repair_truncated_json(text: str) -> str:
    """尽力修复被截断的 JSON 文本（LLM 因 max_tokens 截断的常见残缺）。

    处理三类残缺：
    1. 字符串字面量被截断（如 `{"a": "unfin`，缺闭合引号）；
    2. 裸 token 被截断（如 `"verified": tr`，缺结尾）；
    3. 数组/对象括号未闭合（如 `[{"x":1`，缺 `}]`）。

    仅当能修复时返回修复后的文本，否则返回原文本（由调用方判定）。
    """
    if not text:
        return text
    # 定位第一个结构起点（{ 或 [），丢弃前缀杂文
    start = -1
    for i, ch in enumerate(text):
        if ch in "{[":
            start = i
            break
    if start < 0:
        return text
    frag = text[start:]
    out: List[str] = []
    stack: List[str] = []
    in_string = False
    escape = False
    token: List[str] = []   # value 位置的裸 token 缓冲（补全前不写入 out）

    def flush_token() -> None:
        if token:
            out.append(_complete_bare_token("".join(token)))
            token.clear()

    i = 0
    n = len(frag)
    while i < n:
        ch = frag[i]
        if in_string:
            out.append(ch)
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            i += 1
            continue
        if ch == '"':
            flush_token()
            in_string = True
            out.append(ch)
            i += 1
            continue
        if ch in "{[":
            flush_token()
            stack.append("}" if ch == "{" else "]")
            out.append(ch)
            i += 1
            continue
        if ch in "}]":
            flush_token()
            if stack:
                stack.pop()
            out.append(ch)
            i += 1
            continue
        if ch in ",:":
            flush_token()
            out.append(ch)
            i += 1
            continue
        if ch.isspace():
            flush_token()
            out.append(ch)
            i += 1
            continue
        token.append(ch)
        i += 1
    # 扫描结束：补全残余
    if in_string:
        out.append('"')          # 补闭合引号
    else:
        flush_token()
    while stack:                 # 补未闭合括号
        out.append(stack.pop())
    return "".join(out)


def parse_llm_json(text: str) -> Optional[dict]:
    """容错解析 LLM 输出的 JSON 对象（IR 期望顶层为 dict）。

    依次尝试：
    1. 整体解析（先剥离 ```json 代码块包裹）；
    2. 截取花括号/方括号平衡的合法片段后再解析；
    3. 修复截断值（补全字符串引号、裸 token、未闭合括号）后解析。

    解析失败或顶层不是 dict 时返回 None。
    """
    if not text:
        return None
    stripped = _strip_code_block(text)
    if stripped != text:
        text = stripped
    # 1. 整体解析
    try:
        data = json.loads(text)
        return data if isinstance(data, dict) else None
    except (json.JSONDecodeError, ValueError):
        pass
    # 2. 截取平衡片段。以更靠前的结构分隔符作为优先顶层类型。
    i_open = text.find('[')
    i_brace = text.find('{')
    if i_open < 0:
        i_open = len(text) + 1
    if i_brace < 0:
        i_brace = len(text) + 1
    order = ('[', '{') if i_open < i_brace else ('{', '[')
    for open_char, close_char in ((c, ']' if c == '[' else '}') for c in order):
        fragment = _extract_balanced_json_fragment(text, open_char, close_char)
        if fragment:
            try:
                data = json.loads(fragment)
                if isinstance(data, dict):
                    return data
            except (json.JSONDecodeError, ValueError):
                continue
    # 3. 修复截断值后重试
    repaired = _repair_truncated_json(text)
    if repaired and repaired != text:
        try:
            data = json.loads(repaired)
            if isinstance(data, dict):
                return data
        except (json.JSONDecodeError, ValueError):
            pass
    return None


# ═══════════════════════════════════════════════════════════════════
# IR 校验
# ═══════════════════════════════════════════════════════════════════

def validate_ir(ir: dict) -> dict:
    """校验 IR 是否符合 schema，返回 {"ok", "errors", "warnings"}。

    errors 每项为 {"code": str, "field": str, "message": str}，
    code 为稳定错误码（见模块顶部 IR_* 常量），供上层程序化定位。
    """
    errors: List[dict] = []
    if not isinstance(ir, dict):
        return {"ok": False,
                "errors": [{"code": IR_MISSING_FIELD, "field": "$root",
                            "message": "IR 顶层必须是 JSON 对象"}],
                "warnings": []}

    # 1. 必需字段
    for f in IR_REQUIRED_FIELDS:
        if f not in ir:
            errors.append({"code": IR_MISSING_FIELD, "field": f,
                           "message": f"缺少必需字段: {f}"})

    # 2. schema_version
    if ir.get("schema_version") != WIKI_IR_SCHEMA_VERSION:
        errors.append({"code": IR_SCHEMA_VERSION, "field": "schema_version",
                       "message": f"schema_version 应为 {WIKI_IR_SCHEMA_VERSION}，"
                                  f"实际为 {ir.get('schema_version')!r}"})

    nodes = ir.get("nodes")
    if not isinstance(nodes, list):
        errors.append({"code": IR_NODE_FIELD_TYPE, "field": "nodes",
                       "message": "nodes 必须是数组"})
        nodes = []
    if not nodes:
        errors.append({"code": IR_NODES_EMPTY, "field": "nodes",
                       "message": "nodes 不能为空（至少需要一个节点）"})

    # 3. 节点 id 唯一性 + 字段类型
    seen_ids: Dict[str, int] = {}
    for idx, node in enumerate(nodes):
        if not isinstance(node, dict):
            errors.append({"code": IR_NODE_FIELD_TYPE, "field": f"nodes[{idx}]",
                           "message": f"nodes[{idx}] 必须是对象"})
            continue
        nid = node.get("id")
        if not isinstance(nid, str) or not nid:
            errors.append({"code": IR_NODE_ID_MISSING, "field": f"nodes[{idx}].id",
                           "message": f"nodes[{idx}] 缺少非空 id"})
            continue
        if nid in seen_ids:
            errors.append({"code": IR_NODE_ID_DUPLICATE, "field": f"nodes[{idx}].id",
                           "message": f"节点 id 重复: {nid}（首次出现在 nodes[{seen_ids[nid]}]）"})
        else:
            seen_ids[nid] = idx
        for f in ("name", "type", "role", "file_path", "trust_boundary"):
            v = node.get(f)
            if v is not None and not isinstance(v, str):
                errors.append({"code": IR_NODE_FIELD_TYPE,
                               "field": f"nodes[{idx}].{f}",
                               "message": f"nodes[{idx}].{f} 应为字符串"})

    # 4. 边引用完整性
    edges = ir.get("edges")
    if not isinstance(edges, list):
        errors.append({"code": IR_NODE_FIELD_TYPE, "field": "edges",
                       "message": "edges 必须是数组"})
        edges = []
    for idx, edge in enumerate(edges):
        if not isinstance(edge, dict):
            errors.append({"code": IR_NODE_FIELD_TYPE, "field": f"edges[{idx}]",
                           "message": f"edges[{idx}] 必须是对象"})
            continue
        src = edge.get("source")
        tgt = edge.get("target")
        for ref, label in ((src, "source"), (tgt, "target")):
            if ref not in seen_ids:
                errors.append({"code": IR_EDGE_DANGLING,
                               "field": f"edges[{idx}].{label}",
                               "message": f"edges[{idx}].{label} 引用了不存在的节点: {ref!r}"})

    # 5. 入口点引用完整性
    entry_points = ir.get("entry_points")
    if not isinstance(entry_points, list):
        errors.append({"code": IR_NODE_FIELD_TYPE, "field": "entry_points",
                       "message": "entry_points 必须是数组"})
        entry_points = []
    for ep in entry_points:
        if ep not in seen_ids:
            errors.append({"code": IR_ENTRY_UNKNOWN, "field": "entry_points",
                           "message": f"入口点引用了不存在的节点: {ep!r}"})

    return {"ok": not errors, "errors": errors, "warnings": []}


# ═══════════════════════════════════════════════════════════════════
# IR → 渲染
# ═══════════════════════════════════════════════════════════════════

def ir_to_mermaid(ir: dict) -> str:
    """把合法 IR 转成 Mermaid flowchart（复用 diagram_generator.generate_mermaid）。

    IR 边用节点 id 引用，generate_mermaid 用节点 name 生成 ID，
    因此先把 id 映射回 name 再交给渲染层。
    """
    from core.diagram_generator import generate_mermaid
    nodes = ir.get("nodes") or []
    id_to_name = {n.get("id"): n.get("name", "") for n in nodes}
    dg_nodes = [{"name": n.get("name", ""),
                 "filePath": n.get("file_path", "")} for n in nodes]
    dg_edges = []
    for e in ir.get("edges") or []:
        src = id_to_name.get(e.get("source"), e.get("source", ""))
        tgt = id_to_name.get(e.get("target"), e.get("target", ""))
        dg_edges.append({"source": src, "target": tgt,
                         "relation_type": e.get("relation", "calls")})
    entry = ""
    if ir.get("entry_points"):
        entry = id_to_name.get(ir["entry_points"][0], ir["entry_points"][0])
    title = ir.get("project_name") or "Architecture Overview"
    return generate_mermaid(dg_nodes, dg_edges, entry_point=entry, title=title)


def ir_to_markdown(ir: dict) -> str:
    """把 IR 渲染成结构化 Markdown 段落（节点表 + 边表 + 入口点），供 wiki 嵌入。"""
    lines = []
    lines.append("## 架构事实")
    lines.append("")
    lines.append(f"- 项目：**{ir.get('project_name', '')}**")
    lines.append(f"- IR schema 版本：v{ir.get('schema_version', '?')}")
    lines.append("")

    nodes = ir.get("nodes") or []
    lines.append("### 节点")
    lines.append("| ID | 名称 | 类型 | 角色 | 文件路径 | 信任边界 |")
    lines.append("|----|------|------|------|----------|----------|")
    for n in nodes:
        lines.append(f"| `{n.get('id', '')}` | {n.get('name', '')} | "
                     f"{n.get('type', '')} | {n.get('role', '')} | "
                     f"`{n.get('file_path', '')}` | {n.get('trust_boundary', '')} |")
    lines.append("")

    edges = ir.get("edges") or []
    lines.append("### 依赖关系")
    lines.append("| 源 | 目标 | 关系 |")
    lines.append("|----|------|------|")
    for e in edges:
        lines.append(f"| `{e.get('source', '')}` | `{e.get('target', '')}` | "
                     f"{e.get('relation', 'calls')} |")
    lines.append("")

    entry_points = ir.get("entry_points") or []
    lines.append("### 入口点")
    for ep in entry_points:
        lines.append(f"- `{ep}`")
    lines.append("")
    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════════
# 确定性 IR 兜底：从知识图谱提取
# ═══════════════════════════════════════════════════════════════════

def _infer_role(file_path: str) -> str:
    """按路径关键词启发式推断节点角色（与 diagram_generator 分层思路一致）。"""
    fp = (file_path or "").lower().replace("\\", "/")
    for kw, role in (("main", "entry"), ("app", "entry"), ("run", "entry"),
                     ("controller", "controller"), ("api", "api"),
                     ("service", "service"), ("core", "core"),
                     ("model", "model"), ("data", "data"),
                     ("repository", "repository"), ("dao", "dao"),
                     ("util", "util"), ("common", "shared"),
                     ("config", "config"), ("settings", "config")):
        if kw in fp:
            return role
    return "component"


def _infer_trust_boundary(file_path: str) -> str:
    """按路径关键词推断信任边界：配置/凭据相关归为 config，其余 internal。"""
    fp = (file_path or "").lower().replace("\\", "/")
    if any(kw in fp for kw in ("config", "settings", "secret", "key",
                               "credential", "auth", "token")):
        return "config"
    return "internal"


def _module_of(node: Optional[dict]) -> Optional[str]:
    """从图谱节点推断其所属模块 id（mod:<文件名>，与图谱模块节点命名一致）。"""
    if not node:
        return None
    fp = (node.get("file_path") or "").replace("\\", "/")
    base = os.path.splitext(os.path.basename(fp))[0]
    if not base:
        return None
    return f"mod:{base}"


def extract_ir_from_kg(project_path: str,
                       entry_points: Optional[List[str]] = None) -> Optional[dict]:
    """从知识图谱确定性提取 IR（LLM 不可用时的静态兜底）。

    节点 = 图谱中的模块节点（type='module'），
    边   = 函数级 CALLS 边聚合到模块级（跨模块调用才保留），
    入口 = 可配置；未提供时自动探测 main/app/run 模块。

    知识图谱缺失 / 无模块节点时返回 None（优雅降级，不抛异常）。
    """
    try:
        from core.wiki_cross_verify import locate_kg_db
        from core.graph_closure import load_graph
        db = locate_kg_db(project_path)
        if not db:
            return None
        nodes, adj = load_graph(db)
    except Exception:
        return None

    # 模块节点 → IR 节点
    mod_nodes = {}
    for nid, n in nodes.items():
        if n.get("type") != "module":
            continue
        fp = n.get("file_path") or ""
        mod_nodes[nid] = {
            "id": nid,
            "name": n.get("name", nid),
            "type": "module",
            "role": _infer_role(fp),
            "file_path": fp,
            "trust_boundary": _infer_trust_boundary(fp),
        }
    if not mod_nodes:
        return None

    # 函数级 CALLS 边 → 模块级边（去重）
    edge_set = set()
    for src, targets in adj.items():
        src_mod = _module_of(nodes.get(src))
        if src_mod not in mod_nodes:
            continue
        for tgt in targets:
            tgt_mod = _module_of(nodes.get(tgt))
            if tgt_mod in mod_nodes and tgt_mod != src_mod:
                edge_set.add((src_mod, tgt_mod))
    ir_edges = [{"source": s, "target": t, "relation": "calls"}
                for s, t in sorted(edge_set)]

    # 入口点
    if entry_points is None:
        entry_points = []
        for nid in mod_nodes:
            name = nid.lower()
            if any(kw in name for kw in ("main", "app", "run")):
                entry_points.append(nid)
        entry_points = entry_points[:1]
    else:
        entry_points = [ep for ep in entry_points if ep in mod_nodes]

    return {
        "schema_version": WIKI_IR_SCHEMA_VERSION,
        "project_name": os.path.basename(project_path.rstrip(os.sep)),
        "nodes": list(mod_nodes.values()),
        "edges": ir_edges,
        "entry_points": entry_points,
    }
