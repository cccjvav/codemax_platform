// ER 图页入口：把 layoutEr 的纯布局结果用 d3 画成 SVG。
//
// d3 从 npm 打进产物，**不再走 jsdelivr CDN**（TD-221 / TD-222）。理由有两条：
// ① 供应链：CDN 是运行时取代码，本站任何一次部署都管不到它；打进产物后代码
//    随部署走，版本由 package-lock.json 钉死（含 integrity 哈希）。
// ② 可达性：这是引流页，CDN 在国内不保证可达，d3 加载失败等于整页白屏。
// 代价是产物变大，但 d3 被 tree-shaking 到只剩用到的部分（实测约 49 kB）。
import * as d3 from "d3";

import { initialView, layoutEr } from "./er-layout.js";

// 渲染只画 layoutEr 算好的东西：坐标、折线、截断后的文字都来自纯布局（可在 node 里测试）。
// 这里负责 SVG 细节：箭头、悬停提示（截断时给出完整文字）、悬停时高亮一张表的全部关系。
function renderEr(selector, graph) {
  const L = layoutEr(graph);
  const el = document.querySelector(selector);
  // viewBox 等于画布自身像素尺寸（1 单位 = 1 CSS px），缩放全交给 d3.zoom 的变换 ——
  // 这样「可读比例」有确定含义，也能在全图与原尺寸之间切换（initialView 决定起点）。
  const vw = el.clientWidth || 800;
  const vh = el.clientHeight || 600;
  const svg = d3.select(el).attr("viewBox", [0, 0, vw, vh]).html("");
  // 读屏用户拿不到图形内容，至少告诉他生成了什么（表与关系的数量）
  svg.attr("aria-label", `ER 图：${L.nodes.length} 张表、${L.links.length} 条外键关系`);
  // 箭头指向被引用的父表（外键 → 主键）。markerUnits=userSpaceOnUse：缩放时箭头随图一起缩放。
  svg
    .append("defs")
    .append("marker")
    .attr("id", "er-arrow")
    .attr("viewBox", "0 0 10 10")
    .attr("refX", 10)
    .attr("refY", 5)
    .attr("markerWidth", 9)
    .attr("markerHeight", 9)
    .attr("markerUnits", "userSpaceOnUse")
    .attr("orient", "auto")
    .append("path")
    .attr("d", "M0,0 L10,5 L0,10 z")
    .attr("fill", "context-stroke");
  const root = svg.append("g");
  const auto = initialView(L, vw, vh);
  const zoom = d3
    .zoom()
    .scaleExtent([Math.min(0.2, auto.fitK), 3])
    .on("zoom", (ev) => root.attr("transform", ev.transform));
  const show = (view) => svg.call(zoom.transform, d3.zoomIdentity.translate(view.x, view.y).scale(view.k));
  svg.call(zoom);
  show(auto);
  // 「查看全图 / 回到可读尺寸」只在整图放不下可读比例时出现；全图本来就可读就没必要切换
  const tools = document.getElementById("er-tools");
  const fitBtn = document.getElementById("er-fit");
  if (tools && fitBtn) {
    tools.hidden = false;
    fitBtn.hidden = auto.fits;
    let fitted = false;
    fitBtn.textContent = "查看全图";
    fitBtn.onclick = () => {
      fitted = !fitted;
      show(initialView(L, vw, vh, fitted ? "fit" : "readable"));
      fitBtn.textContent = fitted ? "回到可读尺寸" : "查看全图";
    };
  }

  const links = root
    .append("g")
    .attr("fill", "none")
    .attr("stroke", "#94a3b8")
    .attr("stroke-width", 1.5)
    .selectAll("path")
    .data(L.links)
    .join("path")
    .attr("d", (l) => l.path)
    .attr("marker-end", "url(#er-arrow)");
  // 关系名不再画在线中间（折线的中点常落在通道里、与别的线重叠）：外键列在表内标 FK，
  // 完整的「子表.列 → 父表.列」放在悬停提示里。
  links.append("title").text((l) => `${l.from}.${l.fromColumn} → ${l.to}.${l.toColumn}`);

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
    .attr("stroke", "#2563eb");
  node
    .append("rect")
    .attr("width", (n) => n.w)
    .attr("height", L.headH)
    .attr("rx", 6)
    // 表头是白色 14px 文字：#3b82f6 只有 3.68:1，换成站内统一的 #2563eb（5.17:1，TD-263 同款）
    .attr("fill", "#2563eb");
  const head = node
    .append("text")
    .attr("x", 10)
    .attr("y", 20)
    .attr("fill", "#fff")
    .attr("font-size", 14)
    .attr("font-weight", 600)
    .text((n) => n.header);
  head.filter((n) => n.header !== n.headerFull).append("title").text((n) => n.headerFull);
  const rows = node
    .append("g")
    .attr("font-size", 12)
    .selectAll("text")
    .data((n) => n.rows)
    .join("text")
    .attr("x", 10)
    .attr("y", (r, i) => L.headH + (i + 0.72) * L.rowH)
    // PK 琥珀色、FK 蓝色、普通列深灰；三色对白底都 ≥ 4.5:1
    .attr("fill", (r) => (r.pk ? "#b45309" : r.fk ? "#1d4ed8" : "#334155"))
    .text((r) => r.text);
  rows.filter((r) => r.text !== r.full).append("title").text((r) => r.full);

  // 悬停一张表：它的所有关系线加深，其余线变淡 —— 表多时能看清「谁引用了谁」
  node
    .on("mouseenter", (ev, n) => {
      links
        .attr("stroke", (l) => (l.from === n.name || l.to === n.name ? "#1d4ed8" : "#cbd5e1"))
        .attr("stroke-width", (l) => (l.from === n.name || l.to === n.name ? 2.5 : 1.2));
    })
    .on("mouseleave", () => links.attr("stroke", null).attr("stroke-width", null));

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
      // 非 JSON 应答（代理 502/504 的 HTML 页）按 null 交给 errorText，显示「请求失败（状态码）」
      const data = await res.json().catch(() => null);
      return fail(window.CodeMaxAuth.errorText(data, res.status));
    }
    const url = URL.createObjectURL(await res.blob());
    const a = document.createElement("a");
    a.href = url;
    a.download = "data_dictionary.docx";
    a.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
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
    const data = await res.json().catch(() => null);
    if (!res.ok) return fail(window.CodeMaxAuth.errorText(data, res.status));
    if (!data) return fail("渲染失败：服务器返回的不是 JSON");
    renderEr("#er-canvas", data);
  } catch (e) {
    fail(`渲染失败：${e.message}`);
  } finally {
    submit.disabled = false;
  }
};
