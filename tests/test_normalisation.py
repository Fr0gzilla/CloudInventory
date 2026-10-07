"""T021 — Tests unitaires du module collector/normalisation.py (RG14).

Critères : fonctions publiques, cas limites RG14, normalisation des données
mocks, absence de mutation.

Deux fonctions pures sont testées :
- `normalize_hostname` : minuscules, strip, suppression suffixe domaine.
- `normalize_fqdn`   : minuscules, strip, premier segment FQDN.
"""

import collector.normalisation as norm


def test_normalize_hostname_basic():
    """Hostname simple : conversion en minuscules, strip, suppression suffixe."""
    assert norm.normalize_hostname("web-a500.prod.local") == "web-a500"


def test_normalize_hostname_lowercase():
    """Minuscules imposées."""
    assert norm.normalize_hostname("WEB-A500.PROD.LOCAL") == "web-a500"


def test_normalize_hostname_strip_spaces():
    """Suppression des espaces."""
    assert norm.normalize_hostname("  web-a500  ") == "web-a500"


def test_normalize_hostname_no_domain():
    """Sans point, la chaîne est retournée telle quelle (après strip/lower)."""
    assert norm.normalize_hostname("web-a500") == "web-a500"


def test_normalize_hostname_empty_string():
    """Chaîne vide → chaîne vide."""
    assert norm.normalize_hostname("") == ""


def test_normalize_hostname_none():
    """None → chaîne vide."""
    assert norm.normalize_hostname(None) == ""


def test_normalize_hostname_multi_segment_domain():
    """Ne conserve que le premier segment après le premier point."""
    assert norm.normalize_hostname("a.b.c.d") == "a"


def test_normalize_fqdn_basic():
    """FQDN simple : même logique que normalize_hostname."""
    assert norm.normalize_fqdn("web-a500.prod.local") == "web-a500"


def test_normalize_fqdn_lowercase():
    """Minuscules imposées."""
    assert norm.normalize_fqdn("WEB-A500.PROD.LOCAL") == "web-a500"


def test_normalize_fqdn_strip():
    """Strip appliqué."""
    assert norm.normalize_fqdn("  web-a500  ") == "web-a500"


def test_normalize_fqdn_no_domain():
    """Sans point, retourne la chaîne stripée en minuscules."""
    assert norm.normalize_fqdn("web-a500") == "web-a500"


def test_normalize_fqdn_empty_string():
    """Chaîne vide → chaîne vide."""
    assert norm.normalize_fqdn("") == ""


def test_normalize_fqdn_none():
    """None → chaîne vide."""
    assert norm.normalize_fqdn(None) == ""


def test_normalize_no_mutation_hostname():
    """Les données d'entrée ne doivent pas être modifiées."""
    original = "Web-A500.Prod.Local"
    _ = norm.normalize_hostname(original)
    assert original == "Web-A500.Prod.Local"


def test_normalize_no_mutation_fqdn():
    """Les données d'entrée ne doivent pas être modifiées."""
    original = "Web-A500.Prod.Local"
    _ = norm.normalize_fqdn(original)
    assert original == "Web-A500.Prod.Local"


def test_normalize_hostname_int_string():
    """Conversion en str : un entier passé en argument (pas de point, pas de domaine)."""
    assert norm.normalize_hostname(42) == "42"


def test_normalize_fqdn_int_string():
    """Conversion en str pour FQDN."""
    assert norm.normalize_fqdn(42) == "42"