# -*- coding: utf-8 -*-
"""Agent 安全审计常量模式区（asa_patterns）

从 core/agent_security_auditor.py 拆出的纯数据/模式常量区：
AgentSecurityRisk 数据模型、SEVERITY_ORDER、各 *_PATTERNS / *_RE /
CROSSLANG_GROUPS / RESILIENCE_GAP_CHECKS / CATEGORY_* 等规则表。
供 asa_scan（扫描/精炼/报告渲染）与主文件（类 + re-export）消费。
依赖拓扑（单向无环）：asa_patterns ← asa_scan ← agent_security_auditor。
"""

import re
from dataclasses import dataclass

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


# ─── LLM 上下文场景过滤（AGENT-* 规则只对 LLM/AI 相关代码生效） ───
# 纯 HTTP 库/认证/网络请求等非 LLM 场景不应触发 Agent 特有规则（如 AGENT-SEC-01
# 提示注入、AGENT-SEC-09 不受控网络请求、AGENT-SEC-14 API Key 明文传递），否则
# 产生大量上下文误报。判定：命中行或整个文件含 LLM/AI 特征词（prompt/llm/agent/
# chat/completion 等）才触发；user agent 等 HTTP 库常见词先剔除，避免误判。
_LLM_CONTEXT_RE = re.compile(
    r'\b(?:prompt|llm|openai|anthropic|langchain|completion|completions|'
    r'system_prompt|sys_prompt|system_message|user_message|messages|assistant|'
    r'embeddings|tokenizer|chatgpt|claude|gemini|llama|chat_completion|chat\.completions)\b'
    r'|\bchat\b'
    r'|(?<![-_])agent\b',
    re.IGNORECASE)
_USER_AGENT_RE = re.compile(r'user[-_ ]agent(?:s)?\b', re.IGNORECASE)

# 仅 LLM 上下文生效的 Agent 特有规则：非 LLM 项目（纯 HTTP 库/认证/网络请求等）
# 中这些规则无意义，触发前须确认命中行或文件含 LLM/AI 特征。
_AGENT_LLM_ONLY_RULES = frozenset({
    # 提示注入 / 角色混淆
    "AGENT-SEC-01", "AGENT-SEC-02",
    # 上下文操纵 / 知识投毒
    "AGENT-SEC-03", "AGENT-SEC-04", "AGENT-SEC-05",
    # 不受控网络请求（requests 库误报源）
    "AGENT-SEC-09",
    # 预算/资源耗尽
    "AGENT-SEC-10", "AGENT-SEC-11", "AGENT-SEC-12",
    # 数据泄露（prompt 日志 / API Key 明文 / 敏感数据外传）
    "AGENT-SEC-13", "AGENT-SEC-14", "AGENT-SEC-15",
    # 自主行为
    "AGENT-SEC-16", "AGENT-SEC-17",
    # LLM 调用缺少限流
    "AGENT-SEC-26",
    # LLM 工具执行滥用 / 查询注入
    "AGENT-SEC-42", "AGENT-SEC-LMEXEC",
    # 浏览器沙箱
    "AGENT-SEC-SANDBOX",
})




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
    # os.path.join 动态拼接：目录根与"变量 + 字符串"混合表达式拼接
    # （如 os.path.join(base, key + ".db")），用户可控输入参与路径构造。
    # 逐行正则无法区分静态/动态段，命中后由 _refine_secpt 回溯判定：纯静态
    # 拼接降级 low，含函数参数/用户输入痕迹维持 high。
    (re.compile(r'os\.path\.join\s*\([^)]*\+[^)]*\)', re.IGNORECASE),
     "AGENT-SEC-PT", "路径穿越", "high",
     "检测到 os.path.join 拼接中包含变量 + 字符串的混合表达式（如 os.path.join(base, key + '.db')），用户可控输入可能参与路径构造，未净化 ../ 可越权读写任意文件",
     "清洗用户可控 segment（拒绝 .. 与绝对路径），校验拼接后绝对路径位于允许根目录内"),
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
