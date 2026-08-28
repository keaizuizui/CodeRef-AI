"""软件类型模板：识别项目形态 → 生成 target_arch 初稿 + 整理建议（引导非强求）。

模板是"整理建议"而非"强制标准"：识别项目属于哪类常见软件形态，按该形态的
期望目录骨架生成 target_arch 初稿与文件夹整理建议；编程 AI 可自主决定是否照做，
不做也不阻断工具使用。纯静态、确定性，不依赖 LLM。
"""

import os
import re

# 顶层通用/非业务目录（识别业务模块、判定项目形态时排除）
# 说明：除框架/基础设施目录外，也含“输出/报告/制品”类目录——这类目录是工具
# 或构建的产物而非业务源码，detect/[业务模块] 不应把它们当业务模块。
_COMMON_DIRS = {
    "tests", "test", "docs", "doc", "config", "conf", "data", "output", "out",
    "scripts", "script", "archive", "backup", "_备份", "uploads", "templates",
    "static", "media", "assets", "venv", "node_modules", "__pycache__",
    "third_party", "migrations", "wiki", "knowledge", "logs", "cache", "tmp",
    "temp", "reports", "dist", "build", "knowledge_base", "知识库", "projects",
    "checkpoints", ".checkpoints",
    # 输出/报告/制品类（产物而非业务源码）
    "coderef-report", "report", "result", "results", "artifacts", "generated",
    "产出", "报告", "制品",
}

# 六边形识别加分目录
_HEX_DIRS = {"domain", "application", "adapters", "infrastructure", "ports",
             "use_cases", "usecases"}

# 框架依赖关键词（六边形等后端服务的强信号）
_BACKEND_DEPS = ["fastapi", "flask", "django", "spring", "nestjs", "express",
                 "gin", "echo", "tornado", "aiohttp", "quart"]

# 模块化单体识别：共享底座目录 + 业务模块数下限
_MODULAR_SHARED_DIRS = {"shared", "common", "core", "配置中心", "shared_lib"}

# 中性缺省职责语义词（角色未配置 role_keywords 时兜底）。
# 刻意不含目录匹配泛词 app/main/entry，避免 role_boundary 符号级职责判定误判。
_DEFAULT_ROLE_KEYWORDS = ["核心职责"]


def _top_dirs(project_path: str) -> list:
    """顶层非隐藏目录名（排除 . 开头与 __pycache__）。"""
    out = []
    try:
        for name in sorted(os.listdir(project_path)):
            if name.startswith(".") or name == "__pycache__":
                continue
            full = os.path.join(project_path, name)
            if os.path.isdir(full):
                out.append(name)
    except OSError:
        pass
    return out


def _is_common_dir(name: str) -> bool:
    return name.lower() in _COMMON_DIRS


def _biz_dirs(project_path: str) -> list:
    """业务模块目录：顶层目录排除通用/隐藏目录。"""
    return [d for d in _top_dirs(project_path) if not _is_common_dir(d)]


def _deps_hint(project_path: str) -> set:
    """读取常见依赖清单，返回命中的后端框架关键词。"""
    hits = set()
    candidates = []
    for f in ("requirements.txt", "pyproject.toml", "package.json", "go.mod"):
        p = os.path.join(project_path, f)
        if os.path.exists(p):
            try:
                with open(p, "r", encoding="utf-8", errors="ignore") as fh:
                    candidates.append(fh.read(20000))
            except OSError:
                pass
    blob = "\n".join(candidates).lower()
    for dep in _BACKEND_DEPS:
        if re.search(r"\b" + re.escape(dep) + r"\b", blob):
            hits.add(dep)
    return hits


# ---------------------------------------------------------------------------
# 模板注册表
# ---------------------------------------------------------------------------

TEMPLATES = {
    "hexagonal": {
        "name": "六边形单体",
        "desc": "单体后端服务：业务核心（domain/use-cases）与外部技术（adapters/infrastructure）解耦，依赖向内",
        "detect_dirs": ["domain", "application", "adapters", "infrastructure",
                        "ports", "use_cases", "usecases"],
        "role_skeleton": [
            {"id": "api_entry", "name": "接口入口",
             "kw": ["controller", "route", "api", "web", "handler", "interface"],
             "role_keywords": ["controller", "handler", "endpoint", "view", "route",
                               "接口", "路由", "api", "http"]},
            {"id": "use_case", "name": "应用用例",
             "kw": ["use_case", "usecase", "service", "application"],
             "role_keywords": ["service", "usecase", "interactor", "用例",
                               "应用服务", "业务编排"]},
            {"id": "domain_core", "name": "领域核心",
             "kw": ["domain", "model", "entity", "core"],
             "role_keywords": ["domain", "model", "entity", "event", "value_object",
                               "领域", "领域服务", "实体"]},
            {"id": "outbound_adapter", "name": "出站适配",
             "kw": ["repository", "repo", "adapter", "infrastructure", "db",
                    "storage", "persistence"],
             "role_keywords": ["repository", "repo", "adapter", "persistence",
                               "storage", "infra", "基础设施", "仓储", "持久化",
                               "数据访问", "外部服务客户端", "适配器"]},
        ],
        "suggest_dirs": [
            ("domain", "领域模型与业务规则，零框架依赖"),
            ("application", "用例编排与端口接口（use_cases / ports）"),
            ("adapters", "入站/出站适配器（controller、数据库、外部 API 客户端）"),
        ],
    },
    "modular_monolith": {
        "name": "模块化单体",
        "desc": "按业务域平铺多个模块 + 共享底座，模块间依赖受控（模块内部可再用分层/六边形）",
        "detect_shared_dirs": ["shared", "common", "core", "配置中心", "shared_lib"],
        "role_skeleton": [
            {"id": "entry_point", "name": "入口层",
             "kw": ["web", "gui", "main", "app", "entry"],
             "role_keywords": ["入口", "服务入口", "接口层", "启动器",
                               "命令行", "launcher", "cli", "server"]},
            {"id": "business_modules", "name": "业务模块", "all_biz_dirs": True,
             "role_keywords": []},
            {"id": "shared_base", "name": "共享底座",
             "kw": ["shared", "common", "core", "base", "config", "配置"],
             "role_keywords": ["基础设施", "配置", "公共组件", "工具", "utils",
                               "helper", "common", "shared", "base", "core",
                               "config", "缓存", "日志", "异常", "middleware"]},
        ],
        "suggest_dirs": [
            ("shared", "共享底座：跨模块复用的通用能力（日志/配置/公共模型）"),
            ("（业务模块）", "按业务域平铺模块目录；模块内部建议 domain/use-case/adapter 分层"),
        ],
    },
}


def list_templates() -> dict:
    """模板清单（供 MCP 工具描述与校验）。"""
    return {tid: {"name": t["name"], "desc": t["desc"]}
            for tid, t in TEMPLATES.items()}


def detect_template(project_path: str):
    """按目录/依赖特征识别项目类型，返回 (template_id, score) 或 None。"""
    dirs = _top_dirs(project_path)
    lower = {d.lower() for d in dirs}
    deps = _deps_hint(project_path)

    hex_hits = len(lower & _HEX_DIRS)
    hex_score = 0.0
    if hex_hits >= 2:
        hex_score = 2.0
    elif hex_hits == 1:
        hex_score = 0.8
    if deps:
        hex_score += 0.5

    shared_hits = lower & _MODULAR_SHARED_DIRS
    biz_count = len([d for d in dirs if not _is_common_dir(d)])
    mod_score = 0.0
    if shared_hits and biz_count >= 3:
        mod_score = 2.0
    elif biz_count >= 5 and not shared_hits:
        mod_score = 1.0

    if hex_score >= mod_score and hex_score >= 0.8:
        return "hexagonal", hex_score
    if mod_score >= 0.8:
        return "modular_monolith", mod_score
    return None


def _expand_module_specs(project_path: str, top_dir: str, max_depth: int = 5) -> list:
    """把顶层目录名展开为模块级相对路径 spec，用于覆盖其下整棵子树的命中。

    消费方匹配是精确匹配 + basename 兜底，目录前缀无法直接命中子路径模块，故把
    目录下的子包（含 __init__.py 的目录）与 .py 模块递归收集为模块级相对路径。
    top_dir 本身也保留（作为覆盖锚点，且它通常物理存在，不产生 missing）。
    跳过 __init__.py/__main__.py/conftest.py/setup.py、隐藏目录与 test/tests 目录，
    避免污染。
    """
    specs = [top_dir]
    try:
        if not os.path.isdir(os.path.join(project_path, top_dir)):
            return specs
    except OSError:
        return specs

    def walk(rel: str, depth: int) -> None:
        full = os.path.join(project_path, rel)
        try:
            entries = sorted(os.listdir(full))
        except OSError:
            return
        for e in entries:
            if e.startswith(".") or e == "__pycache__":
                continue
            full_e = os.path.join(full, e)
            rel_e = f"{rel}/{e}"
            if os.path.isdir(full_e):
                if e in ("test", "tests"):
                    continue
                specs.append(rel_e)
                if depth < max_depth:
                    walk(rel_e, depth + 1)
            elif e.endswith(".py") and e not in ("__init__.py", "__main__.py",
                                                 "conftest.py", "setup.py"):
                specs.append(rel_e[:-3])

    walk(top_dir, 0)
    return specs


def build_target_arch(project_path: str, template_id: str) -> dict:
    """按模板生成 target_arch 初稿（结合项目实际顶层目录匹配 target_modules）。

    target_modules 采用“目录前缀覆盖”语义：对每个被目录关键词匹配到的顶层目录，
    递归展开其下的子包与 .py 模块，产出模块级相对路径 spec。这是因为下游
    （arch_gap_analyzer._match_module_ids）按【相对路径精确匹配 + basename 兜底】
    评审 module_assigned 覆盖率——若只写顶层目录名（如 agents），粒度错位会导致
    游离模块（agents/best_image_selector）几乎全部命中不了，
    初稿覆盖率骤降。展开既能覆盖整棵子树，也不破坏 target_arch_schema
    （target_modules 仍是字符串数组）。
    """
    tpl = TEMPLATES[template_id]
    dirs = _biz_dirs(project_path)
    all_dirs = _top_dirs(project_path)

    roles = []
    used = set()
    for r in tpl["role_skeleton"]:
        kw = r.get("kw") or []
        if r.get("all_biz_dirs"):
            # 业务模块全量角色：排除模板共享底座目录，留给 shared_base 等角色
            excluded = {d.lower() for d in tpl.get("detect_shared_dirs", [])}
            matched_dirs = [d for d in dirs if d.lower() not in excluded and d not in used]
        else:
            matched_dirs = []
            for d in all_dirs:
                if d in used:
                    continue
                dl = d.lower()
                if any(k in dl for k in kw):
                    matched_dirs.append(d)
        used.update(matched_dirs)
        # 目录前缀覆盖：把每个匹配目录递归展开为模块级 spec
        target_modules = []
        for d in matched_dirs:
            target_modules.extend(_expand_module_specs(project_path, d))
        # role_keywords 取“职责语义词表”（供 role_boundary 符号级职责判定），
        # 与“目录匹配词表 kw”分离；未配置职责词的角色给中性缺省（不含泛词 app/main/entry）。
        role_keywords = r.get("role_keywords") or _DEFAULT_ROLE_KEYWORDS
        role = {"id": r["id"], "name": r["name"], "target_modules": target_modules,
                "role_keywords": role_keywords}
        roles.append(role)

    flows = _build_flows(template_id, roles)
    return {
        "version": "5.0",
        "project": os.path.basename(os.path.normpath(project_path)) or project_path,
        "tech_roles": roles,
        "business_flows": flows,
        "constraints": [],
    }


def _build_flows(template_id: str, roles: list) -> list:
    """按模板生成一条主干业务流骨架（steps 引用已定义角色 id）。"""
    by_id = {r["id"]: r for r in roles}
    if template_id == "hexagonal":
        chain = ["api_entry", "use_case", "domain_core", "outbound_adapter"]
        title = "主业务流程"
    else:
        chain = ["entry_point", "business_modules", "shared_base"]
        title = "跨模块主干"
    steps = []
    for rid in chain:
        if rid not in by_id:
            continue
        steps.append({"id": rid, "name": by_id[rid]["name"], "tech_roles": [rid]})
    if not steps:
        return []
    return [{"id": "main_flow", "name": title, "steps": steps}]


def templating_suggestions(project_path: str, template_id: str) -> list:
    """对比模板期望目录骨架，给出整理建议（缺失目录/建议归类，引导非强求）。"""
    tpl = TEMPLATES[template_id]
    lower = {d.lower(): d for d in _top_dirs(project_path)}
    suggestions = []
    for path, purpose in tpl["suggest_dirs"]:
        if path.startswith("（"):
            continue
        if path not in lower:
            suggestions.append({
                "type": "add_dir",
                "path": path,
                "purpose": purpose,
            })
    for d in _biz_dirs(project_path):
        dl = d.lower()
        covered = False
        for r in tpl["role_skeleton"]:
            kw = r.get("kw") or []
            if r.get("all_biz_dirs"):
                covered = True
                break
            if any(k in dl for k in kw):
                covered = True
                break
        if not covered:
            suggestions.append({
                "type": "review_dir",
                "path": d,
                "purpose": "未落入模板期望角色，建议归入对应模块或明确其为业务模块",
            })
    return suggestions
