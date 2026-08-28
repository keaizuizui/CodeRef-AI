<!-- AI Summary: CodeRef-AI exposes 57 MCP tools that give coding AI a deterministic "audit brain" and give non-programmers a readable view of their project. Core results (audit, knowledge graph, architecture diagnosis, flow verification, change guard, OWASP, deterministic verification, prompt compliance) are pure static analysis — no LLM, reproducible. LLM is only used for synthesis tasks (wiki, code review) and hard-blocks honestly without an API key. Builds a closed loop: verify LLM/CodeRabbit claims deterministically, replicate solidified design assets, and interpret everything in plain language for non-programmers. Best for: non-programmers who use a coding AI and want to confirm their project runs as intended, and teams who want AI that augments rather than hallucinates. -->
[![MCP Badge](https://lobehub.com/badge/mcp/keaizuizui-coderef-ai?style=flat)](https://lobehub.com/mcp/keaizuizui-coderef-ai)

# CodeRef-AI — 编程 AI 的治理外脑，非编程人员的技术助理

**Version 5.8.0** | Python 3.10+ | MCP Protocol | PolyForm Noncommercial 1.0.0

> 给编程 AI 一双确定性的眼睛，给非编程人员一张看得懂的工程体检单。

---

## 它是什么

CodeRef-AI 通过 MCP 协议暴露 **57 个工具**，同时服务两类人：

- **编程 AI 的治理外脑**：让 AI 不再逐文件读代码，而是像查数据库一样查询项目的结构、调用链与风险；持有一条 LLM/CodeRabbit 论断时，还能用静态图谱做确定性核验，再决定采不采信。
- **非编程人员的技术助理**：把看不懂的代码变成通俗的健康仪表盘、Wiki 文档和流程确证，让你不用读代码，也能确认项目有没有按你的设想运转。

它不替代 AI，而是让它看到用静态事实核验过的世界——核心结论来自代码事实，而不是大模型的猜测。

今天的能力地图上有五条主线，全部基于静态事实、确定且可复现：

1. **静态审计与知识图谱**：11 个确定性检测器把工程体检成结构化 SQLite 图谱，编程 AI 用结构化查询替代 grep 与逐文件阅读（省 10-100 倍 token），非编程人员看降噪后的重点清单。
2. **架构推回正轨（5.0）**：你定义目标架构（业务层 / 技术层 / 约束），CodeRef 对比现状图谱产出 9 类确定性差距、生成可视化自由布局画布、可执行重构任务卡，并四维打分验证是否真正回到正轨。
3. **定期治理体检（5.1 / 5.2）**：把差距转成治理工作项，走「检出 → 确认 → 修复 → 验证 → 归档」状态闭环，配历史趋势报告、Web 看板、跨仓聚合治理与定时体检——让"正确状态"可维护、可追踪，而不是一次性的重构。
4. **记忆层与操作记忆**：项目记忆（增量同步 + 语义检索 + 盲区地图 + 质量评估）与操作记忆（工具位置 / 约定 / 陷阱 + 崩溃恢复），让 AI 跨会话"记得住项目、找得回自己"。
5. **人话解读（4.6+）**：把确定性格子结论翻译成健康仪表盘与 Wiki，让非编程人员第一次能"看懂"自己的项目。

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

## 57 个 MCP 工具

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
| `coderef_target_arch_set` | 设置/更新目标架构 JSON（5.0 架构推回正轨的参照系），校验后落盘 `<project>/.coderef/target_arch.json`。纯确定性校验，不依赖 LLM | 否 |
| `coderef_target_arch_get` | 获取当前目标架构 JSON | 否 |
| `coderef_arch_gap` | 架构差距分析（5.0 核心）：对比现状知识图谱与目标架构，输出 9 类确定性差距（职责缺失/依赖违例/循环依赖/业务断链/游离模块/上帝模块/异常规模/同构重复/目录级重复），游离模块区分真游离（free）与未建模（unmodeled）并豁免 vendor/压缩产物噪声，summary 透出全量分档计数。纯静态、复用 arch_audit，不依赖 LLM | 否 |
| `coderef_arch_canvas` | 可视化架构画布（5.0 Phase 1，5.4 自由布局版）：自包含 HTML 自由画布（业务/技术/代码三层节点），节点自由拖拽、任意连线、平移缩放、对齐吸附、缩略图、右键菜单、差距高亮、导出目标架构 JSON | 否 |
| `coderef_flow_canvas` | 交互式流程画布（5.4）：从代码自动提取业务管线（P0-A 入口管线）+ 跨模块数据流，渲染为可自由拖拽的流程图（同一自由布局引擎） | 否 |
| `coderef_refactor_plan` | 重构任务卡（5.0 Phase 2）：把差距清单转为编程 AI 可执行的任务卡（create_module/fix_dependency/break_cycle/implement_flow/move_module/split_module + 影响范围 + 验证标准） | 否 |
| `coderef_arch_verify` | 架构对齐验证（5.0 Phase 2）：四维对齐度评分（职责40%+依赖30%+业务20%+健康10%）+ 差距复检；支持 changed_files 增量模式 | 否 |
| `coderef_gov_start` | 建档体检周期并导入差距为治理工作项（5.1 定期体检，借鉴 plane 的 Cycle） | 否 |
| `coderef_gov_close` | 收尾体检周期并输出本期统计（完成率/剩余/复发/豁免） | 否 |
| `coderef_gov_issues` | 查询治理工作项（预置视图 open/all/high/recurred/rejected/archived/overdue/assigned/recent） | 否 |
| `coderef_gov_transition` | 治理工作项状态流转（Detected→Confirmed→Fixing→Verified→Archived/Rejected）+ 豁免 | 否 |
| `coderef_gov_report` | 体检报告（单期 + 跨期趋势 + 自包含 HTML） | 否 |
| `coderef_gov_pipeline` | 治理自动化流水线（5.2）：在途工作项 → 任务卡 → 复验 → Verified/附缺口，全程审计轨迹 | 否 |
| `coderef_dynamic_probe` | 动态探针（5.2）：静态挖掘动态信号（动态导入/装饰器注册/间接索引/entry_points），零执行被检项目 | 否 |
| `coderef_gov_board` | 治理 Web 看板（5.2）：自包含交互 HTML 看板 + 只读服务 + 状态流转回写 | 否 |
| `coderef_gov_workspace` | 多代码库聚合治理（5.2）：跨仓汇总治理状态与整体健康度 | 否 |
| `coderef_gov_schedule` | 定时体检（5.2）：生成可执行触发脚本 run_cycle.py + 离期检查 | 否 |
| `coderef_role_boundary` | 符号级职责越界检测（5.2）：模块归属正确但符号逾越角色边界（静态信号 + 可选语义） | 可选 |
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
| `coderef_memory_sync` | 初始化 / mtime+size 增量同步项目记忆层 | 否 |
| `coderef_memory_query` | 语义检索（向量库）+ 结构查询（知识图谱）复用项目记忆 | 否 |
| `coderef_memory_status` | 「AI 知道什么」：认知覆盖度 + 置信度 + 盲区地图 | 否 |
| `coderef_memory_quality` | 记忆质量评估（引用完整性/语义覆盖/偏差）+ 自动补全 | 可选 |
| `coderef_operation_memory_sync` | 操作记忆增量同步（ledger / BRAIN.md） | 否 |
| `coderef_operation_memory_query` | 操作记忆语义 / 结构查询 | 否 |
| `coderef_operation_memory_status` | 操作记忆状态概览 | 否 |
| `coderef_operation_memory_find` | 定位工具 / 约定 / 陷阱（跨进程并发安全） | 否 |
| `coderef_operation_memory_recover` | 恢复关键工具位置 / 约定摘要 / 待人工确认项 | 否 |
| `coderef_operation_memory_export` | 操作记忆导出 Markdown 知识库 + 冲突检测（attach 到不支持 MCP 的 LLM 界面） | 否 |

### 创新识别引擎

| 工具 | 功能 | 需要 LLM |
|------|------|---------|
| `coderef_innovation` | 识别项目创新设计 + 传播缺口，理想清单 vs 实际实现对照 | 是 |
| `coderef_asset` | 将验证过的设计固化 `WorkflowAsset` 资产（查询/导出/提交） | 是 |
| `coderef_replicate` | 复刻铺排：检测目标项目对某已固化资产（蓝图）的采用缺口，并生成可复刻指引（steps + entry_points + verified_findings）。确定性缺口判定，不自动改代码 | 否 |
| `coderef_replicate_apply` | 复刻落地（4.6 新增）：把已固化资产的复刻指引真正落到目标项目——写入 template_code 骨架 + patch_suggestion / migration_guide 说明，生成落地清单 manifest。诚实话护栏：只落地"确定性可给"内容，不自动接入目标源码；默认不覆盖已存在同名文件（冲突如实标注）；template_code 缺失明确标注待补全 | 否 |
| `coderef_asset_blueprint` | 把复刻铺排得出的确定性结论（entry_points / verified_findings）写回资产蓝图，补全为可复刻蓝图 | 否 |
| `coderef_registry` | 管理已知设计库，别名归一（解决 LLM 命名漂移） | 否 |
| `coderef_innovation_review` | 创新复刻的 LLM 协助排查（4.7 新增）：让 LLM 阅读源项目管线设计 + wiki，判定是否确属创新 workflow、管线与 wiki 是否一致、复刻是否合理；无 API Key 时硬阻断 | 是 |

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

# 10. 架构推回正轨（5.0，可选）：目标架构 → 差距分析 → 可视化画布 → 重构任务卡 → 对齐验证
coderef_target_arch_set(project_path="/path/to/project", target_arch={...})  # 一次定义"正轨"
coderef_arch_gap(project_path="/path/to/project")                              # 现状 vs 正轨的确定性差距
coderef_arch_canvas(project_path="/path/to/project")                           # 自由布局画布，浏览器里核对/微调
coderef_refactor_plan(project_path="/path/to/project")                         # 差距 → 可执行任务卡
coderef_arch_verify(project_path="/path/to/project")                           # 修复后四维打分验证是否回正轨

# 11. 定期治理体检（5.1/5.2，可选）：建档 → 导入差距 → 流转 → 收尾 → 报告 → 自动化流水线
coderef_gov_start(project_path="/path/to/project")
coderef_gov_issues(project_path="/path/to/project", view="open")
coderef_gov_transition(project_path="/path/to/project", issue_id="...", to="Fixing")
coderef_gov_pipeline(project_path="/path/to/project")  # 在途项 → 任务卡 → 复验 → Verified/附缺口
coderef_gov_close(project_path="/path/to/project")     # 收尾周期，输出完成率/复发/豁免统计
coderef_gov_report(project_path="/path/to/project")    # 单期 + 跨期趋势报告

# 12. 记忆层 / 操作记忆：了解 AI 记住了什么；上下文丢失后恢复「工具位置 / 约定 / 陷阱」
coderef_memory_status(project_path="/path/to/project")            # 项目认知覆盖度 + 盲区地图
coderef_operation_memory_status(project_path="/path/to/project")  # 操作记忆概览
coderef_operation_memory_find(project_path="/path/to/project", keyword="布局算法")
coderef_operation_memory_recover(project_path="/path/to/project") # 恢复关键工具位置/约定摘要/待确认项

# 13. 人话解读（4.6+，可选）：健康仪表盘 / 健康总览（Wiki 需 LLM）
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
│   ├── mcp_server.py                 # MCP Server 入口（57 个工具）
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
│   ├── target_arch_schema.py         # 目标架构 JSON Schema（5.0：人定义的正轨）
│   ├── arch_gap_analyzer.py          # 架构差距分析器（5.0：现状 vs 目标架构）
│   ├── role_boundary.py             # 符号级职责越界检测（5.2：归属对但符号逾越角色边界）
│   ├── arch_templates.py            # 软件形态模板体系（5.7：hexagonal/modular_monolith 初稿+整理建议）
│   ├── canvas_generator.py           # 可视化架构画布（5.0 Phase 1：三层拖拽画布）
│   ├── refactor_task_generator.py    # 重构任务卡生成器（5.0 Phase 2）
│   ├── arch_alignment_verifier.py    # 架构对齐验证器（5.0 Phase 2：四维评分）
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

> 4.X 系列已定版，完整更新日志（v3.0 – v4.9.12）已归档至 [docs/changelog/CHANGELOG.md](docs/changelog/CHANGELOG.md)。

### v5.8.0 — 业务层表达力扩展：阶段分组 × 子模块/适配器矩阵 × 分支回环（ 落地）

> 承接 （画布/目标架构对"多阶段流程 × 模块/适配器矩阵"复杂架构呈现清晰度不足）：
> working 调研工具真实流程是 8 阶段、阶段内含子模块矩阵、含众多分支回环决策点，而原
> `business_flows.steps` 仅 `{id,name}`，无法承载"阶段×子项矩阵 + 回环"这类高密度业务架构。
> 本版把业务层表达力从"线性 step"扩展为"阶段分组 + 成员矩阵 + 条件回环"，让画布能呈现
> 真实架构主干而非 4 步粗主干。
> - **schema 扩展（`core/target_arch_schema.py`）**：`steps[].kind`（`phase` 阶段/普通 step）、
>   `sub_module_refs`（阶段→子模块/适配器成员挂载，成员含 `module`/`role`/`alias`/`kind`/
>   `note`）、`branches`（step→step 条件/回环边，含 `to`/`type:loop|if|fallback`/`condition`）。
>   `sub_module_refs` 预留 `group` 嵌套槽位（子分组演进接口）；全部可选，`REQUIRED_STEP_KEYS`
>   保持 `{id,name}` 不动 → 旧 target_arch 零新增错误、向后兼容。
> - **画布渲染（`core/canvas_generator.py`）**：`kind=="phase"` 渲染为阶段分组（🎯 + 阶段序号
>   徽章），与普通 step（📈）视觉区分；`sub_module_refs` 成员沿所属阶段拉进可视图——命中图谱
>   模块直连、图谱无独立节点的适配器类补"成员占位"节点强制纳入（消解"5 个搜索 Adapter 命中 0"）；
>   `branches` 绘制粉色虚线回环/条件边（label=condition）。图例新增"阶段成员挂载/分支回环"。
> - **business_gap 成员断链（`core/arch_gap_analyzer.py`）**：`_detect_business_gaps` 新增可选
>   `member_resolved` 参数，主流程解析 `sub_module_refs` 成员是否实现，阶段声明成员全无实现时
>   追加"阶段→实现断链"提示（`member_missing`），不只是角色级断链。
> - **自证与回归**：合成验证 8 项全 PASS（schema 合法/非法校验、阶段分组节点、成员占位、
>   分支回环边、成员挂载边、成员断链）；旧 target_arch 渲染路径不变，/14/30/31/32/33
>   不劣化。
> - **CodeRabbit 评审修订（1 项 critical + 2 项 major + 1 项 minor）**：
>   `arch_gap_analyzer.py` 主流程与 `_detect_business_gaps` 统一解析 `group.items[].module`
>   并改用 `_module_exists`（文件系统+图谱双口径）判定成员实现；`target_arch_schema.py` 分支
>   后置校验防护非 dict step（防 AttributeError 崩溃）；`canvas_generator.py` 预收集步骤节点 id，
>   过滤指向不存在步骤的分支边。修复后合成验证 8 项全 PASS、`py_compile` 全过。
> - **O-C3 模块边界口径（`core/arch_audit.py`）**：同顶层父包的子包互引（如
>   `route/gin↔route/client_side↔route/chat_claw`）在业务上属同一模块/层内部的组件纠缠，而非
>   "跨模块"真环。`audit()` 现按各 SCC 成员顶层父包集合分拣：纯同父包环 → 转 `package_cycles`
>   （包内子组件环，单独透出、不计入 health 扣分）；跨顶层包真环仍保留在 `cycles` 照常扣分，
>   保留真实耦合信息、对齐 LLM"包内循环"认知。
> - **② 阈值去 overfit（`core/business_analyzer.py`）**：入口/基础设施模块判据由硬编码
>   比值（此前为避单一项目特例 1.5→2.0 收死）改为可配置常量
>   `ENTRY_DEGREE_RATIO/INFRA_DEGREE_RATIO/INFRA_MIN_DEGREE`（默认 2.0/2.0/2，即"出/入度
>   相差 2 倍以上即倾向该层"的通用显著失衡判据），`_hier_entry_modules/_hier_infra_modules`
>   可传参覆盖；注释不再引用单一项目业务名,消除工具对 working 的过拟合。
> - **CodeRabbit 二轮修订（`arch_audit.py`，1 major + 1 minor）**：① **Major**——模块内自环
>   判定由"模块内任意符号调用"改为"按符号建模块内子图，仅当符号自递归或形成环(SCC≥2)才标
>   self_loop"，避免把同文件线性调用 a()→b()→c() 误报为模块自环；② **Minor**——no_code 判定
>   由 `len(nodes)==0` 改为"是否存在非 test 模块"，图谱只剩 test/tests 节点时不再误给满分 10.0
>   而判"无代码可评"。合成验证 9 项全 PASS、`py_compile` 过；`self_loops` 口径更精确（去掉线性误报）。
> - **role_boundary 输出面细化（`core/role_boundary.py`）**：① definition 越界仅报**顶层类/
>   函数**（方法名是行为描述如 `_llm_edit`，非职责单元声明，方法级撞词全部归入 `call_hints`
>   弱信号，消除"类名撞词→类+全部方法刷屏"，如 `CheckpointManager` 曾 9 连报）；② 修复
>   `role_matchable` 计算后未接入 `def_hits` 的缺陷——role_keywords 全为中文（无法 token 化
>   提供英文锚点）的角色不判 definition，消除 business 模块因 `engine`/`research` 等跨语言
>   撞词的整模块误报；③ `call_hints` 通道降权 `error`/`logging` 支撑词（异常/日志属通用机制，
>   纯支撑词命中不构成跨角色提示）。working 实测：boundary_issues 200→3（仅剩 service 层
>   重复实现 code 层 checkpoint/chart 能力的真信号），call_hints 187→133（纯 error 撞词
>   清零）；目标产品 验收场景（waiter.cook→chef 越界）保持 PASS、本角色符号不误报。
> - **版本号**：5.7.1 → 5.8.0（minor，业务层表达力扩展 + O-C3 口径 + ② 去 overfit）。

### v5.7.1 — 架构判据口径校准：tests 排除 + 基础设施层归属 + detect 粒度/role_boundary 泛词/入口游离（新代码验收观察点）

> 承接 2026-08-28 新代码验收（O-C1/O-C2 + 观察点 a/b/c/d）：用独立 LLM 子代理与
> coderef 全量探测正面对比发现，coderef 健康分系统性低于人判（-4~-6），偏离集中在少数
> 静态口径。本版校准：
> - **O-C1** 循环/规模/分层判定排除顶层 `tests/`、`test/` 目录（`_is_test_path` 顶层片段
>   判定，不误杀 `src/utils` 等）。request 样例不再被 tests 边凑成 11 模块大环。
> - **O-C2** 新增"基础设施层"（最低层 0，`ARCH_INFRA_DIRS` 配置 i18n/log/plugin/rpc 等），
>   "公共库依赖日志/国际化"这类合理依赖不再被判下层依赖上层，目标项目 的分层违例不再
>   被批量放大。
> - **a** detect 生成初稿把 `target_modules` 展开到模块级与"目录前缀覆盖"匹配语义对齐，
>   消除"目录名 vs 子路径"粒度错位导致的初稿覆盖率极低。
> - **b** `_COMMON_DIRS` 补充 `coderef-report`/`report`/`result`/`artifacts` 等输出制品目录，
>   不再把输出目录当业务模块。
> - **c** `role_boundary` 关键词匹配由子串/前缀收敛为整词边界匹配 + 泛词（app/main/entry）
>   低置信降级，合理符号不再被泛词 `app` 误判越界；模板 `role_keywords` 与目录匹配词分离。
> - **d** `main_*`/`bin/cmd/cli`/`manage`/`__main__` 入口脚本由"真游离 free"改为 `unmodeled`
>   （带 `entry` 标记），不再误导为需删除的危险游离物。
> **CodeRabbit 评审修订（2 轮 5 发现全修）**：一轮 3 发现——① `_match_module_ids` 含 `/` 的
>   模块级 spec（模板展开的 domain/models 等）改精确路径匹配，防 basename 误配无关 `other/models`；
>   ② `_detect_unassigned` 与孪生判定的 fan_in 均排除测试调用边（防生产入口标记丢失、仅被测试
>   引用的孤本被误判活跃副本、整组收敛候选被丢弃）；③ role_boundary 泛词命中的 `uncertainty`
>   保持 high（与"静态-only 即不可靠"语义一致，防消费方把泛词命中当更高可信）+ 新增独立
>   `confidence=low` 表达低置信。二轮 2 发现——基础设施层（最低层 0）进出双向豁免分层违例
>   （跨切面横切非腐化信号，反向依赖业务属正常装配）；模块匹配/缺失判定改为一次预建索引
>   O(nodes)，模板生成千级 spec 不再退化为 O(specs×nodes) 的逐 spec 全扫 relpath。
> - **版本号**：5.7.0 → 5.7.1（patch，架构判据口径校准 + CodeRabbit 两轮评审修订）

### v5.7.0 — 软件形态模板体系：无 target 也能生成目标架构初稿与整理建议（ 解决方案落地）

> 承接  尾部（多案例回归发现"部分样本不能产出对的架构图"）：无 `target_arch` 时画布
> 缺业务/技术层退化为单层模块图。本版新增软件形态模板体系作为降级适配——识别项目属于
> 哪种常见软件形态，按模板自动生成 `target_arch` 初稿 + 文件夹整理建议（引导非强求），
> 先让"非编程人员 + 编程 AI"有架子可调，再谈三层泳道图。

- **模板体系（新增 `core/arch_templates.py`）**：内置两种样板——`hexagonal` 六边形单体
  （业务核心 domain/use-cases 与外部技术 adapters/infrastructure 解耦，依赖向内）与
  `modular_monolith` 模块化单体（按业务域平铺模块 + 共享底座，模块间依赖受控）。每类模板含
  识别特征（目录/依赖关键词）、角色骨架、期望目录与整理建议。纯静态、确定性，不依赖 LLM。
- **define-target 模板初始化（`coderef_target_arch_set` 新增 `template`/`detect` 参数）**：
  不传 `target_arch` 时，`template=hexagonal|modular_monolith` 按模板结合项目实际顶层目录生成
  `target_arch` 初稿（tech_roles 自动匹配真实模块）；`detect=true` 自动识别项目类型并套用对应
  模板（detect 优先级低于 template）。初稿仅供参考，可在此基础上完善；识别到的模板在返回值
  `template` 字段说明。
- **arch_gap 模板整理建议（新增 `templating` 输出）**：每次 `coderef_arch_gap` 自动 detect
  项目形态，对比模板期望骨架输出整理建议（缺失目录 add_dir / 未落入角色目录 review_dir），
  "按期望骨架整理文件夹有助于生成目标架构；可自主决定是否照做"。
- **版本号**：5.6.7 → 5.7.0（minor，新增模板体系功能模块）

### v5.6.7 — arch_audit cycle 口径分流 + 去样例化残留清理（ 复核）

> 承接测试  待复核点 + 过拟合审计（`20260827-过拟合审计`）：多案例回归中 self
> 实测 `arch_audit=0.0`，暴露 cycle 口径把"模块内互调自环"计入循环依赖并压健康分，
> 对大型单体过度悲观。同步清理工具代码中全部 working 特有业务名残留。

- **arch_audit 区分「模块间循环 vs 模块自环」（`core/arch_audit.py`）**：原 `self_edges`
  只要模块内存在任意符号级 CALLS 边（无需成环）就记录，单模块分量命中即判循环依赖并扣健康分
  ——模块内正常函数互调（如 `core/role_boundary` 无自引用 import 却成单元素 SCC）被误当架构腐化。
  修复：`cycles` 只保留模块间 SCC≥2 的真循环（架构腐化，照常扣分）；模块自环单独透出
  `self_loops` 字段与 `summary.self_loops` 计数（不扣健康分）。实证：self（2272 节点高耦合单体）
  health 0.0→3.0（46 中 45 自环分流，core/* 24 模块真环保留）；requests 保持 2.0 不变
  （tests/src 真循环仍在，证明不误伤模块间真环）。
- **去样例化残留清理（① + 过拟合审计 D 节）**：全量替换 LLM prompt、MCP schema description、
  SKILL、注释、docstring 中的 working 特有业务名（调研工具/洞察工具/方案工具/配置中心/创意引擎等）
  与 `目标产品`、`working` 路径示例为中性占位（如 营销助手/业务工具.main/infra_layer）；
  `_domain_flow_model` docstring 的 gptr_service 示例同步中性化。工具本体不再含任何单项目命名残留。
- **版本号**：5.6.6 → 5.6.7（patch， 复核 + 去样例化）

### v5.6.6 — target 与架构图真实化（ 覆盖引导/业务流建议/孪生真身孤本标注）

> 承接测试 ：coderef 自动生成的分层/目标架构与 working 实际架构差异大
> （覆盖率仅 0.06、业务流只有 1 条调研 4 步、source_engine/调研工具双真身被画成平级真身未标孤本），
> 照这张图治理会漏掉真实主线（web 编排、洞察→方案→创意）。

- **define-target 覆盖引导（`coderef_target_arch_set` / `arch_gap`）**：业务流为空或不足 2 条、
  角色 `target_modules` 为空时显式提示（不阻断设置）；`arch_gap` 在 `module_assigned<0.3` 或业务流不足时
  输出 `coverage_guidance`，防治理建在残缺图上。
- **业务流校验/建议（`arch_gap` 新增 `domain_flow`）**：域间业务流量透视三层——`edges`（如实
  跨域调用含证据）、`hubs`（逐域结构角色：共享层/双向枢纽/被共同依赖/业务编排源…，全程无项目名）、
  `suggestions`（去掉共享层与叶子后的主干业务流，调用数 ≥3，并附具体调用证据）。共享层在"被
  ≥50% 源域引用"时自动识别（零项目名硬编码）；"谁是技术底座"属项目语义（working 中 gptr_service
  与真实业务终点创业咨询拓扑同构，工具不擅自下结论），经 `business_flow.scope.exclude_domains` /
  `exclude_suffixes` 配置注入。保留 `flow_suggestions` 作为建议简表。真实主干（web→方案工具、
  洞察→创业咨询 25、方案→目标产品 17、调研→source_engine 43）自动排前。
- **双真身孤本标注进图（`arch_gap` 新增 `twin_identity` 差距 + 画布渲染）**：复用 `duplicate_insight`
  目录级同构对，按跨模块 fan_in 判真身（最高且>0）/孤本（=0）/活跃副本（>0 非最高）；
  画布真身绿 #22C55E、孤本灰 #A1A1AA、活跃副本橙 #F97316，节点子标签直显身份，图例同步。
  working 实测 6 组孪生目录 97 模块全标注：source_engine/engine=真身(fan_in 68)、调研工具/engine=孤本(0)。
- **回归不劣化**： 分层布局（47 层无坍缩）/  三层泳道 /  L0-L3 导航 /  统计联动
  真实浏览器全 PASS。
- **版本号**：5.6.5 → 5.6.6（patch， 修复）

### v5.6.5 — 画布层级导航「统计随视图联动」（ 真实浏览器回归）

> 承接测试  回归： 导航入口/过滤/下钻/回退在真实浏览器实测中，
> 顶部「节点/连线/差距」统计恒为全量（537/2315/357）不随层级切换更新，被误判为"有入口、无行为"。

- **定因**：`renderStats` 用 `nodes.length` / `edges.length` 全量 + 静态 `DATA.meta.summary`，
  未随 `navVisible` 过滤联动——层级过滤/下钻/回退本身在真实浏览器已生效，
  唯一失效的是统计面板反馈信号。
- **修复（`core/canvas_engine.py` `renderStats`）**：统计改按当前 `navVisible` 过滤后的可见节点/连线
  计数；全量视图保留整体差距摘要（含高中低分档），层级视图显示该层可见差距节点数，随视图切换更新。
- **真实浏览器实测（working 537 节点/2315 边）全 PASS**：all 节点537→L0 节点7→L1 业务4/技术3/代码530→
  L3 节点530→L2 下钻 run_cycle 23→回退还原 530，stats 数值与画布可见节点完全一致；差距开关在 L3 下
  独立切换高亮（开=差距色、关=还原默认蓝）；/ 泳道不劣化。
- **版本号**：5.6.4 → 5.6.5（patch， 修复）

### v5.6.4 — 画布 L0→L3 逐层下钻导航（ 治理主链①捋管线堵点）

> 承接测试 ：arch_canvas 把 537 节点全量平铺、只有三层泳道文本标签（不可交互），
> 无法"先整体后局部"逐层捋清（架构→模块→模块内逻辑→代码管线）再谈对齐/治理。
> 堵点是结构性的（工具不承载层级导航），非测试侧手法问题。

- **画布新增层级导航（arch_canvas）**：工具栏加 `L0 总览 / L1 分层 / L2 模块 / L3 代码 / 全量`
  视图切换 + 面包屑回退 + 差距开关（默认折叠、捋清后再叠加）：
  - L0 总览：只看业务定位 + 角色（先整体）；
  - L1 分层：按层聚焦（业务/技术/代码）；
  - L2 模块：双击模块节点下钻其关联子图；
  - L3 代码管线：只看代码层模块与依赖。
  基于现有 layer/type 数据实现，不动布局引擎，（分层布局）/（三层泳道）不劣化；
  flow_canvas 等无业务/技术/代码分层的画布自动不启用导航。
- **coderef-governance SKILL.md 固化 L0-L3 铁律**：核心原则新增第 2 条——捋管线必须按
  L0→L1→L2→L3 自顶向下走完，**未捋清 L0-L3 不得进入定标（define-target）与差距分析（arch_gap）**；
  场景①编排同步改为逐层下钻流程。
- **working 自证**：L0 总览 7 节点（先整体）→ L1 分层聚焦（业务4/技术3/代码530）→ L2 模块下钻
  （双击模块聚焦子图 23 节点）→ L3 代码管线（530 模块），差距 367 节点可折叠，面包屑可回退。
- **版本号**：5.6.3 → 5.6.4（patch， 修复）

### v5.6.3 — arch_gap 游离分档全量计数透出（ 回归存疑项定因）

> 承接测试 ：arch_gap 游离分级 unmodeled 实盘 0 与冒烟 free=189/unmodeled=11 不一致。
> 定因：**展示截断，非检测分支遗漏**——游离按 free 置顶排序，默认 `max_unassigned=50` 只展示
> 前 50 条（全 free），unmodeled 全量被截断；冒烟 189+11=200 恰是 `max_unassigned=200` 的截断口径。
> 实盘全量（working 图谱 + target_arch v2）：free=189 / unmodeled=265 / total=454。

- **summary 新增游离全量分档计数**：`unassigned_free` / `unassigned_unmodeled` 直接透出全量
  free/unmodeled 计数，调用方不再受 `max_unassigned` 展示截断影响（此前 `_detect_unassigned` 已算
  出两档计数但 `analyze_gap` 未解包、summary 未透出，属上一轮半成品，本轮补齐）。
- **展开参数口径说明**：控制游离列表展开的参数是 `max_unassigned`（默认 50），非 `limit`；
  传 `limit` 不影响展开属调用方口径，已在本条目与响应册说明。
- **版本号**：5.6.2 → 5.6.3（patch， 修复）

### v5.6.2 — 治理主链改造批次四收尾：gov 事务原子性 + 场景化 Skill 封装（外部 C/E）

> 承接《建议书_治理主链与工具改造》批次四最后两块：外部 C（gov 状态机原子性/幂等性）与
> 外部 E（场景化 Skill 封装层，P0 最高优先，即「少而精工具链」物化）。至此建议书 8 条改造点 +
> 5 条外部建议全部收尾。

- **gov 工作项写操作全部事务化**（外部 C，P2 中远期保底）：`GovernanceStore` 新增显式事务上下文
  管理器 `_tx()`，建档/导入/流转/豁免/改元五个写入口统一包进 BEGIN/COMMIT，异常时 ROLLBACK 不留
  半截状态——为未来多 Agent 协作（写/审/修）共享 governance.db 提供原子性保险；非法状态流转本就
  不落库（幂等），现再多一层事务兜底。
- **新增 coderef-governance 场景化 Skill**（外部 E，P0）：把 57 个 MCP 工具收敛为「治理主链
  5 阶段 × 每阶段 2–4 个高频工具」编排（map-pipeline→define-target→refactor-along→
  verify-advance→health-cycle），每阶段内含目标、工具、编排步骤、产出与常见坑；内置「意图→工具」
  快速路由表（同义词/别名→主工具，即外部 A 轻量兜底）+ gov_transition 参数速查（P2⑦）+ 真身判定
  看 fan_in 不看可达性 + 治理动作护栏（不动 git 库/备份）。
- **coderef-mcp Skill 补「场景化路由」小节**：意图→工具路由表 + 结构性锈蚀场景指引（P0②），
  与 coderef-governance 联动，编程 AI 不确定工具归属时先查表。
- **版本号**：5.6.1 → 5.6.2（治理能力增强，走 minor）

### v5.6.1 — 治理主链改造批次二三：arch_audit 真身透出 + gov_issues 去噪 + 记忆导出（建议书承接 P1⑤/⑥、P2⑦、外部 B/A/D）

> 承接《建议书_治理主链与工具改造》批次二三四，让工具链沿治理主链更顺：真身判定信息直达
> `arch_audit`、治理库封面不再被游离噪声淹没、超严格状态机有参数速查、记忆可导出为 Markdown
> 供不支持的 LLM 界面复用。纯静态、确定性，全部不依赖 LLM。

- **coderef_arch_audit 直接透出真身/孤本摘要**（P1⑤）：新增 `identity` 列表 + `identity_count`，
  复用 `arch_insight` P0-B `identity_insight`，逐类列出「同名多目录实现」的副本数、活跃真身数、
  无调用者孤本数、各副本 verdict 与优先来源文件——Skill 只看 arch_audit 健康度也不会漏真身判定。
- **coderef_gov_issues 按真实 severity 排序 + unassigned 置底**（P1⑥）：`high`/`open`/`all` 默认
  视图改为 severity 序（high>medium>low）优先、`gap_type=unassigned` 一律置底，再按 last_seen 稳定；
  治理库封面不再被 `*.min.js`/`__init__`/游离噪声刷屏，治理重点（god/cycle/duplicate）能被看到。
- **coderef_audit 补「结构锈蚀 + strategy 分场景」引导**（P0②/P1④/外部D）：description 明示
  结构锈蚀要佐以 architecture P0-B/C 与 arch_gap 的 duplicate 差距；strategy 分场景——回归复核新
  增改动用 `incr`，治理健康度体检/存量结构用 `full`，勿把治理存量当回归用 incr（存量重复不在 diff 内）。
- **coderef_gov_transition 补「参数动作速查」**（P2⑦）：description 内置 transition/reject/meta
  三种 action 所需参数速查，明示 action=meta 时勿传 to_state、非法跳转返回错误属正常。
- **新增 coderef_operation_memory_export**（外部 B）：把操作记忆的 decision/convention/pitfall
  渲染导出为 Markdown（缺省 `<项目>/data/operation_memory/OPERATION_MEMORY.md`），供 attach 到
  不支持 MCP 的 LLM 界面（Claude Project / CustomGPT）；内置冲突检测——剥掉正/否定语气词后
  主题核心相同的同类别条目若方向相反（如「禁止 X」vs「推荐 X」）标记潜在冲突，呼应双册对账防覆盖。
- **外部 A（意图路由）轻量兜底**：通过各工具 description 的「适用/不适用」硬约束分场景定界，
  后续由外部 E 场景化 Skill 封装整体路由；暂不做在线向量反射层（符合纯静态确定性原则）。
- **版本号**：5.6.0 → 5.6.1（治理能力增强，走 minor）

### v5.6.0 — 治理主链改造批次一：arch_gap 新增重复类差距 + 游离真身区分（建议书承接 P0①/P0③）

> 承接测试《建议书_治理主链与工具改造》的第一批工具层改造（P0① + P0③），让 coderef 有能力**识别并排队治理清单里最该治理的「结构性锈蚀」**（重复/孪生/真游离），而非只盯单次变更。真实项目 working 冒烟：识别出 20 个同构孪生（`duplicate`）与 7 组目录级重复（`directory_duplicate`，如 `shared/chart_engine` 与 `目标产品/chart_engine` 100% 同构）。

- **coderef_arch_gap 新增 `duplicate` 差距类型**（P0①）：同构孪生——同名实现跨目录函数体相似度 ≥60%（复用 `arch_insight` P0-C 同一切词/相似度/通用名过滤逻辑，不重写），逐条给出符号、跨目录实现位置与相似度，作为可收敛的治理候选。
- **coderef_arch_gap 新增 `directory_duplicate` 差距类型**（P0①）：目录级重复——整目录与其他目录同构（文件清单 + 函数签名双指标），识别"同构孪生目录"（如多版本并存、主线与备份目录）。
- **游离模块区分「真游离 vs 未建模」**（P0③）：`unassigned` 每条附带 `monitored=free`（fan_in=0，代码孤儿、治理候选，排最前）/ `monitored=unmodeled`（被跨模块真实调用但 target_modules 未覆盖，本质是"目标架构覆盖不足"而非孤儿，文案引导去 define-target 补 target_modules），不再把所有游离一律当孤儿刷屏。
- **游离链路自动豁免噪声**（P0③）：`vendor` / `node_modules` / `*.min.js` / `*.min.css` / `__init__` / `dist` / `build` 自动豁免，避免第三方依赖与压缩静态产物淹没真游离。
- **summary 新增 `duplicate`/`directory_duplicate` 计数**，供 `arch_verify`/`gov_start`/督办链路统一感知重复类差距规模。
- **版本号**：5.5.4 → 5.6.0（治理能力增强，走 minor）

### v5.5.4 — docs 超大项目并发提速（方案 B 能力增强：模块文档并行生成）

> 针对测试对 v5.5.3 方案 (a)「仅接受边界、未增强能力」的反馈，补上工具的**自身能力增强**：LLM 生成模块文档的主耗时段从逐模块串行改为固定并发线程池并行，超大项目（working 573 文件/20 万+ 行 62 分钟级）单次全量耗时约按并发数线性下降，降低超大项目触底超时返回 partial 的概率。

- **模块文档 LLM 调用并发化（`core/wiki_generator.py::_generate_module_docs`）**：原 `for mod in modules → self._llm_ask()` 逐模块串行（网络 IO 是主耗时段），现改为 `ThreadPoolExecutor` **固定并发池**并行执行所有模块的 LLM 生成；落盘 / `docs` 列表 / front_matter 注入 / 进度取消检查点收敛回主线程按原模块顺序执行——写文件与列表操作保持线程安全，输出顺序稳定与串行时一致
- **并发度可控**：默认 `4` 个 worker，上限 `16`，环境变量 `CODEREF_WIKI_CONCURRENCY` 可调（防误设超大并发耗尽连接）；并发数仅影响执行速度，不改变产物内容与顺序
- **取消 / 进度保持  语义**：预算阶段与落盘阶段各保留 `progress_cb` 检查点（`TaskCancelled` 仍可穿透），后台任务取消仍可在阶段点收尾
- **线程安全**：每 worker 只调用 `self._llm_ask`（只读 prompt）；共享状态 `_last_llm_error` 仅作诊断提示、并发下最后写入者胜，不影响产物正确性；`_call_count` 隶属 LLM 客户端实例
- **验证**：假 LLM（每调用固定 0.2s 延迟）10 模块并发耗时 0.63s（串行估算 2.0s，约 3.2× 加速），落盘篇数 / 内容 / front_matter / 顺序与串行一致；`py_compile` 通过
- **版本号**：5.5.3 → 5.5.4

### v5.5.3 — ~：全工具补齐对账修复（治理产出落盘 / 目标架构保真 / 排序追溯 / 后台任务取消）

> 全工具补齐对账（20260826）沉取 6 项确证缺陷集中修复：治理看板 HTML 落盘、目标架构落盘保真、缺省关闭周期、后台任务取消、图谱 callers 追溯、operation_memory 异常兜底。

- ** gov_board 落盘 HTML（`core/mcp_server.py`）**：缺省 output_dir 时自动生成本体 HTML 写盘到 `<project>/.coderef/gov_board.html`，description 明确产物路径，供人工/浏览器直接查看（不再"仅返回 JSON、无 HTML 产物"）
- ** target_arch_set 落盘保真（实证核对）**：`normalize_arch` 以 `dict(arch)` 完整复制输入再补缺省空数组，`_target_arch_set` 全量 `json.dump`，version/tech_roles/business_flows/constraints 等顶层段落完整保真落盘；实测传入该 4 段富结构，落盘文件 4 段齐全无丢失。测试观察到的"丢段"根因为 TRAE coderef MCP（stdio 长驻进程）仍运行旧版本代码——重启 MCP 并重新 set 覆盖写入即可消除
- ** gov_close 缺省关闭（`core/healthcycle.py`）**：缺省 cid 时自动定位当前 open 周期并关闭（与 gov_start 周期状态一致），无 open 周期时返回明确提示"请先用 coderef_gov_start 建档"，不再误导性报"周期不存在或已关闭"
- ** query callers 追溯补全（`core/code_knowledge_graph.py`）**：方法调用侧 `call.func_name` 常带类/模块前缀（如 `self.run_bot` / `Bot.run_bot`），此前用纯短名 `run_bot` 精确匹配失败导致 CALLS 边漏建、callers 查询返空；现 `_find_node_by_name` 精确失败后做唯一候选模糊回退，建边时优先全名匹配再回退短名，`run_bot` 可追溯到真实调用者
- ** operation_memory_sync 异常兜底（`core/operation_memory.py`）**：LLM 提炼路径整体捕获异常，返回结构化 `extract_error`，不再裸 `'"kind"'` JSON 解析报错崩溃后台任务；同时修复提炼提示模板花括号与 `str.format()` 冲突（`replace` 替代 `format`）
- ** 后台任务取消接口 + 可定位状态（`core/mcp_server.py`）**：新增 `coderef_task_cancel` 工具同步置任务为 cancelled——随后 `coderef_task_status` 返回可定位的 `cancelled`（不再无限报"running、无部分结果"），且 `_bg` 的 progress 回调实现协作式取消（下一阶段点抛 `_TaskCancelled` 尽早收尾，非普通 error）。审计/docs 等逐阶段汇报工具可真正停止；取消前已产出的增量产物（文档/报告）按模块落盘可先用
- **验证**： `.coderef/` 生成 gov_board.html； 富结构 4 段落盘归齐； 缺省关闭命中 open 周期； 模拟方法调用 `run_bot` 精确命中 `Bot.run_bot` 且 callers 返回真实调用者； sync 不再裸报错； cancel 后状态转 cancelled、协作收尾；全部改动 `py_compile` 通过
- **CodeRabbit 评审修订**：① `self/cls` 方法调用先按调用者所在类解析（`self.run_bot`→`Bot.run_bot`），避免与顶层同名函数撞 CALLS 边，并加碰撞测试；② `coderef_docs` 透传 `progress_cb` 至扫描/图谱/wiki 生成阶段，docs 后台任务具备阶段内协作取消点；③ `coderef_task_cancel` 对曾取消已收尾的任务保持 `cancelled` 终态，不退化误报 `completed`
- **CodeRabbit 二轮评审修订**：① 取消信号穿透——`TaskCancelled` 下沉定义于 `core/pipeline_runner`（被依赖方），audit/docs/wiki 各 `except Exception` 显式 re-raise，`_bg` 复用同一异常，取消不再被吞、daemon 线程不再跑到底；② WikiGenerator 逐模块生成循环加 `progress_cb` 检查点（`_generate_module_docs` 每模块先过取消点），docs 取消可在 wiki 生成内部生效；③ `self/cls` 调用改按调用者所在**模块+类**构造完整方法 id（`self.run_bot`→`method:<调用者mod>:<调用者类>.run_bot`）精确主键匹配，跨模块同名类方法不再误连（碰撞测试：modA/modB 各自 `Bot.run_bot` 均正确归属本模块）
- **CodeRabbit 三审修复 + docs 超大项目定性（ 决策 (a)）**：① `progress_cb` 透传链补齐——`_generate_all_documents`/`_incremental_update` 及其类方法委托、`_generate_full_pipeline` 主项目与子项目两处调用全部透传，消除 NameError；② docs 定性采纳「接受边界」：`coderef_docs` 描述诚实注明超大项目（实测 573 文件/20 万+ 行）单次全量可能超后台兜底 860s 返回 partial，建议走分片/增量（coderef_audit incr / 按子项目维度逐个扫描），避免大项目预期失败
- **版本号**：5.5.2 → 5.5.3

### v5.5.2 — ~：专项工具可信度修复（owasp/change_guard 降噪 + 入口/描述指引）

> 专项工具对账（20260826）暴露的工具可信度问题集中修复：owasp 静态检测 8/8 误报、change_guard 4/4 误报降噪，flow_verify 入口指引与相近符号提示，target_arch_set 描述补 role_keywords 说明。

- ** owasp 静态检测降噪（`core/owasp_compliance.py`）**：新增 `_is_false_positive` 上下文识别，过滤 mock/测试桩、错误码常量、内部路径拼接、临时文件清理、标准库导入、台账 JSON 写入、角色顺序正确、密钥从配置读取等 8 类静态启发式误报；总量 906→726；summary 明确标注"静态启发式规则误报率较高，需人工复核"。CodeRabbit 评审后修订：临时文件清理需临时/缓存路径证据（`_has_temp_evidence`，避免抑制 `os.remove(request.args["path"])` 等破坏性删除）；角色扫描回溯方向修正（从 line_no 向文件开头回溯、遇 def/class 边界停止，原 `start=i+1` 更新无效导致 system append 在 def 之后时漏检）
- ** change_guard 退化误报修复（`core/change_guard.py`）**：按行方向区分新增/删除校验（`+` 新增、`-` 删除），删除行须在新增行中无等价替代才报退化，避免把"重构/移动/新增校验"误判为"删能力"；4 个误报文件（canvas.py/db_schema.py/engine_v3.py/research_bridge.py）全部消除，真实退化仍能检出。CodeRabbit 评审后修订：重试削弱检测移除校验链前置条件（无校验链的客户端删除重试同样检出）；新增 `_is_decl_or_comment` 排除 SQL 建表/字段声明行、纯注释行，避免 SQL 字段名（如 `retry_count`）误判为删重试逻辑
- ** change_guard 路径可读性（`core/change_guard.py`）**：`_clean_diff_path` 去掉 git diff 路径两端引号、`a/`/`b/` 前缀，解码 UTF-8 八进制字节转义，输出可读相对路径（如 `创意引擎/engine_v3.py`）
- ** flow_verify 入口指引（`core/mcp_server.py` + `core/flow_verify.py`）**：description 补充 `相对目录名.符号名`（如 `调研工具.run_bot`）写法指引；入口未命中时新增 `suggest_entries` 相近符号候选（名称模糊匹配 top N，带文件路径+行号），summary 附候选减少试错
- ** target_arch_set 描述补全（`core/mcp_server.py`）**：description 明确 `tech_roles.role_keywords`（可选，角色职责关键词表，供 coderef_role_boundary 符号级职责判定；缺省时 role_boundary 会提示未配置）
- **验证**： 抽查 8 个误报位置全部过滤； 受控 diff 场景（删校验→检出、重构移动校验→不误报、重试削弱→检出）； 路径清理 4/4 通过； 实测 `调研工具.run_bot` 命中、错误入口附相近符号
- **版本号**：5.5.1 → 5.5.2

### v5.5.1 —  修复：target_arch_set 校验错误透传 + 描述与 schema 对齐 + arch_gap 显式提示

> 治理决策链核心入口（目标架构 → 差距分析）的"静默返空"根因修复：校验失败不再被 TRAE 吞成空 `[]`，调用方不再误判"无差距"。

- **校验错误透传（`core/mcp_server.py` `_target_arch_set`）**：校验失败由 `raise ValueError`（走 JSON-RPC error，TRAE 客户端吞成空 `[]`）改为返回结构化 `{status:error, error, errors:[...]}`（走 result.content 成功通道），调用方可读到含具体字段的可读错误（如 `business_flows[0] 缺少必填键: id`、`steps[0] 必须是对象`），不再与"成功返回空"混淆
- **描述与 schema 对齐（`coderef_target_arch_set` description）**：明确 `business_flows` 每项必填 `id/name/steps`、`steps` 每项必须是 `{id,name}` 对象（非字符串）、可选 `tech_roles` 引用已定义角色 id；`tech_roles` 每项必填 `id/name/target_modules`；`constraints` 每项必填 `from/to/rule`——按描述构造即能通过校验
- **arch_gap 显式提示（`core/mcp_server.py` `_arch_gap`）**：目标架构未设置（读存储抛错）或传入的 target_arch 无效（校验失败）时，返回 `{status:error, error:"目标架构无效（N 条）..."}` 显式提示，不再静默空、不再链式污染为"无差距"
- **验证**：非法样例（flow+字符串 steps）返回 7 条可读错误；合法样例（id/name + {id,name} 步骤）成功落盘 `{roles:3, flows:1, constraints:3}`；arch_gap 未设置/无效 target 均返回显式 error
- **版本号**：5.5.0 → 5.5.1

### v5.5.0 —  + ：画布标签可读性揭示策略 + 业务/技术/代码三层架构对齐

> 让自由画布从"一堆点线看不懂"走向"进门看懂"：标签展示分级揭示，架构按业务/技术/代码三层泳道对齐表达。

- ** 节点标签可读性/揭示策略（`core/canvas_engine.py`）**
  - **LOD 标签分级**：缩放低于 0.30 时自动隐藏标签文字只留色块/图标，杜绝"全局马赛克不可读"；缩放回升即恢复
  - **长标签省略 + 悬停揭示**：标签超宽 `ellipsis` 截断，节点 DOM 携带 `title` 完整名/路径，悬停即显全名
  - **最小可辨尺寸 + 对比度保障**：`fitView`「适应」后缩放下限保正节点最小可辨像素；节点背景 `#1E293B` + 标签文字 `#E2E8F0` 高对比可读
- ** 三层架构对齐（业务/技术/代码泳道 + 跨层 trace）**
  - **数据层 `tier` 语义**：节点支持 `tier`（business/service/code），边支持 `rel`（flow/align/land/depends）
  - **三层横向泳道渲染**：`render_canvas` 按 tier 分三个横向泳道 + 层标题（业务层·业务/用户旅程 / 技术层·服务角色 / 代码层·落地构件），默认视图即呈现三层结构
  - **泳道内保持业务流拓扑**：泳道内仅按本 tier 内部 flow 边算拓扑深度排序（`_split_layer_rows` 增 `topo_always`），保证"进门→点餐→吃饭→结账"链路从左到右可读，不被跨层边污染
  - **跨层 trace 边可视化**：align（业务→技术，橙虚线）、land（技术→代码，绿点线）、depends（代码内部，灰实线）差异化配色/线型，纵向上逐层可追踪
- **验证**：`restaurant_layers.json`（餐厅三层样例）端到端——三层泳道矩形 3 个且层标题可见；节点按 tier 三行纵排（y=124/428/732）；业务链路 x 顺序 1198→1406→1614→1822；跨层 align×6 + land×7 trace 边可见；默认视图 scale≈1.1 文字可读；缩小至 0.2 触发 LOD 文字隐藏
- **版本号**：5.4.4 → 5.5.0

### v5.4.4 —  修复：自由画布分层布局/默认视图 Y 轴坍缩

> 解决 （前端渲染缺陷，首屏即不可用）：分层布局与默认初始视图把全部节点压成一条水平线。

- **`core/canvas_engine.py` 分层布局 Y 轴坍缩修复**：根因是节点 layer 趋同（`_norm_node` 把 `layer` 回退到 `type`，arch_canvas 产出的节点 type 多为 module/default），`_layout_layered` 把所有节点归入同一层 → 只 X 分布、Y 全相等 → 一条线；默认初始布局即坏。新增按依赖 DAG 最长链深度（Kahn 拓扑）对单层/超宽节点排序并拆子行（`_node_depths` + `_split_layer_rows`，Python 端与 JS 端 `layoutLayered` 同步），Y 维逐行二维展开；单行上限 12 节点、总宽限幅占画布可辨宽；已显式定位的节点仍保留为锚点
- **「适应」最小可辨识尺寸下限**：`fitView` 加缩放后节点最小可辨像素下限（24px），防止超大图把节点缩成不可辨，首屏与「适应」后均保持可读
- **验证**：84 节点同层场景由 1 行坍缩 → 拆 7 行，Y 展开 [230,1490]，X 轴每行 12 节点；力导向对照（二维展开正常）不受影响
- **版本号**：5.4.3 → 5.4.4

### v5.4.3 —  强化：review 首调 JSON 命中率（零额外 LLM 耗时）

- **`core/code_review.py` 首调命中强化**：system prompt 更强制（明确"输出会被程序直接解析、违反即失败、无问题输出 []"）+ 重试 prompt 更严格（只输出 JSON 数组、禁 Markdown 标记），从根因减少 v4-flash 输出散文导致的 JSON 解析失败
- **零额外成本**：不增加 LLM 调用次数、不增加耗时，仅提升首调直接命中 JSON 的概率（冒烟验证：首调即命中、未触发重试）
- **版本号**：5.4.2 → 5.4.3

### v5.4.2 — RAE 记忆 × coderef 执行记忆双向落地

> 解决"RAE 记忆与 coderef 执行记忆零互通"：开发 AI 的架构规则与操作规程印不进执行记忆，可恢复性差。

- **方向 A（投放页，主）**：新增 `CODEREF.md` 操作红线与规程投放页，`AGENTS.md` 引入之——任何编程 AI 读仓即自然读到操作守则，规则随项目天然传播到执行记忆
- **方向 B（自动同步，辅）**：`config/settings.py` 新增 `OMEM_AUTO_SYNC_ON_GOV` 开关，治理流程（audit/scan 等）收尾时后台线程增量同步操作记忆（`pipeline_runner._auto_sync_om_on_gov()`，30s 去重，不阻塞高频路径）
- **版本号**：5.4.1 → 5.4.2

### v5.4.1 — CodeRabbit 复审 4 findings：撤销语义/坐标偏移/自动布局不动已定位/角色高亮

> 对 v5.4.0 自由布局画布进行 CodeRabbit 复审，采纳 4 findings（2 critical / 1 major / 1 minor）。

- **`core/canvas_engine.py` undo/redo 语义修正**（critical）：撤销/重做栈原记录"修改后"状态，回退语义错误。重构为 `recordPre()`（mutation 前记录一次）`/`commitChange()`（mutation 后入栈）成对模式，并把全部 mutation 点接入——节点拖拽、方向键微调、任意连线、增删节点/连线、改属性、复制节点、导入 JSON——现在每个操作撤销时都回到操作前状态；无实际位移的单击不再污染历史栈
- **`core/canvas_engine.py` 鼠标坐标 44px 偏移**（major）：`toLocal`/`toWorld` 视口变换将 client 坐标换算为 canvasWrap 局部坐标，修复工具栏导致的连线/吸附/右键落点系统性偏差
- **`core/canvas_engine.py` 自动布局不再覆盖已定位节点**（minor）：`auto_layout` 与分层/力导向布局仅对未定位节点（x=0 且 y=0）赋坐标，已显式定位的节点作为固定锚点保留，避免覆盖调用方排好的位置；力导向布局中锚点只推挤可动节点、自身不移位
- **`core/canvas_generator.py` 角色节点高亮**（critical）：缺角色判定由 `id.endswith(":")` 改为 `id.startswith("role:")`，缺失角色节点恢复红色高亮
- **版本号**：5.4.0 → 5.4.1

### v5.4.0 — 自由布局画布引擎：架构图/流程图可自由拖拽

> 自研轻量实现纯 HTML/CSS/JS + SVG 自由布局画布，零外部依赖、离线可用。拖拽、任意连线、平移缩放、缩略图、右键菜单、自动布局等交互能力为业界自由/流程画布工具的通用范式，实现为独立编写的自有代码，不复制或翻译任何第三方实现。

- **`core/canvas_engine.py` 自由布局画布引擎**（新增）：纯 HTML/CSS/JS + SVG 自包含、零外部依赖、离线可用。完整交互：节点自由拖拽（网格 + 节点边缘对齐吸附）、端口拖出任意连线成流（自动选端口）、画布平移/缩放（滚轮 + 按钮）、缩略图导航（mini-map 点击跳转）、右键菜单（添加/复制/删除节点、连线样式、自动布局、导出 JSON）、快捷键（Ctrl+Z 撤销 / Ctrl+Shift+Z 重做 / Delete 删除 / Ctrl+A 全选 / 方向键微调 / Ctrl+± 缩放）、属性面板（编辑节点/连线 label、颜色、props JSON）、分层/力导向自动布局、导出/导入画布 JSON、撤销/重做历史栈
- **`core/canvas_generator.py` 架构画布升级为自由布局版**：三层布局（业务步骤 → 技术角色 → 代码模块）改为自由画布节点 + 连线；差距高亮保留（游离灰/循环黄/缺失红虚线/依赖违例红连线）；业务步骤→角色映射、角色→模块归属、模块→模块依赖均以可拖拽连线呈现
- **`core/flow_canvas.py` 交互式流程画布**（新增，MCP `coderef_flow_canvas`）：从代码自动提取业务管线（`pipeline_insight` P0-A 入口管线，沿 CALLS 归纳阶段序）+ 跨模块业务数据流（`cross_module_flows`），渲染为可自由拖拽的流程图；每条管线一个图层，步骤按序连线，跨模块数据流带调用次数标签
- **MCP 工具**：`coderef_arch_canvas` 升级为自由布局版；新增 `coderef_flow_canvas`（project_path / output_dir / max_entries / max_depth，默认后台执行，加入 HEAVY_TOOLS）
- **版本号**：5.3.4 → 5.4.0

### v5.3.4 — CodeRabbit 复审 4 findings：测试目录识别/跨目录判定/import 歧义/同构空集

- **`_is_test_file` 根级测试目录识别**（minor）：路径段拆分检查 `test/tests/测试` 目录段，修复根级 `tests/foo.py`（相对路径无前导斜杠）漏判为测试文件——`source_engine/engine.py` 因此被正确标为"活跃真身"（被引用 17，引用方含 `research_queue.py`）
- **`duplicate_insight` 跨目录判定**（major）：改用相对路径目录（`_rel_dir`）判断跨目录，修复 `apps/worker` 与 `legacy/worker` 同名 basename 被 `_mod_of` 合并误判同目录而漏报；`_mod_of` 仅用于报告展示
- **`_resolve_import_target` 歧义兜底**（major）：tail fallback 收集全部匹配模块 ID，仅恰好一个匹配时返回，歧义返回空——不再依赖 `mod_ids` 集合迭代顺序选目标
- **`_dir_isomorph_insight` 空函数集跳过**（major）：任一侧函数签名集为空时跳过该目录对，避免空集 Jaccard=1.0 把纯文件目录误判为同构
- **版本号**：5.3.3 → 5.3.4

### v5.3.3 —  业务级判定增强：真身/重复聚焦业务类，目录级同构识别

- ** P0-B 真身判定业务级增强**（r9 交叉对比反馈）：聚合范围从"通用方法名"改为"业务级同名类"——过滤 `__init__`/`to_dict`/`execute`/`render` 等通用方法名噪音与 Config/Result/TestCase 等通用类名；每个副本报告引用方详情（文件:行 + 符号名，排除测试文件），判定区分"生产入口候选（无被调用者）" / "活跃真身" / "仅测试引用"；业务类名（Bot/Engine/Workflow 等后缀）优先展示，避免双真身被 3+ 副本通用类挤出 top 列表
- ** P0-C 重复识别目录级同构**：新增目录级同构比对——按相对目录聚合文件清单 + 函数签名，双指标 Jaccard 相似度 ≥ 阈值判定"同构重复候选"（如 `调研工具/` 与 `source_engine/` 全目录同构：文件 0.83 / 函数 0.96），报告目录 A/B、相似度、文件数
- **图谱节点 ID 相对路径化**（真实屎山治理发现）：模块/函数/类/方法节点 ID 前缀由 basename 改为相对 project_path 的路径，修复跨目录同名文件（如 `source_engine/engine.py` 与 `调研工具/engine.py`）被 `INSERT OR REPLACE` 互相覆盖导致的图谱漏扫；`_resolve_import_target` 支持点分路径精确匹配 + 跨目录同名模块兜底
- **FlowVerifier 函数体提取修复**：`graph_closure.load_graph` 节点查询补 `end_line` 字段，修复函数体切片为空导致的重复识别失真
- **版本号**：5.3.2 → 5.3.3

### v5.3.2 —  集成加固：图谱 db 直喂洞察，消除二次探测竞态

- ** 集成加固**（r8 反馈）：`Pipe.architecture` 把 `_build_kg` 刚构建的图谱 `db_path` 直接传给 `insight_markdown`，消除 insight 内部二次 `ensure_kg` 探测/重建的时序竞态——MCP 长驻进程下首调即出洞察（不再偶发 790B 壳）
- **洞察为空不再静默**：若图谱不可用导致洞察为空，`errors` 明确记录原因（"洞察为空（图谱 … 不可用）"），不再无声产出壳报告
- **版本号**：5.3.1 → 5.3.2

### v5.3.1 —  修复：MCP 中文路径编解码

- ** MCP 中文 output_dir 写盘乱码**：`Server.run()` 强制 stdin/stdout 为 UTF-8——TRAE 经 stdio 发送的 JSON 是 UTF-8 字节，Windows 下 stdin 若按 GBK 解码，中文 output_dir（如"测试归档"）会被误解码成乱码目录名（如 `娴嬭瘯褰掓。`）。修复后中文路径正确落盘
- **版本号**：5.3.0 → 5.3.1

### v5.3.0 — 架构洞察：管线/真身/重复自动产出人话结论（，P0 级）

- **P0-A 管线自动梳理**（新增 `core/arch_insight.py`）：自动发现入口（无被调用方 + 启发式），沿 CALLS 归纳阶段序管线（x→y→z 带文件/行号/说明），输出 Markdown 表格；另附跨模块业务数据流
- **P0-B 真身/入口判定**：同名多目录实现（如 check_plan_coverage 同时存在于多个子系统），报告各副本被谁引用 / 是否活跃；仅无被调用者的 root 副本标"生产入口候选"（dunder 特殊方法单独标注），无 root 时只标"被引用最多的副本"，不单凭被引用数推断入口
- **P0-C 重复/同构识别**：同名函数跨模块实现按函数体相似度分区——相似度 ≥60% 的副本聚成独立"重复实现簇"（建议收敛），未配对（低相似度）副本归入"同名候选"（仅同名、契约可能不同，不推荐合并）
- **`coderef_architecture` 报告升级**：不再只是"790B 壳"，自动追加三段洞察；`insight_llm` 参数可选追加 LLM 人话总结（需 API Key，缺省静态结果完整可用；非布尔值拒绝）
- ** `coderef_arch_canvas` 后台化**：加入 HEAVY_TOOLS 默认后台执行（超大项目不再同步撞 MCP 超时），支持 `background=false` 强制同步
- ** cache 收口**：清理主仓历史残留图谱库（10.9MB），图谱库已随 project_path 落位
- **CodeRabbit 复审**：6 条 findings 已全部修复——P0-B/C 判定严谨性、洞察失败显式渲染、insight_llm 布尔校验、P0-C 重复簇按相似度分区（未配对副本不误并入重复簇）、gov_pipeline 流水线契约文档对齐（仅 Confirmed/Fixing）
- **版本号**：5.2.3 → 5.3.0

### v5.2.3 — 真实屎山扫描落点修复（r6 红线段落）

- **P1-1 `coderef_architecture` 报告落点可控**：报告默认落 `<project_path>/coderef-report/`（不再写 MCP 进程 cwd 的 `coderef-report/`），支持 `output_dir` 显式外置——避免真实多项目/跨仓协作污染对方主仓
- **P2-1 知识图谱库跟随被检项目**：`cache/kg/*.db` 由安装根迁移至 `<project_path>/cache/kg/`，读方经 `CodeKnowledgeGraph.db_path` 一致定位，不再把 9.7MB+ 图谱库写进调用方 cwd
- **版本号**：5.2.2 → 5.2.3

### v5.2.0 — 5.2 三项预想落地 + 治理自动化流水线贯通

- **符号级职责越界检测**（新增 `role_boundary`）：模块归属正确但符号逾越角色边界（如 waiter.py 里有 cook()），静态信号（定义/调用关键词命中）+ 可选语义判定接口，纯静态确定性
- **治理自动化流水线**（新增 `gov_pipeline`）：把在途工作项串成可追踪闭环——状态→Fixing、凭差距快照生成任务卡（复用 refactor_task_generator）、调 arch_alignment_verifier 复验、达标自动 Verified / 未达标保持 Fixing 附缺口，全程写活动日志
- **动态探针**（新增 `dynamic_probe`）：补全静态图谱盲区，挖掘动态信号（动态导入 / 装饰器注册 / 间接索引 / entry_points），默认零执行被检项目代码
- **Web 看板应用态增强**（`gov_webdash`）：自包含交互 HTML 看板（筛选 / 详情 / 状态流转按钮）+ `/api/transition` 数据回写接口（仅限本机）
- **多代码库聚合治理**（新增 `gov_workspace`）：跨仓汇总治理状态，输出整体健康度视图
- **定时体检实跑落地**（`gov_schedule`）：从"产出 cron 片段"升级为生成可直接运行的 `run_cycle.py` 触发脚本 + `--check` 离期检查
- **新增 MCP 工具**（6 个）：`coderef_gov_pipeline` / `coderef_dynamic_probe` / `coderef_gov_board` / `coderef_gov_workspace` / `coderef_gov_schedule` / `coderef_role_boundary`
- **开发计划**：`docs/5.2-plan.md`（内部规划，未公开）
- **版本号**：5.1.0 → 5.2.0（工具数 48 → 54）

### v5.1.0 — 5.1 定期体检：从"一次性重构"升级为"定期体检"

- **治理持久层**（新增 `governance_store`）：SQLite 存储体检周期 / 治理工作项 / 活动日志，状态机 Detected→Confirmed→Fixing→Verified→Archived/Rejected + 去重/复发/豁免语义
- **体检周期编排**（新增 `healthcycle`）：建档 / 导入差距 / 流转 / 豁免 / 收尾 / 报告
- **预置视图**（新增 `gov_view`）：open / all / high / recurred / rejected / archived / overdue / assigned / recent 固定查询入口
- **报告与趋势**（新增 `gov_dashboard`）：单期报告 + 跨期趋势 + 自包含 HTML（零 CDN）
- **新增 MCP 工具**（5 个）：`coderef_gov_start` / `coderef_gov_close` / `coderef_gov_issues` / `coderef_gov_transition` / `coderef_gov_report`
- **开发计划**：`docs/5.1-plan.md`（内部规划，未公开）
- **版本号**：5.0.0 → 5.1.0（工具数 43 → 48）

### v5.0.0 — 5.0 启动：架构推回正轨（Phase 0-2 核心闭环）

- **目标架构 JSON Schema**（新增 `target_arch_schema`）：定义"人定义的正轨"标准结构（业务层 business_flows / 技术层 tech_roles / 约束 constraints），零依赖手写校验，结构化错误返回
- **架构差距分析器**（新增 `arch_gap_analyzer`）：对比现状知识图谱与目标架构，输出 7 类确定性差距（missing 职责缺失 / dependency_violation 依赖违例 / cycle 循环依赖 / business_gap 业务断链 / unassigned 游离模块 / god_module 上帝模块 / large_module 异常规模），复用 arch_audit 不重写
- **可视化架构画布**（新增 `canvas_generator`，Phase 1）：自包含 HTML 三层画布（业务/技术/代码层），拖拽定义归属、业务→技术连线、差距高亮、导出目标架构 JSON，零外部依赖离线可用
- **重构任务卡生成器**（新增 `refactor_task_generator`，Phase 2）：差距清单 → 编程 AI 可执行任务卡（create_module/fix_dependency/break_cycle/implement_flow/move_module/split_module + 图谱影响范围 + 验证标准）
- **架构对齐验证器**（新增 `arch_alignment_verifier`，Phase 2）：四维对齐度评分（职责40%+依赖30%+业务20%+健康10%）+ 差距复检，支持 changed_files 增量模式
- **新增 MCP 工具**（6 个）：`coderef_target_arch_set` / `coderef_target_arch_get` / `coderef_arch_gap` / `coderef_arch_canvas` / `coderef_refactor_plan` / `coderef_arch_verify`，全部纯静态、确定性、轻量同步
- **开发计划**：`docs/5.0-plan.md`（内部规划，Phase 0-2 详细设计，未公开）
- **版本号**：4.9.12 → 5.0.0（工具数 37 → 43）

### v4.9.12 — 修复 Coderef-Test 测试报告（20260823-v4.9.11-r5）遗留项

- **P2 review 占位率收敛与降级信息增强**（`code_review`）：prompt 加"内容截断属正常"提示（system/diff/batch），从根因减少 v4-flash 因文件截断输出的散文；重试仍失败时改用 `_degraded_comment_from_text`，把 LLM 散文线索压缩进降级评论 detail
- 版本号：4.9.11 → 4.9.12

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
