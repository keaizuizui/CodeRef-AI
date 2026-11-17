# -*- coding: utf-8 -*-
"""产物幂等回归：同一命令连续执行两次，产物应一致（除时间戳）。

判据：
1. 连续两次 memory(sync, full)：图谱 nodes/edges/CALLS 相同，节点集合对称差=0；
2. 连续两次生成 wiki 到同目录：md 文件集合相同，去掉 front matter 的
   generated_at / 时间戳行后逐文件内容完全一致；
3. 连续两次 report：index.html 去掉时间戳后，关键数字（图谱节点/边、记分卡
   各维度分、Wiki 篇数）完全一致；
4. 反向：改一个源文件后重跑，产物必须变化（证明不是缓存冻结）。
"""
import hashlib
import os
import re
import shutil
import sys
import tempfile
import unittest

PROJECT_ROOT = os.environ.get("CODEREF_SRC",
                              os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from core.memory_layer import MemoryLayer, _MEMORY_STATE_DIR  # noqa: E402
from core.code_knowledge_graph import CodeKnowledgeGraph  # noqa: E402
from core.code_analyzer import CodeAnalyzer  # noqa: E402
from core.path_key import project_key  # noqa: E402
from core.wiki_generator import WikiGenerator  # noqa: E402
from core.pipeline_runner import Pipe  # noqa: E402
from core.scorecard import compute, write_card  # noqa: E402

_SRC_TEMPLATE = '''"""示例服务模块 {i}"""


def handle_{i}(x):
    """处理入口 {i}"""
    return x * {i}


class Worker_{i}:
    """工作单元 {i}"""

    def run_{i}(self):
        return handle_{i}({i})
'''

_TS_PATTERNS = (
    (re.compile(r"^generated_at:.*$", re.M), "generated_at: <TS>"),
    (re.compile(r"^> 生成时间:.*$", re.M), "> 生成时间: <TS>"),
    (re.compile(r"\b\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}(?::\d{2})?\b"), "<TS>"),
)


def _disable_ollama():
    """本机 Ollama embed 端点 404（每次 3×2s 重试）；指向不可达回环让它快速
    判定不可用，走既有降级路径，避免拖慢用例。"""
    old = os.environ.get("OLLAMA_BASE_URL")
    os.environ["OLLAMA_BASE_URL"] = "http://127.0.0.1:1"
    return old


def _restore_ollama(old):
    if old is None:
        os.environ.pop("OLLAMA_BASE_URL", None)
    else:
        os.environ["OLLAMA_BASE_URL"] = old


def _write_project(root, count=4):
    os.makedirs(root, exist_ok=True)
    for i in range(count):
        with open(os.path.join(root, "svc%d.py" % i), "w", encoding="utf-8") as f:
            f.write(_SRC_TEMPLATE.format(i=i))
    return root


def _strip_ts(text):
    for pattern, repl in _TS_PATTERNS:
        text = pattern.sub(repl, text)
    return text


def _first_diff(a, b):
    """返回首个不一致行（可定位证据）。"""
    lines_a, lines_b = a.splitlines(), b.splitlines()
    for i in range(max(len(lines_a), len(lines_b))):
        la = lines_a[i] if i < len(lines_a) else "<EOF>"
        lb = lines_b[i] if i < len(lines_b) else "<EOF>"
        if la != lb:
            return "第 %d 行: %r != %r" % (i + 1, la[:160], lb[:160])
    return "（差异不可定位）"


class FakeLLM:
    """确定性伪 LLM：输出只由 prompt 指纹决定，避免网络与随机性。

    wiki 生成需要 LLM 可用（无 Key 会硬阻断），此替身让用例可离线验证
    「同样的输入 → 同样的产物」；指纹随 prompt 变化，可用于验证产物未冻结。
    """

    def __init__(self):
        self.calls = []

    def is_available(self):
        return True

    def reset_budget(self):
        return None

    def is_error_response(self, text):
        return False

    def chat_completion(self, messages, **kwargs):
        prompt = "\n".join(str(m.get("content", "")) for m in messages)
        self.calls.append(prompt)
        fp = hashlib.sha1(prompt.encode("utf-8")).hexdigest()[:16]
        return ("## 概述\n\n"
                "本模块提供确定性的处理能力，供调用方复用。\n\n"
                "## 关键实现\n\n"
                "- 入口函数负责基础校验与转换。\n"
                "- 工作单元封装调用流程。\n\n"
                # 末行必须以句末标点收尾：v5.21.22 起 _emit 会拦截
                # 「末行叙事半句」的文档（U-82），桩输出不闭合会被判截断。
                "指纹：" + fp + "。\n")


def _md_snapshot(root):
    """收集 md 文件 → 去时间戳后的全文（相对路径为键）。"""
    snap = {}
    for base, _dirs, files in os.walk(root):
        for fn in sorted(files):
            if not fn.endswith(".md"):
                continue
            fp = os.path.join(base, fn)
            with open(fp, "r", encoding="utf-8") as f:
                rel = os.path.relpath(fp, root).replace("\\", "/")
                snap[rel] = _strip_ts(f.read())
    return snap


def _graph_snapshot(project_path):
    kg = CodeKnowledgeGraph(project_path)
    try:
        kg._connect()
        stats = kg.get_stats()
        node_ids = {r[0] for r in kg._conn.execute("SELECT id FROM nodes")}
        calls = kg._conn.execute(
            "SELECT COUNT(*) FROM edges WHERE type='CALLS'").fetchone()[0]
        return {"nodes": int(stats.get("node_count") or 0),
                "edges": int(stats.get("edge_count") or 0),
                "calls": int(calls),
                "ids": node_ids}
    finally:
        kg.close()


def _report_signature(html):
    """从 index.html 提取关键数字（先去掉时间戳，避免误判）。"""
    html = _strip_ts(html)

    def _card(label):
        m = re.search(
            r'<div class="lbl">%s</div><div class="val"[^>]*>(.*?)</div>'
            % re.escape(label), html)
        return m.group(1).strip() if m else None

    cards = {lbl: _card(lbl) for lbl in
             ("扫描文件", "代码行", "HIGH", "MEDIUM", "LOW", "建议",
              "图谱节点", "图谱边")}
    overall = re.search(r'id="sc-overall">(.*?)</div>', html)
    dims = re.findall(
        r"<tr><td>([^<]+)</td><td>([^<]*)</td><td>"
        r"(<strong>[^<]+</strong>|<span[^>]*>[^<]*</span>)</td>", html)
    wiki = re.findall(r"Wiki 已生成，(\d+) 篇文档", html)
    return {"cards": cards,
            "overall": overall.group(1).strip() if overall else None,
            "dims": [list(d) for d in dims],
            "wiki_docs": wiki}


def _cleanup_project_caches(project_path):
    """回收用例在全局缓存目录留下的条目（不改产品逻辑）。"""
    try:
        key = project_key(project_path)
        h12 = hashlib.md5(key.encode("utf-8")).hexdigest()[:12]
        h8 = h12[:8]
        h32 = hashlib.md5(key.encode("utf-8")).hexdigest()
    except Exception:  # noqa: BLE001
        return
    try:
        names = os.listdir(_MEMORY_STATE_DIR)
    except OSError:
        names = []
    for fn in names:
        if fn.startswith(h12):
            try:
                os.remove(os.path.join(_MEMORY_STATE_DIR, fn))
            except OSError:
                pass
    try:
        cache_dir = CodeAnalyzer()._cache_dir
        names = os.listdir(cache_dir)
    except Exception:  # noqa: BLE001
        names, cache_dir = [], ""
    raw32 = hashlib.md5(os.path.abspath(str(project_path)).encode(
        "utf-8")).hexdigest()
    for fn in names:
        if fn.startswith(h32) or fn.startswith(raw32):
            try:
                os.remove(os.path.join(cache_dir, fn))
            except OSError:
                pass
    shutil.rmtree(os.path.join(PROJECT_ROOT, "cache", "wiki_cache", h8),
                  ignore_errors=True)


class _ProjectCase(unittest.TestCase):
    prefix = "coderef_idem_"
    file_count = 4

    @classmethod
    def setUpClass(cls):
        cls._old_ollama = _disable_ollama()
        cls.td = tempfile.mkdtemp(prefix=cls.prefix)
        cls.P = _write_project(os.path.join(cls.td, "proj"),
                               count=cls.file_count)

    @classmethod
    def tearDownClass(cls):
        _cleanup_project_caches(cls.P)
        shutil.rmtree(cls.td, ignore_errors=True)
        _restore_ollama(cls._old_ollama)


class MemorySyncIdempotencyTest(_ProjectCase):
    """连续两次 memory(sync, full) 图谱应完全一致。"""

    def test_sync_twice_same_graph(self):
        ml = MemoryLayer()
        r1 = ml.sync(self.P, mode="full")
        self.assertEqual(r1.get("status"), "ok", "首次 sync 失败: %r" % r1)
        s1 = _graph_snapshot(self.P)
        r2 = ml.sync(self.P, mode="full")
        self.assertEqual(r2.get("status"), "ok", "第二次 sync 失败: %r" % r2)
        s2 = _graph_snapshot(self.P)

        self.assertGreater(s1["nodes"], 0, "首次建图节点数为 0，判据无法成立")
        for key in ("nodes", "edges", "calls"):
            self.assertEqual(s1[key], s2[key],
                             "两次 sync 的 %s 不一致（图谱不稳定）: %r -> %r"
                             % (key, s1[key], s2[key]))
        sym = s1["ids"] ^ s2["ids"]
        self.assertFalse(
            sym, "两次建图节点集合对称差=%d（节点漂移）: %r"
                 % (len(sym), sorted(sym)[:5]))


class WikiGenerationIdempotencyTest(_ProjectCase):
    """连续两次生成 wiki 到同目录，md 内容（去时间戳）应一致。"""

    def setUp(self):
        self.out = os.path.join(self.P, "docs", "wiki")

    def _generate(self):
        gen = WikiGenerator(llm_client=FakeLLM())
        return gen.generate(self.P, output_dir=self.out, mode="full",
                            include_subprojects=False, cross_verify=False)

    def test_wiki_twice_same_md(self):
        r1 = self._generate()
        self.assertEqual(r1.errors, [], "首次生成 wiki 报错: %r" % (r1.errors,))
        snap1 = _md_snapshot(self.out)
        self.assertTrue(snap1, "首次生成未产出 md 文件: %s" % self.out)

        r2 = self._generate()
        self.assertEqual(r2.errors, [], "第二次生成 wiki 报错: %r" % (r2.errors,))
        snap2 = _md_snapshot(self.out)

        self.assertEqual(sorted(snap1), sorted(snap2),
                         "两次生成的 md 文件集合不同: 仅第一次 %r / 仅第二次 %r"
                         % (sorted(set(snap1) - set(snap2))[:5],
                            sorted(set(snap2) - set(snap1))[:5]))
        diff = [k for k in snap1 if snap1[k] != snap2[k]]
        detail = ("%s：%s" % (diff[0], _first_diff(snap1[diff[0]], snap2[diff[0]]))
                  if diff else "")
        self.assertEqual(diff, [],
                         "两次生成内容不一致（去时间戳后仍有差异）%d 个文件，首个 %s"
                         % (len(diff), detail))


class WikiSourceChangeTest(_ProjectCase):
    """反向：改源文件后重跑 wiki，产物必须变化。"""

    prefix = "coderef_idem_wiki_"

    def test_source_change_must_refresh_wiki(self):
        """反向：改源文件后重跑，产物必须变化（证明不是缓存冻结）。

        # TODO-U: wiki 生成复用 {CodeRef}/cache/wiki_cache/<hash>/stage1_metadata.json
        #          与 stage2_descriptions_*.json，且 mode=full 走
        #          _build_code_metadata(modules, project_path) 未传 skip_cache=True；
        #          源码改动后重跑，md 产物逐字节不变（缓存未随源码失效）。
        """
        out = os.path.join(self.P, "docs", "wiki")

        def _gen():
            return WikiGenerator(llm_client=FakeLLM()).generate(
                self.P, output_dir=out, mode="full",
                include_subprojects=False, cross_verify=False)

        r1 = _gen()
        self.assertEqual(r1.errors, [], "首次生成 wiki 报错: %r" % (r1.errors,))
        snap1 = _md_snapshot(out)

        with open(os.path.join(self.P, "svc0.py"), "a", encoding="utf-8") as f:
            f.write('\n\ndef added_after_change(y):\n'
                    '    """新增函数，用于验证产物不会冻结。"""\n'
                    '    return y + 100\n')

        r2 = _gen()
        self.assertEqual(r2.errors, [], "改动后生成 wiki 报错: %r" % (r2.errors,))
        snap2 = _md_snapshot(out)

        changed = [k for k in snap1 if snap1[k] != snap2.get(k)]
        self.assertTrue(
            changed,
            "源码已改动（新增 added_after_change），重跑 wiki 后 %d 个 md 文件"
            "内容全部不变（产物被缓存冻结）: %r"
            % (len(snap1), sorted(snap1)[:5]))


class ReportGenerationIdempotencyTest(_ProjectCase):
    """连续两次 report：index.html 去时间戳后关键数字应一致。"""

    file_count = 4

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.report_root = os.path.join(cls.P, ".coderef", "report")
        cls.html_dir = os.path.join(cls.report_root, "html")
        # 先用真实管线建图 + 审计，产出 findings 与图谱（report 重渲染的前提）
        MemoryLayer().sync(cls.P, mode="full")
        cls.audit = Pipe().audit(cls.P, output_dir=cls.report_root)
        # Wiki 产物让 index.html 出现「篇数」
        WikiGenerator(llm_client=FakeLLM()).generate(
            cls.P, output_dir=os.path.join(cls.P, "docs", "wiki"),
            mode="full", include_subprojects=False, cross_verify=False)
        cls._refresh_card()

    @classmethod
    def _refresh_card(cls):
        write_card(cls.P, compute(cls.P))

    def _render(self):
        r, ok = Pipe().render_report(self.P, output_dir=self.html_dir)
        self.assertTrue(ok, "render_report 失败: %r" % (getattr(r, "errors", []),))
        path = os.path.join(self.html_dir, "index.html")
        self.assertTrue(os.path.isfile(path), "index.html 未生成: %s" % path)
        with open(path, "r", encoding="utf-8") as f:
            return f.read()

    def test_report_twice_same_numbers(self):
        sig1 = _report_signature(self._render())
        sig2 = _report_signature(self._render())
        self.assertIsNotNone(sig1["cards"]["图谱节点"],
                             "index.html 缺少「图谱节点」卡片，判据无法成立")
        self.assertTrue(sig1["wiki_docs"],
                        "index.html 未出现 Wiki 篇数，判据无法成立: %r"
                        % (sig1["cards"],))
        self.assertTrue(sig1["dims"], "index.html 未渲染记分卡维度")
        self.assertEqual(sig1, sig2,
                         "两次 report 的关键数字不一致: %r"
                         % {k: (sig1[k], sig2[k]) for k in sig1
                            if sig1[k] != sig2[k]})

    def test_source_change_must_change_report(self):
        """反向：改源文件后重跑，报告关键数字必须变化（不是缓存冻结）。"""
        before = _report_signature(self._render())
        with open(os.path.join(self.P, "svc0.py"), "a", encoding="utf-8") as f:
            f.write('\n\ndef added_after_change(y):\n'
                    '    """新增函数，用于验证报告不会冻结。"""\n'
                    '    return y + 100\n')
        MemoryLayer().sync(self.P, mode="full")
        self._refresh_card()
        after = _report_signature(self._render())

        self.assertNotEqual(
            before["cards"]["图谱节点"], after["cards"]["图谱节点"],
            "源码新增函数后图谱节点数未变化（报告被缓存冻结）: %r"
            % (before["cards"],))
        self.assertNotEqual(before, after,
                            "源码改动后 index.html 关键数字完全未变（产物冻结）")


if __name__ == "__main__":
    unittest.main(verbosity=2)
