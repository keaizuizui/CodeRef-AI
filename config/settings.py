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

# ═══════════════════════════════════════════════════════════════════
# 操作记忆层（operation_memory.py）
# ═══════════════════════════════════════════════════════════════════

# 资源类型清单（静态审计可识别的资源类别）
# env_tool：外部开发工具可执行文件位置（git/python/node 等便携包）
OMEM_RESOURCE_TYPES = ("git", "model", "api", "tool", "doc", "dependency", "report", "env_tool")

# 旁目录敏感特征：命中即视为敏感，只记录位置不收录内容
OMEM_SENSITIVE_DIR_HINTS = ("key", "secret", "credential", "token", "password", ".ssh")

# 旁目录探明上限：单次同步最多探测的旁目录数，防止扩散
OMEM_MAX_SIDE_DIRS = 20

# 静态审计单分类命中上限：防止单分类条目过多拖慢 / 撑爆配置
OMEM_MAX_PER_CATEGORY = 200

# 模型权重文件扩展名（用于模型资源定位）
OMEM_MODEL_EXTENSIONS = (
    ".safetensors", ".ckpt", ".pt", ".pth", ".bin", ".onnx",
    ".gguf", ".ggml", ".h5", ".tflite", ".pb", ".pkl",
)

# LLM 提炼请求相关
OMEM_LLM_TIMEOUT = 90          # LLM 提炼单次超时（秒）
OMEM_LLM_MAX_CHARS_SOURCE = 6000  # 喂给 LLM 的单来源文本截断长度
OMEM_EXTRACT_GRAPH_LIMIT = 30  # 单次同步最多提炼的隐性知识基数

# 时间线保留条数上限（追加式，防无限膨胀）
OMEM_TIMELINE_MAX = 200

# ═══════════════════════════════════════════════════════════════════
# 环境工具探测（env_tool）：识别外部开发工具可执行文件位置
# ═══════════════════════════════════════════════════════════════════

# 候选工具：工具名 -> (可执行文件名, 说明)
OMEM_ENV_TOOL_BINS = {
    "git": ("git.exe", "版本控制"),
    "python": ("python.exe", "Python 解释器"),
    "node": ("node.exe", "Node.js 运行时"),
    "ollama": ("ollama.exe", "本地 LLM 服务"),
    "ffmpeg": ("ffmpeg.exe", "音视频处理"),
}

# 常见便携根目录（支持 glob 通配，如 work/*/PortableGit）。
# PATH 中找不到工具时，在这些位置探测可执行文件，解决便携工具不在 PATH 的问题。
# 末尾几项覆盖"项目内嵌解释器 / 测试 venv"：它们不在 PATH、也不在标准便携根，
# 若不显式列出，自动探测会漏掉（如 psd_tool 自带 python）。
OMEM_ENV_TOOL_ROOTS = (
    "~/.trae-cn/work/*/PortableGit",
    "~/.trae-cn/work/*/*/PortableGit",
    "~/AppData/Local/Programs/Git",
    "~/AppData/Local/Programs/Python",
    "C:/Program Files/Git",
    "C:/Program Files (x86)/Git",
    # --- 项目内嵌解释器 / 测试 venv（自动探测补充）---
    "~/Desktop/psd_tool/psd_tool",                        # psd_tool 自带 python
    "~/Desktop/1111/Coderef-Test/测试用例/*/.venv",       # Coderef-Test 用例 venv
)

# 便携根下的 bin 子目录名（相对便携根）
# "python" 用于 psd_tool 内嵌解释器（位于 <root>/python/python.exe）
OMEM_ENV_TOOL_BIN_SUBDIRS = ("bin", "cmd", "mingw64/bin", "usr/bin", "Scripts", "python")

# 操作记忆数据目录（可配置，默认用户数据目录，避免写入项目根）。
# 设为空串时回退到项目根下 data/operation_memory（兼容旧行为）。
# 支持环境变量 / 直接改本文件两种方式。
OMEM_DATA_DIR = ""