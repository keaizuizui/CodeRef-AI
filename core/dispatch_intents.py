# -*- coding: utf-8 -*-
"""dispatcher 链意图的单一事实源（5.19.6）。

dispatcher / intent_router / tools_manifest 三处曾各写一份枚举，新增链时必须
多处同步。本模块只承载常量，不依赖任何其他 core 模块，供三方单向引用。

作者: CodeRef-AI Team
版本: v1.0
"""

INTENTS = ("probe", "single_audit", "recover", "governance", "asset", "remind")

INTENT_LABELS = {
    "probe": "L1 变更探查（提交前快速自查）",
    "single_audit": "单次全量审计",
    "recover": "上下文恢复（找回工具位置与既有约定）",
    "governance": "L2 架构治理（捋管线→诊断→定标→治理→验证→体检）",
    "asset": "L3 资产沉淀（创新识别→登记→固化）",
    "remind": "纪律复盘（你忘了什么：记忆/守卫/图谱/产物）",
}
