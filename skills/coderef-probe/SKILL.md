---
name: coderef-probe
description: L1 小阶段治理编排 Skill（类 CodeRabbit，变更驱动）。把 CodeRef-AI 的探查/防护工具串成「触发→策略路由→增量探查→确定性核验→变更防护→降噪→登记/升级」探查链，让编程 AI 在日常开发/CI 中每次变更都被快速探查、回归被拦截、结论被确定性核验。当用户要「提交前快速探查 / 变更回归防护 / AI 论断核验 / 误报收敛 / 小问题即时闭环」时使用。核心逻辑：变更驱动、增量轻量、分钟级快速闭环；确定性核验 + 变更守护 + 治理库沉淀是相对 CodeRabbit 的差异化优势。零新工具，纯文档编排；与 `coderef-governance`（L2 大阶段）/ `coderef-asset`（L3 资产沉淀）平级衔接。
---

# CodeRef 探查链 · L1 小阶段治理编排

> 定位：**变更驱动的轻量持续探查与防护**——每次提交/CI/日常改动都被快速探查、回归被拦截、结论被确定性核验、误报被收敛。与 `coderef-governance`（L2 大阶段治理）、`coderef-asset`（L3 资产沉淀）互补衔接：L1 发现存量结构问题 → 升级 L2 立项系统性规整；L1 发现高采用率设计 → 走 L3 沉淀复用；L2/L3 治理完成后 L1 持续维持防再腐化。

## 核心原则（必须遵守）

1. **增量优先、轻量闭环**：本层默认只查本次改动 + 其影响闭包（`scan` 单维优先，快一个量级），不重建图谱；只有 `audit_advisor` 判需要全量时才走 `audit`。
2. **确定性核验，杜绝自查幻觉**：LLM/CodeRabbit 的语义论断必须先过 `verify_findings` 知识图谱核验再采信；verdict 由确定性逻辑打出，无权改写。
3. **变更防护先于提测**：提交/提测前先 `change_guard`（能力签名对比，拦截"把旧代码改坏"），再 `change_report` 出人话变更说明。
4. **降噪收敛**：确认的误报写入 `whitelist`，同误报不重复报；不要反复纠结同一误报。
5. **与 L2 分工**：小问题（本次改动引入）登记治理库即时闭环；存量结构问题（重复/孪生/游离/越界，非本次引入）**升级 L2**（`coderef-governance` 五阶段立项），不在 L1 里硬啃存量。
6. **护栏**：任何探查/防护动作不动被测项目的 git 库与备份；测试不写开发侧文件夹、编程 AI 不写测试侧文件夹。

## 探查链 · 流程编排

```
触发 → 策略路由 → 增量探查 → 确定性核验 → 变更防护 → 降噪 → 登记/升级
```

| 步骤 | 工具 | 说明 |
|---|---|---|
| 1. 触发 | `gov_schedule`（定时）/ git hook（变更）/ 手动 | cron/CI 或提交钩子或编程 AI 每次改完代码触发 |
| 2. 策略路由 | `audit_advisor` | 判本次该增量还是全量（变更信号 + 影响闭包 + 图谱新旧）；调用前建议先 `coderef_memory(action=sync)` 建基线 |
| 3. 增量探查 | `scan`（单维，快一个量级）/ `audit`（全维） | 增量只查本次改动 + 影响闭包；`audit_advisor` 判**全量**时走 `audit` 全量范围（非增量闭包）；`scan_list` 查可用维度 |
| 4. 确定性核验 | `verify_findings` | 用知识图谱 + 静态原语核验 LLM/CodeRabbit/探查论断，杜绝自查幻觉 |
| 5. 变更防护 | `change_guard` + `change_report` + `flow_verify` | 提交前拦截"把旧代码改坏"（校验链删/重试削弱/约束移除/回归风险）；出人话变更说明；对改动相关入口核验期望链路无 `outside` 新增 |
| 6. 降噪 | `whitelist` | 确认的误报入白名单，收敛下次噪声 |
| 7. 登记/升级 | `gov_start`/`gov_issues`/`gov_transition`（小问题即时闭环）；升级 L2（存量结构问题） | 小问题先 `gov_start` 建档（差距自动导入为工作项）→ `gov_issues` 确认登记 → `gov_transition` 流转即时闭环；存量问题移交 `coderef-governance` |

**闭环判定**：增量回归 = 0（`change_guard` 无退化 + `flow_verify` 期望链路无 `outside` 新增）+ 白名单收敛（同误报不重复报）。三者满足即本轮 L1 探查闭环。

## 典型场景编排

### 场景 A · 提交前快速探查（编程 AI 每次改完代码）

1. `coderef_memory(action=sync)` → 建立/刷新基线（首次或图谱较旧时）。
2. `coderef_audit_advisor` → 判增量/全量。
3. 增量 → `coderef_scan`(tool=gov/td/...) → 单维快速探查本次改动 + 影响闭包；**全量 → `coderef_audit` → 全维探查（全量范围而非增量闭包）**。
4. `coderef_change_guard` → 变更前后能力签名对比，拦截回归；`coderef_change_report` → 人话变更说明。
5. `coderef_flow_verify`(entry=改动相关入口, steps=[期望链路]) → 确认无 `outside` 新增（回归不变量，闭环判定的组成）。
6. 有疑问的论断 → `coderef_verify_findings` → 确定性核验后再采信。
7. 确认误报 → `coderef_whitelist`(action=add) → 收敛。
8. 本次改动引入的小问题 → `coderef_gov_start` 建档（差距自动导入为工作项）→ `coderef_gov_issues`(view=open) 确认登记 → `coderef_gov_transition` 流转即时闭环。

### 场景 B · 论断核验（LLM / CodeRabbit / 探查结论）

`coderef_verify_findings`(findings=[...]) → verdict（确证/证伪/部分确证/无法核验）+ 证据链 + 影响面；`entry` 可指定入口核验符号是否在关键管线内。
> 边界：`verify_findings` 是**核验**不是评审——它确证"引用目标真实存在"，不代表语义结论正确。语义评审交 CodeRabbit 本体或 LLM，Coderef 只做确定性核验（Coderef 自建完整探查链）。

### 场景 C · 定时体检（周期触发）

`coderef_gov_schedule` → 生成 `run_cycle.py` 纳入 cron/CI → 开新周期 + 导入差距 + 产出报告；离期未收尾的 open 周期会提醒复检。存量结构问题（重复/孪生/游离/越界）→ 升级 L2 `coderef-governance` 立项。

## 与 L2/L3 的衔接

- **升级**：L1 探查发现存量结构问题（非本次引入）→ 记为 L2 候选立项，走 `coderef-governance` 五阶段（map→target→refactor→verify→health）系统性规整。
- **沉淀（→L3）**：L1 探查发现被多 workflow 采用的设计（传播缺口收敛候选）→ 走 `coderef-asset` 沉淀链（`innovation` 识别 → `registry` 登记 → `asset` 固化），防高价值设计流失。
- **维持**：L2/L3 治理完成后 → 回到 L1 持续探查，防止规整过的区域再腐化（回归被 `change_guard`/`flow_verify` 拦截）。
- **趋势**：L1 每次探查产出是 L2 health-cycle 的跨期趋势输入（`gov_report` 跨期对比）。

## 常见陷阱

- **不要用 L1 啃存量**：存量结构问题（重复/孪生/游离/越界）升级 L2，不在 L1 里硬啃——L1 是增量轻量，L2 是全量重度。
- **不要把 outside 当失败**：`flow_verify` 的 `outside` 是诚实状态，可能印证真实双入口形态，如实转述。
- **不要跳过核验直接采信**：LLM/CodeRabbit 论断未经 `verify_findings` 核验，不视为事实。
- **不要反复报同一误报**：确认误报写入 `whitelist` 后不再重复排查。
- **所有工具都要传 `project_path`**：必填参数，指向被测项目路径。
