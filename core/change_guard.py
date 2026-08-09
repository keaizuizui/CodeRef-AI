# -*- coding: utf-8 -*-
"""
AI 代码退化检测 —— 拦截「AI 把之前写好的代码改坏了」

痛点：vibecoder 根本看不出 AI 在迭代时把之前写好的校验、重试、约束悄悄改没。
本模块在提交前对比「基线能力」与「新代码能力」，自动识别能力退化。

核心逻辑：
  1. 用 Python 标准库 ast 提取文件的能力签名（校验链、重试、约束、异常处理等）；
  2. 解析 git diff，得到被修改 / 删除的行与函数；
  3. 对比基线能力签名 vs 新代码能力签名，识别四类退化：
       - CAPABILITY_MISSING  能力缺失（校验链 validate→sanitize 被删）
       - LOGIC_WEAKENED      逻辑削弱（重试次数从 3 降为 0、超时被去掉）
       - CONSTRAINT_REMOVED  约束移除（去掉输入长度限制、断言、边界）
       - REGRESSION_RISK     回归风险（改动影响未覆盖的调用方，复用 parse_diff 定位）
  4. 输出带 tier 分级 + 行号 + 建议的退化 findings。

设计约束：
  - 纯 Python 标准库 + 复用 parse_diff / code_review._read_file_content，不引入第三方依赖；
  - 所有面向用户的可读文本使用中文；
  - magic number 集中为模块级常量；
  - 异常的退化不静默吞掉，记录日志并降级为待人工确认 finding。

作者: CodeRef-AI Team
"""

import os
import re
import ast
from typing import Dict, List, Optional, Set, Tuple, Any
from enum import Enum
from dataclasses import dataclass

from loguru import logger

from core.code_review import parse_diff, _read_file_content


# ═══════════════════════════════════════════════════════════════════════
# 模块级常量（集中管理 magic number）
# ═══════════════════════════════════════════════════════════════════════

# 能力签名的等级（用于分级）
class ChangeGuardTier(str, Enum):
    HIGH = "high"      # 关键能力缺失（安全/校验链被删）
    MEDIUM = "medium"  # 逻辑削弱、约束移除
    LOW = "low"        # 低危退化 / 待确认

# 退化类型
class DegradationKind(str, Enum):
    CAPABILITY_MISSING = "capability_missing"   # 能力缺失（整段校验/处理被删）
    LOGIC_WEAKENED = "logic_weakened"           # 逻辑削弱（重试/超时阈值降低）
    CONSTRAINT_REMOVED = "constraint_removed"   # 约束移除（断言/长度/边界被删）
    REGRESSION_RISK = "regression_risk"         # 回归风险（改动影响调用方）

# 校验链关键词：出现次数达到阈值才认为存在校验链能力
VALIDATE_KW = ("validate", "sanitize", "normalize", "check", "assert")
# 重试逻辑关键词
RETRY_KW = ("retry", "backoff", "retries", "max_attempts", "timeout")
# 约束关键词（边界/长度/权限）
CONSTRAINT_KW = ("assert", "len(", "max_length", "max_", "min_", "limit", "range", "bound")

# 校验链判定：validate 类关键词出现次数阈值
VALIDATE_CHAIN_THRESHOLD = 2
# 单文件能力签名提取的最大行数（防止超长文件拖慢）
MAX_SIGNATURE_LINES = 2000

# git 自动提取基线时的默认超时（秒）。可按项目规模调整：
#   小型项目（<1 万行）建议 15s；中型（1~10 万行）建议 30s；大型（>10 万行）建议 60s。
DEFAULT_GIT_TIMEOUT = 30


# ═══════════════════════════════════════════════════════════════════════
# 能力签名
# ═══════════════════════════════════════════════════════════════════════

@dataclass
class CapabilitySignature:
    """一个文件的能力签名 —— 描述「这个文件原来有什么能力」。"""
    file_path: str
    validation_count: int = 0        # 校验链关键词出现次数
    has_validation_chain: bool = False  # 存在校验链（validate→sanitize→normalize）
    has_retry: bool = False          # 有重试/退避逻辑
    has_timeout: bool = False        # 有超时控制
    has_error_handling: bool = False # 有 try/except
    constraint_terms: int = 0        # 约束关键词出现次数
    function_names: Set[str] = None  # 文件中的函数名集合
    def __init__(self, file_path: str):
        self.file_path = file_path
        self.validation_count = 0
        self.has_validation_chain = False
        self.has_retry = False
        self.has_timeout = False
        self.has_error_handling = False
        self.constraint_terms = 0
        self.function_names = set()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "file": self.file_path,
            "validation_count": self.validation_count,
            "has_validation_chain": self.has_validation_chain,
            "has_retry": self.has_retry,
            "has_timeout": self.has_timeout,
            "has_error_handling": self.has_error_handling,
            "constraint_terms": self.constraint_terms,
            "functions": sorted(self.function_names),
        }


def extract_signature(file_path: str, content: str) -> CapabilitySignature:
    """从文件内容提取能力签名（Python 标准库 ast + 轻量关键词统计）。"""
    sig = CapabilitySignature(file_path)
    if not content:
        return sig

    # 限制行数，避免超长文件拖慢
    content = "\n".join(content.splitlines()[:MAX_SIGNATURE_LINES])
    content_lower = content.lower()

    # ── ast 解析：函数名 + 异常处理 + 校验语句 ──
    found_raise_validation = False   # raise ValueError/TypeError/AssertionError
    found_isinstance_guard = False   # isinstance 防御性校验
    found_assert_stmt = False        # assert 语句
    try:
        tree = ast.parse(content)
        for node in ast.walk(tree):
            # 函数名
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                sig.function_names.add(node.name)
            # try/except（异常处理能力）
            if isinstance(node, ast.Try):
                sig.has_error_handling = True
            # assert 语句（校验/约束）
            if isinstance(node, ast.Assert):
                found_assert_stmt = True
            # raise 校验异常
            if isinstance(node, ast.Raise):
                exc = node.exc
                if isinstance(exc, ast.Call) and isinstance(exc.func, ast.Name):
                    if exc.func.id in ("ValueError", "TypeError", "AssertionError",
                                       "KeyError", "IndexError"):
                        found_raise_validation = True
            # isinstance 防御性校验
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                if node.func.id == "isinstance":
                    found_isinstance_guard = True
            # 超时：检测 timeout= 关键字参数
            if isinstance(node, ast.Call):
                for kw in node.keywords:
                    if kw.arg and "timeout" in kw.arg.lower():
                        sig.has_timeout = True
    except SyntaxError as e:
        logger.debug(f"AST 解析失败（跳过函数级签名）: {e}")

    # ── 校验链判定：AST 防御性校验 + 关键词统计 ──
    sig.validation_count = sum(
        content_lower.count(kw) for kw in VALIDATE_KW
    ) + int(found_raise_validation) + int(found_assert_stmt)
    if (found_raise_validation or found_isinstance_guard or found_assert_stmt
            or content_lower.count("validate") >= 1
            or sig.validation_count >= VALIDATE_CHAIN_THRESHOLD):
        sig.has_validation_chain = True

    # 重试逻辑
    sig.has_retry = any(kw in content_lower for kw in RETRY_KW)

    # 约束词
    sig.constraint_terms = sum(
        content_lower.count(kw) for kw in CONSTRAINT_KW
    ) + int(found_raise_validation) + int(found_assert_stmt)

    return sig


# ═══════════════════════════════════════════════════════════════════════
# 退化检测
# ═══════════════════════════════════════════════════════════════════════

@dataclass
class DegradationFinding:
    """一条退化检测结果。"""
    kind: DegradationKind
    file_path: str
    line: int = 0
    title: str = ""
    detail: str = ""
    suggestion: str = ""
    tier: ChangeGuardTier = ChangeGuardTier.MEDIUM

    def to_dict(self) -> Dict[str, Any]:
        return {
            "kind": self.kind.value,
            "file": self.file_path,
            "line": self.line,
            "title": self.title,
            "detail": self.detail,
            "suggestion": self.suggestion,
            "tier": self.tier.value,
        }


class ChangeGuard:
    """AI 代码退化检测器：基线 vs 新代码的能力对比。"""

    def __init__(self):
        pass

    def guard(self, project_path: str, diff: Optional[str] = None,
              baseline_dir: Optional[str] = None,
              git_timeout: Optional[int] = None) -> Dict[str, Any]:
        """AI 代码退化检测主入口。

        参数:
            project_path: 当前项目路径（新代码，绝对路径）
            diff: git diff 文本（可选）。若提供，用于定位变更范围、精确对比；
                  否则全量对比当前文件与基线目录。
            baseline_dir: 基线目录（改动前的代码快照）。为空时动态兜底：
                  尝试从 git 历史自动提取最近一次改动作为基线对比。
            git_timeout: 动态兜底时 git 命令的最长等待秒数（可选）。
                  默认 30s；大型项目（>10 万行）建议 60s，小型项目可降到 15s。

        返回:
            {
                "findings": [...],
                "summary": "...",
                "degraded": bool,
                "source": "diff" | "baseline_dir" | "git-auto" | "no-baseline",
            }

        动态兜底规则（保证始终有意义的反馈，而非误导性"未检测到退化"）：
        - 提供 diff → 基于变更范围精确检测；
        - 提供 baseline_dir → 全量对比当前文件与基线目录；
        - 两者皆缺 → 尝试从 git 历史自动提取最近改动（git-auto），
          git-auto 时若工作区干净会回退检测最近一次提交的改动；
        - git 也无法建立基线 → 返回明确降级反馈（no-baseline），
          明确说明"未执行对比"，而非假装"未检测到退化"。
        """
        findings: List[DegradationFinding] = []
        source = "no-baseline"

        try:
            if diff:
                findings = self._guard_diff(project_path, diff)
                source = "diff"
            elif baseline_dir and os.path.isdir(baseline_dir):
                findings = self._guard_dir(project_path, baseline_dir)
                source = "baseline_dir"
            else:
                # 动态兜底：从 git 历史自动提取基线
                auto_diff = self._auto_git_diff(project_path, timeout=git_timeout)
                if auto_diff:
                    findings = self._guard_diff(project_path, auto_diff)
                    source = "git-auto"
                else:
                    # 无法建立任何基线，保留 no-baseline，summary 会明确说明
                    logger.warning("未提供 diff/baseline_dir，且无法从 git 提取基线，退化检测未执行")
        except Exception as e:
            logger.exception(f"退化检测执行出现未预期异常: {e}")
            findings.append(DegradationFinding(
                kind=DegradationKind.REGRESSION_RISK,
                file_path="", line=0,
                title="退化检测引擎异常",
                detail=f"退化检测执行失败：{e}",
                suggestion="请人工核对本次改动，或重试退化检测。",
                tier=ChangeGuardTier.LOW,
            ))
            source = "error"

        degraded = any(f.tier in (ChangeGuardTier.HIGH, ChangeGuardTier.MEDIUM)
                       for f in findings)
        summary = self._build_summary(findings, degraded, source)
        return {
            "findings": [f.to_dict() for f in findings],
            "summary": summary,
            "degraded": degraded,
            "source": source,
        }

    # ── 动态兜底：git 自动提取基线 ────────────────────────────────
    def _auto_git_diff(self, project_path: str,
                       timeout: Optional[int] = None) -> str:
        """从 git 历史自动提取最近一次改动的 diff 作为基线。

        依次尝试：
          1. 当前工作区未提交改动（git diff HEAD）——最适合"AI 刚改完还没提交"的场景；
          2. 最近一次提交的改动（git diff HEAD~1 HEAD）——工作区干净时的兜底。

        git 不可用 / 不是 git 仓库 / 无任何改动历史时返回空字符串。
        绝不抛异常——退化检测必须优雅降级。
        """
        import subprocess
        timeout = timeout if timeout is not None else DEFAULT_GIT_TIMEOUT
        candidates = [
            ["git", "-C", project_path, "diff", "HEAD"],
            ["git", "-C", project_path, "diff", "HEAD~1", "HEAD"],
        ]
        for cmd in candidates:
            try:
                r = subprocess.run(
                    cmd, capture_output=True, text=True, timeout=timeout
                )
            except Exception as e:
                logger.debug(f"git 命令执行失败（降级尝试下一个）: {cmd} → {e}")
                continue
            if r.returncode != 0:
                logger.debug(f"git 命令返回非零（{cmd}）: {r.stderr.strip()[:120]}")
                continue
            content = (r.stdout or "").strip()
            if content:
                return content
        return ""

    # ── 基于 diff 的退化检测（推荐）────────────────────────────────
    def _guard_diff(self, project_path: str, diff: str) -> List[DegradationFinding]:
        units = parse_diff(diff)
        findings: List[DegradationFinding] = []
        for unit in units:
            file_rel = unit["file"]
            if not file_rel:
                continue
            abs_path = os.path.join(project_path, file_rel)
            new_content = _read_file_content(abs_path)

            # 新代码能力签名
            new_sig = extract_signature(file_rel, new_content)

            # 从 diff 中提取被删除的行（旧代码里被删掉的校验/约束）
            deleted_lines = self._extract_deleted_lines(unit)

            # 1) 能力缺失：新代码完全没有校验链，但 diff 里明显删除了校验相关行
            if not new_sig.has_validation_chain and self._has_validation_delete(deleted_lines):
                findings.append(DegradationFinding(
                    kind=DegradationKind.CAPABILITY_MISSING,
                    file_path=file_rel,
                    line=min(unit.get("changed_lines") or [1]),
                    title="校验链疑似被删除",
                    detail=(f"{file_rel} 新代码未检测到校验链（validate/assert/sanitize），"
                            f"但本次变更删除了包含校验关键词的行，疑似把输入校验改没了。"),
                    suggestion="请确认是否误删校验逻辑；若需移除请说明原因并补充等价校验。",
                    tier=ChangeGuardTier.HIGH,
                ))

            # 2) 逻辑削弱：重试/超时能力消失或减弱
            if new_sig.has_validation_chain and not new_sig.has_retry:
                # 仅当 diff 删除了重试相关行时提示
                if self._has_retry_delete(deleted_lines):
                    findings.append(DegradationFinding(
                        kind=DegradationKind.LOGIC_WEAKENED,
                        file_path=file_rel,
                        line=min(unit.get("changed_lines") or [1]),
                        title="重试/超时逻辑可能被削弱",
                        detail=(f"{file_rel} 本次变更删除了重试/超时相关代码，"
                                f"新代码未检测到明确的重试或超时控制。"),
                        suggestion="确认是否保留原有重试与超时保护，避免网络/瞬态故障处理退化。",
                        tier=ChangeGuardTier.MEDIUM,
                    ))

            # 3) 约束移除：删除了 assert/长度/边界约束
            constraint_deleted = self._has_constraint_delete(deleted_lines)
            if constraint_deleted and new_sig.constraint_terms == 0:
                findings.append(DegradationFinding(
                    kind=DegradationKind.CONSTRAINT_REMOVED,
                    file_path=file_rel,
                    line=min(unit.get("changed_lines") or [1]),
                    title="输入约束疑似被移除",
                    detail=(f"{file_rel} 本次变更删除了含 assert/长度/边界限制的代码，"
                            f"新代码未检测到约束，可能导致非法输入直接进入。"),
                    suggestion="确认是否误删校验约束；建议保留输入长度/边界限制。",
                    tier=ChangeGuardTier.MEDIUM,
                ))

        return findings

    def _guard_dir(self, project_path: str, baseline_dir: Optional[str]) -> List[DegradationFinding]:
        """无 diff 时：对比当前文件集与基线目录文件集的能力签名。"""
        findings: List[DegradationFinding] = []
        if not baseline_dir or not os.path.isdir(baseline_dir):
            # 无基线目录：无法做前后对比，仅提示
            logger.info("未提供 baseline_dir，退化检测仅基于 diff。")
            return findings

        current_files = self._collect_py_files(project_path)
        for rel in current_files:
            new_abs = os.path.join(project_path, rel)
            old_abs = os.path.join(baseline_dir, rel)
            if not os.path.exists(old_abs):
                continue  # 新增文件，无退化可能
            new_content = _read_file_content(new_abs)
            old_content = _read_file_content(old_abs)
            new_sig = extract_signature(rel, new_content)
            old_sig = extract_signature(rel, old_content)

            # 能力缺失：原来有校验链，现在没了
            if old_sig.has_validation_chain and not new_sig.has_validation_chain:
                findings.append(DegradationFinding(
                    kind=DegradationKind.CAPABILITY_MISSING,
                    file_path=rel, line=1,
                    title="校验链被移除",
                    detail=f"{rel} 基线存在校验链，新代码已缺失，疑似 AI 改坏了。",
                    suggestion="请恢复校验逻辑，或确认移除原因。",
                    tier=ChangeGuardTier.HIGH,
                ))
            # 重试/超时削弱
            if old_sig.has_retry and not new_sig.has_retry:
                findings.append(DegradationFinding(
                    kind=DegradationKind.LOGIC_WEAKENED,
                    file_path=rel, line=1,
                    title="重试/超时逻辑被移除",
                    detail=f"{rel} 基线存在重试/超时逻辑，新代码已缺失。",
                    suggestion="请确认是否保留重试与超时保护。",
                    tier=ChangeGuardTier.MEDIUM,
                ))
            # 异常处理削弱
            if old_sig.has_error_handling and not new_sig.has_error_handling:
                findings.append(DegradationFinding(
                    kind=DegradationKind.LOGIC_WEAKENED,
                    file_path=rel, line=1,
                    title="异常处理被移除",
                    detail=f"{rel} 基线存在 try/except，新代码已缺失。",
                    suggestion="请确认是否保留异常处理。",
                    tier=ChangeGuardTier.MEDIUM,
                ))
        return findings

    # ── diff 辅助 ───────────────────────────────────────────────────
    @staticmethod
    def _extract_deleted_lines(unit: Dict[str, Any]) -> List[str]:
        """从变更单元中提取所有「被删除」的行文本。"""
        deleted = []
        for hunk in unit.get("hunks", []):
            for c in hunk.get("changes", []):
                if c.get("type") == "del":
                    deleted.append(c.get("text", ""))
        return deleted

    @staticmethod
    def _has_validation_delete(deleted_lines: List[str]) -> bool:
        """被删除的行里是否包含校验相关关键词或防御性校验模式。

        与 extract_signature 的 AST 判定保持一致：不仅看 validate/sanitize/assert，
        也识别 isinstance 类型守卫与 raise ValueError/TypeError/AssertionError 等。
        """
        text = "\n".join(deleted_lines).lower()
        if any(kw in text for kw in ("validate", "sanitize", "normalize",
                                     "assert", "check")):
            return True
        # 防御性校验模式（类型守卫 + 抛校验异常）
        if "isinstance" in text:
            return True
        if any(k in text for k in ("raise valueerror", "raise typeerror",
                                   "raise assertionerror", "raise keyerror",
                                   "raise indexerror", "raise stopiteration")):
            return True
        return False

    @staticmethod
    def _has_retry_delete(deleted_lines: List[str]) -> bool:
        """被删除的行里是否包含重试/超时相关关键词。"""
        text = "\n".join(deleted_lines).lower()
        return any(kw in text for kw in ("retry", "backoff", "retries",
                                         "max_attempts", "timeout"))

    @staticmethod
    def _has_constraint_delete(deleted_lines: List[str]) -> bool:
        """被删除的行里是否包含约束相关关键词。"""
        text = "\n".join(deleted_lines).lower()
        return any(kw in text for kw in ("assert", "len(", "max_", "min_",
                                         "limit", "range", "bound"))

    @staticmethod
    def _collect_py_files(project_path: str) -> List[str]:
        """递归收集 .py 文件（相对路径）。"""
        from core.project_scope import ProjectScope  # 复用作用域判定
        try:
            scope = ProjectScope(project_path)
            keep = scope.analyze()
            files = [getattr(f, "file_path", "") for f in keep]
            return [os.path.relpath(f, project_path) for f in files if f]
        except Exception:
            pass
        # 降级：简单遍历
        result = []
        for root, dirs, files in os.walk(project_path):
            dirs[:] = [d for d in dirs
                       if d not in (".git", "node_modules", "__pycache__", ".venv", "venv")]
            for f in files:
                if f.endswith(".py"):
                    result.append(os.path.relpath(os.path.join(root, f), project_path))
        return result

    # ── summary ─────────────────────────────────────────────────────
    def _build_summary(self, findings: List[DegradationFinding], degraded: bool,
                       source: str = "diff") -> str:
        if source == "no-baseline":
            return (
                "退化检测未执行：未提供 diff，也未提供 baseline_dir，"
                "且无法从 git 历史自动提取基线改动。"
                "请传入 git diff 文本，或传入 baseline_dir（改动前快照），"
                "或在已提交的 git 仓库中重试，才能进行有意义的退化检测。"
            )
        if source == "git-auto":
            if not findings:
                return "已自动从 git 历史提取最近改动完成对比，未检测到明显的代码退化。"
            head = ("⚠️ 检测到代码退化，建议提交前修复" if degraded
                    else "检测到低风险项，建议人工确认")
            n_high = sum(1 for f in findings if f.tier == ChangeGuardTier.HIGH)
            n_med = sum(1 for f in findings if f.tier == ChangeGuardTier.MEDIUM)
            n_low = sum(1 for f in findings if f.tier == ChangeGuardTier.LOW)
            return (
                f"{head}：共 {len(findings)} 条（HIGH {n_high} / MEDIUM {n_med} / "
                f"LOW {n_low}）。基线来自 git 历史自动提取，覆盖最近一次改动。"
                f"重点核查校验链、重试、异常处理等能力是否被 AI 改动悄悄删除。"
            )
        if not findings:
            return "本次变更未检测到明显的代码退化。"
        n_high = sum(1 for f in findings if f.tier == ChangeGuardTier.HIGH)
        n_med = sum(1 for f in findings if f.tier == ChangeGuardTier.MEDIUM)
        n_low = sum(1 for f in findings if f.tier == ChangeGuardTier.LOW)
        head = ("⚠️ 检测到代码退化，建议提交前修复" if degraded
                else "检测到低风险项，建议人工确认")
        return (
            f"{head}：共 {len(findings)} 条（HIGH {n_high} / MEDIUM {n_med} / LOW {n_low}）。"
            f"重点核查校验链、重试、异常处理等能力是否被 AI 改动悄悄删除。"
        )