"""T005 — Sources de virtualisation : mock déterministe, client Proxmox réel,
interface commune et sélection par configuration.

Critères :
- C1 45 VM/CT déterministes, copies isolées (collector/mock_virtualisation.py)
- C2 interface et formats mock/réel interchangeables (collector/__init__.py)
- C3 sélection de la source par configuration USE_MOCK_VIRT (app/config.py)
- C4 transport injecté, aucun appel réseau (collector/proxmox_client.py)
- C5 timeouts explicites
- C6 erreurs explicites
- C7 aucun secret exposé dans les messages d'erreur
- C8 cas VM (qemu) et CT (lxc) au format réel

Aucun réseau : `urllib.request.urlopen` est neutralisé par une fixture autouse
et les clients réels reçoivent un transport de test.
"""
import inspect
import json
import ssl
import urllib.error
import urllib.request

import pytest

from app.config import Config
from collector import (
    collect_vms,
    fetch_mock_vms,
    fetch_proxmox_vms,
    get_vms_source,
)
from collector.mock_virtualisation import MOCK_VMS
from collector.proxmox_client import DEFAULT_TIMEOUT, ProxmoxClientError

# Clés documentées pour une VM/CT (mock et réel identiques, map.md:43).
EXPECTED_KEYS = {
    "vm_id", "vm_name", "type", "node", "status", "tags", "ip_reported",
    "os", "fqdn", "annotation", "cpu_count", "cpu_usage", "ram_max",
    "ram_used", "disk_max", "disk_used", "uptime",
}

# Jetons factices — jamais un secret réel (C4/C7).
_TOKEN_ID = "automation@pve!unit"
_TOKEN_SECRET = "unit-test-token-secret"
_BASE = "https://pve.test:8006/api2/json"


def _u(path):
    return f"{_BASE}{path}"


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """Tout appel réseau direct est une erreur : les tests n'ouvrent rien."""

    def _forbidden(*args, **kwargs):
        raise AssertionError("appel réseau interdit dans les tests")

    monkeypatch.setattr(urllib.request, "urlopen", _forbidden)
    monkeypatch.setattr(urllib.request.OpenerDirector, "open", _forbidden)


@pytest.fixture()
def proxmox_env(monkeypatch):
    """Cible et jetons factices, USE_MOCK_VIRT retiré (état propre par test)."""
    monkeypatch.delenv("USE_MOCK_VIRT", raising=False)
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("PROXMOX_URL", "https://pve.test:8006/")
    monkeypatch.setenv("PROXMOX_TOKEN_ID", _TOKEN_ID)
    monkeypatch.setenv("PROXMOX_TOKEN_SECRET", _TOKEN_SECRET)
    monkeypatch.setenv("PROXMOX_VERIFY_SSL", "false")


class FakeTransport:
    """Transport de test : routes URL -> payload JSON, trace des appels."""

    def __init__(self, routes):
        self.routes = routes  # dict url -> payload, ou callable(url) -> payload
        self.calls = []

    def __call__(self, url, headers, timeout, verify_ssl):
        self.calls.append((url, dict(headers), timeout, verify_ssl))
        if callable(self.routes):
            return self.routes(url)
        if url in self.routes:
            return self.routes[url]
        raise AssertionError(f"URL inattendue : {url}")


class RaisingTransport:
    """Transport de test qui lève une erreur déterminée à chaque appel."""

    def __init__(self, exc):
        self.exc = exc
        self.calls = 0

    def __call__(self, url, headers, timeout, verify_ssl):
        self.calls += 1
        raise self.exc


_QEMU_STATUS = {
    "cpu": 0.325,
    "maxmem": 8 * 1073741824, "mem": 5 * 1073741824,
    "maxdisk": 50 * 1073741824, "disk": 22 * 1073741824,
    "uptime": 864000,
    "nics": {
        "net0": {"ip-addresses": [
            {"ip-address": "127.0.0.1", "ip-address-type": "ipv4"},
            {"ip-address": "10.0.1.10", "ip-address-type": "ipv4"},
            {"ip-address": "fe80::1", "ip-address-type": "ipv6"},
        ]},
    },
}
_QEMU_CONFIG = {
    "cores": 4,
    "tags": "env:production;role:web",
    "ostype": "l26",
    "searchdomain": "prod.local",
    "description": "Serveur web principal",
}
_LXC_STATUS = {
    "cpu": 0.087,
    "maxmem": 4 * 1073741824, "mem": 3 * 1073741824,
    "maxdisk": 10 * 1073741824, "disk": 2 * 1073741824,
    "uptime": 2592000,
}
_LXC_CONFIG = {
    "cores": 2,
    "tags": "env:production;role:cache",
    "ostype": "alpine",
    "description": "Cache Redis",
    "net0": "name=eth0,bridge=vmbr0,ip=10.0.1.14/24,gw=10.0.1.1",
}


def _cluster_responses(**overrides):
    """Cluster Proxmox de test : 1 nœud, une VM qemu et un CT lxc."""
    routes = {
        _u("/nodes"): {"data": [{"node": "pve1"}]},
        _u("/nodes/pve1/qemu"): {"data": [{"vmid": 100, "name": "web-a500",
                                           "status": "running"}]},
        _u("/nodes/pve1/lxc"): {"data": [{"vmid": 104, "name": "cache-redis",
                                          "status": "running"}]},
        _u("/nodes/pve1/qemu/100/status/current"): {"data": dict(_QEMU_STATUS)},
        _u("/nodes/pve1/qemu/100/config"): {"data": dict(_QEMU_CONFIG)},
        _u("/nodes/pve1/lxc/104/status/current"): {"data": dict(_LXC_STATUS)},
        _u("/nodes/pve1/lxc/104/config"): {"data": dict(_LXC_CONFIG)},
    }
    routes.update(overrides)
    return routes


# ══════════════════════════════════════════════════════════════════
# C1 — mock déterministe : 45 VM/CT, copies isolées
# ══════════════════════════════════════════════════════════════════

def test_fetch_mock_vms_returns_exactly_45_records():
    assert len(fetch_mock_vms()) == 45


def test_fetch_mock_vms_is_identical_between_two_calls():
    assert fetch_mock_vms() == fetch_mock_vms()


def test_fetch_mock_vms_returns_isolated_copies():
    first = fetch_mock_vms()
    first.pop()
    first[0]["vm_name"] = "alteree"

    second = fetch_mock_vms()
    assert len(second) == 45
    assert second[0]["vm_name"] == "web-a500"
    assert second[0] is not first[0]
    assert len(MOCK_VMS) == 45
    assert MOCK_VMS[0]["vm_name"] == "web-a500"


def test_mock_dataset_covers_five_nodes_and_both_vm_types():
    vms = fetch_mock_vms()
    nodes = {vm["node"] for vm in vms}
    assert nodes == {"pve1", "pve2", "pve3", "pve4", "pve5"}
    assert {"qemu", "lxc"} <= {vm["type"] for vm in vms}
    for node in nodes:
        assert sum(1 for vm in vms if vm["node"] == node) >= 8

    # Cas spéciaux des anomalies (map.md:46) présents dans le jeu.
    by_id = {vm["vm_id"]: vm for vm in vms}
    assert by_id["997"]["fqdn"] is None
    assert by_id["996"]["ip_reported"] == "10.0.9.98"
    assert by_id["999"]["fqdn"] == "decom-server.legacy.local"


def test_mock_records_expose_the_documented_keys():
    for vm in fetch_mock_vms():
        assert set(vm) == EXPECTED_KEYS


# ══════════════════════════════════════════════════════════════════
# C2 — interface commune mock / réel
# ══════════════════════════════════════════════════════════════════

def test_vms_sources_accept_a_call_without_required_argument():
    assert len(inspect.signature(fetch_mock_vms).parameters) == 0
    real_params = inspect.signature(fetch_proxmox_vms).parameters
    assert list(real_params) == ["transport"]
    assert real_params["transport"].default is None
    assert len(inspect.signature(collect_vms).parameters) == 0


def test_mock_and_real_sources_share_the_same_record_keys(proxmox_env):
    real = fetch_proxmox_vms(transport=FakeTransport(_cluster_responses()))
    mock = fetch_mock_vms()

    assert set(real[0]) == set(mock[0])
    assert all(set(record) == EXPECTED_KEYS for record in real)
    assert isinstance(real[0]["vm_id"], str) and isinstance(mock[0]["vm_id"], str)
    assert isinstance(real[0]["type"], str) and isinstance(mock[0]["type"], str)


def test_real_source_returns_mock_format_without_network(proxmox_env):
    transport = FakeTransport(_cluster_responses())
    records = fetch_proxmox_vms(transport=transport)

    assert transport.calls  # le transport injecté est bien utilisé
    assert isinstance(records, list)
    assert len(records) == 2
    for record in records:
        assert set(record) == EXPECTED_KEYS


# ══════════════════════════════════════════════════════════════════
# C3 — sélection de la source par configuration
# ══════════════════════════════════════════════════════════════════

def test_use_mock_virt_defaults_to_true(monkeypatch):
    monkeypatch.delenv("USE_MOCK_VIRT", raising=False)
    assert Config.use_mock_virt() is True
    assert get_vms_source() is fetch_mock_vms


def test_use_mock_virt_false_selects_the_proxmox_source(monkeypatch):
    monkeypatch.setenv("USE_MOCK_VIRT", "false")
    assert Config.use_mock_virt() is False
    assert get_vms_source() is fetch_proxmox_vms


def test_collect_vms_returns_the_mock_dataset_by_default(monkeypatch):
    monkeypatch.delenv("USE_MOCK_VIRT", raising=False)
    assert len(collect_vms()) == 45


def test_collect_vms_real_source_fails_explicitly_without_tokens(monkeypatch):
    monkeypatch.setenv("USE_MOCK_VIRT", "false")
    monkeypatch.delenv("PROXMOX_TOKEN_ID", raising=False)
    monkeypatch.delenv("PROXMOX_TOKEN_SECRET", raising=False)

    with pytest.raises(ProxmoxClientError) as excinfo:
        collect_vms()
    assert "PROXMOX_TOKEN_ID" in str(excinfo.value)


# ══════════════════════════════════════════════════════════════════
# C4 — transport injecté : cible, timeout, SSL
# ══════════════════════════════════════════════════════════════════

def test_transport_receives_url_timeout_and_ssl_flag(monkeypatch, proxmox_env):
    monkeypatch.setenv("PROXMOX_VERIFY_SSL", "true")
    transport = FakeTransport(_cluster_responses())

    fetch_proxmox_vms(transport=transport)

    assert transport.calls
    for url, headers, timeout, verify_ssl in transport.calls:
        assert url.startswith("https://pve.test:8006/api2/json/")
        assert timeout == DEFAULT_TIMEOUT
        assert verify_ssl is True
    assert transport.calls[0][1]["Authorization"].startswith(
        f"PVEAPIToken={_TOKEN_ID}="
    )


# ══════════════════════════════════════════════════════════════════
# C5 / C6 — timeouts et erreurs explicites
# ══════════════════════════════════════════════════════════════════

def test_timeout_raises_an_explicit_error_with_configured_delay(proxmox_env):
    with pytest.raises(ProxmoxClientError) as excinfo:
        fetch_proxmox_vms(transport=RaisingTransport(TimeoutError("late")))

    message = str(excinfo.value)
    assert message.startswith("Proxmox:")
    assert "délai dépassé" in message
    assert str(DEFAULT_TIMEOUT) in message


def test_http_error_raises_an_explicit_error(proxmox_env):
    error = urllib.error.HTTPError(_u("/nodes"), 401, "Unauthorized", None, None)
    with pytest.raises(ProxmoxClientError) as excinfo:
        fetch_proxmox_vms(transport=RaisingTransport(error))

    message = str(excinfo.value)
    assert "HTTP 401" in message
    assert "PROXMOX_URL" in message
    assert "PROXMOX_TOKEN_ID" in message


def test_connection_error_raises_an_explicit_error(proxmox_env):
    error = urllib.error.URLError("refus de connexion")
    with pytest.raises(ProxmoxClientError) as excinfo:
        fetch_proxmox_vms(transport=RaisingTransport(error))

    message = str(excinfo.value)
    assert "connexion impossible" in message
    assert "refus de connexion" in message


def test_ssl_error_is_converted_to_proxmox_client_error(proxmox_env):
    transport = RaisingTransport(ssl.SSLError("certificate verification failed"))

    with pytest.raises(ProxmoxClientError):
        fetch_proxmox_vms(transport=transport)

    assert transport.calls == 1


def test_value_error_is_converted_to_proxmox_client_error(proxmox_env):
    transport = RaisingTransport(ValueError("invalid transport value"))

    with pytest.raises(ProxmoxClientError):
        fetch_proxmox_vms(transport=transport)

    assert transport.calls == 1


def test_invalid_json_response_raises_an_explicit_error(proxmox_env):
    error = json.JSONDecodeError("Expecting value", "{", 0)
    with pytest.raises(ProxmoxClientError) as excinfo:
        fetch_proxmox_vms(transport=RaisingTransport(error))

    assert "JSON invalide" in str(excinfo.value)


def test_payload_that_is_not_an_object_raises_an_explicit_error(proxmox_env):
    transport = FakeTransport(lambda url: [])
    with pytest.raises(ProxmoxClientError) as excinfo:
        fetch_proxmox_vms(transport=transport)

    assert "objet JSON attendu" in str(excinfo.value)


def test_payload_without_data_key_raises_an_explicit_error(proxmox_env):
    transport = FakeTransport(lambda url: {})
    with pytest.raises(ProxmoxClientError) as excinfo:
        fetch_proxmox_vms(transport=transport)

    assert "« data » absent" in str(excinfo.value)


def test_node_list_payload_of_wrong_type_raises_an_explicit_error(proxmox_env):
    transport = FakeTransport(lambda url: {"data": {"node": "pve1"}})
    with pytest.raises(ProxmoxClientError) as excinfo:
        fetch_proxmox_vms(transport=transport)

    assert "liste de nœuds inattendue" in str(excinfo.value)


def test_node_entry_without_node_key_raises_an_explicit_error(proxmox_env):
    transport = FakeTransport(lambda url: {"data": [{"name": "pve1"}]})
    with pytest.raises(ProxmoxClientError) as excinfo:
        fetch_proxmox_vms(transport=transport)

    assert "entrée de nœud invalide" in str(excinfo.value)


def test_vm_list_payload_of_wrong_type_raises_an_explicit_error(proxmox_env):
    transport = FakeTransport(_cluster_responses(
        **{_u("/nodes/pve1/qemu"): {"data": {"vmid": 100}}},
    ))
    with pytest.raises(ProxmoxClientError) as excinfo:
        fetch_proxmox_vms(transport=transport)

    assert "liste de qemu inattendue" in str(excinfo.value)


def test_vm_status_payload_of_wrong_type_raises_an_explicit_error(proxmox_env):
    transport = FakeTransport(_cluster_responses(
        **{_u("/nodes/pve1/qemu/100/status/current"): {"data": ["statut"]}},
    ))
    with pytest.raises(ProxmoxClientError) as excinfo:
        fetch_proxmox_vms(transport=transport)

    assert "statut inattendu" in str(excinfo.value)


def test_vm_config_payload_of_wrong_type_raises_an_explicit_error(proxmox_env):
    transport = FakeTransport(_cluster_responses(
        **{_u("/nodes/pve1/qemu/100/config"): {"data": ["config"]}},
    ))
    with pytest.raises(ProxmoxClientError) as excinfo:
        fetch_proxmox_vms(transport=transport)

    assert "config inattendue" in str(excinfo.value)


def test_missing_token_secret_raises_an_explicit_error(proxmox_env, monkeypatch):
    monkeypatch.delenv("PROXMOX_TOKEN_SECRET", raising=False)
    transport = FakeTransport(_cluster_responses())
    with pytest.raises(ProxmoxClientError) as excinfo:
        fetch_proxmox_vms(transport=transport)

    message = str(excinfo.value)
    assert "PROXMOX_TOKEN_ID" in message
    assert "PROXMOX_TOKEN_SECRET" in message
    assert transport.calls == []  # erreur avant tout appel


# ══════════════════════════════════════════════════════════════════
# C7 — aucun secret exposé
# ══════════════════════════════════════════════════════════════════

def test_error_messages_never_expose_the_token_secret(proxmox_env):
    scenarios = [
        urllib.error.HTTPError(_u("/nodes"), 500, "Server Error", None, None),
        urllib.error.URLError("refus de connexion"),
        TimeoutError("late"),
        json.JSONDecodeError("Expecting value", "{", 0),
    ]
    for error in scenarios:
        with pytest.raises(ProxmoxClientError) as excinfo:
            fetch_proxmox_vms(transport=RaisingTransport(error))
        message = str(excinfo.value)
        assert _TOKEN_SECRET not in message
        assert _TOKEN_ID not in message
        assert "PVEAPIToken" not in message


def test_missing_token_error_does_not_echo_configured_values(proxmox_env,
                                                             monkeypatch):
    monkeypatch.setenv("PROXMOX_TOKEN_SECRET", "")
    with pytest.raises(ProxmoxClientError) as excinfo:
        fetch_proxmox_vms(transport=FakeTransport(_cluster_responses()))

    message = str(excinfo.value)
    assert _TOKEN_ID not in message
    assert _TOKEN_SECRET not in message


# ══════════════════════════════════════════════════════════════════
# C8 — cas VM (qemu) et CT (lxc) au format réel
# ══════════════════════════════════════════════════════════════════

def test_real_source_builds_a_qemu_record_from_guest_agent_ip(proxmox_env):
    records = fetch_proxmox_vms(transport=FakeTransport(_cluster_responses()))
    qemu = records[0]

    assert qemu["vm_id"] == "100"
    assert qemu["vm_name"] == "web-a500"
    assert qemu["type"] == "qemu"
    assert qemu["node"] == "pve1"
    assert qemu["status"] == "running"
    assert qemu["ip_reported"] == "10.0.1.10"  # 127.0.0.1 et IPv6 écartés
    assert qemu["tags"] == "env:production, role:web"
    assert qemu["os"] == "l26"
    assert qemu["fqdn"] == "web-a500.prod.local"
    assert qemu["annotation"] == "Serveur web principal"
    assert qemu["cpu_count"] == 4
    assert qemu["cpu_usage"] == 32.5
    assert qemu["uptime"] == 864000


def test_real_source_builds_an_lxc_record_from_the_net0_config(proxmox_env):
    records = fetch_proxmox_vms(transport=FakeTransport(_cluster_responses()))
    lxc = records[1]

    assert lxc["vm_id"] == "104"
    assert lxc["vm_name"] == "cache-redis"
    assert lxc["type"] == "lxc"
    assert lxc["ip_reported"] == "10.0.1.14"  # masque CIDR retiré
    assert lxc["tags"] == "env:production, role:cache"
    assert lxc["os"] == "alpine"
    assert lxc["fqdn"] is None  # aucun searchdomain côté CT
    assert lxc["annotation"] == "Cache Redis"
    assert lxc["cpu_count"] == 2
    assert lxc["cpu_usage"] == 8.7
