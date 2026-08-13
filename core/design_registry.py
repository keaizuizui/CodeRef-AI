# -*- coding: utf-8 -*-
"""
设计注册表 —— 已知设计的规范化存储与别名归一化

用途：
  1. 维护一份"已知设计"库（canonical 设计），供 MCP 工具 coderef_innovation /
     coderef_asset / coderef_registry 查询。
  2. 解决 LLM 命名漂移：同一设计被不同话术命名（如 "5W1H 问法" 与
     "结构化提问"），通过 alias→canonical 归一化到同一 canonical。
  3. 提供资产（asset）区，供 InnovationEngine.commit 固化已经验证的设计为
     WorkflowAsset 写入 data/design_registry.json 资产区。

存储：data/design_registry.json（原子写，文本库，无第三方依赖）。

设计约束（与底座一致）：
  - 纯标准库 + 复用同项目底座，不引入第三方新依赖；
  - 面向使用者的可读文本一律中文；
  - 异常不静默吞掉（记录日志并抛出/降级）；
  - magic number 集中定义为模块级常量；不改 config/settings.py。

作者: CodeRef-AI Team
版本: v1.0
"""

import os
import json
import tempfile
from datetime import datetime
from typing import Dict, List, Optional, Any

from loguru import logger


# ═══════════════════════════════════════════════════════════════════
# 模块级常量（集中管理 magic number）
# ═══════════════════════════════════════════════════════════════════

# 注册表文件版本
REGISTRY_VERSION = 1

# 内置常见设计种子（canonical 名称）
SEED_DESIGNS: Dict[str, Dict[str, str]] = {
    "validation_chain": {
        "canonical": "validation_chain",
        "category": "validation",
        "description": "输入 → 校验 → 清洗 → 标准化 的多步校验链，防止脏数据污染下游。",
    },
    "retry_wrapper": {
        "canonical": "retry_wrapper",
        "category": "resilience",
        "description": "重试/回退包装器，对临时故障（网络抖动、超时、5xx）做指数退避重试。",
    },
    "prompt_template": {
        "canonical": "prompt_template",
        "category": "prompt",
        "description": "结构化 Prompt 模板（{placeholder}），统一问法与输出格式。",
    },
    "orchestration": {
        "canonical": "orchestration",
        "category": "architecture",
        "description": "管道式编排层，把多个子流程串成可复用工作流。",
    },
}

# 内置别名字典（alias → canonical），用于归一化 LLM 命名漂移
SEED_ALIASES: Dict[str, List[str]] = {
    "validation_chain": ["校验链", "输入校验链", "validate-sanitize-normalize", "validation chain"],
    "retry_wrapper": ["重试包装", "重试逻辑", "重试装饰器", "retry decorator", "retry"],
    "prompt_template": [
        "提示词模板", "Prompt 模板", "5W1H 问法", "5w1h 问法", "5W1H 提问",
        "结构化提问", "结构化提问法", "问法模板",
    ],
    "orchestration": ["编排器", "编排层", "工作流编排", "orchestrator", "pipeline flow"],
}


def _default_registry_path() -> str:
    """默认注册表文件路径：项目根目录下 data/design_registry.json"""
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(root, "data", "design_registry.json")


class DesignRegistry:
    """已知设计注册表 —— 规范化设计与别名归一化。

    存储结构（data/design_registry.json）:
        {
            "version": 1,
            "designs": { canonical: {"canonical","category","description","aliases"} },
            "assets":  { canonical: { ...WorkflowAsset 字段... } }
        }
    """

    def __init__(self, registry_path: Optional[str] = None):
        self.registry_path = registry_path or _default_registry_path()
        self._data: Dict[str, Any] = {}
        self._load()

    # ─── 持久化（原子写） ────────────────────────────────────────

    def _load(self) -> None:
        """加载注册表。文件不存在时初始化种子数据。"""
        if os.path.exists(self.registry_path):
            try:
                with open(self.registry_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if not isinstance(data, dict):
                    raise ValueError("设计注册表内容不是合法 JSON 对象")
                self._data = {
                    "version": data.get("version", REGISTRY_VERSION),
                    "designs": data.get("designs", {}),
                    "assets": data.get("assets", {}),
                }
                # 若文件来自旧版本，补齐种子，避免缺少内置设计
                self._ensure_seeds()
                logger.info(f"[DesignRegistry] 已加载注册表: {self.registry_path}")
                return
            except Exception as e:
                # 重建前将不可读文件备份，避免无备份覆盖导致数据丢失
                backup = f"{self.registry_path}.corrupt-{int(datetime.now().timestamp())}"
                try:
                    os.replace(self.registry_path, backup)
                    logger.error(f"[DesignRegistry] 加载注册表失败，原文件已备份到 {backup}，将重建: {e}")
                except OSError as be:
                    logger.error(f"[DesignRegistry] 加载注册表失败且备份失败({be})，将重建: {e}")
        # 初始化内置种子
        self._data = {
            "version": REGISTRY_VERSION,
            "designs": {},
            "assets": {},
        }
        self._ensure_seeds()
        self._save()
        logger.info(f"[DesignRegistry] 已初始化注册表: {self.registry_path}")

    def _ensure_seeds(self) -> None:
        """把内置种子设计合并进注册表（不覆盖人工修改的已有条目）。"""
        changed = False
        for canonical, info in SEED_DESIGNS.items():
            if canonical not in self._data["designs"]:
                entry = dict(info)
                entry["aliases"] = list(SEED_ALIASES.get(canonical, []))
                self._data["designs"][canonical] = entry
                changed = True
            else:
                existing = self._data["designs"].get(canonical, {})
                existing.setdefault("aliases", [])
                # 合并种子别名，避免重复
                for al in SEED_ALIASES.get(canonical, []):
                    if al not in existing["aliases"]:
                        existing["aliases"].append(al)
                        changed = True
        if changed:
            self._save()

    def _atomic_write(self) -> None:
        """原子写：先写临时文件再 os.replace，杜绝半写损坏。"""
        os.makedirs(os.path.dirname(self.registry_path), exist_ok=True)
        fd, tmp_path = tempfile.mkstemp(
            dir=os.path.dirname(self.registry_path),
            prefix=".design_registry_",
            suffix=".tmp",
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(self._data, f, ensure_ascii=False, indent=2)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_path, self.registry_path)
        except Exception:
            # 清理残留临时文件后重新抛出，不静默吞掉
            try:
                if os.path.exists(tmp_path):
                    os.remove(tmp_path)
            except OSError:
                pass
            raise

    def _save(self) -> None:
        """持久化当前数据到注册表文件（原子写）。"""
        self._atomic_write()

    # ─── 别名归一化 ──────────────────────────────────────────────

    def resolve(self, name: str) -> str:
        """把任意名称（canonical 或别名）归一化到 canonical。

        Args:
            name: 名称或别名。

        Returns:
            归一化后的 canonical 名称；若无法匹配则原样返回 name。
        """
        if not name:
            return name
        name = name.strip()
        if name in self._data["designs"]:
            return name
        # 大小写不敏感匹配 canonical
        lower = name.lower()
        for canonical in self._data["designs"]:
            if canonical.lower() == lower:
                return canonical
        # 匹配别名
        for canonical, entry in self._data["designs"].items():
            for al in entry.get("aliases", []):
                if al.lower() == lower:
                    return canonical
        return name

    def get_design(self, canonical: str) -> Optional[Dict[str, Any]]:
        """获取 canonical 设计（含别名归一化）。"""
        resolved = self.resolve(canonical)
        entry = self._data["designs"].get(resolved)
        if entry is None:
            return None
        return dict(entry)

    def list_designs(self) -> List[Dict[str, Any]]:
        """列出所有已知设计。"""
        out = []
        for canonical, entry in self._data["designs"].items():
            item = dict(entry)
            item["canonical"] = canonical
            out.append(item)
        return out

    # ─── 管理接口 ────────────────────────────────────────────────

    def manage(
        self,
        project_path: str,
        action: str,
        name: str = "",
        canonical: str = "",
        alias: str = "",
        description: str = "",
    ) -> Dict[str, Any]:
        """管理设计注册表。

        Args:
            project_path: 触发操作的项目路径（用于记录来源元数据）。
            action: list / add / alias。
            name: add 时的 canonical 名称；alias 时忽略。
            canonical: alias 时目标 canonical。
            alias: alias 时新增的别名。
            description: add 时的设计描述。

        Returns:
            结构化 dict（见下述字段说明）。
        """
        action = (action or "").strip().lower()
        if action == "list":
            designs = self.list_designs()
            return {
                "ok": True,
                "action": "list",
                "registry_path": self.registry_path,
                "count": len(designs),
                "designs": designs,
            }
        if action == "add":
            if not name or not name.strip():
                raise ValueError("add 操作必须提供非空的 canonical 名称（name）")
            canonical = self.resolve(name)
            if canonical != name.strip():
                raise ValueError(
                    f"名称「{name}」已通过别名映射到 canonical「{canonical}」，"
                    f"请直接使用 canonical 名称或改用 alias 操作。"
                )
            entry = self._data["designs"].setdefault(canonical, {
                "canonical": canonical,
                "category": "misc",
                "description": description or "",
                "aliases": [],
                "source_project": project_path,
            })
            entry["canonical"] = canonical
            if description:
                entry["description"] = description
            entry.setdefault("aliases", [])
            entry.setdefault("category", "misc")
            entry["source_project"] = project_path
            self._save()
            return {
                "ok": True,
                "action": "add",
                "registry_path": self.registry_path,
                "canonical": canonical,
                "total": len(self._data["designs"]),
                "message": f"已新增/更新设计「{canonical}」。",
            }
        if action == "alias":
            if not alias or not alias.strip():
                raise ValueError("alias 操作必须提供别名（alias）参数")
            if not canonical or not canonical.strip():
                raise ValueError("alias 操作必须提供目标 canonical 参数")
            resolved = self.resolve(canonical)
            if resolved not in self._data["designs"]:
                raise ValueError(f"目标 canonical「{canonical}」不存在，请先 add。")
            entry = self._data["designs"][resolved]
            entry.setdefault("aliases", [])
            alias = alias.strip()
            if alias not in entry["aliases"]:
                entry["aliases"].append(alias)
            self._save()
            return {
                "ok": True,
                "action": "alias",
                "registry_path": self.registry_path,
                "alias": alias,
                "canonical": resolved,
                "resolved": self.resolve(alias),
                "message": f"别名「{alias}」已归一化到 canonical「{resolved}」。",
            }
        raise ValueError(f"不支持的 action「{action}」，仅支持 list / add / alias。")

    # ─── 资产（asset）区 ────────────────────────────────────────

    def add_asset(self, asset: Dict[str, Any]) -> Dict[str, Any]:
        """写入/更新一个 WorkflowAsset 到资产区。"""
        canonical = asset.get("canonical", "") or ""
        if not canonical:
            raise ValueError("资产必须提供 canonical 字段。")
        self._data["assets"][canonical] = asset
        self._save()
        return self.get_asset(canonical) or asset

    def get_asset(self, canonical: str) -> Optional[Dict[str, Any]]:
        """按 canonical 获取资产。"""
        resolved = self.resolve(canonical)
        asset = self._data["assets"].get(resolved)
        return dict(asset) if asset else None

    def list_assets(self) -> List[Dict[str, Any]]:
        """列出所有已固化资产。"""
        return [dict(v) for v in self._data["assets"].values()]

    def count_assets(self) -> int:
        """资产数量。"""
        return len(self._data["assets"])