# -*- coding: utf-8 -*-
"""覆盖率监控（开发/CI 用，非测试文件）。

前置：pip install -r requirements-dev.txt（pytest-cov）
用法：
    python tools/coverage_report.py            # 跑全量测试 + 覆盖率
    python tools/coverage_report.py --html     # 额外生成 HTML 报告
退出码：0=覆盖率达标；1=低于 fail_under 阈值或测试失败

测试目录自动探测（2026-09-18 起，测试归属约定变更）：
  1) 本地 tests/（开发方自持，优先）
  2) 测试方独立仓 ..\\独立测试\\tests（coderef-src 副本无本地 tests 时回退）
"""
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _locate_tests() -> str:
    """返回可用的测试目录（绝对路径）；均不存在时返回本地 tests（报错清晰）。"""
    local = os.path.join(ROOT, "tests")
    if os.path.isdir(local):
        return local
    fallback = os.path.join(ROOT, "..", "独立测试", "tests")
    if os.path.isdir(fallback):
        return fallback
    return local


def main() -> int:
    args = sys.argv[1:]
    tests_dir = _locate_tests()
    cmd = [
        sys.executable, "-m", "pytest",
        tests_dir, "-q",
        "--cov=core", "--cov-branch",
        "--cov-report=term-missing",
        # fail_under 阈值从 pyproject.toml [tool.coverage.report] 读取，此处不硬编码
    ]
    if "--html" in args:
        cmd.append("--cov-report=html:coderef-report/coverage")
    print(f"运行: {' '.join(cmd)}")
    r = subprocess.run(cmd, cwd=ROOT)
    return r.returncode


if __name__ == "__main__":
    sys.exit(main())
