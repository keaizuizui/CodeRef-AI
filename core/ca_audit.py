# -*- coding: utf-8 -*-
"""
code_analyzer 拆解子模块：缺陷审计检测
原 core/code_analyzer.py 1057~1531 行（模块级辅助函数）。
单向依赖：ca_parse ← ca_audit ← 主文件；类型注解使用字符串引用避免循环 import。
"""

import os
import re
from typing import TYPE_CHECKING, Dict, List

from core.code_models import CodeFile

if TYPE_CHECKING:
    # 仅供类型标注（pyflakes 解析注解名），运行时零依赖，避免与主文件循环 import
    from core.code_analyzer import ProjectAnalysis


def _verify_audit_line_numbers(analysis: 'ProjectAnalysis', audit_results: Dict[str, List[Dict]]) -> Dict[str, List[Dict]]:
    """验证审计结果行号有效性（原 CodeAnalyzer._verify_line_numbers 实现）
    
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


def _audit_file_bugs(cf: CodeFile, rel_path: str, content: str, lines: List[str], issues: List[Dict]):
    """Bug与错误检测（原 CodeAnalyzer._audit_bugs 实现）
    注意：content 来自 CodeFile.raw_content，缓存不截断后为完整内容
    拆分说明：logger 检测与异常处理检测提取为独立检测函数，本函数仅编排。"""
    _detect_undefined_logger(content, lines, rel_path, issues)
    _detect_missing_exception_handling(cf, lines, rel_path, issues)


def _logger_is_imported(content: str) -> bool:
    """判断 content 是否以任一形式导入/定义了 logger（供 logger 未定义检测使用）"""
    has_logging_import = bool(re.search(r'import\s+logging', content))
    has_logger_def = bool(re.search(r'\blogger\s*=\s*(?:logging\.getLogger|getLogger)', content))

    # 跨模块导入追踪：from X import ... logger ...
    # 单行匹配：from config import logger / from config import x, logger, y
    has_logger_import_single = bool(re.search(r'\bfrom\s+\S+\s+import\s+[^)]*\blogger\b', content))

    # 多行匹配：from config import ( \n    x, \n    logger, \n )
    has_logger_import_multi = False
    multi_import_matches = list(re.finditer(r'\bfrom\s+\S+\s+import\s*\(', content))
    for m in multi_import_matches:
        if _multiline_import_contains_logger(content, m):
            has_logger_import_multi = True
            break

    # import logger 别名：from loguru import logger as log
    has_logger_alias = bool(re.search(r'\bfrom\s+\S+\s+import\s+.*\blogger\s+as\s+\w+', content))

    return (has_logging_import or has_logger_def or
            has_logger_import_single or has_logger_import_multi or has_logger_alias)


def _multiline_import_contains_logger(content: str, m) -> bool:
    """检查多行 import(...) 块内是否导入 logger（从匹配到的 import ( 开始配对括号）"""
    depth = 1
    pos = m.end()
    while pos < len(content) and depth > 0:
        if content[pos] == '(':
            depth += 1
        elif content[pos] == ')':
            depth -= 1
        pos += 1
    if depth == 0:
        import_block = content[m.end():pos-1]
        return bool(re.search(r'\blogger\b', import_block))
    return False


def _detect_undefined_logger(content: str, lines: List[str], rel_path: str, issues: List[Dict]):
    """logger未定义检测（精准：排除跨模块导入 logger 的情况）"""
    if not re.search(r'\blogger\.(debug|info|warning|error|critical)\b', content):
        return
    if _logger_is_imported(content):
        return
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


def _find_scope_for_line(cf: CodeFile, i: int):
    """返回包含行 i 的 (scope_start, scope_end)，依次查函数→类方法→类体；无则 None"""
    # 优先检查函数作用域（包括类方法）
    for func in cf.functions:
        if func.start_line <= i <= func.end_line:
            return func.start_line, func.end_line

    # 如果不在独立函数内，检查类方法
    for cls in cf.classes:
        for method in cls.methods:
            if method.start_line <= i <= method.end_line:
                return method.start_line, method.end_line

    # 如果不在方法内但位于类体中，检查整个类
    for cls in cf.classes:
        if cls.start_line <= i <= cls.end_line:
            return cls.start_line, cls.end_line
    return None


def _line_covered_by_try(lines: List[str], i: int, scope) -> bool:
    """判断行 i 所在作用域（或模块级邻近5行）是否被 try 覆盖"""
    if scope is not None:
        scope_start, scope_end = scope
        # 在函数/类作用域内：检查整个作用域是否有 try
        for j in range(scope_start, min(scope_end + 1, len(lines) + 1)):
            check_line = lines[j - 1].strip() if j <= len(lines) else ''
            if check_line.startswith('try:') or check_line == 'try:':
                return True
        return False
    # 模块级代码：只检查周围5行
    for j in range(i - 1, max(i - 5, 0), -1):
        check_line = lines[j - 1].strip() if j > 0 else ''
        if check_line.startswith('try:') or check_line == 'try:':
            return True
    return False


def _detect_missing_exception_handling(cf: CodeFile, lines: List[str], rel_path: str, issues: List[Dict]):
    """异常处理缺失检测（函数级try块检测，使用函数/类作用域而非行级回溯）"""
    if cf.language != 'python':
        return
    risky_calls = ['requests.', 'urllib', 'socket.', 'subprocess.', 'open(', 'httpx.']
    for risky in risky_calls:
        for i, line in enumerate(lines, 1):
            if risky in line:
                if _line_covered_by_try(lines, i, _find_scope_for_line(cf, i)):
                    continue
                issues.append({
                    'severity': 'medium',
                    'file': rel_path,
                    'line': i,
                    'description': f'调用 `{risky}` 可能缺少异常处理',
                    'suggestion': '添加 try/except 块处理IO/网络异常'
                })
                break


def _audit_file_security(cf: CodeFile, rel_path: str, content: str, lines: List[str], issues: List[Dict]):
    """安全问题检测（原 CodeAnalyzer._audit_security 实现，AST 精确分类 + 正则回退）
    拆分说明：按风险类别提取为独立检测函数，本函数仅编排。"""
    _detect_hardcoded_secrets(cf, rel_path, content, lines, issues)
    _detect_sql_injection(rel_path, lines, issues)
    _detect_path_traversal(rel_path, content, lines, issues)
    _detect_unsafe_deserialization(rel_path, lines, issues)
    _detect_eval_exec(rel_path, lines, issues)
    _detect_hardcoded_win_paths(rel_path, lines, issues)


def _detect_hardcoded_secrets(cf: CodeFile, rel_path: str, content: str, lines: List[str], issues: List[Dict]):
    """硬编码密钥/Token 检测 — 优先使用 AST 精确分类，AST 不可用回退正则"""
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
        return
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


def _detect_sql_injection(rel_path: str, lines: List[str], issues: List[Dict]):
    """SQL注入风险检测（字符串拼接SQL）"""
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


def _detect_path_traversal(rel_path: str, content: str, lines: List[str], issues: List[Dict]):
    """路径遍历风险检测（open() 拼接用户输入）"""
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


def _detect_unsafe_deserialization(rel_path: str, lines: List[str], issues: List[Dict]):
    """不安全的反序列化检测（pickle.loads / yaml.load）"""
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


def _detect_eval_exec(rel_path: str, lines: List[str], issues: List[Dict]):
    """eval/exec 动态代码执行检测"""
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


def _detect_hardcoded_win_paths(rel_path: str, lines: List[str], issues: List[Dict]):
    """硬编码Windows路径检测（带过滤：跳过测试文件、注释、模板、UI文本等）"""
    # 跳过测试文件和 __pycache__ 目录
    if '__pycache__' in rel_path or '/test_' in rel_path.replace('\\', '/') or rel_path.startswith('test_'):
        return
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


def _audit_file_quality(cf: CodeFile, rel_path: str, content: str, lines: List[str], issues: List[Dict]):
    """代码质量检测（原 CodeAnalyzer._audit_quality 实现）"""
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


def _audit_file_performance(cf: CodeFile, rel_path: str, content: str, lines: List[str], issues: List[Dict]):
    """性能风险检测（原 CodeAnalyzer._audit_performance 实现）"""
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


def _audit_file_design(cf: CodeFile, rel_path: str, content: str, lines: List[str], issues: List[Dict]):
    """设计问题检测（原 CodeAnalyzer._audit_design 实现）"""
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
