// mermaid-page 页面的交互脚本。
//
// 原先是 mermaid.html 里的内联 <script>，C2 搬到这里。搬出来的理由：
// ① 内联脚本要么放宽 CSP 的 'unsafe-inline'，要么逐页配 nonce（TD-163 的方向），
//    外部文件由 `script-src 'self'` 直接覆盖；
// ② 内联在模板里的 JS 无法被构建工具处理（不能压缩、不能拆分、报错没有源文件行号）。
//
// ⚠️ 模块脚本默认 defer，执行时 DOM 已解析完，可直接取元素。

import mermaid from "https://cdn.jsdelivr.net/npm/mermaid@11.17.2/dist/mermaid.esm.min.mjs";
// securityLevel 必须是 "strict"（原先写的是 "loose"）。
// loose 允许图定义里带 HTML 标签与点击回调（click nodeId href ...）。
// 而这里的 mermaid 文本是 **LLM 生成的** —— 等于把一段不可信输入交给一个
// 「允许内嵌 HTML 与跳转」的渲染器，在本站同源下执行。
// strict 会转义标签、禁用点击交互，正是这种场景该有的档位。
mermaid.initialize({ startOnLoad: false, securityLevel: "strict" });

const SAMPLE = "电商系统里有用户、商品、订单，一个用户可以下多个订单，订单包含多个订单项";
const form = document.getElementById("mermaid-form");
const input = document.getElementById("text-input");
const error = document.getElementById("mermaid-error");
const source = document.getElementById("mermaid-source");
const preview = document.getElementById("mermaid-preview");
const submit = document.getElementById("mermaid-submit");

document.getElementById("mermaid-sample").onclick = () => {
  input.value = SAMPLE;
};

function fail(msg) {
  error.hidden = false;
  error.textContent = msg;
}

form.onsubmit = async (ev) => {
  ev.preventDefault();
  error.hidden = true;
  submit.disabled = true;
  try {
    const res = await fetch("/tools/mermaid", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text: input.value }),
    });
    const data = await res.json();
    if (!res.ok) return fail(data.detail || `请求失败（${res.status}）`);
    source.hidden = false;
    source.textContent = data.mermaid;
    preview.removeAttribute("data-processed");
    preview.textContent = data.mermaid;
    await mermaid.run({ nodes: [preview] });
  } catch (e) {
    fail(`渲染失败：${e.message}`);
  } finally {
    submit.disabled = false;
  }
};
