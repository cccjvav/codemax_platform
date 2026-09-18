"""Explicit queue control; signed GET completion remains independently administrator-confirmed."""
import asyncio
from datetime import datetime, timezone

import pytest
from sqlalchemy import select, update
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession

from app import refund_verification as queue
from app.config import settings
from app.models import PaymentEvent, RefundVerificationJob
from app.routers import refunds_admin
from tests.conftest import TestSession
from tests.test_payment_ledger import admin_headers
from tests.test_refund_notifications import plain, send
from tests.test_refund_verification import configuration as configuration
from tests.test_refund_verification import jobs, setup, stub
from tests.test_refunds import provider, query
from tests.test_refunds import refund_case as refund_case
from tests.test_wechat_pay import CFG


async def proof(client, admin, number, action='hold', key='a' * 32):
    data = (await client.get(f'/shop/admin/orders/{number}/ledger', headers=admin)).json()['refund_verification']
    job = data['jobs'][0]
    return {'job_id': job['id'], 'snapshot': job['snapshot'], 'request_id': key, 'confirm_order_no': number,
            'action': action, 'evidence': 'operator checked original notice'}


async def post(client, admin, number, body):
    return await client.post(f'/shop/admin/orders/{number}/refunds/verification/control', headers=admin, json=body)


async def test_hold_retry_preserves_budget_first_attribution_and_review(client, refund_case, monkeypatch):
    _, admin, _, _ = refund_case
    number, _ = await setup(client, refund_case)
    body = await proof(client, admin, number)
    before = (await client.get(f'/shop/admin/orders/{number}/ledger', headers=admin)).json()['review']['snapshot']
    async def forbidden(*a, **k):
        pytest.fail('queue controls never call the provider')
    monkeypatch.setattr(queue, 'query_full_refund', forbidden)
    saved = await post(client, admin, number, body)
    assert saved.status_code == 200 and saved.headers['cache-control'] == 'no-store'
    assert saved.json()['changed'] and (await jobs())[0].outcome == 'manual_hold'
    assert await queue.claim(TestSession) is None
    retry = await proof(client, admin, number, 'retry', 'b' * 32)
    assert (await post(client, admin, number, retry)).status_code == 200
    first = await post(client, admin, number, body)
    assert first.status_code == 200 and not first.json()['changed']
    assert first.json()['control'] == saved.json()['control']  # first action, not current state
    row = (await jobs())[0]
    assert (row.state, row.attempts) == ('retry', 0)
    ledger = (await client.get(f'/shop/admin/orders/{number}/ledger?before=1', headers=admin)).json()
    assert ledger['refund_verification']['latest_control']['request_id'] == 'b' * 32
    assert ledger['review']['snapshot'] != before and ledger['refund'] is None
    assert (await queue.claim(TestSession)).attempt == 1


@pytest.mark.parametrize('change,status', [({'action': 'clear'}, 422), ({'job_id': True}, 422),
    ({'job_id': 999999}, 409), ({'snapshot': '0' * 64}, 409), ({'evidence': 'bad\nline'}, 422),
    ({'reset_attempts': True}, 422), ({'confirm_order_no': 'OTHER'}, 409)])
async def test_strict_input_and_stale_proof(client, refund_case, change, status):
    _, admin, _, _ = refund_case
    number, _ = await setup(client, refund_case)
    body = await proof(client, admin, number)
    assert (await post(client, admin, number, {**body, **change})).status_code == status
    assert (await jobs())[0].state == 'pending'


async def test_actor_origin_revision_rate_and_reused_key(client, refund_case, monkeypatch):
    buyer, admin, _, _ = refund_case
    number, _ = await setup(client, refund_case)
    body = await proof(client, admin, number)
    assert (await post(client, buyer, number, body)).status_code == 403
    assert (await client.post('/auth/login', data={'username': 'auditor', 'password': 'secret123'})).status_code == 200
    assert (await client.post(f'/shop/admin/orders/{number}/refunds/verification/control',
                             headers={'Origin': 'https://evil.test'}, json=body)).status_code == 403
    original = refunds_admin.lock_user
    async def revoke(db, uid):
        row = await original(db, uid)
        row.credential_version += 1
        return row
    with monkeypatch.context() as m:
        m.setattr(refunds_admin, 'lock_user', revoke)
        assert (await post(client, admin, number, body)).status_code == 403
    assert (await post(client, admin, number, body)).status_code == 200
    assert (await post(client, admin, number, {**body, 'evidence': 'different reason'})).status_code == 409
    monkeypatch.setattr(settings, 'RATE_LIMIT_ENABLED', True)
    monkeypatch.setattr(settings, 'RATE_LIMIT_TOOLS', 1)
    assert (await post(client, admin, number, body)).status_code == 200
    assert (await post(client, admin, number, body)).status_code == 429


async def test_old_worker_fenced_and_claim_invalidates_snapshot(client, refund_case):
    _, admin, _, _ = refund_case
    number, _ = await setup(client, refund_case)
    stale = await proof(client, admin, number)
    ticket = await queue.claim(TestSession)
    assert (await post(client, admin, number, stale)).status_code == 409
    body = await proof(client, admin, number)
    assert (await post(client, admin, number, body)).status_code == 200
    assert not await queue.finish(TestSession, ticket, 'verified', 'success_needs_admin')
    assert (await jobs())[0].attempts == 1 and (await jobs())[0].token is None
    retry = await proof(client, admin, number, 'retry', 'b' * 32)
    assert (await post(client, admin, number, retry)).status_code == 200
    next_ticket = await queue.claim(TestSession)
    assert next_ticket.attempt == 2 and next_ticket.token != ticket.token
    assert not await queue.finish(TestSession, ticket, 'verified', 'success_needs_admin')
    assert await queue.finish(TestSession, next_ticket, 'retry', 'processing')


@pytest.mark.parametrize('state,attempts', [('attention', 8), ('verified', 1), ('retry', 1), ('pending', 0)])
async def test_retry_never_resets_exhausted_or_automatically_active_jobs(client, refund_case, state, attempts):
    _, admin, _, _ = refund_case
    number, _ = await setup(client, refund_case)
    async with TestSession() as db:
        await db.execute(update(RefundVerificationJob).values(state=state, attempts=attempts))
        await db.commit()
    body = await proof(client, admin, number, 'retry')
    assert (await post(client, admin, number, body)).status_code == 409
    assert (await jobs())[0].attempts == attempts


async def test_partial_can_hold_but_not_requeue(client, refund_case):
    _, admin, _, _ = refund_case
    number, _ = await setup(client, refund_case, amount={'total': 19900, 'refund': 1, 'payer_total': 19900, 'payer_refund': 1})
    assert (await post(client, admin, number, await proof(client, admin, number))).status_code == 200
    assert (await post(client, admin, number, await proof(client, admin, number, 'retry', 'b' * 32))).status_code == 409


@pytest.mark.parametrize('lost_ack', [False, True])
async def test_commit_ack_failure_retains_atomic_control(client, refund_case, monkeypatch, lost_ack):
    _, admin, _, _ = refund_case
    number, _ = await setup(client, refund_case)
    body = await proof(client, admin, number)
    original = AsyncSession.commit
    async def fail(db):
        if lost_ack:
            await original(db)
        raise OperationalError('synthetic', {}, Exception('lost acknowledgement'))
    with monkeypatch.context() as m:
        m.setattr(AsyncSession, 'commit', fail)
        assert (await post(client, admin, number, body)).status_code == 503
    row = (await jobs())[0]
    assert row.state == ('attention' if lost_ack else 'pending')
    async with TestSession() as db:
        events = list((await db.scalars(select(PaymentEvent).where(PaymentEvent.kind == queue.CONTROL_KIND))).all())
        assert len(events) == int(lost_ack)
    assert (await post(client, admin, number, body)).status_code == 200


async def test_same_snapshot_concurrent_controls_only_one_and_order_scope(client, refund_case):
    _, admin, _, _ = refund_case
    number, _ = await setup(client, refund_case)
    other = await refund_case[2]('wechat')
    assert (await send(client, await plain(other, refund_case[3]), identity='NOTICE-OTHER')).status_code == 204
    body = await proof(client, admin, number)
    assert (await post(client, admin, other, {**body, 'confirm_order_no': other})).status_code == 409
    a, b = await asyncio.gather(post(client, admin, number, body), post(client, admin, number, {**body, 'request_id': 'b' * 32}))
    assert sorted([a.status_code, b.status_code]) == [200, 409]


async def test_same_second_aba_invalidates_proof(client, refund_case, monkeypatch):
    _, admin, _, _ = refund_case
    number, _ = await setup(client, refund_case)
    async def fixed(db):
        return datetime(2026, 9, 18, tzinfo=timezone.utc)
    monkeypatch.setattr(queue, 'clock', fixed)
    for key, action in [('a', 'hold'), ('b', 'retry')]:
        assert (await post(client, admin, number, await proof(client, admin, number, action, key * 32))).status_code == 200
    stale = await proof(client, admin, number, 'hold', 'c' * 32)
    for key, action in [('d', 'hold'), ('e', 'retry')]:
        assert (await post(client, admin, number, await proof(client, admin, number, action, key * 32))).status_code == 200
    assert (await post(client, admin, number, stale)).status_code == 409


async def test_cross_actor_key_and_settled_job_cannot_retry(client, refund_case, monkeypatch):
    _, admin, _, _ = refund_case
    number, completed = await setup(client, refund_case)
    body = await proof(client, admin, number)
    assert (await post(client, admin, number, body)).status_code == 200
    other = await admin_headers(client, 'queue-second-admin')
    assert (await post(client, other, number, body)).status_code == 409
    await provider(monkeypatch, number, completed)
    assert (await query(client, admin, number)).status_code == 200
    assert (await post(client, admin, number, await proof(client, admin, number, 'retry', 'b' * 32))).status_code == 409


async def test_hold_during_get_does_not_recall_but_fences_result(client, refund_case, monkeypatch):
    _, admin, _, _ = refund_case
    number, completed = await setup(client, refund_case)
    calls = await stub(monkeypatch, number, completed)
    real = queue.query_full_refund
    entered, release = asyncio.Event(), asyncio.Event()
    async def held(*a, **k):
        entered.set()
        await release.wait()
        return await real(*a, **k)
    monkeypatch.setattr(queue, 'query_full_refund', held)
    task = asyncio.create_task(queue.run_once(TestSession, CFG, enabled=True))
    try:
        await asyncio.wait_for(entered.wait(), 5)
        assert (await post(client, admin, number, await proof(client, admin, number))).status_code == 200
    finally:
        release.set()
        await task
    assert len(calls) == 1 and (await jobs())[0].outcome == 'manual_hold'


async def test_audit_failure_rolls_back_job_update(client, refund_case, monkeypatch):
    _, admin, _, _ = refund_case
    number, _ = await setup(client, refund_case)
    body = await proof(client, admin, number)
    original = AsyncSession.add
    def fail(db, row, *a, **k):
        if isinstance(row, PaymentEvent) and row.kind == queue.CONTROL_KIND:
            raise OperationalError('synthetic', {}, Exception('audit unavailable'))
        return original(db, row, *a, **k)
    with monkeypatch.context() as m:
        m.setattr(AsyncSession, 'add', fail)
        assert (await post(client, admin, number, body)).status_code == 503
    assert (await jobs())[0].state == 'pending'
    assert (await post(client, admin, number, body)).status_code == 200
