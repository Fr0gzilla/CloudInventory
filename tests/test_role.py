"""T021 — Tests unitaires du module collector/role.py (RG15 → RG16).

Critères : déduction de rôle sur jeux de mocks, priorités RG15/RG16,
cas limites, absence de mutation des entrées.
"""

import collector.role as role


def test_role_by_hostname_letter_three_digits():
    """RG15 : hostname lettre+3 chiffres déduit le rôle (ex: web-a500 → Application)."""
    assert role.deduce_role("web-a500") == "Application"
    assert role.deduce_role("db-b500") == "Base de données"
    assert role.deduce_role("dns-d500") == "DNS"
    assert role.deduce_role("proxy-o100") == "Proxy"


def test_role_by_hostname_case_insensitive():
    """RG15 : la lettre est mise en minuscule avant lookup."""
    assert role.deduce_role("WEB-A500") == "Application"
    assert role.deduce_role("Db-B500") == "Base de données"


def test_role_by_tag_role_xxx():
    """RG16 : tag role:xxx dans le champ tags déduit le rôle."""
    assert role.deduce_role("my-server", "role:web") == "Web"
    assert role.deduce_role("my-server", "role:database") == "Base de données"
    assert role.deduce_role("my-server", "role:monitoring") == "Supervision"


def test_role_priority_hostname_over_tag():
    """RG15/RG16 : la stratégie hostname (RG15) passe avant les tags (RG16)."""
    assert role.deduce_role("web-a500", "role:monitoring") == "Application"


def test_role_no_match_returns_indetermine():
    """Aucune stratégie ne trouve de correspondance → "Indéterminé"."""
    assert role.deduce_role("random-host") == "Indéterminé"
    assert role.deduce_role("random-host", "role:unknown") == "Indéterminé"


def test_role_none_hostname():
    """None ou chaîne vide en hostname → on passe aux tags ou Indéterminé."""
    assert role.deduce_role(None) == "Indéterminé"
    assert role.deduce_role("") == "Indéterminé"


def test_role_none_tags():
    """None en tags → on essaie d'abord le hostname, puis Indéterminé."""
    assert role.deduce_role("db-b500", None) == "Base de données"


def test_role_no_mutation_hostname():
    """Les données d'entrée hostname ne doivent pas être modifiées."""
    original = "Web-A500.Prod"
    _ = role.deduce_role(original)
    assert original == "Web-A500.Prod"


def test_role_no_mutation_tags():
    """Les données d'entrée tags ne doivent pas être modifiées."""
    original = "env:prod, role:web, os:debian-12"
    _ = role.deduce_role("server1", original)
    assert original == "env:prod, role:web, os:debian-12"


def test_role_tag_without_role_prefix():
    """Tags contenant 'web' mais sans préfix role: n'activent pas la stratégie RG16."""
    assert role.deduce_role("server1", "web, env:prod") == "Indéterminé"


def test_role_empty_tags_string():
    """Chaîne tags vide → on passe à Indéterminé si hostname ne matche pas."""
    assert role.deduce_role("random-host", "") == "Indéterminé"


def test_role_hostname_no_pattern():
    """Hostname sans pattern lettre+3 chiffres → on essaie les tags."""
    assert role.deduce_role("my-vm", "role:web") == "Web"
    assert role.deduce_role("my-vm") == "Indéterminé"


def test_role_hostname_web_w500():
    """RG15 : hostname avec lettre 'w' → Web (ex: web-w500)."""
    assert role.deduce_role("web-w500") == "Web"


def test_role_hostname_firewall_p200():
    """RG15 : hostname avec lettre 'p' → Pare-feu (ex: fw-p200)."""
    assert role.deduce_role("fw-p200") == "Pare-feu"