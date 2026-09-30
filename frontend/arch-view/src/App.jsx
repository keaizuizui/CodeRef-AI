import React, { useState } from 'react';
import ReportView from './views/ReportView.jsx';
import FrameworkView from './views/FrameworkView.jsx';
import devData from './fixtures/vimax.json';

// 数据来源：生产环境由 core/arch_view.py 注入 window.__ARCH_DATA__（单文件模板内联）；
// 开发期（npm run dev）无注入时回退 fixtures/vimax.json 真实样本数据联调。
const data = window.__ARCH_DATA__ || devData;

export default function App() {
  const [view, setView] = useState('report'); // 'report' | 'framework'
  const [highlight, setHighlight] = useState(null); // 差距高亮 {type, module, kind, severity}

  return (
    <div className="app">
      {view === 'report' ? (
        <ReportView
          data={data}
          onExplore={(hl) => { setHighlight(hl); setView('framework'); }}
        />
      ) : (
        <FrameworkView
          data={data}
          highlight={highlight}
          onBack={() => { setHighlight(null); setView('report'); }}
        />
      )}
    </div>
  );
}
