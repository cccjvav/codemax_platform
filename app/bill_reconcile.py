"""Explicit operator CLI: private daily bill differences, no financial writes or automatic repair.

python -m app.bill_reconcile --bill-date YYYY-MM-DD --confirm-target HOST:PORT/DATABASE
    --confirm-merchant MCHID
Database/OS privileges are the authority, not a website administrator session. No HTTP endpoint.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import logging
import os
import tempfile
import uuid
from collections import Counter
from datetime import datetime, time, timedelta, timezone
from pathlib import Path

from .wechat_bills import BillError

ROOT = Path(__file__).resolve().parents[1]


class _SafeParser(argparse.ArgumentParser):
    """Even usage errors must not echo a mistakenly pasted credential/DSN argument."""

    def error(self, message):
        self.exit(2, '参数无效；请使用--help查看用法，不要传入密钥或带密码连接串。\n')


async def snapshot(factory, bill, appid, *, check_schema=False):
    """Bounded consistent read of day receipts AND channel references, no commit/row-write locks/network.

    PostgreSQL REPEATABLE READ + READ ONLY; SQLite explicit BEGIN for disposable tests.
    Day matching uses original paid_at, not notification arrival or the operator's timezone.
    Unknown paid timestamps are attention items, never silently omitted as outside the day.
    """
    from sqlalchemy import and_, or_, select, text

    from .models import Order as O
    from .models import PaymentReceipt as R
    from .timeutil import as_utc
    from .wechat_bills import BILL_ZONE, MAX_ROWS

    start = datetime.combine(bill.day, time.min, tzinfo=BILL_ZONE).astimezone(timezone.utc)
    end = start + timedelta(days=1)
    original = and_(O.payment_mode == 'wechat', O.merchant_id == bill.merchant_id, O.app_id == appid)
    receipt = and_(R.source == 'wechat', R.merchant_id == bill.merchant_id, R.app_id == appid)
    def in_day(field):
        return or_(field.is_(None), and_(field >= start, field < end))
    query = select(O, R).outerjoin(R, R.order_id == O.id)
    # Include conflicting order/receipt dates or identities from either side, not just valid pairs.
    day_query = query.where(or_(and_(receipt, in_day(R.paid_at)),
                               and_(original, O.status.in_(('paid', 'downloaded')), in_day(O.paid_at))))
    scoped = [row for row in bill.rows if row.appid == appid and row.trade_type == 'NATIVE' and row.currency == 'CNY']
    payments = [row for row in scoped if row.state == 'SUCCESS']
    found = {}
    async with asyncio.timeout(20), factory() as db:
        dialect = db.bind.dialect.name
        if dialect == 'postgresql':
            await db.connection(execution_options={'isolation_level': 'REPEATABLE READ'})
            await db.execute(text('SET TRANSACTION READ ONLY'))
            await db.execute(text("SET LOCAL statement_timeout = '10000ms'"))
        elif dialect == 'sqlite':
            await db.execute(text('BEGIN'))
        else:
            raise BillError('对账快照数据库类型不支持')
        if check_schema:
            from .db_admin import migration_manifest, verify_ledger
            from .models import SchemaMigration
            verify_ledger((await db.execute(select(SchemaMigration.version, SchemaMigration.checksum))).all(),
                          migration_manifest(), complete=True)
        day_rows = (await db.execute(day_query.limit(MAX_ROWS + 1))).all()
        found.update((order.id, (order, record)) for order, record in day_rows)
        for offset in range(0, len(payments), 400):
            group = payments[offset:offset + 400]
            referenced = query.where(or_(O.order_no.in_([row.order_no for row in group]),
                                        and_(R.source == 'wechat', R.transaction_id.in_([row.transaction_id for row in group]))))
            found.update((order.id, (order, record)) for order, record in (await db.execute(referenced.limit(MAX_ROWS + 1))).all())
            if len(found) > MAX_ROWS:
                raise BillError('本地快照超过5000单；未生成部分成功报告')
        if len(found) > MAX_ROWS:
            raise BillError('本地快照超过5000单；未生成部分成功报告')
        def fact(order, record):
            # Narrow projection: no payer, customer, delivery key, evidence or raw ORM serialization.
            def values(obj, fields):
                return {key: (as_utc(value).isoformat() if isinstance(value, datetime) else value)
                        for key in fields for value in [getattr(obj, key)]}
            return {'order': values(order, ('id', 'order_no', 'payment_mode', 'merchant_id', 'app_id', 'amount',
                                          'currency', 'status', 'transaction_id', 'paid_at')),
                    'receipt': values(record, ('id', 'source', 'merchant_id', 'app_id', 'transaction_id',
                                              'amount', 'currency', 'paid_at')) if record else None}
        return {'captured_at': datetime.now(timezone.utc).isoformat(), 'isolation': dialect + ':snapshot',
                'day_order_ids': sorted(order.id for order, _ in day_rows),
                'facts': [fact(*found[key]) for key in sorted(found)]}


def compare(bill, local, appid):
    """Pure bidirectional payment comparison. All refund rows are non-final observations only.

    No global 'balanced' assertion: exclusions, legacy unknown dates and refund snapshots require
    human review. Even zero differences within this scope is NOT settlement/accounting closure.
    """
    scoped = [row for row in bill.rows if row.appid == appid and row.trade_type == 'NATIVE' and row.currency == 'CNY']
    by_number = {fact['order']['order_no']: fact for fact in local['facts']}
    by_transaction = {fact['receipt']['transaction_id']: fact for fact in local['facts']
                      if fact['receipt'] and fact['receipt']['source'] == 'wechat'}
    results, seen = [], set()
    for row in scoped:
        item = {'line': row.line, 'order_no': row.order_no, 'transaction_id': row.transaction_id}
        if row.state != 'SUCCESS':
            results.append({**item, 'code': 'refund_observation_not_completion', 'refund_id': row.refund_id,
                            'out_refund_no': row.out_refund_no, 'requested_refund_cents': row.refund_total,
                            'bill_refund_state': row.refund_state, 'initiated_at': row.at.isoformat()})
            continue
        fact, owner = by_number.get(row.order_no), by_transaction.get(row.transaction_id)
        seen.add(row.order_no)
        code = 'matched_payment'
        if owner and owner['order']['order_no'] != row.order_no:
            code = 'transaction_owned_elsewhere'
        elif not fact:
            code = 'channel_payment_without_order'
        else:
            order, receipt = fact['order'], fact['receipt']
            identity = (bill.merchant_id, appid, row.total, 'CNY')
            if (order['payment_mode'] != 'wechat'
                    or tuple(order[key] for key in ('merchant_id', 'app_id', 'amount', 'currency')) != identity):
                code = 'order_contract_mismatch'
            elif not receipt:
                code = 'channel_payment_without_receipt'
            elif (receipt['source'] != 'wechat' or receipt['transaction_id'] != row.transaction_id
                    or tuple(receipt[key] for key in ('merchant_id', 'app_id', 'amount', 'currency')) != identity):
                code = 'receipt_contract_mismatch'
            elif order['status'] not in ('paid', 'downloaded') or order['transaction_id'] != row.transaction_id:
                code = 'order_state_mismatch'
            elif (not receipt['paid_at'] or not order['paid_at']
                    or datetime.fromisoformat(receipt['paid_at']) != row.at
                    or datetime.fromisoformat(order['paid_at']) != row.at):
                code = 'paid_time_mismatch'
        results.append({**item, 'code': code, 'order_total_cents': row.total, 'paid_at': row.at.isoformat()})
    day_ids = set(local['day_order_ids'])
    for fact in local['facts']:
        order, receipt = fact['order'], fact['receipt']
        if order['id'] not in day_ids or order['order_no'] in seen:
            continue
        code = 'local_receipt_absent_from_bill' if receipt else 'legacy_paid_without_receipt'
        if not order['paid_at'] or (receipt and not receipt['paid_at']):
            code = 'local_paid_time_unknown'
        results.append({'order_no': order['order_no'], 'code': code})
    excluded = len(bill.rows) - len(scoped)
    counts = dict(sorted(Counter(item['code'] for item in results).items()))
    return {'format_version': 1, 'run_id': uuid.uuid4().hex, 'bill_date': bill.day.isoformat(),
            'merchant_id': bill.merchant_id, 'app_id': appid, 'bill_type': 'ALL', 'bill_timezone': 'UTC+08:00',
            'source_sha256': bill.sha256, 'source_rows': len(bill.rows), 'excluded_rows': excluded,
            'needs_review': bool(excluded or any(item['code'] != 'matched_payment' for item in results)),
            'scope': 'Native CNY payments only; refunds are initiation snapshots, not completion evidence',
            'accounting_closed': False, 'financial_writes': False, 'counts': counts, 'items': results,
            'snapshot': local,
            'snapshot_sha256': hashlib.sha256(json.dumps(local, sort_keys=True, ensure_ascii=True).encode()).hexdigest()}


def report_directory(storage_root):
    """One ignored private operator directory; never place reports in downloadable product/static roots."""
    path = ROOT / 'runtime' / 'wechat-bills'
    if path.is_symlink() or path.parent.is_symlink():
        raise BillError('私有报告目录不允许符号链接')
    path = path.resolve()
    storage = (ROOT / storage_root).resolve()
    if any(path.is_relative_to(public) for public in (storage, (ROOT / 'app' / 'static').resolve(), (ROOT / 'docs' / 'site').resolve())):
        raise BillError('私有报告目录不能位于商品存储或公开静态目录')
    return path


def publish(report, directory):
    """0600 temp + fsync + exclusive hard-link publication on trusted local filesystem, no overwrite.

    JSON contains sensitive order references. POSIX modes do not replace Windows operator ACLs.
    No raw bill retained; this private artifact is neither WORM nor a payment/audit receipt.
    """
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    target = directory / (report['bill_date'] + '-' + report['run_id'] + '.json')
    fd, name = tempfile.mkstemp(prefix='.bill-', suffix='.tmp', dir=directory)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(json.dumps(report, ensure_ascii=False, indent=2).encode('utf-8') + b'\n')
            stream.flush()
            os.fsync(stream.fileno())
        os.link(name, target)  # Atomic visibility and EEXIST, unlike replace() of another report.
    finally:
        os.unlink(name)
    return target


def connection_target(url):
    """Confirm effective single host/socket + port + database, including allowed URL overrides."""
    try:
        host = url.query.get('host', url.host)
        port = int(url.query.get('port', url.port or 5432))
        if (url.get_backend_name() != 'postgresql' or url.get_driver_name() != 'asyncpg'
                or set(url.query) - {'host', 'port', 'ssl', 'sslmode'}
                or not isinstance(host, str) or not host or ',' in host or not url.database
                or not 1 <= port <= 65535):
            raise ValueError
        target = f'{host}:{port}/{url.database}'
        if len(target) > 1024 or any(ord(c) < 33 for c in target):
            raise ValueError
        return target
    except (ValueError, TypeError):
        raise BillError('数据库目标须为单一PostgreSQL asyncpg主机/端口/库，不能含未核准连接覆盖项') from None


async def ledger_ready(factory):
    """Read-only preflight ends BEFORE HTTP; do not download against an unready local schema."""
    from sqlalchemy import select

    from .db_admin import migration_manifest, verify_ledger
    from .models import SchemaMigration

    async with asyncio.timeout(10), factory() as db:
        rows = (await db.execute(select(SchemaMigration.version, SchemaMigration.checksum))).all()
        verify_ledger(rows, migration_manifest(), complete=True)


async def run(args):
    """Explicit opt-in and target confirmation precede network/DB. Overall 80s cooperative budget."""
    from sqlalchemy.engine import make_url

    from .config import settings
    from .wechat_bills import bill_day, fetch_bill
    from .wechat_pay import assert_notify_configuration, pay_config

    if not settings.WX_BILL_READ_ENABLED:
        raise BillError('账单读取默认关闭：仅获授权后启用WX_BILL_READ_ENABLED')
    cfg = pay_config()
    assert_notify_configuration(cfg)
    url = make_url(settings.sqlalchemy_url)
    target = connection_target(url)
    if (args.confirm_target != target
            or not cfg.mchid or args.confirm_merchant != cfg.mchid):
        raise BillError('必须显式确认当前PostgreSQL主机/端口/数据库及商户')
    day = bill_day(args.bill_date)
    directory = report_directory(settings.STORAGE_LOCAL_ROOT)
    from .database import SessionLocal, engine
    try:
        async with asyncio.timeout(80):
            await ledger_ready(SessionLocal)
            bill = await fetch_bill(cfg, day)
            local = await snapshot(SessionLocal, bill, cfg.appid, check_schema=True)
            report = compare(bill, local, cfg.appid)
            path = await asyncio.to_thread(publish, report, directory)
            return path, report['needs_review']
    finally:
        await engine.dispose()


def main(argv=None):
    """Exit 0 = no differences in limited scope; 2 = report needs review; 1 = no trusted result.

    Parse/import failures and DB exceptions never print credentials, SQL parameters or bill tokens.
    Help works without a valid environment. No daemon, automatic retries or default bill date.
    """
    parser = _SafeParser(description=__doc__)
    parser.add_argument('--bill-date', required=True)
    parser.add_argument('--confirm-target', required=True)
    parser.add_argument('--confirm-merchant', required=True)
    args = parser.parse_args(argv)
    # Suppress third-party transport/SQL diagnostics in this dedicated operator process.
    logging.disable(logging.CRITICAL)
    try:
        path, attention = asyncio.run(run(args))
    except BillError as exc:
        print(str(exc))
        return 1
    except KeyboardInterrupt:
        print('账单对账已中断；没有财务写入，请核对私有目录后显式重试。')
        return 130
    except Exception:
        print('账单对账未完成；检查显式开关、日期、目标、凭据、格式/预算及数据库迁移。不得视作零差异。')
        return 1
    print(f'私有报告：{path.name}（runtime/wechat-bills）；' + ('需人工核查。' if attention else '限定范围内未见差异，不是财务结案。'))
    return 2 if attention else 0


if __name__ == '__main__':
    raise SystemExit(main())
