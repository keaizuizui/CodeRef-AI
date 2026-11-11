# -*- coding: utf-8 -*-
"""梦系统的统一记忆入口：确定性环境索引 + 语义笔记。

分工（5.21.15 起）：
  - 语义记忆（决策 / 约定 / 坑 / 经验）→ dream notes（LLM，带 evidence/status）
  - 环境事实（工具位置 / 资源 / 文档 / 规程卡）→ memory_index.json
    （ResourceScanner 纯静态扫描，无 LLM、可离线）

`coderef_operation_memory` 保留工具名与 action 契约，内部全部走本模块；
`core.operation_memory` 只保留确定性扫描器与规程卡读取，不再承担语义记忆。
"""

import os
from typing import Dict, List, Optional

from core import dream


INDEX_FILE = "memory_index.json"
RESOURCE_TYPES = ("git", "dependency", "model", "api",
                  "tool", "env_tool", "doc", "report")


class DreamMemory:
    """梦记忆门面：环境索引（确定性）+ 语义笔记（梦）。"""

    # ─── 环境索引 ───

    @staticmethod
    def _pp(project_path: str) -> str:
        return os.path.abspath(project_path)

    def index_path(self, project_path: str) -> str:
        return dream._path(self._pp(project_path), INDEX_FILE)

    def load_index(self, project_path: str) -> Optional[dict]:
        # _read_json 按 default 的类型做校验：这里必须传 {} 才能接受 dict 产物
        data = dream._read_json(self.index_path(project_path), {})
        return data if isinstance(data, dict) and data else None

    def sync(self, project_path: str, mode: str = "full",
             with_llm: bool = True) -> dict:
        """扫描环境事实并落盘 memory_index.json（不调用 LLM）。

        with_llm 仅为兼容旧参数保留：语义记忆已归梦，sync 不再做 LLM 提炼；
        需要补梦请显式调用 coderef_dream(action=run)。
        """
        pp = self._pp(project_path)
        if mode not in ("full", "incr"):
            mode = "full"
        if not os.path.isdir(pp):
            return {"status": "error", "mode": mode,
                    "message": "项目路径不存在", "total_resources": 0}

        # 延迟 import：operation_memory 依赖本模块做兼容委托，模块级互引会成环
        from core.operation_memory import ResourceScanner, _probe_side_dirs
        try:
            resources = ResourceScanner(pp).scan()
            side_dirs = _probe_side_dirs(pp)
        except Exception as exc:  # noqa: BLE001 —— 扫描失败如实返回，不伪装空索引
            return {"status": "error", "mode": mode,
                    "message": f"环境扫描失败: {exc}", "total_resources": 0}

        index = {
            "version": 1,
            "built_at": dream._now(),
            "project_path": pp,
            "mode": mode,
            "resources": resources,
            "side_dirs": side_dirs,
        }
        try:
            with dream._file_lock(pp):
                dream._write_json(self.index_path(project_path), index)
        except Exception as exc:  # noqa: BLE001
            return {"status": "error", "mode": mode,
                    "message": f"环境索引写入失败: {exc}", "total_resources": 0}

        counts = self._resource_counts(resources)
        notes = dream._load_notes(pp)
        return {
            "status": "ok",
            "mode": mode,
            "changed": True,
            "source": "dream",
            "built_at": index["built_at"],
            "resources": counts,
            "total_resources": sum(counts.values()),
            "side_dirs": side_dirs,
            "index_path": self.index_path(project_path),
            "semantic_memory": {
                "owner": "dream",
                "notes": len(notes),
                "status": "deferred" if with_llm else "skipped",
                "hint": "语义记忆由 coderef_dream(action=run) 维护；sync 只扫环境事实",
            },
        }

    def _ensure_index(self, pp: str) -> dict:
        index = self.load_index(pp)
        if index is None:
            self.sync(pp)
            index = self.load_index(pp)
        return index or {}

    @staticmethod
    def _resource_counts(resources: dict) -> Dict[str, int]:
        return {k: len(resources.get(k) or []) for k in RESOURCE_TYPES}

    # ─── 笔记映射 ───

    @staticmethod
    def _note_category(note: dict) -> str:
        kind = str(note.get("kind") or "")
        if kind == "rule":
            return "decision"
        if kind in ("convention", "pitfall"):
            return kind
        return ""

    @staticmethod
    def _note_source(note: dict) -> str:
        ev = note.get("evidence") or []
        if isinstance(ev, list):
            return "、".join(str(x) for x in ev if str(x))
        return str(ev or "")

    def _note_item(self, note: dict) -> dict:
        return {
            "id": note.get("id", ""),
            "category": self._note_category(note),
            "kind": note.get("kind", ""),
            "job": note.get("job", ""),
            "claim_type": note.get("claim_type", ""),
            "status": note.get("status", ""),
            "summary": dream._clean_text(note.get("text"))[:300],
            "source": self._note_source(note),
        }

    def _notes(self, pp: str) -> List[dict]:
        return [n for n in dream._load_notes(pp)
                if str(n.get("status") or "") != "rejected"]

    # ─── 兼容 API ───

    def query(self, project_path: str, query_type: str = "all",
              keyword: str = "", limit: int = 10) -> dict:
        """按类别检索：资源走环境索引，决策/约定/坑走梦笔记。"""
        pp = self._pp(project_path)
        index = self._ensure_index(pp)
        notes = self._notes(pp)
        kw = (keyword or "").strip().lower()
        max_n = max(1, int(limit or 10))
        results: List[dict] = []

        def _hit(item: dict) -> bool:
            if not kw:
                return True
            hay = " ".join(str(item.get(k, "")) for k in
                           ("name", "summary", "path", "text", "source"))
            return kw in hay.lower()

        qt = (query_type or "all").strip().lower()
        if qt in ("all", "resource", "tool", "doc"):
            for rtype, items in (index.get("resources") or {}).items():
                if qt == "tool" and rtype not in ("tool", "env_tool"):
                    continue
                if qt == "doc" and rtype not in ("doc", "report"):
                    continue
                for it in items or []:
                    if _hit(it):
                        results.append({**it, "category": rtype, "source": "env"})

        if qt in ("all", "decision", "convention", "pitfall"):
            for n in notes:
                cat = self._note_category(n)
                if not cat or (qt != "all" and qt != cat):
                    continue
                item = self._note_item(n)
                if _hit(item):
                    results.append(item)

        if qt == "all":
            for it in index.get("side_dirs") or []:
                if _hit(it):
                    results.append({**it, "category": "side_dir", "source": "side"})

        return {
            "status": "ok",
            "source": "dream",
            "query_type": query_type,
            "keyword": keyword,
            "total": len(results),
            "results": results[:max_n],
        }

    def find(self, project_path: str, name: str, limit: int = 5) -> dict:
        """定位资源 / 笔记：按名称或路径片段搜索。"""
        pp = self._pp(project_path)
        kw = (name or "").strip().lower()
        if not kw:
            return {"status": "error", "message": "name 不能为空", "name": name}
        index = self._ensure_index(pp)
        hits: List[dict] = []
        for rtype, items in (index.get("resources") or {}).items():
            for it in items or []:
                if kw in str(it.get("name", "")).lower() or \
                        kw in str(it.get("path", "")).lower():
                    hits.append({**it, "category": rtype, "side": "project"})
        for it in index.get("side_dirs") or []:
            if kw in str(it.get("name", "")).lower() or \
                    kw in str(it.get("path", "")).lower():
                hits.append({**it, "category": "side_dir", "side": "side"})
        for n in self._notes(pp):
            if kw in str(n.get("text", "")).lower():
                hits.append({**self._note_item(n), "side": "knowledge"})
        rank = {"project": 0, "side": 1, "knowledge": 2}
        hits.sort(key=lambda h: rank.get(h.get("side"), 3))
        return {"status": "ok", "name": name, "total": len(hits),
                "results": hits[:max(1, int(limit or 5))]}

    def status(self, project_path: str) -> dict:
        """记忆健康状态：环境索引 + 梦笔记。"""
        pp = self._pp(project_path)
        index = self.load_index(pp)
        notes = dream._load_notes(pp)
        active = [n for n in notes if str(n.get("status") or "") != "rejected"]
        kn: Dict[str, int] = {"decision": 0, "convention": 0, "pitfall": 0}
        pending = 0
        for n in active:
            cat = self._note_category(n)
            if not cat:
                continue
            kn[cat] += 1
            if str(n.get("status")) == "hypothesis":
                pending += 1
        if index is None and not notes:
            return {"status": "error",
                    "message": "梦记忆尚未建立，请先调用 coderef_operation_memory(action=sync)"}
        resources = index.get("resources") if index else {}
        counts = self._resource_counts(resources or {})
        return {
            "status": "ok",
            "source": "dream",
            "project_path": pp,
            "last_sync": (index or {}).get("built_at", "-"),
            "index_missing": index is None,
            "index_path": self.index_path(project_path),
            "resource_counts": counts,
            "covered_categories": [k for k, v in counts.items() if v],
            "knowledge_counts": kn,
            "side_dirs": len((index or {}).get("side_dirs") or []),
            "pending_human": pending,
            "dream_notes": {
                "total": len(notes),
                "active": len(active),
                "by_status": {s: sum(1 for n in notes if n.get("status") == s)
                              for s in ("verified", "hypothesis", "rejected")},
            },
            "brain_page": dream._path(pp, dream.DREAM_MD),
        }

    def recover(self, project_path: str, limit: int = 8) -> dict:
        """一次调用恢复：环境事实 + 梦笔记（已确认优先，假设单列）。"""
        pp = self._pp(project_path)
        if int(limit or 0) < 1:
            return {"status": "error", "message": "limit 必须大于 0", "tool": "recover"}
        index = self._ensure_index(pp)
        if not index:
            return {"status": "error", "tool": "recover",
                    "message": "梦记忆索引尚未建立，请检查项目路径或调用 action=sync"}
        max_n = int(limit)
        notes = self._notes(pp)
        brief: Dict[str, List[dict]] = {"decision": [], "convention": [], "pitfall": []}
        pending_items: List[dict] = []
        pending_counts = {"decision": 0, "convention": 0, "pitfall": 0}
        for n in notes:
            cat = self._note_category(n)
            if not cat:
                continue
            item = self._note_item(n)
            if str(n.get("status")) == "verified":
                if len(brief[cat]) < max_n:
                    brief[cat].append({"summary": item["summary"],
                                       "source": item["source"],
                                       "id": item["id"]})
            elif str(n.get("status")) == "hypothesis":
                pending_counts[cat] += 1
                if pending_counts[cat] <= max_n:
                    pending_items.append({"category": cat,
                                          "summary": item["summary"],
                                          "id": item["id"]})

        from core.operation_memory import _read_procedure_card
        try:
            llm_available = dream._llm_available(dream._build_llm())
        except Exception:  # noqa: BLE001 —— 状态探测失败不阻断恢复
            llm_available = False
        resources = index.get("resources") or {}
        env_tools = [{k: it.get(k) for k in ("name", "path", "location", "note")}
                     for it in (resources.get("env_tool") or [])]
        return {
            "status": "ok",
            "tool": "recover",
            "source": "dream",
            "project_path": pp,
            "last_sync": index.get("built_at", "-"),
            "llm_available": llm_available,
            "env_tools": env_tools,
            "resources": self._resource_counts(resources),
            "side_dirs": len(index.get("side_dirs") or []),
            "notes": [self._note_item(n) for n in notes[:max_n]],
            "decisions": brief["decision"],
            "conventions": brief["convention"],
            "pitfalls": brief["pitfall"],
            "pending_items": pending_items,
            "pending_counts": pending_counts,
            "procedure_card": _read_procedure_card(project_path),
            "procedure_hint": ("procedure_card 是自动注入的入口卡；开发侧完整规程见项目根 "
                               "AGENTS.md（外部项目见 docs/procedure-card.md）。"
                               "不得只凭卡片执行，也不得凭上下文记忆执行。"),
            "hint": ("梦笔记是假设，引用前先核验 evidence；工具位置以 env_tools 为准，"
                     "勿满 PATH 找工具或凭记忆猜。"),
        }

    def export_markdown(self, project_path: str,
                        output_path: str = "") -> dict:
        """导出 Markdown：环境索引 + 梦笔记（供不支持 MCP 的界面 attach）。"""
        pp = self._pp(project_path)
        index = self._ensure_index(pp)
        notes = self._notes(pp)
        lines = ["# 项目记忆（Dream）", "",
                 f"> 生成于 {dream._now()}；环境事实来自 `{INDEX_FILE}`，"
                 "语义笔记来自梦（假设，引用前核验 evidence）。", ""]
        lines += ["## 工具位置", ""]
        tools = (index.get("resources") or {}).get("env_tool") or []
        if tools:
            for it in tools:
                lines.append(f"- **{it.get('name', '')}**：`{it.get('path', '')}`"
                             f"（{it.get('location', '')}）{it.get('note', '')}")
        else:
            lines.append("- （未扫描到）")
        lines += ["", "## 语义笔记", ""]
        if notes:
            for n in notes:
                lines.append(f"- [{n.get('status', '')}] {dream._clean_text(n.get('text'))[:300]}"
                             f"（证据：{self._note_source(n) or '无'}）")
        else:
            lines.append("- （暂无，可用 coderef_dream(action=run) 补梦）")
        lines += ["", "## 资源清单", ""]
        for rtype in RESOURCE_TYPES:
            items = (index.get("resources") or {}).get(rtype) or []
            if not items:
                continue
            lines.append(f"### {rtype}（{len(items)}）")
            for it in items:
                lines.append(f"- {it.get('name', '')}：`{it.get('path', '')}`")
            lines.append("")
        body = "\n".join(lines) + "\n"
        path = output_path or dream._path(pp, "MEMORY.md")
        try:
            os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
            with dream._file_lock(pp):
                dream._write_text(path, body)
        except Exception as exc:  # noqa: BLE001
            return {"status": "error", "message": f"导出失败: {exc}"}
        return {"status": "ok", "output_path": path,
                "chars": len(body), "notes": len(notes)}


dream_memory = DreamMemory()
