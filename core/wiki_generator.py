# -*- coding: utf-8 -*-
"""
Wiki Generator —— 面向不懂代码的 AI 辅助开发者的项目 Wiki 生成器

基于 LLM 理解代码语义，生成结构化多文档 Wiki，替代旧版机械 docstring 搬运。
借鉴 ai-codebase-scribe 和 readme-llm-generator 的设计理念。

输出结构：
  docs/wiki/
  ├── README.md           # 项目概述（给老板/同事看的）
  ├── ARCHITECTURE.md     # 架构设计（技术全景）
  ├── INSTALLATION.md     # 安装指南（手把手）
  ├── USAGE.md            # 使用指南（怎么用）
  ├── MODULES/            # 模块详解
  │   ├── _index.md       # 模块索引
  │   ├── core.md         # 核心模块
  │   └── ...             # 每个模块一页
  ├── API.md              # API 文档（如有 Web 框架）
  └── WIKI_INDEX.md       # 导航首页

特性：
- LLM 驱动：让 AI 理解代码语义，而非机械搬运 docstring
- 大仓库支持：>300 文件自动切换采样模式，优先核心文件
- Git Hook 配置：可选安装 post-commit hook 自动更新 wiki
- 通俗语言：面向不懂代码的用户，解释"做什么"而不只是"是什么"

作者: CodeRef Team
版本: v2.0 (升级自 generate_docs)
"""

import os
import re
import ast
import json
import shutil
import subprocess
import hashlib
from datetime import datetime
from typing import Dict, List, Any, Optional, Tuple
from dataclasses import dataclass, field
from collections import defaultdict


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
# R7 Agent 指针：写入 AGENTS.md 的指针区块标记
WIKI_AGENT_POINTER_START = _cfg("WIKI_AGENT_POINTER_START", "<!--CODEREFF:START-->")
WIKI_AGENT_POINTER_END = _cfg("WIKI_AGENT_POINTER_END", "<!--CODEREFF:END-->")


# 交叉验证徽章渲染（可选导入：wiki_cross_verify 缺失时降级为空渲染，不阻断生成）
def module_badge_md(status: str) -> str:
    """模块文档顶部徽章区块（与 wiki_cross_verify 同构，防导入失败降级）。"""
    label = {
        "confirmed": "✅ **确证** — 该模块全部符号都在入口管线闭包内，功能确被调用",
        "partial": "🔵 **部分确证** — 部分符号在入口管线内，其余独立/未走主流程",
        "unverified": "🟡 **存疑** — 该模块不在入口管线内（可能动态调用或未走主流程），描述需编程 AI 复核",
        "missing": "🔴 **缺失** — 图谱中找不到该模块，描述无静态铁证背书",
    }.get(status, "")
    if not label:
        return ""
    return (
        "> **静态交叉验证**：" + label + "\n>\n"
        "> 本徽章来自知识图谱调用闭包（确定性铁证），用于核验下方描述的 "
        "「是否真的在流程里被调用」。" + (" 未确证不代表流程错误，只代表需进一步核验。" if status in ("unverified", "missing") else "") + "\n"
    )


BADGE_MD = {
    "confirmed": "✅ 确证",
    "partial": "🔵 部分确证",
    "unverified": "🟡 存疑",
    "missing": "🔴 缺失",
}


# ═══════════════════════════════════════════════════════════════════
# 数据结构
# ═══════════════════════════════════════════════════════════════════

@dataclass
class WikiModule:
    """Wiki 模块信息"""
    name: str
    path: str
    py_files: List[str]
    file_count: int
    is_core: bool = False
    description: str = ""


@dataclass
class WikiResult:
    """Wiki 生成结果"""
    project_path: str
    project_name: str
    output_dir: str
    wiki_style: str = "comprehensive"
    documents: List[str] = field(default_factory=list)
    module_count: int = 0
    total_files: int = 0
    large_repo: bool = False
    subprojects: List[str] = field(default_factory=list)
    subproject_results: List[Dict] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)


# ═══════════════════════════════════════════════════════════════════
# 多级管线元数据结构
# ═══════════════════════════════════════════════════════════════════

@dataclass
class CodeFileMetadata:
    """单文件 AST 元数据（约原代码 5-10% 体积）"""
    rel_path: str
    docstring: str = ""
    classes: List[Dict] = field(default_factory=list)
    functions: List[Dict] = field(default_factory=list)
    imports: List[str] = field(default_factory=list)
    has_main_block: bool = False
    is_entry_point: bool = False


@dataclass
class ModuleCodeMetadata:
    """模块级的元数据聚合"""
    name: str
    path: str
    files: List[CodeFileMetadata] = field(default_factory=list)
    total_files: int = 0


@dataclass
class ProjectCodeMetadata:
    """项目级元数据"""
    project_path: str
    modules: List[ModuleCodeMetadata] = field(default_factory=list)
    total_files: int = 0
    has_web_framework: bool = False


# ═══════════════════════════════════════════════════════════════════
# Wiki 生成器
# ═══════════════════════════════════════════════════════════════════

class WikiGenerator:
    """项目 Wiki 生成器"""

    # 大仓库阈值
    LARGE_REPO_THRESHOLD = 300
    # 大仓库采样上限
    LARGE_REPO_SAMPLE = 150
    # 每个模块最多采样的文件数
    MAX_FILES_PER_MODULE = 30
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

    @staticmethod
    def _core_rules_path(project_path: str) -> str:
        """核心模块规则配置文件路径"""
        import hashlib
        ph = hashlib.md5(os.path.abspath(project_path).encode()).hexdigest()[:12]
        d = os.path.join(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__))), "cache", "pipeline")
        os.makedirs(d, exist_ok=True)
        return os.path.join(d, f"core_rules_{ph}.json")

    def _load_core_rules(self, project_path: str) -> dict:
        """加载核心模块判定规则（默认值 + 配置文件覆盖）"""
        rules = {
            "entry_files": list(self.DEFAULT_ENTRY_FILES),
            "core_names": [],
            "min_files": self.DEFAULT_MIN_FILES,
        }
        path = self._core_rules_path(project_path)
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    saved = json.load(f)
                if "entry_files" in saved:
                    rules["entry_files"] = saved["entry_files"]
                if "core_names" in saved:
                    rules["core_names"] = saved["core_names"]
                if "min_files" in saved:
                    rules["min_files"] = saved["min_files"]
            except Exception:
                pass
        return rules

    @staticmethod
    def save_core_rules(project_path: str, rules: dict) -> bool:
        """保存核心模块判定规则（供 AI 调用）"""
        try:
            path = WikiGenerator._core_rules_path(project_path)
            with open(path, "w", encoding="utf-8") as f:
                json.dump(rules, f, ensure_ascii=False, indent=1)
            return True
        except Exception:
            return False

    @staticmethod
    def get_core_rules(project_path: str) -> dict:
        """获取当前核心模块判定规则（供 AI 查看）"""
        wg = WikiGenerator()
        return wg._load_core_rules(project_path)

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

    def __init__(self, llm_client=None):
        """初始化生成器。
        
        Args:
            llm_client: LLMIntegration 实例，如果为 None 则延迟创建
        """
        self._llm = llm_client
        # 记录本次生成失败明细，供 generate() 末尾汇总进 result.errors，
        # 让外层能感知"部分文档生成失败"，避免失败被静默吞掉却标记任务 completed。
        self._failed_docs: List[str] = []
        self._last_llm_error: str = ""
        # R6 用户授权层：项目根 INSTRUCTIONS.md 解析结果（generate() 时加载）
        self._instructions: Dict[str, str] = {}

    @property
    def llm(self):
        """延迟加载 LLM 客户端"""
        if self._llm is None:
            from core.llm_integration import LLMIntegration
            self._llm = LLMIntegration()
        return self._llm

    # ─── 主入口 ───

    def generate(self, project_path: str, output_dir: str = "",
                 enable_git_hook: bool = False,
                 wiki_style: str = "comprehensive",
                 include_subprojects: bool = False,
                 cross_verify: bool = True,
                 cross_entry_spec: str = "class:pipeline_runner:Pipe",
                 enable_agent_pointer: bool = False) -> WikiResult:
        """生成项目 Wiki

        Args:
            project_path: 项目根目录
            output_dir: 输出目录，默认 {project_path}/docs/wiki/
            enable_git_hook: 是否安装 git post-commit hook
            wiki_style: Wiki 风格 (comprehensive / reference / tutorial / plain)
            include_subprojects: 是否同时为子项目生成独立 Wiki
            cross_verify: 是否对模块描述做静态交叉验证（给每篇模块文档打确证徽章）
            cross_entry_spec: 交叉验证的入口（入口调用闭包为确证依据）
            enable_agent_pointer: 是否在项目根维护 AGENTS.md 的 CodeRef Wiki 指针区块（R7）

        Returns:
            WikiResult: 生成结果
        """
        # 重置本次生成失败统计（实例可能被复用，需保证每次 generate 独立）
        self._failed_docs = []
        self._last_llm_error = ""
        project_path = os.path.abspath(project_path)
        project_name = os.path.basename(project_path)

        # 验证风格参数
        if wiki_style not in self.WIKI_STYLES:
            wiki_style = "comprehensive"

        if not output_dir:
            output_dir = os.path.join(project_path, "docs", "wiki")

        os.makedirs(output_dir, exist_ok=True)
        os.makedirs(os.path.join(output_dir, "MODULES"), exist_ok=True)

        result = WikiResult(
            project_path=project_path,
            project_name=project_name,
            output_dir=output_dir,
            wiki_style=wiki_style,
        )

        # 硬阻断：Wiki 人话文档依赖 LLM 才能产出，LLM 不可用时直接明确告知，
        # 不跑完整流程、不产出任何降级/占位内容，避免把"未生成"伪装成"已成功"。
        # 审计/知识图谱等确定性分析不受影响（由外层管线独立调度）。
        if not self.llm.is_available():
            result.errors.append(
                "Wiki 文档生成需要 LLM，但当前未配置有效的 API Key。"
                "请在配置面板填写 API Key 后再运行 coderef_docs。"
                "（审计、知识图谱等确定性分析不受影响，可正常使用）"
            )
            return result

        # R6: 用户授权层（INSTRUCTIONS.md，只读不重写，内容注入 LLM system prompt）
        self._instructions = self._load_instructions(project_path)

        # R1: 增量同步判定（存在上次状态 + git 可用 + 变更文件数在阈值内 → 增量）
        # 无 git 环境 / 无上次状态 / 变更过多时优雅降级为全量生成，不抛异常。
        incremental_mode = False
        changed_files: List[str] = []
        git_bin = self._git_bin()
        if git_bin:
            last_state = self._load_last_update(output_dir)
            if last_state and last_state.get("gitHead"):
                changed = self._git_changed_files(git_bin, project_path, last_state["gitHead"])
                if changed is None:
                    result.warnings.append("无法读取 git 变更记录，本次采用全量生成。")
                elif len(changed) <= WIKI_INCREMENTAL_MAX_CHANGED_FILES:
                    incremental_mode = True
                    changed_files = changed
                else:
                    result.warnings.append(
                        f"变更文件数 {len(changed)} 超过增量阈值 "
                        f"{WIKI_INCREMENTAL_MAX_CHANGED_FILES}，本次采用全量重建。")
        else:
            result.warnings.append("未检测到 git 环境，增量同步不可用，本次采用全量生成。")

        if incremental_mode:
            # ─── 增量路径（R1）：只重新生成受影响模块的文档，全局文档按需重建 ───
            if include_subprojects:
                result.warnings.append(
                    "增量同步模式暂不处理子项目，如需更新子项目请使用全量生成。")
            docs = self._incremental_update(
                project_path, output_dir, changed_files, wiki_style,
                cross_verify, cross_entry_spec, result,
            )
            result.documents = docs
        else:
            # ─── 全量路径 ───

            # 0. 发现子项目
            if include_subprojects:
                subprojects = self._discover_subprojects(project_path)
                result.subprojects = subprojects

            # 1. 发现模块
            modules = self._discover_modules(project_path)
            result.total_files = sum(m.file_count for m in modules)

            if not modules:
                result.errors.append("未发现任何 Python 模块")
                return result

            # 2. 判断大仓库模式（旧采样保留用于模块发现，元数据不采样）
            if result.total_files > self.LARGE_REPO_THRESHOLD:
                result.large_repo = True
                modules = self._sample_large_repo(modules)

            # ─── 三级管线 ───

            # Stage 1: 全量代码元数据提取（AST，无 LLM）
            code_metadata = self._build_code_metadata(modules, project_path)

            # 超大仓库时对元数据采样降级（但仍比原始代码采样损失小得多）
            if result.large_repo:
                code_metadata = self._sample_metadata(code_metadata)

            # Stage 2: LLM 逐模块归纳描述（从全量元数据写，不丢失信息）
            module_descriptions = self._generate_module_descriptions(code_metadata, wiki_style)

            # Stage 2.5: 静态交叉验证（可选，熔断降级）
            # 给每篇模块文档打"确证徽章"，让非技术人员能区分"确被调用"与"LLM 推测"。
            # 纯静态、确定性，不依赖 LLM；图谱缺失/入口未命中时静默跳过，不阻断生成。
            cross_badges = {}
            if cross_verify:
                cross_badges = self._cross_verify_modules(
                    project_path, modules, cross_entry_spec)

            # Stage 3: LLM 生成各文档（用 Stage 2 的输出，而非原始代码摘要）
            docs = self._generate_all_documents(
                project_name, modules, code_metadata, module_descriptions,
                output_dir, result, cross_badges,
            )
            result.documents = docs

            # 5. 子项目 Wiki（同样使用三级管线）
            if include_subprojects and subprojects:
                for sub_path in subprojects:
                    sub_name = os.path.basename(sub_path)
                    sub_output = os.path.join(output_dir, "subprojects", sub_name)
                    os.makedirs(sub_output, exist_ok=True)
                    os.makedirs(os.path.join(sub_output, "MODULES"), exist_ok=True)

                    sub_result = WikiResult(
                        project_path=sub_path,
                        project_name=sub_name,
                        output_dir=sub_output,
                        wiki_style=wiki_style,
                    )

                    sub_modules = self._discover_modules(sub_path)
                    sub_result.total_files = sum(m.file_count for m in sub_modules)

                    if sub_modules:
                        if sub_result.total_files > self.LARGE_REPO_THRESHOLD:
                            sub_result.large_repo = True
                            sub_modules = self._sample_large_repo(sub_modules)

                        # Stage 1
                        sub_meta = self._build_code_metadata(sub_modules, sub_path)
                        if sub_result.large_repo:
                            sub_meta = self._sample_metadata(sub_meta)
                        # Stage 2
                        sub_descriptions = self._generate_module_descriptions(sub_meta, wiki_style)
                        # Stage 3
                        sub_docs = self._generate_all_documents(
                            sub_name, sub_modules, sub_meta, sub_descriptions,
                            sub_output, sub_result,
                        )
                        sub_result.documents = sub_docs
                        sub_result.module_count = sum(1 for d in sub_docs if "MODULES" in d)

                    result.subproject_results.append({
                        "name": sub_name,
                        "path": sub_path,
                        "documents": len(sub_result.documents),
                        "total_files": sub_result.total_files,
                        "large_repo": sub_result.large_repo,
                    })

        # 6. Git hook 配置
        if enable_git_hook:
            self._setup_git_hook(project_path, output_dir)

        # R7: Agent 指针集成（在项目根维护 AGENTS.md 的 CodeRef Wiki 指针区块）
        if enable_agent_pointer:
            self._write_agent_pointer(project_path, output_dir)

        # 汇总本次生成失败（主项目 + 子项目），让调用方感知"部分文档生成失败"
        if self._failed_docs:
            result.errors.append(
                f"以下 {len(self._failed_docs)} 个文档生成失败（LLM 返回空内容），未落盘: "
                f"{', '.join(self._failed_docs[:20])}"
                + (" ..." if len(self._failed_docs) > 20 else "")
            )
        if self._last_llm_error:
            result.errors.append(f"LLM 调用异常: {self._last_llm_error}")

        # R1: 生成结束后更新增量同步状态文件（写当前 HEAD；仅无错误时更新，
        # 避免把"生成失败"误记为"已同步到 HEAD"导致下次增量漏更）。
        # 无 git 环境时 gitHead 记为 null，状态文件仍生成（记录更新时间/文档数）。
        if not result.errors:
            head = self._git_head(git_bin, project_path) if git_bin else None
            self._save_last_update(output_dir, head, len(result.documents))

        # R3: Last-good 门控（本次无错误则备份当前产物；有错误保留上次可用版本）
        if not result.errors:
            self._save_last_good(output_dir)
        else:
            result.warnings.append("本次生成未通过校验，已保留上次可用版本（.last-good）。")

        return result

    # ─── 增量同步（R1）───

    def _git_bin(self) -> Optional[str]:
        """探测 git 可执行文件路径（参考 operation_memory 的做法）。

        先在 PATH 中查找；找不到再在常见便携根探测（PortableGit 等不在 PATH 的
        便携包，Windows 上很常见）。仍找不到返回 None，调用方据此降级为全量生成。
        """
        try:
            p = shutil.which("git")
            if p:
                return os.path.abspath(p)
        except Exception:
            pass
        # 便携根探测：复用 operation_memory 的便携根 / bin 子目录配置
        try:
            import glob
            roots = getattr(_settings, "OMEM_ENV_TOOL_ROOTS", ()) if _settings else ()
            subdirs = getattr(_settings, "OMEM_ENV_TOOL_BIN_SUBDIRS",
                              ("bin", "cmd", "mingw64/bin", "usr/bin")) if _settings else ("bin", "cmd")
            for root_pat in roots:
                try:
                    matches = glob.glob(os.path.expanduser(root_pat))
                except Exception:
                    continue
                for root in matches:
                    if not os.path.isdir(root):
                        continue
                    for sub in subdirs:
                        cand = os.path.join(root, sub.replace("/", os.sep), "git.exe")
                        if os.path.isfile(cand):
                            return os.path.abspath(cand)
        except Exception:
            pass
        return None

    def _git_head(self, git_bin: str, project_path: str) -> Optional[str]:
        """返回当前 HEAD 短哈希；非 git 仓库或命令失败返回 None。"""
        try:
            out = subprocess.run(
                [git_bin, "rev-parse", "--short", "HEAD"],
                cwd=project_path, capture_output=True, text=True, timeout=15,
            )
            if out.returncode == 0:
                return out.stdout.strip() or None
        except Exception:
            pass
        return None

    def _git_last_commit(self, git_bin: str, project_path: str, file_path: str) -> str:
        """返回某文件最近一次提交的短哈希；失败返回空串（调用方据此降级）。"""
        try:
            out = subprocess.run(
                [git_bin, "log", "-1", "--format=%h", "--", file_path],
                cwd=project_path, capture_output=True, text=True, timeout=15,
            )
            if out.returncode == 0:
                return out.stdout.strip() or ""
        except Exception:
            pass
        return ""

    def _git_changed_files(self, git_bin: str, project_path: str,
                           last_head: str) -> Optional[List[str]]:
        """返回 last_head..HEAD 之间的变更文件相对路径列表。

        非 git 仓库 / last_head 无效 / 命令失败返回 None（调用方降级全量）。
        """
        try:
            out = subprocess.run(
                [git_bin, "log", f"{last_head}..HEAD", "--name-only", "--pretty=format:"],
                cwd=project_path, capture_output=True, text=True, timeout=30,
            )
            if out.returncode != 0:
                return None
            files = []
            for line in out.stdout.splitlines():
                line = line.strip()
                if line and line not in files:
                    files.append(line)
            return files
        except Exception:
            return None

    def _load_last_update(self, output_dir: str) -> Optional[dict]:
        """读取增量同步状态文件 .last-update.json；不存在或损坏返回 None。"""
        path = os.path.join(output_dir, WIKI_LAST_UPDATE_FILE)
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return None

    def _save_last_update(self, output_dir: str, git_head: Optional[str],
                          doc_count: int) -> None:
        """写增量同步状态文件（当前 HEAD、更新时间、文档数）；失败静默。"""
        path = os.path.join(output_dir, WIKI_LAST_UPDATE_FILE)
        data = {
            "gitHead": git_head,
            "updatedAt": datetime.now().isoformat(timespec="seconds"),
            "docCount": doc_count,
        }
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    def _incremental_update(self, project_path: str, output_dir: str,
                            changed_files: List[str], wiki_style: str,
                            cross_verify: bool, cross_entry_spec: str,
                            result: WikiResult) -> List[str]:
        """R1 增量同步：只重新生成受影响模块的 MODULES 文档，全局文档按需重建。

        调用方已判定变更文件数在阈值内；本方法对比变更文件所属模块，
        仅对受影响模块重新生成模块文档；全局文档（README/ARCHITECTURE 等）
        在存在代码变更时重新生成。无 git 环境时调用方已降级为全量，不会进入本方法。
        """
        project_name = os.path.basename(project_path)
        modules = self._discover_modules(project_path)
        result.total_files = sum(m.file_count for m in modules)
        if not modules:
            result.errors.append("未发现任何 Python 模块")
            return []
        if result.total_files > self.LARGE_REPO_THRESHOLD:
            result.large_repo = True
            modules = self._sample_large_repo(modules)

        # 计算受影响模块：变更文件（相对路径）所属的模块
        changed_set = {os.path.normpath(c) for c in changed_files}
        affected = set()
        for mod in modules:
            for f in mod.py_files:
                rel = os.path.normpath(os.path.relpath(f, project_path))
                if rel in changed_set:
                    affected.add(mod.name)

        # Stage 1: 全量 AST 元数据（无 LLM，成本低）
        code_metadata = self._build_code_metadata(modules, project_path)
        if result.large_repo:
            code_metadata = self._sample_metadata(code_metadata)

        # Stage 2: 模块描述（走缓存，未命中则全量归纳）
        module_descriptions = self._generate_module_descriptions(code_metadata, wiki_style)

        # Stage 2.5: 静态交叉验证（可选，熔断降级）
        cross_badges = {}
        if cross_verify:
            cross_badges = self._cross_verify_modules(
                project_path, modules, cross_entry_spec)

        # 只重新生成受影响模块的 MODULES 文档（跳过全局文档）
        docs = self._generate_all_documents(
            project_name, modules, code_metadata, module_descriptions,
            output_dir, result, cross_badges,
            affected_modules=sorted(affected),
            skip_global=True,
        )

        # 全局文档：存在代码变更时重建（README/ARCHITECTURE 等描述可能过时）
        if changed_files:
            cite_warnings: List[str] = []
            global_docs = self._generate_global_docs(
                project_name, modules, code_metadata, module_descriptions,
                output_dir, result, cite_warnings,
            )
            docs.extend(global_docs)
            if cite_warnings:
                result.errors.extend(cite_warnings)

        return docs

    # ─── 模块发现 ───

    def _discover_modules(self, project_path: str) -> List[WikiModule]:
        """发现项目中的 Python 模块"""
        modules = []

        # 先检查根目录下的 .py 文件
        root_py_files = []
        for entry in os.scandir(project_path):
            if entry.is_file() and entry.name.endswith(".py") and not entry.name.startswith("_"):
                root_py_files.append(entry.path)

        if root_py_files:
            modules.append(WikiModule(
                name="root",
                path=project_path,
                py_files=root_py_files,
                file_count=len(root_py_files),
                is_core=True,
            ))

        # 再检查子目录
        for entry in os.scandir(project_path):
            if not entry.is_dir():
                continue
            if entry.name.startswith(".") or entry.name.startswith("_"):
                continue
            if entry.name in self.EXCLUDE_DIRS:
                continue
            if entry.name.startswith(("Python3.", "Python2.", "pypy", ".git")):
                continue

            py_files = self._collect_py_files(entry.path)
            if py_files:
                # 加载可配置的核心模块判定规则
                core_rules = self._load_core_rules(project_path)
                entry_files = core_rules.get("entry_files", self.DEFAULT_ENTRY_FILES)
                core_names = core_rules.get("core_names", [])
                min_files = core_rules.get("min_files", self.DEFAULT_MIN_FILES)

                # 判断是否核心模块：
                # 1. 模块名在 AI 指定的 core_names 中
                # 2. 包含入口文件（可配置列表）
                # 3. 文件数量 >= min_files 阈值
                is_core = (
                    entry.name in core_names or
                    any(os.path.basename(f) in entry_files for f in py_files) or
                    len(py_files) >= min_files
                )
                modules.append(WikiModule(
                    name=entry.name,
                    path=entry.path,
                    py_files=py_files,
                    file_count=len(py_files),
                    is_core=is_core,
                ))

        return modules

    def _collect_py_files(self, dir_path: str) -> List[str]:
        """收集目录下的 Python 文件"""
        py_files = []
        for root, dirs, files in os.walk(dir_path):
            dirs[:] = [d for d in dirs if not d.startswith(".") and d not in self.EXCLUDE_DIRS]
            for f in files:
                if f.endswith(".py") and not f.startswith("_"):
                    py_files.append(os.path.join(root, f))
            if len(py_files) > self.MAX_FILES_PER_MODULE:
                break
        return py_files

    def _discover_subprojects(self, project_path: str) -> List[str]:
        """发现大项目中的子项目（monorepo 支持）

        检测子目录中是否包含独立的依赖文件（requirements.txt /
        pyproject.toml / setup.py），如果有则视为独立子项目。
        限定深度为 2 层，避免过度递归。
        """
        subprojects = []
        max_depth = 2

        def _scan(dir_path: str, depth: int):
            if depth > max_depth:
                return
            try:
                for entry in os.scandir(dir_path):
                    if not entry.is_dir():
                        continue
                    if entry.name.startswith(".") or entry.name.startswith("_"):
                        continue
                    if entry.name in self.EXCLUDE_DIRS:
                        continue

                    # 检查是否为子项目
                    for indicator in self.SUBPROJECT_INDICATORS:
                        if os.path.isfile(os.path.join(entry.path, indicator)):
                            subprojects.append(entry.path)
                            break
                    else:
                        # 不是子项目，继续深入
                        _scan(entry.path, depth + 1)
            except PermissionError:
                pass

        _scan(project_path, 1)
        return subprojects

    # ═══════════════════════════════════════════════════════════════════
    # 三级管线：Stage 1 — 全量代码元数据提取（AST，无 LLM）
    # ═══════════════════════════════════════════════════════════════════

    def _coderef_cache_dir(self) -> str:
        """返回 CodeRef 自身的 cache 目录（用于存元数据，不污染目标项目）"""
        script_dir = os.path.dirname(os.path.abspath(__file__))
        cache_dir = os.path.join(os.path.dirname(script_dir), "cache", "wiki_cache")
        os.makedirs(cache_dir, exist_ok=True)
        return cache_dir

    def _project_cache_path(self, project_path: str) -> str:
        """返回某项目的 cache 子目录"""
        h = hashlib.md5(project_path.encode("utf-8")).hexdigest()[:8]
        p = os.path.join(self._coderef_cache_dir(), h)
        os.makedirs(p, exist_ok=True)
        return p

    def _build_code_metadata(self, modules: List[WikiModule], project_path: str,
                             skip_cache: bool = False) -> ProjectCodeMetadata:
        """Stage 1：对全部 .py 文件执行 AST 扫描，输出结构化元数据

        - 不采样、不截断，所有文件全部扫描
        - 元数据约为原始代码 5-10% 体积
        - 缓存到 CodeRef cache 目录，重复调用不重复扫描
        """
        cache_dir = self._project_cache_path(project_path)
        cache_file = os.path.join(cache_dir, "stage1_metadata.json")

        # 如果缓存存在且有效，直接加载
        if not skip_cache and os.path.exists(cache_file):
            try:
                with open(cache_file, "r", encoding="utf-8") as f:
                    raw = json.load(f)
                meta = self._metadata_from_dict(raw)
                if meta and len(meta.modules) == len(modules):
                    return meta
            except Exception:
                pass

        web_framework_kws = {"fastapi", "django", "flask", "sanic", "tornado",
                             "aiohttp", "starlette", "bottle", "falcon"}
        has_web = False
        mod_metas: List[ModuleCodeMetadata] = []

        for mod in modules:
            files_meta: List[CodeFileMetadata] = []
            for fpath in sorted(mod.py_files):
                rel = os.path.relpath(fpath, mod.path)
                try:
                    with open(fpath, "r", encoding="utf-8", errors="ignore") as f:
                        content = f.read()
                except (OSError, IOError):
                    continue

                fm = self._extract_file_metadata(content, rel)
                files_meta.append(fm)

                # 检查 Web 框架
                if not has_web:
                    for imp in fm.imports:
                        if imp.lower() in web_framework_kws:
                            has_web = True
                            break

            mod_metas.append(ModuleCodeMetadata(
                name=mod.name,
                path=mod.path,
                files=files_meta,
                total_files=len(files_meta),
            ))

        meta = ProjectCodeMetadata(
            project_path=project_path,
            modules=mod_metas,
            total_files=sum(m.total_files for m in mod_metas),
            has_web_framework=has_web,
        )

        # 缓存
        try:
            with open(cache_file, "w", encoding="utf-8") as f:
                json.dump(self._metadata_to_dict(meta), f, ensure_ascii=False, indent=1)
        except Exception:
            pass

        return meta

    def _extract_file_metadata(self, content: str, rel_path: str) -> CodeFileMetadata:
        """从文件内容中提取结构化元数据（AST 扫描）"""
        fm = CodeFileMetadata(rel_path=rel_path)

        try:
            tree = ast.parse(content)

            # docstring
            doc = ast.get_docstring(tree)
            if doc:
                fm.docstring = doc.strip()[:300]

            # 类
            for node in ast.walk(tree):
                if isinstance(node, ast.ClassDef):
                    bases = []
                    for b in node.bases:
                        if isinstance(b, ast.Name):
                            bases.append(b.id)
                        elif isinstance(b, ast.Attribute):
                            bases.append(b.attr)
                        elif isinstance(b, ast.Subscript) and isinstance(b.value, ast.Name):
                            bases.append(b.value.id)
                        else:
                            bases.append("?")
                    cls_doc = ast.get_docstring(node) or ""
                    methods = [
                        n.name for n in node.body
                        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                        and not n.name.startswith("_")
                    ]
                    fm.classes.append({
                        "name": node.name,
                        "bases": bases,
                        "doc": cls_doc.strip()[:200],
                        "methods": methods[:10],  # 只保留前 10 个
                    })

            # 顶层函数
            for node in ast.iter_child_nodes(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    func_doc = ast.get_docstring(node) or ""
                    params = [arg.arg for arg in node.args.args[:8]]
                    fm.functions.append({
                        "name": node.name,
                        "params": params,
                        "doc": func_doc.strip()[:200],
                    })

            # import 依赖
            imports = set()
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        imports.add(alias.name.split(".")[0])
                elif isinstance(node, ast.ImportFrom):
                    if node.module:
                        imports.add(node.module.split(".")[0])
            fm.imports = sorted(imports)

            # 是否有 main 入口
            for node in ast.iter_child_nodes(tree):
                if isinstance(node, ast.If):
                    if (isinstance(node.test, ast.Compare)
                            and isinstance(node.test.left, ast.Name)
                            and node.test.left.id == "__name__"):
                        fm.has_main_block = True
                        break

            # 是否入口文件
            base = os.path.basename(rel_path)
            fm.is_entry_point = base in ("main.py", "app.py", "server.py", "run.py")

        except SyntaxError:
            pass

        return fm

    def _metadata_to_dict(self, meta: ProjectCodeMetadata) -> Dict:
        """ProjectCodeMetadata → dict（用于 JSON 缓存）"""
        return {
            "project_path": meta.project_path,
            "total_files": meta.total_files,
            "has_web_framework": meta.has_web_framework,
            "modules": [
                {
                    "name": m.name,
                    "path": m.path,
                    "total_files": m.total_files,
                    "files": [
                        {
                            "rel_path": f.rel_path,
                            "docstring": f.docstring,
                            "classes": f.classes,
                            "functions": f.functions,
                            "imports": f.imports,
                            "has_main_block": f.has_main_block,
                            "is_entry_point": f.is_entry_point,
                        }
                        for f in m.files
                    ],
                }
                for m in meta.modules
            ],
        }

    def _metadata_from_dict(self, d: Dict) -> Optional[ProjectCodeMetadata]:
        """dict → ProjectCodeMetadata（从 JSON 缓存恢复）"""
        try:
            modules = []
            for md in d.get("modules", []):
                files = [
                    CodeFileMetadata(**ff)
                    for ff in md.get("files", [])
                ]
                modules.append(ModuleCodeMetadata(
                    name=md["name"],
                    path=md["path"],
                    files=files,
                    total_files=md.get("total_files", len(files)),
                ))
            return ProjectCodeMetadata(
                project_path=d.get("project_path", ""),
                modules=modules,
                total_files=d.get("total_files", 0),
                has_web_framework=d.get("has_web_framework", False),
            )
        except Exception:
            return None

    # ═══════════════════════════════════════════════════════════════════
    # 三级管线：Stage 1b — 采样（超大仓库时元数据降级）
    # ═══════════════════════════════════════════════════════════════════

    METADATA_MAX_FILES = 500   # 单个模块元数据文件上限
    METADATA_MAX_CLASSES = 20  # 单个文件最多保留的类
    METADATA_MAX_FUNCS = 30    # 单个文件最多保留的函数

    def _sample_metadata(self, meta: ProjectCodeMetadata) -> ProjectCodeMetadata:
        """超大项目时对元数据进行采样（比采样原始代码损失小得多）"""
        for mod in meta.modules:
            if mod.total_files > self.METADATA_MAX_FILES:
                mod.files = mod.files[:self.METADATA_MAX_FILES]
            for f in mod.files:
                if len(f.classes) > self.METADATA_MAX_CLASSES:
                    f.classes = f.classes[:self.METADATA_MAX_CLASSES]
                if len(f.functions) > self.METADATA_MAX_FUNCS:
                    f.functions = f.functions[:self.METADATA_MAX_FUNCS]
        return meta

    # ═══════════════════════════════════════════════════════════════════
    # 三级管线：Stage 2 — LLM 写模块描述（从全量元数据归纳）
    # ═══════════════════════════════════════════════════════════════════

    def _generate_module_descriptions(self, meta: ProjectCodeMetadata,
                                       style: str) -> Dict[str, str]:
        """Stage 2：对每个模块，LLM 从全量元数据归纳出模块描述

        元数据比原始代码紧凑 10-20 倍，因此 LLM 可以看到所有文件。
        输出缓存到 CodeRef cache 避免重复调用。
        """
        cache_dir = self._project_cache_path(meta.project_path)
        cache_file = os.path.join(cache_dir, f"stage2_descriptions_{style}.json")
        if os.path.exists(cache_file):
            try:
                with open(cache_file, "r", encoding="utf-8") as f:
                    cached = json.load(f)
                if len(cached) == len(meta.modules):
                    return cached
            except Exception:
                pass

        # 生成项目级概览元数据（紧凑形式）
        overview_lines = [f"项目: {os.path.basename(meta.project_path)}"]
        overview_lines.append(f"总文件数: {meta.total_files}, 模块数: {len(meta.modules)}")
        overview_lines.append(f"Web 框架: {'检测到' if meta.has_web_framework else '未检测到'}")
        overview_lines.append("")

        for mod in meta.modules:
            # 计算元数据行数判断是否超限
            md_text = self._module_metadata_to_text(mod)
            overview_lines.append(f"--- 模块: {mod.name} ({mod.total_files} 文件) ---")
            overview_lines.append(md_text)
            overview_lines.append("")

        full_metadata = "\n".join(overview_lines)

        guidelines = self._style_guidelines(style)
        descriptions = {}

        for mod in meta.modules:
            md_text = self._module_metadata_to_text(mod)

            # 风格区分
            if style in ("reference", "plain"):
                # 硬核/极简：直接让 LLM 从元数据浓缩
                system_prompt = (
                    f"你是一个代码分析助手。基于下方元数据，用中文简要描述此模块。"
                    f"{guidelines}"
                    "输出纯 Markdown，直接列出关键信息。"
                )
            else:
                # comprehensive / tutorial：归纳为通俗语言
                system_prompt = (
                    f"你是一个代码分析助手。基于下方元数据，用通俗语言描述此模块。"
                    f"{guidelines}"
                    "要求：基于事实归纳，不要虚构任何类/函数/依赖。"
                    "输出纯 Markdown。"
                )

            user_prompt = (
                f"请描述模块 **{mod.name}** ({mod.total_files} 个 Python 文件, {len(md_text)} 字符元数据)。\n\n"
                f"元数据：\n```\n{md_text[:20000]}\n```\n\n"
                f"输出模块描述（不要包含 '基于元数据' 之类的前缀，直接输出内容）。"
            )

            desc = self._llm_ask(system_prompt, user_prompt)
            descriptions[mod.name] = desc

        # 缓存
        try:
            with open(cache_file, "w", encoding="utf-8") as f:
                json.dump(descriptions, f, ensure_ascii=False, indent=1)
        except Exception:
            pass

        return descriptions

    def _module_metadata_to_text(self, mod: ModuleCodeMetadata) -> str:
        """将模块元数据转为紧凑文本"""
        lines = []
        for f in mod.files:
            parts = [f"## {f.rel_path}"]
            if f.docstring:
                parts.append(f"  doc: {f.docstring[:200]}")
            if f.is_entry_point:
                parts.append(f"  [入口文件]")
            if f.classes:
                for c in f.classes:
                    bases = f"({', '.join(c['bases'])})" if c["bases"] else ""
                    doc = f": {c['doc']}" if c["doc"] else ""
                    methods = f" [方法: {', '.join(c['methods'][:6])}]" if c["methods"] else ""
                    parts.append(f"  class {c['name']}{bases}{doc}{methods}")
            if f.functions:
                for fn in f.functions:
                    params = f"({', '.join(fn['params'])})" if fn["params"] else "()"
                    doc = f": {fn['doc']}" if fn["doc"] else ""
                    parts.append(f"  def {fn['name']}{params}{doc}")
            if f.imports:
                parts.append(f"  依赖: {', '.join(f.imports[:10])}")
            lines.extend(parts)
        return "\n".join(lines)

    # ─── 旧版代码摘要（保留作为回退） ───

    def _sample_large_repo(self, modules: List[WikiModule]) -> List[WikiModule]:
        """大仓库采样：优先核心模块，非核心模块采样"""
        # 核心模块优先保留全部
        core_modules = [m for m in modules if m.is_core]
        non_core = [m for m in modules if not m.is_core]

        import random
        random.seed(42)  # 确定性采样

        # 对非核心模块采样
        for m in non_core:
            if len(m.py_files) > 15:
                m.py_files = random.sample(m.py_files, 15)
                m.file_count = len(m.py_files)

        # 如果总文件数还是太多，对核心模块也采样
        total = sum(m.file_count for m in modules)
        if total > self.LARGE_REPO_SAMPLE:
            for m in core_modules:
                if len(m.py_files) > 20:
                    m.py_files = random.sample(m.py_files, 20)
                    m.file_count = len(m.py_files)

        return modules

    # ─── 代码摘要收集（旧版，保留回退） ───

    def _collect_code_summaries(self, modules: List[WikiModule]) -> Dict[str, str]:
        """收集每个模块的代码摘要文本（供 LLM 分析）"""
        summaries = {}

        for mod in modules:
            parts = [f"## 模块: {mod.name}\n"]
            parts.append(f"文件数: {mod.file_count}\n")

            for fpath in sorted(mod.py_files)[:20]:
                rel = os.path.relpath(fpath, mod.path)
                try:
                    with open(fpath, "r", encoding="utf-8", errors="ignore") as f:
                        content = f.read()
                except (OSError, IOError):
                    continue

                # 提取关键信息
                info = self._extract_file_info(content, rel)
                parts.append(info)

                # 控制总长度
                if sum(len(p) for p in parts) > self.MAX_CONTEXT_CHARS:
                    parts.append(f"\n...(还有 {len(mod.py_files) - 20} 个文件未列出)\n")
                    break

            summaries[mod.name] = "\n".join(parts)

        return summaries

    def _extract_file_info(self, content: str, rel_path: str) -> str:
        """从文件内容中提取关键信息"""
        lines = [f"\n### `{rel_path}`\n"]

        try:
            tree = ast.parse(content)

            # 模块 docstring
            doc = ast.get_docstring(tree)
            if doc:
                lines.append(f"**用途**: {doc.strip()[:200]}\n")

            # 类定义
            classes = []
            for node in ast.walk(tree):
                if isinstance(node, ast.ClassDef):
                    bases = [
                        b.id if isinstance(b, ast.Name)
                        else b.attr if isinstance(b, ast.Attribute)
                        else str(b)
                        for b in node.bases
                    ]
                    doc = ast.get_docstring(node)
                    cls_info = f"- **class `{node.name}`**"
                    if bases:
                        cls_info += f"({', '.join(bases)})"
                    if doc:
                        cls_info += f": {doc.strip()[:100]}"
                    # 公开方法
                    public_methods = [
                        n.name for n in node.body
                        if isinstance(n, ast.FunctionDef) and not n.name.startswith("_")
                    ]
                    if public_methods:
                        cls_info += f" (方法: {', '.join(public_methods[:5])})"
                    classes.append(cls_info)

            if classes:
                lines.append("**类**:")
                lines.extend(classes[:5])
                if len(classes) > 5:
                    lines.append(f"  ...还有 {len(classes) - 5} 个类")
                lines.append("")

            # 函数定义
            functions = []
            for node in ast.iter_child_nodes(tree):
                if isinstance(node, ast.FunctionDef) and not node.name.startswith("_"):
                    doc = ast.get_docstring(node)
                    func_info = f"- **`{node.name}()`**"
                    if doc:
                        func_info += f": {doc.strip()[:100]}"
                    functions.append(func_info)

            if functions:
                lines.append("**函数**:")
                lines.extend(functions[:8])
                if len(functions) > 8:
                    lines.append(f"  ...还有 {len(functions) - 8} 个函数")
                lines.append("")

            # import 依赖
            imports = []
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        imports.append(alias.name.split(".")[0])
                elif isinstance(node, ast.ImportFrom):
                    if node.module:
                        imports.append(node.module.split(".")[0])

            if imports:
                unique_imports = list(set(imports))[:10]
                lines.append(f"**依赖**: {', '.join(unique_imports)}")
                lines.append("")

        except SyntaxError:
            lines.append("(*无法解析 AST，可能包含语法错误*)\n")

        return "\n".join(lines)

    # ─── LLM 文档生成 ───

    def _generate_all_documents(self, project_name: str, modules: List[WikiModule],
                                 meta: ProjectCodeMetadata,
                                 descriptions: Dict[str, str],
                                 output_dir: str,
                                 result: WikiResult,
                                 cross_badges: Dict[str, dict] = None,
                                 affected_modules: Optional[List[str]] = None,
                                 skip_global: bool = False) -> List[str]:
        """生成所有 Wiki 文档

        顺序优化：先逐模块 → 再合并产出跨模块文档
        每篇文档生成后执行 cite-verify，未通过的再由 LLM 修复

        Args:
            affected_modules: 非空时只生成这些模块的 MODULES 文档（R1 增量同步用）
            skip_global: True 时跳过全局文档（README/ARCHITECTURE 等），
                         由调用方单独调用 _generate_global_docs（R1 增量路径）
        """
        docs = []
        style = result.wiki_style
        cite_warnings: List[str] = []
        cross_badges = cross_badges or {}

        # =============================================================
        # 第一轮：逐模块文档（MODULES/*.md）
        # =============================================================
        module_docs = self._generate_module_docs(modules, descriptions, output_dir,
                                                 style, meta, cross_badges,
                                                 affected_modules=affected_modules)
        docs.extend(module_docs)
        result.module_count = len(module_docs)

        # cite-verify 模块文档
        for doc_path in module_docs:
            doc_name = os.path.basename(doc_path)
            try:
                content = open(doc_path, encoding="utf-8").read()
            except Exception:
                continue
            uv = self._cite_verify(content, meta, doc_name)
            if uv:
                fixed = self._cite_fix(content, doc_name, uv, meta)
                if fixed and len(fixed) > 100:
                    with open(doc_path, "w", encoding="utf-8") as f:
                        f.write(fixed)
                cite_warnings.append(f"- {doc_name}: 修复了 {len(uv)} 个未验证标识符: {', '.join(uv[:8])}")

        # =============================================================
        # 第二轮：跨模块文档（README、ARCHITECTURE 等）
        # =============================================================
        if not skip_global:
            docs.extend(self._generate_global_docs(
                project_name, modules, meta, descriptions, output_dir, result,
                cite_warnings))

        # 记录编校警告到 result
        if cite_warnings:
            result.errors.extend(cite_warnings)

        return docs

    def _generate_global_docs(self, project_name: str, modules: List[WikiModule],
                              meta: ProjectCodeMetadata,
                              descriptions: Dict[str, str],
                              output_dir: str, result: WikiResult,
                              cite_warnings: List[str] = None) -> List[str]:
        """生成跨模块全局文档（README/OVERVIEW/ARCHITECTURE/INSTALLATION/USAGE/API/WIKI_INDEX 等）。

        从 _generate_all_documents 的第二轮提取，供 R1 增量同步单独调用全局文档重建。
        cite_warnings 为共享列表（引用传递），编校警告统一由调用方汇总。
        """
        docs = []
        style = result.wiki_style
        cite_warnings = cite_warnings if cite_warnings is not None else []
        project_summary = self._build_project_summary(project_name, modules, descriptions)

        # 1. README.md（元数据作为事实源，描述作为风格参考）
        readme = self._generate_readme(project_name, project_summary, modules, style,
                                       descriptions, meta)
        uv = self._cite_verify(readme, meta, "README.md")
        if uv:
            readme = self._cite_fix(readme, "README.md", uv, meta)
            cite_warnings.append(f"- README.md: 修复了 {len(uv)} 个未验证标识符: {', '.join(uv[:8])}")
        self._emit(docs, output_dir, "README.md", readme)

        # 2. OVERVIEW.md（业务视角，面向非技术读者，与技术文档分层）
        overview = self._generate_overview(project_name, modules, style,
                                           descriptions, meta)
        uv = self._cite_verify(overview, meta, "OVERVIEW.md")
        if uv:
            overview = self._cite_fix(overview, "OVERVIEW.md", uv, meta)
            cite_warnings.append(f"- OVERVIEW.md: 修复了 {len(uv)} 个未验证标识符")
        self._emit(docs, output_dir, "OVERVIEW.md", overview)

        # 2.5 分层人话版：入口级(L1) + 数据流级(L2)
        # 面向更大规模项目：按入口/数据流分块喂 LLM（避免 token 爆炸），
        # 实证绑定抗幻想（只翻译图谱调用链/数据流边，不编造）。图谱缺失时降级。
        project_path = getattr(meta, "project_path", "") or ""
        if project_path:
            # 只定位一次图谱、构造单个 FlowVerifier 复用，避免每个入口重复加载全图
            fv = None
            db = self._locate_kg_db(project_path)
            if db:
                try:
                    from core.flow_verify import FlowVerifier
                    fv = FlowVerifier(db)
                except Exception:
                    fv = None
            entries = self._discover_entry_points(meta, fv)
            if len(entries) > self.MAX_ENTRY_DOCS:
                result.warnings.append(
                    f"检测到 {len(entries)} 个入口，仅生成前 {self.MAX_ENTRY_DOCS} 篇入口文档"
                    f"（可分批生成或调整内部上限）")
                entries = entries[:self.MAX_ENTRY_DOCS]
            for entry in entries:
                chain = self._extract_entry_chain(entry, fv)
                edoc = self._generate_entry_doc(project_name, entry, chain, style, meta)
                uv = self._cite_verify(edoc, meta, f"ENTRIES/{entry['key']}.md")
                if uv:
                    edoc = self._cite_fix(edoc, f"ENTRIES/{entry['key']}.md", uv, meta)
                    cite_warnings.append(f"- ENTRIES/{entry['key']}.md: 修复了 {len(uv)} 个未验证标识符")
                os.makedirs(os.path.join(output_dir, "ENTRIES"), exist_ok=True)
                fp = self._write_doc(output_dir, f"ENTRIES/{entry['key']}.md", edoc)
                if fp:
                    docs.append(fp)

            flows = self._extract_cross_module_flows(fv)
            if len(flows) > self.MAX_FLOW_DOCS:
                result.warnings.append(
                    f"检测到 {len(flows)} 条数据流，仅生成前 {self.MAX_FLOW_DOCS} 条"
                    f"（可按调用热度人工挑选关键链路，或分批生成）")
                flows = flows[:self.MAX_FLOW_DOCS]
            for flow in flows:
                fs = self._sanitize_doc_name(flow["source"])
                ft = self._sanitize_doc_name(flow["target"])
                fdoc = self._generate_flow_doc(project_name, flow, style)
                uv = self._cite_verify(fdoc, meta, f"FLOWS/{fs}__{ft}.md")
                if uv:
                    fdoc = self._cite_fix(fdoc, f"FLOWS/{fs}__{ft}.md", uv, meta)
                    cite_warnings.append(f"- FLOWS/{fs}__{ft}.md: 修复了 {len(uv)} 个未验证标识符")
                os.makedirs(os.path.join(output_dir, "FLOWS"), exist_ok=True)
                fp = self._write_doc(output_dir, f"FLOWS/{fs}__{ft}.md", fdoc)
                if fp:
                    docs.append(fp)

        # 3. ARCHITECTURE.md
        arch = self._generate_architecture(project_name, project_summary, modules, style,
                                           descriptions, meta)
        uv = self._cite_verify(arch, meta, "ARCHITECTURE.md")
        if uv:
            arch = self._cite_fix(arch, "ARCHITECTURE.md", uv, meta)
            cite_warnings.append(f"- ARCHITECTURE.md: 修复了 {len(uv)} 个未验证标识符: {', '.join(uv[:8])}")
        self._emit(docs, output_dir, "ARCHITECTURE.md", arch)

        # 3. INSTALLATION.md
        install = self._generate_installation(project_name, project_summary, modules, style, meta)
        uv = self._cite_verify(install, meta, "INSTALLATION.md")
        if uv:
            install = self._cite_fix(install, "INSTALLATION.md", uv, meta)
            cite_warnings.append(f"- INSTALLATION.md: 修复了 {len(uv)} 个未验证标识符")
        self._emit(docs, output_dir, "INSTALLATION.md", install)

        # 4. USAGE.md
        usage = self._generate_usage(project_name, project_summary, modules, style,
                                     descriptions, meta)
        uv = self._cite_verify(usage, meta, "USAGE.md")
        if uv:
            usage = self._cite_fix(usage, "USAGE.md", uv, meta)
            cite_warnings.append(f"- USAGE.md: 修复了 {len(uv)} 个未验证标识符")
        self._emit(docs, output_dir, "USAGE.md", usage)

        # 5. API.md (如果有 Web 框架)
        if meta.has_web_framework:
            api = self._generate_api_doc(project_name, project_summary, modules, style, meta)
            uv = self._cite_verify(api, meta, "API.md")
            if uv:
                api = self._cite_fix(api, "API.md", uv, meta)
                cite_warnings.append(f"- API.md: 修复了 {len(uv)} 个未验证标识符")
            self._emit(docs, output_dir, "API.md", api)

        # 6. WIKI_INDEX.md（纯模板，无需 cite-verify）
        index = self._build_wiki_index(project_name, modules, docs, result)
        self._emit(docs, output_dir, "WIKI_INDEX.md", index)

        return docs

    def _cross_verify_modules(self, project_path: str,
                              modules: List[WikiModule],
                              entry_spec: str) -> Dict[str, dict]:
        """对 wiki 模块做静态交叉验证（熔断降级，不阻断生成）。

        结果：{模块名: {status, reason, total, in_pipe, confirmed, ...}}。
        图谱不存在 / 入口未命中 / 任何异常 → 返回空 dict（静默跳过徽章）。
        """
        try:
            from core.wiki_cross_verify import locate_kg_db, ModuleCrossVerify
            db = locate_kg_db(project_path)
            if not db:
                return {}
            v = ModuleCrossVerify(db)
            mod_names = [m.name for m in modules]
            # root 伪模块的路径是项目根目录，其目录名是项目名而非 "root"
            dir_aliases = {"root": os.path.basename(project_path.rstrip(os.sep))}
            result = v.verify_modules(mod_names, entry_spec,
                                       dir_aliases=dir_aliases)
            if not result.get("ok"):
                return {}
            # 转为 {模块名: 徽章信息}
            return {m["module"]: m for m in result.get("modules", [])}
        except Exception:
            # 交叉验证是增强项，任何失败都不应阻断 Wiki 主体生成
            return {}

    def _build_project_summary(self, project_name: str, modules: List[WikiModule],
                                summaries: Dict[str, str]) -> str:
        """构建项目摘要文本"""
        lines = [
            f"# 项目: {project_name}",
            f"",
            f"## 模块列表",
            f"",
        ]
        for mod in modules:
            core_tag = " [核心]" if mod.is_core else ""
            lines.append(f"- **{mod.name}**{core_tag}: {mod.file_count} 个文件")
        lines.append("")
        lines.append("## 代码摘要")
        lines.append("")

        # 限制总长度
        total_chars = sum(len(l) for l in lines)
        for mod_name, summary in summaries.items():
            if total_chars + len(summary) > self.MAX_CONTEXT_CHARS:
                lines.append(f"\n*(模块 {mod_name} 的摘要已省略，总共 {len(modules)} 个模块)*\n")
                break
            lines.append(summary)
            lines.append("")
            total_chars += len(summary)

        return "\n".join(lines)

    def _load_instructions(self, project_path: str) -> dict:
        """R6 读取项目根 INSTRUCTIONS.md，解析 scope/priority 等章节。

        简单解析：按行读取，识别 `## 章节名` 标题，把其下的非空行归入该章节；
        返回 {章节名(小写): 章节内容文本}。文件不存在 / 解析失败返回空 dict。
        """
        path = os.path.join(project_path, WIKI_INSTRUCTIONS_FILE)
        if not os.path.isfile(path):
            return {}
        try:
            with open(path, "r", encoding="utf-8") as f:
                lines = f.readlines()
        except Exception:
            return {}
        instructions: Dict[str, List[str]] = {}
        current = None
        for line in lines:
            stripped = line.strip()
            m = re.match(r"^##\s+(.+)$", stripped)
            if m:
                current = m.group(1).strip().lower()
                instructions.setdefault(current, [])
                continue
            if current and stripped:
                instructions[current].append(stripped)
        return {k: "\n".join(v) for k, v in instructions.items() if v}

    def _instructions_text(self) -> str:
        """把解析出的 INSTRUCTIONS.md 内容转成注入 system prompt 的文本；无内容返回空串。"""
        instructions = getattr(self, "_instructions", None) or {}
        if not instructions:
            return ""
        parts = ["=== 用户授权指令（INSTRUCTIONS.md，必须遵守）==="]
        for key, val in instructions.items():
            parts.append(f"## {key}\n{val}")
        return "\n\n".join(parts)

    def _llm_ask(self, system_prompt: str, user_prompt: str) -> str:
        """调用 LLM 生成内容。

        失败时返回空串并记录错误：空内容统一由 _write_doc 跳过落盘并计入失败统计，
        避免把"(LLM 生成失败: ...)"这类占位符当成正常文档写入，产生"看似成功实为错误"的假象。

        R6：若存在 INSTRUCTIONS.md，其内容会拼接在 system prompt 之后，
        让所有文档生成都遵循用户的 scope/优先级指令。
        """
        try:
            # R6: 注入用户授权指令（INSTRUCTIONS.md，只读不重写）
            instr = self._instructions_text()
            if instr:
                system_prompt = system_prompt.rstrip() + "\n\n" + instr
            messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ]
            return self.llm.chat_completion(messages, max_tokens=4096, temperature=0.3)
        except Exception as e:
            self._last_llm_error = str(e)
            return ""

    def _style_guidelines(self, style: str) -> str:
        """根据 Wiki 风格返回写作指引"""
        guidelines = {
            "comprehensive": (
                "目标读者是不懂代码的用户（老板、同事、客户），用通俗语言解释。"
                "内容要详细全面，不遗漏任何重要信息。"
            ),
            "reference": (
                "目标读者是有经验的开发者。内容精简，直接给出关键信息，"
                "不做过多的背景解释。使用表格和列表组织信息，方便快速查阅。"
            ),
            "tutorial": (
                "目标读者是新手。采用教程风格，逐步引导，每一步都解释清楚。"
                "可以包含「学习目标」「前置知识」「实践练习」等环节。"
            ),
            "plain": (
                "极简风格。只保留核心要点，用最简洁的语言描述。"
                "避免任何修饰性文字，每个模块不超过 3-5 句话。"
            ),
        }
        return guidelines.get(style, guidelines["comprehensive"])

    # ─── 各文档生成 ───

    def _make_fact_constraint(self) -> str:
        """返回事实约束段落，插入到所有 LLM prompt 中，强制基于代码分析结果如实描述"""
        return (
            "⚠️ 事实约束（必须遵守）：\n"
            "1. 本项目是一个目录，包含多个独立的工具/模块，它们可能互不关联，不要编造成一个统一产品。\n"
            "2. 你所写的一切内容必须基于下方「项目分析结果」中的代码分析数据，不得自创概念、虚构功能。\n"
            "3. 描述模块时，直接引用分析结果中出现的类名、函数名、依赖库。\n"
            "4. 如果分析结果显示模块之间没有依赖关系，就说它们独立工作。\n"
            "5. 不要使用「平台」「系统」「架构分层」「数据流」等暗示统一产品的词语，除非分析结果证据确凿。\n"
            "6. 宁可保守（如实列出文件），不要夸张（编造模块间协作关系）。\n"
        )

    def _extract_entry_files(self, meta: ProjectCodeMetadata) -> List[str]:
        """提取实证入口文件清单（is_entry_point 的文件），供业务概览"零幻想"引用。

        入口是业务结构的实证起点：由代码静态分析标记，不经过 LLM 推断，
        避免 LLM 编造"哪个文件是入口"。业务概览必须原样引用这份清单。
        """
        if not meta:
            return []
        entries = []
        for mod in meta.modules:
            for f in mod.files:
                if getattr(f, "is_entry_point", False):
                    rel = getattr(f, "rel_path", "")
                    if rel and rel not in entries:
                        entries.append(rel)
        return entries

    def _generate_overview(self, project_name: str, modules: List[WikiModule],
                           style: str = "comprehensive",
                           descriptions: Dict[str, str] = None,
                           meta: ProjectCodeMetadata = None) -> str:
        """生成 OVERVIEW.md — 业务视角报告。

        面向非技术 / 非程序员读者：用大白话讲清项目是做什么的、价值在哪、
        适合谁、怎么大致用起来。与 README/ARCHITECTURE/USAGE 等技术文档分层，
        侧重业务理解而非实现细节（社区反馈：Wiki 过于技术化，缺一份业务人员能看懂的）。

        减少幻想设计：入口清单由静态分析实证提取（_extract_entry_files），LLM 只能
        原样引用，不得编造；每个业务断言要求标注「✅实证 / ❓推测」分级，标识符用
        反引号包裹以便 _cite_verify 交叉校验，无法确证的宁可不说也不夸大。
        """
        guidelines = self._style_guidelines(style)
        desc_text = self._summarize_descriptions(descriptions or {})
        fact_data = self._make_arch_overview(meta, descriptions or {}) if meta else desc_text
        entries = self._extract_entry_files(meta)
        entry_list = "、".join(f"`{e}`" for e in entries) if entries else "（本次未检测到明确入口文件）"
        system_prompt = (
            f"你是一位既懂技术又会讲人话的资深产品顾问，为项目撰写一份"
            f"面向非技术读者（业务人员、管理层、投资人）的业务视角概览。"
            f"{guidelines}"
            "输出纯 Markdown。\n\n"
            "⚠️ 写作规则（减少幻想，务必遵守）：\n"
            "1. 用大白话，避免堆砌类名、函数名；必须提及代码标识符时用 `反引号` 包裹（供证据校验）。\n"
            "2. 只能引用下方「实证入口清单」「事实数据」「模块描述」中真实存在的模块/入口/依赖，\n"
            "   不得编造功能模块或入口文件。\n"
            "3. 每个断言分级标注：有模块/入口/依赖支撑的标「✅实证」；仅凭理解推断的标「❓推测」。\n"
            "4. 不要使用「平台」「系统」「引擎」等暗示统一产品的词语。\n"
            "5. 宁缺毋滥：无法确证的功能宁可不说，也不夸大。"
        )
        user_prompt = (
            f"请为目录 **{project_name}** 编写一份业务视角概览（OVERVIEW.md）。\n\n"
            f"Wiki 风格: {style} ({self.WIKI_STYLES.get(style, '')})\n\n"
            f"要求包含以下小节：\n"
            f"## 这是什么\n"
            f"  用一两段大白话说明这个项目是做什么的、解决什么问题。\n"
            f"## 核心价值\n"
            f"  列出主要好处（3-5 条，通俗短句；每条尽量标注实证或推测）。\n"
            f"## 适合谁\n"
            f"  基于代码推断目标用户，并以「❓推测」标注。\n"
            f"## 入口文件（实证清单，必须原样引用，不得增删）\n"
            f"  {entry_list}\n"
            f"  逐个用一句人话解释每个入口大致做什么。\n"
            f"## 大致怎么用\n"
            f"  用非技术语言描述从拿到项目到用起来的大致步骤；仅引用上述实证入口文件。\n"
            f"## 模块干什么用\n"
            f"  用一句话说明每个模块（目录）大致承担什么角色，标注实证或推测。\n\n"
            f"⚠️ 全文面向非技术读者；不确定处用「❓推测」标注，切勿编造。\n\n"
            f"=== 实证入口清单（零幻想，必引）===\n{entry_list}\n\n"
            f"=== 事实数据（模块/入口/依赖的事实来源）===\n{fact_data[:15000]}\n\n"
            f"=== 模块描述（理解各模块用途的参考）===\n{desc_text[:15000]}"
        )
        return self._llm_ask(system_prompt, user_prompt)

    def _generate_readme(self, project_name: str, summary: str,
                          modules: List[WikiModule], style: str = "comprehensive",
                          descriptions: Dict[str, str] = None,
                          meta: ProjectCodeMetadata = None) -> str:
        """生成 README.md（元数据为事实源，描述为风格参考）"""
        guidelines = self._style_guidelines(style)
        constraint = self._make_fact_constraint()
        desc_text = self._summarize_descriptions(descriptions or {})
        fact_data = self._make_arch_overview(meta, descriptions or {}) if meta else desc_text
        system_prompt = (
            f"你是一个技术文档撰写专家。编写 README。"
            f"{guidelines}"
            "输出纯 Markdown。\n\n"
            "⚠️ 写作规则：\n"
            "1. 所有类名、函数名、文件名必须来自下方「事实数据」，不得编造。\n"
            "2. 模块描述仅作为理解模块用途的参考，具体标识符以事实数据为准。\n"
            "3. 不要使用「平台」「系统」「引擎」等暗示统一产品的词语。"
        )
        user_prompt = (
            f"请为目录 **{project_name}** 编写 README.md 文档。\n\n"
            f"{constraint}\n"
            f"Wiki 风格: {style} ({self.WIKI_STYLES.get(style, '')})\n\n"
            f"要求：\n"
            f"1. 目录概述：一句话说明这个目录下放了哪些内容\n"
            f"2. 各模块：列出每个模块的名称和功能描述\n"
            f"3. 安装运行：如果事实数据中有入口文件（main.py/app.py），给出运行方式\n"
            f"4. 目录结构：列出实际目录名\n"
            f"5. 文档导航：链接到 docs/wiki/ 下的其他文档\n\n"
            f"=== 事实数据（以此为唯一准确来源）===\n{fact_data[:15000]}\n\n"
            f"=== 模块描述（风格参考，标识符以事实数据为准）===\n{desc_text[:15000]}"
        )
        return self._llm_ask(system_prompt, user_prompt)

    def _generate_architecture(self, project_name: str, summary: str,
                                modules: List[WikiModule], style: str = "comprehensive",
                                descriptions: Dict[str, str] = None,
                                meta: ProjectCodeMetadata = None) -> str:
        """生成 ARCHITECTURE.md（使用全量元数据）"""
        guidelines = self._style_guidelines(style)
        constraint = self._make_fact_constraint()
        # 从 meta 构建结构概览
        arch_meta = self._make_arch_overview(meta, descriptions or {})
        system_prompt = (
            f"你是一个技术文档专家。请基于下方实际代码结构数据，如实描述各个模块。"
            f"{guidelines}"
            "输出纯 Markdown。"
        )
        user_prompt = (
            f"请为目录 **{project_name}** 编写模块结构文档。\n\n"
            f"{constraint}\n"
            f"Wiki 风格: {style} ({self.WIKI_STYLES.get(style, '')})\n\n"
            f"要求：\n"
            f"1. 模块清单：列出每个目录及其中检测到的文件\n"
            f"2. 技术栈：只列出实际出现的库\n"
            f"3. 依赖关系：只列出现有的 import 依赖\n\n"
            f"以下是代码结构数据：\n\n{arch_meta[:30000]}"
        )
        return self._llm_ask(system_prompt, user_prompt)

    def _generate_installation(self, project_name: str, summary: str,
                                modules: List[WikiModule], style: str = "comprehensive",
                                meta: ProjectCodeMetadata = None) -> str:
        """生成 INSTALLATION.md"""
        guidelines = self._style_guidelines(style)
        constraint = self._make_fact_constraint()
        deps_info = self._extract_deps_info(summary)
        fact_data = self._make_arch_overview(meta, {}) if meta else summary
        system_prompt = (
            f"你是一个技术文档撰写专家。如实编写安装指南。"
            f"{guidelines}"
            "输出纯 Markdown。\n\n"
            "⚠️ 所有依赖库、文件名必须来自下方事实数据。"
        )
        user_prompt = (
            f"请为目录 **{project_name}** 编写 INSTALLATION.md 安装指南。\n\n"
            f"{constraint}\n"
            f"Wiki 风格: {style} ({self.WIKI_STYLES.get(style, '')})\n\n"
            f"要求：\n"
            f"1. 环境要求：只列事实数据中检测到的 requirements.txt / pyproject.toml 内容\n"
            f"2. 依赖安装：只列事实数据中出现的依赖库\n"
            f"3. 配置步骤：只列事实数据中检测到的 config 文件\n"
            f"4. 验证安装：只列事实数据中的入口文件\n\n"
            f"=== 事实数据 ===\n{fact_data[:15000]}\n\n"
            f"依赖信息：{deps_info}"
        )
        return self._llm_ask(system_prompt, user_prompt)

    def _generate_usage(self, project_name: str, summary: str,
                         modules: List[WikiModule], style: str = "comprehensive",
                         descriptions: Dict[str, str] = None,
                         meta: ProjectCodeMetadata = None) -> str:
        """生成 USAGE.md"""
        guidelines = self._style_guidelines(style)
        constraint = self._make_fact_constraint()
        desc_text = self._summarize_descriptions(descriptions or {})
        fact_data = self._make_arch_overview(meta, descriptions or {}) if meta else desc_text
        system_prompt = (
            f"你是一个技术文档撰写专家。如实编写使用说明。"
            f"{guidelines}"
            "输出纯 Markdown。\n\n"
            "⚠️ 所有入口文件、函数名必须来自下方事实数据。"
        )
        user_prompt = (
            f"请为目录 **{project_name}** 编写 USAGE.md 使用说明。\n\n"
            f"{constraint}\n"
            f"Wiki 风格: {style} ({self.WIKI_STYLES.get(style, '')})\n\n"
            f"要求：\n"
            f"1. 运行方式：只列事实数据中的 main.py/app.py/server.py 入口\n"
            f"2. 每个模块用法：基于模块描述说明用途，但入口函数以事实数据为准\n"
            f"3. 入口文件：列出事实数据中检测到的入口文件\n\n"
            f"=== 事实数据（标识符以此为唯一来源）===\n{fact_data[:15000]}\n\n"
            f"=== 模块描述（风格参考）===\n{desc_text[:15000]}"
        )
        return self._llm_ask(system_prompt, user_prompt)

    def _generate_module_docs(self, modules: List[WikiModule],
                               descriptions: Dict[str, str],
                               output_dir: str, style: str = "comprehensive",
                               meta: ProjectCodeMetadata = None,
                               cross_badges: Dict[str, dict] = None,
                               affected_modules: Optional[List[str]] = None) -> List[str]:
        """生成 MODULES/ 目录下的模块文档

        Args:
            affected_modules: 非空时只生成这些模块的文档（R1 增量同步用）；
                              None 表示全量生成所有核心模块文档。
        """
        docs = []
        modules_dir = os.path.join(output_dir, "MODULES")
        guidelines = self._style_guidelines(style)
        constraint = self._make_fact_constraint()
        cross_badges = cross_badges or {}
        affected_set = set(affected_modules) if affected_modules is not None else None

        index = self._build_module_index(modules, cross_badges)
        self._emit(docs, modules_dir, "_index.md", index)

        # 为每个核心模块生成详细文档
        for mod in modules:
            if not mod.is_core:
                continue
            # R1 增量：只处理受影响模块
            if affected_set is not None and mod.name not in affected_set:
                continue
            desc = descriptions.get(mod.name, "")
            if not desc:
                continue

            # 交叉验证徽章：有铁证时注入文档顶部，供非技术人员核验描述可信度
            badge_md = ""
            mod_badge = cross_badges.get(mod.name)
            if mod_badge:
                try:
                    badge_md = module_badge_md(mod_badge.get("status", ""))
                except Exception:
                    badge_md = ""

            # R3 证据锚定：confirmed/partial 的符号锚定到 Git 文件+行号+commit
            anchor_md = ""
            if mod_badge and mod_badge.get("status") in ("confirmed", "partial"):
                confirmed = mod_badge.get("confirmed") or []
                if confirmed:
                    project_path = getattr(meta, "project_path", "") if meta else ""
                    anchors = self._anchor_evidence(confirmed, project_path, self._git_bin())
                    if anchors:
                        anchor_md = (
                            "> **证据锚定**（Git 文件+行号+commit，来自知识图谱调用闭包）：\n>\n"
                            + "\n".join(f"> - {a}" for a in anchors)
                            + "\n"
                        )

            # R2 front matter：模块文档带 source（相对项目根）与 confidence（徽章映射）
            conf = "high"
            if mod_badge:
                conf = WIKI_CONFIDENCE_MAP.get(mod_badge.get("status"), "high")
            source = ""
            if meta:
                try:
                    source = os.path.relpath(mod.path, meta.project_path).replace("\\", "/")
                except Exception:
                    source = mod.path
            front_matter = self._build_front_matter(
                "module", title=mod.name,
                description=(mod_badge.get("reason", "") if mod_badge else ""),
                source=source, confidence=conf,
            )

            # 从 meta 提取该模块的事实数据
            mod_meta_text = ""
            if meta:
                for mm in meta.modules:
                    if mm.name == mod.name:
                        mod_meta_text = self._module_metadata_to_text(mm)
                        break

            system_prompt = (
                f"你是一个技术文档撰写专家。如实编写模块文档。"
                f"{guidelines}"
                "输出纯 Markdown。\n\n"
                "⚠️ 所有类名、函数名、文件名必须来自下方事实数据。"
            )
            user_prompt = (
                f"请为目录下的 **{mod.name}** 模块编写文档。\n\n"
                f"{constraint}\n"
                f"Wiki 风格: {style} ({self.WIKI_STYLES.get(style, '')})\n\n"
                f"要求：\n"
                f"1. 模块内容：只列事实数据中检测到的文件\n"
                f"2. 类与函数：只列事实数据中出现的类和函数\n"
                f"3. 依赖关系：只列事实数据中的依赖\n"
                f"4. 使用方式：如果有入口文件说明如何运行\n\n"
                f"=== 事实数据（以此为唯一准确来源）===\n{mod_meta_text[:15000] if mod_meta_text else ''}\n\n"
                f"=== 模块描述（风格参考）===\n{desc[:10000]}"
            )
            content = self._llm_ask(system_prompt, user_prompt)
            # 在 LLM 生成内容前注入徽章与证据锚定（均为铁证，不经过 LLM 幻觉）
            if badge_md and content:
                content = badge_md + "\n" + content
            if anchor_md and content:
                content = anchor_md + "\n" + content
            self._emit(docs, modules_dir, f"{mod.name}.md", content,
                       front_matter=front_matter)

        return docs

    def _build_module_index(self, modules: List[WikiModule],
                            cross_badges: Dict[str, dict] = None) -> str:
        """生成模块索引"""
        cross_badges = cross_badges or {}
        has_badges = any(b.get("status") for b in cross_badges.values())
        lines = [
            f"# 模块索引",
            f"",
        ]
        if has_badges:
            lines.append("> 每行带「静态交叉验证」徽章：确证 ✅ / 部分确证 🔵 / 存疑 🟡 / 缺失 🔴。"
                         "徽章来自知识图谱调用闭包，用于核验该模块描述是否真的被调用。")
            lines.append("")
            lines.append(f"| 模块 | 文件数 | 类型 | 文档 | 交叉验证 |")
            lines.append(f"|------|--------|------|------|----------|")
        else:
            lines.append(f"| 模块 | 文件数 | 类型 | 文档 |")
            lines.append(f"|------|--------|------|------|")
        for mod in modules:
            core_tag = "核心" if mod.is_core else "辅助"
            doc_link = f"[查看]({mod.name}.md)" if mod.is_core else "-"
            mod_badge = cross_badges.get(mod.name)
            if has_badges:
                badge_label = (BADGE_MD.get(mod_badge.get("status"), "—")
                               if mod_badge else "—")
                lines.append(f"| {mod.name} | {mod.file_count} | {core_tag} | {doc_link} | {badge_label} |")
            else:
                lines.append(f"| {mod.name} | {mod.file_count} | {core_tag} | {doc_link} |")
        return "\n".join(lines)

    def _summarize_descriptions(self, descriptions: Dict[str, str]) -> str:
        """将 Stage 2 模块描述转为紧凑文本"""
        lines = []
        for mod_name, desc in descriptions.items():
            lines.append(f"## {mod_name}")
            lines.append(desc[:1500])  # 每个模块描述截断以防超长
            lines.append("")
        return "\n".join(lines)

    def _make_arch_overview(self, meta: ProjectCodeMetadata,
                            descriptions: Dict[str, str]) -> str:
        """从元数据构建架构概览"""
        lines = [f"总文件数: {meta.total_files if meta else '?'}"]
        lines.append(f"模块数: {len(meta.modules) if meta else '?'}")
        lines.append(f"Web框架: {'检测到' if meta and meta.has_web_framework else '未检测到'}")
        lines.append("")
        if meta:
            for mod in meta.modules:
                entry_files = [f.rel_path for f in mod.files if f.is_entry_point]
                lines.append(f"### {mod.name} ({mod.total_files} 文件)")
                if entry_files:
                    lines.append(f"入口: {', '.join(entry_files)}")
                # 所有文件的类/函数摘要
                all_classes = []
                all_funcs = []
                all_imports = set()
                for f in mod.files:
                    for c in f.classes:
                        all_classes.append(c["name"])
                    for fn in f.functions:
                        all_funcs.append(fn["name"])
                    for imp in f.imports:
                        all_imports.add(imp)
                if all_classes:
                    lines.append(f"类: {', '.join(all_classes[:10])}")
                if all_funcs:
                    lines.append(f"函数: {', '.join(all_funcs[:10])}")
                if all_imports:
                    lines.append(f"依赖: {', '.join(sorted(all_imports)[:15])}")
                lines.append("")
        return "\n".join(lines)

    # ═══════════════════════════════════════════════════════════════════
    # 分层人话版：入口级(L1) + 数据流级(L2)
    # 面向更大规模项目：自动发现入口、按入口/数据流分块喂 LLM、图谱缺失降级。
    # 减少幻想：LLM 只能翻译/解释实证的调用链与数据流边，绑定证据，不编造。
    # ═══════════════════════════════════════════════════════════════════

    def _locate_kg_db(self, project_path: str) -> Optional[str]:
        """定位知识图谱数据库；图谱不存在返回 None（调用方据此降级）。"""
        try:
            from core.code_knowledge_graph import CodeKnowledgeGraph
            db = CodeKnowledgeGraph(project_path).db_path
            if db and os.path.exists(db):
                return db
        except Exception:
            pass
        return None

    @staticmethod
    def _sanitize_doc_name(name: str) -> str:
        """把入口/数据流标识符转成安全的文件名分词。"""
        name = re.sub(r"[^\w\u4e00-\u9fff]+", "_", name or "").strip("_")
        return name or "untitled"

    def _discover_entry_points(self, meta: ProjectCodeMetadata,
                               fv=None) -> List[dict]:
        """规模化：自动发现公共业务入口。

        实证优先：图谱中无 caller 的顶级函数（真·公共入口）+ is_entry_point 文件。
        回退：仅 is_entry_point 文件对应的模块（无图谱也能给出一份，标记弱实证）。
        fv: FlowVerifier 实例（generate 阶段复用，避免重复加载图谱）；None 时降级。
        返回 [{key, module, func, files, has_kg}]。
        """
        entry_modules = {}   # module_name -> {files, funcs}
        if meta:
            for mod in meta.modules:
                ep_files = [f.rel_path for f in mod.files if f.is_entry_point]
                if ep_files:
                    funcs = []
                    for f in mod.files:
                        for fn in f.functions:
                            funcs.append(fn["name"])
                    entry_modules[mod.name] = {"files": ep_files, "funcs": funcs}

        if not entry_modules:
            return []

        # 图谱实例存在 → 收集"无 caller 的函数"（这些才是公共入口的候选）
        root_funcs = set(fv.root_functions()) if fv else set()

        entries = []
        for mod_name, info in entry_modules.items():
            funcs = info["funcs"]
            chosen = next((f for f in funcs if f in root_funcs), None) or \
                (funcs[0] if funcs else "")
            entries.append({
                "key": self._sanitize_doc_name(
                    f"{mod_name}_{chosen}" if chosen else mod_name),
                "module": mod_name,
                "func": chosen,
                "files": info["files"],
                "has_kg": bool(fv),
            })
        return entries

    def _extract_entry_chain(self, entry: dict, fv=None) -> List[dict]:
        """用 FlowVerifier 提取入口的实证调用链（调用闭包内函数节点）。

        fv: FlowVerifier 实例（generate 阶段复用，不再重复加载图谱）。
        返回 [{name, file, line, doc}]；图谱缺失或入口未命中返回 []。
        """
        if not fv:
            return []
        try:
            spec = f"{entry['module']}.{entry['func']}" if entry.get("func") \
                else entry["module"]
            return fv.entry_chain(spec, max_depth=8)
        except Exception:
            return []

    def _generate_entry_doc(self, project_name: str, entry: dict,
                            chain: List[dict], style: str,
                            meta: ProjectCodeMetadata) -> str:
        """LLM 把入口的实证调用链翻译成人话（入口级 L1 人话版）。"""
        guidelines = self._style_guidelines(style)
        ev_lines = []
        for i, s in enumerate(chain, 1):
            loc = f"{s['file']}:{s['line']}" if s["file"] else s["name"]
            doc = f" —— {s['doc'][:80]}" if s["doc"] else ""
            ev_lines.append(f"{i}. `{s['name']}` ({loc}){doc}")
        evidence = "\n".join(ev_lines) if ev_lines else "（本次未提取到实证调用链）"

        system_prompt = (
            f"你是一位既懂技术又擅长讲人话的产品顾问。为项目 **{project_name}** 的"
            f"某个业务入口撰写一篇面向非技术读者的「入口流程人话版」。{guidelines}"
            "输出纯 Markdown。\n\n"
            "⚠️ 写作规则（减少幻想，务必遵守）：\n"
            "1. 用大白话解释这个入口是做什么的、进来要什么、出去给什么。\n"
            "2. 只能引用下方「实证调用链」中真实存在的函数/文件，不得编造步骤。\n"
            "3. 提及代码标识符用 `反引号` 包裹（供证据校验）。\n"
            "4. 每个步骤标注确信度：有实证调用链支撑标「✅实证」，仅凭理解推断标「❓推测」。\n"
            "5. 宁缺毋滥：无法确证的步骤宁可不说，也不夸大。"
        )
        entry_desc = (
            f"### {entry['module']} 入口\n"
            f"入口函数: `{entry['func'] or '(未识别)'}`\n"
            f"入口文件: {', '.join(entry['files']) or '(未识别)'}\n"
            f"实证状态: {'✅ 有知识图谱实证调用链' if chain else '⚠️ 无图谱（弱实证，以下为基于模块理解的推测）'}"
        )
        user_prompt = (
            f"请为入口 **{entry['module']}** 撰写一篇入口流程说明，包含：\n"
            f"## {entry['module']} 入口是做什么的\n"
            f"  一两段人话说明。\n"
            f"## 它大致怎么做\n"
            f"  依据实证调用链，按顺序用大白话描述流程步骤（每步标注实证/推测）。\n"
            f"## 进入这个入口需要什么 / 它产出什么\n"
            f"  基于实证调用链推断输入输出，标注实证或推测。\n\n"
            f"=== 入口信息 ===\n{entry_desc}\n\n"
            f"=== 实证调用链（零幻想，只能引用，不得增删编造）===\n{evidence}"
        )
        return self._llm_ask(system_prompt, user_prompt)

    def _extract_cross_module_flows(self, fv=None) -> List[dict]:
        """聚合跨模块 CALLS 边为业务数据流（模块→模块）。

        fv: FlowVerifier 实例（generate 阶段复用）；None 表示图谱缺失，返回 []。
        返回 [{source, target, funcs, count}]（无实证则不生数据流）。
        """
        if not fv:
            return []
        try:
            return fv.cross_module_flows()
        except Exception:
            return []

    def _generate_flow_doc(self, project_name: str, flow: dict,
                           style: str) -> str:
        """LLM 把跨模块数据流实证翻译成人话（数据流级 L2 人话版）。"""
        guidelines = self._style_guidelines(style)
        func_text = "、".join(f"`{f}`" for f in flow["funcs"]) or "（未识别具体函数）"
        system_prompt = (
            f"你是一位既懂技术又擅长讲人话的业务分析师。为项目 **{project_name}** 撰写一篇"
            f"面向非技术读者的「数据流说明」，解释数据/调用如何从一个模块流向另一个模块。{guidelines}"
            "输出纯 Markdown。\n\n"
            "⚠️ 写作规则（减少幻想，务必遵守）：\n"
            "1. 只能引用下方「实证数据流边」中真实存在的模块与函数，不得编造。\n"
            "2. 提及代码标识符用 `反引号` 包裹（供证据校验）。\n"
            "3. 不确定模块之间传递的具体字段时，用「❓推测」标注，不要假装知道。\n"
            "4. 讲清业务意义即可，不要过度展开实现细节。"
        )
        user_prompt = (
            f"请说明数据流 **{flow['source']} → {flow['target']}**：\n"
            f"## 谁在向谁要东西\n"
            f"  用大白话说清源模块依赖目标模块的什么能力。\n"
            f"## 传递了什么\n"
            f"  基于实证调用的函数推断传递内容，标注实证或推测。\n"
            f"## 为什么这样设计\n"
            f"  结合业务理解给出一两句推测，标注「❓推测」。\n\n"
            f"=== 实证数据流边（零幻想，只能引用）===\n"
            f"源模块: `{flow['source']}`\n"
            f"目标模块: `{flow['target']}`\n"
            f"跨模块调用函数: {func_text}\n"
            f"调用次数: {flow['count']}"
        )
        return self._llm_ask(system_prompt, user_prompt)

    def _make_web_info(self, meta: ProjectCodeMetadata) -> str:
        """从元数据提取 Web 框架信息"""
        if not meta or not meta.has_web_framework:
            return "未检测到 Web 框架。"
        lines = ["检测到 Web 框架。以下文件可能包含路由/端点："]
        for mod in meta.modules:
            for f in mod.files:
                if any(imp.lower() in {"fastapi", "flask", "django", "aiohttp",
                                       "starlette", "apirouter"} for imp in f.imports):
                    lines.append(f"  - {mod.name}/{f.rel_path}")
                    for c in f.classes:
                        lines.append(f"    class {c['name']}")
                    for fn in f.functions:
                        lines.append(f"    def {fn['name']}()")
        return "\n".join(lines)

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

    def _cite_verify(self, text: str, meta: ProjectCodeMetadata,
                     doc_name: str) -> List[str]:
        """从生成的文本中提取反引号标识符，与元数据交叉核对

        Returns:
            未在元数据中验证通过的标识符列表
        """
        if not meta:
            return []

        # 1. 提取所有反引号片段
        tokens = set()
        for m in re.finditer(r'`([^`]+)`', text):
            token = m.group(1).strip()
            # 过滤：太短、太长、含空格、换行、纯数字
            if len(token) < 2 or len(token) > 80:
                continue
            if ' ' in token or '\n' in token:
                continue
            if token.isdigit():
                continue
            # 过滤含中文的文本（代码标识符不含中文）
            if re.search(r'[\u4e00-\u9fff]', token):
                continue
            # 过滤纯标点或纯符号
            if re.match(r'^[^\w]+$', token):
                continue
            tokens.add(token)

        # 2. 收集元数据中所有已知标识符
        known = set()
        # 类名 + 方法名
        for mod in meta.modules:
            for f in mod.files:
                for c in f.classes:
                    known.add(c["name"])
                    for m in c.get("methods", []):
                        known.add(m)
        # 函数名
        for mod in meta.modules:
            for f in mod.files:
                for fn in f.functions:
                    known.add(fn["name"])
        # 文件名（含正反斜杠两种路径格式）
        for mod in meta.modules:
            for f in mod.files:
                known.add(f.rel_path)
                known.add(f.rel_path.replace("\\", "/"))
                known.add(f.rel_path.replace("/", "\\"))
                known.add(os.path.basename(f.rel_path))
        # 目录名
        for mod in meta.modules:
            for f in mod.files:
                d = os.path.dirname(f.rel_path)
                if d and d != ".":
                    known.add(d)
                    known.add(d.replace("\\", "/"))
        # 依赖库名
        for mod in meta.modules:
            for f in mod.files:
                for imp in f.imports:
                    known.add(imp)
        # 模块名
        for mod in meta.modules:
            known.add(mod.name)
        # 项目名
        known.add(os.path.basename(meta.project_path))

        # 小写化用于忽略大小写匹配
        known_lower = {k.lower() for k in known}

        # 3. 校验
        unverified = []
        for token in sorted(tokens):
            # 标准化：去拖尾斜杠，连字符转下划线
            t = token.rstrip("/").rstrip("\\")
            t = t.replace("-", "_")
            if t.lower() in self.CITE_IGNORE:
                continue
            if t.lower() in known_lower:
                continue
            # 去掉 .py / .json 等后缀再试
            base = re.sub(r'\.(py|json|yaml|yml|toml|cfg|ini|md|txt|html|css|js|ts)$', '', t)
            if base.lower() in known_lower:
                continue
            # 去掉可能的 () 后缀（函数调用）
            base2 = re.sub(r'\(.*\)$', '', t)
            if base2.lower() in known_lower:
                continue
            unverified.append(token)

        if unverified:
            print(f"[cite-verify] {doc_name}: {len(unverified)} 个未验证标识符: {unverified[:20]}")

        return unverified

    def _cite_fix(self, doc_text: str, doc_name: str, unverified: List[str],
                   meta: ProjectCodeMetadata) -> str:
        """让 LLM 根据校验结果修复文档段落"""
        if not unverified:
            return doc_text

        system_prompt = (
            "你是一个文档校对专家。你的任务是修正下文中出现的代码标识符错误。\n"
            "以下标识符在代码中不存在，请将它们替换为文中合理且实际存在的名称，"
            "或删除含这些标识符的句子。\n"
            "不要改变文章的整体结构和写作风格。\n"
            "输出修正后的全文。"
        )
        user_prompt = (
            f"文档: {doc_name}\n\n"
            f"不存在的标识符 ({len(unverified)} 个): {', '.join(unverified[:20])}\n"
            f"{'...' if len(unverified) > 20 else ''}\n\n"
            f"原文:\n```\n{doc_text[:30000]}\n```\n\n"
            f"请修正后输出全文。"
        )
        fixed = self._llm_ask(system_prompt, user_prompt)
        return fixed if fixed and len(fixed) > 100 else doc_text

    def _generate_api_doc(self, project_name: str, summary: str,
                           modules: List[WikiModule], style: str = "comprehensive",
                           meta: ProjectCodeMetadata = None) -> str:
        """生成 API.md（Web 框架专用）"""
        guidelines = self._style_guidelines(style)
        constraint = self._make_fact_constraint()
        web_info = self._make_web_info(meta)
        system_prompt = (
            f"你是一个 API 文档撰写专家。如实编写 API 文档。"
            f"{guidelines}"
            "输出纯 Markdown。\n\n"
            "⚠️ 所有端点、路由、参数必须来自下方事实数据，不得编造。"
        )
        user_prompt = (
            f"请为目录 **{project_name}** 编写 API.md 文档。\n\n"
            f"{constraint}\n"
            f"Wiki 风格: {style} ({self.WIKI_STYLES.get(style, '')})\n\n"
            f"要求：\n"
            f"1. 只列事实数据中检测到的 Web 框架文件和其中定义的类/函数\n"
            f"2. 每个文件路径如实写明\n"
            f"3. 如果没有检测到路由信息，直接写「未检测到 API 端点」\n\n"
            f"=== 事实数据 ===\n{web_info[:15000]}"
        )
        return self._llm_ask(system_prompt, user_prompt)

    def _build_wiki_index(self, project_name: str, modules: List[WikiModule],
                            docs: List[str], result: WikiResult) -> str:
        """生成 WIKI_INDEX.md（导航首页）"""
        now = datetime.now().strftime("%Y-%m-%d %H:%M")
        large_tag = " (大仓库采样模式)" if result.large_repo else ""
        style_tag = f" [{result.wiki_style} 风格]"

        lines = [
            f"# {project_name} — 项目 Wiki",
            f"",
            f"> 自动生成于 {now}{large_tag}{style_tag}",
            f"> 由 CodeRef Wiki Generator 驱动",
            f"",
            f"## 导航",
            f"",
            f"| 文档 | 内容 | 适合谁 |",
            f"|------|------|--------|",
            f"| [💡 业务概览](OVERVIEW.md) | 项目是什么、核心价值、适合谁、怎么用 | 非技术读者 |",
            f"| [📖 README](README.md) | 项目概述、快速开始 | 所有人 |",
            f"| [🏗️ 架构设计](ARCHITECTURE.md) | 系统架构、模块关系 | 开发者 |",
            f"| [📦 安装指南](INSTALLATION.md) | 手把手安装教程 | 新用户 |",
            f"| [📘 使用指南](USAGE.md) | 功能使用说明 | 用户 |",
            f"| [📂 模块索引](MODULES/_index.md) | 模块列表和文档 | 开发者 |",
        ]

        if any("API.md" in d for d in docs):
            lines.append(f"| [🔌 API 文档](API.md) | API 端点说明 | 开发者 |")

        # 分层人话版导航：入口流程(L1) + 数据流(L2)
        # 归一化路径分隔符，避免 Windows 上 os.path.join 产生混合分隔符导致匹配失败
        norm = [d.replace("\\", "/") for d in docs if d]
        entry_docs = [d for d in norm if "/ENTRIES/" in d]
        flow_docs = [d for d in norm if "/FLOWS/" in d]
        if entry_docs:
            lines.append(f"| [🚪 入口流程](ENTRIES/) | 每个业务入口做什么、怎么做（人话版） | 非技术读者 |")
        if flow_docs:
            lines.append(f"| [🔗 数据流](FLOWS/) | 模块之间如何传递数据（人话版） | 非技术读者 |")

        lines.append("")
        lines.append(f"## 模块概览")
        lines.append("")
        for mod in modules:
            core_tag = " 🔑" if mod.is_core else ""
            lines.append(f"- **{mod.name}**{core_tag} — {mod.file_count} 个文件")

        # 子项目导航
        if result.subprojects:
            lines.append("")
            lines.append("## 子项目 Wiki")
            lines.append("")
            for sp in result.subproject_results:
                sp_name = sp["name"]
                sp_docs = sp["documents"]
                sp_files = sp["total_files"]
                sp_large = " ⚠️" if sp["large_repo"] else ""
                lines.append(f"- [{sp_name}](subprojects/{sp_name}/WIKI_INDEX.md) — {sp_files} 个文件, {sp_docs} 个文档{sp_large}")

        lines.append("")
        lines.append("---")
        lines.append("")
        lines.append("### 关于本 Wiki")
        lines.append("")
        lines.append("本 Wiki 由 CodeRef-AI 自动生成，使用 LLM 理解代码语义后撰写。")
        lines.append("如果你修改了代码，可以重新运行 `coderef_generate_wiki` 更新文档。")
        lines.append("")
        if result.large_repo:
            lines.append("⚠️ 本项目文件较多，Wiki 采用采样模式生成。如需完整文档，请在较小批次中分批生成。")
        lines.append("")

        return "\n".join(lines)

    # ─── 辅助方法 ───

    def _extract_deps_info(self, summary: str) -> str:
        """从摘要中提取依赖信息"""
        # 搜索 "依赖:" 行
        deps = re.findall(r'\*\*依赖\*\*:\s*(.+)', summary)
        if deps:
            return "\n".join(deps[:5])
        return "未找到明确的依赖信息"

    def _build_front_matter(self, doc_type: str, title: str = "",
                            description: str = "", tags: List[str] = None,
                            source: str = "", confidence: str = "high") -> str:
        """R2 生成 YAML front matter 字符串（含 type/title/description/tags/source/confidence/generated_at）。

        对 title/description/source 做 YAML 安全处理（去换行、冒号后空格转全角），
        避免破坏 front matter 解析。
        """
        def _yaml_safe(s: str) -> str:
            s = str(s or "").replace("\n", " ").replace("\r", " ")
            s = re.sub(r":\s+", "：", s)
            return s.strip()

        tags = tags or ["comprehensive"]
        now = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
        lines = [
            "---",
            f"type: {_yaml_safe(doc_type)}",
            f"title: {_yaml_safe(title)}",
            f"description: {_yaml_safe(description)}",
            f"tags: [{', '.join(tags)}]",
            f"source: {_yaml_safe(source)}",
            f"confidence: {_yaml_safe(confidence)}",
            f"generated_at: {now}",
            "---",
            "",
        ]
        return "\n".join(lines)

    def _auto_front_matter(self, name: str) -> str:
        """R2 根据文件名推断默认 front matter（全局文档 / 索引使用）。

        模块文档由 _generate_module_docs 显式传入带 source/confidence 的 front matter。
        """
        base = os.path.basename(name)
        low = base.lower()
        norm = name.replace("\\", "/")
        norm_lower = norm.lower()
        if low == "readme.md":
            doc_type, title = "readme", "README"
        elif low == "overview.md":
            doc_type, title = "overview", "业务概览"
        elif low == "architecture.md":
            doc_type, title = "architecture", "架构设计"
        elif low == "installation.md":
            doc_type, title = "installation", "安装指南"
        elif low == "usage.md":
            doc_type, title = "usage", "使用指南"
        elif low == "api.md":
            doc_type, title = "api", "API 文档"
        elif low == "wiki_index.md":
            doc_type, title = "index", "Wiki 导航"
        elif low == "_index.md":
            doc_type, title = "index", "模块索引"
        elif "entries/" in norm_lower:
            doc_type, title = "entry", base
        elif "flows/" in norm_lower:
            doc_type, title = "flow", base
        else:
            doc_type, title = "module", base
        return self._build_front_matter(doc_type, title=title)

    def _anchor_evidence(self, confirmed: List[str], project_path: str,
                         git_bin: Optional[str] = None) -> List[str]:
        """R3 证据锚定：把交叉验证 confirmed 符号锚定到 Git 文件+行号+commit。

        输入格式（来自 _cross_verify_modules）：`func@file.py:line`；
        输出格式：`SRC n: file.py:line (commit)`（n 为序号）。
        无 git 时降级为 `file.py:line` 不带 commit。
        """
        git_bin = git_bin or self._git_bin()
        anchors = []
        for i, item in enumerate(confirmed or [], 1):
            m = re.match(r"^(.*)@(.+):(\d+)$", item.strip())
            if not m:
                continue
            file_path, line = m.group(2), m.group(3)
            commit = ""
            if git_bin and project_path:
                commit = self._git_last_commit(git_bin, project_path, file_path)
            if commit:
                anchors.append(
                    f"{WIKI_SRC_MARK_PREFIX} {i}: {file_path}:{line} ({commit})")
            else:
                anchors.append(f"{WIKI_SRC_MARK_PREFIX} {i}: {file_path}:{line}")
        return anchors

    def _save_last_good(self, output_dir: str) -> None:
        """R3 Last-good 门控：把当前 wiki 输出目录整体复制到 .last-good/。

        先删旧备份再复制，避免残留旧文件；失败静默（备份是增强项，不阻断主流程）。
        """
        dst = os.path.join(output_dir, WIKI_LAST_GOOD_DIR)
        try:
            if os.path.exists(dst):
                shutil.rmtree(dst)
            shutil.copytree(output_dir, dst)
        except Exception:
            pass

    def _emit(self, docs: List[str], output_dir: str, name: str, content: str,
              front_matter: Optional[str] = None) -> None:
        """安全写入文档并追加到 docs 列表（_write_doc 返回空串时不追加）。

        R2：默认在文档顶部注入 front matter（可按文档类型显式传入覆盖）。
        """
        if front_matter is None:
            front_matter = self._auto_front_matter(name)
        full = (front_matter + content) if front_matter else content
        fp = self._write_doc(output_dir, name, full)
        if fp:
            docs.append(fp)

    def _write_doc(self, output_dir: str, filename: str, content: str) -> str:
        """写入文档文件。

        内容为空（LLM 生成失败返回空串）时不落盘，避免产生 0 字节空文件；
        空文件对外表现为"文档已生成"的假象，且无法再触发重试/告警。
        同时记录失败文档名，供 generate() 末尾汇总进 result.errors，让外层感知部分失败。
        返回实际写入的文件路径；内容为空时返回空串。

        R6：禁止写入用户授权文件 INSTRUCTIONS.md（项目根只读不重写）。
        """
        if not content or not content.strip():
            if filename not in self._failed_docs:
                self._failed_docs.append(filename)
            return ""
        # R6: 确保生成器不覆盖用户授权文件 INSTRUCTIONS.md
        if os.path.basename(filename) == WIKI_INSTRUCTIONS_FILE:
            return ""
        filepath = os.path.join(output_dir, filename)
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(content)
        return filepath

    # ─── Git Hook 配置 ───

    def _setup_git_hook(self, project_path: str, output_dir: str):
        """安装 git post-commit hook 自动更新 wiki"""
        git_dir = os.path.join(project_path, ".git")
        if not os.path.isdir(git_dir):
            return

        hooks_dir = os.path.join(git_dir, "hooks")
        os.makedirs(hooks_dir, exist_ok=True)

        hook_path = os.path.join(hooks_dir, "post-commit")
        hook_script = f'''#!/bin/bash
# CodeRef Wiki Auto-Update Hook
# 每次 git commit 后自动更新项目 Wiki
# 安装方式: coderef_generate_wiki --enable-git-hook

echo "[CodeRef] 正在更新项目 Wiki..."

# 找到 CodeRef 的安装路径
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

# 调用 CodeRef MCP 更新 Wiki
python -m core.mcp_server --tool generate_wiki --project "$PROJECT_ROOT" --output "{output_dir}" 2>/dev/null

echo "[CodeRef] Wiki 已更新"
'''

        try:
            with open(hook_path, "w", encoding="utf-8") as f:
                f.write(hook_script)
            # 设置可执行权限
            os.chmod(hook_path, 0o755)
        except (OSError, IOError):
            pass  # 非阻塞，失败了不影响主流程

    # ─── Agent 指针集成（R7）───

    def _write_agent_pointer(self, project_path: str, output_dir: str) -> None:
        """R7 在项目根维护 AGENTS.md 的 CodeRef Wiki 指针区块。

        若 AGENTS.md 已存在，只重写 `<!--CODEREFF:START-->...<!--CODEREFF:END-->`
        区块，其余内容原样保留；不存在则创建含该区块的文件。
        区块内容指向 wiki 入口（WIKI_INDEX.md 相对项目根的路径）。
        失败静默（指针是增强项，不阻断主流程）。
        """
        agent_file = os.path.join(project_path, "AGENTS.md")
        wiki_entry = os.path.join(output_dir, "WIKI_INDEX.md")
        try:
            rel = os.path.relpath(wiki_entry, project_path).replace("\\", "/")
        except Exception:
            rel = "docs/wiki/WIKI_INDEX.md"
        block = (
            f"{WIKI_AGENT_POINTER_START}\n"
            f"## CodeRef Wiki\n"
            f"本项目的 Wiki 文档由 CodeRef-AI 自动生成，入口见 [WIKI_INDEX]({rel})。\n"
            f"修改代码后请重新生成 Wiki 以保持同步。\n"
            f"{WIKI_AGENT_POINTER_END}"
        )
        try:
            if os.path.isfile(agent_file):
                with open(agent_file, "r", encoding="utf-8") as f:
                    content = f.read()
                pattern = re.compile(
                    re.escape(WIKI_AGENT_POINTER_START) + r".*?" + re.escape(WIKI_AGENT_POINTER_END),
                    re.DOTALL,
                )
                if pattern.search(content):
                    content = pattern.sub(block, content)
                else:
                    content = content.rstrip() + "\n\n" + block + "\n"
            else:
                content = block + "\n"
            with open(agent_file, "w", encoding="utf-8") as f:
                f.write(content)
        except Exception:
            pass

    # ─── 报告生成 ───

    def to_report(self, result: WikiResult) -> str:
        """生成 Markdown 格式的生成报告"""
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        lines = [
            f"# Wiki 生成报告",
            f"",
            f"> 项目: `{result.project_path}`",
            f"> 项目名称: **{result.project_name}**",
            f"> 生成时间: {now}",
            f"> Wiki 风格: **{result.wiki_style}** ({self.WIKI_STYLES.get(result.wiki_style, '')})",
            f"> 总文件数: {result.total_files}",
        ]

        if result.large_repo:
            lines.append(f"> ⚠️ 大仓库模式：文件数超过 {self.LARGE_REPO_THRESHOLD}，采用采样分析")

        lines.append("")
        lines.append("## 生成的文档")
        lines.append("")

        for doc in result.documents:
            rel = os.path.relpath(doc, result.output_dir)
            lines.append(f"- [{rel}]({rel})")

        # 子项目信息
        if result.subprojects:
            lines.append("")
            lines.append("## 发现子项目")
            lines.append("")
            for sp in result.subproject_results:
                sp_name = sp["name"]
                sp_count = sp["documents"]
                sp_files = sp["total_files"]
                lines.append(f"- **{sp_name}**: {sp_files} 个文件 → {sp_count} 个文档 (在 `subprojects/{sp_name}/`)")

        lines.append("")
        lines.append(f"## 统计")
        lines.append("")
        lines.append(f"| 指标 | 数值 |")
        lines.append(f"|------|------|")
        lines.append(f"| 文档总数 | {len(result.documents)} |")
        lines.append(f"| 模块文档 | {result.module_count} |")
        lines.append(f"| 总文件数 | {result.total_files} |")
        lines.append(f"| Wiki 风格 | {result.wiki_style} |")
        lines.append(f"| 大仓库模式 | {'是' if result.large_repo else '否'} |")
        lines.append(f"| 子项目数 | {len(result.subprojects)} |")

        if result.errors:
            lines.append("")
            lines.append("## 警告")
            for e in result.errors:
                lines.append(f"- {e}")

        lines.append("")
        lines.append("---")
        lines.append(f"*由 CodeRef Wiki Generator v2.0 生成*")

        return "\n".join(lines)