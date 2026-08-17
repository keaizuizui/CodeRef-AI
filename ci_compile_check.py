# -*- coding: utf-8 -*-
"""CI 编译检查（v4.9.2 新增）—— 轻量、零第三方依赖的快速门禁。

做什么：
  1. 编译校验全部核心 Python 文件（语法无误）
  2. 校验 requirements.txt 与 pyproject.toml 的依赖清单双向一致（PEP 503 归一化）
  3. 计数裸 except 与 print()（作为可读性趋势，不失败门禁）

用法:
    <py> ci_compile_check.py [项目根路径]
退出码: 0 = 通过；1 = 失败（编译错 / 依赖清单漂移）

配套 GitHub Actions 见 .github/workflows/compile-check.yml。
"""
import ast
import os
import re
import sys
from pathlib import Path

try:
    import tomllib  # Python 3.11+
except ImportError:  # Python 3.10：回退到第三方 tomli
    import tomli as tomllib  # type: ignore[no-redef]


def walk_py_files(root: Path):
    for dirpath, dirnames, filenames in os.walk(root):
        # 跳过缓存 / 虚拟环境 / 沙箱样例，避免误扫
        dirnames[:] = [d for d in dirnames
                       if d not in (".git", "__pycache__", ".venv", "venv",
                                    "node_modules", "samples", "tests",
                                    "workspace", "cache", "data")]
        for fn in filenames:
            if fn.endswith(".py"):
                yield Path(dirpath) / fn


def compile_check(root: Path):
    errors = []
    count = 0
    for p in walk_py_files(root):
        count += 1
        try:
            ast.parse(p.read_text(encoding="utf-8"))
        except SyntaxError as e:
            errors.append(f"{p.relative_to(root)}:{e.lineno}: {e.msg}")
        except (UnicodeDecodeError, ValueError, OSError) as e:
            errors.append(f"{p.relative_to(root)}: 无法解析 ({type(e).__name__}: {e})")
    return count, errors


def count_bare_except(root: Path):
    bare, prints = 0, 0
    for p in walk_py_files(root):
        try:
            tree = ast.parse(p.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError, ValueError, OSError):
            continue
        for n in ast.walk(tree):
            if isinstance(n, ast.ExceptHandler) and n.type is None:
                bare += 1
            if isinstance(n, ast.Call) and getattr(n.func, "id", "") == "print":
                prints += 1
    return bare, prints


def _norm(name: str) -> str:
    """PEP 503 归一化：下划线/连字符/点视为等距，统一小写。"""
    return name.replace("_", "-").replace(".", "-").casefold()


def _req_name(line: str):
    """从 requirements 单行提取规范化包名；跳过注释、选项/r-文件等指令行。"""
    seg = line.split("#")[0].strip()
    if not seg or seg.startswith(("-r", "-e", "-i", "--index-url", "--extra-index-url")):
        return None
    m = re.match(r"^([A-Za-z0-9_\-\.]+)", seg)
    return _norm(m.group(1)) if m else None


def deps_req(paths_from_req, deps):
    """把 requirements 与 pyproject deps 做双向子集比对，返回 (req 缺, py 独有)。"""
    req_names = {n for ln in paths_from_req if (n := _req_name(ln))}
    py_names = set()
    for d in deps:
        m = re.match(r"^([A-Za-z0-9_\-\.]+)", d.strip())
        if m:
            py_names.add(_norm(m.group(1)))
    return req_names - py_names, py_names - req_names


def main() -> int:
    root = Path(sys.argv[1] if len(sys.argv) > 1 else os.getcwd()).resolve()
    print(f"[CI] 项目根: {root}")

    n_files, errs = compile_check(root)
    print(f"[CI] 编译校验: {n_files} 个 .py，错误 {len(errs)}")
    for e in errs:
        print(f"  ERR  {e}")

    # 依赖一致性
    req_path = root / "requirements.txt"
    pyproject_path = root / "pyproject.toml"
    missing = set()
    if req_path.exists() and pyproject_path.exists():
        req_lines = req_path.read_text(encoding="utf-8").splitlines()
        with pyproject_path.open("rb") as f:
            pp = tomllib.load(f)
        project = pp.get("project", {})
        deps = list(project.get("dependencies", []))
        for extra in project.get("optional-dependencies", {}).values():
            deps.extend(extra or [])
        missing, extra = deps_req(req_lines, deps)
        print(f"[CI] 依赖一致性: requirements vs pyproject，缺失 {sorted(missing)}，py 独有(可选) {sorted(extra)}")
    else:
        print("[CI] 跳过依赖一致性（缺 requirements.txt 或 pyproject.toml）")

    bare, prints = count_bare_except(root)
    print(f"[CI] 趋势统计: 裸 except={bare}, print()={prints}")

    if errs:
        print("COMPILE_CHECK=FAIL")
        return 1
    if missing:
        print("COMPILE_CHECK=FAIL (依赖清单漂移)")
        return 1
    print("COMPILE_CHECK=PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())