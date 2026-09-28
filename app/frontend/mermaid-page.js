// mermaid-page 页面的交互脚本。
//
// 原先是 mermaid.html 里的内联 <script>，C2 搬到这里。搬出来的理由：
// ① 内联脚本要么放宽 CSP 的 'unsafe-inline'，要么逐页配 nonce（TD-163 的方向），
//    外部文件由 `script-src 'self'` 直接覆盖；
// ② 内联在模板里的 JS 无法被构建工具处理（不能压缩、不能拆分、报错没有源文件行号）。
//
// ⚠️ 模块脚本默认 defer，执行时 DOM 已解析完，可直接取元素。

// Mermaid 按需加载（V-06 / TD-280）：它和依赖分块压缩后约 650 KiB，原来随页面一起下载，
// 只看一眼页面、或模型没配置（生成必然失败）的访客也要付这笔流量。现在点「生成类图」时才 import，
// 并且与模型请求**同时**开始下载 —— 模型生成要好几秒，渲染器通常在回复到达前就已就绪。
// 仍是本地 npm 包（package-lock 锁定），由 Vite 拆成同源分块，不走 CDN。
let mermaidLoading = null;
function loadMermaid() {
  mermaidLoading ||= import("mermaid").then(({ default: mermaid }) => {
    // securityLevel 必须是 "strict"（原先写的是 "loose"）。
    // loose 允许图定义里带 HTML 标签与点击回调（click nodeId href ...）。
    // 而这里的 mermaid 文本是 **LLM 生成的** —— 等于把一段不可信输入交给一个
    // 「允许内嵌 HTML 与跳转」的渲染器，在本站同源下执行。
    // strict 会转义标签、禁用点击交互，正是这种场景该有的档位。
    // suppressErrorRendering（TD-318）：画不出来时不要往预览区塞 Mermaid 自己的「Syntax error in text」炸弹图，
    // 由本页给出中文说明（renderFailure）。不设时它先画错误图再抛异常，两份错误叠在一起。
    mermaid.initialize({ startOnLoad: false, securityLevel: "strict", suppressErrorRendering: true });
    return mermaid;
  });
  // 下载失败（断网、部署途中旧分块被替换）不能把失败的 promise 永久缓存，否则再点也不会重试
  mermaidLoading.catch(() => { mermaidLoading = null; });
  return mermaidLoading;
}

const SAMPLE = "电商系统里有用户、商品、订单，一个用户可以下多个订单，订单包含多个订单项";
const form = document.getElementById("mermaid-form");
const input = document.getElementById("text-input");
const error = document.getElementById("mermaid-error");
const source = document.getElementById("mermaid-source");
const preview = document.getElementById("mermaid-preview");
const submit = document.getElementById("mermaid-submit");
const failure = (e) => window.CodeMaxAuth?.failureText?.(e) ?? e.message; // TD-315：网络层失败显示中文，其余错误原样显示（见 auth.js 的 failureText）

document.getElementById("mermaid-sample").onclick = () => {
  input.value = SAMPLE;
};

function fail(msg) {
  error.hidden = false;
  error.textContent = msg;
}

// 新一次生成失败时（TD-326），源码框与预览区里可能还是上一次的结果，和输入框里现在的描述对不上，
// 原来照常显示、看不出已经过时。不清掉它：模型生成耗配额、再生成结果也不同，一次网络抖动就丢掉一份
// 好结果不划算。改为调暗（data-stale，样式见 base.html）并在错误提示里说明；下次成功生成时恢复。
// 「有没有上一次的结果」用显式状态 hasResult 记（拿到模型源码时置位），不从元素的 hidden 反推。
// setAttribute 用可选调用：部分测试替身的元素没有这个方法（与本仓库其他页面脚本同一约定）。
let hasResult = false;
function setStale(on) {
  for (const el of [source, preview]) {
    if (on) el.setAttribute?.("data-stale", "");
    else el.removeAttribute?.("data-stale");
  }
}

function failKeepingPrevious(msg) {
  setStale(hasResult);
  fail(hasResult ? `${msg.replace(/[。.]$/, "")}；当前显示的仍是上一次的结果` : msg);
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

// 模型给出的源码 Mermaid 画不出来（多半是语法错误：服务端只核对了首行的图类型）。解析器的英文提示
// 只取第一行作线索（例如「Parse error on line 3:」），后面几行是指向出错位置的字符画，放进一行提示里没法读（TD-318）。
function renderFailure(e) {
  const hint = String(failure(e) ?? "").split("\n")[0].trim().slice(0, 120);
  return `无法绘制模型生成的图${hint ? `（Mermaid 提示：${hint}）` : ""}。下方是模型给出的源码，可以调整描述后重新生成，或复制源码自行修改。`;
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
  const renderer = loadMermaid();
  // 出错时按阶段说：请求阶段（含网络中断）是「请求失败」，只有渲染阶段才是「渲染失败」（TD-318）
  let stage = "request";
  try {
    const { res, data } = await requestDiagram(input.value);
    if (!res.ok) return failKeepingPrevious(serverMessage(res, data));
    // 200 却不是 JSON（代理的 HTML 页）：原来在下面读 data.mermaid 时才抛 TypeError，源码框已被打开成空白（TD-326，与 ER 页一致）
    if (!data) return failKeepingPrevious("请求失败：服务器返回的不是 JSON");
    error.hidden = true;
    setStale(false);
    // 先给出 Mermaid 源码（在错误提示下方）：就算渲染器下载失败，用户也拿到了结果，可以复制到别处渲染
    source.hidden = false;
    source.textContent = data.mermaid;
    hasResult = true;
    let mermaid;
    try {
      mermaid = await renderer;
    } catch {
      return fail("图表渲染组件下载失败（网络中断或站点刚更新），下方是生成的 Mermaid 源码；请刷新页面后重试。");
    }
    stage = "render";
    preview.removeAttribute("data-processed");
    preview.textContent = data.mermaid;
    await mermaid.run({ nodes: [preview] });
  } catch (e) {
    if (stage === "render") {
      preview.textContent = "";  // 画失败时预览区里还是刚放进去的源码原文，与源码框重复
      fail(renderFailure(e));
    } else {
      failKeepingPrevious(`请求失败：${failure(e)}`);
    }
  } finally {
    submit.disabled = false;
  }
};
