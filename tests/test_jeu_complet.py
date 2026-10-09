"""T022 — § 13.3 : les six anomalies vérifiées directement sur le jeu de mocks,
puis les mêmes comptages rendus par l'orchestrateur du run.

Couverture directe : chaque détecteur de ``collector/anomalies.py`` est appelé sur
l'intégralité de ``collector/mock_virtualisation`` et ``collector/mock_netbox``,
sans passer par la base. Puis ``run_inventory`` joue le jeu complet et rend les
comptages du cahier des charges § 13.3 : 3 NO_MATCH, 1 HOSTNAME_MISMATCH,
2 STATUS_MISMATCH, 1 DUPLICATE_DNS, 1 DUPLICATE_IP.
"""
import json
from collections import Counter

from app.models import Anomaly, ConsolidatedAsset
from collector import anomalies as anom
from collector import matching as match
from collector.inventory_runner import run_inventory
from collector.mock_netbox import fetch_mock_ipam
from collector.mock_virtualisation import fetch_mock_vms

# Comptages attendus par run (cahier des charges § 13.3).
COMPTAGES_13_3 = {
    "NO_MATCH": 3,
    "HOSTNAME_MISMATCH": 1,
    "STATUS_MISMATCH": 2,
    "DUPLICATE_DNS": 1,
    "DUPLICATE_IP": 1,
}


def _jeu():
    """Le jeu complet (VMs + IPAM) et les deux index de matching."""
    vms = fetch_mock_vms()
    ipam = fetch_mock_ipam()
    return vms, ipam, match.build_dns_index(ipam), match.build_ip_index(ipam)


def _resolu(vm, dns_index, ip_index, ipam):
    """Le niveau de matching d'une VM du jeu, comme le fait l'orchestrateur."""
    return match.resolve_match(
        vm["vm_name"], vm.get("fqdn"), vm.get("ip_reported"), dns_index, ip_index, ipam,
    )


# ---------------------------------------------------------------------------
# Couverture directe des détecteurs sur le jeu complet
# ---------------------------------------------------------------------------


def test_no_match_direct_compte_trois_vms():
    """§ 13.3 : detect_no_match sur le jeu complet désigne exactement les 3 VMs orphelines."""
    vms, _ipam, dns_index, ip_index = _jeu()
    names = {
        vm["vm_name"] for vm in vms
        if anom.detect_no_match(
            vm["vm_name"], vm.get("fqdn"), vm.get("ip_reported"), dns_index, ip_index,
        )
    }
    assert names == {"temp-migration", "old-legacy", "decom-windows"}


def test_hostname_mismatch_direct_concerne_ghost_vm():
    """§ 13.3 : parmi les VMs rapprochées par IP, seule ghost-vm diffère du DNS NetBox."""
    vms, ipam, dns_index, ip_index = _jeu()
    names = set()
    for vm in vms:
        status, record = _resolu(vm, dns_index, ip_index, ipam)
        if status == "MATCHED_IP" and anom.detect_hostname_mismatch(
            vm["vm_name"], vm.get("ip_reported"), record,
        ):
            names.add(vm["vm_name"])
    assert names == {"ghost-vm"}


def test_status_mismatch_direct_concerne_les_deux_sauvegardes():
    """§ 13.3 : VM arrêtée sur IP active → backup-srv et backup-offsite seulement."""
    vms, ipam, dns_index, ip_index = _jeu()
    names = set()
    for vm in vms:
        _status, record = _resolu(vm, dns_index, ip_index, ipam)
        if record and anom.detect_status_mismatch(vm.get("status"), record.get("status")):
            names.add(vm["vm_name"])
    assert names == {"backup-srv", "backup-offsite"}


def test_duplicate_dns_direct_un_seul_groupe():
    """§ 13.3 : detect_duplicate_dns sur l'IPAM complet ne signale que monitoring."""
    _vms, ipam, _dns_index, _ip_index = _jeu()
    assert anom.detect_duplicate_dns(ipam) is True
    assert len(anom.find_duplicate_dns(ipam)) == COMPTAGES_13_3["DUPLICATE_DNS"]


def test_duplicate_ip_direct_un_seul_groupe():
    """§ 13.3 : detect_duplicate_ip sur l'IPAM complet ne signale que 10.0.4.16."""
    _vms, ipam, _dns_index, _ip_index = _jeu()
    assert anom.detect_duplicate_ip(ipam) is True
    assert len(anom.find_duplicate_ip(ipam)) == COMPTAGES_13_3["DUPLICATE_IP"]


def test_matched_ip_direct_ghost_vm_vs_sans_correspondance():
    """§ 13.3 : l'IP de ghost-vm existe dans l'IPAM, celle d'une VM orpheline non."""
    _vms, ipam, _dns_index, _ip_index = _jeu()
    ghost = next(vm for vm in fetch_mock_vms() if vm["vm_name"] == "ghost-vm")
    orphan = next(vm for vm in fetch_mock_vms() if vm["vm_name"] == "temp-migration")
    assert anom.detect_matched_ip(ghost.get("ip_reported"), ipam) is True
    assert anom.detect_matched_ip(orphan.get("ip_reported"), ipam) is False


# ---------------------------------------------------------------------------
# Orchestrateur : le run complet rend les mêmes comptages
# ---------------------------------------------------------------------------


def test_comptages_13_3_sur_le_run_complet(db):
    """§ 13.3 : le run sur le jeu complet rend les 8 anomalies, aux comptages exacts du cahier."""
    vms = [dict(vm) for vm in fetch_mock_vms()]
    ipam = [dict(rec) for rec in fetch_mock_ipam()]
    run = run_inventory(
        collect_vms=lambda: vms, collect_ipam=lambda: ipam,
    )

    counts = Counter(anomaly.code for anomaly in Anomaly.query.filter_by(run_id=run.id))
    assert run.status == "SUCCESS"
    assert dict(counts) == COMPTAGES_13_3
    assert sum(counts.values()) == 8
    assert (run.matched_name_count, run.matched_fqdn_count,
            run.matched_ip_count, run.no_match_count) == (40, 1, 1, 3)


def test_lignes_consolidees_portent_les_memes_codes_que_la_table(db):
    """§ 13.3 : chaque anomalie de la table Anomaly est aussi portée par sa ligne consolidée."""
    vms = [dict(vm) for vm in fetch_mock_vms()]
    ipam = [dict(rec) for rec in fetch_mock_ipam()]
    run = run_inventory(
        collect_vms=lambda: vms, collect_ipam=lambda: ipam,
    )

    from_table = Counter(
        anomaly.code for anomaly in Anomaly.query.filter_by(run_id=run.id)
    )
    from_rows = Counter(
        code
        for row in ConsolidatedAsset.query.filter_by(run_id=run.id)
        for code in json.loads(row.anomaly_codes or "[]")
    )
    assert from_rows == from_table == Counter(COMPTAGES_13_3)
