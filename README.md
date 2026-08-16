<!-- AI Summary: CodeRef-AI exposes 32 MCP tools that give coding AI a deterministic "audit brain" and give non-programmers a readable view of their project. Core results (audit, knowledge graph, architecture diagnosis, flow verification, change guard, OWASP, deterministic verification, prompt compliance) are pure static analysis — no LLM, reproducible. LLM is only used for synthesis tasks (wiki, code review) and hard-blocks honestly without an API key. Builds a closed loop: verify LLM/CodeRabbit claims deterministically, replicate solidified design assets, and interpret everything in plain language for non-programmers. Best for: non-programmers who use a coding AI and want to confirm their project runs as intended, and teams who want AI that augments rather than hallucinates. -->
[![MCP Badge](https://lobehub.com/badge/mcp/keaizuizui-coderef-ai?style=flat)](https://lobehub.com/mcp/keaizuizui-coderef-ai)

# CodeRef-AI — 编程 AI 的治理外脑，非编程人员的技术助理

**Version 4.8.9** | Python 3.10+ | MCP Protocol | MIT License

> 给编程 AI 一双确定性的眼睛，给非编程人员一张看得懂的工程体检单。

---

## 它是什么

CodeRef-AI 通过 MCP 协议暴露 **32 个工具**，同时服务两类人：

- **编程 AI 的治理外脑**：让 AI 不再逐文件读代码，而是像查数据库一样查询项目的结构、调用链与风险；持有一条 LLM/CodeRabbit 论断时，还能用静态图谱做确定性核验，再决定采不采信。
- **非编程人员的技术助理**：把看不懂的代码变成通俗的健康仪表盘、Wiki 文档和流程确证，让你不用读代码，也能确认项目有没有按你的设想运转。

它不替代 AI，而是让它看到用静态事实核验过的世界——核心结论来自代码事实，而不是大模型的猜测。

## 核心优势

### 1. 确定性优先：关键能力不靠 LLM，靠静态事实

大多数 AI 审查工具把结论建立在"大模型读代码"之上，而模型会幻觉。CodeRef 反过来：审计、知识图谱、架构诊断、流程验证、变更守护、OWASP 合规这些核心能力全部走**纯静态分析**，结果确定、可复现，同一个项目每次跑出同样的结论。LLM 只用于 Wiki 归纳、业务报告等"需要人话总结"的场景，并且未配置 API Key 时这些 LLM 产物会被**明确硬阻断**并提示配置，绝不降级编造；确定性分析无 LLM 也照常可用。

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

CodeRef 4.0 由四个引擎驱动，覆盖「审计 → 记忆 → 创新 → 守护」完整闭环；4.3–4.6 在四引擎之上补上「确定性核验 + 平台整合 + 复刻落地」，让闭环真正可落地：

| 引擎 | 解决的问题 | 核心工具 |
|------|-----------|---------|
| **审计引擎** | 全维度代码体检 + 图谱 + 文档 + 审查 + 论断核验 | `coderef_audit` `coderef_query` `coderef_review` `coderef_verify_findings` 等 |
| **记忆引擎** | AI 对项目「记住了什么」，增量同步 + 语义查询 + 治理 | `coderef_memory_*` `coderef_prompt_governance` |
| **创新识别引擎** | 从项目里挖出值得复用的设计，固化为资产并复刻到其他项目 | `coderef_innovation` `coderef_asset` `coderef_replicate` `coderef_registry` |
| **变更守护引擎** | 拦截 AI 把代码改坏，输出人能看懂的变更报告 | `coderef_change_guard` `coderef_change_report` |
| **人话解读平台** | 把确定性格子结论翻译成非编程人员听得懂的"人话" | `coderef_interpret` |

## 32 个 MCP 工具

### 审计引擎

| 工具 | 功能 | 需要 LLM |
|------|------|---------|
| `coderef_audit` | 11 审计工具一键产出 + 自动降噪 + 知识图谱构建；支持 `strategy` 策略（auto 自动判定/full 全量/incr 增量裁剪重型工具） | 否 |
| `coderef_scan` | 单维度审计（11 选 1），实时安全带，快一个量级；大项目自动转后台执行，立即返回 `task_id`，用 `coderef_task_status` 轮询获取结果 | 否 |
| `coderef_scan_list` | 列出 `coderef_scan` 可选的维度清单 | 否 |
| `coderef_flow_verify` | 流程合规验证：非编程人员验证「项目是否按我期望的流程执行」（入口 A 的调用管线是否覆盖步骤 B→C→D）。纯静态、确定性，只读知识图谱 CALLS 边，不依赖 LLM；状态分确证/在管线/存疑/缺失 | 否 |
| `coderef_verify_findings` | 确定性核验 LLM/CodeRabbit 论断：论断引用的代码目标是否真实存在、是否在关键管线内。verdict（确证/证伪/部分确证/存疑）由静态图谱打出，诚实话标签来源分离，LLM 无权改结论 | 否 |
| `coderef_prompt_governance` | Prompt 治理平台：一次调用编排 资产生命周期 × 合规审计 × 跨模块一致性（overview / assets / audit / cross_module）。`audit` 即原 `coderef_prompt_audit` 的注入风险 + 一致性检测。纯规则、确定性、不依赖 LLM | 否 |
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

### 创新识别引擎

| 工具 | 功能 | 需要 LLM |
|------|------|---------|
| `coderef_innovation` | 识别项目创新设计 + 传播缺口，理想清单 vs 实际实现对照 | 是 |
| `coderef_asset` | 将验证过的设计固化 `WorkflowAsset` 资产（查询/导出/提交） | 是 |
| `coderef_replicate` | 复刻铺排：检测目标项目对某已固化资产（蓝图）的采用缺口，并生成可复刻指引（steps + entry_points + verified_findings）。确定性缺口判定，不自动改代码 | 否 |
| `coderef_replicate_apply` | 复刻落地（4.6 新增）：把已固化资产的复刻指引真正落到目标项目——写入 template_code 骨架 + patch_suggestion / migration_guide 说明，生成落地清单 manifest。诚实话护栏：只落地"确定性可给"内容，不自动接入目标源码；默认不覆盖已存在同名文件（冲突如实标注）；template_code 缺失明确标注待补全 | 否 |
| `coderef_asset_blueprint` | 把复刻铺排得出的确定性结论（entry_points / verified_findings）写回资产蓝图，补全为可复刻蓝图 | 否 |
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

### 人话解读平台

| 工具 | 功能 | 需要 LLM |
|------|------|---------|
| `coderef_interpret` | 把确定性格子结论翻译成非编程人员听得懂的"人话"：action=health 健康总览（人话健康分 + 高危清单 + 图谱/合规背景，未审计时诚实提示不给分）/ dashboard 健康仪表盘 HTML / wiki Wiki 生成（无 LLM 诚实阻断）/ prompt Prompt 治理总览 / assets 已固化资产解读。4.6 起 verify / verify_html 已收敛到 `coderef_verify_findings`，本平台不再转发 | 可选 |

## 快速开始

如果你是**非编程人员**：把下面这份说明交给你的编程 AI，它会帮你完成安装、配置和第一轮分析。审计、知识图谱、架构诊断等核心功能不需要 API Key；仅在需要 Wiki 文档、业务报告等 LLM 增强功能时才需准备一个 API Key（可选）。你真正要做的，是最后打开它生成的健康仪表盘和 Wiki，看懂自己的项目。

如果你**自己动手**：照下面四步走。

### 1. 安装

安装只依赖纯 Python 包，不触发任何 C 源码编译，Python 3.10-3.14 免编译直接装好。

```bash
git clone https://github.com/keaizuizui/CodeRef-AI.git
cd CodeRef-AI
pip install -r requirements.txt
```

### 2. 配置 LLM（可选）

> 审计、知识图谱、架构诊断、流程验证、变更守护、OWASP **不需要 LLM**，纯静态分析即可运行。仅 Wiki 文档、业务报告、代码审查、Prompt 资产、创新识别需要 LLM。未配置 API Key 时，Wiki 文档与业务报告这类"人话报告"会被**硬阻断**并明确提示配置，不产出降级/占位内容；代码审查、创新识别等可静态降级的能力照常给出静态结果，确定性分析服务始终可用。

**Windows 用户：**

```bash
setup.bat
```

**Linux / macOS 用户：**

```bash
export CODEREF_API_KEY="your-api-key"
export CODEREF_PROVIDER="deepseek"        # 支持: deepseek / openai / ollama
export CODEREF_BASE_URL="https://api.deepseek.com"
export CODEREF_MODEL="deepseek-v4-flash"  # 官方推荐: deepseek-v4-flash / deepseek-v4-pro
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
# 注意：Wiki 与业务报告依赖 LLM。未配置 API Key 时会被硬阻断并明确提示，
#       不产出降级/占位内容；审计、图谱、架构等确定性分析无 LLM 也照常可用。
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
│   ├── mcp_server.py                 # MCP Server 入口（32 个工具）
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
│   ├── prompt_compliance.py          # Prompt 合规审计（注入风险 + 一致性）
│   ├── prompt_governance.py          # Prompt 治理平台（生命周期 × 合规 × 跨模块）
│   ├── innovation_engine.py          # 创新识别引擎：结构化创新 + 缺口按价值挑选
│   ├── design_registry.py            # 已知设计库（别名归一）
│   ├── replicate_engine.py           # 复刻铺排引擎（蓝图 → 缺口 → 可复刻指引 → 落地）
│   ├── verify_findings.py            # 论断确定性核验（确证/证伪/部分确证/存疑）
│   ├── interpretation_platform.py    # 人话解读平台（健康 × 仪表盘 × Wiki × 核验）
│   ├── owasp_compliance.py           # OWASP LLM Top 10 合规检测
│   ├── change_guard.py               # 变更守护：git 基层 + 健康基线 + 退化检测
│   └── change_report.py              # 人话版变更报告
├── config/                           # 配置（settings.py + 本地 config.json，含密钥，已 gitignore）
│   └── settings.py                   # 集中阈值/魔数配置
├── docs/                             # 文档（config/core/utils 详细说明）
├── cache/                            # 运行时缓存（.gitignore 已忽略）
├── coderef-report/                   # 输出报告（.gitignore 已忽略）
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

## 工具可用性如何验证

我们不把"能跑通"当验收标准，而是用多重方式审查了多类自制项目与真实开源项目，并用量化指标持续证明工具测得准、不误报、不撒谎：

- **错题集回归**：维护一份真实缺陷清单（错题集）。每个缺陷都在源码中定位到文件/行号/标识符证据，并经二次核验，禁止臆造。当前错题集含 **9 个真实项目、70 个真实缺陷、49 种缺陷类型**，覆盖 6 类检测维度。逐批跑审计后，按"缺陷×维度"组合计算检出率，作为可复现的硬指标；检出率低的维度即暴露工具盲区，驱动下一轮补修。
- **端到端测试**：从项目构建知识图谱到产出审计报告，跑通完整链路，确保每个环节不短路、不丢结果。
- **正向模拟 AI 操作 / 正向模拟人类操作**：分别模拟编程 AI 调用各 MCP 工具、与非编程人员核对"工程体检单"的真实操作路径，验证工具能给出确定、可复现、可确证的结论。

**当前量化基线**（每轮回归持续刷新）：

| 检测维度 | 缺陷检出率 |
|---|---|
| 技术债（td） | 100% |
| Prompt 治理 | 100% |
| 供应链（sca） | 85.7% |
| 治理合规（gov） | 75.0% |
| Agent 安全 | 73.7% |
| 流程验证（flow_verify） | 54.5% |
| **总体** | **76.9%**（70/91 缺陷×维度） |

**边界与诚实声明**：错题集需要持续维护，我们通过"每缺陷附确定性证据 + 二次核验 + 逐轮回归刷新"控制其质量与覆盖面，并持续扩增以贴近真实世界的多样性；"正向模拟"无法完全替代真实用户在复杂、动态环境中的反馈，因此我们不把工具定位为"替代人工审查"，而定位为**确定性验证**——能确证的就确证，不能确证的一律明确标注"待人工确证"，把不确定性如实交给使用者判断。

## 杀毒误报处理

CodeRef-AI 是合法开源的安全审计工具，本仓库不含任何恶意代码。但其中的依赖扫描（SCA）本地 CVE 库曾因含英文攻击型漏洞描述，被部分杀毒软件的启发式引擎（如 `HEUR:HackTool/VulnScan`）误判为漏洞扫描工具。v4.2.7 起已将这些描述改为中文中性措辞，大幅降低误报概率。

若你的杀毒软件仍误报，请按以下方式处理：

1. **加入排除项**：将项目目录加入杀毒软件排除/白名单（Windows Defender：设置 → 病毒和威胁防护 → 管理设置 → 排除项 → 添加文件夹）
2. **厂商申诉**：向杀毒厂商提交误报申诉，说明这是合法开源审计工具（源码见本仓库），请求将 `sca_checker.py` 加入白名单；这是根治途经
3. **如实告知审计 AI**：若 SCA 结果缺失或被清理，先把杀毒排除项配好再跑审计，避免误删导致结论失真

## 项目历史

CodeRef-AI 从"一份看得懂的项目简报"出发，一步步长出静态审计、知识图谱、四大引擎与逻辑闭环。每个大版本都在回答同一个问题：让一个不懂编程的人，究竟能对自己的项目知道多少。

| 版本 | 目标 |
|------|------|
| **1.0** | 写一份完整的项目简报，让人类搞清楚他的项目是怎么回事 |
| **2.0** | 通过各类审计工具，让人类搞清楚他的项目有哪些常见问题 |
| **3.0** | 通过知识图谱和 Wiki，开始建立更详细的简报 |
| **4.0** | 通过四个引擎和四个支柱，增强工具的功能覆盖，形成逻辑闭环 |

## 更新日志

> **日志范围说明**：本更新日志仅记录**产品代码（Coderef-Ai）**的功能新增与缺陷修复。测试侧工程——`coderef-positive-test` 测试框架/脚本、`coderef-src` 测试环境同步、正向测试报告/handover——的改动**不并入本日志**，避免把"测试侧修复"误记为产品代码变更。测试侧最新状态见独立测试报告与 handover 文档。

### v4.8.9 — 操作记忆「一次恢复」与上下文丢失强制 gate

- **操作记忆一次恢复摘要**（`operation_memory` 新增 `recover`）：`coderef_operation_memory_recover` 一次调用返回关键工具位置（`env_tool`，含 git / python / wsl / coderabbit）+ 已确认的约定 / 踩坑 / 决策摘要 + 待人工确认项。AI 在上下文丢失后最小成本拿回「东西在哪儿、过去的规范是什么」，避免多次 `query`/`find` 的截断丢失
- **上下文丢失恢复升级为强制 gate**（`SKILL.md` 工作流 E）：涉及 `git` / `push` / `CodeRabbit` / `Release` 等工具或约定类操作必须先走 `recover`；禁止在未查询操作记忆前满 PATH 找工具或直接抓外部连接器（GitHub 等）。修复「AI 上下文丢失后绕过自带确定性记忆层、盲目抓外部连接器」的失败模式

### v4.8.8 — 流程验证输入校验与跨语言动态类名注入面检测

- **流程验证空 `steps` 输入校验**（`mcp_server` `_flow_verify`）：`coderef_flow_verify` 对 `steps` 做非空与类型校验——空数组/`0`/`None`/空串/纯空白统一返回结构化错误（`steps 不能为空`），不再被 `or []` 静默吞掉后返回假成功；非数组（如数字）也明确报错，与 `project_path` 校验风格一致
- **跨语言动态类名注入面检测**（`flow_verify` `cross_lang_contract_scan`）：新增 `cross_lang_dynamic_class_inject` 信号——识别 Go 侧 `map[string]any{...}` 含 `plugin`/`class` 动态键、值为运行时变量（非字符串字面量）并经 `json.Marshal` 序列化转发跨语言执行面的注入风险（chatwiki `internal/app/plugin/php/multi_pool.go:117` 盲区）。与前端硬编码 `pluginName='x'` 的断链检测互补：此为"有实现但类名由外部 payload 动态决定"的动态插件名注入面

### v4.8.7 — 盲区缺陷全量修复与健壮性加固（P0/P1/P2）

- **审计结果跨项目隔离**（`pipeline_runner`）：`audit_findings.json` 与报告文件名按项目哈希命名，`_latest_report` 按项目过滤，修复 `strategy=no_change` 复用时返回他项目报告的串扰
- **治理跨地区冲突检测**（`governance_audit` 新增 `_scan_crossregion_conflicts`）：递归索引 `rules/` 子目录 md，检出 `IRON-GOV-03` 领土主权表述冲突与 `IRON-GOV-04` 统计版本差异污染，纯文档合规项目不再系统性漏检
- **project_path 严格校验**（`mcp_server` 新增 `_validate_project_path`）：拒绝空串/相对路径（`..` 越权扫描上级目录），目录不存在返回结构化错误而非空成功，调用方可区分「路径写错」与「项目无缺陷」
- **Agent 跨语言/资源规则补盲**（`agent_security_auditor`）：新增 Java 规则（`AGENT-SEC-60~65`：SQL 注入/Spring 未鉴权/反序列化/路径穿越/SSRF/硬编码密钥）、前端安全（`AGENT-SEC-66~68`：Vue `v-html` XSS/token 拼 URL/Node 无鉴权）、资源泄漏（`AGENT-SEC-69` PIL 批量句柄泄漏、`AGENT-SEC-70` 外部长任务轮询无超时）
- **analysis_cache 可配置**（`code_analyzer`）：新增 `_resolve_cache_dir`，支持环境变量 `CODEREF_ANALYSIS_CACHE` 或 `settings.CODEREF_ANALYSIS_CACHE` 覆盖默认缓存目录，供测试/CI 隔离、避免跨项目污染
- **tool/strategy 枚举严格校验**（`mcp_server`）：`coderef_scan` 的 `tool` 维度与 `coderef_audit` 的 `strategy`（`auto`/`full`/`incr`/`no_change`）改为大小写敏感白名单校验，非法值返回结构化错误，不再静默放行（此前 `Gov`/`bogus_strategy` 被按默认执行）
- **跨地区检测 CodeRabbit 复审修复**（`governance_audit` `_scan_crossregion_conflicts`）：按 CodeRabbit 复审结论消除误报——`IRON-GOV-04` 仅当并存统计均缺乏来源/范围/版本标注时报违规（带 `数据来源`/`2022年全年` 等标注的合规趋势报告不再误报）；扫描目录排除列表移除 `docs`（现可检出 `docs/*.md` 跨区域冲突）；主权独立主体表述排除「禁止/不得/例如/假设」等否定与政策举例语境（合规红线举例不再误判为实际独立主张）

### v4.8.6 — 并行 SCA 与治理新规则

- **并行 SCA 漏洞查询**（`sca_checker`）：依赖漏洞核查改用 `ThreadPoolExecutor`（8 worker）并发查询 OSV，配合源码缓存，显著缩短大项目依赖扫描耗时，避免单个工具 900s 轮询超时
- **治理/安全规则新增**（`governance_audit`）：`IRON-SEC-01` 硬编码凭据（变量名含 Key/Secret/Token 且值为长随机串）、`IRON-SEC-18` 空鉴权中间件（闭包直接 `return` 不做权限校验）、`IRON-GOV-02` 伪科学术语检测
- **WSL 子系统工具探测**（`operation_memory`）：新增 `_locate_wsl_launcher`（先 PATH、再 `SystemRoot\System32` fallback，解决 PATH 缺 System32 时连 wsl.exe 都找不到）与 `_find_wsl_tool`（经 wsl.exe 用 `command -v` 探测、失败回退 `~/.local/bin`），可定位 WSL 内工具（如 coderabbit 在 `/root/.local/bin`）；`query(tool)` 补齐 `env_tool` 分类覆盖，避免"探测到了却查不到"。`skills/coderef-mcp/SKILL.md` 工作流 E 增加工具定位引导，避免 AI 每次满 PATH 摸索
- **CodeRabbit 复审修复**：`governance_audit` 的 IRON-SEC-01 硬编码凭据规则支持 Go `:=` 短声明、词边界防 `publicKeyHash` 类误报、错误码排除区分大小写、共享 `CREDENTIAL_VALUE` 提取器；`sca_checker` 源码缓存改为按规范化项目路径隔离，修复同一实例跨项目/重扫复用旧缓存导致漏洞利用面误判

### v4.8.5 — 盲区缺陷修复（跨语言契约 / Agent 跨语言规则 / 原子写 / 后台超时兜底）

- **跨语言插件契约断链检测（`flow_verify` 新增 `cross_lang_contract_scan`）**：前端 Vue/JS 的 `pluginName` 与 Go 的 `action` 引用同名业务插件，但 PHP `plugins/` 目录无对应实现即报断链，检出`php/worker.php` 动态插件加载失败/静默降级。插件发现限定 `php/plugins` 根目录（`os.scandir`），扫描时剪枝 `php/plugins` 与 `components/plugins` 子树避免自引用；`php_plugins` 为空时不再提前返回，缺失插件照常上报。`_cross_lang_nodes` 收敛为只输出结构化节点元信息（name/type/file/line），不再 dump docstring，消除文本子串误命中
- **Agent 跨语言规则补盲（11 条，AGENT-SEC-44~55）**：`agent_security_auditor` 新增 PHP 生产调试开关、动态类名注入、任意 action 调用、跨语言 RPC 日志泄漏、SSRF 转发、密钥泄漏，及 Go 的 ticker 泄漏、并发写、未过滤输入、RAG 图谱投毒、跨语言插件类名注入（Go→PHP 执行面）等规则，修复 chatwiki PHP/Go 跨语言缺陷漏报（agent 命中 0/9→6/9）。规则含文件级确证防误报：SSRF sink 确证、证据绑定到具体参数、同变量真值/同函数确证
- **OperationMemory 并发写原子性**：`_write_atomic` 用线程 id 唯一临时文件 + `os.replace` + 5 次指数退避重试，修复 Windows 上并发写 `os.replace` 的 WinError 5/32 竞争；`finally` 清理临时文件，per-project 锁改 `RLock` 串行完整发布序列（ledger/BRAIN/timeline）
- **后台任务超时兜底**：`coderef_scan` 纳入后台执行（HEAVY_TOOLS），后台任务超 860s 返回部分结果并建议 `resume=true`/incr 分片，修复大项目单工具 900s 轮询超时无返回
- **CodeRabbit 三轮复审修复**：agent to_report 补 `deserialization` 类别渲染、AGENT-SEC-54/DESER 证据绑定、AGENT-SEC-55 函数正则支持 receiver 方法、AGENT-SEC-51 仅认同变量 `Stop`；MCP 无部分数据不返 partial、删除不支持的 docs resume 指引、`MCP_SETUP.md` 更新 `coderef_scan` 后台契约；flow_verify 剪枝条件仅匹配 `php/plugins` 与 `components/plugins` 结尾业务目录照常扫描

### v4.8.4 — 缺陷命中回归修复（AST 静态信号 / 合并详情展示 / Agent 安全增强）

- **AST 静态信号扫描（新增 `ast_signals` 模块）**：`flow_verify` 集成 `scan_project`，针对调用图无法覆盖的缺陷补充四类可验证信号——`detect_silent_except`（except 块内无日志/无 raise 的静默吞异常）、`detect_unused_helpers`（`_` 开头私有函数从未被调用，排除测试文件）、`detect_missing_param_pass`（调用缺少关键维度/尺寸参数透传）、`detect_dir_contract_break`（目录契约命名不一致，如缓存目录 batch_id 与时间戳命名并存）。提示性信号不计 `ok` 失败，避免把"提示"误判为"流程失败"
- **Agent 安全补盲**：`agent_security_auditor` 新增 PromQL 注入（`http://`+`query` 拼接）、认证绕过（`@login_required` 缺失 + 前导断言）、空中间件空认证检测等模式，修复 aichatwiki 等 PromQL/鉴权类缺陷漏报
- **合并项详情展示**：`pipeline_runner` 新增 `_row_desc`，HIGH 表格行包含合并项 `count` 与 `detail` 关键内容（如治理违规关键词），修复合并后 detail 丢失导致报告文本缺关键词
- **注册表维度修正**：redink pipeline 断链归入 `flow_verify` 维度（此前误标 `flow_verify`+`agent`，导致 agent 维度误报漏报并存）
- **CodeRabbit 复审修复**：`ast_signals` 函数签名改用限定符号（`ClassName.method` 区分同名方法，避免签名互相覆盖导致 `detect_missing_param_pass` 误判）；`pipeline_runner` 的 agent 严重度改用 `_tier_for` 正确映射 HIGH、相邻行去重键叠加 `[risk_id]` 避免不同风险类型被误合并、MD5 噪声规则的 `detail_exclude` 补充英文安全词；`agent_security_auditor` 的 URL 拼接豁免收紧为仅 HTTP(S) 前缀（避免 `Use /help for {x}` 类 prompt 被误判）、AGENT-SEC-40 敏感路由降为 medium（仅凭路由路径为低置信度信号）

### v4.8.3 — 并行盲区补修（跨语言 / 参数契约 / 供应链 / Agent 安全）

- **Agent 安全补盲**：`agent_security_auditor` 新增 LLM 命令执行（`ShellTool` / `tool.run`）、LLM 生成 SQL 注入、FastAPI 路由认证缺失、密钥明文落盘（`save_env` / 写 `.env`）检测，修复 agent 维度漏报
- **跨语言安全检测**：`agent_security_auditor` 新增 Go（`exec.Command` / SSRF）/ Node.js（`child_process` / `eval`）/ PHP（`system` / `eval`）命令执行与反序列化检测，并下沉 SSRF、路径遍历、`--no-sandbox`、信息泄露通用模式
- **参数契约数据链**：`ast_parser` / `graph_closure` / `memory_layer` 打通调用参数 `keyword_args` 全链路（解析→加载 CALLS 边→写入图谱）；`flow_verify` 新增 `param_contract_scan` 参数契约检测与 `_normalize_params` 归一化
- **Go 知识图谱与流程补盲**：`code_knowledge_graph` 新增 Go 函数定义与调用解析；`flow_verify` 入口未命中时仍输出跨语言 Go 节点（`_cross_lang_nodes`），避免多语言项目整链短路
- **SCA 依赖补盲**：`sca_checker` 新增过时依赖 / 未锁定依赖 / 供应链运行时自动安装检测；对无依赖清单项目通过 `import` 提取第三方包（`_detect_unpinned_from_imports`），并接入 `pipeline_runner` findings
- **治理与 Prompt 补盲**：`governance_audit` 新增文档（`.md` / `.skill`）密钥明文与审查绕过表述检测（`_scan_doc_secrets`）；`prompt_compliance` 新增治理提示检测；`prompt_extractor` 扩展 `.txt` 文件扫描
- **便携工具探测**：`settings.py` 便携根子目录新增 `python`，支持自动探测项目内嵌解释器

### v4.8.2 — 证据审计修复 11 项缺陷（爆发合并 / 多语言 / Prompt 提取）

- **爆发式合并修复**：`_burst_merge` 保留组内最高严重度（此前 7 个 critical 被误降为 LOW），记录全部位置到 `locations` 字段，按 `count` 加权计数（此前 54 处爆发被压成 4 条），并保留原始 `detail`（含命中代码行）供符号级证据核验
- **SCA 多生态支持**：`sca_checker` 新增 npm（`package.json`）/ Go（`go.mod`）依赖解析并按 OSV 生态查询，修复 lodash/express 等 npm 高危依赖漏检（此前仅扫 Python）
- **跨语言安全模式**：`governance_audit` 补充 Go（`exec.Command`）/ PHP（`unserialize`、`system`）/ Java / Node.js 命令执行与反序列化检测，修复非 Python 项目关键风险漏报
- **Prompt 提取多语言**：`prompt_extractor` 扩展扫描 Markdown（`SKILL.md` / `prompts/**` `/agent.md`），修复仅扫 `*.py` 导致的提示词注入风险漏检
- **注入定位闭环**：`prompt_compliance` 注入 findings 携带源文件，保证风险可定位到具体文件
- **健康分口径统一**：`run_single` 与全量 `_compute_health` 统一单维度健康分，空项目返回 `N/A` 而非误报
- **agent 维度兜底**：`agent_security_auditor` 对含 LLM 依赖的项目仍正常产出风险，修复"有 LLM 项目健康分异常"；`blind_spot_detector` 修复空标题条目

### v4.8.1 — 换行符正规化 + CodeRabbit 修复 + 操作记忆来源声明

- **换行符正规化**：新增 `.gitattributes`（`* text=auto`），全仓文本统一为 LF 存储，根治历史遗留的 CRLF/LF 行尾混乱（此前每个文件都被误判为全量改动）
- **修复 operation_memory 写入可靠性**：写入失败显式传播（任一持久化失败返回 `error`）、数据目录可配置（`OMEM_DATA_DIR`）、原子写改用 PID+时间戳唯一临时文件、增量快照比较提到资源扫描之前、LLM 不可用时生成 pending-human 待办条目
- **修复 flow_verify 判定**：拆分"存在性确证（`ok`）"与"顺序确证（`order_confirmed`）"两个标志，避免把"在管线但顺序未确证"误标为失败；`render_report` 优先报告知识图谱缺失根因；`entry_chain` 输出稳定排序保证确定性；`cross_module_flows` 去重键改用完整文件路径；`render_html` 全部插值做 HTML 转义
- **修复 llm_integration JSON 解析**：片段提取定位整个响应中最早的结构分隔符，正确处理 LLM 在 JSON 前加说明文字导致顶层类型误判的场景
- **设计借鉴声明**：README 新增「设计借鉴」章节，操作记忆层标注结合 mindmuxai/brain.md（Apache-2.0）与 TencentDB-Agent-Memory（MIT）；`BRAIN.md` 产物与模块 docstring 同步携带来源声明
- **操作记忆固化**：审查发现 12 条 pitfall + 14 条 decision 已写入本地操作记忆层（`data/operation_memory/`，属运行数据、不入库），可被 `coderef_operation_memory_query` 在本地检索恢复

### v4.8 — 新增 AI 操作记忆层

- **操作记忆层（`coderef_operation_memory_sync` / `query` / `find` / `status`）**：为 AI 辅助编程提供"东西在哪儿、从哪儿来、到哪儿去、过去规范是什么"的持久记忆。解决对话过多后上下文丢失的课题——主要存资源位置（git 便携包、模型权重、测试工具、API 存放处、开发背景报告、外部依赖来源），而非存具体代码
- **静态审计 + LLM 提炼混合**：资源发现走确定性静态审计（`operation_memory.py`），隐性知识（决策/约定/踩坑）由 LLM 从文档提炼，无 API Key 时诚实降级为待人工确认
- **旁目录探测**：除主开发目录外，探测家目录 / 数据目录等旁目录下的资源位置，仅记录位置不记录内容，兼顾隐私
- **增量同步**：基于 mtime+size 快照比较，文件无变更时跳过全量扫描，大幅提升同步速度
- **便携工具探测**：`env_tool` 探测便携根下 bin/cmd/mingw64 等子目录，解决本地使用便携 git 等工具不在 PATH 时找不到的问题
- **工具数**：仍为 32 个 MCP 工具（新增 4 个操作记忆工具）

### v4.7.4 — memory_status 后台化

- **修复较大项目同步超时**：`coderef_memory_status` 纳入后台化重型工具（HEAVY_TOOLS），避免 120s 同步超时，改用轮询获取结果

### v4.7.3 — 扫描忽略依赖目录

- **修复超大项目审计超时**：扫描忽略 `vendor` / `bundle` 依赖目录（PHP/Node 等项目的依赖目录动辄数千文件），避免超大项目审计超时

### v4.7.2 — flow_verify 入口三段式匹配修复

- **支持 `模块.类.方法` 层级限定**：`coderef_flow_verify` 入口符号匹配支持三段式层级限定，精确定位方法级入口

### v4.7.1 — MCP 服务端 stdin 修复 + 前端 LLM 审查并行化 + JSON 解析加固

- **修复 MCP 服务端 stdin**：修正服务端标准输入读取，保证 MCP 长连接稳定
- **前端 LLM 审查并行化**：LLM 审查节点改用线程池并行（8 workers），单节点 120s 超时、总预算 600s，全项目审查耗时从串行数小时降至 170s 量级
- **JSON 解析加固**：`_try_parse_json` 优先按首字符判断数组/对象、剥离 ```` ```json ```` 代码块，修复 JSON 数组被误当对象导致 findings 从 204 掉到 242 的解析问题
- **subprocess 防挂起**：子进程调用增加防挂起机制，避免长时间无输出时卡死

### v4.7.0 — 创新复刻收口：LLM 协助排查

- **新增 `coderef_innovation_review`**：创新复刻的 LLM 协助排查工具，补上"创新确认 + 复刻排查"这条需要语义判断的链路。让 LLM 阅读源项目的管线设计（知识图谱调用链闭包）+ wiki 文档，判定三点：该设计是否确属创新 workflow（区别于已知/常见模式或静态能力标签误命中）、管线调用链与 wiki 人话描述是否一致、复刻到目标项目是否合理（提供 `target` 时）
- **wiki 来源「生成+兜底」**：优先读源项目已有 wiki（`coderef_docs_read`），无则用 WikiGenerator 生成兜底再排查
- **诚实话护栏**：确定性管线摘要（图谱调用链闭包、采用模块、入口）照常给出，不依赖 LLM；LLM 结论明确标注"AI 判断，非确定性事实"，不下"必须复刻"指令；无 API Key 时硬阻断（`is_available()` 判定），只给确定性管线摘要，不产出降级/占位判断
- **修复入口符号提取缺陷**：入口符号改为"真实源码顶层符号优先、资产蓝图 entry_points 仅作补充"。此前若蓝图声明理想模板入口（如 `with_retry`，源项目未必真实存在），会到图谱里查不到、调用链闭包为空；修复后从采用模块源码提取真实符号，能命中图谱提取确定性调用链
- **工具数**：32 个 MCP 工具（31 + 1 新增）

### v4.6.0 — 工具收敛：Prompt 治理合并 + 复刻落地（闭环收口）

- **合并**：`coderef_prompt_mgmt`（资产生命周期：版本/对比/AB）与 `coderef_prompt_audit`（合规审计）合并进统一入口 `coderef_prompt_governance`（overview / assets / audit / cross_module）。两个旧工具从 `tools/list` 移除，但保留 handler 兼容转发（返回 `deprecated` + `migrate_to` 迁移提示），旧调用不中断
- **降级**：`coderef_interpret` 移除重复的 verify / verify_html action（论断核验本就复用 `coderef_verify_findings`，两处重复实现），调用时明确降级提示迁移到 `coderef_verify_findings`，不再静默返回"未知 action"
- **补全**：新增 `coderef_replicate_apply` 复刻落地工具，把 4.4 的复刻铺排真正落到目标项目——写入 template_code 骨架 + patch_suggestion / migration_guide 说明，生成落地清单 manifest。诚实话护栏：只落地"确定性可给"内容，不自动接入目标源码；默认不覆盖已存在同名文件（冲突如实标注）；template_code 缺失明确标注待补全
- **工具数**：31 个 MCP 工具（32 − 2 合并 + 1 新增）

### v4.5.1 — 修复：DeepSeek V4 兼容 + 审查缺陷

- **修复 DeepSeek V4 空响应**：默认 base_url 改为官方 `https://api.deepseek.com`（去掉旧 `/v1` 后缀），默认模型改为 `deepseek-v4-flash`。`chat_completion` 支持 `extra_body` 传 `thinking` 参数，并在 `message.content` 为空时回退读取 `reasoning_content`（V4 推理模型输出优先写入该字段），彻底消除"调用成功但返回空串"的误判。同步更新 `setup.bat`、`README`、`MCP_SETUP.md` 与 `config/config.json`
- **修复 P0：`_discover_workflows` 缺 fallback**：原先仅走 Prompt 工作流，无 Prompt 时静默返回 `None`。现补全三级降级链（Prompt → LLM+知识库 → 规则启发式），绝不静默返回空/None
- **修复 P1：`frontend_inspector` 运行时 URL 无白名单校验（SSRF）**：`_runtime_review` 新增协议与 host 白名单校验，仅允许 http/https 且 host 为本地/内网前缀，越权 URL 一律拒绝访问并降级为静态分析
- **修复 P1：`arch_audit` 用 `or` 回退导致 0 值被忽略**：`fan_out_threshold` / `large_symbol_threshold` / `scc_min_size` 改为 `is not None` 判断，显式传 0 不再被误回退为默认阈值
- **审查并收编 `verify_findings.py` 未提交改动**：路径穿越防护（`realpath` + `commonpath` 限定项目根内）、symbols 形态健壮化（非法类型回退文本启发式提取）、`entry` 参数透传到证据标签

### v4.5.0 — 平台整合：Prompt 治理 + 人话解读（闭环落地）

- **Prompt 治理平台（`coderef_prompt_governance`）**：把 4.3 的资产生命周期（`prompt_asset_manager`）与合规审计（`prompt_compliance`）编排成统一治理视图。`action=overview` 一次调用拿到 资产生命周期 × 合规审计 × 跨模块一致性 总览；`assets` 生命周期（版本/对比/AB）；`audit` 合规审计；`cross_module` 跨模块漂移专项（同一角色/场景在多模块的同名定义漂移）。纯规则、确定性、不引入 LLM
- **人话解读平台（`coderef_interpret`）**：让非编程人员一屏看懂 AI 项目的真实状态。`action=health` 健康总览（确定性人话健康分 + 高危清单 + 图谱/合规背景，未审计时诚实提示不给分）；`dashboard` 健康仪表盘 HTML；`verify` / `verify_html` 论断人话核验（复用 `verify_findings` 确定性 verdict）；`wiki` Wiki 生成（无 LLM 诚实阻断）；`prompt` Prompt 治理总览；`assets` 已固化资产人话解读
- **诚实话解读闭环**：所有"人话结论"只来自确定性原语（健康分/审计/图谱/合规/论断核验），不引入 LLM 给结论；依赖 LLM 的能力（Wiki）在无 API Key 或无依赖时诚实阻断，绝不产出占位内容伪装成"已解读"；未审计 ≠ 无风险，绝不臆断项目健康

### v4.4.0 — 复刻铺排引擎（创新建设翼闭环）

- **复刻铺排（`coderef_replicate`）**：检测目标项目对某已固化资产（蓝图）的采用缺口，并生成可复刻指引（steps + entry_points + verified_findings）。缺口判定是确定性签名比对，只报告"有/没有"，不臆断"该不该采用"；工具是审计工具，不自动改代码
- **蓝图固化（`coderef_asset_blueprint`）**：把复刻铺排得出的确定性结论（entry_points / verified_findings）写回资产蓝图，只填确定性可填字段，不臆断 steps
- **innovation 引擎增强**：`WorkflowAsset` 支持 `blueprint` 字段（结构化复刻蓝图），`prompt_asset_manager` 支持蓝图参数，让"已验证采用的设计"沉淀为可复刻蓝图

### v4.3.0 — 确定性核验 + Prompt 合规（驾驭翼咽喉）

- **论断确定性核验（`coderef_verify_findings`）**：把 LLM/CodeRabbit 给出的论断用知识图谱 + 静态原语核验——论断引用的代码目标是否真实存在、是否在关键管线内。verdict（确证/证伪/部分确证/存疑）由确定性逻辑打出，诚实话标签来源分离，LLM 无权改结论；无确定性证据一律存疑，绝不默认 confirmed
- **Prompt 合规审计（`coderef_prompt_audit`）**：注入风险（提示注入模式化特征）+ 一致性（跨模块/同角色 Prompt 漂移）。纯规则、确定性、不依赖 LLM
- **谦逊让步**：所有新工具与既有工具的 description 明确标注"可靠性边界"（[可靠性] 段），让调用方 AI 知道哪些是确定性结论、哪些需要人工复核，不夸大能力

### v4.2.11 — CodeRabbit 复审 77 项修复（3 Critical + 44 Major + 30 Minor）

- **Critical 修复**：`wiki_generator` 空路径导致 `os.path.relpath('')` 崩溃 → 新增 `_emit` 辅助方法统一守卫；`report_renderer` 的 `_safe_link` HTML 属性注入 → 新增 `_attr` 函数转义引号；`prompt_asset_manager` 的 `_action_compare` IndexError → 先过滤再截断 + 空列表守卫
- **安全修复**：`flow_verify` HTML 插值未转义（XSS）；`agent_security_auditor` pickle 信任豁免移除 + docstring toggle 误判 + param_shadow 风险未渲染；`business_analyzer` 分隔符注入；`owasp_compliance` docstring toggle + markdown 管道符转义
- **数据安全**：`change_guard` 的 `allow_autocommit` 默认改为 False；`design_registry` 损坏注册表覆盖前先备份；`mcp_server` 任务结果不再首次读取即删除
- **功能正确性**：`code_knowledge_graph` IMPORTS 边丢弃包限定导入；`code_analyzer` 循环 IO 索引混乱 + 删除 1180 行不可达死代码；`innovation_engine` intent 过滤分母不一致；`sca_checker` [project] 段假依赖 + packaging 缺失版本比较误判 + OSV 网络失败静默；`governance_audit` 裸 def get/post 误判 + 字符串字面量 vs 变量名比较；`review_strategy` advise() 永不返回 no_change
- **稳定性**：`graph_closure` 连接泄漏 + 自动创建空数据库；`gitnexus_client` shell=True 缺包 60s 超时；`llm_integration` APIRetryError 导致 OpenAI 异常分类全跳过；`integrity_checker` / `innovation_propagation_detector` 未初始化属性
- **性能**：`memory_layer` 持久化剔除函数体 + `commonpath` O(n²) 优化；`frontend_inspector` LLM 调用新增上限 50
- **跨平台**：`wiki_generator` ENTRIES/FLOWS 路径分隔符归一化；`blind_spot_detector` 索引路径归一化
- **架构改进**：`innovation_engine` 不再访问检测器 9 个私有成员，改用公共 API；`tool_registry` ALL_AUDIT_TOOLS 从 SINGLE_TOOLS 派生消除重复维护；`tech_debt_detector` monkey-patching 改为参数传递
- **文档对齐**：LICENSE 占位符、MCP_SETUP 依赖列表与知识图谱触发路径、README API Key 可选说明、启动日志版本号

### v4.2.9 — 架构探测 + 无 LLM 硬阻断人话报告 + HTML 图谱状态修复

- **架构探测（内部增强）**：业务分析前新增轻量静态架构探测器，自动识别项目架构类型（分层/单体、Web/API、事件驱动、插件化）并提取调用图之外的入口信号（Web 路由端点、事件监听器、插件入口）。当项目入口发生在函数图之外时，入口层识别不再只依赖函数出度/入度——入口发现更贴合真实架构，业务全景分析的准确性提升
- **无 LLM 时硬阻断人话报告**：`coderef_docs`（Wiki）与业务报告（`generate_business_report` / `analyze_project_business`）依赖 LLM 才能产出，未配置 API Key 时在入口直接**明确阻断**并提示"请先配置 API Key"，不再跑完整流程、不再降级产出机械/占位内容，避免编程 AI 拿到"看似成功实为降级"的报告。底层 `LLMIntegration` 新增 `is_available()` 统一判定可用性
- **确定性分析不受影响**：审计、知识图谱、架构诊断、流程验证、变更守护、OWASP 等纯静态能力无 LLM 照常可用，服务始终可用
- **修复 HTML 报告图谱误标"未执行"**：`docs()` / `audit()` 实时管线未预置维度状态，导致 HTML 中已构建的知识图谱被误标为"未执行"。现在 `_render_html` 在维度状态为空时依据真实产物（图谱 + findings）自动补全，各维度如实展示执行状态

### v4.2.8 — 重型工具默认后台执行（适配所有 MCP 客户端的超时限制）

- **修复 MCP 工具超时（REQUEST_TIMEOUT）**：此前 `coderef_memory_sync` 等重型工具同步执行，大项目全量扫描会在 Trae 等客户端对单次 `tools/call` 的超时窗口内未完成，导致超时失败、其余工具异常。v4.2.8 起**重型工具默认后台执行**：调用立即返回 `{"status":"running","task_id":"xxxx"}`，由外层 AI 轮询 `coderef_task_status(task_id)` 取最终结果，不再撞超时
- **默认后台的工具**：`coderef_audit` / `coderef_docs` / `coderef_review` / `coderef_frontend` / `coderef_report` / `coderef_audit_advisor` / `coderef_architecture` / `coderef_memory_sync` / `coderef_memory_quality` / `coderef_owasp` / `coderef_innovation` / `coderef_asset` / `coderef_innovation_review` / `coderef_change_guard` / `coderef_change_report`；轻量工具（`coderef_scan` / `coderef_query` / `coderef_whitelist` / `coderef_docs_read` 等）保持同步快速返回
- **显式控制**：所有工具支持 `background` 参数，`background=False` 强制同步（小项目想立即拿结果）、`background=True` 强制后台；统一后台分发避免散落的 if/elif，handler 与 `coderef_task_status` 全工具可用
- **收敛统一分发**：`_call` 收敛为「统一 handler 映射 + 统一的 `background` 决策」，消除散落分支导致的重型工具被同步执行的遗漏；`_run` 统一走 `_handlers` 分发，后台线程与同步路径执行任意工具

### v4.2.7 — SCA 本地 CVE 库去敏感化（降低杀毒软件误报）

- **降低杀毒软件误报**：`coderef_audit` 的依赖扫描（SCA）本地 CVE 库（`LOCAL_KNOWN_VULNS`）漏洞描述由英文高危特征串（如 "Arbitrary code execution"、"Path traversal" 等，易触发 `HEUR:HackTool/VulnScan` 一类启发式误报）改为中文中性措辞。CVE 编号、影响版本、严重度、修复版本（`fixed_version`）全部保持不变，审计报告结论不受影响；OSV 在线查询结果不经本地文件，亦不受影响
- **说明**：CodeRef-AI 是合法开源安全审计工具，不包含任何恶意代码。若你的杀毒软件仍误报，请将项目目录加入排除项，并向杀毒厂商提交误报申诉（详见上方「杀毒误报处理」）

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

## 设计借鉴

CodeRef-AI v4.8 的操作记忆层（`BRAIN.md` 产物、判存标准、时间线机制）在设计上结合了以下开源项目的方案：

- **mindmuxai/brain.md**（Apache-2.0）—— 提供了 `BRAIN.md` 命名、「能否从代码重建」的判存标准、以及「当前理解 + 时间线」的记录结构。参考：[https://github.com/mindmuxai/brain.md](https://github.com/mindmuxai/brain.md)
- **TencentDB-Agent-Memory**（MIT）—— 提供了分层记忆与渐进式披露的思路，控制上下文 token 占用。参考：[https://github.com/Tencent/TencentDB-Agent-Memory](https://github.com/Tencent/TencentDB-Agent-Memory)

完整取舍分析见 [操作记忆层设计文档](docs/operation-memory-design/operation-memory-design.html)。

## 许可证

MIT License — 详见 [LICENSE](LICENSE)。
