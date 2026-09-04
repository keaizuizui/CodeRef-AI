# CodeRef-AI 更新日志

> 本文件归档 CodeRef-AI 自 v3.0 以来的完整更新日志。线上 README 只保留当前版本状态，历史逐版本记录统一归档于此。

---

### v5.13.9 — U-43 design 登记 description 拦截未核验的采用数声明（防「登记假设当事实」）

> 承接登记册「多 Skill 成效与调度审查」#5（开发方欠账）：registry「设计登记」可写入未经核验的采用数声明（如 `multi_source_route` 声称「≥2 workflow 采用」而 callers 实际仅 1），把登记假设当事实。

- **根因**：`DesignRegistry.manage(action="add")` 直接把 description 落库，designs 区不承载 adopters/callers 核验证据，description 里可写死未经核验的「被 N workflow 采用」声明。
- **修复**：
  - 新增 `_ADOPTION_DECL_RE` 正则，`manage(add)` 登记时若 description 命中采用数声明（`被 N workflow 采用` / `N 个 workflow 采用` / `≥N workflow 采用`）即 `ValueError`，引导改用 `coderef_asset(action=commit)` 依 adopters 数据固化后再声明。
  - 修订现有 `data/design_registry.json` 的 `multi_source_route` 描述，去除夸大 claim，改为事实性表述并标注「采用数未经核验（观察项记录用）」。
- **验证**：临时自测——正则命中「≥2 workflow 采用」「2 个 workflow 采用」变体；`add` 拒绝夸大声明并报错含命中片段；事实性描述正常放行落库；现有条目已无禁用声明。全量 **164 用例通过**。
- **版本号**：5.13.8 → 5.13.9（patch，功能修订；`data/design_registry.json` 在 `.gitignore` 仅本地生效）。

---

### v5.13.8 — U-41 arch_verify 健康维度接入 arch_audit 真实健康分

> 承接登记册 U-41（2026-09-04 测试方）：`coderef_arch_verify` 四维评分中 health 维度对三个项目全部恒 `score=0.5 / arch_health_raw=null` 占位，与 `arch_audit` 真实健康分严重不符（Coderef-Ai-master audit=2.0、kuajingdianshang=5.0 却 verify.health 恒 0.5），健康权重 10% 计入总分时失真、可能掩盖重构后真实腐化。

- **根因**：`arch_alignment_verifier._score_health` 读 `r.get("health_score")`，但 `arch_audit.audit` 的健康分实际在 `r["summary"]["health"]`（0-10），该字段名不存在 → 恒 None → 走默认 `0.5`；且结果字典里 `arch_health_raw` 硬编码为 `None`。
- **修复**：`_score_health` 改为读 `r["summary"]["health"]` 并返回 `(归一score, 原始health)`；health 维度 `arch_health_raw` 透传真实 0-10 健康分。no_code 时 health=None，raw 显式透出 None、score 保留保守默认（有代码项目 raw 恒真实值）。
- **验证**：mock 自测 `2.0→(0.2,2.0)`、`5.0→(0.5,5.0)`、`10.0→(1.0,10.0)`、no_code→`(0.5,None)`；真实现场 Coderef-Ai-master `audit 2.0` → verify.health `{score:0.2, arch_health_raw:2.0}`（此前恒 0.5/None）。
- **回归**：全量 **164 用例通过**（Python 3.10）。
- **版本号**：5.13.7 → 5.13.8（patch，缺陷修复；不改工具暴露面）。

---

### v5.13.7 — operation_memory 提炼来源纳入仓库根规程文件（CODEREF.md/AGENTS.md）

> 测试方诉求「tests 测试副本归属约定」落 operation memory 时发现：`ResourceScanner._detect_docs_reports` 只把路径含 `docs`/`wiki` 或文件名含 `readme` 的 md 收为 doc 来源，仓库根的 `CODEREF.md`/`AGENTS.md`（操作红线/规程全文）从未被纳入 sync 提炼来源，导致其内容无法被 LLM 提炼为隐性知识（decision/convention/pitfall）。

- **修复**：`core/operation_memory.py` 的 `_detect_docs_reports` 分类条件追加仓库根 `coderef.md`/`agents.md` 文件名匹配，使操作红线/规程可被 `coderef_operation_memory(action=sync)` 提炼。
- **落库**：`tests/` 测试副本归属约定（测试方唯一 owner、开发方只回报用例清单、验收以用例数一致为校验、留证用例不得覆盖/删除）已确定性写入 `data/operation_memory/<hash>/ledger.json` + `BRAIN.md`（source=CODEREF.md 第 8 节），`coderef_operation_memory(action=query, query_type=convention, keyword=tests)` 可检索命中。
- **回归**：全量 **164 用例通过**（Python 3.10）。
- **版本号**：5.13.6 → 5.13.7（patch，功能修订；不改工具暴露面）。

---

### v5.13.6 — U-39/U-40 双课题修复（图谱层排除口径 + workflow_graph 接入白名单 dir 排除）

> 承接登记册 U-39、U-40（2026-09-03 测试方开立）。v5.13.5 的 memory_layer 排除因过滤条件错误未真正生效（假修复被复测识破），本轮修正口径并补齐 workflow_graph 链路。

- **U-39 · 图谱层同步目录排除失效**：v5.13.5 在 `MemoryLayer._update_knowledge_graph` 的排除过滤里误加 `rule=="dir"`，而真实白名单 dir 排除条目的 `rule` 字段缺失（以 `dir` 字段标志），导致 `exclude_dirs` 恒空、备份目录仍入图（`cache\kg` 中 `_refactor_backup` 节点 265 / 边 1095）。修复：过滤改为仅判 `dir` 字段，与 `pipeline_runner._build_kg` 口径一致。回归测试改用**真实形态条目**（`{"dir": ...}`，无 rule），杜绝假绿。
- **U-40 · architecture 产物备份排除口径割裂**：`workflow_graph` 的 GitNexus 索引链路与 CodeAnalyzer 降级链路收集节点时未应用白名单 `dir` 排除，备份目录（`_refactor_backup`）节点进 `workflow_graph_*.html`，并经 `project_overview` 降级内嵌传导到总览架构图。修复：`generate()` 统一套用 `_apply_whitelist_exclude`，复用图谱层 `_is_excluded_path`，与图谱/画布/洞察口径一致。
- **回归测试**：新增 `WorkflowGraphExcludeTest`×2（备份节点/边过滤 + 无白名单不排除）+ 修订 `MemoryLayerKgExcludeTest` 为真实形态条目；全量 **163 用例通过**（Python 3.10）。
- **版本号**：5.13.5 → 5.13.6（patch，缺陷修复；不改工具暴露面）。

---

> 承接登记册 U-38 真实现场终验后留下的 2 条观察项（不阻塞闭环，但须修复以防止后续版本再触发同类问题）。

- **① coderef_version 版本探针返回空**（登记册 U-38 §观察项①）：`_run` 层 `project_path` 校验豁免名单只列了 `coderef_scan_list` / `coderef_gov_workspace`，**漏了 `coderef_version`**。schema 声明无需 project_path，但调用时 `p=""` 走到 `_validate_project_path` 抛 ValueError → 客户端看到"返回空"。这也是 v5.12.7 登记册 §1 就提过的"schema 标无需参数与实际封装不一致"的根因。修复：把 `coderef_version` 加入豁免名单，使其与 `coderef_scan_list` 一致——schema 与运行时行为对齐。
- **② 图谱重建时 _refactor_backup 备份目录仍入图**（登记册 U-38 §观察项② / U-37③ 关联方向）：`MemoryLayer._update_knowledge_graph` 调用 `kg.build()` **没传 `exclude_dirs`**，导致 `coderef_memory(action=sync, mode=full)` 路径下备份目录文件全入图，污染符号级真身/循环/重复判定。`pipeline_runner._build_kg` 是接了 whitelist dir 的，但 memory_layer 这条独立路径漏了。修复：`_update_knowledge_graph` 读取 whitelist `rule=dir` 条目并传入 `exclude_dirs`，与 `_build_kg` 口径一致。
- **回归测试**：新增 `VersionProbeTest.test_via_run_skips_path_validation`（走 `_run` 层空参数返回版本）+ `MemoryLayerKgExcludeTest.test_update_kg_passes_whitelist_dirs`（memory 层 whitelist dir 传入 kg.build）×2；全量 **161 通过**（Python 3.10）。
- **版本号**：5.13.4 → 5.13.5（patch，缺陷修复；不改工具暴露面）。

---

### v5.13.4 — 架构图产物收敛（architecture 附带生成画布 + 总览降级内嵌）

> 承接外部反馈「AI 连续两次指出没看到做架构图」：根因是 `coderef_architecture` 的产物分离——它产出的 workflow_graph（原始调用图，落 `.gitnexus/`）与架构洞察 md，并不产出带角色归属的可视化画布；而画布 `arch_canvas` 由独立入口 `coderef_arch_canvas` 生成。总览报告只认 `.coderef/arch_canvas_*.html`，没有就显示"尚未生成架构画布"，于是编程 AI 只跑 architecture 却在总览看不到架构图。
> - **A. `coderef_architecture` 一次调用即产出画布**：`Pipe.architecture()` 追加调用 `ArchCanvas().generate()`，画布落 `<proj>/.coderef/arch_canvas_*.html`，路径写入 md 报告尾部「🖼 架构画布」段落，并通过新增 `PipeResult.arch_canvas` 字段回传供 MCP 返回。名字与产物对齐——"架构分析图谱"名副其实包含可视化画布。画布生成失败不阻断报告（记入 errors 继续出 md）。
> - **B. 总览报告降级内嵌 workflow_graph**：`project_overview` 新增 `_find_workflow_graph()` 兜底——无 `arch_canvas` 时在 `.gitnexus/`（兼容 `coderef-report/`）找最新 `workflow_graph_*.html` 内嵌，保证总览至少有一张架构图；都无才提示运行。`arch_canvas` 优先、workflow_graph 兜底的等级顺序，`payload[arch_fallback]` + `sections[workflow_graph]` 透出状态。
> - **回归测试**：新增 `ArchitectureCanvasProbeTest`（architecture 产出画布 + 画布失败不阻断）×2 + `RenderOverviewTest.test_fallback_to_workflow_graph_when_no_canvas`×1；全量 **159 用例通过**（Python 3.10）。
> - **版本号**：5.13.3 → 5.13.4（patch，缺陷修复；不改工具暴露面）。

### v5.13.3 — U-37① 再次修复（arch_gap 豁免尾段匹配，目录前缀形态生效）

> 承接测试方复核（登记册「U-37 开发方回报 v5.13.2 后 · 测试方复核」）：U-37① 在 v5.13.2 用精确集合命中实现豁免消费，但真实重复簇 `copies[].file` 是**带目录前缀的相对路径**（`技能库/smart_data_hub.py`），而豁免条目 `file` 是裸文件名（`smart_data_hub.py`）→ 精确 `in` 比较永不相等 → 豁免失效，`search_products` 仍报 `true_duplicate`。上一轮迷你测试 copies 用裸名故假绿（覆盖缺口恰在"copies.file 带目录前缀"真实形态）。U-37② 经真实形态建图复核**修复成立**（CALLS 边齐全、verify ordered，疑用户实测连旧进程所致）。
> - **fix（U-37① 豁免尾段匹配）**：`arch_gap_analyzer` 新增 `_exempt_match(copy_file, exempt_file)`——统一正斜杠后按「精确相等 或 目录分隔尾段相等」判定（`cf.endswith("/" + ef)`），既容忍 `copies.file` 的目录前缀、又用 `/` 层级边界防不同目录同名文件误豁免；`_detect_duplicates` 的豁免命中由精确 `in` 集合改为对该 helper 的任意匹配。未豁免真重复 / `designed_parallel` 语义保留不受影响。
> - **回归测试**：`ArchGapDuplicateExemptionTest` 新增 `test_exempt_hits_when_copy_has_dir_prefix`（登记册点名 RED 用例，copies.file 带目录前缀时豁免命中）×1、`test_exempt_hits_with_leading_dir_and_slash_normalize`（backslash 路径 + 豁免带目录前缀）×1；保护用例「未豁免真重复仍报 / designed_parallel 保留 / 豁免裸名直匹配」保持 GREEN。全量 156 用例通过（Python 3.10）。
> - **版本号**：5.13.2 → 5.13.3（patch，缺陷修复；不改工具暴露面）。

### v5.13.2 — U-37 外部反馈三工具问题修复（arch_gap 白名单豁免 + flow_verify 实例化调用建边）

> 承接测试方交接清单（`测试归档\20260903-v5.13.1-U37-外部反馈三工具问题交接\交接清单.md`）：U-37① `arch_gap` 不消费白名单 `rule=duplicate` 豁免、U-37② `flow_verify` 对「import（模块级/方法体内）+ 实例化对象方法调用」建边缺口，2 项真实缺陷（测试 7 RED 留证）修复；U-37③ 规则层排除已修复仅防回归。
> - **fix（U-37① arch_gap 消费白名单 rule=duplicate 豁免）**：`arch_gap_analyzer._detect_duplicates` 此前仅按 `semantic_kind`（true/designed_parallel）过滤，不读取 whitelist 的 `rule=duplicate` 条目 → 已人工豁免的"设计并存"符号被重新点名收敛。修复：新增 `_whitelist_duplicate_exemptions` 读取真实 schema（`file`+`rule`+`category`），簇的任一副本文件命中豁免条目即不再产出 duplicate gap；未豁免真重复 / `designed_parallel` 语义保留不受影响。
> - **fix（U-37② flow_verify 实例化对象方法调用建边）**：`code_knowledge_graph._build_from_ast` 对 `svc.run()` 仅做「全名精确 / self-cls / 短名 LIKE 回退」三档解析，无法回推变量宿主类 → 边漏建 → verify 把真连通步骤判 `outside`/`missing`；且构造调用 `Res()` 被 LIKE 碰巧匹配到 `Res.run` 掩盖缺边。修复：① `ast_parser` 新增 `local_imports`/`local_assignments` 字段，递归提取方法体/类体内局部 import 与赋值（模块级 import 仍在 `imports`），`memory_layer` 序列化同步；② `_build_from_ast` 新增 `_collect_var_host_classes` 从两档 import + 实例化赋值推导「变量名 → (宿主类, 模块)」映射；③ 调用解析新增实例化档 `_resolve_instance_call`（`tool=ResearchTool(cfg)` → `tool.run` → `method:<宿主类模块>:ResearchTool.run`，精确主键匹配防跨类同名误绑），置于短名 LIKE 回退之前，杜绝伪 CALLS 边冒充真边；④ caller 定位新增 `_ast_containing_method_id` AST 结构兜底（nodes 表行号缺失时用 AstFileResult 真实行号定位调用所在方法）。
> - **回归测试**：`tests/test_feedback_fixes.py` 新增 `ArchGapDuplicateExemptionTest`（豁免符号不报 / 未豁免真重复仍报 / designed_parallel 保留）×3、`InstanceCallResolveTest`（局部 import 实例化建边 / verify 不判 outside-missing / 不误绑跨类同名）×3、`SelfSiblingCallResolveTest`（self 兄弟调用建边保护）×1、`ModuleLevelImportInstanceCallTest`（模块级 import 实例化建边 / 兄弟+实例化双边 / 不误绑 / verify 闭环）×4；宿主 suite 58 用例 7 RED 全转绿，全量 154 用例通过（Python 3.10）。
> - **fix（CodeRabbit 复审意见，同 v5.13.2）**：`_collect_var_host_classes`/`_module_key_for_import` 3 项 major 修复——① `var_host` 键改为「(变量名, 词法作用域)」（模块级 `mod:<mod>` / 方法体内所在方法 id），避免不同方法同名局部变量相互覆盖，`_resolve_instance_call` 先按调用者作用域查再回退模块级；② 实例化赋值保留类名全称，`import X.Y.Z` 登记 `X → X.Y.Z`，`X.Y.Z.Class()` 按前缀逐级回退解析模块；③ `_module_key_for_import` 优先完整模块路径匹配（`pkg.tool_research` → `pkg/tool_research`）再回退文件名末段，避免跨包同名文件模糊匹配。复跑全量 154 用例通过。
> - **版本号**：5.13.1 → 5.13.2（patch，缺陷修复；不改工具暴露面）。

### v5.13.1 — 规则层审计接入 whitelist 目录排除（备份目录不再污染编程规则）

> 承接外部测试/用户反馈（未解决残留）：单独跑 gov 治理维度扫描，`_refactor_backup` 备份目录仍占 PITFALL-01 空异常（51 处约一半）、IRON-ARCH-01 层级穿透（20 处 10 处）、XSS/命令注入/明文密钥等 HIGH 项的大头。根因：whitelist 的 dir 目录排除此前只作用于**知识图谱符号级分析**（真身/循环/重复匹配）与 governance 的 secret/doc 扫描，而 `audit()` 使用的 `analysis.files`（`CodeAnalyzer.analyze_project` 返回）不读该排除，PITFALL/security/quality/architecture 编程规则全部落到备份目录——「图谱干净 ≠ 审计干净」，两套机制分离。
> - **fix（规则层接入排除）**：`governance_audit.audit()` 在基础分析后统一接入 `_apply_whitelist_exclude(analysis, project_path)`——复用 `_whitelist_exclude_dirs`（读 whitelist 的 `dir` 条目）+ 图谱 `_is_excluded_path`（目录名任意层级匹配）同一排除口径，备份/镜像目录的代码文件不再进入任何编程规则检测；同步重算 `analysis.files` / `total_files` / `total_lines`，报告「扫描范围」与审计结果口径一致，避免健康分惩罚与文件数自相矛盾。一处过滤即覆盖 security/pitfall/quality/architecture 全部规则（逐文件与跨文件分析均遍历 `analysis.files`）。
> - **fix（Python 3.10/3.11 兼容性，顺带）**：`core/project_overview.py` 两处 f-string 表达式含反斜杠（`re.sub(r'^\s*[-*+]\s+', ...)` 中的 `\s`）与转义引号（`'<p style=\"...\">'`），Python 3.12+ 才合法——在 3.10/3.11 下 `gov_report(action=overview)` 整模块 import 即 SyntaxError、功能不可用，与 README「Python 3.10+」承诺冲突；改为表达式外部计算（先 `re.sub` 再入 f-string），恢复 3.10–3.14 兼容。
> - **回归测试**：`tests/test_feedback_fixes.py` 新增 `RuleAuditWhitelistExcludeTest` 4 用例——helper 无排除原样保留 / 备份文件剔除 + total 重算 / 缺省注入读 whitelist / `audit()` 端到端（临时项目含 `_refactor_backup` 违规文件、whitelist 登记排除后报告零备份路径），全量 143 用例通过（Python 3.10）。
> - **版本号**：5.13.0 → 5.13.1（patch，缺陷修复；不改工具暴露面）。

### v5.13.0 — 新增项目总览报告（架构图/Wiki/人话解读/治理工作项一屏收拢）

> 承接外部测试反馈：治理看板 gov_board.html「什么有价值的信息都没有」，追问确认三大高价值产物（架构图、项目 wiki、人话解读）均为旁立岛屿、从未聚入任何综合报告。新增 `gov_report(action=overview)` 项目总览，五区块一屏收拢：
> - **① 一句话体检结论（顶栏大字）**：健康分 + 高危/中危/低危计数 + 在途/已豁免 + 当前治理周期结论（对齐度/管线合规/差距收敛）——此前这些结论压在 cycle description 里从不见面，现直出到 KPI。
> - **② 项目架构图**：`<iframe>` 引用已生成的 `arch_canvas_*.html`（可拖拽/导出）；未生成则诚实占位「需先运行 coderef_arch_canvas」，不臆造。
> - **③ 项目 Wiki 介绍**：内联 `WIKI_INDEX`/`OVERVIEW`/`README` 核心文档 + 全库相对链接；新增轻量 Markdown→HTML 渲染（跳过 YAML front matter、表格、代码块、行内加粗/链接），file:// 直接可开可点。
> - **④ 人话解读摘要**：高危清单 + 分项计数 + 确定性总结，直接复用 `interpretation_platform`（无审计数据则诚实显示「未审计 ≠ 无风险」并给确定性背景）。
> - **⑤ 治理工作项**：表格含完整「标题」列；每个工作项的差距快照(snapshot) + 活动日志(events) 直接内联 `window.__DATA__`，展开行不做 fetch——根治 gov_board 静态打开时详情必失败的缺陷；无服务环境流转按钮降级为只读提示。
> - **聚合形态（静态自包含）**：主体单文件 HTML + 内联 CSS/JS，零 CDN；健康结论/Wiki/人话解读/工作项全部内联，file:// 直接可开；架构图区块以 `<iframe>` 引用同目录 `arch_canvas_*.html`（画布为独立交互产物，非单文件内联）；缺省落盘 `<project>/.coderef/project_overview.html`；`interactive=False` 或缺省只读；`open_server=true` 起本地服务时流转写回治理库。
> - **回归测试**：`tests/test_project_overview.py` 新增 9 用例（md 转换 front matter 跳过/表格/行内加粗链接/链接 scheme 白名单/代码块 pre/wiki 相对链接跨盘回退 + render_overview 聚合分区与架构图诚实占位 + interactive=False 只读降级），全量 138 用例通过。
> - **版本号**：5.12.9 → 5.13.0（minor，新增项目总览报告 feature；不改既有工具暴露面，扩展 `gov_report` 的 `action=overview` 分支）。

### v5.12.9 — 外部用户反馈驱动的可用性修复

> 承接外部用户试用反馈（《CodeRef-缺陷反馈稿》试用轮）：innovation_review/interpret 无 LLM Key 时卡 pending 空转 / _refactor_backup 备份目录污染 adopters 列表与 wiki，2 项修复：
> - **无有效 LLM Key 快速阻断（卡 pending 空转）**：`LLMIntegration.is_available()` 不再把占位符/示例 Key（ollama、sk-xxx、your-api-key 等）误判为可用——此前只要 client 初始化成功（OpenAI SDK 构造不校验 Key 有效性）即返回 True，导致依赖 LLM 的入口（innovation_review / interpret / wiki / business_report）在无有效凭据时仍发起真实请求，网络不通时空转 120s。修复：`_is_placeholder_key` 识别占位符/示例 Key；`chat_completion` 对占位符 Key 直接返回结构化错误、不发起请求；客户端超时从 120s 收紧为 `httpx.Timeout(60.0, connect=10.0)`（连接 10s 快速失败 + 总 60s 给足生成），网络不通时快速返回确定性摘要并明确告知"需配置有效 API Key"。
> - **whitelist dir 排除覆盖全校工具（备份目录污染产物）**：此前 whitelist 的 `dir` 排除只在 arch_* 系列生效，gov_*、innovation、wiki 生成仍用硬编码 EXCLUDE_DIRS，`_refactor_backup` 备份目录噪声进入 adopters 列表与 wiki 文档。修复：`_is_excluded_path` 增强为「目录名任意层级匹配」（排除 `_refactor_backup` 时，`core/_refactor_backup` 同样命中，向后兼容原路径前缀语义）；`innovation_propagation_detector._collect_signatures`、`wiki_generator._discover_modules/_collect_py_files`、`governance_audit` 三处扫描统一接入 `_whitelist_exclude_dirs` + `_is_wl_excluded`，与 arch_* 共享同一排除口径。
> - **回归测试**：`tests/test_feedback_fixes.py` 新增 8 用例（`LLMPlaceholderKeyTest` 占位符判定 / is_available 误判阻断 / chat_completion 不发起请求 / `WhitelistExcludeAllToolsTest` 目录名任意层级匹配 + wiki/gov/innovation 三工具同口径），全量 127 用例通过。
> - **版本号**：5.12.8 → 5.12.9（patch，反馈驱动修复，不改工具暴露面）。

### v5.12.8 — 外部用户试用反馈驱动的治理精度修复

> 承接外部用户试用反馈（《CodeRef-缺陷反馈稿》试用轮）：gov_start 不吃 whitelist 目录排除（备份噪声在治理工作项重出现）/ flow_verify 对 self.method() 调用边覆盖不全，2 项修复：
> - **whitelist 目录排除实时生效（gov_start 不吃排除）**：`coderef_arch_gap` 的 `analyze_gap` 新增 `_whitelist_exclude_dirs` 动态读取 whitelist 的 `dir` 条目，加载图谱后经 `filter_excluded` 实时过滤排除目录下的节点与 CALLS 边——即使图谱是 whitelist 配置前构建的旧图（不重建），备份/镜像目录噪声也不再重入治理工作项（unassigned / duplicate / 架构诊断差距）。`filter_excluded` 为公共函数（`core/graph_closure.py`），`arch_audit.audit` 与 `arch_insight.duplicate_insight` 同步新增 `exclude_dirs` 参数复用同一过滤语义，保证治理工作项、架构诊断、重复识别三处口径一致。
> - **self.method() 调用边落到方法节点（flow_verify 覆盖不全）**：`_find_containing_node` 查询节点时按类型优先级排序（method 优先于 function）——CodeAnalyzer 会把类方法同时注册为 `func:<mod>:<短名>` 与 `method:<mod>:<类>.<方法>` 两个节点（行区间相同），此前命中 func 节点导致 self.method() 调用边建到 func 节点而非 method 节点，`coderef_flow_verify` 以 method 为入口时下游闭包断裂。同区间优先 method 后，调用边正确关联到方法节点，method 入口下游闭包完整。
> - **回归测试**：`tests/test_feedback_fixes.py` 新增 17 用例（`IsExcludedPathTest` 路径判定含前缀边界防误伤 / `FilterExcludedTest` 节点与 CALLS 边实时过滤 / `WhitelistExcludeDirsTest` whitelist dir 条目读取与异常降级 / `FindContainingNodeTest` 同区间 method 优先 + function/class 回退 + 无命中返回 None），全量 119 用例通过。
> - **版本号**：5.12.7 → 5.12.8（patch，反馈驱动修复，不改工具暴露面）。

### v5.12.7 — 新增轻量版本探针 coderef_version

> 承接测试方建议课题（登记册 §4）：靠「结果字段反推版本」会因进程未重启加载旧代码而误判进程新旧（v5.12.6 首探即因此误判），需一行调用即可断言 版本==target。
> - **新增 `coderef_version` 只读工具**：返回当前进程加载的版本号（`version`）+ 工具名（`name`）+ 状态，零副作用、不触发任何扫描/图谱构建、无需 project_path，秒级返回。工具总数 50 → 51。
> - **版本号**：5.12.6 → 5.12.7（patch，新增只读探针，不改既有工具暴露面）。

### v5.12.6 — 外部用户反馈驱动的判定精度修复

> 承接外部用户第二轮反馈（《CodeRef-缺陷反馈稿_v1》）：同名方法簇相似度被夸大误报为"真重复" / cycle 循环依赖判定过粗无法判断真伪，2 项修复：
> - **同名方法按宿主类 + 契约区分**：`duplicate` 匹配不再仅按方法短名聚合——method 保留宿主类全名（`类名.方法名`，如 `DeepSeekClient.chat` vs `DiscussionEngine.chat`），新增 `_contract_compatible` 契约兼容判断（参数列表 + 返回类型，`self` 排除、空返回不阻断），契约不兼容的副本降为"同名候选"（kind=candidate，标注"仅同名、契约不同"）；新增短方法体过滤（归一化后 < 20 字符不参与相似度聚类，避免 `return x` vs `return y` 短文本 bigram 虚高）。duplicate 报告新增"契约（签名 → 返回）"列，便于快速过滤。
> - **cycle 输出最小真环 + 关键逆向边**：`coderef_arch_audit` 对每个模块级 SCC 新增 `cycle_details`——BFS 提取最小真环路径（`min_cycle`，模块名序列首尾相同，替代超长无序列表）、环上具体边清单（`key_edges`，起点/终点 + 跨层逆向标注，与 `layer_viol` 同口径：低层依赖上层才标 reverse）、大环提示（`hint_large_scc`，SCC 节点数 > 12 时提示"整个子图被圈为强连通分量"而非局部循环）；`coderef_arch_gap` 的 cycle 差距展示同步带出最小环与关键边。
> - **版本号**：5.12.5 → 5.12.6（patch，反馈驱动修复，不改工具暴露面）。

### v5.12.5 — 外部用户反馈驱动的可用性修复

> 承接外部用户使用反馈（备份目录污染符号级判定 / target_modules 语义坑 / 异步轮询繁琐 / 大输出转义膨胀），4 项修复：
> - **目录级排除**：`coderef_whitelist` 支持 `dir` 字段（目录相对路径），白名单目录不进入知识图谱符号级分析（真身判定/循环/重复匹配），备份/镜像目录不再污染生产代码判定。`_build_kg` 读取白名单 dir 条目传给图谱构建，`CodeKnowledgeGraph.build` 新增 `exclude_dirs` 参数（analysis/ast/go 三路过滤）。
> - **target_modules 目录展开**：`coderef_arch_gap` 的 `_match_module_ids` 支持目录路径 spec 自动展开为该目录下全部模块（含子目录），不再因按目录写 target_modules 而误判游离。
> - **wait 阻塞等待**：重型工具支持 `wait=true` 阻塞等待任务完成直接返回最终结果，免去手动轮询 `coderef_task_status`（`_call` 后台化分支增强）。
> - **大结果落盘**：超过 10KB 的 JSON 结果自动落盘到 `cache/mcp_out/result_<hash>.json` 并返回文件路径+摘要，避免 MCP text 转义膨胀与超 64KB 限制（`_ok` 增强）。
> - **CodeRabbit 复审收尾**（10 项 finding）：`dir` 白名单保留原始大小写（Linux 路径匹配）；`target_modules` 目录展开建 `by_dir` 前缀索引（O(1) 查索引 + 文件 spec 不误配子路径）；`coderef_docs_read` 大正文豁免落盘；`wait=true` 阻塞等待加 300s 有界超时（超时返回 task_id 供轮询）；知识图谱排除目录语义统一到 `_collect_analysis_ids` 与 GitNexus 引用（排除目录下模块/类 id 不进入 IMPORTS/INHERITS 目标集合）；重型工具 schema 统一暴露 `wait` 参数；大结果落盘加 TTL/数量上限 + 原子替换；排除目录判定保留点目录名（`.cache`/`.generated` 不被 `lstrip("./")` 误剥）。
> - **版本号**：5.12.4 → 5.12.5（patch，反馈驱动修复，不改工具暴露面）。

### v5.12.4 — 治理断链 + 治理状态目录隔离

> - **断链**：`health_dashboard` 对 `pipeline_runner` 的顶层运行时依赖改为 TYPE_CHECKING 类型引用 + 方法内延迟导入，消除模块级循环依赖（AST 级真实 import 扫描验证 core 层模块级依赖环归零）。
> - **隔离**：`.gitignore` 新增 `.coderef/` 治理状态目录（含 governance.db / target_arch.json 等内部过程资料，不入库）。
> - **版本号**：5.12.3 → 5.12.4（patch，治理改动，不改工具暴露面）。

### v5.12.3 — 合并操作记忆层 6 工具 → coderef_operation_memory

> 操作记忆层工具收敛（OperationMemory 6→1）：
> - **合并**：`coderef_operation_memory_sync/query/find/status/recover/export` 6 工具 → 单一 `coderef_operation_memory`，
>   以 `action=sync/query/find/status/recover/export` 区分；handler 与注册表 6→1，核心模块（operation_memory.py）**零改动**，
>   ledger.json / BRAIN.md 产物路径不变。
> - **删旧名不保留别名**：旧 6 名从 tools/list 移除，既有调用须改用 `coderef_operation_memory(action=...)`；工具总数 55 → 50。
> - **后台化矩阵不变**：`MERGE_SYNC_ACTIONS` 确保 action=query/find/status/recover/export 保持同步
>   （尤其 recover 需即时返回），action=sync 保持后台；行为与合并前完全一致。
> - **自证**：冒烟全绿——工具数 50、旧名 100% 消失、`Server()` 初始化正常、`_should_background` 矩阵 10 断言、
>   6 action 全通 + ledger.json / BRAIN.md 落盘断言。
> - **版本号**：5.12.2 → 5.12.3（patch，暴露面精简，不改操作记忆能力）。

> **后续修订（2026-08-30）**：
> - **旧名文案残留清零**：`operation_memory.py` / `memory_layer.py` / `memory_quality.py` 的错误提示与 docstring
>   统一指向 `coderef_operation_memory(action=...)` / `coderef_memory(action=...)` 新命名（纯文案零逻辑）。
> - **记忆库落点约定**：认知记忆 `data/memory_state/{项目hash}.json/.kb.db`、操作记忆 `data/operation_memory/<项目hash>/`
>   （ledger + BRAIN + timeline），均按项目 hash 隔离、`data/` git 不追踪、删对应 hash 即可清理。
> - **语义检索降级体验**：Ollama 未就绪时 `coderef_memory(action=query, query_type=semantic)` 自动降级关键词检索
>   （停用词过滤 + 中文 bigram 召回增强、打分封顶 1.0），并返回 `engine` / `degraded` 标记，不再返空。
> - **验证**：全量回归通过，无阻断缺陷。

### v5.12.2 — 合并记忆层 4 工具 → coderef_memory

> 记忆层工具收敛（MemoryLayer 4→1）：
> - **合并**：`coderef_memory_sync/query/status/quality` 4 工具 → 单一 `coderef_memory`，以 `action=sync/query/status/quality` 区分；
>   handler 与注册表 4→1，核心模块（memory_layer.py / memory_quality.py）**零改动**，SQLite 图谱 / 向量库 / 产物路径不变。
> - **删旧名不保留别名**（与 A 方案刻意不同）：旧 4 名从 tools/list 移除，既有调用须改用 `coderef_memory(action=...)`；
>   工具总数 58 → 55。
> - **后台化矩阵不变**：新增 `MERGE_SYNC_ACTIONS` 使 action=query 保持同步（秒级），action=sync/status/quality 保持后台
>   （全量扫描撞超时教训）；`background=true/false` 仍可显式覆盖；行为与合并前完全一致。
> - **自证**：冒烟全绿——工具数 55、旧名 100% 消失、`Server()` 初始化正常、`_should_background` 矩阵 9 断言、
>   sync/query/status/quality 4 action 全通。
> - **版本号**：5.12.1 → 5.12.2（patch，暴露面精简，不改记忆能力）。

### v5.12.1 — 合并 gov_report + gov_board（报表视图级收敛）

> 工具收敛方案 A（报表视图级合并，B 单独排期）：
> - **合并**：`coderef_gov_report` 新增 `action=report/board` 参数——action=report（默认）单期 + 跨期趋势报告（JSON/HTML）；
>   action=board 交互 HTML 看板（缺省落盘 `<project>/.coderef/gov_board.html` 并返回确切路径，
>   interactive=false 只读、open_server 可选起本地服务）。
> - **兼容别名**：`coderef_gov_board` 保留为兼容别名（转发 action=board，行为不变），既有调用零断链；
>   工具总数仍 58（未删工具名）。
> - **自证**：冒烟验证 4 路径全绿——action=report JSON/HTML、action=board 落盘断言、
>   兼容别名转发 + 落盘；`Server()` 初始化正常。
> - **版本号**：5.12.0 → 5.12.1（patch，报表视图级合并 + 兼容别名，不改治理能力）。

### v5.12.0 — 分层治理编排层补齐：L3 资产沉淀编排（coderef-asset）

> 治理体系编排层落地路线 P3（L3 资产沉淀编排）：
> - **P3 L3 资产沉淀编排**（新建 `skills/coderef-asset/SKILL.md`，零新工具纯文档编排）：
>   把治理/开发产出的高价值设计固化为可复用资产并复刻到新项目——
>   **沉淀链**「`coderef_innovation` 识别 → `coderef_innovation_review` 确认（LLM 排查真创新/
>   管线-wiki 一致性/复刻合理性）→ `coderef_registry` 登记归一（alias→canonical，防命名漂移）→
>   `coderef_asset` commit 固化（≥2 workflow 采用 + evidence 防污染）→ `coderef_asset_blueprint`
>   补全蓝图（entry_points/verified_findings）→ `coderef_interpret` 人话解读」；
>   **复用链**「`coderef_replicate` 铺排（确定性缺口，人拍板）→ `coderef_replicate_apply`
>   落地骨架（不自动改源码、冲突默认不覆盖）→ 回 L1/L2 验证」。治理成果 → 资产 → 新项目
>   沉淀复用闭环。
> - **L1/L2 衔接**：`coderef-governance` 加 L3 衔接（③ 抽公共工具、⑤ 体检高价值设计 → 沉淀；
>   意图路由表加资产沉淀路由）；`coderef-probe` 加 L1→L3 衔接（探查发现多 workflow 采用设计 → 沉淀候选）。
> - **编排结构**：L0 工具层（58 个）→ L1 小阶段治理（coderef-probe，变更驱动）→ L2 大阶段治理
>   （coderef-governance，周期驱动）→ **L3 资产沉淀（coderef-asset，治理成果→资产）**。
> - **自证**：三 SKILL 引用的全部工具与 `mcp_server.py` 注册清单交叉核对通过（asset 8 个均注册，
>   无失效引用）；frontmatter 可解析；工具数不变（58）。
> - **版本号**：5.11.0 → 5.12.0（minor，补齐 L3 资产沉淀编排层）。

### v5.11.0 — 分层治理编排层落地：L2 完整化（收编执行增强层）+ L1 小阶段治理编排（coderef-probe）

> 治理体系编排层落地路线分两阶段：
> - **阶段一：L2 主链完整化**（`skills/coderef-governance/SKILL.md`）：收编执行增强层进主链——
>   ② define-target 引 `coderef_role_boundary`（符号级职责越界，与模块级差距互补）、
>   ③ refactor-along 引 `coderef_refactor_plan`（差距→任务卡）+ `coderef_gov_pipeline`
>   （治理自动化流水线：Fixing→任务卡→复验→Verified 半自动闭环）、④ verify-advance 引
>   `coderef_arch_verify`（0-100 对齐度后验：职责对齐40%+依赖健康30%+业务覆盖20%+代码健康10%）。
>   整改环节从「人工逐条手工改 + arch_gap 复查」升级为「gov_pipeline 半自动整改闭环 +
>   人确认），架构方向决策仍由人确认（工具只做机械性归属动作）。主链工具 21 → 25。
> - **阶段二：L1 小阶段治理编排**（新建 `skills/coderef-probe/SKILL.md`，零新工具纯文档编排）：
>   变更驱动的探查链「触发→策略路由→增量探查→确定性核验→变更防护→降噪→登记/升级」——
>   `gov_schedule`（定时触发）/ git hook（变更触发）→ `audit_advisor`（增量/全量路由）→
>   `scan`/`audit`（增量探查）→ `verify_findings`（确定性核验 LLM/CodeRabbit 论断）→
>   `change_guard`+`change_report`（变更防护）→ `whitelist`（降噪）→ `gov_*`（登记/升级 L2）。
>   闭环判定：增量回归=0（change_guard 无退化 + flow_verify 无 outside 新增）+ 白名单收敛。
>   与 CodeRabbit 边界（编排定位已定）：Coderef 自建完整探查链、不集成 CodeRabbit 编排；
>   `verify_findings` 仍可核验 CodeRabbit 论断（确定性核验差异化优势）。
> - **编排结构**：L0 工具层（58 个）→ L1 小阶段治理（coderef-probe，变更驱动轻量）→
>   L2 大阶段治理（coderef-governance，周期驱动五阶段）→ L3 资产沉淀（展望）。
> - **自证**：两 SKILL 引用的全部工具与 `mcp_server.py` 注册清单交叉核对通过（governance 25 /
>   probe 9，无失效引用）；frontmatter 可解析；五阶段工具数 ② 2→3、③ 2→4、④ 3→4。
> - **版本号**：5.10.0 → 5.11.0（minor，新增 L1 编排层 + L2 完整化）。

### v5.10.0 — 游离一键纳入 + flow_verify 入口跨语言消歧

> 游离模块一键纳入 + flow_verify 入口跨语言消歧：
> - **新增 `coderef_target_adopt`（游离一键纳入）**（`core/arch_gap_analyzer.py` 新增
>   `adopt_free_modules` + `core/mcp_server.py` 注册）：把 `coderef_arch_gap` 报出的
>   **游离/未建模模块按角色批量追加 `target_modules`**，把「游离模块靠手工在
>   define-target 一条条补」的机械性归属动作工具化。游离口径与 arch_gap 完全一致
>   （复用 `_detect_unassigned`）：`free`=真游离孤儿（fan_in=0）、`unmodeled`=被调用
>   但未建模（含已知入口脚本——CLI/命令入口 fan_in=0 系程序启动点，归入未建模而非真游离）。`role_id` 指定纳入角色（缺省第一个 tech_role）、`modules` 指定纳入模块
>   （缺省按口径取全部）、`monitored=free|all` 控制纳入口径、`dry_run=true` 只预览
>   不落盘；纳入后自动按写入后架构重评估剩余游离/未建模数。幂等：已纳入模块跳过不重复追加。
> - **flow_verify 入口跨语言消歧**（`core/flow_verify.py`）：`find_entry` 限定前缀匹配
>   由**子串包含**改为**文件路径段连续子序列精确匹配**（新增 `_path_seqs`），并把
>   `go_func` 加入优先匹配类型——解决混合语言项目「项目根目录名命中所有
>   Python 同名 `main`」的跨语言歧义（此前误导向 Python `main`，现精确命中 Go 入口）。
> - **自证**：混合语言测试项目端到端 **17 项 PASS**——flow_verify `program.Run`→Go `Run` 正确命中；adopt_free_modules 非法角色报错 / dry_run
>   free 纳入 163 模块 / dry_run all 纳入 439 / 指定 modules 精确纳入 / 指定 role_id /
>   幂等跳过 / 真实落盘后架构仍合法且 remaining_free 正确更新；既有 86 项 unittest 全过
>   （无回归）；`py_compile` 全过。
> - **CodeRabbit 评审修订（2 major + 3 minor，二次提交）**：① major `find_entry`
>   支持 **模块.类.方法** 限定——类名先与候选节点限定名后缀对齐扣除，剩余前导段才做
>   file_path 路径段匹配（原逻辑把类名误当路径段，`pipeline_runner.Pipe.run` 无法解析）；
>   ② major `adopt_free_modules` 显式 `modules=[]` 视为不纳入（原逻辑与缺省混同会全量
>   纳入）；③ minor `monitored` 校验 enum（free/all，非法值报错，schema 同步加 enum）；
>   ④ minor `dry_run` 校验为真布尔（拒绝 `"false"` 字符串）；⑤ minor README 补
>   unmodeled 含已知入口脚本。修复后自测 **14 项 PASS**（新增 模块.类.方法 → method 节点 /
>   Go `Receiver.Method` 限定 / modules=[] no-op / monitored 非法值 / dry_run 非布尔，
>   原场景无劣化）+ 86 项 unittest 全过。
> - **版本号**：5.9.0 → 5.10.0（minor，新增工具功能）。

### v5.9.0 — 双真身语义分层：平行管线/设计并存不机械收敛

> **双真身是项目的特别设计，架构探测此前没有捋清楚**。在测试项目复现 15 项 duplicate 全清单，全部集中在
> `alone_doc/doc-to-skill/scripts` 与 `alone_web/web-to-skill/scripts` 两条**平行技能生成
> 管线**（文档转技能 vs 网页转技能，有意并存的产品线）之间——探针此前在函数粒度统一报
> "跨目录重复、建议收敛"，把「管线级设计并存」与「函数级复制粘贴」混为一个信号，机械
> 收敛最危险是误合并平行管线、破坏设计。本版为 duplicate 判定增加**语义分层**。
> - **`duplicate_insight` 新增 `semantic_kind` 分类字段**（`core/arch_insight.py`）：
>   - `designed_parallel`（设计并存）：副本目录在**共同分支点后结构对称**（分支后层级数
>     相同、分支名不同），判为同一设计模板的平行实例，如两条技能生成管线；
>     带 `_DEAD_DIR_HINTS` 黑名单（legacy/old/bak/backup/archive/deprecated/_v1 等），
>     废弃/备份目录的复制**不**判设计并存，防死复制误判。
>   - `true_duplicate`（真重复）：非对称结构的跨目录同构实现，维持收敛/抽公共建议。
> - **`arch_gap` duplicate 差距 detail 按分类差异化建议**（`core/arch_gap_analyzer.py`）：
>   设计并存 → "保留（不建议收敛，抽公共工具可选）"；真重复 → "建议收敛/抽公共工具"。
>   `_duplicate_markdown`（重复识别报告）同步分组展示「真重复」与「平行管线/设计并存」两小节。
> - **自证**：测试项目 15 项 duplicate **全部判为设计并存**（符合"平行管线保留"
>   预期，不再一刀切建议收敛）；`_is_parallel_structure` 单测 **9 项 PASS**（根级分叉 /
>   共同分支对称 / 分支后层级不同 / 废弃目录复制 / 分支名废弃提示 / 大小写不敏感等边界）；
>   `py_compile` 通过。
> - **CodeRabbit 评审修订（2 major，二次提交）**：① `_is_parallel_structure` 允许
>   **共同前缀为 0**——平行管线可从项目根直接分叉（如 `alone_doc/...` vs `alone_web/...`），
>   原 `common < 1` 硬性拒绝会漏判根级分叉的设计并存；② 废弃目录黑名单检查由"仅分支名"
>   扩展为**分支名及其后全部目录段**，且**大小写不敏感**——`products/active/scripts/archive/`
>   这类"分支下归档副本"不再误判设计并存。自测升级 9 项 PASS（新增根级分叉 True /
>   分支后含 archive False / Archive 大写 False / 分支后含 legacy False），测试项目
>   15 项 duplicate 复跑仍全判设计并存（**无劣化**）；`py_compile` 过。
> - **版本号**：5.8.1 → 5.9.0（minor，新功能语义分层）。

### v5.8.1 — 设计注册表可回收：coderef_registry 新增 delete

> 设计注册表此前仅 add/alias/list，多 agent 在任意项目 `registry add` 会永久写入全局共享池
> 且不可自动回收。本版新增 `delete` 动作，让来源项目可回收自己注册的设计，同时保留
> "全局共享 + source_project 溯源"的跨项目设计复用设计意图（清单隔离成立，唯一共享面=设计注册表）。
> - **`coderef_registry` 新增 `delete` 动作（`core/design_registry.py` + `core/mcp_server.py`）**：
>   `name` 支持 canonical 或别名（自动归一化）；删除设计时**同步清理资产区同名资产**，
>   避免设计删除后资产残留。来源防护两级：① `source_project` 为空（预置种子/未标注来源）的
>   基础设计**不可删除**，保护通用设计库；② `source_project` 与当前 `project_path` 不一致的
>   条目**不可删除**，提示"请在来源项目下删除"，防止 A 项目误删 B 项目注册的哨兵。
> - **CodeRabbit 评审修订（2 major + 复审 1 critical/1 major）**：
>   ① delete 改为「恰好一个 canonical/别名匹配才可删」（`resolve()` 只取首个匹配在共享
>   别名时会删错），歧义别名拒绝删除；② 注册表变更操作（add/alias/delete/add_asset）
>   加**跨实例写事务锁** `_synchronized`（持锁 + 变更前重载最新磁盘状态），防多实例
>   「陈旧快照覆盖」把已删除的设计恢复回来/丢弃他实例更新；③ **复审 Critical——`setup.bat`
>   命令注入**：原 `set /p` 输入被直接内插进 `python -c` 源码，恶意输入
>   （如 `x');import os;os.system('calc');#`）可执行任意代码；已新建 **`core/cli.py`**
>   （参数经 `sys.argv` 传入、只当数据不当代码编译），`setup.bat` 全部改调
>   `python -m core.cli ...`，注入 payload 实测仅作为普通设计名存储、不执行；④ **复审
>   Major——跨进程写锁**：`setup.bat` 每个动作是独立 `python -c` 进程，进程内 RLock 盖不住
>   多进程并发；升级为「进程内 RLock + 跨进程文件锁（`msvcrt.locking`/`fcntl.flock`）」
>   并让构造/种子初始化共用同一事务边界，实测双进程并发 add 全落盘无丢失。
> - **`setup.bat` 菜单扩展**：新增「5 设计注册表管理（list/add/alias/delete）」「6 激活治理
>   看板前端（本地服务，回环 127.0.0.1，复用 `gov_webdash.serve`）」「7 生成并打开架构画布」，
>   注册表 delete 带来源校验与二次确认；原退出项顺延为 8。
> - **自证**：临时注册表全流程 8 项 PASS（add→delete 同项目闭环、他项目条目防护拒绝、
>   预置种子保护、资产同步清理、唯一别名归一化删除、删除不存在报错、歧义别名拒绝、8 线程
>   并发 add 全落盘无丢失）；`py_compile` 通过；`setup.bat` 三入口在真实环境实测（reg
>   list/add/delete、看板 HTTP 200、画布生成 44KB HTML）。
> - **CodeRabbit 终审修订（2 major）**：⑤ `core/cli.py` 项目身份校验——`argv` 传入的
>   `project_path` 被当作 delete 的所有权证明不可靠（从项目 A 传项目 B 路径即可绕过来源
>   防护）；修复为从**可信执行上下文（cwd）**派生项目身份 `_require_owned_project`，
>   add/alias/delete 变更操作仅允许作用于当前所在项目，路径不一致即拒绝；⑥
>   `core/design_registry.py` 锁文件父目录——`_file_lock` 打开 `.lock` 前未确保父目录存在，
>   新路径注册表初始化会抛 FileNotFoundError；修复为打开锁前
>   `os.makedirs(dirname, exist_ok=True)`。
> - **版本号**：5.8.0 → 5.8.1（patch，可回收补全）。

### v5.8.0 — 业务层表达力扩展：阶段分组 × 子模块/适配器矩阵 × 分支回环

> 画布/目标架构对"多阶段流程 × 模块/适配器矩阵"复杂架构呈现清晰度不足：
> 真实流程是 8 阶段、阶段内含子模块矩阵、含众多分支回环决策点，而原
> `business_flows.steps` 仅 `{id,name}`，无法承载"阶段×子项矩阵 + 回环"这类高密度业务架构。
> 本版把业务层表达力从"线性 step"扩展为"阶段分组 + 成员矩阵 + 条件回环"，让画布能呈现
> 真实架构主干而非 4 步粗主干。
> - **schema 扩展（`core/target_arch_schema.py`）**：`steps[].kind`（`phase` 阶段/普通 step）、
>   `sub_module_refs`（阶段→子模块/适配器成员挂载，成员含 `module`/`role`/`alias`/`kind`/
>   `note`）、`branches`（step→step 条件/回环边，含 `to`/`type:loop|if|fallback`/`condition`）。
>   `sub_module_refs` 预留 `group` 嵌套槽位（子分组演进接口）；全部可选，`REQUIRED_STEP_KEYS`
>   保持 `{id,name}` 不动 → 旧 target_arch 零新增错误、向后兼容。
> - **画布渲染（`core/canvas_generator.py`）**：`kind=="phase"` 渲染为阶段分组（🎯 + 阶段序号
>   徽章），与普通 step（📈）视觉区分；`sub_module_refs` 成员沿所属阶段拉进可视图——命中图谱
>   模块直连、图谱无独立节点的适配器类补"成员占位"节点强制纳入（消解"5 个搜索 Adapter 命中 0"）；
>   `branches` 绘制粉色虚线回环/条件边（label=condition）。图例新增"阶段成员挂载/分支回环"。
> - **business_gap 成员断链（`core/arch_gap_analyzer.py`）**：`_detect_business_gaps` 新增可选
>   `member_resolved` 参数，主流程解析 `sub_module_refs` 成员是否实现，阶段声明成员全无实现时
>   追加"阶段→实现断链"提示（`member_missing`），不只是角色级断链。
> - **自证与回归**：合成验证 8 项全 PASS（schema 合法/非法校验、阶段分组节点、成员占位、
>   分支回环边、成员挂载边、成员断链）；旧 target_arch 渲染路径不变，既有画布/目标架构
>   场景不劣化。
> - **CodeRabbit 评审修订（1 项 critical + 2 项 major + 1 项 minor）**：
>   `arch_gap_analyzer.py` 主流程与 `_detect_business_gaps` 统一解析 `group.items[].module`
>   并改用 `_module_exists`（文件系统+图谱双口径）判定成员实现；`target_arch_schema.py` 分支
>   后置校验防护非 dict step（防 AttributeError 崩溃）；`canvas_generator.py` 预收集步骤节点 id，
>   过滤指向不存在步骤的分支边。修复后合成验证 8 项全 PASS、`py_compile` 全过。
> - **O-C3 模块边界口径（`core/arch_audit.py`）**：同顶层父包的子包互引（如
>   `route/gin↔route/client_side↔route/chat_claw`）在业务上属同一模块/层内部的组件纠缠，而非
>   "跨模块"真环。`audit()` 现按各 SCC 成员顶层父包集合分拣：纯同父包环 → 转 `package_cycles`
>   （包内子组件环，单独透出、不计入 health 扣分）；跨顶层包真环仍保留在 `cycles` 照常扣分，
>   保留真实耦合信息、对齐 LLM"包内循环"认知。
> - **入口/基础设施判据去 overfit（`core/business_analyzer.py`）**：入口/基础设施模块判据由硬编码
>   比值（此前为避单一项目特例 1.5→2.0 收死）改为可配置常量
>   `ENTRY_DEGREE_RATIO/INFRA_DEGREE_RATIO/INFRA_MIN_DEGREE`（默认 2.0/2.0/2，即"出/入度
>   相差 2 倍以上即倾向该层"的通用显著失衡判据），`_hier_entry_modules/_hier_infra_modules`
>   可传参覆盖；注释不再引用单一项目业务名，消除对具体项目的过拟合。
> - **CodeRabbit 二轮修订（`arch_audit.py`，1 major + 1 minor）**：① **Major**——模块内自环
>   判定由"模块内任意符号调用"改为"按符号建模块内子图，仅当符号自递归或形成环(SCC≥2)才标
>   self_loop"，避免把同文件线性调用 a()→b()→c() 误报为模块自环；② **Minor**——no_code 判定
>   由 `len(nodes)==0` 改为"是否存在非 test 模块"，图谱只剩 test/tests 节点时不再误给满分 10.0
>   而判"无代码可评"。合成验证 9 项全 PASS、`py_compile` 过；`self_loops` 口径更精确（去掉线性误报）。
> - **role_boundary 输出面细化（`core/role_boundary.py`）**：① definition 越界仅报**顶层类/
>   函数**（方法名是行为描述如 `_llm_edit`，非职责单元声明，方法级撞词全部归入 `call_hints`
>   弱信号，消除"类名撞词→类+全部方法刷屏"，如 `CheckpointManager` 曾 9 连报）；② 修复
>   `role_matchable` 计算后未接入 `def_hits` 的缺陷——role_keywords 全为中文（无法 token 化
>   提供英文锚点）的角色不判 definition，消除 business 模块因 `engine`/`research` 等跨语言
>   撞词的整模块误报；③ `call_hints` 通道降权 `error`/`logging` 支撑词（异常/日志属通用机制，
>   纯支撑词命中不构成跨角色提示）；④ **CodeRabbit 复审 3 发现修订**——本角色泛词关键词
>   （service/manager 等）不再作为越界锚点（防 `PaymentService` 被泛词锚定压掉真实越界）、
>   命中关键词保留完整文本（`service_client`/`error_handler` 不再被误判为纯泛词/纯 error
>   支撑词而降权删除）、definition 与 call_hints 各自达到上限才停止扫描（上限互不压制）。
>   实测：boundary_issues 200→3（仅剩 service 层
>   重复实现 code 层 checkpoint/chart 能力的真信号），call_hints 187→133（纯 error 撞词
>   清零）；验收场景（waiter.cook→chef 越界）保持 PASS、本角色符号不误报。
>   **⑤ `package_cycles` 分拣口径修订**——由「顶层第一段」改为「直接父包」：
>   Go 标准布局下所有模块首段恒为 `internal`，原口径把 `business/manage/http_tools ↔
>   common/http_tools` 这类跨业务/公共层环误归「包内环」，修复后按完整父路径分拣
>   （实测 package_cycles 2→1：route 包内环保留、http_tools 环正确归跨包
>   cycles）；detect 初稿递归展开跳过依赖/产物目录（`_COMMON_DIRS` 白名单补
>   vendor/volumes/public + libs 内 npm 包子目录启发），初稿 target_modules
>   1000+→542（php/vendor、public/libs、logs、volumes 全清除，业务子仓不受影响）。
> - **版本号**：5.7.1 → 5.8.0（minor，业务层表达力扩展 + O-C3 口径 + 判据去 overfit）。

### v5.7.1 — 架构判据口径校准：tests 排除 + 基础设施层归属 + detect 粒度/role_boundary 泛词/入口游离

> 用独立 LLM 子代理与 coderef 全量探测正面对比发现，coderef 健康分系统性低于人判（-4~-6），偏离集中在少数
> 静态口径。本版校准：
> - **循环/规模/分层判定排除顶层 `tests/`、`test/` 目录**（`_is_test_path` 顶层片段
>   判定，不误杀 `src/utils` 等）。request 样例不再被 tests 边凑成 11 模块大环。
> - **新增"基础设施层"**（最低层 0，`ARCH_INFRA_DIRS` 配置 i18n/log/plugin/rpc 等），
>   "公共库依赖日志/国际化"这类合理依赖不再被判下层依赖上层，分层违例不再
>   被批量放大。
> - **detect 生成初稿**把 `target_modules` 展开到模块级与"目录前缀覆盖"匹配语义对齐，
>   消除"目录名 vs 子路径"粒度错位导致的初稿覆盖率极低。
> - **`_COMMON_DIRS`** 补充 `coderef-report`/`report`/`result`/`artifacts` 等输出制品目录，
>   不再把输出目录当业务模块。
> - **`role_boundary`** 关键词匹配由子串/前缀收敛为整词边界匹配 + 泛词（app/main/entry）
>   低置信降级，合理符号不再被泛词 `app` 误判越界；模板 `role_keywords` 与目录匹配词分离。
> - **`main_*`/`bin/cmd/cli`/`manage`/`__main__` 入口脚本**由"真游离 free"改为 `unmodeled`
>   （带 `entry` 标记），不再误导为需删除的危险游离物。
> **CodeRabbit 评审修订（2 轮 5 发现全修）**：一轮 3 发现——① `_match_module_ids` 含 `/` 的
>   模块级 spec（模板展开的 domain/models 等）改精确路径匹配，防 basename 误配无关 `other/models`；
>   ② `_detect_unassigned` 与孪生判定的 fan_in 均排除测试调用边（防生产入口标记丢失、仅被测试
>   引用的孤本被误判活跃副本、整组收敛候选被丢弃）；③ role_boundary 泛词命中的 `uncertainty`
>   保持 high（与"静态-only 即不可靠"语义一致，防消费方把泛词命中当更高可信）+ 新增独立
>   `confidence=low` 表达低置信。二轮 2 发现——基础设施层（最低层 0）进出双向豁免分层违例
>   （跨切面横切非腐化信号，反向依赖业务属正常装配）；模块匹配/缺失判定改为一次预建索引
>   O(nodes)，模板生成千级 spec 不再退化为 O(specs×nodes) 的逐 spec 全扫 relpath。
> - **版本号**：5.7.0 → 5.7.1（patch，架构判据口径校准 + CodeRabbit 两轮评审修订）

### v5.7.0 — 软件形态模板体系：无 target 也能生成目标架构初稿与整理建议

> 多案例回归发现"部分样本不能产出对的架构图"：无 `target_arch` 时画布
> 缺业务/技术层退化为单层模块图。本版新增软件形态模板体系作为降级适配——识别项目属于
> 哪种常见软件形态，按模板自动生成 `target_arch` 初稿 + 文件夹整理建议（引导非强求），
> 先让"非编程人员 + 编程 AI"有架子可调，再谈三层泳道图。

- **模板体系（新增 `core/arch_templates.py`）**：内置两种样板——`hexagonal` 六边形单体
  （业务核心 domain/use-cases 与外部技术 adapters/infrastructure 解耦，依赖向内）与
  `modular_monolith` 模块化单体（按业务域平铺模块 + 共享底座，模块间依赖受控）。每类模板含
  识别特征（目录/依赖关键词）、角色骨架、期望目录与整理建议。纯静态、确定性，不依赖 LLM。
- **define-target 模板初始化（`coderef_target_arch_set` 新增 `template`/`detect` 参数）**：
  不传 `target_arch` 时，`template=hexagonal|modular_monolith` 按模板结合项目实际顶层目录生成
  `target_arch` 初稿（tech_roles 自动匹配真实模块）；`detect=true` 自动识别项目类型并套用对应
  模板（detect 优先级低于 template）。初稿仅供参考，可在此基础上完善；识别到的模板在返回值
  `template` 字段说明。
- **arch_gap 模板整理建议（新增 `templating` 输出）**：每次 `coderef_arch_gap` 自动 detect
  项目形态，对比模板期望骨架输出整理建议（缺失目录 add_dir / 未落入角色目录 review_dir），
  "按期望骨架整理文件夹有助于生成目标架构；可自主决定是否照做"。
- **版本号**：5.6.7 → 5.7.0（minor，新增模板体系功能模块）

### v5.6.7 — arch_audit cycle 口径分流 + 去样例化残留清理

> 过拟合审计发现：多案例回归中 self
> 实测 `arch_audit=0.0`，暴露 cycle 口径把"模块内互调自环"计入循环依赖并压健康分，
> 对大型单体过度悲观。同步清理工具代码中全部单一项目特有业务名残留。

- **arch_audit 区分「模块间循环 vs 模块自环」（`core/arch_audit.py`）**：原 `self_edges`
  只要模块内存在任意符号级 CALLS 边（无需成环）就记录，单模块分量命中即判循环依赖并扣健康分
  ——模块内正常函数互调（如 `core/role_boundary` 无自引用 import 却成单元素 SCC）被误当架构腐化。
  修复：`cycles` 只保留模块间 SCC≥2 的真循环（架构腐化，照常扣分）；模块自环单独透出
  `self_loops` 字段与 `summary.self_loops` 计数（不扣健康分）。实证：高耦合单体（2272 节点）
  health 0.0→3.0（46 中 45 自环分流，core/* 24 模块真环保留）；requests 保持 2.0 不变
  （tests/src 真循环仍在，证明不误伤模块间真环）。
- **去样例化残留清理**：全量替换 LLM prompt、MCP schema description、
  SKILL、注释、docstring 中的单一项目特有业务名（调研工具/洞察工具/方案工具/配置中心/创意引擎等）
  与路径示例为中性占位（如 营销助手/业务工具.main/infra_layer）；
  工具本体不再含任何单项目命名残留。
- **版本号**：5.6.6 → 5.6.7（patch，复核 + 去样例化）

### v5.6.6 — target 与架构图真实化（覆盖引导/业务流建议/孪生真身孤本标注）

> coderef 自动生成的分层/目标架构与实际架构差异大
> （覆盖率仅 0.06、业务流只有 1 条调研 4 步、source_engine/调研工具双真身被画成平级真身未标孤本），
> 照这张图治理会漏掉真实主线（web 编排、洞察→方案→创意）。

- **define-target 覆盖引导（`coderef_target_arch_set` / `arch_gap`）**：业务流为空或不足 2 条、
  角色 `target_modules` 为空时显式提示（不阻断设置）；`arch_gap` 在 `module_assigned<0.3` 或业务流不足时
  输出 `coverage_guidance`，防治理建在残缺图上。
- **业务流校验/建议（`arch_gap` 新增 `domain_flow`）**：域间业务流量透视三层——`edges`（如实
  跨域调用含证据）、`hubs`（逐域结构角色：共享层/双向枢纽/被共同依赖/业务编排源…，全程无项目名）、
  `suggestions`（去掉共享层与叶子后的主干业务流，调用数 ≥3，并附具体调用证据）。共享层在"被
  ≥50% 源域引用"时自动识别（零项目名硬编码）；"谁是技术底座"属项目语义（真实项目中 gptr_service
  与真实业务终点拓扑同构，工具不擅自下结论），经 `business_flow.scope.exclude_domains` /
  `exclude_suffixes` 配置注入。保留 `flow_suggestions` 作为建议简表。真实主干业务流自动排前。
- **双真身孤本标注进图（`arch_gap` 新增 `twin_identity` 差距 + 画布渲染）**：复用 `duplicate_insight`
  目录级同构对，按跨模块 fan_in 判真身（最高且>0）/孤本（=0）/活跃副本（>0 非最高）；
  画布真身绿 #22C55E、孤本灰 #A1A1AA、活跃副本橙 #F97316，节点子标签直显身份，图例同步。
  实测 6 组孪生目录 97 模块全标注：真身/孤本自动区分。
- **回归不劣化**：既有分层布局（47 层无坍缩）/ 三层泳道 / 层级导航 / 统计联动
  真实浏览器全 PASS。
- **版本号**：5.6.5 → 5.6.6（patch，target 与架构图真实化）

### v5.6.5 — 画布层级导航「统计随视图联动」

> 回归验证发现：导航入口/过滤/下钻/回退在真实浏览器实测中，
> 顶部「节点/连线/差距」统计恒为全量（537/2315/357）不随层级切换更新，被误判为"有入口、无行为"。

- **定因**：`renderStats` 用 `nodes.length` / `edges.length` 全量 + 静态 `DATA.meta.summary`，
  未随 `navVisible` 过滤联动——层级过滤/下钻/回退本身在真实浏览器已生效，
  唯一失效的是统计面板反馈信号。
- **修复（`core/canvas_engine.py` `renderStats`）**：统计改按当前 `navVisible` 过滤后的可见节点/连线
  计数；全量视图保留整体差距摘要（含高中低分档），层级视图显示该层可见差距节点数，随视图切换更新。
- **真实浏览器实测（真实项目 537 节点/2315 边）全 PASS**：all 节点537→L0 节点7→L1 业务4/技术3/代码530→
  L3 节点530→L2 下钻 run_cycle 23→回退还原 530，stats 数值与画布可见节点完全一致；差距开关在 L3 下
  独立切换高亮（开=差距色、关=还原默认蓝）；泳道不劣化。
- **版本号**：5.6.4 → 5.6.5（patch，统计随视图联动）

### v5.6.4 — 画布 L0→L3 逐层下钻导航

> 回归验证发现：arch_canvas 把 537 节点全量平铺、只有三层泳道文本标签（不可交互），
> 无法"先整体后局部"逐层捋清（架构→模块→模块内逻辑→代码管线）再谈对齐/治理。
> 堵点是结构性的（工具不承载层级导航），非测试侧手法问题。

- **画布新增层级导航（arch_canvas）**：工具栏加 `L0 总览 / L1 分层 / L2 模块 / L3 代码 / 全量`
  视图切换 + 面包屑回退 + 差距开关（默认折叠、捋清后再叠加）：
  - L0 总览：只看业务定位 + 角色（先整体）；
  - L1 分层：按层聚焦（业务/技术/代码）；
  - L2 模块：双击模块节点下钻其关联子图；
  - L3 代码管线：只看代码层模块与依赖。
  基于现有 layer/type 数据实现，不动布局引擎，分层布局/三层泳道不劣化；
  flow_canvas 等无业务/技术/代码分层的画布自动不启用导航。
- **coderef-governance SKILL.md 固化 L0-L3 铁律**：核心原则新增第 2 条——捋管线必须按
  L0→L1→L2→L3 自顶向下走完，**未捋清 L0-L3 不得进入定标（define-target）与差距分析（arch_gap）**；
  场景①编排同步改为逐层下钻流程。
- **真实项目 自证**：L0 总览 7 节点（先整体）→ L1 分层聚焦（业务4/技术3/代码530）→ L2 模块下钻
  （双击模块聚焦子图 23 节点）→ L3 代码管线（530 模块），差距 367 节点可折叠，面包屑可回退。
- **版本号**：5.6.3 → 5.6.4（patch，L0→L3 层级导航）

### v5.6.3 — arch_gap 游离分档全量计数透出

> 回归验证发现：arch_gap 游离分级 unmodeled 实盘 0 与冒烟 free=189/unmodeled=11 不一致。
> 定因：**展示截断，非检测分支遗漏**——游离按 free 置顶排序，默认 `max_unassigned=50` 只展示
> 前 50 条（全 free），unmodeled 全量被截断；冒烟 189+11=200 恰是 `max_unassigned=200` 的截断口径。
> 实盘全量（真实项目 图谱 + target_arch v2）：free=189 / unmodeled=265 / total=454。

- **summary 新增游离全量分档计数**：`unassigned_free` / `unassigned_unmodeled` 直接透出全量
  free/unmodeled 计数，调用方不再受 `max_unassigned` 展示截断影响（此前 `_detect_unassigned` 已算
  出两档计数但 `analyze_gap` 未解包、summary 未透出，属上一轮半成品，本轮补齐）。
- **展开参数口径说明**：控制游离列表展开的参数是 `max_unassigned`（默认 50），非 `limit`；
  传 `limit` 不影响展开属调用方口径，已在本条目说明。
- **版本号**：5.6.2 → 5.6.3（patch，游离全量计数透出）

### v5.6.2 — 治理主链改造批次四收尾：gov 事务原子性 + 场景化 Skill 封装

> 治理主链与工具改造建议批次四最后两块收尾：gov 状态机原子性/幂等性与
> 场景化 Skill 封装层（即「少而精工具链」物化）。至此 8 条改造点 +
> 5 条建议全部收尾。

- **gov 工作项写操作全部事务化**：`GovernanceStore` 新增显式事务上下文
  管理器 `_tx()`，建档/导入/流转/豁免/改元五个写入口统一包进 BEGIN/COMMIT，异常时 ROLLBACK 不留
  半截状态——为未来多 Agent 协作（写/审/修）共享 governance.db 提供原子性保险；非法状态流转本就
  不落库（幂等），现再多一层事务兜底。
- **新增 coderef-governance 场景化 Skill**：把 57 个 MCP 工具收敛为「治理主链
  5 阶段 × 每阶段 2–4 个高频工具」编排（map-pipeline→define-target→refactor-along→
  verify-advance→health-cycle），每阶段内含目标、工具、编排步骤、产出与常见坑；内置「意图→工具」
  快速路由表（同义词/别名→主工具）+ gov_transition 参数速查 + 真身判定
  看 fan_in 不看可达性 + 治理动作护栏（不动 git 库/备份）。
- **coderef-mcp Skill 补「场景化路由」小节**：意图→工具路由表 + 结构性锈蚀场景指引，
  与 coderef-governance 联动，编程 AI 不确定工具归属时先查表。
- **版本号**：5.6.1 → 5.6.2（治理能力增强，走 minor）

### v5.6.1 — 治理主链改造批次二三：arch_audit 真身透出 + gov_issues 去噪 + 记忆导出

> 治理主链与工具改造建议批次二三四收尾，让工具链沿治理主链更顺：真身判定信息直达
> `arch_audit`、治理库封面不再被游离噪声淹没、超严格状态机有参数速查、记忆可导出为 Markdown
> 供不支持的 LLM 界面复用。纯静态、确定性，全部不依赖 LLM。

- **coderef_arch_audit 直接透出真身/孤本摘要**：新增 `identity` 列表 + `identity_count`，
  复用 `arch_insight` 真身判定 `identity_insight`，逐类列出「同名多目录实现」的副本数、活跃真身数、
  无调用者孤本数、各副本 verdict 与优先来源文件——Skill 只看 arch_audit 健康度也不会漏真身判定。
- **coderef_gov_issues 按真实 severity 排序 + unassigned 置底**：`high`/`open`/`all` 默认
  视图改为 severity 序（high>medium>low）优先、`gap_type=unassigned` 一律置底，再按 last_seen 稳定；
  治理库封面不再被 `*.min.js`/`__init__`/游离噪声刷屏，治理重点（god/cycle/duplicate）能被看到。
- **coderef_audit 补「结构锈蚀 + strategy 分场景」引导**：description 明示
  结构锈蚀要佐以 architecture 真身/重复判定与 arch_gap 的 duplicate 差距；strategy 分场景——回归复核新
  增改动用 `incr`，治理健康度体检/存量结构用 `full`，勿把治理存量当回归用 incr（存量重复不在 diff 内）。
- **coderef_gov_transition 补「参数动作速查」**：description 内置 transition/reject/meta
  三种 action 所需参数速查，明示 action=meta 时勿传 to_state、非法跳转返回错误属正常。
- **新增 coderef_operation_memory_export**：把操作记忆的 decision/convention/pitfall
  渲染导出为 Markdown（缺省 `<项目>/data/operation_memory/OPERATION_MEMORY.md`），供 attach 到
  不支持 MCP 的 LLM 界面（Claude Project / CustomGPT）；内置冲突检测——剥掉正/否定语气词后
  主题核心相同的同类别条目若方向相反（如「禁止 X」vs「推荐 X」）标记潜在冲突，防正反规则覆盖遗漏。
- **意图路由轻量兜底**：通过各工具 description 的「适用/不适用」硬约束分场景定界，
  后续由场景化 Skill 封装整体路由；暂不做在线向量反射层（符合纯静态确定性原则）。
- **版本号**：5.6.0 → 5.6.1（治理能力增强，走 minor）

### v5.6.0 — 治理主链改造批次一：arch_gap 新增重复类差距 + 游离真身区分

> 治理主链与工具改造建议的第一批工具层改造收尾，让 coderef 有能力**识别并排队治理清单里最该治理的「结构性锈蚀」**（重复/孪生/真游离），而非只盯单次变更。真实项目冒烟：识别出 20 个同构孪生（`duplicate`）与 7 组目录级重复（`directory_duplicate`，如 `shared/chart_engine` 与 `示例项目/chart_engine` 100% 同构）。

- **coderef_arch_gap 新增 `duplicate` 差距类型**：同构孪生——同名实现跨目录函数体相似度 ≥60%（复用 `arch_insight` 重复识别同一切词/相似度/通用名过滤逻辑，不重写），逐条给出符号、跨目录实现位置与相似度，作为可收敛的治理候选。
- **coderef_arch_gap 新增 `directory_duplicate` 差距类型**：目录级重复——整目录与其他目录同构（文件清单 + 函数签名双指标），识别"同构孪生目录"（如多版本并存、主线与备份目录）。
- **游离模块区分「真游离 vs 未建模」**：`unassigned` 每条附带 `monitored=free`（fan_in=0，代码孤儿、治理候选，排最前）/ `monitored=unmodeled`（被跨模块真实调用但 target_modules 未覆盖，本质是"目标架构覆盖不足"而非孤儿，文案引导去 define-target 补 target_modules），不再把所有游离一律当孤儿刷屏。
- **游离链路自动豁免噪声**：`vendor` / `node_modules` / `*.min.js` / `*.min.css` / `__init__` / `dist` / `build` 自动豁免，避免第三方依赖与压缩静态产物淹没真游离。
- **summary 新增 `duplicate`/`directory_duplicate` 计数**，供 `arch_verify`/`gov_start`/督办链路统一感知重复类差距规模。
- **版本号**：5.5.4 → 5.6.0（治理能力增强，走 minor）

### v5.5.4 — docs 超大项目并发提速（模块文档并行生成）

> 针对"仅接受边界、未增强能力"的反馈，补上工具的**自身能力增强**：LLM 生成模块文档的主耗时段从逐模块串行改为固定并发线程池并行，超大项目（真实项目 573 文件/20 万+ 行 62 分钟级）单次全量耗时约按并发数线性下降，降低超大项目触底超时返回 partial 的概率。

- **模块文档 LLM 调用并发化（`core/wiki_generator.py::_generate_module_docs`）**：原 `for mod in modules → self._llm_ask()` 逐模块串行（网络 IO 是主耗时段），现改为 `ThreadPoolExecutor` **固定并发池**并行执行所有模块的 LLM 生成；落盘 / `docs` 列表 / front_matter 注入 / 进度取消检查点收敛回主线程按原模块顺序执行——写文件与列表操作保持线程安全，输出顺序稳定与串行时一致
- **并发度可控**：默认 `4` 个 worker，上限 `16`，环境变量 `CODEREF_WIKI_CONCURRENCY` 可调（防误设超大并发耗尽连接）；并发数仅影响执行速度，不改变产物内容与顺序
- **取消 / 进度语义保持**：预算阶段与落盘阶段各保留 `progress_cb` 检查点（`TaskCancelled` 仍可穿透），后台任务取消仍可在阶段点收尾
- **线程安全**：每 worker 只调用 `self._llm_ask`（只读 prompt）；共享状态 `_last_llm_error` 仅作诊断提示、并发下最后写入者胜，不影响产物正确性；`_call_count` 隶属 LLM 客户端实例
- **验证**：假 LLM（每调用固定 0.2s 延迟）10 模块并发耗时 0.63s（串行估算 2.0s，约 3.2× 加速），落盘篇数 / 内容 / front_matter / 顺序与串行一致；`py_compile` 通过
- **版本号**：5.5.3 → 5.5.4

### v5.5.3 — 全工具补齐对账修复（治理产出落盘 / 目标架构保真 / 排序追溯 / 后台任务取消）

> 全工具补齐对账沉取 6 项确证缺陷集中修复：治理看板 HTML 落盘、目标架构落盘保真、缺省关闭周期、后台任务取消、图谱 callers 追溯、operation_memory 异常兜底。

- **gov_board 落盘 HTML（`core/mcp_server.py`）**：缺省 output_dir 时自动生成本体 HTML 写盘到 `<project>/.coderef/gov_board.html`，description 明确产物路径，供人工/浏览器直接查看（不再"仅返回 JSON、无 HTML 产物"）
- **target_arch_set 落盘保真（实证核对）**：`normalize_arch` 以 `dict(arch)` 完整复制输入再补缺省空数组，`_target_arch_set` 全量 `json.dump`，version/tech_roles/business_flows/constraints 等顶层段落完整保真落盘；实测传入该 4 段富结构，落盘文件 4 段齐全无丢失。"丢段"现象根因为 TRAE coderef MCP（stdio 长驻进程）仍运行旧版本代码——重启 MCP 并重新 set 覆盖写入即可消除
- **gov_close 缺省关闭（`core/healthcycle.py`）**：缺省 cid 时自动定位当前 open 周期并关闭（与 gov_start 周期状态一致），无 open 周期时返回明确提示"请先用 coderef_gov_start 建档"，不再误导性报"周期不存在或已关闭"
- **query callers 追溯补全（`core/code_knowledge_graph.py`）**：方法调用侧 `call.func_name` 常带类/模块前缀（如 `self.run_bot` / `Bot.run_bot`），此前用纯短名 `run_bot` 精确匹配失败导致 CALLS 边漏建、callers 查询返空；现 `_find_node_by_name` 精确失败后做唯一候选模糊回退，建边时优先全名匹配再回退短名，`run_bot` 可追溯到真实调用者
- **operation_memory_sync 异常兜底（`core/operation_memory.py`）**：LLM 提炼路径整体捕获异常，返回结构化 `extract_error`，不再裸 `'"kind"'` JSON 解析报错崩溃后台任务；同时修复提炼提示模板花括号与 `str.format()` 冲突（`replace` 替代 `format`）
- **后台任务取消接口 + 可定位状态（`core/mcp_server.py`）**：新增 `coderef_task_cancel` 工具同步置任务为 cancelled——随后 `coderef_task_status` 返回可定位的 `cancelled`（不再无限报"running、无部分结果"），且 `_bg` 的 progress 回调实现协作式取消（下一阶段点抛 `_TaskCancelled` 尽早收尾，非普通 error）。审计/docs 等逐阶段汇报工具可真正停止；取消前已产出的增量产物（文档/报告）按模块落盘可先用
- **验证**：缺省 output_dir 时 `.coderef/` 生成 gov_board.html；富结构 4 段落盘归齐；缺省关闭命中 open 周期；模拟方法调用 `run_bot` 精确命中 `Bot.run_bot` 且 callers 返回真实调用者；sync 不再裸报错；cancel 后状态转 cancelled、协作收尾；全部改动 `py_compile` 通过
- **CodeRabbit 评审修订**：① `self/cls` 方法调用先按调用者所在类解析（`self.run_bot`→`Bot.run_bot`），避免与顶层同名函数撞 CALLS 边，并加碰撞测试；② `coderef_docs` 透传 `progress_cb` 至扫描/图谱/wiki 生成阶段，docs 后台任务具备阶段内协作取消点；③ `coderef_task_cancel` 对曾取消已收尾的任务保持 `cancelled` 终态，不退化误报 `completed`
- **CodeRabbit 二轮评审修订**：① 取消信号穿透——`TaskCancelled` 下沉定义于 `core/pipeline_runner`（被依赖方），audit/docs/wiki 各 `except Exception` 显式 re-raise，`_bg` 复用同一异常，取消不再被吞、daemon 线程不再跑到底；② WikiGenerator 逐模块生成循环加 `progress_cb` 检查点（`_generate_module_docs` 每模块先过取消点），docs 取消可在 wiki 生成内部生效；③ `self/cls` 调用改按调用者所在**模块+类**构造完整方法 id（`self.run_bot`→`method:<调用者mod>:<调用者类>.run_bot`）精确主键匹配，跨模块同名类方法不再误连（碰撞测试：modA/modB 各自 `Bot.run_bot` 均正确归属本模块）
- **CodeRabbit 三审修复 + docs 超大项目定性**：① `progress_cb` 透传链补齐——`_generate_all_documents`/`_incremental_update` 及其类方法委托、`_generate_full_pipeline` 主项目与子项目两处调用全部透传，消除 NameError；② docs 定性采纳「接受边界」：`coderef_docs` 描述诚实注明超大项目（实测 573 文件/20 万+ 行）单次全量可能超后台兜底 860s 返回 partial，建议走分片/增量（coderef_audit incr / 按子项目维度逐个扫描），避免大项目预期失败
- **版本号**：5.5.2 → 5.5.3

### v5.5.2 — 专项工具可信度修复（owasp/change_guard 降噪 + 入口/描述指引）

> 专项工具对账暴露的工具可信度问题集中修复：owasp 静态检测 8/8 误报、change_guard 4/4 误报降噪，flow_verify 入口指引与相近符号提示，target_arch_set 描述补 role_keywords 说明。

- **owasp 静态检测降噪（`core/owasp_compliance.py`）**：新增 `_is_false_positive` 上下文识别，过滤 mock/测试桩、错误码常量、内部路径拼接、临时文件清理、标准库导入、台账 JSON 写入、角色顺序正确、密钥从配置读取等 8 类静态启发式误报；总量 906→726；summary 明确标注"静态启发式规则误报率较高，需人工复核"。CodeRabbit 评审后修订：临时文件清理需临时/缓存路径证据（`_has_temp_evidence`，避免抑制 `os.remove(request.args["path"])` 等破坏性删除）；角色扫描回溯方向修正（从 line_no 向文件开头回溯、遇 def/class 边界停止，原 `start=i+1` 更新无效导致 system append 在 def 之后时漏检）
- **change_guard 退化误报修复（`core/change_guard.py`）**：按行方向区分新增/删除校验（`+` 新增、`-` 删除），删除行须在新增行中无等价替代才报退化，避免把"重构/移动/新增校验"误判为"删能力"；4 个误报文件（canvas.py/db_schema.py/engine_v3.py/research_bridge.py）全部消除，真实退化仍能检出。CodeRabbit 评审后修订：重试削弱检测移除校验链前置条件（无校验链的客户端删除重试同样检出）；新增 `_is_decl_or_comment` 排除 SQL 建表/字段声明行、纯注释行，避免 SQL 字段名（如 `retry_count`）误判为删重试逻辑
- **change_guard 路径可读性（`core/change_guard.py`）**：`_clean_diff_path` 去掉 git diff 路径两端引号、`a/`/`b/` 前缀，解码 UTF-8 八进制字节转义，输出可读相对路径（如 `创意引擎/engine_v3.py`）
- **flow_verify 入口指引（`core/mcp_server.py` + `core/flow_verify.py`）**：description 补充 `相对目录名.符号名`（如 `调研工具.run_bot`）写法指引；入口未命中时新增 `suggest_entries` 相近符号候选（名称模糊匹配 top N，带文件路径+行号），summary 附候选减少试错
- **target_arch_set 描述补全（`core/mcp_server.py`）**：description 明确 `tech_roles.role_keywords`（可选，角色职责关键词表，供 coderef_role_boundary 符号级职责判定；缺省时 role_boundary 会提示未配置）
- **验证**：owasp 抽查 8 个误报位置全部过滤；change_guard 受控 diff 场景（删校验→检出、重构移动校验→不误报、重试削弱→检出）；路径清理 4/4 通过；flow_verify 实测 `调研工具.run_bot` 命中、错误入口附相近符号
- **版本号**：5.5.1 → 5.5.2

### v5.5.1 — target_arch_set 校验错误透传 + 描述与 schema 对齐 + arch_gap 显式提示

> 治理决策链核心入口（目标架构 → 差距分析）的"静默返空"根因修复：校验失败不再被 TRAE 吞成空 `[]`，调用方不再误判"无差距"。

- **校验错误透传（`core/mcp_server.py` `_target_arch_set`）**：校验失败由 `raise ValueError`（走 JSON-RPC error，TRAE 客户端吞成空 `[]`）改为返回结构化 `{status:error, error, errors:[...]}`（走 result.content 成功通道），调用方可读到含具体字段的可读错误（如 `business_flows[0] 缺少必填键: id`、`steps[0] 必须是对象`），不再与"成功返回空"混淆
- **描述与 schema 对齐（`coderef_target_arch_set` description）**：明确 `business_flows` 每项必填 `id/name/steps`、`steps` 每项必须是 `{id,name}` 对象（非字符串）、可选 `tech_roles` 引用已定义角色 id；`tech_roles` 每项必填 `id/name/target_modules`；`constraints` 每项必填 `from/to/rule`——按描述构造即能通过校验
- **arch_gap 显式提示（`core/mcp_server.py` `_arch_gap`）**：目标架构未设置（读存储抛错）或传入的 target_arch 无效（校验失败）时，返回 `{status:error, error:"目标架构无效（N 条）..."}` 显式提示，不再静默空、不再链式污染为"无差距"
- **验证**：非法样例（flow+字符串 steps）返回 7 条可读错误；合法样例（id/name + {id,name} 步骤）成功落盘 `{roles:3, flows:1, constraints:3}`；arch_gap 未设置/无效 target 均返回显式 error
- **版本号**：5.5.0 → 5.5.1

### v5.5.0 — 画布标签可读性揭示策略 + 业务/技术/代码三层架构对齐

> 让自由画布从"一堆点线看不懂"走向"进门看懂"：标签展示分级揭示，架构按业务/技术/代码三层泳道对齐表达。

- **节点标签可读性/揭示策略（`core/canvas_engine.py`）**
  - **LOD 标签分级**：缩放低于 0.30 时自动隐藏标签文字只留色块/图标，杜绝"全局马赛克不可读"；缩放回升即恢复
  - **长标签省略 + 悬停揭示**：标签超宽 `ellipsis` 截断，节点 DOM 携带 `title` 完整名/路径，悬停即显全名
  - **最小可辨尺寸 + 对比度保障**：`fitView`「适应」后缩放下限保正节点最小可辨像素；节点背景 `#1E293B` + 标签文字 `#E2E8F0` 高对比可读
- **三层架构对齐（业务/技术/代码泳道 + 跨层 trace）**
  - **数据层 `tier` 语义**：节点支持 `tier`（business/service/code），边支持 `rel`（flow/align/land/depends）
  - **三层横向泳道渲染**：`render_canvas` 按 tier 分三个横向泳道 + 层标题（业务层·业务/用户旅程 / 技术层·服务角色 / 代码层·落地构件），默认视图即呈现三层结构
  - **泳道内保持业务流拓扑**：泳道内仅按本 tier 内部 flow 边算拓扑深度排序（`_split_layer_rows` 增 `topo_always`），保证"进门→点餐→吃饭→结账"链路从左到右可读，不被跨层边污染
  - **跨层 trace 边可视化**：align（业务→技术，橙虚线）、land（技术→代码，绿点线）、depends（代码内部，灰实线）差异化配色/线型，纵向上逐层可追踪
- **验证**：`restaurant_layers.json`（餐厅三层样例）端到端——三层泳道矩形 3 个且层标题可见；节点按 tier 三行纵排（y=124/428/732）；业务链路 x 顺序 1198→1406→1614→1822；跨层 align×6 + land×7 trace 边可见；默认视图 scale≈1.1 文字可读；缩小至 0.2 触发 LOD 文字隐藏
- **版本号**：5.4.4 → 5.5.0

### v5.4.4 — 自由画布分层布局/默认视图 Y 轴坍缩

> 解决前端渲染缺陷（首屏即不可用）：分层布局与默认初始视图把全部节点压成一条水平线。

- **`core/canvas_engine.py` 分层布局 Y 轴坍缩修复**：根因是节点 layer 趋同（`_norm_node` 把 `layer` 回退到 `type`，arch_canvas 产出的节点 type 多为 module/default），`_layout_layered` 把所有节点归入同一层 → 只 X 分布、Y 全相等 → 一条线；默认初始布局即坏。新增按依赖 DAG 最长链深度（Kahn 拓扑）对单层/超宽节点排序并拆子行（`_node_depths` + `_split_layer_rows`，Python 端与 JS 端 `layoutLayered` 同步），Y 维逐行二维展开；单行上限 12 节点、总宽限幅占画布可辨宽；已显式定位的节点仍保留为锚点
- **「适应」最小可辨识尺寸下限**：`fitView` 加缩放后节点最小可辨像素下限（24px），防止超大图把节点缩成不可辨，首屏与「适应」后均保持可读
- **验证**：84 节点同层场景由 1 行坍缩 → 拆 7 行，Y 展开 [230,1490]，X 轴每行 12 节点；力导向对照（二维展开正常）不受影响
- **版本号**：5.4.3 → 5.4.4

### v5.4.3 — review 首调 JSON 命中率强化（零额外 LLM 耗时）

- **`core/code_review.py` 首调命中强化**：system prompt 更强制（明确"输出会被程序直接解析、违反即失败、无问题输出 []"）+ 重试 prompt 更严格（只输出 JSON 数组、禁 Markdown 标记），从根因减少 v4-flash 输出散文导致的 JSON 解析失败
- **零额外成本**：不增加 LLM 调用次数、不增加耗时，仅提升首调直接命中 JSON 的概率（冒烟验证：首调即命中、未触发重试）
- **版本号**：5.4.2 → 5.4.3

### v5.4.2 — 编程 AI 记忆 × coderef 执行记忆双向落地

> 解决"编程 AI 记忆与 coderef 执行记忆零互通"：开发 AI 的架构规则与操作规程印不进执行记忆，可恢复性差。

- **方向 A（投放页，主）**：新增 `CODEREF.md` 操作红线与规程投放页，`AGENTS.md` 引入之——任何编程 AI 读仓即自然读到操作守则，规则随项目天然传播到执行记忆
- **方向 B（自动同步，辅）**：`config/settings.py` 新增 `OMEM_AUTO_SYNC_ON_GOV` 开关，治理流程（audit/scan 等）收尾时后台线程增量同步操作记忆（`pipeline_runner._auto_sync_om_on_gov()`，30s 去重，不阻塞高频路径）
- **版本号**：5.4.1 → 5.4.2

### v5.4.1 — CodeRabbit 复审 4 findings：撤销语义/坐标偏移/自动布局不动已定位/角色高亮

> 对 v5.4.0 自由布局画布进行 CodeRabbit 复审，采纳 4 findings（2 critical / 1 major / 1 minor）。

- **`core/canvas_engine.py` undo/redo 语义修正**（critical）：撤销/重做栈原记录"修改后"状态，回退语义错误。重构为 `recordPre()`（mutation 前记录一次）`/`commitChange()`（mutation 后入栈）成对模式，并把全部 mutation 点接入——节点拖拽、方向键微调、任意连线、增删节点/连线、改属性、复制节点、导入 JSON——现在每个操作撤销时都回到操作前状态；无实际位移的单击不再污染历史栈
- **`core/canvas_engine.py` 鼠标坐标 44px 偏移**（major）：`toLocal`/`toWorld` 视口变换将 client 坐标换算为 canvasWrap 局部坐标，修复工具栏导致的连线/吸附/右键落点系统性偏差
- **`core/canvas_engine.py` 自动布局不再覆盖已定位节点**（minor）：`auto_layout` 与分层/力导向布局仅对未定位节点（x=0 且 y=0）赋坐标，已显式定位的节点作为固定锚点保留，避免覆盖调用方排好的位置；力导向布局中锚点只推挤可动节点、自身不移位
- **`core/canvas_generator.py` 角色节点高亮**（critical）：缺角色判定由 `id.endswith(":")` 改为 `id.startswith("role:")`，缺失角色节点恢复红色高亮
- **版本号**：5.4.0 → 5.4.1

### v5.4.0 — 自由布局画布引擎：架构图/流程图可自由拖拽

> 自研轻量实现纯 HTML/CSS/JS + SVG 自由布局画布，零外部依赖、离线可用。拖拽、任意连线、平移缩放、缩略图、右键菜单、自动布局等交互能力为业界自由/流程画布工具的通用范式，实现为独立编写的自有代码，不复制或翻译任何第三方实现。

- **`core/canvas_engine.py` 自由布局画布引擎**（新增）：纯 HTML/CSS/JS + SVG 自包含、零外部依赖、离线可用。完整交互：节点自由拖拽（网格 + 节点边缘对齐吸附）、端口拖出任意连线成流（自动选端口）、画布平移/缩放（滚轮 + 按钮）、缩略图导航（mini-map 点击跳转）、右键菜单（添加/复制/删除节点、连线样式、自动布局、导出 JSON）、快捷键（Ctrl+Z 撤销 / Ctrl+Shift+Z 重做 / Delete 删除 / Ctrl+A 全选 / 方向键微调 / Ctrl+± 缩放）、属性面板（编辑节点/连线 label、颜色、props JSON）、分层/力导向自动布局、导出/导入画布 JSON、撤销/重做历史栈
- **`core/canvas_generator.py` 架构画布升级为自由布局版**：三层布局（业务步骤 → 技术角色 → 代码模块）改为自由画布节点 + 连线；差距高亮保留（游离灰/循环黄/缺失红虚线/依赖违例红连线）；业务步骤→角色映射、角色→模块归属、模块→模块依赖均以可拖拽连线呈现
- **`core/flow_canvas.py` 交互式流程画布**（新增，MCP `coderef_flow_canvas`）：从代码自动提取业务管线（`pipeline_insight` 入口管线，沿 CALLS 归纳阶段序）+ 跨模块业务数据流（`cross_module_flows`），渲染为可自由拖拽的流程图；每条管线一个图层，步骤按序连线，跨模块数据流带调用次数标签
- **MCP 工具**：`coderef_arch_canvas` 升级为自由布局版；新增 `coderef_flow_canvas`（project_path / output_dir / max_entries / max_depth，默认后台执行，加入 HEAVY_TOOLS）
- **版本号**：5.3.4 → 5.4.0

### v5.3.4 — CodeRabbit 复审 4 findings：测试目录识别/跨目录判定/import 歧义/同构空集

- **`_is_test_file` 根级测试目录识别**（minor）：路径段拆分检查 `test/tests/测试` 目录段，修复根级 `tests/foo.py`（相对路径无前导斜杠）漏判为测试文件——`source_engine/engine.py` 因此被正确标为"活跃真身"（被引用 17，引用方含 `research_queue.py`）
- **`duplicate_insight` 跨目录判定**（major）：改用相对路径目录（`_rel_dir`）判断跨目录，修复 `apps/worker` 与 `legacy/worker` 同名 basename 被 `_mod_of` 合并误判同目录而漏报；`_mod_of` 仅用于报告展示
- **`_resolve_import_target` 歧义兜底**（major）：tail fallback 收集全部匹配模块 ID，仅恰好一个匹配时返回，歧义返回空——不再依赖 `mod_ids` 集合迭代顺序选目标
- **`_dir_isomorph_insight` 空函数集跳过**（major）：任一侧函数签名集为空时跳过该目录对，避免空集 Jaccard=1.0 把纯文件目录误判为同构
- **版本号**：5.3.3 → 5.3.4

### v5.3.3 — 业务级判定增强：真身/重复聚焦业务类，目录级同构识别

- **真身判定业务级增强**（交叉对比反馈）：聚合范围从"通用方法名"改为"业务级同名类"——过滤 `__init__`/`to_dict`/`execute`/`render` 等通用方法名噪音与 Config/Result/TestCase 等通用类名；每个副本报告引用方详情（文件:行 + 符号名，排除测试文件），判定区分"生产入口候选（无被调用者）" / "活跃真身" / "仅测试引用"；业务类名（Bot/Engine/Workflow 等后缀）优先展示，避免双真身被 3+ 副本通用类挤出 top 列表
- **重复识别目录级同构**：新增目录级同构比对——按相对目录聚合文件清单 + 函数签名，双指标 Jaccard 相似度 ≥ 阈值判定"同构重复候选"（如 `调研工具/` 与 `source_engine/` 全目录同构：文件 0.83 / 函数 0.96），报告目录 A/B、相似度、文件数
- **图谱节点 ID 相对路径化**（真实项目治理发现）：模块/函数/类/方法节点 ID 前缀由 basename 改为相对 project_path 的路径，修复跨目录同名文件（如 `source_engine/engine.py` 与 `调研工具/engine.py`）被 `INSERT OR REPLACE` 互相覆盖导致的图谱漏扫；`_resolve_import_target` 支持点分路径精确匹配 + 跨目录同名模块兜底
- **FlowVerifier 函数体提取修复**：`graph_closure.load_graph` 节点查询补 `end_line` 字段，修复函数体切片为空导致的重复识别失真
- **版本号**：5.3.2 → 5.3.3

### v5.3.2 — 集成加固：图谱 db 直喂洞察，消除二次探测竞态

- **集成加固**（交叉对比反馈）：`Pipe.architecture` 把 `_build_kg` 刚构建的图谱 `db_path` 直接传给 `insight_markdown`，消除 insight 内部二次 `ensure_kg` 探测/重建的时序竞态——MCP 长驻进程下首调即出洞察（不再偶发 790B 壳）
- **洞察为空不再静默**：若图谱不可用导致洞察为空，`errors` 明确记录原因（"洞察为空（图谱 … 不可用）"），不再无声产出壳报告
- **版本号**：5.3.1 → 5.3.2

### v5.3.1 — MCP 中文路径编解码

- **MCP 中文 output_dir 写盘乱码**：`Server.run()` 强制 stdin/stdout 为 UTF-8——TRAE 经 stdio 发送的 JSON 是 UTF-8 字节，Windows 下 stdin 若按 GBK 解码，中文 output_dir（如"测试归档"）会被误解码成乱码目录名（如 `娴嬭瘯褰掓。`）。修复后中文路径正确落盘
- **版本号**：5.3.0 → 5.3.1

### v5.3.0 — 架构洞察：管线/真身/重复自动产出人话结论

- **管线自动梳理**（新增 `core/arch_insight.py`）：自动发现入口（无被调用方 + 启发式），沿 CALLS 归纳阶段序管线（x→y→z 带文件/行号/说明），输出 Markdown 表格；另附跨模块业务数据流
- **真身/入口判定**：同名多目录实现（如 check_plan_coverage 同时存在于多个子系统），报告各副本被谁引用 / 是否活跃；仅无被调用者的 root 副本标"生产入口候选"（dunder 特殊方法单独标注），无 root 时只标"被引用最多的副本"，不单凭被引用数推断入口
- **重复/同构识别**：同名函数跨模块实现按函数体相似度分区——相似度 ≥60% 的副本聚成独立"重复实现簇"（建议收敛），未配对（低相似度）副本归入"同名候选"（仅同名、契约可能不同，不推荐合并）
- **`coderef_architecture` 报告升级**：不再只是"790B 壳"，自动追加三段洞察；`insight_llm` 参数可选追加 LLM 人话总结（需 API Key，缺省静态结果完整可用；非布尔值拒绝）
- **`coderef_arch_canvas` 后台化**：加入 HEAVY_TOOLS 默认后台执行（超大项目不再同步撞 MCP 超时），支持 `background=false` 强制同步
- **cache 收口**：清理主仓历史残留图谱库（10.9MB），图谱库已随 project_path 落位
- **CodeRabbit 复审**：6 条 findings 已全部修复——真身/重复判定严谨性、洞察失败显式渲染、insight_llm 布尔校验、重复簇按相似度分区（未配对副本不误并入重复簇）、gov_pipeline 流水线契约文档对齐（仅 Confirmed/Fixing）
- **版本号**：5.2.3 → 5.3.0

### v5.2.3 — 真实项目扫描落点修复

- **`coderef_architecture` 报告落点可控**：报告默认落 `<project_path>/coderef-report/`（不再写 MCP 进程 cwd 的 `coderef-report/`），支持 `output_dir` 显式外置——避免真实多项目/跨仓协作污染对方主仓
- **知识图谱库跟随被检项目**：`cache/kg/*.db` 由安装根迁移至 `<project_path>/cache/kg/`，读方经 `CodeKnowledgeGraph.db_path` 一致定位，不再把 9.7MB+ 图谱库写进调用方 cwd
- **版本号**：5.2.2 → 5.2.3

### v5.2.0 — 5.2 三项预想落地 + 治理自动化流水线贯通

- **符号级职责越界检测**（新增 `role_boundary`）：模块归属正确但符号逾越角色边界（如 waiter.py 里有 cook()），静态信号（定义/调用关键词命中）+ 可选语义判定接口，纯静态确定性
- **治理自动化流水线**（新增 `gov_pipeline`）：把在途工作项串成可追踪闭环——状态→Fixing、凭差距快照生成任务卡（复用 refactor_task_generator）、调 arch_alignment_verifier 复验、达标自动 Verified / 未达标保持 Fixing 附缺口，全程写活动日志
- **动态探针**（新增 `dynamic_probe`）：补全静态图谱盲区，挖掘动态信号（动态导入 / 装饰器注册 / 间接索引 / entry_points），默认零执行被检项目代码
- **Web 看板应用态增强**（`gov_webdash`）：自包含交互 HTML 看板（筛选 / 详情 / 状态流转按钮）+ `/api/transition` 数据回写接口（仅限本机）
- **多代码库聚合治理**（新增 `gov_workspace`）：跨仓汇总治理状态，输出整体健康度视图
- **定时体检实跑落地**（`gov_schedule`）：从"产出 cron 片段"升级为生成可直接运行的 `run_cycle.py` 触发脚本 + `--check` 离期检查
- **新增 MCP 工具**（6 个）：`coderef_gov_pipeline` / `coderef_dynamic_probe` / `coderef_gov_board` / `coderef_gov_workspace` / `coderef_gov_schedule` / `coderef_role_boundary`
- **版本号**：5.1.0 → 5.2.0（工具数 48 → 54）

### v5.1.0 — 5.1 定期体检：从"一次性重构"升级为"定期体检"

- **治理持久层**（新增 `governance_store`）：SQLite 存储体检周期 / 治理工作项 / 活动日志，状态机 Detected→Confirmed→Fixing→Verified→Archived/Rejected + 去重/复发/豁免语义
- **体检周期编排**（新增 `healthcycle`）：建档 / 导入差距 / 流转 / 豁免 / 收尾 / 报告
- **预置视图**（新增 `gov_view`）：open / all / high / recurred / rejected / archived / overdue / assigned / recent 固定查询入口
- **报告与趋势**（新增 `gov_dashboard`）：单期报告 + 跨期趋势 + 自包含 HTML（零 CDN）
- **新增 MCP 工具**（5 个）：`coderef_gov_start` / `coderef_gov_close` / `coderef_gov_issues` / `coderef_gov_transition` / `coderef_gov_report`
- **版本号**：5.0.0 → 5.1.0（工具数 43 → 48）

### v5.0.0 — 5.0 启动：架构推回正轨（Phase 0-2 核心闭环）

- **目标架构 JSON Schema**（新增 `target_arch_schema`）：定义"人定义的正轨"标准结构（业务层 business_flows / 技术层 tech_roles / 约束 constraints），零依赖手写校验，结构化错误返回
- **架构差距分析器**（新增 `arch_gap_analyzer`）：对比现状知识图谱与目标架构，输出 7 类确定性差距（missing 职责缺失 / dependency_violation 依赖违例 / cycle 循环依赖 / business_gap 业务断链 / unassigned 游离模块 / god_module 上帝模块 / large_module 异常规模），复用 arch_audit 不重写
- **可视化架构画布**（新增 `canvas_generator`，Phase 1）：自包含 HTML 三层画布（业务/技术/代码层），拖拽定义归属、业务→技术连线、差距高亮、导出目标架构 JSON，零外部依赖离线可用
- **重构任务卡生成器**（新增 `refactor_task_generator`，Phase 2）：差距清单 → 编程 AI 可执行任务卡（create_module/fix_dependency/break_cycle/implement_flow/move_module/split_module + 图谱影响范围 + 验证标准）
- **架构对齐验证器**（新增 `arch_alignment_verifier`，Phase 2）：四维对齐度评分（职责40%+依赖30%+业务20%+健康10%）+ 差距复检，支持 changed_files 增量模式
- **新增 MCP 工具**（6 个）：`coderef_target_arch_set` / `coderef_target_arch_get` / `coderef_arch_gap` / `coderef_arch_canvas` / `coderef_refactor_plan` / `coderef_arch_verify`，全部纯静态、确定性、轻量同步
- **版本号**：4.9.12 → 5.0.0（工具数 37 → 43）

### v4.9.12 — review 占位率收敛与降级信息增强

- **review 占位率收敛与降级信息增强**（`code_review`）：复测确认占位率 8.5%（4/47），4 条占位均源于 v4-flash 在长上下文下首调+重试双散文（3/4 批散文明确抱怨"文件内容被截断"）。修复两处：①prompt 增强——system 与 diff/batch 两处输出要求均加"文件内容可能因长度限制被截断，请基于可见内容审查，不要因内容不完整而拒绝输出或输出散文"，从根因上减少因截断引发的散文；②兜底增强——重试仍失败时改用 `_degraded_comment_from_text`，把 LLM 散文反馈压缩进降级评论 detail（标题改为"LLM 审查未返回结构化结果"），让占位评论携带可定位线索而非仅"待人工确认"
- **验证**：mock 首调散文+重试散文 → 降级评论带散文线索；diff/batch prompt 均含截断提示；正常 JSON 数组路径无回归（不触发重试）
- 版本号：4.9.11 → 4.9.12

### v4.9.11 — review 散文当思考二次抽取

- **review 散文当思考二次抽取**（`code_review._call_llm`）：复测确认 v4-flash 约 9/14 批首调输出散文触发重试，重试后仍有 2-4 条占位残留（≤5%）。修复：重试提示从"重新输出 JSON"升级为"把你上一次的思考过程整理为结构化 JSON 评论数组"，让 LLM 把散文当思考直接整理成 JSON，不增加调用次数；首调直接成功/空数组合法/重试仍失败降级占位 4 场景验证通过
- **验证**：skill-guide 一致性核对 server_only=0（SKILL.md 补齐 `coderef_operation_memory_recover`，37 工具完全一致）；coderef_audit 全量维度回归（样例项目 56.7s 完成，12 死函数独立呈现，26 类别正常产出无冲突）；混合类别聚合回归（DEAD 独立 + BUG/SEC 正常聚合不受影响）
- 版本号：4.9.10 → 4.9.11

### v4.9.10 — 死代码聚合细分组 + review JSON 约束强化

- **死代码聚合细分组到函数名级**（`pipeline_runner._burst_merge`/`_dedup_adjacent`）：复测确认 `[DEAD-*]` 前缀已把死代码从"未使用导入"中拆出，但同类死函数超过 `BURST_THRESHOLD=8` 仍被爆发式合并成单条（12 个死函数合并为 1 条 count=12，标题只留首个函数名），ARC-04/05 无独立 finding。修复：`_risk_key` 对 `DEAD-` 开头的 risk_id 返回完整 title（含函数/导入名），使每个死函数独立分组独立 finding，`_dedup_adjacent` 的 `_rk` 同步保持一致
- **v4-flash 对 json_object 约束不足**（`code_review._call_llm`）：复测确认 `response_format=json_object` 被端点接受但首调仍输出自由文本触发重试（14 批 12 失败 11 占位）。修复：system 提示模板强化为"唯一输出必须是合法 JSON 数组，严禁任何解释/Markdown 代码块，直接以 [ 开头以 ] 结尾"；`temperature=0.1` 降低随机性；无意义结果（如截断修复产生的字符串数组）视为解析失败触发重试
- **max_tokens=4096 截断合法 JSON**（`code_review._call_llm`）：复测确认 4096 仍会截断合法 JSON 导致 parse 失败降级。修复：`max_tokens` 4096 → 8192（首调与重试均生效）
- **单行死代码 ≥3 行阈值**（`code_simplifier._detect_commented_code`）：反例确认单行注释死代码（如 `# import os` 整行注释掉的代码）因 ≥3 行阈值漏检。修复：注释块检测阈值 3 → 1 行，单行被注释的代码也独立成 finding
- **验证**：聚合层模拟 12 个不同函数名 DEAD-FUNC finding → 12 条独立输出（count 全为 1）；端到端 `run_single(proj, "simp")` 死函数各自独立 finding；3 文件编译零失败
- 版本号：4.9.9 → 4.9.10

### v4.9.9 — 死代码聚合层吞没修复 + review JSON 降级闭环

- **死代码聚合层吞没**（`code_simplifier` + `pipeline_runner._burst_merge`）：复测确认底层检测已修对（`def_pattern` 扣除定义行），但聚合层 `_burst_merge` 分组键 `(tool, category, risk_id, severity)` 把"未使用的导入"与"未调用的函数"（同 `dead_code` + 同 severity + 无 risk_id）合并成单条，标题被覆盖为"未使用的导入"，函数级死代码在最终用户可见结果中被吞没。修复：5 种 dead_code 子类型 title 加 `[DEAD-IMPORT]`/`[DEAD-FUNC]`/`[DEAD-CLASS]`/`[DEAD-COMMENT]`/`[DEAD-TODO]` 前缀，`_dedup_adjacent`/`_burst_merge` 按 risk_id 细分，死代码独立呈现
- **coderef_review JSON 降级不闭环**（`llm_integration` + `code_review`）：复测确认重试机制正确但仅偶发成功（3 次运行仅 1 次产出真实评论），且某次运行全部批次触发重试叠加导致 900s 轮询超时整体无结果。根因是 deepseek-v4-flash 对 prompt 层"严格只输出 JSON"约束服从性差。修复：`chat_completion` 支持 `response_format={"type":"json_object"}` 透传（API 层强制 JSON，比 prompt 约束可靠），端点不支持时自动回退普通调用重试一次；`code_review._call_llm` 首次调用与重试均传 response_format
- **验证**：死代码检测层 12 个真实死函数 + 3 个预埋死函数独立成条（不再混入"未使用导入"）；聚合层模拟 `_dedup_adjacent`+`_burst_merge` 后 `[DEAD-FUNC]`/`[DEAD-IMPORT]`/`[DEAD-CLASS]` 独立呈现；response_format 透传/不传/端点不支持回退三场景 mock 验证通过；3 文件编译零失败
- 版本号：4.9.8 → 4.9.9

### v4.9.8 — 缺陷批量修复（死代码/JSON 降级/规则过滤/检测盲区）

- **死代码检测器 bug**（`code_simplifier._detect_dead_functions`）：原正则把 `def xxx():` 定义行、注释/字符串里的 `xxx(` 误统计为调用，导致死代码全漏报（ARC-04/05/06）。修复：剥离注释与字符串字面量后再统计调用，并扣除函数定义行
- **coderef_review JSON 解析降级**（`code_review._call_llm`）：deepseek-v4-flash 倾向输出自由文本而非严格 JSON，`_try_parse_json` 失败后不重试直接占位，真实审查结论 0 条。修复：首次解析失败后增加一次"强制仅返回 JSON"重试（最多 1 次）
- **规则场景过滤**：IRON-SEC-17 PHP 规则按文件语言过滤（纯 Python 不再误报 `platform.system()`）；AGENT-* 规则仅对含 LLM 特征的代码生效（纯 HTTP 库/认证不再误报 AGENT-SEC-01/09/14）
- **检测模式盲区**：路径穿越扩展 `os.path.join(base, key+".db")` 动态拼接模式（正则 + AST 双层）；参数覆盖放宽 key 与参数同名要求（覆盖 `db.config_read("default_password")`）
- **docs OVERVIEW 归属错位**（`wiki_generator`）：coderef_docs 未指定 output_dir 时默认写 cwd 侧 txt（多项目共用互相覆盖）。修复：纠正为 project_path 侧 `docs/wiki`，缓存命中增加 project_path 校验
- **检测能力**：`arch_audit` 新增函数级递归检测（AST 调用图 + SCC 环，直接递归 medium / 间接递归 high）；上帝模块改双标准综合判定（高扇出 或 高扇入+符号占比）
- **魔法数字过滤**（`code_simplifier`）：1000 不再无条件白名单，`settings`/`config` 文件名不再整体跳过
- **统计口径**（`pipeline_runner._gov`）：gov findings 的 `tier=Tier.MEDIUM` 硬编码改为 `tier=_tier_for(v.severity)`，summary 与 findings severity 口径对齐
- **LLM 输出鲁棒性**（`llm_integration`）：`_try_parse_json` 增强（代码块包裹/前后文字/单引号/Python 字面量/截断修复），强化 JSON 相关 system prompt
- **验证**：8 文件编译零失败；死代码检测修复后 3 个预埋死代码全部识别；MCP server 工具调用正常；CodeRabbit 复审 0 问题
- 版本号：4.9.7 → 4.9.8

### v4.9.7 — 修复 v4.9.6 重构引入的 MCP 工具调用崩溃

- **根因**：v4.9.6 大类模块级化瘦身时，`mcp_server` 的 `class Server` 内 38 个委托壳方法（`_ok` / `_validate_project_path` / `_review` / `_frontend` / `_scan_tool` 等）遗漏 `self` 参数，导致所有经 `self._xxx()` 调用的 MCP 工具在调用瞬间抛 `TypeError`（`Server._ok() takes 2 positional arguments but 3 were given`、`'Server' object has no attribute 'items'`），自举测试 45/45 全失败
- **修复**：38 个委托壳方法统一补齐 `self` 参数（纯机械修复，委托目标与内部逻辑零改动）
- **验证**：语法编译零失败；AST 复查 `class Server` 缺 self 方法归零；实测 MCP server 工具调用恢复正常（`coderef_task_status` / `coderef_scan_list` / `coderef_whitelist` 均正常返回）
- 版本号：4.9.6 → 4.9.7

### 补充修复（并入 v4.9.6）：CodeRabbit 复审 11 条 findings 全部修复

- 复审范围 CodeRabbit 全量审查（5 critical + 3 major + 3 minor），逐条验证全部真实后修复
- **重构残留治理（5 critical）**：`pipeline_runner` 模块级 `_fmt` 残留 `self`（elapsed 兜底分支 NameError）改用 `t0` 参数；模块级 `docs()` 去 `self` 化（局部 `t0`）；`run_single` 改为显式接受 Pipe 实例；`kg_query` 壳补 `**kwargs` 转发；`wiki_generator` 5 个模块级函数删除残留 `@staticmethod`
- **审计正确性（3 major）**：SEC-08 缓存键加 MD5 内容指纹（防 MCP 长驻进程跨审计返回过期判定）；GitNexus 三个解析函数 dict fallback 空列表继续尝试后续键 + 删 unreachable break；子图符号元数据移到节点构建后从 `raw_context` 回填（原在 `subgraph.nodes` 为空时填充，恒无效）
- **输出健壮性（3 minor）**：`flow_verify` 合同文件读取改 with 上下文管理器；`project_maturity_checker` 类别汇总表补表头/分隔行；`tech_debt_detector` 两处报告表头列对齐（6/6、5/5）
- 验证：7 文件编译零失败；`run_single`（修复前入口即 NameError）实测正常完成 agent 扫描（43 findings）；fallback 空跳/SEC-08 指纹隔离/表头对齐行为验证通过；回归测试 85/86 通过（唯一失败为环境基线，与本轮无关）；测试镜像同步 7 文件编译通过

### v4.9.6 — 存量复杂度债清零（4.9.x 系列最终闭环）

- **目标**：自审计（TechDebtDetector）暴露的全部 26 条 high 级复杂度/大类存量债在本版清零，5.0 不背技术债
- **高复杂度函数拆分**：A/B/C 三组并行重构 14 文件 18 函数（wiki_ir / wiki_compare / wiki_cross_verify / pipeline_runner / sca_checker / code_review / verify_findings / ast_signals / flow_verify / arch_detector / change_guard / memory_layer / replicate_engine / report_renderer），圈复杂度 21-28 → ≤8，认知复杂度 25-42 → ≤15
- **大类模块级化瘦身**（方法平移至模块级 + 类内委托壳，公开接口零变化）：Pipe 1749→588、CodeAnalyzer 1812→600、Server 1049→528、BusinessAnalyzer 2716→503、WikiGenerator 2737→332；AgentSecurityAuditor / GovernanceAudit / WikiIR 重构后已无 300 行以上大类
- **附带修复**：BusinessAnalyzer 评估质量分母 5→6；委托壳默认参数值批量还原（docs_read/render_report/_wiki 等 10 处）；staticmethod 壳 self 残留与模块级函数 self 引用清理
- **验证**：TechDebtDetector 全量重扫 351 条（high 0 / medium 54 / low 292 / info 5，high 明细为空）；回归测试 85/86 通过（唯一失败 `test_screen_available_without_llm_returns_empty` 为环境基线：本机 config.json 配置了 llm_api_key，"无 LLM"前提不成立，与重构无关）；测试镜像同步 29 文件后编译与 21 模块导入冒烟全过
- 版本号：4.9.4 → 4.9.6（v4.9.5 两次提交时版本文件未同步，本次一并补齐）

### v4.9.5 — CodeRef 自审闭环修复 + CodeRabbit 复审修复

- **自审闭环**：37 个 MCP 工具全量自测；聚合层按 detector + severity 双维分组，修复高严重度结果掩盖低严重度问题；`arch_audit` 拆分（find_sccs 圈复杂度 16→4）；`code_simplifier` 死代码检测 AST 化（排除 CJK 注释与 `type: ignore`，消除 110 条误报）；SEC-08/58 排除 `.md`/`config.json` 与 CLI 参数；tests/ 目录排除死类检测（修复 3 个 TestCase 误报）
- **结果完整性**：`mcp_server` findings 序列化保留 count/locations/line_start/line_end；`pipeline_runner` 相邻行合并时累计 count（修复 40 处计数丢失）
- **CodeRabbit 复审**：1 major + 4 minor（空壳校验 / 去重维度 / 导入解析 / 叙述边界 / 文案）

### v4.9.4 — CodeRabbit 全量审查（67 条）修复闭环（4.X 系列收尾）

- 对全仓库 89 个文件跑 CodeRabbit 全量审查，共 67 条 finding（major/minor），按两个并行组全部处理：官方 major 风险组 23 条、其余 minor 组 30 条、文档一致类与 core 小缺陷由主流程处理，`config/settings.py` 中开发者专属路径（个人用例路径）按指示保留
- 本轮主要修复项：
  - **图谱与调用链正确性**：`ast_signals` 仅模块级函数用裸名注册、类方法/嵌套函数用限定名，避免同名覆盖；`code_knowledge_graph` Go 处理跳过 `if(/len(` 等关键字避免误建 CALLS 边；`memory_layer` 覆盖率用文件集合判成员，`arch_audit` 递归模块经 self_edges 正确报为循环，`memory_quality` 识别 AsyncFunctionDef 并修正覆盖率守卫
  - **输入与输出健壮性**：`blind_spot_detector` 查询达 5000 行上限视为结果不完整而跳过（不把截断当完整）；`code_review` AST 上下文截断 + changed_lines 计入无换行末行；`wiki_generator` front matter 标量统一 YAML 双引号并转义；`pipeline_runner` 记忆同步仅增量策略触发、`_finding_to_dict/_from_dict` 持久化 count/locations
  - **资源与生命周期**：`operation_memory` 无变更分支防御缺键、`_summarize_deps` 仅取四类依赖组、删除死代码；`mcp_server` `_bg` 完成时写入 `finished_at` 让未轮询任务可被回收；`memory_quality` 用 `get_all_edges()` 单趟 + try/finally 保证 `kg.close()`
  - **CI/打包**：`ci_compile_check` 依赖一致性改双向（pyproject 核心依赖未写入 requirements 亦判失败）+ tomllib 缺失容忍回退
- 全量编译 65 个 py 文件零失败；再按顺序同步测试环境并跑关键回归
- 版本号：4.9.3 → 4.9.4

### 补充修复（并入 v4.9.4）：移除 settings.py 中的个人化路径

- `config/settings.py` 中硬编码的开发者个人工具根与用例 venv 路径从版本库移除，代码库恢复通用性
- 个人化工具根改由 `CODEREF_EXTRA_TOOL_ROOTS` 环境变量（分号分隔 glob）或 `config/config.json` 的 `extra_tool_roots` 字段注入（config.json 已被 .gitignore 忽略，不随版本库分发）
- 新增 `settings.omem_extra_tool_roots()`：环境变量优先、config.json 兜底；两个消费点 `operation_memory` 便携根探测、`wiki_generator` 的 git 便携路径解析均追加该结果
- 验证：全量编译零失败 + `diag_omem_env_missing` PASS + 组装后 roots 数/内容定向校验通过

### v4.9.3 — 收尾修复：front matter 引导文档缺 source/description

- 为 **OVERVIEW / ARCHITECTURE / INSTALLATION** 三篇引导文档显式注入 front matter：`description` 用文档用途文案、`source` 回填项目路径（`meta.project_path`，空则回退项目名）、`confidence=high`
- 修复前这三篇经 `_auto_front_matter` 默认生成，`description`/`source` 落空串，被判定为缺失；FLOWS/ENTRIES 因显式传值本就不受影响
- 验证：CodeRabbit 审计 0 findings、AST 校验 + 行为级回归（三篇 source/description 均非空）+ 全量编译通过

### 补充修复（并入 v4.9.3）：coderef_whitelist 非法 action 显式拒绝

- 参数契约矩阵暴露真实缺口：`coderef_whitelist` 传入非法 `action` 未校验枚举，此前静默落到默认 add 分支并返回成功
- 新增模块级常量 `WHITELIST_ACTIONS`（schema enum 与 `_wl` 校验同源）；非字符串 / 非枚举 `action` 一律返回结构化错误，与 `wiki_style`/`docs_read`/`strategy` 的枚举严格校验保持一致
- 验证：行为测试 6/6 PASS + 全量编译通过

### v4.9.2 — 工具注册表重构 + 打包与 CI + 依赖收敛（4.X 系列收尾）

- **工具注册表外置**：`core/mcp_server.py` 中 700+ 行工具 schema 自 `__init__` 抽取为模块级 `BUILTIN_TOOLS` 常量，`__init__` 缩减至 ~100 行；37 工具 / 36 handlers / 24 重型工具经逐字节 dump 一致性验证为零变化
- **打包规范化**：新增 `pyproject.toml`，支持 `pip install .`；依赖清单与 requirements.txt 双处同步维护，`pip install .[lang]` 可选开启 tree-sitter 多语言解析
- **CI 编译检查**：新增 `ci_compile_check.py`（编译校验 + 依赖一致性 + 裸 except/print 趋势统计）与配套 GitHub Actions（`.github/workflows/compile-check.yml`）；依赖比对按 PEP 503 归一化包名、忽略 `-r`/`-e` 等指令行、并纳入 `[lang]` 可选依赖做双向核对，Python 3.10 自动回退 tomli
- **依赖收敛**：移除 pandas / tqdm / pathspec / ollama 四个死依赖（安装体积与时长下降）
- **坏味道修复**：`_SINGLE_TOOL_LABELS` 提升为模块级常量，消除注册表内对 `self` 的非法引用（重构期引入、已修复并回归验证）

### v4.9.1 — v4.9.0 交接待修复（六项）

- **渲染层崩溃**：`wiki_ir.ir_to_mermaid/ir_to_markdown` 增加类型护栏，非字典/非列表/不可哈希节点 id 安全降级不再崩溃
- **必填校验**：`validate_ir` 校验节点必填字段（name/file_path），缺失返回结构化 `IR_MISSING_FIELD` 而非静默通过
- **Front Matter**：`wiki_generator._build_front_matter` 为 FLOWS / ENTRIES 文档回填 source/description 元数据
- **输入校验**：`docs_read` 负 max_chars、`wiki_style` 非法枚举均返回结构化错误，不再静默回落
- **CodeRabbit minor**：`wiki_style` 仅「key 缺失」才回落默认，显式空串/0/False 等价非法值被拒绝

### v4.9.0 — 版本号提升 + CodeRabbit 复审修复 + 工具数对齐

- **版本号提升至 4.9**：Wiki 十项增强后主版本号由 4.8 提升到 4.9
- **CodeRabbit 复审修复**：增量路径使用陈旧缓存（`skip_cache`/`invalidate`）、Last-good 备份目录重复计数、引用修复笔记误计入 errors、增量忽略未提交变化、wiki_ir 不可哈希引用崩溃、Mermaid 节点 id 校验失效、引用修复覆盖 front matter/证据锚点（body 与确定性前缀分离）、证据锚定每符号一个 git 进程（commit 缓存 + 路径归一）、预算拒绝串误写入文档（`is_error_response` 判定）、README 工具数一致
- **工具数**：README 工具清单标题与当前实现一致（37 个，含操作记忆工具）

### v4.8.10 — Wiki 工具十项增强（增量同步 / JSON-IR / 证据锚定 / 成本封顶）

- **增量同步（R1）**（`wiki_generator`）：以 `.last-update.json` 记录上次已文档化 gitHead，git 可用时对比变更文件，仅重新生成受影响模块文档；变更文件数超过阈值（50）自动降级全量重建；无 git 环境优雅降级
- **front matter 标准化（R2）**：每篇文档自动注入 YAML 头（type/title/description/tags/source/confidence/generated_at），confidence 与交叉验证徽章映射（confirmed→high / partial→medium / unverified→low / missing→none）
- **证据锚定（R3）**：模块文档附「证据锚定」区块，链接到 Git 文件+行号+commit，让非技术人员能追溯每段描述的确证来源；Last-good 门控把全校验通过的产物备份到 `.last-good/`，生成失败时保留上次可用版本
- **JSON-IR 分离（R4）**（新增 `wiki_ir`）：LLM 先输出结构化架构事实 JSON → schema 校验（节点 id 唯一、边/入口引用完整）→ 再渲染 Mermaid/Markdown；LLM 不可用时从知识图谱确定性提取 IR 兜底；容错解析修复 LLM 截断的 JSON（引号/裸 token/未闭合括号）
- **架构图可视化（R5）**（`diagram_generator`）：`generate_mermaid_embed` / `generate_arch_markdown` 生成可嵌入 wiki 的 Mermaid 图 + 节点清单表，节点数不足阈值时自动省略避免噪音
- **用户授权层（R6）**：项目根 `INSTRUCTIONS.md` 只读解析（`## 章节` → 内容），作为 user-level 上下文拼入 LLM 的 user prompt 约束文档 scope/优先级（不进入承载事实约束的 system prompt）；生成器绝不覆盖该文件
- **Agent 指针集成（R7）**：`enable_agent_pointer` 在项目根维护 `AGENTS.md` 的 `<!--CODEREFF:START/END-->` 区块指向 wiki 入口，区块外正文原样保留
- **架构快照比对（R8）**（新增 `wiki_compare`）：`.arch-snapshot.json` 原子快照 + `compare_snapshots` 五类变更收据（added/removed/changed/moved/rerouted），输出 Markdown/JSON 变更报告（viewer-only，不做风险推断）
- **Mermaid 自愈（R9）**（`wiki_cross_verify`）：`verify_mermaid` 校验 fence/节点 id/括号配对，失败时 `fallback_mermaid` 降级为 text fence 并附 `<!-- mermaid-fallback -->` 标记
- **成本/输出封顶（R10）**（`llm_integration`）：单次生成 LLM 调用预算（200 次）用尽即拒绝并提示 `reset_budget()`；单文档输出字符上限（12000）超限截断并附加标记

### v4.8.9 — 操作记忆「一次恢复」与上下文丢失强制 gate

- **操作记忆一次恢复摘要**（`operation_memory` 新增 `recover`）：`coderef_operation_memory_recover` 一次调用返回关键工具位置（`env_tool`，含 git / python / wsl / coderabbit）+ 已确认的约定 / 踩坑 / 决策摘要 + 待人工确认项。AI 在上下文丢失后最小成本拿回「东西在哪儿、过去的规范是什么」，避免多次 `query`/`find` 的截断丢失
- **上下文丢失恢复升级为强制 gate**（`SKILL.md` 工作流 E）：涉及 `git` / `push` / `CodeRabbit` / `Release` 等工具或约定类操作必须先走 `recover`；禁止在未查询操作记忆前满 PATH 找工具或直接抓外部连接器（GitHub 等）。修复「AI 上下文丢失后绕过自带确定性记忆层、盲目抓外部连接器」的失败模式

### v4.8.8 — 流程验证输入校验与跨语言动态类名注入面检测

- **流程验证空 `steps` 输入校验**（`mcp_server` `_flow_verify`）：`coderef_flow_verify` 对 `steps` 做非空与类型校验——空数组/`0`/`None`/空串/纯空白统一返回结构化错误（`steps 不能为空`），不再被 `or []` 静默吞掉后返回假成功；非数组（如数字）也明确报错，与 `project_path` 校验风格一致
- **跨语言动态类名注入面检测**（`flow_verify` `cross_lang_contract_scan`）：新增 `cross_lang_dynamic_class_inject` 信号——识别 Go 侧 `map[string]any{...}` 含 `plugin`/`class` 动态键、值为运行时变量（非字符串字面量）并经 `json.Marshal` 序列化转发跨语言执行面的注入风险（真实项目 `internal/app/plugin/php/multi_pool.go:117` 盲区）。与前端硬编码 `pluginName='x'` 的断链检测互补：此为"有实现但类名由外部 payload 动态决定"的动态插件名注入面

### v4.8.7 — 盲区缺陷全量修复与健壮性加固

- **审计结果跨项目隔离**（`pipeline_runner`）：`audit_findings.json` 与报告文件名按项目哈希命名，`_latest_report` 按项目过滤，修复 `strategy=no_change` 复用时返回他项目报告的串扰
- **治理跨地区冲突检测**（`governance_audit` 新增 `_scan_crossregion_conflicts`）：递归索引 `rules/` 子目录 md，检出 `IRON-GOV-03` 领土主权表述冲突与 `IRON-GOV-04` 统计版本差异污染，纯文档合规项目不再系统性漏检
- **project_path 严格校验**（`mcp_server` 新增 `_validate_project_path`）：拒绝空串/相对路径（`..` 越权扫描上级目录），目录不存在返回结构化错误而非空成功，调用方可区分「路径写错」与「项目无缺陷」
- **Agent 跨语言/资源规则补盲**（`agent_security_auditor`）：新增 Java 规则（`AGENT-SEC-60~65`：SQL 注入/Spring 未鉴权/反序列化/路径穿越/SSRF/硬编码密钥）、前端安全（`AGENT-SEC-66~68`：Vue `v-html` XSS/token 拼 URL/Node 无鉴权）、资源泄漏（`AGENT-SEC-69` PIL 批量句柄泄漏、`AGENT-SEC-70` 外部长任务轮询无超时）
- **analysis_cache 可配置**（`code_analyzer`）：新增 `_resolve_cache_dir`，支持环境变量 `CODEREF_ANALYSIS_CACHE` 或 `settings.CODEREF_ANALYSIS_CACHE` 覆盖默认缓存目录，供测试/CI 隔离、避免跨项目污染
- **tool/strategy 枚举严格校验**（`mcp_server`）：`coderef_scan` 的 `tool` 维度与 `coderef_audit` 的 `strategy`（`auto`/`full`/`incr`/`no_change`）改为大小写敏感白名单校验，非法值返回结构化错误，不再静默放行（此前 `Gov`/`bogus_strategy` 被按默认执行）
- **跨地区检测 CodeRabbit 复审修复**（`governance_audit` `_scan_crossregion_conflicts`）：按 CodeRabbit 复审结论消除误报——`IRON-GOV-04` 仅当并存统计均缺乏来源/范围/版本标注时报违规（带 `数据来源`/`2022年全年` 等标注的合规趋势报告不再误报）；扫描目录排除列表移除 `docs`（现可检出 `docs/*.md` 跨区域冲突）；主权独立主体表述排除「禁止/不得/例如/假设」等否定与政策举例语境（合规红线举例不再误判为实际独立主张）

### v4.8.6 — 并行 SCA 与治理新规则

- **并行 SCA 漏洞查询**（`sca_checker`）：依赖漏洞核查改用 `ThreadPoolExecutor`（8 worker）并发查询 OSV，配合源码缓存，显著缩短大项目依赖扫描耗时，避免单个工具 900s 轮询超时
- **治理/安全规则新增**（`governance_audit`）：`IRON-SEC-01` 硬编码凭据（变量名含 Key/Secret/Token 且值为长随机串）、`IRON-SEC-18` 空鉴权中间件（闭包直接 `return` 不做权限校验）、`IRON-GOV-02` 伪科学术语检测
- **WSL 子系统工具探测**（`operation_memory`）：新增 `_locate_wsl_launcher`（先 PATH、再 `SystemRoot\System32` fallback，解决 PATH 缺 System32 时连 wsl.exe 都找不到）与 `_find_wsl_tool`（经 wsl.exe 用 `command -v` 探测、失败回退 `~/.local/bin`），可定位 WSL 内工具（如 coderabbit 在 `/root/.local/bin`）；`query(tool)` 补齐 `env_tool` 分类覆盖，避免"探测到了却查不到"。`skills/coderef-mcp/SKILL.md` 工作流 E 增加工具定位引导，避免 AI 每次满 PATH 摸索
- **CodeRabbit 复审修复**：`governance_audit` 的 IRON-SEC-01 硬编码凭据规则支持 Go `:=` 短声明、词边界防 `publicKeyHash` 类误报、错误码排除区分大小写、共享 `CREDENTIAL_VALUE` 提取器；`sca_checker` 源码缓存改为按规范化项目路径隔离，修复同一实例跨项目/重扫复用旧缓存导致漏洞利用面误判

### v4.8.5 — 盲区缺陷修复（跨语言契约 / Agent 跨语言规则 / 原子写 / 后台超时兜底）

- **跨语言插件契约断链检测（`flow_verify` 新增 `cross_lang_contract_scan`）**：前端 Vue/JS 的 `pluginName` 与 Go 的 `action` 引用同名业务插件，但 PHP `plugins/` 目录无对应实现即报断链，检出`php/worker.php` 动态插件加载失败/静默降级。插件发现限定 `php/plugins` 根目录（`os.scandir`），扫描时剪枝 `php/plugins` 与 `components/plugins` 子树避免自引用；`php_plugins` 为空时不再提前返回，缺失插件照常上报。`_cross_lang_nodes` 收敛为只输出结构化节点元信息（name/type/file/line），不再 dump docstring，消除文本子串误命中
- **Agent 跨语言规则补盲（11 条，AGENT-SEC-44~55）**：`agent_security_auditor` 新增 PHP 生产调试开关、动态类名注入、任意 action 调用、跨语言 RPC 日志泄漏、SSRF 转发、密钥泄漏，及 Go 的 ticker 泄漏、并发写、未过滤输入、RAG 图谱投毒、跨语言插件类名注入（Go→PHP 执行面）等规则，修复 真实项目 PHP/Go 跨语言缺陷漏报（agent 命中 0/9→6/9）。规则含文件级确证防误报：SSRF sink 确证、证据绑定到具体参数、同变量真值/同函数确证
- **OperationMemory 并发写原子性**：`_write_atomic` 用线程 id 唯一临时文件 + `os.replace` + 5 次指数退避重试，修复 Windows 上并发写 `os.replace` 的 WinError 5/32 竞争；`finally` 清理临时文件，per-project 锁改 `RLock` 串行完整发布序列（ledger/BRAIN/timeline）
- **后台任务超时兜底**：`coderef_scan` 纳入后台执行（HEAVY_TOOLS），后台任务超 860s 返回部分结果并建议 `resume=true`/incr 分片，修复大项目单工具 900s 轮询超时无返回
- **CodeRabbit 三轮复审修复**：agent to_report 补 `deserialization` 类别渲染、AGENT-SEC-54/DESER 证据绑定、AGENT-SEC-55 函数正则支持 receiver 方法、AGENT-SEC-51 仅认同变量 `Stop`；MCP 无部分数据不返 partial、删除不支持的 docs resume 指引、`MCP_SETUP.md` 更新 `coderef_scan` 后台契约；flow_verify 剪枝条件仅匹配 `php/plugins` 与 `components/plugins` 结尾业务目录照常扫描

### v4.8.4 — 缺陷命中回归修复（AST 静态信号 / 合并详情展示 / Agent 安全增强）

- **AST 静态信号扫描（新增 `ast_signals` 模块）**：`flow_verify` 集成 `scan_project`，针对调用图无法覆盖的缺陷补充四类可验证信号——`detect_silent_except`（except 块内无日志/无 raise 的静默吞异常）、`detect_unused_helpers`（`_` 开头私有函数从未被调用，排除测试文件）、`detect_missing_param_pass`（调用缺少关键维度/尺寸参数透传）、`detect_dir_contract_break`（目录契约命名不一致，如缓存目录 batch_id 与时间戳命名并存）。提示性信号不计 `ok` 失败，避免把"提示"误判为"流程失败"
- **Agent 安全补盲**：`agent_security_auditor` 新增 PromQL 注入（`http://`+`query` 拼接）、认证绕过（`@login_required` 缺失 + 前导断言）、空中间件空认证检测等模式，修复真实项目 等 PromQL/鉴权类缺陷漏报
- **合并项详情展示**：`pipeline_runner` 新增 `_row_desc`，HIGH 表格行包含合并项 `count` 与 `detail` 关键内容（如治理违规关键词），修复合并后 detail 丢失导致报告文本缺关键词
- **注册表维度修正**：redink pipeline 断链归入 `flow_verify` 维度（此前误标 `flow_verify`+`agent`，导致 agent 维度误报漏报并存）
- **CodeRabbit 复审修复**：`ast_signals` 函数签名改用限定符号（`ClassName.method` 区分同名方法，避免签名互相覆盖导致 `detect_missing_param_pass` 误判）；`pipeline_runner` 的 agent 严重度改用 `_tier_for` 正确映射 HIGH、相邻行去重键叠加 `[risk_id]` 避免不同风险类型被误合并、MD5 噪声规则的 `detail_exclude` 补充英文安全词；`agent_security_auditor` 的 URL 拼接豁免收紧为仅 HTTP(S) 前缀（避免 `Use /help for {x}` 类 prompt 被误判）、AGENT-SEC-40 敏感路由降为 medium（仅凭路由路径为低置信度信号）

### v4.8.3 — 并行盲区补修（跨语言 / 参数契约 / 供应链 / Agent 安全）

- **Agent 安全补盲**：`agent_security_auditor` 新增 LLM 命令执行（`ShellTool` / `tool.run`）、LLM 生成 SQL 注入、FastAPI 路由认证缺失、密钥明文落盘（`save_env` / 写 `.env`）检测，修复 agent 维度漏报
- **跨语言安全检测**：`agent_security_auditor` 新增 Go（`exec.Command` / SSRF）/ Node.js（`child_process` / `eval`）/ PHP（`system` / `eval`）命令执行与反序列化检测，并下沉 SSRF、路径遍历、`--no-sandbox`、信息泄露通用模式
- **参数契约数据链**：`ast_parser` / `graph_closure` / `memory_layer` 打通调用参数 `keyword_args` 全链路（解析→加载 CALLS 边→写入图谱）；`flow_verify` 新增 `param_contract_scan` 参数契约检测与 `_normalize_params` 归一化
- **Go 知识图谱与流程补盲**：`code_knowledge_graph` 新增 Go 函数定义与调用解析；`flow_verify` 入口未命中时仍输出跨语言 Go 节点（`_cross_lang_nodes`），避免多语言项目整链短路
- **SCA 依赖补盲**：`sca_checker` 新增过时依赖 / 未锁定依赖 / 供应链运行时自动安装检测；对无依赖清单项目通过 `import` 提取第三方包（`_detect_unpinned_from_imports`），并接入 `pipeline_runner` findings
- **治理与 Prompt 补盲**：`governance_audit` 新增文档（`.md` / `.skill`）密钥明文与审查绕过表述检测（`_scan_doc_secrets`）；`prompt_compliance` 新增治理提示检测；`prompt_extractor` 扩展 `.txt` 文件扫描
- **便携工具探测**：`settings.py` 便携根子目录新增 `python`，支持自动探测项目内嵌解释器

### v4.8.2 — 证据审计修复 11 项缺陷（爆发合并 / 多语言 / Prompt 提取）

- **爆发式合并修复**：`_burst_merge` 保留组内最高严重度（此前 7 个 critical 被误降为 LOW），记录全部位置到 `locations` 字段，按 `count` 加权计数（此前 54 处爆发被压成 4 条），并保留原始 `detail`（含命中代码行）供符号级证据核验
- **SCA 多生态支持**：`sca_checker` 新增 npm（`package.json`）/ Go（`go.mod`）依赖解析并按 OSV 生态查询，修复 lodash/express 等 npm 高危依赖漏检（此前仅扫 Python）
- **跨语言安全模式**：`governance_audit` 补充 Go（`exec.Command`）/ PHP（`unserialize`、`system`）/ Java / Node.js 命令执行与反序列化检测，修复非 Python 项目关键风险漏报
- **Prompt 提取多语言**：`prompt_extractor` 扩展扫描 Markdown（`SKILL.md` / `prompts/**` `/agent.md`），修复仅扫 `*.py` 导致的提示词注入风险漏检
- **注入定位闭环**：`prompt_compliance` 注入 findings 携带源文件，保证风险可定位到具体文件
- **健康分口径统一**：`run_single` 与全量 `_compute_health` 统一单维度健康分，空项目返回 `N/A` 而非误报
- **agent 维度兜底**：`agent_security_auditor` 对含 LLM 依赖的项目仍正常产出风险，修复"有 LLM 项目健康分异常"；`blind_spot_detector` 修复空标题条目

### v4.8.1 — 换行符正规化 + CodeRabbit 修复 + 操作记忆来源声明

- **换行符正规化**：新增 `.gitattributes`（`* text=auto`），全仓文本统一为 LF 存储，根治历史遗留的 CRLF/LF 行尾混乱（此前每个文件都被误判为全量改动）
- **修复 operation_memory 写入可靠性**：写入失败显式传播（任一持久化失败返回 `error`）、数据目录可配置（`OMEM_DATA_DIR`）、原子写改用 PID+时间戳唯一临时文件、增量快照比较提到资源扫描之前、LLM 不可用时生成 pending-human 待办条目
- **修复 flow_verify 判定**：拆分"存在性确证（`ok`）"与"顺序确证（`order_confirmed`）"两个标志，避免把"在管线但顺序未确证"误标为失败；`render_report` 优先报告知识图谱缺失根因；`entry_chain` 输出稳定排序保证确定性；`cross_module_flows` 去重键改用完整文件路径；`render_html` 全部插值做 HTML 转义
- **修复 llm_integration JSON 解析**：片段提取定位整个响应中最早的结构分隔符，正确处理 LLM 在 JSON 前加说明文字导致顶层类型误判的场景
- **设计借鉴声明**：README 新增「设计借鉴」章节，操作记忆层标注结合 mindmuxai/brain.md（Apache-2.0）与 TencentDB-Agent-Memory（MIT）；`BRAIN.md` 产物与模块 docstring 同步携带来源声明
- **操作记忆固化**：审查发现 12 条 pitfall + 14 条 decision 已写入本地操作记忆层（`data/operation_memory/`，属运行数据、不入库），可被 `coderef_operation_memory_query` 在本地检索恢复

### v4.8 — 新增 AI 操作记忆层

- **操作记忆层（`coderef_operation_memory_sync` / `query` / `find` / `status`）**：为 AI 辅助编程提供"东西在哪儿、从哪儿来、到哪儿去、过去规范是什么"的持久记忆。解决对话过多后上下文丢失的课题——主要存资源位置（git 便携包、模型权重、测试工具、API 存放处、开发背景报告、外部依赖来源），而非存具体代码
- **静态审计 + LLM 提炼混合**：资源发现走确定性静态审计（`operation_memory.py`），隐性知识（决策/约定/踩坑）由 LLM 从文档提炼，无 API Key 时诚实降级为待人工确认
- **旁目录探测**：除主开发目录外，探测家目录 / 数据目录等旁目录下的资源位置，仅记录位置不记录内容，兼顾隐私
- **增量同步**：基于 mtime+size 快照比较，文件无变更时跳过全量扫描，大幅提升同步速度
- **便携工具探测**：`env_tool` 探测便携根下 bin/cmd/mingw64 等子目录，解决本地使用便携 git 等工具不在 PATH 时找不到的问题
- **工具数**：4.8 增至 32 个 MCP 工具（新增 4 个操作记忆工具；当前 4.9.0 为 37 个，含 5 个操作记忆工具）

### v4.7.4 — memory_status 后台化

- **修复较大项目同步超时**：`coderef_memory_status` 纳入后台化重型工具（HEAVY_TOOLS），避免 120s 同步超时，改用轮询获取结果

### v4.7.3 — 扫描忽略依赖目录

- **修复超大项目审计超时**：扫描忽略 `vendor` / `bundle` 依赖目录（PHP/Node 等项目的依赖目录动辄数千文件），避免超大项目审计超时

### v4.7.2 — flow_verify 入口三段式匹配修复

- **支持 `模块.类.方法` 层级限定**：`coderef_flow_verify` 入口符号匹配支持三段式层级限定，精确定位方法级入口

### v4.7.1 — MCP 服务端 stdin 修复 + 前端 LLM 审查并行化 + JSON 解析加固

- **修复 MCP 服务端 stdin**：修正服务端标准输入读取，保证 MCP 长连接稳定
- **前端 LLM 审查并行化**：LLM 审查节点改用线程池并行（8 workers），单节点 120s 超时、总预算 600s，全项目审查耗时从串行数小时降至 170s 量级
- **JSON 解析加固**：`_try_parse_json` 优先按首字符判断数组/对象、剥离 ```` ```json ```` 代码块，修复 JSON 数组被误当对象导致 findings 从 204 掉到 242 的解析问题
- **subprocess 防挂起**：子进程调用增加防挂起机制，避免长时间无输出时卡死

### v4.7.0 — 创新复刻收口：LLM 协助排查

- **新增 `coderef_innovation_review`**：创新复刻的 LLM 协助排查工具，补上"创新确认 + 复刻排查"这条需要语义判断的链路。让 LLM 阅读源项目的管线设计（知识图谱调用链闭包）+ wiki 文档，判定三点：该设计是否确属创新 workflow（区别于已知/常见模式或静态能力标签误命中）、管线调用链与 wiki 人话描述是否一致、复刻到目标项目是否合理（提供 `target` 时）
- **wiki 来源「生成+兜底」**：优先读源项目已有 wiki（`coderef_docs_read`），无则用 WikiGenerator 生成兜底再排查
- **诚实话护栏**：确定性管线摘要（图谱调用链闭包、采用模块、入口）照常给出，不依赖 LLM；LLM 结论明确标注"AI 判断，非确定性事实"，不下"必须复刻"指令；无 API Key 时硬阻断（`is_available()` 判定），只给确定性管线摘要，不产出降级/占位判断
- **修复入口符号提取缺陷**：入口符号改为"真实源码顶层符号优先、资产蓝图 entry_points 仅作补充"。此前若蓝图声明理想模板入口（如 `with_retry`，源项目未必真实存在），会到图谱里查不到、调用链闭包为空；修复后从采用模块源码提取真实符号，能命中图谱提取确定性调用链
- **工具数**：32 个 MCP 工具（31 + 1 新增）

### v4.6.0 — 工具收敛：Prompt 治理合并 + 复刻落地（闭环收口）

- **合并**：`coderef_prompt_mgmt`（资产生命周期：版本/对比/AB）与 `coderef_prompt_audit`（合规审计）合并进统一入口 `coderef_prompt_governance`（overview / assets / audit / cross_module）。两个旧工具从 `tools/list` 移除，但保留 handler 兼容转发（返回 `deprecated` + `migrate_to` 迁移提示），旧调用不中断
- **降级**：`coderef_interpret` 移除重复的 verify / verify_html action（论断核验本就复用 `coderef_verify_findings`，两处重复实现），调用时明确降级提示迁移到 `coderef_verify_findings`，不再静默返回"未知 action"
- **补全**：新增 `coderef_replicate_apply` 复刻落地工具，把 4.4 的复刻铺排真正落到目标项目——写入 template_code 骨架 + patch_suggestion / migration_guide 说明，生成落地清单 manifest。诚实话护栏：只落地"确定性可给"内容，不自动接入目标源码；默认不覆盖已存在同名文件（冲突如实标注）；template_code 缺失明确标注待补全
- **工具数**：31 个 MCP 工具（32 − 2 合并 + 1 新增）

### v4.5.1 — 修复：DeepSeek V4 兼容 + 审查缺陷

- **修复 DeepSeek V4 空响应**：默认 base_url 改为官方 `https://api.deepseek.com`（去掉旧 `/v1` 后缀），默认模型改为 `deepseek-v4-flash`。`chat_completion` 支持 `extra_body` 传 `thinking` 参数，并在 `message.content` 为空时回退读取 `reasoning_content`（V4 推理模型输出优先写入该字段），彻底消除"调用成功但返回空串"的误判。同步更新 `setup.bat`、`README`、`MCP_SETUP.md` 与 `config/config.json`
- **修复 `_discover_workflows` 缺 fallback**：原先仅走 Prompt 工作流，无 Prompt 时静默返回 `None`。现补全三级降级链（Prompt → LLM+知识库 → 规则启发式），绝不静默返回空/None
- **修复 `frontend_inspector` 运行时 URL 无白名单校验（SSRF）**：`_runtime_review` 新增协议与 host 白名单校验，仅允许 http/https 且 host 为本地/内网前缀，越权 URL 一律拒绝访问并降级为静态分析
- **修复 `arch_audit` 用 `or` 回退导致 0 值被忽略**：`fan_out_threshold` / `large_symbol_threshold` / `scc_min_size` 改为 `is not None` 判断，显式传 0 不再被误回退为默认阈值
- **审查并收编 `verify_findings.py` 未提交改动**：路径穿越防护（`realpath` + `commonpath` 限定项目根内）、symbols 形态健壮化（非法类型回退文本启发式提取）、`entry` 参数透传到证据标签

### v4.5.0 — 平台整合：Prompt 治理 + 人话解读（闭环落地）

- **Prompt 治理平台（`coderef_prompt_governance`）**：把 4.3 的资产生命周期（`prompt_asset_manager`）与合规审计（`prompt_compliance`）编排成统一治理视图。`action=overview` 一次调用拿到 资产生命周期 × 合规审计 × 跨模块一致性 总览；`assets` 生命周期（版本/对比/AB）；`audit` 合规审计；`cross_module` 跨模块漂移专项（同一角色/场景在多模块的同名定义漂移）。纯规则、确定性、不引入 LLM
- **人话解读平台（`coderef_interpret`）**：让非编程人员一屏看懂 AI 项目的真实状态。`action=health` 健康总览（确定性人话健康分 + 高危清单 + 图谱/合规背景，未审计时诚实提示不给分）；`dashboard` 健康仪表盘 HTML；`verify` / `verify_html` 论断人话核验（复用 `verify_findings` 确定性 verdict）；`wiki` Wiki 生成（无 LLM 诚实阻断）；`prompt` Prompt 治理总览；`assets` 已固化资产人话解读
- **诚实话解读闭环**：所有"人话结论"只来自确定性原语（健康分/审计/图谱/合规/论断核验），不引入 LLM 给结论；依赖 LLM 的能力（Wiki）在无 API Key 或无依赖时诚实阻断，绝不产出占位内容伪装成"已解读"；未审计 ≠ 无风险，绝不臆断项目健康

### v4.4.0 — 复刻铺排引擎（创新建设翼闭环）

- **复刻铺排（`coderef_replicate`）**：检测目标项目对某已固化资产（蓝图）的采用缺口，并生成可复刻指引（steps + entry_points + verified_findings）。缺口判定是确定性签名比对，只报告"有/没有"，不臆断"该不该采用"；工具是审计工具，不自动改代码
- **蓝图固化（`coderef_asset_blueprint`）**：把复刻铺排得出的确定性结论（entry_points / verified_findings）写回资产蓝图，只填确定性可填字段，不臆断 steps
- **innovation 引擎增强**：`WorkflowAsset` 支持 `blueprint` 字段（结构化复刻蓝图），`prompt_asset_manager` 支持蓝图参数，让"已验证采用的设计"沉淀为可复刻蓝图

### v4.3.0 — 确定性核验 + Prompt 合规（驾驭翼咽喉）

- **论断确定性核验（`coderef_verify_findings`）**：把 LLM/CodeRabbit 给出的论断用知识图谱 + 静态原语核验——论断引用的代码目标是否真实存在、是否在关键管线内。verdict（确证/证伪/部分确证/存疑）由确定性逻辑打出，诚实话标签来源分离，LLM 无权改结论；无确定性证据一律存疑，绝不默认 confirmed
- **Prompt 合规审计（`coderef_prompt_audit`）**：注入风险（提示注入模式化特征）+ 一致性（跨模块/同角色 Prompt 漂移）。纯规则、确定性、不依赖 LLM
- **谦逊让步**：所有新工具与既有工具的 description 明确标注"可靠性边界"（[可靠性] 段），让调用方 AI 知道哪些是确定性结论、哪些需要人工复核，不夸大能力

### v4.2.11 — CodeRabbit 复审 77 项修复（3 Critical + 44 Major + 30 Minor）

- **Critical 修复**：`wiki_generator` 空路径导致 `os.path.relpath('')` 崩溃 → 新增 `_emit` 辅助方法统一守卫；`report_renderer` 的 `_safe_link` HTML 属性注入 → 新增 `_attr` 函数转义引号；`prompt_asset_manager` 的 `_action_compare` IndexError → 先过滤再截断 + 空列表守卫
- **安全修复**：`flow_verify` HTML 插值未转义（XSS）；`agent_security_auditor` pickle 信任豁免移除 + docstring toggle 误判 + param_shadow 风险未渲染；`business_analyzer` 分隔符注入；`owasp_compliance` docstring toggle + markdown 管道符转义
- **数据安全**：`change_guard` 的 `allow_autocommit` 默认改为 False；`design_registry` 损坏注册表覆盖前先备份；`mcp_server` 任务结果不再首次读取即删除
- **功能正确性**：`code_knowledge_graph` IMPORTS 边丢弃包限定导入；`code_analyzer` 循环 IO 索引混乱 + 删除 1180 行不可达死代码；`innovation_engine` intent 过滤分母不一致；`sca_checker` [project] 段假依赖 + packaging 缺失版本比较误判 + OSV 网络失败静默；`governance_audit` 裸 def get/post 误判 + 字符串字面量 vs 变量名比较；`review_strategy` advise() 永不返回 no_change
- **稳定性**：`graph_closure` 连接泄漏 + 自动创建空数据库；`gitnexus_client` shell=True 缺包 60s 超时；`llm_integration` APIRetryError 导致 OpenAI 异常分类全跳过；`integrity_checker` / `innovation_propagation_detector` 未初始化属性
- **性能**：`memory_layer` 持久化剔除函数体 + `commonpath` O(n²) 优化；`frontend_inspector` LLM 调用新增上限 50
- **跨平台**：`wiki_generator` ENTRIES/FLOWS 路径分隔符归一化；`blind_spot_detector` 索引路径归一化
- **架构改进**：`innovation_engine` 不再访问检测器 9 个私有成员，改用公共 API；`tool_registry` ALL_AUDIT_TOOLS 从 SINGLE_TOOLS 派生消除重复维护；`tech_debt_detector` monkey-patching 改为参数传递
- **文档对齐**：LICENSE 占位符、MCP_SETUP 依赖列表与知识图谱触发路径、README API Key 可选说明、启动日志版本号

### v4.2.9 — 架构探测 + 无 LLM 硬阻断人话报告 + HTML 图谱状态修复

- **架构探测（内部增强）**：业务分析前新增轻量静态架构探测器，自动识别项目架构类型（分层/单体、Web/API、事件驱动、插件化）并提取调用图之外的入口信号（Web 路由端点、事件监听器、插件入口）。当项目入口发生在函数图之外时，入口层识别不再只依赖函数出度/入度——入口发现更贴合真实架构，业务全景分析的准确性提升
- **无 LLM 时硬阻断人话报告**：`coderef_docs`（Wiki）与业务报告（`generate_business_report` / `analyze_project_business`）依赖 LLM 才能产出，未配置 API Key 时在入口直接**明确阻断**并提示"请先配置 API Key"，不再跑完整流程、不再降级产出机械/占位内容，避免编程 AI 拿到"看似成功实为降级"的报告。底层 `LLMIntegration` 新增 `is_available()` 统一判定可用性
- **确定性分析不受影响**：审计、知识图谱、架构诊断、流程验证、变更守护、OWASP 等纯静态能力无 LLM 照常可用，服务始终可用
- **修复 HTML 报告图谱误标"未执行"**：`docs()` / `audit()` 实时管线未预置维度状态，导致 HTML 中已构建的知识图谱被误标为"未执行"。现在 `_render_html` 在维度状态为空时依据真实产物（图谱 + findings）自动补全，各维度如实展示执行状态

### v4.2.8 — 重型工具默认后台执行（适配所有 MCP 客户端的超时限制）

- **修复 MCP 工具超时（REQUEST_TIMEOUT）**：此前 `coderef_memory_sync` 等重型工具同步执行，大项目全量扫描会在 Trae 等客户端对单次 `tools/call` 的超时窗口内未完成，导致超时失败、其余工具异常。v4.2.8 起**重型工具默认后台执行**：调用立即返回 `{"status":"running","task_id":"xxxx"}`，由外层 AI 轮询 `coderef_task_status(task_id)` 取最终结果，不再撞超时
- **默认后台的工具**：`coderef_audit` / `coderef_docs` / `coderef_review` / `coderef_frontend` / `coderef_report` / `coderef_audit_advisor` / `coderef_architecture` / `coderef_memory_sync` / `coderef_memory_quality` / `coderef_owasp` / `coderef_innovation` / `coderef_asset` / `coderef_innovation_review` / `coderef_change_guard` / `coderef_change_report`；轻量工具（`coderef_scan` / `coderef_query` / `coderef_whitelist` / `coderef_docs_read` 等）保持同步快速返回
- **显式控制**：所有工具支持 `background` 参数，`background=False` 强制同步（小项目想立即拿结果）、`background=True` 强制后台；统一后台分发避免散落的 if/elif，handler 与 `coderef_task_status` 全工具可用
- **收敛统一分发**：`_call` 收敛为「统一 handler 映射 + 统一的 `background` 决策」，消除散落分支导致的重型工具被同步执行的遗漏；`_run` 统一走 `_handlers` 分发，后台线程与同步路径执行任意工具

### v4.2.7 — SCA 本地 CVE 库去敏感化（降低杀毒软件误报）

- **降低杀毒软件误报**：`coderef_audit` 的依赖扫描（SCA）本地 CVE 库（`LOCAL_KNOWN_VULNS`）漏洞描述由英文高危特征串（如 "Arbitrary code execution"、"Path traversal" 等，易触发 `HEUR:HackTool/VulnScan` 一类启发式误报）改为中文中性措辞。CVE 编号、影响版本、严重度、修复版本（`fixed_version`）全部保持不变，审计报告结论不受影响；OSV 在线查询结果不经本地文件，亦不受影响
- **说明**：CodeRef-AI 是合法开源安全审计工具，不包含任何恶意代码。若你的杀毒软件仍误报，请将项目目录加入排除项，并向杀毒厂商提交误报申诉（详见上方「杀毒误报处理」）

### v4.2.6 — Agent 安全审计新增「参数透传失效」检测（AGENT-SEC-27）

- **新增 AGENT-SEC-27 静态检测**：`coderef_audit` 的 Agent 安全审计新增「参数透传失效 / 被配置静默覆盖」规则——检测「函数声明了参数 X，函数体却从 config/cred/settings/env 等配置容器读取同名值」的运行时语义矛盾：调用方传入的实参被静默忽略，父代理会基于错误前提做判断（如误以为派了某模型，实际用了配置里的模型）。走 AST 级分析，能识别跨行/跨结构的覆盖，避免逐行正则漏判
- **覆盖三种容器形态**：`config["x"]` / `config.x` / `config.get("x")` 均命中，且支持 `self.config`、`self.creds`、`os.environ` 等 Attribute 链容器；`x = x or config["x"]` 合理兜底不误报，嵌套函数作用域严格隔离、非配置容器不误报
- **工程收敛（自审查修复）**：文件遍历改为单一 `os.walk` + 单次读取（正则扫描与 AST 扫描复用同一份内容，消除二次 I/O）；`EXCLUDE_DIRS` 提取为类级常量供文件遍历与项目级检查复用，消除三处分散定义

### v4.2.5 — coderef_innovation 输出可固化清单（审计工具守边界，固化交给对方 AI）

- **新增 `solidifiable_assets` 可固化清单**：`coderef_innovation` 的 `detect` 结果中新增该字段，仅列出达到固化阈值（≥2 个 workflow 采用 + 附带 evidence）的设计，并附 `adopters` 真实采用记录与 `commit_hint`
- **审计 / 编程职责分离**：CodeRef 只判定「某设计够不够格固化」，不自动生成代码；template_code / patch_suggestion / migration_guide 由对方编程 AI 依据 description 自行补全后，再调用 `coderef_asset(action="commit")` 完成固化
- **防污染一致**：清单判定与 `coderef_asset` 的 commit 防污染检查同源，不会出现「清单可固化但 commit 被拒」的矛盾；不满足条件的设计不进入清单，从源头避免误固化污染资产库

### v4.2.4 — 变更守护引擎接入 git 健康基线（守护闭环真正落地）

- **建立 git 基层 `action=ensure_git`**：项目无 git 时自动 `git init` 并补齐最小用户/分支配置，让「守护引擎从形同虚设变为真正可用」——之前守护依赖 git 基线，但 git 恰恰常常缺失，二者是联动的
- **锚定健康基线 `action=anchor`**：把审计通过 / 人工确认健康的当前代码 commit 并打 `coderef-health-*` tag，作为后续回滚参照；返回本次 committed 文件数，并可通过 `allow_autocommit` 控制工作区有改动时是否先自动提交
- **列出基线 `action=list_baselines`**：列出全部健康基线 tag，便于编程 AI 决定回滚到哪一版
- **guard 增强**：动态兜底从 git 历史提取最近改动作为基线对比，返回附带 `git_ready` 与最近健康基线 `health_baseline`，供外层 AI 回滚参照
- **新增 `git_bin` 参数**：由外层编程 AI 用 `Get-Command git` / `where git` 探测 git 可执行文件路径或安装目录后传入，避免依赖系统 PATH（git 常不在 PATH）
- **稳定性**：git 命令统一 UTF-8 / replace 解码，杜绝 Windows 中文乱码或解码异常；`git_timeout` 支持按项目规模调整
- **回滚边界**：回滚交由外层编程 AI 执行（如 `git checkout <health_baseline tag>`），CodeRef 仅提供确定性参照，不做强制回滚

### v4.2.3 — 误报治理与聚合 HTML 全 0 修复

- **修复聚合 HTML 报告全 0**：`coderef_report` 重渲染既有产物时，若用空 `PipeResult` 聚合，`index.html` 审计卡片与 `audit.html` 明细会全部为 0 / "暂无发现"。现 `audit()` 在落盘 markdown 的同时把 findings 与统计序列化为 `audit_findings.json`，`render_report` 优先读取该 JSON 恢复后再渲染，保证重渲染审计内容完整
- **修复 SCA CVE-2023-32690 归属错误**：`pandas` 旧表误挂该 CVE（实为 DMTF libspdm 漏洞，与 pandas 无关），已移除本地条目，由 OSV 在线查询兜底
- **新增组件级利用面过滤**：`langchain-community` 的 CVE-2024-2965（SitemapLoader 无限递归 DoS）等只影响特定子组件的 CVE，若项目源码未实际 import/使用受影响的组件，自动降级为 `low` 并附「潜在风险」说明，避免对未使用组件机械报高危
- **修复 arch_audit 同名模块误判**：模块识别改用项目相对路径而非 basename，`db/base.py` 与 `utils/base.py` 不再被合并计数，「base fan_in 132」等上帝模块虚高消失；跨目录单向调用不再被误判为循环依赖
- **修复连接池探活机械打标**：`AGENT-RESILIENCE-07` 仅对实际使用数据库连接池的项目打标（精确匹配 `create_engine(`/`import sqlalchemy`/`pool_pre_ping` 等），纯 SQLite 等项目不再误报

### v4.2.2 — 依赖瘦身：tree-sitter 降为可选（可用性承诺的关键修复）

- **核心诉求**：兑现「非技术人员 + 编程 AI 即装即用」承诺。审计发现 `tree-sitter==0.20.4` 是唯一需要 C 编译的依赖，且仅覆盖 Python 3.10-3.12；在 Python 3.13+ 上无预编译 wheel，会强制源码编译导致安装崩溃——这是「装不起来」的第一印象头号来源
- **关键发现**：tree-sitter 实为**死依赖**——核心解析走 Python 标准库 `ast.parse`（`core/ast_parser.py`），而 `_init_parsers` 填充的 `self.parsers` 字典全项目无任何读取方。移除后功能完全不受影响
- **改动**：从 `requirements.txt` 移除必需 `tree-sitter` / `tree-sitter-languages`，改为注释标注的**可选依赖**（保留 `_init_parsers` 容错代码，未来如需多语言解析可自行启用）
- **更轻更稳**：`pip install -r requirements.txt` 不再触发任何 C 源码编译，Python 3.10-3.14 全部免编译直接装好，安装更快更省心
- **验证**：无 tree-sitter、无 API key 环境下端到端审计正常（13 findings, 0 errors），LLM 优雅降级为静态审查

### v4.2.1 — 架构腐化诊断层（MCP 工具补盲区）

- **新增 `coderef_arch_audit`**：补齐 MCP 工具「看不到架构级问题」的盲区。复用知识图谱 `CALLS` 边做模块级静态诊断，输出四类架构症状：`cycles`（模块依赖图强连通分量→循环依赖）、`god_modules`（扇出过高→上帝模块）、`layer_violations`（低层依赖高层）、`large_modules`（异常模块规模），聚合为 0–10 架构健康度
- **纯静态、确定性**：只读知识图谱，不依赖 LLM，结果稳定可复现——延续「非编程人员也能验证工程健康」的目标
- **本轮架构债修复**：抽取 `core/code_models.py` 切断 `CodeAnalyzer↔AstParser` 循环依赖（R1）、收敛全项目函数内惰性导入（R2）、抽取 `core/tool_registry.py` 收敛 `pipeline_runner` 上帝模块（R3）、删除 `utils/helpers.py` 死代码（R4）、抽取 `core/graph_closure.py` 消除 `flow_verify` 与 `wiki_cross_verify` 知识重复（R5）

### v4.2.0 — 流程合规验证（非编程人员最核心的需求）

- **新增 `coderef_flow_verify`**：验证「项目是不是按我期望的流程执行」——入口 A 的调用管线是否覆盖期望步骤 B→C→D，确认数据真的按这条管线走。这是对非编程人员最有价值的功能：他不需要看懂代码，只需定义期望流程，工具给出代码是否按此执行的确证证据
- **纯静态、确定性**：数据只来自知识图谱 `CALLS` 边，不依赖 LLM，因此结果稳定可复现（区别于 Wiki 的 LLM 生成内容）——正契合"流程合规验证优先静态"的稳定性诉求
- **入口消歧义**：`entry` 支持 `模块.函数`（如 `pipeline_runner.audit`）限定，解决同名函数（如多个模块的 `audit`）歧义
- **四态诚实标记**：`ordered`=调用链确证(含顺序)；`in_pipeline`=在管线但顺序未确证(可能并行)；`outside`=管线外/动态调用，需编程 AI 复核；`missing`=项目内无对应符号。绝不把"静态查不到"误判为"流程错误"
- **缺失图谱明确反馈**：知识图谱未构建时返回明确提示需先运行 `coderef_audit` / `coderef_memory_sync`，不静默
- **自动定位图谱**：通过 `CodeKnowledgeGraph(project_path)` 自动定位项目图谱，调用方无需传 db 路径
- **与 `wiki_cross_verify` 的分工**：`core/flow_verify.py`（步骤级，作为 MCP 工具 `coderef_flow_verify` 暴露给非编程人员验证期望流程）与 `core/wiki_cross_verify.py`（目录级，给 Wiki 模块条目打确证徽章，由 `wiki_generator` 内部调用）共享同一套「静态 CALLS 边 + 确定性」方法论，是解决「Wiki 幻觉」的一体两面、互补不冗余——前者是步骤级流程确证，后者把确证结果回贴到 Wiki 人话描述上

### v4.1.3 — git 超时参数化（让外层 AI 按项目规模自调超时）

- **超时参数暴露**：`coderef_change_guard` 新增 `git_timeout` 参数，允许外层 AI 根据项目规模调节 git 命令的等待秒数，避免小项目等太久、大项目超时误判
- **规模建议写入工具描述**：明确建议"小型项目(<1万行) 15s；中型(1~10万行) 30s；大型(>10万行) 60s"，让外层 AI 在 `tools/list` 看到即可自己决策，无需依赖工具侧猜
- **默认值保留兼容**：`DEFAULT_GIT_TIMEOUT` 常量保持 30s（中型项目），不传即时用默认值，旧调用方式不受影响
- **全链路透传**：MCP 工具 schema → `_change_guard` 分发 → `guard(git_timeout=...)` → `_auto_git_diff(timeout=...)` 逐层透传，无硬编码

### v4.1.2 — 退化检测动态兜底（消除误导性空结论）

- **修复承诺未兑现**：`coderef_change_guard` 此前既不传 `diff` 也不传 `baseline_dir` 时，静默返回空 findings 并显示"未检测到明显退化"——这是误导性静态结果，未做任何基线对比
- **git 历史动态兜底**：无 `diff`/`baseline_dir` 时自动尝试从 git 历史提取最近改动作为基线（优先工作区未提交改动 `git diff HEAD`，其次最近一次提交 `git diff HEAD~1 HEAD`），走真实退化检测
- **明确的降级反馈**：git 不可用 / 非 git 仓库 / 无历史改动时，返回 `source=no-baseline` 并明确提示"退化检测未执行，请传入 diff 或 baseline_dir"，绝不假装"未检测到退化"
- **检测依据透明化**：返回结构新增 `source` 字段（`diff` / `baseline_dir` / `git-auto` / `no-baseline`），summary 同步标注基线来源，让外层 AI 清楚结论依据
- **优雅降级**：git 命令执行失败（超时 / 非零退出 / 无输出）逐级降级尝试，全程不抛异常

### v4.1.1 — LLM 逐条粗筛闭环（疑似误报 → 用户 AI 反馈白名单）

- **新增 LLM 逐条粗筛**：功能审查阶段对 findings 逐条做三分类（`suspected_fp` 疑似误报 / `needs_review` 需人工确认 / `confirmed` 真问题），判断依据升级为携带 `detail` + `suggestion`，不再仅凭标题
- **疑似误报附带建议白名单条目**：每条疑似误报自动构造 `{file, rule, category}` 建议条目（file 取相对路径后两段、rule 截断避免误伤），随报告输出提示"确认无误后调用 `coderef_whitelist(action=add)` 反馈，下次自动过滤"，让用户项目 AI 在真实上下文里拍板，而非由工具预设误报
- **粗筛不自动过滤**：粗筛结果仅作建议标记展示，不删除 findings，避免 LLM 误判吞掉真问题
- **优雅降级**：LLM 不可用 / 无 findings 时粗筛返回空结构（`ran=False`），不影响功能审查降级路径；`review()` 返回值统一携带 `screen` 字段保证调用方结构一致
- **健壮性**：粗筛 prompt 沿用 f-string 大括号转义，避免 `Invalid format specifier` 回归

### v4.1.0 — 动态策略审计 + 按需文档读取

- **动态兜底落地**：`coderef_audit` 新增 `strategy` 参数（`auto`/`full`/`incr`/`no_change`）。`auto` 时由 `ReviewAdvisor` 依据变更信号 + 知识图谱影响闭包自动判定增量或全量；增量模式裁剪创新传播/代码精简/项目成熟度 3 个重型全量工具（11→8），聚焦变更相关维度，显著降低重复审计耗时
- **新增 `coderef_docs_read` 工具**：按需读取已生成的 Wiki 文档正文（返回内容而非路径），解决编程 AI 无法主动 fs 访问外部文件夹的问题；自动探测 `docs/wiki/` → `docs/` → `txt/`，含路径穿越防护与 `max_chars` 截断
- **工具清单补全**：文档工具总数由 21 修正为 24（补记 `coderef_report`、`coderef_audit_advisor`），四引擎架构下工具清单与 MCP Server 实际能力对齐
- **健壮性**：`coderef_docs_read` 的 `max_chars` 非法输入防御性回退默认值，避免 MCP 层抛异常

### v4.0.4 — 审计误报修复（Agent 安全审计 + 成熟度建议归类）

- **timeout 误报修复**：`coderef_audit` 的网络请求无超时检测不再把 `requests.get(url, timeout=120)` 误判为无超时。改为平衡括号解析完整调用参数，支持跨行调用与嵌套括号（如 `timeout=compute_timeout()`），仅当调用确实未传 `timeout` 时才报告
- **pickle 误报修复**：反序列化检测从无条件 blocker 降级为 medium，并识别服务端内部 `dumps→loads` 闭环予以豁免；豁免条件收紧为"被负载变量确实由 `pickle.dumps` 赋值"，外部请求数据、复杂表达式不再被误豁免（避免漏报真实攻击面）
- **连接池探活误报修复**：对不使用数据库连接池的项目（如纯 SQLite）不再机械打标 `AGENT-RESILIENCE-07`，仅当项目存在 SQLAlchemy 等连接池依赖时才建议
- **成熟度建议归类**：`coderef_audit` 的成熟度检查将"工程化改进建议"（CI/CD、容器化、格式化、配置模板等 warn 项）与"缺陷"分离，报告新增"💡 建议（工程化改进项，非缺陷）"独立小节，建议项不再以缺陷级别进入 HIGH/MEDIUM 缺陷汇总
- **管线健壮性**：`_matu` 改为直接接住成熟度检查返回值，消除对检测器 `self.report` 副作用的隐式依赖

### v4.0.3 — Wiki 生成可靠性（部分失败可感知）

- **`coderef_docs` 输出目录参数生效**：调用方指定的 `output_dir` 现在会被正确传递到 Wiki 生成器，不再无视参数落到默认 `txt/`
- **不再产生 0 字节空文件**：LLM 生成返回空内容时跳过落盘，避免生成"文档已生成"的假象
- **部分失败可感知**：`coderef_docs` 改为结构化返回 `status`（`completed` / `partial_failed`）+ `errors` + 输出目录 + 文档清单，部分文档生成失败时不再被当作全量成功
- **LLM 失败不再写占位假文档**：`_llm_ask` 失败返回空串并记录错误，交由上层统一跳过与告警，取代原先"错误占位符当正常文档"的假象

### v4.0.2 — 审计证据透明化（区分审计发现与历史数据）

- **知识图谱查询返回构建时间**：`coderef_query` 的所有查询类型（stats/entity/callers/callees/impact/relations/file_entities/search/call_graph）统一附带 `kg_built_at` 与 `kg_note`，明确提示"图谱仅当运行 audit/docs/architecture 后才会重建，代码有改动需先重建再查询"，避免把旧图谱当成本次审计结论
- **审计结果携带证据字段**：`coderef_audit` 返回新增 `evidence` 块，包含本次扫描时间 `scan_ts`、知识图谱构建时间 `kg_built_at`、本次扫描文件快照 `file_snapshot`（mtime+size），并声明统计口径仅覆盖快照所列文件、不代表修复状态
- 报告显式标注统计口径：审计报告头部新增"统计口径"章节，写明本次扫描时间、图谱构建时间，并声明 HIGH/MEDIUM/LOW 均为审计发现、不代表任何修复状态，修复需对照 git 提交单独核实

### v4.0.1 — 复查修复（SCA 准确性 + 报告透明性）

- **修复 SCA 版本比较失效**：补齐 `packaging` 依赖，修正 `_version_matches` 异常被吞导致所有版本约束无条件命中的误报根因
- **修正本地 CVE 表错误归属**：移除 `sqlalchemy→CVE-2023-48795`、`numpy→CVE-2023-32698`、`fastapi→CVE-2024-24762` 等错误映射，Pillow 改用真实 CVE
- **OSV 在线查询失败不再静默**：降级时报告明确告警"仅本地库，可能遗漏"，区分"在线未命中"与"在线未查"
- **修复报告头部耗时 0.0s**：elapsed 改为实时兜底计算，展示真实耗时
- **修复分级归表**：置信度等级严格跟随 severity，MEDIUM 表不再混入 low 级条目，并补上位置列消除"重复"观感
- **审计范围透明**：报告披露排除目录数及原因分布，消除"为何只统计 N 文件"的疑问
- **进度细化**：扫描阶段提供"已扫描文件/总文件"中间进度，长阶段不再无感知
- 清理 `config/settings.py` 中无引用的 LLM 配置死代码，LLM 配置统一走 `core/llm_integration.py`

### v4.0 — 四引擎架构 + 21 个 MCP 工具

- **记忆引擎**：代码记忆增量同步 + 语义查询（`coderef_memory_sync`、`coderef_memory_query` 等），记忆质量评估与 Prompt 资产版本化管理
- **创新识别引擎**：结构化创新识别 + 设计资产化（`coderef_innovation`、`coderef_asset`），把创新传播升级为「理想清单 vs 实际实现」的结构化对比
- **OWASP 合规引擎**：覆盖 OWASP LLM Top 10 的合规检测（`coderef_owasp`），LLM01-LLM10 全维度
- **变更保护引擎**：AI 代码劣化检测 + 人类可读变更报告（`coderef_change_guard`、`coderef_change_report`）
- 统一 `coderef_scan` 参数化工具 + `coderef_scan_list`，总工具数精简为 21 个
- 创新传播检测器性能优化：vendored 库过滤、LLM 一次看清单价值挑选、类级预算兜底、实例级签名缓存

### v3.2 — 代码审查 + 前端交互审查

- 新增 `coderef_review` MCP 工具：基于 git diff 的变更行内审查（mode=diff）+ 新项目全量语义首查（mode=full），覆盖 7 个审查维度，结论带 evidence 标记供交叉验证
- 新增 `coderef_frontend` MCP 工具：静态全量枚举 HTML/JS 全部按钮（事件/确认弹窗/禁用态）与 L1-L5 菜单树，按 6 维度审查；可选 `mode=runtime` 浏览器抽查（失败自动降级）
- 内置 `demo-app/` 测试实例（含 6 个预置交互问题）用于功能验证

### v3.1 — 知识图谱 + 健康仪表盘

- 新增 SQLite 持久化项目知识图谱，6 种节点类型，6 种关系边
- 新增 `coderef_query` MCP 工具，9 种查询类型，替代 grep / 读文件
- 新增零外部依赖 HTML 健康仪表盘，非编程人员友好
- AstParser 集成到知识图谱构建，自动填充 CALLS 边
- Wiki 核心模块判定规则可配置化（`coderef_whitelist` 扩展）

### v3.0 — 三功能管线架构

- 18 个独立 MCP 工具 → 合并为 3 个管线（audit / architecture / docs）
- 统一管线引擎：共享 AST 扫描 + 检查点续跑 + 后台任务
- 三级自动降噪（AutoNoiseFilter）：白名单 + NOISE_RULES + 合并汇总
- 交叉验证：多工具独立分析互验，产生置信度分级

