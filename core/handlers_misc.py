# -*- coding: utf-8 -*-
"""
scan / version / eval / review / docs / wl / query / verify 系工具 handler（handlers_misc）。

从 core/mcp_server.py 拆出（上帝模块拆解第 7 批）：模块级 handler 按领域分组后，
主文件 `from core.handlers_misc import ...` 重新绑定，Server 类同名 wrapper 语义不变。
依赖说明：
  - _SINGLE_TOOL_LABELS / WHITELIST_ACTIONS 从 core.tools_manifest 引入（纯数据层，无循环）；
  - PKG_VERSION 由主文件计算（版本号唯一真源在 core/version.py），为避免循环 import，
    _version/_eval 在函数内延迟 `from core.mcp_server import PKG_VERSION` 读取。
"""

import json
import os

from core.tools_manifest import _SINGLE_TOOL_LABELS, WHITELIST_ACTIONS

def _scan_tool(a: dict) -> str:
    """运行单个审计维度（coderef_scan），返回结构化 JSON findings。

    实时安全带：只跑一个维度（不建图谱/不生成 dashboard），快速返回，
    供 AI 在写完一个模块后即时自查 / 作为客观第二意见。
    """
    from core.pipeline_runner import Pipe
    tool = a.get("tool", "")
    # P2-2：工具维度名严格白名单校验（大小写敏感）。schema enum 为精确匹配，
    # 运行时不再借 run_single 的 .lower() 容错，避免 "Gov" 等大小写混写被静默
    # 放行执行；非法维度返回结构化错误而非空成功。
    valid = {k for k, _ in _SINGLE_TOOL_LABELS}
    if tool not in valid:
        raise ValueError(
            f"coderef_scan: 未知工具维度 '{tool}'，支持(大小写敏感): "
            f"{', '.join(sorted(valid))}")
    pp = a["project_path"]
    r = Pipe().run_single(pp, tool)
    from core.result_envelope import for_scan
    return json.dumps({
        "status": "completed",
        "tool": "coderef_scan",
        "dimension": tool,
        "project_path": pp,
        "findings": [
            {
                "id": f.id, "category": f.category, "severity": f.severity,
                "tier": f.tier.value, "file": f.file_path, "line": f.line,
                "line_label": f.line_label, "title": f.title,
                "detail": f.detail, "suggestion": f.suggestion,
                "xval_by": f.xval_by,
                # 降噪聚合保留的追溯字段：count=该 finding 代表的同规则违规数，
                # locations=被合并的全部位置。硬约束：工具结果必须完整收集，
                # 禁止静默丢弃管道层精心保留的结构化字段。
                "count": getattr(f, "count", 1),
                "locations": getattr(f, "locations", []),
            }
            for f in r.findings
        ],
        "summary": {
            "total_files": r.total_files, "total_lines": r.total_lines,
            "findings": len(r.findings),
            # 加权总数：把聚合条目的 count 展开回真实违规数，
            # 与 findings（聚合后的条目数）互补，供调用方估算真实规模。
            "findings_weighted": sum(getattr(f, "count", 1) for f in r.findings),
            "high": sum(1 for f in r.findings if f.tier.value == "high"),
            "medium": sum(1 for f in r.findings if f.tier.value == "medium"),
            "low": sum(1 for f in r.findings if f.tier.value == "low"),
            "elapsed": r.elapsed,
        },
        "errors": r.errors,
        "envelope": for_scan(len(r.findings), tool, r.errors),
    }, ensure_ascii=False)


def _scan_list() -> str:
    """列出 coderef_scan 可选维度清单"""
    from core.pipeline_runner import Pipe
    return json.dumps({
        "status": "completed",
        "tool": "coderef_scan_list",
        "dimensions": Pipe.list_single_tools(),
    }, ensure_ascii=False)


def _version(a: dict) -> str:
    """轻量版本探针（coderef_version）：返回当前进程加载的版本号。

    测试方反馈：靠「结果字段反推版本」会因进程未重启加载旧代码而误判进程新旧，
    需一行调用即可断言 版本==target。只读、零副作用、不触发扫描/图谱构建。

    传入 project_path 时额外返回派生产物新鲜度快报（各产物生成时间、是否
    早于代码最后修改、建议刷新方式），供调用方判断是否要先重跑再引用结论。
    """
    from core.mcp_server import PKG_VERSION
    out = {
        "status": "completed",
        "tool": "coderef_version",
        "name": "coderef-ai",
        "version": PKG_VERSION,
    }
    pp = a.get("project_path")
    if pp:
        try:
            from core.artifact_freshness import report
            out["artifacts_report"] = report(pp)
        except Exception as e:  # noqa: BLE001
            # 快报是增强项：失败不影响版本探针本身
            out["artifacts_report"] = {"error": str(e)}
    # 使用契约：对方 AI 通常不加载本仓 skill，必须由工具自己声明解读边界
    try:
        from core.result_envelope import handshake
        out["consumer_contract"] = handshake()
    except Exception as e:  # noqa: BLE001
        out["consumer_contract"] = {"error": str(e)}
    return json.dumps(out, ensure_ascii=False)


def _eval(a: dict) -> str:
    """产出物语义评估（coderef_eval）：给 CodeRef 自身的 LLM 产出物加语义质量门禁。

    4 个语义指标 + 软硬判定（硬指标一票否决，防致命错误被平均分稀释）。
    LLM-as-judge，显式标注"AI 判断"；只做软门禁，不阻断确定性结论。
    缺 API key 硬阻断返回 SKIP（不降级编造）。
    """
    from core.mcp_server import PKG_VERSION
    from core.output_evaluator import OutputEvaluator
    pp = a["project_path"]
    r = OutputEvaluator().evaluate(
        a.get("text"),
        metric=a.get("metric", ""),
        context=a.get("context"),
        threshold=a.get("threshold"),
        action=a.get("action", "assert"),
    )
    r["tool"] = "coderef_eval"
    r["project_path"] = pp
    r["coderef_version"] = PKG_VERSION
    return json.dumps(r, ensure_ascii=False)


def _docs_read(a: dict) -> str:
    """按需读取 Wiki 文档正文（coderef_docs_read）"""
    from core.pipeline_runner import Pipe
    pp = a["project_path"]
    # max_chars 防御性转换：非法值回退默认，避免 MCP 层抛 ValueError
    try:
        max_chars = int(a.get("max_chars", 20000))
    except (TypeError, ValueError):
        max_chars = 20000
    # 负 max_chars 会被 content[:max_chars] 当成"去尾 n 字符"（content[:-n]）静默截断
    if max_chars <= 0:
        return json.dumps({
            "status": "error", "tool": "coderef_docs_read",
            "error": f"max_chars 必须为正整数（收到 {max_chars}）",
        }, ensure_ascii=False)
    r = Pipe().docs_read(
        pp, doc=a.get("doc") or None,
        output_dir=a.get("output_dir") or None,
        max_chars=max_chars,
    )
    r["tool"] = "coderef_docs_read"
    r["project_path"] = pp
    return json.dumps(r, ensure_ascii=False)


def _verify_findings(a: dict) -> str:
    """确定性核验 LLM / CodeRabbit 论断（coderef_verify_findings）"""
    from core.verify_findings import verify_findings, render_report, render_html
    pp = a["project_path"]
    findings = a.get("findings") or []
    if isinstance(findings, str):
        findings = json.loads(findings)
    entry = a.get("entry") or None
    out_format = a.get("out_format", "json")
    r = verify_findings(pp, list(findings), entry=entry)
    r["tool"] = "coderef_verify_findings"
    r["project_path"] = pp
    if out_format == "html":
        r["report_html"] = render_html(r)
    elif out_format == "text":
        r["report_text"] = render_report(r)
    return json.dumps(r, ensure_ascii=False)


def _review(a) -> str:
    """执行代码审查（coderef_review），返回结构化 JSON 文本"""
    from core.code_review import CodeReviewer
    pp = a["project_path"]
    mode = a.get("mode", "diff")
    dims = a.get("dimensions") or None
    changed_files = a.get("changed_files") or None
    diff = a.get("diff") or None
    r = CodeReviewer().review(
        pp, mode=mode, diff=diff,
        changed_files=changed_files, dimensions=dims,
    )
    return json.dumps(r, ensure_ascii=False)


def _frontend(a) -> str:
    """执行前端交互审查（coderef_frontend），返回结构化 JSON 文本"""
    from core.frontend_inspector import FrontendInspector
    pp = a["project_path"]
    mode = a.get("mode", "static")
    url = a.get("url") or None
    entry = a.get("entry") or None
    levels = a.get("check_levels") or None
    r = FrontendInspector().inspect(
        pp, entry=entry, mode=mode, url=url, check_levels=levels,
        persona=a.get("persona") or None,
        journey=a.get("journey") or None,
    )
    return json.dumps(r, ensure_ascii=False)


def _has_audit_artifacts(project_path: str) -> bool:
    """是否已有落盘的审计产物（只做 stat，不触发任何扫描）。"""
    from core.scorecard import find_findings_json
    return bool(find_findings_json(project_path))


def _report(a) -> str:
    """执行 HTML 报告渲染（coderef_report）：
    优先重渲染既有产物（图谱+Wiki）；无既有产物时回退为跑一次全量审计并渲染。
    渲染前会重算一次记分卡快照（确定性聚合，含一次结构扫描），其他渲染路径只读该快照。
    open_server=true 时额外拉起本地回环评估台（点按钮即评估，不依赖 AI 在旁）。"""
    from core.pipeline_runner import Pipe
    pp = a["project_path"]
    # 统一输出口径：out_html 是报告目录（index.html 所在），out_root 是审计产物根。
    # audit 会在 out_root 下落 findings JSON 并在 out_root/html 渲染；
    # render_report 直接在 out_html 渲染。两者必须同源，否则重渲染永远找不到产物（U-49）。
    out_html = os.path.abspath(a["output_dir"]) if a.get("output_dir") else None
    # 仅当输出目录本身叫 html 时才把父目录当审计根；否则调用方给的
    # 自定义目录就是根——直接取 dirname 会把报告/产物写到无关的上级目录。
    if out_html and os.path.basename(out_html).lower() == "html":
        out_root = os.path.dirname(out_html)
    else:
        out_root = out_html

    def _refresh_card() -> str:
        """重算并落盘记分卡快照；成功返回空串（用于清除上一次的错误）。"""
        try:
            from core.scorecard import compute, write_card
            write_card(pp, compute(pp))
            return ""
        except Exception as e:  # noqa: BLE001
            return str(e)

    # 评估台是用户入口：这里显式重算并落盘记分卡快照；渲染层只读快照，
    # 因此审计/docs 等其他渲染路径不会再被拖进一次全量结构扫描。
    # 已有审计产物时才先算分；没有产物时等回退审计产出后再算，避免空算一次。
    refreshed = _has_audit_artifacts(pp)
    sc_err = _refresh_card() if refreshed else ""
    r, has_artifacts = Pipe().render_report(pp, output_dir=out_html)
    if not has_artifacts:
        r = Pipe().audit(pp, output_dir=out_root)
    # 审计刚产出新 findings（或此前没有审计产物）：重算快照后只重渲染一次。
    # render_report 只聚合既有产物、不重扫，因此这一步很轻。
    if not has_artifacts or not refreshed:
        sc_err = _refresh_card()
        r2, ok2 = Pipe().render_report(pp, output_dir=out_html)
        # 重渲染失败时保留审计结果，避免丢掉 html_report 与审计错误
        if ok2 and getattr(r2, "html_report", None):
            r = r2
        else:
            errs = list(getattr(r2, "errors", []) or [])
            if errs and hasattr(r, "errors"):
                r.errors.extend(errs)
    hr = getattr(r, "html_report", None) or {}
    if sc_err:
        hr["scorecard_error"] = sc_err
    if a.get("open_server"):
        try:
            from core.report_console import serve
            idx = hr.get("index") or ""
            serve_dir = os.path.dirname(os.path.abspath(idx)) if idx else out_html

            def _rerender() -> None:
                """重渲染评估台；未产出新页面时必须抛错，供页面显式提示（U-49）。"""
                r2, ok = Pipe().render_report(pp, output_dir=serve_dir)
                got = (getattr(r2, "html_report", None) or {}).get("index")
                if not ok or not got:
                    reason = "；".join(getattr(r2, "errors", []) or []) \
                        or "重渲染未产出 index.html"
                    raise RuntimeError(reason)

            srv = serve(pp,
                        output_dir=serve_dir,
                        host=a.get("host") or "127.0.0.1",
                        port=int(a.get("port") or 0),
                        # 组合根注入重渲染：评估落盘后刷新页面，
                        # 传输层（report_console）不直接依赖编排层（pipeline_runner）
                        rerender=_rerender)
            hr["serve"] = srv
        except Exception as e:  # noqa: BLE001
            hr["serve"] = {"ok": False, "error": str(e)}
    from core.result_envelope import for_report
    hr["envelope"] = for_report(bool(hr.get("ok")), str(hr.get("error") or ""),
                                getattr(r, "errors", []) or [])
    return json.dumps(hr, ensure_ascii=False)


def _advisor(a) -> str:
    """审计策略判定 + 功能审查（coderef_audit_advisor）"""
    from core.review_strategy import review_advisor
    from core.functional_review import functional_reviewer
    pp = a["project_path"]
    with_functional = a.get("with_functional", True)
    strategy = review_advisor.advise(pp)
    result = {"strategy": strategy}
    if with_functional:
        try:
            fr = functional_reviewer.review(pp, strategy)
            result["functional_review"] = fr
        except Exception as e:
            result["functional_review"] = {"llm_available": False,
                                           "degraded": True, "error": str(e)}
    return json.dumps(result, ensure_ascii=False)


def _wl(a) -> str:
    from core.pipeline_runner import Pipe
    act = a.get("action", "add")
    # 非法 action 此前静默落到默认 add 分支（参数契约矩阵暴露的缺陷）；
    # 改为结构化拒绝，与 wiki_style/docs_read/strategy 的枚举严格校验保持一致。
    if not isinstance(act, str) or act not in WHITELIST_ACTIONS:
        return json.dumps({
            "status": "error",
            "tool": "coderef_whitelist",
            "error": (f"非法 action 枚举: {act!r}；允许的取值为 "
                      f"{' / '.join(sorted(WHITELIST_ACTIONS))}"),
        }, ensure_ascii=False)
    pp = a["project_path"]
    if act == "list":
        wl = Pipe.whitelist_list(pp)
        return json.dumps({"count": len(wl), "entries": wl}, ensure_ascii=False)
    elif act == "clear":
        n = Pipe.whitelist_clear(pp)
        return json.dumps({"cleared": n}, ensure_ascii=False)
    elif act == "core_rules_get":
        return json.dumps(Pipe.core_rules_get(pp), ensure_ascii=False)
    elif act == "core_rules_set":
        rules = a.get("core_rules", {})
        if not rules:
            return json.dumps({"error": "core_rules 不能为空"}, ensure_ascii=False)
        return json.dumps(Pipe.core_rules_set(pp, rules), ensure_ascii=False)
    elif act == "core_rules_reset":
        return json.dumps(Pipe.core_rules_reset(pp), ensure_ascii=False)
    else:  # add
        entries = a.get("entries", [])
        if not entries:
            return json.dumps({"error": "entries 不能为空"}, ensure_ascii=False)
        n = Pipe.whitelist_add(pp, entries)
        return json.dumps({"added": n, "total": len(Pipe.whitelist_list(pp))}, ensure_ascii=False)


def _query(a) -> str:
    from core.pipeline_runner import Pipe
    qt = a.get("query_type", "stats")
    pp = a["project_path"]
    kwargs = {k: v for k, v in a.items()
              if k not in ("project_path", "query_type") and v}
    result = Pipe.kg_query(pp, qt, **kwargs)
    return json.dumps(result, ensure_ascii=False)

