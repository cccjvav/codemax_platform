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
  downloaded: document.getElementById("st-downloaded"),
};
let timer = null;
let currentNo = null;

function show(name) {
  Object.entries(ST).forEach(([k, el]) => { el.hidden = k !== name; });
  if (name !== "pending" && timer) { clearInterval(timer); timer = null; }
}

const yuan = (fen) => `¥${(fen / 100).toFixed(2)}`;

// 按订单状态切页面。
// ⚠️ 这里**绝不**调用 POST /shop/download —— 那个接口会把订单一次性烧成 downloaded
//    （后端 CAS 只让一个请求拿到链接）。所以状态只能从 GET /shop/orders/{no} 读，
//    下载必须由用户主动点按钮触发。
function render(o) {
  currentNo = o.order_no;
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
      const b = document.createElement("button");
      b.type = "button";
      b.className = "cta";
      b.textContent = "前往收银台支付";
      a.appendChild(b);
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
  if (o.status === "paid") {
    show("paid");
    document.getElementById("d-no").textContent = o.order_no;
    return;
  }
  show("downloaded"); // downloaded 或 closed 都落到这里
  document.getElementById("x-no").textContent = o.order_no;
}

async function poll() {
  if (!currentNo) return;
  const res = await fetch(`/shop/orders/${currentNo}`, { credentials: "same-origin" });
  if (res.status === 401) { stop(); show("landing"); return; }
  if (!res.ok) return;
  const o = await res.json();
  render(o);
  if (o.status !== "pending") stop();
}

function stop() { if (timer) { clearInterval(timer); timer = null; } }

// 待补发的「登录后自动下单」监听器的退订函数；null 表示当前没有待补发的购买意图。
let offBuy = null;
// 每次 buy() 领一个递增序号。**只有最新尝试的响应才算数。**
// 少了这一层会出乱序 bug：用户未登录时点购买（请求 A 发出）→ 从顶栏登录 →
// 再点一次购买（请求 B 成功下单）→ 此时 A 才迟到返回 401 → A 照样把补单监听器
// 挂回去 → 用户将来某次重新登录（没点购买）凭空多下一单。
// 实测复现过：fetchCalls 2 → 3。
let buySeq = 0;

async function buy() {
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
    errEl.textContent = (await res.json().catch(() => null))?.detail || `下单失败（${res.status}）`;
    return;
  }
  render(await res.json());
}

document.getElementById("btn-buy").onclick = buy;
document.getElementById("btn-rebuy").onclick = buy;
document.getElementById("btn-cancel").onclick = () => { stop(); show("landing"); };

// 下载：**只有用户主动点击才调**。成功后立刻打开链接。
document.getElementById("btn-download").onclick = async () => {
  const errEl = document.getElementById("d-error");
  errEl.textContent = "";
  const res = await fetch(`/shop/download/${currentNo}`, {
    method: "POST",
    credentials: "same-origin",
  });
  if (!res.ok) {
    const detail = (await res.json().catch(() => null))?.detail;
    errEl.textContent = detail || `下载失败（${res.status}）`;
    // 403 且提示"已下载过"说明这单已经烧掉了，直接切到对应状态，
    // 免得用户反复点一个永远不会成功的按钮。
    if (res.status === 403) { show("downloaded"); document.getElementById("x-no").textContent = currentNo; }
    return;
  }
  const data = await res.json();
  window.location.href = data.download_url;
  show("downloaded");
  document.getElementById("x-no").textContent = currentNo;
};

// 进页面时**不自动下单**：自动下单会在用户还没决定时就产生订单。
// 只有点了「立即购买」才与后端交互（复用已有 pending 单的逻辑在后端 create_order 里）。
// 早先这里有一行 `CodeMaxAuth.onChange(() => {})`，它什么都不做、只是往监听器列表里
// 塞一个空函数 —— 纯粹的泄漏，已删除。
