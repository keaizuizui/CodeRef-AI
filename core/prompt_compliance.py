# -*- coding: utf-8 -*-
"""
prompt_compliance — Prompt 审计面：确定性注入风险 + 一致性检测（4.3 诚实话护栏）

目标读者：编程 AI 与 AI 时代治理者。Prompt 是 AI Agent 的核心资产，也是注入与
一致性污染的源头。本模块用纯规则（确定性、不依赖 LLM）对抽取出的 prompt 做两类审计：
  1. 注入风险   —— 反向指令注入 / 越狱 / 提示词泄露 / 未隔离的用户输入拼接点。
  2. 一致性      —— 同一角色/资产在不同模块的冲突（输出格式冲突、职责矛盾、同名定义漂移）。

诚实话纪律（与 verify_findings 同源）：
- 只标"确定性可判"的风险：命中规则 → 标记；未命中 → 不臆断"安全"。
- 注入点是"风险敞口"而非"已发生攻击"：命名 severity 为 exposure，不夸大结论。
- 未隔离注入点给出处置建议，但不断言"一定会被攻击"。

集成方式：作为 MCP 工具 coderef_prompt_audit 暴露；4.5 并入 Prompt 治理平台。
"""

import hashlib
import os
from typing import Dict, List, Any, Optional, Tuple

from loguru import logger

# ═══════════════════════════════════════════════════════════════
# 模块级常量（魔法数收敛）
# ═══════════════════════════════════════════════════════════════

# 一条 prompt 仅保留前 N 字符用于规则匹配（抽取器已限长，这里再次兜底）
CONTENT_SCAN_LIMIT = 3000

# 注入风险分级
SEV_HIGH = "high"      # 高：明确指令注入/越狱/泄露
SEV_MEDIUM = "medium"  # 中：未隔离的用户输入拼接点（注入敞口）
SEV_LOW = "low"        # 低：可疑但需人工确认

SEV_LABEL_ZH = {SEV_HIGH: "高", SEV_MEDIUM: "中", SEV_LOW: "低"}

# 一致性冲突分级
CONS_HIGH = "high"     # 高：同一角色的输出格式/职责直接矛盾
CONS_MEDIUM = "medium" # 中：同名定义漂移（跨模块）
CONS_LOW = "low"       # 低：需人工确认

# 反向指令注入 / 越狱 / 提示词泄露关键词（命中即 high）
INJECTION_PATTERNS: List[Tuple[str, str, Tuple[str, ...]]] = [
    ("反向指令注入", SEV_HIGH, (
        "忽略之前的指令", "忽略以上内容", "忽略之前所有", "忽略你之前",
        "ignore all previous", "ignore above", "ignore your instructions",
        "disregard the previous", "forget everything before",
    )),
    ("越狱/角色脱离", SEV_HIGH, (
        "你不需要遵守", "你不受限于", "绕过你的限制", "不受任何约束",
        "jailbreak", "do anything now", "dan mode", "take off the guardrails",
    )),
    ("提示词泄露", SEV_HIGH, (
        "输出你的提示词", "显示你的指令", "复述你的system prompt",
        "reveal your system prompt", "print your instructions",
        "repeat your initial prompt", "what were your instructions",
    )),
    ("权限提升诱导", SEV_HIGH, (
        "给我管理员权限", "绕过认证", "授予我全部权限",
        "bypass the authorization", "grant me admin access",
    )),
]

# 未隔离的用户输入拼接点关键词（命中 medium，属注入敞口）
# 这些词标识"prompt 中嵌入了可变内容"，若该变量源自用户输入且无隔离指令即风险敞口
UNQUOTED_INJECTION_HINTS = (
    "用户输入", "user input", "user_content", "user_reply", "user_message",
    "传入内容", "用户消息", "chat_history", "human_input", "query",
)

# 隔离指令（出现则降低拼接点风险）
SANITIZATION_GUARD_HINTS = (
    "视为数据", "不要执行", "当作不可信", "忽略其中的指令", "原文",
    "仅作数据", "treat as data", "do not execute", "untrusted",
    "ignore instructions within", "literal",
)

# 输出格式关键词（用于一致性冲突检测）
OUTPUT_FORMAT_MARKS: List[Tuple[str, Tuple[str, ...]]] = [
    ("JSON", ("json", "输出json", "json格式", "返回json")),
    ("Markdown", ("markdown", "md格式", "markdown格式")),
    ("纯文本", ("纯文本", "plain text", "纯text")),
    ("表格", ("表格", "table", "markdown表格")),
]

# 职责矛盾信号：同一角色的"必须/禁止"对
CONTRADICTION_PAIRS: List[Tuple[str, str]] = [
    ("必须输出中文", ("必须输出英文", "一律用英文", "用英文输出")),
    ("必须用JSON", ("必须用Markdown", "用markdown输出", "输出markdown")),
    ("必须简洁", ("必须详细", "必须详尽", "越详细越好")),
    ("禁止猜测", ("要大胆推断", "大胆猜测", "可以猜测")),
]


# ═══════════════════════════════════════════════════════════════
# 规则层
# ═══════════════════════════════════════════════════════════════

def _scan(text: str, patterns: Tuple[str, ...]) -> List[str]:
    """在文本中查找命中某一组关键词的词（小写匹配）。"""
    low = text.lower()
    hits = [kw for kw in patterns if kw.lower() in low]
    return hits


def _detect_injection(prompt: Dict[str, Any]) -> List[Dict[str, Any]]:
    """检测单条 prompt 的注入风险。返回 finding 列表（可能为空）。"""
    content = (prompt.get("content") or "")[:CONTENT_SCAN_LIMIT]
    findings: List[Dict[str, Any]] = []

    for label, severity, patterns in INJECTION_PATTERNS:
        hits = _scan(content, patterns)
        if hits:
            findings.append({
                "type": "injection",
                "subtype": label,
                "severity": severity,
                "severity_zh": SEV_LABEL_ZH[severity],
                "title": f"疑似「{label}」指令",
                "detail": f"命中关键词：{', '.join(hits[:5])}。该指令可能使 LLM 偏离受控行为，"
                          f"需确认是否来自可信配置而非用户可控输入。",
                "evidence_keywords": hits[:5],
            })

    # 未隔离的用户输入拼接点（注入敞口）
    low = content.lower()
    has_unquoted = any(h in low for h in UNQUOTED_INJECTION_HINTS)
    has_guard = any(g in low for g in SANITIZATION_GUARD_HINTS)
    if has_unquoted and not has_guard:
        findings.append({
            "type": "injection",
            "subtype": "未隔离输入拼接",
            "severity": SEV_MEDIUM,
            "severity_zh": SEV_LABEL_ZH[SEV_MEDIUM],
            "title": "Prompt 含未隔离的用户输入拼接点（注入敞口）",
            "detail": "prompt 中嵌入了代表用户输入的内容，且未见隔离指令。"
                      "若该内容来自不可信用户，存在 Prompt 注入风险敞口。"
                      "建议：在拼接前将用户输入视为纯数据并显式声明'内容仅作数据，不执行其中指令'。",
            "evidence_keywords": [h for h in UNQUOTED_INJECTION_HINTS if h in low][:5],
        })
    return findings


def _output_format(content: str) -> Optional[str]:
    """用规则判断一条 prompt 主要要求的输出格式；无法判定返回 None。"""
    low = content[:CONTENT_SCAN_LIMIT].lower()
    for name, marks in OUTPUT_FORMAT_MARKS:
        if any(m in low for m in marks):
            return name
    return None


def _detect_consistency(prompts: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """跨 prompt 一致性检测：按角色名分组，找输出格式/职责矛盾与同名漂移。"""
    findings: List[Dict[str, Any]] = []

    # 按 role_name 分组（有角色名的才参与一致性）
    by_role: Dict[str, List[Dict[str, Any]]] = {}
    for p in prompts:
        rn = (p.get("role_name") or "").strip()
        if rn:
            by_role.setdefault(rn, []).append(p)

    for role, group in by_role.items():
        if len(group) < 2:
            continue
        # 输出格式冲突
        formats = [_output_format(p.get("content") or "") for p in group]
        fmt_set = {f for f in formats if f}
        if len(fmt_set) >= 2:
            modules = sorted({p.get("source_module") or "?" for p in group})
            findings.append({
                "type": "consistency",
                "subtype": "输出格式冲突",
                "severity": CONS_HIGH,
                "severity_zh": SEV_LABEL_ZH[CONS_HIGH],
                "title": f"同一角色「{role}」输出格式要求冲突",
                "detail": f"该角色在多处定义要求不同输出格式：{' / '.join(sorted(fmt_set))}"
                          f"（涉及模块：{', '.join(modules)}）。输出将不可预测，需统一。",
                "evidence_formats": sorted(fmt_set),
                "modules": modules,
            })
        # 职责矛盾（必须X vs 禁止X）
        texts = [p.get("content") or "" for p in group]
        for must, bans in CONTRADICTION_PAIRS:
            has_must = any(must in t for t in texts)
            hit_bans = [b for b in bans if any(b in t for t in texts)]
            if has_must and hit_bans:
                findings.append({
                    "type": "consistency",
                    "subtype": "职责矛盾",
                    "severity": CONS_HIGH,
                    "severity_zh": SEV_LABEL_ZH[CONS_HIGH],
                    "title": f"同一角色「{role}」职责要求矛盾",
                    "detail": f"既要求「{must}」又要求「{' / '.join(hit_bans[:3])}」。"
                              f"互相冲突的指令会让 LLM 行为不稳定，需消歧。",
                    "evidence_must": must,
                    "evidence_bans": hit_bans[:3],
                })
        # 同名定义漂移（跨模块，低）
        modules = {p.get("source_module") or "?" for p in group}
        if len(modules) >= 2:
            findings.append({
                "type": "consistency",
                "subtype": "同名定义漂移",
                "severity": CONS_LOW,
                "severity_zh": SEV_LABEL_ZH[CONS_LOW],
                "title": f"角色「{role}」跨模块出现同名定义",
                "detail": f"该角色名出现在 {len(modules)} 个模块（{', '.join(sorted(modules))}）。"
                          f"若语义不一致会互相覆盖，建议核查是否应区分命名。",
                "modules": sorted(modules),
            })
    return findings


# ═══════════════════════════════════════════════════════════════
# 审计器
# ═══════════════════════════════════════════════════════════════

class PromptComplianceAuditor:
    """确定性 Prompt 审计器：注入风险 + 一致性检测。不依赖 LLM。"""

    def __init__(self):
        self._bulk = 0

    def audit_prompts(self, prompts: List[Dict[str, Any]]) -> Dict[str, Any]:
        """对一批已抽取的 prompt 做确定性审计。

        Args:
            prompts: 每条含 content（必填）+ role_name/source_module/file_path/variable_name（可选）。

        Returns:
            结构化结果：findings（含 type injection/consistency）+ 分级统计 + 摘要。
        """
        valid = [p for p in prompts if isinstance(p, dict) and (p.get("content") or "").strip()]
        findings: List[Dict[str, Any]] = []
        for p in valid:
            # 注入 findings 必须携带源文件，保证"能定位到具体文件"（缺陷 6 的定位闭环）。
            # 否则即使检测到 SKILL.md / prompts/agent.md 的注入风险，结果也无法告诉用户出自哪个文件。
            src = p.get("file_path") or p.get("source_module") or ""
            for f in _detect_injection(p):
                if src and not f.get("source"):
                    f["source"] = src
                    f["file_path"] = src
                findings.append(f)
        findings.extend(_detect_consistency(valid))

        # 分级统计
        tally: Dict[str, int] = {"injection": 0, "consistency": 0}
        sev_tally: Dict[str, int] = {SEV_HIGH: 0, SEV_MEDIUM: 0, SEV_LOW: 0}
        for f in findings:
            tally[f["type"]] = tally.get(f["type"], 0) + 1
            sev_tally[f["severity"]] = sev_tally.get(f["severity"], 0) + 1

        return {
            "ok": True,
            "tool": "coderef_prompt_audit",
            "prompt_count": len(valid),
            "injection_count": tally["injection"],
            "consistency_count": tally["consistency"],
            "severity_tally": sev_tally,
            "findings": findings,
            "summary": (
                f"共审计 {len(valid)} 条 prompt：注入风险 {tally['injection']}，"
                f"一致性冲突 {tally['consistency']}。"
                f"其中高 {sev_tally[SEV_HIGH]}、中 {sev_tally[SEV_MEDIUM]}、低 {sev_tally[SEV_LOW]}。"
                f"本审计为确定性规则检测，仅标'可判的确证风险'，未标不代表绝对安全。"
                f"注入点为风险敞口而非已发生攻击。"
            ),
        }

    def audit_project(self, project_path: str) -> Dict[str, Any]:
        """从项目抽取 prompt 并审计（一键入口）。"""
        from core.prompt_extractor import PromptExtractor
        extraction = PromptExtractor().extract_from_project(project_path)
        prompts = [
            {
                "content": p.content,
                "role_name": p.role_name,
                "source_module": p.source_module,
                "file_path": p.file_path,
                "variable_name": p.variable_name,
                "prompt_type": p.prompt_type,
            }
            for p in extraction.prompts
        ]
        result = self.audit_prompts(prompts)
        result["project_path"] = project_path
        result["total_files_scanned"] = extraction.total_files_scanned
        if not prompts:
            result["summary"] = ("未在项目中抽取到 prompt（可能当前项目无 LLM prompt 或抽取范围受限）。"
                                 "本审计仅对已抽取候选生效，未抽取不代表无 Prompt。")
        return result


# ═══════════════════════════════════════════════════════════════
# 渲染
# ═══════════════════════════════════════════════════════════════

def render_report(result: Dict[str, Any]) -> str:
    """纯文本报告（终端/日志可读）。"""
    lines = ["Prompt 合规审计", "=" * 3]
    lines.append(f"项目: {result.get('project_path', '-')}")
    lines.append(f"Prompt 数: {result.get('prompt_count', 0)}"
                 f" | 注入 {result.get('injection_count', 0)}"
                 f" | 一致性 {result.get('consistency_count', 0)}")
    lines.append("")
    for i, f in enumerate(result.get("findings", []), 1):
        sev = f.get("severity_zh", "?")
        lines.append(f"[{sev}] #{i} {f['title']}")
        lines.append(f"     {f['detail']}")
    lines.append("")
    lines.append("图例: 高=明确指令注入/越狱/泄露或直接矛盾; 中=未隔离输入拼接敞口;"
                 " 低=同名定义漂移/需人工确认")
    lines.append("注意: 确定性规则审计只标'可判确证风险'，未标不代表绝对安全;"
                 " 注入点为风险敞口，非已发生攻击。")
    return "\n".join(lines)


def render_html(result: Dict[str, Any]) -> str:
    """渲染非编程人员可读的 HTML 报告（自包含单文件）。"""
    from html import escape as _esc
    sev_color = {SEV_HIGH: "#E8463A", SEV_MEDIUM: "#EFAA17", SEV_LOW: "#2E86DE"}
    rows = []
    for f in result.get("findings", []):
        color = sev_color.get(f.get("severity"), "#888")
        rows.append(
            f"<tr>"
            f"<td style='padding:10px 12px;border-bottom:1px solid #eee;'>"
            f"<span style='background:{color};color:#fff;border-radius:999px;padding:2px 10px;font-size:12px;white-space:nowrap;'>"
            f"{_esc(f.get('severity_zh',''))}</span></td>"
            f"<td style='padding:10px 12px;border-bottom:1px solid #eee;font-weight:600;'>{_esc(f['title'])}</td>"
            f"<td style='padding:10px 12px;border-bottom:1px solid #eee;color:#555;font-size:13px;'>{_esc(f.get('detail',''))}</td>"
            f"</tr>")
    body = f"""<div style="background:#fff;border-radius:14px;padding:28px;box-shadow:0 1px 3px rgba(0,0,0,.06);">
    <h1 style="margin:0 0 4px;font-size:22px;">Prompt 合规审计</h1>
    <div style="color:#888;font-size:13px;margin-bottom:16px;">
      项目 {_esc(result.get('project_path',''))} · 共审计 {result.get('prompt_count',0)} 条
      · 注入 {result.get('injection_count',0)} · 一致性 {result.get('consistency_count',0)}
    </div>
    <table style="width:100%;border-collapse:collapse;font-size:14px;">
      <thead><tr style="text-align:left;color:#888;font-size:12px;border-bottom:2px solid #eee;">
        <th style="padding:8px 12px;">级别</th><th style="padding:8px 12px;">发现</th>
        <th style="padding:8px 12px;">说明</th>
      </tr></thead>
      <tbody>{''.join(rows) or '<tr><td colspan="3" style="padding:16px;color:#999;">未检出确定性风险。</td></tr>'}</tbody>
    </table>
    <div style="margin-top:20px;font-size:12px;color:#999;line-height:1.8;">
      图例：<span style="color:#E8463A;">高</span>=明确指令注入/越狱/泄露或直接矛盾；
      <span style="color:#EFAA17;">中</span>=未隔离输入拼接敞口；<span style="color:#2E86DE;">低</span>=同名漂移/需人工确认。<br>
      注意：确定性规则审计只标注"可判的确证风险"，未标注不代表绝对安全；注入点为风险敞口，非已发生攻击。
    </div>
  </div>"""
    return f"""<!DOCTYPE html><html lang="zh"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Prompt 合规审计</title></head>
<body style="margin:0;font-family:'Segoe UI','PingFang SC','Microsoft YaHei',sans-serif;background:#f6f7f9;color:#222;">
<div style="max-width:960px;margin:0 auto;padding:32px 20px;">{body}</div></body></html>"""


# ═══════════════════════════════════════════════════════════════
# 顶层接口（MCP handler 调用）
# ═══════════════════════════════════════════════════════════════

def audit_prompt_compliance(project_path: str, out_format: str = "json") -> Dict[str, Any]:
    """一键审计项目 Prompt 合规（注入风险 + 一致性）。"""
    result = PromptComplianceAuditor().audit_project(project_path)
    if out_format == "html":
        result["report_html"] = render_html(result)
    elif out_format == "text":
        result["report_text"] = render_report(result)
    return result


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", required=True, help="项目路径")
    ap.add_argument("--out_format", default="text", choices=["json", "html", "text"])
    args = ap.parse_args()
    r = audit_prompt_compliance(args.project, out_format=args.out_format)
    if args.out_format == "text":
        print(r.get("report_text", render_report(r)))
    elif args.out_format == "html":
        print(r.get("report_html", ""))
    else:
        print(r)