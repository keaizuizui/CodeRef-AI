# -*- coding: utf-8 -*-
"""CodeRef 配置辅助 CLI（setup.bat 安全调用入口）

setup.bat 的菜单值经 sys.argv 传入（作为数据而非代码），避免把用户输入
内插进 `python -c` 源码造成命令注入（如设计名 `x');os.system('calc');#`）。
子命令：
  registry list
  registry add <project> <name> [desc]
  registry alias <project> <canonical> <alias>
  registry delete <project> <name>
  board serve <project>
  canvas generate <project>
"""

import sys


def _registry(argv):
    if not argv:
        print("registry 需要子命令: list / add / alias / delete")
        return 2
    from core.design_registry import DesignRegistry
    r = DesignRegistry()
    cmd = argv[0]
    try:
        if cmd == "list":
            items = r.list_designs()
            print(f"共 {len(items)} 条设计:")
            for d in items:
                src = d.get("source_project") or "(预置)"
                print(f"  - {d.get('canonical', '')} ({d.get('category', '')}) | 来源: {src}")
            return 0
        if cmd == "add":
            if len(argv) < 3:
                print("add 需要: <project> <name> [desc]")
                return 2
            r.manage(project_path=argv[1], action="add", name=argv[2],
                     description=argv[3] if len(argv) > 3 else "")
            print(f"[OK] 已新增设计: {argv[2]}")
            return 0
        if cmd == "alias":
            if len(argv) < 4:
                print("alias 需要: <project> <canonical> <alias>")
                return 2
            r.manage(project_path=argv[1], action="alias",
                     canonical=argv[2], alias=argv[3])
            print(f"[OK] 别名已添加: {argv[3]}")
            return 0
        if cmd == "delete":
            if len(argv) < 3:
                print("delete 需要: <project> <name>")
                return 2
            r.manage(project_path=argv[1], action="delete", name=argv[2])
            print(f"[OK] 已删除设计: {argv[2]}")
            return 0
    except ValueError as e:
        print(f"[错误] {e}")
        return 1
    print(f"未知 registry 子命令: {cmd}")
    return 2


def _board(argv):
    if len(argv) < 2 or argv[0] != "serve":
        print("board 需要: serve <project>")
        return 2
    from core.gov_webdash import serve
    r = serve(argv[1])
    print("治理看板已启动:")
    print("  项目: " + argv[1])
    print("  访问地址: " + r["url"])
    print("按回车停止服务...")
    input()
    return 0


def _canvas(argv):
    if len(argv) < 2 or argv[0] != "generate":
        print("canvas 需要: generate <project>")
        return 2
    from core.canvas_generator import ArchCanvas
    try:
        fp = ArchCanvas().generate(project_path=argv[1])
    except ValueError as e:
        print(f"[错误] {e}")
        return 1
    print(fp)
    return 0


def main(argv=None):
    args = argv if argv is not None else sys.argv[1:]
    if not args:
        print(__doc__)
        return 0
    cmd, rest = args[0], args[1:]
    if cmd == "registry":
        return _registry(rest)
    if cmd == "board":
        return _board(rest)
    if cmd == "canvas":
        return _canvas(rest)
    print(f"未知命令: {cmd}")
    return 2


if __name__ == "__main__":
    sys.exit(main())
