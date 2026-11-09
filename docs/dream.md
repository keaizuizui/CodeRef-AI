# CodeRef 梦（Dream）设计说明 v0.1

> 一句话：**CodeRef 是编程 AI 的梦境/潜意识。** 主 AI 负责醒着做事，CodeRef 在启动时替它补梦，醒来只给一小片。梦不占用主 AI 的 LLM，也不改写任何确定性结论。

> **实施状态（5.20.0）**：MVP 已落地 `core/dream.py` + `coderef_dream` 工具 + 启动补梦 + `memory_digest` 梦优先 + `remind` 梦段。不在本期范围：四层 agent、Wiki 记忆树、常驻 daemon、跨项目共享梦。

---

## 1. 定位

| 角色 | 职责 | 算力归属 |
|---|---|---|
| 主 AI（意识） | 处理当前任务，上下文有限，会随会话消散 | 主 AI 的 LLM |
| CodeRef（梦） | 跨会话保留、离线巩固、把经历变成联想 | CodeRef 独立配置的 LLM |

- 不是「CodeRef 内部再分四层 agent」；梦是 CodeRef 的产品形态。
- 梦不是又一个数据库，而是**更少、更准的联想**：当 X 出现时，先想到 Y。
- 与现有模块的关系：`audit_log` / dispatch trace 是梦的原始素材；`operation_memory` 是显式账本；知识图谱是代码现状；梦负责把三者之间「还没被写下来的经验」巩固成直觉。

## 2. 已对齐的决策

| 决策 | 结论 |
|---|---|
| 触发 | **下次启动补梦**，一次启动一轮；首版不常驻 daemon |
| 归属 | **项目级 + 本地**：`.coderef/dream/`，默认不进版本库 |
| 原料 | CodeRef 自身痕迹 + 主 AI **显式 seed**；不读取主 AI 全量对话 |
| LLM | CodeRef **独立配置、独立预算**；无 Key 硬阻断 `SKIP`，不编造 |
| 输出 | `DREAM.md`（≤800 token）+ `runs/` 留痕；由 `memory_digest` 注入 |
| 原则 | **LLM 提议，确定性核验**；梦是假设，不是事实 |

## 3. 一次补梦的生命周期

1. **醒来**：读 `.coderef/dream/state.json`，取 `last_dream_at` 与 cursor。
2. **收集**：audit_log 新行、dispatch trace 新文件、session_state 事实、git log、`seeds.jsonl`。
3. **筛显著（零 LLM）**：只取闸门 blocked、change_guard 退化、verify_findings 被反驳、重复误报、主 AI 显式 seed；成功流水账不喂给模型。
4. **做梦（独立 LLM，预算内）**：生成 3–5 条候选，每条必须有 `kind`、`text`、`evidence[]`、`confidence`。
5. **醒验**：能用 change_guard / verify_findings / scan 复算的先复算；不能复算的标 `hypothesis`；复算失败直接 `rejected`。
6. **写梦**：合并去重 → 更新 `DREAM.md`；`runs/<ts>.json` 留输入摘要与产出；推进 cursor。
7. **失败处理**：做梦失败保留旧梦并记录 error；无 Key / 预算耗尽返回 `SKIP`，不产出占位内容。

## 4. 数据形状

```
.coderef/dream/
  state.json        # {version, last_dream_at, cursor, last_status, budget_used}
  DREAM.md          # 顶层梦，≤800 token
  seeds.jsonl       # 主 AI 显式投喂的显著事件
  notes.json        # 巩固后的笔记（含状态与证据）
  runs/<ts>.json    # 每次做梦的输入摘要、候选、采纳/拒绝、token
  archive/          # 过期/下沉笔记
```

`DREAM.md` 只保留五节，保持精简：

```markdown
# Dream
## 当前目标
## 已验证规则
## 坑
## 联想（当 X，先想到 Y）
## 未决问题
```

`notes.json` 单条形状：

```json
{
  "id": "dream-001",
  "kind": "rule | pitfall | convention | association",
  "text": "改完代码先跑 change_guard，再提交",
  "evidence": ["audit_log/20261109.jsonl#L12", "commit:cb00096"],
  "confidence": 0.9,
  "status": "verified | hypothesis | rejected",
  "created_at": "2026-11-09T16:00:00",
  "last_hit_at": "",
  "ttl_days": 30
}
```

## 5. 接口（只描述，不实现）

- `coderef_dream(action=status|seed|read|run)`
  - `status`：最近做梦时间、笔记数、预算、上次错误；
  - `seed`：主 AI 显式投喂一条显著事件（`text` / `kind` / `source`）；
  - `read`：读 `DREAM.md` 或按 `kind` / 关键词取笔记，默认只返回 `verified`；
  - `run`：手动触发一次做梦（后台执行；无 Key → `SKIP`）。
- `memory_digest` 优先读 `DREAM.md`；无梦时如实返回 `available=false`，不冒充"没这项"。
- `remind` 增加 dream 段：除了「你忘了什么」，列出「梦里提醒过什么」。

## 6. LLM 配置

```json
"dream": {
  "enabled": true,
  "provider": "deepseek",
  "api_key": "...",
  "model": "...",
  "budget_tokens_per_run": 20000,
  "max_notes": 40,
  "ttl_days": 30
}
```

- 与主 AI 的 Key/预算隔离；可换更便宜的模型做巩固。
- 做梦前脱敏：key/token/邮箱/绝对用户路径替换为占位符；证据只保留项目相对路径。
- `.coderef/dream/` 默认写入 `.gitignore`，避免隐私与跨机器漂移。

## 7. 红线与诚实边界

- 梦只写 `.coderef/dream/`，**不改** audit findings、图谱、治理库与任何确定性结论。
- 每条笔记必须有 evidence；无 evidence 只能 `hypothesis`，不得进入 `DREAM.md` 的「已验证规则」。
- 梦的视野 = CodeRef 被调用的范围；不假装知道未记录的经历。
- `unknown` / `SKIP` / `stale` 是事实，不美化、不吞掉。
- 主 AI 引用梦时必须标注「这是梦里的假设」，不得当作审计结论转述。

## 8. 评测

| 指标 | 含义 |
|---|---|
| `dream_hit` | 梦的提示被采用 / 避免重复踩坑的次数 |
| `false_memory` | 后来被证据推翻的梦条数（目标 0） |
| `token_cost` | 每次做梦消耗的独立预算 |
| `decay_count` | 30 天未命中而下沉的笔记数 |

方法：取最近 20 条 audit/dispatch 记录做 replay，对比「有梦 vs 无梦」的规则命中率与 token 消耗。初版通过线：`false_memory=0`（人工复核样本）、`dream_hit ≥ 1/10 任务`、单次做梦成本可控。

## 9. 明确不做

- 不做四层 agent / 记忆 Wiki 树。
- 不常驻 daemon、不设定时任务（首版）。
- 不读取主 AI 全量对话。
- 不自动把梦提升为事实。
- 不跨项目共享梦（首版）。

## 10. 开放问题

- 梦的模型选型：便宜模型做巩固，还是强模型做联想？建议先用现有可用模型 + 预算封顶。
- 人工编辑 `DREAM.md` 的冲突处理：首版由 CodeRef 独占写入，人工只读。
- 多用户共享同一项目时是否按 user 分目录：首版项目单份，后续再拆。
- 「联想」类笔记无法确定性复算，只能靠 hit / TTL 与人工抽查。

---

**实施顺序建议**：`state + seed + DREAM.md` schema → 启动补梦后台 worker → `memory_digest` 接入 → `coderef_dream` 工具 → 评测脚本。
