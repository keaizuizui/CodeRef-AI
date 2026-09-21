# -*- coding: utf-8 -*-
"""
make_fixture.py — 生成前端联调用真实样本数据 fixtures/vimax.json。

直接复用 core.arch_view.build_arch_data（v5.16 契约：framework + modules_graph + summary），
保证开发期前端数据形状与生产一致。生产由 coderef_arch_canvas 注入。
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
sys.path.insert(0, ROOT)

from core.arch_view import build_arch_data

VIMAX = r"D:\CodeRef-GoodJob\Coderef-Test\coderef-positive-test\samples\workspace\vimax"
OUT = os.path.join(HERE, "..", "src", "fixtures", "vimax.json")


def main() -> None:
    data = build_arch_data(VIMAX)
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    fw = data["framework"]
    print(f"fixture written: {OUT}")
    print(f"framework: 入口模块={len(fw['modules'])} 主功能={len(fw['children'])}")
    print(f"modules_graph: roles={len(data['modules_graph']['roles'])} "
          f"modules={len(data['modules_graph']['modules'])}")


if __name__ == "__main__":
    main()
