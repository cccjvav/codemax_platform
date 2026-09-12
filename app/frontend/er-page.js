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

// ---------------------------------------------------------------- 页面装配
//
// 这一段原先是 er.html 里的内联 <script>（C2 搬进来的）。搬进来有两个好处：
// ① 不再需要 `window.renderEr` 这个全局 —— 同模块内直接调用即可，
//    少一个挂在 window 上的可被任意脚本覆写的名字；
// ② 内联脚本要么放宽 CSP 的 'unsafe-inline'，要么逐页配 nonce（TD-163 的方向），
//    外部文件则由 `script-src 'self'` 直接覆盖。
//
// ⚠️ 模块脚本默认 defer，执行时 DOM 已解析完，所以这里可以直接取元素 ——
//    与原内联脚本「放在 body 末尾」的时机等价，不必再包 DOMContentLoaded。

const SAMPLE = [
  "CREATE TABLE sys_user (",
  "  id SERIAL PRIMARY KEY,",
  "  username VARCHAR(50) NOT NULL COMMENT '登录名'",
  ");",
  "CREATE TABLE sys_order (",
  "  id SERIAL PRIMARY KEY,",
  "  user_id INT NOT NULL REFERENCES sys_user(id),",
  "  amount NUMERIC(10,2)",
  ");",
].join("\n");

const form = document.getElementById("er-form");
const input = document.getElementById("ddl-input");
const error = document.getElementById("er-error");
const submit = document.getElementById("er-submit");

function post(url, payload) {
  return fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
}

function fail(msg) {
  error.hidden = false;
  error.textContent = msg;
}

document.getElementById("er-sample").onclick = () => {
  input.value = SAMPLE;
};

document.getElementById("er-word").onclick = async () => {
  error.hidden = true;
  try {
    const res = await post("/tools/word-export", { ddl: input.value });
    if (!res.ok) {
      const data = await res.json();
      return fail(window.CodeMaxAuth.errorText(data, res.status));
    }
    const url = URL.createObjectURL(await res.blob());
    const a = document.createElement("a");
    a.href = url;
    a.download = "data_dictionary.docx";
    a.click();
    URL.revokeObjectURL(url);
  } catch (e) {
    fail(`导出失败：${e.message}`);
  }
};

form.onsubmit = async (ev) => {
  ev.preventDefault();
  error.hidden = true;
  submit.disabled = true;
  try {
    const res = await post("/tools/er-diagram", { ddl: input.value });
    const data = await res.json();
    if (!res.ok) return fail(window.CodeMaxAuth.errorText(data, res.status));
    renderEr("#er-canvas", data);
  } catch (e) {
    fail(`渲染失败：${e.message}`);
  } finally {
    submit.disabled = false;
  }
};
