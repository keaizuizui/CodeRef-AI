import React from 'react';

// 分区架构画布（P2 填充：角色分区 + 三类边 + 折叠探查）
export default function CanvasView({ data, highlight, onBack }) {
  return (
    <div style={{ padding: 24 }}>
      <h2>架构画布（P2 开发中）</h2>
      <p className="muted">模块 {data.summary?.module_total} · 违规 {data.violations?.length}</p>
      <button onClick={onBack}>📋 回决策者报告</button>
    </div>
  );
}
