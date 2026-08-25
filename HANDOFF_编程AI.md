# HANDOFF — 编程 AI 交接

> 归属：**Coderef-Ai-master（源码仓）** · 给下一任负责本仓源码的**编程 AI**
> 配套：测试交接见 `Coderef-Test/HANDOFF_测试AI.md`
> 更新：2026-08-26 · 当前版本 **v5.4.2**

## 打开项目先看
- **当前版本** `5.4.2`（`__init__.py` 的 `__version__` 与 README 顶栏同步；缺陷修复只升末位，major 仅重大升级才增）
- **技术栈**：Python 3.10+，MCP(stdio) 协议，授权 **PolyForm Noncommercial 1.0.0**（5.0 起；4.X 仍 MIT）
- **提交身份**：本仓无持久化 user.name/email，提交必须用临时身份：
  `git -c user.name="CodeRef" -c user.email="coderef@local" commit -m "..."`
- 工具口径：README/工具表=55（含历史/别名工具描述行），`_handlers` 唯一实现=54（如 coderef_prompt_audit 已并入 governance）——两者差异是既有事实，非回归

## 协作偏好（源自 TRAE user_profile，跨项目通用）
> 这是"如何与这位用户协作"的总纲，比任何约定都优先。凡与下无冲突，先照此办。
- **沟通用中文**；交付默认 Markdown，不生成 HTML 渲染
- **务实优先**：要"立刻能用"的方法，拒绝抽象/过度复杂的框架；喜欢**一页可操作**的内容（受众是创业者/非编程人员）
- **迭代 + 常提交**：小步推进、定期测试、频繁 Git 提交
- **CodeRef 须通用**：不硬编码某个项目的架构关系，保持通用能力
- **重构前必须先备份**原文件；凡批量/高危改动先复查脚本会不会侵蚀 git 库或备份
- **AI 职责边界**：AI 只做计算/信息聚合/翻译；操作、判断、真实数据核验这类"人的活"不得由 AI 越俎代庖
- **版本号**：major（首位）只在极重大升级时递增；一般缺陷修复只升末位
- 开源就绪是优先（可靠性示范）
- 必要的精确分析可借助 LLM + 本地代码知识库 + 搜索引擎

## 提交·发布 SOP（必循，每次开发/修复完整走一遍 6 步）
1. **提交本地 git**后，立即刷新 `README` 与 `__init__` 的版本号（**非必要不提升第二位** minor；bug 修复只刷最后一位 patch）
2. **提交 CodeRabbit 复审 diff**（`coderabbit review` 生成，见 `工程约定` 中的易犯缺陷模式）
3. 按复审修订后，**二次提交本地 git**
4. **与用户确认后才 push**；push 目标 = **master 分支**，并同时提交 **tag + release 版本**
5. **发布同时**，把改动**同步到 `Coderef-Test\coderef-src`**（测试自测用源码副本，手动 copy 同步被改文件）——保证测试看到的就是发布版
6. **功能修订时评估是否要刷新本地 TRAE 的 MCP**（coderef MCP 是 stdio 长驻进程，改了工具定义/handler 后必须重启 MCP/TRAE 才生效，否则测试连旧进程误判）

## 源码实现红线（改代码前必读）
- **`core/mcp_server.py`**：所有 MCP wrapper 方法必须带 `self`；在 `Server.__init__` handlers 加任何工具，必须同时补 `Server` 类的同名实例方法 wrapper（`def _x(self,a): return _x(a)`）。漏写会致 `Server()` 构造崩 `AttributeError` → 整个 MCP server 启动即崩溃
- **`coderef_review` / `core/code_review.py`**：
  - JSON 解析失败必须加一次"强制仅 JSON 输出"重试，避免系统性降级
  - `response_format=json_object` + 容错解析 + "散文当思考二次抽取"强制重试
  - 重试仍失败必须调 `_degraded_comment_from_text` 生成降级评论，标题"LLM 审查未返回结构化结果"，LLM 散文压缩进 detail
  - system prompt / diff 输出要求加："文件内容可能被截断，请基于可见内容审查，不要因不完整拒绝输出或输出散文"
- **LLM 类工具**缺 API key → 硬阻断返回 SKIP，**不产出降级内容**
- **版本号**：缺陷修复需同时在 `__init__.py` 与 README 更新，只升末位
- **不删除任何 git 库**（含开发方 master、`测试用例\*.git`）
- **gov 系列**：
  - `gov_webdash.serve` 的 ThreadingHTTPServer/Thread 句柄存模块级注册表，返回值仅可序列化元数据
  - `gov_pipeline` 逐部检查 `store.transition` 返回值；Detected 项明确拒绝进流水线；Verified 流转被拒如实标注
  - 所有 `/api/*` 加本机回环限制 + 强制绑 host
  - `data_json` 中 `<>&` 换码为 `\<`/`>`/`&`
  - `_gov_workspace` 对 projects 逐个绝对路径 + 存在性校验
  - `role_boundary` 图谱缺失仅警告并继续 AST，未归属模块跳过
  - `dynamic_probe` 的 include_tests 开关必须透传
  - `gov_schedule` cron 块 `%` 转义为 `\%F`，路径用 shlex 引号
  - 文档同步更新 serve 读写契约、工具总数、pipeline 仅接收 Confirmed/Fixing、Detected 拒绝转移

## 工程约定（含 CodeRabbit 教训）
- **架构洞察 `core/arch_insight.py`**（P0 能力）：
  - P0-B 真身判定聚合范围 = 业务级同名类，每副本报告引用方，判定区分"生产入口候选 / 活跃真身 / 仅测试引用"
  - P0-C 目录级同构：按相对目录聚合文件清单+函数签名，双指标 Jaccard ≥0.5 判同构
- 目标架构 Schema `core/target_arch_schema.py`：business_flows / tech_roles / constraints，零依赖手写校验
- 差距分析器 `core/arch_gap_analyzer.py` 复用 arch_audit 不重写，纯静态不依赖 LLM，7 类差距：缺失/依赖违例/循环/业务断链/游离/上帝模块/异常规模
- 图谱节点 ID 相对路径化 `core/code_knowledge_graph.py`：ID 前缀由 basename 改 `_module_key`，避免跨目录同名文件被覆盖漏扫
- **自由画布 `core/canvas_engine.py`（v5.4.1 CodeRabbit 复审 4 findings）**：
  1. undo/redo 栈必须记录"操作前(pre-mutation)"态——`recordPre()`（mutation 前记一次）/`commitChange()`（mutation 后入栈）成对，**每个 mutation 点都必须配对**（拖拽/方向键/连线/增删改/复制/导入/saveProps）；不能只 mutation 后调 `pushHistory`（pendingPre 为空则空操作不可撤销）
  2. JS 内鼠标 client 坐标必须先过 `toLocal`/`toWorld` 换算 canvasWrap 局部坐标（44px 工具栏偏移），否则连线/吸附/右键落点偏差
  3. `auto_layout` 与分层/力导向只对未定位节点（x=0 且 y=0）赋坐标，已定位节点作固定锚点保留（force 锚点只推挤可动、自身不移位）
  4. 节点 id 前缀判断用 `startswith("role:")` 而非 `endswith(":")`（角色 id 形如 `role:<rid>`）
- **CodeRabbit 4 类易犯缺陷模式**：①路径子串判断漏根级相对路径——用路径段拆分；②跨目录判定用 basename 会误判同目录——用相对目录；③`set` 迭代选目标歧义——收集全部仅唯一时返回；④空集 Jaccard=1.0 误判同构——跳过空集

## 网络 / 推送
- `git push` 到 github.com 不可达时改用 GitHub API(api.github.com) 推送（blob/tree squash 后 commit SHA 会变，需重建 commit 或接受 ahead 差异）；网络恢复 `git fetch` 自然对齐，**优先避免破坏性 `git reset --hard`**
- 远端 master 分支保护已开启；**5.0 起的推送仍需用户明确确认**，一次性批准不构成长期授权

---

## ✅ 重要课题 — TRAE 记忆 vs coderef 执行记忆的冲突（已落地 v5.4.2）

**背景（2026-08-25 复现确认）**：coderef 宣称提供"操作记忆"（`coderef_operation_memory_sync/query/find/recover`，输出 ledger.json + BRAIN.md + timeline.md，按项目 hash 落盘 `data/operation_memory/<hash>/`），但**使用 coderef 的编程 AI（含 TRAE 侧的我）长期既不主动存、也不主动取**。这不是偶发 bug，而是"MCP 被动工具"生态的系统性通病——任何放在"AI 记忆体系之外"的记忆，默认被所有编程 AI 无视。

**根因**：
1. 所有主流编程 AI（TRAE/Codex/Claude/Cursor/Copilot）只认自己**必然读取的公约文件**（`AGENTS.md`/`CLAUDE.md`/`.cursorrules`/`CODEREF.md`），工作记忆只在会话内
2. coderef 的操作记忆是"需 AI 主动调 MCP 工具"才能存取的被动记忆，AI 的注意力不在这条路径上 → "写了没人读、读了没人写"的孤岛
3. 两套体系零互通：TRAE 侧有独立记忆（user_profile / project_memory / topics / session），coderef 记忆名义上给"被治理项目 AI 恢复用"，但实际干活的 AI 用的是另一套

**关键矛盾**：TRAE 记忆是"我作为助手该怎么干活的操作规程"（作）；coderef 记忆是"项目有哪些客观资产与隐性约定"（知）。两者本可合流为同一条分层链路，但当初未统一设计。

**落地结果（v5.4.2，方向 A 为主 + B 为辅）**：
- **方向 A（主，已落地）**：把操作红线/规程投放到项目根 `CODEREF.md`，再由 `AGENTS.md` 一行引入 → 记忆进入所有认 AGENTS.md 的 AI 的自然读取路径（"送上门"）。CODEREF.md 含第 6 节「记忆守则」，显式要求取/存/强制 gate，把 A+B 串成双向闭环。
- **方向 B（辅，已落地）**：把 `operation_memory` 增量同步收紧为治理流程强制收尾——`core/pipeline_runner.py` 新增 `_auto_sync_om_on_gov()`，在 `coderef_audit`（`Pipe.audit`）与 `coderef_scan`（`run_single`）收尾自动增量同步 `mode="incr", with_llm=False`；**后台 daemon 线程执行，不阻塞工具返回，同一项目 30s 内去重**；开关 `settings.OMEM_AUTO_SYNC_ON_GOV`（默认 True），best-effort 失败仅记日志不破坏主流程。解决"存"，让记忆始终新鲜供 recover/query。`coderef_review/architecture/docs` 等 LLM/重型路径不走自动同步，可显式 sync。
- **待观察**：方向 A 是否真被"陌生编程 AI"自然读到（用多 agent 实测），以及 CODEREF.md 多 AI 并发时的写入治理（当前由我统一维护，暂未开放多 AI 写）。

## 课题双册对账（2026-08-25 起取代"共写课题统一清单"）

> 背景：原「CodeRef-AI课题统一清单」被测试与开发方共写，存在双写冲突且长期堆积已闭环项。现改为**监控双册、各写各的**，消除共写。

- **测试侧登记册**（测试唯一写）：`测试归档\课题台账\登记册.md` —— 发起课题（U-*）、写期望、回归验证、判定关闭；已闭环项由测试移入 `测试归档\课题台账\已关闭\`。
- **开发侧响应册**（**你唯一写**）：`交接\响应册.md` —— 承接课题、修复版本、回报结果。
- **你的动作（必循）**：
  1. 处理新课题前先读登记册，登记册只留未闭环项。
  2. 处理完在**响应册**回报（编号/版本/证据指针），同步升 `__init__.py`+README 末位 patch、`copy` 到 `Coderef-Test\coderef-src\`、重启 coderef MCP。
  3. **只写响应册，不写测试侧任何文件**（`测试归档\`、`真实屎山治理\` 均归测试 owner，你只读）。