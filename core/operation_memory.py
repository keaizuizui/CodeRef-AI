# -*- coding: utf-8 -*-
"""
OperationMemory v1.0 —— AI 辅助编程的操作记忆层

为 MCP 工具 coderef_operation_memory(action=sync/query/find/status)
提供"东西在哪儿、从哪儿来、到哪儿去、过去的规范是什么"的持久记忆。

设计参考：本模块的 `BRAIN.md` 命名、判存标准（能否从代码重建）与
「当前理解 + 时间线」记录结构借鉴自 mindmuxai/brain.md（Apache-2.0，
https://github.com/mindmuxai/brain.md）；分层记忆与渐进式披露思路参考
TencentDB-Agent-Memory（MIT，https://github.com/Tencent/TencentDB-Agent-Memory）。
CoreRef 的差异点：额外处理旁目录（WSL / 家目录 / 数据目录)下的资源定位。

与 MemoryLayer（代码结构记忆）互补：
- MemoryLayer      记忆"代码是什么"（AST、图谱、语义）
- OperationMemory  记忆"东西在哪儿、怎么用、为什么这么做"

核心能力：
1. sync(project_path, mode, with_llm)
   - 静态审计识别主目录 + 旁目录的资源位置（git / 模型 / API / 测试工具 /
     报告 / 依赖），写入 ledger.json + BRAIN.md。
   - 可选调用 LLM 从文档 / 报告提炼隐性知识（决策 / 约定 / 踩坑），写入
     timeline.md。API Key 缺失时优雅降级为"待人工确认"，不抛异常。
   - 增量模式用 mtime+size 快照判断变更，只重扫变更项。

2. query(project_path, query_type, keyword, limit)
   - 按类别检索操作记忆，供 AI 上下文丢失后快速恢复。

3. find(project_path, name, limit)
   - 给定资源名 / 路径片段，定位实际位置、来源、主 / 旁目录归属。

4. status(project_path)
   - 操作记忆健康状态：已覆盖分类、各分类条目数、旁目录探测、LLM 可用性。

5. recover(project_path, limit)
   - 上下文丢失后『一次调用』恢复关键记忆：关键工具位置 + 已确认的约定 / 踩坑 / 决策
     摘要 + 待人工确认项。供 AI 最小成本拿回『东西在哪儿、过去的规范是什么』，
     避免绕过记忆层去满 PATH 找或抓外部连接器。

工程约定：
- 纯标准库 + 复用底座（不修改任何底座文件）。
- 所有用户可读文本使用中文。
- magic number 集中在 config/settings.py（OMEM_*）。
- LLM / API Key 缺失时优雅降级，不抛异常。
- 统一返回结构化 JSON dict。
- 不做任何 MCP 注册（由上层统一接线）。
"""

import os
import re
import json
import glob
import shutil
import subprocess
import hashlib
import tempfile
import threading
import time
from datetime import datetime
from typing import Dict, List, Optional, Any

# ─── 日志（复用 loguru，若缺失则回退到内置 logging） ───
try:
    from loguru import logger as _logger
except Exception:  # pragma: no cover
    import logging
    _logger = logging.getLogger("operation_memory")
    if not _logger.handlers:
        _logger.addHandler(logging.StreamHandler())
        _logger.setLevel(logging.INFO)
logger = _logger

# ─── 阈值常量（集中管理，不散落 magic number） ───
from config import settings

RESOURCE_TYPES = settings.OMEM_RESOURCE_TYPES
SENSITIVE_HINTS = settings.OMEM_SENSITIVE_DIR_HINTS
MAX_SIDE_DIRS = settings.OMEM_MAX_SIDE_DIRS
MAX_PER_CATEGORY = settings.OMEM_MAX_PER_CATEGORY
MODEL_EXTENSIONS = settings.OMEM_MODEL_EXTENSIONS
LLM_TIMEOUT = settings.OMEM_LLM_TIMEOUT
LLM_MAX_CHARS_SOURCE = settings.OMEM_LLM_MAX_CHARS_SOURCE
EXTRACT_GRAPH_LIMIT = settings.OMEM_EXTRACT_GRAPH_LIMIT
TIMELINE_MAX = settings.OMEM_TIMELINE_MAX
ENV_TOOL_BINS = settings.OMEM_ENV_TOOL_BINS
ENV_TOOL_ROOTS = tuple(settings.OMEM_ENV_TOOL_ROOTS) + tuple(settings.omem_extra_tool_roots())
ENV_TOOL_BIN_SUBDIRS = settings.OMEM_ENV_TOOL_BIN_SUBDIRS
WSL_TOOL_BINS = settings.OMEM_WSL_TOOL_BINS
WSL_CMD_TIMEOUT = settings.OMEM_WSL_CMD_TIMEOUT
WSL_PROBE_RETRIES = settings.OMEM_WSL_PROBE_RETRIES

# 项目根目录（Coderef-Ai-master）
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# 操作记忆存放目录：优先环境变量 / settings 配置的用户数据目录，
# 空则回退项目根 data/operation_memory（兼容旧路径）。
def _resolve_omem_dir() -> str:
    cfg = os.environ.get("OMEM_DATA_DIR", "") or (settings.OMEM_DATA_DIR or "")
    if cfg:
        return os.path.abspath(os.path.expanduser(cfg))
    return os.path.join(_PROJECT_ROOT, "data", "operation_memory")

_OMEM_DIR = _resolve_omem_dir()

# 快照 mtime 容差（秒），与 memory_layer 保持一致
_MTIME_TOLERANCE = 0.1

# 需要跳过的目录（避免扫自身产物 / 第三方依赖）
_IGNORE_DIRS = {
    ".git", "node_modules", "__pycache__", ".venv", "venv", "env",
    "site-packages", ".pytest_cache", ".mypy_cache", ".ruff_cache",
    "dist", "build", ".egg-info", "vendor", "data", "coderef-report",
}

# 测试工具目录特征（静态识别"测试资产"）
_TEST_DIR_HINTS = ("test", "tests", "spec")

# 依赖清单文件名 → 依赖来源描述
_DEPENDENCY_FILES = {
    "requirements.txt": "pip requirements",
    "pyproject.toml": "pyproject",
    "package.json": "npm package",
    "Cargo.toml": "cargo",
    "go.mod": "go module",
    "pom.xml": "maven",
    "Gemfile": "bundler",
    "composer.json": "composer",
}

# 报告 / 文档输出特征扩展名
_DOC_EXTENSIONS = (".md", ".rst", ".txt", ".html")
_REPORT_EXTENSIONS = (".html", ".pdf", ".md")

# 隐性知识类别中文标签（用于 pending 待办条目与 BRAIN 渲染）
_KIND_LABELS = {"decision": "决策", "convention": "约定", "pitfall": "踩坑"}


# ═══════════════════════════════════════════════════════════════════
# 辅助函数
# ═══════════════════════════════════════════════════════════════════

def _project_hash(project_path: str) -> str:
    """项目路径 → 稳定短 hash（用于隔离每项目记忆目录）"""
    return hashlib.md5(os.path.abspath(project_path).encode("utf-8")).hexdigest()[:12]


def _omem_dir(project_path: str) -> str:
    """返回项目对应的操作记忆目录"""
    return os.path.join(_OMEM_DIR, _project_hash(project_path))


def _ensure_dirs(project_path: str):
    """创建项目对应的操作记忆目录。目录创建 / 可写校验失败时抛错，
    由调用方 sync() 捕获并返回结构化 error，不让异常逃逸。

    可写校验用唯一临时文件名（pid+线程id）并在项目锁内执行：
    旧实现对同一 .write_probe 文件 open+remove，并发 sync 会互相占用
    触发 WinError 32（文件被占用），导致并发失败率 68.75%。
    """
    d = _omem_dir(project_path)
    try:
        os.makedirs(d, exist_ok=True)
        # 可写校验：确保目录可写，避免后续写入静默失败。
        # 唯一临时文件名 + 项目锁：并发 sync 各写各的探测文件，互不占用。
        probe = os.path.join(
            d, f".write_probe_{os.getpid()}_{threading.get_ident()}")
        with _timeline_lock(project_path):
            with open(probe, "w", encoding="utf-8") as f:
                f.write("probe")
            os.remove(probe)
    except Exception as e:
        raise OSError(f"操作记忆目录不可写: {d} ({e})") from e
    return d


def _safe_read(path: str, limit: int = 0) -> str:
    """安全读文本文件，失败返回空串（best-effort）"""
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            content = f.read()
        if limit and len(content) > limit:
            content = content[:limit]
        return content
    except Exception:
        return ""


# 原子写并发重试与跨进程锁常量（集中管理，不散落 magic number）
_ATOMIC_WRITE_MAX_RETRIES = settings.OMEM_ATOMIC_MAX_RETRIES
_ATOMIC_WRITE_RETRY_DELAY = settings.OMEM_ATOMIC_RETRY_DELAY   # 秒
_ATOMIC_RETRY_BACKOFF = settings.OMEM_ATOMIC_RETRY_BACKOFF
_PER_FILE_LOCK = settings.OMEM_PER_FILE_LOCK

# 跨进程锁能力探测：Windows 用 msvcrt.locking，Unix 用 fcntl.flock
try:
    import msvcrt as _msvcrt  # type: ignore
except ImportError:  # pragma: no cover
    _msvcrt = None
try:
    import fcntl as _fcntl  # type: ignore
except ImportError:  # pragma: no cover
    _fcntl = None

# 占用类错误（值得重试，其余永久性错误直接失败）：
# Windows WinError 5(拒绝访问)/32(被其他进程占用)；POSIX EAGAIN/EBUSY/ETXTBSY
_LOCK_CONTENDED_WINERR = {5, 32}


class _InterProcessLock:
    """对 <目标路径>.lock 加跨进程排他锁，串行化不同进程对同一产物的并发替换。

    仅当目标系统提供 flock/msvcrt 且配置开启时生效；否则退化为空锁（no-op），
    由 _write_atomic 的重试机制兜底。锁文件保持存在（残留 .lock），不清理，
    避免"反复创建/删除锁文件"本身引入新的并发竞态。
    """

    def __init__(self, target_path: str):
        self._lock_path = target_path + ".lock"

    def __enter__(self):
        try:
            self._fd = os.open(self._lock_path, os.O_CREAT | os.O_RDWR, 0o644)
        except OSError:
            self._fd = None
            return self
        try:
            if _msvcrt is not None:
                # msvcrt.locking 要求文件非空，先补一个字节
                if os.fstat(self._fd).st_size == 0:
                    os.write(self._fd, b"\x00")
                os.lseek(self._fd, 0, os.SEEK_SET)
                # LK_LOCK 阻塞式获取，专用于跨进程互斥
                _msvcrt.locking(self._fd, _msvcrt.LK_LOCK, 1)
            elif _fcntl is not None:
                _fcntl.flock(self._fd, _fcntl.LOCK_EX)
            self._locked = True
        except OSError:
            self._locked = False
        return self

    def __exit__(self, *exc):
        if self._fd is None:
            return
        try:
            if self._locked:
                if _msvcrt is not None:
                    os.lseek(self._fd, 0, os.SEEK_SET)
                    _msvcrt.locking(self._fd, _msvcrt.LK_UNLCK, 1)
                elif _fcntl is not None:
                    _fcntl.flock(self._fd, _fcntl.LOCK_UN)
        except OSError:
            # 解锁失败也继续关闭句柄
            pass
        finally:
            os.close(self._fd)


def _is_lock_contended_err(e: BaseException) -> bool:
    """是否属于"文件被占用/资源暂不可用"类错误（重试有价值）。"""
    if not isinstance(e, OSError):
        return False
    if getattr(e, "winerror", None) in _LOCK_CONTENDED_WINERR:
        return True
    if getattr(e, "errno", None) not in (None, 0):
        import errno as _errno
        if e.errno in (_errno.EAGAIN, _errno.EBUSY, _errno.ETXTBSY):
            return True
    return False


# 每项目发布锁，防止并发 sync 互相覆盖产物（ledger / BRAIN.md / timeline）
# 用 RLock 以便同一 sync 流程内多次获取同一项目的锁（外层发布序列 + 内层 timeline）
_timeline_locks: Dict[str, threading.RLock] = {}
_timeline_locks_lock = threading.Lock()


def _timeline_lock(project_path: str) -> threading.RLock:
    """获取或创建项目对应的发布锁（可重入）"""
    with _timeline_locks_lock:
        if project_path not in _timeline_locks:
            _timeline_locks[project_path] = threading.RLock()
        return _timeline_locks[project_path]


def _write_atomic(path: str, data: str) -> bool:
    """原子写（独占临时文件 + os.replace），避免写一半损坏或并发写串扰。

    每次写入用 tempfile.mkstemp 在目标文件同目录创建独占临时文件（原子创建，
    杜绝共享 / 碰撞路径被截断），写成功并 fsync 后再原子替换目标文件。

    并发写稳定性（对外根除 WinError 5/32 的重试兜底）：
    - 跨进程互斥（_PER_FILE_LOCK 开启且系统支持 flock/msvcrt 时）：替换前对
      <目标>.lock 加跨进程排他锁，串行化不同进程对同一产物的替换，从根上避免
      Windows 覆盖正被打开的目标文件时报 WinError 5/32。
    - 精确化重试：无论是否开启文件锁，替换失败时只对「占用类」错误
      （WinError 5/32、POSIX EAGAIN/EBUSY/ETXTBSY）按指数退避重试有限次，
      其余永久性错误直接失败，不做无谓重试。

    持久性保证：仅保证"原子可见性"（读者要么看到完整旧文件、要么看到
    完整新文件，不会看到半写内容），不保证断电持久性——未同步父目录的
    目录项改动，掉电时可能丢失 os.replace 结果。
    """
    # mkstemp 原子创建独占临时文件，避免旧实现"pid+线程id+时间戳"碰撞时
    # 截断已存在文件；随后的 os.replace 再原子替换目标文件。
    try:
        fd, tmp = tempfile.mkstemp(
            dir=os.path.dirname(path) or ".",
            prefix="." + os.path.basename(path) + ".",
            suffix=".tmp",
        )
    except OSError as e:
        logger.warning(f"[OperationMemory] 写入失败 {path}: {e}")
        return False

    try:
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(data)
                f.flush()
                os.fsync(f.fileno())
        except (OSError, UnicodeError) as e:
            # 预期的 IO / 编码错误 → 判定为存储失败
            logger.warning(f"[OperationMemory] 写入失败 {path}: {e}")
            return False

        for attempt in range(_ATOMIC_WRITE_MAX_RETRIES):
            try:
                # 跨进程互斥：同一目标文件的并发替换只在持锁进程内进行。
                # 开启文件锁时可彻底避免 WinError 5/32；未开启锁时重试仍兜底。
                if _PER_FILE_LOCK:
                    with _InterProcessLock(path):
                        os.replace(tmp, path)
                else:
                    os.replace(tmp, path)
                return True
            except OSError as e:
                if _is_lock_contended_err(e) and attempt < _ATOMIC_WRITE_MAX_RETRIES - 1:
                    # 仅占用类错误重试，并按指数退避拉长等待窗
                    time.sleep(_ATOMIC_WRITE_RETRY_DELAY * (_ATOMIC_RETRY_BACKOFF ** attempt))
                    continue
                logger.warning(f"[OperationMemory] 写入失败 {path}: {e}")
                return False
        return False
    except Exception:
        # 非预期异常：记录完整堆栈，仍按存储失败返回，交由 finally 清理临时文件
        logger.exception(f"[OperationMemory] 写入失败 {path}")
        return False
    finally:
        # 所有失败路径都清掉临时文件，避免堆积 .tmp；replace 已成功时
        # tmp 不存在，忽略 FileNotFoundError。
        try:
            os.remove(tmp)
        except FileNotFoundError:
            # replace 成功后 tmp 已不存在，属预期情况，无需处理
            pass
        except OSError as e:
            logger.warning(f"[OperationMemory] 清理临时文件失败 {tmp}: {e}")


def _compute_snapshot(files: List[str]) -> Dict[str, Dict[str, float]]:
    """计算文件快照（mtime + size 双值，用于增量校验）"""
    snap: Dict[str, Dict[str, float]] = {}
    for fp in files:
        try:
            st = os.stat(fp)
            snap[fp] = {"mtime": st.st_mtime, "size": st.st_size}
        except Exception:
            snap[fp] = {"mtime": 0.0, "size": 0}
    return snap


def _same_file(old: Optional[dict], cur: Optional[dict]) -> bool:
    """mtime+size 双校验，避免缓存过期误判"""
    if not old or not cur:
        return False
    return abs(old.get("mtime", 0) - cur.get("mtime", 0)) < _MTIME_TOLERANCE and \
        old.get("size") == cur.get("size")


def _classify_category(query_type: str) -> str:
    """把 query_type 别名归一化为分类账的类别 key"""
    mapping = {
        "resource": "resource", "resources": "resource",
        "tool": "tool", "tools": "tool",
        "doc": "doc", "docs": "doc", "document": "doc",
        "decision": "decision", "decisions": "decision",
        "convention": "convention", "conventions": "convention",
        "pitfall": "pitfall", "pitfalls": "pitfall",
        "all": "all",
    }
    return mapping.get(query_type, "all")


# ═══════════════════════════════════════════════════════════════════
# 静态审计：资源发现
# ═══════════════════════════════════════════════════════════════════

def _walk_files(root: str) -> List[str]:
    """遍历项目文件，跳过忽略目录，返回文件绝对路径列表"""
    files: List[str] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in _IGNORE_DIRS]
        for fn in filenames:
            try:
                files.append(os.path.join(dirpath, fn))
            except Exception:
                # join 不会实际抛异常，保持防御性包裹
                pass
    return files


def _is_sensitive_path(path: str) -> bool:
    """判断路径是否含敏感特征（如 key / secret / token）"""
    low = path.lower()
    return any(hint in low for hint in SENSITIVE_HINTS)


class ResourceScanner:
    """静态资源扫描器：识别主目录内的资源位置"""

    def __init__(self, project_path: str):
        self.root = os.path.abspath(project_path)

    def scan(self) -> Dict[str, List[dict]]:
        """扫描主目录，返回分类后的资源清单"""
        result: Dict[str, List[dict]] = {k: [] for k in RESOURCE_TYPES}
        files = _walk_files(self.root)

        # git 仓库
        self._detect_git(result)
        # 依赖清单
        self._detect_dependencies(files, result)
        # 模型权重
        self._detect_models(files, result)
        # API 配置引用
        self._detect_api(files, result)
        # 测试工具
        self._detect_tools(files, result)
        # 文档 / 报告
        self._detect_docs_reports(files, result)
        # 外部开发工具可执行文件
        self._detect_env_tools(result)
        # WSL 子系统内工具（如 coderabbit）
        self._detect_wsl_tools(result)

        # 每分类截断，防止撑爆
        for k in result:
            result[k] = result[k][:MAX_PER_CATEGORY]
        return result

    def _detect_git(self, result: Dict[str, List[dict]]) -> None:
        git_dir = os.path.join(self.root, ".git")
        if os.path.isdir(git_dir):
            ignore = os.path.join(self.root, ".gitignore")
            excludes = ""
            if os.path.isfile(ignore):
                excludes = _safe_read(ignore, 2000)
            result["git"].append({
                "name": "git 仓库",
                "path": self.root,
                "location": "project_root",
                "source": "主目录内 .git",
                "note": "排除项见 .gitignore" if excludes else "无 .gitignore",
            })

    def _detect_dependencies(self, files: List[str],
                             result: Dict[str, List[dict]]) -> None:
        for fp in files:
            base = os.path.basename(fp)
            if base in _DEPENDENCY_FILES:
                content = _safe_read(fp, 1500)
                result["dependency"].append({
                    "name": base,
                    "path": fp,
                    "location": "project",
                    "source": _DEPENDENCY_FILES[base],
                    "note": _summarize_deps(base, content),
                })

    def _detect_models(self, files: List[str],
                       result: Dict[str, List[dict]]) -> None:
        for fp in files:
            if os.path.splitext(fp)[1].lower() in MODEL_EXTENSIONS:
                rel = os.path.relpath(fp, self.root)
                result["model"].append({
                    "name": os.path.basename(fp),
                    "path": fp,
                    "location": "project",
                    "source": f"主目录内模型文件 {rel}",
                    "size": _fmt_size(os.path.getsize(fp)),
                    "note": "模型权重文件",
                })

    def _detect_api(self, files: List[str],
                    result: Dict[str, List[dict]]) -> None:
        # 常见 API Key 配置文件名
        api_names = ("config.json", ".env", "config.yaml", "config.yml",
                     "settings.json", "secrets.json")
        for fp in files:
            base = os.path.basename(fp)
            if base in api_names:
                content = _safe_read(fp, 3000)
                has_key = any(k in content.lower() for k in
                              ("api_key", "apikey", "api-key", "token", "secret"))
                if has_key:
                    sensitive = _is_sensitive_path(fp)
                    result["api"].append({
                        "name": base,
                        "path": fp,
                        "location": "project",
                        "source": "配置文件中含 API Key 引用",
                        "sensitive": sensitive,
                        "note": "含密钥引用，只记录位置不收录内容" if sensitive else "含密钥引用",
                    })

    def _detect_tools(self, files: List[str],
                      result: Dict[str, List[dict]]) -> None:
        for fp in files:
            parts = fp.replace("\\", "/").split("/")
            if any(p.lower() in _TEST_DIR_HINTS
                   for p in parts[:-1]) and os.path.splitext(fp)[1].lower() in (
                    ".py", ".js", ".ts", ".go", ".rs", ".sh"):
                result["tool"].append({
                    "name": os.path.basename(fp),
                    "path": fp,
                    "location": "project",
                    "source": "测试目录下的可执行脚本",
                    "note": "测试工具 / 用例",
                })

    def _detect_docs_reports(self, files: List[str],
                             result: Dict[str, List[dict]]) -> None:
        for fp in files:
            ext = os.path.splitext(fp)[1].lower()
            rel = os.path.relpath(fp, self.root)
            if ext in _REPORT_EXTENSIONS and (
                    "report" in rel.lower() or "doc" in rel.lower()):
                result["report"].append({
                    "name": os.path.basename(fp),
                    "path": fp,
                    "location": "project",
                    "source": f"报告 / 文档 {rel}",
                    "note": "开发产出文档",
                })
            elif ext in _DOC_EXTENSIONS and (
                    "docs" in rel.lower() or "wiki" in rel.lower() or
                    "readme" in os.path.basename(fp).lower()):
                result["doc"].append({
                    "name": os.path.basename(fp),
                    "path": fp,
                    "location": "project",
                    "source": f"项目文档 {rel}",
                    "note": "说明 / 规范文档",
                })

    def _detect_env_tools(self, result: Dict[str, List[dict]]) -> None:
        """探测外部开发工具可执行文件位置（解决便携工具不在 PATH 的问题）。

        先在 PATH 中查找，找不到再在常见便携根目录探测（如 PortablbeGit）。
        只记录位置与来源，不承载任何工具逻辑。
        """
        for tool, (bin_name, desc) in ENV_TOOL_BINS.items():
            path = _find_tool_executable(tool, bin_name)
            if not path:
                continue
            result["env_tool"].append({
                "name": tool,
                "path": path,
                "location": "env",
                "source": _tool_location_source(path),
                "note": f"{desc}（可执行文件位置）",
            })

    def _detect_wsl_tools(self, result: Dict[str, List[dict]]) -> None:
        """探测 WSL 子系统内工具（如 coderabbit）。

        Windows PATH / 便携根扫不到这类工具，需经 wsl.exe 进入发行版探测。
        先记录 wsl.exe 入口本身（解决其不在 PATH 的问题），再探测清单内工具。
        只记录位置与来源，不承载任何工具逻辑；WSL 不可用时静默跳过。
        """
        launcher = _locate_wsl_launcher()
        if launcher:
            result["env_tool"].append({
                "name": "wsl",
                "path": launcher,
                "location": "env",
                "source": "Windows 子系统 Linux 启动器（SystemRoot\\System32 fallback）",
                "note": "进入 WSL 发行版的入口可执行文件",
            })
        for tool, bin_name in WSL_TOOL_BINS.items():
            path = _find_wsl_tool(bin_name)
            if not path:
                continue
            result["env_tool"].append({
                "name": tool,
                "path": path,
                "location": "wsl",
                "source": f"WSL 子系统内工具（via {launcher or 'wsl'}）",
                "note": f"{bin_name}（WSL 内可执行文件，运行需经 wsl.exe）",
            })


def _find_tool_executable(tool: str, bin_name: str) -> str:
    """先在 PATH 查找，再在常见便携根探测。返回可执行文件绝对路径，找不到返回空串。"""
    # 1. PATH 中查找
    try:
        p = shutil.which(tool) or shutil.which(bin_name)
        if p:
            return os.path.abspath(p)
    except Exception:
        # PATH 探测失败时继续尝试便携根
        pass
    # 2. 常见便携根探测（支持 glob 通配）
    for root_pat in ENV_TOOL_ROOTS:
        try:
            roots = glob.glob(os.path.expanduser(root_pat))
        except Exception:
            # glob 模式异常时跳过该根
            continue
        for root in roots:
            if not os.path.isdir(root):
                continue
            for sub in ENV_TOOL_BIN_SUBDIRS:
                cand = os.path.join(root, sub.replace("/", os.sep), bin_name)
                if os.path.isfile(cand):
                    return os.path.abspath(cand)
    return ""


def _tool_location_source(path: str) -> str:
    """根据可执行文件路径推断工具来源，供 AI 理解便携 / 系统差异。"""
    low = path.lower()
    if "portablegit" in low:
        return "便携 git 包（不在 PATH）"
    if "\\program files\\" in low:
        return "系统安装（PATH 可能不含）"
    if "\\appdata\\" in low:
        return "用户级安装（PATH 可能不含）"
    return "PATH 可执行"


def _locate_wsl_launcher() -> str:
    """定位 wsl.exe 启动器：先 PATH，再 SystemRoot\\System32 fallback。

    本工具环境的 PowerShell PATH 常不含 System32，导致 `wsl` 直接找不到；
    而进入 WSL 子系统执行命令必须以 wsl.exe 为入口，故需显式回退完整路径。
    """
    try:
        p = shutil.which("wsl")
        if p:
            return os.path.abspath(p)
    except Exception:
        # PATH 探测失败时回退 System32 默认路径
        pass
    system32 = os.path.join(os.environ.get("SystemRoot", "C:/Windows"),
                            "System32", "wsl.exe")
    return system32 if os.path.isfile(system32) else ""


def _find_wsl_tool(bin_name: str) -> str:
    """在 WSL 子系统内定位工具（如 coderabbit）。

    通过 wsl.exe 进入默认发行版探测命令路径。先 `command -v`（PATH 命中），
    失败再回退用户级 bin 目录 `$HOME/.local/bin`——WSL 内的 CLI 常用
    `pip install --user` / 脚本安装落在此处，却不在非登录 shell 的 PATH 里。
    WSL 首次冷启动可能较慢导致偶发超时/空输出，作有限次静默重试。
    仅返回"能定位到"的结果；WSL 不可用 / 命令不存在时返回空串，不抛异常。
    """
    launcher = _locate_wsl_launcher()
    if not launcher:
        return ""
    cmd = ("command -v {bin} || ls -d $HOME/.local/bin/{bin} 2>/dev/null || true"
           .format(bin=bin_name))
    for _ in range(WSL_PROBE_RETRIES):
        try:
            proc = subprocess.run(
                [launcher, "-e", "bash", "-lc", cmd],
                capture_output=True, text=True, timeout=WSL_CMD_TIMEOUT,
            )
            out = (proc.stdout or "").strip()
            if out:
                return out
        except Exception:
            # WSL 不可用时返回空，调用方降级
            pass
    return ""


def _fmt_size(size: float) -> str:
    """字节 → 人类可读"""
    if size >= 1 << 30:
        return f"{size / (1 << 30):.1f} GB"
    if size >= 1 << 20:
        return f"{size / (1 << 20):.1f} MB"
    if size >= 1 << 10:
        return f"{size / (1 << 10):.1f} KB"
    return f"{int(size)} B"


def _summarize_deps(filename: str, content: str) -> str:
    """从依赖清单文件内容提炼一行摘要（best-effort，失败返回占位说明）。

    对不同清单格式做轻量解析：抓取顶层包名 / 依赖名，避免原样倾倒大文件。
    """
    if not content:
        return "无可读内容"
    try:
        lines = [ln.strip() for ln in content.splitlines() if ln.strip()]
        if not lines:
            return "空清单"
        if filename == "package.json":
            # 完整解析 manifest，只取四个依赖组键名，避免 name/version/scripts 等元数据
            names = []
            try:
                manifest = json.loads(content)
                for dep_key in ("dependencies", "devDependencies",
                                "peerDependencies", "optionalDependencies"):
                    group = manifest.get(dep_key)
                    if isinstance(group, dict):
                        names.extend(str(k) for k in group)
            except Exception:
                # 内容截断 / 非法 JSON：回退正则抓包名，跳过元数据区键
                names = re.findall(
                    r'"(?!"(?:scripts|name|version|description|author|license|repository|engines|keywords)"):)\s*"([A-Za-z0-9@._/-]+)"\s*:', content)
            picked = names[:20]
        else:
            # requirements/pyproject/Cargo/go/Gemfile 等：取每行包名（去掉版本/约束/注释）
            picked = []
            for ln in lines:
                if ln.startswith(("#", "-", "[", "//", "#[")):
                    continue
                if "== " in ln or ">=" in ln or "~=" in ln or "==" in ln:
                    picked.append(ln)
                elif " " not in ln and ln:
                    picked.append(ln)
                if len(picked) >= 20:
                    break
        if not picked:
            return "依赖较多，未识别到顶层包名"
        shown = ", ".join(picked)
        return shown if len(shown) <= 200 else shown[:200] + "…"
    except Exception:
        return "依赖清单较复杂，未能自动摘要"


# ═══════════════════════════════════════════════════════════════════
# 旁目录探测（按引用定向探明，不主动深扫）
# ═══════════════════════════════════════════════════════════════════

# 常见旁目录候选（相对家目录 / 项目根）
_SIDE_CANDIDATES = (
    ".cache", ".cache/models", ".cache/huggingface", "models", "weights",
    "data", "datasets", "downloads",
)


def _probe_side_dirs(project_path: str) -> List[dict]:
    """探测旁目录：优先家目录候选，命中即记录位置 + 是否敏感。

    不主动深扫，只对候选目录做存在性检查，避免扩散与隐私风险。
    """
    found: List[dict] = []
    home = os.path.expanduser("~")

    # 项目内 data/models 等子目录
    for cand in _SIDE_CANDIDATES:
        p = os.path.join(project_path, cand)
        if os.path.isdir(p):
            found.append(_side_entry("项目旁目录", p, home))

    # 家目录候选（模型缓存等）
    for cand in (".cache/models", ".cache/huggingface", ".cache",
                 "models", "Downloads"):
        p = os.path.join(home, cand)
        if os.path.isdir(p):
            found.append(_side_entry("家目录旁目录", p, home))

    found = found[:MAX_SIDE_DIRS]
    return found


def _side_entry(kind: str, path: str, home: str) -> dict:
    """构造旁目录条目，标注是否敏感"""
    sensitive = _is_sensitive_path(path)
    rel = path
    if path.startswith(home):
        rel = "~" + path[len(home):]
    return {
        "name": os.path.basename(path),
        "path": path,
        "location": "side",
        "kind": kind,
        "display": rel,
        "sensitive": sensitive,
        "note": "敏感旁目录，只记录位置" if sensitive else "旁目录资源",
    }


# ═══════════════════════════════════════════════════════════════════
# LLM 提炼：隐性知识（决策 / 约定 / 踩坑）
# ═══════════════════════════════════════════════════════════════════

_EXTRACT_PROMPT = (
    "你是项目的知识沉淀助手。请从下面的项目文档片段中，提炼三类隐性知识，"
    "以 JSON 数组输出（不要输出其他文字）。每一项包含：\n"
    '{"kind": "decision|convention|pitfall", "summary": "一句话要点", '
    '"detail": "补充说明", "source": "来源文件"}\n'
    "decision=决策及理由；convention=命名/目录/错误处理等约定俗成；"
    "pitfall=踩过的坑与解法。找不到就返回空数组 []。\n\n"
    "文档片段：\n{source}"
)


class KnowledgeExtractor:
    """隐性知识提炼器：依赖 LLM，API Key 缺失时优雅降级"""

    def __init__(self, with_llm: bool = True):
        self.with_llm = with_llm
        self._llm = None
        if with_llm:
            try:
                from core.llm_integration import LLMIntegration
                self._llm = LLMIntegration()
            except Exception as e:
                logger.warning(f"[OperationMemory] LLM 初始化失败: {e}")
                self._llm = None

    def llm_available(self) -> bool:
        return bool(self._llm and getattr(self._llm, "client", None) is not None)

    def extract(self, sources: List[dict]) -> Dict[str, List[dict]]:
        """从文档来源提炼隐性知识，返回按 kind 归类的条目。

        LLM 不可用时降级为"待人工确认"：仍写入带 pending 标记的提醒条目，
        使 status() 的 pending_human 能识别到"有隐性知识待人工补充"，而不是静默返回空。
        """
        result: Dict[str, List[dict]] = {"decision": [], "convention": [], "pitfall": []}
        if not self.llm_available():
            logger.info("[OperationMemory] LLM 不可用，隐性知识提炼降级为待人工确认")
            now = datetime.now().isoformat(timespec="seconds")
            for k in result:
                result[k].append({
                    "summary": f"{_KIND_LABELS.get(k, k)}知识待人工确认",
                    "detail": "运行 sync 时未检测到可用 LLM / API Key，未自动提炼。"
                              "请人工补充，或配置 LLM 后重跑 coderef_operation_memory(action=sync)。",
                    "source": "operation_memory",
                    "time": now,
                    "pending": True,
                })
            return result

        for src in sources[:EXTRACT_GRAPH_LIMIT]:
            content = src.get("content", "")
            if not content:
                continue
            chunk = content[:LLM_MAX_CHARS_SOURCE]
            # 用 replace 占位符而非 str.format：模板内的 JSON 花括号({"kind": ...})
            # 会被 format 误作字段名解析，导致 KeyError('"kind"')（）。
            prompt = _EXTRACT_PROMPT.replace("{source}", chunk)
            try:
                raw = self._llm.chat_completion(
                    [{"role": "user", "content": prompt}],
                    timeout=LLM_TIMEOUT,
                )
                items = _parse_json_list(raw)
            except Exception as e:
                logger.warning(f"[OperationMemory] LLM 提炼失败: {e}")
                continue
            for it in items[:10]:
                kind = it.get("kind")
                if kind in result:
                    result[kind].append({
                        "summary": it.get("summary", ""),
                        "detail": it.get("detail", ""),
                        "source": it.get("source") or src.get("path", ""),
                        "time": datetime.now().isoformat(timespec="seconds"),
                    })
        # 去重（按 summary 去重）
        for k in result:
            seen: set = set()
            dedup = []
            for it in result[k]:
                key = it.get("summary", "")
                if key and key not in seen:
                    seen.add(key)
                    dedup.append(it)
            result[k] = dedup
        return result


def _parse_json_list(raw: str) -> List[dict]:
    """解析 LLM 输出的 JSON 数组，带容错（strip 代码块 / 截断补全）"""
    if not raw:
        return []
    text = raw.strip()
    # 去掉 ```json ... ``` 包裹
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    text = text.strip()
    # 优先按首字符判断数组 / 对象
    if text.startswith("{"):
        text = "[" + text + "]"
    # 截断补全：若未闭合，尝试补 ]
    if text.startswith("[") and not text.rstrip().endswith("]"):
        text = text + "]"
    try:
        data = json.loads(text)
        if isinstance(data, dict):
            data = [data]
        return [d for d in data if isinstance(d, dict)]
    except Exception:
        return []


# ═══════════════════════════════════════════════════════════════════
# 主类
# ═══════════════════════════════════════════════════════════════════

class OperationMemory:
    """操作记忆层核心"""

    def __init__(self):
        pass

    def sync(self, project_path: str, mode: str = "full",
             with_llm: bool = True) -> dict:
        """初始化 / 增量同步操作记忆。

        mode=full 全量盘点；mode=incr 增量（基于 mtime+size 快照只重扫变更）。
        with_llm=False 跳过隐性知识提炼（节省调用）。
        """
        project_path = os.path.abspath(project_path)
        if mode not in ("full", "incr"):
            mode = "full"
        if not os.path.isdir(project_path):
            return {"status": "error", "mode": mode,
                    "message": "项目路径不存在", "total_resources": 0}

        try:
            d = _ensure_dirs(project_path)
        except OSError as e:
            return {"status": "error", "mode": mode, "message": str(e), "total_resources": 0}
        ledger_path = os.path.join(d, "ledger.json")
        brain_path = os.path.join(d, "BRAIN.md")
        timeline_path = os.path.join(d, "timeline.md")

        # 3. 增量判断：对比上次快照（必须放在文件扫描之前）
        prev = self._load_ledger(project_path)
        changed_reason = "full"
        if mode == "incr" and prev:
            old_snap = prev.get("snapshot", {})
            # 先计算当前快照，判断是否变更
            files = _walk_files(project_path)
            cur_snap = _compute_snapshot(files)
            changed = any(not _same_file(old_snap.get(fp), cur_snap.get(fp))
                          for fp in files)
            changed = changed or len(files) != len(old_snap)
            if not changed:
                logger.info("[OperationMemory] 增量同步：文件无变更，复用已有记忆")
                # 无变更 → 复用既有资源和旁目录，不重新扫描
                return {
                    "status": "ok", "mode": mode, "changed": False,
                    "message": "文件无变更，复用已有操作记忆",
                    "resources": prev.get("resources", {}),
                    "side_dirs": prev.get("side_dirs", []),
                    "ledger_path": _omem_dir(project_path),
                    "brain_path": os.path.join(_omem_dir(project_path), "BRAIN.md"),
                }
            changed_reason = "incr-changed"

        # 1. 静态审计：主目录资源（增量变更后才需要全量重扫）
        scanner = ResourceScanner(project_path)
        resources = scanner.scan()

        # 2. 旁目录探测（增量变更后才需要重探）
        side_dirs = _probe_side_dirs(project_path)

        # 4. 隐性知识提炼（可选）
        knowledge: Dict[str, List[dict]] = {"decision": [], "convention": [], "pitfall": []}
        llm_available = False
        extract_error = ""
        if with_llm:
            try:
                extractor = KnowledgeExtractor(with_llm=True)
                llm_available = extractor.llm_available()
                sources = self._collect_knowledge_sources(project_path, resources)
                knowledge = extractor.extract(sources)
            except Exception as e:
                # 提炼路径任何意外异常不得让整个 sync 裸报错（）：降级
                # 为结构化错误记录并继续完成静态盘点到，保证有可用记忆产物。
                logger.exception(f"[OperationMemory] LLM 提炼整体失败: {e}")
                extract_error = f"隐性知识提炼失败: {e}"
                knowledge = {"decision": [], "convention": [], "pitfall": []}

        # 5. 组装 ledger
        ledger = {
            "project_path": project_path,
            "hash": _project_hash(project_path),
            "mode": changed_reason,
            "last_sync": datetime.now().isoformat(timespec="seconds"),
            "snapshot": None,  # 增量一致性由文件 mtime 校验，这里占位
            "resources": resources,
            "side_dirs": side_dirs,
            "knowledge": knowledge,
            "llm_available": llm_available,
        }
        # 记录快照（用于下次增量）
        try:
            files = _walk_files(project_path)
            ledger["snapshot"] = _compute_snapshot(files)
        except Exception:
            ledger["snapshot"] = {}

        # 6. 持久化（发布序列：ledger → BRAIN.md → timeline 在同一 per-project 锁内，
        #    避免并发 sync 交错写坏三个产物；扫描与 LLM 抽取在锁外）
        with _timeline_lock(project_path):
            ok_ledger = _write_atomic(ledger_path, json.dumps(ledger, ensure_ascii=False, indent=2))
            ok_brain = _write_atomic(brain_path, self._render_brain(ledger))
            ok_timeline = self._append_timeline(project_path, ledger, timeline_path)
        if not (ok_ledger and ok_brain and ok_timeline):
            return {
                "status": "error",
                "mode": mode,
                "message": "操作记忆写入失败",
                "total_resources": 0,
            }

        return {
            "status": "ok",
            "mode": mode,
            "changed": True,
            "resources": _count_resources(resources),
            "side_dirs": side_dirs,
            "knowledge": {k: len(v) for k, v in knowledge.items()},
            "llm_available": llm_available,
            "extract_error": extract_error or None,
            "ledger_path": ledger_path,
            "brain_path": brain_path,
        }

    def _load_ledger(self, project_path: str) -> Optional[dict]:
        """读取既有 ledger（供增量 / 查询 / 状态复用）"""
        p = os.path.join(_omem_dir(project_path), "ledger.json")
        if not os.path.isfile(p):
            return None
        try:
            with open(p, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            # 知识源不可读时返回 None
            logger.warning(f"读取知识源失败，跳过 {p}: {e}")
            return None

    def _collect_knowledge_sources(self, project_path: str,
                                   resources: Dict[str, List[dict]]) -> List[dict]:
        """收集用于 LLM 提炼的文档来源（docs / wiki / README / 报告）"""
        sources: List[dict] = []
        seen: set = set()
        for key in ("doc", "report"):
            for r in resources.get(key, []):
                p = r.get("path", "")
                if p and p not in seen:
                    seen.add(p)
                    sources.append({"path": p, "content": _safe_read(p, LLM_MAX_CHARS_SOURCE)})
        # 补充 README
        readme = os.path.join(project_path, "README.md")
        if os.path.isfile(readme) and readme not in seen:
            sources.append({"path": readme, "content": _safe_read(readme, LLM_MAX_CHARS_SOURCE)})
        return sources

    def _render_brain(self, ledger: dict) -> str:
        """渲染人类可读的 BRAIN 式 Markdown 记忆页"""
        lines = [
            "# 操作记忆（Operation Memory）",
            "",
            f"- 项目：`{ledger['project_path']}`",
            f"- 最近同步：{ledger.get('last_sync', '-')}",
            f"- LLM 提炼：{'可用' if ledger.get('llm_available') else '未启用 / 待人工确认'}",
            "",
            "> 本页的 `BRAIN.md` 命名、判存标准与时间线机制，借鉴自 mindmuxai/brain.md"
            "（Apache-2.0）；分层记忆与渐进式披露思路参考 TencentDB-Agent-Memory（MIT）。",
            "",
            "## 资源定位",
            _render_resources(ledger.get("resources", {})),
            "",
            "## 旁目录",
            _render_side_dirs(ledger.get("side_dirs", [])),
            "",
            "## 隐性知识",
            _render_knowledge(ledger.get("knowledge", {})),
            "",
        ]
        return "\n".join(lines)

    def _append_timeline(self, project_path: str, ledger: dict,
                         timeline_path: str) -> bool:
        """追加式时间线（保留最近 N 条，可追溯）"""
        # per-project 锁串行化"读-改-写"，避免并发 sync 读到同一旧内容、
        # 最后一次替换丢掉另一条新增条目；替换仍走 _write_atomic 保持原子性。
        # 该锁可重入：sync 外层的发布序列已持有同一项目的锁，这里再次获取不冲突。
        with _timeline_lock(project_path):
            stamp = ledger.get("last_sync", "-")
            cc = _count_resources(ledger.get("resources", {}).items())
            entry = f"- [{stamp}] 同步完成：资源 {sum(cc.values())} 项，旁目录 {len(ledger.get('side_dirs', []))} 个"
            old = _safe_read(timeline_path)
            lines = old.splitlines() if old else ["# 操作记忆时间线", ""]
            # 去掉可能的旧标题行以便重建
            lines = [line for line in lines if not line.startswith("# 操作记忆时间线")]
            lines.insert(0, "# 操作记忆时间线")
            lines.append(entry)
            # 保留最近 N 条
            if len(lines) > TIMELINE_MAX + 2:
                lines = lines[:2] + lines[-(TIMELINE_MAX - 2):]
            return _write_atomic(timeline_path, "\n".join(lines) + "\n")

    def query(self, project_path: str, query_type: str = "all",
              keyword: str = "", limit: int = 10) -> dict:
        """按类别检索操作记忆。结构化查询直接过滤 ledger。"""
        project_path = os.path.abspath(project_path)
        ledger = self._load_ledger(project_path)
        if not ledger:
            return {"status": "error", "message": "操作记忆尚未同步，请先调用 coderef_operation_memory(action=sync)",
                    "query_type": query_type}

        category = _classify_category(query_type)
        kw = (keyword or "").strip().lower()
        results: List[dict] = []

        # 资源 / 工具 / 文档 / 报告 → resources
        if category in ("all", "resource", "tool", "doc"):
            for rtype in ("git", "model", "api", "dependency",
                          "tool", "env_tool", "doc", "report"):
                if category == "all" or category == "resource" or \
                        (category == "tool" and rtype in ("tool", "env_tool")) or \
                        (category == "doc" and rtype in ("doc", "report")):
                    for it in ledger.get("resources", {}).get(rtype, []):
                        if self._match(it, kw):
                            results.append({**it, "category": rtype})

        # 隐性知识 → knowledge
        if category in ("all", "decision", "convention", "pitfall"):
            for ktype in ("decision", "convention", "pitfall"):
                if category == "all" or category == ktype:
                    for it in ledger.get("knowledge", {}).get(ktype, []):
                        if self._match(it, kw):
                            results.append({**it, "category": ktype})

        # 旁目录
        if category == "all":
            for it in ledger.get("side_dirs", []):
                if self._match(it, kw):
                    results.append({**it, "category": "side_dir"})

        return {
            "status": "ok",
            "query_type": query_type,
            "category": category,
            "keyword": keyword,
            "total": len(results),
            "results": results[:limit],
        }

    def find(self, project_path: str, name: str, limit: int = 5) -> dict:
        """定位资源：给定资源名 / 路径片段，返回实际位置、来源、主 / 旁目录。"""
        project_path = os.path.abspath(project_path)
        ledger = self._load_ledger(project_path)
        if not ledger:
            return {"status": "error", "message": "操作记忆尚未同步，请先调用 coderef_operation_memory(action=sync)",
                    "name": name}

        kw = (name or "").strip().lower()
        if not kw:
            return {"status": "error", "message": "name 不能为空", "name": name}

        hits: List[dict] = []
        # 资源
        for rtype, items in ledger.get("resources", {}).items():
            for it in items:
                if kw in it.get("name", "").lower() or kw in it.get("path", "").lower():
                    hits.append({**it, "category": rtype, "side": "project"})
        # 旁目录
        for it in ledger.get("side_dirs", []):
            if kw in it.get("name", "").lower() or kw in it.get("path", "").lower():
                hits.append({**it, "category": "side_dir", "side": "side"})
        # 隐性知识
        for ktype in ("decision", "convention", "pitfall"):
            for it in ledger.get("knowledge", {}).get(ktype, []):
                if kw in it.get("summary", "").lower():
                    hits.append({**it, "category": ktype, "side": "knowledge"})

        # 按 side 优先级排序：project 优先于 side / knowledge
        side_rank = {"project": 0, "side": 1, "knowledge": 2}
        hits.sort(key=lambda h: side_rank.get(h.get("side"), 3))

        return {
            "status": "ok",
            "name": name,
            "total": len(hits),
            "results": hits[:limit],
        }

    def status(self, project_path: str) -> dict:
        """操作记忆健康状态"""
        project_path = os.path.abspath(project_path)
        ledger = self._load_ledger(project_path)
        if not ledger:
            return {"status": "error",
                    "message": "操作记忆尚未同步，请先调用 coderef_operation_memory(action=sync)"}

        resources = ledger.get("resources", {})
        counts = _count_resources(resources.items())
        knowledge = ledger.get("knowledge", {})
        kn = {k: len(v) for k, v in knowledge.items()}

        # 分类覆盖
        covered = [k for k, v in counts.items() if v > 0]
        pending = [it for k in ("decision", "convention", "pitfall")
                   for it in knowledge.get(k, [])
                   if it.get("pending")]

        return {
            "status": "ok",
            "project_path": project_path,
            "last_sync": ledger.get("last_sync", "-"),
            "llm_available": ledger.get("llm_available", False),
            "resource_counts": counts,
            "knowledge_counts": kn,
            "covered_categories": covered,
            "side_dirs": len(ledger.get("side_dirs", [])),
            "pending_human": len(pending),
            "brain_page": os.path.join(_omem_dir(project_path), "BRAIN.md"),
        }

    def recover(self, project_path: str, limit: int = 8) -> dict:
        """上下文丢失后『一次调用』恢复关键记忆。

        一次返回：关键工具位置（env_tool）+ 已确认的约定 / 踩坑 / 决策摘要 + 待人工确认项。
        供 AI 在上下文被压缩后最小成本拿回『东西在哪儿、过去的规范是什么』，
        避免多次 query/find 的截断丢失，也避免绕过记忆层去满 PATH 找或抓外部连接器。
        """
        project_path = os.path.abspath(project_path)
        ledger = self._load_ledger(project_path)
        if not ledger:
            return {"status": "error",
                    "message": "操作记忆尚未同步，请先调用 coderef_operation_memory(action=sync)",
                    "tool": "recover"}
        if limit < 1:
            return {"status": "error", "message": "limit 必须大于 0", "tool": "recover"}

        # 关键工具位置（git / python / wsl / coderabbit 等，含 WSL 旁目录）
        env_tools: List[dict] = []
        for it in ledger.get("resources", {}).get("env_tool", []):
            env_tools.append({
                "name": it.get("name"),
                "path": it.get("path"),
                "location": it.get("location"),
                "note": it.get("note"),
            })

        # 隐性知识：非 pending（已确认）优先取前 limit 条；pending 也按 limit 截断
        brief: Dict[str, List[dict]] = {"decision": [], "convention": [], "pitfall": []}
        pending_items: List[dict] = []
        pending_counts = {"decision": 0, "convention": 0, "pitfall": 0}
        for k in ("decision", "convention", "pitfall"):
            for it in ledger.get("knowledge", {}).get(k, []):
                if it.get("pending"):
                    pending_counts[k] += 1
                    if pending_counts[k] <= limit:
                        pending_items.append({"category": k, "summary": it.get("summary")})
                elif len(brief[k]) < limit:
                    brief[k].append({"summary": it.get("summary"),
                                     "source": it.get("source")})

        return {
            "status": "ok",
            "tool": "recover",
            "project_path": project_path,
            "last_sync": ledger.get("last_sync", "-"),
            "llm_available": ledger.get("llm_available", False),
            "env_tools": env_tools,
            "decisions": brief["decision"],
            "conventions": brief["convention"],
            "pitfalls": brief["pitfall"],
            "pending_items": pending_items,
            "pending_counts": pending_counts,
            "hint": ("涉及 git / push / CodeRabbit / Release 等工具或约定类操作时，"
                     "请优先采用本结果中的 env_tools 工具定位与 conventions / pitfalls 约定，"
                     "勿满 PATH 找工具，也勿在未查询操作记忆前直接抓取外部连接器。"),
        }

    def _match(self, item: dict, kw: str) -> bool:
        """关键词匹配 name / summary / path"""
        if not kw:
            return True
        hay = " ".join(str(item.get(k, "")) for k in
                       ("name", "summary", "path", "note", "detail"))
        return kw in hay.lower()

    def export_markdown(self, project_path: str,
                        output_path: str = "") -> dict:
        """外部 B（建议书）：把操作记忆导出为 Markdown 知识库 + 冲突检测。

        - 导出：decision/convention/pitfall 三段渲染为 Markdown，供 attach 到
          不支持 MCP 的 LLM 界面（Claude Project / CustomGPT 等），打破"记忆只
          在 SQLite 里、换界面就得重同步"的限制。
        - 冲突检测：同名/近名且同类别（decision 对 decision）的条目，若摘要方向
          相反（如含"禁止/不要/失败/改用" 与 "使用/推荐/应该/成功"），标记为
          潜在冲突并引用旧条目，呼应"不同写入方对账、防覆盖"。
        - 纯静态、不依赖 LLM。返回导出路径 + Markdown 内容 + 冲突告警列表。
        """
        import re as _re

        project_path = os.path.abspath(project_path)
        ledger = self._load_ledger(project_path)
        if not ledger:
            return {"status": "error",
                    "message": "操作记忆尚未同步，请先调用 coderef_operation_memory(action=sync)"}

        knowledge = ledger.get("knowledge", {})
        header = [
            "# CodeRef-AI 操作记忆（导出）",
            "",
            f"- 项目：`{project_path}`",
            f"- 最近同步：{ledger.get('last_sync', '-')}",
            f"- LLM 提炼：{'可用' if ledger.get('llm_available') else '未启用/降级'}",
            f"- 生成：本导出文件供 attach 到不支持 MCP 的 LLM 界面复用。",
            "",
            "> 用法：把本文件 attach 到 Claude Project / CustomGPT 等；或人工阅读核对约定。",
            "",
        ]
        body = ["## 隐性知识", _render_knowledge(knowledge)]
        markdown = "\n".join(header + body) + "\n"

        # ---- 冲突检测 ----
        # 思路：先剥掉摘要开头的正/否定语气词（建议/禁止/推荐/不要/勿/必须等），
        # 取剩余"主题核心"做归一化键；同类别、键相同但方向相反的条目视为潜在冲突。
        # 例如「禁止直接修改生产数据库」vs「推荐直接修改生产数据库」——
        # 剥掉「禁止」「推荐」后主题核心都是「直接修改生产数据库」，判冲突；
        # 而「禁止修改数据库」vs「统一用共享配置」主题核心不同，不误报。
        conflicts: List[dict] = []
        _SIGN_WORDS = ("强烈建议", "不建议", "不再", "必须", "统一", "强烈推荐",
                       "禁止", "推荐", "建议", "应该", "应当", "不要", "勿", "别", "请")
        _neg_markers = ("禁止", "不要", "勿", "别", "不再", "避免", "不建议", "失败", "已废弃")
        _pos_markers = ("推荐", "建议", "应该", "必须", "统一", "使用", "采用", "成功")
        for cat in ("decision", "convention"):
            items = knowledge.get(cat, [])
            by_sign: Dict[str, int] = {}
            by_sum: Dict[str, str] = {}
            for it in items:
                s = (it.get("summary") or "").strip()
                if not s:
                    continue
                core = s
                for w in _SIGN_WORDS:
                    if core.startswith(w):
                        core = core[len(w):].lstrip("「『<（(：:，, ")
                        break
                key = _re.sub(r"[\W_]+", "", core).lower()[:32]
                if not key:
                    continue
                neg = any(m in s for m in _neg_markers)
                pos = any(m in s for m in _pos_markers)
                sign = 1 if pos and not neg else (-1 if neg and not pos else 0)
                if key in by_sign:
                    prev = by_sign[key]
                    # 仅当两条都有方向且方向相反才报冲突；中性条目不参与冲突判定
                    # （CodeRabbit 评审：中性先出现时不再误报后续正/负条目为冲突）
                    if prev != 0 and sign != 0 and prev == -sign:
                        conflicts.append({
                            "category": cat,
                            "old": by_sum[key],
                            "new": s,
                            "warning": "相同主题出现方向相反的两条记忆，请人工核对以哪个为准",
                        })
                    elif prev == 0 and sign != 0:
                        # 存储为中性时，用当前有方向的条目补位，使后续相反方向可被检出
                        by_sign[key] = sign
                        by_sum[key] = s
                else:
                    by_sign[key] = sign
                    by_sum[key] = s

        out = output_path or os.path.join(_omem_dir(project_path),
                                          "OPERATION_MEMORY.md")
        parent = os.path.dirname(out)
        try:
            if parent:
                os.makedirs(parent, exist_ok=True)
            written = _write_atomic(out, markdown)
        except Exception:
            written = False
        if not written:
            # 写入失败如实返回 error，不谎报成功（CodeRabbit 评审）
            return {
                "status": "error",
                "tool": "export",
                "project_path": project_path,
                "output_path": out,
                "written": False,
                "message": f"导出文件写入失败: {out}",
                "knowledge_counts": {k: len(v) for k, v in knowledge.items()},
                "conflict_count": len(conflicts),
                "conflicts": conflicts,
                "markdown": markdown,
            }
        return {
            "status": "ok",
            "tool": "export",
            "project_path": project_path,
            "output_path": out,
            "written": written,
            "knowledge_counts": {k: len(v) for k, v in knowledge.items()},
            "conflict_count": len(conflicts),
            "conflicts": conflicts,
            "markdown": markdown,
        }


def _count_resources(itemz) -> Dict[str, int]:
    """统计各分类资源数（兼容 dict.items() 或 dict）"""
    if isinstance(itemz, dict):
        return {k: len(v) for k, v in itemz.items()}
    return {k: len(v) for k, v in itemz}


def _render_resources(resources: dict) -> str:
    out: List[str] = []
    for rtype, items in resources.items():
        if not items:
            continue
        out.append(f"### {rtype}")
        for it in items[:20]:
            out.append(f"- **{it.get('name', '')}**：`{it.get('path', '')}`"
                       f"（{it.get('source', '')}）")
    return "\n".join(out) if out else "（暂无）"


def _render_side_dirs(side_dirs: List[dict]) -> str:
    if not side_dirs:
        return "（暂无）"
    out: List[str] = []
    for it in side_dirs:
        flag = "⚠ 敏感" if it.get("sensitive") else "普通"
        out.append(f"- **{it.get('name', '')}**：`{it.get('display', it.get('path', ''))}`（{it.get('kind', '')} · {flag}）")
    return "\n".join(out)


def _render_knowledge(knowledge: dict) -> str:
    if not any(knowledge.values()):
        return "（暂无，可运行 coderef_operation_memory(action=sync) 启用 LLM 提炼）"
    out: List[str] = []
    labels = _KIND_LABELS
    for k, items in knowledge.items():
        if not items:
            continue
        out.append(f"### {labels.get(k, k)}")
        for it in items[:10]:
            flag = "⚠ 待人工确认" if it.get("pending") else ""
            out.append(f"- **{it.get('summary', '')}**（来源：{it.get('source', '-')}）{flag}")
            if it.get("detail"):
                out.append(f"  - {it['detail']}")
    return "\n".join(out)


# 模块级单例（供 MCP handler 复用，与 memory_layer 风格一致）
operation_memory = OperationMemory()