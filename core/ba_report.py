# -*- coding: utf-8 -*-
"""
业务分析报告渲染层
==================
God Module Split 第 2 批：从 core/business_analyzer.py 拆解出的报告渲染子模块。

依赖拓扑（单向、无环）：
    ba_models.py ← ba_discovery.py / ba_eval.py / ba_report.py ← business_analyzer.py

职责范围（对应原文件功能分区）：
  ① 章节辅助（_ba_append_h2 / _ba_append_h3 / _ba_filter_business_entities）
  ② to_business_report 各章节渲染（_render_ba_*）
  ③ 层级树渲染（_render_hier_children / _render_hier_mermaid）
  ④ 数据摘要（_ba_call_summary_parts / _ba_build_report_data_summary）
"""

import datetime
from typing import List, Tuple
from collections import defaultdict

from core.ba_models import (
    BusinessAnalysisResult, BusinessEntity, BusinessWorkflow, WorkflowHierarchy,
)


# ── 报告渲染辅助（to_business_report 的各章节渲染函数） ──

def _ba_append_h2(lines: List[str], title: str) -> None:
    """追加二级标题段落（空行 + ## 标题 + 空行）"""
    lines.append('')
    lines.append(f'## {title}')
    lines.append('')


def _ba_append_h3(lines: List[str], title: str) -> None:
    """追加三级标题段落"""
    lines.append(f'### {title}')
    lines.append('')


def _ba_filter_business_entities(entities: List[BusinessEntity]) -> List[BusinessEntity]:
    """过滤非业务实体（测试/根模块/缓存等）"""
    _skip = {'测试', '根模块', '方案待处理', 'root', 'tests', 'test', 'node_modules', '__pycache__'}
    return [e for e in entities
            if e.technical_name.lower() not in _skip and e.name not in _skip]


def _render_ba_header(lines: List[str], result: BusinessAnalysisResult) -> None:
    """渲染报告头部（标题 + 元信息）"""
    project_display = result.project_name

    lines.append(f'# {project_display} — 业务架构全景报告')
    lines.append('')
    lines.append(f'> **报告日期**：{datetime.datetime.now().strftime("%Y-%m-%d %H:%M")}')
    lines.append('> **分析引擎**：CodeRef-AI BusinessAnalyzer V2.1 + GitNexus Code Intelligence')
    lines.append(f'> **代码规模**：{result.file_count} 文件 / {result.line_count:,} 行 / {result.total_classes} 类 / {result.total_functions} 函数')
    if result.enrichment and result.enrichment.available:
        lines.append(f'> **GitNexus 索引**：{len(result.enrichment.all_symbols)} 符号 / {len(result.enrichment.call_pairs)} 调用关系 / {len(result.enrichment.entry_points)} 入口点')
    lines.append('')
    lines.append('---')
    lines.append('')


def _render_ba_architecture(lines: List[str], entities: List[BusinessEntity]) -> None:
    """渲染第一章：系统架构总览（Mermaid 全景图 + 分层说明）"""
    _ba_append_h2(lines, '一、系统架构总览')

    lines.append('### 1.1 架构全景图')
    lines.append('')
    lines.append('```mermaid')
    lines.append('graph TB')
    lines.append('    subgraph 用户层')
    lines.append('        U1[营销人员/Web端]')
    lines.append('        U2[管理员/GUI端]')
    lines.append('    end')
    lines.append('')
    lines.append('    subgraph 业务工具层')
    # 为每个实体生成节点
    for e in entities[:8]:
        node_id = e.technical_name.replace('-', '_').replace(' ', '_')[:20]
        lines.append(f'        {node_id}[{e.name}]')
    lines.append('    end')
    lines.append('')
    lines.append('    subgraph 共享服务层')
    lines.append('        S1[LLM客户端]')
    lines.append('        S2[搜索服务]')
    lines.append('        S3[文档处理]')
    lines.append('        S4[配置服务]')
    lines.append('        S5[安全审计]')
    lines.append('    end')
    lines.append('')
    lines.append('    subgraph 基础设施层')
    lines.append('        I1[配置存储]')
    lines.append('        I2[日志监控]')
    lines.append('        I3[数据存储]')
    lines.append('    end')
    lines.append('')
    # 添加依赖关系（动态生成，不硬编码）
    for e in entities[:8]:
        node_id = e.technical_name.replace('-', '_').replace(' ', '_')[:20]
        lines.append(f'    {node_id} --> S1')
        lines.append(f'    {node_id} --> S4')
    lines.append('    U1 --> S1')
    lines.append('    U2 --> S4')
    lines.append('    S1 --> I1')
    lines.append('    S4 --> I1')
    lines.append('```')
    lines.append('')

    # 架构分层说明（动态生成，不硬编码模块名）
    lines.append('### 1.2 架构分层说明')
    lines.append('')
    lines.append('| 层级 | 模块 | 职责 |')
    lines.append('|------|------|------|')
    top_entity_names = '、'.join(e.name for e in entities[:6]) if entities else '业务模块'
    layers = [
        ('用户交互层', 'Web端 / GUI端 / API', '提供用户界面，接收用户输入，展示分析结果'),
        ('业务层', top_entity_names, '承载核心业务逻辑，执行具体任务'),
        ('共享服务层', 'LLM客户端 / 搜索服务 / 文档处理 / 配置服务', '提供通用能力，被多个业务模块复用'),
        ('基础设施层', '配置存储 / 日志监控 / 数据存储', '提供底层支撑，保障系统稳定运行'),
    ]
    for layer, mods, duty in layers:
        lines.append(f'| **{layer}** | {mods} | {duty} |')
    lines.append('')


def _render_ba_entity_flows(lines: List[str], entity: BusinessEntity, entity_wfs: List[BusinessWorkflow]) -> None:
    """渲染单个实体的流程图（最多 2 个 Mermaid 流程）"""
    for wf in entity_wfs[:2]:
        lines.append(f'**业务流程：{wf.name}**')
        lines.append('')
        lines.append('```mermaid')
        lines.append('flowchart LR')
        # 生成流程图节点
        for i, step in enumerate(wf.steps[:8]):
            step_clean = step.replace('"', "'")[:30]
            lines.append(f'    S{i}["{step_clean}"]')
        # 连接箭头
        for i in range(len(wf.steps[:8]) - 1):
            lines.append(f'    S{i} --> S{i+1}')
        lines.append('```')
        lines.append('')
        lines.append(f"- **触发条件**：{wf.trigger or '用户发起请求'}")
        lines.append(f"- **产出物**：{wf.output or '业务结果'}")
        if wf.roles_involved:
            lines.append(f"- **参与角色**：{'、'.join(wf.roles_involved)}")
        lines.append('')


def _render_ba_modules(lines: List[str], result: BusinessAnalysisResult,
                       entities: List[BusinessEntity]) -> None:
    """渲染第二章：业务模块详解（每个实体一张定位表 + 流程图）"""
    _ba_append_h2(lines, '二、业务模块详解')

    for idx, entity in enumerate(entities, 1):
        entity_wfs = [wf for wf in result.workflows if wf.owner == entity.name or wf.owner == entity.technical_name]

        _ba_append_h3(lines, f'{idx}. {entity.name} `{entity.technical_name}`')

        # 功能定位表格
        lines.append('| 属性 | 内容 |')
        lines.append('|------|------|')
        lines.append(f'| **业务定位** | {entity.purpose or "核心业务组件"} |')
        caps = '、'.join(entity.capabilities[:5]) if entity.capabilities else '-'
        lines.append(f'| **核心能力** | {caps} |')
        classes = '、'.join(entity.core_classes[:5]) if entity.core_classes else '-'
        lines.append(f'| **关键类** | {classes} |')
        files = f'{len(entity.files)} 个文件' if entity.files else '-'
        lines.append(f'| **代码规模** | {files} |')
        lines.append('')

        # 如果有工作流，展示流程图
        _render_ba_entity_flows(lines, entity, entity_wfs)


def _render_ba_hierarchy_nodes(lines: List[str], result: BusinessAnalysisResult) -> None:
    """渲染 3.1：层级总览（主轴 → 子层级的文字/表格形式）"""
    lines.append('### 3.1 业务层级总览')
    lines.append('')
    lines.append('> 按 **主轴 → 一级子流程 → 二级子流程 → 角色步骤** 的层级组织，模拟 AI 直读代码的思考方式。')
    lines.append('')

    # 构建层级树文本
    for node in result.hierarchy:
        spine_marker = ' 🔵 **主轴**' if node.is_spine else ''
        lines.append(f'### 3.{result.hierarchy.index(node)+2} {node.name}{spine_marker}')
        lines.append('')
        if node.description:
            lines.append(f'{node.description}')
            lines.append('')

        # 步骤表格
        if node.steps:
            lines.append('| 步骤 | 说明 |')
            lines.append('|------|------|')
            for i, step in enumerate(node.steps):
                lines.append(f'| {i+1} | {step[:100]} |')
            lines.append('')

        if node.roles_involved:
            roles_str = ' → '.join(node.roles_involved[:8])
            lines.append(f'**涉及角色**：{roles_str}')
            lines.append('')

        if node.trigger:
            lines.append(f'**触发条件**：{node.trigger}')
            lines.append('')
        if node.output:
            lines.append(f'**产出物**：{node.output}')
            lines.append('')

        # 递归输出子层级
        if node.children:
            _render_hier_children(lines, node.children, indent_level=1)

    # 3.2 层级化 Mermaid 流程图
    lines.append('### 3.2 层级化业务流程')
    lines.append('')
    lines.append('```mermaid')
    lines.append('flowchart TB')
    lines.append('    Start([用户输入])')

    node_counter = [0]
    for node in result.hierarchy:
        _render_hier_mermaid(lines, node, "Start", node_counter)

    lines.append('```')
    lines.append('')


def _render_ba_flows(lines: List[str], result: BusinessAnalysisResult) -> None:
    """渲染第三章：核心业务流程（层级化 → 平面降级 → 空占位）"""
    _ba_append_h2(lines, '三、核心业务流程（按层级捋清楚）')

    if result.hierarchy:
        _render_ba_hierarchy_nodes(lines, result)
    elif result.workflows:
        # 降级：平面输出
        lines.append('### 3.1 流程总览')
        lines.append('')
        lines.append('| 流程名称 | 所属模块 | 步骤数 | 触发条件 | 产出物 |')
        lines.append('|----------|----------|--------|----------|--------|')
        for wf in result.workflows:
            steps = len(wf.steps)
            trigger = wf.trigger[:30] + '...' if len(wf.trigger) > 30 else wf.trigger
            output = wf.output[:30] + '...' if len(wf.output) > 30 else wf.output
            lines.append(f'| {wf.name} | {wf.owner} | {steps} | {trigger or "-"} | {output or "-"} |')
        lines.append('')
    else:
        lines.append('（未识别到明确的业务流程）')
        lines.append('')


def _render_ba_roles(lines: List[str], result: BusinessAnalysisResult,
                     entities: List[BusinessEntity]) -> None:
    """渲染第四章：用户角色与权限（角色定义表 + 角色-功能矩阵）"""
    _ba_append_h2(lines, '四、用户角色与权限')

    if result.roles:
        lines.append('### 4.1 角色定义')
        lines.append('')
        lines.append('| 角色 | 描述 | 权限级别 | 可访问功能 | 界面 |')
        lines.append('|------|------|----------|------------|------|')
        for r in result.roles:
            features = '、'.join(r.accessible_features[:4]) if r.accessible_features else '-'
            lines.append(f'| **{r.name}** | {r.description[:40]} | {r.access_level} | {features} | {r.ui_mode or "-"} |')
        lines.append('')

        # 角色-功能矩阵
        if len(result.roles) >= 2 and entities:
            lines.append('### 4.2 角色-功能权限矩阵')
            lines.append('')
            lines.append('| 功能 / 角色 | ' + ' | '.join(r.name for r in result.roles) + ' |')
            lines.append('|' + '|'.join(['---'] * (len(result.roles) + 1)) + '|')

            # 为每个实体生成权限行
            for e in entities[:8]:
                row = [e.name]
                for r in result.roles:
                    # 简单启发：如果角色可访问功能中包含实体名，或高权限角色可以访问所有
                    has_access = any(e.name in f or e.technical_name in f for f in r.accessible_features)
                    if r.access_level == '高' or has_access:
                        row.append('✅')
                    else:
                        row.append('❌')
                lines.append('| ' + ' | '.join(row) + ' |')
            lines.append('')
    else:
        lines.append('（未识别到多角色设计）')
        lines.append('')


def _render_ba_call_relations(lines: List[str], mod_calls) -> None:
    """渲染 5.1：模块级调用关系矩阵（TOP 15）"""
    lines.append('### 5.1 调用关系矩阵')
    lines.append('')

    # 展示 TOP 调用关系
    if mod_calls:
        lines.append('| 调用方 | 被调用方 | 调用次数 | 关系类型 |')
        lines.append('|--------|----------|----------|----------|')
        relations = []
        for caller_mod, callees in mod_calls.items():
            for cal_mod, count in callees.items():
                rel_type = '服务依赖' if any(s in cal_mod.lower() for s in ['shared', 'config', 'common']) else '业务协作'
                relations.append((caller_mod, cal_mod, count, rel_type))

        relations.sort(key=lambda x: -x[2])
        for caller, callee, count, rel_type in relations[:15]:
            lines.append(f'| `{caller}` | `{callee}` | {count} | {rel_type} |')
        lines.append('')


def _render_ba_dependencies(lines: List[str], result: BusinessAnalysisResult) -> None:
    """渲染第五章：模块间依赖关系（调用矩阵 + 入口点分布）"""
    _ba_append_h2(lines, '五、模块间依赖关系')

    if result.enrichment and result.enrichment.available:
        callee = result.enrichment.callee_index

        # 提取模块级调用关系
        mod_calls = defaultdict(lambda: defaultdict(int))
        entry_mods = defaultdict(int)

        for ep in result.enrichment.entry_points:
            parts = ep.replace('\\', '/').split('/')
            if parts:
                entry_mods[parts[0]] += 1

        if callee:
            for caller, callees in list(callee.items())[:50]:
                caller_mod = caller.split('.')[0] if '.' in caller else caller.split('/')[0] if '/' in caller else caller
                for cal in callees[:10]:
                    cal_mod = cal.split('.')[0] if '.' in cal else cal.split('/')[0] if '/' in cal else cal
                    if caller_mod != cal_mod:
                        mod_calls[caller_mod][cal_mod] += 1

        _render_ba_call_relations(lines, mod_calls)

        # 入口点分布
        if entry_mods:
            lines.append('### 5.2 系统入口点分布')
            lines.append('')
            lines.append('| 模块 | 入口点数量 | 说明 |')
            lines.append('|------|------------|------|')
            for mod, count in sorted(entry_mods.items(), key=lambda x: -x[1])[:8]:
                lines.append(f'| `{mod}` | {count} | 主要业务入口 |')
            lines.append('')
    else:
        lines.append('（未启用 GitNexus 增强，无法提供精确的调用关系分析）')
        lines.append('')


def _render_ba_differences(lines: List[str], result: BusinessAnalysisResult) -> None:
    """渲染第六章：跨端/跨角色差异"""
    _ba_append_h2(lines, '六、跨端与角色差异')

    if result.differences:
        for d in result.differences:
            _ba_append_h3(lines, f'{d.dimension} — {d.aspect}')
            lines.append(f'| | {d.side_a} | {d.side_b} |')
            lines.append('|------|------|------|')
            lines.append(f'| **差异** | {d.difference[:50]} | - |')
            lines.append(f'| **影响** | {d.impact[:50] if d.impact else "-"} | - |')
            lines.append('')
    else:
        lines.append('（未识别到显著的跨端或角色差异）')
        lines.append('')


def _ba_overall_grade(result: BusinessAnalysisResult, entities: List[BusinessEntity]) -> Tuple[int, str]:
    """计算报告总体评分与等级（与原实现公式一致）"""
    score = 70
    if entities and len(entities) >= 3: score += 10
    if result.workflows and len(result.workflows) >= 2: score += 5
    if result.roles and len(result.roles) >= 2: score += 5
    if result.enrichment and result.enrichment.available: score += 5
    if result.evaluation_scores:
        avg_eval = sum(result.evaluation_scores) / len(result.evaluation_scores)
        score = int(score * 0.6 + avg_eval * 100 * 0.4)
    score = max(50, min(95, score))
    grade = '优秀' if score >= 85 else '良好' if score >= 70 else '一般' if score >= 60 else '待改进'
    return score, grade


def _render_ba_findings(lines: List[str], result: BusinessAnalysisResult,
                        entities: List[BusinessEntity]) -> None:
    """渲染第七章：关键发现与建议（评分 + 架构优势 + 改进建议）"""
    _ba_append_h2(lines, '七、关键发现与建议')

    # 计算评分
    score, grade = _ba_overall_grade(result, entities)

    lines.append(f'**总体评分：{score}/100 ({grade})**')
    lines.append('')

    lines.append('### 7.1 架构优势')
    lines.append('')
    if len(entities) >= 3:
        lines.append(f'1. **模块化设计**：系统划分为 {len(entities)} 个明确的功能模块，职责清晰')
    if result.workflows:
        lines.append(f'2. **业务流程闭环**：定义了 {len(result.workflows)} 条核心业务流程')
    if result.enrichment and result.enrichment.available:
        lines.append(f'3. **调用链清晰**：GitNexus 索引显示 {len(result.enrichment.call_pairs)} 条调用关系，架构呈有向无环特征')
    lines.append('')

    lines.append('### 7.2 改进建议')
    lines.append('')
    avg_line = result.line_count / max(result.file_count, 1)
    if avg_line > 300:
        lines.append(f'1. **文件拆分**：平均每文件 {avg_line:.0f} 行，建议拆分超过 500 行的大文件')
    if not result.enrichment or not result.enrichment.available:
        lines.append('2. **启用 GitNexus**：获取精确的调用链和依赖关系分析')
    lines.append('3. **自动化测试**：为各引擎核心逻辑编写单元测试，使用 MockLLMClient 避免 LLM 调用成本')
    lines.append('')

    lines.append('---')
    lines.append(f'*报告由 CodeRef-AI BusinessAnalyzer V2.1 生成 · 迭代 {result.iteration_count} 轮 · GitNexus 增强 {"启用" if result.enrichment and result.enrichment.available else "未启用"}*')


def _render_hier_children(lines: List[str], children: List[WorkflowHierarchy], indent_level: int = 1) -> None:
    """递归渲染层级子节点"""
    indent = '  ' * indent_level
    sub_prefix = '├── ' if indent_level == 1 else '└── '
    for child in children:
        spine_marker = ' 🔵' if child.is_spine else ''
        lines.append(f'{indent}{sub_prefix}**{child.name}**{spine_marker}')
        if child.description:
            lines.append(f'{indent}    {child.description[:120]}')
        if child.steps:
            roles_inline = ' → '.join(child.roles_involved[:5]) if child.roles_involved else ''
            steps_str = ' → '.join(s[:50] for s in child.steps[:5])
            lines.append(f'{indent}    步骤: {steps_str}')
            if roles_inline:
                lines.append(f'{indent}    角色: {roles_inline}')
        if child.children:
            _render_hier_children(lines, child.children, indent_level + 1)
        lines.append('')


def _render_hier_mermaid(lines: List[str], node: WorkflowHierarchy, parent_id: str, counter: List[int]) -> None:
    """渲染层级 Mermaid 节点"""
    counter[0] += 1
    node_id = f'N{counter[0]}'
    node_label = node.name[:25]
    spine_badge = ' [主轴]' if node.is_spine else ''
    lines.append(f'    {node_id}["{node_label}{spine_badge}"]')
    lines.append(f'    {parent_id} --> {node_id}')

    for child in node.children:
        _render_hier_mermaid(lines, child, node_id, counter)


def _ba_call_summary_parts(callee_index) -> List[str]:
    """从 GitNexus callee_index 提取调用 TOP15 与跨模块调用 TOP10 摘要行"""
    parts = []
    # GitNexus 调用链 TOP 引用
    call_count = {}
    for caller, callees in callee_index.items():
        for callee in callees:
            call_count[callee] = call_count.get(callee, 0) + 1
    sorted_calls = sorted(call_count.items(), key=lambda x: -x[1])[:15]
    for name, count in sorted_calls:
        parts.append(f"- {name}: 被调用 {count} 次")

    # 跨模块依赖
    file_calls = {}
    for caller, callees in callee_index.items():
        for callee in callees:
            caller_mod = caller.split('.')[0] if '.' in caller else caller
            callee_mod = callee.split('.')[0] if '.' in callee else callee
            if caller_mod != callee_mod:
                key = f"{caller_mod} → {callee_mod}"
                file_calls[key] = file_calls.get(key, 0) + 1
    if file_calls:
        parts.append("\n【跨模块调用 Top 10】")
        sorted_cross = sorted(file_calls.items(), key=lambda x: -x[1])[:10]
        for dep, count in sorted_cross:
            parts.append(f"- {dep}: {count} 次")
    return parts


def _ba_build_report_data_summary(result: BusinessAnalysisResult) -> str:
    """构建紧凑的数据摘要，供 LLM 润色报告时参考"""
    parts = []

    # 实体数据
    if result.entities:
        parts.append("【业务模块】")
        for e in result.entities[:10]:
            caps = '; '.join(e.capabilities[:4]) if e.capabilities else '无'
            classes = ', '.join(e.core_classes[:4]) if e.core_classes else '无'
            files_count = len(e.files)
            parts.append(f"- {e.name}({e.technical_name}): {e.purpose} | 能力: {caps} | 关键类: {classes} | 文件数: {files_count}")

    # 工作流
    if result.workflows:
        parts.append("\n【业务流程】")
        for w in result.workflows[:8]:
            steps = ' → '.join(w.steps[:4])
            parts.append(f"- {w.name}: {w.trigger} → {steps} → {w.output} | 角色: {', '.join(w.roles_involved)}")

    # 差异
    if result.differences:
        parts.append("\n【跨端/跨角色差异】")
        for d in result.differences[:6]:
            parts.append(f"- {d.dimension}/{d.aspect}: {d.side_a} vs {d.side_b} → {d.difference}")

    # GitNexus 调用链 TOP 引用
    if result.enrichment and result.enrichment.available and result.enrichment.callee_index:
        parts.append("\n【GitNexus 调用链 Top 15（被调用最多的符号）】")
        parts.extend(_ba_call_summary_parts(result.enrichment.callee_index))

    parts.append(f"\n【总数据】{result.file_count}文件, {result.line_count}行, {result.total_classes}类, {result.total_functions}函数, {len(result.entities)}模块, {len(result.workflows)}流程")

    return '\n'.join(parts)
