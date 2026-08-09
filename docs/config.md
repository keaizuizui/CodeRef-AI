# config 目录

`config/` 集中管理 CodeRef-AI 的运行配置，避免审计工具的魔法数字散落在各检测器源码中。

## 文件说明

| 文件 | 说明 |
|------|------|
| `settings.py` | 统一维护复杂度阈值、嵌套深度、函数长度等审计魔法数字，供各检测器引用 |
| `config.json` | MCP Server 与审计管线的可调运行参数（LLM 配置、缓存开关等） |

## 用法

检测器通过 `from config import settings` 读取阈值常量，保证审计口径一致；`config.json` 由 `llm_integration.py` 读取用于初始化 LLM 客户端。