# -*- coding: utf-8 -*-
"""
tool_registry — 审计工具的注册表与选择策略（从 pipeline_runner 剥离）

背景：pipeline_runner 作为编排中枢承担了"工具注册 + 选择 + 执行 + 渲染"等多重职责，
是典型上帝模块（29 依赖 / 约 1450 行）。工具注册表（11 个审计工具）本质是纯数据 +
纯选择函数，与执行编排无关，抽到独立模块以降低 pipeline_runner 的耦合与体积。

设计原则：
- 纯声明：只描述"有哪些工具、各自怎么叫、增量该跳过谁"，不执行业务逻辑。
- 零内部依赖：只依赖标准库；短名→方法名的映射由调用方（pipeline_runner）解释。
- 向后兼容：pipeline_runner 的 Pipe 类通过别名引用本模块，保持 Pipe.* 对外接口不变。
"""

# 11 个审计工具：短名 → (展示名, 检测器方法名)
SINGLE_TOOLS = {
    "gov":    ("治理审计", "_gov"),
    "agent":  ("Agent安全", "_agent"),
    "sca":    ("依赖扫描SCA", "_sca"),
    "td":     ("技术债务", "_td"),
    "integ":  ("完整性检查", "_integ"),
    "blind":  ("盲区检测", "_blind"),
    "inn":    ("创新传播", "_inn"),
    "junk":   ("垃圾文件", "_junk"),
    "resgap": ("资源遗漏", "_resgap"),
    "simp":   ("代码精简", "_simp"),
    "matu":   ("项目成熟度", "_matu"),
}

# 全量模式下运行的全部 11 个工具（展示名, 方法名）
ALL_AUDIT_TOOLS = [(label, method) for label, method in SINGLE_TOOLS.values()]

# 增量模式下跳过的重型全量工具（这些维度需全项目盘点，增量变更无意义）
INCR_SKIP_TOOLS = {"_inn", "_simp", "_matu"}


def list_single_tools() -> list:
    """列出所有可单独运行的审计工具（短名 + 展示名）"""
    return [{"name": k, "label": v[0]}
            for k, v in SINGLE_TOOLS.items()]


def select_tools(strategy: str = None) -> list:
    """按审计策略选择要运行的工具子集 —— 动态兜底的核心裁剪。

    规则：
      - strategy == "incr"：跳过重型全量工具（创新传播/代码精简/项目成熟度），
        聚焦与变更直接相关的维度，避免增量变更还跑全量盘点；
      - 其余（full / no_change / 未知）：全量跑 11 个工具，保证一致性。
    返回 [(展示名, 方法名), ...]。
    """
    if (strategy or "").lower() == "incr":
        return [(n, m) for n, m in ALL_AUDIT_TOOLS
                if m not in INCR_SKIP_TOOLS]
    return list(ALL_AUDIT_TOOLS)