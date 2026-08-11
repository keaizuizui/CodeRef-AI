# -*- coding: utf-8 -*-
"""
人能看懂的变更报告 —— 让不懂代码的人也知道 AI 到底改了什么

痛点：AI 每次改完代码，普通的 diff 对非技术人员完全不友好。
本模块把 diff 归纳为「人话版」变更说明：新增 XX 功能 / 修改 XX 逻辑 / 可能影响 XX 地方 / 风险。

核心逻辑：
  1. 复用 parse_diff 解析 git diff，得到变更单元（文件 + 变更行 + 变更内容）；
  2. 用 LLM 把每个变更单元归纳为结构化的人话描述（added / modified / impact / risk）；
  3. 无 LLM 或解析失败时，降级为纯结构摘要（新增行数、涉及文件、删改关键词），保证始终可读。

设计约束：
  - 纯 Python 标准库 + 复用 parse_diff / code_review._read_file_content / llm_integration；
  - 所有面向用户的可读文本使用中文；
  - **人话报告由 LLM 生成，输出必须标注「LLM 生成，请核对」**，避免归纳偏差误导；
  - magic number 集中为模块级常量。

作者: CodeRef-AI Team
"""

import os
from typing import Dict, List, Optional, Any

from loguru import logger

from core.code_review import parse_diff, _read_file_content, _llm_available
from core.llm_integration import LLMIntegration


# ═══════════════════════════════════════════════════════════════════════
# 模块级常量（集中管理 magic number）
# ═══════════════════════════════════════════════════════════════════════

# 单次 prompt 中粘贴的 diff 最大字符数
MAX_PROMPT_DIFF_CHARS = 6000
# 单次 prompt 中合并的变更单元数上限
MAX_UNITS_PER_PROMPT = 8
# 人话报告最大输出 token
CHANGE_REPORT_MAX_TOKENS = 2048


# ═══════════════════════════════════════════════════════════════════════
# 人话版变更报告
# ═══════════════════════════════════════════════════════════════════════

class ChangeReport:
    """把 diff 归纳为「人话版」变更说明。"""

    def __init__(self, llm: Optional[LLMIntegration] = None):
        """初始化。llm 为空时自动创建 LLMIntegration()。"""
        self.llm = llm if llm is not None else LLMIntegration()

    def report(self, project_path: str, diff: str) -> Dict[str, Any]:
        """生成人话版变更报告。

        参数:
            project_path: 项目根目录（绝对路径）
            diff: git diff 文本

        返回:
            {
              "items": [  # 每条变更单元的人话描述
                {
                  "file": str,
                  "added": str,        # 新增了 XX 功能
                  "modified": str,     # 修改了 XX 逻辑
                  "impact": str,       # 可能影响 XX 地方
                  "risk": str,         # 风险说明
                  "source": "llm" / "structural",
                }
              ],
              "summary": str,
              "generated_by": "llm" / "structural",
              "disclaimer": "AI 生成，请以实际代码为准"
            }
        """
        units = parse_diff(diff)
        if not units:
            return {
                "items": [],
                "summary": "未解析到任何代码变更。",
                "generated_by": "structural",
                "disclaimer": "本报告基于 git diff 自动生成，请以实际代码为准。",
            }

        available = _llm_available(self.llm)
        items: List[Dict[str, Any]] = []
        struct_reason = ""

        try:
            if available and len(units) <= MAX_UNITS_PER_PROMPT:
                items = self._llm_summarize(project_path, units)
            else:
                if not available:
                    struct_reason = "LLM 不可用"
                else:
                    struct_reason = f"变更单元数（{len(units)}）超过单次处理上限（{MAX_UNITS_PER_PROMPT}）"
                items = self._structural_summarize(project_path, units)
        except Exception as e:
            logger.exception(f"人话报告生成出现未预期异常: {e}")
            struct_reason = "LLM 调用异常"
            items = self._structural_summarize(project_path, units)

        generated_by = "llm" if items and items[0].get("source") == "llm" else "structural"
        summary = self._build_summary(units, items, generated_by, struct_reason)
        return {
            "items": items,
            "summary": summary,
            "generated_by": generated_by,
            "disclaimer": "本报告由 AI 生成，请以实际代码为准；低置信描述请人工核对。",
        }

    # ── LLM 归纳 ────────────────────────────────────────────────────
    def _llm_summarize(self, project_path: str,
                       units: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """用 LLM 把变更单元归纳为人话描述。"""
        blocks = []
        for i, unit in enumerate(units[:MAX_UNITS_PER_PROMPT]):
            file_rel = unit["file"]
            abs_path = os.path.join(project_path, file_rel)
            content = _read_file_content(abs_path)
            changed = sorted(unit.get("changed_lines", []))
            # 变更内容（hunks）
            diff_text = self._format_unit(unit)
            blocks.append({
                "idx": i,
                "file": file_rel,
                "changed_lines": changed,
                "diff": diff_text[:MAX_PROMPT_DIFF_CHARS],
            })

        prompt = self._build_prompt(blocks)
        messages = [
            {"role": "system", "content": (
                "你是一位资深工程师，擅长把代码变更归纳成非技术用户也能看懂的人话。"
                "你只返回 JSON 数组，不输出任何其它文字或 Markdown 代码块。"
            )},
            {"role": "user", "content": prompt},
        ]

        response = self.llm.chat_completion(
            messages, max_tokens=CHANGE_REPORT_MAX_TOKENS, temperature=0.3
        )
        if response.startswith("LLM调用错误"):
            logger.warning(f"LLM 调用失败，降级为结构摘要: {response[:100]}")
            return self._structural_summarize_units(units)

        data = self.llm._try_parse_json(response)
        if not isinstance(data, list):
            logger.warning("LLM 返回非 JSON 数组，降级为结构摘要")
            return self._structural_summarize_units(units)

        items = []
        for item in data:
            if not isinstance(item, dict):
                continue
            items.append({
                "file": str(item.get("file") or ""),
                "added": str(item.get("added") or ""),
                "modified": str(item.get("modified") or ""),
                "impact": str(item.get("impact") or ""),
                "risk": str(item.get("risk") or ""),
                "source": "llm",
            })
        return items

    # ── 结构降级摘要 ────────────────────────────────────────────────
    def _structural_summarize(self, project_path: str,
                              units: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        return self._structural_summarize_units(units)

    def _structural_summarize_units(self, units: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """无 LLM / 解析失败时的结构降级：新增行数 + 涉及文件 + 删改关键词。"""
        items = []
        for unit in units:
            file_rel = unit["file"]
            changed = sorted(unit.get("changed_lines", []))
            added_lines = len(changed)
            deleted = self._collect_deleted_keywords(unit)
            add_desc = f"新增约 {added_lines} 行代码" if added_lines else "无新增行"
            mod_desc = ("修改逻辑涉及：" + "、".join(deleted)) if deleted else "未检测到明显的逻辑改动"
            items.append({
                "file": file_rel,
                "added": add_desc,
                "modified": mod_desc,
                "impact": "请结合变更行号 " + self._line_range_str(changed) + " 人工确认",
                "risk": "结构摘要，未做语义判断",
                "source": "structural",
            })
        return items

    @staticmethod
    def _collect_deleted_keywords(unit: Dict[str, Any]) -> List[str]:
        """收集被删除/新增行中的能力关键词，用于结构摘要。"""
        kws = []
        for hunk in unit.get("hunks", []):
            for c in hunk.get("changes", []):
                text = c.get("text", "").lower()
                for kw in ("validate", "sanitize", "retry", "timeout", "assert",
                           "exception", "catch", "if", "return", "handle"):
                    if kw in text and kw not in kws:
                        kws.append(kw)
                if len(kws) >= 6:
                    break
            if len(kws) >= 6:
                break
        return kws

    @staticmethod
    def _line_range_str(lines: List[int]) -> str:
        if not lines:
            return "未知"
        if len(lines) == 1:
            return f"第 {lines[0]} 行"
        return f"第 {lines[0]}-{lines[-1]} 行"

    # ── prompt 构造 ─────────────────────────────────────────────────
    def _build_prompt(self, blocks: List[Dict[str, Any]]) -> str:
        sections = []
        for b in blocks:
            ch_range = self._line_range_str(b["changed_lines"])
            sections.append(
                f"### 变更 {b['idx'] + 1}：文件 {b['file']}（{ch_range}）\n"
                f"```\n{b['diff']}\n```"
            )
        return (
            "请把以下代码变更归纳成「人话版」说明，让不懂代码的人也能看懂。\n\n"
            + "\n\n".join(sections)
            + "\n\n请为每个变更输出一个对象，字段如下：\n"
            "- \"file\": 文件路径\n"
            "- \"added\": 这次改动**新增**了什么功能（一句话，人话）\n"
            "- \"modified\": 这次改动**修改**了什么逻辑（一句话，人话）\n"
            "- \"impact\": 可能**影响**项目的哪些地方（模块/功能，人话）\n"
            "- \"risk\": 有没有风险（低/中/高 + 一句说明；没有则写\"低\"）\n"
            "严格只返回 JSON 数组，不要输出任何其它内容。"
        )

    @staticmethod
    def _format_unit(unit: Dict[str, Any]) -> str:
        """把单个变更单元格式化为可读 diff。"""
        lines = [f"--- {unit['file']}"]
        for h in unit.get("hunks", []):
            lines.append(
                f"@@ 新文件行 {h.get('new_start', 1)}-{h.get('new_end', 1)} @@"
            )
            for c in h.get("changes", []):
                marker = {"add": "+", "del": "-", "context": " "}.get(c["type"], " ")
                lines.append(f"{marker}{c['text']}")
        return "\n".join(lines)

    # ── summary ─────────────────────────────────────────────────────
    def _build_summary(self, units: List[Dict[str, Any]], items: List[Dict[str, Any]],
                       generated_by: str, struct_reason: str = "") -> str:
        files = ", ".join(u["file"] for u in units[:5])
        more = f" 等 {len(units)} 个文件" if len(units) > 5 else ""
        total_added = sum(len(u.get("changed_lines", [])) for u in units)
        if generated_by == "llm":
            return (
                f"本次改动涉及 {len(units)} 个文件（{files}{more}），"
                f"共新增约 {total_added} 行。AI 已归纳为「新增/修改/影响/风险」人话说明，"
                f"请以实际代码为准。"
            )
        reason = struct_reason or "LLM 不可用或解析失败"
        return (
            f"本次改动涉及 {len(units)} 个文件（{files}{more}），"
            f"共新增约 {total_added} 行。{reason}，已降级为结构摘要，"
            f"请结合变更行号人工确认影响范围。"
        )