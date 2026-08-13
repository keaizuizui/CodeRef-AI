// app.js —— 演示用，故意埋入若干交互问题供审查工具命中。
// 预埋问题：1) 删除无确认  2) 提交无 loading/禁用  5) 接口失败无提示  6) 保存风格不一致

// ===== 路由切换（简单 SPA 切换标题）=====
function goPage(name) {
  document.getElementById("pageTitle").innerText = name;
}

// ===== 问题1：删除按钮无二次确认 =====
// 直接发起删除请求，没有任何 confirm / window.confirm 保护。
function deleteItem(id) {
  fetch("/api/delete", {
    method: "POST",
    body: "id=" + id
  });
  alert("已删除 " + id);
  loadData();
}

// ===== 问题2：提交按钮无 loading / 无禁用态 =====
// 点击后不禁用按钮、不显示 loading，可被反复点击造成重复提交。
function submitForm() {
  var input = document.querySelector('#mainForm input[name="name"]');
  var name = input ? input.value : "";
  fetch("/api/submit", {
    method: "POST",
    body: "name=" + name
  });
  alert("已提交：" + name);
}

// ===== 问题6a：保存（按钮）带二次确认 =====
function saveA() {
  if (window.confirm("确定保存吗？")) {
    alert("按钮保存成功");
  }
}

// ===== 问题6b：保存（链接）无确认，直接保存 =====
// 与上面的"保存"按钮风格不一致：一个确认一个直接执行。
function saveB() {
  alert("链接保存成功");
}

// ===== 加载数据列表 =====
function loadData() {
  fetch("/api/data")
    .then(function (res) { return res.json(); })
    .then(function (data) {
      var list = document.getElementById("dataList");
      if (!list) { return; }
      list.innerHTML = "";
      data.forEach(function (item) {
        var li = document.createElement("li");
        li.innerText = item;
        list.appendChild(li);
      });
    })
    // 问题5：接口失败时前端无任何提示，catch 为空。
    .catch(function () {
      // 什么都不做，用户看不到任何错误提示。
    });
}

// ===== 问题5：接口失败仅 console.log =====
function fetchDetail(id) {
  fetch("/api/detail?id=" + id)
    .then(function (res) { return res.json(); })
    .catch(function (err) {
      console.log(err); // 只在控制台打印，无任何 UI 提示。
    });
}

// 页面初始化
loadData();