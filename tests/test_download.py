"""S3-02 测试：预签名下载 URL + 可重领短时下载链接的双重校验。

两重机制分别测：
- **第一重（数据库状态与所有者）**：仅已付款所有者可申请，paid/downloaded均能重领
- **第二重（预签名 URL）**：签名绑定 key、有过期时间，篡改 / 过期 / 换 key 一律 403

当前仅实现并测试LocalStorage，没有云存储适配器；
HMAC和过期时间约束链接，但链接有效期内仍可转交，不是云服务验收或防转卖证明。
"""
import time
from urllib.parse import urlsplit

import pytest
from sqlalchemy import select

from app.config import settings
from app.delivery import snapshot_product
from app.models import Order, User
from app.storage import LocalStorage, StorageError, build_storage, sign_download, verify_download

# product fixture 与商品常量已上移到 conftest（test_shop_page.py 也要用）
from tests.conftest import PRODUCT_BYTES, TestSession

_seq = 0


async def auth_headers(client, username="buyer", password="secret123") -> dict:
    await client.post("/auth/register", json={"username": username, "password": password})
    r = await client.post("/auth/login", data={"username": username, "password": password})
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


async def make_order(status: str, username: str = "buyer") -> str:
    global _seq
    _seq += 1
    async with TestSession() as s:
        user = (await s.execute(select(User).where(User.username == username))).scalar_one_or_none()
        if user is None:
            user = User(username=username, password="x")
            s.add(user)
            await s.flush()
        snapshot = snapshot_product(LocalStorage(settings.STORAGE_LOCAL_ROOT, 'http://test', settings.SECRET_KEY), settings.STORAGE_PRODUCT_KEY)
        order = Order(
            order_no=f"CM20260901DL{_seq:04d}",
            user_id=user.id,
            product_name="毕设服务",
            amount=19900, payment_mode=settings.SHOP_PAY_MODE,
            delivery_key=snapshot.key, delivery_digest=snapshot.digest, delivery_size=snapshot.size,
            status=status,
        )
        s.add(order)
        await s.commit()
        return order.order_no


async def status_of(order_no: str) -> str:
    async with TestSession() as s:
        return (
            await s.execute(select(Order.status).where(Order.order_no == order_no))
        ).scalar_one()


def path_of(url: str) -> str:
    """把绝对预签名 URL 拆成 path?query，交给测试客户端请求。"""
    p = urlsplit(url)
    return f"{p.path}?{p.query}"


# ---------------------------------------------------------------- 第一重：数据库状态


async def test_download_requires_login(client, product):
    assert (await client.post("/shop/download/CM1")).status_code == 401


async def test_download_forbidden_when_unpaid(client, product):
    h = await auth_headers(client)
    order_no = await make_order("pending", "buyer")
    r = await client.post(f"/shop/download/{order_no}", headers=h)
    assert r.status_code == 403
    assert "未支付" in r.json()["detail"]


async def test_download_issues_url_and_marks_downloaded(client, product):
    h = await auth_headers(client)
    order_no = await make_order("paid", "buyer")
    r = await client.post(f"/shop/download/{order_no}", headers=h)
    assert r.status_code == 200
    body = r.json()
    assert body["download_url"].startswith("http://test/shop/dl?")
    assert body["expires_in"] == settings.DOWNLOAD_URL_TTL
    assert await status_of(order_no) == "downloaded"


async def test_second_download_recovers_paid_entitlement(client, product):
    """批准的新规则：同一已付订单可以重新领取短时链接。"""
    h = await auth_headers(client)
    order_no = await make_order("paid", "buyer")
    assert (await client.post(f"/shop/download/{order_no}", headers=h)).status_code == 200
    r = await client.post(f"/shop/download/{order_no}", headers=h)
    assert r.status_code == 200
    assert r.json()["download_url"] and r.json()["status"] == "downloaded"


async def test_other_users_order_not_found(client, product):
    bob = await auth_headers(client, "bob", "secret456")  # alice 的订单由下面按用户名创建
    order_no = await make_order("paid", "alice")
    r = await client.post(f"/shop/download/{order_no}", headers=bob)
    assert r.status_code == 404
    assert await status_of(order_no) == "paid", "别人的订单不能被我领走"


async def test_missing_product_file_404(client, product):
    h = await auth_headers(client)
    order_no = await make_order("paid", "buyer")
    async with TestSession() as db:
        order = await db.scalar(select(Order).where(Order.order_no == order_no))
        product.joinpath(order.delivery_key).unlink()
    r = await client.post(f"/shop/download/{order_no}", headers=h)
    assert r.status_code == 404
    assert await status_of(order_no) == "paid", "文件不在就不该把订单标记为已下载"


# ---------------------------------------------------------------- 第二重：预签名 URL


async def test_presigned_url_downloads_the_file(client, product):
    h = await auth_headers(client)
    order_no = await make_order("paid", "buyer")
    url = (await client.post(f"/shop/download/{order_no}", headers=h)).json()["download_url"]
    r = await client.get(path_of(url))
    assert r.status_code == 200
    assert r.content == PRODUCT_BYTES
    assert "attachment" in r.headers["content-disposition"]


async def test_download_streams_instead_of_reading_whole_file(client, product, monkeypatch):
    """P1-6：下载出口不得把整个文件读进内存。

    旧实现 `data = storage.read(key)` 把整个压缩包 `read_bytes()` 进内存再包成
    `Response` —— 一个 500MB 的软件包就是 500MB 常驻，几个用户同时下载就 OOM。
    改法是用 `FileResponse`：starlette 分块读盘、边读边发，并自动带正确的
    Content-Length。

    断言方式：把 `LocalStorage.read` 换成会记账的桩，端点若还走整包读就会被记到。
    直接盯「不许调 read」比盯内存占用可靠得多 —— ASGITransport 在测试里本来就会
    把响应体缓冲起来，内存量根本测不出区别。
    """
    calls: list[str] = []
    real_read = LocalStorage.read

    def spy(self, key):
        calls.append(key)
        return real_read(self, key)

    monkeypatch.setattr(LocalStorage, "read", spy)

    h = await auth_headers(client)
    number = await make_order("paid")
    url = (await client.post(f"/shop/download/{number}", headers=h)).json()["download_url"]
    r = await client.get(path_of(url))

    assert r.status_code == 200
    assert r.content == PRODUCT_BYTES  # 内容必须一字不差
    assert r.headers["content-length"] == str(len(PRODUCT_BYTES))  # 流式也要有正确长度
    assert calls == [], (
        f"serve_download 调了 {len(calls)} 次 LocalStorage.read —— 整个文件被读进内存了。"
        "下载出口必须用 FileResponse 分块读盘。"
    )


async def test_signed_url_for_missing_file_returns_404(client, product):
    """签名合法但文件已被删掉时必须 404，不能 500。

    这一条是改 `FileResponse` 时**必须**配套的：starlette 的 FileResponse 遇到
    文件不存在会在**响应阶段**才炸，那已经不是 HTTPException 能兜住的位置了。
    所以端点要先自己判存在。
    """
    h = await auth_headers(client)
    number = await make_order("paid")
    url = (await client.post(f"/shop/download/{number}", headers=h)).json()["download_url"]
    async with TestSession() as db:
        order = await db.scalar(select(Order).where(Order.order_no == number))
        product.joinpath(order.delivery_key).unlink()
    r = await client.get(path_of(url))
    assert r.status_code == 404


async def test_tampered_signature_rejected(client, product):
    h = await auth_headers(client)
    order_no = await make_order("paid", "buyer")
    url = (await client.post(f"/shop/download/{order_no}", headers=h)).json()["download_url"]
    tampered = url[:-1] + ("0" if url[-1] != "0" else "1")
    assert (await client.get(path_of(tampered))).status_code == 403


async def test_expired_url_rejected(client, product):
    number = await make_order("paid")
    async with TestSession() as db:
        key = await db.scalar(select(Order.delivery_key).where(Order.order_no == number))
    expires = int(time.time()) - 1
    sig = sign_download(settings.SECRET_KEY, key, expires, order_no=number)
    r = await client.get("/shop/dl", params={"order_no": number, "key": key, "expires": expires, "signature": sig})
    assert r.status_code == 403


async def test_signature_is_bound_to_key(client, product):
    """拿 A 文件的合法签名去下 B 文件必须失败 —— 否则一个链接就能遍历整个桶。"""
    LocalStorage(str(product), "http://test", settings.SECRET_KEY).put("product/other.zip", b"secret")
    number = await make_order("paid")
    async with TestSession() as db:
        key = await db.scalar(select(Order.delivery_key).where(Order.order_no == number))
    expires = int(time.time()) + 300
    sig = sign_download(settings.SECRET_KEY, key, expires, order_no=number)
    r = await client.get("/shop/dl", params={"order_no": number, "key": "product/other.zip", "expires": expires, "signature": sig})
    assert r.status_code == 403


async def test_directory_traversal_blocked(client, product):
    (product.parent / "secret.txt").write_bytes(b"top secret")
    storage = LocalStorage(str(product), "http://test", settings.SECRET_KEY)
    assert storage.exists("../secret.txt") is False
    with pytest.raises(ValueError):
        storage.read("../secret.txt")


async def test_unknown_backend_fails_loudly(client, product, monkeypatch):
    """后端写错要报清楚的错，不能静默退回本地（那是安全性降级）。"""
    monkeypatch.setattr(settings, "STORAGE_BACKEND", "oss")
    with pytest.raises(StorageError) as e:
        build_storage("http://test")
    assert "TD-128" in str(e.value)

    h = await auth_headers(client)
    order_no = await make_order("paid", "buyer")
    assert (await client.post(f"/shop/download/{order_no}", headers=h)).status_code == 503


async def test_download_exit_shares_the_download_rate_limit(client, product, monkeypatch):
    """TD-261 / ROADMAP A-08：`GET /shop/dl` 与 `POST /shop/download` 同属 `download` 桶。

    出口每次都要做全量快照哈希（O-01 另测），没有限流就是一条免登录的 CPU 放大入口。
    同一客户端：领取占 1 次，随后的出口请求共享余额；超额是 429 + Retry-After，而不是 403/404。
    限流由 conftest 默认关闭，这里显式打开并把配额压到 3。
    """
    from app.ratelimit import limiter

    limiter.reset()
    monkeypatch.setattr(settings, "RATE_LIMIT_ENABLED", True)
    monkeypatch.setattr(settings, "RATE_LIMIT_WINDOW", 60)
    monkeypatch.setattr(settings, "RATE_LIMIT_TOOLS", 3)
    try:
        h = await auth_headers(client)
        order_no = await make_order("paid", "buyer")
        url = (await client.post(f"/shop/download/{order_no}", headers=h)).json()["download_url"]
        first = await client.get(path_of(url))
        second = await client.get(path_of(url))
        third = await client.get(path_of(url))
        assert (first.status_code, first.content) == (200, PRODUCT_BYTES)
        assert (second.status_code, second.content) == (200, PRODUCT_BYTES), "有效期内重复请求仍然允许"
        assert third.status_code == 429 and third.headers["retry-after"].isdigit()
        assert await status_of(order_no) == "downloaded", "限流不改变已发放的订单状态"
        limiter.reset()
        assert (await client.get(path_of(url))).status_code == 200, "窗口过后同一链接照常可用；权益判断没有被缓存"
    finally:
        limiter.reset()


async def test_repeated_and_range_downloads_hash_once_but_recheck_revocation_every_time(client, product, monkeypatch):
    """TD-266 / ROADMAP O-01：出口对同一未变快照只做一次全量哈希，之后只 stat；权益判断不缓存。

    测量：sha256 约 900 MiB/s，512 MiB 商品每次下载 0.53 s CPU；一分钟 30 次 Range 请求对 256 MiB
    商品要烧 8 s 线程池 CPU。改后重复/Range/HEAD 请求不再重读文件，但退款撤权每次都重新查库。
    """
    import time as _time
    from datetime import datetime, timezone

    from app import delivery
    from app.models import PaymentReceipt, RefundReceipt

    PRODUCT_NAME = settings.STORAGE_PRODUCT_KEY.rsplit("/", 1)[-1]
    delivery._VERIFIED.clear()
    monkeypatch.setattr(delivery, "_VERIFY_CACHE_ENABLED", True)
    monkeypatch.setattr(delivery, "_now_ns", lambda: _time.time_ns() + delivery._VERIFIED_GRACE_NS + 10**9)
    hashed: list[str] = []
    real = delivery.file_digest
    monkeypatch.setattr(delivery, "file_digest", lambda path: (hashed.append(path.name), real(path))[1])
    try:
        h = await auth_headers(client)
        order_no = await make_order("paid", "buyer")
        url = (await client.post(f"/shop/download/{order_no}", headers=h)).json()["download_url"]
        assert hashed == [PRODUCT_NAME], "领取链接时做一次完整校验"
        full = await client.get(path_of(url))
        head = await client.get(path_of(url), headers={"Range": "bytes=0-3"})
        tail = await client.get(path_of(url), headers={"Range": "bytes=4-"})
        again = await client.get(path_of(url))
        assert full.status_code == 200 and full.content == PRODUCT_BYTES
        assert head.status_code == 206 and head.content == PRODUCT_BYTES[:4]
        assert tail.status_code == 206 and tail.content == PRODUCT_BYTES[4:]
        assert again.status_code == 200 and again.content == PRODUCT_BYTES
        assert hashed == [PRODUCT_NAME], f"同一 inode 状态的重复/Range 请求不得再读整个文件，实际 {hashed}"

        # 权益仍每次实时判断：直接写入一张全额退款凭证（SQLite 无触发器，不走管理员流程），缓存命中的路径照样 403
        async with TestSession() as s:
            order = (await s.execute(select(Order).where(Order.order_no == order_no))).scalar_one()
            s.add(PaymentReceipt(order_id=order.id, source="manual", transaction_id="manual-test-pay",
                                 amount=order.amount, currency="CNY", actor_id=order.user_id, actor_name="buyer",
                                 evidence="test payment evidence", paid_at=datetime.now(timezone.utc)))
            await s.flush()
            receipt = (await s.execute(select(PaymentReceipt).where(PaymentReceipt.order_id == order.id))).scalar_one()
            s.add(RefundReceipt(order_id=order.id, payment_receipt_id=receipt.id, source="manual", merchant_id="m",
                                refund_id="manual-test-refund", out_refund_no="OUT-1", amount=order.amount, currency="CNY",
                                actor_id=order.user_id, actor_name="buyer", evidence="test refund evidence",
                                completed_at=datetime.now(timezone.utc)))
            await s.commit()
        assert (await client.get(path_of(url))).status_code == 403, "退款后即使哈希被跳过也必须拒绝"
        assert hashed == [PRODUCT_NAME], "拒绝发生在权益检查，不靠重新哈希"
    finally:
        delivery._VERIFIED.clear()


# ---------------------------------------------------------------- 策略与签名本身


def test_verify_download_rules():
    secret, key = "s3cr3t", "a/b.zip"
    ok = int(time.time()) + 60
    assert verify_download(secret, key, ok, sign_download(secret, key, ok)) is True
    assert verify_download(secret, key, int(time.time()) - 1, sign_download(secret, key, int(time.time()) - 1)) is False
    assert verify_download(secret, key, ok, sign_download("other", key, ok)) is False
    assert verify_download(secret, "other.zip", ok, sign_download(secret, key, ok)) is False
    assert verify_download(secret, key, ok, sign_download(secret, key, ok).upper()) is False


def test_strategy_switch_by_config(monkeypatch, tmp_path):
    """策略模式：换后端就是换一个实现，业务代码不动（S3-02-2）。"""
    monkeypatch.setattr(settings, "STORAGE_LOCAL_ROOT", str(tmp_path))
    monkeypatch.setattr(settings, "STORAGE_BACKEND", "local")
    local = build_storage("http://test/")
    assert local.backend == "local"
    assert local.presigned_url("k.zip", expires_in=60).startswith("http://test/shop/dl?")

    for name in ("oss", "cos", ""):
        monkeypatch.setattr(settings, "STORAGE_BACKEND", name)
        with pytest.raises(StorageError):
            build_storage("http://test")
