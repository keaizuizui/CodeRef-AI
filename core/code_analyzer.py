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
from typing import Dict, List, Any, Optional, Set
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
    }
    
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
        except:
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

        # 1.5 检查额外忽略目录（由调用方通过 analyze_project 的 extra_ignore_dirs 传入）
        # 用路径分隔符边界匹配，避免 /proj/env 误排除 /proj/environment
        extra_dirs = getattr(self, '_extra_ignore_dirs', None)
        if extra_dirs:
            abs_path = str(path)
            for skip_dir in extra_dirs:
                if abs_path == skip_dir or abs_path.startswith(skip_dir + os.sep):
                    return True

        # 2. 检查文件名模式
        str_path = str(path)
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
        """解析Python文件（增强版：AST 精确解析 + 正则回退）"""
        code_file = CodeFile(file_path=file_path, language='python', raw_content=content)

        # 计算当前文件所在模块（相对项目根的目录）
        if project_root:
            rel_dir = os.path.dirname(os.path.relpath(file_path, project_root))
        else:
            rel_dir = ""

        # ─── 优先使用 AST 精确解析 ──────────────────────────────────
        ast_assignments = []  # AST 解析的赋值分类
        ast_result = None
        try:
            from core.ast_parser import AstParser
            ast_parser = AstParser(project_root=project_root)
            ast_result = ast_parser.parse_content(content, file_path)
            if ast_result:
                ast_assignments = ast_result.assignments
                logger.debug(f"[AST] {file_path}: {len(ast_result.functions)}函数, "
                           f"{len(ast_result.classes)}类, {len(ast_result.assignments)}赋值")
        except Exception as e:
            ast_result = None
            logger.debug(f"[AST] 解析失败 {file_path}: {e}")

        # 存储 AST 赋值分类（供 _audit_security 使用）
        code_file.ast_assignments = ast_assignments

        # 提取导入
        import_pattern = r'^(?:from|import)\s+([\w\.]+)'
        for match in re.finditer(import_pattern, content, re.MULTILINE):
            imp = match.group(1)
            code_file.imports.append(imp)
            root_pkg = imp.split('.')[0]
            
            # 区分项目内部导入 vs 外部依赖
            # 如果导入以项目模块名开头，标记为项目内部
            if rel_dir and (root_pkg == rel_dir.split('\\')[0].split('/')[0] or 
                            any(part == root_pkg for part in rel_dir.replace('\\', '/').split('/'))):
                code_file.project_imports.append(imp)
            else:
                code_file.dependencies.add(root_pkg)
        
        # 提取完整的from ... import ... 语句（用于跨模块分析）
        full_import_pattern = r'from\s+([\w\.]+)\s+import\s+([\w\s,]+)'
        for match in re.finditer(full_import_pattern, content):
            module_path = match.group(1)
            names = [n.strip() for n in match.group(2).split(',')]
            # 检测是否导入其他模块的类/函数（表明跨模块调用）
            if rel_dir and module_path not in ('__future__', 'typing', 'abc', 'dataclasses', 'enum'):
                root = module_path.split('.')[0]
                if root != rel_dir.split('\\')[0].split('/')[0] and not root.startswith('_'):
                    for name in names:
                        code_file.function_calls.append(f"{module_path}.{name}")
        
        # 提取 sys.path.insert / sys.path.append（动态注入点）
        syspath_pattern = r'sys\.path\.(?:insert|append)\s*\(([^)]*)\)'
        for match in re.finditer(syspath_pattern, content):
            code_file.sys_path_inserts.append(match.group(1).strip())
        
        # 提取 importlib.import_module（动态导入）
        dyn_import_pattern = r'importlib\.import_module\s*\(([^)]*)\)'
        for match in re.finditer(dyn_import_pattern, content):
            code_file.dynamic_imports.append({"module_expr": match.group(1).strip()})
        
        # 提取对本地服务的 HTTP 请求
        http_pattern = r'(requests|httpx|aiohttp)\.(get|post|put|delete)\s*\(\s*["\'](http://127\.0\.0\.1|http://localhost|http://0\.0\.0\.0)'
        for match in re.finditer(http_pattern, content):
            code_file.http_calls.append({
                "method": match.group(2).upper(),
                "url_pattern": match.group(0)
            })
        
        # 提取函数/类：优先使用 AST 精确结果（node.end_lineno 精确到函数体最后一行，
        # 类方法来自 ClassDef.body），AST 失败（如语法错误）时回退正则推断。
        # 旧正则版缺陷：最后一个函数的 end_line 直接取文件总行数、中间函数取下一个
        # def/class 定义前一行，函数后的模块级常量区被算进函数体，行数虚高；
        # 且 CodeClass.methods 从未填充，方法计数恒为 0。
        if ast_result is not None:
            # all_functions = 顶层函数 + 类方法（与旧正则口径一致，方法也参与行数统计）
            for func in ast_result.all_functions:
                if func.name.startswith('_'):
                    continue  # 与旧正则行为一致：跳过私有函数/方法
                code_file.functions.append(CodeFunction(
                    name=func.name,
                    start_line=func.start_line,
                    end_line=func.end_line,
                    parameters=func.parameters,
                    return_type=func.return_type,
                    docstring=func.docstring,
                    code=func.code,
                ))
            for cls in ast_result.classes:
                code_file.classes.append(CodeClass(
                    name=cls.name,
                    start_line=cls.start_line,
                    end_line=cls.end_line,
                    methods=[CodeFunction(
                        name=m.name,
                        start_line=m.start_line,
                        end_line=m.end_line,
                        parameters=m.parameters,
                        return_type=m.return_type,
                        docstring=m.docstring,
                        code=m.code,
                    ) for m in cls.methods],
                    base_classes=cls.base_classes,
                    docstring=cls.docstring,
                ))
        else:
            # 正则回退：AST 不可用（语法错误文件）时的近似解析
            func_pattern = r'def\s+(\w+)\s*\(([^)]*)\)\s*(?:->\s*([\w\[\],\s]+))?:'
            func_matches = list(re.finditer(func_pattern, content))
            for idx, match in enumerate(func_matches):
                func_name = match.group(1)
                if not func_name.startswith('_'):  # 跳过私有函数
                    params = [p.strip() for p in match.group(2).split(',') if p.strip()]
                    start_line = content[:match.start()].count('\n') + 1
                    # 计算 end_line：下一个函数/类定义之前减1行，或文件末尾
                    end_line = len(content.splitlines())
                    if idx + 1 < len(func_matches):
                        end_line = content[:func_matches[idx + 1].start()].count('\n')
                    code_file.functions.append(CodeFunction(
                        name=func_name,
                        start_line=start_line,
                        end_line=end_line,
                        parameters=params,
                        return_type=match.group(3)
                    ))

            class_pattern = r'class\s+(\w+)(?:\(([^)]*)\))?:'
            class_matches = list(re.finditer(class_pattern, content))
            for idx, match in enumerate(class_matches):
                class_name = match.group(1)
                bases = [b.strip() for b in (match.group(2) or '').split(',') if b.strip()]
                start_line = content[:match.start()].count('\n') + 1
                end_line = len(content.splitlines())
                if idx + 1 < len(class_matches):
                    end_line = content[:class_matches[idx + 1].start()].count('\n')
                code_file.classes.append(CodeClass(
                    name=class_name,
                    start_line=start_line,
                    end_line=end_line,
                    base_classes=bases
                ))
        
        return code_file
    
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
        """分析技术栈"""
        tech_stack = []
        
        # 主要语言
        for lang, count in analysis.languages.items():
            tech_stack.append(f"{lang} ({count} 文件)")
        
        # 主要依赖
        common_frameworks = {
            'fastapi': 'FastAPI',
            'flask': 'Flask',
            'django': 'Django',
            'react': 'React',
            'vue': 'Vue.js',
            'angular': 'Angular',
            'pandas': 'Pandas',
            'numpy': 'NumPy',
            'torch': 'PyTorch',
            'tensorflow': 'TensorFlow',
            'requests': 'Requests',
        }
        
        for dep, name in common_frameworks.items():
            if dep in analysis.dependencies:
                tech_stack.append(name)
        
        return tech_stack
    
    def _extract_core_features(self, analysis: ProjectAnalysis) -> List[str]:
        """提取核心功能"""
        features = []
        
        # 从类名和函数名推断功能
        keywords = {
            'api': 'API接口',
            'db': '数据库操作',
            'database': '数据库',
            'auth': '认证授权',
            'user': '用户管理',
            'search': '搜索功能',
            'parser': '解析器',
            'analyzer': '分析器',
            'crawl': '爬虫',
            'scraper': '数据抓取',
            'ml': '机器学习',
            'model': '模型',
            'train': '训练',
            'predict': '预测',
        }
        
        found_features = set()
        
        for code_file in analysis.files:
            for cls in code_file.classes:
                for kw, feature in keywords.items():
                    if kw.lower() in cls.name.lower() and feature not in found_features:
                        found_features.add(feature)
                        features.append(feature)
            
            for func in code_file.functions:
                for kw, feature in keywords.items():
                    if kw.lower() in func.name.lower() and feature not in found_features:
                        found_features.add(feature)
                        features.append(feature)
        
        return features[:10]  # 最多返回10个核心功能
    
    def _generate_architecture_summary(self, analysis: ProjectAnalysis) -> str:
        """生成架构摘要（基础版）"""
        summary_parts = []
        
        # 基本信息
        summary_parts.append(f"## 项目概览")
        summary_parts.append(f"- 总文件数: {analysis.total_files}")
        summary_parts.append(f"- 总代码行数: {analysis.total_lines:,}")
        lang_str = ', '.join([f'{k}({v}个文件)' for k, v in sorted(analysis.languages.items(), key=lambda x: -x[1])])
        summary_parts.append(f"- 语言分布: {lang_str}")
        summary_parts.append(f"- 总模块数: {len(analysis.modules)}")
        summary_parts.append(f"- 总类数: {sum(len(f.classes) for f in analysis.files)}")
        summary_parts.append(f"- 总函数数: {sum(len(f.functions) for f in analysis.files)}")
        
        # 技术栈
        if analysis.tech_stack:
            summary_parts.append(f"\n## 技术栈")
            for tech in analysis.tech_stack:
                summary_parts.append(f"- {tech}")
        
        # 核心功能
        if analysis.core_features:
            summary_parts.append(f"\n## 核心功能")
            for feature in analysis.core_features:
                summary_parts.append(f"- {feature}")
        
        # 模块结构
        summary_parts.append(f"\n## 模块结构")
        for module, files in sorted(analysis.modules.items()):
            summary_parts.append(f"- **{module}**: {len(files)} 个文件")
        
        # 依赖概览
        if analysis.dependencies:
            summary_parts.append(f"\n## 外部依赖")
            for dep in sorted(analysis.dependencies)[:20]:
                summary_parts.append(f"- {dep}")
            if len(analysis.dependencies) > 20:
                summary_parts.append(f"- ... 及其他 {len(analysis.dependencies) - 20} 个依赖")
        
        return '\n'.join(summary_parts)
    
    def _extract_mode_metadata(self, analysis: ProjectAnalysis) -> list:
        """
        从代码中提取工具模式元数据（模式标识、角色、所属工具等）
        V2.1: 从 MODE_METADATA 字典中提取，不再硬编码 fallback
        """
        modes = []
        
        # 扫描所有文件，查找 MODE_METADATA 字典定义（从磁盘读取完整文件）
        for f in analysis.files:
            if 'MODE_METADATA' not in (getattr(f, 'raw_content', '') or ''):
                # raw_content 只有500字符，可能不包含 MODE_METADATA
                # 直接从磁盘读取（超大文件跳过，避免整读大文件占用内存）
                try:
                    if os.path.getsize(f.file_path) > self.MAX_PARSE_FILE_SIZE:
                        logger.debug(f"跳过超大文件 MODE_METADATA 提取: {f.file_path}")
                        continue
                    with open(f.file_path, 'r', encoding='utf-8') as fh:
                        content = fh.read()
                except Exception as e:
                    # 文件不可读，跳过模式提取
                    logger.warning(f"读取文件失败，跳过模式提取 {f.file_path}: {e}")
                    continue
            else:
                content = getattr(f, 'raw_content', '')
            if 'MODE_METADATA' not in content:
                continue
            
            import re
            # 匹配 "tool:mode": { ... "roles": ["A", "B"], ... "name": "xxx", "description": "xxx" ... }
            # 用多行匹配提取完整的模式块
            pattern = r'["\']([\w]+):([\w]+)["\']\s*:\s*\{([^}]+)\}'
            for match in re.finditer(pattern, content):
                mode_id = match.group(1) + ':' + match.group(2)
                block = match.group(3)
                
                # 提取 roles
                roles_match = re.search(r'["\']roles["\']\s*:\s*\[([^\]]*)\]', block)
                roles = []
                if roles_match:
                    roles = [r.strip().strip('"\'') for r in roles_match.group(1).split(',')]
                
                # 提取 name
                name_match = re.search(r'["\']name["\']\s*:\s*["\']([^"\']+)["\']', block)
                cn_name = name_match.group(1) if name_match else mode_id.split(':')[1]
                
                # 提取 description
                desc_match = re.search(r'["\']description["\']\s*:\s*["\']([^"\']+)["\']', block)
                description = desc_match.group(1) if desc_match else ''
                
                tool = mode_id.split(':')[0]
                a_role = '✓' if 'A' in roles else '✗'
                b_role = '✓' if 'B' in roles else '✗'
                
                # 额外能力从 description 提取
                extra = description if description else '-'
                
                modes.append([mode_id, cn_name, tool, a_role, b_role, extra])
        
        if not modes:
            modes = [['(未找到)', '-', '-', '-', '-', '未找到 MODE_METADATA 定义']]
        
        return modes
    
    def _extract_model_roles(self, analysis: ProjectAnalysis) -> list:
        """
        从代码中提取 LLM 模型角色配置
        V2.1: 直接在项目目录中搜索 config.yaml 并解析，不再硬编码
        """
        import yaml as _yaml
        roles = []
        
        # 在项目目录中搜索 config.yaml（不依赖 analysis.files，因为扫描器只扫描代码文件）
        config_path = None
        for root, dirs, files in os.walk(analysis.project_path):
            # 跳过常见的非项目目录
            dirs[:] = [d for d in dirs if d not in ('node_modules', '__pycache__', '.git', 'venv', '.venv', 'data', 'vendor')]
            for fname in files:
                if fname in ('config.yaml', 'config.yml'):
                    config_path = os.path.join(root, fname)
                    break
            if config_path:
                break
        
        if config_path:
            try:
                with open(config_path, 'r', encoding='utf-8') as fh:
                    content = fh.read()
                cfg = _yaml.safe_load(content)
                if isinstance(cfg, dict):
                    # 提取 llm.models 下的角色配置
                    llm_models = cfg.get('llm', {}).get('models', {})
                    if isinstance(llm_models, dict):
                        for role_name, role_cfg in llm_models.items():
                            if isinstance(role_cfg, dict):
                                model_name = role_cfg.get('name', '?')
                                temperature = str(role_cfg.get('temperature', '?'))
                                max_tokens = str(role_cfg.get('max_tokens', '?'))
                                purpose_map = {
                                    'planner': '规划/策略制定', 'writer': '写作/内容生成',
                                    'reviewer': '审查/校验', 'embedder': '嵌入/向量化',
                                    'extractor': '信息提取', 'backtester': '回测/验证',
                                }
                                purpose = purpose_map.get(role_name, role_name)
                                roles.append([role_name, model_name, temperature, max_tokens, purpose])
                    # 提取 semantic 模型配置
                    semantic = cfg.get('semantic', {})
                    if isinstance(semantic, dict):
                        sem_model = semantic.get('model', '')
                        sem_provider = semantic.get('provider', '')
                        if sem_model:
                            roles.append(['semantic', sem_model, '-', '-', f'语义嵌入 ({sem_provider})'])
                    # 提取 vector 模型配置
                    vector = cfg.get('vector', {})
                    if isinstance(vector, dict):
                        local = vector.get('local', {})
                        if isinstance(local, dict):
                            vec_model = local.get('embed_model', '')
                            vec_provider = local.get('provider', '')
                            if vec_model:
                                roles.append(['vector', vec_model, '-', '-', f'向量嵌入 ({vec_provider})'])
                    # 缓存 agent_mapping
                    agent_mapping = cfg.get('agent_mapping', {})
                    if isinstance(agent_mapping, dict):
                        analysis._agent_mapping = agent_mapping
            except Exception as e:
                logger.warning(f"解析 GitNexus 技术栈配置失败，忽略该增强: {e}")
        
        if not roles:
            roles = [['(未找到配置)', '-', '-', '-', f'未在 {analysis.project_path} 中找到 config.yaml']]
        return roles
    
    def generate_rich_report(self, analysis: ProjectAnalysis) -> str:
        """
        生成深度分析报告（独立方法，纯代码分析，无 LLM/外部依赖）
        返回完整的 Markdown 报告内容
        """
        report = []
        
        # ==================== 头部 ====================
        report.append("# 📊 项目深度分析报告")
        report.append(f"\n> 生成时间: 即时分析")
        report.append(f"> 项目路径: `{analysis.project_path}`")
        report.append("")
        report.append("---")
        
        # ==================== 1. 项目概览 ====================
        report.append("## 一、项目概览")
        
        total_files = analysis.total_files
        total_lines = analysis.total_lines
        total_classes = sum(len(f.classes) for f in analysis.files)
        total_functions = sum(len(f.functions) for f in analysis.files)
        total_imports = sum(len(f.imports) for f in analysis.files)
        
        report.append(f"| 指标 | 数值 |")
        report.append(f"|------|------|")
        report.append(f"| 📄 代码文件数 | {total_files} |")
        report.append(f"| 📝 总代码行数 | {total_lines:,} |")
        report.append(f"| 🏗️ 模块/目录数 | {len(analysis.modules)} |")
        report.append(f"| 🏛️ 类/结构体数 | {total_classes} |")
        report.append(f"| 🔧 函数/方法数 | {total_functions} |")
        report.append(f"| 📦 导入语句数 | {total_imports} |")
        report.append(f"| 🔗 外部依赖数 | {len(analysis.dependencies)} |")
        
        # ==================== 2. 语言分布 ====================
        report.append("\n## 二、语言分布")
        report.append("\n| 语言 | 文件数 | 占比 |")
        report.append("|------|--------|------|")
        for lang, count in sorted(analysis.languages.items(), key=lambda x: -x[1]):
            pct = count / total_files * 100 if total_files > 0 else 0
            bar = "█" * int(pct / 5) + "░" * (20 - int(pct / 5))
            report.append(f"| {lang} | {count} | {bar} {pct:.1f}% |")
        
        # ==================== 3. 模块结构 ====================
        report.append("\n## 三、模块/目录结构")
        
        # 按文件数排序
        sorted_modules = sorted(analysis.modules.items(), key=lambda x: len(x[1]), reverse=True)
        
        for module, files in sorted_modules:
            # 统计该模块的语言分布
            lang_in_module = {}
            for file in analysis.files:
                rel_path = os.path.relpath(file.file_path, analysis.project_path)
                file_module = os.path.dirname(rel_path) or 'root'
                if file_module == module:
                    lang_in_module[file.language] = lang_in_module.get(file.language, 0) + 1
            
            lang_detail = ', '.join([f"{k}({v})" for k, v in lang_in_module.items()])
            report.append(f"\n### 📁 {module}")
            report.append(f"- **文件数**: {len(files)} 个")
            report.append(f"- **语言**: {lang_detail}")
            
            # 列出该模块下的文件详情
            for file_path in files[:8]:  # 最多显示8个
                # 找到对应的 CodeFile 对象
                full_path = os.path.join(analysis.project_path, file_path)
                code_file = next((f for f in analysis.files if f.file_path == full_path), None)
                if code_file:
                    cls_count = len(code_file.classes)
                    func_count = len(code_file.functions)
                    imp_count = len(code_file.imports)
                    details = []
                    if cls_count > 0:
                        cls_names = ', '.join(c.name for c in code_file.classes[:3])
                        details.append(f"{cls_count}个类[{cls_names}]")
                    if func_count > 0:
                        details.append(f"{func_count}个函数")
                    if imp_count > 0:
                        details.append(f"{imp_count}个导入")
                    detail_str = ' / '.join(details) if details else ''
                    report.append(f"  - `{os.path.basename(file_path)}` {detail_str}")
            
            if len(files) > 8:
                report.append(f"  - ... 及其他 {len(files) - 8} 个文件")
        
        # ==================== 4. 代码实体分析 ====================
        report.append("\n## 四、关键代码实体")
        
        # 所有类（按文件分组）
        all_classes = []
        for f in analysis.files:
            for c in f.classes:
                all_classes.append((c, f))
        
        if all_classes:
            report.append("\n### 🏛️ 类/结构体")
            report.append("\n| 类名 | 所在文件 | 基类 |")
            report.append("|------|----------|------|")
            for cls, code_file in sorted(all_classes, key=lambda x: x[0].name):
                rel_path = os.path.relpath(code_file.file_path, analysis.project_path)
                bases = ', '.join(cls.base_classes) if cls.base_classes else '-'
                report.append(f"| `{cls.name}` | `{rel_path}` | {bases} |")
        
        # 所有函数（按文件分组，去私有）
        all_funcs = []
        for f in analysis.files:
            for func in f.functions:
                if not func.name.startswith('_'):
                    all_funcs.append((func, f))
        
        if all_funcs:
            report.append("\n### 🔧 公开函数/方法")
            report.append("\n| 函数名 | 所在文件 | 参数 |")
            report.append("|--------|----------|------|")
            for func, code_file in sorted(all_funcs, key=lambda x: (os.path.relpath(x[1].file_path, analysis.project_path), x[0].name)):
                rel_path = os.path.relpath(code_file.file_path, analysis.project_path)
                params = ', '.join(func.parameters[:4])
                if len(func.parameters) > 4:
                    params += '...'
                report.append(f"| `{func.name}` | `{rel_path}` | `{params}` |")
        
        # ==================== 5. 依赖分析 ====================
        report.append("\n## 五、依赖关系分析")
        
        # 收集每个文件的关键依赖
        file_deps = []
        for f in analysis.files:
            if f.dependencies:
                rel_path = os.path.relpath(f.file_path, analysis.project_path)
                file_deps.append((rel_path, f.dependencies))
        
        if analysis.dependencies:
            report.append("\n### 📦 外部依赖")
            for dep in sorted(analysis.dependencies)[:30]:
                report.append(f"- `{dep}`")
            if len(analysis.dependencies) > 30:
                report.append(f"- ... 及其他 {len(analysis.dependencies) - 30} 个依赖")
        
        if file_deps:
            report.append("\n### 🔗 文件级依赖")
            report.append("\n| 文件 | 依赖 |")
            report.append("|------|------|")
            for rel_path, deps in file_deps:
                dep_str = ', '.join(f'`{d}`' for d in sorted(deps)[:6])
                if len(deps) > 6:
                    dep_str += f' ...(+{len(deps)-6})'
                report.append(f"| `{rel_path}` | {dep_str} |")
        
        # ==================== 6. 代码度量 ====================
        report.append("\n## 六、代码度量")
        
        # 计算各维度指标
        file_class_ratios = [(f, len(f.classes)) for f in analysis.files if len(f.classes) > 0]
        file_func_ratios = [(f, len(f.functions)) for f in analysis.files if len(f.functions) > 0]
        
        avg_classes_per_file = total_classes / total_files if total_files > 0 else 0
        avg_funcs_per_file = total_functions / total_files if total_files > 0 else 0
        avg_lines_per_file = total_lines / total_files if total_files > 0 else 0
        avg_imports_per_file = total_imports / total_files if total_files > 0 else 0
        
        # 函数量最多的文件
        most_funcs = sorted(file_func_ratios, key=lambda x: -x[1])[:3]
        # 类最多的文件
        most_classes = sorted(file_class_ratios, key=lambda x: -x[1])[:3]
        
        report.append("\n| 指标 | 数值 |")
        report.append("|------|------|")
        report.append(f"| 平均每文件类数 | {avg_classes_per_file:.2f} |")
        report.append(f"| 平均每文件函数数 | {avg_funcs_per_file:.2f} |")
        report.append(f"| 平均每文件行数 | {avg_lines_per_file:.0f} |")
        report.append(f"| 平均每文件导入数 | {avg_imports_per_file:.1f} |")
        
        if most_funcs:
            report.append("\n**函数最密集的文件**:")
            for f, count in most_funcs:
                rel_path = os.path.relpath(f.file_path, analysis.project_path)
                report.append(f"- `{rel_path}` — {count} 个函数")
        
        if most_classes:
            report.append("\n**类最集中的文件**:")
            for f, count in most_classes:
                rel_path = os.path.relpath(f.file_path, analysis.project_path)
                report.append(f"- `{rel_path}` — {count} 个类")
        
        # ==================== 7. 技术栈评估 ====================
        if analysis.tech_stack:
            report.append("\n## 七、技术栈评估")
            for tech in analysis.tech_stack:
                report.append(f"- ✅ {tech}")
        
        # ==================== 8. 核心功能 ====================
        if analysis.core_features:
            report.append("\n## 八、核心功能识别")
            for feature in analysis.core_features:
                report.append(f"- 🎯 {feature}")
        
        # ==================== 9. 入口点检测 ====================
        report.append("\n## 九、入口点检测")
        entry_files = []
        for f in analysis.files:
            basename = os.path.basename(f.file_path)
            if basename in ('main.py', 'index.js', 'app.py', 'server.py', '__init__.py', 'index.ts'):
                entry_files.append(f)
        
        if entry_files:
            for f in entry_files:
                rel_path = os.path.relpath(f.file_path, analysis.project_path)
                report.append(f"- 🚪 `{rel_path}`")
        else:
            report.append("- 未检测到标准入口文件")
        
        report.append("\n---")
        report.append("\n*报告由 CodeRef-AI 代码分析引擎自动生成*")
        
        return '\n'.join(report)


    # ==================== GitNexus 增强通道 ====================

    def _enhance_with_gitnexus(self, analysis: ProjectAnalysis):
        """用GitNexus图谱数据增强分析结果
        
        通过MCP查询GitNexus图数据库，获取：
        - 执行流（process）信息
        - 函数集群（community）信息
        - 入口点的上下游关系
        """
        from .gitnexus_client import GitNexusMCPClient
        
        logger.info(f"[GitNexus] 尝试增强分析，项目路径: {analysis.project_path}")
        
        with GitNexusMCPClient(project_path=analysis.project_path) as client:
            # 1. 获取已索引的仓库列表
            repos = client.list_repos()
            logger.info(f"[GitNexus] 发现 {len(repos)} 个索引仓库: {[r.get('name') for r in repos]}")
            if not repos:
                logger.info("[GitNexus] 当前项目无索引数据，跳过增强")
                return
            
            # 2. 搜索项目中的关键符号，补充进程/集群信息
            # 查找入口点文件
            entry_files = []
            for f in analysis.files:
                basename = os.path.basename(f.file_path)
                if basename in ('main.py', 'index.js', 'app.py', 'server.py', 'v4_main.py'):
                    entry_files.append(f)
            
            # 3. 对每个入口函数查询上下文
            for ef in entry_files:
                for func in ef.functions:
                    if func.name in ('main', 'run', 'start', 'serve', 'app'):
                        try:
                            context = client.get_context(func.name)
                            if isinstance(context, dict):
                                # 补充进程信息到架构摘要
                                processes = context.get("processes", [])
                                if processes:
                                    proc_names = []
                                    for p in processes[:5]:
                                        if isinstance(p, dict):
                                            proc_names.append(p.get("name", str(p)))
                                        else:
                                            proc_names.append(str(p))
                                    if proc_names:
                                        analysis.architecture_summary += (
                                            f"\n[GitNexus] 检测到执行流: {', '.join(proc_names)}"
                                        )
                        except Exception as e:
                            logger.warning(f"应用 GitNexus 执行流增强失败，跳过: {e}")
            
            # 4. 用混合搜索发现项目中的关键模块
            try:
                project_name = os.path.basename(analysis.project_path.rstrip('/\\'))
                search_results = client.search(project_name)
                if isinstance(search_results, dict):
                    clusters = search_results.get("clusters", [])
                    if clusters:
                        cluster_info = []
                        for c in clusters[:10]:
                            if isinstance(c, dict):
                                name = c.get("name", "")
                                cohesion = c.get("cohesion", "")
                                if name:
                                    cluster_info.append(f"{name}(内聚度:{cohesion})" if cohesion else name)
                        if cluster_info:
                            analysis.architecture_summary += (
                                f"\n[GitNexus] 功能集群: {', '.join(cluster_info)}"
                            )
            except Exception as e:
                logger.warning(f"应用 GitNexus 功能集群增强失败，跳过: {e}")
            
            logger.info("[CodeAnalyzer] GitNexus增强完成")

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
        """执行 gitnexus 命令行并返回 stdout（列表式参数，无 shell 注入面）

        Args:
            cmd: gitnexus 子命令参数列表（如 ["export", "--entry", "main", ...]）
            project_path: GitNexus 索引所在的项目目录

        Returns:
            命令 stdout 字符串；失败返回 None
        """
        try:
            argv = list(cmd)
            if not argv or argv[0] != "gitnexus":
                argv = ["gitnexus"] + argv
            result = subprocess.run(
                argv,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=120,
                cwd=project_path,
                stdin=subprocess.DEVNULL,
            )
            if result.returncode != 0:
                logger.warning(f"[CodeAnalyzer] gitnexus 命令失败: {result.stderr.strip()[:200]}")
                return None
            return result.stdout
        except FileNotFoundError:
            logger.warning("[CodeAnalyzer] gitnexus CLI 未安装，请执行: npm install -g gitnexus")
            return None
        except subprocess.TimeoutExpired:
            logger.warning("[CodeAnalyzer] gitnexus 命令执行超时（120s）")
            return None
        except Exception as e:
            logger.warning(f"[CodeAnalyzer] gitnexus 调用异常: {e}")
            return None

    def generate_mermaid_diagram(self, analysis: ProjectAnalysis) -> str:
        """基于分析结果生成Mermaid架构图
        
        利用 auto_classifier 自动分层，生成带子图的 Mermaid flowchart
        
        Args:
            analysis: 项目分析结果
        
        Returns:
            Mermaid代码字符串
        """
        from .diagram_generator import generate_mermaid, classify_nodes
        
        # 构建节点列表
        nodes = []
        for f in analysis.files:
            rel_path = os.path.relpath(f.file_path, analysis.project_path)
            # 每个文件作为一个节点
            nodes.append({
                "name": rel_path,
                "filePath": rel_path,
            })
        
        # 构建边列表（基于项目内导入关系）
        edges = []
        for f in analysis.files:
            rel_path = os.path.relpath(f.file_path, analysis.project_path)
            for imp in f.project_imports[:10]:  # 限制每个文件最多10条边
                edges.append({
                    "source": rel_path,
                    "target": imp,
                    "relation_type": "imports",
                })
        
        project_name = os.path.basename(analysis.project_path.rstrip('/\\'))
        
        return generate_mermaid(
            nodes=nodes[:50],  # 限制节点数避免图过大
            edges=edges[:100],
            entry_point="",
            title=f"{project_name} - 模块依赖图",
        )


    def generate_ai_report(self, analysis: ProjectAnalysis) -> str:
        """
        生成「给AI辅助编程LLM看的」全代码审计报告
        
        不再输出代码结构描述（MCP已能提供），而是专注于：
        - Bug/错误发现
        - 安全问题检测
        - 代码质量评估
        - 性能风险识别
        - 设计模式违规
        """
        lines = []
        
        # ==================== 头部 ====================
        lines.append("# 🔍 全代码审计报告（AI辅助编程版）")
        lines.append("")
        lines.append(f"> 项目: `{analysis.project_path}`")
        lines.append(f"> 扫描文件数: {analysis.total_files} | 总行数: {analysis.total_lines:,}")
        lines.append(f"> 生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
        lines.append("")
        lines.append("> 本报告专为AI辅助编程LLM设计，聚焦代码审计维度（Bug/安全/质量/性能/设计），")
        lines.append("> 不再包含代码结构描述（请通过MCP工具获取实时代码上下文）。")
        lines.append("")
        lines.append("---")
        lines.append("")
        
        # 运行所有审计规则
        audit_results = self._run_code_audit(analysis)
        
        # 验证审计结果的行号是否仍然有效（防止缓存导致行号过时）
        audit_results = self._verify_line_numbers(analysis, audit_results)
        
        # ==================== 一、审计摘要 ====================
        lines.append("## 一、审计摘要")
        lines.append("")
        
        total_issues = sum(len(v) for v in audit_results.values())
        critical = sum(1 for cat in audit_results.values() for item in cat if item.get('severity') == 'critical')
        high = sum(1 for cat in audit_results.values() for item in cat if item.get('severity') == 'high')
        medium = sum(1 for cat in audit_results.values() for item in cat if item.get('severity') == 'medium')
        low = sum(1 for cat in audit_results.values() for item in cat if item.get('severity') == 'low')
        
        lines.append(f"| 维度 | 问题数 | 严重 | 高 | 中 | 低 |")
        lines.append(f"|------|--------|------|----|----|----|")
        for category, items in audit_results.items():
            c = sum(1 for i in items if i.get('severity') == 'critical')
            h = sum(1 for i in items if i.get('severity') == 'high')
            m = sum(1 for i in items if i.get('severity') == 'medium')
            l = sum(1 for i in items if i.get('severity') == 'low')
            lines.append(f"| {category} | {len(items)} | {c} | {h} | {m} | {l} |")
        lines.append(f"| **总计** | **{total_issues}** | **{critical}** | **{high}** | **{medium}** | **{low}** |")
        lines.append("")
        
        # ==================== 二~六、各审计维度详情 ====================
        category_titles = {
            'bugs': ('二、Bug与错误', '🔴'),
            'security': ('三、安全问题', '🔒'),
            'quality': ('四、代码质量', '📐'),
            'performance': ('五、性能风险', '⚡'),
            'design': ('六、设计问题', '🏗️'),
        }
        
        for cat_key, (title, emoji) in category_titles.items():
            items = audit_results.get(cat_key, [])
            lines.append(f"## {title}")
            lines.append("")
            
            if not items:
                lines.append(f"{emoji} 未发现此类问题。")
                lines.append("")
                continue
            
            # 按严重度排序
            severity_order = {'critical': 0, 'high': 1, 'medium': 2, 'low': 3}
            items_sorted = sorted(items, key=lambda x: severity_order.get(x.get('severity', 'low'), 3))
            
            for item in items_sorted:
                sev = item.get('severity', 'low')
                sev_emoji = {'critical': '🔴', 'high': '🟠', 'medium': '🟡', 'low': '🔵'}.get(sev, '⚪')
                file_path = item.get('file', '未知文件')
                line = item.get('line', '-')
                desc = item.get('description', '')
                suggestion = item.get('suggestion', '')
                
                lines.append(f"### {sev_emoji} [{sev.upper()}] `{file_path}:{line}`")
                lines.append("")
                lines.append(f"**问题**: {desc}")
                if suggestion:
                    lines.append(f"**建议**: {suggestion}")
                lines.append("")
            
            lines.append("---")
            lines.append("")
        
        # ==================== 七、修复优先级建议 ====================
        lines.append("## 七、修复优先级建议")
        lines.append("")
        lines.append("基于审计结果，建议按以下优先级处理：")
        lines.append("")
        
        all_issues = []
        for cat, items in audit_results.items():
            for item in items:
                all_issues.append(item)
        
        # 按严重度+影响范围排序
        all_issues.sort(key=lambda x: (
            {'critical': 0, 'high': 1, 'medium': 2, 'low': 3}.get(x.get('severity', 'low'), 3),
            -len(x.get('description', ''))
        ))
        
        if all_issues:
            lines.append("| 优先级 | 文件 | 问题简述 | 建议操作 |")
            lines.append("|--------|------|----------|----------|")
            for i, item in enumerate(all_issues[:20], 1):  # 最多显示前20个
                sev = item.get('severity', 'low')
                file_path = item.get('file', '未知')
                desc = item.get('description', '')[:40] + '...' if len(item.get('description', '')) > 40 else item.get('description', '')
                suggestion = item.get('suggestion', '')[:30] + '...' if len(item.get('suggestion', '')) > 30 else item.get('suggestion', '')
                lines.append(f"| P{i} ({sev}) | `{file_path}` | {desc} | {suggestion or '审查修复'} |")
            lines.append("")
        else:
            lines.append("✅ 未发现需要修复的问题。")
            lines.append("")
        
        lines.append("---")
        lines.append("*全代码审计报告 · 供AI辅助编程LLM参考*")
        
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
            
            # ===== Bug检测 =====
            self._audit_bugs(cf, rel_path, content, lines_content, results['bugs'])
            
            # ===== 安全检测 =====
            self._audit_security(cf, rel_path, content, lines_content, results['security'])
            
            # ===== 质量检测 =====
            self._audit_quality(cf, rel_path, content, lines_content, results['quality'])
            
            # ===== 性能检测 =====
            self._audit_performance(cf, rel_path, content, lines_content, results['performance'])
            
            # ===== 设计检测 =====
            self._audit_design(cf, rel_path, content, lines_content, results['design'])
        
        return dict(results)
    
    def _verify_line_numbers(self, analysis: ProjectAnalysis, audit_results: Dict[str, List[Dict]]) -> Dict[str, List[Dict]]:
        """
        验证审计结果的行号是否仍然有效，移除因文件变更（缓存过时）而过期的 findings
        
        对于每个包含行号的 finding，检查：
        - 文件是否仍然存在
        - 行号是否在文件范围内
        - 该行的内容是否仍包含与 description 相关的关键词
        """
        verified = {}
        for category, items in audit_results.items():
            verified_items = []
            for item in items:
                if 'line' not in item or 'file' not in item:
                    verified_items.append(item)
                    continue
                
                file_path = os.path.join(analysis.project_path, item['file'])
                line_no = item['line']
                
                # 检查文件是否存在
                if not os.path.isfile(file_path):
                    continue  # 文件已删除/移动，移除该 finding
                
                try:
                    # 只读取到目标行（采样），避免大文件整读 `readlines()` 造成内存峰值
                    from itertools import islice
                    with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                        file_lines = list(islice(f, line_no))

                    # 检查行号是否在文件范围内
                    if line_no < 1 or len(file_lines) < line_no:
                        continue

                    actual_line = file_lines[line_no - 1]
                    
                    # 从 description 中提取反引号内的关键词，检查该行是否仍包含它们
                    desc = item.get('description', '')
                    keywords = re.findall(r'`([^`]+)`', desc)
                    if keywords:
                        found = any(kw in actual_line for kw in keywords)
                        if not found:
                            continue  # 内容不匹配，移除该 finding
                    
                    verified_items.append(item)
                except Exception:
                    # 读取失败时保守保留 finding
                    verified_items.append(item)
            
            if verified_items:
                verified[category] = verified_items
        
        return verified
    
    def _audit_bugs(self, cf: CodeFile, rel_path: str, content: str, lines: List[str], issues: List[Dict]):
        """Bug与错误检测（注意：content 来自 CodeFile.raw_content，缓存不截断后为完整内容）"""
        # 1. logger未定义检测（精准：排除跨模块导入 logger 的情况）
        if re.search(r'\blogger\.(debug|info|warning|error|critical)\b', content):
            has_logging_import = bool(re.search(r'import\s+logging', content))
            has_logger_def = bool(re.search(r'\blogger\s*=\s*(?:logging\.getLogger|getLogger)', content))
            
            # 跨模块导入追踪：from X import ... logger ...
            # 单行匹配：from config import logger / from config import x, logger, y
            has_logger_import_single = bool(re.search(r'\bfrom\s+\S+\s+import\s+[^)]*\blogger\b', content))
            
            # 多行匹配：from config import ( \n    x, \n    logger, \n )
            has_logger_import_multi = False
            multi_import_matches = list(re.finditer(r'\bfrom\s+\S+\s+import\s*\(', content))
            for m in multi_import_matches:
                # 从 import ( 开始，找到匹配的 )
                paren_start = m.end()
                depth = 1
                pos = paren_start
                while pos < len(content) and depth > 0:
                    if content[pos] == '(':
                        depth += 1
                    elif content[pos] == ')':
                        depth -= 1
                    pos += 1
                if depth == 0:
                    import_block = content[m.end():pos-1]
                    if re.search(r'\blogger\b', import_block):
                        has_logger_import_multi = True
                        break
            
            # import logger 别名：from loguru import logger as log
            has_logger_alias = bool(re.search(r'\bfrom\s+\S+\s+import\s+.*\blogger\s+as\s+\w+', content))
            
            has_logger_import = has_logging_import or has_logger_def or \
                                has_logger_import_single or has_logger_import_multi or has_logger_alias
            
            if not has_logger_import:
                for i, line in enumerate(lines, 1):
                    if re.search(r'\blogger\.(debug|info|warning|error|critical)\b', line):
                        issues.append({
                            'severity': 'high',
                            'file': rel_path,
                            'line': i,
                            'description': 'logger未定义：使用logger.xxx()但无import logging或导入logger',
                            'suggestion': '添加 `import logging` 和 `logger = logging.getLogger(__name__)`'
                        })
                        break
        
        # 2. 异常处理缺失（函数级try块检测，使用函数/类作用域而非行级回溯）
        if cf.language == 'python':
            risky_calls = ['requests.', 'urllib', 'socket.', 'subprocess.', 'open(', 'httpx.']
            for risky in risky_calls:
                for i, line in enumerate(lines, 1):
                    if risky in line:
                        # 查找包含该行的函数/类作用域
                        in_try = False
                        scope_start = None
                        scope_end = None
                        
                        # 优先检查函数作用域（包括类方法）
                        for func in cf.functions:
                            if func.start_line <= i <= func.end_line:
                                scope_start = func.start_line
                                scope_end = func.end_line
                                break
                        
                        # 如果不在独立函数内，检查类方法
                        if scope_start is None:
                            for cls in cf.classes:
                                for method in cls.methods:
                                    if method.start_line <= i <= method.end_line:
                                        scope_start = method.start_line
                                        scope_end = method.end_line
                                        break
                                if scope_start is not None:
                                    break
                        
                        # 如果不在方法内但位于类体中，检查整个类
                        if scope_start is None:
                            for cls in cf.classes:
                                if cls.start_line <= i <= cls.end_line:
                                    scope_start = cls.start_line
                                    scope_end = cls.end_line
                                    break
                        
                        if scope_start is not None and scope_end is not None:
                            # 在函数/类作用域内：检查整个作用域是否有 try
                            for j in range(scope_start, min(scope_end + 1, len(lines) + 1)):
                                check_line = lines[j - 1].strip() if j <= len(lines) else ''
                                if check_line.startswith('try:') or check_line == 'try:':
                                    in_try = True
                                    break
                        else:
                            # 模块级代码：只检查周围5行
                            for j in range(i - 1, max(i - 5, 0), -1):
                                check_line = lines[j - 1].strip() if j > 0 else ''
                                if check_line.startswith('try:') or check_line == 'try:':
                                    in_try = True
                                    break
                        
                        if not in_try:
                            issues.append({
                                'severity': 'medium',
                                'file': rel_path,
                                'line': i,
                                'description': f'调用 `{risky}` 可能缺少异常处理',
                                'suggestion': '添加 try/except 块处理IO/网络异常'
                            })
                            break
    
    def _audit_security(self, cf: CodeFile, rel_path: str, content: str, lines: List[str], issues: List[Dict]):
        """安全问题检测（AST 精确分类 + 正则回退）"""
        # 1. 硬编码密钥/Token — 优先使用 AST 精确分类
        ast_assignments = getattr(cf, 'ast_assignments', [])
        if ast_assignments:
            # AST 解析可用：只报告确认为 hardcoded 的赋值
            for assign in ast_assignments:
                if assign.category == "hardcoded":
                    issues.append({
                        'severity': 'high',
                        'file': rel_path,
                        'line': assign.line,
                        'description': f'硬编码凭据: {assign.target} = {assign.value_repr[:50]}',
                        'suggestion': '使用环境变量或密钥管理服务（os.environ.get()）'
                    })
        else:
            # AST 不可用：回退到正则（但增加排除逻辑）
            secret_patterns = [
                (r'\bapi[_-]?key\b\s*=\s*["\'][^"\']{10,}["\']', '硬编码API Key'),
                (r'\bsecret\b\s*=\s*["\'][^"\']{8,}["\']', '硬编码Secret'),
                (r'\btoken\b\s*=\s*["\'][^"\']{10,}["\']', '硬编码Token'),
                (r'\bpassword\b\s*=\s*["\'][^"\']{4,}["\']', '硬编码密码'),
            ]
            # 排除模式
            exclude_patterns = [
                r'\.get\s*\(',          # config.get()
                r'os\.(?:environ|getenv)',  # os.environ / os.getenv
                r'^[A-Z_]{4,}\s*=\s*["\'][A-Z_\d]+["\']',  # 错误码常量
                r'MISSING_|_MISSING',    # 错误码
            ]
            for pattern, desc in secret_patterns:
                for i, line in enumerate(lines, 1):
                    if re.search(pattern, line, re.IGNORECASE):
                        # 排除配置读取和错误码常量
                        if any(re.search(ep, line, re.IGNORECASE) for ep in exclude_patterns):
                            continue
                        issues.append({
                            'severity': 'high',
                            'file': rel_path,
                            'line': i,
                            'description': f'发现{desc}',
                            'suggestion': '使用环境变量或密钥管理服务'
                        })
                        break
        
        # 2. SQL注入风险
        sql_patterns = [
            r'execute\s*\(\s*["\'].*%s',
            r'execute\s*\(\s*["\'].*\+',
            r'execute\s*\(\s*f["\']',
        ]
        for pattern in sql_patterns:
            for i, line in enumerate(lines, 1):
                if re.search(pattern, line, re.IGNORECASE):
                    issues.append({
                        'severity': 'critical',
                        'file': rel_path,
                        'line': i,
                        'description': '可能的SQL注入风险：字符串拼接SQL',
                        'suggestion': '使用参数化查询（parameterized queries）'
                    })
                    break
        
        # 3. 路径遍历风险
        for i, line in enumerate(lines, 1):
            if 'open(' in line and ('+' in line or 'f"' in line or "f'" in line):
                if 'pathlib' not in content and 'os.path.join' not in line:
                    issues.append({
                        'severity': 'medium',
                        'file': rel_path,
                        'line': i,
                        'description': '文件路径可能包含用户输入，存在路径遍历风险',
                        'suggestion': '使用 pathlib.Path.resolve() 和路径验证'
                    })
                    break
        
        # 4. 不安全的反序列化
        for i, line in enumerate(lines, 1):
            if 'pickle.loads' in line or 'yaml.load(' in line:
                issues.append({
                    'severity': 'high',
                    'file': rel_path,
                    'line': i,
                    'description': '使用不安全的反序列化方法',
                    'suggestion': 'pickle→json; yaml.load→yaml.safe_load'
                })
                break
        
        # 5. eval/exec 使用
        for i, line in enumerate(lines, 1):
            if re.search(r'\beval\s*\(', line) or re.search(r'\bexec\s*\(', line):
                issues.append({
                    'severity': 'critical',
                    'file': rel_path,
                    'line': i,
                    'description': '使用 eval/exec 执行动态代码',
                    'suggestion': '避免使用eval/exec，改用ast.literal_eval或安全替代方案'
                })
                break
        
        # 6. 硬编码Windows路径（带过滤）
        # 跳过测试文件和 __pycache__ 目录
        if '__pycache__' not in rel_path and '/test_' not in rel_path.replace('\\', '/') and not rel_path.startswith('test_'):
            win_path_pattern = re.compile(r'(?<![a-zA-Z])[A-Za-z]:(?:[\\/][^\\/"\'\n\r\t\)\]\}]+)+')
            for i, line in enumerate(lines, 1):
                stripped = line.strip()
                # 跳过注释行
                if stripped.startswith('#') or stripped.startswith('//') or stripped.startswith('/*') or stripped.startswith('*'):
                    continue
                # 跳过日志消息模板（包含 {} 或 %s/%d 等占位符）
                if re.search(r'[\{\}]|%[sd]|%\(', line):
                    continue
                # 跳过UI标签/显示文本（包含常见UI关键词的短行）
                if any(kw in line.lower() for kw in ['路径', '文件', '目录', 'folder', 'path', 'directory', 'file']):
                    if len(stripped) < 100:
                        continue
                # 跳过默认值/示例
                if re.search(r'example|sample|your[-\s]|默认|示例', line, re.IGNORECASE):
                    continue
                # 实际检测硬盘路径
                if win_path_pattern.search(line):
                    issues.append({
                        'severity': 'low',
                        'file': rel_path,
                        'line': i,
                        'description': f'硬编码Windows路径: `{line.strip()[:60]}`',
                        'suggestion': '使用 os.path.join() 或 pathlib.Path 构建路径'
                    })
    
    def _audit_quality(self, cf: CodeFile, rel_path: str, content: str, lines: List[str], issues: List[Dict]):
        """代码质量检测"""
        # 1. 函数过长
        for func in cf.functions:
            func_lines = func.end_line - func.start_line
            if func_lines > 100:
                issues.append({
                    'severity': 'medium',
                    'file': rel_path,
                    'line': func.start_line,
                    'description': f'函数 `{func.name}` 过长 ({func_lines} 行)',
                    'suggestion': '拆分为多个小函数，遵循单一职责原则'
                })
            elif func_lines > 50:
                issues.append({
                    'severity': 'low',
                    'file': rel_path,
                    'line': func.start_line,
                    'description': f'函数 `{func.name}` 较长 ({func_lines} 行)',
                    'suggestion': '考虑拆分或提取辅助函数'
                })
        
        # 2. 类过大
        for cls in cf.classes:
            cls_lines = cls.end_line - cls.start_line
            if cls_lines > 300:
                issues.append({
                    'severity': 'medium',
                    'file': rel_path,
                    'line': cls.start_line,
                    'description': f'类 `{cls.name}` 过大 ({cls_lines} 行)',
                    'suggestion': '拆分为多个类或使用组合替代继承'
                })
        
        # 3. 参数过多
        for func in cf.functions:
            if len(func.parameters) > 7:
                issues.append({
                    'severity': 'low',
                    'file': rel_path,
                    'line': func.start_line,
                    'description': f'函数 `{func.name}` 参数过多 ({len(func.parameters)} 个)',
                    'suggestion': '使用dataclass或dict封装参数'
                })
        
        # 4. TODO/FIXME 标记
        for i, line in enumerate(lines, 1):
            if 'TODO' in line or 'FIXME' in line or 'HACK' in line:
                issues.append({
                    'severity': 'low',
                    'file': rel_path,
                    'line': i,
                    'description': f'发现技术债务标记: {line.strip()[:60]}',
                    'suggestion': '安排时间清理或转化为正式issue'
                })
    
    def _audit_performance(self, cf: CodeFile, rel_path: str, content: str, lines: List[str], issues: List[Dict]):
        """性能风险检测"""
        # 1. 文件级循环中的IO操作
        for i, line in enumerate(lines, 1):
            window = '\n'.join(lines[i:i + 10])
            if ('for ' in line or 'while ' in line) and 'open(' in window:
                issues.append({
                    'severity': 'medium',
                    'file': rel_path,
                    'line': i,
                    'description': '循环中可能包含文件IO操作',
                    'suggestion': '将IO操作移出循环，或使用批量读写'
                })
                break
        
        # 2. 字符串拼接在循环中
        for i, line in enumerate(lines, 1):
            if ('for ' in line or 'while ' in line) and ('+=' in line and '"' in line):
                issues.append({
                    'severity': 'low',
                    'file': rel_path,
                    'line': i,
                    'description': '循环中使用字符串拼接',
                    'suggestion': '使用列表+join或StringIO替代+=拼接'
                })
                break
        
        # 3. 潜在的内存泄漏（全局缓存无上限）
        if 'cache' in content.lower() and 'maxsize' not in content.lower():
            for i, line in enumerate(lines, 1):
                if '@lru_cache' in line and 'maxsize' not in line:
                    issues.append({
                        'severity': 'low',
                        'file': rel_path,
                        'line': i,
                        'description': 'lru_cache未设置maxsize，可能导致内存无限增长',
                        'suggestion': '添加 maxsize 参数限制缓存大小'
                    })
                    break
    
    def _audit_design(self, cf: CodeFile, rel_path: str, content: str, lines: List[str], issues: List[Dict]):
        """设计问题检测"""
        # 1. sys.path 动态注入
        if cf.sys_path_inserts:
            for spi in cf.sys_path_inserts:
                issues.append({
                    'severity': 'medium',
                    'file': rel_path,
                    'line': 1,  # 无法精确定位行号
                    'description': f'使用 sys.path.insert 动态注入路径: `{spi}`',
                    'suggestion': '改为相对导入或包结构重构'
                })
        
        # 2. 循环依赖检测（简单：A导入B，B导入A）
        # 这个需要在全局层面检测，这里只做文件级标记
        
        # 3. 上帝类检测（方法过多）
        for cls in cf.classes:
            if len(cls.methods) > 20:
                issues.append({
                    'severity': 'medium',
                    'file': rel_path,
                    'line': cls.start_line,
                    'description': f'类 `{cls.name}` 方法过多 ({len(cls.methods)} 个)，可能是上帝类',
                    'suggestion': '拆分为多个职责单一的类'
                })
        
        # 4. 重复代码检测（简单：相同import模式）
        # 复杂重复检测需要AST级分析，这里跳过
        
        # 5. 硬编码配置（排除注释、字符串常量、文档路径等合理场景）
        hardcoded_patterns = [
            (r'localhost:\d+', '硬编码本地服务地址'),
            (r'127\.0\.0\.1:\d+', '硬编码本地IP地址'),
        ]
        for pattern, desc in hardcoded_patterns:
            for i, line in enumerate(lines, 1):
                if re.search(pattern, line) and '#' not in line and '"""' not in line:
                    issues.append({
                        'severity': 'low',
                        'file': rel_path,
                        'line': i,
                        'description': f'{desc}: `{line.strip()[:50]}`',
                        'suggestion': '使用配置文件或环境变量'
                    })
                    break
