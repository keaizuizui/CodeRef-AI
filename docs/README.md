# CodeRef-AI 文档索引

本目录存放 CodeRef-AI 的补充文档。核心实现与架构说明见仓库根目录的 `README.md` 与 `MCP_SETUP.md`。

## core/ 模块文档

`core/` 目录是本项目的核心实现，包含 MCP Server、四大引擎与 11 个审计检测器，相关文档见：

- `MCP_SETUP.md` — MCP Server 配置指南（工具清单、知识图谱查询速查、白名单、架构说明）
- `README.md` — 项目总览、四引擎架构、24 个 MCP 工具、审计管线、更新日志

## config/ 目录

`config/` 存放项目运行配置：

- `settings.py` — 集中管理审计工具的重构魔法数字（复杂度阈值、嵌套深度、函数长度等），避免散落硬编码
- `config.json` — MCP Server 与审计管线的可调参数

## utils/ 目录

`utils/` 已在 v4.2.1 架构清理中移除，通用辅助函数已合并至 `core/` 各模块内部。

## 目录说明

| 路径 | 说明 |
|------|------|
| `core/mcp_server.py` | MCP Server 入口，暴露 24 个工具 |
| `core/pipeline_runner.py` | 审计管线引擎（audit / architecture / docs + 知识图谱） |
| `core/governance_audit.py` | 治理审计检测器（架构 / 变更 / 质量 / 安全铁律） |
| `core/agent_security_auditor.py` | Agent 安全审计检测器 |
| `core/code_simplifier.py` | 代码精简检测器 |
| `core/resource_gap_detector.py` | 资源遗漏检测器 |
| `core/integrity_checker.py` | 完整性检查检测器 |
| `core/code_knowledge_graph.py` | 知识图谱引擎（SQLite 持久化） |
| `core/report_renderer.py` | HTML 报告渲染器 |
| `core/review_strategy.py` | 增量/全量审查策略判定 |
| `core/functional_review.py` | LLM 功能审查增强 |
| `core/memory_layer.py` | 记忆引擎（增量同步 + 语义查询） |
| `core/innovation_engine.py` | 创新识别引擎 |
| `core/change_guard.py` | 变更守护引擎 |

各引擎的详细设计与使用方式，请以 `README.md` 对应章节为准。