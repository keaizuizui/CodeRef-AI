import React, { useMemo } from 'react';
import './report.css';

const SEV = {
  high: { label: '高', cls: 'sev-high' },
  medium: { label: '中', cls: 'sev-medium' },
  low: { label: '低', cls: 'sev-low' },
};

const TYPE_LABEL = {
  unassigned: '未归属',
  cycle: '循环依赖',
  duplicate: '重复',
  directory_duplicate: '目录重复',
  god_module: '上帝模块',
  large_module: '大模块',
  business_gap: '业务差距',
  dependency_violation: '跨层违例',
  twin_identity: '孪生',
};

// 从数据生成"一句话总述"（叙事式，不数数）
function buildNarrative(d) {
  const roles = d.roles || [];
  const s = d.summary || {};
  const roleNames = roles.map((r) => r.name).join('、');
  const part = roles.length ? `由 ${roles.length} 大块组成（${roleNames}）` : '结构尚未定义';
  const modTxt = `${s.module_total || 0} 个代码模块`;
  const alignPct = Math.round((s.alignment || 0) * 100);
  const alignTxt = alignPct >= 95 ? '整体架构已基本归位'
    : alignPct >= 60 ? '大部分模块已归位，仍有关键缺口'
    : '架构对齐度偏低，需要优先治理';
  const gapN = s.gap_total || 0;
  const highN = s.high || 0;
  const gapTxt = gapN === 0 ? '当前没有检测到结构问题'
    : highN > 0 ? `还有 ${gapN} 个问题需要关注，其中 ${highN} 个高优先级`
    : `还有 ${gapN} 个问题需要关注`;
  return `${d.project} 是一个${part}的系统，共 ${modTxt}。${alignTxt}（对齐度 ${alignPct}%）。${gapTxt}。`;
}

export default function ReportView({ data, onExplore }) {
  const narrative = useMemo(() => buildNarrative(data), [data]);
  const roles = data.roles || [];
  const flows = data.flows || [];
  const gaps = data.gaps || [];

  // 角色卡片排序：按 flows 出现顺序优先（入口→业务→底座语义），其余按原序
  const orderedRoles = useMemo(() => {
    const order = [];
    for (const f of flows) for (const st of f.steps || []) {
      for (const r of roles) {
        if (!order.includes(r.id)) {
          const stepNames = (f.steps || []).map((x) => x);
          // 简单启发：flow 步骤名与角色名一致时优先
          if (stepNames.some((n) => n.includes(r.name))) order.push(r.id);
        }
      }
    }
    for (const r of roles) if (!order.includes(r.id)) order.push(r.id);
    return order.map((id) => roles.find((r) => r.id === id)).filter(Boolean);
  }, [roles, flows]);

  const sevGroups = useMemo(() => {
    const g = { high: [], medium: [], low: [] };
    for (const item of gaps) {
      const key = SEV[item.severity] ? item.severity : 'low';
      g[key].push(item);
    }
    return g;
  }, [gaps]);

  const jumpToCanvas = (gap) => {
    // 报告条目 → 画布高亮联动：module 精确定位；cycle 类高亮全部循环模块
    const hl = { type: 'gap', severity: gap.severity, kind: gap.type };
    if (gap.module) hl.module = gap.module;
    onExplore(hl);
  };

  return (
    <div className="report">
      <header className="rp-head">
        <div className="rp-title-row">
          <h1 className="rp-title">{data.project} · 架构总览</h1>
          <button className="primary" onClick={() => onExplore(null)}>🔍 深看架构画布</button>
        </div>
        <p className="rp-narrative">{narrative}</p>
      </header>

      {/* ② 系统怎么搭：角色分区卡片 */}
      <section className="rp-sec">
        <h2 className="rp-h2">系统怎么搭</h2>
        {orderedRoles.length === 0 && <p className="muted">尚未定义技术角色。</p>}
        <div className="rp-role-chain">
          {orderedRoles.map((r, i) => (
            <React.Fragment key={r.id}>
              {i > 0 && <div className="rp-arrow">→</div>}
              <div className="rp-role-card">
                <div className="rp-role-top">
                  <span className="rp-role-name">{r.name}</span>
                  <span className="rp-badge">{r.modules.length} 模块</span>
                </div>
                {r.missing && <div className="rp-missing">⚠ 定义缺失</div>}
                <div className="rp-role-mods">
                  {r.modules.slice(0, 6).map((m) => (
                    <span key={m} className="rp-mod-chip" title={m}>
                      {m.split('/').pop()}
                    </span>
                  ))}
                  {r.modules.length > 6 && (
                    <span className="rp-more">+{r.modules.length - 6}</span>
                  )}
                  {r.modules.length === 0 && <span className="muted">尚无归属模块</span>}
                </div>
                {r.desc && <div className="rp-role-desc">{r.desc}</div>}
              </div>
            </React.Fragment>
          ))}
          <div className="rp-role-card rp-role-free">
            <div className="rp-role-name">未建模区</div>
            <div className="rp-role-mods">
              {(data.modules || []).filter((m) => !m.role).map((m) => (
                <span key={m.name} className="rp-mod-chip rp-mod-free" title={m.name}>
                  {m.short}
                </span>
              ))}
            </div>
          </div>
        </div>
      </section>

      {/* ③ 系统怎么跑：业务流 */}
      <section className="rp-sec">
        <h2 className="rp-h2">系统怎么跑</h2>
        {flows.length === 0 && <p className="muted">尚未定义业务主干流。</p>}
        {flows.map((f, i) => (
          <div key={i} className="rp-flow">
            <span className="rp-flow-name">{f.name}</span>
            <div className="rp-flow-chain">
              {(f.steps || []).map((s, j) => (
                <React.Fragment key={j}>
                  {j > 0 && <span className="rp-flow-arrow">→</span>}
                  <span className="rp-flow-step">{s}</span>
                </React.Fragment>
              ))}
            </div>
            {f.desc && <span className="rp-flow-desc">{f.desc}</span>}
          </div>
        ))}
      </section>

      {/* ④ 哪里有问题：差距清单（人话，可跳画布） */}
      <section className="rp-sec">
        <h2 className="rp-h2">哪里有问题</h2>
        {gaps.length === 0 && <p className="muted">没有检测到结构问题，系统整体健康。</p>}
        {['high', 'medium', 'low'].map((sev) =>
          sevGroups[sev].length > 0 && (
            <div key={sev} className="rp-gap-group">
              <h3 className={`rp-gap-sev ${SEV[sev].cls}`}>
                {SEV[sev].label}优先级 · {sevGroups[sev].length} 项
              </h3>
              {sevGroups[sev].map((g, i) => (
                <div key={i} className={`rp-gap-item ${SEV[g.severity || 'low'].cls}`}
                     onClick={() => jumpToCanvas(g)} title="点击在画布中定位">
                  <span className="rp-gap-type">{TYPE_LABEL[g.type] || g.type}</span>
                  <span className="rp-gap-detail">{g.detail}</span>
                </div>
              ))}
            </div>
          )
        )}
      </section>

      <footer className="rp-foot muted">
        由 CodeRef-AI 静态分析生成 · 点击「深看架构画布」查看分区架构图
      </footer>
    </div>
  );
}
