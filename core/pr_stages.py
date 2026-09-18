# -*- coding: utf-8 -*-
"""
管线阶段函数（core/pipeline_runner 拆解子模块）：_gov / _sca / _inn / _simp / _matu /
_wiki / _denoise。

原为 pipeline_runner.py 1352~1534 行；拆出后由主文件 re-export，对外符号不变。
依赖方向：本模块依赖 pr_models / pr_whitelist / pr_ckpt / pr_render；对主文件的
Pipe 类与 _dedup_adjacent/_burst_merge/TaskCancelled 采用函数内延迟 import，
避免模块级循环依赖。_gov 的 governance 三件套热更新 reload 链（先子后父）原样保留。
"""

import os, importlib

from core.pr_models import Finding, PipeResult, Tier
from core.pr_whitelist import _tier_for, _match_whitelist
from core.pr_ckpt import _save, _load_whitelist
from core.pr_render import _match_noise_rule


def _gov(p: str, r: PipeResult, done: set):
    if "gov" in done: return
    try:
        importlib.invalidate_caches()
        pc = os.path.join(os.path.dirname(__file__), "__pycache__")
        if os.path.exists(pc):
            for f in os.listdir(pc):
                if ("governance" in f and ("base" in f or "checks" in f
                                           or "audit" in f)) or "shared_filter" in f:
                    os.remove(os.path.join(pc, f))
        from core import governance_audit as g, shared_filter as sf
        import core.governance_base as gb
        import core.governance_checks as gc
        # 热更新先子后父：base → checks → audit 门面壳，保证规则常量/检测逻辑均刷新
        importlib.reload(sf); importlib.reload(gb); importlib.reload(gc); importlib.reload(g)
        a = g.GovernanceAuditor(); a.audit(p)
        ro = getattr(a, "report", None)
        if ro:
            for v in ro.violations:
                # 缺陷 5：gov violations 的 title/detail 缺少数符号引用，导致
                # extract_symbols(title+detail) 返回空、符号级证据核验(L3)失效。
                # 把命中的代码行（line_content，含函数调用等符号）附加到 detail，
                # 使符号核验可提取到具体函数/模块名。
                hit_line = getattr(v, "line_content", "") or ""
                det = v.detail
                if hit_line:
                    det = f"{det}｜命中代码: {hit_line}"
                r.findings.append(Finding(id=f"gov-{len(r.findings)}", tool="gov",
                    category=v.category, severity=v.severity,
                    file_path=v.file_path, line=v.line_number,
                    title=f"[{v.rule_id}] {v.rule_name}", detail=det,
                    suggestion=v.suggestion, tier=_tier_for(v.severity)))
        done.add("gov"); _save(p, list(done))
    except Exception as e: r.errors.append(f"gov: {e}")

def _sca(p: str, r: PipeResult, done: set):
    if "sca" in done: return
    try:
        from core.sca_checker import SCAChecker
        c = SCAChecker(); c.scan(p)
        rep = getattr(c, "report", None)
        for dep in getattr(rep, "dependencies", []):
            for v in getattr(dep, "vulnerabilities", []):
                r.findings.append(Finding(id=f"sca-{len(r.findings)}", tool="sca",
                    category="dependency_vuln", severity=getattr(v,"severity","medium"),
                    file_path=getattr(dep,"source_file",""), line=getattr(dep,"source_line",0),
                    title=f"{getattr(dep,'package','')} {getattr(dep,'version','')} - {getattr(v,'cve_id','')}",
                    detail=getattr(v,"summary",""),
                    suggestion=f"升级到 {(getattr(v,'fixed_version',None) or '最新可用版本')} 修复漏洞",
                    tier=_tier_for(getattr(v,"severity","medium"))))
        # 无漏洞时也追加一条"扫描完成"汇总 finding，确保 sca 结果不被静默丢弃
        if not any(f.tool == "sca" for f in r.findings):
            scanned = getattr(rep, "scanned_deps", 0)
            r.findings.append(Finding(id=f"sca-{len(r.findings)}", tool="sca",
                category="dependency_scan", severity="low",
                file_path="", line=0,
                title=f"依赖扫描完成：扫描 {scanned} 个依赖，未发现已知漏洞",
                detail="SCA 工具已执行，本项目依赖未命中本地/OSV 已知漏洞库。",
                suggestion="", tier=Tier.LOW))
        # 非 CVE 类供应链风险（供应链安装 / 依赖未锁定 / 弃用依赖）
        for risk in getattr(rep, "supply_chain_risks", []):
            r.findings.append(Finding(id=f"sca-{len(r.findings)}", tool="sca",
                category="supply_chain_install", severity="high",
                file_path=risk.get("file", ""), line=risk.get("line", 0),
                title=f"供应链安装风险：运行时自动安装第三方包（{risk.get('file','')}:{risk.get('line',0)}）",
                detail=risk.get("detail", ""),
                suggestion="运行时禁止自动安装第三方包；改为启动前显式安装并锁定版本/hash 校验，包名来源需经人工确认",
                tier=Tier.HIGH))
        for risk in getattr(rep, "unpinned_deps", []):
            r.findings.append(Finding(id=f"sca-{len(r.findings)}", tool="sca",
                category="unpinned_dependency", severity="medium",
                file_path=risk.get("source_file", ""), line=risk.get("source_line", 0),
                title=f"依赖未锁定：{risk.get('package','')} {risk.get('version','')}",
                detail=risk.get("detail", ""),
                suggestion="为依赖固定精确版本并生成 lock 文件（如 requirements.lock/poetry.lock），保证构建可复现",
                tier=Tier.MEDIUM))
        for risk in getattr(rep, "deprecated_deps", []):
            r.findings.append(Finding(id=f"sca-{len(r.findings)}", tool="sca",
                category="deprecated_dependency", severity="medium",
                file_path=risk.get("source_file", ""), line=risk.get("source_line", 0),
                title=f"弃用依赖：{risk.get('package','')}（迁移至 {risk.get('migration','')}）",
                detail=risk.get("detail", ""),
                suggestion=f"迁移至官方替代 {risk.get('migration','')}",
                tier=Tier.MEDIUM))
        done.add("sca"); _save(p, list(done))
    except Exception as e: r.errors.append(f"sca: {e}")

def _inn(p: str, r: PipeResult, done: set):
    if "inn" in done: return
    try:
        from core.innovation_propagation_detector import InnovationPropagationDetector
        # 实战修复：缺口检测 _generate_suggestion 对每个缺口无条件调用 LLM 且无上限，
        # 在含 vendored 库的大项目上会挂起数十分钟。审计管线改用纯结构对比（无 LLM）。
        d = InnovationPropagationDetector(); d.detect(p, use_llm=False)
        for s in getattr(d, "gaps", []):
            r.findings.append(Finding(id=f"inn-{len(r.findings)}", tool="inn",
                category="propagation_gap", severity="medium",
                file_path=getattr(s,"target_file",""), line=0,
                title=f"{getattr(s,'target_module','')} 缺少 {getattr(getattr(s,'pattern',None),'pattern_name','')} 模式",
                detail=getattr(s,"suggestion",""), suggestion=getattr(s,"suggestion",""),
                tier=Tier.MEDIUM))
        done.add("inn"); _save(p, list(done))
    except Exception as e: r.errors.append(f"inn: {e}")

def _simp(p: str, r: PipeResult, done: set):
    if "simp" in done: return
    try:
        from core.code_simplifier import CodeSimplifier
        c = CodeSimplifier(); c.analyze(p)
        for s in getattr(c, "last_items", []) or []:
            lr = s.get("line_range", [0, 0]) if isinstance(s, dict) else [0, 0]
            line = lr[0] if isinstance(lr, (list, tuple)) and lr else 0
            r.findings.append(Finding(id=f"simp-{len(r.findings)}", tool="simp",
                category=(s.get("category","") if isinstance(s, dict) else getattr(s,"category","")),
                severity=(s.get("severity","medium") if isinstance(s, dict) else getattr(s,"severity","medium")),
                file_path=(s.get("file_path","") if isinstance(s, dict) else getattr(s,"file_path","")),
                line=line,
                title=(s.get("title","") if isinstance(s, dict) else getattr(s,"title","")),
                detail=(s.get("current","") if isinstance(s, dict) else getattr(s,"current","")),
                suggestion=(s.get("suggestion","") if isinstance(s, dict) else getattr(s,"suggestion","")),
                tier=_tier_for((s.get("severity","medium") if isinstance(s, dict) else getattr(s,"severity","medium")))))
        done.add("simp"); _save(p, list(done))
    except Exception as e: r.errors.append(f"simp: {e}")

def _matu(p: str, r: PipeResult, done: set):
    if "matu" in done: return
    try:
        from core.project_maturity_checker import ProjectMaturityChecker
        c = ProjectMaturityChecker()
        rep = c.check(p)
        for s in getattr(rep, "checks", []):
            if getattr(s, "status", "") == "pass":
                continue  # 只上报未通过的成熟度检查
            # 成熟度检查含两类：缺陷/缺失(kind=defect) 与 工程化改进建议(kind=suggestion)。
            # 建议项(a.k.a 工程化 warn)不以安全缺陷同级别的 severity 进入缺陷汇总，
            # 通过 kind="advice" + severity="info" 标记，供汇总/报告层区分"建议项"与"缺陷项"。
            # getattr 兜底，即使 detector 未提供 kind 也保持向后兼容。
            mk = getattr(s, "kind", "defect")
            is_advice = mk == "suggestion"
            sev = "info" if is_advice else "medium"
            r.findings.append(Finding(id=f"matu-{len(r.findings)}", tool="matu",
                category=getattr(s,"category",""), severity=sev,
                file_path="", line=0,
                title=f"[{getattr(s,'check_id','')}] {getattr(s,'name','')} - {getattr(s,'status','')}",
                detail=getattr(s,"detail",""), suggestion=getattr(s,"suggestion",""),
                kind="advice" if is_advice else "defect",
                tier=_tier_for(sev)))
        done.add("matu"); _save(p, list(done))
    except Exception as e: r.errors.append(f"matu: {e}")

def _wiki(p: str, r: PipeResult, done: set, output_dir: str = None,
          wiki_style: str = "comprehensive",
          include_subprojects: bool = True,
          enable_agent_pointer: bool = False,
          cross_verify: bool = True,
          cross_entry_spec: str = "class:pipeline_runner:Pipe",
          progress_cb=None):
    if "wiki" in done: return
    # 延迟 import：TaskCancelled 定义于主文件，运行时主文件已加载完成，避免模块级循环
    from core.pipeline_runner import TaskCancelled
    try:
        from core.wiki_generator import WikiGenerator
        # 尊重调用方指定的输出目录；未指定时回退到默认 txt/
        wo = output_dir or os.path.join(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__))), "txt")
        # ：进入逐模块生成（主耗时段）前先过一个进度/取消检查点，其余阶段由
        # docs() 在扫描/图谱处上报；wg.generate 内部不暴露进度时此处即为协作式收尾点。
        if progress_cb:
            progress_cb("wiki生成", 3, 3, "逐模块生成文档")
        wg = WikiGenerator()
        gres = wg.generate(p, output_dir=wo, wiki_style=wiki_style,
                           include_subprojects=include_subprojects,
                           enable_agent_pointer=enable_agent_pointer,
                           cross_verify=cross_verify,
                           cross_entry_spec=cross_entry_spec,
                           # /CodeRabbit major：透传 progress_cb，使 wiki 逐模块
                           # 生成循环内置的取消检查点可触达，取消后不再跑到底。
                           progress_cb=progress_cb)
        # 把 Wiki 生成失败明细带入管线结果，让 _fmt / MCP 层能感知"部分文档生成失败"，
        # 避免部分阶段失败却仍对外标记为 fully completed。
        for e in getattr(gres, "errors", []) or []:
            r.errors.append(f"wiki: {e}")
        r.wiki_result = gres
        done.add("wiki"); _save(p, list(done))
    except TaskCancelled:
        raise
    except Exception as e:
        r.errors.append(f"wiki: {e}")

def _denoise(r: PipeResult):
    """自动降噪：AI白名单（file/rule/category 匹配 + dir 级排除）+ 规则去重 + 抑制 + 降级"""
    if not r.findings:
        return

    suppressed = 0
    downgraded = 0
    wl_suppressed = 0
    dir_excluded = 0
    kept = []

    # 加载 AI 白名单
    wl = _load_whitelist(r.project_path)

    # ── 第〇轮：白名单 dir 级排除（所有审计维度一致生效）──
    # 以条目携带 dir 字段为准（对齐 U-39 教训：dir 排除不是 rule=="dir" 假条件），
    # 复用图谱层 _is_excluded_path 判定语义，保证与图谱符号级排除口径一致。
    exclude_dirs = [e["dir"] for e in wl if e.get("dir")]
    if exclude_dirs:
        from core.code_knowledge_graph import _is_excluded_path
        pre = r.findings
        r.findings = [f for f in pre
                      if not _is_excluded_path(f.file_path, r.project_path,
                                               exclude_dirs)]
        dir_excluded = len(pre) - len(r.findings)

    # ── 第一轮：AI 白名单 + 规则匹配 ──
    for f in r.findings:
        if _match_whitelist(f, wl):
            wl_suppressed += 1
            continue
        matched_rule = _match_noise_rule(f)
        if matched_rule:
            action = matched_rule["action"]
            if action == "suppress":
                suppressed += 1
                continue
            elif action == "downgrade":
                f.tier = Tier.LOW
                downgraded += 1
        kept.append(f)

    r.findings = kept

    # ── 第二轮：邻近行合并（同 file + tool + category) ──
    # 延迟 import：_dedup_adjacent/_burst_merge 定义于主文件，运行时主文件已加载完成
    from core.pipeline_runner import _dedup_adjacent, _burst_merge
    r.findings = _dedup_adjacent(r.findings)

    # ── 第三轮：爆发式合并（同 tool + category > 阈值 → 保留 1 条 + 摘要）──
    r.findings = _burst_merge(r.findings)

    # 记录降噪统计
    if suppressed or downgraded or wl_suppressed or dir_excluded:
        setattr(r, 'noise_suppressed', suppressed)
        setattr(r, 'noise_downgraded', downgraded)
        setattr(r, 'wl_suppressed', wl_suppressed)
        setattr(r, 'dir_excluded', dir_excluded)
