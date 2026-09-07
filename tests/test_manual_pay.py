"""人工确认收款模式（S5-04 / TD-205）的测试。

这个模式存在的原因是：**没有商户号就拿不到支付回调**（TD-113）。既然机器不知道
钱到没到，就只能由人来告诉系统。所以这里的重点不是「能改状态」，而是三件事：

1. **只有管理员能确认**。mock 模式是登录用户自己点，那是免费发货按钮；
   manual 若也这样，就只是把后门换了个名字。
2. **关掉的时候端点必须彻底不存在**（404），和 mock 端点同一个纪律。
3. **复用真实回调那条状态机**（`mark_paid`）：幂等、且 CLOSED 也能收 ——
   用户扫了旧码照样可能付钱，钱到账就必须发货（TD-156）。

另外这个文件守着 full_init.sql 里那个真实存在过的坑：种子账号漏写 `role` 列，
导致「昵称叫管理员的账号进不了任何管理端点」。
"""
import re

import pytest
from sqlalchemy import select

import app.routers.shop as shop
from app.config import settings
from app.models import Order, User
from tests.conftest import TestSession

FULL_INIT_SQL = "database init/full_init.sql"


@pytest.fixture
def manual_mode(monkeypatch):
    monkeypatch.setattr(settings, "SHOP_PAY_MODE", "manual")

    async def no_wechat(cfg, **kw):
        raise AssertionError("manual 模式不该调用微信支付")

    monkeypatch.setattr(shop, "native_prepay", no_wechat)


async def _login(client, username="buyer", password="secret123") -> dict:
    await client.post("/auth/register", json={"username": username, "password": password})
    r = await client.post("/auth/login", data={"username": username, "password": password})
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


async def _promote(username: str) -> None:
    """直接改库提权 —— 生产上也只能这么提权（迁移脚本刻意不做，见 migrate_0005）。"""
    async with TestSession() as s:
        u = await s.scalar(select(User).where(User.username == username))
        u.role = 1
        await s.commit()


async def _status(order_no: str) -> str:
    async with TestSession() as s:
        row = await s.scalar(select(Order).where(Order.order_no == order_no))
        return row.status


# ------------------------------------------------------------------ 下单


async def test_manual_order_returns_static_qr_and_no_code_url(client, manual_mode):
    """manual 模式没有 code_url —— 收款码是全站共用的一张静态图，与订单无关。"""
    h = await _login(client)
    r = await client.post("/shop/orders", headers=h)
    assert r.status_code == 200
    body = r.json()
    assert body["pay_mode"] == "manual"
    assert body["qr_image"] == settings.SHOP_MANUAL_QR
    assert not body["code_url"], "不该给一个图片路径假装成支付串"
    assert body["qr_svg"] is None, "不该拿路径字符串去画二维码"


async def test_wechat_and_mock_payloads_carry_no_qr_image(client, manual_mode, monkeypatch):
    """qr_image 只在 manual 下非空，否则前端无法二选一渲染。"""
    monkeypatch.setattr(settings, "SHOP_PAY_MODE", "mock")
    h = await _login(client, "buyer2")
    body = (await client.post("/shop/orders", headers=h)).json()
    assert body["qr_image"] is None
    assert body["code_url"].startswith("http")


async def test_repeated_manual_order_is_reused_not_duplicated(client, manual_mode):
    """回归：manual 单没有 code_url，若复用判据只看 code_url，每次下单都会新建一张。"""
    h = await _login(client)
    first = (await client.post("/shop/orders", headers=h)).json()
    second = (await client.post("/shop/orders", headers=h)).json()
    assert first["order_no"] == second["order_no"]
    assert second["reused"] is True

    async with TestSession() as s:
        rows = (await s.execute(select(Order))).scalars().all()
    assert len(rows) == 1, f"库里堆出了 {len(rows)} 张单"


# ------------------------------------------------------- 端点的存在性纪律


async def test_confirm_is_unavailable_outside_manual_mode(client, monkeypatch):
    """关掉时谁都不能确认，且绝不能顺手改状态。

    注意两种身份拿到的状态码**不一样**，而且这是有意的：
      非管理员 → 403（`require_admin` 是依赖，比函数体里的模式判断先跑）
      管理员   → 404（模式判断才轮到）
    `require_admin` 的文档写明「端点存在与否不是本站的秘密」，所以不向非管理员
    泄露存在性、只对管理员说「没开」，与本仓库既有纪律一致。

    用 mock 模式建单（默认 wechat 没配商户号会 503，建不出单）。
    """
    monkeypatch.setattr(settings, "SHOP_PAY_MODE", "mock")
    h = await _login(client)
    no = (await client.post("/shop/orders", headers=h)).json()["order_no"]

    r = await client.post(f"/shop/orders/{no}/confirm", headers=h)
    assert r.status_code == 403, "非管理员先撞权限门"
    assert await _status(no) == "pending"

    admin = await _login(client, "boss9")
    await _promote("boss9")
    r2 = await client.post(f"/shop/orders/{no}/confirm", headers=admin)
    assert r2.status_code == 404, "管理员才会看到「模式没开」"
    assert "manual" in r2.json()["detail"]
    assert await _status(no) == "pending", "两次尝试都不该改状态"


async def test_confirm_requires_login(client, manual_mode):
    """未登录必须 401。注意：**不能**用 _login 拿到的同一个 client 测这条 ——
    登录会把 cookie 留在 client 的 cookie jar 里，之后不带 Authorization 也照样通过。
    所以这里直接造单，全程不碰 client 的登录态。"""
    async with TestSession() as s:
        u = User(username="ghost", password="x", nickname="g")
        s.add(u)
        await s.commit()
        s.add(Order(order_no="CM-GHOST", user_id=u.id, product_name="p", amount=1, status="pending"))
        await s.commit()
    r = await client.post("/shop/orders/CM-GHOST/confirm")
    assert r.status_code == 401
    assert await _status("CM-GHOST") == "pending"


async def test_confirm_rejects_non_admin(client, manual_mode):
    """普通用户 403 而不是 404：端点存在与否不是秘密，真正的防线是权限本身。"""
    h = await _login(client)
    no = (await client.post("/shop/orders", headers=h)).json()["order_no"]
    r = await client.post(f"/shop/orders/{no}/confirm", headers=h)
    assert r.status_code == 403
    assert "管理员" in r.json()["detail"]
    assert await _status(no) == "pending"


# ------------------------------------------------------------- 确认收款


async def test_admin_confirm_marks_paid(client, manual_mode):
    buyer = await _login(client)
    no = (await client.post("/shop/orders", headers=buyer)).json()["order_no"]

    admin = await _login(client, "boss")
    await _promote("boss")
    r = await client.post(f"/shop/orders/{no}/confirm", headers=admin)
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "paid"
    assert body["pay_mode"] == "manual"
    assert body["transaction_id"] == f"MANUAL-{no}"
    assert body["confirmed_by"] == "boss", "要留下审计线索：谁确认的这笔款"
    assert await _status(no) == "paid"


async def test_admin_confirm_is_idempotent(client, manual_mode):
    """重复确认不能报错，也不能把状态搞乱（微信回调本身就可能重发）。"""
    buyer = await _login(client)
    no = (await client.post("/shop/orders", headers=buyer)).json()["order_no"]
    admin = await _login(client, "boss2")
    await _promote("boss2")

    first = await client.post(f"/shop/orders/{no}/confirm", headers=admin)
    second = await client.post(f"/shop/orders/{no}/confirm", headers=admin)
    assert first.status_code == second.status_code == 200
    assert second.json()["status"] == "paid"


async def test_admin_confirm_delivers_on_closed_order(client, manual_mode):
    """迟到支付：单子被超时关掉了，但用户扫旧码照样付了钱 —— 钱到账就必须发货（TD-156）。"""
    buyer = await _login(client)
    no = (await client.post("/shop/orders", headers=buyer)).json()["order_no"]
    async with TestSession() as s:
        row = await s.scalar(select(Order).where(Order.order_no == no))
        row.status = "closed"
        await s.commit()

    admin = await _login(client, "boss3")
    await _promote("boss3")
    r = await client.post(f"/shop/orders/{no}/confirm", headers=admin)
    assert r.status_code == 200 and r.json()["status"] == "paid"


async def test_confirm_unknown_order_is_404(client, manual_mode):
    """对管理员**可以**明确说「不存在」：这是他需要的运维信息，不是要保密的东西。"""
    admin = await _login(client, "boss4")
    await _promote("boss4")
    r = await client.post("/shop/orders/CM-NOT-EXIST/confirm", headers=admin)
    assert r.status_code == 404


# ---------------------------------------------------------------- 闭环


async def test_download_works_after_manual_confirm(client, product, manual_mode):
    """整条链路走通：下单 → 管理员确认 → 用户下载。这是这个模式唯一的目的。"""
    buyer = await _login(client)
    no = (await client.post("/shop/orders", headers=buyer)).json()["order_no"]

    admin = await _login(client, "boss5")
    await _promote("boss5")
    assert (await client.post(f"/shop/orders/{no}/confirm", headers=admin)).status_code == 200

    dl = await client.post(f"/shop/download/{no}", headers=buyer)
    assert dl.status_code == 200, f"确认后下载失败：{dl.text}"
    assert "download_url" in dl.json()


async def test_shop_page_renders_the_manual_qr_branch(client, manual_mode):
    """下单页得真有渲染 qr_image 的那条分支，否则后端返回了也没人用。

    C3 之后那段分支在外部脚本 `/static/js/shop-page.js` 里（原先是 shop.html 的内联块），
    所以要去脚本里核对。⚠️ 这里刻意查**浏览器真正加载的产物**而不是源码：
    这条断言守的是「后端返回的字段前端有没有人接」，只有产物才代表线上真会跑的那份代码。
    顺带也钉住了模板确实加载了该脚本 —— 否则产物再对也不会被执行。
    """
    html = (await client.get("/shop")).text
    assert "/static/js/shop-page.js" in html, "下单页没加载自己的交互脚本"
    js = (await client.get("/static/js/shop-page.js")).text
    # ⚠️ 别写成 `"qr_image" in js`：那个标识符在注释与别处也出现，**改坏分支它照样绿**
    # （实测过：把 `else if (o.qr_image)` 换成永假条件，弱断言仍然通过）。
    # 要断言的是**结构**：收款码必须走 `<img src=...>`，不是塞进 innerHTML ——
    # 后者会给「运营可配的收款码路径」留下 XSS 面。压缩后属性名保留，故可查。
    assert re.search(r"\.src\s*=\s*\w+\.qr_image", js), (
        "manual 收款码没走 <img src>（应形如 img.src = o.qr_image），"
        "若改成 innerHTML 就开了 XSS 面"
    )
    assert "由客服核对到账并确认" in js, "manual 模式要告诉用户为什么不会自动到账"


# ------------------------------------------------- full_init.sql 种子账号


def test_full_init_sql_grants_admin_role_to_the_seeded_account():
    """回归：**曾经真实存在过**的坑。

    `full_init.sql` 的 INSERT 漏写 `role` 列，而该列 DDL 默认 0、
    `require_admin` 要求 1 —— 结果昵称叫「管理员」的预置账号打任何管理端点
    都是 403。测试一直发现不了，因为 test_admin_ingest.py 每个用例都显式
    `_set_role(..., 1)`，从来没依赖过种子数据。

    这里直接钉住 SQL 文本：测试库用 create_all 建表、不走这个文件，
    所以只有这一条能守住它。
    """
    from pathlib import Path

    sql = Path(FULL_INIT_SQL).read_text(encoding="utf-8")
    inserts = [
        line
        for line in sql.splitlines()
        if line.strip().upper().startswith("INSERT INTO SYS_USER")
    ]
    assert len(inserts) == 1, f"预期一条 sys_user 插入，实际 {len(inserts)} 条"
    assert "role" in inserts[0], "列清单必须包含 role，否则默认 0、管理员进不了管理端点"
    # 值也得是 1，光有列名不够
    values = sql[sql.index(inserts[0]):]
    values = values[: values.index(";")]
    assert values.rstrip().endswith("1)"), f"role 必须填 1，实际结尾：{values[-40:]!r}"


# ---------------------------------------------------------------- 审计痕迹（N-3）


async def test_manual_confirm_leaves_an_audit_trail(client, manual_mode, caplog):
    """人工确认收款必须留下「谁确认的」这条记录。

    这个端点是**人**替机器做了「钱到账了」的判断 —— manual 模式下服务端收不到
    任何支付通知，钱到没到全凭管理员一句话。所以事后必须能回答
    「这一单是谁放的货」。

    之前只在响应体里回一个 `confirmed_by`，那等于没记录：调用方关掉页面
    就什么都没了。`sys_order` 也没有能放备注的列（`remark` 属于 `SysConfig`，
    不是 `Order` —— review 里那半条是看错了表），所以走结构化审计日志。
    """
    h = await _login(client)
    no = (await client.post("/shop/orders", headers=h)).json()["order_no"]

    admin = await _login(client, "boss9")
    await _promote("boss9")

    with caplog.at_level("INFO", logger="codemax.audit"):
        r = await client.post(f"/shop/orders/{no}/confirm", headers=admin)
    assert r.status_code == 200, r.text

    audit = [rec for rec in caplog.records if rec.name == "codemax.audit"]
    assert len(audit) == 1, f"期望 1 条审计日志，实际 {len(audit)} 条"
    rec = audit[0]
    # 三要素：谁、哪一单、多少钱 —— 少一样这条记录就没法用来对账
    assert getattr(rec, "audit_event", None) == "manual_payment_confirmed"
    assert getattr(rec, "order_no", None) == no
    assert "boss9" in rec.getMessage(), f"审计日志必须记确认人，实际：{rec.getMessage()!r}"
    assert no in rec.getMessage(), "审计日志必须记订单号"


async def test_rejected_confirm_writes_no_audit_record(client, manual_mode, caplog):
    """对照组：确认没成功就不该留审计记录。

    只测「成功会记」是单侧断言 —— 万一写成了进函数就打日志，
    被 403/404 挡掉的尝试也会留下痕迹，审计日志立刻变成噪音。
    """
    h = await _login(client)
    no = (await client.post("/shop/orders", headers=h)).json()["order_no"]

    with caplog.at_level("INFO", logger="codemax.audit"):
        r = await client.post(f"/shop/orders/{no}/confirm", headers=h)  # 非管理员
    assert r.status_code == 403

    assert [rec for rec in caplog.records if rec.name == "codemax.audit"] == [], (
        "被权限挡掉的尝试不该写审计日志"
    )
