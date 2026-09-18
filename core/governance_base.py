# -*- coding: utf-8 -*-
"""
治理规则常量与数据模型 —— 自 governance_audit 拆出的底层模块（god-module-split 试点）

承载治理审计的全部规则常量、模式表与数据模型，供 governance_checks
（检测函数）与 governance_audit（门面类）引用。本模块不依赖其他 core 模块，
常量别名（FUNCTION_TOO_LONG 等）保持自 config.settings 的 import + 别名赋值原样。
"""

import re
from typing import List, Optional
from dataclasses import dataclass, field

from config.settings import (
    GOVERNANCE_FUNCTION_TOO_LONG,
    GOVERNANCE_FUNCTION_TOO_MANY_PARAMS,
    GOVERNANCE_NESTING_TOO_DEEP,
)


SEVERITY_ORDER = {"blocker": 0, "critical": 1, "high": 2, "medium": 3, "low": 4, "info": 5}

# CWE 映射表：将 IRON 规则映射到 CWE 编号
CWE_MAPPING = {
    "IRON-SEC-01": "CWE-798",   # 硬编码凭据
    "IRON-SEC-02": "CWE-89",    # SQL注入
    "IRON-SEC-03": "CWE-78",    # 命令注入
    "IRON-SEC-04": "CWE-502",   # 不安全反序列化
    "IRON-SEC-05": "CWE-547",   # 硬编码地址
    "IRON-SEC-06": "CWE-95",    # 危险函数调用
    "IRON-SEC-07": "CWE-78",    # 不安全子进程
    "IRON-SEC-08": "CWE-22",    # 路径遍历
    "IRON-SEC-09": "CWE-532",   # 敏感信息泄露
    "IRON-SEC-10": "CWE-326",   # 弱加密算法
    "IRON-SEC-11": "CWE-918",   # SSRF
    "IRON-SEC-12": "CWE-79",    # XSS
    "IRON-SEC-13": "CWE-330",   # 不安全随机数
    "IRON-SEC-14": "CWE-327",   # 弱哈希算法
    "IRON-SEC-15": "CWE-295",   # 证书验证禁用
    "IRON-SEC-16": "CWE-400",   # 资源耗尽
}

# OWASP Top 10 2021 映射
OWASP_MAPPING = {
    "IRON-SEC-01": "A07:2021",  # 身份识别和认证失败
    "IRON-SEC-02": "A03:2021",  # 注入
    "IRON-SEC-03": "A03:2021",  # 注入
    "IRON-SEC-04": "A08:2021",  # 软件和数据完整性故障
    "IRON-SEC-06": "A03:2021",  # 注入
    "IRON-SEC-07": "A03:2021",  # 注入
    "IRON-SEC-08": "A01:2021",  # 访问控制失效
    "IRON-SEC-09": "A09:2021",  # 安全日志和监控故障
    "IRON-SEC-10": "A02:2021",  # 加密失败
    "IRON-SEC-11": "A10:2021",  # SSRF
    "IRON-SEC-12": "A03:2021",  # 注入
    "IRON-SEC-13": "A02:2021",  # 加密失败
    "IRON-SEC-14": "A02:2021",  # 加密失败
    "IRON-SEC-15": "A02:2021",  # 加密失败
    "IRON-SEC-16": "A05:2021",  # 安全配置错误
}


@dataclass
class GovernanceViolation:
    """治理违规项"""
    rule_id: str           # 规则编号（如 IRON-01）
    rule_name: str         # 规则名称
    category: str          # 分类：architecture / change / quality / security / pitfall
    severity: str          # critical / high / medium / low
    file_path: str         # 违规文件
    line_number: int       # 行号
    line_content: str      # 违规行内容
    detail: str            # 详细说明
    suggestion: str        # 修复建议
    pattern: str = ""      # 匹配到的错题本模式名


@dataclass
class GovernanceReport:
    """治理审计报告"""
    project_path: str
    total_files: int
    total_lines: int
    violations: List[GovernanceViolation] = field(default_factory=list)

    @property
    def blocker_count(self) -> int:
        return sum(1 for v in self.violations if v.severity == "blocker")

    @property
    def critical_count(self) -> int:
        return sum(1 for v in self.violations if v.severity == "critical")

    @property
    def high_count(self) -> int:
        return sum(1 for v in self.violations if v.severity == "high")

    @property
    def medium_count(self) -> int:
        return sum(1 for v in self.violations if v.severity == "medium")

    @property
    def low_count(self) -> int:
        return sum(1 for v in self.violations if v.severity == "low")

    @property
    def total_violations(self) -> int:
        return len(self.violations)

    @property
    def clean_score(self) -> Optional[int]:
        """治理健康分（0-100）。无代码文件时返回 None（N/A），
        避免"空项目健康分=100"的静默满分误导（证据审计缺陷 9）。"""
        if self.total_files == 0:
            return None
        penalty = self.blocker_count * 25 + self.critical_count * 15 + self.high_count * 5 + self.medium_count * 2 + self.low_count * 0.5
        return max(0, min(100, 100 - int(penalty / max(self.total_files, 1) * 10)))


# ═══════════════════════════════════════════════════════════════════
# 治理审计器
# ═══════════════════════════════════════════════════════════════════

# ==================== 模块级规则常量区（自 GovernanceAuditor 提取，规则数据与检测逻辑分离） ====================
# ─── 安全铁律：高危模式 ────────────────────────────────────────

# 共享的凭据字面量值提取器：从已命中的凭据声明行提取引号内字面量值。
# 供 HARDCODED_SECRET_PATTERNS 命中后的占位符过滤复用，保证取值逻辑唯一。
CREDENTIAL_VALUE = re.compile(r'[:=]\s*([`"\'])([^`"\']{8,})[`"\']')

# 硬编码密钥/密码/Token（使用 \b 单词边界防止子串误匹配）
HARDCODED_SECRET_PATTERNS = [
    # 只匹配字面值（引号内的值），不匹配变量引用/函数调用
    (re.compile(r'\b(?:password|passwd|pwd|secret|token|api_key|apikey|access_key|private_key)\b\s*(?::=|=)\s*["\'][^"\']{8,}["\']', re.IGNORECASE),
     "IRON-SEC-01", "硬编码凭据", "critical",
     "代码中直接写入了密码/Token/密钥等敏感凭据，应改用环境变量或密钥管理服务"),
    # 自定义密钥常量：变量名含 Key/Secret/Token/Credential/Passwd/Pwd 词尾，值为 16+ 字符随机串
    # （目标项目 code_run.go:5 const CodeRunKey = `QwXKBkHZRJ4StIYr06hvUOLVu9AemFa2`）
    # 词尾后 (?!\w) 防止 publicKeyHash 类把 Key 当普通词中片段；:= 支持 Go 短声明。
    # 占位符/规则串（纯字母/纯数字/重复字符）由 _is_credential_false_positive 排除。
    # 不加 IGNORECASE：规则依赖词尾大写（CodeRunKey/apiSecret），
    # 大小写不敏感会把 cache_key 等普通标识符误报为 critical。
    (re.compile(r'\b(?:const\s+)?[A-Za-z_]\w*(?:Key|Secret|Token|Credential|Passwd|Pwd)(?!\w)\s*(?::=|=)\s*[`"\']([^`"\']{16,})[`"\']'),
     "IRON-SEC-01", "硬编码凭据", "critical",
     "代码中直接写入了自定义密钥常量（变量名含 Key/Secret/Token 等，值为长随机串），应改用环境变量或密钥管理服务"),
]

# 凭据检测的排除模式（匹配后仍需要二次验证）
# 这些模式指示变量引用而非字面值，应排除
SECRET_EXCLUDE_PATTERNS = [
    # 函数调用：token = create_access_token(...) 或 token = getToken()
    re.compile(r'[:=]\s*\w+\s*\(', re.IGNORECASE),
    # 对象属性访问：self.config.xxx, self.xxx
    re.compile(r'self\.\w+', re.IGNORECASE),
    # 配置字典访问：config["key"], config.get("key")
    re.compile(r'(?:config|settings|cfg)\s*\[', re.IGNORECASE),
    re.compile(r'(?:config|settings|cfg)\.get\s*\(', re.IGNORECASE),
    # 环境变量：os.environ / os.getenv / process.env
    re.compile(r'os\.(?:environ|getenv)', re.IGNORECASE),
    re.compile(r'process\.env', re.IGNORECASE),
    # .env 文件引用
    re.compile(r'\.env\b', re.IGNORECASE),
    # 注释（Python # 或 JS //）
    re.compile(r'^\s*#', re.IGNORECASE),
    re.compile(r'^\s*//', re.IGNORECASE),
    # 错误码常量：E1001_KEY = "E1001_KEY"（全大写约定，区分大小写避免误排小写/复合凭据名）
    re.compile(r'^[A-Z_]{4,}\s*=\s*["\'][A-Z_\d]+["\']'),
    # 错误码/错误常量名（全大写约定；E 前缀错误码加词边界，避免误匹配值内数字串）
    re.compile(r'MISSING_|_MISSING|ERROR_|\bE\d{3,}'),
    # 从 localStorage/sessionStorage 获取（JS/TS）
    re.compile(r'(?:localStorage|sessionStorage)\.(?:get|getItem)', re.IGNORECASE),
    # 函数返回值赋值：token = get_xxx() 或 const token = getXxx()
    re.compile(r'=\s*\w+\s*\(\s*\)', re.IGNORECASE),
]

# SQL 注入风险
SQL_INJECTION_PATTERNS = [
    (re.compile(r'(?:execute|cursor\.execute|\.raw)\s*\(\s*(?:f["\']|["\'].*%.*["\'])', re.IGNORECASE),
     "IRON-SEC-02", "SQL注入风险", "critical",
     "使用字符串拼接/f-string构造SQL，存在SQL注入风险。应使用参数化查询"),
    (re.compile(r'(?:execute|cursor\.execute)\s*\(\s*["\'].*\{\s*\}.*["\']', re.IGNORECASE),
     "IRON-SEC-02", "SQL注入风险", "critical",
     "使用字符串格式化构造SQL，存在SQL注入风险。应使用参数化查询"),
]

# 命令注入风险
COMMAND_INJECTION_PATTERNS = [
    (re.compile(r'(?:os\.system|subprocess\.call|subprocess\.Popen|eval|exec)\s*\(\s*.*\+', re.IGNORECASE),
     "IRON-SEC-03", "命令注入风险", "high",
     "使用字符串拼接构造系统命令，存在命令注入风险。应使用subprocess.run(list) + shlex.quote()"),
    (re.compile(r'(?:os\.system|subprocess\.call|subprocess\.Popen)\s*\(\s*(?:f["\'])', re.IGNORECASE),
     "IRON-SEC-03", "命令注入风险", "high",
     "使用f-string构造系统命令，存在命令注入风险。应使用subprocess.run(list)"),
]

# 不安全的反序列化
UNSAFE_DESERIALIZE = [
    (re.compile(r'pickle\.(?:load|loads)\s*\(', re.IGNORECASE),
     "IRON-SEC-04", "不安全反序列化", "high",
     "pickle.load/loads 可被利用执行任意代码。应使用 json 或安全的序列化格式",
     "改用 json 或安全的序列化格式（如 msgpack）"),
    (re.compile(r'yaml\.(?:load|full_load)\s*\(', re.IGNORECASE),
     "IRON-SEC-04", "不安全反序列化", "high",
     "yaml.load 可被利用执行任意代码。应使用 yaml.safe_load()",
     "改用 yaml.safe_load()"),
    (re.compile(r'\bunserialize\s*\(', re.IGNORECASE),
     "IRON-SEC-04", "不安全反序列化(PHP)", "high",
     "PHP 的 unserialize() 可被利用触发对象注入/任意代码执行，若数据来自用户输入（$_POST/$_COOKIE）风险极高。",
     "避免对不可信输入使用 unserialize()；改用 json_decode() 或对反序列化对象做白名单校验"),
]

# 硬编码 URL/端口
HARDCODED_NETWORK = [
    (re.compile(r'(?:url|host|endpoint|base_url|api_url)\s*[:=]\s*["\']https?://\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}', re.IGNORECASE),
     "IRON-SEC-05", "硬编码内网地址", "medium",
     "代码中硬编码了IP地址，部署时容易遗漏修改。应使用配置文件或环境变量"),
]

# 危险协议/函数检测（marshal、eval、exec 等可执行任意代码的函数）
DANGEROUS_FUNCTIONS = [
    (re.compile(r'marshal\.loads?\s*\(', re.IGNORECASE),
     "IRON-SEC-06", "危险函数调用(marshal)", "critical",
     "marshal.load/loads 可被恶意构造的数据触发任意代码执行。通俗解释：marshal 就像把压缩包解压后自动运行里面的程序，坏人可以伪造压缩包让你的电脑执行恶意代码。",
     "改用 json.loads() 或 msgpack 等安全的序列化格式，并始终用 try-except 包裹"),
    (re.compile(r'\beval\s*\(', re.IGNORECASE),
     "IRON-SEC-06", "危险函数调用(eval)", "critical",
     "eval() 会将字符串当作代码执行，攻击者可通过输入注入恶意代码。通俗解释：eval 就像让别人直接在你的电脑上敲命令，你不知道对方会敲什么。",
     "使用 ast.literal_eval() 替代 eval()，或完全避免动态执行。如果必须使用，对输入做严格白名单校验"),
    (re.compile(r'(?<!\.)\bexec\s*\(', re.IGNORECASE),
     "IRON-SEC-06", "危险函数调用(exec)", "critical",
     "exec() 会执行任意 Python 代码，是最高危的函数之一。通俗解释：exec 比 eval 更危险，相当于把家门钥匙和保险柜密码一起给了陌生人。",
     "几乎永远不应该使用 exec()。如果确需动态代码执行，考虑使用受限的沙箱环境"),
]

# 跨语言危险命令执行（Go/PHP/Java/Node 等）—— 多语言适配（证据审计缺陷 7）
# 原有规则只覆盖 Python（subprocess/os.system/eval/exec），对 Go/PHP/Java/JS
# 项目的系统命令执行"看不见"。这里补充主流后端语言的命令执行函数检测，
# 作为审计关注点提示（确认命令与参数来源可信），避免非 Python 项目致命漏洞静默漏检。
CROSS_LANG_COMMAND_EXEC = [
    (re.compile(r'exec\.Command\s*\(', re.IGNORECASE),   # Go
     "IRON-SEC-17", "系统命令执行(exec.Command)", "high",
     "调用 exec.Command 执行系统命令，若命令或参数可被外部输入影响，存在命令注入风险。",
     "确认命令与参数均来自可信来源；对用户输入做白名单校验，避免拼接成命令"),
    (re.compile(r'(?<!os\.)\b(?:shell_exec|system|passthru|pcntl_exec|proc_open)\s*\(', re.IGNORECASE),  # PHP
     "IRON-SEC-17", "系统命令执行(PHP)", "high",
     "PHP 调用 system/shell_exec 等执行系统命令，若参数来自用户输入（$_GET/$_POST/$_REQUEST），存在远程命令执行风险。",
     "避免使用命令执行函数；改用安全的库，或对输入做严格白名单校验"),
    (re.compile(r'Runtime\s*\.\s*getRuntime\s*\(\)\s*\.\s*exec|ProcessBuilder\s*\(', re.IGNORECASE),  # Java
     "IRON-SEC-17", "系统命令执行(Java)", "high",
     "调用 Runtime.exec / ProcessBuilder 执行系统命令，若参数可被外部输入影响，存在命令注入风险。",
     "确认命令与参数来源可信；对用户输入做白名单校验"),
    (re.compile(r'child_process\s*\.\s*(?:exec|execSync|spawn|spawnSync)\s*\(', re.IGNORECASE),  # Node
     "IRON-SEC-17", "系统命令执行(Node)", "high",
     "调用 child_process.exec/spawn 执行系统命令，若参数可被外部输入影响，存在命令注入风险。",
     "优先使用 spawn 的数组参数形式；对用户输入做白名单校验"),
]

# 不安全的子进程调用（shell=True 未校验参数）
UNSAFE_SUBPROCESS = [
    (re.compile(r'subprocess\.(?:call|Popen|run)\s*\([^)]*shell\s*=\s*True', re.IGNORECASE),
     "IRON-SEC-07", "不安全的子进程调用(shell=True)", "high",
     "shell=True 会启动系统 shell 解释命令字符串，攻击者可注入额外命令（如 ; rm -rf /）。通俗解释：shell=True 就像把命令写在纸条上交给一个不检查内容的人去执行，坏人可以在纸条上加料。",
     "去掉 shell=True，改用列表形式传递参数：subprocess.run(['cmd', 'arg1', 'arg2'])。如果必须用 shell，使用 shlex.quote() 转义所有用户输入"),
    (re.compile(r'os\.system\s*\([^)]*\+', re.IGNORECASE),
     "IRON-SEC-07", "不安全的子进程调用(os.system)", "high",
     "os.system() 直接调用系统 shell，且通过字符串拼接构造命令，极易被注入。通俗解释：os.system 是直接对操作系统喊话，如果喊的内容里混入了用户输入，坏人可以让系统执行任意命令。",
     "使用 subprocess.run() 替代 os.system()，参数以列表形式传递"),
]

# 不安全的文件操作（路径遍历风险）
PATH_TRAVERSAL = [
    (re.compile(r'os\.(?:remove|unlink|rmdir)\s*\([^)]*\+', re.IGNORECASE),
     "IRON-SEC-08", "路径遍历风险(拼接删除)", "high",
     "使用字符串拼接构造文件删除路径，攻击者可通过 ../ 跳出预期目录，删除系统关键文件。通俗解释：如果让用户输入文件名然后直接删除，坏人可以输入 ../../windows/system32/xxx 来删除系统文件。",
     "使用 os.path.realpath() 规范化路径后，验证路径是否在允许的目录范围内。或使用 pathlib.Path.resolve()"),
    (re.compile(r'\.\.\/|\.\.\\\\', re.IGNORECASE),
     "IRON-SEC-08", "路径遍历风险(../)", "high",
     '代码中出现了 ../ 或 ..\\ 路径遍历模式，如果与用户输入拼接，存在目录穿越攻击风险。通俗解释：../ 在文件路径中表示"上一级目录"，坏人可以用它跳出你的工作目录，访问到不该访问的文件。',
     "使用 os.path.realpath() 或 Path.resolve() 规范化路径，验证最终路径是否在允许的基准目录内"),
]

# 弱加密算法（MD5/SHA1/RC4）- 排除 hashlib. 前缀（由 IRON-SEC-14 专门处理）
WEAK_CRYPTO = [
    (re.compile(r'(?<!hashlib\.)\b(?:MD5|SHA1|RC4|DES)\b', re.IGNORECASE),
     "IRON-SEC-10", "弱加密算法", "high",
     "使用了已知不安全的加密算法（MD5/SHA1/RC4/DES）。这些算法已被破解或不推荐使用。",
     "使用 SHA-256 或更高级别算法替代。对于密码存储，使用 bcrypt/scrypt/argon2。"),
]

# SSRF 风险（requests.get/urllib 使用用户输入 URL）
SSRF_PATTERNS = [
    (re.compile(r'(?:requests|urllib|httpx)\.(?:get|post|request)\s*\([^)]*\+\s*', re.IGNORECASE),
     "IRON-SEC-11", "SSRF风险", "high",
     "使用字符串拼接构造 HTTP 请求 URL，攻击者可注入内网地址进行 SSRF 攻击。",
     "对用户输入的 URL 做白名单校验，禁止访问内网地址（127.0.0.1, 10.x, 172.16-31.x, 192.168.x）"),
]

# XSS 风险（未转义的用户输入输出到 HTML）
XSS_PATTERNS = [
    (re.compile(r'(?:innerHTML|outerHTML|dangerouslySetInnerHTML|document\.write\s*\()', re.IGNORECASE),
     "IRON-SEC-12", "XSS风险", "high",
     "直接使用 innerHTML/dangerouslySetInnerHTML 设置 HTML 内容，存在 XSS 攻击风险。",
     "使用 textContent 替代 innerHTML，或对用户输入做 HTML 实体转义"),
]

# 不安全随机数（random 模块用于安全场景）
INSECURE_RANDOM = [
    (re.compile(r'\brandom\.(?:random|randint|choice|sample|shuffle)\b', re.IGNORECASE),
     "IRON-SEC-13", "不安全随机数", "medium",
     "使用 random 模块生成随机数，不适合安全场景（可预测）。",
     "安全场景应使用 secrets 模块（secrets.token_hex, secrets.choice）或 os.urandom()"),
]

# 弱哈希算法（用于密码存储）
WEAK_HASH = [
    (re.compile(r'\bhashlib\.(?:md5|sha1)\b', re.IGNORECASE),
     "IRON-SEC-14", "弱哈希算法", "medium",
     "使用 MD5/SHA1 哈希算法，不适合密码存储和完整性校验。",
     "密码存储使用 bcrypt/scrypt/argon2。完整性校验使用 SHA-256 起。"),
]

# SSL/TLS 证书验证禁用
SSL_VERIFY_DISABLED = [
    (re.compile(r'\bverify\s*=\s*False\b', re.IGNORECASE),
     "IRON-SEC-15", "SSL证书验证禁用", "medium",
     "禁用了 SSL/TLS 证书验证（verify=False），容易遭受中间人攻击。",
     "移除 verify=False，使用正确的证书链。仅在内网开发环境保留。"),
]

# 资源耗尽风险（无限制循环/递归/大文件读取）
RESOURCE_EXHAUSTION = [
    (re.compile(r'while\s+True\s*:', re.IGNORECASE),
     "IRON-SEC-16", "资源耗尽风险(无限循环)", "low",
     "检测到 while True 无限循环，缺少明确的退出条件可能导致资源耗尽。",
     "确保循环有明确的退出条件（break/return），或使用 timeout 机制"),
]

# ===== 非 Python 文件的机密检测 =====
# 在 .env / .yaml / .json / .toml 文件中扫描机密
NON_PY_SECRET_EXTENSIONS = {".env", ".yaml", ".yml", ".json", ".toml", ".cfg", ".ini", ".conf"}

NON_PY_SECRET_MASK_PATTERN = re.compile(
    r'(?:password|passwd|pwd|secret|token|api_key|apikey|access_key|private_key|client_secret)\s*[:=]\s*["\']?([^"\'#\s]{8,})["\']?', re.IGNORECASE
)

# ===== 文档类文件（.md/.SKILL/.txt）的治理合规检测 =====
# 治理合规盲区：SKILL.md / 文档中的明文凭据与审查绕过表述（已有真实项目缺陷验证）
# 文档文件扩展名
DOC_EXTENSIONS = {".md", ".markdown", ".txt", ".skill"}

# 文档中的明文密钥环境变量赋值：如 SN_API_KEY=xxx、BRAND_LLM_API_KEY=xxx、KEY=xxx
# 仅匹配密钥类环境变量名 + 赋值，避免误报普通说明文字
DOC_SECRET_PATTERNS = [
    (re.compile(r'\b[A-Z0-9_]+(?:_API_KEY|_APISECRET|_SECRET_KEY|_ACCESS_KEY|_PRIVATE_KEY|_CLIENT_SECRET)\b\s*=', re.IGNORECASE),
     "IRON-SEC-01", "文档明文密钥赋值", "critical",
     "文档中展示了密钥环境变量的明文赋值（如 *_API_KEY=...），若被用户照抄写入配置文件会造成凭据明文落盘。应改用占位符并提示从密钥管理服务获取。"),
    (re.compile(r'\bsk-[A-Za-z0-9_\-]{8,}\b'),
     "IRON-SEC-01", "文档明文密钥赋值", "critical",
     "文档中出现了 sk- 开头的明文 API 密钥，可能是泄露的真实密钥。应立即撤销并改用密钥管理服务。"),
]

# 文档中引导把密钥写入凭据配置文件（.env 等）
DOC_ENV_CRED_PATTERNS = [
    (re.compile(r'\.env\b[^\n]*\b(?:API_KEY|SECRET|TOKEN|CREDENTIAL|[A-Z0-9_]*KEY)\b', re.IGNORECASE),
     "IRON-SEC-01", "引导密钥写入凭据文件", "critical",
     "文档引导/示范将密钥类环境变量（API_KEY/SECRET/TOKEN/KEY）写入 .env 凭据配置文件，属于明文凭据落盘的高风险指引。应引导使用密钥管理服务或加密存储。"),
    (re.compile(r'(?:写入|保存|配置到|export)\s*[^\n]*\.env\b', re.IGNORECASE),
     "IRON-SEC-01", "引导密钥写入凭据文件", "high",
     "文档引导将配置（可能含密钥）写入 .env 凭据配置文件，若包含 API Key 属于明文凭据落盘。应避免在文档中示范密钥明文写入凭据文件。"),
]

# 文档中的审查绕过类表述（削弱合规审查严格度）
GOV_BYPASS_PATTERNS = [
    (re.compile(r'忽略清单'), "审查绕过(忽略清单)", "high",
     "文档建立了'忽略清单'以跳过合规审查，属治理合规盲区。忽略清单应受审计追踪，不得静默放行。"),
    (re.compile(r'直接放行'), "审查绕过(直接放行)", "high",
     "文档声明对命中项'直接放行'不审查，削弱了合规审查严格度，属治理合规盲区。"),
    (re.compile(r'一律不输出'), "审查绕过(一律不输出)", "high",
     "文档规定'一律不输出来些风险'，隐藏了非致命但仍需记录的风险，属治理合规盲区。应保留风险日志供审计。"),
    (re.compile(r'直接跳过[^\n]*不报告'), "审查绕过(直接跳过不报告)", "high",
     "文档规定'直接跳过且不报告风险'，削弱了合规审查的可审计性，属治理合规盲区。"),
]

# 文档中的违禁宣称/伪科学工艺词（与 prompt_compliance 治理词表一致，防知识包示例污染）
# 仅收录最明确的伪科学工艺词，排除"发酵工艺"等可作科学描述的术语，避免误报
DOC_CLAIM_PATTERNS = [
    (re.compile(r'生物合成|工业生物制造|工业生物合成|DNA重组', re.IGNORECASE),
     "IRON-GOV-02", "文档违禁宣称(伪科学工艺词)", "medium",
     "文档/知识包示例使用了伪科学工艺词（如'生物合成'），与库内合规自禁令冲突，会示范污染下游 prompt 生成。应删除或替换为合规表述。"),
]

# ===== 代码中密钥明文落盘（写入 .env 等凭据文件）=====
# f-string 密钥赋值格式化进字符串（如 f"BRAND_LLM_API_KEY={self.llm.api_key}"）
# 值来源必须为 api_key/secret/token/key 变量，确保确证性
SECRET_PERSIST_PATTERNS = [
    (re.compile(r'f["\'][^"\']*\b[A-Z0-9_]*(?:API|ACCESS|SECRET|PRIVATE|CLIENT)[_-]?KEY\b\s*=\s*\{[^}]*\b(?:api_key|secret|token|key)\b', re.IGNORECASE),
     "IRON-SEC-01", "密钥明文落盘", "critical",
     "将 API Key 以 'KEY={...}' 形式格式化进字符串（通常用于写入 .env 等凭据配置文件），属于明文凭据落盘，应改用密钥管理服务。"),
]

# 密钥落盘方法名（如 def save_env / write_env / export_env）
SECRET_PERSIST_FUNC = re.compile(r'def\s+\w*(?:save_env|write_env|export_env|dump_env)\w*\s*\(')

# 敏感信息泄露（日志/调试输出中暴露敏感变量）
SENSITIVE_LOG = [
    (re.compile(r'(?:print|log(?:ger)?\.(?:debug|info|warning|error)|console\.log)\s*\([^)]*\b(?:password|passwd|pwd|secret|token|api_key|apikey|access_key|private_key)\b', re.IGNORECASE),
     "IRON-SEC-09", "敏感信息泄露(日志输出)", "medium",
     "调试/日志输出中包含了密码、Token、密钥等敏感信息，一旦日志泄露，攻击者可直接获取凭据。通俗解释：把密码打印到日志里，就像把银行卡密码写在快递单上——任何看到这张单子的人都能用你的卡。",
     "移除调试输出中的敏感变量，或使用脱敏函数（如显示前2位+***）替代完整输出。生产环境日志级别应设为 INFO 以上"),
    (re.compile(r'(?:print|log(?:ger)?\.(?:debug|info|warning|error)|console\.log)\s*\(\s*["\'][^"\']*\b(?:password|token|secret|key)\b[^"\']*["\']', re.IGNORECASE),
     "IRON-SEC-09", "敏感信息泄露(明文输出)", "medium",
     "日志字符串中明文包含了 'password'、'token' 等敏感词汇，可能是误将敏感信息输出。通俗解释：即使日志里没有真的密码，包含 'password' 字样也会引起攻击者的注意，让他们知道去哪找。",
     "检查此处是否确实输出敏感信息，若是则立即移除；若为普通描述文字，建议改用更中性的措辞"),
]

# 不安全的反序列化扩展（json 无异常处理、XML 外部实体注入）
UNSAFE_DESERIALIZE_EXTENDED = [
    (re.compile(r'json\.loads?\s*\(', re.IGNORECASE),
     "IRON-SEC-10", "不安全反序列化(json无异常处理)", "medium",
     "json.loads() 未包裹在 try-except 中，遇到格式错误或恶意构造的 JSON 数据时会导致程序崩溃。通俗解释：json.loads 就像拆包裹，如果包裹里有炸弹（格式错误的数据），没有防护措施的话整个程序就会炸掉。",
     "用 try-except json.JSONDecodeError 包裹 json.loads() 调用，并记录异常日志"),
    (re.compile(r'(?:xml\.etree\.ElementTree|xml\.dom\.minidom|xml\.sax)\s*\.\s*parse', re.IGNORECASE),
     "IRON-SEC-10", "不安全反序列化(XXE漏洞)", "high",
     'XML 解析器默认允许外部实体（XXE），攻击者可通过构造恶意 XML 读取服务器文件、发起 SSRF 攻击。通俗解释：XML 解析器在处理文件时，会"听话地"去读取文件里指向的外部资源，坏人可以利用这一点偷看服务器上的文件。',
     "使用 defusedxml 库替代标准 xml 库，或设置 parser 禁用外部实体：parser.entity = False"),
    (re.compile(r'(?:from\s+xml\.etree|import\s+xml\.etree|from\s+xml\.dom|import\s+xml\.dom)', re.IGNORECASE),
     "IRON-SEC-10", "不安全反序列化(XML导入)", "high",
     "代码中导入了 xml.etree 或 xml.dom 模块。Python 标准 XML 库默认不安全，存在 XXE 和 Billion Laughs 攻击风险。通俗解释：Python 自带的 XML 处理工具就像没装杀毒软件的电脑，处理恶意 XML 文件时会被攻击。",
     "安装并使用 defusedxml 库替代标准 xml 库：pip install defusedxml，然后 from defusedxml import ElementTree"),
]

# ─── 错题本模式 ────────────────────────────────────────────────

# 空 catch / 裸 except
PITFALL_EMPTY_EXCEPT = [
    (re.compile(r'except\s*(?:\w+\s*)?(?:as\s+\w+\s*)?:\s*\n\s*(?:pass|continue|return\s+None)\s*$', re.MULTILINE),
     "PITFALL-01", "空异常处理", "high",
     "异常被静默吞噬（pass/continue），问题被隐藏。应至少记录日志或明确处理"),
    (re.compile(r'except\s*:\s*\n\s*(?:pass|continue)', re.MULTILINE),
     "PITFALL-01", "裸except", "high",
     "裸 except 会捕获 KeyboardInterrupt 等系统异常。应指定具体异常类型"),
]

# 可变默认参数
PITFALL_MUTABLE_DEFAULT = [
    (re.compile(r'def\s+\w+\s*\([^)]*=\s*(?:\[\s*\]|\{\s*\}|set\s*\(\s*\))', re.IGNORECASE),
     "PITFALL-02", "可变默认参数", "medium",
     "默认参数使用了可变对象（[]/{}），多次调用会共享同一实例。应使用 None + 内部初始化"),
]

# 不安全的文件操作
PITFALL_UNSAFE_FILE = [
    (re.compile(r'open\s*\([^)]*\)\s*\.\s*(?:read|write|readlines)', re.IGNORECASE),
     "PITFALL-03", "未使用上下文管理器", "medium",
     "文件操作未使用 with 语句，可能导致资源泄漏。应使用 with open() as f:"),
    (re.compile(r'os\.remove\s*\(|os\.rmdir\s*\(|shutil\.rmtree\s*\(', re.IGNORECASE),
     "PITFALL-04", "危险删除操作", "medium",
     "直接删除文件/目录，批量操作可能导致数据丢失。确认有备份机制"),
]

# 竞态条件
PITFALL_RACE_CONDITION = [
    (re.compile(r'os\.path\.exists\s*\([^)]+\)\s*\n.*open\s*\(', re.IGNORECASE),
     "PITFALL-05", "TOCTOU竞态", "medium",
     "先检查文件存在再打开，存在 TOCTOU 竞态条件。应直接尝试打开并捕获异常"),
]

# 不安全的线程操作
PITFALL_THREAD_UNSAFE = [
    (re.compile(r'threading\.Thread\s*\([^)]*\)\s*\.\s*start\s*\(\s*\)', re.IGNORECASE),
     "PITFALL-06", "裸线程启动", "low",
     "直接使用 threading.Thread 未做线程管理。建议使用 ThreadPoolExecutor 或守护线程"),
]

# I/O 反模式
PITFALL_IO_PATTERNS = [
    # 一次性读取整个文件到内存（不包含 with 上下文管理器中的操作）
    (re.compile(r'open\s*\([^)]*\)\s*\.\s*(?:read|readlines)\s*\(\s*\)', re.IGNORECASE),
     "PITFALL-07", "一次性读取整个文件", "medium",
     "一次性读取整个文件到内存。大文件应使用逐行迭代或指定 buffering 参数"),
    # 文件操作未使用 with 语句
    (re.compile(r'(\w+)\s*=\s*open\s*\(', re.IGNORECASE),
     "PITFALL-08", "文件未使用with", "medium",
     "文件打开后未使用 with 语句管理，可能导致资源泄漏。应使用 with open() as f:"),
]

# ─── 质量铁律 ──────────────────────────────────────────────────

# 函数过长（>100行）（阈值收敛到 config/settings.py）
FUNCTION_TOO_LONG = GOVERNANCE_FUNCTION_TOO_LONG

# 参数过多（>8个）
FUNCTION_TOO_MANY_PARAMS = GOVERNANCE_FUNCTION_TOO_MANY_PARAMS

# 嵌套过深（>4层）
NESTING_TOO_DEEP = GOVERNANCE_NESTING_TOO_DEEP

# ─── 架构铁律：依赖方向检测 ────────────────────────────────────

# 允许的层级依赖方向（低层不应该依赖高层）
# 注意：这是默认规则，可通过配置文件覆盖。
# 开源项目可通过 .coderef_governance.json 自定义层级规则。
# test 为独立测试层：测试代码引用被测代码（任意层）属正常行为，不算穿透。
LAYER_RULES = {
    "entry": ["core", "data", "shared", "other"],
    "core": ["data", "shared", "other"],
    "data": ["shared", "other"],
    "shared": ["other"],
    "test": ["entry", "core", "data", "shared", "other"],
    "other": [],
}

# 层级→目录名映射（默认值，可通过 LLM 分析覆盖）
# 开源项目不应依赖此映射，应通过 LLM 分析代码内容动态分类。
# test 层目录名：tests/test/spec 等，避免测试文件落入 "other" 层后
# 因白名单为空，import core.* 全部误报层级穿透。
DEFAULT_LAYER_PATTERNS = {
    "entry": ["route", "routes", "controller", "controllers", "handler", "handlers",
               "api", "view", "views", "gui", "window"],
    "core": ["service", "services", "core", "engine", "business", "logic", "domain", "agent"],
    "data": ["model", "models", "data", "db", "database", "store", "repo",
              "repository", "entity", "entities", "schema", "schemas"],
    "shared": ["util", "utils", "shared", "common", "config", "helper", "helpers", "lib"],
    "test": ["test", "tests", "testing", "spec", "specs", "__tests__"],
}

# ─── 异步函数中同步阻塞检测 ────────────────────────────────────

BLOCKING_CALLS = {
    "time.sleep", "time.sleep_ms", "time.sleep_us",
    "requests.get", "requests.post", "requests.put", "requests.delete",
    "requests.patch", "requests.head",
    "urllib.request.urlopen",
    "socket.connect", "socket.recv", "socket.send",
    "subprocess.run", "subprocess.call", "subprocess.Popen",
    "os.system", "os.popen",
}

# 文档密钥赋值的已知占位符值（lower 精确匹配）：示例配置的通用占位写法。
# ollama 是本地免费模型名（README 明示无需 Key），同样按占位符处理。
DOC_SECRET_PLACEHOLDER_VALUES = frozenset({
    "xxx", "xxxx", "xxx-xxx", "placeholder", "example", "sample", "dummy",
    "changeme", "change_me", "replace_me", "replace-me", "your-api-key",
    "your_api_key", "your-key", "your_key", "your-secret", "your_token",
    "your-token", "your-key-here", "yourkeyhere", "ollama", "test", "demo",
    "mock", "fake",
})


