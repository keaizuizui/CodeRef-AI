<!-- AI Summary: CodeRef-AI exposes 54 MCP tools that give coding AI a deterministic "audit brain" and give non-programmers a readable view of their project. Core results (audit, knowledge graph, architecture diagnosis, flow verification, change guard, OWASP, deterministic verification, prompt compliance) are pure static analysis — no LLM, reproducible. LLM is only used for synthesis tasks (wiki, code review) and hard-blocks honestly without an API key. Builds a closed loop: verify LLM/CodeRabbit claims deterministically, replicate solidified design assets, and interpret everything in plain language for non-programmers. Best for: non-programmers who use a coding AI and want to confirm their project runs as intended, and teams who want AI that augments rather than hallucinates. -->
[![MCP Badge](https://lobehub.com/badge/mcp/keaizuizui-coderef-ai?style=flat)](https://lobehub.com/mcp/keaizuizui-coderef-ai)

# CodeRef-AI — 编程 AI 的治理外脑，非编程人员的技术助理

**Version 5.3.4** | Python 3.10+ | MCP Protocol | PolyForm Noncommercial 1.0.0

> 给编程 AI 一双确定性的眼睛，给非编程人员一张看得懂的工程体检单。

---

## 它是什么

CodeRef-AI 通过 MCP 协议暴露 **54 个工具**，同时服务两类人：

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

## 54 个 MCP 工具

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
| `coderef_arch_gap` | 架构差距分析（5.0 核心）：对比现状知识图谱与目标架构，输出 7 类确定性差距（职责缺失/依赖违例/循环依赖/业务断链/游离模块/上帝模块/异常规模）。纯静态、复用 arch_audit，不依赖 LLM | 否 |
| `coderef_arch_canvas` | 可视化架构画布（5.0 Phase 1）：自包含 HTML 三层画布（业务/技术/代码层），拖拽定义归属、业务→技术连线、差距高亮、导出目标架构 JSON | 否 |
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
│   ├── mcp_server.py                 # MCP Server 入口（54 个工具）
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
- **开发计划**：`docs/5.2-plan.md`
- **版本号**：5.1.0 → 5.2.0（工具数 48 → 54）

### v5.1.0 — 5.1 定期体检：从"一次性重构"升级为"定期体检"

- **治理持久层**（新增 `governance_store`）：SQLite 存储体检周期 / 治理工作项 / 活动日志，状态机 Detected→Confirmed→Fixing→Verified→Archived/Rejected + 去重/复发/豁免语义
- **体检周期编排**（新增 `healthcycle`）：建档 / 导入差距 / 流转 / 豁免 / 收尾 / 报告
- **预置视图**（新增 `gov_view`）：open / all / high / recurred / rejected / archived / overdue / assigned / recent 固定查询入口
- **报告与趋势**（新增 `gov_dashboard`）：单期报告 + 跨期趋势 + 自包含 HTML（零 CDN）
- **新增 MCP 工具**（5 个）：`coderef_gov_start` / `coderef_gov_close` / `coderef_gov_issues` / `coderef_gov_transition` / `coderef_gov_report`
- **开发计划**：`docs/5.1-plan.md`
- **版本号**：5.0.0 → 5.1.0（工具数 43 → 48）

### v5.0.0 — 5.0 启动：架构推回正轨（Phase 0-2 核心闭环）

- **目标架构 JSON Schema**（新增 `target_arch_schema`）：定义"人定义的正轨"标准结构（业务层 business_flows / 技术层 tech_roles / 约束 constraints），零依赖手写校验，结构化错误返回
- **架构差距分析器**（新增 `arch_gap_analyzer`）：对比现状知识图谱与目标架构，输出 7 类确定性差距（missing 职责缺失 / dependency_violation 依赖违例 / cycle 循环依赖 / business_gap 业务断链 / unassigned 游离模块 / god_module 上帝模块 / large_module 异常规模），复用 arch_audit 不重写
- **可视化架构画布**（新增 `canvas_generator`，Phase 1）：自包含 HTML 三层画布（业务/技术/代码层），拖拽定义归属、业务→技术连线、差距高亮、导出目标架构 JSON，零外部依赖离线可用
- **重构任务卡生成器**（新增 `refactor_task_generator`，Phase 2）：差距清单 → 编程 AI 可执行任务卡（create_module/fix_dependency/break_cycle/implement_flow/move_module/split_module + 图谱影响范围 + 验证标准）
- **架构对齐验证器**（新增 `arch_alignment_verifier`，Phase 2）：四维对齐度评分（职责40%+依赖30%+业务20%+健康10%）+ 差距复检，支持 changed_files 增量模式
- **新增 MCP 工具**（6 个）：`coderef_target_arch_set` / `coderef_target_arch_get` / `coderef_arch_gap` / `coderef_arch_canvas` / `coderef_refactor_plan` / `coderef_arch_verify`，全部纯静态、确定性、轻量同步
- **开发计划**：`docs/5.0-plan.md`（Phase 0-2 详细设计 + 设计疑点决策 + 验证方案）
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

与上述项目不同，CodeRef 保留了自己的差异化主轴：以静态知识图谱交叉验证徽章为文档可信来源，而不是依赖宿主 LLM 的自我断言。完整取舍分析见 [操作记忆层设计文档](docs/operation-memory-design/operation-memory-design.html) 与 Wiki 增强评估报告 `wiki-tool-enhancement-evaluation.html`。

## 许可证

CodeRef-AI 从 **5.0** 起采用 **PolyForm Noncommercial 1.0.0**（[LICENSE](LICENSE)）：

- **欢迎大家使用**：任何非商业目的（个人学习、研究、开源项目、非营利机构、教育机构、
  政府机构等）均可自由下载、使用、修改、分发，无需付费或授权。
- **禁止商用**：不得售卖本软件，也不得将本软件（或经你修改的衍生版本）集成进任何
  **商业服务 / 商业产品 / 商业内部用途**——包括其他编程软件、IDE、商业 SaaS 等
  以盈利为目的的整合。详见 LICENSE 的 “Noncommercial Purposes” 条款。
- 完整许可文本见 [LICENSE](LICENSE)；需要商业授权的合作请与作者联系。

**版本分界**：`v4.9.12` 及更早的 **4.X 系列** 仍按 **MIT License** 授权
（[LICENSE-MIT-v4.md](LICENSE-MIT-v4.md)），从任何 v4.* 归档检出的代码可按 MIT 使用。
