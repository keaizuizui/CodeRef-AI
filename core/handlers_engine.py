# -*- coding: utf-8 -*-
"""
memory / operation_memory / prompt / owasp / innovation / change 系工具 handler（handlers_engine）。

从 core/mcp_server.py 拆出（上帝模块拆解第 7 批）：模块级 handler 按领域分组后，
主文件 `from core.handlers_engine import ...` 重新绑定，Server 类同名 wrapper 语义不变。
本模块只依赖 core.* 业务模块（函数内延迟 import）与 json 标准库，不依赖主文件。
"""

import json

def _change_guard(a: dict) -> str:
    """运行 AI 代码退化检测 / 守护 git 基层管理（coderef_change_guard）"""
    from core.change_guard import ChangeGuard
    pp = a["project_path"]
    action = a.get("action") or "guard"
    timeout = a.get("git_timeout")
    git_bin = a.get("git_bin") or None
    cg = ChangeGuard()
    if action == "ensure_git":
        r = cg.ensure_git_repo(pp, git_timeout=timeout, git_bin=git_bin)
    elif action == "anchor":
        r = cg.anchor_health_baseline(
            pp, label=a.get("label"), git_timeout=timeout,
            allow_autocommit=a.get("allow_autocommit", True), git_bin=git_bin)
    elif action == "list_baselines":
        r = {"ok": True, "baselines": cg.list_health_baselines(
            pp, git_timeout=timeout, git_bin=git_bin)}
    else:
        diff = a.get("diff") or None
        baseline = a.get("baseline_dir") or None
        r = cg.guard(pp, diff=diff, baseline_dir=baseline,
                     git_timeout=timeout, git_bin=git_bin)
    r["tool"] = "coderef_change_guard"
    r["project_path"] = pp
    r["action"] = action
    return json.dumps(r, ensure_ascii=False)


def _change_report(a: dict) -> str:
    """生成人话版变更报告（coderef_change_report）"""
    from core.change_report import ChangeReport
    pp = a["project_path"]
    diff = a.get("diff") or ""
    r = ChangeReport().report(pp, diff)
    r["tool"] = "coderef_change_report"
    r["project_path"] = pp
    return json.dumps(r, ensure_ascii=False)


def _memory(a: dict) -> str:
    """项目记忆层（coderef_memory，5.12.2 合并 4 工具）"""
    from core.memory_layer import memory_layer
    from core.memory_quality import MemoryQuality
    pp = a["project_path"]
    act = a.get("action", "sync")
    if act == "sync":
        r = memory_layer.sync(pp, mode=a.get("mode", "full"))
    elif act == "query":
        qt = a.get("query_type", "semantic")
        kwargs = {k: v for k, v in a.items()
                  if k not in ("project_path", "action", "query_type", "node_id") and v}
        if qt == "relations" and a.get("node_id"):
            kwargs["keyword"] = a["node_id"]  # 底层 relations 以 keyword 复用为 node_id
        r = memory_layer.query(pp, query_type=qt, **kwargs)
    elif act == "status":
        r = memory_layer.status(pp)
    elif act == "quality":
        r = MemoryQuality().assess(pp, auto_fix=a.get("auto_fix", False))
    else:
        raise ValueError(f"coderef_memory: 未知 action={act}")
    r["tool"] = "coderef_memory"
    r["project_path"] = pp
    return json.dumps(r, ensure_ascii=False)


def _operation_memory(a: dict) -> str:
    """操作记忆层（coderef_operation_memory，5.12.3 合并 6 工具）"""
    from core.operation_memory import operation_memory
    pp = a["project_path"]
    act = a.get("action", "sync")
    if act == "sync":
        r = operation_memory.sync(pp, mode=a.get("mode", "full"),
                                  with_llm=a.get("with_llm", True))
    elif act == "query":
        r = operation_memory.query(pp, query_type=a.get("query_type", "all"),
                                   keyword=a.get("keyword", ""),
                                   limit=a.get("limit", 10))
    elif act == "find":
        r = operation_memory.find(pp, name=a.get("name", ""), limit=a.get("limit", 5))
    elif act == "status":
        r = operation_memory.status(pp)
    elif act == "recover":
        r = operation_memory.recover(pp, limit=int(a.get("limit", 8)))
    elif act == "export":
        r = operation_memory.export_markdown(pp, output_path=a.get("output_path", ""))
    else:
        raise ValueError(f"coderef_operation_memory: 未知 action={act}")
    r["tool"] = "coderef_operation_memory"
    r["project_path"] = pp
    return json.dumps(r, ensure_ascii=False)


def _prompt_mgmt(a: dict) -> str:
    """Prompt 资产管理（兼容层：4.6 已并入 coderef_prompt_governance，此处仅做转发）"""
    from core.prompt_governance import govern_prompt
    pp = a["project_path"]
    r = govern_prompt(
        pp,
        action="assets",
        name=a.get("name", ""),
        content=a.get("content", ""),
        version=a.get("version", ""),
        abtest_group=a.get("abtest_group", ""),
        asset_action=a.get("action", ""),
    )
    r["tool"] = "coderef_prompt_mgmt"
    r["deprecated"] = True
    r["migrate_to"] = "coderef_prompt_governance"
    r["project_path"] = pp
    return json.dumps(r, ensure_ascii=False)


def _prompt_audit(a: dict) -> str:
    """确定性 Prompt 合规审计（兼容层：4.6 已并入 coderef_prompt_governance，此处仅做转发）"""
    from core.prompt_governance import govern_prompt
    pp = a["project_path"]
    r = govern_prompt(
        pp,
        action="audit",
        out_format=a.get("out_format", "json"),
    )
    r["tool"] = "coderef_prompt_audit"
    r["deprecated"] = True
    r["migrate_to"] = "coderef_prompt_governance"
    r["project_path"] = pp
    return json.dumps(r, ensure_ascii=False)


def _owasp(a: dict) -> str:
    """OWASP LLM Top 10 合规检测（coderef_owasp）"""
    from core.owasp_compliance import OWASPCompliance
    pp = a["project_path"]
    out_format = a.get("out_format", "json")
    r = OWASPCompliance().check(pp, out_format=out_format)
    r["tool"] = "coderef_owasp"
    r["project_path"] = pp
    return json.dumps(r, ensure_ascii=False)


def _innovation(a: dict) -> str:
    """识别项目创新设计 + 传播缺口（coderef_innovation）"""
    from core.innovation_engine import InnovationEngine
    pp = a["project_path"]
    r = InnovationEngine().detect(
        pp, intent=a.get("intent", ""),
        min_adoption=a.get("min_adoption", 0.0),
    )
    r["tool"] = "coderef_innovation"
    r["project_path"] = pp
    return json.dumps(r, ensure_ascii=False)


def _asset(a: dict) -> str:
    """WorkflowAsset 资产化/查询/导出（coderef_asset）"""
    from core.innovation_engine import InnovationEngine
    pp = a["project_path"]
    r = InnovationEngine().asset(
        pp, action=a.get("action", "list"), canonical=a.get("canonical", ""),
        description=a.get("description", ""), template_code=a.get("template_code", ""),
        patch_suggestion=a.get("patch_suggestion", ""),
        migration_guide=a.get("migration_guide", ""),
        blueprint=a.get("blueprint"),
    )
    r["tool"] = "coderef_asset"
    r["project_path"] = pp
    return json.dumps(r, ensure_ascii=False)


def _registry(a: dict) -> str:
    """管理已知设计库（coderef_registry）"""
    from core.design_registry import DesignRegistry
    pp = a["project_path"]
    r = DesignRegistry().manage(
        pp, action=a.get("action", "list"), name=a.get("name", ""),
        canonical=a.get("canonical", ""), alias=a.get("alias", ""),
        description=a.get("description", ""),
    )
    r["tool"] = "coderef_registry"
    r["project_path"] = pp
    return json.dumps(r, ensure_ascii=False)


def _replicate(a: dict) -> str:
    """复刻铺排：检测目标项目缺口 + 生成复刻指引（coderef_replicate）"""
    from core.replicate_engine import replicate_design, render_report, render_html
    pp = a["project_path"]
    out_format = a.get("out_format", "json")
    r = replicate_design(
        pp, a["canonical"],
        verify_symbols=a.get("verify_symbols", True),
    )
    r["tool"] = "coderef_replicate"
    r["project_path"] = pp
    if out_format == "html":
        r["report_html"] = render_html(r)
    elif out_format == "text":
        r["report_text"] = render_report(r)
    return json.dumps(r, ensure_ascii=False)


def _replicate_apply(a: dict) -> str:
    """复刻落地：把已固化资产的复刻指引落到目标项目（coderef_replicate_apply）"""
    from core.replicate_engine import apply_replicate
    pp = a["project_path"]
    # overwrite 必须是真正的布尔：只接受 True/False，拒绝 "false"/"0" 等被 bool() 误转成 True 的输入
    raw_overwrite = a.get("overwrite", False)
    if raw_overwrite is not True and raw_overwrite is not False:
        return json.dumps({
            "ok": False,
            "tool": "coderef_replicate_apply",
            "project_path": pp,
            "error": f"overwrite 必须是布尔值（True/False），收到 {raw_overwrite!r}（{type(raw_overwrite).__name__}）。",
            "summary": "overwrite 参数类型非法，已拒绝执行。",
        }, ensure_ascii=False)
    r = apply_replicate(
        pp, a["canonical"],
        target=a.get("target", ""),
        filename=a.get("filename", ""),
        overwrite=raw_overwrite,
    )
    r["tool"] = "coderef_replicate_apply"
    r["project_path"] = pp
    return json.dumps(r, ensure_ascii=False)


def _innovation_review(a: dict) -> str:
    """创新复刻排查：LLM 阅读管线设计 + wiki，判定创新与复刻合理性（coderef_innovation_review）"""
    from core.innovation_review import review_innovation, render_report, render_html
    pp = a["project_path"]
    out_format = a.get("out_format", "json")
    r = review_innovation(
        pp, a["canonical"],
        target=a.get("target", ""),
        out_format=out_format,
    )
    r["tool"] = "coderef_innovation_review"
    r["project_path"] = pp
    if out_format == "html":
        r["report_html"] = render_html(r)
    elif out_format == "text":
        r["report_text"] = render_report(r)
    return json.dumps(r, ensure_ascii=False)


def _asset_blueprint(a: dict) -> str:
    """把复刻铺排结论写回资产蓝图（coderef_asset_blueprint）"""
    from core.replicate_engine import solidify_asset_blueprint
    pp = a["project_path"]
    entry_points = a.get("entry_points") or []
    if isinstance(entry_points, str):
        import re as _re
        entry_points = [s.strip() for s in _re.split(r"[,\s;]+", entry_points) if s.strip()]
    r = solidify_asset_blueprint(pp, a["canonical"], entry_points=list(entry_points))
    r["tool"] = "coderef_asset_blueprint"
    r["project_path"] = pp
    return json.dumps(r, ensure_ascii=False)


def _govern(a: dict) -> str:
    """Prompt 治理平台：编排资产生命周期 × 合规审计 × 跨模块一致性（coderef_prompt_governance）"""
    from core.prompt_governance import govern_prompt
    pp = a["project_path"]
    r = govern_prompt(
        pp,
        action=a.get("action", "overview"),
        name=a.get("name", ""),
        content=a.get("content", ""),
        version=a.get("version", ""),
        abtest_group=a.get("abtest_group", ""),
        asset_action=a.get("asset_action", ""),
        out_format=a.get("out_format", "json"),
    )
    r["tool"] = "coderef_prompt_governance"
    r["project_path"] = pp
    return json.dumps(r, ensure_ascii=False)


def _interpret(a: dict) -> str:
    """人话解读平台：健康/仪表盘/论断核验/Wiki/Prompt 治理/资产（coderef_interpret）"""
    from core.interpretation_platform import interpret_project
    pp = a["project_path"]
    r = interpret_project(
        pp,
        action=a.get("action", "health"),
        findings_text=a.get("findings_text", ""),
        entry=a.get("entry", ""),
        out_format=a.get("out_format", "json"),
    )
    r["tool"] = "coderef_interpret"
    r["project_path"] = pp
    return json.dumps(r, ensure_ascii=False)

