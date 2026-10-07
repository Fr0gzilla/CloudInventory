"""Sources de l'inventaire : mocks déterministes et clients réels interchangeables.

Interface commune (mêmes fonctions, mêmes signatures, mêmes formats de dict) :

- virtualisation : ``fetch_mock_vms()`` / ``fetch_proxmox_vms()`` -> list[dict]
- IPAM            : ``fetch_mock_ipam()`` / ``fetch_ipam_records()`` -> list[dict]

Sélection par configuration (cahier §7, variables USE_MOCK_VIRT et
USE_MOCK_IPAM, défaut ``true``) lues via ``app.config.Config`` :

- ``collect_vms()``  -> mock si ``USE_MOCK_VIRT``, sinon client Proxmox réel
- ``collect_ipam()`` -> mock si ``USE_MOCK_IPAM``,  sinon client NetBox réel

Transport des clients réels : ``transport(url, headers, timeout, verify_ssl)
-> dict`` (corps JSON complet), par défaut urllib — les tests injectent un
objet de remplacement et n'ouvrent aucune connexion réseau.
"""

from app.config import Config

from collector.mock_netbox import fetch_mock_ipam
from collector.mock_virtualisation import fetch_mock_vms
from collector.netbox_client import NetBoxClientError, fetch_ipam_records
from collector.proxmox_client import ProxmoxClientError, fetch_proxmox_vms

__all__ = [
    "NetBoxClientError",
    "ProxmoxClientError",
    "collect_ipam",
    "collect_vms",
    "fetch_ipam_records",
    "fetch_mock_ipam",
    "fetch_mock_vms",
    "fetch_proxmox_vms",
    "get_ipam_source",
    "get_vms_source",
]


def get_vms_source():
    """Retourne la fonction de collecte virtualisation selon USE_MOCK_VIRT."""
    return fetch_mock_vms if Config.use_mock_virt() else fetch_proxmox_vms


def get_ipam_source():
    """Retourne la fonction de collecte IPAM selon USE_MOCK_IPAM."""
    return fetch_mock_ipam if Config.use_mock_ipam() else fetch_ipam_records


def collect_vms():
    """Collecte la virtualisation via la source choisie par configuration."""
    return get_vms_source()()


def collect_ipam():
    """Collecte l'IPAM via la source choisie par configuration."""
    return get_ipam_source()()
