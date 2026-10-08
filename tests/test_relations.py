"""C2 — Relations étrangères (7 FK) et relations ORM des 5 modèles.

Les FK déclarées dans schema.sql existent bien, SQLite les applique
(PRAGMA foreign_keys=ON) : rejet d'une clé inconnue, RESTRICT sur run/asset/
ipam_record, CASCADE de consolidated_asset quand l'asset part.
`consolidated_asset.run_id` (RG35) rattache la ligne au run producteur,
indépendamment de `asset.consolidated_run_id` (T024).
"""
from datetime import datetime

import pytest
from sqlalchemy import delete as sa_delete
from sqlalchemy import inspect
from sqlalchemy.exc import IntegrityError

from app.extensions import db
from app.models import Anomaly, Asset, ConsolidatedAsset, IpamRecord, Run

NOW = datetime(2026, 1, 1, 12, 0, 0)

# table -> {colonne -> table référencée} (docs/modele/schema.sql)
EXPECTED_FKS = {
    "asset": {("consolidated_run_id", "run")},
    "consolidated_asset": {
        ("run_id", "run"),
        ("asset_id", "asset"),
        ("ipam_record_id", "ipam_record"),
    },
    "anomaly": {
        ("run_id", "run"),
        ("asset_id", "asset"),
        ("ipam_record_id", "ipam_record"),
    },
}


def test_seven_foreign_keys_declared(db):
    inspector = inspect(db.engine)
    total = 0

    for table, expected in EXPECTED_FKS.items():
        found = {
            (column, fk["referred_table"])
            for fk in inspector.get_foreign_keys(table)
            for column in fk["constrained_columns"]
        }
        assert found == expected, table
        total += len(found)

    assert total == 7


def test_consolidated_asset_with_unknown_run_is_rejected(db, asset):
    """RG35 : run_id est une FK réelle, pas un simple entier."""
    with pytest.raises(IntegrityError) as excinfo:
        db.session.add(
            ConsolidatedAsset(
                run_id=999999, asset_id=asset.id, match_status="MATCHED_NAME"
            )
        )
        db.session.flush()

    assert "FOREIGN KEY constraint failed" in str(excinfo.value.orig)
    db.session.rollback()


def test_asset_with_unknown_run_is_rejected(db):
    with pytest.raises(IntegrityError) as excinfo:
        db.session.add(
            Asset(vm_id="vm-404", vm_name="ghost", consolidated_run_id=999999)
        )
        db.session.flush()

    assert "FOREIGN KEY constraint failed" in str(excinfo.value.orig)
    db.session.rollback()


def test_run_delete_is_restricted_by_asset(db, run, asset):
    with pytest.raises(IntegrityError) as excinfo:
        db.session.execute(sa_delete(Run).where(Run.id == run.id))
        db.session.commit()

    assert "FOREIGN KEY constraint failed" in str(excinfo.value.orig)
    db.session.rollback()


def test_run_delete_is_restricted_by_consolidated_asset(db, run, asset):
    db.session.add(
        ConsolidatedAsset(
            run_id=run.id, asset_id=asset.id, match_status="MATCHED_NAME"
        )
    )
    db.session.commit()

    with pytest.raises(IntegrityError) as excinfo:
        db.session.execute(sa_delete(Run).where(Run.id == run.id))
        db.session.commit()

    assert "FOREIGN KEY constraint failed" in str(excinfo.value.orig)
    db.session.rollback()


def test_run_delete_is_restricted_by_anomaly(db, run):
    db.session.add(
        Anomaly(run_id=run.id, code="NO_MATCH", detected_at=NOW)
    )
    db.session.commit()

    with pytest.raises(IntegrityError) as excinfo:
        db.session.execute(sa_delete(Run).where(Run.id == run.id))
        db.session.commit()

    assert "FOREIGN KEY constraint failed" in str(excinfo.value.orig)
    db.session.rollback()


def test_asset_delete_is_restricted_by_anomaly(db, asset, run):
    db.session.add(
        Anomaly(run_id=run.id, asset_id=asset.id, code="NO_MATCH", detected_at=NOW)
    )
    db.session.commit()

    with pytest.raises(IntegrityError) as excinfo:
        db.session.execute(sa_delete(Asset).where(Asset.id == asset.id))
        db.session.commit()

    assert "FOREIGN KEY constraint failed" in str(excinfo.value.orig)
    db.session.rollback()


def test_asset_delete_cascades_consolidated_assets(db, run, asset, ipam_record):
    db.session.add(
        ConsolidatedAsset(
            run_id=run.id,
            asset_id=asset.id,
            ipam_record_id=ipam_record.id,
            match_status="MATCHED_NAME",
        )
    )
    db.session.commit()

    db.session.execute(sa_delete(Asset).where(Asset.id == asset.id))
    db.session.commit()

    assert db.session.query(ConsolidatedAsset).count() == 0
    assert db.session.query(Asset).count() == 0


def test_ipam_record_delete_is_restricted_by_consolidated_asset(
    db, run, asset, ipam_record
):
    db.session.add(
        ConsolidatedAsset(
            run_id=run.id,
            asset_id=asset.id,
            ipam_record_id=ipam_record.id,
            match_status="MATCHED_IP",
        )
    )
    db.session.commit()

    with pytest.raises(IntegrityError) as excinfo:
        db.session.execute(
            sa_delete(IpamRecord).where(IpamRecord.id == ipam_record.id)
        )
        db.session.commit()

    assert "FOREIGN KEY constraint failed" in str(excinfo.value.orig)
    db.session.rollback()


def test_orm_relationships_are_linked(db, run, asset, ipam_record):
    consolidated = ConsolidatedAsset(
        run_id=run.id,
        asset_id=asset.id,
        ipam_record_id=ipam_record.id,
        match_status="MATCHED_IP",
    )
    anomaly = Anomaly(
        run_id=run.id,
        asset_id=asset.id,
        ipam_record_id=ipam_record.id,
        code="DUPLICATE_IP",
        detected_at=NOW,
    )
    db.session.add_all([consolidated, anomaly])
    db.session.commit()

    assert [item.id for item in run.assets] == [asset.id]
    assert [item.id for item in run.anomalies] == [anomaly.id]
    assert [item.id for item in asset.consolidated_assets] == [consolidated.id]
    assert [item.id for item in run.consolidated_assets] == [consolidated.id]
    assert consolidated.asset.id == asset.id
    assert consolidated.ipam_record.id == ipam_record.id
    assert consolidated.run.id == run.id
    assert anomaly.run.id == run.id


def test_run_consolidated_assets_ignores_asset_consolidated_run_id(db, run, asset):
    """T024 — `Run.consolidated_assets` se lit par `consolidated_asset.run_id`.

    L'asset reste rattaché à son run de collecte (`asset.consolidated_run_id`)
    alors que la ligne consolidée appartient à un autre run : les deux
    relations sont indépendantes (OF9, §8.5-8.6).
    """
    other_run = Run(status="SUCCESS", start_date=NOW)
    db.session.add(other_run)
    db.session.flush()
    consolidated = ConsolidatedAsset(
        run_id=other_run.id,
        asset_id=asset.id,
        match_status="MATCHED_NAME",
    )
    db.session.add(consolidated)
    db.session.commit()

    assert asset.consolidated_run_id == run.id
    assert consolidated.run_id == other_run.id
    assert [item.id for item in other_run.consolidated_assets] == [consolidated.id]
    assert run.consolidated_assets == []
    assert [item.id for item in asset.consolidated_assets] == [consolidated.id]
