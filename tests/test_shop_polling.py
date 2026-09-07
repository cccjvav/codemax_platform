# tests/test_shop_polling.py
#
# 下单页的**轮询生命周期**（CODE_REVIEW_99662ca 的 N-2）。
#
# 这组测试钉住两条不变式：
#
# 1. **订单过期后必须停表。** 二维码只有 `ORDER_EXPIRE_MINUTES` 分钟有效，
#    但 `setInterval(poll, 3000)` 一开就没人关 —— 用户开着标签页去吃个午饭，
#    就是一张早就失效的二维码以 3 秒一次无限打后端。
# 2. **状态查询不许被缓存。** 浏览器/中间代理一旦缓存了这个 200 GET，
#    页面会永远看不到订单变 `paid`：用户付了钱，页面一直转圈。
"""N-2：过期订单停轮询 + 状态接口不可缓存。"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from tests.conftest import TestSession

pytestmark = pytest.mark.asyncio(loop_scope="session")

from tests.test_download import auth_headers, make_order  # noqa: E402

# ---------------------------------------------------------------- 前端：过期停表
#
# 用 node 跑 shop.html 里**真实的内联脚本**（不是复制一份逻辑来测 ——
# 复制出来的副本会和真实代码各自演化，改坏了照样绿）。
# 关键是给 setInterval/clearInterval 装上假时钟，这样能确定性地
# 「快进到 9 秒后」而不用真的睡 9 秒。

_EXPIRY_HARNESS = r"""
const els = {};
const mkEl = () => ({
  value: "", textContent: "", innerHTML: "", hidden: false,
  appendChild() {}, click() {}, addEventListener() {}, focus() {},
  classList: { add() {}, remove() {} },
});
global.document = { getElementById: (id) => (els[id] ||= mkEl()), createElement: mkEl };
// 点击必须真的触发 shop.html 里 `btn.onclick = buy` 绑上去的函数，
// 否则「点购买」是空操作，两条用例都只会因为什么都没发生而失败。
const click = (id) => { const el = els[id]; if (el && el.onclick) return el.onclick({ preventDefault() {} }); };
global.addEventListener = () => {};
global.window = global;
global.window.location = { href: "" };
const store = { getItem(){}, setItem(){}, removeItem(){}, clear(){} };
global.localStorage = store; global.sessionStorage = store;

let expired = false;
let pollCalls = 0;
// ⚠️ 变量名不能叫 `timer`：shop.html 的内联脚本自己就 `let timer = null`，
// 两段脚本拼在同一个作用域里执行，重名会直接 SyntaxError。
let __pendingTimer = null;

global.fetch = async (url) => {
  // 下单（POST /shop/orders，无尾斜杠）：必须真的返回 code_url，
  // 否则 render() 走不到 show("pending")，定时器压根不会起 ——
  // 那样两条用例都会「因为什么都没发生」而失败，看不出真实行为。
  if (url === "/shop/orders") {
    return { ok: true, status: 200, json: async () => ({
      order_no: "CM1", status: "pending", expired: false,
      code_url: "weixin://wxpay/bizpayurl?x=1", qr_svg: "<svg></svg>", qr_image: null,
    }) };
  }
  if (url.startsWith("/shop/orders/")) {
    pollCalls += 1;
    return { ok: true, status: 200, json: async () => ({
      order_no: "CM1", status: "pending", expired,
    }) };
  }
  return { ok: true, status: 200, json: async () => ({}) };
};

// 假时钟：setInterval 只登记，由 advance() 手动推进
global.setInterval = (fn, ms) => { const id = { fn, ms }; __pendingTimer = id; return id; };
global.clearInterval = (id) => { if (id === __pendingTimer) __pendingTimer = null; };
global.setTimeout = (fn) => fn();
global.__state = () => ({ pollCalls, timerActive: __pendingTimer !== null });
global.__expire = () => { expired = true; };
global.__advance = async (ms) => {
  // ⚠️ 必须逐个 await：poll() 是 async 函数，同步连打 tick 会在第一个
  // render() 跑完之前把后面几次全发出去 —— 那样「过期即停表」根本没机会
  // 生效，用例测到的是假象。
  const ticks = Math.floor(ms / (__pendingTimer ? __pendingTimer.ms : 3000));
  for (let i = 0; i < ticks; i++) {
    if (!__pendingTimer) return;
    await __pendingTimer.fn();
  }
};
"""


def _run_expiry_scenario(scenario: str) -> dict:
    """按浏览器真实顺序执行 auth.js + shop.html 内联脚本，返回末行 JSON。"""
    root = Path(__file__).resolve().parents[1]
    auth = (root / "app/frontend/auth.js").read_text(encoding="utf-8")
    shop_html = (root / "app/templates/shop.html").read_text(encoding="utf-8")
    inline = re.findall(r"<script>(.*?)</script>", shop_html, re.S)
    assert inline, "shop.html 应该有自己的内联脚本"
    script = "\n;\n".join([auth, *inline])

    harness = Path("/tmp/_shop_expiry_harness.js")
    harness.write_text(_EXPIRY_HARNESS + "\n;\n" + script + "\n" + scenario, encoding="utf-8")
    proc = subprocess.run(["node", str(harness)], capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, f"前端脚本执行失败：\n{proc.stdout}\n{proc.stderr}"
    return json.loads(proc.stdout.strip().splitlines()[-1])


@pytest.mark.skipif(shutil.which("node") is None, reason="未安装 node")
def test_polling_stops_once_the_order_expires():
    """**过期即停表。**

    为什么原来的代码停不下来：订单到期时 `status` 仍是 `pending`，于是
      · `show("pending")` 不清定时器（它的条件是 `name !== "pending"`）
      · `poll()` 里的 `o.status !== "pending"` 也为假，不会 `stop()`
    而 `if (!o.expired && !timer)` 只是「不 expired 才起表」，
    对**已经在跑**的定时器毫无作用。实测 9.5 秒里打了 3 次，且永不停止。
    """
    out = _run_expiry_scenario(
        """
(async () => {
  await click("btn-buy");   // 真的点一次购买（id 是 btn-buy，不是 buy-btn）
  const r1 = { afterOrder: __state() };
  __expire();               // 页面开着的时候订单到期了
  await __advance(9000);    // 快进 9 秒（3 个轮询周期）
  console.log(JSON.stringify({ ...r1, afterExpiry: __state() }));
})();
        """
    )
    assert out["afterOrder"]["timerActive"], "刚下单应该起轮询（前置条件）"
    assert out["afterExpiry"]["pollCalls"] == 1, (
        f"过期后又打了 {out['afterExpiry']['pollCalls'] - 1} 次轮询，期望 0 —— "
        "过期订单的定时器没被清掉。"
    )
    assert out["afterExpiry"]["timerActive"] is False, "过期后定时器必须停掉"


@pytest.mark.skipif(shutil.which("node") is None, reason="未安装 node")
def test_polling_starts_for_a_live_order():
    """对照组：没过期就该正常起表。

    只测「过期会停」是单侧断言 —— 万一改成了无条件 stop()，上一条照样绿。
    """
    out = _run_expiry_scenario(
        """
(async () => {
  await click("btn-buy");
  await __advance(12000);   // 12 秒 = 4 个轮询周期
  console.log(JSON.stringify({ afterAdvance: __state() }));
})();
        """
    )
    assert out["afterAdvance"]["timerActive"], "未过期的订单必须持续轮询"
    assert out["afterAdvance"]["pollCalls"] == 4, (
        f"12 秒轮询了 {out['afterAdvance']['pollCalls']} 次，期望 4（3 秒一次）"
    )


# ---------------------------------------------------------------- 后端：禁止缓存


async def test_order_status_response_is_not_cacheable(client: TestSession, mock_mode, product):
    """状态查询必须带 `Cache-Control: no-store`。

    这是个每 3 秒一次的轮询接口，而它是个普通的 200 GET ——
    浏览器和中间代理都有资格缓存它。一旦命中缓存，订单变 paid 这件事
    **永远传不到页面**：用户付了钱，页面一直转圈，客服也查不出所以然。
    """
    h = await auth_headers(client)
    order_no = await make_order("pending")

    resp = await client.get(f"/shop/orders/{order_no}", headers=h)
    assert resp.status_code == 200, resp.text
    assert resp.headers.get("Cache-Control") == "no-store", (
        f"实际 Cache-Control={resp.headers.get('Cache-Control')!r} —— "
        "轮询接口可能被缓存，用户会看不到自己的订单变已支付。"
    )
