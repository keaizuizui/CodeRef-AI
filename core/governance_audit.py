# -*- coding: utf-8 -*-
"""
代码治理审计 —— 基于 AI 编程治理框架，检测铁律违反和错题本模式

检测维度：
1. 架构铁律：模块依赖方向、循环依赖、层级穿透
2. 变更铁律：高危操作模式识别
3. 质量铁律：函数长度、参数数量、嵌套深度、圈复杂度
4. 安全铁律：硬编码密钥、SQL注入风险、命令注入风险
5. 错题本模式：空catch块、裸except、不安全的默认值、竞态条件

与 CodeSimplifier 互补：
- CodeSimplifier 聚焦代码精简（YAGNI、死代码、过度工程）
- GovernanceAuditor 聚焦安全与架构合规（铁律、错题本）

作者: PersuadeAI Team
版本: v1.0
"""

import os

from loguru import logger



# ═══════════════════════════════════════════════════════════════════
# 数据结构
# ═══════════════════════════════════════════════════════════════════

# 常量规则区 + 数据模型已拆至 core.governance_base（god-module-split 试点），此处 re-export 兼容
from core.governance_base import (
    SEVERITY_ORDER, CWE_MAPPING, OWASP_MAPPING,
    GovernanceViolation, GovernanceReport,
    CREDENTIAL_VALUE, HARDCODED_SECRET_PATTERNS, SECRET_EXCLUDE_PATTERNS,
    SQL_INJECTION_PATTERNS, COMMAND_INJECTION_PATTERNS, UNSAFE_DESERIALIZE,
    HARDCODED_NETWORK, DANGEROUS_FUNCTIONS, CROSS_LANG_COMMAND_EXEC,
    UNSAFE_SUBPROCESS, PATH_TRAVERSAL, WEAK_CRYPTO, SSRF_PATTERNS, XSS_PATTERNS,
    INSECURE_RANDOM, WEAK_HASH, SSL_VERIFY_DISABLED, RESOURCE_EXHAUSTION,
    NON_PY_SECRET_EXTENSIONS, NON_PY_SECRET_MASK_PATTERN,
    DOC_EXTENSIONS, DOC_SECRET_PATTERNS, DOC_ENV_CRED_PATTERNS,
    GOV_BYPASS_PATTERNS, DOC_CLAIM_PATTERNS,
    SECRET_PERSIST_PATTERNS, SECRET_PERSIST_FUNC, SENSITIVE_LOG,
    UNSAFE_DESERIALIZE_EXTENDED,
    PITFALL_EMPTY_EXCEPT, PITFALL_MUTABLE_DEFAULT, PITFALL_UNSAFE_FILE,
    PITFALL_RACE_CONDITION, PITFALL_THREAD_UNSAFE, PITFALL_IO_PATTERNS,
    FUNCTION_TOO_LONG, FUNCTION_TOO_MANY_PARAMS, NESTING_TOO_DEEP,
    LAYER_RULES, DEFAULT_LAYER_PATTERNS, BLOCKING_CALLS,
    DOC_SECRET_PLACEHOLDER_VALUES,
)

# __all__：re-export 兼容契约（god-module-split 试点）。
# 常量/数据模型来自 governance_base、检测函数来自 governance_checks，
# 全部在此 re-export 以维持对外符号面不变（含 tests 直接 import 的
# _is_wl_excluded / _apply_whitelist_exclude / GovernanceAuditor）。
__all__ = [
    "SEVERITY_ORDER", "CWE_MAPPING", "OWASP_MAPPING",
    "GovernanceViolation", "GovernanceReport",
    "CREDENTIAL_VALUE", "HARDCODED_SECRET_PATTERNS", "SECRET_EXCLUDE_PATTERNS",
    "SQL_INJECTION_PATTERNS", "COMMAND_INJECTION_PATTERNS", "UNSAFE_DESERIALIZE",
    "HARDCODED_NETWORK", "DANGEROUS_FUNCTIONS", "CROSS_LANG_COMMAND_EXEC",
    "UNSAFE_SUBPROCESS", "PATH_TRAVERSAL", "WEAK_CRYPTO", "SSRF_PATTERNS", "XSS_PATTERNS",
    "INSECURE_RANDOM", "WEAK_HASH", "SSL_VERIFY_DISABLED", "RESOURCE_EXHAUSTION",
    "NON_PY_SECRET_EXTENSIONS", "NON_PY_SECRET_MASK_PATTERN",
    "DOC_EXTENSIONS", "DOC_SECRET_PATTERNS", "DOC_ENV_CRED_PATTERNS",
    "GOV_BYPASS_PATTERNS", "DOC_CLAIM_PATTERNS",
    "SECRET_PERSIST_PATTERNS", "SECRET_PERSIST_FUNC", "SENSITIVE_LOG",
    "UNSAFE_DESERIALIZE_EXTENDED",
    "PITFALL_EMPTY_EXCEPT", "PITFALL_MUTABLE_DEFAULT", "PITFALL_UNSAFE_FILE",
    "PITFALL_RACE_CONDITION", "PITFALL_THREAD_UNSAFE", "PITFALL_IO_PATTERNS",
    "FUNCTION_TOO_LONG", "FUNCTION_TOO_MANY_PARAMS", "NESTING_TOO_DEEP",
    "LAYER_RULES", "DEFAULT_LAYER_PATTERNS", "BLOCKING_CALLS",
    "DOC_SECRET_PLACEHOLDER_VALUES",
    "_get_docstring_lines",
    "_is_credential_false_positive",
    "_is_pattern_def_line",
    "_is_sensitive_log_false_positive",
    "_is_command_injection_false_positive",
    "_is_json_in_try_block",
    "_is_static_html_generator",
    "_get_call_string",
    "_doc_value_is_placeholder",
    "_doc_placeholder",
    "_doc_violation",
    "_get_module_name",
    "_classify_layer",
    "_extract_import",
    "_match_import_to_file",
    "_cross_lang_rule_applies",
    "_check_security",
    "_check_pitfalls",
    "_check_async_blocking",
    "_check_quality",
    "_whitelist_exclude_dirs",
    "_is_wl_excluded",
    "_apply_whitelist_exclude",
    "_scan_non_py_secrets",
    "_scan_doc_secrets",
    "_scan_crossregion_conflicts",
    "_check_secret_persistence",
    "_check_architecture",
    "_generate_report",
    "GovernanceAuditor",
]


class GovernanceAuditor:
    """
    代码治理审计器

    按 5 大维度扫描代码库，检测铁律违反和错题本模式。
    所有检测基于正则/AST 静态分析，不依赖 LLM。
    """

    # 拆分说明：以下规则常量表已整体移至模块级常量区（本文件上方），
    # 此处保留同名类属性引用，兼容 GovernanceAuditor.XXX 与 self.XXX 的既有访问。
    CREDENTIAL_VALUE = CREDENTIAL_VALUE
    HARDCODED_SECRET_PATTERNS = HARDCODED_SECRET_PATTERNS
    SECRET_EXCLUDE_PATTERNS = SECRET_EXCLUDE_PATTERNS
    SQL_INJECTION_PATTERNS = SQL_INJECTION_PATTERNS
    COMMAND_INJECTION_PATTERNS = COMMAND_INJECTION_PATTERNS
    UNSAFE_DESERIALIZE = UNSAFE_DESERIALIZE
    HARDCODED_NETWORK = HARDCODED_NETWORK
    DANGEROUS_FUNCTIONS = DANGEROUS_FUNCTIONS
    CROSS_LANG_COMMAND_EXEC = CROSS_LANG_COMMAND_EXEC
    UNSAFE_SUBPROCESS = UNSAFE_SUBPROCESS
    PATH_TRAVERSAL = PATH_TRAVERSAL
    WEAK_CRYPTO = WEAK_CRYPTO
    SSRF_PATTERNS = SSRF_PATTERNS
    XSS_PATTERNS = XSS_PATTERNS
    INSECURE_RANDOM = INSECURE_RANDOM
    WEAK_HASH = WEAK_HASH
    SSL_VERIFY_DISABLED = SSL_VERIFY_DISABLED
    RESOURCE_EXHAUSTION = RESOURCE_EXHAUSTION
    NON_PY_SECRET_EXTENSIONS = NON_PY_SECRET_EXTENSIONS
    NON_PY_SECRET_MASK_PATTERN = NON_PY_SECRET_MASK_PATTERN
    DOC_EXTENSIONS = DOC_EXTENSIONS
    DOC_SECRET_PATTERNS = DOC_SECRET_PATTERNS
    DOC_ENV_CRED_PATTERNS = DOC_ENV_CRED_PATTERNS
    GOV_BYPASS_PATTERNS = GOV_BYPASS_PATTERNS
    DOC_CLAIM_PATTERNS = DOC_CLAIM_PATTERNS
    SECRET_PERSIST_PATTERNS = SECRET_PERSIST_PATTERNS
    SECRET_PERSIST_FUNC = SECRET_PERSIST_FUNC
    SENSITIVE_LOG = SENSITIVE_LOG
    UNSAFE_DESERIALIZE_EXTENDED = UNSAFE_DESERIALIZE_EXTENDED
    PITFALL_EMPTY_EXCEPT = PITFALL_EMPTY_EXCEPT
    PITFALL_MUTABLE_DEFAULT = PITFALL_MUTABLE_DEFAULT
    PITFALL_UNSAFE_FILE = PITFALL_UNSAFE_FILE
    PITFALL_RACE_CONDITION = PITFALL_RACE_CONDITION
    PITFALL_THREAD_UNSAFE = PITFALL_THREAD_UNSAFE
    PITFALL_IO_PATTERNS = PITFALL_IO_PATTERNS
    FUNCTION_TOO_LONG = FUNCTION_TOO_LONG
    FUNCTION_TOO_MANY_PARAMS = FUNCTION_TOO_MANY_PARAMS
    NESTING_TOO_DEEP = NESTING_TOO_DEEP
    LAYER_RULES = LAYER_RULES
    DEFAULT_LAYER_PATTERNS = DEFAULT_LAYER_PATTERNS
    BLOCKING_CALLS = BLOCKING_CALLS
    DOC_SECRET_PLACEHOLDER_VALUES = DOC_SECRET_PLACEHOLDER_VALUES

    def __init__(self):
        pass

    def audit(self, project_path: str) -> str:
        """
        执行治理审计

        Args:
            project_path: 项目路径

        Returns:
            Markdown 格式的治理审计报告
        """
        from core.code_analyzer import CodeAnalyzer

        logger.info(f"[GovernanceAudit] 开始扫描: {project_path}")

        # 加载项目专属的 cache 硬编码优化（白名单）
        from core.shared_filter import SharedFilter
        SharedFilter.load_cache(project_path)

        # 1. 基础分析
        analyzer = CodeAnalyzer()
        analysis = analyzer.analyze_project(project_path)

        # 1.5 统一 whitelist 目录排除：备份/镜像目录不进入规则层审计，
        #     避免污染 PITFALL/security/quality/architecture 等编程规则结果
        #     （与 _scan_non_py_secrets 同一排除口径，根治图谱干净≠审计干净）
        analysis = _apply_whitelist_exclude(analysis, project_path)

        violations = []

        # 检测器自身文件集（拆解后三件套）：跳过自身，避免规则描述文本自命中
        # （如 unserialize 规则的说明字符串 "PHP 的 unserialize() 可被利用..." 命中自身正则），
        # 参照 resource_gap_detector 的自身文件豁免机制
        self_files = {"governance_audit.py", "governance_base.py",
                      "governance_checks.py"}

        # 2. 逐文件检测
        for cf in analysis.files:
            if os.path.basename(cf.file_path) in self_files:
                continue
            # 安全铁律
            violations.extend(self._check_security(cf))
            # 密钥明文落盘（写入 .env 等凭据文件）
            violations.extend(self._check_secret_persistence(cf))
            # 错题本模式
            violations.extend(self._check_pitfalls(cf))
            # 异步函数中同步阻塞（AST 级别检测）
            violations.extend(self._check_async_blocking(cf))
            # 质量铁律
            violations.extend(self._check_quality(cf))

        # 3. 架构铁律（跨文件分析）
        violations.extend(self._check_architecture(analysis))

        # 3.5 非 Python 文件机密扫描（.env/.yaml/.json/.toml等）
        violations.extend(self._scan_non_py_secrets(project_path))

        # 3.6 文档类文件（.md/.SKILL/.txt）治理合规扫描（明文凭据 + 审查绕过）
        violations.extend(self._scan_doc_secrets(project_path))

        # 3.6.5 跨地区合规条款矛盾检测（领土主权表述冲突 + 统计数据版本差异污染）
        violations.extend(self._scan_crossregion_conflicts(project_path))

        # 3.7 集中过滤 cache 白名单（确保所有违规都经过白名单检查）
        #    注意：_check_security 内部也有过滤，这里做集中兜底
        from core.cache_manager import cache_manager
        cache_manager.load_hardcoded(project_path)
        logger.info(f"[GovernanceAudit] 白名单已加载，安全规则白名单条目数: {sum(len(v) for v in cache_manager._security_whitelist.values())}")
        violations = [
            v for v in violations
            if not cache_manager.is_security_whitelisted(v.rule_id, v.file_path, v.line_number)
        ]

        # 3.8 集中兜底：跨文件检测（架构/循环依赖）中自身文件作为一端出现的
        #     条目同样豁免，确保 governance 三件套不出现在任何 findings 中
        violations = [
            v for v in violations
            if os.path.basename(v.file_path) not in self_files
        ]

        # 4. 构建报告
        self.violations = violations
        report = GovernanceReport(
            project_path=project_path,
            total_files=analysis.total_files,
            total_lines=analysis.total_lines,
            violations=violations,
        )
        self.report = report

        logger.info(f"[GovernanceAudit] 审计完成: {len(violations)} 条违规 "
                     f"(critical={report.critical_count}, high={report.high_count}, "
                     f"medium={report.medium_count}, low={report.low_count}) "
                     f"健康分={report.clean_score if report.clean_score is not None else 'N/A'}")

        # 缺陷 9：无代码文件时显式提示，避免"静默健康（满分）"误导
        if analysis.total_files == 0:
            logger.warning(
                f"[GovernanceAudit] 未发现任何可分析的代码文件，"
                f"当前审计结果健康分为 N/A（非满分），审计可能无意义。"
                f"项目路径: {project_path}")

        return self._generate_report(report)

    def _check_security(self, cf):
        """拆分说明：实现已移至模块级 _check_security，此处保留方法签名做委托"""
        return _check_security(cf)

    def _check_pitfalls(self, cf):
        """拆分说明：实现已移至模块级 _check_pitfalls，此处保留方法签名做委托"""
        return _check_pitfalls(cf)

    def _check_async_blocking(self, cf):
        """拆分说明：实现已移至模块级 _check_async_blocking，此处保留方法签名做委托"""
        return _check_async_blocking(cf)

    def _check_quality(self, cf):
        """拆分说明：实现已移至模块级 _check_quality，此处保留方法签名做委托"""
        return _check_quality(cf)

    def _scan_non_py_secrets(self, project_path: str):
        """拆分说明：实现已移至模块级 _scan_non_py_secrets，此处保留方法签名做委托"""
        return _scan_non_py_secrets(project_path)

    def _scan_doc_secrets(self, project_path: str):
        """拆分说明：实现已移至模块级 _scan_doc_secrets，此处保留方法签名做委托"""
        return _scan_doc_secrets(project_path)

    def _scan_crossregion_conflicts(self, project_path: str):
        """拆分说明：实现已移至模块级 _scan_crossregion_conflicts，此处保留方法签名做委托"""
        return _scan_crossregion_conflicts(project_path)

    def _check_secret_persistence(self, cf):
        """拆分说明：实现已移至模块级 _check_secret_persistence，此处保留方法签名做委托"""
        return _check_secret_persistence(cf)

    def _check_architecture(self, analysis):
        """拆分说明：实现已移至模块级 _check_architecture，此处保留方法签名做委托"""
        return _check_architecture(analysis)

    def _generate_report(self, report: GovernanceReport):
        """拆分说明：实现已移至模块级 _generate_report，此处保留方法签名做委托"""
        return _generate_report(report)

# ==================== 模块级检测函数（自 GovernanceAuditor 提取，纯静态检测逻辑） ====================
# 模块级检测函数 + 报告生成已拆至 core.governance_checks（god-module-split 试点），此处 re-export 兼容
from core.governance_checks import (
    _get_docstring_lines, _is_credential_false_positive, _is_pattern_def_line,
    _is_sensitive_log_false_positive, _is_command_injection_false_positive,
    _is_json_in_try_block, _is_static_html_generator, _get_call_string,
    _doc_value_is_placeholder, _doc_placeholder, _doc_violation,
    _get_module_name, _classify_layer, _extract_import, _match_import_to_file,
    _cross_lang_rule_applies,
    _check_security, _check_pitfalls, _check_async_blocking, _check_quality,
    _whitelist_exclude_dirs, _is_wl_excluded, _apply_whitelist_exclude,
    _scan_non_py_secrets, _scan_doc_secrets, _scan_crossregion_conflicts,
    _check_secret_persistence, _check_architecture,
    _generate_report,
)


