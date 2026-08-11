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


class LLMIntegration:
    """LLM集成管理器"""
    
    def __init__(self, config: Optional[LLMConfig] = None):
        if config is None:
            config = self._load_config_from_settings()
        self.config = config
        self.client = None
        self._init_client()
    
    @staticmethod
    def _load_config_from_settings() -> LLMConfig:
        """
        加载 LLM 配置，按优先级尝试多个来源：
        1. 环境变量（CODEREF_API_KEY / CODEREF_BASE_URL / CODEREF_MODEL）
        2. config/config.json（兼容旧版配置文件）
        3. 默认值（DeepSeek）
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
                base_url=os.environ.get("CODEREF_BASE_URL", "https://api.deepseek.com/v1"),
                model=os.environ.get("CODEREF_MODEL", "deepseek-chat"),
                temperature=_safe_float(os.environ.get("CODEREF_TEMPERATURE"), 0.7),
                max_tokens=_safe_int(os.environ.get("CODEREF_MAX_TOKENS"), 4096),
            )

        

        # ── 优先级 3：config/config.json（旧版配置文件，兼容） ──
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
                            base_url=data.get("llm_base_url", data.get("base_url", "https://api.deepseek.com/v1")),
                            model=data.get("llm_model", data.get("model_name", "deepseek-chat")),
                            temperature=float(data.get("llm_temperature", data.get("temperature", 0.7))),
                            max_tokens=int(data.get("llm_max_tokens", data.get("max_tokens", 4096))),
                        )
                    else:
                        logger.debug(f"config.json 中 api_key 为占位符或空，跳过: {cfg_path}")
        except Exception as e:
            logger.debug(f"读取 config.json 失败: {e}")

        # ── 优先级 4：默认值（无 API Key） ──
        logger.debug("未找到有效的 LLM 配置（环境变量/config.json 均无），LLM 功能暂不可用")
        return LLMConfig(
            provider=LLMProvider.DEEPSEEK,
            base_url="https://api.deepseek.com/v1",
            model="deepseek-chat",
            api_key=""
        )
    
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
                    timeout=120, max_retries=1,
                )
            elif self.config.provider == LLMProvider.OPENAI:
                self.client = OpenAI(
                    api_key=api_key,
                    base_url=self.config.base_url or "https://api.openai.com/v1",
                    timeout=120, max_retries=1,
                )
            elif self.config.provider == LLMProvider.DEEPSEEK:
                self.client = OpenAI(
                    api_key=api_key,
                    base_url=self.config.base_url or "https://api.deepseek.com/v1",
                    timeout=120, max_retries=1,
                )
            else:
                self.client = OpenAI(
                    api_key=api_key,
                    base_url=self.config.base_url,
                    timeout=120, max_retries=1,
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

    @classmethod
    def _try_parse_json(cls, text: str) -> Optional[Any]:
        """尝试从 LLM 返回文本中解析出 JSON 对象/数组。

        依次尝试：
        1. 整体解析；
        2. 截取花括号/方括号平衡的合法片段后再解析；
        3. 修复截断值（LLM 常因 max_tokens 截断）：补全字符串引号、
           裸 token（tru→true/fals→false/nul→null）与未闭合括号后解析。
        解析失败返回 None。
        """
        if not text:
            return None
        # 1. 整体解析
        try:
            return json.loads(text)
        except (json.JSONDecodeError, ValueError):
            pass
        # 2. 截取平衡片段（对象优先，其次数组）
        for open_char, close_char in (('{', '}'), ('[', ']')):
            fragment = cls._extract_balanced_json_fragment(text, open_char, close_char)
            if fragment:
                try:
                    return json.loads(fragment)
                except (json.JSONDecodeError, ValueError):
                    continue
        # 3. 修复截断值后重试
        repaired = cls._repair_truncated_json(text)
        if repaired and repaired != text:
            try:
                return json.loads(repaired)
            except (json.JSONDecodeError, ValueError):
                pass
        return None

    @staticmethod
    def _complete_bare_token(tok: str) -> str:
        """补全被截断的裸 token：tr/tru → true，fa/fal/fals → false，nu/nul → null。"""
        t = tok.strip()
        low = t.lower()
        for prefix, full in (("true", "true"), ("false", "false"), ("null", "null")):
            if prefix.startswith(low) and low:
                return full
        if re.fullmatch(r"-?\d*\.?\d*(?:[eE][+-]?\d*)?", t):
            return t
        return tok

    @classmethod
    def _repair_truncated_json(cls, text: str) -> str:
        """尽力修复被截断的 JSON 文本（LLM 因 max_tokens 截断的常见残缺）。

        处理三类残缺：
        1. 字符串字面量被截断（如 `{"a": "unfin`，缺闭合引号）；
        2. 裸 token 被截断（如 `"verified": tr`，缺结尾）；
        3. 数组/对象括号未闭合（如 `[{"x":1`，缺 `}]`）。

        仅当能修复时返回修复后的文本，否则返回原文本（由调用方判定）。
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
                out.append(cls._complete_bare_token("".join(token)))
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

    def is_available(self) -> bool:
        """判断 LLM 是否真正可用（客户端已初始化且存在有效 API Key）。

        供各"依赖 LLM 才能产出人话内容"的入口做硬阻断判断：LLM 不可用时，
        应明确告知调用方"需要 LLM 请先配置 API Key"，而不是降级产出占位/机械内容。
        """
        if self.client is None:
            return False
        api_key = getattr(self.config, "api_key", "") if self.config is not None else ""
        return bool(api_key)

    def chat_completion(self, messages: List[Dict[str, str]], **kwargs) -> str:
        """执行聊天补全（含有限重试与降级回退）"""
        if not self.client:
            if not self.config.api_key:
                logger.warning("LLM不可用：未设置API Key。请在配置面板中填写API Key。")
                return "LLM调用错误: 未设置API Key，请在配置面板中填写"
            logger.error("LLM客户端未初始化")
            return "LLM调用错误: 客户端初始化失败"

        # 显式传入超时参数
        timeout = kwargs.get('timeout', 120)
        max_retries = 2  # 原始请求之外最多重试 2 次（含指数退避 1s/2s）
        delay = 1
        last_error = None
        for attempt in range(max_retries + 1):
            if attempt > 0:
                time.sleep(delay)
                delay *= 2  # 指数退避：1s、2s
            try:
                response = self.client.chat.completions.create(
                    model=kwargs.get('model', self.config.model),
                    messages=messages,
                    temperature=kwargs.get('temperature', self.config.temperature),
                    max_tokens=kwargs.get('max_tokens', self.config.max_tokens),
                    timeout=timeout
                )
                return response.choices[0].message.content or ""
            except Exception as e:
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
            {"role": "system", "content": "你是专业的代码分析专家，只返回JSON格式的分析结果。"},
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
            {"role": "system", "content": "你是专业的代码顾问，擅长将开源代码和论文思路融入现有项目。只返回JSON。"},
            {"role": "user", "content": prompt}
        ])
        
        try:
            json_start = response.find('{')
            json_end = response.rfind('}') + 1
            if json_start >= 0 and json_end > json_start:
                result = json.loads(response[json_start:json_end])
                
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
        except Exception as e:
            logger.error(f"解析LLM响应失败: {e}, 响应: {response[:200]}")
        
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
            {"role": "system", "content": "你是技术研究员，擅长从论文和开源项目中提取精华。只返回JSON。"},
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
