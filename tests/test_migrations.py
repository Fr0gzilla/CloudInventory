import sqlite3
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.pool import StaticPool

from app import db
from tests.test_models import SCHEMA, extract_checks, inspect_schema


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
            assert migrated_schema == inspect_schema(reference) == orm_schema
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
