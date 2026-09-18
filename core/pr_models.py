# -*- coding: utf-8 -*-
"""
数据模型（core/pipeline_runner 拆解子模块）：Tier / Finding / PipeResult。

原为 pipeline_runner.py 68~127 行；拆出后由主文件 re-export，对外符号不变。
依赖方向：本模块为最底层，不依赖任何其他 pr_* 子模块。
"""

from dataclasses import dataclass, field
from typing import List, Optional
from enum import Enum


class Tier(Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"

@dataclass
class Finding:
    id: str; tool: str; category: str; severity: str = "medium"
    file_path: str = ""; line: int = 0; title: str = ""
    detail: str = ""; suggestion: str = ""
    tier: Tier = Tier.LOW
    xval_by: List[str] = field(default_factory=list)
    # 相邻行合并后记录真实行号区间，避免标题退化为 "(等多行)"
    line_start: int = 0
    line_end: int = 0
    # 类型标记：defect=缺陷/缺失；advice=工程化改进建议（非缺陷）。
    # 汇总层据此区分"建议项"与"缺陷项"，避免把工程化 warn 等建议误报为缺陷。
    # 带默认值，向后兼容所有不含 kind 的现有 Finding 构造调用。
    kind: str = "defect"
    # 爆发式合并（_burst_merge）后保留的真实数量与全部位置。
    # count=1 表示未聚合（单条）；>1 表示该 finding 代表 N 条同 tool+category 违规。
    # locations 记录被合并的全部 file:line，避免"标题写共 N 条却无法定位其余 N-1 条"。
    # 带默认值，向后兼容所有不含 count/locations 的现有 Finding 构造调用。
    count: int = 1
    locations: List[str] = field(default_factory=list)

    @property
    def line_label(self) -> str:
        """可读的行号定位：单行显示 Ln，区间显示 Ln~Lm"""
        if self.line_end and self.line_end > self.line:
            return f"{self.file_path}:{self.line}~{self.line_end}"
        if self.line:
            return f"{self.file_path}:{self.line}"
        return self.file_path or ""

@dataclass
class PipeResult:
    project_path: str; total_files: int = 0; total_lines: int = 0
    findings: List[Finding] = field(default_factory=list)
    report: str = ""; errors: List[str] = field(default_factory=list)
    elapsed: float = 0.0; report_path: str = ""
    scope_text: str = ""  # 审计范围说明（披露被排除的目录，保证统计透明）
    # 审计证据字段：让调用方能区分"本次扫描结果"与"历史缓存/修复状态"，
    # 避免外层把旧知识图谱或记忆当成本次审计结论。
    scan_ts: str = ""              # 本次扫描开始时间（ISO 时间戳）
    file_snapshot: dict = field(default_factory=dict)  # 本次被扫描文件的 mtime+size 快照
    kg_built_at: str = ""          # 本次审计构建的知识图谱时间（若为历史图谱则为旧时间）
    wiki_result: Optional[object] = None  # coderef_docs 的 WikiResult，供 MCP 层返回结构化文档统计
    html_report: dict = field(default_factory=dict)  # HTML 报告渲染结果（见 report_renderer.render 返回值）
    arch_canvas: str = ""  # coderef_architecture 追加生成的架构画布绝对路径（无则空）
    # 功能②：审计策略判定 + LLM 功能审查增强结果
    review_strategy: dict = field(default_factory=dict)     # ReviewAdvisor.advise() 返回值
    functional_review: dict = field(default_factory=dict)   # FunctionalReviewer.review() 返回值
    # 动态兜底：本次审计实际采用的策略（full/incr/no_change），由 audit() 在跑工具前判定
    audit_strategy: str = "full"
    # 统一健康分（0-100）。空项目（0 代码文件）为 None（N/A）。
    # 由 _compute_health 统一计算，保证 run_single 与 audit 两入口一致（缺陷 10）。
    health_score: Optional[int] = None
