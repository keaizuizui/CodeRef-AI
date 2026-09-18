# -*- coding: utf-8 -*-
"""
code_analyzer 拆解子模块：汇总分析与报告渲染（含 mermaid）
原 core/code_analyzer.py 263~903 + 1019~1056 行（模块级辅助函数）。
单向依赖：ca_parse ← ca_render ← 主文件；类型注解使用字符串引用避免循环 import。
"""

import os
from datetime import datetime
from typing import TYPE_CHECKING, Dict, List, Optional

from loguru import logger

if TYPE_CHECKING:
    # 仅供类型标注（pyflakes 解析注解名），运行时零依赖，避免与主文件循环 import
    from core.code_analyzer import ProjectAnalysis


def _tech_stack_summary(analysis: 'ProjectAnalysis') -> List[str]:
    """分析技术栈（原 CodeAnalyzer._analyze_tech_stack 实现）"""
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


def _core_features_from(analysis: 'ProjectAnalysis') -> List[str]:
    """提取核心功能（原 CodeAnalyzer._extract_core_features 实现）"""
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


def _architecture_summary_text(analysis: 'ProjectAnalysis') -> str:
    """生成架构摘要（原 CodeAnalyzer._generate_architecture_summary 实现）"""
    summary_parts = []
    
    # 基本信息
    summary_parts.append("## 项目概览")
    summary_parts.append(f"- 总文件数: {analysis.total_files}")
    summary_parts.append(f"- 总代码行数: {analysis.total_lines:,}")
    lang_str = ', '.join([f'{k}({v}个文件)' for k, v in sorted(analysis.languages.items(), key=lambda x: -x[1])])
    summary_parts.append(f"- 语言分布: {lang_str}")
    summary_parts.append(f"- 总模块数: {len(analysis.modules)}")
    summary_parts.append(f"- 总类数: {sum(len(f.classes) for f in analysis.files)}")
    summary_parts.append(f"- 总函数数: {sum(len(f.functions) for f in analysis.files)}")
    
    # 技术栈
    if analysis.tech_stack:
        summary_parts.append("\n## 技术栈")
        for tech in analysis.tech_stack:
            summary_parts.append(f"- {tech}")
    
    # 核心功能
    if analysis.core_features:
        summary_parts.append("\n## 核心功能")
        for feature in analysis.core_features:
            summary_parts.append(f"- {feature}")
    
    # 模块结构
    summary_parts.append("\n## 模块结构")
    for module, files in sorted(analysis.modules.items()):
        summary_parts.append(f"- **{module}**: {len(files)} 个文件")
    
    # 依赖概览
    if analysis.dependencies:
        summary_parts.append("\n## 外部依赖")
        for dep in sorted(analysis.dependencies)[:20]:
            summary_parts.append(f"- {dep}")
        if len(analysis.dependencies) > 20:
            summary_parts.append(f"- ... 及其他 {len(analysis.dependencies) - 20} 个依赖")
    
    return '\n'.join(summary_parts)


def _mode_metadata_from(analysis: 'ProjectAnalysis', max_parse_size: int) -> list:
    """
    从代码中提取工具模式元数据（原 CodeAnalyzer._extract_mode_metadata 实现）
    V2.1: 从 MODE_METADATA 字典中提取，不再硬编码 fallback
    """
    modes = []
    
    # 扫描所有文件，查找 MODE_METADATA 字典定义（从磁盘读取完整文件）
    for f in analysis.files:
        if 'MODE_METADATA' not in (getattr(f, 'raw_content', '') or ''):
            # raw_content 只有500字符，可能不包含 MODE_METADATA
            # 直接从磁盘读取（超大文件跳过，避免整读大文件占用内存）
            try:
                if os.path.getsize(f.file_path) > max_parse_size:
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


def _model_roles_from(analysis: 'ProjectAnalysis') -> list:
    """
    从代码中提取 LLM 模型角色配置（原 CodeAnalyzer._extract_model_roles 实现）
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
            logger.warning(f"解析模型角色配置失败，跳过该文件 {config_path}: {e}")
    
    if not roles:
        roles = [['(未找到配置)', '-', '-', '-', f'未在 {analysis.project_path} 中找到 config.yaml']]
    return roles


def _render_rich_header(analysis: 'ProjectAnalysis', totals: Dict[str, int]) -> List[str]:
    """渲染深度报告头部 + 项目概览 + 语言分布（generate_rich_report 拆分步骤）"""
    report = []
    
    # ==================== 头部 ====================
    report.append("# 📊 项目深度分析报告")
    report.append("\n> 生成时间: 即时分析")
    report.append(f"> 项目路径: `{analysis.project_path}`")
    report.append("")
    report.append("---")
    
    # ==================== 1. 项目概览 ====================
    report.append("## 一、项目概览")
    
    total_files = totals["files"]
    total_lines = totals["lines"]
    total_classes = totals["classes"]
    total_functions = totals["functions"]
    total_imports = totals["imports"]
    
    report.append("| 指标 | 数值 |")
    report.append("|------|------|")
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
    
    return report


def _render_rich_module_file_entry(analysis: 'ProjectAnalysis', file_path: str) -> Optional[str]:
    """渲染模块内单个文件的详情行；无法定位 CodeFile 时返回 None。

    拆分说明：原 _render_rich_modules 内层文件详情渲染段提取为纯函数，
    输出行内容与拆分前逐字符一致（details 为空时同样返回带尾随空格的行）。
    """
    full_path = os.path.join(analysis.project_path, file_path)
    code_file = next((f for f in analysis.files if f.file_path == full_path), None)
    if not code_file:
        return None
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
    return f"  - `{os.path.basename(file_path)}` {detail_str}"


def _render_rich_modules(analysis: 'ProjectAnalysis') -> List[str]:
    """渲染模块/目录结构段（generate_rich_report 拆分步骤）"""
    report = []

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
            entry = _render_rich_module_file_entry(analysis, file_path)
            if entry:
                report.append(entry)

        if len(files) > 8:
            report.append(f"  - ... 及其他 {len(files) - 8} 个文件")

    return report


def _render_rich_entities(analysis: 'ProjectAnalysis') -> List[str]:
    """渲染关键代码实体段（generate_rich_report 拆分步骤）"""
    report = []
    
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
    
    return report


def _render_rich_deps(analysis: 'ProjectAnalysis') -> List[str]:
    """渲染依赖关系分析段（generate_rich_report 拆分步骤）"""
    report = []
    
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
    
    return report


def _render_rich_metrics(analysis: 'ProjectAnalysis', totals: Dict[str, int]) -> List[str]:
    """渲染代码度量段（generate_rich_report 拆分步骤）"""
    report = []
    
    total_files = totals["files"]
    total_lines = totals["lines"]
    total_classes = totals["classes"]
    total_functions = totals["functions"]
    total_imports = totals["imports"]
    
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
    
    return report


def _render_rich_tail(analysis: 'ProjectAnalysis') -> List[str]:
    """渲染技术栈/核心功能/入口点与报告尾部（generate_rich_report 拆分步骤）"""
    report = []
    
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
    
    return report


def _render_ai_header(analysis: 'ProjectAnalysis') -> List[str]:
    """渲染 AI 审计报告头部（generate_ai_report 拆分步骤）"""
    lines = []
    
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
    return lines


def _render_ai_summary(audit_results: Dict[str, List[Dict]]) -> List[str]:
    """渲染审计摘要段（generate_ai_report 拆分步骤）"""
    lines = []
    
    lines.append("## 一、审计摘要")
    lines.append("")
    
    total_issues = sum(len(v) for v in audit_results.values())
    critical = sum(1 for cat in audit_results.values() for item in cat if item.get('severity') == 'critical')
    high = sum(1 for cat in audit_results.values() for item in cat if item.get('severity') == 'high')
    medium = sum(1 for cat in audit_results.values() for item in cat if item.get('severity') == 'medium')
    low = sum(1 for cat in audit_results.values() for item in cat if item.get('severity') == 'low')
    
    lines.append("| 维度 | 问题数 | 严重 | 高 | 中 | 低 |")
    lines.append("|------|--------|------|----|----|----|")
    for category, items in audit_results.items():
        c = sum(1 for i in items if i.get('severity') == 'critical')
        h = sum(1 for i in items if i.get('severity') == 'high')
        m = sum(1 for i in items if i.get('severity') == 'medium')
        l = sum(1 for i in items if i.get('severity') == 'low')
        lines.append(f"| {category} | {len(items)} | {c} | {h} | {m} | {l} |")
    lines.append(f"| **总计** | **{total_issues}** | **{critical}** | **{high}** | **{medium}** | **{low}** |")
    lines.append("")
    return lines


def _render_ai_categories(audit_results: Dict[str, List[Dict]]) -> List[str]:
    """渲染各审计维度详情段（generate_ai_report 拆分步骤）"""
    lines = []
    
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
    return lines


def _render_ai_priorities(audit_results: Dict[str, List[Dict]]) -> List[str]:
    """渲染修复优先级建议与报告尾部（generate_ai_report 拆分步骤）"""
    lines = []
    
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
    return lines


def _mermaid_from_analysis(analysis: 'ProjectAnalysis') -> str:
    """基于分析结果生成Mermaid架构图（原 CodeAnalyzer.generate_mermaid_diagram 实现）
    
    利用 auto_classifier 自动分层，生成带子图的 Mermaid flowchart
    """
    from .diagram_generator import generate_mermaid
    
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
