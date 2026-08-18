# -*- coding: utf-8 -*-
"""
盲区检测器 —— 检测 AI、用户、代码之间的三方知识盲区

场景：用户不懂代码，AI 不知道用户不知道什么，AI 也不知道自己不知道什么。
本模块从多个维度扫描项目，找出"应该知道但不知道"的信息盲区。

检测维度：
1. 文档盲区：有代码但缺少文档的模块
2. 缺失依赖：import 了但目录中不存在的模块（可能是外部依赖或遗漏）
3. 动态路径注入：sys.path 动态修改，标记可能的外部依赖盲区
4. GitNexus 符号索引覆盖：哪些文件有符号但未被索引
5. 空文件：只有 import 没有实际代码的"占位"文件

与 CodeSimplifier / GovernanceAuditor 互补：
- CodeSimplifier 聚焦代码精简（YAGNI、死代码、过度工程）
- GovernanceAuditor 聚焦安全与架构合规（铁律、错题本）
- BlindSpotDetector 聚焦知识盲区（用户不知道、AI 不知道、代码缺失）

作者: PersuadeAI Team
版本: v1.0
"""

import ast
import os
import re
import sys
from typing import Dict, List, Optional, Set, Tuple
from collections import defaultdict
from dataclasses import dataclass, field

from loguru import logger
from core.shared_filter import SharedFilter


# ═══════════════════════════════════════════════════════════════════
# 数据结构
# ═══════════════════════════════════════════════════════════════════

@dataclass
class BlindSpot:
    """单条盲区检测结果

    Attributes:
        category: 分类（doc_blindspot / missing_dependency / dynamic_path /
                  unindexed_symbol / empty_file）
        item: 条目名称（模块名、文件名等）
        detail: 详细描述
        file_path: 关联文件路径
        risk_level: 风险等级（high / medium / low）
        user_should_know: 面向不懂代码的用户的解释
    """
    category: str
    item: str
    detail: str
    file_path: str
    risk_level: str
    user_should_know: str


RISK_ORDER = {"high": 0, "medium": 1, "low": 2}

CATEGORY_LABELS = {
    "doc_blindspot": "文档盲区",
    "missing_dependency": "缺失依赖",
    "dynamic_path": "动态路径注入",
    "unindexed_symbol": "符号索引盲区",
    "empty_file": "空文件",
}


# ═══════════════════════════════════════════════════════════════════
# 盲区检测器
# ═══════════════════════════════════════════════════════════════════

class BlindSpotDetector:
    """
    盲区检测器

    检测三方盲区（用户、AI、代码），帮助用户了解"自己不知道什么"和
    "AI 可能不知道什么"。
    """

    def __init__(self):
        self._all_py_files: List[str] = []
        self._all_imports: Dict[str, Set[str]] = {}  # file_path -> set of imports
        self._all_module_names: Set[str] = set()

    def detect(self, project_path: str) -> str:
        """
        执行盲区检测并生成报告

        Args:
            project_path: 项目路径

        Returns:
            Markdown 格式的盲区检测报告
        """
        logger.info(f"[BlindSpotDetector] 开始扫描: {project_path}")

        # 加载项目专属的 cache 硬编码优化（白名单）
        SharedFilter.load_cache(project_path)

        # 收集项目基础信息
        self._collect_project_info(project_path)

        spots: List[BlindSpot] = []

        # 1. 文档盲区
        spots.extend(self._detect_doc_blindspots(project_path))
        # 2. 缺失依赖
        spots.extend(self._detect_missing_dependencies(project_path))
        # 3. 动态路径注入
        spots.extend(self._detect_dynamic_paths(project_path))
        # 4. GitNexus 符号索引盲区（可选）
        spots.extend(self._detect_unindexed_symbols(project_path))
        # 5. 空文件
        spots.extend(self._detect_empty_files(project_path))

        # 按风险等级排序
        spots.sort(key=lambda s: (RISK_ORDER.get(s.risk_level, 9), s.item))

        logger.info(f"[BlindSpotDetector] 检测完成: {len(spots)} 个盲区")

        # 暴露结构化结果，供管线统一收集
        self.spots = spots

        return self._generate_report(project_path, spots)

    # ─── 项目信息收集 ─────────────────────────────────────────────

    def _collect_project_info(self, project_path: str) -> None:
        """收集项目中所有 .py 文件和 import 信息"""
        from core.project_scope import ProjectScope

        self._all_py_files = []
        self._all_imports = {}
        self._all_module_names = set()

        scope = ProjectScope(project_path)
        scope.analyze()

        for root, dirs, files in os.walk(project_path):
            # 使用 ProjectScope 过滤目录
            dirs[:] = [d for d in dirs if scope.should_scan(os.path.join(root, d))]
            for f in files:
                if f.endswith(".py"):
                    fp = os.path.join(root, f)
                    self._all_py_files.append(fp)
                    rel = os.path.relpath(fp, project_path)
                    self._all_module_names.add(rel.replace(os.sep, ".").replace(".py", ""))

                    # 提取 import 语句
                    imports = self._extract_imports(fp)
                    self._all_imports[fp] = imports

        logger.info(f"[BlindSpotDetector] 收集到 {len(self._all_py_files)} 个 .py 文件")

    def _extract_imports(self, file_path: str) -> Set[str]:
        """从 .py 文件中提取所有 import 的模块名"""
        imports = set()
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                content = f.read()
        except Exception:
            return imports

        # docstring/字符串里的示例 import（如本文件 docstring 中的
        # `import tomli as tomllib` 示例）不是真实代码行为，落在字符串
        # 区间内的行跳过，避免把文档示例中的模块名误报为缺失依赖
        str_ranges = self._string_line_ranges(content)

        for idx, line in enumerate(content.split("\n"), 1):
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            if str_ranges and any(lo <= idx <= hi for lo, hi in str_ranges):
                continue
            # from X.Y import Z
            m = re.match(r'from\s+([\w.]+)\s+import', stripped)
            if m:
                imports.add(m.group(1))
            # import X.Y
            m = re.match(r'import\s+([\w.]+)', stripped)
            if m:
                imports.add(m.group(1))
        return imports

    # ─── 检测规则 ─────────────────────────────────────────────────

    def _detect_doc_blindspots(self, project_path: str) -> List[BlindSpot]:
        """检测文档盲区：有 .py 文件的目录，但无对应 .md 文档"""
        spots = []

        # 收集有 .py 文件的目录（去重，取父目录层级）
        py_dirs = set()
        for fp in self._all_py_files:
            d = os.path.dirname(fp)
            py_dirs.add(d)

        # 检查 docs/ 目录
        docs_dir = os.path.join(project_path, "docs")
        if not os.path.isdir(docs_dir):
            # 整个项目没有 docs/ 目录
            for py_dir in sorted(py_dirs):
                rel = os.path.relpath(py_dir, project_path)
                py_count = sum(1 for fp in self._all_py_files
                               if os.path.dirname(fp) == py_dir)
                spots.append(BlindSpot(
                    category="doc_blindspot",
                    item=(rel if rel not in ("", ".") else "根目录"),
                    detail=f"目录下有 {py_count} 个 Python 文件，但项目没有 docs/ 目录",
                    file_path=py_dir,
                    risk_level="high",
                    user_should_know=f"'{rel}' 目录里有 {py_count} 个代码文件，但没有任何文档说明它们是做什么的。你需要请开发者补充文档，否则你无法知道这些代码的功能和用法。",
                ))
            return spots

        # 收集 docs/ 下所有 .md 文件
        doc_files: Set[str] = set()
        for root, _, files in os.walk(docs_dir):
            for f in files:
                if f.endswith(".md"):
                    doc_files.add(os.path.splitext(f)[0].lower())

        # 按父目录分组 .py 文件
        dir_py_count: Dict[str, int] = defaultdict(int)
        for fp in self._all_py_files:
            d = os.path.dirname(fp)
            dir_py_count[d] += 1

        for py_dir, py_count in sorted(dir_py_count.items()):
            rel = os.path.relpath(py_dir, project_path)
            dir_name = os.path.basename(py_dir).lower()

            # 检查是否有对应的文档
            has_doc = False
            for doc_name in doc_files:
                if dir_name in doc_name or doc_name in dir_name:
                    has_doc = True
                    break

            if not has_doc and py_count >= 1:
                risk = "high" if py_count >= 5 else ("medium" if py_count >= 2 else "low")
                spots.append(BlindSpot(
                    category="doc_blindspot",
                    item=(rel if rel not in ("", ".") else "根目录"),
                    detail=f"目录下有 {py_count} 个 Python 文件，但 docs/ 中无对应文档",
                    file_path=py_dir,
                    risk_level=risk,
                    user_should_know=f"'{rel}' 目录里有 {py_count} 个代码文件，但 docs/ 目录中没有对应的文档说明。这意味着没有人写过这些代码的功能说明，AI 也无法准确告诉你这些代码是做什么的。",
                ))

        logger.info(f"[BlindSpotDetector] 文档盲区: {len(spots)} 个")
        return spots

    def _detect_missing_dependencies(self, project_path: str) -> List[BlindSpot]:
        """检测缺失依赖：import 了但项目中不存在的模块"""
        spots = []

        # 收集所有标准库模块名（Python 3.10+）
        stdlib_modules = self._get_stdlib_modules()

        # 收集所有本地模块名
        local_modules = self._all_module_names.copy()

        # 依赖清单中已声明的第三方包（requirements*.txt）：已明确声明的安装
        # 项（如 loguru）不属于"缺失依赖"盲区，跳过避免误报
        declared_deps = self._declared_dependencies(project_path)

        # 项目自身的顶层模块名（项目根下的顶层 .py 文件名 + 顶层目录名）：
        # `from config import settings`、`from core import tool_registry` 的
        # import 根名是项目自己的顶层包目录名，不是缺失的第三方依赖
        local_roots = self._project_local_roots(project_path)

        all_imported: Set[str] = set()
        for imports in self._all_imports.values():
            all_imported.update(imports)

        # 检查每个 import 是否在本地或标准库中存在
        for imp in sorted(all_imported):
            # 跳过相对导入（以 . 开头，如 .auto_classifier）—— 相对导入始终是本地模块
            if imp.startswith("."):
                continue
            # 跳过标准库
            root = imp.split(".")[0]
            if root in stdlib_modules:
                continue
            # 跳过 Python 内置模块
            if root in ("__future__", "__main__"):
                continue
            # 跳过本地模块
            if imp in local_modules:
                continue
            # 检查顶级模块是否在本地
            if root in local_modules:
                continue
            # 跳过项目自身的顶层包/模块（如 config、core 等项目内目录）
            if root in local_roots:
                continue
            # 跳过依赖清单中已声明的第三方包（pip 安装项，非项目内缺失）
            if root.lower().replace("_", "-") in declared_deps:
                continue

            # 找到引用该模块的文件
            ref_files = [fp for fp, imps in self._all_imports.items() if imp in imps]
            # 排除"标准库后备别名"写法：import tomli as tomllib —— 给标准库
            # 模块（tomllib）提供旧版本后备（tomli），是标准兼容写法而非缺依赖
            ref_files = [fp for fp in ref_files
                         if not self._is_stdlib_fallback_alias(fp, imp, stdlib_modules)]
            if not ref_files:
                continue
            for ref_file in ref_files[:3]:  # 最多列 3 个引用
                # 可选依赖（try-import / 函数内延迟 import，缺失时功能自动
                # 降级而非崩溃，如 stdlib_list、selenium）：降级为 low 保持
                # 可见性，不再按 high 缺失处理
                optional = root in self._optional_import_roots(ref_file)
                if optional:
                    detail = (f"import 了 '{imp}'，属可选依赖（try-import / 延迟 "
                              f"import，缺失时功能自动降级），如需该功能请 pip install {root}")
                    user_should_know = (f"文件 '{os.path.basename(ref_file)}' 引用了可选模块 "
                                        f"'{imp}'，当前环境未安装也不影响其它功能。如需启用该功能，"
                                        f"请安装：pip install {root}。")
                    risk_level = "low"
                else:
                    detail = f"import 了 '{imp}'，但项目中找不到该模块（可能是外部依赖，需要 pip install 检查）"
                    user_should_know = f"文件 '{os.path.basename(ref_file)}' 引用了一个叫 '{imp}' 的模块，但项目里找不到这个模块。这可能是需要额外安装的第三方库，或者是一个被删除/遗漏的模块。你需要确认环境是否完整。"
                    risk_level = "high"
                spots.append(BlindSpot(
                    category="missing_dependency",
                    item=imp,
                    detail=detail,
                    file_path=ref_file,
                    risk_level=risk_level,
                    user_should_know=user_should_know,
                ))

        logger.info(f"[BlindSpotDetector] 缺失依赖: {len(spots)} 个")
        return spots

    @staticmethod
    def _declared_dependencies(project_path: str) -> Set[str]:
        """读取项目根目录的依赖声明文件，返回已声明的包名集合。

        目前支持 requirements*.txt（含 requirements-dev.txt 等变体）。
        包名做规范化处理（小写、下划线归一为连字符，与 pip 规则一致）。
        在依赖清单中声明的包是明确的安装项，不构成"缺失依赖"盲区。
        """
        declared: Set[str] = set()
        try:
            names = os.listdir(project_path)
        except OSError:
            return declared
        for name in names:
            if not (name.lower().startswith("requirements") and name.lower().endswith(".txt")):
                continue
            try:
                with open(os.path.join(project_path, name), "r",
                          encoding="utf-8", errors="ignore") as f:
                    content = f.read()
            except OSError as e:
                # 依赖清单不可读，跳过该文件
                logger.warning(f"读取文件失败，跳过依赖清单检查 {name}: {e}")
                continue
            for line in content.splitlines():
                stripped = line.strip()
                if not stripped or stripped.startswith(("#", "-", ";")):
                    continue
                # 取行首包名（兼容 pkg>=1.0 / pkg[extras]==2.0 / pkg~=1.0 等）
                m = re.match(r'[A-Za-z0-9][A-Za-z0-9._\-]*', stripped)
                if m:
                    declared.add(m.group(0).lower().replace("_", "-"))
        return declared

    @staticmethod
    def _project_local_roots(project_path: str) -> Set[str]:
        """收集项目自身的顶层模块名（项目根下的顶层 .py 文件名 + 顶层目录名）。

        项目内顶层包（如 config/、core/）以 `from config import settings`、
        `from core import tool_registry` 形式引用时，import 根名是包目录名
        本身，不在逐文件收集的模块名集合（形如 "config.__init__"）里，
        曾被误判为缺失的第三方依赖；命中本集合即视为项目内模块。
        """
        roots: Set[str] = set()
        try:
            names = os.listdir(project_path)
        except OSError:
            return roots
        for name in names:
            if name.endswith(".py"):
                roots.add(name[:-3])
            elif not name.startswith(".") and os.path.isdir(os.path.join(project_path, name)):
                roots.add(name)
        return roots

    @staticmethod
    def _optional_import_roots(file_path: str) -> Set[str]:
        """收集文件中"可选依赖"import 的模块根名（缺失时功能降级而非崩溃）。

        两类形态：
        1. try/except（ImportError/ModuleNotFoundError/Exception/裸 except）
           包裹的 import（如 ast_parser.py 的 stdlib_list）：缺失时代码
           自动走降级分支；
        2. 函数/方法体内的延迟 import（如 frontend_inspector.py 的
           selenium）：仅在对应功能被调用时加载，调用方捕获异常降级为
           静态分析。
        """
        roots: Set[str] = set()
        try:
            with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read()
        except OSError:
            return roots
        try:
            tree = ast.parse(content)
        except SyntaxError:
            return roots

        def _add_import_roots(stmts) -> None:
            for stmt in stmts:
                if isinstance(stmt, ast.Import):
                    for alias in stmt.names:
                        roots.add(alias.name.split(".")[0])
                elif isinstance(stmt, ast.ImportFrom) and stmt.module and stmt.level == 0:
                    roots.add(stmt.module.split(".")[0])

        def _catches_import_error(handler: ast.ExceptHandler) -> bool:
            t = handler.type
            if t is None:  # 裸 except 同样覆盖 ImportError
                return True
            names: List[str] = []
            elts = t.elts if isinstance(t, ast.Tuple) else [t]
            for el in elts:
                if isinstance(el, ast.Name):
                    names.append(el.id)
                elif isinstance(el, ast.Attribute):
                    names.append(el.attr)
            return any(n in ("ImportError", "ModuleNotFoundError", "Exception")
                       for n in names)

        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                _add_import_roots(node.body)
            elif isinstance(node, ast.Try):
                if any(_catches_import_error(h) for h in node.handlers):
                    _add_import_roots(node.body)
                    for h in node.handlers:
                        _add_import_roots(h.body)
        return roots

    def _is_stdlib_fallback_alias(self, file_path: str, imp: str,
                                  stdlib_modules: Set[str]) -> bool:
        """该 import 是否是"标准库后备别名"写法（不应报缺失依赖）。

        标准 Py3.11 兼容写法（如 ci_compile_check.py）：
            try:
                import tomllib  # Python 3.11+
            except ImportError:
                import tomli as tomllib  # 旧版本回退
        `import tomli as tomllib` 中别名 tomllib 是标准库模块名，说明 tomli
        只是为旧解释器提供同名能力的后备，并非缺失依赖。
        仅当文件中对该模块的所有引用都采用此写法时才跳过；裸 import
        （如 try: import notinstalled_pkg）仍正常报出。
        """
        try:
            with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read()
        except OSError:
            return False
        try:
            tree = ast.parse(content)
        except SyntaxError:
            return False

        hits = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name == imp or alias.name.split(".")[0] == imp.split(".")[0]:
                        hits.append(alias)
        if not hits:
            return False
        return all(
            alias.asname
            and alias.asname.split(".")[0] in stdlib_modules
            for alias in hits
        )

    @staticmethod
    def _string_line_ranges(content: str) -> Optional[List[Tuple[int, int]]]:
        """用 ast 定位所有字符串字面量（含 docstring、f-string）的行区间。

        返回 None 表示无法解析（语法错误），调用方应回退为不过滤。
        """
        try:
            tree = ast.parse(content)
        except SyntaxError:
            # 语法错误的文件无法用 ast 定位字符串区间，返回 None 由调用方回退为不过滤
            return None
        ranges = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                ranges.append((node.lineno, node.end_lineno))
            elif isinstance(node, ast.JoinedStr):
                ranges.append((node.lineno, node.end_lineno))
        return ranges

    def _detect_dynamic_paths(self, project_path: str) -> List[BlindSpot]:
        """检测动态路径注入：sys.path.insert / sys.path.append"""
        spots = []
        pattern = re.compile(r'sys\.path\.(?:insert|append)\s*\(')

        for fp in self._all_py_files:
            try:
                with open(fp, "r", encoding="utf-8") as f:
                    lines = f.readlines()
            except Exception as e:
                # 文件不可读，跳过该文件的盲区检测
                logger.warning(f"读取文件失败，跳过盲区检测 {fp}: {e}")
                continue

            # 字符串/docstring 内的示例说明（如文档中的用法示例、测试的期望
            # 数据 JSON）不是真实代码行为，命中行落在字符串区间内则跳过
            str_ranges = self._string_line_ranges("".join(lines))

            for i, line in enumerate(lines, 1):
                if pattern.search(line):
                    stripped = line.strip()
                    # 注释中的示例说明不作为代码行为
                    if stripped.startswith("#"):
                        continue
                    if str_ranges and any(lo <= i <= hi for lo, hi in str_ranges):
                        continue
                    spots.append(BlindSpot(
                        category="dynamic_path",
                        item=f"sys.path 动态修改",
                        detail=f"第 {i} 行: {stripped[:100]}",
                        file_path=fp,
                        risk_level="medium",
                        user_should_know=f"代码在运行时动态修改了 Python 的模块搜索路径。这意味着有些模块的存放位置不在标准位置，可能导致 AI 分析时遗漏这些模块，或者在不同环境下运行报错。",
                    ))

        logger.info(f"[BlindSpotDetector] 动态路径注入: {len(spots)} 个")
        return spots

    def _detect_unindexed_symbols(self, project_path: str) -> List[BlindSpot]:
        """检测 GitNexus 符号索引盲区（可选）"""
        spots = []

        # 尝试导入 GitNexus 客户端
        try:
            from core.gitnexus_client import GitNexusMCPClient
            # 先检查 CLI 是否可用，不可用则直接跳过，避免启动遗留子进程造成资源泄漏
            if not GitNexusMCPClient.is_cli_available():
                logger.info("[BlindSpotDetector] GitNexus CLI 未安装，跳过符号索引盲区检测")
                return spots
            indexed_files = set()
            row_limit = 5000
            with GitNexusMCPClient(project_path) as client:
                result = client.query_cypher(
                    f"MATCH (n) RETURN DISTINCT n.filePath LIMIT {row_limit}"
                )
                rows = list(client.parse_markdown_table(result, ["filePath"]))
                if len(rows) >= row_limit:
                    logger.info("[BlindSpotDetector] GitNexus 索引结果达到查询上限，"
                                "结果不完整，跳过符号索引盲区检测")
                    return spots
                for row in rows:
                    fp = row.get("filePath", "")
                    if fp:
                        # 归一化为 / 分隔，统一 Windows \ 与 POSIX / 的差异
                        indexed_files.add(fp.replace("\\", "/"))
            if not indexed_files:
                logger.info("[BlindSpotDetector] GitNexus 索引为空，跳过符号索引盲区检测")
                return spots
        except Exception as e:
            logger.info(f"[BlindSpotDetector] GitNexus 不可用，跳过符号索引盲区: {e}")
            return spots

        # 检查哪些文件有符号定义但未被索引
        for fp in self._all_py_files:
            rel = os.path.relpath(fp, project_path)
            # 简单的符号检测：查找 def 和 class 定义
            try:
                with open(fp, "r", encoding="utf-8") as f:
                    content = f.read()
            except Exception as e:
                # 文件不可读，跳过该文件的符号检测
                logger.warning(f"读取文件失败，跳过符号检测 {fp}: {e}")
                continue

            has_symbols = bool(re.search(r'^\s*(def|class)\s+\w+', content, re.MULTILINE))
            # 归一化路径为 / 分隔后比较，避免 Windows \ 与 POSIX / 不匹配
            rel_norm = rel.replace("\\", "/")
            fp_norm = fp.replace("\\", "/")
            if has_symbols and rel_norm not in indexed_files and fp_norm not in indexed_files:
                # 计数符号
                symbol_count = len(re.findall(r'^\s*(def|class)\s+\w+', content, re.MULTILINE))
                spots.append(BlindSpot(
                    category="unindexed_symbol",
                    item=rel,
                    detail=f"文件包含 {symbol_count} 个函数/类定义，但未被 GitNexus 索引覆盖",
                    file_path=fp,
                    risk_level="medium",
                    user_should_know=f"'{rel}' 文件中有 {symbol_count} 个函数或类，但代码索引工具没有收录它们。这意味着 AI 搜索代码时可能找不到这些定义，给出的分析可能不完整。",
                ))

        logger.info(f"[BlindSpotDetector] 符号索引盲区: {len(spots)} 个")
        return spots

    def _detect_empty_files(self, project_path: str) -> List[BlindSpot]:
        """检测空文件：只有 import 没有实际代码"""
        spots = []

        for fp in self._all_py_files:
            try:
                with open(fp, "r", encoding="utf-8") as f:
                    lines = f.readlines()
            except Exception as e:
                # 文件不可读，跳过该文件的检查
                logger.warning(f"读取文件失败，跳过检查 {fp}: {e}")
                continue

            if not lines:
                continue

            # 去除空行、注释、docstring、import 行
            meaningful_lines = []
            in_docstring = False
            for line in lines:
                stripped = line.strip()
                if not stripped:
                    continue
                if stripped.startswith("#"):
                    continue
                if stripped.startswith("import ") or stripped.startswith("from "):
                    continue
                if stripped in ("__all__",):
                    continue
                # 处理 docstring
                if stripped.startswith('"""') or stripped.startswith("'''"):
                    if in_docstring:
                        in_docstring = False
                        continue
                    elif stripped.endswith('"""') or stripped.endswith("'''"):
                        continue
                    else:
                        in_docstring = True
                        continue
                if in_docstring:
                    continue
                meaningful_lines.append(stripped)

            if not meaningful_lines:
                # 跳过 __init__.py —— 它们是正常的 Python 包标记文件
                fname = os.path.basename(fp)
                if fname == "__init__.py":
                    continue
                spots.append(BlindSpot(
                    category="empty_file",
                    item=os.path.relpath(fp, project_path),
                    detail=f"文件只有 import 和注释，没有实际代码逻辑（{len(lines)} 行）",
                    file_path=fp,
                    risk_level="low",
                    user_should_know=f"文件只包含 import 导入语句，没有实际的功能代码。这可能是一个空的占位文件，或者代码被删除了但文件忘了删除。你可以忽略它，但如果它被其他文件引用，可能会出问题。",
                ))

        logger.info(f"[BlindSpotDetector] 空文件: {len(spots)} 个")
        return spots

    # ─── 辅助方法 ─────────────────────────────────────────────────

    @staticmethod
    def _get_stdlib_modules() -> Set[str]:
        """获取标准库模块名集合

        优先用 sys.stdlib_module_names（Python 3.10+，与解释器实际版本一致，
        天然包含 tomllib 等新标准库）；旧解释器回退到静态清单并补 tomllib。
        """
        dyn = getattr(sys, "stdlib_module_names", None)
        if dyn:
            return set(dyn)
        stdlib = {
            "abc", "aifc", "argparse", "array", "ast", "asynchat", "asyncio",
            "asyncore", "atexit", "audioop", "base64", "bdb", "binascii", "binhex",
            "bisect", "builtins", "bz2", "calendar", "cgi", "cgitb", "chunk",
            "cmath", "cmd", "code", "codecs", "codeop", "collections", "colorsys",
            "compileall", "concurrent", "configparser", "contextlib", "contextvars",
            "copy", "copyreg", "cProfile", "crypt", "csv", "ctypes", "curses",
            "dataclasses", "datetime", "dbm", "decimal", "difflib", "dis",
            "distutils", "doctest", "email", "encodings", "enum", "errno",
            "faulthandler", "fcntl", "filecmp", "fileinput", "fnmatch", "fractions",
            "ftplib", "functools", "gc", "getopt", "getpass", "gettext", "glob",
            "graphlib", "grp", "gzip", "hashlib", "heapq", "hmac", "html", "http",
            "idlelib", "imaplib", "imghdr", "imp", "importlib", "inspect", "io",
            "ipaddress", "itertools", "json", "keyword", "lib2to3", "linecache",
            "locale", "logging", "lzma", "mailbox", "mailcap", "marshal", "math",
            "mimetypes", "mmap", "modulefinder", "multiprocessing", "netrc", "nis",
            "nntplib", "numbers", "operator", "optparse", "os", "ossaudiodev",
            "pathlib", "pdb", "pickle", "pickletools", "pipes", "pkgutil",
            "platform", "plistlib", "poplib", "posix", "posixpath", "pprint",
            "profile", "pstats", "pty", "pwd", "py_compile", "pyclbr", "pydoc",
            "queue", "quopri", "random", "re", "readline", "reprlib", "resource",
            "rlcompleter", "runpy", "sched", "secrets", "select", "selectors",
            "shelve", "shlex", "shutil", "signal", "site", "smtpd", "smtplib",
            "sndhdr", "socket", "socketserver", "sqlite3", "ssl", "stat",
            "statistics", "string", "stringprep", "struct", "subprocess", "sunau",
            "symtable", "sys", "sysconfig", "syslog", "tabnanny", "tarfile",
            "telnetlib", "tempfile", "termios", "test", "textwrap", "threading",
            "time", "timeit", "tkinter", "token", "tokenize", "trace", "traceback",
            "tracemalloc", "tty", "turtle", "turtledemo", "types", "typing",
            "unicodedata", "unittest", "urllib", "uu", "uuid", "venv", "warnings",
            "wave", "weakref", "webbrowser", "winreg", "winsound", "wsgiref",
            "xdrlib", "xml", "xmlrpc", "zipapp", "zipfile", "zipimport", "zlib",
            "zoneinfo", "_thread",
        }
        stdlib.add("tomllib")  # Python 3.11+ 标准库，旧静态清单缺失
        return stdlib

    # ─── 报告生成 ─────────────────────────────────────────────────

    def _generate_report(self, project_path: str, spots: List[BlindSpot]) -> str:
        """生成 Markdown 格式的盲区检测报告"""
        # 分类统计
        cat_counts: Dict[str, int] = defaultdict(int)
        risk_counts: Dict[str, int] = defaultdict(int)
        for s in spots:
            cat_counts[s.category] += 1
            risk_counts[s.risk_level] += 1

        lines = []
        lines.append("# 盲区检测报告")
        lines.append("")
        lines.append(f"> 项目路径: `{project_path}`")
        lines.append(f"> 扫描文件数: {len(self._all_py_files)}")
        lines.append(f"> 发现盲区: {len(spots)} 个")
        lines.append("")
        lines.append("> 盲区 = 用户不知道 + AI 不知道 + 代码中缺失的信息。")
        lines.append('> 本报告帮助你了解项目的\u201c信息黑洞\u201d，减少意外。')
        lines.append("")
        lines.append("---")
        lines.append("")

        # 盲区总览
        lines.append("## 盲区总览")
        lines.append("")
        lines.append("### 按分类统计")
        lines.append("")
        lines.append("| 分类 | 数量 | 说明 |")
        lines.append("|------|------|------|")
        for cat, label in CATEGORY_LABELS.items():
            count = cat_counts.get(cat, 0)
            if count > 0:
                desc = {
                    "doc_blindspot": "有代码但缺少文档",
                    "missing_dependency": "引用了不存在的模块",
                    "dynamic_path": "运行时修改了模块搜索路径",
                    "unindexed_symbol": "代码未被索引工具收录",
                    "empty_file": "只有 import 没有实际代码",
                }.get(cat, "")
                lines.append(f"| {label} | {count} | {desc} |")
        lines.append("")

        lines.append("### 按风险等级统计")
        lines.append("")
        lines.append("| 风险等级 | 数量 |")
        lines.append("|---------|------|")
        for risk in ["high", "medium", "low"]:
            count = risk_counts.get(risk, 0)
            if count > 0:
                label = {"high": "高", "medium": "中", "low": "低"}[risk]
                lines.append(f"| {label} | {count} |")
        lines.append("")

        if not spots:
            lines.append("本次扫描未发现盲区，项目信息结构良好。")
            return "\n".join(lines)

        lines.append("---")
        lines.append("")

        # 按分类展示详情
        for cat, label in CATEGORY_LABELS.items():
            cat_spots = [s for s in spots if s.category == cat]
            if not cat_spots:
                continue

            lines.append(f"## {label}（{len(cat_spots)} 个）")
            lines.append("")

            for s in cat_spots:
                risk_icon = {"high": "!!", "medium": "~", "low": "-"}[s.risk_level]
                lines.append(f"### [{risk_icon}] {s.item}")
                lines.append("")
                lines.append(f"- **文件**: `{s.file_path}`")
                lines.append(f"- **风险等级**: {s.risk_level}")
                lines.append(f"- **详情**: {s.detail}")
                lines.append(f"- **用户须知**: {s.user_should_know}")
                lines.append("")

        lines.append("---")
        lines.append("")

        # 建议
        lines.append("## 建议")
        lines.append("")
        lines.append("1. **文档盲区**：请开发者补充对应模块的 README 或 API 文档")
        lines.append("2. **缺失依赖**：检查 `requirements.txt` 或 `pyproject.toml` 是否完整")
        lines.append("3. **动态路径注入**：评估是否可以将模块移到标准位置")
        lines.append("4. **符号索引盲区**：确保 GitNexus 索引覆盖所有代码文件")
        lines.append("5. **空文件**：确认是否需要保留，不需要的可以删除")
        lines.append("")
        lines.append("---")
        lines.append("*报告由 CodeRef-AI BlindSpotDetector v1.0 生成*")

        return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════════
# 独立运行入口
# ═══════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import sys

    target = sys.argv[1] if len(sys.argv) > 1 else os.getcwd()
    detector = BlindSpotDetector()
    report = detector.detect(target)
    print(report)
