# -*- coding: utf-8 -*-
"""覆盖率监控（开发/CI 用，非测试文件）。

前置：pip install -r requirements-dev.txt（pytest-cov）
用法：
    python tools/coverage_report.py            # 跑全量测试 + 覆盖率
    python tools/coverage_report.py --html     # 额外生成 HTML 报告
退出码：0=覆盖率达标；1=低于 fail_under 阈值或测试失败

测试目录探测：
  1) 环境变量 CODEREF_TESTS_DIR 指定的目录（可选）
  2) 本地 tests/
"""
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _locate_tests() -> str:
    """返回可用的测试目录（绝对路径）；优先环境变量，其次本地 tests/。"""
    env = os.environ.get("CODEREF_TESTS_DIR", "").strip()
    if env and os.path.isdir(env):
        return os.path.abspath(env)
    return os.path.join(ROOT, "tests")


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
