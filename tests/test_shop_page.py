# tests/test_shop_page.py
#
# S2-02-2「免费引流 → 商业变现」的转化路径。
#
# 这组测试的重点不是"页面能打开"，而是钉住三条容易在后续改动中被破坏的不变式：
#
# 1. **轮询订单状态绝不能烧掉可恢复下载权益。** `POST /shop/download/{order_no}` 成功后
#    状态可记为 `downloaded`，但同一已付款订单仍可重领链接；不证明传输已完成。
#    下单页每 3 秒轮询一次 `GET /shop/orders/{no}`，谁要是图省事拿 download 当状态查询，
#    会把只读轮询变成写操作。领取与查询必须分离，轮询不能改变购买权益。
# 2. **同意页零脚本。** base.html 现在带了全站登录模块，但 OAuth 同意页必须一个脚本都没有
#    （`auth_ui=False`）。这条在 test_oauth_consent.py 里，这里只补它的对偶：
#    其它页面确实拿到了登录入口。
# 3. **二维码是内联 SVG，不引入任何外部请求。** CSP 的 `connect-src 'self'` 会拦掉
#    第三方请求，所以二维码不能靠 CDN 画。
"""S2-02-2：商城落地页、订单状态轮询与二维码。"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest
from sqlalchemy import select

from app.config import settings
from app.models import Order, User
from app.routers import shop
from tests.conftest import TestSession, iter_app_routes

# product fixture 在 conftest 里（pytest 自动发现），这里只需要这三个 helper
from tests.test_download import auth_headers, make_order, status_of
from tests.test_wechat_pay import CFG

_seq = 0



# ---------------------------------------------------------------- 落地页


async def test_shop_page_renders_product(client):
    """落地页必须把商品名与价格渲染出来，且价格来自配置而不是写死。"""
    r = await client.get("/shop")
    assert r.status_code == 200
    assert settings.SHOP_PRODUCT_NAME in r.text
    # 金额以「分」存储，页面必须换算成元
    assert f"¥{settings.SHOP_PRODUCT_AMOUNT / 100:.2f}" in r.text
    assert "btn-buy" in r.text, "落地页必须有下单按钮"


async def test_shop_page_explains_recoverable_download(client):
    """页面必须说清过期或中断可重领，不能让用户误以为再次下载会丢失权益。"""
    text = (await client.get("/shop")).text
    assert "过期或中断可重新领取" in text
    assert "一次性有效" not in text


async def test_shop_page_is_in_sitemap(client):
    """加进 PAGES 就该自动进 sitemap（SEO 收录）。"""
    urls = re.findall(r"<loc>(.*?)</loc>", (await client.get("/sitemap.xml")).text)
    assert any(u.endswith("/shop") for u in urls), urls


async def test_shop_page_not_in_tool_nav(client):
    """SHOP 进 PAGES 但**不进 TOOLS** —— 商业页不该混在免费工具导航里。"""
    from app.site import TOOLS

    assert all(t.path != "/shop" for t in TOOLS)
    # 首页的工具卡片区只遍历 TOOLS，所以不该出现指向 /shop 的卡片
    home = (await client.get("/")).text
    assert home.count('class="card"') == len(TOOLS)


@pytest.mark.parametrize("path", ["/", "/shop", "/tools/er", "/tools/mermaid", "/tools/drawio"])
async def test_every_page_has_global_entry_points(client, path):
    """转化路径的前提：任何页面都能走到登录与商城。

    改动前 base.html 的 header 只有工具导航 —— 从搜索进来的游客**找不到任何登录入口**，
    也没有页脚。这条测试就是把"全站都有入口"钉住。
    """
    html = (await client.get(path)).text
    assert "<footer>" in html, f"{path} 缺页脚"
    assert 'id="btn-auth"' in html, f"{path} 缺登录入口"
    assert 'href="/shop"' in html, f"{path} 缺商城入口"
    assert "/static/js/auth.js" in html, f"{path} 没加载共享登录模块"


async def test_auth_js_is_served_and_defines_module(client):
    """共享登录模块是外部文件（不是内联脚本），必须真的能取到。

    做成外部文件的原因：base.html 也是 OAuth 同意页的父模板，
    而那页必须零内联脚本（见 test_oauth_consent.py）。
    """
    r = await client.get("/static/js/auth.js")
    assert r.status_code == 200
    assert "CodeMaxAuth" in r.text
    assert "/auth/login" in r.text
    assert "/auth/register" in r.text, "注册入口也在这个模块里（此前全站没有任何页面能注册）"


# ---------------------------------------------------------------- 订单状态查询


async def test_order_status_returns_pending(client, mock_mode):
    h = await auth_headers(client)
    no = (await client.post("/shop/orders", headers=h)).json()["order_no"]
    r = await client.get(f"/shop/orders/{no}", headers=h)
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "pending"
    assert body["expired"] is False
    assert body["order_no"] == no


async def test_order_status_requires_login(client, mock_mode):
    """未登录必须 401。

    ⚠️ 订单是**直接造**的，不能先调 `auth_headers(client)` 再取掉请求头 ——
    那个 helper 是在**同一个 client** 上登录的，Set-Cookie 会留在 client 的
    cookie jar 里，之后不带 Authorization 头也照样通过（我第一版就是这么写错的，
    测出来 200 而不是 401）。
    """
    no = await make_order("pending")
    assert (await client.get(f"/shop/orders/{no}")).status_code == 401


async def test_order_status_other_users_order_is_404(client, mock_mode):
    """非本人一律 404，不用 403 —— 不暴露"这个订单号存在"。"""
    h1 = await auth_headers(client, "buyer1", "secret123")
    no = (await client.post("/shop/orders", headers=h1)).json()["order_no"]
    h2 = await auth_headers(client, "buyer2", "secret123")
    assert (await client.get(f"/shop/orders/{no}", headers=h2)).status_code == 404


async def test_order_status_unknown_order_is_404(client):
    h = await auth_headers(client)
    assert (await client.get("/shop/orders/CM00000000XX", headers=h)).status_code == 404


async def test_status_polling_preserves_download_entitlement(client, product, mock_mode):
    """**这组测试里最重要的一条。**

    下单页每 3 秒轮询状态。如果轮询走的是 `POST /shop/download`（它会把订单
    一次性烧成 `downloaded`），用户付完钱就来不及下载了。
    这里模拟"轮询 5 次然后再下载"，下载必须仍然成功。
    """
    h = await auth_headers(client)
    no = await make_order("paid")

    for _ in range(5):  # 模拟轮询
        r = await client.get(f"/shop/orders/{no}", headers=h)
        assert r.status_code == 200
        assert r.json()["status"] == "paid", "轮询不该改变状态"

    assert await status_of(no) == "paid", "轮询把状态改了 —— GET 必须只读"

    dl = await client.post(f"/shop/download/{no}", headers=h)
    assert dl.status_code == 200, (
        "轮询之后下载失败了 —— 说明轮询路径错误改变了下载资格。"
        f"响应：{dl.text}"
    )
    assert "download_url" in dl.json()


async def test_status_reflects_downloaded(client, product, mock_mode):
    """下载后轮询要能看到 downloaded，页面才能切到"已下载"态。"""
    h = await auth_headers(client)
    no = await make_order("paid")
    await client.post(f"/shop/download/{no}", headers=h)
    r = await client.get(f"/shop/orders/{no}", headers=h)
    assert r.json()["status"] == "downloaded"


async def test_status_reports_expired_without_closing(client, mock_mode):
    """过期只**报告**，不在轮询里写库关单。

    轮询每 3 秒一次，在里面写库（哪怕只是关个过期单）会把读接口变成写接口 ——
    这既是性能问题，也违反 test_auth_cookie.py 里"需登录的 GET 必须无副作用"的约束。
    关单只在 create_order 里做。
    """
    from datetime import datetime, timedelta, timezone

    h = await auth_headers(client)
    no = await make_order("pending")
    async with TestSession() as s:  # 把创建时间推到 TTL 之外
        o = (await s.execute(select(Order).where(Order.order_no == no))).scalar_one()
        o.create_time = datetime.now(timezone.utc) - timedelta(
            minutes=settings.ORDER_EXPIRE_MINUTES + 1
        )
        await s.commit()

    body = (await client.get(f"/shop/orders/{no}", headers=h)).json()
    assert body["expired"] is True, "应该报告已过期"
    assert body["status"] == "pending", "但**不该**顺手把单子关掉"


# ---------------------------------------------------------------- 二维码


async def test_mock_mode_returns_link_not_qr(client, mock_mode):
    """mock 模式的 code_url 是本站 http 链接，画成二维码反而多一步扫码。"""
    h = await auth_headers(client)
    body = (await client.post("/shop/orders", headers=h)).json()
    assert body["code_url"].startswith("http")
    assert body["qr_svg"] is None


async def test_wechat_mode_returns_inline_qr_svg(client, monkeypatch):
    """真实微信模式下 code_url 是 `weixin://` 串，必须给出二维码。"""
    monkeypatch.setattr(settings, "SHOP_PAY_MODE", "wechat")
    # 沙箱没有商户号，`pay_config().configured` 是 False，下单会先撞 503 门禁
    # 使用完整合成CFG，通过下单与本地回调配置检查；只替换实际商户网络调用。
    monkeypatch.setattr(shop, "pay_config", lambda: CFG)

    async def fake_prepay(cfg, **kw):
        return "weixin://wxpay/bizpayurl?pr=AbCdEfGh"

    monkeypatch.setattr(shop, "native_prepay", fake_prepay)
    h = await auth_headers(client)
    body = (await client.post("/shop/orders", headers=h)).json()
    svg = body["qr_svg"]
    assert svg and svg.startswith("<svg")
    # 内联 SVG：不含脚本、不含任何外部请求（CSP connect-src 'self' 会拦第三方）
    assert "<script" not in svg.lower()
    assert "http" not in svg
    assert "href" not in svg


async def test_qr_is_only_generated_for_own_orders(client, mock_mode):
    """二维码只在订单 payload 里给出，不存在"任意内容生成二维码"的接口。

    那种接口会被拿去做钓鱼（生成指向恶意站点的码）。这里断言路由表里没有它。
    """
    from fastapi.routing import APIRoute

    from main import app

    paths = {r.path for r in iter_app_routes(app.routes) if isinstance(r, APIRoute)}
    assert not [p for p in paths if "qr" in p.lower()], f"不该有独立的二维码接口：{paths}"


async def test_user_model_has_no_leftover_fixture_user(client):
    """占位测试：确认 make_order 造的用户确实落库了（其它测试依赖它）。"""
    await make_order("paid", username="probe_user")
    async with TestSession() as s:
        u = (
            await s.execute(select(User).where(User.username == "probe_user"))
        ).scalar_one_or_none()
    assert u is not None


# =============================================== 前端行为：登录后自动补单（S5-05）
#
# 这一节是**真实执行前端代码**，不是 grep 模板。
#
# 起因是复审抓到的一个真 bug：`buy()` 在 401 时注册一个「登录成功后补一次下单」的
# 监听器，但当时 `CodeMaxAuth.onChange` 只会 push、没有退订接口，而代码里写的
# `CodeMaxAuth.onChange(() => {})` 被当成了「解绑」—— 它其实只是**再追加一个空监听器**。
# 后果是监听器永久残留：
#   ① 用户下次登录（哪怕根本没点购买）会再触发一次 buy()，凭空多下一单；
#   ② 未登录时多点几次「立即购买」会累积多个监听器，一次登录触发多次 buy()。
# 实测复现过：只重新登录一次，下单调用次数 2 → 3。
#
# 静态 grep 完全看不出这个 bug —— 模板里那行注释还写着「避免重复绑定」。
# 只有把 auth.js 与页面脚本按浏览器顺序拼起来真跑一遍才暴露。

_NODE_EXECUTOR = r"""
const els = {};
const mkEl = () => ({
  value: "", textContent: "", innerHTML: "", hidden: false, width: 0, alt: "",
  appendChild() {}, click() {}, addEventListener() {}, focus() {},
  classList: { add() {}, remove() {} },
});
global.document = { getElementById: (id) => (els[id] ||= mkEl()), createElement: mkEl };
global.addEventListener = () => {};
global.window = global;              // 浏览器里 window 就是全局对象本身
global.window.location = { href: "" };
const store = { getItem(){}, setItem(){}, removeItem(){}, clear(){} };
global.localStorage = store; global.sessionStorage = store;

let loggedIn = false, orderCalls = 0;
// parkNext=true 时，下一个下单请求会被挂起，由场景代码手动放行 ——
// 用来制造「旧请求迟到返回」这种乱序时序（默认 false，不影响顺序场景）。
const parked = [];
let parkNext = false;
global.fetch = (url, opts = {}) => {
  if (url === "/shop/orders") {
    orderCalls += 1;
    if (parkNext) {
      parkNext = false;
      // **必须在请求发出时就冻结登录态**：迟到的响应反映的是「当时」的状态，
      // 不是放行那一刻的状态。写成放行时才求值，用户在等待期间登录后
      // 这个「迟到的 401」就变成了 200 —— 乱序场景根本没被测到，用例假绿。
      const wasLoggedIn = loggedIn;
      const resp = wasLoggedIn
        ? { ok: true, status: 200, json: async () => ({
            order_no: "CM1", code_url: "", qr_svg: null, qr_image: null,
            product_name: "p", amount: 19900, status: "pending",
            reused: false, pay_mode: "manual", expired: false }) }
        : { ok: false, status: 401, json: async () => ({}) };
      return new Promise((r) => parked.push(() => r(resp)));
    }
    return Promise.resolve(loggedIn
      ? { ok: true, status: 200, json: async () => ({
          order_no: "CM1", code_url: "", qr_svg: null, qr_image: null,
          product_name: "p", amount: 19900, status: "pending",
          reused: false, pay_mode: "manual", expired: false }) }
      : { ok: false, status: 401, json: async () => ({}) });
  }
  if (url === "/auth/me") return loggedIn
    ? Promise.resolve({ ok: true, status: 200, json: async () => ({ id: 1, username: "a", nickname: "A" }) })
    : Promise.resolve({ ok: false, status: 401, json: async () => ({}) });
  if (url === "/auth/login")  { loggedIn = true;  return Promise.resolve({ ok: true, status: 200, json: async () => ({ access_token: "T" }) }); }
  if (url === "/auth/logout") { loggedIn = false; return Promise.resolve({ ok: true, status: 204, json: async () => ({}) }); }
  return Promise.resolve({ ok: true, status: 200, json: async () => ({}) });
};
"""

_NODE_SCENARIO = r"""
const tick = () => new Promise((r) => setTimeout(r, 30));
async function loginAs(name) {
  els["auth-user"].value = name; els["auth-pass"].value = "pw123456";
  await els["auth-form"].onsubmit({ preventDefault() {} });
  await tick();
}
(async () => {
  const at = [];
  const snap = () => at.push(orderCalls);

  await els["btn-buy"].onclick();                       snap();  // ① 一次 401
  await els["btn-buy"].onclick();
  await els["btn-buy"].onclick(); await tick();          snap();  // ② 连点两次：只该登记一个意图
  await loginAs("alice");                                snap();  // ③ 登录 → 只补发一次
  await els["btn-logout"].onclick(); await tick();
  await loginAs("bob");                                  snap();  // ④ 没点购买就再登录 → 不许变
  await els["btn-logout"].onclick(); await tick();
  await els["btn-buy"].onclick(); await tick();
  await loginAs("carol");                                snap();  // ⑤ 守卫已释放，能重新登记

  stop(); // release the live polling timer before terminating this Node scenario
  console.log(JSON.stringify(at));
})();
"""


def _run_shop_frontend(scenario: str = _NODE_SCENARIO) -> list[int]:
    """按浏览器真实的文档顺序执行 `auth.js` + 下单页脚本 `shop-page.js`，返回各步的下单调用数。"""
    root = Path(__file__).resolve().parents[1]
    auth = (root / "app/frontend/auth.js").read_text(encoding="utf-8")
    # C3 之后下单页脚本是外部文件 app/frontend/shop-page.js（原先是 shop.html 的内联块）。
    # 仍然读**源码**而不是构建产物：产物压缩过，注释全没了，而下面这些断言靠的就是注释。
    shop_js = (root / "app/frontend/shop-page.js").read_text(encoding="utf-8")
    assert shop_js.strip(), "下单页脚本是空的 —— 很可能读错了文件"
    script = "\n;\n".join([auth, shop_js])

    # tempfile 而不是写死 /tmp：Windows 没有 /tmp（验收指南第 14 步在 Windows 上跑全量 pytest），
    # 并发跑时也不会互相踩（TD-270）。
    harness = Path(tempfile.gettempdir()) / f"_shop_frontend_harness_{os.getpid()}.js"
    harness.write_text(_NODE_EXECUTOR + "\n;\n" + script + "\n" + scenario, encoding="utf-8")
    proc = subprocess.run(["node", str(harness)], capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, f"前端脚本执行失败：\n{proc.stdout}\n{proc.stderr}"
    return json.loads(proc.stdout.strip().splitlines()[-1])


@pytest.mark.skipif(shutil.which("node") is None, reason="未安装 node")
def test_login_retry_does_not_replay_buy_on_later_logins():
    """**这一节最重要的一条**：登录后自动补单必须是**一次性**的。

    各步的预期下单调用数（累计）：
      ① 点一次购买、未登录           → 1（一次 401）
      ② 未登录再连点两次             → 3（三次 401，但只登记一个购买意图）
      ③ 登录成功                     → 4（**只补发一次**，不是三次）
      ④ 退出后换个人再登录、没点购买 → 4（**不许变** —— 变了就是幽灵下单）
      ⑤ 再点一次购买并登录           → 6（守卫已释放，允许重新登记）
    """
    at = _run_shop_frontend()
    assert at == [1, 3, 4, 4, 6], (
        f"下单调用序列不符：{at}（预期 [1, 3, 4, 4, 6]）。"
        "第 4 个数变大 = 旧监听器重放了下单；第 3 个数超过 4 = 一次登录补发了多次。"
    )


@pytest.mark.skipif(shutil.which("node") is None, reason="未安装 node")
def test_auth_module_exposes_an_unsubscribe():
    """`onChange` 必须返回退订函数 —— 没有退订能力，上面那个 bug 就无从修起。"""
    root = Path(__file__).resolve().parents[1]
    auth = (root / "app/frontend/auth.js").read_text(encoding="utf-8")
    assert "listeners.splice" in auth, "onChange 应当能真正把监听器移除"
    assert "listeners.slice()" in auth, "notify 必须遍历副本，否则回调里退订会跳过元素"


def test_shop_template_no_longer_fakes_unsubscribe():
    """回归：那行被当成「解绑」的 `onChange(() => {})` 不许再以**代码**形式出现。

    它看起来像在清理监听器，实际只是往列表里追加一个空函数 —— 纯泄漏。
    注意要先剥掉 `//` 注释：解释这个 bug 的注释里必须引用原句，否则会自我误伤。
    """
    js = (Path(__file__).resolve().parents[1] / "app/frontend/shop-page.js").read_text(encoding="utf-8")
    code = "\n".join(re.sub(r"//.*$", "", line) for line in js.splitlines())
    assert "onChange(() => {})" not in code, "这行是伪解绑，只会追加空监听器"
    assert "offBuy" in code, "应当用退订函数登记唯一的待补发购买意图"


_NODE_RACE_SCENARIO = r"""
const tick = () => new Promise((r) => setTimeout(r, 30));
async function loginAs(name) {
  // auth.js 是懒取 auth-user/auth-pass 的（只在 open() 与提交时取），
  // 真实浏览器里这些元素一直在 base.html 中；harness 里先摸一下让它建出来。
  document.getElementById("auth-user").value = name;
  document.getElementById("auth-pass").value = "pw123456";
  await document.getElementById("auth-form").onsubmit({ preventDefault() {} });
  await tick();
}
(async () => {
  const at = [];
  const snap = () => at.push(orderCalls);

  parkNext = true;                       // 请求 A 被挂起，模拟慢网络
  const pA = els["btn-buy"].onclick();
  await tick();                                                    snap();  // ① 1
  await loginAs("alice");                                          snap();  // ② 1（用户从顶栏登录）
  await els["btn-buy"].onclick(); await tick();                    snap();  // ③ 2（请求 B 成功下单）
  parked.shift()();                        // 迟到的 A 现在才带着 401 回来
  await pA; await tick();                                          snap();  // ④ 2
  await els["btn-logout"].onclick(); await tick();
  await loginAs("bob");                                            snap();  // ⑤ 2（不许变）

  stop(); // release the live polling timer before terminating this Node scenario
  console.log(JSON.stringify(at));
})();
"""


@pytest.mark.skipif(shutil.which("node") is None, reason="未安装 node")
def test_a_stale_401_cannot_revive_a_purchase_intent():
    """**乱序场景**：迟到的 401 不许把补单意图重新挂回去。

    时序（每一步的累计下单调用数）：
      ① 未登录点购买，请求 A 被挂起        → 1
      ② 用户从顶栏登录（与 A 无关）        → 1
      ③ 再点一次购买，请求 B 成功下单      → 2
      ④ 旧请求 A 这时才迟到返回 401        → 2
      ⑤ 将来某次重新登录（**没点购买**）   → 2  ← 变 3 就是幽灵下单

    第 ⑤ 步是关键：A 是在「未登录」时发出的，它带回来的 401 早已过期 ——
    用户此刻不但登录了，还已经下过单。若照旧登记补单监听器，
    用户将来某次登录就会凭空多出一张 pending 订单。
    """
    at = _run_shop_frontend(_NODE_RACE_SCENARIO)
    assert at == [1, 1, 2, 2, 2], (
        f"乱序时序不符：{at}（预期 [1, 1, 2, 2, 2]）。"
        "第 5 个数变成 3 = 迟到的 401 复活了补单意图，未来登录会幽灵下单。"
    )


# =============================================== 已购用户与返回恢复（V-05，2026-09-24）
#
# 2026-09-23 复核的 N-05：单 SKU 商品没有"已购买"这一状态。
#   · 已付款用户回到落地页，主按钮还是「立即购买」，再点一次会**静默新建一张待支付单**；
#   · 收银台点「返回商城」（或浏览器后退）时 `/shop` 是全新加载（响应带 no-store，
#     bfcache 被拒），`currentNo` 为 null → 又落回落地页，看不到刚付的那一单。
#
# 用户 2026-09-24 确认：不设计复购 —— 已购用户的主按钮直接变成「已购买，去下载」。
# 这里仍然用 Node 真跑 `auth.js` + `shop-page.js`，覆盖五种页面加载形态。

_OWNED_EXECUTOR = r"""
const CASE = process.env.SHOP_CASE || "none";
// 收银台「返回商城」带的 ?order= —— 必须在页面脚本执行**之前**就在 location 上。
const SEARCH = { order_paid: "?order=CM7", order_refunded: "?order=CM8" }[CASE] || "";

const els = {};
const mkEl = () => ({
  value: "", textContent: "", innerHTML: "", hidden: false, width: 0, alt: "",
  appendChild() {}, click() {}, addEventListener() {}, focus() {},
  classList: { add() {}, remove() {} },
});
global.document = { getElementById: (id) => (els[id] ||= mkEl()), createElement: mkEl };
global.addEventListener = () => {};
global.window = global;                    // 浏览器里 window 就是全局对象本身
global.window.location = { href: "", search: SEARCH };
const store = { getItem(){}, setItem(){}, removeItem(){}, clear(){} };
global.localStorage = store; global.sessionStorage = store;

// 假时钟只替换 setInterval/clearInterval（与 test_shop_polling 同款）；
// setTimeout 保持真实 —— 场景里的 `await tick()` 用它让出事件循环。
let __timer = null;
global.setInterval = (fn, ms) => { __timer = { fn, ms }; return __timer; };
global.clearInterval = (id) => { if (id === __timer) __timer = null; };

// 页面初始形态：只有落地态可见，主按钮文案是服务端渲染的（脚本把它记下来当还原值）。
els["btn-buy"] = mkEl(); els["btn-buy"].textContent = "立即购买（¥199.00）";
els["buy-note"] = mkEl(); els["buy-note"].hidden = true;
els["st-landing"] = mkEl();
["st-pending", "st-paid", "st-refunded", "st-closed", "st-downloaded"].forEach((id) => {
  els[id] = mkEl(); els[id].hidden = true;
});

let loggedIn = true;
let postOrders = 0;
const fetched = [];
const LIST = {
  paid: [{ order_no: "CM2", status: "paid", refunded: false }],
  // 已购一张，但之后又开过一张没付款的单（旧代码点购买会在这上面再下一单）
  owned_later_pending: [{ order_no: "CM9", status: "pending", refunded: false },
                        { order_no: "CM2", status: "paid", refunded: false }],
  owned_refunded: [{ order_no: "CM3", status: "downloaded", refunded: true }],
  pending_live: [{ order_no: "CM4", status: "pending", refunded: false }],
  pending_expired: [{ order_no: "CM4", status: "pending", refunded: false }],
}[CASE] || [];
const SINGLE = {
  CM2: { order_no: "CM2", status: "paid", refunded: false },
  CM3: { order_no: "CM3", status: "downloaded", refunded: true },
  CM4: { order_no: "CM4", status: "pending", refunded: false, expired: CASE === "pending_expired",
         code_url: "weixin://wxpay/bizpayurl?x=1", qr_svg: "<svg></svg>", qr_image: null },
  CM7: { order_no: "CM7", status: "paid", refunded: false },
  CM8: { order_no: "CM8", status: "paid", refunded: true },
  CM9: { order_no: "CM9", status: "pending", refunded: false, expired: false,
         code_url: "weixin://wxpay/bizpayurl?x=1", qr_svg: "<svg></svg>", qr_image: null },
};

global.fetch = async (url, opts = {}) => {
  const method = opts.method || "GET";
  fetched.push(`${method} ${url}`);
  if (url === "/shop/orders" && method === "POST") {
    postOrders += 1;
    return { ok: true, status: 200, json: async () => SINGLE.CM4 };
  }
  if (url === "/shop/orders") {
    return loggedIn
      ? { ok: true, status: 200, json: async () => ({ orders: LIST, next_cursor: null }) }
      : { ok: false, status: 401, json: async () => ({}) };
  }
  if (url.startsWith("/shop/orders/")) {
    const o = SINGLE[url.slice("/shop/orders/".length)];
    return o ? { ok: true, status: 200, json: async () => o }
             : { ok: false, status: 404, json: async () => ({}) };
  }
  if (url === "/auth/me") return loggedIn
    ? { ok: true, status: 200, json: async () => ({ id: 1, username: "alice", nickname: "A" }) }
    : { ok: false, status: 401, json: async () => ({}) };
  return { ok: true, status: 200, json: async () => ({}) };
};

global.__state = () => {
  // 元素是惰性创建的（只有脚本真的取过的 id 才存在），所以这里也要走 getElementById。
  const text = (id) => document.getElementById(id).textContent;
  const visible = [["landing", "st-landing"], ["pending", "st-pending"], ["paid", "st-paid"],
                   ["refunded", "st-refunded"], ["closed", "st-closed"], ["downloaded", "st-downloaded"]]
    .filter(([, id]) => document.getElementById(id).hidden === false).map(([k]) => k);
  return {
    buyText: text("btn-buy"),
    noteHidden: document.getElementById("buy-note").hidden,
    noteText: text("buy-note"),
    paidNo: text("d-no"),
    pendingNo: text("p-no"),
    visible, postOrders, timerActive: __timer !== null,
    fetchGets: fetched.filter((f) => f.startsWith("GET ")),
  };
};
"""


def _run_owned_scenario(case: str, scenario: str) -> dict:
    """按浏览器真实顺序执行 auth.js + shop-page.js，返回场景末行 JSON。"""
    root = Path(__file__).resolve().parents[1]
    auth = (root / "app/frontend/auth.js").read_text(encoding="utf-8")
    shop_js = (root / "app/frontend/shop-page.js").read_text(encoding="utf-8")
    assert shop_js.strip(), "下单页脚本是空的 —— 很可能读错了文件"
    script = "\n;\n".join([auth, shop_js])

    harness = Path(tempfile.gettempdir()) / f"_shop_owned_harness_{os.getpid()}.js"
    harness.write_text(_OWNED_EXECUTOR + "\n;\n" + script + "\n" + scenario, encoding="utf-8")
    proc = subprocess.run(
        ["node", str(harness)], capture_output=True, text=True, timeout=60,
        env={**os.environ, "SHOP_CASE": case},
    )
    assert proc.returncode == 0, f"前端脚本执行失败：\n{proc.stdout}\n{proc.stderr}"
    return json.loads(proc.stdout.strip().splitlines()[-1])


@pytest.mark.skipif(shutil.which("node") is None, reason="未安装 node")
def test_an_owned_latest_order_opens_its_status_instead_of_repurchase():
    """**V-05 主断言之一**：最新一张就是已购单时，打开 /shop 直接显示那一单。

    这是「返回商城恢复订单状态」的自动恢复路径（收银台不带 `?order=` 时也成立）：
    用户看到的是「✅ 支付成功」和取链接按钮，而不是一个能再下一单的「立即购买」。
    """
    out = _run_owned_scenario(
        "paid",
        """
(async () => {
  const tick = () => new Promise((r) => setTimeout(r, 30));
  await tick();                                  // 等首次身份解析 + 自动恢复
  console.log(JSON.stringify(__state()));
})();
        """,
    )
    assert out["visible"] == ["paid"], f"应直接显示该单状态，实际 {out['visible']}"
    assert out["paidNo"] == "CM2", f"显示的应当是已购的那一单，实际 {out['paidNo']!r}"
    assert out["postOrders"] == 0, "恢复状态不该下单"


@pytest.mark.skipif(shutil.which("node") is None, reason="未安装 node")
def test_paid_user_sees_download_button_and_cannot_repurchase():
    """**V-05 主断言之二**：有已购权益、最新一张却是未付款单时，只给「已购买，去下载」。

    这是旧代码最危险的一条路径：已付过款的用户看到「立即购买」，点下去会**静默新建一张
    待支付单**。修好之后应当是：落地页 + 已购说明；点按钮回到已购那一单，且订单数不变。
    """
    out = _run_owned_scenario(
        "owned_later_pending",
        """
(async () => {
  const tick = () => new Promise((r) => setTimeout(r, 30));
  await tick();
  const before = __state();
  await els["btn-buy"].onclick();                 // 点主按钮
  await tick();
  console.log(JSON.stringify({ before, after: __state() }));
})();
        """,
    )
    assert out["before"]["buyText"] == "已购买，去下载", (
        f"已购用户的主按钮仍是 {out['before']['buyText']!r} —— 再点一次会静默新建待支付单。"
    )
    assert out["before"]["noteHidden"] is False, "切换到去下载后应说明已购原因"
    assert "CM2" in out["before"]["noteText"], "已购说明里应当有订单号"
    assert out["before"]["visible"] == ["landing"], (
        f"已购优先：不该把更晚的未付款单（CM9）恢复成待支付视图，实际 {out['before']['visible']}"
    )
    assert out["after"]["postOrders"] == 0, "点「已购买，去下载」不该再下单"
    assert out["after"]["visible"] == ["paid"], f"应切到支付成功视图，实际 {out['after']['visible']}"
    assert out["after"]["paidNo"] == "CM2", "去下载的应当是已购的那一单"


@pytest.mark.skipif(shutil.which("node") is None, reason="未安装 node")
def test_a_fully_refunded_order_does_not_block_buying_again():
    """已全额退款的订单不算已购权益：按钮必须还原成「立即购买」。

    否则用户退完款就再也买不了了 —— 「不设计复购」不等于「退款后不能重买」。
    """
    out = _run_owned_scenario(
        "owned_refunded",
        """
(async () => {
  const tick = () => new Promise((r) => setTimeout(r, 30));
  await tick();
  console.log(JSON.stringify(__state()));
})();
        """,
    )
    assert out["buyText"] == "立即购买（¥199.00）", "退款后的订单不该把主按钮切成去下载"
    assert out["noteHidden"] is True, "没有已购权益时不该显示已购说明"
    assert out["visible"] == ["landing"]


@pytest.mark.skipif(shutil.which("node") is None, reason="未安装 node")
def test_returning_with_order_number_restores_that_order():
    """收银台「返回商城」带的 `?order=` 必须直接把那一单的状态显示出来。

    这条替代的是原来的「靠 bfcache 恢复」——`/shop` 响应带 no-store，Chromium 实测
    拒绝入 bfcache，全新加载时 `currentNo` 是 null，只会落回落地页（复核 6.3）。
    """
    out = _run_owned_scenario(
        "order_paid",
        """
(async () => {
  const tick = () => new Promise((r) => setTimeout(r, 30));
  await tick();
  console.log(JSON.stringify(__state()));
})();
        """,
    )
    assert out["visible"] == ["paid"], f"应直接显示该单的支付成功视图，实际 {out['visible']}"
    assert out["paidNo"] == "CM7"
    assert out["postOrders"] == 0, "恢复订单状态绝不能顺手下单"
    assert "GET /shop/orders/CM7" in out["fetchGets"], f"应当直接查这一单：{out['fetchGets']}"


@pytest.mark.skipif(shutil.which("node") is None, reason="未安装 node")
def test_returning_with_order_number_shows_refund_state():
    """退款单也要按 URL 里的单号恢复：显示"已全额退款"而不是落地页。"""
    out = _run_owned_scenario(
        "order_refunded",
        """
(async () => {
  const tick = () => new Promise((r) => setTimeout(r, 30));
  await tick();
  console.log(JSON.stringify(__state()));
})();
        """,
    )
    assert out["visible"] == ["refunded"], f"实际 {out['visible']}"
    assert out["postOrders"] == 0


@pytest.mark.skipif(shutil.which("node") is None, reason="未安装 node")
def test_a_live_pending_order_is_restored_with_polling():
    """未过期的待支付单要接着展示并重新起轮询 —— 用户从收银台回来才看得到状态变化。"""
    out = _run_owned_scenario(
        "pending_live",
        """
(async () => {
  const tick = () => new Promise((r) => setTimeout(r, 30));
  await tick();
  console.log(JSON.stringify(__state()));
})();
        """,
    )
    assert out["visible"] == ["pending"], f"实际 {out['visible']}"
    assert out["pendingNo"] == "CM4"
    assert out["timerActive"] is True, "恢复的待支付单必须继续轮询，否则付款后页面不会更新"
    assert out["postOrders"] == 0, "恢复状态不该下单"


@pytest.mark.skipif(shutil.which("node") is None, reason="未安装 node")
def test_an_expired_pending_order_is_not_restored():
    """对照：已过期的待支付单不能恢复成\"可以扫码\"的样子（二维码早就失效）。"""
    out = _run_owned_scenario(
        "pending_expired",
        """
(async () => {
  const tick = () => new Promise((r) => setTimeout(r, 30));
  await tick();
  console.log(JSON.stringify(__state()));
})();
        """,
    )
    assert out["visible"] == ["landing"], f"实际 {out['visible']}"
    assert out["timerActive"] is False, "过期的单不该起轮询"
    assert out["buyText"] == "立即购买（¥199.00）", "没有已购权益，按钮保持原样"
