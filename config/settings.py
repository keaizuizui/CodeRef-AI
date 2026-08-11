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

# 架构腐化诊断（arch_audit.py）
ARCH_SCC_CYCLE_MIN_SIZE = 2                # 模块 CALLS 图 SCC 大小 ≥2 或自环即视为循环依赖
ARCH_GOD_FAN_OUT_THRESHOLD = 15            # 模块扇出超过此值视为上帝模块（依赖过多下游）
ARCH_LARGE_MODULE_SYMBOL_THRESHOLD = 20    # 单模块符号数超过此值视为异常规模
ARCH_HEALTH_WEIGHT_CYCLE = 3.0             # 每个循环依赖扣分权重（封顶 6.0）
ARCH_HEALTH_WEIGHT_GOD = 1.0               # 每个上帝模块扣分权重（封顶 2.0）
ARCH_HEALTH_WEIGHT_LAYER = 1.0             # 每个分层违例扣分权重（封顶 2.0）
ARCH_HEALTH_WEIGHT_LARGE = 0.5             # 每个大模块扣分权重（封顶 2.0）

# 架构探测器（arch_detector.py）
ARCH_DETECT_MAX_ENTRY_SIGNALS = 80         # 入口信号总数上限，防止过大拖慢后续
ARCH_DETECT_MAX_TARGET_LEN = 60            # 入口目标描述截断长度
ARCH_DETECT_CONFIDENCE_FLOOR = 0.05        # 至少命中 1 个文件时的置信度兜底加成
ARCH_DETECT_CONFIDENCE_CAP = 1.0           # 置信度上限