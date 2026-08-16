# -*- coding: utf-8 -*-
"""
CodeRef MCP Server v4.6 — 四大引擎 + 工具
  审计引擎     → coderef_audit / coderef_scan / coderef_scan_list / architecture / docs / query / review / frontend / whitelist / task_status
  记忆引擎     → coderef_memory_sync / memory_query / memory_status / memory_quality
  创新识别引擎 → coderef_innovation / asset / replicate / replicate_apply / asset_blueprint / registry
  变更守护引擎 → coderef_change_guard / change_report
  OWASP 合规   → coderef_owasp
  Prompt 治理  → coderef_prompt_governance（4.6 合并原 prompt_mgmt / prompt_audit）
  人话解读     → coderef_interpret
"""

import json, sys, os, logging, traceback, threading, time, uuid
from datetime import datetime
from typing import Dict, List, Any
from contextlib import contextmanager

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# 统一取包版本号，避免 serverInfo 与 __init__.py / README 版本漂移
def _pkg_version() -> str:
    try:
        ver_file = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "__init__.py")
        with open(ver_file, "r", encoding="utf-8") as f:
            for line in f:
                if line.strip().startswith("__version__"):
                    return line.split("=", 1)[1].strip().strip('"').strip("'")
    except Exception:
        pass
    return "4.0.0"

PKG_VERSION = _pkg_version()

logging.basicConfig(level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler(sys.stderr)])
logger = logging.getLogger("coderef")


# ─── 后台任务超时兜底常量 ─────────────────────────────────────
# 超大项目上单个后台工具（如 coderef_docs 全量生成、coderef_scan 的 gov 全量盘点）
# 一次调用可能远超 host 层轮询上限（真实案例 poll_timeout=900 用尽整步失败）。
# 后台线程为 daemon 会一直跑下去，_tsk 原只判 thread.is_alive()，永远返回 running，
# 导致外层 AI 在轮询用尽后拿到空结果。此处定义超时阈值：线程运行超过该秒数后，
# _tsk 不再无限返回 running，而是返回"部分完成 + 未完成提示"的诚实结构化结果，
# 让外层 AI 在放弃轮询前能拿到非空、不误导的部分结论（宁可部分完成，不要静默失败）。
# 取略低于常见 host 轮询上限（900s），保证 partial 状态在轮询用尽前可被读到。
MAX_BG_TASK_SECONDS = 860
# 分片提示门槛：后台任务运行超过该秒数时，在 partial 结果中建议调用方改用分片/增量
# 方式继续（如 resume=true 续跑、对子项目/维度逐个扫描），提示分片为更稳妥的路径。
BG_PARTIAL_SUGGEST_SECONDS = 300


# ─── 工具可靠性清单 ─────────────────────────────────────────────
# 汇总工具的可信度与使用边界，注入到 MCP 元数据（initialize.serverInfo.reliability +
# tools/list 各工具 description），让外层 LLM 在选工具时主动避开误报、改用更合适的工具。
# 依据：对多轮真实审计结果的人工复核（区分工具误报 vs 代码真问题）。
RELIABILITY_GUIDE = (
    "CodeRef 工具可靠性清单（供外层 AI 选工具时参考）：\n"
    "【可靠，可放心日常使用】coderef_audit / coderef_scan / coderef_owasp / "
    "coderef_architecture / coderef_change_guard / coderef_change_report / coderef_query / "
    "coderef_review / coderef_memory_* / coderef_task_status / coderef_verify_findings / "
    "coderef_prompt_governance / coderef_flow_verify / coderef_arch_audit。\n"
    "【有使用边界，需注意场景】\n"
    "  - coderef_frontend：仅静态枚举按钮/菜单'是否存在'，不确证'交互逻辑正确'；SPA 组件逻辑改用 mode=runtime 浏览器抽查。\n"
    "  - coderef_scan/audit 的 sca 维度（CVE 扫描）：不可全信。会把 poetry 配置 priority='primary' 误当依赖、"
    "把 >= 范围约束当固定版本。CVE 类发现需人工对照真实 CVE 库复核，勿直接采信。\n"
    "  - coderef_review：规则级 + LLM 审查，语义级缺陷（XSS/死代码/跨平台）覆盖有限；"
    "持有 LLM/CodeRabbit 论断时先用 coderef_verify_findings 做确定性核验再采信。\n"
    "【误报已修复】标准库 import 不再被 memory_quality 判为孤儿边；PII 拼接不再误报 api_address 等技术变量；"
    "测试目录默认不参与 agent 安全审计。\n"
    "【协作建议】收到 CVE/安全类发现时先人工复核再决定是否修复；超大文件/结构债属改进建议非缺陷。"
)

# 逐工具边界提示（追加到 tools/list 对应工具的 description 末尾）
TOOL_BOUNDARY_NOTES = {
    "coderef_scan": (
        "\n\n[可靠性] sca 维度(CVE)有已知误报：会把 poetry 配置 priority='primary' 误当依赖、"
        "把 >= 范围约束当固定版本。CVE 类结果需人工复核，勿直接采信。"
    ),
    "coderef_frontend": (
        "\n\n[可靠性] 仅静态扫描 HTML 按钮/菜单树，不适合 React/Vue SPA 组件逻辑；"
        "SPA 请改用 mode=runtime 做浏览器抽查。"
    ),
    "coderef_audit": (
        "\n\n[可靠性] 依赖扫描(CVE)子项有已知误报（见 coderef_scan 提示），"
        "CVE 类发现请人工复核后再决定是否修复。"
    ),
    "coderef_owasp": (
        "\n\n[可靠性] 其 CVE 检测复用 SCA，存在与 coderef_scan 相同的误报边界，违规项需人工复核。"
    ),
    "coderef_verify_findings": (
        "\n\n[可靠性] verdict 由确定性逻辑打出，只核验'引用目标是否存在/是否在管线内'，"
        "不核验'论断的语义结论是否正确'；图谱对动态调用/反射不完整，未确证不代表一定不存在。"
    ),
    "coderef_prompt_governance": (
        "\n\n[可靠性] 纯编排 + 确定性规则（含资产生命周期、合规审计与跨模块一致性，"
        "4.6 已合并原 coderef_prompt_mgmt / coderef_prompt_audit 全部能力），不引入 LLM；"
        "各维度如实标注是否已执行，不把'未审计'渲染成'无风险'；跨模块漂移是风险提示而非已发生故障。"
    ),
    "coderef_replicate": (
        "\n\n[可靠性] 缺口判定为确定性签名比对，只报告'有/没有'，不臆断'该不该采用'；"
        "复刻指引是铺排建议，不自动改代码；template_code 缺失会标注待补全。"
    ),
    "coderef_replicate_apply": (
        "\n\n[可靠性] 只落地'确定性可给'的骨架与说明，不自动接入目标源码；"
        "默认不覆盖已存在文件（冲突如实标注），overwrite=true 才允许覆盖。"
    ),
    "coderef_asset_blueprint": (
        "\n\n[可靠性] 仅写回确定性可填字段（entry_points / verified_findings），"
        "不臆断 steps；蓝图完整性取决于已固化资产质量。"
    ),
    "coderef_interpret": (
        "\n\n[可靠性] 人话解读全部来自确定性原语（健康分/审计/图谱/合规/论断核验），"
        "不引入 LLM 给结论；健康分只在确实审计过时给出，未审计绝不臆断；"
        "Wiki 等依赖 LLM 的能力在无 API Key 时诚实阻断。"
    ),
}


class Server:

    def __init__(self):
        self._tools = [
            {
                "name": "coderef_whitelist",
                "description": (
                    "管理 AI 白名单和核心模块判定规则。\n"
                    "action=add/list/clear → 误报白名单管理；\n"
                    "action=core_rules_get → 查看当前核心模块判定规则；\n"
                    "action=core_rules_set → 设置核心模块规则（entry_files入口文件名列表/core_names强制核心模块名/min_files文件数阈值）；\n"
                    "action=core_rules_reset → 重置为默认规则。\n"
                    "你审查完报告后，把确认无误的误报条目写入白名单。发现 Wiki 漏了核心模块时，用 core_rules_set 追加。"
                ),
                "inputSchema": {"type": "object", "properties": {
                    "project_path": {"type": "string", "description": "目标项目路径"},
                    "action": {"type": "string", "enum": ["add", "list", "clear", "core_rules_get", "core_rules_set", "core_rules_reset"], "default": "add"},
                    "entries": {
                        "type": "array", "items": {"type": "object",
                            "properties": {
                                "file": {"type": "string", "description": "文件路径子串"},
                                "rule": {"type": "string", "description": "规则名/标题子串"},
                                "category": {"type": "string", "description": "分类子串"},
                            }
                        },
                        "description": "要加入白名单的条目 (action=add 时必填)"
                    },
                    "core_rules": {
                        "type": "object",
                        "properties": {
                            "entry_files": {"type": "array", "items": {"type": "string"}, "description": "入口文件名列表，如 [\"main.py\",\"app.py\",\"server.py\"]"},
                            "core_names": {"type": "array", "items": {"type": "string"}, "description": "强制核心模块名列表，如 [\"洞察工具\",\"shared\"]"},
                            "min_files": {"type": "integer", "description": "文件数阈值（>=此值自动视为核心模块）"},
                        },
                        "description": "核心模块规则 (action=core_rules_set 时必填)"
                    },
                }, "required": ["project_path"]},
            },
            {
                "name": "coderef_audit",
                "description": (
                    "全维度代码审计 = 治理审计 + Agent安全 + 依赖扫描(CVE) + 技术债务 + "
                    "完整性检查 + 盲区检测 + 创新传播 + 垃圾文件 + 资源遗漏 + 代码精简 + 项目成熟度。\n"
                    "11 个工具一次产出，交叉验证自动分级(HIGH/MEDIUM/LOW)。\n"
                    "解决 AI 自查幻觉：多独立工具互验。\n"
                    "支持 background=True 后台执行。"
                ),
                "inputSchema": {"type": "object", "properties": {
                    "project_path": {"type": "string", "description": "目标项目路径"},
                    "output_dir": {"type": "string", "description": "报告输出目录（默认 coderef-report/）"},
                    "background": {"type": "boolean", "description": "后台执行", "default": True},
                    "strategy": {"type": "string",
                        "enum": ["auto", "full", "incr", "no_change"],
                        "default": "auto",
                        "description": "审计策略：auto=自动判定（首次全量/增量裁剪重型工具）；full=全量 11 工具；incr=增量裁剪；no_change=复用既有结论"},
                }, "required": ["project_path"]},
            },
            {
                "name": "coderef_architecture",
                "description": (
                    "架构分析图谱 = 代码结构分析 + 交互式模块画布(HTML)。\n"
                    "含 GitNexus 索引增强，展示模块交互关系、调用链。\n"
                    "用于发现零散重复代码、模块不统一等问题。"
                ),
                "inputSchema": {"type": "object", "properties": {
                    "project_path": {"type": "string", "description": "目标项目路径"},
                    "background": {"type": "boolean", "description": "后台执行（重型工具默认后台，返回 task_id 用 coderef_task_status 查询）", "default": True},
                }, "required": ["project_path"]},
            },
            {
                "name": "coderef_docs",
                "description": (
                    "项目文档探查 = 结构化 Wiki 生成(README/架构/安装/使用/API)。\n"
                    "三级管线：AST元数据(全量)→LLM归纳→编校验证(无幻觉)。\n"
                    "wiki_style 可选 comprehensive/reference/tutorial/plain；"
                    "include_subprojects 控制是否同时为子项目生成独立 Wiki。\n"
                    "自动发现子项目并生成独立 Wiki。\n"
                    "支持 background=True（推荐，生成耗时 3-20 分钟）。"
                ),
                "inputSchema": {"type": "object", "properties": {
                    "project_path": {"type": "string", "description": "目标项目路径"},
                    "output_dir": {"type": "string", "description": "输出目录（默认 txt/）"},
                    "wiki_style": {"type": "string", "enum": ["comprehensive","reference","tutorial","plain"], "default": "comprehensive"},
                    "include_subprojects": {"type": "boolean", "default": True},
                    "background": {"type": "boolean", "default": True},
                }, "required": ["project_path"]},
            },
            {
                "name": "coderef_docs_read",
                "description": (
                    "按需读取已生成的 Wiki 文档正文（返回文档内容，而非路径）。\n"
                    "解决编程 AI 无法主动调取外部文件夹的问题：docs 生成后正文落在磁盘，\n"
                    "本工具把正文作为返回值直接交给 AI，无需 fs 访问。\n"
                    "doc 为空 → 列出全部文档；doc 指定 → 返回该文档正文（可截断）。\n"
                    "输出目录自动探测 docs/wiki/ 或 txt/，也可用 output_dir 显式指定。"
                ),
                "inputSchema": {"type": "object", "properties": {
                    "project_path": {"type": "string", "description": "目标项目路径"},
                    "doc": {"type": "string", "description": "文档相对路径，如 README.md 或 MODULES/xxx.md；留空则列出全部"},
                    "output_dir": {"type": "string", "description": "Wiki 输出目录（可选，默认自动探测）"},
                    "max_chars": {"type": "integer", "description": "返回正文最大字符数", "default": 20000},
                }, "required": ["project_path"]},
            },
            {
                "name": "coderef_task_status",
                "description": "查询后台任务状态",
                "inputSchema": {"type": "object", "properties": {
                    "task_id": {"type": "string"},
                }},
            },
            {
                "name": "coderef_query",
                "description": (
                    "查询项目知识图谱（结构化项目记忆层）。\n"
                    "在运行 coderef_audit/coderef_docs/coderef_architecture 后自动构建。\n"
                    "query_type 支持:\n"
                    "  stats      → 图谱统计（节点数、边数、类型分布）\n"
                    "  entity     → 按名称搜索实体 (需 name；可选 type: function/class/module/config/constant)\n"
                    "  callers    → 查询谁调用了这个函数 (需 func_name)\n"
                    "  callees    → 查询这个函数调用了谁 (需 func_name)\n"
                    "  impact     → 修改影响分析：修改此文件会影响哪些模块 (需 file_path)\n"
                    "  relations  → 查询节点所有关系 (需 node_id)\n"
                    "  file_entities → 查询文件中的所有实体 (需 file_path)\n"
                    "  search     → 全文搜索 (需 keyword)\n"
                    "  call_graph → 调用链子图 (需 func_name；可选 depth 默认2)\n"
                    "用于编程 AI 替代 grep/读文件：精准查询项目结构，节省 10-100 倍 token。"
                ),
                "inputSchema": {"type": "object", "properties": {
                    "project_path": {"type": "string", "description": "目标项目路径"},
                    "query_type": {"type": "string", "enum": ["stats","entity","callers","callees","impact","relations","file_entities","search","call_graph"]},
                    "name": {"type": "string", "description": "实体名称（query_type=entity 时必填）"},
                    "func_name": {"type": "string", "description": "函数名（query_type=callers/callees/call_graph 时必填）"},
                    "file_path": {"type": "string", "description": "文件路径（query_type=impact/file_entities 时必填）"},
                    "node_id": {"type": "string", "description": "节点ID（query_type=relations 时必填）"},
                    "keyword": {"type": "string", "description": "搜索关键词（query_type=search 时必填）"},
                    "depth": {"type": "integer", "description": "调用链深度（call_graph 默认2）", "default": 2},
                    "type": {"type": "string", "description": "实体类型过滤（query_type=entity 时可选）"},
                    "limit": {"type": "integer", "description": "返回数量上限（search 默认30）", "default": 30},
                }, "required": ["project_path", "query_type"]},
            },
            {
                "name": "coderef_review",
                "description": (
                    "代码审查（Code Review）= 基于 diff 的变更审查 + 新项目全量语义首查。\n"
                    "mode=diff（默认）：审变更范围，给出行内评论（file:line + 分级 + 证据标记）。\n"
                    "mode=full：新项目无 git 历史时一次性全量语义审查，按模块分块 batching。\n"
                    "用 LLM 语义判断 + 上下文增强，结论带 evidence 标记（pending-human/static-confirmed）供交叉验证。\n"
                    "支持 background=True 后台执行。\n"
                    "诚实边界：本工具为规则级 + LLM 审查，对语义级缺陷（XSS、死代码、跨平台、竞态、资源泄漏）"
                    "覆盖有限。若你持有一条 LLM/CodeRabbit 论断，建议先用 coderef_verify_findings 做确定性核验"
                    "（确证引用目标是否真实存在），再决定是否采信，避免把未核验的语义论断当事实。"
                ),
                "inputSchema": {"type": "object", "properties": {
                    "project_path": {"type": "string", "description": "目标项目路径"},
                    "mode": {"type": "string", "enum": ["diff", "full"], "default": "diff", "description": "diff=审变更；full=新项目全量语义首查"},
                    "diff": {"type": "string", "description": "git diff 文本；与 changed_files 二选一（mode=diff 用）"},
                    "changed_files": {"type": "array", "items": {"type": "string"}, "description": "变更文件列表（无 diff 时用）"},
                    "dimensions": {"type": "array", "items": {"type": "string"}, "description": "审查维度，默认全部（bug/security/cross_module/maintainability/consistency/testing/regression）"},
                    "background": {"type": "boolean", "description": "后台执行", "default": True},
                }, "required": ["project_path"]},
            },
            {
                "name": "coderef_frontend",
                "description": (
                    "前端交互审查（Frontend Review）= 静态清单全量枚举 + LLM 审查（可选运行时抽查）。\n"
                    "静态枚举 HTML/JS 所有按钮（含事件/确认弹窗/禁用）与 L1-L5 菜单树，再按 6 维度审查。\n"
                    "mode=static（默认）：静态清单 + LLM 审查，不依赖浏览器，100% 覆盖。\n"
                    "mode=runtime：需 url，用浏览器抽查关键路径，失败自动降级为静态结论。\n"
                    "支持 background=True 后台执行。\n"
                    "诚实边界：静态枚举只确证按钮/菜单'存在与否'，不确证'交互逻辑正确'；"
                    "SPA 组件逻辑请用 mode=runtime 浏览器抽查，或联动外部前端审查工具。"
                ),
                "inputSchema": {"type": "object", "properties": {
                    "project_path": {"type": "string", "description": "前端项目路径"},
                    "entry": {"type": "string", "description": "入口 HTML/路由文件；不填则自动扫描"},
                    "mode": {"type": "string", "enum": ["static", "runtime"], "default": "static"},
                    "url": {"type": "string", "description": "运行 URL（mode=runtime 时必填）"},
                    "check_levels": {"type": "array", "items": {"type": "integer"}, "description": "要审查的菜单层级，默认 [1,2,3,4,5]"},
                    "background": {"type": "boolean", "description": "后台执行", "default": True},
                }, "required": ["project_path"]},
            },
            {
                "name": "coderef_report",
                "description": (
                    "把审计报告 / 知识图谱 / Wiki 聚合成自包含 HTML 报告目录（解决没有有效前端的问题）。\n"
                    "渲染到 output_dir（默认 coderef-report/html/）：index.html（概览+导航）/ audit.html / kg.html / wiki.html。\n"
                    "优先重渲染既有产物（图谱+Wiki，不重跑扫描，速度快）；若项目尚无审计/图谱产物，则回退为跑一次全量审计并渲染。\n"
                    "返回 index.html 绝对路径与生成文件清单。"
                ),
                "inputSchema": {"type": "object", "properties": {
                    "project_path": {"type": "string", "description": "目标项目路径"},
                    "output_dir": {"type": "string", "description": "报告输出目录（默认 coderef-report/html/）"},
                    "background": {"type": "boolean", "description": "后台执行（重型工具默认后台，返回 task_id 用 coderef_task_status 查询）", "default": True},
                }, "required": ["project_path"]},
            },
            {
                "name": "coderef_audit_advisor",
                "description": (
                    "审计策略判定 + 功能审查（审计前先和 AI 沟通审查范围）。\n"
                    "不直接跑代码审计，而是先判断本次该【增量审查】还是【全量审查】。\n"
                    "依据：变更信号（记忆层快照 diff）+ 知识图谱影响闭包（多跳 BFS）+ 图谱新旧。\n"
                    "同时给出应重点审查的功能维度（创新传播/结构复杂度/回归一致性等），\n"
                    "并可选叠加 LLM 功能审查（with_functional=True 时）。\n"
                    "建议：调用前先 coderef_memory_sync 建立基线，效果最佳。"
                ),
                "inputSchema": {"type": "object", "properties": {
                    "project_path": {"type": "string", "description": "目标项目路径"},
                    "with_functional": {"type": "boolean", "description": "是否叠加 LLM 功能审查增强", "default": True},
                    "background": {"type": "boolean", "description": "后台执行（重型工具默认后台，返回 task_id 用 coderef_task_status 查询）", "default": True},
                }, "required": ["project_path"]},
            },
        ]
        # ── 单维度审计工具（coderef_scan）：11 个维度合并为 1 个工具
        #    实时安全带 + 客观第二意见：AI 写完一个模块即可按需调用单维度自查，
        #    无需跑全量 coderef_audit。用 tool 参数选维度，避免 11 个同构工具
        #    撑爆工具列表、增加 AI 选择负担。复用 pipeline_runner.run_single()。
        self._SINGLE_TOOL_LABELS = [
            ("gov", "治理审计"), ("agent", "Agent安全"), ("sca", "依赖扫描(CVE)"),
            ("td", "技术债务"), ("integ", "完整性检查"), ("blind", "盲区检测"),
            ("inn", "创新传播"), ("junk", "垃圾文件"), ("resgap", "资源遗漏"),
            ("simp", "代码精简"), ("matu", "项目成熟度"),
        ]
        self._tools.append({
            "name": "coderef_scan",
            "description": (
                "单维度代码审计，实时安全带 + 客观第二意见。\n"
                "coderef_audit 是 11 个维度一次全跑；本工具只跑 tool 指定的一个维度，"
                "速度快一个量级（不建知识图谱/不生成 dashboard），适合 AI 写完一个模块后即时自查。\n"
                "tool 可选: gov治理 / agent安全 / sca依赖CVE / td技术债务 / integ完整性 / "
                "blind盲区 / inn创新传播 / junk垃圾文件 / resgap资源遗漏 / simp代码精简 / matu成熟度。\n"
                "先用 coderef_scan_list 查看可选维度清单。返回该维度 findings（tier 分级 + file/line + suggestion）。\n"
                "[可靠性] 单维度扫描只跑一个工具，无法产生「多工具交叉验证」的 xval_by 字段"
                "（交叉验证需 coderef_audit 全量多工具互验），请勿把空 xval_by 误判为异常。"
            ),
            "inputSchema": {"type": "object", "properties": {
                "project_path": {"type": "string", "description": "目标项目路径"},
                "tool": {"type": "string", "enum": [k for k, _ in self._SINGLE_TOOL_LABELS],
                         "description": "要审计的维度，如 gov / agent / sca / td / integ / blind / inn / junk / resgap / simp / matu"},
                "background": {"type": "boolean",
                    "description": "后台执行（超大项目单维度如 gov 会全量盘点，可能超过单次调用超时，默认后台返回 task_id 用 coderef_task_status 查询）",
                    "default": True},
            }, "required": ["project_path", "tool"]},
        })
        self._tools.append({
            "name": "coderef_scan_list",
            "description": "列出 coderef_scan 可选的单维度审计清单（维度名 + 说明）。",
            "inputSchema": {"type": "object", "properties": {}},
        })
        # ── 流程合规验证：非编程人员验证项目是否按期望流程执行 ──
        self._tools.append({
            "name": "coderef_flow_verify",
            "description": (
                "流程合规验证 —— 非编程人员最核心的需求：项目是不是按我期待的流程执行。\n"
                "验证「入口 A 的调用管线是否覆盖期望步骤 B→C→D」，确认数据真的按这条管线走。\n"
                "纯静态、确定性：数据只来自知识图谱 CALLS 边，不依赖 LLM。\n"
                "entry 支持 模块.函数（如 pipeline_runner.audit）消除同名歧义；"
                "steps 传期望步骤的符号关键词（中英文均可，编程 AI 需先把中文期望步骤映射为代码符号）。\n"
                "状态语义：ordered=调用链确证(含顺序)；in_pipeline=在管线但顺序未确证(可能并行)；"
                "outside=管线外/动态调用，需编程AI复核；missing=项目内无对应符号。\n"
                "图谱不存在时会明确反馈需先构建（coderef_audit / coderef_memory_sync）。"
            ),
            "inputSchema": {"type": "object", "properties": {
                "project_path": {"type": "string", "description": "目标项目路径"},
                "entry": {"type": "string", "description": "入口符号，支持 模块.函数（如 pipeline_runner.audit）"},
                "steps": {"type": "array", "items": {"type": "string"},
                          "description": "期望步骤的符号关键词列表，如 ['analyze_project','build_knowledge_graph','render']"},
                "depth": {"type": "integer", "description": "调用链搜索深度，默认 8"},
            }, "required": ["project_path", "entry", "steps"]},
        })
        # ── 架构腐化诊断：非编程人员验证工程结构是否健康 ──
        self._tools.append({
            "name": "coderef_arch_audit",
            "description": (
                "架构腐化诊断 —— 补齐 MCP 工具的架构诊断层。\n"
                "复用知识图谱 CALLS 边做模块级静态诊断，输出四类架构症状：\n"
                "cycles=循环依赖（模块依赖图强连通分量）；god_modules=上帝模块（扇出过高）；"
                "layer_violations=分层违例（低层依赖高层）；large_modules=异常模块规模。\n"
                "聚合为 0-10 架构健康度。纯静态、确定性，不依赖 LLM。\n"
                "图谱不存在时会明确反馈需先构建（coderef_audit / coderef_memory_sync）。"
            ),
            "inputSchema": {"type": "object", "properties": {
                    "project_path": {"type": "string", "description": "目标项目路径"},
                }, "required": ["project_path"]},
        })
        # ── 诚实话解读护栏：确定性核验 LLM / CodeRabbit 论断 ──
        self._tools.append({
            "name": "coderef_verify_findings",
            "description": (
                "确定性核验 LLM / CodeRabbit 论断（爬取翼咽喉 + 诚实话解读护栏）。\n"
                "编程 AI 或 CodeRabbit 给出一条'论断'（finding），本工具用知识图谱 + 静态原语"
                "核验论断引用的代码目标是否真实存在、是否在指定入口管线内，\n"
                "输出 verdict（确证/证伪/部分确证/无法核验）+ 证据链 + 影响面。\n"
                "诚实话纪律：verdict 只由本工具的确定性逻辑打出，调用方 AI 无权改变；\n"
                "无确定性证据一律存疑，绝不默认确证；确证只代表'引用目标真实存在'，不代表语义结论正确。\n"
                "findings 传论断列表，每条含 title（必填）+ detail/file/line/rule/severity/symbols（可选）；\n"
                "entry 可选：指定入口符号（模块.函数）核验符号是否在关键管线内；\n"
                "out_format=html 输出自包含人话 HTML 报告（非编程人员可读）。\n"
                "图谱不存在会明确反馈需先构建（coderef_audit / coderef_memory_sync），不返回空结论。"
            ),
            "inputSchema": {"type": "object", "properties": {
                "project_path": {"type": "string", "description": "目标项目路径（自动定位知识图谱）"},
                "findings": {"type": "array", "items": {"type": "object"},
                             "description": "论断列表，每条含 title(必填)+detail/file/line/rule/severity/symbols(可选)"},
                "entry": {"type": "string", "description": "可选入口符号（模块.函数），核验符号是否在关键管线内"},
                "out_format": {"type": "string", "enum": ["json", "html", "text"], "default": "json",
                               "description": "输出格式：json=结构化 / html=自包含人话报告 / text=终端可读"},
                "background": {"type": "boolean", "description": "后台执行（核验需加载图谱，重型工具默认后台）", "default": True},
            }, "required": ["project_path", "findings"]},
        })
        # ── 引擎四 · 变更守护：AI 代码退化检测 + 人话版变更报告 ──
        self._tools.append({
            "name": "coderef_change_guard",
            "description": (
                "AI 代码退化检测 —— 拦截「AI 把之前写好的代码改坏了」。\n"
                "守护引擎建立在 git 之上：先确保 git 基层，再对比基线与新代码能力签名，"
                "识别四类退化：校验链被删(high)、重试/超时削弱(medium)、输入约束移除(medium)、回归风险。\n"
                "vibecoder 最需要的功能：AI 改没改坏代码，提交前自动拦截。\n"
                "action=guard（默认）：退化检测。\n"
                "  动态兜底：传 diff 则精确检测；否则传 baseline_dir 全量对比；"
                "两者皆缺时自动从 git 历史提取最近改动作为基线对比"
                "(git-auto；若工作区干净会回退检测最近一次提交的改动)；"
                "仍无法建立基线则明确反馈需补充输入，绝不静默返回空结论。\n"
                "  返回附带 git_ready 与 health_baseline（最近健康基线 tag），供外层 AI 回滚参照。\n"
                "action=ensure_git：守护前置保障。项目无 git 时自动 git init 并补齐最小配置，"
                "使守护引擎从形同虚设变为真正可用。\n"
                "action=anchor：锚定健康基线。把审计通过/人工确认健康的当前代码 commit 并打 "
                "coderef-health-* tag，作为后续回滚参照。label 可选。\n"
                "action=list_baselines：列出全部健康基线 tag。\n"
                "回滚交由外层 AI 执行（如 git checkout <health_baseline tag>），CodeRef 仅提供确定性参照。\n"
                "git_bin 可选：由外层 AI 用 Get-Command git / where git 探测 git 可执行文件路径或安装目录后传入，"
                "避免依赖系统 PATH（git 常不在 PATH）。缺省回退到 PATH 的 git。\n"
                "git_timeout 建议：小型项目(<1万行)15s；中型(1~10万行)30s；大型(>10万行)60s。"
            ),
            "inputSchema": {"type": "object", "properties": {
                "project_path": {"type": "string", "description": "目标项目路径（新代码）"},
                "action": {"type": "string", "enum": ["guard", "ensure_git", "anchor", "list_baselines"], "default": "guard", "description": "guard=退化检测；ensure_git=确保 git 基层；anchor=锚定健康基线；list_baselines=列出健康基线"},
                "diff": {"type": "string", "description": "git diff 文本（action=guard，推荐，用于精确检测）"},
                "baseline_dir": {"type": "string", "description": "基线目录（改动前的代码快照，action=guard 可选）"},
                "label": {"type": "string", "description": "健康基线标签（action=anchor 可选，如 release-1.0）"},
                "allow_autocommit": {"type": "boolean", "description": "anchor 时若工作区有改动是否先自动提交再打 tag（默认 true，使基线指向完整健康状态）"},
                "git_bin": {"type": "string", "description": "git 可执行文件路径或安装目录（由外层 AI 探测后传入，可选；缺省回退 PATH 的 git）"},
                "git_timeout": {"type": "integer", "description": "git 命令超时秒数；默认 30，小型项目 15 / 中型 30 / 大型 60"},
                "background": {"type": "boolean", "description": "后台执行（重型工具默认后台，返回 task_id 用 coderef_task_status 查询）", "default": True},
            }, "required": ["project_path"]},
        })
        self._tools.append({
            "name": "coderef_change_report",
            "description": (
                "人能看懂的变更报告 —— 把 diff 归纳为「人话版」变更说明。\n"
                "不是 diff，而是「新增 XX 功能 / 修改 XX 逻辑 / 可能影响 XX 地方 / 风险」，"
                "让不懂代码的人也能知道 AI 到底改了什么。\n"
                "LLM 不可用时自动降级为结构摘要，保证始终可读。"
            ),
            "inputSchema": {"type": "object", "properties": {
                "project_path": {"type": "string", "description": "目标项目路径"},
                "diff": {"type": "string", "description": "git diff 文本"},
                "background": {"type": "boolean", "description": "后台执行（重型工具默认后台，返回 task_id 用 coderef_task_status 查询）", "default": True},
            }, "required": ["project_path", "diff"]},
        })
        # ── 引擎一 · 记忆层（M1/M2/M3）──────────────────────────────
        self._tools.append({
            "name": "coderef_memory_sync",
            "description": (
                "初始化 / 增量同步项目记忆层。用 mtime+size 快照做增量，只重扫变更文件。\n"
                "mode=full 全量初始化；mode=incr 增量（改一行只重扫该文件）。\n"
                "返回认知覆盖度、置信度、图谱/向量库统计。供所有 AI 助手复用项目记忆。"
            ),
            "inputSchema": {"type": "object", "properties": {
                "project_path": {"type": "string", "description": "目标项目路径"},
                "mode": {"type": "string", "enum": ["full", "incr"], "default": "full"},
                "background": {"type": "boolean", "description": "后台执行（重型工具默认后台，返回 task_id 用 coderef_task_status 查询）", "default": True},
            }, "required": ["project_path"]},
        })
        self._tools.append({
            "name": "coderef_memory_query",
            "description": (
                "供 AI 助手复用项目记忆（替代重扫）。\n"
                "query_type=semantic 语义检索（走向量库，Ollama 缺失降级关键词）；\n"
                "query_type=stats/entity/callers/callees/impact/relations/file_entities/search/call_graph 结构查询（走知识图谱）。"
            ),
            "inputSchema": {"type": "object", "properties": {
                "project_path": {"type": "string", "description": "目标项目路径"},
                "query_type": {"type": "string", "enum": ["semantic","stats","entity","callers","callees","impact","relations","file_entities","search","call_graph"], "default": "semantic"},
                "keyword": {"type": "string", "description": "语义检索关键词或全文搜索关键词"},
                "name": {"type": "string", "description": "实体名称（entity 用）"},
                "func_name": {"type": "string", "description": "函数名（callers/callees/call_graph 用）"},
                "file_path": {"type": "string", "description": "文件路径（impact/file_entities 用）"},
                "limit": {"type": "integer", "default": 10},
            }, "required": ["project_path"]},
        })
        self._tools.append({
            "name": "coderef_memory_status",
            "description": (
                "「AI 知道什么」：认知覆盖度 + 每模块置信度 + 盲区地图 + 认知地图 HTML。\n"
                "用户直观看到项目哪些部分已被 AI 理解、哪些未理解。"
            ),
            "inputSchema": {"type": "object", "properties": {
                "project_path": {"type": "string", "description": "目标项目路径"},
            }, "required": ["project_path"]},
        })
        self._tools.append({
            "name": "coderef_memory_quality",
            "description": (
                "记忆质量评估 + 自动补全。三项体检：引用完整性、语义覆盖、偏差检测。\n"
                "auto_fix=True 自动补全缺失上下文并标注来源；偏差检测自动注入全局 LLM（有 API Key 时真正复核），"
                "无可用 LLM 时降级 pending-human。"
            ),
            "inputSchema": {"type": "object", "properties": {
                "project_path": {"type": "string", "description": "目标项目路径"},
                "auto_fix": {"type": "boolean", "default": False},
                "background": {"type": "boolean", "description": "后台执行（重型工具默认后台，返回 task_id 用 coderef_task_status 查询）", "default": True},
            }, "required": ["project_path"]},
        })
        # ── 引擎 · 操作记忆层（4.8）──────────────────────────────
        # 与 memory_layer（记忆"代码是什么"）互补：操作记忆记忆"东西在哪儿、
        # 从哪儿来、到哪儿去、过去的规范是什么"，应对对话过多后上下文丢失。
        self._tools.append({
            "name": "coderef_operation_memory_sync",
            "description": (
                "初始化 / 增量同步「AI 操作记忆层」。静态审计识别主目录 + 旁目录资源位置"
                "（git / 模型权重 / API 引用 / 测试工具 / 文档报告 / 依赖清单），可选 LLM 提炼"
                "隐性知识（决策理由 / 约定俗成 / 踩坑解法）。输出 ledger.json + BRAIN.md，"
                "供对话上下文丢失后快速恢复。mode=full 全量盘点；mode=incr 增量（mtime+size 快照）。"
                "with_llm=false 跳过 LLM 提炼以省调用；API Key 缺失时自动降级为待人工确认。"
            ),
            "inputSchema": {"type": "object", "properties": {
                "project_path": {"type": "string", "description": "目标项目路径"},
                "mode": {"type": "string", "enum": ["full", "incr"], "default": "full",
                         "description": "full=全量盘点；incr=基于快照只重扫变更"},
                "with_llm": {"type": "boolean", "default": True,
                             "description": "是否启用 LLM 提炼隐性知识"},
                "background": {"type": "boolean", "description": "后台执行（同步含 LLM 提炼较慢，默认后台，返回 task_id 用 coderef_task_status 查询）", "default": True},
            }, "required": ["project_path"]},
        })
        self._tools.append({
            "name": "coderef_operation_memory_query",
            "description": (
                "按类别检索操作记忆（替代重新扫描）。query_type=decision / convention / "
                "pitfall 检索隐性知识；resource / tool / doc 检索资源定位；all 全量。"
                "keyword 可选，做 name/summary/path 模糊过滤。供 AI 上下文丢失后快速恢复。"
            ),
            "inputSchema": {"type": "object", "properties": {
                "project_path": {"type": "string", "description": "目标项目路径"},
                "query_type": {"type": "string",
                               "enum": ["all", "resource", "tool", "doc", "decision", "convention", "pitfall"],
                               "default": "all"},
                "keyword": {"type": "string", "description": "可选，模糊匹配 name/summary/path"},
                "limit": {"type": "integer", "default": 10},
            }, "required": ["project_path"]},
        })
        self._tools.append({
            "name": "coderef_operation_memory_find",
            "description": (
                "定位资源：给定资源名 / 路径片段，返回实际位置、来源、主目录 / 旁目录归属。"
                "例如想知道『test 工具在哪儿』『模型文件在哪儿』『API 配在哪儿』，别再满项目找。"
            ),
            "inputSchema": {"type": "object", "properties": {
                "project_path": {"type": "string", "description": "目标项目路径"},
                "name": {"type": "string", "description": "资源名或路径片段，如 'test'、'model'、'.env'"},
                "limit": {"type": "integer", "default": 5},
            }, "required": ["project_path", "name"]},
        })
        self._tools.append({
            "name": "coderef_operation_memory_status",
            "description": (
                "操作记忆健康状态：已覆盖分类、各分类条目数（资源 / 知识 / 旁目录）、"
                "LLM 可用性、待人工确认项。快速判断『这份操作记忆还新鲜吗、够覆盖吗』。"
            ),
            "inputSchema": {"type": "object", "properties": {
                "project_path": {"type": "string", "description": "目标项目路径"},
            }, "required": ["project_path"]},
        })
        # 引擎 · Prompt 治理（4.6 合并收敛：原 coderef_prompt_mgmt / coderef_prompt_audit
        # 已并入 coderef_prompt_governance 唯一入口，见下方 governance 工具定义）
        # ── 引擎三 · OWASP LLM 合规（M4）────────────────────────────
        self._tools.append({
            "name": "coderef_owasp",
            "description": (
                "OWASP LLM Top 10 合规检测。复用 AgentSecurityAuditor + SCA，\n"
                "把全部风险归并到 LLM01-LLM10 十类，补充 LLM09/LLM10 维度，逐类分级。\n"
                "未覆盖维度如实标注 covered=false（避免过度承诺）。out_format=report 输出中文合规报告。"
            ),
            "inputSchema": {"type": "object", "properties": {
                "project_path": {"type": "string", "description": "目标项目路径"},
                "out_format": {"type": "string", "enum": ["json", "report"], "default": "json"},
                "background": {"type": "boolean", "description": "后台执行（重型工具默认后台，返回 task_id 用 coderef_task_status 查询）", "default": True},
            }, "required": ["project_path"]},
        })
        # ── 引擎二 · 创新识别 + 资产沉淀（M6/M7）────────────────────
        self._tools.append({
            "name": "coderef_innovation",
            "description": (
                "识别项目创新设计 + 传播缺口。按意图分组（prompt/validation/retry/orchestration），\n"
                "理想清单 vs 实际实现对照，registry 归一化命名，输出 structured workflows/gaps/designs。"
            ),
            "inputSchema": {"type": "object", "properties": {
                "project_path": {"type": "string", "description": "目标项目路径"},
                "intent": {"type": "string", "description": "只查指定意图（空=全部）"},
                "min_adoption": {"type": "number", "description": "最小采用率过滤", "default": 0},
                "background": {"type": "boolean", "description": "后台执行（重型工具默认后台，返回 task_id 用 coderef_task_status 查询）", "default": True},
            }, "required": ["project_path"]},
        })
        self._tools.append({
            "name": "coderef_asset",
            "description": (
                "WorkflowAsset 资产化 / 查询 / 导出。\n"
                "action=list 列出资产；get 查单个（支持别名）；export 导出（可省略 canonical 导出全部）；"
                "commit 固化设计为资产（需 ≥2 workflow 采用 + evidence，防污染）。\n"
                "commit 时可选传 blueprint（结构化复刻蓝图 dict）；缺省自动从已验证 adopters 构建骨架。"
            ),
            "inputSchema": {"type": "object", "properties": {
                "project_path": {"type": "string", "description": "目标项目路径"},
                "action": {"type": "string", "enum": ["list","get","export","commit"], "default": "list"},
                "canonical": {"type": "string", "description": "规范设计名"},
                "description": {"type": "string", "description": "一句话说明（commit 用）"},
                "template_code": {"type": "string", "description": "可复制骨架代码（commit 用）"},
                "patch_suggestion": {"type": "string", "description": "迁移补丁建议（commit 用）"},
                "migration_guide": {"type": "string", "description": "迁移指南（commit 用）"},
                "blueprint": {"type": "object", "description": "结构化复刻蓝图 dict（commit 可选；缺省自动构建骨架）"},
                "background": {"type": "boolean", "description": "后台执行（重型工具默认后台，返回 task_id 用 coderef_task_status 查询）", "default": True},
            }, "required": ["project_path"]},
        })
        self._tools.append({
            "name": "coderef_replicate",
            "description": (
                "复刻铺排：检测目标项目对某已固化资产（蓝图）的采用缺口，并生成可复刻指引。\n"
                "输入 canonical（资产 canonical 或别名）与目标项目路径。\n"
                "输出 gap_report（确定性缺口：已采用/未采用模块）+ steps（复刻步骤）+ entry_points"
                "（入口，来自蓝图或已验证采用模块）+ verified_findings（复用 coderef_verify_findings 的确定性核验）。\n"
                "诚实话护栏：本工具是审计工具，不自动改代码；未采用不等于'该采用'；"
                "template_code 缺失会明确标注待补全，不编造。"
            ),
            "inputSchema": {"type": "object", "properties": {
                "project_path": {"type": "string", "description": "目标项目路径（要复刻到的项目）"},
                "canonical": {"type": "string", "description": "要复刻的已固化资产 canonical（或别名）"},
                "verify_symbols": {"type": "boolean", "description": "是否对蓝图入口做确定性核验", "default": True},
                "background": {"type": "boolean", "description": "后台执行（重型工具默认后台，返回 task_id 用 coderef_task_status 查询）", "default": True},
            }, "required": ["project_path", "canonical"]},
        })
        self._tools.append({
            "name": "coderef_replicate_apply",
            "description": (
                "复刻落地（4.6 新增）：把已固化资产的复刻指引真正落到目标项目。\n"
                "输入 canonical（资产 canonical 或别名）与目标项目路径。\n"
                "把资产自带的 template_code 骨架与 patch_suggestion / migration_guide 说明"
                "写入目标项目的 coderef-replicate-apply 目录，并生成落地清单 manifest。\n"
                "诚实话护栏：只落地'确定性可给'的内容（template_code 骨架、说明文档），"
                "不自动接入目标源码；默认不覆盖已存在的同名文件（冲突时如实标注，"
                "overwrite=true 才允许覆盖）；template_code 缺失会明确标注待补全。"
            ),
            "inputSchema": {"type": "object", "properties": {
                "project_path": {"type": "string", "description": "触发调用的项目路径（用于解析资产）"},
                "canonical": {"type": "string", "description": "要落地的已固化资产 canonical（或别名）"},
                "target": {"type": "string", "description": "目标项目路径（默认 = project_path 对应项目根）"},
                "filename": {"type": "string", "description": "落地文件名（默认取 template_code 标题或 replicate_template.py；可含子路径）"},
                "overwrite": {"type": "boolean", "description": "是否允许覆盖已存在的同名文件（默认 False，冲突时如实标注）", "default": False},
                "background": {"type": "boolean", "description": "后台执行（重型工具默认后台，返回 task_id 用 coderef_task_status 查询）", "default": True},
            }, "required": ["project_path", "canonical"]},
        })
        self._tools.append({
            "name": "coderef_asset_blueprint",
            "description": (
                "把复刻铺排（coderef_replicate）得出的确定性结论写回资产蓝图。\n"
                "仅写回确定性可填字段（entry_points / verified_findings 若空），不臆断 steps。\n"
                "供对方 AI 确认铺排有效后调用，把蓝图从骨架补全为可复刻蓝图。"
            ),
            "inputSchema": {"type": "object", "properties": {
                "project_path": {"type": "string", "description": "目标项目路径"},
                "canonical": {"type": "string", "description": "要补全蓝图的资产 canonical（或别名）"},
                "entry_points": {"type": "array", "items": {"type": "string"}, "description": "要写入蓝图的可信入口符号列表"},
                "background": {"type": "boolean", "description": "后台执行（重型工具默认后台，返回 task_id 用 coderef_task_status 查询）", "default": True},
            }, "required": ["project_path", "canonical"]},
        })
        self._tools.append({
            "name": "coderef_registry",
            "description": (
                "管理已知设计库（DesignRegistry）。\n"
                "action=list 列出已知设计；add 新增 canonical 设计；alias 把别名归一到 canonical（解决 LLM 命名漂移）。"
            ),
            "inputSchema": {"type": "object", "properties": {
                "project_path": {"type": "string", "description": "目标项目路径"},
                "action": {"type": "string", "enum": ["list","add","alias"], "default": "list"},
                "name": {"type": "string", "description": "设计名/别名"},
                "canonical": {"type": "string", "description": "规范设计名（add/alias 用）"},
                "alias": {"type": "string", "description": "要归一化的别名（alias 用）"},
                "description": {"type": "string", "description": "设计说明（add 用）"},
            }, "required": ["project_path"]},
        })
        self._tools.append({
            "name": "coderef_innovation_review",
            "description": (
                "创新复刻的 LLM 协助排查（4.7 收口）：让 LLM 阅读源项目的管线设计（知识图谱调用链）"
                "+ wiki 文档，对『创新确认』与『复刻排查』给出 AI 判断。\n"
                "判定三点：(1) 该设计是否确属一个创新 workflow（区别于已知/常见模式或静态能力标签误命中）；"
                "(2) 管线调用链与 wiki 人话描述是否一致；(3) 复刻到目标项目是否合理（提供 target 时）。\n"
                "wiki 来源『生成+兜底』：优先读已有，无则自动生成再排查。\n"
                "诚实话护栏：确定性管线摘要照常给出；LLM 结论为 AI 意见而非确定性事实，不下『必须复刻』指令；"
                "无 API Key 时硬阻断（只给确定性管线摘要，不产出降级判断）。"
            ),
            "inputSchema": {"type": "object", "properties": {
                "project_path": {"type": "string", "description": "源项目路径（创新所在项目）"},
                "canonical": {"type": "string", "description": "要复查的创新设计（workflow 名或资产 canonical/别名）"},
                "target": {"type": "string", "description": "目标项目路径（可选；提供时追加复刻合理性排查）"},
                "out_format": {"type": "string", "enum": ["json","text","html"], "default": "json"},
                "background": {"type": "boolean", "description": "后台执行（重型工具默认后台，返回 task_id 用 coderef_task_status 查询）", "default": True},
            }, "required": ["project_path", "canonical"]},
        })
        self._tools.append({
            "name": "coderef_prompt_governance",
            "description": (
                "Prompt 治理平台：一次调用编排 资产生命周期 × 合规审计 × 跨模块一致性。\n"
                "4.6 已合并原 coderef_prompt_mgmt 与 coderef_prompt_audit 的全部能力，"
                "本工具是 Prompt 治理的唯一入口。\n"
                "action=overview → 治理总览（资产清单 + 生效版本 + 合规审计 + 跨模块漂移，一屏看清 Prompt 资产健不健康）；\n"
                "action=assets → 资产生命周期（asset_action=list 查清单 / version 登记新版本 / "
                "compare 多版本评分 / abtest 下发 A/B 组并择优晋升；name+content+version+abtest_group 透传）；\n"
                "action=audit → 合规审计（注入风险 + 一致性；out_format=json/text/html 指定输出）；\n"
                "action=cross_module → 跨模块一致性专项（同一角色/场景在多模块的同名定义漂移）。\n"
                "诚实话护栏：纯编排 + 确定性规则，不引入 LLM；各维度如实标注是否已执行，"
                "不把'未审计'渲染成'无风险'；跨模块漂移是风险提示而非已发生故障。"
            ),
            "inputSchema": {"type": "object", "properties": {
                "project_path": {"type": "string", "description": "目标项目路径"},
                "action": {"type": "string", "enum": ["overview","assets","audit","cross_module"], "default": "overview"},
                "name": {"type": "string", "description": "资产名（assets veraction/compare/abtest 用）"},
                "content": {"type": "string", "description": "资产内容（assets version/abtest 登记用）"},
                "version": {"type": "string", "description": "版本号（assets version/compare/abtest 用）"},
                "abtest_group": {"type": "string", "description": "A/B 分组（assets abtest 用）"},
                "asset_action": {"type": "string", "enum": ["list","version","compare","abtest"], "description": "资产生命周期子动作（action=assets 时用；缺省按 name/version 自动判定 list 或 version）"},
                "out_format": {"type": "string", "enum": ["json","text","html"], "default": "json", "description": "合规审计输出格式（action=audit 用）"},
                "background": {"type": "boolean", "description": "后台执行（重型工具默认后台，返回 task_id 用 coderef_task_status 查询）", "default": True},
            }, "required": ["project_path"]},
        })
        self._tools.append({
            "name": "coderef_interpret",
            "description": (
                "人话解读平台：让非编程人员一屏看懂 AI 项目的真实状态。\n"
                "action=health → 健康总览（确定性人话：健康分 + 高危清单 + 图谱/合规背景；未审计时诚实提示不给分）；\n"
                "action=dashboard → 生成健康仪表盘 HTML（非编程人员可读）；\n"
                "action=wiki → 生成 Wiki 人话文档（依赖 LLM，无 API Key 时诚实阻断）；\n"
                "action=prompt → Prompt 治理总览；\n"
                "action=assets → 人话解读已固化创新资产。\n"
                "4.6 收敛：论断核验动作 verify/verify_html 已移除，统一走 coderef_verify_findings。\n"
                "诚实话护栏：人话结论全部来自确定性原语，不引入 LLM 给结论；"
                "健康分只在确实审计过时给出，未审计绝不臆断；依赖 LLM 的能力无 Key 时诚实阻断。"
            ),
            "inputSchema": {"type": "object", "properties": {
                "project_path": {"type": "string", "description": "目标项目路径"},
                "action": {"type": "string", "enum": ["health","dashboard","wiki","prompt","assets"], "default": "health"},
                "out_format": {"type": "string", "enum": ["json","text","html"], "default": "json"},
                "background": {"type": "boolean", "description": "后台执行（重型工具默认后台，返回 task_id 用 coderef_task_status 查询）", "default": True},
            }, "required": ["project_path"]},
        })
        self._tasks: Dict[str, Any] = {}
        # 并发保护：多 Agent 后台任务可能同时读写 _tasks，用可重入锁保证一致性
        self._lock = threading.RLock()

        # ─── 统一工具分发映射 ─────────────────────────────────────────
        # 工具名 -> handler。收敛 _call 里散落的 if/elif，让所有工具都能复用统一的
        # background 后台化逻辑。audit/docs 的 handler 额外接收 progress_cb 上报阶段进度。
        self._handlers = {
            "coderef_whitelist": self._wl,
            "coderef_architecture": self._arch,
            "coderef_docs_read": self._docs_read,
            "coderef_query": self._query,
            "coderef_review": self._review,
            "coderef_frontend": self._frontend,
            "coderef_report": self._report,
            "coderef_audit_advisor": self._advisor,
            "coderef_scan": self._scan_tool,
            "coderef_scan_list": lambda a: self._scan_list(),
            "coderef_flow_verify": self._flow_verify,
            "coderef_arch_audit": self._arch_audit,
            "coderef_verify_findings": self._verify_findings,
            "coderef_change_guard": self._change_guard,
            "coderef_change_report": self._change_report,
            "coderef_memory_sync": self._memory_sync,
            "coderef_memory_query": self._memory_query,
            "coderef_memory_status": self._memory_status,
            "coderef_memory_quality": self._memory_quality,
            "coderef_operation_memory_sync": self._operation_memory_sync,
            "coderef_operation_memory_query": self._operation_memory_query,
            "coderef_operation_memory_find": self._operation_memory_find,
            "coderef_operation_memory_status": self._operation_memory_status,
            # 4.6 兼容层：coderef_prompt_mgmt / coderef_prompt_audit 已从 tools/list 移除
            #（收敛到 coderef_prompt_governance 唯一入口），此处保留 handler 供旧调用向后兼容转发。
            "coderef_prompt_mgmt": self._prompt_mgmt,
            "coderef_prompt_audit": self._prompt_audit,
            "coderef_owasp": self._owasp,
            "coderef_innovation": self._innovation,
            "coderef_asset": self._asset,
            "coderef_replicate": self._replicate,
            "coderef_replicate_apply": self._replicate_apply,
            "coderef_asset_blueprint": self._asset_blueprint,
            "coderef_innovation_review": self._innovation_review,
            "coderef_registry": self._registry,
            "coderef_prompt_governance": self._govern,
            "coderef_interpret": self._interpret,
        }

        # ─── 重型工具：默认后台执行 ───────────────────────────────────
        # 解决 MCP 宿主层（Trae / Claude Desktop / Cursor 等任意客户端）对单次
        # tools/call 的超时限制：大项目同步全量必然超时（如 memory_sync 全量扫描
        # Python+Vue+Go，曾触发 Trae 的 REQUEST_TIMEOUT）。后台化后调用立即返回
        # running + task_id，由外层 AI 轮询 coderef_task_status 取结果，不再撞超时。
        # 轻量工具（query / scan_list / whitelist / docs_read 等）不在此列，保持同步
        # 快速返回。调用方可用 background=false 强制同步（小项目想立即拿结果时）。
        self.HEAVY_TOOLS = {
            "coderef_audit", "coderef_docs", "coderef_review", "coderef_frontend",
            "coderef_report", "coderef_audit_advisor", "coderef_architecture",
            "coderef_memory_sync", "coderef_memory_quality", "coderef_memory_status",
            "coderef_operation_memory_sync",
            "coderef_owasp",
            "coderef_innovation", "coderef_asset", "coderef_change_guard",
            "coderef_change_report", "coderef_verify_findings",
            "coderef_replicate", "coderef_replicate_apply", "coderef_asset_blueprint",
            "coderef_innovation_review",
            "coderef_prompt_governance", "coderef_interpret",
            # coderef_scan 单维度在超大项目上也会全量盘点（如 gov 全项目治理审计，
            # 真实案例 目标项目 跑 131.5s 撞 rpc 层单次调用超时）。加入重工具集使其
            # 默认后台执行，单次 tools/call 立即返回 task_id，避免同步撞超时。
            "coderef_scan",
        }

    def _should_background(self, n: str, a: Dict) -> bool:
        """决定工具是否后台执行：
        - background=true 显式要求 → 后台
        - background=false 显式要求 → 同步
        - 未指定 → 重型工具默认后台，轻量工具同步
        """
        explicit = a.get("background")
        if explicit is True:
            return True
        if explicit is False:
            return False
        return n in self.HEAVY_TOOLS

    @contextmanager
    def _locked_tasks(self):
        """加锁访问运行中的任务状态字典，保证并发下读写一致。

        用法: with self._locked_tasks() as tasks: ... 
        所有对 self._tasks 的读写都通过该访问器完成，避免散落裸锁。
        """
        with self._lock:
            yield self._tasks

    def _evict_finished_tasks(self, tasks: Dict[str, Any], max_age: int = 3600, max_size: int = 50):
        """清除已完成且过期的后台任务，避免 _tasks 无限增长。

        策略：1) 超过 max_age 秒的已完成任务直接清除；
              2) 已完成任务数超过 max_size 时，按 finished_at 从旧到新清除。
        """
        import time
        now = time.time()
        # 清除超时的已完成任务
        expired = [
            tid for tid, t in tasks.items()
            if t.get("finished_at") is not None and now - t["finished_at"] > max_age
        ]
        for tid in expired:
            del tasks[tid]
        # 如果已完成任务仍过多，按时间从旧到新清除
        finished = sorted(
            [(tid, t) for tid, t in tasks.items() if t.get("finished_at") is not None],
            key=lambda x: x[1]["finished_at"],
        )
        while len(finished) > max_size:
            tid, _ = finished.pop(0)
            if tid in tasks:
                del tasks[tid]

    # ─── request ───

    def _handle(self, req: Dict) -> Dict:
        m, rid = req.get("method",""), req.get("id")
        if m == "initialize":
            return {"jsonrpc":"2.0","id":rid,"result":{
                "protocolVersion":"2024-11-05","capabilities":{"tools":{}},
                "serverInfo":{
                    "name":"coderef-ai","version":PKG_VERSION,
                    "reliability": RELIABILITY_GUIDE,
                }}}
        if m == "notifications/initialized": return None
        if m == "tools/list":
            # 在返回工具描述时追加可靠性/边界提示，供外层 LLM 选工具时判断
            tools = []
            for t in self._tools:
                note = TOOL_BOUNDARY_NOTES.get(t["name"], "")
                if note:
                    t = dict(t)
                    t["description"] = (t.get("description", "") + note).strip()
                tools.append(t)
            return {"jsonrpc":"2.0","id":rid,"result":{"tools":tools}}
        if m == "tools/call":
            return self._call(rid, req.get("params",{}))
        return {"jsonrpc":"2.0","id":rid,"error":{"code":-32601,"message":f"未知: {m}"}}

    def _call(self, rid, params):
        n, a = params.get("name",""), params.get("arguments",{})
        try:
            # 状态查询永远同步返回，不后台化
            if n == "coderef_task_status":
                return self._ok(rid, self._tsk(a))
            # 未知工具提前拦截，避免落入 _run 才报"未知"
            if n not in self._handlers and n not in ("coderef_audit", "coderef_docs"):
                return {"jsonrpc":"2.0","id":rid,"error":{"code":-32602,"message":f"未知工具: {n}"}}
            # 统一后台化决策：重型工具默认后台，轻量工具同步；background 显式参数可覆盖
            if self._should_background(n, a):
                tid = str(uuid.uuid4())[:8]; rc = {}
                t = threading.Thread(target=lambda: self._bg(rc, n, a), daemon=True)
                t.start()
                with self._locked_tasks() as tasks:
                    tasks[tid] = {"thread":t,"result":rc,"tool":n,
                                  "started_at":time.time()}
                logger.info(f"后台: {tid} {n}")
                return self._ok(rid, json.dumps({"status":"running","task_id":tid,
                    "message":f"已启动。coderef_task_status(task_id='{tid}') 查询进度"}, ensure_ascii=False))
            return self._ok(rid, self._run(n, a))
        except Exception as e:
            return {"jsonrpc":"2.0","id":rid,"error":{"code":-32000,"message":str(e)}}

    def _bg(self, rc, n, a):
        try:
            # progress 回调：每个阶段完成后写入共享 rc，_tsk 据此回传进度
            def prog(stage, done, total, detail=None):
                rc["progress"] = {"stage": stage, "done": done, "total": total, "detail": detail}
            rc["result"] = self._run(n, a, progress_cb=prog)
        except Exception as e: rc["error"] = str(e); rc["tb"] = traceback.format_exc()

    def _scan_tool(self, a: dict) -> str:
        """运行单个审计维度（coderef_scan），返回结构化 JSON findings。

        实时安全带：只跑一个维度（不建图谱/不生成 dashboard），快速返回，
        供 AI 在写完一个模块后即时自查 / 作为客观第二意见。
        """
        from core.pipeline_runner import Pipe
        tool = a.get("tool", "")
        # P2-2：工具维度名严格白名单校验（大小写敏感）。schema enum 为精确匹配，
        # 运行时不再借 run_single 的 .lower() 容错，避免 "Gov" 等大小写混写被静默
        # 放行执行；非法维度返回结构化错误而非空成功。
        valid = {k for k, _ in self._SINGLE_TOOL_LABELS}
        if tool not in valid:
            raise ValueError(
                f"coderef_scan: 未知工具维度 '{tool}'，支持(大小写敏感): "
                f"{', '.join(sorted(valid))}")
        pp = a["project_path"]
        r = Pipe().run_single(pp, tool)
        return json.dumps({
            "status": "completed",
            "tool": "coderef_scan",
            "dimension": tool,
            "project_path": pp,
            "findings": [
                {
                    "id": f.id, "category": f.category, "severity": f.severity,
                    "tier": f.tier.value, "file": f.file_path, "line": f.line,
                    "line_label": f.line_label, "title": f.title,
                    "detail": f.detail, "suggestion": f.suggestion,
                    "xval_by": f.xval_by,
                }
                for f in r.findings
            ],
            "summary": {
                "total_files": r.total_files, "total_lines": r.total_lines,
                "findings": len(r.findings),
                "high": sum(1 for f in r.findings if f.tier.value == "high"),
                "medium": sum(1 for f in r.findings if f.tier.value == "medium"),
                "low": sum(1 for f in r.findings if f.tier.value == "low"),
                "elapsed": r.elapsed,
            },
            "errors": r.errors,
        }, ensure_ascii=False)

    def _scan_list(self) -> str:
        """列出 coderef_scan 可选维度清单"""
        from core.pipeline_runner import Pipe
        return json.dumps({
            "status": "completed",
            "tool": "coderef_scan_list",
            "dimensions": Pipe.list_single_tools(),
        }, ensure_ascii=False)

    def _change_guard(self, a: dict) -> str:
        """运行 AI 代码退化检测 / 守护 git 基层管理（coderef_change_guard）"""
        from core.change_guard import ChangeGuard
        pp = a["project_path"]
        action = a.get("action") or "guard"
        timeout = a.get("git_timeout")
        git_bin = a.get("git_bin") or None
        cg = ChangeGuard()
        if action == "ensure_git":
            r = cg.ensure_git_repo(pp, git_timeout=timeout, git_bin=git_bin)
        elif action == "anchor":
            r = cg.anchor_health_baseline(
                pp, label=a.get("label"), git_timeout=timeout,
                allow_autocommit=a.get("allow_autocommit", True), git_bin=git_bin)
        elif action == "list_baselines":
            r = {"ok": True, "baselines": cg.list_health_baselines(
                pp, git_timeout=timeout, git_bin=git_bin)}
        else:
            diff = a.get("diff") or None
            baseline = a.get("baseline_dir") or None
            r = cg.guard(pp, diff=diff, baseline_dir=baseline,
                         git_timeout=timeout, git_bin=git_bin)
        r["tool"] = "coderef_change_guard"
        r["project_path"] = pp
        r["action"] = action
        return json.dumps(r, ensure_ascii=False)

    def _change_report(self, a: dict) -> str:
        """生成人话版变更报告（coderef_change_report）"""
        from core.change_report import ChangeReport
        pp = a["project_path"]
        diff = a.get("diff") or ""
        r = ChangeReport().report(pp, diff)
        r["tool"] = "coderef_change_report"
        r["project_path"] = pp
        return json.dumps(r, ensure_ascii=False)

    # ── 引擎一 · 记忆层 ─────────────────────────────────────────────
    def _memory_sync(self, a: dict) -> str:
        """初始化/增量同步项目记忆层（coderef_memory_sync）"""
        from core.memory_layer import memory_layer
        pp = a["project_path"]
        mode = a.get("mode", "full")
        r = memory_layer.sync(pp, mode=mode)
        r["tool"] = "coderef_memory_sync"
        r["project_path"] = pp
        return json.dumps(r, ensure_ascii=False)

    def _memory_query(self, a: dict) -> str:
        """供 AI 助手复用项目记忆（coderef_memory_query）"""
        from core.memory_layer import memory_layer
        pp = a["project_path"]
        qt = a.get("query_type", "semantic")
        kwargs = {k: v for k, v in a.items()
                  if k not in ("project_path", "query_type") and v}
        r = memory_layer.query(pp, query_type=qt, **kwargs)
        r["tool"] = "coderef_memory_query"
        r["project_path"] = pp
        return json.dumps(r, ensure_ascii=False)

    def _memory_status(self, a: dict) -> str:
        """「AI 知道什么」认知地图（coderef_memory_status）"""
        from core.memory_layer import memory_layer
        pp = a["project_path"]
        r = memory_layer.status(pp)
        r["tool"] = "coderef_memory_status"
        r["project_path"] = pp
        return json.dumps(r, ensure_ascii=False)

    def _memory_quality(self, a: dict) -> str:
        """记忆质量评估 + 自动补全（coderef_memory_quality）"""
        from core.memory_quality import MemoryQuality
        pp = a["project_path"]
        auto_fix = a.get("auto_fix", False)
        r = MemoryQuality().assess(pp, auto_fix=auto_fix)
        r["tool"] = "coderef_memory_quality"
        r["project_path"] = pp
        return json.dumps(r, ensure_ascii=False)

    def _operation_memory_sync(self, a: dict) -> str:
        """初始化/增量同步操作记忆层（coderef_operation_memory_sync）"""
        from core.operation_memory import operation_memory
        pp = a["project_path"]
        mode = a.get("mode", "full")
        with_llm = a.get("with_llm", True)
        r = operation_memory.sync(pp, mode=mode, with_llm=with_llm)
        r["tool"] = "coderef_operation_memory_sync"
        r["project_path"] = pp
        return json.dumps(r, ensure_ascii=False)

    def _operation_memory_query(self, a: dict) -> str:
        """按类别检索操作记忆（coderef_operation_memory_query）"""
        from core.operation_memory import operation_memory
        pp = a["project_path"]
        qt = a.get("query_type", "all")
        kw = a.get("keyword", "")
        limit = a.get("limit", 10)
        r = operation_memory.query(pp, query_type=qt, keyword=kw, limit=limit)
        r["tool"] = "coderef_operation_memory_query"
        r["project_path"] = pp
        return json.dumps(r, ensure_ascii=False)

    def _operation_memory_find(self, a: dict) -> str:
        """定位资源（coderef_operation_memory_find）"""
        from core.operation_memory import operation_memory
        pp = a["project_path"]
        name = a.get("name", "")
        limit = a.get("limit", 5)
        r = operation_memory.find(pp, name=name, limit=limit)
        r["tool"] = "coderef_operation_memory_find"
        r["project_path"] = pp
        return json.dumps(r, ensure_ascii=False)

    def _operation_memory_status(self, a: dict) -> str:
        """操作记忆健康状态（coderef_operation_memory_status）"""
        from core.operation_memory import operation_memory
        pp = a["project_path"]
        r = operation_memory.status(pp)
        r["tool"] = "coderef_operation_memory_status"
        r["project_path"] = pp
        return json.dumps(r, ensure_ascii=False)

    def _prompt_mgmt(self, a: dict) -> str:
        """Prompt 资产管理（兼容层：4.6 已并入 coderef_prompt_governance，此处仅做转发）"""
        from core.prompt_governance import govern_prompt
        pp = a["project_path"]
        r = govern_prompt(
            pp,
            action="assets",
            name=a.get("name", ""),
            content=a.get("content", ""),
            version=a.get("version", ""),
            abtest_group=a.get("abtest_group", ""),
            asset_action=a.get("action", ""),
        )
        r["tool"] = "coderef_prompt_mgmt"
        r["deprecated"] = True
        r["migrate_to"] = "coderef_prompt_governance"
        r["project_path"] = pp
        return json.dumps(r, ensure_ascii=False)

    def _prompt_audit(self, a: dict) -> str:
        """确定性 Prompt 合规审计（兼容层：4.6 已并入 coderef_prompt_governance，此处仅做转发）"""
        from core.prompt_governance import govern_prompt
        pp = a["project_path"]
        r = govern_prompt(
            pp,
            action="audit",
            out_format=a.get("out_format", "json"),
        )
        r["tool"] = "coderef_prompt_audit"
        r["deprecated"] = True
        r["migrate_to"] = "coderef_prompt_governance"
        r["project_path"] = pp
        return json.dumps(r, ensure_ascii=False)

    # ── 引擎三 · OWASP 合规 ────────────────────────────────────────
    def _owasp(self, a: dict) -> str:
        """OWASP LLM Top 10 合规检测（coderef_owasp）"""
        from core.owasp_compliance import OWASPCompliance
        pp = a["project_path"]
        out_format = a.get("out_format", "json")
        r = OWASPCompliance().check(pp, out_format=out_format)
        r["tool"] = "coderef_owasp"
        r["project_path"] = pp
        return json.dumps(r, ensure_ascii=False)

    # ── 引擎二 · 创新识别 + 资产沉淀 ───────────────────────────────
    def _innovation(self, a: dict) -> str:
        """识别项目创新设计 + 传播缺口（coderef_innovation）"""
        from core.innovation_engine import InnovationEngine
        pp = a["project_path"]
        r = InnovationEngine().detect(
            pp, intent=a.get("intent", ""),
            min_adoption=a.get("min_adoption", 0.0),
        )
        r["tool"] = "coderef_innovation"
        r["project_path"] = pp
        return json.dumps(r, ensure_ascii=False)

    def _asset(self, a: dict) -> str:
        """WorkflowAsset 资产化/查询/导出（coderef_asset）"""
        from core.innovation_engine import InnovationEngine
        pp = a["project_path"]
        r = InnovationEngine().asset(
            pp, action=a.get("action", "list"), canonical=a.get("canonical", ""),
            description=a.get("description", ""), template_code=a.get("template_code", ""),
            patch_suggestion=a.get("patch_suggestion", ""),
            migration_guide=a.get("migration_guide", ""),
            blueprint=a.get("blueprint"),
        )
        r["tool"] = "coderef_asset"
        r["project_path"] = pp
        return json.dumps(r, ensure_ascii=False)

    def _registry(self, a: dict) -> str:
        """管理已知设计库（coderef_registry）"""
        from core.design_registry import DesignRegistry
        pp = a["project_path"]
        r = DesignRegistry().manage(
            pp, action=a.get("action", "list"), name=a.get("name", ""),
            canonical=a.get("canonical", ""), alias=a.get("alias", ""),
            description=a.get("description", ""),
        )
        r["tool"] = "coderef_registry"
        r["project_path"] = pp
        return json.dumps(r, ensure_ascii=False)

    def _replicate(self, a: dict) -> str:
        """复刻铺排：检测目标项目缺口 + 生成复刻指引（coderef_replicate）"""
        from core.replicate_engine import replicate_design, render_report, render_html
        pp = a["project_path"]
        out_format = a.get("out_format", "json")
        r = replicate_design(
            pp, a["canonical"],
            verify_symbols=a.get("verify_symbols", True),
        )
        r["tool"] = "coderef_replicate"
        r["project_path"] = pp
        if out_format == "html":
            r["report_html"] = render_html(r)
        elif out_format == "text":
            r["report_text"] = render_report(r)
        return json.dumps(r, ensure_ascii=False)

    def _replicate_apply(self, a: dict) -> str:
        """复刻落地：把已固化资产的复刻指引落到目标项目（coderef_replicate_apply）"""
        from core.replicate_engine import apply_replicate
        pp = a["project_path"]
        # overwrite 必须是真正的布尔：只接受 True/False，拒绝 "false"/"0" 等被 bool() 误转成 True 的输入
        raw_overwrite = a.get("overwrite", False)
        if raw_overwrite is not True and raw_overwrite is not False:
            return json.dumps({
                "ok": False,
                "tool": "coderef_replicate_apply",
                "project_path": pp,
                "error": f"overwrite 必须是布尔值（True/False），收到 {raw_overwrite!r}（{type(raw_overwrite).__name__}）。",
                "summary": "overwrite 参数类型非法，已拒绝执行。",
            }, ensure_ascii=False)
        r = apply_replicate(
            pp, a["canonical"],
            target=a.get("target", ""),
            filename=a.get("filename", ""),
            overwrite=raw_overwrite,
        )
        r["tool"] = "coderef_replicate_apply"
        r["project_path"] = pp
        return json.dumps(r, ensure_ascii=False)

    def _innovation_review(self, a: dict) -> str:
        """创新复刻排查：LLM 阅读管线设计 + wiki，判定创新与复刻合理性（coderef_innovation_review）"""
        from core.innovation_review import review_innovation, render_report, render_html
        pp = a["project_path"]
        out_format = a.get("out_format", "json")
        r = review_innovation(
            pp, a["canonical"],
            target=a.get("target", ""),
            out_format=out_format,
        )
        r["tool"] = "coderef_innovation_review"
        r["project_path"] = pp
        if out_format == "html":
            r["report_html"] = render_html(r)
        elif out_format == "text":
            r["report_text"] = render_report(r)
        return json.dumps(r, ensure_ascii=False)

    def _asset_blueprint(self, a: dict) -> str:
        """把复刻铺排结论写回资产蓝图（coderef_asset_blueprint）"""
        from core.replicate_engine import solidify_asset_blueprint
        pp = a["project_path"]
        entry_points = a.get("entry_points") or []
        if isinstance(entry_points, str):
            import re as _re
            entry_points = [s.strip() for s in _re.split(r"[,\s;]+", entry_points) if s.strip()]
        r = solidify_asset_blueprint(pp, a["canonical"], entry_points=list(entry_points))
        r["tool"] = "coderef_asset_blueprint"
        r["project_path"] = pp
        return json.dumps(r, ensure_ascii=False)

    def _govern(self, a: dict) -> str:
        """Prompt 治理平台：编排资产生命周期 × 合规审计 × 跨模块一致性（coderef_prompt_governance）"""
        from core.prompt_governance import govern_prompt
        pp = a["project_path"]
        r = govern_prompt(
            pp,
            action=a.get("action", "overview"),
            name=a.get("name", ""),
            content=a.get("content", ""),
            version=a.get("version", ""),
            abtest_group=a.get("abtest_group", ""),
            asset_action=a.get("asset_action", ""),
            out_format=a.get("out_format", "json"),
        )
        r["tool"] = "coderef_prompt_governance"
        r["project_path"] = pp
        return json.dumps(r, ensure_ascii=False)

    def _interpret(self, a: dict) -> str:
        """人话解读平台：健康/仪表盘/论断核验/Wiki/Prompt 治理/资产（coderef_interpret）"""
        from core.interpretation_platform import interpret_project
        pp = a["project_path"]
        r = interpret_project(
            pp,
            action=a.get("action", "health"),
            findings_text=a.get("findings_text", ""),
            entry=a.get("entry", ""),
            out_format=a.get("out_format", "json"),
        )
        r["tool"] = "coderef_interpret"
        r["project_path"] = pp
        return json.dumps(r, ensure_ascii=False)

    def _docs_read(self, a: dict) -> str:
        """按需读取 Wiki 文档正文（coderef_docs_read）"""
        from core.pipeline_runner import Pipe
        pp = a["project_path"]
        # max_chars 防御性转换：非法值回退默认，避免 MCP 层抛 ValueError
        try:
            max_chars = int(a.get("max_chars", 20000))
        except (TypeError, ValueError):
            max_chars = 20000
        r = Pipe().docs_read(
            pp, doc=a.get("doc") or None,
            output_dir=a.get("output_dir") or None,
            max_chars=max_chars,
        )
        r["tool"] = "coderef_docs_read"
        r["project_path"] = pp
        return json.dumps(r, ensure_ascii=False)

    def _flow_verify(self, a: dict) -> str:
        """流程合规验证（coderef_flow_verify）"""
        from core.flow_verify import verify_flow
        pp = a["project_path"]
        # steps 校验：只在调用方显式提供 steps 键时做非空/类型校验。
        # cross_lang 等内部维度复用本工具时只传 project_path+entry（不传 steps 键），
        # 仅取 cross_lang_contract 等不依赖流程步骤的结果 → 放行为空。
        # 而显式传 steps=[]/0/None/空串 属契约非法，返回结构化错误而非假成功。
        if "steps" in a:
            steps = a["steps"]
            if isinstance(steps, str):
                # 兼容逗号分隔字符串
                steps = [s.strip() for s in steps.split(",") if s.strip()]
            elif not isinstance(steps, (list, tuple)):
                # 非数组/非字符串（如数字 0、负数、None）→ 结构化错误
                raise ValueError(
                    f"coderef_flow_verify: steps 必须是数组或逗号分隔字符串，收到 {type(steps).__name__}")
            if not steps:
                # 空数组/0/空串清空后 → 无待验证步骤，契约非法
                raise ValueError("coderef_flow_verify: steps 不能为空，请传入待验证的流程步骤")
        else:
            steps = []
        r = verify_flow(pp, a["entry"], list(steps), depth=a.get("depth"))
        r["tool"] = "coderef_flow_verify"
        r["project_path"] = pp
        return json.dumps(r, ensure_ascii=False)

    def _arch_audit(self, a: dict) -> str:
        """架构腐化诊断（coderef_arch_audit）—— 复用知识图谱 CALLS 边做模块级静态诊断"""
        from core.arch_audit import audit as arch_audit
        pp = a["project_path"]
        r = arch_audit(pp)
        r["tool"] = "coderef_arch_audit"
        r["project_path"] = pp
        return json.dumps(r, ensure_ascii=False)

    def _verify_findings(self, a: dict) -> str:
        """确定性核验 LLM / CodeRabbit 论断（coderef_verify_findings）"""
        from core.verify_findings import verify_findings, render_report, render_html
        pp = a["project_path"]
        findings = a.get("findings") or []
        if isinstance(findings, str):
            findings = json.loads(findings)
        entry = a.get("entry") or None
        out_format = a.get("out_format", "json")
        r = verify_findings(pp, list(findings), entry=entry)
        r["tool"] = "coderef_verify_findings"
        r["project_path"] = pp
        if out_format == "html":
            r["report_html"] = render_html(r)
        elif out_format == "text":
            r["report_text"] = render_report(r)
        return json.dumps(r, ensure_ascii=False)

    @staticmethod
    def _validate_project_path(tool: str, p: str) -> str:
        """校验并规范化 project_path。

        规则：
        1. 空串/纯空白/缺省 → 结构化错误（此前缺省键 audit 仍完成并生成报告，属假阴性）
        2. 相对路径（含 .. / . / 纯文件名）→ 拒绝，要求绝对路径，
           避免 ".." 越权扫描上级目录、空串被当作 cwd 扫描被测源码自身
        3. 绝对路径但目录不存在/不是目录 → 结构化错误（此前静默返回空成功）
        返回规范化后的绝对路径（realpath）。
        """
        if not p or not p.strip():
            raise ValueError(
                f"{tool}: project_path 不能为空或缺失，请传入目标项目的绝对路径")
        p = p.strip()
        if not os.path.isabs(p):
            raise ValueError(
                f"{tool}: project_path 必须是绝对路径（收到相对路径 '{p}'）。"
                f"相对路径（如 .. / 空串）会被解析到非预期目录，已拒绝以防越权扫描")
        real = os.path.realpath(p)
        if not os.path.isdir(real):
            raise ValueError(f"{tool}: project_path 目录不存在: {p}")
        return real

    def _run(self, n, a, progress_cb=None) -> str:
        from core.pipeline_runner import Pipe
        # 部分工具（如 coderef_scan_list）不依赖 project_path，用容错读取避免误抛 KeyError
        p, o = a.get("project_path", ""), a.get("output_dir")
        # P1高-3：project_path 校验与规范化（coderef_scan_list 不依赖路径，跳过）。
        # 无效路径返回结构化错误而非空成功；相对路径（../空串）拒绝，禁止越权扫描。
        if n != "coderef_scan_list":
            p = self._validate_project_path(n, p)
            a["project_path"] = p
        logger.info(f"[{n}] {p}")
        if n == "coderef_audit":
            strat = a.get("strategy", "auto")
            # P2-2：审计策略严格枚举校验（大小写敏感）。非法策略此前静默按默认
            # (full) 执行，调用方无法区分显式传错与未指定；现改为结构化错误。
            if strat not in ("auto", "full", "incr", "no_change"):
                raise ValueError(
                    f"coderef_audit: 未知审计策略 '{strat}'，支持(大小写敏感): "
                    f"auto/full/incr/no_change")
            strat = None if strat == "auto" else strat
            r = Pipe().audit(p, output_dir=o, progress_cb=progress_cb,
                             strategy=strat)
            d = getattr(r, 'dashboard_path', '')
            # 结构化返回：携带落盘路径与明细统计，避免调用方只看得到摘要
            return json.dumps({
                "status": "completed",
                "tool": n,
                "project_path": p,
                "report": r.report,
                "report_path": r.report_path or "",
                "dashboard_path": d or "",
                "strategy": getattr(r, "audit_strategy", "full"),
                "evidence": {
                    "scan_ts": getattr(r, "scan_ts", ""),          # 本次扫描时间戳
                    "kg_built_at": getattr(r, "kg_built_at", ""),   # 知识图谱构建时间
                    "file_snapshot": getattr(r, "file_snapshot", {}),  # 本次扫描文件快照
                    "note": "evidence.scan_ts 为本次审计实际扫描时间；kg_built_at 为知识图谱构建时间，两者不一致时说明图谱可能滞后于代码，查询类结果请以重建后为准。统计口径仅覆盖 file_snapshot 所列文件，不代表修复状态。",
                },
                "summary": {
                    "total_files": getattr(r, "total_files", 0),
                    "total_lines": getattr(r, "total_lines", 0),
                    "findings": len(r.findings),
                    "high": sum(1 for f in r.findings if f.tier.value == "high"),
                    "medium": sum(1 for f in r.findings if f.tier.value == "medium"),
                    "low": sum(1 for f in r.findings if f.tier.value == "low"),
                    "elapsed": getattr(r, "elapsed", 0),
                },
                "errors": getattr(r, "errors", []),
            }, ensure_ascii=False)
        elif n == "coderef_docs":
            r = Pipe().docs(p, output_dir=o,
                            wiki_style=a.get("wiki_style") or "comprehensive",
                            include_subprojects=True if a.get("include_subprojects", True) else False)
            wr = getattr(r, "wiki_result", None)
            # 结构化返回：携带输出目录/文档清单/失败明细，让调用方能区分"全量成功"与"部分失败"，
            # 避免部分文档生成失败时仍被当作 fully completed。
            return json.dumps({
                "status": "completed" if not getattr(r, "errors", []) else "partial_failed",
                "tool": n,
                "project_path": p,
                "report": getattr(r, "report", ""),
                "errors": getattr(r, "errors", []),
                "wiki": {
                    "output_dir": getattr(wr, "output_dir", "") if wr else "",
                    "documents": getattr(wr, "documents", []) if wr else [],
                    "module_count": getattr(wr, "module_count", 0) if wr else 0,
                    "subprojects": getattr(wr, "subproject_results", []) if wr else [],
                },
            }, ensure_ascii=False)
        elif n == "coderef_review":
            return self._review(a)
        elif n == "coderef_frontend":
            return self._frontend(a)
        elif n == "coderef_report":
            return self._report(a)
        elif n == "coderef_audit_advisor":
            return self._advisor(a)
        # 其余工具统一走 _handlers 分发（query / memory_* / owasp / innovation /
        # asset / change_* / scan / whitelist / registry / docs_read 等），
        # 使后台线程与同步路径都能执行任意工具，避免各自散落 if/elif。
        handler = self._handlers.get(n)
        if handler is not None:
            return handler(a)
        return "未知工具: " + n

    def _review(self, a) -> str:
        """执行代码审查（coderef_review），返回结构化 JSON 文本"""
        from core.code_review import CodeReviewer
        pp = a["project_path"]
        mode = a.get("mode", "diff")
        dims = a.get("dimensions") or None
        changed_files = a.get("changed_files") or None
        diff = a.get("diff") or None
        r = CodeReviewer().review(
            pp, mode=mode, diff=diff,
            changed_files=changed_files, dimensions=dims,
        )
        return json.dumps(r, ensure_ascii=False)

    def _frontend(self, a) -> str:
        """执行前端交互审查（coderef_frontend），返回结构化 JSON 文本"""
        from core.frontend_inspector import FrontendInspector
        pp = a["project_path"]
        mode = a.get("mode", "static")
        url = a.get("url") or None
        entry = a.get("entry") or None
        levels = a.get("check_levels") or None
        r = FrontendInspector().inspect(
            pp, entry=entry, mode=mode, url=url, check_levels=levels,
        )
        return json.dumps(r, ensure_ascii=False)

    def _report(self, a) -> str:
        """执行 HTML 报告渲染（coderef_report）：
        优先重渲染既有产物（图谱+Wiki，不重跑扫描）；无既有产物时回退为跑一次全量审计并渲染。"""
        from core.pipeline_runner import Pipe
        pp = a["project_path"]
        out = a.get("output_dir") or None
        r, has_artifacts = Pipe().render_report(pp, output_dir=out)
        if not has_artifacts:
            r = Pipe().audit(pp, output_dir=out)
        hr = getattr(r, "html_report", None) or {}
        return json.dumps(hr, ensure_ascii=False)

    def _advisor(self, a) -> str:
        """审计策略判定 + 功能审查（coderef_audit_advisor）"""
        from core.review_strategy import review_advisor
        from core.functional_review import functional_reviewer
        pp = a["project_path"]
        with_functional = a.get("with_functional", True)
        strategy = review_advisor.advise(pp)
        result = {"strategy": strategy}
        if with_functional:
            try:
                fr = functional_reviewer.review(pp, strategy)
                result["functional_review"] = fr
            except Exception as e:
                result["functional_review"] = {"llm_available": False,
                                               "degraded": True, "error": str(e)}
        return json.dumps(result, ensure_ascii=False)

    def _arch(self, a) -> str:
        from core.pipeline_runner import Pipe
        r = Pipe().architecture(a["project_path"])
        # 结构化返回，与 coderef_audit 一致
        return json.dumps({
            "status": "completed",
            "tool": "coderef_architecture",
            "project_path": a["project_path"],
            "report": r.report,
            "report_path": r.report_path or "",
            "summary": {
                "total_files": getattr(r, "total_files", 0),
                "total_lines": getattr(r, "total_lines", 0),
                "findings": len(r.findings),
                "elapsed": getattr(r, "elapsed", 0),
            },
            "errors": getattr(r, "errors", []),
        }, ensure_ascii=False)

    def _wl(self, a) -> str:
        from core.pipeline_runner import Pipe
        act = a.get("action", "add")
        pp = a["project_path"]
        if act == "list":
            wl = Pipe.whitelist_list(pp)
            return json.dumps({"count": len(wl), "entries": wl}, ensure_ascii=False)
        elif act == "clear":
            n = Pipe.whitelist_clear(pp)
            return json.dumps({"cleared": n}, ensure_ascii=False)
        elif act == "core_rules_get":
            return json.dumps(Pipe.core_rules_get(pp), ensure_ascii=False)
        elif act == "core_rules_set":
            rules = a.get("core_rules", {})
            if not rules:
                return json.dumps({"error": "core_rules 不能为空"})
            return json.dumps(Pipe.core_rules_set(pp, rules), ensure_ascii=False)
        elif act == "core_rules_reset":
            return json.dumps(Pipe.core_rules_reset(pp), ensure_ascii=False)
        else:  # add
            entries = a.get("entries", [])
            if not entries:
                return json.dumps({"error": "entries 不能为空"})
            n = Pipe.whitelist_add(pp, entries)
            return json.dumps({"added": n, "total": len(Pipe.whitelist_list(pp))}, ensure_ascii=False)

    def _tsk(self, a) -> str:
        import time
        tid = a.get("task_id","")
        if not tid:
            with self._locked_tasks() as tasks:
                self._evict_finished_tasks(tasks)
                return json.dumps({"tasks":list(tasks.keys())})
        with self._locked_tasks() as tasks:
            self._evict_finished_tasks(tasks)
            t = tasks.get(tid)
            if not t: return json.dumps({"error":f"不存在: {tid}"})
            if t["thread"].is_alive():
                # 超时兜底：超大项目单次后台任务可能远超 host 层轮询上限而一直 running，
                # 导致外层 AI 轮询（poll_timeout）用尽后整步失败、结果被丢弃。运行超过
                # 阈值时改返回"部分完成 + 未完成提示"的诚实结构化结果，让调用方在放弃
                # 轮询前能拿到非空、不误导的部分结论（宁可部分完成，不要静默失败）。
                elapsed = time.time() - t.get("started_at") if t.get("started_at") else 0.0
                if elapsed > MAX_BG_TASK_SECONDS:
                    rc = t["result"]
                    prog = rc.get("progress")
                    # 结果已完整产出（线程仅在收尾）→ 直接按完成返回，带上真实 content，
                    # 避免把"已完成数据"错装成空壳 partial 让调用方误判未完成而丢弃。
                    # （coderef_scan 这类整段 run_single 完成后才一次性产出 findings。）
                    if rc.get("result"):
                        return json.dumps({
                            "status": "completed", "task_id": tid,
                            "elapsed": round(elapsed, 1),
                            "content": rc["result"],
                        }, ensure_ascii=False)
                    # 超时兜底仅在确有部分结果可交换时才返回 partial；否则（既无 result
                    # 也无 progress）直接返回 running + 超时说明，避免调用方把空壳 partial
                    # 当终态、丢弃之后才完成的完整结果。coderef_docs 等有增量产物的工具
                    # 仍保留 partial。
                    if not prog:
                        return json.dumps({
                            "status": "running", "task_id": tid,
                            "elapsed": round(elapsed, 1),
                            "message": (
                                f"任务已运行 {round(elapsed,1)}s，超过后台兜底阈值 "
                                f"({MAX_BG_TASK_SECONDS}s)，仍在后台继续执行，可稍后重查；"
                                "当前尚无部分结果可先行使用。"
                            ),
                        }, ensure_ascii=False)
                    partial = {
                        "status": "partial",
                        "task_id": tid,
                        "elapsed": round(elapsed, 1),
                        "partial": True,
                        "message": (
                            f"任务已运行 {round(elapsed,1)}s，超过后台兜底阈值 "
                            f"({MAX_BG_TASK_SECONDS}s)，仍在后台继续执行，可稍后重查；"
                            "本次返回当前已处理部分，未完成清单见下。"
                        ),
                    }
                    if prog:
                        partial["progress"] = prog
                        partial["progress_text"] = (
                            f"已完成 {prog['done']}/{prog['total']} 阶段，当前: {prog['stage']}"
                            + (f"（{prog.get('detail')}）" if prog.get("detail") else "")
                        )
                    if elapsed > BG_PARTIAL_SUGGEST_SECONDS:
                        partial["suggestion"] = (
                            "超大项目建议改用分片/增量方式：coderef_audit 用 incr 策略，"
                            "或对子项目/维度逐个扫描，"
                            "避免单次全量超时。已生成的产物（文档/报告）已按模块落盘，可先行使用。"
                        )
                    return json.dumps(partial, ensure_ascii=False)
                # 运行中：回传当前阶段进度（若有）
                rc = t["result"]
                prog = rc.get("progress")
                if prog:
                    detail = prog.get("detail")
                    base = f"已完成 {prog['done']}/{prog['total']} 阶段，当前: {prog['stage']}"
                    if detail:
                        base += f"（{detail}）"
                    return json.dumps({
                        "status": "running", "task_id": tid,
                        "progress": prog,
                        "progress_text": base,
                    }, ensure_ascii=False)
                return json.dumps({"status":"running","task_id":tid})
            rc = t["result"]
            # 标记完成时间戳，不再首次读取即删除；由 _evict_finished_tasks 按年龄/大小清除
            if "finished_at" not in t:
                t["finished_at"] = time.time()
            if "error" in rc:
                return json.dumps({"status":"error","task_id":tid,"error":rc["error"]})
            r = rc.get("result","")
        return json.dumps({"status":"completed","task_id":tid,"content":r}, ensure_ascii=False)

    def _query(self, a) -> str:
        from core.pipeline_runner import Pipe
        qt = a.get("query_type", "stats")
        pp = a["project_path"]
        kwargs = {k: v for k, v in a.items()
                  if k not in ("project_path", "query_type") and v}
        result = Pipe.kg_query(pp, qt, **kwargs)
        return json.dumps(result, ensure_ascii=False)

    @staticmethod
    def _ok(rid, text):
        return {"jsonrpc":"2.0","id":rid,"result":{"content":[{"type":"text","text":text}]}}

    def run(self):
        # 强制 stdout 为 UTF-8，解决 Windows 下中文乱码
        import io
        if hasattr(sys.stdout, 'reconfigure'):
            sys.stdout.reconfigure(encoding='utf-8')
        else:
            sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
        logger.info(f"CodeRef MCP v{PKG_VERSION} (audit|arch|docs) 启动")
        try:
            # 手动 readline 阻塞读，替代 `for line in sys.stdin`：
            # 迭代器在后台守护线程并发场景下会提前返回 EOF（即使 stdin 仍打开、fd0
            # 仍有效），导致 server 主线程提前退出、后台任务被丢弃。
            while True:
                line = sys.stdin.readline()
                if not line:
                    break
                line = line.strip()
                if not line:
                    continue
                try:
                    req = json.loads(line)
                    resp = self._handle(req)
                    if resp is not None:
                        sys.stdout.write(json.dumps(resp, ensure_ascii=False)+"\n")
                        sys.stdout.flush()
                except json.JSONDecodeError: pass
                except Exception as e:
                    sys.stdout.write(json.dumps({"jsonrpc":"2.0","id":None,
                        "error":{"code":-32000,"message":str(e)}}, ensure_ascii=False)+"\n")
                    sys.stdout.flush()
        except Exception as e:
            logger.error(f"[RUN-EXC] {type(e).__name__}: {e}")
            import traceback; logger.error(traceback.format_exc())
        return

def main(): Server().run()
if __name__ == "__main__": main()
