"""
Agent 安全审计器 —— 专为 AI Agent 系统设计的风险检测

检测维度（基于 OWASP Top 10 for LLM Applications + Agent 安全实践）：

1. 提示注入风险 (Prompt Injection)
   - 检测用户输入直接拼接到 prompt 中
   - 检测未做输入过滤的 f-string/format 拼接

2. 上下文操纵风险 (Context Manipulation)
   - 检测外部文档/URL 内容直接注入到上下文
   - 检测未分类/未过滤的外部数据源

3. 工具滥用风险 (Tool Misuse)
   - 检测 Agent 可调用的危险函数（文件删除、命令执行、数据库写入）
   - 检测缺失权限检查的工具调用

4. 预算/资源耗尽风险 (Budget Exhaustion)
   - 检测无限制的 LLM 调用循环
   - 检测缺失 token 预算控制的流程

5. 数据泄露风险 (Data Exfiltration)
   - 检测敏感数据通过 LLM 输出到外部 API
   - 检测日志中记录了完整 prompt 内容

5.5 PII 泄露风险 (PII Leak)
   - 检测日志中打印邮箱、手机号、身份证号等个人身份信息
   - 检测 PII 明文拼接到 f-string 中

5.6 安全配置风险 (Security Config)
   - 检测 DEBUG=True 的生产环境配置
   - 检测不安全反序列化（pickle/yaml.load）
   - 检测 CORS 配置过于宽松
   - 检测网络请求缺少超时设置

6. 自主行为风险 (Autonomous Action)
   - 检测 Agent 未经人类确认即可执行危险操作
   - 检测缺失 human-in-the-loop 的关键路径

7. 知识投毒风险 (Knowledge Poisoning)
   - 检测 RAG 检索结果未做可信度校验
   - 检测向量数据库写入未做权限控制

作者: CodeRef Team
版本: v1.0
"""

import ast
import hashlib
import logging
import os
import re
from typing import List, Set
from dataclasses import dataclass
from collections import defaultdict

# ==================== 上帝模块拆解（第 3 批）说明 ====================
# 常量/模式规则区 → core/asa_patterns.py（纯数据：AgentSecurityRisk、
# SEVERITY_ORDER、各 *_PATTERNS / *_RE / CROSSLANG_GROUPS / CATEGORY_* 等）；
# 模块级扫描/精炼/报告渲染函数 → core/asa_scan.py（_scan_* / _refine_* /
# _collect_* / _lookup_* / _crosslang_* / _render_* / to_report）。
# 本文件保留 AgentSecurityAuditor 类 + audit_agent_security 入口，并对两个
# 子模块符号做全量 re-export（import 兼容层，`from core.agent_security_auditor
# import X` 符号面不缺失）。
# 依赖拓扑（单向无环）：asa_patterns ← asa_scan ← 本文件。

from core.asa_patterns import (
    AgentSecurityRisk,
    SEVERITY_ORDER,
    _SEC53_WRITE_RE,
    _LLM_CONTEXT_RE,
    _USER_AGENT_RE,
    _AGENT_LLM_ONLY_RULES,
    PROMPT_INJECTION_PATTERNS,
    CONTEXT_MANIPULATION_PATTERNS,
    TOOL_MISUSE_PATTERNS,
    BUDGET_EXHAUSTION_PATTERNS,
    DATA_EXFIL_PATTERNS,
    PII_LEAK_PATTERNS,
    SECURITY_CONFIG_PATTERNS,
    GO_PATTERNS,
    NODE_PATTERNS,
    PHP_PATTERNS,
    JAVA_PATTERNS,
    CROSSLANG_GROUPS,
    AUTONOMOUS_ACTION_PATTERNS,
    SSRF_PATTERNS,
    PATH_TRAVERSAL_PATTERNS,
    DESERIALIZATION_PATTERNS,
    BROWSER_SANDBOX_PATTERNS,
    INFO_LEAK_PATTERNS,
    LLM_EXEC_PATTERNS,
    AUTH_MISSING_PATTERNS,
    SECRET_WRITE_PATTERNS,
    RESOURCE_LEAK_PATTERNS,
    EXCLUDE_PATTERNS,
    EXCLUDE_DIRS,
    RESILIENCE_GAP_CHECKS,
    SELF_EXEMPT_FILES,
    _SEC08_READONLY_SQL_HEADS,
    _SEC06_SELF_MANAGED_RE,
    _SEC08_SELF_DB_RE,
    _SEC08_WRITE_LITERAL_RE,
    _SEC08_TMP_HANDLE_RE,
    _CLOSURE_STOPWORDS,
    _PT_STATIC_WORDS,
    _PT_UNSAFE_RE,
    _SSRF_LOCAL_RE,
    _SECRET_WRITE_RE,
    _CROSSLANG_FILE_LEVEL_RULES,
    CONFIG_CONTAINER_HINTS,
    CATEGORY_NAMES,
    CATEGORY_DETAILS,
    CATEGORY_ORDER,
)

from core.asa_scan import (
    _SEC08_DB_CACHE,
    _has_llm_context,
    _is_excluded_dir,
    _collect_target_files,
    _collect_docstring_lines,
    _collect_noise_lines,
    _scan_file,
    _lookup_var_rhs,
    _refine_sec08,
    _extract_string_literals,
    _sec08_write_target,
    _sec08_self_managed_db,
    _symbol_closure_text,
    _symbol_closure_segments,
    _extract_sql_literal,
    _refine_sec06,
    _lookup_module_const_rhs,
    _lookup_attr_rhs,
    _pt_token_is_static,
    _refine_secpt,
    _refine_ssrf,
    _refine_secret,
    _scan_crosslang_file,
    _crosslang_file_signal_lines,
    _crosslang_ssrf_signal_lines,
    _scan_path_traversal_ast,
    _expr_references_params,
    _scan_param_shadow,
    _scan_function_param_shadow,
    _match_param_shadow,
    _iter_body_assigns,
    _contains_name,
    _CONFIG_READ_METHODS,
    _STRONG_CONFIG_READ_METHODS,
    _key_matches_param,
    _match_config_shadow,
    _container_leaf_name,
    _is_config_container,
    _check_resilience_gaps,
    _collect_call_args,
    _is_pattern_definition,
    _report_severity_stats,
    _render_report_header,
    _render_category_summary,
    _render_risk_details,
    _render_report_footer,
    to_report,
)

logger = logging.getLogger(__name__)

# 兼容层显式导出清单：列出本模块命名空间中全部对外可见符号（含 re-export 的
# 常量/函数与保留的 import 名），保证拆解前后 `dir()` 符号面不缺失；同时作为
# pyflakes 对 re-export import 不报未使用的显式声明。
__all__ = [
    # 标准库/类型导入名（符号面兼容，保持原模块命名空间可见）
    "ast", "hashlib", "logging", "os", "re", "List", "Set",
    "dataclass", "defaultdict", "logger",
    # 常量与数据模型（自 core.asa_patterns re-export）
    "AgentSecurityRisk", "SEVERITY_ORDER",
    "PROMPT_INJECTION_PATTERNS", "CONTEXT_MANIPULATION_PATTERNS",
    "TOOL_MISUSE_PATTERNS", "BUDGET_EXHAUSTION_PATTERNS",
    "DATA_EXFIL_PATTERNS", "PII_LEAK_PATTERNS", "SECURITY_CONFIG_PATTERNS",
    "GO_PATTERNS", "NODE_PATTERNS", "PHP_PATTERNS", "JAVA_PATTERNS",
    "CROSSLANG_GROUPS", "AUTONOMOUS_ACTION_PATTERNS", "SSRF_PATTERNS",
    "PATH_TRAVERSAL_PATTERNS", "DESERIALIZATION_PATTERNS",
    "BROWSER_SANDBOX_PATTERNS", "INFO_LEAK_PATTERNS", "LLM_EXEC_PATTERNS",
    "AUTH_MISSING_PATTERNS", "SECRET_WRITE_PATTERNS", "RESOURCE_LEAK_PATTERNS",
    "EXCLUDE_PATTERNS", "EXCLUDE_DIRS", "RESILIENCE_GAP_CHECKS",
    "SELF_EXEMPT_FILES", "CONFIG_CONTAINER_HINTS",
    "CATEGORY_NAMES", "CATEGORY_DETAILS", "CATEGORY_ORDER",
    "_SEC53_WRITE_RE", "_LLM_CONTEXT_RE", "_USER_AGENT_RE",
    "_AGENT_LLM_ONLY_RULES",
    "_SEC08_READONLY_SQL_HEADS", "_SEC06_SELF_MANAGED_RE", "_SEC08_SELF_DB_RE",
    "_SEC08_WRITE_LITERAL_RE", "_SEC08_TMP_HANDLE_RE", "_CLOSURE_STOPWORDS",
    "_PT_STATIC_WORDS", "_PT_UNSAFE_RE", "_SSRF_LOCAL_RE", "_SECRET_WRITE_RE",
    "_CROSSLANG_FILE_LEVEL_RULES",
    # 扫描/精炼/报告渲染函数（自 core.asa_scan re-export）
    "_SEC08_DB_CACHE", "_has_llm_context", "_is_excluded_dir",
    "_collect_target_files", "_collect_docstring_lines", "_collect_noise_lines",
    "_scan_file", "_lookup_var_rhs", "_refine_sec08", "_extract_string_literals",
    "_sec08_write_target", "_sec08_self_managed_db", "_symbol_closure_text",
    "_symbol_closure_segments", "_extract_sql_literal", "_refine_sec06",
    "_lookup_module_const_rhs", "_lookup_attr_rhs", "_pt_token_is_static",
    "_refine_secpt", "_refine_ssrf", "_refine_secret", "_scan_crosslang_file",
    "_crosslang_file_signal_lines", "_crosslang_ssrf_signal_lines",
    "_scan_path_traversal_ast", "_expr_references_params", "_scan_param_shadow",
    "_scan_function_param_shadow", "_match_param_shadow", "_iter_body_assigns",
    "_contains_name", "_CONFIG_READ_METHODS", "_STRONG_CONFIG_READ_METHODS",
    "_key_matches_param", "_match_config_shadow", "_container_leaf_name",
    "_is_config_container", "_check_resilience_gaps", "_collect_call_args",
    "_is_pattern_definition",
    "_report_severity_stats", "_render_report_header", "_render_category_summary",
    "_render_risk_details", "_render_report_footer", "to_report",
    # 类与入口
    "AgentSecurityAuditor", "audit_agent_security",
]


class AgentSecurityAuditor:
    """Agent 安全审计器"""

    # 拆分说明：以下规则常量表已整体移至模块级常量区（本文件上方，
    # 自 core.asa_patterns re-export），此处保留同名类属性引用，
    # 兼容 AgentSecurityAuditor.XXX 与 self.XXX 的既有访问。
    PROMPT_INJECTION_PATTERNS = PROMPT_INJECTION_PATTERNS
    CONTEXT_MANIPULATION_PATTERNS = CONTEXT_MANIPULATION_PATTERNS
    TOOL_MISUSE_PATTERNS = TOOL_MISUSE_PATTERNS
    BUDGET_EXHAUSTION_PATTERNS = BUDGET_EXHAUSTION_PATTERNS
    DATA_EXFIL_PATTERNS = DATA_EXFIL_PATTERNS
    PII_LEAK_PATTERNS = PII_LEAK_PATTERNS
    SECURITY_CONFIG_PATTERNS = SECURITY_CONFIG_PATTERNS
    GO_PATTERNS = GO_PATTERNS
    NODE_PATTERNS = NODE_PATTERNS
    PHP_PATTERNS = PHP_PATTERNS
    JAVA_PATTERNS = JAVA_PATTERNS
    CROSSLANG_GROUPS = CROSSLANG_GROUPS
    AUTONOMOUS_ACTION_PATTERNS = AUTONOMOUS_ACTION_PATTERNS
    SSRF_PATTERNS = SSRF_PATTERNS
    PATH_TRAVERSAL_PATTERNS = PATH_TRAVERSAL_PATTERNS
    DESERIALIZATION_PATTERNS = DESERIALIZATION_PATTERNS
    BROWSER_SANDBOX_PATTERNS = BROWSER_SANDBOX_PATTERNS
    INFO_LEAK_PATTERNS = INFO_LEAK_PATTERNS
    LLM_EXEC_PATTERNS = LLM_EXEC_PATTERNS
    AUTH_MISSING_PATTERNS = AUTH_MISSING_PATTERNS
    SECRET_WRITE_PATTERNS = SECRET_WRITE_PATTERNS
    RESOURCE_LEAK_PATTERNS = RESOURCE_LEAK_PATTERNS
    EXCLUDE_PATTERNS = EXCLUDE_PATTERNS
    EXCLUDE_DIRS = EXCLUDE_DIRS
    RESILIENCE_GAP_CHECKS = RESILIENCE_GAP_CHECKS
    SELF_EXEMPT_FILES = SELF_EXEMPT_FILES
    _SEC08_READONLY_SQL_HEADS = _SEC08_READONLY_SQL_HEADS
    _SEC06_SELF_MANAGED_RE = _SEC06_SELF_MANAGED_RE
    _SEC08_SELF_DB_RE = _SEC08_SELF_DB_RE
    _SEC08_WRITE_LITERAL_RE = _SEC08_WRITE_LITERAL_RE
    _SEC08_TMP_HANDLE_RE = _SEC08_TMP_HANDLE_RE
    _CLOSURE_STOPWORDS = _CLOSURE_STOPWORDS
    _PT_STATIC_WORDS = _PT_STATIC_WORDS
    _PT_UNSAFE_RE = _PT_UNSAFE_RE
    _SSRF_LOCAL_RE = _SSRF_LOCAL_RE
    _SECRET_WRITE_RE = _SECRET_WRITE_RE
    _CROSSLANG_FILE_LEVEL_RULES = _CROSSLANG_FILE_LEVEL_RULES
    CONFIG_CONTAINER_HINTS = CONFIG_CONTAINER_HINTS
    CATEGORY_NAMES = CATEGORY_NAMES
    CATEGORY_DETAILS = CATEGORY_DETAILS
    CATEGORY_ORDER = CATEGORY_ORDER

    def __init__(self):
        pass

    def audit(self, project_path: str) -> List[AgentSecurityRisk]:
        """执行 Agent 安全审计"""
        risks = []

        # 加载项目专属的 cache 硬编码优化（白名单）
        from core.shared_filter import SharedFilter
        SharedFilter.load_cache(project_path)

        scan_files = self._collect_target_files(project_path)

        for fpath in scan_files:
            ext = os.path.splitext(fpath)[1].lower()
            try:
                with open(fpath, "r", encoding="utf-8", errors="ignore") as f:
                    content = f.read()
            except (OSError, IOError):
                continue
            risks.extend(self._scan_file(fpath, content, ext))
            # AST 级参数透传失效检测（AGENT-SEC-27）：仅 Python（AST 解析器为 Python 专用）
            if ext == ".py":
                risks.extend(self._scan_param_shadow(fpath, content))
                # AST 级路径穿越动态拼接检测（AGENT-SEC-PT 正则盲区补充）
                risks.extend(self._scan_path_traversal_ast(fpath, content))

        # 项目级防御层级韧性缺口检测（检查缺失的防御模式）
        resilience_gaps = self._check_resilience_gaps(project_path)
        risks.extend(resilience_gaps)

        # 过滤 cache 白名单（用户标记为可接受的安全风险）
        risks = [
            r for r in risks
            if not SharedFilter.is_security_whitelisted(r.risk_id, r.file_path, r.line_number)
        ]

        # 去重：regex 层（_scan_file）与 AST 层（_scan_path_traversal_ast）可能对同一行
        # 重复产生 AGENT-SEC-PT，按 (file_path, line_number, risk_id) 去重，避免重复条目
        # 与 severity 计数错误
        seen: set = set()
        deduped: list = []
        for r in risks:
            key = (r.file_path, r.line_number, r.risk_id)
            if key in seen:
                continue
            seen.add(key)
            deduped.append(r)
        risks = deduped

        # 按严重程度排序
        risks.sort(key=lambda r: (SEVERITY_ORDER.get(r.severity, 99), r.file_path, r.line_number))
        # 暴露结构化结果，供管线统一收集
        self.risks = risks
        return risks

    def _is_excluded_dir(self, parent: str, name: str):
        """拆分说明：实现已移至模块级 _is_excluded_dir，此处保留方法签名做委托"""
        return _is_excluded_dir(parent, name)

    def _collect_target_files(self, project_path: str):
        """拆分说明：实现已移至模块级 _collect_target_files，此处保留方法签名做委托"""
        return _collect_target_files(project_path)

    def _collect_docstring_lines(self, content: str):
        """拆分说明：实现已移至模块级 _collect_docstring_lines，此处保留方法签名做委托"""
        return _collect_docstring_lines(content)

    def _collect_noise_lines(self, content: str):
        """拆分说明：实现已移至模块级 _collect_noise_lines，此处保留方法签名做委托"""
        return _collect_noise_lines(content)

    def _scan_file(self, filepath: str, content: str, ext: str='.py'):
        """拆分说明：实现已移至模块级 _scan_file，此处保留方法签名做委托"""
        return _scan_file(filepath, content, ext)

    def _lookup_var_rhs(self, lines: List[str], cur_idx: int, var: str, max_up: int=60):
        """拆分说明：实现已移至模块级 _lookup_var_rhs，此处保留方法签名做委托"""
        return _lookup_var_rhs(lines, cur_idx, var, max_up)

    def _refine_sec08(self, lines: List[str], cur_idx: int, stripped: str, indent_len: int, m, filepath: str=''):
        """拆分说明：实现已移至模块级 _refine_sec08，此处保留方法签名做委托"""
        return _refine_sec08(lines, cur_idx, stripped, indent_len, m, filepath)

    @staticmethod
    def _extract_string_literals(blob: str, max_span: int=120):
        """拆分说明：实现已移至模块级 _extract_string_literals，此处保留方法签名做委托"""
        return _extract_string_literals(blob, max_span)

    def _sec08_write_target(self, lines: List[str], cur_idx: int, stripped: str, m, indent_len: int):
        """拆分说明：实现已移至模块级 _sec08_write_target，此处保留方法签名做委托"""
        return _sec08_write_target(lines, cur_idx, stripped, m, indent_len)

    def _sec08_self_managed_db(self, lines: List[str], cur_idx: int, stripped: str, m, indent_len: int, filepath: str):
        """拆分说明：实现已移至模块级 _sec08_self_managed_db，此处保留方法签名做委托"""
        return _sec08_self_managed_db(lines, cur_idx, stripped, m, indent_len, filepath)

    def _symbol_closure_text(self, lines: List[str], seeds: List[str], max_depth: int=5):
        """拆分说明：实现已移至模块级 _symbol_closure_text，此处保留方法签名做委托"""
        return _symbol_closure_text(lines, seeds, max_depth)

    def _symbol_closure_segments(self, lines: List[str], seeds: List[str], max_depth: int=5):
        """拆分说明：实现已移至模块级 _symbol_closure_segments，此处保留方法签名做委托"""
        return _symbol_closure_segments(lines, seeds, max_depth)

    def _extract_sql_literal(self, args: str, lines: List[str], cur_idx: int):
        """拆分说明：实现已移至模块级 _extract_sql_literal，此处保留方法签名做委托"""
        return _extract_sql_literal(args, lines, cur_idx)

    def _refine_sec06(self, lines: List[str], cur_idx: int, stripped: str, indent_len: int, m):
        """拆分说明：实现已移至模块级 _refine_sec06，此处保留方法签名做委托"""
        return _refine_sec06(lines, cur_idx, stripped, indent_len, m)

    def _lookup_module_const_rhs(self, lines: List[str], var: str):
        """拆分说明：实现已移至模块级 _lookup_module_const_rhs，此处保留方法签名做委托"""
        return _lookup_module_const_rhs(lines, var)

    def _lookup_attr_rhs(self, lines: List[str], attr: str):
        """拆分说明：实现已移至模块级 _lookup_attr_rhs，此处保留方法签名做委托"""
        return _lookup_attr_rhs(lines, attr)

    def _pt_token_is_static(self, lines: List[str], cur_idx: int, tok: str):
        """拆分说明：实现已移至模块级 _pt_token_is_static，此处保留方法签名做委托"""
        return _pt_token_is_static(lines, cur_idx, tok)

    def _refine_secpt(self, lines: List[str], cur_idx: int, stripped: str, indent_len: int, m):
        """拆分说明：实现已移至模块级 _refine_secpt，此处保留方法签名做委托"""
        return _refine_secpt(lines, cur_idx, stripped, indent_len, m)

    def _refine_ssrf(self, lines: List[str], cur_idx: int, stripped: str, indent_len: int, m):
        """拆分说明：实现已移至模块级 _refine_ssrf，此处保留方法签名做委托"""
        return _refine_ssrf(lines, cur_idx, stripped, indent_len, m)

    def _refine_secret(self, lines: List[str], cur_idx: int, stripped: str):
        """拆分说明：实现已移至模块级 _refine_secret，此处保留方法签名做委托"""
        return _refine_secret(lines, cur_idx, stripped)

    def _scan_crosslang_file(self, filepath: str, content: str, ext: str):
        """拆分说明：实现已移至模块级 _scan_crosslang_file，此处保留方法签名做委托"""
        return _scan_crosslang_file(filepath, content, ext)

    def _crosslang_file_signal_lines(self, risk_id: str, content: str):
        """拆分说明：实现已移至模块级 _crosslang_file_signal_lines，此处保留方法签名做委托"""
        return _crosslang_file_signal_lines(risk_id, content)

    def _crosslang_ssrf_signal_lines(self, content: str, hit_re: re.Pattern):
        """拆分说明：实现已移至模块级 _crosslang_ssrf_signal_lines，此处保留方法签名做委托"""
        return _crosslang_ssrf_signal_lines(content, hit_re)

    def _scan_param_shadow(self, filepath: str, content: str):
        """拆分说明：实现已移至模块级 _scan_param_shadow，此处保留方法签名做委托"""
        return _scan_param_shadow(filepath, content)

    def _scan_path_traversal_ast(self, filepath: str, content: str):
        """拆分说明：实现已移至模块级 _scan_path_traversal_ast，此处保留方法签名做委托"""
        return _scan_path_traversal_ast(filepath, content)

    def _scan_function_param_shadow(self, node, filepath: str, content: str):
        """拆分说明：实现已移至模块级 _scan_function_param_shadow，此处保留方法签名做委托"""
        return _scan_function_param_shadow(node, filepath, content)

    def _match_param_shadow(self, param: str, value):
        """拆分说明：实现已移至模块级 _match_param_shadow，此处保留方法签名做委托"""
        return _match_param_shadow(param, value)

    @staticmethod
    def _iter_body_assigns(body):
        """拆分说明：实现已移至模块级 _iter_body_assigns，此处保留方法签名做委托"""
        return _iter_body_assigns(body)

    @staticmethod
    def _contains_name(value, name: str):
        """拆分说明：实现已移至模块级 _contains_name，此处保留方法签名做委托"""
        return _contains_name(value, name)

    def _match_config_shadow(self, value, param: str):
        """拆分说明：实现已移至模块级 _match_config_shadow，此处保留方法签名做委托"""
        return _match_config_shadow(value, param)

    @staticmethod
    def _container_leaf_name(base):
        """拆分说明：实现已移至模块级 _container_leaf_name，此处保留方法签名做委托"""
        return _container_leaf_name(base)

    def _is_config_container(self, name: str):
        """拆分说明：实现已移至模块级 _is_config_container，此处保留方法签名做委托"""
        return _is_config_container(name)

    def _check_resilience_gaps(self, project_path: str):
        """拆分说明：实现已移至模块级 _check_resilience_gaps，此处保留方法签名做委托"""
        return _check_resilience_gaps(project_path)

    def _collect_call_args(self, lines: List[str], start_idx: int, stripped: str, from_col: int, keep_parens: bool=False):
        """拆分说明：实现已移至模块级 _collect_call_args，此处保留方法签名做委托"""
        return _collect_call_args(lines, start_idx, stripped, from_col, keep_parens)

    def _is_pattern_definition(self, line: str, risk_name: str):
        """拆分说明：实现已移至模块级 _is_pattern_definition，此处保留方法签名做委托"""
        return _is_pattern_definition(line, risk_name)

    def _report_severity_stats(self, risks: List[AgentSecurityRisk]):
        """拆分说明：实现已移至模块级 _report_severity_stats，此处保留方法签名做委托"""
        return _report_severity_stats(risks)

    def _render_report_header(self, risks: List[AgentSecurityRisk], project_path: str, stats: dict):
        """拆分说明：实现已移至模块级 _render_report_header，此处保留方法签名做委托"""
        return _render_report_header(risks, project_path, stats)

    def _render_category_summary(self, by_category: 'defaultdict'):
        """拆分说明：实现已移至模块级 _render_category_summary，此处保留方法签名做委托"""
        return _render_category_summary(by_category)

    def _render_risk_details(self, by_category: 'defaultdict'):
        """拆分说明：实现已移至模块级 _render_risk_details，此处保留方法签名做委托"""
        return _render_risk_details(by_category)

    def _render_report_footer(self):
        """拆分说明：实现已移至模块级 _render_report_footer，此处保留方法签名做委托"""
        return _render_report_footer()

    def to_report(self, risks: List[AgentSecurityRisk], project_path: str):
        """拆分说明：实现已移至模块级 to_report，此处保留方法签名做委托"""
        return to_report(risks, project_path)


def audit_agent_security(project_path: str) -> str:
    """便捷函数"""
    auditor = AgentSecurityAuditor()
    risks = auditor.audit(project_path)
    return auditor.to_report(risks, project_path)
