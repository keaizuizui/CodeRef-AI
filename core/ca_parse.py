# -*- coding: utf-8 -*-
"""
code_analyzer 拆解子模块：AST 解析与结构提取
原 core/code_analyzer.py 84~262 行（模块级辅助函数，从 CodeAnalyzer 类提取的纯逻辑）。
单向依赖：models/parse ← render/audit/gitnexus ← 主文件。
"""

import os
import re

from loguru import logger
from core.code_models import CodeFile, CodeFunction, CodeClass


def _parse_python_file_content(content: str, file_path: str, project_root: str = "") -> CodeFile:
    """解析 Python 文件内容（原 CodeAnalyzer.parse_python_file 实现）
    拆分说明：AST 解析、导入/调用提取、函数/类填充提取为独立步骤函数，本函数仅编排。"""
    code_file = CodeFile(file_path=file_path, language='python', raw_content=content)

    # 计算当前文件所在模块（相对项目根的目录）
    if project_root:
        rel_dir = os.path.dirname(os.path.relpath(file_path, project_root))
    else:
        rel_dir = ""

    # ─── 优先使用 AST 精确解析 ──────────────────────────────────
    ast_result, ast_assignments = _ast_parse_content(content, file_path, project_root)
    # 存储 AST 赋值分类（供 _audit_security 使用）
    code_file.ast_assignments = ast_assignments

    _extract_basic_imports(content, code_file, rel_dir)
    _extract_full_import_calls(content, code_file, rel_dir)
    _extract_dynamic_and_http(content, code_file)

    # 提取函数/类：优先使用 AST 精确结果（node.end_lineno 精确到函数体最后一行，
    # 类方法来自 ClassDef.body），AST 失败（如语法错误）时回退正则推断。
    # 旧正则版缺陷：最后一个函数的 end_line 直接取文件总行数、中间函数取下一个
    # def/class 定义前一行，函数后的模块级常量区被算进函数体，行数虚高；
    # 且 CodeClass.methods 从未填充，方法计数恒为 0。
    _fill_functions_and_classes(code_file, ast_result, content)

    return code_file


def _ast_parse_content(content: str, file_path: str, project_root: str):
    """AST 精确解析（失败时返回 (None, []) 供调用方回退正则）"""
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
    return ast_result, ast_assignments


def _extract_basic_imports(content: str, code_file: CodeFile, rel_dir: str):
    """提取导入语句，并区分项目内部导入 vs 外部依赖"""
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


def _extract_full_import_calls(content: str, code_file: CodeFile, rel_dir: str):
    """提取完整的from ... import ... 语句（用于跨模块分析）"""
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


def _extract_dynamic_and_http(content: str, code_file: CodeFile):
    """提取 sys.path 注入、动态导入、本地 HTTP 调用"""
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


def _fill_functions_and_classes(code_file: CodeFile, ast_result, content: str):
    """填充函数/类结构：AST 可用时走精确路径，否则回退正则近似解析"""
    if ast_result is not None:
        _fill_funcs_classes_from_ast(code_file, ast_result)
    else:
        _fill_funcs_classes_from_regex(code_file, content)


def _fill_funcs_classes_from_ast(code_file: CodeFile, ast_result):
    """从 AST 结果填充函数/类（all_functions = 顶层函数 + 类方法，方法也参与行数统计）。"""
    # 私有方法仍会通过下面的 classes 分支注册；只跳过"私有方法在 all_functions
    # 里的重复副本"，但**必须收录私有顶层函数**——否则 flow_verify / callers /
    # impact 会把真实存在的 `_run_capability_job` 判成 missing（外部审计 D-25）。
    method_keys = {(m.name, m.start_line)
                   for cls in ast_result.classes for m in cls.methods}
    for func in ast_result.all_functions:
        if func.name.startswith('_') and (func.name, func.start_line) in method_keys:
            continue
        if func.name.startswith('__') and func.name.endswith('__'):
            continue  # dunder 不是业务符号
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


def _fill_funcs_classes_from_regex(code_file: CodeFile, content: str):
    """正则回退：AST 不可用（语法错误文件）时的近似解析"""
    func_pattern = r'def\s+(\w+)\s*\(([^)]*)\)\s*(?:->\s*([\w\[\],\s]+))?:'
    func_matches = list(re.finditer(func_pattern, content))
    for idx, match in enumerate(func_matches):
        func_name = match.group(1)
        if not (func_name.startswith('__') and func_name.endswith('__')):
            # 私有顶层函数也要收录（与 AST 路径一致）；仅跳过 dunder
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
