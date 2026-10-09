"""T038 — styles hors des gabarits (MEDIUM-05).

C1 : aucun gabarit rendu ne contient `<style` ni l'attribut `style=` (la CSP
impose `style-src 'self'` sans `'unsafe-inline'` : un navigateur bloquerait le
style inline) ; la feuille `css/app.css` est liée depuis les pages et servie en
200 `text/css` depuis l'origine.

Pages contrôlées : `/login` (gabarit autonome, historiquement porteur du bloc
`<style>`), le tableau de bord et la fiche asset (gabarit `base.html`).
"""
import re

_CSRF_FIELD = re.compile(r'name="csrf_token"\s+value="([^"]+)"')
_STYLE_TAG = re.compile(r"<style", re.IGNORECASE)
_STYLE_ATTR = re.compile(r"\sstyle\s*=")
_APP_CSS_LINK = re.compile(r'<link rel="stylesheet" href="[^"]*css/app\.css"')


def _login(client):
    """Session admin : la connexion vide la session, le CSRF doit être relu."""
    response = client.get("/login")
    assert response.status_code == 200
    token = _CSRF_FIELD.search(response.get_data(as_text=True))
    assert token, "formulaire de connexion sans champ csrf_token"
    response = client.post("/login", data={
        "username": "admin",
        "password": "test-admin-password",
        "csrf_token": token.group(1),
    })
    assert response.status_code == 302


def _page(client, path):
    """HTML d'une page rendue en 200 pour la session en cours."""
    response = client.get(path)
    assert response.status_code == 200, f"{path} n'est pas rendu en 200"
    return response.get_data(as_text=True)


def _pages(app, asset):
    """Corps rendus de la connexion, du tableau de bord et de la fiche asset."""
    client = app.test_client()
    pages = {"/login": _page(client, "/login")}
    _login(client)
    for path in ("/", f"/assets/{asset.id}"):
        pages[path] = _page(client, path)
    return pages


def test_rendered_templates_contain_no_style_tag_nor_style_attribute(app, asset):
    """C1a : ni `<style>` ni attribut `style=` dans les pages rendues."""
    pages = _pages(app, asset)

    for path, body in pages.items():
        assert not _STYLE_TAG.search(body), f"bloc <style> inline dans {path}"
        assert not _STYLE_ATTR.search(body), f"attribut style= dans {path}"


def test_pages_link_the_app_css_stylesheet(app, asset):
    """C1b : `css/app.css` est lié depuis chaque page rendue."""
    pages = _pages(app, asset)

    for path, body in pages.items():
        assert _APP_CSS_LINK.search(body), f"css/app.css non lié depuis {path}"


def test_app_css_is_served_as_text_css(app):
    """C1c : la feuille répond 200 `text/css` ; une feuille absente reste 404."""
    client = app.test_client()

    response = client.get("/static/css/app.css")

    assert response.status_code == 200
    assert response.mimetype == "text/css"
    assert client.get("/static/css/inexistante.css").status_code == 404


def test_regles_de_page_de_connexion_limitees_a_sa_page():
    """La mise en page de la connexion (body en flex centré) ne touche que /login : dans la feuille commune, une règle
    sur `body` seul s'appliquerait à toutes les pages (vu le 9 oct. : barre et contenu côte à côte)."""
    from pathlib import Path
    racine = Path(__file__).resolve().parent.parent
    css = (racine / "app" / "static" / "css" / "app.css").read_text(encoding="utf-8")
    sans_commentaires = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    for selecteur, corps in re.findall(r"([^{}]+)\{([^{}]*)\}", sans_commentaires):
        if selecteur.strip() == "body":
            assert "display" not in corps and "min-height" not in corps, corps
    assert "body.page-login" in css
    assert '<body class="page-login">' in (racine / "app" / "templates" / "login.html").read_text(encoding="utf-8")


def test_page_du_tableau_de_bord_sans_classe_de_connexion(app):
    client = app.test_client()
    _login(client)
    page = client.get("/").get_data(as_text=True)
    assert "page-login" not in page

