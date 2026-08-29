---
name: coderef-asset
description: L3 资产沉淀编排 Skill。把治理/开发产出的高价值设计沉淀成可复用资产，串成「识别→登记→固化→解读→复用」资产链，让治理成果不随项目迁移而流失。当用户要「把某设计沉淀成可复用资产 / 创新识别与确认 / 设计登记与归一 / 把资产复刻到新项目 / 人话解读资产 / 资产体检」时使用。核心逻辑：沉淀有门槛（≥2 workflow 采用 + evidence，防污染）、命名先归一（registry alias→canonical）、复用不自动改代码（replicate 只铺排、replicate_apply 只落地骨架+说明）。零新工具，纯文档编排；与 coderef-governance（L2 大阶段）/ coderef-probe（L1 小阶段）平级衔接，L2 治理成果与 L1 高采用率设计是沉淀上游。
---

# CodeRef 资产链 · L3 资产沉淀编排

> 承接《治理体系定位与编排层设计研究_20260829.md》§3.4（L3 资产沉淀层）与 §六 P3（L3 资产沉淀编排评估）。
> 定位：**治理/开发产物的沉淀复用**——把「值得复用的设计」从具体项目里提炼成资产，登记命名、固化证据、补全蓝图，再铺排复刻到新项目。与 `coderef-governance`（L2 大阶段治理）、`coderef-probe`（L1 小阶段探查）平级：L1/L2 产出治理成果 → L3 沉淀；L3 复刻落地新项目 → 回 L1/L2 验证维持。

## 核心原则（必须遵守）

1. **沉淀有门槛，防污染**：`asset` commit 需 ≥2 workflow 采用 + evidence（调用证据/契约一致）；一次性设计不固化。宁缺毋滥——资产库污染比没有资产更糟。
2. **先识别后固化**：创新/高价值设计先过 `innovation_review` 排查（真创新？管线与 wiki 描述一致？复刻是否合理），再登记固化。LLM 排查结论是 AI 意见而非确定性事实，不下「必须复刻」指令。
3. **命名先归一**：任何 `add`/`commit` 前，先用 `registry` 把别名归一到 canonical——命名漂移会产生重复资产、稀释采用率门槛判定。`alias` 归一解 LLM 命名漂移。
4. **复用不自动改代码**：`replicate` 是审计工具（只报告缺口，未采用≠该采用）；`replicate_apply` 只落地「确定性可给」的骨架与说明到 `coderef-replicate-apply` 目录 + 生成 manifest，不自动接入目标源码；默认不覆盖已存在文件（冲突如实标注，overwrite=true 才允许覆盖）。
5. **护栏**：沉淀/复刻动作不动被测项目的 git 库与备份；测试不写开发侧文件夹、编程 AI 不写测试侧文件夹。
6. **闭环判定**：资产可用性 = `registry` canonical 唯一（无别名漂移）+ `asset` 有 ≥2 采用 evidence + 蓝图 `entry_points`/`verified_findings` 非空 + `interpret` 人话可读；复刻成功 = `replicate` 缺口收敛 + `replicate_apply` 落地 manifest 生成 + 目标项目回 L1 探查无新增回归。

## 资产链 · 流程编排

```
识别 → 确认 → 登记 → 固化 → 补全 → 解读    （沉淀链：治理成果 → 资产）
铺排 → 核验 → 落地                          （复用链：资产 → 新项目）
```

### 沉淀链 · 治理成果 → 资产

| 步骤 | 工具 | 说明 |
|---|---|---|
| 1. 识别 | `innovation` | 识别项目创新设计 + 传播缺口，按意图分组输出 candidates（哪些设计可能值得沉淀） |
| 2. 确认 | `innovation_review` | LLM 排查三点（真创新？管线与 wiki 一致？复刻合理性）；确定性管线摘要照常给出，LLM 结论为 AI 意见 |
| 3. 登记 | `registry` | `add` 登记 canonical 设计名；既有别名先 `alias` 归一（防重复资产） |
| 4. 固化 | `asset`(action=commit) | ≥2 workflow 采用 + evidence 才固化；可传 `blueprint` 或缺省自动从已验证 adopters 构建骨架 |
| 5. 补全 | `asset_blueprint` | 把铺排核验出的确定性 `entry_points`/`verified_findings` 写回蓝图，骨架 → 可复刻蓝图 |
| 6. 解读 | `interpret`(action=assets) | 人话解读已固化资产，非编程人员可懂（沉淀的对外可读面） |

### 复用链 · 资产 → 新项目

| 步骤 | 工具 | 说明 |
|---|---|---|
| 1. 铺排 | `replicate` | 检测目标项目对该资产的采用缺口 + 生成复刻指引（gap_report/steps/entry_points/verified_findings）；只报告不自动改 |
| 2. 核验 | `replicate`(verify_symbols) / `verify_findings` | 对蓝图入口做确定性核验（符号真实存在、在关键管线内）后再决策 |
| 3. 落地 | `replicate_apply` | 把 `template_code` 骨架 + `patch_suggestion`/`migration_guide` 说明写入目标项目 `coderef-replicate-apply` 目录 + manifest；冲突默认标注不覆盖 |

**决策边界**：铺排是审计输出，「未采用」不等于「该采用」——是否复刻、复刻哪些由人拍板，AI 只做确定性铺排与机械落地。

## 典型场景编排

### 场景 A · 治理成果沉淀（L2 治理后的资产化收尾）

1. `coderef_innovation`(project_path=源项目) → 识别创新设计 + 传播缺口，取 candidates。
2. `coderef_innovation_review`(canonical=候选) → LLM 排查真创新/一致性/复刻合理性；无 API Key 时只取确定性管线摘要、不产降级判断。
3. `coderef_registry`(action=alias/add) → 先 alias 归一、再 add 登记 canonical（命名基准落定）。
4. `coderef_asset`(action=commit, canonical=...) → ≥2 采用 + evidence 固化；缺省自动构建骨架。
5. `coderef_asset_blueprint`(canonical=...) → 补全 entry_points/verified_findings。
6. `coderef_interpret`(action=assets) → 人话解读沉淀结果。

**触发**：L2 治理 ③ 抽出公共工具/⑤ 体检发现高价值设计后；或 L1 探查发现被多 workflow 采用的设计。

### 场景 B · 资产复刻新项目（资产 → 新项目）

1. `coderef_registry`(action=list) → 确认资产 canonical 与别名现状。
2. `coderef_replicate`(project_path=目标项目, canonical=...) → 缺口报告 + 复刻指引（确定性）。
3. 人拍板「哪些缺口采用」→ 传 `verify_symbols=true` 对蓝图入口确定性核验。
4. `coderef_replicate_apply`(canonical=..., target=目标项目) → 落地骨架 + 说明 + manifest；冲突默认不覆盖。
5. 回 L1/L2：目标项目 `coderef_flow_verify`/`coderef_arch_verify` 验证落地无回归。

### 场景 C · 资产体检（周期维护）

`coderef_registry`(action=list) → 查命名健康（别名是否漂移、canonical 是否唯一）；`coderef_asset`(action=list) → 查资产清单与采用数（低于门槛的标记待清理/待补证）；漂移别名 → `coderef_registry`(action=alias) 归一。产出资产健康面，供 L2 ⑤ health-cycle 跨期引用。

## 与 L1/L2 的衔接

- **上游（→L3 沉淀）**：L2 治理 ③ 抽出可复用公共工具、⑤ 体检发现高价值设计 → 走 `coderef-asset` 沉淀链；L1 探查发现被多 workflow 采用的设计（传播缺口收敛）→ 沉淀候选。
- **下游（L3 复用 →）**：复刻落地目标项目后 → 回 L1 持续探查防再腐化 + L2 `flow_verify`/`arch_verify` 验证链路与对齐度。
- **命名协同**：`registry` canonical 是 L2 `target_arch_set`/`duplicate` 差距与 L3 资产的共同命名基准，别名统一经 registry 归一。

## 常见陷阱

- **不要固化一次性设计**：≥2 采用 + evidence 是硬门槛，单点设计进资产库只会稀释库质量。
- **不要把"未采用"当"该采用"**：`replicate` 只报缺口，复刻决策必须人拍板。
- **不要用 `replicate_apply` 自动改源码**：它只落地骨架+说明到 `coderef-replicate-apply` 目录，不自动接入目标源码；需要覆盖必须显式 `overwrite=true`。
- **不要跳命名归一**：先 `registry` alias→canonical 再 commit，否则命名漂移产生重复资产、污染采用率门槛。
- **不要动 git 库/备份**：沉淀/复刻动作前确认目标不在 git 库范围内（项目硬约束）。
- **所有工具都要传 `project_path`**：必填参数，指向被测/源项目路径。
