// Admin financial data stays in memory. Session and view generations reject late responses.
(() => {
  const auth = window.CodeMaxAuth;
  const el = (id) => document.getElementById(`finance-${id}`);
  let user = null, epoch = 0, listSeq = 0, viewSeq = 0;
  let controller = new AbortController(), selected = null, contract = null;
  let listCursor = null, eventCursor = null, busy = false;
  const inputs = ["order-no", "confirm-no", "evidence", "reference", "amount", "source"];
  const money = (cents) => `${cents} 分（¥${(cents / 100).toFixed(2)}）`;
  const alive = (stamp, view) => stamp === epoch && (view === undefined || view === viewSeq);
  const message = (text) => { el("message").textContent = text; };
  const ledgerURL = (number) => `/shop/admin/orders/${encodeURIComponent(number)}/ledger`;

  function clearDetail() {
    viewSeq++; selected = null; contract = null; busy = false; eventCursor = null;
    for (const id of inputs.filter((x) => x !== "order-no")) el(id).value = "";
    for (const id of ["contract", "receipt"]) el(id).textContent = "";
    el("title").textContent = "请选择订单"; el("events").replaceChildren();
    el("operations").hidden = true; el("older").hidden = true;
    el("refresh").disabled = true;
    for (const id of ["manual", "query", "binding"]) el(`${id}-submit`).disabled = false;
  }
  function reset(next) {
    epoch++; listSeq++; controller.abort(); controller = new AbortController();
    user = next?.role === 1 ? next : null;
    clearDetail(); listCursor = null; el("list").replaceChildren();
    el("order-no").value = ""; el("bucket").value = "all"; el("mode").value = "manual";
    el("next").hidden = true; el("workspace").hidden = !user;
    el("login").hidden = !!user; message("");
    el("access").textContent = user ? `管理员：${user.username}` : "请使用管理员账号登录；普通账号不能读取收款数据。";
  }
  async function request(url, options = {}) {
    const stamp = epoch;
    const res = await fetch(url, {credentials: "same-origin", cache: "no-store", signal: controller.signal, ...options});
    const data = await res.json().catch(() => null);
    if (!res.ok) {
      if (alive(stamp) && [401, 403].includes(res.status)) reset(null);
      throw new Error(auth.errorText(data, res.status));
    }
    return data;
  }
  function report(error, stamp, view) {
    if (alive(stamp, view) && error.name !== "AbortError") message(error.message);
  }
  async function list(more = false) {
    if (!user) return;
    const stamp = epoch, serial = ++listSeq;
    const params = new URLSearchParams({bucket: el("bucket").value});
    if (el("order-no").value.trim()) params.set("order_no", el("order-no").value.trim());
    if (more && listCursor) params.set("before", listCursor);
    el("next").hidden = true;
    try {
      const data = await request(`/shop/admin/orders?${params}`);
      if (!alive(stamp) || serial !== listSeq) return;
      el("list").replaceChildren();
      for (const row of data.orders) {
        const li = document.createElement("li"), button = document.createElement("button");
        button.type = "button";
        button.textContent = `${row.order_no} · ${row.username} (#${row.user_id}) · ${row.product_name} · ${money(row.amount)} · ${row.status} / ${row.payment_mode}`;
        button.onclick = () => {
          if (busy) { message("请先等待当前操作完成；离开页面不会撤销已提交操作。"); return; }
          clearDetail(); selected = row.order_no; message(""); detail();
        };
        li.append(button); el("list").append(li);
      }
      if (!data.orders.length) el("list").textContent = "没有匹配订单";
      listCursor = data.next_cursor; el("next").hidden = !listCursor;
    } catch (error) { if (serial === listSeq) report(error, stamp); }
  }
  async function detail(older = false) {
    if (!user || !selected || busy) return;
    const stamp = epoch, view = ++viewSeq, number = selected;
    const cursor = older ? eventCursor : null;
    el("operations").hidden = true; el("older").hidden = true; el("refresh").disabled = true;
    try {
      const data = await request(ledgerURL(number) + (cursor ? `?before=${cursor}` : ""));
      if (!alive(stamp, view)) return;
      contract = data.order;
      el("title").textContent = `订单 ${number}`;
      el("contract").textContent = `客户ID：${contract.user_id}\n商品：${contract.product_name}\n合同金额：${money(contract.amount)} ${contract.currency}\n状态：${contract.status} / ${contract.payment_mode}\n原商户 / 应用：${contract.merchant_id || "无"} / ${contract.app_id || "无"}\n冻结文件：${contract.delivery_key || "尚未绑定"}\nSHA-256：${contract.delivery_digest || "无"}\n字节数：${contract.delivery_size ?? "无"}`;
      const receipt = data.receipt;
      el("receipt").textContent = receipt ? `来源：${receipt.source}\n流水：${receipt.reference}\n金额：${money(receipt.amount)} ${receipt.currency}\n确认/核查发起人：${receipt.actor || "自动回调"}\n依据：${receipt.evidence || "渠道验签"}\n渠道付款时间：${receipt.paid_at || "未提供"}\n本地入账时间：${receipt.received_at}` : "没有收款凭证（不等于没有付款；已付历史单不得伪造收入）。";
      el("manual").hidden = !(data.actions.manual && contract.payment_mode === "manual" && !receipt && ["pending", "closed"].includes(contract.status));
      el("query").hidden = contract.payment_mode !== "wechat";
      el("binding").hidden = contract.payment_mode !== "legacy";
      el("operations").hidden = ["manual", "query", "binding"].every((x) => el(x).hidden);
      el("events").replaceChildren();
      for (const event of data.events) {
        const li = document.createElement("li");
        li.textContent = `${event.create_time} · ${event.kind} · ${event.actor || "系统"}\n尝试 ${event.attempt_id}\n${event.evidence || ""}`;
        el("events").append(li);
      }
      eventCursor = data.next_cursor; el("older").hidden = !eventCursor;
    } catch (error) { report(error, stamp, view); }
    finally { if (alive(stamp, view)) el("refresh").disabled = false; }
  }
  async function operate(kind, event) {
    event.preventDefault();
    if (!user || !selected || !contract || busy || el("operations").hidden || el(kind).hidden) return;
    const number = selected, stamp = epoch, view = viewSeq;
    const evidence = el("evidence").value.trim();
    if (el("confirm-no").value.trim() !== number || evidence.length < 3 || evidence.length > 500 || /[\x00-\x1f]/.test(evidence)) {
      message("请手动输入与当前订单一致的完整单号，以及3–500字的单行核查依据。"); return;
    }
    let url, body = {evidence}, summary;
    if (kind === "manual") {
      body.amount = Number(el("amount").value); body.reference = el("reference").value.trim();
      if (!Number.isSafeInteger(body.amount) || body.amount <= 0 || body.amount !== contract.amount || !/^[A-Za-z0-9][A-Za-z0-9._:/-]{0,63}$/.test(body.reference)) {
        message("实际到账分数须与合同一致，且必须有真实、有效的流水号。"); return;
      }
      url = `/shop/orders/${encodeURIComponent(number)}/confirm`;
      summary = `已在真实收款记录核实 ${money(body.amount)}，流水 ${body.reference}？`;
    } else if (kind === "query") {
      url = `/shop/admin/orders/${encodeURIComponent(number)}/reconcile`;
      body.confirm_order_no = number; summary = "向原商户查询；可信成功结果可能补记收款。是否继续？";
    } else {
      url = `/shop/orders/${encodeURIComponent(number)}/legacy-binding`;
      body.payment_mode = el("mode").value; body.source_key = el("source").value.trim();
      if (!body.source_key) { message("请核实并填写原交付文件路径。"); return; }
      summary = `永久绑定渠道 ${body.payment_mode} 与原文件 ${body.source_key}；不可改写。是否已核实？`;
    }
    if (!window.confirm(`订单 ${number} / 客户ID ${contract.user_id}\n${summary}`)) return;
    busy = true;
    for (const name of ["manual", "query", "binding"]) el(`${name}-submit`).disabled = true;
    let resultText;
    try {
      const result = await request(url, {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(body)});
      if (!alive(stamp, view)) return;
      resultText = result.observed_state ? `微信观察：${result.observed_state}；本地：${result.status}。${result.warning}` : "操作已提交；请核对下方凭证和历史记录。";
    } catch (error) {
      if (alive(stamp, view)) resultText = `${error.message}。网络失败不证明操作未提交，请先刷新记录，勿另造流水重试。`;
    } finally {
      if (alive(stamp, view)) {
        busy = false;
        for (const name of ["manual", "query", "binding"]) el(`${name}-submit`).disabled = false;
        message(resultText || "请核对记录");
        await detail(); // preserves entered proof on failure; no automatic mutation retries
      }
    }
  }
  el("login").onclick = () => auth.open("login");
  el("search").onsubmit = (event) => { event.preventDefault(); list(); };
  el("next").onclick = () => list(true);
  el("refresh").onclick = () => detail();
  el("older").onclick = () => detail(true);
  for (const kind of ["manual", "query", "binding"]) el(kind).onsubmit = (event) => operate(kind, event);
  auth.onChange((next) => { reset(next); if (user) list(); });
  reset(auth.user); if (user) list();
})();
