# -*- coding: utf-8 -*-
"""report_console — 项目评估台的回环轻后端（v5.17.0）。

背景：评估台页面（`coderef-report/html/index.html`）需要"点按钮即评估"，
但又不能把任意文件/路径暴露给浏览器。本模块起一个**仅回环**的
`ThreadingHTTPServer`（仿 core/gov_webdash.py 的成熟范式），只暴露显式白名单端点：

    GET  /                      评估台页面（注入一次性 token 供写操作使用）
    GET  /audit.html|kg.html|wiki.html   既有子页
    GET  /.coderef/arch_canvas_*.html   业务架构画布（iframe 引用）
    GET  /api/scorecard?refresh=0|1     记分卡（refresh=1 重算并落盘）
    GET  /api/health                    健康探针
    POST /api/config                    权重/阈值（写 <project>/.coderef/eval_config.json）
    POST /api/evaluate                  评估场景（P0 固定 501 NOT_IMPLEMENTED）

安全约束（缺一不通过）：
  1. 仅回环绑定（127.0.0.1 / ::1 / localhost），其他 host 直接 ValueError；
  2. 项目路径在 serve() 时固定，端点不接受前端传路径；
  3. 无任何 CORS 响应头；
  4. POST 需同源 Origin + JSON Content-Type + 一次性 token 头；
  5. 请求体上限 64KB；
  6. 不启用目录列举/任意静态文件读取，只路由白名单端点；
  7. 日志只记方法与路径，不记请求体。
"""

import json
import os
import secrets
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, Optional, Tuple
from urllib.parse import parse_qs, urlparse

from loguru import logger

_MAX_BODY = 64 * 1024
_LOOPBACK_HOSTS = ("127.0.0.1", "::1", "localhost")
_SERVERS: Dict[str, Dict[str, Any]] = {}
_LOCK = threading.Lock()


def _normalize_host(host: str) -> str:
    h = (host or "").strip().lower()
    if h not in _LOOPBACK_HOSTS:
        raise ValueError(f"仅允许回环地址（127.0.0.1 / ::1 / localhost），收到: {host!r}")
    return "127.0.0.1" if h in ("localhost", "::1") else h


def _guidance_html(project_path: str, out_dir: str) -> str:
    return f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="UTF-8"><title>项目评估台 · 尚未生成</title>
<style>body{{background:#0f1117;color:#e5e7eb;font-family:-apple-system,"Segoe UI",
"Microsoft YaHei",sans-serif;padding:48px;line-height:1.7}}
.card{{max-width:760px;margin:0 auto;background:#1a1d2e;border:1px solid #2a2d3a;
border-radius:12px;padding:28px 32px}}h1{{font-size:20px;margin-bottom:14px}}
code{{background:#14161f;border:1px solid #2a2d3a;border-radius:4px;padding:2px 6px;
color:#93c5fd;font-size:13px}}.muted{{color:#9ca3af;font-size:13px}}</style></head>
<body><div class="card"><h1>项目评估台尚未生成</h1>
<p>未找到评估台页面：<code>{out_dir}</code></p>
<p class="muted">请先用 MCP 工具 <code>coderef_report</code> 生成报告目录（
<code>open_server=true</code> 会自动起本服务并打开页面），或先跑一次
<code>coderef_audit</code>。</p>
<p class="muted">当前项目：<code>{project_path}</code></p></div></body></html>"""


def _make_handler(project_path: str, out_dir: str, token: str,
                  cache: Dict[str, Any]):
    class Handler(BaseHTTPRequestHandler):
        server_version = "CodeRefEval/5.17"

        # ── 基础响应 ──
        def _json(self, obj: Any, code: int = 200) -> None:
            body = json.dumps(obj, ensure_ascii=False, default=str).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(body)

        def _html(self, text: str, code: int = 200) -> None:
            body = text.encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, fmt, *args):  # noqa: N802
            return  # 静默：只记业务异常，不记访问噪声

        # ── 路由 ──
        def do_GET(self):  # noqa: N802
            try:
                self._route_get()
            except Exception as e:  # noqa: BLE001
                logger.error(f"[report_console] GET 异常 {self.path}: {e}")
                self._json({"ok": False, "error": "内部错误"}, 500)

        def do_POST(self):  # noqa: N802
            try:
                self._route_post()
            except Exception as e:  # noqa: BLE001
                logger.error(f"[report_console] POST 异常 {self.path}: {e}")
                self._json({"ok": False, "error": "内部错误"}, 500)

        # ── 安全 ──
        def _same_origin(self) -> bool:
            origin = self.headers.get("Origin")
            if not origin:
                return True
            o = urlparse(origin)
            port = self.server.server_address[1]
            return (o.scheme == "http" and o.hostname in _LOOPBACK_HOSTS
                    and (o.port or 80) == port)

        def _post_guard(self) -> Optional[Tuple[int, str]]:
            """POST 三道闸：同源 + JSON + token。返回 (错误码, 原因) 或 None。"""
            if not self._same_origin():
                return 403, "跨源请求被拒绝"
            site = (self.headers.get("Sec-Fetch-Site") or "").lower()
            if site in ("cross-site", "same-site"):
                return 403, "跨站请求被拒绝"
            ctype = (self.headers.get("Content-Type") or "").split(";")[0].strip().lower()
            if ctype != "application/json":
                return 415, "Content-Type 必须为 application/json"
            if self.headers.get("X-CodeRef-Token") != token:
                return 403, "缺少或错误的 X-CodeRef-Token"
            return None

        def _read_body(self) -> Tuple[Optional[dict], Optional[str]]:
            try:
                length = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                return None, "Content-Length 非法"
            if length <= 0:
                return {}, None
            if length > _MAX_BODY:
                return None, f"请求体超过 {_MAX_BODY} 字节上限"
            raw = self.rfile.read(length)
            try:
                data = json.loads(raw.decode("utf-8"))
            except Exception:  # noqa: BLE001
                return None, "请求体不是合法 JSON"
            if not isinstance(data, dict):
                return None, "请求体必须是 JSON 对象"
            return data, None

        # ── GET 路由 ──
        def _route_get(self) -> None:
            parsed = urlparse(self.path)
            path = parsed.path
            if path in ("/", "/index.html"):
                return self._serve_index()
            if path in ("/audit.html", "/kg.html", "/wiki.html"):
                return self._serve_subpage(path.lstrip("/"))
            if path.startswith("/.coderef/"):
                return self._serve_arch(path[len("/.coderef/"):])
            if path == "/api/scorecard":
                return self._api_scorecard(parse_qs(parsed.query))
            if path == "/api/health":
                from core.version import __version__
                return self._json({"ok": True, "version": __version__,
                                   "project": os.path.basename(project_path.rstrip(os.sep))})
            return self._json({"ok": False, "error": f"404 {path}"}, 404)

        def _serve_index(self) -> None:
            fp = os.path.join(out_dir, "index.html")
            if not os.path.isfile(fp):
                return self._html(_guidance_html(project_path, out_dir), 200)
            try:
                with open(fp, "r", encoding="utf-8") as f:
                    text = f.read()
            except Exception as e:  # noqa: BLE001
                return self._json({"ok": False, "error": f"读取页面失败: {e}"}, 500)
            # 注入一次性 token：页面里的写操作（P1 起）带 X-CodeRef-Token
            meta = f'<meta name="coderef-token" content="{token}">'
            if "</head>" in text:
                text = text.replace("</head>", meta + "</head>", 1)
            return self._html(text, 200)

        def _serve_subpage(self, name: str) -> None:
            fp = os.path.join(out_dir, os.path.basename(name))
            if not os.path.isfile(fp):
                return self._json({"ok": False, "error": f"404 {name}"}, 404)
            try:
                with open(fp, "r", encoding="utf-8") as f:
                    return self._html(f.read(), 200)
            except Exception as e:  # noqa: BLE001
                return self._json({"ok": False, "error": f"读取失败: {e}"}, 500)

        def _serve_arch(self, name: str) -> None:
            # 只放行 .coderef 下的业务架构画布产物；不允许分隔符/目录穿越
            fn = os.path.basename(name)
            if fn != name or not fn.startswith("arch_canvas_") or not fn.endswith(".html"):
                return self._json({"ok": False, "error": "404"}, 404)
            fp = os.path.join(project_path, ".coderef", fn)
            if not os.path.isfile(fp):
                return self._json({"ok": False, "error": "404"}, 404)
            try:
                with open(fp, "r", encoding="utf-8") as f:
                    return self._html(f.read(), 200)
            except Exception as e:  # noqa: BLE001
                return self._json({"ok": False, "error": f"读取失败: {e}"}, 500)

        def _api_scorecard(self, qs: Dict[str, Any]) -> None:
            from core.scorecard import compute, write_card
            refresh = str((qs.get("refresh") or ["0"])[0]).lower() in ("1", "true", "yes")
            with _LOCK:
                card = cache.get("card")
            if card is None or refresh:
                # 重计算放在锁外：避免秒级扫描阻塞配置写入与服务句柄增删
                card = compute(project_path)
                write_card(project_path, card)
                with _LOCK:
                    cache["card"] = card
            return self._json(card)

        # ── POST 路由 ──
        def _route_post(self) -> None:
            parsed = urlparse(self.path)
            path = parsed.path
            if path not in ("/api/config", "/api/evaluate"):
                return self._json({"ok": False, "error": f"404 {path}"}, 404)
            err = self._post_guard()
            if err:
                code, reason = err
                return self._json({"ok": False, "error": reason}, code)
            data, bad = self._read_body()
            if bad:
                code = 413 if "上限" in bad else 400
                return self._json({"ok": False, "error": bad}, code)

            if path == "/api/evaluate":
                scene = str((data or {}).get("scene") or "")
                return self._json({
                    "ok": False,
                    "status": "NOT_IMPLEMENTED",
                    "scene": scene,
                    "message": ("产品评估（v5.17.1）与 UX 人格走查（v5.18.0）尚未接入；"
                                "本版不产出任何评估结论，避免占位内容被误当真实判断。"),
                }, 501)

            try:
                from core.scorecard import save_config
                saved = save_config(project_path, data or {})
            except ValueError as e:
                return self._json({"ok": False, "error": str(e)}, 400)
            except Exception as e:  # noqa: BLE001
                return self._json({"ok": False, "error": f"配置保存失败: {e}"}, 500)
            with _LOCK:
                cache["card"] = None  # 权重变更后下次读取重算
            return self._json({"ok": True, **saved})

    return Handler


def serve(project_path: str, output_dir: Optional[str] = None,
          host: str = "127.0.0.1", port: int = 0) -> dict:
    """启动评估台回环服务（进程内常驻线程；同项目重复调用先关闭旧句柄）。"""
    pp = os.path.abspath(project_path)
    if not os.path.isdir(pp):
        raise ValueError(f"项目路径不存在: {pp}")
    host = _normalize_host(host)
    out_dir = os.path.abspath(output_dir or os.path.join(pp, "coderef-report", "html"))
    if not os.path.isdir(out_dir):
        os.makedirs(out_dir, exist_ok=True)

    token = secrets.token_urlsafe(24)
    cache: Dict[str, Any] = {"card": None}
    handler = _make_handler(pp, out_dir, token, cache)

    with _LOCK:
        old = _SERVERS.pop(pp, None)
    if old:
        try:
            old["httpd"].shutdown()
            old["httpd"].server_close()
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[report_console] 关闭旧服务失败（忽略）: {e}")

    httpd = ThreadingHTTPServer((host, port), handler)
    httpd.daemon_threads = True
    th = threading.Thread(target=httpd.serve_forever, daemon=True,
                          name=f"coderef-eval-{httpd.server_address[1]}")
    th.start()
    url = f"http://{host}:{httpd.server_address[1]}/"
    with _LOCK:
        _SERVERS[pp] = {"httpd": httpd, "thread": th, "url": url,
                        "output_dir": out_dir}
    logger.info(f"[report_console] 评估台已启动: {url} → {out_dir}")
    return {"ok": True, "url": url, "host": host,
            "port": httpd.server_address[1], "output_dir": out_dir}
