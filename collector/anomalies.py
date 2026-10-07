"""Détection des six anomalies — RG05 à RG13.

Modul : collector/anomalies.py
Chaque fonction est pure : pas de réseau, pas d'accès base de données,
pas de mutation de données d'entrée. Elles reprennent la logique du
reference runner (inventory_runner.py) mais sous forme de fonctions
importables hors application Flask.

Aperçu des 6 anomalies et leurs RG :
  RG05/RG08  → NO_MATCH
  RG06/RG10  → HOSTNAME_MISMATCH
  RG07/RG11  → STATUS_MISMATCH
  RG12       → DUPLICATE_DNS
  RG13       → DUPLICATE_IP
  RG04/RG09  → MATCHED_IP
"""

from collector.normalisation import normalize_hostname, normalize_fqdn


def detect_no_match(vm_name, vm_fqdn, vm_ip_reported, dns_index, ip_index):
    """RG05 / RG08 — Détecter NO_MATCH.

    Aucune des 4 stratégies de matching (hostname, FQDN, IP) ne trouve
    de correspondance dans les enregistrements IPAM fournis.

    Args:
        vm_name: nom de la VM (hostname brut).
        vm_fqdn: FQDN de la VM (peut être None).
        vm_ip_reported: IP rapportée par la VM (peut être None).
        dns_index: dict {hostname_normalisé → record_ipam}.
        ip_index: dict {adresse_ip → record_ipam}.

    Returns:
        True si aucune stratégie ne matche → anomalie NO_MATCH.
    """
    # Stratégie 1 : MATCHED_NAME (RG02)
    if normalize_hostname(vm_name) in dns_index:
        return False
    # Stratégie 2 : MATCHED_FQDN (RG03)
    fqdn_key = normalize_fqdn(vm_fqdn)
    if fqdn_key and fqdn_key in dns_index:
        return False
    # Stratégie 3 : MATCHED_IP (RG04)
    if vm_ip_reported and vm_ip_reported in ip_index:
        return False
    # Stratégie 4 : NO_MATCH (RG05, RG08)
    return True


def detect_hostname_mismatch(vm_name, vm_ip_reported, ipam_record):
    """RG06 / RG10 — Détecter HOSTNAME_MISMATCH.

    La VM est matcheée par son IP dans l'enregistrement IPAM, mais le
    hostname normalisé de la VM diffère du DNS name de cet enregistrement.

    Args:
        vm_name: nom de la VM (hostname brut).
        vm_ip_reported: IP rapportée par la VM (peut être None).
        ipam_record: dict enregistrement IPAM trouvé (ou None).

    Returns:
        True si l'IP matche mais le hostname ne correspond pas → anomalie.
    """
    if not ipam_record or not vm_ip_reported:
        return False
    # L'IP de la VM doit matcher l'enregistrement IPAM
    if vm_ip_reported != ipam_record.get("ip"):
        return False
    # Le hostname de la VM ne doit pas égaler le DNS name de l'IPAM
    vm_key = normalize_hostname(vm_name)
    ipam_key = normalize_hostname(ipam_record.get("dns_name", ""))
    if vm_key == ipam_key:
        return False
    return True


def detect_status_mismatch(vm_status, ipam_status):
    """RG07 / RG11 — Détecter STATUS_MISMATCH.

    La VM est arrêtée (status='stopped') alors que son enregistrement
    IPAM porte le statut 'active'.

    Args:
        vm_status: statut de la VM (ex: 'running', 'stopped').
        ipam_status: statut de l'enregistrement IPAM (ex: 'active', 'reserved').

    Returns:
        True si VM arrêtée + IP active → anomalie STATUS_MISMATCH.
    """
    return vm_status == "stopped" and ipam_status == "active"


def detect_duplicate_dns(ipam_records):
    """RG12 — Détecter DUPLICATE_DNS.

    Le même nom DNS normalisé apparaît dans plusieurs enregistrements
    IPAM (doublon DNS).

    Args:
        ipam_records: liste de dicts enregistrements IPAM.

    Returns:
        True si un nom DNS normalisé apparaît plus d'une fois.
    """
    from collections import Counter

    dns_names = [
        rec.get("dns_name", "").strip().lower()
        for rec in ipam_records
        if rec.get("dns_name")
    ]
    counts = Counter(dns_names)
    return any(count > 1 for count in counts.values())


def detect_duplicate_ip(ipam_records):
    """RG13 — Détecter DUPLICATE_IP.

    La même adresse IP apparaît dans plusieurs enregistrements IPAM
    (doublon IP).

    Args:
        ipam_records: liste de dicts enregistrements IPAM.

    Returns:
        True si une adresse IP apparaît plus d'une fois.
    """
    from collections import Counter

    ips = [rec.get("ip", "") for rec in ipam_records if rec.get("ip")]
    counts = Counter(ips)
    return any(count > 1 for count in counts.values())


def detect_matched_ip(vm_ip_reported, ipam_records):
    """RG04 / RG09 — Détecter la correspondance MATCHED_IP.

    L'IP reportée par la VM matche une adresse IP dans les enregistrements
    IPAM (stratégie 3 du matching multi-niveau). Cette fonction ne vérifie
    que le matching d'IP ; la détection éventuelle de HOSTNAME_MISMATCH
    incombe à detect_hostname_mismatch.

    Args:
        vm_ip_reported: IP rapportée par la VM (peut être None).
        ipam_records: liste de dicts enregistrements IPAM.

    Returns:
        True si l'IP matche un enregistrement IPAM → stratégie MATCHED_IP.
    """
    ip_index = {}
    for rec in ipam_records:
        ip = rec.get("ip", "")
        if ip and ip not in ip_index:
            ip_index[ip] = rec
    return vm_ip_reported in ip_index