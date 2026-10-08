# Dictionnaire des données — CloudInventory v2.0

| Table | Colonne | Type | Longueur | Nullabilité | Clé | Description | RG liée |
|-------|---------|------|----------|-------------|-----|-------------|---------|
| Run | id | integer | auto-increment | NOT NULL | PK | Identifiant unique du run | RG17, RG19 |
| Run | status | varchar | 20 | NOT NULL | | Statut du run (RUNNING/SUCCESS/FAIL) | RG17, RG19, RG20 |
| Run | start_date | datetime | — | NOT NULL | | Date de début d'exécution | RG17 |
| Run | end_date | datetime | — | NULL | | Date de fin d'exécution | RG19 |
| Run | matched_name_count | integer | — | NULL | | Nombre de correspondances par hostname | RG19 |
| Run | matched_fqdn_count | integer | — | NULL | | Nombre de correspondances par FQDN | RG19 |
| Run | matched_ip_count | integer | — | NULL | | Nombre de correspondances par IP | RG19 |
| Run | no_match_count | integer | — | NULL | | Nombre de non-correspondances | RG19 |
| Run | error_message | text | — | NULL | | Message d'erreur en cas d'échec | RG20 |
| Asset | id | integer | auto-increment | NOT NULL | PK | Identifiant unique de l'asset | RG18 |
| Asset | vm_id | varchar | 50 | UNIQUE NOT NULL | | ID unique VM/CT depuis l'hyperviseur | RG18 |
| Asset | vm_name | varchar | 100 | NOT NULL | | Nom de la machine virtuelle/container | RG02, RG15 |
| Asset | fqdn | varchar | 200 | NULL | | Nom de domaine complet | RG02 |
| Asset | ip_reported | varchar | 45 | NULL | | IP rapportée par l'asset | RG04, RG06 |
| Asset | node | varchar | 10 | NULL | | Noeud hyperviseur (pve1-pve5) | RG31 |
| Asset | type | varchar | 10 | NULL | | Type (qemu/lxc) | RG32 |
| Asset | status | varchar | 20 | NULL | | Statut VM (running/stopped) | RG07 |
| Asset | tags | text | — | NULL | | Tags JSON de la VM | RG16 |
| Asset | role | varchar | 50 | NULL | | Rôle fonctionnel déduit | RG15, RG16 |
| Asset | match_status | varchar | 20 | NULL | | Statut de correspondance (MATCHED_NAME/MATCHED_FQDN/MATCHED_IP/NO_MATCH) | RG05-RG08 |
| Asset | source | varchar | 10 | NULL | | Source (VIRT/IPAM) | RG04 |
| Asset | consolidated_run_id | integer | — | NOT NULL | FK | Run auquel cet asset appartient | RG18 |
| Asset | os | varchar | 100 | NULL | | OS de la VM | RG24 |
| Asset | annotation | text | — | NULL | | Note ou description libre | RG24 |
| Asset | cpu_count | integer | — | NULL | | Nombre de vCPU alloués | RG24 |
| Asset | cpu_usage | float | — | NULL | | Utilisation CPU en pourcentage | RG24 |
| Asset | ram_max | bigint | — | NULL | | RAM allouée en octets | RG24 |
| Asset | ram_used | bigint | — | NULL | | RAM utilisée en octets | RG24 |
| Asset | disk_max | bigint | — | NULL | | Espace disque alloué en octets | RG24 |
| Asset | disk_used | bigint | — | NULL | | Espace disque utilisé en octets | RG24 |
| Asset | uptime | integer | — | NULL | | Uptime en secondes | RG19, RG24 |
| IpamRecord | id | integer | auto-increment | NOT NULL | PK | Identifiant unique enregistrement IPAM | RG18 |
| IpamRecord | ip | varchar | 45 | NOT NULL | | Adresse IP (non unique seule : RG13 détecte les doublons ; couple ip + dns_name unique, RG18) | RG04, RG06, RG09, RG13, RG18 |
| IpamRecord | dns_name | varchar | 200 | NOT NULL | | Nom DNS de l'enregistrement | RG02, RG03, RG10, RG12 |
| IpamRecord | tenant | varchar | 50 | NULL | | Tenant (Production/Infra/Dev/Staging/Supervision/DevOps) | RG33 |
| IpamRecord | site | varchar | 50 | NULL | | Site (DC1/DC2 ou zone) | RG34 |
| IpamRecord | tags | text | — | NULL | | Tags JSON | RG16 |
| IpamRecord | is_duplicate_dns | boolean | — | NULL | default FALSE | Indicateur doublon DNS | RG12 |
| IpamRecord | is_duplicate_ip | boolean | — | NULL | default FALSE | Indicateur doublon IP | RG13 |
| ConsolidatedAsset | id | integer | auto-increment | NOT NULL | PK | Identifiant unique asset consolidé | RG18 |
| ConsolidatedAsset | run_id | integer | — | NOT NULL | FK | Run associé | RG35 |
| ConsolidatedAsset | asset_id | integer | — | NOT NULL | FK | Référence Asset | RG18 |
| ConsolidatedAsset | ipam_record_id | integer | — | NULL | FK | Référence IpamRecord (peut être NULL si NO_MATCH) | RG18 |
| ConsolidatedAsset | match_status | varchar | 20 | NOT NULL | | Statut de correspondance (MATCHED_NAME/MATCHED_FQDN/MATCHED_IP/NO_MATCH) | RG05-RG08 |
| ConsolidatedAsset | role | varchar | 50 | NULL | | Rôle fonctionnel déduit après consolidation | RG15, RG16 |
| ConsolidatedAsset | anomaly_codes | text | — | NULL | | Codes anomalies détectées (JSON) | RG08-RG13 |
| ConsolidatedAsset | consolidated_at | datetime | — | NULL | | Timestamp de consolidation | RG19 |
| ConsolidatedAsset | ip_final | varchar | 45 | NULL | | IP consolidée finale | RG24 |
| ConsolidatedAsset | dns_final | varchar | 200 | NULL | | DNS consolidé final | RG24 |
| ConsolidatedAsset | vm_status | varchar | 20 | NULL | | Statut VM après consolidation | RG24 |
| Anomaly | id | integer | auto-increment | NOT NULL | PK | Identifiant unique anomalie | RG08 |
| Anomaly | run_id | integer | — | NOT NULL | FK | Run associé | RG17, RG19 |
| Anomaly | asset_id | integer | — | NULL | FK | Asset associé (peut être NULL) | RG06, RG07, RG11 |
| Anomaly | ipam_record_id | integer | — | NULL | FK | IpamRecord associé (peut être NULL) | RG12, RG13 |
| Anomaly | code | varchar | 20 | NOT NULL | | Code anomalie (NO_MATCH/HOSTNAME_MISMATCH/STATUS_MISMATCH/DUPLICATE_DNS/DUPLICATE_IP) | RG08-RG13 |
| Anomaly | description | text | — | NULL | | Description détaillée de l'anomalie | RG08 |
| Anomaly | detected_at | datetime | — | NOT NULL | | Timestamp de détection | RG19 |