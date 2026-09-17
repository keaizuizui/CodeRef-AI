# -*- coding: utf-8 -*-
"""版本一致性校验（发布前必跑）：核心/core/version.py 与所有引用处比对。

背景：v5.14.3 起版本号收敛为单一真源 core/version.py。
功能性引用（__init__ / pyproject dynamic / mcp_server）已自动跟随；
文档性快照（README 版本行 / MCP_SETUP 架构图 / CHANGELOG 最新标题）
是 Markdown 静态文本，无法自动更新，本脚本在发布前兜底校验防漂移。

用法：python tools/check_version.py
退出码：0=一致；1=漂移（输出差异清单）
"""
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


def main() -> int:
    from core.version import __version__ as truth

    issues = []
    checks = [
        ("README.md", rf"Version\s+{re.escape(truth)}", "README 版本行"),
        ("MCP_SETUP.md", rf"v{re.escape(truth)}", "MCP_SETUP 架构图版本"),
    ]
    for rel, pattern, label in checks:
        path = os.path.join(ROOT, rel)
        text = open(path, encoding="utf-8").read()
        if not re.search(pattern, text):
            issues.append(f"{label} 未同步到 {truth}（{rel}）")

    # CHANGELOG 最新区块标题必须含当前版本（取首个 "### v..." 标题直接比对）
    changelog = os.path.join(ROOT, "docs", "changelog", "CHANGELOG.md")
    head = open(changelog, encoding="utf-8").read()
    latest = re.search(r"^### v(?P<version>\S+)", head, re.MULTILINE)
    if latest is None or latest.group("version") != truth:
        found = latest.group("version") if latest else "（无标题）"
        issues.append(f"CHANGELOG 最新区块标题版本 {found} ≠ 真源 {truth}")

    if issues:
        print("版本漂移检测失败：")
        for i in issues:
            print(f"  - {i}")
        print(f"提示：真源为 core/version.py（当前 {truth}），请同步上述文档后重跑。")
        return 1
    print(f"OK：版本一致（core/version.py == {truth}），README/MCP_SETUP/CHANGELOG 均已同步。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
