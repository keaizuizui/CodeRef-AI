# -*- coding: utf-8 -*-
"""
前端交互审查模块（FrontendInspector）

针对「纯 Python + HTML」的 vibe coding 产物，进行前端交互层面的审计。

三阶段设计中的静态部分 + 可选运行时：
1. 静态枚举：用 Python 标准库 html.parser 扫描 HTML，枚举所有按钮与菜单树。
2. LLM 审查：对每个按钮/菜单节点按 6 个维度（交互正确性、反馈缺失、禁用与边界、
   可达性、错误处理、一致性）构造 prompt，调用 core.llm_integration.LLMIntegration
   进行审查，并把结果解析为 findings。
3. 运行时抽查（可选）：mode="runtime" 时尝试调用浏览器自动化抽查关键路径；
   若环境无浏览器或运行失败，则降级为静态结论并明确说明。

约束：
- 仅使用 Python 标准库 + 复用 core/llm_integration.py，不新增第三方依赖。
- 文件可独立导入：from core.frontend_inspector import FrontendInspector
- 所有对外可读文本使用中文。
- 使用 loguru logger，不静默吞异常。
- magic number 集中定义为模块级常量。
"""

import os
import re
import time
import ipaddress
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Dict, List, Optional
from dataclasses import dataclass, field
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse
from urllib.request import Request, urlopen, build_opener, HTTPRedirectHandler
import urllib.error

from loguru import logger

from core.llm_integration import LLMIntegration


# ─────────────────────────── 模块级常量（magic number 集中定义） ───────────────────────────

# 文件扫描相关
MAX_FILES_SCAN = 200            # 最多扫描的 HTML 文件数
MAX_HTML_FILE_SIZE = 2 * 1024 * 1024  # 单个 HTML 文件大小上限（字节），超过则跳过
IGNORED_DIRS = {".git", ".node_modules", "node_modules", "__pycache__", "dist", "build", ".venv", "venv"}

# HTML 空元素（void elements）：无结束标签，遇到即视为闭合
VOID_TAGS = {
    "area", "base", "br", "col", "embed", "hr", "img", "input",
    "link", "meta", "param", "source", "track", "wbr",
}

# 文本截断
MAX_TEXT_LEN = 200              # 按钮/菜单文本最长保留长度

# 菜单层级
DEFAULT_CHECK_LEVELS = [1, 2, 3, 4, 5]  # 默认列出/审查的层级
MAX_MENU_LEVEL = 5              # 菜单最大层级（L1→L5）

# 审查相关
DEFAULT_SEVERITY = "low"        # 默认严重级别
MAX_PROMPT_CHARS = 2000         # 单条 prompt 中上下文最长长度
MAX_LLM_REVIEW_ITEMS = 50       # LLM 审查的按钮/菜单节点上限，超出则部分审查
LLM_REVIEW_WORKERS = 8          # LLM 审查并发线程数（并行审查按钮/菜单节点，避免串行累积超时）
LLM_REVIEW_PER_TIMEOUT = 120    # 单个节点 LLM 审查超时（秒），并行后整体约等于该值而非 item*该值
LLM_REVIEW_TOTAL_TIMEOUT = 600  # LLM 审查整体总预算（秒），超时即放弃剩余节点并诚实标记，绝不无限挂起

# 浏览器自动化（runtime 可选）
RUNTIME_PAGE_TIMEOUT = 30       # 页面加载超时（秒）

# runtime 抽查 URL 白名单（SSRF 防护）：仅允许 http/https，且 host 必须为本地/内网
URL_ALLOWED_SCHEMES = ("http", "https")
# 允许的 host 字面名：本机回环主机名（精确匹配，避免 127.0.0.1.evil.com 之类前缀绕过）
URL_ALLOWED_HOSTNAMES = ("localhost", "127.0.0.1", "::1", "0.0.0.0")
# 允许的 IP 网段（用 ipaddress 精确解析，涵盖回环/私网/链路本地等内网范围）
URL_ALLOWED_NETWORKS = (
    "127.0.0.0/8",      # IPv4 回环
    "10.0.0.0/8",       # 私网 A
    "172.16.0.0/12",    # 私网 B
    "192.168.0.0/16",   # 私网 C
    "169.254.0.0/16",   # 链路本地（含云元数据端点所在网段）
    "::1/128",          # IPv6 回环
    "fe80::/10",        # IPv6 链路本地
    "fc00::/7",         # IPv6 ULA
)

# 六维审查清单
CATEGORIES = ["交互正确性", "反馈缺失", "禁用与边界", "可达性", "错误处理", "一致性"]

# 确认弹窗关键字（onclick 中含 confirm / 确认 即视为有确认弹窗）
CONFIRM_PATTERN = re.compile(r"confirm|确认")

# 结构化容器标签：其文本不直接作为菜单 label（避免把子菜单文本混入父节点）
MENU_STRUCTURAL_TAGS = {
    "ul", "li", "form", "nav", "script", "style", "html", "body", "head",
    "main", "aside", "section", "div", "header", "footer", "table",
    "thead", "tbody", "tfoot", "tr", "td", "th",
}

# LLM 不可用时返回的错误前缀（与 LLMIntegration.chat_completion 保持一致）
LLM_UNAVAILABLE_KEYWORDS = ("LLM调用错误", "LLM 调用错误")


# ─────────────────────────────────── 数据结构 ───────────────────────────────────

@dataclass
class ButtonItem:
    """前端按钮项。"""
    text: str = ""
    file: str = ""
    line: int = 0
    events: List[str] = field(default_factory=list)   # 绑定事件（onclick/onchange 等）
    form: str = ""                                     # 所属表单（id/name），无则空串
    has_confirm: bool = False                          # 是否有确认弹窗
    disabled: bool = False                             # 是否禁用
    tag: str = ""                                      # 元素标签（button/a/input 等）
    href: str = ""                                     # 跳转目标（跳转类元素）

    def to_dict(self) -> Dict[str, Any]:
        return {
            "text": self.text,
            "file": self.file,
            "line": self.line,
            "events": self.events,
            "form": self.form,
            "has_confirm": self.has_confirm,
            "disabled": self.disabled,
            "tag": self.tag,
            "href": self.href,
        }


@dataclass
class MenuNode:
    """菜单树节点（L1→L5 层级树）。"""
    label: str = ""
    level: int = 1
    parent: str = ""            # 父节点 label，根节点为空串
    href: str = ""              # 跳转目标
    children: List["MenuNode"] = field(default_factory=list)


# ───────────────────────────── HTML 静态扫描器 ─────────────────────────────

class _HtmlScanner(HTMLParser):
    """基于标准库 html.parser 的 HTML 扫描器：枚举按钮与构建菜单树。

    用 getpos() 获得行号；用元素栈累积文本并传播，从而得到按钮文本与菜单 label。
    """

    def __init__(self, filepath: str):
        super().__init__(convert_charrefs=True)
        self.filepath = filepath
        self.buttons: List[ButtonItem] = []
        self.menu_roots: List[MenuNode] = []
        self._stack: List[Dict[str, Any]] = []   # 元素栈
        self._ul_depth = 0                        # 当前 <ul> 嵌套深度
        self._level_last: Dict[int, MenuNode] = {}  # 每层最近出现的 li 节点
        self._current_form = ""                   # 当前所在的表单上下文（id/name）

    # ── 判定辅助 ──
    @staticmethod
    def _is_button_element(tag: str, attrs: Dict[str, str]) -> bool:
        """判断一个元素是否为「按钮候选」：
        <button>、<a class=*btn*>、<input type=submit|button|image>，或含 on* 事件属性。"""
        tag = tag.lower()
        if tag == "button":
            return True
        if tag == "a":
            classes = attrs.get("class", "").lower()
            if "btn" in classes:
                return True
        if tag == "input":
            itype = attrs.get("type", "").lower()
            if itype in ("submit", "button", "image"):
                return True
        for key in attrs:
            if key.startswith("on"):
                return True
        return False

    # ── 解析回调 ──
    def handle_starttag(self, tag: str, attrs: List[tuple]) -> None:
        tag = tag.lower()
        attrs_dict: Dict[str, str] = {}
        for k, v in attrs:
            attrs_dict[k.lower()] = (v or "").strip()
        line = self.getpos()[0]

        parent = self._stack[-1] if self._stack else None
        elem = {
            "tag": tag,
            "attrs": attrs_dict,
            "line": line,
            "text": "",          # 综合文本（含后代，用于按钮文本）
            "own_text": "",      # 直接文本（仅当前元素自身，用于菜单 label）
            "a_text": "",        # li 内直接 <a> 的文本（菜单 label 回退）
            "leaf_text": "",     # li 内首个叶子元素（如 span/a）的文本（菜单 label 首选）
            "parent": parent,
            "is_button": self._is_button_element(tag, attrs_dict),
            "menu_node": None,
            "href": attrs_dict.get("href", ""),
        }

        # 菜单结构跟踪
        if tag == "ul":
            self._ul_depth += 1
        elif tag == "li":
            level = max(1, min(self._ul_depth, MAX_MENU_LEVEL))
            parent_node = self._level_last.get(level - 1)
            node = MenuNode(
                label="",
                level=level,
                parent=(parent_node.label if parent_node else ""),
                href="",
            )
            elem["menu_node"] = node
            if parent_node is not None:
                parent_node.children.append(node)
            else:
                self.menu_roots.append(node)
            self._level_last[level] = node

        # 表单上下文
        if tag == "form":
            self._current_form = attrs_dict.get("id", "") or attrs_dict.get("name", "")
        elem["form_ctx"] = self._current_form

        # 跳转目标：<a href> 若位于某个 li 内，则写入对应菜单节点
        if tag == "a" and attrs_dict.get("href"):
            for anc in reversed(self._stack):
                if anc["menu_node"] is not None:
                    if not anc["menu_node"].href:
                        anc["menu_node"].href = attrs_dict["href"]
                    break

        self._stack.append(elem)

        # 空元素（void）：无结束标签，立即闭合
        if tag in VOID_TAGS:
            self._stack.pop()
            self._finalize_element(elem)

    def handle_startendtag(self, tag: str, attrs: List[tuple]) -> None:
        # 自闭合标签（如 <input ... />）当作「开始后立即结束」处理
        self.handle_starttag(tag, attrs)
        self.handle_endtag(tag)

    def handle_data(self, data: str) -> None:
        if self._stack:
            self._stack[-1]["text"] += data
            self._stack[-1]["own_text"] += data

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        # 找到栈中匹配的最近元素，并弹出其上的所有元素（容错非严格嵌套）
        idx = None
        for i in range(len(self._stack) - 1, -1, -1):
            if self._stack[i]["tag"] == tag:
                idx = i
                break
        if idx is None:
            return
        while len(self._stack) > idx:
            elem = self._stack.pop()
            self._finalize_element(elem)

    # ── 元素收尾 ──
    def _finalize_element(self, elem: Dict[str, Any]) -> None:
        # 文本向上传播
        if elem["parent"] is not None:
            elem["parent"]["text"] += elem["text"]

        if elem["tag"] == "ul":
            self._ul_depth = max(0, self._ul_depth - 1)

        if elem["tag"] == "form":
            # 表单闭合后重置表单上下文
            self._current_form = ""

        if elem["is_button"]:
            self.buttons.append(self._make_button(elem))

        if elem["menu_node"] is not None:
            # 菜单 label 优先取 li 内首个叶子元素文本，其次取 li 直接文本，最后取 <a> 文本
            label = (" ".join(elem["leaf_text"].split())
                     or " ".join(elem["own_text"].split())
                     or elem["a_text"])
            node = elem["menu_node"]
            node.label = (label or "(无文本)")[:MAX_TEXT_LEN]
            # 子节点最终确定父节点 label
            for child in node.children:
                child.parent = node.label

        if elem["tag"] == "a":
            # 将 <a> 文本写入最近的 li 祖先，作为菜单 label 回退（仅当尚未设置）
            a_text = " ".join(elem["text"].split())
            if a_text:
                for anc in reversed(self._stack):
                    if anc["tag"] == "li" and not anc["a_text"]:
                        anc["a_text"] = a_text
                        break

        # 将叶子元素（span/a/button 等）文本写入最近的 li 祖先，作为菜单 label 首选
        if elem["tag"] not in MENU_STRUCTURAL_TAGS:
            leaf_text = " ".join(elem["text"].split())
            if leaf_text:
                for anc in reversed(self._stack):
                    if anc["tag"] == "li" and not anc["leaf_text"]:
                        anc["leaf_text"] = leaf_text
                        break

    def _make_button(self, elem: Dict[str, Any]) -> ButtonItem:
        attrs = elem["attrs"]
        onclick = attrs.get("onclick", "")
        events = [k for k in attrs if k.startswith("on")]
        text = " ".join(elem["text"].split())
        # input 按钮的可视文本来自 value 属性
        if not text and elem["tag"] == "input":
            text = attrs.get("value", "")
        return ButtonItem(
            text=text[:MAX_TEXT_LEN],
            file=self.filepath,
            line=elem["line"],
            events=events,
            form=elem["form_ctx"],
            has_confirm=bool(CONFIRM_PATTERN.search(onclick)),
            disabled="disabled" in attrs,
            tag=elem["tag"],
            href=elem["href"],
        )


# ─────────────────────────────── 主审查类 ───────────────────────────────

class FrontendInspector:
    """前端交互审查器。

    用法：
        from core.frontend_inspector import FrontendInspector
        inspector = FrontendInspector()
        result = inspector.inspect(project_path, mode="static")
    """

    def __init__(self, llm: Optional[LLMIntegration] = None):
        """初始化。llm 为空时自动创建 LLMIntegration()。"""
        if llm is None:
            try:
                llm = LLMIntegration()
            except Exception as e:
                logger.warning(f"自动创建 LLMIntegration 失败: {e}")
                llm = None
        self.llm = llm

    # ── 主入口 ──
    def inspect(
        self,
        project_path: str,
        entry: Optional[str] = None,
        mode: str = "static",
        url: Optional[str] = None,
        check_levels: Optional[List[int]] = None,
    ) -> Dict[str, Any]:
        """主入口。返回 {"buttons": [...], "menu_tree": [...], "findings": [...], "summary": "..."}。

        project_path : 项目根目录
        entry        : 可选入口（目录或单个 HTML 文件；None 时扫描整个项目）
        mode         : "static"（默认，静态解析 + LLM 审查）或 "runtime"（浏览器抽查，失败降级）
        url          : runtime 模式所需的服务地址
        check_levels : 菜单要列出到第几层（默认 [1,2,3,4,5]）
        """
        if check_levels is None:
            check_levels = list(DEFAULT_CHECK_LEVELS)

        mode = (mode or "static").lower()
        if mode == "runtime":
            return self._inspect_runtime(project_path, entry, url, check_levels)

        if mode != "static":
            logger.warning(f"未知模式 {mode!r}，降级为 static")
        return self._inspect_static(project_path, entry, check_levels)

    # ── 静态分析 ──
    def _inspect_static(
        self,
        project_path: str,
        entry: Optional[str],
        check_levels: List[int],
    ) -> Dict[str, Any]:
        html_files = self._find_html_files(project_path, entry)
        buttons: List[ButtonItem] = []
        menu_roots: List[MenuNode] = []

        for fp in html_files:
            try:
                scanner = self._scan_file(fp)
                buttons.extend(scanner.buttons)
                menu_roots.extend(scanner.menu_roots)
            except Exception as e:
                logger.warning(f"扫描文件失败 {fp}: {e}")

        # 收集需要审查的菜单节点（仅 check_levels 内的层级）
        menu_nodes: List[MenuNode] = []
        self._collect_menu_nodes(menu_roots, check_levels, menu_nodes)

        findings: List[Dict[str, Any]] = []
        if self._llm_available():
            try:
                reviewed_buttons = buttons[:MAX_LLM_REVIEW_ITEMS]
                reviewed_menus = menu_nodes[:MAX_LLM_REVIEW_ITEMS]
                # 并行审查按钮与菜单节点：串行逐个调用 LLM 会随节点数线性累积超时，
                # 改为线程池并发，整体耗时 ≈ 单节点耗时（而非节点数 × 单节点耗时）。
                # 每个节点独立受 LLM_REVIEW_PER_TIMEOUT 保护，单个节点慢不拖垮整体；
                # 整体另设 LLM_REVIEW_TOTAL_TIMEOUT 总预算，超时即放弃剩余节点并诚实标记，
                # 保证即便个别节点真正卡死，整体也绝不无限挂起。
                llm_review_items = reviewed_buttons + reviewed_menus
                # 手动管理执行器生命周期：with 上下文退出会 shutdown(wait=True)
                # 阻塞等待所有未完成任务，导致 LLM_REVIEW_TOTAL_TIMEOUT 不能真正约束
                # 整体运行时长。改为在总预算耗尽时取消排队实例并 shutdown(wait=False)，
                # 不等未完成任务即退出本次扫描。
                ex = ThreadPoolExecutor(max_workers=LLM_REVIEW_WORKERS)
                try:
                    futures = [ex.submit(self._review_item, item) for item in llm_review_items]
                    timed_out = False
                    try:
                        for fut in as_completed(futures, timeout=LLM_REVIEW_TOTAL_TIMEOUT):
                            try:
                                findings.extend(fut.result(timeout=LLM_REVIEW_PER_TIMEOUT))
                            except Exception as e:  # noqa: BLE001  (含单节点超时)
                                logger.error(f"并发审查节点异常: {e}")
                                findings.append(self._pending_finding(
                                    "全局", f"并发审查节点异常: {e}"))
                    except TimeoutError:
                        # 总预算耗尽：as_completed 抛 TimeoutError，落到下方统一标记
                        timed_out = True
                    # 未完成节点如实标记为待确认，绝不静默丢弃
                    pending = [f for f in futures if not f.done()]
                    if pending:
                        findings.append(self._pending_finding(
                            "全局", f"LLM 审查总预算 {LLM_REVIEW_TOTAL_TIMEOUT}s 内未完成 "
                            f"{len(pending)}/{len(llm_review_items)} 个节点，已放弃并请人工复核。"))
                finally:
                    if timed_out:
                        # 总预算超时：不再等待未完成任务，避免扫描被挂起的节点拖垮
                        for f in futures:
                            f.cancel()
                        ex.shutdown(wait=False)
                    else:
                        ex.shutdown(wait=True)
                if len(buttons) > MAX_LLM_REVIEW_ITEMS or len(menu_nodes) > MAX_LLM_REVIEW_ITEMS:
                    findings.append(self._pending_finding(
                        "全局", f"按钮/菜单数量超过 {MAX_LLM_REVIEW_ITEMS} 上限，"
                        f"LLM 审查为部分审查（按钮 {len(reviewed_buttons)}/{len(buttons)}，"
                        f"菜单 {len(reviewed_menus)}/{len(menu_nodes)}），请人工复核未审查项。"))
            except Exception as e:
                logger.error(f"LLM 审查过程中发生异常: {e}")
                findings.append(self._pending_finding("全局", f"LLM 审查过程中发生异常: {e}"))
        else:
            logger.warning("LLM 不可用，仅完成静态枚举，未进行 AI 审查")

        summary = self._build_static_summary(html_files, buttons, menu_nodes, findings)

        return {
            "buttons": [b.to_dict() for b in buttons],
            "menu_tree": self._serialize_menu(menu_roots, check_levels),
            "findings": findings,
            "summary": summary,
        }

    # ── 运行时抽查（可选；失败降级为静态结论） ──
    def _inspect_runtime(
        self,
        project_path: str,
        entry: Optional[str],
        url: Optional[str],
        check_levels: List[int],
    ) -> Dict[str, Any]:
        if not url:
            logger.warning("runtime 模式需要 url，降级为静态分析")
            result = self._inspect_static(project_path, entry, check_levels)
            result["summary"] = "runtime 模式未提供 url，已降级为静态分析。\n" + result["summary"]
            return result

        # SSRF 防护：url 必须通过白名单校验，否则拒绝访问并降级为静态分析
        url_ok, url_err = self._validate_runtime_url(url)
        if not url_ok:
            logger.warning(f"runtime url 校验未通过，拒绝访问并降级为静态分析: {url_err}")
            result = self._inspect_static(project_path, entry, check_levels)
            result["findings"].append(self._pending_finding(
                "运行时", f"runtime url 未通过白名单校验（{url_err}），已拒绝访问并降级为静态分析"))
            result["summary"] = f"runtime 模式：url 未通过白名单校验（{url_err}），已拒绝访问并降级为静态分析。\n" + result["summary"]
            return result

        runtime_ok = False
        runtime_findings: List[Dict[str, Any]] = []
        try:
            runtime_findings = self._runtime_review(url)
            runtime_ok = True
        except Exception as e:
            logger.warning(f"浏览器自动化不可用或运行失败，降级为静态分析: {e}")

        result = self._inspect_static(project_path, entry, check_levels)

        if not runtime_ok:
            result["findings"].append(self._pending_finding(
                "运行时", "浏览器自动化不可用或运行失败，返回的是静态分析结论，请人工复核关键路径"))
            result["summary"] = "runtime 模式：浏览器自动化不可用或运行失败，已降级为静态分析结论。\n" + result["summary"]
        else:
            result["findings"].extend(runtime_findings)
            result["summary"] = "已执行浏览器自动化抽查关键路径。\n" + result["summary"]
        return result

    @staticmethod
    def _validate_runtime_url(url: str) -> tuple:
        """校验 runtime 抽查 URL 是否在允许白名单内（SSRF 防护）。

        规则：协议必须为 http/https，且 host 必须命中允许清单——字面主机名精确匹配，
        或解析为允许的 IP 网段（回环/私网/链路本地/ULA）。用语义网段解析替代前缀
        startswith，杜绝 127.0.0.1.evil.com、host.192.168.1.1 之类前缀绕过。
        返回 (ok: bool, 失败原因: str)。
        """
        parsed = None
        try:
            parsed = urlparse(url)
        except Exception:
            return False, "url 解析失败"
        scheme = (parsed.scheme or "").lower()
        if scheme not in URL_ALLOWED_SCHEMES:
            return False, f"协议 {scheme or '(空)'} 不在允许范围（仅 http/https）"
        host = (parsed.hostname or "").strip().lower()
        if not host:
            return False, "缺少有效主机名"
        # 1) 字面主机名精确匹配（含 localhost 别名）
        if any(host == h for h in URL_ALLOWED_HOSTNAMES):
            return True, ""
        # 2) 解析为 IP 字面 → 网段归属判断
        if ":" in host:
            return False, f"目标主机 {host!r} 为 IPv6 但不在允许字面名内，已拒绝"
        try:
            ip = ipaddress.ip_address(host)
        except ValueError:
            # 非 IP 字面：仅接受 localhost 家族字面名，其余主机名一律拒绝（不解析 DNS，
            # 避免 DNS 重绑定类绕过）
            return False, f"目标主机 {host!r} 非 IP 字面且非允许主机名，已拒绝"
        for net in URL_ALLOWED_NETWORKS:
            if ip.version == 4 and "/" in net and "." in net:
                if ip in ipaddress.ip_network(net, strict=False):
                    return True, ""
            elif ip.version == 6 and ":" in net:
                if ip in ipaddress.ip_network(net, strict=False):
                    return True, ""
        return False, f"目标主机 {host!r} 的 IP 不在本地/内网白名单，已拒绝"

    @staticmethod
    def _probe_redirect_chain(url: str, max_hops: int = 5, timeout: Optional[int] = None) -> tuple:
        """重定向逃逸预检：手动跟随 3xx 重定向，逐跳校验落点仍在白名单内。

        浏览器（selenium driver.get）默认跟随 HTTP 重定向，若内网服务返回 302 到
        白名单外地址（外网/云端），则构成 SSRF 逃逸。此处用标准库 urlopen 手动跟随，
        对每一跳的 Location 解析后再次走 _validate_runtime_url 校验，任一跳越界即拒绝。
        返回 (最终 url, 错误信息或空串)。
        """
        if timeout is None:
            timeout = RUNTIME_PAGE_TIMEOUT
        # 自定义 opener：禁用自动重定向。urlopen 默认会跟随 3xx，导致此处永远收不到
        # 重定向状态，校验形同虚设。改为不自动跟随，3xx 由本函数手动解析 Location。
        class _NoRedirect(HTTPRedirectHandler):
            def redirect_request(self, req, fp, code, msg, headers, newurl):
                return None
        opener = build_opener(_NoRedirect())

        current = url
        for _ in range(max_hops):
            ok, err = FrontendInspector._validate_runtime_url(current)
            if not ok:
                return current, f"重定向落点 {current!r} 越出白名单：{err}"
            req = Request(current, method="GET", headers={"User-Agent": "coderef-frontend-inspector"})
            try:
                with opener.open(req, timeout=timeout) as resp:
                    status = resp.getcode()
                    location = resp.headers.get("Location")
            except urllib.error.HTTPError as he:
                status = he.code
                location = he.headers.get("Location")
            except Exception as e:
                # 请求失败（连接拒绝/超时）视为不可达，交由上层决定；不把失败当逃逸
                return current, ""
            if status in (301, 302, 303, 307, 308) and location:
                next_url = urljoin(current, location)
                if next_url == current:
                    return current, ""
                current = next_url
                continue
            return current, ""
        return current, f"重定向跳数超过上限 {max_hops}，已停止跟随"

    def _runtime_review(self, url: str) -> List[Dict[str, Any]]:
        """可选依赖 selenium 的浏览器抽查。未安装 selenium 时抛出 ImportError，
        由上层统一降级为静态分析。"""
        # 重定向逃逸预检：浏览器会跟随 3xx 重定向，若内网服务跳转到白名单外地址
        # （如云端、外网 IP），则构成 SSRF 逃逸。这里用标准库手动跟随重定向，
        # 对每一跳目标逐一校验，确保最终落点仍在白名单内，否则拒绝访问。
        final_url, redir_err = self._probe_redirect_chain(url)
        if redir_err:
            logger.warning(f"runtime url 重定向预检未通过，拒绝访问: {redir_err}")
            raise ValueError(redir_err)
        url = final_url

        from selenium import webdriver  # 可选依赖，非本项目 requirements 强制

        options = webdriver.ChromeOptions()
        options.add_argument("--headless")
        driver = webdriver.Chrome(options=options)
        findings: List[Dict[str, Any]] = []
        try:
            driver.set_page_load_timeout(RUNTIME_PAGE_TIMEOUT)
            driver.get(url)
            report = driver.execute_script(
                """
                const btns = document.querySelectorAll(
                    'button, input[type=submit], input[type=button], a.btn');
                return {
                    buttonCount: btns.length,
                    disabledCount: Array.from(btns).filter(function(b){return b.disabled;}).length,
                    title: document.title || ''
                };
                """
            )
            count = int(report.get("buttonCount", 0) or 0)
            if count == 0:
                findings.append({
                    "category": "可达性",
                    "finding": f"运行时在 {url} 未检测到任何可交互按钮，可能存在交互缺失",
                    "severity": "medium",
                    "suggestion": "检查页面是否正常渲染、入口是否正确",
                    "verified": True,
                })
            else:
                logger.info(f"运行时抽查 {url}：检测到 {count} 个按钮，其中禁用 "
                            f"{report.get('disabledCount', 0)} 个")
        finally:
            try:
                driver.quit()
            except Exception as e:
                logger.warning(f"关闭浏览器驱动失败: {e}")
        return findings

    # ── 文件发现与扫描 ──
    def _find_html_files(self, project_path: str, entry: Optional[str]) -> List[str]:
        if project_path is None or not os.path.isdir(project_path):
            logger.error(f"project_path 无效或不存在: {project_path}")
            return []
        # entry 可指向目录（覆盖 project_path）或单个 HTML 文件
        if entry and os.path.isdir(entry):
            project_path = entry
        if entry and os.path.isfile(entry):
            base = os.path.basename(entry).lower()
            if base.endswith((".html", ".htm")):
                return [entry]
            return []

        files: List[str] = []
        for root, dirs, names in os.walk(project_path):
            dirs[:] = [d for d in dirs if d not in IGNORED_DIRS]
            for name in names:
                if name.lower().endswith((".html", ".htm")):
                    files.append(os.path.join(root, name))
        files.sort()
        return files[:MAX_FILES_SCAN]

    def _scan_file(self, fp: str) -> _HtmlScanner:
        try:
            size = os.path.getsize(fp)
        except OSError:
            size = 0
        if size > MAX_HTML_FILE_SIZE:
            logger.warning(f"HTML 文件过大，跳过扫描: {fp} ({size} 字节)")
            return _HtmlScanner(fp)
        try:
            with open(fp, "r", encoding="utf-8", errors="ignore") as fh:
                content = fh.read()
        except OSError as e:
            logger.warning(f"读取 HTML 文件失败 {fp}: {e}")
            return _HtmlScanner(fp)
        scanner = _HtmlScanner(fp)
        try:
            scanner.feed(content)
            scanner.close()
        except Exception as e:
            logger.warning(f"解析 HTML 出错 {fp}: {e}")
        return scanner

    # ── 菜单树工具 ──
    def _collect_menu_nodes(
        self,
        nodes: List[MenuNode],
        check_levels: List[int],
        collected: List[MenuNode],
    ) -> None:
        for n in nodes:
            if n.level in check_levels:
                collected.append(n)
            self._collect_menu_nodes(n.children, check_levels, collected)

    def _serialize_menu(self, nodes: List[MenuNode], check_levels: List[int]) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        for n in nodes:
            if n.level in check_levels:
                out.append({
                    "label": (n.label or "(无文本)")[:MAX_TEXT_LEN],
                    "level": n.level,
                    "parent": n.parent,
                    "href": n.href,
                    "children": self._serialize_menu(n.children, check_levels),
                })
            else:
                # 当前层不在 check_levels，但仍需深入子层寻找匹配层级的节点
                out.extend(self._serialize_menu(n.children, check_levels))
        return out

    # ── LLM 审查 ──
    def _llm_available(self) -> bool:
        try:
            return bool(getattr(self.llm, "client", None))
        except Exception:
            return False

    def _build_menu_prompt(self, node: MenuNode) -> str:
        return (
            "请对以下 Web 前端「菜单节点」进行交互审查，覆盖 6 个维度："
            "交互正确性、反馈缺失、禁用与边界、可达性、错误处理、一致性。\n"
            "返回 JSON 数组，每项含字段：category、finding、severity、suggestion、verified。\n"
            "severity 取值 low/medium/high；verified 为 bool（是否已在源码/结构中确认）。\n\n"
            "菜单节点信息：\n"
            f"- 名称: {node.label or '(无文本)'}\n"
            f"- 层级: L{node.level}\n"
            f"- 父节点: {node.parent or '根'}\n"
            f"- 跳转目标: {node.href or '无'}\n"
            f"- 子节点数: {len(node.children)}\n\n"
            "只返回 JSON 数组，不要其他内容。"
        )

    def _build_button_prompt(self, btn: ButtonItem) -> str:
        return (
            "请对以下 Web 前端「按钮」进行交互审查，覆盖 6 个维度："
            "交互正确性、反馈缺失、禁用与边界、可达性、错误处理、一致性。\n"
            "返回 JSON 数组，每项含字段：category、finding、severity、suggestion、verified。\n"
            "severity 取值 low/medium/high；verified 为 bool（是否已在源码/结构中确认）。\n\n"
            "按钮信息：\n"
            f"- 文本: {btn.text or '(无文本)'}\n"
            f"- 标签: {btn.tag}\n"
            f"- 绑定事件: {btn.events or '无'}\n"
            f"- 所属表单: {btn.form or '无'}\n"
            f"- 确认弹窗: {'是' if btn.has_confirm else '否'}\n"
            f"- 是否禁用: {'是' if btn.disabled else '否'}\n"
            f"- 跳转目标: {btn.href or '无'}\n"
            f"- 文件: {btn.file}\n"
            f"- 行号: {btn.line}\n\n"
            "只返回 JSON 数组，不要其他内容。"
        )

    def _review_button(self, btn: ButtonItem) -> List[Dict[str, Any]]:
        prompt = self._build_button_prompt(btn)
        return self._llm_review(kind="按钮", item=btn, prompt=prompt)

    def _review_menu_node(self, node: MenuNode) -> List[Dict[str, Any]]:
        prompt = self._build_menu_prompt(node)
        return self._llm_review(kind="菜单节点", item=node, prompt=prompt)

    def _review_item(self, item: Any) -> List[Dict[str, Any]]:
        """按条目类型统一分发审查（供并发线程调用）。"""
        if isinstance(item, ButtonItem):
            return self._review_button(item)
        return self._review_menu_node(item)

    def _llm_review(self, kind: str, item: Any, prompt: str) -> List[Dict[str, Any]]:
        """调用 LLM 审查一个条目，返回 findings 列表。解析失败时降级为一条 pending 结论。"""
        if not self._llm_available():
            return []

        messages = [
            {"role": "system", "content": "你是资深的前端交互审查专家，只返回 JSON 数组。"},
            {"role": "user", "content": prompt[:MAX_PROMPT_CHARS]},
        ]
        try:
            response = self.llm.chat_completion(messages)
        except Exception as e:
            logger.error(f"{kind} LLM 调用异常: {e}")
            return [self._pending_finding(kind, f"LLM 调用异常: {e}")]

        # LLM 未配置/初始化失败时，chat_completion 返回错误字符串
        if response.strip().startswith(LLM_UNAVAILABLE_KEYWORDS):
            logger.warning(f"{kind} LLM 不可用，返回降级结论: {response[:120]}")
            return [self._pending_finding(kind, "LLM 不可用，未执行 AI 审查")]

        data = self.llm._try_parse_json(response)
        if not isinstance(data, list):
            reason = "LLM 返回内容无法解析为 JSON 数组"
            logger.warning(f"{kind} 审查结果降级: {reason}; 响应片段: {response[:200]}")
            return [self._pending_finding(kind, reason)]

        findings: List[Dict[str, Any]] = []
        for f in data:
            if not isinstance(f, dict):
                continue
            category = str(f.get("category", "")).strip()
            if category not in CATEGORIES:
                category = "未分类"
            severity = str(f.get("severity", DEFAULT_SEVERITY)).lower()
            if severity not in ("low", "medium", "high"):
                severity = DEFAULT_SEVERITY
            findings.append({
                "category": category,
                "finding": str(f.get("finding", "")),
                "severity": severity,
                "suggestion": str(f.get("suggestion", "")),
                "verified": bool(f.get("verified", False)),
            })
        return findings

    def _pending_finding(self, kind: str, reason: str) -> Dict[str, Any]:
        """构造一条降级（pending）结论，绝不静默吞掉。"""
        return {
            "category": "待确认",
            "finding": f"{kind} 审查待确认：{reason}",
            "severity": DEFAULT_SEVERITY,
            "suggestion": "请人工复核该条目",
            "verified": False,
        }

    # ── 摘要 ──
    def _build_static_summary(
        self,
        html_files: List[str],
        buttons: List[ButtonItem],
        menu_nodes: List[MenuNode],
        findings: List[Dict[str, Any]],
    ) -> str:
        lines: List[str] = []
        if not self._llm_available():
            lines.append("LLM 不可用（未配置 API Key 或客户端未初始化），仅完成静态枚举，未进行 AI 审查；findings 为空或仅含降级结论。")
        else:
            high = sum(1 for f in findings if f.get("severity") == "high")
            medium = sum(1 for f in findings if f.get("severity") == "medium")
            lines.append(f"LLM 审查完成：共发现 {len(findings)} 条问题（高 {high} / 中 {medium} / 低 "
                         f"{len(findings) - high - medium}）。")
        lines.append(f"共扫描 HTML 文件 {len(html_files)} 个，枚举按钮 {len(buttons)} 个，"
                     f"菜单节点 {len(menu_nodes)} 个。")
        return "\n".join(lines)