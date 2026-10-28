import csv
import io
import os

import pytest

from app import db
from app.models import Anomaly, Asset, ConsolidatedAsset, IpamRecord, Run


CSV_FIELDS = (
    "vm_id,vm_name,type,node,status,tags,ip_reported,fqdn,os,cpu,ram,disk,uptime,"
    "ip_final,dns_final,status_final,match_status,role,ipam_ip,ipam_dns_name,"
    "ipam_status,ipam_tenant,ipam_site,ipam_tags,ipam_meta_zone"
).split(",")


@pytest.fixture
def authorized(orm_app):
    client = orm_app.test_client()
    token = client.post("/api/login", json={
        "username": "admin", "password": os.environ["ADMIN_PASSWORD"]
    }).get_json()["access_token"]
    return client, {"Authorization": f"Bearer {token}"}


def new_run(status="SUCCESS"):
    item = Run(status=status, started_at="2026-01-01T00:00:00Z")
    db.session.add(item)
    db.session.flush()
    return item


def observation(run, vm_id, *, name=None, node="node-a", kind="qemu", status="active",
                tags="team:blue", match="MATCHED_NAME", ip=None, ipam=False):
    asset = Asset(run_id=run.id, vm_id=vm_id, vm_name=name or vm_id,
                  node=node, type=kind, status=status, tags=tags, fqdn=f"{vm_id}.example",
                  ip_reported=ip, cpu=2.0)
    db.session.add(asset)
    db.session.flush()
    record = None
    if ipam:
        record = IpamRecord(run_id=run.id, ip=ip, dns_name=f"{vm_id}.example",
                            status="active", site="test-site")
        db.session.add(record)
        db.session.flush()
    db.session.add(ConsolidatedAsset(run_id=run.id, asset_id=asset.id,
                                    ipam_record_id=record.id if record else None,
                                    ip_final=ip, dns_final=f"{vm_id}.example",
                                    status_final=status, match_status=match, role="web"))
    db.session.flush()
    return asset


@pytest.fixture
def inventory_data(authorized):
    old = new_run()
    observation(old, "old-only", ip="10.0.0.1")
    latest = new_run("FAIL")
    alpha = observation(latest, "alpha", name="Alpha", ip="10.0.0.2", ipam=True)
    beta = observation(latest, "beta", name="Beta", node="node-b", kind="lxc",
                       status="stopped", tags="team:red", match="NO_MATCH", ip="10.0.0.3")
    gamma = observation(latest, "gamma", name="Gamma", ip="10.0.0.4", match="MATCHED_IP")
    db.session.commit()
    return authorized, latest, (alpha, beta, gamma)


@pytest.mark.parametrize("query,expected", [
    ("q=ALPHA", ["alpha"]), ("q=10.0.0.3", ["beta"]),
    ("status=stopped", ["beta"]), ("node=node-b", ["beta"]),
    ("type=lxc", ["beta"]), ("match=MATCHED_IP", ["gamma"]),
    ("tag=RED", ["beta"]),
    ("node=node-a&type=qemu&status=active&match=MATCHED_NAME&tag=blue&q=alpha", ["alpha"]),
    ("node=node-a&type=lxc", []),
])
def test_inventory_filters_latest_run_and_combines_predicates(inventory_data, query, expected):
    (client, headers), latest, _ = inventory_data
    response = client.get(f"/api/inventory?{query}", headers=headers)
    assert (response.status_code, response.get_json()["run_id"],
            [item["vm_id"] for item in response.get_json()["items"]]) == (200, latest.id, expected)


def test_inventory_defaults_and_paginates_without_changing_total(inventory_data):
    (client, headers), latest, _ = inventory_data
    first = client.get("/api/inventory", headers=headers).get_json()
    second = client.get("/api/inventory?page=2&per_page=2", headers=headers).get_json()
    assert (first["run_id"], first["page"], first["per_page"], first["total"],
            [row["vm_id"] for row in first["items"]], second["total"],
            [row["vm_id"] for row in second["items"]]) == (
                latest.id, 1, 20, 3, ["alpha", "beta", "gamma"], 3, ["gamma"])


def test_inventory_valid_sort_and_order(inventory_data):
    (client, headers), _, _ = inventory_data
    items = client.get("/api/inventory?sort=vm_name&order=desc", headers=headers).get_json()["items"]
    assert [item["vm_id"] for item in items] == ["gamma", "beta", "alpha"]


@pytest.mark.parametrize("query", [
    "sort=unknown", "sort=vm_id%3BDROP%20TABLE%20asset", "order=sideways",
    "type=invalid", "match=INVALID", "unexpected=value", "q=a&q=b",
    "page=0", "page=-1", "page=abc", "per_page=0", "per_page=101",
    "per_page=2147483648",
])
def test_inventory_rejects_invalid_filters_sort_and_pagination(inventory_data, query):
    (client, headers), _, _ = inventory_data
    response = client.get(f"/api/inventory?{query}", headers=headers)
    assert (response.status_code, response.get_json()) == (400, {"error": "Invalid inventory query"})


def test_inventory_empty_database_returns_empty_page(authorized):
    client, headers = authorized
    response = client.get("/api/inventory", headers=headers)
    assert response.get_json() == {"run_id": None, "items": [], "page": 1, "per_page": 20, "total": 0}


def test_export_includes_all_matching_rows_without_pagination(inventory_data):
    (client, headers), _, _ = inventory_data
    response = client.get("/api/inventory/export?node=node-a&sort=vm_id&order=desc", headers=headers)
    rows = list(csv.DictReader(io.StringIO(response.get_data(as_text=True))))
    assert (response.status_code, response.mimetype, list(rows[0]),
            [row["vm_id"] for row in rows]) == (200, "text/csv", CSV_FIELDS, ["gamma", "alpha"])


def test_export_escapes_csv_cells_and_neutralizes_formula(inventory_data):
    (client, headers), latest, _ = inventory_data
    observation(latest, "=2+2", name='a,"quoted"\nline', tags="  @SUM(A1)")
    db.session.commit()
    response = client.get("/api/inventory/export?q=%3D2%2B2", headers=headers)
    rows = list(csv.DictReader(io.StringIO(response.get_data(as_text=True))))
    assert (rows[0]["vm_id"], rows[0]["vm_name"], rows[0]["tags"]) == (
        "'=2+2", 'a,"quoted"\nline', "'  @SUM(A1)")


@pytest.mark.parametrize("query", ["?page=1", "?per_page=2", "?sort=DROP", "?match=WRONG"])
def test_export_rejects_invalid_query(inventory_data, query):
    (client, headers), _, _ = inventory_data
    response = client.get("/api/inventory/export" + query, headers=headers)
    assert (response.status_code, response.get_json()) == (400, {"error": "Invalid inventory query"})


def test_export_empty_database_has_exact_header(authorized):
    client, headers = authorized
    response = client.get("/api/inventory/export", headers=headers)
    assert list(csv.reader(io.StringIO(response.get_data(as_text=True)))) == [CSV_FIELDS]


def test_asset_detail_contains_metrics_history_and_linked_anomalies(inventory_data):
    (client, headers), latest, (alpha, _, _) = inventory_data
    old = new_run()
    old_alpha = observation(old, "alpha", ip="10.0.0.7")
    db.session.add(Anomaly(run_id=latest.id, asset_id=alpha.id, code="MATCHED_IP",
                            description="linked", created_at="2026-01-01", severity="info"))
    db.session.commit()
    data = client.get(f"/api/assets/{alpha.id}", headers=headers).get_json()
    assert (data["asset"]["asset_id"], data["asset"]["cpu"], data["asset"]["ipam_site"],
            [(row["asset_id"], row["ip_final"]) for row in data["history"]],
            [item["code"] for item in data["anomalies"]]) == (
                alpha.id, 2.0, "test-site", [(old_alpha.id, "10.0.0.7"), (alpha.id, "10.0.0.2")],
                ["MATCHED_IP"])


def test_unknown_asset_returns_json_404(authorized):
    client, headers = authorized
    response = client.get("/api/assets/999999", headers=headers)
    assert (response.status_code, response.get_json()) == (404, {"error": "Asset not found"})


def test_anomalies_filter_by_run_and_code(inventory_data):
    (client, headers), latest, (alpha, _, _) = inventory_data
    old = new_run()
    anomalies = [
        Anomaly(run_id=latest.id, asset_id=alpha.id, code="NO_MATCH", description="one", created_at="2026-01-01", severity="info"),
        Anomaly(run_id=latest.id, code="DUPLICATE_IP", description="two", created_at="2026-01-01", severity="info"),
        Anomaly(run_id=old.id, code="NO_MATCH", description="three", created_at="2026-01-01", severity="info"),
    ]
    for anomaly in anomalies:
        db.session.add(anomaly)
        db.session.flush()
    db.session.commit()
    response = client.get(f"/api/anomalies?run_id={latest.id}&code=NO_MATCH", headers=headers)
    assert (response.get_json()["run_id"], [(a["code"], a["description"])
            for a in response.get_json()["items"]]) == (latest.id, [("NO_MATCH", "one")])


def test_anomalies_default_to_latest_run_and_support_all_codes(authorized):
    client, headers = authorized
    old = new_run()
    latest = new_run()
    codes = ("NO_MATCH", "MATCHED_IP", "HOSTNAME_MISMATCH", "STATUS_MISMATCH", "DUPLICATE_DNS", "DUPLICATE_IP")
    db.session.add(Anomaly(run_id=old.id, code="NO_MATCH", description="old", created_at="2026-01-01", severity="info"))
    db.session.flush()
    for code in codes:
        db.session.add(Anomaly(run_id=latest.id, code=code, description=code, created_at="2026-01-01", severity="info"))
        db.session.flush()
    db.session.commit()
    data = client.get("/api/anomalies", headers=headers).get_json()
    assert (data["run_id"], [item["code"] for item in data["items"]]) == (latest.id, list(codes))


@pytest.mark.parametrize("query", ["?code=BAD", "?run_id=0", "?run_id=abc", "?code=NO_MATCH&code=MATCHED_IP",
                                         "?unexpected=1"])
def test_anomalies_reject_invalid_filters(authorized, query):
    client, headers = authorized
    response = client.get("/api/anomalies" + query, headers=headers)
    assert (response.status_code, response.get_json()) == (400, {"error": "Invalid anomaly filter"})


def test_anomalies_empty_database(authorized):
    client, headers = authorized
    assert client.get("/api/anomalies", headers=headers).get_json() == {"run_id": None, "items": []}


@pytest.mark.parametrize("path", ["/api/inventory", "/api/inventory/export", "/api/assets/1", "/api/anomalies"])
@pytest.mark.parametrize("headers", [{}, {"Authorization": "Bearer invalid"}])
def test_each_inventory_route_rejects_missing_or_invalid_bearer_in_json(orm_app, path, headers):
    response = orm_app.test_client().get(path, headers=headers)
    assert (response.status_code, response.get_json()) == (401, {"error": "Unauthorized"})


def test_swagger_documents_inventory_routes_parameters_and_security(authorized):
    client, _ = authorized
    client.get("/login")
    with client.session_transaction() as session:
        csrf = session["csrf_token"]
    client.post("/login", data={"username": "admin", "password": os.environ["ADMIN_PASSWORD"], "csrf_token": csrf})
    paths = client.get("/apispec_1.json").get_json()["paths"]
    expected = {
        "/api/inventory": {"q", "status", "node", "type", "match", "tag", "meta_zone", "sort", "order", "page", "per_page"},
        "/api/inventory/export": {"q", "status", "node", "type", "match", "tag", "meta_zone", "sort", "order"},
        "/api/assets/{id}": {"id"},
        "/api/anomalies": {"run_id", "code"},
    }
    assert {path: ({param["name"] for param in paths[path]["get"]["parameters"]},
                   paths[path]["get"]["security"]) for path in expected} == {
                       path: (parameters, [{"Bearer": []}]) for path, parameters in expected.items()}
