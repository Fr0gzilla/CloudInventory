"""T022 — Tests unitaires du module collector/anomalies.py (RG05→RG13).

Critères : six anomalies testées sur les jeux de mock, cas nominaux et négatifs,
absence de mutation des entrées, attendus §13.3 (3 NO_MATCH, 1 MATCHED_IP+HOSTNAME_MISMATCH,
2 STATUS_MISMATCH, 1 DUPLICATE_DNS, 1 DUPLICATE_IP).
"""

import copy

import collector.anomalies as anom
from collector.mock_netbox import fetch_mock_ipam
from collector.mock_virtualisation import MOCK_VMS


def test_detect_no_match_nominal():
    """RG05/RG08 : NO_MATCH quand aucune stratégie ne matche."""
    vm_name = "totally-unknown"
    vm_fqdn = "unknown.fqdn.local"
    vm_ip_reported = "192.0.2.99"
    dns_index = {"web-a500": {"ip": "10.0.1.10", "dns_name": "web-a500"}}
    ip_index = {"10.0.1.10": {"ip": "10.0.1.10", "dns_name": "web-a500"}}
    assert anom.detect_no_match(vm_name, vm_fqdn, vm_ip_reported, dns_index, ip_index) is True


def test_detect_no_match_with_hostname_match():
    """RG05 : pas de NO_MATCH si le hostname matche."""
    vm_name = "web-a500"
    vm_fqdn = "unknown.fqdn.local"
    vm_ip_reported = "192.0.2.99"
    dns_index = {"web-a500": {"ip": "10.0.1.10", "dns_name": "web-a500"}}
    ip_index = {"10.0.1.10": {"ip": "10.0.1.10", "dns_name": "web-a500"}}
    assert anom.detect_no_match(vm_name, vm_fqdn, vm_ip_reported, dns_index, ip_index) is False


def test_detect_no_match_with_fqdn_match():
    """RG05 : pas de NO_MATCH si le FQDN matche."""
    vm_name = "unknown-host"
    vm_fqdn = "web-a500.prod.local"
    vm_ip_reported = "192.0.2.99"
    dns_index = {"web-a500": {"ip": "10.0.1.10", "dns_name": "web-a500"}}
    ip_index = {"10.0.1.10": {"ip": "10.0.1.10", "dns_name": "web-a500"}}
    assert anom.detect_no_match(vm_name, vm_fqdn, vm_ip_reported, dns_index, ip_index) is False


def test_detect_no_match_with_ip_match():
    """RG05 : pas de NO_MATCH si l'IP matche."""
    vm_name = "unknown-host"
    vm_fqdn = "unknown.fqdn.local"
    vm_ip_reported = "10.0.1.10"
    dns_index = {"web-a500": {"ip": "10.0.1.10", "dns_name": "web-a500"}}
    ip_index = {"10.0.1.10": {"ip": "10.0.1.10", "dns_name": "web-a500"}}
    assert anom.detect_no_match(vm_name, vm_fqdn, vm_ip_reported, dns_index, ip_index) is False


def test_detect_no_match_none_fqdn():
    """RG05 : FQDN None ne fait pas planter."""
    vm_name = "unknown-host"
    vm_fqdn = None
    vm_ip_reported = "10.9.9.9"
    dns_index = {"web-a500": {"ip": "10.0.1.10", "dns_name": "web-a500"}}
    ip_index = {"10.9.9.9": {"ip": "10.9.9.9", "dns_name": "other"}}
    result = anom.detect_no_match(vm_name, vm_fqdn, vm_ip_reported, dns_index, ip_index)
    assert result in (True, False)


def test_detect_no_match_none_ip():
    """RG05 : IP reportée None ne fait pas planter."""
    vm_name = "unknown-host"
    vm_fqdn = "unknown.fqdn.local"
    vm_ip_reported = None
    dns_index = {"web-a500": {"ip": "10.0.1.10", "dns_name": "web-a500"}}
    ip_index = {"10.0.1.10": {"ip": "10.0.1.10", "dns_name": "web-a500"}}
    assert anom.detect_no_match(vm_name, vm_fqdn, vm_ip_reported, dns_index, ip_index) is True


def test_detect_hostname_mismatch_nominal_match():
    """RG06 : pas de HOSTNAME_MISMATCH quand hostname et DNS correspondent."""
    vm_name = "web-a500"
    vm_ip_reported = "10.0.1.10"
    ipam_record = {"ip": "10.0.1.10", "dns_name": "web-a500"}
    assert anom.detect_hostname_mismatch(vm_name, vm_ip_reported, ipam_record) is False


def test_detect_hostname_mismatch_ip_mismatch():
    """RG06 : pas de HOSTNAME_MISMATCH quand l'IP ne matche pas."""
    vm_name = "web-a500"
    vm_ip_reported = "1.2.3.4"
    ipam_record = {"ip": "10.0.1.10", "dns_name": "web-a500"}
    assert anom.detect_hostname_mismatch(vm_name, vm_ip_reported, ipam_record) is False


def test_detect_hostname_mismatch_mismatch():
    """RG10 : HOSTNAME_MISMATCH quand l'IP matche mais hostname diffère."""
    vm_name = "ghost-vm"
    vm_ip_reported = "10.0.9.98"
    ipam_record = {"ip": "10.0.9.98", "dns_name": "old-printer"}
    assert anom.detect_hostname_mismatch(vm_name, vm_ip_reported, ipam_record) is True


def test_detect_hostname_mismatch_no_ipam():
    """RG06 : pas de HOSTNAME_MISMATCH sans enregistrement IPAM."""
    vm_name = "unknown-host"
    vm_ip_reported = "10.0.1.10"
    ipam_record = None
    assert anom.detect_hostname_mismatch(vm_name, vm_ip_reported, ipam_record) is False


def test_detect_status_mismatch_stopped_active():
    """RG11 : STATUS_MISMATCH quand VM arrêtée + IP active."""
    vm_status = "stopped"
    ipam_status = "active"
    assert anom.detect_status_mismatch(vm_status, ipam_status) is True


def test_detect_status_mismatch_running_active():
    """RG11 : pas de STATUS_MISMATCH VM running + IP active."""
    vm_status = "running"
    ipam_status = "active"
    assert anom.detect_status_mismatch(vm_status, ipam_status) is False


def test_detect_status_mismatch_stopped_reserved():
    """RG11 : pas de STATUS_MISMATCH VM arrêtée + IP réservée."""
    vm_status = "stopped"
    ipam_status = "reserved"
    assert anom.detect_status_mismatch(vm_status, ipam_status) is False


def test_detect_duplicate_dns_nominal():
    """RG12 : DUPLICATE_DNS quand un même DNS apparaît plusieurs fois."""
    ipam_records = [
        {"dns_name": "monitoring", "ip": "10.0.4.10"},
        {"dns_name": "other", "ip": "10.0.4.11"},
        {"dns_name": "monitoring", "ip": "10.0.4.12"},
    ]
    assert anom.detect_duplicate_dns(ipam_records) is True


def test_detect_duplicate_dns_no_dup():
    """RG12 : pas de DUPLICATE_DNS quand les DNS sont uniques."""
    ipam_records = [
        {"dns_name": "web-a500", "ip": "10.0.1.10"},
        {"dns_name": "web-b501", "ip": "10.0.1.11"},
    ]
    assert anom.detect_duplicate_dns(ipam_records) is False


def test_detect_duplicate_dns_no_dns_records():
    """RG12 : pas de DUPLICATE_DNS sans champ dns_name."""
    ipam_records = [
        {"ip": "10.0.1.10"},
        {"ip": "10.0.1.11"},
    ]
    assert anom.detect_duplicate_dns(ipam_records) is False


def test_detect_duplicate_ip_nominal():
    """RG13 : DUPLICATE_IP quand une même IP apparaît plusieurs fois."""
    ipam_records = [
        {"ip": "10.0.4.16", "dns_name": "gitea-mirror"},
        {"ip": "10.0.5.10", "dns_name": "dev-frontend"},
        {"ip": "10.0.4.16", "dns_name": "gitea-mirror-copy"},
    ]
    assert anom.detect_duplicate_ip(ipam_records) is True


def test_detect_duplicate_ip_no_dup():
    """RG13 : pas de DUPLICATE_IP quand les IPs sont uniques."""
    ipam_records = [
        {"ip": "10.0.1.10", "dns_name": "web-a500"},
        {"ip": "10.0.1.11", "dns_name": "web-b501"},
    ]
    assert anom.detect_duplicate_ip(ipam_records) is False


def test_detect_duplicate_ip_no_ip_records():
    """RG13 : pas de DUPLICATE_IP sans champ ip."""
    ipam_records = [
        {"dns_name": "web-a500"},
        {"dns_name": "web-b501"},
    ]
    assert anom.detect_duplicate_ip(ipam_records) is False


def test_detect_duplicate_ip_no_mutation():
    """RG14 : detect_duplicate_ip ne doit pas modifier les entrées."""
    ipam_records_orig = [
        {"ip": "10.0.4.16", "dns_name": "gitea-mirror"},
        {"ip": "10.0.5.10", "dns_name": "dev-frontend"},
    ]
    ipam_copy = copy.deepcopy(ipam_records_orig)
    anom.detect_duplicate_ip(ipam_records_orig)
    assert ipam_records_orig == ipam_copy


def test_detect_matched_ip_nominal():
    """RG04/RG09 : MATCHED_IP quand l'IP rapportée matche un enregistrement."""
    vm_ip_reported = "10.0.1.10"
    ipam_records = [
        {"ip": "10.0.1.10", "dns_name": "web-a500"},
        {"ip": "10.0.1.11", "dns_name": "web-b501"},
    ]
    assert anom.detect_matched_ip(vm_ip_reported, ipam_records) is True


def test_detect_matched_ip_no_match():
    """RG04/RG09 : pas de MATCHED_IP quand l'IP ne matche pas."""
    vm_ip_reported = "192.0.2.99"
    ipam_records = [
        {"ip": "10.0.1.10", "dns_name": "web-a500"},
        {"ip": "10.0.1.11", "dns_name": "web-b501"},
    ]
    assert anom.detect_matched_ip(vm_ip_reported, ipam_records) is False


def test_detect_matched_ip_none_ip():
    """RG04/RG09 : pas de MATCHED_IP avec IP None."""
    vm_ip_reported = None
    ipam_records = [
        {"ip": "10.0.1.10", "dns_name": "web-a500"},
    ]
    assert anom.detect_matched_ip(vm_ip_reported, ipam_records) is False


def test_detect_matched_ip_no_mutation():
    """RG14 : detect_matched_ip ne doit pas modifier les entrées."""
    vm_ip_reported = "10.0.1.10"
    ipam_records_orig = [
        {"ip": "10.0.1.10", "dns_name": "web-a500"},
        {"ip": "10.0.1.11", "dns_name": "web-b501"},
    ]
    ipam_copy = copy.deepcopy(ipam_records_orig)
    anom.detect_matched_ip(vm_ip_reported, ipam_records_orig)
    assert ipam_records_orig == ipam_copy


def test_detect_no_match_with_mock_data():
    """RG05/RG08 : NO_MATCH avec les VMs mock et IPAM mock.
    
    VM 997 (old-legacy): pas de IP, pas de FQDN, hostname "old-legacy" sans correspondance.
    VM 995 (decom-windows): pas de IP, FQDN "decom-windows.legacy.local" sans correspondance.
    VM 998: hostname sans correspondance dans IPAM.
    """
    ipam_records = fetch_mock_ipam()
    
    # VM 997 : old-legacy, ip_reported=None, fqdn=None → NO_MATCH
    vm = next(v for v in MOCK_VMS if v["vm_id"] == "997")
    vm_name = vm["vm_name"]
    vm_fqdn = vm.get("fqdn")
    vm_ip_reported = vm.get("ip_reported")
    dns_index = {}
    ip_index = {}
    for rec in ipam_records:
        h = rec.get("dns_name", "").lower()
        if h and h not in dns_index:
            dns_index[h] = rec
        ip = rec.get("ip", "")
        if ip and ip not in ip_index:
            ip_index[ip] = rec
    result = anom.detect_no_match(vm_name, vm_fqdn, vm_ip_reported, dns_index, ip_index)
    assert result is True, f"VM 997 should be NO_MATCH, got {result}"

def test_find_duplicate_dns_groupes_normalises():
    """RG12 + RG14 : noms comparés normalisés (casse, espaces, domaine), comme dans l'index du matching."""
    ipam_records = [
        {"dns_name": "Web-A500.prod.local", "ip": "10.0.1.10"},
        {"dns_name": "web-a500 ", "ip": "10.0.1.20"},
        {"dns_name": "db-b500", "ip": "10.0.2.10"},
    ]
    groups = anom.find_duplicate_dns(ipam_records)
    assert list(groups) == ["web-a500"]
    assert [rec["ip"] for rec in groups["web-a500"]] == ["10.0.1.10", "10.0.1.20"]
    assert anom.detect_duplicate_dns(ipam_records) is True


def test_find_duplicate_ip_groupes():
    """RG13 : les enregistrements d'une même IP sont rendus ensemble, dans l'ordre de la liste."""
    ipam_records = [
        {"ip": "10.0.4.16", "dns_name": "gitea-repo"},
        {"ip": "10.0.5.10", "dns_name": "dev-frontend"},
        {"ip": "10.0.4.16", "dns_name": "gitea-mirror"},
    ]
    groups = anom.find_duplicate_ip(ipam_records)
    assert {ip: [rec["dns_name"] for rec in group] for ip, group in groups.items()} == {
        "10.0.4.16": ["gitea-repo", "gitea-mirror"],
    }


def test_find_duplicates_sur_les_mocks():
    """§ 13.2 : un doublon DNS (monitoring) et un doublon IP (10.0.4.16) dans le mock NetBox."""
    ipam_records = fetch_mock_ipam()
    assert list(anom.find_duplicate_dns(ipam_records)) == ["monitoring"]
    assert list(anom.find_duplicate_ip(ipam_records)) == ["10.0.4.16"]
