"""C3 — Unicité des clés métier et upsert RG18.

RG18 (docs/modele/regles.md) : l'upsert s'effectue par vm_id pour Asset et par
ip+dns_name pour IpamRecord. Un doublon de clé est rejeté par la contrainte
UNIQUE, une seconde écriture sur la même clé met la ligne à jour (pas de 2e ligne).
T024 : la réécriture d'un Asset dans un nouveau run repointe
`consolidated_run_id` vers ce run (une seule ligne par vm_id, un seul run).
"""
from datetime import datetime

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.extensions import db
from app.models import Asset, IpamRecord, Run

NOW = datetime(2026, 1, 1, 12, 0, 0)


def upsert_asset(session, vm_id, **fields):
    """Insert ou met à jour un Asset identifié par vm_id (RG18)."""
    asset = session.scalar(select(Asset).where(Asset.vm_id == vm_id))
    if asset is None:
        asset = Asset(vm_id=vm_id, **fields)
        session.add(asset)
    else:
        for column, value in fields.items():
            setattr(asset, column, value)
    session.commit()
    return asset


def upsert_ipam_record(session, ip, dns_name, **fields):
    """Insert ou met à jour un IpamRecord identifié par ip+dns_name (RG18)."""
    record = session.scalar(
        select(IpamRecord).where(
            IpamRecord.ip == ip, IpamRecord.dns_name == dns_name
        )
    )
    if record is None:
        record = IpamRecord(ip=ip, dns_name=dns_name, **fields)
        session.add(record)
    else:
        for column, value in fields.items():
            setattr(record, column, value)
    session.commit()
    return record


def test_asset_duplicate_vm_id_is_rejected(db, run):
    db.session.add(Asset(vm_id="100", vm_name="first", consolidated_run_id=run.id))
    db.session.commit()

    with pytest.raises(IntegrityError) as excinfo:
        db.session.add(
            Asset(vm_id="100", vm_name="second", consolidated_run_id=run.id)
        )
        db.session.flush()

    assert "UNIQUE constraint failed: asset.vm_id" in str(excinfo.value.orig)
    db.session.rollback()


def test_ipam_record_duplicate_ip_and_dns_is_rejected(db):
    db.session.add(IpamRecord(ip="10.0.0.1", dns_name="host-a"))
    db.session.commit()

    with pytest.raises(IntegrityError) as excinfo:
        db.session.add(IpamRecord(ip="10.0.0.1", dns_name="host-a"))
        db.session.flush()

    assert "UNIQUE constraint failed: ipam_record.ip" in str(excinfo.value.orig)
    db.session.rollback()


def test_ipam_record_same_ip_with_other_dns_is_accepted(db):
    """Deux enregistrements de même IP mais DNS différents sont acceptés."""
    db.session.add(IpamRecord(ip="10.0.0.1", dns_name="host-a"))
    db.session.commit()

    db.session.add(IpamRecord(ip="10.0.0.1", dns_name="host-b"))
    db.session.commit()

    assert db.session.query(IpamRecord).count() == 2


def test_asset_upsert_by_vm_id_updates_existing_row(db, run):
    first = upsert_asset(
        db.session, "100", vm_name="before", consolidated_run_id=run.id
    )
    second = upsert_asset(db.session, "100", vm_name="after")

    assert db.session.query(Asset).count() == 1
    assert second.id == first.id
    assert second.vm_name == "after"


def test_ipam_upsert_by_ip_and_dns_updates_existing_row(db):
    first = upsert_ipam_record(db.session, "10.0.0.1", "host-a", tenant="before")
    second = upsert_ipam_record(db.session, "10.0.0.1", "host-a", tenant="after")

    assert db.session.query(IpamRecord).count() == 1
    assert second.id == first.id
    assert second.tenant == "after"


def test_ipam_upsert_creates_one_row_per_distinct_key(db):
    upsert_ipam_record(db.session, "10.0.0.1", "host-a")
    upsert_ipam_record(db.session, "10.0.0.2", "host-b")

    assert db.session.query(IpamRecord).count() == 2


def test_asset_upsert_creates_one_row_per_distinct_vm_id(db, run):
    upsert_asset(db.session, "100", vm_name="a", consolidated_run_id=run.id)
    upsert_asset(db.session, "200", vm_name="b", consolidated_run_id=run.id)

    assert db.session.query(Asset).count() == 2


def test_asset_upsert_repoints_run_id_to_the_new_run(db, run):
    """RG18 + T024 : une nouvelle exécution réécrit l'asset sur le nouveau run."""
    other_run = Run(status="SUCCESS", start_date=NOW)
    db.session.add(other_run)
    db.session.commit()

    first = upsert_asset(
        db.session, "100", vm_name="before", consolidated_run_id=run.id
    )
    second = upsert_asset(db.session, "100", consolidated_run_id=other_run.id)

    assert db.session.query(Asset).count() == 1
    assert second.id == first.id
    assert second.consolidated_run_id == other_run.id
