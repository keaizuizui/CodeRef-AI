import React, { useState } from 'react';
import ReportView from './views/ReportView.jsx';
import CanvasView from './views/CanvasView.jsx';
import devData from './fixtures/vimax.json';

// 数据来源：生产环境由 core/arch_view.py 注入 window.__ARCH_DATA__（单文件模板内联）；
// 开发期（npm run dev）无注入时回退 fixtures/vimax.json 真实样本数据联调。
const data = window.__ARCH_DATA__ || devData;

export default function App() {
  const [view, setView] = useState('report'); // 'report' | 'canvas'
  const [highlight, setHighlight] = useState(null); // {type:'gap'|'module', value, severity}

  const goCanvas = (hl) => {
    if (hl) setHighlight(hl);
    setView('canvas');
  };

  return (
    <div className="app">
      {view === 'report' ? (
        <ReportView data={data} onExplore={(hl) => goCanvas(hl)} />
      ) : (
        <CanvasView
          data={data}
          highlight={highlight}
          onBack={() => {
            setHighlight(null);
            setView('report');
          }}
        />
      )}
    </div>
  );
}
