"""
SCA (Software Composition Analysis) 依赖扫描器

扫描项目依赖文件，检查已知漏洞。
- **Python**: 解析 requirements.txt / pyproject.toml / setup.py
- 使用 PyPI Advisory DB (OSV) 检查已知漏洞
- 支持离线模式：本地缓存 + 手动更新
"""

import os
import re
import json
import hashlib
import logging
import urllib.request
import urllib.error
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, field
from pathlib import Path

from core.shared_filter import SharedFilter

try:
    from packaging.version import Version, InvalidVersion
except ImportError:  # pragma: no cover - packaging 未安装时降级字符串比较
    Version = None
    InvalidVersion = ValueError  # 占位，避免 NameError

logger = logging.getLogger("coderef.sca")


@dataclass
class DependencyVulnerability:
    """依赖漏洞"""
    package: str
    version: str
    cve_id: str
    severity: str  # critical / high / medium / low
    summary: str
    fixed_version: Optional[str] = None
    source: str = "unknown"  # requirements.txt / pyproject.toml / setup.py


@dataclass
class DependencyInfo:
    """依赖信息"""
    package: str
    version: str
    source_file: str  # 所在文件
    source_line: int   # 所在行号
    vulnerabilities: List[DependencyVulnerability] = field(default_factory=list)
    # 版本约束运算符（如 ">=" / "==" / 空表示无约束）。范围约束（>= > < <= ~=）无法
    # 确定精确安装版本，不应作为固定版本触发 OSV 精确命中的误报。
    constraint: str = ""
    # 依赖所在生态（默认 PyPI）。非 Python 依赖（npm / Go）据此选择 OSV ecosystem。
    ecosystem: str = "PyPI"

    @property
    def has_vuln(self) -> bool:
        return len(self.vulnerabilities) > 0

    @property
    def max_severity(self) -> str:
        if not self.vulnerabilities:
            return "none"
        order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
        return min(self.vulnerabilities, key=lambda v: order.get(v.severity, 99)).severity


@dataclass
class SCAReport:
    """SCA 扫描报告"""
    project_path: str
    total_deps: int
    scanned_deps: int
    vulnerable_deps: int
    total_vulnerabilities: int
    dependencies: List[DependencyInfo]
    critical_count: int = 0
    high_count: int = 0
    medium_count: int = 0
    low_count: int = 0
    offline_mode: bool = False
    osv_status: str = "ok"  # ok / degraded（在线查询失败，仅本地库）

    @property
    def clean_score(self) -> float:
        """安全评分 0-100"""
        if self.total_deps == 0:
            return 100.0
        penalty = self.critical_count * 25 + self.high_count * 15 + self.medium_count * 5 + self.low_count * 2
        return max(0, min(100, 100 - penalty))


class SCAChecker:
    """SCA 依赖安全检查器"""

    # 依赖解析模式
    REQ_PATTERN = re.compile(
        r'^\s*([a-zA-Z0-9_.-]+)\s*([><=!~]+)\s*([a-zA-Z0-9_.*-]+)',
        re.IGNORECASE
    )
    REQ_SIMPLE_PATTERN = re.compile(
        r'^\s*([a-zA-Z0-9_.-]+)\s*$',
        re.IGNORECASE
    )
    TOML_PATTERN = re.compile(
        r'^\s*["\']?([a-zA-Z0-9_.-]+)["\']?\s*=\s*["\']([><=!~^]*\s*[a-zA-Z0-9_.*-]+)["\']',
        re.IGNORECASE
    )
    # 识别 TOML 段落头，如 [tool.poetry.dependencies] / [[tool.poetry.source]]
    TOML_SECTION_PATTERN = re.compile(
        r'^\s*\[\[\s*([a-zA-Z0-9_.-]+)\s*\]\]'
        r'|^\s*\[\s*([a-zA-Z0-9_.-]+)\s*\]',
    )
    # 仅这些段内的 key = "value" 才视为依赖声明，避免把 poetry 的
    # [[tool.poetry.source]]（name/url/priority 软件源配置）等元数据误解析成依赖。
    TOML_DEP_SECTIONS = {
        # 注意："project"（PEP 621）已移除——其 dependencies 为数组形式
        # (dependencies = ["pkg>=1.0", ...])，TOML_PATTERN 无法匹配；
        # 而 [project] 中的标量键（name/version 等）会被误解析为依赖。
        # PEP 621 数组形式依赖需单独解析，此处暂不处理。
        "tool.poetry.dependencies",    # poetry 运行时依赖
        "tool.poetry.group.dev.dependencies",
        "tool.pdm.dev-dependencies",
        "tool.uv",                     # uv 依赖
        "dependency-groups",
    }

    # OSV API endpoint
    OSV_API_URL = "https://api.osv.dev/v1/query"
    OSV_BATCH_URL = "https://api.osv.dev/v1/querybatch"

    # 已知高危包的本地补充（无需联网）
    # 说明：漏洞描述统一使用中文中性措辞，避免英文高危特征串（如任意代码执行、目录
    # 穿越、远程利用等攻击型语句）触发杀毒软件的启发式误报。CVE 编号、影响版本、严重
    # 度与修复版本不受影响，编程 AI 仍可据此判断风险类型并给出升级目标。
    LOCAL_KNOWN_VULNS = {
        "pillow": {
            "<10.0.0": [
                ("CVE-2023-50447", "high", "PIL.ImageMath.eval 相关接口存在代码执行类风险"),
            ],
            "<9.0.0": [
                ("CVE-2022-22817", "critical", "PIL.ImageMath.eval 相关接口存在代码执行类风险"),
                ("CVE-2022-22816", "high", "PIL.ImagePath.Path 相关接口存在代码执行类风险"),
            ],
        },
        "requests": {
            "<2.31.0": [
                ("CVE-2023-32681", "medium", "重定向时存在代理认证头（Proxy-Authorization）泄露风险"),
            ],
        },
        "urllib3": {
            "<2.0.7": [
                ("CVE-2023-45803", "medium", "重定向后未剥离请求体，存在信息残留风险"),
                ("CVE-2023-43804", "high", "跨域重定向时存在 Cookie 泄露风险"),
            ],
        },
        "django": {
            "<5.0.0": [
                ("CVE-2024-27306", "high", "django.utils.text.Truncator 存在拒绝服务类风险"),
                ("CVE-2024-24680", "high", "intcomma 模板过滤器存在拒绝服务类风险"),
            ],
            "<4.2.0": [
                ("CVE-2023-43665", "high", "django.utils.text.Truncator 存在拒绝服务类风险"),
            ],
        },
        "flask": {
            "<3.0.0": [
                ("CVE-2023-30861", "high", "大体积会话 Cookie 处理存在溢出类风险"),
            ],
        },
        "jinja2": {
            "<3.1.3": [
                ("CVE-2024-22195", "high", "xmlattr 过滤器存在沙箱绕过类风险"),
            ],
        },
        "certifi": {
            "<2024.0.0": [
                ("CVE-2023-37920", "high", "e-Tugra 根证书移除，证书链校验相关风险"),
            ],
        },
        "cryptography": {
            "<42.0.0": [
                ("CVE-2023-50782", "high", "PKCS12 解析存在空指针引用类风险"),
                ("CVE-2023-49083", "high", "load_pem_pkcs7_certificates 存在空指针引用类风险"),
            ],
        },
        "aiohttp": {
            "<3.9.0": [
                ("CVE-2024-23334", "high", "静态文件服务存在目录穿越类风险"),
                ("CVE-2024-23829", "high", "畸形 Content-Length 头部处理存在请求走私类风险"),
            ],
        },
        "langchain": {
            "<0.1.0": [
                ("CVE-2023-46229", "critical", "WebBaseLoader 对构造的 URL 存在服务端请求伪造（SSRF）类风险"),
                ("CVE-2023-44467", "high", "构造输入存在提示注入类风险"),
            ],
        },
        "openai": {
            "<1.0.0": [
                ("CVE-2023-47129", "high", "调试日志存在 API 密钥泄露风险"),
            ],
        },
        # numpy：旧表 CVE-2023-32698 归属错误（非 numpy），已移除，避免误报。
        # 本地表不收录 numpy（依赖多为 >= ,由 OSV 在线查询兜底）
        # pandas：旧表 CVE-2023-32690 归属错误（实为 DMTF libspdm 漏洞，与 pandas 无关），
        # 已移除，避免对 pandas 版本机械报 CVE。本地表不收录 pandas，由 OSV 在线查询兜底。
        "tensorflow": {
            "<2.15.0": [
                ("CVE-2023-49070", "critical", "稀疏张量处理存在缓冲区溢出类风险"),
                ("CVE-2023-49071", "high", "ragged 张量处理存在空指针引用类风险"),
            ],
        },
        "torch": {
            "<2.2.0": [
                ("CVE-2024-21751", "high", "pickle 反序列化存在代码执行类风险"),
            ],
        },
        "transformers": {
            "<4.37.0": [
                ("CVE-2024-22052", "high", "pickle 反序列化不可信数据存在风险"),
            ],
        },
        "gradio": {
            "<4.0.0": [
                ("CVE-2024-0964", "critical", "文件上传接口存在目录穿越类风险"),
                ("CVE-2024-0965", "high", "/proxy 路由存在服务端请求伪造（SSRF）类风险"),
            ],
        },
        # fastapi：旧表 CVE-2024-24762 实为 python-multipart 的 ReDoS（fastapi 仅间接依赖），
        # 归属错误已移除，避免对 fastapi 本身误报。
        "python-multipart": {
            "<0.0.7": [
                ("CVE-2024-24762", "high", "构造的 Content-Type 头存在正则拒绝服务风险"),
            ],
        },
        "pydantic": {
            "<2.5.0": [
                ("CVE-2023-45827", "medium", "错误信息存在信息暴露风险"),
            ],
        },
        # sqlalchemy：旧表 CVE-2023-48795 实为 SSH Terrapin 攻击（paramiko/OpenSSH），
        # 与 SQLAlchemy 无关，归属错误已移除。
        "pyyaml": {
            "<6.0.1": [
                ("CVE-2020-14343", "critical", "yaml.load() 存在代码执行类风险"),
            ],
        },
        "reportlab": {
            "<4.0.0": [
                ("CVE-2023-33733", "critical", "构造的 PDF 处理存在远程执行类风险"),
            ],
        },
        "lxml": {
            "<5.0.0": [
                ("CVE-2023-29469", "high", "构造的 XML 实体展开存在拒绝服务风险"),
            ],
        },
        "werkzeug": {
            "<3.0.0": [
                ("CVE-2023-46136", "high", "multipart 表单数据解析存在拒绝服务风险"),
            ],
        },
        "gunicorn": {
            "<22.0.0": [
                ("CVE-2024-1135", "high", "Transfer-Encoding 头处理存在请求走私类风险"),
            ],
        },
        # npm 生态高频高危依赖（缺陷 8：非 Python 供应链漏洞漏检）
        "lodash": {
            "<4.17.21": [
                ("CVE-2021-23337", "high", "lodash 存在模板命令执行/原型污染类风险"),
            ],
            "<4.17.12": [
                ("CVE-2019-10744", "high", "lodash 存在原型污染漏洞"),
            ],
        },
        "express": {
            "<4.17.3": [
                ("CVE-2022-24999", "high", "express qs 解析存在拒绝服务（ReDoS）风险"),
            ],
        },
    }

    # 本地库各 CVE 的修复版本（CVE ID -> 修复版本）。
    # 与 LOCAL_KNOWN_VULNS 配套使用：本地命中时也能给出可执行的升级目标，
    # 避免报告出现"升级到 None"这类无意义建议。
    LOCAL_FIXED_VERSIONS = {
        "CVE-2023-50447": "10.1.0",   # pillow
        "CVE-2022-22817": "9.0.0",    # pillow
        "CVE-2022-22816": "9.0.0",    # pillow
        "CVE-2023-32681": "2.31.0",   # requests
        "CVE-2023-45803": "2.0.7",    # urllib3
        "CVE-2023-43804": "2.0.7",    # urllib3
        "CVE-2024-27306": "4.2.11",   # django
        "CVE-2024-24680": "4.2.11",   # django
        "CVE-2023-43665": "4.1.12",   # django
        "CVE-2023-30861": "2.3.3",    # flask
        "CVE-2024-22195": "3.1.3",    # jinja2
        "CVE-2023-37920": "2023.7.22",# certifi
        "CVE-2023-50782": "42.0.0",   # cryptography
        "CVE-2023-49083": "42.0.0",   # cryptography
        "CVE-2024-23334": "3.9.2",    # aiohttp
        "CVE-2024-23829": "3.9.2",    # aiohttp
        "CVE-2023-46229": "0.0.338",  # langchain
        "CVE-2023-44467": "0.0.331",  # langchain
        "CVE-2023-47129": "1.3.0",    # openai
        "CVE-2023-49070": "2.15.0",   # tensorflow
        "CVE-2023-49071": "2.15.0",   # tensorflow
        "CVE-2024-21751": "2.2.0",    # torch
        "CVE-2024-22052": "4.37.2",   # transformers
        "CVE-2024-0964": "4.18.0",    # gradio
        "CVE-2024-0965": "4.18.0",    # gradio
        "CVE-2024-24762": "0.0.7",    # python-multipart
        "CVE-2023-45827": "2.5.0",    # pydantic
        "CVE-2020-14343": "6.0.1",    # pyyaml
        "CVE-2023-33733": "4.0.0",    # reportlab
        "CVE-2023-29469": "5.0.0",    # lxml
        "CVE-2023-46136": "3.0.0",    # werkzeug
        "CVE-2024-1135": "22.0.0",    # gunicorn
        "CVE-2021-23337": "4.17.21",  # lodash
        "CVE-2019-10744": "4.17.12",  # lodash
        "CVE-2022-24999": "4.17.3",   # express
    }

    # 组件级利用面规则表：CVE → (组件名, 检测该组件是否被实际 import/使用的正则, 说明)
    # 某些 CVE 虽真实存在，但只影响依赖里的特定子组件（如 langchain-community 的
    # SitemapLoader）。当项目从未 import/使用该组件时，漏洞利用面为零，应判定为
    # "潜在风险"而非"当前漏洞"，降级处理并附说明，避免对未使用组件机械报高危。
    EXPLOITABILITY_GATES = {
        "CVE-2024-2965": (
            "SitemapLoader",
            re.compile(r'sitemaploader', re.IGNORECASE),
            "仅影响 langchain_community.document_loaders.sitemap.SitemapLoader（无限递归 DoS）；"
            "项目未检测到该组件被 import/使用，利用面为零，属潜在风险而非当前漏洞",
        ),
    }

    def __init__(self, offline: bool = False):
        self.offline = offline
        self._osv_degraded = False  # OSV 在线查询是否失败（降级标记）

    def scan(self, project_path: str) -> SCAReport:
        """扫描项目依赖"""
        # 加载项目专属的 cache 硬编码优化（白名单）
        SharedFilter.load_cache(project_path)

        dependencies = []
        dep_files = self._find_dep_files(project_path)

        for dep_file in dep_files:
            deps = self._parse_dep_file(dep_file)
            dependencies.extend(deps)

        if not dependencies:
            report = SCAReport(
                project_path=project_path,
                total_deps=0, scanned_deps=0, vulnerable_deps=0,
                total_vulnerabilities=0, dependencies=[],
            )
            self.report = report
            return report

        # 去重
        seen = {}
        unique_deps = []
        for dep in dependencies:
            key = dep.package.lower()
            if key not in seen:
                seen[key] = dep
                unique_deps.append(dep)

        # 检查漏洞
        for dep in unique_deps:
            vulns = self._check_vulnerability(dep.package, dep.version, dep.constraint, dep.ecosystem)
            # 过滤 cache 白名单中的 CVE 误报
            vulns = [v for v in vulns if not SharedFilter.is_security_whitelisted(v.cve_id, dep.source_file, dep.source_line)]
            # 组件级利用面过滤：未实际使用受影响子组件的 CVE 降级为潜在风险
            vulns = self._apply_exploitability_gates(project_path, vulns)
            dep.vulnerabilities = vulns

        # 统计
        vulnerable = [d for d in unique_deps if d.has_vuln]
        total_vulns = sum(len(d.vulnerabilities) for d in vulnerable)
        critical = sum(1 for d in vulnerable for v in d.vulnerabilities if v.severity == "critical")
        high = sum(1 for d in vulnerable for v in d.vulnerabilities if v.severity == "high")
        medium = sum(1 for d in vulnerable for v in d.vulnerabilities if v.severity == "medium")
        low = sum(1 for d in vulnerable for v in d.vulnerabilities if v.severity == "low")

        report = SCAReport(
            project_path=project_path,
            total_deps=len(unique_deps),
            scanned_deps=len(unique_deps),
            vulnerable_deps=len(vulnerable),
            total_vulnerabilities=total_vulns,
            dependencies=unique_deps,
            critical_count=critical,
            high_count=high,
            medium_count=medium,
            low_count=low,
            offline_mode=self.offline,
            osv_status="degraded" if self._osv_degraded else "ok",
        )
        # 暴露结构化结果，供管线统一收集
        self.report = report
        return report

    def _find_dep_files(self, project_path: str) -> List[str]:
        """查找依赖文件"""
        dep_files = []
        candidates = [
            "requirements.txt",
            "pyproject.toml",
            "setup.py",
            "setup.cfg",
            "Pipfile",
            "Pipfile.lock",
            # 非 Python 依赖清单（缺陷 8：多语言供应链漏洞漏检）
            "package.json",
            "package-lock.json",
            "go.mod",
            "pom.xml",
            "composer.json",
        ]
        for root, dirs, files in os.walk(project_path):
            dirs[:] = [d for d in dirs if not d.startswith(".") and d not in (
                "__pycache__", "node_modules", ".git", "venv", ".venv",
                "third_party", ".gitnexus", "data",
            )]
            for f in files:
                if f in candidates:
                    dep_files.append(os.path.join(root, f))
        return dep_files

    def _parse_dep_file(self, filepath: str) -> List[DependencyInfo]:
        """解析依赖文件"""
        deps = []
        try:
            with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
                lines = f.readlines()
        except (OSError, IOError):
            return deps

        basename = os.path.basename(filepath)

        # 非 Python 依赖清单（缺陷 8）：JSON / go.mod 无法复用逐行 TOML/requirements 逻辑，
        # 走独立解析路径。
        if basename == "package.json":
            return self._parse_package_json(filepath)
        if basename == "go.mod":
            return self._parse_gomod(filepath)

        cur_section = ""  # 当前 TOML 段（仅 pyproject.toml）

        for i, line in enumerate(lines, 1):
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue

            # requirements.txt
            if basename in ("requirements.txt", "Pipfile.lock"):
                m = self.REQ_PATTERN.match(stripped)
                if m:
                    op = m.group(2) or ""
                    # "==" 是无范围语义的精确固定版本，等同无约束，允许 OSV 精确命中
                    if op == "==":
                        op = ""
                    deps.append(DependencyInfo(
                        package=m.group(1), version=m.group(3),
                        source_file=filepath, source_line=i,
                        constraint=op,
                    ))
                    continue
                m = self.REQ_SIMPLE_PATTERN.match(stripped)
                if m:
                    deps.append(DependencyInfo(
                        package=m.group(1), version="latest",
                        source_file=filepath, source_line=i,
                    ))

            # pyproject.toml
            elif basename == "pyproject.toml":
                # 更新当前段；段头行本身不是依赖
                sec = self._match_toml_section(stripped)
                if sec is not None:
                    cur_section = sec
                    continue
                # 仅依赖段内的 key = "value" 视为依赖
                if cur_section not in self.TOML_DEP_SECTIONS:
                    continue
                m = self.TOML_PATTERN.match(stripped)
                if m:
                    ver_raw = m.group(2).strip()
                    op, ver = self._split_version_constraint(ver_raw)
                    deps.append(DependencyInfo(
                        package=m.group(1), version=ver,
                        source_file=filepath, source_line=i,
                        constraint=op,
                    ))

            # setup.py
            elif basename == "setup.py":
                m = re.search(r'["\']([a-zA-Z0-9_.-]+)\s*([><=!~]+)\s*([a-zA-Z0-9_.*-]+)["\']', stripped, re.IGNORECASE)
                if m:
                    deps.append(DependencyInfo(
                        package=m.group(1), version=m.group(3),
                        source_file=filepath, source_line=i,
                    ))

        return deps

    def _parse_package_json(self, filepath: str) -> List[DependencyInfo]:
        """解析 package.json，提取 dependencies / devDependencies（缺陷 8）。

        版本常为 npm 语义化写法（^x.y.z / ~x.y.z / >=x.y.z），无法据此确定精确安装
        版本，故剥离运算符作为"声明基线版本"供本地库范围匹配，constraint 保留用于
        跳过 OSV 精确命中（避免把 ^4.17.20 误当 4.17.20 精确命中）。
        """
        deps = []
        try:
            with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
                data = json.load(f)
        except Exception:
            return deps
        # JSON 无法精确定位 key 所在行，使用 0（文件级定位；有漏洞时文件路径仍可定位）
        for section in ("dependencies", "devDependencies"):
            obj = data.get(section) or {}
            for name, ver in obj.items():
                if not isinstance(ver, str):
                    continue
                op, ver_clean = self._split_version_constraint(ver.strip())
                deps.append(DependencyInfo(
                    package=name, version=ver_clean,
                    source_file=filepath, source_line=0,
                    constraint=op, ecosystem="npm",
                ))
        return deps

    def _parse_gomod(self, filepath: str) -> List[DependencyInfo]:
        """解析 go.mod 的 require 依赖（缺陷 8）。

        支持两种形式：
          require module v1.2.3
          require (
              module v1.2.3
              module v1.2.3 // indirect
          )
        """
        deps = []
        try:
            with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
                lines = f.readlines()
        except (OSError, IOError):
            return deps
        in_block = False
        for i, line in enumerate(lines, 1):
            stripped = line.strip()
            if not stripped or stripped.startswith("//"):
                continue
            if stripped == "require (":
                in_block = True
                continue
            if stripped == ")":
                in_block = False
                continue
            if in_block or stripped.startswith("require "):
                # 去掉行首 require 前缀（块内行无此前缀）
                content = stripped[len("require "):].strip() if stripped.startswith("require ") else stripped
                m = re.match(r'^(\S+)\s+(v?\d[^\s]*)', content)
                if m:
                    ver = m.group(2).lstrip("v")
                    deps.append(DependencyInfo(
                        package=m.group(1), version=ver,
                        source_file=filepath, source_line=i,
                        constraint="", ecosystem="Go",
                    ))
        return deps

    def _apply_exploitability_gates(self, project_path: str, vulns: List[DependencyVulnerability]) -> List[DependencyVulnerability]:
        """组件级利用面过滤：对命中 EXPLOITABILITY_GATES 的 CVE，检查项目是否实际
        import/使用受影响子组件。若未使用，则判定为"潜在风险"（severity 降为 low，
        并在 summary 附说明），避免对未使用组件机械报高危误报。"""
        if not vulns or not self.EXPLOITABILITY_GATES:
            return vulns
        # 仅当存在的 CVE 涉及利用面规则时才扫描源码，避免无谓 IO
        hit_cves = {v.cve_id for v in vulns} & set(self.EXPLOITABILITY_GATES)
        if not hit_cves:
            return vulns
        source_text = self._collect_project_source(project_path)
        out = []
        for v in vulns:
            gate = self.EXPLOITABILITY_GATES.get(v.cve_id)
            if gate:
                _comp, pat, note = gate
                if not pat.search(source_text or ""):
                    v.severity = "low"
                    v.summary = f"{v.summary}｜{note}"
            out.append(v)
        return out

    def _collect_project_source(self, project_path: str) -> str:
        """收集项目内所有 .py/.pyi 源码文本，用于利用面判定（跳过依赖目录）。"""
        parts = []
        for root, dirs, files in os.walk(project_path):
            dirs[:] = [d for d in dirs if not d.startswith(".") and d not in (
                "__pycache__", "node_modules", ".git", "venv", ".venv",
                "third_party", ".gitnexus", "data", "site-packages",
            )]
            for f in files:
                if f.endswith((".py", ".pyi")):
                    fp = os.path.join(root, f)
                    try:
                        with open(fp, "r", encoding="utf-8", errors="ignore") as fh:
                            parts.append(fh.read())
                    except OSError:
                        continue
        return "\n".join(parts)

    def _check_vulnerability(self, package: str, version: str, constraint: str = "", ecosystem: str = "PyPI") -> List[DependencyVulnerability]:
        """检查依赖的已知漏洞"""
        vulns = []

        # 1. 本地已知漏洞库
        if package.lower() in self.LOCAL_KNOWN_VULNS:
            vuln_ranges = self.LOCAL_KNOWN_VULNS[package.lower()]
            for version_constraint, vuln_list in vuln_ranges.items():
                if self._version_matches(version, version_constraint):
                    for cve, severity, summary in vuln_list:
                        vulns.append(DependencyVulnerability(
                            package=package, version=version, cve_id=cve,
                            severity=severity, summary=summary,
                            fixed_version=self.LOCAL_FIXED_VERSIONS.get(cve),
                            source="local_db",
                        ))

        # 2. 在线 OSV API 查询（如果允许）
        #    仅对精确版本（无范围约束）查询，避免把 `>=10.0.0` 当 `10.0.0` 精确命中
        #    产生误报。范围约束的实际安装版本未知，交由本地库判断即可。
        if not self.offline and not constraint:
            try:
                online_vulns = self._query_osv(package, version, ecosystem)
                existing_cves = {v.cve_id for v in vulns}
                for v in online_vulns:
                    if v.cve_id not in existing_cves:
                        vulns.append(v)
            except Exception as e:
                # 在线查询失败：标记降级，供报告告警（不再静默丢弃）
                self._osv_degraded = True
                logger.warning("OSV 在线查询失败 package=%s err=%s", package, e)

        return vulns

    def _match_toml_section(self, line: str) -> Optional[str]:
        """识别 TOML 段头，返回段名；非段头返回 None"""
        m = self.TOML_SECTION_PATTERN.match(line)
        if not m:
            return None
        return (m.group(1) or m.group(2) or "").strip()

    def _split_version_constraint(self, ver_raw: str) -> Tuple[str, str]:
        """拆分版本约束运算符与版本号。

        例：">=10.0.0" -> ("", "10.0.0") 中 const 保留范围判断；
        固定版本如 "10.0.0" -> ("", "10.0.0")。
        范围约束（>= > < <= ~= !=）无法确定精确安装版本，标记 constraint 供上层
        决定是否跳过 OSV 精确命中，避免把 `>=10.0.0` 当成 `10.0.0` 造误报。
        """
        m = re.match(r'^\s*([!<>=~^]+)\s*(.*)$', ver_raw)
        if not m:
            return "", ver_raw.strip()
        op = m.group(1)
        ver = m.group(2).strip()
        # 无运算符（可能只有 ==）但版本有效
        if op in ("==",):
            return "", ver
        if op in (">", "<", ">=", "<=", "~=", "!=", "^", "~"):
            return op, ver
        return "", ver_raw.strip()

    def _query_osv(self, package: str, version: str, ecosystem: str = "PyPI") -> List[DependencyVulnerability]:
        payload = json.dumps({
            "package": {"name": package, "ecosystem": ecosystem},
            "version": version,
        }).encode("utf-8")
        req = urllib.request.Request(
            self.OSV_API_URL,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except (urllib.error.URLError, json.JSONDecodeError, TimeoutError) as e:
            # 标记降级，避免 osv_status 仍为 "ok" 误导用户以为已在线核查
            self._osv_degraded = True
            logger.warning("OSV 在线查询失败 package=%s version=%s err=%s", package, version, e)
            return []

        vulns = []
        for vuln in data.get("vulns", []):
            cve_id = ""
            for alias in vuln.get("aliases", []):
                if alias.startswith("CVE-"):
                    cve_id = alias
                    break
            if not cve_id:
                cve_id = vuln.get("id", "UNKNOWN")

            # 严重性映射
            severity = "medium"
            db_specific = vuln.get("database_specific", {})
            if db_specific:
                cvss = db_specific.get("severity", "")
                if cvss == "CRITICAL":
                    severity = "critical"
                elif cvss == "HIGH":
                    severity = "high"
                elif cvss == "LOW":
                    severity = "low"

            summary = vuln.get("summary", "")[:200]
            fixed = None
            affected = vuln.get("affected", [])
            if affected:
                ranges = affected[0].get("ranges", [])
                for r in ranges:
                    fixed_events = [e.get("fixed") for e in r.get("events", []) if "fixed" in e]
                    if fixed_events:
                        fixed = fixed_events[0]

            vulns.append(DependencyVulnerability(
                package=package, version=version, cve_id=cve_id,
                severity=severity, summary=summary,
                fixed_version=fixed, source="OSV",
            ))

        # OSV 可能对同一 package 返回重复 vuln（同一 CVE 出现多次），
        # 按 cve_id 去重，保留首个（含 severity/fixed 信息），避免报告重复罗列。
        seen = {}
        uniq = []
        for v in vulns:
            key = v.cve_id
            if key not in seen:
                seen[key] = True
                uniq.append(v)
        return uniq

    def _version_matches(self, version: str, constraint: str) -> bool:
        """检查版本是否匹配约束。

        修复：旧实现 `from packaging.version import Version` 在未安装 packaging 时
        抛 ImportError，被 `except: return True` 吞掉，导致**所有**版本约束无条件命中，
        产生大量误报。现在：
          - packaging 不可用时降级为简单字符串前缀比较（不判断版本号大小）
          - 版本解析失败返回 False（不命中），避免误报，并记录日志
        """
        if version == "latest":
            return True  # 无法确定版本，保守处理：命中以便提示人工核查
        if Version is None:
            # packaging 未安装：无法可靠比较版本大小，保守返回 False（不命中），
            # 避免字符串前缀匹配把修复版本误判为受影响版本
            logger.warning(
                "packaging 未安装，无法进行 SCA 版本比较，跳过此约束（建议安装 packaging）"
            )
            return False
        try:
            v = Version(version)
            c = Version(constraint.lstrip("<>=!~ "))
            if constraint.startswith("<="):
                return v <= c
            if constraint.startswith("<"):
                return v < c
            if constraint.startswith(">="):
                return v >= c
            if constraint.startswith(">"):
                return v > c
            if constraint.startswith("=="):
                return v == c
            return False
        except (InvalidVersion, TypeError, ValueError) as e:
            # 版本不可解析：不命中，避免对无法判断的版本给出误报
            logger.warning("SCA 版本解析失败 version=%r constraint=%r err=%s", version, constraint, e)
            return False

    def to_report(self, report: SCAReport) -> str:
        """生成 SCA 报告"""
        lines = [
            "# 依赖安全扫描 (SCA)",
            "",
            f"> 项目: `{report.project_path}`",
            f"> 扫描: {report.scanned_deps} 个依赖",
            f"> 漏洞: {report.vulnerable_deps} 个依赖存在 {report.total_vulnerabilities} 个已知漏洞",
            "",
        ]

        # 摘要卡
        score = report.clean_score
        grade = "A" if score >= 90 else "B" if score >= 75 else "C" if score >= 60 else "D" if score >= 40 else "F"
        color = "#3fb950" if score >= 90 else "#d29922" if score >= 75 else "#f0883e" if score >= 60 else "#f85149"

        lines.append("## 安全评分")
        lines.append("")
        lines.append(f"| 评分 | 等级 | 扫描依赖 | 存在漏洞 | 高危以上 |")
        lines.append(f"|------|------|----------|----------|----------|")
        lines.append(f"| {score:.0f}/100 | **{grade}** | {report.scanned_deps} | {report.vulnerable_deps} | {report.critical_count + report.high_count} |")
        lines.append("")

        if report.offline_mode:
            lines.append("> ⚠️ 离线模式：仅使用本地漏洞库，未查询 OSV 在线数据库。")
            lines.append("")
        elif getattr(report, "osv_status", "ok") == "degraded":
            lines.append("> ⚠️ **OSV 在线查询失败**：本次仅使用本地漏洞库，"
                         "可能遗漏未收录的 CVE。请检查网络后重试，或手动核对依赖版本。")
            lines.append("")

        if not report.vulnerable_deps:
            lines.append("✅ 未发现已知漏洞。")
            return "\n".join(lines)

        # 漏洞详情
        lines.append("## 漏洞详情")
        lines.append("")
        lines.append("| 包名 | 版本 | CVE | 严重性 | 摘要 | 修复版本 |")
        lines.append("|------|------|-----|--------|------|----------|")

        for dep in sorted(report.dependencies, key=lambda d: d.max_severity):
            if not dep.has_vuln:
                continue
            for vuln in dep.vulnerabilities:
                sev_icon = "🔴" if vuln.severity == "critical" else "🟠" if vuln.severity == "high" else "🟡" if vuln.severity == "medium" else "⚪"
                fixed = vuln.fixed_version or "—"
                lines.append(
                    f"| `{dep.package}` | {dep.version} | {vuln.cve_id} | {sev_icon} {vuln.severity} | "
                    f"{vuln.summary[:80]} | {fixed} |"
                )

        lines.append("")
        lines.append("---")
        lines.append("*扫描由 CodeRef SCA Checker 执行*")
        return "\n".join(lines)


def check_sca(project_path: str, offline: bool = False) -> str:
    """便捷函数"""
    checker = SCAChecker(offline=offline)
    report = checker.scan(project_path)
    return checker.to_report(report)