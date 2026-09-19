"""Private local verifier heartbeat and read-only queue alarms; never financial authority.

Check via python -m app.refund_health --status-file PATH [--alerts]. The checker
uses only stdlib, not Settings, a database connection or merchant credentials.
One trusted private local path per worker. No HTTP endpoint or external notifier.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import tempfile
import time
import uuid
from pathlib import Path

COUNTERS = ('pending', 'retry', 'running', 'attention', 'due', 'expired', 'oldest_due_seconds')
MAX_AGE = 120  # two 60-second cycle budgets; not proof the PID is still alive.


async def queue_snapshot(factory):
    """Aggregate with DB clock; no order IDs, tokens, raw errors, mutation or provider I/O."""
    from sqlalchemy import case, func, select

    from .models import RefundVerificationJob as Job
    from .refund_verification import clock
    from .timeutil import as_utc

    async with factory() as db:
        now = await clock(db)
        due = Job.state.in_(('pending', 'retry')) & (Job.next_at <= now)
        expired = (Job.state == 'running') & (Job.lease_until <= now)
        values = (await db.execute(select(
            *(func.count(case((Job.state == state, 1))) for state in COUNTERS[:4]),
            func.count(case((due, 1))), func.count(case((expired, 1))),
            func.min(case((due, Job.next_at))),
        ).where(Job.state != 'verified'))).one()
        oldest = max(0, int((now - as_utc(values[-1])).total_seconds())) if values[-1] else 0
        return dict(zip(COUNTERS, (*values[:6], oldest), strict=True))


def alarms(counters):
    """Stable local alarm codes; attention includes intentional manual holds, never auto-requeue."""
    return [code for condition, code in (
        (counters['attention'] > 0, 'needs_operator'),
        (counters['expired'] > 0, 'expired_lease'),
        (counters['retry'] > 0, 'query_retry'),
        (counters['due'] >= 50, 'due_backlog'),
        (counters['oldest_due_seconds'] >= 300, 'overdue_queue'),
    ) if condition]


class Publisher:
    """OS-released single-writer lock plus atomic snapshots, inside an operator-owned directory.

    The persistent .lock file is NOT a stale PID lock: never unlink it to recover.
    POSIX flock / Windows byte lock releases on crash. This is local, not shared-storage HA.
    """
    def __init__(self, path):
        self.path = Path(path)
        self.instance = uuid.uuid4().hex
        self.sequence = 0
        self.previous = None
        self.lock = None

    def __enter__(self):
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        fd = os.open(str(self.path) + '.lock', os.O_CREAT | os.O_RDWR | getattr(os, 'O_NOFOLLOW', 0), 0o600)
        self.lock = os.fdopen(fd, 'r+b')
        try:
            if os.name == 'nt':
                import msvcrt
                # Lock byte zero, including for an initially empty file.
                msvcrt.locking(self.lock.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BaseException:
            self.lock.close()
            raise
        return self

    def __exit__(self, *args):
        self.lock.close()  # OS releases the lock; retain the inode for the next process.

    def publish(self, state, counters=None):
        self.sequence += 1
        note = {'v': 1, 'instance': self.instance, 'pid': os.getpid(), 'sequence': self.sequence,
                'updated_at': time.time(), 'state': state, 'queue': counters}
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=self.path.parent, delete=False) as stream:
                temporary = stream.name
                json.dump(note, stream, separators=(',', ':'), allow_nan=False)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
        finally:
            if temporary is not None and os.path.exists(temporary):
                os.unlink(temporary)
        codes = alarms(counters) if counters is not None else []
        transition = (state, tuple(codes))
        if transition != self.previous:
            print(json.dumps({'component': 'refund_verifier', 'state': state, 'alarms': codes}), flush=True)
            self.previous = transition  # log transitions, not every five-second idle cycle.


def check(path, *, include_alerts=False, max_age=MAX_AGE, now=None):
    """Fail closed on absent, oversized, malformed, future or stale data; never echo untrusted file text."""
    try:
        with Path(path).open('rb') as stream:
            raw = stream.read(4097)
        if len(raw) > 4096:
            raise ValueError('size')
        note = json.loads(raw)
        if (type(note) is not dict or type(note.get('v')) is not int or note['v'] != 1
                or not isinstance(note.get('instance'), str) or not re.fullmatch('[0-9a-f]{32}', note['instance'])
                or any(type(note.get(k)) is not int or note[k] <= 0 for k in ('pid', 'sequence'))
                or type(note.get('updated_at')) not in (float, int) or not math.isfinite(note['updated_at'])):
            raise ValueError('schema')
        age = (time.time() if now is None else now) - note['updated_at']
        if age < -5 or age > max_age:
            return 1, ['stale_or_clock_skew']
        if note.get('state') != 'running':
            return 1, ['worker_not_running']
        counters = note.get('queue')
        if (type(counters) is not dict or set(counters) != set(COUNTERS)
                or any(type(v) is not int or v < 0 for v in counters.values())):
            raise ValueError('counters')
        codes = alarms(counters) if include_alerts else []
        return (1 if codes else 0), codes
    except (OSError, ValueError, TypeError, OverflowError, RecursionError):
        return 1, ['status_unavailable']


def main():
    parser = argparse.ArgumentParser(description='Local verifier freshness / queue alarms, no DB or network I/O')
    parser.add_argument('--status-file', required=True, type=Path)
    parser.add_argument('--alerts', action='store_true', help='also fail on queue alarms; NOT a restart policy')
    parser.add_argument('--max-age', type=int, default=MAX_AGE, help='freshness seconds, 1..3600 (default 120)')
    args = parser.parse_args()
    if not 1 <= args.max_age <= 3600:
        parser.error('max-age must be 1..3600')
    code, reasons = check(args.status_file, include_alerts=args.alerts, max_age=args.max_age)
    print(json.dumps({'component': 'refund_verifier', 'check': 'alerts' if args.alerts else 'freshness',
                      'ok': code == 0, 'reasons': reasons}))
    raise SystemExit(code)


if __name__ == '__main__':
    main()
