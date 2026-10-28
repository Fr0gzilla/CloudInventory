# Dictionnaire des données — CloudInventory v2.2

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
| ipam_record | custom_fields | TEXT (JSON) | NULL | ajout EV07 : objet NetBox complet, sérialisation JSON sans perte ; NULL si non fourni, objet vide distinct ; instantané de chaque run ; aucune clé arbitraire imposée |
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
| anomaly | code | TEXT | NON NULL | NO_MATCH, MATCHED_IP, HOSTNAME_MISMATCH, STATUS_MISMATCH, DUPLICATE_DNS, DUPLICATE_IP ou RENAMED (§18 EV12) |
| anomaly | description | TEXT | NON NULL | détail de l’anomalie |
| anomaly | created_at | TEXT | NON NULL | date du constat |
| anomaly | tracking_key | TEXT | NULL | clé de suivi inter-runs, forme à confirmer (Q9) ; les constats restent attachés à leur run |
| anomaly | state | TEXT | NON NULL | ouverte, acquittée, résolue ; état courant, transitions datées (Q9) |
| anomaly | state_changed_at | TEXT | NULL | date ISO 8601 du dernier changement d’état (Q9) |
| anomaly | severity | TEXT | NON NULL | info, mineure, majeure, critique ; classification par code obligatoire pour chaque nouveau constat, correspondance à confirmer (Q10) |
| anomaly_exception | id | INTEGER | NON NULL | identifiant interne de l’exception (§18 EV15) |
| anomaly_exception | code | TEXT | NON NULL | un des sept codes, dont RENAMED |
| anomaly_exception | target | TEXT | NON NULL | cible stable explicite, format selon code à préciser (Q11) |
| anomaly_exception | justification | TEXT | NON NULL | texte non vide |
| anomaly_exception | expires_at | TEXT | NON NULL | date/heure ISO 8601 d’expiration ; comparaison explicite avant suppression (Q11) |
| anomaly_exception | author | TEXT | NON NULL | identité de l’auteur, compte administrateur actuel (Q12) |
| run | current_step | INTEGER | NULL | étape en cours 1 à 7 ; NULL avant la première ; étapes §8.2 |
| run | completed_steps | INTEGER | NULL | progression 0 à 7, NULL pour les anciens runs ; rollback : Q13 |
| run | run_trigger | TEXT | NULL | manuel ou planifié ; NULL si ancien run sans provenance (Q14) |
| run | schedule_id | INTEGER | NULL | référence à run_schedule.id si déclenché par la planification |
| run_schedule | id | INTEGER | NON NULL | identifiant de planification (§20 EV32) |
| run_schedule | frequency | TEXT | NON NULL | horaire ou quotidien |
| run_schedule | enabled | INTEGER | NON NULL | booléen 0/1 ; activation à préciser (Q15) |
| run_schedule | next_at | TEXT | NULL | date/heure ISO 8601 calculée, fuseau à préciser (Q15) |

Décisions à confirmer avant schéma SQL : format réel de vm_id et unicité intra-run, unités des métriques, vocabulaires des états, sérialisation des tags, portée des anomalies de doublons et statut final. Aucun champ source inconnu n’est remplacé par une valeur fictive.

## Contrat EV07/EV59 et baseline SQL–ORM

- `ipam_record.meta_zone TEXT NULL` existe dans le SQL et l’ORM ; `collector/netbox.py` extrait déjà `custom_fields.meta_zone` mais écarte les autres champs. Le SQL cible contient `ipam_record.custom_fields TEXT NULL` (JSON sérialisé) et l’index `idx_ipam_run_zone` sur `(run_id, meta_zone)` pour filtrer les observations par zone dans un run ; ces deux ajouts sont absents de l’ORM et restent à y reporter. La zone manquante reste NULL ; ni unicité ni valeur par défaut fabriquée. La forme et le vocabulaire des custom fields restent ouverts (Q8). Voir `baseline-sql-orm.md`, « Évolution additive et écart temporaire ».
- La baseline SQL ↔ ORM avant EV07 comporte cinq tables `run`, `asset`, `ipam_record`, `consolidated_asset`, `anomaly`, avec les mêmes colonnes, nullabilités, CHECK et FK composites ; les identifiants `INTEGER PRIMARY KEY` SQL correspondent aux PK ORM `nullable=True` (SQLite attribue les identifiants). Les quatorze index conservés, les contraintes `UNIQUE(id, run_id)` d’asset et d’ipam_record, `UNIQUE(asset_id, run_id)` du consolidé et les six codes d’anomalie sont détaillés dans `baseline-sql-orm.md`, « État conservé ». Pour une base existante, ajouter la colonne nullable et l’index sans effacer les lignes ; inscrire l’état antérieur comme baseline versionnée avant les migrations suivantes (§23, EV59).

## Contrat cible v2.2 (non encore appliqué au SQL ni à l’ORM)

- Les sept étapes numérotées du §8.2 sont : 1 création RUNNING ; 2 collecte VM/CT ; 3 collecte IPAM ; 4 rapprochement des observations par identités source ; 5 consolidation, rôle et anomalies ; 6 doublons DNS/IP ; 7 finalisation SUCCESS, dates et compteurs. L’affichage de progression est indépendant de la transaction des observations ; si rollback, aucun constat d’un run échoué ne doit subsister (RG20, RG28, RG46).
- Choix SQLite proposés, non obligations du cahier : `anomaly_exception` et `run_schedule` sont deux nouvelles entités ; codes, états, sévérités et fréquences se stockent en TEXT avec CHECK ; dates en TEXT ISO 8601 ; FK `run.schedule_id` nullable ; progression en entiers bornés. Les anciennes lignes restent lisibles, sans imputation rétrospective de provenance ni gravité fictive.
- La persistance exacte du suivi inter-runs (clé, transitions, historique de résolution), la répartition des sept sévérités, la granularité des cibles et les paramètres temporels restent Q9–Q15 ; une sévérité inconnue est signalée et non remplacée. Ne pas appliquer une exception expirée ni fusionner des anomalies ambiguës.
