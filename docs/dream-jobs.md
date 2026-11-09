# CodeRef 梦任务与工具面设计 v0.1

> 定位：`docs/dream.md` 回答「梦是什么」；本文回答「梦还能接哪些活、Skill 怎么配合、MCP 对外暴露多少」。
> 状态：**已实现（5.21.1）**。Skill 梦协议、job 契约 `pitfall/recap/routing/triage/change_narrative/failure_postmortem`、P2 草稿 `review_draft/refactor_draft/asset_candidate/noise_proposal`（`drafts/` + 用户确认）、`claim_type`、`DREAM.md` v2 六节、triage digest 优先、`CODEREF_TOOL_PROFILE=lite|full` 均已落地。
> 当前基线 5.21.1：59 个 MCP 工具，梦复用主 LLM 配置。

---

## 0. 结论摘要

1. **梦是离线归约器，不是第二个 agent。** 它不持有工具、不执行审计、不改代码/配置；只读少量显著痕迹，产出带 `evidence` 的假设，本地核验后写笔记或草稿。
2. **可外包的是「记忆、注意力、草稿」；不可外包的是「确定性结论、写操作、安全判定」。**
3. **Skill 不新增，只加“梦协议”。** 在 `coderef-mcp` 写一份权威协议，L1/L2/L3 各挂两个钩子（醒来看梦、关键节点 seed）。
4. **MCP 工具不删，加 profile。** `CODEREF_TOOL_PROFILE=lite|full` 控制 `tools/list` 暴露面；`lite` 约 12 个入口，`full` 仍是 59 个，handler 全部保留。
5. **先做 P0/P1，再做 P2 草稿。** 草稿类必须标 `hypothesis` + 用户确认，不能自动应用。

---

## 1. 背景与现状

- 5.20.x 已有：`.coderef/dream/`、`DREAM.md`、`notes.json`、`runs/`、`coderef_dream(status|seed|read|run)`、启动补梦（`run_on_startup` 默认关闭）、`memory_digest` 梦优先、`remind` 梦段。
- 梦的 LLM 已统一复用主 LLM（`LLMIntegration`，与 Wiki/报告/eval 同源），不再有独立 Key/预算。
- 当前 `run` 只做一件事：把被阻断、失败、变更守卫退化与显式 seed 巩固成笔记。
- 用户诉求：把一部分“编排之外”的工作外包给梦；同时重新审视 Skill 与 MCP 工具面。

---

## 2. 边界：梦做什么 / 不做什么

### 2.1 做

| 类型 | 说明 | 允许写入 |
|---|---|---|
| 记忆归约 | 把跨会话经历压成更少、更准的规则/坑/联想 | `notes.json`、`DREAM.md` |
| 注意力排序 | 开工前指出“先看什么”，只排序不下结论 | `DREAM.md` 的「本次开工建议」 |
| 草稿提议 | review 清单、refactor 草稿、资产候选、降噪建议 | `drafts/`，不直接改治理库 |
| 自检 | 旧梦对照新证据，标 `rejected` / `decayed` | `notes.json`、`archive/` |

### 2.2 不做

- 不执行任何 MCP 工具；梦没有工具调用循环。
- 不产出 audit / scan / owasp / change_guard / flow_verify / arch_gap / arch_verify / verify_findings / query 的替代结论。
- 不改代码、不改 scope/whitelist/target_arch/registry、不做 git 操作。
- 不把 `hypothesis` 写进 `DREAM.md` 的「已验证规则」。
- 不读取主 AI 全量对话；只读 CodeRef 自身痕迹与显式 `seed`。
- 不跨项目共享梦；不做常驻 daemon。

---

## 3. 任务模型（Dream Job Contract）

### 3.1 接口演进

当前：

```text
coderef_dream(action=status|seed|read|run)
```

建议 5.21 起：

```text
coderef_dream(action=run, job=recap|pitfall|routing|triage|change_narrative|failure_postmortem|review_draft|refactor_draft|asset_candidate|noise_proposal)
coderef_dream(action=read, job=..., kind=..., keyword=...)
coderef_dream(action=drafts, job=..., limit=...)
```

- `job` 缺省为 `pitfall`（保持 5.20 行为）。
- 每个 job 一次 LLM 调用，不做多轮 agent 循环。
- 所有 job 共用：原料筛选、脱敏、证据核验、`runs/` 落盘、TTL/去重。
- 不同 job 只换：输入白名单、提示词、输出 schema、写入目标。

### 3.2 通用输出信封

```json
{
  "job": "triage",
  "status": "ok | SKIP | error",
  "generated_at": "2026-11-09T18:00:00",
  "inputs": {"audit_events": 3, "dispatch_events": 1, "seeds": 2},
  "items": [
    {
      "claim_type": "fact | advice",
      "kind": "rule | pitfall | convention | association | context",
      "text": "一句话",
      "evidence": ["file:README.md", "dispatch:abc123"],
      "confidence": 0.8,
      "status": "verified | hypothesis | rejected",
      "job": "triage",
      "ttl_days": 14
    }
  ],
  "open_questions": ["..."],
  "limits": {"max_prompt_chars": 12000, "max_output_tokens": 2048, "estimated_tokens": 0}
}
```

### 3.3 核验规则（不变）

- `evidence` 只允许：`file:相对路径` / `git:短SHA` / `audit:工具名` / `seed:种子ID` / `dispatch:任务ID`。
- 任意引用核验失败 → `rejected`。
- 无 evidence 或无法本地核验 → `hypothesis`。
- `claim_type=advice` 永远不得标 `verified`（建议不是事实）。
- `claim_type=fact` 且引用全部可核验 → 可标 `verified`，但仍不是审计结论。

### 3.4 写入路由

| job 类型 | 写入位置 | 是否更新 `DREAM.md` |
|---|---|---|
| 记忆类：`pitfall` / `routing` / `recap` | `notes.json` | 是 |
| 注意力类：`triage` | `notes.json` + 当日 `triage` 缓存 | 是（新增「本次开工建议」） |
| 叙事类：`change_narrative` / `failure_postmortem` | `notes.json` | 视内容而定 |
| 草稿类：`review_draft` / `refactor_draft` / `asset_candidate` / `noise_proposal` | `drafts/<job>-<ts>.json` | 否 |
| 自检类：`false_memory_check` | `notes.json` / `archive/` | 是（移除/降级） |

---

## 4. 任务目录

### P0：记忆与续接（优先做）

| job | 触发 | 输入 | 产出 | 核验 | 风险 |
|---|---|---|---|---|---|
| `recap` | 开工前手动 / 启动补梦开启时 | 上次梦、近 7 天 blocked、失败调度、git log、未消费 seed | 当前目标、上次停在哪、3 条未决问题、1–3 条开工建议 | 事实用 `dispatch:` / `git:` / `file:`；建议标 hypothesis | 低 |
| `pitfall` | 已有 | blocked、失败调度、change_guard 退化、seed | 坑 / 规则 | 现有规则 | 低 |
| `routing` | 手动 / 启动 | dispatch 轨迹、工具成功/失败、seed | 「当 X，先用 Y」的联想 | `dispatch:` / `audit:`；advice 永远 hypothesis | 低 |

### P1：注意力与叙事

| job | 触发 | 输入 | 产出 | 核验 | 风险 |
|---|---|---|---|---|---|
| `triage` | 开工前 / `remind` | 过期产物、高危 finding、失败任务、图谱 stale、未决问题 | 最多 3 条「先看什么」，每条带证据 | `file:` / `audit:` / `dispatch:`；advice 不升级 | 中（排序主观） |
| `change_narrative` | 手动 / 变更后 | git log、audit delta、dispatch 结果 | 3 句话「上次改了什么、结果如何」 | `git:` / `audit:` / `dispatch:` | 低 |
| `failure_postmortem` | 手动 / dispatcher 失败后 | 失败轨迹、闸门记录、相关 seed | 根因假设 + 下一步建议 | `dispatch:` / `audit:`；只进未决问题/坑的假设位 | 中（根因可能错） |

### P2：草稿（必须用户确认）

| job | 输入 | 产出 | 后续确定性闸门 |
|---|---|---|---|
| `review_draft` | 变更文件清单、最近 audit findings | 检查清单/问题草稿 | `coderef_review` / `coderef_verify_findings` |
| `refactor_draft` | `arch_gap` / `refactor_plan` / 治理库 | 步骤草稿与风险 | `coderef_arch_verify` / `coderef_target_adopt` |
| `asset_candidate` | 治理库、创新识别结果 | 可复用设计候选 | `coderef_asset` / `coderef_registry` 门槛 |
| `noise_proposal` | 反复出现的误报、白名单/scope 历史 | scope/whitelist 建议 | `coderef_scope` / `coderef_whitelist` + 用户确认 |

草稿类只写 `drafts/`，不写 `notes.json`；`read` 时默认不返回草稿，需显式 `action=drafts`。

### P3：可选，不默认跑

- `false_memory_check`：旧梦对照新证据，`rejected`/`decayed`。大部分可确定性完成，LLM 只用于判断语义冲突。
- `eval_replay`：对历史报告做 LLM-as-judge 抽样评估。成本高，只在用户明确要求时跑。

---

## 5. 存储与 `DREAM.md` 演进

### 5.1 目录

```text
.coderef/dream/
  state.json
  DREAM.md
  seeds.jsonl
  notes.json
  drafts/            # 新增：草稿类 job，不进入记忆
    review_draft-20261109-180000.json
    refactor_draft-20261109-180500.json
  runs/
  archive/
```

### 5.2 `notes.json` v2

在现有字段上增加：

```json
{
  "job": "routing",
  "claim_type": "advice",
  "source_run": "runs/20261109-180000-abcd.json",
  "expires_at": "2026-11-23T18:00:00"
}
```

- `kind` 扩展一个 `context`，用于「当前目标 / 上次停在哪」。
- `claim_type=advice` 永远 `hypothesis`；`kind=context` 的事实可 `verified`。
- 旧笔记无需迁移；缺字段按 `claim_type=fact`、`job=pitfall` 兼容。

### 5.3 `DREAM.md` v2

```markdown
# Dream
<!-- 梦是假设：引用前先核验 evidence，不得当作审计结论 -->

## 当前目标
## 本次开工建议
## 已验证规则
## 坑
## 联想（当 X，先想到 Y）
## 未决问题
```

- 「本次开工建议」只放 `advice`，最多 3 条，每条带证据。
- 「坑」可放 verified pitfall；未验证的失败假设放「未决问题」，标「假设」。
- 「联想」可放 verified association 与 hypothesis routing，后者标「假设」。
- 总预算仍 ≤800 token；新增 section 后优先压缩低置信条目。

### 5.4 `runs/` v2

每次 job 记录：

```json
{
  "job": "triage",
  "trigger": "manual",
  "status": "ok",
  "inputs": {"audit_events": 3, "dispatch_events": 1},
  "items": [{"text": "...", "status": "hypothesis", "evidence": ["..."]}],
  "rejected": [{"text": "...", "reason": "证据引用核验失败"}],
  "limits": {"max_prompt_chars": 12000, "max_output_tokens": 2048, "estimated_tokens": 1200}
}
```

---

## 6. MCP 工具面 profile

### 6.1 原则

- **不删工具、不删 handler**；只控制 `tools/list` 的暴露面。
- `full` 仍是默认值，保证向后兼容；`lite` 由用户/团队显式开启。
- 隐藏不等于不可用：自动化仍可按名字直接调用；Skill 按 `lite` 编写，`full` 作为逃生口。

### 6.2 配置

```json
{
  "tool_profile": "full"
}
```

或环境变量：

```bash
CODEREF_TOOL_PROFILE=lite
```

取值：`lite` / `full`。非法值回退 `full` 并在 `coderef_version` 中如实报告。

### 6.3 `lite` 清单（约 12 个）

```text
coderef_version
coderef_plan
coderef_execute
coderef_dispatch_status
coderef_dispatch_report
coderef_dream
coderef_task_status
coderef_scope
coderef_operation_memory
coderef_verify_findings
coderef_change_guard
coderef_audit
```

说明：

- `plan/execute` 覆盖 L1/L2/L3 主链；`audit`/`change_guard`/`verify_findings` 是高频确定性入口。
- 其余工具（scan、arch_*、gov_*、owasp、asset、prompt_governance 等）通过 `full` 或 `execute` 链调用。
- Skill 中所有工具引用必须在 `lite` 清单内；若必须用 `full` 工具，显式标注“需 full profile”。

### 6.4 `coderef_version` 报告

```json
{
  "version": "5.21.0",
  "tool_profile": "lite",
  "tools_exposed": 12,
  "tools_hidden": 47,
  "profile_note": "hidden 不等于移除；直接调用仍可用"
}
```

### 6.5 实现落点

- `Server._handle` 的 `tools/list` 分支：按 `_tool_profile()` 过滤。
- `core/version.py` 或 `core/mcp_server.py` 增加 profile 读取（env 优先，config 次之）。
- 不改 `BUILTIN_TOOLS`、`_handlers`、`HEAVY_TOOLS`。
- 测试：`lite` 暴露数、`full` 暴露数、隐藏工具可直接调用、非法 profile 回退。

---

## 7. Skill 调整

### 7.1 不新增 Skill

现有 4 个 Skill 已覆盖 L1/L2/L3 + 顶层入口。新增“梦 Skill”会增加选择成本，且梦只有 4 个 action，不需要独立 Skill。

### 7.2 在 `coderef-mcp` 增加“梦协议”（权威文本）

建议原文：

```markdown
## 梦协议（5.20.x）

1. 开工前：先看 `context.memory_digest.dream`；若没有，调用
   `coderef_dream(action=read, limit=5)`。把梦当假设，不要当审计结论。
2. 关键节点 seed（最多 1–3 条，每条 ≤200 字）：
   - 用户纠正了你的判断；
   - 闸门/变更守卫拦截了问题；
   - `verify_findings` 推翻了某个论断；
   - 你做了一个会影响后续的决策。
3. 不自动 `coderef_dream(action=run)`；只有用户要求或
   `dream.run_on_startup=true` 时才后台补梦。
4. 引用梦必须写“这是梦里的假设，证据是 …”，不得转述为事实。
5. 梦与确定性工具冲突时，以工具为准，并 seed 一条“梦错了”的 pitfall。
```

### 7.3 L1 / L2 / L3 钩子

| Skill | 醒来看梦 | 关键节点 seed |
|---|---|---|
| `coderef-probe` | 改前看 pitfall / routing | 误报、回归、verify_findings 反驳 |
| `coderef-governance` | 看上次治理目标与未决问题 | 定标决策、refactor 取舍、验证结论 |
| `coderef-asset` | 看资产候选与上次拒绝原因 | 采用/拒绝资产的理由、复刻踩坑 |

### 7.4 不写进 Skill 的内容

- 不让梦自动跑 `run`。
- 不让梦直接调 59 个工具。
- 不把 `DREAM.md` 当作事实来源。
- 不把 seed 当日志；只投喂“下次会用到”的经验。

---

## 8. 与现有模块的接入点

| 模块 | 现状 | 5.21 建议 |
|---|---|---|
| `core/dream.py` | `run()` 单任务 | 增加 `job` 参数与 job 路由；共用核验/写盘 |
| `memory_digest` | 梦优先注入 verified 笔记 | 增加「本次开工建议」最多 3 条，标 hypothesis |
| `remind` | 增加 `dream` 段 | 读取 triage/recap，输出「梦里提醒过什么」 |
| `coderef_version` | 版本 + 新鲜度 | 增加 `tool_profile` / `tools_exposed` / `tools_hidden` |
| `tools_manifest` | 59 个工具 | 不增删；profile 只影响 `tools/list` |
| `docs/dream.md` | 梦的定位与存储 | 增加 job 目录与 `DREAM.md` v2 的链接 |

---

## 9. 验收标准

1. `coderef_dream(status)` 能列出支持的 job、最近 runs、上次错误。
2. `run(job=recap)` 在有 blocked/失败/seed 的项目上返回 ≤3 条开工建议，每条有 evidence；建议全部 `hypothesis`。
3. `run(job=triage)` 不产生 `verified` 建议；`DREAM.md` 的「本次开工建议」最多 3 条。
4. 草稿类 job 只写 `drafts/`，不写 `notes.json`；`read` 默认不返回草稿。
5. `CODEREF_TOOL_PROFILE=lite` 时 `tools/list` 只暴露 12 个；`full` 暴露 59 个；隐藏工具仍可按名调用。
6. `coderef_version` 报告 profile 与隐藏数量；非法 profile 回退 `full` 且如实标注。
7. Skill 中所有工具引用都在 `lite` 清单内，或显式标注 `full`。
8. 全量 pytest 绿；`check_version` EXIT=0；`change_guard` 无退化。

---

## 10. 实施顺序

| 版本 | 内容 | 状态 |
|---|---|---|
| 5.20.2 | Skill 梦协议 + L1/L2/L3 钩子（纯文档） | 已并入 5.21.0 |
| 5.21.0 | `job` 契约 + `recap` / `routing` / `triage` + `DREAM.md` v2 + `CODEREF_TOOL_PROFILE` | 已实现 |
| 5.21.1 | `failure_postmortem` / `change_narrative` + `drafts/` + P2 草稿 job | 已实现 |

排序理由：先把“主 AI 怎么用梦”写进 Skill，再扩梦的任务，最后收工具面；避免工具面先收导致 Skill 引用了不可见工具。

---

## 11. 风险与开放问题

### 风险

- **成本**：每个 job 一次主 LLM 调用。默认只跑 P0/P1，P2 必须显式触发；单 job 有 `MAX_OUTPUT_TOKENS` 上限。
- **建议被误当事实**：`claim_type=advice` 永远 `hypothesis`；Skill 强制标注“梦里的假设”。
- **输入注入**：git log / audit reason 可能含用户文本；只作为数据，不作为指令；继续脱敏与长度限制。
- **草稿污染**：草稿只写 `drafts/`，不自动进入治理库；应用必须走确定性工具 + 用户确认。
- **profile 误导**：隐藏工具不等于移除；`coderef_version` 明确报告。

### 开放问题

1. `recap` 是否应在每次 MCP 启动时自动跑一次？当前 `run_on_startup` 默认关闭，建议首版仍手动/显式开启。
2. `triage` 的排序主观性较强，是否需要引入 `confidence` 阈值或人工反馈来校准？
3. P2 草稿是否需要独立的 `coderef_dream(action=drafts)` 工具，还是复用 `read` 的 `draft=true`？
4. `lite` 是否应在下一版本改为默认？建议先 opt-in 观察一版，再评估 breaking change。
5. 梦的 job 是否需要按项目类型（前端/后端/治理）裁剪？首版不做，避免过度配置。

---

## 12. 明确不做

- 不做“梦 agent 调 59 个工具”。
- 不做梦自动应用 scope/whitelist/target_arch/registry。
- 不做梦直接改代码、提交、推送。
- 不做四层 agent / 记忆 Wiki 树。
- 不把梦的输出当审计结论或健康分依据。
- 不在首版做跨项目共享梦。
