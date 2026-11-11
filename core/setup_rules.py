# -*- coding: utf-8 -*-
"""把「记忆契约」安装到编辑器会自动读取的规则文件里。

背景：MCP 服务端无法主动呼叫编程 AI；它能稳定触达的只有编辑器启动时加载的
规则文件（AGENTS.md / CLAUDE.md / .cursor/rules / copilot instructions）。
本模块负责在这些文件里维护一个带 marker 的短规则块：

  - 幂等：重复安装只更新 marker 块，不动手写内容；
  - 可回滚：写前备份，支持 uninstall；
  - 可审计：块头带 integration version，便于判断旧版本；
  - 无 LLM 依赖：纯文件操作，安装阶段即可完成。

调用方：`coderef_operation_memory` 的 setup_* action、MCP 首次调用自动 setup。
"""

import os
import re
import time
from typing import List, Optional

INTEGRATION_VERSION = 1

MARKER_START = "<!-- CODEREF-MEMORY-RULE:START version={version} -->"
MARKER_END = "<!-- CODEREF-MEMORY-RULE:END -->"
_MARKER_RE = re.compile(
    r"<!-- CODEREF-MEMORY-RULE:START(?P<attrs>[^>]*)-->.*?<!-- CODEREF-MEMORY-RULE:END -->",
    re.DOTALL)

RULE_BODY = """## CodeRef 记忆契约（自动生成，勿手改块内）
- 开始任何改码 / 审计 / 发布前，必须先调用 `coderef_operation_memory(action=recover)`。
- 返回 `status != ok` 时如实说明，不得假装有记忆；梦笔记是假设，引用前先核验 evidence。
- 改码后必须调用 `coderef_change_guard`。
- 若当前会话在安装/升级前就已开始，规则不会热加载；请粘贴：
  `请先调用 coderef_operation_memory(action=recover)，之后每次改码前都先调用。`
"""


def activation_line() -> str:
    return "请先调用 coderef_operation_memory(action=recover)，之后每次改码前都先调用。"


def _block(version: int = INTEGRATION_VERSION) -> str:
    return (MARKER_START.format(version=version) + "\n"
            + RULE_BODY + MARKER_END)


def _project_targets(project_path: str) -> List[str]:
    pp = os.path.abspath(project_path)
    return [
        os.path.join(pp, "AGENTS.md"),
        os.path.join(pp, "CLAUDE.md"),
        os.path.join(pp, ".cursor", "rules", "coderef.mdc"),
        os.path.join(pp, ".github", "copilot-instructions.md"),
    ]


def _global_targets() -> List[str]:
    home = os.path.expanduser("~")
    return [
        os.path.join(home, ".claude", "CLAUDE.md"),
        os.path.join(home, ".cursor", "rules", "coderef.mdc"),
        os.path.join(home, ".codex", "AGENTS.md"),
    ]


def _read(path: str) -> str:
    try:
        with open(path, encoding="utf-8") as f:
            return f.read()
    except OSError:
        return ""


def _installed_version(text: str) -> int:
    m = _MARKER_RE.search(text or "")
    if not m:
        return 0
    vm = re.search(r"version=(\d+)", m.group("attrs") or "")
    return int(vm.group(1)) if vm else 1


def _is_under(path: str, root: str) -> bool:
    try:
        return os.path.commonpath([os.path.abspath(path), root]) == root
    except ValueError:  # Windows 跨盘符
        return False


def _backup(path: str, root: str) -> str:
    try:
        base = os.path.join(root, ".coderef", "backups")
        os.makedirs(base, exist_ok=True)
        name = os.path.basename(path)
        dst = os.path.join(base, f"{name}.{time.strftime('%Y%m%d-%H%M%S')}.bak")
        with open(path, "rb") as src, open(dst, "wb") as out:
            out.write(src.read())
        return dst
    except OSError:
        return ""


def _atomic_write(path: str, text: str) -> bool:
    tmp = path + ".tmp"
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(tmp, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
        os.replace(tmp, path)
        return True
    except OSError:
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except OSError:
            pass
        return False


def _apply(text: str) -> str:
    block = _block()
    if _MARKER_RE.search(text or ""):
        return _MARKER_RE.sub(block, text, count=1)
    if not text:
        return block + "\n"
    return text.rstrip() + "\n\n" + block + "\n"


def _target_status(path: str) -> dict:
    exists = os.path.isfile(path)
    text = _read(path) if exists else ""
    version = _installed_version(text)
    return {"path": path, "exists": exists,
            "installed": version > 0, "version": version}


def status(project_path: str, include_global: bool = True) -> dict:
    """返回接入状态：哪些规则文件已写、版本是否落后、梦是否从未启动。"""
    pp = os.path.abspath(project_path)
    project = [_target_status(p) for p in _project_targets(pp)]
    global_files = [_target_status(p) for p in _global_targets()] if include_global else []
    installed = [f for f in project + global_files if f["installed"]]
    stale = [f for f in installed if f["version"] < INTEGRATION_VERSION]
    try:
        from core import dream
        state = dream._load_state(pp)
        notes = dream._load_notes(pp)
        dream_never_ran = not bool(state.get("last_dream_at")) and not notes
    except Exception:  # noqa: BLE001 —— 梦状态不可读不阻断接入判定
        dream_never_ran = False
    return {
        "status": "ok",
        "integration_version": INTEGRATION_VERSION,
        "project": project,
        "global": global_files,
        "installed_count": len(installed),
        "stale_count": len(stale),
        "needs_setup": (not installed) or bool(stale),
        "dream_never_ran": dream_never_ran,
        "activation_line": activation_line(),
    }


def memory_rule_installed(project_path: str) -> bool:
    """项目规则文件里是否已有记忆契约（任一目标即可）。"""
    pp = os.path.abspath(project_path)
    return any(_installed_version(_read(p)) > 0 for p in _project_targets(pp))


def install(project_path: str, targets: str = "project",
            include_global: bool = False, dry_run: bool = False,
            create_missing: bool = True) -> dict:
    """安装/升级 marker 块。默认只写项目级，避免未经确认改用户全局文件。"""
    pp = os.path.abspath(project_path)
    if not os.path.isdir(pp):
        return {"status": "error", "message": "项目路径不存在"}
    paths: List[str] = []
    if targets in ("project", "auto", "all"):
        paths += _project_targets(pp)
    if include_global or targets in ("global", "all"):
        paths += _global_targets()

    written: List[dict] = []
    skipped: List[dict] = []
    for path in paths:
        exists = os.path.isfile(path)
        is_agents = os.path.basename(path) == "AGENTS.md" and \
            os.path.dirname(path) == pp
        # 项目级：AGENTS.md 可新建；其余只在文件已存在时更新，避免造噪音
        if not exists and not (create_missing and is_agents):
            skipped.append({"path": path, "reason": "文件不存在"})
            continue
        old = _read(path) if exists else ""
        new = _apply(old)
        if old == new:
            skipped.append({"path": path, "reason": "已是最新"})
            continue
        if dry_run:
            written.append({"path": path, "dry_run": True, "changed": True})
            continue
        root = pp if _is_under(path, pp) else os.path.expanduser("~")
        backup = _backup(path, root) if exists else ""
        if not _atomic_write(path, new):
            skipped.append({"path": path, "reason": "写入失败"})
            continue
        written.append({"path": path, "backup": backup, "changed": True})

    return {
        "status": "ok",
        "integration_version": INTEGRATION_VERSION,
        "dry_run": bool(dry_run),
        "written": written,
        "skipped": skipped,
        "activation_line": activation_line(),
        "requires_new_conversation": bool(written),
    }


def uninstall(project_path: str, include_global: bool = False,
              dry_run: bool = False) -> dict:
    """只删除 marker 块，保留用户手写内容。"""
    pp = os.path.abspath(project_path)
    paths = _project_targets(pp) + (_global_targets() if include_global else [])
    removed: List[dict] = []
    for path in paths:
        if not os.path.isfile(path):
            continue
        old = _read(path)
        if not _MARKER_RE.search(old):
            continue
        if dry_run:
            removed.append({"path": path, "dry_run": True})
            continue
        root = pp if _is_under(path, pp) else os.path.expanduser("~")
        backup = _backup(path, root)
        new = _MARKER_RE.sub("", old).rstrip() + "\n"
        if not _atomic_write(path, new):
            continue
        removed.append({"path": path, "backup": backup})
    return {"status": "ok", "removed": removed, "dry_run": bool(dry_run)}


def _looks_like_project(project_path: str) -> bool:
    """只对真实工程做自动 setup，避免往测试/空目录写规则。"""
    pp = os.path.abspath(project_path)
    if not os.path.isdir(pp):
        return False
    for name in (".git", "pyproject.toml", "package.json", "requirements.txt",
                 "setup.py", "Cargo.toml", "go.mod", "pom.xml",
                 "composer.json", "Gemfile"):
        if os.path.exists(os.path.join(pp, name)):
            return True
    try:
        for name in os.listdir(pp):
            if os.path.splitext(name)[1].lower() in (
                    ".py", ".js", ".ts", ".tsx", ".java",
                    ".go", ".rs", ".php", ".rb"):
                return True
    except OSError:
        pass
    return False


def ensure_project_rule(project_path: str) -> Optional[dict]:
    """首次调用自动 setup：只在需要时写项目级规则，返回本次结果或 None。"""
    try:
        if not _looks_like_project(project_path):
            return None
        info = status(project_path, include_global=False)
        if not info.get("needs_setup"):
            return None
        res = install(project_path, targets="project", include_global=False)
        res["auto"] = True
        res["status_before"] = {
            "stale_count": info.get("stale_count", 0),
            "installed_count": info.get("installed_count", 0),
            "dream_never_ran": info.get("dream_never_ran", False),
        }
        return res
    except Exception:  # noqa: BLE001 —— 自动 setup 失败不阻断工具主流程
        return None
