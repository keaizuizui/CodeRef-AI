# -*- coding: utf-8 -*-
"""项目路径归一：所有「按路径派生项目标识」的唯一入口。

Windows 下 `D:\\proj` 与 `d:\\proj` 是同一项目，但直接对 abspath 做 md5
会得到两个哈希，导致同一项目分裂出两套图谱库 / 审计发现 / 状态文件
（登记册 U-71）。这里统一 abspath + normcase，保证同一项目只有一份标识。
"""
import os


def project_key(project_path) -> str:
    """返回归一后的项目路径（绝对路径 + normcase）。

    Windows：去大小写差异、统一分隔符；Linux/macOS：仅绝对化。
    非法路径退回原字符串，不中断调用方。
    """
    try:
        return os.path.normcase(os.path.abspath(str(project_path)))
    except Exception:  # noqa: BLE001
        return str(project_path)
