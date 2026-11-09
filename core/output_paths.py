# -*- coding: utf-8 -*-
"""输出路径与项目侧产物忽略规则（中立基础设施层）。

为什么单独成模块：`pipeline_runner`（编排层）与 `report_renderer`（展示层）都要
使用报告目录与 `.coderef/.gitignore` 规则。此前编排层反向 import 展示层只为拿
这两个函数（外部 Brooks 复审：Dependency Disorder），现移到中立模块，两侧平级共用。

作者: CodeRef-AI Team
版本: v1.0
"""

import os


def default_report_root(project_path: str) -> str:
    """报告产物根目录：按项目隔离（`<project>/.coderef/report`）。

    共享输出目录会 last-writer-wins：多项目交替跑 report 时，后跑项目覆盖
    `index.html`，前一项目的用户入口被顶掉（U-51）。放到项目侧与
    scorecard/findings 同域，既天然按项目隔离，也便于随项目一起版本管理。
    显式传 output_dir 时仍以调用方为准。
    """
    return os.path.join(os.path.abspath(project_path), ".coderef", "report")


def default_report_dir(project_path: str) -> str:
    """评估台 index.html 所在目录（`<project>/.coderef/report/html`）。"""
    return os.path.join(default_report_root(project_path), "html")


# 工具运行时产物的忽略清单（相对 .coderef/）；target_arch / whitelist / eval_config
# 属于设计/配置，刻意不忽略，便于随项目提交。
_CODEREF_IGNORE_LINES = (
    "# CodeRef-AI 运行时产物（自动生成，可删除本文件以改为自行管理）",
    "report/", "kg/", "wiki/", "*.html",
    "scorecard.json", "product_fit.json", "ux_fit.json",
    "business_semantics.json", "audit_findings_*.json", "governance.db",
)


def ensure_coderef_gitignore(project_path: str) -> None:
    """确保 `<project>/.coderef/.gitignore` 覆盖运行时产物（已存在则不覆盖）。

    外部审计反馈：`.coderef/report/`、`arch_layout_*.html` 等产物默认可被 git
    跟踪，使用者会困惑该提交还是忽略。以“只在缺失时创建”的方式给出默认口径。
    """
    d = os.path.join(os.path.abspath(project_path), ".coderef")
    fp = os.path.join(d, ".gitignore")
    if os.path.isfile(fp):
        return
    try:
        os.makedirs(d, exist_ok=True)
        with open(fp, "w", encoding="utf-8") as f:
            f.write("\n".join(_CODEREF_IGNORE_LINES) + "\n")
    except OSError:
        pass
