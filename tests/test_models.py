import re
import sqlite3
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app import db


SCHEMA = Path(__file__).resolve().parents[1] / "docs/modele/schema.sql"
TABLES = {"run_schedule", "run", "asset", "ipam_record", "consolidated_asset", "anomaly", "anomaly_exception", "auth_origin", "role", "permission", "account", "role_permission", "audit"}


def extract_checks(sql):
    checks = []
    for match in re.finditer(r"\bCHECK\s*\(", sql, re.IGNORECASE):
        start = match.end() - 1
        depth = 0
        quote = None
        position = start
        while position < len(sql):
            character = sql[position]
            if quote:
                if character == quote:
                    if position + 1 < len(sql) and sql[position + 1] == quote:
                        position += 2
                        continue
                    quote = None
            elif character in ("'", '"'):
                quote = character
            elif character == "(":
                depth += 1
            elif character == ")":
                depth -= 1
                if depth == 0:
                    checks.append(re.sub(r"\s+", "", sql[start:position + 1]).lower())
                    break
            position += 1
        else:
            raise ValueError("Unclosed CHECK constraint")
    return sorted(checks)


def inspect_schema(connection):
    tables = {
        row[0]
        for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
        if not row[0].startswith("sqlite_")
    }
    result = {}
    for table in tables:
        columns = {row[1]: tuple(row[2:6]) for row in connection.execute(f"PRAGMA table_info('{table}')")}
        foreign = {
            (row[0], row[2], row[6].upper()): []
            for row in connection.execute(f"PRAGMA foreign_key_list('{table}')")
        }
        for row in connection.execute(f"PRAGMA foreign_key_list('{table}')"):
            foreign[row[0], row[2], row[6].upper()].append((row[1], row[3], row[4]))
        indexes = set()
        for row in connection.execute(f"PRAGMA index_list('{table}')"):
            name, unique, origin = row[1], row[2], row[3]
            indexed = tuple(
                item[2] for item in connection.execute(f"PRAGMA index_info('{name}')")
            )
            indexes.add((name if origin == "c" else origin, unique, indexed))
        sql = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (table,)
        ).fetchone()[0]
        checks = extract_checks(sql)
        result[table] = (columns, foreign, indexes, checks)
    return result


def insert(table, **values):
    fields = ", ".join(values)
    parameters = ", ".join(f":{key}" for key in values)
    db.session.execute(text(f"INSERT INTO {table} ({fields}) VALUES ({parameters})"), values)
    db.session.commit()


def rejected(table, **values):
    with pytest.raises(IntegrityError):
        insert(table, **values)
    db.session.rollback()


def run(identifier=1):
    insert("run", id=identifier, status="RUNNING", started_at="2026-01-01")


def test_thirteen_tables_match_reference_schema(orm_app):
    reference = sqlite3.connect(":memory:")
    try:
        reference.executescript(SCHEMA.read_text(encoding="utf-8"))
        with db.engine.connect() as connection:
            actual = inspect_schema(connection.connection.driver_connection)
        assert set(actual) == TABLES
        assert actual == inspect_schema(reference)
    finally:
        reference.close()


def test_foreign_keys_enabled_on_each_connection(orm_app):
    db.session.remove()
    with db.engine.connect() as connection:
        assert connection.exec_driver_sql("PRAGMA foreign_keys").scalar() == 1


@pytest.mark.parametrize("table,values", [
    ("run", {"id": 1, "status": "INVALID", "started_at": "now"}),
    ("run", {"id": 1, "status": "RUNNING", "started_at": "now", "matched_ip_count": -1}),
    ("ipam_record", {"id": 1, "run_id": 1, "duplicate_ip": 2}),
    ("asset", {"id": 1, "run_id": 1, "vm_id": "vm", "type": "bad"}),
    ("consolidated_asset", {"id": 1, "run_id": 1, "asset_id": 1, "match_status": "bad"}),
    ("anomaly", {"id": 1, "run_id": 1, "code": "bad", "description": "x", "created_at": "now"}),
    ("run_schedule", {"id": 1, "frequency": "bad"}),
    ("anomaly_exception", {"id": 1, "code": "bad", "target": "vm", "justification": "reason", "expires_at": "now", "author": "tester"}),
])
def test_invalid_check_rejected(orm_app, table, values):
    if table != "run":
        run()
    if table == "consolidated_asset":
        insert("asset", id=1, run_id=1, vm_id="vm")
    rejected(table, **values)


def test_missing_required_vm_id_rejected(orm_app):
    run()
    rejected("asset", id=1, run_id=1)


def test_missing_run_rejected_by_foreign_key(orm_app):
    rejected("asset", id=1, run_id=987, vm_id="vm")


def test_duplicate_consolidation_rejected(orm_app):
    run()
    insert("asset", id=1, run_id=1, vm_id="vm")
    insert("consolidated_asset", id=1, run_id=1, asset_id=1, match_status="NO_MATCH")
    rejected("consolidated_asset", id=2, run_id=1, asset_id=1, match_status="NO_MATCH")


@pytest.mark.parametrize("table,field", [("consolidated_asset", "asset_id"),
                                          ("consolidated_asset", "ipam_record_id"),
                                          ("anomaly", "asset_id"),
                                          ("anomaly", "ipam_record_id")])
def test_cross_run_composite_reference_rejected(orm_app, table, field):
    run(1)
    run(2)
    insert("asset", id=1, run_id=1, vm_id="vm")
    insert("asset", id=2, run_id=2, vm_id="other")
    insert("ipam_record", id=1, run_id=2)
    values = {"id": 1, "run_id": 1, field: 2 if field == "asset_id" else 1}
    if table == "consolidated_asset":
        values.update(asset_id=1, match_status="NO_MATCH")
        values[field] = 2 if field == "asset_id" else 1
    else:
        values.update(code="NO_MATCH", description="x", created_at="now", severity="info")
    rejected(table, **values)


@pytest.mark.parametrize("parent,child", [("asset", "anomaly"),
                                           ("ipam_record", "anomaly"),
                                           ("ipam_record", "consolidated_asset")])
def test_referenced_parent_delete_restricted(orm_app, parent, child):
    run()
    insert("asset", id=1, run_id=1, vm_id="vm")
    insert("ipam_record", id=1, run_id=1)
    if child == "anomaly":
        insert(child, id=1, run_id=1, **{parent + "_id": 1},
                code="NO_MATCH", description="x", created_at="now", severity="info")
    else:
        insert(child, id=1, run_id=1, asset_id=1, ipam_record_id=1,
               match_status="NO_MATCH")
    with pytest.raises(IntegrityError):
        db.session.execute(text(f"DELETE FROM {parent} WHERE id=1"))
        db.session.commit()
    db.session.rollback()


def test_unblocked_run_delete_cascades_children(orm_app):
    run()
    insert("asset", id=1, run_id=1, vm_id="vm")
    insert("ipam_record", id=1, run_id=1)
    insert("consolidated_asset", id=1, run_id=1, asset_id=1, match_status="NO_MATCH")
    insert("anomaly", id=1, run_id=1, code="NO_MATCH", description="x", created_at="now", severity="info")
    db.session.execute(text("DELETE FROM run WHERE id=1"))
    db.session.commit()
    assert all(db.session.execute(text(f"SELECT count(*) FROM {table}")).scalar() == 0
               for table in ("run", "asset", "ipam_record", "consolidated_asset", "anomaly"))


def test_database_supplies_ipam_flag_defaults(orm_app):
    run()
    insert("ipam_record", id=1, run_id=1)
    flags = db.session.execute(text(
        "SELECT duplicate_dns, duplicate_ip FROM ipam_record WHERE id=1"
    )).one()
    assert tuple(flags) == (0, 0)
