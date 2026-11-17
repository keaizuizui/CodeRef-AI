# -*- coding: utf-8 -*-
"""缺陷记录 U-40 · workflow_graph 链路接入白名单 dir 排除（备份目录不入图）。

开发方 v5.13.6 回报回归用例清单（tests 归属约定，独立回归落位 coderef-src）：
- test_drops_backup_dir_nodes_and_edges — 白名单 dir 排除生效，备份目录节点/边从 workflow_graph 移除
- test_no_whitelist_keeps_backup_nodes — 无白名单时备份节点保留（基线对照）
- test_whitelist_load_failure_propagates — 白名单加载失败向上传播（CodeRabbit major，fail-open 会泄备份入图）

与 U-39 图谱层排除（MemoryLayerKgExcludeTest）互补：本类验证调用图产物层。

运行：python -m unittest tests.test_workflow_graph_exclude -v
"""
import os
import sys
import tempfile
import unittest
from unittest import mock

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)


class WorkflowGraphExcludeTest(unittest.TestCase):

    def test_drops_backup_dir_nodes_and_edges(self):
        from core.workflow_graph import WorkflowGraph
        wg = WorkflowGraph()
        with tempfile.TemporaryDirectory() as tmp:
            pp = os.path.join(tmp, "prj")
            core_dir = os.path.join(pp, "core")
            back_dir = os.path.join(pp, "_refactor_backup")
            os.makedirs(core_dir, exist_ok=True)
            os.makedirs(back_dir, exist_ok=True)
            nodes = [
                {"id": "core_main", "filePath": os.path.join(core_dir, "main.py")},
                {"id": "backup_x", "filePath": os.path.join(back_dir, "x.py")},
                {"id": "core_util", "filePath": os.path.join(core_dir, "util.py")},
            ]
            edges = [{"from": "core_main", "to": "backup_x"},
                     {"from": "core_main", "to": "core_util"}]
            from core.pipeline_runner import whitelist_add, whitelist_clear
            whitelist_clear(pp)
            # 真实形态白名单条目（无 rule 字段，以 dir 字段标志）
            whitelist_add(pp, [{"dir": "_refactor_backup"}])
            try:
                kept_nodes, kept_edges = wg._apply_whitelist_exclude(
                    pp, nodes, edges)
            finally:
                whitelist_clear(pp)
            ids = {n["id"] for n in kept_nodes}
            self.assertIn("core_main", ids)
            self.assertIn("core_util", ids)
            self.assertNotIn("backup_x", ids)
            self.assertEqual(kept_edges,
                             [{"from": "core_main", "to": "core_util"}])

    def test_no_whitelist_keeps_backup_nodes(self):
        from core.workflow_graph import WorkflowGraph
        wg = WorkflowGraph()
        with tempfile.TemporaryDirectory() as tmp:
            pp = os.path.join(tmp, "prj")
            back_dir = os.path.join(pp, "_refactor_backup")
            os.makedirs(back_dir, exist_ok=True)
            nodes = [{"id": "b", "filePath": os.path.join(back_dir, "x.py")}]
            edges = []
            from core.pipeline_runner import whitelist_clear
            whitelist_clear(pp)
            kept_nodes, kept_edges = wg._apply_whitelist_exclude(pp, nodes, edges)
        self.assertEqual(len(kept_nodes), 1)
        self.assertEqual(kept_edges, [])

    def test_whitelist_load_failure_propagates(self):
        from core.workflow_graph import WorkflowGraph
        wg = WorkflowGraph()
        with tempfile.TemporaryDirectory() as tmp:
            pp = os.path.join(tmp, "prj")
            os.makedirs(pp, exist_ok=True)
            nodes = [{"id": "a", "filePath": os.path.join(pp, "a.py")}]
            with mock.patch("core.pipeline_runner.whitelist_list",
                            side_effect=RuntimeError("wl broken")):
                with self.assertRaises(RuntimeError):
                    wg._apply_whitelist_exclude(pp, nodes, [])


if __name__ == "__main__":
    unittest.main(verbosity=2)