"""Operator review is versioned workflow evidence, not money; test late facts and lost responses."""
import asyncio
import json
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Order, PaymentEvent, User
from app.payment_review import REVIEW_KIND, decode_review, review_payload, review_states
from tests.conftest import TestSession, engine
from tests.test_payment_ledger import admin_headers, receipts
from tests.test_payments_admin import events
from tests.test_wechat_notify import fetch, make_order


async def add_event(number, kind='query_unknown', *, identity=None, attempt=None, at=None):
    async with TestSession() as db:
        order_id = await db.scalar(select(Order.id).where(Order.order_no == number))
        row = PaymentEvent(order_id=order_id, attempt_id=attempt or uuid.uuid4().hex, kind=kind)
        if identity is not None:
            row.id = identity
        if at is not None:
            row.create_time = at
        db.add(row)
        await db.commit()
        return row


async def state(client, headers, number):
    response = await client.get(f'/shop/admin/orders/{number}/ledger', headers=headers)
    assert response.status_code == 200
    return response.json()['review']


async def payload(client, headers, number, action='close'):
    view = await state(client, headers, number)
    return {'action': action, 'snapshot': view['snapshot'], 'expected_version': view['version'],
            'evidence': '已联系客户核对记录，本轮仅记录复核', 'request_id': uuid.uuid4().hex, 'confirm_order_no': number}


async def write_review(client, headers, number, proof):
    return await client.post(f'/shop/admin/orders/{number}/review', headers=headers, json=proof)


@pytest.fixture
async def review_case(client):
    headers, number = await admin_headers(client), await make_order()
    await add_event(number)
    return headers, number


async def test_followup_close_reopen_history_never_changes_money(client, review_case):
    headers, number = review_case
    order_before = await fetch(number)
    for action, expected in [('followup', 'followup'), ('close', 'reviewed'), ('reopen', 'open')]:
        response = await write_review(client, headers, number, await payload(client, headers, number, action))
        assert response.status_code == 200 and not response.json()['replayed']
        view = await state(client, headers, number)
        assert view['state'] == expected and view['actor'] == 'auditor'
    assert await receipts() == []
    after = await fetch(number)
    assert (after.status, after.update_time, after.transaction_id) == (order_before.status, order_before.update_time, None)
    trail = [e for e in await events(number) if e.kind == REVIEW_KIND]
    assert [decode_review(e)['action'] for e in trail] == ['followup', 'close', 'reopen']


async def test_new_visible_lower_id_event_invalidates_close_even_when_max_is_unchanged(client, review_case):
    headers, number = review_case
    await add_event(number, identity=10002)
    proof = await payload(client, headers, number)
    assert (await write_review(client, headers, number, proof)).status_code == 200
    assert (await state(client, headers, number))['state'] == 'reviewed'
    # Models a transaction that reserved a lower sequence value but committed later.
    await add_event(number, identity=10001)
    view = await state(client, headers, number)
    assert view['state'] == 'open' and view['new_facts'] and view['snapshot'] != proof['snapshot']


async def test_new_facts_reject_stale_submission_and_retry_can_refresh(client, review_case):
    headers, number = review_case
    proof = await payload(client, headers, number)
    await add_event(number, 'query_refund')
    assert (await write_review(client, headers, number, proof)).status_code == 409
    assert not any(e.kind == REVIEW_KIND for e in await events(number))
    assert (await write_review(client, headers, number, await payload(client, headers, number))).status_code == 200
    assert (await fetch(number)).status == 'pending' and await receipts() == []


async def test_same_facts_competing_reviewers_cannot_overwrite(client, review_case):
    headers, number = review_case
    other = await admin_headers(client, 'second-reviewer')
    first, second = await payload(client, headers, number), await payload(client, other, number, 'followup')
    responses = await asyncio.gather(write_review(client, headers, number, first), write_review(client, other, number, second))
    assert sorted(r.status_code for r in responses) == [200, 409]
    assert len([e for e in await events(number) if e.kind == REVIEW_KIND]) == 1


async def test_exact_retry_retains_actor_and_cannot_change_payload_or_owner(client, review_case):
    headers, number = review_case
    proof = await payload(client, headers, number)
    first = await write_review(client, headers, number, proof)
    await add_event(number)
    repeated = await write_review(client, headers, number, proof)
    assert repeated.status_code == 200 and repeated.json()['replayed']
    assert first.json()['review_id'] == repeated.json()['review_id']
    assert (await state(client, headers, number))['state'] == 'open'
    assert (await write_review(client, headers, number, {**proof, 'evidence': 'different evidence'})).status_code == 409
    other = await admin_headers(client, 'other-admin')
    assert (await write_review(client, other, number, proof)).status_code == 409
    different = await make_order()
    assert (await write_review(client, headers, different, {**proof, 'confirm_order_no': different})).status_code == 409


async def test_orphan_grace_and_matching_terminal_records(client, review_case):
    headers, number = review_case
    now = datetime.now(timezone.utc)
    start = await add_event(number, 'prepay_started', at=now)
    async with TestSession() as db:
        order = await db.scalar(select(Order).where(Order.order_no == number))
        young = (await review_states(db, [order], now=now + timedelta(seconds=59)))[order.id]
        old = (await review_states(db, [order], now=now + timedelta(seconds=61)))[order.id]
    assert young['orphans'] == 0 and old['orphans'] == 1 and young['snapshot'] != old['snapshot']
    # A terminal of the wrong protocol must not swallow an unfinished prepay.
    await add_event(number, 'query_success', attempt=start.attempt_id)
    async with TestSession() as db:
        order = await db.scalar(select(Order).where(Order.order_no == number))
        assert (await review_states(db, [order], now=now + timedelta(seconds=61)))[order.id]['orphans'] == 1
    await add_event(number, 'prepay_ready', attempt=start.attempt_id)
    async with TestSession() as db:
        order = await db.scalar(select(Order).where(Order.order_no == number))
        assert (await review_states(db, [order], now=now + timedelta(seconds=61)))[order.id]['orphans'] == 0


async def test_orphan_discovered_by_queue_without_writing_a_scan_cursor(client):
    headers, number = await admin_headers(client), await make_order()
    await add_event(number, 'query_started', at=datetime(2000, 1, 1, tzinfo=timezone.utc))
    statements = []
    def capture(conn, cursor, statement, params, context, many):
        statements.append(statement.lstrip().split()[0].upper())
    event.listen(engine.sync_engine, 'before_cursor_execute', capture)
    try:
        result = await client.get('/shop/admin/orders?bucket=needs_review', headers=headers)
        view = await state(client, headers, number)
    finally:
        event.remove(engine.sync_engine, 'before_cursor_execute', capture)
    assert result.status_code == 200 and result.headers['cache-control'] == 'no-store'
    assert result.json()['orders'][0]['order_no'] == number and view['orphans'] == 1
    assert set(statements) <= {'SELECT'} and len(await events(number)) == 1


async def test_new_payment_reopens_review_without_revoking_rights(client, review_case):
    from app.payment_ledger import settle
    headers, number = review_case
    assert (await write_review(client, headers, number, await payload(client, headers, number))).status_code == 200
    async with TestSession() as db:
        order = await db.scalar(select(Order).where(Order.order_no == number))
        await settle(db, order, source='wechat', transaction_id='PAID', merchant_id='1900000109', app_id='wxAPPID')
    assert (await state(client, headers, number))['state'] == 'open'
    assert len(await receipts()) == 1 and (await fetch(number)).status == 'paid'


@pytest.mark.parametrize('field,value', [('evidence', ' ' * 4), ('evidence', 'x'*161), ('evidence', 'bad\nline'),
    ('snapshot', 'bad'), ('request_id', 'bad'), ('expected_version', True), ('expected_version', -1),
    ('action', 'refund'), ('confirm_order_no', '../BAD')])
async def test_review_input_rejected_without_journal_entry(client, review_case, field, value):
    headers, number = review_case
    proof = await payload(client, headers, number)
    proof[field] = value
    assert (await write_review(client, headers, number, proof)).status_code == 422
    assert len(await events(number)) == 1


async def test_only_admin_and_same_origin_can_review(client, review_case):
    from tests.test_download import auth_headers
    headers, number = review_case
    proof = await payload(client, headers, number)
    assert (await write_review(client, {'Origin': 'https://foreign.invalid'}, number, proof)).status_code == 403
    basic = await auth_headers(client, 'ordinary')
    assert (await write_review(client, basic, number, proof)).status_code == 403
    client.cookies.clear()
    assert (await write_review(client, {}, number, proof)).status_code == 401
    assert len(await events(number)) == 1


async def test_failed_commit_does_not_complete_review(client, review_case, monkeypatch):
    headers, number = review_case
    proof = await payload(client, headers, number)
    async def broken(db):
        await db.flush()
        raise RuntimeError('commit fault after flush')
    monkeypatch.setattr(AsyncSession, 'commit', broken)
    with pytest.raises(RuntimeError, match='commit fault'):
        await write_review(client, headers, number, proof)
    assert (await state(client, headers, number))['state'] == 'open'
    assert len(await events(number)) == 1


@pytest.mark.parametrize('raw', ['bad', '[]', '{"v":2}', '{"v":1,"action":"close"}'])
async def test_malformed_review_never_hides_a_case(client, review_case, raw):
    headers, number = review_case
    async with TestSession() as db:
        oid = await db.scalar(select(Order.id).where(Order.order_no == number))
        db.add(PaymentEvent(order_id=oid, attempt_id=uuid.uuid4().hex, kind=REVIEW_KIND, evidence=raw))
        await db.commit()
    view = await state(client, headers, number)
    assert view['state'] == 'open' and view['new_facts'] and view['note'] is None


async def test_bounded_filtered_pages_can_be_empty_with_a_continuation(client, review_case):
    headers, number = review_case
    async with TestSession() as db:
        parent = await db.scalar(select(Order).where(Order.order_no == number))
        actor = await db.scalar(select(User).where(User.username == 'auditor'))
        orders = [Order(order_no=f'FILTER{i}', user_id=parent.user_id, product_name='original', amount=1,
                        payment_mode='manual', status='closed') for i in range(205)]
        db.add_all(orders)
        await db.flush()
        for offset in range(0, len(orders), 50):
            group = orders[offset:offset+50]
            states = await review_states(db, group)
            for order in group:
                db.add(PaymentEvent(order_id=order.id, attempt_id=uuid.uuid4().hex, kind=REVIEW_KIND,
                                    actor_id=actor.id, actor_name=actor.username, evidence=review_payload('close', states[order.id]['snapshot'], 0, 'fixture reviewed')))
        await db.commit()
    page = (await client.get('/shop/admin/orders?bucket=needs_review', headers=headers)).json()
    assert page['orders'] == [] and page['next_cursor'] is not None
    next_page = (await client.get(f"/shop/admin/orders?bucket=needs_review&before={page['next_cursor']}", headers=headers)).json()
    assert [r['order_no'] for r in next_page['orders']] == [number] and next_page['next_cursor'] is None
    done = (await client.get('/shop/admin/orders?bucket=reviewed', headers=headers)).json()
    assert len(done['orders']) == 50 and done['next_cursor'] is not None


async def test_manual_followup_can_open_an_order_without_a_provider_exception(client):
    headers, number = await admin_headers(client), await make_order()
    assert (await state(client, headers, number))['state'] == 'none'
    assert (await write_review(client, headers, number, await payload(client, headers, number))).status_code == 409
    assert (await write_review(client, headers, number, await payload(client, headers, number, 'followup'))).status_code == 200
    assert (await client.get('/shop/admin/orders?bucket=needs_review', headers=headers)).json()['orders'][0]['order_no'] == number


def test_review_envelope_handles_escaped_notes_without_overflow():
    text = review_payload('followup', 'a'*64, 9223372036854775807, '"\\'*80)
    assert len(text) <= 500 and json.loads(text)['note'] == '"\\'*80


async def test_review_without_attribution_cannot_hide_a_case(client, review_case):
    headers, number = review_case
    before = await state(client, headers, number)
    async with TestSession() as db:
        oid = await db.scalar(select(Order.id).where(Order.order_no == number))
        db.add(PaymentEvent(order_id=oid, attempt_id=uuid.uuid4().hex, kind=REVIEW_KIND,
                            evidence=review_payload('close', before['snapshot'], 0, 'no actor attribution')))
        await db.commit()
    after = await state(client, headers, number)
    assert after['state'] == 'open' and after['new_facts']


async def test_candidate_completed_between_reads_is_not_returned_as_pending(client, monkeypatch):
    from app.routers import payments_admin
    headers, number = await admin_headers(client), await make_order()
    start = await add_event(number, 'prepay_started', at=datetime(2000, 1, 1, tzinfo=timezone.utc))
    original = payments_admin.review_states
    async def completed(db, orders, **kw):
        await add_event(number, 'prepay_ready', attempt=start.attempt_id)
        return await original(db, orders, **kw)
    monkeypatch.setattr(payments_admin, 'review_states', completed)
    response = await client.get('/shop/admin/orders?bucket=needs_review', headers=headers)
    assert response.status_code == 200 and response.json() == {'orders': [], 'next_cursor': None}
