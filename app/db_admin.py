"""Offline PostgreSQL maintenance: explicit target, transactional locks and checksum ledger.

Never creates/drops a database, never auto-upgrades at web startup. Legacy adoption
requires an operator-confirmed 0008 baseline; it is not a complete schema diff tool.
"""
from __future__ import annotations

import hashlib
import re
from pathlib import Path

import psycopg2
from sqlalchemy.dialects import postgresql
from sqlalchemy.engine import make_url
from sqlalchemy.schema import CreateTable

from .config import settings
from .models import SchemaMigration
from .schemas import RegisterIn
from .security import hash_password

SQL_ROOT = Path(__file__).resolve().parents[1] / 'database init'
MAINTENANCE_LOCK = 71943208715602

# Immutable historical contract: new ORM columns must not make old adoption impossible.
LEGACY_0008_COLUMNS = {'sys_user': ['id',
              'username',
              'password',
              'nickname',
              'avatar',
              'status',
              'role',
              'credential_version',
              'password_changed_at',
              'create_time',
              'update_time'],
 'sys_order': ['id',
               'order_no',
               'user_id',
               'product_name',
               'amount',
               'status',
               'code_url',
               'transaction_id',
               'paid_at',
               'create_time',
               'update_time'],
 'sys_article': ['id',
                 'url',
                 'title',
                 'author',
                 'published_at',
                 'content',
                 'source_site',
                 'create_time'],
 'sys_config': ['id', 'config_key', 'config_value', 'remark'],
 'sys_diagram': ['id',
                 'user_id',
                 'name',
                 'content',
                 'deleted_at',
                 'version',
                 'create_time',
                 'update_time'],
 'oauth_client': ['id', 'client_id', 'client_secret_hash', 'name', 'redirect_uri', 'status'],
 'oauth_code': ['id',
                'code',
                'user_id',
                'client_id',
                'redirect_uri',
                'expires_at',
                'credential_version',
                'used',
                'create_time'],
 'support_message': ['id',
                     'customer_id',
                     'sender_id',
                     'sender_role',
                     'body',
                     'client_nonce',
                     'create_time']}


class MaintenanceError(RuntimeError):
    """Refuse ambiguous, stale or unsafe maintenance without exposing a connection URI."""


def migration_manifest(root: Path = SQL_ROOT) -> dict[str, tuple[Path, str]]:
    """Version -> file and exact byte checksum, contiguous from 0001; includes historical SQL."""
    result = {}
    for path in sorted(root.glob('migrate_*.sql')):
        match = re.fullmatch(r'migrate_([0-9]{4})_[a-z0-9_]+\.sql', path.name)
        if not match or match[1] in result:
            raise MaintenanceError('Invalid or duplicate migration filename')
        result[match[1]] = (path, hashlib.sha256(path.read_bytes()).hexdigest())
    if list(result) != [f'{n:04}' for n in range(1, len(result) + 1)] or len(result) < 14:
        raise MaintenanceError('Missing migration history')
    return result


def verify_ledger(rows, manifest, *, complete: bool = False) -> None:
    """Reject gaps, edited historical SQL, unknown future versions and incomplete startup state."""
    applied = dict(rows)
    if sorted(applied) != list(manifest)[:len(applied)] or len(applied) < 8:
        raise MaintenanceError('Missing, out-of-order or unsupported migration baseline')
    if any(v not in manifest or digest != manifest[v][1] for v, digest in applied.items()):
        raise MaintenanceError('Migration checksum mismatch; do not edit applied SQL')
    if complete and len(applied) != len(manifest):
        raise MaintenanceError('Pending migrations; run offline maintenance before startup')


def connect_target(confirm_database: str):
    """Use the application's DATABASE_URL/DB settings, never print credentials; caller closes."""
    url = make_url(settings.sqlalchemy_url)
    if url.get_backend_name() != 'postgresql' or url.database != confirm_database:
        raise MaintenanceError('PostgreSQL target must match --confirm-database')
    params = url.translate_connect_args(username='user', database='dbname')
    params.update(url.query)
    conn = psycopg2.connect(**params, connect_timeout=10)
    if conn.info.dbname != confirm_database:
        conn.close()
        raise MaintenanceError('Connected database does not match confirmation')
    return conn


def maintenance_lock(cur) -> None:
    """Transaction-scoped cross-process lock, with finite wait and fixed schema search path."""
    cur.execute("SET LOCAL lock_timeout = '10s'")
    cur.execute("SET LOCAL statement_timeout = '120s'")
    cur.execute('SET LOCAL search_path = public')
    cur.execute('SELECT pg_advisory_xact_lock(%s)', (MAINTENANCE_LOCK,))


def _record(cur, manifest, versions) -> None:
    for version in versions:
        cur.execute('INSERT INTO schema_migration(version,checksum) VALUES (%s,%s)',
                    (version, manifest[version][1]))


def initialize(conn) -> None:
    """Only empty public schema. Schema and complete baseline are committed atomically, no seeds."""
    manifest = migration_manifest()
    with conn, conn.cursor() as cur:
        maintenance_lock(cur)
        cur.execute((SQL_ROOT / 'full_init.sql').read_text(encoding='utf-8'))
        _record(cur, manifest, manifest)


def adopt_legacy(conn) -> None:
    """Operator explicitly declares 0008; verify columns and crucial indexes, not every attribute."""
    manifest = migration_manifest()
    with conn, conn.cursor() as cur:
        maintenance_lock(cur)
        cur.execute("SELECT table_name,column_name FROM information_schema.columns WHERE table_schema='public'")
        columns = {}
        for table, column in cur.fetchall():
            columns.setdefault(table, set()).add(column)
        if 'schema_migration' in columns:
            raise MaintenanceError('Ledger already exists; use migrate/status, never overwrite it')
        for table, required in LEGACY_0008_COLUMNS.items():
            if set(required) - columns.get(table, set()):
                raise MaintenanceError('Legacy schema is not at 0008; inspect historical upgrades first')
        cur.execute("SELECT indexname FROM pg_indexes WHERE schemaname='public'")
        indexes = {row[0] for row in cur.fetchall()}
        if not {'uq_sys_order_user_pending', 'uq_support_sender_nonce'} <= indexes:
            raise MaintenanceError('Legacy key constraints are missing')
        cur.execute(str(CreateTable(SchemaMigration.__table__).compile(dialect=postgresql.dialect())))
        _record(cur, manifest, list(manifest)[:8])
        _migrate(cur, manifest)


def _migrate(cur, manifest) -> None:
    cur.execute('SELECT version,checksum FROM schema_migration ORDER BY version')
    rows = cur.fetchall()
    verify_ledger(rows, manifest)
    for version in list(manifest)[len(rows):]:
        text = manifest[version][0].read_text(encoding='utf-8')
        # 0001–0008 contain legacy transaction wrappers: never replay them through this runner.
        if int(version) < 9 or has_transaction_control(text):
            raise MaintenanceError('New migrations must not control the outer transaction')
        cur.execute(text)
        _record(cur, manifest, [version])


def migrate(conn) -> None:
    """Apply only missing post-baseline SQL, with its journal entry in the same transaction."""
    with conn, conn.cursor() as cur:
        maintenance_lock(cur)
        _migrate(cur, migration_manifest())


def status(conn) -> list[str]:
    """Read-only journal validation; no DDL and no implicit adoption/upgrade."""
    with conn, conn.cursor() as cur:
        cur.execute('SELECT version,checksum FROM public.schema_migration ORDER BY version')
        rows = cur.fetchall()
        manifest = migration_manifest()
        verify_ledger(rows, manifest)
        return list(manifest)[len(rows):]


def seed_demo(conn) -> None:
    """Explicit development-only fixtures. No overwrite/upsert of existing identities."""
    if settings.ENV != 'development':
        raise MaintenanceError('Demo identities are forbidden outside development')
    with conn, conn.cursor() as cur:
        maintenance_lock(cur)
        cur.execute('SELECT version,checksum FROM schema_migration')
        verify_ledger(cur.fetchall(), migration_manifest(), complete=True)
        cur.execute("SET LOCAL codemax.allow_demo = 'yes'")
        cur.execute((SQL_ROOT / 'seed_demo.sql').read_text(encoding='utf-8'))


def bootstrap_admin(conn, username: str, password: str) -> None:
    """Create the first active admin only; never promote/overwrite an existing user, no default secret."""
    RegisterIn(username=username, password=password)
    if len(password) < 12:
        raise MaintenanceError('Administrator password must be at least 12 characters (maximum 72 UTF-8 bytes)')
    digest = hash_password(password)
    with conn, conn.cursor() as cur:
        maintenance_lock(cur)
        cur.execute('SELECT version,checksum FROM schema_migration')
        verify_ledger(cur.fetchall(), migration_manifest(), complete=True)
        cur.execute('SELECT 1 FROM sys_user WHERE role=1 AND status=1 LIMIT 1')
        if cur.fetchone():
            raise MaintenanceError('An active administrator already exists; bootstrap cannot reset it')
        cur.execute('SELECT 1 FROM sys_user WHERE username=%s', (username,))
        if cur.fetchone():
            raise MaintenanceError('Username already exists; bootstrap cannot promote an existing account')
        cur.execute('INSERT INTO sys_user(username,password,status,role,credential_version) VALUES (%s,%s,1,1,0)',
                    (username, digest))


def has_transaction_control(sql: str) -> bool:
    """Inspect top-level PG statements, excluding quoted function bodies/comments.

    A repository migration is trusted code, not arbitrary user SQL. This narrow lexer guards
    accidental transaction wrappers, including same-line COMMIT and START TRANSACTION. It is
    not a SQL authorization sandbox and rejects unterminated quoted/comment regions.
    """
    plain, i = [], 0
    while i < len(sql):
        if sql.startswith('--', i):
            end = sql.find('\n', i + 2)
            i = len(sql) if end < 0 else end
            plain.append(' ')
        elif sql.startswith('/*', i):
            depth, i = 1, i + 2
            while i < len(sql) and depth:
                if sql.startswith('/*', i):
                    depth, i = depth + 1, i + 2
                elif sql.startswith('*/', i):
                    depth, i = depth - 1, i + 2
                else:
                    i += 1
            if depth:
                raise MaintenanceError('Unterminated migration comment')
            plain.append(' ')
        elif sql[i] in "'\"":
            quote = sql[i]
            escaped = quote == "'" and i > 0 and sql[i - 1] in 'eE' and (i < 2 or not sql[i - 2].isalnum())
            i += 1
            while i < len(sql):
                if escaped and sql[i] == '\\':
                    i += 2
                elif sql[i] == quote:
                    i += 1
                    if i < len(sql) and sql[i] == quote:
                        i += 1
                    else:
                        break
                else:
                    i += 1
            else:
                raise MaintenanceError('Unterminated migration quote')
            plain.append(' quoted ')
        elif sql[i] == '$' and (match := re.match(r'\$(?:[A-Za-z_][A-Za-z0-9_]*)?\$', sql[i:])):
            delimiter = match[0]
            end = sql.find(delimiter, i + len(delimiter))
            if end < 0:
                raise MaintenanceError('Unterminated migration function body')
            i = end + len(delimiter)
            plain.append(' quoted ')
        else:
            plain.append(sql[i])
            i += 1
    return any(re.match(r'\s*(?:BEGIN|END|COMMIT|ROLLBACK|ABORT|SAVEPOINT|RELEASE|START\s+TRANSACTION|PREPARE\s+TRANSACTION)\b',
                        statement, re.I) for statement in ''.join(plain).split(';'))
