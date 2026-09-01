# -*- coding: utf-8 -*-
"""
LLM集成模块
支持OpenAI、DeepSeek、Ollama等多种模型
"""

import os
import re
import json
import time
from typing import Dict, List, Any, Optional, Literal
from dataclasses import dataclass, field
from enum import Enum

from loguru import logger
from openai import OpenAI
import httpx

from config import settings


class LLMProvider(Enum):
    """LLM服务提供商"""
    OPENAI = "openai"
    DEEPSEEK = "deepseek"
    OLLAMA = "ollama"
    CUSTOM = "custom"


@dataclass
class LLMConfig:
    """LLM配置"""
    provider: LLMProvider
    api_key: str = ""
    base_url: str = ""
    model: str = ""
    temperature: float = 0.7
    max_tokens: int = 2048


@dataclass
class CodeSuggestion:
    """代码建议"""
    suggestion_id: str
    title: str
    description: str
    insert_position: Dict[str, Any] = field(default_factory=dict)
    code_snippet: str = ""
    reference_comment: str = ""
    modification_notes: List[str] = field(default_factory=list)
    risk_warnings: List[str] = field(default_factory=list)
    test_suggestions: List[str] = field(default_factory=list)
    source_reference: str = ""


def _safe_float(raw, default: float) -> float:
    """安全转换浮点，非法/空值回退默认，避免环境变量配置导致崩溃。"""
    try:
        if raw is None or str(raw).strip() == "":
            return default
        return float(raw)
    except (TypeError, ValueError):
        return default


def _safe_int(raw, default: int) -> int:
    """安全转换整数，非法/空值回退默认。"""
    try:
        if raw is None or str(raw).strip() == "":
            return default
        return int(raw)
    except (TypeError, ValueError):
        return default


def _load_llm_config_from_settings() -> LLMConfig:
    """加载 LLM 配置，按优先级尝试多个来源：
    1. 环境变量（CODEREF_API_KEY / CODEREF_BASE_URL / CODEREF_MODEL）
    2. config/config.json（旧版配置文件，兼容）
    3. 默认值（DeepSeek）

    拆分说明：原 LLMIntegration._load_config_from_settings 静态方法提取为
    模块级纯函数（不依赖 self/cls），类内保留同名静态方法作委托，
    配置读取与回退语义与拆分前一致。
    """
    # ── 优先级 1：环境变量 ──
    env_key = os.environ.get("CODEREF_API_KEY", "")
    if env_key:
        provider_str = os.environ.get("CODEREF_PROVIDER", "deepseek")
        provider_map = {
            "deepseek": LLMProvider.DEEPSEEK,
            "openai": LLMProvider.OPENAI,
            "ollama": LLMProvider.OLLAMA,
            "custom": LLMProvider.CUSTOM,
        }
        return LLMConfig(
            provider=provider_map.get(provider_str, LLMProvider.DEEPSEEK),
            api_key=env_key,
            base_url=os.environ.get("CODEREF_BASE_URL", "https://api.deepseek.com"),
            model=os.environ.get("CODEREF_MODEL", "deepseek-v4-flash"),
            temperature=_safe_float(os.environ.get("CODEREF_TEMPERATURE"), 0.7),
            max_tokens=_safe_int(os.environ.get("CODEREF_MAX_TOKENS"), 4096),
        )

    # ── 优先级 2：config/config.json（旧版配置文件，兼容） ──
    try:
        config_paths = [
            os.path.join(os.path.dirname(os.path.dirname(__file__)), "config", "config.json"),
            # 支持从项目根目录查找
            os.path.join(os.getcwd(), "config", "config.json"),
        ]
        for cfg_path in config_paths:
            if os.path.exists(cfg_path):
                with open(cfg_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                api_key = data.get("llm_api_key", "") or data.get("api_key", "")
                if api_key and api_key != "ollama":  # "ollama" 是占位符，不算有效 key
                    provider_str = data.get("llm_provider", "deepseek")
                    provider_map = {
                        "deepseek": LLMProvider.DEEPSEEK,
                        "openai": LLMProvider.OPENAI,
                        "ollama": LLMProvider.OLLAMA,
                        "custom": LLMProvider.CUSTOM,
                    }
                    return LLMConfig(
                        provider=provider_map.get(provider_str, LLMProvider.DEEPSEEK),
                        api_key=api_key,
                        base_url=data.get("llm_base_url", data.get("base_url", "https://api.deepseek.com")),
                        model=data.get("llm_model", data.get("model_name", "deepseek-v4-flash")),
                        temperature=float(data.get("llm_temperature", data.get("temperature", 0.7))),
                        max_tokens=int(data.get("llm_max_tokens", data.get("max_tokens", 4096))),
                    )
                else:
                    logger.debug(f"config.json 中 api_key 为占位符或空，跳过: {cfg_path}")
    except Exception as e:
        logger.debug(f"读取 config.json 失败: {e}")

    # ── 优先级 3：默认值（无 API Key） ──
    logger.debug("未找到有效的 LLM 配置（环境变量/config.json 均无），LLM 功能暂不可用")
    return LLMConfig(
        provider=LLMProvider.DEEPSEEK,
        base_url="https://api.deepseek.com",
        model="deepseek-v4-flash",
        api_key=""
    )


def _complete_bare_token(tok: str) -> str:
    """补全被截断的裸 token：tr/tru → true，fa/fal/fals → false，nu/nul → null。

    拆分说明：原 LLMIntegration._complete_bare_token 静态方法提取为模块级
    纯函数，类内保留同名静态方法作委托。
    """
    t = tok.strip()
    low = t.lower()
    for prefix, full in (("true", "true"), ("false", "false"), ("null", "null")):
        if prefix.startswith(low) and low:
            return full
    if re.fullmatch(r"-?\d*\.?\d*(?:[eE][+-]?\d*)?", t):
        return t
    return tok


def _repair_truncated_json(text: str) -> str:
    """尽力修复被截断的 JSON 文本（LLM 因 max_tokens 截断的常见残缺）。

    处理三类残缺：
    1. 字符串字面量被截断（如 `{"a": "unfin`，缺闭合引号）；
    2. 裸 token 被截断（如 `"verified": tr`，缺结尾）；
    3. 数组/对象括号未闭合（如 `[{"x":1`，缺 `}]`）。

    仅当能修复时返回修复后的文本，否则返回原文本（由调用方判定）。

    拆分说明：原 LLMIntegration._repair_truncated_json 类方法提取为模块级
    纯函数（内部改调模块级 _complete_bare_token），类内保留同名类方法作委托，
    修复逻辑与拆分前逐字符一致。
    """
    if not text:
        return text
    # 定位第一个结构起点（{ 或 [），丢弃前缀杂文
    start = -1
    for i, ch in enumerate(text):
        if ch in "{[":
            start = i
            break
    if start < 0:
        return text
    frag = text[start:]
    out: List[str] = []
    stack: List[str] = []
    in_string = False
    escape = False
    token: List[str] = []   # value 位置的裸 token 缓冲（补全前不写入 out）

    def flush_token() -> None:
        """把已缓冲的裸 token 补全后写入 out。"""
        if token:
            out.append(_complete_bare_token("".join(token)))
            token.clear()

    i = 0
    n = len(frag)
    while i < n:
        ch = frag[i]
        if in_string:
            out.append(ch)
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            i += 1
            continue
        if ch == '"':
            flush_token()
            in_string = True
            out.append(ch)
            i += 1
            continue
        if ch in "{[":
            flush_token()
            stack.append("}" if ch == "{" else "]")
            out.append(ch)
            i += 1
            continue
        if ch in "}]":
            flush_token()
            if stack:
                stack.pop()
            out.append(ch)
            i += 1
            continue
        if ch in ",:":
            flush_token()
            out.append(ch)
            i += 1
            continue
        if ch.isspace():
            flush_token()
            out.append(ch)
            i += 1
            continue
        token.append(ch)
        i += 1
    # 扫描结束：补全残余
    if in_string:
        out.append('"')          # 补闭合引号
    else:
        flush_token()
    while stack:                 # 补未闭合括号
        out.append(stack.pop())
    return "".join(out)


def _normalize_nonstandard_json(text: str) -> str:
    """将非标准 JSON 规范化：单引号字符串→双引号、Python 字面量→JSON 字面量。

    处理 LLM 常见的两种非标准输出：
    1. 单引号字符串（如 {'a': 'x'}）→ 双引号字符串；
    2. Python 风格字面量 True/False/None → JSON 的 true/false/null。

    仅在字符串字面量之外处理，避免破坏双引号字符串内部内容；
    单引号字符串内部的双引号会被转义为 \\"，内部 \\' 还原为 '。
    无任何改动时返回原文本（由调用方判定是否生效）。
    """
    if not text:
        return text
    out: List[str] = []
    i = 0
    n = len(text)
    changed = False
    while i < n:
        ch = text[i]
        if ch == '"':
            # 双引号字符串：原样复制直到闭合（含转义）
            out.append(ch)
            i += 1
            while i < n:
                c = text[i]
                out.append(c)
                if c == "\\" and i + 1 < n:
                    out.append(text[i + 1])
                    i += 2
                    continue
                if c == '"':
                    i += 1
                    break
                i += 1
            continue
        if ch == "'":
            # 单引号字符串：转为双引号，内部双引号转义、\' 还原为 '
            out.append('"')
            changed = True
            i += 1
            while i < n:
                c = text[i]
                if c == "\\" and i + 1 < n:
                    nxt = text[i + 1]
                    if nxt == "'":
                        out.append("'")
                    else:
                        out.append("\\")
                        out.append(nxt)
                    i += 2
                    continue
                if c == "'":
                    out.append('"')
                    i += 1
                    break
                if c == '"':
                    out.append('\\"')
                    i += 1
                    continue
                out.append(c)
                i += 1
            continue
        if ch.isalpha():
            # 字符串外的裸 token：True/False/None → JSON 字面量
            j = i
            while j < n and text[j].isalpha():
                j += 1
            tok = text[i:j]
            mapping = {"True": "true", "False": "false", "None": "null"}
            if tok in mapping:
                out.append(mapping[tok])
                changed = True
            else:
                out.append(tok)
            i = j
            continue
        out.append(ch)
        i += 1
    return "".join(out) if changed else text


class LLMIntegration:
    """LLM集成管理器"""
    # 结构化错误串统一前缀：预算拒绝/缺Key/初始化失败/调用失败等非正常内容
    # 一律以此前缀返回，供调用方用 is_error_response() 判定并跳过落盘，
    # 避免“LLM调用错误: ...”被当成正常文档正文写入（R10 成本封顶的配套防御）。
    ERROR_PREFIX = "LLM调用错误:"

    @classmethod
    def is_error_response(cls, text: str) -> bool:
        """判断 chat_completion 返回值是否为结构化错误串（而非正常内容）。"""
        return bool(text) and text.startswith(cls.ERROR_PREFIX)

    def __init__(self, config: Optional[LLMConfig] = None):
        if config is None:
            config = self._load_config_from_settings()
        self.config = config
        self.client = None
        self._init_client()
        # ── 成本/输出封顶（R10，规避 OpenWiki #44/#51 坑）──
        # 单次 wiki 生成的 LLM 调用次数上限与单文档输出字符上限，
        # 超限时返回结构化错误/截断标记，而非无界消耗 token 或产出超长文本。
        self.output_cap_chars: int = settings.WIKI_LLM_OUTPUT_CAP_CHARS
        self.call_budget: int = settings.WIKI_LLM_CALL_BUDGET
        self._call_count: int = 0          # 实例级已用调用次数
        self._last_truncated: bool = False  # 上一次调用是否因超长被截断
    
    @staticmethod
    def _load_config_from_settings() -> LLMConfig:
        """加载 LLM 配置（实现已拆分至模块级 _load_llm_config_from_settings）。"""
        return _load_llm_config_from_settings()
    
    def _init_client(self):
        """初始化LLM客户端（无API Key时不初始化，留待用时提示）"""
        api_key = self.config.api_key or ""
        if not api_key:
            logger.debug("未设置API Key，LLM功能暂不可用")
            self.client = None
            return
        
        try:
            if self.config.provider == LLMProvider.OLLAMA:
                self.client = OpenAI(
                    base_url=self.config.base_url or "http://localhost:11434/v1",
                    api_key=api_key,
                    timeout=httpx.Timeout(60.0, connect=10.0), max_retries=1,
                )
            elif self.config.provider == LLMProvider.OPENAI:
                self.client = OpenAI(
                    api_key=api_key,
                    base_url=self.config.base_url or "https://api.openai.com/v1",
                    timeout=httpx.Timeout(60.0, connect=10.0), max_retries=1,
                )
            elif self.config.provider == LLMProvider.DEEPSEEK:
                self.client = OpenAI(
                    api_key=api_key,
                    base_url=self.config.base_url or "https://api.deepseek.com",
                    timeout=httpx.Timeout(60.0, connect=10.0), max_retries=1,
                )
            else:
                self.client = OpenAI(
                    api_key=api_key,
                    base_url=self.config.base_url,
                    timeout=httpx.Timeout(60.0, connect=10.0), max_retries=1,
                )
            
            logger.info(f"LLM客户端初始化完成: {self.config.provider.value}")
        except Exception as e:
            logger.warning(f"LLM客户端初始化失败: {e}")
    
    def update_config(self, config: LLMConfig):
        """更新配置"""
        self.config = config
        self._init_client()

    @staticmethod
    def _is_retryable_llm_error(e: Exception) -> bool:
        """判断 LLM 调用错误是否可重试。

        可重试：网络错误、超时、服务端 5xx、限流等临时性错误。
        不可重试：API Key 缺失、认证失败、权限/参数错误等永久性错误。
        """
        # 优先按 openai SDK 的异常类型识别
        try:
            from openai import (
                APIConnectionError, APITimeoutError,
                APIStatusError, RateLimitError,
                AuthenticationError, PermissionDeniedError, BadRequestError,
            )
            if isinstance(e, (APIConnectionError, APITimeoutError)):
                return True
            if isinstance(e, RateLimitError):
                return True
            if isinstance(e, (AuthenticationError, PermissionDeniedError, BadRequestError)):
                return False
            if isinstance(e, APIStatusError):
                # 5xx 可重试；408/429 也可重试；其余 4xx 为永久性错误
                return e.status_code >= 500 or e.status_code in (408, 429)
        except ImportError:
            # openai 异常类型不可用时，降级为按状态码/关键字识别
            pass

        # 兜底：按状态码 / 关键字识别
        status = getattr(e, "status_code", None)
        if isinstance(status, int):
            if 500 <= status < 600:
                return True
            if status in (408, 429):
                return True
            if 400 <= status < 500:
                return False
        name = type(e).__name__.lower()
        msg = str(e).lower()
        # 网络/超时类异常视为可重试
        if ("connection" in name or "timeout" in name or "network" in name
                or "socket" in name or "timed out" in msg):
            return True
        # 明确的服务端错误可重试
        if "server" in name or "internal" in name:
            return True
        # 其余默认视为不可重试，避免对永久错误做无意义重试
        return False

    @staticmethod
    def _extract_balanced_json_fragment(text: str, open_char: str = '{', close_char: str = '}') -> str:
        """从文本中提取括号平衡的 JSON 片段。

        从第一个开括号开始，逐字符扫描（正确处理字符串字面量与转义），
        直到括号完全闭合；若无法闭合则返回空字符串。
        """
        start = text.find(open_char)
        if start < 0:
            return ""
        depth = 0
        in_string = False
        escape = False
        for i in range(start, len(text)):
            ch = text[i]
            if in_string:
                if escape:
                    escape = False
                elif ch == '\\':
                    escape = True
                elif ch == '"':
                    in_string = False
                continue
            if ch == '"':
                in_string = True
            elif ch == open_char:
                depth += 1
            elif ch == close_char:
                depth -= 1
                if depth == 0:
                    return text[start:i + 1]
        return ""

    @staticmethod
    def _strip_code_block(text: str) -> str:
        """剥离 LLM 返回的 Markdown 代码块包裹。

        兼容 ```json ... ``` 与 ``` ... ``` 两种形式，且允许 ``` 后紧跟 JSON
        （不强制换行，如 ```json{...}```）；未命中代码块时原样返回。
        """
        if not text or "```" not in text:
            return text
        m = re.search(r"```(?:json)?\s*", text, re.IGNORECASE)
        if not m:
            return text
        end = text.find("```", m.end())
        if end < 0:
            return text
        return text[m.end():end].strip()

    @classmethod
    def _try_parse_json(cls, text: str) -> Optional[Any]:
        """尝试从 LLM 返回文本中解析出 JSON 对象/数组。

        依次尝试：
        1. 整体解析（原始文本，避免误剥离合法 JSON 内部的 ``` 等）；
        2. 剥离 Markdown 代码块包裹（```json ... ``` / ``` ... ```）后整体解析；
        3. 规范化非标准 JSON（单引号字符串、True/False/None 字面量），
           使后续片段提取/截断修复能正确处理非标准字面量；
        4. 截取花括号/方括号平衡的合法片段后再解析（处理前后自然语言）；
        5. 修复截断值（LLM 常因 max_tokens 截断）：补全字符串引号、
           裸 token（tru→true/fals→false/nul→null）与未闭合括号后解析。
        解析失败返回 None。
        """
        if not text:
            return None
        # 1. 整体解析（原始文本）
        try:
            return json.loads(text)
        except (json.JSONDecodeError, ValueError):
            pass
        # 2. 剥离 Markdown 代码块包裹（```json ... ``` 或 ``` ... ```），露出纯 JSON
        stripped = cls._strip_code_block(text)
        if stripped != text:
            try:
                return json.loads(stripped)
            except (json.JSONDecodeError, ValueError):
                pass
            text = stripped
        # 3. 规范化非标准 JSON（单引号字符串、Python 字面量 True/False/None）。
        #    提前规范化可避免"内嵌合法片段被误判为顶层"：若单引号对象解析失败，
        #    其内部的双引号/标准数组片段（如 [1,2]）可能被步骤 4 误提取为顶层。
        #    同时保留原始文本：散文撇号（如 user's）等被规范化转成双引号后可能
        #    破坏片段提取，需在规范化失败时回退原始文本重试。
        original = text
        normalized = cls._normalize_nonstandard_json(text)
        if normalized != text:
            text = normalized

        def _extract_parse(t: str) -> Optional[Any]:
            # 4. 截取平衡片段。定位整个响应中最早出现的结构分隔符（'[' 或 '{'），
            #    以更靠前的那个作为优先顶层类型：若数组先出现说明期望顶层是数组，
            #    应先按数组截取完整内容（否则对象优先会把数组截成第一个元素对象，导致
            #    调用方期望 list 却拿到 dict，误判"解析失败"）。
            #    不能只看首字符——LLM 常在 JSON 前加说明文字（如 "Here is the list:"）。
            i_open = t.find('[')
            i_brace = t.find('{')
            if i_open < 0:
                i_open = len(t) + 1
            if i_brace < 0:
                i_brace = len(t) + 1
            order = ('[', '{') if i_open < i_brace else ('{', '[')
            for open_char, close_char in ((c, ']' if c == '[' else '}') for c in order):
                fragment = cls._extract_balanced_json_fragment(t, open_char, close_char)
                if fragment:
                    try:
                        return json.loads(fragment)
                    except (json.JSONDecodeError, ValueError):
                        continue
            # 5. 修复截断值后重试（t 已规范化，覆盖"单引号 + 截断"组合）
            repaired = cls._repair_truncated_json(t)
            if repaired and repaired != t:
                try:
                    return json.loads(repaired)
                except (json.JSONDecodeError, ValueError):
                    pass
            return None

        result = _extract_parse(text)
        if result is not None:
            return result
        # 6. 规范化文本失败时回退原始文本（规范化可能破坏散文撇号等场景）
        if original != text:
            return _extract_parse(original)
        return None

    @staticmethod
    def _complete_bare_token(tok: str) -> str:
        """补全被截断的裸 token（实现已拆分至模块级 _complete_bare_token）。"""
        return _complete_bare_token(tok)

    @classmethod
    def _repair_truncated_json(cls, text: str) -> str:
        """尽力修复被截断的 JSON 文本（实现已拆分至模块级 _repair_truncated_json）。"""
        return _repair_truncated_json(text)

    @staticmethod
    def _normalize_nonstandard_json(text: str) -> str:
        """规范化非标准 JSON（实现已拆分至模块级 _normalize_nonstandard_json）。"""
        return _normalize_nonstandard_json(text)

    @staticmethod
    def _validate_analysis_dict(data: Any) -> bool:
        """校验分析结果结构：必须为 dict 且核心字段存在、类型正确。"""
        if not isinstance(data, dict):
            return False
        if not isinstance(data.get("file_purpose"), str):
            return False
        if not isinstance(data.get("key_functions"), list):
            return False
        return True

    @staticmethod
    def _validate_reference_points(data: Any) -> bool:
        """校验借鉴点结果结构：必须为 list 且每个元素为包含核心字段的 dict。"""
        if not isinstance(data, list):
            return False
        for item in data:
            if not isinstance(item, dict):
                return False
            if not isinstance(item.get("title"), str) or not isinstance(item.get("description"), str):
                return False
        return True

    # 占位符/示例 Key：配置里常见但并非真实凭据。若误判为可用，会在无有效
    # Key 时仍发起真实 LLM 请求而空转（外部反馈：无 Key 时卡 pending 1 分多钟）。
    PLACEHOLDER_KEYS = frozenset({
        "ollama", "sk-xxx", "sk-xxxx", "your-api-key", "your_api_key",
        "none", "null", "changeme", "change-me", "api-key", "apikey",
        "placeholder", "example", "test", "sk-test", "sk-test-key",
    })

    @classmethod
    def _is_placeholder_key(cls, api_key: str) -> bool:
        """判断 API Key 是否为占位符/示例值（非真实凭据）。"""
        if not api_key:
            return True
        low = api_key.strip().lower()
        if low in cls.PLACEHOLDER_KEYS:
            return True
        if low.startswith("sk-") and any(
                marker in low for marker in ("xxx", "your", "example", "placeholder", "changeme")):
            return True
        return False

    def is_available(self) -> bool:
        """判断 LLM 是否真正可用（客户端已初始化且存在有效 API Key）。

        供各"依赖 LLM 才能产出人话内容"的入口做硬阻断判断：LLM 不可用时，
        应明确告知调用方"需要 LLM 请先配置 API Key"，而不是降级产出占位/机械内容。
        占位符/示例 Key（如 ollama、sk-xxx）不算有效凭据，避免无 Key 时仍发起
        真实 LLM 请求而空转。
        """
        if self.client is None:
            return False
        api_key = getattr(self.config, "api_key", "") if self.config is not None else ""
        return not self._is_placeholder_key(api_key)

    # ── 成本/输出封顶（R10）──

    def reset_budget(self) -> None:
        """重置本次调用预算计数（_call_count 归零）。

        单次 wiki 生成结束 / 开始新一轮生成时调用，重新获得完整调用额度。
        """
        self._call_count = 0

    def budget_remaining(self) -> int:
        """返回剩余调用额度（call_budget - 已用次数，下限 0）。"""
        return max(0, self.call_budget - self._call_count)

    def last_output_truncated(self) -> bool:
        """返回上一次 LLM 调用是否因超过输出字符上限而被截断。"""
        return self._last_truncated

    def _cap_output(self, text: str) -> str:
        """对 LLM 返回文本做输出封顶：超限截断并附加标记。

        保持返回类型为 str（不破坏现有调用方签名）；截断状态记录在
        self._last_truncated，供调用方通过 last_output_truncated() 查询。
        """
        self._last_truncated = False
        if not text or len(text) <= self.output_cap_chars:
            return text
        self._last_truncated = True
        return (text[:self.output_cap_chars]
                + f"\n\n<!-- truncated: 输出超过 {self.output_cap_chars} 字符上限 -->")

    def chat_completion(self, messages: List[Dict[str, str]], **kwargs) -> str:
        """执行聊天补全（含有限重试与降级回退）

        DeepSeek V4 兼容说明：V4 是推理模型，输出优先写入 reasoning_content，
        content 在推理完成前可能为空。因此本方法：
        1. 支持通过 extra_body 传 thinking 参数（如 {"thinking": {"type": "enabled"}}）；
        2. 当 message.content 为空时，回退读取 reasoning_content，避免误判为"空响应"。
        """
        # 成本封顶（R10）：调用次数预算用尽则拒绝本次调用（返回结构化错误，不抛异常）
        if self._call_count >= self.call_budget:
            logger.warning(f"LLM调用预算已用尽（{self._call_count}/{self.call_budget}），本次调用被拒绝")
            return (f"LLM调用错误: 已达本次调用预算上限（{self._call_count}/{self.call_budget}），"
                    f"请调用 reset_budget() 重置或分批处理")

        if not self.client:
            if not self.config.api_key:
                logger.warning("LLM不可用：未设置API Key。请在配置面板中填写API Key。")
                return "LLM调用错误: 未设置API Key，请在配置面板中填写"
            logger.error("LLM客户端未初始化")
            return "LLM调用错误: 客户端初始化失败"

        # 兜底：占位符/示例 Key 不发起真实请求，避免无有效凭据时连接空转
        if self._is_placeholder_key(self.config.api_key):
            logger.warning("LLM不可用：API Key 为占位符/示例值，未配置有效凭据。")
            return "LLM调用错误: 未配置有效的API Key，请在配置面板中填写"

        # 显式传入超时参数（连接超时 10s 快速失败，总超时 60s 给足生成时间）
        timeout = kwargs.get('timeout', httpx.Timeout(60.0, connect=10.0))
        max_retries = 2  # 原始请求之外最多重试 2 次（含指数退避 1s/2s）
        delay = 1
        last_error = None
        # DeepSeek V4 推理模型：允许调用方显式传 thinking 参数，否则不强制
        extra_body = kwargs.get('extra_body') or {}
        # JSON 模式：调用方可通过 response_format={"type": "json_object"} 强制模型输出
        # 合法 JSON，从 API 层约束比 prompt 提示更可靠。端点不支持时自动回退。
        response_format = kwargs.get('response_format')
        rf_active = response_format is not None
        try:
            from openai import BadRequestError
        except ImportError:
            BadRequestError = None
        for attempt in range(max_retries + 1):
            if attempt > 0:
                time.sleep(delay)
                delay *= 2  # 指数退避：1s、2s
            try:
                create_kwargs = dict(
                    model=kwargs.get('model', self.config.model),
                    messages=messages,
                    temperature=kwargs.get('temperature', self.config.temperature),
                    max_tokens=kwargs.get('max_tokens', self.config.max_tokens),
                    timeout=timeout,
                    extra_body=extra_body or None,
                )
                if rf_active:
                    create_kwargs['response_format'] = response_format
                response = self.client.chat.completions.create(**create_kwargs)
                message = response.choices[0].message
                content = message.content or ""
                # DeepSeek V4 空 content 回退：推理内容在 reasoning_content
                if not content:
                    content = getattr(message, "reasoning_content", None) or ""
                # 成本/输出封顶（R10）：成功调用计数 + 输出超长截断并附加标记
                self._call_count += 1
                return self._cap_output(content)
            except Exception as e:
                # response_format 不被端点支持（参数错误）：去掉该参数回退重试一次，
                # 避免 JSON 约束反而导致整体调用失败
                if (rf_active and BadRequestError is not None
                        and isinstance(e, BadRequestError)):
                    logger.warning(f"端点不支持 response_format，回退为普通调用重试: {e}")
                    rf_active = False
                    continue
                last_error = e
                # 永久性错误（认证失败、API Key 缺失、参数错误等）不重试
                if not self._is_retryable_llm_error(e) or attempt == max_retries:
                    break
                logger.warning(f"LLM调用临时失败，将重试({attempt + 1}/{max_retries}): {e}")
        logger.error(f"LLM调用失败: {last_error}")
        return f"LLM调用错误: {str(last_error)}"
    
    def analyze_code_context(self, code_content: str, file_path: str) -> Dict[str, Any]:
        """分析代码上下文"""
        prompt = f"""
分析以下代码文件，提供结构化的分析结果。

文件路径: {file_path}

代码内容:
```
{code_content[:5000]}
```

请以JSON格式返回分析结果，包含以下字段：
1. "file_purpose": 该文件的主要功能和用途
2. "key_functions": 关键函数列表
3. "code_style": 代码风格特点（命名规范、缩进、注释等）
4. "dependencies": 主要依赖
5. "insertion_points": 建议插入新代码的位置（包含行号和说明）
6. "optimization_points": 可优化的点

只返回JSON，不要其他解释。
"""
        
        response = self.chat_completion([
            {"role": "system", "content": "你是专业的代码分析专家。只输出合法的 JSON 对象，不要输出任何解释、前后缀文字或 Markdown 代码块。"},
            {"role": "user", "content": prompt}
        ])

        # 降级结构（默认值）
        degraded = {
            "file_purpose": "代码分析",
            "key_functions": [],
            "code_style": "standard",
            "dependencies": [],
            "insertion_points": [],
            "optimization_points": []
        }

        # 1. 解析（含修复：截取花括号平衡的合法片段）
        data = self._try_parse_json(response)
        # 2. 结构校验：字段存在、类型正确
        if data is not None and self._validate_analysis_dict(data):
            return data

        # 3. 仍失败 → 记录降级原因，绝不静默吞掉
        if data is None:
            reason = "LLM 返回内容不含合法 JSON 或无法解析"
        elif not isinstance(data, dict):
            reason = "LLM 返回的 JSON 不是对象"
        else:
            reason = "LLM 返回的 JSON 结构不完整（缺少必需字段或类型错误）"
        degraded["error"] = reason
        logger.warning(f"analyze_code_context 结果降级: {reason}; 响应片段: {response[:200]}")
        return degraded
    
    def generate_code_suggestion(
        self,
        current_code: str,
        reference_content: str,
        reference_source: str,
        insert_hint: str = ""
    ) -> CodeSuggestion:
        """生成代码借鉴建议"""
        
        prompt = f"""
基于参考资源，为现有代码生成借鉴建议。

## 当前代码
```
{current_code[:3000]}
```

## 参考资源
来源: {reference_source}

内容:
{reference_content[:4000]}

{insert_hint}

## 任务
生成完整的代码借鉴建议，包含：

1. 一个简洁的标题
2. 详细的功能描述
3. 可直接插入的代码片段（适配现有代码风格）
4. 规范的参考来源注释
5. 修改说明列表
6. 风险提示列表
7. 测试建议列表

请严格按照以下JSON格式返回：
{{
    "title": "建议标题",
    "description": "详细描述",
    "insert_position": {{"location": "函数末尾/类中/文件末尾", "hint": "插入位置说明"}},
    "code_snippet": "完整的代码，包含参考注释",
    "modification_notes": ["修改说明1", "修改说明2"],
    "risk_warnings": ["风险1", "风险2"],
    "test_suggestions": ["测试建议1", "测试建议2"]
}}

只返回JSON，不要其他内容。
"""
        
        response = self.chat_completion([
            {"role": "system", "content": "你是专业的代码顾问，擅长将开源代码和论文思路融入现有项目。只输出合法的 JSON，不要任何解释、前后缀文字或 Markdown 代码块。"},
            {"role": "user", "content": prompt}
        ])
        
        result = None
        try:
            json_start = response.find('{')
            json_end = response.rfind('}') + 1
            if json_start >= 0 and json_end > json_start:
                result = json.loads(response[json_start:json_end])
        except Exception as e:
            logger.error(f"解析LLM响应失败: {e}, 响应: {response[:200]}")
        # 回退：使用增强的容错解析（代码块包裹/前后文字/单引号/截断等）
        if not isinstance(result, dict):
            result = self._try_parse_json(response)
        if isinstance(result, dict):
            import uuid
            return CodeSuggestion(
                suggestion_id=str(uuid.uuid4())[:8],
                title=result.get('title', '代码优化建议'),
                description=result.get('description', ''),
                insert_position=result.get('insert_position', {}),
                code_snippet=result.get('code_snippet', ''),
                modification_notes=result.get('modification_notes', []),
                risk_warnings=result.get('risk_warnings', []),
                test_suggestions=result.get('test_suggestions', []),
                source_reference=reference_source
            )
        
        # 返回默认建议
        return CodeSuggestion(
            suggestion_id="default",
            title="代码参考建议",
            description=f"基于 {reference_source} 的代码参考",
            code_snippet=f"# 参考来源: {reference_source}\n# 请手动实现相关功能",
            source_reference=reference_source
        )
    
    def generate_analysis_report(self, project_analysis: Dict) -> str:
        """生成项目分析报告"""
        prompt = f"""
基于以下项目分析数据，生成一份专业的项目深度分析报告。

项目分析数据:
{json.dumps(project_analysis, ensure_ascii=False, indent=2)}

报告应包含：
1. 项目概览（规模、语言、结构）
2. 架构分析（模块划分、依赖关系）
3. 技术栈评估
4. 核心功能梳理
5. 改进建议

使用Markdown格式，专业、清晰、有深度。
"""
        
        report = self.chat_completion([
            {"role": "system", "content": "你是专业的软件架构师，擅长代码审计和项目分析。"},
            {"role": "user", "content": prompt}
        ])
        
        return report
    
    def generate_business_report(self, project_analysis, code_samples_text: str = "") -> str:
        """
        生成「给人看的」项目业务全景报告
        使用 BusinessAnalyzer 多阶段管线：先扫描 → 逐层发现业务概念 → 自评估 → 自改进 → 输出
        
        与旧版的区别：
        1. 通用性 —— 不硬编码特定项目知识，对任意代码库动态发现业务概念
        2. 自学习 —— 发现不足后自动优化分析方案再跑一遍
        3. 多层级 —— 技术架构 → 业务能力 → 用户角色 → 业务流程 → 跨端差异
        """
        # 硬阻断：业务报告是依赖 LLM 才能产出的人话报告，无 LLM 时直接明确告知，
        # 不降级产出机械/占位内容，避免编程 AI 拿到一份"看似成功实为降级"的报告。
        if not self.is_available():
            return (
                "【业务报告未生成】业务全景报告需要 LLM 才能产出，但当前未配置有效的 API Key。\n"
                "请在配置面板填写 API Key 后再生成。\n"
                "（审计、知识图谱、架构等确定性分析不受影响，可正常使用）"
            )
        try:
            from core.business_analyzer import BusinessAnalyzer
            
            analyzer = BusinessAnalyzer(llm_client=self)
            result = analyzer.analyze(project_analysis, max_iterations=3)
            report = analyzer.to_business_report(result)
            
            logger.info(f"[BusinessReport] 业务分析完成, 迭代{result.iteration_count}轮, "
                       f"得分{result.evaluation_scores[-1]:.0%}" if result.evaluation_scores else "")
            return report
            
        except Exception as e:
            logger.warning(f"[BusinessReport] BusinessAnalyzer 执行失败, 回退到旧版: {e}")
            # 回退：使用旧版方式
            return self._legacy_business_report(project_analysis, code_samples_text)
    
    def _legacy_business_report(self, project_analysis, code_samples_text: str = "") -> str:
        """（回退）旧版业务报告生成"""
        name = project_analysis.get("project_path", "").split("\\")[-1].split("/")[-1]
        total_files = project_analysis.get("total_files", 0)
        total_lines = project_analysis.get("total_lines", 0)
        languages = project_analysis.get("languages", {})
        modules_dict = project_analysis.get("modules", {})
        
        lang_str = ', '.join(f'{k}({v}文件)' for k, v in sorted(languages.items(), key=lambda x: -x[1]))
        mod_summary = []
        top_modules = {}
        for mod_path, files in modules_dict.items():
            top = mod_path.split('\\')[0].split('/')[0]
            if top not in top_modules:
                top_modules[top] = {'files': set()}
            top_modules[top]['files'].update(files)
        for mod, data in sorted(top_modules.items(), key=lambda x: -len(x[1]['files'])):
            mod_summary.append(f'- **{mod}**: {len(data["files"])}个文件')
        
        code_preview = code_samples_text[:40000] if code_samples_text else "（无代码数据）"
        
        prompt = f"""你是一位**业务架构师**。请分析以下代码项目，撰写一份**业务全景分析报告**。

【目标读者】非程序员（业务人员 / 管理者）
【要求】用通俗语言描述，不出现技术实现细节

## 项目数据
- 名称: {name}
- 文件数: {total_files}
- 代码行数: {total_lines:,}
- 语言: {lang_str}

## 模块分布
{chr(10).join(mod_summary[:10])}

## 代码样本
```
{code_preview}
```

请按以下结构输出 Markdown 报告：

### 一、项目定位 — 这个项目是做什么的（一句话）

### 二、业务模块全景 — 哪些子系统各自负责什么

### 三、用户角色 — 谁会使用这个系统，各角色能做什么

### 四、核心业务流程 — 关键操作步骤

### 五、差异对比 — Web端/桌面端差异、不同角色差异（如存在）

### 六、关键结论"""
        
        report = self.chat_completion([
            {"role": "system", "content": "你是一位擅长从代码中提取业务概念的业务架构师，能用通俗语言向非程序员解释代码架构。只输出 Markdown 报告。"},
            {"role": "user", "content": prompt}
        ])
        
        return report
    
    def extract_reference_points(self, resource_content: str) -> List[Dict[str, str]]:
        """从资源中提取可借鉴点"""
        prompt = f"""
从以下参考资源中，提取最有价值的、可以借鉴到其他项目中的核心要点。

资源内容:
{resource_content[:5000]}

请以JSON数组格式返回，每个元素包含：
- "title": 借鉴点标题
- "description": 详细说明
- "category": 分类（算法/架构/工具/最佳实践等）
- "priority": 优先级（high/medium/low）

只返回JSON数组。
"""
        
        response = self.chat_completion([
            {"role": "system", "content": "你是技术研究员，擅长从论文和开源项目中提取精华。只输出合法的 JSON 数组，不要任何解释、前后缀文字或 Markdown 代码块。"},
            {"role": "user", "content": prompt}
        ])

        # 1. 解析（含修复：截取方括号平衡的合法片段）
        data = self._try_parse_json(response)
        # 2. 结构校验：必须为数组且每个元素结构正确
        if isinstance(data, list) and self._validate_reference_points(data):
            return data

        # 3. 仍失败 → 记录降级原因到日志，返回空列表，绝不静默吞掉
        if not isinstance(data, list):
            reason = "LLM 返回内容不含合法 JSON 数组"
        else:
            reason = "LLM 返回的数组元素结构不完整（缺少必需字段或类型错误）"
        logger.warning(f"extract_reference_points 结果降级: {reason}; 响应片段: {response[:200]}")
        return []
