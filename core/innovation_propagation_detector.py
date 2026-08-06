# -*- coding: utf-8 -*-
"""
创新传播检测器 —— 发现"模块A有，模块B该有但没有"的设计模式盲区

场景：模块A实现了一种不错的微创新（如5w1h问法、TDDDR骨架降噪、输入校验链），
      理论上同类型的模块B也应该使用，但被遗漏了。
      这种盲区全靠人工回忆和反思才能发现，本检测器自动化这个过程。

检测流程：
  Step 1 — 能力签名提取（AST）：提取每个模块的"能力标签"
  Step 2 — 模块聚类：将共享 ≥3 个能力标签的模块归为同类
  Step 3 — 模式提取（LLM）：对每个模块，让LLM识别可复用的设计模式
  Step 4 — 缺口检测（LLM）：同类模块间交叉对比，发现缺失的模式

设计原则：
  - 通用性：不依赖预设规则，LLM 自行发现模式
  - 可控误报：≥2 模块共享上下文才做对比，≥60% 采用率才建议传播
  - 降级运行：LLM 不可用时自动降级为纯结构对比

与 BlindSpotDetector 的互补：
  - BlindSpotDetector 检测"缺少什么（文档、依赖、模块）"
  - InnovationPropagationDetector 检测"应该传播什么（设计模式、架构决策）"

作者: PersuadeAI Team
版本: v1.0
"""

import os
import re
import ast
import json
from typing import Dict, List, Optional, Set, Tuple, Any
from collections import defaultdict
from dataclasses import dataclass, field

from loguru import logger
from core.shared_filter import SharedFilter


# ═══════════════════════════════════════════════════════════════════
# 数据结构
# ═══════════════════════════════════════════════════════════════════

@dataclass
class CapabilitySignature:
    """模块的能力签名 —— 描述一个模块"有什么能力"的标签集合"""
    file_path: str
    module_name: str
    # ── 能力标签 ──
    has_prompt_template: bool = False        # 有 {placeholder} 格式的 prompt 模板
    has_llm_call: bool = False               # 有 LLM 调用（chat/complete/generate）
    has_validation_chain: bool = False       # 有 validate → sanitize → normalize 校验链
    has_pipeline_flow: bool = False          # 有管道式处理流（函数链式调用）
    has_retry_logic: bool = False            # 有重试/回退逻辑
    has_error_handling: bool = False         # 有 try/except 包裹
    has_decorators: bool = False             # 有自定义装饰器
    has_context_manager: bool = False        # 有 with 语句
    has_async_code: bool = False             # 有 async/await
    has_config_loading: bool = False         # 有配置加载（yaml/json/env）
    has_file_io: bool = False                # 有文件读写
    has_network_call: bool = False           # 有网络请求
    has_class_structure: bool = False        # 有类定义
    has_type_hints: bool = False             # 有类型注解
    has_docstring: bool = False              # 有文档字符串
    # ── 元数据 ──
    tags: List[str] = field(default_factory=list)  # 激活的能力标签列表
    line_count: int = 0
    function_count: int = 0


@dataclass
class InnovationPattern:
    """从一个模块中提取的可复用设计模式"""
    module_name: str
    file_path: str
    pattern_name: str                       # 模式名称（如"5w1h 问法"）
    pattern_category: str                   # 分类（methodology / architecture / validation / prompt）
    description: str                        # 描述
    source_location: str                    # 源代码位置（行号或函数名）
    confidence: float = 0.8                 # LLM 置信度


@dataclass
class PropagationGap:
    """一个传播缺口 —— 同类型的模块B缺少了模块A的模式"""
    source_module: str                      # 来源模块（有该模式）
    source_file: str
    target_module: str                      # 目标模块（缺少该模式）
    target_file: str
    pattern: InnovationPattern              # 应该传播的模式
    suggestion: str                         # 具体插入建议
    cluster_size: int = 0                   # 同一聚类中的模块数
    adoption_rate: float = 0.0              # 该模式在聚类中的采用率


# ═══════════════════════════════════════════════════════════════════
# 检测器主体
# ═══════════════════════════════════════════════════════════════════

class InnovationPropagationDetector:
    """创新传播检测器 —— 发现"这里有，那里该有却没有"的设计模式盲区"""

    # 排除扫描的目录（仅通用目录，不包含任何项目特定名称）
    EXCLUDED_DIRS = {
        "__pycache__", ".git", ".venv", "venv", "node_modules",
        ".pytest_cache", ".mypy_cache", ".ruff_cache", "dist", "build",
        ".eggs", ".tox", "egg-info",
    }
    # 排除的目录名前缀（匹配 Python3.14 等捆绑运行时目录）
    EXCLUDED_DIR_PREFIXES = ("Python3.", "Python2.", "pypy",)

    # 排除扫描的文件名
    EXCLUDED_FILES = {
        "__init__.py", "setup.py", "conftest.py",
    }

    # 自动跳过非代码目录的阈值：如果目录中 .py 文件占比低于此值，跳过
    NON_CODE_DIR_THRESHOLD = 0.2

    # 聚类所需的最小共享标签数（基础标签如 error_handling/docstring 太常见，需更高阈值）
    MIN_SHARED_TAGS = 5

    # 专业标签 —— 指示模块的特定领域（权重更高）
    # 注意：不包含 prompt_template/network_call/pipeline_flow —— 这些太普遍
    SPECIALIZED_TAGS = {
        "llm_call", "validation_chain", "retry_logic",
    }

    # 聚类要求：至少共享 1 个专业标签
    MIN_SPECIALIZED_SHARED = 1

    # 模式传播的最小采用率（低于此值才建议传播）
    MAX_ADOPTION_FOR_GAP = 0.6

    # LLM 调用最大 token
    LLM_MAX_TOKENS = 2048

    # 进入报告的缺口建议上限（只保留最值得传播的 top-N，每条都用 LLM 精建议）
    MAX_GAP_SUGGESTIONS = 30

    # 单次 LLM 排名调用中展示的最大候选缺口数（超过则先按采纳率预裁剪到该池，
    # 避免一次 prompt 过长。LLM 在池内按真实价值重新挑选 top-N。）
    LLM_RANK_POOL = 150

    # LLM 总预算（Step 3 模式提取 + Step 4 排名 + Step 4 缺口建议共用），仅作极端兜底。
    # 预算构成: Step3 至多 20 次 + Step4 排名 1 次 + 缺口精建议至多 30 次 = 51。
    # 正常路径靠 MAX_GAP_SUGGESTIONS 截断，不会触达此预算。
    LLM_TOTAL_BUDGET = 20 + 1 + 30

    def __init__(self, llm_client=None):
        """
        Args:
            llm_client: 可选的 LLMIntegration 实例。若为 None，自动尝试初始化。
        """
        self._llm_client = llm_client
        self._llm_available = False
        # 实例级签名缓存（按 project_path），避免同实例内重复收集（innovation_engine 复用）
        self._sig_cache_path = None
        self._sig_cache = None
        # LLM 总预算兜底（跨 Step 3/4）
        self._llm_budget = 0

    def _ensure_llm(self):
        """延迟初始化 LLM 客户端"""
        if self._llm_client is not None:
            self._llm_available = True
            return
        try:
            from core.llm_integration import LLMIntegration
            # 统一走 _load_config_from_settings() 配置源（环境变量/QSettings/config.json），
            # 不在此处手写 LLMConfig，避免凭据与配置散落各处。
            self._llm_client = LLMIntegration()
            self._llm_available = bool(self._llm_client.client)
        except Exception as e:
            logger.warning(f"LLM 初始化失败，将使用纯结构对比模式: {e}")
            self._llm_available = False

    # ─── Step 1: 能力签名提取 ────────────────────────────────────

    def _extract_capability_signature(self, file_path: str, content: str) -> CapabilitySignature:
        """
        通过 AST 提取模块的能力签名

        不用正则，用 AST 精确解析，避免把字符串/注释中的关键字误判为能力。
        """
        rel_path = os.path.basename(file_path)
        sig = CapabilitySignature(
            file_path=file_path,
            module_name=rel_path.replace(".py", ""),
            line_count=len(content.splitlines()),
        )

        try:
            tree = ast.parse(content)
        except SyntaxError:
            return sig

        # 收集函数/类定义
        func_defs = []
        class_defs = []
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef):
                func_defs.append(node)
            elif isinstance(node, ast.ClassDef):
                class_defs.append(node)

        sig.function_count = len(func_defs)
        sig.has_class_structure = len(class_defs) > 0

        # ── 能力检测（AST 遍历） ──

        for node in ast.walk(tree):
            # 类型注解
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if node.returns or any(a.annotation for a in node.args.args):
                    sig.has_type_hints = True
                if ast.get_docstring(node):
                    sig.has_docstring = True

            # 装饰器
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                if node.decorator_list:
                    sig.has_decorators = True

            # 上下文管理器
            if isinstance(node, ast.With):
                sig.has_context_manager = True

            # 异步代码
            if isinstance(node, ast.AsyncFunctionDef):
                sig.has_async_code = True

            # try/except
            if isinstance(node, ast.Try):
                sig.has_error_handling = True

            # 字符串模板 (f-string 或 .format)
            if isinstance(node, ast.JoinedStr):
                sig.has_prompt_template = True
            if isinstance(node, ast.Call):
                if isinstance(node.func, ast.Attribute) and node.func.attr == "format":
                    if isinstance(node.func.value, ast.Constant) and isinstance(node.func.value.value, str):
                        sig.has_prompt_template = True

            # LLM 调用检测
            if isinstance(node, ast.Call):
                if isinstance(node.func, ast.Attribute):
                    func_name = node.func.attr.lower()
                    if any(kw in func_name for kw in ("chat", "complete", "generate", "completion")):
                        sig.has_llm_call = True
                elif isinstance(node.func, ast.Name):
                    if node.func.id.lower() in ("chat", "generate", "complete"):
                        sig.has_llm_call = True

            # 网络请求
            if isinstance(node, ast.Call):
                if isinstance(node.func, ast.Attribute):
                    func_name = node.func.attr.lower()
                    if any(kw in func_name for kw in ("get", "post", "request", "fetch", "urlopen")):
                        sig.has_network_call = True

            # 文件 I/O
            if isinstance(node, ast.Call):
                if isinstance(node.func, ast.Name):
                    if node.func.id in ("open", "read", "write"):
                        sig.has_file_io = True

            # 配置加载
            if isinstance(node, ast.Call):
                if isinstance(node.func, ast.Attribute):
                    func_name = node.func.attr.lower()
                    if any(kw in func_name for kw in ("load", "safe_load", "getenv", "environ")):
                        sig.has_config_loading = True

        # ── 高级能力：重试逻辑 ──
        content_lower = content.lower()
        if any(kw in content_lower for kw in ("retry", "backoff", "retries", "max_attempts")):
            sig.has_retry_logic = True

        # ── 高级能力：校验链 ──
        validate_count = len(re.findall(r'\bvalidate\b', content_lower))
        sanitize_count = len(re.findall(r'\bsanitize\b', content_lower))
        normalize_count = len(re.findall(r'\bnormalize\b', content_lower))
        if validate_count >= 2 and (sanitize_count >= 1 or normalize_count >= 1):
            sig.has_validation_chain = True

        # ── 高级能力：管道流（超过3个连续函数调用在同一行） ──
        pipeline_pattern = re.findall(r'(\w+\([^)]*\))\s*\.\s*(\w+\([^)]*\))', content)
        if len(pipeline_pattern) >= 2:
            sig.has_pipeline_flow = True

        # ── 收集激活的标签 ──
        for attr_name in dir(sig):
            if attr_name.startswith("has_") and getattr(sig, attr_name):
                sig.tags.append(attr_name[4:])  # 去掉 "has_" 前缀

        return sig

    # ─── Step 2: 模块聚类 ───────────────────────────────────────

    def _cluster_modules(self, signatures: List[CapabilitySignature]) -> List[List[CapabilitySignature]]:
        """
        基于能力标签的模块聚类

        两个模块共享 ≥ MIN_SHARED_TAGS 个标签 → 归为同一聚类。
        使用并查集合并重叠的聚类。
        """
        n = len(signatures)
        if n <= 1:
            return [signatures]

        # 构建邻接表
        parent = list(range(n))

        def find(x):
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        def union(a, b):
            parent[find(a)] = find(b)

        for i in range(n):
            tags_i = set(signatures[i].tags)
            for j in range(i + 1, n):
                tags_j = set(signatures[j].tags)
                shared = len(tags_i & tags_j)
                shared_specialized = len((tags_i & tags_j) & self.SPECIALIZED_TAGS)
                # 必须同时满足：总标签 >= MIN_SHARED_TAGS 且专业标签 >= MIN_SPECIALIZED_SHARED
                if shared >= self.MIN_SHARED_TAGS and shared_specialized >= self.MIN_SPECIALIZED_SHARED:
                    union(i, j)

        # 收集聚类
        cluster_map = defaultdict(list)
        for i in range(n):
            root = find(i)
            cluster_map[root].append(signatures[i])

        clusters = list(cluster_map.values())
        # 按聚类大小降序排列
        clusters.sort(key=len, reverse=True)

        logger.info(f"聚类完成: {len(clusters)} 个聚类，最大 {len(clusters[0]) if clusters else 0} 个模块")
        return clusters

    # ─── Step 3: 模式提取（LLM） ──────────────────────────────────

    def _extract_patterns_for_module(self, sig: CapabilitySignature, content: str) -> List[InnovationPattern]:
        """使用 LLM 从单个模块中提取可复用的设计模式"""
        if not self._llm_available:
            return []

        # 限制内容长度，避免 token 超限
        snippet = content[:5000]
        if len(content) > 5000:
            snippet += "\n\n... (内容截断，共 {} 行) ...".format(sig.line_count)

        prompt = f"""你是一个代码审查专家。请分析以下模块，找出其中**值得推广到其他模块的设计模式或微创新**。

只关注项目特有的、非标准库、非框架约定的设计决策。例如：
- 特定的问法/方法论（如 "5w1h 问法"）
- 管线架构（如 "TDDDR 骨架降噪"）
- 校验链设计（如 "输入 → 校验 → 清洗 → 标准化"）
- 错误处理策略
- Prompt 模板设计

模块名: {sig.module_name}
能力标签: {', '.join(sig.tags)}

```python
{snippet}
```

严格按以下 JSON 格式输出（只输出 JSON，不要其他文字）：
[
  {{
    "pattern_name": "模式名称（简短，10字以内）",
    "pattern_category": "methodology / architecture / validation / prompt / error_handling",
    "description": "一句话描述这个模式是什么",
    "source_location": "行号或函数名",
    "confidence": 0.8
  }}
]

如果没有值得推广的模式，输出: []"""

        try:
            messages = [{"role": "user", "content": prompt}]
            response = self._llm_client.chat_completion(
                messages,
                temperature=0.2,
                max_tokens=1024,
            )
            return self._parse_pattern_response(response, sig)
        except Exception as e:
            logger.warning(f"LLM 模式提取失败 [{sig.module_name}]: {e}")
            return []

    def _parse_pattern_response(self, response: str, sig: CapabilitySignature) -> List[InnovationPattern]:
        """解析 LLM 返回的模式列表"""
        try:
            # 尝试提取 JSON
            json_start = response.find("[")
            json_end = response.rfind("]") + 1
            if json_start >= 0 and json_end > json_start:
                data = json.loads(response[json_start:json_end])
                patterns = []
                for item in data:
                    patterns.append(InnovationPattern(
                        module_name=sig.module_name,
                        file_path=sig.file_path,
                        pattern_name=item.get("pattern_name", "未知模式"),
                        pattern_category=item.get("pattern_category", "architecture"),
                        description=item.get("description", ""),
                        source_location=item.get("source_location", ""),
                        confidence=float(item.get("confidence", 0.8)),
                    ))
                return patterns
        except (json.JSONDecodeError, KeyError, ValueError) as e:
            logger.warning(f"模式解析失败 [{sig.module_name}]: {e}")
        return []

    # ─── Step 4: 缺口检测（LLM） ──────────────────────────────────

    def _detect_gaps_in_cluster(
        self,
        cluster: List[CapabilitySignature],
        all_patterns: Dict[str, List[InnovationPattern]],
    ) -> List[PropagationGap]:
        """
        在同一聚类中检测传播缺口

        对聚类中的每个模块对（源→目标），检查源模块的模式是否在目标模块中缺失。
        仅当该模式在聚类中的采用率低于 MAX_ADOPTION_FOR_GAP 时才报告缺口。
        """
        gaps = []
        if len(cluster) < 2:
            return gaps

        # 收集聚类中所有模式名
        cluster_pattern_names: Dict[str, List[InnovationPattern]] = defaultdict(list)
        for sig in cluster:
            patterns = all_patterns.get(sig.module_name, [])
            for p in patterns:
                cluster_pattern_names[p.pattern_name].append(p)

        cluster_size = len(cluster)

        for sig in cluster:
            own_patterns = all_patterns.get(sig.module_name, [])
            own_names = {p.pattern_name for p in own_patterns}

            for pattern_name, pattern_instances in cluster_pattern_names.items():
                # 跳过自己已有的模式
                if pattern_name in own_names:
                    continue

                # 计算采用率
                adoption_rate = len(pattern_instances) / cluster_size
                if adoption_rate >= self.MAX_ADOPTION_FOR_GAP:
                    continue

                # 找到一个代表性实例
                ref = pattern_instances[0]

                # 生成建议（此处仅占位；真正的 LLM 精建议在 detect() 中
                # 按价值截断后、受 LLM 预算约束地统一生成）
                suggestion = (
                    f"建议在 {sig.module_name} 中引入「{pattern_name}」模式："
                    f"{ref.description}（参考 {ref.module_name} 的 {ref.source_location}）"
                )

                gaps.append(PropagationGap(
                    source_module=ref.module_name,
                    source_file=ref.file_path,
                    target_module=sig.module_name,
                    target_file=sig.file_path,
                    pattern=ref,
                    suggestion=suggestion,
                    cluster_size=cluster_size,
                    adoption_rate=adoption_rate,
                ))

        return gaps

    def _detect_structural_gaps(self, cluster: List[CapabilitySignature]) -> List[PropagationGap]:
        """
        纯结构对比：检测同聚类中 A 有但 B 没有的专业标签

        不依赖 LLM，基于 AST 标签做简单的缺失检测。
        """
        gaps = []
        if len(cluster) < 2:
            return gaps

        cluster_size = len(cluster)

        tag_descriptions = {
            "llm_call": "LLM 调用能力",
            "validation_chain": "输入校验链（validate → sanitize → normalize）",
            "retry_logic": "重试/回退逻辑",
        }
        # 详细说明：让人类看懂每个标签到底是什么意思
        tag_explanations = {
            "llm_call": (
                "模块中调用了 LLM（如 chat/complete/generate 等函数），说明该模块参与 AI 生成流程。"
                "如果同类模块都调 LLM 但某个模块没有，可能是漏了 LLM 增强环节。"
            ),
            "validation_chain": (
                "模块对输入数据做了「校验 → 清洗 → 标准化」的多步处理链。"
                "例如：先 validate 检查输入合法性，再 sanitize 去除危险字符，最后 normalize 统一格式。"
                "有校验链的模块比裸接收输入的模块更健壮，能防止脏数据污染下游。"
            ),
            "retry_logic": (
                "模块包含重试/回退逻辑（如 retry、backoff、max_attempts 等关键词）。"
                "当模块依赖外部服务（API、文件系统、数据库）时，有重试逻辑能防止因临时故障导致整个流程中断。"
                "没有重试逻辑的模块在遇到网络抖动或服务暂时不可用时，会直接失败。"
            ),
        }

        for tag in self.SPECIALIZED_TAGS:
            modules_with_tag = [s for s in cluster if tag in s.tags]
            if len(modules_with_tag) == 0:
                continue
            if len(modules_with_tag) == cluster_size:
                continue

            adoption_rate = len(modules_with_tag) / cluster_size
            if adoption_rate >= self.MAX_ADOPTION_FOR_GAP:
                continue

            modules_without = [s for s in cluster if tag not in s.tags]
            ref = modules_with_tag[0]
            # 把实际拥有此能力的模块列表写入 source_location，供报告展示
            have_list = ",".join(s.module_name for s in modules_with_tag)

            for target in modules_without:
                pattern = InnovationPattern(
                    module_name=ref.module_name,
                    file_path=ref.file_path,
                    pattern_name=tag_descriptions.get(tag, tag),
                    pattern_category="architecture",
                    description=f"同聚类中 {len(modules_with_tag)}/{cluster_size} 个模块具有「{tag}」能力",
                    source_location=have_list,
                    confidence=0.7,
                )
                suggestion = (
                    f"模块 {target.module_name} 缺少「{tag_descriptions.get(tag, tag)}」。\n"
                    f"{tag_explanations.get(tag, '')}\n"
                    f"同类型模块中 {adoption_rate:.0%} 已具备此能力。"
                    f"建议参考 {ref.module_name} 的实现方式。"
                )
                gaps.append(PropagationGap(
                    source_module=ref.module_name,
                    source_file=ref.file_path,
                    target_module=target.module_name,
                    target_file=target.file_path,
                    pattern=pattern,
                    suggestion=suggestion,
                    cluster_size=cluster_size,
                    adoption_rate=adoption_rate,
                ))

        return gaps

    def _generate_suggestion(self, pattern: InnovationPattern, target_sig: CapabilitySignature) -> str:
        """为传播缺口生成具体建议"""
        if not self._llm_available:
            return (
                f"模块 {target_sig.module_name} 与 {pattern.module_name} 属于同类型模块"
                f"（标签: {', '.join(target_sig.tags)}），"
                f"建议参考 {pattern.module_name} 中的「{pattern.pattern_name}」模式：{pattern.description}"
            )

        # 用 LLM 生成更精准的建议
        prompt = f"""给出一个简短的代码迁移建议（1-2句话）：

源模块: {pattern.module_name}
目标模块: {target_sig.module_name}
目标模块能力: {', '.join(target_sig.tags)}
要传播的模式: {pattern.pattern_name}
模式描述: {pattern.description}
源位置: {pattern.source_location}

请用中文给出建议，说明在目标模块的哪个位置、如何插入这个模式。"""

        try:
            messages = [{"role": "user", "content": prompt}]
            response = self._llm_client.chat_completion(
                messages,
                temperature=0.3,
                max_tokens=256,
            )
            return response.strip()
        except Exception:
            return (
                f"建议在 {target_sig.module_name} 中引入「{pattern.pattern_name}」模式："
                f"{pattern.description}（参考 {pattern.module_name} 的 {pattern.source_location}）"
            )

    # ─── 主入口 ──────────────────────────────────────────────────

    def detect(self, project_path: str, use_llm: bool = True, max_llm_rounds: int = 20) -> str:
        """
        执行创新传播检测

        Args:
            project_path: 项目根目录
            use_llm: 是否使用 LLM 进行模式提取和缺口检测
            max_llm_rounds: LLM 最大调用轮数（防止成本失控）

        Returns:
            Markdown 格式的检测报告
        """
        logger.info(f"[InnovationGap] 开始检测: {project_path}")
        if use_llm:
            self._ensure_llm()

        # 重置类级 LLM 总预算（跨 Step 3 模式提取 + Step 4 缺口建议共用）
        self._llm_budget = self.LLM_TOTAL_BUDGET

        # 加载项目专属的 cache 硬编码优化（白名单）
        SharedFilter.load_cache(project_path)

        # Step 1: 收集并分析所有 Python 文件
        signatures = self._collect_signatures(project_path)
        logger.info(f"Step 1 完成: {len(signatures)} 个模块已签名")

        if len(signatures) < 2:
            self.gaps = []
            return self._format_report([], [], signatures, use_llm)

        # Step 2: 聚类
        clusters = self._cluster_modules(signatures)
        # 过滤掉单模块聚类（无法对比）
        clusters = [c for c in clusters if len(c) >= 2]
        logger.info(f"Step 2 完成: {len(clusters)} 个有效聚类（≥2模块）")

        if not clusters:
            self.gaps = []
            return self._format_report([], clusters, signatures, use_llm)

        # Step 3: 模式提取（LLM）
        all_patterns: Dict[str, List[InnovationPattern]] = {}
        llm_rounds = 0

        if use_llm and self._llm_available:
            for cluster in clusters:
                for sig in cluster:
                    if llm_rounds >= max_llm_rounds:
                        logger.warning(f"LLM 调用达到上限 {max_llm_rounds}，停止模式提取")
                        break
                    if self._llm_budget <= 0:
                        logger.warning("LLM 总预算耗尽，停止模式提取")
                        break
                    content = self._read_file(sig.file_path)
                    if not content:
                        continue
                    patterns = self._extract_patterns_for_module(sig, content)
                    if patterns:
                        all_patterns[sig.module_name] = patterns
                    llm_rounds += 1
                    self._llm_budget -= 1
            logger.info(f"Step 3 完成: LLM 调用 {llm_rounds} 轮，提取 {sum(len(v) for v in all_patterns.values())} 个模式")

        # Step 4: 缺口检测
        all_gaps = []
        if all_patterns:
            # LLM 模式：基于提取的模式做交叉对比
            for cluster in clusters:
                gaps = self._detect_gaps_in_cluster(cluster, all_patterns)
                all_gaps.extend(gaps)
        else:
            # 纯结构模式：检测专业标签级缺口（同聚类中 A 有 B 没有的专业标签）
            for cluster in clusters:
                gaps = self._detect_structural_gaps(cluster)
                all_gaps.extend(gaps)

        # 去重（同一模式→同一目标只保留一个）
        seen = set()
        unique_gaps = []
        for g in all_gaps:
            key = (g.pattern.pattern_name, g.target_module)
            if key not in seen:
                seen.add(key)
                unique_gaps.append(g)

        # ── 缺口价值挑选 ──
        # 候选缺口超过上限时，拉一份完整清单让 LLM 一次看全，由它按真实价值
        # 挑选 top-N（而非粗造的采纳率升序）。LLM 不可用/失败时回退采纳率截断。
        if len(unique_gaps) > self.MAX_GAP_SUGGESTIONS:
            logger.info(
                f"候选缺口 {len(unique_gaps)} 超出上限 {self.MAX_GAP_SUGGESTIONS}，开始价值挑选"
            )
            unique_gaps = self._select_valuable_gaps(unique_gaps)

        # ── 预算兜底：为幸存缺口生成 LLM 精建议（受类级 LLM 总预算约束） ──
        if use_llm and self._llm_available:
            sig_by_module = {s.module_name: s for s in signatures}
            for g in unique_gaps:
                if self._llm_budget <= 0:
                    logger.warning(
                        f"LLM 总预算耗尽，剩余缺口使用结构建议（{self._llm_budget}）"
                    )
                    break
                target_sig = sig_by_module.get(g.target_module)
                if target_sig is not None:
                    llm_suggestion = self._generate_suggestion(g.pattern, target_sig)
                    if llm_suggestion.strip():
                        g.suggestion = llm_suggestion
                    self._llm_budget -= 1

        logger.info(f"Step 4 完成: {len(unique_gaps)} 个传播缺口")
        # 暴露结构化结果，供管线统一收集
        self.gaps = unique_gaps
        return self._format_report(unique_gaps, clusters, signatures, use_llm)

    # ─── 辅助方法 ────────────────────────────────────────────────

    def _collect_signatures(self, project_path: str) -> List[CapabilitySignature]:
        """收集项目中所有 Python 文件的能力签名。

        实战修复：复用 ProjectScope.should_scan() 过滤 vendored/内嵌第三方库、
        虚拟环境、资源目录，避免把 site-packages 里的数千个第三方 .py 当项目代码，
        导致签名爆炸（实测 16395 个模块）与聚类失真。
        """
        # 实例级签名缓存：同实例内重复调用（innovation_engine 的 detect/_find_adopters）
        # 只收集一次，消除重复扫描。
        if self._sig_cache_path == project_path and self._sig_cache is not None:
            return self._sig_cache

        # 复用 ProjectScope 判定哪些目录应被扫描（懒加载一次 analyze）
        from core.project_scope import ProjectScope
        scope = ProjectScope(project_path)
        scope.analyze()

        signatures = []
        for root, dirs, files in os.walk(project_path):
            # 过滤排除的目录：叠加 ProjectScope 的 vendored/虚拟环境判定
            dirs[:] = [
                d for d in dirs
                if d not in self.EXCLUDED_DIRS
                and not d.startswith(self.EXCLUDED_DIR_PREFIXES)
                and scope.should_scan(os.path.join(root, d))
                and self._is_code_dir(os.path.join(root, d))
            ]
            for f in files:
                if not f.endswith(".py"):
                    continue
                if f in self.EXCLUDED_FILES:
                    continue
                fpath = os.path.join(root, f)
                content = self._read_file(fpath)
                if not content:
                    continue
                sig = self._extract_capability_signature(fpath, content)
                if sig.tags:
                    signatures.append(sig)

        self._sig_cache_path = project_path
        self._sig_cache = signatures
        return signatures

    def _is_code_dir(self, dirpath: str) -> bool:
        """判断目录是否为代码目录（非文档/资料/配置等非代码目录）"""
        try:
            entries = list(os.scandir(dirpath))
        except (OSError, PermissionError):
            return False
        if not entries:
            return False
        files = [e for e in entries if e.is_file()]
        if not files:
            return True  # 空目录或只有子目录，继续递归
        py_count = sum(1 for e in files if e.name.endswith(".py"))
        ratio = py_count / len(files)
        return ratio >= self.NON_CODE_DIR_THRESHOLD

    def _read_file(self, file_path: str) -> Optional[str]:
        """安全读取文件"""
        try:
            with open(file_path, "r", encoding="utf-8", errors="surrogateescape") as f:
                return f.read()
        except Exception as e:
            logger.debug(f"读取文件失败 [{file_path}]: {e}")
            return None

    # ─── 缺口价值排序（LLM 一次看清单挑选 top-N） ────────────────

    def _select_valuable_gaps(self, gaps: List[PropagationGap]) -> List[PropagationGap]:
        """
        从候选缺口中挑选最值得传播的 top-N（MAX_GAP_SUGGESTIONS）。

        不再用"采纳率升序"这种粗糙代理，而是把完整候选清单一次性交给 LLM，
        由它基于模式价值、缺失风险、复用成本做语义判断挑选。

        策略：
          - 候选数 <= MAX_GAP_SUGGESTIONS：全部保留，无需挑选。
          - 候选数 <= LLM_RANK_POOL 且 LLM 可用：一次 LLM 调用读取整份清单，
            返回它认为最值得的 top-N 序号。
          - 候选数 > LLM_RANK_POOL：先按采纳率裁剪进池（兜底），LLM 在池内重选。
          - LLM 不可用或挑选失败：回退为采纳率升序截断（保守兜底）。
        """
        if len(gaps) <= self.MAX_GAP_SUGGESTIONS:
            return gaps

        # 预裁剪：候选过多时先按采纳率升序收进池（保留最值得传播的），
        # 与下方回退逻辑方向一致，避免 prompt 过长。
        pool = gaps
        if len(pool) > self.LLM_RANK_POOL:
            pool = sorted(pool, key=lambda g: g.adoption_rate)[: self.LLM_RANK_POOL]

        if not (self._llm_available and self._llm_budget > 0):
            logger.warning("LLM 不可用或预算耗尽，缺口价值挑选回退为采纳率截断")
            return sorted(pool, key=lambda g: g.adoption_rate)[: self.MAX_GAP_SUGGESTIONS]

        # 构建完整清单（紧凑文本）
        lines = []
        for i, g in enumerate(pool):
            lines.append(
                f"{i}. [{g.pattern.pattern_category}] 模式「{g.pattern.pattern_name}」"
                f" | 来源 {g.source_module} → 目标 {g.target_module}"
                f" | 聚类 {g.cluster_size} 模块 | 采用率 {g.adoption_rate:.0%}"
                f" | {g.pattern.description}"
            )
        listing = "\n".join(lines)

        prompt = f"""你是一位资深技术负责人，正在决定哪些「设计模式传播缺口」最值得优先处理。

所谓传播缺口：在同类型模块中，某个模块已经采用了某种设计模式（如校验链、重试逻辑、输入清洗），
而同类型的其他模块却没有。补上这些缺口能让项目更健壮、更一致。

下面是 {len(pool)} 个候选缺口清单。请挑选其中**最值得优先传播**的 {self.MAX_GAP_SUGGESTIONS} 个，
评判依据（按重要性）：
1. 该模式缺失带来的实际风险（越易导致线上故障/数据污染越优先）
2. 该模式对健壮性与可维护性的价值
3. 模式成熟度与复用成本（是否容易落地）

候选清单：
{listing}

严格输出一个 JSON 数组，元素为所选缺口的序号（从 0 开始），共 {self.MAX_GAP_SUGGESTIONS} 个。
只输出 JSON 数组，不要任何其他文字。例如: [3, 17, 5]"""

        try:
            messages = [{"role": "user", "content": prompt}]
            response = self._llm_client.chat_completion(
                messages,
                temperature=0.2,
                max_tokens=512,
            )
            self._llm_budget -= 1
            selected = self._parse_rank_indices(response, len(pool))
            if selected:
                # 保持候选池原有顺序，仅保留被选中的序号，取前 top-N
                chosen = [pool[i] for i in selected if 0 <= i < len(pool)]
                chosen = chosen[: self.MAX_GAP_SUGGESTIONS]
                if chosen:
                    logger.info(
                        f"LLM 排名挑选: {len(pool)} 个候选 → 保留 {len(chosen)} 个"
                    )
                    return chosen
            logger.warning("LLM 排名结果无效，回退为采纳率截断")
        except Exception as e:
            logger.warning(f"LLM 缺口价值挑选失败: {e}")
            self._llm_budget -= 1

        return sorted(pool, key=lambda g: g.adoption_rate)[: self.MAX_GAP_SUGGESTIONS]

    def _parse_rank_indices(self, response: str, pool_size: int) -> List[int]:
        """解析 LLM 返回的序号数组，容错非法序号。"""
        try:
            start = response.find("[")
            end = response.rfind("]") + 1
            if start < 0 or end <= start:
                return []
            data = json.loads(response[start:end])
            if not isinstance(data, list):
                return []
            indices = []
            for x in data:
                try:
                    idx = int(x)
                except (TypeError, ValueError):
                    continue
                if 0 <= idx < pool_size and idx not in indices:
                    indices.append(idx)
            return indices
        except (json.JSONDecodeError, ValueError) as e:
            logger.warning(f"排名结果解析失败: {e}")
            return []

    # ─── 报告生成 ────────────────────────────────────────────────

    def _format_report(
        self,
        gaps: List[PropagationGap],
        clusters: List[List[CapabilitySignature]],
        signatures: List[CapabilitySignature],
        use_llm: bool,
    ) -> str:
        """生成 Markdown 格式的检测报告"""
        lines = []
        lines.append("# 创新传播检测报告")
        lines.append("")
        lines.append(f"> 检测时间: 自动生成")
        lines.append(f"> 分析模式: {'LLM 增强' if use_llm and self._llm_available else '纯结构对比'}")
        lines.append(f"> 扫描模块: {len(signatures)} 个")
        lines.append(f"> 有效聚类: {len(clusters)} 个（≥2 模块）")
        lines.append(f"> 传播缺口: {len(gaps)} 个")
        lines.append("")

        # ── 摘要 ──
        if not gaps:
            lines.append("## ✅ 未发现传播缺口")
            lines.append("")
            lines.append("所有同类型模块之间的设计模式传播良好，没有发现遗漏。")
            lines.append("")
            if clusters:
                lines.append("### 模块聚类概览")
                lines.append("")
                for i, cluster in enumerate(clusters[:10], 1):
                    modules = [s.module_name for s in cluster]
                    shared_tags = set()
                    for s in cluster:
                        shared_tags.update(s.tags)
                    lines.append(f"**聚类 {i}** ({len(cluster)} 模块) — 共享标签: {', '.join(sorted(shared_tags)[:8])}")
                    lines.append(f"  {' → '.join(modules)}")
                    lines.append("")
            return "\n".join(lines)

        # ── 一句话总结 ──
        # 按模式名分组
        gaps_by_pattern: Dict[str, List[PropagationGap]] = defaultdict(list)
        for g in gaps:
            gaps_by_pattern[g.pattern.pattern_name].append(g)

        lines.append("## 这个功能是做什么的？")
        lines.append("")
        lines.append("> 想象这个场景：你的项目里，`research` 模块用了一种「5w1h 问法」来结构化用户输入，效果很好。")
        lines.append("> 但 `generate` 模块也是做类似的事，却没有用这个方法。")
        lines.append("> 为什么？——**因为你忘了，AI 也忘了。**")
        lines.append("> ")
        lines.append("> 这个检测器就是在自动发现这种遗漏：")
        lines.append("> 1. 先把所有模块按「能力标签」（有没有 LLM 调用、有没有校验链、有没有重试逻辑等）归类")
        lines.append("> 2. 然后找出同组里「A 模块有，但 B 模块没有」的能力差异")
        lines.append("> 3. 如果只有少数模块有某种能力，就建议传播到同类模块")
        lines.append("> ")
        lines.append("> **纯结构对比模式**（当前）：只能发现标签级缺口（比如 B 少了校验链）")
        lines.append("> **LLM 增强模式**（需 API key）：能发现方法论级缺口（比如「5w1h 问法」「TDDDR 骨架降噪」）")
        lines.append("")

        # ── 按模式分组展示 ──
        lines.append("## 发现的模式缺口")
        lines.append("")

        for pattern_name, pattern_gaps in sorted(gaps_by_pattern.items(), key=lambda x: -len(x[1])):
            first = pattern_gaps[0]
            source_modules = sorted(set(g.source_module for g in pattern_gaps))
            target_modules = sorted(set(g.target_module for g in pattern_gaps))
            missing_count = len(target_modules)
            actual_have = int(first.adoption_rate * first.cluster_size) if first.adoption_rate > 0 else len(source_modules)
            # 从 source_location 中提取实际拥有此能力的模块列表（结构模式）
            have_set = set()
            if first.pattern.source_location:
                have_set = set(first.pattern.source_location.split(","))
            if not have_set:
                have_set = set(source_modules)

            lines.append(f"### 「{pattern_name}」— {missing_count} 个模块缺失")
            lines.append("")
            lines.append(f"**这是什么？** {first.suggestion}")
            lines.append("")
            lines.append(f"| 属性 | 值 |")
            lines.append(f"|------|-----|")
            lines.append(f"| 采用率 | {first.adoption_rate:.0%}（{first.cluster_size} 个同类模块中仅 {actual_have} 个具备） |")
            lines.append(f"| 已有此能力的模块 | {', '.join(f'`{m}`' for m in sorted(have_set))} |")
            lines.append(f"| 建议传播到 | {', '.join(f'`{m}`' for m in target_modules[:8])}{' ...等' if missing_count > 8 else ''} |")
            lines.append("")

        # ── 聚类概览 ──
        lines.append("## 聚类依据")
        lines.append("")
        lines.append("> 以下展示模块是如何被归为「同类」的。只有同类模块之间才会做模式对比。")
        lines.append("")

        for i, cluster in enumerate(clusters[:10], 1):
            shared_tags = set()
            for s in cluster:
                shared_tags.update(s.tags)
            specialized_shared = sorted(shared_tags & self.SPECIALIZED_TAGS)
            basic_shared = sorted(shared_tags - self.SPECIALIZED_TAGS)
            lines.append(f"### 聚类 {i} — {len(cluster)} 个模块")
            lines.append(f"**关键能力**: {', '.join(specialized_shared) if specialized_shared else '(无专业标签)'}")
            lines.append(f"**基础能力**: {', '.join(basic_shared[:6])}{'...' if len(basic_shared) > 6 else ''}")
            lines.append("")
            lines.append(f"模块: {', '.join(f'`{s.module_name}`' for s in cluster)}")
            lines.append("")

        # ── 免责声明 ──
        lines.append("---")
        lines.append("")
        lines.append("*本报告自动生成，所有建议仅供参考。模式传播不等于强制统一，部分模块的差异可能是有意为之。*")

        return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════════
# 便捷函数
# ═══════════════════════════════════════════════════════════════════

def detect_innovation_gaps(project_path: str, use_llm: bool = True) -> str:
    """便捷函数：检测创新传播缺口"""
    detector = InnovationPropagationDetector()
    return detector.detect(project_path, use_llm=use_llm)