# 规程入口卡（示例模板）

> 这是给 GitHub 用户的**示例**，不是 CodeRef 的完整规程，也不是要求你照抄的规则。
>
> **怎么用**：把本文件复制到你自己的项目 `docs/procedure-card.md`，再把下面每条命令替换成你项目的真实命令。
> CodeRef 在 `coderef_operation_memory(action=recover)` 与首次重工具注入时会读取它，作为 `procedure_card` 返回；
> 它只是入口提醒，完整步骤仍以你项目自己的文档为准。
>
> **读取优先级**：项目 `AGENTS.md` 的 `## 0. 规程索引` → 项目 `docs/procedure-card.md` → CodeRef 安装目录自带的同名文件。

## 0. 规程索引

下面以 CodeRef 自己为例，复制后请替换为你项目的流程：

- **发布 SOP**：`pyflakes` 0 → `pytest` 全绿 → `python tools/check_version.py` EXIT=0 → 同步测试副本（SHA256 一致）→ 报告写入 `交接/响应册.md` → 与维护者确认后 push。
- **审计全流程**：`recover` → `audit` → `architecture` → `report` → 产品 / UX 评估 → `change_guard` → lint / 测试 / 版本检查 → 同步副本 → 响应册。
- **CodeRabbit**：在 WSL `~/.local/bin/coderabbit`（用 `coderabbit --version` 验证）；Windows `where coderabbit` 找不到不代表没装，实际位置以 `recover` 返回的 `env_tools` 为准。
- **硬规则**：涉及发布 / 审计 / CodeRabbit，先 `recover` 拿 `procedure_card`；未读对应章节不得动手，不得凭上下文记忆执行。
- **诚实边界**：`missing` / `outside` / `SKIP` / `stale` / `unknown` 是事实不是故障，不得美化或吞掉；确定性静态分析优先，LLM 缺 Key 硬阻断不编造。

## 1. 最小示例（复制这段，替换尖括号内容）

```markdown
## 规程索引

- 发布 SOP：<安装依赖> → <lint> → <测试> → <构建> → <发布命令> → <与谁确认后推送>
- 审计全流程：<恢复记忆命令> → <审计命令> → <报告输出位置> → <变更守护命令>
- CodeRabbit：<可执行文件路径>（验证：<版本命令>）
- 硬规则：<你不希望 AI 跳过的步骤>
- 诚实边界：<哪些状态是事实、不是故障>
```

## 2. 说明

- 本文件只放「入口」：发布、审计、CodeRabbit、硬规则；完整步骤放在你自己项目的文档里。
- 不想写 `AGENTS.md` 也可以，只靠本文件即可被 CodeRef 读取。
