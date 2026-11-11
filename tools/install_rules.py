#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""把 CodeRef 记忆契约安装到编辑器规则文件（AGENTS.md / CLAUDE.md / cursor / copilot）。

用法：
  python tools/install_rules.py --project <路径>            # 项目级（默认）
  python tools/install_rules.py --project <路径> --include-global
  python tools/install_rules.py --project <路径> --status
  python tools/install_rules.py --project <路径> --uninstall
  python tools/install_rules.py --project <路径> --dry-run
"""

import argparse
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from core import setup_rules  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description="安装 CodeRef 记忆契约")
    ap.add_argument("--project", default=os.getcwd(), help="项目路径")
    ap.add_argument("--targets", choices=["project", "global", "all"],
                    default="project", help="安装目标")
    ap.add_argument("--include-global", action="store_true",
                    help="同时写用户级规则（~/.claude / ~/.cursor / ~/.codex）")
    ap.add_argument("--dry-run", action="store_true", help="只预览不落盘")
    ap.add_argument("--no-create", action="store_true", help="不新建缺失的 AGENTS.md")
    ap.add_argument("--uninstall", action="store_true", help="只删除 marker 块")
    ap.add_argument("--status", action="store_true", help="查看接入状态")
    a = ap.parse_args()

    if a.status:
        r = setup_rules.status(a.project, include_global=a.include_global)
    elif a.uninstall:
        r = setup_rules.uninstall(a.project, include_global=a.include_global,
                                  dry_run=a.dry_run)
    else:
        r = setup_rules.install(a.project, targets=a.targets,
                                include_global=a.include_global,
                                dry_run=a.dry_run,
                                create_missing=not a.no_create)
    print(json.dumps(r, ensure_ascii=False, indent=2))
    if r.get("status") == "ok" and r.get("written"):
        print("\n提示：规则写在编辑器启动时加载的文件里；请新建一个对话后再验证。")
        print("已开工的旧会话请粘贴：" + setup_rules.activation_line())
    return 0 if r.get("status") == "ok" else 1


if __name__ == "__main__":
    sys.exit(main())
