# -*- coding: utf-8 -*-
"""HTTP 原始请求路径的安全判据（与具体服务实现无关）。

评估台服务（core/report_console.py）与画布静态服务（core/handlers_arch.py）
都需要在路径规范化**之前**拒绝含 `..` 的原始 path：一旦被 HTTP 层折叠，
`/.coderef/../index.html` 会变成 `/index.html` 而命中白名单（U-50）。

把判据放在中立模块，避免画布服务为了复用一个字符串函数而依赖评估台后端。
"""

from urllib.parse import unquote


def has_unsafe_path(raw_path: str) -> bool:
    """原始请求路径是否含 `..`（含 URL 编码 `%2e%2e` / `%2E%2E`）。

    按原始 path 判定，拒绝后不影响 `/`、`/index.html`、`/.coderef/arch_*.html`
    等正常路由。逐层解码 3 次，覆盖 `%252e%252e` 这类双重编码。
    """
    raw = raw_path or ""
    for _ in range(3):
        if ".." in raw:
            return True
        decoded = unquote(raw)
        if decoded == raw:
            break
        raw = decoded
    return False
