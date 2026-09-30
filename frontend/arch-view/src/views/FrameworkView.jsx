import React, { useEffect, useRef, useState } from 'react';
import './framework.css';

// 业务架构大白话图（v5.16）：入口层 → 主功能卡 → 子功能（可展开）→ 步骤链
export default function FrameworkView({ data, onBack, highlight }) {
  const [expanded, setExpanded] = useState(new Set());
  const [hl, setHl] = useState(null); // 差距高亮 {cardId, subId}
  const cardRefs = useRef({});
  const framework = data.framework || { name: '入口层', modules: [], children: [] };

  // 差距点击联动（D2 修复）：定位 module 所属主功能/子功能，展开并高亮
  useEffect(() => {
    if (!highlight || !highlight.module) { setHl(null); return; }
    const mod = highlight.module;
    let target = null;
    for (const c of framework.children || []) {
      if ((c.modules || []).includes(mod)) { target = { cardId: c.id }; break; }
      for (const s of c.children || []) {
        if ((s.modules || []).includes(mod)) { target = { cardId: c.id, subId: s.id }; break; }
      }
      if (target) break;
    }
    if (!target) { setHl(null); return; }
    setExpanded((prev) => {
      const next = new Set(prev);
      const card = framework.children.find((x) => x.id === target.cardId);
      for (const s of (card ? card.children : [])) next.add(s.id);
      return next;
    });
    setHl(target);
    setTimeout(() => {
      cardRefs.current[target.cardId]?.scrollIntoView({ behavior: 'smooth', block: 'center' });
    }, 120);
  }, [highlight]);

  const toggle = (id) => {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const exportJson = () => {
    const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `${data.project || 'arch'}_arch_data.json`;
    a.click();
    URL.revokeObjectURL(url);
  };

  return (
    <div className="fw-root">
      <div className="cv-toolbar">
        <span className="cv-title">业务架构图 · {data.project}</span>
        <span className="cv-spacer" />
        <button onClick={onBack}>📋 回决策者报告</button>
        <button onClick={exportJson}>⬇ 导出 JSON</button>
      </div>

      <div className="fw-wrap">
        {/* 入口层 */}
        <div className="fw-entry">
          <span className="fw-entry-tag">入口</span>
          <span className="fw-entry-title">{framework.name}</span>
          <div className="fw-entry-mods">
            {(framework.modules || []).map((m) => (
              <span key={m} className="fw-mod" title={m}>{m.split('/').pop()}</span>
            ))}
          </div>
        </div>

        {/* 主功能卡 */}
        <div className="fw-cards">
          {(framework.children || []).map((c) => (
            <div key={c.id}
                 ref={(el) => { cardRefs.current[c.id] = el; }}
                 className={`fw-card${hl && hl.cardId === c.id ? ' fw-card-hl' : ''}`}>
              <div className="fw-card-head">
                <span className="fw-card-title">{c.title}</span>
                <span className="fw-badge">{(c.modules || []).length} 模块</span>
              </div>
              {c.desc && <div className="fw-desc">{c.desc}</div>}
              <div className="fw-subs">
                {(c.children || []).map((s) => {
                  const open = expanded.has(s.id);
                  return (
                    <div key={s.id} className={`fw-sub${hl && hl.subId === s.id ? ' fw-sub-hl' : ''}`}>
                      <button className="fw-sub-head" onClick={() => toggle(s.id)}>
                        <span className="fw-arrow">{open ? '▾' : '▸'}</span>
                        <span className="fw-sub-title">{s.title}</span>
                        {(s.modules || []).length > 0 && <span className="fw-sub-n">{(s.modules || []).length}</span>}
                      </button>
                      {open && (
                        <div className="fw-sub-body">
                          {s.desc && <div className="fw-desc">{s.desc}</div>}
                          {(s.steps || []).length > 0 && (
                            <ol className="fw-steps">
                              {s.steps.map((st, i) => <li key={i}>{st}</li>)}
                            </ol>
                          )}
                          {(s.modules || []).length > 0 && (
                            <div className="fw-mods">
                              {(s.modules || []).map((m) => (
                                <span key={m} className="fw-mod" title={m}>{m.split('/').pop()}</span>
                              ))}
                            </div>
                          )}
                        </div>
                      )}
                    </div>
                  );
                })}
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
