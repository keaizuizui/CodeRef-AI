# -*- coding: utf-8 -*-
"""
业务分析评估与自改进层
==================
God Module Split 第 2 批：从 core/business_analyzer.py 拆解出的评估/自改进子模块。

依赖拓扑（单向、无环）：
    ba_models.py ← ba_discovery.py / ba_eval.py / ba_report.py ← business_analyzer.py

职责范围（对应原文件功能分区）：
  ① Stage 4 自评估（_eval_* 各维度评分 + _ba_evaluate_quality 汇总 + 改进建议）
  ② Stage 5 自改进（_ba_result_summaries / _ba_generate_improvement）
  ③ analyze 编排辅助（_ba_init_result / _ba_snapshot_result）
"""

import os
from typing import List, Optional, Tuple

from core.ba_models import (
    AnalysisEvaluation, BusinessAnalysisResult, BusinessEntity, BusinessWorkflow,
    CrossCuttingDifference, UserRole, WorkflowHierarchy,
)


# ── Stage 4: 自评估辅助（各维度独立评分，保持原累加顺序） ──

def _eval_entity_quality(result) -> Tuple[float, Optional[str]]:
    """实体维度：已有的实体是否有清晰描述"""
    if not result.entities:
        return 0.0, "未发现任何业务实体"
    good_entities = [e for e in result.entities if len(e.purpose) > 5 and len(e.capabilities) > 0]
    good_ratio = len(good_entities) / len(result.entities)
    if good_ratio >= 0.7:
        return 1.0, None  # 大部分实体描述清晰
    if good_ratio >= 0.3:
        return 0.5, f"部分业务实体（{len(result.entities) - len(good_entities)}个）缺少详细描述"
    return 0.0, "业务实体描述过于笼统"


def _eval_role_quality(result) -> Tuple[float, Optional[str]]:
    """角色维度：已有的角色是否有描述（无角色不扣分）"""
    if not result.roles:
        # 不扣分——很多项目根本没有多角色概念
        return 0.8, None  # 中性处理：不扣分，也不给满分
    good_roles = [r for r in result.roles if len(r.description) > 5]
    if len(good_roles) == len(result.roles):
        return 1.0, None
    return 0.5, f"{len(result.roles) - len(good_roles)} 个角色描述不完整"


def _eval_workflow_quality(result) -> Tuple[float, Optional[str]]:
    """工作流维度：已有的流程是否步骤清晰（无流程不扣分）"""
    if not result.workflows:
        # 不扣分——很多项目没有"流程"概念（如工具库、服务层）
        return 0.8, None
    good_flows = [w for w in result.workflows if len(w.steps) >= 2]
    if len(good_flows) == len(result.workflows):
        return 1.0, None
    if len(good_flows) > 0:
        return 0.5, "部分业务流程步骤描述不完整"
    return 0.0, "业务流程缺少步骤说明"


def _eval_diff_quality(result) -> Tuple[float, Optional[str]]:
    """差异维度：已有的差异是否说明清楚（无差异不扣分）"""
    if not result.differences:
        # 不扣分——很多项目只有一个平台、一种角色
        return 0.8, None
    good_diffs = [d for d in result.differences if len(d.difference) > 10]
    if len(good_diffs) == len(result.differences):
        return 1.0, None
    return 0.5, "部分差异描述不完整"


def _ba_evaluate_quality(result: BusinessAnalysisResult) -> AnalysisEvaluation:
    """自我评估分析质量（V2 通用版）

    - 不硬编码任何项目的预期特征（角色、工作流、跨端差异）
    - 只评估"你分析出来的东西质量如何"，不扣"缺了什么"的分
    """
    issues = []
    quality_points = 0.0

    # 4 个内容维度（顺序与原实现一致，保证浮点累加结果不变）
    for scorer in (_eval_entity_quality, _eval_role_quality,
                   _eval_workflow_quality, _eval_diff_quality):
        points, issue = scorer(result)
        quality_points += points
        if issue:
            issues.append(issue)

    # 5. 报告覆盖率：报告覆盖了几个维度
    covered = sum(1 for x in (result.entities, result.roles,
                              result.workflows, result.differences) if x)
    quality_points += covered / 4  # 0.25 ~ 1.0

    # 最终得分 = 质量分 / 总分（原口径：4 个内容维度 + 覆盖率共 5 项检查，再 +1，分母为 6）
    total_checks = 5
    score = quality_points / (total_checks + 1)

    # 有迭代提升加分
    if result.iteration_count > 1:
        score = min(1.0, score + 0.05)

    # GitNexus 增强加分
    if result.enrichment and result.enrichment.available:
        if result.enrichment.call_pairs:
            score = min(1.0, score + 0.08)  # 有调用链数据加分
        if result.enrichment.entry_points:
            score = min(1.0, score + 0.04)  # 有入口点加分

    score = max(0.0, min(1.0, score))
    passed = score >= 0.8

    suggestions = _ba_generate_evaluation_suggestions(issues)

    return AnalysisEvaluation(
        passed=passed,
        score=score,
        issues=issues,
        suggestions=suggestions,
    )


def _ba_generate_evaluation_suggestions(issues: List[str]) -> List[str]:
    """根据问题生成改进建议"""
    suggestion_map = {
        "未发现任何业务实体": "从文件名和类名推断业务含义，关注目录结构中的业务模块划分",
        "业务实体描述过于笼统": "从类注释和函数 docstring 中提取更多业务语义",
        "部分业务实体（": "从类注释和函数 docstring 中补充业务描述",
        "缺少详细描述": "从类注释和函数 docstring 中补充业务描述",
        "角色描述不完整": "从配置文件和认证相关代码提取更多角色信息",
        "部分业务流程步骤描述不完整": "深入分析方法调用链和参数",
        "业务流程缺少步骤说明": "关注 run/execute/process 等主流程方法",
        "部分差异描述不完整": "对比不同目录下的文件功能差异",
    }

    result = []
    for issue in issues:
        matched = False
        for key, sug in suggestion_map.items():
            if key in issue:
                result.append(sug)
                matched = True
                break
        if not matched:
            result.append(f"改进: {issue}")

    return result


# ── Stage 5: 自改进辅助 ──

def _ba_result_summaries(result: BusinessAnalysisResult) -> Tuple[str, str, str, str]:
    """构建当前分析结果的四类摘要（实体/角色/流程/差异）"""
    entity_summary = "\n".join(
        f"- {e.name} ({e.technical_name}): {e.purpose} [{'; '.join(e.capabilities[:3])}]"
        for e in result.entities[:8]
    ) if result.entities else "（无）"

    role_summary = "\n".join(
        f"- {r.name}: {r.description[:60]}"
        for r in result.roles[:5]
    ) if result.roles else "（无）"

    workflow_summary = "\n".join(
        f"- {w.name}: {len(w.steps)}步 | {w.trigger[:50]}"
        for w in result.workflows[:5]
    ) if result.workflows else "（无）"

    diff_summary = "\n".join(
        f"- {d.dimension}/{d.aspect}: {d.difference[:60]}"
        for d in result.differences[:5]
    ) if result.differences else "（无）"

    return entity_summary, role_summary, workflow_summary, diff_summary


def _ba_generate_improvement(llm, result: BusinessAnalysisResult,
                             evaluation: AnalysisEvaluation) -> str:
    """核心自学习方法：
    1. LLM 分析当前轮的结果（发现了什么、缺少了什么）
    2. LLM 基于分析「硬编码」一个更好的 Prompt
    3. 下一轮分析将使用这个改进后的 Prompt
    """
    # 构建当前分析摘要
    entity_summary, role_summary, workflow_summary, diff_summary = _ba_result_summaries(result)

    # 没有 LLM 时，走规则式改进
    if not llm or not hasattr(llm, 'chat_completion'):
        issues_text = "、".join(evaluation.issues) if evaluation.issues else "无明确问题"
        return (
            f"【规则式自改进】\n"
            f"当前不足: {issues_text}\n"
            f"改进建议: {'; '.join(evaluation.suggestions)}\n"
            f"历史得分: {', '.join(f'{s:.2f}' for s in result.evaluation_scores)}"
        )

    # 有 LLM 时：让 LLM 硬编码一个更好的 Prompt
    prompt = f"""你是一位**资深业务架构师 + 提示词工程师**。你刚刚完成了一轮代码业务分析，但结果不够理想。

## 当前分析结果

【已发现的业务实体】
{entity_summary}

【已发现的用户角色】
{role_summary}

【已发现的业务流程】
{workflow_summary}

【已发现的跨端差异】
{diff_summary}

## 自评估发现问题
{chr(10).join(f'- {i}' for i in evaluation.issues)}

## 你的任务
请**硬编码一份改进后的分析 Prompt**，用于下一轮分析。

要求：
1. 基于你到现在对代码项目的理解，写出更有针对性的分析指示
2. 明确指出上一轮分析中遗漏了什么、应该关注什么
3. 用「业务分析师」的口吻，引导下一轮分析更深入地发现业务概念
4. **直接输出改进后的 Prompt 文本**，不要任何包裹

关键改进方向：
- 如果缺少业务实体 → 引导下一轮分析更仔细地识别模块边界
- 如果缺少用户角色 → 引导关注 "admin/user/role/permission" 等关键词和认证相关代码
- 如果缺少业务流程 → 引导关注方法调用链、主流程类、run/execute/process 等方法
- 如果缺少跨端差异 → 引导关注 gui/ web/ 目录差异和不同入口文件的功能对比

输出格式（直接输出，不要使用代码块）：
【第{result.iteration_count + 1}轮改进版分析指示】
[你的硬编码 Prompt 内容]"""

    try:
        improved_prompt = llm.chat_completion([
            {"role": "system", "content": "你是一位提示词工程师和业务架构师。你擅长分析代码分析结果，发现不足，并硬编码更好的分析提示词。"},
            {"role": "user", "content": prompt}
        ])
        return improved_prompt.strip()
    except Exception as e:
        issues_text = "、".join(evaluation.issues) if evaluation.issues else "无明确问题"
        return (
            f"【LLM自改进失败，回退规则式】\n"
            f"错误: {e}\n"
            f"当前不足: {issues_text}\n"
            f"改进方向: {'; '.join(evaluation.suggestions)}"
        )


# ── analyze 编排辅助 ──

def _ba_init_result(project_analysis) -> BusinessAnalysisResult:
    """从 CodeAnalyzer 结果初始化 BusinessAnalysisResult 的基础字段"""
    result = BusinessAnalysisResult()
    proj_name = os.path.basename(project_analysis.project_path.rstrip('/\\').rstrip('\\'))
    result.project_name = proj_name
    result.file_count = project_analysis.total_files
    result.line_count = project_analysis.total_lines
    result.languages = dict(project_analysis.languages)
    result.tech_summary = project_analysis.architecture_summary
    result.total_classes = sum(len(f.classes) for f in project_analysis.files)
    result.total_functions = sum(len(f.functions) for f in project_analysis.files)
    return result


def _ba_snapshot_result(result: BusinessAnalysisResult, iteration: int) -> BusinessAnalysisResult:
    """深拷贝当前轮结果作为最优快照（各字段独立副本，互不影响后续迭代）"""
    return BusinessAnalysisResult(
        project_name=result.project_name,
        tech_summary=result.tech_summary,
        languages=dict(result.languages),
        file_count=result.file_count,
        line_count=result.line_count,
        total_classes=result.total_classes,
        total_functions=result.total_functions,
        entities=[BusinessEntity(**e.__dict__) for e in result.entities],
        roles=[UserRole(**r.__dict__) for r in result.roles],
        workflows=[BusinessWorkflow(**w.__dict__) for w in result.workflows],
        hierarchy=[WorkflowHierarchy(
            name=h.name, level=h.level, description=h.description,
            module=h.module, steps=list(h.steps), trigger=h.trigger,
            output=h.output, roles_involved=list(h.roles_involved),
            children=list(h.children), is_spine=h.is_spine,
        ) for h in result.hierarchy],
        differences=[CrossCuttingDifference(**d.__dict__) for d in result.differences],
        enrichment=result.enrichment,
        iteration_count=iteration,
        improvement_history=list(result.improvement_history),
        evaluation_scores=list(result.evaluation_scores),
    )
