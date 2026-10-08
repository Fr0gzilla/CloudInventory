"""C1 — Les 5 modèles : création nominale et colonnes NOT NULL.

Critère 1 de T004 (plan-dev.md, section « Points à tester ») : Run, Asset,
IpamRecord, ConsolidatedAsset et Anomaly — insertion, ids auto, valeurs par
défaut, puis colonnes NOT NULL déclarées (schema.sql) et appliquées par SQLite.
"""
from datetime import datetime

import pytest
from sqlalchemy import inspect
from sqlalchemy.exc import IntegrityError

from app.extensions import db
from app.models import Anomaly, Asset, ConsolidatedAsset, IpamRecord, Run

NOW = datetime(2026, 1, 1, 12, 0, 0)

# Colonnes NOT NULL de docs/modele/schema.sql, hors clé primaire.
EXPECTED_NOT_NULL = {
    "run": {"status", "start_date"},
    "asset": {"vm_id", "vm_name", "consolidated_run_id"},
    "ipam_record": {"ip", "dns_name"},
    "consolidated_asset": {"run_id", "asset_id", "match_status"},
    "anomaly": {"run_id", "code", "detected_at"},
}

# Colonnes ajoutées par T026 (docs/modele/schema.sql) : nom -> type attendu.
EXPECTED_T026_COLUMNS = {
    "asset": {
        "os": "VARCHAR(100)",
        "annotation": "TEXT",
        "cpu_count": "INT",
        "cpu_usage": "FLOAT",
        "ram_max": "BIGINT",
        "ram_used": "BIGINT",
        "disk_max": "BIGINT",
        "disk_used": "BIGINT",
        "uptime": "INT",
    },
    "consolidated_asset": {
        "ip_final": "VARCHAR(45)",
        "dns_final": "VARCHAR(200)",
        "vm_status": "VARCHAR(20)",
    },
}


def test_run_creation(db):
    run = Run(status="RUNNING", start_date=NOW)
    db.session.add(run)
    db.session.commit()

    assert run.id is not None
    assert run.status == "RUNNING"
    assert run.end_date is None
    assert run.no_match_count is None


def test_asset_creation(db, run):
    asset = Asset(vm_id="100", vm_name="test", consolidated_run_id=run.id)
    db.session.add(asset)
    db.session.commit()

    assert asset.id is not None
    assert asset.vm_id == "100"
    assert asset.run.id == run.id
    assert asset.fqdn is None


def test_ipam_record_creation(db):
    record = IpamRecord(ip="10.0.0.1", dns_name="host")
    db.session.add(record)
    db.session.commit()

    assert record.id is not None
    assert record.ip == "10.0.0.1"
    assert record.dns_name == "host"
    assert record.is_duplicate_dns is False
    assert record.is_duplicate_ip is False


def test_consolidated_asset_creation(db, run, asset, ipam_record):
    consolidated = ConsolidatedAsset(
        run_id=run.id,
        asset_id=asset.id,
        ipam_record_id=ipam_record.id,
        match_status="MATCHED_IP",
    )
    db.session.add(consolidated)
    db.session.commit()

    assert consolidated.id is not None
    assert consolidated.run_id == run.id
    assert consolidated.run.id == run.id
    assert consolidated.asset_id == asset.id
    assert consolidated.match_status == "MATCHED_IP"
    assert consolidated.consolidated_at is None


def test_anomaly_creation(db, run):
    anomaly = Anomaly(run_id=run.id, code="NO_MATCH", detected_at=NOW)
    db.session.add(anomaly)
    db.session.commit()

    assert anomaly.id is not None
    assert anomaly.run_id == run.id
    assert anomaly.code == "NO_MATCH"
    assert anomaly.asset_id is None
    assert anomaly.description is None


def test_not_null_columns_declared(db):
    inspector = inspect(db.engine)

    for table, expected in EXPECTED_NOT_NULL.items():
        actual = {
            column["name"]
            for column in inspector.get_columns(table)
            if not column["nullable"] and column["name"] != "id"
        }
        assert actual == expected, table


@pytest.mark.parametrize(
    "overrides",
    [
        pytest.param({"status": None}, id="run-without-status"),
        pytest.param({"start_date": None}, id="run-without-start-date"),
    ],
)
def test_run_missing_required_column_is_rejected(db, overrides):
    params = {"status": "RUNNING", "start_date": NOW}
    params.update(overrides)

    with pytest.raises(IntegrityError) as excinfo:
        db.session.add(Run(**params))
        db.session.flush()

    assert "NOT NULL constraint failed" in str(excinfo.value.orig)
    db.session.rollback()


@pytest.mark.parametrize(
    "overrides",
    [
        pytest.param({"vm_id": None}, id="asset-without-vm_id"),
        pytest.param({"vm_name": None}, id="asset-without-vm_name"),
        pytest.param(
            {"consolidated_run_id": None}, id="asset-without-consolidated_run_id"
        ),
    ],
)
def test_asset_missing_required_column_is_rejected(db, run, overrides):
    params = {"vm_id": "vm-002", "vm_name": "test", "consolidated_run_id": run.id}
    params.update(overrides)

    with pytest.raises(IntegrityError) as excinfo:
        db.session.add(Asset(**params))
        db.session.flush()

    assert "NOT NULL constraint failed" in str(excinfo.value.orig)
    db.session.rollback()


@pytest.mark.parametrize(
    "overrides",
    [
        pytest.param({"ip": None}, id="ipam-record-without-ip"),
        pytest.param({"dns_name": None}, id="ipam-record-without-dns_name"),
    ],
)
def test_ipam_record_missing_required_column_is_rejected(db, overrides):
    params = {"ip": "10.0.0.9", "dns_name": "host"}
    params.update(overrides)

    with pytest.raises(IntegrityError) as excinfo:
        db.session.add(IpamRecord(**params))
        db.session.flush()

    assert "NOT NULL constraint failed" in str(excinfo.value.orig)
    db.session.rollback()


@pytest.mark.parametrize(
    "overrides",
    [
        pytest.param({"run_id": None}, id="consolidated-without-run_id"),
        pytest.param({"asset_id": None}, id="consolidated-without-asset_id"),
        pytest.param({"match_status": None}, id="consolidated-without-match_status"),
    ],
)
def test_consolidated_asset_missing_required_column_is_rejected(
    db, run, asset, overrides
):
    params = {
        "run_id": run.id,
        "asset_id": asset.id,
        "match_status": "MATCHED_NAME",
    }
    params.update(overrides)

    with pytest.raises(IntegrityError) as excinfo:
        db.session.add(ConsolidatedAsset(**params))
        db.session.flush()

    assert "NOT NULL constraint failed" in str(excinfo.value.orig)
    db.session.rollback()


@pytest.mark.parametrize(
    "overrides",
    [
        pytest.param({"run_id": None}, id="anomaly-without-run_id"),
        pytest.param({"code": None}, id="anomaly-without-code"),
        pytest.param({"detected_at": None}, id="anomaly-without-detected_at"),
    ],
)
def test_anomaly_missing_required_column_is_rejected(db, run, overrides):
    params = {"run_id": run.id, "code": "NO_MATCH", "detected_at": NOW}
    params.update(overrides)

    with pytest.raises(IntegrityError) as excinfo:
        db.session.add(Anomaly(**params))
        db.session.flush()

    assert "NOT NULL constraint failed" in str(excinfo.value.orig)
    db.session.rollback()


def test_t026_columns_are_declared_with_expected_types(db):
    """T026 — métriques asset et finals consolidé : nom, type et nullabilité."""
    inspector = inspect(db.engine)

    for table, expected in EXPECTED_T026_COLUMNS.items():
        columns = {column["name"]: column for column in inspector.get_columns(table)}
        for name, type_name in expected.items():
            assert name in columns, f"{table}.{name} absente"
            declared = " ".join(str(columns[name]["type"]).upper().split())
            # L'inspecteur SQLAlchemy normalise tout entier 32 bits en Integer()
            # (str = "INTEGER") alors que le DDL de schema.sql écrit "INT".
            assert declared.replace("INTEGER", "INT") == type_name, (
                f"{table}.{name} = {declared}"
            )
            assert columns[name]["nullable"] is True, f"{table}.{name} NOT NULL"


def test_t026_columns_persist_collected_values(db, run, asset):
    """Valeurs collectées : métriques sur Asset, ip/dns/statut final consolidé."""
    asset.os = "Debian 12"
    asset.annotation = "gééré via T026"
    asset.cpu_count = 4
    asset.cpu_usage = 12.5
    asset.ram_max = 8192
    asset.ram_used = 2048
    asset.disk_max = 100_000
    asset.disk_used = 42_000
    asset.uptime = 86_400
    consolidated = ConsolidatedAsset(
        run_id=run.id,
        asset_id=asset.id,
        match_status="MATCHED_IP",
        ip_final="10.0.0.42",
        dns_final="web-a500.internal",
        vm_status="running",
    )
    db.session.add(consolidated)
    db.session.commit()

    assert asset.cpu_usage == 12.5 and asset.ram_used == 2048
    assert asset.uptime == 86_400 and asset.os == "Debian 12"
    assert consolidated.ip_final == "10.0.0.42"
    assert consolidated.dns_final == "web-a500.internal"
    assert consolidated.vm_status == "running"
