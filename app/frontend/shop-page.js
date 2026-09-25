// 下单页（/shop）的交互脚本。
//
// ⚠️ 这里几乎每一段注释都对应一个**实测复现过的 bug**，删注释等于删证据。
// 动它之前先读 tests/test_shop_page.py 与 tests/test_shop_polling.py ——
// 那两个文件用 node 真跑这份代码（不是复制一份逻辑来测）。
//
// 原先它是 shop.html 里的内联脚本，TD-223 把它抽了出来：内联脚本没法 lint、
// 没法 import、也没法被 node 直接执行。搬迁前实测过 shop.html 的这段 JS 里
// Jinja 插值 0 处，所以是纯机械搬迁，数据传递方式没变。
const ST = {
  landing: document.getElementById("st-landing"),
  pending: document.getElementById("st-pending"),
  paid: document.getElementById("st-paid"),
  refunded: document.getElementById("st-refunded"),
  closed: document.getElementById("st-closed"),
  downloaded: document.getElementById("st-downloaded"),
};
let timer = null;
let currentNo = null;
let pollBusy = false;
let historyCursor = null;
// 主按钮的原始文案（服务端按配置渲染的价格）。切成「已购买，去下载」之后要能还原 ——
// 例如订单在别的标签页里被全额退款，回来点按钮时应当重新允许购买。
const buyBtn = document.getElementById("btn-buy");
const BUY_TEXT = buyBtn.textContent;

function show(name) {
  Object.entries(ST).forEach(([k, el]) => { el.hidden = k !== name; });
  if (name !== "pending" && timer) { clearInterval(timer); timer = null; }
}


// 按订单状态切页面。
// 状态渲染不调用 POST /shop/download/{no}：读取订单和申请文件链接是两件事。
// downloaded 只记录已经发过链接，paid/downloaded 均支持重新申请短时链接。
// 下载仍由用户主动点按钮触发，避免轮询反复申请链接或跳转页面。
function render(o) {
  currentNo = o.order_no;
  if (o.refunded) { show("refunded"); return; }
  if (o.status === "pending") {
    show("pending");
    document.getElementById("p-no").textContent = o.order_no;
    document.getElementById("p-expired").hidden = !o.expired;
    document.getElementById("p-tip").hidden = !!o.expired;
    // 收款码有两种来源，二选一渲染：
    //   qr_svg   —— 微信 Native 的 code_url 现画的内联 SVG（segno，纯 <path>）
    //   qr_image —— manual 模式的静态收款码（S5-04），服务端收不到回调，
    //               要管理员核对到账后人工确认（POST /shop/orders/{no}/confirm）
    // 用 createElement + .src 而不是把路径塞进 innerHTML：路径虽来自服务端配置，
    // 但它将来可能变成运营可配的值，从一开始就不给它 XSS 的机会。
    const box = document.getElementById("p-qr");
    box.innerHTML = "";
    if (o.qr_svg) {
      box.innerHTML = o.qr_svg;
    } else if (o.qr_image) {
      const img = document.createElement("img");
      img.src = o.qr_image;
      img.alt = "扫码付款";
      img.width = 220;
      box.appendChild(img);
      if (!o.expired) {
        document.getElementById("p-tip").textContent =
          "请扫码付款。付款后由客服核对到账并确认，本页会自动刷新状态。";
      }
    }
    const link = document.getElementById("p-link");
    // mock 模式（TD-124）的 code_url 是本站 http 链接，给个按钮比让人扫码方便
    if (o.code_url && /^https?:/.test(o.code_url)) {
      link.innerHTML = "";
      const a = document.createElement("a");
      a.href = o.code_url;
      a.className = "cta";
      a.textContent = "前往收银台支付";
      link.appendChild(a);
    } else {
      link.textContent = "";
    }
    // 过期就把定时器停掉。**只判断「不 expired 才起表」是不够的**：
    // 订单在页面开着的时候到期，此时 status 仍是 pending ——
    //   · show("pending") 不清定时器（它的条件是 name !== "pending"）
    //   · poll() 里 `o.status !== "pending"` 也为假，不会 stop()
    // 于是一张已经失效的二维码会以 3 秒一次**无限**打 GET /shop/orders/{no}
    // （实测 9.5 秒内 3 次，且永远不停）。用户开着标签页去吃个午饭 = 上千次请求。
    if (o.expired) {
      stop();
      return;
    }
    if (!timer) timer = setInterval(poll, 3000);
    return;
  }
  if (o.status === "paid" || o.status === "downloaded") {
    show("paid");
    document.getElementById("d-no").textContent = o.order_no;
    return;
  }
  show(o.status === "closed" ? "closed" : "landing");
  document.getElementById("x-no").textContent = o.order_no;
}

async function poll() {
  if (!currentNo || pollBusy) return;
  const no = currentNo, stamp = buySeq; pollBusy = true;
  try {
    const res = await fetch(`/shop/orders/${no}`, { credentials: "same-origin" });
    if (stamp !== buySeq || no !== currentNo) return;
    if (res.status === 401) { stop(); show("landing"); window.CodeMaxAuth?.sessionExpired?.(401); return; }
    if (!res.ok) return;
    const o = await res.json();
    if (stamp !== buySeq || no !== currentNo) return;
    if (!o || o.order_no !== no) throw new Error("订单响应格式无效");
    render(o);
    if (o.status !== "pending") stop();
  } catch (e) {
    if (stamp === buySeq) document.getElementById("buy-error").textContent = `状态暂时不可用，将重试：${e.message}`;
  } finally { pollBusy = false; }
}

function stop() { if (timer) { clearInterval(timer); timer = null; } }

// ---------------------------------------------------------------- 已购用户与返回恢复（V-05）
//
// 两件之前做不到的事（2026-09-23 复核 N-05 / C8）：
//   ① 已付款用户再看落地页，主按钮仍是「立即购买」—— 再点一次会**静默新建一张待支付单**。
//      单 SKU 数字商品重复购买没有价值（用户 2026-09-24 确认：不设计复购）。
//   ② 收银台「返回商城」或浏览器后退回到 /shop 时，页面是全新加载、currentNo 为 null，
//      `/shop` 又带 no-store（bfcache 被拒），于是又落回落地页，看不到刚付的订单。
//
// 做法：页面加载/登录后取一次自己的订单，按最新状态恢复视图；`?order=CM…` 直接在 URL 里
// 指名要看哪一单（收银台的返回链接带订单号）。
// 只在用户还没动过手（没点过购买/取消/历史订单）时自动恢复，免得和页面内的操作打架。
let entitledNo = null;   // 最近一张 paid/downloaded 且未退款的订单号；null = 没有已购权益
let restored = false;    // 本次加载是否已经自动恢复过
let userActed = false;   // 用户是否已经在本页点过东西

function resetPrimary() {
  entitledNo = null;
  buyBtn.textContent = BUY_TEXT;
  document.getElementById("buy-note").hidden = true;
}

// 已购权益换成主按钮「已购买，去下载」：点它直接回到那张单，不再 POST /shop/orders。
function offerDownload(order) {
  entitledNo = order.order_no;
  buyBtn.textContent = "已购买，去下载";
  const note = document.getElementById("buy-note");
  note.textContent = `你已经购买过此商品（订单 ${order.order_no}），同一商品无需重复购买；`
    + "点上方按钮即可重新领取下载链接。";
  note.hidden = false;
}

// 收银台返回链接带的 ?order=CM… ；只接受与订单号同形的字符，别把 URL 参数当选择器用。
function orderFromUrl() {
  try {
    const v = new URLSearchParams(location.search || "").get("order");
    return v && /^[A-Za-z0-9_-]{1,32}$/.test(v) ? v : null;
  } catch (e) { return null; }
}

// 取单张订单。返回订单对象；`undefined` = 这单不存在/不属于我（当作没有该参数）；
// `null` = 暂时读不到（未登录、网络或服务端问题）—— 留给下次身份变化再试。
async function fetchOrder(no) {
  try {
    const res = await fetch(`/shop/orders/${no}`, { credentials: "same-origin" });
    if (res.status === 401) return null;
    if (!res.ok) return undefined;
    const o = await res.json();
    return o && o.order_no === no ? o : undefined;
  } catch (e) { return null; }
}

// 订单列表按 id 倒序（最新在前），见后端 order_history。
function isOwned(o) { return (o.status === "paid" || o.status === "downloaded") && !o.refunded; }

async function restoreLatest() {
  let orders;
  try {
    const res = await fetch("/shop/orders", { credentials: "same-origin" });
    if (!res.ok) return;
    orders = (await res.json())?.orders;
  } catch (e) { return; }
  if (!Array.isArray(orders) || !orders.length) return;
  const newest = orders[0];
  const owned = orders.filter(isOwned);
  // ① 最新一张就是已购单：直接把这一单的状态显示出来（刚付完款返回商城时就是这条路，
  //    即使没带 ?order= 也能看到「支付成功」）。
  if (owned.length && owned[0].order_no === newest.order_no) { render(newest); return; }
  // ② 有已购权益：主按钮换成「已购买，去下载」。**已购优先于更晚的未付款单** ——
  //    已经买过就不该再为同一件商品付一次钱。刻意不自动跳支付成功视图，用户打开的是商品页。
  if (owned.length) { offerDownload(owned[0]); return; }
  // ③ 没有已购权益：未过期的待支付单接着付（用户可能在收银台中途关页/返回）。
  if (newest.status === "pending") {
    const o = await fetchOrder(newest.order_no);
    if (o && !o.refunded && !o.expired) render(o);
  }
}

async function bootstrap() {
  if (restored || userActed) return;
  const no = orderFromUrl();
  if (no) {
    const o = await fetchOrder(no);
    if (o === null) return;              // 未登录/暂时读不到：等身份确定后还有一次机会
    if (o) { restored = true; render(o); return; }
  }
  if (!window.CodeMaxAuth?.user) return; // 未登录：等登录后的 onChange 再恢复
  restored = true;
  await restoreLatest();
}

// 主按钮：有已购权益时是「去下载」，否则才是下单。
async function primary() {
  if (!entitledNo) return buy();
  userActed = true;
  const errEl = document.getElementById("buy-error");
  errEl.textContent = "";
  const o = await fetchOrder(entitledNo);
  if (o) { render(o); return; }
  if (o === undefined) {                 // 单子没了/不属于我（例如刚被退款又清了记录）
    resetPrimary();
    show("landing");
    return;
  }
  errEl.textContent = "暂时读不到订单状态，请稍后重试，或点「查看 / 刷新我的订单」。";
}

// 待补发的「登录后自动下单」监听器的退订函数；null 表示当前没有待补发的购买意图。
let offBuy = null;
// 每次 buy() 领一个递增序号。**只有最新尝试的响应才算数。**
// 少了这一层会出乱序 bug：用户未登录时点购买（请求 A 发出）→ 从顶栏登录 →
// 再点一次购买（请求 B 成功下单）→ 此时 A 才迟到返回 401 → A 照样把补单监听器
// 挂回去 → 用户将来某次重新登录（没点购买）凭空多下一单。
// 实测复现过：fetchCalls 2 → 3。
let buySeq = 0;

async function buy() {
  userActed = true;   // 用户明确点了购买：自动恢复不再介入（见 bootstrap）
  const attempt = buySeq + 1;
  try { await doBuy(); } catch (e) {
    if (attempt === buySeq) document.getElementById("buy-error").textContent = `下单失败：${e.message}`;
  }
}

async function doBuy() {
  const mySeq = ++buySeq;
  const errEl = document.getElementById("buy-error");
  errEl.textContent = "";
  const res = await fetch("/shop/orders", { method: "POST", credentials: "same-origin" });
  // 响应回来时若已有更新的下单尝试，这一份就是过期的，整条丢掉 ——
  // 既不弹登录浮层（用户可能早就登录了），也不登记补单意图。
  if (mySeq !== buySeq) return;
  if (res.status === 401) {
    // 未登录：唤起全站统一浮层，登录成功后自动继续下单 ——
    // 这就是「value first, ask later」：先让用户决定要买，再要求身份。
    CodeMaxAuth.open("login");
    // 已经有一个待补发的购买意图了：只把浮层再打开一次，不重复注册监听器。
    // 否则未登录时连点几次「立即购买」就会累积几个监听器，登录成功后一次触发多次下单
    // （后端「同一用户最多一张 pending 单」能兜住结果，但多余请求与错误意图不该发出去）。
    if (offBuy) return;
    // 登录成功后**只补发一次**下单，然后立刻退订。
    // 早先这里写的是 `CodeMaxAuth.onChange(() => {})` 当作「解绑」，但那个调用只会
    // **再追加一个空监听器**，旧的 `once` 一直留着 —— 于是：
    //   ① 用户下次登录（哪怕没点购买）会再触发一次 buy()，凭空多下一单；
    //   ② 未登录时多点几次「立即购买」会累积多个监听器，一次登录触发多次 buy()。
    // 实测复现过：只重新登录一次，下单调用次数 2 → 3；连点两次购买再登录，一次跳到 6。
    offBuy = CodeMaxAuth.onChange((u) => {
      if (!u) return;   // 退出登录时 user 为 null，不补单
      const off = offBuy;
      offBuy = null;    // 先清标记再退订：下单本身可能又 401，要允许重新登记
      if (off) off();
      buy();
    });
    return;
  }
  if (res.status === 503) {
    errEl.textContent = "支付通道暂未配置，暂时无法下单，请联系我们。";
    return;
  }
  if (!res.ok) {
    errEl.textContent = window.CodeMaxAuth?.errorText(await res.json().catch(() => null), res.status) || `下单失败（${res.status}）`;
    return;
  }
  const order = await res.json();
  if (mySeq !== buySeq) return;
  if (!order || !order.order_no || !order.status) throw new Error("订单响应格式无效");
  render(order);
}

// 从模拟收银台按「返回」或标签页重新可见时，立即查一次状态，而不是等最长 3 秒的下一次轮询：
// 用户刚在别处付完款回来，页面应当马上从"待支付"切到"支付成功"（TD-270）。
// 只在有待支付订单在看的时候触发；bfcache 恢复（pageshow.persisted）时定时器可能已被 pagehide 停掉。
function refreshOnReturn() {
  if (!currentNo) return;
  if (!timer) timer = setInterval(poll, 3000);
  poll();
}
window.addEventListener("pageshow", (ev) => { if (ev.persisted) refreshOnReturn(); });
document.addEventListener?.("visibilitychange", () => {
  if (document.visibilityState === "visible") refreshOnReturn();
});

buyBtn.onclick = primary;
document.getElementById("btn-rebuy").onclick = buy;
document.getElementById("btn-cancel").onclick = () => {
  userActed = true;
  ++buySeq; currentNo = null; stop(); show("landing");
  if (offBuy) { offBuy(); offBuy = null; }
};

// 下载：**只有用户主动点击才调**。成功后立刻打开链接。
document.getElementById("btn-download").onclick = async () => {
  const stamp = buySeq, no = currentNo;
  const errEl = document.getElementById("d-error"); errEl.textContent = "";
  try {
    const res = await fetch(`/shop/download/${no}`, { method: "POST", credentials: "same-origin" });
    // 非 JSON 应答（反向代理的 502/504 HTML 页）不能变成「Unexpected token <」这种天书：
    // 解析失败按 null 处理，交给下面的 errorText 给出「请求失败（502）」。
    const data = await res.json().catch(() => null);
    if (stamp !== buySeq || no !== currentNo) return;
    if (window.CodeMaxAuth?.sessionExpired?.(res.status)) throw new Error("登录已过期，请重新登录后再领取");
    if (!res.ok) throw new Error(window.CodeMaxAuth?.errorText(data, res.status) || `下载失败（${res.status}）`);
    if (typeof data?.download_url !== "string" || !/^https?:\/\//.test(data.download_url)) throw new Error("下载响应格式无效");
    window.location.href = data.download_url;
    show("paid");
  } catch (e) { if (stamp === buySeq) errEl.textContent = `未完成下载，可重试：${e.message}`; }
};

async function loadHistory(more = false) {
  const stamp = buySeq, box = document.getElementById("order-history");
  try {
    const res = await fetch("/shop/orders" + (more && historyCursor ? `?before=${historyCursor}` : ""), { credentials: "same-origin" });
    const data = await res.json().catch(() => null);   // 同上：代理错误页不是 JSON
    if (stamp !== buySeq) return;
    if (window.CodeMaxAuth?.sessionExpired?.(res.status)) throw new Error("登录已过期，请重新登录后查看订单");
    if (!res.ok || !Array.isArray(data?.orders)) throw new Error(window.CodeMaxAuth?.errorText(data, res.status) || "请登录后查看订单");
    if (!more) box.innerHTML = "";
    const labels = { pending: "待付款", paid: "可下载", downloaded: "可重新下载", closed: "已关闭" };
    for (const order of data.orders) {
      const button = document.createElement("button"); button.type = "button";
      button.textContent = `${order.order_no} · ${order.refunded ? "已全额退款（不可下载）" : labels[order.status] || order.status}`;
      button.onclick = () => { userActed = true; ++buySeq; stop(); render(order); };
      box.appendChild(button);
    }
    historyCursor = data.next_cursor; document.getElementById("btn-history-more").hidden = !historyCursor;
    document.getElementById("history-error").textContent = data.orders.length ? "" : "暂无订单";
  } catch (e) { if (stamp === buySeq) document.getElementById("history-error").textContent = e.message; }
}
document.getElementById("btn-history").onclick = () => loadHistory();
document.getElementById("btn-history-more").onclick = () => loadHistory(true);
if (window.CodeMaxAuth) {
  let identity = window.CodeMaxAuth.user?.username || null;
  window.CodeMaxAuth.onChange((user) => {
    const next = user?.username || null;
    if (next === identity) return;
    identity = next; ++buySeq; stop(); currentNo = null; show("landing");
    document.getElementById("order-history").innerHTML = "";
    document.getElementById("history-error").textContent = "";
    historyCursor = null; document.getElementById("btn-history-more").hidden = true;
    if (!user && offBuy) { offBuy(); offBuy = null; }
    // 换人/退出后重新按新身份恢复：已购按钮不能跟着上一个账号留下来（账号隔离）。
    resetPrimary();
    restored = false;
    if (user) bootstrap();
  });
  if (identity) bootstrap();   // auth.js 可能在本脚本之前就完成了首次身份解析
}

// 进页面时**不自动下单**：自动下单会在用户还没决定时就产生订单。
// 只有点了「立即购买」才与后端交互（复用已有 pending 单的逻辑在后端 create_order 里）。
// 早先这里有一行 `CodeMaxAuth.onChange(() => {})`，它什么都不做、只是往监听器列表里
// 塞一个空函数 —— 纯粹的泄漏，已删除。
