# -*- coding: utf-8 -*-
"""CodeRef 梦（Dream, 5.20.x）—— 编程 AI 的离线巩固层。

定位：主 AI 醒着做事，CodeRef 在安静时替它补梦。梦只写
`<project>/.coderef/dream/`，不改任何确定性结论；LLM 只负责「提议」，
所有证据引用都经过本地确定性核验，核验失败直接 rejected。

LLM 配置：5.20.1 起不再有 dream 独立 Key/预算，统一复用 CodeRef 主 LLM 配置
（`core.llm_integration.LLMIntegration`，即 CODEREF_API_KEY / config.json 的
llm_* 与主 LLM 调用预算）。`dream` 段只保留行为开关（是否启用、是否启动补梦、
笔记上限、TTL、最小间隔）。

公开 API：
  - status(project_path) -> dict          最近做梦时间 / 笔记数 / 主 LLM 状态 / 上次错误
  - seed(project_path, text, ...) -> dict 主 AI 显式投喂一条显著事件
  - read(project_path, ...) -> dict       读 DREAM.md / 按 job、kind 或关键词取笔记
  - run(project_path, trigger=..., job=...) -> dict  手动或启动时补一轮梦
      job ∈ pitfall（默认）/ recap / routing / triage（记忆类）
            / change_narrative / failure_postmortem（叙事类，写 notes）
            / review_draft / refactor_draft / asset_candidate / noise_proposal（草稿类，只写 drafts/）
            / false_memory_check（维护类，确定性复核证据引用，不需要 LLM）
            / eval_replay（维护类，默认确定性指标，可选 LLM 摘要，写 evals/）
  - list_drafts(project_path, ...) -> dict 读草稿（action=drafts）
  - list_evals(project_path, ...) -> dict  读评估（action=evals）
  - trigger_if_due(project_path) -> bool  进程内一次启动补梦（后台线程）
  - digest_items(project_path) -> list    供 memory_digest 优先注入的梦条目

诚实边界：
  - 主 LLM 无 Key / 调用失败 → status="SKIP" 或 error，不产出占位内容；
  - 梦是假设，DREAM.md 只收「证据引用可核验」的 verified 笔记，并明确标注
    「引用前先核验」，不得当成审计结论转述；
  - 做梦失败保留旧梦，只记录 last_error，不覆盖既有笔记。

作者: CodeRef-AI Team
版本: v1.1
"""

import contextlib
import json
import os
import re
import subprocess
import threading
import time
import uuid
from collections import Counter
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from loguru import logger


# ── 存储布局（项目级、本地） ────────────────────────────────────
DREAM_DIR = os.path.join(".coderef", "dream")
STATE_FILE = "state.json"
DREAM_MD = "DREAM.md"
SEEDS_FILE = "seeds.jsonl"
NOTES_FILE = "notes.json"
RUNS_DIR = "runs"
ARCHIVE_DIR = "archive"
DRAFTS_DIR = "drafts"
EVALS_DIR = "evals"
SCHEMA_VERSION = 1

# ── 梦的形态边界 ────────────────────────────────────────────────
# 记忆类：巩固成 notes.json / DREAM.md
JOBS = ("pitfall", "recap", "routing", "triage")
# 叙事类：P1，同样写 notes.json / DREAM.md（fact 可 verified，advice 永远假设）
NOTE_JOBS = JOBS + ("change_narrative", "failure_postmortem")
# 草稿类：P2，只写 drafts/，必须用户确认，不进入 notes / DREAM.md
DRAFT_JOBS = ("review_draft", "refactor_draft", "asset_candidate",
              "noise_proposal")
# 维护类：P3，手动触发。false_memory_check 纯确定性（不需要主 LLM）；
# eval_replay 默认确定性指标，有 LLM 时追加语义摘要。
MAINT_JOBS = ("false_memory_check", "eval_replay")
ALL_JOBS = NOTE_JOBS + DRAFT_JOBS + MAINT_JOBS
DEFAULT_JOB = "pitfall"

# 草稿类 job → 后续确定性闸门（服务端固定，忽略 LLM 返回的 next_tools）
DRAFT_NEXT_TOOLS = {
    "review_draft": ["coderef_review", "coderef_verify_findings"],
    "refactor_draft": ["coderef_arch_verify", "coderef_target_adopt"],
    "asset_candidate": ["coderef_asset", "coderef_registry"],
    "noise_proposal": ["coderef_scope", "coderef_whitelist"],
}
CLAIM_TYPES = ("fact", "advice")
KINDS = ("rule", "pitfall", "convention", "association", "context")
STATUSES = ("verified", "hypothesis", "rejected", "decayed")

# DREAM.md 上限 800 token；按 3 字符/token 粗略折算（与 memory_digest 同口径）
DREAM_MD_TOKEN_BUDGET = 800
CHARS_PER_TOKEN = 3
DREAM_MD_MAX_CHARS = DREAM_MD_TOKEN_BUDGET * CHARS_PER_TOKEN

MAX_NOTE_CHARS = 300
MAX_EVIDENCE = 8
MAX_SEEDS = 20
MAX_AUDIT_EVENTS = 30
MAX_DISPATCH_EVENTS = 10
MAX_GIT_COMMITS = 10
MAX_CHANGED_FILES = 20
AUDIT_DAYS = 7
GIT_TIMEOUT = 10
# 单次补梦的输入/输出硬上限（防止一次做梦把主 LLM 上下文撑爆；不是独立预算）
MAX_PROMPT_CHARS = 12000
MAX_OUTPUT_TOKENS = 2048

# 证据引用格式：kind:value（value 由本地确定性核验）
_EVIDENCE_RE = re.compile(r"^(file|git|commit|audit|seed|dispatch|trace):(.+)$")
_ID_RE = re.compile(r"^[0-9A-Za-z_-]{1,64}$")
_SHA_RE = re.compile(r"^[0-9a-fA-F]{4,40}$")
_AUDIT_TOOL_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,63}$")

# 笔记类别 → DREAM.md 分组标签
KIND_LABELS = {
    "rule": "规则",
    "convention": "约定",
    "pitfall": "坑",
    "association": "联想",
    "context": "上下文",
}

# 只保留行为开关；LLM 的 provider/api_key/model/预算全部复用主 LLM 配置。
# run_on_startup 默认关闭：复用主 LLM 后没有独立预算，自动补梦需用户显式开启，
# 避免在不知情时消耗主额度；手动 coderef_dream(action=run) 不受影响。
DEFAULT_CONFIG = {
    "enabled": True,
    "run_on_startup": False,
    "max_notes": 40,
    "ttl_days": 30,
    "min_interval_hours": 6,
}

# 进程内「本项目已尝试启动补梦」集合（一次启动一轮）
_started = set()
_start_lock = threading.Lock()

# 同一项目的 run/seed 串行化：notes.json 是整文件读改写，并发 run 会互相覆盖；
# seeds.jsonl 的多线程 append 也可能丢行。RLock 允许同线程内嵌套调用。
_run_locks: Dict[str, threading.RLock] = {}
_run_locks_guard = threading.Lock()


def _project_lock(pp: str) -> threading.RLock:
    key = os.path.normcase(os.path.abspath(pp))
    with _run_locks_guard:
        lock = _run_locks.get(key)
        if lock is None:
            lock = threading.RLock()
            _run_locks[key] = lock
        return lock


# 跨进程锁（U-67）：进程内 RLock 管不到多进程，seeds.jsonl / notes.json 的
# 读改写需要文件锁兜底。同线程可重入：run 整体持锁时 _save_notes 再取不自杀。
_lock_state = threading.local()
_LOCK_FILE = ".lock"
_SEEDS_LOCK_FILE = ".lock.seeds"
_LOCK_TIMEOUT = 120.0


def _os_lock(fh, unlock: bool = False) -> None:
    """对锁文件加/解非阻塞独占锁，带超时轮询（Windows/POSIX 通用）。"""
    if unlock:
        if os.name == "nt":
            import msvcrt
            fh.seek(0)
            msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
        return
    deadline = time.time() + _LOCK_TIMEOUT
    while True:
        try:
            if os.name == "nt":
                import msvcrt
                fh.seek(0)
                msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            return
        except OSError:
            if time.time() >= deadline:
                raise
            time.sleep(0.05)


@contextlib.contextmanager
def _file_lock(project_path: str, name: str = _LOCK_FILE):
    """项目级跨进程文件锁（同线程可重入）。

    name 用于按资源分锁：seeds.jsonl 的追加用独立锁，避免被「正在跑 LLM 的
    整轮梦」长时间挡住（Brooks 复审：run 的长事务不应阻塞轻量 seed）。
    """
    key = _norm(project_path) + "|" + name
    held = getattr(_lock_state, "held", None)
    if held is None:
        held = _lock_state.held = {}
    if held.get(key, 0) > 0:
        held[key] += 1
        try:
            yield
        finally:
            held[key] -= 1
        return
    path = os.path.join(_dream_dir(project_path), name)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fh = open(path, "a+b")
    try:
        fh.seek(0, os.SEEK_END)
        if fh.tell() == 0:   # Windows 锁定需要文件里有字节
            fh.write(b"0")
            fh.flush()
        _os_lock(fh)
        held[key] = 1
        try:
            yield
        finally:
            held[key] = 0
            _os_lock(fh, unlock=True)
    finally:
        fh.close()


# ═══════════════════════════════════════════════════════════════
# 基础工具
# ═══════════════════════════════════════════════════════════════

def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _norm(project_path: str) -> str:
    try:
        return os.path.normcase(os.path.abspath(str(project_path)))
    except Exception:  # noqa: BLE001
        return str(project_path)


def _dream_dir(project_path: str) -> str:
    return os.path.join(os.path.abspath(project_path), DREAM_DIR)


def _path(project_path: str, name: str) -> str:
    return os.path.join(_dream_dir(project_path), name)


def _read_text(path: str) -> str:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    except Exception:  # noqa: BLE001
        return ""


def _read_json(path: str, default: Any) -> Any:
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, type(default)) else default
    except Exception:  # noqa: BLE001
        return default


def _write_json(path: str, data: Any) -> str:
    """原子写 JSON；返回空串表示成功，否则返回错误文本。"""
    tmp = path + ".tmp"
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
        return ""
    except Exception as e:  # noqa: BLE001
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except OSError:
            pass
        return str(e)


def _write_text(path: str, text: str) -> str:
    tmp = path + ".tmp"
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(tmp, path)
        return ""
    except Exception as e:  # noqa: BLE001
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except OSError:
            pass
        return str(e)


def _as_bool(value, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    text = str(value).strip().lower()
    if text in ("1", "true", "yes", "on"):
        return True
    if text in ("0", "false", "no", "off"):
        return False
    return default


def _as_int(value, default: int, minimum: int = 0) -> int:
    try:
        return max(minimum, int(value))
    except (TypeError, ValueError):
        return default


def _clean_text(value) -> str:
    return " ".join(str(value or "").split())


def _norm_text(value) -> str:
    return re.sub(r"\s+", "", str(value or "")).lower()


def _git(args: List[str], cwd: str) -> Tuple[int, str, str]:
    """执行 git 命令，返回 (rc, stdout, stderr)；异常统一 rc=-1。"""
    try:
        r = subprocess.run(
            ["git"] + list(args), cwd=cwd, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=GIT_TIMEOUT,
            stdin=subprocess.DEVNULL,
        )
        return r.returncode, (r.stdout or "").strip(), (r.stderr or "").strip()
    except Exception as e:  # noqa: BLE001 —— git 不可用统一降级
        logger.debug(f"[dream] git 执行失败: {args} → {e}")
        return -1, "", str(e)


# ═══════════════════════════════════════════════════════════════
# 配置：dream 段只保留行为开关，LLM 统一复用主配置
# ═══════════════════════════════════════════════════════════════

def _config_candidates() -> List[str]:
    return [
        os.environ.get("CODEREF_CONFIG_PATH", ""),
        os.path.join(os.path.expanduser("~"), ".coderef", "config.json"),
        os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                     "config", "config.json"),
        os.path.join(os.getcwd(), "config", "config.json"),
    ]


def _load_config_section() -> Tuple[dict, str]:
    """读取 dream 段（行为开关）；返回 (section, 配置路径)。"""
    for path in [p for p in _config_candidates() if p]:
        if not os.path.exists(path):
            continue
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:  # noqa: BLE001
            logger.debug(f"[dream] 配置读取失败 {path}: {e}")
            continue
        if not isinstance(data, dict):
            continue
        section = data.get("dream")
        if isinstance(section, dict):
            return dict(section), path
    return {}, ""


def load_config(project_path: str = "") -> dict:
    """合并默认值与 dream 段行为开关（LLM 配置不在此处读取）。"""
    section, path = _load_config_section()
    cfg = dict(DEFAULT_CONFIG)
    for key in cfg:
        if key in section and section[key] is not None:
            cfg[key] = section[key]
    cfg["enabled"] = _as_bool(cfg.get("enabled"), True)
    cfg["run_on_startup"] = _as_bool(cfg.get("run_on_startup"), False)
    cfg["max_notes"] = _as_int(cfg.get("max_notes"), 40, minimum=1)
    cfg["ttl_days"] = _as_int(cfg.get("ttl_days"), 30, minimum=1)
    cfg["min_interval_hours"] = _as_int(
        cfg.get("min_interval_hours"), 6, minimum=0)
    cfg["config_source"] = path or "（未找到 dream 段：默认不自动补梦）"
    return cfg


def _build_llm():
    """复用主 LLM 配置构造客户端（延迟 import，缺依赖时返回 None）。

    与其他 LLM 工具完全同源：CODEREF_API_KEY / config.json 的 llm_*、
    同一套默认 provider/model、同一套调用预算与输出上限。
    """
    try:
        from core.llm_integration import LLMIntegration
        return LLMIntegration()
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[dream] LLM 初始化失败: {e}")
        return None


def _llm_available(llm) -> bool:
    try:
        return bool(llm is not None and llm.is_available())
    except Exception:  # noqa: BLE001
        return False


# ═══════════════════════════════════════════════════════════════
# 状态 / 笔记 / seed
# ═══════════════════════════════════════════════════════════════

def _default_state() -> dict:
    return {
        "version": SCHEMA_VERSION,
        "last_dream_at": "",
        "last_attempt_at": "",
        "last_status": "",
        "last_error": "",
        "last_job": "",
        "goal": "",
        "open_questions": [],
        "cursor": {"audit_after": "", "seeds_consumed": 0, "git_head": ""},
        "last_run_tokens": 0,
        "runs": 0,
    }


def _load_state(project_path: str) -> dict:
    state = _read_json(_path(project_path, STATE_FILE), {})
    out = _default_state()
    if isinstance(state, dict):
        for key in out:
            if key in state:
                out[key] = state[key]
        cursor = state.get("cursor")
        if isinstance(cursor, dict):
            out["cursor"].update(cursor)
    if not isinstance(out.get("open_questions"), list):
        out["open_questions"] = []
    return out


def _save_state(project_path: str, state: dict) -> str:
    return _write_json(_path(project_path, STATE_FILE), state)


def _load_notes(project_path: str) -> List[dict]:
    data = _read_json(_path(project_path, NOTES_FILE), {})
    notes = data.get("notes") if isinstance(data, dict) else data
    if not isinstance(notes, list):
        return []
    out = []
    for n in notes:
        if not isinstance(n, dict):
            continue
        # 旧笔记兼容：缺 job / claim_type 时按 pitfall / fact 处理
        if not str(n.get("job") or "").strip():
            n["job"] = DEFAULT_JOB
        claim_type = str(n.get("claim_type") or "").strip().lower()
        if claim_type not in CLAIM_TYPES:
            n["claim_type"] = "fact"
        # advice 永远不是事实：历史数据即使写成 verified 也按 hypothesis 读
        if n.get("claim_type") == "advice" and n.get("status") == "verified":
            n["status"] = "hypothesis"
        out.append(n)
    return out


_notes_lock = threading.Lock()
_save_base = threading.local()


def _save_notes(project_path: str, notes: List[dict]) -> str:
    """写 notes.json。

    run 会在保存前通过 `_save_base.ids` 告知本轮读到的基线 id；写入时在锁内
    重读磁盘，保留并发 run 新增的笔记，避免「后写覆盖前写」。没有基线时保持
    原有的整文件替换语义（供测试/维护路径直接调用）。
    """
    base = getattr(_save_base, "ids", None)
    if base is None:
        with _file_lock(project_path):
            return _write_json(_path(project_path, NOTES_FILE), {
                "version": SCHEMA_VERSION,
                "updated_at": _now(),
                "notes": notes,
            })
    with _notes_lock, _file_lock(project_path):
        current = _load_notes(project_path)
        extra = [n for n in current if str(n.get("id") or "") not in base]
        merged: Dict[str, dict] = {str(n.get("id") or ""): n for n in extra}
        for n in notes:
            merged[str(n.get("id") or "")] = n
        final = list(merged.values())
        return _write_json(_path(project_path, NOTES_FILE), {
            "version": SCHEMA_VERSION,
            "updated_at": _now(),
            "notes": final,
        })


def _read_seeds(project_path: str) -> List[dict]:
    path = _path(project_path, SEEDS_FILE)
    out: List[dict] = []
    if not os.path.exists(path):
        return out
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    item = json.loads(line)
                except Exception:  # noqa: BLE001
                    continue
                if isinstance(item, dict) and item.get("text"):
                    out.append(item)
    except Exception as e:  # noqa: BLE001
        logger.debug(f"[dream] seed 读取失败: {e}")
    return out


def seed(project_path: str, text: str, kind: str = "association",
         source: str = "main_ai") -> dict:
    """主 AI 显式投喂一条显著事件（不读取主 AI 全量对话）。"""
    pp = os.path.abspath(project_path)
    if not os.path.isdir(pp):
        return {"status": "error", "tool": "coderef_dream", "action": "seed",
                "error": f"项目目录不存在: {pp}"}
    try:
        # 只取 seeds 专用锁：run 可能正持主锁跑多分钟 LLM，不该拦住一条投喂。
        with _project_lock(pp), _file_lock(pp, _SEEDS_LOCK_FILE):
            return _seed_impl(pp, text, kind, source)
    except OSError as e:   # 锁超时或文件系统错误：结构化返回，不抛给协议层
        return {"status": "error", "tool": "coderef_dream", "action": "seed",
                "error": f"dream 锁被占用或不可用，请稍后重试: {e}"}


def _seed_impl(pp: str, text: str, kind: str = "association",
               source: str = "main_ai") -> dict:
    """seed 的实际写入（调用方已持有项目锁）。"""
    if not os.path.isdir(pp):
        return {"status": "error", "tool": "coderef_dream", "action": "seed",
                "error": f"项目目录不存在: {pp}"}
    body = _clean_text(text)[:2000]
    if not body:
        return {"status": "error", "tool": "coderef_dream", "action": "seed",
                "error": "text 不能为空"}
    kind = str(kind or "association").strip().lower()
    if kind not in KINDS:
        return {"status": "error", "tool": "coderef_dream", "action": "seed",
                "error": f"非法 kind: {kind!r}；允许 {'/'.join(KINDS)}"}
    item = {"id": "seed-" + uuid.uuid4().hex[:8], "ts": _now(),
            "kind": kind, "text": body, "source": _clean_text(source)[:120]}
    path = _path(pp, SEEDS_FILE)
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(item, ensure_ascii=False) + "\n")
    except Exception as e:  # noqa: BLE001
        return {"status": "error", "tool": "coderef_dream", "action": "seed",
                "error": f"seed 写入失败: {e}"}
    return {"status": "ok", "tool": "coderef_dream", "action": "seed",
            "seed": item, "seeds_total": len(_read_seeds(pp)),
            "note": "已入梦料；下次补梦时才会被巩固"}


def _draft_files(project_path: str, job: str = "") -> List[str]:
    """drafts/ 下的草稿文件（不含 .tmp）；job 非空时按 `<job>-` 前缀过滤。"""
    base = _path(project_path, DRAFTS_DIR)
    if not os.path.isdir(base):
        return []
    prefix = (job + "-") if job else ""
    try:
        names = os.listdir(base)
    except OSError:
        return []
    out = []
    for name in names:
        if not name.endswith(".json") or name.endswith(".json.tmp"):
            continue
        if prefix and not name.startswith(prefix):
            continue
        out.append(os.path.join(base, name))
    return out


def _draft_summary(path: str) -> Optional[dict]:
    data = _read_json(path, {})
    if not isinstance(data, dict):
        return None
    draft_id = os.path.basename(path)[:-5]
    return {
        "draft_id": draft_id,
        "job": str(data.get("job") or draft_id.split("-")[0]),
        "generated_at": str(data.get("generated_at") or ""),
        "items": len(data.get("items") or []),
        "rejected": len(data.get("rejected") or []),
        "requires_confirmation": bool(data.get("requires_confirmation")),
        "next_tools": list(data.get("next_tools") or []),
        "path": path,
    }


def _draft_summaries(project_path: str, job: str = "") -> List[dict]:
    paths = _draft_files(project_path, job)

    def _mtime(path: str) -> float:
        try:
            return os.path.getmtime(path)
        except OSError:
            return 0.0

    out = []
    for path in sorted(paths, key=_mtime, reverse=True):
        summary = _draft_summary(path)
        if summary:
            out.append(summary)
    return out


def _eval_files(project_path: str) -> List[str]:
    """evals/ 下的评估文件（不含 .tmp）。"""
    base = _path(project_path, EVALS_DIR)
    if not os.path.isdir(base):
        return []
    try:
        names = os.listdir(base)
    except OSError:
        return []
    return [os.path.join(base, n) for n in names
            if n.endswith(".json") and not n.endswith(".json.tmp")]


def _mtime(path: str) -> float:
    try:
        return os.path.getmtime(path)
    except OSError:
        return 0.0


def _eval_summaries(project_path: str) -> List[dict]:
    out = []
    for path in sorted(_eval_files(project_path), key=_mtime, reverse=True):
        data = _read_json(path, {})
        if not isinstance(data, dict) or not data:
            continue
        out.append({
            "eval_id": os.path.basename(path)[:-5],
            "generated_at": str(data.get("generated_at") or ""),
            "llm_available": bool(data.get("llm_available")),
            "metrics": data.get("metrics") or {},
            "summary": str(data.get("summary") or ""),
            "path": path,
        })
    return out


def status(project_path: str) -> dict:
    """梦的诚实状态：时间、笔记数、主 LLM 状态、上次错误。"""
    pp = os.path.abspath(project_path)
    cfg = load_config(pp)
    state = _load_state(pp)
    notes = _load_notes(pp)
    md_path = _path(pp, DREAM_MD)
    md = _read_text(md_path)
    by_status = Counter(str(n.get("status") or "unknown") for n in notes)
    by_kind = Counter(str(n.get("kind") or "unknown") for n in notes)
    by_job = Counter(str(n.get("job") or DEFAULT_JOB) for n in notes)
    llm = _build_llm()
    llm_cfg = getattr(llm, "config", None)
    provider = getattr(llm_cfg, "provider", None)
    seeds = _read_seeds(pp)
    drafts = _draft_summaries(pp)
    draft_by_job = Counter(str(d.get("job") or "unknown") for d in drafts)
    evals = _eval_summaries(pp)
    return {
        "status": "ok",
        "tool": "coderef_dream",
        "action": "status",
        "project_path": pp,
        "enabled": cfg["enabled"],
        "run_on_startup": cfg["run_on_startup"],
        "llm": {
            "available": _llm_available(llm),
            "provider": getattr(provider, "value", str(provider or "")),
            "model": str(getattr(llm_cfg, "model", "") or ""),
            "source": "主 LLM 配置（与其他 LLM 工具同源）",
            "call_budget": int(getattr(llm, "call_budget", 0) or 0),
            "output_cap_chars": int(getattr(llm, "output_cap_chars", 0) or 0),
            "note": "复用主 LLM Key/预算；无有效 Key 时补梦 SKIP，不编造",
        },
        "last_dream_at": state.get("last_dream_at", ""),
        "last_attempt_at": state.get("last_attempt_at", ""),
        "last_status": state.get("last_status", ""),
        "last_error": state.get("last_error", ""),
        "last_job": state.get("last_job", ""),
        "jobs": list(JOBS),
        "note_jobs": list(NOTE_JOBS),
        "draft_jobs": list(DRAFT_JOBS),
        "maintenance_jobs": list(MAINT_JOBS),
        "default_job": DEFAULT_JOB,
        "due": _is_due(state, cfg),
        "notes": {
            "total": len(notes),
            "by_status": dict(by_status),
            "by_kind": dict(by_kind),
            "by_job": dict(by_job),
        },
        "drafts": {
            "total": len(drafts),
            "by_job": dict(draft_by_job),
            "last_at": drafts[0].get("generated_at", "") if drafts else "",
        },
        "evals": {
            "total": len(evals),
            "last_at": evals[0].get("generated_at", "") if evals else "",
        },
        "seeds": {"total": len(seeds),
                  "pending": max(0, len(seeds) - int(
                      (state.get("cursor") or {}).get("seeds_consumed") or 0))},
        "dream_md": {
            "exists": bool(md),
            "path": md_path,
            "chars": len(md),
            "token_estimate": round(len(md) / CHARS_PER_TOKEN),
            "token_budget": DREAM_MD_TOKEN_BUDGET,
        },
        "limits": {
            "max_prompt_chars": MAX_PROMPT_CHARS,
            "max_output_tokens": MAX_OUTPUT_TOKENS,
            "last_run_tokens": state.get("last_run_tokens", 0),
            "note": "单次补梦的上下文/输出上限，不是独立预算",
        },
        "behavior_config": cfg["config_source"],
        "runs": state.get("runs", 0),
        "guardrail": "梦是假设，不是审计结论；引用前先核验 evidence",
    }


def _is_due(state: dict, cfg: dict) -> bool:
    last = str(state.get("last_dream_at") or "")
    if not last:
        return True
    try:
        last_dt = datetime.fromisoformat(last)
    except (TypeError, ValueError):
        return True
    return datetime.now() - last_dt >= timedelta(hours=cfg["min_interval_hours"])


def _mark_hit(project_path: str, note_ids: List[str]) -> None:
    if not note_ids:
        return
    wanted = set(note_ids)
    notes = _load_notes(project_path)
    changed = False
    now = _now()
    for n in notes:
        if n.get("id") in wanted:
            n["last_hit_at"] = now
            changed = True
    if changed:
        _save_notes(project_path, notes)


def read(project_path: str, kind: str = "", keyword: str = "",
         include_hypothesis: bool = False, limit: int = 20,
         mark_hit: bool = True, job: str = "") -> dict:
    """读 DREAM.md 或按 job/kind/关键词取笔记；默认只返回 verified。"""
    pp = os.path.abspath(project_path)
    kind = str(kind or "").strip().lower()
    job = str(job or "").strip().lower()
    keyword = str(keyword or "").strip().lower()
    try:
        limit = max(1, min(100, int(limit)))
    except (TypeError, ValueError):
        limit = 20

    allowed = {"verified"}
    if include_hypothesis:
        allowed.add("hypothesis")
    notes = []
    for n in _load_notes(pp):
        if str(n.get("status")) not in allowed:
            continue
        if kind and str(n.get("kind")) != kind:
            continue
        if job and str(n.get("job") or DEFAULT_JOB) != job:
            continue
        haystack = (str(n.get("text") or "") + " "
                    + " ".join(str(e) for e in (n.get("evidence") or []))).lower()
        if keyword and keyword not in haystack:
            continue
        notes.append({
            "id": n.get("id", ""),
            "kind": n.get("kind", ""),
            "job": n.get("job", DEFAULT_JOB),
            "claim_type": n.get("claim_type", "fact"),
            "text": n.get("text", ""),
            "evidence": n.get("evidence") or [],
            "confidence": n.get("confidence", 0),
            "status": n.get("status", ""),
            "created_at": n.get("created_at", ""),
            "last_hit_at": n.get("last_hit_at", ""),
        })
    notes = notes[-limit:][::-1]
    if mark_hit:
        _mark_hit(pp, [n["id"] for n in notes if n["id"]])
    md_path = _path(pp, DREAM_MD)
    return {
        "status": "ok",
        "tool": "coderef_dream",
        "action": "read",
        "project_path": pp,
        "dream_md": _read_text(md_path),
        "dream_md_path": md_path,
        "notes": notes,
        "filter": {"kind": kind, "keyword": keyword, "job": job,
                   "include_hypothesis": bool(include_hypothesis)},
        "guardrail": "梦是假设；引用前先核验 evidence，不得当作审计结论转述",
    }


_DRAFT_ID_RE = re.compile(r"^[0-9A-Za-z_-]{1,120}$")


def list_drafts(project_path: str, job: str = "", limit: int = 10,
                draft_id: str = "") -> dict:
    """读草稿（action=drafts）：摘要列表或按 draft_id 读全文。

    草稿只存在于 `.coderef/dream/drafts/`，不进入 notes / DREAM.md。
    """
    pp = os.path.abspath(project_path)
    job = str(job or "").strip().lower()
    draft_id = str(draft_id or "").strip()
    try:
        limit = max(1, min(100, int(limit)))
    except (TypeError, ValueError):
        limit = 10

    if job and job not in DRAFT_JOBS:
        return {"status": "error", "tool": "coderef_dream", "action": "drafts",
                "error": (f"非法 draft job: {job!r}；"
                          f"支持 {'/'.join(DRAFT_JOBS)}")}

    if draft_id:
        if not _DRAFT_ID_RE.fullmatch(draft_id):
            return {"status": "error", "tool": "coderef_dream",
                    "action": "drafts",
                    "error": "非法 draft_id：只允许 [0-9A-Za-z_-]，最长 120"}
        path = _path(pp, os.path.join(DRAFTS_DIR, draft_id + ".json"))
        if not os.path.isfile(path):
            return {"status": "error", "tool": "coderef_dream",
                    "action": "drafts", "draft_id": draft_id,
                    "error": f"草稿不存在: {draft_id}"}
        data = _read_json(path, {})
        if not isinstance(data, dict) or not data:
            return {"status": "error", "tool": "coderef_dream",
                    "action": "drafts", "draft_id": draft_id,
                    "error": "草稿内容不是合法 JSON 对象"}
        return {"status": "ok", "tool": "coderef_dream", "action": "drafts",
                "project_path": pp, "available": True, "draft_id": draft_id,
                "draft": data, "drafts": [data]}

    drafts = _draft_summaries(pp, job)[:limit]
    return {"status": "ok", "tool": "coderef_dream", "action": "drafts",
            "project_path": pp, "available": bool(drafts),
            "filter": {"job": job}, "drafts": drafts,
            "guardrail": ("草稿是建议不是结论；应用前必须用户确认，"
                          "并走 next_tools 里的确定性工具")}


_EVAL_ID_RE = re.compile(r"^[0-9A-Za-z_-]{1,120}$")


def list_evals(project_path: str, limit: int = 10,
               eval_id: str = "") -> dict:
    """读评估（action=evals）：摘要列表或按 eval_id 读全文。

    评估只存在于 `.coderef/dream/evals/`；指标来自落盘 runs/notes/archive，
    不进入 notes / DREAM.md，也不注入 digest。
    """
    pp = os.path.abspath(project_path)
    eval_id = str(eval_id or "").strip()
    try:
        limit = max(1, min(100, int(limit)))
    except (TypeError, ValueError):
        limit = 10

    if eval_id:
        if not _EVAL_ID_RE.fullmatch(eval_id):
            return {"status": "error", "tool": "coderef_dream",
                    "action": "evals",
                    "error": "非法 eval_id：只允许 [0-9A-Za-z_-]，最长 120"}
        path = _path(pp, os.path.join(EVALS_DIR, eval_id + ".json"))
        if not os.path.isfile(path):
            return {"status": "error", "tool": "coderef_dream",
                    "action": "evals", "eval_id": eval_id,
                    "error": f"评估不存在: {eval_id}"}
        data = _read_json(path, {})
        if not isinstance(data, dict) or not data:
            return {"status": "error", "tool": "coderef_dream",
                    "action": "evals", "eval_id": eval_id,
                    "error": "评估内容不是合法 JSON 对象"}
        return {"status": "ok", "tool": "coderef_dream", "action": "evals",
                "project_path": pp, "available": True, "eval_id": eval_id,
                "eval": data, "evals": [data]}

    evals = _eval_summaries(pp)[:limit]
    return {"status": "ok", "tool": "coderef_dream", "action": "evals",
            "project_path": pp, "available": bool(evals), "evals": evals,
            "guardrail": ("评估指标来自落盘 runs/notes/archive，不补推断；"
                          "LLM summary 是软判断，不是审计结论")}


# ═══════════════════════════════════════════════════════════════
# 原料收集与显著性筛选（零 LLM）
# ═══════════════════════════════════════════════════════════════

def _collect_audit(project_path: str, audit_after: str) -> Tuple[List[dict], List[dict]]:
    """返回 (全部审计条目, 显著事件)。显著 = blocked；成功流水账不喂给模型。"""
    try:
        from core import audit_log
        entries = audit_log.read(project_path, days=AUDIT_DAYS)
    except Exception as e:  # noqa: BLE001
        logger.debug(f"[dream] 审计日志读取失败: {e}")
        return [], []
    salient: List[dict] = []
    for e in entries:
        ts = str(e.get("ts") or "")
        if audit_after and ts and ts <= audit_after:
            continue
        blocked = e.get("blocked")
        if isinstance(blocked, dict) and blocked:
            salient.append({"ts": ts, "kind": "blocked",
                            "tool": str(e.get("tool") or ""),
                            "gate": str(blocked.get("gate") or ""),
                            "reason": str(blocked.get("reason") or "")[:200]})
    salient.sort(key=lambda x: x.get("ts", ""))
    return entries, salient[-MAX_AUDIT_EVENTS:]


def _collect_dispatch(project_path: str) -> List[dict]:
    """只取失败/暂停的调度轨迹（成功轨迹是流水账）。"""
    base = os.path.join(os.path.abspath(project_path), ".coderef", "dispatch")
    out: List[dict] = []
    if not os.path.isdir(base):
        return out
    try:
        names = [n for n in os.listdir(base) if n.endswith(".json")]
    except OSError:
        return out
    for name in names:
        path = os.path.join(base, name)
        data = _read_json(path, {})
        if not isinstance(data, dict):
            continue
        st = str(data.get("status") or "")
        if st not in ("failed", "paused"):
            continue
        out.append({
            "task_id": str(data.get("task_id") or name[:-5]),
            "intent": str(data.get("intent") or ""),
            "status": st,
            "current_step": str(data.get("current_step") or ""),
            "failed_steps": [
                {"id": str(s.get("id") or ""),
                 "error": str(s.get("error") or "")[:160]}
                for s in (data.get("steps") or [])
                if isinstance(s, dict) and s.get("status") == "failed"
            ][:6],
        })
        if len(out) >= MAX_DISPATCH_EVENTS:
            break
    return out


def _collect_git(project_path: str) -> dict:
    rc, head, err = _git(["rev-parse", "HEAD"], project_path)
    if rc != 0:
        return {"available": False, "head": "", "commits": [],
                "changed_files": [], "error": err or "非 git 仓库"}
    rc, out, _ = _git(["log", "-n", str(MAX_GIT_COMMITS),
                       "--pretty=format:%h|%s"], project_path)
    commits = []
    if rc == 0:
        for line in out.splitlines():
            if "|" in line:
                h, s = line.split("|", 1)
                commits.append({"commit": h.strip(), "subject": s.strip()[:160]})
    # 未提交的变更文件（草稿类 job 的输入之一）；只取路径，不读内容
    changed_files: List[str] = []
    rc, out, _ = _git(["status", "--porcelain"], project_path)
    if rc == 0:
        for line in out.splitlines()[:MAX_CHANGED_FILES]:
            # porcelain 首行可能被 _git 的 strip() 去掉状态前缀空格，
            # 不能按固定偏移切；用 split(None, 1) 稳健取路径。
            parts = line.split(None, 1)
            if len(parts) < 2:
                continue
            path = parts[1].strip()
            if " -> " in path:  # 重命名行：只保留新路径
                path = path.split(" -> ", 1)[1].strip()
            path = path.strip('"')
            if path:
                changed_files.append(path.replace("\\", "/")[:200])
    return {"available": True, "head": head, "commits": commits,
            "changed_files": changed_files, "error": ""}


def _collect(project_path: str, state: dict, new_seeds: List[dict]) -> dict:
    cursor = state.get("cursor") or {}
    audit_all, audit_events = _collect_audit(
        project_path, str(cursor.get("audit_after") or ""))
    dispatch = _collect_dispatch(project_path)
    git = _collect_git(project_path)
    session = {}
    try:
        from core.session_guard import load_state
        session = load_state(project_path)
    except Exception:  # noqa: BLE001
        session = {}
    last_guard = session.get("last_guard") if isinstance(
        session.get("last_guard"), dict) else {}
    facts = {}
    if isinstance(session.get("facts"), dict):
        for key, item in session["facts"].items():
            if isinstance(item, dict):
                facts[str(key)] = str(item.get("at") or "")
    return {
        "audit_all": audit_all,
        "audit": audit_events,
        "dispatch": dispatch,
        "git": git,
        "seeds": new_seeds,
        "facts": facts,
        "last_guard": {
            "at": str(last_guard.get("at") or ""),
            "findings": int(last_guard.get("findings") or 0),
            "high": int(last_guard.get("high") or 0),
            "summary": str(last_guard.get("summary") or "")[:200],
        },
    }


def _salient_count(materials: dict) -> int:
    count = len(materials.get("audit") or [])
    count += len(materials.get("dispatch") or [])
    count += len(materials.get("seeds") or [])
    guard = materials.get("last_guard") or {}
    if guard.get("findings"):
        count += 1
    return count


def _has_context(materials: dict, existing_notes: List[dict]) -> bool:
    """草稿类允许 salient==0，但必须至少有一种可起草的上下文。"""
    git = materials.get("git") or {}
    if git.get("commits") or git.get("changed_files"):
        return True
    if existing_notes:
        return True
    guard = materials.get("last_guard") or {}
    if guard.get("findings"):
        return True
    return bool(materials.get("facts"))


# ═══════════════════════════════════════════════════════════════
# 脱敏与提示词
# ═══════════════════════════════════════════════════════════════

_SECRET_RE = re.compile(r"(?i)\b(sk-[A-Za-z0-9_-]{8,}|"
                        r"(?:api[_-]?key|token|secret)\s*[=:]\s*\S+)")
_EMAIL_RE = re.compile(r"\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b")
_WIN_PATH_RE = re.compile(r"\b[A-Za-z]:\\[^\s\"']+")
_POSIX_HOME_RE = re.compile(r"/(?:home|Users)/[^/\s\"']+")


def _sanitize(text: str, project_path: str) -> str:
    """脱敏后再喂 LLM：密钥 / 邮箱 / 绝对路径 / 项目根。"""
    value = str(text or "")
    for variant in {project_path, os.path.abspath(project_path),
                    _norm(project_path)}:
        if variant:
            value = value.replace(variant, "<project>")
    home = os.path.expanduser("~")
    if home and home not in ("~", "/"):
        value = value.replace(home, "~")
    value = _SECRET_RE.sub("***", value)
    value = _EMAIL_RE.sub("***@***", value)
    value = _WIN_PATH_RE.sub("<abs_path>", value)
    value = _POSIX_HOME_RE.sub("~", value)
    return value


def _prompt_materials(materials: dict, project_path: str,
                      existing_notes: List[dict]) -> str:
    compact = {
        "显著事件": {
            "被阻断的调用": materials.get("audit") or [],
            "失败的调度": materials.get("dispatch") or [],
            "显式 seed": [
                {"id": s.get("id"), "kind": s.get("kind"),
                 "text": s.get("text"), "source": s.get("source")}
                for s in materials.get("seeds") or []
            ],
            "变更守卫": materials.get("last_guard") or {},
        },
        "上下文": {
            "最近提交": (materials.get("git") or {}).get("commits") or [],
            "变更文件": (materials.get("git") or {}).get("changed_files") or [],
            "git_available": (materials.get("git") or {}).get("available", False),
            "会话事实": materials.get("facts") or {},
        },
        "已有笔记（不要重复）": [
            {"kind": n.get("kind"), "text": n.get("text")}
            for n in existing_notes
            if n.get("status") in ("verified", "hypothesis")
        ][:20],
    }
    raw = json.dumps(compact, ensure_ascii=False, indent=1)
    return _sanitize(raw, project_path)


_SYSTEM_PROMPT = (
    "你是 CodeRef 的离线巩固层（做梦）。主 AI 醒着做事，你只在安静时把"
    "「值得下次先想到的经验」巩固成更少、更准的联想。\n"
    "只输出合法 JSON，不要解释、前后缀或 Markdown 代码块。"
)

_PROMPT_HEAD = """项目：{project}
时间：{now}
以下原料已按显著性筛选（被阻断 / 失败 / 显式 seed / 变更守卫退化），不是成功流水账。

{materials}

"""

_PITFALL_PROMPT = """请输出 JSON：
{
  "goal": "一句话概括当前目标（可为空串）",
  "notes": [
    {
      "kind": "rule|pitfall|convention|association",
      "text": "一条可被下次直接想起的经验（<=120字）",
      "evidence": ["file:相对路径", "git:短SHA", "audit:工具名", "seed:种子ID", "dispatch:任务ID"],
      "confidence": 0.0
    }
  ],
  "open_questions": ["尚未验证、需要下次留意的问题（<=3条）"]
}

硬约束：
1. 最多 5 条 notes；没有可靠经验就返回空数组，不要凑数。
2. 每条 note 必须有 evidence，且只使用上面五种格式；不要编造不存在的路径/SHA/ID。
3. 不要重复「已有笔记」；梦是假设，不要把猜测写成事实。
4. 只依据原料，不假装知道未记录的经历。
"""

_RECAP_PROMPT = """本次 job=recap（续接）：让下次开工立刻知道「当前目标 / 上次停在哪 / 该先做什么」。
请输出 JSON：
{
  "goal": "当前目标（一句话，可为空串）",
  "notes": [
    {
      "claim_type": "fact|advice",
      "kind": "context|rule|convention|pitfall",
      "text": "一句话（<=120字）",
      "evidence": ["file:相对路径", "git:短SHA", "audit:工具名", "seed:种子ID", "dispatch:任务ID"],
      "confidence": 0.0
    }
  ],
  "open_questions": ["未决问题（<=3条）"]
}

硬约束：
1. 最多 5 条 notes；goal 与「上次停在哪」用 claim_type=fact + kind=context。
2. 1–3 条开工建议必须 claim_type=advice；建议不是事实，只能是假设。
3. 每条 note 必须有 evidence，且只使用上面五种格式；不要编造不存在的路径/SHA/ID。
4. 不要重复「已有笔记」；只依据原料，不假装知道未记录的经历。
"""

_ROUTING_PROMPT = """本次 job=routing（联想）：把「当 X 发生，先用 Y」的工具/流程联想固化下来。
请输出 JSON：
{
  "notes": [
    {
      "claim_type": "advice",
      "kind": "association",
      "text": "当 X，先用 Y（<=120字）",
      "evidence": ["dispatch:任务ID", "audit:工具名", "seed:种子ID"],
      "confidence": 0.0
    }
  ],
  "open_questions": ["尚未验证的问题（<=3条）"]
}

硬约束：
1. 最多 5 条 notes；每条 claim_type 必须为 "advice"，kind 必须为 "association"。
2. text 必须是「当 X，先用 Y」形式；建议永远是假设，不得写成事实。
3. evidence 只用 dispatch: / audit: / seed: 三种格式；不要编造不存在的 ID。
4. 不要重复「已有笔记」；只依据原料，不假装知道未记录的经历。
"""

_TRIAGE_PROMPT = """本次 job=triage（开工排序）：只排序「先看什么」，不下确定性结论。
请输出 JSON：
{
  "notes": [
    {
      "claim_type": "advice",
      "kind": "association",
      "text": "先看 X，因为 Y（<=120字）",
      "evidence": ["file:相对路径", "audit:工具名", "dispatch:任务ID"],
      "confidence": 0.0
    }
  ],
  "open_questions": ["尚未验证的问题（<=3条）"]
}

硬约束：
1. 最多 3 条 notes；每条 claim_type 必须为 "advice"，text 必须是「先看 X，因为 Y」形式。
2. evidence 只用 file: / audit: / dispatch: 三种格式；不要编造不存在的路径/ID。
3. advice 永远是假设，不得标成事实或验证结论。
4. 不要重复「已有笔记」；只依据原料，不假装知道未记录的经历。
"""

_CHANGE_NARRATIVE_PROMPT = """本次 job=change_narrative（叙事）：只复述「上次改了什么 / 结果如何 / 下一步」，全部是事实。
请输出 JSON：
{
  "goal": "当前目标（一句话，可为空串）",
  "notes": [
    {
      "claim_type": "fact",
      "kind": "context",
      "text": "上次改了什么 / 结果如何 / 下一步（<=120字）",
      "evidence": ["git:短SHA", "audit:工具名", "dispatch:任务ID"],
      "confidence": 0.0
    }
  ],
  "open_questions": ["尚未确认的下一步问题（<=3条）"]
}

硬约束：
1. 最多 3 条 notes；每条 claim_type 必须为 "fact"，kind 必须为 "context"。
2. 只复述原料里确实发生的改动与结果；不推测原因、不写建议。
3. evidence 只用 git: / audit: / dispatch: 三种格式；不要编造不存在的 SHA/工具/ID。
4. 不要重复「已有笔记」；不假装知道未记录的经历。
"""

_FAILURE_POSTMORTEM_PROMPT = """本次 job=failure_postmortem（复盘）：只产出根因假设与下次如何避免，它们是建议，不是结论。
请输出 JSON：
{
  "notes": [
    {
      "claim_type": "advice",
      "kind": "pitfall",
      "text": "根因假设 / 下次怎么避免（<=120字）",
      "evidence": ["dispatch:任务ID", "audit:工具名", "seed:种子ID"],
      "confidence": 0.0
    }
  ],
  "open_questions": ["需要下次验证的根因（<=3条）"]
}

硬约束：
1. 最多 3 条 notes；每条 claim_type 必须为 "advice"，kind 必须为 "pitfall"。
2. 根因只能是假设，不得写成已验证结论；不确定就放进 open_questions。
3. evidence 只用 dispatch: / audit: / seed: 三种格式；不要编造不存在的 ID。
4. 不要重复「已有笔记」；不假装知道未记录的经历。
"""

_REVIEW_DRAFT_PROMPT = """本次 job=review_draft（草稿）：产出「变更审查清单」草稿。
草稿只写 drafts/，必须由用户确认后才能调用确定性工具，不得声称问题已确认。
请输出 JSON：
{
  "items": [
    {
      "text": "建议检查的点 / 疑似问题（<=120字）",
      "evidence": ["file:相对路径", "audit:工具名"],
      "confidence": 0.0
    }
  ],
  "open_questions": ["尚未确认的问题（<=3条）"],
  "next_tools": []
}

硬约束：
1. 最多 5 条 items；只依据原料，不假装读过未列出的文件。
2. 每条 item 必须有 evidence，只用 file: / audit: / dispatch: / git: 格式；不要编造路径/ID。
3. 这是草稿不是结论；不要输出修复代码，不要声称已定论。
4. next_tools 由服务端固定，留空即可。
"""

_REFACTOR_DRAFT_PROMPT = """本次 job=refactor_draft（草稿）：产出「重构步骤与风险」草稿。
草稿只写 drafts/，必须由用户确认后走 coderef_arch_verify / coderef_target_adopt，不得自动改代码。
请输出 JSON：
{
  "items": [
    {
      "text": "建议的重构步骤 / 风险（<=120字）",
      "evidence": ["file:相对路径", "audit:工具名", "dispatch:任务ID"],
      "confidence": 0.0
    }
  ],
  "open_questions": ["需先确认的结构问题（<=3条）"],
  "next_tools": []
}

硬约束：
1. 最多 5 条 items；只依据原料，不假装读过未列出的文件。
2. 每条 item 必须有 evidence，只用 file: / audit: / dispatch: / git: 格式；不要编造路径/ID。
3. 这是草稿不是结论；不输出具体代码改动，不声称架构已验证。
4. next_tools 由服务端固定，留空即可。
"""

_ASSET_CANDIDATE_PROMPT = """本次 job=asset_candidate（草稿）：产出「可复用设计候选」草稿。
草稿只写 drafts/，必须由用户确认后走 coderef_asset / coderef_registry 的门槛。
请输出 JSON：
{
  "items": [
    {
      "text": "可复用设计候选与复用条件（<=120字）",
      "evidence": ["file:相对路径", "audit:工具名", "seed:种子ID"],
      "confidence": 0.0
    }
  ],
  "open_questions": ["采用前需确认的问题（<=3条）"],
  "next_tools": []
}

硬约束：
1. 最多 5 条 items；只依据原料，不假装读过未列出的文件。
2. 每条 item 必须有 evidence，只用 file: / audit: / seed: / dispatch: 格式；不要编造路径/ID。
3. 这是候选不是已登记资产；不要声称资产已可复用。
4. next_tools 由服务端固定，留空即可。
"""

_NOISE_PROPOSAL_PROMPT = """本次 job=noise_proposal（草稿）：产出「降噪建议」草稿（scope / whitelist 方向）。
草稿只写 drafts/，必须由用户确认后调用 coderef_scope / coderef_whitelist，不得直接改规则。
请输出 JSON：
{
  "items": [
    {
      "text": "疑似误报 / 建议排除范围及理由（<=120字）",
      "evidence": ["audit:工具名", "file:相对路径", "dispatch:任务ID"],
      "confidence": 0.0
    }
  ],
  "open_questions": ["需要用户拍板的排除范围（<=3条）"],
  "next_tools": []
}

硬约束：
1. 最多 5 条 items；只依据原料，不假装读过未列出的文件。
2. 每条 item 必须有 evidence，只用 audit: / file: / dispatch: / seed: 格式；不要编造路径/ID。
3. 这是建议不是已生效规则；不得声称误报已被确认。
4. next_tools 由服务端固定，留空即可。
"""

# job → 输出契约（共用同一段原料头，只换这部分）
_JOB_BODIES = {
    "pitfall": _PITFALL_PROMPT,
    "recap": _RECAP_PROMPT,
    "routing": _ROUTING_PROMPT,
    "triage": _TRIAGE_PROMPT,
    "change_narrative": _CHANGE_NARRATIVE_PROMPT,
    "failure_postmortem": _FAILURE_POSTMORTEM_PROMPT,
}

# 草稿类 job 的提示词（只写 drafts/，不进入 notes）
_DRAFT_PROMPTS = {
    "review_draft": _REVIEW_DRAFT_PROMPT,
    "refactor_draft": _REFACTOR_DRAFT_PROMPT,
    "asset_candidate": _ASSET_CANDIDATE_PROMPT,
    "noise_proposal": _NOISE_PROPOSAL_PROMPT,
}

# 各 job 的 notes 条数上限（与提示词里的硬约束一致，服务端再兜一层）
_JOB_MAX_NOTES = {"pitfall": 5, "recap": 5, "routing": 5, "triage": 3,
                  "change_narrative": 3, "failure_postmortem": 3}
MAX_DRAFT_ITEMS = 5


# ═══════════════════════════════════════════════════════════════
# 确定性核验
# ═══════════════════════════════════════════════════════════════

def _check_ref(ref: str, ctx: dict) -> Tuple[Optional[bool], str]:
    """核验一条 evidence：True=存在，False=明确失败，None=无法核验。"""
    text = str(ref or "").strip()
    m = _EVIDENCE_RE.match(text)
    if not m:
        return None, "未知证据格式"
    kind, value = m.group(1), m.group(2).strip()
    pp = ctx["project_path"]

    if kind == "file":
        rel = value.replace("\\", "/")
        while rel.startswith("./"):
            rel = rel[2:]
        if (not rel or rel.startswith("/") or re.match(r"^[A-Za-z]:", rel)
                or ".." in rel.split("/")):
            return False, "文件路径越界"
        full = os.path.normpath(os.path.join(pp, rel))
        root = os.path.normpath(pp)
        if not (full == root or full.startswith(root + os.sep)):
            return False, "文件路径越界"
        return (os.path.isfile(full),
                "文件存在" if os.path.isfile(full) else "文件不存在")

    if kind in ("git", "commit"):
        if not _SHA_RE.fullmatch(value):
            return False, "非法 commit SHA"
        rc, _, err = _git(["rev-parse", "--verify", "--quiet",
                           value + "^{commit}"], pp)
        if rc == -1:
            return None, f"git 不可用: {err}"
        return rc == 0, ("commit 存在" if rc == 0 else "commit 不存在")

    if kind == "audit":
        if not _AUDIT_TOOL_RE.fullmatch(value):
            return False, "非法 audit 工具名"
        return (value in ctx["audit_tools"],
                "审计记录存在" if value in ctx["audit_tools"] else "无该工具审计记录")

    if kind == "seed":
        return (value in ctx["seed_ids"],
                "seed 存在" if value in ctx["seed_ids"] else "seed 不存在")

    if kind in ("dispatch", "trace"):
        if not _ID_RE.fullmatch(value):
            return False, "非法 task_id"
        path = os.path.join(pp, ".coderef", "dispatch", value + ".json")
        return os.path.isfile(path), ("轨迹存在" if os.path.isfile(path)
                                      else "轨迹不存在")

    return None, "未知证据格式"


def _normalize_candidate(raw: dict, ctx: dict,
                         job: str = DEFAULT_JOB) -> Optional[dict]:
    if not isinstance(raw, dict):
        return None
    text = _clean_text(raw.get("text"))
    if not text:
        return None
    kind = str(raw.get("kind") or "").strip().lower()
    claim_type = str(raw.get("claim_type") or "fact").strip().lower()
    if claim_type not in CLAIM_TYPES:
        claim_type = "fact"
    # job 契约兜底：routing/triage 的产出只能是建议，且统一归到「联想」
    if job in ("routing", "triage"):
        claim_type = "advice"
        kind = "association"
    # 叙事类：change_narrative 只复述事实（上下文），failure_postmortem 只产假设坑
    if job == "change_narrative":
        claim_type = "fact"
        kind = "context"
    elif job == "failure_postmortem":
        claim_type = "advice"
        kind = "pitfall"
    if kind not in KINDS:
        return {
            "id": "dream-" + uuid.uuid4().hex[:8],
            "kind": kind or "unknown", "job": job,
            "claim_type": claim_type,
            "text": text[:MAX_NOTE_CHARS],
            "evidence": [], "confidence": 0.0, "status": "rejected",
            "reason": f"非法 kind: {kind!r}",
            "created_at": _now(), "last_hit_at": "",
        }
    evidence = raw.get("evidence")
    if isinstance(evidence, str):
        evidence = [evidence]
    if not isinstance(evidence, list):
        evidence = []
    evidence = list(dict.fromkeys(
        _clean_text(e) for e in evidence if _clean_text(e)))[:MAX_EVIDENCE]
    try:
        confidence = max(0.0, min(1.0, float(raw.get("confidence", 0.5))))
    except (TypeError, ValueError):
        confidence = 0.5

    checks = []
    has_fail = has_ok = False
    for ref in evidence:
        ok, detail = _check_ref(ref, ctx)
        checks.append({"ref": ref, "ok": ok, "detail": detail})
        if ok is False:
            has_fail = True
        elif ok is True:
            has_ok = True

    if has_fail:
        status, reason = "rejected", "证据引用核验失败"
    elif claim_type == "advice":
        # 建议不是事实：无论有没有证据，都不允许 verified
        status = "hypothesis"
        reason = ("建议只能是假设（证据引用可核验，但仍非事实）" if has_ok
                  else "建议只能是假设")
    elif not evidence:
        status, reason = "hypothesis", "无 evidence，只能作为假设"
    elif has_ok:
        status, reason = "verified", "证据引用可核验（存在性核验，非语义真值）"
    else:
        status, reason = "hypothesis", "证据引用无法本地核验"

    return {
        "id": "dream-" + uuid.uuid4().hex[:8],
        "kind": kind,
        "job": job,
        "claim_type": claim_type,
        "text": text[:MAX_NOTE_CHARS],
        "evidence": evidence,
        "confidence": confidence,
        "status": status,
        "reason": reason,
        "checks": checks,
        "created_at": _now(),
        "last_hit_at": "",
    }


def _normalize_draft_item(raw: dict, ctx: dict, job: str) -> Optional[dict]:
    """草稿条目：统一 advice + hypothesis；证据核验失败 → rejected。"""
    if not isinstance(raw, dict):
        return None
    text = _clean_text(raw.get("text"))
    if not text:
        return None
    evidence = raw.get("evidence")
    if isinstance(evidence, str):
        evidence = [evidence]
    if not isinstance(evidence, list):
        evidence = []
    evidence = list(dict.fromkeys(
        _clean_text(e) for e in evidence if _clean_text(e)))[:MAX_EVIDENCE]
    try:
        confidence = max(0.0, min(1.0, float(raw.get("confidence", 0.5))))
    except (TypeError, ValueError):
        confidence = 0.5

    checks = []
    has_fail = False
    for ref in evidence:
        ok, detail = _check_ref(ref, ctx)
        checks.append({"ref": ref, "ok": ok, "detail": detail})
        if ok is False:
            has_fail = True

    rejected = has_fail
    return {
        "id": "draft-" + uuid.uuid4().hex[:8],
        "job": job,
        "claim_type": "advice",
        "text": text[:MAX_NOTE_CHARS],
        "evidence": evidence,
        "confidence": confidence,
        "status": "rejected" if rejected else "hypothesis",
        "reason": ("证据引用核验失败" if rejected
                   else "草稿条目：建议只能是假设，需用户确认后才能执行"),
        "checks": checks,
        "created_at": _now(),
    }


# ═══════════════════════════════════════════════════════════════
# 合并 / 衰减 / 写梦
# ═══════════════════════════════════════════════════════════════

_STATUS_RANK = {"verified": 0, "hypothesis": 1, "rejected": 2, "decayed": 3}


def _merge_notes(existing: List[dict], candidates: List[dict],
                 cfg: dict) -> Tuple[List[dict], List[dict]]:
    """按归一化文本去重合并；返回 (全部笔记, 新增笔记)。"""
    by_text = {_norm_text(n.get("text")): n for n in existing
               if n.get("text")}
    added = []
    for cand in candidates:
        key = _norm_text(cand.get("text"))
        old = by_text.get(key)
        if old is not None:
            old["evidence"] = list(dict.fromkeys(
                list(old.get("evidence") or []) + list(cand.get("evidence") or [])
            ))[:MAX_EVIDENCE]
            old["confidence"] = max(float(old.get("confidence") or 0),
                                    float(cand.get("confidence") or 0))
            # 同一文本的 advice 不能把已有 verified fact 降级/改写为建议
            if (str(old.get("claim_type") or "fact") == "fact"
                    and old.get("status") == "verified"
                    and str(cand.get("claim_type") or "fact") == "advice"):
                continue
            if (_STATUS_RANK.get(cand.get("status"), 9)
                    < _STATUS_RANK.get(str(old.get("status")), 9)):
                old["status"] = cand["status"]
                old["reason"] = cand.get("reason", "")
            continue
        cand["ttl_days"] = cfg["ttl_days"]
        existing.append(cand)
        by_text[key] = cand
        added.append(cand)
    return existing, added


def _decay_notes(project_path: str, notes: List[dict], cfg: dict
                 ) -> Tuple[List[dict], List[dict]]:
    """TTL：长期未命中的笔记下沉到 archive/，返回 (活跃笔记, 下沉笔记)。"""
    now = datetime.now()
    keep, decayed = [], []
    for n in notes:
        base = str(n.get("last_hit_at") or n.get("created_at") or "")
        try:
            age = now - datetime.fromisoformat(base)
        except (TypeError, ValueError):
            keep.append(n)
            continue
        ttl = _as_int(n.get("ttl_days"), cfg["ttl_days"], minimum=1)
        if age > timedelta(days=ttl):
            n["status"] = "decayed"
            n["decayed_at"] = _now()
            decayed.append(n)
        else:
            keep.append(n)
    if decayed:
        path = _path(project_path, os.path.join(
            ARCHIVE_DIR, f"notes-{datetime.now().strftime('%Y%m%d')}.json"))
        old = _read_json(path, {})
        old_notes = old.get("notes") if isinstance(old, dict) else []
        if not isinstance(old_notes, list):
            old_notes = []
        _write_json(path, {"version": SCHEMA_VERSION, "updated_at": _now(),
                           "notes": old_notes + decayed})
    return keep, decayed


def _created_desc(n: dict) -> float:
    """排序辅助：同状态下新笔记优先保留。"""
    try:
        return -datetime.fromisoformat(str(n.get("created_at") or "")).timestamp()
    except (TypeError, ValueError):
        return 0.0


def _cap_notes(notes: List[dict], max_notes: int) -> Tuple[List[dict], int]:
    if len(notes) <= max_notes:
        return notes, 0
    ranked = sorted(
        notes,
        key=lambda n: (_STATUS_RANK.get(str(n.get("status")), 9),
                       _created_desc(n)),
    )
    kept = ranked[:max_notes]
    return kept, len(notes) - len(kept)


def _fit_lines(lines: List[str], limit: int) -> str:
    out: List[str] = []
    used = 0
    for line in lines:
        if used + len(line) + 1 > limit:
            out.append("<!-- truncated: 超过 800 token 预算 -->")
            break
        out.append(line)
        used += len(line) + 1
    return "\n".join(out) + "\n"


def _render_md(goal: str, notes: List[dict], open_questions: List[str]) -> str:
    def _evidence(n: dict) -> str:
        return "、".join(str(e) for e in (n.get("evidence") or [])[:2])

    def _bullets(items: List[dict], assumption: bool = False) -> List[str]:
        out = []
        for n in items:
            line = f"- {str(n.get('text') or '')[:MAX_NOTE_CHARS]}"
            if assumption or n.get("claim_type") == "advice":
                line += "（假设）"
            ev = _evidence(n)
            if ev:
                line += f"（证据：{ev}）"
            out.append(line)
        return out or ["- （暂无）"]

    verified = [n for n in notes if n.get("status") == "verified"]
    hypotheses = [n for n in notes if n.get("status") == "hypothesis"]

    # 「当前目标」可附 kind=context 的 fact/advice（最多 3 条）；
    # triage 的 advice 统一归 association，避免与「本次开工建议」重复渲染。
    contexts = [n for n in notes
                if n.get("kind") == "context"
                and n.get("job") != "triage"
                and n.get("status") in ("verified", "hypothesis")][:3]
    # 「本次开工建议」只放 triage 的 advice（最多 3 条）
    triage = [n for n in notes
              if n.get("job") == "triage"
              and n.get("claim_type") == "advice"
              and n.get("status") in ("verified", "hypothesis")][:3]
    rules = [n for n in verified if n.get("kind") in ("rule", "convention")]
    pitfalls = [n for n in verified if n.get("kind") == "pitfall"]
    associations = [n for n in verified if n.get("kind") == "association"]
    routing = [n for n in hypotheses
               if n.get("job") == "routing"
               and n.get("kind") == "association"]

    rendered = {id(n) for n in contexts + triage + routing}
    rendered.update(id(n) for n in rules + pitfalls + associations)
    others = [n for n in hypotheses if id(n) not in rendered]
    routing_lines = _bullets(routing, assumption=True) if routing else []
    association_lines = _bullets(associations) if associations else []
    if not association_lines and not routing_lines:
        association_lines = ["- （暂无）"]

    goal_lines = [f"- {goal}"] if goal else []
    context_lines = _bullets(contexts) if contexts else []
    if not goal_lines and not context_lines:
        goal_lines = ["- （未记录）"]

    lines = [
        "# Dream",
        f"<!-- 生成于 {_now()}；梦是假设：引用前先核验 evidence，"
        "不得当作审计结论转述 -->",
        "",
        "## 当前目标",
        *goal_lines,
        *context_lines,
        "",
        "## 本次开工建议",
        *_bullets(triage, assumption=True),
        "",
        "## 已验证规则",
        *_bullets(rules),
        "",
        "## 坑",
        *_bullets(pitfalls),
        "",
        "## 联想（当 X，先想到 Y）",
        *association_lines,
        *routing_lines,
        "",
        "## 未决问题",
    ]
    questions = [str(q)[:MAX_NOTE_CHARS] for q in list(open_questions)[:5]]
    for n in others[:5]:
        ev = _evidence(n)
        line = f"（假设，未验证）{n.get('text')}"
        if ev:
            line += f"（证据：{ev}）"
        questions.append(line)
    if questions:
        lines.extend(f"- {q}" for q in questions)
    else:
        lines.append("- （暂无）")
    return _fit_lines(lines, DREAM_MD_MAX_CHARS)


def _md_section(md: str, heading: str) -> str:
    """取 DREAM.md 中某个 `## 标题` 到下一个 `## ` 之间的正文。"""
    text = str(md or "")
    marker = "## " + heading
    idx = text.find(marker)
    if idx < 0:
        return ""
    section = text[idx + len(marker):]
    end = section.find("\n## ")
    return section[:end] if end >= 0 else section


def _extract_md_goal(md: str) -> str:
    """从当前 DREAM.md 的「当前目标」取第一条非占位项。"""
    for line in _md_section(md, "当前目标").splitlines():
        line = line.strip()
        if not line.startswith("- "):
            continue
        item = line[2:].strip()
        if not item or item in ("（未记录）", "（暂无）"):
            continue
        return item
    return ""


def _extract_md_open_questions(md: str) -> List[str]:
    """从当前 DREAM.md 的「未决问题」提取已有问题。

    `（暂无）` 是占位、`（假设，未验证）` 由 notes 重渲染，都不保留。
    """
    out: List[str] = []
    for line in _md_section(md, "未决问题").splitlines():
        line = line.strip()
        if not line.startswith("- "):
            continue
        item = line[2:].strip()
        if not item or item == "（暂无）":
            continue
        if item.startswith("（假设，未验证）"):
            continue
        out.append(item)
    return out


def _write_run(project_path: str, record: dict) -> str:
    # 微秒 + 6 位随机：同一秒内多轮 run（并发/密集触发）也不会撞名覆盖。
    name = (datetime.now().strftime("%Y%m%d-%H%M%S-%f")
            + "-" + uuid.uuid4().hex[:6] + ".json")
    return _write_json(_path(project_path, os.path.join(RUNS_DIR, name)), record)


# ═══════════════════════════════════════════════════════════════
# 做梦主流程
# ═══════════════════════════════════════════════════════════════

def _skip(pp: str, state: dict, cfg: dict, reason: str, trigger: str,
          run_extra: Optional[dict] = None,
          job: str = DEFAULT_JOB) -> dict:
    """诚实 SKIP：不写 DREAM.md / notes，不推进 cursor；只留运行痕迹。"""
    state["last_attempt_at"] = _now()
    state["last_status"] = "SKIP"
    state["last_error"] = reason
    state["last_job"] = job
    state["runs"] = int(state.get("runs") or 0) + 1
    _save_state(pp, state)
    record = {"ts": _now(), "trigger": trigger, "job": job, "status": "SKIP",
              "reason": reason,
              "llm": {"available": False, "source": "主 LLM 配置"},
              "limits": {"max_prompt_chars": MAX_PROMPT_CHARS,
                         "max_output_tokens": MAX_OUTPUT_TOKENS}}
    if run_extra:
        record.update(run_extra)
    run_path = _write_run(pp, record)
    from core.result_envelope import envelope
    no_key = "Key" in reason
    env = envelope(
        f"本次补梦 SKIP：{reason}",
        usable=False,
        blocked_by=[{"id": "dream_unavailable",
                     "owner": "consumer" if no_key else "by_design",
                     "human": reason}],
        owner_actions={"consumer": (
            ["配置主 LLM API Key：CODEREF_API_KEY 或 config/config.json 的 llm_api_key"]
            if no_key else [])},
        cannot_conclude=["任何梦结论（本轮未产出）"],
        guardrails=["SKIP 是事实，不是失败；旧梦保持不变"],
    )
    return {"status": "SKIP", "tool": "coderef_dream", "action": "run",
            "project_path": pp, "reason": reason, "run_path": run_path,
            "envelope": env}


def run(project_path: str, trigger: str = "manual", llm=None,
        job: str = DEFAULT_JOB) -> dict:
    """补一轮梦；并发 run 通过 `_save_notes` 的基线合并保留彼此新增。

    只取进程内锁，不在整轮期间持跨进程文件锁：整轮包含多分钟 LLM 调用，
    长持锁会让另一进程的 run 等满超时后失败。跨进程写安全由三段短锁保证：
    读取 seeds（seeds 锁）、写 notes.json（`_save_notes` 合并锁）、
    写 DREAM.md 与 state（主锁）。
    """
    pp = os.path.abspath(project_path)
    if not os.path.isdir(pp):
        return {"status": "error", "tool": "coderef_dream", "action": "run",
                "error": f"项目目录不存在: {pp}"}
    try:
        with _project_lock(pp):
            return _run_impl(pp, trigger=trigger, llm=llm, job=job)
    except OSError as e:   # 短锁超时或文件系统错误：结构化返回
        return {"status": "error", "tool": "coderef_dream", "action": "run",
                "error": f"dream 锁被占用或不可用，请稍后重试: {e}"}


def _run_impl(project_path: str, trigger: str = "manual", llm=None,
              job: str = DEFAULT_JOB) -> dict:
    """补一轮梦：收集 → 筛显著 → 主 LLM 提议 → 确定性核验 → 写梦。

    job ∈ ALL_JOBS；记忆/叙事类写 notes.json + DREAM.md，
    草稿类只写 drafts/，两者共用核验、脱敏与 runs/ 记录。
    """
    pp = os.path.abspath(project_path)
    job = str(job or DEFAULT_JOB).strip().lower()
    if job not in ALL_JOBS:
        return {"status": "error", "tool": "coderef_dream", "action": "run",
                "error": f"未知 job: {job!r}；支持 {'/'.join(ALL_JOBS)}"}
    if not os.path.isdir(pp):
        return {"status": "error", "tool": "coderef_dream", "action": "run",
                "error": f"项目目录不存在: {pp}"}
    cfg = load_config(pp)
    state = _load_state(pp)
    if not cfg["enabled"]:
        return _skip(pp, state, cfg, "dream.enabled=false（梦已禁用）", trigger,
                     job=job)

    # P3 维护类：手动触发，不走主 LLM 的 SKIP 闸门。
    # false_memory_check 纯确定性核验，不需要主 LLM；
    # eval_replay 默认确定性指标，有 LLM 时只追加软摘要。
    if job == "false_memory_check":
        return _run_false_memory_check(pp, cfg, state, trigger)
    if job == "eval_replay":
        return _run_eval_replay(pp, cfg, state, trigger, llm)

    llm = llm if llm is not None else _build_llm()
    if not _llm_available(llm):
        return _skip(pp, state, cfg,
                     "未配置有效的主 LLM API Key（梦复用主 LLM 配置，无 Key 不编造）",
                     trigger, job=job)

    with _file_lock(pp, _SEEDS_LOCK_FILE):
        seeds = _read_seeds(pp)
    consumed = _as_int((state.get("cursor") or {}).get("seeds_consumed"), 0)
    new_seeds = seeds[consumed:consumed + MAX_SEEDS]
    # 本轮只消费最前面的 MAX_SEEDS 条；剩余留到下次，不能直接记成全部已消费。
    seeds_consumed_after = min(len(seeds), consumed + MAX_SEEDS)
    seeds_pending = max(0, len(seeds) - seeds_consumed_after)
    materials = _collect(pp, state, new_seeds)
    salient = _salient_count(materials)
    existing = _load_notes(pp)
    base_ids = {str(n.get("id") or "") for n in existing}
    if job in DRAFT_JOBS:
        # 草稿类允许 salient==0，但必须有可起草的上下文
        if salient == 0 and not _has_context(materials, existing):
            return _skip(
                pp, state, cfg,
                "没有可起草的上下文（无 git 变更 / 已有笔记 / 变更守卫 / 会话事实）",
                trigger, {"materials": {"salient": 0}}, job=job)
        return _run_draft(pp, cfg, state, trigger, job, llm, materials,
                          existing, seeds, salient)
    if job == "change_narrative":
        # 变更叙事只需要「有变更可讲」；没有 blocked/失败时也允许基于 git 记录成文
        if salient == 0 and not _has_context(materials, existing):
            return _skip(
                pp, state, cfg,
                "没有可叙事的上下文（无 git 提交/变更、已有笔记、变更守卫或会话事实）",
                trigger, {"materials": {"salient": 0}}, job=job)
    elif salient == 0:
        return _skip(pp, state, cfg, "没有新的显著原料（成功流水账不入梦）",
                     trigger, {"materials": {"salient": 0}}, job=job)

    material_text = _prompt_materials(materials, pp, existing)
    # 单次上下文/输出硬上限（防止撑爆主 LLM，不是 dream 独立预算）
    max_input_chars = MAX_PROMPT_CHARS
    if len(material_text) > max_input_chars:
        material_text = material_text[:max_input_chars] + "\n…（原料已按上下文上限截断）"
    llm_cfg = getattr(llm, "config", None)
    out_tokens = min(MAX_OUTPUT_TOKENS, max(
        256, _as_int(getattr(llm_cfg, "max_tokens", MAX_OUTPUT_TOKENS),
                     MAX_OUTPUT_TOKENS, minimum=256)))
    messages = [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": _PROMPT_HEAD.format(
            project=os.path.basename(pp), now=_now(),
            materials=material_text) + _JOB_BODIES[job]},
    ]

    try:
        response = llm.chat_completion(
            messages, temperature=0.2, max_tokens=out_tokens,
            response_format={"type": "json_object"})
    except Exception as e:  # noqa: BLE001
        return _fail(pp, state, cfg, trigger, f"LLM 调用异常: {e}", materials,
                     job=job)

    from core.llm_integration import LLMIntegration
    if LLMIntegration.is_error_response(response):
        return _fail(pp, state, cfg, trigger, response, materials, job=job)
    data = LLMIntegration.parse_json_response(response)
    if data is None:
        return _fail(pp, state, cfg, trigger,
                     "LLM 返回内容无法解析为 JSON（旧梦保持不变）", materials,
                     response=response, job=job)
    if isinstance(data, list):
        data = {"notes": data}
    if not isinstance(data, dict):
        return _fail(pp, state, cfg, trigger, "LLM 返回结构不是对象", materials,
                     job=job)

    goal = _clean_text(data.get("goal"))[:200]
    open_questions = [str(q)[:200] for q in (data.get("open_questions") or [])
                      if str(q).strip()][:5]
    raw_notes = data.get("notes")
    if not isinstance(raw_notes, list):
        raw_notes = []

    ctx = {
        "project_path": pp,
        "audit_tools": {str(e.get("tool") or "")
                        for e in materials.get("audit_all") or []},
        "seed_ids": {str(s.get("id")) for s in seeds},
    }
    candidates = [c for c in (_normalize_candidate(n, ctx, job)
                              for n in raw_notes[:_JOB_MAX_NOTES[job]]) if c]
    notes, added = _merge_notes(existing, candidates, cfg)
    notes, decayed = _decay_notes(pp, notes, cfg)
    notes, dropped = _cap_notes(notes, cfg["max_notes"])
    prev_base = getattr(_save_base, "ids", None)
    _save_base.ids = base_ids
    try:
        save_err = _save_notes(pp, notes)
    finally:
        _save_base.ids = prev_base
    if save_err:
        return _fail(pp, state, cfg, trigger, "notes.json 写入失败", materials,
                     job=job)
    # 并发 run 可能刚合并进彼此的新笔记；重读后再渲染，DREAM.md 不丢并发结果。
    # 重读 + 渲染 + 写回放进主锁，保证 DREAM.md 与当时的 notes.json 一致。
    with _file_lock(pp):
        notes = _load_notes(pp)

        verified = [n for n in notes if n.get("status") == "verified"]
        if goal or added:
            md = _render_md(goal or str(state.get("goal") or ""), notes,
                            open_questions)
            err = _write_text(_path(pp, DREAM_MD), md)
            if err:
                return _fail(pp, state, cfg, trigger, f"DREAM.md 写入失败: {err}",
                             materials, job=job)
        md_chars = len(_read_text(_path(pp, DREAM_MD)))

    est_tokens = round((len(material_text) + len(response)) / CHARS_PER_TOKEN)
    now = _now()
    last_audit = ""
    if materials.get("audit"):
        last_audit = str(materials["audit"][-1].get("ts") or "")
    with _file_lock(pp):
        # 跨进程 run 可能已推进过 cursor/runs：以磁盘状态为准做一次合并写，
        # 不把对方新增的计数或游标覆盖回本轮开始时的旧值。
        fresh = _load_state(pp)
        prev_cursor = fresh.get("cursor") or {}
        fresh.update({
            "last_dream_at": now,
            "last_attempt_at": now,
            "last_status": "ok",
            "last_error": "",
            "last_job": job,
            "goal": goal or str(fresh.get("goal") or ""),
            "open_questions": open_questions,
            "last_run_tokens": est_tokens,
            "runs": int(fresh.get("runs") or 0) + 1,
        })
        fresh["cursor"] = {
            "audit_after": last_audit or prev_cursor.get("audit_after", ""),
            "seeds_consumed": max(seeds_consumed_after,
                                  _as_int(prev_cursor.get("seeds_consumed"), 0)),
            "git_head": ((materials.get("git") or {}).get("head", "")
                         or prev_cursor.get("git_head", "")),
        }
        _save_state(pp, fresh)

    accepted = [n for n in added if n.get("status") in ("verified", "hypothesis")]
    rejected = [{"text": n.get("text", ""), "reason": n.get("reason", "")}
                for n in candidates if n.get("status") == "rejected"]
    llm_provider = getattr(getattr(llm, "config", None), "provider", None)
    record = {
        "ts": now, "trigger": trigger, "job": job, "status": "ok",
        "llm": {"available": True, "source": "主 LLM 配置",
                "provider": getattr(llm_provider, "value",
                                    str(llm_provider or "")),
                "model": str(getattr(llm_cfg, "model", "") or "")},
        "materials": {
            "salient": salient,
            "audit_events": len(materials.get("audit") or []),
            "dispatch_events": len(materials.get("dispatch") or []),
            "seeds": len(new_seeds),
            "seeds_pending": seeds_pending,
            "git_commits": len((materials.get("git") or {}).get("commits") or []),
        },
        "goal": goal,
        "seeds_pending": seeds_pending,
        "items": [{"text": n.get("text", ""), "kind": n.get("kind", ""),
                   "claim_type": n.get("claim_type", "fact"),
                   "status": n.get("status", ""),
                   "evidence": list(n.get("evidence") or [])}
                  for n in candidates],
        "candidates": candidates,
        "accepted": [n.get("id") for n in accepted],
        "rejected": rejected,
        "decayed": len(decayed), "dropped": dropped,
        "notes_total": len(notes), "verified_total": len(verified),
        "dream_md_chars": md_chars,
        "limits": {"max_prompt_chars": max_input_chars,
                   "max_output_tokens": out_tokens,
                   "estimated_tokens": est_tokens},
    }
    run_path = _write_run(pp, record)

    from core.result_envelope import envelope
    env = envelope(
        f"本次补梦完成：新增 {len(accepted)} 条笔记"
        f"（已验证 {len(verified)} 条，拒绝 {len(rejected)} 条）",
        usable=bool(accepted),
        cannot_conclude=["梦条目的语义真值（仅做了证据存在性核验）",
                         "未记录的经历"],
        guardrails=["梦是假设：引用前先核验 evidence，不得当作审计结论转述"],
    )
    if rejected:
        env["blocked_by"] = [{"id": "dream_rejected", "owner": "coderef",
                              "human": f"{len(rejected)} 条候选证据核验失败，已拒绝"}]
    return {
        "status": "ok", "tool": "coderef_dream", "action": "run",
        "project_path": pp, "trigger": trigger, "job": job,
        "goal": goal,
        "notes": {"added": len(accepted), "verified": len(verified),
                  "rejected": len(rejected), "decayed": len(decayed),
                  "dropped": dropped, "total": len(notes)},
        "seeds_pending": seeds_pending,
        "dream_md_path": _path(pp, DREAM_MD), "dream_md_chars": md_chars,
        "notes_path": _path(pp, NOTES_FILE), "run_path": run_path,
        "llm": record["llm"], "limits": record["limits"],
        "envelope": env,
    }


def _run_draft(pp: str, cfg: dict, state: dict, trigger: str, job: str,
               llm, materials: dict, existing: List[dict],
               seeds: List[dict], salient: int) -> dict:
    """草稿类 job：只写 `.coderef/dream/drafts/`，不碰 notes.json / DREAM.md。"""
    material_text = _prompt_materials(materials, pp, existing)
    # 单次上下文/输出硬上限（防止撑爆主 LLM，不是 dream 独立预算）
    max_input_chars = MAX_PROMPT_CHARS
    if len(material_text) > max_input_chars:
        material_text = material_text[:max_input_chars] + "\n…（原料已按上下文上限截断）"
    llm_cfg = getattr(llm, "config", None)
    out_tokens = min(MAX_OUTPUT_TOKENS, max(
        256, _as_int(getattr(llm_cfg, "max_tokens", MAX_OUTPUT_TOKENS),
                     MAX_OUTPUT_TOKENS, minimum=256)))
    messages = [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": _PROMPT_HEAD.format(
            project=os.path.basename(pp), now=_now(),
            materials=material_text) + _DRAFT_PROMPTS[job]},
    ]

    try:
        response = llm.chat_completion(
            messages, temperature=0.2, max_tokens=out_tokens,
            response_format={"type": "json_object"})
    except Exception as e:  # noqa: BLE001
        return _fail(pp, state, cfg, trigger, f"LLM 调用异常: {e}", materials,
                     job=job)

    from core.llm_integration import LLMIntegration
    if LLMIntegration.is_error_response(response):
        return _fail(pp, state, cfg, trigger, response, materials, job=job)
    data = LLMIntegration.parse_json_response(response)
    if data is None:
        return _fail(pp, state, cfg, trigger,
                     "LLM 返回内容无法解析为 JSON（草稿未写入）", materials,
                     response=response, job=job)
    if isinstance(data, list):
        data = {"items": data}
    if not isinstance(data, dict):
        return _fail(pp, state, cfg, trigger, "LLM 返回结构不是对象", materials,
                     job=job)

    raw_items = data.get("items")
    if not isinstance(raw_items, list):
        raw_items = []
    open_questions = [str(q)[:200] for q in (data.get("open_questions") or [])
                      if str(q).strip()][:5]
    ctx = {
        "project_path": pp,
        "audit_tools": {str(e.get("tool") or "")
                        for e in materials.get("audit_all") or []},
        "seed_ids": {str(s.get("id")) for s in seeds},
    }
    normalized = [c for c in (_normalize_draft_item(n, ctx, job)
                              for n in raw_items[:MAX_DRAFT_ITEMS]) if c]
    items = [n for n in normalized if n.get("status") != "rejected"]
    rejected = [{"text": n.get("text", ""), "reason": n.get("reason", ""),
                 "evidence": list(n.get("evidence") or [])}
                for n in normalized if n.get("status") == "rejected"]
    # 服务端固定后续确定性闸门，忽略 LLM 返回的 next_tools
    next_tools = list(DRAFT_NEXT_TOOLS[job])

    now = _now()
    est_tokens = round((len(material_text) + len(response)) / CHARS_PER_TOKEN)
    limits = {"max_prompt_chars": max_input_chars,
              "max_output_tokens": out_tokens,
              "estimated_tokens": est_tokens}
    draft_id = (job + "-" + datetime.now().strftime("%Y%m%d-%H%M%S")
                + "-" + uuid.uuid4().hex[:4])
    draft_path = _path(pp, os.path.join(DRAFTS_DIR, draft_id + ".json"))
    draft = {
        "schema_version": SCHEMA_VERSION,
        "job": job,
        "generated_at": now,
        "project_path": pp,
        "items": items,
        "rejected": rejected,
        "open_questions": open_questions,
        "requires_confirmation": True,
        "next_tools": next_tools,
        "materials": {
            "salient": salient,
            "audit_events": len(materials.get("audit") or []),
            "dispatch_events": len(materials.get("dispatch") or []),
            "seeds": len(materials.get("seeds") or []),
            "git_commits": len((materials.get("git") or {}).get("commits") or []),
            "changed_files": len((materials.get("git") or {}).get("changed_files") or []),
            "notes_total": len(existing),
        },
        "limits": limits,
    }
    err = _write_json(draft_path, draft)
    if err:
        return _fail(pp, state, cfg, trigger, f"草稿写入失败: {err}", materials,
                     job=job)

    state.update({
        "last_attempt_at": now,
        "last_status": "ok",
        "last_error": "",
        "last_job": job,
        "last_run_tokens": est_tokens,
        "runs": int(state.get("runs") or 0) + 1,
    })
    # 草稿提示词确实消费了这批 seed，成功写盘后按同样规则推进 cursor。
    consumed = _as_int((state.get("cursor") or {}).get("seeds_consumed"), 0)
    seeds_consumed_after = min(len(seeds), consumed + MAX_SEEDS)
    seeds_pending = max(0, len(seeds) - seeds_consumed_after)
    cursor = dict(state.get("cursor") or {})
    cursor["seeds_consumed"] = seeds_consumed_after
    state["cursor"] = cursor
    _save_state(pp, state)

    llm_provider = getattr(llm_cfg, "provider", None)
    record = {
        "ts": now, "trigger": trigger, "job": job, "status": "ok",
        "draft_path": draft_path,
        "llm": {"available": True, "source": "主 LLM 配置",
                "provider": getattr(llm_provider, "value",
                                    str(llm_provider or "")),
                "model": str(getattr(llm_cfg, "model", "") or "")},
        "materials": draft["materials"],
        "seeds_pending": seeds_pending,
        "items": [{"text": n.get("text", ""),
                   "status": n.get("status", ""),
                   "evidence": list(n.get("evidence") or [])}
                  for n in items],
        "rejected": rejected,
        "open_questions": open_questions,
        "requires_confirmation": True,
        "next_tools": next_tools,
        "limits": limits,
    }
    run_path = _write_run(pp, record)

    from core.result_envelope import envelope
    consumer_actions = list(next_tools) + ["请人工确认草稿后再执行上述确定性工具"]
    env = envelope(
        f"草稿已生成（{job}）：{len(items)} 条建议、{len(rejected)} 条被拒；"
        "必须用户确认后才能应用",
        usable=False,
        blocked_by=[{"id": "draft_requires_confirmation", "owner": "consumer",
                     "human": "草稿是建议不是结论，需人工确认后走确定性工具"}],
        owner_actions={"consumer": consumer_actions},
        cannot_conclude=["草稿中的任何结论（未经确定性工具确认）",
                         "草稿是否应被采纳"],
        guardrails=["草稿只写 drafts/，不进入 notes.json / DREAM.md",
                    "应用草稿必须走 next_tools + 用户确认"],
    )
    return {
        "status": "ok", "tool": "coderef_dream", "action": "run",
        "project_path": pp, "trigger": trigger, "job": job,
        "draft_id": draft_id, "draft_path": draft_path,
        "items": len(items), "rejected": len(rejected),
        "requires_confirmation": True, "next_tools": next_tools,
        "open_questions": open_questions,
        "seeds_pending": seeds_pending,
        "run_path": run_path, "limits": limits, "llm": record["llm"],
        "envelope": env,
    }


def _run_false_memory_check(pp: str, cfg: dict, state: dict,
                            trigger: str) -> dict:
    """P3 自检：旧梦对照新证据，证据引用失效 → rejected（纯确定性，不调 LLM）。"""
    notes = _load_notes(pp)
    base_ids = {str(n.get("id") or "") for n in notes}
    if not notes:
        return _skip(pp, state, cfg, "没有可复核的笔记", trigger,
                     job="false_memory_check")

    try:
        from core import audit_log
        entries = audit_log.read(pp, days=AUDIT_DAYS)
    except Exception:  # noqa: BLE001 —— 审计日志不可用 → audit: 引用无法核验
        entries = []
    ctx = {
        "project_path": pp,
        "audit_tools": {str(e.get("tool") or "") for e in entries
                        if isinstance(e, dict)},
        "seed_ids": {str(s.get("id")) for s in _read_seeds(pp)},
    }

    now = _now()
    checked = kept = 0
    rejected: List[dict] = []
    downgraded: List[dict] = []
    unknown: List[dict] = []
    for n in notes:
        if str(n.get("status") or "") not in ("verified", "hypothesis"):
            continue
        checked += 1
        n["checked_at"] = now
        failed = []
        unverifiable = []
        refs = [str(r) for r in (n.get("evidence") or [])]
        if not refs and str(n.get("status") or "") == "verified":
            unverifiable.append({"ref": "", "detail": "无 evidence"})
        for ref in refs:
            ok, detail = _check_ref(ref, ctx)
            if ok is None:
                # 格式非法 / 空引用 / git 不可用：无法核验，不能继续当 verified。
                unverifiable.append({"ref": ref, "detail": detail})
            elif ok is False:
                # 审计日志按日轮转，只有 7 天窗口；窗口外找不到 ≠ 证据失效，
                # 但必须如实记为 unknown，不能悄悄吞掉。格式非法的 audit 引用
                # （如 audit:../../etc/passwd）不是窗口问题，按失败处理。
                if (ref.startswith("audit:")
                        and _AUDIT_TOOL_RE.fullmatch(ref[6:].strip())):
                    unknown.append({"ref": ref, "detail": detail})
                    continue
                failed.append({"ref": ref, "detail": detail})
        if failed:
            n["status"] = "rejected"
            n["reason"] = "false_memory_check: 证据引用已失效"
            rejected.append({
                "id": n.get("id", ""),
                "text": n.get("text", ""),
                "status": "rejected",
                "reason": n["reason"],
                "evidence": list(n.get("evidence") or []),
                "failed": failed,
            })
        else:
            if unverifiable and str(n.get("status") or "") == "verified":
                n["status"] = "hypothesis"
                n["reason"] = "false_memory_check: 证据无法核验，降级为假设"
                downgraded.append({
                    "id": n.get("id", ""),
                    "text": n.get("text", ""),
                    "status": "hypothesis",
                    "reason": n["reason"],
                    "evidence": list(n.get("evidence") or []),
                    "unverifiable": unverifiable,
                })
            kept += 1

    prev_base = getattr(_save_base, "ids", None)
    _save_base.ids = base_ids
    try:
        save_err = _save_notes(pp, notes)
    finally:
        _save_base.ids = prev_base
    if save_err:
        return _fail(pp, state, cfg, trigger, "notes.json 写入失败", {},
                     job="false_memory_check")
    notes = _load_notes(pp)

    goal = str(state.get("goal") or "").strip()
    open_questions = [str(q).strip() for q in (state.get("open_questions") or [])
                      if str(q).strip()]
    md_path = _path(pp, DREAM_MD)
    current_md = _read_text(md_path)
    # 旧 state 可能没有 goal / open_questions：回落到当前 DREAM.md，避免清空。
    if not goal:
        goal = _extract_md_goal(current_md)
    if not open_questions:
        open_questions = _extract_md_open_questions(current_md)
    if rejected or downgraded:
        err = _write_text(md_path, _render_md(goal, notes, open_questions))
        if err:
            return _fail(pp, state, cfg, trigger, f"DREAM.md 写入失败: {err}", {},
                         job="false_memory_check")

    # 维护不是做梦：只更新 last_attempt_at，不动 last_dream_at。
    state.update({
        "last_attempt_at": now,
        "last_status": "ok",
        "last_error": "",
        "last_job": "false_memory_check",
        "runs": int(state.get("runs") or 0) + 1,
    })
    _save_state(pp, state)

    limits = {"max_prompt_chars": 0, "max_output_tokens": 0,
              "estimated_tokens": 0}
    record = {
        "ts": now, "trigger": trigger, "job": "false_memory_check",
        "status": "ok",
        "llm": {"available": False, "source": "确定性核验（不调用 LLM）"},
        "checked": checked,
        "rejected": rejected,
        "downgraded": downgraded,
        "kept": kept,
        "unknown": len(unknown),
        "unknown_items": unknown,
        "limits": limits,
    }
    run_path = _write_run(pp, record)

    from core.result_envelope import envelope
    cannot_conclude = ["笔记的语义真值（只核验证据引用是否仍存在）"]
    if unknown:
        cannot_conclude.append("部分 audit 证据超出 7 天窗口，未能复核")
    if downgraded:
        cannot_conclude.append("证据无法核验的笔记已降级为 hypothesis")
    env = envelope(
        f"复核 {checked} 条，拒绝 {len(rejected)} 条，降级 {len(downgraded)} 条，"
        f"{len(unknown)} 条证据无法复核（unknown）",
        usable=True,
        cannot_conclude=cannot_conclude,
        guardrails=["false_memory_check 只核验证据引用是否仍存在，"
                    "不判断语义真值；audit 窗口外证据如实记为 unknown"],
    )
    return {
        "status": "ok", "tool": "coderef_dream", "action": "run",
        "project_path": pp, "trigger": trigger, "job": "false_memory_check",
        "checked": checked, "rejected": len(rejected),
        "downgraded": len(downgraded), "downgraded_items": downgraded,
        "kept": kept,
        "unknown": len(unknown), "unknown_items": unknown,
        "dream_md_path": md_path, "notes_path": _path(pp, NOTES_FILE),
        "run_path": run_path, "llm": record["llm"], "limits": limits,
        "envelope": env,
    }


_EVAL_REPLAY_PROMPT = """本次 job=eval_replay（评估回放）：只对已落盘的梦指标做抽样评估。
请只输出 JSON：
{"summary": "一句话总体评价（<=200字）", "issues": ["值得留意的指标异常（<=5条）"]}

硬约束：
1. 只依据给出的指标与 DREAM.md 摘录，不推断未记录的事实。
2. issues 最多 5 条；没有异常就返回空数组。
3. summary 是软判断，不是审计结论。
"""


def _recent_run_records(project_path: str, limit: int = 100) -> List[dict]:
    """最近 limit 个 runs/*.json（文件名为时间戳，倒序即最近优先）。"""
    base = _path(project_path, RUNS_DIR)
    if not os.path.isdir(base):
        return []
    try:
        names = sorted((n for n in os.listdir(base)
                        if n.endswith(".json")), reverse=True)
    except OSError:
        return []
    out = []
    for name in names[:limit]:
        data = _read_json(os.path.join(base, name), {})
        if isinstance(data, dict):
            out.append(data)
    return out


def _decayed_count(project_path: str) -> int:
    """统计 archive/notes-*.json 里 status=decayed 的条数。"""
    base = _path(project_path, ARCHIVE_DIR)
    if not os.path.isdir(base):
        return 0
    try:
        names = [n for n in os.listdir(base)
                 if n.startswith("notes-") and n.endswith(".json")]
    except OSError:
        return 0
    total = 0
    for name in names:
        data = _read_json(os.path.join(base, name), {})
        items = data.get("notes") if isinstance(data, dict) else []
        if isinstance(items, list):
            total += sum(1 for n in items
                         if isinstance(n, dict)
                         and n.get("status") == "decayed")
    return total


def _run_eval_replay(pp: str, cfg: dict, state: dict, trigger: str,
                     llm=None) -> dict:
    """P3 评估回放：默认确定性指标；有 LLM 时只追加软摘要，不阻断。"""
    runs = _recent_run_records(pp)
    notes = _load_notes(pp)
    md = _read_text(_path(pp, DREAM_MD))

    token_cost = 0
    last_run_at = ""
    for r in runs:
        limits = r.get("limits") if isinstance(r.get("limits"), dict) else {}
        token_cost += _as_int(limits.get("estimated_tokens"), 0)
        ts = str(r.get("ts") or "")
        if ts > last_run_at:
            last_run_at = ts

    by_status = Counter(str(n.get("status") or "unknown") for n in notes)
    metrics = {
        "runs_total": len(runs),
        "token_cost": token_cost,
        "last_run_at": last_run_at,
        "notes_total": len(notes),
        "verified": by_status.get("verified", 0),
        "hypothesis": by_status.get("hypothesis", 0),
        "rejected": by_status.get("rejected", 0),
        "dream_hit": sum(1 for n in notes if str(n.get("last_hit_at") or "")),
        "false_memory": by_status.get("rejected", 0),
        "decayed": _decayed_count(pp),
        "dream_md_chars": len(md),
        "dream_md_tokens": round(len(md) / CHARS_PER_TOKEN),
    }

    llm = llm if llm is not None else _build_llm()
    llm_available = _llm_available(llm)
    summary: str = ""
    issues: List[str] = []
    est_tokens = 0
    max_input_chars = MAX_PROMPT_CHARS
    out_tokens = min(1024, MAX_OUTPUT_TOKENS)
    if llm_available:
        payload = json.dumps({
            "metrics": metrics,
            "dream_md_excerpt": _sanitize(md[:4000], pp),
        }, ensure_ascii=False, indent=1)
        if len(payload) > max_input_chars:
            payload = payload[:max_input_chars]
        messages = [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": payload + "\n\n" + _EVAL_REPLAY_PROMPT},
        ]
        try:
            response = llm.chat_completion(
                messages, temperature=0.2, max_tokens=out_tokens,
                response_format={"type": "json_object"})
            est_tokens = round((len(payload) + len(response or ""))
                               / CHARS_PER_TOKEN)
            from core.llm_integration import LLMIntegration
            if not LLMIntegration.is_error_response(response):
                data = LLMIntegration.parse_json_response(response)
                if isinstance(data, dict):
                    summary = _clean_text(data.get("summary"))[:600]
                    issues = [str(x)[:200] for x in (data.get("issues") or [])
                              if str(x).strip()][:10]
        except Exception as e:  # noqa: BLE001 —— 软摘要失败不阻断评估
            logger.debug(f"[dream] eval_replay LLM 摘要失败: {e}")

    now = _now()
    eval_id = (datetime.now().strftime("%Y%m%d-%H%M%S")
               + "-" + uuid.uuid4().hex[:4])
    eval_path = _path(pp, os.path.join(EVALS_DIR, eval_id + ".json"))
    limits = {"max_prompt_chars": max_input_chars,
              "max_output_tokens": out_tokens,
              "estimated_tokens": est_tokens}
    err = _write_json(eval_path, {
        "schema_version": SCHEMA_VERSION,
        "job": "eval_replay",
        "generated_at": now,
        "project_path": pp,
        "llm_available": llm_available,
        "metrics": metrics,
        "summary": summary,
        "issues": issues,
        "limits": limits,
    })
    if err:
        return _fail(pp, state, cfg, trigger, f"评估写入失败: {err}", {},
                     job="eval_replay")

    # 维护不是做梦：只更新 last_attempt_at，不动 last_dream_at。
    state.update({
        "last_attempt_at": now,
        "last_status": "ok",
        "last_error": "",
        "last_job": "eval_replay",
        "last_run_tokens": est_tokens,
        "runs": int(state.get("runs") or 0) + 1,
    })
    _save_state(pp, state)

    record = {
        "ts": now, "trigger": trigger, "job": "eval_replay", "status": "ok",
        "eval_id": eval_id, "eval_path": eval_path,
        "llm": {"available": llm_available,
                "source": ("主 LLM 配置" if llm_available
                           else "未配置有效主 LLM Key，仅确定性指标")},
        "metrics": metrics, "summary": summary, "issues": issues,
        "limits": limits,
    }
    run_path = _write_run(pp, record)

    from core.result_envelope import envelope
    env = envelope(
        f"评估回放完成：{metrics['runs_total']} 次运行、"
        f"{metrics['notes_total']} 条笔记"
        + ("" if llm_available else "（无 LLM，仅确定性指标）"),
        usable=True,
        cannot_conclude=["笔记的语义真值", "指标之外的历史行为"],
        guardrails=["指标来自落盘 runs/notes/archive，不补推断；"
                    "LLM summary 是软判断"],
    )
    return {
        "status": "ok", "tool": "coderef_dream", "action": "run",
        "project_path": pp, "trigger": trigger, "job": "eval_replay",
        "eval_id": eval_id, "eval_path": eval_path,
        "metrics": metrics, "summary": summary, "issues": issues,
        "llm_available": llm_available,
        "run_path": run_path, "limits": limits,
        "envelope": env,
    }


def _fail(pp: str, state: dict, cfg: dict, trigger: str, error: str,
          materials: dict, response: str = "",
          job: str = DEFAULT_JOB) -> dict:
    """做梦失败：保留旧梦，只记录错误（不写 DREAM.md / notes）。"""
    state["last_attempt_at"] = _now()
    state["last_status"] = "error"
    state["last_error"] = error[:500]
    state["last_job"] = job
    state["runs"] = int(state.get("runs") or 0) + 1
    _save_state(pp, state)
    record = {"ts": _now(), "trigger": trigger, "job": job, "status": "error",
              "error": error[:500], "response_preview": (response or "")[:500],
              "materials": {"salient": _salient_count(materials)},
              "limits": {"max_prompt_chars": MAX_PROMPT_CHARS,
                         "max_output_tokens": MAX_OUTPUT_TOKENS}}
    run_path = _write_run(pp, record)
    from core.result_envelope import envelope
    env = envelope(
        f"本次补梦失败：{error[:160]}",
        usable=False,
        blocked_by=[{"id": "dream_failed", "owner": "coderef",
                     "human": "补梦执行失败；旧梦保持不变，详见 run 记录"}],
        cannot_conclude=["本轮任何梦结论"],
        guardrails=["失败不覆盖旧梦；last_error 是事实，不是新梦"],
    )
    return {"status": "error", "tool": "coderef_dream", "action": "run",
            "project_path": pp, "error": error[:500], "run_path": run_path,
            "envelope": env}


# ═══════════════════════════════════════════════════════════════
# 启动补梦 / 供 memory_digest 注入
# ═══════════════════════════════════════════════════════════════

def _safe_run(pp: str) -> None:
    try:
        run(pp, trigger="startup")
    except Exception as e:  # noqa: BLE001 —— 后台补梦失败不能影响主流程
        logger.warning(f"[dream] 启动补梦失败: {e}")


def trigger_if_due(project_path: str) -> bool:
    """首次工具调用时启动后台补梦（每进程每项目一次，不阻塞首响应）。"""
    try:
        key = _norm(project_path)
        # 先占位再检查：之后的高频工具调用只做一次 set 查询，不重复读配置。
        with _start_lock:
            if key in _started:
                return False
            _started.add(key)
        pp = os.path.abspath(project_path)
        if not os.path.isdir(pp):
            return False
        cfg = load_config(pp)
        if not cfg["enabled"] or not cfg["run_on_startup"]:
            return False
        # 主 LLM 无有效 Key 时不启动后台线程：run() 会诚实 SKIP，
        # 启动补梦无需空转，也避免在只读/测试场景留下无意义的运行痕迹。
        if not _llm_available(_build_llm()):
            return False
        if not _is_due(_load_state(pp), cfg):
            return False
        threading.Thread(target=_safe_run, args=(pp,), daemon=True,
                         name="coderef-dream").start()
        return True
    except Exception as e:  # noqa: BLE001
        logger.debug(f"[dream] 启动补梦检查失败: {e}")
        return False


def digest_items(project_path: str, limit: int = 6) -> List[dict]:
    """把 triage 建议与 verified 笔记压成可注入条目（梦优先于操作记忆）。"""
    pp = os.path.abspath(project_path)
    notes = _load_notes(pp)
    limit = max(1, _as_int(limit, 6, minimum=1))

    out: List[dict] = []
    seen = set()

    def _add(kind: str, text: str) -> bool:
        key = _norm_text(text)
        if not key or key in seen:
            return True
        seen.add(key)
        out.append({"kind": kind, "text": text})
        return len(out) < limit

    # 1) triage 的 advice 先占位（「先看什么」比规则更该在开工时被看到）
    triage = [n for n in notes
              if n.get("job") == "triage"
              and n.get("claim_type") == "advice"
              and n.get("status") in ("verified", "hypothesis")][:3]
    for n in triage:
        if not _add("梦·开工建议", _clean_text(n.get("text"))[:180]):
            return out

    # 2) verified 笔记（原有顺序：规则 → 约定 → 坑 → 联想）
    order = {"rule": 0, "convention": 1, "pitfall": 2, "association": 3}
    verified = [n for n in notes if n.get("status") == "verified"]
    verified.sort(key=lambda n: order.get(str(n.get("kind")), 9))
    for n in verified:
        if len(out) >= limit:
            break
        text = _clean_text(n.get("text"))[:180]
        if not text:
            continue
        if not _add("梦·" + KIND_LABELS.get(str(n.get("kind")), "笔记"), text):
            break
    return out


def digest_meta(project_path: str) -> dict:
    pp = os.path.abspath(project_path)
    state = _load_state(pp)
    notes = _load_notes(pp)
    verified = [n for n in notes if n.get("status") == "verified"]
    triage_notes = [n for n in notes
                    if n.get("job") == "triage"
                    and n.get("claim_type") == "advice"
                    and n.get("status") in ("verified", "hypothesis")]
    jobs = sorted({str(n.get("job") or DEFAULT_JOB) for n in notes})
    return {
        "available": bool(verified or triage_notes),
        "last_dream_at": state.get("last_dream_at", ""),
        "verified_notes": len(verified),
        "triage_notes": len(triage_notes),
        "jobs": jobs,
        "guardrail": "梦是假设；引用前先核验 evidence，不得当作审计结论",
    }
