# -*- coding: utf-8 -*-
"""
代码审查模块（多工具交叉验证 · 防 LLM 幻觉）

项目核心哲学：通过「多工具交叉验证」防止大模型幻觉。
本模块是审查编排层：
  1. 解析 git diff 或扫描目录，得到变更单元 / 文件批次；
  2. 用 AST（Python 标准库）定位变更涉及的函数/类，抽取相关代码片段增强上下文；
  3. 为每个变更单元 / 文件批次构造针对性 prompt，调用 LLM 生成审查评论；
  4. 每条评论携带 evidence 字段，默认 "pending-human"，为后续与 11 个静态工具
     对照（交叉验证）预留占位通道——静态工具可确认的置为 "static-confirmed"。

设计约束：
  - 纯 Python 标准库 + 复用 core/llm_integration.py，不引入任何第三方新依赖；
  - 可独立导入，不破坏现有代码（from core.code_review import CodeReviewer, parse_diff）；
  - 所有面向使用者的可读文本一律使用中文；
  - 使用 loguru logger，异常不静默吞掉（记录日志并降级为待人工确认评论）；
  - magic number 集中定义为模块级常量。

作者: CodeRef-AI Team
"""

import os
import re
import ast
from typing import Dict, List, Optional, Any, Set, Tuple

from loguru import logger

from core.llm_integration import LLMIntegration
from core.code_analyzer import CodeAnalyzer


# ═══════════════════════════════════════════════════════════════════════
# 模块级常量（集中管理 magic number）
# ═══════════════════════════════════════════════════════════════════════

# 全量扫描模式：每个文件批次的文件个数上限
DEFAULT_BATCH_SIZE = 5

# AST 上下文增强：单个函数/类抽取的最大代码行数
MAX_CONTEXT_FUNC_LINES = 60

# 单次 prompt 中粘贴的代码/变更内容最大字符数
MAX_PROMPT_CODE_CHARS = 6000
MAX_PROMPT_DIFF_CHARS = 8000

# 单次 prompt 中允许列出的批次文件个数上限（防止超长）
MAX_BATCH_FILES_IN_PROMPT = DEFAULT_BATCH_SIZE

# 审查评论降级标志
EVIDENCE_PENDING = "pending-human"     # 待人工确认（默认，供后续与 11 工具对照）
EVIDENCE_STATIC = "static-confirmed"   # 静态工具已确认（交叉验证占位）

# 全部审查维度（默认维度集合）
ALL_DIMENSIONS: Tuple[str, ...] = (
    "bug", "security", "cross_module", "maintainability",
    "consistency", "testing", "regression",
)

# 合法严重级别
VALID_SEVERITIES: Tuple[str, ...] = ("high", "medium", "low")

# 首次降级评论的默认行号（表示"整文件/未知行"）
DEFAULT_LINE = 1


# ═══════════════════════════════════════════════════════════════════════
# 工具函数
# ═══════════════════════════════════════════════════════════════════════

def _read_file_content(file_path: str) -> str:
    """以多种编码尝试读取文件内容，失败返回空字符串。"""
    for encoding in ("utf-8", "utf-8-sig", "gbk", "latin-1"):
        try:
            with open(file_path, "r", encoding=encoding) as f:
                return f.read()
        except (UnicodeDecodeError, OSError) as e:
            logger.debug(f"以 {encoding} 读取 {file_path} 失败: {e}")
            continue
    logger.warning(f"无法以任何已知编码读取文件: {file_path}")
    return ""


def _resolve_file_path(project_path: str, rel_or_abs: str) -> str:
    """把 diff / 变更列表中的相对路径解析为绝对路径。"""
    if not rel_or_abs:
        return ""
    if os.path.isabs(rel_or_abs):
        return rel_or_abs
    return os.path.join(project_path, rel_or_abs)


def _extract_function_context(code: str, changed_lines: Set[int]) -> Tuple[Set[str], str]:
    """用 Python 标准库 ast 定位变更行涉及的函数/类，抽取相关代码片段。

    返回:
        (相关的函数/类名集合, 拼接后的上下文代码字符串)
    不依赖 ast_parser 的具体类，仅使用标准库 ast。
    """
    if not changed_lines:
        return set(), ""
    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        logger.debug(f"AST 解析失败（跳过上下文增强）: {e}")
        return set(), ""

    snippets: List[str] = []
    names: Set[str] = set()
    source_lines = code.splitlines()

    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        start = node.lineno
        end = getattr(node, "end_lineno", None) or start
        if not any(start <= line <= end for line in changed_lines):
            continue
        names.add(node.name)

        # 抽取函数/类体片段（含上方一行上下文，便于看到签名前的装饰器/注释）
        from_line = max(1, start - 1)
        to_line = min(len(source_lines), end)
        body = source_lines[from_line - 1: to_line]
        # 限制长度
        if len(body) > MAX_CONTEXT_FUNC_LINES:
            body = body[:MAX_CONTEXT_FUNC_LINES]
        header = f"# [{node.__class__.__name__}] {node.name} (行 {start}-{end})"
        numbered = "\n".join(
            f"{from_line + i:>4}| {l}" for i, l in enumerate(body)
        )
        snippets.append(f"{header}\n{numbered}")

    return names, "\n\n".join(snippets)


def _dimensions_str(dimensions: Optional[List[str]]) -> str:
    """把维度列表格式化为中文可读字符串。"""
    dims = list(dimensions) if dimensions else list(ALL_DIMENSIONS)
    dim_name = {
        "bug": "逻辑缺陷（bug）",
        "security": "安全漏洞（security）",
        "cross_module": "跨模块耦合/一致性（cross_module）",
        "maintainability": "可维护性（maintainability）",
        "consistency": "命名/风格一致性（consistency）",
        "testing": "测试覆盖（testing）",
        "regression": "回归风险（regression）",
    }
    return "、".join(dim_name.get(d, d) for d in dims)


def _normalize_comment(item: Any, default_file: str, default_line: int,
                       dimensions: Optional[List[str]]) -> Optional[Dict[str, Any]]:
    """把 LLM 返回的单个评论归一化为标准结构；不符合维度则过滤（返回 None）。"""
    if not isinstance(item, dict):
        return None

    severity = item.get("severity", "medium")
    if severity not in VALID_SEVERITIES:
        severity = "medium"

    dimension = item.get("dimension", "maintainability")
    if dimension not in ALL_DIMENSIONS:
        dimension = "maintainability"

    # 按维度过滤
    if dimensions and dimension not in dimensions:
        return None

    line = item.get("line", default_line)
    try:
        line = int(line)
    except (TypeError, ValueError):
        line = default_line

    return {
        "file": item.get("file") or default_file,
        "line": line,
        "severity": severity,
        "dimension": dimension,
        "title": str(item.get("title") or "（无标题）"),
        "detail": str(item.get("detail") or item.get("description") or ""),
        "suggestion": str(item.get("suggestion") or ""),
        # 交叉验证占位：默认待人工确认，供后续与 11 工具对照
        "evidence": EVIDENCE_PENDING,
    }


def _degraded_comment(file: str, line: int, reason: str) -> Dict[str, Any]:
    """构造降级评论（LLM 调用失败 / 解析失败 / LLM 不可用时使用）。"""
    return {
        "file": file,
        "line": line,
        "severity": "low",
        "dimension": "maintainability",
        "title": "LLM 审查失败待人工确认",
        "detail": reason,
        "suggestion": "请人工核对此处变更，或重试 LLM 审查。",
        "evidence": EVIDENCE_PENDING,
    }


def _llm_available(llm: LLMIntegration) -> bool:
    """判断 LLM 是否真正可用（客户端已初始化且存在有效 API Key）。"""
    if llm is None:
        return False
    client = getattr(llm, "client", None)
    config = getattr(llm, "config", None)
    api_key = getattr(config, "api_key", "") if config is not None else ""
    return bool(client) and bool(api_key)


# ═══════════════════════════════════════════════════════════════════════
# parse_diff —— git diff 解析
# ═══════════════════════════════════════════════════════════════════════

def parse_diff(diff_text: str) -> List[Dict[str, Any]]:
    """解析 git diff 文本，返回变更单元列表。

    每个变更单元结构:
        {
            "file": str,                      # 新文件路径
            "hunks": [                        # 变更块列表
                {
                    "new_start": int,         # 新文件起始行号
                    "new_end": int,           # 新文件结束行号
                    "changes": [              # 变更行列表
                        {"new_line": Optional[int], "text": str,
                         "type": "add"/"del"/"context"}
                    ],
                }
            ],
            "changed_lines": set({int, ...})  # 新文件变更行号集合（仅新增行）
        }

    解析失败的行不会使整个函数崩溃，而是记日志后跳过。
    """
    if not diff_text:
        return []

    units: List[Dict[str, Any]] = []
    current: Optional[Dict[str, Any]] = None
    current_hunk: Optional[Dict[str, Any]] = None
    new_line = 0

    for raw_line in diff_text.splitlines():
        line = raw_line.rstrip("\n")

        # 新文件单元开始
        if line.startswith("diff --git"):
            if current is not None:
                units.append(current)
            current = {"file": "", "hunks": [], "changed_lines": set()}
            current_hunk = None
            m = re.match(r"diff --git\s+a/(.+?)\s+b/(.+)$", line)
            if m:
                current["file"] = m.group(2).strip()
            continue

        if current is None:
            continue

        # 新文件路径（+++ b/xxx），/dev/null 表示删除文件
        # 仅在尚未进入 hunk body 时检查，避免 hunk 中以 "++" 开头的添加行被误判为路径头
        if current_hunk is None and line.startswith("+++ "):
            f = line[4:].strip()
            # 去掉 "b/" 前缀（git 默认新版路径前缀）
            if f.startswith("b/"):
                f = f[2:]
            if f and f != "/dev/null":
                current["file"] = f
            continue

        # 变更块头
        if line.startswith("@@"):
            m = re.match(r"@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@", line)
            try:
                new_start = int(m.group(1))
                new_count = int(m.group(2)) if m.group(2) else 1
            except (AttributeError, TypeError, ValueError):
                new_start, new_count = 1, 0
            new_line = new_start
            new_end = new_start + new_count - 1 if new_count > 0 else new_start - 1
            current_hunk = {
                "new_start": new_start,
                "new_end": new_end,
                "changes": [],
            }
            current["hunks"].append(current_hunk)
            continue

        if current_hunk is None:
            continue

        # 变更内容行
        if line.startswith("+"):
            current_hunk["changes"].append(
                {"new_line": new_line, "text": line[1:], "type": "add"}
            )
            current["changed_lines"].add(new_line)
            new_line += 1
        elif line.startswith("-"):
            current_hunk["changes"].append(
                {"new_line": None, "text": line[1:], "type": "del"}
            )
        elif line.startswith(" "):
            current_hunk["changes"].append(
                {"new_line": new_line, "text": line[1:], "type": "context"}
            )
            new_line += 1
        # 其余行（如 "\ No newline at end of file"、非变更内容）忽略

    if current is not None:
        units.append(current)

    # 过滤无实际变更或路径为空的单元。
    # 注意：纯删除（如 AI 删掉校验链）也是真实变更，须保留。
    # 保留条件：有新增行，或任意 hunk 内有增删变更。
    result = [
        u for u in units
        if u.get("file") and (
            u.get("changed_lines")
            or any(h.get("changes") for h in u.get("hunks", []))
        )
    ]
    logger.info(f"parse_diff 解析完成：共 {len(result)} 个变更单元")
    return result


# ═══════════════════════════════════════════════════════════════════════
# CodeReviewer
# ═══════════════════════════════════════════════════════════════════════

class CodeReviewer:
    """代码审查器（diff 模式 / 全量模式双模式）。"""

    def __init__(self, llm: Optional[LLMIntegration] = None):
        """初始化审查器。llm 为空时自动创建 LLMIntegration()。"""
        self.llm = llm if llm is not None else LLMIntegration()
        logger.debug("CodeReviewer 初始化完成")

    # ── 主入口 ──────────────────────────────────────────────────────
    def review(self, project_path: str, mode: str = "diff", diff: Optional[str] = None,
               changed_files: Optional[List[str]] = None,
               dimensions: Optional[List[str]] = None) -> Dict[str, Any]:
        """代码审查主入口。

        参数:
            project_path: 项目根目录（绝对路径）
            mode: "diff"（基于 git diff / 变更文件）或 "full"（全量扫描）
            diff: git diff 文本（mode="diff" 时可选）
            changed_files: 变更文件列表（mode="diff" 且未提供 diff 时使用）
            dimensions: 需要审查的维度子集（None 表示全部维度）

        返回:
            {"comments": [...], "summary": "...", "mode": mode}
        """
        if mode not in ("diff", "full"):
            logger.warning(f"未知审查模式 {mode!r}，回退为 diff")
            mode = "diff"

        # 规范化维度
        dims = None
        if dimensions:
            dims = list(dimensions)

        comments: List[Dict[str, Any]] = []
        available = _llm_available(self.llm)
        processed = 0

        try:
            if mode == "diff":
                processed = self._review_diff(
                    project_path, diff, changed_files, dims, available, comments
                )
            else:
                processed = self._review_full(
                    project_path, dims, available, comments
                )
        except Exception as e:
            # 顶层兜底：不静默吞掉，记录日志并附加降级评论
            logger.exception(f"代码审查执行出现未预期异常: {e}")
            comments.append(_degraded_comment(
                "", DEFAULT_LINE, f"审查引擎异常：{e}"
            ))

        summary = self._build_summary(mode, processed, len(comments), available, dims)
        return {"comments": comments, "summary": summary, "mode": mode}

    # ── diff 模式 ───────────────────────────────────────────────────
    def _review_diff(self, project_path: str, diff: Optional[str],
                     changed_files: Optional[List[str]], dims: Optional[List[str]],
                     available: bool, comments: List[Dict[str, Any]]) -> int:
        units = []
        if diff:
            units = parse_diff(diff)
            logger.info(f"diff 模式：解析到 {len(units)} 个变更单元")
        elif changed_files:
            for f in changed_files:
                abs_path = _resolve_file_path(project_path, f)
                content = _read_file_content(abs_path)
                # 变更文件模式：整个文件作为一次审查单元
                units.append({
                    "file": f,
                    "abs_path": abs_path,
                    "content": content,
                    "changed_lines": set(range(1, len(content.splitlines()) + 1)) if content else set(),
                })
            logger.info(f"diff 模式：按变更文件构造 {len(units)} 个审查单元")
        else:
            logger.warning("diff 模式未提供 diff 文本或 changed_files，无可审查内容")
            return 0

        for unit in units:
            if not unit.get("file"):
                continue
            if available:
                comments.extend(self._review_diff_unit(project_path, unit, dims))
            else:
                comments.append(_degraded_comment(
                    unit["file"], DEFAULT_LINE,
                    "LLM 不可用（未配置 API Key），已降级为待人工确认占位评论。"
                ))
        return len(units)

    def _review_diff_unit(self, project_path: str, unit: Dict[str, Any],
                          dims: Optional[List[str]]) -> List[Dict[str, Any]]:
        """对单个 diff 变更单元执行 LLM 审查。"""
        file_rel = unit["file"]
        abs_path = _resolve_file_path(project_path, file_rel)
        changed_lines: Set[int] = set(unit.get("changed_lines", []))

        # 读取文件内容（用于上下文增强）
        content = unit.get("content")
        if content is None:
            content = _read_file_content(abs_path)

        # 构造 prompt 中的变更上下文
        hunks = unit.get("hunks", [])
        if hunks:
            diff_text = self._format_hunks(file_rel, hunks)[:MAX_PROMPT_DIFF_CHARS]
        else:
            diff_text = self._format_content_preview(file_rel, content)

        # AST 上下文增强
        _, context = _extract_function_context(content, changed_lines)
        context = context[:MAX_PROMPT_CODE_CHARS]
        if not context:
            context = "(未定位到相关函数/类，或文件无法解析)"

        prompt = self._build_diff_prompt(file_rel, project_path, diff_text, context, dims)
        return self._call_llm(prompt, file_rel, changed_lines, dims)

    # ── full 模式 ───────────────────────────────────────────────────
    def _review_full(self, project_path: str, dims: Optional[List[str]],
                     available: bool, comments: List[Dict[str, Any]]) -> int:
        analyzer = CodeAnalyzer()
        files = analyzer.scan_directory(project_path)
        logger.info(f"full 模式：全量扫描到 {len(files)} 个代码文件")

        # 按文件分块（每块若干文件）
        batches = [
            files[i:i + DEFAULT_BATCH_SIZE]
            for i in range(0, len(files), DEFAULT_BATCH_SIZE)
        ]
        logger.info(f"full 模式：划分为 {len(batches)} 个文件批次")

        for batch in batches:
            if not batch:
                continue
            if available:
                comments.extend(self._review_batch(project_path, batch, dims))
            else:
                for f in batch:
                    comments.append(_degraded_comment(
                        f, DEFAULT_LINE,
                        "LLM 不可用（未配置 API Key），已降级为待人工确认占位评论。"
                    ))
        return len(batches)

    def _review_batch(self, project_path: str, batch: List[str],
                      dims: Optional[List[str]]) -> List[Dict[str, Any]]:
        """对一批文件执行 LLM 审查。"""
        blocks = []
        for f in batch:
            content = _read_file_content(f)
            blocks.append(self._format_content_preview(f, content))
        prompt = self._build_batch_prompt(project_path, blocks, dims)
        return self._call_llm(prompt, batch[0], set(), dims)

    # ── LLM 调用与结果解析 ──────────────────────────────────────────
    def _call_llm(self, prompt: str, default_file: str, changed_lines: Set[int],
                  dims: Optional[List[str]]) -> List[Dict[str, Any]]:
        """调用 LLM 并解析返回的评论数组；失败时降级为待人工确认评论。"""
        default_line = min(changed_lines) if changed_lines else DEFAULT_LINE

        messages = [
            {
                "role": "system",
                "content": (
                    "你是一位资深代码审查专家，擅长多维度代码审查。"
                    "你只返回 JSON 数组，不输出任何其它文字或 Markdown 代码块。"
                ),
            },
            {"role": "user", "content": prompt},
        ]

        try:
            response = self.llm.chat_completion(
                messages, max_tokens=4096, temperature=0.2
            )
        except Exception as e:
            # 不静默吞掉：记录日志并降级
            logger.error(f"LLM 调用抛出异常: {e}")
            return [_degraded_comment(default_file, default_line, f"LLM 调用异常：{e}")]

        # LLM 调用内部错误（如无 API Key）返回的是错误文本，而非 JSON
        if response.startswith("LLM调用错误"):
            logger.warning(f"LLM 调用失败：{response[:200]}")
            return [_degraded_comment(default_file, default_line, response)]

        # 交叉验证占位入口：解析 LLM 返回的评论数组
        data = self.llm._try_parse_json(response)

        if not isinstance(data, list):
            reason = "LLM 返回内容不包含合法 JSON 评论数组"
            logger.warning(f"{reason}; 响应片段: {response[:200]}")
            return [_degraded_comment(default_file, default_line, reason)]

        comments: List[Dict[str, Any]] = []
        for item in data:
            normalized = _normalize_comment(item, default_file, default_line, dims)
            if normalized is not None:
                comments.append(normalized)
        return comments

    # ── prompt 构造 ─────────────────────────────────────────────────
    def _build_diff_prompt(self, file_rel: str, project_path: str, diff_text: str,
                           context: str, dims: Optional[List[str]]) -> str:
        return f"""请审查以下代码变更，找出潜在问题。

项目路径: {project_path}
文件: {file_rel}

## 变更上下文（diff）
```
{diff_text}
```

## 相关代码上下文（变更涉及的函数/类，AST 定位）
```
{context}
```

## 审查维度
{_dimensions_str(dims)}

## 输出要求
请返回 JSON 数组，每个元素为一条审查评论，字段如下：
- "file": 文件路径（字符串）
- "line": 行号（新文件行号，整数）
- "severity": "high" 或 "medium" 或 "low"
- "dimension": "bug" 或 "security" 或 "cross_module" 或 "maintainability" 或 "consistency" 或 "testing" 或 "regression"
- "title": 简短标题
- "detail": 详细说明
- "suggestion": 修改建议

严格只返回 JSON 数组，不要输出任何其它内容。
"""

    def _build_batch_prompt(self, project_path: str, blocks: List[str],
                            dims: Optional[List[str]]) -> str:
        files_section = "\n\n".join(
            f"--- 文件 {i + 1} ---\n{blk}" for i, blk in enumerate(blocks)
        )
        return f"""请审查以下一个代码文件批次（共 {len(blocks)} 个文件），找出潜在问题。

项目路径: {project_path}

{files_section}

## 审查维度
{_dimensions_str(dims)}

## 输出要求
请返回 JSON 数组，每个元素为一条审查评论，字段如下：
- "file": 文件路径（字符串，必须与上方文件名一致）
- "line": 行号（整数）
- "severity": "high" 或 "medium" 或 "low"
- "dimension": "bug" 或 "security" 或 "cross_module" 或 "maintainability" 或 "consistency" 或 "testing" 或 "regression"
- "title": 简短标题
- "detail": 详细说明
- "suggestion": 修改建议

严格只返回 JSON 数组，不要输出任何其它内容。
"""

    # ── 格式化辅助 ──────────────────────────────────────────────────
    @staticmethod
    def _format_hunks(file_rel: str, hunks: List[Dict[str, Any]]) -> str:
        """把变更块格式化为可读 diff 文本。"""
        lines = [f"--- {file_rel}"]
        for h in hunks:
            lines.append(
                f"@@ -{h['new_start']},{h['new_end']} @@ "
                f"(新文件行 {h['new_start']}-{h['new_end']})"
            )
            for c in h.get("changes", []):
                marker = {"add": "+", "del": "-", "context": " "}.get(c["type"], " ")
                lines.append(f"{marker}{c['text']}")
        return "\n".join(lines)

    @staticmethod
    def _format_content_preview(file: str, content: str) -> str:
        """把整个文件内容格式化为带行号的预览（截断到上限）。"""
        preview = content[:MAX_PROMPT_CODE_CHARS]
        lines = preview.splitlines()
        numbered = "\n".join(f"{i + 1:>4}| {l}" for i, l in enumerate(lines))
        return f"{file}\n{numbered}"

    # ── summary 构造 ────────────────────────────────────────────────
    def _build_summary(self, mode: str, processed: int, comment_count: int,
                       available: bool, dims: Optional[List[str]] = None) -> str:
        if not available:
            return (
                "LLM 当前不可用（未配置有效的 API Key），本次审查未调用大模型，"
                f"comments 已降级为待人工确认的占位评论（共 {comment_count} 条）。"
                f"请先在配置面板 / 环境变量中填写 API Key（如 CODEREF_API_KEY）后重试。"
                f"（模式: {mode}，处理单元/批次: {processed}）"
            )
        dims_desc = _dimensions_str(dims)
        return (
            f"代码审查完成：模式 {mode}，处理 {processed} 个单元/批次，"
            f"审查维度 {dims_desc}，共生成 {comment_count} 条评论。"
            f"所有评论的 evidence 暂标记为 {EVIDENCE_PENDING}，"
            f"后续将与 11 个静态工具交叉验证以确认真伪。"
        )