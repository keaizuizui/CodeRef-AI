// 分区布局纯函数：角色 → 泳道分区 → 模块网格槽位。
// 不依赖 React/DOM，输入 roles + modules，输出每个模块坐标与分区几何。

export const BOX_W = 1500;
export const NODE_W = 118;
export const NODE_H = 32;
export const GAP = 6;
export const PAD = 14;
export const SECTION_GAP = 72; // 分区之间的主干流箭头区
const TITLE_H = 34;

// 每行模块数
function perRow() {
  return Math.max(1, Math.floor((BOX_W - 2 * PAD) / (NODE_W + GAP)));
}

// 计算一个分区内模块网格
function grid(items, y0) {
  const cols = perRow();
  const rows = Math.ceil(items.length / cols) || 1;
  let y = y0 + TITLE_H;
  const placed = [];
  items.forEach((name, i) => {
    const col = i % cols;
    const row = Math.floor(i / cols);
    placed.push({
      name,
      x: PAD + col * (NODE_W + GAP),
      y: y + row * (NODE_H + GAP),
      w: NODE_W,
      h: NODE_H,
    });
  });
  return {
    placed,
    h: TITLE_H + rows * NODE_H + (rows - 1) * GAP + PAD,
  };
}

// 主入口：roles（含 modules 全路径）+ freeModules（未归属）
// 返回 { roleBoxes, freeBox, modulePos, totalW, totalH }
export function computeLayout(roles, freeModules) {
  const roleBoxes = [];
  const modulePos = {};
  let y = 30; // 顶部留白（工具条空间）
  const roleRects = [];

  for (const role of roles) {
    const { placed, h } = grid(role.modules || [], y);
    const rect = { id: role.id, name: role.name, x: 20, y, w: BOX_W, h };
    roleBoxes.push({ ...rect, modules: placed, missing: role.missing });
    roleRects.push(rect);
    for (const p of placed) modulePos[p.name] = p;
    y += h + SECTION_GAP;
  }

  // 未建模区（游离/未归属）
  let freeBox = null;
  if (freeModules && freeModules.length) {
    const { placed, h } = grid(freeModules, y);
    freeBox = { id: '__free', name: '未建模区', x: 20, y, w: BOX_W, h, modules: placed };
    for (const p of placed) modulePos[p.name] = p;
    y += h + 30;
  }

  return {
    roleBoxes,
    freeBox,
    modulePos,
    roleRects,
    totalW: BOX_W + 40,
    totalH: y,
  };
}

// 分区之间的主干流箭头几何：按 flows.steps 顺序匹配角色分区
// 返回 [{x, y1, y2}] 每个相邻分区对的竖直线（居中）
export function flowArrows(roleRects, flows) {
  const arrows = [];
  if (!roleRects.length || !flows) return arrows;
  for (const f of flows) {
    const steps = f.steps || [];
    for (let i = 0; i + 1 < steps.length; i += 1) {
      const a = roleRects.find((r) => r.name === steps[i]);
      const b = roleRects.find((r) => r.name === steps[i + 1]);
      if (!a || !b || b.y <= a.y) continue;
      arrows.push({
        flow: f.name,
        x: a.x + a.w / 2,
        y1: a.y + a.h,
        y2: b.y,
      });
    }
  }
  return arrows;
}
