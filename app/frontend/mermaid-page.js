// mermaid-page 页面的交互脚本。
//
// 原先是 mermaid.html 里的内联 <script>，C2 搬到这里。搬出来的理由：
// ① 内联脚本要么放宽 CSP 的 'unsafe-inline'，要么逐页配 nonce（TD-163 的方向），
//    外部文件由 `script-src 'self'` 直接覆盖；
// ② 内联在模板里的 JS 无法被构建工具处理（不能压缩、不能拆分、报错没有源文件行号）。
//
// ⚠️ 模块脚本默认 defer，执行时 DOM 已解析完，可直接取元素。

import mermaid from "mermaid";
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

// 服务端把「本站没配 LLM_API_KEY」原样返回（那是给运维看的），对访客是开发者术语。
// 只改这一条的**展示**文案，不改服务端 detail（运维/测试仍按原文判断），也不隐藏其它错误。
function serverMessage(res, data) {
  const text = window.CodeMaxAuth.errorText(data, res.status);
  const detail = typeof data?.detail === "string" ? data.detail : "";
  if (res.status === 502 && detail.includes("LLM_API_KEY")) {
    return "AI 生成暂不可用（站点未开启模型服务）；可以先使用「SQL DDL 转 ER 图」，或在站内客服留言。";
  }
  return text;
}

// 503 = 本站模型并发闸门已满（TD-264），带 Retry-After；不是上游故障，等几秒再试通常就能成功。
// 只自动重试一次：第二次仍繁忙就把服务端文案原样给用户，不无限打转。
const MAX_BUSY_RETRIES = 1;
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

async function requestDiagram(text) {
  for (let attempt = 0; ; attempt++) {
    const res = await fetch("/tools/mermaid", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text }),
    });
    const data = await res.json().catch(() => null);
    if (res.status === 503 && attempt < MAX_BUSY_RETRIES) {
      const wait = Math.min(Math.max(Number(res.headers.get("Retry-After")) || 5, 1), 30);
      fail(`服务繁忙，${wait} 秒后自动重试…`);
      await sleep(wait * 1000);
      continue;
    }
    return { res, data };
  }
}

form.onsubmit = async (ev) => {
  ev.preventDefault();
  error.hidden = true;
  submit.disabled = true;
  try {
    const { res, data } = await requestDiagram(input.value);
    if (!res.ok) return fail(serverMessage(res, data));
    error.hidden = true;
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
