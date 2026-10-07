"""Mock de la source B (IPAM/DNS NetBox) — dataset fixe Phase 1.

Correspondances DNS pour les VMs des 5 nodes.
Les VMs NO_MATCH (999, 998, 997, 996, 995) n'ont volontairement pas d'entrée ici.
"""


MOCK_IPAM = [
    # ── pve1 : production — front & applicatif ──
    {"ip": "10.0.1.10", "dns_name": "web-a500", "status": "active", "tenant": "Production", "site": "DC1", "meta_zone": "ZM"},
    {"ip": "10.0.1.11", "dns_name": "web-b501", "status": "active", "tenant": "Production", "site": "DC1", "meta_zone": "ZM"},
    {"ip": "10.0.1.12", "dns_name": "app-backend", "status": "active", "tenant": "Production", "site": "DC1", "meta_zone": "ZM"},
    {"ip": "10.0.1.13", "dns_name": "app-worker", "status": "active", "tenant": "Production", "site": "DC1", "meta_zone": "ZM"},
    {"ip": "10.0.1.14", "dns_name": "cache-redis", "status": "active", "tenant": "Production", "site": "DC1", "meta_zone": "ZM"},
    {"ip": "10.0.1.15", "dns_name": "proxy-nginx", "status": "active", "tenant": "Production", "site": "DC1", "meta_zone": "ZM"},
    {"ip": "10.0.1.16", "dns_name": "haproxy-lb", "status": "active", "tenant": "Production", "site": "DC1", "meta_zone": "ZM"},
    {"ip": "10.0.1.17", "dns_name": "rabbitmq-prod", "status": "active", "tenant": "Production", "site": "DC1", "meta_zone": "ZM"},

    # ── pve2 : production — données & stockage ──
    {"ip": "10.0.2.10", "dns_name": "db-b500", "status": "active", "tenant": "Production", "site": "DC1", "meta_zone": "ZM"},
    {"ip": "10.0.2.11", "dns_name": "db-replica", "status": "active", "tenant": "Production", "site": "DC1", "meta_zone": "ZM"},
    {"ip": "10.0.2.12", "dns_name": "nfs-storage", "status": "active", "tenant": "Production", "site": "DC1", "meta_zone": "ZM"},
    {"ip": "10.0.2.13", "dns_name": "minio-s3", "status": "active", "tenant": "Production", "site": "DC1", "meta_zone": "ZM"},
    {"ip": "10.0.2.14", "dns_name": "log-elastic", "status": "active", "tenant": "Production", "site": "DC1", "meta_zone": "ZM"},
    {"ip": "10.0.2.15", "dns_name": "log-kibana", "status": "active", "tenant": "Production", "site": "DC1", "meta_zone": "ZM"},
    {"ip": "10.0.2.16", "dns_name": "backup-srv", "status": "active", "tenant": "Infra", "site": "DC1", "meta_zone": "ZCS"},
    {"ip": "10.0.2.17", "dns_name": "backup-offsite", "status": "active", "tenant": "Infra", "site": "DC1", "meta_zone": "ZCS"},

    # ── pve3 : infra & services réseau ──
    {"ip": "10.0.3.10", "dns_name": "dns-d500", "status": "active", "tenant": "Infra", "site": "DC1", "meta_zone": "ZCS"},
    {"ip": "10.0.3.11", "dns_name": "dns-secondary", "status": "active", "tenant": "Infra", "site": "DC1", "meta_zone": "ZCS"},
    {"ip": "10.0.3.12", "dns_name": "ldap-auth", "status": "active", "tenant": "Infra", "site": "DC1", "meta_zone": "ZCS"},
    {"ip": "10.0.3.13", "dns_name": "mail-smtp", "status": "active", "tenant": "Infra", "site": "DC1", "meta_zone": "ZCS"},
    {"ip": "10.0.3.14", "dns_name": "vpn-gateway", "status": "active", "tenant": "Infra", "site": "DC1", "meta_zone": "ZCS"},
    {"ip": "10.0.3.15", "dns_name": "firewall-pf", "status": "active", "tenant": "Infra", "site": "DC1", "meta_zone": "ZCS"},
    {"ip": "10.0.3.16", "dns_name": "ntp-srv", "status": "active", "tenant": "Infra", "site": "DC1", "meta_zone": "ZCS"},
    {"ip": "10.0.3.17", "dns_name": "syslog-central", "status": "active", "tenant": "Infra", "site": "DC1", "meta_zone": "ZCS"},

    # ── pve4 : supervision & monitoring ──
    {"ip": "10.0.4.10", "dns_name": "monitoring", "status": "active", "tenant": "Supervision", "site": "DC1", "meta_zone": "ZCS"},
    {"ip": "10.0.4.11", "dns_name": "grafana-dash", "status": "active", "tenant": "Supervision", "site": "DC1", "meta_zone": "ZCS"},
    {"ip": "10.0.4.12", "dns_name": "prometheus-ts", "status": "active", "tenant": "Supervision", "site": "DC1", "meta_zone": "ZCS"},
    {"ip": "10.0.4.13", "dns_name": "alertmanager", "status": "active", "tenant": "Supervision", "site": "DC1", "meta_zone": "ZCS"},
    {"ip": "10.0.4.14", "dns_name": "uptime-kuma", "status": "active", "tenant": "Supervision", "site": "DC1", "meta_zone": "ZCS"},
    {"ip": "10.0.4.15", "dns_name": "ansible-ctrl", "status": "active", "tenant": "DevOps", "site": "DC1", "meta_zone": "ZCS"},
    {"ip": "10.0.4.16", "dns_name": "gitea-repo", "status": "active", "tenant": "DevOps", "site": "DC1", "meta_zone": "ZCS"},
    {"ip": "10.0.4.17", "dns_name": "vault-secrets", "status": "active", "tenant": "Infra", "site": "DC1", "meta_zone": "ZCS"},

    # ── pve5 : dev, test & staging ──
    {"ip": "10.0.5.10", "dns_name": "dev-frontend", "status": "active", "tenant": "Dev", "site": "DC1", "meta_zone": "ZE"},
    {"ip": "10.0.5.11", "dns_name": "dev-api", "status": "reserved", "tenant": "Dev", "site": "DC1", "meta_zone": "ZE"},
    {"ip": "10.0.5.12", "dns_name": "test-db", "status": "active", "tenant": "Dev", "site": "DC1", "meta_zone": "ZE"},
    {"ip": "10.0.5.13", "dns_name": "ci-runner", "status": "active", "tenant": "DevOps", "site": "DC1", "meta_zone": "ZE"},
    {"ip": "10.0.5.14", "dns_name": "staging-web", "status": "active", "tenant": "Staging", "site": "DC1", "meta_zone": "ZE"},
    {"ip": "10.0.5.15", "dns_name": "staging-api", "status": "active", "tenant": "Staging", "site": "DC1", "meta_zone": "ZE"},
    {"ip": "10.0.5.16", "dns_name": "staging-db", "status": "active", "tenant": "Staging", "site": "DC1", "meta_zone": "ZE"},
    {"ip": "10.0.5.17", "dns_name": "sandbox-test", "status": "reserved", "tenant": "Dev", "site": "DC1", "meta_zone": "ZE"},

    # ── IPs orphelines (pas de VM correspondante) ──
    {"ip": "10.0.9.99", "dns_name": "decom-server", "status": "reserved", "tenant": None, "site": "DC1", "meta_zone": "ZM"},
    {"ip": "10.0.9.98", "dns_name": "old-printer", "status": "deprecated", "tenant": None, "site": "DC1", "meta_zone": "ZM"},

    # ── Anomalies volontaires pour démonstration ──
    # DUPLICATE_DNS : même dns_name sur 2 IPs différentes
    {"ip": "10.0.8.50", "dns_name": "monitoring", "status": "active", "tenant": "Supervision", "site": "DC2", "meta_zone": "ZCS"},
    # DUPLICATE_IP : même IP avec 2 dns_name différents
    {"ip": "10.0.4.16", "dns_name": "gitea-mirror", "status": "active", "tenant": "DevOps", "site": "DC1", "meta_zone": "ZCS"},
]


def fetch_mock_ipam():
    """Retourne le dataset mock IPAM/DNS.

    Le jeu est constant : 44 records (40 + 2 orphelins + 2 anomalies).
    Chaque appel renvoie une copie, pour qu'aucun appelant ne puisse altérer
    le jeu de la collecte suivante (déterminisme).

    Returns:
        list[dict]: Liste de records IPAM simulés.
    """
    return [dict(rec) for rec in MOCK_IPAM]
