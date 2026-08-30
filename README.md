<!-- AI Summary: CodeRef-AI exposes 50 MCP tools that give coding AI a deterministic "audit brain" and give non-programmers a readable view of their project. Core results (audit, knowledge graph, architecture diagnosis, flow verification, change guard, OWASP, deterministic verification, prompt compliance) are pure static analysis — no LLM, reproducible. LLM is only used for synthesis tasks (wiki, code review) and hard-blocks honestly without an API key. Builds a closed loop: verify LLM/CodeRabbit claims deterministically, replicate solidified design assets, and interpret everything in plain language for non-programmers. Best for: non-programmers who use a coding AI and want to confirm their project runs as intended, and teams who want AI that augments rather than hallucinates. -->
[![MCP Badge](https://lobehub.com/badge/mcp/keaizuizui-coderef-ai?style=flat)](https://lobehub.com/mcp/keaizuizui-coderef-ai)

# CodeRef-AI — 编程 AI 的治理外脑，非编程人员的技术助理

**Version 5.12.3** | Python 3.10+ | MCP Protocol | PolyForm Noncommercial 1.0.0

> 给编程 AI 一双确定性的眼睛，给非编程人员一张看得懂的工程体检单。

---

## 它是什么

CodeRef-AI 通过 MCP 协议暴露 **50 个工具**，同时服务两类人：

- **编程 AI 的治理外脑**：让 AI 不再逐文件读代码，而是像查数据库一样查询项目的结构、调用链与风险；持有一条 LLM/CodeRabbit 论断时，还能用静态图谱做确定性核验，再决定采不采信。
- **非编程人员的技术助理**：把看不懂的代码变成通俗的健康仪表盘、Wiki 文档和流程确证，让你不用读代码，也能确认项目有没有按你的设想运转。

它不替代 AI，而是让它看到用静态事实核验过的世界——核心结论来自代码事实，而不是大模型的猜测。

今天的能力地图上有五条主线，全部基于静态事实、确定且可复现：

1. **静态审计与知识图谱**：11 个确定性检测器把工程体检成结构化 SQLite 图谱，编程 AI 用结构化查询替代 grep 与逐文件阅读（省 10-100 倍 token），非编程人员看降噪后的重点清单。
2. **架构推回正轨**：你定义目标架构（业务层 / 技术层 / 约束），CodeRef 对比现状图谱产出 9 类确定性差距、生成可视化自由布局画布、可执行重构任务卡，并四维打分验证是否真正回到正轨。
3. **定期治理体检**：把差距转成治理工作项，走「检出 → 确认 → 修复 → 验证 → 归档」状态闭环，配历史趋势报告、Web 看板、跨仓聚合治理与定时体检——让"正确状态"可维护、可追踪，而不是一次性的重构。
4. **记忆层与操作记忆**：项目记忆（增量同步 + 语义检索 + 盲区地图 + 质量评估）与操作记忆（工具位置 / 约定 / 陷阱 + 崩溃恢复），让 AI 跨会话"记得住项目、找得回自己"。
5. **人话解读**：把确定性格子结论翻译成健康仪表盘与 Wiki，让非编程人员第一次能"看懂"自己的项目。

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

CodeRef 由四个引擎驱动，覆盖「审计 → 记忆 → 创新 → 守护」完整闭环，并补上「确定性核验 + 平台整合 + 复刻落地」能力，让闭环真正可落地：

| 引擎 | 解决的问题 | 核心工具 |
|------|-----------|---------|
| **审计引擎** | 全维度代码体检 + 图谱 + 文档 + 审查 + 论断核验 | `coderef_audit` `coderef_query` `coderef_review` `coderef_verify_findings` 等 |
| **记忆引擎** | AI 对项目「记住了什么」，增量同步 + 语义查询 + 治理 | `coderef_memory` `coderef_operation_memory` `coderef_prompt_governance` |
| **创新识别引擎** | 从项目里挖出值得复用的设计，固化为资产并复刻到其他项目 | `coderef_innovation` `coderef_asset` `coderef_replicate` `coderef_registry` |
| **变更守护引擎** | 拦截 AI 把代码改坏，输出人能看懂的变更报告 | `coderef_change_guard` `coderef_change_report` |
| **人话解读平台** | 把确定性格子结论翻译成非编程人员听得懂的"人话" | `coderef_interpret` |

## 50 个 MCP 工具

### 审计引擎

| 工具 | 功能 | 需要 LLM |
|------|------|---------|
| `coderef_audit` | 11 审计工具一键产出 + 自动降噪 + 知识图谱构建；支持 `strategy` 策略（auto 自动判定/full 全量/incr 增量裁剪重型工具） | 否 |
| `coderef_scan` | 单维度审计（11 选 1），实时安全带，快一个量级；默认后台执行，立即返回 `task_id`，用 `coderef_task_status` 轮询获取结果（`background` 参数可覆盖为同步） | 否 |
| `coderef_scan_list` | 列出 `coderef_scan` 可选的维度清单 | 否 |
| `coderef_flow_verify` | 流程合规验证：非编程人员验证「项目是否按我期望的流程执行」（入口 A 的调用管线是否覆盖步骤 B→C→D）。纯静态、确定性，只读知识图谱 CALLS 边，不依赖 LLM；状态分确证/在管线/存疑/缺失 | 否 |
| `coderef_verify_findings` | 确定性核验 LLM/CodeRabbit 论断：论断引用的代码目标是否真实存在、是否在关键管线内。verdict（确证/证伪/部分确证/存疑）由静态图谱打出，诚实话标签来源分离，LLM 无权改结论 | 否 |
| `coderef_prompt_governance` | Prompt 治理平台：一次调用编排 资产生命周期 × 合规审计 × 跨模块一致性（overview / assets / audit / cross_module）。`audit` 即原 `coderef_prompt_audit` 的注入风险 + 一致性检测。纯规则、确定性、不依赖 LLM | 否 |
| `coderef_arch_audit` | 架构腐化诊断：复用知识图谱 CALLS 边做模块级静态诊断（循环依赖/上帝模块/分层违例/异常模块规模），聚合 0–10 架构健康度。纯静态、不依赖 LLM | 否 |
| `coderef_target_arch_set` | 设置/更新目标架构 JSON（架构推回正轨的参照系），校验后落盘 `<project>/.coderef/target_arch.json`。纯确定性校验，不依赖 LLM | 否 |
| `coderef_target_arch_get` | 获取当前目标架构 JSON | 否 |
| `coderef_target_adopt` | 游离一键纳入：把 arch_gap 报出的游离/未建模模块按角色批量追加 target_modules（free=真游离孤儿 / unmodeled=被调用未建模；monitored=free/all；dry_run 预览；幂等），机械性归属动作工具化 | 否 |
| `coderef_arch_gap` | 架构差距分析（核心）：对比现状知识图谱与目标架构，输出 9 类确定性差距（职责缺失/依赖违例/循环依赖/业务断链/游离模块/上帝模块/异常规模/同构重复/目录级重复），游离模块区分真游离（free）与未建模（unmodeled）并豁免 vendor/压缩产物噪声，summary 透出全量分档计数。纯静态、复用 arch_audit，不依赖 LLM | 否 |
| `coderef_arch_canvas` | 可视化架构画布（自由布局版）：自包含 HTML 自由画布（业务/技术/代码三层节点），节点自由拖拽、任意连线、平移缩放、对齐吸附、缩略图、右键菜单、差距高亮、导出目标架构 JSON | 否 |
| `coderef_flow_canvas` | 交互式流程画布：从代码自动提取业务管线（P0-A 入口管线）+ 跨模块数据流，渲染为可自由拖拽的流程图（同一自由布局引擎） | 否 |
| `coderef_refactor_plan` | 重构任务卡：把差距清单转为编程 AI 可执行的任务卡（create_module/fix_dependency/break_cycle/implement_flow/move_module/split_module + 影响范围 + 验证标准） | 否 |
| `coderef_arch_verify` | 架构对齐验证：四维对齐度评分（职责40%+依赖30%+业务20%+健康10%）+ 差距复检；支持 changed_files 增量模式 | 否 |
| `coderef_gov_start` | 建档体检周期并导入差距为治理工作项（定期体检，借鉴 plane 的 Cycle） | 否 |
| `coderef_gov_close` | 收尾体检周期并输出本期统计（完成率/剩余/复发/豁免） | 否 |
| `coderef_gov_issues` | 查询治理工作项（预置视图 open/all/high/recurred/rejected/archived/overdue/assigned/recent） | 否 |
| `coderef_gov_transition` | 治理工作项状态流转（Detected→Confirmed→Fixing→Verified→Archived/Rejected）+ 豁免 | 否 |
| `coderef_gov_report` | 体检报告 / 治理看板（action=report 单期+跨期趋势+自包含 HTML；action=board 交互 HTML 看板，缺省落盘 gov_board.html；已合并原 gov_board） | 否 |
| `coderef_gov_pipeline` | 治理自动化流水线：在途工作项 → 任务卡 → 复验 → Verified/附缺口，全程审计轨迹 | 否 |
| `coderef_dynamic_probe` | 动态探针：静态挖掘动态信号（动态导入/装饰器注册/间接索引/entry_points），零执行被检项目 | 否 |
| `coderef_gov_board` | 治理 Web 看板（兼容别名，转发到 coderef_gov_report(action=board)）：自包含交互 HTML 看板 + 只读服务 + 状态流转回写 | 否 |
| `coderef_gov_workspace` | 多代码库聚合治理：跨仓汇总治理状态与整体健康度 | 否 |
| `coderef_gov_schedule` | 定时体检：生成可执行触发脚本 run_cycle.py + 离期检查 | 否 |
| `coderef_role_boundary` | 符号级职责越界检测：模块归属正确但符号逾越角色边界（静态信号 + 可选语义） | 可选 |
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
| `coderef_task_cancel` | 后台任务取消（协作式收尾：置 cancelled 状态，长任务在阶段汇报点尽早停止，可定位不再挂起） | 否 |

### 记忆引擎

| 工具 | 功能 | 需要 LLM |
|------|------|---------|
| `coderef_memory` | 项目记忆层：action=sync 初始化/增量同步；action=query 语义检索（向量库）+结构查询（知识图谱）；action=status 认知覆盖度+置信度+盲区地图；action=quality 质量评估（引用完整性/语义覆盖/偏差）+自动补全（由原 4 个记忆工具合并而来） | 否 |
| `coderef_operation_memory` | 操作记忆层：action=sync 增量同步；action=query 语义/结构查询；action=find 定位工具/约定/陷阱；action=status 状态概览；action=recover 恢复关键工具位置/约定摘要/待确认项；action=export 导出 Markdown 知识库+冲突检测（由原 6 个操作记忆工具合并而来） | 否 |

**记忆库落点约定**：`coderef_memory` 的认知记忆写入 `<项目根>/data/memory_state/`（`{项目hash}.json` 快照 + `{项目hash}.kb.db` 语义库）；`coderef_operation_memory` 的操作记忆写入 `<项目根>/data/operation_memory/<项目hash>/`（`ledger.json + BRAIN.md + timeline.md`）。两者均按项目 hash 隔离，属运行时产物——`data/` 已在 `.gitignore`（git 不追踪、不影响仓库干净度）；需要清空某项目记忆时删除对应 hash 文件/目录即可，测试产生的残留可整目录清理。

### 创新识别引擎

| 工具 | 功能 | 需要 LLM |
|------|------|---------|
| `coderef_innovation` | 识别项目创新设计 + 传播缺口，理想清单 vs 实际实现对照 | 是 |
| `coderef_asset` | 将验证过的设计固化 `WorkflowAsset` 资产（查询/导出/提交） | 是 |
| `coderef_replicate` | 复刻铺排：检测目标项目对某已固化资产（蓝图）的采用缺口，并生成可复刻指引（steps + entry_points + verified_findings）。确定性缺口判定，不自动改代码 | 否 |
| `coderef_replicate_apply` | 复刻落地：把已固化资产的复刻指引真正落到目标项目——写入 template_code 骨架 + patch_suggestion / migration_guide 说明，生成落地清单 manifest。诚实话护栏：只落地"确定性可给"内容，不自动接入目标源码；默认不覆盖已存在同名文件（冲突如实标注）；template_code 缺失明确标注待补全 | 否 |
| `coderef_asset_blueprint` | 把复刻铺排得出的确定性结论（entry_points / verified_findings）写回资产蓝图，补全为可复刻蓝图 | 否 |
| `coderef_registry` | 管理已知设计库，别名归一（解决 LLM 命名漂移） | 否 |
| `coderef_innovation_review` | 创新复刻的 LLM 协助排查：让 LLM 阅读源项目管线设计 + wiki，判定是否确属创新 workflow、管线与 wiki 是否一致、复刻是否合理；无 API Key 时硬阻断 | 是 |

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
| `coderef_interpret` | 把确定性格子结论翻译成非编程人员听得懂的"人话"：action=health 健康总览（人话健康分 + 高危清单 + 图谱/合规背景，未审计时诚实提示不给分）/ dashboard 健康仪表盘 HTML / wiki Wiki 生成（无 LLM 诚实阻断）/ prompt Prompt 治理总览 / assets 已固化资产解读。verify / verify_html 已收敛到 `coderef_verify_findings`，本平台不再转发 | 可选 |

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

# 10. 架构推回正轨（可选）：目标架构 → 差距分析 → 可视化画布 → 重构任务卡 → 对齐验证
coderef_target_arch_set(project_path="/path/to/project", target_arch={...})  # 一次定义"正轨"
coderef_arch_gap(project_path="/path/to/project")                              # 现状 vs 正轨的确定性差距
coderef_arch_canvas(project_path="/path/to/project")                           # 自由布局画布，浏览器里核对/微调
coderef_refactor_plan(project_path="/path/to/project")                         # 差距 → 可执行任务卡
coderef_arch_verify(project_path="/path/to/project")                           # 修复后四维打分验证是否回正轨

# 11. 定期治理体检（可选）：建档 → 导入差距 → 流转 → 收尾 → 报告 → 自动化流水线
coderef_gov_start(project_path="/path/to/project")
coderef_gov_issues(project_path="/path/to/project", view="open")
coderef_gov_transition(project_path="/path/to/project", issue_id="...", to="Fixing")
coderef_gov_pipeline(project_path="/path/to/project")  # 在途项 → 任务卡 → 复验 → Verified/附缺口
coderef_gov_close(project_path="/path/to/project")     # 收尾周期，输出完成率/复发/豁免统计
coderef_gov_report(project_path="/path/to/project")                     # action=report 单期 + 跨期趋势报告
coderef_gov_report(project_path="/path/to/project", action="board")     # action=board 交互 HTML 看板（落盘 gov_board.html）

# 12. 记忆层 / 操作记忆：了解 AI 记住了什么；上下文丢失后恢复「工具位置 / 约定 / 陷阱」
coderef_memory(project_path="/path/to/project", action="sync")    # 初始化/增量同步项目记忆（默认后台）
coderef_memory(project_path="/path/to/project", action="status")  # 项目认知覆盖度 + 盲区地图
coderef_operation_memory(project_path="/path/to/project", action="status")  # 操作记忆概览
coderef_operation_memory(project_path="/path/to/project", action="find", name="布局算法")
coderef_operation_memory(project_path="/path/to/project", action="recover") # 恢复关键工具位置/约定摘要/待确认项

# 13. 人话解读（可选）：健康仪表盘 / 健康总览（Wiki 需 LLM）
coderef_interpret(project_path="/path/to/project", action="dashboard")
coderef_interpret(project_path="/path/to/project", action="health")
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
│   ├── mcp_server.py                 # MCP Server 入口（50 个工具）
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
│   ├── wiki_generator.py             # Wiki 生成器（三级管线 + 增量同步 + 证据锚定）
│   ├── wiki_ir.py                    # Wiki 架构事实中间表示（JSON-IR 分离，schema 校验）
│   ├── wiki_compare.py               # Wiki 架构快照比对（Before/Delta/After 变更收据）
│   ├── wiki_cross_verify.py          # Wiki 模块级交叉验证（确证徽章 + Mermaid 自愈）
│   ├── flow_verify.py                # 流程合规验证（步骤级，coderef_flow_verify）
│   ├── arch_audit.py                 # 架构腐化诊断（循环依赖/上帝模块/分层违例）
│   ├── target_arch_schema.py         # 目标架构 JSON Schema（人定义的正轨）
│   ├── arch_gap_analyzer.py          # 架构差距分析器（现状 vs 目标架构）
│   ├── role_boundary.py             # 符号级职责越界检测（归属对但符号逾越角色边界）
│   ├── arch_templates.py            # 软件形态模板体系（hexagonal/modular_monolith 初稿+整理建议）
│   ├── canvas_generator.py           # 可视化架构画布（三层拖拽画布）
│   ├── refactor_task_generator.py    # 重构任务卡生成器
│   ├── arch_alignment_verifier.py    # 架构对齐验证器（四维评分）
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
├── docs/                             # 文档（config/core/utils 详细说明 + changelog 更新日志归档）
├── cache/                            # 运行时缓存（.gitignore 已忽略）
├── coderef-report/                   # 输出报告（.gitignore 已忽略）
├── setup.bat                         # Windows 配置向导
├── requirements.txt
├── MCP_SETUP.md                      # 详细配置指南
├── LICENSE                          # 5.0+ 采用 PolyForm Noncommercial 1.0.0
└── LICENSE-MIT-v4.md                # 4.X 系列（v4.9.12 及更早）仍按 MIT 授权
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

我们不把"能跑通"当验收标准，而是用多重方式审查了多类自制项目与真实开源项目，并用量化指标持续证明工具测得准、不误报、不撒谎。测试框架按五层结构由浅入深组织，逐步逼近真实使用：

- **单工具可调用（tool）**：验证每个 MCP 工具能被正确调用、输入校验符合预期。
- **多工具编排（workflow）**：验证审计→图谱→报告等既定工作流多工具编排不短路、不丢结果。
- **跨工具思路（idea）**：验证跨引擎配合，如审计发现驱动知识图谱与创新复刻。
- **已知缺陷命中（defect-hit）**：维护一份真实缺陷清单（错题集），每个缺陷都在源码中定位到文件/行号/标识符证据，并经二次核验，禁止臆造。当前错题集含 **9 个真实项目、70 个真实缺陷、49 种缺陷类型**，覆盖 6 类检测维度。逐批跑审计后，按"缺陷×维度"组合计算检出率，作为可复现的硬指标；检出率低的维度即暴露工具盲区，驱动下一轮补修。
- **修复验证负向断言（defect_clean）**：对已登记缺陷预置「修复后应不再命中」的负向断言，验证缺陷被修复后工具不再误报，补上「错题重做做对没」的双向闭环。
- **维度独立命中率**：对绑定维度逐个判定命中，暴露单维度漏报，避免「任一维度命中即 PASS」掩盖盲区。
- **注册表↔源码一致性校验（validate_registry）**：校验错题集登记与源码真实签名不漂移，防止错题集长期失真。
- **LLM 自主审查（llm-review）**：让 LLM 扮演审查者自主编排审计工具对真实项目做端到端审查，验证「AI 自己会用这些工具」这一最贴近真实使用的场景。
- **正向模拟 AI 操作 / 正向模拟人类操作**：分别模拟编程 AI 调用各 MCP 工具、与非编程人员核对"工程体检单"的真实操作路径，验证工具能给出确定、可复现、可确证的结论；并覆盖"环境工具缺失时 AI 通过操作记忆恢复"的场景，验证上下文丢失后的自愈路径。

**当前量化基线**（每轮回归持续刷新）：

| 检测维度 | 缺陷命中率 |
|---|---|
| 技术债（td） | 100% |
| Prompt 治理（prompt_governance） | 100% |
| 供应链（sca） | 100% |
| 治理合规（gov） | 100% |
| Agent 安全（agent） | 100% |
| 流程验证（flow_verify） | 100% |
| **总体** | **100%**（9 项目 35/35 命中用例） |

> 数据口径：**缺陷命中回归（defect-hit regression，v4.8.4，2026-08-14）**——9 个真实项目共 35 个缺陷命中用例全部通过；六个检测维度全部收敛至 100%（修复前基线为 41.9%）。上一轮记录的 11 处真实盲区（agent 注入/认证、flow_verify 数据流、sca 跨语言、gov prompt 污染）均已分别被本轮修复命中。按维度样本：td 5/5、gov 20/20、sca 7/7、agent 37/37、flow_verify 19/19、prompt_governance 4/4。

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
| **5.0** | 治理已被 vibecoding 搞出来的混乱管线，让项目回归正确 |

## 更新日志

> 4.X 与 5.X 系列的完整逐版本更新日志（v3.0 – v5.12.3）统一归档至 [docs/changelog/CHANGELOG.md](docs/changelog/CHANGELOG.md)；线上 README 只保留当前版本状态。

### 当前版本 v5.12.3 — 合并操作记忆层 6 工具 → coderef_operation_memory

> - **合并**：`coderef_operation_memory_sync/query/find/status/recover/export` 6 工具 → 单一 `coderef_operation_memory`，
>   以 `action=sync/query/find/status/recover/export` 区分；核心模块零改动，ledger.json / BRAIN.md 产物路径不变。
> - **删旧名不保留别名**：旧 6 名从工具列表移除，既有调用须改用 `coderef_operation_memory(action=...)`；工具总数 55 → 50。
> - **后台化矩阵不变**：`MERGE_SYNC_ACTIONS` 确保 action=query/find/status/recover/export 保持同步（recover 需即时返回），action=sync 保持后台；行为与合并前完全一致。
> - **验证**：全量回归通过，无阻断缺陷。
> - **版本号**：5.12.2 → 5.12.3（patch，暴露面精简，不改操作记忆能力）。

## 设计借鉴

CodeRef-AI v4.8 的操作记忆层（`BRAIN.md` 产物、判存标准、时间线机制）在设计上结合了以下开源项目的方案：

- **mindmuxai/brain.md**（Apache-2.0）—— 提供了 `BRAIN.md` 命名、「能否从代码重建」的判存标准、以及「当前理解 + 时间线」的记录结构。参考：[https://github.com/mindmuxai/brain.md](https://github.com/mindmuxai/brain.md)
- **TencentDB-Agent-Memory**（MIT）—— 提供了分层记忆与渐进式披露的思路，控制上下文 token 占用。参考：[https://github.com/Tencent/TencentDB-Agent-Memory](https://github.com/Tencent/TencentDB-Agent-Memory)

CodeRef-AI v4.9 的 Wiki 工具增强层（`wiki_generator` 增量同步 / `wiki_ir` / `wiki_cross_verify`）在方案思路上参考了以下开源项目的实践：

- **langchain-ai/openwiki**（MIT）—— 提供了增量同步（`.last-update.json` + 快照比对，仅重建受影响文档）与结构化元数据（front matter 头 + 确定性 index）的思路；同时以其成本失控、限流重试不健壮、输出截断静默失败等真实缺陷警示我们为增量模式补上开销封顶与诚实失败。参考：[https://github.com/langchain-ai/openwiki](https://github.com/langchain-ai/openwiki)
- **tt-a1i/archify**（Apache-2.0）—— 提供了「生成/校验分离」（LLM 先产出结构化 JSON-IR → schema 校验 → 确定性渲染）与 Last-good 门控（校验通过的产物备份，失败时保留上次可用版本）的思路。参考：[https://github.com/tt-a1i/archify](https://github.com/tt-a1i/archify)

与上述项目不同，CodeRef 保留了自己的差异化主轴：以静态知识图谱交叉验证徽章为文档可信来源，而不是依赖宿主 LLM 的自我断言。

## 许可证

CodeRef-AI 从 **5.0** 起采用**双轨授权协议**（[LICENSE](LICENSE)）：兼容 PolyForm Noncommercial 1.0.0 的使用边界，并以清晰的条款明确「企业内部自用免费、禁止转卖」。针对最常见的使用场景，这里给出边界说明：

- **企业内部自用，免费**：企业团队用本工具协助自己的软件开发、排解编程困境（无论是否以盈利为目的的自研业务），**不属于「商业再分发」**，欢迎直接使用，无需付费或额外授权。我们鼓励更多企业和团队用它来解决实际编程问题。
- **禁止转卖 / 对外提供 / 嵌入竞品**：**不得**将本软件（或经你修改的衍生版本）直接出售、**作为服务/工具对外提供并收费**，或**作为竞争性产品的一部分**嵌入其他以售卖为目标的商业软件——即防止“拿本工具去卖钱”。若你的场景确实需要对外提供商业服务，请与作者联系另行授权。
- **非商业场景免费**：个人学习、研究、开源项目、非营利机构、教育机构、政府机构等非商业目的均可自由下载、使用、修改、分发，无需付费或授权。
- 完整许可文本见 [LICENSE](LICENSE)；需要商业授权的合作请与作者联系。

**版本分界**：`v4.9.12` 及更早的 **4.X 系列** 仍按 **MIT License** 授权
（[LICENSE-MIT-v4.md](LICENSE-MIT-v4.md)），从任何 v4.* 归档检出的代码可按 MIT 使用。

## 贡献指引（Contributing）

欢迎通过 **Issues** 报告缺陷、提出建议或参与讨论；**本仓库暂不接收外部代码合并
（Pull Request）**，以保留未来商业化（商业授权）空间并规避外部贡献的版权归属问题。
详见 [贡献指引](CONTRIBUTING.md)。
