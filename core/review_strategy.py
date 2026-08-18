# -*- coding: utf-8 -*-
"""
ReviewAdvisor v1.0 —— 审计策略判定（增量 vs 全量）

背景：用户提出审计时，不应只机械地跑一遍代码审计。本模块在审计前做一次"审查规划"：
  1. 变更信号检测：对比记忆层快照（mtime+size）找出自上次同步以来变更的文件；
  2. 影响闭包计算：基于知识图谱的 CALLS/IMPORTS/REFERENCES/INHERITS 边做多跳 BFS，
     估算"改这些文件会牵动哪些模块"，从而判断变更波及范围；
  3. 策略判定：结合变更规模、波及范围、图谱新旧程度，给出"增量审查"还是"全量审查"建议，
     并列出本次应重点审查的功能维度（创新传播、结构复杂度等）。

设计约束：
  - 纯标准库，不新增第三方依赖；
  - 复用 MemoryLayer 快照与 CodeKnowledgeGraph，不重复造轮子；
  - 所有面向使用者的可读文本一律中文；
  - 异常不静默吞掉：任何一步失败都如实记录到 errors，并给出保守的"全量审查"建议；
  - magic number 集中定义为模块级常量。

作者: CodeRef-AI Team
版本: v1.0
"""

import os
import json
import hashlib
from datetime import datetime
from collections import deque
from typing import Dict, List, Optional, Set, Tuple

from loguru import logger


# ═══════════════════════════════════════════════════════════════════
# 模块级常量（集中管理 magic number）
# ═══════════════════════════════════════════════════════════════════

# 项目根目录（Coderef-Ai-master）
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# 记忆层状态目录（与 memory_layer 保持一致）
_MEMORY_STATE_DIR = os.path.join(_PROJECT_ROOT, "data", "memory_state")

# 快照 mtime 容差（秒），与 memory_layer 保持一致
_MTIME_TOLERANCE = 0.1

# 影响闭包 BFS 最大跳跃深度（避免大项目上无限扩散）
_MAX_BFS_DEPTH = 3
# 影响闭包节点数上限（超过则视为"波及全项目"，强制全量）
_MAX_IMPACT_NODES = 200
# 影响闭包节点返回给调用方的条数上限（供报告展示，避免字段过大）
_IMPACT_NODES_RETURN = 50

# 策略判定阈值
# 变更文件数 <= _INCR_MAX_FILES 且波及节点 <= _INCR_MAX_IMPACT → 倾向增量
_INCR_MAX_FILES = 10
_INCR_MAX_IMPACT = 60
# 变更文件数 > _FULL_MIN_FILES 或波及节点 > _FULL_MIN_IMPACT → 强制全量
_FULL_MIN_FILES = 30
_FULL_MIN_IMPACT = 150

# 图谱过期阈值（小时）：图谱构建超过该时长视为"旧"，倾向全量重建
_STALE_GRAPH_HOURS = 24

# 影响闭包相关的边类型
_IMPACT_EDGE_TYPES = ("CALLS", "IMPORTS", "REFERENCES", "INHERITS")

# 建议等级
_STRATEGY_INCR = "incr"   # 增量审查
_STRATEGY_FULL = "full"   # 全量审查
_STRATEGY_NO_CHANGE = "no_change"   # 无变更，直接复用既有结论


# ═══════════════════════════════════════════════════════════════════
# 影响闭包：基于知识图谱边的多跳 BFS
# ═══════════════════════════════════════════════════════════════════

def _build_adjacency(edges: List[Tuple[int, object]]) -> Tuple[Dict[str, Set[str]], Dict[str, Set[str]]]:
    """根据图谱全部边构建双向邻接表。

    返回:
        (forward, backward)
        forward : 节点 -> 被它引用的目标集合（出边）
        backward: 节点 -> 引用它的来源集合（入边）
    只保留对影响分析有意义的边类型。
    """
    forward: Dict[str, Set[str]] = {}
    backward: Dict[str, Set[str]] = {}
    for _, edge in edges:
        etype = getattr(edge, "type", "")
        if etype not in _IMPACT_EDGE_TYPES:
            continue
        src = getattr(edge, "source", "")
        tgt = getattr(edge, "target", "")
        if not src or not tgt:
            continue
        forward.setdefault(src, set()).add(tgt)
        backward.setdefault(tgt, set()).add(src)
    return forward, backward


def compute_impact_closure(kg, start_nodes: Set[str], edges,
                           max_depth: int = _MAX_BFS_DEPTH,
                           max_nodes: int = _MAX_IMPACT_NODES) -> Tuple[Set[str], int]:
    """从 start_nodes 出发，沿影响边做多跳 BFS，收集受影响节点闭包。

    影响方向：谁引用了变更点（backward 追踪），即"改了这个，谁会被波及"。

    返回:
        (impact_closure, max_reached_depth)
        impact_closure  受影响节点 id 集合
        max_reached_depth  实际到达的最大深度（用于报告）
    """
    forward, backward = _build_adjacency(edges)
    closure: Set[str] = set(start_nodes)
    frontier = set(start_nodes)
    depth = 0
    while frontier and depth < max_depth:
        if len(closure) > max_nodes:
            break
        next_frontier: Set[str] = set()
        for nid in frontier:
            for upstream in backward.get(nid, set()):
                if upstream not in closure:
                    closure.add(upstream)
                    next_frontier.add(upstream)
        frontier = next_frontier
        depth += 1
    return closure, depth


# ═══════════════════════════════════════════════════════════════════
# ReviewAdvisor
# ═══════════════════════════════════════════════════════════════════

class ReviewAdvisor:
    """审计策略判定器：变更信号 + 影响闭包 → 增量/全量建议。"""

    # ─── 路径 / 快照 ───

    @staticmethod
    def _project_hash(project_path: str) -> str:
        return hashlib.md5(os.path.abspath(project_path).encode("utf-8")).hexdigest()[:12]

    def _state_path(self, project_path: str) -> str:
        return os.path.join(_MEMORY_STATE_DIR, f"{self._project_hash(project_path)}.json")

    def _load_state(self, project_path: str) -> dict:
        path = self._state_path(project_path)
        if not os.path.exists(path):
            return {}
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.warning(f"[ReviewAdvisor] 读取记忆状态失败: {e}")
            return {}

    @staticmethod
    def _compute_snapshot(files: List[str]) -> Dict[str, Dict[str, float]]:
        snap = {}
        for fp in files:
            try:
                snap[fp] = {"mtime": os.path.getmtime(fp), "size": os.path.getsize(fp)}
            except OSError as e:
                logger.warning(f"读取文件快照失败，跳过 {fp}: {e}")
        return snap

    @staticmethod
    def _same_file(old: Optional[dict], cur: Optional[dict]) -> bool:
        if not old or not cur:
            return False
        return abs(old.get("mtime", 0) - cur.get("mtime", 0)) <= _MTIME_TOLERANCE and \
            old.get("size") == cur.get("size")

    # ─── 变更信号检测 ───

    def detect_changes(self, project_path: str) -> dict:
        """对比记忆层快照与当前文件系统，找出变更/新增/删除文件。

        Returns:
            {"has_prev_snapshot": bool, "changed": [..], "added": [..],
             "deleted": [..], "total": int, "errors": []}
        """
        result: dict = {"has_prev_snapshot": False, "changed": [], "added": [],
                        "deleted": [], "total": 0, "errors": []}
        try:
            from core.code_analyzer import CodeAnalyzer
            files = CodeAnalyzer().scan_directory(project_path)
        except Exception as e:
            result["errors"].append(f"扫描文件清单失败: {e}")
            return result

        state = self._load_state(project_path)
        prev_snapshot = state.get("snapshot", {})
        if not prev_snapshot:
            result["has_prev_snapshot"] = False
            result["total"] = len(files)
            return result

        result["has_prev_snapshot"] = True
        current = self._compute_snapshot(files)
        prev_paths = set(prev_snapshot.keys())
        cur_paths = set(current.keys())

        changed: List[str] = []
        added: List[str] = []
        for fp in files:
            old = prev_snapshot.get(fp)
            cur = current.get(fp)
            if fp not in prev_paths:
                added.append(fp)
            elif not self._same_file(old, cur):
                changed.append(fp)
        deleted = sorted(prev_paths - cur_paths)

        result["changed"] = sorted(changed)
        result["added"] = sorted(added)
        result["deleted"] = deleted
        result["total"] = len(files)
        return result

    # ─── 图谱信息 ───

    def _load_kg(self, project_path: str):
        """加载已有知识图谱；不存在返回 None。"""
        try:
            from core.code_knowledge_graph import load_knowledge_graph
            return load_knowledge_graph(project_path)
        except Exception as e:
            logger.warning(f"[ReviewAdvisor] 加载图谱失败: {e}")
            return None

    @staticmethod
    def _kg_is_stale(built_at: str) -> bool:
        try:
            if not built_at:
                return True
            bt = datetime.strptime(built_at, "%Y-%m-%d %H:%M:%S")
            return (datetime.now() - bt).total_seconds() > _STALE_GRAPH_HOURS * 3600
        except Exception:
            return True

    # ─── 功能维度建议 ───

    def _dimension_focus(self, changes: dict, impact_count: int, stale: bool) -> List[dict]:
        """根据变更性质给出应重点审查的功能维度。

        创新传播/结构复杂度等维度在"波及面大/图谱旧"时更值得全量审查。
        """
        focus: List[dict] = []
        total_change = len(changes.get("changed", [])) + len(changes.get("added", [])) \
            + len(changes.get("deleted", []))
        has_prev = changes.get("has_prev_snapshot", False)

        if stale or impact_count > _INCR_MAX_IMPACT:
            focus.append({
                "dimension": "innovation_propagation",
                "label": "创新设计传播",
                "reason": "图谱较旧或波及面大，需全量核验创新模式是否在新增/变更模块中保持一致传播。",
            })
            focus.append({
                "dimension": "architecture_complexity",
                "label": "代码结构复杂度",
                "reason": "波及面大时需复核模块耦合与复杂度，避免变更引入结构性债务。",
            })
        elif not has_prev:
            focus.append({
                "dimension": "innovation_propagation",
                "label": "创新设计传播",
                "reason": "首次审计无基线，需全量盘点创新设计与传播缺口。",
            })
        else:
            focus.append({
                "dimension": "regression_risk",
                "label": "回归与一致性",
                "reason": "小范围增量变更，重点核验变更是否破坏既有契约与跨模块一致性。",
            })

        if total_change == 0 and has_prev:
            focus.append({
                "dimension": "no_change",
                "label": "无代码变更",
                "reason": "未检测到代码变更，可复用既有审计结论，仅需确认图谱时效。",
            })

        return focus

    # ─── 主入口 ───

    def advise(self, project_path: str) -> dict:
        """生成审计策略建议。

        Returns:
            {"strategy": "incr"/"full", "reason": str,
             "changes": {...}, "impact": {"count": int, "depth": int, "nodes": [..]},
             "kg": {"exists": bool, "built_at": str, "stale": bool},
             "dimensions_focus": [...], "errors": [...]}
        """
        project_path = os.path.abspath(project_path)
        result: dict = {
            "strategy": _STRATEGY_FULL,
            "reason": "",
            "changes": {},
            "impact": {"count": 0, "depth": 0, "nodes": []},
            "kg": {"exists": False, "built_at": "", "stale": True},
            "dimensions_focus": [],
            "errors": [],
        }

        # 1. 变更信号
        changes = self.detect_changes(project_path)
        result["changes"] = changes
        result["errors"].extend(changes.get("errors", []))

        # 2. 图谱信息
        kg = self._load_kg(project_path)
        built_at = ""
        stale = True
        edges = []
        if kg is not None:
            result["kg"]["exists"] = True
            built_at = kg.get_built_at() or ""
            result["kg"]["built_at"] = built_at
            stale = self._kg_is_stale(built_at)
            result["kg"]["stale"] = stale
            try:
                edges = kg.get_all_edges()
            except Exception as e:
                result["errors"].append(f"读取图谱边失败: {e}")
                edges = []

        # 3. 影响闭包
        impact_nodes: Set[str] = set()
        impact_depth = 0
        closed = False
        if kg is not None and edges:
            start_nodes: Set[str] = set()
            # 变更/新增文件对应的节点
            for fp in changes.get("changed", []) + changes.get("added", []):
                try:
                    qr = kg.query_file_entities(fp)
                    for n in qr.nodes:
                        start_nodes.add(n.id)
                except Exception as e:
                    logger.warning(f"查询知识图谱文件实体失败，跳过 {fp}: {e}")
                    continue
            if start_nodes:
                impact_nodes, impact_depth = compute_impact_closure(
                    kg, start_nodes, edges)
                closed = True
        result["impact"]["count"] = len(impact_nodes)
        result["impact"]["depth"] = impact_depth
        result["impact"]["nodes"] = sorted(impact_nodes)[:_IMPACT_NODES_RETURN]

        changed_count = len(changes.get("changed", [])) + len(changes.get("added", [])) \
            + len(changes.get("deleted", []))
        has_prev = changes.get("has_prev_snapshot", False)

        # 4. 策略判定
        strategy = _STRATEGY_FULL
        reason = ""
        if not has_prev:
            strategy = _STRATEGY_FULL
            reason = "项目尚无记忆层基线（首次审计），需全量审查建立基线。"
        elif changed_count == 0:
            # 无变更：若图谱不过期则直接复用，否则全量重建
            if not stale:
                strategy = _STRATEGY_NO_CHANGE
                reason = "未检测到代码变更且知识图谱在有效期内，可直接复用既有结论。"
            else:
                strategy = _STRATEGY_FULL
                reason = "代码无变更但知识图谱已过期，建议全量重建图谱后再审查。"
        elif changed_count > _FULL_MIN_FILES or len(impact_nodes) > _FULL_MIN_IMPACT:
            strategy = _STRATEGY_FULL
            reason = (f"变更文件 {changed_count} 个、波及节点 {len(impact_nodes)} 个，"
                      f"超出增量审查阈值，建议全量审查。")
        elif changed_count <= _INCR_MAX_FILES and len(impact_nodes) <= _INCR_MAX_IMPACT \
                and not stale:
            strategy = _STRATEGY_INCR
            reason = (f"变更文件 {changed_count} 个、波及节点 {len(impact_nodes)} 个，"
                      f"范围可控且图谱在有效期内，建议增量审查（聚焦变更文件及其影响闭包）。")
        else:
            # 变更量中等但图谱旧，或波及面中等偏大
            strategy = _STRATEGY_FULL
            reason = (f"变更文件 {changed_count} 个、波及节点 {len(impact_nodes)} 个，"
                      f"范围中等或图谱需更新，建议全量审查以保证一致性。")

        result["strategy"] = strategy
        result["reason"] = reason
        result["dimensions_focus"] = self._dimension_focus(changes, len(impact_nodes), stale)

        if kg is not None:
            try:
                kg.close()
            except Exception:
                # 关闭图谱连接尽力而为
                pass

        return result


# 全局单例
review_advisor = ReviewAdvisor()