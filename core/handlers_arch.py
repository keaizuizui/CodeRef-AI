# -*- coding: utf-8 -*-
"""
arch / target / gov / flow 系工具 handler（handlers_arch）。

从 core/mcp_server.py 拆出（上帝模块拆解第 7 批）：模块级 handler 按领域分组后，
主文件 `from core.handlers_arch import ...` 重新绑定，Server 类同名 wrapper 语义不变。
本模块只依赖 core.* 业务模块（函数内延迟 import）与 json/os 标准库，不依赖主文件。
"""

import json
import os
from html import escape
from http.server import SimpleHTTPRequestHandler

# arch 画布静态预览服务句柄注册表（防 GC + 可重复调用复用，仿 gov_webdash._SERVERS 约定）
_ARCH_SERVE = {}


class _ArchViewHandler(SimpleHTTPRequestHandler):
    """arch 产物目录静态服务：根路径返回引导页（列出产物 + 直达最新），
    替代默认目录列表（v5.14.5/v5.15.0 测试方观察点 1）。"""

    def do_GET(self):  # noqa: N802
        if self.path in ("/", "/index.html"):
            self._serve_index()
            return
        super().do_GET()

    def _serve_index(self) -> None:
        try:
            items = sorted(
                f for f in os.listdir(self.directory)
                if f.startswith("arch_canvas_") and f.endswith(".html"))
        except OSError:
            items = []
        latest = items[-1] if items else None
        links = "".join(
            f'<li><a href="{escape(f)}">{escape(f)}</a></li>' for f in reversed(items))
        body = (
            "<!DOCTYPE html><html lang='zh-CN'><head><meta charset='utf-8'>"
            "<title>CodeRef 架构视图</title>"
            "<style>body{font-family:system-ui,sans-serif;max-width:720px;"
            "margin:40px auto;padding:0 20px;color:#1f2430}"
            "h1{font-size:22px}a{color:#4f6ef7;text-decoration:none}"
            ".latest{display:inline-block;margin:12px 0;padding:10px 18px;"
            "background:#4f6ef7;color:#fff;border-radius:8px}"
            "ul{line-height:2}</style></head><body>"
            "<h1>CodeRef 架构视图</h1>"
            + (f'<a class="latest" href="{escape(latest)}">打开最新架构视图 →</a>'
               if latest else "<p>暂无产物</p>")
            + f"<h3>历史产物（{len(items)}）</h3><ul>{links}</ul></body></html>")
        data = body.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def _serve_arch_static(directory: str, host: str = "127.0.0.1",
                       port: int = 0) -> dict:
    """启动 arch 画布静态预览服务（前端 push），仅允许回环绑定。

    ThreadingHTTPServer + _ArchViewHandler 指向产物目录（根路径引导页）；
    服务句柄存模块级注册表，返回值只含可 JSON 序列化元数据（与 gov_webdash.serve
    同约定：回环限制 / daemon 线程 / 句柄注册表）。
    """
    import threading
    from functools import partial
    from http.server import ThreadingHTTPServer

    if host not in ("127.0.0.1", "::1", "localhost"):
        host = "127.0.0.1"
    handler = partial(_ArchViewHandler, directory=directory)
    httpd = ThreadingHTTPServer((host, port), handler)
    actual_port = httpd.server_address[1]
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    url = f"http://{host}:{actual_port}"
    old = _ARCH_SERVE.get(directory)
    if old:
        try:
            old["httpd"].shutdown()
        except Exception:
            pass
    _ARCH_SERVE[directory] = {"httpd": httpd, "thread": t, "url": url,
                              "port": actual_port}
    return {"ok": True, "url": url, "host": host, "port": actual_port,
            "interactive": False}

def _flow_verify(a: dict) -> str:
    """流程合规验证（coderef_flow_verify）"""
    from core.flow_verify import verify_flow
    pp = a["project_path"]
    # steps 校验：schema 中 steps 为可选（optional）。省略 steps 键时仅执行
    # cross_lang 契约检测 / 入口与图谱定位（不依赖流程步骤），供跨语言契约专项复用；
    # 显式传 steps 但为空或含非法元素（[]/0/None/空串/非字符串元素）属契约非法，
    # 返回结构化错误而非假成功。
    if "steps" in a:
        steps = a["steps"]
        if isinstance(steps, str):
            # 兼容逗号分隔字符串
            steps = [s.strip() for s in steps.split(",") if s.strip()]
        elif isinstance(steps, (list, tuple)):
            # 逐元素校验：每个 step 必须是非空字符串（拒绝 [0]/[None]/[" "]/[""]）
            if any(not isinstance(s, str) or not s.strip() for s in steps):
                raise ValueError(
                    "coderef_flow_verify: steps 的每个元素必须是非空字符串")
            steps = [s.strip() for s in steps]
        else:
            # 非数组/非字符串（如数字 0、负数、None）→ 结构化错误
            raise ValueError(
                f"coderef_flow_verify: steps 必须是数组或逗号分隔字符串，收到 {type(steps).__name__}")
        if not steps:
            # 空数组/0/空串/全空白元素清空后 → 无待验证步骤，契约非法
            raise ValueError("coderef_flow_verify: steps 不能为空，请传入待验证的流程步骤")
    else:
        # 省略 steps 键：仅执行跨语言契约检测 / 入口与图谱定位，不验证流程步骤
        steps = []
    r = verify_flow(pp, a["entry"], list(steps), depth=a.get("depth"))
    r["tool"] = "coderef_flow_verify"
    r["project_path"] = pp
    return json.dumps(r, ensure_ascii=False)


def _arch_audit(a: dict) -> str:
    """架构腐化诊断（coderef_arch_audit）—— 复用知识图谱 CALLS 边做模块级静态诊断"""
    from core.arch_audit import audit as arch_audit
    pp = a["project_path"]
    r = arch_audit(pp)
    r["tool"] = "coderef_arch_audit"
    r["project_path"] = pp
    return json.dumps(r, ensure_ascii=False)


def _target_arch_path(project_path: str) -> str:
    """目标架构 JSON 存储路径：<project>/.coderef/target_arch.json"""
    return os.path.join(project_path, ".coderef", "target_arch.json")


def _load_target_arch(pp: str) -> dict:
    """读取已存储的目标架构；未设置时抛结构化错误。"""
    path = _target_arch_path(pp)
    if not os.path.exists(path):
        raise ValueError(
            "项目尚未设置目标架构，请先用 coderef_target_arch_set 设置，"
            "或在本工具参数中直接传入 target_arch")
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _coerce_target_arch(tool: str, ta) -> dict:
    """把 target_arch 参数规范化为 dict（支持对象或 JSON 字符串）。"""
    if isinstance(ta, str):
        try:
            ta = json.loads(ta)
        except Exception as e:
            raise ValueError(f"{tool}: target_arch 不是合法 JSON: {e}")
    if not isinstance(ta, dict):
        raise ValueError(f"{tool}: target_arch 必须是 JSON 对象")
    return ta


def _target_arch_set(a: dict) -> str:
    """设置/更新目标架构 JSON（coderef_target_arch_set）"""
    from core.arch_templates import TEMPLATES, build_target_arch, detect_template
    from core.target_arch_schema import normalize_arch, validate_target_arch
    pp = a["project_path"]
    ta = a.get("target_arch")
    template_id = None
    if ta is None:
        # 模板初始化：template 显式指定，detect 自动识别，二者都不给则报错
        template_id = a.get("template")
        detect = a.get("detect")
        if template_id:
            template_id = str(template_id).strip().lower()
            if template_id not in TEMPLATES:
                return json.dumps({
                    "status": "error",
                    "tool": "coderef_target_arch_set",
                    "project_path": pp,
                    "error": f"未知模板：{template_id}，可选：{', '.join(sorted(TEMPLATES))}",
                }, ensure_ascii=False)
        elif detect:
            found = detect_template(pp)
            if not found:
                return json.dumps({
                    "status": "error",
                    "tool": "coderef_target_arch_set",
                    "project_path": pp,
                    "error": "detect 未能识别项目类型，请显式指定 template 或传入 target_arch",
                }, ensure_ascii=False)
            template_id = found[0]
        else:
            return json.dumps({
                "status": "error",
                "tool": "coderef_target_arch_set",
                "project_path": pp,
                "error": "缺少 target_arch 参数，请传入目标架构 JSON，或使用 template/detect 按模板生成初稿",
            }, ensure_ascii=False)
        try:
            ta = build_target_arch(pp, template_id)
        except Exception as e:
            return json.dumps({
                "status": "error",
                "tool": "coderef_target_arch_set",
                "project_path": pp,
                "error": f"按模板生成 target_arch 初稿失败: {e}",
            }, ensure_ascii=False)
    else:
        try:
            ta = _coerce_target_arch("coderef_target_arch_set", ta)
        except ValueError as e:
            return json.dumps({
                "status": "error",
                "tool": "coderef_target_arch_set",
                "project_path": pp,
                "error": str(e),
            }, ensure_ascii=False)
    ok, errors = validate_target_arch(ta)
    if not ok:
        return json.dumps({
            "status": "error",
            "tool": "coderef_target_arch_set",
            "project_path": pp,
            "error": f"目标架构校验失败（{len(errors)} 条），请按 schema 修正后重试",
            "errors": errors,
        }, ensure_ascii=False)
    ta = normalize_arch(ta)
    path = _target_arch_path(pp)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(ta, f, ensure_ascii=False, indent=2)
    # 覆盖引导（①）：业务流不足或 target_modules 为空时提示（不阻断设置）
    warnings = []
    flows = ta.get("business_flows") or []
    if not flows:
        warnings.append("业务流为空，建议基于真实调用枚举主干业务流（可先跑 arch_gap 查看 flow_suggestions 列出的真实跨域主干）")
    elif len(flows) < 2:
        warnings.append(f"业务流仅 {len(flows)} 条，建议纳入 arch_gap 的 flow_suggestions 建议的真实主干业务流（来自知识图谱跨域调用），而非唯一定义单条链路")
    empty_roles = [r.get("id") for r in ta.get("tech_roles", []) if not r.get("target_modules")]
    if empty_roles:
        warnings.append(f"以下角色 target_modules 为空：{empty_roles}，target 覆盖会偏低（module_assigned 低）")
    result = {
        "status": "completed",
        "tool": "coderef_target_arch_set",
        "project_path": pp,
        "path": path,
        "summary": {
            "roles": len(ta.get("tech_roles", [])),
            "flows": len(flows),
            "constraints": len(ta.get("constraints", [])),
        },
        "warnings": warnings,
    }
    if template_id:
        result["template"] = template_id
    return json.dumps(result, ensure_ascii=False)


def _target_arch_get(a: dict) -> str:
    """获取当前目标架构（coderef_target_arch_get）"""
    pp = a["project_path"]
    path = _target_arch_path(pp)
    if not os.path.exists(path):
        return json.dumps({
            "status": "not_set",
            "tool": "coderef_target_arch_get",
            "project_path": pp,
            "message": "尚未设置目标架构，请先用 coderef_target_arch_set 设置",
        }, ensure_ascii=False)
    with open(path, "r", encoding="utf-8") as f:
        ta = json.load(f)
    return json.dumps({
        "status": "completed",
        "tool": "coderef_target_arch_get",
        "project_path": pp,
        "path": path,
        "target_arch": ta,
    }, ensure_ascii=False)


def _target_adopt(a: dict) -> str:
    """游离一键纳入（coderef_target_adopt）"""
    from core.arch_gap_analyzer import adopt_free_modules
    from core.target_arch_schema import validate_target_arch
    pp = a["project_path"]
    ta = a.get("target_arch")
    if ta is None:
        try:
            ta = _load_target_arch(pp)
        except ValueError as e:
            return json.dumps({
                "status": "error",
                "tool": "coderef_target_adopt",
                "project_path": pp,
                "error": str(e),
            }, ensure_ascii=False)
    else:
        try:
            ta = _coerce_target_arch("coderef_target_adopt", ta)
        except ValueError as e:
            return json.dumps({
                "status": "error",
                "tool": "coderef_target_adopt",
                "project_path": pp,
                "error": str(e),
            }, ensure_ascii=False)
    ok, errors = validate_target_arch(ta)
    if not ok:
        return json.dumps({
            "status": "error",
            "tool": "coderef_target_adopt",
            "project_path": pp,
            "error": f"目标架构无效（{len(errors)} 条），请先用 coderef_target_arch_set 设置合法目标架构",
            "errors": errors,
        }, ensure_ascii=False)
    dry_run = a.get("dry_run", False)
    if not isinstance(dry_run, bool):
        return json.dumps({
            "status": "error",
            "tool": "coderef_target_adopt",
            "project_path": pp,
            "error": f"dry_run 必须是布尔值，收到 {dry_run!r}",
        }, ensure_ascii=False)
    monitored = a.get("monitored", "free")
    if monitored not in ("free", "all"):
        return json.dumps({
            "status": "error",
            "tool": "coderef_target_adopt",
            "project_path": pp,
            "error": f"monitored 仅支持 free/all，收到 {monitored!r}",
        }, ensure_ascii=False)
    r = adopt_free_modules(pp, ta, role_id=a.get("role_id"),
                           modules=a.get("modules"),
                           monitored=monitored,
                           dry_run=dry_run)
    r["tool"] = "coderef_target_adopt"
    r["project_path"] = pp
    return json.dumps(r, ensure_ascii=False)


def _arch_gap(a: dict) -> str:
    """架构差距分析（coderef_arch_gap）"""
    from core.arch_gap_analyzer import analyze_gap
    from core.target_arch_schema import validate_target_arch
    pp = a["project_path"]
    ta = a.get("target_arch")
    if ta is None:
        try:
            ta = _load_target_arch(pp)
        except ValueError as e:
            return json.dumps({
                "status": "error",
                "tool": "coderef_arch_gap",
                "project_path": pp,
                "error": str(e),
            }, ensure_ascii=False)
    else:
        try:
            ta = _coerce_target_arch("coderef_arch_gap", ta)
        except ValueError as e:
            return json.dumps({
                "status": "error",
                "tool": "coderef_arch_gap",
                "project_path": pp,
                "error": str(e),
            }, ensure_ascii=False)
    ok, errors = validate_target_arch(ta)
    if not ok:
        return json.dumps({
            "status": "error",
            "tool": "coderef_arch_gap",
            "project_path": pp,
            "error": f"目标架构无效（{len(errors)} 条），请先用 coderef_target_arch_set 设置合法目标架构",
            "errors": errors,
        }, ensure_ascii=False)
    max_unassigned = a.get("max_unassigned", 50)
    r = analyze_gap(pp, ta, max_unassigned=max_unassigned)
    r["tool"] = "coderef_arch_gap"
    r["project_path"] = pp
    return json.dumps(r, ensure_ascii=False)


def _arch_canvas(a: dict) -> str:
    """可视化架构画布（coderef_arch_canvas，5.15 决策者报告首屏 + 分区画布 + open_server 前端推送）

    v5.18：同时产出「业务流程视图」（actor × stage 泳道），与业务树画布并列，
    两者共用同一份业务语义标注（`.coderef/business_semantics.json`）。
    """
    from core.arch_view import render_arch_view
    pp = a["project_path"]
    ta = a.get("target_arch")
    if ta is not None:
        ta = _coerce_target_arch("coderef_arch_canvas", ta)
    output_dir = a.get("output_dir")
    filepath = render_arch_view(project_path=pp, target_arch=ta,
                                output_dir=output_dir)
    r = {
        "status": "completed",
        "tool": "coderef_arch_canvas",
        "project_path": pp,
        "result_path": filepath,
        "message": ("架构视图已生成：默认打开为决策者架构总览报告（一段话讲清 这是什么系统·怎么搭·怎么跑·哪里要修），"
                    "点「查看业务架构图」进入业务树画布（入口→主功能→子功能→步骤，AI 归纳大白话）"),
    }
    if a.get("with_flow", True):
        try:
            from core.business_semantics import analyze, write_result
            from core.arch_flow import render_file
            sem = analyze(pp, use_llm=bool(a.get("flow_use_llm", True)))
            sem_path = write_result(pp, sem)
            flow_dir = output_dir or os.path.join(pp, ".coderef")
            flow_path = render_file(pp, sem, output_dir=flow_dir,
                                    title="业务流程视图")
            r["flow_path"] = flow_path
            r["semantics_path"] = sem_path
            r["flow_summary"] = sem.get("summary", {})
            r["message"] += ("；已同时生成「业务流程视图」（按角色与流程阶段划分的泳道画布），"
                             "未判定 actor/stage 的模块会进「未归类」并计数，不假装已归类")
        except Exception as e:  # noqa: BLE001
            # 流程视图是增强项：失败不影响业务树画布可用性
            r["flow_error"] = f"业务流程视图生成失败: {e}"
    if a.get("open_server"):
        r["server"] = _serve_arch_static(
            os.path.dirname(filepath),
            host=a.get("host", "127.0.0.1"), port=a.get("port", 0))
    return json.dumps(r, ensure_ascii=False)


def _flow_canvas(a: dict) -> str:
    """交互式流程画布（coderef_flow_canvas）"""
    from core.flow_canvas import FlowCanvas
    pp = a["project_path"]
    output_dir = a.get("output_dir")
    max_entries = a.get("max_entries", 6)
    max_depth = a.get("max_depth", 6)
    filepath = FlowCanvas().generate(project_path=pp, output_dir=output_dir,
                                     max_entries=max_entries, max_depth=max_depth)
    return json.dumps({
        "status": "completed",
        "tool": "coderef_flow_canvas",
        "project_path": pp,
        "result_path": filepath,
        "message": "流程画布已生成，浏览器打开即可拖拽编辑、导出画布 JSON",
    }, ensure_ascii=False)


def _refactor_plan(a: dict) -> str:
    """重构任务卡生成（coderef_refactor_plan）"""
    from core.refactor_task_generator import RefactorTaskGenerator
    pp = a["project_path"]
    ta = a.get("target_arch")
    if ta is not None:
        ta = _coerce_target_arch("coderef_refactor_plan", ta)
    gap_result = a.get("gap_result")
    r = RefactorTaskGenerator().generate(pp, gap_result=gap_result,
                                         target_arch=ta)
    r["tool"] = "coderef_refactor_plan"
    r["project_path"] = pp
    return json.dumps(r, ensure_ascii=False)


def _arch_verify(a: dict) -> str:
    """架构对齐验证（coderef_arch_verify）"""
    from core.arch_alignment_verifier import ArchAlignmentVerifier
    pp = a["project_path"]
    ta = a.get("target_arch")
    if ta is not None:
        ta = _coerce_target_arch("coderef_arch_verify", ta)
    changed_files = a.get("changed_files")
    r = ArchAlignmentVerifier().verify(pp, target_arch=ta,
                                       changed_files=changed_files)
    r["tool"] = "coderef_arch_verify"
    r["project_path"] = pp
    return json.dumps(r, ensure_ascii=False)


def _gov_start(a: dict) -> str:
    """建档体检周期并导入差距为治理工作项（coderef_gov_start）"""
    from core.healthcycle import HealthCycle
    pp = a["project_path"]
    ta = a.get("target_arch")
    if ta is not None:
        ta = _coerce_target_arch("coderef_gov_start", ta)
    hc = HealthCycle(pp)
    r = hc.start_cycle(
        name=a.get("name", ""), description=a.get("description", ""),
        end_date=a.get("end_date", ""), target_arch=ta,
        max_unassigned=a.get("max_unassigned", 50))
    r["tool"] = "coderef_gov_start"
    r["project_path"] = pp
    return json.dumps(r, ensure_ascii=False)


def _gov_close(a: dict) -> str:
    """收尾体检周期（coderef_gov_close）"""
    from core.healthcycle import HealthCycle
    pp = a["project_path"]
    hc = HealthCycle(pp)
    r = hc.close_cycle(a.get("cid", ""), note=a.get("note", ""))
    r["tool"] = "coderef_gov_close"
    r["project_path"] = pp
    return json.dumps(r, ensure_ascii=False)


def _gov_issues(a: dict) -> str:
    """查询治理工作项（coderef_gov_issues）"""
    from core.healthcycle import HealthCycle
    pp = a["project_path"]
    hc = HealthCycle(pp)
    status = a.get("status", "")
    if status:
        r = {"ok": True, "view": "status",
             "count": 0, "status_counts": hc.store.status_counts(),
             "issues": hc.store.list_issues(
                 cycle_id=a.get("cycle_id", ""), status=status,
                 assignee=a.get("assignee", ""), limit=a.get("limit", 500))}
        r["ok"] = True
        r["count"] = len(r["issues"])
    else:
        r = hc.issues(cycle_id=a.get("cycle_id", ""), view=a.get("view", "open"),
                      assignee=a.get("assignee", ""), limit=a.get("limit", 500))
    r["tool"] = "coderef_gov_issues"
    r["project_path"] = pp
    return json.dumps(r, ensure_ascii=False)


def _gov_transition(a: dict) -> str:
    """治理工作项状态流转/豁免（coderef_gov_transition）"""
    from core.healthcycle import HealthCycle
    pp = a["project_path"]
    iid = a["issue_id"]
    act = a.get("action", "transition")
    hc = HealthCycle(pp)
    if act == "reject":
        if not a.get("reason"):
            return json.dumps({"ok": False,
                               "message": "豁免必须提供 reason（防掩盖真实问题）"},
                              ensure_ascii=False)
        r = hc.reject_issue(iid, reason=a.get("reason", ""),
                            actor=a.get("actor", ""), detail=a.get("detail", ""))
    elif act == "meta":
        r = hc.set_issue_meta(iid, priority=a.get("priority"),
                              assignee=a.get("assignee"),
                              due_date=a.get("due_date"),
                              note=a.get("note"), actor=a.get("actor", ""))
    else:
        if not a.get("to_state"):
            return json.dumps({"ok": False, "message": "transition 需提供 to_state"},
                              ensure_ascii=False)
        r = hc.transition_issue(iid, a.get("to_state", ""),
                                actor=a.get("actor", ""), detail=a.get("detail", ""))
    r["tool"] = "coderef_gov_transition"
    r["project_path"] = pp
    return json.dumps(r, ensure_ascii=False)


def _gov_report(a: dict) -> str:
    """体检报告/治理看板（coderef_gov_report，5.12.1 合并 report+board）"""
    from core.healthcycle import HealthCycle
    from core.gov_dashboard import render_report
    from core.gov_webdash import render_board, serve
    pp = a["project_path"]
    action = a.get("action", "report")
    if action not in ("report", "board", "overview"):
        raise ValueError(f"coderef_gov_report: 未知 action={action}")
    if action == "overview":
        #  契约：缺省落盘 <project>/.coderef/project_overview.html，返回确切路径
        #  缺省只读（静态自包含）；显式起服务时才默认启用交互流转
        from core.project_overview import render_overview
        interactive = a.get("interactive")
        if interactive is None:
            interactive = bool(a.get("open_server") or a.get("open"))
        output_dir = a.get("output_dir", "")
        if not output_dir:
            output_dir = os.path.join(pp, ".coderef")
        r = render_overview(pp, output_dir=output_dir,
                            cid=a.get("cid", ""), interactive=interactive)
        r["project_path"] = pp
        if a.get("open_server") or a.get("open"):
            srv = serve(pp, host=a.get("host", "127.0.0.1"),
                        port=a.get("port", 0))
            r["server"] = srv
        return json.dumps(r, ensure_ascii=False)
    if action == "board":
        #  契约：缺省落盘 <project>/.coderef/gov_board.html，返回确切路径
        interactive = a.get("interactive", True)
        output_dir = a.get("output_dir", "")
        if not output_dir:
            output_dir = os.path.join(pp, ".coderef")
        r = render_board(pp, output_dir=output_dir,
                         cid=a.get("cid", ""), interactive=interactive)
        r["tool"] = "coderef_gov_report"
        r["project_path"] = pp
        if a.get("open_server") or a.get("open"):
            srv = serve(pp, host=a.get("host", "127.0.0.1"),
                        port=a.get("port", 0))
            r["server"] = srv
        return json.dumps(r, ensure_ascii=False)
    if a.get("out_format", "json") == "html":
        r = render_report(pp, output_dir=a.get("output_dir", ""),
                          cid=a.get("cid", ""))
    else:
        hc = HealthCycle(pp)
        r = hc.report(cid=a.get("cid") or "")
        r["tool"] = "coderef_gov_report"
        r["project_path"] = pp
    return json.dumps(r, ensure_ascii=False)


def _gov_board(a: dict) -> str:
    """治理 Web 看板（coderef_gov_board 兼容别名，转发 coderef_gov_report action=board）"""
    payload = json.loads(_gov_report({**a, "action": "board"}))
    payload["tool"] = "coderef_gov_board"
    return json.dumps(payload, ensure_ascii=False)


def _gov_pipeline(a: dict) -> str:
    """治理自动化流水线（coderef_gov_pipeline）"""
    from core.gov_pipeline import GovPipeline
    pp = a["project_path"]
    r = GovPipeline(pp).run(
        issue_ids=a.get("issue_ids") or [],
        changed_files=a.get("changed_files"),
        auto_verified=a.get("auto_verified", True))
    r["tool"] = "coderef_gov_pipeline"
    r["project_path"] = pp
    return json.dumps(r, ensure_ascii=False)


def _dynamic_probe(a: dict) -> str:
    """动态探针（coderef_dynamic_probe）"""
    from core.dynamic_probe import probe
    pp = a["project_path"]
    r = probe(pp, include_tests=a.get("include_tests", False))
    r["tool"] = "coderef_dynamic_probe"
    r["project_path"] = pp
    return json.dumps(r, ensure_ascii=False)


def _gov_workspace(a: dict) -> str:
    """多代码库聚合治理（coderef_gov_workspace）"""
    from core.gov_workspace import aggregate
    raw = a.get("projects") or []
    if not isinstance(raw, list) or not raw:
        raise ValueError("coderef_gov_workspace: projects 必须是非空数组")
    # 逐个做绝对路径 + 存在性校验，防 ../ 穿越读取治理库
    projects = [_validate_project_path("coderef_gov_workspace", p) for p in raw]
    r = aggregate(projects)
    r["tool"] = "coderef_gov_workspace"
    return json.dumps(r, ensure_ascii=False)


def _gov_schedule(a: dict) -> str:
    """定时体检（coderef_gov_schedule）"""
    from core.gov_schedule import render
    pp = a["project_path"]
    r = render(pp, cron_expr=a.get("cron_expr", "0 6 * * 1"),
               command=a.get("command", "coderef_scan"),
               output_dir=a.get("output_dir", ""))
    r["tool"] = "coderef_gov_schedule"
    r["project_path"] = pp
    return json.dumps(r, ensure_ascii=False)


def _role_boundary(a: dict) -> str:
    """符号级职责越界检测（coderef_role_boundary）"""
    from core.role_boundary import detect
    pp = a["project_path"]
    ta = a.get("target_arch")
    if ta is not None:
        ta = _coerce_target_arch("coderef_role_boundary", ta)
    r = detect(pp, target_arch=ta,
               max_issues=a.get("max_issues", 200),
               semantic=a.get("semantic", False))
    r["tool"] = "coderef_role_boundary"
    r["project_path"] = pp
    return json.dumps(r, ensure_ascii=False)


def _validate_project_path(tool: str, p: str) -> str:
    """校验并规范化 project_path。

    规则：
    1. 空串/纯空白/缺省 → 结构化错误（此前缺省键 audit 仍完成并生成报告，属假阴性）
    2. 相对路径（含 .. / . / 纯文件名）→ 拒绝，要求绝对路径，
       避免 ".." 越权扫描上级目录、空串被当作 cwd 扫描被测源码自身
    3. 绝对路径但目录不存在/不是目录 → 结构化错误（此前静默返回空成功）
    返回规范化后的绝对路径（realpath）。
    """
    if not p or not p.strip():
        raise ValueError(
            f"{tool}: project_path 不能为空或缺失，请传入目标项目的绝对路径")
    p = p.strip()
    if not os.path.isabs(p):
        raise ValueError(
            f"{tool}: project_path 必须是绝对路径（收到相对路径 '{p}'）。"
            f"相对路径（如 .. / 空串）会被解析到非预期目录，已拒绝以防越权扫描")
    real = os.path.realpath(p)
    if not os.path.isdir(real):
        raise ValueError(f"{tool}: project_path 目录不存在: {p}")
    return real


def _arch(a) -> str:
    from core.pipeline_runner import Pipe
    insight_llm = a.get("insight_llm", False)
    if insight_llm is not True and insight_llm is not False:
        raise ValueError("coderef_architecture: insight_llm 必须是布尔值")
    r = Pipe().architecture(a["project_path"],
                            output_dir=a.get("output_dir") or None,
                            insight_llm=insight_llm)
    # 结构化返回，与 coderef_audit 一致
    return json.dumps({
        "status": "completed",
        "tool": "coderef_architecture",
        "project_path": a["project_path"],
        "report": r.report,
        "report_path": r.report_path or "",
        "arch_canvas": getattr(r, "arch_canvas", ""),
        "summary": {
            "total_files": getattr(r, "total_files", 0),
            "total_lines": getattr(r, "total_lines", 0),
            "findings": len(r.findings),
            "elapsed": getattr(r, "elapsed", 0),
        },
        "errors": getattr(r, "errors", []),
    }, ensure_ascii=False)

