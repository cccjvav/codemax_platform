"""Explicit offline maintenance CLI. No subcommand means help, never destructive initialization.

Run from any working directory; configuration always comes from repository .env
and the same Settings as the web app. The database must already exist.
"""
from __future__ import annotations

import argparse
import getpass
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app import db_admin  # noqa: E402


def main(argv=None) -> int:
    """Validate CLI before connecting, confirm actual database, prompt secrets without echo/history."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['init', 'adopt-legacy-0008', 'migrate', 'status', 'seed-demo', 'bootstrap-admin'])
    parser.add_argument('--confirm-database', required=True)
    parser.add_argument('--username', help='new non-reserved username for bootstrap-admin only')
    args = parser.parse_args(argv)
    conn = None
    try:
        password = None
        if args.command == 'bootstrap-admin':
            if not args.username:
                parser.error('--username is required for bootstrap-admin')
            password = getpass.getpass('New administrator password: ')
            if password != getpass.getpass('Repeat password: '):
                raise db_admin.MaintenanceError('Passwords do not match')
        conn = db_admin.connect_target(args.confirm_database)
        if args.command == 'bootstrap-admin':
            db_admin.bootstrap_admin(conn, args.username, password)
        else:
            action = {'init': db_admin.initialize, 'adopt-legacy-0008': db_admin.adopt_legacy,
                      'migrate': db_admin.migrate, 'status': db_admin.status, 'seed-demo': db_admin.seed_demo}[args.command]
            result = action(conn)
            if args.command == 'status':
                print('Pending versions:', ', '.join(result) or 'none')
        print('Maintenance completed. No database was created or dropped.')
        return 0
    except db_admin.MaintenanceError as error:
        print(f"Maintenance refused: {error}", file=sys.stderr)
        return 1
    except Exception as error:
        # Driver errors can contain connection details. Keep CLI failures nonzero and redacted:
        # only the exception *class* is shown (TD-270) so "PostgreSQL service not running" (OperationalError)
        # is distinguishable from a baseline/ledger refusal without ever printing the DSN or message.
        kind = type(error).__name__
        hint = ('database connection failed: check that PostgreSQL is running and DB_*/DATABASE_URL match'
                if kind == 'OperationalError' else 'check target, baseline, ledger and command requirements')
        print(f'Maintenance refused/failed ({kind}): {hint}; no automatic reset.', file=sys.stderr)
        return 1
    finally:
        if conn is not None:
            conn.close()


if __name__ == '__main__':
    raise SystemExit(main())
