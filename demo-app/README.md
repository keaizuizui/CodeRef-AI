# demo-app 前端交互审查测试实例

一个轻量 Flask 前端测试实例，用于验证"前端交互审查"功能能否命中预埋的交互问题。

## 启动方式

```bash
# 1. （可选）安装依赖
pip install flask

# 2. 启动
python app.py

# 3. 浏览器访问
http://127.0.0.1:5000/
```

> 说明：若环境未安装 flask，`app.py` 仍可正常导入（含降级 fallback），不影响语法/导入检查。

## 目录结构

```
demo-app/
├── app.py              # Flask 路由：/、/settings、/logout
├── static/
│   ├── index.html      # 单页 HTML（含 5 级菜单树、表单、按钮）
│   └── app.js          # 事件绑定、路由切换、模拟接口请求
├── templates/
│   └── base.html       # 基础模板（由 Flask 渲染）
└── README.md
```

## 预埋问题清单（供审查工具命中）

| #   | 问题类型                         | 位置 |
|-----|----------------------------------|------|
| 1   | 删除按钮无二次确认直接提交        | `app.js` 的 `deleteItem()`；`base.html` / `index.html` 中 `onclick="deleteItem(1)"` |
| 2   | 提交按钮无 loading / 无禁用态      | `app.js` 的 `submitForm()`；`base.html` / `index.html` 中 `onclick="submitForm()"` |
| 3   | L3 级菜单死链（href="#" 无处理）   | `base.html` / `index.html` 中 `<li><a class="dead" href="#">L3 / 死链项</a></li>` |
| 4   | L5 级菜单无返回/面包屑路径        | `base.html` / `index.html` 中 L4「角色权限」下的两个 L5 叶子节点（查看权限 / 编辑权限），无返回链接 |
| 5   | 接口失败时前端无任何提示          | `app.js` 的 `loadData()` 中空的 `.catch()`；`fetchDetail()` 中 `.catch` 仅 `console.log` |
| 6   | 两个"保存"按钮交互风格不一致      | `base.html` / `index.html` 中 `<button onclick="saveA()">`（带确认）与 `<a class="btn" href="#" onclick="saveB()">`（无确认） |

## 设计说明

- HTML 标签文本均为中文。
- 所有文件 UTF-8 编码。
- 菜单树为真正的 5 级嵌套 `<ul><li>` 结构，每级含 label 与可选 href，L5 至少 2 个节点。
- 整体刻意保持"vibe coding"的简单风格，不做过度工程。