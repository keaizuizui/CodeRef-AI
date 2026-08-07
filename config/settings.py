# -*- coding: utf-8 -*-
"""
配置管理 —— 检测阈值常量

本文件仅收敛各检测器（core/*.py）中散落的阈值魔法数字。
LLM 配置统一由 core/llm_integration.py 的 LLMIntegration() 无参构造加载
（环境变量 QSettings → config.json → 默认值），本文件不再承载 LLM 密钥配置。
"""

# ═══════════════════════════════════════════════════════════════
# 检测阈值常量
# ═══════════════════════════════════════════════════════════════

# 技术债务检测器（tech_debt_detector.py）
TECH_DEBT_COMPLEXITY_THRESHOLD = 10        # 圈复杂度阈值（if/for/while/except 语句数）
TECH_DEBT_COGNITIVE_THRESHOLD = 15         # 认知复杂度阈值（SonarQube 默认）
TECH_DEBT_LONG_FUNCTION_THRESHOLD = 100    # 函数行数阈值
TECH_DEBT_NESTING_DEPTH_THRESHOLD = 4      # 嵌套深度阈值（缩进级别）
TECH_DEBT_COMMENTED_CODE_MIN_LINES = 3     # 注释代码块最少行数

# 治理审计质量铁律（governance_audit.py）
GOVERNANCE_FUNCTION_TOO_LONG = 100         # 函数过长阈值（行数）
GOVERNANCE_FUNCTION_TOO_MANY_PARAMS = 8    # 参数过多阈值（个数）
GOVERNANCE_NESTING_TOO_DEEP = 4            # 嵌套过深阈值（缩进级别）