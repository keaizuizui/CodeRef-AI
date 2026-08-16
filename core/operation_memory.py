# -*- coding: utf-8 -*-
"""
OperationMemory v1.0 —— AI 辅助编程的操作记忆层

为 MCP 工具 coderef_operation_memory_sync / query / find / status
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
ENV_TOOL_ROOTS = settings.OMEM_ENV_TOOL_ROOTS
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


# 原子写并发重试的常量（集中管理，不散落 magic number）
_ATOMIC_WRITE_MAX_RETRIES = 5
_ATOMIC_WRITE_RETRY_DELAY = 0.1  # 秒

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

    Windows 上 os.replace 覆盖一个正处于打开状态的目标文件会触发
    WinError 5/32（拒绝访问 / 文件被占用），因此在替换失败时按模块级
    常量做有限次重试 + 短暂退避，尽量规避并发写的不稳定。

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
                os.replace(tmp, path)
                return True
            except OSError as e:
                if attempt < _ATOMIC_WRITE_MAX_RETRIES - 1:
                    time.sleep(_ATOMIC_WRITE_RETRY_DELAY)
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
        pass
    # 2. 常见便携根探测（支持 glob 通配）
    for root_pat in ENV_TOOL_ROOTS:
        try:
            roots = glob.glob(os.path.expanduser(root_pat))
        except Exception:
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
    """从依赖清单内容提取简短的版本 / 来源摘要"""
    if not content:
        return "（空清单）"
    lines = [l.strip() for l in content.splitlines()
             if l.strip() and not l.strip().startswith(("#", "//"))]
    count = len(lines)
    head = lines[0] if lines else ""
    return f"约 {count} 项依赖；首项：{head[:60]}"


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
            # 抓 dependencies / devDependencies 的键名
            names = re.findall(r'"(?!"(?:dependencies|devDependencies|peerDependencies|optionalDependencies)"":)\s*"([A-Za-z0-9@._/-]+)"\s*:', content)
            if not names:
                names = re.findall(r'"([A-Za-z0-9@._/-]+)"\s*:\s*"', content)
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
                              "请人工补充，或配置 LLM 后重跑 coderef_operation_memory_sync。",
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
            prompt = _EXTRACT_PROMPT.format(source=chunk)
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
                    "resources": prev["resources"],
                    "side_dirs": prev["side_dirs"],
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
        if with_llm:
            extractor = KnowledgeExtractor(with_llm=True)
            llm_available = extractor.llm_available()
            sources = self._collect_knowledge_sources(project_path, resources)
            knowledge = extractor.extract(sources)

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
        except Exception:
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
            return {"status": "error", "message": "操作记忆尚未同步，请先调用 coderef_operation_memory_sync",
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
            return {"status": "error", "message": "操作记忆尚未同步，请先调用 coderef_operation_memory_sync",
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
                    "message": "操作记忆尚未同步，请先调用 coderef_operation_memory_sync"}

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

    def _match(self, item: dict, kw: str) -> bool:
        """关键词匹配 name / summary / path"""
        if not kw:
            return True
        hay = " ".join(str(item.get(k, "")) for k in
                       ("name", "summary", "path", "note", "detail"))
        return kw in hay.lower()


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
        return "（暂无，可运行 coderef_operation_memory_sync 启用 LLM 提炼）"
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