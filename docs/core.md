# core 目录

`core/` 是 CodeRef-AI 的核心实现，包含 MCP Server 入口、四大引擎（记忆 / 创新识别 / 变更守护 / 知识图谱）与 11 个审计检测器。

## 模块分类

| 分类 | 模块 |
|------|------|
| MCP 入口 | `mcp_server.py` |
| 审计管线 | `pipeline_runner.py`、`review_strategy.py`、`functional_review.py` |
| 审计检测器 | `governance_audit.py`、`agent_security_auditor.py`、`code_simplifier.py`、`resource_gap_detector.py`、`integrity_checker.py`、`sca_checker.py`、`tech_debt_detector.py`、`blind_spot_detector.py`、`junk_detector.py`、`innovation_propagation_detector.py`、`owasp_compliance.py` |
| 引擎 | `memory_layer.py`、`innovation_engine.py`、`change_guard.py`、`code_knowledge_graph.py`、`code_knowledge_base.py` |
| 支撑 | `code_analyzer.py`、`ast_parser.py`、`report_renderer.py`、`health_dashboard.py`、`wiki_generator.py`、`llm_integration.py`、`shared_filter.py`、`project_scope.py`、`cache_manager.py`、`design_registry.py` 等 |

## 说明

- `mcp_server.py` 通过 MCP 协议将审计 / 记忆 / 创新识别 / 变更守护能力封装为工具，供 AI 编程助手调用。
- 审计管线 `audit()` 一次性集成 11 个检测器，产出分级、过滤误报的审计报告。
- 详细设计见仓库根目录 `README.md` 与 `MCP_SETUP.md`。