"""Functional roles derived from virtualization observations."""

import re

from collector.normalization import normalize
from collector.source import AssetRecord

_CONVENTION_ROLES = {
    "a": "Application", "b": "Base de données", "c": "Communication",
    "d": "DNS", "f": "Fichiers", "h": "Hyperviseur", "i": "Impression",
    "j": "Journalisation", "k": "SSI", "l": "Authentification",
    "m": "Messagerie", "n": "News", "o": "Proxy", "p": "Pare-feu",
    "r": "Ressources", "s": "Supervision", "t": "Temps", "v": "Correctifs",
    "w": "Web", "x": "Annuaire", "z": "Multifonctions",
}
_TAG_ROLES = {"web": "Web", "api": "Application"}


def infer_role(asset: AssetRecord) -> str:
    """Prefer a recognized letter + three digits, then a supported role tag."""
    convention = re.fullmatch(r"(?:[a-z0-9-]+-)?([a-z])\d{3}", normalize(asset.get("vm_name")))
    if convention and convention.group(1) in _CONVENTION_ROLES:
        return _CONVENTION_ROLES[convention.group(1)]
    for tag in re.split(r"[,;\s]+", asset.get("tags") or ""):
        key, separator, value = tag.lower().partition(":")
        if separator and key == "role" and value in _TAG_ROLES:
            return _TAG_ROLES[value]
    return "Indéterminé"
