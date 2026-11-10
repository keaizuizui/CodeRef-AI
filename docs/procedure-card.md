# CodeRef-AI 规程入口卡（公开版）

> 这是给 GitHub 用户与外部编程 AI 的**公开入口卡**。内部红线与完整规程（`AGENTS.md` / `CODEREF.md`）不随仓库发布；本卡只保留可公开的发布、审计、CodeRabbit 入口，供 `coderef_operation_memory(action=recover)` 与首次重工具的 `memory_digest` 作为 `procedure_card` 返回。

## 0. 规程索引

- **发布 SOP**：`pyflakes` 0 → `pytest` 全绿 → `python tools/check_version.py` EXIT=0 → 同步测试副本（SHA256 一致）→ 报告写入 `交接/响应册.md` → 与用户确认后 push（master + tag + GitHub Release）。
- **审计全流程**：`coderef_operation_memory(action=recover)` → `coderef_audit` → `coderef_architecture` → `coderef_report` → 产品 / UX 评估 → `coderef_change_guard` → `pyflakes` / `pytest` / `check_version` → 同步测试副本 → 响应册。
- **CodeRabbit**：在 WSL `~/.local/bin/coderabbit`（用 `coderabbit --version` 验证）；Windows `where coderabbit` 找不到不代表没装。实际位置以 `coderef_operation_memory(action=recover)` 返回的 `env_tools` 为准。
- **硬规则**：涉及发布 / 审计 / CodeRabbit，先 `recover` 拿 `procedure_card`；未读对应章节不得动手，不得凭上下文记忆执行。
- **诚实边界**：`missing` / `outside` / `SKIP` / `stale` / `unknown` 是事实，不是故障；不得美化或吞掉。确定性静态分析优先，LLM 只做人话与语义评估，缺 Key 硬阻断不编造。

## 1. 说明

- 本卡是入口，不是完整规程；完整内部红线与复测点以仓库维护者本地的 `AGENTS.md` / `CODEREF.md` 为准。
- 外部用户可以直接把本文件放在自己项目的 `docs/procedure-card.md`；CodeRef 会在 `AGENTS.md` 缺失时读取它并注入 `procedure_card`。
- 若项目同时存在 `AGENTS.md` 的 `## 0. 规程索引`，优先使用项目自己的卡片。
