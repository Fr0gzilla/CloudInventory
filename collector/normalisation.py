"""Fonctions pures de normalisation — RG14.

Aucune dépendance réseau ni base de données ; ces fonctions ne font que
transformer des chaînes de caractères selon la convention du projet.
"""

def normalize_hostname(name):
    """Normalise un hostname selon RG14.

    - conversion en minuscules
    - suppression des espaces
    - suppression du suffixe de domaine (tout ce qui suit le premier '')

    Exemple : ``web-a500.prod.local`` → ``web-a500``

    Args:
        name: chaîne éventuellement vide ou ``None``.

    Returns:
        chaîne normalisée (toujours non ``None``, vide si entrée vide).
    """
    if not name:
        return ""
    name = str(name).strip().lower()
    # Supprime le suffixe de domaine après le premier point
    if "." in name:
        name = name.split(".", 1)[0]
    return name


def normalize_fqdn(fqdn):
    """Normalise un FQDN selon RG14 : minuscules, strip, premier segment.

    Exemple : ``web-a500.prod.local`` → ``web-a500``

    Args:
        fqdn: chaîne éventuellement vide ou ``None``.

    Returns:
        chaîne normalisée (toujours non ``None``, vide si entrée vide).
    """
    if not fqdn:
        return ""
    return str(fqdn).strip().lower().split(".", 1)[0]