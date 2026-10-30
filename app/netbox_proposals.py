"""Build NetBox dry-run proposals using persisted observations only."""

from ipaddress import ip_address, ip_interface, ip_network

from app import db
from app.anomaly_exceptions import anomaly_coverage
from app.models import Anomaly, Asset, ConsolidatedAsset, IpamRecord

SUPPORTED = frozenset({
    "HOSTNAME_MISMATCH", "STATUS_MISMATCH", "NO_MATCH", "RENAMED",
    "MATCHED_IP", "DUPLICATE_DNS", "DUPLICATE_IP", "IP_OUT_OF_PREFIX",
    "VM_IP_CONFLICT", "DNS_MISSING_PTR", "PTR_MISMATCH",
    "MISSING_REQUIRED_TAG", "MISSING_TENANT", "MISSING_SITE",
})


def _refusal(reason):
    return {"simulation": True, "status": "aucune proposition", "reason": reason,
            "method": None, "url": None, "body": None, "explanation": None,
            "current_value": None}


def propose(anomaly: Anomaly):
    if anomaly.state != "ouverte":
        return _refusal("Anomalie non ouverte")
    if anomaly_coverage([anomaly])[anomaly.id] is not None:
        return _refusal("Anomalie couverte par une exception active")
    if anomaly.code not in SUPPORTED:
        return _refusal("Code sans correction NetBox démontrable")

    asset = (db.session.get(Asset, anomaly.asset_id) if anomaly.asset_id is not None else None)
    if asset is not None and asset.run_id != anomaly.run_id:
        return _refusal("Observation d'actif ambiguë")
    ipam = (db.session.get(IpamRecord, anomaly.ipam_record_id)
            if anomaly.ipam_record_id is not None else None)
    if ipam is not None and ipam.run_id != anomaly.run_id:
        return _refusal("Observation IPAM ambiguë")
    if ((anomaly.asset_id is not None and asset is None) or
            (anomaly.ipam_record_id is not None and ipam is None)):
        return _refusal("Observations liées introuvables")

    if anomaly.code != "NO_MATCH":
        if ipam is None:
            return _refusal("Aucune cible IPAM liée à cette anomalie")
        if ipam.ipam_source != "netbox":
            return _refusal("La cible observée n'est pas NetBox")
        return _refusal("Identifiant NetBox authentique absent des observations persistées ; PATCH impossible")

    if asset is None or not isinstance(asset.ip_reported, str):
        return _refusal("Adresse de l'actif absente")
    consolidation = ConsolidatedAsset.query.filter_by(run_id=anomaly.run_id, asset_id=asset.id).first()
    if (consolidation is None or consolidation.match_status != "NO_MATCH" or
            consolidation.ipam_record_id is not None or consolidation.match_doubt or ipam is not None):
        return _refusal("Absence de correspondance IPAM non prouvée ou ambiguë")
    try:
        address = ip_address(asset.ip_reported)
    except ValueError:
        return _refusal("Adresse de l'actif invalide")
    observations = IpamRecord.query.filter_by(run_id=anomaly.run_id).all()
    if not observations or any(row.ipam_source != "netbox" for row in observations):
        return _refusal("Observations NetBox du run absentes ou de sources mixtes")
    for row in observations:
        try:
            if row.ip and ip_interface(row.ip).ip == address:
                return _refusal("Adresse déjà observée dans l'IPAM")
        except ValueError:
            return _refusal("Adresse IPAM invalide : absence de cible non prouvée")
    for other in Asset.query.filter_by(run_id=anomaly.run_id).all():
        if other.id != asset.id and other.ip_reported:
            try:
                if ip_address(other.ip_reported) == address:
                    return _refusal("Adresse partagée entre plusieurs actifs")
            except ValueError:
                return _refusal("Adresse d'un autre actif invalide : contexte ambigu")
    prefixes = set()
    for row in observations:
        if not row.network_prefix:
            continue
        try:
            network = ip_network(row.network_prefix, strict=True)
        except ValueError:
            return _refusal("Préfixe observé invalide")
        if address in network:
            prefixes.add(network)
    if len(prefixes) != 1:
        return _refusal("Préfixe NetBox absent ou ambigu pour cette adresse")
    prefix = prefixes.pop()
    return {"simulation": True, "status": "proposition", "method": "POST",
            "url": "/api/ipam/ip-addresses/",
            "body": {"address": f"{address}/{prefix.prefixlen}"},
            "explanation": "Adresse absente des observations IPAM du run ; préfixe observé dans NetBox",
            "current_value": None, "reason": None}
