"""T006 — Orchestrateur du run (collector/inventory_runner.py) : pipeline § 8.2, RG17 → RG20.

Un run complet sur les mocks rend les comptages du cahier § 13.3 ; deux runs gardent chacun leurs résultats
(§ 8.5, 8.6) ; un échec laisse un run FAIL et rien d'autre en base (RG20). Aucune connexion réseau : les
sources sont les mocks, ou des fonctions de remplacement.
"""
import copy
import json

from app.models import Anomaly, Asset, ConsolidatedAsset, IpamRecord, Run
from collector.inventory_runner import run_inventory
from collector.mock_netbox import fetch_mock_ipam
from collector.mock_virtualisation import fetch_mock_vms
from collector.proxmox_client import ProxmoxClientError

METRIC_FIELDS = (
    "os", "annotation", "cpu_count", "cpu_usage", "ram_max", "ram_used", "disk_max", "disk_used", "uptime",
)


def _run(vms=None, ipam=None):
    """Un run sur des listes fixes (défaut : les mocks)."""
    vms = fetch_mock_vms() if vms is None else vms
    ipam = fetch_mock_ipam() if ipam is None else ipam
    return run_inventory(collect_vms=lambda: copy.deepcopy(vms), collect_ipam=lambda: copy.deepcopy(ipam))


def _vm(name):
    """La VM du mock qui porte ce nom (copie)."""
    return next(vm for vm in fetch_mock_vms() if vm["vm_name"] == name)


def _rows(run):
    """Les lignes consolidées d'un run, par nom de VM."""
    return {row.asset.vm_name: row for row in ConsolidatedAsset.query.filter_by(run_id=run.id)}


def test_run_complet_sur_les_mocks(db):
    """RG17, RG19 : le run finit SUCCESS, avec les compteurs des quatre niveaux et ses dates."""
    run = _run()

    assert run.status == "SUCCESS"
    assert run.error_message is None
    counts = (run.matched_name_count, run.matched_fqdn_count, run.matched_ip_count, run.no_match_count)
    assert counts == (40, 1, 1, 3)
    assert run.start_date <= run.end_date
    assert Asset.query.count() == 45
    assert IpamRecord.query.count() == 44  # le doublon IP 10.0.4.16 est stocké deux fois
    assert ConsolidatedAsset.query.filter_by(run_id=run.id).count() == 45


def test_anomalies_attendues_du_cahier(db):
    """§ 13.3 : 3 NO_MATCH, 1 MATCHED_IP + HOSTNAME_MISMATCH, 2 STATUS_MISMATCH, 1 DUPLICATE_DNS, 1 DUPLICATE_IP."""
    run = _run()

    anomalies = Anomaly.query.filter_by(run_id=run.id).all()
    found = {}
    for anomaly in anomalies:
        found.setdefault(anomaly.code, set()).add(anomaly.asset.vm_name)
    assert found == {
        "NO_MATCH": {"temp-migration", "old-legacy", "decom-windows"},
        "HOSTNAME_MISMATCH": {"ghost-vm"},
        "STATUS_MISMATCH": {"backup-srv", "backup-offsite"},
        "DUPLICATE_DNS": {"monitoring"},
        "DUPLICATE_IP": {"gitea-repo"},
    }
    assert len(anomalies) == 8
    assert all(anomaly.detected_at is not None for anomaly in anomalies)


def test_rapprochements_particuliers(db):
    """§ 13.1 : srv-renamed-x999 par FQDN, ghost-vm par IP (old-printer), old-legacy sans IPAM."""
    rows = _rows(_run())

    renamed = rows["srv-renamed-x999"]
    assert (renamed.match_status, renamed.ipam_record.dns_name) == ("MATCHED_FQDN", "decom-server")
    ghost = rows["ghost-vm"]
    assert ghost.match_status == "MATCHED_IP"
    assert (ghost.ipam_record.dns_name, ghost.ip_final, ghost.dns_final) == ("old-printer", "10.0.9.98", "old-printer")
    assert json.loads(ghost.anomaly_codes) == ["HOSTNAME_MISMATCH"]
    legacy = rows["old-legacy"]
    assert (legacy.match_status, legacy.ipam_record_id, legacy.dns_final) == ("NO_MATCH", None, "old-legacy")
    assert json.loads(legacy.anomaly_codes) == ["NO_MATCH"]
    assert json.loads(rows["backup-srv"].anomaly_codes) == ["STATUS_MISMATCH"]
    assert json.loads(rows["web-a500"].anomaly_codes) == []


def test_asset_porte_le_dernier_resultat(db):
    """RG15, RG16, RG18 : rôle, statut de correspondance, source et run de l'asset ; métriques recopiées."""
    run = _run()

    web = Asset.query.filter_by(vm_name="web-a500").one()
    assert (web.role, web.match_status, web.source, web.consolidated_run_id) == (
        "Application", "MATCHED_NAME", "IPAM", run.id,
    )
    vm = _vm("web-a500")
    assert {field: getattr(web, field) for field in METRIC_FIELDS} == {field: vm[field] for field in METRIC_FIELDS}
    legacy = Asset.query.filter_by(vm_name="old-legacy").one()
    assert (legacy.match_status, legacy.source) == ("NO_MATCH", "VIRT")
    assert _rows(run)["web-a500"].role == web.role


def test_doublons_ipam_marques(db):
    """RG12, RG13 : les enregistrements de chaque doublon sont marqués, les autres non ; une anomalie par groupe."""
    _run()

    dns = {(rec.ip, rec.dns_name) for rec in IpamRecord.query.filter_by(is_duplicate_dns=True)}
    ips = {(rec.ip, rec.dns_name) for rec in IpamRecord.query.filter_by(is_duplicate_ip=True)}
    assert dns == {("10.0.4.10", "monitoring"), ("10.0.8.50", "monitoring")}
    assert ips == {("10.0.4.16", "gitea-repo"), ("10.0.4.16", "gitea-mirror")}
    duplicate_ip = Anomaly.query.filter_by(code="DUPLICATE_IP").one()
    assert "gitea-repo" in duplicate_ip.description and "gitea-mirror" in duplicate_ip.description
    assert duplicate_ip.ipam_record.dns_name == "gitea-repo"


def test_doublon_sans_asset_rattache_a_l_enregistrement(db):
    """RG13 : un doublon IP qu'aucune VM ne rapproche reste signalé, rattaché à son enregistrement."""
    ipam = [
        {"ip": "10.9.9.9", "dns_name": "orphan-a", "status": "active"},
        {"ip": "10.9.9.9", "dns_name": "orphan-b", "status": "active"},
    ]
    run = _run(vms=[], ipam=ipam)

    anomaly = Anomaly.query.filter_by(run_id=run.id).one()
    assert (anomaly.code, anomaly.asset_id, anomaly.ipam_record.dns_name) == ("DUPLICATE_IP", None, "orphan-a")
    assert run.status == "SUCCESS"


def test_couple_repete_par_la_source(db):
    """RG18 : un couple (ip, dns_name) répété par la source fait un seul enregistrement, sans doublon signalé."""
    ipam = [{"ip": "10.0.1.10", "dns_name": "web-a500", "status": "active"}] * 2
    run = _run(vms=[_vm("web-a500")], ipam=ipam)

    assert IpamRecord.query.count() == 1
    assert Anomaly.query.filter_by(run_id=run.id).count() == 0
    assert run.matched_name_count == 1


def test_deux_runs_gardent_chacun_leurs_resultats(db):
    """RG18, § 8.5 et 8.6 : l'upsert ne duplique rien ; chaque run garde ses lignes consolidées et ses anomalies."""
    first = _run()
    vms = fetch_mock_vms()
    web = next(vm for vm in vms if vm["vm_name"] == "web-a500")
    web["status"] = "stopped"
    web["ip_reported"] = "10.0.1.99"
    second = _run(vms=vms)

    assert (first.status, second.status) == ("SUCCESS", "SUCCESS")
    assert (Asset.query.count(), IpamRecord.query.count()) == (45, 44)
    assert ConsolidatedAsset.query.filter_by(run_id=first.id).count() == 45
    assert ConsolidatedAsset.query.filter_by(run_id=second.id).count() == 45
    asset = Asset.query.filter_by(vm_id=web["vm_id"]).one()
    assert (asset.status, asset.ip_reported, asset.consolidated_run_id) == ("stopped", "10.0.1.99", second.id)
    assert _rows(first)["web-a500"].vm_status == "running"
    assert _rows(second)["web-a500"].vm_status == "stopped"
    # web-a500 arrêté alors que son IP est active dans NetBox : une anomalie de plus au second run (RG11)
    assert Anomaly.query.filter_by(run_id=first.id).count() == 8
    assert Anomaly.query.filter_by(run_id=second.id).count() == 9


def test_echec_de_source_run_fail_sans_ecriture(db):
    """RG20 : la source virtualisation lève ; le run est FAIL avec son message et rien d'autre n'est écrit."""
    def panne():
        raise ProxmoxClientError("Proxmox injoignable")

    run = run_inventory(collect_vms=panne, collect_ipam=fetch_mock_ipam)

    assert run.status == "FAIL"
    assert run.error_message == "ProxmoxClientError: Proxmox injoignable"
    assert run.end_date is not None and run.matched_name_count is None
    assert Run.query.count() == 1
    for model in (Asset, IpamRecord, ConsolidatedAsset, Anomaly):
        assert model.query.count() == 0


def test_echec_en_cours_de_pipeline_annule_le_run(db):
    """RG20 : une erreur après le début de l'upsert annule tout le run ; le run précédent reste intact."""
    first = _run()
    vms = fetch_mock_vms()
    vms[1]["status"] = "stopped" if vms[1]["status"] == "running" else "running"  # ne doit pas survivre
    vms[0]["node"] = "pve9"  # hors de la contrainte CHECK du nœud : échec à l'écriture
    second = _run(vms=vms)

    assert second.status == "FAIL"
    assert "IntegrityError" in second.error_message
    assert Asset.query.filter_by(vm_id=vms[1]["vm_id"]).one().status == fetch_mock_vms()[1]["status"]
    assert ConsolidatedAsset.query.filter_by(run_id=second.id).count() == 0
    assert Anomaly.query.filter_by(run_id=second.id).count() == 0
    assert ConsolidatedAsset.query.filter_by(run_id=first.id).count() == 45
    assert Anomaly.query.filter_by(run_id=first.id).count() == 8


def test_sources_par_defaut_selon_la_configuration(db, monkeypatch):
    """§ 7 et T005 : sans source passée, USE_MOCK_VIRT et USE_MOCK_IPAM à true choisissent les mocks."""
    monkeypatch.setenv("USE_MOCK_VIRT", "true")
    monkeypatch.setenv("USE_MOCK_IPAM", "true")

    run = run_inventory()

    assert run.status == "SUCCESS"
    assert (Asset.query.count(), IpamRecord.query.count()) == (45, 44)
