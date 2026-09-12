/* codemax_platform 文档站交互脚本
 *
 * **刻意零依赖**：不用 marked / highlight.js / mermaid / d3，也不用任何 CDN。
 * Markdown 由 Python/Mistune 预渲染，依赖图由 Python 生成 SVG。
 * 业务 Mermaid 已本地打包；本独立文档站仍不需要加载它。
 */

(function () {
  "use strict";
  const $ = (s, r) => (r || document).querySelector(s);
  const $$ = (s, r) => Array.prototype.slice.call((r || document).querySelectorAll(s));

  /* ------------------------------------------------------------ 搜索
   * 索引是构建时生成的 data/search-index.js（只含标题与小节，体积小）。
   * 正文搜索交给浏览器自带的 Ctrl+F —— 自己做全文索引会让站点体积翻倍，收益不值。
   */
  const SITE_ROOT = new URL(".", document.currentScript.src);
  let searchSequence = 0;
  async function loadIndex() { return window.CODEMAX_DOC_SEARCH || []; }

  async function doSearch(q) {
    const stamp = ++searchSequence;
    const box = $("#hits");
    if (!box) return;
    if (!q || q.length < 2) { box.innerHTML = ""; return; }
    const idx = await loadIndex();
    if (stamp !== searchSequence) return;
    const ql = q.toLowerCase();
    const out = [];
    for (const d of idx) {
      if (d.t.toLowerCase().includes(ql)) out.push({ h: d.h, a: "", label: d.t, sub: d.p, score: 0 });
      for (const s of d.s) {
        if (s.t.toLowerCase().includes(ql)) {
          out.push({ h: d.h, a: s.i, label: s.t, sub: d.p, score: 1 });
        }
      }
    }
    out.sort((x, y) => x.score - y.score);
    box.innerHTML = out.length
      ? out.slice(0, 14).map(o =>
          `<a class="hit" href="${esc(new URL(o.h, SITE_ROOT).href)}${o.a ? "#" + o.a : ""}"><b>${esc(o.label)}</b><br><span>${esc(o.sub)}</span></a>`
        ).join("")
      : "无命中";
  }

  /* ------------------------------------------------------------ 源码页：高亮目标行 */
  function highlightHashLine() {
    if (!location.hash.startsWith("#L")) return;
    const el = document.getElementById(location.hash.slice(1));
    if (!el) return;
    el.classList.add("hl");
    const end = Number(new URL(location.href).searchParams.get("end"));
    const start = Number(location.hash.slice(2));
    for (let line = start + 1; line <= Math.min(end, start + 10000); line++) document.getElementById(`L${line}`)?.classList.add("hl");
    el.scrollIntoView({ block: "center" });
  }

  /* ------------------------------------------------------------ 依赖图：悬停高亮 */
  function wireGraph() {
    const svg = $("#depgraph");
    if (!svg) return;
    const nodes = $$(".node", svg);
    const edges = $$(".edge", svg);
    nodes.forEach(n => {
      n.addEventListener("mouseenter", () => {
        const id = n.dataset.id;
        const rel = new Set([id]);
        edges.forEach(e => {
          const on = e.dataset.f === id || e.dataset.t === id;
          e.classList.toggle("hot", on);
          e.classList.toggle("dim", !on);
          if (on) { rel.add(e.dataset.f); rel.add(e.dataset.t); }
        });
        nodes.forEach(m => m.classList.toggle("dim", !rel.has(m.dataset.id)));
      });
      n.addEventListener("mouseleave", () => {
        edges.forEach(e => e.classList.remove("hot", "dim"));
        nodes.forEach(m => m.classList.remove("dim"));
      });
    });
    const q = $("#gq");
    if (q) q.addEventListener("input", () => {
      const v = q.value.trim().toLowerCase();
      nodes.forEach(n => n.classList.toggle("dim", !!v && !n.dataset.id.toLowerCase().includes(v)));
      edges.forEach(e => e.classList.toggle("dim", !!v));
    });
    const rs = $("#greset");
    if (rs) rs.onclick = () => {
      q.value = "";
      nodes.forEach(n => n.classList.remove("dim"));
      edges.forEach(e => e.classList.remove("dim", "hot"));
    };
  }

  /* ------------------------------------------------------------ 路由地图过滤 */
  function wireRoutes() {
    const body = $("#rbody");
    if (!body) return;
    const rows = $$("tr", body);
    const draw = () => {
      const m = $("#fm").value, a = $("#fa").checked, r = $("#fr").checked, q = $("#fq").value.trim();
      let n = 0;
      rows.forEach(tr => {
        const ok = (!m || tr.dataset.m === m)
          && (!a || tr.dataset.auth === "1")
          && (!r || tr.dataset.rl === "1")
          && (!q || tr.dataset.p.includes(q));
        tr.style.display = ok ? "" : "none";
        if (ok) n++;
      });
      $("#fstat").textContent = n + " / " + rows.length + " 条";
    };
    ["fm", "fa", "fr", "fq"].forEach(id => {
      const el = $("#" + id);
      if (el) el.addEventListener("input", draw);
    });
    draw();
  }

  /* ------------------------------------------------------------ 符号索引过滤 */
  function wireSymbols() {
    const list = $("#slist");
    if (!list) return;
    const cards = $$(".sym-card", list);
    const draw = () => {
      const q = $("#sq").value.trim().toLowerCase(), k = $("#sk").value, pub = $("#sp").checked;
      let n = 0;
      cards.forEach(c => {
        const ok = (!q || c.dataset.n.includes(q) || c.dataset.m.toLowerCase().includes(q))
          && (!k || c.dataset.k === k)
          && (!pub || c.dataset.pub === "1")
          && ($("#st").checked || c.dataset.test !== "1");
        c.style.display = ok ? "" : "none";
        if (ok) n++;
      });
      $("#sstat").textContent = n + " / " + cards.length + " 个";
    };
    ["sq", "sk", "sp", "st"].forEach(id => {
      const el = $("#" + id);
      if (el) el.addEventListener("input", draw);
    });
    draw();
  }

  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"]/g, c =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  }

  document.addEventListener("DOMContentLoaded", () => {
    const q = $("#q");
    if (q) q.addEventListener("input", e => doSearch(e.target.value.trim()));
    highlightHashLine();
    wireGraph();
    wireRoutes();
    wireSymbols();
  });
  window.addEventListener("hashchange", highlightHashLine);
})();
