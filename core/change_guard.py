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

# ═══ 健康基线（守护引擎的 git 基层） ═══
# 守护引擎建立在 git 之上：没有 git 就无法 diff、无法对比基线、无法确认健康版本。
# 因此建 git + 锚定健康基线是守护引擎运转的前提，而非可选附带。
# 统一用 coderef-health-* 前缀命名健康基线 tag，便于识别与回滚。
HEALTH_TAG_PREFIX = "coderef-health-"
# 健康基线提交的固定 message
HEALTH_COMMIT_MSG = "coderef: 锚定健康基线"
# 健康基线提交的最小本地身份（仅写入该项目 git 配置，不污染全局）
HEALTH_GIT_NAME = "CodeRef-AI"
HEALTH_GIT_EMAIL = "coderef@local"


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
              git_timeout: Optional[int] = None,
              git_bin: Optional[str] = None) -> Dict[str, Any]:
        """AI 代码退化检测主入口。

        参数:
            project_path: 当前项目路径（新代码，绝对路径）
            diff: git diff 文本（可选）。若提供，用于定位变更范围、精确对比；
                  否则全量对比当前文件与基线目录。
            baseline_dir: 基线目录（改动前的代码快照）。为空时动态兜底：
                  尝试从 git 历史自动提取最近一次改动作为基线对比。
            git_timeout: 动态兜底时 git 命令的最长等待秒数（可选）。
                  默认 30s；大型项目（>10 万行）建议 60s，小型项目可降到 15s。
            git_bin: git 可执行文件路径或安装目录（由外层 AI 探测提供，可选）。
                  缺省时回退到系统 PATH 的 "git"。

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
                auto_diff = self._auto_git_diff(project_path, timeout=git_timeout,
                                                git_bin=git_bin)
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
        # 附带健康基线参照（供外层 AI 回滚）。仅查询不 init，避免副作用。
        health_baseline = self._latest_health_baseline(project_path, timeout=git_timeout,
                                                       git_bin=git_bin)
        return {
            "findings": [f.to_dict() for f in findings],
            "summary": summary,
            "degraded": degraded,
            "source": source,
            "git_ready": bool(health_baseline or self.is_git_repo(
                project_path, timeout=git_timeout, git_bin=git_bin)),
            "health_baseline": health_baseline,
        }

    # ── 守护引擎的 git 基层（联动）────────────────────────────────
    @staticmethod
    def _resolve_git_bin(git_bin: Optional[str]) -> str:
        """把编程 AI 提供的 git 路径解析为可执行命令。

        让外层 AI 探测 git 所在位置后传入，避免依赖系统 PATH（git 常不在 PATH）。
        支持三种形态：
          1. 完整可执行文件路径（如 C:\\...\\git.exe）→ 直接用；
          2. git 安装目录 → 在该目录里查找 git.exe / git；
          3. 空 / None → 回退到系统 PATH 的 "git"。
        """
        if not git_bin:
            return "git"
        p = os.path.normpath(git_bin)
        if os.path.isdir(p):
            for cand in ("git.exe", "git"):
                full = os.path.join(p, cand)
                if os.path.isfile(full):
                    return full
            return "git"
        # 只接受以 git / git.exe 命名的可执行文件，否则回退系统 PATH 的 "git"
        base = os.path.basename(p).lower()
        if base in ("git", "git.exe"):
            return p
        return "git"

    @staticmethod
    def _git(project_path: str, args: List[str],
             timeout: Optional[int] = None,
             git_bin: Optional[str] = None) -> Tuple[int, str, str]:
        """执行 git 命令，返回 (returncode, stdout, stderr)。绝不抛异常。

        守护引擎的 git 基层：所有 git 交互都经此统一执行，便于超时与降级。
        git_bin 由外层 AI 探测提供（可执行文件路径或 git 安装目录），
        避免依赖系统 PATH；输出统一按 UTF-8/replace 解码，杜绝中文乱码或解码异常。
        """
        import subprocess
        timeout = timeout if timeout is not None else DEFAULT_GIT_TIMEOUT
        cmd = [ChangeGuard._resolve_git_bin(git_bin), "-C", project_path] + args
        try:
            r = subprocess.run(
                cmd, capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=timeout,
                stdin=subprocess.DEVNULL,
            )
            return r.returncode, (r.stdout or "").strip(), (r.stderr or "").strip()
        except Exception as e:
            logger.debug(f"git 命令执行失败: {cmd} → {e}")
            return -1, "", str(e)

    def is_git_repo(self, project_path: str,
                    timeout: Optional[int] = None,
                    git_bin: Optional[str] = None) -> bool:
        """项目是否为 git 仓库。"""
        rc, _, _ = self._git(project_path, ["rev-parse", "--is-inside-work-tree"],
                             timeout=timeout, git_bin=git_bin)
        return rc == 0

    def ensure_git_repo(self, project_path: str,
                        git_timeout: Optional[int] = None,
                        git_bin: Optional[str] = None) -> Dict[str, Any]:
        """守护引擎的 git 前置保障：项目无 git 时自动 init 并补齐最小配置。

        守护引擎建立在 git 之上：没有 git 就无法 diff、无法对比基线、无法锚定健康版本。
        因此建 git 是守护引擎运转的前提。首次调用守护前先确保 git 就绪，可让
        守护引擎从"形同虚设"变为"真正可用"。

        返回:
            {
              "ok": bool,            # 是否就绪
              "git_ready": bool,     # 当前是否为 git 仓库
              "newly_initialized": bool,  # 本次是否刚初始化
              "message": str,
              "health_baselines": [tag, ...],  # 已有的健康基线
            }
        """
        if not os.path.isdir(project_path):
            return {"ok": False, "git_ready": False, "newly_initialized": False,
                    "message": f"项目目录不存在: {project_path}",
                    "health_baselines": []}
        newly = False
        if not self.is_git_repo(project_path, timeout=git_timeout, git_bin=git_bin):
            rc, _, err = self._git(project_path, ["init"], timeout=git_timeout,
                                   git_bin=git_bin)
            if rc != 0:
                return {"ok": False, "git_ready": False, "newly_initialized": False,
                        "message": f"git init 失败: {err}",
                        "health_baselines": []}
            newly = True
            # 补齐最小本地身份（仅该项目配置），否则后续 commit/tag 会失败
            self._git(project_path, ["config", "user.name", HEALTH_GIT_NAME],
                      timeout=git_timeout, git_bin=git_bin)
            self._git(project_path, ["config", "user.email", HEALTH_GIT_EMAIL],
                      timeout=git_timeout, git_bin=git_bin)
        baselines = self.list_health_baselines(project_path, git_timeout=git_timeout,
                                               git_bin=git_bin)
        return {
            "ok": True,
            "git_ready": True,
            "newly_initialized": newly,
            "message": ("已初始化 git 仓库，守护引擎基层就绪" if newly
                        else "项目已是 git 仓库，守护引擎基层就绪"),
            "health_baselines": baselines,
        }

    def anchor_health_baseline(self, project_path: str, label: Optional[str] = None,
                               git_timeout: Optional[int] = None,
                               allow_autocommit: bool = False,
                               git_bin: Optional[str] = None) -> Dict[str, Any]:
        """锚定健康基线：把当前确认健康的代码 commit 并打 coderef-health-* tag。

        由上层（审计通过 / 人工确认健康）决定何时调用，CodeRef 只负责记录。
        若工作区有未提交改动且 allow_autocommit=True，先自动提交再打 tag，使 tag
        指向"此刻完整健康状态"；工作区干净时直接打 tag 到 HEAD。
        若工作区有未提交改动且 allow_autocommit=False，则拒绝锚定并提示用户先提交。

        返回:
            {
              "ok": bool,
              "tag": str,           # 生成的健康基线 tag
              "committed": int,     # 锚定时自动提交的文件数（0=未自动提交）
              "message": str,
              "baselines": [tag, ...],  # 锚定后的全部健康基线
            }
        """
        prep = self.ensure_git_repo(project_path, git_timeout=git_timeout, git_bin=git_bin)
        if not prep["ok"]:
            return {"ok": False, "tag": "", "committed": 0,
                    "message": prep["message"], "baselines": []}
        # tag 名：首个基线带标签，后续自动追加序号避免覆盖
        existing = self.list_health_baselines(project_path, git_timeout=git_timeout,
                                              git_bin=git_bin)
        tag = self._next_health_tag(label, existing)
        # 若工作区有改动，先提交为健康快照
        rc, status_out, _ = self._git(project_path, ["status", "--porcelain"],
                                      timeout=git_timeout, git_bin=git_bin)
        dirty = bool(rc == 0 and status_out)
        committed = 0
        if dirty and not allow_autocommit:
            return {"ok": False, "tag": "", "committed": 0,
                    "message": "工作区有未提交改动且 allow_autocommit=False，请先手动提交或暂存后再锚定健康基线。",
                    "baselines": existing}
        if dirty and allow_autocommit:
            self._git(project_path, ["add", "-A"], timeout=git_timeout, git_bin=git_bin)
            rc, _, _ = self._git(project_path, ["commit", "-m", HEALTH_COMMIT_MSG],
                                 timeout=git_timeout, git_bin=git_bin)
            if rc == 0:
                committed = len([l for l in status_out.splitlines() if l.strip()])
        rc, _, err = self._git(project_path, ["tag", "-a", tag, "-m", HEALTH_COMMIT_MSG],
                               timeout=git_timeout, git_bin=git_bin)
        if rc != 0:
            return {"ok": False, "tag": "", "committed": committed,
                    "message": f"打健康基线 tag 失败: {err}", "baselines": existing}
        baselines = self.list_health_baselines(project_path, git_timeout=git_timeout,
                                               git_bin=git_bin)
        msg = f"已锚定健康基线 {tag}"
        if committed:
            msg += (f"；工作区有改动，已自动提交 {committed} 个文件以固化健康状态"
                    "（如需自行控制提交，请用 allow_autocommit=False）")
        return {"ok": True, "tag": tag, "committed": committed,
                "message": msg, "baselines": baselines}

    def list_health_baselines(self, project_path: str,
                              git_timeout: Optional[int] = None,
                              git_bin: Optional[str] = None) -> List[str]:
        """列出所有健康基线 tag（按创建时间倒序）。"""
        if not self.is_git_repo(project_path, timeout=git_timeout, git_bin=git_bin):
            return []
        rc, out, _ = self._git(project_path,
                               ["tag", "-l", HEALTH_TAG_PREFIX + "*",
                                "--sort=-creatordate"],
                               timeout=git_timeout, git_bin=git_bin)
        if rc != 0 or not out:
            return []
        return [t for t in out.splitlines() if t.strip()]

    def _latest_health_baseline(self, project_path: str,
                                timeout: Optional[int] = None,
                                git_bin: Optional[str] = None) -> Optional[str]:
        """返回最近一个健康基线 tag；无 git 或无基线时返回 None。（仅查询，无副作用）"""
        bs = self.list_health_baselines(project_path, git_timeout=timeout, git_bin=git_bin)
        return bs[0] if bs else None

    @staticmethod
    def _next_health_tag(label: Optional[str], existing: List[str]) -> str:
        """生成下一个健康基线 tag 名。优先用 label；无 label 时按日期+序号。"""
        if label:
            return HEALTH_TAG_PREFIX + label
        from datetime import date
        base = HEALTH_TAG_PREFIX + date.today().isoformat()
        n = sum(1 for t in existing if t.startswith(base))
        return base if n == 0 else f"{base}-{n + 1}"

    # ── 动态兜底：git 自动提取基线 ────────────────────────────────
    def _auto_git_diff(self, project_path: str,
                       timeout: Optional[int] = None,
                       git_bin: Optional[str] = None) -> str:
        """从 git 历史自动提取最近一次改动的 diff 作为基线。

        依次尝试：
          1. 当前工作区未提交改动（git diff HEAD）——最适合"AI 刚改完还没提交"的场景；
          2. 最近一次提交的改动（git diff HEAD~1 HEAD）——工作区干净时的兜底。

        git 不可用 / 不是 git 仓库 / 无任何改动历史时返回空字符串。
        统一经 _git 执行，透传 git_bin 与 UTF-8 解码，绝不抛异常——退化检测必须优雅降级。
        """
        timeout = timeout if timeout is not None else DEFAULT_GIT_TIMEOUT
        candidates = [
            ["diff", "HEAD"],
            ["diff", "HEAD~1", "HEAD"],
        ]
        for args in candidates:
            rc, out, _ = self._git(project_path, args, timeout=timeout, git_bin=git_bin)
            if rc == 0 and out:
                return out
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