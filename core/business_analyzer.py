# -*- coding: utf-8 -*-
"""
通用业务分析引擎
==================
解决传统代码分析报告「只有技术细节、缺乏业务理解」的痛点。

核心设计理念:
1. 通用性 —— 不硬编码任何项目特定知识，对任意代码库都能动态发现业务概念
2. 自学习 —— 每次分析完成后自我评估，发现不足后自动优化分析方案再跑一遍
3. 多层级 —— 从文件结构 → 技术架构 → 业务能力 → 用户角色 → 业务流程 → 跨端差异，
           逐层深入到非程序员也能看懂的业务全景图

工作管线 (Pipeline):
  Stage 0: 结构扫描  (依赖现有 CodeAnalyzer)
  Stage 1: 业务概念发现 (LLM 从类/函数/docstring 推断业务名词)
  Stage 2: 角色与工作流发现 (LLM 分析调用链和模式，提取业务流)
  Stage 3: 跨端与角色差异分析 (LLM 对比 Web/GUI 差异、角色权限差异)
  Stage 4: 自评估 (检查输出质量)
  Stage 5: 自改进 (如果质量不足，修正分析策略后重跑 Stage 1-4)
  Stage 6: 报告生成 (产出非程序员可读的业务全景报告)
"""

import os
import re
import json
import datetime
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, field
from collections import defaultdict
from loguru import logger
from core.shared_filter import SharedFilter
from core.gitnexus_client import GitNexusEnrichment
from core.arch_detector import detect_architecture
from core.code_knowledge_base import CodeKnowledgeBase, LLMAnalyzer as KbLLMAnalyzer
from core.prompt_extractor import PromptExtractor, PromptExtractionResult
from core.prompt_analyzer import PromptAnalyzer, PromptAnalysisResult


# ========================================================================
#  God Module Split 第 2 批：业务分析引擎按职责前缀拆为 4 个子模块，
#  本文件为「门面 + 兼容层」：保留 BusinessAnalyzer 类 / analyze_project_business
#  入口，并全量 re-export 子模块符号，保证 `from core.business_analyzer import X`
#  的既有 import 兼容（符号面只增不减）。
#  依赖拓扑（单向、无环）：
#    ba_models.py ← ba_discovery.py / ba_eval.py / ba_report.py ← business_analyzer.py
# ========================================================================

from core.ba_models import (
    AnalysisEvaluation, BusinessAnalysisResult, BusinessEntity, BusinessWorkflow,
    CrossCuttingDifference, UserRole, WorkflowHierarchy,
)
from core.ba_discovery import (
    ENTRY_DEGREE_RATIO, INFRA_DEGREE_RATIO, INFRA_MIN_DEGREE, _ROLE_CLUE_KEYWORDS,
    _ba_apply_entity_symbols, _ba_build_compact_overview, _ba_build_hierarchy_from_callgraph,
    _ba_call_llm, _ba_collect_entity_symbols, _ba_collect_nodes_to_enrich, _ba_collect_role_clues,
    _ba_collect_ui_clues, _ba_detect_arch_profile, _ba_dir_to_business_name,
    _ba_docstring_methods_of_file, _ba_docstring_workflows, _ba_enrich_entities_with_prompt_roles,
    _ba_enrich_entities_with_symbols, _ba_enrich_hierarchy_with_llm, _ba_extract_code_sample,
    _ba_extract_flow_clues, _ba_find_role_config, _ba_find_ui_clues, _ba_flow_clues_of_file,
    _ba_heuristic_difference_discovery, _ba_heuristic_entity_discovery, _ba_heuristic_role_discovery,
    _ba_heuristic_workflow_discovery, _ba_infer_capabilities_from_names, _ba_kb_workflows,
    _ba_llm_difference_discovery, _ba_llm_entity_discovery, _ba_llm_hierarchical_workflows,
    _ba_llm_role_discovery, _ba_llm_workflow_discovery, _ba_merge_entities, _ba_module_from_path,
    _ba_module_roles_from_prompt, _ba_overview_file_block, _ba_parse_hierarchy,
    _ba_parse_llm_differences, _ba_parse_llm_entities, _ba_parse_llm_roles,
    _ba_parse_llm_workflows, _ba_role_clues_of_file, _ba_roles_from_prompt_analysis,
    _ba_rule_hierarchical_workflows, _ba_rule_module_node, _ba_rule_spine_modules,
    _ba_sanitize_memory, _ba_summarize_signals, _ba_workflows_from_prompt_analysis,
    _hier_arch_signaled, _hier_attach_level2, _hier_attach_uncovered_infra,
    _hier_classify_modules, _hier_edges_from_call_pairs, _hier_edges_from_callee_index,
    _hier_edges_from_caller_index, _hier_entry_modules, _hier_filter_modules,
    _hier_func_maps, _hier_infra_modules, _hier_make_level1_child, _hier_make_spine_node,
    _hier_module_graph, _hier_pick_spine, _hier_sorted_level1,
)
from core.ba_eval import (
    _ba_evaluate_quality, _ba_generate_evaluation_suggestions, _ba_generate_improvement,
    _ba_init_result, _ba_result_summaries, _ba_snapshot_result, _eval_diff_quality,
    _eval_entity_quality, _eval_role_quality, _eval_workflow_quality,
)
from core.ba_report import (
    _ba_append_h2, _ba_append_h3, _ba_build_report_data_summary, _ba_call_summary_parts,
    _ba_filter_business_entities, _ba_overall_grade, _render_ba_architecture,
    _render_ba_call_relations, _render_ba_dependencies, _render_ba_differences,
    _render_ba_entity_flows, _render_ba_findings, _render_ba_flows, _render_ba_header,
    _render_ba_hierarchy_nodes, _render_ba_modules, _render_ba_roles, _render_hier_children,
    _render_hier_mermaid,
)


# 公开符号面声明：完整覆盖拆解前的模块命名空间（含顶部 import 名），
# 供 `from core.business_analyzer import *` 兼容，并使 pyflakes 将上述
# re-export / 兼容 import 视为已使用。
__all__ = [
    # 数据模型
    'AnalysisEvaluation', 'BusinessAnalysisResult', 'BusinessEntity', 'BusinessWorkflow',
    'CrossCuttingDifference', 'UserRole', 'WorkflowHierarchy',
    # 门面类与入口
    'BusinessAnalyzer', 'analyze_project_business',
    # 常量
    'ENTRY_DEGREE_RATIO', 'INFRA_DEGREE_RATIO', 'INFRA_MIN_DEGREE', '_ROLE_CLUE_KEYWORDS',
    # 发现层（ba_discovery）
    '_ba_apply_entity_symbols', '_ba_build_compact_overview', '_ba_build_hierarchy_from_callgraph',
    '_ba_call_llm', '_ba_collect_entity_symbols', '_ba_collect_nodes_to_enrich',
    '_ba_collect_role_clues', '_ba_collect_ui_clues', '_ba_detect_arch_profile',
    '_ba_dir_to_business_name', '_ba_docstring_methods_of_file', '_ba_docstring_workflows',
    '_ba_enrich_entities_with_prompt_roles', '_ba_enrich_entities_with_symbols',
    '_ba_enrich_hierarchy_with_llm', '_ba_extract_code_sample', '_ba_extract_flow_clues',
    '_ba_find_role_config', '_ba_find_ui_clues', '_ba_flow_clues_of_file',
    '_ba_heuristic_difference_discovery', '_ba_heuristic_entity_discovery',
    '_ba_heuristic_role_discovery', '_ba_heuristic_workflow_discovery',
    '_ba_infer_capabilities_from_names', '_ba_kb_workflows', '_ba_llm_difference_discovery',
    '_ba_llm_entity_discovery', '_ba_llm_hierarchical_workflows', '_ba_llm_role_discovery',
    '_ba_llm_workflow_discovery', '_ba_merge_entities', '_ba_module_from_path',
    '_ba_module_roles_from_prompt', '_ba_overview_file_block', '_ba_parse_hierarchy',
    '_ba_parse_llm_differences', '_ba_parse_llm_entities', '_ba_parse_llm_roles',
    '_ba_parse_llm_workflows', '_ba_role_clues_of_file', '_ba_roles_from_prompt_analysis',
    '_ba_rule_hierarchical_workflows', '_ba_rule_module_node', '_ba_rule_spine_modules',
    '_ba_sanitize_memory', '_ba_summarize_signals', '_ba_workflows_from_prompt_analysis',
    '_hier_arch_signaled', '_hier_attach_level2', '_hier_attach_uncovered_infra',
    '_hier_classify_modules', '_hier_edges_from_call_pairs', '_hier_edges_from_callee_index',
    '_hier_edges_from_caller_index', '_hier_entry_modules', '_hier_filter_modules',
    '_hier_func_maps', '_hier_infra_modules', '_hier_make_level1_child', '_hier_make_spine_node',
    '_hier_module_graph', '_hier_pick_spine', '_hier_sorted_level1',
    # 评估/自改进层（ba_eval）
    '_ba_evaluate_quality', '_ba_generate_evaluation_suggestions', '_ba_generate_improvement',
    '_ba_init_result', '_ba_result_summaries', '_ba_snapshot_result', '_eval_diff_quality',
    '_eval_entity_quality', '_eval_role_quality', '_eval_workflow_quality',
    # 报告渲染层（ba_report）
    '_ba_append_h2', '_ba_append_h3', '_ba_build_report_data_summary', '_ba_call_summary_parts',
    '_ba_filter_business_entities', '_ba_overall_grade', '_render_ba_architecture',
    '_render_ba_call_relations', '_render_ba_dependencies', '_render_ba_differences',
    '_render_ba_entity_flows', '_render_ba_findings', '_render_ba_flows', '_render_ba_header',
    '_render_ba_hierarchy_nodes', '_render_ba_modules', '_render_ba_roles',
    '_render_hier_children', '_render_hier_mermaid',
    # 顶部 import 名（兼容既有符号面）
    'CodeKnowledgeBase', 'Dict', 'GitNexusEnrichment', 'KbLLMAnalyzer', 'List', 'Optional',
    'PromptAnalysisResult', 'PromptAnalyzer', 'PromptExtractionResult', 'PromptExtractor',
    'SharedFilter', 'Tuple', 'dataclass', 'datetime', 'defaultdict', 'detect_architecture',
    'field', 'json', 'logger', 'os', 're',
]


# ========================================================================
#  业务分析引擎
# ========================================================================

class BusinessAnalyzer:
    """
    通用业务分析引擎
    
    用法:
        from core.business_analyzer import BusinessAnalyzer
        analyzer = BusinessAnalyzer(llm_client)
        result = analyzer.analyze(project_analysis)
        print(result.to_business_report())
    """
    
    # 自评估的检查项
    EVALUATION_CRITERIA = [
        "是否描述了项目或系统在做什么",
        "是否识别出了不同的用户角色",
        "是否描述了核心业务流程",
        "是否包含跨平台/跨端差异",
        "语言是否通俗易懂，非程序员能理解",
        "是否有业务实体之间的关系描述",
    ]
    
    def __init__(self, llm_client=None, gitnexus_client=None, knowledge_base=None):
        self.llm = llm_client         # LLMIntegration 实例 (可选)
        self.gitnexus = gitnexus_client  # GitNexusMCPClient 实例 (可选)
        self.kb = knowledge_base      # CodeKnowledgeBase 实例 (可选，LLM 驱动分析的核心)
        self._kb_analyzer = None      # LLMAnalyzer 实例 (延迟初始化)
        self._learning_history = []    # 自学习历史
        self._prompt_memory = ""       # 自学习中累积的「硬编码 Prompt」
        self._prompt_analysis = None   # PromptAnalysisResult（缓存，避免重复分析）
        self._arch_profile = None      # 架构画像（静态探测结果，缓存复用）
        self._project_path = None      # 项目根路径（用于把绝对路径转模块名）

    @staticmethod
    def _sanitize_prompt_memory(memory: str, limit: int=1000) -> str:
        """把自学习记忆当作「不可信数据」安全注入 prompt，防提示注入。"""
        return _ba_sanitize_memory(memory, limit)

    def _get_kb_analyzer(self):
        """延迟初始化知识库分析器"""
        if self._kb_analyzer is None and self.kb is not None:
            self._kb_analyzer = KbLLMAnalyzer(self.kb, llm_client=self.llm)
        return self._kb_analyzer

    def _gitnexus_enrich(self, project_path: str) -> Optional['GitNexusEnrichment']:
        """Stage 0: GitNexus 增强 — 自动获取符号索引 + 调用图"""
        try:
            from core.gitnexus_client import GitNexusMCPClient
            if not GitNexusMCPClient.is_cli_available():
                logger.info("[BusinessAnalyzer] GitNexus CLI 不可用，跳过增强")
                return None
            with GitNexusMCPClient(project_path=project_path) as client:
                enrichment = client.enrich_project(project_path)
                if enrichment.available:
                    logger.info(f"[BusinessAnalyzer] GitNexus 增强可用: "
                               f"{len(enrichment.all_symbols)} 符号, "
                               f"{len(enrichment.call_pairs)} 调用关系, "
                               f"{len(enrichment.entry_points)} 入口点")
                return enrichment if enrichment.available else None
        except Exception as e:
            logger.warning(f"[BusinessAnalyzer] GitNexus 增强失败(跳过): {e}")
            return None

    def _prompt_enrich(self, project_path: str) -> Optional[PromptAnalysisResult]:
        """
        Stage 0.5: Prompt 抽取 + 角色分析
        
        从代码中的 Prompt 模板提取角色定义和工作流信息。
        这是连接"代码结构分析"和"业务语义理解"的关键桥梁。
        
        核心思路：
        - Prompt 是 Agent 的 DNA——system prompt 定义了角色行为
        - 通过抽取和分析 prompt，可以：
          1. 识别业务流程中的角色（如小验→小张→老商→小调）
          2. 提取角色使用的方法论/框架
          3. 重建端到端的工作流
        """
        try:
            # Step 1: 抽取 Prompt 模板
            extractor = PromptExtractor()
            prompt_result = extractor.extract_from_project(project_path)
            
            if prompt_result.total_prompts_found == 0:
                logger.info("[BusinessAnalyzer] 未发现 Prompt 模板，跳过 Prompt 增强")
                return None
            
            logger.info(f"[BusinessAnalyzer] Prompt 抽取完成: {prompt_result.total_prompts_found} 个 Prompt, "
                       f"角色模式: {prompt_result.role_patterns}")
            
            # Step 2: 分析 Prompt（提取角色 + 工作流）
            analyzer = PromptAnalyzer(llm_client=self.llm)
            analysis = analyzer.analyze(prompt_result)
            
            logger.info(f"[BusinessAnalyzer] Prompt 分析完成: {analysis.total_roles} 角色, "
                       f"{analysis.total_workflows} 工作流")
            
            return analysis
        except Exception as e:
            logger.warning(f"[BusinessAnalyzer] Prompt 增强失败(跳过): {e}")
            return None

    def analyze(self, project_analysis, max_iterations=3) -> BusinessAnalysisResult:
        """全量业务分析入口（多轮自学习迭代，取最优结果）

        Args:
            project_analysis: ProjectAnalysis 对象 (来自 CodeAnalyzer)
            max_iterations: 最多自学习迭代次数

        Returns:
            BusinessAnalysisResult
        """
        # 加载项目专属的 cache 硬编码优化（白名单）
        SharedFilter.load_cache(project_analysis.project_path)

        result = _ba_init_result(project_analysis)

        # Stage 0: GitNexus 增强
        enrichment = self._gitnexus_enrich(project_analysis.project_path)
        result.enrichment = enrichment

        # Stage 0.25: 架构探测（独立于调用图的静态证据源，用于增强入口发现）
        self._project_path = project_analysis.project_path
        self._arch_profile = _ba_detect_arch_profile(project_analysis.files)

        # Stage 0.5: Prompt 抽取 + 角色分析（核心创新：从 Prompt 中提取角色和流程）
        self._prompt_analysis = self._prompt_enrich(project_analysis.project_path)

        iteration = 0
        best_result = None
        best_score = 0.0

        while iteration < max_iterations:
            iteration += 1
            result.iteration_count = iteration
            logger.info(f"[BusinessAnalyzer] 第 {iteration} 轮分析开始")

            # --- Stage 1: 业务概念发现 ---
            entities = self._discover_business_entities(project_analysis, enrichment=enrichment)

            # --- Stage 2: 角色与工作流发现（传入累积学习记忆）---
            roles = self._discover_roles(project_analysis, entities)
            workflows = self._discover_workflows(project_analysis, entities, roles, enrichment=enrichment)

            # --- Stage 2.5: 层级化组织（找到主轴，按层级捋清楚）---
            hierarchy = self._organize_workflows_hierarchically(workflows, entities, roles, enrichment=enrichment)

            # --- Stage 3: 跨端与角色差异分析 ---
            differences = self._discover_cross_cutting_differences(
                project_analysis, entities, roles, enrichment=enrichment
            )

            # 更新结果
            result.entities = entities or []
            result.roles = roles or []
            result.workflows = workflows or []
            result.hierarchy = hierarchy or []
            result.differences = differences or []

            # --- Stage 4: 自评估 ---
            evaluation = self._evaluate_quality(result)
            result.evaluation_scores.append(evaluation.score)
            logger.info(f"[BusinessAnalyzer] 第 {iteration} 轮评估得分: {evaluation.score:.2f}")

            if evaluation.score > best_score:
                best_score = evaluation.score
                best_result = _ba_snapshot_result(result, iteration)

            # 检查是否通过
            if evaluation.passed:
                logger.info(f"[BusinessAnalyzer] 第 {iteration} 轮通过评估，结束")
                break

            # --- Stage 5: 自改进（核心：LLM先分析、再硬编码更好的Prompt）---
            if iteration < max_iterations:
                self._prompt_memory = self._generate_improvement(result, evaluation)
                result.improvement_history.append(self._prompt_memory)
                self._learning_history.append({
                    "iteration": iteration,
                    "score": evaluation.score,
                    "improvement": self._prompt_memory,
                    "issues": evaluation.issues,
                })
                logger.info(f"[BusinessAnalyzer] 自改进: 已生成第{iteration+1}轮用的硬编码Prompt "
                            f"(len={len(self._prompt_memory)} chars)")

        # 返回最优结果
        if best_result:
            best_result.iteration_count = iteration
            best_result.improvement_history = result.improvement_history
            best_result.evaluation_scores = result.evaluation_scores
            return best_result

        return result
    
    # ====================================================================
    #  Stage 1: 业务概念发现
    # ====================================================================
    
    def _discover_business_entities(self, analysis, enrichment=None) -> List[BusinessEntity]:
        """从代码结构中发现业务实体（工具/模块/子系统）"""
        # 先尝试用无 LLM 的方式推断（保证即使无 LLM 也能给基本输出）
        entities = self._heuristic_entity_discovery(analysis)
        
        # GitNexus 增强：为实体补充符号级信息
        if enrichment and enrichment.available:
            entities = self._enrich_entities_with_symbols(entities, enrichment, analysis)
        
        # 如果有 LLM，再用 LLM 深度增强
        if self.llm and hasattr(self.llm, 'chat_completion'):
            llm_entities = self._llm_entity_discovery(analysis, enrichment=enrichment)
            if llm_entities:
                # 合并：以 LLM 发现为主
                entities = self._merge_entities(entities, llm_entities)
        
        return entities

    def _enrich_entities_with_symbols(self, entities, enrichment, analysis) -> List[BusinessEntity]:
        """用 GitNexus 符号数据增强实体（补充核心类/函数）。"""
        return _ba_enrich_entities_with_symbols(entities, enrichment, analysis, self.llm)
    
    def _heuristic_entity_discovery(self, analysis) -> List[BusinessEntity]:
        """启发式实体发现（目录/命名结构推断，Prompt 角色优先）。"""
        return _ba_heuristic_entity_discovery(analysis, self.llm, self._prompt_analysis)

    def _extract_code_sample(self, cf) -> str:
        """抽取代码样本（类/函数签名片段，供 LLM 分析）。"""
        return _ba_extract_code_sample(cf)

    def _enrich_entities_with_prompt_roles(self, entities: List[BusinessEntity], pa: PromptAnalysisResult) -> List[BusinessEntity]:
        """用 Prompt 角色分析结果增强实体能力描述。"""
        return _ba_enrich_entities_with_prompt_roles(entities, pa)
    
    def _dir_to_business_name(self, dir_name: str, code_context: str='') -> str:
        """把目录名翻译成通俗的业务名称。"""
        return _ba_dir_to_business_name(self.llm, dir_name, code_context)
    
    def _infer_capabilities_from_names(self, names: List[str], code_context: str='') -> List[str]:
        """从类/函数命名推断业务能力描述。"""
        return _ba_infer_capabilities_from_names(self.llm, names, code_context)
    
    def _summarize_signals(self, signals: List[str], dir_name: str, code_context: str='') -> str:
        """汇总目录结构信号为一句业务描述。"""
        return _ba_summarize_signals(self.llm, signals, dir_name, code_context)
    
    def _llm_entity_discovery(self, analysis, enrichment=None) -> List[BusinessEntity]:
        """用 LLM 深入发现业务实体。"""
        return _ba_llm_entity_discovery(analysis, enrichment, self.llm, self._prompt_memory)
    
    def _build_compact_overview(self, analysis) -> str:
        """构建供 LLM 分析的紧凑代码概览（目录树 + 关键文件）。"""
        return _ba_build_compact_overview(analysis)
    
    def _parse_llm_entities(self, response: str) -> List[BusinessEntity]:
        """解析 LLM 返回的业务实体 JSON（含截断容错）。"""
        return _ba_parse_llm_entities(response)
    
    def _merge_entities(self, heuristic: List[BusinessEntity], llm: List[BusinessEntity]) -> List[BusinessEntity]:
        """合并启发式与 LLM 两路实体发现结果。"""
        return _ba_merge_entities(heuristic, llm)
    
    # ====================================================================
    #  Stage 2: 角色与工作流发现
    # ====================================================================
    
    def _discover_roles(self, analysis, entities: List[BusinessEntity]) -> List[UserRole]:
        """发现用户角色"""
        # 优先使用 Prompt 分析中提取的角色
        if self._prompt_analysis and self._prompt_analysis.roles:
            roles = self._roles_from_prompt_analysis(self._prompt_analysis)
            if roles:
                logger.info(f"[BusinessAnalyzer] 从 Prompt 中提取到 {len(roles)} 个角色")
                return roles
        
        if self.llm and hasattr(self.llm, 'chat_completion'):
            return self._llm_role_discovery(analysis, entities)
        return self._heuristic_role_discovery(analysis)

    def _roles_from_prompt_analysis(self, pa: PromptAnalysisResult) -> List[UserRole]:
        """从 Prompt 分析结果提取用户角色。"""
        return _ba_roles_from_prompt_analysis(pa)
    
    def _heuristic_role_discovery(self, analysis) -> List[UserRole]:
        """启发式角色发现（配置/命名/Prompt 角色模式）。"""
        return _ba_heuristic_role_discovery(analysis, self.llm)
    
    def _llm_role_discovery(self, analysis, entities: List[BusinessEntity]) -> List[UserRole]:
        """用 LLM 发现用户角色。"""
        return _ba_llm_role_discovery(analysis, entities, self.llm, self._prompt_memory)
    
    def _find_role_config(self, analysis) -> str:
        """查找项目中的角色/权限配置文件内容。"""
        return _ba_find_role_config(analysis)
    
    def _parse_llm_roles(self, response: str) -> List[UserRole]:
        """解析 LLM 返回的角色 JSON（含截断容错）。"""
        return _ba_parse_llm_roles(response)
    
    def _discover_workflows(self, analysis, entities: List[BusinessEntity],
                            roles: List[UserRole], enrichment=None) -> List[BusinessWorkflow]:
        """发现业务流程（三级降级：Prompt → LLM → 规则，绝不静默返回空/None）"""
        # 优先级 1：Prompt 分析中提取的工作流（最精确）
        if self._prompt_analysis and self._prompt_analysis.workflows:
            workflows = self._workflows_from_prompt_analysis(self._prompt_analysis)
            if workflows:
                logger.info(f"[BusinessAnalyzer] 从 Prompt 中提取到 {len(workflows)} 个工作流")
                return workflows

        # 优先级 2：LLM + 知识库驱动（知识库本身是确定性底座，LLM 只做语义整合）
        # LLM 可用即尝试（enrichment 为可选上下文，缺失时 _llm_workflow_discovery 内部已容错）
        if self.llm and hasattr(self.llm, 'chat_completion'):
            try:
                llm_flows = self._llm_workflow_discovery(analysis, entities, roles, enrichment=enrichment)
                if llm_flows:
                    logger.info(f"[BusinessAnalyzer] 从 LLM+知识库发现 {len(llm_flows)} 个工作流")
                    return llm_flows
            except Exception as e:
                logger.warning(f"[BusinessAnalyzer] LLM 工作流发现失败，回退规则: {e}")

        # 优先级 3：规则降级（docstring + 调用关系启发式，纯确定性）
        rule_flows = self._heuristic_workflow_discovery(analysis, entities)
        if rule_flows:
            logger.info(f"[BusinessAnalyzer] 从规则启发式发现 {len(rule_flows)} 个工作流")
        else:
            logger.info("[BusinessAnalyzer] 各途径均未发现工作流，返回空列表（不在关键管线内时不阻断）")
        return rule_flows
    
    def _organize_workflows_hierarchically(
        self, workflows: List[BusinessWorkflow], entities: List[BusinessEntity],
        roles: List[UserRole], enrichment=None
    ) -> List[WorkflowHierarchy]:
        """
        找到主轴，按层级捋清楚业务流程
        
        核心策略（模拟 AI 直读代码的做法）：
        1. 优先使用 GitNexus 调用链重建模块级调度关系（真实代码证据）
        2. 其次用 LLM 推断层级关系（语义理解）
        3. 最后用规则降级（启发式）
        
        调用链是"骨架"，LLM 分析是"血肉"——结合两者才最准确。
        """
        if not workflows:
            return []
        
        # ── 优先级 1：GitNexus 调用链（真实代码证据）──
        if enrichment and enrichment.call_pairs:
            try:
                call_hierarchy = self._build_hierarchy_from_callgraph(
                    enrichment, entities, workflows, roles
                )
                if call_hierarchy:
                    logger.info(f"[BusinessAnalyzer] 从 GitNexus 调用链重建层级: {len(call_hierarchy)} 个主轴节点, "
                               f"共 {sum(1 + len(h.children) for h in call_hierarchy)} 个节点")
                    return call_hierarchy
            except Exception as e:
                logger.warning(f"[BusinessAnalyzer] GitNexus 调用链层级化失败，回退: {e}")
        
        # ── 优先级 2：LLM 推断层级 ──
        if self.llm and hasattr(self.llm, 'chat_completion'):
            try:
                return self._llm_hierarchical_workflows(workflows, entities, roles)
            except Exception as e:
                logger.warning(f"[BusinessAnalyzer] LLM 层级化失败，使用规则降级: {e}")
        
        # ── 优先级 3：规则推断 ──
        return self._rule_hierarchical_workflows(workflows, entities, roles)
    
    def _build_hierarchy_from_callgraph(self, enrichment, entities: List[BusinessEntity], workflows: List[BusinessWorkflow], roles: List[UserRole]) -> List[WorkflowHierarchy]:
        """基于调用图构建层级工作流（主轴 → 一级 → 二级 → 角色步骤）。"""
        return _ba_build_hierarchy_from_callgraph(enrichment, entities, workflows, roles, self.llm, self._arch_profile, self._project_path)
    
    def _extract_module_from_path(self, file_path: str) -> str:
        """从文件路径提取模块名（项目根目录下的第一层）。"""
        return _ba_module_from_path(file_path)
    
    def _enrich_hierarchy_with_llm(self, hierarchy: List[WorkflowHierarchy], spine_module: str, module_call_count: Dict[str, Dict[str, int]]) -> List[WorkflowHierarchy]:
        """用 LLM 增强层级节点的业务语义描述。"""
        return _ba_enrich_hierarchy_with_llm(self.llm, hierarchy, spine_module, module_call_count)
    
    def _llm_hierarchical_workflows(self, workflows: List[BusinessWorkflow], entities: List[BusinessEntity], roles: List[UserRole]) -> List[WorkflowHierarchy]:
        """用 LLM 重建层级化工作流。"""
        return _ba_llm_hierarchical_workflows(workflows, entities, roles, self.llm)
    
    def _parse_hierarchy(self, data: list, level: int=0) -> List[WorkflowHierarchy]:
        """递归解析 LLM 返回的层级 JSON。"""
        return _ba_parse_hierarchy(data, level)
    
    def _rule_hierarchical_workflows(self, workflows: List[BusinessWorkflow], entities: List[BusinessEntity], roles: List[UserRole]) -> List[WorkflowHierarchy]:
        """规则式层级重建（无 LLM 时的降级方案）。"""
        return _ba_rule_hierarchical_workflows(workflows, entities, roles)
    
    def _workflows_from_prompt_analysis(self, pa: PromptAnalysisResult) -> List[BusinessWorkflow]:
        """从 Prompt 分析结果提取工作流。"""
        return _ba_workflows_from_prompt_analysis(pa)
    
    def _heuristic_workflow_discovery(self, analysis, entities) -> List[BusinessWorkflow]:
        """发现业务流程（知识库 + LLM 驱动，docstring 降级，无硬编码关键词）。"""
        return _ba_heuristic_workflow_discovery(analysis, entities, self._get_kb_analyzer(), self.llm)
    
    def _llm_workflow_discovery(self, analysis, entities, roles, enrichment=None) -> List[BusinessWorkflow]:
        """用 LLM 发现业务流程。"""
        return _ba_llm_workflow_discovery(analysis, entities, roles, enrichment, self.llm, self._prompt_memory)
    
    def _extract_flow_clues(self, analysis) -> str:
        """抽取流程线索（调用链/入口方法），供 LLM 流程分析。"""
        return _ba_extract_flow_clues(analysis)
    
    def _parse_llm_workflows(self, response: str) -> List[BusinessWorkflow]:
        """解析 LLM 返回的工作流 JSON（含截断容错）。"""
        return _ba_parse_llm_workflows(response)
    
    # ====================================================================
    #  Stage 3: 跨端与角色差异分析
    # ====================================================================
    
    def _discover_cross_cutting_differences(self, analysis, entities, roles, enrichment=None) -> List[CrossCuttingDifference]:
        """发现跨切面差异"""
        if self.llm and hasattr(self.llm, 'chat_completion'):
            return self._llm_difference_discovery(analysis, entities, roles, enrichment=enrichment)
        return self._heuristic_difference_discovery(analysis)
    
    def _heuristic_difference_discovery(self, analysis) -> List[CrossCuttingDifference]:
        """启发式跨端差异发现（gui/web 目录与 UI 线索对比）。"""
        return _ba_heuristic_difference_discovery(analysis, self.llm)
    
    def _llm_difference_discovery(self, analysis, entities, roles, enrichment=None) -> List[CrossCuttingDifference]:
        """用 LLM 发现跨端/跨角色差异。"""
        return _ba_llm_difference_discovery(analysis, entities, roles, enrichment, self.llm, self._prompt_memory)
    
    def _find_ui_clues(self, analysis) -> str:
        """收集 UI 相关线索（界面代码/入口）。"""
        return _ba_find_ui_clues(analysis)
    
    def _parse_llm_differences(self, response: str) -> List[CrossCuttingDifference]:
        """解析 LLM 返回的差异 JSON（含截断容错）。"""
        return _ba_parse_llm_differences(response)
    
    # ====================================================================
    #  Stage 4: 自评估
    # ====================================================================
    
    def _evaluate_quality(self, result: BusinessAnalysisResult) -> AnalysisEvaluation:
        """自评估：按六大检查项给业务分析结果打分。"""
        return _ba_evaluate_quality(result)
    
    def _generate_evaluation_suggestions(self, issues: List[str]) -> List[str]:
        """针对评估发现的问题生成改进建议。"""
        return _ba_generate_evaluation_suggestions(issues)
    
    # ====================================================================
    #  Stage 5: 自改进
    # ====================================================================
    
    def _generate_improvement(self, result: BusinessAnalysisResult, evaluation: AnalysisEvaluation) -> str:
        """自改进：让 LLM 硬编码更好的分析 Prompt。"""
        return _ba_generate_improvement(self.llm, result, evaluation)
    
    # ====================================================================
    #  报告生成
    # ====================================================================
    
    def to_business_report(self, result: BusinessAnalysisResult) -> str:
        """生成专业级业务全景报告 V2

        各章节渲染委托到模块级 _render_ba_* 函数，本方法只负责章节编排顺序：
        报告头 → 架构总览 → 模块详解 → 流程层级 → 角色权限 → 依赖关系 → 跨端差异 → 关键发现。
        """
        lines: List[str] = []
        entities = _ba_filter_business_entities(result.entities)

        # 一~四章：报告头 / 系统架构 / 业务模块 / 核心流程（层级或平面降级）
        _render_ba_header(lines, result)
        _render_ba_architecture(lines, entities)
        _render_ba_modules(lines, result, entities)
        _render_ba_flows(lines, result)

        # 五~七章：用户角色 / 模块依赖与调用关系 / 跨端差异
        _render_ba_roles(lines, result, entities)
        _render_ba_dependencies(lines, result)
        _render_ba_differences(lines, result)

        # 八章：关键发现与建议（评分 + 优势 + 改进建议）
        _render_ba_findings(lines, result, entities)

        return '\n'.join(lines)
    
    # ====================================================================
    #  LLM 调用辅助
    # ====================================================================

    def _build_report_data_summary(self, result: BusinessAnalysisResult) -> str:
        """生成报告数据摘要（供上级报告引用）。"""
        return _ba_build_report_data_summary(result)

    def _call_llm(self, prompt: str) -> str:
        """调用 LLM（含超时与临时错误重试，永久错误不重试）。"""
        return _ba_call_llm(self.llm, prompt)

    def _render_hierarchy_children(self, lines: List[str], children: List[WorkflowHierarchy], indent_level: int=1) -> None:
        """渲染层级子节点（缩进 Markdown 列表）。"""
        return _render_hier_children(lines, children, indent_level)

    def _render_hierarchy_mermaid(self, lines: List[str], node: WorkflowHierarchy, parent_id: str, counter: List[int]) -> None:
        """渲染层级节点的 mermaid 流程图。"""
        return _render_hier_mermaid(lines, node, parent_id, counter)


# ========================================================================
#  便捷函数
# ========================================================================

def analyze_project_business(project_analysis, llm_client=None, max_iterations=3) -> str:
    """
    一键生成业务全景分析报告
    
    用法:
        from core.code_analyzer import CodeAnalyzer
        from core.business_analyzer import analyze_project_business
        
        analyzer = CodeAnalyzer()
        analysis = analyzer.analyze_project("/path/to/project")
        report = analyze_project_business(analysis)
        print(report)
    """
    if llm_client is None:
        from core.llm_integration import LLMIntegration
        llm_client = LLMIntegration()
    probe = getattr(llm_client, "is_available", None)
    if callable(probe) and not probe():
        return (
            "【业务报告未生成】业务全景报告需要 LLM 才能产出，但当前未配置有效的 API Key。\n"
            "请在配置面板填写 API Key 后再生成。\n"
            "（审计、知识图谱、架构等确定性分析不受影响，可正常使用）"
        )
    ba = BusinessAnalyzer(llm_client=llm_client)
    result = ba.analyze(project_analysis, max_iterations=max_iterations)
    return ba.to_business_report(result)
