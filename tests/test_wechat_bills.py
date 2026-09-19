"""Synthetic signed bill HTTP, strict parser, snapshot comparisons and private CLI publication.

No live merchant download or business database. PostgreSQL uses the disposable suite database.
"""
import argparse
import asyncio
import hashlib
import json
import logging
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import httpx
import pytest
from sqlalchemy import event, select

from app import bill_reconcile as report
from app import wechat_bills as bills
from app.config import settings
from app.models import Order, PaymentEvent, PaymentReceipt, RefundReceipt, User
from tests.conftest import TestSession, engine
from tests.test_wechat_pay import _AUTH_RE, _ENV, CFG, signed_response, verify

DAY = (datetime.now(bills.BILL_ZONE) - timedelta(days=1)).date()
AT = datetime.combine(DAY, datetime.min.time(), tzinfo=bills.BILL_ZONE).astimezone(timezone.utc)
URL = 'https://api.mch.weixin.qq.com/v3/billdownload/file?token=PRIVATE_TOKEN%2B%2F%3D'


def row(**changes):
    """Official modern ALL schema including zero refund IDs on payment rows and coupon totals."""
    values = [f'{DAY} 00:00:00', CFG.appid, CFG.mchid, '0', '', 'TX_1', 'ORDER_1', 'PRIVATE_OPENID',
              'NATIVE', 'SUCCESS', 'OTHERS', 'CNY', '8.00', '2.00', '0', '0', '0.00', '0.00', '', '',
              'PRIVATE_PRODUCT\\ 名称\\n\\"', 'PRIVATE_ATTACH', '0.04', '0.60%', '10.00', '0.00', '']
    for key, value in changes.items():
        values[bills.DETAIL_HEADER.index(key)] = value
    return values


def refund(**changes):
    return row(**{'交易状态': 'REFUND', '应结订单金额': '0.00', '代金券金额': '0.00', '订单金额': '0.00',
                  '微信退款单号': 'RF_1', '商户退款单号': 'REFUND_1', '退款金额': '4.00',
                  '申请退款金额': '5.00', '退款状态': 'PROCESSING', '退款类型': 'ORIGINAL', '手续费': '-0.02', **changes})


def raw_bill(*rows):
    """Independent Decimal summation; assertions must not reuse production money conversion."""
    total = [str(len(rows))] + [f'{sum((Decimal(r[index]) for r in rows), Decimal(0)):.2f}' for index in bills.TOTAL_COLUMNS]
    lines = [','.join(bills.DETAIL_HEADER), *[','.join('`' + cell for cell in r) for r in rows],
             ','.join(bills.TOTAL_HEADER), ','.join('`' + cell for cell in total)]
    return ('\r\n'.join(lines) + '\r\n').encode('utf-8')


def parse(raw):
    return bills.parse_bill(raw, day=DAY, merchant_id=CFG.mchid)


def metadata(raw, **changes):
    return {'hash_type': 'SHA1', 'hash_value': hashlib.sha1(raw).hexdigest(), 'download_url': URL, **changes}


async def fetch(raw, *, meta=None, downloaded=None):
    seen = []
    def handler(req):
        seen.append(req)
        return (meta if meta is not None else signed_response(metadata(raw))) if len(seen) == 1 else (
            downloaded if downloaded is not None else httpx.Response(200, content=raw))
    return await bills.fetch_bill(CFG, DAY, transport=httpx.MockTransport(handler))


@pytest.mark.parametrize('path', bills.DOWNLOAD_PATHS)
async def test_signed_metadata_unsigned_file_exact_get_bytes_and_token_redaction(path, caplog):
    raw, requests = raw_bill(row(), refund()), []
    caplog.set_level(logging.INFO)
    def handler(req):
        requests.append(req)
        assert req.method == 'GET' and req.content == b''
        assert req.headers['Accept-Encoding'] == 'identity'
        fields = _AUTH_RE.fullmatch(req.headers['Authorization'])
        verify(fields['signature'], 'GET', req.url.raw_path.decode(), fields['timestamp'], fields['nonce_str'], '')
        if len(requests) == 1:
            assert req.url.raw_path.decode() == f'/v3/bill/tradebill?bill_date={DAY}&bill_type=ALL'
            return signed_response(metadata(raw, download_url=URL.replace(bills.DOWNLOAD_PATHS[0], path)))
        assert req.url.raw_path.decode() == path + '?token=PRIVATE_TOKEN%2B%2F%3D'
        return httpx.Response(200, content=raw)  # Deliberately unsigned, as documented for files.
    result = await bills.fetch_bill(CFG, DAY, transport=httpx.MockTransport(handler))
    assert len(requests) == 2 and len(result.rows) == 2
    assert result.rows[0].total == 1000 and result.totals[0] == 800
    assert result.rows[1].refund_total == 500 and result.totals[1] == 400
    assert result.sha256 == hashlib.sha256(raw).hexdigest()
    assert 'PRIVATE' not in caplog.text


@pytest.mark.parametrize('kind', ['unsigned', 'stale', 'wrong-key', 'tampered', 'duplicate', 'error', 'hash-type', 'hash-size', 'url'])
async def test_bad_metadata_never_downloads(kind):
    raw, seen = raw_bill(row()), []
    meta = metadata(raw)
    if kind == 'hash-type':
        meta['hash_type'] = 'MD5'
    if kind == 'hash-size':
        meta['hash_value'] = '0' * 39
    if kind == 'url':
        meta['download_url'] = 'https://evil.invalid/'
    response = signed_response(meta)
    if kind == 'unsigned':
        response.headers.clear()
    if kind == 'stale':
        response = signed_response(meta, timestamp='1000000000')
    if kind == 'wrong-key':
        response.headers['Wechatpay-Serial'] = 'WRONG'
    if kind == 'tampered':
        response = httpx.Response(200, content=response.content + b' ', headers=response.headers)
    if kind == 'duplicate':
        response.headers = httpx.Headers([*response.headers.multi_items(), ('Wechatpay-Nonce', 'duplicate')])
    if kind == 'error':
        response = signed_response({'code': 'NO_STATEMENT_EXIST', 'message': 'PRIVATE'}, 400)
    def handler(req):
        seen.append(req)
        return response
    with pytest.raises(bills.BillError) as exc:
        await bills.fetch_bill(CFG, DAY, transport=httpx.MockTransport(handler))
    assert len(seen) == 1 and 'PRIVATE' not in str(exc.value)


@pytest.mark.parametrize('value', [None, '', 'http://api.mch.weixin.qq.com/v3/billdownload/file?token=a',
    URL.replace('api.mch.weixin.qq.com', '127.0.0.1'), URL.replace('api.mch.weixin.qq.com', 'api.mch.weixin.qq.com.evil.invalid'),
    URL.replace('api.mch.weixin.qq.com', 'evil@api.mch.weixin.qq.com'), URL.replace('.com/', '.com:443/'),
    URL.replace('/v3/billdownload/file', '/v3/pay/transactions/native'), URL + '#fragment', URL + '&token=b',
    URL + '&tar_type=GZIP', URL.replace('PRIVATE_TOKEN', '%0d%0a'), URL.replace('PRIVATE_TOKEN', '%xx'),
    URL.replace('token=', 'token= '), URL.replace('/v3/', '/v3/../v3/'), URL + 'x' * 2048])
def test_download_url_pin_rejects_before_signing(value):
    with pytest.raises(bills.BillError):
        bills.download_path(value)


@pytest.mark.parametrize('kind', ['changed', 'redirect', 'gzip', 'status', 'length', 'negative-length', 'huge-length', 'oversize', 'timeout'])
async def test_download_failures_never_become_empty_reports(kind):
    raw = raw_bill(row())
    response = httpx.Response(200, content=raw)
    if kind == 'changed':
        response = httpx.Response(200, content=raw.replace(b'ORDER_1', b'ORDER_X'))
    if kind == 'redirect':
        response = httpx.Response(302, headers={'Location': 'https://evil.invalid/PRIVATE'})
    if kind == 'gzip':
        response.headers['Content-Encoding'] = 'gzip'
    if kind == 'status':
        response = httpx.Response(404, content=b'PRIVATE')
    if kind in ('length', 'negative-length', 'huge-length'):
        response.headers['Content-Length'] = {'length': str(len(raw) + 1), 'negative-length': '-1', 'huge-length': '999999999'}[kind]
    if kind == 'oversize':
        response = httpx.Response(200, content=b'x' * (bills.MAX_BYTES + 1))
        del response.headers['Content-Length']  # Exercise incremental bound, not just declared length.
    calls = []
    async def handler(req):
        calls.append(req)
        if len(calls) == 1:
            return signed_response(metadata(raw))
        if kind == 'timeout':
            raise httpx.ReadTimeout('PRIVATE network error')
        return response
    with pytest.raises(bills.BillError) as exc:
        await bills.fetch_bill(CFG, DAY, transport=httpx.MockTransport(handler))
    assert len(calls) == 2 and 'PRIVATE' not in str(exc.value)


async def test_overall_deadline(monkeypatch):
    monkeypatch.setattr(bills, 'DEADLINE', 0.01)
    async def handler(req):
        await asyncio.sleep(0.1)
        pytest.fail('must be cancelled')
    with pytest.raises(bills.BillError):
        await bills.fetch_bill(CFG, DAY, transport=httpx.MockTransport(handler))


@pytest.mark.parametrize('kind', ['header', 'old-format', 'footer', 'extra-row', 'truncated', 'count', 'sum', 'prefix', 'column', 'duplicate-payment', 'duplicate-refund', 'nul', 'utf8', 'money-float', 'negative', 'date', 'merchant', 'submerchant', 'refund-zero', 'refund-state', 'payment-refund-id'])
def test_strict_parser_rejects_bad_or_ambiguous_whole_file(kind):
    raw = raw_bill(row())
    replacements = {'header': ('交易时间,', '错误时间,'), 'old-format': ('应结订单金额', '总金额'),
                    'footer': ('总交易单数', '总笔数'), 'count': ('`1,`8.00', '`2,`8.00'),
                    'sum': ('`1,`8.00', '`1,`8.01'), 'prefix': ('`TX_1', 'TX_1'),
                    'column': ('`PRIVATE_ATTACH,', ''), 'nul': ('PRIVATE_ATTACH', 'PRIVATE\x00'),
                    'money-float': ('`10.00', '`1e1'), 'negative': ('`10.00', '`-10.00'),
                    'date': (f'{DAY} 00:00:00', f'{DAY - timedelta(days=1)} 00:00:00'),
                    'merchant': (CFG.mchid, 'FOREIGN'), 'submerchant': ('`0,`', '`12345678,`')}
    if kind in replacements:
        old, new = replacements[kind]
        raw = raw.replace(old.encode(), new.encode(), 1)
    elif kind == 'extra-row':
        raw += b'PRIVATE\n'
    elif kind == 'truncated':
        raw = raw.rsplit(b'\r\n', 2)[0]
    elif kind == 'duplicate-payment':
        raw = raw_bill(row(), row())
    elif kind == 'duplicate-refund':
        raw = raw_bill(refund(), refund())
    elif kind == 'utf8':
        raw += b'\xff'
    elif kind == 'refund-zero':
        raw = raw_bill(refund(**{'申请退款金额': '0.00'}))
    elif kind == 'refund-state':
        raw = raw_bill(refund(**{'退款状态': 'UNKNOWN'}))
    elif kind == 'payment-refund-id':
        raw = raw_bill(row(**{'微信退款单号': 'RF_1'}))
    with pytest.raises(bills.BillError) as exc:
        parse(raw)
    assert 'PRIVATE' not in str(exc.value)


def test_bom_empty_totals_and_parser_budgets(monkeypatch):
    assert parse(b'\xef\xbb\xbf' + raw_bill()).rows == ()
    monkeypatch.setattr(bills, 'MAX_ROWS', 1)
    with pytest.raises(bills.BillError, match='预算'):
        parse(raw_bill(row(), refund()))
    monkeypatch.setattr(bills, 'MAX_BYTES', 10)
    with pytest.raises(bills.BillError):
        parse(raw_bill())


def test_beijing_day_not_machine_or_operator_day():
    now = datetime(2026, 9, 19, 16, 0, tzinfo=timezone.utc)
    assert bills.bill_day('2026-09-19', now=now).isoformat() == '2026-09-19'
    for value in ('2026-09-20', '2026-09-21', '2026-05-01', '2026-9-19', '2026-09-19?evil', '２０２６-０９-１９'):
        with pytest.raises(bills.BillError):
            bills.bill_day(value, now=now)


async def seed(db, *, number='ORDER_1', transaction='TX_1', paid_at=AT, receipt=True, **changes):
    """Disposable ORM setup, not a production migration or simulated provider confirmation."""
    user = User(username='user_' + number, password='unused')
    db.add(user)
    await db.flush()
    fields = {'order_no': number, 'user_id': user.id, 'product_name': 'PRIVATE_PRODUCT', 'amount': 1000,
                  'payment_mode': 'wechat', 'merchant_id': CFG.mchid, 'app_id': CFG.appid, 'currency': 'CNY',
                  'status': 'paid', 'transaction_id': transaction, 'paid_at': paid_at,
                  'delivery_key': 'PRIVATE_DELIVERY', 'delivery_digest': 'f' * 64, 'delivery_size': 1}
    order = Order(**{**fields, **changes})
    db.add(order)
    await db.flush()
    if receipt:
        db.add(PaymentReceipt(order_id=order.id, source='wechat', merchant_id=CFG.mchid, app_id=CFG.appid,
                              currency='CNY', amount=1000, transaction_id=transaction, paid_at=paid_at))
    await db.commit()
    return order


async def read_report(raw):
    bill = parse(raw)
    local = await report.snapshot(TestSession, bill, CFG.appid)
    return report.compare(bill, local, CFG.appid)


async def test_bidirectional_payment_coupon_and_refund_snapshot_without_writes(db):
    order = await seed(db)
    await seed(db, number='MISSING_CHANNEL', transaction='TX_2')
    statements = []
    def record(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)
    event.listen(engine.sync_engine, 'before_cursor_execute', record)
    try:
        result = await read_report(raw_bill(row(), refund()))
    finally:
        event.remove(engine.sync_engine, 'before_cursor_execute', record)
    assert result['counts'] == {'matched_payment': 1, 'local_receipt_absent_from_bill': 1, 'refund_observation_not_completion': 1}
    assert result['needs_review'] and result['financial_writes'] is False and result['accounting_closed'] is False
    assert not any(sql.lstrip().upper().startswith(('INSERT', 'UPDATE', 'DELETE', 'COMMIT')) for sql in statements)
    async with TestSession() as check:
        original = await check.get(Order, order.id)
        assert original.status == 'paid' and original.delivery_key == 'PRIVATE_DELIVERY' and original.amount == 1000
        assert len((await check.scalars(select(PaymentReceipt))).all()) == 2
        assert (await check.scalars(select(RefundReceipt))).all() == []
        assert (await check.scalars(select(PaymentEvent))).all() == []
    serialized = json.dumps(result)
    assert all(secret not in serialized for secret in ('PRIVATE', 'token=', 'Authorization', '"evidence":'))


@pytest.mark.parametrize('kind,expected', [
    ('normal', 'matched_payment'), ('unknown', 'channel_payment_without_order'),
    ('no-receipt', 'channel_payment_without_receipt'), ('contract', 'order_contract_mismatch'),
    ('source', 'order_contract_mismatch'), ('transaction', 'receipt_contract_mismatch'),
    ('receipt', 'receipt_contract_mismatch'), ('status', 'order_state_mismatch'),
    ('order-transaction', 'order_state_mismatch'), ('time', 'paid_time_mismatch'),
    ('unknown-time', 'paid_time_mismatch'), ('owner', 'transaction_owned_elsewhere')])
async def test_difference_classifications_and_receipt_binding(db, kind, expected):
    if kind != 'unknown':
        order = await seed(db, receipt=kind != 'no-receipt',
                           **({'amount': 999} if kind == 'contract' else {}),
                           **({'payment_mode': 'mock'} if kind == 'source' else {}))
        record = await db.scalar(select(PaymentReceipt).where(PaymentReceipt.order_id == order.id))
        if kind == 'receipt':
            record.amount = 999
        if kind == 'transaction':
            record.transaction_id = 'OTHER_TX'
        if kind == 'status':
            order.status = 'closed'
        if kind == 'order-transaction':
            order.transaction_id = 'OTHER_TX'
        if kind == 'time':
            order.paid_at = record.paid_at = AT - timedelta(days=1)
        if kind == 'unknown-time':
            order.paid_at = record.paid_at = None
        if kind == 'owner':
            order.order_no = 'OTHER_ORDER'
        await db.commit()
    result = await read_report(raw_bill(row()))
    assert result['items'][0]['code'] == expected
    assert result['needs_review'] == (kind != 'normal')


async def test_scope_and_utc_midnight_reverse_scan_uses_paid_not_received(db):
    await seed(db, number='DAY_START', transaction='START')
    await seed(db, number='BEFORE', transaction='BEFORE', paid_at=AT - timedelta(seconds=1))
    await seed(db, number='DAY_END', transaction='END', paid_at=AT + timedelta(days=1))
    await seed(db, number='UNKNOWN_TIME', transaction='UNKNOWN', paid_at=None)
    await seed(db, number='LEGACY', transaction='LEGACY', receipt=False)
    result = await read_report(raw_bill(row(**{'公众账号ID': 'OTHER_APP'})))
    assert result['excluded_rows'] == 1 and result['needs_review']
    assert {item['order_no'] for item in result['items']} == {'DAY_START', 'UNKNOWN_TIME', 'LEGACY'}
    assert result['counts'] == {'local_receipt_absent_from_bill': 1, 'local_paid_time_unknown': 1, 'legacy_paid_without_receipt': 1}


async def test_both_channel_and_local_limits_fail_not_partial_success(db, monkeypatch):
    await seed(db)
    await seed(db, number='OTHER', transaction='OTHER')
    monkeypatch.setattr(bills, 'MAX_ROWS', 1)
    with pytest.raises(bills.BillError, match='本地快照'):
        await read_report(raw_bill())


async def test_missing_migration_ledger_refuses_cli_snapshot(db):
    with pytest.raises(Exception, match='migration|Migration|baseline'):
        await report.snapshot(TestSession, parse(raw_bill()), CFG.appid, check_schema=True)


@pytest.mark.parametrize('state', ['SUCCESS', 'PROCESSING', 'FAIL', 'CHANGE'])
async def test_partial_refund_states_never_mean_completed_receipt(db, state):
    result = await read_report(raw_bill(refund(**{'退款状态': state})))
    assert result['items'][0]['code'] == 'refund_observation_not_completion'
    assert result['needs_review'] and result['items'][0]['requested_refund_cents'] == 500
    assert (await db.scalars(select(RefundReceipt))).all() == []


def test_atomic_private_report_no_overwrite_and_failed_publish_cleanup(tmp_path, monkeypatch):
    artifact = {'bill_date': str(DAY), 'run_id': 'a' * 32, 'needs_review': True}
    path = report.publish(artifact, tmp_path)
    before = path.read_bytes()
    assert json.loads(before) == artifact
    if os.name != 'nt':
        assert path.stat().st_mode & 0o777 == 0o600
    with pytest.raises(FileExistsError):
        report.publish({**artifact, 'needs_review': False}, tmp_path)
    assert path.read_bytes() == before and list(tmp_path.glob('*.tmp')) == []
    def fail(*args):
        raise OSError('PRIVATE')
    monkeypatch.setattr(os, 'fsync', fail)
    with pytest.raises(OSError):
        report.publish({**artifact, 'run_id': 'b' * 32}, tmp_path)
    assert [p.name for p in tmp_path.iterdir()] == [path.name]


def test_report_directory_not_downloadable_or_symlinked(tmp_path, monkeypatch):
    monkeypatch.setattr(report, 'ROOT', tmp_path)
    assert report.report_directory('storage') == tmp_path / 'runtime' / 'wechat-bills'
    with pytest.raises(bills.BillError):
        report.report_directory('runtime')
    (tmp_path / 'runtime').mkdir()
    try:
        (tmp_path / 'runtime' / 'wechat-bills').symlink_to(tmp_path, target_is_directory=True)
    except OSError:
        if os.name == 'nt':
            pytest.skip('Windows symlink creation requires local privileges/developer mode')
        raise
    with pytest.raises(bills.BillError):
        report.report_directory('storage')


@pytest.mark.parametrize('kind', ['disabled', 'merchant', 'target', 'date'])
async def test_cli_gates_precede_all_external_io(monkeypatch, kind):
    for key, value in _ENV.items():
        monkeypatch.setattr(settings, key, value)
    monkeypatch.setattr(settings, 'WX_BILL_READ_ENABLED', kind != 'disabled')
    monkeypatch.setattr(settings, 'DATABASE_URL', 'postgresql+asyncpg://unused:PRIVATE@localhost:5432/disposable')
    args = argparse.Namespace(bill_date=str(DAY), confirm_merchant=CFG.mchid, confirm_target='localhost:5432/disposable')
    setattr(args, {'merchant': 'confirm_merchant', 'target': 'confirm_target', 'date': 'bill_date'}.get(kind, 'unused'), 'WRONG')
    async def forbidden(*args, **kwargs):
        pytest.fail('gate must precede bill application')
    monkeypatch.setattr(bills, 'fetch_bill', forbidden)
    with pytest.raises(bills.BillError):
        await report.run(args)


@pytest.mark.parametrize('attention,code', [(False, 0), (True, 2), (None, 1)])
def test_cli_exit_codes_and_secret_safe_errors(monkeypatch, capsys, attention, code):
    async def fake(args):
        if attention is None:
            raise RuntimeError('PRIVATE DSN SQL key raw-body')
        return Path('safe.json'), attention
    monkeypatch.setattr(report, 'run', fake)
    monkeypatch.setattr(logging, 'disable', lambda *args: None)
    assert report.main(['--bill-date', str(DAY), '--confirm-target', 'target', '--confirm-merchant', 'merchant']) == code
    text = capsys.readouterr().out
    assert 'PRIVATE' not in text
    assert ('safe.json' in text) == (code != 1)


def test_real_cli_help_and_invalid_environment_are_secret_safe():
    env = {**os.environ, 'DB_PORT': 'PRIVATE-invalid', 'WX_BILL_READ_ENABLED': 'true'}
    base = [sys.executable, '-m', 'app.bill_reconcile']
    help_run = subprocess.run([*base, '--help'], env=env, capture_output=True, text=True, timeout=10)
    assert help_run.returncode == 0
    bad_args = subprocess.run([*base, '--bill-date', str(DAY), '--confirm-target', 'x', '--confirm-merchant', 'x',
                               '--private-token', 'PRIVATE_BAD_ARGUMENT'], env=env,
                              capture_output=True, text=True, timeout=10)
    assert bad_args.returncode == 2 and 'PRIVATE' not in bad_args.stdout + bad_args.stderr
    failed = subprocess.run([*base, '--bill-date', str(DAY), '--confirm-target', 'x', '--confirm-merchant', 'x'],
                            env=env, capture_output=True, text=True, timeout=10)
    assert failed.returncode == 1 and 'PRIVATE' not in failed.stdout + failed.stderr and 'Traceback' not in failed.stderr


async def test_consistent_pg_snapshot_does_not_see_mid_read_receipt(db, monkeypatch):
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import AsyncSession

    if engine.dialect.name != 'postgresql':
        pytest.skip('Concurrent reader/writer snapshot needs disposable PostgreSQL')
    await seed(db)
    real, inserted, flags = AsyncSession.execute, False, []
    async def execute(self, statement, *args, **kwargs):
        nonlocal inserted
        result = await real(self, statement, *args, **kwargs)
        sql = str(statement)
        if sql == 'SET TRANSACTION READ ONLY':
            flags.append((await real(self, text('SHOW transaction_read_only'))).scalar_one())
            flags.append((await real(self, text('SHOW transaction_isolation'))).scalar_one())
        if 'FROM sys_order LEFT OUTER JOIN payment_receipt' in sql and not inserted:
            inserted = True
            async with TestSession() as other:
                await seed(other, number='LATE', transaction='LATE')
        return result
    monkeypatch.setattr(AsyncSession, 'execute', execute)
    first = await read_report(raw_bill(row(**{'商户订单号': 'LATE', '微信订单号': 'LATE'})))
    assert inserted and first['items'][0]['code'] == 'channel_payment_without_order'
    assert flags == ['on', 'repeatable read']
    second = await read_report(raw_bill(row(**{'商户订单号': 'LATE', '微信订单号': 'LATE'})))
    assert second['items'][0]['code'] == 'matched_payment'


@pytest.mark.parametrize('dsn,expected', [
    ('postgresql+asyncpg://u:PRIVATE@host/db', 'host:5432/db'),
    ('postgresql+asyncpg://u:PRIVATE@host:5555/db?host=/private/socket&port=5433', '/private/socket:5433/db'),
    ('postgresql+asyncpg://u:PRIVATE@host/db?database=OTHER', None),
    ('postgresql+asyncpg://u:PRIVATE@host/db?host=a&host=b', None),
    ('postgresql+asyncpg://u:PRIVATE@host/db?port=0', None),
    ('postgresql+asyncpg://u:PRIVATE@host/db?host=a,b', None),
    ('sqlite+aiosqlite:///local.db', None),
    ('postgresql://u:PRIVATE@host/db', None),
])
def test_confirmation_cannot_hide_database_url_overrides(dsn, expected):
    from sqlalchemy.engine import make_url

    if expected is None:
        with pytest.raises(bills.BillError):
            report.connection_target(make_url(dsn))
    else:
        assert report.connection_target(make_url(dsn)) == expected


async def test_streamed_hash_and_download_phase_deadline(monkeypatch):
    raw = raw_bill(row())
    class Chunks(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield raw[:20]
            yield raw[20:]
    result = await fetch(raw, downloaded=httpx.Response(200, stream=Chunks()))
    assert result.sha256 == hashlib.sha256(raw).hexdigest()
    calls = []
    async def slow(req):
        calls.append(req)
        if len(calls) == 1:
            return signed_response(metadata(raw))
        await asyncio.sleep(1)
        pytest.fail('download must be cancelled')
    monkeypatch.setattr(bills, 'DEADLINE', 0.1)
    with pytest.raises(bills.BillError):
        await bills.fetch_bill(CFG, DAY, transport=httpx.MockTransport(slow))
    assert len(calls) == 2


async def test_full_cli_pipeline_on_complete_disposable_pg(tmp_path, monkeypatch):
    """Own true PG + full SQL initializer; real signed MockTransport, no production connection."""
    from sqlalchemy.engine import make_url
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app import database, db_admin

    pgserver = pytest.importorskip('pgserver', reason='Disposable PostgreSQL package required')
    server = await asyncio.to_thread(pgserver.get_server, tmp_path / 'pg', cleanup_mode='delete')
    try:
        await asyncio.to_thread(server.psql, 'CREATE ROLE bill_test LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE')
        await asyncio.to_thread(server.psql, 'CREATE DATABASE bill_test OWNER bill_test')
        uri = make_url(server.get_uri()).set(username='bill_test', database='bill_test')
        def initialize():
            from contextlib import closing

            import psycopg2
            with closing(psycopg2.connect(uri.render_as_string(hide_password=False))) as conn:
                db_admin.initialize(conn)
        await asyncio.to_thread(initialize)
        uri = uri.set(drivername='postgresql+asyncpg')
        test_engine = create_async_engine(uri)
        factory = async_sessionmaker(test_engine, expire_on_commit=False)
        monkeypatch.setattr(database, 'engine', test_engine)
        monkeypatch.setattr(database, 'SessionLocal', factory)
        monkeypatch.setattr(report, 'ROOT', tmp_path)
        for key, value in _ENV.items():
            monkeypatch.setattr(settings, key, value)
        monkeypatch.setattr(settings, 'WX_BILL_READ_ENABLED', True)
        monkeypatch.setattr(settings, 'DATABASE_URL', uri.render_as_string(hide_password=False))
        raw, real_fetch, calls = raw_bill(row()), bills.fetch_bill, []
        def handler(req):
            calls.append(req)
            return signed_response(metadata(raw)) if len(calls) == 1 else httpx.Response(200, content=raw)
        async def download(cfg, day):
            return await real_fetch(cfg, day, transport=httpx.MockTransport(handler))
        monkeypatch.setattr(bills, 'fetch_bill', download)
        args = argparse.Namespace(bill_date=str(DAY), confirm_merchant=CFG.mchid,
                                  confirm_target=report.connection_target(uri))
        path, attention = await report.run(args)
        assert attention and len(calls) == 2
        result = json.loads(path.read_bytes())
        assert result['counts'] == {'channel_payment_without_order': 1}
        async with factory() as check:
            assert (await check.scalars(select(Order))).all() == []
            assert (await check.scalars(select(PaymentReceipt))).all() == []
        await test_engine.dispose()
    finally:
        await asyncio.to_thread(server.cleanup)
