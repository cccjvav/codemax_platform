// Durable customer/admin messaging. Account/conversation epochs discard stale responses.
(() => {
  const auth = window.CodeMaxAuth;
  const el = (id) => document.getElementById(`support-${id}`);
  let epoch = 0, timer, controller = new AbortController(), currentUser = null;
  let target = null, newest = 0, oldest = null, inboxCursor = null, pending = null;
  let polling = false, sending = false, inboxSeq = 0;
  function newNonce() {
    if (globalThis.crypto?.randomUUID) return globalThis.crypto.randomUUID();
    const bytes = new Uint8Array(16);
    if (globalThis.crypto?.getRandomValues) globalThis.crypto.getRandomValues(bytes);
    else for (let i = 0; i < bytes.length; i++) bytes[i] = Math.floor(Math.random() * 256);
    // Idempotency identifier, NOT an authentication secret. Older HTTP contexts lack randomUUID.
    bytes[6] = (bytes[6] & 15) | 64; bytes[8] = (bytes[8] & 63) | 128;
    const hex = Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join("");
    return `${hex.slice(0,8)}-${hex.slice(8,12)}-${hex.slice(12,16)}-${hex.slice(16,20)}-${hex.slice(20)}`;
  }
  const seen = new Set();

  function errorText(data, status) {
    if (typeof data?.detail === "string") return data.detail;
    if (Array.isArray(data?.detail)) return data.detail.map((x) => x.msg || "输入无效").join("；");
    return `请求失败（${status}）`;
  }
  async function request(url, options = {}) {
    const res = await fetch(url, { credentials: "same-origin", signal: controller.signal, ...options });
    const data = await res.json().catch(() => null);
    if (!res.ok) {
      // 会话到期：由共享模块清用户、弹浮层；onUser(null) 会随之 reset 本页并停止轮询
      if (auth.sessionExpired?.(res.status)) throw new Error("登录已过期，请重新登录");
      throw new Error(errorText(data, res.status));
    }
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
  // 消息区有最大高度并自带滚动条。追加新消息时，如果用户本来就停在底部（或这是第一屏），
  // 就把视图带到最新一条；用户正往上翻旧消息时不打断他。此前新回复到达后停在原处，
  // 管理员的回复常常落在可视区之外，看起来像「没有收到」。
  function nearBottom(box) {
    return !box.scrollHeight || box.scrollHeight - box.scrollTop - box.clientHeight < 48;
  }
  function render(rows, prepend = false) {
    const box = el("messages"), stick = !prepend && nearBottom(box);
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
    if (prepend) box.prepend(...nodes); else box.append(...nodes);
    if (stick && nodes.length && typeof box.scrollHeight === "number") box.scrollTop = box.scrollHeight;
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
    const stamp = epoch, serial = ++inboxSeq;
    try {
      const rows = await request("/support/conversations" + (more && inboxCursor ? `?before=${inboxCursor}` : ""));
      if (stamp !== epoch || serial !== inboxSeq) return;
      if (!more) el("inbox").replaceChildren();
      for (const row of rows) {
        const button = document.createElement("button");
        button.type = "button"; button.textContent = `${row.username} · ${row.awaiting_admin ? "待回复" : "已回复"}`;
        button.onclick = () => {
          reset(); target = row.customer_id; el("send").disabled = false; el("title").textContent = `与 ${row.username} 的会话`; poll();
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
    if (currentUser.role === 1 && target === null) {
      el("error").textContent = "请先选择要回复的客户会话"; return;
    }
    const body = el("body").value.trim(); if (!body) return;
    const stamp = epoch; sending = true; el("send").disabled = true;
    try {
      if (!pending || pending.body !== body) pending = { body, client_nonce: newNonce() };
      await request(endpoint(), { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(pending) });
      if (stamp !== epoch) return;
      // Do not advance the read cursor to this POST's id: intervening admin replies must not be skipped.
      el("body").value = ""; pending = null; el("error").textContent = "";
      clearTimeout(timer); if (!polling) poll();
    } catch (err) {
      if (stamp === epoch && err.name !== "AbortError") el("error").textContent = `发送未确认，可重试（不会重复入库）：${err.message}`;
    } finally { if (stamp === epoch) { sending = false; el("send").disabled = false; } }
  };
  // ---------------------------------------------------------------- 先问智能助手（N-08 / TD-295）
  // 公开的 /support/ask，不需要登录，也不走上面带会话代次的 request()：登录状态变化不会清掉助手的回答。
  // 回答只用 textContent 渲染（模型输出不可信）；reason 是运维字段，只识别「模型繁忙」给出重试提示。
  const SOURCE_LABELS = {
    faq: "来自常见问题", "faq-semantic": "来自常见问题（相近问法）", llm: "智能助手回答",
    rag: "根据站内文章整理", human: "需要管理员协助",
  };
  let asking = false, lastQuestion = "", handoffDraft = null;
  const askController = new AbortController();
  function fillDraft(text) {
    const box = el("body");
    box.value = text; box.focus?.(); box.scrollIntoView?.({ block: "center" });
  }
  function showAnswer(data, question) {
    lastQuestion = question;
    el("ask-source").textContent = SOURCE_LABELS[data?.source] || "";
    el("ask-answer").textContent = typeof data?.answer === "string" ? data.answer : "";
    el("ask-busy").hidden = !(typeof data?.reason === "string" && data.reason.includes("模型繁忙"));
    const refs = Array.isArray(data?.references) ? data.references.filter((x) => typeof x === "string" && x) : [];
    el("ask-refs").replaceChildren(...refs.map((title) => {
      const li = document.createElement("li"); li.textContent = title; return li;
    }));
    el("ask-refs-panel").hidden = !refs.length;
    // 管理员自己是回复方，不需要「转给管理员」
    el("ask-handoff").hidden = !data?.escalated || currentUser?.role === 1;
    el("ask-result").hidden = false;
  }
  el("ask-form").onsubmit = async (e) => {
    e.preventDefault();
    const text = el("ask-text").value.trim();
    if (!text || asking) return;
    asking = true; el("ask-send").disabled = true; el("ask-error").textContent = "";
    try {
      const res = await fetch("/support/ask", {
        method: "POST", credentials: "same-origin", signal: askController.signal,
        headers: { "Content-Type": "application/json" }, body: JSON.stringify({ text }),
      });
      const data = await res.json().catch(() => null);
      if (!res.ok) throw new Error(errorText(data, res.status));
      showAnswer(data, text);
    } catch (err) {
      if (err.name !== "AbortError") el("ask-error").textContent = `暂时无法回答：${err.message}`;
    } finally { asking = false; el("ask-send").disabled = false; }
  };
  el("ask-to-human").onclick = () => {
    if (!lastQuestion) return;
    // 未登录：先记住问题，登录成功后由 onUser 填进留言框
    if (!currentUser) { handoffDraft = lastQuestion; auth.open(); return; }
    if (currentUser.role !== 1) fillDraft(lastQuestion);
  };
  function onUser(user, reason) {
    // 会话到期不是主动退出：留言草稿还在用户手上，先留住再重置视图（TD-271 复核的 N-01）。
    const draft = reason === "expired" ? el("body").value : "";
    reset(); currentUser = user; target = null; inboxCursor = null;
    if (draft) el("body").value = draft;
    if (user && user.role !== 1 && handoffDraft) { fillDraft(handoffDraft); handoffDraft = null; }
    el("inbox").replaceChildren(); el("inbox-more").hidden = true;
    el("send").disabled = !user || user.role === 1;
    el("login").hidden = !!user; el("workspace").hidden = !user;
    const isAdmin = user?.role === 1;
    el("inbox-panel").hidden = !isAdmin; el("title").textContent = "我的留言";
    // 两栏布局用 class 切换而不是 CSS :has()：旧内核不支持 :has()，管理员会只看到单栏叠放（TD-263）。
    const layout = el("workspace").classList;
    if (isAdmin) layout.add("with-inbox"); else layout.remove("with-inbox");
    if (user) { poll(); inbox(); }
  }
  auth.onChange(onUser); onUser(auth.user);
  window.addEventListener("pagehide", () => { epoch += 1; clearTimeout(timer); controller.abort(); askController.abort(); });
})();
