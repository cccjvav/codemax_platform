"""No live refunds: actual ASGI/independent sessions plus signed HTTP transports and disposable PG."""

import asyncio
import json
import uuid
from datetime import datetime, timedelta, timezone

import httpx
import psycopg2
import pytest
from sqlalchemy import delete, event, select, update
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession

from app import db_admin
from app import refund_submissions as flow
from app.config import settings
from app.models import PaymentEvent, RefundAuthorization, RefundRequest, User
from app.routers import refunds_admin
from app.wechat_pay import WeChatPayError, submit_full_refund
from tests.conftest import TestSession
from tests.test_db_admin import isolated_pg as isolated_pg
from tests.test_db_admin import legacy_0016, rows
from tests.test_db_admin import maintenance_db as maintenance_db
from tests.test_payment_review import state
from tests.test_refund_requests import ledger, prepare, proof
from tests.test_refunds import provider, query, refunds, response_body
from tests.test_refunds import refund_case as refund_case
from tests.test_wechat_pay import _AUTH_RE, CFG, signed_response, verify


@pytest.fixture(autouse=True)
def isolated_sender(monkeypatch):
    monkeypatch.setattr(settings, "WX_REFUND_SEND_ENABLED", False)
    monkeypatch.setattr(settings, "SITE_BASE_URL", "https://refund.example.test")
    monkeypatch.setattr(refunds_admin, "pay_config", lambda: CFG)

    async def never_live(*args, **kwargs):
        raise WeChatPayError("synthetic failure; never real HTTP")

    monkeypatch.setattr(refunds_admin, "submit_full_refund", never_live)


async def post(client, admin, number, action, body):
    return await client.post(f"/shop/admin/orders/{number}/refunds/{action}", headers=admin, json=body)


async def setup(client, case):
    buyer, admin, create, _ = case
    number = await create("wechat")
    prepared = (await prepare(client, admin, number, proof(number))).json()["refund_request"]
    body = {
        "request_id": uuid.uuid4().hex,
        "confirm_order_no": number,
        "amount": 19900,
        "out_refund_no": prepared["out_refund_no"],
        "reason": "客户确认取消",
        "evidence": "合成独立授权",
    }
    return buyer, admin, number, body


async def authorized(client, case):
    buyer, admin, number, body = await setup(client, case)
    response = await post(client, admin, number, "authorize", body)
    assert response.status_code == 200, response.text
    value = response.json()["submission"]
    send = {k: body[k] for k in ("confirm_order_no", "amount", "out_refund_no", "evidence")}
    send.update(request_id=uuid.uuid4().hex, authorization_id=value["authorization_id"], digest=value["digest"])
    return buyer, admin, number, send, value


async def signed_sender(monkeypatch, number, completed, handler=None, **changes):
    calls = []

    async def exchange(cfg, **kw):
        async def serve(request):
            calls.append(request)
            # Started record must be committed and independently visible before any HTTP.
            async with TestSession() as db:
                assert await db.scalar(select(PaymentEvent.id).where(PaymentEvent.kind == flow.STARTED))
                user = await db.scalar(select(User).where(User.username == "auditor"))
                await db.execute(update(User).where(User.id == user.id).values(update_time=User.update_time))
                await db.commit()  # proves no actor lock held over network
            params = _AUTH_RE.fullmatch(request.headers["Authorization"])
            assert params
            verify(
                params["signature"],
                "POST",
                "/v3/refund/domestic/refunds",
                params["timestamp"],
                params["nonce_str"],
                request.content.decode(),
            )
            if handler:
                return await handler(request)
            data = response_body(number, kw["transaction_id"], completed, **changes)
            data["out_refund_no"] = json.loads(kw["body"])["out_refund_no"]
            return signed_response(data)

        return await submit_full_refund(cfg, **kw, transport=httpx.MockTransport(serve))

    monkeypatch.setattr(refunds_admin, "submit_full_refund", exchange)
    return calls


async def test_authorization_is_separate_immutable_and_retryable(client, refund_case, monkeypatch):
    buyer, admin, number, body = await setup(client, refund_case)
    before = await state(client, admin, number)
    a = await post(client, admin, number, "authorize", body)
    assert a.status_code == 200 and a.headers["cache-control"] == "no-store"
    saved = a.json()["submission"]
    assert saved["attempt"] is None and saved["body"]["reason"] != body["evidence"]
    assert saved["body"]["notify_url"] == "https://refund.example.test/shop/refunds/notify"
    assert saved["body"]["amount"] == {"refund": 19900, "total": 19900, "currency": "CNY"}
    monkeypatch.setattr(settings, "SITE_BASE_URL", "https://changed.example.test")
    again = await post(client, admin, number, "authorize", body)
    assert not again.json()["changed"] and again.json()["submission"] == saved
    for change in [{"reason": "changed"}, {"evidence": "changed"}, {"amount": 1}, {"request_id": uuid.uuid4().hex}]:
        assert (await post(client, admin, number, "authorize", {**body, **change})).status_code == 409
    async with TestSession() as db:
        await db.execute(delete(PaymentEvent).where(PaymentEvent.kind == flow.AUTHORIZED))
        await db.commit()
    assert (await state(client, admin, number))["snapshot"] != before["snapshot"]
    assert (await ledger(client, admin, number, "?before=1"))["refund_submission"] == saved
    assert (await client.post(f"/shop/download/{number}", headers=buyer)).status_code == 200
    assert await refunds() == []


@pytest.mark.parametrize(
    "change",
    [
        {"reason": "中" * 27},
        {"reason": "\x7fxx"},
        {"reason": " "},
        {"notify_url": "https://evil.test"},
        {"amount": True},
        {"currency": "USD"},
        {"reason": " padded "},
    ],
)
async def test_authorization_rejects_invalid_or_caller_owned_fields(client, refund_case, change):
    _, admin, number, body = await setup(client, refund_case)
    assert (await post(client, admin, number, "authorize", {**body, **change})).status_code == 422
    async with TestSession() as db:
        assert await db.scalar(select(RefundAuthorization)) is None


async def test_send_default_off_and_preparation_alone_not_authority(client, refund_case, monkeypatch):
    buyer, admin, number, body = await setup(client, refund_case)
    send = {k: v for k, v in body.items() if k != "reason"}
    send.update(authorization_id="a" * 32, digest="b" * 64)
    monkeypatch.setattr(settings, "WX_REFUND_SEND_ENABLED", True)
    assert (await post(client, admin, number, "send", send)).status_code == 409
    a = (await post(client, admin, number, "authorize", body)).json()["submission"]
    send.update(authorization_id=a["authorization_id"], digest=a["digest"])
    monkeypatch.setattr(settings, "WX_REFUND_SEND_ENABLED", False)
    assert (await post(client, admin, number, "send", send)).status_code == 409
    async with TestSession() as db:
        assert await db.scalar(select(PaymentEvent.id).where(PaymentEvent.kind == flow.STARTED)) is None
    assert (await post(client, buyer, number, "send", send)).status_code == 403
    assert (
        await client.post(
            f"/shop/admin/orders/{number}/refunds/send", headers={"Origin": "https://evil.test"}, json=send
        )
    ).status_code in (401, 403)


@pytest.mark.parametrize("status", ["PROCESSING", "SUCCESS", "CLOSED", "ABNORMAL"])
async def test_signed_submission_is_observation_only_and_exact_attempt_never_resends(
    client, refund_case, monkeypatch, status
):
    buyer, admin, number, body, frozen = await authorized(client, refund_case)
    monkeypatch.setattr(settings, "WX_REFUND_SEND_ENABLED", True)
    calls = await signed_sender(monkeypatch, number, refund_case[3], status=status)
    first = await post(client, admin, number, "send", body)
    assert first.status_code == 200, first.text
    attempt = first.json()["attempt"]
    assert attempt["state"] == "accepted" and attempt["provider_status"] == status
    assert calls[0].content.decode() == flow.canonical(frozen["body"])
    monkeypatch.setattr(settings, "WX_REFUND_SEND_ENABLED", False)
    assert (await post(client, admin, number, "send", body)).json()["attempt"] == attempt
    monkeypatch.setattr(settings, "WX_REFUND_SEND_ENABLED", True)
    assert (await post(client, admin, number, "send", {**body, "request_id": uuid.uuid4().hex})).status_code == 409
    assert len(calls) == 1 and await refunds() == []
    assert (await client.post(f"/shop/download/{number}", headers=buyer)).status_code == 200
    await provider(monkeypatch, number, refund_case[3], out_refund_no=body["out_refund_no"])
    assert (await query(client, admin, number, out_refund_no=body["out_refund_no"])).status_code == 200
    assert (await client.post(f"/shop/download/{number}", headers=buyer)).status_code == 403
    assert (await post(client, admin, number, "send", body)).status_code == 200 and len(calls) == 1


async def test_unknown_retry_keeps_original_bytes_and_waits(client, refund_case, monkeypatch):
    _, admin, number, body, frozen = await authorized(client, refund_case)
    monkeypatch.setattr(settings, "WX_REFUND_SEND_ENABLED", True)

    async def fail(request):
        return signed_response({"code": "SYSTEM_ERROR"}, status=500)

    calls = await signed_sender(monkeypatch, number, refund_case[3], handler=fail)
    assert (await post(client, admin, number, "send", body)).json()["attempt"]["state"] == "unknown"
    assert (await post(client, admin, number, "send", body)).status_code == 200 and len(calls) == 1
    new = {**body, "request_id": uuid.uuid4().hex}
    assert (await post(client, admin, number, "send", new)).status_code == 409
    async with TestSession() as db:
        await db.execute(
            update(PaymentEvent)
            .where(PaymentEvent.kind == flow.STARTED)
            .values(create_time=datetime.now(timezone.utc) - timedelta(seconds=61))
        )
        await db.commit()
    assert (await post(client, admin, number, "send", new)).status_code == 200
    assert len(calls) == 2 and calls[0].content == calls[1].content == flow.canonical(frozen["body"]).encode()


@pytest.mark.parametrize("same", [True, False])
async def test_concurrent_sends_have_one_network_effect(client, refund_case, monkeypatch, same):
    _, admin, number, body, _ = await authorized(client, refund_case)
    monkeypatch.setattr(settings, "WX_REFUND_SEND_ENABLED", True)
    calls = await signed_sender(monkeypatch, number, refund_case[3], status="PROCESSING")
    other = body if same else {**body, "request_id": uuid.uuid4().hex}
    responses = await asyncio.gather(
        post(client, admin, number, "send", body), post(client, admin, number, "send", other)
    )
    assert sorted(r.status_code for r in responses) == ([200, 200] if same else [200, 409])
    assert len(calls) == 1


@pytest.mark.parametrize("committed", [False, True])
async def test_start_commit_failure_never_calls_network_and_replay_does_not_resend(
    client, refund_case, monkeypatch, committed
):
    _, admin, number, body, _ = await authorized(client, refund_case)
    monkeypatch.setattr(settings, "WX_REFUND_SEND_ENABLED", True)
    calls = await signed_sender(monkeypatch, number, refund_case[3], status="PROCESSING")
    original = AsyncSession.commit

    async def broken(db):
        if committed:
            await original(db)
        else:
            await db.flush()
        raise OperationalError("commit", {}, RuntimeError("lost"))

    with monkeypatch.context() as m:
        m.setattr(AsyncSession, "commit", broken)
        assert (await post(client, admin, number, "send", body)).status_code == 503
    assert not calls
    response = await post(client, admin, number, "send", body)
    assert response.status_code == 200 and len(calls) == (0 if committed else 1)


async def test_finish_failure_keeps_unknown_and_never_repeats_attempt(client, refund_case, monkeypatch):
    _, admin, number, body, _ = await authorized(client, refund_case)
    monkeypatch.setattr(settings, "WX_REFUND_SEND_ENABLED", True)
    calls = await signed_sender(monkeypatch, number, refund_case[3], status="PROCESSING")

    def fail(mapper, connection, obj):
        if obj.kind == flow.OBSERVED:
            raise OperationalError("event", {}, RuntimeError("fail"))

    event.listen(PaymentEvent, "before_insert", fail)
    try:
        assert (await post(client, admin, number, "send", body)).status_code == 503
    finally:
        event.remove(PaymentEvent, "before_insert", fail)
    assert (await post(client, admin, number, "send", body)).json()["attempt"]["state"] == "unknown"
    assert len(calls) == 1


async def test_authorization_and_event_rollback_together(client, refund_case):
    _, admin, number, body = await setup(client, refund_case)

    def fail(mapper, connection, obj):
        if obj.kind == flow.AUTHORIZED:
            raise OperationalError("event", {}, RuntimeError("fail"))

    event.listen(PaymentEvent, "before_insert", fail)
    try:
        assert (await post(client, admin, number, "authorize", body)).status_code == 503
    finally:
        event.remove(PaymentEvent, "before_insert", fail)
    async with TestSession() as db:
        assert await db.scalar(select(RefundAuthorization)) is None
    assert (await post(client, admin, number, "authorize", body)).status_code == 200


def test_package_requires_authorization_migration(tmp_path):
    for version, (path, _) in db_admin.migration_manifest().items():
        if int(version) < 13:
            (tmp_path / path.name).write_bytes(path.read_bytes())
    with pytest.raises(db_admin.MaintenanceError):
        db_admin.migration_manifest(tmp_path)


def test_real_pg_authorization_contract_and_append_only(maintenance_db):
    conn, _ = maintenance_db
    db_admin.initialize(conn)
    legacy_0016(conn)
    rows(
        conn,
        "DROP TRIGGER check_system_refund_actor ON refund_receipt; DROP FUNCTION codemax_check_system_refund_actor(); ALTER TABLE refund_receipt DROP CONSTRAINT ck_refund_authority; ALTER TABLE refund_receipt DROP COLUMN verification_event_id; ALTER TABLE refund_receipt ALTER COLUMN actor_id SET NOT NULL; DELETE FROM schema_migration WHERE version::integer=16; DROP TABLE refund_verification_job; DROP FUNCTION codemax_check_refund_verification_job(); DROP TABLE refund_send_stop; DROP FUNCTION codemax_check_refund_send_stop(); DROP TABLE refund_authorization; DROP FUNCTION codemax_check_refund_authorization(); DELETE FROM schema_migration WHERE version IN ('0013','0014','0015')",
    )
    rows(conn, "INSERT INTO sys_user(id,username,password,role,status) VALUES(1,'author','synthetic',1,1)")
    rows(
        conn,
        "INSERT INTO sys_order(id,order_no,user_id,product_name,amount,status,payment_mode,merchant_id,app_id,transaction_id,currency) VALUES(1,'ORDER1',1,'synthetic',100,'paid','wechat','MCH','APP','TX','CNY')",
    )
    rows(
        conn,
        "INSERT INTO payment_receipt(order_id,source,transaction_id,amount,currency,merchant_id,app_id) VALUES(1,'wechat','TX',100,'CNY','MCH','APP')",
    )
    rows(
        conn,
        "INSERT INTO refund_request(order_id,payment_receipt_id,request_id,out_refund_no,merchant_id,app_id,amount,currency,actor_id,actor_name,evidence) VALUES(1,1,repeat('a',32),'CMR'||repeat('b',32),'MCH','APP',100,'CNY',1,'author','synthetic')",
    )
    db_admin.migrate(conn)
    assert rows(conn, "SELECT * FROM refund_authorization") == []
    body = {
        "transaction_id": "TX",
        "out_refund_no": "CMR" + "b" * 32,
        "reason": "cancel",
        "notify_url": "https://example.test/shop/refunds/notify",
        "amount": {"total": 100, "refund": 100, "currency": "CNY"},
    }
    sql = "INSERT INTO refund_authorization(preparation_id,request_id,actor_id,actor_name,evidence,body,digest) VALUES(1,%s,1,'author','synthetic',%s,%s)"

    def insert(text):
        with conn, conn.cursor() as cur:
            cur.execute(sql, (uuid.uuid4().hex, text, flow.digest(text)))

    for bad in [
        {**body, "amount": {"total": 100, "refund": 1, "currency": "CNY"}},
        {**body, "transaction_id": "OTHER"},
        {**body, "reason": "中" * 27},
        {**body, "notify_url": "https://evil.test/?x=1"},
    ]:
        text = flow.canonical(bad)
        with pytest.raises(psycopg2.Error):
            insert(text)
    text = flow.canonical(body)
    insert(text)
    for bad in ["UPDATE refund_authorization SET evidence='changed'", "DELETE FROM refund_authorization"]:
        with pytest.raises(psycopg2.Error):
            rows(conn, bad)
    assert rows(conn, "SELECT body FROM refund_authorization") == [(text,)]
    db_admin.migrate(conn)


@pytest.mark.parametrize('fault', ['bad-signature', 'wrong-contract', 'role-changed'])
async def test_untrusted_result_or_revoked_actor_cannot_settle(client, refund_case, monkeypatch, fault):
    buyer, admin, number, body, _ = await authorized(client, refund_case)
    monkeypatch.setattr(settings, 'WX_REFUND_SEND_ENABLED', True)
    async def handler(request):
        if fault == 'role-changed':
            async with TestSession() as db:
                await db.execute(update(User).where(User.username == 'auditor').values(role=0))
                await db.commit()
        request_body = json.loads(request.content)
        data = response_body(number, request_body['transaction_id'], refund_case[3], out_refund_no=body['out_refund_no'])
        if fault == 'wrong-contract':
            data['amount']['refund'] = 1
        response = signed_response(data)
        if fault == 'bad-signature':
            response.headers['Wechatpay-Signature'] = 'bad'
        return response
    calls = await signed_sender(monkeypatch, number, refund_case[3], handler=handler)
    result = await post(client, admin, number, 'send', body)
    assert result.status_code == 200
    assert result.json()['attempt']['state'] == ('accepted' if fault == 'role-changed' else 'unknown')
    assert len(calls) == 1 and await refunds() == []
    assert (await client.post(f'/shop/download/{number}', headers=buyer)).status_code == 200
    if fault == 'role-changed':
        assert (await post(client, admin, number, 'send', body)).status_code == 403


async def test_send_rechecks_permissions_merchant_digest_and_intervening_activity(client, refund_case, monkeypatch):
    _, admin, number, body, _ = await authorized(client, refund_case)
    monkeypatch.setattr(settings, 'WX_REFUND_SEND_ENABLED', True)
    calls = await signed_sender(monkeypatch, number, refund_case[3])
    for change in [{'digest':'0'*64}, {'out_refund_no':'CMR'+'f'*32}, {'amount':1}, {'authorization_id':'f'*32}]:
        assert (await post(client, admin, number, 'send', {**body, **change})).status_code == 409
    from dataclasses import replace
    with monkeypatch.context() as m:
        m.setattr(refunds_admin,'pay_config',lambda: replace(CFG,mchid='FOREIGN'))
        assert (await post(client, admin, number, 'send', body)).status_code == 409
    original = refunds_admin.lock_user
    async def revoked(db, uid):
        row = await original(db, uid)
        row.credential_version += 1
        return row
    with monkeypatch.context() as m:
        m.setattr(refunds_admin,'lock_user',revoked)
        assert (await post(client, admin, number, 'send', body)).status_code == 403
    async with TestSession() as db:
        prepared = await db.scalar(select(RefundRequest))
        db.add(PaymentEvent(order_id=prepared.order_id, kind='refund_notify_signal', attempt_id=uuid.uuid4().hex))
        await db.commit()
    assert (await post(client, admin, number, 'send', body)).status_code == 409
    assert calls == []


@pytest.mark.parametrize('origin', ['https://[', 'https://user@host', 'https://host/path', 'https://host:bad', 'http://host'])
async def test_invalid_callback_never_authorizes(client, refund_case, monkeypatch, origin):
    _, admin, number, body = await setup(client, refund_case)
    monkeypatch.setattr(settings, 'SITE_BASE_URL', origin)
    assert (await post(client, admin, number, 'authorize', body)).status_code == 409
