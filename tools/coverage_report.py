# -*- coding: utf-8 -*-
"""覆盖率监控（开发/CI 用，非测试文件）。

前置：pip install -r requirements-dev.txt（pytest-cov）
用法：
    python tools/coverage_report.py            # 跑全量测试 + 覆盖率
    python tools/coverage_report.py --html     # 额外生成 HTML 报告
退出码：0=覆盖率达标；1=低于 fail_under 阈值或测试失败
"""
import subprocess
import sys


def main() -> int:
    args = sys.argv[1:]
    cmd = [
        sys.executable, "-m", "pytest",
        "tests", "-q",
        "--cov=core", "--cov-branch",
        "--cov-report=term-missing",
        # fail_under 阈值从 pyproject.toml [tool.coverage.report] 读取，此处不硬编码
    ]
    if "--html" in args:
        cmd.append("--cov-report=html:coderef-report/coverage")
    print(f"运行: {' '.join(cmd)}")
    r = subprocess.run(cmd)
    return r.returncode


if __name__ == "__main__":
    sys.exit(main())
