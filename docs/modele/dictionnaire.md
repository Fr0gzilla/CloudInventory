# Dictionnaire des données — CloudInventory v2.0

Les cinq ensembles sont `run`, `asset`, `ipam_record`, `consolidated_asset` et `anomaly`. Types SQLite proposés : INTEGER pour identifiants, compteurs et booléens (0/1), TEXT pour dates ISO 8601, chaînes et énumérations, REAL pour métriques numériques dont unités et précision restent à confirmer. NULL signifie valeur source absente ou non applicable, jamais suppression de la ligne. Les valeurs variables sont conservées par run ; les doublons IPAM restent stockables. Les clés de liaison doivent viser le même run.

| table | donnée | type SQLite | nullabilité | provenance / sens |
| --- | --- | --- | --- | --- |
| run | id | INTEGER | NON NULL | identité interne de l’exécution |
| run | status | TEXT | NON NULL | RUNNING, SUCCESS ou FAIL |
| run | started_at | TEXT | NON NULL | début de collecte |
| run | finished_at | TEXT | NULL | fin de collecte réussie |
| run | matched_name_count | INTEGER | NULL | nombre de VM MATCHED_NAME, finalisé au succès |
| run | matched_fqdn_count | INTEGER | NULL | nombre de VM MATCHED_FQDN, finalisé au succès |
| run | matched_ip_count | INTEGER | NULL | nombre de VM MATCHED_IP, finalisé au succès |
| run | no_match_count | INTEGER | NULL | nombre de VM NO_MATCH, finalisé au succès |
| run | error_message | TEXT | NULL | erreur conservée en cas d’échec |
| asset | id | INTEGER | NON NULL | identité interne de l’observation |
| asset | run_id | INTEGER | NON NULL | exécution de cette observation |
| asset | vm_id | TEXT | NON NULL | identité hyperviseur, type source à confirmer |
| asset | vm_name | TEXT | NULL | nom de la VM source |
| asset | fqdn | TEXT | NULL | nom complet source |
| asset | ip_reported | TEXT | NULL | IP annoncée par l’hyperviseur |
| asset | node | TEXT | NULL | nœud source |
| asset | type | TEXT | NULL | qemu ou lxc dans les sources simulées |
| asset | status | TEXT | NULL | état source, vocabulaire à confirmer |
| asset | tags | TEXT | NULL | tags source, encodage à confirmer |
| asset | role | TEXT | NULL | rôle déduit de la convention ou des tags |
| asset | os | TEXT | NULL | système d’exploitation source |
| asset | annotation | TEXT | NULL | annotation source |
| asset | cpu | REAL | NULL | mesure CPU, unité à confirmer |
| asset | ram | REAL | NULL | mesure mémoire, unité à confirmer |
| asset | disk | REAL | NULL | mesure disque, unité à confirmer |
| asset | uptime | REAL | NULL | durée de fonctionnement, unité à confirmer |
| ipam_record | id | INTEGER | NON NULL | identité interne ; ni IP ni DNS uniques |
| ipam_record | run_id | INTEGER | NON NULL | exécution de cette observation |
| ipam_record | ip | TEXT | NULL | IP source |
| ipam_record | dns_name | TEXT | NULL | nom DNS source |
| ipam_record | status | TEXT | NULL | statut de l’adresse, vocabulaire à confirmer |
| ipam_record | tenant | TEXT | NULL | locataire source |
| ipam_record | site | TEXT | NULL | site source |
| ipam_record | tags | TEXT | NULL | tags source, encodage à confirmer |
| ipam_record | duplicate_dns | INTEGER | NON NULL | indicateur calculé, 0/1 |
| ipam_record | duplicate_ip | INTEGER | NON NULL | indicateur calculé, 0/1 |
| ipam_record | meta_zone | TEXT | NULL | zone IPAM source |
| consolidated_asset | id | INTEGER | NON NULL | identité interne du résultat |
| consolidated_asset | run_id | INTEGER | NON NULL | exécution du résultat |
| consolidated_asset | asset_id | INTEGER | NON NULL | VM observée dans ce run |
| consolidated_asset | ipam_record_id | INTEGER | NULL | IPAM retenu dans ce run ; absent si NO_MATCH |
| consolidated_asset | ip_final | TEXT | NULL | IP retenue pour ce run |
| consolidated_asset | dns_final | TEXT | NULL | DNS retenu pour ce run |
| consolidated_asset | status_final | TEXT | NULL | statut retenu pour ce run, sens à confirmer |
| consolidated_asset | match_status | TEXT | NON NULL | MATCHED_NAME, MATCHED_FQDN, MATCHED_IP ou NO_MATCH |
| consolidated_asset | role | TEXT | NULL | rôle calculé à cette exécution |
| anomaly | id | INTEGER | NON NULL | identité interne de l’anomalie |
| anomaly | run_id | INTEGER | NON NULL | exécution où elle est constatée |
| anomaly | asset_id | INTEGER | NULL | VM concernée si applicable |
| anomaly | ipam_record_id | INTEGER | NULL | IPAM concerné si applicable |
| anomaly | code | TEXT | NON NULL | NO_MATCH, MATCHED_IP, HOSTNAME_MISMATCH, STATUS_MISMATCH, DUPLICATE_DNS ou DUPLICATE_IP |
| anomaly | description | TEXT | NON NULL | détail de l’anomalie |
| anomaly | created_at | TEXT | NON NULL | date du constat |

Décisions à confirmer avant schéma SQL : format réel de vm_id et unicité intra-run, unités des métriques, vocabulaires des états, sérialisation des tags, portée des anomalies de doublons et statut final. Aucun champ source inconnu n’est remplacé par une valeur fictive.
