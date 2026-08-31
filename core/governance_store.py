# -*- coding: utf-8 -*-
"""
GovernanceStore v1.0 —— CodeRef 5.1 架构治理运营库

把"定期体检"从单次扫描升级为可追踪、可回顾、可自动化的治理循环。
本模块承载治理运营状态的持久层（SQLite），借鉴 plane 项目管理模型：

  HealthCycle（体检周期）  → plane 的 Cycle
  GovernanceIssue（治理工作项）→ plane 的 Work Item / Issue
  状态机 Detected→Confirmed→Fixing→Verified→Archived/Rejected → plane 的 Workflow State
  IssueEvent（活动日志）  → plane 的 Activity Log

存储位置：<project>/.coderef/governance.db（随项目进 git，作为决策资产）。
与知识图谱（cache/kg/，扫描产物）职责分离，治理库持续累积、图谱每次重建。
"""

import os, json, time, uuid, sqlite3
from datetime import datetime
from typing import Dict, List, Optional, Any, Tuple
from contextlib import contextmanager

from loguru import logger


# ═══════════════════════════════════════════════════════════════════
# 状态机定义
# ═══════════════════════════════════════════════════════════════════

STATUS_DETECTED = "Detected"      # 扫描发现
STATUS_CONFIRMED = "Confirmed"    # 人工确认要治理
STATUS_FIXING = "Fixing"          # 修复中
STATUS_VERIFIED = "Verified"      # 复验达标
STATUS_ARCHIVED = "Archived"      # 归档（终态）
STATUS_REJECTED = "Rejected"      # 人工豁免/确认无需改

ALL_STATUSES = (STATUS_DETECTED, STATUS_CONFIRMED, STATUS_FIXING,
                STATUS_VERIFIED, STATUS_ARCHIVED, STATUS_REJECTED)

# 合法流转表；Archived 前必须经过 Verified（由 transition 强制校验）
_ALLOWED_TRANSITIONS: Dict[str, set] = {
    STATUS_DETECTED: {STATUS_CONFIRMED, STATUS_REJECTED},
    STATUS_CONFIRMED: {STATUS_FIXING, STATUS_REJECTED, STATUS_DETECTED},
    STATUS_FIXING: {STATUS_VERIFIED, STATUS_REJECTED, STATUS_CONFIRMED},
    STATUS_VERIFIED: {STATUS_ARCHIVED, STATUS_FIXING, STATUS_REJECTED},
    STATUS_ARCHIVED: set(),   # 终态；复发走 import 重新激活为 Detected
    STATUS_REJECTED: {STATUS_DETECTED, STATUS_CONFIRMED},
}


# ═══════════════════════════════════════════════════════════════════
# 缺口定位键（去重 / 复发 / 豁免的关键）
# ═══════════════════════════════════════════════════════════════════

def _norm(s: Any) -> str:
    return str(s or "").strip().replace("\\", "/")


def gap_key(g: Dict[str, Any]) -> str:
    """从一条差距（arch_gap_analyzer 输出）生成稳定定位键。

    键必须对"同一处问题"稳定、对"不同位置"区分。基于各类 gap 的可定位字段：
      - cycle                 → 用成员模块（排序后取最小）做锚，避免依赖成员顺序
      - business_gap          → flow_id + step_id
      - dependency_violation  → from_module -> to_module
      - 其余（missing/unassigned/god_module/large_module）→ module（或 role_id 兜底）
    """
    t = _norm(g.get("type"))
    if t == "cycle":
        members = sorted(_norm(x) for x in (g.get("modules") or []))
        return f"cycle:{len(members)}:{members[0] if members else '?'}"
    if t == "business_gap":
        return f"business_gap:{_norm(g.get('flow_id'))}:{_norm(g.get('step_id'))}"
    if t == "dependency_violation":
        return (f"dependency_violation:{_norm(g.get('from_module'))}"
                f"->{_norm(g.get('to_module'))}")
    mod = _norm(g.get("module")) or _norm(g.get("role_id")) or "?"
    return f"{t}:{mod}"


def gap_snapshot(g: Dict[str, Any]) -> str:
    """把一条差距序列化为留痕快照（避免治理记录随图谱重建而漂移）。"""
    return json.dumps(g, ensure_ascii=False, sort_keys=True)


# ═══════════════════════════════════════════════════════════════════
# 存储层
# ═══════════════════════════════════════════════════════════════════

def gov_db_path(project_path: str) -> str:
    pp = os.path.abspath(project_path)
    return os.path.join(pp, ".coderef", "governance.db")


class GovernanceStore:
    """架构治理运营库（SQLite）。"""

    SCHEMA_VERSION = 1

    def __init__(self, project_path: str, db_path: Optional[str] = None):
        self.project_path = os.path.abspath(project_path)
        self.db_path = os.path.abspath(db_path) if db_path else gov_db_path(
            self.project_path)
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        self._conn = None

    # ---------- 连接与建表 ----------

    def _ensure(self):
        if self._conn is None:
            self._conn = self._connect()
            self._init_schema()
        return self._conn

    def _connect(self):
        import sqlite3
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def _init_schema(self):
        c = self._conn
        c.executescript("""
        CREATE TABLE IF NOT EXISTS meta (
          key TEXT PRIMARY KEY, value TEXT
        );
        CREATE TABLE IF NOT EXISTS health_cycle (
          id TEXT PRIMARY KEY,
          name TEXT NOT NULL,
          description TEXT DEFAULT '',
          start_date TEXT NOT NULL,
          end_date TEXT DEFAULT '',
          status TEXT NOT NULL DEFAULT 'open',
          note TEXT DEFAULT '',
          created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS gov_issue (
          id TEXT PRIMARY KEY,
          cycle_id TEXT DEFAULT '',
          gap_key TEXT NOT NULL,
          gap_type TEXT NOT NULL,
          severity TEXT DEFAULT 'low',
          title TEXT DEFAULT '',
          module TEXT DEFAULT '',
          role_id TEXT DEFAULT '',
          flow_id TEXT DEFAULT '',
          status TEXT NOT NULL,
          priority TEXT DEFAULT 'medium',
          assignee TEXT DEFAULT '',
          due_date TEXT DEFAULT '',
          note TEXT DEFAULT '',
          snapshot TEXT DEFAULT '',
          recurred INTEGER DEFAULT 0,
          first_detected TEXT NOT NULL,
          last_seen TEXT NOT NULL,
          UNIQUE(gap_key)
        );
        CREATE TABLE IF NOT EXISTS issue_event (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          issue_id TEXT NOT NULL,
          at TEXT NOT NULL,
          action TEXT NOT NULL,
          actor TEXT DEFAULT '',
          detail TEXT DEFAULT ''
        );
        CREATE INDEX IF NOT EXISTS idx_issue_status ON gov_issue(status);
        CREATE INDEX IF NOT EXISTS idx_issue_cycle ON gov_issue(cycle_id);
        CREATE INDEX IF NOT EXISTS idx_issue_key ON gov_issue(gap_key);
        CREATE INDEX IF NOT EXISTS idx_event_issue ON issue_event(issue_id);
        """)
        cur = c.execute("SELECT value FROM meta WHERE key='schema_version'")
        if cur.fetchone() is None:
            c.execute("INSERT INTO meta(key,value) VALUES(?,?)",
                      ("schema_version", str(self.SCHEMA_VERSION)))
        self._conn.commit()

    def close(self):
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    # ---------- 事务原子性（外部 C：多 Agent 协作保险，写操作要么全成要么全回滚） ----------

    @contextmanager
    def _tx(self):
        """显式事务：写操作包进 BEGIN/COMMIT，异常时 ROLLBACK 不留半截状态。"""
        c = self._ensure()
        try:
            c.execute("BEGIN")
            yield c
            c.execute("COMMIT")
        except Exception:
            try:
                c.execute("ROLLBACK")
            except Exception:  # noqa: BLE001
                pass
            raise

    # ---------- HealthCycle ----------

    def create_cycle(self, name: str, description: str = "",
                     start_date: str = "", end_date: str = "") -> Dict[str, Any]:
        cid = "cyc_" + uuid.uuid4().hex[:12]
        today = datetime.now().strftime("%Y-%m-%d")
        row = {
            "id": cid, "name": name, "description": description,
            "start_date": start_date or today, "end_date": end_date,
            "status": "open", "note": "", "created_at": datetime.now().isoformat(
                timespec="seconds"),
        }
        c = self._ensure()
        with self._tx() as c:
            c.execute(
                "INSERT INTO health_cycle(id,name,description,start_date,end_date,"
                "status,note,created_at) VALUES(:id,:name,:description,"
                ":start_date,:end_date,:status,:note,:created_at)", row)
        logger.info(f"体检周期建档 {cid} {name}")
        return self.get_cycle(cid)

    def get_cycle(self, cid: str) -> Optional[Dict[str, Any]]:
        c = self._ensure()
        row = c.execute("SELECT * FROM health_cycle WHERE id=?",
                        (cid,)).fetchone()
        return dict(row) if row else None

    def open_cycle(self) -> Optional[Dict[str, Any]]:
        c = self._ensure()
        row = c.execute("SELECT * FROM health_cycle WHERE status='open' "
                        "ORDER BY created_at DESC LIMIT 1").fetchone()
        return dict(row) if row else None

    def close_cycle(self, cid: str, note: str = "") -> Optional[Dict[str, Any]]:
        c = self._ensure()
        if not self.get_cycle(cid):
            return None
        today = datetime.now().strftime("%Y-%m-%d")
        with self._tx() as c:
            c.execute("UPDATE health_cycle SET status='closed', end_date=?, note=? "
                      "WHERE id=? AND status='open'",
                      (today, note, cid))
        return self.get_cycle(cid)

    def list_cycles(self) -> List[Dict[str, Any]]:
        c = self._ensure()
        rows = c.execute("SELECT * FROM health_cycle ORDER BY created_at").fetchall()
        return [dict(r) for r in rows]

    # ---------- GovernanceIssue ----------

    def upsert_issue(self, gap: Dict[str, Any], cycle_id: str = "") -> Tuple[str, str]:
        """按 gap_key 去重导入一条差距为治理工作项。

        Returns: (issue_id, action) action ∈ {created, kept, reactivate, recurred, skipped}
          - created  : 新发现，状态 Detected
          - kept     : 已存在且仍 open（仅刷新 last_seen，不改变人工状态）
          - recurred : 曾 Archived 又出现 → 重开为 Detected 并标记复发
          - reactivate: 曾 Rejected 且证据变化 → 重新激活为 Detected（提示复核）
          - skipped  : 曾 Rejected 且证据未变 → 豁免仍生效，跳过
        """
        key = gap_key(gap)
        now = datetime.now().strftime("%Y-%m-%d %H:%M")
        snap = gap_snapshot(gap)
        gtype = _norm(gap.get("type")) or "unknown"
        sev = _norm(gap.get("severity")) or "low"
        title = _norm(gap.get("detail")) or _norm(gap.get("module")) or gtype
        mod = _norm(gap.get("module"))
        rid = _norm(gap.get("role_id")) or _norm(gap.get("from_role"))
        fid = _norm(gap.get("flow_id")) or _norm(gap.get("step_id"))
        c = self._ensure()

        row = c.execute("SELECT * FROM gov_issue WHERE gap_key=?",
                        (key,)).fetchone()
        if row is None:
            iid = "iss_" + uuid.uuid4().hex[:12]
            try:
                with self._tx() as c:
                    c.execute(
                        "INSERT INTO gov_issue(id,cycle_id,gap_key,gap_type,severity,"
                        "title,module,role_id,flow_id,status,priority,assignee,due_date,"
                        "note,snapshot,recurred,first_detected,last_seen) "
                        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (iid, cycle_id, key, gtype, sev, title[:500], mod, rid, fid,
                         STATUS_DETECTED, "medium", "", "", "", snap, 0, now, now))
                    c.execute("INSERT INTO issue_event(issue_id,at,action,actor,detail) "
                              "VALUES(?,?,?,?,?)",
                              (iid, now, "created", "system",
                               f"体检导入: {gtype} @ {key}"))
            except sqlite3.IntegrityError:
                # 并发下同 gap_key 已被另一 agent 插入：重读走 kept，不抛裸 UNIQUE 错误
                row2 = c.execute("SELECT * FROM gov_issue WHERE gap_key=?",
                                 (key,)).fetchone()
                if row2 is not None:
                    d2 = dict(row2)
                    with self._tx() as c:
                        c.execute("UPDATE gov_issue SET last_seen=?, snapshot=? "
                                  "WHERE gap_key=?",
                                  (now, snap, key))
                        c.execute("INSERT INTO issue_event(issue_id,at,action,actor,detail) "
                                  "VALUES(?,?,?,?,?)",
                                  (d2["id"], now, "seen", "system",
                                   "并发导入同 key 冲突，重读为 kept"))
                    return d2["id"], "kept"
                return iid, "created"
            return iid, "created"

        d = dict(row)
        if d["status"] == STATUS_ARCHIVED:
            # 复发：归档项再次出现 → 重开并打复发标记（条件更新防并发覆盖新状态）
            with self._tx() as c:
                n = c.execute("UPDATE gov_issue SET status=?, cycle_id=?, recurred=1, "
                              "snapshot=?, last_seen=? WHERE gap_key=? AND status=?",
                              (STATUS_DETECTED, cycle_id, snap, now, key,
                               STATUS_ARCHIVED)).rowcount
                if n != 1:
                    return d["id"], "skipped"
                c.execute("INSERT INTO issue_event(issue_id,at,action,actor,detail) "
                          "VALUES(?,?,?,?,?)",
                          (d["id"], now, "recurred", "system",
                           "已归档差距再次出现，复发重开为 Detected"))
            return d["id"], "recurred"
        if d["status"] == STATUS_REJECTED:
            # 豁免项：证据变化则重开提请复核，未变化则跳过（条件更新防并发覆盖）
            if snap != (d.get("snapshot") or ""):
                with self._tx() as c:
                    n = c.execute("UPDATE gov_issue SET status=?, cycle_id=?, snapshot=?, "
                                  "last_seen=? WHERE gap_key=? AND status=?",
                                  (STATUS_DETECTED, cycle_id, snap, now, key,
                                   STATUS_REJECTED)).rowcount
                    if n != 1:
                        return d["id"], "skipped"
                    c.execute("INSERT INTO issue_event(issue_id,at,action,actor,detail) "
                              "VALUES(?,?,?,?,?)",
                              (d["id"], now, "reactivate", "system",
                               "豁免差距证据变化，重新激活提请复核"))
                return d["id"], "reactivate"
            return d["id"], "skipped"
        # 其余 open 状态：刷新 last_seen，保持人工状态/优先级/负责人
        with self._tx() as c:
            c.execute("UPDATE gov_issue SET last_seen=?, snapshot=? WHERE gap_key=?",
                      (now, snap, key))
            c.execute("INSERT INTO issue_event(issue_id,at,action,actor,detail) "
                      "VALUES(?,?,?,?,?)", (d["id"], now, "seen", "system",
                                            "本轮体检仍存在"))
        return d["id"], "kept"

    def get_issue(self, issue_id: str) -> Optional[Dict[str, Any]]:
        c = self._ensure()
        row = c.execute("SELECT * FROM gov_issue WHERE id=?",
                        (issue_id,)).fetchone()
        return dict(row) if row else None

    def transition(self, issue_id: str, to_state: str, actor: str = "",
                   detail: str = "") -> Tuple[bool, str]:
        """工作项状态流转，返回 (ok, message)。"""
        if to_state not in ALL_STATUSES:
            return False, f"未知状态: {to_state}"
        iss = self.get_issue(issue_id)
        if iss is None:
            return False, "工作项不存在"
        cur = iss["status"]
        if to_state == cur:
            return False, f"已是 {cur}，无需流转"
        allowed = _ALLOWED_TRANSITIONS.get(cur, set())
        if to_state not in allowed:
            return False, (f"非法流转: {cur} → {to_state}（允许: "
                           f"{sorted(allowed) or '终态'}）")
        if to_state == STATUS_ARCHIVED and cur != STATUS_VERIFIED:
            return False, "仅可在 Verified 后归档（Archived 前须复验达标）"
        now = datetime.now().strftime("%Y-%m-%d %H:%M")
        with self._tx() as c:
            # 条件更新（WHERE status=cur）：并发下状态被另一 agent 改动则 rowcount=0，
            # 放弃本次流转并如实返回，事件只记录真实发生的流转（CodeRabbit 评审）
            n = c.execute("UPDATE gov_issue SET status=? WHERE id=? AND status=?",
                          (to_state, issue_id, cur)).rowcount
            if n != 1:
                return False, f"状态已变更（当前非 {cur}），请刷新后重试"
            c.execute("INSERT INTO issue_event(issue_id,at,action,actor,detail) "
                      "VALUES(?,?,?,?,?)",
                      (issue_id, now, f"{cur}->{to_state}", actor, detail))
        logger.info(f"工作项 {issue_id} 状态 {cur} → {to_state}")
        return True, f"{cur} → {to_state}"

    def reject(self, issue_id: str, reason: str = "", actor: str = "",
               detail: str = "") -> Tuple[bool, str]:
        """豁免：置为 Rejected 并记录 reason（必留）。"""
        iss = self.get_issue(issue_id)
        if iss is None:
            return False, "工作项不存在"
        if iss["status"] == STATUS_REJECTED:
            return False, "已是豁免状态"
        now = datetime.now().strftime("%Y-%m-%d %H:%M")
        cur = iss["status"]
        with self._tx() as c:
            # 条件更新（WHERE status=cur）：并发下状态被另一 agent 改动则 rowcount=0，
            # 放弃本次豁免并如实返回，避免覆盖新状态 / 重复 reject 事件（CodeRabbit 评审）
            n = c.execute("UPDATE gov_issue SET status=?, note=? WHERE id=? AND status=?",
                          (STATUS_REJECTED, reason, issue_id, cur)).rowcount
            if n != 1:
                return False, f"状态已变更（当前非 {cur}），请刷新后重试"
            c.execute("INSERT INTO issue_event(issue_id,at,action,actor,detail) "
                      "VALUES(?,?,?,?,?)",
                      (issue_id, now, "reject", actor,
                       f"豁免: {reason or '(超时)'} {detail}".strip()))
        return True, "已豁免 (Rejected)"

    def set_issue_meta(self, issue_id: str, priority: str = None,
                       assignee: str = None, due_date: str = None,
                       note: str = None, actor: str = "") -> Tuple[bool, str]:
        """更新工作项的可选治理元字段（优先级/负责人/截止/备注）。"""
        iss = self.get_issue(issue_id)
        if iss is None:
            return False, "工作项不存在"
        ups: Dict[str, Any] = {}
        for k, v in (("priority", priority), ("assignee", assignee),
                     ("due_date", due_date), ("note", note)):
            if v is not None:
                ups[k] = v
        if not ups:
            return True, "无字段更新"
        cols = ", ".join(f"{k}=?" for k in ups)
        vals = list(ups.values()) + [issue_id]
        with self._tx() as c:
            c.execute(f"UPDATE gov_issue SET {cols} WHERE id=?", vals)
            now = datetime.now().strftime("%Y-%m-%d %H:%M")
            c.execute("INSERT INTO issue_event(issue_id,at,action,actor,detail) "
                      "VALUES(?,?,?,?,?)",
                      (issue_id, now, "meta", actor, json.dumps(ups, ensure_ascii=False)))
        return True, "已更新: " + ", ".join(f"{k}={v}" for k, v in ups.items())

    # ---------- 查询 ----------

    def list_issues(self, cycle_id: str = "", status: str = "",
                    view: str = "", assignee: str = "",
                    limit: int = 500) -> List[Dict[str, Any]]:
        """按预置 view 或显式过滤查询治理工作项。"""
        c = self._ensure()
        sql = "SELECT * FROM gov_issue WHERE 1=1"
        args: list = []
        if cycle_id:
            sql += " AND cycle_id=?"
            args.append(cycle_id)
        if status:
            sql += " AND status=?"
            args.append(status)
        elif view in ("open", "all"):
            pass
        elif view == "rejected":
            sql += " AND status=?"
            args.append(STATUS_REJECTED)
        elif view == "archived":
            sql += " AND status=?"
            args.append(STATUS_ARCHIVED)
        elif view == "recurred":
            sql += " AND recurred=1"
        elif view == "overdue":
            sql += " AND due_date!='' AND due_date < date('now') AND status NOT IN (?,?)"
            args += [STATUS_ARCHIVED, STATUS_REJECTED]
        elif view == "assigned":
            sql += " AND assignee!=''"
        elif view == "high":
            # 高优先级队列：仅真实 high 严重度、排除终态（CodeRabbit 评审补分支）
            sql += " AND severity='high' AND status NOT IN (?,?)"
            args += [STATUS_ARCHIVED, STATUS_REJECTED]
        elif view == "recent":
            pass
        else:
            # 默认 open：所有非终结态
            sql += " AND status NOT IN (?,?)"
            args += [STATUS_ARCHIVED, STATUS_REJECTED]
        if assignee:
            sql += " AND assignee=?"
            args.append(assignee)
        if view == "recent":
            sql += " ORDER BY last_seen DESC"
        else:
            # 视图排序增强：high/open/all 默认视图按真实 severity 排序、unassigned 置底。
            # 避免封面被游离/噪声刷屏、治理重点（god/cycle/duplicate）被埋没。
            # severity 序 high>medium>low；gap_type=unassigned 一律置底；其余按 last_seen 稳定。
            sql += (
                " ORDER BY "
                "  CASE gap_type WHEN 'unassigned' THEN 3 ELSE 0 END,"
                "  CASE severity WHEN 'high' THEN 0 WHEN 'medium' THEN 1 ELSE 2 END,"
                "  last_seen DESC")
        sql += " LIMIT ?"
        args.append(min(int(limit or 500), 5000))
        rows = c.execute(sql, args).fetchall()
        return [dict(r) for r in rows]

    def issue_events(self, issue_id: str, limit: int = 50) -> List[Dict[str, Any]]:
        c = self._ensure()
        rows = c.execute("SELECT * FROM issue_event WHERE issue_id=? "
                         "ORDER BY id DESC LIMIT ?", (issue_id, limit)).fetchall()
        return [dict(r) for r in rows]

    # ---------- 统计 ----------

    def status_counts(self, cycle_id: str = "") -> Dict[str, int]:
        c = self._ensure()
        sql = "SELECT status, COUNT(*) n FROM gov_issue WHERE 1=1"
        args: list = []
        if cycle_id:
            sql += " AND cycle_id=?"
            args.append(cycle_id)
        sql += " GROUP BY status"
        counts = {s: 0 for s in ALL_STATUSES}
        for r in c.execute(sql, args):
            counts[r["status"]] = r["n"]
        return counts

    def cycle_summary(self, cid: str) -> Dict[str, Any]:
        """单期体检统计（完成率/剩余/新增/复发/豁免）。"""
        c = self._ensure()
        total = c.execute(
            "SELECT COUNT(*) n FROM gov_issue WHERE cycle_id=?", (cid,)).fetchone()["n"]
        closed = c.execute(
            "SELECT COUNT(*) n FROM gov_issue WHERE cycle_id=? AND status=?",
            (cid, STATUS_ARCHIVED)).fetchone()["n"]
        verified = c.execute(
            "SELECT COUNT(*) n FROM gov_issue WHERE cycle_id=? AND status=?",
            (cid, STATUS_VERIFIED)).fetchone()["n"]
        rejected = c.execute(
            "SELECT COUNT(*) n FROM gov_issue WHERE cycle_id=? AND status=?",
            (cid, STATUS_REJECTED)).fetchone()["n"]
        recurred = c.execute(
            "SELECT COUNT(*) n FROM gov_issue WHERE cycle_id=? AND recurred=1",
            (cid,)).fetchone()["n"]
        closed += verified  # Verified 视为已达标待归档
        return {
            "cycle_id": cid,
            "total": total,
            "done": closed,
            "rejected": rejected,
            "recurred": recurred,
            "completion_rate": round(closed / total, 2) if total else 0.0,
            "remaining": total - closed - rejected,
        }

    def trend(self) -> List[Dict[str, Any]]:
        """跨周期趋势：各 closed cycle 的 done/remaining/recurred。"""
        out = []
        for cyc in self.list_cycles():
            if cyc["status"] != "closed":
                continue
            s = self.cycle_summary(cyc["id"])
            out.append({
                "cycle_id": cyc["id"], "name": cyc["name"],
                "end_date": cyc["end_date"],
                "done": s["done"], "remaining": s["remaining"],
                "recurred": s["recurred"],
                "completion_rate": s["completion_rate"],
            })
        return out