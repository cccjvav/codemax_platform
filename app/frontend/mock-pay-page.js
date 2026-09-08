// mock-pay-page 页面的交互脚本。
//
// 原先是 mock_pay.html 里的内联 <script>，C2 搬到这里。搬出来的理由：
// ① 内联脚本要么放宽 CSP 的 'unsafe-inline'，要么逐页配 nonce（TD-163 的方向），
//    外部文件由 `script-src 'self'` 直接覆盖；
// ② 内联在模板里的 JS 无法被构建工具处理（不能压缩、不能拆分、报错没有源文件行号）。
//
// ⚠️ 模块脚本默认 defer，执行时 DOM 已解析完，可直接取元素。

const orderNo = document.getElementById("no").textContent.trim();
const out = document.getElementById("out");

document.getElementById("pay").addEventListener("click", async () => {
  // 登录态由 HttpOnly cookie 携带（TD-44），脚本读不到；没登录就让后端回 401，
  // 下面分支负责把话说清楚。
  const res = await fetch("/shop/mock-pay/confirm", {
    method: "POST",
    credentials: "same-origin",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({order_no: orderNo}),
  });
  const data = await res.json();
  out.textContent = res.status === 200
    ? `支付成功（模拟）\n状态：${data.status}\n模拟流水号：${data.transaction_id}`
    : res.status === 401
      ? "未登录：请先在工具页登录后再来。"
      : `失败 ${res.status}：${data.detail || JSON.stringify(data)}`;
});
