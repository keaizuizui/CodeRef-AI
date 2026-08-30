---
name: coderef-governance
description: 治理主链场景化 Skill（外部建议 E 物化）。把 CodeRef-AI 的 58 个 MCP 工具收敛为「5 阶段 × 每阶段 2–5 个高频工具」的少而精工具链，让编程 AI 不必直面全部工具也能沿主链把屎山捋顺。当用户要做「架构治理 / 存量屎山收敛 / 重复与孪生治理 / 定期体检 / 治理工作项流转」时使用。核心逻辑：人工先捋对管线（map→target），编程 AI 照清单治理（refactor→verify），周期体检维持（health）。L2 大阶段治理编排（本 SKILL）+ L1 小阶段治理编排（`coderef-probe` SKILL，变更驱动探查/防护）+ L3 资产沉淀编排（`coderef-asset` SKILL，治理成果→资产）互补衔接。
---

# CodeRef 治理主链 · 少而精工具链（L2 大阶段治理编排）

> 承接《建议书_治理主链与工具改造_20260826.md》§五「少而精工具链」与 §七 外部建议 E（场景化 Skill 封装层，P0 采纳）。
> 目标：把 58 个 MCP 工具收敛成 **5 阶段 × 每阶段 2–5 个高频工具**，其余工具按需按名调用即可。
> L2 编排定位：周期驱动的屎山系统性规整。已收编执行增强层（P1/P1 补）：② 引 `coderef_role_boundary`、③ 引 `coderef_refactor_plan`/`coderef_target_adopt`/`coderef_gov_pipeline`、④ 引 `coderef_arch_verify`——整改环节从「人工逐条手工改 + arch_gap 复查」升级为「游离一键纳入 + gov_pipeline 半自动整改闭环 + 人拍板确认」，但架构方向决策仍由人拍板（工具只做机械性归属动作）。
> L3 衔接（P3）：治理成果——③ 抽出的可复用公共工具、⑤ 体检发现的高价值设计（多 workflow 采用）→ 沉淀走 `coderef-asset` SKILL（资产沉淀编排），避免成果随项目迁移流失。

## 核心原则（必须遵守）

1. **先捋管线，再谈治理**：治理第一步永远是「用真实业务入口理清它当下实际怎么走」（哪是真身、哪是死线、哪在重复），产出人可确认的管线清单。管线确认后，目标架构与差距清单才可靠；否则游离/缺失全是虚数。
2. **L0→L3 逐层捋清铁律（）**：捋管线必须按自顶向下顺序走完四层——**L0 架构总览**（这是什么业务/商店还是饭店/客人是谁）→ **L1 模块盘点**（厨房/卫生间/前台/大厅有哪些模块，按层聚焦）→ **L2 模块内逻辑**（点进"业务工具"看内部 N 步流程）→ **L3 代码管线**（从模块穿透到具体代码流转/调用链）。**未捋清 L0-L3 不得进入定标（define-target）与差距分析（arch_gap）**；画布默认全量平铺是"并列展示"，必须先经层级导航（L0/L1/L2/L3 视图 + 面包屑回退）逐层捋清后，才叠加差距高亮（差距开关默认折叠）。
3. **结构性锈蚀 ≠ 回归复核**：`strategy=incr` 只用于「回归复核新增改动」；治理存量结构（重复/孪生/真身）必须走 `strategy=full` + 结构锈蚀扫描，否则双真身/重复库会被完全漏掉。
4. **真身判定看 fan_in，不看可达性**：双孪生入口的 flow_verify 结果可能完全同构（孤本也调 shared 层）。判定活跃真身 vs 无调用者孤本，唯一可靠信号是 `architecture` P0-B 的 fan_in（who-calls-me）。
5. **治理动作护栏**：任一治理动作前，确认目标不在 git 库/备份范围内（项目硬约束）；测试不写开发侧文件夹、编程 AI 不写测试侧文件夹，双册对账。
6. **闭环判定**：治理成功的量化信号 = `alignment.module_assigned` 上升 + gap 总数（尤其 unassigned/duplicate）下降 + `flow_verify` 期望链路 `outside` 收敛为 `ordered/in_pipeline`；大阶段收尾再叠加 `coderef_arch_verify` 对齐度四维（职责对齐/依赖健康/业务覆盖/代码健康）确认达标。

## 意图 → 工具快速路由（外部 A 兜底，轻量版）

> 编程 AI 不确定该用哪个工具时，先查这张表。同义词/别名 → 主工具。

| 用户意图（可能说法） | 主工具 | 备选/组合 |
|---|---|---|
| 项目健康吗 / 完整体检 / 有没有坏味道 | `coderef_audit` | + `coderef_arch_audit` + `coderef_owasp` |
| 代码结构乱 / 重复多 / 模块不统一 | `coderef_architecture` | + `coderef_arch_gap`（duplicate 差距） |
| 这个入口按不按我期望的流程走 | `coderef_flow_verify` | + `coderef_query`(callers) |
| 架构腐化 / 循环依赖 / 上帝模块 | `coderef_arch_audit` | + `coderef_architecture` P0-B |
| 哪个是真身 / 哪个是孤本 / 同名多实现 | `coderef_architecture`（读 P0-B/P0-C） | + `coderef_arch_audit` identity 摘要 |
| 治理差距清单 / 该治理什么 | `coderef_arch_gap` | + `coderef_gov_issues`（排队） |
| 差距清单转任务卡 / 治理作业单 | `coderef_refactor_plan` | + `coderef_gov_issues` |
| 治理半自动流水线（流转+复验闭环） | `coderef_gov_pipeline` | 机械性归属照卡自动执行，方向决策人拍板 |
| 重构后对齐度 / 是否达标 | `coderef_arch_verify` | 增量传 `changed_files` 快速反馈单卡 |
| 符号越界 / 职责归属错位 | `coderef_role_boundary` | + `coderef_arch_gap`（模块级缺失互补） |
| 治理工作项怎么流转 / 豁免 | `coderef_gov_transition` | 参数速查见下 |
| 定期体检 / 建档 / 闭环 | `coderef_gov_start` / `coderef_gov_close` | + `coderef_gov_report`（action=report/board） |
| 治理出成果想沉淀 / 创新识别 / 资产复刻 | `coderef_asset` / `coderef_innovation` / `coderef_replicate` | 详见 `coderef-asset` SKILL（L3） |
| AI 改完代码提交前确认没改坏 | `coderef_change_guard` | + `coderef_change_report` |
| 查调用关系 / 影响面 | `coderef_query` | 替代 grep，省 token |
| 安全合规（OWASP） | `coderef_owasp` | + `coderef_prompt_governance` |
| 上下文丢了，东西在哪儿 | `coderef_operation_memory`（action=recover） | 强制 gate（见 coderef-mcp 工作流 E） |

## 主链五阶段 · 场景化编排

> ①/②/⑤ 由人工/测试（或负责人陪同 AI）执行以「捋对管线」；③ 由编程 AI 执行；④ 由编程 AI 自证 + 测试复核。

### 场景 ① map-pipeline · 捋管线（人工/测试）

**目标**：用一个真实业务入口，理清它当下实际怎么走，产出人可确认的管线清单。

**工具（4 个）**：`coderef_architecture`（必看 P0-B/P0-C 真身/孪生）、`coderef_arch_audit`（健康度）、`coderef_flow_canvas`（三层画布）、`coderef_query`（交叉查证）

**编排**：
1. `coderef_architecture` → 读 P0-B（真身/孤本 fan_in）与 P0-C（同构孪生簇/目录级重复）——这是本阶段最大价值，别只看健康度数字。
2. `coderef_arch_audit` → 健康度 0-10 + cycle/god_module/large_module 存量结构症状。
3. `coderef_flow_canvas` → 三层画布，**按 L0→L3 逐层下钻**（）：先 L0 总览看业务定位 → L1 分层按层聚焦模块盘点 → L2 双击模块节点下钻其内部逻辑 → L3 只看代码层模块与依赖；面包屑回退，差距开关默认折叠、捋清后再叠加。
4. `coderef_query`(callers/callees) → 对可疑真身交叉查证。

**产出**：管线清单（真身/断点/孪生）+ 三层画布（L0-L3 已逐层捋清）。**常见坑**：不要用 `coderef_audit_advisor` 的 diff 焦点替代结构锈蚀扫描——它只认变更文件，对存量重复/孪生失明；未走完 L0-L3 就进 define-target/arch_gap 属违规（见核心原则 2）。

### 场景 ② define-target · 定标（人工/测试）

**目标**：把已确认的管线落成目标架构 + 差距清单（优先级队列）。

**工具（3 个）**：`coderef_target_arch_set`、`coderef_arch_gap`、`coderef_role_boundary`

**编排**：
1. `coderef_target_arch_set` → 落 target_arch.json（business_flows/constraints 顶层字段必须完整，否则 arch_verify/arch_gap 基于残缺架构假达标）。
2. `coderef_arch_gap` → 差距清单（含 duplicate/directory_duplicate 差距类型；游离区分真游离 vs 未建模）。
3. `coderef_role_boundary` → 符号级职责越界检测（模块归属正确但符号逾越所属角色边界：命名语义/调用边界/复用信号），与 arch_gap 模块级差距互补——同一目标架构下查「模块缺失 + 符号越界」两层问题。

**产出**：目标架构 vN + 差距清单（优先级队列）。**常见坑**：target_modules 需覆盖全部核心模块，否则游离清单失真（未建模被误报为真游离）。

### 场景 ③ refactor-along · 照管线治理（编程 AI + 人拍板）

**目标**：按②的差距清单逐项治理，每动一处可验证、可回滚。治理动作分两类：**机械性归属**（游离纳入/照卡执行，工具可半自动完成）与**架构方向决策**（收敛哪条真身/拆不拆模块，人拍板后执行）。

**工具（5 个）**：`coderef_gov_issues`（取队列）、`coderef_refactor_plan`（差距→任务卡）、`coderef_target_adopt`（游离一键纳入）、`coderef_gov_pipeline`（治理自动化流水线）、`coderef_arch_gap`（治理后复查）

**编排**：
1. `coderef_gov_issues`(view=high) → 取去噪后的工作项队列（已按真实 severity 排序、unassigned 置底）。
2. `coderef_refactor_plan` → 把差距清单转为可执行任务卡（type/operations/impact/verify，按执行顺序排序），作为逐项治理的作业单。
3. 游离模块（free/unmodeled）→ `coderef_target_adopt`(dry_run=true 预览 → 确认后落盘) → 按 role 批量追加 target_modules（机械性归属，幂等，已纳入跳过）。
4. 机械性归属 → `coderef_gov_pipeline`(issue_ids=[...]) → 自动走 Fixing→任务卡→复验→Verified 半自动闭环；架构方向决策由人拍板后执行（工具只做机械性归属动作，绝不自动改架构方向）。
5. 治理后 `coderef_arch_gap` 复查 gap 总数下降。

**护栏**：任一治理动作前，确认目标不在 git 库/备份范围内；绝不动 git 库与备份。

### 场景 ④ verify-advance · 验通推进（编程 AI 自证 + 测试复核）

**目标**：流程合规验证 + 对齐度后验 + 治理状态流转。

**工具（4 个）**：`coderef_flow_verify`、`coderef_arch_verify`、`coderef_gov_transition`、`coderef_gov_report`（action=report）

**编排**：
1. `coderef_flow_verify`(entry=入口, steps=[期望链路]) → 状态分 ordered/in_pipeline/outside/missing；`outside` 是诚实状态不是失败，如实转述。
2. `coderef_arch_verify` → 0-100 对齐度后验（职责对齐 40% + 依赖健康 30% + 业务覆盖 20% + 代码健康 10%）+ 差距复检清单；增量模式传 `changed_files` 可对单张任务卡快速反馈是否达标（③ gov_pipeline 复验也复用它）。
3. `coderef_gov_transition` → 沿状态机推进（Detected→Confirmed→Fixing→Verified→Archived）。
4. `coderef_gov_report`(action=report) → 单期报告 + 跨期趋势。

**gov_transition 参数速查（P2⑦）**：
- `transition`：必填 issue_id + action='transition' + to_state（如 Confirmed→Fixing 传 to_state='Fixing'）
- `reject`：必填 issue_id + action='reject' + reason（豁免理由必留，缺省会报错）
- `meta`：必填 issue_id + action='meta' + 至少一项（priority/assignee/due_date/note）；**勿传 to_state**（否则报「需 provide to_state」）
- 状态机强约束：仅 Confirmed→Fixing 可进 Fixing；仅 Verified 后可归档。非法跳转返回明确错误属正常，先 transition 到合法中间态。

### 场景 ⑤ health-cycle · 周期体检（人工/测试定期触发）

**目标**：定期建档体检、跨期趋势、闭环归档。

**工具（4 个）**：`coderef_gov_start`、`coderef_gov_issues`、`coderef_gov_report`（action=report/board）、`coderef_gov_close`

**编排**：
1. `coderef_gov_start` → 建档 open 周期 + 差距全量导入（去重/复发/豁免生效）。
2. `coderef_gov_issues`(view=high) → 看真实治理重点（已降噪）。
3. `coderef_gov_report`(action=board) → 交互看板（自动落盘 <project>/.coderef/gov_board.html，）；`coderef_gov_report`(action=report) → 单期报告。
4. `coderef_gov_close` → 收尾闭环（cid 缺省时自动定位当前 open 周期）。

**产出**：体检周期 + 跨期趋势报告。**定时化**：`coderef_gov_schedule` 生成 run_cycle.py 可纳入 cron/CI。

### L3 衔接 · 治理成果 → 资产沉淀（见 `coderef-asset` SKILL）

治理 ③ 抽出可复用公共工具、⑤ 体检发现高价值设计（多 workflow 采用）→ 走 `coderef-asset` 沉淀链：`innovation` 识别 → `innovation_review` 确认 → `registry` 登记归一（alias→canonical）→ `asset`(commit，≥2 采用 + evidence) 固化 → `asset_blueprint` 补全 → `interpret` 人话解读；复用走 `replicate` 铺排（人拍板）→ `replicate_apply` 落地骨架。`registry` canonical 是 L2 目标架构/duplicate 差距与 L3 资产的共同命名基准。

## 常见陷阱

- **不要用 incr 审存量**：治理差距是存量结构，不在本次 diff 内；`strategy=incr` 只用于回归复核新增改动。
- **不要把 flow_verify 的 outside 当失败**：它是诚实状态，可能印证双入口真实形态（如 run_bot 直连引擎不经 queue）。
- **不要只靠可达性判真身**：双孪生入口 flow_verify 结果同构，必须叠加 fan_in（who-calls-me）判定。
- **不要动 git 库/备份**：治理动作前确认目标不在 git 库范围内（项目硬约束）。
- **所有工具都要传 `project_path`**：必填参数，指向被测项目路径。
