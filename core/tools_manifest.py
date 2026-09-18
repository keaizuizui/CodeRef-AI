# -*- coding: utf-8 -*-
"""
CodeRef MCP 工具注册数据（tools_manifest）——BUILTIN_TOOLS 描述表与注册表同源常量。

从 core/mcp_server.py 拆出（上帝模块拆解第 7 批）：
- BUILTIN_TOOLS（原 145~1166 行，超 1000 行纯数据）整体外置；
- 注册表同源常量 _SINGLE_TOOL_LABELS / WHITELIST_ACTIONS 随迁（被 BUILTIN_TOOLS 的
  schema enum 直接引用，与主文件 Server.__init__ / handler 校验共用，避免双源漂移）。

本模块为纯数据层：不 import core.mcp_server（无循环依赖），主文件负责 re-export。
"""

from typing import Dict, List

# 指标清单同源引用：coderef_eval 的 schema enum 与 core.output_evaluator.VALID_METRICS
# 保持唯一真源（新增指标只改 output_evaluator 一处，schema 自动同步，防双处漂移）
from core.output_evaluator import VALID_METRICS as _EVAL_METRICS

# ── 单维度审计工具的维度注册表 ───────────────────────────────────
# 模块级常量：既被 BUILTIN_TOOLS 中 coderef_scan 的 enum 引用，也被
# __init__ 内 _SINGLE_TOOL_LABELS 初始化使用（同源，避免重复字面量）。
_SINGLE_TOOL_LABELS: List[tuple] = [
    ("gov", "治理审计"), ("agent", "Agent安全"), ("sca", "依赖扫描(CVE)"),
    ("td", "技术债务"), ("integ", "完整性检查"), ("blind", "盲区检测"),
    ("inn", "创新传播"), ("junk", "垃圾文件"), ("resgap", "资源遗漏"),
    ("simp", "代码精简"), ("matu", "项目成熟度"),
]

# ── coderef_whitelist 合法 action 枚举（同源：schema enum 与 _wl 校验共用）──
WHITELIST_ACTIONS: tuple = ("add", "list", "clear",
                            "core_rules_get", "core_rules_set", "core_rules_reset")

# ── 内置工具注册表（MCP tools/list 的 schema 数据）───────────────
# 原为 Server.__init__ 内 700+ 行字面量 + 多次 append；外置成本模块级常量后，
# 增删工具只需改本表，构造函数只做引用（单个工具定义不再塞进构造逻辑）。
# 请保持顺序稳定：顺序即 tools/list 的展示顺序，也影响 handlers 映射的多对一解析。
BUILTIN_TOOLS: List[Dict] = [
        {
                        "name": "coderef_whitelist",
                        "description": (
                            "管理 AI 白名单和核心模块判定规则。\n"
                            "action=add/list/clear → 误报白名单管理；\n"
                            "action=core_rules_get → 查看当前核心模块判定规则；\n"
                            "action=core_rules_set → 设置核心模块规则（entry_files入口文件名列表/core_names强制核心模块名/min_files文件数阈值）；\n"
                            "action=core_rules_reset → 重置为默认规则。\n"
                            "你审查完报告后，把确认无误的误报条目写入白名单。发现 Wiki 漏了核心模块时，用 core_rules_set 追加。\n"
                            "【目录级排除】entry 带 dir 字段（目录相对路径，如 _refactor_backup）时，"
                            "该目录下文件不进入知识图谱符号级分析（真身判定/循环/重复匹配），"
                            "用于排除备份/镜像目录对生产代码的污染。"
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
                                        "dir": {"type": "string", "description": "排除目录相对路径（作用于知识图谱符号级分析，如 _refactor_backup）"},
                                    }
                                },
                                "description": "要加入白名单的条目 (action=add 时必填)"
                            },
                            "core_rules": {
                                "type": "object",
                                "properties": {
                                    "entry_files": {"type": "array", "items": {"type": "string"}, "description": "入口文件名列表，如 [\"main.py\",\"app.py\",\"server.py\"]"},
                                    "core_names": {"type": "array", "items": {"type": "string"}, "description": "强制核心模块名列表，如 [\"业务工具\",\"shared\"]"},
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
                                "支持 background=True 后台执行。\n\n"
                                "【结构锈蚀场景】本工具做「治理审计/结构诊断」时，重点看 "
                                "cycle/god_module/large_module 等存量结构症状；对「结构性锈蚀」（重复/孪生/真身），"
                                "请佐以 coderef_architecture 的 P0-B/P0-C（真身判定/重复簇）与 coderef_arch_gap 的 "
                                "duplicate/directory_duplicate 差距，勿只依赖本工具 diff 焦点。\n\n"
                                "[strategy 分场景]\n"
                                "  • 回归复核新增改动 → strategy=incr（只看本次变更文件，快）\n"
                                "  • 治理健康度体检/存量结构 → strategy=full（全量 11 工具；若嫌游离噪声多再看 arch_gap 降噪游离）\n"
                                "  勿把「治理存量结构」当「回归复核」用 strategy=incr——存量重复/孪生不在变更 diff 内，会漏。"
                            ),
                        "inputSchema": {"type": "object", "properties": {
                            "project_path": {"type": "string", "description": "目标项目路径"},
                            "output_dir": {"type": "string", "description": "报告输出目录（默认 coderef-report/）"},
                            "background": {"type": "boolean", "description": "后台执行", "default": True},
                            "wait": {"type": "boolean", "description": "阻塞等待任务完成，直接返回最终结果（免轮询 coderef_task_status）；默认 False 返回 task_id", "default": False},
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
                            "output_dir": {"type": "string", "description": "报告输出目录（默认 <project_path>/coderef-report/，可选外置）"},
                            "insight_llm": {"type": "boolean", "description": "架构洞察（管线/真身/重复）追加 LLM 人话总结（需配置 API Key，缺省静态结果完整可用）", "default": False},
                            "background": {"type": "boolean", "description": "后台执行（重型工具默认后台，返回 task_id 用 coderef_task_status 查询）", "default": True},
                            "wait": {"type": "boolean", "description": "阻塞等待任务完成，直接返回最终结果（免轮询 coderef_task_status）；默认 False 返回 task_id", "default": False},
                        }, "required": ["project_path"]},
                    },
        {
                        "name": "coderef_docs",
                        "description": (
                            "项目文档探查 = 结构化 Wiki 生成(README/架构/安装/使用/API)。\n"
                            "三级管线：AST元数据(全量)→LLM归纳→编校验证(无幻觉)。\n"
                            "wiki_style 可选 comprehensive/reference/tutorial/plain；"
                            "include_subprojects 控制是否同时为子项目生成独立 Wiki。\n"
                            "enable_agent_pointer 控制在项目根维护 AGENTS.md 的 CodeRef Wiki 指针区块（R7）；"
                            "cross_verify 控制是否对模块描述做静态交叉验证（确证徽章）。\n"
                            "自动发现子项目并生成独立 Wiki。\n"
                            "支持 background=True（推荐，生成耗时 3-20 分钟）。\n"
                            "【超大项目边界（ 定性）】全量生成对超大项目（实测 573 文件/20 万+ 行）"
                            "单次可能超后台兜底 860s 返回 partial——已按模块增量落盘可先用。"
                            "超大项目请走分片/增量：coderef_audit incr、对子项目/维度逐个扫描，"
                            "勿期望单次全量一次成功。"
                        ),
                        "inputSchema": {"type": "object", "properties": {
                            "project_path": {"type": "string", "description": "目标项目路径"},
                            "output_dir": {"type": "string", "description": "输出目录（默认 txt/）"},
                            "wiki_style": {"type": "string", "enum": ["comprehensive","reference","tutorial","plain"], "default": "comprehensive"},
                            "include_subprojects": {"type": "boolean", "default": True},
                            "enable_agent_pointer": {"type": "boolean", "default": False, "description": "在项目根维护 AGENTS.md 的 CodeRef Wiki 指针区块（R7）"},
                            "cross_verify": {"type": "boolean", "default": True, "description": "对模块描述做静态交叉验证（确证徽章）"},
                            "cross_entry_spec": {"type": "string", "default": "class:pipeline_runner:Pipe", "description": "交叉验证入口（入口调用闭包为确证依据）"},
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
                        "name": "coderef_task_cancel",
                        "description": (
                            "取消/停止一个运行中的后台任务（）。\n"
                            "对仍 running 的任务：立即置为 cancelled 状态——随后 coderef_task_status "
                            "返回可定位的 cancelled（不再误报\"running、无部分结果\"），且任务在下一个阶段"
                            "汇报点协作式尽早收尾（audit/docs 等逐阶段工具可真正停止）。\n"
                            "对已完成/已结束/不存在的任务：返回其实际状态，无副作用。\n"
                            "说明：折叠式取消无法强制杀死 daemon 线程；若任务已被取消前产出的部分产物（文档/报告）仍按模块落盘，可先使用。"
                        ),
                        "inputSchema": {"type": "object", "properties": {
                            "task_id": {"type": "string", "description": "要取消的后台任务 task_id（来自启动时的返回值）"},
                        }, "required": ["task_id"]},
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
                            "建议：调用前先 coderef_memory(action=sync) 建立基线，效果最佳。"
                        ),
                        "inputSchema": {"type": "object", "properties": {
                            "project_path": {"type": "string", "description": "目标项目路径"},
                            "with_functional": {"type": "boolean", "description": "是否叠加 LLM 功能审查增强", "default": True},
                            "background": {"type": "boolean", "description": "后台执行（重型工具默认后台，返回 task_id 用 coderef_task_status 查询）", "default": True},
                        }, "required": ["project_path"]},
                    },
        {
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
                        "tool": {"type": "string", "enum": [k for k, _ in _SINGLE_TOOL_LABELS],
                                 "description": "要审计的维度，如 gov / agent / sca / td / integ / blind / inn / junk / resgap / simp / matu"},
                        "background": {"type": "boolean",
                            "description": "后台执行（超大项目单维度如 gov 会全量盘点，可能超过单次调用超时，默认后台返回 task_id 用 coderef_task_status 查询）",
                            "default": True},
                    }, "required": ["project_path", "tool"]},
                },
        {
                    "name": "coderef_scan_list",
                    "description": "列出 coderef_scan 可选的单维度审计清单（维度名 + 说明）。",
                    "inputSchema": {"type": "object", "properties": {}},
                },
        {
                    "name": "coderef_flow_verify",
                    "description": (
                        "流程合规验证 —— 非编程人员最核心的需求：项目是不是按我期待的流程执行。\n"
                        "验证「入口 A 的调用管线是否覆盖期望步骤 B→C→D」，确认数据真的按这条管线走。\n"
                        "纯静态、确定性：数据只来自知识图谱 CALLS 边，不依赖 LLM。\n"
                        "entry 建议传 相对目录名.符号名（如 业务工具.main），前缀命中文件路径即定位；"
                        "也支持 模块.函数（如 pipeline_runner.audit）消除同名歧义；"
                        "steps 传期望步骤的符号关键词（中英文均可，编程 AI 需先把中文期望步骤映射为代码符号）。\n"
                        "状态语义：ordered=调用链确证(含顺序)；in_pipeline=在管线但顺序未确证(可能并行)；"
                        "outside=管线外/动态调用，需编程AI复核；missing=项目内无对应符号。\n"
                        "图谱不存在时会明确反馈需先构建（coderef_audit / coderef_memory(action=sync)）。"
                    ),
                    "inputSchema": {"type": "object", "properties": {
                        "project_path": {"type": "string", "description": "目标项目路径"},
                        "entry": {"type": "string", "description": "入口符号，建议传 相对目录名.符号名（如 业务工具.main），前缀命中文件路径即定位；也支持 模块.函数（如 pipeline_runner.audit）"},
                        "steps": {"type": ["array", "string"], "items": {"type": "string"},
                                  "description": "期望步骤的符号关键词列表或逗号分隔字符串，如 ['analyze_project','build_knowledge_graph','render']。省略（可选）时仅执行跨语言契约检测与入口/图谱定位，不验证流程步骤；提供空/非法元素则报错"},
                        "depth": {"type": "integer", "description": "调用链搜索深度，默认 8"},
                    }, "required": ["project_path", "entry"]},
                },
        {
                    "name": "coderef_arch_audit",
                    "description": (
                        "架构腐化诊断 —— 补齐 MCP 工具的架构诊断层。\n"
                        "复用知识图谱 CALLS 边做模块级静态诊断，输出四类架构症状：\n"
                        "cycles=循环依赖（模块依赖图强连通分量）；god_modules=上帝模块（扇出过高）；"
                        "layer_violations=分层违例（低层依赖高层）；large_modules=异常模块规模。\n"
                        "聚合为 0-10 架构健康度。纯静态、确定性，不依赖 LLM。\n"
                        "图谱不存在时会明确反馈需先构建（coderef_audit / coderef_memory(action=sync)）。"
                    ),
                    "inputSchema": {"type": "object", "properties": {
                            "project_path": {"type": "string", "description": "目标项目路径"},
                        }, "required": ["project_path"]},
                },
        {
                    "name": "coderef_target_arch_set",
                    "description": (
                        "设置/更新目标架构 JSON（5.0 架构推回正轨的参照系）。\n"
                        "目标架构由人定义（业务层 + 技术层 + 约束），CodeRef 负责对比现状并输出差距。\n"
                        "target_arch 传目标架构 JSON（对象或字符串），结构：\n"
                        "  version（必填，字符串）/ project（可选，字符串）/ \n"
                        "  business_flows（可选，数组）：每项必填 id/name/steps；\n"
                        "    steps 每项必须是 {id,name} 对象（不是字符串），可选 tech_roles 引用已定义角色 id。\n"
                        "  tech_roles（必填，数组）：每项必填 id/name/target_modules，\n"
                        "    可选 depends_on/depended_by/role_keywords（字符串数组，角色职责关键词表，\n"
                        "    供 coderef_role_boundary 做符号级职责判定；缺省时 role_boundary 会提示未配置）。\n"
                        "  constraints（可选，数组）：每项必填 from/to/rule，rule=no_dependency。\n"
                        "模板初始化（不传 target_arch 时）：template=模板名（hexagonal 六边形单体 /\n"
                        "  modular_monolith 模块化单体）按模板结合项目顶层目录生成 target_arch 初稿；\n"
                        "  detect=true 自动识别项目类型并套用对应模板（detect 优先级低于 template）。\n"
                        "  初稿仅供参考，可在此基础上完善；识别到的模板会在返回值 template 中说明。\n"
                        "校验失败返回 status=error + errors 明细（含具体字段），不会静默成功。\n"
                        "校验通过后写入 <project>/.coderef/target_arch.json（进 git 版本控制）。\n"
                        "纯确定性校验，不依赖 LLM。"
                    ),
                    "inputSchema": {"type": "object", "properties": {
                            "project_path": {"type": "string", "description": "目标项目路径"},
                            "target_arch": {"type": ["object", "string"], "description": "目标架构 JSON 对象或 JSON 字符串（与 template/detect 二选一）"},
                            "template": {"type": "string", "description": "软件类型模板名：hexagonal / modular_monolith（不传 target_arch 时生效）"},
                            "detect": {"type": "boolean", "description": "自动识别项目类型并套用对应模板（不传 target_arch/template 时生效）", "default": False},
                        }, "required": ["project_path"]},
                },
        {
                    "name": "coderef_target_arch_get",
                    "description": (
                        "获取当前目标架构（coderef_target_arch_get）。\n"
                        "返回 <project>/.coderef/target_arch.json 的内容；尚未设置时明确反馈 not_set。\n"
                        "纯确定性读取，不依赖 LLM。"
                    ),
                    "inputSchema": {"type": "object", "properties": {
                            "project_path": {"type": "string", "description": "目标项目路径"},
                        }, "required": ["project_path"]},
                },
        {
                    "name": "coderef_target_adopt",
                    "description": (
                        "游离一键纳入（②）——把 coderef_arch_gap 报出的游离/未建模模块按角色批量追加 target_modules。\n"
                        "机械性归属动作工具化：消除『游离模块靠手工在 define-target 一条条补 target_modules』的低效。\n"
                        "游离口径与 arch_gap 完全一致：free=真游离孤儿（fan_in=0 无调用者）；unmodeled=被调用但未建模。\n"
                        "role_id 指定纳入角色（缺省取第一个 tech_role）；modules 指定纳入的游离模块（module 相对路径，\n"
                        "缺省按 monitored 口径取全部）；monitored=free 仅纳真游离（缺省），all=free+unmodeled 一并纳入；\n"
                        "dry_run=true 只预览不落盘（返回拟纳入清单与拟写入架构）。\n"
                        "纳入后自动按写入后架构重评估剩余游离/未建模数，并写回 <project>/.coderef/target_arch.json。\n"
                        "纯静态、确定性，不依赖 LLM。"
                    ),
                    "inputSchema": {"type": "object", "properties": {
                            "project_path": {"type": "string", "description": "目标项目路径"},
                            "target_arch": {"type": ["object", "string"], "description": "目标架构 JSON（可选，缺省读已存储）"},
                            "role_id": {"type": "string", "description": "纳入目标角色 id（可选，缺省取第一个 tech_role）"},
                            "modules": {"type": "array", "items": {"type": "string"}, "description": "指定纳入的游离模块（module 相对路径，可选；缺省按 monitored 取全部）"},
                            "monitored": {"type": "string", "description": "纳入口径：free=仅真游离孤儿（缺省）；all=free+unmodeled 一并纳入", "default": "free", "enum": ["free", "all"]},
                            "dry_run": {"type": "boolean", "description": "只预览不落盘", "default": False},
                        }, "required": ["project_path"]},
                },
        {
                    "name": "coderef_arch_gap",
                    "description": (
                        "架构差距分析 —— 对比现状知识图谱与目标架构，输出结构化差距清单（5.0 核心）。\n"
                        "差距类型：missing=职责缺失（目标角色声明的模块不存在）；\n"
                        "dependency_violation=依赖违例（模块依赖违反角色约束）；cycle=循环依赖；\n"
                        "business_gap=业务断链（业务步骤关联角色无实现）；unassigned=游离模块（未归属）；\n"
                        "god_module=上帝模块；large_module=异常规模。\n"
                        "纯静态、确定性，复用 arch_audit 与知识图谱，不依赖 LLM。\n"
                        "target_arch 可选：不传则读取已存储的目标架构（coderef_target_arch_set）。\n"
                        "max_unassigned 控制游离模块报出上限（默认 50）。\n"
                        "图谱不存在时会明确反馈需先构建（coderef_audit / coderef_memory(action=sync)）。"
                    ),
                    "inputSchema": {"type": "object", "properties": {
                            "project_path": {"type": "string", "description": "目标项目路径"},
                            "target_arch": {"type": "object", "description": "目标架构 JSON（可选，缺省读已存储）"},
                            "max_unassigned": {"type": "integer", "description": "游离模块报出上限", "default": 50},
                        }, "required": ["project_path"]},
                },
        {
                    "name": "coderef_arch_canvas",
                    "description": (
                        "生成可视化架构视图 HTML（5.15 重写版：决策者报告首屏 + 分区画布）。\n"
                        "默认首屏=决策者架构总览报告（叙事式一页讲清：一段话总述 · 系统怎么搭（角色分区）·\n"
                        "系统怎么跑（业务主干流）· 哪里有问题（差距人话清单按严重度分组，点击可跳画布定位））；\n"
                        "点「深看架构画布」进入分区架构图：角色泳道分区 + 业务主干流粗箭头 + 违规边红曲线 +\n"
                        "循环/未归属/孪生标识，普通调用边默认折叠（悬停/点击模块展开直接依赖探查），\n"
                        "缩放/适应/导出 JSON。React 单文件构建，全内联零 CDN 依赖、离线可用。\n"
                        "open_server=true 时起本地回环静态服务直接把视图 push 给用户浏览器（5.X 前端推送构想）。\n"
                        "数据层复用 arch_gap_analyzer + 知识图谱，纯静态、确定性、不依赖 LLM。\n"
                        "输出 HTML 文件路径。超大项目同步可能超时，默认后台执行。"
                    ),
                    "inputSchema": {"type": "object", "properties": {
                            "project_path": {"type": "string", "description": "目标项目路径"},
                            "target_arch": {"type": "object", "description": "目标架构 JSON（可选，缺省读已存储）"},
                            "output_dir": {"type": "string", "description": "画布输出目录（可选，默认 <project>/.coderef/）"},
                            "background": {"type": "boolean", "description": "后台执行（超大项目同步可能超时，默认后台，返回 task_id 用 coderef_task_status 查询）", "default": True},
                            "open_server": {"type": "boolean", "description": "生成后启动本地回环静态服务，把画布 push 给用户浏览器（默认 false）", "default": False},
                            "host": {"type": "string", "description": "open_server 绑定地址（仅允许回环 127.0.0.1/::1/localhost，默认 127.0.0.1）", "default": "127.0.0.1"},
                            "port": {"type": "integer", "description": "open_server 端口（默认 0=自动分配）", "default": 0},
                        }, "required": ["project_path"]},
                },
        {
                    "name": "coderef_flow_canvas",
                    "description": (
                        "生成交互式流程画布 HTML（5.4：从代码自动提取业务管线与跨模块数据流）。\n"
                        "纯 HTML/CSS/JS + SVG 自包含、零外部依赖、离线可用。\n"
                        "数据源（纯静态、确定性）：pipeline_insight（P0-A 入口管线，沿 CALLS 归纳阶段序）\n"
                        "+ cross_module_flows（跨模块业务数据流）。\n"
                        "自由布局交互：节点自由拖拽（对齐吸附）、端口拖出任意连线成流、画布平移/缩放、\n"
                        "缩略图导航、右键菜单、快捷键、属性面板、分层/力导向自动布局、导出/导入画布 JSON。\n"
                        "输出 HTML 文件路径。纯静态、确定性，不依赖 LLM。超大项目同步可能超时，默认后台执行。"
                    ),
                    "inputSchema": {"type": "object", "properties": {
                            "project_path": {"type": "string", "description": "目标项目路径"},
                            "output_dir": {"type": "string", "description": "画布输出目录（可选，默认 <project>/.coderef/）"},
                            "max_entries": {"type": "integer", "description": "最多归纳的入口管线数（默认 6）", "default": 6},
                            "max_depth": {"type": "integer", "description": "调用链搜索深度（默认 6）", "default": 6},
                            "background": {"type": "boolean", "description": "后台执行（超大项目同步可能超时，默认后台，返回 task_id 用 coderef_task_status 查询）", "default": True},
                        }, "required": ["project_path"]},
                },
        {
                    "name": "coderef_refactor_plan",
                    "description": (
                        "生成重构任务卡列表（5.0 Phase 2：差距→任务卡）。\n"
                        "把 coderef_arch_gap 差距清单转化为编程 AI 可执行的重构任务卡。\n"
                        "每张任务卡含：type（create_module/fix_dependency/break_cycle/implement_flow/\n"
                        "move_module/split_module）、operations（具体操作）、impact（图谱确定性影响范围）、\n"
                        "verify（架构+功能验证标准）。按执行顺序排序。\n"
                        "纯静态、确定性，影响范围来自知识图谱调用闭包，不依赖 LLM。\n"
                        "target_arch 可选：不传则读取已存储目标架构。"
                    ),
                    "inputSchema": {"type": "object", "properties": {
                            "project_path": {"type": "string", "description": "目标项目路径"},
                            "target_arch": {"type": "object", "description": "目标架构 JSON（可选，缺省读已存储）"},
                            "gap_result": {"type": "object", "description": "差距清单（可选，缺省内部自动分析）"},
                        }, "required": ["project_path"]},
                },
        {
                    "name": "coderef_arch_verify",
                    "description": (
                        "架构对齐验证（5.0 Phase 2：重构后确定性验证对齐）。\n"
                        "输出 0–100 对齐度评分（四维：职责对齐40%+依赖健康30%+业务覆盖20%+代码健康10%）\n"
                        "与差距复检清单。\n"
                        "增量模式：传 changed_files（本次改动文件集合）时只对该子集影响的差距复检，\n"
                        "快速反馈单张任务卡是否达标；不传则全量验证。\n"
                        "纯静态、确定性，复用 arch_gap_analyzer 与 arch_audit，不依赖 LLM。"
                    ),
                    "inputSchema": {"type": "object", "properties": {
                            "project_path": {"type": "string", "description": "目标项目路径"},
                            "target_arch": {"type": "object", "description": "目标架构 JSON（可选，缺省读已存储）"},
                            "changed_files": {"type": "array", "items": {"type": "string"}, "description": "本次改动文件集合（可选，增量验证）"},
                        }, "required": ["project_path"]},
                },
        {
                    "name": "coderef_gov_start",
                    "description": (
                        "建档体检周期并导入差距为治理工作项（5.1 定期体检）。\n"
                        "借鉴 plane 的 Cycle：把一次差距扫描变成可追踪、可回顾的体检周期。\n"
                        "建档一个 open 的 HealthCycle，并调用 arch_gap 把当前差距全量导入为 Detected 工作项（去重/复发检测/豁免生效）。\n"
                        "name 指定周期名（缺省自动按日期）；end_date 可选截止；target_arch 可选，缺省读已存储。\n"
                        "治理状态持久化到 <project>/.coderef/governance.db（随项目进 git）。\n"
                        "纯静态、确定性，不依赖 LLM。"
                    ),
                    "inputSchema": {"type": "object", "properties": {
                            "project_path": {"type": "string", "description": "目标项目路径"},
                            "name": {"type": "string", "description": "体检周期名（可选，缺省按日期）"},
                            "description": {"type": "string", "description": "周期描述（可选）"},
                            "end_date": {"type": "string", "description": "截止日期 YYYY-MM-DD（可选）"},
                            "target_arch": {"type": "object", "description": "目标架构 JSON（可选，缺省读已存储）"},
                            "max_unassigned": {"type": "integer", "description": "游离模块报出上限（可选）", "default": 50},
                        }, "required": ["project_path"]},
                },
        {
                    "name": "coderef_gov_close",
                    "description": (
                        "收尾体检周期并输出本期统计（5.1）。\n"
                        "把指定周期标记为 closed 并记录收尾 end_date 与 note，\n"
                        "返回本期完成率/剩余/复发/豁免统计，作为单期体检报告的基础数据。\n"
                        "cid 指定周期 id，缺省关闭当前 open 周期。\n"
                        "纯静态、确定性，不依赖 LLM。"
                    ),
                    "inputSchema": {"type": "object", "properties": {
                            "project_path": {"type": "string", "description": "目标项目路径"},
                            "cid": {"type": "string", "description": "周期 id（可选，缺省当前 open 周期）"},
                            "note": {"type": "string", "description": "收尾备注（可选）"},
                        }, "required": ["project_path"]},
                },
        {
                    "name": "coderef_gov_issues",
                    "description": (
                        "查询治理工作项（预置视图/过滤）（5.1）。\n"
                        "借鉴 plane 的 View：通过固定视图快速查看治理项。\n"
                        "可用视图：open=未闭环在途项；all=全部；high=高严重级在途项；\n"
                        "recent=最近出现；recurred=复发项；rejected=已豁免项；archived=已归档项；\n"
                        "overdue=已过截止未闭环；assigned=已分配负责人。\n"
                        "也可用 status（Detected/Confirmed/Fixing/Verified/Archived/Rejected）直接过滤，\n"
                        "或用 cycle_id/assignee/limit。返回结构化工作项列表与状态分布。\n"
                        "纯静态、确定性，不依赖 LLM。"
                    ),
                    "inputSchema": {"type": "object", "properties": {
                            "project_path": {"type": "string", "description": "目标项目路径"},
                            "view": {"type": "string", "description": "预置视图名（open/all/high/recent/recurred/rejected/archived/overdue/assigned）", "default": "open"},
                            "status": {"type": "string", "description": "按状态直接过滤（可选，优先于 view）"},
                            "cycle_id": {"type": "string", "description": "限定周期（可选）"},
                            "assignee": {"type": "string", "description": "限定负责人（可选）"},
                            "limit": {"type": "integer", "description": "返回条数上限", "default": 500},
                        }, "required": ["project_path"]},
                },
        {
                    "name": "coderef_gov_transition",
                    "description": (
                        "治理工作项状态流转 / 豁免（5.1）。\n"
                        "借鉴 plane 的 Workflow State：把一条工作项沿状态机推进。\n"
                        "状态机：Detected→Confirmed→Fixing→Verified→Archived；另可豁免为 Rejected。\n"
                        "约束：仅可从 Confirmed 进入 Fixing；仅可在 Verified 后归档（Archived 前须复验达标）。\n"
                        "action=transition + to_state 做流转；action=reject + reason(必留) 做豁免；\n"
                        "action=meta 更新优先级/负责人/截止/备注。每次流转写活动日志形成审计轨迹。\n"
                        "issue_id 为工作项 id。纯静态、确定性，不依赖 LLM。\n\n"
                        "【参数动作速查】\n"
                        "  transition：必填 issue_id + action='transition' + to_state（如 Confirmed→Fixing 传 to_state='Fixing'）\n"
                        "  reject    ：必填 issue_id + action='reject' + reason（豁免理由必留，缺省会报错）\n"
                        "  meta      ：必填 issue_id + action='meta' + 至少一项（priority/assignee/due_date/note）；to_state 会被忽略，无需传\n"
                        "  状态机强约束下，非法跳转（如 Detected→Fixing）会返回明确错误属正常，先 transition 到合法中间态。"
                    ),
                    "inputSchema": {"type": "object", "properties": {
                            "project_path": {"type": "string", "description": "目标项目路径"},
                            "issue_id": {"type": "string", "description": "治理工作项 id"},
                            "action": {"type": "string", "enum": ["transition", "reject", "meta"], "default": "transition"},
                            "to_state": {"type": "string", "description": "目标状态（action=transition 时必填）"},
                            "reason": {"type": "string", "description": "豁免原因（action=reject 时必填）"},
                            "priority": {"type": "string", "description": "优先级 high/medium/low（action=meta）"},
                            "assignee": {"type": "string", "description": "负责人（action=meta）"},
                            "due_date": {"type": "string", "description": "截止日期 YYYY-MM-DD（action=meta）"},
                            "note": {"type": "string", "description": "备注（action=meta）"},
                            "actor": {"type": "string", "description": "操作者/调用方标识（可选）"},
                            "detail": {"type": "string", "description": "流转说明（可选）"},
                        }, "required": ["project_path", "issue_id"]},
                },
        {
                    "name": "coderef_gov_report",
                    "description": (
                        "体检报告 / 治理看板（单期 + 跨期趋势 + 交互看板）（5.1 起；5.12.1 合并 gov_board）。\n"
                        "借鉴 plane 的 Dashboard/Analytics + Workspace View：持续监控健康状态。\n"
                        "action=report（默认）：返回当前 open 周期（或 cid）完成率/剩余/复发/豁免统计 + 跨期趋势\n"
                        "  （各已关闭周期整改量曲线）；out_format=json 返回结构化，out_format=html 额外写出 gov_report.html 并返回路径。\n"
                        "action=board：生成自包含交互 HTML 看板（KPI/状态分布/跨期趋势/工作项表格/状态流转按钮）；\n"
                        "   契约：缺省落盘 <project>/.coderef/gov_board.html 并返回确切路径；\n"
                        "  interactive=false 退化为只读；open_server=true 可起本地 http.server 服务（进程常驻，返回 URL）。\n"
                        "action=overview：生成'项目总览'自包含 HTML（5.13 起）——五区块一屏收拢：\n"
                        "  ① 一句话体检结论（健康分 + 治理差距/豁免结论，顶栏大字）；\n"
                        "  ② 项目架构图（iframe 引用 arch_canvas_*.html，未生成则诚实占位）；\n"
                        "  ③ 项目 wiki 介绍（WIKI_INDEX/OVERVIEW 核心内联 + 全库链接）；\n"
                        "  ④ 人话解读摘要（高危清单 + 分项计数 + 确定性总结）；\n"
                        "  ⑤ 治理工作项（完整标题列 + 详情内联展开 + 全量统计）。\n"
                        "   契约：缺省落盘 <project>/.coderef/project_overview.html；静态自包含、file:// 直接可开、详情不做 fetch。\n"
                        "纯静态、确定性，趋势由治理库聚合，不依赖 LLM。"
                    ),
                    "inputSchema": {"type": "object", "properties": {
                            "project_path": {"type": "string", "description": "目标项目路径"},
                            "action": {"type": "string", "enum": ["report", "board", "overview"], "default": "report",
                                       "description": "report=体检报告（JSON/HTML）；board=交互 HTML 看板；overview=项目总览 HTML"},
                            "cid": {"type": "string", "description": "指定周期 id（可选，缺省当前 open 周期）"},
                            "out_format": {"type": "string", "enum": ["json", "html"], "default": "json",
                                           "description": "action=report 时输出格式"},
                            "output_dir": {"type": "string", "description": "输出目录（可选，默认 <project>/.coderef/）"},
                            "interactive": {"type": "boolean", "description": "action=board 时启用交互应用态（默认 true）", "default": True},
                            "open_server": {"type": "boolean", "description": "action=board 时是否同时启动本地 http.server 服务（进程常驻）", "default": False},
                            "host": {"type": "string", "description": "服务监听地址（open_server=true 时）", "default": "127.0.0.1"},
                            "port": {"type": "integer", "description": "服务端口（open_server=true 时，0=自动分配）", "default": 0},
                        }, "required": ["project_path"]},
                },
        {
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
                        "图谱不存在会明确反馈需先构建（coderef_audit / coderef_memory(action=sync)），不返回空结论。"
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
                },
        {
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
                },
        {
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
                },
        {
                    "name": "coderef_memory",
                    "description": (
                        "项目记忆层——AI 对项目「记住了什么」（4.8 起；5.12.2 合并 sync/query/status/quality 4 工具）。\n"
                        "action=sync（默认）：初始化/增量同步（mode=full 全量 / incr 增量 mtime+size）；\n"
                        "action=query：语义检索（走向量库，Ollama 缺失降级关键词）+ 结构查询（走知识图谱）；\n"
                        "action=status：认知覆盖度 + 置信度 + 盲区地图 + 认知地图 HTML；\n"
                        "action=quality：记忆质量评估（引用完整性/语义覆盖/偏差）+ auto_fix 自动补全。\n"
                        "供所有 AI 助手复用项目记忆（替代重扫）。"
                    ),
                    "inputSchema": {"type": "object", "properties": {
                        "project_path": {"type": "string", "description": "目标项目路径"},
                        "action": {"type": "string", "enum": ["sync", "query", "status", "quality"], "default": "sync"},
                        "mode": {"type": "string", "enum": ["full", "incr"], "default": "full", "description": "action=sync 时增量/全量"},
                        "query_type": {"type": "string", "enum": ["semantic","stats","entity","callers","callees","impact","relations","file_entities","search","call_graph"], "default": "semantic", "description": "action=query 时检索类型"},
                        "keyword": {"type": "string", "description": "语义检索关键词或全文搜索关键词"},
                        "name": {"type": "string", "description": "实体名称（entity 用）"},
                        "func_name": {"type": "string", "description": "函数名（callers/callees/call_graph 用）"},
                        "node_id": {"type": "string", "description": "节点 ID（query_type=relations 用）"},
                        "file_path": {"type": "string", "description": "文件路径（impact/file_entities 用）"},
                        "limit": {"type": "integer", "default": 10},
                        "auto_fix": {"type": "boolean", "default": False, "description": "action=quality 时自动补全"},
                        "background": {"type": "boolean", "description": "是否后台执行；未指定时 action=sync/status/quality 默认后台、action=query 默认同步（返回 task_id 用 coderef_task_status 查询）"},
                    }, "required": ["project_path"]},
                },
        {
                    "name": "coderef_operation_memory",
                    "description": (
                        "操作记忆层——上下文丢失后恢复（4.8 起；5.12.3 合并 6 工具）。\n"
                        "action=sync（默认）：初始化/增量同步（识别资源位置 + 可选 LLM 提炼隐性知识，输出 ledger.json + BRAIN.md）；\n"
                        "action=query：按类别检索（decision/convention/pitfall/resource/tool/doc/all）；\n"
                        "action=find：定位资源（name=test/.env/model）；\n"
                        "action=status：健康状态 + 待人工确认项；\n"
                        "action=recover：上下文丢失后一次调用恢复关键记忆（强制 gate，涉及 git/约定类操作先调用，勿满 PATH 找工具）；\n"
                        "action=export：导出 Markdown 知识库 + 冲突检测（attach 到不支持 MCP 的 LLM 界面）。\n"
                        "供 AI 在上下文被压缩后最小成本拿回『东西在哪儿、过去的规范是什么』。"
                    ),
                    "inputSchema": {"type": "object", "properties": {
                        "project_path": {"type": "string", "description": "目标项目路径"},
                        "action": {"type": "string", "enum": ["sync", "query", "find", "status", "recover", "export"], "default": "sync"},
                        "mode": {"type": "string", "enum": ["full", "incr"], "default": "full", "description": "action=sync 时增量/全量"},
                        "with_llm": {"type": "boolean", "default": True, "description": "action=sync 时是否启用 LLM 提炼"},
                        "query_type": {"type": "string", "enum": ["all", "resource", "tool", "doc", "decision", "convention", "pitfall"], "default": "all", "description": "action=query 时类别"},
                        "keyword": {"type": "string", "description": "模糊匹配 name/summary/path"},
                        "name": {"type": "string", "description": "资源名或路径片段（find 用，如 'test'、'.env'）"},
                        "limit": {"type": "integer", "description": "返回条数上限（各 action 默认：query=10 / find=5 / recover=8）"},
                        "output_path": {"type": "string", "description": "导出文件路径（export 用，缺省 <项目>/data/operation_memory/OPERATION_MEMORY.md）"},
                        "background": {"type": "boolean", "description": "是否后台执行；未指定时 action=sync 默认后台，recover/query/find/status/export 保持同步（返回 task_id 用 coderef_task_status 查询）"},
                    }, "required": ["project_path"]},
                },
        {
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
                },
        {
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
                },
        {
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
                },
        {
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
                },
        {
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
                },
        {
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
                },
        {
                    "name": "coderef_registry",
                    "description": (
                        "管理已知设计库（DesignRegistry）。\n"
                        "action=list 列出已知设计；add 新增 canonical 设计；alias 把别名归一到 canonical（解决 LLM 命名漂移）；"
                        "delete 删除当前项目注册的设计（预置/未标注来源与他项目注册的条目受保护，不可删除）。"
                    ),
                    "inputSchema": {"type": "object", "properties": {
                        "project_path": {"type": "string", "description": "目标项目路径"},
                        "action": {"type": "string", "enum": ["list","add","alias","delete"], "default": "list"},
                        "name": {"type": "string", "description": "设计名/别名（add/delete 用）"},
                        "canonical": {"type": "string", "description": "规范设计名（add/alias 用）"},
                        "alias": {"type": "string", "description": "要归一化的别名（alias 用）"},
                        "description": {"type": "string", "description": "设计说明（add 用）"},
                    }, "required": ["project_path"]},
                },
        {
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
                },
        {
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
                },
        {
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
                },
        # ── 5.2 治理运营收尾：自动化流水线 / 动态探针 / Web 看板 / 多仓聚合 / 定时实跑 / 符号级职责越界 ──
        {
                    "name": "coderef_gov_pipeline",
                    "description": (
                        "治理自动化流水线（5.2）：把工作项治理串成可追踪闭环。\n"
                        "对每个在途工作项（Confirmed/Fixing；Detected 需先人工确认，不直接进流水线）：\n"
                        "  1) 状态 → Fixing；\n"
                        "  2) 凭差距快照生成任务卡（含影响范围/验证标准，复用 refactor_task_generator）；\n"
                        "  3) 调 arch_alignment_verifier 复验（changed_files 增量或全量）；\n"
                        "  4) 复验达标（差距不再复现）→ Verified；未达标保持 Fixing 附缺口。\n"
                        "全程写活动日志形成审计轨迹。issue_ids 为治理工作项 id 列表。\n"
                        "纯静态、确定性，不依赖 LLM。"
                    ),
                    "inputSchema": {"type": "object", "properties": {
                            "project_path": {"type": "string", "description": "目标项目路径"},
                            "issue_ids": {"type": "array", "items": {"type": "string"}, "description": "治理工作项 id 列表"},
                            "changed_files": {"type": "array", "items": {"type": "string"}, "description": "本次已执行改动文件集合（可选，增量复验）"},
                            "auto_verified": {"type": "boolean", "description": "达标判定通过后是否自动流转 Verified", "default": True},
                        }, "required": ["project_path", "issue_ids"]},
                },
        {
                    "name": "coderef_dynamic_probe",
                    "description": (
                        "动态探针（5.2）：补全静态盲区，挖掘运行时才成立的动态信号，零执行。\n"
                        "静态层扫描：动态导入（importlib/__import__/pkgutil）、装饰器注册（@app.route/registry.register）、\n"
                        "工厂/回调登记（getattr/globals 索引字面量符号）、setuptools entry_points 组件发现。\n"
                        "输出 probe 信号（dynamic_imports/registrations/indirect_lookups/entry_points）+ hints。\n"
                        "危险操作隔离：不 import、不 subprocess 被检项目代码，executed=false 显式声明。\n"
                        "纯静态、确定性，不依赖 LLM。"
                    ),
                    "inputSchema": {"type": "object", "properties": {
                            "project_path": {"type": "string", "description": "目标项目路径"},
                            "include_tests": {"type": "boolean", "description": "是否包含测试目录（默认否）", "default": False},
                        }, "required": ["project_path"]},
                },
        {
                    "name": "coderef_gov_board",
                    "description": (
                        "治理 Web 看板（5.2 兼容别名；5.12.1 起转发到 coderef_gov_report(action=board)）。\n"
                        "为既有调用方保留：生成自包含交互 HTML 看板并缺省落盘 <project>/.coderef/gov_board.html（），\n"
                        "返回确切 board_html 路径供浏览器直接打开。新调用请用 coderef_gov_report(action=board)，本别名行为与之一致。\n"
                        "interactive=true（默认）启用前端流转回写治理库（/api/transition，仅 127.0.0.1）；\n"
                        "false 退化为只读。open=true 可起本地 http.server 服务（进程常驻，返回 URL）。\n"
                        "纯静态、确定性，不依赖 LLM。"
                    ),
                    "inputSchema": {"type": "object", "properties": {
                            "project_path": {"type": "string", "description": "目标项目路径"},
                            "output_dir": {"type": "string", "description": "看板输出目录（可选，默认 <project>/.coderef/）"},
                            "cid": {"type": "string", "description": "指定周期 id（可选，缺省当前 open 周期）"},
                            "interactive": {"type": "boolean", "description": "启用交互应用态（默认 true）", "default": True},
                            "open": {"type": "boolean", "description": "是否同时启动本地 http.server 服务（进程常驻）", "default": False},
                            "host": {"type": "string", "description": "服务监听地址（open=true 时）", "default": "127.0.0.1"},
                            "port": {"type": "integer", "description": "服务端口（open=true 时，0=自动分配）", "default": 0},
                        }, "required": ["project_path"]},
                },
        {
                    "name": "coderef_gov_workspace",
                    "description": (
                        "多代码库聚合治理（5.2）：跨仓汇总治理状态。\n"
                        "projects 为多个项目路径列表；跨仓聚合 open/high/复发/归档统计，\n"
                        "给出跨仓 TOP 风险（repos_sorted 按高危数降序）。\n"
                        "对应 plane 的 Workspace：一组 project 的聚合治理边界。\n"
                        "纯静态、确定性，不依赖 LLM。"
                    ),
                    "inputSchema": {"type": "object", "properties": {
                            "projects": {"type": "array", "items": {"type": "string"}, "description": "目标项目路径列表（≥1）"},
                        }, "required": ["projects"]},
                },
        {
                    "name": "coderef_gov_schedule",
                    "description": (
                        "定时体检（5.2）：cron/CI 触发片段 + 可运行触发脚本 + 离期检查。\n"
                        "生成可直接运行的入口脚本 run_cycle.py（调用 HealthCycle.start_cycle 开新周期\n"
                        "+ 导入差距 + 产出报告 JSON/HTML），供 cron/CI/人工直接调用。\n"
                        "离期检查：存在已过 end_date 仍未收尾的 open 周期时提醒复检。\n"
                        "不内置后台驻留（延续决策），触发交给外层 cron/CI/任务计划。\n"
                        "纯静态、确定性，不依赖 LLM。"
                    ),
                    "inputSchema": {"type": "object", "properties": {
                            "project_path": {"type": "string", "description": "目标项目路径"},
                            "cron_expr": {"type": "string", "description": "建议触发时刻（5 段标准 cron；默认每周一 06:00）", "default": "0 6 * * 1"},
                            "command": {"type": "string", "description": "每次体检触发的 MCP 动作（默认 coderef_scan）", "default": "coderef_scan"},
                            "output_dir": {"type": "string", "description": "触发脚本输出目录（可选，默认 <project>/.coderef/）"},
                        }, "required": ["project_path"]},
                },
        {
                    "name": "coderef_role_boundary",
                    "description": (
                        "符号级职责越界检测（5.2）：模块归属正确，但符号职责逾越所属角色边界。\n"
                        "静态层职责信号（确定性，零 LLM）：\n"
                        "  - 命名语义：符号名与 tech_roles.role_keywords 匹配 → 判定符号疑似属于某角色；\n"
                        "  - 调用边界：本角色模块调用属于其他角色的符号/入口 → 跨角色调用；\n"
                        "  - 复用信号：符号在其他角色 target_modules 中作为核心符号存在却定义在本模块。\n"
                        "semantic=true 时对 top 候选做可选语义判定（需 LLM）；缺省 false 只给静态信号 + uncertainty。\n"
                        "基于已有知识图谱 + 目标架构 role_keywords；无图谱/无 role_keywords 时诚实提示。\n"
                        "纯静态、确定性（语义可选）。"
                    ),
                    "inputSchema": {"type": "object", "properties": {
                            "project_path": {"type": "string", "description": "目标项目路径（须已构建知识图谱）"},
                            "target_arch": {"type": "object", "description": "目标架构 JSON（可选，缺省读已存储）"},
                            "max_issues": {"type": "integer", "description": "越界符号报出上限", "default": 200},
                            "semantic": {"type": "boolean", "description": "是否启用可选语义判定（需 LLM）", "default": False},
                        }, "required": ["project_path"]},
                },
        {
                    "name": "coderef_version",
                    "description": (
                        "轻量版本探针（只读、零副作用）：返回当前 CodeRef-AI 进程加载的版本号。\n"
                        "一行调用即可断言 版本==target，杜绝「结果字段反推版本」的进程新旧误判\n"
                        "（测试方 2026-08-31 反馈：v5.12.6 首探即因进程未重启加载旧代码而误判）。\n"
                        "无需 project_path，秒级返回，不触发任何扫描/图谱构建。"
                    ),
                    "inputSchema": {"type": "object", "properties": {}, "required": []},
                },
        {
                        "name": "coderef_eval",
                        "description": (
                            "产出物语义评估（LLM-as-judge）= 给 CodeRef 自身的 LLM 产出物加语义质量门禁。\n"
                            "像写单测一样断言「LLM 输出过不过阈值」（4 个语义指标 + 软硬判定），可进 CI、可沉淀。\n"
                            "action=assert（默认）：单条产出物评估 + 阈值断言 → verdict PASS/FAIL；\n"
                            "action=score：批量评估（text 可传字符串数组）→ 逐条分数 + 明细 + 汇总报告。\n"
                            "指标：answer_relevancy(软) 相关性 / faithfulness(硬) 忠实度 / hallucination(硬) 幻觉检出 / coherence(软) 连贯性。\n"
                            "软硬判定（防致命错误被平均分稀释）：硬指标(faithfulness/hallucination) 挂 → 整条 FAIL 一票否决；\n"
                            "软指标失分只降平均分不单独否决；verdict=PASS 当且仅当硬指标全过 且 得分 ≥ threshold。\n"
                            "诚实边界：语义评分 = AI 判断，非确定性事实，仅软门禁；\n"
                            "只评 CodeRef 自产的 LLM 文本，不评用户项目运行时的 LLM/RAG/Agent 产出（静态审计禁区）。\n"
                            "缺 API key 时硬阻断返回 SKIP（不降级编造）。"
                        ),
                        "inputSchema": {"type": "object", "properties": {
                            "project_path": {"type": "string", "description": "目标项目路径（评估上下文/报告落盘用）"},
                            "action": {"type": "string", "enum": ["assert", "score"], "default": "assert", "description": "assert=单条评估+阈值断言；score=批量评估→报告"},
                            "metric": {"type": ["string", "array"], "items": {"type": "string", "enum": list(_EVAL_METRICS)}, "description": "评估指标（见工具描述中的软硬分级）：单指标传字符串；多指标聚合传数组（含硬指标时按软硬判定一票否决，防致命错误被平均分稀释）"},
                            "text": {"type": ["string", "array"], "items": {"type": "string"}, "description": "待评估产出物（LLM 生成的文本）；action=score 时也可传字符串数组批量评估"},
                            "context": {"type": "string", "description": "参考上下文/源材料（answer_relevancy/faithfulness/hallucination 必填）"},
                            "threshold": {"type": "number", "description": "断言阈值 0–1", "default": 0.7},
                            "background": {"type": "boolean", "description": "是否后台执行；coderef_eval 默认后台（返回 task_id 用 coderef_task_status 轮询），background=false 强制同步拿结果"},
                        }, "required": ["project_path", "metric", "text"]},
                    }
]
