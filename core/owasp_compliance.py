# -*- coding: utf-8 -*-
"""
OWASP LLM Top 10 合规检测器 —— 供 MCP 工具 coderef_owasp 调用

设计思路（复用底座，不重复造轮子）：
  1. 调用 AgentSecurityAuditor.audit() 拿到底层 AGENT-SEC-01~26 风险（含韧性缺口）。
  2. 调用 SCAChecker.scan() 补齐依赖供应链维度（同 Pipe._sca 的用法）。
  3. 依据 9.2 覆盖矩阵，把全部底层风险映射/归并到 OWASP LLM01~LLM10 十类。
  4. 补充文档要求新增的两个维度：
       - LLM09 过度依赖：检测幻觉/未验证依赖（模型输出直接执行、生成代码直接落盘等）。
       - LLM10 模型窃取：检测模型/提示词外泄防护缺失（提示词模板日志外泄、凭证硬编码等）。
  5. 未覆盖到的维度标注 covered=false 并给出说明，避免过度承诺（遵守文档 15.2 风险对策）。

工程约定：
  - 纯标准库 + 复用底座，不引入第三方依赖。
  - 中文注释与输出。
  - magic number 提取为模块级常量，不修改 config/settings.py。
  - LLM 能力缺失时优雅降级（本模块不依赖 LLM，天然降级）。
  - 不静默吞异常：检测器自身异常记入 errors 并向调用方暴露。
  - 不做 MCP 注册（由外部统一接线）。

作者: CodeRef Team
版本: v1.0
"""

import os
import re
import json
import time
from collections import defaultdict
from typing import Dict, List, Optional

# ─────────────────────────────────────────────────────────────────────────────
# 模块级常量（magic number / 配置集中于此，避免散落魔法值）
# ─────────────────────────────────────────────────────────────────────────────

# OWASP LLM Top 10 十类元数据：(显示名, 简要说明)
OWASP_CATEGORIES: Dict[str, dict] = {
    "LLM01": {"name": "提示注入", "desc": "直接/间接提示注入：用户或外部内容未经隔离进入 prompt，可绕过模型安全限制"},
    "LLM02": {"name": "不安全输出处理", "desc": "模型输出未经无害化处理即用于渲染/执行，可能引入 XSS、代码注入等下游风险"},
    "LLM03": {"name": "训练数据投毒", "desc": "训练/微调/RAG 数据源被污染，导致模型输出被定向操纵"},
    "LLM04": {"name": "模型拒绝服务", "desc": "资源耗尽：无限循环、无 token 预算、无限流/超时、韧性缺口导致服务不可用"},
    "LLM05": {"name": "供应链漏洞", "desc": "依赖/组件存在已知漏洞或来源不可信，可被利用实施攻击"},
    "LLM06": {"name": "敏感信息泄露", "desc": "PII、凭据、内部数据经日志/响应/外发通道泄露"},
    "LLM07": {"name": "不安全插件设计", "desc": "插件/扩展权限过大、未校验输入输出，可作为攻击跳板"},
    "LLM08": {"name": "过度代理", "desc": "Agent 权限过大、未经人类确认即可执行危险操作（文件/命令/写库/自主行为）"},
    "LLM09": {"name": "过度依赖", "desc": "对模型输出过度信任、幻觉内容未经验证即被使用（本模块新增检测维度）"},
    "LLM10": {"name": "模型窃取", "desc": "模型/提示词/凭证外泄防护缺失，导致核心资产被逆向或盗用（本模块新增检测维度）"},
}

# 底层风险 id → OWASP 类别映射（9.2 覆盖矩阵）
MAPPING_BY_ID: Dict[str, str] = {
    # 提示注入
    "AGENT-SEC-01": "LLM01", "AGENT-SEC-02": "LLM01",
    # 上下文操纵 = 间接提示注入
    "AGENT-SEC-03": "LLM01", "AGENT-SEC-04": "LLM01",
    # 上下文溢出 → 资源耗尽
    "AGENT-SEC-05": "LLM04",
    # 工具滥用 → 过度代理
    "AGENT-SEC-06": "LLM08", "AGENT-SEC-07": "LLM08",
    "AGENT-SEC-08": "LLM08", "AGENT-SEC-09": "LLM08",
    # 预算/资源耗尽 → 模型拒绝服务
    "AGENT-SEC-10": "LLM04", "AGENT-SEC-11": "LLM04", "AGENT-SEC-12": "LLM04",
    # 数据泄露 → 敏感信息泄露
    "AGENT-SEC-13": "LLM06", "AGENT-SEC-14": "LLM06", "AGENT-SEC-15": "LLM06",
    # 自主行为 → 过度代理
    "AGENT-SEC-16": "LLM08", "AGENT-SEC-17": "LLM08",
    # PII 泄露 → 敏感信息泄露
    "AGENT-SEC-18": "LLM06", "AGENT-SEC-19": "LLM06",
    "AGENT-SEC-20": "LLM06", "AGENT-SEC-21": "LLM06",
    # 安全配置
    "AGENT-SEC-22": "LLM06",   # DEBUG=True 生产开启 → 信息暴露
    "AGENT-SEC-23": "LLM05",   # 不安全反序列化 → 供应链/组件风险
    "AGENT-SEC-24": "LLM06",   # CORS 过宽 → 数据暴露
    "AGENT-SEC-25": "LLM04",   # 无超时 → 请求挂起（可用性）
    "AGENT-SEC-26": "LLM04",   # 无限流 → 资源耗尽
    # 韧性缺口
    "AGENT-RESILIENCE-01": "LLM04", "AGENT-RESILIENCE-02": "LLM04",
    "AGENT-RESILIENCE-03": "LLM04", "AGENT-RESILIENCE-04": "LLM04",
    "AGENT-RESILIENCE-05": "LLM04", "AGENT-RESILIENCE-06": "LLM04",
    "AGENT-RESILIENCE-07": "LLM04", "AGENT-RESILIENCE-08": "LLM04",
    "AGENT-RESILIENCE-09": "LLM04",
    "AGENT-RESILIENCE-10": "LLM06",  # 日志上下文绑定 → 审计/隐私
}

# 底层风险 category → OWASP 类别（兜底，处理未收录 risk_id 的情况）
MAPPING_BY_CATEGORY: Dict[str, str] = {
    "prompt_injection": "LLM01",
    "context_manipulation": "LLM01",
    "tool_misuse": "LLM08",
    "budget": "LLM04",
    "data_exfil": "LLM06",
    "pii_leak": "LLM06",
    "security_config": "LLM04",
    "autonomous": "LLM08",
    "knowledge": "LLM03",
    "resilience_gap": "LLM04",
}

# severity 映射：底层 blocker/critical/high → OWASP-HIGH，medium → MEDIUM，low → LOW
SEVERITY_WEIGHT: Dict[str, int] = {
    "blocker": 5, "critical": 4, "high": 3, "medium": 2, "low": 1,
}
SEVERITY_TO_OWASP: Dict[str, str] = {
    "blocker": "HIGH", "critical": "HIGH", "high": "HIGH",
    "medium": "MEDIUM", "low": "LOW",
}

# 文件遍历时排除的目录
EXCLUDE_DIRS = {
    "__pycache__", "node_modules", ".git", "venv", ".venv", "env",
    "Lib", "lib", "lib64", "site-packages", "dist-packages",
    "third_party", ".gitnexus", "data", "docs", "reports",
    "cache", "coderef-report", "logs", "build", "dist",
}

# 排除行模式（注释/docstring）
EXCLUDE_LINE_PATTERNS = [
    re.compile(r'^\s*#'),
    re.compile(r'^\s*"""'),
    re.compile(r"^\s*'''"),
    re.compile(r'^\s*//'),
]

# ─────────────────────────────────────────────────────────────────────────────
# 新增检测维度模式（LLM09 / LLM10）
# ─────────────────────────────────────────────────────────────────────────────

# LLM09 过度依赖：幻觉 / 未验证依赖检测
LLM09_OVERRELIANCE_PATTERNS = [
    (re.compile(r'(?:eval|exec|os\.system|subprocess)\s*\(\s*(?:result|response|answer|output|assistant_message|model_output|llm_response|content)', re.IGNORECASE),
     "OWASP-LLM09-01", "LLM输出直接执行", "high",
     "检测到 LLM 输出直接作为代码/命令执行，未做任何校验，模型幻觉或被注入内容将导致任意代码执行",
     "对 LLM 输出做白名单/语法/AST 校验，绝不直接 eval/exec 模型输出"),
    (re.compile(r'(?:open|write|save|dump|(?:[a-z_]\w*)\s*\.)\s*\([^)]*\)\s*\.\s*(?:write|w)\s*\(\s*(?:result|response|output|code|answer)', re.IGNORECASE),
     "OWASP-LLM09-02", "LLM生成代码直接落盘", "high",
     "检测到模型生成的代码/内容直接写入文件，未经人工复核即投入使用，幻觉代码可能上生产",
     "对模型生成的代码进行人工 Code Review 后再合入，增加单元测试与静态检查"),
    (re.compile(r'if\s+(?:result|response|answer|output|model_output|completion)\s*[=!]=', re.IGNORECASE),
     "OWASP-LLM09-03", "无条件信任模型输出", "medium",
     "检测到将 LLM 输出直接作为业务判断依据，未加入置信度/校验机制，存在幻觉误判风险",
     "对关键决策增加验证：交叉校验、置信度阈值、人工兜底、日志留痕"),
    (re.compile(r'(?:pip|conda|npm|yarn|go get)\s+install\b(?:[^\n]*--(?:no-index|index-url|registry|extra-index-url))?', re.IGNORECASE),
     "OWASP-LLM09-04", "无验证依赖引入", "medium",
     "检测到依赖安装未做来源/版本锁定，供应链存在投毒风险，尤其是模型推荐的可执行安装",
     "依赖锁定精确版本并校验哈希，来源仅限可信仓库，避免盲目信任模型推荐"),
]

# LLM10 模型窃取：模型 / 提示词外泄防护
LLM10_MODEL_THEFT_PATTERNS = [
    (re.compile(r'(?:logger\.(?:info|debug|error|warning)|print|logging)\s*\(.*(?:system_prompt|prompt_template|instructions|chain_of_thought|few_shot)', re.IGNORECASE),
     "OWASP-LLM10-01", "提示词模板外泄", "high",
     "检测到日志/输出中可能泄露 system prompt 或提示词模板，攻击者可据此逆向模型行为与绕过策略",
     "提示词模板属核心资产，禁止写入日志，必要时做脱敏/占位符"),
    (re.compile(r'(?:api_key|apikey|api\.key|api_key\s*=|openai_api_key|anthropic_api_key|gemini_api_key|model_key)\s*=\s*["\'][^"\']{4,}', re.IGNORECASE),
     "OWASP-LLM10-02", "模型凭证硬编码", "high",
     "检测到模型 API Key 硬编码在源码中，可被提取用于盗用模型服务",
     "使用环境变量/密钥管理系统注入凭证，禁止硬编码，并周期性轮换密钥"),
    (re.compile(r'(?:model_id|model_name|weights|checkpoint|\.safetensors|\.ckpt|\.bin)\s*.*(?:logger|print|open|save|send)', re.IGNORECASE),
     "OWASP-LLM10-03", "模型资产外泄", "medium",
     "检测到模型权重/配置以日志或文件形式暴露，可能泄露模型架构与行为特征",
     "模型权重与配置仅保存在受控存储，禁止随代码/日志分发"),
    (re.compile(r'(?:requests\.(?:post|put)|httpx\.(?:post|put)|aiohttp\.(?:post|put))\s*\([^)]*(?:prompt|system_prompt|messages)', re.IGNORECASE),
     "OWASP-LLM10-04", "提示词外发至外部端点", "medium",
     "检测到将完整提示词发送到外部端点，若目的地不受控则提示词与业务数据均外泄",
     "外发目的地做白名单校验，提示词内容脱敏，记录外发审计日志"),
]

# 报告 / 生成器元信息
REPORT_TITLE = "OWASP LLM Top 10 合规检测报告"
REPORT_VERSION = "1.0"


class OWASPCompliance:
    """OWASP LLM Top 10 合规检测器"""

    def __init__(self):
        self.risks = []          # AgentSecurityRisk 列表（from base auditor）
        self.sca_findings = []   # SCA 供应链 findings
        self.custom_findings = []  # LLM09/LLM10 自定义 findings
        self.errors = []

    # ─────────────────────────────────────────────────────────────
    # 主入口
    # ─────────────────────────────────────────────────────────────
    def check(self, project_path: str, out_format: str = "json") -> dict:
        """执行 OWASP LLM Top 10 合规检测。

        参数:
            project_path: 待检测项目绝对路径
            out_format:   "json" 返回结构化 dict；"report" 返回含中文 markdown 合规报告的 dict

        返回:
            结构化 dict，见模块 docstring / 自测脚本。
        """
        t0 = time.time()
        self.risks = []
        self.sca_findings = []
        self.custom_findings = []
        self.errors = []
        if not project_path or not os.path.isdir(project_path):
            raise ValueError(f"project_path 不存在或不是目录: {project_path}")

        # 1) 复用底座：Agent 安全审计
        try:
            from core.agent_security_auditor import AgentSecurityAuditor
            auditor = AgentSecurityAuditor()
            self.risks = auditor.audit(project_path)
        except Exception as e:  # 不静默吞异常：记录并暴露
            self.errors.append(f"agent_security_auditor: {e}")

        # 2) 复用底座：SCA 依赖供应链（同 Pipe._sca 用法）
        try:
            from core.sca_checker import SCAChecker
            checker = SCAChecker()
            checker.scan(project_path)
            report = getattr(checker, "report", None)
            if report is not None:
                for dep in getattr(report, "dependencies", []):
                    for v in getattr(dep, "vulnerabilities", []):
                        self.sca_findings.append({
                            "file": getattr(dep, "source_file", ""),
                            "line": getattr(dep, "source_line", 0),
                            "title": "{} {} - {}".format(
                                getattr(dep, "package", ""),
                                getattr(dep, "version", ""),
                                getattr(v, "cve_id", "")),
                            "detail": getattr(v, "summary", ""),
                            "suggestion": "升级到 {} 修复供应链漏洞".format(
                                getattr(v, "fixed_version", None) or "最新可用版本"),
                            "severity": getattr(v, "severity", "medium"),
                        })
        except Exception as e:
            self.errors.append(f"sca_checker: {e}")

        # 3) 新增维度：LLM09 / LLM10（项目级扫描）
        try:
            self.custom_findings = self._scan_custom_dimensions(project_path)
        except Exception as e:
            self.errors.append(f"owasp_custom: {e}")

        # 4) 归并到 OWASP 十类
        categories = self._build_categories(project_path)

        result = {
            "tool": "coderef_owasp",
            "status": "completed",
            "version": REPORT_VERSION,
            "project_path": project_path,
            "out_format": out_format,
            "summary": self._build_summary(categories),
            "categories": categories,
            "errors": self.errors,
            "elapsed": round(time.time() - t0, 2),
        }

        # 5) report 格式：追加中文 markdown 合规报告
        if out_format == "report":
            result["report_markdown"] = self._to_markdown(result)

        return result

    # ─────────────────────────────────────────────────────────────
    # 自定义维度项目级扫描（LLM09 / LLM10）
    # ─────────────────────────────────────────────────────────────
    def _scan_custom_dimensions(self, project_path: str) -> List[dict]:
        """扫描 .py（及 .env/.txt）文件，检测 LLM09 过度依赖与 LLM10 模型窃取。

        返回 findings 列表，每条含 {code, severity, file, line, title, detail, suggestion}。
        """
        findings = []
        for root, dirs, files in os.walk(project_path):
            dirs[:] = [d for d in dirs if not d.startswith(".") and d not in EXCLUDE_DIRS]
            for f in files:
                if not (f.endswith(".py") or f.endswith(".env") or f.endswith(".txt")):
                    continue
                fpath = os.path.join(root, f)
                findings.extend(self._scan_custom_file(fpath))
        return findings

    def _scan_custom_file(self, filepath: str) -> List[dict]:
        """扫描单个文件的自定义维度。"""
        findings = []
        try:
            with open(filepath, "r", encoding="utf-8", errors="ignore") as fh:
                lines = fh.readlines()
        except (OSError, IOError):
            return findings

        in_docstring = False
        for i, line in enumerate(lines, 1):
            stripped = line.strip()
            if not stripped:
                continue
            if stripped.startswith('"""') or stripped.startswith("'''"):
                in_docstring = not in_docstring
                continue
            if in_docstring:
                continue
            if any(p.match(stripped) for p in EXCLUDE_LINE_PATTERNS):
                continue
            # 跳过检测器自身的模式定义（正则/描述字符串）
            if self._is_def_self(stripped):
                continue

            for patterns, code in [
                (LLM09_OVERRELIANCE_PATTERNS, "LLM09"),
                (LLM10_MODEL_THEFT_PATTERNS, "LLM10"),
            ]:
                for pattern, fid, name, severity, detail, suggestion in patterns:
                    if pattern.search(stripped):
                        findings.append({
                            "code": code,
                            "severity": severity,
                            "file": filepath,
                            "line": i,
                            "title": f"[{fid}] {name}",
                            "detail": detail,
                            "suggestion": suggestion,
                        })
        return findings

    def _is_def_self(self, line: str) -> bool:
        """判断该行是否为检测器自身的模式定义（避免自命中）。"""
        if re.search(r'OWASP-LLM0\d-\d+', line):
            return True
        if re.search(r're\.compile\(', line):
            return True
        if re.search(r'["\']检测到', line) or re.search(r'["\']使用\s', line):
            return True
        return False

    # ─────────────────────────────────────────────────────────────
    # 归并 / 输出
    # ─────────────────────────────────────────────────────────────
    def _build_categories(self, project_path: str) -> dict:
        """把全部风险归并到 OWASP 十类，生成 {code: category_dict}。"""
        # 按 OWASP 类别聚合底层风险
        buckets = defaultdict(list)  # code -> list[raw_item]
        for r in self.risks:
            code = MAPPING_BY_ID.get(r.risk_id) or MAPPING_BY_CATEGORY.get(r.category)
            if not code:
                # 未映射到任何类别的风险统一归入 LLM06（信息泄露兜底），并记录
                code = "LLM06"
            buckets[code].append({
                "file": r.file_path,
                "line": r.line_number,
                "title": f"[{r.risk_id}] {r.risk_name}",
                "detail": r.detail,
                "suggestion": r.suggestion,
                "severity": r.severity,
            })

        # SCA 供应链 → LLM05
        buckets["LLM05"].extend(self.sca_findings)

        # 新增维度 → LLM09 / LLM10
        for f in self.custom_findings:
            buckets[f["code"]].append(f)

        categories = {}
        for code, meta in OWASP_CATEGORIES.items():
            items = buckets.get(code, [])
            if items:
                max_sev = self._max_owasp_severity(items)
                categories[code] = {
                    "code": code,
                    "name": meta["name"],
                    "severity": max_sev,
                    "findings": [
                        {
                            "file": self._rel(item.get("file", ""), project_path),
                            "line": item.get("line", 0),
                            "title": item.get("title", ""),
                            "detail": item.get("detail", ""),
                            "suggestion": item.get("suggestion", ""),
                        }
                        for item in items
                    ],
                    "covered": True,
                }
            else:
                categories[code] = {
                    "code": code,
                    "name": meta["name"],
                    "severity": "LOW",
                    "findings": [],
                    "covered": False,
                    "note": self._uncovered_note(code),  # 说明为何未覆盖（避免过度承诺）
                }
        return categories

    @staticmethod
    def _max_owasp_severity(items: List[dict]) -> str:
        """取一批 finding 的最高 OWASP 严重性（HIGH/MEDIUM/LOW）。"""
        best = "LOW"
        best_w = 0
        for it in items:
            w = SEVERITY_WEIGHT.get(it.get("severity", "low"), 1)
            if w > best_w:
                best_w = w
                best = SEVERITY_TO_OWASP.get(it.get("severity", "low"), "LOW")
        return best

    @staticmethod
    def _rel(path: str, project_path: str) -> str:
        """把绝对路径转为相对项目路径（可读），非项目内则保留原样。"""
        if not path:
            return ""
        try:
            rel = os.path.relpath(path, project_path)
            if rel == "." or rel.startswith(".."):
                return path
            return rel
        except ValueError:
            return path

    @staticmethod
    def _uncovered_note(code: str) -> str:
        """未覆盖维度的原因说明（遵守文档 15.2 风险对策，避免过度承诺）。"""
        notes = {
            "LLM02": "当前扫描器未对该维度做主动检测：需要模型输出无害化/渲染安全检查，属人工评审与下游防护范畴，未纳入自动化到静态扫描。",
            "LLM03": "当前扫描器未对该维度做主动检测：训练数据投毒需访问训练集/数据血缘，属数据治理范畴，静态扫描无法覆盖。",
            "LLM07": "当前扫描器未对该维度做主动检测：插件/扩展安全需审查插件清单与权限模型，暂未纳入本扫描。",
        }
        return notes.get(code, "该维度本次扫描未发现具体风险，也不存在对应底层检测规则，保持谨慎未做判定。")

    def _build_summary(self, categories: dict) -> dict:
        covered = [c["code"] for c in categories.values() if c.get("covered")]
        uncovered = [c["code"] for c in categories.values() if not c.get("covered")]
        total = sum(len(c["findings"]) for c in categories.values() if c.get("covered"))
        high = sum(1 for c in categories.values() if c.get("severity") == "HIGH" and c.get("covered"))
        medium = sum(1 for c in categories.values() if c.get("severity") == "MEDIUM" and c.get("covered"))
        low = sum(1 for c in categories.values() if c.get("severity") == "LOW" and c.get("covered"))
        return {
            "covered_count": len(covered),
            "uncovered_count": len(uncovered),
            "covered": covered,
            "uncovered": uncovered,
            "total_findings": total,
            "high": high,
            "medium": medium,
            "low": low,
            "errors": len(self.errors),
            "compliance_note": (
                "已对 {} 个维度形成覆盖判定，{} 个维度未覆盖（已说明原因，不夸大覆盖范围）。"
                .format(len(covered), len(uncovered))
            ),
        }

    def _to_markdown(self, result: dict) -> str:
        """生成中文 markdown 合规报告。"""
        s = result["summary"]
        cats = result["categories"]
        lines = [
            "# {}".format(REPORT_TITLE),
            "",
            "> 项目: `{}`".format(result["project_path"]),
            "> 检测时间: {} | 耗时: {}s | 工具: coderef_owasp v{}".format(
                time.strftime("%Y-%m-%d %H:%M"), result.get("elapsed", "-"), REPORT_VERSION),
            "",
            "## 覆盖概览",
            "",
            "| 已覆盖维度 | 未覆盖维度 | 命中项 | HIGH | MEDIUM | LOW |",
            "|---|---|---|---|---|---|",
            "| {} | {} | {} | {} | {} | {} |".format(
                s["covered_count"], s["uncovered_count"],
                s["total_findings"], s["high"], s["medium"], s["low"]),
            "",
            "> {}".format(s["compliance_note"]),
            "",
        ]

        if result.get("errors"):
            lines.append("## 检测器异常")
            lines.append("")
            for e in result["errors"]:
                lines.append("- `{}`".format(e))
            lines.append("")

        lines.append("## OWASP 十类合规明细")
        lines.append("")

        for code, meta_order in zip(OWASP_CATEGORIES.keys(), OWASP_CATEGORIES.values()):
            c = cats[code]
            icon = {"HIGH": "🔴", "MEDIUM": "🟠", "LOW": "🟡"}.get(c["severity"], "🟡")
            status = "覆盖" if c["covered"] else "未覆盖"
            lines.append("### {} {} {}".format(code, c["name"], "({})".format(status)))
            lines.append("")
            lines.append("> 严重性: {} {} | 说明: {}".format(icon, c["severity"], meta_order["desc"]))
            lines.append("")
            if not c["covered"]:
                lines.append("**未覆盖说明**：{}".format(c.get("note", "")))
                lines.append("")
                continue
            fs = c["findings"]
            lines.append("| 文件 | 行号 | 标题 | 描述 | 建议 |")
            lines.append("|------|------|------|------|------|")
            for f in fs[:20]:
                lines.append("| `{}` | {} | {} | {} | {} |".format(
                    f["file"] or "-", f["line"] or "-",
                    f["title"][:40], f["detail"][:60], f["suggestion"][:60]))
            if len(fs) > 20:
                lines.append("| ... | ... | ... | （还有 {} 条） | ... |".format(len(fs) - 20))
            lines.append("")

        lines.append("---")
        lines.append("")
        lines.append("### 关于本报告")
        lines.append("")
        lines.append("本报告基于 **OWASP Top 10 for LLM Applications**（LLM01~LLM10）生成，")
        lines.append("底层复用 Agent Security Auditor 与 SCA 依赖扫描能力，并补充 LLM09 过度依赖、")
        lines.append("LLM10 模型窃取两个新增维度。未覆盖维度已如实标注，避免对扫描能力过度承诺。")
        lines.append("")
        lines.append("*扫描由 CodeRef OWASP Compliance 执行*\n")

        return "\n".join(lines)


def compute_owasp_compliance(project_path: str, out_format: str = "json") -> dict:
    """便捷函数：供 MCP 工具 coderef_owasp 调用。"""
    return OWASPCompliance().check(project_path, out_format=out_format)