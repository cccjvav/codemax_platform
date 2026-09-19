"""Money/rights boundaries with real local crypto, independent DB sessions and immutable file bytes."""
import asyncio
from datetime import datetime, timezone
from pathlib import Path

import psycopg2
import pytest
from sqlalchemy import event, select
from sqlalchemy.exc import IntegrityError

from app import db_admin, delivery
from app.config import settings
from app.models import Order, PaymentEvent, PaymentReceipt, User
from app.payment_ledger import PaymentConflict, settle
from app.storage import StorageError, build_storage
from tests.conftest import PRODUCT_BYTES, PRODUCT_KEY, TestSession, engine
from tests.test_db_admin import isolated_pg as isolated_pg
from tests.test_db_admin import legacy_0016, rows
from tests.test_db_admin import maintenance_db as maintenance_db
from tests.test_download import auth_headers, path_of
from tests.test_wechat_notify import build_notify, fetch, make_order, post_notify, txn
from tests.test_wechat_notify import notify_ready as notify_ready
from tests.test_wechat_pay import CFG


async def admin_headers(client, name='auditor'):
    headers = await auth_headers(client, name)
    async with TestSession() as db:
        user = await db.scalar(select(User).where(User.username == name))
        user.role = 1
        await db.commit()
    return headers


async def receipts():
    async with TestSession() as db:
        return list((await db.scalars(select(PaymentReceipt))).all())


async def test_signed_transaction_has_exactly_one_order_owner(client, notify_ready):
    first, second = await make_order(), await make_order()
    assert (await post_notify(client, *build_notify(txn(first, txid='shared')))).status_code == 200
    assert (await post_notify(client, *build_notify(txn(second, txid='shared')))).status_code == 409
    assert (await fetch(second)).status == 'pending'
    assert len(await receipts()) == 1


async def test_different_transaction_for_paid_order_is_conflict(client, notify_ready):
    no = await make_order()
    assert (await post_notify(client, *build_notify(txn(no, txid='first')))).status_code == 200
    async with TestSession() as db:
        order = await db.scalar(select(Order).where(Order.order_no == no))
        order.update_time = datetime(2000, 1, 1, tzinfo=timezone.utc)
        await db.commit()
    assert (await post_notify(client, *build_notify(txn(no, txid='other')))).status_code == 409
    assert (await post_notify(client, *build_notify(txn(no, txid='first')))).status_code == 200
    assert (await fetch(no)).update_time.year == 2000  # no-op lock must not invoke ORM onupdate
    assert (await receipts())[0].transaction_id == 'first'
    assert len(await receipts()) == 1


async def test_independent_connections_race_for_same_transaction(client):
    numbers = [await make_order(), await make_order()]
    async def pay(no):
        async with TestSession() as db:
            order = await db.scalar(select(Order).where(Order.order_no == no))
            try:
                return await settle(db, order, source='wechat', transaction_id='one-transaction',
                                    merchant_id='1900000109', app_id='wxAPPID')
            except PaymentConflict:
                return False
    assert sorted(await asyncio.gather(*(pay(no) for no in numbers))) == [False, True]
    assert len(await receipts()) == 1
    assert sorted([(await fetch(no)).status for no in numbers]) == ['paid', 'pending']


async def test_settlement_commit_failure_rolls_back_both_records(client, monkeypatch):
    no = await make_order()
    async with TestSession() as db:
        order = await db.scalar(select(Order).where(Order.order_no == no))
        original_commit = db.commit
        async def broken_commit():
            await db.flush()
            if await db.scalar(select(PaymentReceipt.id)) is not None:
                raise RuntimeError('synthetic commit failure after receipt flush')
            await original_commit()  # catches a mutant that prematurely commits paid before adding receipt

        monkeypatch.setattr(db, 'commit', broken_commit)
        with pytest.raises(RuntimeError):
            await settle(db, order, source='wechat', transaction_id='failure',
                         merchant_id='1900000109', app_id='wxAPPID')
    assert (await fetch(no)).status == 'pending'
    assert await receipts() == []


async def test_unique_reference_is_a_database_constraint(client):
    numbers = [await make_order(), await make_order()]
    async with TestSession() as db:
        orders = list((await db.scalars(select(Order).where(Order.order_no.in_(numbers)))).all())
        for order in orders:
            db.add(PaymentReceipt(order_id=order.id, source='wechat', transaction_id='duplicate',
                                  amount=order.amount, currency='CNY'))
        with pytest.raises(IntegrityError):
            await db.commit()
        await db.rollback()
    assert await receipts() == []


@pytest.mark.parametrize('endpoint', ['mock', 'manual', 'wechat'])
async def test_cannot_confirm_order_through_a_different_channel(client, mock_mode, monkeypatch, notify_ready, endpoint):
    buyer = await auth_headers(client)
    no = (await client.post('/shop/orders', headers=buyer)).json()['order_no']
    if endpoint == 'mock':
        async with TestSession() as db:
            order = await db.scalar(select(Order).where(Order.order_no == no))
            order.payment_mode = 'manual'  # synthetic malformed contract, not production SQL mutation
            await db.commit()
        response = await client.post('/shop/mock-pay/confirm', headers=buyer, json={'order_no': no})
    elif endpoint == 'manual':
        monkeypatch.setattr(settings, 'SHOP_PAY_MODE', 'manual')
        admin = await admin_headers(client)
        response = await client.post(f'/shop/orders/{no}/confirm', headers=admin,
                                     json={'reference': 'bank-1', 'amount': settings.SHOP_PRODUCT_AMOUNT, 'evidence': 'test statement'})
    else:
        response = await post_notify(client, *build_notify(txn(no)))
    assert response.status_code == 409
    assert (await fetch(no)).status == 'pending'
    assert await receipts() == []


async def test_manual_proof_is_required_and_first_actor_survives_retry(client, monkeypatch):
    monkeypatch.setattr(settings, 'SHOP_PAY_MODE', 'manual')
    buyer = await auth_headers(client)
    no = (await client.post('/shop/orders', headers=buyer)).json()['order_no']
    first, second = await admin_headers(client, 'first'), await admin_headers(client, 'second')
    url = f'/shop/orders/{no}/confirm'
    assert (await client.post(url, headers=first)).status_code == 422
    proof = {'reference': 'statement-row-001', 'amount': settings.SHOP_PRODUCT_AMOUNT, 'evidence': 'verified synthetic bank entry'}
    assert (await client.post(url, headers=first, json={**proof, 'amount': 1})).status_code == 409
    assert (await client.post(url, headers=first, json={**proof, 'amount': True})).status_code == 422
    assert (await client.post(url, headers=first, json=proof)).json()['changed'] is True
    again = await client.post(url, headers=second, json=proof)
    assert again.json()['changed'] is False and again.json()['confirmed_by'] == 'first'
    stored = await receipts()
    assert len(stored) == 1 and stored[0].evidence == proof['evidence'] and stored[0].actor_name == 'first'
    assert (await client.get(f'/shop/admin/orders/{no}/ledger', headers=buyer)).status_code == 403
    writes = []
    def record_write(conn, cursor, statement, parameters, context, executemany):
        if statement.lstrip().upper().startswith(('INSERT', 'UPDATE', 'DELETE')):
            writes.append(statement)
    event.listen(engine.sync_engine, 'before_cursor_execute', record_write)
    try:
        ledger = await client.get(f'/shop/admin/orders/{no}/ledger', headers=first)
    finally:
        event.remove(engine.sync_engine, 'before_cursor_execute', record_write)
    assert ledger.json()['receipt']['reference'] == proof['reference']
    assert ledger.headers['cache-control'] == 'no-store' and writes == []


async def test_prepay_unknown_and_retry_are_durable_attempts(client, monkeypatch):
    from app.routers import shop
    from app.wechat_pay import WeChatPayError
    monkeypatch.setattr(settings, 'SHOP_PAY_MODE', 'wechat')
    monkeypatch.setattr(shop, 'pay_config', lambda: CFG)
    buyer = await auth_headers(client)
    calls = []
    async def provider(cfg, **kwargs):
        async with TestSession() as independent:
            attempt = await independent.scalar(select(PaymentEvent).where(PaymentEvent.kind == 'prepay_started'))
            assert attempt is not None  # must be committed before any external side effect
        calls.append(kwargs['out_trade_no'])
        if len(calls) == 1:
            raise WeChatPayError('synthetic secret body must not be journaled')
        return 'weixin://test-qr'
    monkeypatch.setattr(shop, 'native_prepay', provider)
    assert (await client.post('/shop/orders', headers=buyer)).status_code == 502
    assert (await client.post('/shop/orders', headers=buyer)).status_code == 200
    assert calls[0] == calls[1]
    async with TestSession() as db:
        events = list((await db.scalars(select(PaymentEvent).order_by(PaymentEvent.id))).all())
        assert [e.kind for e in events] == ['prepay_started', 'prepay_unknown', 'prepay_started', 'prepay_ready']
        assert events[0].attempt_id == events[1].attempt_id != events[2].attempt_id == events[3].attempt_id
        assert all(e.evidence is None for e in events)


async def test_order_snapshot_survives_config_and_source_changes(client, mock_mode, product, monkeypatch):
    buyer = await auth_headers(client)
    no = (await client.post('/shop/orders', headers=buyer)).json()['order_no']
    assert (await client.post('/shop/mock-pay/confirm', headers=buyer, json={'order_no': no})).status_code == 200
    product.joinpath(PRODUCT_KEY).write_bytes(b'new version')
    monkeypatch.setattr(settings, 'STORAGE_PRODUCT_KEY', 'another-product.zip')
    monkeypatch.setattr(settings, 'SHOP_PAY_MODE', 'manual')
    assert (await client.get(f'/shop/orders/{no}', headers=buyer)).json()['pay_mode'] == 'mock'
    for _ in range(2):
        result = await client.post(f'/shop/download/{no}', headers=buyer)
        assert result.status_code == 200
        response = await client.get(path_of(result.json()['download_url']))
        assert response.content == PRODUCT_BYTES


async def test_missing_source_refuses_checkout_without_order(client, mock_mode, product):
    product.joinpath(PRODUCT_KEY).unlink()
    buyer = await auth_headers(client)
    assert (await client.post('/shop/orders', headers=buyer)).status_code == 503
    async with TestSession() as db:
        assert list((await db.scalars(select(Order))).all()) == []


async def test_tampered_snapshot_blocks_issued_link_and_preserves_rights(client, mock_mode, product):
    buyer = await auth_headers(client)
    no = (await client.post('/shop/orders', headers=buyer)).json()['order_no']
    await client.post('/shop/mock-pay/confirm', headers=buyer, json={'order_no': no})
    link = (await client.post(f'/shop/download/{no}', headers=buyer)).json()['download_url']
    key = (await fetch(no)).delivery_key
    for alias in (key, f'unused/../{key}'):
        with pytest.raises(StorageError):
            build_storage('http://test').put(alias, b'bad')
    product.joinpath(key).write_bytes(b'tampered')  # malicious/accidental operator disk write
    assert (await client.get(path_of(link))).status_code == 409
    assert (await client.post(f'/shop/download/{no}', headers=buyer)).status_code == 409
    product.joinpath(key).write_bytes(PRODUCT_BYTES)  # explicit byte-identical backup restoration
    assert (await client.post(f'/shop/download/{no}', headers=buyer)).status_code == 200


async def test_legacy_paid_order_needs_explicit_one_time_binding(client, monkeypatch):
    monkeypatch.setattr(settings, "SHOP_PAY_MODE", "manual")
    buyer = await auth_headers(client)
    async with TestSession() as db:
        user = await db.scalar(select(User).where(User.username == 'buyer'))
        db.add(Order(order_no='legacy-paid', user_id=user.id, product_name='historical', amount=100, status='paid'))
        await db.commit()
    assert (await client.post('/shop/download/legacy-paid', headers=buyer)).status_code == 409
    admin = await admin_headers(client)
    proof = {'payment_mode': 'manual', 'source_key': PRODUCT_KEY, 'evidence': 'verified original historical deliverable'}
    url = '/shop/orders/legacy-paid/legacy-binding'
    assert (await client.post(url, headers=buyer, json=proof)).status_code == 403
    assert (await client.post(url, headers=admin, json=proof)).status_code == 200
    assert (await client.post(url, headers=admin, json=proof)).status_code == 409
    assert (await client.post('/shop/download/legacy-paid', headers=buyer)).status_code == 200
    assert (await client.post('/shop/orders/legacy-paid/confirm', headers=admin,
                              json={'reference': 'old', 'amount': 100, 'evidence': 'historical'})).status_code == 409
    assert await receipts() == []  # no invented retrospective revenue
    ledger = (await client.get('/shop/admin/orders/legacy-paid/ledger', headers=admin)).json()
    assert ledger['events'][0]['kind'] == 'legacy_bound'
    assert ledger['events'][0]['evidence'] == proof['evidence']


async def test_pending_order_cannot_silently_switch_channel(client, mock_mode, monkeypatch):
    buyer = await auth_headers(client)
    no = (await client.post('/shop/orders', headers=buyer)).json()['order_no']
    monkeypatch.setattr(settings, 'SHOP_PAY_MODE', 'manual')
    assert (await client.post('/shop/orders', headers=buyer)).status_code == 409
    assert (await fetch(no)).payment_mode == 'mock'


def test_snapshot_copy_has_size_and_admission_bounds(product, monkeypatch):
    storage = build_storage('http://test')
    monkeypatch.setattr(delivery, 'MAX_PRODUCT_BYTES', 1)
    with pytest.raises(StorageError):
        delivery.snapshot_product(storage, PRODUCT_KEY)
    delivery._COPY_SLOTS.acquire()
    delivery._COPY_SLOTS.acquire()
    try:
        with pytest.raises(StorageError, match='繁忙'):
            delivery.snapshot_product(storage, PRODUCT_KEY)
    finally:
        delivery._COPY_SLOTS.release()
        delivery._COPY_SLOTS.release()
    assert not list(Path(product).glob('.snapshots/tmp*'))


@pytest.mark.parametrize('sql', ['BEGIN; SELECT 1', 'SELECT 1;COMMIT;', '-- comment\nSTART TRANSACTION;',
                                 '/* outer /* inner */ x */ END;', 'PREPARE TRANSACTION \'x\';',
                                 "SELECT '{}'::jsonb #>> '{}'; ROLLBACK;"])
def test_transaction_guard_rejects_real_wrappers(sql):
    assert db_admin.has_transaction_control(sql)


@pytest.mark.parametrize('sql', ["CREATE FUNCTION x() RETURNS void LANGUAGE plpgsql AS $$ BEGIN RETURN; END $$;",
                                 "SELECT 'COMMIT; BEGIN'; -- ROLLBACK", 'SELECT "COMMIT";',
                                 "SELECT E'foo\\\';COMMIT';", 'SELECT 1 /* COMMIT; */;'])
def test_transaction_guard_accepts_quoted_bodies(sql):
    assert not db_admin.has_transaction_control(sql)


def test_pg_upgrade_preserves_history_and_enforces_immutable_evidence(maintenance_db):
    conn, _ = maintenance_db
    db_admin.initialize(conn)
    legacy_0016(conn)
    rows(conn, 'DROP TRIGGER check_system_refund_actor ON refund_receipt; DROP FUNCTION codemax_check_system_refund_actor(); ALTER TABLE refund_receipt DROP CONSTRAINT ck_refund_authority; ALTER TABLE refund_receipt DROP COLUMN verification_event_id; ALTER TABLE refund_receipt ALTER COLUMN actor_id SET NOT NULL; DELETE FROM schema_migration WHERE version::integer=16; DROP TABLE refund_verification_job; DROP FUNCTION codemax_check_refund_verification_job(); DROP TABLE refund_send_stop; DROP FUNCTION codemax_check_refund_send_stop(); DROP TABLE refund_authorization; DROP FUNCTION codemax_check_refund_authorization(); DROP TABLE refund_request; DROP FUNCTION codemax_check_refund_request(); DROP TABLE refund_receipt; DROP FUNCTION codemax_check_full_refund(); DROP TABLE payment_event; DROP TABLE payment_receipt; '
               'DROP FUNCTION codemax_freeze_order_contract() CASCADE; DROP FUNCTION codemax_append_only_evidence() CASCADE; '
               "DELETE FROM schema_migration WHERE version IN ('0010','0011','0012','0013','0014','0015')")
    for column in ('payment_mode', 'merchant_id', 'app_id', 'currency', 'delivery_key', 'delivery_digest', 'delivery_size'):
        rows(conn, f'ALTER TABLE sys_order DROP COLUMN {column}')
    rows(conn, "INSERT INTO sys_user(username,password) VALUES ('old','hash'); "
               "INSERT INTO sys_order(order_no,user_id,product_name,amount,status,transaction_id) "
               "SELECT 'old-paid',id,'old product',100,'paid','unverified-reference' FROM sys_user")
    db_admin.migrate(conn)
    assert rows(conn, 'SELECT status,transaction_id,payment_mode,delivery_key FROM sys_order') == [('paid', 'unverified-reference', None, None)]
    assert rows(conn, 'SELECT * FROM payment_receipt') == []
    with pytest.raises(psycopg2.Error):
        rows(conn, 'UPDATE sys_order SET amount=1')
    rows(conn, "INSERT INTO payment_receipt(order_id,source,transaction_id,amount,currency) "
               "SELECT id,'mock','synthetic-proof',100,'CNY' FROM sys_order")
    for sql in ["UPDATE payment_receipt SET amount=2", 'DELETE FROM payment_receipt']:
        with pytest.raises(psycopg2.Error):
            rows(conn, sql)
    assert rows(conn, 'SELECT amount FROM payment_receipt') == [(100,)]
    db_admin.migrate(conn)
    assert db_admin.status(conn) == []


@pytest.mark.parametrize('text', ["SELECT 'unterminated", '/* open', 'DO $body$ BEGIN'])
def test_transaction_guard_rejects_unterminated_regions(text):
    with pytest.raises(db_admin.MaintenanceError):
        db_admin.has_transaction_control(text)


@pytest.mark.parametrize('value', [None, '2026-09-01', 'invalid', '2026-09-01T00:00:00'])
async def test_callback_needs_provider_time_with_timezone(client, notify_ready, value):
    no = await make_order()
    data = {**txn(no), 'success_time': value}
    assert (await post_notify(client, *build_notify(data))).status_code == 400
    assert await receipts() == []


def test_pg_contract_and_event_are_not_overwritable(maintenance_db):
    conn, _ = maintenance_db
    db_admin.initialize(conn)
    rows(conn, "INSERT INTO sys_user(username,password) VALUES ('buyer','hash'); "
               "INSERT INTO sys_order(order_no,user_id,product_name,amount,payment_mode,delivery_key) "
               "SELECT 'immutable',id,'product',100,'mock','snapshot' FROM sys_user; "
               "INSERT INTO payment_event(order_id,attempt_id,kind) SELECT id,'attempt','prepay_started' FROM sys_order")
    for sql in ["UPDATE sys_order SET payment_mode='manual'", "UPDATE sys_order SET delivery_key='another'",
                "UPDATE payment_event SET kind='prepay_ready'", 'DELETE FROM payment_event']:
        with pytest.raises(psycopg2.Error):
            rows(conn, sql)
    assert rows(conn, 'SELECT payment_mode,delivery_key FROM sys_order') == [('mock', 'snapshot')]
    assert rows(conn, 'SELECT kind FROM payment_event') == [('prepay_started',)]
