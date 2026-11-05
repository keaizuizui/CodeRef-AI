# -*- coding: utf-8 -*-
"""product_evidence — 产品业务契合度证据包（v5.18.0，确定性优先，无 LLM）。

只做「能从代码 / 文档静态确证」的枚举与锚点，不做「用户认为 / 市场如何」的判断：

  - 能力清单**自动枚举**：业务架构树（arch_framework）+ 前端菜单/表单枚举
    （FrontendInspector）+ 路由与服务端接口 + CLI 子命令；
  - 每条能力 / 声称 / 信号都带 `file:line`、路由或命令锚点；
  - 推不出的写 `unknown`，不写 0、不写满分、不编造市场或竞品数字。

输出供 `core/product_eval.py`（F 轨 F1 五问 + F2 链路核验）与评估台「产品」Tab。

诚实边界（与 scorecard / coderef_eval 的口径一致）：
  - 能力枚举可能遗漏（动态注册、反射、插件机制），报告必须如实标注，
    **不得宣称全量覆盖**；
  - 代码只能推出「该能力有无上游数据源」，不能宣称「用户手上有没有数据」，
    相关字段一律由上层标 `proxy=true`。
"""

import json
import os
import re
from datetime import datetime
from typing import Dict, Iterator, List, Optional, Tuple

from core.version import __version__

# ── 扫描边界（确定性、可复现；不做无上限全盘扫描） ──────────────────
_SKIP_DIRS = {
    ".git", ".coderef", ".venv", "venv", "__pycache__", "node_modules",
    "dist", "build", "coderef-report", "coderef-wiki", ".gitnexus",
    "tests", "test",
}
_PY_EXTS = (".py",)
_WEB_EXTS = (".js", ".jsx", ".ts", ".tsx", ".vue", ".html", ".htm")
_MAX_FILES = 3000
_MAX_FILE_BYTES = 1024 * 1024
_MAX_CLAIMS = 60
_MAX_SIGNAL_LINES = 20

# 路由 / 接口锚点（Flask / FastAPI / aiohttp 常见写法）
_ROUTE_RE = re.compile(
    r"""@[\w\.]+\.(?:route|get|post|put|patch|delete|websocket)\(\s*['"]([^'"]+)['"]""")
_JS_FETCH_RE = re.compile(r"""fetch\(\s*['"`](/[^'"`]+)['"`]""")
# CLI 子命令锚点
_ADD_PARSER_RE = re.compile(r"""add_parser\(\s*['"]([^'"]+)['"]""")

# 声称识别：README/文档中像"产品声称"的短句
_CLAIM_MARKERS = (
    "支持", "提供", "可以", "用于", "帮助", "让", "一键", "自动", "生成",
    "分析", "导出", "管理", "创建", "检测", "扫描", "评估", "报告", "集成",
    "support", "provide", "allow", "generate", "analyze", "export", "scan",
    "manage", "detect", "report", "create", "integrate",
)
# 市场/竞品数字：评估结论硬红线（出现即拒收）。TAM/SAM/SOM 只按整词匹配，
# 避免 re.IGNORECASE 把 same/some/sample/stamp 误判成缩写。
MARKET_NUMBER_RE = re.compile(
    r"((?i:市场份额|市占率|竞品|竞争对手|market\s*share|competitor)"
    r"|(?<![A-Za-z])(?:TAM|SAM|SOM)(?![A-Za-z]))"
    r"[^。\n]{0,24}?\d")

_PAYMENT_MARKERS = ("定价", "价格", "付费", "订阅", "购买", "套餐",
                    "pricing", "price", "subscription", "billing", "plan", "purchase")
_ROADMAP_MARKERS = ("TODO", "FIXME", "WIP", "未完成", "待实现", "计划中",
                    "planned", "roadmap", "coming soon")
# 硬编码 / 演示数据标记（用于数据诚实度的代码侧线索）
FAKE_DATA_MARKERS = ("mock", "dummy", "fake", "sample data", "演示数据",
                     "示例数据", "test data")
# 输出类模块命名线索（仅作可行动性的代码侧线索，不作绝对判断）
OUTPUT_HINTS = ("export", "report", "render", "output", "generate", "write",
                "save", "download", "publish")
# 输入类模块命名线索（仅作上游数据源存在的代码侧线索）
INPUT_HINTS = ("ingest", "import", "upload", "parse", "reader", "loader",
               "fetch", "crawler", "spider", "collect", "sync")


def _rel(project_path: str, path: str) -> str:
    try:
        return os.path.relpath(path, project_path).replace(os.sep, "/")
    except Exception:  # noqa: BLE001
        return path.replace(os.sep, "/")


def _iter_text(project_path: str, exts: Tuple[str, ...],
               max_files: int = _MAX_FILES) -> Iterator[Tuple[str, str]]:
    """稳定顺序产出 (相对路径, 文本)；跳过工具产物/依赖/超大文件。"""
    seen = 0
    for dirpath, dirnames, filenames in os.walk(project_path):
        dirnames[:] = sorted(
            d for d in dirnames
            if d.lower() not in _SKIP_DIRS and not d.startswith("."))
        for fn in sorted(filenames):
            if not fn.lower().endswith(exts):
                continue
            seen += 1
            if seen > max_files:
                return
            fp = os.path.join(dirpath, fn)
            try:
                if os.path.getsize(fp) > _MAX_FILE_BYTES:
                    continue
                with open(fp, "r", encoding="utf-8", errors="replace") as f:
                    yield _rel(project_path, fp), f.read()
            except OSError:
                continue


def _line_of(text: str, pos: int) -> int:
    return text.count("\n", 0, pos) + 1


def _find_text_anchor(project_path: str, needle: str,
                      exts: Tuple[str, ...] = _WEB_EXTS) -> str:
    """在 Web 源码里查找文本首次出现位置（菜单 label 等无行号的元素用）。"""
    if not needle:
        return ""
    needle_low = needle.lower()
    for rel, text in _iter_text(project_path, exts, max_files=200):
        idx = text.lower().find(needle_low)
        if idx >= 0:
            return f"{rel}#L{_line_of(text, idx)}"
    return ""


def _module_anchor(project_path: str, module: str) -> str:
    """模块（相对路径、无扩展名）→ `rel#L1` 锚点；文件不存在返回空串。"""
    mod = str(module or "").strip().replace("\\", "/")
    if not mod:
        return ""
    for ext in _PY_EXTS + (".ts", ".tsx", ".js", ".jsx", ".vue"):
        fp = os.path.join(project_path, mod + ext)
        if os.path.isfile(fp):
            return f"{mod}{ext}#L1"
    return ""


# ────────────────────────────────────────────────────────────────
# 能力枚举（四路自动汇合，不引入人工注册表）
# ────────────────────────────────────────────────────────────────

def _cap(source: str, name: str, anchors: List[str],
         modules: Optional[List[str]] = None, **extra) -> dict:
    item = {
        "id": f"{source}:{name}",
        "name": name,
        "source": source,
        "modules": modules or [],
        "anchors": [a for a in anchors if a][:4],
        "evidence_level": "E2",
    }
    item.update(extra)
    return item


def _framework_capabilities(project_path: str) -> Tuple[List[dict], bool]:
    """业务架构树的主功能层 → 能力；无图谱时返回空（由目录兜底）。"""
    try:
        from core.arch_framework import build_framework
        fw = (build_framework(project_path) or {}).get("framework") or {}
    except Exception:  # noqa: BLE001
        return [], False
    caps: List[dict] = []
    for child in fw.get("children") or []:
        mods = [str(m) for m in (child.get("modules") or []) if m]
        if not mods:
            continue
        anchors = [a for a in (_module_anchor(project_path, m) for m in mods[:3]) if a]
        if not anchors:
            continue
        caps.append(_cap("business_tree", str(child.get("title") or child.get("id") or "未命名"),
                         anchors, mods, business_id=str(child.get("id") or "")))
    return caps, bool(caps)


def _dir_capabilities(project_path: str) -> List[dict]:
    """无图谱兜底：顶层源码目录 → 能力（每目录取其首个源码文件作锚点）。"""
    caps: List[dict] = []
    try:
        entries = sorted(os.listdir(project_path))
    except OSError:
        return caps
    for name in entries:
        if name.startswith(".") or name.lower() in _SKIP_DIRS:
            continue
        d = os.path.join(project_path, name)
        if not os.path.isdir(d):
            continue
        first = ""
        for rel, _text in _iter_text(d, _PY_EXTS + _WEB_EXTS, max_files=1):
            first = rel
            break
        if not first:
            continue
        # _iter_text(d) 返回相对 d 的路径；必须补回目录前缀才是项目相对锚点，
        # 否则 core/core_utils.py 这类会被误判成已带前缀，产出不存在的路径。
        anchors = [f"{name}/{first}#L1"]
        caps.append(_cap("directory", name, anchors, [name]))
    return caps


def _frontend_capabilities(project_path: str) -> Tuple[List[dict], dict]:
    """前端菜单 L1 + 表单 → 能力；返回 (能力, 前端枚举摘要)。"""
    try:
        from core.frontend_inspector import FrontendInspector
        # llm=False：证据包只做确定性枚举，不初始化 LLM 客户端
        enum = FrontendInspector(llm=False).enumerate_static(project_path)
    except Exception as e:  # noqa: BLE001
        return [], {"applicable": False, "error": str(e)}
    if enum.get("applicable") is False:
        return [], {"applicable": False, "message": enum.get("message", "")}
    caps: List[dict] = []
    buttons = enum.get("buttons") or []
    forms = sorted({b.form for b in buttons if getattr(b, "form", "")})
    for fid in forms[:30]:
        anchors = list(dict.fromkeys(
            f"{_rel(project_path, b.file)}#L{b.line}"
            for b in buttons if b.form == fid))[:4]
        caps.append(_cap("frontend", f"表单 {fid}", anchors))
    for root in (enum.get("menu_roots") or [])[:30]:
        label = str(getattr(root, "label", "") or "").strip()
        if not label or label == "(无文本)":
            continue
        anchor = _find_text_anchor(project_path, label)
        if anchor:
            caps.append(_cap("frontend", f"菜单 {label}", [anchor]))
    summary = {
        "applicable": True,
        "files": len(enum.get("files") or []),
        "buttons": len(buttons),
        "menu_roots": len(enum.get("menu_roots") or []),
        "forms": len(forms),
        # HTML 与组件源码并存时只覆盖 HTML，必须向下游标注部分覆盖
        "partial": bool(enum.get("partial")),
        "coverage_note": str(enum.get("coverage_note") or ""),
    }
    return caps, summary


def _route_capabilities(project_path: str) -> List[dict]:
    """路由 / 前端调用接口 → 能力（按路径去重，保留首个锚点）。"""
    seen: Dict[str, dict] = {}
    for rel, text in _iter_text(project_path, _PY_EXTS + _WEB_EXTS):
        for rx in (_ROUTE_RE, _JS_FETCH_RE):
            for m in rx.finditer(text):
                path = m.group(1).strip()
                if not path or path in seen:
                    continue
                seen[path] = _cap("route", path, [f"{rel}#L{_line_of(text, m.start())}"])
    return [seen[k] for k in sorted(seen)]


def _cli_capabilities(project_path: str) -> List[dict]:
    """CLI 子命令 → 能力（argparse add_parser / pyproject 脚本入口）。"""
    seen: Dict[str, dict] = {}
    for rel, text in _iter_text(project_path, _PY_EXTS):
        for m in _ADD_PARSER_RE.finditer(text):
            name = m.group(1).strip()
            if name and name not in seen:
                seen[name] = _cap("cli", name, [f"{rel}#L{_line_of(text, m.start())}"])
    for fname in ("pyproject.toml", "setup.cfg"):
        fp = os.path.join(project_path, fname)
        if not os.path.isfile(fp):
            continue
        try:
            with open(fp, "r", encoding="utf-8", errors="replace") as f:
                text = f.read()
        except OSError:
            continue
        in_scripts = False
        for i, line in enumerate(text.splitlines(), 1):
            low = line.strip().lower()
            if low.startswith(("[project.scripts]", "[options.entry_points]")):
                in_scripts = True
                continue
            if in_scripts and line.strip().startswith("["):
                in_scripts = False
            if not in_scripts:
                continue
            m = re.match(r"\s*([\w\-.]+)\s*=", line)
            if m and m.group(1) not in seen:
                seen[m.group(1)] = _cap("cli", m.group(1), [f"{fname}#L{i}"])
    return [seen[k] for k in sorted(seen)]


def enumerate_capabilities(project_path: str) -> Tuple[List[dict], dict]:
    """四路能力枚举 + 去重；返回 (能力列表, 枚举摘要)。"""
    caps: List[dict] = []
    fw_caps, has_fw = _framework_capabilities(project_path)
    caps.extend(fw_caps)
    if not has_fw:
        caps.extend(_dir_capabilities(project_path))
    fe_caps, fe_summary = _frontend_capabilities(project_path)
    caps.extend(fe_caps)
    caps.extend(_route_capabilities(project_path))
    caps.extend(_cli_capabilities(project_path))

    # 按名称去重（不同来源同名视为同一能力，保留首个并合并锚点）
    merged: Dict[str, dict] = {}
    for c in caps:
        key = c["name"].strip().lower()
        if key not in merged:
            merged[key] = c
            continue
        keep = merged[key]
        keep["anchors"] = list(dict.fromkeys(keep["anchors"] + c["anchors"]))[:6]
        keep["modules"] = list(dict.fromkeys(keep["modules"] + c["modules"]))
        if c["source"] != keep["source"]:
            keep.setdefault("also_from", [])
            if c["source"] not in keep["also_from"]:
                keep["also_from"].append(c["source"])

    out = [merged[k] for k in sorted(merged)]
    summary = {
        "capability_count": len(out),
        "by_source": {},
        "frontend": fe_summary,
        "note": ("能力由业务树/前端/路由/CLI 自动枚举，可能遗漏动态注册与反射入口；"
                 "未覆盖能力数无法自动判定，故不宣称全量覆盖。"),
    }
    for c in out:
        summary["by_source"][c["source"]] = summary["by_source"].get(c["source"], 0) + 1
    return out, summary


# ────────────────────────────────────────────────────────────────
# 声称 / 首次价值路径 / 业务信号
# ────────────────────────────────────────────────────────────────

def _read_doc(project_path: str, name: str) -> Tuple[str, str]:
    fp = os.path.join(project_path, name)
    if not os.path.isfile(fp):
        return name, ""
    try:
        with open(fp, "r", encoding="utf-8", errors="replace") as f:
            return name, f.read()
    except OSError:
        return name, ""


def _key_in_text(key: str, text_low: str) -> bool:
    """能力键匹配：ASCII 键按词边界、中文键按子串（CJK 文本无空格分词）。

    裸子串会把 core 命中 scorecard、ui 命中 build，从而把无关声称标成已验证。
    """
    if not key:
        return False
    if re.fullmatch(r"[a-z0-9_.\-/ ]+", key):
        if len(key) < 2:
            return False
        return bool(re.search(r"(?<![a-z0-9])" + re.escape(key) + r"(?![a-z0-9])",
                              text_low))
    return len(key) >= 2 and key in text_low


def _clean_claim(line: str) -> str:
    text = line.strip()
    text = re.sub(r"^[-*+>\s]+", "", text)
    text = re.sub(r"^\d+\.\s+", "", text)
    text = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", text)   # 图片
    text = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", text)  # 链接保留文字
    text = text.replace("**", "").replace("`", "")
    return text.strip()


def extract_claims(project_path: str, capabilities: List[dict]) -> List[dict]:
    """从 README / docs 提取产品声称，并尽力匹配到已枚举能力。"""
    docs = [("README.md",)]
    for name in sorted(os.listdir(project_path)) if os.path.isdir(project_path) else []:
        if name.lower() in ("readme.md", "readme.rst", "readme.txt"):
            docs.append((name,))
    docs = list(dict.fromkeys(d[0] for d in docs))
    lines: List[Tuple[str, int, str]] = []
    for doc in docs[:4]:
        rel, text = _read_doc(project_path, doc)
        if not text:
            continue
        for i, raw in enumerate(text.splitlines(), 1):
            line = _clean_claim(raw)
            if not line or len(line) > 160:
                continue
            low = line.lower()
            if not any(m in low for m in _CLAIM_MARKERS):
                continue
            lines.append((rel, i, line))
            if len(lines) >= _MAX_CLAIMS:
                break
        if len(lines) >= _MAX_CLAIMS:
            break

    cap_names = [(c["name"], c.get("modules") or []) for c in capabilities]
    claims: List[dict] = []
    for rel, i, line in lines:
        low = line.lower()
        matched: List[str] = []
        for name, mods in cap_names:
            keys = [name] + [m.split("/")[-1].replace("_", " ") for m in mods[:2] if m]
            for k in keys:
                k = str(k).strip().lower()
                if _key_in_text(k, low):
                    matched.append(name)
                    break
        claims.append({
            "text": line,
            "anchor": f"{rel}#L{i}",
            "matched_capabilities": matched[:5],
            # 只有"声称里出现了已枚举能力/模块名"才算代码侧可核；
            # 匹配不上不等于没实现，保持 unknown，不武断判 false。
            "verified": "true" if matched else "unknown",
            # 声称来自文档，证据等级恒为 E3；是否匹配能力只影响 verified
            "evidence_level": "E3",
        })
    return claims


def first_value_path(project_path: str, capabilities: List[dict],
                     framework: Optional[dict] = None) -> dict:
    """首次价值路径：入口 → 命令/路由 → 首个能力（全部带锚点）。"""
    entries: List[str] = []
    if framework:
        entries = [str(m) for m in (framework.get("modules") or [])]
    steps: List[dict] = []
    for m in entries[:3]:
        a = _module_anchor(project_path, m)
        if a:
            steps.append({"label": f"入口 {m.split('/')[-1]}", "anchor": a})
    for src in ("cli", "route"):
        cand = next((c for c in capabilities if c["source"] == src), None)
        if cand:
            steps.append({"label": f"{'命令' if src == 'cli' else '接口'} {cand['name']}",
                          "anchor": cand["anchors"][0]})
    if capabilities:
        steps.append({"label": f"首个能力 {capabilities[0]['name']}",
                      "anchor": capabilities[0]["anchors"][0]})
    return {
        "steps": steps,
        "complete": bool(steps) and any(s["label"].startswith("入口") for s in steps),
        "evidence_level": "E2",
        "note": "只证明代码里存在入口与能力，不证明用户能顺利走到并拿到价值。",
    }


def business_signals(project_path: str) -> dict:
    """许可 / 付费 / 未完成路线图信号（代码与文档可核；不推断商业模式优劣）。"""
    license_files = sorted(
        n for n in (os.listdir(project_path) if os.path.isdir(project_path) else [])
        if n.upper().startswith(("LICENSE", "COPYING")))
    payment: List[dict] = []
    roadmap: List[dict] = []
    for doc in ("README.md", "docs/ROADMAP.md"):
        rel, text = _read_doc(project_path, doc)
        if not text:
            continue
        for i, raw in enumerate(text.splitlines(), 1):
            line = _clean_claim(raw)
            low = line.lower()
            if not line:
                continue
            if any(m in low for m in _PAYMENT_MARKERS) and len(payment) < _MAX_SIGNAL_LINES:
                payment.append({"text": line[:140], "anchor": f"{rel}#L{i}"})
            if any(m.lower() in low for m in _ROADMAP_MARKERS) and len(roadmap) < _MAX_SIGNAL_LINES:
                roadmap.append({"text": line[:140], "anchor": f"{rel}#L{i}"})
    return {
        "license": [{"file": n, "anchor": n} for n in license_files],
        "payment": payment,
        "roadmap_open": roadmap,
        "evidence_level": "E3",
    }


def _has_tests(project_path: str) -> bool:
    # 目录名检查要覆盖 _iter_text 跳过的常见测试目录，否则只有 test/ 的项目
    # 会被判成无测试，阶段退化到 concept。
    for d in ("tests", "test", "__tests__"):
        if os.path.isdir(os.path.join(project_path, d)):
            return True
    for _rel_path, _text in _iter_text(project_path, _PY_EXTS + _WEB_EXTS, max_files=200):
        base = _rel_path.split("/")[-1].lower()
        if base.startswith("test_") or ".test." in base or ".spec." in base:
            return True
    return False


def _auto_stage(capability_count: int, has_tests: bool, signals: dict) -> dict:
    """阶段判定（启发式）：概念期 / PMF 搜索期 / 扩张期。禁止用成熟期指标判原型。"""
    licensed = bool(signals.get("license"))
    paying = bool(signals.get("payment"))
    if capability_count <= 3 and not has_tests and not licensed:
        phase, reason = "concept", "能力 ≤3 且无测试/许可信号，按概念期评估"
    elif capability_count >= 10 and has_tests and (licensed or paying):
        phase, reason = "expansion", "能力 ≥10 且有测试与许可/付费信号，按扩张期评估"
    else:
        phase, reason = "pmf", "已有能力与部分验证，按 PMF 搜索期评估"
    return {"phase": phase, "reason": reason, "decided_by": "auto"}


def _read_stage_override(project_path: str) -> Optional[dict]:
    fp = os.path.join(os.path.abspath(project_path), ".coderef", "eval_config.json")
    try:
        with open(fp, "r", encoding="utf-8") as f:
            cfg = json.load(f)
    except Exception:  # noqa: BLE001
        return None
    st = (cfg.get("product") or {}).get("stage") if isinstance(cfg, dict) else None
    if isinstance(st, dict) and st.get("phase") in ("concept", "pmf", "expansion"):
        return {"phase": st["phase"],
                "reason": str(st.get("reason") or "项目级确认门记录"),
                "decided_by": "user"}
    return None


def save_stage(project_path: str, phase: str, reason: str = "") -> dict:
    """把确认门结果写回 `.coderef/eval_config.json`（不改入库默认值）。"""
    from core.scorecard import _atomic_write_json

    if phase not in ("concept", "pmf", "expansion"):
        raise ValueError(f"未知阶段: {phase!r}")
    path = os.path.join(os.path.abspath(project_path), ".coderef", "eval_config.json")
    try:
        with open(path, "r", encoding="utf-8") as f:
            cfg = json.load(f)
        if not isinstance(cfg, dict):
            cfg = {}
    except Exception:  # noqa: BLE001
        cfg = {}
    cfg["product"] = {"stage": {"phase": phase,
                                "reason": reason or "用户确认门记录",
                                "confirmed_at": datetime.now().astimezone().isoformat(
                                    timespec="seconds")}}
    _atomic_write_json(path, cfg)
    return cfg["product"]["stage"]


# ────────────────────────────────────────────────────────────────
# 顶层：证据包
# ────────────────────────────────────────────────────────────────

def build_evidence(project_path: str) -> dict:
    """构建确定性证据包（无 LLM、无网络、同输入可复现）。"""
    pp = os.path.abspath(project_path)
    capabilities, enum_summary = enumerate_capabilities(pp)
    signals = business_signals(pp)
    claims = extract_claims(pp, capabilities)

    framework = None
    try:
        from core.arch_framework import build_framework
        framework = (build_framework(pp) or {}).get("framework")
    except Exception:  # noqa: BLE001
        framework = None
    path = first_value_path(pp, capabilities, framework)

    auto_stage = _auto_stage(len(capabilities), _has_tests(pp), signals)
    stage = _read_stage_override(pp) or auto_stage

    unknowns: List[str] = []
    if not claims:
        unknowns.append("未从 README/文档提取到产品声称，无法核对「声称 vs 实现」")
    fe = enum_summary.get("frontend", {}) or {}
    if fe.get("applicable") is False:
        unknowns.append(
            fe.get("message")
            or "未检测到用户维护的前端源码，无法核对用户可见文案与首次价值路径")
    elif fe.get("partial"):
        unknowns.append(
            fe.get("coverage_note")
            or "项目含组件源码，静态枚举只覆盖 HTML 部分，组件交互未核对")
    if not capabilities:
        unknowns.append("未枚举到任何能力（无业务树/前端/路由/CLI），无法评估业务契合度")
    unknowns.append("代码只能推断「该能力有无上游数据源」，不能证明「用户手上是否有该数据」")
    unknowns.append("能力为自动枚举，动态注册/反射/插件入口可能遗漏，不宣称全量覆盖")

    return {
        "version": __version__,
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "project": os.path.basename(pp.rstrip(os.sep)),
        "stage": stage,
        "capabilities": capabilities,
        "enumeration": enum_summary,
        "claims": claims,
        "first_value_path": path,
        "signals": signals,
        "unknowns": unknowns,
        "evidence_note": ("本证据包为静态确证（E2/E3），不含市场/竞品数字；"
                          "所有推断项由上层评估标注 proxy / E4 并进入待验证假设。"),
    }
