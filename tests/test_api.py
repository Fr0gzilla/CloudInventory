"""T010 — API REST JWT (cahier §9) : chaque endpoint, jeton absent/invalide,
erreurs 4xx, login, Swagger, export CSV sûr, comparaison sur instantanés,
lancement mocké du runner et purge conservant les assets.

C1 chaque endpoint /api/* hors login refusé sans jeton (§9.3, §10.2) ·
C2 jeton absent ou falsifié refusé · C3 login nominal et jeton réutilisable ·
C4 login en 400/401 (§9.5) · C5 /apidocs en 200 et spec /apispec_1.json ·
C6 statistiques · C7 liste paginée des runs · C8 détail de run (+404) ·
C9 lancement 201 avec runner mocké, réseau bloqué · C10 comparaison : 400/404,
changement lu dans l'instantané consolidé (jamais la ligne asset) ·
C11 inventaire paginé, filtré, sans run · C12 export CSV (en-tête, formules
neutralisées, 404 sans run) · C13 détail d'un asset (+404) · C14 anomalies
filtrées par type · C15 purge : N derniers gardés, assets conservés et
réattachés, keep invalide 400, purge non authentifiée sans effet.

Attendus lus dans le cahier des charges §9 ; en cas de conflit la référence
(reference/CloudInventory.v2/app/api.py) fait foi pour le comportement. Aucun
service externe : le runner est mocké et le réseau bloqué.
"""
import csv
import io
import socket
from datetime import datetime

import pytest

from app.models import Anomaly, Asset, ConsolidatedAsset, IpamRecord, Run

# Mot de passe de test posé par tests/conftest.py (_TEST_ENV) : aucune valeur
# réelle ; le compte est lu dans la configuration de l'application de test.
ADMIN_PASSWORD = "test-admin-password"
NOW = datetime(2026, 1, 1, 12, 0, 0)

# Endpoints protégés (méthode, chemin, identifiant) — /api/login est absent :
# c'est la seule route sans @jwt_required (cahier §10.2).
PROTECTED = [
    ("GET", "/api/stats", "stats"),
    ("GET", "/api/runs", "runs-list"),
    ("POST", "/api/runs", "runs-create"),
    ("GET", "/api/runs/1", "run-detail"),
    ("GET", "/api/runs/compare?run1=1&run2=2", "run-compare"),
    ("POST", "/api/runs/purge", "runs-purge"),
    ("GET", "/api/inventory", "inventory"),
    ("GET", "/api/inventory/export", "inventory-export"),
    ("GET", "/api/assets/1", "asset-detail"),
    ("GET", "/api/anomalies", "anomalies"),
]

# En-tête de l'export (app/queries.export_inventory_csv, aligné sur schema.sql).
CSV_HEADER = [
    "Hostname", "Hote", "Etat", "Type", "IP", "DNS", "FQDN",
    "Role", "Tenant", "Site", "Match", "Source",
]


def _username(app):
    return app.config.get("ADMIN_USERNAME", "admin")


def _token(client, app):
    """Jeton JWT obtenu par POST /api/login (aucun appel réseau)."""
    response = client.post("/api/login", json={
        "username": _username(app), "password": ADMIN_PASSWORD,
    })
    assert response.status_code == 200, response.get_data(as_text=True)
    return response.get_json()["access_token"]


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


def _block_network(monkeypatch):
    """Toute connexion sortante échoue le test : aucun service externe."""
    def _blocked(*args, **kwargs):
        raise AssertionError("accès réseau sollicité pendant le test")

    monkeypatch.setattr(socket.socket, "connect", _blocked)
    monkeypatch.setattr(socket, "getaddrinfo", _blocked)


def _add_run(db, status="SUCCESS", **counts):
    """Run persistant ; counts = compteurs de match du run."""
    run = Run(status=status, start_date=NOW, **counts)
    db.session.add(run)
    db.session.commit()
    return run


def _snapshot(db, run, asset, *, match="MATCHED_NAME", vm_status="running",
              ip_final=None, dns_final=None, ipam=None):
    """Instantané consolidé d'un asset existant pour ce run (RG instantané)."""
    record = None
    if ipam:
        record = IpamRecord(**ipam)
        db.session.add(record)
        db.session.flush()
    row = ConsolidatedAsset(
        run_id=run.id, asset_id=asset.id,
        ipam_record_id=record.id if record else None,
        match_status=match, ip_final=ip_final, dns_final=dns_final,
        vm_status=vm_status,
    )
    db.session.add(row)
    db.session.commit()
    return row


def _add_vm(db, run, vm_name, *, ipam=None, ip_reported=None, **kwargs):
    """Asset neuf (vm_id unique) rattaché au run + son premier instantané."""
    asset = Asset(vm_id=vm_name, vm_name=vm_name, node="pve1", type="qemu",
                  status="running", source="VIRT", ip_reported=ip_reported,
                  consolidated_run_id=run.id)
    db.session.add(asset)
    db.session.flush()
    _snapshot(db, run, asset, ipam=ipam, **kwargs)
    return asset


# --- C1 — chaque endpoint protégé refusé sans jeton --------------------------
@pytest.mark.parametrize(
    "method, path, name", PROTECTED, ids=[name for _, _, name in PROTECTED]
)
def test_protected_endpoints_refuse_missing_jwt(app, method, path, name):
    """C1 — hors /api/login, toute route /api/* répond 401 sans jeton (§10.2)."""
    client = app.test_client()

    response = client.open(path, method=method)

    assert response.status_code == 401, name


# --- C2 — jeton invalide ou falsifié -----------------------------------------
def test_protected_endpoint_refuses_malformed_jwt(app):
    """C2 — jeton non déchiffrable : accès refusé, jamais de 200. §9.5 annonce
    401 ; la bibliothèque (et la référence) renvoient 422 : les deux refusent."""
    client = app.test_client()

    response = client.get("/api/stats", headers=_auth("pas.un.jeton.valide"))

    assert response.status_code in (401, 422)


def test_protected_endpoint_refuses_forged_jwt(app):
    """C2 — jeton authentique dont on altère la signature : refusé (§10.2)."""
    client = app.test_client()
    token = _token(client, app)
    forged = token[:-1] + ("A" if token[-1] != "A" else "B")

    response = client.get("/api/stats", headers=_auth(forged))

    assert response.status_code in (401, 422)


# --- C3/C4 — login -----------------------------------------------------------
def test_login_returns_a_usable_token(app):
    """C3 — login valide : 200, access_token consommé sur une route protégée."""
    client = app.test_client()

    response = client.post("/api/login", json={
        "username": _username(app), "password": ADMIN_PASSWORD,
    })

    assert response.status_code == 200
    token = response.get_json().get("access_token")
    assert token
    assert client.get("/api/stats", headers=_auth(token)).status_code == 200


def test_login_rejects_wrong_credentials(app):
    """C4 — mauvais mot de passe : 401 « Identifiants incorrects » (§9.5)."""
    client = app.test_client()
    credentials = {"username": _username(app), "password": ADMIN_PASSWORD}
    credentials["password"] += "-incorrect"

    response = client.post("/api/login", json=credentials)

    assert response.status_code == 401
    assert response.get_json()["error"] == "Identifiants incorrects"


def test_login_requires_a_json_body(app):
    """C4 — requête sans corps JSON : 400 (§9.5)."""
    client = app.test_client()

    response = client.post("/api/login", content_type="application/json")

    assert response.status_code == 400


def test_login_rejects_a_non_object_body(app):
    """C4 — corps JSON qui n'est pas un objet : 400, aucune erreur serveur."""
    client = app.test_client()

    response = client.post("/api/login", json=["admin"])

    assert response.status_code == 400


def test_login_rejects_non_string_credentials(app):
    """C4 — champs non chaînes : 401 sans exception (§9.2)."""
    client = app.test_client()

    response = client.post("/api/login", json={"username": 1, "password": ["x"]})

    assert response.status_code == 401


# --- C5 — Swagger ------------------------------------------------------------
def test_apidocs_answers_200(app):
    """C5 — Swagger UI servi sur /apidocs (§9.1)."""
    client = app.test_client()

    response = client.get("/apidocs")

    assert response.status_code == 200
    assert "swagger" in response.get_data(as_text=True).lower()


def test_apispec_documents_endpoints_and_bearer(app):
    """C5 — spec JSON : titre, sécurité Bearer et endpoints du §9.3."""
    client = app.test_client()

    response = client.get("/apispec_1.json")

    assert response.status_code == 200
    spec = response.get_json()
    assert spec["info"]["title"] == "CloudInventory API"
    schemes = spec.get("securityDefinitions") or spec.get(
        "components", {}
    ).get("securitySchemes", {})
    assert "Bearer" in schemes
    assert {"/api/login", "/api/stats", "/api/runs/compare",
            "/api/inventory/export"} <= set(spec["paths"])


# --- C6 — statistiques -------------------------------------------------------
def test_stats_without_run_reports_no_data(app):
    """C6 — base vide : has_data false (§9.3)."""
    client = app.test_client()
    token = _token(client, app)

    body = client.get("/api/stats", headers=_auth(token)).get_json()

    assert body["has_data"] is False


def test_stats_with_run_returns_match_and_evolution(app, db):
    """C6 — dernier run : compteurs de match et série d'évolution."""
    _add_run(db, matched_name_count=2, no_match_count=1)
    client = app.test_client()
    token = _token(client, app)

    body = client.get("/api/stats", headers=_auth(token)).get_json()

    assert body["match"] == {
        "matched_name": 2, "matched_fqdn": 0, "matched_ip": 0, "no_match": 1,
    }
    assert body["evolution"]["labels"]


# --- C7 — liste des runs -----------------------------------------------------
def test_runs_list_is_empty_without_run(app):
    """C7 — aucun run : runs vide, total 0 (§9.3)."""
    client = app.test_client()
    token = _token(client, app)

    body = client.get("/api/runs", headers=_auth(token)).get_json()

    assert body["runs"] == []
    assert body["total"] == 0


def test_runs_list_paginates_newest_first(app, db):
    """C7 — 3 runs, per_page=2 : page 1 sur 2, du plus récent au plus ancien."""
    first = _add_run(db)
    second = _add_run(db)
    third = _add_run(db)
    client = app.test_client()
    token = _token(client, app)

    body = client.get("/api/runs?page=1&per_page=2",
                      headers=_auth(token)).get_json()

    assert [run["id"] for run in body["runs"]] == [third.id, second.id]
    assert body["total"] == 3
    assert body["pages"] == 2
    assert first.id < second.id


# --- C8 — détail de run ------------------------------------------------------
def test_run_detail_returns_inventory_and_anomalies(app, db):
    """C8 — run existant : inventaire et anomalies sérialisés (§9.3)."""
    run = _add_run(db)
    asset = _add_vm(db, run, "web-01", ip_final="10.9.9.1",
                    dns_final="web-01.internal")
    db.session.add(Anomaly(run_id=run.id, asset_id=asset.id, code="NO_MATCH",
                           description="aucune correspondance", detected_at=NOW))
    db.session.commit()
    client = app.test_client()
    token = _token(client, app)

    body = client.get(f"/api/runs/{run.id}", headers=_auth(token)).get_json()

    assert body["id"] == run.id
    assert [item["vm_name"] for item in body["inventory"]] == ["web-01"]
    assert body["inventory"][0]["ip"] == "10.9.9.1"
    assert body["anomalies"][0]["type"] == "NO_MATCH"


def test_run_detail_unknown_answers_404(app, db):
    """C8 — run introuvable : 404 (§9.5)."""
    _add_run(db)
    client = app.test_client()
    token = _token(client, app)

    response = client.get("/api/runs/4242", headers=_auth(token))

    assert response.status_code == 404


# --- C9 — lancement mocké du runner ------------------------------------------
def test_trigger_run_returns_created_run_without_network(app, db, monkeypatch):
    """C9 — POST /api/runs : 201, run sérialisé ; runner mocké et réseau bloqué,
    aucun service externe n'est appelé (§9.5)."""
    import collector.inventory_runner as inventory_runner

    created = _add_run(db, matched_name_count=2, no_match_count=1)
    calls = []

    def _fake_run_inventory():
        calls.append(True)
        return created

    monkeypatch.setattr(inventory_runner, "run_inventory", _fake_run_inventory)
    _block_network(monkeypatch)
    client = app.test_client()
    token = _token(client, app)

    response = client.post("/api/runs", headers=_auth(token))

    assert response.status_code == 201
    assert response.get_json() == {
        "id": created.id,
        "status": "SUCCESS",
        "start_date": NOW.isoformat(),
        "end_date": None,
        "matched_name_count": 2,
        "matched_fqdn_count": 0,
        "matched_ip_count": 0,
        "no_match_count": 1,
        "error_message": None,
    }
    assert calls == [True]


# --- C10 — comparaison de runs -----------------------------------------------
def test_run_compare_requires_both_parameters(app):
    """C10 — run1/run2 manquant : 400 (§9.5)."""
    client = app.test_client()
    token = _token(client, app)

    response = client.get("/api/runs/compare?run1=1", headers=_auth(token))

    assert response.status_code == 400
    assert "run1 et run2" in response.get_json()["error"]


def test_run_compare_unknown_run_answers_404(app, db):
    """C10 — run2 introuvable : 404 (§9.5)."""
    first = _add_run(db)
    client = app.test_client()
    token = _token(client, app)

    response = client.get(f"/api/runs/compare?run1={first.id}&run2=4242",
                          headers=_auth(token))

    assert response.status_code == 404


def test_run_compare_reports_ip_change_from_the_snapshot(app, db):
    """C10 — même VM, IP différente entre les instantanés : changement IP."""
    first = _add_run(db)
    second = _add_run(db)
    asset = _add_vm(db, first, "web-01", ip_final="10.0.0.1")
    _snapshot(db, second, asset, ip_final="10.0.0.2")
    client = app.test_client()
    token = _token(client, app)

    body = client.get(f"/api/runs/compare?run1={first.id}&run2={second.id}",
                      headers=_auth(token)).get_json()

    assert body["changed"] == [
        {"vm_name": "web-01",
         "changes": [{"field": "IP", "before": "10.0.0.1",
                      "after": "10.0.0.2"}]}
    ]
    assert body["added"] == []
    assert body["removed"] == []


def test_run_compare_ignores_the_asset_row(app, db):
    """C10 — la ligne asset porte une IP différente des instantanés : aucune
    différence — la comparaison ne lit que consolidated_asset."""
    first = _add_run(db)
    second = _add_run(db)
    asset = _add_vm(db, first, "web-01", ip_final="10.0.0.1",
                    ip_reported="10.7.7.7")
    _snapshot(db, second, asset, ip_final="10.0.0.1")
    client = app.test_client()
    token = _token(client, app)

    body = client.get(f"/api/runs/compare?run1={first.id}&run2={second.id}",
                      headers=_auth(token)).get_json()

    assert body["changed"] == []
    assert body["added"] == []
    assert body["removed"] == []


# --- C11 — inventaire --------------------------------------------------------
def test_inventory_returns_the_last_run_snapshot(app, db):
    """C11 — inventaire paginé : instantané du run le plus récent (§9.4)."""
    first = _add_run(db)
    second = _add_run(db)
    asset = _add_vm(db, first, "web-01", ip_final="10.1.1.1",
                    vm_status="running")
    _snapshot(db, second, asset, ip_final="10.2.2.2", vm_status="stopped")
    client = app.test_client()
    token = _token(client, app)

    body = client.get("/api/inventory", headers=_auth(token)).get_json()

    assert body["run_id"] == second.id
    assert [item["ip"] for item in body["items"]] == ["10.2.2.2"]
    assert [item["status"] for item in body["items"]] == ["stopped"]


def test_inventory_filters_by_free_text(app, db):
    """C11 — ?q= filtre sur le nom : une seule VM conservée (§9.4)."""
    run = _add_run(db)
    _add_vm(db, run, "web-prod-01", ip_final="10.0.0.1")
    _add_vm(db, run, "db-prod-02", match="NO_MATCH", ip_final="10.0.0.2")
    client = app.test_client()
    token = _token(client, app)

    body = client.get("/api/inventory?q=db-prod",
                      headers=_auth(token)).get_json()

    assert body["total"] == 1
    assert body["items"][0]["vm_name"] == "db-prod-02"


def test_inventory_without_run_is_empty(app):
    """C11 — aucun run : page vide en 200 (§9.5)."""
    client = app.test_client()
    token = _token(client, app)

    body = client.get("/api/inventory", headers=_auth(token)).get_json()

    assert body["items"] == []
    assert body["total"] == 0


# --- C12 — export CSV --------------------------------------------------------
def test_inventory_export_downloads_the_last_run_csv(app, db):
    """C12 — export : text/csv, pièce jointe nommée, en-tête et lignes du
    dernier run (§9.3)."""
    run = _add_run(db)
    _add_vm(db, run, "web-01", ip_final="10.9.9.9", dns_final="web-01.int",
            ipam={"ip": "10.0.0.1", "dns_name": "web-01.int"})
    client = app.test_client()
    token = _token(client, app)

    response = client.get("/api/inventory/export", headers=_auth(token))

    assert response.status_code == 200
    assert response.mimetype == "text/csv"
    assert (f"attachment; filename=inventaire_run{run.id}.csv"
            in response.headers["Content-Disposition"])
    rows = list(csv.reader(io.StringIO(response.get_data(as_text=True)),
                           delimiter=";"))
    assert rows[0] == CSV_HEADER
    assert rows[1][0] == "web-01"
    assert rows[1][4] == "10.9.9.9"  # instantané, pas l'ip_reported de asset


def test_inventory_export_neutralizes_formula_cells(app, db):
    """C12 — cellule amorçant une formule : préfixe « ' » (injection CSV)."""
    run = _add_run(db)
    _add_vm(db, run, "=2+2", match="NO_MATCH", ip_final="10.1.0.99")
    client = app.test_client()
    token = _token(client, app)

    response = client.get("/api/inventory/export", headers=_auth(token))
    rows = list(csv.reader(io.StringIO(response.get_data(as_text=True)),
                           delimiter=";"))

    assert rows[1][0] == "'=2+2"


def test_inventory_export_without_run_answers_404(app):
    """C12 — aucun run : 404 (§9.5)."""
    client = app.test_client()
    token = _token(client, app)

    response = client.get("/api/inventory/export", headers=_auth(token))

    assert response.status_code == 404


# --- C13 — détail d'un asset -------------------------------------------------
def test_asset_detail_returns_metrics_history_and_anomalies(app, db):
    """C13 — fiche asset : métriques, historique et anomalies (§9.3)."""
    run = _add_run(db)
    asset = _add_vm(db, run, "web-01", ip_final="10.0.0.1",
                    dns_final="web-01.int", ipam={"ip": "10.0.0.1",
                                                  "dns_name": "web-01.int",
                                                  "tenant": "prod"})
    db.session.add(Anomaly(run_id=run.id, asset_id=asset.id,
                           code="DUPLICATE_DNS", description="doublon",
                           detected_at=NOW))
    db.session.commit()
    client = app.test_client()
    token = _token(client, app)

    body = client.get(f"/api/assets/{asset.id}",
                      headers=_auth(token)).get_json()

    assert body["vm_name"] == "web-01"
    assert body["history"][0]["ip"] == "10.0.0.1"
    assert body["history"][0]["tenant"] == "prod"
    assert body["anomalies"][0]["type"] == "DUPLICATE_DNS"


def test_asset_detail_unknown_answers_404(app, db):
    """C13 — asset introuvable : 404 (§9.5)."""
    _add_run(db)
    client = app.test_client()
    token = _token(client, app)

    response = client.get("/api/assets/4242", headers=_auth(token))

    assert response.status_code == 404


# --- C14 — anomalies ---------------------------------------------------------
def test_anomalies_are_filtered_by_type(app, db):
    """C14 — ?type= ne retourne que le type demandé, paginé (§9.3)."""
    run = _add_run(db)
    asset = _add_vm(db, run, "web-01")
    for code in ("NO_MATCH", "STATUS_MISMATCH"):
        db.session.add(Anomaly(run_id=run.id, asset_id=asset.id, code=code,
                               description="d", detected_at=NOW))
    db.session.commit()
    client = app.test_client()
    token = _token(client, app)

    body = client.get("/api/anomalies?type=NO_MATCH",
                      headers=_auth(token)).get_json()

    assert body["total"] == 1
    assert body["items"][0]["type"] == "NO_MATCH"
    assert body["items"][0]["asset"]["vm_name"] == "web-01"


# --- C15 — purge -------------------------------------------------------------
def test_purge_keeps_recent_runs_and_preserves_assets(app, db):
    """C15 — keep=3 sur 5 runs : 2 supprimés, les assets sont conservés et
    réattachés au run le plus récent conservé (RESTRICT)."""
    runs = [_add_run(db) for _ in range(5)]
    _add_vm(db, runs[0], "web-01", ip_final="10.0.0.1")
    _add_vm(db, runs[4], "db-02", match="NO_MATCH", ip_final="10.0.0.2")
    client = app.test_client()
    token = _token(client, app)

    response = client.post("/api/runs/purge", json={"keep": 3},
                           headers=_auth(token))

    assert response.status_code == 200
    assert response.get_json() == {"deleted": 2, "kept": 3}
    assert db.session.query(Run).count() == 3
    assert db.session.query(Asset).count() == 2
    owners = {row[0] for row in
              db.session.query(Asset.consolidated_run_id).all()}
    assert owners == {runs[4].id}


def test_purge_rejects_invalid_keep(app, db):
    """C15 — keep nul, négatif ou non entier : 400, aucun run supprimé
    (§9.5)."""
    _add_run(db)
    client = app.test_client()
    token = _token(client, app)

    for keep in (0, -1, "3", 2.5):
        response = client.post("/api/runs/purge", json={"keep": keep},
                               headers=_auth(token))
        assert response.status_code == 400, keep

    assert db.session.query(Run).count() == 1


def test_purge_rejects_boolean_keep_without_deleting_runs(app, db):
    """C15 — {"keep": true} hors booléens : 400 et aucune suppression, sinon
    il vaudrait keep=1 (§9.5)."""
    runs = [_add_run(db) for _ in range(3)]
    _add_vm(db, runs[0], "web-01", ip_final="10.0.0.1")
    client = app.test_client()
    token = _token(client, app)

    response = client.post("/api/runs/purge", json={"keep": True},
                           headers=_auth(token))

    assert response.status_code == 400
    assert db.session.query(Run).count() == 3
    assert db.session.query(Asset).count() == 1


@pytest.mark.parametrize("body", [[{"keep": 1}], ["keep", 1]])
def test_purge_rejects_json_array_body_without_deleting_runs(app, db, body):
    """C15 — corps JSON tableau (objet exigé) : 400, aucune mutation (§9.5)."""
    for _ in range(3):
        _add_run(db)
    client = app.test_client()
    token = _token(client, app)

    response = client.post("/api/runs/purge", json=body,
                           headers=_auth(token))

    assert response.status_code == 400
    assert db.session.query(Run).count() == 3


def test_purge_without_jwt_leaves_runs_untouched(app, db):
    """C15 — purge non authentifiée : 401 et aucune suppression (§10.2)."""
    for _ in range(3):
        _add_run(db)
    client = app.test_client()

    response = client.post("/api/runs/purge", json={"keep": 1})

    assert response.status_code == 401
    assert db.session.query(Run).count() == 3
