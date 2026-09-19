"""Opt-in supervised refund verifier: python -m app.refund_worker [--once]. Never a sender."""
import argparse
import asyncio
import signal
from contextlib import nullcontext
from pathlib import Path

from sqlalchemy import text

try:
    from .config import settings
    from .database import SessionLocal
    from .db_admin import MaintenanceError, migration_manifest, verify_ledger
    from .refund_health import Publisher, queue_snapshot
    from .refund_verification import run_once
    from .wechat_pay import pay_config
except Exception:
    if __name__ == '__main__':
        raise SystemExit('Verifier startup configuration unavailable; no raw secrets logged.') from None
    raise

CYCLE_TIMEOUT = 60  # includes repair/DB/GET/summary; timeout preserves unknown in-flight lease.


async def run(once=False, publisher=None):
    """No DDL or auto-enable. A heartbeat certifies a completed bounded cycle, not refund success."""
    try:
        if publisher:
            publisher.publish('starting')  # invalidate a prior process's fresh status before schema work.
        if not settings.WX_REFUND_VERIFY_ENABLED:
            raise MaintenanceError('WX_REFUND_VERIFY_ENABLED is false; no query performed')
        async with asyncio.timeout(15), SessionLocal() as db:
            rows = (await db.execute(text('SELECT version, checksum FROM schema_migration ORDER BY version'))).all()
            verify_ledger(rows, migration_manifest(), complete=True)
        while settings.WX_REFUND_VERIFY_ENABLED:
            async with asyncio.timeout(CYCLE_TIMEOUT):
                await run_once(SessionLocal, pay_config(), enabled=settings.WX_REFUND_VERIFY_ENABLED,
                               auto_record=settings.WX_REFUND_AUTO_RECORD_ENABLED)
                counters = await queue_snapshot(SessionLocal) if publisher else None
            if publisher:
                publisher.publish('completed' if once else 'running', counters)
            if once:
                return
            await asyncio.sleep(5)
        if publisher:
            publisher.publish('stopped')
    except (asyncio.CancelledError, KeyboardInterrupt):
        if publisher:
            publisher.publish('stopped')
        raise
    except Exception:
        if publisher:
            publisher.publish('failed')
        raise


async def serve(once=False, publisher=None):
    """SIGTERM cancels the cycle without clearing/requeuing its lease; recovery remains DB-fenced."""
    loop = asyncio.get_running_loop()
    task = asyncio.current_task()
    installed = False
    try:
        try:
            loop.add_signal_handler(signal.SIGTERM, task.cancel)
            installed = True
        except (NotImplementedError, RuntimeError):
            pass  # Windows/non-main thread: Ctrl+C or supervisor termination, freshness still expires.
        await run(once, publisher)
    finally:
        if installed:
            loop.remove_signal_handler(signal.SIGTERM)


def main():
    parser = argparse.ArgumentParser(description='Refund GET verification, optional local receipt recording, no outgoing money')
    parser.add_argument('--once', action='store_true', help='one bounded cycle; NOT drain the whole backlog')
    parser.add_argument('--status-file', type=Path, help='private local heartbeat path; one writer, never in static/storage')
    args = parser.parse_args()
    try:
        with Publisher(args.status_file) if args.status_file else nullcontext() as publisher:
            asyncio.run(serve(args.once, publisher))
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass  # Unfinished leases remain recoverable; not a claim that the HTTP result was saved.
    except Exception:
        parser.exit(1, 'Verifier disabled or failed (database/schema/configuration/status/timeout); inspect private deployment, no raw secrets logged.\n')


if __name__ == '__main__':
    main()
