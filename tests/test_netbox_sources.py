"""T005 — Sources IPAM : mock NetBox déterministe, client NetBox réel,
interface commune et sélection par configuration.

Critères :
- C1 44 records déterministes, copies isolées (collector/mock_netbox.py)
- C2 interface et formats mock/réel interchangeables (collector/__init__.py)
- C3 sélection de la source par configuration USE_MOCK_IPAM (app/config.py)
- C4 pagination des pages NetBox (collector/netbox_client.py)
- C5 timeout explicite par appel
- C6 erreurs explicites (HTTP, connexion, JSON, payload, jeton absent)
- C7 aucun secret exposé dans les messages d'erreur
- C8 couverture des 6 anomalies (cahier des charges §304) avec les deux mocks
- C9 couverture des 4 niveaux de match (docs/modele/regles.md:5) avec les deux mocks

Aucun réseau : `urllib.request.urlopen` est neutralisé par une fixture autouse
et le client réel reçoit un transport de test.
"""
import inspect
import json
import ssl
import urllib.error
import urllib.request
from collections import Counter

import pytest

from app.config import Config
from collector import (
    NetBoxClientError,
    collect_ipam,
    fetch_ipam_records,
    fetch_mock_ipam,
    get_ipam_source,
)
from collector.mock_virtualisation import fetch_mock_vms
from collector.netbox_client import API_PATH, DEFAULT_TIMEOUT

# Clés documentées pour un record IPAM (mock et réel identiques, map.md:44).
EXPECTED_KEYS = ["ip", "dns_name", "status", "tenant", "site", "meta_zone"]

_BASE = "https://netbox.test"
_ENTRY_URL = f"{_BASE}{API_PATH}"
_NEXT_URL = f"{_ENTRY_URL}?limit=2&offset=2"

# Jeton factice — jamais un secret réel (C7).
_TOKEN = "unit-test-netbox-token"


# ══════════════════════════════════════════════════════════════════
# Fixtures et objets de remplacement (aucune connexion réseau)
# ══════════════════════════════════════════════════════════════════

@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """Tout appel réseau direct est une erreur : les tests n'ouvrent rien."""

    def _forbidden(*args, **kwargs):
        raise AssertionError("appel réseau interdit dans les tests")

    monkeypatch.setattr(urllib.request, "urlopen", _forbidden)
    monkeypatch.setattr(urllib.request.OpenerDirector, "open", _forbidden)


@pytest.fixture()
def netbox_env(monkeypatch):
    """Cible et jeton factices, USE_MOCK_IPAM retiré (état propre par test)."""
    monkeypatch.delenv("USE_MOCK_IPAM", raising=False)
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("NETBOX_URL", f"{_BASE}/")
    monkeypatch.setenv("NETBOX_TOKEN", _TOKEN)
    monkeypatch.setenv("NETBOX_VERIFY_SSL", "false")


class FakeTransport:
    """Transport de test : routes URL -> payload JSON, trace des appels."""

    def __init__(self, routes):
        self.routes = routes  # dict url -> payload
        self.calls = []

    def __call__(self, url, headers, timeout, verify_ssl):
        self.calls.append((url, dict(headers), timeout, verify_ssl))
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


def _page(results, nxt=None):
    return {"count": len(results), "next": nxt, "previous": None,
            "results": results}


def _entry(ip, dns_name="", status="active", tenant="Production", site="DC1",
           meta_zone="ZM"):
    """Entrée brute au format API NetBox (champs empaquetés par _map_record)."""
    return {
        "address": f"{ip}/24",
        "dns_name": dns_name,
        "status": {"value": status, "label": status.title()},
        "tenant": {"name": tenant} if tenant else None,
        "site": {"name": site} if site else None,
        "custom_fields": {"meta_zone": meta_zone},
    }


# ══════════════════════════════════════════════════════════════════
# Aides de rapprochement (RG01 → RG05, RG14) sur les deux mocks
# ══════════════════════════════════════════════════════════════════

def _normalize(name):
    """RG14 : minuscules, sans espaces, sans suffixe de domaine."""
    if not name:
        return ""
    return str(name).strip().lower().replace(" ", "").split(".")[0]


def _indexes():
    """Index IPAM : premier record par dns normalisé et par IP."""
    by_dns, by_ip = {}, {}
    for record in fetch_mock_ipam():
        by_dns.setdefault(_normalize(record["dns_name"]), record)
        by_ip.setdefault(record["ip"], record)
    return by_dns, by_ip


def _resolve(vm, by_dns, by_ip):
    """RG01 : priorité name → fqdn → ip, sinon NO_MATCH, avec l'IPAM associé."""
    name = _normalize(vm["vm_name"])
    if name in by_dns:
        return "MATCHED_NAME", by_dns[name]
    if vm["fqdn"] and _normalize(vm["fqdn"]) in by_dns:
        return "MATCHED_FQDN", by_dns[_normalize(vm["fqdn"])]
    if vm["ip_reported"] and vm["ip_reported"] in by_ip:
        return "MATCHED_IP", by_ip[vm["ip_reported"]]
    return "NO_MATCH", None


def _levels():
    """{vm_id: (niveau, record IPAM associé)} pour les 45 VM/CT du mock."""
    by_dns, by_ip = _indexes()
    return {vm["vm_id"]: _resolve(vm, by_dns, by_ip)
            for vm in fetch_mock_vms()}


def _level_of(vm_name):
    """Niveau atteint par une VM identifiée par son nom."""
    by_dns, by_ip = _indexes()
    vm = next(v for v in fetch_mock_vms() if v["vm_name"] == vm_name)
    return _resolve(vm, by_dns, by_ip)[0]


def _vm(vm_name):
    return next(v for v in fetch_mock_vms() if v["vm_name"] == vm_name)


# ══════════════════════════════════════════════════════════════════
# C1 — mock déterministe : 44 records, copies isolées
# ══════════════════════════════════════════════════════════════════

def test_fetch_mock_ipam_returns_exactly_44_records():
    assert len(fetch_mock_ipam()) == 44


def test_fetch_mock_ipam_is_identical_between_two_calls():
    assert fetch_mock_ipam() == fetch_mock_ipam()


def test_fetch_mock_ipam_returns_isolated_copies():
    first = fetch_mock_ipam()
    first[0]["dns_name"] = "tampered"
    first.append({"ip": "10.9.9.9", "dns_name": "injected", "status": "active",
                  "tenant": None, "site": None, "meta_zone": None})
    second = fetch_mock_ipam()
    assert len(second) == 44
    assert second[0]["dns_name"] == "web-a500"


def test_mock_records_expose_the_documented_keys():
    for record in fetch_mock_ipam():
        assert list(record) == EXPECTED_KEYS


# ══════════════════════════════════════════════════════════════════
# C2 — interface commune mock / réel
# ══════════════════════════════════════════════════════════════════

def test_ipam_sources_accept_a_call_without_required_argument():
    for source in (fetch_mock_ipam, fetch_ipam_records, collect_ipam):
        params = inspect.signature(source).parameters.values()
        required = [p.name for p in params
                    if p.default is inspect.Parameter.empty]
        assert required == []


def test_mock_and_real_sources_share_the_same_record_keys(netbox_env):
    transport = FakeTransport({_ENTRY_URL: _page([_entry("10.0.1.10",
                                                         "web-a500")])})
    real = fetch_ipam_records(transport=transport)
    mock = fetch_mock_ipam()
    assert list(real[0]) == EXPECTED_KEYS
    assert list(mock[0]) == EXPECTED_KEYS


def test_real_source_returns_the_mock_format_without_network(netbox_env):
    transport = FakeTransport({_ENTRY_URL: _page([_entry("10.0.1.10",
                                                         "web-a500")])})
    records = fetch_ipam_records(transport=transport)
    assert records == [{"ip": "10.0.1.10", "dns_name": "web-a500",
                        "status": "active", "tenant": "Production",
                        "site": "DC1", "meta_zone": "ZM"}]
    assert len(transport.calls) == 1  # transport injecté, aucun réseau


def test_real_source_tolerates_flat_status_and_absent_optional_fields(
        netbox_env):
    entry = {"address": "10.0.1.10", "dns_name": " web-a500 ",
             "status": "active"}
    transport = FakeTransport({_ENTRY_URL: _page([entry])})
    assert fetch_ipam_records(transport=transport) == [
        {"ip": "10.0.1.10", "dns_name": "web-a500", "status": "active",
         "tenant": None, "site": None, "meta_zone": None}
    ]


# ══════════════════════════════════════════════════════════════════
# C3 — sélection de la source par configuration
# ══════════════════════════════════════════════════════════════════

def test_use_mock_ipam_defaults_to_true(monkeypatch):
    monkeypatch.delenv("USE_MOCK_IPAM", raising=False)
    assert Config.use_mock_ipam() is True


def test_get_ipam_source_returns_the_mock_by_default(monkeypatch):
    monkeypatch.delenv("USE_MOCK_IPAM", raising=False)
    assert get_ipam_source() is fetch_mock_ipam


def test_use_mock_ipam_false_selects_the_netbox_source(monkeypatch):
    monkeypatch.setenv("USE_MOCK_IPAM", "false")
    assert Config.use_mock_ipam() is False
    assert get_ipam_source() is fetch_ipam_records


def test_collect_ipam_returns_the_mock_dataset_by_default(monkeypatch):
    monkeypatch.delenv("USE_MOCK_IPAM", raising=False)
    collected = collect_ipam()
    assert len(collected) == 44
    assert collected == fetch_mock_ipam()


def test_collect_ipam_real_source_fails_explicitly_without_token(monkeypatch):
    monkeypatch.setenv("USE_MOCK_IPAM", "false")
    monkeypatch.delenv("NETBOX_TOKEN", raising=False)
    with pytest.raises(NetBoxClientError) as excinfo:
        collect_ipam()
    assert "NETBOX_TOKEN" in str(excinfo.value)


# ══════════════════════════════════════════════════════════════════
# C4 — pagination et paramètres d'appel
# ══════════════════════════════════════════════════════════════════

def test_pagination_walks_every_page_until_next_is_null(netbox_env):
    page_one = _page([_entry("10.0.1.10", "web-a500"),
                      _entry("10.0.1.11", "web-b501")], nxt=_NEXT_URL)
    page_two = _page([_entry("10.0.1.12", "app-backend")])
    transport = FakeTransport({_ENTRY_URL: page_one, _NEXT_URL: page_two})

    records = fetch_ipam_records(transport=transport)

    assert [r["ip"] for r in records] == ["10.0.1.10", "10.0.1.11",
                                          "10.0.1.12"]
    assert [call[0] for call in transport.calls] == [_ENTRY_URL, _NEXT_URL]


def test_transport_receives_url_headers_timeout_and_ssl_flag(netbox_env,
                                                             monkeypatch):
    monkeypatch.setenv("NETBOX_VERIFY_SSL", "true")
    transport = FakeTransport({_ENTRY_URL: _page([])})

    fetch_ipam_records(transport=transport)

    url, headers, timeout, verify_ssl = transport.calls[0]
    assert url == _ENTRY_URL
    assert headers["Accept"] == "application/json"
    assert timeout == DEFAULT_TIMEOUT
    assert verify_ssl is True


# ══════════════════════════════════════════════════════════════════
# C5 / C6 / C7 — timeouts, erreurs explicites, secrets
# ══════════════════════════════════════════════════════════════════

def test_timeout_raises_an_explicit_error_with_configured_delay(netbox_env):
    with pytest.raises(NetBoxClientError) as excinfo:
        fetch_ipam_records(transport=RaisingTransport(TimeoutError()))
    message = str(excinfo.value)
    assert "délai dépassé" in message
    assert f"{DEFAULT_TIMEOUT} s" in message
    assert "NETBOX_URL" in message


def test_http_error_raises_an_explicit_error(netbox_env):
    error = urllib.error.HTTPError(_ENTRY_URL, 403, "Forbidden", None, None)
    with pytest.raises(NetBoxClientError) as excinfo:
        fetch_ipam_records(transport=RaisingTransport(error))
    message = str(excinfo.value)
    assert "HTTP 403" in message
    assert "NETBOX_URL" in message


def test_http_error_does_not_leak_pagination_url_with_secret(netbox_env):
    """Régression : l'URL de pagination peut contenir un secret (ex. token en query),
    le diagnostic doit citer NETBOX_URL et le code HTTP, pas l'URL brute."""
    pagination_url = f"{_ENTRY_URL}?limit=100&token=secret-value"
    error = urllib.error.HTTPError(pagination_url, 403, "Forbidden", None, None)
    with pytest.raises(NetBoxClientError) as excinfo:
        fetch_ipam_records(transport=RaisingTransport(error))
    message = str(excinfo.value)
    assert "HTTP 403" in message
    assert "NETBOX_URL" in message
    assert "secret-value" not in message
    assert "token=" not in message
    assert pagination_url not in message


def test_timeout_does_not_leak_pagination_url_with_secret(netbox_env):
    """Régression : timeout sur URL de pagination avec paramètre sensible —
    le diagnostic doit citer NETBOX_URL et le délai, pas l'URL brute."""
    pagination_url = f"{_ENTRY_URL}?limit=100&api_key=sensitive-key"
    # Premier appel : retourne une page avec un lien 'next' vers l'URL sensible
    # Deuxième appel (pagination) : lève TimeoutError
    def raising_transport(url, headers, timeout, verify_ssl):
        if url == _ENTRY_URL:
            return {"count": 1, "next": pagination_url, "results": [{"address": "10.0.0.1/24"}]}
        if url == pagination_url:
            raise TimeoutError()
        raise AssertionError(f"URL inattendue : {url}")

    with pytest.raises(NetBoxClientError) as excinfo:
        fetch_ipam_records(transport=raising_transport)
    message = str(excinfo.value)
    assert "délai dépassé" in message
    assert f"{DEFAULT_TIMEOUT} s" in message
    assert "NETBOX_URL" in message
    assert "sensitive-key" not in message
    assert "api_key=" not in message
    assert pagination_url not in message


def test_connection_error_raises_an_explicit_error(netbox_env):
    error = urllib.error.URLError("connexion refusée")
    with pytest.raises(NetBoxClientError) as excinfo:
        fetch_ipam_records(transport=RaisingTransport(error))
    assert "connexion impossible" in str(excinfo.value)


@pytest.mark.parametrize("error_type", [ssl.SSLError, ValueError],
                         ids=["ssl_error", "value_error"])
def test_transport_ssl_and_value_errors_raise_netbox_client_error(
    netbox_env, error_type
):
    transport = RaisingTransport(error_type("transport failure"))

    with pytest.raises(NetBoxClientError):
        fetch_ipam_records(transport=transport)

    assert transport.calls == 1


def test_invalid_json_response_raises_an_explicit_error(netbox_env):
    error = json.JSONDecodeError("Expecting value", "not json", 0)
    with pytest.raises(NetBoxClientError) as excinfo:
        fetch_ipam_records(transport=RaisingTransport(error))
    assert "réponse JSON invalide" in str(excinfo.value)


def test_payload_that_is_not_an_object_raises_an_explicit_error(netbox_env):
    transport = FakeTransport({_ENTRY_URL: ["pas", "un", "objet"]})
    with pytest.raises(NetBoxClientError) as excinfo:
        fetch_ipam_records(transport=transport)
    assert "objet JSON attendu" in str(excinfo.value)


def test_payload_without_results_key_raises_an_explicit_error(netbox_env):
    transport = FakeTransport({_ENTRY_URL: {"count": 0}})
    with pytest.raises(NetBoxClientError) as excinfo:
        fetch_ipam_records(transport=transport)
    assert "results" in str(excinfo.value)


def test_results_of_wrong_type_raises_an_explicit_error(netbox_env):
    transport = FakeTransport({_ENTRY_URL: {"results": 42}})
    with pytest.raises(NetBoxClientError) as excinfo:
        fetch_ipam_records(transport=transport)
    assert "results" in str(excinfo.value)


def test_invalid_record_entry_raises_an_explicit_error(netbox_env):
    transport = FakeTransport({_ENTRY_URL: {"results": ["pas-un-record"]}})
    with pytest.raises(NetBoxClientError) as excinfo:
        fetch_ipam_records(transport=transport)
    assert "entrée de record invalide" in str(excinfo.value)


def test_missing_token_raises_an_explicit_error(netbox_env, monkeypatch):
    monkeypatch.setenv("NETBOX_TOKEN", "   ")
    with pytest.raises(NetBoxClientError) as excinfo:
        fetch_ipam_records(transport=FakeTransport({_ENTRY_URL: _page([])}))
    assert "NETBOX_TOKEN" in str(excinfo.value)


def test_error_messages_never_expose_the_token(netbox_env):
    failures = [
        urllib.error.HTTPError(_ENTRY_URL, 500, "Boom", None, None),
        urllib.error.URLError("refusé"),
        TimeoutError(),
        json.JSONDecodeError("Expecting value", "not json", 0),
    ]
    for failure in failures:
        with pytest.raises(NetBoxClientError) as excinfo:
            fetch_ipam_records(transport=RaisingTransport(failure))
        assert _TOKEN not in str(excinfo.value)


def test_authorization_header_uses_token_prefix_by_default(netbox_env):
    transport = FakeTransport({_ENTRY_URL: _page([])})
    fetch_ipam_records(transport=transport)
    assert transport.calls[0][1]["Authorization"] == f"Token {_TOKEN}"


def test_authorization_header_switches_to_bearer_for_nbt_tokens(netbox_env,
                                                                monkeypatch):
    monkeypatch.setenv("NETBOX_TOKEN", "nbt_unit-test-token")
    transport = FakeTransport({_ENTRY_URL: _page([])})
    fetch_ipam_records(transport=transport)
    assert (transport.calls[0][1]["Authorization"]
            == "Bearer nbt_unit-test-token")


def test_fetch_without_transport_is_blocked_by_the_network_guard(netbox_env):
    with pytest.raises(AssertionError, match="appel réseau interdit"):
        fetch_ipam_records()


# ══════════════════════════════════════════════════════════════════
# C8 — couverture des 6 anomalies (cahier des charges §304)
# ══════════════════════════════════════════════════════════════════

def test_mock_dataset_covers_duplicate_dns_anomaly():
    counts = Counter(_normalize(r["dns_name"]) for r in fetch_mock_ipam())
    duplicated = {name for name, n in counts.items() if n > 1}
    assert duplicated == {"monitoring"}
    ips = {r["ip"] for r in fetch_mock_ipam()
           if _normalize(r["dns_name"]) == "monitoring"}
    assert ips == {"10.0.4.10", "10.0.8.50"}


def test_mock_dataset_covers_duplicate_ip_anomaly():
    counts = Counter(r["ip"] for r in fetch_mock_ipam())
    duplicated = {ip for ip, n in counts.items() if n > 1}
    assert duplicated == {"10.0.4.16"}
    names = {r["dns_name"] for r in fetch_mock_ipam() if r["ip"] == "10.0.4.16"}
    assert names == {"gitea-repo", "gitea-mirror"}


def test_mock_dataset_covers_no_match_anomaly():
    """RG08 : VMs sans aucune correspondance (hostname, FQDN et IP absents)."""
    by_dns, by_ip = _indexes()
    unmatched = set()
    for vm in fetch_mock_vms():
        level, record = _resolve(vm, by_dns, by_ip)
        if level == "NO_MATCH":
            unmatched.add(vm["vm_name"])
    assert {"temp-migration", "old-legacy", "decom-windows"} <= unmatched
    ghost = _vm("temp-migration")
    assert _normalize(ghost["vm_name"]) not in by_dns
    assert ghost["ip_reported"] not in by_ip


def test_mock_dataset_covers_matched_ip_anomaly():
    """RG09 : une IP existe dans NetBox alors que le hostname diffère."""
    by_dns, by_ip = _indexes()
    ghost = _vm("ghost-vm")
    level, record = _resolve(ghost, by_dns, by_ip)
    assert level == "MATCHED_IP"
    assert record["ip"] == "10.0.9.98"
    assert _normalize(ghost["vm_name"]) not in by_dns


def test_mock_dataset_covers_hostname_mismatch_anomaly():
    """RG10 : VM matchée par IP dont le hostname diffère du DNS NetBox."""
    by_dns, by_ip = _indexes()
    ghost = _vm("ghost-vm")
    _, record = _resolve(ghost, by_dns, by_ip)
    assert record["dns_name"] == "old-printer"
    assert _normalize(ghost["vm_name"]) != _normalize(record["dns_name"])


def test_mock_dataset_covers_status_mismatch_anomaly():
    """RG11 : VM stopped dont le record IPAM apparié est actif."""
    by_dns, by_ip = _indexes()
    triggered = set()
    for vm in fetch_mock_vms():
        if vm["status"] != "stopped":
            continue
        _, record = _resolve(vm, by_dns, by_ip)
        if record is not None and record["status"] == "active":
            triggered.add(vm["vm_name"])
    assert {"backup-srv", "backup-offsite"} <= triggered


# ══════════════════════════════════════════════════════════════════
# C9 — couverture des 4 niveaux de match (regles.md:5, RG01 → RG05)
# ══════════════════════════════════════════════════════════════════

def test_mock_dataset_covers_the_four_match_levels():
    levels = {level for level, _ in _levels().values()}
    assert levels == {"MATCHED_NAME", "MATCHED_FQDN", "MATCHED_IP",
                      "NO_MATCH"}
    assert len(_levels()) == 45


def test_hostname_level_is_reached_by_web_a500():
    assert _level_of("web-a500") == "MATCHED_NAME"


def test_fqdn_level_is_reached_only_by_srv_renamed_x999():
    vm = _vm("srv-renamed-x999")
    by_dns, _ = _indexes()
    assert _level_of("srv-renamed-x999") == "MATCHED_FQDN"
    assert _normalize(vm["vm_name"]) not in by_dns
    assert _normalize(vm["fqdn"]) in by_dns


def test_ip_level_is_reached_only_by_ghost_vm():
    vm = _vm("ghost-vm")
    by_dns, _ = _indexes()
    assert _level_of("ghost-vm") == "MATCHED_IP"
    assert _normalize(vm["vm_name"]) not in by_dns
    assert vm["fqdn"] is None


def test_no_match_level_is_reached_by_the_three_orphan_vms():
    for name in ("temp-migration", "old-legacy", "decom-windows"):
        assert _level_of(name) == "NO_MATCH"
