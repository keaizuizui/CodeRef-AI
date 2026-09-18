# -*- coding: utf-8 -*-
"""
治理检测函数 —— 自 governance_audit 拆出的检查层（god-module-split 试点）

包含全部模块级检测函数（_check_*/_scan_*/_is_*/_get_*/架构辅助）与报告生成
_generate_report，以及随函数迁入的共享过滤器实例 _sf = SharedFilter()。
本模块只依赖 governance_base（常量/模型）与既有公共依赖（core.shared_filter
等延迟 import 保持原位），不反向依赖 governance_audit。
"""

import os
import re
from typing import List, Optional
from collections import defaultdict

from core.shared_filter import SharedFilter
from core.governance_base import (
    SEVERITY_ORDER, GovernanceViolation, GovernanceReport,
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

# 创建共享过滤器实例（自 governance_audit 随使用它的检测函数迁入，单例）
_sf = SharedFilter()


def _get_docstring_lines(lines: List[str]) -> set:
    """委托给 SharedFilter"""
    return _sf.get_docstring_lines(lines)


def _is_credential_false_positive(line: str) -> bool:
    """判断硬编码凭据匹配是否为误报"""
    # 1. 如果行是正则模式（包含 r' 或 r"），跳过
    #    注意：需要覆盖所有关键词，不只是 password
    if re.search(r"""r['"].*(?:password|passwd|pwd|secret|token|api_key|apikey|access_key|private_key)""", line, re.IGNORECASE):
        return True
    # 2. 如果行是函数定义或 lambda，匹配的是参数名而非值
    if re.search(r'def\s+\w+\s*\(', line) or re.search(r'lambda\s+', line):
        return True
    # 3. 如果行包含 self.config、self._config、os.environ、os.getenv
    if re.search(r'self\.(?:config|_config|settings|_settings|cfg)|os\.environ|os\.getenv|getenv\(', line):
        return True
    # 3b. 如果行是配置对象的属性访问（cfg.xxx、config.xxx、settings.xxx）
    if re.search(r'\b(?:cfg|config|settings)\.\w+', line):
        return True
    # 3c. 如果行是函数调用且参数名与值相同（api_key=api_key, base_url=base_url）
    if re.search(r'\b(api_key|apikey|secret|token|password|passwd|pwd|base_url|access_key|private_key)\s*=\s*\1\b', line, re.IGNORECASE):
        return True
    # 4. 如果行是注释（以 # 开头）
    stripped = line.strip()
    if stripped.startswith('#'):
        return True
    # 5. 如果行是类型注解或接口定义（不含实际赋值字面量）
    if re.match(r'^\s*\w+\s*:\s*(Optional|str|Dict|List|Tuple|Any|Union)', line) \
            and not re.search(r'[:=]\s*[`"\']', line):
        return True
    # 6. 如果行是文档字符串的一部分
    if stripped.startswith('"""') or stripped.startswith("'''"):
        return True
    # 7. 如果值是纯变量名引用（非字符串字面量），排除
    #    例如: api_key=env_key, token=my_token
    if re.search(r'\b(?:password|passwd|pwd|secret|token|api_key|apikey|access_key|private_key)\b\s*=\s*([a-zA-Z_]\w*)\s*[,)]?\s*$', line, re.IGNORECASE):
        return True
    # 8. GUI 组件创建（如 QLineEdit()）
    if re.search(r'=\s*Q\w+\(\)', line):
        return True
    # 9. 值是对象属性访问（如 request.api_key, cfg.xxx）
    if re.search(r'\b(?:password|passwd|pwd|secret|token|api_key|apikey|access_key|private_key)\b\s*=\s*\w+\.\w+', line, re.IGNORECASE):
        return True
    # 10. 自定义密钥常量（变量名含 Key/Secret/Token/Pwd 等词尾）的值是
    #     占位符/示例/规则串：复用 CREDENTIAL_VALUE 提取的值，排除
    #     your_xxx/changeme/REPLACE 等占位符，以及纯字母/纯数字/重复字符等
    #     非随机串，避免把普通标识符常量误报为密钥。
    if re.search(r'\b(?:Key|Secret|Token|Credential|Passwd|Pwd)\w*\s*[:=]\s*[`"\']', line, re.IGNORECASE):
        m = CREDENTIAL_VALUE.search(line)
        if m:
            v = m.group(2)
            if re.search(r'(?i)(your_|your-|xxx+|changeme|replace|example|placeholder|dummy|sample|todo|fixme|<|>|demo|fake|mock|password)', v):
                return True
            if re.fullmatch(r'[a-z]+', v) or re.fullmatch(r'[A-Z]+', v) \
                    or re.fullmatch(r'\d+', v) or re.fullmatch(r'(.)\1{15,}', v):
                return True
    return False


def _is_pattern_def_line(line: str) -> bool:
    """委托给 SharedFilter"""
    return _sf.is_pattern_def_line(line)


def _is_sensitive_log_false_positive(line: str) -> bool:
    """判断敏感日志匹配是否为误报（仅描述性提到 API Key，非实际泄露）"""
    stripped = line.strip()
    # 日志消息中仅包含 "API Key" 作为描述性文字（如 "未设置API Key"）
    if re.search(r'(?:logger\.|print\s*\()\s*[^,)]*["\'][^"\']*(?:未设置|占位符|暂不可用|not\s+(?:set|available|configured)|empty|placeholder|skip|跳过)[^"\']*["\']', stripped, re.IGNORECASE):
        return True
    # 日志消息中提到 "api_key" 字样但只是描述状态
    if re.search(r'logger\.(?:debug|info|warning|error)\s*\(\s*f?["\'].*\b(?:api_key|API\s*Key)\b.*(?:占位符|跳过|空|暂不可用|not\s+(?:set|available|configured))', stripped, re.IGNORECASE):
        return True
    return False


def _is_command_injection_false_positive(line: str) -> bool:
    """判断命令注入匹配是否为误报（参数是内部路径而非用户输入）"""
    stripped = line.strip()
    # Popen/explorer 打开内部路径（self.xxx, 非用户输入）
    if re.search(r'self\.\w+', stripped, re.IGNORECASE):
        return True
    # Popen 中使用 os.path 构造的路径
    if re.search(r'os\.path\.\w+', stripped, re.IGNORECASE):
        return True
    return False


def _is_json_in_try_block(lines: List[str], line_idx: int) -> bool:
    """委托给 SharedFilter"""
    return _sf.is_in_try_block(lines, line_idx)


def _is_static_html_generator(content: str) -> bool:
    """判断文件是否是"生成静态 HTML 报告/可视化页面"的代码。

    用于 IRON-SEC-12 XSS 降级：这类文件的 innerHTML 注入目标是本地
    查看页面，渲染数据是分析器自产的结构化产物而非用户输入，无对外
    攻击面（仅理论二阶注入）。判据从简且保守——文件需含 HTML 写文件
    特征才认定；面向用户输入渲染的 Web 服务代码不含这些特征，保持 high。
    """
    if not content:
        return False
    hints = (
        r'\.html["\']',        # .html 文件名字符串（写入 HTML 报告/可视化文件）
        r'\bHTML_TEMPLATE\b',  # HTML 模板常量（内嵌静态页面模板）
    )
    return any(re.search(h, content) for h in hints)


def _get_call_string(node) -> str:
    """从 AST Call 节点获取完整调用字符串"""
    import ast
    func = node.func
    if isinstance(func, ast.Name):
        return func.id
    elif isinstance(func, ast.Attribute):
        current = func
        attr_parts = []
        while isinstance(current, ast.Attribute):
            attr_parts.append(current.attr)
            current = current.value
        if isinstance(current, ast.Name):
            attr_parts.append(current.id)
        attr_parts.reverse()
        return ".".join(attr_parts)
    return ""


def _doc_value_is_placeholder(value: str) -> bool:
    """判断密钥赋值语句的值侧是否为已知占位符（不区分大小写）。

    保守实现：仅按常见占位词/包裹形式精确匹配，不做熵计算。
    宁可漏掉少见占位符，也不放过真密钥——高熵长随机串、sk- 后跟
    20+ 字符的真密钥格式均不在占位清单内，仍会正常报出。
    """
    v = value.strip()
    # 去包裹引号/反引号（"your-api-key" → your-api-key）
    while len(v) >= 2 and v[0] == v[-1] and v[0] in ("'", '"', '`'):
        v = v[1:-1].strip()
    if not v:
        return True
    lv = v.lower()
    # 变量引用/角括号占位：<your-api-key> / ${VAR} / $VAR / {{VAR}}
    if re.fullmatch(r'<[^<>]*>|\$\{[^}]*\}|\$[A-Za-z_]\w*|\{\{[^}]*\}\}', v):
        return True
    # 已知占位词精确匹配
    if lv in DOC_SECRET_PLACEHOLDER_VALUES:
        return True
    # your-xxx / your_xxx / sk-xxx / sk-your 等短占位前缀（限长 24，
    # 确保不会误吞长随机真密钥）
    if len(lv) <= 24 and re.fullmatch(r'(?:your[-_][a-z0-9_-]*|sk-(?:x+|your[-_a-z]*))', lv):
        return True
    return False


def _doc_placeholder(line: str) -> bool:
    """判断文档行是否仅为占位符/示例配置，避免把示例密钥误报为明文泄露。

    复用 _scan_non_py_secrets 的占位符思路（your_key_here/changeme/xxx 等），
    并覆盖角括号占位符（= <your_key>），供文档类扫描过滤 guidance 用。
    """
    # 值侧占位符识别：提取行内最后一个 = 后的赋值值（去引号）做占位判断。
    # export CODEREF_API_KEY="your-api-key" 这类值包在引号内的占位写法，
    # 下方行尾正则匹配不到（占位词后有引号收尾），必须取值侧判断。
    m = re.search(r'=([^=]*)$', line.strip())
    if m and _doc_value_is_placeholder(m.group(1)):
        return True
    if re.search(r'(?i)(your_|your-|your\s+key|your\s+secret|your\s+token|'
                 r'changeme|replace_me|replace\s+me|xxx|placeholder|\be\.?g\.?\b|'
                 r'example|dummy|sample|todo|fixme|<[A-Za-z_>-]+>|`<[^`]+>`)\s*$', line.strip()):
        return True
    return False


def _doc_violation(rule_id, rule_name, severity, detail, fpath, lineno, line) -> GovernanceViolation:
    """构造文档类违规项"""
    return GovernanceViolation(
        rule_id=rule_id, rule_name=rule_name, category="security",
        severity=severity, file_path=fpath, line_number=lineno,
        line_content=line.strip()[:120], detail=detail,
        suggestion="将凭据改为占位符并从密钥管理服务读取；审查绕过/忽略清单应受审计追踪，不得静默放行",
    )


def _get_module_name(file_path: str) -> str:
    """从文件路径提取模块名"""
    return os.path.splitext(os.path.basename(file_path))[0]


def _classify_layer(file_path: str, code_content: str = "") -> str:
    """
    根据文件路径和代码内容分类层级
    
    优先使用 LLM 分析代码内容确定层级（通用方案），
    LLM 不可用时使用默认目录名模式匹配（降级方案）。
    
    开源项目建议：通过 LLM 分析代码内容而非目录名来确定层级，
    这样可以适应任意项目结构。
    """
    fp_lower = file_path.replace("\\", "/").lower()
    parts = fp_lower.split("/")

    # 尝试用 LLM 分析代码内容确定层级
    if code_content and len(code_content) > 30:
        try:
            # 检查代码内容中的关键模式（通用检测，不依赖目录名）
            # 真正的 web 入口一定带路由装饰器；仅出现框架名（如检测器自身
            # 内容含 "FastAPI"/"Flask" 字符串）不代表入口，不应据此判 entry，
            # 否则会把"检测框架名的检测器"误分类为入口层造成层级穿透误报。
            # 移除裸 'def get(' 等模式（cache.get() 等普通方法会被误判为入口），
            # 仅保留装饰器模式与框架类名；类继承视图改为下方正则检测
            if any(kw in code_content for kw in ['@app.route', '@app.get', '@app.post',
                                                   '@app.put', '@app.delete', '@router.',
                                                   'Blueprint', 'APIView',
                                                   'JSONResponse']):
                return "entry"
            # 检测视图类子类中的 HTTP 方法定义（Django REST / Tornado 等），
            # 仅当类继承自 APIView/Resource/RequestHandler/ViewSet/Controller 时
            # 才将 def get/post 等视为入口
            if re.search(r'class\s+\w+\s*\([^)]*(?:APIView|Resource|RequestHandler|ViewSet|Controller)[^)]*\)',
                         code_content) and \
               re.search(r'def\s+(?:get|post|put|delete|patch)\s*\(', code_content):
                return "entry"
            # 如果包含 ORM 模型定义 → data
            if any(kw in code_content for kw in ['class Meta:', 'db.Model', 'BaseModel', 'Table(',
                                                   'Column(', 'session.query', '__tablename__',
                                                   'SQLAlchemy', 'declarative_base']):
                return "data"
            # 如果包含核心业务逻辑特征 → core
            if re.search(r'class\s+\w*(Service|Engine|Manager|Handler|Agent|Processor)', code_content):
                return "core"
        except Exception:
            pass

    # 降级：按目录名精确匹配（使用可配置的 DEFAULT_LAYER_PATTERNS）
    for part in reversed(parts):
        for layer, patterns in DEFAULT_LAYER_PATTERNS.items():
            if part in patterns:
                return layer

    return "other"


def _extract_import(line: str) -> Optional[str]:
    """从代码行提取导入路径"""
    line = line.strip()
    # Python import
    m = re.match(r'(?:from|import)\s+([\w.]+)', line)
    if m:
        return m.group(1)
    # JS/TS import
    m = re.match(r'import\s+.*\s+from\s+["\']([^"\']+)["\']', line)
    if m:
        return m.group(1)
    # JS/TS require
    m = re.match(r'(?:const|let|var)\s+\w+\s*=\s*require\s*\(\s*["\']([^"\']+)["\']', line)
    if m:
        return m.group(1)
    return None


def _match_import_to_file(imp: str, all_files: List[str]) -> List[str]:
    """将导入路径匹配到实际文件（严格后缀匹配，避免子串误报）"""
    matched = []
    imp_normalized = imp.replace(".", "/").replace("\\", "/")
    imp_parts = imp_normalized.split("/")
    # 至少需要2级路径才算有效匹配
    if len(imp_parts) < 1:
        return matched

    # 规则：导入路径的最后 N 段必须与文件路径的最后 N 段严格匹配
    for f in all_files:
        f_normalized = f.replace("\\", "/")
        f_parts = f_normalized.split("/")
        # 去掉文件扩展名比较
        f_last = f_parts[-1]
        if "." in f_last:
            f_last = f_last.rsplit(".", 1)[0]
            f_parts[-1] = f_last

        # 方法1：导入路径的最后2段与文件路径的最后2段严格相等
        if len(imp_parts) >= 2 and len(f_parts) >= 2:
            if imp_parts[-1] == f_parts[-1] and imp_parts[-2] == f_parts[-2]:
                matched.append(f)
                continue

        # 方法2：导入路径的最后1段与文件路径的最后1段严格相等（仅当导入路径只有1段时）
        if len(imp_parts) == 1 and len(f_parts) >= 1:
            if imp_parts[0] == f_parts[-1]:
                matched.append(f)
                continue

    return matched


def _cross_lang_rule_applies(rule_name: str, cf) -> bool:
    """判断跨语言命令执行子规则是否适用于当前文件语言。

    IRON-SEC-17 是跨语言规则（Go/PHP/Java/Node），各子规则的正则关键词
    （如 PHP 的 system()）在 Python 等语言中可能作为普通 API 名出现
    （如 platform.system()），必须按文件语言过滤，避免 PHP 专属规则
    误报 Python 代码（缺陷 1）。Python 的命令执行风险由
    COMMAND_INJECTION_PATTERNS / UNSAFE_SUBPROCESS 等规则覆盖。
    """
    lang = str(getattr(cf, "language", "") or "").lower()
    if "exec.Command" in rule_name:      # Go 子规则
        return lang == "go"
    if "PHP" in rule_name:               # PHP 子规则
        return lang == "php"
    if "Java" in rule_name:              # Java 子规则
        return lang == "java"
    if "Node" in rule_name:              # Node 子规则
        return lang in ("javascript", "typescript")
    return True


def _check_security(cf) -> List[GovernanceViolation]:
    """检测安全铁律违规"""
    violations = []
    lines = cf.raw_content.splitlines() if cf.raw_content else []

    # 预解析文档字符串范围，跳过其中的所有安全检测
    docstring_lines = _get_docstring_lines(lines)

    # XSS 降级预判：该文件是否为生成静态 HTML 报告/可视化的代码
    is_static_html_gen = _is_static_html_generator(cf.raw_content or "")

    for i, line in enumerate(lines, 1):
        line_stripped = line.strip()

        # 跳过文档字符串行（所有检测）
        if i in docstring_lines:
            continue

        # 跳过注释行（所有检测）
        if line_stripped.startswith("#") or line_stripped.startswith("//"):
            continue

        # 跳过安全检测规则定义行（字符串中的代码模式）
        is_pattern_def = _is_pattern_def_line(line_stripped)

        # 硬编码凭据
        for pattern, rule_id, rule_name, severity, detail in HARDCODED_SECRET_PATTERNS:
            if pattern.search(line_stripped):
                # 排除配置读取、环境变量、注释
                if any(ep.search(line_stripped) for ep in SECRET_EXCLUDE_PATTERNS):
                    continue
                # 上下文过滤：排除正则模式、函数参数、配置读取等误报
                if _is_credential_false_positive(line_stripped):
                    continue
                violations.append(GovernanceViolation(
                    rule_id=rule_id, rule_name=rule_name, category="security",
                    severity=severity, file_path=cf.file_path, line_number=i,
                    line_content=line_stripped[:120],
                    detail=detail,
                    suggestion="将凭据移到环境变量或密钥管理服务（如 Vault/AWS Secrets Manager）",
                ))

        # SQL 注入
        for pattern, rule_id, rule_name, severity, detail in SQL_INJECTION_PATTERNS:
            if pattern.search(line_stripped):
                violations.append(GovernanceViolation(
                    rule_id=rule_id, rule_name=rule_name, category="security",
                    severity=severity, file_path=cf.file_path, line_number=i,
                    line_content=line_stripped[:120],
                    detail=detail,
                    suggestion="使用参数化查询（如 ? 占位符 + 参数元组）",
                ))

        # 命令注入
        for pattern, rule_id, rule_name, severity, detail in COMMAND_INJECTION_PATTERNS:
            if pattern.search(line_stripped):
                if _is_command_injection_false_positive(line_stripped):
                    continue
                violations.append(GovernanceViolation(
                    rule_id=rule_id, rule_name=rule_name, category="security",
                    severity=severity, file_path=cf.file_path, line_number=i,
                    line_content=line_stripped[:120],
                    detail=detail,
                    suggestion="使用 subprocess.run([cmd, arg1, arg2]) + shlex.quote()",
                ))

        # 不安全反序列化
        if not is_pattern_def:
            for pattern, rule_id, rule_name, severity, detail, suggestion in UNSAFE_DESERIALIZE:
                if pattern.search(line_stripped):
                    violations.append(GovernanceViolation(
                        rule_id=rule_id, rule_name=rule_name, category="security",
                        severity=severity, file_path=cf.file_path, line_number=i,
                        line_content=line_stripped[:120],
                        detail=detail,
                        suggestion=suggestion,
                    ))

        # 硬编码网络地址
        for pattern, rule_id, rule_name, severity, detail in HARDCODED_NETWORK:
            if pattern.search(line_stripped):
                violations.append(GovernanceViolation(
                    rule_id=rule_id, rule_name=rule_name, category="security",
                    severity=severity, file_path=cf.file_path, line_number=i,
                    line_content=line_stripped[:120],
                    detail=detail,
                    suggestion="将 IP 地址移到配置文件或环境变量中",
                ))

        # 危险函数调用（marshal、eval、exec）
        if not is_pattern_def:
            for pattern, rule_id, rule_name, severity, detail, suggestion in DANGEROUS_FUNCTIONS:
                if pattern.search(line_stripped):
                    violations.append(GovernanceViolation(
                        rule_id=rule_id, rule_name=rule_name, category="security",
                        severity=severity, file_path=cf.file_path, line_number=i,
                        line_content=line_stripped[:120],
                        detail=detail,
                        suggestion=suggestion,
                    ))

        # 不安全的子进程调用（shell=True）
        for pattern, rule_id, rule_name, severity, detail, suggestion in UNSAFE_SUBPROCESS:
            if pattern.search(line_stripped):
                violations.append(GovernanceViolation(
                    rule_id=rule_id, rule_name=rule_name, category="security",
                    severity=severity, file_path=cf.file_path, line_number=i,
                    line_content=line_stripped[:120],
                    detail=detail,
                    suggestion=suggestion,
                ))

        # 跨语言系统命令执行（Go/PHP/Java/Node）—— 缺陷 7：非 Python 项目危险命令漏检
        for pattern, rule_id, rule_name, severity, detail, suggestion in CROSS_LANG_COMMAND_EXEC:
            if pattern.search(line_stripped):
                # 缺陷 1：跨语言子规则只对对应语言文件生效，避免
                # platform.system() 等 Python 调用被 PHP system() 规则误报
                if not _cross_lang_rule_applies(rule_name, cf):
                    continue
                violations.append(GovernanceViolation(
                    rule_id=rule_id, rule_name=rule_name, category="security",
                    severity=severity, file_path=cf.file_path, line_number=i,
                    line_content=line_stripped[:120],
                    detail=detail,
                    suggestion=suggestion,
                ))

        # 空鉴权中间件（Go）：func XxxAuth() gin.HandlerFunc { return func(...) { return } }
        # 目标项目 permission.go:9 CasbinAuth() 返回的闭包直接 return，不校验任何权限
        if (not is_pattern_def
                and re.search(r'func\s+\w*[Aa]uth\w*\s*\(\s*\)\s*\w+\.(?:HandlerFunc|MiddlewareFunc)\s*\{',
                              line_stripped)):
            window = "\n".join(lines[i:i + 6])
            if re.search(r'return\s+func\s*\([^)]*\)\s*\{\s*\n\s*return\s*\n\s*\}', window):
                violations.append(GovernanceViolation(
                    rule_id="IRON-SEC-18", rule_name="空鉴权中间件", category="security",
                    severity="critical", file_path=cf.file_path, line_number=i,
                    line_content=line_stripped[:120],
                    detail="鉴权中间件函数返回的闭包直接 return，不执行任何权限校验，可被任意请求绕过鉴权。",
                    suggestion="在闭包内实现真实的权限校验（token 解析/角色检查），并挂载到受保护路由。",
                ))

        # 路径遍历风险
        if not is_pattern_def:
            for pattern, rule_id, rule_name, severity, detail, suggestion in PATH_TRAVERSAL:
                if pattern.search(line_stripped):
                    violations.append(GovernanceViolation(
                        rule_id=rule_id, rule_name=rule_name, category="security",
                        severity=severity, file_path=cf.file_path, line_number=i,
                        line_content=line_stripped[:120],
                        detail=detail,
                        suggestion=suggestion,
                    ))

        # 弱加密算法
        if not is_pattern_def:
            for pattern, rule_id, rule_name, severity, detail, suggestion in WEAK_CRYPTO:
                if pattern.search(line_stripped):
                    violations.append(GovernanceViolation(
                        rule_id=rule_id, rule_name=rule_name, category="security",
                        severity=severity, file_path=cf.file_path, line_number=i,
                        line_content=line_stripped[:120],
                        detail=detail,
                        suggestion=suggestion,
                    ))

        # SSRF
        if not is_pattern_def:
            for pattern, rule_id, rule_name, severity, detail, suggestion in SSRF_PATTERNS:
                if pattern.search(line_stripped):
                    violations.append(GovernanceViolation(
                        rule_id=rule_id, rule_name=rule_name, category="security",
                        severity=severity, file_path=cf.file_path, line_number=i,
                        line_content=line_stripped[:120],
                        detail=detail,
                        suggestion=suggestion,
                    ))

        # XSS
        if not is_pattern_def:
            for pattern, rule_id, rule_name, severity, detail, suggestion in XSS_PATTERNS:
                if pattern.search(line_stripped):
                    eff_severity, eff_detail = severity, detail
                    # 本地静态查看页面降级：命中文件是生成静态 HTML 报告/
                    # 可视化的代码（数据为自产分析产物，无对外攻击面）时
                    # 降为 low，仅提示理论二阶注入；面向用户输入渲染的
                    # Web 服务保持 high
                    if is_static_html_gen:
                        eff_severity = "low"
                        eff_detail = (detail
                                      + "（降级说明：本地静态查看页面，数据为自产"
                                        "分析产物，理论二阶注入）")
                    violations.append(GovernanceViolation(
                        rule_id=rule_id, rule_name=rule_name, category="security",
                        severity=eff_severity, file_path=cf.file_path, line_number=i,
                        line_content=line_stripped[:120],
                        detail=eff_detail,
                        suggestion=suggestion,
                    ))

        # 不安全随机数
        if not is_pattern_def:
            for pattern, rule_id, rule_name, severity, detail, suggestion in INSECURE_RANDOM:
                if pattern.search(line_stripped):
                    violations.append(GovernanceViolation(
                        rule_id=rule_id, rule_name=rule_name, category="security",
                        severity=severity, file_path=cf.file_path, line_number=i,
                        line_content=line_stripped[:120],
                        detail=detail,
                        suggestion=suggestion,
                    ))

        # 弱哈希算法
        if not is_pattern_def:
            for pattern, rule_id, rule_name, severity, detail, suggestion in WEAK_HASH:
                if pattern.search(line_stripped):
                    violations.append(GovernanceViolation(
                        rule_id=rule_id, rule_name=rule_name, category="security",
                        severity=severity, file_path=cf.file_path, line_number=i,
                        line_content=line_stripped[:120],
                        detail=detail,
                        suggestion=suggestion,
                    ))

        # SSL 证书验证禁用
        if not is_pattern_def:
            for pattern, rule_id, rule_name, severity, detail, suggestion in SSL_VERIFY_DISABLED:
                if pattern.search(line_stripped):
                    violations.append(GovernanceViolation(
                        rule_id=rule_id, rule_name=rule_name, category="security",
                        severity=severity, file_path=cf.file_path, line_number=i,
                        line_content=line_stripped[:120],
                        detail=detail,
                        suggestion=suggestion,
                    ))

        # 资源耗尽
        if not is_pattern_def:
            for pattern, rule_id, rule_name, severity, detail, suggestion in RESOURCE_EXHAUSTION:
                if pattern.search(line_stripped):
                    violations.append(GovernanceViolation(
                        rule_id=rule_id, rule_name=rule_name, category="security",
                        severity=severity, file_path=cf.file_path, line_number=i,
                        line_content=line_stripped[:120],
                        detail=detail,
                        suggestion=suggestion,
                    ))

        # 敏感信息泄露（日志/调试输出）
        if not is_pattern_def:
            for pattern, rule_id, rule_name, severity, detail, suggestion in SENSITIVE_LOG:
                if pattern.search(line_stripped):
                    # 过滤：仅包含 "api_key" / "API Key" 作为描述性文字的日志
                    if _is_sensitive_log_false_positive(line_stripped):
                        continue
                    violations.append(GovernanceViolation(
                        rule_id=rule_id, rule_name=rule_name, category="security",
                        severity=severity, file_path=cf.file_path, line_number=i,
                        line_content=line_stripped[:120],
                        detail=detail,
                        suggestion=suggestion,
                    ))

        # 不安全反序列化扩展（json 无异常处理、XML XXE）
        if not is_pattern_def:
            for pattern, rule_id, rule_name, severity, detail, suggestion in UNSAFE_DESERIALIZE_EXTENDED:
                if pattern.search(line_stripped):
                    # json.load/json.loads 跨行 try-except 检测
                    if 'json.load' in line_stripped and _is_json_in_try_block(lines, i - 1):
                        continue
                    violations.append(GovernanceViolation(
                        rule_id=rule_id, rule_name=rule_name, category="security",
                        severity=severity, file_path=cf.file_path, line_number=i,
                        line_content=line_stripped[:120],
                        detail=detail,
                        suggestion=suggestion,
                    ))

    # 过滤 cache 白名单（用户标记为可接受的安全规则命中）
    violations = [
        v for v in violations
        if not SharedFilter.is_security_whitelisted(v.rule_id, v.file_path, v.line_number)
    ]

    return violations


def _check_pitfalls(cf) -> List[GovernanceViolation]:
    """检测错题本模式"""
    violations = []
    lines = cf.raw_content.splitlines() if cf.raw_content else []
    content = cf.raw_content if cf.raw_content else ""

    # 空异常处理 / 裸 except
    for pattern, rule_id, rule_name, severity, detail in PITFALL_EMPTY_EXCEPT:
        for m in pattern.finditer(content):
            lineno = content[:m.start()].count("\n") + 1
            line = lines[lineno - 1] if lineno <= len(lines) else ""
            # 降级：except Exception: continue 在遍历/初始化场景中属于合理容错
            actual_severity = severity
            exc_line = line.strip()
            if rule_id == "PITFALL-01":
                # except <Type>: continue → 降级为 low
                if re.search(r'except\s+\w+\s*:\s*continue', exc_line):
                    actual_severity = "low"
                # except Empty: → 队列超时，降级为 low
                if re.search(r'except\s+Empty\s*:', exc_line):
                    actual_severity = "low"
                # except: return None → 降级为 medium（初始化降级场景）
                if re.search(r'except\s*:\s*return\s+None', exc_line):
                    actual_severity = "medium"
            violations.append(GovernanceViolation(
                rule_id=rule_id, rule_name=rule_name, category="pitfall",
                severity=actual_severity, file_path=cf.file_path, line_number=lineno,
                line_content=line.strip()[:120],
                detail=detail,
                suggestion="至少记录异常日志 logger.exception()，或 re-raise",
                pattern=rule_name,
            ))

    # 可变默认参数
    for pattern, rule_id, rule_name, severity, detail in PITFALL_MUTABLE_DEFAULT:
        for i, line in enumerate(lines, 1):
            if pattern.search(line):
                if line.strip().startswith("#"):
                    continue
                violations.append(GovernanceViolation(
                    rule_id=rule_id, rule_name=rule_name, category="pitfall",
                    severity=severity, file_path=cf.file_path, line_number=i,
                    line_content=line.strip()[:120],
                    detail=detail,
                    suggestion="使用 None 作为默认值，函数内部做 if x is None: x = []",
                    pattern=rule_name,
                ))

    # 不安全的文件操作
    for pattern, rule_id, rule_name, severity, detail in PITFALL_UNSAFE_FILE:
        for i, line in enumerate(lines, 1):
            if pattern.search(line):
                if line.strip().startswith("#"):
                    continue
                violations.append(GovernanceViolation(
                    rule_id=rule_id, rule_name=rule_name, category="pitfall",
                    severity=severity, file_path=cf.file_path, line_number=i,
                    line_content=line.strip()[:120],
                    detail=detail,
                    suggestion="使用 with 语句确保资源正确释放" if rule_id == "PITFALL-03" else "确认有备份机制，操作前做二次确认",
                    pattern=rule_name,
                ))

    # 竞态条件（TOCTOU）
    for pattern, rule_id, rule_name, severity, detail in PITFALL_RACE_CONDITION:
        for m in pattern.finditer(content):
            lineno = content[:m.start()].count("\n") + 1
            line = lines[lineno - 1] if lineno <= len(lines) else ""
            violations.append(GovernanceViolation(
                rule_id=rule_id, rule_name=rule_name, category="pitfall",
                severity=severity, file_path=cf.file_path, line_number=lineno,
                line_content=line.strip()[:120],
                detail=detail,
                suggestion="直接尝试操作并捕获 FileNotFoundError/PermissionError",
                pattern=rule_name,
            ))

    # 裸线程
    for pattern, rule_id, rule_name, severity, detail in PITFALL_THREAD_UNSAFE:
        for i, line in enumerate(lines, 1):
            if pattern.search(line):
                if line.strip().startswith("#"):
                    continue
                violations.append(GovernanceViolation(
                    rule_id=rule_id, rule_name=rule_name, category="pitfall",
                    severity=severity, file_path=cf.file_path, line_number=i,
                    line_content=line.strip()[:120],
                    detail=detail,
                    suggestion="使用 concurrent.futures.ThreadPoolExecutor 管理线程生命周期",
                    pattern=rule_name,
                ))

    # I/O 反模式
    for pattern, rule_id, rule_name, severity, detail in PITFALL_IO_PATTERNS:
        for i, line in enumerate(lines, 1):
            if pattern.search(line):
                if line.strip().startswith("#"):
                    continue
                suggestions = {
                    "PITFALL-07": "对大型文件使用 for line in f: 逐行迭代，避免全部加载到内存",
                    "PITFALL-08": "使用 with open() as f: 确保文件正确关闭",
                }
                violations.append(GovernanceViolation(
                    rule_id=rule_id, rule_name=rule_name, category="pitfall",
                    severity=severity, file_path=cf.file_path, line_number=i,
                    line_content=line.strip()[:120],
                    detail=detail,
                    suggestion=suggestions.get(rule_id, "优化 I/O 操作模式"),
                    pattern=rule_name,
                ))

    return violations


def _check_async_blocking(cf) -> List[GovernanceViolation]:
    """使用 AST 检测异步函数中的同步阻塞调用"""
    violations = []
    if cf.language not in ("Python", "python"):
        return violations
    if not cf.file_path.endswith(".py"):
        return violations
    try:
        import ast
        with open(cf.file_path, "r", encoding="utf-8", errors="ignore") as f:
            source = f.read()
        tree = ast.parse(source)
    except (SyntaxError, OSError):
        return violations

    for node in ast.walk(tree):
        if not isinstance(node, ast.AsyncFunctionDef):
            continue

        # 查找 async 函数体内的同步阻塞调用
        for child in ast.walk(node):
            if isinstance(child, ast.Call):
                func_str = _get_call_string(child)
                if func_str in BLOCKING_CALLS:
                    violations.append(GovernanceViolation(
                        rule_id="PITFALL-09", rule_name="异步函数中同步阻塞",
                        category="pitfall", severity="high",
                        file_path=cf.file_path, line_number=child.lineno,
                        line_content=ast.unparse(child)[:120],
                        detail=f"在 async 函数 {node.name}() 中调用了同步阻塞操作 {func_str}，会阻塞事件循环",
                        suggestion=f"使用异步替代：{func_str.replace('requests.', 'httpx.AsyncClient.').replace('time.sleep', 'asyncio.sleep')}",
                        pattern="异步函数中同步阻塞",
                    ))
    return violations


def _check_quality(cf) -> List[GovernanceViolation]:
    """检测质量铁律违规"""
    violations = []
    lines = cf.raw_content.splitlines() if cf.raw_content else []

    # 函数过长
    for func in cf.functions:
        func_lines = func.end_line - func.start_line + 1
        if func_lines > FUNCTION_TOO_LONG:
            violations.append(GovernanceViolation(
                rule_id="IRON-QUAL-01", rule_name="函数过长",
                category="quality", severity="medium",
                file_path=cf.file_path, line_number=func.start_line,
                line_content=f"{func.name}() — {func_lines}行",
                detail=f"函数 {func.name}() 有 {func_lines} 行，超过 {FUNCTION_TOO_LONG} 行阈值。过长函数难以理解和测试。",
                suggestion=f"拆分为多个职责单一的小函数，每个函数不超过 {FUNCTION_TOO_LONG} 行",
            ))

        # 参数过多
        param_count = len(func.parameters) if func.parameters else 0
        if param_count > FUNCTION_TOO_MANY_PARAMS:
            violations.append(GovernanceViolation(
                rule_id="IRON-QUAL-02", rule_name="参数过多",
                category="quality", severity="medium",
                file_path=cf.file_path, line_number=func.start_line,
                line_content=f"{func.name}({param_count}个参数)",
                detail=f"函数 {func.name}() 有 {param_count} 个参数，超过 {FUNCTION_TOO_MANY_PARAMS} 个阈值。过多参数降低可读性。",
                suggestion="使用数据类/配置对象封装相关参数，或拆分为多个方法",
            ))

    # 嵌套过深
    for i, line in enumerate(lines, 1):
        stripped = line.rstrip()
        indent = len(line) - len(stripped)
        # 缩进级别（4空格=1级）
        indent_level = indent // 4
        if indent_level > NESTING_TOO_DEEP and stripped and not stripped.startswith(("#", "//", "/*", "*", "'''", '"""')):
            # 检查是否是真正的嵌套（不是对齐的续行）
            if stripped[0] not in (")", "}", "]", ")", ".", ","):
                violations.append(GovernanceViolation(
                    rule_id="IRON-QUAL-03", rule_name="嵌套过深",
                    category="quality", severity="low",
                    file_path=cf.file_path, line_number=i,
                    line_content=stripped[:120],
                    detail=f"嵌套深度 {indent_level} 层，超过 {NESTING_TOO_DEEP} 层阈值。深层嵌套降低可读性。",
                    suggestion="使用 early return / guard clause 减少嵌套，或提取为子函数",
                ))
                # 只报告每个文件的第一处过深嵌套
                break

    return violations


def _whitelist_exclude_dirs(project_path: str) -> List[str]:
    """读取 whitelist 中的 dir 条目（目录级排除，备份/镜像目录不污染治理审计）。

    与 arch_gap_analyzer._whitelist_exclude_dirs 同源；延迟 import pipeline_runner
    避免模块级循环依赖。
    """
    try:
        from core.pipeline_runner import whitelist_list
        return [e["dir"] for e in whitelist_list(project_path) if e.get("dir")]
    except Exception:  # pragma: no cover
        return []


def _is_wl_excluded(path: str, project_path: str, exclude_dirs: List[str]) -> bool:
    """判断 path（目录/文件）是否位于 whitelist 排除目录下（复用图谱排除口径）。"""
    if not exclude_dirs:
        return False
    from core.code_knowledge_graph import _is_excluded_path
    return _is_excluded_path(path, project_path, exclude_dirs)


def _apply_whitelist_exclude(analysis, project_path, wl_exclude=None):
    """把 whitelist 的 dir 目录排除作用于规则层审计的代码文件集。

    与 _scan_non_py_secrets / _scan_doc_secrets 复用同一 _whitelist_exclude_dirs
    排除口径；备份/镜像目录不参与 PITFALL/security/quality/architecture 等
    编程规则审计，根治「图谱干净 ≠ 审计干净」的机制割裂。同步重算
    total_files / total_lines，保证报告「扫描范围」与审计结果口径一致。

    wl_exclude 为可选的排除目录列表注入（测试用）；缺省为 None 时自行读取。
    """
    if wl_exclude is None:
        wl_exclude = _whitelist_exclude_dirs(project_path)
    if not wl_exclude:
        return analysis
    kept = []
    for cf in analysis.files:
        if not _is_wl_excluded(cf.file_path, project_path, wl_exclude):
            kept.append(cf)
    analysis.files = kept
    analysis.total_files = len(kept)
    analysis.total_lines = sum(
        (cf.raw_content.count("\n") + 1) if cf.raw_content else 0
        for cf in kept
    )
    return analysis


def _scan_non_py_secrets(project_path: str) -> List[GovernanceViolation]:
    """扫描非 Python 文件中的机密（.env/.yaml/.json/.toml）"""
    violations = []
    wl_exclude = _whitelist_exclude_dirs(project_path)
    for root, dirs, files in os.walk(project_path):
        dirs[:] = [d for d in dirs
                   if not d.startswith(".")
                   and not _is_wl_excluded(os.path.join(root, d), project_path, wl_exclude)
                   and d not in (
            "__pycache__", "node_modules", ".git", "venv", ".venv", "data",
            "third_party", ".gitnexus", "docs", "reports",
        )]
        for f in files:
            ext = os.path.splitext(f)[1].lower()
            if ext not in NON_PY_SECRET_EXTENSIONS:
                continue
            fpath = os.path.join(root, f)
            try:
                with open(fpath, "r", encoding="utf-8", errors="ignore") as fh:
                    for lineno, line in enumerate(fh, 1):
                        m = NON_PY_SECRET_MASK_PATTERN.search(line)
                        if not m:
                            continue
                        value = m.group(1)
                        # 跳过占位符/示例值
                        if value.lower() in ("placeholder", "your_key", "your_token", "xxx", "example",
                                              "changeme", "replace_me", "your_api_key", "your_secret"):
                            continue
                        # 跳过空值/variable ref
                        if re.match(r'^[\$\{].*[\}\}]$', value):
                            continue
                        violations.append(GovernanceViolation(
                            rule_id="IRON-SEC-01", rule_name="硬编码凭据",
                            category="security", severity="critical",
                            file_path=fpath, line_number=lineno,
                            line_content=line.strip()[:120],
                            detail=f"非代码文件中发现硬编码凭据：{m.group(0)[:60]}",
                            suggestion="将凭据移到环境变量中，或使用 .env.example 作为模板",
                        ))
            except (OSError, IOError, UnicodeDecodeError):
                pass
    return violations


def _scan_doc_secrets(project_path: str) -> List[GovernanceViolation]:
    """
    扫描文档类文件（.md/.SKILL/.txt）中的治理合规缺陷：
    1. 明文密钥环境变量赋值（如 SN_API_KEY=xxx）
    2. 引导/示范把密钥写入凭据配置文件（.env 等）
    3. 审查绕过类表述（忽略清单/白名单/直接放行/一律不输出）
    """
    violations = []
    wl_exclude = _whitelist_exclude_dirs(project_path)
    for root, dirs, files in os.walk(project_path):
        dirs[:] = [d for d in dirs
                   if not d.startswith(".")
                   and not _is_wl_excluded(os.path.join(root, d), project_path, wl_exclude)
                   and d not in (
            "__pycache__", "node_modules", ".git", "venv", ".venv", "data",
            "third_party", ".gitnexus", "docs", "reports",
        )]
        for f in files:
            ext = os.path.splitext(f)[1].lower()
            if ext not in DOC_EXTENSIONS:
                continue
            fpath = os.path.join(root, f)
            try:
                with open(fpath, "r", encoding="utf-8", errors="ignore") as fh:
                    for lineno, line in enumerate(fh, 1):
                        for pattern, rule_id, rule_name, severity, detail in DOC_SECRET_PATTERNS:
                            if pattern.search(line):
                                # 占位符/示例配置（=your_key_here）=<your_key> 等不报
                                if _doc_placeholder(line):
                                    continue
                                violations.append(_doc_violation(
                                    rule_id, rule_name, severity, detail, fpath, lineno, line))
                        for pattern, rule_id, rule_name, severity, detail in DOC_ENV_CRED_PATTERNS:
                            if pattern.search(line):
                                if _doc_placeholder(line):
                                    continue
                                violations.append(_doc_violation(
                                    rule_id, rule_name, severity, detail, fpath, lineno, line))
                        for pattern, rule_name, severity, detail in GOV_BYPASS_PATTERNS:
                            if pattern.search(line):
                                violations.append(_doc_violation(
                                    "IRON-GOV-01", rule_name, severity, detail, fpath, lineno, line))
                        for pattern, rule_id, rule_name, severity, detail in DOC_CLAIM_PATTERNS:
                            if pattern.search(line):
                                violations.append(_doc_violation(
                                    rule_id, rule_name, severity, detail, fpath, lineno, line))
            except (OSError, IOError, UnicodeDecodeError):
                pass
    return violations


def _scan_crossregion_conflicts(project_path: str) -> List[GovernanceViolation]:
    """跨地区合规条款矛盾检测（IRON-GOV-03/04）：
    1. 领土主权表述冲突：一个地区规则文件把某地区视为独立主权主体（如"台湾被视为
       他国"、"标注台湾为主体"），另一地区文件/SKILL 却禁止领土主权争议，同一审查
       工具给出互相矛盾的领土主权立场。
    2. 版本差异污染：同一文档内多套执法/统计数据并存且年份、口径（互联网 vs 全部、
       查处 vs 查办）混杂、无版本标注，引用时易误导审查置信度。
    """
    violations = []
    doc_files = []
    wl_exclude = _whitelist_exclude_dirs(project_path)
    for root, dirs, files in os.walk(project_path):
        dirs[:] = [d for d in dirs
                   if not d.startswith(".")
                   and not _is_wl_excluded(os.path.join(root, d), project_path, wl_exclude)
                   and d not in (
            "__pycache__", "node_modules", ".git", "venv", ".venv", "data",
            "third_party", ".gitnexus", "reports",
        )]
        for f in files:
            if os.path.splitext(f)[1].lower() in DOC_EXTENSIONS:
                doc_files.append(os.path.join(root, f))

    # 1. 领土主权表述冲突（跨文件对比）
    sovereignty_claims = []    # 独立主体表述
    sovereignty_redlines = []  # 领土主权红线
    for fpath in doc_files:
        try:
            with open(fpath, "r", encoding="utf-8", errors="ignore") as fh:
                for lineno, line in enumerate(fh, 1):
                    if re.search(r'被视为他国|标注.{0,8}为主体|视为独立.{0,6}(主权|国家)|独立主权', line):
                        # 排除禁止/否定/示例/假设语境：如"禁止标注台湾为主体""不得将其视为
                        # 他国""例如标注台湾为主体"，这些是合规红线/政策举例，并非本文件实际
                        # 主张的独立主体立场；误归入 sovereignty_claims 会与同文件红线自相
                        # 矛盾，对同一合规文档产生 IRON-GOV-03 误报。
                        banned = re.search(
                            r'(禁止|严禁|不得|不应|不能|避免|切勿|诸如|例如|比如|示例|举例|'
                            r'假设|假如|若|如果|不应.{0,6}认为|不可).{0,16}'
                            r'(被视为他国|标注.{0,8}为主体|视为独立.{0,6}(主权|国家)|独立主权)',
                            line)
                        if not banned:
                            sovereignty_claims.append((fpath, lineno, line.strip()))
                    if re.search(r'禁止涉及领土主权|领土主权.{0,8}(红线|争议)|不得.{0,8}主权|主权.{0,4}红线', line):
                        sovereignty_redlines.append((fpath, lineno, line.strip()))
        except (OSError, IOError, UnicodeDecodeError):
            pass
    if sovereignty_claims and sovereignty_redlines:
        for fpath, lineno, line in sovereignty_claims:
            redline_desc = "; ".join(
                f"{os.path.relpath(rf, project_path)}:{rl}"
                for rf, rl, _ in sovereignty_redlines[:3])
            violations.append(GovernanceViolation(
                rule_id="IRON-GOV-03", rule_name="跨地区领土主权表述冲突",
                category="security", severity="high",
                file_path=fpath, line_number=lineno,
                line_content=line[:120],
                detail=(f"本文件将某地区表述为独立主权主体，与项目内领土主权红线"
                        f"（{redline_desc}）直接冲突，同一审查工具给出互相矛盾的领土主权立场"),
                suggestion="统一领土主权立场：任何地区均为中国领土不可分割的一部分，删除独立主体表述",
            ))

    # 2. 版本差异污染（单文件多套统计并存）
    # 仅当并存的多套统计均缺乏来源/范围/版本标注时才报违规。年度趋势报告常含多个年份，
    # 但每个年份通常带明确来源（"数据来源：XX"）、范围（"互联网领域"）与时间/版本标签
    # （"2022年全年""截至2023年底"），属合规文档而非版本污染；裸统计（无任何元数据）
    # 并存才构成污染，避免对合规趋势报告产生 IRON-GOV-04 误报。
    stat_re = re.compile(
        r'20\d{2}年(?:前[一二三]季度|全年|上半年|下半年)?[^。\n]{0,40}?'
        r'(\d+(?:\.\d+)?万?件)')
    stat_meta_re = re.compile(
        r'(数据来源|来源|据\S*|统计|报告|公报|发布|口径|范围|领域|行业|'
        r'全年|季度|上半年|下半年|截至|同比|环比|年均|全国|全区|全网|年度)')
    for fpath in doc_files:
        try:
            with open(fpath, "r", encoding="utf-8", errors="ignore") as fh:
                content = fh.read()
        except (OSError, IOError, UnicodeDecodeError):
            continue
        matches = list(stat_re.finditer(content))
        if len(matches) < 2:
            continue
        years = {m.group(0)[:4] for m in matches}
        if len(years) < 2:
            continue
        # 检测并存统计是否带来源/范围/版本标注：任一匹配附近（前后 40 字符窗口）含
        # 元数据词，即视为有明确来源口径的合规趋势数据，跳过不报违规。
        if any(stat_meta_re.search(content[max(0, m.start() - 40):m.end() + 40])
               for m in matches):
            continue
        first_line = content[:matches[0].start()].count("\n") + 1
        stats_desc = "; ".join(
            f"L{content[:m.start()].count(chr(10)) + 1}:{m.group(0).strip()}"
            for m in matches[:3])
        violations.append(GovernanceViolation(
            rule_id="IRON-GOV-04", rule_name="版本差异污染（统计数据多版本并存）",
            category="quality", severity="medium",
            file_path=fpath, line_number=first_line,
            line_content=matches[0].group(0).strip()[:120],
            detail=(f"同一文档并存 {len(matches)} 套执法/统计数据（{stats_desc}），"
                    f"年份/口径（互联网 vs 全部、查处 vs 查办）混杂且无版本标注，"
                    f"引用时易误导审查置信度"),
            suggestion="统一统计口径与时间版本，标注数据来源与统计范围，仅保留单一权威版本",
        ))
    return violations


def _check_secret_persistence(cf) -> List[GovernanceViolation]:
    """检测代码中把密钥明文落盘到 .env 等凭据文件（如 def save_env 写入 API_KEY）"""
    violations = []
    lines = cf.raw_content.splitlines() if cf.raw_content else []
    total = len(lines)

    # 1) 行级 f-string 密钥赋值落盘（如 f"BRAND_LLM_API_KEY={self.llm.api_key}"）
    for i, line in enumerate(lines, 1):
        ls = line.strip()
        if ls.startswith("#") or ls.startswith("//"):
            continue
        # 跳过检测器自身正则规则定义行（SECRET_PERSIST_PATTERNS 的 re.compile(...)），
        # 避免审计 Coderef 自身时把规则定义当真实落盘误报。真实落盘代码不含 re.compile(。
        if "re.compile(" in ls:
            continue
        for pattern, rule_id, rule_name, severity, detail in SECRET_PERSIST_PATTERNS:
            if pattern.search(ls):
                violations.append(GovernanceViolation(
                    rule_id=rule_id, rule_name=rule_name, category="security",
                    severity=severity, file_path=cf.file_path, line_number=i,
                    line_content=ls[:120], detail=detail,
                    suggestion="改用密钥管理服务或加密存储，避免 API Key 明文落盘到 .env 等凭据文件",
                ))

    # 2) 方法级：save_env/write_env 类方法，且方法体内含密钥变量 + .env 写入
    for i, line in enumerate(lines):
        if SECRET_PERSIST_FUNC.search(line):
            body = [line]
            base_indent = len(line) - len(line.lstrip())
            j = i + 1
            while j < total:
                nxt = lines[j]
                # 仅当 def/class 与原方法同级（缩进深度一致）时才视为方法体结束；
                # 嵌套在方法内的子定义（缩进更深）仍属于本方法体，继续保留，
                # 以免内部的 .env 写入被提前截断漏检。
                if (re.match(r'^\s*(?:def|class)\s+', nxt)
                        and len(nxt) - len(nxt.lstrip()) == base_indent):
                    break
                body.append(nxt)
                j += 1
            bodytext = "\n".join(body)
            if re.search(r'\b(?:api_key|API_KEY|secret|token|SECRET_KEY)\b', bodytext) and \
               re.search(r'\.env\b|write_text|\.write\s*\(', bodytext):
                violations.append(GovernanceViolation(
                    rule_id="IRON-SEC-01", rule_name="密钥明文落盘",
                    category="security", severity="critical",
                    file_path=cf.file_path, line_number=i + 1,
                    line_content=line.strip()[:120],
                    detail=f"方法 {line.strip()[:40]} 将 API Key 明文写入 .env 凭据文件，属明文凭据落盘。",
                    suggestion="改用密钥管理服务或加密存储，避免 API Key 明文落盘到 .env",
                ))
    return violations


def _check_architecture(analysis) -> List[GovernanceViolation]:
    """检测架构铁律违规"""
    violations = []

    # 构建模块依赖图
    deps = defaultdict(set)  # file -> set of imported files
    modules = {}  # file -> layer

    for cf in analysis.files:
        # 传递代码内容以支持 LLM 驱动的层级分类
        raw_content = cf.raw_content if cf.raw_content else ""
        layer = _classify_layer(cf.file_path, code_content=raw_content[:2000])
        modules[cf.file_path] = layer

        # 提取导入
        flines = cf.raw_content.splitlines() if cf.raw_content else []
        for line in flines:
            imp = _extract_import(line)
            if imp:
                deps[cf.file_path].add(imp)

    # 检查层级穿透
    for file_path, layer in modules.items():
        for dep in deps.get(file_path, set()):
            # 尝试匹配导入路径
            matched = _match_import_to_file(dep, modules.keys())
            for target_file in matched:
                target_layer = modules.get(target_file, "other")
                if target_layer == layer:
                    continue
                allowed = LAYER_RULES.get(layer, ["other"])
                if target_layer not in allowed and target_layer != "other":
                    violations.append(GovernanceViolation(
                        rule_id="IRON-ARCH-01", rule_name="层级穿透",
                        category="architecture", severity="high",
                        file_path=file_path, line_number=0,
                        line_content=f"{layer} → {target_layer} ({dep})",
                        detail=f"{layer} 层直接依赖了 {target_layer} 层，违反了依赖方向规则。{layer} 层只能依赖: {allowed}",
                        suggestion=f"将 {target_layer} 层的逻辑下沉到 {layer} 允许依赖的层级，或通过接口反转依赖",
                    ))

    # 检查循环依赖（简单检测：A imports B and B imports A）
    checked = set()
    for a_file, a_deps in deps.items():
        for b_file in a_deps:
            pair = tuple(sorted([a_file, b_file]))
            if pair in checked:
                continue
            checked.add(pair)
            b_deps = deps.get(b_file, set())
            if a_file in b_deps:
                violations.append(GovernanceViolation(
                    rule_id="IRON-ARCH-02", rule_name="循环依赖",
                    category="architecture", severity="high",
                    file_path=a_file, line_number=0,
                    line_content=f"{os.path.basename(a_file)} ↔ {os.path.basename(b_file)}",
                    detail=f"检测到循环依赖：{os.path.basename(a_file)} 和 {os.path.basename(b_file)} 互相导入",
                    suggestion="提取共同依赖到第三个模块，或使用依赖注入打破循环",
                ))

    return violations


def _generate_report(report: GovernanceReport) -> str:
    """生成 Markdown 治理审计报告"""
    # 按严重程度排序
    violations = sorted(report.violations,
                        key=lambda v: (SEVERITY_ORDER.get(v.severity, 9), v.file_path, v.line_number))

    # 分类统计
    cat_counts = defaultdict(int)
    sev_counts = defaultdict(int)
    for v in violations:
        cat_counts[v.category] += 1
        sev_counts[v.severity] += 1

    # 健康分颜色（无代码文件时 clean_score 为 None → 显示 N/A）
    if report.clean_score is not None:
        if report.clean_score >= 80:
            score_emoji = "🟢"
        elif report.clean_score >= 50:
            score_emoji = "🟡"
        else:
            score_emoji = "🔴"
        score_text = f"{score_emoji} **{report.clean_score}/100**"
    else:
        score_text = "⚪ **N/A**（未发现可分析代码文件）"

    lines = []
    lines.append("# 🔍 代码治理审计报告")
    lines.append("")
    lines.append(f"**项目路径**: `{report.project_path}`  ")
    lines.append(f"**扫描范围**: {report.total_files} 个文件, {report.total_lines} 行  ")
    lines.append(f"**治理健康分**: {score_text}  ")
    lines.append("")

    # 总览
    lines.append("## 📊 违规总览")
    lines.append("")
    lines.append("| 严重程度 | 数量 |")
    lines.append("|---------|------|")
    lines.append(f"| 🔴 Critical | {report.critical_count} |")
    lines.append(f"| 🟠 High | {report.high_count} |")
    lines.append(f"| 🟡 Medium | {report.medium_count} |")
    lines.append(f"| ⚪ Low | {report.low_count} |")
    lines.append(f"| **总计** | **{report.total_violations}** |")
    lines.append("")

    lines.append("| 分类 | 数量 |")
    lines.append("|------|------|")
    for cat in ["security", "architecture", "pitfall", "quality"]:
        if cat_counts.get(cat, 0) > 0:
            cat_names = {"security": "安全铁律", "architecture": "架构铁律", "pitfall": "错题本模式", "quality": "质量铁律"}
            lines.append(f"| {cat_names.get(cat, cat)} | {cat_counts[cat]} |")
    lines.append("")

    if not violations:
        lines.append("✅ **未发现违规项，项目治理状况良好。**")
        return "\n".join(lines)

    # 分类明细
    for cat in ["security", "architecture", "pitfall", "quality"]:
        cat_violations = [v for v in violations if v.category == cat]
        if not cat_violations:
            continue

        cat_names = {
            "security": "🛡️ 安全铁律",
            "architecture": "🏗️ 架构铁律",
            "pitfall": "📋 错题本模式",
            "quality": "📏 质量铁律",
        }

        lines.append(f"## {cat_names.get(cat, cat)}（{len(cat_violations)} 项）")
        lines.append("")

        # 每类最多展示 100 条，避免报告过大
        display_count = min(len(cat_violations), 100)
        for v in cat_violations[:display_count]:
            sev_icon = {"critical": "🔴", "high": "🟠", "medium": "🟡", "low": "⚪"}.get(v.severity, "⚪")
            lines.append(f"### {sev_icon} [{v.rule_id}] {v.rule_name}")
            lines.append("")
            lines.append(f"- **文件**: `{v.file_path}`")
            if v.line_number > 0:
                lines.append(f"- **行号**: L{v.line_number}")
            lines.append(f"- **代码**: `{v.line_content}`")
            lines.append(f"- **说明**: {v.detail}")
            lines.append(f"- **建议**: {v.suggestion}")
            if v.pattern:
                lines.append(f"- **错题本模式**: {v.pattern}")
            lines.append("")

        if len(cat_violations) > display_count:
            lines.append(f"*... 还有 {len(cat_violations) - display_count} 条未展示，请优化匹配规则后重新扫描*")
            lines.append("")

    # 修复优先级建议
    lines.append("---")
    lines.append("")
    lines.append("## 🎯 修复优先级建议")
    lines.append("")
    if report.critical_count > 0:
        lines.append(f"1. **立即修复** ({report.critical_count} 项 critical): 硬编码凭据、SQL注入等安全漏洞必须立即处理")
    if report.high_count > 0:
        lines.append(f"2. **本周修复** ({report.high_count} 项 high): 命令注入、空异常处理、层级穿透、循环依赖")
    if report.medium_count > 0:
        lines.append(f"3. **下个迭代** ({report.medium_count} 项 medium): 函数过长、参数过多、可变默认参数")
    if report.low_count > 0:
        lines.append(f"4. **技术债务** ({report.low_count} 项 low): 嵌套过深、裸线程等可逐步优化")

    lines.append("")
    lines.append("---")
    lines.append("*报告由 CodeRef-AI GovernanceAuditor v1.0 生成*")
    lines.append("")
    lines.append("> **LLM 复查提示**：此报告可能包含误报。审查每条结果后，调用 `coderef_cache_whitelist` 工具将误报自动加入白名单，然后重新扫描。")

    return "\n".join(lines)


