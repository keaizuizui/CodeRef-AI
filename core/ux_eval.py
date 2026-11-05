# -*- coding: utf-8 -*-
"""ux_eval — U 轨·人格走查（v5.18.0，只判「是否阻断任务完成」）。

定位：U 轨是 F 轨的支撑证据，**不产出「体验好不好」的独立评分**。

  - 人格：`core/personas.json` 的 7 项必填人格（禁止「普通用户」这类无特征人格）；
  - 走查：3 秒生存 → 关键任务旅程 → Rage Map → 心智模型违背 → 信任与转化 → 桌面尺寸抽查；
  - 出口：只回答 `blocking` / `partial` / `non_blocking`；
  - Rage Map 必带「业务后果」列：愤怒 ≥4 **且** 任务中断 → P0/P1；否则最高 P2；
  - 每条发现必须有静态证据锚点，无锚点不输出；
  - 全部标注 `simulated=true` + E4（锚点本身是 E2，可回到 file:line）；
  - runtime 不可用时降级静态，并明确标注未做运行时验证；
  - 视口只评常规桌面（默认 1280/1440/1920），不产出窄屏课题。
"""

import json
import os
import re
from datetime import datetime
from typing import Dict, List, Optional

from loguru import logger

from core.product_evidence import MARKET_NUMBER_RE
from core.version import __version__

_PERSONAS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                              "personas.json")
_DEFAULT_VIEWPORTS = [1280, 1440, 1920]
_MAX_FINDINGS = 80

# 破坏性动作（无确认弹窗即高风险）
_DESTRUCTIVE_MARKERS = ("删除", "清空", "重置", "移除", "注销", "退订",
                        "delete", "remove", "clear", "reset", "destroy")
# 表单提交类动作（用于判断表单是否有提交出口）
_SUBMIT_MARKERS = ("提交", "保存", "生成", "开始", "发布", "确定", "下单",
                   "导出", "submit", "save", "generate", "start", "confirm")

_SEVERITY_ORDER = {"P0": 0, "P1": 1, "P2": 2}


def _rel(project_path: str, path: str) -> str:
    try:
        return os.path.relpath(path, project_path).replace(os.sep, "/")
    except Exception:  # noqa: BLE001
        return path.replace(os.sep, "/")


# ────────────────────────────────────────────────────────────────
# 人格加载与校验
# ────────────────────────────────────────────────────────────────

def load_personas(path: str = _PERSONAS_FILE) -> Dict[str, dict]:
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError("personas.json 结构非法（应为对象）")
    required = data.get("required_fields") or []
    min_triggers = int((data.get("rules") or {}).get("abandon_triggers_min", 3))
    personas = {}
    for p in data.get("personas") or []:
        if not isinstance(p, dict) or not p.get("id"):
            raise ValueError("persona 缺少 id")
        for key in required:
            val = p.get(key)
            if isinstance(val, list):
                if not val:
                    raise ValueError(f"persona {p['id']} 的 {key} 为空")
            elif val is None or (isinstance(val, str) and not val.strip()):
                raise ValueError(f"persona {p['id']} 缺少必填项 {key}")
        triggers = p.get("abandon_triggers") or []
        if len(triggers) < min_triggers:
            raise ValueError(f"persona {p['id']} 的 abandon_triggers 少于 {min_triggers} 条")
        personas[p["id"]] = p
    if not personas:
        raise ValueError("personas.json 未定义任何人格")
    return personas


def resolve_persona(persona: object) -> dict:
    """接受人格 id 或完整人格对象；缺省用内置第一个人格。"""
    personas = load_personas()
    if isinstance(persona, dict):
        pid = str(persona.get("id") or "")
        if not pid:
            raise ValueError("传入的人格对象缺少 id")
        merged = dict(personas.get(pid) or {})
        merged.update(persona)
        # 传入对象同样要满足必填项，防止用「普通用户」绕过校验
        with open(_PERSONAS_FILE, "r", encoding="utf-8") as f:
            meta = json.load(f)
        for key in meta.get("required_fields") or []:
            val = merged.get(key)
            if isinstance(val, list):
                if not val:
                    raise ValueError(f"传入人格对象缺少必填项 {key}")
            elif val is None or (isinstance(val, str) and not val.strip()):
                raise ValueError(f"传入人格对象缺少必填项 {key}")
        min_triggers = int((meta.get("rules") or {}).get("abandon_triggers_min", 3))
        if len(merged.get("abandon_triggers") or []) < min_triggers:
            raise ValueError(f"传入人格对象的 abandon_triggers 少于 {min_triggers} 条")
        return merged
    if persona:
        pid = str(persona)
        if pid not in personas:
            raise ValueError(f"未知人格 id: {pid!r}（可选: {sorted(personas)}）")
        return personas[pid]
    return next(iter(personas.values()))


# ────────────────────────────────────────────────────────────────
# 静态呈现问题（确定性、有锚点）
# ────────────────────────────────────────────────────────────────

def _problem(rule: str, text: str, anchor: str, component: str,
             rage: int, interrupted: bool, consequence: str) -> dict:
    return {
        "rule": rule,
        "problem": text,
        "anchor": anchor,
        "component": component,
        "rage": rage,
        "task_interrupted": interrupted,
        "business_consequence": consequence,
    }


def _anchor_exists(project_path: str, anchor: str) -> bool:
    """锚点文件是否存在（外部传入的 problems 必须可回到真实 file:line）。"""
    fp = str(anchor or "").split("#L")[0]
    return bool(fp) and os.path.isfile(os.path.join(project_path, fp))


def collect_static_problems(project_path: str,
                            problems: Optional[List[dict]] = None) -> dict:
    """枚举可静态确证的呈现问题；无锚点或锚点不存在的条目一律丢弃。

    Returns:
        {"problems": [...], "surface": {"applicable": bool, ...}}
        surface 用于区分「检查过且无问题」与「根本没有页面可查」。
    """
    pp = os.path.abspath(project_path)
    found: List[dict] = []

    for p in problems or []:
        anchor = str(p.get("anchor") or "")
        # 外部问题同样要求锚点可回到真实文件，避免伪造证据进入产物
        if not anchor or not _anchor_exists(pp, anchor):
            continue
        found.append(_problem(
            str(p.get("rule") or "external"),
            str(p.get("problem") or p.get("text") or ""),
            anchor,
            str(p.get("component") or ""),
            _rage(p.get("rage")),
            bool(p.get("task_interrupted")),
            str(p.get("business_consequence") or "未给出业务后果，按最高 P2 处理")))

    try:
        from core.frontend_inspector import FrontendInspector
        enum = FrontendInspector().enumerate_static(pp)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[ux_eval] 前端枚举失败，仅使用外传问题: {e}")
        return {"problems": found[:_MAX_FINDINGS],
                "surface": {"applicable": False, "error": str(e)}}

    if enum.get("applicable") is False:
        return {"problems": found[:_MAX_FINDINGS],
                "surface": {"applicable": False,
                            "message": enum.get("message", "")}}

    buttons = enum.get("buttons") or []
    forms: Dict[str, List] = {}
    for b in buttons:
        if getattr(b, "form", ""):
            forms.setdefault(b.form, []).append(b)

    for b in buttons:
        anchor = f"{_rel(pp, b.file)}#L{b.line}"
        text = (b.text or "").strip() or "(无文本按钮)"
        low = text.lower()
        if any(m in low for m in _DESTRUCTIVE_MARKERS) and not b.has_confirm:
            found.append(_problem(
                "R1", f"破坏性动作「{text}」没有确认弹窗", anchor, text,
                5, True, "误操作不可撤销，需要人工回滚，原任务被迫中断"))
        elif (b.tag in ("button", "a") and not b.disabled
              and not b.events and not b.form
              and not (b.tag == "a" and b.href)):
            # 静态扫描看不到 addEventListener / 脚本按 id 绑定，也不能判定
            # 「疑似无绑定」会阻断任务，因此只记 P2 可疑项，不下阻断结论。
            found.append(_problem(
                "R2", f"可点击控件「{text}」未发现内联事件或跳转"
                       "（可能由脚本绑定，需运行时确认）",
                anchor, text, 3, False,
                "若确无绑定则点击无响应；静态无法确证脚本绑定"))

    for fid, bs in forms.items():
        if any(any(m in (b.text or "").lower() for m in _SUBMIT_MARKERS) for b in bs):
            continue
        b0 = bs[0]
        found.append(_problem(
            "R5", f"表单「{fid}」没有提交/继续出口", f"{_rel(pp, b0.file)}#L{b0.line}",
            f"表单 {fid}", 4, True, "填完无法提交，任务无法完成"))

    # 去重（同锚点同规则只留一条）并保留确定性顺序
    seen = set()
    out = []
    for p in found:
        if not p["anchor"]:
            continue
        key = (p["rule"], p["anchor"])
        if key in seen:
            continue
        seen.add(key)
        out.append(p)
    return {
        "problems": out[:_MAX_FINDINGS],
        "surface": {"applicable": True,
                    "files": len(enum.get("files") or []),
                    "buttons": len(buttons),
                    "menu_roots": len(enum.get("menu_roots") or [])},
    }


# ────────────────────────────────────────────────────────────────
# 阻断判定（确定性出口，LLM 不决定最终 verdict）
# ────────────────────────────────────────────────────────────────

def _rage(value) -> int:
    """把外部/LLM 传来的 rage 归一为 1-5 的整数（非法值回退 3，不抛异常）。"""
    try:
        return max(1, min(5, int(float(value))))
    except (TypeError, ValueError):
        return 3


def _severity(rage: int, interrupted: bool) -> str:
    if rage >= 4 and interrupted:
        return "P0" if rage >= 5 else "P1"
    return "P2"


def _verdict(findings: List[dict]) -> str:
    if any(f["severity"] in ("P0", "P1") for f in findings):
        return "blocking"
    if findings:
        return "partial"
    return "non_blocking"


def _dedup_findings(findings: List[dict]) -> List[dict]:
    """按锚点去重：同一元素的静态规则发现与 persona 发现只保留更严重的一条。"""
    by_anchor = {}
    out = []
    for f in findings:
        anchor = str(f.get("anchor") or "")
        if not anchor:
            continue
        f["severity"] = _severity(_rage(f.get("rage")), bool(f.get("task_interrupted")))
        if anchor in by_anchor:
            idx = by_anchor[anchor]
            if (_SEVERITY_ORDER.get(f["severity"], 3)
                    < _SEVERITY_ORDER.get(out[idx]["severity"], 3)):
                out[idx] = f
            continue
        by_anchor[anchor] = len(out)
        out.append(f)
    out.sort(key=lambda x: (_SEVERITY_ORDER.get(x["severity"], 3), x["anchor"]))
    return out


def _finding(p: dict, source: str, extra: Optional[dict] = None) -> dict:
    f = {
        "rule": p.get("rule", ""),
        "problem": p.get("problem", ""),
        "anchor": p.get("anchor", ""),
        "component": p.get("component", ""),
        "rage": _rage(p.get("rage")),
        "task_interrupted": bool(p.get("task_interrupted")),
        "business_consequence": p.get("business_consequence", ""),
        "source": source,
        # 模拟判断按 E4；锚点本身是 E2（可回到 file:line）
        "evidence_level": "E4",
        "anchor_evidence": "E2",
    }
    if extra:
        f.update(extra)
    f["severity"] = _severity(f["rage"], f["task_interrupted"])
    return f


# ────────────────────────────────────────────────────────────────
# LLM 人格走查（严格 JSON；锚点必须来自给定集合）
# ────────────────────────────────────────────────────────────────

def _prompt(persona: dict, journey: str, problems: List[dict],
            viewports: List[int]) -> List[dict]:
    system = (
        "你是 UX 人格走查器。只回答一件事：页面上的呈现问题是否**阻断任务完成**。\n"
        "硬规则：\n"
        "1) 不产出「体验好不好」的独立分数；只给 blocking / partial / non_blocking。\n"
        "2) 每条 finding 必须带 anchor，且 anchor 只能来自给定 problems 列表；"
        "无锚点或锚点不在列表内的发现一律不要输出。\n"
        "3) Rage Map 必须带 business_consequence（业务后果）："
        "愤怒≥4 且任务中断 → 严重；愤怒但任务完成 → 最高 P2。\n"
        "4) 全部结论标注 evidence_level=E4（模拟推断），不要写成「用户认为」。\n"
        "5) 禁止编造市场/竞品数字；视口只评常规桌面，不产出窄屏课题。\n"
        "只输出 JSON：{\"three_second\":{\"what_is_this\":\"...\",\"for_whom\":\"...\","
        "\"where_to_click\":\"...\",\"time_to_value\":\"...\",\"trustworthy\":\"...\"},"
        "\"findings\":[{\"anchor\":\"file#Lx\",\"problem\":\"...\",\"rage\":1-5,"
        "\"task_interrupted\":true/false,\"business_consequence\":\"...\","
        "\"mental_model_violation\":\"...\",\"evidence_level\":\"E4\"}],"
        "\"unknowns\":[\"...\"]}。unknowns 不得为空。")
    user = {
        "persona": {k: persona.get(k) for k in (
            "id", "name", "identity", "goal", "behaviors", "patience",
            "trust_level", "verification_habit", "price_sensitivity",
            "abandon_triggers", "voice_sample")},
        "journey": journey,
        "viewports": viewports,
        "problems": [{k: p.get(k) for k in ("rule", "problem", "anchor",
                                            "rage", "task_interrupted",
                                            "business_consequence")}
                     for p in problems],
    }
    return [{"role": "system", "content": system},
            {"role": "user", "content": json.dumps(user, ensure_ascii=False)}]


def _parse_json(raw: str) -> Optional[dict]:
    if not raw:
        return None
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw.strip())
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        data = json.loads(text[start:end + 1])
    except Exception:  # noqa: BLE001
        return None
    return data if isinstance(data, dict) else None


def _validate_llm(out: dict) -> str:
    if MARKET_NUMBER_RE.search(json.dumps(out, ensure_ascii=False)):
        return "LLM 输出含市场/竞品数字，按硬红线拒收"
    if not isinstance(out.get("findings"), list):
        return "LLM 输出缺少 findings 列表"
    if not isinstance(out.get("unknowns"), list) or not out["unknowns"]:
        return "LLM 输出未给出 unknowns（未知项必须非空）"
    return ""


# ────────────────────────────────────────────────────────────────
# 顶层入口
# ────────────────────────────────────────────────────────────────

def _result(project_path: str, status: str, persona: dict, journey: str,
            findings: List[dict], three_second: Optional[dict],
            viewports: List[int], unknowns: List[str],
            llm_available: bool, extra: Optional[dict] = None) -> dict:
    base = {
        "status": status,
        "version": __version__,
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "project": os.path.basename(os.path.abspath(project_path).rstrip(os.sep)),
        "persona": persona,
        "journey": journey,
        # SKIP 表示未产出结论，不能沿用空 findings 推导的 non_blocking
        "verdict": _verdict(findings) if status != "SKIP" else None,
        "findings": findings,
        "three_second": three_second or {},
        "viewports": {
            "evaluated": viewports,
            "method": "static",
            "runtime_verified": False,
            "note": "只评常规桌面视口；未做运行时验证，不产出窄屏课题。",
        },
        "simulated": True,
        "evidence_level": "E4",
        "llm_available": llm_available,
        "unknowns": unknowns,
        "note": ("U 轨只判阻断，不产出体验评分；所有判断为模拟推断（E4），"
                 "锚点可回到 file:line（E2）。"),
    }
    if extra:
        base.update(extra)
    return base


def write_result(project_path: str, result: dict) -> str:
    from core.scorecard import _atomic_write_json
    out = os.path.join(os.path.abspath(project_path), ".coderef", "ux_fit.json")
    _atomic_write_json(out, result)
    return out


def evaluate(project_path: str, persona: object = None,
             journey: str = "", problems: Optional[List[dict]] = None,
             viewports: Optional[List[int]] = None,
             use_llm: bool = True) -> dict:
    """执行 U 轨人格走查并落盘 `.coderef/ux_fit.json`。"""
    pp = os.path.abspath(project_path)
    if not os.path.isdir(pp):
        return {"status": "ERROR", "error": f"项目路径不存在: {pp}"}
    try:
        person = resolve_persona(persona)
    except ValueError as e:
        return {"status": "ERROR", "error": str(e)}

    journey = journey or "从打开页面到拿到第一个可用结果"
    vps = [int(v) for v in (viewports or _DEFAULT_VIEWPORTS)
           if str(v).isdigit() and 1024 <= int(v) <= 3840] or list(_DEFAULT_VIEWPORTS)

    collected = collect_static_problems(pp, problems)
    static_problems = collected["problems"]
    surface = collected["surface"] or {}
    findings = [_finding(p, "rule") for p in static_problems]

    unknowns = [
        "未做运行时验证：点击、等待、真实数据下的行为未实测",
        "未覆盖窄屏/移动端，不推断移动端流失",
        "人格为内置模板，未与真实用户画像核对",
    ]

    # 没有页面可查、也没有外部显式问题：不能把空结果当成「不阻断」
    if not surface.get("applicable") and not static_problems:
        reason = ("未检测到可走查的前端页面"
                  + (f"：{surface.get('message')}" if surface.get("message") else "")
                  + "，不产出阻断结论")
        res = _result(pp, "SKIP", person, journey, [], None, vps,
                      unknowns + [reason], False,
                      {"applicable": False, "reason": reason})
        write_result(pp, res)
        return res

    if not use_llm:
        res = _result(pp, "STATIC_ONLY", person, journey, _dedup_findings(findings),
                      None, vps, unknowns, False)
        write_result(pp, res)
        return res

    try:
        from core.llm_integration import LLMIntegration
        llm = LLMIntegration()
        if not llm.is_available():
            res = _result(pp, "STATIC_ONLY", person, journey, _dedup_findings(findings),
                          None, vps, unknowns, False)
            write_result(pp, res)
            return res
        raw = llm.chat_completion(_prompt(person, journey, static_problems, vps),
                                  response_format={"type": "json_object"},
                                  max_tokens=3000)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[ux_eval] LLM 调用异常，降级静态: {e}")
        res = _result(pp, "STATIC_ONLY", person, journey, _dedup_findings(findings),
                      None, vps, unknowns, False,
                      {"degrade_reason": f"LLM 调用异常: {e}"})
        write_result(pp, res)
        return res

    if LLMIntegration.is_error_response(raw):
        res = _result(pp, "STATIC_ONLY", person, journey, _dedup_findings(findings),
                      None, vps, unknowns, False,
                      {"degrade_reason": f"LLM 调用失败: {raw[:200]}"})
        write_result(pp, res)
        return res

    out = _parse_json(raw)
    if out is None:
        res = _result(pp, "STATIC_ONLY", person, journey, _dedup_findings(findings),
                      None, vps, unknowns, False,
                      {"degrade_reason": "LLM 返回无法解析为 JSON"})
        write_result(pp, res)
        return res
    bad = _validate_llm(out)
    if bad:
        res = _result(pp, "STATIC_ONLY", person, journey, _dedup_findings(findings),
                      None, vps, unknowns, False, {"degrade_reason": bad})
        write_result(pp, res)
        return res

    allowed = {p["anchor"] for p in static_problems}
    llm_findings: List[dict] = []
    dropped = 0
    for f in out.get("findings") or []:
        if not isinstance(f, dict):
            continue
        anchor = str(f.get("anchor") or "")
        # 无锚点或锚点不在静态枚举集合内：不输出
        if anchor not in allowed:
            dropped += 1
            continue
        llm_findings.append(_finding({
            "rule": "persona",
            "problem": str(f.get("problem") or ""),
            "anchor": anchor,
            "component": "",
            "rage": f.get("rage") or 3,
            "task_interrupted": bool(f.get("task_interrupted")),
            "business_consequence": str(f.get("business_consequence")
                                        or "未给出业务后果，按最高 P2 处理"),
        }, "persona", {"mental_model_violation": f.get("mental_model_violation", "")}))

    merged = _dedup_findings(llm_findings + findings)
    unknowns = list(dict.fromkeys(unknowns + [str(u) for u in out.get("unknowns") or []]))
    extra = {"dropped_without_anchor": dropped} if dropped else {}
    res = _result(pp, "OK", person, journey, merged,
                  out.get("three_second") or {}, vps, unknowns, True, extra)
    write_result(pp, res)
    return res
