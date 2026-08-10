# -*- coding: utf-8 -*-
"""
架构探测器（Architecture Detector）
====================================
在业务分析之前，对项目做一次轻量静态扫描，判断其架构类型，并提取
「调用图之外的入口信号」（Web 路由端点 / 事件监听器 / 插件入口）。

背景：
  业务分析的层级化（入口层→业务层→基础设施层）默认依赖 GitNexus 调用图。
  但调用图只能表达「函数调用链」，对 Web/API、事件驱动、插件化这类
  入口发生在函数图之外（路由注册、消息订阅、插件接口）的架构力不从心。
  本模块提供一个独立于调用图的证据源：直接扫描源码文本，识别架构信号。
  业务分析器把这里的入口信号作为「补充入口证据」，让入口发现不再只依赖
  函数出度/入度。

设计约束：
  - 零依赖：只依赖 datetime/re/dataclasses 与 core.code_models（纯数据模型）
  - 纯静态：只读 project_analysis.files 的 raw_content，不调用外部工具
  - 不抛异常：任何扫描失败都返回默认的 layered 画像，绝不阻断主流程
  - 可解释：画像带置信度与证据列表，便于在报告里展示「为什么判定为某架构」

作者: CodeRef-AI Team
版本: v1.0
"""

import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from loguru import logger

try:
    from core.code_models import CodeFile
except Exception:  # pragma: no cover - 防御性导入
    CodeFile = None  # type: ignore


# ========================================================================
#  架构类型常量
# ========================================================================

ARCH_LAYERED = "layered"            # 分层 / 单体（默认，函数调用链为主体）
ARCH_WEB = "web"                    # Web / API 框架（路由注册入口）
ARCH_EVENT = "event_driven"         # 事件驱动 / 消息队列（订阅/监听入口）
ARCH_PLUGIN = "plugin"              # 插件化（插件注册入口）

ARCH_LABELS = {
    ARCH_LAYERED: "分层/单体",
    ARCH_WEB: "Web/API",
    ARCH_EVENT: "事件驱动",
    ARCH_PLUGIN: "插件化",
}

# 扫描阈值（集中定义于 config/settings.py，此处导入并保留默认值作为防御回退）
try:
    from config.settings import (
        ARCH_DETECT_MAX_ENTRY_SIGNALS as _MAX_ENTRY_SIGNALS,
        ARCH_DETECT_MAX_TARGET_LEN as _MAX_TARGET_LEN,
        ARCH_DETECT_CONFIDENCE_FLOOR as _CONFIDENCE_FLOOR,
        ARCH_DETECT_CONFIDENCE_CAP as _CONFIDENCE_CAP,
    )
except Exception:  # pragma: no cover - 防御性回退，保证模块可独立使用
    _MAX_ENTRY_SIGNALS = 80     # 入口信号总数上限，防止过大拖慢后续
    _MAX_TARGET_LEN = 60        # 入口目标描述截断长度
    _CONFIDENCE_FLOOR = 0.05    # 至少命中 1 个文件时的置信度兜底加成
    _CONFIDENCE_CAP = 1.0       # 置信度上限


# ========================================================================
#  数据模型
# ========================================================================

@dataclass
class EntrySignal:
    """调用图之外的入口信号（路由端点 / 事件监听 / 插件入口）"""
    kind: str                    # web_route / event_listener / plugin_hook
    name: str                    # 函数 / 类 / 处理器名（尽力而为）
    file_path: str               # 所在的源文件路径
    target: str = ""             # 路由路径 / 事件主题 / 插件标识（可空）


@dataclass
class ArchitectureProfile:
    """架构画像 —— 一次探测的结果"""
    arch_type: str = ARCH_LAYERED                      # 判定出的架构类型
    confidence: float = 0.0                            # 置信度 0~1
    evidence: List[str] = field(default_factory=list)  # 判定依据（人类可读）
    entry_signals: List[EntrySignal] = field(default_factory=list)  # 入口信号
    frameworks: List[str] = field(default_factory=list)  # 检测到的框架/中间件

    @property
    def label(self) -> str:
        return ARCH_LABELS.get(self.arch_type, self.arch_type)


# ========================================================================
#  信号模式（跨语言，用正则匹配 raw_content）
# ========================================================================

# 每类信号的多条正则，命中即累加该架构的「文件命中数」。
# 用一个文件里命中多少次都只算 1 次文件命中，避免单文件刷高置信度。
_FRAMEWORK_PATTERNS: Dict[str, List[re.Pattern]] = {
    ARCH_WEB: [
        re.compile(r"fastapi|FastAPI|APIRouter|@app\.(get|post|put|delete|patch|route)|@router\.(get|post|put|delete)", re.I),
        re.compile(r"\bflask\b|Flask\(|@app\.route", re.I),
        re.compile(r"\bdjango\b|Django|urlpatterns", re.I),
        re.compile(r"aiohttp|aiohttp\.web", re.I),
        re.compile(r"\bexpress\b|Router\(\)|app\.(get|post|put|delete|patch)\(|router\.(get|post)\(|\.listen\(", re.I),
        re.compile(r"\bkoa\b|router\.(get|post|put|delete)\(|@RestController|@Controller|@RequestMapping", re.I),
        re.compile(r"@GetMapping|@PostMapping|@PutMapping|@DeleteMapping|SpringBootApplication", re.I),
        re.compile(r"gin\.Default|r\.GET\(|r\.POST\(|r\.HandleFunc|chi\.NewRouter|echo\.New|http\.HandleFunc", re.I),
    ],
    ARCH_EVENT: [
        re.compile(r"\bkafka\b|@kafka_listener|@KafkaListener|消息队列|消息中间件", re.I),
        re.compile(r"\brabbitmq\b|@rabbit_listener|@RabbitListener|@Consume|pika\.", re.I),
        re.compile(r"\bcelery\b|@task\b|事件总线|event.?bus|EventBus|@on_event|@subscribe", re.I),
        re.compile(r"\bpubsub\b|PubSub|redis|pulsar|Pulsar|\bnats\b|\bmqtt\b|\bsqs\b|发布|订阅", re.I),
    ],
    ARCH_PLUGIN: [
        re.compile(r"register_plugin|registerPlugin|@plugin\b|@Plugin\b|plugin\.register|register_hook", re.I),
        re.compile(r"entry_points\s*=|ExtensionPoint|AbstractPlugin|PluginBase|hook\.register|插件注册", re.I),
    ],
}

# 入口信号提取：每条 (正则, kind, 目标捕获组索引或 None)
# 用于把「函数之外的入口」映射回所在模块，增强入口层识别。
_ENTRY_SIGNAL_PATTERNS: List[tuple] = [
    # Web 路由端点（group(1)=HTTP方法, group(2)=路径）
    (re.compile(r"@app\.(get|post|put|delete|patch|route)\s*\(\s*[\"']([^\"']+)[\"']", re.I), "web_route", 2),
    (re.compile(r"@router\.(get|post|put|delete|patch)\s*\(\s*[\"']([^\"']+)[\"']", re.I), "web_route", 2),
    (re.compile(r"@(?:Get|Post|Put|Delete|Patch)Mapping\s*\(*(?:value\s*=\s*)?[\"']([^\"']+)[\"']", re.I), "web_route", 1),
    (re.compile(r"app\.(get|post|put|delete|patch)\s*\(\s*[\"']/([^\"']+)[\"']", re.I), "web_route", 2),
    (re.compile(r"router\.(get|post|put|delete|patch)\s*\(\s*[\"']/([^\"']+)", re.I), "web_route", 2),
    # 事件监听器（group(1)=topic/事件名）
    (re.compile(r"@kafka_listener\s*\(\s*[\"']?([^\"',)]+)", re.I), "event_listener", 1),
    (re.compile(r"@rabbit_listener\s*\(\s*[\"']?([^\"',)]+)", re.I), "event_listener", 1),
    (re.compile(r"@consume\s*\(\s*[\"']([^\"']+)[\"']", re.I), "event_listener", 1),
    (re.compile(r"@on_event\s*\(\s*[\"']([^\"']+)[\"']", re.I), "event_listener", 1),
    (re.compile(r"subscribe\s*\(\s*[\"']([^\"']+)[\"']", re.I), "event_listener", 1),
    # 插件入口（group(1)=插件标识）
    (re.compile(r"@plugin\s*\(\s*[\"']([^\"']+)[\"']", re.I), "plugin_hook", 1),
    (re.compile(r"register_plugin\s*\(\s*[\"']([^\"']+)[\"']", re.I), "plugin_hook", 1),
    (re.compile(r"plugin\.register\s*\(\s*[\"']([^\"']+)[\"']", re.I), "plugin_hook", 1),
]


# ========================================================================
#  探测实现
# ========================================================================

def _scan_file(raw: str) -> Dict[str, int]:
    """扫描单个文件，返回各架构类型的命中标志（0/1）。"""
    if not raw:
        return {}
    hits: Dict[str, int] = {}
    for arch, patterns in _FRAMEWORK_PATTERNS.items():
        for pat in patterns:
            if pat.search(raw):
                hits[arch] = 1
                break  # 一个文件对某类架构只计一次
    return hits


def _extract_entry_signals(files: List[CodeFile]) -> List[EntrySignal]:
    """从源码中提取入口信号（路由 / 事件 / 插件）。"""
    signals: List[EntrySignal] = []
    for f in files:
        raw = getattr(f, "raw_content", "") or ""
        if not raw:
            continue
        for pat, kind, target_group in _ENTRY_SIGNAL_PATTERNS:
            for m in pat.finditer(raw):
                target = m.group(target_group) if target_group is not None else ""
                # 目标过长视为噪声，截断；统一去引号、去前导斜杠，便于去重
                if target and len(target) > _MAX_TARGET_LEN:
                    target = target[:_MAX_TARGET_LEN]
                target = target.strip("\"'").lstrip("/") or "/"
                signals.append(EntrySignal(
                    kind=kind,
                    name="",  # 名称由调用方补充（尽力而为，不在此强求）
                    file_path=f.file_path,
                    target=target,
                ))
    # 单文件同类信号去重，避免刷屏
    seen = set()
    dedup: List[EntrySignal] = []
    for s in signals:
        key = (s.kind, s.file_path, s.target)
        if key in seen:
            continue
        seen.add(key)
        dedup.append(s)
    # 限制数量，防止信号过多拖慢后续
    return dedup[:_MAX_ENTRY_SIGNALS]


def detect_architecture(files: List[CodeFile]) -> ArchitectureProfile:
    """从文件列表静态探测架构画像。

    Args:
        files: List[CodeFile]（来自 project_analysis.files）

    Returns:
        ArchitectureProfile；任何失败都回退为默认 layered 画像。
    """
    profile = ArchitectureProfile()
    try:
        if not files:
            profile.evidence.append("未扫描到源码文件，按默认分层处理。")
            return profile

        # 1) 汇总各架构的文件命中数
        arch_hits: Dict[str, int] = {ARCH_WEB: 0, ARCH_EVENT: 0, ARCH_PLUGIN: 0}
        for f in files:
            for arch, hit in _scan_file(getattr(f, "raw_content", "") or "").items():
                arch_hits[arch] += hit

        # 2) 记录命中的框架/中间件关键词（用于报告展示）
        frameworks: List[str] = []
        for arch, hits in arch_hits.items():
            if hits > 0:
                frameworks.append(ARCH_LABELS[arch])
        profile.frameworks = frameworks

        total = len(files) or 1
        # 3) 综合判定架构类型（web/event/plugin 取命中文件数最高的；默认 layered）
        scored = [(a, c) for a, c in arch_hits.items() if c > 0]
        if scored:
            scored.sort(key=lambda x: x[1], reverse=True)
            best_arch, best_count = scored[0]
            # 置信度：命中文件数 / 总文件数，且至少抓到 1 个文件
            profile.confidence = round(min(_CONFIDENCE_CAP, best_count / total + _CONFIDENCE_FLOOR), 2)
            profile.arch_type = best_arch
            profile.evidence.append(
                f"检测到 {best_count}/{total} 个文件含 {ARCH_LABELS[best_arch]} 信号"
                f"（{ '、'.join(a for a, c in scored[:-1] if c > 0) or '无其他架构信号'}）。"
                if len(scored) >= 2 else
                f"检测到 {best_count}/{total} 个文件含 {ARCH_LABELS[best_arch]} 信号。"
            )
        else:
            profile.confidence = 0.0
            profile.arch_type = ARCH_LAYERED
            profile.evidence.append("未检测到框架/中间件信号，按分层/单体处理。")

        # 4) 提取入口信号
        profile.entry_signals = _extract_entry_signals(files)
        if profile.entry_signals:
            profile.evidence.append(
                f"提取到 {len(profile.entry_signals)} 个调用图之外的入口信号"
                f"（{len([s for s in profile.entry_signals if s.kind == 'web_route'])} 路由 / "
                f"{len([s for s in profile.entry_signals if s.kind == 'event_listener'])} 事件 / "
                f"{len([s for s in profile.entry_signals if s.kind == 'plugin_hook'])} 插件）。"
            )

        logger.info(f"[ArchDetector] 架构画像: {profile.label} "
                    f"(置信度 {profile.confidence:.2f}, 入口信号 {len(profile.entry_signals)})")
        return profile
    except Exception as e:
        logger.warning(f"[ArchDetector] 架构探测失败(回退默认): {e}")
        profile.arch_type = ARCH_LAYERED
        profile.confidence = 0.0
        profile.evidence.append("探测过程异常，按默认分层处理。")
        return profile