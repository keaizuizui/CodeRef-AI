---
name: coderef-governance
description: 治理主链场景化 Skill。把 CodeRef-AI 的 MCP 工具收敛为「5 阶段 × 每阶段 2–5 个高频工具」的少而精工具链，让编程 AI 不必直面全部工具也能沿主链把存量工程捋顺。当用户要做「架构治理 / 存量结构收敛 / 重复与孪生治理 / 定期体检 / 治理工作项流转」时使用。核心逻辑：人工先捋对管线（map→target），编程 AI 照清单治理（refactor→verify），周期体检维持（health）。L2 大阶段治理编排（本 SKILL）+ L1 小阶段治理编排（`coderef-probe` SKILL，变更驱动探查/防护）+ L3 资产沉淀编排（`coderef-asset` SKILL，治理成果→资产）互补衔接，并按「编排 gate」条件触发强制跨层转场：改码前转 probe（L1），产出可复用设计转 asset（L3）。
---

# CodeRef 治理主链 · 少而精工具链（L2 大阶段治理编排）

> 目标：把 MCP 工具收敛成 **5 阶段 × 每阶段 2–5 个高频工具**，其余工具按需按名调用即可。
> L2 编排定位：周期驱动的存量工程系统性规整。整改环节通过「游离一键纳入 + gov_pipeline 半自动整改闭环 + 人拍板确认」完成，但架构方向决策仍由人拍板（工具只做机械性归属动作）。
> L3 衔接：治理成果——③ 抽出的可复用公共工具、⑤ 体检发现的高价值设计（多 workflow 采用）→ 沉淀走 `coderef-asset` SKILL（资产沉淀编排），避免成果随项目迁移流失。

## 核心原则（必须遵守）

1. **先捋管线，再谈治理**：治理第一步永远是「用真实业务入口理清它当下实际怎么走」（哪是真身、哪是死线、哪在重复），产出人可确认的管线清单。管线确认后，目标架构与差距清单才可靠；否则游离/缺失全是虚数。
2. **L0→L3 逐层捋清铁律（）**：捋管线必须按自顶向下顺序走完四层——**L0 架构总览**（这是什么业务/商店还是饭店/客人是谁）→ **L1 模块盘点**（厨房/卫生间/前台/大厅有哪些模块，按层聚焦）→ **L2 模块内逻辑**（点进"业务工具"看内部 N 步流程）→ **L3 代码管线**（从模块穿透到具体代码流转/调用链）。**未捋清 L0-L3 不得进入定标（define-target）与差距分析（arch_gap）**；画布默认全量平铺是"并列展示"，必须先经层级导航（L0/L1/L2/L3 视图 + 面包屑回退）逐层捋清后，才叠加差距高亮（差距开关默认折叠）。
3. **结构性锈蚀 ≠ 回归复核**：`strategy=incr` 只用于「回归复核新增改动」；治理存量结构（重复/孪生/真身）必须走 `strategy=full` + 结构锈蚀扫描，否则双真身/重复库会被完全漏掉。
4. **真身判定看 fan_in，不看可达性**：双孪生入口的 flow_verify 结果可能完全同构（孤本也调 shared 层）。判定活跃真身 vs 无调用者孤本，唯一可靠信号是 `architecture` P0-B 的 fan_in（who-calls-me）。
5. **治理动作护栏**：任一治理动作前，确认目标不在 git 库/备份范围内（项目硬约束）；测试不写开发侧文件夹、编程 AI 不写测试侧文件夹。
6. **闭环判定**：治理成功的量化信号 = `alignment.module_assigned` 上升 + gap 总数（尤其 unassigned/duplicate）下降 + `flow_verify` 期望链路 `outside` 收敛为 `ordered/in_pipeline`；大阶段收尾再叠加 `coderef_arch_verify` 对齐度四维（职责对齐/依赖健康/业务覆盖/代码健康）确认达标。

## ⑨ 软入口档 · 先架构诊断（屎山摸底，选项，不作强制）

> 适用：**真·屎山 / 存量工程混乱，还拿不准值不值得上全链治理**。别一进来就硬上地图→定标→重构五阶段——先做「架构摸底」确认治理价值，价值成立再升级正式主链，否则停在摸底（只立案不硬上，防撞墙）。**这是 L2 的低门槛入口，也是「屎山先做架构」的落点**：摸底本身就等价于主链 ①map（架构梳理），只多一道「治理价值决策门」。

**入口**：从 `coderef-mcp` 顶层入口判定「存量混乱但不确定是否值得治理」路由进本档。

**摸底编排（4 步 = 正式主链 ①map 的同一资产，一次执行，不重复劳动）**：
> 摸底这张 `steps 1-4` = 主链 ①map 的 `steps 1-3` + 一道 gap 诊断读。**两者是同一次执行**：若已走本档摸底并决策升级，正式主链 **①map 不再重跑**，直接从 ② define-target 复用摸底产出进入。

1. `coderef_architecture`（读 P0-B 真身/孤本 fan_in、P0-C 孪生簇/目录级重复）→ 先把屎山**画清楚**：哪是真身、哪是死线、哪在重复（这一步就是先做架构）。
2. `coderef_arch_audit` → 健康度 0-10 + cycle/god_module/large_module 存量结构症状，评估腐化面大小。
3. `coderef_flow_canvas`（按 L0→L3 逐层下钻）→ 摸清业务管线真实形状与断点。
4. `coderef_arch_gap`（本次仅作诊断读，不落 target）→ 看差距分布：unassigned/duplicate/directory_duplicate 到底有多少、集中在哪。

**决策门（人拍板，评估治理价值）**：
- **价值成立**（重复/孪生真实存在、模块游离集中、腐化可控）→ **升级正式主链**，从 ② define-target 进入；**①map 跳过复用摸底产出**（摸底即 map，避免重复劳动）。
- **价值不足**（差距稀疏、多为豁免噪声、重构面超出当期治理容量）→ **停在摸底**，把诊断结论归档到 `coderef_gov_report`(action=report)，不强行推进重构；等其价值显现或进入 ⑤ health 周期体检维持可见。
- 摸底阶段发现的**高价值设计**（意外发现真身值得抽共享）→ 仍触发 G2 转 `coderef-asset`（L3）评估沉淀，以免摸底空转浪费治理洞察。

**降级护栏**：软入口档只做「读 + 诊断 + 立案」，**绝不动生产代码**；一旦确定要改码治理，必须回正式主链走 ③ 的 gate G1。

## 意图 → 工具快速路由（轻量版）

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
**复用**：若已走 ⑨ 软入口档「先架构诊断」摸底且决策升级，**①map 不再重跑**，直接复用摸底产出进入 ② define-target（摸底即 map，避免重复劳动）。

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
4. **gate G1（动手改码前，强制）**：本阶段若涉及**修改/删除存在生产代码**（真身收敛、安全修复、模块重组、共享逻辑抽取），必须先转 `coderef-probe`（L1 变更驱动探查/防护）承接本次变更——改动被快速探查、回归被拦截、结论被确定性核验；探查无存量新问题时再回到本步继续。机械性归属（只动 target_modules 配置）不触发。
5. 机械性归属 → `coderef_gov_pipeline`(issue_ids=[...]) → 自动走 Fixing→任务卡→复验→Verified 半自动闭环；架构方向决策由人拍板后执行（工具只做机械性归属动作，绝不自动改架构方向）。
6. 治理后 `coderef_arch_gap` 复查 gap 总数下降。

**护栏**：任一治理动作前，确认目标不在 git 库/备份范围内；绝不动 git 库与备份。
**收尾 gate G2**：本阶段若治理产出了**可复用公共工具**（如收敛出的真身、抽出的共享逻辑、归一化骨架），在进入下一期前，先转 `coderef-asset`（L3 资产沉淀）评估「是否值得沉淀为复用资产」；评估为否就停，为是则走沉淀链（见「编排 gate」）。

### 场景 ④ verify-advance · 验通推进（编程 AI 自证 + 测试复核）

**目标**：流程合规验证 + 对齐度后验 + 治理状态流转。

**工具（4 个）**：`coderef_flow_verify`、`coderef_arch_verify`、`coderef_gov_transition`、`coderef_gov_report`（action=report）

**编排**：
1. `coderef_flow_verify`(entry=入口, steps=[期望链路]) → 状态分 ordered/in_pipeline/outside/missing；`outside` 是诚实状态不是失败，如实转述。
2. `coderef_arch_verify` → 0-100 对齐度后验（职责对齐 40% + 依赖健康 30% + 业务覆盖 20% + 代码健康 10%）+ 差距复检清单；增量模式传 `changed_files` 可对单张任务卡快速反馈是否达标（③ gov_pipeline 复验也复用它）。
3. `coderef_gov_transition` → 沿状态机推进（Detected→Confirmed→Fixing→Verified→Archived）。
4. `coderef_gov_report`(action=report) → 单期报告 + 跨期趋势。

**gov_transition 参数速查**：
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

**产出**：体检周期 + 跨期趋势报告。**定时化**：`coderef_gov_schedule` 生成 run_cycle.py 可纳入 cron/CI。**gate G2**：本次体检若发现**高价值设计**（多 workflow 采用、值得复用），关期前转 `coderef-asset`（L3）评估沉淀（见「编排 gate」）。

## 编排 gate（条件触发式强制转场）

> 主链不是单 skill 闭环：跨 L1/L2/L3 的转场是**条件触发式强制 gate**——当满足触发条件时**必须**转场到对应 skill，未命中就留在主链继续。这是为了把存量治理连上「变更防护（L1）」与「成果沉淀（L3）」，避免编程 AI 停在 L2 一条链走完（外部实测：只显式调 governance，probe/asset 都没触发）。**命中即强制接流程，不要只在心里想到、却让对应 skill 空转。**

| gate | 位置 | 触发条件（命中即必须转场） | 转去 | 承接内容 |
|---|---|---|---|---|
| **G1 → L1 probe** | 场景 ③ 动手改码前 | 治理动作涉及**修改/删除存在生产代码**（真身收敛、安全修复、模块重组、共享逻辑抽取） | `coderef-probe`（L1 变更驱动探查/防护） | 探测每次变更：探查→策略路由→增量探查→确定性核验→变更防护→降噪；改动被快速探查、回归被拦截、结论被确定性核验 |
| **G2 → L3 asset** | 治理产出后（③ 收尾 / ⑤ 体检后） | 抽出**可复用公共工具** / 体检发现**高价值设计**（多 workflow 采用） | `coderef-asset`（L3 资产沉淀） | 成果沉淀链（见下）：是否可沉淀评估 → registry 归一 → asset commit 固化 |
| **G3 → 回主链**（闭环） | L1/L3 执行后 | L1 探查发现**存量结构问题**（重复/孪生/游离）；L3 复刻落地后 | 回到本 skill（L2） | 存量问题 align 到 ② 差距清单立项治理；资产复刻落地后回 L1 probe 验证，形成 L1↔L2↔L3 闭环 |

**使用规则**：
- **命中触发条件必须转场**，加载对应 skill，让它的实际流程接管该环节；做回主链时继续 ③④⑤ 剩余步骤，不重置主链进度。
- **未命中就留在主链**，不强制跳转——简单治理不被反复切 skill 拖累（条件触发而非每次必经）。
- G2 成果沉淀链：`innovation` 识别 → `innovation_review` 确认 → `registry` 登记归一（alias→canonical）→ `asset`(commit，≥2 采用 + evidence) 固化 → `asset_blueprint` 补全 → `interpret` 人话解读；复用走 `replicate` 铺排（人拍板）→ `replicate_apply` 落地骨架。`registry` canonical 是 L2 目标架构/duplicate 差距与 L3 资产的共同命名基准。

## 常见陷阱

- **不要用 incr 审存量**：治理差距是存量结构，不在本次 diff 内；`strategy=incr` 只用于回归复核新增改动。
- **不要把 flow_verify 的 outside 当失败**：它是诚实状态，可能印证双入口真实形态（如 run_bot 直连引擎不经 queue）。
- **不要只靠可达性判真身**：双孪生入口 flow_verify 结果同构，必须叠加 fan_in（who-calls-me）判定。
- **不要动 git 库/备份**：治理动作前确认目标不在 git 库范围内（项目硬约束）。
- **所有工具都要传 `project_path`**：必填参数，指向被测项目路径。
