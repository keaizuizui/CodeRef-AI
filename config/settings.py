# -*- coding: utf-8 -*-
"""
配置管理 —— 检测阈值常量

本文件仅收敛各检测器（core/*.py）中散落的阈值魔法数字。
LLM 配置统一由 core/llm_integration.py 的 LLMIntegration() 无参构造加载
（环境变量 QSettings → config.json → 默认值），本文件不再承载 LLM 密钥配置。
"""

import os
import logging

logger = logging.getLogger(__name__)

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
# PATH 中找不到工具时，在这些通用位置探测可执行文件，解决便携工具不在 PATH 的问题。
# 个人化根（项目内嵌解释器 / 私有 test venv）不走版本库，改由
# omem_extra_tool_roots() 从环境变量 CODEREF_EXTRA_TOOL_ROOTS / config.json 注入并追加。
OMEM_ENV_TOOL_ROOTS = (
    "~/.trae-cn/work/*/PortableGit",
    "~/.trae-cn/work/*/*/PortableGit",
    "~/AppData/Local/Programs/Git",
    "~/AppData/Local/Programs/Python",
    "C:/Program Files/Git",
    "C:/Program Files (x86)/Git",
    # ── 个人化工具根（项目内嵌解释器 / 私有 test venv）──────────────────
    # 不再硬编码到版本库：由环境变量 CODEREF_EXTRA_TOOL_ROOTS（分号分隔 glob）
    # 或 config/config.json 的 extra_tool_roots 字段注入，保持代码库通用。
    # 例：CODEREF_EXTRA_TOOL_ROOTS="~/Desktop/psd_tool/psd_tool;C:/my/venvs/*"
)

# 个人化工具根的环境变量名（分号分隔的 glob 列表，最高优先级）
OMEM_ENV_TOOL_ROOTS_EXTRA_ENV = "CODEREF_EXTRA_TOOL_ROOTS"


def omem_extra_tool_roots():
    """读取个人化工具根（不进版本库）：环境变量优先级最高，config/config.json 兜底。

    返回按注入顺序排列的 glob 列表（不含通用根），允许重复注入与 `~` 展开。
    """
    extra = []
    raw = os.environ.get(OMEM_ENV_TOOL_ROOTS_EXTRA_ENV, "")
    if raw:
        for part in raw.split(";"):
            part = (part or "").strip()
            if part:
                extra.append(part)
        return extra
    # config/config.json 兜底（已被 .gitignore 忽略，不随版本库分发）
    try:
        import json
        here = os.path.dirname(os.path.abspath(__file__))
        cfg_path = os.path.join(here, "config.json")
        if os.path.isfile(cfg_path):
            with open(cfg_path, "r", encoding="utf-8") as fh:
                cfg = json.load(fh)
            for item in (cfg.get("extra_tool_roots") or []):
                item = (item or "").strip()
                if item:
                    extra.append(item)
    except Exception as e:
        logger.warning(f"读取 extra_tool_roots 配置失败，忽略附加工具根配置: {e}")
    return extra

# 便携根下的 bin 子目录名（相对便携根）
# "python" 用于 psd_tool 内嵌解释器（位于 <root>/python/python.exe）
OMEM_ENV_TOOL_BIN_SUBDIRS = ("bin", "cmd", "mingw64/bin", "usr/bin", "Scripts", "python")

# WSL 子系统内工具清单（如 coderabbit 住在 WSL 的 /root/.local/bin）。
# Windows PATH / 便携根都扫不到这类工具，需经 wsl.exe 进入发行版用 `command -v` 探测。
# 值 = 在 WSL 内执行 `command -v <值>` 的命令名。
OMEM_WSL_TOOL_BINS = {
    "coderabbit": "coderabbit",   # CodeRabbit CLI（WSL 内 /root/.local/bin）
}

# 单次 WSL 命令探测超时（秒），防止 WSL 未配置 / 启动慢时阻塞扫描
OMEM_WSL_CMD_TIMEOUT = 20

# WSL 探测重试次数（含首次）。WSL 首次冷启动可能较慢导致偶发超时/空输出，
# 静默重试一次提升确定性，避免工具位置间歇性漏记。
OMEM_WSL_PROBE_RETRIES = 2

# 操作记忆数据目录（可配置，默认用户数据目录，避免写入项目根）。
# 设为空串时回退到项目根下 data/operation_memory（兼容旧行为）。
# 支持环境变量 / 直接改本文件两种方式。
OMEM_DATA_DIR = ""

# ═══ 操作记忆原子写并发稳定性（operation_memory.py） ═══
# 跨进程互斥写：同一目标文件在多个进程并发替换时，Windows 上 os.replace 覆盖
# 正在被其他进程打开/读写的目标可能触发 WinError 5/32（拒绝访问/文件被占用）。
# 为根除该竞态，对每个目标写入前先对 <path>.lock 加跨进程排他锁（Unix flock /
# Windows msvcrt.locking），串行化不同进程对同一产物的替换。下列为可调参数。
OMEM_ATOMIC_MAX_RETRIES = 8      # 单个目标替换失败的最大重试次数
OMEM_ATOMIC_RETRY_DELAY = 0.05   # 重试初始退避（秒）
OMEM_ATOMIC_RETRY_BACKOFF = 2.0  # 退避倍率（指数退避，重试间隔逐次翻倍）
OMEM_PER_FILE_LOCK = True        # 是否启用跨进程文件锁（置 False 可退化回仅重试）

# ═══════════════════════════════════════════════════════════════════
# 分析缓存目录（code_analyzer.py）
# ═══════════════════════════════════════════════════════════════════

# 代码分析结果缓存目录（可配置，默认项目根 data/analysis_cache）。
# 设为空串时回退到项目根下 data/analysis_cache（兼容旧行为），保证测试/CI
# 可通过环境变量 CODEREF_ANALYSIS_CACHE 或直接改本文件，将缓存隔离到独立目录，
# 避免多项目 / 多进程并发污染同一缓存。
CODEREF_ANALYSIS_CACHE = ""

# ═══════════════════════════════════════════════════════════════════
# Wiki 生成器（wiki_generator.py / wiki_ir.py / wiki_compare.py）
# ═══════════════════════════════════════════════════════════════════

# 增量同步（R1）：记录上次已文档化 gitHead 的状态文件名（位于 wiki 输出目录）
WIKI_LAST_UPDATE_FILE = ".last-update.json"

# 增量同步：判定"页面失真需重写"的变更文件数阈值（git log 变更文件数超过即全量重建）
WIKI_INCREMENTAL_MAX_CHANGED_FILES = 50

# front matter / confidence（R2）：交叉验证徽章 → confidence 字段映射
WIKI_CONFIDENCE_MAP = {
    "confirmed": "high",
    "partial": "medium",
    "unverified": "low",
    "missing": "none",
}

# 证据锚定（R3）：wiki 文档中证据锚定标记前缀（Git 文件+行号+commit）
WIKI_SRC_MARK_PREFIX = "SRC"

# Last-good 门控（R3）：上次全校验通过的产物备份目录名（位于 wiki 输出目录）
WIKI_LAST_GOOD_DIR = ".last-good"

# 用户授权层（R6）：只读不重写的用户 brief 文件名（位于项目根）
WIKI_INSTRUCTIONS_FILE = "INSTRUCTIONS.md"

# 用户授权指令注入 system prompt 的字符上限（防越权注入撑爆 context；超限按章节截断）
WIKI_INSTRUCTIONS_MAX_CHARS = 2000

# Agent 指针集成（R7）：写入 AGENTS.md / CLAUDE.md 的指针区块标记
WIKI_AGENT_POINTER_START = "<!--CODEREFF:START-->"
WIKI_AGENT_POINTER_END = "<!--CODEREFF:END-->"

# JSON-IR 分离（R4）：IR schema 版本
WIKI_IR_SCHEMA_VERSION = 1

# 架构图可视化（R5）：Mermaid 图嵌入的最小节点数（低于则不生成图，避免噪音）
WIKI_MERMAID_MIN_NODES = 3

# Mermaid 自愈（R9）：失败降级为 text fence 的注释标记
WIKI_MERMAID_FALLBACK_MARK = "<!-- mermaid-fallback -->"

# Compare diff（R8）：架构快照文件名
WIKI_SNAPSHOT_FILE = ".arch-snapshot.json"

# 成本/输出封顶（R10）：单文档 LLM 输出最大字符数（超限截断并标记）
WIKI_LLM_OUTPUT_CAP_CHARS = 12000

# 成本/输出封顶（R10）：单次 wiki 生成的 LLM 调用次数上限（超限停止并提示）
WIKI_LLM_CALL_BUDGET = 200
