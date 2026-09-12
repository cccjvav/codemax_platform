"""S3-02 测试：预签名下载 URL + 一次性下载双重校验。

两重机制分别测：
- **第一重（数据库状态）**：同一订单只能领一次链接，第二次 403 —— 防倒卖的主力
- **第二重（预签名 URL）**：签名绑定 key、有过期时间，篡改 / 过期 / 换 key 一律 403

云存储后端没有密钥（S3-02-1 未完成），所以这里测的是本地后端；
但它用的是与云厂商同构的 HMAC 签名 + 过期时间机制，安全性质是一样的（TD-128）。
"""
import time
from urllib.parse import urlsplit

import pytest
from sqlalchemy import select

from app.config import settings
from app.models import Order, User
from app.storage import LocalStorage, StorageError, build_storage, sign_download, verify_download

# product fixture 与商品常量已上移到 conftest（test_shop_page.py 也要用）
from tests.conftest import PRODUCT_BYTES, PRODUCT_KEY, TestSession

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
        order = Order(
            order_no=f"CM20260901DL{_seq:04d}",
            user_id=user.id,
            product_name="毕设服务",
            amount=19900,
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
    product.joinpath(PRODUCT_KEY).unlink()
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

    expires = int(time.time()) + 300
    sig = sign_download(settings.SECRET_KEY, PRODUCT_KEY, expires)
    r = await client.get(f"/shop/dl?key={PRODUCT_KEY}&expires={expires}&signature={sig}")

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
    expires = int(time.time()) + 300
    sig = sign_download(settings.SECRET_KEY, "product/gone.zip", expires)
    r = await client.get(f"/shop/dl?key=product/gone.zip&expires={expires}&signature={sig}")
    assert r.status_code == 404


async def test_tampered_signature_rejected(client, product):
    h = await auth_headers(client)
    order_no = await make_order("paid", "buyer")
    url = (await client.post(f"/shop/download/{order_no}", headers=h)).json()["download_url"]
    tampered = url[:-1] + ("0" if url[-1] != "0" else "1")
    assert (await client.get(path_of(tampered))).status_code == 403


async def test_expired_url_rejected(client, product):
    expires = int(time.time()) - 1
    sig = sign_download(settings.SECRET_KEY, PRODUCT_KEY, expires)
    r = await client.get(f"/shop/dl?key={PRODUCT_KEY}&expires={expires}&signature={sig}")
    assert r.status_code == 403


async def test_signature_is_bound_to_key(client, product):
    """拿 A 文件的合法签名去下 B 文件必须失败 —— 否则一个链接就能遍历整个桶。"""
    LocalStorage(str(product), "http://test", settings.SECRET_KEY).put("product/other.zip", b"secret")
    expires = int(time.time()) + 300
    sig = sign_download(settings.SECRET_KEY, PRODUCT_KEY, expires)
    r = await client.get(f"/shop/dl?key=product/other.zip&expires={expires}&signature={sig}")
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
