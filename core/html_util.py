# -*- coding: utf-8 -*-
"""HTML 转义的公共实现（渲染器与内容面板共用，避免两处各写一遍）。"""

import html as _html


def esc(s) -> str:
    """文本上下文转义（不转义引号）。"""
    return _html.escape(str(s), quote=False)


def attr(s) -> str:
    """属性上下文转义（引号也转义），用于 href/id 等属性值。"""
    return _html.escape(str(s or ""), quote=True)
