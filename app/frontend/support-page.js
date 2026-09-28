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

  // 错误文字用共享模块的实现（TD-315）：原来这里有一份与 auth.js 逐字相同的 errorText 副本
  const errorText = (data, status) => auth.errorText(data, status);
  const failure = (e) => auth.failureText?.(e) ?? e.message;
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
  // 时间固定按中文格式（TD-306）：不带参数的 toLocaleString() 跟随浏览器语言，英文系统上是
  // 「9/28/2026, 9:49:30 AM」，夹在全中文页面里很突兀。
  const TIME_FORMAT = { year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hour12: false };
  function formatTime(value) {
    const time = new Date(value);
    return Number.isNaN(time.getTime()) ? "" : time.toLocaleString("zh-CN", TIME_FORMAT);
  }
  function render(rows, prepend = false) {
    const box = el("messages"), stick = !prepend && nearBottom(box);
    const nodes = [];
    for (const row of rows) {
      if (seen.has(row.id)) continue;
      seen.add(row.id); newest = Math.max(newest, row.id); oldest = Math.min(oldest ?? row.id, row.id);
      const li = document.createElement("li"), meta = document.createElement("small");
      li.className = row.sender_role === 1 ? "admin" : "customer";
      meta.textContent = `${row.sender_role === 1 ? "管理员" : "客户"} · ${formatTime(row.create_time)}`;
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
      if (stamp === epoch && e.name !== "AbortError") el("error").textContent = `读取失败，将重试：${failure(e)}`;
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
      if (!more) el("inbox-empty").hidden = rows.length > 0;  // 空状态只看第一页：「更多会话」拿到空页不代表列表为空（TD-326）
      for (const row of rows) {
        const button = document.createElement("button");
        button.type = "button"; button.className = "item";
        button.textContent = `${row.username} · ${row.awaiting_admin ? "待回复" : "已回复"}`;
        if (row.customer_id === target) button.setAttribute?.("aria-current", "true");  // 刷新列表后保留选中标记
        button.onclick = () => {
          // 选中的会话高亮（TD-306）；可选调用：测试替身没有 setAttribute
          for (const other of el("inbox").children || []) other.removeAttribute?.("aria-current");
          button.setAttribute?.("aria-current", "true");
          reset(); target = row.customer_id; el("send").disabled = false; el("title").textContent = `与 ${row.username} 的会话`; poll();
        };
        el("inbox").append(button);
      }
      inboxCursor = rows.at(-1)?.id; el("inbox-more").hidden = rows.length < 50;
    } catch (e) { if (stamp === epoch && e.name !== "AbortError") el("error").textContent = failure(e); }
  }
  el("inbox-refresh").onclick = () => inbox();
  el("login-btn").onclick = () => auth.open();
  el("inbox-more").onclick = () => inbox(true);
  el("older").onclick = async () => {
    const stamp = epoch;
    try {
      const rows = await request(endpoint() + `?before=${oldest}`);
      if (stamp === epoch) { render(rows, true); el("older").hidden = rows.length < 50; }
    } catch (e) { if (stamp === epoch && e.name !== "AbortError") el("error").textContent = failure(e); }
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
      if (stamp === epoch && err.name !== "AbortError") el("error").textContent = `发送未确认，可重试（不会重复入库）：${failure(err)}`;
    } finally { if (stamp === epoch) { sending = false; el("send").disabled = false; } }
  };
  // ---------------------------------------------------------------- 先问智能助手（N-08 / TD-295）
  // 公开的 /support/ask，不需要登录，也不走上面带会话代次的 request()：登录状态变化不会清掉助手的回答。
  // 回答只用 textContent 渲染（模型输出不可信）；reason 是运维字段，只识别「模型繁忙」给出重试提示。
  const SOURCE_LABELS = {
    faq: "来自常见问题", "faq-semantic": "来自常见问题（相近问法）", llm: "智能助手回答",
    rag: "根据站内文章整理", human: "需要管理员协助",
  };
  // 固定的转人工答案（source=human）是写给 /support/ask 所有调用方的，要他们「登录站内客服页留言」；
  // 人已经在客服页上，就按登录身份换成页内说法（R-03）。其他来源的回答原样显示。
  const HUMAN_ANSWERS = {
    guest: "这个问题需要管理员协助。点下面的按钮并登录，问题会自动带到留言框。",
    user: "这个问题需要管理员协助。点下面的按钮把问题带到留言框，提交后管理员会在本页回复。",
    admin: "这个问题需要管理员协助；普通用户在这里会看到「留言给管理员」的入口。",
  };
  let asking = false, lastQuestion = "", lastEscalated = false, lastAnswer = null, handoffDraft = null;
  function renderAnswer(user) {
    if (lastAnswer === null) return;
    const role = !user ? "guest" : user.role === 1 ? "admin" : "user";
    el("ask-answer").textContent = lastAnswer.human ? HUMAN_ANSWERS[role] : lastAnswer.text;
    // 管理员自己是回复方，不需要「转给管理员」
    el("ask-handoff").hidden = !lastEscalated || role === "admin";
  }
  const askController = new AbortController();
  function fillDraft(text) {
    const box = el("body");
    box.value = text; box.focus?.(); box.scrollIntoView?.({ block: "center" });
  }
  function showAnswer(data, question) {
    lastQuestion = question; lastEscalated = !!data?.escalated;
    el("ask-source").textContent = SOURCE_LABELS[data?.source] || "";
    lastAnswer = { human: data?.source === "human" && lastEscalated, text: typeof data?.answer === "string" ? data.answer : "" };
    el("ask-busy").hidden = !(typeof data?.reason === "string" && data.reason.includes("模型繁忙"));
    const refs = Array.isArray(data?.references) ? data.references.filter((x) => typeof x === "string" && x) : [];
    el("ask-refs").replaceChildren(...refs.map((title) => {
      const li = document.createElement("li"); li.textContent = title; return li;
    }));
    el("ask-refs-panel").hidden = !refs.length;
    renderAnswer(currentUser);
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
      if (err.name !== "AbortError") el("ask-error").textContent = `暂时无法回答：${failure(err)}`;
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
    // 回答显示之后换了账号：管理员是回复方，隐藏「留言给管理员」；换回普通用户或退出后按原回答恢复
    renderAnswer(user);
    el("inbox").replaceChildren(); el("inbox-more").hidden = true; el("inbox-empty").hidden = true;
    el("send").disabled = !user || user.role === 1;
    el("login").hidden = !!user; el("workspace").hidden = !user;
    const isAdmin = user?.role === 1;
    // 管理员选中会话之前没有「我的留言」可看，标题直接提示下一步（TD-306）；手机上会话列表在上方
    el("inbox-panel").hidden = !isAdmin; el("title").textContent = isAdmin ? "请先选择一个客户会话" : "我的留言";
    // 两栏布局用 class 切换而不是 CSS :has()：旧内核不支持 :has()，管理员会只看到单栏叠放（TD-263）。
    const layout = el("workspace").classList;
    if (isAdmin) layout.add("with-inbox"); else layout.remove("with-inbox");
    if (user) { poll(); inbox(); }
  }
  auth.onChange(onUser); onUser(auth.user);
  window.addEventListener("pagehide", () => { epoch += 1; clearTimeout(timer); controller.abort(); askController.abort(); });
})();
