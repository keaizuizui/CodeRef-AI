# -*- coding: utf-8 -*-
"""Wiki Generator 共享配置常量（纯常量 + 配置加载机制）

自 core/wiki_generator.py 常量配置区外置（第 4 批上帝模块拆解）：
- _settings / _cfg：从 config.settings 读取常量的配置加载机制（缺失时用内置默认值兜底，
  保证本模块在独立导入（不在项目根）时也能正常工作）
- WIKI_*（R1/R2/R3/R6/R7）、BADGE_MD、LARGE_REPO_*、METADATA_*、CITE_IGNORE 等纯常量

主文件 core/wiki_generator.py 以 re-export 方式引用，对外符号面保持不变。
"""
# ═══════════════════════════════════════════════════════════════════
# 共享配置常量（R1/R2/R3/R6/R7）
# 优先从 config.settings 读取（主 agent 已添加），缺失时用内置默认值兜底，
# 保证本模块在独立导入（不在项目根）时也能正常工作。
# ═══════════════════════════════════════════════════════════════════
try:
    from config import settings as _settings
except Exception:
    _settings = None


def _cfg(name: str, default):
    """从 config.settings 读取常量；settings 缺失或常量缺失时返回默认值。"""
    return getattr(_settings, name, default) if _settings is not None else default


# R1 增量同步：状态文件名（位于 wiki 输出目录）
WIKI_LAST_UPDATE_FILE = _cfg("WIKI_LAST_UPDATE_FILE", ".last-update.json")
# R1 增量同步：变更文件数超此阈值则全量重建
WIKI_INCREMENTAL_MAX_CHANGED_FILES = _cfg("WIKI_INCREMENTAL_MAX_CHANGED_FILES", 50)
# R2 front matter：交叉验证徽章 → confidence 映射
WIKI_CONFIDENCE_MAP = _cfg("WIKI_CONFIDENCE_MAP", {
    "confirmed": "high",
    "partial": "medium",
    "unverified": "low",
    "missing": "none",
})
# R3 证据锚定：锚定标记前缀（Git 文件+行号+commit）
WIKI_SRC_MARK_PREFIX = _cfg("WIKI_SRC_MARK_PREFIX", "SRC")
# R3 Last-good 门控：上次全校验通过的产物备份目录名
WIKI_LAST_GOOD_DIR = _cfg("WIKI_LAST_GOOD_DIR", ".last-good")
# R6 用户授权层：只读不重写的用户 brief 文件名（位于项目根）
WIKI_INSTRUCTIONS_FILE = _cfg("WIKI_INSTRUCTIONS_FILE", "INSTRUCTIONS.md")
# R6 用户授权指令注入 system prompt 的字符上限（防越权注入撑爆 context）
WIKI_INSTRUCTIONS_MAX_CHARS = _cfg("WIKI_INSTRUCTIONS_MAX_CHARS", 2000)
# R7 Agent 指针：写入 AGENTS.md 的指针区块标记
WIKI_AGENT_POINTER_START = _cfg("WIKI_AGENT_POINTER_START", "<!--CODEREFF:START-->")
WIKI_AGENT_POINTER_END = _cfg("WIKI_AGENT_POINTER_END", "<!--CODEREFF:END-->")


BADGE_MD = {
    "confirmed": "✅ 确证",
    "partial": "🔵 部分确证",
    "unverified": "🟡 存疑",
    "missing": "🔴 缺失",
}


# ==================== 模块级常量区（自 WikiGenerator 提取，配置与生成逻辑分离） ====================
# 大仓库阈值
LARGE_REPO_THRESHOLD = 300

# 大仓库采样上限
LARGE_REPO_SAMPLE = 150

# 每个模块收集阶段的失控保护上限。
# 收集必须覆盖全部文件：file_count 与模块描述都依赖它；采样交给 LARGE_REPO_SAMPLE，
# 旧实现把 MAX_FILES_PER_MODULE(30) 当成收集上限，导致 327 文件的模块被写成"31 个文件"。
MAX_COLLECT_FILES_PER_MODULE = 2000

# LLM 单次最大输入字符数（避免 token 超限）
MAX_CONTEXT_CHARS = 40000

# 分层人话版规模上限（更大项目保护：避免入口/数据流数量极大时 LLM 调用爆炸）
MAX_ENTRY_DOCS = 20       # 最多生成多少篇入口级(L1)人话版

MAX_FLOW_DOCS = 30        # 最多生成多少条数据流(L2)人话版

# ─── 核心模块判定规则（可配置，AI 可追加）───
# 默认入口文件名
DEFAULT_ENTRY_FILES = ["main.py", "app.py", "server.py", "run.py", "__init__.py"]

# 默认文件数阈值
DEFAULT_MIN_FILES = 10

# 规则配置文件（相对于项目 cache）
CORE_RULES_FILE = "core_rules.json"

# 排除的目录名
EXCLUDE_DIRS = {
    "__pycache__", "node_modules", ".git", "venv", ".venv", "env",
    "Lib", "lib", "lib64", "site-packages", "dist-packages",
    "third_party", ".gitnexus", "data", "docs", "reports",
    "cache", "coderef-report", "logs", "build", "dist",
    ".pytest_cache", ".mypy_cache", ".ruff_cache", ".tox",
}

# 子项目指示器：包含这些文件的子目录视为独立子项目
SUBPROJECT_INDICATORS = ["requirements.txt", "pyproject.toml", "setup.py"]

# Wiki 风格定义
WIKI_STYLES = {
    "comprehensive": "详细全面，面向非程序员，用通俗语言解释一切",
    "reference": "精简参考，面向有经验的开发者，快速查阅关键信息",
    "tutorial": "教程风格，逐步引导，适合新手学习项目",
    "plain": "极简风格，最短说明，只保留核心要点",
}

# ═══════════════════════════════════════════════════════════════════
# 三级管线：Stage 1b — 采样（超大仓库时元数据降级）
# ═══════════════════════════════════════════════════════════════════

METADATA_MAX_FILES = 500   # 单个模块元数据文件上限

METADATA_MAX_CLASSES = 20  # 单个文件最多保留的类

METADATA_MAX_FUNCS = 30    # 单个文件最多保留的函数

# ═══════════════════════════════════════════════════════════════════
# 编校验证：从生成文本提取标识符，与 Stage 1 元数据交叉核对
# ═══════════════════════════════════════════════════════════════════

# 标识符提取时忽略的常见词
CITE_IGNORE = {
    "pip", "bash", "json", "txt", "yaml", "html", "css", "md",
    "exe", "com", "org", "url", "http", "https", "api", "rest",
    "utf", "utf-8", "ascii", "base64", "sha256", "md5",
    "true", "false", "none", "null", "yes", "no",
    "localhost", "0.0.0.0", "127.0.0.1", "v1", "v2", "v3",
    "chcp", "nul", "goto", "set", "echo", "rem", "pause",
    "cli", "gui", "sdk", "ide", "db", "sql", "nosql",
    "linux", "macos", "windows", "ios", "android",
    "readme", "license", "gitignore",
    "post-commit", "pre-commit", "commit-msg",
    "node_modules", "pycache", "venv", "env",
}
