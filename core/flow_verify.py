# -*- coding: utf-8 -*-
"""
flow_verify — 流程合规验证（正式集成 v1.0）

目标读者：非编程人员。
核心问题：我设定的期望流程（A→B→C→D→结果），代码里到底有没有真的按这条管线走通？

定位：静态优先、确定性优先。数据只来自知识图谱 CALLS 边，不依赖 LLM。
它既是"非编程人员验证流程是否按预期执行"的入口，也是编程 AI 的客观参考。

设计原则（吸取 workflow_graph 依赖 GitNexus 不可靠的教训）：
- 纯静态、确定性：数据只来自知识图谱 CALLS 边，不依赖 LLM。
- 绝不误导：能确证标 ordered；在管线但顺序未确证标 in_pipeline；
  入口管线外 / 可能动态调用标 outside；项目里根本无对应符号才标 missing。
- 期望流程的中文步骤 → 代码符号的映射，由调用方（用户的编程 AI）预完成，
  本工具接收"已映射的符号关键词"，避免把语义鸿沟硬塞给静态引擎。

集成方式：作为 MCP 工具 coderef_flow_verify 暴露。
入口：project_path + entry（入口符号，支持 模块.函数）+ steps（期望步骤符号关键词列表）。
图谱自动定位：使用 CodeKnowledgeGraph(project_path).db_path，图谱不存在时明确反馈需先构建。
"""

import os
import re
from collections import defaultdict
from html import escape as _esc
from typing import Dict, List, Optional, Set, Tuple
from core.graph_closure import load_graph, load_call_edges, file_base, downstream

try:
    from loguru import logger
except Exception:  # 单元测试/无 loguru 环境仍可运行
    logger = None


def _log(msg: str):
    if logger:
        logger.info(f"[flow_verify] {msg}")


def _kg_db_path(project_path: str) -> str:
    """定位项目知识图谱数据库路径（与 coderef 其它工具一致）。"""
    from core.code_knowledge_graph import CodeKnowledgeGraph
    return CodeKnowledgeGraph(project_path).db_path


def ensure_kg(project_path: str, db_path: Optional[str] = None) -> str:
    """确保知识图谱就绪：图谱缺失时自动构建（full 全量），返回 db 路径。

    流程验证依赖图谱 CALLS 边。若调用方未预先执行 coderef_memory_sync 构建图谱，
    本函数会就地补建，避免 flow_verify 因图谱缺失而整体短路（defect-hit 0% 的根因）。
    构建失败时返回原 db_path（交给调用方按 has_kg:false 诚实反馈）。
    """
    if db_path is None:
        db_path = _kg_db_path(project_path)
    if os.path.exists(db_path):
        return db_path
    try:
        from core.memory_layer import MemoryLayer
        _log(f"图谱缺失({db_path})，自动执行 memory_sync 构建…")
        r = MemoryLayer().sync(project_path, mode="full")
        if r.get("status") == "ok" and os.path.exists(db_path):
            _log(f"图谱自动构建完成: {r.get('kg')}")
            return db_path
        _log(f"图谱自动构建未完成: status={r.get('status')} kg={r.get('kg')}")
    except Exception as e:  # pragma: no cover
        _log(f"图谱自动构建异常: {type(e).__name__}: {e}")
    return db_path


# ═══════════════════════════════════════════════════════════════════
# 验证器
# ═══════════════════════════════════════════════════════════════════

class FlowVerifier:
    def __init__(self, db_path: str):
        self.nodes, self.adj = load_graph(db_path)
        # CALLS 边 props（含 keyword_args），供参数契约检测
        try:
            self.call_edges = load_call_edges(db_path)
        except Exception:  # pragma: no cover
            self.call_edges = {}
        # 名称索引：小写名 -> [id]；也索引方法后缀名
        self.name_index = defaultdict(list)
        for nid, n in self.nodes.items():
            self.name_index[n["name"].lower()].append(nid)
            if "." in n["name"]:
                self.name_index[n["name"].split(".")[-1].lower()].append(nid)

    # ─── 入口定位 ───

    def find_entry(self, spec: str) -> Optional[str]:
        """定位入口。支持：
        - 'func'                      → 唯一/首个名为 func 的节点
        - 'module.func'               → 限定模块
        - 'module'                    → 该模块节点自身
        - 完整节点 id                  → 直接命中（如 class:pipeline_runner:Pipe）
        """
        spec = spec.strip()
        if spec in self.nodes:
            return spec
        if "." in spec:
            # 支持 模块.函数 / 模块.类.方法 / 任意层级限定。
            # 取最后一段为符号名，其余为限定前缀（模块路径某一段或类名）。
            *prefixes, name = spec.split(".")
            prefixes = [p for p in prefixes if p]
            best = None
            for nid, n in self.nodes.items():
                if n["name"] == name and prefixes:
                    fp = (n.get("file_path") or "").lower()
                    nm = n["name"].lower()
                    if any(p in fp or p in nm for p in prefixes):
                        if best is None or n["type"] in ("function", "method"):
                            best = nid
            if best:
                return best
        key = spec.lower()
        if key in self.name_index:
            return self.name_index[key][0]
        for nid, n in self.nodes.items():
            if key in n["name"].lower():
                return nid
        return None

    # ─── 步骤匹配（三层） ───

    def match_step_nodes(self, keyword: str) -> List[str]:
        """三层匹配：符号名 / docstring / 模块名。返回节点 id 列表（按匹配强度排序）。"""
        kw = keyword.lower()
        scored: List[Tuple[int, str]] = []
        seen = set()

        def _add(nid, prio):
            if nid not in seen:
                seen.add(nid)
                scored.append((prio, nid))

        # 层0：符号名精确相等（含方法后缀）——最强
        for nid in self.name_index.get(kw, []):
            _add(nid, 0)
        # 层1：符号名包含关键词
        for nid, n in self.nodes.items():
            if kw and kw in n["name"].lower():
                _add(nid, 1)
        # 层2：docstring
        for nid, n in self.nodes.items():
            doc = (n.get("props") or {}).get("doc") or ""
            if kw and kw in doc.lower():
                _add(nid, 2)

        # 注：不按完整文件路径匹配——路径含关键词会误伤该模块内所有函数
        #（如 governance_audit.py 里所有函数都会被 "audit" 命中）。模块名
        # 由层1"名称包含"覆盖（mod:governance_audit 的 name 本身含 audit）。

        scored.sort(key=lambda x: (x[0], 0 if self.nodes[x[1]]["type"] in
                                   ("function", "method") else 1))
        return [nid for _, nid in scored]

    def _downstream(self, start_id: str, max_depth: int = 8) -> Set[str]:
        return downstream(self.adj, start_id, max_depth=max_depth)

    # ─── 参数契约检测 ───

    @staticmethod
    def _normalize_params(params: List[str]):
        """把签名参数列表归一化为形参名集合，并返回是否含 **kwargs。

        处理形式：`a` / `a=1` / `a: int` / `a: int = 5` / `*args` / `**kwargs`。
        `**kwargs` 表明可接受任意关键字参数，契约检测应放行。
        """
        names: Set[str] = set()
        has_kwargs = False
        for p in params or []:
            s = str(p).strip()
            if not s:
                continue
            if s.startswith("**"):
                has_kwargs = True
                continue
            if s.startswith("*"):
                continue  # *args 只接收位置参数
            name = s.split("=")[0].split(":")[0].strip()
            if name:
                names.add(name)
        return names, has_kwargs

    def param_contract_scan(self) -> List[dict]:
        """扫描图谱内所有 CALLS 边，检测「调用点显式关键字参数名 ≠ 被调函数签名形参名」。

        这是静态可确证的硬信号（如调 script2video_pipeline(character_registry=...) 而签名是
        character_portraits_registry），无需 LLM。仅检测图谱内已索引的项目内函数：
        - 被调节点必须是项目内函数/方法（第三方/内置不在图谱，天然跳过）；
        - 被调签名含 **kwargs 时放行（可接受任意关键字参数）；
        - 仅报告显式 `name=value` 关键字参数，忽略位置参数与 **kwargs 展开。
        """
        issues: List[dict] = []
        for (src, tgt), edge_list in self.call_edges.items():
            for props in edge_list:
                kw = props.get("keyword_args") or []
                if not kw:
                    continue
                tgt_node = self.nodes.get(tgt)
                if not tgt_node or tgt_node["type"] not in ("function", "method"):
                    continue
                params = (tgt_node.get("props") or {}).get("params") or []
                names, has_kwargs = self._normalize_params(params)
                if has_kwargs:
                    continue
                bad = [k for k in kw if k not in names]
                if not bad:
                    continue
                src_node = self.nodes.get(src)
                issues.append({
                    "caller": src_node["name"] if src_node else src,
                    "caller_id": src,
                    "callee": tgt_node["name"],
                    "callee_file": file_base(tgt_node),
                    "callee_line": tgt_node.get("start_line", 0),
                    "call_line": props.get("line", 0),
                    "mismatch": bad,
                    "params": sorted(names),
                })
        # 稳定排序，保证跨调用确定性
        issues.sort(key=lambda x: (x["callee_file"], x["callee_line"], x["call_line"]))
        return issues

    # ─── 公开查询（供 Wiki 等下游复用，避免外部直查 schema / 私有成员）───

    def root_functions(self) -> Set[str]:
        """返回无被调用方（无 inbound CALLS 边）的函数/方法名集合——公共入口候选。"""
        called = {t for targets in self.adj.values() for t in targets}
        return {n["name"].split(".")[-1] for nid, n in self.nodes.items()
                if n["type"] in ("function", "method") and nid not in called}

    def entry_chain(self, spec: str, max_depth: int = 8) -> List[dict]:
        """返回入口的实证下游调用链（函数/方法节点），步骤含 name/file/line/doc。

        入口未命中返回 []；图谱由本实例承载，不重复加载全图。
        输出按 (文件路径, 起始行) 稳定排序，保证跨调用确定性。
        """
        node = self.find_entry(spec)
        if not node:
            return []
        reach = self._downstream(node, max_depth=max_depth)
        steps: List[dict] = []
        seen: Set[str] = set()
        for nid in reach:
            n = self.nodes[nid]
            if n["type"] in ("function", "method") and nid not in seen:
                seen.add(nid)
                steps.append({
                    "name": n["name"],
                    "file": (n.get("file_path") or "").replace("\\", "/"),
                    "line": n.get("start_line", 0),
                    "doc": (n.get("props") or {}).get("doc", "") or "",
                })
        steps.sort(key=lambda s: (s["file"], s["line"], s["name"]))
        return steps

    def cross_module_flows(self) -> List[dict]:
        """把 CALLS 边聚合为跨模块业务数据流（模块→模块）。

        返回 [{source, target, funcs, count}]。count=不同(源文件,目标文件,被调函数)
        组合数。模块名取"文件所在目录名"，兼容相对(brand/app.py→brand)与绝对
        (c:\\...\\core\\a.py→core)路径；复用本实例已加载的全图，不直查 schema。
        """
        def _mod(fp: str) -> str:
            d = os.path.dirname(os.path.normpath(fp or ""))
            return os.path.basename(d) or ""

        flows: Dict[Tuple[str, str], dict] = {}
        seen_keys: Set[Tuple[str, str, str]] = set()
        for src, targets in self.adj.items():
            sfile = self.nodes[src].get("file_path", "")
            smod = _mod(sfile)
            if not smod:
                continue
            for tgt in targets:
                tfile = self.nodes[tgt].get("file_path", "")
                tmod = _mod(tfile)
                if not tmod or tmod == smod:
                    continue
                callee = self.nodes[tgt].get("name", "")
                # 去重键用完整 (源文件, 目标文件, 被调函数) 而非仅模块名，
                # 避免同一模块对内的多条边因共享模块名被误判为同一条，导致 count 低估。
                key = (sfile, tfile, callee)
                if key in seen_keys:
                    continue
                seen_keys.add(key)
                fid = flows.setdefault((smod, tmod), {
                    "source": smod, "target": tmod, "funcs": set(), "count": 0})
                fid["funcs"].add(callee)
                fid["count"] += 1
        out: List[dict] = []
        for v in flows.values():
            v["funcs"] = sorted(v["funcs"])[:20]
            out.append(v)
        out.sort(key=lambda x: (-x["count"], x["source"], x["target"]))
        return out

    # ─── 主验证 ───

    def _cross_lang_nodes(self) -> List[dict]:
        """列出图谱中所有非 Python 语言节点（如 Go），供跨语言补盲。

        只输出结构化节点元信息（name/type/file/line），不 dump docstring。
        docstring 常含代码片段与路径引用（如 Go 节点 doc 里出现
        "php/worker.php"、插件名等），若一并 dump 会与缺陷关键词/文件路径
        发生文本子串误命中，把"节点清单"误判为"缺陷检出"（假阳性）。
        """
        return sorted(
            [{"name": n["name"], "type": n.get("type", ""),
              "file": (n.get("file_path") or "").replace("\\", "/"),
              "line": n.get("start_line", 0)}
             for nid, n in self.nodes.items()
             if (n.get("props") or {}).get("language") == "go"],
            key=lambda x: (x["file"], x["line"]))

    def verify(self, entry_spec: str, steps: List[str],
               max_depth: int = 8) -> dict:
        """验证入口管线是否覆盖期望步骤。

        区分两个层面（旁路原型 v2 的核心修正）：
        - 存在性：步骤符号是否在"入口为根"的调用闭包内（root_reach）。
          真实项目入口往往直接调用多个并行工具，它们是兄弟节点，而非串行链路，
          因此必须用入口的完整下游做存在性判断，而不是每步收窄 frontier。
        - 顺序性：能否从"已确证的上一步"继续推下一步（linear_reach）。
          兄弟/并行工具之间没有 CALLS 边，顺序推不出来时就诚实标记 in_pipeline，
          而不是错误地判为 outside 或 missing。
        """
        entry = self.find_entry(entry_spec)
        if not entry:
            return {"entry": {"spec": entry_spec, "found": False},
                    "steps": [], "ok": False,
                    "summary": f"入口未找到: {entry_spec}",
                    "cross_lang": self._cross_lang_nodes()}

        en = self.nodes[entry]
        root_reach = self._downstream(entry, max_depth=max_depth)

        # 参数契约检测：全图「调用关键字参数名 ≠ 签名形参名」的静态可确证信号。
        # 独立于步骤匹配输出，避免与入口闭包是否包含该调用边耦合——即使某步骤因
        # 动态调用被判 outside，契约断裂仍能如实上报（如 vimax 的 character_registry 断链）。
        contracts = self.param_contract_scan()
        # 跨语言组件补盲：图谱中非 Python 语言节点（如 Go）即使不在入口闭包/步骤内也
        # 一并列出，供调用方/缺陷命中识别"存在但可能未接入主链路"的跨语言函数
        #（如 目标项目 augmented/ 的 Go 增强检索）。纯 Python 项目此列表为空，零影响。
        cross_lang = self._cross_lang_nodes()
        result = {
            "entry": {"spec": entry_spec, "found": True,
                      "id": entry,
                      "node": f"{en['name']} ({file_base(en)}:{en['start_line']})"},
            "steps": [],
            "param_contract": contracts,
            # 契约断裂若发生在入口调用闭包内，作为高置信信号触发 ok=False，
            # 避免"流程走通但参数对不上"的假通过；闭包外的契约断裂仅如实展示。
            "param_contract_in_entry": [c for c in contracts
                                        if c.get("caller_id") in root_reach],
            # 跨语言组件（Go 等）清单 —— flow_verify 跨语言补盲
            "cross_lang": cross_lang,
            # ok：存在性层面是否全部通过（无 outside / missing）。
            # order_confirmed：是否所有命中步骤都达到顺序确证（ordered）。
            # 两者分离，避免把"在管线但顺序未确证"当成完全成功，避免误导非编程人员。
            "ok": True,
            "order_confirmed": True,
            "graph_stats": {"nodes": len(self.nodes),
                            "calls_edges": sum(len(v) for v in self.adj.values())},
        }

        current = entry
        for step in steps:
            hits = self.match_step_nodes(step)
            if not hits:
                result["steps"].append({
                    "keyword": step, "status": "missing",
                    "reason": "项目图谱中找不到含该关键词的符号/docstring/模块名",
                    "evidence": [], "candidates": [],
                })
                result["ok"] = False
                result["order_confirmed"] = False
                continue

            # 1) 存在性：是否在入口调用闭包内
            in_pipe = [sid for sid in hits if sid in root_reach]
            if not in_pipe:
                result["steps"].append({
                    "keyword": step, "status": "outside",
                    "reason": "找到对应符号，但不在入口的静态调用链上——"
                              "可能该步骤并行/独立于入口，或经动态注册/反射/运行时拼装调用"
                              "（静态图谱会丢这类链路）",
                    "candidates": [f"{self.nodes[s]['name']} ({file_base(self.nodes[s])}:{self.nodes[s]['start_line']})"
                                   for s in hits[:5]],
                })
                result["ok"] = False
                result["order_confirmed"] = False
                continue

            # 2) 顺序性：能否从 current 继续推下一步
            ordered = [sid for sid in in_pipe
                       if sid in self._downstream(current, max_depth=max_depth)]
            if ordered:
                best = ordered[0]
                b = self.nodes[best]
                result["steps"].append({
                    "keyword": step, "status": "ordered",
                    "reason": "该符号在入口调用链上，且能从已确证的上一步调用关系推出",
                    "node": f"{b['name']} ({file_base(b)}:{b['start_line']})",
                    "evidence": best,
                    "candidates": [f"{self.nodes[s]['name']} ({file_base(self.nodes[s])}:{self.nodes[s]['start_line']})"
                                   for s in ordered[:5]],
                })
                current = best
            else:
                best = in_pipe[0]
                b = self.nodes[best]
                result["steps"].append({
                    "keyword": step, "status": "in_pipeline",
                    "reason": "该符号确认在入口调用链上（存在性确证），"
                              "但从前一步调用关系推不出先后顺序——很可能是与上一步并行/并列的兄弟调用，"
                              "顺序需结合入口调度代码或运行时复核",
                    "node": f"{b['name']} ({file_base(b)}:{b['start_line']})",
                    "evidence": best,
                    "candidates": [f"{self.nodes[s]['name']} ({file_base(self.nodes[s])}:{self.nodes[s]['start_line']})"
                                   for s in in_pipe[:5]],
                })
                # 顺序未确证：不推进 current，避免错误地基于丢弃的兄弟节点做顺序断言。
                # ok 保持 True（存在性确证），但 order_confirmed=False，诚实标注顺序未确证。
                result["order_confirmed"] = False

        # 入口调用闭包内的参数契约断裂是硬错误：流程即使走通，参数名也对不上，
        # 运行时必然 TypeError。一旦存在，整体判定失败，避免假通过。
        if result["param_contract_in_entry"]:
            result["ok"] = False
            result["order_confirmed"] = False

        return result


# ═══════════════════════════════════════════════════════════════════
# 跨语言插件契约断链检测
# ═══════════════════════════════════════════════════════════════════

_SKIP_CONTRACT_DIRS = {".git", ".venv", "venv", "__pycache__", "node_modules",
                       "vendor", "dist", "build", "static", "assets"}


def cross_lang_contract_scan(project_path: str) -> List[dict]:
    """跨语言插件契约断链检测：前端/Go 引用的业务插件名 vs PHP 插件实现目录。

    背景：目标项目 worker.php 用 `setModule($plugin, "\\app\\plugins\\{$plugin}\\Module")`
    动态加载插件，插件名即 `php/plugins/<name>/` 目录名。前端 Vue/JS 的
    `pluginName = 'xxx'` 与 Go 的 `"action":"a/b"` / `Param("action","a/b")` 引用
    同名插件，而 PHP 端无对应目录即跨语言契约断裂——运行时加载失败/静默降级。

    只收集"业务组件"目录下的插件名引用，排除前端 UI 插件目录
    （`components/plugins/`）与构建产物（`static/`），避免把 canvasHistory/elk
    等纯前端 UI 插件误判为 PHP 插件断链。输出结构化信号（medium 提示性），
    不置流程失败。
    """
    if not project_path or not os.path.isdir(project_path):
        return []
    # 1) PHP 插件实现目录：仅限 PHP 插件约定根目录 php/plugins/<name>/
    #    （排除 components/vendor 等任意名为 plugins 的无关路径，避免误补缺失实现）
    php_plugins: Set[str] = set()
    php_plugins_root = os.path.join(project_path, "php", "plugins")
    has_php_plugin_root = os.path.isdir(php_plugins_root)
    try:
        with os.scandir(php_plugins_root) as it:
            for entry in it:
                if entry.is_dir():
                    php_plugins.add(entry.name.lower())
    except OSError:
        php_plugins = set()  # 路径不存在或不可访问 → 按无 PHP 插件处理
    if not has_php_plugin_root:
        # 项目根本没有 PHP 插件约定目录 → 不存在"跨语言插件契约"，不上报断链。
        # 否则纯前端/Go 项目里任何形似插件引用（pluginName/action）都会被误报为缺失。
        return []
    # 2) 收集前端/Go 引用插件名
    plug_re = re.compile(r'''pluginName\s*=\s*['"]([a-zA-Z][a-zA-Z0-9_\-]*)['"]''', re.I)
    action_re = re.compile(
        r'''(?:Param\(\s*['"]action['"]\s*,\s*|['"]action['"]\s*:\s*)['"]([a-zA-Z][a-zA-Z0-9_\-]*)/''',
        re.I)
    refs: Dict[str, List[tuple]] = {}
    out: List[dict] = []  # 信号输出（断链 + 动态类名注入面），在文件扫描循环开始前初始化
    for root, dirs, files in os.walk(project_path):
        dirs[:] = [d for d in dirs if d not in _SKIP_CONTRACT_DIRS]
        # 排除前端 UI 插件目录（components/plugins/）、PHP 插件实现目录本身：
        # 仅当路径以 php/plugins 或 components/plugins 结尾时才剪枝，其余恰好叫
        # plugins 的业务目录照常扫描，避免漏掉缺失实现
        rel = os.path.relpath(root, project_path).replace("\\", "/")
        if rel.endswith("php/plugins") or rel.endswith("components/plugins"):
            dirs[:] = []  # 剪枝该子树，避免深入扫描被排除插件目录内的文件产生假断链信号
            continue
        for fn in files:
            if not fn.endswith((".vue", ".js", ".ts", ".go", ".php")):
                continue
            path = os.path.join(root, fn)
            try:
                lines = open(path, encoding="utf-8", errors="ignore").read().splitlines()
            except Exception as e:
                # 文件不可读，跳过该文件
                _log(f"读取文件失败，跳过流程扫描 {path}: {e}")
                continue
            rel = os.path.relpath(path, project_path).replace("\\", "/")
            # Go 动态插件名/类名注入面（§二.2）：map[string]any{...} 含 plugin/class 键
            # 且值为运行时变量（非字符串字面量），经 json.Marshal 序列化转发跨语言执行面。
            # "有实现但类名由外部 payload 动态决定" —— 与前端硬编码引用(pluginName='x')
            # 不同，是动态插件名注入面，运行时可由外部请求决定加载哪个插件。
            # （目标项目 internal/app/plugin/php/multi_pool.go:117 盲区）
            if fn.endswith(".go"):
                content = "\n".join(lines)
                if (re.search(r'map\[string\](?:any|interface\{\})\s*\{', content, re.I)
                        and re.search(r'["\'](?:plugin|class)["\']\s*:\s*[a-zA-Z_][a-zA-Z0-9_]*',
                                      content, re.I)
                        and re.search(r'json\.Marshal\s*\(', content, re.I)):
                    dyn_line = next(
                        (i for i, ln in enumerate(lines, 1)
                         if re.search(r'["\'](?:plugin|class)["\']\s*:\s*[a-zA-Z_][a-zA-Z0-9_]*', ln)), 0)
                    out.append({
                        "signal": "cross_lang_dynamic_class_inject",
                        "severity": "medium",
                        "plugin": "动态(外部payload)",
                        "file": rel,
                        "line": dyn_line,
                        "implemented": sorted(php_plugins),
                        "detail": "Go 侧把运行时插件名/类名作为动态键经 json.Marshal 序列化"
                                  "转发跨语言执行面，类名由外部 payload 动态决定，"
                                  "存在跨语言动态插件名注入面",
                    })
            for i, line in enumerate(lines, 1):
                for m in plug_re.finditer(line):
                    refs.setdefault(m.group(1).lower(), []).append((rel, i))
                for m in action_re.finditer(line):
                    if m.group(1):
                        refs.setdefault(m.group(1).lower(), []).append((rel, i))
    # 3) 比对：引用但无 PHP 实现 → 断链
    for name, locs in sorted(refs.items()):
        if name in php_plugins:
            continue
        uniq: List[tuple] = []
        for loc in locs:
            if loc not in uniq:
                uniq.append(loc)
        for rel, line in uniq[:3]:
            out.append({
                "signal": "cross_lang_plugin_break",
                "severity": "medium",
                "plugin": name,
                "file": rel,
                "line": line,
                "implemented": sorted(php_plugins),
                "detail": f"前端/Go 引用插件「{name}」但 PHP plugins/ 目录无同名实现"
                          f"(现有: {', '.join(sorted(php_plugins))})，"
                          f"跨语言插件名契约断裂，运行时将加载失败或静默降级",
            })
    return out


# ═══════════════════════════════════════════════════════════════════
# 顶层接口（MCP handler 调用）
# ═══════════════════════════════════════════════════════════════════

def verify_flow(project_path: str, entry: str, steps: List[str],
                depth: Optional[int] = None, db_path: Optional[str] = None) -> dict:
    """验证入口管线是否覆盖期望步骤。

    Args:
        project_path: 目标项目路径（用于自动定位知识图谱）。
        entry: 入口符号，支持 模块.函数 消除歧义。
        steps: 期望步骤的符号关键词列表（中英文均可，由调用方预映射）。
        depth: 调用链搜索深度，默认 8。
        db_path: 显式指定知识图谱数据库（测试/旁路用）；缺省自动定位。

    Returns:
        结构化验证结果 dict（含 steps 状态与 evidence）。
    """
    if depth is None:
        depth = 8
    if db_path is None:
        db_path = _kg_db_path(project_path)
    # 图谱缺失时自动构建（coderef_memory_sync），避免 flow_verify 因图谱缺失整体短路。
    db_path = ensure_kg(project_path, db_path)
    if not os.path.exists(db_path):
        return {
            "ok": False,
            "entry": {"spec": entry, "found": False},
            "steps": [],
            "graph_stats": {"has_kg": False, "db": db_path},
            "summary": f"知识图谱不存在({db_path})，"
                       f"请先运行 coderef_audit 或 coderef_memory_sync 构建知识图谱",
        }
    _log(f"verify_flow entry={entry} steps={steps} db={db_path}")
    result = FlowVerifier(db_path).verify(entry, list(steps), max_depth=depth)
    # AST 静态信号扫描：补充图谱无法覆盖的流程/参数/错误处理缺陷信号
    #（静默异常吞掉 / 未使用辅助函数 / 参数透传缺失 / 目录契约断裂）。
    # 提示性信号，不置 ok=False，避免把"提示"误判为"流程失败"。
    try:
        from core.ast_signals import scan_project
        result["static_signals"] = scan_project(project_path)
    except Exception as e:  # pragma: no cover
        _log(f"static_signals 扫描异常: {type(e).__name__}: {e}")
        result["static_signals"] = []
    # 跨语言插件契约断链检测：前端/Go 引用插件名 vs PHP plugins/ 实现目录。
    # 静态可确证信号，提示性（medium），不置 ok=False，避免把"提示"误判为"流程失败"。
    try:
        result["cross_lang_contract"] = cross_lang_contract_scan(project_path)
    except Exception as e:  # pragma: no cover
        _log(f"cross_lang_contract_scan 异常: {type(e).__name__}: {e}")
        result["cross_lang_contract"] = []
    return result


# ═══════════════════════════════════════════════════════════════════
# 渲染
# ═══════════════════════════════════════════════════════════════════

def render_report(result: dict) -> str:
    """纯文本报告（终端/日志可读）。"""
    lines = []
    lines.append("流程合规验证报告" + "=" * 3)
    gs = result.get("graph_stats", {})
    # 图谱缺失（has_kg=False）优先于入口判断：入口 found=False 是"图谱不存在"的
    # 附带结果，不应误报为"入口未找到"。必须在 missing-entry 分支之前处理。
    if gs.get("has_kg") is False:
        lines.append(f"知识图谱不存在: {gs.get('db', '')}")
        lines.append("请先运行 coderef_audit 或 coderef_memory_sync 构建知识图谱，再执行流程验证。")
        return "\n".join(lines)

    en = result.get("entry", {})
    if not en.get("found"):
        lines.append(f"入口: {en.get('spec')} → 未找到（请确认函数名，或使用 模块.函数 消除歧义）")
        return "\n".join(lines)

    lines.append(f"入口: {en['spec']} → {en['node']}")
    lines.append(f"图谱: {gs.get('nodes', 0)} 个节点, {gs.get('calls_edges', 0)} 条调用边")
    lines.append(f"期望步骤数: {len(result['steps'])}")
    lines.append("")

    for i, s in enumerate(result["steps"], 1):
        status = {"ordered": "确证", "in_pipeline": "在管线",
                  "outside": "存疑", "missing": "缺失"}[s["status"]]
        mark = {"ordered": "[OK]", "in_pipeline": "[~]", "outside": "[?]",
                "missing": "[X]"}[s["status"]]
        lines.append(f"{mark} 步骤{i}「{s['keyword']}」  {status}")
        lines.append(f"    原因: {s['reason']}")
        if s["status"] in ("ordered", "in_pipeline"):
            lines.append(f"    证据: {s['node']}")
        elif s["status"] == "outside" and s.get("candidates"):
            lines.append(f"    候选中: {'; '.join(s['candidates'])}")
            lines.append("    指引: 请编程AI沿上述候选符号的调用点复核,"
                         "或在运行时打点确认是否真的被调用")

    lines.append("")
    # 参数契约检测：调用点显式关键字参数名 ≠ 被调函数签名形参名（静态确证，运行时必 TypeError）
    contracts = result.get("param_contract", [])
    if contracts:
        lines.append(f"参数契约断裂: {len(contracts)} 处")
        for c in contracts[:20]:
            loc = f"{c['callee_file']}:{c['callee_line']}"
            lines.append(f"  [契约] {c['caller']} → {c['callee']}({loc}) "
                         f"调用参数 {', '.join(c['mismatch'])} 不在签名 {', '.join(c.get('params', []))} 中")
    if result.get("ok"):
        verdict = ("全部步骤在入口管线中确证。" if result.get("order_confirmed")
                   else "步骤均确认在入口管线中，但存在顺序未确证（可能并行），"
                        "需结合入口调度代码或运行时复核顺序。")
    else:
        verdict = "存在缺失/存疑/契约断裂步骤，需结合动态运行复核，或确认期望流程是否与实现一致。"
    lines.append(f"结论: {verdict}")
    lines.append("")
    lines.append("图例: [OK]=入口调用链确证(含顺序); [~]=在入口管线但顺序未确证(可能并行);"
                 + " [?]=入口管线外/动态调用; [X]=项目内找不到对应符号")
    lines.append("注意: 静态图谱对动态注册/反射/运行时拼装的调用天然不完整，"
                 + "本报告未被确证的步骤不代表流程错误，只代表'需要进一步核验'。")
    return "\n".join(lines)


def render_html(result: dict) -> str:
    """渲染非编程人员可读的 HTML 报告（自包含单文件）。"""
    en = result.get("entry", {})
    gs = result.get("graph_stats", {})
    steps = result.get("steps", [])
    ok = result.get("ok", False)
    order_confirmed = result.get("order_confirmed", False)

    # 图谱缺失优先于入口判断，避免误报"入口未找到"
    if gs.get("has_kg") is False:
        return f"""<!DOCTYPE html><html lang="zh"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>流程合规验证报告</title></head>
<body style="margin:0;font-family:'Segoe UI','PingFang SC','Microsoft YaHei',sans-serif;background:#f6f7f9;color:#222;">
<div style="max-width:900px;margin:0 auto;padding:32px 20px;">
  <div style="background:#fff;border-radius:14px;padding:28px;box-shadow:0 1px 3px rgba(0,0,0,.06);">
    <h1 style="margin:0 0 4px;font-size:22px;">流程合规验证报告</h1>
    <div style="background:#EFAA17;border-left:4px solid #EFAA17;padding:12px 16px;border-radius:8px;margin-top:16px;">
      <strong>知识图谱不存在</strong>
      <div style="margin-top:6px;color:#555;font-size:13px;">{_esc(gs.get('db',''))}</div>
      <div style="margin-top:6px;color:#555;font-size:13px;">请先运行 coderef_audit 或 coderef_memory_sync 构建知识图谱，再执行流程验证。</div>
    </div>
  </div>
</div></body></html>"""

    if not en.get("found"):
        return f"""<!DOCTYPE html><html lang="zh"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>流程合规验证报告</title></head>
<body style="margin:0;font-family:'Segoe UI','PingFang SC','Microsoft YaHei',sans-serif;background:#f6f7f9;color:#222;">
<div style="max-width:900px;margin:0 auto;padding:32px 20px;">
  <div style="background:#fff;border-radius:14px;padding:28px;box-shadow:0 1px 3px rgba(0,0,0,.06);">
    <h1 style="margin:0 0 4px;font-size:22px;">流程合规验证报告</h1>
    <div style="background:#E8463A;border-left:4px solid #E8463A;padding:12px 16px;border-radius:8px;margin-top:16px;">
      <strong>入口未找到</strong>
      <div style="margin-top:6px;color:#555;font-size:13px;">入口 <code>{_esc(en.get('spec',''))}</code> 未找到，请确认函数名，或使用 模块.函数 消除歧义。</div>
    </div>
  </div>
</div></body></html>"""

    def badge(status):
        return {
            "ordered": ("#1DC981", "确证"),
            "in_pipeline": ("#2E86DE", "在管线"),
            "outside": ("#EFAA17", "存疑"),
            "missing": ("#E8463A", "缺失"),
        }[status]

    rows = []
    for i, s in enumerate(steps, 1):
        bg, label = badge(s["status"])
        cand = _esc("; ".join(s.get("candidates", [])[:3]) or "—")
        ev = _esc(s.get("node", ""))
        keyword = _esc(str(s.get("keyword", "")))
        rows.append(
            f"<tr>"
            f"<td style='padding:10px 12px;border-bottom:1px solid #eee;white-space:nowrap;color:#888;'>#{i}</td>"
            f"<td style='padding:10px 12px;border-bottom:1px solid #eee;font-weight:600;'>{keyword}</td>"
            f"<td style='padding:10px 12px;border-bottom:1px solid #eee;'><span style='background:{bg};color:#fff;border-radius:999px;padding:2px 10px;font-size:12px;'>{label}</span></td>"
            f"<td style='padding:10px 12px;border-bottom:1px solid #eee;color:#555;font-size:13px;'>{ev or cand}</td>"
            f"</tr>")

    contracts = result.get("param_contract", [])
    if ok and order_confirmed:
        status_banner = ("#1DC981", "全部步骤已在入口管线中确证")
    elif ok:
        status_banner = ("#2E86DE", "步骤均在入口管线中，但存在顺序未确证（可能并行），需复核顺序")
    elif result.get("param_contract_in_entry"):
        status_banner = ("#E8463A", "入口调用链存在参数契约断裂（调用参数名与签名不符），运行时必报错")
    else:
        status_banner = ("#EFAA17", "存在存疑/缺失步骤，需结合动态运行复核")

    # 参数契约断裂表（全图静态确证信号）
    params_html = ""
    if contracts:
        prows = []
        for c in contracts[:20]:
            prows.append(
                f"<tr>"
                f"<td style='padding:8px 12px;border-bottom:1px solid #eee;font-weight:600;'>{_esc(c['caller'])}</td>"
                f"<td style='padding:8px 12px;border-bottom:1px solid #eee;'>{_esc(c['callee'])}<br>"
                f"<span style='color:#aaa;font-size:12px;'>{_esc(c['callee_file'])}:{c['callee_line']}</span></td>"
                f"<td style='padding:8px 12px;border-bottom:1px solid #eee;color:#E8463A;font-weight:600;'>{_esc(', '.join(c['mismatch']))}</td>"
                f"<td style='padding:8px 12px;border-bottom:1px solid #eee;color:#555;font-size:13px;'>{_esc(', '.join(c.get('params', [])) or '—')}</td>"
                f"</tr>")
        params_html = f"""<div style="margin-top:24px;">
  <h3 style="margin:0 0 8px;font-size:15px;color:#E8463A;">参数契约断裂（{len(contracts)} 处）</h3>
  <table style="width:100%;border-collapse:collapse;font-size:14px;">
    <thead><tr style="text-align:left;color:#888;font-size:12px;border-bottom:2px solid #eee;">
      <th style="padding:8px 12px;">调用方</th><th style="padding:8px 12px;">被调函数</th>
      <th style="padding:8px 12px;">多余参数</th><th style="padding:8px 12px;">签名形参</th>
    </tr></thead>
    <tbody>{''.join(prows)}</tbody>
  </table>
  <div style="margin-top:6px;font-size:12px;color:#999;">调用方显式传入的关键字参数名不在被调函数签名中，运行时必然触发 TypeError。</div>
</div>"""

    return f"""<!DOCTYPE html><html lang="zh"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>流程合规验证报告</title></head>
<body style="margin:0;font-family:'Segoe UI','PingFang SC','Microsoft YaHei',sans-serif;background:#f6f7f9;color:#222;">
<div style="max-width:900px;margin:0 auto;padding:32px 20px;">
  <div style="background:#fff;border-radius:14px;padding:28px;box-shadow:0 1px 3px rgba(0,0,0,.06);">
    <h1 style="margin:0 0 4px;font-size:22px;">流程合规验证报告</h1>
    <div style="color:#888;font-size:13px;margin-bottom:20px;">
      入口节点：<code style="background:#f0f0f3;padding:2px 6px;border-radius:6px;">{_esc(en.get('spec',''))}</code>
      &nbsp;→&nbsp;{_esc(en.get('node',''))}
      &nbsp;·&nbsp;图谱 {gs.get('nodes',0)} 节点 / {gs.get('calls_edges',0)} 调用边
    </div>
    <div style="background:{status_banner[0]}14;border-left:4px solid {status_banner[0]};padding:12px 16px;border-radius:8px;margin-bottom:20px;">
      <strong style="color:{status_banner[0]};">{status_banner[1]}</strong>
    </div>
    <table style="width:100%;border-collapse:collapse;font-size:14px;">
      <thead><tr style="text-align:left;color:#888;font-size:12px;border-bottom:2px solid #eee;">
        <th style="padding:8px 12px;">步骤</th><th style="padding:8px 12px;">期望节点</th>
        <th style="padding:8px 12px;">状态</th><th style="padding:8px 12px;">证据 / 候选</th>
      </tr></thead>
      <tbody>{''.join(rows)}</tbody>
    </table>
    {params_html}
    <div style="margin-top:20px;font-size:12px;color:#999;line-height:1.8;">
      图例：<span style="color:#1DC981;">确证</span>=入口调用链确证(含顺序)；
      <span style="color:#2E86DE;">在管线</span>=确认在入口管线，但顺序未确证(可能并行)；
      <span style="color:#EFAA17;">存疑</span>=入口管线外或动态调用，需编程AI复核；
      <span style="color:#E8463A;">缺失</span>=项目内找不到对应符号。<br>
      注意：静态图谱对动态注册/反射/运行时拼装的调用天然不完整，未确证不代表流程错误，只代表需进一步核验。
    </div>
  </div>
</div></body></html>"""


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", required=True, help="项目路径（自动定位图谱）")
    ap.add_argument("--entry", required=True, help="入口，支持 模块.函数")
    ap.add_argument("--steps", required=True, help="期望步骤，逗号分隔")
    ap.add_argument("--depth", type=int, default=8)
    ap.add_argument("--html", help="可选：输出 HTML 报告路径")
    args = ap.parse_args()
    steps = [s.strip() for s in args.steps.split(",") if s.strip()]
    result = verify_flow(args.project, args.entry, steps, depth=args.depth)
    print(render_report(result))
    if args.html:
        with open(args.html, "w", encoding="utf-8") as f:
            f.write(render_html(result))
        _log(f"HTML 报告已写入: {args.html}")


if __name__ == "__main__":
    main()