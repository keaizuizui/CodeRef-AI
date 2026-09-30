# -*- coding: utf-8 -*-
"""
arch_framework — 业务架构树自动归纳（v5.16 画布业务树数据源）。

把"冰山之下"（知识图谱 + target_arch + 目录结构）翻译成决策者能看的
业务层级大白话骨架：入口层 → 主功能层 → 子功能层 → 步骤（叶子）。

确定性优先（P0 骨架）：目录结构 + 模块命名语义给出树形骨架与关联模块；
LLM 辅助大白话（P1）：对节点生成业务语义标题/描述/步骤，缺 key 静态降级
（用模块名可读化直出，不编造）。人工在 skill 确认门处确认/修正框架。
"""

import json
import os
from typing import Dict, List, Optional

from core.arch_audit import locate_kg_db, module_of
from core.arch_gap_analyzer import _is_test_module
from core.graph_closure import load_graph

# 目录名 → 业务语义（确定性骨架标题用；LLM 辅助时会覆盖为更业务的表述）
_DIR_CN = {
    "pipelines": "流水线", "pipeline": "流水线", "agents": "智能体", "agent": "智能体",
    "tools": "工具", "tool": "工具", "interfaces": "接口", "interface": "接口",
    "ui": "界面", "web": "前端", "frontend": "前端", "backend": "后端",
    "utils": "工具库", "util": "工具库", "configs": "配置", "config": "配置",
    "prompts": "提示词", "prompt": "提示词", "models": "模型", "model": "模型",
    "runtime": "运行时", "agent_runtime": "运行时", "services": "服务",
    "service": "服务", "controllers": "控制器", "controller": "控制器",
    "handlers": "处理器", "main": "入口", "src": "源码", "core": "核心",
    "common": "公共", "shared": "共享", "domain": "领域",
    "application": "应用", "adapters": "适配器", "tests": "测试", "test": "测试",
}


def _readable(name: str) -> str:
    """模块名可读化：basename 去下划线转空格首字母大写。"""
    short = name.split("/")[-1]
    text = short.replace("_", " ").replace("-", " ").strip()
    return text[:1].upper() + text[1:] if text else short


def _is_entry_module(m: str) -> bool:
    """入口模块启发：basename 以 main/__main__/cli/server/app 开头。"""
    last = m.split("/")[-1].lower()
    return (last.startswith("main") or last == "__main__"
            or last.startswith("cli") or last.startswith("server")
            or last == "app")


def _domain_of(m: str) -> str:
    """模块 → 业务域：路径第二段（vimax/<域>/...）。"""
    parts = m.split("/")
    return parts[1] if len(parts) > 1 else parts[0]


def _load_target_arch(project_path: str) -> dict:
    path = os.path.join(project_path, ".coderef", "target_arch.json")
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"version": "5.0", "project": os.path.basename(project_path),
            "tech_roles": [], "business_flows": []}


def _module_names(project_path: str, nodes: dict) -> List[str]:
    """非测试模块全路径列表。"""
    names = []
    for nid, n in nodes.items():
        if n.get("type") != "module":
            continue
        m = module_of(n, project_path) or n.get("name", "?")
        if _is_test_module(m) or m.split("/")[-1] == "__init__":
            continue
        names.append(m)
    return names


def build_framework(project_path: str,
                    target_arch: Optional[dict] = None) -> dict:
    """归纳业务架构树（确定性骨架）。

    Returns:
        {project, framework: {name, desc, modules, children: [...]}}
        主功能节点 = {id, title, desc, modules, children: [子功能节点]}
        子功能节点 = {id, title, desc, modules, steps: [...]}
    """
    ta = target_arch or _load_target_arch(project_path)

    db = locate_kg_db(project_path)
    nodes, adj = (load_graph(db) if db and os.path.exists(db) else ({}, {}))
    modules = _module_names(project_path, nodes)

    # ── 入口层 ──
    entry_mods = [m for m in modules if _is_entry_module(m)]
    biz_mods = [m for m in modules if not _is_entry_module(m)]

    # ── 主功能层：按业务域分组 ──
    domains: Dict[str, List[str]] = {}
    for m in biz_mods:
        d = _domain_of(m)
        if d in ("run_cycle",):
            continue  # 工具自身产物目录，不是业务域
        domains.setdefault(d, []).append(m)

    children: List[dict] = []
    for d in sorted(domains):
        mods = sorted(domains[d])
        title = _DIR_CN.get(d.lower(), _readable(d))
        # ── 子功能层：域内下一级分组 ──
        sub: Dict[str, List[str]] = {}
        for m in mods:
            parts = m.split("/")
            key = parts[2] if len(parts) > 2 else parts[-1]
            sub.setdefault(key, []).append(m)
        subchildren = []
        for k in sorted(sub):
            sm = sorted(sub[k])
            subchildren.append({
                "id": f"{d}:{k}",
                "title": _readable(k),
                "desc": "",
                "modules": sm,
                "steps": [],
            })
        children.append({
            "id": d,
            "title": title,
            "desc": "",
            "modules": mods,
            "children": subchildren,
        })

    framework = {
        "id": "__root__",  # 稳定根 id，供 LLM 结果 merge 入口层 title/desc
        "name": "入口层",
        "desc": "系统入口（主程序/命令行/服务启动器）",
        "modules": sorted(entry_mods),
        "children": children,
    }
    return {
        "project": ta.get("project", os.path.basename(os.path.normpath(project_path))),
        "framework": framework,
    }


def framework_json(project_path: str,
                   target_arch: Optional[dict] = None,
                   indent: Optional[int] = 2) -> str:
    """业务树 JSON 输出（供画布注入 / 编程 AI 消费）。"""
    return json.dumps(build_framework(project_path, target_arch),
                      ensure_ascii=False, indent=indent)


# ---------------------------------------------------------------------------
# P1：LLM 辅助大白话（缺 key / 失败时静态兜底，不编造）
# ---------------------------------------------------------------------------

def _static_desc_fallback(framework: dict) -> dict:
    """静态兜底：节点 desc 用模块名直出，steps 留空（画布渲染兜底）。"""
    def fill(node: dict) -> dict:
        mods = node.get("modules", [])
        names = ", ".join(_readable(m) for m in mods[:6])
        node["desc"] = node.get("desc") or (
            f"涵盖 {len(mods)} 个模块：{names}" if names else "暂无归属模块")
        for ch in node.get("children", []):
            fill(ch)
        return node
    fill(framework["framework"])
    return framework


def _collect_nodes(framework: dict) -> List[tuple]:
    """收集 (id, title, modules_basename, is_leaf) 列表，供 prompt 与合并。"""
    out: List[tuple] = []

    def walk(node: dict, path: str = "") -> None:
        nid = node.get("id", path)
        is_leaf = not node.get("children")
        bases = [_readable(m) for m in node.get("modules", [])[:8]]
        out.append((nid, node.get("title", ""), bases, is_leaf))
        for ch in node.get("children", []):
            walk(ch, f"{path}/{ch.get('id','')}")

    walk(framework["framework"], framework["framework"].get("id", "__root__"))
    return out


def _build_prompt(nodes: List[tuple]) -> str:
    """为给定节点子集构建 prompt（分批用：每批仅一个主功能子树，控制 token 防超限）。"""
    skeleton = json.dumps(
        [{"id": nid, "title": title, "modules": bases, "leaf": is_leaf}
         for nid, title, bases, is_leaf in nodes],
        ensure_ascii=False)
    return (
        "你是软件架构梳理助手。下面是一个项目自动归纳出的业务骨架片段（JSON），"
        "每个节点有 id 和机械的模块名。请用大白话为每个节点补充：\n"
        "- title：业务语义标题（中文，简短，如\"创意转视频管线\"）\n"
        "- desc：一句话描述这个功能是干什么的（中文，20-40字）\n"
        "- steps：仅对 leaf=true 的节点，列出 3-5 条业务步骤的大白话"
        "（如\"输入产品链接并选择电商平台\"），概括其典型流程\n"
        "只输出 JSON：{\"节点id\": {\"title\": \"...\", \"desc\": \"...\", "
        "\"steps\": [\"...\", \"...\"]}}。只包含片段里出现的 id，不要发明节点。\n"
        f"骨架：{skeleton}")


def _merge_llm_result(framework: dict, result: dict) -> dict:
    """用 LLM 返回的 {id: {title, desc, steps}} 覆盖骨架节点。"""
    def walk(node: dict) -> None:
        r = result.get(node.get("id", "")) or {}
        if r.get("title"):
            node["title"] = r["title"]
        if r.get("desc"):
            node["desc"] = r["desc"]
        if r.get("steps") and not node.get("children"):
            node["steps"] = [s for s in r["steps"] if isinstance(s, str)][:10]
        for ch in node.get("children", []):
            walk(ch)
    walk(framework["framework"])
    return framework


def _split_batches(framework: dict, nodes: List[tuple]) -> List[List[tuple]]:
    """按主功能分 prompt 批次：每批 = 根节点 + 一个主功能子树（控制 token 防超限）。"""
    root_id = framework["framework"].get("id", "__root__")
    root_node = next((n for n in nodes if n[0] == root_id), None)
    batches: List[List[tuple]] = []
    for c in framework["framework"].get("children", []):
        cids = {c.get("id")}

        def walk(n: dict) -> None:
            cids.add(n.get("id"))
            for ch in n.get("children", []):
                walk(ch)
        for ch in c.get("children", []):
            walk(ch)
        batch = [n for n in nodes if n[0] in cids]
        if root_node and root_node not in batch:
            batch = [root_node] + batch
        if batch:
            batches.append(batch)
    return batches


def enhance_framework_with_llm(framework: dict) -> dict:
    """LLM 辅助生成业务大白话标题/描述/步骤；缺 key / 调用失败返回静态兜底。

    分批调用（每批一个主功能子树），单批失败只跳过该批，已成功批保留；
    全部失败才整体兜底，任何异常不阻断业务树输出。
    """
    try:
        from core.llm_integration import LLMIntegration
        llm = LLMIntegration()
        if not llm.config.api_key or not llm.client:
            return _static_desc_fallback(framework)
        nodes = _collect_nodes(framework)
        batches = _split_batches(framework, nodes)
        merged: dict = {}
        for batch in batches:
            resp = llm.chat_completion(
                [{"role": "user", "content": _build_prompt(batch)}],
                response_format={"type": "json_object"},
                max_tokens=4000,
            )
            if LLMIntegration.is_error_response(resp):
                continue  # 单批失败跳过，不整体阻断
            try:
                part = json.loads(resp)
            except Exception:
                continue
            if isinstance(part, dict):
                merged.update(part)
        if not merged:
            return _static_desc_fallback(framework)
        return _merge_llm_result(framework, merged)
    except Exception:
        # 任何 LLM 路径异常都降级为静态兜底，绝不让大白话增强阻断业务树输出
        return _static_desc_fallback(framework)


def build_framework_enhanced(project_path: str,
                             target_arch: Optional[dict] = None,
                             with_llm: bool = True) -> dict:
    """业务树 + 可选 LLM 大白话（缺 key 自动静态兜底）。"""
    data = build_framework(project_path, target_arch)
    if with_llm:
        data = enhance_framework_with_llm(data)
    else:
        data = _static_desc_fallback(data)
    return data
