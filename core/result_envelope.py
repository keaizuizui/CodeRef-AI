# -*- coding: utf-8 -*-
"""结论信封（Result Envelope）——让调用方 AI 能安全转述给非技术用户。

背景：真正消费 CodeRef 结论的往往是对方的 AI，而它的用户可能不懂代码。
各工具自己返回分数/字段，调用方容易把「输入过期」说成「工具坏了」。
本模块把结论统一包装成：

    verdict              一句话人话结论
    usable_for_decisions 这轮结果能否用于决策
    blocked_by           被什么挡住（含 owner：coderef / consumer / by_design）
    owner_actions        双方各自要做什么
    cannot_conclude      本轮明确不能下的结论
    guardrails           转述时必须带上的限制
    limitations          已知限制编号（KL-xx，见 KNOWN_LIMITATIONS）

只做包装，不改任何分析逻辑。

作者: CodeRef-AI Team
版本: v1.0
"""

from typing import Dict, List, Optional


# 已知限制登记表：对方再次报同一件事时，引用编号即可，不必每轮重新解释。
# owner=consumer 表示需要调用方先修输入；by_design 表示设计取舍。
KNOWN_LIMITATIONS = (
    {"id": "KL-01", "owner": "consumer",
     "title": "定标过期时不产对齐分",
     "detail": "target_arch 中超过一半模块不存在时，对齐度/覆盖率不可用；需重新 coderef_target_arch_set。"},
    {"id": "KL-02", "owner": "by_design",
     "title": "IMPORTS 只建项目内模块边",
     "detail": "第三方/标准库 import 不建 IMPORTS 边，因此「零边模块」比例高属正常。"},
    {"id": "KL-03", "owner": "by_design",
     "title": "CALLS 按 import 作用域解析",
     "detail": "同名但未 import、属性链（datetime.now）不建边；宁漏不假，fan_in 偏保守。"},
    {"id": "KL-04", "owner": "by_design",
     "title": "模块数有多种口径",
     "detail": "图谱全量 / 参与调用图 / 生产模块 / 画布归类是不同统计范围，不是数据错误。"},
    {"id": "KL-05", "owner": "by_design",
     "title": "architecture 不产代码 findings",
     "detail": "该工具只做结构分析；代码问题看 coderef_audit / coderef_scan。"},
    {"id": "KL-06", "owner": "consumer",
     "title": "过期产物标 unknown",
     "detail": "治理库全 Rejected/超 30 天、审计过期、Wiki 未生成时不给分，需先重跑对应工具。"},
    {"id": "KL-07", "owner": "consumer",
     "title": "备份目录需 whitelist",
     "detail": "备份/镜像目录用 coderef_whitelist 的 dir 条目排除后再解读。"},
    {"id": "KL-08", "owner": "by_design",
     "title": "动态调用不覆盖",
     "detail": "threading.Thread(target=...)、getattr 等运行时调用不在静态调用链内。"},
    {"id": "KL-09", "owner": "by_design",
     "title": "文档覆盖只认 Wiki 产物目录",
     "detail": "只统计 docs/wiki、.coderef/wiki、coderef-wiki；通用 docs/ 不计入覆盖率。"},
)

_LIM_BY_ID = {x["id"]: x for x in KNOWN_LIMITATIONS}


def envelope(verdict: str, *, usable: bool = True,
             blocked_by: Optional[List[dict]] = None,
             owner_actions: Optional[Dict[str, List[str]]] = None,
             cannot_conclude: Optional[List[str]] = None,
             guardrails: Optional[List[str]] = None,
             limitations: Optional[List[str]] = None) -> dict:
    """构造结论信封（所有字段都有默认值，便于工具按需填充）。"""
    lims = [i for i in (limitations or []) if i in _LIM_BY_ID]
    return {
        "verdict": verdict,
        "usable_for_decisions": bool(usable),
        "blocked_by": list(blocked_by or []),
        "owner_actions": {
            "coderef": list((owner_actions or {}).get("coderef") or []),
            "consumer": list((owner_actions or {}).get("consumer") or []),
        },
        "cannot_conclude": list(cannot_conclude or []),
        "guardrails": list(guardrails or []),
        "limitations": lims,
    }


def _blocked(block_id: str, owner: str, human: str, kl: str = "") -> dict:
    item = {"id": block_id, "owner": owner, "human": human}
    if kl:
        item["limitation"] = kl
    return item


# ── 各工具的信封构造 ─────────────────────────────────────────────

def for_audit(findings: int, errors: Optional[List[str]] = None) -> dict:
    errs = list(errors or [])
    usable = not errs
    return envelope(
        f"审计完成：{findings} 条发现" + ("（存在执行错误，结论不完整）" if errs else ""),
        usable=usable,
        blocked_by=([_blocked("audit_errors", "coderef",
                              "审计部分维度执行失败，结果不完整；请把 errors 原样反馈给工具方")]
                    if errs else []),
        cannot_conclude=["未发现的问题", "修复状态", "审计范围之外的代码"],
        guardrails=["findings=0 不代表无问题，只代表当前规则与扫描范围内未命中",
                    "备份/生成目录请先用 coderef_whitelist 的 dir 条目排除"],
        limitations=["KL-03", "KL-07"],
    )


def for_scan(findings: int, dimension: str) -> dict:
    return envelope(
        f"单维度扫描完成（{dimension}）：{findings} 条发现",
        cannot_conclude=["其他未被扫描的维度", "修复状态"],
        guardrails=["单维度结果不能当全量审计结论"],
        limitations=["KL-03"],
    )


def for_report(ok: bool, error: str = "") -> dict:
    return envelope(
        "评估台已生成" if ok else f"评估台生成失败：{error}",
        usable=bool(ok),
        blocked_by=([_blocked("report_failed", "coderef", error or "报告渲染失败")] if not ok else []),
        cannot_conclude=["页面中标注『证据不足』的维度", "iframe 未打开时的画布内容"],
        guardrails=["证据不足的维度不计分，也不能当 0 分", "总分只在有证据的维度内加权"],
        limitations=["KL-06", "KL-04"],
    )


def for_arch_gap(result: dict) -> dict:
    th = (result or {}).get("target_health") or {}
    if th.get("stale"):
        warn = th.get("warning") or "target_arch 已过期，请先重新定标"
        return envelope(
            "定标过期：本轮对齐度/覆盖率不可用，不是代码问题",
            usable=False,
            blocked_by=[_blocked("stale_target_arch", "consumer", warn, "KL-01")],
            owner_actions={"consumer": ["coderef_target_arch_set"]},
            cannot_conclude=["对齐度", "覆盖率", "差距条数作为代码质量结论"],
            guardrails=["定标过期时任何基于 target_arch 的指标都不可对外引用"],
            limitations=["KL-01"],
        )
    return envelope(
        "差距分析完成：可参考对齐度与差距清单",
        cannot_conclude=["未声明在 target_arch 中的业务能力"],
        limitations=["KL-04"],
    )


def for_arch_verify(result: dict) -> dict:
    th = (result or {}).get("target_health") or {}
    if th.get("stale") or (result or {}).get("score_status") == "stale_target":
        warn = (result or {}).get("score_note") or th.get("warning") or "定标过期"
        return envelope(
            "定标过期：本轮对齐分不可用，不是代码问题",
            usable=False,
            blocked_by=[_blocked("stale_target_arch", "consumer", warn, "KL-01")],
            owner_actions={"consumer": ["coderef_target_arch_set"]},
            cannot_conclude=["对齐分", "维度得分"],
            guardrails=["定标过期时不要用分数评价代码"],
            limitations=["KL-01"],
        )
    return envelope("架构对齐验证完成")


def for_health(result: dict) -> dict:
    data = result or {}
    if data.get("data_source") == "none":
        return envelope(
            "没有可用的审计产物，健康分不可用",
            usable=False,
            blocked_by=[_blocked("no_audit", "consumer",
                                 "尚未运行 coderef_audit", "KL-06")],
            owner_actions={"consumer": ["coderef_audit"]},
            cannot_conclude=["健康分"],
            limitations=["KL-06"],
        )
    stale = data.get("data_stale")
    if stale is True or stale is None:
        reason = ("审计数据已过期" if stale is True else "审计数据年龄未知")
        return envelope(
            f"{reason}：健康分不代表当前代码",
            usable=False,
            blocked_by=[_blocked("stale_audit", "consumer",
                                 f"{reason}，请先重跑 coderef_audit", "KL-06")],
            owner_actions={"consumer": ["coderef_audit"]},
            cannot_conclude=["健康分", "发现数作为当前状态"],
            guardrails=["data_stale=null 表示未知，不得当作新鲜"],
            limitations=["KL-06"],
        )
    return envelope(
        f"健康分 {data.get('score')}/100（{data.get('score_label', '')}）",
        cannot_conclude=["未检出问题不代表代码无错"],
        guardrails=["健康分来自确定性审计，仅反映已检出问题"],
        limitations=["KL-03"],
    )


def handshake() -> dict:
    """首次调用 coderef_version 时返回的使用契约（不依赖对方加载 skill）。"""
    return {
        "must_read": [
            "unknown / stale 不是 0 分，也不是工具故障；先看 reason 与 envelope.blocked_by[].owner",
            "引用任何历史产物前先看 artifacts_report 的 code_newer / stale_by_age",
            "只有 envelope.blocked_by 中 owner=coderef 才可向用户报『工具缺陷』；"
            "owner=consumer 表示需要用户方先重跑/重定标",
        ],
        "known_limitations": list(KNOWN_LIMITATIONS),
        "envelope_fields": {
            "verdict": "一句话人话结论，可直接转述",
            "usable_for_decisions": "本轮结果能否用于决策",
            "blocked_by": "被什么挡住；owner=coderef/consumer/by_design",
            "owner_actions": "coderef / consumer 各自要做什么",
            "cannot_conclude": "本轮明确不能下的结论",
            "guardrails": "转述时必须带上的限制",
            "limitations": "已知限制编号（KL-xx）",
        },
    }
