<!-- AI Summary: CodeRef-AI exposes 50 MCP tools that give coding AI a deterministic "audit brain" and give non-programmers a readable view of their project. It covers audit, knowledge graph, architecture diagnosis, flow verification, change guard, OWASP, deterministic verification, and prompt compliance — all pure static analysis that is reproducible with no LLM. LLM is only used for synthesis tasks (wiki, report, code review) and hard-blocks honestly without an API key. It ships three orchestration Skills (L1 probe / L2 governance / L3 asset) that turn the 50 tools into a compact chain. It builds a closed loop: deterministically verify LLM/CodeRabbit claims, govern legacy structure along a map→target→refactor→verify→health mainline, and solidify/replicate reusable design assets. Best for: non-programmers who use a coding AI and want to confirm their project runs as intended, and teams that want AI that augments rather than hallucinates. -->
[![MCP Badge](https://lobehub.com/badge/mcp/keaizuizui-coderef-ai?style=flat)](https://lobehub.com/mcp/keaizuizui-coderef-ai)

# CodeRef-AI — 编程 AI 的治理外脑，非编程人员的技术助理

**Version 5.12.3** | Python 3.10+ | MCP Protocol | PolyForm Noncommercial 1.0.0

> 给编程 AI 一双确定性的眼睛，给非编程人员一张看得懂的工程体检单。

---

## 它是什么

CodeRef-AI 通过 MCP 协议暴露 **50 个工具**，同时服务两类人：

- **编程 AI 的治理外脑**：让 AI 不再逐文件读代码，而是像查数据库一样查询项目的结构、调用链与风险；编程 AI（或 CodeRabbit）给出论断时，还能用静态图谱做确定性核验，再决定采不采信。
- **非编程人员的技术助理**：把代码变成通俗的健康仪表盘、Wiki 与流程确证。你只需定义「入口 A 应该依次经过步骤 B→C→D」，`coderef_flow_verify` 就会在调用链里给出确证 / 在管线 / 存疑 / 缺失四种状态——不用读代码，也能确认项目有没有按你的设想运转。

多数 AI 审查工具把结论建立在「大模型读代码」上，而模型会幻觉。CodeRef 反过来：**审计、图谱、架构诊断、流程验证、变更守护、OWASP、论断核验等核心能力全部是纯静态分析**，结果确定、可复现——同一个项目每次跑出同样结论。LLM 只用于 Wiki、业务报告、创新排查等「要人话」的场景，未配置 API Key 时会明确硬阻断并提示配置，绝不降级编造。

---

## 三条编排主线：50 个工具不是散件，而是一条主链

50 个工具虽多，但 CodeRef 把它们整理成**三条互补的编排主线（Skill）**，编程 AI 只需知道自己该在哪条链上：

```
L1  coderef-probe      · 小阶段 · 变更驱动的轻量探查与防护（类 CodeRabbit）
L2  coderef-governance · 大阶段 · 周期驱动的存量工程系统性规整
L3  coderef-asset      · 资产层 · 把治理/开发成果沉淀成可复用设计
```

- **L1 探查链**：提交/CI 前，增量、分钟级地快速探查——`change_guard` 拦截「把旧代码改坏」、`verify_findings` 确定性核验 LLM/CodeRabbit 论断防自查幻觉、误报进白名单收敛、小问题即时闭环；存量结构问题升级 L2。
- **L2 治理主链**：把被反复修改搞乱的存量工程捋回正轨——先辨清真身/孪生/孤本，再沿 `map → target → refactor → verify → health` 五阶段规整，用 9 类确定性差距生成任务卡，靠四维对齐度确认真正回正轨，最后用定期体检维持。
- **L3 资产链**：把值得复用的设计提炼成资产，防止高价值设计随项目迁移流失——沉淀有门槛（≥2 处采用 + 证据防污染）、命名先归一、复刻不自动改源码。

三层衔接：L1 发现存量结构问题 → 升级 L2 规整；L1 发现被多处采用的好设计 → 走 L3 沉淀；L3 复刻落到新项目 → 回 L1/L2 验证维持、防再腐化。详见仓库 `skills/` 目录下的 4 个 Skill（含 `coderef-mcp` 的「意图 → 工具」速查表）。

---

## 为什么可信：关键主张都有量化实证

CodeRef 不靠宣称，靠可复现的硬指标（完整方法见后文「可靠性如何验证」）：

| 主张 | 实证 |
|---|---|
| **全量回归** | 11 个样本工程 × 9 类子探针 = **99/99 通过、0 失败**，验证「测试污染 = 无」 |
| **治理闭环** | 五阶段端到端，指标单调收敛：模块归属度 **0.03→0.04→0.11** ↑、未归属缺口 **479→478→439** ↓、期望流程越界/缺失 **=0**；游离一键纳入后再降至 **436**、归属度 **0.12** |
| **LLM 盲区** | 被测主链均为确定性静态链路（天然不触发 LLM）；LLM 型链路一测即通，受控提炼零残留 |
| **工具收敛** | 冗余工具合并（操作记忆 6 合 1、记忆层 4 合 1）、旧接口移除、未知参数如实报、语义检索不可用时自动降级为关键词——收敛不缩水，降级不撒谎 |
| **降噪** | 一次审计从 **873 条噪声收敛到 79 条（约 91% 降幅）** |

---

## 快速开始

如果你是**非编程人员**：把这部分说明交给你的编程 AI，它会帮你完成安装、配置和第一轮分析。你真正要做的，是最后打开它生成的健康仪表盘和 Wiki，看懂自己的项目。如果你是**自己动手**：照下面四步走。

### 1. 安装

只依赖纯 Python 包，不触发 C 源码编译，Python 3.10-3.14 免编译直接装好。

```bash
git clone https://github.com/keaizuizui/CodeRef-AI.git
cd CodeRef-AI
pip install -r requirements.txt
```

### 2. 配置 LLM（可选）

> 审计、图谱、架构诊断、流程验证、变更守护、OWASP **不需要 LLM**，纯静态即可运行。仅 Wiki、业务报告、代码审查、Prompt 资产、创新识别需要 LLM；未配置 API Key 时这类「人话报告」被硬阻断并提示配置，不产出降级/占位内容。

**Windows：** 运行 `setup.bat`。

**Linux / macOS：**

```bash
export CODEREF_API_KEY="your-api-key"
export CODEREF_PROVIDER="deepseek"        # deepseek / openai / ollama
export CODEREF_BASE_URL="https://api.deepseek.com"
export CODEREF_MODEL="deepseek-v4-flash"  # 官方推荐: deepseek-v4-flash / deepseek-v4-pro
```

**本地 Ollama（免费，无需 API Key）：**

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

在 Trae / Claude Desktop 等 MCP 客户端中添加（详细指南见 [MCP_SETUP.md](MCP_SETUP.md)）：

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

> 本项目由编程 AI 辅助研发，作为 AI 治理方向的实践样本。建议拿到代码后，用 CodeRef 自己审计一遍，让报告带你理解每处实现。

### 第一轮 · 完整体检（3 分钟看懂你的项目）

```
# 1. 全量审计（后台，自动构建知识图谱），用任务状态轮询取结果
coderef_audit(project_path="/path/to/project", background=True)
coderef_task_status(task_id="...")

# 2. 生成项目 Wiki（未配 Key 时被硬阻断并提示配置）
coderef_docs(project_path="/path/to/project", background=True)

# 3. 看人话健康仪表盘（无需读代码）
coderef_interpret(project_path="/path/to/project", action="dashboard")
```

### 后续 · 按需深入

```
# 提交前防止 AI 把代码改坏（L1）
coderef_change_guard(project_path=..., action="guard", diff="<git diff 文本>")
coderef_change_report(project_path=..., diff="<git diff 文本>")

# 验证流程是否按预期走（L1 核验）
coderef_flow_verify(project_path=..., entry="pipeline_runner.audit", steps=["A","B","C"])

# 沿治理主链治理存量（L2）：捋管线 → 定目标 → 差距 → 任务卡 → 对齐验证
coderef_architecture(project_path=...)
coderef_target_arch_set(project_path=..., target_arch={...})
coderef_arch_gap(project_path=...)
coderef_refactor_plan(project_path=...)
coderef_arch_verify(project_path=...)

# 定期体检维持（L2 health）
coderef_gov_start(project_path=...) → coderef_gov_transition(project_path=..., issue_id=..., to="Fixing") → coderef_gov_close(project_path=...)
coderef_gov_report(project_path=...)   # 单期 + 跨期趋势 / Web 看板

# 沉淀 & 复刻好设计（L3）
coderef_innovation(project_path=...)    # 识别创新设计 + 传播缺口
coderef_replicate(project_path=..., canonical="...")   # 检测目标项目采用缺口（只报告）

# 查询知识图谱，替代 grep（省 10-100 倍 token）
coderef_query(project_path=..., query_type="callers", func_name="login")
coderef_query(project_path=..., query_type="impact", file_path="utils.py")
```

---

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

**三级自动降噪**：AI 白名单精准抑制已知误报 → 规则匹配过滤 MD5 哈希、配置 URL 等常见噪声 → 邻行去重 + 爆发式合并同类项（>8 条同类别 → 1 条统计）。实测 873 → 79 条（约 91% 降幅）。

**交叉验证反幻觉**：多工具独立分析同一项目，相互验证，输出 HIGH / MEDIUM / LOW 置信度分级——单一工具可能误判，但多工具互验后置信度显著提升。

---

## 知识图谱

运行 audit / architecture / docs / memory_sync 后自动构建 SQLite 知识图谱，持久化到 `cache/kg/`，一次构建跨会话复用。

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

---

## 50 个 MCP 工具速查

> 完整功能、参数与「意图 → 工具」路由见 `skills/` 各 Skill 与 `MCP_SETUP.md`。这里按引擎列出。

### 审计引擎

| 工具 | 功能 | 需 LLM |
|------|------|:---:|
| `coderef_audit` | 11 审计一键产出 + 自动降噪 + 构建知识图谱；`strategy`=auto/full/incr | 否 |
| `coderef_scan` | 单维度审计（11 选 1），快一个量级；后台执行，用 `coderef_task_status` 轮询 | 否 |
| `coderef_scan_list` | 列出 `coderef_scan` 可选维度 | 否 |
| `coderef_flow_verify` | 流程合规验证：「入口 A 调用管线是否覆盖 B→C→D」；状态分确证/在管线/存疑/缺失 | 否 |
| `coderef_verify_findings` | 确定性核验 LLM/CodeRabbit 论断：引用目标是否真实存在、是否在关键管线内 | 否 |
| `coderef_prompt_governance` | Prompt 治理平台：资产生命周期 × 合规审计 × 跨模块一致性 | 否 |
| `coderef_arch_audit` | 架构腐化诊断（循环依赖/上帝模块/分层违例），聚合 0–10 健康度 | 否 |
| `coderef_target_arch_set` | 设置/更新目标架构 JSON（治理参照系），纯确定性校验 | 否 |
| `coderef_target_arch_get` | 获取当前目标架构 JSON | 否 |
| `coderef_target_adopt` | 游离一键纳入：游离/未建模模块按角色批量追加目标模块（dry_run 预览；幂等） | 否 |
| `coderef_arch_gap` | 架构差距分析（核心）：9 类确定性差距，游离模块区分真游离/未建模并豁免 vendor/压缩产物噪声 | 否 |
| `coderef_arch_canvas` | 可视化架构画布（三层自由拖拽、差距高亮、导出目标架构） | 否 |
| `coderef_flow_canvas` | 交互式流程画布：自动提取业务管线 + 数据流 | 否 |
| `coderef_refactor_plan` | 差距清单 → 可执行重构任务卡 + 影响范围 + 验证标准 | 否 |
| `coderef_arch_verify` | 四维对齐度评分（职责 40%+依赖 30%+业务 20%+健康 10%）+ 差距复检 | 否 |
| `coderef_gov_start` | 建档体检周期并导入差距为治理工作项 | 否 |
| `coderef_gov_close` | 收尾周期，输出完成率/剩余/复发/豁免统计 | 否 |
| `coderef_gov_issues` | 查询治理工作项（预置视图 open/all/high/recurred/rejected/archived/overdue/assigned/recent） | 否 |
| `coderef_gov_transition` | 工作项状态流转 + 豁免 | 否 |
| `coderef_gov_report` | 体检报告 / 治理看板（report 单期+跨期趋势；board 交互 HTML；已合并原 gov_board） | 否 |
| `coderef_gov_pipeline` | 治理自动流水线：在途项 → 任务卡 → 复验 → Verified/附缺口 | 否 |
| `coderef_dynamic_probe` | 动态探针：静态挖掘动态信号（动态导入/装饰器注册/间接索引），零执行被检项目 | 否 |
| `coderef_gov_board` | 治理 Web 看板（兼容别名，转发到 gov_report action=board） | 否 |
| `coderef_gov_workspace` | 多代码库聚合治理 | 否 |
| `coderef_gov_schedule` | 定时体检：生成 run_cycle.py + 离期检查 | 否 |
| `coderef_role_boundary` | 符号级职责越界检测 | 可选 |
| `coderef_architecture` | 架构分析图谱 + 交互式 HTML 模块画布 | 否 |
| `coderef_docs` | 项目 Wiki 文档生成 + 子项目探测 | 是 |
| `coderef_docs_read` | 按需读取已生成 Wiki 正文（返回内容而非路径） | 否 |
| `coderef_query` | 知识图谱结构化查询（9 种查询类型） | 否 |
| `coderef_review` | 代码审查：diff 变更审查 / 新项目全量语义首查 | 是 |
| `coderef_frontend` | 前端交互审查（按钮/菜单静态枚举 + 6 维度审查） | 是 |
| `coderef_report` | 聚合审计/图谱/Wiki 为自包含 HTML 报告目录 | 否 |
| `coderef_audit_advisor` | 审计策略判定（增量/全量）+ 重点维度 | 可选 |
| `coderef_whitelist` | 白名单管理 + 核心模块规则配置 | 否 |
| `coderef_task_status` | 后台任务状态查询 | 否 |
| `coderef_task_cancel` | 后台任务取消（协作式收尾） | 否 |

### 记忆引擎

| 工具 | 功能 | 需 LLM |
|------|------|:---:|
| `coderef_memory` | 项目记忆层：sync 增量同步 / query 语义+结构查询 / status 覆盖度+盲区 / quality 质量评估+自动补全（4 工具合并） | 否 |
| `coderef_operation_memory` | 操作记忆层：sync / query / find 定位工具约定陷阱 / status / recover 恢复关键工具位置 / export 导出 Markdown（6 工具合并） | 否 |

**记忆库落点**：`coderef_memory` 写入 `<项目根>/data/memory_state/`；`coderef_operation_memory` 写入 `<项目根>/data/operation_memory/`。均按项目 hash 隔离，属运行时产物，`data/` 已在 `.gitignore`。

### 创新识别引擎

| 工具 | 功能 | 需 LLM |
|------|------|:---:|
| `coderef_innovation` | 识别项目创新设计 + 传播缺口，理想清单 vs 实际实现对照 | 是 |
| `coderef_asset` | 将验证过的设计固化 `WorkflowAsset` 资产（需 ≥2 处采用 + evidence 防污染） | 是 |
| `coderef_replicate` | 复刻铺排：检测目标项目对某资产的采用缺口 + 生成复刻指引；确定性，不自动改代码 | 否 |
| `coderef_replicate_apply` | 复刻落地：把骨架 + 说明写入目标项目并生成 manifest；只落"确定性可给"内容，冲突默认不覆盖 | 否 |
| `coderef_asset_blueprint` | 把复刻铺排的确定性结论（entry_points / verified_findings）写回蓝图 | 否 |
| `coderef_registry` | 管理已知设计库（别名归一，解决 LLM 命名漂移） | 否 |
| `coderef_innovation_review` | 创新复刻的 LLM 排查（是否真创新 + 复刻合理性）；无 API Key 硬阻断 | 是 |

### 变更守护引擎

| 工具 | 功能 | 需 LLM |
|------|------|:---:|
| `coderef_change_guard` | AI 代码退化检测（建立在 git 之上）。guard 对比基线拦截退化 / ensure_git 自动建库 / anchor 锚定健康基线 / list_baselines 列出基线 | 否 |
| `coderef_change_report` | 把 diff 归纳为「人话版」变更说明（新增/修改/影响/风险） | 可选 |

### OWASP 合规

| 工具 | 功能 | 需 LLM |
|------|------|:---:|
| `coderef_owasp` | OWASP LLM Top 10 合规检测，LLM01-LLM10 逐类分级 | 否 |

### 人话解读平台

| 工具 | 功能 | 需 LLM |
|------|------|:---:|
| `coderef_interpret` | 把确定性结论翻译成人话：health 健康总览 / dashboard 仪表盘 HTML / wiki / prompt / assets | 可选 |

---

## 设计特性

| 特性 | 说明 |
|------|------|
| 不修改代码 | 所有建议只输出不执行，原代码保持不变 |
| 本地优先 | 分析完全在本地，审计和知识图谱无需网络，支持离线 |
| 隐私安全 | LLM 密钥存 `config/config.json`（已 gitignore），不提交 Git |
| 结构化输出 | 报告 Markdown，仪表盘 HTML，知识图谱 SQLite |
| 检查点续跑 | 管线每 2 分钟保存进度，中断后可恢复 |
| 后台任务 | 长任务（audit / docs）异步执行，轮询获取结果 |
| 项目隔离 | 每个项目独立缓存，切换项目不互相干扰 |
| 开源友好 | 敏感数据集中 `cache/` 与 `config/config.json`，删除即清理 |

---

## 项目结构

```
coderef-ai/
├── core/                             # 核心引擎
│   ├── mcp_server.py                 # MCP Server 入口（50 个工具）
│   ├── pipeline_runner.py            # 管线引擎（audit/architecture/docs + 知识图谱）
│   ├── tool_registry.py              # 工具注册中心
│   ├── review_strategy.py            # 审计策略判定（增量/全量 + 影响闭包）
│   ├── functional_review.py          # 功能审查（创新传播/结构复杂度等）
│   ├── report_renderer.py            # 报告/图谱/Wiki → HTML 渲染
│   ├── code_review.py                # 代码审查（diff / 全量语义首查，evidence 标记）
│   ├── frontend_inspector.py         # 前端交互审查
│   ├── code_analyzer.py / ast_parser.py / code_models.py   # AST 分析
│   ├── code_knowledge_graph.py       # 知识图谱引擎（SQLite 持久化）
│   ├── health_dashboard.py           # 健康仪表盘（零外部依赖 HTML）
│   ├── wiki_generator.py / wiki_ir.py / wiki_compare.py / wiki_cross_verify.py  # Wiki 生成与交叉验证
│   ├── flow_verify.py                # 流程合规验证
│   ├── arch_audit.py / arch_gap_analyzer.py / target_arch_schema.py / refactor_task_generator.py / arch_alignment_verifier.py  # 架构治理
│   ├── canvas_generator.py / workflow_graph.py / diagram_generator.py  # 可视化
│   ├── governance_audit.py / agent_security_auditor.py / sca_checker.py / tech_debt_detector.py / integrity_checker.py / blind_spot_detector.py / innovation_propagation_detector.py / junk_detector.py / resource_gap_detector.py / code_simplifier.py / project_maturity_checker.py  # 11 检测器
│   ├── memory_layer.py / memory_quality.py / prompt_governance.py / prompt_compliance.py  # 记忆与 Prompt 治理
│   ├── innovation_engine.py / design_registry.py / replicate_engine.py  # 创新与资产
│   ├── verify_findings.py / interpretation_platform.py / owasp_compliance.py  # 核验/人话/合规
│   ├── change_guard.py / change_report.py              # 变更守护
│   └── llm_integration.py / cache_manager.py / project_scope.py / shared_filter.py  # 基础设施
├── skills/                          # 三层编排 Skill（probe/governance/asset/mcp）
├── config/                           # 配置（settings.py + 本地 config.json，含密钥，已 gitignore）
├── docs/                             # 文档 + changelog 更新日志归档
├── cache/                            # 运行时缓存（已 gitignore）
├── coderef-report/                   # 输出报告（已 gitignore）
├── setup.bat                         # Windows 配置向导
├── requirements.txt
├── MCP_SETUP.md / LICENSE / LICENSE-MIT-v4.md
```

---

## 可靠性如何验证

我们不把「能跑通」当验收标准，而是用**多重方式 + 量化指标**持续证明工具测得准、不误报、不撒谎，按五层由浅入深逼近真实使用：

- **单工具可调用（tool）**：每个 MCP 工具能被正确调用、输入校验符合预期。
- **多工具编排（workflow）**：审计→图谱→报告等既定工作流不短路、不丢结果。
- **跨工具思路（idea）**：跨引擎配合，如审计发现驱动知识图谱与创新复刻。
- **已知缺陷命中（defect-hit）**：维护真实缺陷清单（错题集），每个缺陷都定位到源码文件/行号/标识符证据、经二次核验、禁止臆造；逐批跑审计后按「缺陷 × 维度」算检出率，作为可复现硬指标——检出率低的维度即暴露盲区，驱动下一轮补修。
- **修复验证负向断言（defect_clean）**：对已登记缺陷预置「修复后应不再命中」的负向断言，验证缺陷修复后工具不再误报，补上「错题重做做对没」的双向闭环。
- **维度独立命中率**：对绑定维度逐个判定命中，暴露单维度漏报，避免「任一维度命中即 PASS」掩盖盲区。
- **注册表 ↔ 源码一致性（validate_registry）**：校验错题集登记与源码真实签名不漂移，防止错题集长期失真。
- **LLM 自主审查（llm-review）**：让 LLM 扮演审查者自主编排工具做端到端审查，验证「AI 自己会用这些工具」这一最贴近真实使用的场景。
- **正向模拟**：分别模拟编程 AI 调工具、非编程人员核对体检单，并覆盖「环境工具缺失时通过操作记忆恢复」的自愈路径。

**当前量化基线**（每轮回归刷新）：

| 检测维度 | 缺陷命中率 |
|---|---|
| 技术债 / Prompt 治理 / 供应链 / 治理合规 / Agent 安全 / 流程验证 | 全 100% |
| **总体**（9 个真实项目、35 个命中用例） | **100%** |

> 早期基线为 **41.9%**——也就是说，工具盲区是通过可复现的硬指标暴露、并被逐轮补修填平的，而不是靠宣称。

**三层防线 + 全量泛化回归**（覆盖 v5.9–v5.12）：

- **静态契约层**：schema/工具注册扫描、Skill 引用断链扫描、旧接口移除核查。
- **运行时行为层**：MCP 实调（合法/非法参数、全 action 覆盖、降级路径、落盘契约）。
- **端到端闭环层**：真实项目五阶段治理闭环 + 定向构造的对抗场景（资产沉淀/复用/流程校验/真重复识别）。
- **全量泛化**：11 样本 × 9 子探针 = **99/99 通过、0 失败**。

**边界与诚实声明**：错题集需持续维护，我们通过「每缺陷附确定性证据 + 二次核验 + 逐轮刷新」控制其质量与覆盖面。我们**不把工具定位为「替代人工审查」**，而定位为**确定性验证**——能确证的就确证，不能确证的一律明确标注「待人工确证」，把不确定性如实交给使用者判断。

---

## 杀毒误报处理

CodeRef-AI 是合法开源的安全审计工具，不含任何恶意代码。但依赖扫描（SCA）本地 CVE 库曾因含英文攻击型漏洞描述，被部分杀毒软件的启发式引擎（如 `HEUR:HackTool/VulnScan`）误判为漏洞扫描工具；v4.2.7 起已改为中文中性措辞，大幅降低误报概率。若仍误报：

1. **加入排除项**：将项目目录加入杀毒排除/白名单（Windows Defender：设置 → 病毒和威胁防护 → 管理设置 → 排除项 → 添加文件夹）
2. **厂商申诉**：向杀毒厂商提交误报申诉，说明这是合法开源审计工具，请求将 `sca_checker.py` 加入白名单（根治途经）
3. **如实告知审计 AI**：若 SCA 结果缺失或被清理，先配好排除项再跑审计，避免误删导致结论失真

---

## 项目历史

CodeRef-AI 从「一份看得懂的项目简报」出发，一步步长出静态审计、知识图谱、四大引擎与逻辑闭环。每个大版本都在回答同一个问题：让一个不懂编程的人，究竟能对自己的项目知道多少。

| 版本 | 目标 |
|------|------|
| **1.0** | 写一份完整的项目简报，让人类搞清楚项目是怎么回事 |
| **2.0** | 通过各类审计工具，查清项目有哪些常见问题 |
| **3.0** | 通过知识图谱和 Wiki，建立更详细的简报 |
| **4.0** | 通过四个引擎和四个支柱，增强覆盖、形成逻辑闭环 |
| **5.0** | 治理被反复修改搞乱的混乱管线，让项目回归正确 |

---

## 更新日志

> 4.X 与 5.X 系列的完整逐版本更新日志（v3.0 – v5.12.3）统一归档至 [docs/changelog/CHANGELOG.md](docs/changelog/CHANGELOG.md)；线上 README 只保留当前版本状态。

### 当前版本 v5.12.3 — 合并操作记忆层 6 工具 → coderef_operation_memory

> - **合并**：`coderef_operation_memory_sync/query/find/status/recover/export` 6 工具 → 单一 `coderef_operation_memory`，以 `action=sync/query/find/status/recover/export` 区分；核心模块零改动，产物路径不变。
> - **删旧名不保留别名**：旧 6 名从工具列表移除，需改用 `coderef_operation_memory(action=...)`；工具总数 55 → 50。
> - **后台化矩阵不变**：`MERGE_SYNC_ACTIONS` 确保 query/find/status/recover/export 同步、sync 保持后台，行为与合并前一致。
> - **验证**：全量回归通过，无阻断缺陷。
> - **版本号**：5.12.2 → 5.12.3（patch，暴露面精简，不改操作记忆能力）。

---

## 设计借鉴

CodeRef-AI v4.8 的操作记忆层（`BRAIN.md` 产物、判存标准、时间线机制）在设计上结合了以下开源项目方案：

- **mindmuxai/brain.md**（Apache-2.0）—— `BRAIN.md` 命名、「能否从代码重建」判存标准、「当前理解 + 时间线」结构。参考：[https://github.com/mindmuxai/brain.md](https://github.com/mindmuxai/brain.md)
- **TencentDB-Agent-Memory**（MIT）—— 分层记忆与渐进式披露，控制上下文 token 占用。参考：[https://github.com/Tencent/TencentDB-Agent-Memory](https://github.com/Tencent/TencentDB-Agent-Memory)

CodeRef-AI v4.9 的 Wiki 工具增强层（`wiki_generator` 增量同步 / `wiki_ir` / `wiki_cross_verify`）参考了：

- **langchain-ai/openwiki**（MIT）—— 增量同步（`.last-update.json` + 快照比对）+ 结构化元数据；同时以其成本失控、限流重试不健壮、输出截断静默失败等真实缺陷警示我们补上开销封顶与诚实失败。参考：[https://github.com/langchain-ai/openwiki](https://github.com/langchain-ai/openwiki)
- **tt-a1i/archify**（Apache-2.0）——「生成/校验分离」（LLM 产出 JSON-IR → schema 校验 → 确定性渲染）与 Last-good 门控（校验通过的产物备份，失败时保留上次可用版本）。参考：[https://github.com/tt-a1i/archify](https://github.com/tt-a1i/archify)

与上述项目不同，CodeRef 保留自己的差异化主轴：以静态知识图谱交叉验证徽章为文档可信来源，而不是依赖宿主 LLM 的自我断言。

---

## 许可证

CodeRef-AI 从 **5.0** 起采用**双轨授权协议**（[LICENSE](LICENSE)）：兼容 PolyForm Noncommercial 1.0.0 的使用边界，并清晰界定「企业内部自用免费、禁止转卖」：

- **企业内部自用，免费**：企业团队用本工具协助自己的软件开发、排解编程困境（无论是否以盈利为目的的自研业务），**不属于「商业再分发」**，欢迎直接使用，无需付费或额外授权。
- **禁止转卖 / 对外提供 / 嵌入竞品**：**不得**将本软件（或衍生版本）直接出售、**作为服务/工具对外提供并收费**，或**作为竞争产品的部分**嵌入其他以售卖为目标的商业软件——即防止「拿本工具去卖钱」。若确实需要对外提供商业服务，请与作者联系另行授权。
- **非商业场景免费**：个人学习、研究、开源项目、非营利/教育/政府机构等非商业目的可自由下载、使用、修改、分发，无需付费。
- 完整许可文本见 [LICENSE](LICENSE)；需要商业授权的合作请与作者联系。

**版本分界**：`v4.9.12` 及更早的 **4.X 系列** 仍按 **MIT License** 授权（[LICENSE-MIT-v4.md](LICENSE-MIT-v4.md)）。

---

## 贡献指引（Contributing）

欢迎通过 **Issues** 报告缺陷、提出建议或参与讨论；**本仓库暂不接收外部代码合并（Pull Request）**，以保留未来商业化（商业授权）空间并规避外部贡献的版权归属问题。详见 [贡献指引](CONTRIBUTING.md)。