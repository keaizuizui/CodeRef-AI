<!-- AI Summary: CodeRef-AI is a vibe coding governance framework with 21 MCP tools for code audit, knowledge graph, and change guard. Similar to Spec-Kit but focused on auditing and MCP integration. Best for: individuals and small teams using Claude Code/Cursor with vibe coding. -->

# CodeRef-AI — 编程 AI 的治理外脑 & 非编程人员技术助理


**Version 4.0.2** | Python 3.10+ | MCP Protocol | MIT License

> 一键审计 · 架构图谱 · 项目文档 · 知识图谱 · 健康仪表盘 · 代码审查 · 前端交互审查 · 记忆层 · 创新识别 · OWASP 合规 · 变更守护

---

## 一句话定位

CodeRef-AI 是**编程 AI 的外置大脑**和**非编程人员的技术助理**。它通过 MCP 协议暴露 **21 个工具**，让 AI 编程助手不再逐文件读代码，而是像查数据库一样查询项目结构与风险；同时为不懂编程的人生成通俗易懂的项目健康仪表盘和 Wiki 文档。

> 本项目在 vibe coding 中自然产出，作为 AI 辅助编程治理方向的引子；建议自行拷贝本地后，交由本地编程 AI 复查并改造其实现逻辑是否符合你的项目。

## 为什么需要 CodeRef

| 痛点 | CodeRef 怎么解决 |
|------|-----------------|
| AI 逐文件读代码产生幻觉，遗漏关键信息 | 11 个独立检测工具交叉验证，置信度分级，消除 AI 自查幻觉 |
| 审计报告海量误报，人工筛选耗时 | 三级自动降噪，实测 873 条噪声 → 79 条（约 91% 降幅） |
| AI 每次都要 grep/读文件才能理解项目 | 知识图谱持久化，结构化查询代替逐文件阅读，节省 10-100 倍 token |
| 非技术人员完全看不懂代码 | 一键生成通俗 Wiki + 健康仪表盘 HTML，零技术门槛 |
| 安全漏洞、技术债务默默积累无人发现 | 全维度审计覆盖 11 个维度，持续监控项目健康 |
| AI 改坏了之前的代码却没人发现 | 变更守护引擎在提交前拦截「校验链被删 / 超时削弱」等退化 |
| LLM 应用存在安全合规风险 | OWASP LLM Top 10 全维度合规检测 |

## 四引擎架构

CodeRef 4.0 由四个引擎驱动，覆盖「审计 → 记忆 → 创新 → 守护」完整闭环：

| 引擎 | 解决的问题 | 核心工具 |
|------|-----------|---------|
| **审计引擎** | 全维度代码体检 + 图谱 + 文档 + 审查 | `coderef_audit` `coderef_query` `coderef_review` 等 |
| **记忆引擎** | AI 对项目「记住了什么」，增量同步 + 语义查询 | `coderef_memory_*` `coderef_prompt_mgmt` |
| **创新识别引擎** | 从项目里挖出值得复用的设计，并沉淀为资产 | `coderef_innovation` `coderef_asset` `coderef_registry` |
| **变更守护引擎** | 拦截 AI 把代码改坏，输出人能看懂的变更报告 | `coderef_change_guard` `coderef_change_report` |

## 21 个 MCP 工具

### 审计引擎

| 工具 | 功能 | 需要 LLM |
|------|------|---------|
| `coderef_audit` | 11 审计工具一键产出 + 自动降噪 + 知识图谱构建 | 否 |
| `coderef_scan` | 单维度审计（11 选 1），实时安全带，快一个量级 | 否 |
| `coderef_scan_list` | 列出 `coderef_scan` 可选的维度清单 | 否 |
| `coderef_architecture` | 架构分析图谱 + 交互式 HTML 模块画布 | 否 |
| `coderef_docs` | 项目 Wiki 文档生成 + 子项目探测 | 是 |
| `coderef_query` | 知识图谱结构化查询（9 种查询类型） | 否 |
| `coderef_review` | 代码审查：diff 变更审查 / 新项目全量语义首查 | 是 |
| `coderef_frontend` | 前端交互审查：按钮/菜单静态枚举 + 6 维度审查 | 是 |
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
| `coderef_change_guard` | AI 代码退化检测：拦截「把之前写好的代码改坏了」 | 否 |
| `coderef_change_report` | 把 diff 归纳为「人话版」变更说明（新增/修改/影响/风险） | 可选 |

### OWASP 合规

| 工具 | 功能 | 需要 LLM |
|------|------|---------|
| `coderef_owasp` | OWASP LLM Top 10 合规检测，LLM01-LLM10 逐类分级 | 否 |

## 快速开始

### 1. 安装

```bash
git clone https://github.com/keaizuizui/CodeRef-AI.git
cd coderef-ai
pip install -r requirements.txt
```

### 2. 配置 LLM（可选）

> 审计、知识图谱、变更守护**不需要 LLM**，纯静态分析即可运行。仅 Wiki 文档、代码审查、Prompt 资产、创新识别需要 LLM。

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

# 4. 审查代码变更（AI 帮你自查 PR / 提交）
coderef_review(project_path="/path/to/project", mode="diff", diff="<git diff 文本>", background=True)

# 5. 审查前端交互（按钮 / 菜单）
coderef_frontend(project_path="/path/to/project", mode="static", background=True)

# 6. 提交前拦截 AI 把代码改坏 + 生成人话版变更说明
coderef_change_guard(project_path="/path/to/project", diff="<git diff 文本>")
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
├── core/
│   ├── mcp_server.py                  # MCP Server 入口（21 个工具）
│   ├── pipeline_runner.py             # 管线引擎（audit/architecture/docs + 知识图谱）
│   ├── code_review.py                 # 代码审查（diff 变更/全量语义首查，evidence 标记）
│   ├── frontend_inspector.py          # 前端交互审查（按钮/菜单静态枚举 + LLM 审查）
│   ├── code_analyzer.py               # 代码分析引擎（AST）
│   ├── ast_parser.py                  # AST 精细解析器（调用关系/赋值/配置）
│   ├── code_knowledge_graph.py        # 知识图谱引擎（SQLite 持久化）
│   ├── code_knowledge_base.py         # 代码知识库
│   ├── health_dashboard.py            # 项目健康仪表盘（零外部依赖 HTML）
│   ├── wiki_generator.py              # Wiki 生成器（三级管线）
│   ├── workflow_graph.py              # 架构图生成器（vis-network）
│   ├── diagram_generator.py           # 图表/画布生成
│   ├── shared_filter.py               # 通用过滤基础设施（AutoNoiseFilter）
│   ├── project_scope.py               # 项目范围管理（含 vendored/venv 过滤）
│   ├── llm_integration.py             # LLM 集成（超时/重试/JSON 截断容错/预算）
│   ├── business_analyzer.py           # 业务语义分析（提示注入防护）
│   ├── cache_manager.py               # 缓存管理
│   ├── gitnexus_client.py             # GitNexus 客户端
│   ├── governance_audit.py            # 11 个检测器
│   ├── agent_security_auditor.py
│   ├── sca_checker.py
│   ├── tech_debt_detector.py
│   ├── integrity_checker.py
│   ├── blind_spot_detector.py
│   ├── innovation_propagation_detector.py
│   ├── junk_detector.py
│   ├── resource_gap_detector.py
│   ├── code_simplifier.py
│   ├── project_maturity_checker.py
│   ├── memory_layer.py                # 记忆引擎：增量同步 + 语义查询
│   ├── memory_quality.py              # 记忆质量评估 + 补全
│   ├── prompt_asset_manager.py        # Prompt 资产版本化 / 对比 / A-B 测试
│   ├── prompt_analyzer.py             # Prompt 分析
│   ├── prompt_extractor.py            # Prompt 提取
│   ├── innovation_engine.py           # 创新识别引擎：结构化创新 + 缺口按价值挑选
│   ├── design_registry.py             # 已知设计库（别名归一）
│   ├── owasp_compliance.py            # OWASP LLM Top 10 合规检测
│   ├── change_guard.py                # 变更守护：AI 代码退化检测
│   └── change_report.py               # 人话版变更报告
├── config/                            # 配置（settings.py + 本地 config.json，含密钥，已 gitignore）
│   └── settings.py
├── utils/
│   └── helpers.py
# tests/ 已移除（v4.0.2 回归测试不入库）
├── cache/                             # 运行时缓存（.gitignore 已忽略）
├── coderef-report/                    # 输出报告（.gitignore 已忽略）
├── demo-app/                          # 前端审查测试实例（含 6 个预置交互问题）
├── setup.bat                          # Windows 配置向导
├── requirements.txt
├── MCP_SETUP.md                       # 详细配置指南
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

### v4.0.2 — 审计证据透明化（区分审计发现与历史数据）

- **知识图谱查询返回构建时间**：`coderef_query` 的所有查询类型（stats/entity/callers/callees/impact/relations/file_entities/search/call_graph）统一附带 `kg_built_at` 与 `kg_note`，明确提示"图谱仅当运行 audit/docs/architecture 后才会重建，代码有改动需先重建再查询"，避免把旧图谱当成本次审计结论
- **审计结果携带证据字段**：`coderef_audit` 返回新增 `evidence` 块，包含本次扫描时间 `scan_ts`、知识图谱构建时间 `kg_built_at`、本次扫描文件快照 `file_snapshot`（mtime+size），并声明统计口径仅覆盖快照所列文件、不代表修复状态
- **报告显式标注统计口径**：审计报告头部新增"统计口径"章节，写明本次扫描时间、图谱构建时间，并声明 HIGH/MEDIUM/LOW 均为审计发现、不代表任何修复状态，修复需对照 git 提交单独核实
- 移除 `tests/` 目录（回归测试不入库，避免内部实现细节外透）

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
- 回归测试：新增 `test_code_review.py`、`test_frontend_inspector.py`

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
