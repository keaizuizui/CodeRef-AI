# -*- coding: utf-8 -*-
"""
target_arch_schema — 目标架构 JSON Schema 定义与校验（5.0 Phase 0）

目标架构是"人定义的正轨"，作为差距分析器（arch_gap_analyzer）与对齐验证器
的输入参照系。本模块只负责：
  1. 定义目标架构 JSON 的标准结构（常量）；
  2. 校验一份目标架构 JSON 是否合法（零依赖手写校验，不引入 jsonschema）。

设计原则：
- 纯函数、零内部依赖：只依赖标准库，可被任何 core 模块安全复用。
- 确定性：校验结果只由输入决定，不依赖 LLM。
- 结构化错误：返回错误列表而非抛异常，方便 MCP 层直接透传。
"""

from typing import Any, Dict, List, Optional, Tuple

# 当前支持的约束规则（后续可扩展 allowed_dependency 等）
SUPPORTED_RULES: tuple = ("no_dependency",)

# 顶层必填键
REQUIRED_TOP_KEYS: tuple = ("version", "tech_roles")

# tech_roles 每项必填键
REQUIRED_ROLE_KEYS: tuple = ("id", "name", "target_modules")

# business_flows 每项必填键
REQUIRED_FLOW_KEYS: tuple = ("id", "name", "steps")

# business_flows.steps 每项必填键
REQUIRED_STEP_KEYS: tuple = ("id", "name")

# constraints 每项必填键
REQUIRED_CONSTRAINT_KEYS: tuple = ("from", "to", "rule")


def _is_str_list(v: Any) -> bool:
    return isinstance(v, list) and all(isinstance(x, str) and x.strip() for x in v)


def validate_target_arch(arch: Any) -> Tuple[bool, List[str]]:
    """校验目标架构 JSON 是否合法。

    Args:
        arch: 目标架构（dict 或可解析为 dict 的对象）。

    Returns:
        (ok, errors)：ok=True 表示合法；errors 为结构化错误列表。
    """
    errors: List[str] = []
    if not isinstance(arch, dict):
        return False, ["目标架构必须是 JSON 对象（dict）"]

    # 顶层必填键：仅检查存在性；类型/非空由下面专项校验
    for k in REQUIRED_TOP_KEYS:
        if k not in arch:
            errors.append(f"缺少顶层必填键: {k}")
    if "version" in arch:
        ver = arch.get("version")
        if not isinstance(ver, str) or not ver.strip():
            errors.append("version 必须是非空字符串")
    if arch.get("project") is not None and not isinstance(arch["project"], str):
        errors.append("project 必须是字符串")

    # tech_roles
    roles = arch.get("tech_roles")
    if roles is None:
        roles = []
    if not isinstance(roles, list):
        errors.append("tech_roles 必须是数组")
        roles = []
    role_ids: set = set()
    for i, role in enumerate(roles):
        if not isinstance(role, dict):
            errors.append(f"tech_roles[{i}] 必须是对象")
            continue
        for k in REQUIRED_ROLE_KEYS:
            if k not in role:
                errors.append(f"tech_roles[{i}] 缺少必填键: {k}")
        rid = role.get("id")
        if isinstance(rid, str) and rid.strip():
            if rid in role_ids:
                errors.append(f"tech_roles 角色 id 重复: {rid}")
            role_ids.add(rid)
        else:
            errors.append(f"tech_roles[{i}].id 必须是非空字符串")
        if "target_modules" in role and not _is_str_list(role.get("target_modules")):
            errors.append(f"tech_roles[{i}].target_modules 必须是非空字符串数组")
        # role_keywords：角色职责关键词表（可选），供符号级职责越界检测匹配符号职责。
        if "role_keywords" in role and not _is_str_list(role.get("role_keywords")):
            errors.append(f"tech_roles[{i}].role_keywords 必须是非空字符串数组")
        if "depends_on" in role and not _is_str_list(role.get("depends_on")):
            errors.append(f"tech_roles[{i}].depends_on 必须是非空字符串数组")
        if "depended_by" in role and not _is_str_list(role.get("depended_by")):
            errors.append(f"tech_roles[{i}].depended_by 必须是非空字符串数组")

    # business_flows
    flows = arch.get("business_flows")
    if flows is None:
        flows = []
    if not isinstance(flows, list):
        errors.append("business_flows 必须是数组")
        flows = []
    flow_ids: set = set()
    for i, flow in enumerate(flows):
        if not isinstance(flow, dict):
            errors.append(f"business_flows[{i}] 必须是对象")
            continue
        for k in REQUIRED_FLOW_KEYS:
            if k not in flow:
                errors.append(f"business_flows[{i}] 缺少必填键: {k}")
        fid = flow.get("id")
        if isinstance(fid, str) and fid.strip():
            if fid in flow_ids:
                errors.append(f"business_flows 流程 id 重复: {fid}")
            flow_ids.add(fid)
        else:
            errors.append(f"business_flows[{i}].id 必须是非空字符串")
        steps = flow.get("steps")
        if not isinstance(steps, list):
            errors.append(f"business_flows[{i}].steps 必须是数组")
            steps = []
        step_ids: set = set()
        for j, step in enumerate(steps):
            if not isinstance(step, dict):
                errors.append(f"business_flows[{i}].steps[{j}] 必须是对象")
                continue
            for k in REQUIRED_STEP_KEYS:
                if k not in step:
                    errors.append(f"business_flows[{i}].steps[{j}] 缺少必填键: {k}")
            sid = step.get("id")
            if isinstance(sid, str) and sid.strip():
                if sid in step_ids:
                    errors.append(f"business_flows[{i}].steps 步骤 id 重复: {sid}")
                step_ids.add(sid)
            else:
                errors.append(f"business_flows[{i}].steps[{j}].id 必须是非空字符串")
            tr = step.get("tech_roles")
            if tr is not None and not _is_str_list(tr):
                errors.append(
                    f"business_flows[{i}].steps[{j}].tech_roles 必须是非空字符串数组")
            # 步骤引用的角色 id 必须已定义
            if _is_str_list(tr):
                for rid in tr:
                    if rid not in role_ids:
                        errors.append(
                            f"business_flows[{i}].steps[{j}] 引用了未定义的角色: {rid}")

    # constraints
    constraints = arch.get("constraints")
    if constraints is None:
        constraints = []
    if not isinstance(constraints, list):
        errors.append("constraints 必须是数组")
        constraints = []
    for i, c in enumerate(constraints):
        if not isinstance(c, dict):
            errors.append(f"constraints[{i}] 必须是对象")
            continue
        for k in REQUIRED_CONSTRAINT_KEYS:
            if k not in c:
                errors.append(f"constraints[{i}] 缺少必填键: {k}")
        if c.get("rule") not in SUPPORTED_RULES:
            errors.append(
                f"constraints[{i}].rule 不支持: {c.get('rule')!r}，"
                f"当前仅支持 {list(SUPPORTED_RULES)}")
        for side in ("from", "to"):
            rid = c.get(side)
            if isinstance(rid, str) and rid.strip():
                if rid not in role_ids:
                    errors.append(f"constraints[{i}].{side} 引用了未定义的角色: {rid}")
            else:
                errors.append(f"constraints[{i}].{side} 必须是非空字符串")

    return len(errors) == 0, errors


def normalize_arch(arch: Dict[str, Any]) -> Dict[str, Any]:
    """规范化目标架构：补齐缺省空数组，保证下游访问安全。

    只做结构性兜底，不修改用户显式提供的内容。
    """
    out = dict(arch)
    out.setdefault("business_flows", [])
    out.setdefault("tech_roles", [])
    out.setdefault("constraints", [])
    for flow in out.get("business_flows", []):
        if isinstance(flow, dict):
            flow.setdefault("steps", [])
    for role in out.get("tech_roles", []):
        if isinstance(role, dict):
            role.setdefault("target_modules", [])
            role.setdefault("role_keywords", [])
            role.setdefault("depends_on", [])
            role.setdefault("depended_by", [])
    return out
