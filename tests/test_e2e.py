"""S5-01 全链路测试：把前面各阶段的单元测试**串成完整业务旅程**。

和单元测试的分工：
- 单元测试（test_wechat_pay / test_download / test_oauth …）验证**单个环节**对不对；
- 本文件验证**整条链**接得起来 —— 注册→登录→下单→支付→下载，以及跨平台 SSO 全流程。
  环节各自正确但串起来断掉的情况（状态没传下去、事务边界不对、依赖注入漏了）
  只有这种测试能发现。

另外补三类**单元测试结构上测不到**的东西：
1. 真并发下的竞态（同一单重复支付、同一单并发抢下载）；
2. 超时未支付（S5-01-1 明确列了，但 `closed` 状态此前根本没实现，见 TD-100）；
3. 商品是多文件压缩包时的完整性。
"""
import asyncio
import io
import zipfile
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlsplit

import pytest
from sqlalchemy import select, update

import app.routers.shop as shop
from app.config import settings
from app.models import OAuthClient, Order, User
from app.order_state import CLOSED, DOWNLOADED, PAID, PENDING
from app.storage import LocalStorage
from tests.conftest import TestSession, seed_clients, sso_authorize

PRODUCT_KEY = "product/codemax_package.zip"


# ---------------------------------------------------------------- 公共工具


@pytest.fixture
def mock_mode(monkeypatch):
    """走模拟收银台：与真实回调共用同一套状态机与幂等逻辑，跑的是同一条路径。"""
    monkeypatch.setattr(settings, "SHOP_PAY_MODE", "mock")

    async def no_wechat(cfg, **kw):
        raise AssertionError("模拟模式不该调用微信支付")

    monkeypatch.setattr(shop, "native_prepay", no_wechat)


@pytest.fixture
def product_zip(tmp_path, monkeypatch):
    """造一个**真的含多个文件**的商品压缩包。

    之前的测试用的是 `b"PK\\x03\\x04 " + 正文` 这种假 zip 头，只能验证字节透传，
    验证不了「多文件下载」这件事本身。这里用真 zip，下载完能解开并数出文件数。
    """
    monkeypatch.setattr(settings, "STORAGE_BACKEND", "local")
    monkeypatch.setattr(settings, "STORAGE_LOCAL_ROOT", str(tmp_path))
    monkeypatch.setattr(settings, "STORAGE_PRODUCT_KEY", PRODUCT_KEY)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("README.md", "# 毕设交付说明")
        z.writestr("src/main.py", "print('hello')")
        z.writestr("docs/设计文档.docx", "x" * 32)
    LocalStorage(str(tmp_path), "http://test", settings.SECRET_KEY).put(PRODUCT_KEY, buf.getvalue())
    return tmp_path


async def signup(client, username="buyer", password="secret123") -> dict:
    await client.post("/auth/register", json={"username": username, "password": password})
    r = await client.post("/auth/login", data={"username": username, "password": password})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


async def place_order(client, headers) -> str:
    r = await client.post("/shop/orders", headers=headers)
    assert r.status_code == 200, r.text
    return r.json()["order_no"]


async def pay_via_mock(client, headers, order_no: str):
    return await client.post("/shop/mock-pay/confirm", headers=headers, json={"order_no": order_no})


async def order_row(order_no: str) -> Order:
    async with TestSession() as s:
        return (await s.execute(select(Order).where(Order.order_no == order_no))).scalar_one()


async def backdate(order_no: str, minutes: int):
    """把下单时间往前挪，用来模拟「超时未支付」。"""
    async with TestSession() as s:
        await s.execute(
            update(Order)
            .where(Order.order_no == order_no)
            .values(create_time=datetime.now(timezone.utc) - timedelta(minutes=minutes))
        )
        await s.commit()


# ================================================================ S5-01-1 支付全链路


@pytest.mark.asyncio
async def test_full_chain_register_to_single_download(client, mock_mode, product_zip):
    """正常支付完整链路：注册 → 登录 → 下单 → 支付 → 领链接 → 下到真文件 → 再领被拒。"""
    h = await signup(client)

    order_no = await place_order(client, h)
    assert (await order_row(order_no)).status == PENDING

    r = await pay_via_mock(client, h, order_no)
    assert r.status_code == 200
    assert (await order_row(order_no)).status == PAID

    r = await client.post(f"/shop/download/{order_no}", headers=h)
    assert r.status_code == 200
    url = r.json()["download_url"]
    assert (await order_row(order_no)).status == DOWNLOADED

    got = await client.get(url)
    assert got.status_code == 200
    with zipfile.ZipFile(io.BytesIO(got.content)) as z:  # 链尾真的拿到了可用文件
        assert len(z.namelist()) == 3

    # 一次性：同一单第二次领链接必须被拒
    r2 = await client.post(f"/shop/download/{order_no}", headers=h)
    assert r2.status_code == 403


@pytest.mark.asyncio
async def test_expired_pending_order_is_closed_and_replaced(client, mock_mode, monkeypatch):
    """超时未支付：旧单关掉，重新下单能拿到**新**单号（TD-109 的出路）。"""
    monkeypatch.setattr(settings, "ORDER_EXPIRE_MINUTES", 30)
    h = await signup(client)

    stale = await place_order(client, h)
    await backdate(stale, minutes=31)  # 超过 30 分钟

    fresh_no = await place_order(client, h)
    assert fresh_no != stale, "过期单必须被换掉，不能再复用"
    assert (await order_row(stale)).status == CLOSED
    assert (await order_row(fresh_no)).status == PENDING


@pytest.mark.asyncio
async def test_unexpired_pending_order_is_still_reused(client, mock_mode):
    """没超时就仍然复用同一单 —— 别让关单逻辑把「防连点刷单」给弄没了。"""
    h = await signup(client)
    first = await place_order(client, h)
    second = await place_order(client, h)
    assert first == second
    assert (await order_row(first)).status == PENDING


@pytest.mark.asyncio
async def test_late_payment_on_closed_order_still_delivers(client, mock_mode, product_zip):
    """关单之后用户才付钱：**必须照样发货**。

    这是 `CLOSED → PAID` 这条边存在的唯一理由。少了它，用户扫旧二维码付了钱
    却拿不到货 —— 收钱不发货比多发货严重得多（TD-156）。
    """
    h = await signup(client)
    order_no = await place_order(client, h)
    await backdate(order_no, minutes=999)
    await place_order(client, h)  # 触发关单
    assert (await order_row(order_no)).status == CLOSED

    r = await pay_via_mock(client, h, order_no)
    assert r.status_code == 200, "已关单的订单收到真实支付，不能拒"
    assert (await order_row(order_no)).status == PAID

    r = await client.post(f"/shop/download/{order_no}", headers=h)
    assert r.status_code == 200, "付了钱就得能下载"


@pytest.mark.asyncio
async def test_expired_order_can_never_be_downloaded(client, mock_mode, product_zip):
    """关单本身不等于发货：没付过钱的过期单，下载一律拒。"""
    h = await signup(client)
    order_no = await place_order(client, h)
    await backdate(order_no, minutes=999)
    await place_order(client, h)
    assert (await order_row(order_no)).status == CLOSED

    r = await client.post(f"/shop/download/{order_no}", headers=h)
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_concurrent_duplicate_payment_marks_paid_exactly_once(client, mock_mode):
    """重复支付模拟：同一个单并发确认 5 次，状态只能迁移一次，且**每次都返回成功**。

    返回成功是幂等契约的一部分 —— 微信会重发通知，回调若报错它会一直重试。
    """
    h = await signup(client)
    order_no = await place_order(client, h)

    results = await asyncio.gather(*[pay_via_mock(client, h, order_no) for _ in range(5)])
    assert [r.status_code for r in results] == [200] * 5, "重复通知必须都算成功"

    async with TestSession() as s:
        rows = (
            await s.execute(select(Order).where(Order.order_no == order_no))
        ).scalars().all()
    assert len(rows) == 1
    assert rows[0].status == PAID


@pytest.mark.asyncio
async def test_payment_then_late_duplicate_keeps_downloaded_state(client, mock_mode, product_zip):
    """下载完成后又来一次支付通知：不能把 downloaded 打回 paid（会白送一次下载）。"""
    h = await signup(client)
    order_no = await place_order(client, h)
    await pay_via_mock(client, h, order_no)
    assert (await client.post(f"/shop/download/{order_no}", headers=h)).status_code == 200

    r = await pay_via_mock(client, h, order_no)
    assert r.status_code == 200
    assert (await order_row(order_no)).status == DOWNLOADED


# ================================================================ S5-01-2 下载与防盗链


@pytest.mark.asyncio
async def test_concurrent_download_only_one_wins(client, mock_mode, product_zip):
    """并发抢下载：同一单同时来 4 个请求，**恰好一个**拿到链接。

    这是「一次性下载」在真并发下的有效性 —— 串行测过不代表并发也守得住。
    """
    h = await signup(client)
    order_no = await place_order(client, h)
    await pay_via_mock(client, h, order_no)

    results = await asyncio.gather(
        *[client.post(f"/shop/download/{order_no}", headers=h) for _ in range(4)]
    )
    codes = sorted(r.status_code for r in results)
    assert codes.count(200) == 1, f"必须只有一个成功，实际 {codes}"
    assert set(codes) - {200} == {403}


@pytest.mark.asyncio
async def test_downloaded_archive_really_contains_multiple_files(client, mock_mode, product_zip):
    """多文件下载有效性：下到的确实是能解开、含多个条目的压缩包，且内容逐字节一致。"""
    h = await signup(client)
    order_no = await place_order(client, h)
    await pay_via_mock(client, h, order_no)
    url = (await client.post(f"/shop/download/{order_no}", headers=h)).json()["download_url"]

    data = (await client.get(url)).content
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        names = sorted(z.namelist())
        assert names == ["README.md", "docs/设计文档.docx", "src/main.py"]
        assert z.read("src/main.py") == b"print('hello')"
        assert z.testzip() is None, "压缩包损坏"


@pytest.mark.asyncio
async def test_presigned_url_cannot_be_reused_after_order_consumed(client, mock_mode, product_zip):
    """防盗链有效性：链接被转发出去后，在过期时间内仍然能下 —— 这是**已知取舍**，
    所以第一重（数据库一次性）才是主力。这里钉住的是：转发者拿不到**第二个**链接。
    """
    h = await signup(client)
    order_no = await place_order(client, h)
    await pay_via_mock(client, h, order_no)
    url = (await client.post(f"/shop/download/{order_no}", headers=h)).json()["download_url"]

    other = await signup(client, username="pirate", password="secret123")
    # 拿到链接的人可以下（链接本身合法），但他无法为这个单再领新链接
    r = await client.post(f"/shop/download/{order_no}", headers=other)
    assert r.status_code == 404, "别人的单一律 404，不暴露是否存在"
    assert (await client.get(url)).status_code == 200


@pytest.mark.asyncio
async def test_tampered_and_expired_links_rejected_end_to_end(client, mock_mode, product_zip):
    """防盗链第二重：改签名、改过期时间都下不到。"""
    h = await signup(client)
    order_no = await place_order(client, h)
    await pay_via_mock(client, h, order_no)
    url = (await client.post(f"/shop/download/{order_no}", headers=h)).json()["download_url"]

    parts = urlsplit(url)
    qs = parse_qs(parts.query)
    bad_sig = url.split("signature=")[0] + "signature=" + "0" * len(qs["signature"][0])
    assert (await client.get(bad_sig)).status_code == 403

    expired = url.replace(f"expires={qs['expires'][0]}", "expires=1000000000")
    assert (await client.get(expired)).status_code == 403


# ================================================================ S5-01-3 SSO 跨域


def _cross_origin_headers(origin: str) -> dict:
    """模拟从另一个域名的页面发起请求时浏览器会带的头。"""
    return {"Origin": origin, "Referer": origin + "/"}


@pytest.mark.asyncio
async def test_full_sso_journey_across_two_platforms(client):
    """跨平台 SSO 完整链路：主站登录 → 授权工具平台 → 回调拿 code → 换 token → 访问受保护接口。

    两个 client 是 conftest 里种下的（tools / shop），redirect_uri 分属不同域名，
    所以这条链天然就是跨域的。
    """
    h = await signup(client, username="sso_user")

    r = await sso_authorize(client, h, state="xyz")
    assert r.status_code in (302, 307)
    loc = r.headers["location"]
    assert loc.startswith("https://tools.codemax.top/callback")
    code = parse_qs(urlsplit(loc).query)["code"][0]
    assert parse_qs(urlsplit(loc).query)["state"][0] == "xyz", "state 必须原样回传（防 CSRF）"

    # 换 token 是**服务端到服务端**：client_secret 走表单字段，绝不进浏览器
    r = await client.post(
        "/oauth/token",
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": "https://tools.codemax.top/callback",
            "client_id": "tools",
            "client_secret": "codemax-tools-secret",
        },
    )
    assert r.status_code == 200, r.text
    assert r.json()["access_token"]

    # 用换来的 token 访问工具平台受保护端点
    r = await client.get(
        "/tools/ping", headers={"Authorization": f"Bearer {r.json()['access_token']}"}
    )
    assert r.status_code == 200
    assert r.json()["platform"] == "tools"


@pytest.mark.asyncio
async def test_sso_flow_survives_cross_origin_headers(client):
    """带跨域 Origin/Referer 头也能走完整条链。

    这里顺便说明为什么**没有配 CORS**（TD-157）：授权码流程靠 302 跳转，
    换 token 靠服务端到服务端调用（client_secret 绝不能进浏览器），
    全程没有浏览器跨域 XHR，所以不需要 CORS。配了反而是白开一个攻击面。
    """
    h = await signup(client, username="cors_user")
    h.update(_cross_origin_headers("https://tools.codemax.top"))

    r = await sso_authorize(client, h, state="s1")
    assert r.status_code in (302, 307)
    assert parse_qs(urlsplit(r.headers["location"]).query)["code"]


@pytest.mark.asyncio
async def test_redirect_uri_must_match_exactly_across_domains(client):
    """跨域安全底线：redirect_uri 换个域名就拒，否则授权码能被劫持到攻击者站点。"""
    h = await signup(client, username="evil_target")
    r = await client.get(
        "/oauth/authorize",
        params={
            "client_id": "tools",
            "redirect_uri": "https://evil.example.com/callback",
            "response_type": "code",
            "state": "s",
        },
        headers=h,
        follow_redirects=False,
    )
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_code_from_one_client_cannot_be_used_by_another(client):
    """tools 平台拿到的授权码，不能被 shop 平台拿去换 token。"""
    h = await signup(client, username="cross_client")
    r = await sso_authorize(client, h, state="s")
    code = parse_qs(urlsplit(r.headers["location"]).query)["code"][0]

    r = await client.post(
        "/oauth/token",
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": "https://shop.codemax.top/callback",
            "client_id": "shop",
            "client_secret": "codemax-shop-secret",
        },
    )
    assert r.status_code == 400
    assert r.json()["detail"]["error"] == "invalid_grant"


@pytest.mark.asyncio
async def test_seeded_clients_are_on_different_domains(client):
    """上面几条跨域测试的前提：两个接入平台的 redirect_uri 确实分属不同域名。"""
    async with TestSession() as s:
        rows = (await s.execute(select(OAuthClient))).scalars().all()
    hosts = {urlsplit(c.redirect_uri).netloc for c in rows}
    assert hosts == {"tools.codemax.top", "shop.codemax.top"}
    assert len(seed_clients()) == 2


@pytest.mark.asyncio
async def test_sso_token_does_not_leak_into_authorize_redirect(client):
    """授权跳转的 URL 里只能有 code，不能直接带 access_token（隐式流的老毛病）。"""
    h = await signup(client, username="no_token_in_url")
    r = await sso_authorize(client, h, state="s")
    loc = r.headers["location"]
    assert "access_token" not in loc
    assert "code=" in loc


@pytest.mark.asyncio
async def test_order_belongs_to_exactly_one_user_after_full_chain(client, mock_mode, product_zip):
    """收尾一致性检查：跑完整条链后，订单归属、金额、状态三项都还对得上。"""
    h = await signup(client, username="owner")
    order_no = await place_order(client, h)
    await pay_via_mock(client, h, order_no)
    await client.post(f"/shop/download/{order_no}", headers=h)

    async with TestSession() as s:
        o = (await s.execute(select(Order).where(Order.order_no == order_no))).scalar_one()
        u = (await s.execute(select(User).where(User.username == "owner"))).scalar_one()
    assert o.user_id == u.id
    assert o.amount == settings.SHOP_PRODUCT_AMOUNT
    assert o.status == DOWNLOADED
    assert o.paid_at is not None, "付过钱的单必须留下支付时间"
