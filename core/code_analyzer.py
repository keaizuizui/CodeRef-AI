# -*- coding: utf-8 -*-
"""
代码库深度分析模块
使用Tree-sitter进行多语言代码解析和结构分析
"""

import os
import re
import json
import hashlib
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Set
from dataclasses import dataclass, field
from collections import defaultdict

from loguru import logger
from core.shared_filter import SharedFilter
from core.code_models import CodeFile, CodeFunction, CodeClass
from config import settings


# 分析缓存目录：优先环境变量 / settings 配置的独立目录，空则回退项目根
# data/analysis_cache（兼容旧行为），供测试/CI 隔离缓存避免跨项目污染。
def _resolve_cache_dir() -> str:
    cfg = os.environ.get("CODEREF_ANALYSIS_CACHE", "") or (settings.CODEREF_ANALYSIS_CACHE or "")
    if cfg:
        return os.path.abspath(os.path.expanduser(cfg))
    return os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "data", "analysis_cache"
    )


@dataclass
class ProjectAnalysis:
    """项目分析结果"""
    project_path: str
    total_files: int = 0
    total_lines: int = 0
    languages: Dict[str, int] = field(default_factory=dict)
    files: List[CodeFile] = field(default_factory=list)
    modules: Dict[str, List[str]] = field(default_factory=lambda: defaultdict(list))
    dependencies: Set[str] = field(default_factory=set)
    architecture_summary: str = ""
    core_features: List[str] = field(default_factory=list)
    tech_stack: List[str] = field(default_factory=list)
    
    def to_dict(self) -> dict:
        return {
            "project_path": self.project_path,
            "total_files": self.total_files,
            "total_lines": self.total_lines,
            "languages": dict(self.languages),
            "files": [f.to_dict() for f in self.files],
            "modules": {k: list(v) for k, v in self.modules.items()},
            "dependencies": list(self.dependencies),
            "architecture_summary": self.architecture_summary,
            "core_features": self.core_features,
            "tech_stack": self.tech_stack,
        }
    
    @staticmethod
    def from_dict(d: dict) -> 'ProjectAnalysis':
        obj = ProjectAnalysis(project_path=d.get("project_path", ""))
        obj.total_files = d.get("total_files", 0)
        obj.total_lines = d.get("total_lines", 0)
        obj.languages = d.get("languages", {})
        obj.files = [CodeFile.from_dict(f) for f in d.get("files", [])]
        obj.modules = defaultdict(list, {k: list(v) for k, v in d.get("modules", {}).items()})
        obj.dependencies = set(d.get("dependencies", []))
        obj.architecture_summary = d.get("architecture_summary", "")
        obj.core_features = d.get("core_features", [])
        obj.tech_stack = d.get("tech_stack", [])
        return obj


# ============================================================
# 模块级辅助函数 —— 从 CodeAnalyzer 类提取的纯逻辑（不依赖 self 状态）。
# 类内保留原方法签名作为委托入口，公共接口与行为不变。
# 已按职责拆分为四个子模块（god-module-split 第五批）：
#   ca_parse.py  —— AST 解析与结构提取
#   ca_render.py —— 汇总分析与报告渲染（含 mermaid）
#   ca_gitnexus.py —— GitNexus 图谱增强
#   ca_audit.py  —— 缺陷审计检测
# ============================================================

from core.ca_parse import (
    _parse_python_file_content,
    _ast_parse_content,
    _extract_basic_imports,
    _extract_full_import_calls,
    _extract_dynamic_and_http,
    _fill_functions_and_classes,
    _fill_funcs_classes_from_ast,
    _fill_funcs_classes_from_regex,
)
from core.ca_render import (
    _tech_stack_summary,
    _core_features_from,
    _architecture_summary_text,
    _mode_metadata_from,
    _model_roles_from,
    _render_rich_header,
    _render_rich_module_file_entry,
    _render_rich_modules,
    _render_rich_entities,
    _render_rich_deps,
    _render_rich_metrics,
    _render_rich_tail,
    _render_ai_header,
    _render_ai_summary,
    _render_ai_categories,
    _render_ai_priorities,
    _mermaid_from_analysis,
)
from core.ca_gitnexus import (
    _enhance_analysis_with_gitnexus,
    _run_gitnexus_cmd,
)
from core.ca_audit import (
    _verify_audit_line_numbers,
    _audit_file_bugs,
    _logger_is_imported,
    _multiline_import_contains_logger,
    _detect_undefined_logger,
    _find_scope_for_line,
    _line_covered_by_try,
    _detect_missing_exception_handling,
    _audit_file_security,
    _detect_hardcoded_secrets,
    _detect_sql_injection,
    _detect_path_traversal,
    _detect_unsafe_deserialization,
    _detect_eval_exec,
    _detect_hardcoded_win_paths,
    _audit_file_quality,
    _audit_file_performance,
    _audit_file_design,
)

# __all__：re-export 兼容契约（god-module-split 第五批）。
# 模块级函数来自 ca_parse/ca_render/ca_gitnexus/ca_audit，在此 re-export
# 以维持对外符号面不变（9 个引用方 + tests 均只 import CodeAnalyzer/ProjectAnalysis）。
# 顶部 import 名一并列入，使 pyflakes 将 re-export / 兼容 import 视为已使用。
__all__ = [
    # 数据模型与门面类
    'ProjectAnalysis', 'CodeAnalyzer',
    # AST 解析（ca_parse）
    '_parse_python_file_content', '_ast_parse_content',
    '_extract_basic_imports', '_extract_full_import_calls', '_extract_dynamic_and_http',
    '_fill_functions_and_classes', '_fill_funcs_classes_from_ast', '_fill_funcs_classes_from_regex',
    # 汇总/渲染（ca_render）
    '_tech_stack_summary', '_core_features_from', '_architecture_summary_text',
    '_mode_metadata_from', '_model_roles_from',
    '_render_rich_header', '_render_rich_module_file_entry', '_render_rich_modules',
    '_render_rich_entities', '_render_rich_deps', '_render_rich_metrics', '_render_rich_tail',
    '_render_ai_header', '_render_ai_summary', '_render_ai_categories', '_render_ai_priorities',
    '_mermaid_from_analysis',
    # GitNexus 增强（ca_gitnexus）
    '_enhance_analysis_with_gitnexus', '_run_gitnexus_cmd',
    # 缺陷审计（ca_audit）
    '_verify_audit_line_numbers',
    '_audit_file_bugs', '_audit_file_security', '_audit_file_quality',
    '_audit_file_performance', '_audit_file_design',
    '_logger_is_imported', '_multiline_import_contains_logger', '_detect_undefined_logger',
    '_find_scope_for_line', '_line_covered_by_try', '_detect_missing_exception_handling',
    '_detect_hardcoded_secrets', '_detect_sql_injection', '_detect_path_traversal',
    '_detect_unsafe_deserialization', '_detect_eval_exec', '_detect_hardcoded_win_paths',
    # 顶部 import 名（兼容既有符号面）
    'CodeClass', 'CodeFile', 'CodeFunction', 'Dict', 'List', 'Optional', 'Path', 'Set',
    'SharedFilter', 'dataclass', 'datetime', 'defaultdict', 'field', 'hashlib', 'json',
    'logger', 'os', 're', 'settings', 'subprocess',
]


class CodeAnalyzer:
    """代码分析器"""
    
    # 文件扩展名到语言的映射
    EXTENSION_MAP = {
        '.py': 'python',
        '.js': 'javascript',
        '.ts': 'typescript',
        '.jsx': 'javascript',
        '.tsx': 'typescript',
        '.java': 'java',
        '.cpp': 'cpp',
        '.c': 'c',
        '.h': 'c',
        '.hpp': 'cpp',
        '.go': 'go',
        '.rs': 'rust',
        '.rb': 'ruby',
        '.php': 'php',
    }
    
    # 忽略的目录——只过滤真正不该扫的
    IGNORE_DIRS = {
        # git、缓存、编译产物
        '__pycache__', '.git', 'node_modules',
        # 本地Python运行时（不是你的代码）
        'venv', 'env', '.venv', '.env',
        'site-packages', 'Lib', 'lib',
        'egg-info', '.eggs',
        'Python3.14', 'Python3.13', 'Python3.12',
        # 第三方集成代码（不是你写的）
        'third_party', 'third-party',
        # Composer/NPM 等包管理器依赖目录（PHP vendor / Ruby bundle 等）
        'vendor', 'bundle', 'node_modules/.store',
        # CodeRef 自身运行时产物目录（分析它们只会把报告当代码审）
        '.coderef', '.gitnexus', 'coderef-report', 'coderef-wiki',
        # 运行时缓存目录：多存临时产物/数据快照，审计无意义（且会误报硬编码凭据）
        'cache',
    }

    # 目录名模式：备份/归档目录是复制品，纳入审计会重复计数旧代码
    IGNORE_DIR_PATTERNS = [
        r'^(?:[a-zA-Z0-9]*_)?backups?(?:[_-][a-zA-Z0-9_-]*)?$',
    ]
    
    # 忽略的文件名模式（正则表达式）— 仅排除编译产物
    IGNORE_FILE_PATTERNS = [
        r'.*\.pyc$',                    # Python编译文件
        r'.*\.pyo$',                    # Python优化文件
        r'.*\.so$',                     # 动态链接库（C扩展编译产物）
        r'.*\.pyd$',                    # Windows Python DLL
        r'.*\.egg-info/.*',             # Python包信息
        r'.*__pycache__/.*',           # Python缓存
        r'.*\.swp$',                    # vim swap
        r'.*\.bak$',                    # 备份文件
        r'.*\.tmp$',                    # 临时文件
    ]
    
    # 工具自身生成的报告文件（.md / .docx），不纳入用户代码分析
    IGNORE_REPORT_PATTERNS = [
        r'.*全项目架构分析报告.*\.md$',
        r'.*深度架构分析报告.*\.md$',
        r'.*深度分析报告.*\.md$',
        r'.*分析报告.*\.md$',
        r'.*业务概览.*\.md$',
        r'^README\.md$',
    ]
    
    def __init__(self):
        self.parsers = {}
        self._init_parsers()
        self.MAX_PARSE_FILE_SIZE = 500 * 1024  # 超过500KB的文件不做详细解析
        self._parse_count = 0  # 统计解析过的文件数
        
        # 缓存目录（支持环境变量 / settings 配置覆盖，见 _resolve_cache_dir）
        self._cache_dir = _resolve_cache_dir()
        os.makedirs(self._cache_dir, exist_ok=True)
        
        # GitNexus 增强通道（可用时自动启用）
        self._gitnexus_available = False
        self._gitnexus_client = None
        self._init_gitnexus()
    
    def _init_gitnexus(self):
        """检测GitNexus是否可用，可用则初始化MCP客户端"""
        try:
            from .gitnexus_client import GitNexusMCPClient
            if GitNexusMCPClient.is_cli_available():
                self._gitnexus_available = True
                logger.info("[CodeAnalyzer] GitNexus MCP通道已启用")
            else:
                logger.debug("[CodeAnalyzer] GitNexus CLI未安装，使用传统分析模式")
        except Exception as e:
            logger.debug(f"[CodeAnalyzer] GitNexus初始化检测失败: {e}")
    
    def _cache_path(self, project_path: str) -> str:
        """获取项目对应的缓存文件路径"""
        # 用项目路径的 hash 做文件名
        safe_name = hashlib.md5(project_path.encode('utf-8')).hexdigest()
        return os.path.join(self._cache_dir, f"{safe_name}.json")
    
    def _cache_snapshot_path(self, project_path: str) -> str:
        """获取项目文件快照路径（用于判断缓存是否过期）"""
        safe_name = hashlib.md5(project_path.encode('utf-8')).hexdigest()
        return os.path.join(self._cache_dir, f"{safe_name}_snapshot.json")
    
    def _compute_file_snapshot(self, project_path: str) -> Dict[str, Dict[str, float]]:
        """计算项目所有代码文件的快照（mtime + size），用于判断缓存是否过期

        仅依赖 mtime 会在以下场景产生 stale 缓存：
        - 文件系统时间戳精度不足（如 FAT 粒度为 2 秒），同秒内内容修改无法被感知
        - 文件被复制/恢复后 mtime 未变化但内容不同
        因此同时记录文件大小，size 变化也判定缓存失效，降低与持久化记忆不一致的风险。
        """
        snapshot = {}
        code_files = self.scan_directory(project_path)
        for fp in code_files:
            try:
                mtime = os.path.getmtime(fp)
                size = os.path.getsize(fp)
                snapshot[fp] = {"mtime": mtime, "size": size}
            except OSError as e:
                logger.warning(f"读取文件状态失败，跳过快照项 {fp}: {e}")
        return snapshot
    
    def _is_cache_valid(self, project_path: str) -> bool:
        """检查缓存是否有效（所有文件未被修改）"""
        snapshot_path = self._cache_snapshot_path(project_path)
        if not os.path.exists(snapshot_path):
            return False
        cache_path = self._cache_path(project_path)
        if not os.path.exists(cache_path):
            return False
        try:
            with open(snapshot_path, 'r', encoding='utf-8') as f:
                saved_snapshot = json.load(f)
        except Exception:
            return False
        current_snapshot = self._compute_file_snapshot(project_path)
        # 文件数不同 = 过期
        if set(saved_snapshot.keys()) != set(current_snapshot.keys()):
            return False
        # mtime 或 size 不同 = 过期（兼容旧版仅存 mtime 的快照）
        for fp, cur in current_snapshot.items():
            old = saved_snapshot.get(fp)
            if old is None:
                return False
            if isinstance(old, dict):
                if abs(old.get("mtime", 0) - cur["mtime"]) > 0.1 or old.get("size") != cur["size"]:
                    return False
            else:
                # 旧快照格式：仅存 mtime；非法条目（字符串/None 等）作废缓存
                try:
                    if abs(float(old) - cur["mtime"]) > 0.1:
                        return False
                except (TypeError, ValueError):
                    return False
        return True
    
    def save_cache(self, analysis: ProjectAnalysis):
        """保存分析结果到缓存"""
        cache_path = self._cache_path(analysis.project_path)
        snapshot_path = self._cache_snapshot_path(analysis.project_path)
        
        data = analysis.to_dict()
        with open(cache_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=1)
        
        snapshot = self._compute_file_snapshot(analysis.project_path)
        with open(snapshot_path, 'w', encoding='utf-8') as f:
            json.dump(snapshot, f, ensure_ascii=False)
        
        file_count = len(analysis.files)
        logger.info(f"分析结果已缓存: {cache_path} ({file_count}个文件)")
    
    def load_cache(self, project_path: str) -> Optional[ProjectAnalysis]:
        """从缓存加载分析结果"""
        cache_path = self._cache_path(project_path)
        if not os.path.exists(cache_path):
            logger.debug(f"缓存不存在: {cache_path}")
            return None
        try:
            with open(cache_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            result = ProjectAnalysis.from_dict(data)
            logger.info(f"从缓存加载分析结果: {cache_path} ({result.total_files}个文件)")
            return result
        except Exception as e:
            logger.warning(f"缓存加载失败: {e}")
            return None
    
    def _init_parsers(self):
        """初始化支持的语言解析器"""
        # tree_sitter_languages 在新版本中 API 有变化，这里改为 try/except 方式逐个尝试
        supported_langs = ['python', 'javascript', 'typescript', 'java', 'cpp', 'c', 'go', 'rust']
        for lang in supported_langs:
            try:
                from tree_sitter_languages import get_parser
                self.parsers[lang] = get_parser(lang)
                logger.debug(f"已加载 {lang} 解析器")
            except Exception as e:
                logger.debug(f"加载 {lang} 解析器失败（不影响基础分析）: {e}")
    
    def _should_skip_large_file(self, file_path: str) -> bool:
        """跳过超大文件，避免卡死"""
        try:
            return os.path.getsize(file_path) > self.MAX_PARSE_FILE_SIZE
        except Exception:
            return False
    
    def _detect_language(self, file_path: str) -> Optional[str]:
        """根据文件扩展名检测语言"""
        ext = Path(file_path).suffix.lower()
        return self.EXTENSION_MAP.get(ext)
    
    def _should_ignore(self, path: Path) -> bool:
        """判断是否应该忽略该文件/目录"""
        # 1. 检查目录名是否在忽略列表中
        if any(part in self.IGNORE_DIRS for part in path.parts):
            return True

        # 1.1 备份/归档目录（复制品，审计会重复计数）
        if any(re.search(p, part, re.IGNORECASE)
               for p in self.IGNORE_DIR_PATTERNS for part in path.parts):
            return True

        # 1.5 检查额外忽略目录（由调用方通过 analyze_project 的 extra_ignore_dirs 传入）
        # 用路径分隔符边界匹配，避免 /proj/env 误排除 /proj/environment
        extra_dirs = getattr(self, '_extra_ignore_dirs', None)
        if extra_dirs:
            abs_path = str(path)
            for skip_dir in extra_dirs:
                if abs_path == skip_dir or abs_path.startswith(skip_dir + os.sep):
                    return True

        # 2. 检查文件名模式
        name = path.name
        
        # 对文件使用 IGNORE_FILE_PATTERNS
        if path.is_file():
            for pattern in self.IGNORE_FILE_PATTERNS:
                if re.search(pattern, name):
                    return True
        
        # 3. 检查是否是报告类文件
        if path.is_file():
            for pattern in self.IGNORE_REPORT_PATTERNS:
                if re.search(pattern, name):
                    return True
        
        return False
    
    def scan_directory(self, dir_path: str) -> List[str]:
        """扫描目录，获取所有代码文件路径（使用 os.walk 以容错处理损坏的符号链接）"""
        code_files = []
        root = Path(dir_path)
        
        try:
            for current_dir, dirs, files in os.walk(str(root), topdown=True):
                current_path = Path(current_dir)
                
                # 跳过应忽略的目录
                rel_parts = current_path.relative_to(root).parts
                skip_this = any(self._should_ignore(Path(part)) for part in rel_parts) if rel_parts else False
                if skip_this:
                    dirs.clear()
                    continue
                
                # 过滤子目录列表：把应忽略的移除掉
                dirs[:] = [d for d in dirs if not self._should_ignore(current_path / d)]
                
                for file_name in files:
                    file_path = current_path / file_name
                    try:
                        if self._detect_language(str(file_path)):
                            code_files.append(str(file_path))
                    except (PermissionError, FileNotFoundError, OSError):
                        continue
        except (PermissionError, FileNotFoundError, OSError) as walk_err:
            logger.warning(f"扫描路径时遇到错误（跳过）: {walk_err}")
        
        logger.info(f"扫描完成，发现 {len(code_files)} 个代码文件")
        return code_files
    
    def parse_python_file(self, content: str, file_path: str, project_root: str = "") -> CodeFile:
        """解析Python文件（实现提取至模块级 _parse_python_file_content）"""
        return _parse_python_file_content(content, file_path, project_root)
    
    def parse_file(self, file_path: str) -> Optional[CodeFile]:
        """解析单个代码文件"""
        try:
            lang = self._detect_language(file_path)
            if not lang:
                return None
            
            # 超大文件跳过详细解析，只记录基本信息
            if self._should_skip_large_file(file_path):
                logger.debug(f"跳过超大文件详细解析: {file_path}")
                code_file = CodeFile(file_path=file_path, language=lang, raw_content="[超大文件，已跳过详细分析]")
                # 只读一遍估算行数，避免对超大文件二次读取
                try:
                    with open(file_path, 'rb') as f_large:
                        line_count = sum(1 for _ in f_large)
                    code_file.raw_content = f"[超大文件，已跳过详细分析，约 {line_count} 行]"
                except OSError as e:
                    # 行数估算失败，保留默认超大文件标记
                    logger.warning(f"超大文件行数估算失败 {file_path}: {e}")
                return code_file
            
            with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                content = f.read()
            
            # 使用简化的解析方式（Tree-sitter完整解析较复杂，这里用正则）
            if lang == 'python':
                return self.parse_python_file(content, file_path, project_root=getattr(self, '_current_project', ''))
            
            # 其他语言的简化解析
            code_file = CodeFile(file_path=file_path, language=lang, raw_content=content)
            return code_file
            
        except Exception as e:
            logger.error(f"解析文件 {file_path} 失败: {e}")
            return None
    
    def analyze_project(self, project_path: str, force_reanalyze: bool = False,
                        file_progress_cb=None,
                        extra_ignore_dirs: Optional[List[str]] = None) -> ProjectAnalysis:
        """完整分析项目（支持缓存）

        file_progress_cb: 可选回调 file_progress_cb(done:int, total:int),
        在逐个解析文件时上报进度，供长阶段提供"已扫描文件/总文件"的中间粒度。
        extra_ignore_dirs: 额外忽略的目录绝对路径列表（如 ProjectScope 检测到的跳过目录）。
        """
        logger.info(f"开始分析项目: {project_path}")

        # 设置额外忽略目录供 _should_ignore 使用
        self._extra_ignore_dirs = extra_ignore_dirs or []
        
        # 加载项目专属的 cache 硬编码优化（白名单）
        SharedFilter.load_cache(project_path)

        # 检查缓存
        if not force_reanalyze and self._is_cache_valid(project_path):
            cached = self.load_cache(project_path)
            if cached:
                return cached
        
        result = ProjectAnalysis(project_path=project_path)
        
        # 设置当前项目路径以便 parse_file 使用
        self._current_project = project_path
        
        # 扫描所有文件
        code_files = self.scan_directory(project_path)
        result.total_files = len(code_files)
        
        skipped_count = 0
        
        # 逐个解析文件
        for idx, file_path in enumerate(code_files):
            # 每500个文件打一次进度日志
            if idx > 0 and idx % 500 == 0:
                logger.info(f"  分析进度: {idx}/{len(code_files)} 个文件")

            # 文件级进度回调：长阶段也能看到中间进度（每50个文件触发一次，避免回调过密）
            if file_progress_cb and (idx % 50 == 0 or idx + 1 == len(code_files)):
                try:
                    file_progress_cb(idx + 1, len(code_files))
                except Exception:
                    # 进度回调异常不影响分析主流程
                    pass

            code_file = self.parse_file(file_path)
            if code_file:
                # 检查是否跳过
                if code_file.raw_content.startswith("[超大文件"):
                    skipped_count += 1
                
                result.files.append(code_file)
                
                # 统计语言
                result.languages[code_file.language] = result.languages.get(code_file.language, 0) + 1
                
                # 统计行数 - 对超大文件直接从信息字符串提取
                if code_file.raw_content.startswith("[超大文件"):
                    line_match = re.search(r'约 (\d+) 行', code_file.raw_content)
                    if line_match:
                        result.total_lines += int(line_match.group(1))
                else:
                    result.total_lines += len(code_file.raw_content.splitlines())
                
                # 收集依赖
                result.dependencies.update(code_file.dependencies)
                
                # 按模块组织
                rel_path = os.path.relpath(file_path, project_path)
                module = os.path.dirname(rel_path) or 'root'
                result.modules[module].append(rel_path)
        
        # 生成技术栈分析
        result.tech_stack = self._analyze_tech_stack(result)
        
        # 生成核心功能
        result.core_features = self._extract_core_features(result)
        
        # 生成架构摘要
        result.architecture_summary = self._generate_architecture_summary(result)
        
        skip_msg = f"（跳过 {skipped_count} 个超大文件的详细解析）" if skipped_count else ""
        logger.info(f"项目分析完成: {result.total_files} 个文件, {result.total_lines} 行代码 {skip_msg}")
        
        # GitNexus 增强：如果可用，用图谱数据补充分析
        if self._gitnexus_available:
            try:
                self._enhance_with_gitnexus(result)
            except Exception as e:
                logger.warning(f"[CodeAnalyzer] GitNexus增强失败（不影响基础分析）: {e}")
        
        # 保存到缓存
        self.save_cache(result)
        
        return result
    
    def _analyze_tech_stack(self, analysis: ProjectAnalysis) -> List[str]:
        """分析技术栈（实现提取至模块级 _tech_stack_summary）"""
        return _tech_stack_summary(analysis)
    
    def _extract_core_features(self, analysis: ProjectAnalysis) -> List[str]:
        """提取核心功能（实现提取至模块级 _core_features_from）"""
        return _core_features_from(analysis)
    
    def _generate_architecture_summary(self, analysis: ProjectAnalysis) -> str:
        """生成架构摘要（基础版，实现提取至模块级 _architecture_summary_text）"""
        return _architecture_summary_text(analysis)
    
    def _extract_mode_metadata(self, analysis: ProjectAnalysis) -> list:
        """
        从代码中提取工具模式元数据（模式标识、角色、所属工具等）
        V2.1: 从 MODE_METADATA 字典中提取，不再硬编码 fallback
        （实现提取至模块级 _mode_metadata_from）
        """
        return _mode_metadata_from(analysis, self.MAX_PARSE_FILE_SIZE)
    
    def _extract_model_roles(self, analysis: ProjectAnalysis) -> list:
        """
        从代码中提取 LLM 模型角色配置
        V2.1: 直接在项目目录中搜索 config.yaml 并解析，不再硬编码
        （实现提取至模块级 _model_roles_from）
        """
        return _model_roles_from(analysis)
    
    def generate_rich_report(self, analysis: ProjectAnalysis) -> str:
        """
        生成深度分析报告（独立方法，纯代码分析，无 LLM/外部依赖）
        返回完整的 Markdown 报告内容
        
        拆分说明：按报告章节提取为模块级 _render_rich_* 渲染函数，
        本方法仅作编排，输出内容与拆分前逐字段一致。
        """
        # 汇总统计（概览段与代码度量段共用）
        totals = {
            "files": analysis.total_files,
            "lines": analysis.total_lines,
            "classes": sum(len(f.classes) for f in analysis.files),
            "functions": sum(len(f.functions) for f in analysis.files),
            "imports": sum(len(f.imports) for f in analysis.files),
        }
        report: List[str] = []
        report.extend(_render_rich_header(analysis, totals))
        report.extend(_render_rich_modules(analysis))
        report.extend(_render_rich_entities(analysis))
        report.extend(_render_rich_deps(analysis))
        report.extend(_render_rich_metrics(analysis, totals))
        report.extend(_render_rich_tail(analysis))
        return '\n'.join(report)


    # ==================== GitNexus 增强通道 ====================

    def _enhance_with_gitnexus(self, analysis: ProjectAnalysis):
        """用GitNexus图谱数据增强分析结果（实现提取至模块级 _enhance_analysis_with_gitnexus）"""
        _enhance_analysis_with_gitnexus(analysis)

    def scan_function(self, entry: str, depth: int = 3, fmt: str = "report") -> str:
        """扫描单个功能的上下游依赖，生成架构图
        
        这是 GitNexus 的核心能力——按需提取子图 + 动态生成图表
        
        Args:
            entry: 入口点（符号名或 file:function 格式）
            depth: 上下游遍历深度（默认3）
            fmt: 输出格式 (mermaid/structurizr/report)
        
        Returns:
            生成的图表/报告字符串
        """
        if not self._gitnexus_available:
            return "# Error: GitNexus不可用。请先安装: npm install -g gitnexus，然后索引项目: gitnexus analyze"
        
        # GitNexus 已经索引好，用 gitnexus 命令行导出子图
        cmd = [
            "gitnexus", "export",
            "--entry", entry,
            "--depth", str(depth),
        ]
        
        result = self._run_gitnexus(cmd, self._current_project or ".")
        if result is None:
            return "# Error: 执行 gitnexus export 失败"
        
        if fmt == "mermaid":
            return f"```mermaid\n{result}\n```"
        elif fmt == "structurizr":
            return f"```dsl\n{result}\n```"
        else:  # report
            if result.strip().startswith("```"):
                return result
            return f"```text\n{result}\n```"

    def _run_gitnexus(self, cmd: List[str], project_path: str) -> Optional[str]:
        """执行 gitnexus 命令行并返回 stdout（实现提取至模块级 _run_gitnexus_cmd）"""
        return _run_gitnexus_cmd(cmd, project_path)

    def generate_mermaid_diagram(self, analysis: ProjectAnalysis) -> str:
        """基于分析结果生成Mermaid架构图（实现提取至模块级 _mermaid_from_analysis）
        
        利用 auto_classifier 自动分层，生成带子图的 Mermaid flowchart
        
        Args:
            analysis: 项目分析结果
        
        Returns:
            Mermaid代码字符串
        """
        return _mermaid_from_analysis(analysis)


    def generate_ai_report(self, analysis: ProjectAnalysis) -> str:
        """
        生成「给AI辅助编程LLM看的」全代码审计报告
        
        不再输出代码结构描述（MCP已能提供），而是专注于：
        - Bug/错误发现
        - 安全问题检测
        - 代码质量评估
        - 性能风险识别
        - 设计模式违规
        
        拆分说明：按报告章节提取为模块级 _render_ai_* 渲染函数，
        本方法仅作编排，输出内容与拆分前逐字段一致。
        """
        lines: List[str] = []
        lines.extend(_render_ai_header(analysis))
        
        # 运行所有审计规则
        audit_results = self._run_code_audit(analysis)
        
        # 验证审计结果的行号是否仍然有效（防止缓存导致行号过时）
        audit_results = self._verify_line_numbers(analysis, audit_results)
        
        lines.extend(_render_ai_summary(audit_results))
        lines.extend(_render_ai_categories(audit_results))
        lines.extend(_render_ai_priorities(audit_results))
        
        return '\n'.join(lines)
    
    def _run_code_audit(self, analysis: ProjectAnalysis) -> Dict[str, List[Dict]]:
        """
        运行所有代码审计规则，返回按类别分组的问题列表
        
        Returns:
            {
                'bugs': [{'severity': 'high', 'file': '...', 'line': 42, 'description': '...', 'suggestion': '...'}, ...],
                'security': [...],
                'quality': [...],
                'performance': [...],
                'design': [...],
            }
        """
        results = defaultdict(list)
        
        for cf in analysis.files:
            rel_path = os.path.relpath(cf.file_path, analysis.project_path)
            content = cf.raw_content
            lines_content = content.split('\n')
            
            # 跳过超大文件和空文件
            if content.startswith('[超大文件') or len(content) < 100:
                continue
            
            # ===== Bug检测 =====（审计器实现提取至模块级 _audit_file_* 函数）
            _audit_file_bugs(cf, rel_path, content, lines_content, results['bugs'])
            
            # ===== 安全检测 =====
            _audit_file_security(cf, rel_path, content, lines_content, results['security'])
            
            # ===== 质量检测 =====
            _audit_file_quality(cf, rel_path, content, lines_content, results['quality'])
            
            # ===== 性能检测 =====
            _audit_file_performance(cf, rel_path, content, lines_content, results['performance'])
            
            # ===== 设计检测 =====
            _audit_file_design(cf, rel_path, content, lines_content, results['design'])
        
        return dict(results)
    
    def _verify_line_numbers(self, analysis: ProjectAnalysis, audit_results: Dict[str, List[Dict]]) -> Dict[str, List[Dict]]:
        """
        验证审计结果的行号是否仍然有效，移除因文件变更（缓存过时）而过期的 findings
        （实现提取至模块级 _verify_audit_line_numbers）
        """
        return _verify_audit_line_numbers(analysis, audit_results)
