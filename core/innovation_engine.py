# -*- coding: utf-8 -*-
"""
创新引擎 —— 把设计传播检测升级为结构化创新识别 + 资产化

复用底座 InnovationPropagationDetector 的底层能力（能力签名提取 / 模块聚类 /
缺口检测），把原本只输出 Markdown 的报告升级为结构化 dict，并支持把确定的设计
固化为 WorkflowAsset（写入 data/design_registry.json 资产区）。

供 MCP 工具 coderef_innovation / coderef_asset 调用。

资产化（asset.commit）遵循防污染约束（对应文档 15.2）：
  - 需 ≥ MIN_ADOPTION_FOR_SOLIDIFY 个 workflow 真实采用该设计；
  - 且必须附带 evidence（真实采用记录）才允许固化；
  - 不满足条件时拒绝写入并返回 ok=False，避免污染资产库。

设计约束（与底座一致）：
  - 纯标准库 + 复用底座（core/innovation_propagation_detector.py、
    core/design_registry.py），不引入第三方新依赖；
  - 面向使用者的可读文本一律中文；
  - LLM 缺失时自动降级为纯结构对比；
  - 异常不静默吞掉；magic number 集中定义为模块级常量；不改 config/settings.py。

作者: CodeRef-AI Team
版本: v1.0
"""

import os
from datetime import datetime
from typing import Dict, List, Optional, Any

from loguru import logger

from core.innovation_propagation_detector import InnovationPropagationDetector
from core.design_registry import DesignRegistry, SEED_DESIGNS


# ═══════════════════════════════════════════════════════════════════
# 模块级常量（集中管理 magic number）
# ═══════════════════════════════════════════════════════════════════

# 意图分组顺序
INTENT_ORDER: List[str] = ["prompt", "validation", "retry", "orchestration"]

# 意图 → 该意图的"理想设计" canonical
INTENT_DESIGN: Dict[str, str] = {
    "prompt": "prompt_template",
    "validation": "validation_chain",
    "retry": "retry_wrapper",
    "orchestration": "orchestration",
}

# 设计 canonical → 对应的能力签名标签（用于在 AST 签名中探测采用）
DESIGN_CAPABILITY_TAG: Dict[str, str] = {
    "prompt_template": "prompt_template",
    "validation_chain": "validation_chain",
    "retry_wrapper": "retry_logic",
    "orchestration": "pipeline_flow",
}

# 固化资产所需的最小 workflow 采用数（防污染，对应文档 15.2）
MIN_ADOPTION_FOR_SOLIDIFY = 2

# 时间戳格式
TIMESTAMP_FORMAT = "%Y-%m-%d %H:%M:%S"


# ═══════════════════════════════════════════════════════════════════
# 数据结构
# ═══════════════════════════════════════════════════════════════════

class WorkflowAsset:
    """确定的设计资产 —— 已通过验证、具备 ≥2 采用 + evidence 的可复用资产。

    作为 dict 写入 design_registry.json 的 assets 区。
    """

    def __init__(
        self,
        canonical: str,
        category: str = "",
        description: str = "",
        intent: str = "",
        template_code: str = "",
        patch_suggestion: str = "",
        migration_guide: str = "",
        adopters: Optional[List[str]] = None,
        adoption_count: int = 0,
        evidence: bool = False,
        solidified_at: str = "",
        source_project: str = "",
    ):
        self.canonical = canonical
        self.category = category
        self.description = description
        self.intent = intent
        self.template_code = template_code
        self.patch_suggestion = patch_suggestion
        self.migration_guide = migration_guide
        self.adopters = adopters or []
        self.adoption_count = adoption_count
        self.evidence = evidence
        self.solidified = bool(evidence) and adoption_count >= MIN_ADOPTION_FOR_SOLIDIFY
        self.solidified_at = solidified_at
        self.source_project = source_project

    def to_dict(self) -> Dict[str, Any]:
        return {
            "canonical": self.canonical,
            "category": self.category,
            "description": self.description,
            "intent": self.intent,
            "template_code": self.template_code,
            "patch_suggestion": self.patch_suggestion,
            "migration_guide": self.migration_guide,
            "adopters": self.adopters,
            "adoption_count": self.adoption_count,
            "evidence": self.evidence,
            "solidified": self.solidified,
            "solidified_at": self.solidified_at,
            "source_project": self.source_project,
        }

    @staticmethod
    def from_dict(data: Dict[str, Any]) -> "WorkflowAsset":
        return WorkflowAsset(
            canonical=data.get("canonical", ""),
            category=data.get("category", ""),
            description=data.get("description", ""),
            intent=data.get("intent", ""),
            template_code=data.get("template_code", ""),
            patch_suggestion=data.get("patch_suggestion", ""),
            migration_guide=data.get("migration_guide", ""),
            adopters=data.get("adopters", []),
            adoption_count=data.get("adoption_count", 0),
            evidence=data.get("evidence", False),
            solidified_at=data.get("solidified_at", ""),
            source_project=data.get("source_project", ""),
        )


class InnovationEngine:
    """创新引擎 —— 结构化创新识别 + 资产化。"""

    def __init__(self, llm_client=None):
        self.detector = InnovationPropagationDetector(llm_client=llm_client)
        self.registry = DesignRegistry()

    # ─── 内部工具 ────────────────────────────────────────────────

    @staticmethod
    def _primary_intent(sig) -> str:
        """根据能力签名判断模块的『主意图』（prompt/validation/retry/orchestration）。"""
        for it in INTENT_ORDER:
            design = INTENT_DESIGN[it]
            tag = DESIGN_CAPABILITY_TAG.get(design)
            if tag and tag in sig.tags:
                return it
        return "misc"

    @staticmethod
    def _gap_to_dict(g) -> Dict[str, Any]:
        """把 PropagationGap 转成结构化 dict。"""
        return {
            "source_module": g.source_module,
            "source_file": g.source_file,
            "target_module": g.target_module,
            "target_file": g.target_file,
            "pattern": {
                "name": g.pattern.pattern_name,
                "category": g.pattern.pattern_category,
                "description": g.pattern.description,
                "source_location": g.pattern.source_location,
                "confidence": g.pattern.confidence,
            },
            "suggestion": g.suggestion,
            "cluster_size": g.cluster_size,
            "adoption_rate": round(g.adoption_rate, 4),
        }

    def _find_adopters(self, project_path: str, canonical: str) -> List[str]:
        """查找真实采用某设计的 workflow（模块名）列表，作为 evidence。"""
        tag = DESIGN_CAPABILITY_TAG.get(canonical)
        if not tag:
            return []
        signatures = self.detector.collect_signatures(project_path)
        return [sig.module_name for sig in signatures if tag in sig.tags]

    def _gather_clusters_gaps(self, project_path: str):
        """复用检测器底层：收集签名、聚类、做纯结构缺口检测，并按价值挑选 top-N。

        通过检测器公共接口 gather_clusters_gaps 调用，避免直接访问私有成员。
        """
        return self.detector.gather_clusters_gaps(project_path)

    # ─── 检测（结构化创新识别） ────────────────────────────────

    def detect(
        self,
        project_path: str,
        intent: str = "",
        min_adoption: float = 0.0,
    ) -> Dict[str, Any]:
        """结构化创新识别。

        Args:
            project_path: 项目根目录。
            intent: 若提供，仅关注该意图（prompt/validation/retry/orchestration）。
            min_adoption: 最小采用率阈值，低于该采用率的设计不返回。

        Returns:
            结构化 dict（见字段说明）。
        """
        logger.info(f"[InnovationEngine] 开始检测: {project_path}")
        signatures, clusters, gaps = self._gather_clusters_gaps(project_path)
        total = len(signatures)

        # 为幸存缺口生成 LLM 精建议（受类级 LLM 预算约束，与 detector.detect() 对齐）
        self.detector.refine_gap_suggestions(gaps, signatures)

        # ── workflows ──
        workflows: List[Dict[str, Any]] = []
        for sig in signatures:
            adopted = [
                d for d, tag in DESIGN_CAPABILITY_TAG.items()
                if tag in sig.tags and d in SEED_DESIGNS
            ]
            wf = {
                "wf_id": sig.module_name,
                "intent": self._primary_intent(sig),
                "name": sig.module_name,
                "files": [sig.file_path],
                "adoption": adopted,
                "adoption_rate": round(
                    len(adopted) / len(DESIGN_CAPABILITY_TAG), 4
                ) if DESIGN_CAPABILITY_TAG else 0.0,
            }
            workflows.append(wf)

        # 意图过滤
        if intent and intent in INTENT_ORDER:
            workflows = [w for w in workflows if w["intent"] == intent]
        scope_total = len(workflows)

        # ── designs（每个已知设计的采用情况） ──
        designs: List[Dict[str, Any]] = []
        for canonical, info in SEED_DESIGNS.items():
            adopters = [w for w in workflows if canonical in w["adoption"]]
            adoption_n = len(adopters)
            rate = (adoption_n / scope_total) if scope_total else 0.0
            if min_adoption and rate < min_adoption:
                continue
            designs.append({
                "canonical": canonical,
                "category": info["category"],
                "description": info["description"],
                "adoption": adoption_n,
                "adoption_rate": round(rate, 4),
                "intent": next((k for k, v in INTENT_DESIGN.items() if v == canonical), ""),
            })

        # ── 意图分析：理想清单 vs 实际实现 ──
        intent_analysis: List[Dict[str, Any]] = []
        for it in INTENT_ORDER:
            ideal = [INTENT_DESIGN[it]]
            actual = [w for w in workflows if w["intent"] == it]
            adopted = [w for w in actual if INTENT_DESIGN[it] in w["adoption"]]
            intent_analysis.append({
                "intent": it,
                "ideal": ideal,
                "actual_workflows": [w["wf_id"] for w in actual],
                "adopted": len(adopted),
                "total": len(actual),
                "coverage": round(len(adopted) / len(actual), 4) if actual else 0.0,
            })

        # ── registry_matches：设计在注册表中的命中情况 ──
        registry_matches: List[Dict[str, Any]] = []
        for canonical in SEED_DESIGNS:
            adopters = [w["wf_id"] for w in workflows if canonical in w["adoption"]]
            registry_matches.append({
                "canonical": canonical,
                "matched": len(adopters) > 0,
                "workflows": adopters,
                "message": (
                    f"{len(adopters)} 个 workflow 采用「{canonical}」"
                    if adopters else f"暂无 workflow 采用「{canonical}」"
                ),
            })

        # ── solidifiable_assets：达到固化阈值的可固化清单 ──
        # 仅列出「≥ MIN_ADOPTION_FOR_SOLIDIFY 采用 + 附带 evidence（真实采用记录）」的
        # 设计。本工具是审计工具，不自动生成代码，因此只给出清单与证据，由对方 AI
        # 依据 description 自行补全 template_code / patch_suggestion / migration_guide，
        # 再调用 coderef_asset(action="commit") 完成固化。不满足条件的不出现在清单中。
        solidifiable_assets: List[Dict[str, Any]] = []
        for canonical, info in SEED_DESIGNS.items():
            adopters = [w["wf_id"] for w in workflows if canonical in w["adoption"]]
            adoption_count = len(adopters)
            if adoption_count < MIN_ADOPTION_FOR_SOLIDIFY:
                continue
            solidifiable_assets.append({
                "canonical": canonical,
                "category": info["category"],
                "description": info["description"],
                "intent": next((k for k, v in INTENT_DESIGN.items() if v == canonical), ""),
                "adoption_count": adoption_count,
                "adopters": adopters,
                "solidifiable": True,
                "commit_hint": (
                    f"已达到固化阈值（≥{MIN_ADOPTION_FOR_SOLIDIFY} 采用 + evidence）。"
                    f"请调用 coderef_asset(action='commit', canonical='{canonical}')，"
                    "补全 template_code / patch_suggestion / migration_guide 后完成固化。"
                ),
            })

        return {
            "ok": True,
            "project_path": project_path,
            "intent": intent,
            "min_adoption": min_adoption,
            "total_workflows": total,
            "workflows": workflows,
            "gaps": [self._gap_to_dict(g) for g in gaps],
            "designs": designs,
            "intent_analysis": intent_analysis,
            "registry_matches": registry_matches,
            "solidifiable_assets": solidifiable_assets,
            "registry_path": self.registry.registry_path,
        }

    # ─── 资产化 ────────────────────────────────────────────────

    def asset(
        self,
        project_path: str,
        action: str,
        canonical: str = "",
        description: str = "",
        template_code: str = "",
        patch_suggestion: str = "",
        migration_guide: str = "",
    ) -> Dict[str, Any]:
        """资产化管理。

        Args:
            project_path: 项目根目录。
            action: list / get / export / commit。
            canonical: 目标设计 canonical（或别名，自动归一化）。
            description: commit 时的设计描述。
            template_code: commit 时的模板代码。
            patch_suggestion: commit 时的补丁建议。
            migration_guide: commit 时的迁移指南。

        Returns:
            结构化 dict。
        """
        action = (action or "").strip().lower()

        if action == "list":
            return {
                "ok": True,
                "action": "list",
                "registry_path": self.registry.registry_path,
                "count": self.registry.count_assets(),
                "assets": self.registry.list_assets(),
            }

        if action in ("get", "export"):
            if not canonical:
                # export 可省略 canonical：导出全部资产
                if action == "export":
                    return {
                        "ok": True,
                        "action": "export",
                        "registry_path": self.registry.registry_path,
                        "count": self.registry.count_assets(),
                        "assets": self.registry.list_assets(),
                    }
                raise ValueError(f"{action} 操作必须提供 canonical 参数")
            resolved = self.registry.resolve(canonical)
            asset = self.registry.get_asset(resolved)
            return {
                "ok": bool(asset),
                "action": action,
                "canonical": resolved,
                "found": bool(asset),
                "asset": asset,
                "message": "资产存在" if asset else f"资产「{resolved}」不存在",
            }

        if action == "commit":
            if not canonical:
                raise ValueError("commit 操作必须提供 canonical 参数")
            resolved = self.registry.resolve(canonical)

            # 防污染检查：≥2 workflow 采用 + evidence
            adopters = self._find_adopters(project_path, resolved)
            adoption_count = len(adopters)
            evidence = bool(adopters)  # 真实采用记录即 evidence
            if adoption_count < MIN_ADOPTION_FOR_SOLIDIFY or not evidence:
                return {
                    "ok": False,
                    "action": "commit",
                    "canonical": resolved,
                    "adoption_count": adoption_count,
                    "solidified": False,
                    "message": (
                        f"拒绝固化（防污染，文档 15.2）：「{resolved}」仅有 "
                        f"{adoption_count} 个 workflow 采用，需达到 ≥ "
                        f"{MIN_ADOPTION_FOR_SOLIDIFY} 且附带 evidence 才允许固化。"
                    ),
                }

            seed_info = SEED_DESIGNS.get(resolved, {})
            asset = WorkflowAsset(
                canonical=resolved,
                category=seed_info.get("category", "misc"),
                description=description or seed_info.get("description", ""),
                intent=next((k for k, v in INTENT_DESIGN.items() if v == resolved), ""),
                template_code=template_code,
                patch_suggestion=patch_suggestion,
                migration_guide=migration_guide,
                adopters=adopters,
                adoption_count=adoption_count,
                evidence=True,
                solidified_at=datetime.now().strftime(TIMESTAMP_FORMAT),
                source_project=project_path,
            )
            self.registry.add_asset(asset.to_dict())
            logger.info(f"[InnovationEngine] 已固化资产「{resolved}」({adoption_count} 采用)")
            return {
                "ok": True,
                "action": "commit",
                "canonical": resolved,
                "solidified": True,
                "adoption_count": adoption_count,
                "evidence": True,
                "asset": asset.to_dict(),
            }

        raise ValueError(f"不支持的 action「{action}」，仅支持 list / get / export / commit。")


# ═══════════════════════════════════════════════════════════════════
# 便捷函数
# ═══════════════════════════════════════════════════════════════════

def detect_innovation(project_path: str, intent: str = "", min_adoption: float = 0.0) -> Dict[str, Any]:
    """便捷函数：结构化创新识别。"""
    engine = InnovationEngine()
    return engine.detect(project_path, intent=intent, min_adoption=min_adoption)


def manage_asset(
    project_path: str,
    action: str,
    canonical: str = "",
    description: str = "",
    template_code: str = "",
    patch_suggestion: str = "",
    migration_guide: str = "",
) -> Dict[str, Any]:
    """便捷函数：资产化管理。"""
    engine = InnovationEngine()
    return engine.asset(
        project_path, action, canonical=canonical, description=description,
        template_code=template_code, patch_suggestion=patch_suggestion,
        migration_guide=migration_guide,
    )