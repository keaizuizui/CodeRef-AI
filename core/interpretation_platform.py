# -*- coding: utf-8 -*-
"""
interpretation_platform — 人话解读平台（4.5 平台整合：健康仪表盘 × Wiki × 诚实话解读闭环）

目标读者：非编程人员（AI 时代的项目治理者）与编程 AI（调用方）。
核心问题：审计/图谱/架构/合规产出的是"工程师话术"，一个完全不懂编程的人
怎么知道"这个 AI 写的项目到底靠不靠谱、我该不该信"？
本平台把它们编排成"人话"——用确定性事实回答，不靠 LLM 猜。

诚实话解读闭环（本平台的立身之本，与 verify_findings / prompt_governance 同源）：
- 确定性 > 猜测：所有"人话结论"只来自确定性原语（健康分、审计 findings、图谱、
  Prompt 合规、论断核验），不引入 LLM 给结论。
- 分层硬阻断：依赖 LLM 才能产出的人话（Wiki 故事化、业务深度解读）在无 API Key
  时明确阻断并提示，绝不产出"占位/降级"内容伪装成"已解读"。
- 未审计 ≠ 无风险：健康解读只在"确实审计过"时才给健康分；从未审计时明确提示
  "尚未运行审计"，只给图谱/合规等已有确定性背景，不臆断项目健康。
- 数字转人话是"翻译"不是"美化"：健康分低就直说"风险偏高"，不粉饰。

集成方式：作为 MCP 工具 coderef_interpret 暴露。
"""

import os
import json
from typing import Dict, List, Optional, Any

from loguru import logger


# ═══════════════════════════════════════════════════════════════
# 模块级常量（magic number 收敛）
# ═══════════════════════════════════════════════════════════════

# 平台支持的 action 集合
INTERPRET_ACTIONS = ("health", "dashboard", "verify", "verify_html",
                     "wiki", "prompt", "assets")

# 健康分段的"人话"护栏（宁可保守，不夸大）
_SCORE_BANDS = (
    (90, "项目状态健康，风险很低。"),
    (80, "项目基本健康，但存在少量需要关注的问题。"),
    (60, "项目存在中等偏高的风险，建议优先处理高危项。"),
    (0, "项目风险偏高，强烈建议先处理高危项再继续推进。"),
)

# 健康分各档颜色（用于 HTML 仪表盘）
_SCORE_COLOR = (
    (90, "#1DC981"),
    (80, "#7BC96F"),
    (60, "#EFAA17"),
    (0, "#E8463A"),
)

# 严重度 display 名
_TIER_LABEL = {"high": "高危", "medium": "中危", "low": "低危"}

# 审计 findings 落盘文件名（与 pipeline_runner 保持一致）
AUDIT_FINDINGS_FILE = "audit_findings.json"


def _humanize_count(n: int) -> str:
    """把数字转成易读单位（千/万）。"""
    try:
        n = int(n or 0)
    except (TypeError, ValueError):
        return "0"
    if n >= 10000:
        return f"{n/10000:.1f}万"
    if n >= 1000:
        return f"{n/1000:.1f}千"
    return str(n)


def humanize_score(score: int) -> str:
    """健康分 → 人话描述（确定性翻译，不美化）。"""
    try:
        score = int(score or 0)
    except (TypeError, ValueError):
        score = 0
    for band_score, label in _SCORE_BANDS:
        if score >= band_score:
            return label
    return _SCORE_BANDS[-1][1]


def score_color(score: int) -> str:
    """健康分 → 颜色。"""
    try:
        score = int(score or 0)
    except (TypeError, ValueError):
        score = 0
    for band_score, color in _SCORE_COLOR:
        if score >= band_score:
            return color
    return _SCORE_COLOR[-1][1]


def _compute_score(findings: List[Dict]) -> int:
    """从 findings dict 计算健康分（与 health_dashboard 一致：100 - 高5/中1/低0.2）。"""
    if not findings:
        return 100
    score = 100.0
    for f in findings:
        tier = str(f.get("tier") or f.get("severity") or "low").lower()
        if tier in ("high", "critical"):
            score -= 5
        elif tier == "medium":
            score -= 1
        elif tier == "low":
            score -= 0.2
    return max(0, int(score))


def _load_audit_findings(project_path: str) -> Optional[Dict]:
    """读取已落盘的审计 findings（确定性；无则返回 None，由调用方诚实提示）。"""
    candidates = [
        os.path.join(project_path, "coderef-report", AUDIT_FINDINGS_FILE),
        os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                     "coderef-report", AUDIT_FINDINGS_FILE),
    ]
    for fp in candidates:
        if not os.path.isfile(fp):
            continue
        try:
            with open(fp, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as exc:
            logger.warning(f"[interpret] 读取审计 findings 失败 {fp}: {exc}")
    return None


def _escape_html(text: str) -> str:
    from html import escape as _esc
    return _esc(str(text or ""))


class InterpretationPlatform:
    """人话解读平台 —— 编排健康仪表盘 × Wiki × 诚实话解读闭环"""

    def __init__(self):
        pass

    # ─── 主入口 ────────────────────────────────────────────────

    def interpret(self, project_path: str, action: str = "health",
                  findings_text: str = "", entry: str = "",
                  out_format: str = "json") -> Dict[str, Any]:
        """统一解读入口，分派到具体子能力。

        Args:
            project_path: 项目路径
            action: health | dashboard | verify | verify_html | wiki | prompt | assets
            findings_text: (verify/verify_html) 论断文本，多行或多条 JSON
            entry: (verify) 可选入口符号
            out_format: json | text | html

        Returns:
            结构化 dict（各 action 字段不同，均含 ok/action/summary）
        """
        if action not in INTERPRET_ACTIONS:
            return {
                "ok": False,
                "action": action,
                "error": f"未知 action: {action}（支持 {'/'.join(INTERPRET_ACTIONS)}）",
            }
        if action == "health":
            return self._action_health(project_path)
        if action == "dashboard":
            return self._action_dashboard(project_path)
        if action == "verify":
            return self._action_verify(project_path, findings_text, entry, out_format)
        if action == "verify_html":
            return self._action_verify_html(project_path, findings_text, entry)
        if action == "wiki":
            return self._action_wiki(project_path)
        if action == "prompt":
            return self._action_prompt(project_path)
        if action == "assets":
            return self._action_assets(project_path)
        return {"ok": False, "action": action, "error": "内部错误"}

    # ─── health：健康总览（确定性人话） ────────────────────────

    def _action_health(self, project_path: str) -> Dict[str, Any]:
        """健康总览：只在"确实审计过"时给健康分；否则只给确定性背景。"""
        project_path = os.path.abspath(project_path)

        # 1. 尝试复用已落盘的审计 findings
        data = _load_audit_findings(project_path)
        findings = (data or {}).get("findings", [])

        # 2. 图谱背景（确定性旁证）
        kg_stats = self._kg_stats(project_path)

        # 3. Prompt 合规背景（确定性）
        prompt = self._prompt_summary(project_path)

        if data is None:
            # 诚实：从未审计 → 不给健康分，只给背景
            summary = (
                f"尚未运行 coderef_audit，无法给出健康分（未审计 ≠ 无风险）。"
                f"当前已知确定性背景：知识图谱 {'已构建' if kg_stats else '未构建'}，"
                f"Prompt 合规审计 {prompt.get('prompt_count', 0)} 条。"
                f"建议先运行 coderef_audit 获得完整健康读数，再用人话解读判断。"
            )
            return {
                "ok": True,
                "action": "health",
                "tool": "coderef_interpret",
                "project_path": project_path,
                "audit_ran": False,
                "score": None,
                "score_label": "未审计",
                "score_color": "#999",
                "findings_count": 0,
                "kg_stats": kg_stats,
                "prompt": prompt,
                "summary": summary,
            }

        # 4. 已审计 → 计算健康分并解读
        score = _compute_score(findings)
        high = [f for f in findings if str(f.get("tier") or f.get("severity") or "").lower() in ("high", "critical")]
        medium = [f for f in findings if str(f.get("tier") or f.get("severity") or "").lower() == "medium"]
        low = [f for f in findings if str(f.get("tier") or f.get("severity") or "").lower() == "low"]

        # 归纳高危项标题（前 5 条），让人话有依据
        top_risks = [_TITLE_SHORT(f) for f in high[:5]]

        summary = (
            f"健康分 {score}/100（{humanize_score(score)}）"
            f"共 {len(findings)} 条审计发现：高危 {len(high)}、中危 {len(medium)}、低危 {len(low)}。"
            + (f" 高危清单：{'、'.join(top_risks)}。" if top_risks else "")
            + f" 知识图谱 {'已构建' if kg_stats else '未构建'}。"
            + " 注意：健康分来自确定性审计，仅反映已检出问题，不代表代码无错。"
        )

        return {
            "ok": True,
            "action": "health",
            "tool": "coderef_interpret",
            "project_path": project_path,
            "audit_ran": True,
            "score": score,
            "score_label": humanize_score(score),
            "score_color": score_color(score),
            "findings_count": len(findings),
            "tally": {"high": len(high), "medium": len(medium), "low": len(low)},
            "top_risks": top_risks,
            "kg_stats": kg_stats,
            "prompt": prompt,
            "summary": summary,
        }

    # ─── dashboard：健康仪表盘 HTML ────────────────────────────

    def _action_dashboard(self, project_path: str) -> Dict[str, Any]:
        """生成健康仪表盘 HTML（复用已有审计 findings；无则诚实提示）。"""
        project_path = os.path.abspath(project_path)
        data = _load_audit_findings(project_path)
        if data is None:
            return {
                "ok": False,
                "action": "dashboard",
                "tool": "coderef_interpret",
                "project_path": project_path,
                "error": "尚无审计结果。请先运行 coderef_audit 生成 findings，再生成仪表盘。",
                "summary": "仪表盘依赖审计结果；未审计时无法渲染健康读数。",
            }
        findings = data.get("findings", [])
        score = _compute_score(findings)
        kg_stats = self._kg_stats(project_path)

        html = self._render_dashboard_html(
            project_path=project_path,
            score=score,
            findings=findings,
            kg_stats=kg_stats,
            data=data,
        )

        # 落盘到 coderef-report
        out_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                               "coderef-report")
        os.makedirs(out_dir, exist_ok=True)
        from datetime import datetime
        fp = os.path.join(out_dir, f"interpret_dashboard_{datetime.now().strftime('%Y%m%d_%H%M%S')}.html")
        with open(fp, "w", encoding="utf-8") as f:
            f.write(html)

        return {
            "ok": True,
            "action": "dashboard",
            "tool": "coderef_interpret",
            "project_path": project_path,
            "score": score,
            "score_label": humanize_score(score),
            "report_file": fp,
            "summary": f"健康仪表盘已生成：{fp}（健康分 {score}/100）。",
        }

    # ─── verify / verify_html：论断人话核验 ────────────────────

    def _action_verify(self, project_path: str, findings_text: str,
                       entry: str, out_format: str) -> Dict[str, Any]:
        """把 LLM/CodeRabbit 论断用确定性逻辑核验，并人话解读。"""
        from core.verify_findings import verify_findings, render_report
        findings = self._parse_findings_text(findings_text)
        if not findings:
            return {"ok": False, "action": "verify", "tool": "coderef_interpret",
                    "error": "未提供可核验的论断。findings_text 应为多行（每行一条 title）或 JSON 数组。",
                    "summary": "没有论断可核验。"}
        r = verify_findings(project_path, findings, entry=entry)
        r["tool"] = "coderef_interpret"
        r["action_alias"] = "verify"
        r["project_path"] = os.path.abspath(project_path)
        if out_format == "text":
            r["report_text"] = render_report(r)
        return r

    def _action_verify_html(self, project_path: str, findings_text: str,
                            entry: str) -> Dict[str, Any]:
        """论断核验 + 生成非编程人员可读的 HTML 报告。"""
        from core.verify_findings import verify_findings, render_html
        findings = self._parse_findings_text(findings_text)
        if not findings:
            return {"ok": False, "action": "verify_html", "tool": "coderef_interpret",
                    "error": "未提供可核验的论断。", "summary": "没有论断可核验。"}
        r = verify_findings(project_path, findings, entry=entry)
        r["report_html"] = render_html(r)
        r["tool"] = "coderef_interpret"
        r["action_alias"] = "verify_html"
        r["project_path"] = os.path.abspath(project_path)
        return r

    # ─── wiki：Wiki 生成（LLM 硬阻断） ─────────────────────────

    def _action_wiki(self, project_path: str) -> Dict[str, Any]:
        """Wiki 生成。依赖 LLM → 无 API Key 时由 WikiGenerator 硬阻断。"""
        try:
            from core.wiki_generator import WikiGenerator
            wg = WikiGenerator()
            if not wg.llm.is_available():
                return {
                    "ok": False,
                    "action": "wiki",
                    "tool": "coderef_interpret",
                    "project_path": os.path.abspath(project_path),
                    "error": "Wiki 人话文档需要 LLM，但当前未配置有效 API Key。"
                             "请配置 API Key 后再运行。审计/图谱/Prompt 合规等确定性解读不受影响。",
                    "summary": "Wiki 需要 LLM；无 API Key 时不产出占位内容（诚实阻断）。",
                }
        except Exception as exc:
            # LLM 依赖层不可用（如 openai 未安装）本质上等同于"LLM 不可用"，
            # 同样诚实阻断，不把"无法生成"伪装成"已生成"。
            logger.warning(f"[interpret] Wiki LLM 依赖不可用，诚实阻断: {exc}")
            return {
                "ok": False,
                "action": "wiki",
                "tool": "coderef_interpret",
                "project_path": os.path.abspath(project_path),
                "error": "Wiki 人话文档需要 LLM，但 LLM 依赖当前不可用"
                         f"（{exc}）。请配置并在环境安装 LLM 依赖后再运行。"
                         "审计/图谱/Prompt 合规等确定性解读不受影响。",
                "summary": "Wiki 需要 LLM；LLM 依赖不可用时诚实阻断，不产出占位内容。",
            }
        result = wg.generate(project_path)
        return {
            "ok": not result.errors,
            "action": "wiki",
            "tool": "coderef_interpret",
            "project_path": os.path.abspath(project_path),
            "output_dir": result.output_dir,
            "module_count": result.module_count,
            "total_files": result.total_files,
            "documents": result.documents,
            "errors": result.errors,
            "summary": (f"Wiki 已生成到 {result.output_dir}（{len(result.documents)} 篇文档）。"
                        + (f" 有 {len(result.errors)} 项注意：{'；'.join(result.errors[:5])}" if result.errors else "")),
        }

    # ─── prompt：Prompt 治理（确定性） ─────────────────────────

    def _action_prompt(self, project_path: str) -> Dict[str, Any]:
        """Prompt 治理总览（委托 prompt_governance，确定性、人话）。"""
        from core.prompt_governance import PromptGovernance
        r = PromptGovernance().govern(project_path, action="overview")
        r["tool"] = "coderef_interpret"
        r["action_alias"] = "prompt"
        r["project_path"] = os.path.abspath(project_path)
        return r

    # ─── assets：创新建设资产（确定性） ────────────────────────

    def _action_assets(self, project_path: str) -> Dict[str, Any]:
        """人话解读已固化资产（复用 DesignRegistry / 创新传播检测）。"""
        from core.design_registry import DesignRegistry
        reg = DesignRegistry()
        assets = reg.list_assets()
        if not assets:
            return {
                "ok": True,
                "action": "assets",
                "tool": "coderef_interpret",
                "project_path": os.path.abspath(project_path),
                "asset_count": 0,
                "assets": [],
                "summary": "暂无已固化的创新资产。可先运行 coderef_innovation 发现设计，"
                           "再用 coderef_asset 固化。",
            }
        # 人话化：只提炼资产的关键信息，不倾倒全字段
        brief = []
        for a in assets:
            brief.append({
                "canonical": a.get("canonical"),
                "category": a.get("category"),
                "description": a.get("description"),
                "adoption_count": a.get("adoption_count", 0),
                "adopters": a.get("adopters", [])[:5],
                "blueprint_complete": bool(a.get("blueprint") and a["blueprint"].get("entry_points")),
            })
        return {
            "ok": True,
            "action": "assets",
            "tool": "coderef_interpret",
            "project_path": os.path.abspath(project_path),
            "asset_count": len(brief),
            "assets": brief,
            "summary": f"已固化 {len(brief)} 个创新资产"
                       f"（复刻蓝图完整 {sum(1 for b in brief if b['blueprint_complete'])} 个）。"
                       f"这些是被验证/传播的设计，可被 coderef_replicate 复刻到其他项目。",
        }

    # ─── 内部工具 ──────────────────────────────────────────────

    def _kg_stats(self, project_path: str) -> Optional[Dict]:
        """确定性图谱统计（失败返回 None，由调用方诚实标注）。"""
        try:
            from core.code_knowledge_graph import CodeKnowledgeGraph
            kg = CodeKnowledgeGraph(project_path)
            if not os.path.isfile(kg.db_path):
                return None
            stats = kg.get_stats()
            return {
                "node_count": stats.get("node_count", 0),
                "edge_count": stats.get("edge_count", 0),
                "built_at": stats.get("built_at", ""),
            }
        except Exception:
            return None

    def _prompt_summary(self, project_path: str) -> Dict:
        """确定性 Prompt 合规汇总（失败返回空计数，由调用方诚实标注）。"""
        try:
            from core.prompt_compliance import PromptComplianceAuditor
            r = PromptComplianceAuditor().audit_project(project_path)
            return {
                "prompt_count": r.get("prompt_count", 0),
                "injection_count": r.get("injection_count", 0),
                "consistency_count": r.get("consistency_count", 0),
            }
        except Exception:
            return {"prompt_count": 0, "injection_count": 0, "consistency_count": 0}

    def _parse_findings_text(self, text: str) -> List[Dict]:
        """解析论断文本：JSON 数组 或 多行（每行一条 title）。"""
        if not text:
            return []
        text = text.strip()
        # JSON 数组
        if text.startswith("["):
            try:
                data = json.loads(text)
                if isinstance(data, list):
                    return [d for d in data if isinstance(d, dict) and d.get("title")]
                return []
            except Exception:
                pass
        # 多行 → 每条 title
        return [{"title": line.strip()} for line in text.splitlines() if line.strip()]

    # ─── HTML 仪表盘渲染 ───────────────────────────────────────

    def _render_dashboard_html(self, project_path: str, score: int,
                               findings: List[Dict], kg_stats: Optional[Dict],
                               data: Dict) -> str:
        """渲染非编程人员可读的健康仪表盘 HTML（自包含单文件）。"""
        project_name = os.path.basename(project_path.rstrip(os.sep))
        high = [f for f in findings if str(f.get("tier") or f.get("severity") or "").lower() in ("high", "critical")]
        medium = [f for f in findings if str(f.get("tier") or f.get("severity") or "").lower() == "medium"]
        low = [f for f in findings if str(f.get("tier") or f.get("severity") or "").lower() == "low"]

        color = score_color(score)
        label = humanize_score(score)

        # 高危清单
        risk_rows = ""
        if high:
            for f in high[:8]:
                risk_rows += (
                    f"<li style='padding:8px 0;border-bottom:1px solid #f0f0f0;'>"
                    f"<span style='color:{color};font-weight:600;'>紧急</span> {_escape_html(f.get('title'))}"
                    f"<div style='color:#888;font-size:12px;margin-top:2px;'>"
                    f"{_escape_html(f.get('file_path') or '')}"
                    f"{(':' + str(f.get('line'))) if f.get('line') is not None else ''}</div></li>")
        else:
            risk_rows = "<li style='color:#1DC981;'>暂未发现高危项。</li>"

        kg_line = ""
        if kg_stats:
            kg_line = (f"知识图谱已构建：{_humanize_count(kg_stats.get('node_count', 0))} 个节点 · "
                       f"{_humanize_count(kg_stats.get('edge_count', 0))} 条边"
                       + (f"（构建于 {_escape_html(kg_stats.get('built_at', ''))}）" if kg_stats.get("built_at") else ""))
        else:
            kg_line = "知识图谱未构建（人话解读无法引用图谱佐证）。"

        return f"""<!DOCTYPE html><html lang="zh"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>健康仪表盘 · {project_name}</title></head>
<body style="margin:0;font-family:'Segoe UI','PingFang SC','Microsoft YaHei',sans-serif;background:#f6f7f9;color:#222;">
<div style="max-width:920px;margin:0 auto;padding:32px 20px;">

  <div style="background:#fff;border-radius:16px;padding:28px;box-shadow:0 1px 3px rgba(0,0,0,.06);">
    <h1 style="margin:0 0 4px;font-size:24px;">项目健康仪表盘</h1>
    <div style="color:#888;font-size:13px;margin-bottom:20px;">{project_name} · 人话解读（非编程人员可读）</div>

    <div style="display:flex;align-items:center;gap:24px;flex-wrap:wrap;">
      <div style="width:120px;height:120px;border-radius:50%;background:conic-gradient({color} 0% {score}%, #eee {score}% 100%);display:flex;align-items:center;justify-content:center;">
        <div style="background:#fff;width:92px;height:92px;border-radius:50%;display:flex;flex-direction:column;align-items:center;justify-content:center;">
          <span style="font-size:30px;font-weight:700;color:{color};">{score}</span>
          <span style="font-size:11px;color:#888;">/ 100</span>
        </div>
      </div>
      <div style="flex:1;min-width:220px;">
        <div style="font-size:18px;font-weight:600;color:{color};margin-bottom:6px;">{_escape_html(label)}</div>
        <div style="color:#666;font-size:14px;line-height:1.8;">
          共 {len(findings)} 条审计发现：高危 {len(high)} · 中危 {len(medium)} · 低危 {len(low)}<br>
          {kg_line}
        </div>
      </div>
    </div>
  </div>

  <div style="background:#fff;border-radius:16px;padding:24px;margin-top:20px;box-shadow:0 1px 3px rgba(0,0,0,.06);">
    <h2 style="margin:0 0 12px;font-size:18px;">最需要优先处理的高危项</h2>
    <ul style="margin:0;padding:0;list-style:none;">{risk_rows}</ul>
  </div>

  <div style="margin-top:20px;font-size:12px;color:#999;line-height:1.8;">
    诚实说明：健康分来自确定性审计，仅反映"已检出的问题"，不代表代码绝对无错。
    未审计的问题不会计入本分；请结合 coderef_verify_findings 对关键论断做确定性核验后再采信。
  </div>
</div></body></html>"""


# ═══════════════════════════════════════════════════════════════
# 顶层接口（MCP handler 调用）
# ═══════════════════════════════════════════════════════════════

def _TITLE_SHORT(f: Dict) -> str:
    """提取 finding 标题用于人话清单（截断过长）。"""
    t = str(f.get("title") or f.get("detail") or "未知问题")
    return t if len(t) <= 40 else t[:40] + "…"


def interpret_project(project_path: str, action: str = "health",
                      findings_text: str = "", entry: str = "",
                      out_format: str = "json") -> Dict[str, Any]:
    """一键人话解读：健康 / 仪表盘 / 论断核验 / Wiki / Prompt 治理 / 资产。"""
    return InterpretationPlatform().interpret(
        project_path, action=action, findings_text=findings_text,
        entry=entry, out_format=out_format,
    )


if __name__ == "__main__":
    import argparse
    import pprint
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", required=True, help="项目路径")
    ap.add_argument("--action", default="health", choices=list(INTERPRET_ACTIONS))
    ap.add_argument("--findings", default="", help="论断文本（verify 用）")
    ap.add_argument("--entry", default="", help="入口符号（verify 用）")
    args = ap.parse_args()
    pprint.pprint(interpret_project(args.project, action=args.action,
                                    findings_text=args.findings, entry=args.entry),
                  width=130, sort_dicts=False)