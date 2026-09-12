"""合并审查第一批回归：配置诊断、压缩响应、支付原子性与版本响应。

只使用 MockTransport 和 conftest 的隔离数据库，不连接支付平台或抓取外部站点。
"""
import gzip
import zlib
from datetime import datetime, timezone

import httpx
import pytest
from sqlalchemy.engine import make_url

from app.config import Settings, settings
from app.models import Order, User
from app.order_state import CLOSED, DOWNLOADED, PAID, mark_closed, mark_paid
from app.startup_checks import DEFAULT_SECRET, check_production_settings
from app.tools.crawler import CrawlError, _request
from tests.conftest import TestSession
from tests.test_diagrams import auth_headers, create


@pytest.mark.parametrize("secret", [DEFAULT_SECRET, "short", "x" * 32])
@pytest.mark.parametrize("password,url", [("", ""), ("password", ""), ("", "postgresql+asyncpg://test")])
def test_secret_diagnostic_is_independent_of_database(monkeypatch, secret, password, url):
    monkeypatch.setattr(settings, "ENV", "production")
    monkeypatch.setattr(settings, "SECRET_KEY", secret)
    monkeypatch.setattr(settings, "DB_PASSWORD", password)
    monkeypatch.setattr(settings, "DATABASE_URL", url)
    issues = check_production_settings()
    key_issues = [p for p in issues if p.startswith("SECRET_KEY")]
    assert len(key_issues) == (0 if len(secret) >= 32 else 1)
    if secret == DEFAULT_SECRET:
        assert "默认值" in key_issues[0]
    elif secret == "short":
        assert "短于" in key_issues[0]
    assert any(p.startswith("DB_PASSWORD") for p in issues) == (not password and not url)


@pytest.mark.parametrize("value", ["with space", "a+b@c/d%", "用户名 密码"])
def test_database_credentials_roundtrip(value):
    config = Settings(_env_file=None, DATABASE_URL="", DB_USER=value, DB_PASSWORD=value)
    parsed = make_url(config.sqlalchemy_url)
    assert parsed.username == value
    assert parsed.password == value


@pytest.mark.parametrize("encoding,compress", [("gzip", gzip.compress), ("deflate", zlib.compress)])
async def test_crawler_decodes_compressed_body_once(encoding, compress):
    raw = "<html>正常的压缩页面</html>".encode()
    packed = compress(raw)
    transport = httpx.MockTransport(lambda request: httpx.Response(
        200, content=packed,
        headers={"Content-Encoding": encoding, "Content-Length": str(len(packed)),
                 "Content-Type": "text/html; charset=utf-8"},
    ))
    response = await _request("https://93.184.216.34/page", transport=transport, max_bytes=1024)
    assert response.content == raw
    assert "压缩页面" in response.text
    assert "content-encoding" not in response.headers
    assert int(response.headers["content-length"]) == len(raw)


async def test_decoded_body_still_has_a_size_budget():
    packed = gzip.compress(b"x" * 2048)
    transport = httpx.MockTransport(lambda request: httpx.Response(
        200, content=packed, headers={"Content-Encoding": "gzip"},
    ))
    with pytest.raises(CrawlError, match="页面过大"):
        await _request("https://93.184.216.34/page", transport=transport, max_bytes=1024)


async def test_restore_etag_can_be_used_for_next_update(client):
    headers = await auth_headers(client)
    created = await create(client, headers)
    did = created.json()["id"]
    await client.delete(f"/diagrams/{did}", headers=headers)
    restored = await client.post(f"/diagrams/{did}/restore", headers=headers)
    assert restored.status_code == 200
    assert restored.headers.get("etag") == '"2"'
    assert restored.headers["etag"] != created.headers["etag"]
    changed = await client.put(f"/diagrams/{did}", headers={
        **headers, "If-Match": restored.headers["etag"],
    }, json={"name": "恢复后继续修改", "content": "<mxfile/>"})
    assert changed.status_code == 200
    assert changed.headers["etag"] != restored.headers["etag"]


async def _order():
    async with TestSession() as db:
        user = User(username="review-buyer", password="unused")
        db.add(user)
        await db.flush()
        order = Order(user_id=user.id, order_no="review-order", product_name="Test", amount=100)
        db.add(order)
        await db.commit()
        return order.id


async def test_payment_accepts_an_order_closed_after_it_was_read(client):
    oid = await _order()
    async with TestSession() as payment, TestSession() as closing:
        stale = await payment.get(Order, oid)
        await mark_closed(closing, await closing.get(Order, oid))
        assert stale.status != CLOSED
        assert await mark_paid(payment, stale) is True
        assert stale.status == PAID


@pytest.mark.parametrize("download_first", [False, True])
async def test_competing_payment_preserves_first_receipt(client, download_first):
    from app.order_state import mark_downloaded

    oid = await _order()
    first_time = datetime(2026, 9, 11, tzinfo=timezone.utc)
    async with TestSession() as first, TestSession() as second:
        old_snapshot = await second.get(Order, oid)
        winning = await first.get(Order, oid)
        assert await mark_paid(first, winning, transaction_id="FIRST", paid_at=first_time)
        if download_first:
            await mark_downloaded(first, winning)
        assert not await mark_paid(second, old_snapshot, transaction_id="LATER", paid_at=datetime.now(timezone.utc))
        assert old_snapshot.status == (DOWNLOADED if download_first else PAID)
        assert old_snapshot.transaction_id == "FIRST"
        assert old_snapshot.paid_at.replace(tzinfo=timezone.utc) == first_time
