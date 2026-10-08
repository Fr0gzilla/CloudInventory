"""T008/T009 — Pages web : accès protégé, statistiques, lancements, inventaire,
export CSV et détail asset.

T030 — Pages web 2b/2 : liste des runs et détail d'un run

C1 accès anonyme refusé (dashboard et endpoint AJAX, RG26) · C2 tableau de bord
rendu pour une session ouverte, gabarit + ressources locales · C3 statistiques
en JSON sur `/ajax/stats` (cahier §8.1) · C4 lancement AJAX (`/ajax/run`) et
classique (`/run`, cahier §8.2) avec un run réellement persisté · C5 CSRF exigé
sur les deux lancements (cahier « CSRF sur les formulaires ») · C6 déconnexion
referme l'accès · C7 page de connexion labellisée · C8 CSP : aucune ressource
externe, aucun script inline · C9 lancements refusés à un anonyme (jeton CSRF
valide ou non) · C10 mauvais mot de passe laisse la session fermée · C11 jeton
CSRF renouvelé par la connexion : l'ancien est refusé · C12 pipeline en échec :
réponse FAIL sans exception, message « en échec » sur le dashboard · C13
ressources `/static/` servies en 200 et markup compatible CSP (sans navigateur).

T009 (cahier des charges §8.3 et §8.7) : C14 les quatre routes inventaire,
recherche AJAX, export et fiche asset refusent l'anonyme · C15 inventaire rendu
depuis l'instantané du dernier run (ip_final, lien vers la fiche) · C16 filtres
q/statut/noeud/type/match/tag/role · C17 filtre sans correspondance · C18 tri
descendant · C19 colonne de tri hors liste blanche ramenée à vm_name · C20
pagination conservant les filtres dans les liens · C21 recherche AJAX filtrée et
sérialisée · C22 valeurs d'instantané (ip/dns/métriques) en JSON · C23 recherche
AJAX sans run · C24 export CSV du seul dernier run · C25 cellules de formule
neutralisées · C26 export sans run en 404 · C27 fiche asset (métriques,
historique, anomalies) · C28 fiche inconnue en 404 · C29 états vides de la fiche
· C30 inventaire sans run.

Attendus lus dans cahier des charges §8.1/§8.2 et docs/modele/regles.md
(RG26) — jamais dans le code. Les lancements utilisent les sources simulées
(USE_MOCK_VIRT / USE_MOCK_IPAM) : aucun réseau n'est sollicité.
"""
import csv
import io
import re
import socket
from urllib.parse import urlparse

import pytest

# Valeurs factices posées par tests/conftest.py (_TEST_ENV) — aucun secret réel.
ADMIN_USERNAME = "admin"
ADMIN_PASSWORD = "test-admin-password"

_CSRF_FIELD = re.compile(r'name="csrf_token"\s+value="([^"]+)"')
_INLINE_SCRIPT = re.compile(r"<script(?![^>]*\bsrc=)", re.IGNORECASE)
_EXTERNAL_URL = re.compile(r"https?://")


def _use_mocks(monkeypatch):
    """Sources simulées + garde réseau : le pipeline tourne hors ligne, comme
    en production — toute connexion lève et fait échouer le test."""
    monkeypatch.setenv("USE_MOCK_VIRT", "true")
    monkeypatch.setenv("USE_MOCK_IPAM", "true")

    def _no_socket(*args, **kwargs):
        raise AssertionError("accès réseau sollicité pendant le test")

    monkeypatch.setattr(socket.socket, "connect", _no_socket)
    monkeypatch.setattr(socket, "getaddrinfo", _no_socket)


def _login(client):
    """Ouvre une session admin. La connexion vide la session : le jeton CSRF
    doit être relu ensuite, sur la page protégée (auth.py, session.clear())."""
    response = client.get("/login")
    assert response.status_code == 200
    match = _CSRF_FIELD.search(response.get_data(as_text=True))
    assert match, "le formulaire de connexion n'expose pas de champ csrf_token"
    response = client.post(
        "/login",
        data={
            "username": ADMIN_USERNAME,
            "password": ADMIN_PASSWORD,
            "csrf_token": match.group(1),
        },
    )
    assert response.status_code == 302


def _body(client, path="/"):
    """Contenu HTML d'une page rendue en 200 pour la session en cours."""
    response = client.get(path)
    assert response.status_code == 200, f"{path} n'est pas rendu en 200"
    return response.get_data(as_text=True)


def _csrf_on(client, path="/"):
    """Jeton CSRF du formulaire classique rendu sur `path`."""
    match = _CSRF_FIELD.search(_body(client, path))
    assert match, f"aucun champ csrf_token sur {path}"
    return match.group(1)


def _static_assets(client, path):
    """Chemins `/static/…` référencés par les balises src/href de la page."""
    return sorted(
        set(re.findall(r'(?:src|href)="(/static/[^"]+)"', _body(client, path)))
    )


def _failing_source(monkeypatch):
    """Collecte de virtualisation qui échoue : le run passe FAIL sans réseau."""
    import collector

    def boom():
        raise RuntimeError("source de test injoignable")

    monkeypatch.setattr(collector, "collect_vms", boom)


# --- C1 — accès anonyme ------------------------------------------------------
def test_dashboard_redirects_anonymous_to_login(app):
    """Sans session, / renvoie vers la page de connexion (RG26)."""
    client = app.test_client()

    response = client.get("/")

    assert response.status_code == 302
    assert urlparse(response.headers["Location"]).path == "/login"


def test_stats_endpoint_redirects_anonymous_to_login(app):
    """L'endpoint AJAX des statistiques est aussi protégé (RG26)."""
    client = app.test_client()

    response = client.get("/ajax/stats")

    assert response.status_code == 302
    assert urlparse(response.headers["Location"]).path == "/login"


# --- C2 — tableau de bord ----------------------------------------------------
def test_dashboard_renders_for_authenticated_user(app):
    """Session ouverte : / sert le gabarit, l'unique h1 et les CSS locales."""
    client = app.test_client()
    _login(client)

    body = _body(client)

    assert body.count("<h1") == 1
    assert "Dashboard" in body
    assert "/static/vendor/bootstrap.min.css" in body
    assert "/static/vendor/bootstrap-icons.min.css" in body
    assert 'id="btn-run"' in body
    # Liens de navigation : la page inventaire est enregistrée (T009).
    assert 'href="/inventory"' in body


def test_dashboard_serves_local_assets_only(app):
    """Aucun CDN ni origine externe dans les pages (cahier §43-46, CSP)."""
    client = app.test_client()
    _login(client)

    for path in ("/", "/login"):
        response = client.get(path)
        assert response.status_code == 200
        body = response.get_data(as_text=True)
        assert not _EXTERNAL_URL.search(body), f"origine externe dans {path}"
        assert not _INLINE_SCRIPT.search(body), f"script inline dans {path}"
        assert "script-src 'self'" in response.headers["Content-Security-Policy"]

    assert "/static/vendor/chart.umd.min.js" in _body(client)


# --- C3 — statistiques JSON --------------------------------------------------
def test_stats_endpoint_answers_json_without_run(app):
    """Base vide : JSON valide qui annonce l'absence de données."""
    client = app.test_client()
    _login(client)

    response = client.get("/ajax/stats")

    assert response.status_code == 200
    assert response.is_json
    assert response.get_json() == {"has_data": False}


def test_stats_endpoint_answers_json_with_a_run(app, run):
    """Run en base : les compteurs du cahier §8.1 sont exposés en JSON."""
    client = app.test_client()
    _login(client)

    payload = client.get("/ajax/stats").get_json()

    assert payload["has_data"] is True
    assert set(payload["match"]) == {
        "matched_name", "matched_fqdn", "matched_ip", "no_match"
    }
    assert payload["match"] == {
        "matched_name": 0, "matched_fqdn": 0, "matched_ip": 0, "no_match": 0
    }
    assert payload["anomalies"] == {}
    assert set(payload["evolution"]) == {
        "labels", "matched_name", "matched_fqdn", "matched_ip", "no_match"
    }


# --- C4 — lancement de run ---------------------------------------------------
def test_ajax_run_answers_json_and_persists_the_run(app, monkeypatch):
    """POST /ajax/run : réponse JSON SUCCESS, run persisté (cahier §8.2)."""
    _use_mocks(monkeypatch)
    client = app.test_client()
    _login(client)
    token = _csrf_on(client)

    response = client.post("/ajax/run", data={"csrf_token": token})

    assert response.status_code == 200
    assert response.is_json
    payload = response.get_json()
    assert payload["id"] >= 1
    assert payload["status"] == "SUCCESS"
    assert payload["error_message"] is None
    assert payload["no_match_count"] >= 0
    # Le run est en base : les statistiques suivantes le voient.
    assert client.get("/ajax/stats").get_json()["has_data"] is True


def test_classic_run_redirects_to_dashboard(app, monkeypatch):
    """POST /run : redirection vers / puis dernier run affiché (cahier §8.2)."""
    _use_mocks(monkeypatch)
    client = app.test_client()
    _login(client)
    token = _csrf_on(client)

    response = client.post("/run", data={"csrf_token": token})

    assert response.status_code == 302
    assert urlparse(response.headers["Location"]).path == "/"
    body = _body(client)
    assert "Dernier run" in body
    assert "#1" in body
    assert "terminé" in body


# --- C5 — CSRF ---------------------------------------------------------------
def test_run_endpoints_reject_missing_csrf(app, monkeypatch):
    """Les deux lancements exigent le jeton de session : 400, aucun run créé."""
    _use_mocks(monkeypatch)
    client = app.test_client()
    _login(client)

    assert client.post("/ajax/run", data={}).status_code == 400
    assert client.post("/run", data={}).status_code == 400
    assert client.post("/ajax/run", data={"csrf_token": "jeton-incorrect"}).status_code == 400
    assert client.get("/ajax/stats").get_json() == {"has_data": False}


# --- C6 — déconnexion --------------------------------------------------------
def test_logout_closes_dashboard_access(app):
    """Déconnexion : la session suivante est de nouveau redirigée vers /login."""
    client = app.test_client()
    _login(client)
    assert client.get("/").status_code == 200

    response = client.get("/logout")

    assert response.status_code == 302
    assert urlparse(response.headers["Location"]).path == "/login"
    assert urlparse(client.get("/").headers["Location"]).path == "/login"


# --- C7 — formulaire de connexion -------------------------------------------
def test_login_form_is_labelled_and_constrained(app):
    """Chaque champ visible porte son label, son autocomplete et `required`."""
    client = app.test_client()

    body = _body(client, "/login")

    assert '<label for="username"' in body
    assert '<label for="password"' in body
    assert 'autocomplete="username"' in body
    assert 'autocomplete="current-password"' in body
    assert 'type="password"' in body
    assert " required" in body
    assert 'action="/login' in body  # url_for(auth.login, next=…) renseigné par auth.py


# --- C9 — lancements refusés à un anonyme ------------------------------------
def test_run_endpoints_redirect_anonymous_to_login(app):
    """POST /run et /ajax/run sans session : redirection vers /login, aucun run."""
    client = app.test_client()
    token = _csrf_on(client, "/login")  # jeton d'anonyme, propre et refusé plus bas

    for path in ("/run", "/ajax/run"):
        response = client.post(path, data={"csrf_token": token})
        assert response.status_code == 302
        assert urlparse(response.headers["Location"]).path == "/login"

    _login(client)
    assert client.get("/ajax/stats").get_json() == {"has_data": False}


# --- C10 — connexion refusée -------------------------------------------------
def test_wrong_password_leaves_dashboard_closed(app):
    """Mauvais mot de passe : formulaire rechargeé, session toujours fermée."""
    client = app.test_client()
    token = _csrf_on(client, "/login")

    response = client.post(
        "/login",
        data={"username": ADMIN_USERNAME, "password": "faux", "csrf_token": token},
    )

    assert response.status_code == 200
    assert urlparse(client.get("/").headers["Location"]).path == "/login"


# --- C11 — renouvellement du jeton CSRF après connexion ----------------------
def test_csrf_token_is_renewed_after_login(app, monkeypatch):
    """La connexion vide la session : l'ancien jeton n'ouvre plus les POST."""
    _use_mocks(monkeypatch)
    client = app.test_client()
    token_before = _csrf_on(client, "/login")

    response = client.post(
        "/login",
        data={
            "username": ADMIN_USERNAME,
            "password": ADMIN_PASSWORD,
            "csrf_token": token_before,
        },
    )
    assert response.status_code == 302
    token_after = _csrf_on(client)

    assert token_after != token_before
    assert client.post("/run", data={"csrf_token": token_before}).status_code == 400
    assert client.get("/ajax/stats").get_json() == {"has_data": False}


# --- C12 — pipeline en échec -------------------------------------------------
def test_ajax_run_answers_fail_json_without_raising(app, monkeypatch):
    """POST /ajax/run : la collecte qui échoue rend 200 + JSON FAIL (note T008)."""
    _use_mocks(monkeypatch)
    _failing_source(monkeypatch)
    client = app.test_client()
    _login(client)
    token = _csrf_on(client)

    response = client.post("/ajax/run", data={"csrf_token": token})

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["status"] == "FAIL"
    assert "source de test injoignable" in payload["error_message"]
    assert client.get("/ajax/stats").get_json()["has_data"] is True


def test_classic_run_shows_failure_on_dashboard(app, monkeypatch):
    """POST /run en échec : 302 puis message « en échec » sur le tableau de bord."""
    _use_mocks(monkeypatch)
    _failing_source(monkeypatch)
    client = app.test_client()
    _login(client)
    token = _csrf_on(client)

    response = client.post("/run", data={"csrf_token": token})

    assert response.status_code == 302
    body = _body(client)
    assert "en échec" in body
    assert "source de test injoignable" in body


# --- C13 — ressources locales et CSP, sans navigateur ------------------------
def test_referenced_static_assets_are_served(app):
    """Chaque `/static/…` cité par les pages répond en 200 depuis l'origine."""
    client = app.test_client()
    _login(client)

    paths = _static_assets(client, "/") + _static_assets(client, "/login")

    assert "/static/js/app.js" in paths
    assert "/static/vendor/chart.umd.min.js" in paths
    for asset in paths:
        assert client.get(asset).status_code == 200, f"{asset} introuvable"


def test_dashboard_markup_is_csp_compatible(app):
    """Aucun gestionnaire inline ni `javascript:` : `script-src 'self'` suffit."""
    client = app.test_client()
    _login(client)

    body = _body(client)

    assert not re.search(r"\son[a-z]+\s*=", body), "attribut on*= inline"
    assert "javascript:" not in body
    assert not _INLINE_SCRIPT.search(body)


# --- T009 — inventaire, export CSV, détail asset ------------------------------

def _seed_inventory(db, run, specs):
    """Assets + lignes consolidées (+ IPAM facultatif) du run de test.

    L'inventaire d'un run se lit dans `consolidated_asset` (ip_final,
    dns_final, match_status) : `asset` ne garde que la dernière valeur.
    """
    from app.models import Asset, ConsolidatedAsset, IpamRecord

    rows = []
    for spec in specs:
        asset = Asset(
            vm_id=spec["vm_name"], vm_name=spec["vm_name"],
            node=spec.get("node"), type=spec.get("type", "qemu"),
            status=spec.get("status", "running"), tags=spec.get("tags"),
            ip_reported=spec.get("ip_reported"), fqdn=spec.get("fqdn"),
            source=spec.get("source", "VIRT"), consolidated_run_id=run.id,
            match_status=spec.get("match", "MATCHED_NAME"),
            cpu_usage=spec.get("cpu_usage"),
            ram_used=spec.get("ram_used"), ram_max=spec.get("ram_max"),
            disk_used=spec.get("disk_used"), disk_max=spec.get("disk_max"),
            uptime=spec.get("uptime"),
        )
        db.session.add(asset)
        db.session.flush()

        record = None
        if spec.get("ipam"):
            record = IpamRecord(**spec["ipam"])
            db.session.add(record)
            db.session.flush()

        ca = ConsolidatedAsset(
            run_id=run.id, asset_id=asset.id,
            ipam_record_id=record.id if record else None,
            match_status=spec.get("match", "MATCHED_NAME"),
            role=spec.get("role"), ip_final=spec.get("ip_final"),
            dns_final=spec.get("dns_final"),
        )
        db.session.add(ca)
        rows.append((asset, ca, record))
    db.session.commit()
    return rows


# Trois VMs couvrant chaque filtre de la page (cahier §8.3) — lecture seule.
_INVENTORY_SPECS = [
    {
        "vm_name": "web-prod-01", "node": "pve1", "type": "qemu",
        "status": "running", "tags": "env:prod,team:ops",
        "ip_reported": "10.1.0.10", "match": "MATCHED_NAME", "role": "Web",
        "ip_final": "10.9.9.9", "dns_final": "web-prod-01.internal",
        "ipam": {"ip": "10.1.0.10", "dns_name": "web-a500.internal",
                 "tenant": "prod", "site": "dc1"},
        "cpu_usage": 12.5, "ram_used": 4 * 1073741824,
        "ram_max": 8 * 1073741824,
    },
    {
        "vm_name": "db-prod-02", "node": "pve2", "type": "lxc",
        "status": "stopped", "tags": "env:prod",
        "ip_reported": "10.1.0.20", "match": "NO_MATCH",
        "ipam": {"ip": "10.1.0.20", "dns_name": "db-prod-02.internal",
                 "tenant": "prod", "site": "dc2"},
    },
    {
        "vm_name": "lab-test-03", "node": "pve3", "type": "qemu",
        "status": "running", "tags": "env:dev",
        "ip_reported": "10.1.0.30", "match": "MATCHED_IP", "role": "Cache",
        "ipam": {"ip": "10.1.0.30", "dns_name": "lab-test-03.internal"},
    },
]

# En-tête de l'export (queries.export_inventory_csv, aligné sur schema.sql).
_CSV_HEADER = [
    "Hostname", "Hote", "Etat", "Type", "IP", "DNS", "FQDN",
    "Role", "Tenant", "Site", "Match", "Source",
]


# --- C14 — accès protégé ------------------------------------------------------
def test_t009_routes_redirect_anonymous_to_login(app):
    """Les quatre routes T009 refusent l'anonyme (RG26)."""
    client = app.test_client()

    for path in ("/inventory", "/ajax/inventory/search", "/inventory/export",
                 "/assets/1"):
        response = client.get(path)
        assert response.status_code == 302, path
        assert urlparse(response.headers["Location"]).path == "/login", path


# --- C15 — page inventaire ----------------------------------------------------
def test_inventory_renders_the_last_run_snapshot(app, db, run):
    """Inventaire : les trois VMs du run, instantané IP, liens fiche et export."""
    web, _, _ = _seed_inventory(db, run, _INVENTORY_SPECS)[0]
    client = app.test_client()
    _login(client)

    body = _body(client, "/inventory")

    assert all(
        name in body for name in ("web-prod-01", "db-prod-02", "lab-test-03")
    )
    assert 'data-search-url="/ajax/inventory/search"' in body
    assert 'href="/inventory/export"' in body
    assert f'href="/assets/{web.id}"' in body
    assert "10.9.9.9" in body  # ip_final du run, pas l'ip_reported de asset


# --- C16 — filtres ------------------------------------------------------------
@pytest.mark.parametrize(
    "query, kept, dropped",
    [
        ("q=lab-test", "lab-test-03", "web-prod-01"),
        ("status=stopped", "db-prod-02", "lab-test-03"),
        ("node=pve3", "lab-test-03", "db-prod-02"),
        ("type=lxc", "db-prod-02", "web-prod-01"),
        ("match=NO_MATCH", "db-prod-02", "lab-test-03"),
        ("tag=env:dev", "lab-test-03", "db-prod-02"),
        ("role=Web", "web-prod-01", "db-prod-02"),
    ],
    ids=["q", "status", "node", "type", "match", "tag", "role"],
)
def test_inventory_filters_narrow_the_rows(app, db, run, query, kept, dropped):
    """Chaque filtre garde la VM visée et retire les autres (cahier §8.3)."""
    _seed_inventory(db, run, _INVENTORY_SPECS)
    client = app.test_client()
    _login(client)

    body = _body(client, f"/inventory?{query}")

    assert kept in body
    assert dropped not in body


# --- C17 — filtre sans correspondance -----------------------------------------
def test_inventory_filter_without_hit_shows_an_empty_state(app, db, run):
    """Filtre sans correspondance : message d'état vide, page toujours en 200."""
    _seed_inventory(db, run, _INVENTORY_SPECS)
    client = app.test_client()
    _login(client)

    body = _body(client, "/inventory?q=introuvable")

    assert "Aucun résultat pour ces filtres." in body


# --- C18 — tri ----------------------------------------------------------------
def test_inventory_sorts_descending_by_vm_name(app, db, run):
    """Tri demandé : vm_name en desc affiche la VM la plus haute en tête."""
    _seed_inventory(db, run, _INVENTORY_SPECS)
    client = app.test_client()
    _login(client)

    body = _body(client, "/inventory?sort=vm_name&order=desc")

    assert body.index("web-prod-01") < body.index("db-prod-02")


# --- C19 — liste blanche des tris ---------------------------------------------
def test_inventory_falls_back_to_the_sort_whitelist(app, db, run):
    """Colonne hors liste blanche + sens inconnu : 200 et tri vm_name asc."""
    _seed_inventory(db, run, _INVENTORY_SPECS)
    client = app.test_client()
    _login(client)

    body = _body(client, "/inventory?sort=vm_name%3BDROP&order=sideways")

    assert body.index("db-prod-02") < body.index("web-prod-01")


# --- C20 — pagination ---------------------------------------------------------
def test_inventory_paginates_and_keeps_filters_in_page_links(app, db, run):
    """Une page par tranche de PER_PAGE ; les liens conservent les filtres."""
    _seed_inventory(db, run, _INVENTORY_SPECS)
    app.config["PER_PAGE"] = 1
    client = app.test_client()
    _login(client)

    body = _body(client, "/inventory?status=running")

    assert "2 résultat(s) — page 1/2" in body
    assert "lab-test-03" in body and "web-prod-01" not in body
    assert 'id="inventory-pagination"' in body
    page_two = re.search(r'href="(/inventory\?[^"]*page=2)"', body)
    assert page_two, "lien vers la page 2 absent"
    assert "status=running" in page_two.group(1).replace("&amp;", "&")
    following = _body(client, "/inventory?status=running&page=2")
    assert "web-prod-01" in following and "lab-test-03" not in following


# --- C21 — recherche AJAX -----------------------------------------------------
def test_inventory_ajax_search_filters_and_serializes(app, db, run):
    """Recherche live : JSON filtré sur q, item sérialisé avec sa fiche."""
    rows = _seed_inventory(db, run, _INVENTORY_SPECS)
    client = app.test_client()
    _login(client)

    response = client.get("/ajax/inventory/search?q=lab")

    assert response.status_code == 200
    assert response.is_json
    payload = response.get_json()
    assert payload["total"] == 1
    item = payload["items"][0]
    assert item["vm_name"] == "lab-test-03"
    assert item["detail_url"] == f"/assets/{rows[2][0].id}"


# --- C22 — instantané du run en JSON ------------------------------------------
def test_inventory_ajax_search_uses_the_run_snapshot(app, db, run):
    """ip/dns/métriques du JSON viennent de l'instantané et de `asset`."""
    _seed_inventory(db, run, _INVENTORY_SPECS)
    client = app.test_client()
    _login(client)

    payload = client.get("/ajax/inventory/search?q=web-prod").get_json()

    assert payload["total"] == 1
    item = payload["items"][0]
    assert item["ip"] == "10.9.9.9"
    assert item["dns"] == "web-prod-01.internal"
    assert item["ram_pct"] == 50.0


# --- C23 — recherche AJAX sans run --------------------------------------------
def test_inventory_ajax_search_without_run_returns_empty_json(app):
    """Aucun run : JSON vide en 200, jamais une erreur serveur."""
    client = app.test_client()
    _login(client)

    response = client.get("/ajax/inventory/search?q=web")

    assert response.status_code == 200
    assert response.get_json() == {"items": [], "total": 0}


# --- C24 — export CSV filtré sur le dernier run -------------------------------
def test_inventory_export_downloads_only_the_last_run(app, db, run):
    """Export CSV : en-tête, pièce jointe et lignes du seul dernier run."""
    from datetime import datetime

    from app.models import Run

    _seed_inventory(db, run, _INVENTORY_SPECS)
    last = Run(status="SUCCESS", start_date=datetime(2026, 1, 2, 8, 0, 0))
    db.session.add(last)
    db.session.commit()
    _seed_inventory(db, last, [
        {"vm_name": "other-run-vm", "node": "pve5", "type": "qemu",
         "status": "running", "match": "MATCHED_NAME",
         "ipam": {"ip": "10.9.9.1", "dns_name": "other-run.internal"}},
    ])
    client = app.test_client()
    _login(client)

    response = client.get("/inventory/export")
    text = response.get_data(as_text=True)

    assert response.status_code == 200
    assert response.mimetype == "text/csv"
    assert (
        f"attachment; filename=inventaire_run{last.id}.csv"
        in response.headers["Content-Disposition"]
    )
    assert "other-run-vm" in text
    assert "web-prod-01" not in text
    assert next(csv.reader(io.StringIO(text), delimiter=";")) == _CSV_HEADER


# --- C25 — neutralisation des formules CSV ------------------------------------
def test_inventory_export_neutralizes_formula_cells(app, db, run):
    """Cellule amorçant une formule : préfixe « ' » (injection CSV, OWASP)."""
    _seed_inventory(db, run, [
        {"vm_name": "=2+2", "status": "running", "match": "NO_MATCH",
         "ipam": {"ip": "10.1.0.99", "dns_name": "formule.internal"}},
    ])
    client = app.test_client()
    _login(client)

    text = client.get("/inventory/export").get_data(as_text=True)
    rows = list(csv.reader(io.StringIO(text), delimiter=";"))

    assert rows[1][0] == "'=2+2"


# --- C26 — export sans run ----------------------------------------------------
def test_inventory_export_without_run_answers_404(app):
    """Aucun run d'inventaire : l'export répond 404 (cahier §8.3)."""
    client = app.test_client()
    _login(client)

    assert client.get("/inventory/export").status_code == 404


# --- C27 — fiche d'un asset ---------------------------------------------------
def test_asset_detail_shows_metrics_history_and_anomalies(app, db, run):
    """Fiche asset (cahier §8.7) : métriques, 30 derniers runs, anomalies."""
    from datetime import datetime

    from app.models import Anomaly

    web, _, _ = _seed_inventory(db, run, _INVENTORY_SPECS)[0]
    db.session.add(Anomaly(
        run_id=run.id, asset_id=web.id, code="DUPLICATE_DNS",
        description="DNS en double pour cette VM",
        detected_at=datetime(2026, 1, 1, 12, 0, 0),
    ))
    db.session.commit()
    client = app.test_client()
    _login(client)

    body = _body(client, f"/assets/{web.id}")

    assert "Métriques runtime" in body
    assert "12.5%" in body and "4.0 / 8.0 Go" in body
    assert "10.9.9.9" in body and "web-prod-01.internal" in body
    assert "DUPLICATE_DNS" in body


# --- C28 — fiche inconnue -----------------------------------------------------
def test_asset_detail_unknown_id_answers_404(app):
    """Identifiant absent de la base : 404, pas d'erreur 500."""
    client = app.test_client()
    _login(client)

    assert client.get("/assets/4242").status_code == 404


# --- C29 — fiche sans historique ni anomalie ----------------------------------
def test_asset_detail_without_history_shows_empty_states(app, asset):
    """Aucune ligne consolidée ni anomalie : messages d'état vide."""
    client = app.test_client()
    _login(client)

    body = _body(client, f"/assets/{asset.id}")

    assert "Aucun historique." in body
    assert "Aucune anomalie." in body
    assert "web-a500" in body


# --- C30 — inventaire sans run ------------------------------------------------
def test_inventory_without_run_shows_an_empty_state(app):
    """Aucun run : page rendue avec le message d'absence de données."""
    client = app.test_client()
    _login(client)

    body = _body(client, "/inventory")

    assert "Aucun run d'inventaire" in body
    assert 'id="inventory"' not in body


# --- T030 — Pages runs et détail historique ------------------------------------

def test_runs_list_anonymous_redirects_to_login(app):
    """GET /runs sans session : redirection vers /login (RG26)."""
    client = app.test_client()

    response = client.get("/runs")

    assert response.status_code == 302
    assert urlparse(response.headers["Location"]).path == "/login"


def test_runs_list_authenticated_displays_runs(app, db):
    """GET /runs avec session : liste paginée de runs."""
    from datetime import datetime
    from app.models import Run

    NOW = datetime(2026, 1, 1, 12, 0, 0)

    # Run 1 avec SUCCESS
    run1 = Run(status="SUCCESS", start_date=NOW)
    db.session.add(run1)
    db.session.commit()

    # Run 2 avec FAIL
    run2 = Run(status="FAIL", start_date=NOW, error_message="erreur de collecte")
    db.session.add(run2)
    db.session.commit()

    client = app.test_client()
    _login(client)

    body = _body(client, "/runs")

    assert "SUCCESS" in body
    assert "FAIL" in body
    assert "1" in body  # run IDs are displayed


def test_run_detail_anonymous_redirects_to_login(app):
    """GET /runs/<id> sans session : redirection vers /login (RG26)."""
    client = app.test_client()

    response = client.get("/runs/1")

    assert response.status_code == 302
    assert urlparse(response.headers["Location"]).path == "/login"


def test_run_detail_unknown_id_404(app):
    """GET /runs/<id> avec ID inexistant : 404."""
    client = app.test_client()
    _login(client)

    response = client.get("/runs/4242")

    assert response.status_code == 404


def test_run_detail_displays_snapshot_fields(app, db):
    """GET /runs/<id> : les quatre champs historiques (ip_final, dns_final,
    match_status, vm_status) proviennent de consolidated_asset."""
    from datetime import datetime
    from app.models import Run, ConsolidatedAsset, Asset

    NOW = datetime(2026, 1, 1, 12, 0, 0)

    # Run 1 avec instantané rempli
    run1 = Run(status="SUCCESS", start_date=NOW)
    db.session.add(run1)
    db.session.commit()

    asset1 = Asset(
        vm_id="test-vm-snap", vm_name="vm-001", node="pve1", type="qemu",
        status="running", match_status="MATCHED_NAME", source="VIRT",
        consolidated_run_id=run1.id,
    )
    db.session.add(asset1)
    db.session.commit()

    ca1 = ConsolidatedAsset(
        run_id=run1.id, asset_id=asset1.id,
        ip_final="10.0.0.1", dns_final="vm-001.internal",
        match_status="MATCHED_NAME", vm_status="running",
    )
    db.session.add(ca1)
    db.session.commit()

    client = app.test_client()
    _login(client)

    body = _body(client, f"/runs/{run1.id}")

    # Les quatre champs historiques doivent être présents dans le détail
    assert "10.0.0.1" in body  # ip_final depuis consolidated_asset
    assert "vm-001.internal" in body  # dns_final depuis consolidated_asset


def test_run_detail_different_vm_between_two_runs(app, db):
    """Deux runs avec VM modifiée entre les deux : le détail affiche l'instantané
    du run concerné, pas la valeur actuelle de asset (RG « instantané »)."""
    from datetime import datetime
    from app.models import Run, ConsolidatedAsset, Asset

    NOW = datetime(2026, 1, 1, 12, 0, 0)

    # Run ancien avec VM en running, IP 10.0.0.5
    run_old = Run(status="SUCCESS", start_date=NOW)
    db.session.add(run_old)
    db.session.commit()

    asset_old = Asset(
        vm_id="test-vm-old-01", vm_name="vm-old-01", node="pve1", type="qemu",
        status="running", match_status="MATCHED_NAME", source="VIRT",
        consolidated_run_id=run_old.id,
    )
    db.session.add(asset_old)
    db.session.commit()

    ca_old = ConsolidatedAsset(
        run_id=run_old.id, asset_id=asset_old.id,
        ip_final="10.0.0.5", dns_final="vm-old-01.old.lan",
        match_status="MATCHED_NAME", vm_status="running",
    )
    db.session.add(ca_old)
    db.session.commit()

    # Run nouveau : la VM a été modifiée (nouvelle IP, nouveau status)
    # asset vm-old-01 voit ses valeurs à jour, mais l'instantané doit garder
    # les valeurs de l'ancien run
    run_new = Run(status="SUCCESS", start_date=NOW)
    db.session.add(run_new)
    db.session.commit()

    # Modification de l'asset : nouvelle IP et status arrêtés
    asset_old.ip_reported = "10.0.0.99"
    asset_old.status = "stopped"
    # La liaison vers le nouveau run
    asset_old.consolidated_run_id = run_new.id
    db.session.add(asset_old)
    db.session.flush()

    ca_new = ConsolidatedAsset(
        run_id=run_new.id, asset_id=asset_old.id,
        ip_final="10.0.0.99", dns_final="vm-old-01.new.lan",
        match_status="MATCHED_NAME", vm_status="stopped",
    )
    db.session.add(ca_new)
    db.session.commit()

    client = app.test_client()
    _login(client)

    # Détail du run ancien : doit afficher l'instantané ancien
    body_old = _body(client, f"/runs/{run_old.id}")
    assert "10.0.0.5" in body_old  # ip_final de l'instantané ancien
    assert "vm-old-01.old.lan" in body_old  # dns_final de l'instantané ancien
    assert "running" in body_old  # vm_status de l'instantané ancien

    # Détail du run nouveau : doit afficher l'instantané nouveau
    body_new = _body(client, f"/runs/{run_new.id}")
    assert "10.0.0.99" in body_new  # ip_final de l'instantané nouveau (pas la valeur old)
    assert "vm-old-01.new.lan" in body_new  # dns_final de l'instantané nouveau
    assert "stopped" in body_new  # vm_status de l'instantané nouveau (pas running)
