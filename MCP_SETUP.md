# CodeRef-AI — MCP Server 配置指南

## 概述

CodeRef-AI 通过 MCP (Model Context Protocol) 协议暴露 59 个工具给 AI 编程助手使用。配置一次后，AI 可以分析**任何项目**——每次调用时传入 `project_path` 参数即可，不需要重复配置。

**适用客户端：** Trae / Claude Desktop / Cursor / 任何支持 MCP 的 AI 编程助手

---

## 前置条件

- Python 3.10+
- （可选）LLM API Key —— 仅 LLM 型工具需要：Wiki 文档生成（`coderef_docs`）、业务报告、`coderef_innovation_review`、产出物语义评估（`coderef_eval`）等；审计、知识图谱、架构等确定性分析无需 LLM
- 未配置 LLM 时，上述 LLM 型工具会被**硬阻断**并明确提示"需要 LLM 请先配置 API Key"（`coderef_eval` 返回 status=SKIP），不会产出降级/占位内容；审计、图谱、架构照常可用

---

## 安装依赖

```bash
cd /path/to/coderef-ai
pip install -r requirements.txt
```

核心依赖：`openai`（LLM 集成）、`loguru`（日志）、`numpy`（知识图谱向量化）、`requests`（HTTP 调用）、`pyyaml` / `packaging`。`tree-sitter` 为可选依赖，未安装时自动降级为 Python 标准库 ast.parse，不影响核心功能。

---

## 配置 LLM（可选）

> 审计和知识图谱功能**不需要 LLM**，纯静态分析即可运行。仅 LLM 型工具（Wiki 文档生成、业务报告、`coderef_innovation_review`、`coderef_eval` 等）需要 LLM；未配置 LLM 时这些工具会被硬阻断并明确提示（`coderef_eval` 返回 status=SKIP），不会产出降级内容。

### 方式一：环境变量（推荐）

**Linux / macOS：**

```bash
export CODEREF_API_KEY="your-api-key"
export CODEREF_PROVIDER="deepseek"        # 支持: deepseek / openai / ollama
export CODEREF_BASE_URL="https://api.deepseek.com"
export CODEREF_MODEL="deepseek-flash"
```

**Windows PowerShell：**

```powershell
$env:CODEREF_API_KEY="your-api-key"
$env:CODEREF_PROVIDER="deepseek"
$env:CODEREF_BASE_URL="https://api.deepseek.com"
$env:CODEREF_MODEL="deepseek-flash"
```

> 只对当前窗口生效。若要让**所有 MCP 客户端/所有项目**共用同一 Key，写进用户级环境变量（永久生效，重启终端与 MCP 服务后读取）：

```powershell
setx CODEREF_API_KEY "your-api-key"
setx CODEREF_PROVIDER "deepseek"
setx CODEREF_MODEL "deepseek-flash"
```

### 方式二：交互式配置（Windows）

```bash
setup.bat
```

按提示填写 API 信息，配置将保存到 `config/config.json`（已加入 `.gitignore`）。

### 方式三：用户级共享配置（多副本 / 多项目共用同一 Key，推荐）

CodeRef 会按以下顺序查找 LLM 配置，命中即用：

1. 环境变量 `CODEREF_API_KEY`
2. 环境变量 `CODEREF_CONFIG_PATH` 指向的文件
3. **`~/.coderef/config.json`（用户级共享配置）**
4. 安装目录的 `config/config.json`
5. 当前工作目录的 `config/config.json`

发布包通常不含 `config/config.json`（密钥不入库），因此同一台机器上的多个 CodeRef 副本（工作仓 / 测试仓 / MCP 安装目录）建议把配置放到用户级路径：

```bash
mkdir -p ~/.coderef && cp config/config.json ~/.coderef/config.json   # Linux / macOS
```

```powershell
New-Item -ItemType Directory -Force -Path "$HOME\.coderef"; Copy-Item config\config.json "$HOME\.coderef\config.json"
```

这样无论 MCP 服务从哪个项目目录启动，都能读到同一份 Key。

### 使用本地 Ollama（免费，无需 API Key）

先确保 Ollama 已安装并运行，然后设置：

```bash
export CODEREF_PROVIDER="ollama"
export CODEREF_BASE_URL="http://localhost:11434/v1"
export CODEREF_MODEL="qwen2.5:7b"
export CODEREF_API_KEY="ollama"
```

### 支持的模型

| 提供商 | 推荐模型 | 说明 |
|--------|---------|------|
| DeepSeek | `deepseek-flash` / `deepseek-v4-pro` | 官方当前推荐，性价比高，中文友好 |
| OpenAI | `gpt-4o` / `gpt-4o-mini` | 质量最高 |
| Ollama | `qwen2.5:7b` / `llama3.1:8b` | 免费，本地运行 |

---

## MCP 客户端配置

### Trae

**UI 配置（推荐）：**

1. 打开 Trae → 设置 → MCP Servers
2. 点击 "Add Custom MCP Server"
3. 填写：
   - **Name**: `coderef-ai`
   - **Command**: `python`
   - **Args**: `-m`, `core.mcp_server`
   - **Working Directory**: 指向 coderef-ai 的绝对路径
4. 保存后，Trae 会自动启动 MCP Server

**配置文件（`~/.trae-cn/mcp.json`）：**

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

### Claude Desktop

在 Claude Desktop 的配置文件中添加（位置因平台而异）：

**Windows:** `%APPDATA%\Claude\claude_desktop_config.json`  
**macOS:** `~/Library/Application Support/Claude/claude_desktop_config.json`

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

### Cursor

在 Cursor 的 MCP 配置中添加：

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

### 通用配置模板

所有 MCP 客户端配置本质相同，注意替换 `cwd` 为你的实际路径：

```json
{
  "mcpServers": {
    "coderef-ai": {
      "command": "python",
      "args": ["-m", "core.mcp_server"],
      "cwd": "/absolute/path/to/coderef-ai"
    }
  }
}
```

---

## Skill 接入（让编程 AI 知道「先做什么、再做什么」）

MCP 只负责暴露工具，**不负责告诉 AI 先调哪个**。`skills/` 下的 5 个 Skill 就是这层编排说明，按阶段分工：

| Skill | 层级 | 什么时候加载 |
|---|---|---|
| `skills/coderef-mcp/SKILL.md` | 顶层入口 | **先加载这个**：工具速查、意图→工具路由、梦协议、结论信封读法、诚实边界 |
| `skills/coderef-probe/SKILL.md` | L1 小阶段 | 提交/CI 前做增量探查、变更回归防护、论断核验 |
| `skills/coderef-governance/SKILL.md` | L2 大阶段 | 存量架构治理（map → target → refactor → verify → health） |
| `skills/coderef-governance-semi/SKILL.md` | L2 软入口 | 屎山项目先摸底，不动生产码 |
| `skills/coderef-asset/SKILL.md` | L3 资产层 | 把值得复用的设计沉淀为资产、复刻到新项目 |

**接入方式**（按客户端任选其一）：

1. **支持目录式 Skill 的客户端**（如 Trae / Claude Code）：把整个 `skills/` 目录复制到客户端的 skills 目录（常见为用户级 `~/.trae-cn/skills/` 或项目级 `.trae/skills/`），或直接指向本仓 `skills/` 路径。
2. **只支持系统提示的客户端**：把 `skills/coderef-mcp/SKILL.md` 的正文粘进项目规则/系统提示；其余 Skill 在命中对应阶段时再追加。
3. **不想装 Skill**：直接调 `coderef_version(project_path)`，其 `consumer_contract` 已内联 3 条必读 + KL-01~KL-09 限制表，足以正确转述结论，只是不如 Skill 细。

> Skill 只描述「怎么编排」，不新增工具；所有能力仍走下面的 MCP 工具。Skill 里的工具名按 `lite` profile 编写，full-only 工具会显式标注。

---

## 工具面 profile（可选）

`CODEREF_TOOL_PROFILE` 控制 `tools/list` 暴露多少工具，**不删除 handler**：被隐藏的工具仍可按名直接调用。

| 值 | 暴露数 | 适用场景 |
|---|---|---|
| `full`（默认） | 59 | CI、高级用户、需要完整工具面 |
| `lite` | 12 | 日常开发，只暴露入口工具（version / plan / execute / dispatch_status / dispatch_report / dream / task_status / scope / operation_memory / verify_findings / change_guard / audit） |

配置方式（优先级从高到低）：

```bash
# 1. 环境变量
export CODEREF_TOOL_PROFILE=lite

# 2. config.json 顶层字段
{ "tool_profile": "lite" }
```

非法值会自动回退 `full`，并在 `coderef_version` 的 `tool_profile.error` 中如实报告。`coderef_version` 返回的 `tool_profile` 形如：

```json
{"profile": "full", "source": "default", "error": "", "tools_exposed": 59, "tools_hidden": 0}
```

---

## 梦（coderef_dream，可选）

梦是 CodeRef 的离线巩固层：主 AI 醒着做事，CodeRef 在安静时把「被闸门阻断 / 失败 / 变更守卫退化 / 主 AI 显式 seed」的经历巩固成更少更准的联想，写进项目本地 `.coderef/dream/DREAM.md`（≤800 token）。

- **复用主 LLM**：不维护独立 Key/预算，和其他 LLM 工具共用 `CODEREF_API_KEY` / `config.json`。
- **默认不自动跑**：`dream.run_on_startup` 默认 `false`；只有显式设为 `true`，才会在首次带 `project_path` 的调用后后台补一轮。
- **四个常用 action**：`status`（默认，同步）/ `seed`（投喂一条显著事件）/ `read`（读 DREAM.md）/ `run`（补梦，后台，可传 `job`）。
- **输出是假设不是事实**：`advice` 永远 `hypothesis`；只有 `verified` 事实进「已验证规则」。草稿（`drafts/`）必须用户确认后走 `next_tools` 的确定性工具，P3（`false_memory_check` / `eval_replay`）只手动跑。

```json
{
  "dream": {
    "enabled": true,
    "run_on_startup": false,
    "max_notes": 40,
    "ttl_days": 30,
    "min_interval_hours": 12
  }
}
```

详细设计见 [docs/dream.md](docs/dream.md) 与 [docs/dream-jobs.md](docs/dream-jobs.md)。

---

## MCP 工具

| 工具 | 功能 | 模式 | 需要 LLM |
|------|------|------|---------|
| `coderef_audit` | 11 审计工具一键产出 + 自动降噪 + 知识图谱构建 | 后台 | 否 |
| `coderef_scan` | 单维度审计（11 选 1），实时安全带；默认后台执行、立即返回 task_id（`background` 参数可覆盖为同步），客户端用 `coderef_task_status` 查询结果 | 后台 | 否 |
| `coderef_scan_list` | 列出 `coderef_scan` 可选的维度清单 | 同步 | 否 |
| `coderef_architecture` | 架构分析图谱 + 交互式 HTML 模块画布 | 后台 | 否 |
| `coderef_docs` | 项目 Wiki 文档生成 + 子项目探测 | 后台 | 是 |
| `coderef_docs_read` | 按需读取已生成 Wiki 文档正文 | 同步 | 否 |
| `coderef_query` | 知识图谱结构化查询（9 种查询类型） | 同步 | 否 |
| `coderef_review` | 代码审查：diff 变更 / 全量语义首查 | 后台 | 是 |
| `coderef_frontend` | 前端交互审查：按钮/菜单静态枚举 | 后台 | 是 |
| `coderef_report` | 生成「项目评估台」（唯一用户入口：五 Tab 概览/架构/产品/UX/技术 + 审计/图谱/Wiki 详情页） | 后台 | 否 |
| `coderef_audit_advisor` | 审计策略判定（增量/全量）+ 功能维度 | 后台 | 可选 |
| `coderef_flow_verify` | 流程合规验证（期望流程确证） | 同步 | 否 |
| `coderef_arch_audit` | 架构腐化诊断（循环依赖/上帝模块/分层违例） | 同步 | 否 |
| `coderef_whitelist` | 白名单管理 + 核心模块规则配置 | 同步 | 否 |
| `coderef_task_status` | 后台任务状态查询 | 同步 | 否 |
| `coderef_change_guard` | 变更守护：git 基层(ensure_git) + 退化检测(guard) + 健康基线(anchor/list_baselines)，git_bin 可由外层 AI 传入 | 后台 | 否 |
| `coderef_change_report` | 变更人话版说明 | 后台 | 可选 |
| `coderef_memory` | 记忆层：action=sync 同步/增量；action=query 语义/结构查询；action=status 覆盖度+盲区地图；action=quality 质量评估+自动补全（由原 4 个记忆工具合并而来） | sync/status/quality 后台、query 同步 | 否 |
| `coderef_operation_memory` | 操作记忆层：action=sync 增量同步；action=query 语义/结构查询；action=find 定位工具/约定/陷阱；action=status 状态概览；action=recover 恢复关键工具位置/约定摘要/待确认项；action=export 导出 Markdown+冲突检测（由原 6 个操作记忆工具合并而来） | sync 后台，其余同步 | 否 |
| `coderef_prompt_governance` | Prompt 治理平台（资产生命周期 × 合规审计 × 跨模块一致性，已合并原 prompt_mgmt + prompt_audit） | 后台 | 否 |
| `coderef_verify_findings` | 论断确定性核验（确证/证伪/部分确证/存疑） | 后台 | 否 |
| `coderef_innovation` | 识别项目创新设计 + 传播缺口 | 后台 | 是 |
| `coderef_asset` | 设计固化 WorkflowAsset 资产 | 后台 | 是 |
| `coderef_registry` | 已知设计库管理，别名归一 | 同步 | 否 |
| `coderef_asset_blueprint` | 已固化资产蓝图/复刻指引读取 | 同步 | 否 |
| `coderef_replicate` | 复刻铺排（蓝图 → 缺口 → 可复刻指引） | 后台 | 否 |
| `coderef_replicate_apply` | 复刻落地（把指引落到目标项目） | 后台 | 否 |
| `coderef_innovation_review` | 创新复刻的 LLM 协助排查：让 LLM 阅读源项目管线设计（知识图谱调用链）+ wiki，判定是否确属创新 workflow、管线与 wiki 是否一致、复刻是否合理。无 API Key 时硬阻断（只给确定性管线摘要，不产出降级判断） | 后台 | 是 |
| `coderef_interpret` | 人话解读（把审计/图谱结论转成人人可读） | 后台 | 是 |
| `coderef_owasp` | OWASP LLM Top 10 合规检测 | 后台 | 否 |
| **调度与观测（v5.19）** | | | |
| `coderef_plan` | dispatcher 计划卡：intent（probe / single_audit / recover / governance / asset / remind）或 intent_text → 链、步骤、决策点；纯规划不执行 | 同步 | 否 |
| `coderef_execute` | 执行计划卡或 intent，返回 task_id；每步过前置闸门，缺 recover/图谱旧时自动补齐，用户决策步骤暂停 | 后台 | 否 |
| `coderef_dispatch_status` | 任务当前步骤、各步状态/耗时、闸门命中与自动补齐记录、暂停决策点 | 同步 | 否 |
| `coderef_dispatch_report` | 最终摘要信封：结论 + freshness + KL 限制 + owner_actions + 折叠步骤索引，中间返回不进主上下文 | 同步 | 否 |
| `coderef_dispatch_metrics` | 从 `.coderef/audit_log` 聚合闸门命中率、阻断 Top、记忆命中率、新鲜度事件、scope 生效、任务结局 | 同步 | 否 |
| `coderef_task_cancel` | 取消运行中的后台任务；已完成/不存在时如实返回实际状态 | 同步 | 否 |
| **范围、版本与梦（v5.19–v5.21）** | | | |
| `coderef_scope` | 项目边界单一事实源 `.coderef/scope.json`：add / remove / list / verify；add 后立即回执生效验证 | 同步 | 否 |
| `coderef_version` | 版本探针 + `consumer_contract`（必读 + KL-01~KL-09）+ 派生产物新鲜度快报 + `tool_profile` 暴露面 | 同步 | 否 |
| `coderef_dream` | 梦：status / seed / read / run（job 12 类：pitfall/recap/routing/triage/change_narrative/failure_postmortem/review_draft/refactor_draft/asset_candidate/noise_proposal/false_memory_check/eval_replay）；另支持 action=drafts / evals 读草稿与评估 | status/seed/read 同步，run 后台 | run 是 |
| **架构治理（v5.0）** | | | |
| `coderef_target_arch_set` | 设置/更新目标架构 JSON（业务层 + 技术层 + 约束），作为差距分析的参照系 | 同步 | 否 |
| `coderef_target_arch_get` | 读取当前目标架构；未设置时明确返回 `not_set` | 同步 | 否 |
| `coderef_target_adopt` | 把 `arch_gap` 报出的游离/未建模模块按角色批量追加 `target_modules` | 同步 | 否 |
| `coderef_arch_gap` | 现状图谱 vs 目标架构的确定性差距清单（missing / dependency_violation / cycle / business_gap / unassigned / god_module / large_module） | 同步 | 否 |
| `coderef_refactor_plan` | 差距 → 可执行重构任务卡（操作、确定性影响范围、验证标准） | 同步 | 否 |
| `coderef_arch_verify` | 重构后 0–100 四维对齐度评分 + 差距复检；传 `changed_files` 走增量 | 同步 | 否 |
| `coderef_role_boundary` | 符号级职责越界检测（命名语义 / 跨角色调用 / 复用信号）；`semantic=true` 时用 LLM 辅助 | 同步 | 可选 |
| `coderef_dynamic_probe` | 动态探针：动态导入、装饰器注册、工厂/回调、entry_points；只扫代码不执行 | 同步 | 否 |
| **定期体检（v5.1–5.2）** | | | |
| `coderef_gov_start` | 建档体检周期并把当前差距导入为治理工作项（去重 / 复发 / 豁免） | 同步 | 否 |
| `coderef_gov_close` | 收尾周期并输出完成率 / 剩余 / 复发 / 豁免统计 | 同步 | 否 |
| `coderef_gov_issues` | 按视图查询工作项（open / high / recent / recurred / rejected / overdue 等） | 同步 | 否 |
| `coderef_gov_transition` | 工作项状态机流转与豁免（Detected→Confirmed→Fixing→Verified→Archived） | 同步 | 否 |
| `coderef_gov_report` | 体检报告 / 治理看板（单期 + 跨期趋势 + 交互 HTML）；`action=board` 等价旧 `gov_board` | 同步 | 否 |
| `coderef_gov_pipeline` | 在途工作项自动化流水线：改状态 → 生成任务卡 → 复验 → 闭环 | 同步 | 否 |
| `coderef_gov_board` | 兼容别名，转发 `coderef_gov_report(action=board)`，落盘 `.coderef/gov_board.html` | 同步 | 否 |
| `coderef_gov_workspace` | 多代码库聚合治理状态与跨仓 TOP 风险 | 同步 | 否 |
| `coderef_gov_schedule` | 生成 cron/CI 可运行的体检触发脚本 `run_cycle.py` + 离期检查 | 同步 | 否 |

---

## 使用示例

配置完成后，在 AI 对话中直接描述需求即可，AI 会自动调用对应工具：

**"分析这个项目的代码质量"**  
→ AI 调用 `coderef_audit(project_path="/path/to/project", background=True)`

**"生成这个项目的文档"**  
→ AI 调用 `coderef_docs(project_path="/path/to/project", background=True)`

**"这个函数被谁调用？"**  
→ AI 调用 `coderef_query(project_path="/path/to/project", query_type="callers", func_name="login")`

**"修改 auth.py 会影响哪些模块？"**  
→ AI 调用 `coderef_query(project_path="/path/to/project", query_type="impact", file_path="auth.py")`

**"看看这个项目的架构"**  
→ AI 调用 `coderef_architecture(project_path="/path/to/project")`

**"验证项目是不是按我期望的流程执行"**  
→ AI 调用 `coderef_flow_verify(project_path="/path/to/project", entry="pipeline_runner.audit", steps=["B","C","D"])`

**"这个项目的架构健康吗？有没有循环依赖/上帝模块？"**  
→ AI 调用 `coderef_arch_audit(project_path="/path/to/project")`

---

## 知识图谱查询速查

知识图谱在运行 audit / architecture / docs / memory_sync 后自动构建，持久化到 `cache/kg/`。一次构建，跨会话复用。

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
| 查询某个节点的所有关系 | `relations` | `node_id="node_xxx"` |

**实体类型：** `module` / `function` / `class` / `method` / `config` / `constant`  
**关系类型：** `CONTAINS` / `IMPORTS` / `INHERITS` / `CALLS` / `REFERENCES`

---

## 白名单使用

审查审计报告后，将确认无误的误报加入白名单，下次审计时自动过滤：

```python
# 添加白名单条目
coderef_whitelist(project_path="/path/to/project", action="add", entries=[
    {"file": "utils.py", "rule": "不安全随机数"},
    {"category": "security"},
])

# 查看当前白名单
coderef_whitelist(project_path="/path/to/project", action="list")

# 清空白名单
coderef_whitelist(project_path="/path/to/project", action="clear")
```

白名单文件存储在 `cache/pipeline/{project_hash}/whitelist.json`。

---

## 核心模块规则配置

如果 Wiki 文档生成漏掉了某些核心模块，可以通过规则配置来修正：

```python
# 查看当前规则
coderef_whitelist(project_path="/path/to/project", action="core_rules_get")

# 自定义规则
coderef_whitelist(project_path="/path/to/project", action="core_rules_set", core_rules={
    "core_names": ["my_core_module", "shared"],                      # 强制核心模块名
    "entry_files": ["main.py", "app.py", "server.py", "cli.py"],     # 入口文件名
    "min_files": 5                                                    # 文件数阈值
})

# 恢复默认规则
coderef_whitelist(project_path="/path/to/project", action="core_rules_reset")
```

**默认规则：**
- `entry_files`: `["main.py", "app.py", "server.py", "run.py", "cli.py", "index.py", "start.py"]`
- `min_files`: 10
- `core_names`: 空

规则存储在 `cache/pipeline/core_rules_{project_hash}.json`。

---

## 故障排查

### MCP Server 启动失败

```bash
cd /path/to/coderef-ai
python -m core.mcp_server
# 观察输出，检查是否有依赖缺失或语法错误
```

常见原因：
- Python 版本低于 3.10
- `tree-sitter` 依赖未安装（`pip install -r requirements.txt`）
- 工作目录路径不正确

### 后台任务模式（重型工具默认）

为避免任意 MCP 客户端（Trae / Claude Desktop / Cursor / Cherry Studio 等）对单次 `tools/call` 的**超时限制**，**重型工具默认后台执行**：调用立即返回 `{"status":"running","task_id":"xxxx"}`，由外层 AI 轮询 `coderef_task_status(task_id="xxxx")` 取最终结果，不再撞超时。

- 默认后台：`coderef_audit` / `coderef_docs` / `coderef_review` / `coderef_frontend` / `coderef_report` / `coderef_audit_advisor` / `coderef_architecture` / `coderef_arch_canvas` / `coderef_flow_canvas` / `coderef_memory`（action=sync/status/quality） / `coderef_operation_memory`（action=sync） / `coderef_owasp` / `coderef_innovation` / `coderef_asset` / `coderef_innovation_review` / `coderef_change_guard` / `coderef_change_report` / `coderef_verify_findings` / `coderef_replicate` / `coderef_replicate_apply` / `coderef_asset_blueprint` / `coderef_prompt_governance` / `coderef_interpret` / `coderef_scan`
- 轻量工具（`coderef_query` / `coderef_whitelist` / `coderef_docs_read` 等）保持同步快速返回
- 显式控制：传 `background=False` 强制同步（小项目想立即拿结果），传 `background=True` 强制后台

### 后台任务一直没有完成

```python
coderef_task_status(task_id="xxx")
# 返回 running → 还在执行，继续等待
# 返回 error → 查看错误信息
# 返回 completed → 已完成，报告已保存到 <project>/.coderef/report/
```

### 知识图谱查询返回空

知识图谱需要先运行 audit / architecture / docs 才会构建。如果之前没有运行过：

```python
coderef_audit(project_path="/path/to/project", background=True)
```

### 中文路径支持

目前代码分析器对中文路径兼容性有限，建议项目路径使用纯英文。

### Wiki 文档生成失败

检查 LLM 配置是否正确：

```bash
# 验证 LLM 连接
python -c "
from core.llm_integration import get_llm_client
client = get_llm_client()
print(client.models.list())
"
```

---

## 架构说明

```
AI 编程助手 (Trae / Claude Desktop / Cursor)
   │
   └── coderef-ai MCP Server (v5.21.10, 59 个工具)
          │
          ├── coderef_audit ─── 11 检测器管线
          │      ├── 治理审计 (governance_audit)
          │      ├── Agent 安全 (agent_security_auditor)
          │      ├── 依赖扫描 (sca_checker)
          │      ├── 技术债务 (tech_debt_detector)
          │      ├── 完整性检查 (integrity_checker)
          │      ├── 盲区检测 (blind_spot_detector)
          │      ├── 创新传播 (innovation_propagation_detector)
          │      ├── 垃圾文件 (junk_detector)
          │      ├── 资源遗漏 (resource_gap_detector)
          │      ├── 代码精简 (code_simplifier)
          │      └── 项目成熟度 (project_maturity_checker)
          │      ├── 交叉验证（置信度分级 HIGH/MEDIUM/LOW）
          │      ├── 三级自动降噪（AutoNoiseFilter）
          │      └── 自动构建知识图谱（SQLite）
          │
          ├── coderef_architecture ─── 架构图谱
          │      └── 交互式 HTML 模块画布 (vis-network)
          │
          ├── coderef_docs ─── Wiki 文档
          │      ├── Stage 1: AST 全量元数据提取
          │      ├── Stage 2: LLM 逐模块归纳
          │      └── Stage 3: LLM 生成文档 + 编校验证
          │
          ├── coderef_query ─── 知识图谱
          │      └── SQLite 持久化，9 种查询类型
          │
          ├── coderef_whitelist ─── 白名单 + 核心规则
          ├── coderef_task_status ─── 后台任务查询
          ├── coderef_flow_verify ─── 流程合规验证（非编程人员验证期望流程）
          ├── coderef_arch_audit ─── 架构腐化诊断（循环依赖/上帝模块/分层违例/模块规模）
          ├── coderef_review ─── 代码审查（diff 变更 / 全量语义首查）
          ├── coderef_frontend ─── 前端交互审查（按钮/菜单静态枚举）
          ├── coderef_report ─── 审计报告/图谱/Wiki 聚合 HTML 报告
          ├── coderef_audit_advisor ─── 审计策略判定（增量/全量）
          ├── coderef_memory / coderef_operation_memory ─── 记忆引擎（项目记忆 + 操作记忆）
          ├── coderef_prompt_governance ─── Prompt 治理（生命周期 × 合规 × 跨模块）
          ├── coderef_verify_findings ─── 论断确定性核验
          ├── coderef_innovation / coderef_asset / coderef_registry ─── 创新识别引擎
          ├── coderef_change_guard / coderef_change_report ─── 变更守护引擎
          └── coderef_owasp ─── OWASP LLM Top 10 合规检测
```

### 关键设计决策

1. **单 Server 集中管控**：59 个工具统一暴露，无需为每个检测器单独配置 MCP Server
2. **审计无需 LLM**：11 个检测器均基于静态分析，离线可用，零 API 成本
3. **知识图谱持久化**：一次构建，跨会话复用，节省重复分析时间
4. **交叉验证反幻觉**：多工具独立分析同一项目，相互验证，解决 AI 自查幻觉
5. **后台任务模式**：重型工具默认后台异步执行，避免任意 MCP 客户端（Trae / Claude Desktop / Cursor 等）的超时限制；轻量工具同步快速返回
6. **项目隔离**：每个项目独立缓存，切换项目不互相干扰
7. **版本管理（v5.14.3 起）**：版本号单一真源在 `core/version.py`——`__init__.py` / `pyproject.toml`（动态读取）/ `mcp_server` 均自动跟随，**升版本只改一处**；文档快照由 `tools/check_version.py` 发布前校验防漂移

---

## 安全与隐私

- **API 密钥**：存储在 `config/config.json` 或环境变量中，`config/` 和 `cache/` 目录已加入 `.gitignore`，不会提交到 Git
- **代码分析**：完全在本地运行，审计和知识图谱功能无需网络
- **开源前清理**：删除 `cache/` 和 `config/` 目录即可清除所有敏感数据

```bash
# 开源前清理
rm -rf cache/ config/
```