// drawio-page 页面的交互脚本。
//
// 原先是 drawio.html 里的内联 <script>，C2 搬到这里。搬出来的理由：
// ① 内联脚本要么放宽 CSP 的 'unsafe-inline'，要么逐页配 nonce（TD-163 的方向），
//    外部文件由 `script-src 'self'` 直接覆盖；
// ② 内联在模板里的 JS 无法被构建工具处理（不能压缩、不能拆分、报错没有源文件行号）。
//
// ⚠️ 模块脚本默认 defer，执行时 DOM 已解析完，可直接取元素。

const ORIGIN = "https://embed.diagrams.net";
const BLANK = '<mxfile><diagram name="Page-1"><mxGraphModel><root><mxCell id="0"/><mxCell id="1" parent="0"/></root></mxGraphModel></diagram></mxfile>';

const frame = document.getElementById("drawio-frame");
const status = document.getElementById("drawio-status");
const authStatus = document.getElementById("auth-status");
const nameInput = document.getElementById("diagram-name");
const list = document.getElementById("diagram-list");

let currentId = null; // null = 尚未保存到云端的新图
let currentXml = BLANK;
let etag = null; // 手上这一版的版本号（ETag），保存时带回去做乐观锁（TD-65）

// 登录态由 HttpOnly cookie 携带，脚本**读不到也不需要读** token（TD-44）。
// 因此「是否已登录」只能问后端，见下面的 checkAuth()。
let loggedIn = false;

function send(msg) {
  frame.contentWindow.postMessage(JSON.stringify(msg), ORIGIN);
}

function api(url, method, body, extraHeaders) {
  return fetch(url, {
    method,
    credentials: "same-origin", // 让浏览器带上登录 cookie
    headers: { "Content-Type": "application/json", ...extraHeaders },
    body: body ? JSON.stringify(body) : undefined,
  });
}

// ---- 与 drawio iframe 的 postMessage 协议 ----
window.addEventListener("message", (evt) => {
  if (evt.origin !== ORIGIN || evt.source !== frame.contentWindow) return; // 只认 drawio 的消息
  let msg;
  try {
    msg = JSON.parse(evt.data);
  } catch {
    return;
  }
  if (msg.event === "init") send({ action: "load", xml: currentXml });
  if (msg.event === "save") {
    currentXml = msg.xml;
    saveToCloud();
  }
  if (msg.event === "autosave") currentXml = msg.xml;
});

// ---- 云端保存 ----
async function saveToCloud() {
  if (!loggedIn) {
    status.textContent = "未登录，未保存";
    return;
  }
  const res = await api(
    currentId ? `/diagrams/${currentId}` : "/diagrams",
    currentId ? "PUT" : "POST",
    { name: nameInput.value.trim() || "未命名流程图", content: currentXml },
    currentId ? { "If-Match": etag } : undefined, // 只有改已有的图才需要带版本
  );
  if (res.status === 401) {
    status.textContent = "登录已失效，请重新登录";
    return;
  }
  if (res.status === 412) {
    // 另一个标签页/设备已经改过了。**绝不能自动重试** —— 那正好会把对方的改动盖掉，
    // 乐观锁就白加了。让用户自己决定。
    status.textContent = "云端已有更新的版本，本次未保存 —— 请从列表重新打开后再改";
    return;
  }
  if (!res.ok) {
    // 409（配额用满）后端给了人话，直接显示比一个光秃秃的状态码有用
    const detail = (await res.json().catch(() => null))?.detail;
    status.textContent = detail ? `保存失败：${detail}` : `保存失败（${res.status}）`;
    return;
  }
  const saved = await res.json();
  currentId = saved.id;
  etag = res.headers.get("ETag"); // 服务端已经把版本 +1，换掉手上的旧版本
  status.textContent = `已保存到云端（#${saved.id}）`;
  await refreshList();
}

async function refreshList() {
  if (!loggedIn) return;
  const res = await api("/diagrams", "GET");
  if (!res.ok) return;
  const items = await res.json();
  list.innerHTML = '<option value="">— 我的流程图 —</option>';
  for (const d of items) {
    const opt = document.createElement("option");
    opt.value = d.id;
    opt.textContent = d.name;
    list.appendChild(opt);
  }
  if (currentId) list.value = String(currentId);
}

async function openFromCloud(id) {
  const res = await api(`/diagrams/${id}`, "GET");
  if (!res.ok) {
    status.textContent = `读取失败（${res.status}）`;
    return;
  }
  const d = await res.json();
  currentId = d.id;
  currentXml = d.content;
  etag = res.headers.get("ETag");
  nameInput.value = d.name;
  send({ action: "load", xml: currentXml });
  status.textContent = `已打开 #${d.id}`;
}

// ---- 本地保存 / 导入 ----
document.getElementById("btn-download").onclick = () => {
  const url = URL.createObjectURL(new Blob([currentXml], { type: "application/xml" }));
  const a = document.createElement("a");
  a.href = url;
  a.download = `${nameInput.value.trim() || "diagram"}.drawio`;
  a.click();
  URL.revokeObjectURL(url);
};

document.getElementById("btn-import").onclick = () => document.getElementById("file-input").click();
document.getElementById("file-input").onchange = async (ev) => {
  const file = ev.target.files[0];
  if (!file) return;
  currentXml = await file.text();
  currentId = null;
  nameInput.value = file.name.replace(/\.(drawio|xml)$/i, "");
  send({ action: "load", xml: currentXml });
  status.textContent = `已导入本地文件：${file.name}`;
  ev.target.value = ""; // 不清空的话，连续导入同一个文件不会再触发 change
};

document.getElementById("btn-save").onclick = saveToCloud;
document.getElementById("btn-new").onclick = () => {
  currentId = null;
  currentXml = BLANK;
  nameInput.value = "";
  list.value = "";
  send({ action: "load", xml: currentXml });
  status.textContent = "已新建空白流程图";
};
list.onchange = () => list.value && openFromCloud(list.value);

// 登录/退出都在顶栏的全站浮层里（S2-02-2），本页只负责唤起与响应状态变化。
document.getElementById("btn-login").onclick = () => CodeMaxAuth.open("login");

function setAuthState(on) {
  loggedIn = on;
  authStatus.textContent = on ? "已登录，可保存到云端" : "未登录，图只能在本地画";
}

// 「是否已登录」由 CodeMaxAuth 统一问后端（GET /auth/me）——
// 登录态在 HttpOnly cookie 里脚本读不到（TD-44），所以只能问后端。
// 这里不再自己 fetch /auth/me：原来本页与顶栏各问一次，两处状态各管各的，
// 容易出现一个显示已登录另一个没显示。
CodeMaxAuth.onChange(async (u) => {
  setAuthState(!!u);
  if (u) await refreshList();
});
