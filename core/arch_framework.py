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
