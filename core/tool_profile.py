# -*- coding: utf-8 -*-
"""MCP 工具面 profile（5.21.0）：只控制 tools/list 暴露面，不删工具与 handler。

优先级：环境变量 CODEREF_TOOL_PROFILE > 配置文件顶层 tool_profile > 默认 full。
合法值 lite / full（大小写不敏感）；非法值回退 full 并在返回值中如实报告 error。
隐藏工具仍可按名字直接调用（_call / _run 不经过本模块）。
"""
import json
import logging
import os
from typing import List

logger = logging.getLogger("coderef")

PROFILE_ENV = "CODEREF_TOOL_PROFILE"

# lite 清单（与 docs/dream-jobs.md 6.3 同源，严格 12 个）
LITE_TOOLS = frozenset({
    "coderef_version",
    "coderef_plan",
    "coderef_execute",
    "coderef_dispatch_status",
    "coderef_dispatch_report",
    "coderef_dream",
    "coderef_task_status",
    "coderef_scope",
    "coderef_operation_memory",
    "coderef_verify_findings",
    "coderef_change_guard",
    "coderef_audit",
})

_VALID_PROFILES = ("lite", "full")
_DEFAULT_PROFILE = "full"


def _config_candidates() -> List[str]:
    """与 core/dream.py 的 _config_candidates() 同口径。"""
    return [
        os.environ.get("CODEREF_CONFIG_PATH", ""),
        os.path.join(os.path.expanduser("~"), ".coderef", "config.json"),
        os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                     "config", "config.json"),
        os.path.join(os.getcwd(), "config", "config.json"),
    ]


def _normalize(value) -> str:
    """合法值返回小写 profile；非法返回值原样转字符串（用于错误报告）。"""
    text = str(value).strip().lower()
    return text if text in _VALID_PROFILES else str(value)


def _config_profile() -> str:
    """读第一个存在配置文件的顶层 tool_profile；没有则返回空串。"""
    for path in [p for p in _config_candidates() if p]:
        if not os.path.exists(path):
            continue
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:  # noqa: BLE001
            logger.debug(f"[tool_profile] 配置读取失败 {path}: {e}")
            continue
        if not isinstance(data, dict):
            continue
        value = data.get("tool_profile")
        if value is not None:
            return value
    return ""


def current_profile() -> dict:
    """返回 {"profile", "source", "error"}；任何异常都收敛为 full。"""
    try:
        env_value = os.environ.get(PROFILE_ENV, "")
        if str(env_value).strip():
            normalized = _normalize(env_value)
            if normalized in _VALID_PROFILES:
                return {"profile": normalized, "source": "env", "error": ""}
            return {"profile": _DEFAULT_PROFILE, "source": "env",
                    "error": f"非法 {PROFILE_ENV}={env_value!r}，已回退 full"}
        config_value = _config_profile()
        if config_value != "":
            normalized = _normalize(config_value)
            if normalized in _VALID_PROFILES:
                return {"profile": normalized, "source": "config", "error": ""}
            return {"profile": _DEFAULT_PROFILE, "source": "config",
                    "error": f"非法配置 tool_profile={config_value!r}，已回退 full"}
        return {"profile": _DEFAULT_PROFILE, "source": "default", "error": ""}
    except Exception as e:  # noqa: BLE001 —— profile 读取失败不得影响工具面
        return {"profile": _DEFAULT_PROFILE, "source": "default",
                "error": f"profile 读取异常，已回退 full: {e}"}


def filter_tools(tools: list) -> list:
    """full 原样返回；lite 只保留 LITE_TOOLS 内的工具，保持原顺序。"""
    try:
        if current_profile()["profile"] != "lite":
            return tools
        return [t for t in tools if isinstance(t, dict) and t.get("name") in LITE_TOOLS]
    except Exception:  # noqa: BLE001
        return tools
