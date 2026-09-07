// ER 图页入口：把 layoutEr 的纯布局结果用 d3 画成 SVG。
//
// d3 从 npm 打进产物，**不再走 jsdelivr CDN**（TD-221 / TD-222）。理由有两条：
// ① 供应链：CDN 是运行时取代码，本站任何一次部署都管不到它；打进产物后代码
//    随部署走，版本由 package-lock.json 钉死（含 integrity 哈希）。
// ② 可达性：这是引流页，CDN 在国内不保证可达，d3 加载失败等于整页白屏。
// 代价是产物变大，但 d3 被 tree-shaking 到只剩用到的部分（实测约 49 kB）。
import * as d3 from "d3";

import { layoutEr } from "./er-layout.js";

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

// er.html 的内联脚本仍按全局函数调用 renderEr（经典脚本时代的写法）。
// 打成 ES 模块后顶层函数不再是全局，所以显式挂一次。C2 会把那段内联脚本也搬进来，
// 届时这个全局就可以去掉。
window.renderEr = renderEr;
