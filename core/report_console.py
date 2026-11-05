# -*- coding: utf-8 -*-
"""report_console — 项目评估台的回环轻后端（v5.18.0）。

背景：评估台页面（`coderef-report/html/index.html`）需要"点按钮即评估"，
但又不能把任意文件/路径暴露给浏览器。本模块起一个**仅回环**的
`ThreadingHTTPServer`（仿 core/gov_webdash.py 的成熟范式），只暴露显式白名单端点：

    GET  /                      评估台页面（注入一次性 token 供写操作使用）
    GET  /audit.html|kg.html|wiki.html   既有子页
    GET  /.coderef/arch_canvas_*.html   业务架构画布（iframe 引用）
    GET  /.coderef/arch_flow_*.html     业务流程泳道视图（iframe 引用）
    GET  /api/scorecard?refresh=0|1     记分卡（refresh=1 重算并落盘）
    GET  /api/health                    健康探针
    POST /api/config                    权重/阈值（写 <project>/.coderef/eval_config.json）
    POST /api/evaluate                  产品（F 轨）/ UX（U 轨）评估，写 product_fit/ux_fit.json

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
from typing import Any, Callable, Dict, Optional, Tuple
from urllib.parse import parse_qs, urlparse

from loguru import logger

_MAX_BODY = 64 * 1024
_LOOPBACK_HOSTS = ("127.0.0.1", "::1", "localhost")
_SERVERS: Dict[str, Dict[str, Any]] = {}
_LOCK = threading.Lock()
# 同一项目同一场景的评估串行（LLM 慢调用时不重复跑）；不同场景可并行
_EVAL_LOCKS: Dict[str, threading.Lock] = {}
# 页面重渲染串行：产品/UX 两个场景可能同时收尾，render 写文件非原子
_RENDER_LOCK = threading.Lock()


def _eval_lock(key: str) -> threading.Lock:
    with _LOCK:
        return _EVAL_LOCKS.setdefault(key, threading.Lock())


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
                  cache: Dict[str, Any], rerender: Optional[Callable[[], Any]] = None):
    class Handler(BaseHTTPRequestHandler):
        server_version = "CodeRefEval/5.18"

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
        def _host_ok(self) -> bool:
            """校验 Host 头，阻断 DNS rebinding（恶意域名解析到 127.0.0.1 后读取数据）。"""
            host = (self.headers.get("Host") or "").strip().lower()
            if not host:
                return False
            if host.startswith("["):
                hostname = host[1:].split("]", 1)[0]
            else:
                hostname = host.rsplit(":", 1)[0]
            return hostname in _LOOPBACK_HOSTS

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
            if not self._host_ok():
                return self._json({"ok": False, "error": "非法 Host"}, 403)
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
            # 只放行 .coderef 下的画布产物（业务树 arch_canvas_* / 业务流程 arch_flow_*）；
            # 不允许分隔符/目录穿越
            fn = os.path.basename(name)
            prefixes = ("arch_canvas_", "arch_flow_")
            if (fn != name or not fn.endswith(".html")
                    or not fn.startswith(prefixes)):
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

        def _refresh_page(self) -> Tuple[bool, str]:
            """评估产物落盘后重渲染评估台；返回 (是否产出新页面, 失败原因)。

            重渲染未产出新页面时必须让调用方感知（U-49：此前只记日志，
            页面仍显示"正在刷新…"却拿到旧快照）。
            """
            if rerender is None:
                return False, "未注入重渲染回调，页面未刷新"
            try:
                with _RENDER_LOCK:
                    rerender()
                return True, ""
            except Exception as e:  # noqa: BLE001
                logger.warning(f"[report_console] 评估后台页重渲染失败: {e}")
                return False, str(e)

        def _api_evaluate(self, scene: str, data: dict) -> None:
            """产品（F 轨）/ UX（U 轨）评估：同项目同场景串行，不同场景可并行。"""
            lock = _eval_lock(f"{project_path}:{scene}")
            if not lock.acquire(blocking=False):
                return self._json({
                    "ok": False, "status": "BUSY", "scene": scene,
                    "message": "同一项目同一场景正在评估，请稍后重试",
                }, 409)
            try:
                if scene == "product":
                    from core.product_eval import evaluate as eval_product
                    res = eval_product(
                        project_path,
                        stage=data.get("stage") or None,
                        scope=data.get("scope") or None)
                else:
                    from core.ux_eval import evaluate as eval_ux
                    res = eval_ux(
                        project_path,
                        persona=data.get("persona"),
                        journey=str(data.get("journey") or ""),
                        problems=data.get("problems") or None,
                        viewports=data.get("viewports") or None)
                refreshed, refresh_err = self._refresh_page()
                res["page_refreshed"] = refreshed
                if not refreshed:
                    res["page_refresh_error"] = refresh_err
                res.setdefault("ok", res.get("status") != "ERROR")
                return self._json(res, 400 if res.get("status") == "ERROR" else 200)
            except Exception as e:  # noqa: BLE001
                logger.error(f"[report_console] 评估失败 scene={scene}: {e}")
                return self._json({"ok": False, "status": "ERROR",
                                   "scene": scene, "error": f"评估失败: {e}"}, 500)
            finally:
                lock.release()

        # ── POST 路由 ──
        def _route_post(self) -> None:
            if not self._host_ok():
                return self._json({"ok": False, "error": "非法 Host"}, 403)
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
                scene = str((data or {}).get("scene") or "").strip().lower()
                if scene not in ("product", "ux"):
                    return self._json(
                        {"ok": False, "error": "scene 必须为 product 或 ux"}, 400)
                return self._api_evaluate(scene, data or {})

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
          host: str = "127.0.0.1", port: int = 0,
          rerender: Optional[Callable[[], Any]] = None) -> dict:
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
    handler = _make_handler(pp, out_dir, token, cache, rerender)

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
