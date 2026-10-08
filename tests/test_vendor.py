"""T028 — Présence et non-vacuité des ressources locales app/static/vendor/.

Six fichiers servis en local (Bootstrap css+js, Bootstrap Icons css+2 fontes,
Chart.js), les deux fontes réellement référencées par le `@font-face` du CSS
Icons, et la documentation des versions (`VERSIONS.md`, cahier §5.1).

Lecture de fichiers uniquement : aucun test ne sollicite le réseau. Chaque
« détecte_* » reproduit l'écart sur `tmp_path` pour prouver que l'assertion
peut échouer (suite verte obligée, jamais un test vide).
"""
import pathlib

VENDOR = pathlib.Path(__file__).resolve().parents[1] / "app" / "static" / "vendor"

# Les six fichiers livrés (consigne T028, critère 1 / VERSIONS.md).
VENDOR_FILES = (
    "bootstrap.min.css",
    "bootstrap.bundle.min.js",
    "bootstrap-icons.min.css",
    "fonts/bootstrap-icons.woff2",
    "fonts/bootstrap-icons.woff",
    "chart.umd.min.js",
)

ICONS_CSS = "bootstrap-icons.min.css"
VERSIONS_DOC = "VERSIONS.md"

# Versions imposées par le cahier des charges §5.1, par fichier documenté.
DOC_VERSIONS = {
    "bootstrap.min.css": "5.3.3",
    "bootstrap.bundle.min.js": "5.3.3",
    "bootstrap-icons.min.css": "1.11.3",
    "fonts/bootstrap-icons.woff2": "1.11.3",
    "fonts/bootstrap-icons.woff": "1.11.3",
    "chart.umd.min.js": "4.4.7",
}


def _gaps(root, relpaths):
    """Chemins absents ou vides sous `root` ; liste vide = tous fournis."""
    gaps = []
    for rel in relpaths:
        path = root / rel
        if not path.is_file():
            gaps.append(f"{rel} : absent")
        elif path.stat().st_size == 0:
            gaps.append(f"{rel} : vide")
    return gaps


def _read(root, relpath):
    return (root / relpath).read_text(encoding="utf-8", errors="replace")


def _font_urls(css_text):
    """Cibles d'url(...) locales du CSS Icons, sans le suffixe `?hachage`."""
    targets = set()
    for chunk in css_text.split("url(")[1:]:
        raw = chunk.split(")", 1)[0].strip().strip("\"'")
        if raw.startswith(("http://", "https://", "//", "data:")):
            continue
        targets.add(raw.split("?", 1)[0])
    return targets


# --- C1 — les six fichiers vendor existent et ne sont pas vides -------------


def test_each_of_the_six_vendor_files_is_present_and_non_empty():
    """Les six fichiers de app/static/vendor/ sont servis en local, non vides."""
    assert _gaps(VENDOR, VENDOR_FILES) == []


# --- C2 — les deux fontes référencées par le CSS Icons ----------------------


def test_icons_css_references_exactly_the_two_delivered_fonts():
    """Le @font-face du CSS Icons ne référence que woff2 et woff, en local."""
    urls = _font_urls(_read(VENDOR, ICONS_CSS))

    assert urls == {"fonts/bootstrap-icons.woff2", "fonts/bootstrap-icons.woff"}


def test_fonts_referenced_by_icons_css_exist_and_are_non_empty():
    """Chaque fonte citée par le CSS Icons est résolue à côté de lui, non vide."""
    referenced = sorted(_font_urls(_read(VENDOR, ICONS_CSS)))

    assert _gaps(VENDOR, referenced) == []


# --- C3 — la documentation des versions -------------------------------------


def test_versions_documentation_is_present_and_non_empty():
    """app/static/vendor/VERSIONS.md documente la livraison (non vide)."""
    assert _gaps(VENDOR, [VERSIONS_DOC]) == []


def test_versions_documentation_lists_the_six_files():
    """VERSIONS.md nomme chacun des six fichiers livrés."""
    doc = _read(VENDOR, VERSIONS_DOC)

    missing = [name for name in VENDOR_FILES if name not in doc]

    assert missing == []


def test_versions_documentation_records_the_required_versions():
    """Chaque fichier documenté porte la version imposée par le cahier §5.1."""
    lines = _read(VENDOR, VERSIONS_DOC).splitlines()

    undocumented = [
        f"{name} : {version}"
        for name, version in DOC_VERSIONS.items()
        if not any(name in line and version in line for line in lines)
    ]

    assert undocumented == []


# --- C4 — les contrôles échouent bien quand la ressource manque -------------


def test_detects_missing_vendor_file(tmp_path):
    """Un fichier vendor absent est signalé (absence simulée dans tmp_path)."""
    assert _gaps(tmp_path, ["bootstrap.min.css"]) == ["bootstrap.min.css : absent"]


def test_detects_empty_vendor_file(tmp_path):
    """Un fichier vendor vide est signalé (vacuité simulée dans tmp_path)."""
    (tmp_path / "chart.umd.min.js").touch()

    assert _gaps(tmp_path, ["chart.umd.min.js"]) == ["chart.umd.min.js : vide"]


def test_detects_font_referenced_by_icons_css_but_missing(tmp_path):
    """Une fonte citée par le CSS mais absente du dossier est signalée."""
    (tmp_path / ICONS_CSS).write_text(
        '@font-face{src:url("fonts/bootstrap-icons.woff2")}',
        encoding="utf-8",
    )
    referenced = sorted(_font_urls(_read(tmp_path, ICONS_CSS)))

    assert referenced == ["fonts/bootstrap-icons.woff2"]
    assert _gaps(tmp_path, referenced) == ["fonts/bootstrap-icons.woff2 : absent"]
