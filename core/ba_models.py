# -*- coding: utf-8 -*-
"""
业务分析数据模型
==================
God Module Split 第 2 批：从 core/business_analyzer.py 拆解出的数据模型子模块。

依赖拓扑（单向、无环）：
    ba_models.py ← ba_discovery.py / ba_eval.py / ba_report.py ← business_analyzer.py
本模块只含纯数据模型（dataclass），不承载任何分析逻辑。
"""

from typing import Dict, List, Optional
from dataclasses import dataclass, field

from core.gitnexus_client import GitNexusEnrichment


# ========================================================================
#  数据模型
# ========================================================================

@dataclass
class BusinessEntity:
    """业务实体 —— 代码中映射到业务概念的类/模块"""
    name: str                         # 业务名称 (如 "营销助手", "报表工具")
    technical_name: str               # 代码中的技术名称
    purpose: str                      # 业务目的 (一句话)
    files: List[str] = field(default_factory=list)     # 涉及的文件 (相对路径)
    core_classes: List[str] = field(default_factory=list)  # 核心类名
    capabilities: List[str] = field(default_factory=list)   # 业务能力列表


@dataclass
class UserRole:
    """用户角色"""
    name: str                         # 角色名
    description: str                  # 角色描述
    access_level: str                 # 权限级别
    accessible_features: List[str] = field(default_factory=list)
    ui_mode: str = ""                 # GUI / Web / 通用


@dataclass
class BusinessWorkflow:
    """业务流程"""
    name: str
    owner: str                        # 所属实体
    steps: List[str] = field(default_factory=list)     # 步骤描述
    trigger: str = ""                 # 触发条件
    output: str = ""                  # 产出物
    roles_involved: List[str] = field(default_factory=list)


@dataclass
class WorkflowHierarchy:
    """工作流层级节点 —— 按层级组织的业务流程树"""
    name: str                              # 层级名称
    level: int = 0                         # 层级深度（0=主轴入口, 1=一级, 2=二级...）
    description: str = ""                  # 层级描述
    module: str = ""                       # 所属模块
    steps: List[str] = field(default_factory=list)     # 该层级的关键步骤
    trigger: str = ""                      # 触发条件
    output: str = ""                       # 产出物
    roles_involved: List[str] = field(default_factory=list)  # 涉及角色
    children: List['WorkflowHierarchy'] = field(default_factory=list)  # 子层级
    is_spine: bool = False                 # 是否为主轴节点


@dataclass
class CrossCuttingDifference:
    """跨切面差异 (Web vs GUI, A角色 vs B角色 等)"""
    dimension: str                    # 对比维度 ("UI平台" / "用户角色" / "工具模式")
    aspect: str                       # 对比方面
    side_a: str                       # 对比方A
    side_b: str                       # 对比方B
    difference: str                   # 差异描述
    impact: str = ""                  # 对业务的影响


@dataclass
class AnalysisEvaluation:
    """自评估结果"""
    passed: bool
    score: float                      # 0-1
    issues: List[str] = field(default_factory=list)
    suggestions: List[str] = field(default_factory=list)


@dataclass
class BusinessAnalysisResult:
    """业务分析全量结果"""
    project_name: str = ""
    
    # 技术概览 (来自 CodeAnalyzer)
    tech_summary: str = ""
    languages: Dict[str, int] = field(default_factory=dict)
    file_count: int = 0
    line_count: int = 0
    total_classes: int = 0
    total_functions: int = 0
    
    # 业务层发现
    entities: List[BusinessEntity] = field(default_factory=list)
    roles: List[UserRole] = field(default_factory=list)
    workflows: List[BusinessWorkflow] = field(default_factory=list)
    hierarchy: List[WorkflowHierarchy] = field(default_factory=list)  # 层级化工作流树
    differences: List[CrossCuttingDifference] = field(default_factory=list)
    
    # GitNexus 增强数据
    enrichment: Optional[GitNexusEnrichment] = None

    # 自学习轨迹
    iteration_count: int = 1
    improvement_history: List[str] = field(default_factory=list)
    evaluation_scores: List[float] = field(default_factory=list)
