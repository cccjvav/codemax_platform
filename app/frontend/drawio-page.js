// Drawio is an external editor. Fresh XML is requested via correlated export messages,
// never assumed to be the last autosave. Private document changes invalidate pending work.
(() => {
  const ORIGIN = "https://embed.diagrams.net";
  const BLANK = '<mxfile><diagram name="Page-1"><mxGraphModel><root><mxCell id="0"/><mxCell id="1" parent="0"/></root></mxGraphModel></diagram></mxfile>';
  let frame = document.getElementById("drawio-frame");
  const status = document.getElementById("drawio-status");
  const authStatus = document.getElementById("auth-status");
  const name = document.getElementById("diagram-name");
  const list = document.getElementById("diagram-list");
  let currentId = null, xml = BLANK, etag = null, epoch = 0, sequence = 0, listSeq = 0;
  let identity, loggedIn = false, ready = false, saving = false, pending = null, loading = false;

  function send(message) { frame.contentWindow.postMessage(JSON.stringify(message), ORIGIN); }
  function load() { if (loading) return; send({ action: "load", xml, autosave: 1 }); }
  function validXml(value) {
    if (typeof value !== "string" || value.length > 500000 || !value.trim()) throw new Error("XML 内容为空或超过上限");
    if (/<!DOCTYPE/i.test(value)) throw new Error("不支持带 DOCTYPE 的 XML");
    const document = new DOMParser().parseFromString(value, "application/xml");
    if (document.querySelector("parsererror") || !["mxfile", "mxGraphModel"].includes(document.documentElement.nodeName)) throw new Error("不是有效的 Drawio XML");
    return value;
  }
  function resetEditor() {
    ++epoch; ready = false; saving = false;
    if (pending) { clearTimeout(pending.timer); pending.reject(new Error("文档已切换")); pending = null; }
    // A new browsing context makes messages from the previous account/document rejectable by source.
    if (frame.cloneNode && frame.parentNode) {
      const next = frame.cloneNode(false); frame.parentNode.replaceChild(next, frame); frame = next;
    }
  }
  function exportXml() {
    if (!ready) return Promise.reject(new Error("编辑器尚未就绪，请稍后重试"));
    if (pending) return Promise.reject(new Error("正在读取编辑器，请稍候"));
    return new Promise((resolve, reject) => {
      const id = ++sequence;
      pending = { id, epoch, resolve, reject, timer: setTimeout(() => {
        if (pending?.id === id) { pending = null; reject(new Error("编辑器未响应，未保存，请重试")); }
      }, 10000) };
      send({ action: "export", format: "xml", requestId: id });
    });
  }
  async function api(url, method = "GET", body, headers = {}) {
    const response = await fetch(url, { method, credentials: "same-origin",
      headers: { "Content-Type": "application/json", ...headers }, body: body ? JSON.stringify(body) : undefined });
    const data = response.status === 204 ? null : await response.json();
    if (!response.ok) {
      if (response.status === 412) throw new Error("云端已有新版本，本次未覆盖。请重新打开或先下载本地副本");
      throw new Error(CodeMaxAuth.errorText?.(data, response.status) || `请求失败（${response.status}）`);
    }
    return { data, etag: response.headers?.get("ETag") };
  }
  window.addEventListener("message", (event) => {
    if (event.origin !== ORIGIN || event.source !== frame.contentWindow) return;
    let message;
    try { message = JSON.parse(event.data); } catch { return; }
    if (!message || typeof message !== "object") return;
    if (message.event === "init") { load(); return; }
    if (message.event === "load") { ready = !loading; return; }
    if (message.event === "export" && pending && message.message?.requestId === pending.id && pending.epoch === epoch) {
      const job = pending; pending = null; clearTimeout(job.timer);
      try { xml = validXml(message.xml); job.resolve(xml); } catch (e) { job.reject(e); }
    }
    if (message.event === "autosave" && ready && typeof message.xml === "string") xml = message.xml;
    if (message.event === "save" && ready) save();
  });
  async function refreshList() {
    if (!loggedIn) return;
    const stamp = epoch, serial = ++listSeq;
    try {
      const { data } = await api("/diagrams");
      if (stamp !== epoch || serial !== listSeq) return;
      if (!Array.isArray(data)) throw new Error("流程图列表格式无效");
      list.innerHTML = '<option value="">— 我的流程图 —</option>';
      for (const row of data) {
        const option = document.createElement("option"); option.value = row.id; option.textContent = row.name; list.appendChild(option);
      }
      if (currentId) list.value = String(currentId);
    } catch (e) { if (stamp === epoch) status.textContent = e.message; }
  }
  async function save() {
    if (!loggedIn) { status.textContent = "未登录，未保存"; return; }
    if (saving) return;
    const stamp = epoch; saving = true;
    try {
      const content = await exportXml();
      if (stamp !== epoch) return;
      const result = await api(currentId ? `/diagrams/${currentId}` : "/diagrams", currentId ? "PUT" : "POST",
        { name: name.value.trim() || "未命名流程图", content }, currentId ? { "If-Match": etag } : {});
      if (stamp !== epoch) return;
      if (!result.data?.id || !result.etag) throw new Error("保存响应缺少标识或版本，请刷新列表确认");
      currentId = result.data.id; etag = result.etag;
      status.textContent = `已保存到云端（#${currentId}）${xml !== content ? "，编辑器还有新改动" : ""}`;
      await refreshList();
    } catch (e) { if (stamp === epoch) status.textContent = `未保存：${e.message}`; }
    finally { if (stamp === epoch) saving = false; }
  }
  document.getElementById("btn-save").onclick = save;
  list.onchange = async () => {
    if (!list.value) return;
    const id = list.value; loading = true; resetEditor(); const stamp = epoch;
    try {
      const { data, etag: tag } = await api(`/diagrams/${id}`);
      if (stamp !== epoch) return;
      xml = validXml(data.content); currentId = data.id; etag = tag; name.value = data.name;
      // The iframe may have initialized during the fetch. Restart with the fetched document only.
      loading = false; resetEditor(); status.textContent = `已打开 #${data.id}`;
    } catch (e) { if (stamp === epoch) { loading = false; resetEditor(); status.textContent = e.message; } }
  };
  document.getElementById("btn-new").onclick = () => {
    loading = false; xml = BLANK; currentId = null; etag = null; name.value = ""; list.value = ""; resetEditor(); status.textContent = "已新建空白流程图";
  };
  document.getElementById("btn-download").onclick = async () => {
    const stamp = epoch;
    try {
      const content = await exportXml(); if (stamp !== epoch) return;
      const url = URL.createObjectURL(new Blob([content], { type: "application/xml" }));
      const a = document.createElement("a"); a.href = url; a.download = `${name.value.trim() || "diagram"}.drawio`; a.click();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (e) { if (stamp === epoch) status.textContent = e.message; }
  };
  document.getElementById("btn-import").onclick = () => document.getElementById("file-input").click();
  document.getElementById("file-input").onchange = async (event) => {
    const file = event.target.files[0]; if (!file) return;
    const stamp = epoch;
    try {
      if (file.size > 500000) throw new Error("文件超过 500000 字节，请缩小后导入");
      const content = validXml(await file.text()); if (stamp !== epoch) return;
      xml = content; currentId = null; etag = null; name.value = file.name.replace(/\.(drawio|xml)$/i, "");
      resetEditor(); status.textContent = `已导入：${file.name}`;
    } catch (e) { if (stamp === epoch) status.textContent = e.message; }
    finally { event.target.value = ""; }
  };
  document.getElementById("btn-login").onclick = () => CodeMaxAuth.open("login");
  async function syncAuth(user) {
    const next = user?.username || null;
    loggedIn = !!user; authStatus.textContent = user ? "已登录，可保存到云端" : "未登录，图只能在本地画";
    if (next === identity) return;
    if (identity) xml = BLANK; // guest work may be kept on first login, account-owned work never is
    document.getElementById("diagram-manage").innerHTML = "";
    loading = false; identity = next; currentId = null; etag = null; name.value = ""; list.innerHTML = ""; resetEditor();
    if (user) await refreshList();
  }
  async function manage() {
    if (!loggedIn) { CodeMaxAuth.open("login"); return; }
    const stamp = epoch;
    try {
      const [live, trash] = await Promise.all([api("/diagrams"), api("/diagrams?deleted=true")]);
      if (stamp !== epoch) return;
      const box = document.getElementById("diagram-manage"); box.innerHTML = "";
      for (const [rows, deleted] of [[live.data, false], [trash.data, true]]) {
        for (const row of rows) {
          const li = document.createElement("li"); li.textContent = `${row.name} · ${deleted ? "回收站" : "云端"} `;
          function action(label, path, method, permanent = false) {
            const button = document.createElement("button"); button.type = "button"; button.textContent = label;
            button.onclick = async () => {
              if (permanent && !window.confirm("永久删除后无法恢复，确定吗？")) return;
              const actionEpoch = epoch;
              try {
                await api(path, method, undefined, permanent ? { "If-Match": `"${row.version}"` } : {});
                if (actionEpoch !== epoch) return;
                if (!deleted && currentId === row.id) {
                  xml = BLANK; currentId = null; etag = null; name.value = ""; resetEditor();
                }
                status.textContent = `${label}成功`;
                await refreshList(); await manage();
              } catch (e) { if (actionEpoch === epoch) status.textContent = e.message; }
            };
            li.appendChild(button);
          }
          action(deleted ? "恢复" : "移至回收站", `/diagrams/${row.id}${deleted ? "/restore" : ""}`, deleted ? "POST" : "DELETE");
          if (deleted) action("永久删除", `/diagrams/${row.id}/purge`, "DELETE", true);
          box.appendChild(li);
        }
      }
    } catch (e) { if (stamp === epoch) status.textContent = e.message; }
  }
  document.getElementById("btn-manage").onclick = manage;
  CodeMaxAuth.onChange(syncAuth); syncAuth(CodeMaxAuth.user);
})();
