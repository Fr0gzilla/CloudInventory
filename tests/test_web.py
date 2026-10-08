"""T008 — Pages web : accès protégé, statistiques JSON, lancement de run, sécurité.

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

Attendus lus dans CAHIER_DES_CHARGES.md §8.1/§8.2 et docs/modele/regles.md
(RG26) — jamais dans le code. Les lancements utilisent les sources simulées
(USE_MOCK_VIRT / USE_MOCK_IPAM) : aucun réseau n'est sollicité.
"""
import re
import socket
from urllib.parse import urlparse

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
    # Liens de navigation : seules les routes déjà enregistrées apparaissent.
    assert 'href="/inventory"' not in body


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
