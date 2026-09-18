import React, { useMemo, useRef, useState, useEffect, useCallback } from 'react';
import { computeLayout, flowArrows, BOX_W, NODE_W, NODE_H } from '../layout/partition.js';
import './canvas.css';

const PALETTE = ['#eef2ff', '#e0f2fe', '#ecfdf5', '#fef3c7', '#fae8ff'];
const PALETTE_BORDER = ['#c7d2fe', '#bae6fd', '#a7f3d0', '#fde68a', '#f5d0fe'];

// 模块颜色判定：循环 > 未归属 > 孪生 > 普通
function nodeStyle(mod, isHL, isTarget, isSel) {
  let fill = '#ffffff';
  let stroke = '#93c5fd';
  let text = '#1d4ed8';
  let strokeWidth = 1.2;
  if (mod.unassigned) { fill = '#f4f4f5'; stroke = '#a1a1aa'; text = '#52525b'; }
  if (mod.in_cycles) { stroke = '#f59e0b'; text = '#b45309'; strokeWidth = 1.6; }
  if (mod.twin === '真身') { stroke = '#16a34a'; text = '#15803d'; }
  if (mod.twin === '孤本') { fill = '#e4e4e7'; stroke = '#71717a'; text = '#3f3f46'; }
  if (isSel) { stroke = '#f97316'; strokeWidth = 2.2; }
  if (isTarget) { stroke = '#0ea5e9'; strokeWidth = 2.2; }
  if (isHL) { stroke = '#dc2626'; strokeWidth = 2.6; }
  return { fill, stroke, text, strokeWidth };
}

// 长模块名截断（避免挤压出界），全名保留在 title/aria-label
function shortLabel(s) {
  const MAX = 15;
  return s && s.length > MAX ? `${s.slice(0, MAX - 1)}…` : s;
}

export default function CanvasView({ data, highlight, onBack }) {
  const wrapRef = useRef(null);
  const [scale, setScale] = useState(0.9);
  const [hover, setHover] = useState(null);   // 悬停模块名
  const [selected, setSelected] = useState(null); // 点选模块名（展开依赖）

  // 未归属模块（无 role）
  const freeModules = useMemo(
    () => (data.modules || []).filter((m) => !m.role).map((m) => m.name),
    [data],
  );

  // 布局
  const layout = useMemo(() => computeLayout(data.roles || [], freeModules), [data, freeModules]);
  const arrows = useMemo(() => flowArrows(layout.roleRects, data.flows || []), [layout, data]);
  const modByName = useMemo(() => {
    const m = {};
    for (const mod of data.modules || []) m[mod.name] = mod;
    return m;
  }, [data]);

  // highlight 目标模块集合（报告跳转）
  const hlModules = useMemo(() => {
    if (!highlight) return new Set();
    const s = new Set();
    if (highlight.module) s.add(highlight.module);
    else if (highlight.kind === 'cycle') {
      for (const mod of data.modules || []) if (mod.in_cycles) s.add(mod.name);
    }
    return s;
  }, [highlight, data]);

  const fitView = useCallback(() => {
    const el = wrapRef.current;
    if (!el) return;
    const w = el.clientWidth || 1200;
    const h = el.clientHeight || 800;
    const s = Math.min(w / layout.totalW, h / layout.totalH, 1.1);
    setScale(Math.max(0.15, s));
  }, [layout]);

  useEffect(() => { fitView(); }, [fitView]);
  useEffect(() => { if (highlight) fitView(); }, [highlight, fitView]);

  // 依赖边（hover/selected 模块的直接调用）
  const depEdges = useMemo(() => {
    const active = selected || hover;
    if (!active) return [];
    const mod = modByName[active];
    if (!mod) return [];
    const edges = [];
    const seen = new Set();
    const add = (from, to) => {
      const key = `${from}>${to}`;
      if (seen.has(key)) return;
      seen.add(key);
      edges.push({ from, to });
    };
    for (const t of mod.call_out || []) add(active, t);
    for (const s of mod.call_in || []) add(s, active);
    return edges;
  }, [selected, hover, modByName]);

  // SVG 路径（二次贝塞尔，带箭头）
  const edgePath = (from, to) => {
    const a = layout.modulePos[from];
    const b = layout.modulePos[to];
    if (!a || !b) return null;
    const x1 = a.x + a.w, y1 = a.y + a.h / 2;
    const x2 = b.x, y2 = b.y + b.h / 2;
    const cx = (x1 + x2) / 2;
    return { d: `M ${x1} ${y1} Q ${cx} ${y1} ${cx} ${y2} T ${x2} ${y2}`, x1, y1, x2, y2 };
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
    <div className="canvas-root">
      <div className="cv-toolbar">
        <span className="cv-title">架构分区图 · {data.project}</span>
        <span className="cv-spacer" />
        <button onClick={onBack}>📋 回决策者报告</button>
        <button onClick={() => setScale((s) => Math.min(2, s * 1.2))}>➕ 放大</button>
        <button onClick={() => setScale((s) => Math.max(0.15, s * 0.8))}>➖ 缩小</button>
        <button onClick={fitView}>📐 适应</button>
        <button onClick={exportJson}>⬇ 导出 JSON</button>
      </div>

      <div className="cv-legend">
        <span><i className="lg lg-box" />角色分区</span>
        <span><i className="lg lg-mod" />模块</span>
        <span><i className="lg lg-cycle" />循环依赖</span>
        <span><i className="lg lg-free" />未归属</span>
        <span><i className="lg lg-vio" />违规边</span>
        <span><i className="lg lg-flow" />业务主干流</span>
        <span className="muted">· 悬停/点击模块查看直接依赖</span>
      </div>

      <div className="cv-wrap" ref={wrapRef}>
        <svg
          width={layout.totalW * scale}
          height={layout.totalH * scale}
          viewBox={`0 0 ${layout.totalW} ${layout.totalH}`}
          className="cv-svg"
        >
          {/* 分区背景 */}
          {layout.roleBoxes.map((box, i) => (
            <g key={box.id}>
              <rect x={box.x} y={box.y} width={box.w} height={box.h}
                    rx={10} fill={PALETTE[i % PALETTE.length]}
                    stroke={PALETTE_BORDER[i % PALETTE_BORDER.length]} strokeWidth={1} />
              {box.missing && (
                <text x={box.x + 14} y={box.y + 20} fontSize={12} fill="#dc2626" fontWeight="700">
                  ⚠ 目标角色定义缺失
                </text>
              )}
            </g>
          ))}
          {layout.freeBox && (
            <g>
              <rect x={layout.freeBox.x} y={layout.freeBox.y} width={layout.freeBox.w}
                    height={layout.freeBox.h} rx={10} fill="#f4f4f5"
                    stroke="#d4d4d8" strokeWidth={1.4} strokeDasharray="6 4" />
              <text x={layout.freeBox.x + 14} y={layout.freeBox.y + 20}
                    fontSize={13} fill="#71717a" fontWeight="700">未建模区</text>
            </g>
          )}

          {/* 主干流箭头 */}
          {arrows.map((a, i) => (
            <g key={`flow-${i}`}>
              <line x1={a.x} y1={a.y1 + 4} x2={a.x} y2={a.y2 - 4} stroke="#4f6ef7"
                    strokeWidth={4} strokeLinecap="round" markerEnd="url(#arrowBlue)" />
              <text x={a.x + 10} y={(a.y1 + a.y2) / 2} fontSize={11} fill="#4f6ef7" fontWeight="700">
                {a.flow}
              </text>
            </g>
          ))}

          {/* 违规边（红） */}
          {data.violations && data.violations.map((v, i) => {
            const p = edgePath(v.from, v.to);
            if (!p) return null;
            return (
              <g key={`vio-${i}`}>
                <path d={p.d} fill="none" stroke="#dc2626" strokeWidth={2}
                      strokeLinecap="round" markerEnd="url(#arrowRed)" />
              </g>
            );
          })}

          {/* 依赖边（hover/selected） */}
          {depEdges.map((e, i) => {
            const p = edgePath(e.from, e.to);
            if (!p) return null;
            return (
              <g key={`dep-${i}`}>
                <path d={p.d} fill="none" stroke="#cbd5e1" strokeWidth={1.4}
                      strokeDasharray="5 3" strokeLinecap="round" markerEnd="url(#arrowDep)" />
              </g>
            );
          })}

          {/* 模块节点 */}
          {data.modules && data.modules.map((mod) => {
            const pos = layout.modulePos[mod.name];
            if (!pos) return null;
            const isHL = hlModules.has(mod.name);
            const isSel = selected === mod.name;
            const isTarget = depEdges.some((e) => e.to === mod.name || e.from === mod.name);
            const st = nodeStyle(mod, isHL, isTarget, isSel);
            return (
              <g key={mod.name}
                 transform={`translate(${pos.x},${pos.y})`}
                 onMouseEnter={() => setHover(mod.name)}
                 onMouseLeave={() => setHover((h) => (h === mod.name ? null : h))}
                 onClick={() => setSelected((s) => (s === mod.name ? null : mod.name))}
                 role="button"
                 aria-label={`模块 ${mod.name}，循环依赖 ${mod.in_cycles ? '是' : '否'}`}
                 aria-pressed={isSel}
                 className="cv-node">
                {isHL && <rect x={-3} y={-3} width={pos.w + 6} height={pos.h + 6} rx={6}
                               fill="none" stroke="#dc2626" strokeWidth={2.6} className="cv-pulse" />}
                <rect x={0} y={0} width={pos.w} height={pos.h} rx={6}
                      fill={st.fill} stroke={st.stroke} strokeWidth={st.strokeWidth} />
                <text x={pos.w / 2} y={pos.h / 2 + 4} textAnchor="middle" fontSize={11.5}
                      fill={st.text} fontWeight={isSel || isHL ? 700 : 500}
                      className="cv-node-text">
                  {shortLabel(mod.short)}
                </text>
                {mod.in_cycles && <circle cx={pos.w - 7} cy={7} r={3.5} fill="#f59e0b" />}
              </g>
            );
          })}

          {/* 分区标题 */}
          {layout.roleBoxes.map((box, i) => (
            <text key={`t-${box.id}`} x={box.x + 14} y={box.y + 20} fontSize={13.5}
                  fill="#3f3f46" fontWeight="700">
              {box.name}
              <tspan fill="#6b7280" fontWeight="500"> · {box.modules.length} 模块</tspan>
            </text>
          ))}

          <defs>
            <marker id="arrowBlue" markerWidth="10" markerHeight="10" refX="8" refY="5" orient="auto">
              <path d="M 0 1 L 8 5 L 0 9 Z" fill="#4f6ef7" />
            </marker>
            <marker id="arrowRed" markerWidth="10" markerHeight="10" refX="8" refY="5" orient="auto">
              <path d="M 0 1 L 8 5 L 0 9 Z" fill="#dc2626" />
            </marker>
            <marker id="arrowDep" markerWidth="9" markerHeight="9" refX="7" refY="4.5" orient="auto">
              <path d="M 0 1 L 7 4.5 L 0 8 Z" fill="#cbd5e1" />
            </marker>
          </defs>
        </svg>
      </div>
    </div>
  );
}
