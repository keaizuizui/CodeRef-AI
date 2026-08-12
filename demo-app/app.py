# -*- coding: utf-8 -*-
"""demo-app 轻量 Flask 前端测试实例。

用于验证前端交互审查功能能否命中预埋的 6 类交互问题。
运行：python app.py  ->  http://127.0.0.1:5000/
"""
try:
    from flask import Flask, render_template, redirect, url_for
except ImportError:
    # 环境中未安装 flask 时，仅用于语法/导入检查（不抛异常）。
    import sys

    def render_template(*args, **kwargs):
        return "<html><body>fallback (flask 未安装)</body></html>"

    def redirect(*args, **kwargs):
        return "/"

    def url_for(*args, **kwargs):
        return "/"

    class Flask:
        def __init__(self, name):
            self.name = name

        def route(self, path, **opts):
            def deco(fn):
                return fn
            return deco

        def run(self, *args, **kwargs):
            print("flask 未安装，跳过启动。")

app = Flask(__name__)


@app.route("/")
def index():
    """首页：渲染基础模板。"""
    return render_template("base.html")


@app.route("/settings")
def settings():
    """设置页：简化处理，复用基础模板。"""
    return render_template("base.html")


@app.route("/logout")
def logout():
    """登出：直接跳回首页。"""
    return redirect(url_for("index"))


if __name__ == "__main__":
    import os
    app.run(host="127.0.0.1", port=5000, debug=os.environ.get("DEMO_DEBUG") == "1")