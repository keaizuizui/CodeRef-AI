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
import logging
import os
import re
from typing import List, Set
from dataclasses import dataclass
from collections import defaultdict

logger = logging.getLogger(__name__)

# SEC-08 自管缓存库判定结果记忆化：键 (文件路径, 接收对象表达式)，值 bool。
# 纯文件内容函数（与实例状态无关），进程级共享缓存语义等价于原实例懒加载缓存。
_SEC08_DB_CACHE = {}


@dataclass
class AgentSecurityRisk:
    """Agent 安全风险"""
    risk_id: str
    risk_name: str
    category: str  # prompt_injection / context_manipulation / tool_misuse / budget / data_exfil / autonomous / knowledge
    severity: str  # blocker / critical / high / medium / low
    file_path: str
    line_number: int
    line_content: str
    detail: str
    suggestion: str
    cwe_id: str = ""  # 映射到传统 CWE（如果适用）


SEVERITY_ORDER = {"blocker": 0, "critical": 1, "high": 2, "medium": 3, "low": 4}

# AGENT-SEC-53 共享写调用正则：GO_PATTERNS 的候选匹配与 AgentSecurityAuditor 的
# 文件级确认（_crosslang_file_signal_lines）必须复用同一编译对象，避免两处正则漂移
# 导致"候选命中但确认匹配不到"或反之。用 .{0,200}? 支持嵌套参数（如
# WriteFile(sanitize(req.Path))）；模块级常量供类体内类属性列表与实例方法共用。
_SEC53_WRITE_RE = re.compile(
    r'(?:WriteFile|WriteFileByString|WriteFileByBytes|os\.WriteFile|os\.Create|os\.OpenFile|WriteAt)'
    r'\s*\(.{0,200}?\b(?:req|body)\.[A-Za-z_]+', re.IGNORECASE)


# ==================== 模块级规则常量区（自 AgentSecurityAuditor 提取，规则数据与检测逻辑分离） ====================
# ─── 提示注入检测 ───
PROMPT_INJECTION_PATTERNS = [
    # 直接拼接用户输入到提示词
    (re.compile(r'f["\'].*\{.*(?:user_input|user_message|query|question|prompt|input|content).*\}', re.IGNORECASE),
     "AGENT-SEC-01", "提示注入风险", "critical",
     "检测到用户输入直接拼接到 prompt 中，攻击者可通过精心构造的输入绕过 Agent 的安全限制",
     "使用结构化 prompt 模板 + 参数注入，或对用户输入做分层标记（system/user/assistant）"),
    # 未过滤的用户输入进入 system prompt
    (re.compile(r'(?:system_prompt|sys_prompt|system_message)\s*[+=]\s*.*\{.*\}', re.IGNORECASE),
     "AGENT-SEC-01", "提示注入风险", "critical",
     "检测到 system prompt 中包含用户可控变量，这是最高风险的注入点",
     "system prompt 应完全由开发者控制，不包含任何用户输入"),
    # 多轮对话中未区分角色
    (re.compile(r'messages\.append\s*\(\s*\{.*role.*user.*content.*\}', re.IGNORECASE),
     "AGENT-SEC-02", "角色混淆风险", "high",
     "检测到消息列表构建时用户内容可能与系统指令混淆",
     "确保 messages 列表中 role 字段正确，且 system 角色的消息在任何用户消息之前"),
]

# ─── 上下文操纵检测 ───
CONTEXT_MANIPULATION_PATTERNS = [
    # 外部 URL 内容直接注入上下文
    (re.compile(r'(?:requests\.get|urllib|fetch|httpx\.get)\s*\(.*\).*\.text.*prompt', re.IGNORECASE),
     "AGENT-SEC-03", "外部内容注入", "high",
     "检测到外部 URL 内容直接注入到 LLM 上下文，攻击者可通过控制URL内容操控Agent",
     "对外部内容做沙箱化处理：限制长度、过滤控制字符、添加来源标记"),
    # 未做内容过滤的 RAG 检索
    (re.compile(r'(?:retrieve|search|query).*\.content.*prompt', re.IGNORECASE),
     "AGENT-SEC-04", "知识投毒风险", "medium",
     "检测到 RAG 检索结果直接注入到 prompt，未经可信度校验",
     "对检索结果做可信度评分，过滤低质量内容，添加来源引用"),
    # 未限制长度的上下文
    (re.compile(r'context\s*\+=\s*|context\.append|context\.extend', re.IGNORECASE),
     "AGENT-SEC-05", "上下文溢出风险", "medium",
     "检测到上下文无限追加，可能导致 token 超限或上下文窗口溢出",
     "实现上下文窗口管理：滑动窗口、摘要压缩、或限制最大 token 数"),
]

# ─── 工具滥用检测 ───
TOOL_MISUSE_PATTERNS = [
    # 文件删除/修改
    (re.compile(r'(?:os\.remove|os\.unlink|shutil\.rmtree|Path\.unlink|os\.rename)', re.IGNORECASE),
     "AGENT-SEC-06", "危险文件操作", "blocker",
     "检测到 Agent 可执行文件删除/重命名操作，可能被滥用导致数据丢失",
     "添加 human-in-the-loop 确认、文件操作白名单、或沙箱化执行环境"),
    # 命令执行
    (re.compile(r'(?:subprocess|os\.system|os\.popen|os\.exec|eval|exec)\s*\(', re.IGNORECASE),
     "AGENT-SEC-07", "危险命令执行", "blocker",
     "检测到 Agent 可执行系统命令，这是最高风险的操作",
     "禁用命令执行能力，或严格限制为白名单命令 + 沙箱环境"),
    # 数据库写入
    (re.compile(r'(?:\.execute\s*\(|\.commit\s*\(|\.write\s*\(|\.save\s*\()', re.IGNORECASE),
     "AGENT-SEC-08", "无确认写入操作", "high",
     "检测到 Agent 可执行数据库写入/文件保存操作，未经人工确认",
     "添加写入前确认机制，或实现 dry-run 模式先预览变更"),
    # 网络请求（可能被 SSRF 利用）—— 覆盖 Python/Go/PHP 跨语言签名
    (re.compile(r'(?:requests\.(?:get|post|put|delete)|httpx\.(?:get|post)|urllib\.request|'
                r'http\.(?:Get|Post|NewRequest|Head|Do)\s*\(|\.Do\s*\(|'          # Go net/http
                r'curl_(?:init|exec|setopt)\s*\(|GuzzleHttp|->request\s*\(|sendRequest\s*\(|'  # PHP
                r'file_get_contents\s*\(https?)', re.IGNORECASE),
     "AGENT-SEC-09", "不受控网络请求", "medium",
     "检测到 Agent 可发起网络请求（Python/Go/PHP），可能被用于 SSRF 或数据外传",
     "限制网络请求的目标域名白名单，或使用代理层过滤"),
]

# ─── 预算/资源耗尽检测 ───
BUDGET_EXHAUSTION_PATTERNS = [
    # 无限制 LLM 循环
    (re.compile(r'while\s+(?:True|1)\s*:.*(?:chat|completion|generate|invoke|call)', re.IGNORECASE),
     "AGENT-SEC-10", "无限LLM调用循环", "blocker",
     "检测到无限循环中调用 LLM，可能导致 API 费用失控",
     "添加 max_iterations 限制、token 预算计数器、或费用上限"),
    # 缺失 token 预算
    (re.compile(r'(?:max_tokens|max_length)\s*=\s*(?:None|0|99999)', re.IGNORECASE),
     "AGENT-SEC-11", "Token预算未设置", "high",
     "检测到 LLM 调用未设置合理的 max_tokens 限制",
     "设置合理的 max_tokens（如 4096），防止单次调用消耗过多资源"),
    # 循环中累积上下文
    (re.compile(r'for\s+\w+\s+in\s+.*:\s*.*(?:messages|context|prompt).*append', re.IGNORECASE),
     "AGENT-SEC-12", "上下文无限累积", "medium",
     "检测到循环中无限追加消息到上下文，可能导致 token 消耗指数增长",
     "实现上下文窗口管理：仅保留最近 N 轮对话，或使用摘要压缩"),
]

# ─── 数据泄露检测 ───
DATA_EXFIL_PATTERNS = [
    # 日志中记录完整 prompt 内容。仅当实际把 prompt 泄漏进日志/print 时触发：
    #   - 插值形式：`f"... {prompt} ..."`（花括号内出现 prompt/messages 等变量）
    #   - 直接变量：`logger.info(prompt)` / `print(messages)`（无引号包裹的变量）
    # 避免把 `print(f"prompt template for: {type}")` 这类仅含"prompt"字样、
    # 但未泄漏任何 prompt 内容的语句误判为日志泄露。
    (re.compile(r'(?:logger\.(?:info|debug|error|warning)|print|logging\.[a-z]+)\s*\((?:.*\{.*(?:prompt|messages|system_prompt|conversation).*\}|\s*(?:str\(\s*)?(?:prompt|messages|system_prompt)\s*\))', re.IGNORECASE),
     "AGENT-SEC-13", "Prompt日志泄露", "high",
     "检测到日志中可能记录了完整 prompt 内容，敏感信息可能被泄露",
     "对日志中的 prompt 内容做脱敏处理，或使用专门的审计日志"),
    # API Key 在请求中传递
    (re.compile(r'headers\s*\[.*(?:api.?key|authorization|token).*\]', re.IGNORECASE),
     "AGENT-SEC-14", "API Key 明文传递", "medium",
     "检测到 API Key 在 HTTP 请求头中明文传递",
     "使用环境变量存储 API Key，通过密钥管理服务注入"),
    # 敏感数据输出到外部
    (re.compile(r'(?:requests\.(?:post|put)|httpx\.(?:post|put)).*response.*text', re.IGNORECASE),
     "AGENT-SEC-15", "敏感数据外传风险", "medium",
     "检测到 LLM 响应内容通过网络请求发送到外部",
     "审计所有外部网络请求的目的地，添加数据外传检测"),
]

# ─── PII 泄露检测 ───
PII_LEAK_PATTERNS = [
    # 日志中打印邮箱
    (re.compile(r'(?:logger\.(?:info|debug|error|warning)|print|logging)\s*\(.*@.*\.', re.IGNORECASE),
     "AGENT-SEC-18", "PII日志泄露（邮箱）", "high",
     "检测到日志中可能包含邮箱地址，违反 GDPR/CCPA 数据隐私法规",
     "对日志中的 PII 做脱敏处理：user@example.com → u***@example.com"),
    # 日志中打印手机号
    (re.compile(r'(?:logger\.(?:info|debug|error|warning)|print|logging)\s*\(.*(?:\+?86)?\s*1[3-9]\d{9}', re.IGNORECASE),
     "AGENT-SEC-19", "PII日志泄露（手机号）", "high",
     "检测到日志中可能包含手机号码，违反数据隐私法规",
     "对日志中的手机号做脱敏：139****1234，或完全移除"),
    # 日志中打印身份证号
    (re.compile(r'(?:logger\.(?:info|debug|error|warning)|print|logging)\s*\(.*\d{6}(?:19|20)\d{2}(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])\d{3}[\dXx]', re.IGNORECASE),
     "AGENT-SEC-20", "PII日志泄露（身份证号）", "blocker",
     "检测到日志中可能包含身份证号码，严重违反数据隐私法规",
     "身份证号绝对不应出现在日志中，立即移除相关日志语句"),
    # f-string 中直接拼接用户数据。
    # 用 \b 词边界包裹 PII 关键词：避免把 `api_address`/`server_address` 这类
    # 技术性变量名（address 前是下划线，无词边界）误判为"用户敏感地址"。
    (re.compile(r'f["\'].*\{.*\b(?:email|phone|mobile|id_card|passport|ssn|address|birthday)\b.*\}', re.IGNORECASE),
     "AGENT-SEC-21", "PII明文拼接", "high",
     "检测到用户敏感信息直接拼接到字符串中，可能泄露到日志或响应",
     "使用脱敏函数处理后再输出，或使用结构化日志格式"),
]

# ─── 安全配置检查 ───
SECURITY_CONFIG_PATTERNS = [
    # 调试模式未关闭
    (re.compile(r'(?:debug|DEBUG)\s*=\s*True', re.IGNORECASE),
     "AGENT-SEC-22", "调试模式开启", "high",
     "检测到 DEBUG=True，生产环境应关闭调试模式",
     "生产环境设置 DEBUG=False，或通过环境变量控制"),
    # 不安全的反序列化
    (re.compile(r'(?:pickle\.loads\s*\(([^)]*)\)|yaml\.load\s*\(|json\.loads\s*\(.*ensure_ascii)', re.IGNORECASE),
     "AGENT-SEC-23", "不安全反序列化", "medium",
     "检测到使用 pickle.loads 或 yaml.load（非 SafeLoader），可被利用执行任意代码",
     "使用 yaml.safe_load() 替代 yaml.load()，避免 pickle 反序列化不可信数据"),
    # CORS 配置过于宽松
    (re.compile(r'allow_origins\s*=\s*\[.*\*.*\]|Access-Control-Allow-Origin.*\*', re.IGNORECASE),
     "AGENT-SEC-24", "CORS配置过宽", "high",
     "检测到 CORS 配置允许所有来源（*），可能被恶意站点利用",
     "将 allow_origins 限制为具体域名白名单"),
    # 超时未设置
    (re.compile(r'(?:requests\.(?:get|post|put|delete)|httpx\.(?:get|post))\s*\(([^)]*)\)', re.IGNORECASE),
     "AGENT-SEC-25", "网络请求无超时", "medium",
     "检测到网络请求未设置 timeout 参数，可能导致请求永久挂起",
     "所有网络请求添加 timeout=30 参数"),
    # 缺少速率限制
    (re.compile(r'(?:def\s+\w+|async\s+def\s+\w+)\s*\(.*\).*:\s*\n\s*(?:result|response|data)\s*=.*(?:generate|completion|chat)', re.IGNORECASE),
     "AGENT-SEC-26", "LLM调用缺少限流", "medium",
     "检测到 LLM 调用未做速率限制，可能被滥用导致 API 费用暴涨",
     "添加 rate limiter（如 token bucket），限制每分钟调用次数"),
]

# ─── 跨语言检测（Go / Node / PHP）─────────────────────────────
# Agent 系统常由多语言组件构成（Go 后端 / Node 前端 / PHP 服务），传统逐语言
# 扫描只覆盖 Python 会系统性漏检。以下模式组按文件扩展名路由，命中即确证性
# 危险操作信号（命令执行 / 提权 / 配置模板注入 / 路径穿越 / 动态代码执行）。
# 每条：(pattern, risk_id, risk_name, severity, detail, suggestion)

# Go：常见于独立 RPC/服务端组件（如 目标项目 llm_runner server.go）
GO_PATTERNS = [
    # shell -c 命令注入：exec.Command(..., "/bin/sh", "-c", req.Command)
    (re.compile(r'exec\.Command[^(]*\([^)]*"/bin/sh"', re.IGNORECASE),
     "AGENT-SEC-30", "命令注入（Go shell）", "blocker",
     "exec.Command 以 /bin/sh -c 执行命令，若命令字符串含外部可控输入（如 RPC 请求字段）可导致任意命令执行",
     "禁止拼接 shell 字符串；改用参数数组传参 + 命令白名单 + 进程沙箱隔离"),
    # sudo 提权执行
    (re.compile(r'exec\.Command[^(]*\([^)]*"sudo"', re.IGNORECASE),
     "AGENT-SEC-31", "提权命令执行（sudo）", "blocker",
     "以 sudo 提权执行系统命令，配合未鉴权接口可向本机投递恶意配置或命令",
     "避免以 root/sudo 执行；特权操作应最小化权限并强制先行鉴权"),
    # 通用 exec.Command（未匹配 shell/sudo 的其余命令执行）
    (re.compile(r'exec\.Command(?:Context)?\s*\(', re.IGNORECASE),
     "AGENT-SEC-32", "危险命令执行（Go）", "high",
     "检测到 os/exec 命令执行，若命令或参数含外部可控输入可被利用",
     "校验命令与参数来源，使用参数数组而非 shell 字符串拼接"),
    # nginx 配置模板注入
    (re.compile(r'fmt\.Sprintf\s*\([^)]*proxy_pass|proxy_pass\s+http://%?s', re.IGNORECASE),
     "AGENT-SEC-33", "配置模板注入（nginx）", "high",
     "用户可控值拼接进 nginx 配置模板（proxy_pass/server_name），可注入配置、SSRF 或路径穿越",
     "对 Upstream 做白名单与字符白名单校验，禁止直接拼接配置模板"),
    # 文件名拼接路径穿越
    (re.compile(r'fmt\.Sprintf\s*\([^)]*%s\.(?:crt|key|conf|pem|html)', re.IGNORECASE),
     "AGENT-SEC-34", "文件路径穿越（Go）", "high",
     "用户可控值拼接进文件名（如 %s.crt），含 ../ 或路径分隔符可写出许可目录写入任意路径",
     "清洗文件名，禁止路径分隔符与 ..，落盘前校验绝对路径位于许可目录内"),
    # URL query string 手工拼接未编码
    (re.compile(r'(?:[a-z_]+)\s*\+=?\s*[a-z_]+["\']&?["\']\s*\+|encoded\s*\+=', re.IGNORECASE),
     "AGENT-SEC-35", "URL 拼接未编码（Go）", "medium",
     "手工拼接 query string 未做 url.QueryEscape，含 &/空格/中文会破坏参数或注入额外参数",
     "使用 url.Values.Encode() 构造查询参数，避免手工字符串拼接"),
    # 敏感管理端点缺少鉴权：Go 路由框架（gin/echo/chi/自带 map 路由）注册
    # manage/admin/save_ 等敏感路径，若未挂载鉴权中间件/校验可被任意调用。
    # 命中即确证性"敏感端点"信号，后续是否真未鉴权由人工/上下文确认。
    (re.compile(r'(?:Route|RouteGroup|router|mux|engine|group|Handle|HandleFunc|\.POST|\.GET|\.Any)\s*[\[\(][^`"\n]{0,60}[`"\']/(?:manage|admin|save|upload|config|secret|setting)[`"\']?', re.IGNORECASE),
     "AGENT-SEC-40", "敏感路由缺少鉴权（Go）", "medium",
     "注册到敏感管理端点（manage/admin/save_ 等）的路由可能未鉴权，构成未鉴权管理面；仅凭路由路径为低置信度信号，实际是否挂载鉴权中间件需结合上下文确认",
     "为敏感路由统一挂载鉴权中间件与权限校验，禁止无鉴权开放管理/配置端点"),
    # 黑名单式 Python 沙箱过滤：仅字符串 Contains/ToLower 匹配黑名单关键词，
    # 可被多空格/换行/字符串拼接等变体绕过；最终代码经 exec 拼接用 python -c
    # 执行，进程不隔离（目标项目 gopa.go checkPythonCodeSafety）
    (re.compile(r'checkPythonCodeSafety|strings\.Contains\s*\(\s*lowerCode|dangerousKeywords\s*=', re.IGNORECASE),
     "AGENT-SEC-41", "沙箱绕过面（黑名单过滤）", "high",
     "Python 沙箱仅靠字符串 Contains+ToLower 黑名单过滤，可经多空格/换行/字符串拼接等变体绕过黑名单；最终代码经 exec 拼接用 python -c 执行，进程不隔离",
     "改用 AST 解析 + 白名单 + 进程级沙箱（seccomp/容器）隔离执行"),
    # ticker 泄漏：创建 time.NewTicker 但全文件无 Stop（目标项目 internal/ delete.go 盲区）。
    # 需整文件确认缺失 Stop，见 _crosslang_file_signal_lines。
    (re.compile(r'time\.NewTicker\s*\(', re.IGNORECASE),
     "AGENT-SEC-51", "定时器泄漏（time.NewTicker 未 Stop）", "medium",
     "检测到 time.NewTicker 创建定时器但未调用 Stop，goroutine 与底层定时器资源永不释放，长期运行导致资源泄漏",
     "defer ticker.Stop() 及时释放定时器资源"),
    # goroutine 内直接写文件（可能是多个 goroutine 并发写同一文件/资源），无互斥锁保护。
    # 需整文件确认 goroutine 块内确有写文件，见 _crosslang_file_signal_lines。
    (re.compile(r'go\s+func\s*\(', re.IGNORECASE),
     "AGENT-SEC-52", "并发写（goroutine 内写文件）", "medium",
     "检测到 goroutine 内直接写文件且无互斥锁，多个 goroutine 并发写同一文件/资源可导致数据损坏或数据竞争",
     "对共享文件写入加 Mutex/RWMutex 保护，或使用原子写入（临时文件+改名）"),
    # 未过滤外部输入：RPC/HTTP 请求参数字段直接作为文件写入目标/内容，未做路径净化与白名单。
    # 命令方向已被 AGENT-SEC-30/31/32 覆盖，此处聚焦写文件方向；需整文件确认参数来自请求体。
    (_SEC53_WRITE_RE,
     "AGENT-SEC-53", "未过滤外部输入（请求参数直入文件写入）", "high",
     "检测到来自请求/RPC 的参数直接作为文件写入目标或内容，未做路径净化与白名单校验，可越权写入任意路径",
     "校验写入路径位于许可目录内，对写入内容做白名单/类型校验"),
    # Go 端动态执行 PHP 插件类名：请求体（req/body/payload/param）经 json.Marshal
    # 序列化后交给 LambdaPool.Exec 执行 PHP 插件，插件类名/动作名取自函数参数（外部
    # 请求字段透传）且未做白名单，与 PHP 侧动态类名 AGENT-SEC-45 同类但发生在 Go 侧
    # （目标项目 multi_pool.go:117）。单行 json.Marshal(req) 仅为候选，须整文件存在
    # 插件执行面确证（见 _crosslang_file_signal_lines），避免匹配普通 JSON 序列化。
    (re.compile(r'json\.Marshal\s*\(\s*(?:req|body|payload|param)[a-zA-Z0-9_]*\s*\)', re.IGNORECASE),
     "AGENT-SEC-55", "跨语言插件类名注入（Go→PHP 执行面）", "high",
     "检测到 Go 端将外部请求字段（req/body/payload/param）经 json.Marshal 序列化后执行 PHP 插件，插件类名/动作名来自外部输入且未做白名单校验，可跨语言注入任意 PHP 插件执行逻辑",
     "对插件类名/动作名做白名单校验，禁止直接采用外部输入作为插件标识"),
    # SSRF 网络请求面：Go net/http 出站请求直接使用外部/不可信 URL
    # （目标项目 internal/.../html.go:68 http.Get(src)、process_page.go:670
    #  http.NewRequest + client.Do 盲区）。http.Get/NewRequest/Do 本身就是出站
    # 请求，单行命中即确证性信号，无需整文件 sink 确证。
    (re.compile(r'http\.(?:Get|Post|Head)\s*\(|http\.NewRequest\s*\(|\.Do\s*\(', re.IGNORECASE),
     "AGENT-SEC-56", "SSRF 网络请求面（Go）", "high",
     "检测到 Go net/http 出站请求（http.Get/NewRequest/client.Do 等）直接使用外部/不可信 URL，无 scheme/host 白名单，可注入内网地址触发 SSRF",
     "为 Go 网络请求目标添加 scheme/host 白名单，禁止访问内网/保留地址段"),
]

# Node/TS：常见于前端/服务端 JS（如 目标项目 frontend、web 端）
NODE_PATTERNS = [
    # child_process 命令执行
    (re.compile(r'(?:require\(["\']child_process|from["\']child_process|execSync|spawnSync|execFile)', re.IGNORECASE),
     "AGENT-SEC-36", "命令注入（Node）", "blocker",
     "检测到 child_process 命令执行，参数含外部输入会导致任意命令执行",
     "禁止拼接 shell 命令，使用 execFile 参数数组 + 命令白名单"),
    # 动态代码执行
    (re.compile(r'\beval\s*\(|new\s+Function\s*\(', re.IGNORECASE),
     "AGENT-SEC-37", "动态代码执行（eval）", "high",
     "检测到 eval/Function 动态执行代码，外部可控会导致任意代码执行",
     "避免 eval，使用 JSON.parse 等安全解析代替"),
    # Vue 模板注入：v-html 直接渲染用户/外部可控内容（未净化）。
    # 需整文件确证：绑定表达式含用户数据字段（props./item./msg./content/instructions 等）
    # 且未经过 escapeHTML/sanitize/DOMPurify 净化，见 _crosslang_file_signal_lines。
    # （目标项目 subsection-box.vue / templates/detail.vue / message-item.vue 盲区）
    (re.compile(r'v-html\s*=', re.IGNORECASE),
     "AGENT-SEC-66", "Vue 模板注入（v-html DOM XSS）", "high",
     "检测到 Vue 模板 v-html 指令直接渲染用户/外部可控内容（未净化），可注入任意 HTML/脚本触发 DOM XSS",
     "改用 {{ }} 插值自动转义，或对 HTML 内容经 DOMPurify.sanitize 白名单净化后再渲染"),
    # 前端敏感信息外传：token 拼进 URL 并交给浏览器导航/发送。
    # 需整文件确证：token 插值拼 URL 且文件内存在 window.open/location 发送动作，
    # 且拼接目标为变量（非同源硬编码路径），见 _crosslang_file_signal_lines。
    # （目标项目 useOpenUrlWithToken.js:36 盲区）
    (re.compile(r'token\s*=\s*\$\{|[?&]token=\$\{', re.IGNORECASE),
     "AGENT-SEC-67", "前端敏感信息外传（token 拼 URL）", "high",
     "检测到把鉴权 token 拼进 URL 后交给 window.open/location 导航，token 会经浏览器历史、日志、Referer 泄露给目标站点（尤其外部/用户可控 URL）",
     "token 经 Authorization 请求头传递，禁止拼进 URL 查询参数"),
    # Node HTTP 服务端敏感接口无鉴权：/api/messages、/api/agent、/api/config、
    # /api/uploads 等敏感路由直接处理请求，无 token/authorization 鉴权校验。
    # 需整文件确证：文件含 HTTP 服务端 + 敏感 API 路由 + 无鉴权校验，
    # 见 _crosslang_file_signal_lines。
    # （vimax web/server.mjs:96 盲区）
    (re.compile(r"url\.pathname\s*===?\s*['\"]/api/(?:messages|agent|config|uploads|admin|user|users|delete|stop|start|exec|shell|file|files)", re.IGNORECASE),
     "AGENT-SEC-68", "Node HTTP 服务端敏感接口无鉴权", "high",
     "检测到 Node HTTP 服务端暴露敏感 API 接口（/api/messages、/api/agent、/api/config、/api/uploads 等）但无 token/authorization 鉴权校验，可被任意匿名调用执行敏感操作",
     "为敏感接口添加 token/authorization 鉴权中间件，禁止无鉴权开放管理/执行端点"),
]

# PHP：常见于遗留服务端（如 目标项目 php/worker.php）
PHP_PATTERNS = [
    (re.compile(r'\b(?:exec|shell_exec|system|passthru|proc_open)\s*\(', re.IGNORECASE),
     "AGENT-SEC-38", "命令注入（PHP）", "blocker",
     "检测到 PHP 命令执行函数，参数含外部输入会导致任意命令执行",
     "禁止命令执行，或使用白名单 + 参数转义"),
    (re.compile(r'\beval\s*\(|assert\s*\(.*\)|create_function\s*\(', re.IGNORECASE),
     "AGENT-SEC-39", "动态代码执行（PHP）", "high",
     "检测到 eval/assert 动态执行代码，外部可控导致任意代码执行",
     "避免 eval，使用安全的解析方式"),
    # 生产调试开关暴露：YII_DEBUG/YII_ENV 硬编码为 true/dev，未受环境隔离
    # （目标项目 php/worker.php:13 盲区）
    (re.compile(r'define\s*\(\s*[\'"]YII_(?:DEBUG|ENV)[\'"]\s*,\s*[\'"]?(?:true|on|dev|development)[\'"]?', re.IGNORECASE),
     "AGENT-SEC-44", "生产调试开关暴露", "high",
     "检测到 YII_DEBUG/YII_ENV 硬编码为 true/dev，生产环境暴露调试信息与堆栈，未受环境隔离",
     "调试开关由环境变量注入，生产环境关闭 display_errors 与 YII_DEBUG"),
    # 动态类名/类加载注入：基于外部输入（插件名）拼装类名后加载/实例化
    # （目标项目 php/worker.php:45 盲区）
    (re.compile(r'(?:setModule\s*\(\s*\$|new\s+\$[a-z_][\w]*\s*\(|\\app\\(?:plugins|components)\\{)', re.IGNORECASE),
     "AGENT-SEC-45", "动态类名/类加载注入", "high",
     "检测到基于外部输入（如插件名）动态拼装类名并加载/实例化模块，外部可控类名可加载任意类触发任意逻辑",
     "对插件/类名做白名单校验，禁止直接拼接外部输入作为类名"),
    # 任意 action 调用面：runAction 接收的外部 action 直接触发模块 action
    # （目标项目 php/worker.php:51 盲区）
    (re.compile(r'->\s*runAction\s*\(\s*\$', re.IGNORECASE),
     "AGENT-SEC-46", "任意 action 调用面", "high",
     "检测到 runAction 接收外部请求参数（action 来自请求体），攻击者可调用模块内任意未授权 action",
     "对 action 做白名单校验，禁止透传外部参数直接触发 action"),
    # 跨语言 RPC 日志转发无过滤：日志文本原样经 RPC 转发到 Go 服务
    # （目标项目 php/components/GoTarget.php:59 盲区）
    (re.compile(r'rpc\s*->\s*call\s*\(', re.IGNORECASE),
     "AGENT-SEC-47", "跨语言 RPC 日志转发无过滤", "medium",
     "检测到经 RPC 把日志文本（含外部可控内容）原样转发到 Go 服务，无过滤/转义，可能污染下游日志或被注入伪造记录",
     "对转发内容做转义与长度限制，落库前校验来源与格式"),
    # SSRF 转发面：外部配置/用户可控 host 拼装 URL 后直接发起 HTTP 请求
    # （目标项目 php/plugins/official_article/controllers/DefaultController.php:251 盲区）
    (re.compile(r'\$fullUrl\s*=\s*\$[a-z_][\w]*\s*\.\s*\$', re.IGNORECASE),
     "AGENT-SEC-48", "SSRF 转发面", "high",
     "检测到外部配置/用户可控 host 拼装 URL 后直接交由 HTTP 客户端请求，host 无协议与内网地址白名单校验，可被诱导访问内网/元数据地址",
     "对请求目标 host 做协议与内网地址白名单校验，禁止转发不可信 URL"),
    # 密钥透传：app_secret 等敏感密钥从外部参数直接透传进请求体
    # （目标项目 php/plugins/official_access_token/controllers/DefaultController.php:70 盲区）
    (re.compile(r'[\'"](?:\w*_)?secret[\'"]\s*=>\s*\$arguments', re.IGNORECASE),
     "AGENT-SEC-49", "密钥透传/日志泄露", "high",
     "检测到 app_secret 等敏感密钥从外部参数直接透传进请求体，密钥可被日志记录、中转泄露或在网络层明文暴露",
     "密钥由服务端配置注入，禁止从请求参数透传敏感密钥"),
    # SSRF 诱饵可达性：HTTP 请求目标 host 直接取自外部配置（环境变量），无协议白名单
    # （目标项目 php/plugins/official_article/controllers/DefaultController.php:251 盲区）
    (re.compile(r'getenv\s*\(\s*[\'"]\w+_(?:HOST|URL)[\'"]\s*\)', re.IGNORECASE),
     "AGENT-SEC-50", "SSRF 诱饵可达性", "medium",
     "检测到 HTTP 请求目标 host 直接取自外部配置（环境变量等），无协议/host 白名单，成为可被诱导的 SSRF 诱饵向量",
     "对配置来源的 host 做协议与内网地址校验后再发起请求"),
    # SSRF 出站请求面：PHP curl / file_get_contents URL / Yii sendRequest 直接发起请求
    # （目标项目 php/plugins/official_article/controllers/DefaultController.php:251 盲区）。
    # curl_init/curl_exec、file_get_contents(http://)、sendRequest 本身就是出站请求，
    # 单行命中即确证性信号，无需整文件 sink 确证。
    (re.compile(r'curl_(?:init|exec)\s*\(|file_get_contents\s*\(https?|sendRequest\s*\(', re.IGNORECASE),
     "AGENT-SEC-57", "SSRF 网络请求面（PHP）", "high",
     "检测到 PHP 出站请求（curl_init/curl_exec、file_get_contents URL、Yii sendRequest 等）直接使用外部/不可信 URL，无 scheme/host 白名单，可注入内网地址触发 SSRF",
     "为 PHP 网络请求目标添加 scheme/host 白名单，禁止访问内网/保留地址段"),
]

# Java：常见于 Spring 服务端（如 java_target 缺陷样本 J1-J6）
JAVA_PATTERNS = [
    # SQL 注入：SQL 语句字符串与外部变量直接拼接（未参数化）
    (re.compile(r'(?:SELECT|INSERT|UPDATE|DELETE)\s+[^"\';\n]*["\'][^"\';\n]*["\']\s*\+', re.IGNORECASE),
     "AGENT-SEC-60", "SQL 注入（Java 字符串拼接）", "high",
     "检测到 SQL 语句字符串与变量直接拼接（prepareStatement 前），用户可控输入未参数化可注入 SQL",
     "使用 PreparedStatement 占位符 ? + setString 参数化查询"),
    # Spring 未鉴权：@GetMapping/@PostMapping 敏感路径无鉴权注解。需整文件确认
    # 文件含 @RestController/@Controller 且无 @PreAuthorize/@Secured 等鉴权注解，
    # 见 _crosslang_file_signal_lines。
    (re.compile(r'@(?:Get|Post|Put|Delete|Request)Mapping\s*\(', re.IGNORECASE),
     "AGENT-SEC-61", "Spring 接口未鉴权（敏感路径）", "high",
     "检测到 Spring 控制器敏感路径（admin/delete/upload/export 等）未声明鉴权注解（@PreAuthorize/@Secured/@AuthenticationPrincipal），可被任意匿名调用",
     "为敏感接口添加 @PreAuthorize 或统一鉴权过滤器，禁止无鉴权开放管理端点"),
    # 不安全反序列化：ObjectInputStream.readObject 直接反序列化不可信输入
    (re.compile(r'ObjectInputStream|readObject\s*\(', re.IGNORECASE),
     "AGENT-SEC-62", "不安全反序列化（Java readObject）", "blocker",
     "检测到 ObjectInputStream.readObject() 直接反序列化输入，未做来源校验与类型白名单，可触发任意代码执行（RCE）",
     "使用安全反序列化方案（JSON + 类型白名单），校验字节流来源与签名"),
    # 路径穿越：目录字符串与外部变量拼接后 Paths.get/Files 读写（未规范化 ../）
    (re.compile(r'["\'][^"\']*/(?:uploads?|data|tmp|var|home|files?)[^"\']*["\']\s*\+|Paths\.get\s*\([^)]*\+', re.IGNORECASE),
     "AGENT-SEC-63", "路径穿越（Java 路径拼接）", "high",
     "检测到目录字符串与外部变量拼接后经 Paths.get/Files 读写，文件名未校验 ../ 可越界读写任意文件",
     "使用 Path.normalize() 并校验绝对路径位于允许根目录内"),
    # SSRF：Java HttpClient 出站请求直接使用外部/不可信 URL
    (re.compile(r'HttpClient|client\.send\s*\(|HttpRequest\.newBuilder\s*\([^)]*URI\s*\(', re.IGNORECASE),
     "AGENT-SEC-64", "SSRF 网络请求面（Java HttpClient）", "high",
     "检测到 Java HttpClient 出站请求（HttpRequest.newBuilder(URI(targetUrl))/client.send）直接使用外部/不可信 URL，无 scheme/host 白名单，可注入内网地址触发 SSRF",
     "为 Java 网络请求目标添加 scheme/host 白名单，禁止访问内网/保留地址段"),
    # 硬编码密钥：模型 API Key/口令/密钥字面量直接写在源码
    (re.compile(r'sk-[A-Za-z0-9]{8,}|(?:API_KEY|PASSWORD|SECRET|TOKEN|PASSWD)\s*=\s*["\'][^"\']{6,}', re.IGNORECASE),
     "AGENT-SEC-65", "硬编码密钥（Java 源码）", "high",
     "检测到模型 API Key/数据库口令/密钥以字面量硬编码在源码，提交仓库后即可被提取盗用",
     "改为环境变量/密钥管理服务注入，禁止源码硬编码"),
]

# 文件扩展名 → 跨语言模式组
CROSSLANG_GROUPS = {
    ".go": ("go", GO_PATTERNS),
    ".ts": ("node", NODE_PATTERNS),
    ".tsx": ("node", NODE_PATTERNS),
    ".js": ("node", NODE_PATTERNS),
    ".jsx": ("node", NODE_PATTERNS),
    ".mjs": ("node", NODE_PATTERNS),
    ".cjs": ("node", NODE_PATTERNS),
    ".vue": ("node", NODE_PATTERNS),
    ".php": ("php", PHP_PATTERNS),
    ".java": ("java", JAVA_PATTERNS),
}

# ─── 自主行为检测 ───
AUTONOMOUS_ACTION_PATTERNS = [
    # 自动重试逻辑（else 分支中有 retry/redo/recreate）
    (re.compile(r'else\s*:.*(?:retry|redo|recreate)', re.IGNORECASE),
     "AGENT-SEC-16", "无确认自动重试", "medium",
     "检测到 Agent 在操作失败后自动重试，未征求人类确认",
     "添加失败后人工确认环节，或限制重试次数"),
    # 基于结果的自动判断（低风险，常见模式）
    (re.compile(r'if\s+(?:not\s+)?(?:result|success|ok)\s*:', re.IGNORECASE),
     "AGENT-SEC-16", "无确认自动重试", "low",
     "检测到基于结果的条件判断，需人工确认是否为自动重试逻辑",
     "如果是自动重试，添加人工确认环节；如果是正常流程控制，可忽略"),
    # 自动修改自身配置（仅匹配 self.config/self.settings/self.params 赋值，不匹配通用 self.xxx = result.xxx）
    (re.compile(r'(?:self\.(?:config|settings|params)\s*(?:\[|\.update|\.set))', re.IGNORECASE),
     "AGENT-SEC-17", "自修改配置风险", "high",
     "检测到 Agent 可能修改自身配置或参数，行为不可预测",
     "配置应设为只读，或添加配置变更审计日志"),
    # 缺少 human-in-the-loop
    # 通过检测是否有 confirm/approve 相关函数来实现
]

# ─── SSRF 面检测 ───
# 聚焦"结果 URL 直接下载"这类 SSRF 面（fetch_url_text 等把外部/不可信 URL 直接
# 交给网络请求下载，无 scheme/host 白名单）。AGENT-SEC-09 只报通用网络请求，
# 此处针对"URL 直接抓取"给出更具体的 SSRF 面信号。
SSRF_PATTERNS = [
    (re.compile(r'(?:fetch_url_text|requests\.(?:get|post|put)|httpx\.(?:get|post)|urllib\.request\.urlopen)\s*\(', re.IGNORECASE),
     "AGENT-SEC-SSRF", "SSRF 面", "high",
     "检测到对外部/不可信 URL 直接发起网络请求下载，无 scheme/host 白名单校验，攻击者可注入内网地址触发 SSRF",
     "为网络请求目标添加 scheme/host 白名单，禁止访问内网/保留地址段"),
    # aiohttp / requests.Session 异步客户端下载（session.get）
    # 仅当第一参数看起来是 URL 变量/字面量时才判定为下载（排除 session.get("user_id")）
    (re.compile(r'(?:self\.)?session\.get\s*\(\s*(?:f?["\']https?://|[a-z_]*url[a-z_]*\b|[a-z_]*uri[a-z_]*\b)', re.IGNORECASE),
     "AGENT-SEC-SSRF", "SSRF 面（Session 下载）", "high",
     "检测到经 Session 客户端（aiohttp/requests.Session）对外部/不可信 URL 发起下载，无 scheme/host 白名单，可注入内网地址触发 SSRF",
     "约束下载 URL 的 scheme/host，禁止内网与保留地址段"),
    # 自定义下载封装（_get_bytes 等直接把不可信 URL 交给网络层）
    (re.compile(r'_get_bytes\s*\(', re.IGNORECASE),
     "AGENT-SEC-SSRF", "SSRF 面（自定义下载）", "high",
     "检测到自定义下载封装（_get_bytes 等）直接把外部/不可信 URL 交给网络下载，无 scheme/host 白名单，可被诱导访问内网/云元数据地址",
     "为下载封装的目标 URL 添加 scheme/host 白名单校验"),
    # 引用图 URL 直接原样转发透传（http/https/data 开头的不校验直接返回）
    (re.compile(r'_image_uri\s*\(', re.IGNORECASE),
     "AGENT-SEC-SSRF", "SSRF 面（引用图转发）", "high",
     "检测到引用图 URL 转换函数（_image_uri 等）对 http/https/data 开头的 URL 不校验直接转发透传，用户/LLM 可控 URL 形成 SSRF 诱饵向量",
     "对透传的上游 URL 做 scheme/host 白名单校验，禁止内网地址"),
    # Go net/http 出站请求（http.Get / http.NewRequest / client.Do）
    (re.compile(r'http\.(?:Get|Post|NewRequest|Head)\s*\(|\.Do\s*\(', re.IGNORECASE),
     "AGENT-SEC-SSRF", "SSRF 面（Go 网络请求）", "high",
     "检测到 Go net/http 出站请求（http.Get/NewRequest/client.Do 等）直接使用外部/不可信 URL，无 scheme/host 白名单，可注入内网地址触发 SSRF",
     "为 Go 网络请求目标添加 scheme/host 白名单，禁止访问内网/保留地址段"),
    # PHP 出站请求（curl / file_get_contents URL / Yii sendRequest）
    (re.compile(r'curl_(?:init|exec)\s*\(|file_get_contents\s*\(https?|sendRequest\s*\(', re.IGNORECASE),
     "AGENT-SEC-SSRF", "SSRF 面（PHP 网络请求）", "high",
     "检测到 PHP 出站请求（curl_init/curl_exec、file_get_contents URL、Yii sendRequest 等）直接使用外部/不可信 URL，无 scheme/host 白名单，可注入内网地址触发 SSRF",
     "为 PHP 网络请求目标添加 scheme/host 白名单，禁止访问内网/保留地址段"),
]

# ─── 路径穿越检测 ───
PATH_TRAVERSAL_PATTERNS = [
    # 用户可控输入直接拼接构造文件路径（data_dir/base_dir/... 与外部变量拼接，未净化
    # 规范化），../../ 可越权读写任意文件。
    (re.compile(r'(?:settings\.)?(?:data_dir|base_dir|root_dir|upload_dir|output_dir|storage_dir|work_dir)\s*(?:/|\\\\|\.join|os\.path\.join)\s*[a-z_][\w]*', re.IGNORECASE),
     "AGENT-SEC-PT", "路径穿越", "high",
     "检测到用户可控输入直接拼接文件路径，未做路径净化/规范化，../../ 可越权读写任意文件",
     "对输入做路径规范化并校验在允许根目录内，拒绝 .. 与绝对路径"),
    # os.path.join 拼接目录根 + 用户可控变量（history_root / *_root：变量直接拼接）
    (re.compile(r'os\.path\.join\s*\([^)]*(?:history_root|[a-z_]+_root)[^)]*\)', re.IGNORECASE),
     "AGENT-SEC-PT", "路径穿越", "high",
     "检测到 os.path.join 将目录根与用户可控变量拼接构造文件路径，未净化 ../ 可越权读写任意文件（配合 send_file 等可能直接外泄服务器文件）",
     "清洗用户可控 segment，校验拼接后绝对路径位于允许根目录内"),
    # os.path.join 中工作目录 + 插值（working_dir / character_dir 与 f-string 插值变量拼接）
    (re.compile(r'os\.path\.join\s*\([^)]*(?:working_dir|character_dir)[^)]*\{', re.IGNORECASE),
     "AGENT-SEC-PT", "路径穿越", "high",
     "检测到 os.path.join 将工作目录与 f-string 插值变量（如 LLM 生成的 identifier）拼接构造路径，未净化 ../ 可穿越工作目录",
     "对插值 segment 调用净化函数（如 safe_path_component），校验拼接后路径位于工作目录内"),
]

# ─── 反序列化检测 ───
DESERIALIZATION_PATTERNS = [
    (re.compile(r'pickle\.(?:load|loads)\s*\(', re.IGNORECASE),
     "AGENT-SEC-DESER", "反序列化任意代码执行", "blocker",
     "检测到从文件/不可信输入 pickle.load 反序列化，可触发任意代码执行",
     "禁止对不可信数据使用 pickle；改用安全的 JSON 序列化并校验数据来源"),
    # 知识图谱文件加载未校验来源：json/pickle 加载 knowledge_graph 相关路径文件。
    # （目标项目 retrieval.py:47 盲区）需整文件确认加载的是图谱文件，见 _scan_file。
    (re.compile(r'(?:json\.load|pickle\.load)\s*\(', re.IGNORECASE),
     "AGENT-SEC-54", "知识图谱文件加载未校验来源", "medium",
     "检测到对 knowledge_graph 相关路径文件执行 json/pickle 加载，未校验文件来源是否可信目录或是否被外部可写，知识图谱文件可被投毒污染检索结果",
     "校验图谱文件位于可信目录且仅由受信进程可写，加载前做来源与完整性校验"),
    # RAG 数据投毒·加载面：外部持久化数据文件（JSON/YAML/CSV/Excel/pickle）被
    # 加载后直接作为可信输入喂给图谱/检索/LLM 下游。AGENT-SEC-54 要求 json.load
    # 单行参数含 knowledge_graph 字样，但 目标项目 retrieval.py:47 是
    # json.load(f)（参数仅文件句柄），单行参数确认漏检；此处改为整文件级确认：
    # 文件内存在图谱/检索消费信号（from_dict/kg_search/load_knowledge_graph/
    # add_triple/hybrid_search 等）即判定，见 _scan_file。
    # 注意：yaml.safe_load 是安全 API（非 yaml.load 的任意对象构造），不作为
    # 风险信号，避免把安全用法误计入投毒面。
    (re.compile(r'(?:json\.load|yaml\.load|pickle\.load|pd\.read_csv|pd\.read_excel|pd\.read_json)\s*\(', re.IGNORECASE),
     "AGENT-SEC-58", "RAG 数据投毒（外部数据加载）", "high",
     "检测到外部持久化数据文件（JSON/YAML/CSV/Excel/pickle）被加载后直接作为可信输入喂给图谱/检索/LLM 下游，文件可被投毒污染检索与生成结果",
     "对外部数据文件做格式校验、内容过滤与来源可信度评估，加载后标记不可信并做消毒"),
    # RAG 数据投毒·消费面：图谱数据反序列化/检索消费（from_dict / data.get("triples") /
    # kg_search）直接消费外部加载的数据，无内容可信度校验。整文件级确认：文件内存在
    # 数据加载/序列化信号（json.load/json.loads/yaml.load/pickle.load/json.dump），
    # 见 _scan_file。
    (re.compile(r'(?:from_dict\s*\(|data\.get\s*\(\s*["\']triples|kg_search\s*\()', re.IGNORECASE),
     "AGENT-SEC-59", "RAG 数据投毒（图谱数据消费）", "high",
     "检测到图谱数据反序列化/检索消费（from_dict/data.get(\"triples\")/kg_search）直接消费外部加载的数据，无内容可信度校验，投毒数据可污染检索结果",
     "对图谱数据做来源校验与内容过滤，消费前验证数据完整性"),
]

# ─── 浏览器沙箱禁用检测 ───
BROWSER_SANDBOX_PATTERNS = [
    (re.compile(r'"--no-sandbox"|\'--no-sandbox\'', re.IGNORECASE),
     "AGENT-SEC-SANDBOX", "浏览器沙箱禁用", "medium",
     "检测到启动浏览器时禁用沙箱（--no-sandbox），访问不可信站点时放大浏览器漏洞利用面",
     "移除 --no-sandbox；如必须仅用于隔离专用环境并严格限制访问站点"),
]

# ─── 信息泄露检测（HTTP 接口返回内部路径/配置）───
INFO_LEAK_PATTERNS = [
    (re.compile(r'["\'](?:data_dir|config_path|gpt_config_path|[a-z_]*_path|[a-z_]*_dir)["\']\s*:\s*str\(\s*settings\.', re.IGNORECASE),
     "AGENT-SEC-LEAK", "信息泄露", "high",
     "检测到 HTTP 接口返回内部路径/配置信息（data_dir/config_path 等），向任意访问者泄露服务器结构",
     "从对外响应中移除内部路径与配置细节，仅返回必要字段"),
]

# ─── LLM 工具执行滥用 / SQL 注入链检测 ───
# LangChain 工具（ShellTool）与数据库链（SQLDatabaseChain）把 LLM 可控输入直接交
# 给危险执行器，无白名单/参数校验，构成 RCE / 注入入口。
LLM_EXEC_PATTERNS = [
    (re.compile(r'\bShellTool\b', re.IGNORECASE),
     "AGENT-SEC-LMEXEC", "命令执行（LangChain ShellTool）", "blocker",
     "检测到 LangChain ShellTool 将 LLM 可控 query 直接交给系统 Shell 执行，无白名单/参数校验，高危 RCE 入口",
     "为 shell 工具添加命令白名单与参数校验，或禁用该工具"),
    (re.compile(r'tool\.run\s*\(.*tool_input', re.IGNORECASE),
     "AGENT-SEC-LMEXEC", "命令执行（tool.run）", "blocker",
     "检测到 BaseToolOutput(tool.run(tool_input=query)) 将 LLM 可控输入直接交给命令执行工具，可执行任意系统命令",
     "限制 run 工具可执行命令集合，校验 tool_input 来源"),
    (re.compile(r'\bdb_chain\.invoke\s*\(', re.IGNORECASE),
     "AGENT-SEC-LMEXEC", "SQL 注入链（db_chain.invoke）", "high",
     "检测到直接 invoke LLM 生成的 SQL（SQLDatabaseChain），无 read_only 拦截或可被绕过的注入链，可执行任意 SQL",
     "为数据库链强制只读连接，对生成的 SQL 做语法与关键字白名单校验"),
    (re.compile(r'\bSQLDatabaseChain\b', re.IGNORECASE),
     "AGENT-SEC-LMEXEC", "SQL 注入链（SQLDatabaseChain）", "high",
     "检测到 SQLDatabaseChain 将自然语言转 SQL 并执行 LLM 生成的查询，read_only 拦截器可被注释/多语句绕过",
     "使用只读连接并校验生成的 SQL，禁止写操作"),
    # PromQL 查询注入：LLM 生成的 PromQL 经 split('?') 解析后，query_type 直接
    # 拼进 URL 路径并执行（requests.get），无语法/端点白名单校验，可注入任意查询
    (re.compile(r'promql\.split\s*\(\s*["\']\?["\']', re.IGNORECASE),
     "AGENT-SEC-42", "查询注入（PromQL）", "high",
     "检测到 LLM 生成的 PromQL 经 split('?') 解析后直接拼进 URL 路径并执行，query_type 可被注入任意查询或端点",
     "对 LLM 生成的 PromQL 做语法与端点白名单校验，禁止直接拼接 URL 路径"),
]

# ─── FastAPI 路由缺失鉴权依赖检测 ───
AUTH_MISSING_PATTERNS = [
    (re.compile(r'\bnew_router\s*\((?!.*dependencies)', re.IGNORECASE),
     "AGENT-SEC-AUTH", "API 路由缺失鉴权", "high",
     "检测到 FastAPI/路由 new_router() 未挂载鉴权依赖（dependencies=[Depends(verify_token)]），对应接口可被任意调用",
     "为路由挂载统一鉴权依赖 dependencies=[Depends(verify_token)]，避免逐接口遗漏"),
    # 认证绕过（空库放行）：数据库无 active API Key 时所有请求直接放行，
    # 生产空库即鉴权全关；require_api_key_scope 对未认证同样放行
    (re.compile(r'not\s+[^\n]*has_any_active_api_key', re.IGNORECASE),
     "AGENT-SEC-43", "认证绕过（空库放行）", "high",
     "检测到 has_any_active_api_key 空库放行逻辑：数据库无 active API Key 时所有请求直接通过，生产空库即鉴权全关",
     "禁止空库放行；未配置密钥时应拒绝服务或强制初始化密钥"),
    (re.compile(r'not\s+key_info\.get\s*\(\s*["\']authenticated["\']\s*\)', re.IGNORECASE),
     "AGENT-SEC-43", "认证绕过（未认证放行）", "high",
     "检测到未认证状态直接放行（require_api_key_scope 对 authenticated=False 不拦截），配合空库放行可完全绕过鉴权",
     "未认证请求应返回 401/403，禁止静默放行"),
]

# ─── 密钥明文落盘检测 ───
SECRET_WRITE_PATTERNS = [
    (re.compile(r'\bsave_env\s*\(', re.IGNORECASE),
     "AGENT-SEC-SECRET", "密钥明文落盘（save_env）", "high",
     "检测到 save_env 类方法将 API Key 等密钥明文写入 .env 落盘，密钥来源分散且明文存储",
     "将密钥交由密钥管理服务/环境变量注入，避免明文写入 .env 文件"),
    (re.compile(r'(?:write_text|open\s*\([^)]*\.env[^)]*["\']w)\(?.*', re.IGNORECASE),
     "AGENT-SEC-SECRET", "密钥明文落盘（.env 写入）", "high",
     "检测到向 .env 以写模式写入内容，若包含 API Key/密钥则明文落盘",
     "避免将密钥写入 .env；使用密钥管理服务并限制文件权限"),
    (re.compile(r'(?:API_KEY|api_key|API_SECRET|api_secret)\s*=\s*\{?self\.', re.IGNORECASE),
     "AGENT-SEC-SECRET", "密钥明文落盘（密钥写盘）", "high",
     "检测到 API Key 明文赋值/写入（如 f\"...API_KEY={self.xxx.api_key}\" 落盘），密钥明文存储",
     "使用密钥管理服务存储密钥，禁止明文落盘"),
]

# 资源/逻辑缺陷（Python）：PIL 批量句柄泄漏、外部长任务轮询无超时。
# 轮询无超时需整文件确认无 timeout/deadline 防护，见 _scan_file 内 AGENT-SEC-70 判断。
RESOURCE_LEAK_PATTERNS = [
    # PIL 列表推导式批量打开未 close（vimax image_generator_nanobanana_google_api.py:49
    #  `[Image.open(path) for path in reference_image_paths]` 盲区）。列表推导式内
    # 批量可迭代打开且未赋给 with 管理，句柄全部泄漏，单行命中即确证性信号。
    (re.compile(r'\[[^\n]*Image\.open\s*\([^)]*\)\s*for\s+', re.IGNORECASE),
     "AGENT-SEC-69", "PIL 批量句柄泄漏（列表推导式 Image.open）", "medium",
     "列表推导式内批量调用 Image.open(path) 打开图像但全部句柄未释放，文件描述符随调用次数累积泄漏，长时间运行将耗尽",
     "为每个打开句柄使用 with 上下文管理器，或在 finally 中遍历 close"),
    # 外部长任务轮询无超时上限（vimax video_generator_veo_google_api.py:100
    #  `while not operation.done` 无限轮询盲区）。while not <op>.done 轮询第三方
    # 异步任务，若无超时/放弃路径，第三方挂起时调用永久阻塞。需整文件确证无
    # timeout/deadline/max_wait/give_up 防护（见 _scan_file AGENT-SEC-70 判断）。
    (re.compile(r'while\s+not\s+\w+\.(?:done|status|ready|complete|finished)\s*:', re.IGNORECASE),
     "AGENT-SEC-70", "外部长任务轮询无超时上限", "high",
     "while not <外部操作>.done 无限轮询第三方长任务（视频/异步生成），无超时上限或放弃路径，第三方服务挂起时调用永久阻塞",
     "为轮询循环添加超时上限与放弃/降级路径（最大等待秒数、max_attempts、超时后抛错或返回降级结果）"),
]

# 排除模式（工具定义中的注释/文档字符串）
EXCLUDE_PATTERNS = [
    re.compile(r'^\s*#', re.IGNORECASE),
    re.compile(r'^\s*"""', re.IGNORECASE),
    re.compile(r'^\s*//', re.IGNORECASE),
]

# 扫描时排除的目录（集中定义，供文件遍历与项目级检查复用）
# 只排除当前目录名，不影响路径中包含该词的项目。测试目录：测试代码常含
# 占位符密钥（EMPTY）、调试打印，且不进入生产，默认排除以避免 PII/日志类
# 误报。如需审计测试代码可显式加入。
EXCLUDE_DIRS = {
    "__pycache__", "node_modules", ".git", "venv", ".venv", "env",
    "Lib", "lib", "lib64", "site-packages", "dist-packages",
    "third_party", ".gitnexus", "data", "docs", "reports",
    "cache", "coderef-report", "logs", "build", "dist",
    "tests", "test", "e2e",
}

# ─── 防御层级韧性检测（检查"缺失"的防御模式，而非"存在"的风险） ───
RESILIENCE_GAP_CHECKS = [
    # 重试退避 —— 检测 tenacity / @retry / exponential backoff
    {
        "id": "AGENT-RESILIENCE-01",
        "name": "缺少重试退避",
        "severity": "high",
        "detail": "未检测到 tenacity/@retry/指数退避等重试机制，LLM API 调用遇到暂时性故障（429/503/超时）会直接失败",
        "suggestion": "使用 tenacity 库添加重试：@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=1, max=10))",
        "patterns": [
            re.compile(r'tenacity|from\s+tenacity', re.IGNORECASE),
            re.compile(r'@retry\b', re.IGNORECASE),
            re.compile(r'stop_after_attempt|wait_exponential|retry_if_exception', re.IGNORECASE),
        ],
    },
    # 异常过滤 —— 区分"该重试"和"不该重试"的异常
    {
        "id": "AGENT-RESILIENCE-02",
        "name": "缺少异常分类过滤",
        "severity": "medium",
        "detail": "未检测到 retry_if_exception_type 或按异常类型分类处理，遇到不可重试错误（如 401 认证失败）也会重试，浪费资源",
        "suggestion": "使用 retry_if_exception_type((RateLimitError, APITimeoutError, APIError)) 区分可重试和不可重试异常",
        "patterns": [
            re.compile(r'retry_if_exception_type', re.IGNORECASE),
            re.compile(r'except\s+\(.*Timeout.*Error.*\).*retry', re.IGNORECASE),
        ],
    },
    # 模型回退 —— 主模型挂了有备胎
    {
        "id": "AGENT-RESILIENCE-03",
        "name": "缺少模型回退",
        "severity": "high",
        "detail": "未检测到 LLM 模型注册表或回退机制，主模型不可用时服务完全中断",
        "suggestion": "实现 LLMRegistry 注册表 + 环形索引轮换：try 主模型 → except 切备胎 → 确保总有一个可用",
        "patterns": [
            re.compile(r'(?:LLMRegistry|ModelRegistry|llm_registry|fallback.*llm|backup.*model)', re.IGNORECASE),
            re.compile(r'try:.*(?:chat|completion|generate).*except.*(?:chat|completion|generate)', re.IGNORECASE),
            re.compile(r'next_index\s*=\s*\(.*\+\s*1\)\s*%\s*len', re.IGNORECASE),
        ],
    },
    # 上下文截断 —— 防止 token 炸了
    {
        "id": "AGENT-RESILIENCE-04",
        "name": "缺少上下文截断",
        "severity": "high",
        "detail": "未检测到 trim_messages 或上下文窗口管理，多轮对话中 token 可能无限增长导致 API 错误或费用暴涨",
        "suggestion": "实现上下文截断：trim_messages(strategy=\"last\", max_tokens=2000) 或滑动窗口 + 摘要压缩",
        "patterns": [
            re.compile(r'trim_messages|trim_context|context_window|max_context', re.IGNORECASE),
            re.compile(r'max_tokens\s*=\s*\d{3,4}', re.IGNORECASE),
            re.compile(r'(?:messages|context)\s*=\s*(?:messages|context)\s*\[-?\d+:\]', re.IGNORECASE),
        ],
    },
    # 异步记忆 —— 存记忆不阻塞响应
    {
        "id": "AGENT-RESILIENCE-05",
        "name": "缺少异步记忆存储",
        "severity": "medium",
        "detail": "未检测到 asyncio.create_task 或异步记忆存储，保存记忆时可能阻塞主流程响应",
        "suggestion": "使用 asyncio.create_task(memory.add(...)) 异步存储记忆，避免阻塞 LLM 响应",
        "patterns": [
            re.compile(r'asyncio\.create_task|create_task\(', re.IGNORECASE),
            re.compile(r'memory\.(?:add|save|store)', re.IGNORECASE),
        ],
    },
    # 流式响应 —— 用户不用干等
    {
        "id": "AGENT-RESILIENCE-06",
        "name": "缺少流式响应",
        "severity": "low",
        "detail": "未检测到 StreamingResponse/SSE/stream=True，用户可能长时间等待完整响应",
        "suggestion": "使用 StreamingResponse 或 stream=True 参数实现流式输出，改善用户体验",
        "patterns": [
            re.compile(r'StreamingResponse|stream\s*=\s*True|text/event-stream|ServerSentEvent', re.IGNORECASE),
            re.compile(r'streaming\s*=|stream\s*:\s*True|yield\s+.*chunk', re.IGNORECASE),
        ],
    },
    # 连接池 —— 防止连接断了不知道
    {
        "id": "AGENT-RESILIENCE-07",
        "name": "缺少连接池探活",
        "severity": "medium",
        "detail": "未检测到 pool_pre_ping/pool_recycle 等连接池配置，数据库连接断开后可能长时间不可用",
        "suggestion": "配置连接池：pool_pre_ping=True, pool_recycle=1800，确保连接断开后自动重建",
        "patterns": [
            re.compile(r'pool_pre_ping|pool_recycle|create_engine.*pool', re.IGNORECASE),
        ],
    },
    # 状态持久化 —— 服务器重启不丢状态
    {
        "id": "AGENT-RESILIENCE-08",
        "name": "缺少状态持久化",
        "severity": "medium",
        "detail": "未检测到 checkpoint/saver 等状态持久化机制，服务器重启后对话状态丢失",
        "suggestion": "使用 checkpoint（如 AsyncPostgresSaver）定期保存状态，确保重启后可恢复",
        "patterns": [
            re.compile(r'checkpoint|Saver|saver|state\.persist|save_state|load_state', re.IGNORECASE),
            re.compile(r'AsyncPostgresSaver|SqliteSaver|MemorySaver', re.IGNORECASE),
        ],
    },
    # 可观测性 —— 出问题能定位
    {
        "id": "AGENT-RESILIENCE-09",
        "name": "缺少可观测性",
        "severity": "medium",
        "detail": "未检测到 Prometheus metrics / Counter/Histogram 等可观测性指标，出问题时难以定位根因",
        "suggestion": "添加 Prometheus metrics：Counter 统计调用次数，Histogram 统计延迟，Labels 区分模型/状态",
        "patterns": [
            re.compile(r'Counter|Histogram|Gauge|prometheus_client|prometheus', re.IGNORECASE),
            re.compile(r'metrics\s*=|\.observe\(|\.inc\(|\.set\(', re.IGNORECASE),
        ],
    },
    # 日志上下文 —— 日志带用户 ID 方便排查
    {
        "id": "AGENT-RESILIENCE-10",
        "name": "缺少日志上下文",
        "severity": "low",
        "detail": "未检测到 bind_context/structured logging 等日志上下文绑定，排查问题时无法关联用户请求",
        "suggestion": "使用 bind_context(subject_id=subject) 或 structlog 绑定请求上下文，日志自动带用户 ID",
        "patterns": [
            re.compile(r'bind_context|structlog|extra\s*=\s*\{.*(?:user_id|subject_id|request_id)', re.IGNORECASE),
            re.compile(r'logger\.bind\(|logging\.LoggerAdapter', re.IGNORECASE),
        ],
    },
]

# 自身/同级检测器豁免清单：这些文件是规则签名与规则描述文本密集的检测器
# （正则字面量、危险 API 名、detail/suggestion 文本），agent 检测器跨扫它们
# 只会产生规则文本自命中（如 governance_audit 的 DANGEROUS_FUNCTIONS 描述、
# resource_gap_detector 的 dynamic_patterns 规则文本），无真实风险含义。
# 各检测器只豁免自身及同类检测器文件；_check_resilience_gaps 的项目级
# 存在性统计不豁免（否则规则文本不再满足防御模式会新增韧性缺口条目）。
SELF_EXEMPT_FILES = frozenset({
    "agent_security_auditor.py",
    "governance_audit.py",
    "resource_gap_detector.py",
})

# ─── AGENT-SEC-08 / SEC-06 误报抑制辅助 ───

# SEC-08 只读 SQL 首词集合：命中即降级为 low 提示（不再按"写入"报 high）
_SEC08_READONLY_SQL_HEADS = ("SELECT", "PRAGMA", "EXPLAIN")

# SEC-06 自管产物信号：路径参数（含回溯到的赋值来源）命中即降级为 low 提示。
# 注意避免裸 "temp"（会撞 template）；tmp/_tmp/tmp 前后缀、cache、checkpoint、
# __pycache__、write_probe、tempfile/mkstemp 产物、".tmp" 后缀均视为自管产物。
# 追加工具自产管理文件信号：运行时生成的白名单（whitelist）、Last-good 备份
# （last_good/lastgood）、.bak/backup 备份、coderef-wiki 产出的 wiki 文档
# （MODULES 目录下的 *.md）——删除这些是缓存/产物清理，不是删用户源文件。
_SEC06_SELF_MANAGED_RE = re.compile(
    r'(?:__pycache__|\bpycache\b|\b\w*cache\w*\b|\b\w*ckpt\w*\b|\bcheckpoint\w*\b'
    r'|\bwrite_probe\w*\b|\btempfile\b|\bmkstemp\b|\.tmp\b|\btmp\w*\b|\b\w*_tmp\b'
    r'|\bwhitelist\w*\b|\w*last[_-]?good\w*\b|\.bak\b|\bbackups?\b|\bcoderef-wiki\b'
    r'|\bMODULES\b)',
    re.IGNORECASE)

# SEC-08 自管缓存库目录信号：连接对象闭包回溯文本命中即视为自管库。
# code_knowledge：CodeRef 自管知识库（code_knowledge.db），与 cache 同级
# 的自管数据目录信号；coderef[-_]\w* 覆盖 coderef-report/coderef-wiki/
# coderef_audit 等全部自产目录/文件前缀。
_SEC08_SELF_DB_RE = re.compile(
    r'(?:\b\w*cache\w*\b|coderef[-_]\w*|gitnexus|memory_state|code_knowledge)',
    re.IGNORECASE)

# SEC-08 自管产物/报告输出信号：文件写入（.write/.save）目标路径经符号
# 闭包回溯命中即降级 low。与 _SEC06_SELF_MANAGED_RE 同源（缓存/临时/
# 备份/白名单），追加报告产物（*report*、.html）、coderef-* 自产前缀、
# 跨进程锁文件（.lock）。信号只认字符串字面量（_extract_string_literals
# 配对扫描），防止闭包展开卷入无关文本时标识符撞词（如函数参数
# gap_report、变量 cache_file、函数名 _save_last_good）造成误降——
# 自管目录/文件名在代码中以字面量形态出现才有回溯意义。临时文件句柄
# （tempfile.mkstemp/mkdtemp）另由句柄来源特判覆盖（调用形态无字面量）。
# 注意不收裸 "temp"（会撞 template）。
_SEC08_WRITE_LITERAL_RE = re.compile(
    r'(?:__pycache__|coderef[-_]\w*|\w*report\w*|\.html\b|\.md\b|\.lock\b|\.tmp\b'
    r'|\btmp\w*\b|\bwrite_probe\w*|\bcheckpoint\w*|\bwhitelist\w*'
    r'|\w*last[_-]?good\w*|\.bak\b|\bbackups?\b|\bcode_knowledge\b|config\.json)',
    re.IGNORECASE)

# 临时文件 API（句柄直接来自临时文件，写入必然是自管临时产物）
_SEC08_TMP_HANDLE_RE = re.compile(r'\btempfile\s*\.\s*mk[ds]temp\b')

# 符号闭包展开停用词：模块/内建/关键字与常见调用名，不作为回溯种子
_CLOSURE_STOPWORDS = frozenset({
    "self", "return", "None", "True", "False", "str", "int", "len", "open",
    "list", "dict", "set", "tuple", "float", "bool", "time", "json", "re",
    "os", "sys", "sqlite3", "connect", "execute", "commit", "cursor",
    "print", "isinstance", "range", "enumerate", "Exception", "Optional",
    "Path", "dirname", "abspath", "basename", "join", "exists", "makedirs",
    "import", "from", "with", "class", "def", "try", "except", "finally",
    "else", "elif", "while", "for", "not", "and", "or", "raise", "yield",
    "pass", "break", "continue", "global", "lambda", "assert", "del",
})

# ─── AGENT-SEC-PT / SSRF / SECRET 误报抑制辅助 ───

# SEC-PT 静态词：路径构造常用模块/函数/方法名与关键字，不参与"用户可控"判定
_PT_STATIC_WORDS = frozenset({
    "os", "path", "join", "Path", "str", "dirname", "abspath", "basename",
    "splitext", "expanduser", "normpath", "realpath", "parent", "name",
    "stem", "suffix", "resolve", "return", "self", "f", "r",
})

# SEC-PT 用户输入痕迹：赋值回溯文本命中即判非常量（保持 high）
_PT_UNSAFE_RE = re.compile(
    r'(?:\binput\s*\(|\brequest|\bargv|\benviron\b|\bgetenv|\.form\b'
    r'|\bparams\[|\brecv|\breadline|\bcookies?\b)', re.IGNORECASE)

# SSRF 本地端点：请求目标命中即降级（本地服务，无外部 SSRF 面）
_SSRF_LOCAL_RE = re.compile(r'localhost|127\.0\.0\.1|0\.0\.0\.0|::1', re.IGNORECASE)

# SECRET 落盘调用：本行或后 2 行命中则维持原报
_SECRET_WRITE_RE = re.compile(
    r'\.write\s*\(|write_text\s*\(|open\s*\([^)]*[\'"][wa]b?[\'"]|json\.dump')

# 需要整文件上下文确证（而非单行命中即报警）的跨语言规则
_CROSSLANG_FILE_LEVEL_RULES = frozenset(
    {"AGENT-SEC-48", "AGENT-SEC-50", "AGENT-SEC-51", "AGENT-SEC-52", "AGENT-SEC-53", "AGENT-SEC-55", "AGENT-SEC-61",
     "AGENT-SEC-66", "AGENT-SEC-67", "AGENT-SEC-68"}
)

# ─── 参数透传失效检测（AGENT-SEC-27） ───
# 检测「函数签名声明了参数 X，但函数体从未使用 X，而是从 config/cred/settings
# 等配置容器读取同名值」——即工具参数被配置静默覆盖，调用方传入的实参永远不生效。
# 典型场景：Hermes delegate_task 的 `model=creds["model"]`（参数 model 被 delegation
# config 覆盖）。该缺陷是运行时语义矛盾，逐行正则在 _scan_file 里不可靠，需 AST 级分析。
CONFIG_CONTAINER_HINTS = (
    "config", "conf", "cfg", "cred", "settings", "setting",
    "env", "environ", "param", "params", "context",
    "opts", "options",
)

# ─── 报告渲染（to_report 的分段 helper 共用的类别元数据） ───
# 类别键 → 中文类别名（"风险类别汇总"表与"详细风险列表"段标题共用同一映射）
CATEGORY_NAMES = {
    "prompt_injection": "提示注入",
    "context_manipulation": "上下文操纵",
    "tool_misuse": "工具滥用",
    "budget": "预算/资源耗尽",
    "data_exfil": "数据泄露",
    "pii_leak": "PII泄露",
    "security_config": "安全配置",
    "autonomous": "自主行为",
    "param_shadow": "参数透传失效",
    "knowledge": "知识投毒",
    "resilience_gap": "防御层级韧性缺口",
    "deserialization": "反序列化",
    "ssrf": "SSRF",
    "path_traversal": "路径穿越",
    "browser_sandbox": "浏览器沙箱逃逸",
    "info_leak": "信息泄露",
    "resource_leak": "资源泄漏",
}

# 类别键 → "风险类别汇总"表"说明"列文案
CATEGORY_DETAILS = {
    "prompt_injection": "用户输入可能被注入到 LLM prompt 中，绕过安全限制",
    "context_manipulation": "外部内容可能操控 Agent 的上下文和决策",
    "tool_misuse": "Agent 可能滥用工具能力执行危险操作",
    "budget": "Agent 可能消耗过多资源（API费用、Token）",
    "data_exfil": "敏感数据可能通过 Agent 泄露到外部",
    "pii_leak": "个人身份信息（PII）可能泄露到日志或响应中",
    "security_config": "安全配置不当可能导致生产环境风险",
    "autonomous": "Agent 可能未经确认执行自主行为",
    "param_shadow": "函数参数被配置读取静默覆盖，调用方传入的实参不生效",
    "knowledge": "知识库/向量数据库可能被投毒",
    "resilience_gap": "缺失的防御层级，如重试退避、模型回退、可观测性等",
    "deserialization": "从文件/不可信输入反序列化，可能触发任意代码执行或知识图谱文件投毒",
    "ssrf": "对不可信 URL 发起网络请求，无 scheme/host 白名单可注入内网地址",
    "path_traversal": "用户可控输入拼接文件路径，未净化可能越权读写任意文件",
    "browser_sandbox": "浏览器/沙箱隔离不足，可能逃逸访问宿主资源",
    "info_leak": "敏感信息可能通过响应、日志或报错泄露",
    "resource_leak": "打开的资源（连接/文件/句柄）未及时关闭导致泄漏",
}

# 报告中类别的固定展示顺序（"风险类别汇总"表与"详细风险列表"段共用）
CATEGORY_ORDER = ["prompt_injection", "tool_misuse", "budget", "data_exfil", "pii_leak", "security_config", "context_manipulation", "autonomous", "param_shadow", "resilience_gap", "deserialization", "ssrf", "path_traversal", "browser_sandbox", "info_leak", "resource_leak"]


class AgentSecurityAuditor:
    """Agent 安全审计器"""

    # 拆分说明：以下规则常量表已整体移至模块级常量区（本文件上方），
    # 此处保留同名类属性引用，兼容 AgentSecurityAuditor.XXX 与 self.XXX 的既有访问。
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

        # 项目级防御层级韧性缺口检测（检查缺失的防御模式）
        resilience_gaps = self._check_resilience_gaps(project_path)
        risks.extend(resilience_gaps)

        # 过滤 cache 白名单（用户标记为可接受的安全风险）
        risks = [
            r for r in risks
            if not SharedFilter.is_security_whitelisted(r.risk_id, r.file_path, r.line_number)
        ]

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

# ==================== 模块级检测函数（自 AgentSecurityAuditor 提取，纯静态检测逻辑） ====================
def _is_excluded_dir(parent: str, name: str) -> bool:
    """路径感知的目录排除：排除 EXCLUDE_DIRS 中的目录，但 Java 生产包例外。

    Java 包结构常用 test 作包名（如 src/main/java/com/foo/test/），属生产代码；
    仅 src/test / src/test/java 等测试目录才应排除，避免把生产 Java 包误排除。
    """
    if name.startswith("."):
        return True
    if name not in EXCLUDE_DIRS:
        return False
    if name in ("test", "tests") and re.search(
            r'(?:src[\\/]main|main[\\/]java)', parent, re.IGNORECASE):
        return False
    return True


def _collect_target_files(project_path: str) -> List[str]:
    """收集所有受支持语言的待扫描文件（单一遍历，正则与 AST 扫描复用）。

    只排除当前目录名，不影响路径中包含该词的项目。Python 走完整 8 维 +
    参数透传 AST 扫描；Go/Node/PHP/Java 走各自跨语言安全模式组。跳过
    SELF_EXEMPT_FILES 中的检测器自身文件（规则文本自命中豁免）。
    """
    scan_files: List[str] = []
    for root, dirs, files in os.walk(project_path):
        dirs[:] = [d for d in dirs if not _is_excluded_dir(root, d)]
        for f in files:
            if f in SELF_EXEMPT_FILES:
                continue
            ext = os.path.splitext(f)[1].lower()
            if ext == ".py" or ext in CROSSLANG_GROUPS:
                scan_files.append(os.path.join(root, f))
    return scan_files


def _collect_docstring_lines(content: str) -> Set[int]:
    """用 AST 收集真正的 docstring 行号（模块/类/函数 docstring）。"""
    doc_lines: Set[int] = set()
    try:
        tree = ast.parse(content)
    except SyntaxError:
        return doc_lines
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            body = node.body
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) and isinstance(body[0].value.value, str):
                doc_node = body[0]
                for ln in range(doc_node.lineno, getattr(doc_node, "end_lineno", doc_node.lineno) + 1):
                    doc_lines.add(ln)
    return doc_lines


def _collect_noise_lines(content: str) -> Set[int]:
    """用 AST 收集日志/打印/异常等非 prompt 上下文行号。

    提示注入检测按 f-string 变量名（query/content/prompt 等）匹配，会把
    logger.info / print / raise 中的描述性消息误判为注入。这些行由 AST
    精确识别后排除，避免 AST docstring 修复暴露代码后引入误报。
    """
    noise: Set[int] = set()
    try:
        tree = ast.parse(content)
    except SyntaxError:
        return noise
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
                and isinstance(node.func.value, ast.Name) and node.func.value.id == "logger":
            for ln in range(node.lineno, getattr(node, "end_lineno", node.lineno) + 1):
                noise.add(ln)
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "print":
            for ln in range(node.lineno, getattr(node, "end_lineno", node.lineno) + 1):
                noise.add(ln)
        elif isinstance(node, ast.Raise):
            for ln in range(node.lineno, getattr(node, "end_lineno", node.lineno) + 1):
                noise.add(ln)
    return noise


def _scan_file(filepath: str, content: str, ext: str = ".py") -> List[AgentSecurityRisk]:
    """扫描单个文件（content 由调用方统一读取，避免二次 I/O）。

    ext 决定路由：`.py` 走完整 8 维（含 docstring 跟踪 / logger 判断等 Python 语义）；
    Go/Node/PHP 走各自跨语言安全模式组（逐行正则，无 Python 语义）。
    """
    if ext != ".py":
        return _scan_crosslang_file(filepath, content, ext)
    risks = []
    lines = content.splitlines()
    # 日志/打印/异常等非 prompt 上下文行（AST 精确识别），提示注入/上下文操纵
    # 检测只对这些"噪声"行做豁免：logger.info/print/raise 中的描述性消息不判注入
    noise_lines = _collect_noise_lines(content)
    # 用 AST 精确识别真实 docstring 行（模块/类/函数 docstring），避免把多行
    # 字符串赋值（如 TEMPLATE = """..."""）的结束符误判为 docstring 开始，
    # 导致其后的真实代码被当作 docstring 整段跳过（漏检）
    docstring_lines = _collect_docstring_lines(content)
    # RAG 数据投毒整文件级信号（AGENT-SEC-58/59）：加载面需文件内存在图谱/检索
    # 消费信号，消费面需文件内同时存在数据加载/序列化信号与图谱专用消费信号，
    # 避免把任意 json.load 或 Pydantic 通用 from_dict（如 platform_tools/schema.py）
    # 误判为投毒链。
    file_has_graph_consume = bool(re.search(
        r'(?:kg_search|load_knowledge_graph|knowledge_graph|add_triple|data\.get\s*\(\s*["\']triples|self\.triples)',
        content, re.IGNORECASE))
    file_has_data_load = bool(re.search(
        r'(?:json\.load|json\.loads|yaml\.load|pickle\.load|pd\.read_|json\.dump)',
        content, re.IGNORECASE))

    # 外部长任务轮询无超时（AGENT-SEC-70）整文件确证：文件存在 while not <op>.done
    # 轮询，但全文无 timeout/deadline/max_wait/give_up 等超时防护词才上报。
    # max_retries/retry_delay（429 重试）不算轮询超时防护，避免误放行无超时轮询。
    file_has_poll_timeout = bool(re.search(
        r'\b(?:timeout|deadline|max_wait_seconds?|give_up)\b|while\s+.*\b(?:timeout|deadline)\b',
        content, re.IGNORECASE))

    # SEC-58/59 自产数据消费信号（整文件级）：数据根指向工具自管目录
    # （cache/coderef-report/coderef-wiki/.gitnexus/data/memory_state 等，
    # 含 MEMORY_STATE 快照类常量）时，"RAG 投毒"威胁模型错配——加载的是
    # 本工具运行时自产的缓存/快照，不是外部可控输入，降级为 medium 人工确认。
    file_self_managed_data = bool(re.search(
        r'(?:memory_state|coderef-report|coderef-wiki|gitnexus|\w*snapshot\w*'
        r'|(?:^|[\'"\\/])cache[\'"\\/]|_cache_|CACHE_)',
        content, re.IGNORECASE))

    for i, line in enumerate(lines, 1):
        stripped = line.strip()
        # 前导缩进长度：m.start() 是相对 stripped（去缩进后）的列，
        # _collect_call_args 起始行用原始行 lines[]，需偏移回原始行列号。
        indent_len = len(line) - len(line.lstrip())
        if not stripped:
            continue
        if i in docstring_lines:
            continue

        # 跳过注释
        if any(p.match(stripped) for p in EXCLUDE_PATTERNS):
            continue

        # 判断是否为 logger 行（PII 检测需要 logger 行，其他检测跳过）
        is_logger = bool(re.match(r'logger\.', stripped))
        # 判断是否为报告生成代码（lines.append 构建 Markdown 报告，不是真正的注入）
        is_report_gen = bool(re.match(r'lines\.append\(', stripped))
        # 判断是否为净化函数包裹的注入点（sanitize/escape/净化 等，视为已安全处理）
        is_sanitized = bool(
            re.search(r'(?:sanitize|escape|净化|_clean|neutralize|safe_)[a-z_\w]*\s*\(', stripped, re.IGNORECASE)
        )
        # 判断是否为错误/诊断消息返回（结构化数据，非 LLM prompt）。
        # 形如 {"error": f"..."} / "message": f"..." 的插值只是把选择器/状态拼进
        # 返回给调用方的错误文本，从未进入任何 prompt。排除含 role 键的聊天消息，
        # 后者（如 {"role":"user","message":f"..."}）仍是真实注入点，需继续检测。
        is_error_msg = bool(
            re.search(r'["\'](?:error|message)["\']\s*:\s*f?["\']', stripped)
            and not re.search(r'role\s*[:=]', stripped)
        )
        # 判断是否为 URL 路径拼接（f"{host}/api/v1/{var}"），构造网络请求而非 prompt。
        # 仅豁免确证的 HTTP(S) URL 构造；裸 "/" 分支会把 "Use /help for {x}" 等
        # prompt 误判为 URL 拼接而漏掉注入，故只保留 http(s):// 前缀限定。
        is_url_concat = bool(
            re.search(r'f["\'].*(?:https?://)\{', stripped, re.IGNORECASE)
        )

        # 检测所有维度
        for patterns, category_key in [
            (PROMPT_INJECTION_PATTERNS, "prompt_injection"),
            (CONTEXT_MANIPULATION_PATTERNS, "context_manipulation"),
            (TOOL_MISUSE_PATTERNS, "tool_misuse"),
            (BUDGET_EXHAUSTION_PATTERNS, "budget"),
            (DATA_EXFIL_PATTERNS, "data_exfil"),
            (PII_LEAK_PATTERNS, "pii_leak"),
            (SECURITY_CONFIG_PATTERNS, "security_config"),
            (AUTONOMOUS_ACTION_PATTERNS, "autonomous"),
            (SSRF_PATTERNS, "ssrf"),
            (PATH_TRAVERSAL_PATTERNS, "path_traversal"),
            (DESERIALIZATION_PATTERNS, "deserialization"),
            (BROWSER_SANDBOX_PATTERNS, "browser_sandbox"),
            (INFO_LEAK_PATTERNS, "info_leak"),
            (LLM_EXEC_PATTERNS, "tool_misuse"),
            (AUTH_MISSING_PATTERNS, "security_config"),
            (SECRET_WRITE_PATTERNS, "security_config"),
            (RESOURCE_LEAK_PATTERNS, "resource_leak"),
        ]:
            # PII 检测需要检查 logger 行，其他检测跳过 logger 行
            if category_key != "pii_leak" and is_logger:
                continue
            # 跳过报告生成代码（lines.append 构建 Markdown 报告）
            if is_report_gen:
                continue
            for pattern, risk_id, risk_name, severity, detail, suggestion in patterns:
                m = pattern.search(stripped)
                if m:
                    # 跳过模式定义自身（如类中的正则表达式定义）
                    if _is_pattern_definition(stripped, risk_name):
                        continue
                    # 提示注入/上下文操纵：噪声行（logger/print/raise 描述性消息）不明判注入
                    if category_key in ("prompt_injection", "context_manipulation") and i in noise_lines:
                        continue
                    # 提取完整调用参数（平衡括号，支持跨行/嵌套），避免单行匹配误报：
                    # AGENT-SEC-25 无 timeout；AGENT-SEC-AUTH 多行 router 调用挂载了 dependencies
                    if risk_id in ("AGENT-SEC-25", "AGENT-SEC-AUTH"):
                        args = _collect_call_args(lines, i - 1, stripped, m.start() + indent_len)
                        if risk_id == "AGENT-SEC-25" and re.search(r'timeout\s*=', args, re.IGNORECASE):
                            continue
                        if risk_id == "AGENT-SEC-AUTH" and re.search(r'dependencies\s*=', args, re.IGNORECASE):
                            continue
                    # 提示注入/上下文操纵：若注入点已被净化函数包裹，视为已安全处理
                    if is_sanitized and category_key in ("prompt_injection", "context_manipulation"):
                        continue
                    # 提示注入：若是错误/诊断消息返回（结构化数据，非 prompt），跳过
                    if is_error_msg and category_key in ("prompt_injection", "context_manipulation"):
                        continue
                    # 提示注入：若是 URL 路径拼接（构造网络请求），非 prompt，跳过
                    if is_url_concat and category_key == "prompt_injection":
                        continue
                    # 知识图谱投毒：确证 json/pickle 加载的输入参数本身解析为
                    # knowledge_graph 相关路径（如 json.load(open(GRAPH_PATH))），而非
                    # 仅整文件出现 knowledge_graph 字样，避免把任意加载误判为图谱加载。
                    if risk_id == "AGENT-SEC-54":
                        load_args = _collect_call_args(lines, i - 1, stripped, m.start() + indent_len)
                        if not re.search(
                                r'(?:knowledge_graph|knowledge-graph|graph_path|graph_file|kg_path|graph)\w*',
                                load_args, re.IGNORECASE):
                            continue
                    # RAG 数据投毒·加载面：整文件内存在图谱/检索消费信号才判定，
                    # 避免把任意 json.load/yaml.load 误判为投毒链加载。
                    if risk_id == "AGENT-SEC-58" and not file_has_graph_consume:
                        continue
                    # RAG 数据投毒·消费面：整文件内同时存在数据加载/序列化信号与
                    # 图谱专用消费信号才判定，避免把孤立 from_dict/kg_search 或
                    # Pydantic 通用反序列化误判为投毒链消费。
                    if risk_id == "AGENT-SEC-59" and not (file_has_data_load and file_has_graph_consume):
                        continue
                    # 外部长任务轮询无超时：整文件存在 timeout/deadline 防护则跳过
                    if risk_id == "AGENT-SEC-70" and file_has_poll_timeout:
                        continue
                    # ─── 误报抑制/降级（按规则定制，见各 _refine_* 方法注释） ───
                    eff_severity, eff_detail = severity, detail
                    if risk_id == "AGENT-SEC-08":
                        verdict = _refine_sec08(lines, i - 1, stripped, indent_len, m, filepath)
                        if verdict is None:
                            continue
                        if verdict:
                            eff_severity, eff_detail = verdict
                    elif risk_id == "AGENT-SEC-06":
                        verdict = _refine_sec06(lines, i - 1, stripped, indent_len, m)
                        if verdict:
                            eff_severity, eff_detail = verdict
                    elif risk_id == "AGENT-SEC-PT":
                        verdict = _refine_secpt(lines, i - 1, stripped, indent_len, m)
                        if verdict:
                            eff_severity, eff_detail = verdict
                    elif risk_id == "AGENT-SEC-SSRF":
                        verdict = _refine_ssrf(lines, i - 1, stripped, indent_len, m)
                        if verdict:
                            eff_severity, eff_detail = verdict
                    elif risk_id == "AGENT-SEC-SECRET":
                        verdict = _refine_secret(lines, i - 1, stripped)
                        if verdict:
                            eff_severity, eff_detail = verdict
                    elif risk_id in ("AGENT-SEC-58", "AGENT-SEC-59"):
                        # CLI 参数显式指定的本地文件（open(args.*)）：用户传参
                        # 即人工确认，与 SEC-08 的 args.* 豁免同口径，降级。
                        _win58 = " ".join(
                            x.strip() for x in lines[max(0, i - 6):i])
                        if re.search(r'\bopen\s*\(\s*args\.', _win58):
                            eff_severity = "medium"
                            eff_detail = detail + "（数据文件为 CLI 参数显式指定（open(args.*)），用户传参即人工确认，非不受信外部数据；需人工复核）"
                        elif file_self_managed_data:
                            eff_severity = "medium"
                            eff_detail = detail + "（数据源为本地自产缓存/快照目录（cache/coderef-report/memory_state 等），非外部输入，威胁模型错配；需人工确认）"
                    elif risk_id == "AGENT-SEC-01" and category_key == "prompt_injection":
                        # ① 全大写常量名（如 MAX_UNITS_PER_PROMPT）不是用户输入，
                        #    剔除后不再命中即视为常量撞名，跳过
                        caps_removed = re.sub(r'\b[A-Z][A-Z0-9_]{2,}\b', ' ', stripped)
                        if not pattern.search(caps_removed):
                            continue
                        # ② dict 字面量键（如 {"content": ...} 的 "content"）是结构
                        #    字段名而非注入变量，跳过
                        if re.search(
                                r'["\'](?:user_input|user_message|query|question|prompt|input|content)["\']\s*:',
                                stripped):
                            continue
                        # ③ 剩余命中为纯关键词撞名候选，降级为人工确认项
                        eff_severity = "medium"
                        eff_detail = detail + "（纯关键词撞名候选，需人工确认是否进入 LLM 调用）"
                    risks.append(AgentSecurityRisk(
                        risk_id=risk_id,
                        risk_name=risk_name,
                        category=category_key,
                        severity=eff_severity,
                        file_path=filepath,
                        line_number=i,
                        line_content=stripped[:150],
                        detail=eff_detail,
                        suggestion=suggestion,
                    ))

    # 同一源行、同一风险族/类别保留最高严重性的一条，避免同行同类重复罗列。
    # 多个规则对同一行命中（如同一行 SSRF+INFO_LEAK 各模式）只取最严重项。
    best: dict = {}
    for r in risks:
        key = (r.line_number, r.category)
        cur = best.get(key)
        if cur is None or SEVERITY_ORDER.get(r.severity, 99) < SEVERITY_ORDER.get(cur.severity, 99):
            best[key] = r
    return list(best.values())


def _lookup_var_rhs(lines: List[str], cur_idx: int, var: str, max_up: int = 60) -> str:
    """从 cur_idx（0-based 当前行）向上回溯变量的全部赋值，拼接返回。

    收集函数内（遇到 def/class/装饰器行即停止）该变量的所有 `=` / `+=` 赋值行
    及其后 2 行（覆盖 `x = os.path.join(\\n ...)` 跨行写法），按"最远在前"拼接：
    SEC-08 判断 SQL 首词时需取基始赋值（如 `sql = "SELECT..."`），而不是更近的
    `sql += " AND ..."` 增量；SEC-06 判定自管产物信号只需任一赋值命中。
    找不到返回空串。
    """
    if not var or not re.fullmatch(r'[A-Za-z_]\w*', var):
        return ""
    assign_re = re.compile(r'(?<![\w.])' + re.escape(var) + r'\s*(?:\+?=)\s*(?!=)')
    found: List[str] = []
    j = cur_idx - 1
    steps = 0
    while j >= 0 and steps < max_up:
        line = lines[j]
        if re.match(r'\s*(?:def|class|@)\s', line):
            break
        if assign_re.search(line):
            found.append(" ".join(s.strip() for s in lines[j:j + 3]))
        j -= 1
        steps += 1
    return " ".join(reversed(found))  # 最远（基始）赋值在前


def _refine_sec08(lines: List[str], cur_idx: int, stripped: str,
                  indent_len: int, m, filepath: str = "") -> "tuple | None":
    """AGENT-SEC-08（无确认写入）误报抑制。

    返回 None 表示跳过不报；返回 ("low", detail) 表示降级为 low 提示；
    返回 () （空 tuple，falsy）表示维持原报。

    - 命中 `.write(` 且调用目标为进程管道（stdin/stdout/stderr，如
      proc.stdin.write）：进程间协议通信，不是文件保存，跳过。
    - 命中 `.execute(`：提取 SQL 首词（字面量优先；参数以变量开头时回溯变量
      赋值再取字面量），SELECT/PRAGMA/EXPLAIN 开头为只读查询，降级 low；
      CREATE 开头为幂等 schema 初始化（CREATE TABLE/INDEX IF NOT
      EXISTS），不写入业务数据，降级 low。DELETE/INSERT/UPDATE 等写入
      语句：若目标库经符号闭包回溯属于自管 cache 目录（如
      cache/kg/{md5}.db），降级 low"自管缓存库写入"；否则维持原报。
    - `.commit(`：目标库属于自管 cache 目录时降级 low；否则维持原报。
    - `.write(`（非管道）/`.save(`：写入目标为 CLI 参数（open(args.*)）
      或路径经符号闭包回溯命中自管产物/报告输出信号（cache、coderef-*、
      *report*、tempfile/mkstemp、.html、.lock 等，见
      _SEC08_WRITE_SIGNAL_RE）时降级 low；写入用户项目路径（无自管
      信号，如 manifest 目标、git hook）维持原报。
    """
    method = m.group(0).lstrip(".").lower()  # 如 "execute(" / "write("
    if method.startswith("write") and re.search(
            r'\.\s*std(?:in|out|err)\s*\.\s*write\s*\(', stripped):
        return None
    if method.startswith("save") or method.startswith("write"):
        verdict = _sec08_write_target(lines, cur_idx, stripped, m, indent_len)
        return verdict if verdict is not None else ()
    if method.startswith("execute"):
        args = _collect_call_args(lines, cur_idx, stripped, m.start() + indent_len, keep_parens=True)
        sql_literal = _extract_sql_literal(args, lines, cur_idx)
        head = sql_literal.strip().split(None, 1)[0].upper() if sql_literal.strip() else ""
        if head in _SEC08_READONLY_SQL_HEADS:
            return ("low",
                    "检测到数据库/文件写入调用，但 SQL 为只读查询（" + head +
                    "），已降级为提示；如后续引入写语句需人工复核")
        if head == "CREATE":
            return ("low",
                    "检测到数据库写入调用，但 SQL 为 CREATE TABLE/INDEX（幂等 "
                    "schema 初始化，不写入业务数据），已降级为提示")
        if _sec08_self_managed_db(lines, cur_idx, stripped, m, indent_len, filepath):
            return ("low",
                    "检测到数据库写入调用，但目标库为工具自管缓存库"
                    "（cache 目录下的 .db，如 cache/kg/{hash}.db），"
                    "非用户数据库，已降级为提示；写入内容仍建议人工复核")
        return ()
    # commit：目标库为自管缓存库时降级
    if method.startswith("commit") and _sec08_self_managed_db(
            lines, cur_idx, stripped, m, indent_len, filepath):
        return ("low",
                "检测到数据库提交调用，但目标库为工具自管缓存库"
                "（cache 目录下的 .db），非用户数据库，已降级为提示")
    return ()


@staticmethod
def _extract_string_literals(blob: str, max_span: int = 120) -> str:
    """配对扫描提取 blob 中的字符串字面量内容（跨双/单引号交替）。

    闭包文本由"行 + 后 2 行"拼接而成，跨行三引号字符串被截断后会产生
    孤立引号；若用简单正则提取，孤立引号会一直吞并到下一段文本的引号，
    把无关的 def 行整段误当作"字面量内容"。这里顺序扫描：每个引号只在
    max_span 内寻找同种配对引号，找不到配对就当作孤立引号跳过。
    三引号（\"\"\"abc\"\"\") 会按空串 + abc + 空串正确取出。
    """
    out: List[str] = []
    i, n = 0, len(blob)
    while i < n:
        ch = blob[i]
        if ch in ('"', "'"):
            j = blob.find(ch, i + 1)
            if j != -1 and j - i <= max_span:
                out.append(blob[i + 1:j])
                i = j + 1
                continue
        i += 1
    return " ".join(out)


def _sec08_write_target(lines: List[str], cur_idx: int, stripped: str,
                        m, indent_len: int) -> "tuple | None":
    """SEC-08 文件写入（.write/.save）的目标确认。

    定位写入句柄来源：写入点向上 15 行窗口内的 open(/fdopen( 行取第一
    参数作闭包种子；`os.write(fd, ...)` 取第一参数（fd）。判定：
    - 路径参数以 args. 开头：CLI 显式指定的输出文件，用户传参即人工
      确认，降级 low；
    - 句柄直接来自 tempfile.mkstemp/mkdtemp：临时文件写入，降级 low；
    - 路径标识符做符号闭包展开（2 层：种子赋值 → 赋值中变量的赋值，
      覆盖 filepath→out_dir→字面量、report_path→fn→字面量这类路径
      构造链；更深展开会把无关函数体/HTML 模板卷进来造成撞词误降），
      闭包与窗口内的字符串字面量（逐行配对提取，防止跨行三引号截断
      产生的孤立引号吞并相邻代码行）命中自管产物/报告输出信号
      （coderef-*、*report*、.html、.lock 等）时降级 low；窗口兜底
      覆盖路径变量来自循环/参数的断链（如 results["xx.html"] 渲染
      产物落盘）；
    - 其余（manifest 目标、git hook、配置恢复、用户项目源码路径等
      无字面量信号写入）返回 None 维持原报。
    """
    win = lines[max(0, cur_idx - 14):cur_idx + 1]
    ctx = " ".join(x.strip() for x in win)
    # ① CLI 参数输出：open(args.xxx, "w") —— 用户显式传参即人工确认
    if re.search(r'\bopen\s*\(\s*args\.', ctx):
        return ("low",
                "检测到文件写入调用，但目标为 CLI 参数显式指定的输出文件"
                "（args.*，用户传参即人工确认），已降级为提示")
    # ② 提取路径闭包种子
    seeds: List[str] = []
    om = re.search(r'\b(?:open|fdopen)\s*\(\s*([^,()]+)', ctx)
    if om:
        seeds.extend(re.findall(r'[A-Za-z_]\w*', om.group(1)))
    if re.match(r'\s*os\.write\s*\(', stripped):
        wm = re.search(r'\bos\.write\s*\(\s*([A-Za-z_][\w.]*)', stripped)
        if wm:
            seeds.extend(re.findall(r'[A-Za-z_]\w*', wm.group(1)))
    # ③ 句柄来源特判：fd/f 直接来自 tempfile.mkstemp/mkdtemp
    if _SEC08_TMP_HANDLE_RE.search(ctx):
        return ("low",
                "检测到文件写入调用，但句柄直接来自 tempfile.mkstemp/"
                "mkdtemp（自管临时文件，通常配合 os.replace 原子落盘），"
                "已降级为提示；最终目标路径仍建议人工复核")
    segments = _symbol_closure_segments(lines, seeds, max_depth=2)
    # 逐行提取字面量（行内配对；跨行三引号截断的孤立引号只影响所在行）
    lit_parts: List[str] = []
    for seg in [win] + segments:
        for ln in seg:
            lit_parts.append(_extract_string_literals(ln))
    literals = " ".join(lit_parts)
    if _SEC08_WRITE_LITERAL_RE.search(literals):
        return ("low",
                "检测到文件写入调用，但目标经回溯属于工具自管产物/报告输出"
                "（路径字面量含 coderef-*、*report*、.html、.lock 等信号），"
                "非用户项目源码，已降级为提示；写入内容仍建议人工复核")
    return None


def _sec08_self_managed_db(lines: List[str], cur_idx: int, stripped: str,
                           m, indent_len: int, filepath: str) -> bool:
    """判定 SEC-08 命中行的目标库是否为自管缓存库。

    提取调用接收对象（`.execute(`/`.commit(` 前的表达式，如 self._conn），
    做全文件符号闭包展开（赋值 RHS / 函数体，5 层），回溯到
    sqlite3.connect(db_path) → db_path 赋值 → 目录推算函数体，
    判定路径是否含 cache 等自管目录信号。按 (文件, 接收对象) 记忆化。
    """
    head = stripped[:m.start()] if m.start() <= len(stripped) else ""
    rm = re.search(r'([A-Za-z_][\w.]*)\s*$', head)
    receiver = rm.group(1) if rm else ""
    if not receiver:
        return False
    key = (filepath, receiver)
    cache = _SEC08_DB_CACHE
    if key in cache:
        return cache[key]
    sig = _symbol_closure_text(lines, re.findall(r'[A-Za-z_]\w*', receiver))
    # 参数中标识符的近程赋值也并入（覆盖 conn 由参数传入的情形）
    args = _collect_call_args(lines, cur_idx, stripped, m.start() + indent_len, keep_parens=True)
    for ident in set(re.findall(r'\b[A-Za-z_]\w*\b', args)):
        sig += " " + _lookup_var_rhs(lines, cur_idx, ident)
    verdict = bool(_SEC08_SELF_DB_RE.search(sig))
    cache[key] = verdict
    return verdict


def _symbol_closure_text(lines: List[str], seeds: List[str], max_depth: int = 5) -> str:
    """收集 seeds 符号在全文件中的赋值 RHS / 函数体文本（有限层闭包展开）。

    实现为 _symbol_closure_segments 的拼接视图；文本形态用于信号
    search（DB 路径回溯等）。行级形态见 _symbol_closure_segments。
    """
    return " ".join(" ".join(seg) for seg in
                    _symbol_closure_segments(lines, seeds, max_depth))


def _symbol_closure_segments(lines: List[str], seeds: List[str],
                             max_depth: int = 5) -> List[List[str]]:
    """符号闭包展开（行级）：返回涉及的代码段（每段为行列表）。

    每层对未处理的名字搜索：①赋值行 + 后 2 行（覆盖跨行写法）；环视
    只排除前接 word 字符，因此 `x.attr = ...` 属性赋值与 `d["attr"] = ...`
    字典键赋值也能回溯（如 r.report_path = os.path.join(...)）；②`self.`
    属性赋值行（同①，保留显式形态）；③`def name(` 函数体（到下一个
    def/class 或 30 行）；④name 作为函数参数出现的 def 行本身 + 后 2 行
    （收集参数默认值字面量，如 `def __init__(self, db_path=
    "code_knowledge.db")`，否则参数名无赋值行时回溯断链）。层间从新增
    文本提取新标识符继续展开，用于把调用点回溯到数据来源定义处
    （如 self._conn → sqlite3.connect(self._db_path) → _make_db_path()
    → _kg_dir() → "cache"）。保留行边界供调用方做逐行字面量提取。
    """
    segments: List[List[str]] = []
    seen: Set[str] = set()
    frontier = [s for s in seeds if s and len(s) >= 2]
    for _ in range(max_depth):
        if not frontier:
            break
        for name in frontier:
            if name in seen or name in _CLOSURE_STOPWORDS:
                continue
            seen.add(name)
            esc = re.escape(name)
            assign_re = re.compile(r'(?<!\w)' + esc + r'\s*(?:\+?=)\s*(?!=)')
            attr_re = re.compile(r'self\s*\.\s*' + esc + r'\s*(?:\+?=)\s*(?!=)')
            def_re = re.compile(r'\bdef\s+' + esc + r'\s*\(')
            param_re = re.compile(r'\bdef\s+[\w.]+\s*\([^)\n]*\b' + esc + r'\b')
            for j, line in enumerate(lines):
                if assign_re.search(line) or attr_re.search(line):
                    segments.append(lines[j:j + 3])
                if def_re.search(line):
                    body = []
                    for k in range(j, min(len(lines), j + 30)):
                        if k > j and re.match(r'\s*(?:def|class)\s', lines[k]):
                            break
                        body.append(lines[k])
                    segments.append(body)
                elif param_re.search(line):
                    # name 作为参数出现：收集 def 行（含默认值字面量）
                    segments.append(lines[j:j + 3])
        blob = " ".join(" ".join(seg) for seg in segments)
        frontier = [n for n in set(re.findall(r'[A-Za-z_]\w{3,}', blob))
                    if n not in seen and n not in _CLOSURE_STOPWORDS]
    return segments


def _extract_sql_literal(args: str, lines: List[str], cur_idx: int) -> str:
    """从 execute() 参数中提取 SQL 字符串字面量。

    参数以字符串字面量开头则直接取；以变量开头（如 `sql + " LIMIT 50"`）则
    回溯该变量赋值，从赋值 RHS 中取字面量（拼接表达式取首个字面量）。
    提取不到返回空串（调用方按未知处理，保守维持原报）。
    """
    # keep_parens=True 时 args 以调用开括号 '(' 开头，先剥掉再匹配
    args = args.lstrip()
    if args.startswith("("):
        args = args[1:]
    m = re.match(r'\s*[fFrRbB]{0,2}(?:"""(.*?)"""|\'\'\'(.*?)\'\'\'|"([^"]*)"|\'([^\']*)\')',
                 args, re.S)
    if m:
        return next(g for g in m.groups() if g is not None)
    vm = re.match(r'\s*([A-Za-z_]\w*)\s*(?:\+|,|\)|$)', args)
    if not vm:
        return ""
    rhs = _lookup_var_rhs(lines, cur_idx, vm.group(1))
    if not rhs:
        return ""
    rm = re.search(r'"""(.*?)"""|\'\'\'(.*?)\'\'\'|"([^"]*)"|\'([^\']*)\'', rhs, re.S)
    if not rm:
        return ""
    return next(g for g in rm.groups() if g is not None)


def _refine_sec06(lines: List[str], cur_idx: int, stripped: str,
                  indent_len: int, m) -> "tuple":
    """AGENT-SEC-06（危险文件操作）自管产物降级。

    删除/重命名目标明显指向自管缓存/临时/管理文件（cache/tmp/__pycache__/
    checkpoint/write_probe/.tmp/whitelist/last-good 备份/wiki 文档等）时降级为
    low 提示，保持可见性不删条目。

    判定文本 = 调用参数 + 参数中各标识符回溯到的赋值来源（向上到函数边界）
    + 命中行上方 15 行窗口（覆盖 for entry in os.scandir(dir) 这类无赋值形式
    的循环变量来源，如 wiki MODULES/*.md 增量清理）。
    返回 ("low", detail) 降级；返回 () （空 tuple）维持原报。
    """
    args = _collect_call_args(lines, cur_idx, stripped, m.start() + indent_len, keep_parens=True)
    sig_text = args
    for ident in set(re.findall(r'\b[A-Za-z_]\w*\b', args)):
        sig_text += " " + _lookup_var_rhs(lines, cur_idx, ident)
    sig_text += " " + " ".join(
        s.strip() for s in lines[max(0, cur_idx - 15):cur_idx + 1])
    if _SEC06_SELF_MANAGED_RE.search(sig_text):
        return ("low",
                "检测到文件删除/重命名操作，但目标为自管缓存/临时/管理文件"
                "（cache/tmp/checkpoint/whitelist/last-good 备份/wiki 文档等），"
                "已降级为提示")
    return ()


def _lookup_module_const_rhs(lines: List[str], var: str) -> str:
    """全文件搜索模块级/类级常量赋值（至多一层缩进），返回 RHS 拼接文本。

    _lookup_var_rhs 在函数内回溯会被 def/class 边界截断，函数体内引用的
    模块级常量（如 AUTO_SUMMARY_FILE）需经本方法补齐来源。
    """
    if not var or not re.fullmatch(r'[A-Za-z_]\w*', var):
        return ""
    found: List[str] = []
    pat = re.compile(r'^\s{0,8}' + re.escape(var) + r'\s*(?:\+?=)\s*(?!=)')
    for j, line in enumerate(lines):
        if pat.search(line):
            found.append(" ".join(s.strip() for s in lines[j:j + 3]))
    return " ".join(found)


def _lookup_attr_rhs(lines: List[str], attr: str) -> str:
    """全文件搜索 `xxx.attr =` / 模块级 `attr =` 赋值，返回 RHS 拼接文本。

    覆盖 self.base_url / self.OSV_API_URL 这类实例属性与全大写类常量
    （定义在 __init__/类体/模块级，跨函数引用时函数内回溯拿不到）。
    """
    if not attr or not re.fullmatch(r'[A-Za-z_]\w*', attr):
        return ""
    found: List[str] = []
    pat = re.compile(r'(?:\.\s*' + re.escape(attr) + r'|(?<![\w.])' + re.escape(attr)
                     + r')\s*(?:\+?=)\s*(?!=)')
    for j, line in enumerate(lines):
        if pat.search(line):
            found.append(" ".join(s.strip() for s in lines[j:j + 3]))
    return " ".join(found)


def _pt_token_is_static(lines: List[str], cur_idx: int, tok: str) -> bool:
    """判定 SEC-PT 命中行中某标识符是否为静态（非用户可控）来源。

    回溯该变量的赋值（函数内 _lookup_var_rhs + 全文件模块级常量补齐），
    RHS 满足以下之一视为静态：
    - 含 __file__ 推算（os.path.dirname(__file__) / Path(__file__) 等）；
    - 字符串段提取（X.split(...)/X.strip(...) 等，模块名/后缀剥离，
      来源是被解析对象而非用户路径输入）且无用户输入痕迹；
    - 剥离字面量后仅引用全大写常量/静态词/调用名。
    回溯不到赋值（函数参数、外部传入）视为非常量。
    """
    rhs = _lookup_var_rhs(lines, cur_idx, tok)
    if not rhs:
        rhs = _lookup_module_const_rhs(lines, tok)
    if not rhs:
        return False
    if "__file__" in rhs:
        return True
    if _PT_UNSAFE_RE.search(rhs):
        return False
    if re.search(r'\.\s*(?:split|strip|lstrip|rstrip|replace)\s*\(', rhs) \
            and not re.search(r'\b(?:input|request|argv|form|recv)\b', rhs, re.IGNORECASE):
        return True
    rest = re.sub(r'"""[\s\S]*?"""|\'\'\'[\s\S]*?\'\'\'|"[^"\n]*"|\'[^\'\n]*\'', ' ', rhs)
    for ident in re.findall(r'[A-Za-z_]\w*', rest):
        if ident in _PT_STATIC_WORDS or ident == tok:
            continue
        if re.fullmatch(r'_*[A-Z][A-Z0-9_]*', ident):
            continue
        if re.search(r'\b' + re.escape(ident) + r'\s*\(', rhs):
            continue
        return False
    return True


def _refine_secpt(lines: List[str], cur_idx: int, stripped: str,
                  indent_len: int, m) -> "tuple":
    """AGENT-SEC-PT（路径穿越）常量拼接降级。

    命中行若各路径段均为字符串字面量、全大写/下划线前缀模块级常量
    （__file__ 推算）、静态属性（self.xxx 配置根路径），即纯常量拼接、
    无用户可控输入，降级为 low 提示；含非常量段（函数参数直接拼接、
    用户输入痕迹）时维持 high。

    判定 token 集 = 赋值左值以外的全部标识符；属性访问（obj.attr）视为
    静态属性（属性链含用户输入痕迹词时除外）；裸标识符经
    _pt_token_is_static 回溯判定。
    返回 ("low", detail) 降级；返回 () （空 tuple）维持原报。
    """
    rhs_text = stripped
    lhs_names: Set[str] = set()
    am = re.match(r'\s*([A-Za-z_]\w*(?:\s*,\s*[A-Za-z_]\w*)*)\s*=\s*(?!=)(.*)$',
                  stripped)
    if am:
        lhs_names = set(re.findall(r'[A-Za-z_]\w*', am.group(1)))
        rhs_text = am.group(2)
    if _PT_UNSAFE_RE.search(rhs_text):
        return ()
    # 剥掉字符串字面量再提取标识符："data"/"memory_state" 等字面量段
    # 不是变量引用，不参与"用户可控"判定
    code_only = re.sub(r'"""[\s\S]*?"""|\'\'\'[\s\S]*?\'\'\'|"[^"\n]*"|\'[^\'\n]*\'', ' ', rhs_text)
    tokens = set(re.findall(r'[A-Za-z_]\w*', code_only)) - lhs_names
    for tok in tokens:
        if tok in _PT_STATIC_WORDS:
            continue
        # 调用名（后随 '('）：其参数已作为独立 token 判定，跳过
        if re.search(r'\b' + re.escape(tok) + r'\s*\(', rhs_text):
            continue
        # 属性访问 obj.tok / self.tok：静态属性（工具配置根路径等）
        if re.search(r'[A-Za-z_]\w*\s*\.\s*' + re.escape(tok) + r'\b', rhs_text):
            continue
        if not _pt_token_is_static(lines, cur_idx, tok):
            return ()
    return ("low",
            "检测到路径拼接，但各段均为字符串字面量/模块级常量"
            "（__file__ 推算或静态属性），无用户可控输入，已降级为提示")


def _refine_ssrf(lines: List[str], cur_idx: int, stripped: str,
                 indent_len: int, m) -> "tuple":
    """AGENT-SEC-SSRF 本地/常量端点降级。

    - 请求目标为本地服务（localhost/127.0.0.1/0.0.0.0/::1，如本地
      Ollama http://localhost:11434）时降级 low：无外部 SSRF 面。
    - 请求 URL 为全大写类常量属性（_CAPS 模式，如 OSV_API_URL）且其
      赋值为 http(s) 字面量（官方 API 端点）时降级 low：固定常量端点。

    回溯两层：调用参数 → 裸标识符赋值（req → Request(self.OSV_API_URL)）
    与属性赋值（self.base_url → DEFAULT_BASE_URL → "http://localhost..."）。
    返回 ("low", detail) 降级；返回 () （空 tuple）维持原报。
    """
    args = _collect_call_args(lines, cur_idx, stripped, m.start() + indent_len, keep_parens=True)
    layer1 = " ".join(_lookup_var_rhs(lines, cur_idx, t)
                      for t in set(re.findall(r'\b[A-Za-z_]\w*\b', args)))
    attrs1 = set(re.findall(r'\.\s*([A-Za-z_]\w*)', args))
    layer1 += " " + " ".join(_lookup_attr_rhs(lines, a) for a in attrs1)
    attrs2 = set(re.findall(r'\.\s*([A-Za-z_]\w*)', layer1))
    attrs2 |= set(re.findall(r'\b(_?[A-Z][A-Z0-9_]{2,})\b', layer1))
    attrs2 -= attrs1
    layer2 = " ".join(_lookup_attr_rhs(lines, a) for a in attrs2)
    sig_all = args + " " + layer1 + " " + layer2
    if _SSRF_LOCAL_RE.search(sig_all):
        return ("low",
                "检测到网络请求，但目标为本地服务端点（localhost/127.0.0.1 等），"
                "无外部 SSRF 面，已降级为提示")
    for attr in attrs1 | attrs2:
        if re.fullmatch(r'_?[A-Z][A-Z0-9_]*', attr):
            if re.search(r'=\s*[fFrRbB]{0,2}["\']https?://',
                         _lookup_attr_rhs(lines, attr)):
                return ("low",
                        "检测到网络请求，但目标为固定常量端点"
                        "（全大写类常量 URL，如官方 API），无外部 SSRF 面，"
                        "已降级为提示")
    return ()


def _refine_secret(lines: List[str], cur_idx: int, stripped: str) -> "tuple":
    """AGENT-SEC-SECRET 读配置未见落盘降级。

    仅处理"密钥赋值自配置属性"形态（如 `api_key = self.config.api_key or ""`，
    只是读配置到局部变量）：本行及后 2 行无 .write(/write_text/open(...w)/
    json.dump 等落盘调用时降级 low"仅读取配置，未见落盘"；有落盘调用维持
    原报。save_env()/向 .env 写入的调用形态本身即落盘语义，不适用本降级。
    返回 ("low", detail) 降级；返回 () （空 tuple）维持原报。
    """
    if not re.match(r'\s*[A-Za-z_]\w*\s*=\s*\{?self\.', stripped):
        return ()
    window = " ".join(s.strip() for s in lines[cur_idx:cur_idx + 3])
    if _SECRET_WRITE_RE.search(window):
        return ()
    return ("low",
            "检测到 API Key 赋值，但仅为读取配置到局部变量，"
            "本行及后 2 行未见落盘调用，已降级为提示")


def _scan_crosslang_file(filepath: str, content: str, ext: str) -> List[AgentSecurityRisk]:
    """扫描 Go / Node / PHP 文件：仅应用该语言的跨语言安全模式组。

    跨语言文件无 Python 语义（无 docstring / logger 行判断），直接逐行正则匹配。
    命中即确证性危险操作信号。跳过注释行（// 、#），避免文档中的示例误报。
    少数规则（ticker 泄漏 / 并发写 / 未过滤输入）单行命中只能作为候选，须结合
    整文件上下文确证（见 _crosslang_file_signal_lines），否则跳过避免误报。
    """
    risks: List[AgentSecurityRisk] = []
    group = CROSSLANG_GROUPS[ext][1]
    # 预计算需要整文件确证的文件级规则报警行号（1-based）
    file_level_lines: dict = {}
    for pattern, rid, *_ in group:
        if rid in _CROSSLANG_FILE_LEVEL_RULES:
            file_level_lines.setdefault(rid, _crosslang_file_signal_lines(rid, content))
    lines = content.splitlines()
    for i, line in enumerate(lines, 1):
        stripped = line.strip()
        if not stripped:
            continue
        # 跳过注释行（Go // 、Python/Shell # 、PHP #）/ 块注释起止由单行态近似处理
        if stripped.startswith("//") or stripped.startswith("#") or stripped.startswith("/*"):
            continue
        for pattern, risk_id, risk_name, severity, detail, suggestion in group:
            if pattern.search(stripped):
                # 跳过模式定义自身（如本项目该文件内联的正则字符串）
                if _is_pattern_definition(stripped, risk_name):
                    continue
                # 文件级规则：单行命中仅当整文件上下文确证（行号在预计算集合内）才报警
                if risk_id in file_level_lines and i not in file_level_lines[risk_id]:
                    continue
                risks.append(AgentSecurityRisk(
                    risk_id=risk_id,
                    risk_name=risk_name,
                    category="tool_misuse",
                    severity=severity,
                    file_path=filepath,
                    line_number=i,
                    line_content=stripped[:150],
                    detail=detail,
                    suggestion=suggestion,
                ))
    return risks


def _crosslang_file_signal_lines(risk_id: str, content: str) -> set:
    """返回跨语言文件级规则应报警的行号集合（1-based）；无风险返回空集。

    这些规则仅凭单行会误报（NewTicker 往往伴随 Stop、goroutine 内未必写文件、
    写文件参数未必来自请求体、URL 拼接/getenv 未必到达出站请求），须结合整文件
    判定确证：
      - AGENT-SEC-48 SSRF 转发面：URL 拼接行之后存在出站 HTTP 客户端 sink 调用
      - AGENT-SEC-50 SSRF 诱饵可达性：getenv host 读取行之后存在出站 HTTP 客户端 sink 调用
      - AGENT-SEC-51 ticker 泄漏：同一 ticker 变量从未配对 Stop() 调用
      - AGENT-SEC-52 并发写：goroutine 体（配平花括号）内确实存在文件写入调用
      - AGENT-SEC-53 未过滤输入：写文件调用参数里确实出现请求/RPC 字段
      - AGENT-SEC-55 跨语言插件执行：插件执行面出现在同一 json.Marshal 所在函数内
    """
    if risk_id == "AGENT-SEC-48":
        # SSRF 转发面确证：仅当 URL 拼接行的后续窗口内存在出站 HTTP 客户端 sink
        # （curl_exec/curl_init/file_get_contents/Guzzle Client/->request( 等）才上报，
        # 避免孤立 URL 构造被误判，且不向 OWASP/pipeline 上游传播无 sink 支撑的命中。
        return _crosslang_ssrf_signal_lines(
            content, re.compile(r'\$fullUrl\s*=\s*\$[a-z_][\w]*\s*\.\s*\$', re.IGNORECASE))
    if risk_id == "AGENT-SEC-50":
        # SSRF 诱饵确证：getenv 读取 host 后须确实交给 HTTP 客户端发起请求才上报。
        return _crosslang_ssrf_signal_lines(
            content, re.compile(r'getenv\s*\(\s*[\'"]\w+_(?:HOST|URL)[\'"]\s*\)', re.IGNORECASE))
    if risk_id == "AGENT-SEC-51":
        result = set()
        for m in re.finditer(r'([A-Za-z_][\w]*)\s*:?=\s*time\.NewTicker\s*\(', content, re.IGNORECASE):
            var = m.group(1)
            line_no = content[:m.start()].count("\n") + 1
            # Stop() 必须绑定到同一个 ticker 变量（ticker.Stop），单凭 .Reset( 出现
            # 不足以证明该 ticker 被 Stop，不视为已清理。
            if not re.search(r'\b' + re.escape(var) + r'\.Stop\s*\(', content, re.IGNORECASE):
                result.add(line_no)
        return result
    if risk_id == "AGENT-SEC-52":
        result = set()
        for gm in re.finditer(r'go\s+func\s*\([^)]*\)\s*\{', content, re.IGNORECASE):
            # 用配平花括号解析 goroutine 函数体，避免非贪婪到首个 '}' 提前截断。
            depth = 0
            idx = gm.end() - 1  # 指向 goroutine 体起始 '{'
            while idx < len(content):
                if content[idx] == '{':
                    depth += 1
                elif content[idx] == '}':
                    depth -= 1
                    if depth == 0:
                        break
                idx += 1
            body = content[gm.start():idx]
            if re.search(r'(?:WriteFile|WriteFileByString|WriteFileByBytes|WriteAt|os\.Create|os\.OpenFile)', body):
                result.add(content[:gm.start()].count("\n") + 1)
        return result
    if risk_id == "AGENT-SEC-53":
        return {content[:m.start()].count("\n") + 1 for m in _SEC53_WRITE_RE.finditer(content)}
    if risk_id == "AGENT-SEC-55":
        result = set()
        for m in re.finditer(
                r'json\.Marshal\s*\(\s*(?:req|body|payload|param)[a-zA-Z0-9_]*\s*\)',
                content, re.IGNORECASE):
            func_area = content[:m.start()]
            # 定位 json.Marshal 所在函数：普通 Go 函数 func Name( 与 receiver
            # 方法 func (s *Server) Name( 均匹配，receiver 段不包含嵌套括号。
            fm = list(re.finditer(r'\bfunc\s+(?:\([^)]*\)\s*)?[A-Za-z_][\w]*\s*\(', func_area, re.IGNORECASE))
            if not fm:
                continue
            # 定位 json.Marshal 所在函数并配平到函数体结束，插件执行证据须与其绑定。
            last_func = fm[-1]
            open_idx = func_area.find('{', last_func.start())
            if open_idx == -1:
                continue
            depth = 0
            idx = open_idx
            while idx < len(content):
                if content[idx] == '{':
                    depth += 1
                elif content[idx] == '}':
                    depth -= 1
                    if depth == 0:
                        break
                idx += 1
            func_body = content[last_func.start():idx]
            # 确证同一函数内含 Go→PHP 插件执行面（ExecLambdaPhpPlugin / LambdaPool.Exec），
            # 而非文件任意处存在执行标识即认为所有 json.Marshal 都触发了插件执行。
            if re.search(r'(?:ExecLambdaPhpPlugin|LambdaPool\s*\.\s*Exec|Exec\w*Plugin)',
                         func_body, re.IGNORECASE):
                result.add(content[:m.start()].count("\n") + 1)
        return result
    if risk_id == "AGENT-SEC-61":
        # Spring 未鉴权确证：文件须为控制器（@RestController/@Controller），且
        # 无 @PreAuthorize/@Secured/@AuthenticationPrincipal 等鉴权注解，敏感路径
        # 映射（admin/delete/upload/export 等）才上报，避免把已鉴权控制器误判。
        # 先剥离 Java 注释（// 与 /* */），避免注释中提及的鉴权注解干扰判断。
        code = re.sub(r'//[^\n]*|/\*.*?\*/', '', content, flags=re.DOTALL)
        if not re.search(r'@(?:RestController|Controller)\b', code, re.IGNORECASE):
            return set()
        if re.search(
                r'@(?:PreAuthorize|Secured|AuthenticationPrincipal|EnableGlobalMethodSecurity|RequiresPermissions|RequiresAuthentication)\b',
                code, re.IGNORECASE):
            return set()
        return {content[:m.start()].count("\n") + 1
                for m in re.finditer(
                    r'@(?:Get|Post|Put|Delete|Request)Mapping\s*\([^)]*/(?:admin|manage|delete|upload|export|config|secret|user|api)',
                    content, re.IGNORECASE)}
    if risk_id == "AGENT-SEC-66":
        # Vue v-html 注入确证：绑定表达式含用户数据字段（props./item./msg./record./
        # tplInfo./rc./content/description/instructions/answer/question/html 等）且
        # 未经过 escapeHTML/sanitize/DOMPurify 净化才上报；静态文本（t('...')）与
        # 已净化渲染不报。同时识别单/双引号 v-html 绑定（v-html='item.content'）。
        result = set()
        for m in re.finditer(r'''v-html\s*=\s*(?:"([^"]*)"|'([^']*)')''', content, re.IGNORECASE):
            expr = m.group(1) if m.group(1) is not None else m.group(2)
            if re.search(r'escapeHTML|sanitize|DOMPurify|xss|purify', expr, re.IGNORECASE):
                continue
            if re.search(
                    r'(?:props|item|record|msg|tplInfo|rc|row|data)\.'
                    r'|(?:content|description|instructions|answer|question|html|text)\b',
                    expr, re.IGNORECASE):
                result.add(content[:m.start()].count("\n") + 1)
        return result
    if risk_id == "AGENT-SEC-67":
        # token 拼 URL 确证：token 插值拼 URL 且文件内存在 window.open/location 发送
        # 动作才上报；排除同源硬编码路径（模板以 / 开头，如 /manage/...?token=${}，
        # 属常规下载鉴权），避免把 13 处同源下载 URL 全量误报。
        if not re.search(r'window\.open\s*\(|location\.(?:href|assign)|window\.location',
                         content, re.IGNORECASE):
            return set()
        result = set()
        for i, line in enumerate(content.splitlines(), 1):
            if not re.search(r'token\s*=\s*\$\{|[?&]token=\$\{', line, re.IGNORECASE):
                continue
            if re.search(r'`\s*/', line):
                continue
            result.add(i)
        return result
    if risk_id == "AGENT-SEC-68":
        # Node HTTP 服务端无鉴权确证：文件含 HTTP 服务端（createServer/express 等）+
        # 敏感 API 路由 + 无鉴权校验（401/authorization/bearer/jwt/apiKey 等）才上报。
        # 仅出现 session 参数名不算鉴权（session 可能只是会话 ID 查询参数）。
        # 先剥离注释，避免注释中提及鉴权词干扰判断。
        if not re.search(r'createServer\s*\(|express\s*\(|http\.createServer',
                         content, re.IGNORECASE):
            return set()
        code = re.sub(r'//[^\n]*|/\*.*?\*/', '', content, flags=re.DOTALL)
        if re.search(
                r'\b401\b|authorization|api[_-]?key|bearer\s|jwt|verifyToken|checkAuth|'
                r'requireAuth|isAuthenticated|authMiddleware',
                code, re.IGNORECASE):
            return set()
        return {content[:m.start()].count("\n") + 1
                for m in re.finditer(
                    r"url\.pathname\s*===?\s*['\"]/api/(?:messages|agent|config|uploads|admin|user|users|delete|stop|start|exec|shell|file|files)",
                    content, re.IGNORECASE)}
    return set()


def _crosslang_ssrf_signal_lines(content: str, hit_re: re.Pattern) -> set:
    """返回命中行号（1-based），仅当命中 URL 构造/getenv 的行后一段窗口内（同一
    调用链/同函数附近）确实存在出站 HTTP 客户端 sink 调用。无 sink 的孤立 URL 拼接
    或配置读取不构成 SSRF，不返回该行，避免向 OWASP/pipeline 上游传播误报。
    """
    sink_re = re.compile(
        r'(?:curl_exec|curl_init|file_get_contents|new\s+Client\b|->\s*request\s*\(|'
        r'->\s*send\s*\(|Http\s*Client|GuzzleHttp)',
        re.IGNORECASE)
    lines = content.splitlines()
    result = set()
    for m in hit_re.finditer(content):
        line_no = content[:m.start()].count("\n") + 1
        # line_no 为 1-based，需转成 0-based 起点再取窗口，否则漏掉命中行本身
        #（命中行与 sink 同行时 sink 落在被跳过的 index 上，判断会漏报）。
        start = line_no - 1
        window = "\n".join(lines[start: start + 60])
        if sink_re.search(window):
            result.add(line_no)
    return result


def _scan_param_shadow(filepath: str, content: str) -> List[AgentSecurityRisk]:
    """AST 级扫描单个文件：检测函数参数被配置读取静默覆盖。"""
    risks: List[AgentSecurityRisk] = []
    try:
        tree = ast.parse(content)
    except SyntaxError:
        return risks
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            risks.extend(_scan_function_param_shadow(node, filepath, content))
    return risks


def _scan_function_param_shadow(node, filepath: str, content: str) -> List[AgentSecurityRisk]:
    """检测单个函数：参数是否被同名配置读取覆盖。"""
    risks: List[AgentSecurityRisk] = []
    params: Set[str] = {a.arg for a in node.args.args}
    params |= {a.arg for a in node.args.kwonlyargs}
    if node.args.vararg:
        params.add(node.args.vararg.arg)
    if node.args.kwarg:
        params.add(node.args.kwarg.arg)
    if not params:
        return risks

    source_lines = content.splitlines()
    for stmt in _iter_body_assigns(node.body):
        target = None
        value = None
        if isinstance(stmt, ast.Assign) and len(stmt.targets) == 1:
            target = stmt.targets[0]
            value = stmt.value
        elif isinstance(stmt, ast.AnnAssign) and stmt.value is not None:
            target = stmt.target
            value = stmt.value
        if not isinstance(target, ast.Name) or target.id not in params:
            continue
        param = target.id

        # 排除 RHS 引用参数本身的兜底 + 配置覆盖判定，统一收敛到 _match_param_shadow
        match = _match_param_shadow(param, value)
        if not match:
            continue

        line_no = getattr(stmt, "lineno", 0)
        line_content = source_lines[line_no - 1].strip()[:150] if 0 < line_no <= len(source_lines) else ""
        risks.append(AgentSecurityRisk(
            risk_id="AGENT-SEC-27",
            risk_name="参数透传失效（被配置静默覆盖）",
            category="param_shadow",
            severity="medium",
            file_path=filepath,
            line_number=line_no,
            line_content=line_content,
            detail=(
                f"函数参数「{param}」从未使用，函数体从配置容器「{match}」读取同名值，"
                f"调用方传入的实参被静默忽略。父代理会基于错误前提做判断（如误以为派了某模型）。"
            ),
            suggestion=(
                f"要么删除未生效的参数「{param}」，要么让函数体真正使用参数值；"
                f"若确需配置优先，应显式声明优先级并在调用处提示参数被忽略，禁止静默覆盖。"
            ),
        ))
    return risks


def _match_param_shadow(param: str, value):
    """若参数被同名的配置容器读取静默覆盖，返回容器名；否则返回 None。

    排除 RHS 引用参数本身的合理兜底（如 `x = x or config["x"]` 的默认值兜底），
    避免把「参数 = 参数 or 配置默认」这一常规模式误判为覆盖。
    """
    if value is None:
        return None
    if _contains_name(value, param):
        return None
    return _match_config_shadow(value, param)


@staticmethod
def _iter_body_assigns(body) -> List[ast.AST]:
    """深度遍历函数体，收集赋值语句，跳过嵌套函数/类定义子树（避免作用域混淆）。"""
    assigns: List[ast.AST] = []

    def walk_list(items):
        for item in items:
            if isinstance(item, ast.AST):
                walk(item)

    def walk(item: ast.AST):
        if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)):
            return
        if isinstance(item, (ast.Assign, ast.AnnAssign)):
            assigns.append(item)
            return
        walk_list(list(ast.iter_child_nodes(item)))

    walk_list(body)
    return assigns


@staticmethod
def _contains_name(value, name: str) -> bool:
    """判断 AST 子树中是否引用了指定变量名。"""
    if value is None:
        return False
    return any(isinstance(n, ast.Name) and n.id == name for n in ast.walk(value))


def _match_config_shadow(value, param: str):
    """判断 value 是否为「从配置容器读取同名参数」的表达式，命中返回容器标识，否则返回 None。

    匹配三种形态：
      - container["param"]          （Subscript）
      - container.param            （Attribute）
      - container.get("param")     （Call，含 os.environ.get / self.config.get）
    容器名需含配置来源特征（config/cred/settings/env/...），且 key/属性与参数同名。
    容器名从 Name 或 Attribute 链取叶子名：self.config → config、os.environ → environ。
    """
    if isinstance(value, ast.Subscript):
        base = value.value
        key = None
        sl = value.slice
        if isinstance(sl, ast.Constant) and isinstance(sl.value, str):
            key = sl.value
        elif getattr(ast, 'Str', None) is not None and isinstance(sl, ast.Str):
            key = sl.s
        container = _container_leaf_name(base)
        if container and key == param and _is_config_container(container):
            return container
    elif isinstance(value, ast.Attribute):
        base = value.value
        container = _container_leaf_name(base)
        if container and value.attr == param and _is_config_container(container):
            return container
    elif isinstance(value, ast.Call):
        func = value.func
        if isinstance(func, ast.Attribute) and func.attr in ("get", "getenv"):
            base = func.value
            container = _container_leaf_name(base)
            if container and _is_config_container(container):
                if value.args and isinstance(value.args[0], ast.Constant) \
                        and isinstance(value.args[0].value, str) \
                        and value.args[0].value == param:
                    return container
    return None


@staticmethod
def _container_leaf_name(base) -> str:
    """从 Name 或 Attribute 链中提取容器叶子名。
    config → config; os.environ → environ; self.config → config; cfg.sub → sub
    """
    if isinstance(base, ast.Name):
        return base.id
    if isinstance(base, ast.Attribute):
        return base.attr
    return ""


def _is_config_container(name: str) -> bool:
    """判断变量名是否命中配置来源容器特征（config/cred/settings/env/...）。"""
    n = name.lower()
    return any(h in n for h in CONFIG_CONTAINER_HINTS)


def _check_resilience_gaps(project_path: str) -> List[AgentSecurityRisk]:
    """检查防御层级韧性缺口 —— 检测缺失的防御模式
    
    与逐行扫描不同，这是项目级检查：扫描所有 .py 文件，判断每种防御模式是否存在。
    如果某种防御模式在整个项目中都没有找到，则生成一个缺口风险。
    """
    risks = []

    # 收集所有 Python 文件内容
    all_content = ""
    for root, dirs, files in os.walk(project_path):
        dirs[:] = [d for d in dirs if not d.startswith(".") and d not in EXCLUDE_DIRS]
        for f in files:
            if not f.endswith(".py"):
                continue
            try:
                with open(os.path.join(root, f), "r", encoding="utf-8", errors="ignore") as fh:
                    all_content += fh.read() + "\n"
            except (OSError, IOError):
                continue
    
    if not all_content.strip():
        return risks

    # 缺陷 11：LLM 韧性缺口检查的前提是"项目确实调用 LLM"。
    # 若项目不含任何 LLM 调用信号（import openai/anthropic、completions.create、
    # chat.completions 等），则"缺少重试退避/模型回退/上下文截断"等缺口是无依据的
    # 误报，应跳过，避免对非 LLM 项目污染审计结论。
    LLM_SIGNAL = re.compile(
        r'\b(?:chat\.completions|completions\.create|chat_completions|'
        r'LLMRegistry|ModelRegistry)\b|'
        r'\bimport\s+(?:openai|anthropic)\b|'
        r'\bfrom\s+(?:openai|anthropic)\b|'
        r'\b(?:openai|anthropic)\.(?:OpenAI|AsyncOpenAI|Client|Anthropic)\b',
        re.IGNORECASE)
    has_llm = bool(LLM_SIGNAL.search(all_content))
    # 仅当项目使用 LLM 时才检查的 LLM 强相关韧性缺口
    LLM_ONLY_GAP_IDS = {
        "AGENT-RESILIENCE-01", "AGENT-RESILIENCE-02",
        "AGENT-RESILIENCE-03", "AGENT-RESILIENCE-04",
    }
    if not has_llm:
        logger.info(
            "[AgentSecurityAudit] 项目未检测到 LLM 调用信号，跳过 LLM 韧性缺口检查"
            "（避免对非 LLM 项目误报缺少重试退避等）")

    # 对每种防御模式，检查是否在项目中存在
    for check in RESILIENCE_GAP_CHECKS:
        # 无 LLM 项目：跳过 LLM 强相关缺口
        if not has_llm and check["id"] in LLM_ONLY_GAP_IDS:
            continue
        # AGENT-RESILIENCE-07 连接池探活：只对"实际使用数据库连接池"的项目打标。
        # 精确匹配导入/调用（create_engine( / import sqlalchemy / pool_pre_ping 等），
        # 避免 sqlalchemy/psycopg 等关键词出现在注释、字符串或文档里就误触发；
        # 纯 SQLite（sqlite3 标准库，无连接池概念）等项目直接跳过，不做机械打标。
        if check["id"] == "AGENT-RESILIENCE-07":
            if not re.search(
                r'create_engine\s*\(|import\s+sqlalchemy|from\s+sqlalchemy|'
                r'import\s+(psycopg|pymysql)|mysql\.connector|import\s+asyncpg|'
                r'DBUtils|pool_pre_ping|pool_recycle',
                all_content, re.IGNORECASE
            ):
                continue
        found = False
        for pattern in check["patterns"]:
            if pattern.search(all_content):
                found = True
                break
        
        if not found:
            # 该防御模式缺失，生成缺口风险
            risks.append(AgentSecurityRisk(
                risk_id=check["id"],
                risk_name=check["name"],
                category="resilience_gap",
                severity=check["severity"],
                file_path="",  # 项目级检查，无具体文件
                line_number=0,
                line_content="",
                detail=check["detail"],
                suggestion=check["suggestion"],
            ))
    
    return risks


def _collect_call_args(lines: List[str], start_idx: int, stripped: str, from_col: int,
                       keep_parens: bool = False) -> str:
    """从调用起点提取完整括号内的参数（平衡括号匹配，支持跨行/嵌套调用）。

    从 from_col 位置起扫描，遇到 '(' 深度+1，遇到 ')' 深度-1，深度归零即返回
    括号内完整内容。若参数跨行（调用写到下一行），自动续读后续行，避免
    `[^)]*` 在第一个 ')' 提前截断导致的漏判（如 requests.get(url, timeout=compute())）。

    keep_parens=True 时保留括号字符：默认剥括号会把 `os.path.join(pc, f)` 的
    内容压成 `os.path.joinpc, f`（标识符粘连），需要按标识符解析参数时必须保留。
    """
    depth = 0
    started = False
    buf = []
    col = from_col
    idx = start_idx
    while True:
        if idx >= len(lines):
            break
        line = lines[idx] if idx == start_idx else lines[idx].strip()
        while col < len(line):
            ch = line[col]
            if not started:
                if ch == '(':
                    started = True
                    depth = 1
                    if keep_parens:
                        buf.append(ch)
                col += 1
                continue
            if ch == '(':
                depth += 1
                if keep_parens:
                    buf.append(ch)
            elif ch == ')':
                depth -= 1
                if keep_parens:
                    buf.append(ch)
                if depth == 0:
                    return "".join(buf)
            elif ch not in ("\n", "\r", "\t"):
                buf.append(ch)
            col += 1
        idx += 1
        col = 0
    return "".join(buf)


def _is_pattern_definition(line: str, risk_name: str) -> bool:
    """检查是否匹配了工具自身的检测模式定义"""
    # 匹配 AGENT-SEC- 编号
    if re.search(r'AGENT-SEC-\d+', line, re.IGNORECASE):
        return True
    # 匹配规则描述字符串（如 "检测到 DEBUG=True" 或 "检测到使用 pickle"）
    if re.search(r'["\']检测到', line):
        return True
    # 匹配规则建议字符串（如 "使用 yaml.safe_load() 替代"）
    if re.search(r'["\']使用\s.*(?:替代|替换|避免)', line):
        return True
    # 匹配安全规则的正则表达式定义行（如 re.compile(r'...')）
    if re.search(r're\.compile\(', line):
        return True
    # 匹配 CWE 映射行
    if re.search(r'CWE-\d+', line):
        return True
    # 匹配字符串字面量中引用的危险 API 名（检测器用 'pickle.loads' in line 之类
    # 字符串匹配来检测目标代码，字符串名不是真实调用，不应被自身规则误报）
    if re.search(r'["\'](?:pickle\.loads|yaml\.load|json\.loads|eval|exec|subprocess)[`"\']', line):
        return True
    return False


def _report_severity_stats(risks: List[AgentSecurityRisk]) -> dict:
    """统计各级别风险数量，并计算扣分后的安全总分与等级。"""
    counts = defaultdict(int)
    for r in risks:
        counts[r.severity] += 1
    blocker = counts["blocker"]
    critical = counts["critical"]
    high = counts["high"]
    medium = counts["medium"]
    low = counts["low"]

    # 评分
    penalty = blocker * 30 + critical * 20 + high * 10 + medium * 3 + low * 1
    score = max(0, min(100, 100 - penalty))
    grade = "A" if score >= 90 else "B" if score >= 75 else "C" if score >= 60 else "D" if score >= 40 else "F"

    return {
        "blocker": blocker,
        "critical": critical,
        "high": high,
        "medium": medium,
        "low": low,
        "score": score,
        "grade": grade,
    }


def _render_report_header(risks: List[AgentSecurityRisk], project_path: str, stats: dict) -> List[str]:
    """渲染报告头部：标题、项目信息与安全评分表。"""
    return [
        "# Agent 系统安全审计",
        "",
        f"> 项目: `{project_path}`",
        f"> 检测到 {len(risks)} 个 Agent 安全风险",
        "",
        "## 安全评分",
        "",
        f"| 评分 | 等级 | 阻断 | 严重 | 高危 | 中危 | 低危 |",
        f"|------|------|------|------|------|------|------|",
        f"| {stats['score']:.0f}/100 | **{stats['grade']}** | {stats['blocker']} | {stats['critical']} | {stats['high']} | {stats['medium']} | {stats['low']} |",
        "",
    ]


def _render_category_summary(by_category: "defaultdict") -> List[str]:
    """渲染"风险类别汇总"段：每类风险一行的汇总表。"""
    lines = [
        "## 风险类别汇总",
        "",
        "| 类别 | 风险数 | 最高严重性 | 说明 |",
        "|------|--------|------------|------|",
    ]

    for cat_key in CATEGORY_ORDER:
        cat_risks = by_category.get(cat_key, [])
        if not cat_risks:
            continue
        max_sev = min(cat_risks, key=lambda r: SEVERITY_ORDER.get(r.severity, 99)).severity
        sev_icon = "🔴" if max_sev in ("blocker", "critical") else "🟠" if max_sev == "high" else "🟡" if max_sev == "medium" else "⚪"
        lines.append(f"| {CATEGORY_NAMES.get(cat_key, cat_key)} | {len(cat_risks)} | {sev_icon} {max_sev} | {CATEGORY_DETAILS.get(cat_key, '')} |")

    lines.append("")
    return lines


def _render_risk_details(by_category: "defaultdict") -> List[str]:
    """渲染"详细风险列表"段：每类风险一张明细表（单类别最多列 20 条）。"""
    lines = [
        "## 详细风险列表",
        "",
    ]

    for cat_key in CATEGORY_ORDER:
        cat_risks = by_category.get(cat_key, [])
        if not cat_risks:
            continue
        lines.append(f"### {CATEGORY_NAMES.get(cat_key, cat_key)}")
        lines.append("")
        lines.append("| ID | 严重性 | 文件 | 行号 | 描述 | 建议 |")
        lines.append("|----|--------|------|------|------|------|")
        for r in cat_risks[:20]:
            sev_icon = "🔴" if r.severity in ("blocker", "critical") else "🟠" if r.severity == "high" else "🟡" if r.severity == "medium" else "⚪"
            if r.file_path:
                fname = os.path.basename(r.file_path)
                file_col = f"`{fname}`"
                line_col = str(r.line_number)
            else:
                file_col = "项目全局"
                line_col = "-"
            lines.append(
                f"| {r.risk_id} | {sev_icon} {r.severity} | {file_col} | {line_col} | "
                f"{r.risk_name}: {r.detail[:80]} | {r.suggestion[:80]} |"
            )
        if len(cat_risks) > 20:
            lines.append(f"| ... | ... | ... | ... | （还有 {len(cat_risks) - 20} 条） | ... |")
        lines.append("")

    return lines


def _render_report_footer() -> List[str]:
    """渲染报告尾部：审计方法说明与 LLM 复查提示。"""
    return [
        "---",
        "",
        "### 关于 Agent 安全审计",
        "",
        "本报告基于 OWASP Top 10 for LLM Applications 和 Agent 安全最佳实践设计。",
        "传统 SAST 工具（如 SonarQube、Snyk）主要关注代码注入和依赖漏洞，",
        "但 AI Agent 系统引入了全新的攻击面：提示注入、上下文操纵、工具滥用、自主行为等。",
        "",
        "这些风险在传统安全扫描中完全不可见，需要专门的 Agent 安全审计。",
        "",
        "*扫描由 CodeRef Agent Security Auditor 执行*\n",
        "",
        "> **LLM 复查提示**：此报告可能包含误报。审查每条结果后，调用 `coderef_cache_whitelist` 工具将误报自动加入白名单，然后重新扫描。",
    ]


def to_report(risks: List[AgentSecurityRisk], project_path: str) -> str:
    """生成 Agent 安全审计报告"""
    # 统计
    by_category = defaultdict(list)
    for r in risks:
        by_category[r.category].append(r)

    stats = _report_severity_stats(risks)
    lines = _render_report_header(risks, project_path, stats)

    if not risks:
        lines.append("✅ 未发现 Agent 安全风险。")
        return "\n".join(lines)

    lines.extend(_render_category_summary(by_category))
    lines.extend(_render_risk_details(by_category))
    lines.extend(_render_report_footer())

    return "\n".join(lines)


def audit_agent_security(project_path: str) -> str:
    """便捷函数"""
    auditor = AgentSecurityAuditor()
    risks = auditor.audit(project_path)
    return auditor.to_report(risks, project_path)