"""Handoff diagnostics for baseline 7f2e125; PASS means an outstanding behavior was reproduced.

NOT release regression assertions. Explicit invocation only (not named test_*.py):
python -m pytest -c pytest.ini tests/audit_handoff_probes.py -q -s
Use disposable databases only: fixtures recreate tables.
All external requests use local replacements.
After fixing a finding, replace its reproduction with a protective test in the normal suite.

Retired probes (fixed; the protective regression now lives in the default suite):
- F-01 body consumed/echoed before field rejection -> tests/test_request_body_budget.py (A-01).
- F-05 bool/float embedding index accepted as 0/1 -> tests/test_faq_semantic.py (A-05).
"""

import asyncio

import httpx
import pytest
from sqlalchemy import select

from app.config import settings
from app.models import Order, PaymentReceipt
from app.routers import shop
from app.tools.llm import LLMClient
from tests.conftest import TestSession
from tests.test_download import auth_headers
from tests.test_wechat_pay import _ENV


@pytest.mark.asyncio
async def test_llm_reads_ignored_large_padding():
    class Upstream(httpx.AsyncByteStream):
        read = 0

        async def __aiter__(self):
            for chunk in [
                b'{"choices":[{"message":{"content":"ok"}}],"padding":"',
                *[b"x" * 65536 for _ in range(32)],
                b'"}',
            ]:
                self.read += len(chunk)
                yield chunk

    body = Upstream()
    client = LLMClient(api_key="synthetic", transport=httpx.MockTransport(lambda r: httpx.Response(200, stream=body)))
    assert await client.chat("probe", "probe") == "ok"
    print(f"LLM accepted ignored bytes={body.read}")


@pytest.mark.asyncio
async def test_deep_json_is_not_normalized():
    body = b"[" * 1200 + b"0" + b"]" * 1200
    client = LLMClient(api_key="synthetic", transport=httpx.MockTransport(lambda r: httpx.Response(200, content=body)))
    with pytest.raises(RecursionError):
        await client.chat("probe", "probe")
    print("LLM nesting=1200 escapes as RecursionError rather than LLMError")


@pytest.mark.asyncio
async def test_two_prepays_in_flight_for_one_order(client, product, monkeypatch):
    headers = await auth_headers(client, "handoff_prepay")
    for key, value in _ENV.items():
        monkeypatch.setattr(settings, key, value)
    monkeypatch.setattr(settings, "SHOP_PAY_MODE", "wechat")
    monkeypatch.setattr(settings, "RATE_LIMIT_ENABLED", True)
    monkeypatch.setattr(settings, "RATE_LIMIT_TOOLS", 1)
    arrived, release = asyncio.Event(), asyncio.Event()
    calls = []

    async def provider(cfg, **kw):
        calls.append(kw["out_trade_no"])
        if len(calls) == 2:
            arrived.set()
        await release.wait()
        return "weixin://synthetic"

    monkeypatch.setattr(shop, "native_prepay", provider)
    tasks = [asyncio.create_task(client.post("/shop/orders", headers=headers)) for _ in range(2)]
    try:
        await asyncio.wait_for(arrived.wait(), 5)
    finally:
        release.set()
        results = await asyncio.gather(*tasks)
    assert [r.status_code for r in results] == [200, 200]
    assert len(calls) == 2 and len(set(calls)) == 1
    async with TestSession() as db:
        assert len((await db.scalars(select(Order))).all()) == 1
        assert (await db.scalars(select(PaymentReceipt))).all() == []
    print("PREPAY concurrent calls=2 order_count=1 receipts=0 with RATE_LIMIT_TOOLS=1")


@pytest.mark.asyncio
async def test_foreign_origin_form_login_accepted(client):
    await client.post("/auth/register", json={"username": "handoff_login", "password": "synthetic-pass"})
    response = await client.post(
        "/auth/login",
        data={"username": "handoff_login", "password": "synthetic-pass"},
        headers={"Origin": "https://untrusted.invalid", "Sec-Fetch-Site": "cross-site"},
    )
    assert response.status_code == 200 and "access_token=" in response.headers["set-cookie"]
    print("LOGIN foreign-origin form status=200, auth-cookie-issued; browser exploit not tested")
