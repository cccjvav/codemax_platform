"""Permanent local send stop; signed synthetic in-flight requests are NOT recalled."""

import asyncio
import json
import uuid

import psycopg2
import pytest
from sqlalchemy import delete, event, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession

from app import db_admin
from app import refund_submissions as flow
from app.config import settings
from app.models import PaymentEvent, RefundSendStop
from app.routers import refunds_admin
from tests.conftest import TestSession
from tests.test_db_admin import isolated_pg as isolated_pg
from tests.test_db_admin import maintenance_db as maintenance_db
from tests.test_db_admin import rows
from tests.test_payment_ledger import admin_headers
from tests.test_payment_review import state
from tests.test_refund_requests import ledger
from tests.test_refund_submissions import authorized, post, setup, signed_sender
from tests.test_refund_submissions import isolated_sender as isolated_sender
from tests.test_refunds import provider, query, refunds, response_body
from tests.test_refunds import refund_case as refund_case
from tests.test_wechat_pay import signed_response


def stop_proof(send):
    return {**send, "request_id": uuid.uuid4().hex, "evidence": "核对发现停办，保留原记录"}


async def stop_rows():
    async with TestSession() as db:
        return list((await db.scalars(select(RefundSendStop))).all())


async def test_stop_is_immutable_local_only_and_blocks_new_send(client, refund_case, monkeypatch):
    buyer, admin, number, send, original = await authorized(client, refund_case)
    before = await state(client, admin, number)
    body = stop_proof(send)
    first = await post(client, admin, number, "stop", body)
    assert first.status_code == 200 and first.headers["cache-control"] == "no-store"
    stop = first.json()["stop"]
    assert first.json()["changed"] and stop["scope"] == "future_local_sends_only"
    again = await post(client, admin, number, "stop", body)
    assert not again.json()["changed"] and again.json()["stop"] == stop
    for change in [{"request_id": uuid.uuid4().hex}, {"evidence": "另一依据不可覆盖"}]:
        assert (await post(client, admin, number, "stop", {**body, **change})).status_code == 409
    after = (await ledger(client, admin, number, "?before=1"))["refund_submission"]
    assert after == {**original, "stop": stop}
    assert len(await stop_rows()) == 1
    async with TestSession() as db:
        await db.execute(delete(PaymentEvent).where(PaymentEvent.kind == flow.STOPPED))
        await db.commit()
    assert (await state(client, admin, number))["snapshot"] != before["snapshot"]
    monkeypatch.setattr(settings, "WX_REFUND_SEND_ENABLED", True)
    calls = await signed_sender(monkeypatch, number, refund_case[3])
    assert (await post(client, admin, number, "send", send)).status_code == 409
    assert not calls and await refunds() == []
    assert (await client.post(f"/shop/download/{number}", headers=buyer)).status_code == 200


async def test_missing_authorization_and_cross_actor_or_order_replay_rejected(client, refund_case):
    _, admin, number, send, _ = await authorized(client, refund_case)
    body = stop_proof(send)
    assert (await post(client, admin, number, "stop", body)).status_code == 200
    other = await admin_headers(client, "second-stopper")
    assert (await post(client, other, number, "stop", body)).status_code == 409
    _, _, another, another_send, _ = await authorized(client, refund_case)
    assert (
        await post(client, admin, another, "stop", {**stop_proof(another_send), "request_id": body["request_id"]})
    ).status_code == 409
    _, _, unapproved, raw = await setup(client, refund_case)
    assert (
        await post(
            client,
            admin,
            unapproved,
            "stop",
            {**send, "confirm_order_no": unapproved, "out_refund_no": raw["out_refund_no"]},
        )
    ).status_code == 409
    assert len(await stop_rows()) == 1


@pytest.mark.parametrize(
    "change,status",
    [
        ({"amount": True}, 422),
        ({"amount": 1}, 409),
        ({"digest": "a" * 64}, 409),
        ({"out_refund_no": "CMR" + "f" * 32}, 409),
        ({"authorization_id": "f" * 32}, 409),
        ({"confirm_order_no": "OTHER"}, 409),
        ({"request_id": "wrong"}, 422),
        ({"evidence": "\x7fxx"}, 422),
        ({"resume": True}, 422),
    ],
)
async def test_invalid_stop_never_writes(client, refund_case, change, status):
    _, admin, number, send, _ = await authorized(client, refund_case)
    assert (await post(client, admin, number, "stop", {**stop_proof(send), **change})).status_code == status
    assert await stop_rows() == []


async def test_permissions_origin_revision_and_rate_limit(client, refund_case, monkeypatch):
    buyer, admin, number, send, _ = await authorized(client, refund_case)
    body = stop_proof(send)
    assert (await post(client, buyer, number, "stop", body)).status_code == 403
    cookie = await client.post("/auth/login", data={"username": "auditor", "password": "secret123"})
    assert cookie.status_code == 200
    assert (
        await client.post(
            f"/shop/admin/orders/{number}/refunds/stop", headers={"Origin": "https://evil.test"}, json=body
        )
    ).status_code == 403
    original = refunds_admin.lock_user

    async def revoke(db, uid):
        row = await original(db, uid)
        row.credential_version += 1
        return row

    with monkeypatch.context() as m:
        m.setattr(refunds_admin, "lock_user", revoke)
        assert (await post(client, admin, number, "stop", body)).status_code == 403
    monkeypatch.setattr(settings, "RATE_LIMIT_ENABLED", True)
    monkeypatch.setattr(settings, "RATE_LIMIT_TOOLS", 1)
    assert (await post(client, admin, number, "stop", body)).status_code == 200
    assert (await post(client, admin, number, "stop", body)).status_code == 429


@pytest.mark.parametrize("same", [True, False])
async def test_concurrent_stop_has_one_record(client, refund_case, same):
    _, admin, number, send, _ = await authorized(client, refund_case)
    first = stop_proof(send)
    results = await asyncio.gather(
        post(client, admin, number, "stop", first),
        post(client, admin, number, "stop", first if same else stop_proof(send)),
    )
    assert sorted(r.status_code for r in results) == ([200, 200] if same else [200, 409])
    assert len(await stop_rows()) == 1


@pytest.mark.parametrize("committed", [False, True])
async def test_unknown_commit_recovers_original_stop(client, refund_case, monkeypatch, committed):
    _, admin, number, send, _ = await authorized(client, refund_case)
    body = stop_proof(send)
    original = AsyncSession.commit

    async def broken(db):
        if committed:
            await original(db)
        else:
            await db.flush()
        raise OperationalError("commit", {}, RuntimeError("unknown"))

    with monkeypatch.context() as m:
        m.setattr(AsyncSession, "commit", broken)
        assert (await post(client, admin, number, "stop", body)).status_code == 503
    assert len(await stop_rows()) == int(committed)
    response = await post(client, admin, number, "stop", body)
    assert response.status_code == 200 and response.json()["changed"] is not committed
    assert len(await stop_rows()) == 1


async def test_stop_and_audit_are_atomic(client, refund_case):
    _, admin, number, send, _ = await authorized(client, refund_case)

    def fail(mapper, connection, obj):
        if obj.kind == flow.STOPPED:
            raise OperationalError("audit", {}, RuntimeError("broken"))

    event.listen(PaymentEvent, "before_insert", fail)
    try:
        assert (await post(client, admin, number, "stop", stop_proof(send))).status_code == 503
    finally:
        event.remove(PaymentEvent, "before_insert", fail)
    assert await stop_rows() == []


async def test_started_request_can_finish_after_stop_but_never_start_another(client, refund_case, monkeypatch):
    buyer, admin, number, send, _ = await authorized(client, refund_case)
    monkeypatch.setattr(settings, "WX_REFUND_SEND_ENABLED", True)
    entered, release = asyncio.Event(), asyncio.Event()

    async def handler(request):
        entered.set()
        await release.wait()
        raw = json.loads(request.content)
        return signed_response(
            response_body(number, raw["transaction_id"], refund_case[3], out_refund_no=raw["out_refund_no"])
        )

    calls = await signed_sender(monkeypatch, number, refund_case[3], handler=handler)
    pending = asyncio.create_task(post(client, admin, number, "send", send))
    try:
        await asyncio.wait_for(entered.wait(), 5)
        result = await asyncio.wait_for(post(client, admin, number, "stop", stop_proof(send)), 5)
        assert result.status_code == 200
    finally:
        release.set()
        result = await pending
    assert result.json()["attempt"]["state"] == "accepted"
    assert (await post(client, admin, number, "send", send)).status_code == 200
    assert (await post(client, admin, number, "send", {**send, "request_id": uuid.uuid4().hex})).status_code == 409
    assert len(calls) == 1 and await refunds() == []
    assert (await client.post(f"/shop/download/{number}", headers=buyer)).status_code == 200
    await provider(monkeypatch, number, refund_case[3], out_refund_no=send["out_refund_no"])
    assert (await query(client, admin, number, out_refund_no=send["out_refund_no"])).status_code == 200
    assert (await client.post(f"/shop/download/{number}", headers=buyer)).status_code == 403
    assert (await ledger(client, admin, number))["refund_submission"]["stop"]


async def test_stop_lock_wins_against_concurrent_send(client, refund_case, monkeypatch):
    _, admin, number, send, _ = await authorized(client, refund_case)
    monkeypatch.setattr(settings, "WX_REFUND_SEND_ENABLED", True)
    entered, release = asyncio.Event(), asyncio.Event()
    original = flow.lock_order
    first = True

    async def hold(db, order):
        nonlocal first
        await original(db, order)
        if first:
            first = False
            entered.set()
            await release.wait()

    calls = await signed_sender(monkeypatch, number, refund_case[3])
    monkeypatch.setattr(flow, "lock_order", hold)
    stop = asyncio.create_task(post(client, admin, number, "stop", stop_proof(send)))
    try:
        await asyncio.wait_for(entered.wait(), 5)
        sending = asyncio.create_task(post(client, admin, number, "send", send))
    finally:
        release.set()
    assert (await stop).status_code == 200
    assert (await sending).status_code == 409 and calls == []


def test_manifest_requires_stop_migration(tmp_path):
    for version, (path, _) in db_admin.migration_manifest().items():
        if int(version) < 14:
            (tmp_path / path.name).write_bytes(path.read_bytes())
    with pytest.raises(db_admin.MaintenanceError):
        db_admin.migration_manifest(tmp_path)


def test_real_pg_stop_upgrade_constraints_and_append_only(maintenance_db):
    conn, _ = maintenance_db
    db_admin.initialize(conn)
    rows(
        conn,
        "DROP TABLE refund_verification_job; DROP FUNCTION codemax_check_refund_verification_job(); DROP TABLE refund_send_stop; DROP FUNCTION codemax_check_refund_send_stop(); DELETE FROM schema_migration WHERE version IN ('0014','0015')",
    )
    rows(
        conn,
        "INSERT INTO sys_user(id,username,password,role,status) VALUES(1,'operator','synthetic',1,1); "
        "INSERT INTO sys_order(id,order_no,user_id,product_name,amount,status,payment_mode,merchant_id,app_id,transaction_id) VALUES(1,'ORDER',1,'test',100,'paid','wechat','M','A','TX'); "
        "INSERT INTO payment_receipt(order_id,source,transaction_id,amount,currency,merchant_id,app_id) VALUES(1,'wechat','TX',100,'CNY','M','A'); "
        "INSERT INTO refund_request(order_id,payment_receipt_id,request_id,out_refund_no,merchant_id,app_id,amount,currency,actor_id,actor_name,evidence) VALUES(1,1,repeat('a',32),'CMR'||repeat('b',32),'M','A',100,'CNY',1,'operator','synthetic')",
    )
    body = flow.canonical(
        {
            "transaction_id": "TX",
            "out_refund_no": "CMR" + "b" * 32,
            "amount": {"total": 100, "refund": 100, "currency": "CNY"},
            "reason": "cancel",
            "notify_url": "https://example.test/shop/refunds/notify",
        }
    )
    with conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO refund_authorization(preparation_id,request_id,actor_id,actor_name,evidence,body,digest) VALUES(1,%s,1,'operator','synthetic',%s,%s)",
            ("c" * 32, body, flow.digest(body)),
        )
    db_admin.migrate(conn)
    assert rows(conn, "SELECT * FROM refund_send_stop") == []
    sql = "INSERT INTO refund_send_stop(authorization_id,request_id,actor_id,actor_name,evidence) VALUES(1,repeat('d',32),1,'operator','synthetic stop')"
    for bad in [
        sql.replace("'operator'", "'foreign'"),
        sql.replace("VALUES(1,", "VALUES(99,"),
        sql.replace("repeat('d',32)", "'invalid'"),
        sql.replace("'synthetic stop'", "' '"),
    ]:
        with pytest.raises(psycopg2.Error):
            rows(conn, bad)
    rows(conn, sql)
    for bad in [sql, "UPDATE refund_send_stop SET evidence='rewrite'", "DELETE FROM refund_send_stop"]:
        with pytest.raises(psycopg2.Error):
            rows(conn, bad)
    assert rows(conn, "SELECT body FROM refund_authorization") == [(body,)]
    db_admin.migrate(conn)
