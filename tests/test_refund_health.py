"""Local supervision, alarms and disposable recovery; never calls a real merchant or system supervisor."""
import asyncio
import json
import os
import signal
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import select, update

from app import refund_health as health
from app import refund_verification as queue
from app import refund_worker as worker
from app.config import settings
from app.models import PaymentEvent, RefundReceipt, RefundVerificationJob
from tests.conftest import TEST_DATABASE_URL, TestSession
from tests.test_db_admin import isolated_pg as isolated_pg
from tests.test_db_admin import maintenance_db as maintenance_db
from tests.test_refund_verification import configuration as configuration
from tests.test_refund_verification import jobs, setup
from tests.test_refunds import refund_case as refund_case

EMPTY = dict.fromkeys(health.COUNTERS, 0)


def test_atomic_private_status_lock_and_transition_logs(tmp_path, capsys):
    path = tmp_path / 'private/status.json'
    with health.Publisher(path) as publisher:
        publisher.publish('starting')
        assert health.check(path)[0] == 1
        publisher.publish('running', EMPTY)
        first = json.loads(path.read_text())
        publisher.publish('running', EMPTY)
        assert json.loads(path.read_text())['sequence'] == first['sequence'] + 1
        assert health.check(path) == (0, [])
        with pytest.raises(OSError), health.Publisher(path):
            pytest.fail('Second writer acquired the same status path')
        if os.name != 'nt':
            assert path.stat().st_mode & 0o777 == 0o600
    with health.Publisher(path) as next_process:
        assert next_process.instance != first['instance']
        next_process.publish('starting')
    assert health.check(path)[0] == 1
    assert len(capsys.readouterr().out.splitlines()) == 3  # starting/running/new starting, no idle flood
    assert {p.name for p in path.parent.iterdir()} == {'status.json', 'status.json.lock'}


@pytest.mark.parametrize('change', [
    {'v': True}, {'v': 2}, {'pid': False}, {'sequence': 0}, {'instance': 'secret'},
    {'updated_at': float('nan')}, {'updated_at': float('inf')}, {'queue': []},
    {'queue': {**EMPTY, 'attention': True}}, {'queue': {**EMPTY, 'due': -1}},
    {'queue': {**EMPTY, 'customer': 'secret'}}, {'state': 'completed'}, {'state': 'failed'},
])
def test_status_fails_closed_on_malformed_or_non_running(tmp_path, change):
    path = tmp_path / 'status.json'
    with health.Publisher(path) as publisher:
        publisher.publish('running', EMPTY)
    note = json.loads(path.read_text())
    path.write_text(json.dumps({**note, **change}))
    code, reasons = health.check(path)
    assert code == 1 and reasons and 'secret' not in str(reasons)


@pytest.mark.parametrize('raw', [b'', b'{', b'[]', b'null', b'\xff', b'x'*4097, b'['*1500])
def test_missing_partial_oversized_status_is_not_healthy(tmp_path, raw):
    path = tmp_path / 'status.json'
    assert health.check(path) == (1, ['status_unavailable'])
    path.write_bytes(raw)
    assert health.check(path) == (1, ['status_unavailable'])


def test_stale_future_alarm_and_clear_are_distinct(tmp_path):
    path = tmp_path / 'status.json'
    with health.Publisher(path) as publisher:
        publisher.publish('running', {**EMPTY, 'attention': 1, 'expired': 1, 'retry': 1, 'due': 50, 'oldest_due_seconds': 300})
        stamp = json.loads(path.read_text())['updated_at']
        assert health.check(path, now=stamp + 120)[0] == 0
        assert health.check(path, now=stamp + 121) == (1, ['stale_or_clock_skew'])
        assert health.check(path, now=stamp - 6)[0] == 1
        assert health.check(path, include_alerts=True) == (1, ['needs_operator', 'expired_lease', 'query_retry', 'due_backlog', 'overdue_queue'])
        publisher.publish('running', {**EMPTY, 'due': 49, 'oldest_due_seconds': 299})
        assert health.check(path, include_alerts=True) == (0, [])


def test_failed_atomic_replace_retains_old_snapshot_and_cleans_temporary(tmp_path, monkeypatch):
    path = tmp_path / 'status.json'
    with health.Publisher(path) as publisher:
        publisher.publish('running', EMPTY)
        original = path.read_bytes()
        def fail(*args):
            raise OSError('synthetic private filename')
        monkeypatch.setattr(health.os, 'replace', fail)
        with pytest.raises(OSError):
            publisher.publish('running', {**EMPTY, 'due': 100})
        assert path.read_bytes() == original
        assert {p.name for p in tmp_path.iterdir()} == {'status.json', 'status.json.lock'}


def test_check_cli_ignores_configuration_and_never_connects(tmp_path):
    path = tmp_path / 'status.json'
    env = {**os.environ, 'DB_PORT': 'invalid-private-value', 'DATABASE_URL': 'not-a-dsn', 'WX_REFUND_VERIFY_ENABLED': 'bad'}
    with health.Publisher(path) as publisher:
        publisher.publish('running', {**EMPTY, 'attention': 1})
    command = [sys.executable, '-m', 'app.refund_health', '--status-file', str(path)]
    result = subprocess.run(command, capture_output=True, text=True, env=env, timeout=10)
    assert result.returncode == 0 and json.loads(result.stdout)['ok']
    result = subprocess.run([*command, '--alerts'], capture_output=True, text=True, env=env, timeout=10)
    assert result.returncode == 1 and json.loads(result.stdout)['reasons'] == ['needs_operator']
    assert 'invalid-private-value' not in result.stdout + result.stderr
    assert subprocess.run([*command, '--max-age', '0'], capture_output=True, env=env, timeout=10).returncode == 2


async def test_queue_snapshot_uses_due_clock_and_never_changes_evidence(client, refund_case):
    await setup(client, refund_case)
    now = datetime.now(timezone.utc)
    async with TestSession() as db:
        await db.execute(update(RefundVerificationJob).values(next_at=now - timedelta(seconds=400)))
        await db.commit()
        before = list((await db.scalars(select(PaymentEvent.id))).all())
    counters = await health.queue_snapshot(TestSession)
    assert counters == {**EMPTY, 'pending': 1, 'due': 1, 'oldest_due_seconds': counters['oldest_due_seconds']}
    assert 398 <= counters['oldest_due_seconds'] < 450
    ticket = await queue.claim(TestSession)
    counters = await health.queue_snapshot(TestSession)
    assert counters == {**EMPTY, 'running': 1}
    async with TestSession() as db:
        await db.execute(update(RefundVerificationJob).values(lease_until=now - timedelta(seconds=1)))
        await db.commit()
    assert (await health.queue_snapshot(TestSession))['expired'] == 1
    assert not await queue.finish(TestSession, ticket, 'verified', 'success_needs_admin')
    async with TestSession() as db:
        after = list((await db.scalars(select(PaymentEvent.id))).all())
        assert len(after) == len(before) + 1  # only the real claim, no health-check audit or receipt
        assert await db.scalar(select(RefundReceipt.id)) is None


@pytest.fixture
def fake_worker(monkeypatch):
    class Result:
        def all(self):
            return []
    class Session:
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            pass
        async def execute(self, statement):
            return Result()
    monkeypatch.setattr(worker, 'SessionLocal', Session)
    monkeypatch.setattr(worker, 'verify_ledger', lambda *a, **kw: None)
    monkeypatch.setattr(settings, 'WX_REFUND_VERIFY_ENABLED', True)
    monkeypatch.setattr(settings, 'WX_REFUND_AUTO_RECORD_ENABLED', False)
    async def snapshot(factory):
        return EMPTY
    async def cycle(*args, **kwargs):
        assert kwargs == {'enabled': True, 'auto_record': False}
    monkeypatch.setattr(worker, 'queue_snapshot', snapshot)
    monkeypatch.setattr(worker, 'run_once', cycle)


async def test_once_is_not_daemon_health(fake_worker, tmp_path):
    path = tmp_path / 'status.json'
    with health.Publisher(path) as publisher:
        await worker.run(once=True, publisher=publisher)
    assert json.loads(path.read_text())['state'] == 'completed'
    assert health.check(path)[0] == 1


@pytest.mark.parametrize('fault', ['cycle', 'summary', 'schema', 'timeout', 'disabled'])
async def test_failed_cycle_cannot_refresh_healthy_status(fake_worker, tmp_path, monkeypatch, fault):
    async def broken(*args, **kwargs):
        if fault == 'timeout':
            await asyncio.Event().wait()
        raise RuntimeError('private DSN or provider payload')
    if fault == 'schema':
        def invalid(*args, **kwargs):
            raise worker.MaintenanceError('invalid ledger')
        monkeypatch.setattr(worker, 'verify_ledger', invalid)
    elif fault == 'disabled':
        monkeypatch.setattr(settings, 'WX_REFUND_VERIFY_ENABLED', False)
    else:
        monkeypatch.setattr(worker, 'queue_snapshot' if fault == 'summary' else 'run_once', broken)
    monkeypatch.setattr(worker, 'CYCLE_TIMEOUT', 0.02)
    path = tmp_path / 'status.json'
    with health.Publisher(path) as publisher:
        publisher.publish('running', EMPTY)
        with pytest.raises((RuntimeError, worker.MaintenanceError, TimeoutError)):
            await worker.run(publisher=publisher)
    assert json.loads(path.read_text())['state'] == 'failed'
    assert health.check(path)[0] == 1 and 'private' not in path.read_text()


async def test_cancel_writes_stopped_and_does_not_invent_completion(fake_worker, tmp_path, monkeypatch):
    entered = asyncio.Event()
    async def blocked(*args, **kwargs):
        entered.set()
        await asyncio.Event().wait()
    monkeypatch.setattr(worker, 'run_once', blocked)
    path = tmp_path / 'status.json'
    with health.Publisher(path) as publisher:
        task = asyncio.create_task(worker.run(publisher=publisher))
        await asyncio.wait_for(entered.wait(), 5)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert json.loads(path.read_text())['state'] == 'stopped'
    assert health.check(path)[0] == 1


def test_main_redacts_unexpected_error_and_lock_conflict(tmp_path):
    script = "from app import refund_worker as w\nasync def fail(*a): raise RuntimeError('PRIVATE-DSN')\nw.serve=fail\nw.main()"
    path = tmp_path / 'status.json'
    result = subprocess.run([sys.executable, '-c', script, '--status-file', str(path)], capture_output=True, text=True, timeout=10)
    assert result.returncode == 1
    assert 'PRIVATE-DSN' not in result.stderr and 'Traceback' not in result.stderr
    with health.Publisher(path) as publisher:
        publisher.publish('running', EMPTY)
        original = path.read_bytes()
        result = subprocess.run([sys.executable, '-m', 'app.refund_worker', '--status-file', str(path)], capture_output=True, text=True, timeout=10)
        assert result.returncode == 1 and path.read_bytes() == original


# Only the I/O boundary is replaced. Real OS process, publisher lock, independent DB claim/finish.
# Test schema uses ORM create_all; the separate maintenance test checks real migration ledger startup.
PROCESS_SCRIPT = '''
import asyncio, sys
from app import refund_worker as w
from app import refund_verification as q
w.verify_ledger = lambda *a, **kw: None
async def cycle(factory, cfg, **kwargs):
    assert kwargs == {'enabled': True, 'auto_record': False}
    ticket = await q.claim(factory)
    print('CLAIMED' if ticket else 'IDLE', flush=True)
    if ticket and MODE == 'block':
        await asyncio.Event().wait()
    elif ticket:
        await q.finish(factory, ticket, 'verified', 'success_needs_admin')
w.run_once = cycle
w.main()
'''


async def child(path, mode, *, once=False):
    env = {**os.environ, 'DATABASE_URL': TEST_DATABASE_URL, 'WX_REFUND_VERIFY_ENABLED': 'true',
           'WX_REFUND_AUTO_RECORD_ENABLED': 'false', 'WX_REFUND_SEND_ENABLED': 'false'}
    return await asyncio.create_subprocess_exec(sys.executable, '-c', f'MODE={mode!r}\n'+PROCESS_SCRIPT,
        '--status-file', str(path), *(['--once'] if once else []),
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, env=env)


async def wait_claim(process):
    async with asyncio.timeout(10):
        while line := await process.stdout.readline():
            if line.strip() == b'CLAIMED':
                return
    raise AssertionError('Process exited before durable claim')


@pytest.mark.skipif(os.name == 'nt', reason='POSIX SIGTERM/SIGKILL process rehearsal; Windows requires its own supervisor acceptance')
@pytest.mark.parametrize('hard', [False, True])
async def test_real_process_exit_and_restart_preserve_lease_and_fence_old_result(client, refund_case, tmp_path, hard):
    await setup(client, refund_case)
    path = tmp_path / 'status.json'
    process = await child(path, 'block')
    try:
        await wait_claim(process)
        old = (await jobs())[0]
        ticket = queue.Ticket(old.id, old.order_id, old.notice_event_id, old.token, old.attempts)
        process.send_signal(signal.SIGKILL if hard else signal.SIGTERM)
        await asyncio.wait_for(process.wait(), 10)
        assert process.returncode == (-signal.SIGKILL if hard else 0)
    finally:
        if process.returncode is None:
            process.kill()
            await process.wait()
    assert health.check(path)[0] == 1
    assert (await jobs())[0].state == 'running' and (await jobs())[0].attempts == 1
    assert await queue.claim(TestSession) is None  # neither kill nor restart may immediately steal lease
    async with TestSession() as db:
        await db.execute(update(RefundVerificationJob).values(lease_until=datetime.now(timezone.utc) - timedelta(seconds=1)))
        await db.commit()  # controlled expiry, not a production recovery instruction
    recovered = await child(path, 'finish', once=True)
    try:
        stdout, stderr = await asyncio.wait_for(recovered.communicate(), 10)
        assert recovered.returncode == 0, stderr.decode()
        assert b'CLAIMED' in stdout
    finally:
        if recovered.returncode is None:
            recovered.kill()
            await recovered.wait()
    current = (await jobs())[0]
    assert current.state == 'verified' and current.attempts == 2
    assert not await queue.finish(TestSession, ticket, 'attention', 'stale')
    assert (await jobs())[0].state == 'verified'
    async with TestSession() as db:
        assert await db.scalar(select(RefundReceipt.id)) is None  # synthetic observation, no financial settlement
    assert json.loads(path.read_text())['state'] == 'completed'


def test_real_migration_startup_once_disabled_and_db_outage(maintenance_db, tmp_path):
    from sqlalchemy.engine import make_url

    from app import db_admin
    conn, uri = maintenance_db
    db_admin.initialize(conn)
    path = tmp_path / 'status.json'
    env = {**os.environ, 'DATABASE_URL': make_url(uri).set(drivername='postgresql+asyncpg').render_as_string(hide_password=False),
           'WX_REFUND_VERIFY_ENABLED': 'true', 'WX_REFUND_AUTO_RECORD_ENABLED': 'false', 'WX_REFUND_SEND_ENABLED': 'false'}
    command = [sys.executable, '-m', 'app.refund_worker', '--once', '--status-file', str(path)]
    success = subprocess.run(command, capture_output=True, text=True, env=env, timeout=20)
    assert success.returncode == 0, success.stderr
    assert json.loads(path.read_text())['state'] == 'completed'
    assert health.check(path)[0] == 1
    disabled = subprocess.run(command, capture_output=True, text=True, env={**env, 'WX_REFUND_VERIFY_ENABLED': 'false'}, timeout=20)
    assert disabled.returncode == 1 and json.loads(path.read_text())['state'] == 'failed'
    outage = subprocess.run(command, capture_output=True, text=True,
        env={**env, 'DATABASE_URL': 'postgresql+asyncpg://synthetic:PRIVATE-PASSWORD@127.0.0.1:1/disposable'}, timeout=20)
    assert outage.returncode == 1 and json.loads(path.read_text())['state'] == 'failed'
    assert 'PRIVATE-PASSWORD' not in outage.stderr + outage.stdout and 'Traceback' not in outage.stderr
    with conn, conn.cursor() as cursor:
        cursor.execute("UPDATE schema_migration SET checksum=repeat('0',64) WHERE version='0017'")
    drift = subprocess.run(command, capture_output=True, text=True, env=env, timeout=20)
    assert drift.returncode == 1 and json.loads(path.read_text())['state'] == 'failed'


def test_supervision_template_is_opt_in_and_overrides_web_probe():
    root = Path(__file__).resolve().parents[1]
    text = (root / 'docker-compose.yml').read_text()
    block = text.split('  refund-verifier:\n')[1].split('\nvolumes:')[0]
    assert 'profiles: ["refund-verifier"]' in block and 'restart: "on-failure:5"' in block
    assert 'app.refund_worker' in block and 'app.refund_health' in block and '/healthz' not in block
    assert 'WX_REFUND_AUTO_RECORD_ENABLED: "false"' in block and 'WX_REFUND_SEND_ENABLED: "false"' in block
    assert 'WX_REFUND_VERIFY_ENABLED:' not in block and 'ports:' not in block
    assert '--alerts' not in block  # backlog/manual hold must not cause restart loops
    assert 'runtime' in (root / '.dockerignore').read_text() and '/runtime/' in (root / '.gitignore').read_text()


@pytest.mark.parametrize('environment', [
    {'DATABASE_URL': 'PRIVATE-INVALID-DSN'}, {'DB_PORT': 'PRIVATE-INVALID-PORT'},
])
def test_import_time_configuration_failure_is_redacted(environment):
    result = subprocess.run([sys.executable, '-m', 'app.refund_worker', '--once'],
        env={**os.environ, **environment}, capture_output=True, text=True, timeout=10)
    assert result.returncode == 1 and 'startup configuration unavailable' in result.stderr
    assert 'PRIVATE-' not in result.stderr + result.stdout and 'Traceback' not in result.stderr


@pytest.mark.parametrize('state', ['pending', 'retry', 'attention', 'verified'])
async def test_future_retry_hold_and_verified_are_not_due(client, refund_case, state):
    await setup(client, refund_case)
    async with TestSession() as db:
        await db.execute(update(RefundVerificationJob).values(state=state,
            next_at=datetime.now(timezone.utc) + timedelta(hours=1)))
        await db.commit()
    counters = await health.queue_snapshot(TestSession)
    assert counters == (EMPTY if state == 'verified' else {**EMPTY, state: 1})
    assert health.alarms(counters) == {'pending': [], 'retry': ['query_retry'], 'attention': ['needs_operator'], 'verified': []}[state]


@pytest.mark.skipif(os.name == 'nt', reason='POSIX daemon signals; Windows supervisor not accepted by this Linux rehearsal')
@pytest.mark.parametrize('hard', [False, True])
async def test_real_idle_daemon_freshness_exit_and_lock_recovery(maintenance_db, tmp_path, hard):
    from sqlalchemy.engine import make_url

    from app import db_admin
    conn, uri = maintenance_db
    db_admin.initialize(conn)
    path = tmp_path / 'status.json'
    env = {**os.environ, 'DATABASE_URL': make_url(uri).set(drivername='postgresql+asyncpg').render_as_string(hide_password=False),
           'WX_REFUND_VERIFY_ENABLED': 'true', 'WX_REFUND_AUTO_RECORD_ENABLED': 'false', 'WX_REFUND_SEND_ENABLED': 'false'}
    command = [sys.executable, '-m', 'app.refund_worker', '--status-file', str(path)]
    process = await asyncio.create_subprocess_exec(*command, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, env=env)
    try:
        async with asyncio.timeout(15):
            while line := await process.stdout.readline():
                if json.loads(line)['state'] == 'running':
                    break
            else:
                pytest.fail('Real daemon never completed first cycle')
        assert health.check(path, include_alerts=True) == (0, [])
        note = json.loads(path.read_text())
        process.send_signal(signal.SIGKILL if hard else signal.SIGTERM)
        await asyncio.wait_for(process.wait(), 10)
        assert process.returncode == (-signal.SIGKILL if hard else 0)
        if hard:
            # Freshness is a bounded lagging signal, NOT an instantaneous process-existence test.
            assert health.check(path, now=note['updated_at']) == (0, [])
            assert health.check(path, now=note['updated_at'] + 121)[0] == 1
        else:
            assert health.check(path)[0] == 1
    finally:
        if process.returncode is None:
            process.kill()
            await process.wait()
    recovered = await asyncio.create_subprocess_exec(*command, '--once', stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, env=env)
    try:
        _, errors = await asyncio.wait_for(recovered.communicate(), 15)
        assert recovered.returncode == 0, errors.decode()
        assert json.loads(path.read_text())['instance'] != note['instance']
        assert health.check(path)[0] == 1  # one-shot success must not masquerade as a running service
    finally:
        if recovered.returncode is None:
            recovered.kill()
            await recovered.wait()
