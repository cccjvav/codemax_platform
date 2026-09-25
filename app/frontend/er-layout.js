// ER 图**纯布局**：{tables, edges} → {nodes, links, width, height}。
//
// ⚠️ 这个文件**绝不能 import d3 或碰 DOM**。原因不是洁癖，是测试要在 CI 上跑得动：
// tests/test_er_page.py 用 node 直接 import 它、把 /tools/er-diagram 的真实返回喂进来，
// 逐字段核对前后端契约。而 CI 只装 pip 依赖、**没有 node_modules** —— 一旦这里
// import 了 d3，node 就加载不了，那条契约测试在 CI 上必红。
// 要 d3 的渲染那一半在 er-page.js。
//
// 布局思路（TD-279，替换原来的「每行 3 张表 + 右中点连左中点的曲线」）：
// ① 按外键深度分列：被引用的表在左、引用它的表在右（depth = 1 + 父表最大深度）；
//    一列太高时拆成相邻的子列；没有任何外键关系的表放在最右的独立列。
// ② 节点宽度按文字估算（中文按 1em、ASCII 按字形宽度），夹在 [MIN_W, MAX_W]；仍放不下的
//    文字截断加省略号，完整文字交给渲染层做悬停提示 —— 原来固定 230px，长表名/注释溢出框外。
// ③ 连线是正交折线：从子表**外键列那一行**出发，终点是父表**被引用列那一行**；竖直段
//    只走列与列之间的空白通道，跨列的线从全部表的上方「车道」绕过去。所以任何线段都
//    不会穿过表格（测试逐段核对）—— 原来的曲线固定从右中点连到左中点，常横穿整张表。
//    指向同一父列的线在同一通道共用一条竖线（总线），多对一的关系读起来像一棵树。

const HEAD_H = 30;       // 表头高度
const ROW_H = 24;        // 每列一行
const PAD_BOTTOM = 8;
const PAD_X = 10;        // 文字左右内边距
const MIN_W = 160;
const MAX_W = 340;
const GAP_Y = 28;        // 同列上下两表的间距
const MAX_COL_H = 900;   // 超过这个高度就拆子列（单表更高时以单表为准）
const GAP_MIN = 56;      // 列间通道最小宽度
const GAP_PAD = 16;      // 通道里第一条/最后一条竖线离表格的距离
const SLOT = 8;          // 通道里相邻竖线的间距
const LANE_TOP = 10;     // 第一条顶部车道的 y
const LANE_STEP = 10;    // 相邻车道的间距
const LANE_CLEAR = 18;   // 最低一条车道到表格顶部的距离
const HEAD_FONT = 14;
const ROW_FONT = 12;

// 文字宽度估算（px）。只求「不低估」：真实字体（Noto Sans SC / 系统 UI 字体）下实测
// 估算值比真实宽度略大，截断宁可早一点，也不让文字压到框线外。
function textWidth(text, size, bold = false) {
  let w = 0;
  for (const ch of String(text)) {
    const c = ch.codePointAt(0);
    if (c >= 0x2e80) w += size;                         // CJK、全角标点
    else if (/[A-Z@#%&MW]/.test(ch)) w += size * 0.7;
    else if (/[mw]/.test(ch)) w += size * 0.86;
    else if (/[iljtfr.,:;'|!() ]/.test(ch)) w += size * 0.36;
    else w += size * 0.6;
  }
  return bold ? w * 1.08 : w;
}

// 放得下就原样返回；放不下就逐字截短并加省略号。
function fitText(text, maxWidth, size, bold = false) {
  const s = String(text);
  if (textWidth(s, size, bold) <= maxWidth) return s;
  const chars = Array.from(s);
  while (chars.length && textWidth(chars.join("") + "…", size, bold) > maxWidth) chars.pop();
  return chars.join("") + "…";
}

function nodeHeight(table) {
  return HEAD_H + (table.columns || []).length * ROW_H + PAD_BOTTOM;
}

function headerText(t) {
  return t.comment ? `${t.name}（${t.comment}）` : t.name;
}

function rowText(c, isFk) {
  return `${c.primary_key ? "PK " : ""}${isFk ? "FK " : ""}${c.name}: ${c.type}`;
}

// 列所在行的中线 y（相对节点顶部）；找不到该列时落在表头中线。
function rowCenter(node, column) {
  const i = node.columns.findIndex((c) => c.name === column);
  return i < 0 ? HEAD_H / 2 : HEAD_H + (i + 0.5) * ROW_H;
}

/* {tables, edges} → {nodes, links, width, height, headH, rowH}。 */
function layoutEr(graph) {
  const tables = graph.tables || [];
  const index = new Map(tables.map((t, i) => [t.name, i]));

  // 悬空外键（目标表不在 DDL 内）：只丢这条线，不让整图崩掉
  const edges = (graph.edges || [])
    .map((e) => {
      if (!index.has(e.from_table) || !index.has(e.to_table)) return null;
      return { e, child: index.get(e.from_table), parent: index.get(e.to_table) };
    })
    .filter(Boolean);

  const parents = tables.map(() => new Set());
  const related = tables.map(() => false);
  const fkCols = tables.map(() => new Set());
  for (const { e, child, parent } of edges) {
    fkCols[child].add(e.from_column);
    if (child === parent) continue;          // 自引用不影响分列
    parents[child].add(parent);
    related[child] = related[parent] = true;
  }

  // ---- ① 深度：环（a→b→a）里回到栈上的父表忽略，保证有限
  const depth = new Array(tables.length).fill(-1);
  const onStack = new Set();
  function depthOf(i) {
    if (depth[i] >= 0) return depth[i];
    onStack.add(i);
    let d = 0;
    for (const p of parents[i]) if (!onStack.has(p)) d = Math.max(d, depthOf(p) + 1);
    onStack.delete(i);
    depth[i] = d;
    return d;
  }
  tables.forEach((_, i) => depthOf(i));

  // ---- ② 节点尺寸与文字
  const nodes = tables.map((t, i) => {
    const columns = t.columns || [];
    const head = headerText(t);
    const rows = columns.map((c) => rowText(c, fkCols[i].has(c.name)));
    const natural = Math.max(textWidth(head, HEAD_FONT, true), ...rows.map((r) => textWidth(r, ROW_FONT)), 0);
    const w = Math.ceil(Math.min(MAX_W, Math.max(MIN_W, natural + 2 * PAD_X)));
    const inner = w - 2 * PAD_X;
    return {
      name: t.name,
      comment: t.comment || "",
      columns,
      x: 0,
      y: 0,
      w,
      h: nodeHeight(t),
      header: fitText(head, inner, HEAD_FONT, true),
      headerFull: head,
      rows: columns.map((c, k) => ({
        text: fitText(rows[k], inner, ROW_FONT),
        full: rows[k],
        pk: !!c.primary_key,
        fk: fkCols[i].has(c.name),
      })),
    };
  });

  // ---- ③ 分列：同一深度按父表的平均高度排序（减少交叉），过高则拆子列
  const columns = [];            // 每项：节点下标数组
  const col = new Array(tables.length).fill(-1);
  function pushColumns(list) {
    let current = [];
    let height = 0;
    for (const i of list) {
      const h = nodes[i].h;
      if (current.length && height + GAP_Y + h > MAX_COL_H) {
        columns.push(current);
        current = [];
        height = 0;
      }
      height += (current.length ? GAP_Y : 0) + h;
      current.push(i);
    }
    if (current.length) columns.push(current);
  }
  function stack(list) {
    let y = 0;
    for (const i of list) {
      nodes[i].y = y;
      y += nodes[i].h + GAP_Y;
    }
  }
  const maxDepth = Math.max(-1, ...tables.map((_, i) => (related[i] ? depth[i] : -1)));
  for (let d = 0; d <= maxDepth; d++) {
    const layer = tables.map((_, i) => i).filter((i) => related[i] && depth[i] === d);
    const center = (i) => {
      const placed = [...parents[i]].filter((p) => col[p] >= 0);
      if (!placed.length) return i;         // 根层保持 DDL 原顺序
      return placed.reduce((s, p) => s + nodes[p].y + nodes[p].h / 2, 0) / placed.length;
    };
    const keyed = layer.map((i) => [center(i), i]).sort((a, b) => a[0] - b[0] || a[1] - b[1]);
    const first = columns.length;
    pushColumns(keyed.map(([, i]) => i));
    for (let c = first; c < columns.length; c++) {
      stack(columns[c]);
      for (const i of columns[c]) col[i] = c;
    }
  }
  const firstIsolated = columns.length;
  pushColumns(tables.map((_, i) => i).filter((i) => !related[i]));
  for (let c = firstIsolated; c < columns.length; c++) {
    stack(columns[c]);
    for (const i of columns[c]) col[i] = c;
  }

  // ---- ④ 路由：先决定每条线用哪个通道（gap g 在第 g-1 列与第 g 列之间，0 与末尾是外边距）
  const routes = edges.map(({ e, child, parent }) => {
    const cc = col[child];
    const pc = col[parent];
    let childGap, parentGap, childRight, parentRight;
    if (pc < cc) { childGap = cc; parentGap = pc + 1; childRight = false; parentRight = true; }
    else if (pc > cc) { childGap = cc + 1; parentGap = pc; childRight = true; parentRight = false; }
    else { childGap = parentGap = cc + 1; childRight = parentRight = true; }  // 同列（含自引用）
    const target = `${parent}|${e.to_column}`;
    return { e, child, parent, childGap, parentGap, childRight, parentRight, target,
             direct: childGap === parentGap };
  });

  // 通道里的竖线（slot）：指向同一父列的线在同一通道共用一条（总线）
  const slotKeys = new Map();    // gap → Map(key → sortValue)
  const addSlot = (gap, key, order) => {
    if (!slotKeys.has(gap)) slotKeys.set(gap, new Map());
    const m = slotKeys.get(gap);
    if (!m.has(key)) m.set(key, order);
  };
  const lanes = new Map();       // target → 车道序号
  for (const r of routes) {
    const py = nodes[r.parent].y + rowCenter(nodes[r.parent], r.e.to_column);
    addSlot(r.parentGap, `T|${r.target}`, py);
    if (!r.direct) {
      const cy = nodes[r.child].y + rowCenter(nodes[r.child], r.e.from_column);
      addSlot(r.childGap, `C|${r.child}|${r.target}`, cy);
      if (!lanes.has(r.target)) lanes.set(r.target, lanes.size);
    }
  }

  // 通道宽度随竖线条数增长；列宽取该列最宽的表
  const gapCount = columns.length + 1;
  const gapWidth = [];
  const slotX = new Map();       // `${gap}|${key}` → 相对通道左边的 x
  for (let g = 0; g < gapCount; g++) {
    const m = slotKeys.get(g);
    const keys = m ? [...m.entries()].sort((a, b) => a[1] - b[1]).map(([k]) => k) : [];
    const inner = g > 0 && g < gapCount - 1;
    gapWidth[g] = keys.length ? Math.max(inner ? GAP_MIN : 0, 2 * GAP_PAD + (keys.length - 1) * SLOT)
                              : (inner ? GAP_MIN : 0);
    keys.forEach((k, n) => slotX.set(`${g}|${k}`, GAP_PAD + n * SLOT));
  }
  const colWidth = columns.map((list) => Math.max(...list.map((i) => nodes[i].w)));
  const gapLeft = [];
  let x = 0;
  for (let g = 0; g < gapCount; g++) {
    gapLeft[g] = x;
    x += gapWidth[g];
    if (g < columns.length) {
      for (const i of columns[g]) nodes[i].x = x;
      x += colWidth[g];
    }
  }
  const widest = x;              // 通道 + 列的总宽度

  // 顶部车道占的高度；所有节点整体下移
  const top = lanes.size ? LANE_TOP + (lanes.size - 1) * LANE_STEP + LANE_CLEAR : 0;
  for (const n of nodes) n.y += top;

  const links = routes.map((r) => {
    const s = nodes[r.child];
    const t = nodes[r.parent];
    const x1 = r.childRight ? s.x + s.w : s.x;
    const y1 = s.y + rowCenter(s, r.e.from_column);
    const x2 = r.parentRight ? t.x + t.w : t.x;
    const y2 = t.y + rowCenter(t, r.e.to_column);
    const px = gapLeft[r.parentGap] + slotX.get(`${r.parentGap}|T|${r.target}`);
    let points;
    if (r.direct) {
      points = [[x1, y1], [px, y1], [px, y2], [x2, y2]];
    } else {
      const cx = gapLeft[r.childGap] + slotX.get(`${r.childGap}|C|${r.child}|${r.target}`);
      const ly = LANE_TOP + lanes.get(r.target) * LANE_STEP;
      points = [[x1, y1], [cx, y1], [cx, ly], [px, ly], [px, y2], [x2, y2]];
    }
    return {
      from: r.e.from_table,
      to: r.e.to_table,
      fromColumn: r.e.from_column,
      toColumn: r.e.to_column,
      label: r.e.from_column + " → " + r.e.to_column,
      x1, y1, x2, y2,
      points,
      path: "M" + points.map(([px2, py2]) => `${px2},${py2}`).join(" L"),
    };
  });

  const bottom = nodes.length ? Math.max(...nodes.map((n) => n.y + n.h)) : 0;
  return {
    nodes,
    links,
    width: widest ? widest : 0,  // 空输入不能算出负宽度
    height: bottom,
    headH: HEAD_H,
    rowH: ROW_H,
  };
}

// 初始视图（纯函数，便于测试）：整图按比例缩进画布后字仍可读（≥ READABLE）就显示全图并居中；
// 否则以 READABLE 比例从左上角开始，用户拖动平移。原来 viewBox 永远等于整图，表一多
// （本项目 16 张表、深度 6 层）整图被压到约 40%，12px 的列名缩成 5px，根本读不了。
const READABLE = 0.75;
const MARGIN = 12;
function initialView(layout, viewW, viewH, mode = "auto") {
  const w = Math.max(layout.width, 1);
  const h = Math.max(layout.height, 1);
  const fitK = Math.min(1, (viewW - 2 * MARGIN) / w, (viewH - 2 * MARGIN) / h);
  const fits = fitK >= READABLE;
  if (mode === "fit" || (mode === "auto" && fits)) {
    return { k: fitK, x: (viewW - w * fitK) / 2, y: (viewH - h * fitK) / 2, fits, fitK };
  }
  return { k: READABLE, x: MARGIN, y: MARGIN, fits, fitK };
}

export { layoutEr, nodeHeight, textWidth, fitText, initialView, READABLE };
