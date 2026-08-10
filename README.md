<!-- AI Summary: CodeRef-AI exposes 26 MCP tools that give coding AI a deterministic "audit brain" and give non-programmers a readable view of their project. Core results (audit, knowledge graph, architecture diagnosis, flow verification, change guard, OWASP) are pure static analysis — no LLM, reproducible. LLM is only used for synthesis tasks (wiki, code review) and degrades gracefully without an API key. Install is pure-Python, no C compilation, works out of the box. Best for: non-programmers who use a coding AI and want to confirm their project runs as intended, and teams who want AI that augments rather than hallucinates. -->

# CodeRef-AI — 编程 AI 的治理外脑，非编程人员的技术助理

**Version 4.2.6** | Python 3.10+ | MCP Protocol | MIT License

> 给编程 AI 一双确定性的眼睛，给非编程人员一张看得懂的工程体检单。

---

## 它是什么

CodeRef-AI 通过 MCP 协议暴露 **26 个工具**，同时服务两类人：

- **编程 AI 的治理外脑**：让 AI 不再逐文件读代码，而是像查数据库一样查询项目的结构、调用链与风险。
- **非编程人员的技术助理**：把看不懂的代码变成通俗的健康仪表盘、Wiki 文档和流程确证，让你不用读代码，也能确认项目有没有按你的设想运转。

它不替代 AI，而是让它看到用静态事实核验过的世界——核心结论来自代码事实，而不是大模型的猜测。

## 核心优势

### 1. 确定性优先：关键能力不靠 LLM，靠静态事实

大多数 AI 审查工具把结论建立在"大模型读代码"之上，而模型会幻觉。CodeRef 反过来：审计、知识图谱、架构诊断、流程验证、变更守护、OWASP 合规这些核心能力全部走**纯静态分析**，结果确定、可复现，同一个项目每次跑出同样的结论。LLM 只用于 Wiki 归纳、代码审查等"需要总结"的场景，并且没有 API Key 时优雅降级为静态结果，绝不编造。

### 2. 交叉验证：用独立性对抗幻觉

11 个检测器独立分析同一工程，相互验证，输出 HIGH / MEDIUM / LOW 置信度分级。单一工具可能误判，但多个独立工具互验之后，结论的置信度显著提升。这是 CodeRef 对抗"AI 自己读自己"幻觉的底层机制。

### 3. 三级降噪：报告不再海量误报

实测一次审计从 873 条噪声收敛到 79 条（约 91% 降幅）。白名单精准抑制已知误报 → 规则匹配过滤 MD5 哈希、配置 URL 等常见噪声 → 爆发式合并同类项。报告剩下的是人真正该看的东西。

### 4. 知识图谱：一次构建，跨会话复用

运行一次审计即构建 SQLite 知识图谱，之后编程 AI 用结构化查询代替 grep 和逐文件阅读，省下 10-100 倍 token。修改某个文件会影响哪些模块、谁调用了某个函数、从入口展开调用链——不再是 AI 现场猜，而是查表。

### 5. 非技术人员也能验证项目是否按预期运转

这是别的工具做不了的事：你不需要看懂代码，只需定义期望流程（入口 A 应该依次经过步骤 B→C→D），`coderef_flow_verify` 会在调用链里给出确证证据——确证、在管线、存疑、缺失，四种状态如实标记，绝不把"静态查不到"误判成"流程错误"。配合健康仪表盘和 Wiki，你第一次能"看懂"自己的项目。

### 6. 即装即用：纯 Python 免编译，无 API Key 也能跑

安装只依赖纯 Python 包，Python 3.10-3.14 全部免编译直接装好，不在安装阶段要求 C 工具链。核心功能（审计、图谱、架构、变更守护、OWASP）不需要任何 API Key，本地跑通之后，再补一个 Key 让编程 AI 把 Wiki、代码审查这些 LLM 能力也打开。你只需要给编程 AI 一个 Key，它自己就能装好、配好、审好、读好、产出报告。

## 四引擎架构

CodeRef 4.0 由四个引擎驱动，覆盖「审计 → 记忆 → 创新 → 守护」完整闭环：

| 引擎 | 解决的问题 | 核心工具 |
|------|-----------|---------|
| **审计引擎** | 全维度代码体检 + 图谱 + 文档 + 审查 | `coderef_audit` `coderef_query` `coderef_review` 等 |
| **记忆引擎** | AI 对项目「记住了什么」，增量同步 + 语义查询 | `coderef_memory_*` `coderef_prompt_mgmt` |
| **创新识别引擎** | 从项目里挖出值得复用的设计，并沉淀为资产 | `coderef_innovation` `coderef_asset` `coderef_registry` |
| **变更守护引擎** | 拦截 AI 把代码改坏，输出人能看懂的变更报告 | `coderef_change_guard` `coderef_change_report` |

## 26 个 MCP 工具

### 审计引擎

| 工具 | 功能 | 需要 LLM |
|------|------|---------|
| `coderef_audit` | 11 审计工具一键产出 + 自动降噪 + 知识图谱构建；支持 `strategy` 策略（auto 自动判定/full 全量/incr 增量裁剪重型工具） | 否 |
| `coderef_scan` | 单维度审计（11 选 1），实时安全带，快一个量级 | 否 |
| `coderef_scan_list` | 列出 `coderef_scan` 可选的维度清单 | 否 |
| `coderef_flow_verify` | 流程合规验证：非编程人员验证「项目是否按我期望的流程执行」（入口 A 的调用管线是否覆盖步骤 B→C→D）。纯静态、确定性，只读知识图谱 CALLS 边，不依赖 LLM；状态分确证/在管线/存疑/缺失 | 否 |
| `coderef_arch_audit` | 架构腐化诊断：复用知识图谱 CALLS 边做模块级静态诊断（循环依赖/上帝模块/分层违例/异常模块规模），聚合 0–10 架构健康度。纯静态、不依赖 LLM | 否 |
| `coderef_architecture` | 架构分析图谱 + 交互式 HTML 模块画布 | 否 |
| `coderef_docs` | 项目 Wiki 文档生成 + 子项目探测 | 是 |
| `coderef_docs_read` | 按需读取已生成 Wiki 文档正文（返回内容而非路径，解决 AI 无法 fs 访问外部文件夹） | 否 |
| `coderef_query` | 知识图谱结构化查询（9 种查询类型） | 否 |
| `coderef_review` | 代码审查：diff 变更审查 / 新项目全量语义首查 | 是 |
| `coderef_frontend` | 前端交互审查：按钮/菜单静态枚举 + 6 维度审查 | 是 |
| `coderef_report` | 把审计报告/知识图谱/Wiki 聚合成自包含 HTML 报告目录 | 否 |
| `coderef_audit_advisor` | 审计策略判定（增量/全量）+ 重点功能维度 + 可选 LLM 功能审查 | 可选 |
| `coderef_whitelist` | 白名单管理 + 核心模块规则配置 | 否 |
| `coderef_task_status` | 后台任务状态查询 | 否 |

### 记忆引擎

| 工具 | 功能 | 需要 LLM |
|------|------|---------|
| `coderef_memory_sync` | 初始化 / mtime+size 增量同步项目记忆层 | 否 |
| `coderef_memory_query` | 语义检索（向量库）+ 结构查询（知识图谱）复用项目记忆 | 否 |
| `coderef_memory_status` | 「AI 知道什么」：认知覆盖度 + 置信度 + 盲区地图 | 否 |
| `coderef_memory_quality` | 记忆质量评估（引用完整性/语义覆盖/偏差）+ 自动补全 | 可选 |
| `coderef_prompt_mgmt` | Prompt 资产管理：版本 / 对比 / A-B 测试 | 是 |

### 创新识别引擎

| 工具 | 功能 | 需要 LLM |
|------|------|---------|
| `coderef_innovation` | 识别项目创新设计 + 传播缺口，理想清单 vs 实际实现对照 | 是 |
| `coderef_asset` | 将验证过的设计固化 `WorkflowAsset` 资产（查询/导出/提交） | 是 |
| `coderef_registry` | 管理已知设计库，别名归一（解决 LLM 命名漂移） | 否 |

### 变更守护引擎

| 工具 | 功能 | 需要 LLM |
|------|------|---------|
| `coderef_change_guard` | AI 代码退化检测（守护引擎建立在 git 之上）。`action=guard` 对比基线与新代码拦截退化；`ensure_git` 项目无 git 时自动建库；`anchor` 锚定健康基线 tag；`list_baselines` 列出健康基线。回滚交由外层 AI 执行。`git_bin` 可由外层 AI 探测 git 路径传入，避免依赖系统 PATH | 否 |
| `coderef_change_report` | 把 diff 归纳为「人话版」变更说明（新增/修改/影响/风险） | 可选 |

### OWASP 合规

| 工具 | 功能 | 需要 LLM |
|------|------|---------|
| `coderef_owasp` | OWASP LLM Top 10 合规检测，LLM01-LLM10 逐类分级 | 否 |

## 快速开始

如果你是**非编程人员**：把下面这份说明交给你的编程 AI，它会帮你完成安装、配置和第一轮分析，你只需要准备一个 API Key。你真正要做的，是最后打开它生成的健康仪表盘和 Wiki，看懂自己的项目。

如果你**自己动手**：照下面四步走。

### 1. 安装

安装只依赖纯 Python 包，不触发任何 C 源码编译，Python 3.10-3.14 免编译直接装好。

```bash
git clone https://github.com/keaizuizui/CodeRef-AI.git
cd CodeRef-AI
pip install -r requirements.txt
```

### 2. 配置 LLM（可选）

> 审计、知识图谱、架构诊断、流程验证、变更守护、OWASP **不需要 LLM**，纯静态分析即可运行。仅 Wiki 文档、代码审查、Prompt 资产、创新识别需要 LLM。没有 API Key 时，这些功能优雅降级为纯静态结果，服务照常可用。

**Windows 用户：**

```bash
setup.bat
```

**Linux / macOS 用户：**

```bash
export CODEREF_API_KEY="your-api-key"
export CODEREF_PROVIDER="deepseek"        # 支持: deepseek / openai / ollama
export CODEREF_BASE_URL="https://api.deepseek.com/v1"
export CODEREF_MODEL="deepseek-chat"
```

**使用本地 Ollama（免费，无需 API Key）：**

```bash
export CODEREF_PROVIDER="ollama"
export CODEREF_BASE_URL="http://localhost:11434/v1"
export CODEREF_MODEL="qwen2.5:7b"
export CODEREF_API_KEY="ollama"
```

### 3. 启动 MCP Server

```bash
python -m core.mcp_server
```

### 4. 配置 MCP 客户端

在 Trae / Claude Desktop 等 MCP 客户端中添加：

```json
{
  "mcpServers": {
    "coderef-ai": {
      "command": "python",
      "args": ["-m", "core.mcp_server"],
      "cwd": "/path/to/coderef-ai"
    }
  }
}
```

详细配置指南见 [MCP_SETUP.md](MCP_SETUP.md)。

> 本项目由编程 AI 辅助研发，作为 AI 治理方向的实践样本。建议你拿到代码后，用 CodeRef 自己审计一遍，让报告带你理解每处实现，再按需调整。

## 典型使用流程

```
# 1. 初次分析：跑一次全量审计（后台，自动构建知识图谱）
coderef_audit(project_path="/path/to/project", background=True)
coderef_task_status(task_id="...")

# 2. 编程 AI 随时查询知识图谱（替代 grep/读文件）
coderef_query(project_path="/path/to/project", query_type="callers", func_name="login")
coderef_query(project_path="/path/to/project", query_type="impact", file_path="utils.py")

# 3. 生成项目文档（非编程人员阅读）
coderef_docs(project_path="/path/to/project", background=True)

# 3.1 编程 AI 按需读取文档正文（无需 fs 访问外部文件夹）
coderef_docs_read(project_path="/path/to/project", doc="README.md")

# 3.2 明确指定审计策略（默认 auto 自动判定：首次全量 / 变更增量裁剪）
coderef_audit(project_path="/path/to/project", strategy="incr", background=True)

# 4. 审查代码变更（AI 帮你自查 PR / 提交）
coderef_review(project_path="/path/to/project", mode="diff", diff="<git diff 文本>", background=True)

# 5. 审查前端交互（按钮 / 菜单）
coderef_frontend(project_path="/path/to/project", mode="static", background=True)

# 6. 守护 git 基层 + 提交前拦截 AI 把代码改坏 + 锚定健康基线 + 人话版变更说明
coderef_change_guard(project_path="/path/to/project", action="ensure_git")
coderef_change_guard(project_path="/path/to/project", action="guard", diff="<git diff 文本>")
coderef_change_guard(project_path="/path/to/project", action="anchor", label="release-1.0")
coderef_change_guard(project_path="/path/to/project", action="list_baselines")
coderef_change_report(project_path="/path/to/project", diff="<git diff 文本>")

# 7. 沉淀项目里值得复用的设计
coderef_innovation(project_path="/path/to/project")
coderef_asset(project_path="/path/to/project", action="list")

# 8. 查看健康仪表盘
# → coderef-report/health_dashboard_{timestamp}.html

# 9. 审查/治理：请求你的编程 AI 阅读报告，把误报写进白名单，
#    并把问题归类为 4 种：① AI 可自行处理 ② 需要你介入 ③ 复杂需讨论 ④ 新建暂存区待定
```

## 审计管线

### 11 个检测器

| 检测器 | 检测内容 |
|--------|---------|
| 治理审计 (gov) | 架构违规、安全漏洞、反模式、质量铁律，CWE/OWASP 映射 |
| Agent 安全审计 (agent) | 提示注入、上下文操纵、工具滥用、数据泄露、自主行为 |
| 依赖扫描 (sca) | requirements.txt / pyproject.toml 的 CVE 漏洞 |
| 技术债务 (td) | 圈复杂度、认知复杂度、过长函数、魔法数字、注释代码 |
| 完整性检查 (integ) | TODO/FIXME 残留、孤立测试文件、文档覆盖率 |
| 盲区检测 (blind) | 文档盲区、缺失依赖、动态路径注入、空文件 |
| 创新传播 (inn) | 模块间设计模式不一致、"A 有 B 该有但没有"的缺口 |
| 垃圾文件 (junk) | 重复文件、应被 gitignore 的文件、孤立文件 |
| 资源遗漏 (resgap) | 缺失本地模块、动态导入风险、未使用依赖 |
| 代码精简 (simp) | 死代码、可标准库替代、过度工程 |
| 项目成熟度 (matu) | 项目健康度综合评分 |

### 三级自动降噪

| 层级 | 机制 | 效果 |
|------|------|------|
| Layer 1 | AI 白名单（`coderef_whitelist` 写入） | 精准抑制已知误报 |
| Layer 2 | NOISE_RULES 规则匹配 | 自动抑制 MD5 哈希、配置 URL 等常见误报 |
| Layer 3 | 合并汇总 | 邻行去重 + 爆发式汇总（>8 条同类别 → 1 条统计） |

### 交叉验证反幻觉

多工具独立分析同一项目，相互验证结果，产生置信度分级（HIGH / MEDIUM / LOW）。这是 CodeRef 对抗 AI 自查幻觉的核心机制——单一工具可能误判，但多个独立工具交叉验证后，置信度大幅提升。

## 知识图谱

运行 audit / architecture / docs / memory_sync 后自动构建 SQLite 知识图谱，持久化到 `cache/kg/`。一次构建，跨会话复用。

**查询速查：**

| 想知道什么 | query_type | 参数 |
|-----------|-----------|------|
| 项目有多大 | `stats` | 无 |
| 搜索包含 "auth" 的代码 | `search` | `keyword="auth"` |
| 查找所有认证相关函数 | `entity` | `name="auth", type="function"` |
| 谁调用了 `process_order` | `callers` | `func_name="process_order"` |
| `main` 调用了哪些函数 | `callees` | `func_name="main"` |
| 修改 `utils.py` 影响哪些模块 | `impact` | `file_path="utils.py"` |
| `server.py` 有哪些函数和类 | `file_entities` | `file_path="server.py"` |
| 从 `handle_request` 展开调用链 | `call_graph` | `func_name="handle_request", depth=3` |

**实体类型：** `module` / `function` / `class` / `method` / `config` / `constant`
**关系类型：** `CONTAINS` / `IMPORTS` / `INHERITS` / `CALLS` / `REFERENCES`

## 项目结构

```
coderef-ai/
├── core/                             # 核心引擎
│   ├── mcp_server.py                 # MCP Server 入口（26 个工具）
│   ├── pipeline_runner.py            # 管线引擎（audit/architecture/docs + 知识图谱）
│   ├── tool_registry.py              # 工具注册中心（收敛管线引擎的上帝模块职责）
│   ├── review_strategy.py            # 审计策略判定（增量/全量 + 影响闭包）
│   ├── functional_review.py          # 功能审查（创新传播/结构复杂度等维度）
│   ├── report_renderer.py            # 审计报告/知识图谱/Wiki → HTML 报告渲染
│   ├── code_review.py                # 代码审查（diff 变更/全量语义首查，evidence 标记）
│   ├── frontend_inspector.py         # 前端交互审查（按钮/菜单静态枚举 + LLM 审查）
│   ├── code_analyzer.py              # 代码分析引擎（AST）
│   ├── ast_parser.py                 # AST 精细解析器（调用关系/赋值/配置）
│   ├── code_models.py                # 代码数据模型（切断 CodeAnalyzer↔AstParser 循环依赖）
│   ├── code_knowledge_graph.py       # 知识图谱引擎（SQLite 持久化）
│   ├── code_knowledge_base.py        # 代码知识库
│   ├── health_dashboard.py           # 项目健康仪表盘（零外部依赖 HTML）
│   ├── wiki_generator.py             # Wiki 生成器（三级管线）
│   ├── wiki_cross_verify.py          # Wiki 模块级交叉验证（确证徽章）
│   ├── flow_verify.py                # 流程合规验证（步骤级，coderef_flow_verify）
│   ├── arch_audit.py                 # 架构腐化诊断（循环依赖/上帝模块/分层违例）
│   ├── graph_closure.py              # 调用闭包计算（flow_verify 与 wiki_cross_verify 共用）
│   ├── workflow_graph.py             # 架构图生成器（vis-network）
│   ├── diagram_generator.py          # 图表/画布生成
│   ├── shared_filter.py              # 通用过滤基础设施（AutoNoiseFilter）
│   ├── project_scope.py              # 项目范围管理（含 vendored/venv 过滤）
│   ├── llm_integration.py            # LLM 集成（超时/重试/JSON 截断容错/预算）
│   ├── business_analyzer.py          # 业务语义分析（提示注入防护）
│   ├── cache_manager.py              # 缓存管理
│   ├── gitnexus_client.py            # GitNexus 客户端
│   ├── governance_audit.py           # 治理审计（CWE/OWASP 映射）
│   ├── agent_security_auditor.py     # Agent 安全审计
│   ├── sca_checker.py                # 依赖安全扫描（CVE）
│   ├── tech_debt_detector.py         # 技术债务检测
│   ├── integrity_checker.py          # 完整性检查
│   ├── blind_spot_detector.py        # 盲区检测
│   ├── innovation_propagation_detector.py  # 创新传播检测
│   ├── junk_detector.py              # 垃圾文件检测
│   ├── resource_gap_detector.py      # 资源遗漏检测
│   ├── code_simplifier.py            # 代码精简检测
│   ├── project_maturity_checker.py   # 项目成熟度评估
│   ├── memory_layer.py               # 记忆引擎：增量同步 + 语义查询
│   ├── memory_quality.py             # 记忆质量评估 + 补全
│   ├── prompt_asset_manager.py       # Prompt 资产版本化 / 对比 / A-B 测试
│   ├── prompt_analyzer.py            # Prompt 分析
│   ├── prompt_extractor.py           # Prompt 提取
│   ├── innovation_engine.py          # 创新识别引擎：结构化创新 + 缺口按价值挑选
│   ├── design_registry.py            # 已知设计库（别名归一）
│   ├── owasp_compliance.py           # OWASP LLM Top 10 合规检测
│   ├── change_guard.py               # 变更守护：git 基层 + 健康基线 + 退化检测
│   └── change_report.py              # 人话版变更报告
├── config/                           # 配置（settings.py + 本地 config.json，含密钥，已 gitignore）
│   └── settings.py                   # 集中阈值/魔数配置
├── docs/                             # 文档（config/core/utils 详细说明）
├── cache/                            # 运行时缓存（.gitignore 已忽略）
├── coderef-report/                   # 输出报告（.gitignore 已忽略）
├── demo-app/                         # 前端审查测试实例（含 6 个预置交互问题）
├── setup.bat                         # Windows 配置向导
├── requirements.txt
├── MCP_SETUP.md                      # 详细配置指南
└── LICENSE
```

## 设计特性

| 特性 | 说明 |
|------|------|
| 不修改代码 | 所有建议只输出不执行，原代码保持不变 |
| 本地优先 | 代码分析完全在本地，审计和知识图谱无需网络，支持离线运行 |
| 隐私安全 | LLM API 密钥存 `config/config.json`（已 gitignore），不提交 Git |
| 结构化输出 | 报告 Markdown，仪表盘 HTML，知识图谱 SQLite |
| 检查点续跑 | 管线每 2 分钟保存进度，中断后可恢复 |
| 后台任务 | 长任务（audit / docs）异步执行，轮询获取结果 |
| 项目隔离 | 每个项目独立缓存，切换项目不互相干扰 |
| 开源友好 | 敏感数据集中 `cache/` 与 `config/config.json`，删除即清理，一行命令安全开源 |

## 更新日志

### v4.2.6 — Agent 安全审计新增「参数透传失效」检测（AGENT-SEC-27）

- **新增 AGENT-SEC-27 静态检测**：`coderef_audit` 的 Agent 安全审计新增「参数透传失效 / 被配置静默覆盖」规则——检测「函数声明了参数 X，函数体却从 config/cred/settings/env 等配置容器读取同名值」的运行时语义矛盾：调用方传入的实参被静默忽略，父代理会基于错误前提做判断（如误以为派了某模型，实际用了配置里的模型）。走 AST 级分析，能识别跨行/跨结构的覆盖，避免逐行正则漏判
- **覆盖三种容器形态**：`config["x"]` / `config.x` / `config.get("x")` 均命中，且支持 `self.config`、`self.creds`、`os.environ` 等 Attribute 链容器；`x = x or config["x"]` 合理兜底不误报，嵌套函数作用域严格隔离、非配置容器不误报
- **工程收敛（自审查修复）**：文件遍历改为单一 `os.walk` + 单次读取（正则扫描与 AST 扫描复用同一份内容，消除二次 I/O）；`EXCLUDE_DIRS` 提取为类级常量供文件遍历与项目级检查复用，消除三处分散定义

### v4.2.5 — coderef_innovation 输出可固化清单（审计工具守边界，固化交给对方 AI）

- **新增 `solidifiable_assets` 可固化清单**：`coderef_innovation` 的 `detect` 结果中新增该字段，仅列出达到固化阈值（≥2 个 workflow 采用 + 附带 evidence）的设计，并附 `adopters` 真实采用记录与 `commit_hint`
- **审计 / 编程职责分离**：CodeRef 只判定「某设计够不够格固化」，不自动生成代码；template_code / patch_suggestion / migration_guide 由对方编程 AI 依据 description 自行补全后，再调用 `coderef_asset(action="commit")` 完成固化
- **防污染一致**：清单判定与 `coderef_asset` 的 commit 防污染检查同源，不会出现「清单可固化但 commit 被拒」的矛盾；不满足条件的设计不进入清单，从源头避免误固化污染资产库

### v4.2.4 — 变更守护引擎接入 git 健康基线（守护闭环真正落地）

- **建立 git 基层 `action=ensure_git`**：项目无 git 时自动 `git init` 并补齐最小用户/分支配置，让「守护引擎从形同虚设变为真正可用」——之前守护依赖 git 基线，但 git 恰恰常常缺失，二者是联动的
- **锚定健康基线 `action=anchor`**：把审计通过 / 人工确认健康的当前代码 commit 并打 `coderef-health-*` tag，作为后续回滚参照；返回本次 committed 文件数，并可通过 `allow_autocommit` 控制工作区有改动时是否先自动提交
- **列出基线 `action=list_baselines`**：列出全部健康基线 tag，便于编程 AI 决定回滚到哪一版
- **guard 增强**：动态兜底从 git 历史提取最近改动作为基线对比，返回附带 `git_ready` 与最近健康基线 `health_baseline`，供外层 AI 回滚参照
- **新增 `git_bin` 参数**：由外层编程 AI 用 `Get-Command git` / `where git` 探测 git 可执行文件路径或安装目录后传入，避免依赖系统 PATH（git 常不在 PATH）
- **稳定性**：git 命令统一 UTF-8 / replace 解码，杜绝 Windows 中文乱码或解码异常；`git_timeout` 支持按项目规模调整
- **回滚边界**：回滚交由外层编程 AI 执行（如 `git checkout <health_baseline tag>`），CodeRef 仅提供确定性参照，不做强制回滚

### v4.2.3 — 误报治理与聚合 HTML 全 0 修复

- **修复聚合 HTML 报告全 0**：`coderef_report` 重渲染既有产物时，若用空 `PipeResult` 聚合，`index.html` 审计卡片与 `audit.html` 明细会全部为 0 / "暂无发现"。现 `audit()` 在落盘 markdown 的同时把 findings 与统计序列化为 `audit_findings.json`，`render_report` 优先读取该 JSON 恢复后再渲染，保证重渲染审计内容完整
- **修复 SCA CVE-2023-32690 归属错误**：`pandas` 旧表误挂该 CVE（实为 DMTF libspdm 漏洞，与 pandas 无关），已移除本地条目，由 OSV 在线查询兜底
- **新增组件级利用面过滤**：`langchain-community` 的 CVE-2024-2965（SitemapLoader 无限递归 DoS）等只影响特定子组件的 CVE，若项目源码未实际 import/使用受影响的组件，自动降级为 `low` 并附「潜在风险」说明，避免对未使用组件机械报高危
- **修复 arch_audit 同名模块误判**：模块识别改用项目相对路径而非 basename，`db/base.py` 与 `utils/base.py` 不再被合并计数，「base fan_in 132」等上帝模块虚高消失；跨目录单向调用不再被误判为循环依赖
- **修复连接池探活机械打标**：`AGENT-RESILIENCE-07` 仅对实际使用数据库连接池的项目打标（精确匹配 `create_engine(`/`import sqlalchemy`/`pool_pre_ping` 等），纯 SQLite 等项目不再误报

### v4.2.2 — 依赖瘦身：tree-sitter 降为可选（可用性承诺的关键修复）

- **核心诉求**：兑现「非技术人员 + 编程 AI 即装即用」承诺。审计发现 `tree-sitter==0.20.4` 是唯一需要 C 编译的依赖，且仅覆盖 Python 3.10-3.12；在 Python 3.13+ 上无预编译 wheel，会强制源码编译导致安装崩溃——这是「装不起来」的第一印象头号来源
- **关键发现**：tree-sitter 实为**死依赖**——核心解析走 Python 标准库 `ast.parse`（`core/ast_parser.py`），而 `_init_parsers` 填充的 `self.parsers` 字典全项目无任何读取方。移除后功能完全不受影响
- **改动**：从 `requirements.txt` 移除必需 `tree-sitter` / `tree-sitter-languages`，改为注释标注的**可选依赖**（保留 `_init_parsers` 容错代码，未来如需多语言解析可自行启用）
- **更轻更稳**：`pip install -r requirements.txt` 不再触发任何 C 源码编译，Python 3.10-3.14 全部免编译直接装好，安装更快更省心
- **验证**：无 tree-sitter、无 API key 环境下端到端审计正常（13 findings, 0 errors），LLM 优雅降级为静态审查

### v4.2.1 — 架构腐化诊断层（MCP 工具补盲区）

- **新增 `coderef_arch_audit`**：补齐 MCP 工具「看不到架构级问题」的盲区。复用知识图谱 `CALLS` 边做模块级静态诊断，输出四类架构症状：`cycles`（模块依赖图强连通分量→循环依赖）、`god_modules`（扇出过高→上帝模块）、`layer_violations`（低层依赖高层）、`large_modules`（异常模块规模），聚合为 0–10 架构健康度
- **纯静态、确定性**：只读知识图谱，不依赖 LLM，结果稳定可复现——延续「非编程人员也能验证工程健康」的目标
- **本轮架构债修复**：抽取 `core/code_models.py` 切断 `CodeAnalyzer↔AstParser` 循环依赖（R1）、收敛全项目函数内惰性导入（R2）、抽取 `core/tool_registry.py` 收敛 `pipeline_runner` 上帝模块（R3）、删除 `utils/helpers.py` 死代码（R4）、抽取 `core/graph_closure.py` 消除 `flow_verify` 与 `wiki_cross_verify` 知识重复（R5）

### v4.2.0 — 流程合规验证（非编程人员最核心的需求）

- **新增 `coderef_flow_verify`**：验证「项目是不是按我期望的流程执行」——入口 A 的调用管线是否覆盖期望步骤 B→C→D，确认数据真的按这条管线走。这是对非编程人员最有价值的功能：他不需要看懂代码，只需定义期望流程，工具给出代码是否按此执行的确证证据
- **纯静态、确定性**：数据只来自知识图谱 `CALLS` 边，不依赖 LLM，因此结果稳定可复现（区别于 Wiki 的 LLM 生成内容）——正契合"流程合规验证优先静态"的稳定性诉求
- **入口消歧义**：`entry` 支持 `模块.函数`（如 `pipeline_runner.audit`）限定，解决同名函数（如多个模块的 `audit`）歧义
- **四态诚实标记**：`ordered`=调用链确证(含顺序)；`in_pipeline`=在管线但顺序未确证(可能并行)；`outside`=管线外/动态调用，需编程 AI 复核；`missing`=项目内无对应符号。绝不把"静态查不到"误判为"流程错误"
- **缺失图谱明确反馈**：知识图谱未构建时返回明确提示需先运行 `coderef_audit` / `coderef_memory_sync`，不静默
- **自动定位图谱**：通过 `CodeKnowledgeGraph(project_path)` 自动定位项目图谱，调用方无需传 db 路径
- **与 `wiki_cross_verify` 的分工**：`core/flow_verify.py`（步骤级，作为 MCP 工具 `coderef_flow_verify` 暴露给非编程人员验证期望流程）与 `core/wiki_cross_verify.py`（目录级，给 Wiki 模块条目打确证徽章，由 `wiki_generator` 内部调用）共享同一套「静态 CALLS 边 + 确定性」方法论，是解决「Wiki 幻觉」的一体两面、互补不冗余——前者是步骤级流程确证，后者把确证结果回贴到 Wiki 人话描述上

### v4.1.3 — git 超时参数化（让外层 AI 按项目规模自调超时）

- **超时参数暴露**：`coderef_change_guard` 新增 `git_timeout` 参数，允许外层 AI 根据项目规模调节 git 命令的等待秒数，避免小项目等太久、大项目超时误判
- **规模建议写入工具描述**：明确建议"小型项目(<1万行) 15s；中型(1~10万行) 30s；大型(>10万行) 60s"，让外层 AI 在 `tools/list` 看到即可自己决策，无需依赖工具侧猜
- **默认值保留兼容**：`DEFAULT_GIT_TIMEOUT` 常量保持 30s（中型项目），不传即时用默认值，旧调用方式不受影响
- **全链路透传**：MCP 工具 schema → `_change_guard` 分发 → `guard(git_timeout=...)` → `_auto_git_diff(timeout=...)` 逐层透传，无硬编码

### v4.1.2 — 退化检测动态兜底（消除误导性空结论）

- **修复承诺未兑现**：`coderef_change_guard` 此前既不传 `diff` 也不传 `baseline_dir` 时，静默返回空 findings 并显示"未检测到明显退化"——这是误导性静态结果，未做任何基线对比
- **git 历史动态兜底**：无 `diff`/`baseline_dir` 时自动尝试从 git 历史提取最近改动作为基线（优先工作区未提交改动 `git diff HEAD`，其次最近一次提交 `git diff HEAD~1 HEAD`），走真实退化检测
- **明确的降级反馈**：git 不可用 / 非 git 仓库 / 无历史改动时，返回 `source=no-baseline` 并明确提示"退化检测未执行，请传入 diff 或 baseline_dir"，绝不假装"未检测到退化"
- **检测依据透明化**：返回结构新增 `source` 字段（`diff` / `baseline_dir` / `git-auto` / `no-baseline`），summary 同步标注基线来源，让外层 AI 清楚结论依据
- **优雅降级**：git 命令执行失败（超时 / 非零退出 / 无输出）逐级降级尝试，全程不抛异常

### v4.1.1 — LLM 逐条粗筛闭环（疑似误报 → 用户 AI 反馈白名单）

- **新增 LLM 逐条粗筛**：功能审查阶段对 findings 逐条做三分类（`suspected_fp` 疑似误报 / `needs_review` 需人工确认 / `confirmed` 真问题），判断依据升级为携带 `detail` + `suggestion`，不再仅凭标题
- **疑似误报附带建议白名单条目**：每条疑似误报自动构造 `{file, rule, category}` 建议条目（file 取相对路径后两段、rule 截断避免误伤），随报告输出提示"确认无误后调用 `coderef_whitelist(action=add)` 反馈，下次自动过滤"，让用户项目 AI 在真实上下文里拍板，而非由工具预设误报
- **粗筛不自动过滤**：粗筛结果仅作建议标记展示，不删除 findings，避免 LLM 误判吞掉真问题
- **优雅降级**：LLM 不可用 / 无 findings 时粗筛返回空结构（`ran=False`），不影响功能审查降级路径；`review()` 返回值统一携带 `screen` 字段保证调用方结构一致
- **健壮性**：粗筛 prompt 沿用 f-string 大括号转义，避免 `Invalid format specifier` 回归

### v4.1.0 — 动态策略审计 + 按需文档读取

- **动态兜底落地**：`coderef_audit` 新增 `strategy` 参数（`auto`/`full`/`incr`/`no_change`）。`auto` 时由 `ReviewAdvisor` 依据变更信号 + 知识图谱影响闭包自动判定增量或全量；增量模式裁剪创新传播/代码精简/项目成熟度 3 个重型全量工具（11→8），聚焦变更相关维度，显著降低重复审计耗时
- **新增 `coderef_docs_read` 工具**：按需读取已生成的 Wiki 文档正文（返回内容而非路径），解决编程 AI 无法主动 fs 访问外部文件夹的问题；自动探测 `docs/wiki/` → `docs/` → `txt/`，含路径穿越防护与 `max_chars` 截断
- **工具清单补全**：文档工具总数由 21 修正为 24（补记 `coderef_report`、`coderef_audit_advisor`），四引擎架构下工具清单与 MCP Server 实际能力对齐
- **健壮性**：`coderef_docs_read` 的 `max_chars` 非法输入防御性回退默认值，避免 MCP 层抛异常

### v4.0.4 — 审计误报修复（Agent 安全审计 + 成熟度建议归类）

- **timeout 误报修复**：`coderef_audit` 的网络请求无超时检测不再把 `requests.get(url, timeout=120)` 误判为无超时。改为平衡括号解析完整调用参数，支持跨行调用与嵌套括号（如 `timeout=compute_timeout()`），仅当调用确实未传 `timeout` 时才报告
- **pickle 误报修复**：反序列化检测从无条件 blocker 降级为 medium，并识别服务端内部 `dumps→loads` 闭环予以豁免；豁免条件收紧为"被负载变量确实由 `pickle.dumps` 赋值"，外部请求数据、复杂表达式不再被误豁免（避免漏报真实攻击面）
- **连接池探活误报修复**：对不使用数据库连接池的项目（如纯 SQLite）不再机械打标 `AGENT-RESILIENCE-07`，仅当项目存在 SQLAlchemy 等连接池依赖时才建议
- **成熟度建议归类**：`coderef_audit` 的成熟度检查将"工程化改进建议"（CI/CD、容器化、格式化、配置模板等 warn 项）与"缺陷"分离，报告新增"💡 建议（工程化改进项，非缺陷）"独立小节，建议项不再以缺陷级别进入 HIGH/MEDIUM 缺陷汇总
- **管线健壮性**：`_matu` 改为直接接住成熟度检查返回值，消除对检测器 `self.report` 副作用的隐式依赖

### v4.0.3 — Wiki 生成可靠性（部分失败可感知）

- **`coderef_docs` 输出目录参数生效**：调用方指定的 `output_dir` 现在会被正确传递到 Wiki 生成器，不再无视参数落到默认 `txt/`
- **不再产生 0 字节空文件**：LLM 生成返回空内容时跳过落盘，避免生成"文档已生成"的假象
- **部分失败可感知**：`coderef_docs` 改为结构化返回 `status`（`completed` / `partial_failed`）+ `errors` + 输出目录 + 文档清单，部分文档生成失败时不再被当作全量成功
- **LLM 失败不再写占位假文档**：`_llm_ask` 失败返回空串并记录错误，交由上层统一跳过与告警，取代原先"错误占位符当正常文档"的假象

### v4.0.2 — 审计证据透明化（区分审计发现与历史数据）

- **知识图谱查询返回构建时间**：`coderef_query` 的所有查询类型（stats/entity/callers/callees/impact/relations/file_entities/search/call_graph）统一附带 `kg_built_at` 与 `kg_note`，明确提示"图谱仅当运行 audit/docs/architecture 后才会重建，代码有改动需先重建再查询"，避免把旧图谱当成本次审计结论
- **审计结果携带证据字段**：`coderef_audit` 返回新增 `evidence` 块，包含本次扫描时间 `scan_ts`、知识图谱构建时间 `kg_built_at`、本次扫描文件快照 `file_snapshot`（mtime+size），并声明统计口径仅覆盖快照所列文件、不代表修复状态
- 报告显式标注统计口径：审计报告头部新增"统计口径"章节，写明本次扫描时间、图谱构建时间，并声明 HIGH/MEDIUM/LOW 均为审计发现、不代表任何修复状态，修复需对照 git 提交单独核实

### v4.0.1 — 复查修复（SCA 准确性 + 报告透明性）

- **修复 SCA 版本比较失效**：补齐 `packaging` 依赖，修正 `_version_matches` 异常被吞导致所有版本约束无条件命中的误报根因
- **修正本地 CVE 表错误归属**：移除 `sqlalchemy→CVE-2023-48795`、`numpy→CVE-2023-32698`、`fastapi→CVE-2024-24762` 等错误映射，Pillow 改用真实 CVE
- **OSV 在线查询失败不再静默**：降级时报告明确告警"仅本地库，可能遗漏"，区分"在线未命中"与"在线未查"
- **修复报告头部耗时 0.0s**：elapsed 改为实时兜底计算，展示真实耗时
- **修复分级归表**：置信度等级严格跟随 severity，MEDIUM 表不再混入 low 级条目，并补上位置列消除"重复"观感
- **审计范围透明**：报告披露排除目录数及原因分布，消除"为何只统计 N 文件"的疑问
- **进度细化**：扫描阶段提供"已扫描文件/总文件"中间进度，长阶段不再无感知
- 清理 `config/settings.py` 中无引用的 LLM 配置死代码，LLM 配置统一走 `core/llm_integration.py`

### v4.0 — 四引擎架构 + 21 个 MCP 工具

- **记忆引擎**：代码记忆增量同步 + 语义查询（`coderef_memory_sync`、`coderef_memory_query` 等），记忆质量评估与 Prompt 资产版本化管理
- **创新识别引擎**：结构化创新识别 + 设计资产化（`coderef_innovation`、`coderef_asset`），把创新传播升级为「理想清单 vs 实际实现」的结构化对比
- **OWASP 合规引擎**：覆盖 OWASP LLM Top 10 的合规检测（`coderef_owasp`），LLM01-LLM10 全维度
- **变更保护引擎**：AI 代码劣化检测 + 人类可读变更报告（`coderef_change_guard`、`coderef_change_report`）
- 统一 `coderef_scan` 参数化工具 + `coderef_scan_list`，总工具数精简为 21 个
- 创新传播检测器性能优化：vendored 库过滤、LLM 一次看清单价值挑选、类级预算兜底、实例级签名缓存

### v3.2 — 代码审查 + 前端交互审查

- 新增 `coderef_review` MCP 工具：基于 git diff 的变更行内审查（mode=diff）+ 新项目全量语义首查（mode=full），覆盖 7 个审查维度，结论带 evidence 标记供交叉验证
- 新增 `coderef_frontend` MCP 工具：静态全量枚举 HTML/JS 全部按钮（事件/确认弹窗/禁用态）与 L1-L5 菜单树，按 6 维度审查；可选 `mode=runtime` 浏览器抽查（失败自动降级）
- 内置 `demo-app/` 测试实例（含 6 个预置交互问题）用于功能验证

### v3.1 — 知识图谱 + 健康仪表盘

- 新增 SQLite 持久化项目知识图谱，6 种节点类型，6 种关系边
- 新增 `coderef_query` MCP 工具，9 种查询类型，替代 grep / 读文件
- 新增零外部依赖 HTML 健康仪表盘，非编程人员友好
- AstParser 集成到知识图谱构建，自动填充 CALLS 边
- Wiki 核心模块判定规则可配置化（`coderef_whitelist` 扩展）

### v3.0 — 三功能管线架构

- 18 个独立 MCP 工具 → 合并为 3 个管线（audit / architecture / docs）
- 统一管线引擎：共享 AST 扫描 + 检查点续跑 + 后台任务
- 三级自动降噪（AutoNoiseFilter）：白名单 + NOISE_RULES + 合并汇总
- 交叉验证：多工具独立分析互验，产生置信度分级

## 许可证

MIT License — 详见 [LICENSE](LICENSE)。
