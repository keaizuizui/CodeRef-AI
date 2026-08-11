# -*- coding: utf-8 -*-
"""
CodeRef MCP Server v4.2.4 — 四大引擎 + 26 个工具
  审计引擎     → coderef_audit / coderef_scan / coderef_scan_list / architecture / docs / query / review / frontend / whitelist / task_status
  记忆引擎     → coderef_memory_sync / memory_query / memory_status / memory_quality / prompt_mgmt
  创新识别引擎 → coderef_innovation / asset / registry
  变更守护引擎 → coderef_change_guard / change_report
  OWASP 合规   → coderef_owasp
"""

import json, sys, os, logging, traceback, threading, uuid, time
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


# ─── 工具可靠性清单 ─────────────────────────────────────────────
# 汇总工具的可信度与使用边界，注入到 MCP 元数据（initialize.serverInfo.reliability +
# tools/list 各工具 description），让外层 LLM 在选工具时主动避开误报、改用更合适的工具。
# 依据：对多轮真实审计结果的人工复核（区分工具误报 vs 代码真问题）。
RELIABILITY_GUIDE = (
    "CodeRef 工具可靠性清单（供外层 AI 选工具时参考）：\n"
    "【可靠，可放心日常使用】coderef_audit / coderef_scan / coderef_owasp / "
    "coderef_architecture / coderef_change_guard / coderef_change_report / coderef_query / "
    "coderef_review / coderef_memory_* / coderef_task_status。\n"
    "【有使用边界，需注意场景】\n"
    "  - coderef_frontend：仅扫 HTML/菜单，不适合 React/Vue SPA 组件逻辑；SPA 改用 mode=runtime 浏览器抽查。\n"
    "  - coderef_scan/audit 的 sca 维度（CVE 扫描）：不可全信。会把 poetry 配置 priority='primary' 误当依赖、"
    "把 >= 范围约束当固定版本。CVE 类发现需人工对照真实 CVE 库复核，勿直接采信。\n"
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
                    "支持 background=True 后台执行。"
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
                    "支持 background=True 后台执行。"
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
                "先用 coderef_scan_list 查看可选维度清单。返回该维度 findings（tier 分级 + file/line + suggestion）。"
            ),
            "inputSchema": {"type": "object", "properties": {
                "project_path": {"type": "string", "description": "目标项目路径"},
                "tool": {"type": "string", "enum": [k for k, _ in self._SINGLE_TOOL_LABELS],
                         "description": "要审计的维度，如 gov / agent / sca / td / integ / blind / inn / junk / resgap / simp / matu"},
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
        self._tools.append({
            "name": "coderef_prompt_mgmt",
            "description": (
                "Prompt 资产管理：版本 / 对比 / A-B 测试。\n"
                "action=list 列出资产；version 记录新版本/回滚；compare 同一场景多版本评分；"
                "abtest 下发 A/B 组并择优晋升。Prompt 是 AI Agent 核心资产。"
            ),
            "inputSchema": {"type": "object", "properties": {
                "project_path": {"type": "string", "description": "目标项目路径"},
                "action": {"type": "string", "enum": ["list","version","compare","abtest"], "default": "list"},
                "name": {"type": "string", "description": "prompt 资产名"},
                "content": {"type": "string", "description": "prompt 内容（version 新建时用）"},
                "version": {"type": "string", "description": "版本号（回滚/指定版本）"},
                "abtest_group": {"type": "string", "description": "A/B 组（abtest 用，A/B/promote）"},
            }, "required": ["project_path"]},
        })
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
                "commit 固化设计为资产（需 ≥2 workflow 采用 + evidence，防污染）。"
            ),
            "inputSchema": {"type": "object", "properties": {
                "project_path": {"type": "string", "description": "目标项目路径"},
                "action": {"type": "string", "enum": ["list","get","export","commit"], "default": "list"},
                "canonical": {"type": "string", "description": "规范设计名"},
                "description": {"type": "string", "description": "一句话说明（commit 用）"},
                "template_code": {"type": "string", "description": "可复制骨架代码（commit 用）"},
                "patch_suggestion": {"type": "string", "description": "迁移补丁建议（commit 用）"},
                "migration_guide": {"type": "string", "description": "迁移指南（commit 用）"},
                "background": {"type": "boolean", "description": "后台执行（重型工具默认后台，返回 task_id 用 coderef_task_status 查询）", "default": True},
            }, "required": ["project_path"]},
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
            "coderef_change_guard": self._change_guard,
            "coderef_change_report": self._change_report,
            "coderef_memory_sync": self._memory_sync,
            "coderef_memory_query": self._memory_query,
            "coderef_memory_status": self._memory_status,
            "coderef_memory_quality": self._memory_quality,
            "coderef_prompt_mgmt": self._prompt_mgmt,
            "coderef_owasp": self._owasp,
            "coderef_innovation": self._innovation,
            "coderef_asset": self._asset,
            "coderef_registry": self._registry,
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
            "coderef_memory_sync", "coderef_memory_quality", "coderef_owasp",
            "coderef_innovation", "coderef_asset", "coderef_change_guard",
            "coderef_change_report",
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
                    tasks[tid] = {"thread":t,"result":rc,"tool":n}
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

    def _prompt_mgmt(self, a: dict) -> str:
        """Prompt 资产管理（coderef_prompt_mgmt）"""
        from core.prompt_asset_manager import PromptAssetManager
        pp = a["project_path"]
        r = PromptAssetManager().manage(
            pp, action=a.get("action", "list"), name=a.get("name", ""),
            content=a.get("content", ""), version=a.get("version", ""),
            abtest_group=a.get("abtest_group", ""),
        )
        r["tool"] = "coderef_prompt_mgmt"
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
        steps = a.get("steps") or []
        if isinstance(steps, str):
            # 兼容逗号分隔字符串
            steps = [s.strip() for s in steps.split(",") if s.strip()]
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

    def _run(self, n, a, progress_cb=None) -> str:
        from core.pipeline_runner import Pipe
        # 部分工具（如 coderef_scan_list）不依赖 project_path，用容错读取避免误抛 KeyError
        p, o = a.get("project_path", ""), a.get("output_dir")
        logger.info(f"[{n}] {p}")
        if n == "coderef_audit":
            strat = a.get("strategy", "auto")
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
        tid = a.get("task_id","")
        if not tid:
            with self._locked_tasks() as tasks:
                return json.dumps({"tasks":list(tasks.keys())})
        with self._locked_tasks() as tasks:
            t = tasks.get(tid)
            if not t: return json.dumps({"error":f"不存在: {tid}"})
            if t["thread"].is_alive():
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
            if "error" in rc:
                t["finished_at"] = time.time()
                self._evict_finished_tasks(tasks)
                return json.dumps({"status":"error","task_id":tid,"error":rc["error"]})
            r = rc.get("result","")
            t["finished_at"] = time.time()
            self._evict_finished_tasks(tasks)
        return json.dumps({"status":"completed","task_id":tid,"content":r}, ensure_ascii=False)

    _TASK_RETENTION_SECONDS = 300
    _TASK_MAX_ENTRIES = 100

    def _evict_finished_tasks(self, tasks: dict) -> None:
        """驱逐已过期的完成任务，避免无限增长。"""
        now = time.time()
        # 先驱逐过期的
        expired = [
            tid for tid, t in tasks.items()
            if t.get("finished_at") and now - t["finished_at"] > self._TASK_RETENTION_SECONDS
        ]
        for tid in expired:
            del tasks[tid]
        # 若仍超上限，按完成时间驱逐最旧的
        if len(tasks) > self._TASK_MAX_ENTRIES:
            finished = sorted(
                [(tid, t.get("finished_at", 0)) for tid, t in tasks.items() if t.get("finished_at")],
                key=lambda x: x[1]
            )
            for tid, _ in finished[:len(tasks) - self._TASK_MAX_ENTRIES]:
                del tasks[tid]

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
        logger.info(f"CodeRef MCP v{PKG_VERSION} 启动，已注册 {len(self._tools)} 个工具")
        for line in sys.stdin:
            if not (line := line.strip()): continue
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

def main(): Server().run()
if __name__ == "__main__": main()
