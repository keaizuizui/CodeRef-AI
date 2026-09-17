# -*- coding: utf-8 -*-
"""版本号唯一真源（v5.14.3 起集中管理）。

所有消费方均从此处取版本，避免多处写死漂移：
  - 根 __init__.py        ：`from core.version import __version__`
  - pyproject.toml        ：`[tool.setuptools.dynamic] version = {attr = "core.version.__version__"}`
  - core/mcp_server.py    ：`PKG_VERSION = _pkg_version()`（import 本模块）

升版本时只需改此文件一处。
"""
__version__ = "5.14.3"
