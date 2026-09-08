// ER 图**纯布局**：{tables, edges} → {nodes, links, width, height}。
//
// ⚠️ 这个文件**绝不能 import d3 或碰 DOM**。原因不是洁癖，是测试要在 CI 上跑得动：
// tests/test_er_page.py 用 node 直接 import 它、把 /tools/er-diagram 的真实返回喂进来，
// 逐字段核对前后端契约。而 CI 只装 pip 依赖、**没有 node_modules** —— 一旦这里
// import 了 d3，node 就加载不了，那条契约测试在 CI 上必红。
// 要 d3 的渲染那一半在 er-page.js。


const NODE_W = 230;
const HEAD_H = 30;
const ROW_H = 24;
const PAD_BOTTOM = 8;
const COLS_PER_ROW = 3;
const GAP_X = 90;
const GAP_Y = 70;

function nodeHeight(table) {
  return HEAD_H + (table.columns || []).length * ROW_H + PAD_BOTTOM;
}

/* {tables, edges} → {nodes, links, width, height}：网格排布，行高取该行最高的表。 */
function layoutEr(graph) {
  const rows = [];
  (graph.tables || []).forEach((t, i) => {
    const r = Math.floor(i / COLS_PER_ROW);
    if (!rows[r]) rows[r] = [];
    rows[r].push(t);
  });

  const nodes = [];
  const byName = new Map();
  let y = 0;
  rows.forEach((rowTables) => {
    const rowH = Math.max(...rowTables.map(nodeHeight));
    rowTables.forEach((t, c) => {
      const node = {
        name: t.name,
        comment: t.comment || "",
        columns: t.columns || [],
        x: c * (NODE_W + GAP_X),
        y: y,
        w: NODE_W,
        h: nodeHeight(t),
      };
      nodes.push(node);
      byName.set(t.name, node);
    });
    y += rowH + GAP_Y;
  });

  const links = (graph.edges || [])
    .map((e) => {
      const s = byName.get(e.from_table);
      const t = byName.get(e.to_table);
      if (!s || !t) return null; // 悬空外键（目标表不在 DDL 内）：跳过，不让整图崩掉
      return {
        from: e.from_table,
        to: e.to_table,
        label: e.from_column + " → " + e.to_column,
        x1: s.x + s.w,
        y1: s.y + s.h / 2,
        x2: t.x,
        y2: t.y + t.h / 2,
      };
    })
    .filter(Boolean);

  const widest = rows.length ? Math.max(...rows.map((r) => r.length)) : 0;
  return {
    nodes,
    links,
    width: widest ? widest * (NODE_W + GAP_X) - GAP_X : 0, // 空输入不能算出负宽度
    height: Math.max(y - GAP_Y, 0),
    headH: HEAD_H,
    rowH: ROW_H,
  };
}

export { layoutEr, nodeHeight };
