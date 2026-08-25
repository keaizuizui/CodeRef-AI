# CODEREF.md — 本仓库操作红线与规程（记忆投放页）

> 归属：**Coderef-Ai-master（源码仓）** · 给任何进入本仓干活的编程 AI（TRAE / Codex / Claude / Cursor / Copilot）看的**操作规程**。
> 版本 **5.4.2**。凡是认 `AGENTS.md` 的 AI，都会经它一行引到本页（见 `AGENTS.md`）。
> 本页是「操作记忆」的持久化载体之一：**先读我，再动手**。工位后面的实时记忆用 coderef 操作记忆工具存取（见末尾「记忆守则」）。

## 0. 你是谁 / 一句话
你正在维护 **CodeRef-AI**——通过 MCP(stdio) 暴露工具给编程 AI 做确定性审计/图谱/架构/流程验证/变更守护的「治理外脑」。核心结论来自**静态分析**（可复现、无 LLM），LLM 只做人话总结且缺 Key 时**硬阻断**不编造。

## 1. 协作偏好（跨项目通用，优先级最高）
- 沟通用**中文**；交付默认 **Markdown**，**不生成 HTML 渲染**。
- **务实优先**：要「立刻能用」，拒绝抽象/过度复杂框架；受众是创业者/非编程人员，内容一页可操作。
- 迭代 + 常提交；CodeRef 须**通用**，不硬编码某项目架构关系。
- 重构前**先备份原文件**；批量/高危改动先复查脚本是否侵蚀 git 库或备份。
- AI 只做计算/信息聚合/翻译；操作、判断、真实数据核验归人。
- 版本号：major 首位仅在极重大升级时递增；一般修复只升**末位**。

## 2. 提交身份（强制）
本仓无持久化 user.name/email，**一切提交必须带临时身份**：
```
git -c user.name="CodeRef" -c user.email="coderef@local" commit -m "..."
```

## 3. 提交·发布 SOP（每次开发/修复完整走 6 步）
1. 提交本地 git 后，立即刷新 `README` 与 `__init__.py` 的版本号（bug 修复只刷末位 patch）。
2. 跑 CodeRabbit 复审 diff（`coderabbit review`），对照下节「易犯缺陷模式」。
3. 按复审修订后，二次提交本地 git。
4. **与用户确认后才 push**；push 目标 = master 分支，并同时提交 tag + release。
5. 发布同时，把改动**手动 copy 同步到 `Coderef-Test\coderef-src`**（测试自测用源码副本），保证测试看到的就是发布版。
6. 功能修订时评估是否需重启本地 TRAE 的 coderef MCP（stdio 长驻进程，改了工具定义/handler 必须重启才生效）。

## 4. 源码实现红线（改代码前必读）
- `core/mcp_server.py`：所有 MCP wrapper 必须带 `self`；在 `Server.__init__.handlers` 加任何工具，必须同时补 `Server` 类同名实例方法 wrapper（`def _x(self,a): return _x(a)`）。漏写致 `Server()` 构造崩 `AttributeError` → MCP 启动即崩溃。
- `coderef_review`(`core/code_review.py`)：JSON 解析失败必须加一次「强制仅 JSON 输出」重试；仍需 `response_format=json_object` + 容错解析 + 「散文当思考二次抽取」强制重试；重试仍失败必须调 `_degraded_comment_from_text` 生成降级评论（标题「LLM 审查未返回结构化结果」，散文压进 detail）；system prompt 要求「文件可能被截断，请基于可见内容审查，勿因不完整拒绝输出或输出散文」。
- LLM 类工具缺 API key → **硬阻断返回 SKIP**，不产出降级内容。
- 版本号：缺陷修复同时更新 `__init__.py` 与 README，只升末位。
- **不删除任何 git 库**（含开发方 master、测试用例子仓）。
- gov 系列：`gov_webdash.serve` 句柄存模块级注册表、返回仅可序列化元数据；`gov_pipeline` 逐部检查 `store.transition` 返回值、Detected 拒绝进流水线；所有 `/api/*` 加本机回环限制 + 强制绑 host；`data_json` 中 `<>&` 换码；`_gov_workspace` 对 projects 逐绝对路径 + 存在性校验；`role_boundary` 图谱缺失仅警告继续 AST；`dynamic_probe` 的 include_tests 开关必须透传；`gov_schedule` cron 块 `%` 转义 `\%F`，路径用 shlex 引号。
- 方向 B：治理流程收尾会触发 `operation_memory` 增量同步（`OMEM_AUTO_SYNC_ON_GOV` 开关，默认开），失败仅静默记日志，**不得影响主流程结果**。

## 5. 工程约定精华（含 CodeRabbit 教训）
- 架构洞察 `core/arch_insight.py`（P0）：P0-B 真身判定聚合业务级同名类、每副本报引用方、区分「生产入口候选/活跃真身/仅测试引用」；P0-C 目录级同构按相对目录聚合、双指标 Jaccard≥0.5、**空集跳过**。
- 目标架构 Schema `core/target_arch_schema.py` 零依赖手写校验；差距分析器 `core/arch_gap_analyzer.py` 复用 arch_audit 不重写、纯静态不依赖 LLM、7 类差距。
- 图谱节点 ID 相对路径化 `core/code_knowledge_graph.py`（用 `_module_key` 而非 basename，防跨目录同名覆盖）。
- 自由画布 `core/canvas_engine.py`：undo/redo 成对 `recordPre()/commitChange()`（每个 mutation 点都配对）；JS 鼠标坐标先过 `toLocal/toWorld` 换算；`auto_layout` 只对未定位节点(x=0,y=0)赋坐标；节点 id 前缀用 `startswith("role:")` 而非 `endswith(":")`。
- **CodeRabbit 4 类易犯缺陷模式**：①路径子串判断漏根级相对路径→用路径段拆分；②跨目录判定用 basename 误判→用相对目录；③`set` 迭代选目标歧义→收集全部仅唯一时返回；④空集 Jaccard=1.0 误判同构→跳过空集。

## 6. 记忆守则（方向 A+B 落地：存 + 取双向闭环）
- **取（上下文丢失/工具定位/约定类操作前）——强制 gate**：涉及 git / push / CodeRabbit / Release 或约定类操作，先调 `coderef_operation_memory_recover`（一次拿回工具位置+约定/踩坑/决策）→ 按需 `coderef_operation_memory_query`/`find`/`status`。勿满 PATH 找工具、勿未查记忆直接抓外部连接器。coderabbit 等 CLI 常装 WSL `~/.local/bin`，不在 Windows PATH，`where` 找不到不代表没装。
- **存（规模化沉淀）——强制收尾**：走 `coderef_audit` / `coderef_scan` / `coderef_review` 等治理流程后，coderef 已自动做操作记忆增量同步（best-effort）。你也可随时显式 `coderef_operation_memory_sync` 沉淀最新决策。
- 被治理的项目各有独立操作记忆目录（`data/operation_memory/<项目hash>/`），产出 `ledger.json + BRAIN.md + timeline.md`。
- 本页与 `AGENTS.md` 属「送上门」的生效件；operation_memory 属「要取才取」的辅助件——两者都认，互为补充。

## 7. 网络 / 推送
- `git push` 到 github.com 不可达时改用 GitHub API（api.github.com）推送（blob/tree squash 后 commit SHA 会变，需重建 commit 或接受 ahead）；网络恢复 `git fetch` 自然对齐，**优先避免破坏性 `git reset --hard`**。
- 远端 master 分支保护已开启；**push 始终需用户明确确认**，一次性批准不构成长期授权。