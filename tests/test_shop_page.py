# tests/test_shop_page.py
#
# S2-02-2「免费引流 → 商业变现」的转化路径。
#
# 这组测试的重点不是"页面能打开"，而是钉住三条容易在后续改动中被破坏的不变式：
#
# 1. **轮询订单状态绝不能烧掉一次性下载。** `POST /shop/download/{order_no}` 成功后
#    订单永久变 `downloaded`，同一单不能再下载（后端 CAS，见 shop.py 的 `won`）。
#    下单页每 3 秒轮询一次 `GET /shop/orders/{no}`，谁要是图省事拿 download 当状态查询，
#    用户的货就没了 —— 而且现象是"付了钱下载不了"，极难排查。
# 2. **同意页零脚本。** base.html 现在带了全站登录模块，但 OAuth 同意页必须一个脚本都没有
#    （`auth_ui=False`）。这条在 test_oauth_consent.py 里，这里只补它的对偶：
#    其它页面确实拿到了登录入口。
# 3. **二维码是内联 SVG，不引入任何外部请求。** CSP 的 `connect-src 'self'` 会拦掉
#    第三方请求，所以二维码不能靠 CDN 画。
"""S2-02-2：商城落地页、订单状态轮询与二维码。"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.config import settings
from app.models import Order, User
from app.routers import shop
from tests.conftest import TestSession, iter_app_routes

# product fixture 在 conftest 里（pytest 自动发现），这里只需要这三个 helper
from tests.test_download import auth_headers, make_order, status_of

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


async def test_shop_page_warns_about_one_time_download(client):
    """一次性下载是会让用户丢货的约束，必须在页面上说清楚。"""
    assert "一次性有效" in (await client.get("/shop")).text


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


async def test_status_polling_does_not_burn_the_one_time_download(client, product, mock_mode):
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
        "轮询之后下载失败了 —— 说明轮询路径把一次性下载额度烧掉了。"
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
    # （`WX_APPID` 等六项没填）。这里把配置桩成"已配置"，才能真正走到 native_prepay。
    monkeypatch.setattr(shop, "pay_config", lambda: SimpleNamespace(configured=True))

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

  console.log(JSON.stringify(at));
})();
"""


def _run_shop_frontend(scenario: str = _NODE_SCENARIO) -> list[int]:
    """按浏览器真实的文档顺序执行 `auth.js` + `shop.html` 内联脚本，返回各步的下单调用数。"""
    root = Path(__file__).resolve().parents[1]
    auth = (root / "app/frontend/auth.js").read_text(encoding="utf-8")
    shop_html = (root / "app/templates/shop.html").read_text(encoding="utf-8")
    inline = re.findall(r"<script>(.*?)</script>", shop_html, re.S)
    assert inline, "shop.html 应该有自己的内联脚本"
    script = "\n;\n".join([auth, *inline])

    harness = Path("/tmp/_shop_frontend_harness.js")
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
    html = (Path(__file__).resolve().parents[1] / "app/templates/shop.html").read_text(encoding="utf-8")
    code = "\n".join(re.sub(r"//.*$", "", line) for line in html.splitlines())
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
