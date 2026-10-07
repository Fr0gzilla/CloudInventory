"""C3 — Unicité des clés métier et upsert RG18.

RG18 (docs/modele/regles.md) : l'upsert s'effectue par vm_id pour Asset et par
ip+dns_name pour IpamRecord. Un doublon de clé est rejeté par la contrainte
UNIQUE, une seconde écriture sur la même clé met la ligne à jour (pas de 2e ligne).
"""
import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.extensions import db
from app.models import Asset, IpamRecord


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


def test_ipam_record_same_ip_with_other_dns_is_rejected(db):
    """uk_ipam_record_ip : une adresse IP n'existe qu'une seule fois."""
    db.session.add(IpamRecord(ip="10.0.0.1", dns_name="host-a"))
    db.session.commit()

    with pytest.raises(IntegrityError) as excinfo:
        db.session.add(IpamRecord(ip="10.0.0.1", dns_name="host-b"))
        db.session.flush()

    assert "UNIQUE constraint failed: ipam_record.ip" in str(excinfo.value.orig)
    db.session.rollback()


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
