# -*- coding: utf-8 -*-
"""Agent 安全审计扫描/精炼/报告渲染（asa_scan）

从 core/agent_security_auditor.py 拆出的模块级静态检测逻辑：
_scan_* / _refine_* / _collect_* / _lookup_* / _crosslang_* 等扫描与
精炼函数，以及 _render_* / to_report 报告渲染。常量与数据模型自
asa_patterns 导入。依赖拓扑（单向无环）：asa_patterns ← asa_scan ←
agent_security_auditor。
"""

import ast
import hashlib
import logging
import os
import re
from typing import List, Set
from collections import defaultdict

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
    CROSSLANG_GROUPS,
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

logger = logging.getLogger(__name__)

# SEC-08 自管缓存库判定结果记忆化：键 (文件路径, 接收对象表达式, 内容指纹)，值 bool。
# 纯文件内容函数（与实例状态无关），进程级共享缓存语义等价于原实例懒加载缓存；
# 内容指纹保证文件变更后缓存失效，不会跨审计累积过期判定。
_SEC08_DB_CACHE = {}

def _has_llm_context(text: str) -> bool:
    """判断文本是否含 LLM/AI 特征（先剔除 user agent 等 HTTP 库常见词）。"""
    return bool(_LLM_CONTEXT_RE.search(_USER_AGENT_RE.sub(' ', text)))



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
    # whitelist 的 dir 条目（如 _refactor_backup）必须与审计/图谱同口径排除：
    # 旧实现只查 EXCLUDE_DIRS 固定名单，导致备份目录仍进入 Agent 安全扫描，
    # 进而让 OWASP 的 LLM 分类把备份代码计成当前项目风险（外部实测 58%）。
    try:
        from core.pr_whitelist import whitelist_exclude_dirs, path_hits_excluded_dir
        excl_dirs = whitelist_exclude_dirs(project_path)
    except Exception:  # noqa: BLE001 —— 白名单读取失败不阻断扫描
        excl_dirs, path_hits_excluded_dir = [], lambda *_: False
    for root, dirs, files in os.walk(project_path):
        dirs[:] = [d for d in dirs if not _is_excluded_dir(root, d)]
        for f in files:
            if f in SELF_EXEMPT_FILES:
                continue
            ext = os.path.splitext(f)[1].lower()
            if ext != ".py" and ext not in CROSSLANG_GROUPS:
                continue
            fpath = os.path.join(root, f)
            if path_hits_excluded_dir(
                    os.path.relpath(fpath, project_path), excl_dirs):
                continue
            scan_files.append(fpath)
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

    # AGENT-* 特有规则 LLM 场景过滤：文件含 LLM/AI 特征才允许触发（命中行本身
    # 含特征时也放行，见规则循环内判断）
    file_has_llm = _has_llm_context(content)

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
                    # AGENT-* 特有规则仅对 LLM 相关代码生效：命中行与文件均无
                    # LLM/AI 特征（纯 HTTP 库/认证/网络请求等非 LLM 场景）则跳过
                    if risk_id in _AGENT_LLM_ONLY_RULES \
                            and not (file_has_llm or _has_llm_context(stripped)):
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
    # 缓存键带内容指纹：文件内容变更后旧判定自动失效，防止跨审计返回过期结论
    fp = hashlib.md5("\n".join(lines).encode("utf-8", "ignore")).hexdigest()
    key = (filepath, receiver, fp)
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


def _scan_path_traversal_ast(filepath: str, content: str) -> List[AgentSecurityRisk]:
    """AST 级检测 os.path.join 动态路径拼接（用户可控输入参与路径构造）。

    覆盖 PATH_TRAVERSAL_PATTERNS 正则盲区：`os.path.join(base, key + ".db")`
    这类目录根与用户可控变量（函数参数）动态拼接构造文件路径，未净化 ../ 可
    越权读写任意文件。逐行正则只认特定变量名（data_dir/*_root/working_dir）
    或含 + 的混合表达式，无法识别"参数直接拼进 join"的通用形态（如
    os.path.join(base, key)），需 AST 分析函数参数引用。含 + 的混合表达式
    已由正则层覆盖，此处只补正则盲区，避免重复上报。
    """
    risks: List[AgentSecurityRisk] = []
    try:
        tree = ast.parse(content)
    except SyntaxError:
        return risks
    source_lines = content.splitlines()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        params: Set[str] = {a.arg for a in node.args.args}
        params |= {a.arg for a in node.args.kwonlyargs}
        if not params:
            continue
        # 收集函数体内赋值（var -> RHS），支持一层间接引用（name = key + ".db"）
        local_assigns: dict = {}
        for stmt in _iter_body_assigns(node.body):
            if isinstance(stmt, ast.Assign) and len(stmt.targets) == 1 \
                    and isinstance(stmt.targets[0], ast.Name):
                local_assigns[stmt.targets[0].id] = stmt.value
            elif isinstance(stmt, ast.AnnAssign) and stmt.value is not None \
                    and isinstance(stmt.target, ast.Name):
                local_assigns[stmt.target.id] = stmt.value
        for sub in ast.walk(node):
            if not isinstance(sub, ast.Call):
                continue
            # os.path.join：func 为 Attribute(Attribute(Name('os'), 'path'), 'join')
            func = sub.func
            if not (isinstance(func, ast.Attribute) and func.attr == "join"
                    and isinstance(func.value, ast.Attribute)
                    and func.value.attr == "path"
                    and isinstance(func.value.value, ast.Name)
                    and func.value.value.id == "os"):
                continue
            # 含 + 的混合表达式拼接已由 PATH_TRAVERSAL_PATTERNS 正则层覆盖，
            # 此处只补正则盲区：无 + 的直接变量拼接（os.path.join(base, key)）
            if any(isinstance(a, ast.BinOp) and isinstance(a.op, ast.Add)
                   for a in sub.args):
                continue
            # join 参数中任一引用函数参数（直接或经赋值链）即判定动态拼接
            if not any(_expr_references_params(a, params, local_assigns)
                       for a in sub.args):
                continue
            line_no = getattr(sub, "lineno", 0)
            line_content = source_lines[line_no - 1].strip()[:150] \
                if 0 < line_no <= len(source_lines) else ""
            risks.append(AgentSecurityRisk(
                risk_id="AGENT-SEC-PT",
                risk_name="路径穿越",
                category="path_traversal",
                severity="high",
                file_path=filepath,
                line_number=line_no,
                line_content=line_content,
                detail=(
                    "检测到 os.path.join 将目录根与用户可控输入（函数参数）动态"
                    "拼接构造文件路径，未净化 ../ 可越权读写任意文件"
                ),
                suggestion=(
                    "清洗用户可控 segment（拒绝 .. 与绝对路径），校验拼接后绝对"
                    "路径位于允许根目录内"
                ),
            ))
    return risks


def _expr_references_params(expr, params: Set[str], local_assigns: dict,
                            _seen: "Set[str] | None" = None) -> bool:
    """判断表达式是否（直接或经赋值链）引用函数参数。"""
    if expr is None:
        return False
    if _seen is None:
        _seen = set()
    if any(isinstance(n, ast.Name) and n.id in params for n in ast.walk(expr)):
        return True
    for n in ast.walk(expr):
        if isinstance(n, ast.Name) and n.id in local_assigns and n.id not in _seen:
            _seen.add(n.id)
            if _expr_references_params(local_assigns[n.id], params, local_assigns, _seen):
                return True
    return False


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
                f"函数参数「{param}」从未使用，函数体从配置容器「{match}」读取配置值"
                f"（key 与参数同名或语义相关），调用方传入的实参被静默忽略。"
                f"父代理会基于错误前提做判断（如误以为派了某模型）。"
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


def _contains_name(value, name: str) -> bool:
    """判断 AST 子树中是否引用了指定变量名。"""
    if value is None:
        return False
    return any(isinstance(n, ast.Name) and n.id == name for n in ast.walk(value))


# 配置读取方法名（Call 形态 func.attr 命中即视为从配置容器读取值）
_CONFIG_READ_METHODS = frozenset({
    "get", "getenv", "read", "config_read", "get_config", "read_config",
    "load_config", "get_setting", "get_param", "get_value", "fetch_config",
})
# 强配置读取方法：方法名本身即表明配置读取语义，容器名可放宽
# （如 db.config_read("default_password")，容器 db 不含 config 特征）
_STRONG_CONFIG_READ_METHODS = frozenset({
    "config_read", "get_config", "read_config", "load_config",
    "get_setting", "get_param", "get_value", "fetch_config",
})


def _key_matches_param(key: str, param: str) -> bool:
    """判断配置 key 与参数名是否语义相关（同名或参数名为 key 后缀）。

    放宽"key 与参数同名"的严格要求：`db.config_read("default_password")`
    覆盖参数 `password` 时，key 含参数名作为后缀（default_password 的
    后缀是 password），视为同一语义字段的配置覆盖。
    """
    if not key or not param:
        return False
    if key == param:
        return True
    return key.endswith(param)


def _match_config_shadow(value, param: str):
    """判断 value 是否为「从配置容器读取同名参数」的表达式，命中返回容器标识，否则返回 None。

    匹配三种形态：
      - container["param"]          （Subscript）
      - container.param            （Attribute）
      - container.get("param")     （Call，含 os.environ.get / self.config.get）
    容器名需含配置来源特征（config/cred/settings/env/...），且 key/属性与参数
    同名或语义相关（参数名为 key 后缀）。强配置读取方法（config_read 等）本身
    即表明配置读取语义，容器名可放宽。容器名从 Name 或 Attribute 链取叶子名：
    self.config → config、os.environ → environ。
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
        if container and _key_matches_param(key, param) and _is_config_container(container):
            return container
    elif isinstance(value, ast.Attribute):
        base = value.value
        container = _container_leaf_name(base)
        if container and value.attr == param and _is_config_container(container):
            return container
    elif isinstance(value, ast.Call):
        func = value.func
        if isinstance(func, ast.Attribute):
            base = func.value
            container = _container_leaf_name(base)
            method = func.attr.lower()
            if method in _CONFIG_READ_METHODS:
                if value.args and isinstance(value.args[0], ast.Constant) \
                        and isinstance(value.args[0].value, str) \
                        and _key_matches_param(value.args[0].value, param):
                    # 强配置读取方法（config_read 等）容器判定放宽；通用读取
                    # 方法（get/getenv/read）仍要求容器名含配置来源特征
                    if method in _STRONG_CONFIG_READ_METHODS or _is_config_container(container):
                        return container
    return None


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
        "| 评分 | 等级 | 阻断 | 严重 | 高危 | 中危 | 低危 |",
        "|------|------|------|------|------|------|------|",
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
