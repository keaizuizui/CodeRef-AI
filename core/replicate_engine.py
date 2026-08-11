# -*- coding: utf-8 -*-
"""
replicate_engine — 复刻铺排引擎（4.4 创新建设翼：从资产蓝图到可复刻指引）

目标读者：编程 AI（执行复刻）与 AI 时代治理者（看懂铺排逻辑）。
核心问题：一个已固化的 WorkflowAsset（含结构化蓝图）怎么落到另一个项目里？
本工具检测目标项目对该设计的采用缺口，并结合蓝图 / 已验证采用清单 / 确定性
核验结论，产出可执行的复刻铺排指引（steps + entry_points + verified_findings）。

诚实话纪律（与 verify_findings / prompt_compliance 同源）：
- 缺口判定是确定性的：基于"目标项目是否已采用该设计能力"的能力签名比对，
  不臆断"该不该采用"；只报告"哪些模块有、哪些没有"。
- 复刻指引是"铺排建议"而非"自动改代码"：本工具是审计工具，不直接写代码，
  由对方 AI 依据 steps 与 template_code 自行落地；缺失的 template_code 明确标注。
- verified_findings 复用 verify_findings 的确定性结论：只核验引用目标是否真实存在。
- entry_points 只从可信来源提取（蓝图已填 / 已验证采用模块的真实入口），不编造。

集成方式：作为 MCP 工具 coderef_replicate 暴露。
"""

import os
from typing import Dict, List, Optional, Any

from loguru import logger

from core.design_registry import DesignRegistry
from core.innovation_propagation_detector import InnovationPropagationDetector
from core.innovation_engine import DESIGN_CAPABILITY_TAG, INTENT_DESIGN, INTENT_ORDER


# ═══════════════════════════════════════════════════════════════════
# 模块级常量（集中管理 magic number）
# ═══════════════════════════════════════════════════════════════════

# 蓝图字段：完整可复刻蓝图必备的字段集合（缺任一即视为"待补全"）
BLUEPRINT_REQUIRED_FIELDS = ("steps", "entry_points", "verified_findings")

# 复刻步骤的默认顺序（依此组织 steps 数组）
STEP_ORDER = ("intent", "entry_points", "template_code", "patch_suggestion", "migration_guide")

# 目标项目"已采用"阈值：命中该设计能力签名的模块才算采用者
# （与 detector 的标签判定一致，无需额外阈值）

# 入口提取时，单模块最多取前 N 个候选入口
MAX_ENTRY_CANDIDATES_PER_MODULE = 5


class ReplicateEngine:
    """复刻铺排引擎 —— 结合蓝图与目标项目，产出可复刻指引。"""

    def __init__(self):
        self.registry = DesignRegistry()
        self.detector = InnovationPropagationDetector()

    # ─── 内部工具 ────────────────────────────────────────────────

    def _resolve_asset(self, canonical: str) -> Optional[Dict[str, Any]]:
        """解析 canonical / 别名 → 已固化资产。"""
        resolved = self.registry.resolve(canonical)
        asset = self.registry.get_asset(resolved)
        return asset

    def _collect_target_signatures(self, project_path: str):
        """收集目标项目的能力签名（复用 detector 底层）。"""
        self.detector.prepare_analysis()
        return self.detector.collect_signatures(project_path)

    def _detect_adoption_gaps(
        self, project_path: str, canonical: str, signatures
    ) -> Dict[str, Any]:
        """确定性缺口判定：目标项目中哪些模块已/未采用该设计。

        判定依据：canonical 对应的能力标签（DESIGN_CAPABILITY_TAG）。命中该标签的
        模块视为已采用；未命中但同属相关意图的模块视为潜在待铺排对象（标注为
        "未采用"，不臆断"应采用"）。
        """
        tag = DESIGN_CAPABILITY_TAG.get(canonical)
        adopted = []
        not_adopted = []
        for sig in signatures:
            has = bool(tag and tag in sig.tags)
            entry = {
                "module": sig.module_name,
                "file": sig.file_path,
                "adopted": has,
            }
            if has:
                adopted.append(entry)
            else:
                not_adopted.append(entry)

        # 意图归属（用于"潜在待铺排对象"的筛选说明）
        intent = next((k for k, v in INTENT_DESIGN.items() if v == canonical), "")

        return {
            "canonical": canonical,
            "capability_tag": tag,
            "intent": intent,
            "total_modules": len(signatures),
            "adopted_count": len(adopted),
            "adopted": adopted,
            "untouched_count": len(not_adopted),
            "untouched": not_adopted,
            "adoption_rate": round(len(adopted) / len(signatures), 4) if signatures else 0.0,
        }

    # ─── 入口提取（只从可信来源） ────────────────────────────────

    def _extract_entry_points(self, asset: Dict[str, Any], project_path: str) -> List[str]:
        """从蓝图已填 entry_points 或已验证采用模块的真实入口提取入口。

        优先用蓝图自带的 entry_points；缺失时，从已验证采用模块（adopter 的源码）
        提取函数/类名作为候选入口。只返回真实存在的符号，不编造。
        """
        blue = asset.get("blueprint") or {}
        existing = [e for e in blue.get("entry_points", []) if str(e).strip()]
        if existing:
            return existing[:MAX_ENTRY_CANDIDATES_PER_MODULE]

        # 从已验证采用模块提取真实入口
        adopters = asset.get("adopters") or []
        entries: List[str] = []
        seen: set = set()
        for mod in adopters:
            if len(entries) >= MAX_ENTRY_CANDIDATES_PER_MODULE:
                break
            # 在项目内定位该模块源码
            module_path = self._locate_module(project_path, mod)
            if not module_path:
                continue
            for name in self._top_level_symbols(module_path):
                if len(entries) >= MAX_ENTRY_CANDIDATES_PER_MODULE:
                    break
                if name not in seen:
                    seen.add(name)
                    entries.append(name)
        return entries

    def _locate_module(self, project_path: str, module_name: str) -> Optional[str]:
        """在项目内定位模块源码文件（module_name → .py 文件）。"""
        if not module_name:
            return None
        # 直接路径
        direct = os.path.join(project_path, f"{module_name}.py")
        if os.path.isfile(direct):
            return direct
        # 模块名可能是 points 分隔（如 core.foo）
        parts = module_name.split(".")
        for i in range(len(parts), 0, -1):
            rel = os.path.join(project_path, *parts[:i]) + ".py"
            if os.path.isfile(rel):
                return rel
        # 递归查找同名文件
        for root, _dirs, files in os.walk(project_path):
            if f"{parts[-1]}.py" in files:
                return os.path.join(root, f"{parts[-1]}.py")
        return None

    def _top_level_symbols(self, file_path: str) -> List[str]:
        """提取文件顶层函数 / 类名（作为候选入口）。纯 AST，不执行。"""
        try:
            import ast
            with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                tree = ast.parse(f.read())
        except Exception:
            return []
        names = []
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                names.append(node.name)
        return names

    # ─── 生成复刻步骤 ────────────────────────────────────────────

    def _build_steps(self, asset: Dict[str, Any], gap_report: Dict[str, Any]) -> List[Dict[str, Any]]:
        """依据 asset 内容与缺口报告，组织可执行的复刻步骤。

        每步含：step（序号标题）、action（动作）、detail（具体内容或缺失标注）。
        确定性可填的填内容；缺失的明确标注"待补全"，不编造。
        """
        steps: List[Dict[str, Any]] = []
        intent = gap_report.get("intent") or asset.get("intent") or ""
        if intent:
            steps.append({
                "step": "1. 明确意图",
                "action": "确认设计归属的意图",
                "detail": f"该设计属于「{intent}」意图（{gap_report.get('canonical')}）。",
            })

        entry_points = asset.get("blueprint", {}).get("entry_points") or []
        if entry_points:
            steps.append({
                "step": "2. 定位入口",
                "action": "在目标项目确定接入点",
                "detail": f"建议入口：{', '.join(entry_points)}（来自蓝图/已验证采用模块）。",
            })
        else:
            steps.append({
                "step": "2. 定位入口",
                "action": "在目标项目确定接入点",
                "detail": "蓝图未提供入口，需依据目标项目现有调用结构自行确定。",
            })

        template_code = asset.get("template_code") or ""
        if template_code:
            steps.append({
                "step": "3. 落地骨架",
                "action": "复制模板代码并适配",
                "detail": "使用资产自带的 template_code 作为起点，按目标项目命名/结构适配。",
                "has_template": True,
            })
        else:
            steps.append({
                "step": "3. 落地骨架",
                "action": "编写模板代码",
                "detail": "资产未提供 template_code（待补全），需依据 description 自行编写。",
                "has_template": False,
            })

        patch_suggestion = asset.get("patch_suggestion") or ""
        if patch_suggestion:
            steps.append({
                "step": "4. 迁移补丁",
                "action": "应用补丁建议",
                "detail": patch_suggestion,
            })

        migration_guide = asset.get("migration_guide") or ""
        if migration_guide:
            steps.append({
                "step": "5. 迁移指南",
                "action": "阅读迁移指南",
                "detail": migration_guide,
            })

        # 缺口提示
        untouched = gap_report.get("untouched_count", 0)
        steps.append({
            "step": "6. 缺口确认",
            "action": "对照目标项目采用缺口",
            "detail": (
                f"目标项目共 {gap_report.get('total_modules', 0)} 个模块，"
                f"当前已采用该设计 {gap_report.get('adopted_count', 0)} 个，"
                f"未采用 {untouched} 个。是否全部铺排由对方 AI 依据实际意图判断，"
                f"本工具不臆断'该全部采用'。"
            ),
        })
        return steps

    # ─── 确定性核验（复用 verify_findings） ─────────────────────

    def _verify_blueprint_symbols(
        self, project_path: str, asset: Dict[str, Any]
    ) -> List[Dict[str, Any]]:
        """对蓝图引用的符号做确定性核验（复用 coderef_verify_findings 逻辑）。

        只核验"引用目标是否真实存在"，不核验语义结论。无图谱时返回空并标注。
        """
        from core.verify_findings import verify_findings
        blue = asset.get("blueprint") or {}
        entry_points = blue.get("entry_points") or []
        if not entry_points:
            return []
        findings = [{
            "title": f"蓝图入口「{e}」应存在",
            "file": "",
            "symbols": [e],
        } for e in entry_points]
        try:
            r = verify_findings(project_path, findings)
        except Exception as exc:
            logger.warning(f"[ReplicateEngine] 蓝图符号核验失败: {exc}")
            return []
        return [
            {
                "symbol": (f.get("evidence", {}).get("symbols") or [{}])[0].get("symbol", "?"),
                "verdict": f.get("verdict", "unverifiable"),
                "label_zh": f.get("label_zh", "存疑"),
                "reason": f.get("reason", ""),
            }
            for f in r.get("results", [])
        ]

    # ─── 主入口 ──────────────────────────────────────────────────

    def replicate(
        self,
        project_path: str,
        canonical: str,
        verify_symbols: bool = True,
    ) -> Dict[str, Any]:
        """复刻铺排：检测目标项目缺口 + 生成复刻指引。

        Args:
            project_path: 目标项目路径（要复刻到的项目）。
            canonical: 要复刻的已固化资产（canonical 或别名）。
            verify_symbols: 是否对蓝图入口做确定性核验（默认 True）。

        Returns:
            结构化 dict：缺口报告 + steps + entry_points + verified_findings。
        """
        asset = self._resolve_asset(canonical)
        resolved = self.registry.resolve(canonical)
        if not asset:
            return {
                "ok": False,
                "canonical": resolved,
                "message": (
                    f"资产「{resolved}」尚未固化。请先调用 coderef_asset(action='commit', "
                    f"canonical='{resolved}') 固化后，再执行复刻铺排。"
                ),
            }

        # 1. 收集目标项目签名
        try:
            signatures = self._collect_target_signatures(project_path)
        except Exception as exc:
            logger.error(f"[ReplicateEngine] 目标项目签名收集失败: {exc}")
            return {
                "ok": False,
                "canonical": resolved,
                "message": f"目标项目签名收集失败：{exc}",
            }

        # 2. 缺口判定
        gap_report = self._detect_adoption_gaps(project_path, resolved, signatures)

        # 3. 入口提取（补全蓝图缺口）
        entry_points = self._extract_entry_points(asset, project_path)

        # 4. 复刻步骤
        steps = self._build_steps(asset, gap_report)

        # 5. 确定性核验
        verified = self._verify_blueprint_symbols(project_path, asset) if verify_symbols else []

        # 6. 蓝图完整性评估
        blue = asset.get("blueprint") or {}
        missing = [f for f in BLUEPRINT_REQUIRED_FIELDS if not blue.get(f)]
        blueprint_complete = not missing

        return {
            "ok": True,
            "tool": "coderef_replicate",
            "canonical": resolved,
            "asset": {
                "category": asset.get("category", ""),
                "description": asset.get("description", ""),
                "intent": asset.get("intent", ""),
                "adoption_count": asset.get("adoption_count", 0),
                "adopters": asset.get("adopters", []),
            },
            "gap_report": gap_report,
            "entry_points": entry_points,
            "steps": steps,
            "verified_findings": verified,
            "blueprint_complete": blueprint_complete,
            "blueprint_missing_fields": missing,
            "message": (
                f"复刻铺排完成：资产「{resolved}」蓝图{'完整' if blueprint_complete else '待补全'}。"
                f"目标项目 {gap_report['total_modules']} 模块中已采用 {gap_report['adopted_count']} 个。"
                f"本指引为铺排建议，不自动改代码；template_code{'已提供' if asset.get('template_code') else '缺失（需补全）'}。"
                f"verified_findings 为确定性核验，仅代表引用目标存在性。"
            ),
        }

    # ─── 把铺排结果写回资产蓝图（可选） ─────────────────────────

    def solidify_blueprint(
        self, project_path: str, canonical: str, entry_points: Optional[List[str]] = None
    ) -> Dict[str, Any]:
        """把复刻铺排得出的 entry_points 等确定性结论写回资产蓝图。

        仅写回确定性可填字段（entry_points），不臆断 steps/verified_findings。
        供对方 AI 在确认铺排有效后调用，把蓝图从骨架补全为可复刻蓝图。
        """
        asset = self._resolve_asset(canonical)
        resolved = self.registry.resolve(canonical)
        if not asset:
            return {
                "ok": False,
                "canonical": resolved,
                "message": f"资产「{resolved}」尚未固化，无法补全蓝图。",
            }

        blue = dict(asset.get("blueprint") or {})
        if entry_points:
            # 去重保序
            seen = set()
            eps = []
            for e in entry_points:
                if e not in seen:
                    seen.add(e)
                    eps.append(e)
            blue["entry_points"] = eps

        # 补全 verified_findings（若为空）：用当前铺排的确定性核验结果
        if not blue.get("verified_findings"):
            verified = self._verify_blueprint_symbols(project_path, asset)
            if verified:
                blue["verified_findings"] = verified

        blue["notes"] = (
            "该蓝图为 4.4 复刻铺排后的补全版：entry_points 与 verified_findings 来自"
            "确定性核验；steps 依据目标项目缺口生成，可据此从零复刻该设计。"
        )

        asset["blueprint"] = blue
        self.registry.add_asset(asset)
        return {
            "ok": True,
            "canonical": resolved,
            "blueprint": blue,
            "message": f"资产「{resolved}」蓝图已补全（entry_points / verified_findings）。",
        }


# ═══════════════════════════════════════════════════════════════════
# 顶层接口（MCP handler 调用）
# ═══════════════════════════════════════════════════════════════════

def replicate_design(
    project_path: str,
    canonical: str,
    verify_symbols: bool = True,
) -> Dict[str, Any]:
    """复刻铺排：检测目标项目缺口 + 生成复刻指引。"""
    return ReplicateEngine().replicate(project_path, canonical, verify_symbols=verify_symbols)


def solidify_asset_blueprint(
    project_path: str,
    canonical: str,
    entry_points: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """把复刻铺排结论写回资产蓝图（确定性字段）。"""
    return ReplicateEngine().solidify_blueprint(project_path, canonical, entry_points=entry_points)


# ═══════════════════════════════════════════════════════════════════
# 渲染
# ═══════════════════════════════════════════════════════════════════

def render_report(result: Dict[str, Any]) -> str:
    """纯文本报告（终端/日志可读）。"""
    lines = ["复刻铺排报告", "=" * 3]
    if not result.get("ok"):
        lines.append(result.get("message", "复刻铺排失败"))
        return "\n".join(lines)

    lines.append(f"资产: {result['canonical']}")
    asset = result.get("asset", {})
    if asset.get("description"):
        lines.append(f"描述: {asset['description']}")
    gap = result.get("gap_report", {})
    lines.append(f"缺口: 目标项目 {gap.get('total_modules', 0)} 模块"
                 f" | 已采用 {gap.get('adopted_count', 0)}"
                 f" | 未采用 {gap.get('untouched_count', 0)}"
                 f" | 采用率 {gap.get('adoption_rate', 0)}")
    lines.append("")
    lines.append("复刻步骤:")
    for s in result.get("steps", []):
        lines.append(f"  {s['step']} — {s['action']}")
        lines.append(f"      {s['detail']}")
    if result.get("entry_points"):
        lines.append("")
        lines.append(f"入口: {', '.join(result['entry_points'])}")
    if result.get("verified_findings"):
        lines.append("")
        lines.append("确定性核验:")
        for v in result["verified_findings"]:
            lines.append(f"  [{v.get('label_zh','存疑')}] {v.get('symbol','?')} — {v.get('reason','')}")
    lines.append("")
    lines.append("图例: 复刻指引为铺排建议，不自动改代码; verified_findings 仅代表引用目标存在性。")
    return "\n".join(lines)


def render_html(result: Dict[str, Any]) -> str:
    """渲染非编程人员可读的 HTML 报告（自包含单文件）。"""
    from html import escape as _esc
    if not result.get("ok"):
        body = (f"<div style='background:#fff;border-radius:14px;padding:28px;'>"
                f"<h1 style='margin:0 0 12px;font-size:22px;'>复刻铺排报告</h1>"
                f"<p style='color:#E8463A;'>{_esc(result.get('message',''))}</p></div>")
    else:
        asset = result.get("asset", {})
        gap = result.get("gap_report", {})
        ep = "、".join(result.get("entry_points") or []) or "未提供（需自行确定）"

        steps_html = "".join(
            f"<div style='padding:12px;border:1px solid #eee;border-radius:10px;margin-bottom:8px;background:#fafbfc;'>"
            f"<div style='font-weight:600;color:#333;'>{_esc(s['step'])} — {_esc(s['action'])}</div>"
            f"<div style='color:#666;font-size:13px;margin-top:4px;'>{_esc(s['detail'])}</div></div>"
            for s in result.get("steps", [])
        )

        verified_html = ""
        if result.get("verified_findings"):
            vcolor = {"确证": "#1DC981", "证伪": "#E8463A", "部分确证": "#2E86DE", "存疑": "#EFAA17"}
            vrows = "".join(
                f"<tr><td style='padding:8px;border-bottom:1px solid #eee;'><span style='background:"
                f"{vcolor.get(v.get('label_zh','#EFAA17'),'#EFAA17')};color:#fff;border-radius:999px;"
                f"padding:2px 10px;font-size:12px;'>{_esc(v.get('label_zh','存疑'))}</span></td>"
                f"<td style='padding:8px;border-bottom:1px solid #eee;font-family:monospace;'>"
                f"{_esc(v.get('symbol',''))}</td>"
                f"<td style='padding:8px;border-bottom:1px solid #eee;color:#555;font-size:13px;'>"
                f"{_esc(v.get('reason',''))}</td></tr>"
                for v in result["verified_findings"]
            )
            verified_html = f"""<h3 style='margin:20px 0 8px;font-size:16px;'>确定性核验</h3>
            <table style='width:100%;border-collapse:collapse;font-size:14px;'>
              <thead><tr style='text-align:left;color:#888;font-size:12px;border-bottom:2px solid #eee;'>
                <th style='padding:8px;'>结论</th><th style='padding:8px;'>符号</th>
                <th style='padding:8px;'>依据</th></tr></thead><tbody>{vrows}</tbody></table>"""

        body = f"""<div style="background:#fff;border-radius:14px;padding:28px;box-shadow:0 1px 3px rgba(0,0,0,.06);">
    <h1 style="margin:0 0 4px;font-size:22px;">复刻铺排报告</h1>
    <div style="color:#888;font-size:13px;margin-bottom:16px;">
      资产 {_esc(result['canonical'])} · {_esc(asset.get('category',''))}
      · 蓝图{'完整' if result.get('blueprint_complete') else '待补全'}
    </div>
    <div style="color:#555;font-size:14px;line-height:1.7;margin-bottom:16px;">
      {_esc(asset.get('description',''))}
    </div>
    <div style="display:flex;gap:12px;flex-wrap:wrap;margin-bottom:16px;">
      <div style="background:#f0f7ff;border-radius:10px;padding:12px 16px;flex:1;min-width:140px;">
        <div style="font-size:22px;font-weight:700;color:#2E86DE;">{gap.get('adopted_count',0)}</div>
        <div style="color:#888;font-size:12px;">已采用模块</div></div>
      <div style="background:#fff5f0;border-radius:10px;padding:12px 16px;flex:1;min-width:140px;">
        <div style="font-size:22px;font-weight:700;color:#E8463A;">{gap.get('untouched_count',0)}</div>
        <div style="color:#888;font-size:12px;">未采用模块</div></div>
      <div style="background:#f5f5f5;border-radius:10px;padding:12px 16px;flex:1;min-width:140px;">
        <div style="font-size:22px;font-weight:700;color:#333;">{gap.get('total_modules',0)}</div>
        <div style="color:#888;font-size:12px;">目标模块总数</div></div>
    </div>
    <h3 style="margin:0 0 8px;font-size:16px;">入口</h3>
    <div style="font-family:monospace;background:#f5f5f5;border-radius:8px;padding:10px 12px;font-size:13px;color:#333;margin-bottom:16px;">
      {_esc(ep)}
    </div>
    <h3 style="margin:0 0 8px;font-size:16px;">复刻步骤</h3>
    {steps_html}
    {verified_html}
    <div style="margin-top:20px;font-size:12px;color:#999;line-height:1.8;">
      注意：复刻指引为铺排建议，不自动改代码；template_code{'已提供' if asset.get('template_code') else '缺失（需补全）'}；
      verified_findings 为确定性核验，仅代表引用目标存在性，不代表语义结论正确。
    </div>
  </div>"""

    return f"""<!DOCTYPE html><html lang="zh"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>复刻铺排报告</title></head>
<body style="margin:0;font-family:'Segoe UI','PingFang SC','Microsoft YaHei',sans-serif;background:#f6f7f9;color:#222;">
<div style="max-width:960px;margin:0 auto;padding:32px 20px;">{body}</div></body></html>"""


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", required=True, help="目标项目路径")
    ap.add_argument("--canonical", required=True, help="要复刻的已固化资产 canonical")
    ap.add_argument("--out_format", default="text", choices=["json", "html", "text"])
    args = ap.parse_args()
    r = replicate_design(args.project, args.canonical)
    if args.out_format == "text":
        print(render_report(r))
    elif args.out_format == "html":
        print(render_html(r))
    else:
        print(r)