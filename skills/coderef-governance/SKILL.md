---
name: coderef-governance
description: 治理主链场景化 Skill（外部建议 E 物化）。把 CodeRef-AI 的 57 个 MCP 工具收敛为「5 阶段 × 每阶段 2–4 个高频工具」的少而精工具链，让编程 AI 不必直面全部工具也能沿主链把屎山捋顺。当用户要做「架构治理 / 存量屎山收敛 / 重复与孪生治理 / 定期体检 / 治理工作项流转」时使用。核心逻辑：人工先捋对管线（map→target），编程 AI 照清单治理（refactor→verify），周期体检维持（health）。
---

# CodeRef 治理主链 · 少而精工具链

> 承接《建议书_治理主链与工具改造_20260826.md》§五「少而精工具链」与 §七 外部建议 E（场景化 Skill 封装层，P0 采纳）。
> 目标：把 57 个 MCP 工具收敛成 **5 阶段 × 每阶段 2–4 个高频工具**，其余工具按需按名调用即可。

## 核心原则（必须遵守）

1. **先捋管线，再谈治理**：治理第一步永远是「用真实业务入口理清它当下实际怎么走」（哪是真身、哪是死线、哪在重复），产出人可确认的管线清单。管线确认后，目标架构与差距清单才可靠；否则游离/缺失全是虚数。
2. **L0→L3 逐层捋清铁律（）**：捋管线必须按自顶向下顺序走完四层——**L0 架构总览**（这是什么业务/商店还是饭店/客人是谁）→ **L1 模块盘点**（厨房/卫生间/前台/大厅有哪些模块，按层聚焦）→ **L2 模块内逻辑**（点进"业务工具"看内部 N 步流程）→ **L3 代码管线**（从模块穿透到具体代码流转/调用链）。**未捋清 L0-L3 不得进入定标（define-target）与差距分析（arch_gap）**；画布默认全量平铺是"并列展示"，必须先经层级导航（L0/L1/L2/L3 视图 + 面包屑回退）逐层捋清后，才叠加差距高亮（差距开关默认折叠）。
3. **结构性锈蚀 ≠ 回归复核**：`strategy=incr` 只用于「回归复核新增改动」；治理存量结构（重复/孪生/真身）必须走 `strategy=full` + 结构锈蚀扫描，否则双真身/重复库会被完全漏掉。
4. **真身判定看 fan_in，不看可达性**：双孪生入口的 flow_verify 结果可能完全同构（孤本也调 shared 层）。判定活跃真身 vs 无调用者孤本，唯一可靠信号是 `architecture` P0-B 的 fan_in（who-calls-me）。
5. **治理动作护栏**：任一治理动作前，确认目标不在 git 库/备份范围内（项目硬约束）；测试不写开发侧文件夹、编程 AI 不写测试侧文件夹，双册对账。
6. **闭环判定**：治理成功的量化信号 = `alignment.module_assigned` 上升 + gap 总数（尤其 unassigned/duplicate）下降 + `flow_verify` 期望链路 `outside` 收敛为 `ordered/in_pipeline`。

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
| 治理工作项怎么流转 / 豁免 | `coderef_gov_transition` | 参数速查见下 |
| 定期体检 / 建档 / 闭环 | `coderef_gov_start` / `coderef_gov_close` | + `coderef_gov_board` / `coderef_gov_report` |
| AI 改完代码提交前确认没改坏 | `coderef_change_guard` | + `coderef_change_report` |
| 查调用关系 / 影响面 | `coderef_query` | 替代 grep，省 token |
| 安全合规（OWASP） | `coderef_owasp` | + `coderef_prompt_governance` |
| 上下文丢了，东西在哪儿 | `coderef_operation_memory_recover` | 强制 gate（见 coderef-mcp 工作流 E） |

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

**工具（2 个）**：`coderef_target_arch_set`、`coderef_arch_gap`

**编排**：
1. `coderef_target_arch_set` → 落 target_arch.json（business_flows/constraints 顶层字段必须完整，否则 arch_verify/arch_gap 基于残缺架构假达标）。
2. `coderef_arch_gap` → 差距清单（含 duplicate/directory_duplicate 差距类型；游离区分真游离 vs 未建模）。

**产出**：目标架构 vN + 差距清单（优先级队列）。**常见坑**：target_modules 需覆盖全部核心模块，否则游离清单失真（未建模被误报为真游离）。

### 场景 ③ refactor-along · 照管线治理（编程 AI）

**目标**：按②的差距清单逐项治理，每动一处可验证、可回滚。

**工具（2 个）**：`coderef_gov_issues`（取队列）、`coderef_arch_gap`（治理后复查）

**编排**：
1. `coderef_gov_issues`(view=high) → 取去噪后的工作项队列（已按真实 severity 排序、unassigned 置底）。
2. 按清单逐项治理（如收敛双真身到活跃真身），每项登记到治理库。
3. 治理后 `coderef_arch_gap` 复查 gap 总数下降。

**护栏**：任一治理动作前，确认目标不在 git 库/备份范围内；绝不动 git 库与备份。

### 场景 ④ verify-advance · 验通推进（编程 AI 自证 + 测试复核）

**目标**：流程合规验证 + 治理状态流转。

**工具（3 个）**：`coderef_flow_verify`、`coderef_gov_transition`、`coderef_gov_report`

**编排**：
1. `coderef_flow_verify`(entry=入口, steps=[期望链路]) → 状态分 ordered/in_pipeline/outside/missing；`outside` 是诚实状态不是失败，如实转述。
2. `coderef_gov_transition` → 沿状态机推进（Detected→Confirmed→Fixing→Verified→Archived）。
3. `coderef_gov_report` → 单期报告 + 跨期趋势。

**gov_transition 参数速查（P2⑦）**：
- `transition`：必填 issue_id + action='transition' + to_state（如 Confirmed→Fixing 传 to_state='Fixing'）
- `reject`：必填 issue_id + action='reject' + reason（豁免理由必留，缺省会报错）
- `meta`：必填 issue_id + action='meta' + 至少一项（priority/assignee/due_date/note）；**勿传 to_state**（否则报「需 provide to_state」）
- 状态机强约束：仅 Confirmed→Fixing 可进 Fixing；仅 Verified 后可归档。非法跳转返回明确错误属正常，先 transition 到合法中间态。

### 场景 ⑤ health-cycle · 周期体检（人工/测试定期触发）

**目标**：定期建档体检、跨期趋势、闭环归档。

**工具（4 个）**：`coderef_gov_start`、`coderef_gov_issues`、`coderef_gov_board`/`coderef_gov_report`、`coderef_gov_close`

**编排**：
1. `coderef_gov_start` → 建档 open 周期 + 差距全量导入（去重/复发/豁免生效）。
2. `coderef_gov_issues`(view=high) → 看真实治理重点（已降噪）。
3. `coderef_gov_board` → 交互看板（自动落盘 <project>/.coderef/gov_board.html）；`coderef_gov_report` → 单期报告。
4. `coderef_gov_close` → 收尾闭环（cid 缺省时自动定位当前 open 周期）。

**产出**：体检周期 + 跨期趋势报告。**定时化**：`coderef_gov_schedule` 生成 run_cycle.py 可纳入 cron/CI。

## 常见陷阱

- **不要用 incr 审存量**：治理差距是存量结构，不在本次 diff 内；`strategy=incr` 只用于回归复核新增改动。
- **不要把 flow_verify 的 outside 当失败**：它是诚实状态，可能印证双入口真实形态（如 run_bot 直连引擎不经 queue）。
- **不要只靠可达性判真身**：双孪生入口 flow_verify 结果同构，必须叠加 fan_in（who-calls-me）判定。
- **不要动 git 库/备份**：治理动作前确认目标不在 git 库范围内（项目硬约束）。
- **所有工具都要传 `project_path`**：必填参数，指向被测项目路径。
