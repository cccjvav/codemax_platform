/* S2-01-1 前端：SQL DDL → ER 图渲染（D3.js）。
 * layoutEr 是纯函数，不碰 DOM / d3，可被 node 直接 require —— 后端契约测试
 * （tests/test_er_page.py）就是拿接口真实返回喂给它，保证字段两端对得上。 */

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
    width: widest * (NODE_W + GAP_X) - GAP_X,
    height: Math.max(y - GAP_Y, 0),
    headH: HEAD_H,
    rowH: ROW_H,
  };
}

function renderEr(selector, graph) {
  const L = layoutEr(graph);
  const svg = d3
    .select(selector)
    .attr("viewBox", [0, 0, Math.max(L.width, 320), Math.max(L.height, 200)])
    .html("");
  const root = svg.append("g");
  svg.call(d3.zoom().scaleExtent([0.2, 3]).on("zoom", (ev) => root.attr("transform", ev.transform)));

  root
    .append("g")
    .attr("fill", "none")
    .attr("stroke", "#94a3b8")
    .selectAll("path")
    .data(L.links)
    .join("path")
    .attr("d", (l) => {
      const mx = (l.x1 + l.x2) / 2;
      return `M${l.x1},${l.y1} C${mx},${l.y1} ${mx},${l.y2} ${l.x2},${l.y2}`;
    });

  root
    .append("g")
    .attr("font-size", 11)
    .attr("fill", "#64748b")
    .selectAll("text")
    .data(L.links)
    .join("text")
    .attr("x", (l) => (l.x1 + l.x2) / 2)
    .attr("y", (l) => (l.y1 + l.y2) / 2 - 4)
    .attr("text-anchor", "middle")
    .text((l) => l.label);

  const node = root
    .append("g")
    .selectAll("g")
    .data(L.nodes)
    .join("g")
    .attr("transform", (n) => `translate(${n.x},${n.y})`);
  node
    .append("rect")
    .attr("width", (n) => n.w)
    .attr("height", (n) => n.h)
    .attr("rx", 6)
    .attr("fill", "#fff")
    .attr("stroke", "#3b82f6");
  node
    .append("rect")
    .attr("width", (n) => n.w)
    .attr("height", L.headH)
    .attr("rx", 6)
    .attr("fill", "#3b82f6");
  node
    .append("text")
    .attr("x", 10)
    .attr("y", 20)
    .attr("fill", "#fff")
    .attr("font-size", 14)
    .attr("font-weight", 600)
    .text((n) => (n.comment ? `${n.name}（${n.comment}）` : n.name));
  node
    .append("g")
    .attr("font-size", 12)
    .selectAll("text")
    .data((n) => n.columns)
    .join("text")
    .attr("x", 10)
    .attr("y", (c, i) => L.headH + (i + 0.75) * L.rowH)
    .attr("fill", (c) => (c.primary_key ? "#b45309" : "#334155"))
    .text((c) => `${c.primary_key ? "PK " : ""}${c.name}: ${c.type}`);

  return L;
}

if (typeof module !== "undefined") module.exports = { layoutEr, nodeHeight };
