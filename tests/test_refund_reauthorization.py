"""Stopped, NEVER-started authorization revisions; synthetic HTTP and disposable databases only."""
import asyncio
import uuid

import psycopg2
import pytest
from sqlalchemy import delete, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession

from app import db_admin
from app import refund_submissions as service
from app.config import settings
from app.models import Order, PaymentEvent, RefundAuthorization
from tests.conftest import TestSession
from tests.test_db_admin import isolated_pg as isolated_pg
from tests.test_db_admin import legacy_0016, rows
from tests.test_db_admin import maintenance_db as maintenance_db
from tests.test_payment_review import state
from tests.test_refund_requests import ledger
from tests.test_refund_stops import stop_proof
from tests.test_refund_submissions import authorized, post, signed_sender
from tests.test_refund_submissions import isolated_sender as isolated_sender
from tests.test_refunds import refund_case as refund_case


async def setup(client, case, *, stop=True):
    buyer, admin, number, send, original = await authorized(client, case)
    stopped = stop_proof(send)
    if stop:
        assert (await post(client, admin, number, 'stop', stopped)).status_code == 200
    body = {**send, 'request_id': uuid.uuid4().hex, 'reason': '更正后的客户可见原因', 'evidence': '核对未发送后重新授权'}
    return buyer, admin, number, send, stopped, body, original


async def versions():
    async with TestSession() as db:
        return list((await db.scalars(select(RefundAuthorization).order_by(RefundAuthorization.id))).all())


async def test_reauthorization_preserves_history_reference_and_requires_separate_send(client, refund_case, monkeypatch):
    buyer, admin, number, old_send, stop, body, original = await setup(client, refund_case)
    before = await state(client, admin, number)
    monkeypatch.setattr(settings, 'SITE_BASE_URL', 'https://corrected.example')
    result = await post(client, admin, number, 'reauthorize', body)
    assert result.status_code == 200 and result.headers['cache-control'] == 'no-store'
    data = result.json()
    new = data['authorization']
    assert data['changed'] and new['authorization_id'] == body['request_id']
    assert new['supersedes_authorization_id'] == original['authorization_id']
    assert new['body'] == {**original['body'], 'reason': body['reason'], 'notify_url': 'https://corrected.example/shop/refunds/notify'}
    saved = await versions()
    assert len(saved) == 2 and saved[1].supersedes_id == saved[0].id
    assert saved[0].body == service.canonical(original['body'])
    view = (await ledger(client, admin, number, '?before=1'))['refund_submission']
    assert view['authorization_id'] == new['authorization_id'] and len(view['history']) == 2
    assert view['history'][1]['stop']['request_id'] == stop['request_id']
    assert view['stop'] is None and not view['reauthorize_allowed']
    assert (await client.post(f'/shop/download/{number}', headers=buyer)).status_code == 200
    # Even without the audit event, the new authorization fact changes the review fingerprint.
    async with TestSession() as db:
        await db.execute(delete(PaymentEvent).where(PaymentEvent.kind == service.REAUTHORIZED))
        await db.commit()
    assert (await state(client, admin, number))['snapshot'] != before['snapshot']
    assert (await post(client, admin, number, 'stop', stop)).status_code == 200  # old exact command is historical only
    assert (await ledger(client, admin, number))['refund_submission']['stop'] is None
    assert (await post(client, admin, number, 'send', old_send)).status_code == 409
    new_send = {**old_send, 'request_id': uuid.uuid4().hex, 'authorization_id': new['authorization_id'], 'digest': new['digest']}
    assert (await post(client, admin, number, 'send', new_send)).status_code == 409  # gate still OFF
    monkeypatch.setattr(settings, 'WX_REFUND_SEND_ENABLED', True)
    calls = await signed_sender(monkeypatch, number, refund_case[3])
    sent = await post(client, admin, number, 'send', new_send)
    assert sent.status_code == 200 and sent.json()['attempt']['state'] == 'accepted'
    assert len(calls) == 1
    monkeypatch.setattr(settings, 'SITE_BASE_URL', 'https://later.example')
    again = await post(client, admin, number, 'reauthorize', body)
    assert again.status_code == 200 and not again.json()['changed']
    assert again.json()['authorization'] == new and len(calls) == 1


@pytest.mark.parametrize('change', [
    {'amount': True}, {'amount': 1}, {'reason': '中'*27}, {'reason': ' bad'},
    {'digest': 'a'*64}, {'authorization_id': 'b'*32}, {'out_refund_no': 'CMR'+'0'*32},
    {'notify_url': 'https://evil.example'}, {'supersedes_id': 1}, {'evidence': 'bad\nproof'},
])
async def test_invalid_reauthorization_never_writes(client, refund_case, change):
    _, admin, number, _, _, body, _ = await setup(client, refund_case)
    result = await post(client, admin, number, 'reauthorize', {**body, **change})
    assert result.status_code in (409, 422) and len(await versions()) == 1


@pytest.mark.parametrize('activity', ['not_stopped', 'refund_send_started', 'refund_query_started', 'refund_notify_signal', 'refund_query_unknown'])
async def test_never_correct_active_sent_unknown_or_observed_request(client, refund_case, activity):
    _, admin, number, _, _, body, _ = await setup(client, refund_case, stop=activity != 'not_stopped')
    if activity != 'not_stopped':
        async with TestSession() as db:
            oid = await db.scalar(select(Order.id).where(Order.order_no == number))
            db.add(PaymentEvent(order_id=oid, attempt_id=uuid.uuid4().hex, kind=activity))
            await db.commit()
    assert (await post(client, admin, number, 'reauthorize', body)).status_code == 409
    assert len(await versions()) == 1


async def test_stale_parent_cross_actor_and_global_key_cannot_fork(client, refund_case):
    _, admin, number, send, stop, body, _ = await setup(client, refund_case)
    first = await post(client, admin, number, 'reauthorize', body)
    assert first.status_code == 200
    for change in [{'request_id': uuid.uuid4().hex}, {'reason': '不同原因'}, {'evidence': '不同依据'}]:
        assert (await post(client, admin, number, 'reauthorize', {**body, **change})).status_code == 409
    from tests.test_payment_ledger import admin_headers
    other = await admin_headers(client, 'other-auditor')
    assert (await post(client, other, number, 'reauthorize', body)).status_code == 409
    # A successor key cannot be replayed through the original authorization endpoint.
    root_proof = {k: v for k, v in body.items() if k not in ('authorization_id', 'digest')}
    assert (await post(client, admin, number, 'authorize', root_proof)).status_code == 409
    new = first.json()['authorization']
    stop2 = stop_proof({**send, 'authorization_id': new['authorization_id'], 'digest': new['digest']})
    assert (await post(client, admin, number, 'stop', stop2)).status_code == 200
    third_body = {**body, 'request_id': uuid.uuid4().hex, 'authorization_id': new['authorization_id'], 'digest': new['digest']}
    assert (await post(client, admin, number, 'reauthorize', third_body)).status_code == 200
    replay = await post(client, admin, number, 'reauthorize', body)
    assert not replay.json()['changed'] and replay.json()['authorization']['authorization_id'] == body['request_id']
    assert replay.json()['authorization']['stop']['request_id'] == stop2['request_id']
    assert replay.json()['submission']['authorization_id'] == third_body['request_id']
    assert len(await versions()) == 3


@pytest.mark.parametrize('same_key', [True, False])
async def test_competing_new_authorizations_have_one_successor(client, refund_case, same_key):
    _, admin, number, _, _, body, _ = await setup(client, refund_case)
    other = body if same_key else {**body, 'request_id': uuid.uuid4().hex}
    results = await asyncio.gather(post(client, admin, number, 'reauthorize', body), post(client, admin, number, 'reauthorize', other))
    assert sorted(r.status_code for r in results) == ([200,200] if same_key else [200,409])
    assert len(await versions()) == 2


@pytest.mark.parametrize('lost_ack', [True, False])
async def test_unknown_commit_recovers_first_authorization(client, refund_case, monkeypatch, lost_ack):
    _, admin, number, _, _, body, _ = await setup(client, refund_case)
    original = AsyncSession.commit
    async def fail(db):
        if lost_ack:
            await original(db)
        raise OperationalError('synthetic', {}, Exception('unknown'))
    with monkeypatch.context() as m:
        m.setattr(AsyncSession, 'commit', fail)
        assert (await post(client, admin, number, 'reauthorize', body)).status_code == 503
    assert len(await versions()) == 1+int(lost_ack)
    retry = await post(client, admin, number, 'reauthorize', body)
    assert retry.status_code == 200 and retry.json()['changed'] is (not lost_ack)
    assert len(await versions()) == 2


async def test_audit_insert_failure_rolls_back_successor(client, refund_case, monkeypatch):
    _, admin, number, _, _, body, _ = await setup(client, refund_case)
    from sqlalchemy import event
    def fail(mapper, connection, target):
        if target.kind == service.REAUTHORIZED:
            raise OperationalError('synthetic', {}, Exception('audit failed'))
    event.listen(PaymentEvent, 'before_insert', fail)
    try:
        assert (await post(client, admin, number, 'reauthorize', body)).status_code == 503
    finally:
        event.remove(PaymentEvent, 'before_insert', fail)
    assert len(await versions()) == 1
    assert (await post(client, admin, number, 'reauthorize', body)).status_code == 200


async def test_permissions_and_origin_rechecked(client, refund_case, monkeypatch):
    buyer, admin, number, _, _, body, _ = await setup(client, refund_case)
    assert (await post(client, buyer, number, 'reauthorize', body)).status_code == 403
    from app.routers import refunds_admin
    lock = refunds_admin.lock_user
    async def revoked(db, identity):
        row = await lock(db, identity)
        row.credential_version += 1
        return row
    with monkeypatch.context() as m:
        m.setattr(refunds_admin, 'lock_user', revoked)
        assert (await post(client, admin, number, 'reauthorize', body)).status_code == 403
    login = await client.post('/auth/login', data={'username':'auditor', 'password':'secret123'})
    assert login.status_code == 200
    assert (await client.post(f'/shop/admin/orders/{number}/refunds/reauthorize', json=body,
                             headers={'Origin':'https://evil.example'})).status_code == 403
    assert len(await versions()) == 1


def test_manifest_requires_reauthorization_migration(tmp_path):
    for version, (path, _) in db_admin.migration_manifest().items():
        if int(version) < 17:
            (tmp_path / path.name).write_bytes(path.read_bytes())
    with pytest.raises(db_admin.MaintenanceError):
        db_admin.migration_manifest(tmp_path)


@pytest.mark.parametrize("historical_stop", [False, True])
def test_real_pg_upgrade_preserves_stop_and_guards_successor_chain(maintenance_db, historical_stop):
    conn, _ = maintenance_db
    db_admin.initialize(conn)
    legacy_0016(conn)
    rows(conn, "INSERT INTO sys_user(id,username,password,role) VALUES(1,'admin','synthetic',1); "
         "INSERT INTO sys_order(id,order_no,user_id,product_name,amount,payment_mode,status,transaction_id,merchant_id,app_id) "
         "VALUES(1,'ORDER1',1,'fixed',100,'wechat','paid','TX1','MCH','APP'); "
         "INSERT INTO payment_receipt(id,order_id,source,transaction_id,amount,currency,merchant_id,app_id) VALUES(1,1,'wechat','TX1',100,'CNY','MCH','APP'); "
         "INSERT INTO refund_request(id,order_id,payment_receipt_id,request_id,out_refund_no,amount,currency,merchant_id,app_id,actor_id,actor_name,evidence) "
         "VALUES(1,1,1,repeat('a',32),'CMR'||repeat('b',32),100,'CNY','MCH','APP',1,'admin','original preparation')")
    body = service.canonical({'transaction_id':'TX1','out_refund_no':'CMR'+'b'*32,'amount':{'total':100,'refund':100,'currency':'CNY'},'reason':'original','notify_url':'https://test.example/shop/refunds/notify'})
    def insert(key, parent=None):
        with conn, conn.cursor() as cur:
            if parent is None:
                cur.execute("INSERT INTO refund_authorization(preparation_id,request_id,body,digest,actor_id,actor_name,evidence) VALUES(1,%s,%s,%s,1,'admin','synthetic approval') RETURNING id", (key,body,service.digest(body)))
            else:
                cur.execute("INSERT INTO refund_authorization(preparation_id,request_id,body,digest,actor_id,actor_name,evidence,supersedes_id) VALUES(1,%s,%s,%s,1,'admin','synthetic approval',%s) RETURNING id", (key,body,service.digest(body),parent))
            return cur.fetchone()[0]
    root = insert('c'*32)
    stop_sql = f"INSERT INTO refund_send_stop(authorization_id,request_id,actor_id,actor_name,evidence) VALUES({root},repeat('e',32),1,'admin','stop original')"
    if historical_stop:
        rows(conn, stop_sql)
    original_authorization = rows(conn, 'SELECT id,preparation_id,request_id,body,digest,actor_id,actor_name,evidence,created_at FROM refund_authorization')
    original_stops = rows(conn, 'SELECT * FROM refund_send_stop')
    db_admin.migrate(conn)
    assert rows(conn, 'SELECT id,preparation_id,request_id,body,digest,actor_id,actor_name,evidence,created_at FROM refund_authorization') == original_authorization
    assert rows(conn, 'SELECT * FROM refund_send_stop') == original_stops
    assert rows(conn,'SELECT body,supersedes_id FROM refund_authorization') == [(body,None)]
    if not historical_stop:
        with pytest.raises(psycopg2.Error):
            insert('d'*32,root)
        rows(conn, stop_sql)
    # A stopped root from another preparation is never authority for this order.
    rows(conn, "INSERT INTO sys_order(id,order_no,user_id,product_name,amount,payment_mode,status,transaction_id,merchant_id,app_id) "
         "VALUES(2,'ORDER2',1,'fixed',100,'wechat','paid','TX2','MCH','APP'); "
         "INSERT INTO payment_receipt(id,order_id,source,transaction_id,amount,currency,merchant_id,app_id) VALUES(2,2,'wechat','TX2',100,'CNY','MCH','APP'); "
         "INSERT INTO refund_request(id,order_id,payment_receipt_id,request_id,out_refund_no,amount,currency,merchant_id,app_id,actor_id,actor_name,evidence) "
         "VALUES(2,2,2,repeat('3',32),'CMR'||repeat('4',32),100,'CNY','MCH','APP',1,'admin','other preparation')")
    other_body = service.canonical({'transaction_id':'TX2','out_refund_no':'CMR'+'4'*32,
        'amount':{'total':100,'refund':100,'currency':'CNY'},'reason':'other','notify_url':'https://test.example/shop/refunds/notify'})
    with pytest.raises(psycopg2.Error, match='stopped leaf of the same preparation'), conn, conn.cursor() as cur:
        cur.execute("INSERT INTO refund_authorization(preparation_id,request_id,body,digest,actor_id,actor_name,evidence,supersedes_id) "
                "VALUES(2,%s,%s,%s,1,'admin','wrong parent',%s)", ('5'*32,other_body,service.digest(other_body),root))
    child = insert('d'*32,root)
    for key,parent in [('f'*32,None),('f'*32,root),('f'*32,child)]:
        with pytest.raises(psycopg2.Error):
            insert(key,parent)
    rows(conn, f"INSERT INTO refund_send_stop(authorization_id,request_id,actor_id,actor_name,evidence) VALUES({child},repeat('f',32),1,'admin','stop child'); "
         "INSERT INTO payment_event(order_id,attempt_id,kind) VALUES(1,repeat('1',32),'refund_send_started')")
    with pytest.raises(psycopg2.Error):
        insert('2'*32,child)
    for sql in ['UPDATE refund_authorization SET evidence=\'changed\'', 'DELETE FROM refund_authorization']:
        with pytest.raises(psycopg2.Error):
            rows(conn,sql)
    db_admin.migrate(conn)
    assert len(rows(conn,'SELECT * FROM refund_authorization')) == 2


async def test_started_commit_ack_loss_blocks_correction_even_without_http(client, refund_case, monkeypatch):
    _, admin, number, send, stop, body, _ = await setup(client, refund_case, stop=False)
    monkeypatch.setattr(settings, 'WX_REFUND_SEND_ENABLED', True)
    original = AsyncSession.commit
    async def lost(db):
        started = any(isinstance(x, PaymentEvent) and x.kind == service.STARTED for x in db.new)
        await original(db)
        if started:
            raise OperationalError('synthetic', {}, Exception('lost acknowledgement'))
    with monkeypatch.context() as m:
        m.setattr(AsyncSession, 'commit', lost)
        assert (await post(client, admin, number, 'send', send)).status_code == 503
    assert (await post(client, admin, number, 'stop', stop)).status_code == 200
    assert not (await ledger(client, admin, number))['refund_submission']['reauthorize_allowed']
    assert (await post(client, admin, number, 'reauthorize', body)).status_code == 409
    assert len(await versions()) == 1


async def test_cross_order_key_and_original_authorization_replay(client, refund_case):
    _, admin, number, _, _, body, original = await setup(client, refund_case)
    assert (await post(client, admin, number, 'reauthorize', body)).status_code == 200
    root_body = {k:v for k,v in body.items() if k not in ('authorization_id','digest')}
    root_body.update(request_id=original['authorization_id'], reason=original['body']['reason'], evidence=original['evidence'])
    replay = await post(client, admin, number, 'authorize', root_body)
    assert replay.status_code == 200 and not replay.json()['changed']
    assert replay.json()['authorization']['authorization_id'] == original['authorization_id']
    assert replay.json()['submission']['authorization_id'] == body['request_id']
    _, _, other, _, _, other_body, _ = await setup(client, refund_case)
    assert (await post(client, admin, other, 'reauthorize', {**other_body, 'request_id':body['request_id']})).status_code == 409
    assert len(await versions()) == 3


async def test_history_is_bounded_and_not_hidden_by_event_pagination(client, refund_case):
    _, admin, number, _, _, body, _ = await setup(client, refund_case)
    assert (await post(client, admin, number, 'reauthorize', body)).status_code == 200
    # Projection fixture only: ORM create_all has no production authorization INSERT triggers.
    async with TestSession() as db:
        leaf = (await db.scalars(select(RefundAuthorization).order_by(RefundAuthorization.id.desc()))).first()
        for _ in range(50):
            leaf = RefundAuthorization(preparation_id=leaf.preparation_id, supersedes_id=leaf.id,
                request_id=uuid.uuid4().hex, body=leaf.body, digest=leaf.digest, actor_id=leaf.actor_id,
                actor_name=leaf.actor_name, evidence='projection history fixture')
            db.add(leaf)
            await db.flush()
        await db.commit()
    data = (await ledger(client, admin, number, '?before=1'))['refund_submission']
    assert len(data['history']) == 50 and data['history_has_more']
    assert data['history'][0]['authorization_id'] == data['authorization_id'] == leaf.request_id
