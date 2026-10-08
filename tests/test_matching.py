"""T021 — Tests unitaires du module collector/matching.py (RG01→RG05).

Critères : quatre niveaux de match testés sur les jeux de mocks collector,
priorités nommage/FQDN/IP, absence de match, ambiguïtés, absence de mutation.
"""

import collector.matching as match
import collector.normalisation as norm


def test_matched_name_priority():
    """RG02 : MATCHED_NAME a priorité sur les autres stratégies."""
    vm_name = "web-a500"
    vm_fqdn = "other-fqdn.local"
    vm_ip_reported = "10.9.9.9"
    dns_index = {"web-a500": {"ip": "10.0.1.10", "dns_name": "web-a500"}}
    ip_index = {"10.9.9.9": {"ip": "10.9.9.9", "dns_name": "other"}}
    status, _ = match.resolve_match(vm_name, vm_fqdn, vm_ip_reported, dns_index, ip_index)
    assert status == "MATCHED_NAME"


def test_matched_fqdn_when_name_fails():
    """RG03 : MATCHED_FQDN quand le hostname normalisé ne matche pas."""
    vm_name = "unknown-host"
    vm_fqdn = "web-a500.prod.local"
    vm_ip_reported = "10.9.9.9"
    dns_index = {"web-a500": {"ip": "10.0.1.10", "dns_name": "web-a500"}}
    ip_index = {"10.9.9.9": {"ip": "10.9.9.9", "dns_name": "other"}}
    status, _ = match.resolve_match(vm_name, vm_fqdn, vm_ip_reported, dns_index, ip_index)
    assert status == "MATCHED_FQDN"


def test_matched_ip_when_name_and_fqdn_fail():
    """RG04 : MATCHED_IP quand le hostname et le FQDN ne matchent pas."""
    vm_name = "unknown-host"
    vm_fqdn = "unknown.fqdn.local"
    vm_ip_reported = "10.0.1.10"
    dns_index = {"web-a500": {"ip": "10.0.1.10", "dns_name": "web-a500"}}
    ip_index = {"10.0.1.10": {"ip": "10.0.1.10", "dns_name": "web-a500"}}
    status, _ = match.resolve_match(vm_name, vm_fqdn, vm_ip_reported, dns_index, ip_index)
    assert status == "MATCHED_IP"


def test_no_match_when_nothing_founds():
    """RG05 : NO_MATCH quand aucune stratégie ne matche."""
    vm_name = "totally-unknown"
    vm_fqdn = "unknown.fqdn.local"
    vm_ip_reported = "192.0.2.99"  # IP qui n'est pas dans l'index
    dns_index = {"web-a500": {"ip": "10.0.1.10", "dns_name": "web-a500"}}
    ip_index = {"10.0.1.10": {"ip": "10.0.1.10", "dns_name": "web-a500"}}
    status, _ = match.resolve_match(vm_name, vm_fqdn, vm_ip_reported, dns_index, ip_index)
    assert status == "NO_MATCH"


def test_no_match_with_none_fqdn():
    """RG05 : FQDN None ne doit pas faire planter, tomber sur IP ou NO_MATCH."""
    vm_name = "unknown-host"
    vm_fqdn = None
    vm_ip_reported = "10.9.9.9"
    dns_index = {"web-a500": {"ip": "10.0.1.10", "dns_name": "web-a500"}}
    ip_index = {"10.9.9.9": {"ip": "10.9.9.9", "dns_name": "other"}}
    status, _ = match.resolve_match(vm_name, vm_fqdn, vm_ip_reported, dns_index, ip_index)
    assert status in ("MATCHED_IP", "NO_MATCH")


def test_no_match_with_none_ip():
    """RG05 : IP reportée None ne doit pas faire planter."""
    vm_name = "unknown-host"
    vm_fqdn = "unknown.fqdn.local"
    vm_ip_reported = None
    dns_index = {"web-a500": {"ip": "10.0.1.10", "dns_name": "web-a500"}}
    ip_index = {"10.0.1.10": {"ip": "10.0.1.10", "dns_name": "web-a500"}}
    status, _ = match.resolve_match(vm_name, vm_fqdn, vm_ip_reported, dns_index, ip_index)
    assert status == "NO_MATCH"


def test_compute_levels_with_mock_data():
    """RG01→RG05 : compute_levels sur l'ensemble des VMs mocks."""
    from collector.mock_virtualisation import MOCK_VMS
    from collector.mock_netbox import fetch_mock_ipam
    vm_list = MOCK_VMS
    ipam_records = fetch_mock_ipam()
    result = match.compute_levels(vm_list, ipam_records)
    # Tous les tests doivent avoir un status connu
    valid_statuses = {"MATCHED_NAME", "MATCHED_FQDN", "MATCHED_IP", "NO_MATCH"}
    for vm_id, (status, record) in result.items():
        assert status in valid_statuses, f"Status inattendu pour {vm_id}: {status}"
    # Vérifie que le compteur sommaire fait sens
    total = len(result)
    matched = sum(1 for s, _ in result.values() if s.startswith("MATCHED"))
    no_match = sum(1 for s, _ in result.values() if s == "NO_MATCH")
    assert matched + no_match == total


def test_compute_levels_no_mutation_inputs():
    """RG14 : compute_levels ne doit pas modifier les entrées."""
    vm_list_orig = [
        {"vm_name": "web-a500", "fqdn": "web-a500.prod.local", "ip_reported": "10.0.1.10"},
    ]
    ipam_records_orig = [{"ip": "10.0.1.10", "dns_name": "web-a500"}]
    import copy
    vm_list_copy = copy.deepcopy(vm_list_orig)
    ipam_records_copy = copy.deepcopy(ipam_records_orig)
    match.compute_levels(vm_list_orig, ipam_records_orig)
    assert vm_list_orig == vm_list_copy, "vm_list a été modifié"
    assert ipam_records_orig == ipam_records_copy, "ipam_records a été modifié"


def test_build_dns_index():
    """RG02 : build_dns_index construit correctement l'index."""
    records = [
        {"dns_name": "web-a500", "ip": "10.0.1.10"},
        {"dns_name": "web-b501", "ip": "10.0.1.11"},
    ]
    index = match.build_dns_index(records)
    assert "web-a500" in index
    assert "web-b501" in index
    assert index["web-a500"]["ip"] == "10.0.1.10"


def test_build_ip_index():
    """RG04 : build_ip_index construit correctement l'index."""
    records = [
        {"ip": "10.0.1.10", "dns_name": "web-a500"},
        {"ip": "10.0.1.11", "dns_name": "web-b501"},
    ]
    index = match.build_ip_index(records)
    assert "10.0.1.10" in index
    assert "10.0.1.11" in index
    assert index["10.0.1.10"]["dns_name"] == "web-a500"


def test_priority_order_name_over_fqdn_over_ip():
    """RG01 : la priorité est hostname → FQDN → IP → NO_MATCH."""
    # Un VM dont le hostname matche par FQDN mais pas par name,
    # et dont l'IP matche aussi : doit choisir MATCHED_NAME si hostname matche
    # Mais testons le cas où seulement FQDN et IP matchent :
    vm_name = "totally-different"
    vm_fqdn = "web-a500.prod.local"
    vm_ip_reported = "10.0.1.10"
    dns_index = {"web-a500": {"ip": "10.0.1.10", "dns_name": "web-a500"}}
    ip_index = {"10.0.1.10": {"ip": "10.0.1.10", "dns_name": "web-a500"}}
    status, _ = match.resolve_match(vm_name, vm_fqdn, vm_ip_reported, dns_index, ip_index)
    # MATCHED_NAME ne matche pas (hostname différent), donc MATCHED_FQDN
    assert status == "MATCHED_FQDN"


def test_monitoring_dns_tiebreak_by_ip():
    """T032 : monitoring VM ip_reported=10.0.4.10 doit préférer l'enregistrement dont l'IP matche."""
    vm_name = "monitoring"
    vm_fqdn = "monitoring.supervision.local"
    vm_ip_reported = "10.0.4.10"
    ipam_records = [
        {"ip": "10.0.4.10", "dns_name": "monitoring", "status": "active", "tenant": "Supervision", "site": "DC1", "meta_zone": "ZCS"},
        {"ip": "10.0.8.50", "dns_name": "monitoring", "status": "active", "tenant": "Supervision", "site": "DC2", "meta_zone": "ZCS"},
    ]
    dns_index = match.build_dns_index(ipam_records)
    ip_index = match.build_ip_index(ipam_records)
    status, raw = match.resolve_match(
        vm_name, vm_fqdn, vm_ip_reported, dns_index, ip_index, ipam_records,
    )
    assert status == "MATCHED_NAME", f"Expected MATCHED_NAME, got {status}"
    assert raw.get("ip") == "10.0.4.10", f"Expected ip 10.0.4.10, got {raw.get('ip')}"
    assert raw.get("dns_name") == "monitoring"
    assert raw.get("site") == "DC1", f"Expected site DC1, got {raw.get('site')}"


def test_monitoring_dns_fallback_no_ip_match():
    """T032 : doublon DNS sans IP correspondante garde le comportement d'avant (dernier)."""
    vm_name = "monitoring"
    vm_fqdn = "monitoring.supervision.local"
    vm_ip_reported = "10.0.0.99"  # IP qui n'appartient à aucun enregistrement en doublon
    ipam_records = [
        {"ip": "10.0.4.10", "dns_name": "monitoring", "status": "active", "tenant": "Supervision", "site": "DC1", "meta_zone": "ZCS"},
        {"ip": "10.0.8.50", "dns_name": "monitoring", "status": "active", "tenant": "Supervision", "site": "DC2", "meta_zone": "ZCS"},
    ]
    dns_index = match.build_dns_index(ipam_records)
    ip_index = match.build_ip_index(ipam_records)
    status, raw = match.resolve_match(
        vm_name, vm_fqdn, vm_ip_reported, dns_index, ip_index, ipam_records,
    )
    # Sans IP correspondante parmi les doublons, on garde le dernier enregistrement (comportement d'avant)
    assert status == "MATCHED_NAME", f"Expected MATCHED_NAME, got {status}"
    assert raw.get("ip") == "10.0.8.50", f"Expected last record ip 10.0.8.50, got {raw.get('ip')}"
    assert raw.get("dns_name") == "monitoring"


def test_normalize_used_by_match():
    """Vérifier que resolve_match utilise bien normalize_hostname/normalize_fqdn."""
    import inspect
    source = inspect.getsource(match.resolve_match)
    assert "normalize_hostname" in source
    assert "normalize_fqdn" in source