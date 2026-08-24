# -*- coding: utf-8 -*-
"""
GovSchedule v1.1 —— CodeRef 5.2 定时体检实跑落地

延续 5.1 决策：MCP 工具不做后台定时，只产出供外部 cron / CI 消费的
触发片段，并做"离期检查"（周期已过 end_date 却未收尾 → 提醒）。

5.2 升级：从"产出 cron/CI 片段"到"真实可触发的调度入口"——
render 额外生成一个可直接运行的入口脚本 run_cycle.py（调用
HealthCycle.start_cycle 开新周期 + 导入差距 + 产出报告 JSON/HTML），
供 cron/CI/人工直接 `python run_cycle.py --project <path> --name <cycle>`
调用；`--check` 离期检查仅报告不建档。
"""

import os
import textwrap
from datetime import date, datetime
from typing import Any, Dict, List

from loguru import logger

# run_cycle.py 模板：生成到 <project>/.coderef/run_cycle.py，供 cron/CI/人工直接调用。
# 用 textwrap.dedent + 占位符注入，避免手写转义；脚本自身零外部依赖（仅标准库）。
_RUN_CYCLE_TEMPLATE = textwrap.dedent("""\
    # -*- coding: utf-8 -*-
    \"\"\"CodeRef 5.2 定时体检触发入口（由 coderef_gov_schedule 生成）。

    用法:
      python run_cycle.py --project <path> [--name <cycle>] [--desc <描述>] [--end <YYYY-MM-DD>] [--out <dir>]
      python run_cycle.py --project <path> --check          # 离期检查，仅报告不建档

    可被 cron / CI / 任务计划直接调用；CodeRef 本身不内置后台驻留。
    \"\"\"

    import argparse
    import json
    import os
    import sys
    from datetime import date, datetime


    def _load_target_arch(project_path):
        p = os.path.join(os.path.abspath(project_path), ".coderef", "target_arch.json")
        if not os.path.isfile(p):
            return None
        try:
            with open(p, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return None


    def main():
        ap = argparse.ArgumentParser(description="CodeRef 定时体检触发入口")
        ap.add_argument("--project", required=True, help="目标项目路径")
        ap.add_argument("--name", default="", help="体检周期名（缺省按日期）")
        ap.add_argument("--desc", default="", help="周期描述")
        ap.add_argument("--end", default="", help="截止日期 YYYY-MM-DD")
        ap.add_argument("--out", default="", help="报告输出目录（缺省 <project>/.coderef/）")
        ap.add_argument("--check", action="store_true", help="离期检查，仅报告不建档")
        args = ap.parse_args()

        # 定位 CodeRef 源码根：生成时注入（见 _SRC_ROOT 占位符），
        # 不依赖被检项目结构。
        _src_root = __SRC_ROOT__
        sys.path.insert(0, _src_root)

        from core.healthcycle import HealthCycle

        hc = HealthCycle(args.project)

        if args.check:
            cycles = hc.store.list_cycles()
            hc.store.close()
            overdue = []
            today = date.today()
            for cyc in cycles:
                if cyc.get("status") != "open":
                    continue
                end = cyc.get("end_date") or ""
                try:
                    ed = datetime.strptime(end, "%Y-%m-%d").date()
                except Exception:
                    continue
                if ed < today:
                    overdue.append({"id": cyc["id"], "name": cyc["name"],
                                    "end_date": end,
                                    "hint": "已过截止日仍未收尾，建议复检并 coderef_gov_close"})
            print(json.dumps({"ok": True, "mode": "check", "overdue_cycles": overdue},
                             ensure_ascii=False, indent=2))
            return 0 if not overdue else 2

        r = hc.start_cycle(name=args.name, description=args.desc,
                           end_date=args.end,
                           target_arch=_load_target_arch(args.project))
        if not r.get("ok"):
            print(json.dumps({"ok": False, "message": r.get("message", "体检失败")},
                             ensure_ascii=False, indent=2))
            return 1

        out_dir = args.out or os.path.join(os.path.abspath(args.project), ".coderef")
        os.makedirs(out_dir, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        json_path = os.path.join(out_dir, f"cycle_{r['cycle']['id']}_{ts}.json")
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(r, f, ensure_ascii=False, indent=2)

        try:
            from core.gov_dashboard import render_report
            html = render_report(args.project, output_dir=out_dir, cid=r["cycle"]["id"])
            html_path = html.get("report_html", "")
        except Exception:
            html_path = ""

        print(json.dumps({
            "ok": True, "mode": "cycle", "cycle": r["cycle"],
            "imported": {k: r.get(k) for k in ("new", "kept", "recurred", "reactivated", "skipped")},
            "report_json": json_path, "report_html": html_path,
        }, ensure_ascii=False, indent=2))
        return 0


    if __name__ == "__main__":
        sys.exit(main())
""")


def _parse_date(s: str):
    for fmt in ("%Y-%m-%d", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(s, fmt).date()
        except Exception:  # noqa: BLE001
            continue
    return None


def render(project_path: str, cron_expr: str = "0 6 * * 1",
           command: str = "coderef_scan",
           output_dir: str = "") -> Dict[str, Any]:
    """生成 cron / CI 触发片段 + 可运行触发脚本 + 离期检查。

    Args:
        project_path: 治理项目路径。
        cron_expr: 建议触发时刻（5 段标准 cron；默认每周一 06:00）。
        command: 每次体检触发的 MCP 动作（默认 coderef_scan，也可用
                 coderef_gov_start / coderef_arch_gap）。
        output_dir: 触发脚本输出目录（缺省 <project>/.coderef/）。

    Returns:
        片段 + run_cycle.py 路径 + 触发命令 + 离期检查信息。
    """
    from core.healthcycle import HealthCycle
    hc = HealthCycle(project_path)
    cycles = hc.store.list_cycles()
    hc.store.close()

    overdue = []
    open_cnt = 0
    for cyc in cycles:
        if cyc.get("status") == "open":
            open_cnt += 1
            end = cyc.get("end_date") or ""
            ed = _parse_date(end)
            if ed and ed < date.today():
                overdue.append({"id": cyc["id"], "name": cyc["name"],
                                "end_date": end,
                                "hint": "已过截止日仍未收尾，建议复检并 "
                                        "coderef_gov_close"})
    last_closed = next((c for c in reversed(cycles)
                        if c.get("status") == "closed"), None)

    # —— 写出可运行触发脚本 run_cycle.py ——
    out_dir = output_dir or os.path.join(os.path.abspath(project_path), ".coderef")
    os.makedirs(out_dir, exist_ok=True)
    script_path = os.path.join(out_dir, "run_cycle.py")
    # 源码根：core/gov_schedule.py 的上上级目录（本模块所在包）
    src_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    try:
        with open(script_path, "w", encoding="utf-8", newline="\n") as f:
            f.write(_RUN_CYCLE_TEMPLATE.replace("__SRC_ROOT__",
                                                repr(src_root)))
        script_ok = True
    except Exception as e:  # noqa: BLE001
        logger.warning(f"写出 run_cycle.py 失败: {e}")
        script_path, script_ok = "", False

    run_cmd = (f"python {script_path} --project {project_path} "
               f"--name \"体检 $(date +%F)\"" if script_ok else "")

    cron_block = f"""# CodeRef 5.2 定期体检调度片段（由 coderef_gov_schedule 生成）
# 项目: {project_path}
# 周期: 每周一次（默认周一 06:00，可按需改 cron_expr）
# 触发入口: {script_path or '（脚本写出失败）'}
{cron_expr}  {run_cmd or f"cd {project_path} && coderef scan {command} --project {project_path}"}
# 离期检查（仅报告不建档，可单独调度）:
#   python {script_path} --project {project_path} --check
# 触发后建议动作:
#   coderef_gov_report  查看跨期趋势，确认是否需要补治理"""

    return {
        "ok": True,
        "tool": "coderef_gov_schedule",
        "project_path": project_path,
        "cron_expression": cron_expr,
        "cycle_counts": {"total": len(cycles), "open": open_cnt},
        "overdue_cycles": overdue,
        "ran_builtin_timer": False,  # 明确：不内置后台定时
        "runnable_script": script_path,
        "script_written": script_ok,
        "run_command": run_cmd,
        "check_command": (f"python {script_path} --project {project_path} --check"
                          if script_ok else ""),
        "cron_block": cron_block,
        "ci_note": f"建议在 cron/CI 外层触发，CodeRef 本身不做后台定时（{date.today()}）。",
        "last_closed_cycle": last_closed,
    }
