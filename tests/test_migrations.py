import sqlite3
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.pool import StaticPool

from app import db
from tests.test_models import SCHEMA, extract_checks, inspect_schema, published_schema_with_session_version


ROOT = Path(__file__).resolve().parents[1]


def migration_config(connection):
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "migrations"))
    config.attributes["connection"] = connection
    return config


def test_baseline_matches_published_sql_and_orm(orm_app):
    reference = sqlite3.connect(":memory:")
    engine = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
    try:
        reference.executescript(SCHEMA.read_text(encoding="utf-8"))
        with engine.begin() as connection:
            config = migration_config(connection)
            command.upgrade(config, "head")
            migrated = connection.connection.driver_connection
            assert migrated.execute("SELECT version_num FROM alembic_version").fetchone()[0] != "0002_model_v22"
            migrated_schema = inspect_schema(migrated)
            migrated_schema.pop("alembic_version")
            with db.engine.connect() as orm_connection:
                orm_schema = inspect_schema(orm_connection.connection.driver_connection)
            assert migrated_schema == published_schema_with_session_version(reference) == orm_schema
            command.downgrade(config, "base")
            assert not (set(inspect_schema(migrated)) - {"alembic_version"})
    finally:
        engine.dispose()
        reference.close()


@pytest.mark.parametrize("difference", ["column", "index"])
def test_schema_comparison_rejects_missing_ipam_fields(difference):
    reference = sqlite3.connect(":memory:")
    changed = sqlite3.connect(":memory:")
    try:
        sql = SCHEMA.read_text(encoding="utf-8")
        reference.executescript(sql)
        if difference == "column":
            sql = sql.replace("    custom_fields TEXT,\n", "")
        else:
            sql = sql.replace("CREATE INDEX idx_ipam_run_zone ON ipam_record(run_id, meta_zone);", "")
        assert sql != SCHEMA.read_text(encoding="utf-8")
        changed.executescript(sql)
        assert inspect_schema(changed) != inspect_schema(reference)
    finally:
        changed.close()
        reference.close()


def test_schema_comparison_accepts_reordered_columns_and_constraints():
    original = sqlite3.connect(":memory:")
    reordered = sqlite3.connect(":memory:")
    try:
        original.execute("CREATE TABLE example (id INTEGER PRIMARY KEY, label TEXT NOT NULL, CHECK (length(trim(label)) > 0), UNIQUE (label))")
        reordered.execute("CREATE TABLE example (label TEXT NOT NULL, id INTEGER PRIMARY KEY, UNIQUE (label), CHECK (length(trim(label)) > 0))")
        assert inspect_schema(original) == inspect_schema(reordered)
    finally:
        original.close()
        reordered.close()


def test_schema_comparison_detects_changed_column_attribute_and_check():
    original = sqlite3.connect(":memory:")
    changed = sqlite3.connect(":memory:")
    try:
        original.execute("CREATE TABLE example (id INTEGER PRIMARY KEY, label TEXT NOT NULL DEFAULT 'x', CHECK (length(trim(label)) > 0))")
        changed.execute("CREATE TABLE example (id INTEGER PRIMARY KEY, label TEXT DEFAULT 'y', CHECK (length(trim(label)) >= 0))")
        assert inspect_schema(original) != inspect_schema(changed)
        assert extract_checks(original.execute("SELECT sql FROM sqlite_master WHERE name='example'").fetchone()[0]) != extract_checks(changed.execute("SELECT sql FROM sqlite_master WHERE name='example'").fetchone()[0])
    finally:
        original.close()
        changed.close()


def test_upgrade_preserves_baseline_rows_and_accepts_renamed(monkeypatch):
    monkeypatch.setenv("ANOMALY_SEVERITIES", '{"NO_MATCH": "mineure"}')
    engine = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
    try:
        with engine.begin() as connection:
            config = migration_config(connection)
            command.upgrade(config, "0001_baseline")
            connection.execute(text("INSERT INTO run (id, status, started_at) VALUES (1, 'SUCCESS', '2026-01-01')"))
            connection.execute(text("INSERT INTO asset (id, run_id, vm_id, vm_name) VALUES (1, 1, 'vm-1', 'old')"))
            connection.execute(text("INSERT INTO anomaly (id, run_id, asset_id, code, description, created_at) VALUES (1, 1, 1, 'NO_MATCH', 'old event', '2026-01-01')"))
            command.upgrade(config, "head")
            assert connection.execute(text("SELECT vm_name FROM asset WHERE id=1")).scalar_one() == "old"
            assert connection.execute(text("SELECT code, description, severity FROM anomaly WHERE id=1")).one() == ("NO_MATCH", "old event", "mineure")
            connection.execute(text("INSERT INTO anomaly (run_id, asset_id, code, description, created_at, severity) VALUES (1, 1, 'RENAMED', 'old → new', '2026-01-02', 'info')"))
            with pytest.raises(IntegrityError):
                connection.execute(text("INSERT INTO anomaly (run_id, code, description, created_at, severity) VALUES (1, 'INVALID', 'bad', '2026-01-02', 'info')"))
    finally:
        engine.dispose()


def test_upgrade_v23_filled_database_preserves_rows_and_enforces_v30_constraints():
    engine = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
    try:
        with engine.begin() as connection:
            config = migration_config(connection)
            command.upgrade(config, "0003_model_v23")
            raw = connection.connection.driver_connection
            raw.execute("PRAGMA foreign_keys=ON")
            rows = {
                "run_schedule": ("id, frequency", (11, "horaire")),
                "run": ("id, status, started_at, schedule_id", (12, "SUCCESS", "2026-01-01", 11)),
                "asset": ("id, run_id, vm_id, virtual_source", (13, 12, "vm-13", "proxmox")),
                "ipam_record": ("id, run_id, ipam_source", (14, 12, "ipam")),
                "consolidated_asset": ("id, run_id, asset_id, ipam_record_id, match_status", (15, 12, 13, 14, "MATCHED_IP")),
                "anomaly": ("id, run_id, asset_id, code, description, created_at, severity", (16, 12, 13, "NO_MATCH", "historical", "2026-01-01", "info")),
                "anomaly_exception": ("id, code, target, justification, expires_at, author", (17, "NO_MATCH", "vm-13", "reason", "2027-01-01", "tester")),
                "auth_origin": ("origin_code", ("local",)),
                "role": ("role_code", ("admin",)),
                "permission": ("permission_code", ("read",)),
                "account": ("id, origin_code, role_code, username, local_password_hash", (18, "local", "admin", "tester", "hash")),
                "role_permission": ("id, role_code, permission_code", (19, "admin", "read")),
                "audit": ("id, account_id, actor_identity, action, created_at, target", (20, 18, "tester", "read", "2026-01-01", "vm-13")),
            }
            for table, (columns, values) in rows.items():
                placeholders = ", ".join("?" for _ in values)
                raw.execute(f"INSERT INTO {table} ({columns}) VALUES ({placeholders})", values)
            before = {table: raw.execute(f"SELECT {columns} FROM {table}").fetchall() for table, (columns, _) in rows.items()}
            command.upgrade(config, "0004_model_v30")
            assert {table: raw.execute(f"SELECT {columns} FROM {table}").fetchall() for table, (columns, _) in rows.items()} == before
            for table, (columns, values) in rows.items():
                assert raw.execute(f"SELECT {columns} FROM {table}").fetchone() == values
            reference = sqlite3.connect(":memory:")
            try:
                reference.executescript(SCHEMA.read_text(encoding="utf-8"))
                schema = inspect_schema(raw)
                schema.pop("alembic_version")
                assert schema == inspect_schema(reference)
            finally:
                reference.close()
            raw.commit()
            raw.execute("PRAGMA foreign_keys=ON")
            assert raw.execute("PRAGMA foreign_keys").fetchone() == (1,)
            assert raw.execute("PRAGMA foreign_key_check").fetchall() == []
            assert raw.execute("PRAGMA integrity_check").fetchall() == [("ok",)]
            for statement, values in (
                ("INSERT INTO asset (run_id, vm_id, disk_usage_percent) VALUES (?, ?, ?)", (12, "bad", 101)),
                ("INSERT INTO anomaly (run_id, code, description, created_at) VALUES (?, ?, ?, ?)", (12, "INVALID", "bad", "now")),
                ("INSERT INTO snapshot_observation (run_id, asset_id) VALUES (?, ?)", (12, 999)),
                ("INSERT INTO node_capacity_observation (run_id, node_capacity_name) VALUES (?, ?)", (999, "node")),
            ):
                with pytest.raises(sqlite3.IntegrityError):
                    raw.execute(statement, values)
    finally:
        engine.dispose()


def test_upgrade_from_filled_v30_preserves_rows_and_initializes_session_version():
    engine = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
    try:
        with engine.begin() as connection:
            config = migration_config(connection)
            command.upgrade(config, "0004_model_v30")
            raw = connection.connection.driver_connection
            raw.execute("PRAGMA foreign_keys=ON")
            raw.execute("INSERT INTO auth_origin (origin_code) VALUES ('local')")
            raw.execute("INSERT INTO role (role_code) VALUES ('admin')")
            raw.execute("INSERT INTO account (id, origin_code, role_code, username, local_password_hash) VALUES (41, 'local', 'admin', 'existing', 'hash')")
            raw.execute("INSERT INTO audit (id, account_id, actor_identity, action, created_at, target) VALUES (42, 41, 'existing', 'login', '2026-01-01', 'account')")
            before = raw.execute("SELECT id, origin_code, role_code, username, local_password_hash FROM account WHERE id=41").fetchone()
            audit_before = raw.execute("SELECT id, account_id, actor_identity, action, created_at, target FROM audit WHERE id=42").fetchone()
            command.upgrade(config, "head")
            assert raw.execute("SELECT id, origin_code, role_code, username, local_password_hash FROM account WHERE id=41").fetchone() == before
            assert raw.execute("SELECT id, account_id, actor_identity, action, created_at, target FROM audit WHERE id=42").fetchone() == audit_before
            assert raw.execute("SELECT session_version FROM account WHERE id=41").fetchone() == (0,)
            column = next(row for row in raw.execute("PRAGMA table_info(account)") if row[1] == "session_version")
            assert (column[2], column[3], column[4]) == ("INTEGER", 1, "'0'")
            assert db.metadata.tables["account"].c.session_version.default.arg == 0
            raw.execute("INSERT INTO account (origin_code, role_code, username, local_password_hash) VALUES ('local', 'admin', 'new', 'hash')")
            assert raw.execute("SELECT session_version FROM account WHERE username='new'").fetchone() == (0,)
            with pytest.raises(sqlite3.IntegrityError):
                raw.execute("UPDATE account SET session_version=NULL WHERE id=41")
            assert raw.execute("PRAGMA foreign_key_check").fetchall() == []
            assert raw.execute("PRAGMA integrity_check").fetchall() == [("ok",)]
    finally:
        engine.dispose()
