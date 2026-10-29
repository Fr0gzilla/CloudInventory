import pytest
from alembic import command
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.pool import StaticPool

from tests.test_migrations import migration_config
from tests.test_models import SCHEMA


@pytest.fixture(params=["published", "migrated"])
def schema_connection(request, monkeypatch):
    engine = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
    try:
        with engine.begin() as connection:
            connection.exec_driver_sql("PRAGMA foreign_keys=ON")
            if request.param == "published":
                connection.connection.driver_connection.executescript(SCHEMA.read_text(encoding="utf-8"))
            else:
                command.upgrade(migration_config(connection), "head")
            yield connection
    finally:
        engine.dispose()


def seed_account(connection):
    connection.execute(text("INSERT INTO auth_origin (origin_code) VALUES ('local'), ('LDAP'), ('AD')"))
    connection.execute(text("INSERT INTO role (role_code) VALUES ('admin')"))
    connection.execute(text("INSERT INTO account (id, origin_code, role_code, username, local_password_hash) VALUES (1, 'local', 'admin', 'operator', 'temporary-hash')"))


def test_local_accounts_require_nonempty_hash(schema_connection):
    connection = schema_connection
    seed_account(connection)
    for hash_value in (None, "   "):
        with pytest.raises(IntegrityError):
            connection.execute(text("INSERT INTO account (origin_code, role_code, username, local_password_hash) VALUES ('local', 'admin', 'other', :hash)"), {"hash": hash_value})


def test_remote_accounts_reject_local_hash(schema_connection):
    connection = schema_connection
    seed_account(connection)
    with pytest.raises(IntegrityError):
        connection.execute(text("INSERT INTO account (origin_code, role_code, username, local_password_hash) VALUES ('LDAP', 'admin', 'remote', 'not-allowed')"))


def test_origin_and_username_form_unique_identity(schema_connection):
    connection = schema_connection
    seed_account(connection)
    connection.execute(text("INSERT INTO account (origin_code, role_code, username) VALUES ('LDAP', 'admin', 'operator')"))
    assert connection.execute(text("SELECT origin_code FROM account WHERE username='operator' ORDER BY origin_code")).scalars().all() == ["LDAP", "local"]
    with pytest.raises(IntegrityError):
        connection.execute(text("INSERT INTO account (origin_code, role_code, username) VALUES ('LDAP', 'admin', 'operator')"))


@pytest.mark.parametrize("removal", ["account", "run"])
def test_audit_survives_account_removal_or_run_purge(schema_connection, removal):
    connection = schema_connection
    seed_account(connection)
    connection.execute(text("INSERT INTO run (id, status, started_at) VALUES (1, 'SUCCESS', '2026-01-01')"))
    connection.execute(text("INSERT INTO audit (id, account_id, actor_identity, action, created_at, target) VALUES (1, 1, 'local:operator', 'view', '2026-01-02', 'run:1')"))
    connection.execute(text(f"DELETE FROM {removal} WHERE id=1"))
    row = connection.execute(text("SELECT account_id, actor_identity, action, created_at, target FROM audit WHERE id=1")).one()
    assert row == ((None if removal == "account" else 1), "local:operator", "view", "2026-01-02", "run:1")


def test_upgrade_from_0002_preserves_historical_rows_and_null_sources():
    engine = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
    try:
        with engine.begin() as connection:
            connection.exec_driver_sql("PRAGMA foreign_keys=ON")
            config = migration_config(connection)
            command.upgrade(config, "0002_model_v22")
            connection.execute(text("INSERT INTO run (id, status, started_at) VALUES (1, 'SUCCESS', '2026-01-01')"))
            connection.execute(text("INSERT INTO asset (id, run_id, vm_id) VALUES (1, 1, 'historical-vm')"))
            connection.execute(text("INSERT INTO ipam_record (id, run_id, ip) VALUES (1, 1, '192.0.2.1')"))
            connection.execute(text("INSERT INTO anomaly (id, run_id, code, description, created_at, severity) VALUES (1, 1, 'NO_MATCH', 'historical event', '2026-01-01', 'mineure')"))
            command.upgrade(config, "head")
            assert connection.execute(text("SELECT vm_id, virtual_source FROM asset WHERE id=1")).one() == ("historical-vm", None)
            assert connection.execute(text("SELECT ip, ipam_source FROM ipam_record WHERE id=1")).one() == ("192.0.2.1", None)
            assert connection.execute(text("SELECT description FROM anomaly WHERE id=1")).scalar_one() == "historical event"
            assert connection.execute(text("SELECT status FROM run WHERE id=1")).scalar_one() == "SUCCESS"
            assert connection.exec_driver_sql("PRAGMA foreign_key_check").fetchall() == []
            assert connection.exec_driver_sql("PRAGMA integrity_check").scalar_one() == "ok"
            assert connection.execute(text("SELECT count(*) FROM account")).scalar_one() == 0
    finally:
        engine.dispose()
