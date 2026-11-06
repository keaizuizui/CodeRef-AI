# -*- coding: utf-8 -*-
"""
checkpoint 断点续跑（core/pipeline_runner 拆解子模块）：_ckpt / _save / _load / _load_whitelist。

原为 pipeline_runner.py 1131~1155 行；拆出后由主文件 re-export，对外符号不变。
依赖方向：本模块依赖 pr_whitelist（_cdir/_phash/whitelist_list），供主文件与 pr_stages 使用。
"""

import os, json
from datetime import datetime
from typing import List

from loguru import logger

from core.pr_whitelist import _cdir, _phash, whitelist_list

# ══ 模块级函数（v4.9.5 存量债治理第三波提取：checkpoint/单维检测/报告入口） ══
def _ckpt(p: str) -> str:
    return os.path.join(_cdir(), f"{_phash(p)}.ckpt.json")

def _save(p: str, done: List[str]):
    try:
        cp = _ckpt(p)
        tmp = cp + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"done": done, "ts": datetime.now().isoformat()}, f)
        os.replace(tmp, cp)  # 原子替换，避免多 Agent 并发时读到半写入的检查点
    except Exception as e:  # noqa: BLE001
        # 检查点写失败只影响断点续跑，不影响本次审计结果
        logger.debug(f"[ckpt] 保存检查点失败（忽略）: {e}")

def _load(p: str) -> set:
    try:
        cp = _ckpt(p)
        if not os.path.exists(cp):
            return set()
        with open(cp, "r", encoding="utf-8") as f:
            return set(json.load(f).get("done", []))
    except Exception: return set()

def _load_whitelist(project_path: str) -> list:
    """加载 AI 白名单（供 _denoise 使用）"""
    return whitelist_list(project_path)
