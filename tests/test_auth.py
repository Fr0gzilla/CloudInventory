"""T007 — Authentification web : critères de la consigne.

C1 connexion valide · C2 identifiants invalides · C3 jeton CSRF absent ·
C4 jeton CSRF invalide (aucun essai consommé) · C5 verrouillage après
LOGIN_MAX_ATTEMPTS puis expiration de la fenêtre · C6 route protégée ·
C7 déconnexion · C8 paramètre `next` externe refusé · C9 cookie de session
et en-têtes de sécurité · C10 mot de passe admin uniquement haché.

Attendus lus dans cahier des charges §10.1/§10.3, docs/modele/sequences.md
(schéma a) et docs/modele/regles.md (RG25, RG26) — jamais dans le code.
`regles.md` ne porte aucune RG de limitation des essais (sequences.md:66) :
C5 est jugé sur CAHIER « Sécurité » + sequences.md.
"""
import re
import time
from urllib.parse import urlparse

from werkzeug.security import check_password_hash

# Valeurs factices posées par tests/conftest.py (_TEST_ENV) — aucun secret réel.
ADMIN_USERNAME = "admin"
ADMIN_PASSWORD = "test-admin-password"
# Mot de passe de test volontairement faux, pour les connexions en échec.
_WRONG = "incorrect"

_CSRF_FIELD = re.compile(r'name="csrf_token"\s+value="([^"]+)"')


class _FrozenClock:
    """Horloge figée : fait avancer time.time() et time.monotonic() sans dormir."""

    def __init__(self, now):
        self.now = now

    def time(self):
        return self.now

    def monotonic(self):
        return self.now


def _csrf_token(client):
    """Jeton CSRF obtenu par GET /login (formulaire rendu en 200)."""
    response = client.get("/login")
    assert response.status_code == 200
    match = _CSRF_FIELD.search(response.get_data(as_text=True))
    assert match, "le formulaire de connexion n'expose pas de champ csrf_token"
    return match.group(1)


def _post_login(
    client,
    csrf=None,
    username=ADMIN_USERNAME,
    password=ADMIN_PASSWORD,
    next_target=None,
):
    """POST /login — csrf=None signifie champ CSRF absent du formulaire."""
    url = "/login" if next_target is None else f"/login?next={next_target}"
    data = {"username": username, "password": password}
    if csrf is not None:
        data["csrf_token"] = csrf
    return client.post(url, data=data)


def _location_path(response):
    return urlparse(response.headers["Location"]).path


def _is_local_target(location):
    """Cible de redirection à la fois relative et sans hôte (pas d'open redirect)."""
    parsed = urlparse(location)
    return (
        location.startswith("/")
        and not location.startswith("//")
        and not parsed.scheme
        and not parsed.netloc
    )


def _session_cookie(response, cookie_name):
    for raw in response.headers.getlist("Set-Cookie"):
        if raw.startswith(cookie_name + "="):
            return raw
    return None


# --- C1 — connexion nominale -------------------------------------------------
def test_valid_login_redirects_to_requested_page(app):
    """Identifiants corrects : 302 vers la page `next` demandée."""
    client = app.test_client()
    token = _csrf_token(client)

    response = _post_login(client, csrf=token, next_target="/profile")

    assert response.status_code == 302
    assert _location_path(response) == "/profile"


def test_valid_login_opens_access_to_protected_route(app):
    """La session ouverte par une connexion valide donne accès à /profile."""
    client = app.test_client()
    token = _csrf_token(client)
    _post_login(client, csrf=token, next_target="/profile")

    response = client.get("/profile")

    assert response.status_code == 200


# --- C2 — identifiants invalides --------------------------------------------
def test_wrong_password_renders_form_without_opening_session(app):
    """Mot de passe faux : 200 avec message d'erreur, session non ouverte."""
    client = app.test_client()
    token = _csrf_token(client)

    response = _post_login(client, csrf=token, password="mauvais-mdp")

    assert response.status_code == 200
    assert "Identifiants incorrects." in response.get_data(as_text=True)
    assert _location_path(client.get("/profile")) == "/login"


def test_unknown_username_is_refused(app):
    """Utilisateur inconnu : même comportement que le mauvais mot de passe."""
    client = app.test_client()
    token = _csrf_token(client)

    response = _post_login(client, csrf=token, username="intrus")

    assert response.status_code == 200
    assert _location_path(client.get("/profile")) == "/login"


# --- C3 / C4 — jeton CSRF ----------------------------------------------------
def test_login_without_csrf_token_is_rejected(app):
    """CSRF absent du formulaire : 400, aucune session ouverte."""
    client = app.test_client()

    response = _post_login(client, csrf=None)

    assert response.status_code == 400
    assert _location_path(client.get("/profile")) == "/login"


def test_login_with_invalid_csrf_token_is_rejected(app):
    """CSRF falsifié : 400, aucune session ouverte."""
    client = app.test_client()

    response = _post_login(client, csrf="jeton-qui-ne-correspond-pas")

    assert response.status_code == 400
    assert _location_path(client.get("/profile")) == "/login"


def test_csrf_rejection_consumes_no_login_attempt(app):
    """Un POST sans CSRF ne consomme aucun essai (sequences.md:32 « aucun essai
    compte ») : la fenêtre reste ouverte pour la connexion suivante."""
    client = app.test_client()
    token = _csrf_token(client)
    max_attempts = app.config["LOGIN_MAX_ATTEMPTS"]
    for _ in range(max_attempts - 1):
        assert _post_login(client, csrf=token, password="mauvais").status_code == 200
    assert _post_login(client, csrf=None).status_code == 400

    response = _post_login(client, csrf=token)

    assert response.status_code == 302


# --- C5 — verrouillage et expiration ----------------------------------------
def test_lockout_after_max_attempts_answers_429_with_retry_after(app):
    """Après LOGIN_MAX_ATTEMPTS échecs, toute tentative est refusée en 429
    avec un en-tête Retry-After (CAHIER « limitation des essais »)."""
    client = app.test_client()
    token = _csrf_token(client)
    for _ in range(app.config["LOGIN_MAX_ATTEMPTS"]):
        assert _post_login(client, csrf=token, password="mauvais").status_code == 200

    response = _post_login(client, csrf=token)

    assert response.status_code == 429
    assert int(response.headers["Retry-After"]) >= 1


def test_expired_lock_resets_the_attempt_window(app, monkeypatch):
    """Une fois LOGIN_LOCKOUT_SECONDS écoulé, le verrou saute et le compteur
    repart à zéro : la connexion valide suivante ouvre une session."""
    client = app.test_client()
    token = _csrf_token(client)
    for _ in range(app.config["LOGIN_MAX_ATTEMPTS"]):
        _post_login(client, csrf=token, password="mauvais")
    assert _post_login(client, csrf=token).status_code == 429

    beyond_lockout = time.time() + app.config["LOGIN_LOCKOUT_SECONDS"] + 1
    monkeypatch.setattr("app.auth.time", _FrozenClock(beyond_lockout))
    assert _post_login(client, csrf=token, password="mauvais").status_code == 200

    response = _post_login(client, csrf=token)

    assert response.status_code == 302


# --- C6 — route protégée -----------------------------------------------------
def test_protected_route_redirects_anonymous_to_login(app):
    """Sans session, /profile redirige vers /login (RG26, CAHIER §10.1)."""
    client = app.test_client()

    response = client.get("/profile")

    assert response.status_code == 302
    assert _location_path(response) == "/login"


def test_protected_route_renders_for_authenticated_user(app):
    """Avec session, /profile est rendue en 200 (RG26)."""
    client = app.test_client()
    token = _csrf_token(client)
    _post_login(client, csrf=token)

    response = client.get("/profile")

    assert response.status_code == 200


# --- C7 — déconnexion --------------------------------------------------------
def test_logout_closes_session_and_returns_to_login(app):
    """Déconnexion : retour au formulaire puis /profile de nouveau protégée."""
    client = app.test_client()
    token = _csrf_token(client)
    _post_login(client, csrf=token)

    response = client.get("/logout")

    assert response.status_code == 302
    assert _location_path(response) == "/login"
    assert _location_path(client.get("/profile")) == "/login"


# --- C8 — paramètre `next` ---------------------------------------------------
def test_external_next_target_is_refused(app):
    """`next` avec hôte absolu : connexion OK mais redirection locale uniquement."""
    client = app.test_client()
    token = _csrf_token(client)

    response = _post_login(
        client, csrf=token, next_target="https://evil.example/voler"
    )

    assert response.status_code == 302
    assert _is_local_target(response.headers["Location"])


def test_protocol_relative_next_target_is_refused(app):
    """`next=//evil.example` (protocole-relatif) : même refus d'open redirect."""
    client = app.test_client()
    token = _csrf_token(client)

    response = _post_login(client, csrf=token, next_target="//evil.example/piege")

    assert response.status_code == 302
    assert _is_local_target(response.headers["Location"])


def test_safe_next_target_is_honoured(app):
    """`next` local relatif : la redirection demandée est respectée."""
    client = app.test_client()
    token = _csrf_token(client)

    response = _post_login(client, csrf=token, next_target="/profile")

    assert response.status_code == 302
    assert _location_path(response) == "/profile"


# --- C9 — cookies et en-têtes ------------------------------------------------
def test_session_cookie_is_httponly_and_samesite(app):
    """Le cookie de session posé par le serveur est HttpOnly et SameSite=Lax
    (CAHIER « cookie de session HttpOnly et SameSite »)."""
    client = app.test_client()

    response = client.get("/login")
    cookie = _session_cookie(response, app.config["SESSION_COOKIE_NAME"])

    assert cookie is not None, "aucun cookie de session posé sur GET /login"
    assert "HttpOnly" in cookie
    assert "SameSite=Lax" in cookie


def test_security_headers_are_present_on_every_response(app):
    """En-têtes de sécurité sur une réponse de formulaire et sur une 404
    (CAHIER : « en-têtes de sécurité sur toutes les réponses »)."""
    client = app.test_client()

    form_response = client.get("/login")
    missing_response = client.get("/chemin-inexistant")

    for response in (form_response, missing_response):
        assert response.headers.get("Content-Security-Policy"), "CSP absente"
        assert "frame-ancestors 'none'" in response.headers["Content-Security-Policy"]
        assert response.headers.get("X-Content-Type-Options") == "nosniff"
        assert response.headers.get("X-Frame-Options") in ("DENY", "SAMEORIGIN")
        assert response.headers.get("Referrer-Policy")
        assert response.headers.get("Permissions-Policy")
    assert missing_response.status_code == 404


def test_tampered_session_cookie_is_not_accepted(app):
    """Cookie de session falsifié : signature invalide, accès refusé (RG25)."""
    client = app.test_client()
    token = _csrf_token(client)
    login_response = _post_login(client, csrf=token)
    cookie = _session_cookie(login_response, app.config["SESSION_COOKIE_NAME"])
    assert cookie is not None
    value = cookie.split(";", 1)[0].split("=", 1)[1]
    # On mute un caractère du milieu : ses bits sont tous significatifs, la
    # signature décodée diffère réellement (contrairement au dernier caractère
    # base64url, dont 4 bits de padding sont inutilisés).
    mid = len(value) // 2
    forged = value[:mid] + ("A" if value[mid] != "A" else "B") + value[mid + 1:]

    # Client vierge présentant uniquement le cookie falsifié (un navigateur
    # remplace son cookie, il n'en garde pas deux du même nom).
    forger = app.test_client()
    forger.set_cookie(app.config["SESSION_COOKIE_NAME"], forged)
    stored = forger.get_cookie(app.config["SESSION_COOKIE_NAME"])
    assert stored is not None and stored.value == forged

    response = forger.get("/profile")

    assert response.status_code == 302
    assert _location_path(response) == "/login"


# --- C9 bis — correctifs sécurité T036 ----------------------------------------

def test_session_cookie_secure_in_production(monkeypatch):
    """En production (APP_ENV=production), le cookie de session porte l'attribut Secure."""
    # Secrets de test (mêmes valeurs que tests/conftest.py:_TEST_ENV)
    test_env = {
        "SECRET_KEY": "test-secret-key-32-bytes-minimum!!",
        "JWT_SECRET_KEY": "test-jwt-secret-key-32-bytes-min!!",
        "ADMIN_PASSWORD": "test-admin-password",
        "DATABASE_URL": "sqlite:///:memory:",
    }
    for name, value in test_env.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("APP_ENV", "production")

    from app import create_app
    from app.config import Config
    from app.extensions import db

    class TestConfig(Config):
        TESTING = True
        SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"

    application = create_app(TestConfig)
    client = application.test_client()

    response = client.get("/login")
    cookie = _session_cookie(response, application.config["SESSION_COOKIE_NAME"])

    assert cookie is not None, "aucun cookie de session posé sur GET /login"
    assert "Secure" in cookie, f"cookie Secure absent en production : {cookie}"

    with application.app_context():
        db.session.remove()
        db.drop_all()


def test_csp_style_src_self(app):
    """Sur une page non-apidocs, la CSP contient "style-src 'self'" sans 'unsafe-inline'."""
    client = app.test_client()

    response = client.get("/login")

    assert response.status_code == 200
    csp = response.headers.get("Content-Security-Policy")
    assert csp, "CSP absente"
    assert "style-src 'self'" in csp, f"style-src 'self' manquant dans CSP : {csp}"
    assert "unsafe-inline" not in csp, f"'unsafe-inline' ne doit pas être dans la CSP hors apidocs : {csp}"


def test_hsts_header_without_include_subdomains(app):
    """Sur une réponse HTTPS, l'en-tête HSTS vaut 'max-age=31536000' sans includeSubDomains."""
    client = app.test_client()

    # Simuler une requête HTTPS via environ_overrides
    response = client.get("/login", environ_overrides={"wsgi.url_scheme": "https"})

    assert response.status_code == 200
    hsts = response.headers.get("Strict-Transport-Security")
    assert hsts, "HSTS absent sur réponse HTTPS"
    assert hsts == "max-age=31536000", f"HSTS inattendu : {hsts}"
    assert "includeSubDomains" not in hsts, f"includeSubDomains ne doit pas être présent : {hsts}"


# --- C10 — mot de passe haché uniquement -------------------------------------
def test_admin_password_is_stored_only_as_hash(app):
    """Après amorçage, la configuration ne conserve que le hash du mot de
    passe admin (CAHIER §10.3, contrainte « jamais en clair »)."""
    stored = app.config.get("ADMIN_PASSWORD_HASH")

    assert "ADMIN_PASSWORD" not in app.config
    assert stored is not None
    assert stored != ADMIN_PASSWORD
    assert ADMIN_PASSWORD not in stored
    assert ADMIN_PASSWORD not in [
        value for value in app.config.values() if isinstance(value, str)
    ]
    assert check_password_hash(stored, ADMIN_PASSWORD)
