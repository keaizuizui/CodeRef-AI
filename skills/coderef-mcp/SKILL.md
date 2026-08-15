---
name: coderef-mcp
description: 使用 CodeRef-AI 的 MCP 工具对一个项目做确定性审计、知识图谱查询、流程验证、创新复刻与变更守护。当你需要「验证一个项目是否健康 / 是否按预期流程执行 / AI 改没改坏代码 / 查出值得复用的设计 / 在上下文丢失后恢复项目记忆」时使用。核心结论来自静态分析而非大模型猜测。
---

# CodeRef-MCP 使用指南

CodeRef-AI 通过 MCP 协议暴露一组工具，给编程 AI 一双「确定性的眼睛」，给非编程人员一张「看得懂的工程体检单」。本 skill 教你如何正确编排这些工具，得到可信、可复现、诚实的结果。

## 核心原则（必须遵守）

1. **确定性优先**：审计、知识图谱、架构诊断、流程验证、变更守护、OWASP、论断核验这些核心能力全部是纯静态分析，结果确定、可复现，同一个项目每次跑出同样结论。不要怀疑这些工具的确定性结论。
2. **LLM 只用于「人话总结」**：Wiki 归纳、业务报告、创新复刻排查才用到 LLM。没有 API Key 时这些 LLM 产物会被**硬阻断**并提示配置，绝不降级编造。遇到"需要 LLM 但没 Key"的反馈，就如实告诉用户，不要伪造。
3. **后台任务**：重型工具默认后台执行，立即返回 `task_id`，用 `coderef_task_status` 轮询取结果。不要等同步返回（大项目会超时）。
4. **诚实话边界**：
   - 工具返回的 `missing` / `outside` / `未审计` 是**诚实状态**，不是失败。不要把它当成"坏了"。
   - `coderef_verify_findings` 的 verdict 由确定性逻辑打出，你无权改变。确证只代表"引用目标真实存在"，不代表语义结论正确。
   - 你持有一条 LLM/CodeRabbit 论断时，先 `coderef_verify_findings` 核验再采信，避免把未核验的语义论断当事实。

## 工具速查（按引擎）

### 审计引擎 — 全维度体检 + 图谱 + 文档

| 工具 | 用途 | 备注 |
|------|------|------|
| `coderef_audit` | 11 维度一次全量审计 + 自动降噪 + 构建知识图谱 | 首次必跑；`strategy=auto` 自动判定 |
| `coderef_scan` | 单维度审计（快一个量级），写完模块即时自查 | 先 `coderef_scan_list` 看维度 |
| `coderef_architecture` | 架构分析图谱 + 模块画布 HTML | 发现零散重复/模块不统一 |
| `coderef_docs` | 生成结构化 Wiki（README/架构/安装/使用/API） | 后台，3-20 分钟 |
| `coderef_docs_read` | 按需读已生成的 Wiki 正文（返回内容而非路径） | 省 token |
| `coderef_query` | 查询知识图谱（callers/callees/impact/search 等） | 替代 grep，省 10-100 倍 token |
| `coderef_report` | 聚合审计/图谱/Wiki 为自包含 HTML 报告 | 给非编程人员看 |
| `coderef_audit_advisor` | 判断该增量还是全量审查 + 重点维度 | 审计前用 |
| `coderef_review` | 代码审查（diff 变更审查 / full 新项目全量语义首查） | LLM 语义判断，后台 |
| `coderef_frontend` | 前端交互审查（静态清单枚举按钮/菜单 + 可选运行时抽查） | mode=static/runtime |

### 确定性核验 / 流程验证 — 非编程人员最核心需求

| 工具 | 用途 | 备注 |
|------|------|------|
| `coderef_flow_verify` | 验证「入口 A 的调用管线是否覆盖步骤 B→C→D」 | 纯静态，状态分 ordered/in_pipeline/outside/missing |
| `coderef_arch_audit` | 架构腐化诊断（循环依赖/上帝模块/分层违例/模块过大） | 0-10 健康度 |
| `coderef_verify_findings` | 确定性核验 LLM/CodeRabbit 论断 | verdict 由确定性逻辑打出 |

### 变更守护引擎 — 拦截 AI 把代码改坏

| 工具 | 用途 | 备注 |
|------|------|------|
| `coderef_change_guard` | AI 代码退化检测（校验链被删/重试削弱/约束移除/回归风险） | `action=guard` 核心；需 git |
| `coderef_change_report` | 把 diff 归纳成人话版变更说明 | LLM 不可用降级为结构摘要 |

### 记忆引擎 — AI 对项目「记住了什么」

| 工具 | 用途 | 备注 |
|------|------|------|
| `coderef_memory_sync` | 初始化/增量同步项目记忆层 | mtime+size 增量 |
| `coderef_memory_query` | 供 AI 复用项目记忆（语义/结构查询） | 替代重扫 |
| `coderef_memory_status` | 「AI 知道什么」：覆盖度 + 置信度 + 盲区 | 用户直观视角 |
| `coderef_memory_quality` | 记忆质量评估 + 自动补全 | |

### 操作记忆层 — 应对上下文丢失（4.8 新增）

| 工具 | 用途 | 备注 |
|------|------|------|
| `coderef_operation_memory_sync` | 盘点 git/模型/API/测试工具/文档/依赖的位置 + 提炼决策/约定/踩坑 | 输出 ledger.json + BRAIN.md |
| `coderef_operation_memory_query` | 按类别检索操作记忆（decision/convention/pitfall/resource/tool/doc） | 上下文丢失后快速恢复 |
| `coderef_operation_memory_find` | 定位资源：「test 工具在哪儿」「.env 在哪儿」 | 别再满项目找 |
| `coderef_operation_memory_status` | 操作记忆健康度 + 待人工确认项 | |

### 创新引擎 — 挖出值得复用的设计

| 工具 | 用途 | 备注 |
|------|------|------|
| `coderef_innovation` | 识别创新设计 + 传播缺口 | |
| `coderef_asset` | 资产化/查询/导出/固化设计 | commit 需 ≥2 workflow 采用 |
| `coderef_replicate` | 复刻铺排：检测目标项目对某资产的采用缺口 | 生成复刻指引 |
| `coderef_replicate_apply` | 把复刻指引实际落到目标项目 | 只落地确定性内容，不自动改源码 |
| `coderef_asset_blueprint` | 把复刻铺排的确定性结论写回蓝图 | |
| `coderef_registry` | 已知设计库管理（list/add/alias 归一） | |
| `coderef_innovation_review` | LLM 协助复查「是否真创新 + 复刻是否合理」 | 无 API Key 硬阻断 |

### 合规 / 治理 / 人话解读

| 工具 | 用途 | 备注 |
|------|------|------|
| `coderef_owasp` | OWASP LLM Top 10 合规检测 | 逐类分级 |
| `coderef_prompt_governance` | Prompt 治理平台（资产生命周期×合规审计×跨模块一致性） | 唯一入口 |
| `coderef_interpret` | 人话解读：健康总览/仪表盘/Wiki/assets | 给非编程人员看 |
| `coderef_whitelist` | 误报白名单 + 核心模块规则管理 | 审查确认为误报后写入 |

## 标准工作流

### 工作流 A：对一个新项目做「完整体检」
```
1. coderef_audit (strategy=auto, background=true)   → 全量审计 + 建图谱
2. coderef_task_status 轮询 → 取审计结果
3. coderef_docs (background=true) → 生成 Wiki
4. coderef_arch_audit → 架构健康度
5. coderef_owasp → 合规
6. coderef_interpret (action=dashboard) → 人话仪表盘给非编程人员
```

### 工作流 B：非编程人员验证「项目是否按我期待的流程执行」
```
1. 用户说期望：入口 A 应依次经过 B→C→D
2. 你把中文步骤映射成代码符号（如 'pipeline_runner.audit'）
3. coderef_flow_verify (entry=A, steps=[B,C,D])
4. 把 ordered/in_pipeline/outside/missing 四种状态如实告诉用户
5. 若 missing → 补查 coderef_query (search) 确认符号是否存在
```

### 工作流 C：AI 改完代码，提交前确认没改坏
```
1. 生成 git diff
2. coderef_change_guard (action=guard, diff=...) → 退化检测
3. 若发现退化高风险 → 如实报告，建议回滚（不自动改）
4. coderef_change_report (diff=...) → 生成人话变更说明
```
> 提示：git 常不在 PATH，用 `Get-Command git` / `where git` 探测后用 `git_bin` 传入。

### 工作流 D：复刻一个值得复用的设计到另一个项目
```
1. coderef_innovation (project_path=源) → 识别创新
2. coderef_asset (action=commit) → 固化为资产
3. coderef_registry (action=list) → 确认 canonical
4. coderef_innovation_review → LLM 复查是否真创新（需 API Key）
5. coderef_replicate (project_path=目标, canonical=...) → 复刻铺排
6. coderef_replicate_apply → 落地到目标项目
```

### 工作流 E：上下文丢失后恢复项目记忆
```
1. coderef_operation_memory_status → 看覆盖度 + 待人工确认项
2. coderef_operation_memory_query (query_type=all) → 恢复资源/决策/约定/踩坑
3. coderef_operation_memory_find (name=test/.env/model) → 定位具体资源
4. coderef_operation_memory_query (query_type=tool) → 定位开发工具位置（git/python/coderabbit 等，含 WSL 内工具）
5. coderef_memory_query (query_type=semantic) → 恢复代码语义记忆
```

## 常见陷阱

- **不要同步等重型工具**：audit/docs/review/memory_sync 都默认后台，必须轮询 `coderef_task_status`。
- **不要把诚实状态当失败**：`coderef_flow_verify` 返回 `missing`、`coderef_interpret` 提示"未审计"，都是如实反馈，要原样转述给用户。
- **不要自己改 verify_findings 的 verdict**：它由确定性逻辑打出，你无权改变。
- **没有 API Key 时的 LLM 工具**：`coderef_docs`(LLM 归纳部分)、`coderef_change_report`、`coderef_innovation_review`、`coderef_interpret action=wiki` 会诚实提示需配置 Key。如实告诉用户，不要伪造产物。
- **工具定位优先查操作记忆**：需要 git/python/coderabbit 等工具位置时，先 `coderef_operation_memory_find` / `coderef_operation_memory_query (query_type=tool)` 从操作记忆取，别满 PATH 找。coderabbit 等 CLI 常装在 WSL 的 `~/.local/bin`，不在 Windows PATH——`where` / `Get-Command` 找不到不代表不存在，不代表没装。
- **所有工具都要传 `project_path`**：这是必填参数，指向被测项目路径。