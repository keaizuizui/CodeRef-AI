<!-- AI Summary: CodeRef-AI exposes 52 MCP tools that give coding AI a deterministic "audit brain" and give non-programmers a readable view of their project. It covers audit, knowledge graph, architecture diagnosis, flow verification, change guard, OWASP, deterministic verification, and prompt compliance — all pure static analysis that is reproducible with no LLM. LLM is only used for synthesis tasks (wiki, report, code review) and semantic evaluation (coderef_eval) and hard-blocks honestly without an API key. It ships three orchestration Skills (L1 probe / L2 governance / L3 asset) that turn the 52 tools into a compact chain. It builds a closed loop: deterministically verify LLM/CodeRabbit claims, govern legacy structure along a map→target→refactor→verify→health mainline, and solidify/replicate reusable design assets. Best for: non-programmers who use a coding AI and want to confirm their project runs as intended, and teams that want AI that augments rather than hallucinates. -->
[![MCP Badge](https://lobehub.com/badge/mcp/keaizuizui-coderef-ai?style=flat)](https://lobehub.com/mcp/keaizuizui-coderef-ai)

# CodeRef-AI — 编程 AI 的治理外脑，非编程人员的技术助理

**Version 5.19.4** | Python 3.10+ | MCP Protocol | PolyForm Noncommercial 1.0.0

> 给编程 AI 一双确定性的眼睛，给非编程人员一张看得懂的工程体检单。

---

## 它是什么

CodeRef-AI 通过 MCP 协议暴露 **57 个工具**，同时服务两类人：

- **编程 AI 的治理外脑**：让 AI 不再逐文件读代码，而是像查数据库一样查询项目的结构、调用链与风险；编程 AI（或 CodeRabbit）给出论断时，还能用静态图谱做确定性核验，再决定采不采信。
- **非编程人员的技术助理**：把代码变成通俗的健康仪表盘、Wiki 与流程确证。你只需定义「入口 A 应该依次经过步骤 B→C→D」，`coderef_flow_verify` 就会在调用链里给出确证 / 在管线 / 存疑 / 缺失四种状态——不用读代码，也能确认项目有没有按你的设想运转。

多数 AI 审查工具把结论建立在「大模型读代码」上，而模型会幻觉。CodeRef 反过来：**审计、图谱、架构诊断、流程验证、变更守护、OWASP、论断核验等核心能力全部是纯静态分析**，结果确定、可复现——同一个项目每次跑出同样结论。LLM 只用于 Wiki、业务报告、创新排查、产出物语义评估（`coderef_eval`）等「要人话」的场景，未配置 API Key 时会明确硬阻断（`coderef_eval` 返回 status=SKIP）并提示配置，绝不降级编造。

---

## 三条编排主线：57 个工具不是散件，而是一条主链

57 个工具虽多，但 CodeRef 把它们整理成**三条互补的编排主线（Skill）**，编程 AI 只需知道自己该在哪条链上；L1/L2/L3 之间用**编排 gate 强制转场**（命中条件才转，简单任务不被反复切链拖累），并由 `coderef-mcp` 的「顶层入口判定」在动身前先选对链：

```
L1  coderef-probe      · 小阶段 · 变更驱动的轻量探查与防护（类 CodeRabbit）
L2  coderef-governance · 大阶段 · 周期驱动的存量工程系统性规整
L3  coderef-asset      · 资产层 · 把治理/开发成果沉淀成可复用设计
```

- **L1 探查链**：提交/CI 前，增量、分钟级地快速探查——`change_guard` 拦截「把旧代码改坏」、`verify_findings` 确定性核验 LLM/CodeRabbit 论断防自查幻觉、误报进白名单收敛、小问题即时闭环；存量结构问题升级 L2。
- **L2 治理主链**：把被反复修改搞乱的存量工程捋回正轨——先辨清真身/孪生/孤本，再沿 `map → target → refactor → verify → health` 五阶段规整，用 9 类确定性差距生成任务卡，靠四维对齐度确认真正回正轨，最后用定期体检维持。
- **L3 资产链**：把值得复用的设计提炼成资产，防止高价值设计随项目迁移流失——沉淀有门槛（≥2 处采用 + 证据防污染）、命名先归一、复刻不自动改源码。

跨链转场由**编排 gate 强制**兜底，不靠调用方自觉：L2 ③ 改码前 → gate G1 转 L1 做变更探查/防护；L2 ③ 收尾/⑤ 体检/L2 软入口档摸底发现可复用设计 → gate G2 转 L3 评估沉淀；L1 发现存量结构问题 → gate G3 回 L2 立项，L3 复刻落地 → gate G3 回 L1 验证。真·屎山项目可先进 **L2 软入口档「先架构诊断」**只摸底（架构梳理 + 治理价值决策门，不动生产码），价值成立再升级正式主链，价值不足则停在摸底不硬上全链。详见仓库 `skills/` 目录下的 4 个 Skill（含 `coderef-mcp` 的「意图 → 工具」速查表与顶层入口判定）。

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

## 给调用方 AI 的结论信封（v5.18.12 起）

真正读结论的往往是对方的 AI，而它的用户可能不懂代码。为避免把「输入过期」说成「工具坏了」，6 个高频工具的返回顶层新增 `envelope`：

```json
"envelope": {
  "verdict": "定标过期：本轮对齐度/覆盖率不可用，不是代码问题",
  "usable_for_decisions": false,
  "blocked_by": [{"id": "stale_target_arch", "owner": "consumer",
                  "human": "target_arch 已过期，请先重新定标", "limitation": "KL-01"}],
  "owner_actions": {"coderef": [], "consumer": ["coderef_target_arch_set"]},
  "cannot_conclude": ["对齐度", "覆盖率", "差距条数作为代码质量结论"],
  "guardrails": ["定标过期时任何基于 target_arch 的指标都不可对外引用"],
  "limitations": ["KL-01"]
}
```

- **归属模型**：`owner=coderef` 才是工具缺陷；`consumer` 表示需要调用方先重跑/重定标；`by_design` 是已知取舍。
- **已知限制登记表 KL-01~KL-09**：定标过期、IMPORTS 只建内部边、CALLS 按 import 作用域、模块数多口径、architecture 不产 findings、过期产物 unknown、备份需 whitelist、动态调用不覆盖、文档只认 Wiki 目录。
- **握手契约**：`coderef_version(project_path)` 返回 `consumer_contract`（3 条必读 + KL 全表 + 字段说明），调用方 AI 无需加载本仓 Skill 也能正确转述。
- **覆盖工具**：`coderef_audit` / `coderef_scan` / `coderef_report` / `coderef_arch_gap` / `coderef_arch_verify` / `coderef_interpret(action=health)`；旧字段全部保留，向后兼容。

### 结果级新鲜度硬戳（v5.19.1 起）

信封新增 `freshness` 块，滞后信息直接贴在结论上，调用方不查 `coderef_version` 也能看到：

```json
"freshness": {
  "artifacts_used": [{"key": "kg_db", "exists": true, "modified_at": "2026-11-09 10:00:00",
                      "code_newer": true, "stale_by_age": false, "refresh_hint": "coderef_memory(action=sync) 或重跑 coderef_audit"}],
  "code_newer": true,
  "stale_reason": "代码比产物新: 知识图谱",
  "refresh_action": "coderef_memory(action=sync) 或重跑 coderef_audit"
}
```

- **`coderef_query` stale 感知**：查询前比对代码 mtime 与图谱 `built_at`，代码更新超过阈值（默认 30 分钟或 3 个文件）时结果标 `stale=true`，并自动排入后台增量 `sync`，返回 `sync_task_id`；`<project>/.coderef/guard_config.json` 的 `query.mode=block` 可改为同步刷新后重查。
- **图谱文件级增量**：`coderef_memory(action=sync, mode=incr)` 只重建变更/删除文件及其 import 依赖方的节点与边，并与全量重建结果逐行一致；无变更时不重建。

### 统一范围层与前置闸门（v5.19.2 起）

- **`.coderef/scope.json` 单一事实源**：新增 `coderef_scope` 工具（`add/remove/list/verify`）。`exclude_glob`（路径模式）、`ignore_lib`（第三方库名）、`include_override`（误伤找回）一次写入、后续审计/图谱/SCA 自动生效；`add` 立即返回生效验证回执（排除文件数、按工具计数、与保留文件同名的可疑误伤）。
- **scope 边界**：只作用于扫描与检索，**不作用于 change_guard**——忽略的是审计噪音，不是安全责任。
- **precondition 四闸门**：G-A 未 recover、G-B 图谱过期、G-C 定标缺失、G-D 审计产物超龄。默认 `warn`（返回体附 `context.gate_warnings`），`<project>/.coderef/guard_config.json` 设 `preconditions.mode=block` 后返回 `status=blocked` + `envelope.blocked_by.owner=consumer` + `owner_actions`；`coderef_version`/`task_status`/`scan_list`/`scope` 等只读工具永远放行。

### 调度层 coderef-dispatcher（v5.19.3 起，形态 A）

编程 AI 从「编排 57 个工具」降级为「调用 4 个调度工具」，中间工具返回不再逐条进主 AI 上下文：

- **`coderef_plan`**：意图（probe / single_audit / recover / governance / asset）→ 计划卡（步骤、预计工具数与时长、用户决策点），纯规划不执行。
- **`coderef_execute`**：按计划或意图执行，逐步过前置闸门；缺 recover 先 recover、图旧先 sync（违规自动补齐），G-C 定标缺失等需用户决策的步骤**暂停浮出**，不替用户做治理决策；默认后台执行返回 `task_id`。
- **`coderef_dispatch_status` / `coderef_dispatch_report`**：前者看当前步骤与闸门记录，后者取最终摘要信封（结论 + freshness + KL + `owner_actions` + 折叠的步骤索引 + 上下文节省比）。
- **链定义**：`core/dispatch_chains.yaml` 是唯一事实源（L1/L2/L3 步骤、失败策略、决策点）。轨迹落盘 `.coderef/dispatch/<task_id>.json`；YAML 缺失/损坏时 `coderef_plan` 明确报错并给出裸工具回退建议，57 个工具照常可用。

### LLM 意图层（v5.19.4 起，形态 B，可选）

- **`coderef_plan(intent_text="用大白话说目标")`**：由 LLM 把自然语言归到 5 个链意图之一，计划卡标 `mode=llm` 与 `router{intent, confidence, reason}`。LLM **只选链**，审计/图谱/架构结论仍全部来自规则引擎与静态分析。
- **无 Key 硬阻断**：未配置有效 API Key 时返回 `status=blocked` + `mode=rule`，提示改用 `intent` 枚举（形态 A），不猜不编。
- **置信度不足不执行**：低于 0.6 或返回不可解析时 `status=needs_confirmation` + 候选意图，交由用户/主 AI 确认。
- **确认门**：`coderef_execute` 不接受未确认的 `intent_text`——必须先 `coderef_plan(intent_text=...)` 拿计划卡，再 `coderef_execute(plan_id=...)`。

---

## 快速开始

如果你是**非编程人员**：把这部分说明交给你的编程 AI，它会帮你完成安装、配置和第一轮分析。你真正要做的，是最后打开它生成的**项目评估台**和 Wiki，看懂自己的项目。如果你是**自己动手**：照下面四步走。

### 1. 安装

只依赖纯 Python 包，不触发 C 源码编译，Python 3.10-3.14 免编译直接装好。

```bash
git clone https://github.com/keaizuizui/CodeRef-AI.git
cd CodeRef-AI
pip install -r requirements.txt
```

### 2. 配置 LLM（可选）

> 审计、图谱、架构诊断、流程验证、变更守护、OWASP **不需要 LLM**，纯静态即可运行。仅 LLM 型工具（Wiki、业务报告、代码审查、Prompt 资产、创新识别、`coderef_eval` 产出物语义评估）需要 LLM；未配置 API Key 时这类工具被硬阻断并提示配置（`coderef_eval` 返回 status=SKIP），不产出降级/占位内容。

**Windows：** 运行 `setup.bat`。

**Linux / macOS：**

```bash
export CODEREF_API_KEY="your-api-key"
export CODEREF_PROVIDER="deepseek"        # deepseek / openai / ollama
export CODEREF_BASE_URL="https://api.deepseek.com"
export CODEREF_MODEL="deepseek-flash"      # 官方推荐: deepseek-flash / deepseek-v4-pro
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

# 3. 看健康读数（已归并到评估台；有评估台时直接返回其路径，不再产第二份仪表盘）
coderef_interpret(project_path="/path/to/project", action="dashboard")

# 4. 打开「项目评估台」（唯一用户入口）：五 Tab（概览记分卡 / 架构 / 产品 / UX / 技术）
#    页面自带仅回环的轻后端：点「重新评估」刷新分数，
#    点产品/UX Tab 的按钮即跑 F 轨业务契合度 / U 轨人格走查，不依赖 AI 在旁
coderef_report(project_path="/path/to/project", open_server=True)
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

运行 audit / architecture / docs / memory_sync 后自动构建 SQLite 知识图谱，持久化到 `<project>/cache/kg/`，一次构建跨会话复用。

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

**关系层口径（v5.18.7 / v5.18.11 起）：**

- **私有函数 100% 收录**：模块级 `_foo` 不再被丢弃（只跳过类方法重复副本与 dunder）。
- **CALLS 按 import 作用域解析**：只有「本模块内定义」或「确实被本文件 import（含 `as` 别名）」的调用才建边；同名但未 import 的 `check(...)`、`datetime.now` 等属性链一律不建边。精度优先、宁漏不假，因此 `fan_in` 偏保守，关键链路请用 `flow_verify` 核验。
- **IMPORTS 只建项目内模块边**：第三方/标准库 import 不建边，因此「零 IMPORTS 边模块」比例高属设计取舍，不代表依赖图缺失。
- **实测（外部 50 万行级项目）**：`check` 入边 7,788 → 12；跨模块且无 import 依据的 CALLS 66.1% → 17.1%；生产→测试调用 89 → 0。

---

## 57 个 MCP 工具速查

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
| `coderef_arch_canvas` | 业务架构画布：业务树 + 业务流程泳道（`with_flow`，默认开）；`format=data` 不渲染 HTML、直接返回结构化数据（`lanes`/`edges`/`clusters`）给 AI | 否 |
| `coderef_flow_canvas` | 交互式流程画布：自动提取业务管线 + 数据流 | 否 |
| `coderef_refactor_plan` | 差距清单 → 可执行重构任务卡 + 影响范围 + 验证标准 | 否 |
| `coderef_arch_verify` | 四维对齐度评分（职责 40%+依赖 30%+业务 20%+健康 10%）+ 差距复检 | 否 |
| `coderef_gov_start` | 建档体检周期并导入差距为治理工作项 | 否 |
| `coderef_gov_close` | 收尾周期，输出完成率/剩余/复发/豁免统计 | 否 |
| `coderef_gov_issues` | 查询治理工作项（预置视图 open/all/high/recurred/rejected/archived/overdue/assigned/recent） | 否 |
| `coderef_gov_transition` | 工作项状态流转 + 豁免 | 否 |
| `coderef_gov_report` | 体检报告 / 治理看板 / 项目总览（report 单期+跨期趋势；board 交互 HTML；overview 健康+架构+Wiki+人话解读+工作项总览；已合并原 gov_board） | 否 |
| `coderef_gov_pipeline` | 治理自动流水线：在途项 → 任务卡 → 复验 → Verified/附缺口 | 否 |
| `coderef_dynamic_probe` | 动态探针：静态挖掘动态信号（动态导入/装饰器注册/间接索引），零执行被检项目 | 否 |
| `coderef_gov_board` | 治理 Web 看板（兼容别名，转发到 gov_report action=board） | 否 |
| `coderef_gov_workspace` | 多代码库聚合治理 | 否 |
| `coderef_gov_schedule` | 定时体检：生成 run_cycle.py + 离期检查 | 否 |
| `coderef_role_boundary` | 符号级职责越界检测 | 可选 |
| `coderef_architecture` | 架构分析图谱 + 技术分区布局图（`arch_layout_*`，面向架构师/AI，非用户交付物） | 否 |
| `coderef_docs` | 项目 Wiki 文档生成 + 子项目探测 | 是 |
| `coderef_docs_read` | 按需读取已生成 Wiki 正文（返回内容而非路径） | 否 |
| `coderef_query` | 知识图谱结构化查询（9 种查询类型） | 否 |
| `coderef_review` | 代码审查：diff 变更审查 / 新项目全量语义首查 | 是 |
| `coderef_frontend` | 前端交互审查（按钮/菜单静态枚举 + 6 维度审查） | 是 |
| `coderef_report` | 聚合审计/图谱/Wiki 为自包含 HTML，`index.html` 即「项目评估台」（五 Tab：概览 / 架构 / 产品 / UX / 技术 + 健康记分卡；`open_server` 起回环服务） | 否 |
| `coderef_audit_advisor` | 审计策略判定（增量/全量）+ 重点维度 | 可选 |
| `coderef_whitelist` | 白名单管理 + 核心模块规则配置 | 否 |
| `coderef_scope` | 统一范围层：`.coderef/scope.json` 持久化忽略规则（exclude_glob / ignore_lib / include_override），add 即返回生效验证回执；只作用于扫描检索，不影响 change_guard | 否 |
| `coderef_plan` | dispatcher 计划卡：意图 → 链步骤 / 预计工具数与时长 / 用户决策点，纯规划不执行 | 否 |
| `coderef_execute` | dispatcher 执行链：逐步入闸门、违规自动补齐、需用户决策处暂停；默认后台返回 task_id | 否 |
| `coderef_dispatch_status` | dispatcher 任务状态：当前步骤、闸门命中与补齐记录、暂停决策点 | 否 |
| `coderef_dispatch_report` | dispatcher 最终摘要信封：结论 + freshness + KL + owner_actions + 折叠的步骤索引 | 否 |
| `coderef_task_status` | 后台任务状态查询 | 否 |
| `coderef_task_cancel` | 后台任务取消（协作式收尾） | 否 |
| `coderef_version` | 轻量版本探针（只读、零副作用）：秒级返回当前加载的版本号，无需 project_path，用于断言「进程加载版本 == 目标版本」，杜绝进程未重启导致的结果误判 | 否 |
| `coderef_eval` | 产出物语义评估（LLM-as-judge）：给 CodeRef 自身的 LLM 产出物加语义质量门禁——4 个指标（answer_relevancy 相关性 / faithfulness 忠实度 / hallucination 幻觉检出 / coherence 连贯性）+ 软硬判定（硬指标一票否决，防致命错误被平均分稀释）；action=assert 单条断言 / score 批量报告；只评自产，不评用户项目运行时产出 | 是 |

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
| `coderef_interpret` | 把确定性结论翻译成人话：health 健康总览 / dashboard（已归并评估台，直接返回其路径）/ wiki / prompt / assets | 可选 |

---

## 设计特性

| 特性 | 说明 |
|------|------|
| 不修改代码 | 审计、图谱、诊断等分析**只输出不执行**；唯一例外：`coderef_change_guard ensure_git` 会在被测项目无 git 时 `git init` 并写本地身份配置（守护引擎运转前提，仅该项目本地配置，不写全局） |
| 本地优先 | 分析完全在本地，审计和知识图谱无需网络，支持离线 |
| 隐私安全 | LLM 密钥存 `config/config.json`（已 gitignore），不提交 Git |
| 结构化输出 | 报告 Markdown，仪表盘 HTML，知识图谱 SQLite |
| 检查点续跑 | 管线每 2 分钟保存进度，中断后可恢复 |
| 后台任务 | 长任务（audit / docs）异步执行，轮询获取结果 |
| 项目隔离 | 每个项目独立缓存，切换项目不互相干扰 |
| 开源友好 | 敏感数据集中 `cache/` 与 `config/config.json`，删除即清理 |

---

## 产物地图：哪些给人看，哪些给 AI 用

CodeRef 会落很多文件，但**用户只需要看两个入口**。其余产物要么是详情页，要么是给编程 AI 消费的数据。

### 用户面（需要打开看）

| 入口 | 从哪来 | 看什么 |
|---|---|---|
| **项目评估台** `<project>/.coderef/report/html/index.html` | `coderef_report` | 概览（结论）/ 架构（业务树 + 业务流程）/ 产品 F 轨 / UX U 轨 / **技术**（原始分项 + 证据锚点 + 技术分区图）；`audit.html` / `kg.html` / `wiki.html` 是它的详情页。**按项目隔离**，多项目交替跑不会互相覆盖入口 |
| **架构治理看板** `gov_board.html` / `project_overview.html` | `coderef_gov_start(open_server=true)` / `coderef_gov_report` | 治理工作项流转、周期体检（L2 治理专用，与评估台定位不同） |

### 架构画布：三种视图，前缀已分离

| 文件前缀 | 生成者 | 回答什么 |
|---|---|---|
| `arch_canvas_*.html` | `coderef_arch_canvas`（arch_view） | 业务树：入口 → 主功能 → 子功能 → 步骤 |
| `arch_flow_*.html` | `coderef_arch_canvas`（arch_flow） | 业务流程：泳道（角色）× 阶段（触发→校验→执行→落库→通知→交付） |
| `arch_layout_*.html` | `coderef_architecture`（canvas_generator） | **技术视图**：角色分区布局图，面向架构师/AI 排查结构，非用户交付物 |

### 给编程 AI 的数据（不需要人工打开）

| 文件 | 用途 |
|---|---|
| `.coderef/scorecard.json` | 健康记分卡（CI 门禁、AI 读分） |
| `.coderef/product_fit.json` / `ux_fit.json` | F 轨 / U 轨结论 |
| `.coderef/business_semantics.json` | 业务语义标注（actor / stage / handoff / value_node） |
| `.coderef/audit_findings_<hash>.json` | 审计发现结构化产物（重渲染的输入） |
| `workflow_graph_*.html` / `flow_canvas_*.html` | 调用链/数据流调试视图，供 AI 定位，非交付物 |
| **AI 直接取数据**：`coderef_arch_canvas(format="data")` | 不渲染 HTML，直接返回 `tree` + `semantics`（`lanes` 泳道分组 / `edges` 邻接 / `clusters` 连通分量聚类）——AI 读不了交互页面，需要的是可复现的结构数据 |
| `health_dashboard_*.html` | **默认不再自动生成**（需 `coderef_audit(with_dashboard=true)`）；健康读数统一看评估台 |
| `interpret_dashboard_*.html` | `coderef_interpret(action=dashboard)` 已改为**直接返回评估台路径**；仅评估台不存在时才兜底渲染 |

> 原则：**用户面只有两个入口**——项目评估台（代码健康 / 架构 / 产品 / UX）与架构治理看板（治理工作项）。其余 HTML 均为技术视图或历史产物，不作为交付物交付给非技术读者。

---

## 项目结构

```
coderef-ai/
├── core/                             # 核心引擎
│   ├── mcp_server.py                 # MCP Server 入口（57 个工具）
│   ├── pipeline_runner.py            # 管线引擎（audit/architecture/docs + 知识图谱）
│   ├── tool_registry.py              # 工具注册中心
│   ├── review_strategy.py            # 审计策略判定（增量/全量 + 影响闭包）
│   ├── functional_review.py          # 功能审查（创新传播/结构复杂度等）
│   ├── report_renderer.py            # 报告站点渲染（index.html = 项目评估台五 Tab）
│   ├── scorecard.py / scorecard_weights.json  # 健康记分卡（确定性聚合 + 权重外置 + CLI）
│   ├── report_console.py             # 评估台回环轻后端（/api/scorecard、/api/config、/api/evaluate）
│   ├── product_evidence.py           # 产品证据包（能力/声称/首次价值路径自动枚举，全部带锚点）
│   ├── product_eval.py               # F 轨业务契合度（五问矩阵 + 链路核验 + 三档结论）
│   ├── personas.json / ux_eval.py    # U 轨人格走查（只判阻断 + Rage Map 业务后果列）
│   ├── business_semantics.py / arch_flow.py  # 业务叙事标注（actor/stage/handoff）+ 流程泳道画布
│   ├── code_review.py                # 代码审查（diff / 全量语义首查，evidence 标记）
│   ├── frontend_inspector.py         # 前端交互审查
│   ├── code_analyzer.py / ast_parser.py / code_models.py   # AST 分析
│   ├── code_knowledge_graph.py       # 知识图谱引擎（SQLite 持久化）
│   ├── health_dashboard.py           # 健康仪表盘（默认不再自动产出，健康读数统一看评估台）
│   ├── wiki_generator.py / wiki_ir.py / wiki_compare.py / wiki_cross_verify.py  # Wiki 生成与交叉验证
│   ├── flow_verify.py                # 流程合规验证
│   ├── arch_audit.py / arch_gap_analyzer.py / arch_framework.py / target_arch_schema.py / refactor_task_generator.py / arch_alignment_verifier.py  # 架构治理
│   ├── canvas_generator.py / workflow_graph.py / diagram_generator.py  # 可视化
│   ├── governance_audit.py / agent_security_auditor.py / sca_checker.py / tech_debt_detector.py / integrity_checker.py / blind_spot_detector.py / innovation_propagation_detector.py / junk_detector.py / resource_gap_detector.py / code_simplifier.py / project_maturity_checker.py  # 11 检测器
│   ├── memory_layer.py / memory_quality.py / prompt_governance.py / prompt_compliance.py  # 记忆与 Prompt 治理
│   ├── innovation_engine.py / design_registry.py / replicate_engine.py  # 创新与资产
│   ├── verify_findings.py / interpretation_platform.py / owasp_compliance.py  # 核验/人话/合规
│   ├── change_guard.py / change_report.py              # 变更守护
│   ├── version.py                   # 版本号唯一真源（__init__ / pyproject / mcp_server 均从此读取）
│   └── llm_integration.py / cache_manager.py / project_scope.py / shared_filter.py  # 基础设施
├── skills/                          # 三层编排 Skill（probe/governance/asset/mcp）
├── tools/check_version.py           # 发布前版本一致性校验（README/MCP_SETUP/CHANGELOG 与真源比对）
├── config/                           # 配置（settings.py + 本地 config.json，含密钥，已 gitignore）
├── docs/                             # 文档 + changelog 更新日志归档
├── cache/                            # 运行时缓存（已 gitignore）
├── .coderef/                         # 运行时产物：report/html 评估台、scorecard/findings（按项目隔离，已 gitignore）
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

> 3.X 与 5.X 系列的完整逐版本更新日志（v3.0 – v5.17.0）统一归档至 [docs/changelog/CHANGELOG.md](docs/changelog/CHANGELOG.md)；线上 README 只保留当前版本状态。

### 当前版本 v5.19.0 — 纪律代码化地基（自动守卫 + 记忆注入）

> 5.19.x 版本线第一版。核心命题：把「AI 该记得做的事」从 skill 文字移到服务端代码——不依赖调用方自觉。

- **自动变更守卫**：调用 audit / scan / query / report / interpret / arch_gap / arch_verify 时，服务端自动比对 git 指纹；代码变了就先跑 `change_guard`，结果附在返回体 `context.guard`，同一指纹不重复跑。
- **记忆摘要前置注入**：每项目进程内首次调重工具时自动 recover 操作记忆，压成 ≤800 token 摘要附在 `context.memory_digest`；记忆为空也如实标注，不冒充"没这项"。
- **AGENTS.md 自包含**：6 条红线内联，不再只写"见 CODEREF.md"。
- 完整实施真源见 [docs/5.19.x-版本线开发文档.md](docs/5.19.x-版本线开发文档.md)；后续 5.19.1–5.19.5 依次为：新鲜度硬戳、会话状态闸门、dispatcher 子 agent、LLM 意图层、观测层。

#### v5.18.13 — 结论信封、CALLS 作用域与陈旧输入防呆

> 承接外部审计第二轮/第三轮反馈，用「归属 + 限制」代替“每轮再修一遍”。工具数仍为 52，全部改动向后兼容。

- **结论信封**（5.18.12）：audit / scan / report / arch_gap / arch_verify / interpret(health) 返回 `envelope`，含 `usable_for_decisions`、`blocked_by.owner`、`owner_actions`、`cannot_conclude`、`guardrails`、`limitations`（KL-01~KL-09）。
- **握手契约**（5.18.12）：`coderef_version(project_path)` 返回 `consumer_contract`，调用方 AI 无需加载 Skill 即可正确区分「工具缺陷 / 输入过期 / 设计取舍」。
- **CALLS 按 import 作用域解析**（5.18.11）：同名未 import、属性链不建边；外部实测 `check` 入边 7,788→12、无 import 依据 66.1%→17.1%、生产→测试 89→0。
- **评估台可用性**（5.18.11）：画布复制进报告目录，修复 open_server 下三个 iframe 404；统计口径优先当前图谱 `built_at`。
- **陈旧输入防呆**（5.18.9）：`coderef_version` 的 `artifacts_report` 列出各产物生成时间、`stale_by_age`、`code_newer` 与刷新方式。
- **读数诚实化**（5.18.7/5.18.8）：治理/文档/对齐在证据不足时标 `unknown`；私有函数 100% 入图；扫描范围统一接入 whitelist。
- **验证**：全量 pytest **313 全绿**；`core/` pyflakes 0；`check_version` EXIT=0；`coderef-src` SHA256 一致。

#### v5.18.0 — 三轨评估（F 轨契合度 / B 轨条件触发 / U 轨只判阻断）+ 业务流程视图

> 承接用户 2026-11-05 拍板（采纳外部 skill 的「三轨层级」）：评估台从「产品/UX 两 Tab 平行」改为**三轨有主轴**——F 轨业务契合度必跑，B 轨业务模式条件触发，U 轨人格走查只判阻断、不独立评分。版本按 SOP 升 minor（5.17.0 → 5.18.0；P1/P2 合并落地）。
> - **产品证据包**（`core/product_evidence.py`，确定性、无 LLM）：能力清单**自动枚举**（业务架构树 + 前端菜单/表单 + 路由 + CLI 子命令），声称/首次价值路径/许可/付费/路线图信号全部带 `file:line`、路由或命令锚点；推不出的写 `unknown`，不写 0、不编造。
> - **F 轨五问矩阵**（`core/product_eval.py`）：逐能力核对「支撑哪个决策 / 输入现实性 / 输出可行动性 / 数据诚实度 / 频率匹配」；**输入现实性强制标 `proxy=true`**（代码只能推「有无上游数据源」，不能宣称用户手上有什么）；F2 链路核验优先用知识图谱 CALLS 边，图谱跑不出才降级并如实标注。
> - **三档结论与硬红线**：Go / Validate-first / No-go + 待验证假设清单；**无 LLM Key 只回证据包（status=SKIP）不产结论**；LLM 输出含市场/竞品数字即整份拒收；E4 必须进假设清单；业务逻辑不支撑决策的能力进「砍/改清单」而非修复清单。
> - **U 轨人格走查**（`core/personas.json` + `core/ux_eval.py`）：3 个内置人格（低耐心新手 / 谨慎比价者 / 时间紧迫决策者），7 项必填（身份/目标/行为特征/耐心/信任与验证习惯/价格敏感/放弃触发 ≥3 条/第一人称口吻），禁止「普通用户」；只输出 `blocking` / `partial` / `non_blocking`；**Rage Map 必带业务后果列**（愤怒 ≥4 且任务中断 → P0/P1，否则最高 P2）；无锚点不输出；全部 `simulated=true` + E4；只评 1280/1440/1920 桌面视口。
> - **页面与工具接入**：`POST /api/evaluate` 真实现（同项目同场景串行，写 `.coderef/product_fit.json` / `ux_fit.json` 后自动重渲染页面）；产品/UX Tab 直接展示结论、五问矩阵、Rage Map 与锚点；`coderef_frontend` 新增可选 `persona` / `journey`（不新增工具，工具总数仍 52）。
> - **业务流程视图**（`core/business_semantics.py` + `core/arch_flow.py`）：在图谱事实与画布之间补一层业务叙事标注——`actor`（泳道）/ `stage`（流程阶段）/ `handoff`（交接点）/ `value_node`（交付点）。静态启发式给事实锚点，LLM 只做命名与归类；**判不出的进「未归类」并计数，不假装已归类**。布局确定性：X=阶段（触发→校验→执行→落库→通知→交付），Y=角色泳道，同一份标注必出同一张图。`coderef_arch_canvas` 默认同时产出业务树与流程泳道两张画布，架构 Tab 可切换。
> - **U-49 修复**：根因在写入端——`_reuse_no_change` 只要图谱存在就短路复用，首轮不跑审计、不落 findings JSON。已改为「必须能读到 findings 才复用」，findings 同写项目侧 `.coderef/`，`/api/evaluate` 返回 `page_refreshed` 让页面在刷新失败时显式提示。
> - **U-50 修复**：评估台与画布静态服务在路径规范化**前**拒绝含 `..` 的原始请求（含 `%2e%2e` / 双重编码），`GET /.coderef/../index.html` 返回 404，不再看起来像目录穿越未拦截。
> - **U-51 修复**：报告产物按项目隔离——默认目录由工具仓共享的 `coderef-report/html/` 改为 `<project>/.coderef/report/html/`，`report` / `audit` / `docs` / 回环服务 / `interpret(dashboard)` 同源，多项目交替跑不再互相顶掉入口。
> - **审查与验证**：三层审查（pyflakes → Brooks-Lint → CodeRabbit）0 新增 finding；全量 pytest **265 全绿**；`check_version` 通过；`coderef-src` 同步 SHA256 一致。

### 历史版本

> v3.0 – v5.17.0 的逐版本记录（v5.17.0 评估台首版、v5.16.0 业务架构树、v5.15.0 可视化重写、v5.14.x 契约加固与债务治理、v5.13.x 补丁链）已完整归档至 [docs/changelog/CHANGELOG.md](docs/changelog/CHANGELOG.md)。README 只保留当前版本状态，避免同一份更新日志维护两处。

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