// Durable customer/admin messaging. Account/conversation epochs discard stale responses.
(() => {
  const auth = window.CodeMaxAuth;
  const el = (id) => document.getElementById(`support-${id}`);
  let epoch = 0, timer, controller = new AbortController(), currentUser = null;
  let target = null, newest = 0, oldest = null, inboxCursor = null, pending = null;
  let polling = false, sending = false;
  const seen = new Set();

  function errorText(data, status) {
    if (typeof data?.detail === "string") return data.detail;
    if (Array.isArray(data?.detail)) return data.detail.map((x) => x.msg || "输入无效").join("；");
    return `请求失败（${status}）`;
  }
  async function request(url, options = {}) {
    const res = await fetch(url, { credentials: "same-origin", signal: controller.signal, ...options });
    const data = await res.json().catch(() => null);
    if (!res.ok) throw new Error(errorText(data, res.status));
    return data;
  }
  function endpoint() {
    return currentUser?.role === 1 && target !== null
      ? `/support/conversations/${target}/messages` : "/support/messages";
  }
  function reset() {
    epoch += 1;
    clearTimeout(timer);
    controller.abort(); controller = new AbortController();
    newest = 0; oldest = null; seen.clear(); polling = false; sending = false; pending = null;
    el("messages").replaceChildren(); el("body").value = ""; el("error").textContent = "";
    el("send").disabled = false; el("older").hidden = true;
  }
  function render(rows, prepend = false) {
    const nodes = [];
    for (const row of rows) {
      if (seen.has(row.id)) continue;
      seen.add(row.id); newest = Math.max(newest, row.id); oldest = Math.min(oldest ?? row.id, row.id);
      const li = document.createElement("li"), meta = document.createElement("small");
      li.className = row.sender_role === 1 ? "admin" : "customer";
      meta.textContent = `${row.sender_role === 1 ? "管理员" : "客户"} · ${new Date(row.create_time).toLocaleString()}`;
      const text = document.createElement("div"); text.textContent = row.body;
      li.append(meta, text); nodes.push(li);
    }
    if (prepend) el("messages").prepend(...nodes); else el("messages").append(...nodes);
  }
  async function poll() {
    if (!currentUser || polling) return;
    const stamp = epoch; polling = true;
    try {
      const rows = await request(endpoint() + (newest ? `?after=${newest}` : ""));
      if (stamp !== epoch) return;
      if (!newest) el("older").hidden = rows.length < 50;
      render(rows); el("error").textContent = "";
    } catch (e) {
      if (stamp === epoch && e.name !== "AbortError") el("error").textContent = `读取失败，将重试：${e.message}`;
    } finally {
      if (stamp === epoch) { polling = false; timer = setTimeout(poll, 4000); }
    }
  }
  async function inbox(more = false) {
    if (currentUser?.role !== 1) return;
    const stamp = epoch;
    try {
      const rows = await request("/support/conversations" + (more && inboxCursor ? `?before=${inboxCursor}` : ""));
      if (stamp !== epoch) return;
      if (!more) el("inbox").replaceChildren();
      for (const row of rows) {
        const button = document.createElement("button");
        button.type = "button"; button.textContent = `${row.username} · ${row.awaiting_admin ? "待回复" : "已回复"}`;
        button.onclick = () => {
          reset(); target = row.customer_id; el("title").textContent = `与 ${row.username} 的会话`; poll();
        };
        el("inbox").append(button);
      }
      inboxCursor = rows.at(-1)?.id; el("inbox-more").hidden = rows.length < 50;
    } catch (e) { if (stamp === epoch && e.name !== "AbortError") el("error").textContent = e.message; }
  }
  el("inbox-refresh").onclick = () => inbox();
  el("inbox-more").onclick = () => inbox(true);
  el("older").onclick = async () => {
    const stamp = epoch;
    try {
      const rows = await request(endpoint() + `?before=${oldest}`);
      if (stamp === epoch) { render(rows, true); el("older").hidden = rows.length < 50; }
    } catch (e) { if (stamp === epoch && e.name !== "AbortError") el("error").textContent = e.message; }
  };
  el("form").onsubmit = async (e) => {
    e.preventDefault();
    if (!currentUser) return auth.open();
    if (sending) return;
    const body = el("body").value.trim(); if (!body) return;
    if (!pending || pending.body !== body) pending = { body, client_nonce: crypto.randomUUID() };
    const stamp = epoch; sending = true; el("send").disabled = true;
    try {
      await request(endpoint(), { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(pending) });
      if (stamp !== epoch) return;
      // Do not advance the read cursor to this POST's id: intervening admin replies must not be skipped.
      el("body").value = ""; pending = null; el("error").textContent = "";
      clearTimeout(timer); if (!polling) poll();
    } catch (err) {
      if (stamp === epoch && err.name !== "AbortError") el("error").textContent = `发送未确认，可重试（不会重复入库）：${err.message}`;
    } finally { if (stamp === epoch) { sending = false; el("send").disabled = false; } }
  };
  function onUser(user) {
    reset(); currentUser = user; target = null; inboxCursor = null;
    el("inbox").replaceChildren(); el("inbox-more").hidden = true;
    el("login").hidden = !!user; el("workspace").hidden = !user;
    el("inbox-panel").hidden = user?.role !== 1; el("title").textContent = "我的留言";
    if (user) { poll(); inbox(); }
  }
  auth.onChange(onUser); onUser(auth.user);
  window.addEventListener("pagehide", () => { epoch += 1; clearTimeout(timer); controller.abort(); });
})();
