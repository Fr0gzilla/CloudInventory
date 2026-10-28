import sqlite3
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

from app import db
from tests.test_models import SCHEMA, inspect_schema


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
            assert migrated.execute("SELECT version_num FROM alembic_version").fetchone()[0] == "0001_baseline"
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
