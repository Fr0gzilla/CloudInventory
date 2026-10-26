import pytest

from collector.mock_proxmox import MockProxmoxSource
from collector.roles import infer_role


@pytest.mark.parametrize("letter,role", [
    ("a", "Application"), ("b", "Base de données"),
    ("c", "Communication"), ("d", "DNS"), ("f", "Fichiers"),
    ("h", "Hyperviseur"), ("i", "Impression"),
    ("j", "Journalisation"), ("k", "SSI"),
    ("l", "Authentification"), ("m", "Messagerie"),
    ("n", "News"), ("o", "Proxy"), ("p", "Pare-feu"),
    ("r", "Ressources"), ("s", "Supervision"),
    ("t", "Temps"), ("v", "Correctifs"), ("w", "Web"),
    ("x", "Annuaire"), ("z", "Multifonctions"),
])
def test_convention_maps_each_of_21_letters_before_tags(letter, role):
    assert infer_role({"vm_name": f"host-{letter}500", "tags": "role:api"}) == role


@pytest.mark.parametrize("name", ["host-a50", "host-a5000", "host-e500", "unknown"])
def test_invalid_convention_without_usable_tag_is_indeterminate(name):
    assert infer_role({"vm_name": name, "tags": "role:unknown"}) == "Indéterminé"


@pytest.mark.parametrize("tag,role", [
    ("role:web", "Web"), ("role:api", "Application"),
])
def test_role_tag_is_used_when_convention_is_missing(tag, role):
    assert infer_role({"vm_name": "unknown", "tags": tag}) == role


def test_mock_proxmox_assets_have_expected_roles():
    assets = MockProxmoxSource().collect()
    roles = {asset["vm_name"]: infer_role(asset) for asset in assets}
    assert roles["ghost-vm"] == "Indéterminé"
    assert roles["web-a500"] == "Application"
    assert roles["db-b500"] == "Base de données"
