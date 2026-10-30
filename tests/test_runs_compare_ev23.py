import os

import pytest

from app import db
from app.models import Anomaly, Asset, ConsolidatedAsset, Run
from test_runs_compare_anomalies import page


def run():
    item = Run(status="SUCCESS", started_at="2026-01-01T00:00:00Z")
    db.session.add(item)
    db.session.commit()
    return item


def asset(current, vm_id, *, consolidated=True, **values):
    item = Asset(run_id=current.id, vm_id=vm_id, **values)
    db.session.add(item)
    db.session.flush()
    if consolidated:
        db.session.add(ConsolidatedAsset(run_id=current.id, asset_id=item.id,
                                         match_status="MATCHED_NAME", ip_final=values.get("ip_reported")))
    db.session.commit()
    return item


@pytest.fixture
def clients(orm_app):
    admin = orm_app.test_client()
    admin.get("/login")
    with admin.session_transaction() as session:
        csrf = session["csrf_token"]
    assert admin.post("/login", data={"username": "admin", "password": os.environ["ADMIN_PASSWORD"],
                                      "csrf_token": csrf}).status_code == 302
    token = orm_app.test_client().post("/api/login", json={"username": "admin", "password": os.environ["ADMIN_PASSWORD"]}).get_json()["access_token"]
    return admin, {"Authorization": f"Bearer {token}"}


def test_api_reports_appeared_disappeared_changed_and_identical_vm(clients):
    client, headers = clients
    before, after = run(), run()
    asset(before, "gone", vm_name="depart", ip_reported="10.0.0.1")
    asset(after, "new", vm_name="arrivee", ip_reported="10.0.0.2")
    asset(before, "same", vm_name="stable")
    asset(after, "same", vm_name="stable")
    asset(before, "changed", vm_name="old", fqdn="old.example", node="node-a", cpu=1, ram=2)
    asset(after, "changed", vm_name="new", fqdn="new.example", node="node-b", cpu=4, ram=8)
    response = client.get(f"/api/runs/compare?from={before.id}&to={after.id}", headers=headers)
    data = response.get_json()
    assert response.status_code == 200
    assert ([x["vm_id"] for x in data["appeared"]], [x["vm_id"] for x in data["disappeared"]],
            [x["vm_id"] for x in data["changed"]]) == (["new"], ["gone"], ["changed"])
    assert {field: (data["changed"][0]["before"][field], data["changed"][0]["after"][field])
            for field in data["changed"][0]["fields"]} == {
                "vm_name": ("old", "new"), "fqdn": ("old.example", "new.example"),
                "node": ("node-a", "node-b"), "cpu": (1, 4), "ram": (2, 8),
            }
    assert (data["appeared"][0]["vm_name"], data["disappeared"][0]["ip_final"]) == ("arrivee", "10.0.0.1")


def test_api_distinguishes_missing_empty_and_zero_for_unconsolidated_vm(clients):
    client, headers = clients
    before, after = run(), run()
    asset(before, "bare", consolidated=False, annotation=None, cpu=None, ram=0)
    asset(after, "bare", consolidated=False, annotation="", cpu=0, ram=None)
    response = client.get(f"/api/runs/compare?from={before.id}&to={after.id}", headers=headers)
    change = response.get_json()["changed"][0]
    assert response.status_code == 200
    assert (change["vm_id"], set(change["fields"]),
            {key: (change["before"][key], change["after"][key]) for key in ("annotation", "cpu", "ram")},
            change["before"]["match_status"], change["after"]["ip_final"]) == (
                "bare", {"annotation", "cpu", "ram"},
                {"annotation": (None, ""), "cpu": (None, 0), "ram": (0, None)}, None, None)


def test_comparison_does_not_mutate_historical_runs_or_anomalies(clients):
    client, headers = clients
    before, after = run(), run()
    old = asset(before, "vm", vm_name="before")
    asset(after, "vm", vm_name="after")
    db.session.add(Anomaly(run_id=before.id, code="NO_MATCH", severity="info",
                           description="historical", created_at="2026-01-01"))
    db.session.commit()
    response = client.get(f"/api/runs/compare?from={before.id}&to={after.id}", headers=headers)
    web = client.get(f"/runs/compare?from={before.id}&to={after.id}")
    db.session.expire_all()
    assert (response.status_code, web.status_code, db.session.get(Asset, old.id).vm_name,
            Anomaly.query.filter_by(run_id=before.id).one().description) == (200, 200, "before", "historical")


def test_page_displays_each_changed_field_with_exact_old_and_new_values(clients):
    client, _ = clients
    before, after = run(), run()
    asset(before, "vm", vm_name="old", fqdn="before.example", cpu=1)
    asset(after, "vm", vm_name="new", fqdn="after.example", cpu=3)
    response = client.get(f"/runs/compare?from={before.id}&to={after.id}")
    assert response.status_code == 200
    assert [row for row in page(response).rows if row and row[0] == "vm"] == [
        ["vm", "vm_name", "old", "new"], ["vm", "fqdn", "before.example", "after.example"],
        ["vm", "cpu", "1.0", "3.0"],
    ]


def test_page_escapes_hostile_values_in_changed_cells(clients):
    client, _ = clients
    before, after = run(), run()
    hostile = '<img src=x onerror="alert(1)">'
    asset(before, "vm", vm_name=hostile)
    asset(after, "vm", vm_name="safe")
    response = client.get(f"/runs/compare?from={before.id}&to={after.id}")
    assert [row for row in page(response).rows if row[:2] == ["vm", "vm_name"]] == [["vm", "vm_name", hostile, "safe"]]
    assert hostile not in response.get_data(as_text=True)


def test_page_distinguishes_absent_empty_and_zero_in_changed_cells(clients):
    client, _ = clients
    before, after = run(), run()
    asset(before, "vm", consolidated=False, annotation=None, cpu=None, ram=0)
    asset(after, "vm", consolidated=False, annotation="", cpu=0, ram=None)
    response = client.get(f"/runs/compare?from={before.id}&to={after.id}")
    assert [row for row in page(response).rows if row and row[0] == "vm"] == [
        ["vm", "annotation", "Absent", "Chaîne vide"], ["vm", "cpu", "Absent", "0.0"],
        ["vm", "ram", "0.0", "Absent"],
    ]


def test_reader_can_access_api_and_comparison_page(clients):
    admin, _ = clients
    admin.get("/accounts")
    with admin.session_transaction() as session:
        csrf = session["csrf_token"]
    assert admin.post("/accounts", data={"username": "ev23-reader", "role": "lecteur",
                                         "password": "reader-test-password", "csrf_token": csrf}).status_code == 302
    before, after = run(), run()
    asset(after, "visible", vm_name="reader-visible")
    client = admin.application.test_client()
    token_response = client.post("/api/login", json={"username": "ev23-reader", "password": "reader-test-password"})
    token = token_response.get_json()["access_token"]
    api_response = client.get(f"/api/runs/compare?from={before.id}&to={after.id}",
                              headers={"Authorization": f"Bearer {token}"})
    client.get("/login")
    with client.session_transaction() as session:
        csrf = session["csrf_token"]
    login = client.post("/login", data={"username": "ev23-reader", "password": "reader-test-password", "csrf_token": csrf})
    web = client.get(f"/runs/compare?from={before.id}&to={after.id}")
    assert (token_response.status_code, api_response.status_code, api_response.get_json()["appeared"][0]["vm_id"],
            login.status_code, web.status_code, [row[0] for row in page(web).rows if row and row[0] == "visible"]) == (
                200, 200, "visible", 302, 200, ["visible"])
