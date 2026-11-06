# -*- coding: utf-8 -*-
"""F 轨（product_eval）与 U 轨（ux_eval）共用的评估基础规则。

两条评估轨都需要：把路径相对项目根、校验证据锚点是否存在、把 LLM 输出解析成
JSON 对象、把结论原子落盘。这些规则此前各写一份，改动时容易只改一条轨，导致
两条轨对同类输入的严格度悄悄分叉。集中到这里后，两轨只保留各自的 schema 校验
与 prompt 差异。
"""

import json
import os
import re
from typing import Optional


def rel(project_path: str, path: str) -> str:
    """把绝对路径转成相对项目根、以 `/` 分隔的形式；失败时退回原路径。"""
    try:
        return os.path.relpath(path, project_path).replace(os.sep, "/")
    except Exception:  # noqa: BLE001
        return path.replace(os.sep, "/")


def anchor_exists(project_path: str, anchor: str) -> bool:
    """证据锚点（形如 `path#L12`）对应的文件是否真实存在。"""
    fp = str(anchor or "").split("#L")[0]
    return bool(fp) and os.path.isfile(os.path.join(project_path, fp))


def parse_json_object(raw: str) -> Optional[dict]:
    """从 LLM 文本中取出第一个 JSON 对象；解析失败返回 None，不抛异常。"""
    if not raw:
        return None
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw.strip())
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        data = json.loads(text[start:end + 1])
    except Exception:  # noqa: BLE001
        return None
    return data if isinstance(data, dict) else None


def write_json_result(project_path: str, filename: str, result: dict) -> str:
    """把评估结论原子写入 `<project>/.coderef/<filename>`，返回落盘路径。"""
    from core.scorecard import _atomic_write_json
    out = os.path.join(os.path.abspath(project_path), ".coderef", filename)
    _atomic_write_json(out, result)
    return out
