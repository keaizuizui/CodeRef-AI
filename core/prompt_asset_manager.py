# -*- coding: utf-8 -*-
"""
Prompt 资产管理器 —— PromptAssetManager

维护项目的 prompt 资产，存储于 data/prompt_assets.json（原子写），
供 MCP 工具 coderef_prompt_mgmt 调用。

支持四类操作（manage 的 action 参数）：
- list    —— 列出项目所有 prompt 资产
- version —— 对某资产记录新版本，标注当前生效版，可回滚
- compare —— 对同一场景不同版本评分（结构/清晰度/命中三维度）
- abtest  —— 同一任务下发不同版本到 A/B 组，择优晋升

设计约定：
- 纯标准库
- 中文可读文本
- magic number 收敛为模块级常量
- 原子写（.tmp + os.replace）

存储结构：
{
  "updated": "...",
  "projects": {
    "<md5>": {
      "path": "...",
      "assets": {
        "<name>": {
          "active_version": "v1",
          "versions": [
            {"version": "v1", "content": "...", "size": N, "created_at": "..."}
          ],
          "abtest": {
            "A": {"version": "v1", "content": "...", "deployed_at": "..."},
            "B": {"version": "v2", "content": "...", "deployed_at": "..."},
            "winner": "A",
            "promoted_at": "..."
          }
        }
      }
    }
  }
}
"""

import os
import json
import hashlib
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Any, Optional

from loguru import logger


# ═══════════════════════════════════════════════════════════════
# 模块级常量（magic number 收敛）
# ═══════════════════════════════════════════════════════════════

ASSET_FILE = "prompt_assets.json"          # 存储文件名
CONTENT_CAP = 10000                        # 单条 content 最大保存长度
DEFAULT_VERSION = "v1"                     # 首个版本号
MAX_COMPARE_VERSIONS = 20                  # compare 最多评分版本数

# 评分满分
SCORE_MAX = 100
# 三维度权重
W_STRUCTURE = 0.35
W_CLARITY = 0.35
W_HIT = 0.30

# 结构评分：出现以下特征各加分（用于 No.1 结构维度）
STRUCTURE_FEATURES = [
    ("角色定义", ("你是", "You are", "角色", "role")),
    ("输出格式", ("输出格式", "output format", "json", "JSON", "markdown", "Markdown")),
    ("分节标题", ("##", "###", "====", "【", "步骤", "Step", "step")),
    ("约束条件", ("必须", "禁止", "不得", "不要", "must", "must not", "do not")),
]
# 清晰度负面信号（出现则扣分）
CLARITY_NEGATIVE = ["TODO", "待补充", "xxx", "lorem", "占位"]

# abtest 分组
GROUP_A = "A"
GROUP_B = "B"
ABTEST_GROUPS = (GROUP_A, GROUP_B)

# 缺失摘要来源标识
SOURCE_PROMPT_ASSET = "prompt-asset"


# ═══════════════════════════════════════════════════════════════
# Prompt 资产管理器
# ═══════════════════════════════════════════════════════════════

class PromptAssetManager:
    """项目 prompt 资产的生命周期管理"""

    def __init__(self, data_dir: Optional[str] = None):
        if data_dir is None:
            data_dir = str(Path(__file__).parent.parent / "data")
        self._data_dir = data_dir
        os.makedirs(self._data_dir, exist_ok=True)
        self._store_path = os.path.join(self._data_dir, ASSET_FILE)

    # ─── 主入口 ────────────────────────────────────────────────

    def manage(self, project_path: str, action: str = "list",
               name: str = "", content: str = "", version: str = "",
               abtest_group: str = "") -> Dict[str, Any]:
        """
        统一入口，分派到具体操作。

        Args:
            project_path: 项目路径
            action: list | version | compare | abtest
            name: 资产名（场景名，如 "安全审计"）
            content: prompt 内容
            version: 版本号（缺省自动生成，如 v2）
            abtest_group: A | B | promote

        Returns:
            结构化 dict（各操作字段不同，均含 ok/action/summary）
        """
        if action == "list":
            return self._action_list(project_path)
        if action == "version":
            return self._action_version(project_path, name, content, version)
        if action == "compare":
            return self._action_compare(project_path, name, version)
        if action == "abtest":
            return self._action_abtest(project_path, name, content, version, abtest_group)
        return {
            "ok": False,
            "action": action,
            "error": f"未知 action: {action}（支持 list/version/compare/abtest）",
        }

    # ─── list ──────────────────────────────────────────────────

    def _action_list(self, project_path: str) -> Dict[str, Any]:
        store = self._load_store()
        proj = self._get_project(store, project_path)
        assets = proj.get("assets", {})

        asset_list = []
        for aname, info in assets.items():
            versions = info.get("versions", [])
            abtest = info.get("abtest", {})
            asset_list.append({
                "name": aname,
                "active_version": info.get("active_version", ""),
                "versions": [v.get("version") for v in versions],
                "version_count": len(versions),
                "abtest_groups": {
                    g: {"version": abtest.get(g, {}).get("version", ""),
                        "deployed": bool(abtest.get(g))}
                    for g in ABTEST_GROUPS
                },
                "winner": abtest.get("winner", ""),
            })

        return {
            "ok": True,
            "action": "list",
            "project": project_path,
            "assets": asset_list,
            "asset_count": len(asset_list),
            "summary": f"共 {len(asset_list)} 个 prompt 资产。"
                       + ("" if asset_list else "当前项目暂无资产。"),
        }

    # ─── version ───────────────────────────────────────────────

    def _action_version(self, project_path: str, name: str, content: str,
                        version: str) -> Dict[str, Any]:
        if not name:
            return {"ok": False, "action": "version", "error": "缺少资产名 name。"}

        store = self._load_store()
        proj = self._get_project(store, project_path)
        assets = proj.setdefault("assets", {})
        info = assets.setdefault(name, {"active_version": "", "versions": [], "abtest": {}})

        content = content[:CONTENT_CAP]

        # 指定版本号：优先处理回滚/覆盖
        if version:
            existing = self._find_version(info, version)
            if existing is not None:
                # 回滚/切换：content 为空仅切换生效版本
                if not content:
                    info["active_version"] = version
                    self._save_store(store)
                    return {
                        "ok": True, "action": "version", "mode": "rollback",
                        "name": name, "active_version": version,
                        "summary": f"已回滚「{name}」到 {version}。",
                    }
                # 覆盖：版本号已存在且带新内容
                existing["content"] = content
                existing["size"] = len(content)
                existing["updated_at"] = datetime.now().isoformat()
                info["active_version"] = version
                self._save_store(store)
                return {
                    "ok": True, "action": "version", "mode": "overwrite",
                    "name": name, "active_version": version,
                    "summary": f"已覆盖「{name}」{version} 并设为生效版。",
                }
            # 指定了新版本号但未存在 → 需提供内容
            if not content:
                return {"ok": False, "action": "version",
                        "error": f"版本 {version} 尚不存在，新增版本需要提供 content。"}
        else:
            # 未指定版本号 → 需提供内容，自动生成下一个版本号
            if not content:
                return {"ok": False, "action": "version", "error": "缺少内容 content。"}
            version = self._next_version(info)

        # 新增版本
        info["versions"].append({
            "version": version,
            "content": content,
            "size": len(content),
            "created_at": datetime.now().isoformat(),
        })
        info["active_version"] = version
        self._save_store(store)

        versions_available = [v.get("version") for v in info["versions"]]
        return {
            "ok": True, "action": "version", "mode": "create",
            "name": name, "version": version, "active_version": version,
            "versions_available": versions_available,
            "summary": f"已为「{name}」记录新版本 {version} 并设为当前生效版。"
                       f"可回滚到：{versions_available[:-1] or '无'}",
        }

    # ─── compare ───────────────────────────────────────────────

    def _action_compare(self, project_path: str, name: str, version: str = "") -> Dict[str, Any]:
        if not name:
            return {"ok": False, "action": "compare", "error": "缺少资产名 name。"}

        store = self._load_store()
        proj = self._get_project(store, project_path)
        info = proj.get("assets", {}).get(name)
        if not info:
            return {"ok": False, "action": "compare", "error": f"资产「{name}」不存在，请先使用 version 创建。"}

        versions = info.get("versions", [])
        if not versions:
            return {"ok": False, "action": "compare", "error": f"资产「{name}」暂无版本。"}

        # 可指定某版本对比，否则对比全部版本
        if version:
            versions = [v for v in versions if v.get("version") == version]

        # 先过滤再截断，避免超过 MAX_COMPARE_VERSIONS 索引的版本被误排除
        versions = versions[:MAX_COMPARE_VERSIONS]
        if not versions:
            return {"ok": False, "action": "compare",
                    "error": f"资产「{name}」未找到版本 {version}。"}

        scored = []
        for v in versions:
            scores = self.score_content(v.get("content", ""))
            scored.append({
                "version": v.get("version"),
                "created_at": v.get("created_at", ""),
                **scores,
            })

        scored.sort(key=lambda x: x["total"], reverse=True)
        if not scored:
            return {"ok": False, "action": "compare",
                    "error": f"资产「{name}」版本评分失败，无法生成对比结果。"}
        best = scored[0]["version"]
        return {
            "ok": True, "action": "compare",
            "name": name, "best_version": best,
            "dimensions": ["structure", "clarity", "hit"],
            "results": scored,
            "summary": f"「{name}」共对比 {len(scored)} 个版本，最佳为 {best}（总分 {scored[0]['total']}）。",
        }

    # ─── abtest ────────────────────────────────────────────────

    def _action_abtest(self, project_path: str, name: str, content: str,
                       version: str, abtest_group: str) -> Dict[str, Any]:
        if not name:
            return {"ok": False, "action": "abtest", "error": "缺少资产名 name。"}

        store = self._load_store()
        proj = self._get_project(store, project_path)
        info = proj.setdefault("assets", {}).setdefault(
            name, {"active_version": "", "versions": [], "abtest": {}})
        abtest = info.setdefault("abtest", {})

        # 晋升模式
        if abtest_group == "promote":
            return self._promote(store, proj, name, info, abtest)

        # 部署到 A/B 组
        if abtest_group not in ABTEST_GROUPS:
            return {
                "ok": False, "action": "abtest",
                "error": f"abtest_group 须为 {ABTEST_GROUPS[0]}/{ABTEST_GROUPS[1]} 或 promote，收到「{abtest_group}」。",
            }
        if not content:
            return {"ok": False, "action": "abtest", "error": "部署到分组需要提供 content。"}

        content = content[:CONTENT_CAP]
        version = version or self._next_version(info)
        abtest[abtest_group] = {
            "version": version,
            "content": content,
            "deployed_at": datetime.now().isoformat(),
        }
        self._save_store(store)

        both = all(g in abtest for g in ABTEST_GROUPS)
        return {
            "ok": True, "action": "abtest", "mode": "deploy",
            "name": name, "group": abtest_group, "version": version,
            "groups": {g: abtest.get(g, {}).get("version", "") for g in ABTEST_GROUPS},
            "ready_to_promote": both,
            "summary": f"已将「{name}」的 {version} 下发到 {abtest_group} 组。"
                       + ("两组均已就绪，可调用 abtest_group=promote 择优晋升。" if both
                          else f"还需下发 {self._pending_group(abtest)} 组。"),
        }

    def _promote(self, store, proj, name: str, info: Dict, abtest: Dict) -> Dict[str, Any]:
        """择优选优并晋升为生效版。"""
        if not all(g in abtest for g in ABTEST_GROUPS):
            return {
                "ok": False, "action": "abtest", "mode": "promote",
                "error": f"A/B 两组未全部部署，无法晋升（当前：{', '.join(abtest.keys()) or '无'}）。",
            }

        a_score = self.score_content(abtest[GROUP_A].get("content", ""))["total"]
        b_score = self.score_content(abtest[GROUP_B].get("content", ""))["total"]
        winner = GROUP_A if a_score >= b_score else GROUP_B
        winner_version = abtest[winner].get("version", "")

        abtest["winner"] = winner
        abtest["promoted_at"] = datetime.now().isoformat()
        info["active_version"] = winner_version
        self._save_store(store)

        return {
            "ok": True, "action": "abtest", "mode": "promote",
            "name": name, "winner": winner, "winner_version": winner_version,
            "scores": {
                GROUP_A: {"version": abtest[GROUP_A].get("version"), "total": a_score},
                GROUP_B: {"version": abtest[GROUP_B].get("version"), "total": b_score},
            },
            "summary": f"A 组 {a_score} 分 vs B 组 {b_score} 分，"
                       f"晋升 {winner} 组（{winner_version}）为「{name}」生效版。",
        }

    # ─── 评分 ──────────────────────────────────────────────────

    @classmethod
    def score_content(cls, content: str) -> Dict[str, Any]:
        """对 prompt 内容按 结构/清晰度/命中 三维度评分（0-100）。"""
        text = content or ""
        length = len(text.strip())

        # 结构分
        structure = SCORE_MAX
        if length < 20:
            structure = 25
        else:
            hit_features = sum(1 for _, kws in STRUCTURE_FEATURES
                               if any(kw in text for kw in kws))
            structure = min(SCORE_MAX, 30 + hit_features * 18)

        # 清晰度分：有过长/过短惩罚，负面信号扣分
        clarity = SCORE_MAX
        if length < 20:
            clarity = 30
        elif length > CONTENT_CAP:
            clarity = 60
        neg_count = sum(1 for kw in CLARITY_NEGATIVE if kw in text)
        clarity -= neg_count * 25
        clarity = max(0, min(SCORE_MAX, clarity))

        # 命中分：关键词/关键结构覆盖度（贴近常见 prompt 要素）
        hit = 30
        if any(kw in text for kw in ("你是", "You are", "角色")):
            hit += 20
        if any(kw in text for kw in ("输出", "返回", "JSON", "Markdown", "格式")):
            hit += 20
        if any(kw in text for kw in ("必须", "禁止", "约束", "步骤", "Step")):
            hit += 20
        if any(kw in text for kw in ("示例", "例子", "样例如", "比如")):
            hit += 10
        hit = min(SCORE_MAX, hit)

        total = round(W_STRUCTURE * structure + W_CLARITY * clarity + W_HIT * hit, 1)
        meta = {
            "structure": structure,
            "clarity": clarity,
            "hit": hit,
            "total": total,
        }
        if length < 20:
            meta["note"] = "内容过短，评分参考意义有限"
        return meta

    # ─── 版本辅助 ──────────────────────────────────────────────

    @staticmethod
    def _find_version(info: Dict, version: str) -> Optional[Dict]:
        for v in info.get("versions", []):
            if v.get("version") == version:
                return v
        return None

    @staticmethod
    def _next_version(info: Dict) -> str:
        """根据现有版本号生成下一个版本号（v1→v2→...）。"""
        nums = []
        for v in info.get("versions", []):
            ver = str(v.get("version", ""))
            if ver.startswith("v") and ver[1:].isdigit():
                nums.append(int(ver[1:]))
        nxt = (max(nums) + 1) if nums else 1
        return f"v{nxt}"

    @staticmethod
    def _pending_group(abtest: Dict) -> str:
        for g in ABTEST_GROUPS:
            if g not in abtest:
                return g
        return ""

    # ─── 存储 ──────────────────────────────────────────────────

    def _load_store(self) -> Dict[str, Any]:
        if os.path.exists(self._store_path):
            try:
                with open(self._store_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, dict):
                    return data
            except Exception as e:
                logger.warning(f"[PromptAssetManager] 读取存储失败，重置: {e}")
        return {"updated": "", "projects": {}}

    def _save_store(self, store: Dict[str, Any]):
        store["updated"] = datetime.now().isoformat()
        self._atomic_write_json(self._store_path, store)

    @staticmethod
    def _get_project(store: Dict[str, Any], project_path: str) -> Dict[str, Any]:
        abs_path = os.path.abspath(project_path)
        phash = hashlib.md5(abs_path.encode("utf-8")).hexdigest()[:12]
        projects = store.setdefault("projects", {})
        proj = projects.get(phash)
        if proj is None:
            proj = {"path": abs_path, "assets": {}}
            projects[phash] = proj
        return proj

    @staticmethod
    def _atomic_write_json(path: str, data: Dict[str, Any]):
        """原子写 JSON：先写 .tmp 再 os.replace，避免并发读到半写入文件。"""
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        os.replace(tmp, path)


# ═══════════════════════════════════════════════════════════════
# 便捷函数
# ═══════════════════════════════════════════════════════════════

def manage_prompt_assets(project_path: str, action: str = "list",
                         name: str = "", content: str = "", version: str = "",
                         abtest_group: str = "") -> Dict[str, Any]:
    """一键管理 prompt 资产。"""
    manager = PromptAssetManager()
    return manager.manage(project_path, action=action, name=name,
                          content=content, version=version,
                          abtest_group=abtest_group)