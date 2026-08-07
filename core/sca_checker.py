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
        r'^\s*["\']?([a-zA-Z0-9_.-]+)["\']?\s*=\s*["\']([><=!~]*\s*[a-zA-Z0-9_.*-]+)["\']',
        re.IGNORECASE
    )

    # OSV API endpoint
    OSV_API_URL = "https://api.osv.dev/v1/query"
    OSV_BATCH_URL = "https://api.osv.dev/v1/querybatch"

    # 已知高危包的本地补充（无需联网）
    LOCAL_KNOWN_VULNS = {
        "pillow": {
            "<10.0.0": [
                ("CVE-2023-50447", "high", "Arbitrary code execution via PIL.ImageMath.eval"),
            ],
            "<9.0.0": [
                ("CVE-2022-22817", "critical", "PIL.ImageMath.eval arbitrary code execution"),
                ("CVE-2022-22816", "high", "PIL.ImagePath.Path arbitrary code execution"),
            ],
        },
        "requests": {
            "<2.31.0": [
                ("CVE-2023-32681", "medium", "Proxy-Authorization header leak on redirect"),
            ],
        },
        "urllib3": {
            "<2.0.7": [
                ("CVE-2023-45803", "medium", "Request body not stripped after redirect"),
                ("CVE-2023-43804", "high", "Cookie leak via redirect cross-origin"),
            ],
        },
        "django": {
            "<5.0.0": [
                ("CVE-2024-27306", "high", "Potential DoS in django.utils.text.Truncator"),
                ("CVE-2024-24680", "high", "Potential DoS in intcomma template filter"),
            ],
            "<4.2.0": [
                ("CVE-2023-43665", "high", "Potential DoS in django.utils.text.Truncator"),
            ],
        },
        "flask": {
            "<3.0.0": [
                ("CVE-2023-30861", "high", "Cookie jar overflow via large session cookie"),
            ],
        },
        "jinja2": {
            "<3.1.3": [
                ("CVE-2024-22195", "high", "Sandbox escape via xmlattr filter"),
            ],
        },
        "certifi": {
            "<2024.0.0": [
                ("CVE-2023-37920", "high", "e-Tugra root certificate removal"),
            ],
        },
        "cryptography": {
            "<42.0.0": [
                ("CVE-2023-50782", "high", "Null pointer dereference in PKCS12 parsing"),
                ("CVE-2023-49083", "high", "Null pointer dereference in load_pem_pkcs7_certificates"),
            ],
        },
        "aiohttp": {
            "<3.9.0": [
                ("CVE-2024-23334", "high", "Directory traversal via static file serving"),
                ("CVE-2024-23829", "high", "HTTP request smuggling via malformed Content-Length"),
            ],
        },
        "langchain": {
            "<0.1.0": [
                ("CVE-2023-46229", "critical", "SSRF via crafted URL in WebBaseLoader"),
                ("CVE-2023-44467", "high", "Prompt injection via crafted input"),
            ],
        },
        "openai": {
            "<1.0.0": [
                ("CVE-2023-47129", "high", "API key leak via debug logging"),
            ],
        },
        # numpy：旧表 CVE-2023-32698 归属错误（非 numpy），已移除，避免误报。
        # 本地表不收录 numpy（依赖多为 >= ,由 OSV 在线查询兜底）
        "pandas": {
            "<2.1.0": [
                ("CVE-2023-32690", "high", "Arbitrary code execution via crafted pickle file"),
            ],
        },
        "tensorflow": {
            "<2.15.0": [
                ("CVE-2023-49070", "critical", "Heap buffer overflow via sparse tensor"),
                ("CVE-2023-49071", "high", "Null pointer dereference via ragged tensor"),
            ],
        },
        "torch": {
            "<2.2.0": [
                ("CVE-2024-21751", "high", "Arbitrary code execution via pickle deserialization"),
            ],
        },
        "transformers": {
            "<4.37.0": [
                ("CVE-2024-22052", "high", "Deserialization of untrusted data via pickle"),
            ],
        },
        "gradio": {
            "<4.0.0": [
                ("CVE-2024-0964", "critical", "Path traversal via file upload"),
                ("CVE-2024-0965", "high", "SSRF via /proxy route"),
            ],
        },
        # fastapi：旧表 CVE-2024-24762 实为 python-multipart 的 ReDoS（fastapi 仅间接依赖），
        # 归属错误已移除，避免对 fastapi 本身误报。
        "python-multipart": {
            "<0.0.7": [
                ("CVE-2024-24762", "high", "ReDoS via crafted Content-Type header"),
            ],
        },
        "pydantic": {
            "<2.5.0": [
                ("CVE-2023-45827", "medium", "Information exposure via error messages"),
            ],
        },
        # sqlalchemy：旧表 CVE-2023-48795 实为 SSH Terrapin 攻击（paramiko/OpenSSH），
        # 与 SQLAlchemy 无关，归属错误已移除。
        "pyyaml": {
            "<6.0.1": [
                ("CVE-2020-14343", "critical", "Arbitrary code execution via yaml.load()"),
            ],
        },
        "reportlab": {
            "<4.0.0": [
                ("CVE-2023-33733", "critical", "Remote code execution via crafted PDF"),
            ],
        },
        "lxml": {
            "<5.0.0": [
                ("CVE-2023-29469", "high", "DoS via crafted XML entity expansion"),
            ],
        },
        "werkzeug": {
            "<3.0.0": [
                ("CVE-2023-46136", "high", "DoS via multipart form data parsing"),
            ],
        },
        "gunicorn": {
            "<22.0.0": [
                ("CVE-2024-1135", "high", "HTTP request smuggling via Transfer-Encoding"),
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
        "CVE-2023-32690": "2.0.3",    # pandas
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
            vulns = self._check_vulnerability(dep.package, dep.version)
            # 过滤 cache 白名单中的 CVE 误报
            vulns = [v for v in vulns if not SharedFilter.is_security_whitelisted(v.cve_id, dep.source_file, dep.source_line)]
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

        for i, line in enumerate(lines, 1):
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue

            # requirements.txt
            if basename in ("requirements.txt", "Pipfile.lock"):
                m = self.REQ_PATTERN.match(stripped)
                if m:
                    deps.append(DependencyInfo(
                        package=m.group(1), version=m.group(3),
                        source_file=filepath, source_line=i,
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
                m = self.TOML_PATTERN.match(stripped)
                if m:
                    deps.append(DependencyInfo(
                        package=m.group(1), version=m.group(2).strip().lstrip("><=!~ "),
                        source_file=filepath, source_line=i,
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

    def _check_vulnerability(self, package: str, version: str) -> List[DependencyVulnerability]:
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
        if not self.offline:
            try:
                online_vulns = self._query_osv(package, version)
                existing_cves = {v.cve_id for v in vulns}
                for v in online_vulns:
                    if v.cve_id not in existing_cves:
                        vulns.append(v)
            except Exception as e:
                # 在线查询失败：标记降级，供报告告警（不再静默丢弃）
                self._osv_degraded = True
                logger.warning("OSV 在线查询失败 package=%s err=%s", package, e)

        return vulns

    def _query_osv(self, package: str, version: str) -> List[DependencyVulnerability]:
        """查询 OSV 数据库"""
        payload = json.dumps({
            "package": {"name": package, "ecosystem": "PyPI"},
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
        except (urllib.error.URLError, json.JSONDecodeError, TimeoutError):
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

        return vulns

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
            # packaging 未安装：仅能按约束前缀做字符串比较，无法判断大小
            logger.warning("packaging 未安装，SCA 版本比较降级为字符串前缀匹配")
            norm = constraint.lstrip("<>=!~ ")
            return version.startswith(norm)
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