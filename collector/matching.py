"""Fonctions pures de matching 4 niveaux — RG01 → RG05.

Stratégies d'appels, par ordre de priorité :
  1. MATCHED_NAME : hostname normalisé ↔ dns_name normalisé de l'IPAM
  2. MATCHED_FQDN : premier segment du FQDN normalisé ↔ dns_name normalisé de l'IPAM
  3. MATCHED_IP   : ip_reported de l'asset ↔ adresse IP de l'enregistrement IPAM
  4. NO_MATCH     : aucune des stratégies précédentes n'a matché

Aucune dépendance réseau ni base de données ; ces fonctions ne font que
comparaison de valeurs en mémoire, compatibles avec les jeux de mock.
"""

from collector.normalisation import normalize_hostname, normalize_fqdn


def build_dns_index(ipam_records):
    """Construit un index {dns_name_normalisé → record} depuis une liste d'enregistrements IPAM.

    Args:
        ipam_records: liste de dicts ``{ip, dns_name, ...}``.

    Returns:
        dict clé par dns_name normalisé, valeur le record correspondant.
    """
    index = {}
    for rec in ipam_records:
        key = normalize_hostname(rec.get("dns_name", ""))
        if key:
            index[key] = rec
    return index


def build_ip_index(ipam_records):
    """Construit un index {adresse_ip → record} depuis une liste d'enregistrements IPAM.

    Pour le fallback IP : on garde le premier record rencontré pour chaque IP.

    Args:
        ipam_records: liste de dicts ``{ip, dns_name, ...}``.

    Returns:
        dict clé par adresse IP, valeur le record correspondant.
    """
    index = {}
    for rec in ipam_records:
        ip = rec.get("ip", "")
        if ip and ip not in index:
            index[ip] = rec
    return index


def resolve_match(vm_name, vm_fqdn, vm_ip_reported, dns_index, ip_index, ipam_records=None):
    """Détermine le niveau de matching 4 niveaux pour une VM.

    Stratégie (RG01 → RG05) :
      1. MATCHED_NAME : hostname normalisé en clé DNS
      2. MATCHED_FQDN : premier segment du FQDN normalisé en clé DNS
      3. MATCHED_IP   : IP reportée en clé IP
      4. NO_MATCH     : aucune des stratégies précédentes n'a matché

    Args:
        vm_name: nom de la VM (vm_name de l'asset).
        vm_fqdn: FQDN de la VM (fqdn de l'asset), peut être ``None``.
        vm_ip_reported: IP rapportée par la VM (ip_reported de l'asset), peut être ``None``.
        dns_index: dict ``{hostname_normalisé → record_ipam}`` issu de ``build_dns_index``.
        ip_index: dict ``{adresse_ip → record_ipam}`` issu de ``build_ip_index``.
        ipam_records: liste optionnelle de dicts enregistrements IPAM ; si fournie,
            parmi les doublons DNS pour le nom matcheé, on préfère celui dont
            l'IP correspond à ``vm_ip_reported » (si aucun ne matche, comportement
            historique conservé).

    Returns:
        tuple ``(match_status, record_ipam)`` où::
            * ``match_status`` est l'un de ``MATCHED_NAME``, ``MATCHED_FQDN``,
              ``MATCHED_IP``, ``NO_MATCH``.
            * ``record_ipam`` est le dict record IPAM associé ou ``None``.
    """
    # Stratégie 1 : MATCHED_NAME (RG02)
    vm_key = normalize_hostname(vm_name)
    if vm_key in dns_index:
        raw = dns_index[vm_key]
        # Si des enregistrements d'entrée sont fournis, chercher parmi les doublons
        # DNS celui dont l'IP matche l'IP rapportée par la VM (priorité IP parmi doublons).
        if ipam_records is not None and vm_ip_reported:
            for rec in ipam_records:
                if normalize_hostname(rec.get("dns_name")) == vm_key and rec.get("ip") == vm_ip_reported:
                    raw = rec
                    break
        return "MATCHED_NAME", raw

    # Stratégie 2 : MATCHED_FQDN (RG03)
    fqdn_key = normalize_fqdn(vm_fqdn)
    if fqdn_key and fqdn_key in dns_index:
        return "MATCHED_FQDN", dns_index[fqdn_key]

    # Stratégie 3 : MATCHED_IP (RG04)
    if vm_ip_reported and vm_ip_reported in ip_index:
        return "MATCHED_IP", ip_index[vm_ip_reported]

    # Stratégie 4 : NO_MATCH (RG05)
    return "NO_MATCH", None


def compute_levels(vm_list, ipam_records):
    """Calcule le niveau de matching pour une liste de VMs par rapport à des enregistrements IPAM.

    Args:
        vm_list: liste de dicts ``{vm_name, fqdn, ip_reported, ...}``.
        ipam_records: liste de dicts ``{ip, dns_name, ...}``.

    Returns:
        dict ``{vm_id ou index: (match_status, record_ipam)}`` pour chaque VM.
        Si une VM ne possède pas de ``vm_id``, on utilise sa position dans la liste.
    """
    dns_index = build_dns_index(ipam_records)
    ip_index = build_ip_index(ipam_records)

    result = {}
    for i, vm in enumerate(vm_list):
        # On utilise vm_id s'il existe, sinon l'index positionnel
        vm_id = vm.get("vm_id", i)
        status, record = resolve_match(
            vm.get("vm_name", ""),
            vm.get("fqdn"),
            vm.get("ip_reported"),
            dns_index,
            ip_index,
            ipam_records,
        )
        result[vm_id] = (status, record)
    return result