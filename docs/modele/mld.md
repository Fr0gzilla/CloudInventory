## Tables

Les identifiants physiques `id` correspondent aux identifiants conceptuels de chaque entité. Les dates sont des textes ISO 8601 ; les métriques REAL conservent la valeur source, sans conversion d'unité. SQLite nécessite `PRAGMA foreign_keys = ON` sur chaque connexion pour appliquer les FK. Les références composées imposent l'appartenance au même run ; `UNIQUE(id, run_id)` fournit la clé parent nécessaire malgré la PK simple.

### run

| colonne | type | null | défaut | contrainte |
| --- | --- | --- | --- | --- |
| id | INTEGER | non | — | identité du run |
| status | TEXT | non | — | CHECK RUNNING, SUCCESS, FAIL |
| started_at | TEXT | non | — | date ISO 8601 |
| finished_at | TEXT | oui | NULL | date ISO 8601 |
| matched_name_count | INTEGER | oui | NULL | CHECK ≥ 0 |
| matched_fqdn_count | INTEGER | oui | NULL | CHECK ≥ 0 |
| matched_ip_count | INTEGER | oui | NULL | CHECK ≥ 0 |
| no_match_count | INTEGER | oui | NULL | CHECK ≥ 0 |
| error_message | TEXT | oui | NULL | message en cas d'échec |

- PK : id (INTEGER PRIMARY KEY).
- FK : aucune.
- Index : PK implicite ; la purge des runs récents utilise id décroissant.
- Origine : RUN — RG1, RG7, RG19, RG20, RG23.

### asset

| colonne | type | null | défaut | contrainte |
| --- | --- | --- | --- | --- |
| id | INTEGER | non | — | identité d'observation |
| run_id | INTEGER | non | — | appartenance au run |
| vm_id | TEXT | non | — | identité source ; pas d'unicité intra-run confirmée |
| vm_name | TEXT | oui | NULL | nom source |
| fqdn | TEXT | oui | NULL | nom complet source |
| ip_reported | TEXT | oui | NULL | IP source |
| node | TEXT | oui | NULL | nœud source |
| type | TEXT | oui | NULL | CHECK qemu, lxc si renseigné |
| status | TEXT | oui | NULL | statut source |
| tags | TEXT | oui | NULL | forme source à confirmer |
| role | TEXT | oui | NULL | rôle calculé |
| os | TEXT | oui | NULL | système source |
| annotation | TEXT | oui | NULL | annotation |
| cpu | REAL | oui | NULL | mesure source |
| ram | REAL | oui | NULL | mesure source |
| disk | REAL | oui | NULL | mesure source |
| uptime | REAL | oui | NULL | mesure source |

- PK : id ; UNIQUE(id, run_id) pour les références composites.
- FK : run_id → run(id) ON DELETE CASCADE.
- Index : idx_asset_run (run_id) ; idx_asset_run_vm (run_id, vm_id) ; idx_asset_run_name (run_id, vm_name) ; idx_asset_run_fqdn (run_id, fqdn) ; idx_asset_run_ip (run_id, ip_reported) ; idx_asset_run_node (run_id, node), pour comparaison, rapprochement et filtre par nœud.
- Origine : ASSET, observer_vm — RG2, RG7, RG15, RG16, RG21, RG22.

### ipam_record

| colonne | type | null | défaut | contrainte |
| --- | --- | --- | --- | --- |
| id | INTEGER | non | — | identité d'observation |
| run_id | INTEGER | non | — | appartenance au run |
| ip | TEXT | oui | NULL | doublons permis |
| dns_name | TEXT | oui | NULL | doublons permis |
| status | TEXT | oui | NULL | statut source |
| tenant | TEXT | oui | NULL | locataire source |
| site | TEXT | oui | NULL | site source |
| tags | TEXT | oui | NULL | forme source à confirmer |
| duplicate_dns | INTEGER | non | 0 | CHECK (0, 1) |
| duplicate_ip | INTEGER | non | 0 | CHECK (0, 1) |
| meta_zone | TEXT | oui | NULL | zone source même sans VM |

- PK : id ; UNIQUE(id, run_id) pour les références composites ; aucune unicité de ip ou dns_name.
- FK : run_id → run(id) ON DELETE CASCADE.
- Index : idx_ipam_run_ip (run_id, ip) ; idx_ipam_run_dns (run_id, dns_name), pour rapprochement et détection de doublons ; tous deux servent aussi la FK run_id.
- Origine : IPAM_RECORD, observer_ipam — RG3, RG8, RG9, RG17, RG18, RG24.

### consolidated_asset

| colonne | type | null | défaut | contrainte |
| --- | --- | --- | --- | --- |
| id | INTEGER | non | — | identité du résultat |
| run_id | INTEGER | non | — | appartenance au run |
| asset_id | INTEGER | non | — | VM du même run ; UNIQUE(asset_id, run_id) |
| ipam_record_id | INTEGER | oui | NULL | rapprochement facultatif, même run |
| ip_final | TEXT | oui | NULL | IP retenue |
| dns_final | TEXT | oui | NULL | DNS retenu |
| status_final | TEXT | oui | NULL | statut retenu |
| match_status | TEXT | non | — | CHECK MATCHED_NAME, MATCHED_FQDN, MATCHED_IP, NO_MATCH |
| role | TEXT | oui | NULL | rôle instantané |

- PK : id ; UNIQUE(asset_id, run_id) réalise ASSET (0,1) ↔ résultat (1,1).
- FK : run_id → run(id) ON DELETE CASCADE.
- FK : (asset_id, run_id) → asset(id, run_id) ON DELETE CASCADE.
- FK : (ipam_record_id, run_id) → ipam_record(id, run_id) ON DELETE RESTRICT.
- Index : idx_consolidated_run (run_id) ; UNIQUE(asset_id, run_id) pour la FK asset ; idx_consolidated_ipam_run (ipam_record_id, run_id) ; idx_consolidated_run_match (run_id, match_status), pour la consultation et le filtre des résultats.
- Origine : CONSOLIDATED_ASSET, produire_resultat, consolider, rapprocher — RG4, RG7, RG9, RG14, RG22.

### anomaly

| colonne | type | null | défaut | contrainte |
| --- | --- | --- | --- | --- |
| id | INTEGER | non | — | identité du constat |
| run_id | INTEGER | non | — | appartenance au run |
| asset_id | INTEGER | oui | NULL | VM facultative du même run |
| ipam_record_id | INTEGER | oui | NULL | IPAM facultatif du même run |
| code | TEXT | non | — | CHECK NO_MATCH, MATCHED_IP, HOSTNAME_MISMATCH, STATUS_MISMATCH, DUPLICATE_DNS, DUPLICATE_IP |
| description | TEXT | non | — | détail du constat |
| created_at | TEXT | non | — | date ISO 8601 |

- PK : id.
- FK : run_id → run(id) ON DELETE CASCADE.
- FK : (asset_id, run_id) → asset(id, run_id) ON DELETE RESTRICT.
- FK : (ipam_record_id, run_id) → ipam_record(id, run_id) ON DELETE RESTRICT.
- Index : idx_anomaly_run (run_id) pour la consultation ; idx_anomaly_asset_run (asset_id, run_id) ; idx_anomaly_ipam_run (ipam_record_id, run_id) pour les FK et les fiches liées.
- Origine : ANOMALY, constater, concerner_vm, concerner_ipam — RG5, RG6, RG11, RG12, RG13, RG17, RG18.

## Décisions

- Cinq entités → cinq tables ; les huit associations se traduisent par huit FK (quatre appartenances au run, deux liens du résultat et deux liens de l'anomalie), sans table de jointure. Les FK composites et les clés candidates associées contrôlent le run des références facultatives et obligatoires.
- 1FN : une colonne par propriété ; les tags restent une valeur textuelle source indivisible en attendant Q3, sans recherche de composant SQL. 2FN : chaque PK est simple ; les clés candidates composites (id, run_id) ne portent aucune dépendance partielle de données métier. 3FN : les résultats et rôles sont des instantanés calculés exigés par RG4, RG7, RG15–RG16 et RG22 ; compteurs du run et indicateurs de doublons sont des matérialisations prévues par RG1, RG17–RG19, recalculées dans le traitement du run, sans écraser les runs précédents.
- Les colonnes physiques suivent le dictionnaire publié (`id`, `run_id`, `type`, `status`, `tags`, `role`, `ip`, `code`, `created_at`) ; elles réalisent respectivement les attributs conceptuels identifiant, run, asset_type, asset_status/ipam_status/run_status, asset_tags/ipam_tags, asset_role/consolidated_role, ipam_ip, anomaly_code et anomaly_date. Le SQL n'impose aucune longueur absente des règles.
- CASCADE pour la suppression d'un run et de ses observations/résultats ; RESTRICT pour une observation encore référencée par une anomalie ou un rapprochement. Une FK composite facultative avec run_id non NULL est vérifiée dès que son identifiant de référence est présent.
- Aucun CHECK reliant match_status et référence IPAM, ni déclencheur forçant les étapes RUNNING/SUCCESS/FAIL : les règles de pipeline RG9, RG14, RG19–RG20 relèvent du traitement applicatif ; les statuts sources et les unités restent à confirmer (Q1–Q6).

## Ordre de création

1. run
2. asset, ipam_record (après run)
3. consolidated_asset, anomaly (après asset et ipam_record)
