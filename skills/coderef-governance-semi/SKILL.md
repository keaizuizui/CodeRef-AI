---
name: "coderef-governance-semi"
description: "治理主链「半自动确认版」。在 coderef-governance 五阶段基础上插入软确认门：每阶段产出交付物（简报/画布/清单）并停下让用户确认，未确认不得进下一阶段；纯读诊断阶段允许自动继续但必须汇总汇报。当用户说「半自动 / 一步步来 / 需要我确认 / 首次治理 / 架构方向要拍板 / 和用户确认架构」时触发；说「重新扫 / 体检 / 复扫」走全自动 coderef-governance。"
---

# CodeRef 治理主链 · 半自动确认版（软门禁编排）

> 定位：**全自动版 `coderef-governance` 的确认增强版**。两者共用同一 MCP 工具链与五阶段，唯一差异 = **每阶段一道软确认门**：产出交付物 → 停下汇报 → 等用户答复后才进下一阶段。解决外部实测暴露的问题——AI 把治理主链当工具清单一次性跑完，用户全程无感知、无法在架构方案上拍板。

## 触发路由（先用这张表决定走哪个版本）

| 用户说法 | 路由 |
|---|---|
| 「重新扫一遍治理 / 体检 / 复扫 / 定期检查」 | **全自动** `coderef-governance`（连续执行，一次汇报） |
| 「半自动 / 一步步来 / 需要我确认 / 首次治理 / 架构方向要拍板 / 和用户确认架构 / 你决定方案我来定」 | **本 SKILL（半自动）** |
| 拿不准 → 首次治理/涉及架构决策默认走半自动，复扫/体检默认走全自动；若用户中途说「先别继续，确认下」→ **立即停下进入半自动门** |

## 软确认门机制（本 SKILL 的核心，必须遵守）

> **软门禁定义**：默认每阶段结束必须交付 + 停下等用户确认；**例外**——该阶段为纯读诊断（不动 target_arch、不改任何文件）时，允许自动继续，但必须在阶段结束汇总汇报当前结论，并预告下一步，用户可随时打断。

**硬规则（无论软/硬门禁都不变）**：
1. **交付物优先**：每阶段结束，先产出人可读交付物（文字简报 + 画布/清单文件路径），再提问。**不允许只报一句「跑完了」**。
2. **AskUserQuestion 提问**：每个确认门用 AskUserQuestion，选项至少含「确认继续 / 调整 / 跳过此门」，让用户明确表态；用户选「调整」时按反馈修订后重交。
3. **未确认不进下一阶段**：用户在门处未答复或选调整而未定稿前，**不得自动执行下一阶段动作**（纯读例外见上）。
4. **工具失败诚实转述**：某工具被自身缺陷挡住（如 flow_canvas 特殊符号解析错误）时，如实说明原因与替代方案（如 flow_verify 补位），**不静默绕行**，并在交付物中标注。

### 梦钩子

- **先看梦再摸底**：开工前读 `context.memory_digest.dream` 或 `coderef_dream(action=read, limit=5)`，取上次治理目标与未决问题作为线索。
- **梦只出草稿**：梦提供的 `target` / `refactor` 草稿只是假设，定标、差距、验证仍由确定性工具给出，不得把草稿当已确认结论。
- **确认门带梦的未决问题**：M-FW / M2 / M3a 等确认门提问时，可把梦的 `open_questions` 一并交给用户判断，但必须标注“这是梦里的假设，证据是 …”。
- **决策后 seed**：定标、治理范围、豁免等关键决策落定后，seed 1–3 条（每条 ≤200 字）；不自动调用 `coderef_dream(action=run)`。
- **草稿需确认**：`refactor_draft` 草稿用 `coderef_dream(action=drafts)` 读取，可在确认门带给用户；应用前必须走 `coderef_arch_verify` / `coderef_target_adopt`。

> 工具面：本 Skill 默认按 `full` profile 编写；启用 `lite` 时，未暴露的治理工具请通过 `coderef_plan/execute` 链调用，或临时切回 `full`。

## 五阶段 × 确认门（复用 governance 工具链）

> 工具与编排细节与 `coderef-governance` 完全一致，本 SKILL 只标注每阶段尾的**确认门**。加载本 SKILL 前先读 `coderef-governance/SKILL.md` 获取工具参数与编排 gate（G1→L1 probe / G2→L3 asset / G3 回主链）全文，两版共享同一工具链。

### 场景 ① map-pipeline · 捋管线（纯读，软门禁例外）

**执行**：`coderef_architecture`（P0-B 真身/孤本 fan_in、P0-C 孪生簇）→ `coderef_arch_audit`（健康度）→ `coderef_flow_canvas`（L0→L3 逐层下钻）→ `coderef_query`（可疑真身交叉查证）。

**交付物**：「架构认知简报」——图谱规模（节点/边/覆盖率）、分层结构、健康度、业务主干流、真身/孪生/断点结论。

**确认门 M1**：纯读 → 允许自动进入 ②，但**必须先汇总简报**并 AskUserQuestion：「这个管线认知对吗？」（确认继续 / 调整关注点 / 就此打住只摸底）。选「就此打住」则走全自动档的 ⑨ 软入口决策门归档。

### 场景 ② define-target · 定标（**强制确认门**）

**执行（框架确认 → 草稿确认 → 再落盘，不先写盘）**：
0. **框架确认门 M-FW（硬门禁）**：调 `coderef_arch_canvas`（或 `arch_framework` 归纳）产出**业务架构树初稿**（入口 → 主功能 → 子功能 → 步骤，大白话）——AI 自动归纳 + 辅助大白话，人工负责确认框架。**必须停下** AskUserQuestion：「业务框架对吗？」（确认框架 / 修正节点标题与层级 / 重新归纳）。**确认后仅保留在草案中，不写盘**；step 3 落盘时才按 `coderef_target_arch_set` 的 business_flows 形状映射写入（业务树 ↔ 目标架构双向一致）。
1. **产出目标架构草案（不持久化）**：基于已确认框架 + 模板/现有 target_arch，整理「目标架构草案」（角色/目标模块/业务流），供用户审阅——此步**不调用会写盘的工具**。
2. **确认门 M2（硬门禁）**：**必须停下** AskUserQuestion：「定标对吗？治理范围圈哪些？」（确认落盘 / 调整后重交 / 跳过定标仅诊断）。**未确认不得落盘**。
3. **落盘与差距（确认后才执行）**：`coderef_target_arch_set`（写入 target_arch.json）→ `coderef_target_arch_get`（核对）→ `coderef_arch_gap`（差距清单）→ `coderef_role_boundary`（符号越界）→ `coderef_arch_canvas`（业务树 + 业务流程泳道；技术分区图走 `coderef_architecture`）。

**交付物**：「业务架构树（已确认框架）+ 目标架构草案 vN + 差距清单」——业务树层级/大白话、角色/目标模块/业务流摘要、差距按严重度分组、画布文件路径。

### 场景 ③ refactor-along · 照管线治理（**强制确认门 ×2**）

**执行**：`coderef_gov_issues`（取队列）→ `coderef_refactor_plan`（差距→任务卡）→ 机械性归属走 `coderef_target_adopt`(dry_run 预览→确认) → gate G1（改生产代码前转 `coderef-probe`）→ `coderef_gov_pipeline` 半自动闭环 → `coderef_arch_gap` 复查。

**确认门 M3a（治理范围，硬门禁）**：任务卡清单产出后**必须停下** AskUserQuestion：「治理范围与优先级？」（按卡全部治理 / 只治高优 / 标记豁免项）。方向决策（收敛哪条真身/拆不拆模块）由用户拍板后执行。

**确认门 M3b（每项动码前，硬门禁）**：涉及修改/删除生产代码的治理动作，逐项向用户确认后再执行；机械性归属（只动 target_modules 配置）可连续执行但汇总展示。

### 场景 ④ verify-advance · 验通推进（纯读+状态流转，软门禁例外）

**执行**：`coderef_flow_verify`（期望链路）→ `coderef_arch_verify`（对齐度后验）→ `coderef_gov_transition`（状态流转）→ `coderef_gov_report`(action=report)。

**交付物**：「治理验收结论」——对齐度前后对比、差距下降、flow_verify 状态表、outside 项如实标注。

**确认门 M4**：纯读 → 允许自动完成，但**必须汇总**并 AskUserQuestion：「达标可验收吗？」（确认闭环 / 继续修未达标项 / 归档留档）。

### 场景 ⑤ health-cycle · 周期体检（纯读，软门禁例外）

**执行**：`coderef_gov_start`（建档）→ `coderef_gov_issues` → `coderef_gov_report`(board/report) → `coderef_gov_close`。

**交付物**：「体检周期报告」——跨期趋势、open/high/复发统计。

**确认门 M5**：AskUserQuestion：「关期归档？还是要沉淀资产（gate G2 触发 asset）或继续治理？」

## 编排 gate（与全自动版共享）

G1（改码前 → `coderef-probe`）、G2（可复用成果 → `coderef-asset`）、G3（回主链/回 L1）**条件命中即强制转场**，与 `coderef-governance` 完全一致；转场后回到本 SKILL 时**先补走未确认的门**，不重置进度。

## 常见陷阱

- **不要用「等最后一起问」替代逐步确认**：确认门的意义是每阶段对齐，攒到最后问又退回一次性模式。
- **不要把软门禁例外当借口连续跑完**：纯读例外只覆盖「无写操作 + 无方向决策」；涉及 target_arch 落盘、改码、豁免、立案方向，必须停下问。
- **交付物不给路径/不给清单** = 没交付：简报要可核验（文件路径、数字、清单），不是形容词。
- **工具失败不要静默绕行**：如实说明（如 flow_canvas 特殊符号解析缺陷 → 用 flow_verify 补位），让用户知道链路里发生了什么。
- **所有工具都要传 `project_path`**：必填参数，指向被测项目路径。
