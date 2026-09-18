// Admin financial data stays in memory. Session and view generations reject late responses.
(() => {
  const auth = window.CodeMaxAuth;
  const el = (id) => document.getElementById(`finance-${id}`);
  let user = null, epoch = 0, listSeq = 0, viewSeq = 0;
  let controller = new AbortController(), selected = null, contract = null;
  let listFilter = null, currentNotice = null, currentRequest = null, pendingRequest = null;
  let listCursor = null, eventCursor = null, busy = false, review = null, pendingReview = null;
  let submission = null, pendingAuthorization = null, pendingSend = null, pendingStop = null;
  let verificationJobs = [], pendingControl = null;
  const forms = ["manual", "query", "binding", "review", "refund-query", "refund-manual", "refund-request", "refund-authorize", "refund-send", "refund-stop", "verification-control"];
  const nonce = () => globalThis.crypto?.randomUUID ? globalThis.crypto.randomUUID().replaceAll("-", "") : Array.from({length: 16}, () => Math.floor(Math.random() * 256).toString(16).padStart(2, "0")).join("");
  const inputs = ["order-no", "confirm-no", "evidence", "reference", "amount", "source", "refund-no", "refund-reference", "refund-amount", "refund-time", "request-amount", "send-number", "send-amount", "customer-reason", "verify-job"];
  const money = (cents) => `${cents} 分（¥${(cents / 100).toFixed(2)}）`;
  const alive = (stamp, view) => stamp === epoch && (view === undefined || view === viewSeq);
  const message = (text) => { el("message").textContent = text; };
  const ledgerURL = (number) => `/shop/admin/orders/${encodeURIComponent(number)}/ledger`;

  function clearDetail() {
    verificationJobs = []; pendingControl = null; el("verify-action").value = "hold"; el("verification-control-view").textContent = "";
    submission = null; pendingAuthorization = null; pendingSend = null; pendingStop = null; el("stop-view").textContent = ""; el("submission-view").textContent = "";
    currentRequest = null; pendingRequest = null; el("request-view").textContent = ""; el("request-prefill").disabled = true;
    currentNotice = null; el("refund-prefill").disabled = true; el("refund-notice").textContent = "";
    review = null; pendingReview = null; el("review-status").textContent = ""; el("review-action").value = "followup";
    viewSeq++; selected = null; contract = null; busy = false; eventCursor = null;
    for (const id of inputs.filter((x) => x !== "order-no")) el(id).value = "";
    for (const id of ["contract", "receipt", "refund-receipt", "verification-view"]) el(id).textContent = "";
    el("title").textContent = "请选择订单"; el("events").replaceChildren();
    el("operations").hidden = true; el("older").hidden = true;
    el("refresh").disabled = true;
    for (const id of forms) el(`${id}-submit`).disabled = false;
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
      const error = new Error(auth.errorText(data, res.status)); error.status = res.status; throw error;
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
    const filter = params.toString();
    if (more && listCursor && filter === listFilter) params.set("before", listCursor);
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
      if (!data.orders.length) el("list").textContent = data.next_cursor ? "本段候选暂无匹配，请点下一页继续（未到末页）" : "没有匹配订单";
      listFilter = filter; listCursor = data.next_cursor; el("next").hidden = !listCursor;
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
      contract = data.order; review = data.review || null;
      if (pendingReview && review?.request_id === pendingReview.request_id) pendingReview = null;
      const labels = {none: "暂无异常，可登记人工跟进", open: "需要复核", followup: "继续跟进", reviewed: "当前进展已复核（不是已退款）"};
      el("review-status").textContent = review ? `${labels[review.state]}${review.new_facts ? "；有新进展或记录需重新核对" : ""}\n异常记录 ${review.issues}；超时无结果尝试 ${review.orphans}\n${review.actor || "尚无复核人"} · ${review.time || ""}\n${review.note || ""}` : "复核状态未加载";
      el("review").hidden = !review;
      el("title").textContent = `订单 ${number}`;
      el("contract").textContent = `客户ID：${contract.user_id}\n商品：${contract.product_name}\n合同金额：${money(contract.amount)} ${contract.currency}\n状态：${contract.status} / ${contract.payment_mode}\n原商户 / 应用：${contract.merchant_id || "无"} / ${contract.app_id || "无"}\n冻结文件：${contract.delivery_key || "尚未绑定"}\nSHA-256：${contract.delivery_digest || "无"}\n字节数：${contract.delivery_size ?? "无"}`;
      const receipt = data.receipt;
      el("receipt").textContent = receipt ? `来源：${receipt.source}\n流水：${receipt.reference}\n金额：${money(receipt.amount)} ${receipt.currency}\n确认/核查发起人：${receipt.actor || "自动回调"}\n依据：${receipt.evidence || "渠道验签"}\n渠道付款时间：${receipt.paid_at || "未提供"}\n本地入账时间：${receipt.received_at}` : "没有收款凭证（不等于没有付款；已付历史单不得伪造收入）。";
      currentNotice = data.refund_notice || null;
      el("refund-notice").textContent = currentNotice ? `已接收并核验的渠道通知（不是本地退款完成凭证）\n通知ID：${currentNotice.notification_id}\n商户退款号：${currentNotice.refund_no}\n渠道退款ID：${currentNotice.refund_id}\n通知状态：${currentNotice.state}\n合同退款金额：${money(currentNotice.refund)}\n${currentNotice.partial ? "部分退款：当前全额核验入口不处理，请在原渠道核账；该通知本身不改变权益。" : "全额通知仍须独立查询原路退款；资金结论以成功退款凭证为准。"}` : "暂无可展示的退款通知摘要；完整历史见事件列表。";
      el("refund-prefill").disabled = !currentNotice || currentNotice.partial;
      const verification = data.refund_verification || {jobs: [], has_more: false};
      verificationJobs = verification.jobs;
      const latestControl = verification.latest_control;
      if (pendingControl && latestControl?.request_id === pendingControl.request_id) pendingControl = null;
      el("verification-control").hidden = !verificationJobs.length;
      el("verification-control-view").textContent = latestControl ? `最近核验调度操作（不是当前状态保证）\n任务 ${latestControl.job_id} · ${latestControl.action === "hold" ? "人工接管" : latestControl.action === "retry" ? "剩余次数重排" : "未知动作"} · ${latestControl.actor} · ${latestControl.created_at}\n${latestControl.evidence}` : "尚无人工调度操作";
      const jobLabels = {pending: "待核验", running: "核验中（租约超时可恢复）", retry: "等待重试", verified: "查询成功，待管理员按原号确认", attention: "需要人工处理"};
      el("verification-view").textContent = `${data.refund_verify_enabled ? "允许独立worker运行（不证明进程在线）" : "自动核验开关关闭，任务仍保存"}\n${data.refund_auto_record_enabled ? "已允许系统登记可信全额原路成功凭证并停止后续下载；不会发起退款，不补处理旧终态任务。" : "只读查询，不发起退款；SUCCESS观察不自动撤销下载。"}\n` + verification.jobs.map((j) => `任务 ${j.id} · 原退款号 ${j.refund_no || "需核对原通知"} · 通知事件 ${j.notice_event_id} · ${j.state === "verified" && data.refund?.out_refund_no === j.refund_no ? "该原号已有成功退款凭证" : jobLabels[j.state] || "未知状态"} · 尝试 ${j.attempts}/8\n结果：${j.outcome} · 更新：${j.updated_at} · 下次/租约：${j.state === "running" ? j.lease_until : j.state === "retry" || j.state === "pending" ? j.next_at : "无自动重试"}`).join("\n") + (verification.has_more ? "\n仅显示最近50项，旧观察见事件历史。" : "");
      currentRequest = data.refund_request || null;
      if (pendingRequest && currentRequest?.request_id === pendingRequest.request_id) pendingRequest = null;
      const requestStates = {prepared: "仅有本地准备；不证明渠道已发送或已退款", confirmed: "已有匹配成功退款凭证", completed_elsewhere: "已有其他退款号的成功凭证，不得另发退款"};
      el("request-view").textContent = currentRequest ? `${requestStates[currentRequest.state]}\n商户退款号：${currentRequest.out_refund_no}\n请求ID：${currentRequest.request_id}\n全额：${money(currentRequest.amount)} ${currentRequest.currency}\n原商户 / 应用：${currentRequest.merchant_id} / ${currentRequest.app_id}\n登记人：${currentRequest.actor} · ${currentRequest.created_at}\n依据：${currentRequest.evidence}` : "没有本地退款准备。已有通知/退款查询的订单须先核对原退款，不生成新编号。";
      el("refund-request").hidden = !data.refund_prepare_allowed;
      el("request-prefill").disabled = !currentRequest || currentRequest.state !== "prepared";
      submission = data.refund_submission || null;
      if (pendingAuthorization && submission?.authorization_id === pendingAuthorization.request_id) pendingAuthorization = null;
      el("submission-view").textContent = submission ? `独立授权：${submission.authorization_id}\n摘要：${submission.digest}\n首次授权人：${submission.actor} · ${submission.created_at}\n冻结的客户可见请求：\n${JSON.stringify(submission.body, null, 2)}\n最近发送尝试：${submission.attempt ? JSON.stringify(submission.attempt) : "无"}\n申请观察不是成功凭证。` : "未独立授权发送；旧准备不会自动发送。";
      el("refund-authorize").hidden = !currentRequest || currentRequest.state !== "prepared" || !!submission;
      if (pendingStop && submission?.stop?.request_id === pendingStop.request_id) pendingStop = null;
      el("stop-view").textContent = submission?.stop ? `已停止后续本站发送\n首次记录人：${submission.stop.actor} · ${submission.stop.created_at}\n依据：${submission.stop.evidence}\n已经开始的请求仍可能退款；不是渠道取消，不改下载权益，请查询原号。` : "暂无本站发送停止记录。";
      el("refund-stop").hidden = !submission || !!submission.stop;
      el("refund-send").hidden = !submission || !!submission.stop || !data.refund_send_enabled || !!data.refund;
      const refund = data.refund;
      el("refund-receipt").textContent = refund ? `已全额退款，停止此订单后续下载\n来源：${refund.source}\n渠道退款号：${refund.refund_id}\n商户退款单号：${refund.out_refund_no}\n金额：${money(refund.amount)} ${refund.currency}\n成功时间：${refund.completed_at}\n本地记录时间：${refund.received_at}\n记录身份：${refund.recorded_by === "system" ? "系统核验（非管理员代办）" : "管理员"} · ${refund.actor}\n核验开始事件：${refund.verification_event_id ?? "人工入口"}\n依据：${refund.evidence}` : "没有成功退款凭证；申请、处理中和查询失败不是退款完成。";
      el("refund-query").hidden = !receipt || receipt.source !== "wechat";
      el("refund-manual").hidden = !receipt || receipt.source !== "manual" || !!refund;
      el("manual").hidden = !(data.actions.manual && contract.payment_mode === "manual" && !receipt && ["pending", "closed"].includes(contract.status));
      el("query").hidden = contract.payment_mode !== "wechat";
      el("binding").hidden = contract.payment_mode !== "legacy";
      el("operations").hidden = forms.every((x) => el(x).hidden);
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
    if (el("confirm-no").value.trim() !== number || evidence.length < 3 || evidence.length > 500 || /[\x00-\x1f\x7f-\x9f]/.test(evidence)) {
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
    } else if (kind === "binding") {
      url = `/shop/orders/${encodeURIComponent(number)}/legacy-binding`;
      body.payment_mode = el("mode").value; body.source_key = el("source").value.trim();
      if (!body.source_key) { message("请核实并填写原交付文件路径。"); return; }
      summary = `永久绑定渠道 ${body.payment_mode} 与原文件 ${body.source_key}；不可改写。是否已核实？`;
    }
    if (kind === "refund-query" || kind === "refund-manual") {
      if (evidence.length > 160) { message("退款核验依据须为3–160字。"); return; }
      body.confirm_order_no = number;
      if (kind === "refund-query") {
        body.out_refund_no = el("refund-no").value.trim();
        if (!/^[A-Za-z0-9_\-|*@]{1,64}$/.test(body.out_refund_no)) { message("请填写原渠道的商户退款单号。"); return; }
        url = `/shop/admin/orders/${encodeURIComponent(number)}/refunds/query`;
        summary = "只查询既有全额原路退款；成功凭证入库后，此订单旧/新链接均停止下载。不会发起退款。是否继续？";
      } else {
        body.amount = Number(el("refund-amount").value); body.reference = el("refund-reference").value.trim();
        body.completed_at = el("refund-time").value.trim();
        if (!Number.isSafeInteger(body.amount) || body.amount !== contract.amount || body.amount <= 0 ||
            !/^[A-Za-z0-9][A-Za-z0-9._:/-]{0,63}$/.test(body.reference) ||
            !/(Z|[+-]\d{2}:\d{2})$/.test(body.completed_at) || !Number.isFinite(Date.parse(body.completed_at))) {
          message("填写实际全额退款分数、真实退款流水及带时区的成功时间；不是收款流水或退款申请时间。"); return;
        }
        url = `/shop/admin/orders/${encodeURIComponent(number)}/refunds/manual`;
        summary = `已在真实记录核实全额退款 ${money(body.amount)}，退款流水 ${body.reference}？保存不可覆盖，会停止此订单后续下载。`;
      }
    }
    if (kind === "refund-request") {
      const amount = Number(el("request-amount").value);
      if (evidence.length > 160 || !Number.isSafeInteger(amount) || amount <= 0 || amount !== contract.amount) {
        message("准备金额必须等于原合同全额整数分，依据须为3–160字。"); return;
      }
      if (pendingRequest && (pendingRequest.evidence !== evidence || pendingRequest.amount !== amount)) {
        message("上次准备结果未知，请先刷新或用原内容重试，不能换号、改金额或依据。"); return;
      }
      body = pendingRequest || {request_id: nonce(), confirm_order_no: number, amount, evidence};
      url = `/shop/admin/orders/${encodeURIComponent(number)}/refunds/requests`;
      summary = "保存不可覆盖的全额退款准备与固定商户退款号。不是发送或自动退款授权，不停止下载；一单不能换号再建。是否继续？";
    }
    if (kind === "refund-authorize" || kind === "refund-send" || kind === "refund-stop") {
      const amount = Number(el("send-amount").value), numberConfirmed = el("send-number").value.trim();
      if (!currentRequest || numberConfirmed !== currentRequest.out_refund_no || amount !== currentRequest.amount ||
          !Number.isSafeInteger(amount) || evidence.length > 160) {
        message("手动输入原准备商户退款号、全额整数分和最多160字内部依据。"); return;
      }
      const base = {confirm_order_no: number, amount, out_refund_no: numberConfirmed, evidence};
      const isAuth = kind === "refund-authorize", isStop = kind === "refund-stop";
      if (isAuth) {
        const reason = el("customer-reason").value.trim();
        if (!reason || new TextEncoder().encode(reason).length > 80 || /[\x00-\x1f\x7f-\x9f]/.test(reason)) {
          message("客户可见退款原因须为1–80 UTF-8字节单行；不是内部核账依据。"); return;
        }
        base.reason = reason;
      } else {
        if (!submission) return;
        base.authorization_id = submission.authorization_id; base.digest = submission.digest;
      }
      const pending = isAuth ? pendingAuthorization : isStop ? pendingStop : pendingSend;
      if (pending && Object.keys(base).some(key => base[key] !== pending[key])) {
        message("上次结果未知或已记录，请按原内容恢复；不可覆盖未知尝试。"); return;
      }
      body = pending || {...base, request_id: nonce()};
      url = `/shop/admin/orders/${encodeURIComponent(number)}/refunds/${isAuth ? "authorize" : isStop ? "stop" : "send"}`;
      summary = isStop ? "永久停止新的本站发送，并保存当前纠错/停办依据；不能撤回已开始或已送达微信的请求，不改退款号/正文/金额/下载权益。确认停止？" : isAuth ? `冻结并授权全额退款请求，客户会看到原因：${base.reason}。此按钮仅保存，之后仍需单独确认发送。` :
        `即将真实向微信申请全额退款 ${money(amount)}，商户退款号 ${numberConfirmed}，摘要 ${submission.digest}。首次点击可能转出资金；同尝试恢复不会重发。确认发送？`;
    }
    if (kind === "verification-control") {
      const jobId = Number(el("verify-job").value), action = el("verify-action").value;
      const job = verificationJobs.find(j => j.id === jobId);
      if (!job || !["hold", "retry"].includes(action) || evidence.length > 160) {
        message("输入当前列表中的任务ID，核对原退款号，依据须为3–160字。"); return;
      }
      if (pendingControl && (pendingControl.job_id !== jobId || pendingControl.action !== action || pendingControl.evidence !== evidence)) {
        message("上次核验操作结果未知，请按原内容重试或刷新核对，不覆盖原请求。"); return;
      }
      body = pendingControl || {confirm_order_no: number, request_id: nonce(), job_id: jobId, action, snapshot: job.snapshot, evidence};
      url = `/shop/admin/orders/${encodeURIComponent(number)}/refunds/verification/control`;
      summary = `任务 ${jobId} / 原退款号 ${job.refund_no || "须核对原通知"}：${action === "hold" ? "人工接管此任务，停止其自动调度；已领取尝试（即使还未发出HTTP）仍可能继续GET，不停止其他通知任务" : "重新排队，仅使用8次上限内剩余次数；开关关闭时不会执行，耗尽/已核验需人工查询"}。不发退款、不改变下载权，确认？`;
    }
    if (kind === "review") {
      if (!review || evidence.length > 160) { message("复核说明须为3–160字，请先刷新进度。"); return; }
      const action = el("review-action").value;
      if (pendingReview && (pendingReview.action !== action || pendingReview.evidence !== evidence)) {
        message("上次复核结果未确认，请先用原内容重试，或重新选单核对历史后再发起新操作。"); return;
      }
      body = pendingReview || {evidence, action, snapshot: review.snapshot, expected_version: review.version,
        confirm_order_no: number, request_id: nonce()};
      url = `/shop/admin/orders/${encodeURIComponent(number)}/review`;
      summary = `保存复核进度 ${action}；不代表到账或退款完成，也不改变下载权。是否继续？`;
    }
    if (!window.confirm(`订单 ${number} / 客户ID ${contract.user_id}\n${summary}`)) return;
    if (kind === "refund-authorize") pendingAuthorization = body;
    if (kind === "refund-send") pendingSend = body;
    if (kind === "refund-stop") pendingStop = body;
    if (kind === "verification-control") pendingControl = body;
    if (kind === "review") pendingReview = body;
    if (kind === "refund-request") pendingRequest = body;
    busy = true;
    for (const name of forms) el(`${name}-submit`).disabled = true;
    let resultText;
    try {
      const result = await request(url, {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(body)});
      if (!alive(stamp, view)) return;
      if (kind === "review") pendingReview = null;
      if (kind === "refund-request") pendingRequest = null;
      if (kind === "refund-authorize") pendingAuthorization = null;
      if (kind === "refund-stop") pendingStop = null;
      if (kind === "verification-control") pendingControl = null;
      resultText = kind === "verification-control" ? "核验调度操作已记录；请核对当前任务状态。未发退款或修改下载权，历史次数保留。" : kind === "refund-stop" ? "已记录停止后续本站发送；不是渠道取消。已经开始的请求仍须查询原号确认。" : kind === "refund-send" ? "已记录发送尝试观察；不是退款成功。刷新并按原号查询，不换号。" : kind === "refund-authorize" ? "独立授权与完整请求已保存，尚未发送；请核对冻结内容。" : kind === "refund-request" ? "本地准备已保存，未发送或批准自动退款，下载权未改变。请核对固定商户退款号。" : kind === "review" ? "复核记录已保存，资金/下载权未改变。请刷新左侧清单。" : result.observed_state ? `微信观察：${result.observed_state}；本地：${result.status}。${result.warning}` : "操作已提交；请核对下方凭证和历史记录。";
    } catch (error) {
      if (alive(stamp, view) && kind === "verification-control" && error.status === 409) pendingControl = null;
      if (alive(stamp, view) && kind === "review" && error.status >= 400 && error.status < 500) pendingReview = null;
      if (alive(stamp, view)) resultText = `${error.message}。网络失败不证明操作未提交，请先刷新记录，勿另造流水重试。`;
    } finally {
      if (alive(stamp, view)) {
        busy = false;
        for (const name of forms) el(`${name}-submit`).disabled = false;
        message(resultText || "请核对记录");
        await detail(); // preserves entered proof on failure; no automatic mutation retries
      }
    }
  }
  el("send-new-attempt").onclick = () => {
    if (!user || !submission || busy || el("refund-send").hidden) return;
    if (window.confirm("仅为结果未知的同一冻结请求创建新尝试；至少等待60秒，服务端仍会拒绝已受理/有通知或查询的订单。下一次发送仍须单独确认，不能换退款号。")) {
      pendingSend = null; message("已清本页尝试键，未发送。核对原号及完整请求后再单独确认。");
    }
  };
  el("request-prefill").onclick = () => {
    if (!user || !currentRequest || currentRequest.state !== "prepared" || busy || el("operations").hidden || el("refund-query").hidden) return;
    el("refund-no").value = currentRequest.out_refund_no;
    message("只填入已保存的准备号，不发送查询或退款。查询不到不代表可以换号重退；填号不会触发独立授权/发送按钮。");
  };
  el("refund-prefill").onclick = () => {
    if (!user || !currentNotice || currentNotice.partial || busy || el("operations").hidden || el("refund-query").hidden) return;
    el("refund-no").value = currentNotice.refund_no;
    message("仅填入商户退款号，未发起查询或退款。请核对并手动填写完整订单号和核验依据。");
  };
  el("login").onclick = () => auth.open("login");
  el("search").onsubmit = (event) => { event.preventDefault(); list(); };
  el("next").onclick = () => list(true);
  el("refresh").onclick = () => detail();
  el("older").onclick = () => detail(true);
  for (const kind of forms) el(kind).onsubmit = (event) => operate(kind, event);
  auth.onChange((next) => { reset(next); if (user) list(); });
  reset(auth.user); if (user) list();
})();
