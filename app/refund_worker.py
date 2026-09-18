"""Opt-in supervised refund verifier: python -m app.refund_worker [--once]. Never a sender."""
import argparse
import asyncio

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from .config import settings
from .database import SessionLocal
from .db_admin import MaintenanceError, migration_manifest, verify_ledger
from .refund_verification import run_once
from .wechat_pay import pay_config


async def run(once=False):
    """Refuse disabled/missing schema before work; no auto-DDL, startup task or service account."""
    if not settings.WX_REFUND_VERIFY_ENABLED:
        raise MaintenanceError('WX_REFUND_VERIFY_ENABLED is false; no query performed')
    async with SessionLocal() as db:
        rows = (await db.execute(text('SELECT version, checksum FROM schema_migration ORDER BY version'))).all()
        verify_ledger(rows, migration_manifest(), complete=True)
    while settings.WX_REFUND_VERIFY_ENABLED:
        await run_once(SessionLocal, pay_config(), enabled=settings.WX_REFUND_VERIFY_ENABLED)
        if once:
            return
        await asyncio.sleep(5)  # bounded pressure, including failures/idle; supervise/restart externally.


def main():
    parser = argparse.ArgumentParser(description='Read-only refund notification verification, no money movement')
    parser.add_argument('--once', action='store_true', help='one bounded cycle; NOT drain the whole backlog')
    args = parser.parse_args()
    try:
        asyncio.run(run(args.once))
    except (MaintenanceError, SQLAlchemyError):
        parser.exit(1, 'Verifier disabled or database/schema unavailable; inspect configuration and status, no raw secrets logged.\n')
    except KeyboardInterrupt:
        pass  # An unfinished lease remains recoverable; do not claim the HTTP result was saved.


if __name__ == '__main__':
    main()
