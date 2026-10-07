"""Fonctions pures de déduction de rôle — RG15 → RG16.

Stratégies, par ordre de priorité :
  1. Convention de nommage ``lettre + 3 chiffres`` (ex: ``web-a500`` → ``Application``).
  2. Tags ``role:xxx`` extraits du champ ``tags`` de la VM (ex: ``role:web`` → ``Web``).

Aucune dépendance réseau ni base de données ; fonctions pures sur chaînes
et listes, compatibles avec les jeux de mock.

La table ``ROLE_MAP`` et ``TAG_ROLE_MAP`` reproduit celles du référence
``reference/CloudInventory.v2/collector/inventory_runner.py`` mais en
formule pure (importable sans application Flask).
"""

import re

# Convention lettre+3 chiffres → rôle fonctionnel (RG15)
ROLE_MAP = {
    "a": "Application",
    "b": "Base de données",
    "c": "Communication",
    "d": "DNS",
    "f": "Fichiers",
    "h": "Hyperviseur",
    "i": "Impression",
    "j": "Journalisation",
    "l": "Authentification",
    "m": "Messagerie",
    "n": "News",
    "o": "Proxy",
    "p": "Pare-feu",
    "t": "Temps",
    "s": "Supervision",
    "v": "Correctifs",
    "w": "Web",
    "x": "Annuaire",
    "k": "SSI",
    "r": "Ressources",
    "z": "Multifonctions",
}

# Tags ``role:xxx`` → rôle fonctionnel (RG16)
TAG_ROLE_MAP = {
    "web": "Web",
    "api": "Application",
    "database": "Base de données",
    "cache": "Communication",
    "proxy": "Proxy",
    "loadbalancer": "Proxy",
    "messaging": "Communication",
    "storage": "Fichiers",
    "logs": "Journalisation",
    "backup": "Fichiers",
    "dns": "DNS",
    "auth": "Authentification",
    "mail": "Messagerie",
    "network": "Communication",
    "firewall": "Pare-feu",
    "ntp": "Temps",
    "monitoring": "Supervision",
    "alerting": "Supervision",
    "automation": "Ressources",
    "git": "Ressources",
    "secrets": "SSI",
    "ci": "Ressources",
    "test": "Application",
    "migration": "Ressources",
    "deprecated": "Multifonctions",
}


def deduce_role(hostname, tags=None):
    """Déduit le rôle fonctionnel à partir du hostname puis des tags — RG15, RG16.

    Stratégie 1 : analyse ``hostname`` à la recherche d'une lettre suivie de
    exactement 3 chiffres ; la lettre est recherchée dans ``ROLE_MAP``.
    Stratégie 2 : balises ``role:xxx`` présentes dans le champ ``tags`` ;
    le premier tag trouvé dont la valeur est dans ``TAG_ROLE_MAP`` gagne.

    Si aucune stratégie ne trouve de correspondance, renvoie ``"Indéterminé"``.

    Args:
        hostname: chaîne de caractères (vm_name ou autre champ hostname), peut
            être ``None`` ou vide.
        tags: chaîne de caractères contenant des tags séparés par des virgules
            (ex: ``"env:production, role:web, os:debian-12"``), peut être
            ``None``.

    Returns:
        rôle déduit (``str``) ; toujours ``"Indéterminé`` si rien ne matche.
    """
    # Stratégie 1 : lettre + 3 chiffres dans le hostname (RG15)
    if hostname:
        match = re.search(r"([a-zA-Z])(\d{3})", str(hostname))
        if match:
            letter = match.group(1).lower()
            if letter in ROLE_MAP:
                return ROLE_MAP[letter]

    # Stratégie 2 : tags ``role:xxx`` (RG16)
    if tags:
        for tag in str(tags).split(","):
            tag = tag.strip()
            if tag.startswith("role:"):
                role_tag = tag.split(":", 1)[1].strip()
                if role_tag in TAG_ROLE_MAP:
                    return TAG_ROLE_MAP[role_tag]

    return "Indéterminé"