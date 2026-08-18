# -*- coding: utf-8 -*-
"""
replicate_engine — 复刻铺排引擎（4.4 创新建设翼：从资产蓝图到可复刻指引）
                   + 复刻落地（4.6：把铺排指引真正落到目标项目）

目标读者：编程 AI（执行复刻）与 AI 时代治理者（看懂铺排逻辑）。
核心问题：一个已固化的 WorkflowAsset（含结构化蓝图）怎么落到另一个项目里？
本工具检测目标项目对该设计的采用缺口，并结合蓝图 / 已验证采用清单 / 确定性
核验结论，产出可执行的复刻铺排指引（steps + entry_points + verified_findings）；
4.6 起新增 apply 落地能力，把 template_code 与 patch_suggestion 落到目标项目。

诚实话纪律（与 verify_findings / prompt_compliance 同源）：
- 缺口判定是确定性的：基于"目标项目是否已采用该设计能力"的能力签名比对，
  不臆断"该不该采用"；只报告"哪些模块有、哪些没有"。
- 复刻指引是"铺排建议"而非"自动改代码"：本工具是审计工具，不直接写代码，
  由对方 AI 依据 steps 与 template_code 自行落地；缺失的 template_code 明确标注。
- 4.6 apply 落地：只落地"确定性可给"的内容（template_code 骨架、patch_suggestion），
  且不覆盖目标项目已存在的同名文件——冲突时如实标注，绝不强写。
- verified_findings 复用 verify_findings 的确定性结论：只核验引用目标是否真实存在。
- entry_points 只从可信来源提取（蓝图已填 / 已验证采用模块的真实入口），不编造。

集成方式：作为 MCP 工具 coderef_replicate / coderef_replicate_apply 暴露。
"""

import os
import json
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

# 落地文件的默认文件名（template_code 无显式路径时使用）
DEFAULT_TEMPLATE_FILENAME = "replicate_template.py"

# 落地清单文件名
APPLY_MANIFEST_FILENAME = "replicate_apply_manifest.json"

# 落地骨架目录名（target 项目下，集中存放落地产物，避免污染源码树）
APPLY_OUTPUT_DIR = "coderef-replicate-apply"


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

    # ─── 复刻落地（4.6 新增） ────────────────────────────────────

    @staticmethod
    def _safe_apply_dest(name: str, kind: str, out_dir: str,
                         conflicts: List[Dict[str, Any]]) -> Optional[str]:
        """把相对文件名解析为 out_dir 内的安全绝对路径；越界（绝对路径 / 父级穿越）返回 None 并记冲突。"""
        # 拒绝绝对路径与含父级穿越（..）的路径，避免落地文件逃逸出集中落地目录
        if os.path.isabs(name) or ".." in name.split(os.sep):
            conflicts.append({
                "kind": kind,
                "dest": name,
                "reason": f"文件名 {name!r} 含绝对路径或父级穿越（..），已拒绝落地以留在落地目录内。",
            })
            return None
        dest = os.path.normpath(os.path.join(out_dir, name))
        # 双重校验：realpath 后仍须在 out_dir 之内（防符号链接/平台归一绕过）
        real_out = os.path.realpath(out_dir)
        real_dest = os.path.realpath(os.path.dirname(dest))
        if not (real_dest == real_out or real_dest.startswith(real_out + os.sep)):
            conflicts.append({
                "kind": kind,
                "dest": dest,
                "reason": f"目标 {dest!r} 在落地目录之外，已拒绝写入。",
            })
            return None
        return dest

    @staticmethod
    def _write_apply_file(dest: str, content: str) -> None:
        """写入落地文件（自动创建父目录）。"""
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        with open(dest, "w", encoding="utf-8") as f:
            f.write(content)

    def _apply_template_code(self, asset: Dict[str, Any], filename: str,
                             out_dir: str, overwrite: bool,
                             written: List[Dict[str, Any]],
                             conflicts: List[Dict[str, Any]],
                             missing_optional: List[str]) -> None:
        """1. 落地 template_code（确定性可给，且不覆盖已有文件）。"""
        template_code = asset.get("template_code") or ""
        if not (template_code or "").strip():
            missing_optional.append("template_code")
            return
        default_name = filename or DEFAULT_TEMPLATE_FILENAME
        dest = self._safe_apply_dest(default_name, "template_code", out_dir, conflicts)
        if dest is None:
            return
        if os.path.exists(dest) and not overwrite:
            conflicts.append({
                "kind": "template_code",
                "dest": dest,
                "reason": "目标文件已存在（不覆盖）。如需覆盖请设置 overwrite=true。",
            })
        else:
            self._write_apply_file(dest, template_code)
            written.append({"kind": "template_code", "dest": dest})

    def _apply_doc_fields(self, asset: Dict[str, Any], resolved: str,
                          out_dir: str, overwrite: bool,
                          written: List[Dict[str, Any]],
                          conflicts: List[Dict[str, Any]],
                          missing_optional: List[str]) -> None:
        """2. 落地 patch_suggestion / migration_guide（若存在，作为说明文档）。"""
        for kind, value in (("patch_suggestion", asset.get("patch_suggestion")),
                            ("migration_guide", asset.get("migration_guide"))):
            if not (value or "").strip():
                missing_optional.append(kind)
                continue
            ext = "_patch.md" if kind == "patch_suggestion" else "_migration.md"
            dest = self._safe_apply_dest(f"{resolved}{ext}", kind, out_dir, conflicts)
            if dest is None:
                continue
            if os.path.exists(dest) and not overwrite:
                conflicts.append({"kind": kind, "dest": dest, "reason": "目标文件已存在（不覆盖）。"})
            else:
                self._write_apply_file(dest, str(value))
                written.append({"kind": kind, "dest": dest})

    @staticmethod
    def _write_apply_manifest(resolved: str, asset: Dict[str, Any], out_dir: str,
                              written: List[Dict[str, Any]],
                              conflicts: List[Dict[str, Any]],
                              missing_optional: List[str],
                              overwrite: bool) -> str:
        """3. 生成落地清单 manifest（每次覆盖写：它记录本次落地状态，非既有源文件）。"""
        manifest = {
            "canonical": resolved,
            "asset": {
                "category": asset.get("category", ""),
                "description": asset.get("description", ""),
                "intent": asset.get("intent", ""),
                "adoption_count": asset.get("adoption_count", 0),
            },
            "entry_points": (asset.get("blueprint") or {}).get("entry_points", []),
            "target_dir": out_dir,
            "written": written,
            "conflicts": conflicts,
            "missing_optional": missing_optional,
            "overwrite": overwrite,
            "applied_at": __import__("datetime").datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "note": (
                "落地文件为复刻骨架与说明，不自动接入目标项目源码；"
                "请依据 entry_points 与蓝图 steps 由对方 AI 完成接入。"
            ),
        }
        manifest_fp = os.path.join(out_dir, APPLY_MANIFEST_FILENAME)
        with open(manifest_fp, "w", encoding="utf-8") as f:
            json.dump(manifest, f, ensure_ascii=False, indent=2)
        return manifest_fp

    @staticmethod
    def _apply_summary(resolved: str, out_dir: str,
                       written: List[Dict[str, Any]],
                       conflicts: List[Dict[str, Any]]) -> str:
        """落地结果摘要文案。"""
        return (
            f"复刻落地完成：资产「{resolved}」骨架已生成到 {out_dir}（写 {len(written)} 个文件）。"
            + (f" 有 {len(conflicts)} 项冲突未写入：{'；'.join(c['reason'] for c in conflicts[:3])}。"
               if conflicts else " 无冲突。")
            + " 落地文件为复刻骨架，不自动接入源码；接入请依据蓝图 steps 与 entry_points。"
        )

    def apply(
        self,
        project_path: str,
        canonical: str,
        target: str = "",
        filename: str = "",
        overwrite: bool = False,
    ) -> Dict[str, Any]:
        """把已固化资产的复刻指引落到目标项目（生成落地文件 + 落地清单）。

        Args:
            project_path: 触发调用的项目路径（用于解析资产）。
            canonical: 要落地的已固化资产 canonical（或别名）。
            target: 目标项目路径（默认 = project_path 对应项目根）。
            filename: 落地文件名（默认取 template_code 标题或 DEFAULT_TEMPLATE_FILENAME）。
            overwrite: 是否允许覆盖目标项目已存在的同名文件（默认 False，冲突时如实标注）。

        Returns:
            结构化 dict：落地文件清单 + 冲突清单 + 落地清单 manifest + 摘要。
        """
        asset = self._resolve_asset(canonical)
        resolved = self.registry.resolve(canonical)
        if not asset:
            return {
                "ok": False,
                "canonical": resolved,
                "message": f"资产「{resolved}」尚未固化，无法落地。请先调用 coderef_asset(action='commit') 固化。",
            }

        # 目标目录：显式 target 优先，否则落在目标项目根的集中落地目录下
        root = os.path.abspath(target or project_path)
        out_dir = os.path.join(root, APPLY_OUTPUT_DIR)
        os.makedirs(out_dir, exist_ok=True)

        written: List[Dict[str, Any]] = []
        conflicts: List[Dict[str, Any]] = []
        missing_optional: List[str] = []  # 资产未提供的可选内容（不算写入冲突）

        self._apply_template_code(asset, filename, out_dir, overwrite,
                                  written, conflicts, missing_optional)
        self._apply_doc_fields(asset, resolved, out_dir, overwrite,
                               written, conflicts, missing_optional)
        manifest_fp = self._write_apply_manifest(resolved, asset, out_dir,
                                                 written, conflicts,
                                                 missing_optional, overwrite)
        ok = not conflicts
        summary = self._apply_summary(resolved, out_dir, written, conflicts)
        return {
            "ok": ok,
            "tool": "coderef_replicate_apply",
            "canonical": resolved,
            "target_dir": out_dir,
            "manifest_file": manifest_fp,
            "written": written,
            "written_count": len(written),
            "conflicts": conflicts,
            "conflict_count": len(conflicts),
            "missing_optional": missing_optional,
            "summary": summary,
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


def apply_replicate(
    project_path: str,
    canonical: str,
    target: str = "",
    filename: str = "",
    overwrite: bool = False,
) -> Dict[str, Any]:
    """复刻落地：把已固化资产的复刻指引落到目标项目（4.6 新增）。"""
    return ReplicateEngine().apply(
        project_path, canonical,
        target=target, filename=filename, overwrite=overwrite,
    )


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