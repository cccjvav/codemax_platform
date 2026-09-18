"""Offline maintenance runs against its own disposable PostgreSQL, never a supplied business DSN."""
import concurrent.futures
import subprocess
import sys
import uuid
from pathlib import Path

import psycopg2
import pytest
from sqlalchemy.engine import make_url

from app import db_admin
from app.config import settings
from app.security import verify_password


@pytest.fixture(scope='module')
def isolated_pg(tmp_path_factory):
    pgserver = pytest.importorskip('pgserver', reason='Disposable PostgreSQL binary required; Linux CI runs these tests')
    server = pgserver.get_server(tmp_path_factory.mktemp('maintenance-pg') / 'data', cleanup_mode='delete')
    server.psql('CREATE ROLE maintenance_test LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE')
    try:
        yield server
    finally:
        server.cleanup()


@pytest.fixture
def maintenance_db(isolated_pg):
    name = 'case_' + uuid.uuid4().hex
    isolated_pg.psql(f'CREATE DATABASE {name} OWNER maintenance_test')
    uri = make_url(isolated_pg.get_uri()).set(username='maintenance_test', database=name).render_as_string(hide_password=False)
    conn = psycopg2.connect(uri)
    try:
        yield conn, uri
    finally:
        conn.close()
        isolated_pg.psql(f'DROP DATABASE {name}')


def rows(conn, sql):
    with conn, conn.cursor() as cur:
        cur.execute(sql)
        return cur.fetchall() if cur.description else []


def test_init_has_no_default_identity_and_repeat_preserves_data(maintenance_db):
    conn, _ = maintenance_db
    db_admin.initialize(conn)
    assert rows(conn, 'SELECT * FROM sys_user') == rows(conn, 'SELECT * FROM oauth_client') == []
    assert len(rows(conn, 'SELECT * FROM schema_migration')) == len(db_admin.migration_manifest())
    rows(conn, "INSERT INTO sys_config(config_key, config_value) VALUES ('sentinel', 'keep')")
    with pytest.raises(psycopg2.Error):
        db_admin.initialize(conn)
    assert rows(conn, 'SELECT config_value FROM sys_config') == [('keep',)]
    assert db_admin.status(conn) == []


def test_init_refuses_unrelated_nonempty_database(maintenance_db):
    conn, _ = maintenance_db
    rows(conn, 'CREATE TABLE important_data(id int); INSERT INTO important_data VALUES (7)')
    with pytest.raises(psycopg2.Error):
        db_admin.initialize(conn)
    assert rows(conn, 'SELECT * FROM important_data') == [(7,)]
    assert rows(conn, "SELECT to_regclass('sys_user')") == [(None,)]


def test_concurrent_init_cannot_reset_winner(maintenance_db):
    conn, uri = maintenance_db
    def run():
        other = psycopg2.connect(uri)
        try:
            db_admin.initialize(other)
            return 'created'
        except psycopg2.Error:
            return 'refused'
        finally:
            other.close()
    with concurrent.futures.ThreadPoolExecutor(2) as pool:
        assert sorted(pool.map(lambda _: run(), range(2))) == ['created', 'refused']
    assert len(rows(conn, 'SELECT * FROM schema_migration')) == len(db_admin.migration_manifest())


def test_legacy_adoption_preserves_orders_and_retires_demo(maintenance_db):
    conn, _ = maintenance_db
    db_admin.initialize(conn)
    db_admin.seed_demo(conn)
    rows(conn, "INSERT INTO sys_order(order_no,user_id,product_name,amount) SELECT 'LEGACY',id,'kept',100 FROM sys_user")
    legacy_0008(conn)
    db_admin.adopt_legacy(conn)
    assert rows(conn, 'SELECT status,credential_version FROM sys_user') == [(0, 1)]
    assert rows(conn, 'SELECT status FROM oauth_client') == [(0,), (0,)]
    assert rows(conn, 'SELECT order_no,amount FROM sys_order') == [('LEGACY', 100)]
    db_admin.migrate(conn)
    assert rows(conn, 'SELECT credential_version FROM sys_user') == [(1,)]  # no repeated invalidation
    with pytest.raises(db_admin.MaintenanceError):
        db_admin.adopt_legacy(conn)


def test_legacy_adoption_preserves_changed_admin_credentials(maintenance_db):
    conn, _ = maintenance_db
    db_admin.initialize(conn)
    db_admin.seed_demo(conn)
    rows(conn, "UPDATE sys_user SET password='already-rotated-hash'")
    legacy_0008(conn)
    db_admin.adopt_legacy(conn)
    assert rows(conn, 'SELECT status,credential_version,password FROM sys_user') == [(1, 0, 'already-rotated-hash')]


def test_adoption_refuses_incomplete_schema_without_ledger(maintenance_db):
    conn, _ = maintenance_db
    db_admin.initialize(conn)
    legacy_0008(conn)
    rows(conn, 'ALTER TABLE sys_user DROP COLUMN credential_version')
    with pytest.raises(db_admin.MaintenanceError):
        db_admin.adopt_legacy(conn)
    assert rows(conn, "SELECT to_regclass('schema_migration')") == [(None,)]


def test_failed_migration_rolls_back_ddl_and_journal(maintenance_db, tmp_path, monkeypatch):
    conn, _ = maintenance_db
    db_admin.initialize(conn)
    original = db_admin.migration_manifest
    for path, _ in original().values():
        (tmp_path / path.name).write_bytes(path.read_bytes())
    (tmp_path / f'migrate_{len(original()) + 1:04}_failure.sql').write_text('CREATE TABLE should_rollback(id int); SELECT 1/0;')
    monkeypatch.setattr(db_admin, 'migration_manifest', lambda: original(tmp_path))
    with pytest.raises(psycopg2.Error):
        db_admin.migrate(conn)
    assert rows(conn, "SELECT to_regclass('should_rollback')") == [(None,)]
    assert len(rows(conn, 'SELECT * FROM schema_migration')) == len(original())


def test_checksum_drift_refuses_upgrade(maintenance_db):
    conn, _ = maintenance_db
    db_admin.initialize(conn)
    rows(conn, "UPDATE schema_migration SET checksum='bad' WHERE version='0001'")
    with pytest.raises(db_admin.MaintenanceError, match='checksum'):
        db_admin.migrate(conn)


def test_bootstrap_is_explicit_and_never_overwrites_admin(maintenance_db):
    conn, _ = maintenance_db
    db_admin.initialize(conn)
    with pytest.raises(db_admin.MaintenanceError):
        db_admin.bootstrap_admin(conn, 'owner', 'short-pass')
    db_admin.bootstrap_admin(conn, 'owner', 'synthetic-only-password')
    data = rows(conn, 'SELECT username,password,role FROM sys_user')
    assert data[0][0] == 'owner' and data[0][2] == 1
    assert verify_password('synthetic-only-password', data[0][1])
    with pytest.raises(db_admin.MaintenanceError):
        db_admin.bootstrap_admin(conn, 'other', 'another-test-password')
    assert len(rows(conn, 'SELECT * FROM sys_user')) == 1


def test_seed_requires_explicit_command_empty_identities_and_development(maintenance_db, monkeypatch):
    conn, _ = maintenance_db
    db_admin.initialize(conn)
    with pytest.raises(psycopg2.Error):
        rows(conn, (db_admin.SQL_ROOT / 'seed_demo.sql').read_text())
    monkeypatch.setattr(settings, 'ENV', 'production')
    with pytest.raises(db_admin.MaintenanceError):
        db_admin.seed_demo(conn)
    assert rows(conn, 'SELECT * FROM sys_user') == []
    monkeypatch.setattr(settings, 'ENV', 'development')
    db_admin.seed_demo(conn)
    with pytest.raises(psycopg2.Error):
        db_admin.seed_demo(conn)


def test_cli_without_explicit_command_never_connects(tmp_path):
    script = Path(__file__).resolve().parents[1] / 'database init/db_init.py'
    result = subprocess.run([sys.executable, str(script)], cwd=tmp_path, capture_output=True, text=True, timeout=15)
    assert result.returncode == 2 and 'usage:' in result.stderr


def test_wrong_target_refused_before_connect(monkeypatch):
    monkeypatch.setattr(settings, 'DATABASE_URL', 'postgresql+asyncpg://invalid/expected')
    monkeypatch.setattr(psycopg2, 'connect', lambda **kw: pytest.fail('must not connect to an unconfirmed target'))
    with pytest.raises(db_admin.MaintenanceError):
        db_admin.connect_target('other')


def test_maintenance_lock_is_cross_connection_and_transaction_scoped(maintenance_db):
    conn, uri = maintenance_db
    other = psycopg2.connect(uri)
    try:
        with conn.cursor() as cur:
            db_admin.maintenance_lock(cur)
        with other, other.cursor() as cur:
            cur.execute('SELECT pg_try_advisory_xact_lock(%s)', (db_admin.MAINTENANCE_LOCK,))
            assert cur.fetchone() == (False,)
        conn.rollback()
        with other, other.cursor() as cur:
            cur.execute('SELECT pg_try_advisory_xact_lock(%s)', (db_admin.MAINTENANCE_LOCK,))
            assert cur.fetchone() == (True,)
    finally:
        conn.rollback()
        other.close()


def test_successful_new_migration_is_not_replayed(maintenance_db, tmp_path, monkeypatch):
    conn, _ = maintenance_db
    db_admin.initialize(conn)
    original = db_admin.migration_manifest
    for path, _ in original().values():
        (tmp_path / path.name).write_bytes(path.read_bytes())
    (tmp_path / f'migrate_{len(original()) + 1:04}_success.sql').write_text('CREATE TABLE once_only(id int); INSERT INTO once_only VALUES (1);')
    monkeypatch.setattr(db_admin, 'migration_manifest', lambda: original(tmp_path))
    db_admin.migrate(conn)
    db_admin.migrate(conn)
    assert rows(conn, 'SELECT * FROM once_only') == [(1,)]
    assert len(rows(conn, 'SELECT * FROM schema_migration')) == len(original()) + 1


def legacy_0008(conn):
    """Remove post-0008 structures to exercise adoption against a real historical column set."""
    rows(conn, 'DROP TABLE refund_send_stop; DROP FUNCTION codemax_check_refund_send_stop(); DROP TABLE refund_authorization; DROP FUNCTION codemax_check_refund_authorization(); DROP TABLE refund_request; DROP FUNCTION codemax_check_refund_request(); DROP TABLE refund_receipt; DROP FUNCTION codemax_check_full_refund(); DROP TABLE payment_event; DROP TABLE payment_receipt; DROP TABLE schema_migration; '
               'DROP FUNCTION codemax_freeze_order_contract() CASCADE; '
               'DROP FUNCTION codemax_append_only_evidence() CASCADE')
    for column in ('payment_mode', 'merchant_id', 'app_id', 'currency', 'delivery_key', 'delivery_digest', 'delivery_size'):
        rows(conn, f'ALTER TABLE sys_order DROP COLUMN {column}')


def test_manifest_requires_current_refund_preparation_migration(tmp_path):
    for version, (path, _) in db_admin.migration_manifest().items():
        if int(version) < 12:
            (tmp_path / path.name).write_bytes(path.read_bytes())
    with pytest.raises(db_admin.MaintenanceError):
        db_admin.migration_manifest(tmp_path)
