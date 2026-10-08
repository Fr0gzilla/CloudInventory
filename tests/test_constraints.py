"""C1 — Contraintes CHECK et index/unicités déclarés (mapping 1:1 schema.sql).

Chaque colonne sous CHECK doit accepter ses valeurs autorisées et rejeter les
autres ; les 33 index de docs/modele/schema.sql doivent exister, dont les
2 index uniques (uk_asset_vm_id, uq_ipam_record_ip_dns) : ip seule n'est pas
unique (RG13 détecte les doublons IP).
Index T026 : métriques asset (idx_asset_os … idx_asset_uptime) et finals
consolidé (idx_consolidated_asset_run_id, ip_final, dns_final, vm_status).
"""
from datetime import datetime

import pytest
from sqlalchemy import inspect
from sqlalchemy.exc import IntegrityError

from app.extensions import db
from app.models import Anomaly, Asset, ConsolidatedAsset, IpamRecord, Run

NOW = datetime(2026, 1, 1, 12, 0, 0)

# Nom d'index -> unique ? (docs/modele/schema.sql)
EXPECTED_INDEXES = {
    "run": {
        "idx_run_status": False,
        "idx_run_start_date": False,
    },
    "ipam_record": {
        "uq_ipam_record_ip_dns": True,
        "idx_ipam_record_dns_name": False,
    },
    "asset": {
        "uk_asset_vm_id": True,
        "idx_asset_consolidated_run_id": False,
        "idx_asset_vm_name": False,
        "idx_asset_fqdn": False,
        "idx_asset_ip_reported": False,
        "idx_asset_status": False,
        "idx_asset_node": False,
        "idx_asset_type": False,
        "idx_asset_match_status": False,
        "idx_asset_os": False,
        "idx_asset_annotation": False,
        "idx_asset_cpu_count": False,
        "idx_asset_cpu_usage": False,
        "idx_asset_ram_max": False,
        "idx_asset_ram_used": False,
        "idx_asset_disk_max": False,
        "idx_asset_disk_used": False,
        "idx_asset_uptime": False,
    },
    "consolidated_asset": {
        "idx_consolidated_asset_asset_id": False,
        "idx_consolidated_asset_run_id": False,
        "idx_consolidated_asset_ipam_record_id": False,
        "idx_consolidated_asset_match_status": False,
        "idx_consolidated_asset_ip_final": False,
        "idx_consolidated_asset_dns_final": False,
        "idx_consolidated_asset_vm_status": False,
    },
    "anomaly": {
        "idx_anomaly_run_id": False,
        "idx_anomaly_asset_id": False,
        "idx_anomaly_ipam_record_id": False,
        "idx_anomaly_code": False,
    },
}


def test_check_constraints_accept_valid_values(db, run, asset, ipam_record):
    """Valeurs autorisées par chaque CHECK : les 5 modèles sont persistés."""
    run.status = "SUCCESS"
    asset.node = "pve5"
    asset.type = "lxc"
    asset.status = "stopped"
    asset.match_status = "MATCHED_IP"
    asset.source = "IPAM"
    consolidated = ConsolidatedAsset(
        run_id=run.id,
        asset_id=asset.id,
        ipam_record_id=ipam_record.id,
        match_status="MATCHED_FQDN",
    )
    anomaly = Anomaly(
        run_id=run.id,
        asset_id=asset.id,
        ipam_record_id=ipam_record.id,
        code="HOSTNAME_MISMATCH",
        detected_at=NOW,
    )
    db.session.add_all([consolidated, anomaly])
    db.session.commit()

    assert asset.source == "IPAM"
    assert anomaly.code == "HOSTNAME_MISMATCH"


@pytest.mark.parametrize(
    "status",
    ["FAILED", "running", ""],
    ids=["unknown-status", "lowercase-status", "empty-status"],
)
def test_run_check_rejects_invalid_status(db, status):
    with pytest.raises(IntegrityError) as excinfo:
        db.session.add(Run(status=status, start_date=NOW))
        db.session.flush()

    assert "CHECK constraint failed" in str(excinfo.value.orig)
    db.session.rollback()


@pytest.mark.parametrize(
    ("column", "value"),
    [
        ("node", "pve9"),
        ("type", "vmware"),
        ("status", "paused"),
        ("match_status", "MAYBE"),
        ("source", "API"),
    ],
    ids=["node", "type", "status", "match-status", "source"],
)
def test_asset_check_rejects_invalid_value(db, run, column, value):
    params = {
        "vm_id": "vm-009",
        "vm_name": "bad",
        "consolidated_run_id": run.id,
        column: value,
    }

    with pytest.raises(IntegrityError) as excinfo:
        db.session.add(Asset(**params))
        db.session.flush()

    assert "CHECK constraint failed" in str(excinfo.value.orig)
    db.session.rollback()


@pytest.mark.parametrize(
    "match_status",
    ["MATCHED", "no_match"],
    ids=["unknown-match-status", "lowercase-match-status"],
)
def test_consolidated_asset_check_rejects_invalid_match_status(
    db, run, asset, match_status
):
    with pytest.raises(IntegrityError) as excinfo:
        db.session.add(
            ConsolidatedAsset(
                run_id=run.id, asset_id=asset.id, match_status=match_status
            )
        )
        db.session.flush()

    assert "CHECK constraint failed" in str(excinfo.value.orig)
    db.session.rollback()


@pytest.mark.parametrize(
    "code",
    ["UNKNOWN", "duplicate_dns"],
    ids=["unknown-code", "lowercase-code"],
)
def test_anomaly_check_rejects_invalid_code(db, run, code):
    with pytest.raises(IntegrityError) as excinfo:
        db.session.add(Anomaly(run_id=run.id, code=code, detected_at=NOW))
        db.session.flush()

    assert "CHECK constraint failed" in str(excinfo.value.orig)
    db.session.rollback()


def test_ipam_record_has_no_check_but_defaults_to_false(db):
    """ipam_record n'a pas de CHECK : les drapeaux dupliqués restent à False."""
    record = IpamRecord(ip="10.0.0.50", dns_name="host-b500", tenant="t1")
    db.session.add(record)
    db.session.commit()

    assert record.is_duplicate_dns is False
    assert record.is_duplicate_ip is False


def test_all_indexes_declared(db):
    """Les 33 index de schema.sql existent, avec le bon drapeau unique.

    Tous les écarts sont listés en une seule fois : aucun index manquant n'est
    masqué.
    """
    inspector = inspect(db.engine)
    problems = []

    for table, expected in EXPECTED_INDEXES.items():
        found = {
            index["name"]: bool(index["unique"])
            for index in inspector.get_indexes(table)
        }
        for name, unique in expected.items():
            if name not in found:
                problems.append(f"index {table}.{name} absent")
            elif found[name] is not unique:
                problems.append(f"index {table}.{name} (unique={unique})")

    assert problems == [], "; ".join(problems)
