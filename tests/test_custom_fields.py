import csv
import io
import json
import os

import pytest

from app import db
from app.models import IpamRecord
from collector.inventory_runner import run_inventory
from tests.test_api_inventory import new_run, observation


class Source:
    def __init__(self, records):
        self.records = records

    def collect(self):
        return self.records


def test_custom_fields_persist_for_unmatched_ipam_and_are_isolated_between_runs(orm_app):
    fields = {"meta_zone": "Étage", "nested": {"values": [True, None, 7, 1.5, "été"]}}
    source = Source([{"ip": "192.0.2.12", "dns_name": "orphan", "status": "active",
                      "tenant": None, "site": None, "tags": None, "duplicate_dns": 0,
                      "duplicate_ip": 0, "meta_zone": "Étage", "custom_fields": fields}])
    first = run_inventory(virtualization_source=Source([]), ipam_source=source)
    first_row = db.session.query(IpamRecord).filter_by(run_id=first.id).one()
    assert json.loads(first_row.custom_fields) == fields
    fields["nested"]["values"].append("changed")
    db.session.rollback()
    second = run_inventory(virtualization_source=Source([]), ipam_source=Source([dict(source.records[0])]))
    assert json.loads(db.session.query(IpamRecord).filter_by(run_id=first.id).one().custom_fields) == {
        "meta_zone": "Étage", "nested": {"values": [True, None, 7, 1.5, "été"]}}
    assert db.session.query(IpamRecord).filter_by(run_id=second.id).count() == 1


@pytest.mark.parametrize("value,expected", [(None, None), ({}, {})])
def test_custom_fields_distinguish_null_from_empty_object(orm_app, value, expected):
    source = Source([{"ip": "192.0.2.1", "dns_name": "orphan", "status": None,
                      "tenant": None, "site": None, "tags": None, "duplicate_dns": 0,
                      "duplicate_ip": 0, "meta_zone": None, "custom_fields": value}])
    run = run_inventory(virtualization_source=Source([]), ipam_source=source)
    stored = db.session.query(IpamRecord).filter_by(run_id=run.id).one().custom_fields
    assert (None if stored is None else json.loads(stored)) == expected


@pytest.fixture
def authorized(orm_app):
    client = orm_app.test_client()
    token = client.post("/api/login", json={
        "username": "admin", "password": os.environ["ADMIN_PASSWORD"]
    }).get_json()["access_token"]
    return client, {"Authorization": f"Bearer {token}"}


@pytest.fixture
def zones(authorized):
    client, headers = authorized
    old = new_run()
    observation(old, "old", ipam=True)
    db.session.query(IpamRecord).filter_by(run_id=old.id).one().meta_zone = "Étage"
    latest = new_run()
    for name, zone, node in (("alpha", "Étage", "node-a"),
                             ("beta", "étage", "node-a"),
                             ("gamma", "Étage", "node-b"),
                             ("delta", None, "node-a")):
        observation(latest, name, node=node, ipam=True)
        db.session.query(IpamRecord).filter_by(run_id=latest.id, dns_name=f"{name}.example").one().meta_zone = zone
    db.session.commit()
    return client, headers, latest.id


@pytest.mark.parametrize("query,expected", [
    ("meta_zone=%C3%89tage", ["alpha", "gamma"]),
    ("meta_zone=%C3%A9tage", ["beta"]),
    ("meta_zone=unknown", []),
    ("meta_zone=%C3%89tage&node=node-a", ["alpha"]),
    ("meta_zone=%C3%89tage&sort=vm_id&order=desc&per_page=1&page=2", ["alpha"]),
    ("meta_zone=", ["alpha", "beta", "delta", "gamma"]),
])
def test_zone_filter_is_exact_latest_run_and_combines_with_pagination(zones, query, expected):
    client, headers, latest_id = zones
    result = client.get(f"/api/inventory?{query}", headers=headers)
    assert (result.status_code, result.get_json()["run_id"],
            [item["vm_id"] for item in result.get_json()["items"]]) == (200, latest_id, expected)


def test_zone_export_includes_all_matches_without_pagination(zones):
    client, headers, _ = zones
    response = client.get("/api/inventory/export?meta_zone=%C3%89tage&node=node-a", headers=headers)
    assert (response.status_code, [row["vm_id"] for row in csv.DictReader(
        io.StringIO(response.get_data(as_text=True)))]) == (200, ["alpha"])


@pytest.mark.parametrize("query", ["meta_zone=a&meta_zone=b", "meta_zone=" + "a" * 256,
                                          "meta_zone=ab%00cd"])
@pytest.mark.parametrize("path", ["/api/inventory", "/api/inventory/export"])
def test_zone_filter_rejects_duplicate_oversize_and_nul(zones, path, query):
    client, headers, _ = zones
    response = client.get(f"{path}?{query}", headers=headers)
    assert (response.status_code, response.get_json()) == (400, {"error": "Invalid inventory query"})
