# utils 目录

`utils/` 目录已在本项目 v4.2.1 架构清理中移除。

## 说明

原 `utils/helpers.py` 为早期遗留的通用辅助模块，功能与 `core/` 内相关逻辑重复，且全项目无实际引用。经架构审查（R4 死代码）确认后已删除，`utils/` 空壳目录一并清理。

## 现在的共享辅助

跨模块复用的通用逻辑已收敛到 `core/` 内，例如：

- `core/graph_closure.py`：知识图谱读取 + 下游闭包遍历（`flow_verify` / `wiki_cross_verify` / `arch_audit` 共用）
- `core/code_models.py`：共享数据模型（切断 `CodeAnalyzer` 与 `AstParser` 的循环依赖）
- `core/tool_registry.py`：工具目录 + 策略裁剪（收敛 `pipeline_runner` 上帝模块）