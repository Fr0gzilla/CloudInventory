# Dictionnaire des données — CloudInventory v3.0

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
| anomaly | severity | TEXT | NULL | info, mineure, majeure, critique si classification par code connue ; NULL signalé si inconnue (Q10, Q26) |
| anomaly_exception | id | INTEGER | NON NULL | identifiant interne de l’exception (§18 EV15) |
| anomaly_exception | code | TEXT | NON NULL | un des sept codes historiques, dont RENAMED, ou un des treize codes v3 candidats (Q26) |
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

## Compléments conceptuels v2.3 (schéma cible)

| ensemble | donnée | type conceptuel | contraintes / provenance |
| --- | --- | --- | --- |
| asset | virtual_source | texte à confirmer | origine par run ; disjoint de vm_id pour éviter les collisions inter-sources (§17 EV01, Q16) |
| ipam_record | ipam_source | texte à confirmer | origine par run ; IP et DNS toujours non uniques (§17 EV02, Q16) |
| account | account_id | entier | identifiant candidat (§22 EV48) |
| account | account_username | texte | identité du compte ; portée d’unicité Q17 (§22 EV48–EV49) |
| account | account_role | énumération{admin,opérateur,lecteur} | rôle obligatoire ; permissions détaillées Q18 (§22 EV48) |
| account | account_auth_source | énumération{local,LDAP,AD} | origine de connexion ; liaison inter-origines Q17 (§22 EV49) |
| account | local_password_hash | texte | seulement compte local ; absent pour LDAP/AD (§10.1, §22 EV49) |
| audit | audit_id | entier | identifiant candidat (§22 EV52) |
| audit | audit_actor_ref | entier à confirmer | référence au compte si connu ; rétention Q19 (§22 EV52) |
| audit | audit_actor_identity | texte | identité historique de l’auteur ; conservation si compte supprimé, Q19 (§22 EV52) |
| audit | audit_action | texte à confirmer | lancement, acquittement, purge, modification ; extension Q19 (§22 EV52) |
| audit | audit_date | datetime | instant de l’action, obligatoire (§22 EV52) |
| audit | audit_target | texte à confirmer | cible de l’action, format Q19 (§22 EV52) |

Migration compatible : ajouter les origines facultatives aux observations historiques sans leur attribuer arbitrairement une source ; conserver les comptes et les runs préexistants, sans fabriquer d’identité annuaire ni de rôle historique. L’identité d’un compte ancien et l’attribution de ses droits restent à confirmer (Q17–Q18). Le stockage précis et les contraintes SQL sont des choix de la chaîne MLD, pas des obligations nouvelles du besoin.

Correspondance physique : `asset.virtual_source TEXT NULL`, `ipam_record.ipam_source TEXT NULL` ; `auth_origin(origin_code TEXT PRIMARY KEY)` limité à local/LDAP/AD, `role(role_code TEXT PRIMARY KEY)` limité à admin/opérateur/lecteur, `permission(permission_code TEXT PRIMARY KEY)` non vide. `account(id INTEGER PRIMARY KEY, origin_code TEXT NOT NULL, role_code TEXT NOT NULL, username TEXT NOT NULL, local_password_hash TEXT NULL)` : FK vers les référentiels, UNIQUE(origin_code, username), hash non vide obligatoire pour local et NULL pour LDAP/AD. `role_permission(id INTEGER PRIMARY KEY, role_code TEXT NOT NULL, permission_code TEXT NOT NULL)` : deux FK et UNIQUE(role_code, permission_code), sans attribution initiale. `audit(id INTEGER PRIMARY KEY, account_id INTEGER NULL, actor_identity TEXT NOT NULL, action TEXT NOT NULL, created_at TEXT NOT NULL, target TEXT NOT NULL)` : FK compte ON DELETE SET NULL ; identité et cible historiques non vides. Une purge des runs ne supprime jamais l'audit. L'unicité par origine est un choix technique révisable (Q17).

## Compléments conceptuels v3.0 (§17 EV09 ; §18 EV11, EV17–EV21 ; §19 EV25)

| ensemble | donnée | type conceptuel | contraintes / provenance |
| --- | --- | --- | --- |
| snapshot observé | snapshot_id, snapshot_created_at | texte à confirmer, datetime à confirmer | identité et date source si présentes ; VM et run obligatoires (RG59, Q20) |
| sauvegarde observée | backup_id, backup_created_at | texte à confirmer, datetime à confirmer | identité et date source si présentes ; VM et run obligatoires (RG59, Q20) |
| collecte des sauvegardes | backup_collection_status | énumération à confirmer | état par VM et run, distingue absence prouvée et collecte indisponible (RG72, Q20) |
| résultat consolidé | match_confidence, match_doubt | entier 0–100, booléen | score et doute flous ; critères non fixés (RG61, Q21) |
| réseau observé | network_prefix, ptr_name | textes à confirmer | préfixe et PTR observés dans le run si disponibles (RG62–RG65, Q22) |
| VM observée | disk_usage_percent, ram_usage_percent, vm_stopped_since | décimaux, datetime à confirmer | valeurs par VM et run ; absences non assimilées à zéro (RG68–RG71, Q23–Q24) |
| capacité par nœud | node_capacity_run_id, node_capacity_name, node_capacity_measured_at, node_capacity_value, node_capacity_limit | entier, texte, datetime, décimaux à confirmer | série par run et nœud ; ressources, unités et limite non définies (RG73–RG74, Q25) |
| anomalie observée | anomaly_code_v3 | énumération à confirmer | catégories réseau, conformité, capacité, zombie, absence de sauvegarde et snapshot oublié ; historique intact (RG60, RG62–RG72, RG76, Q26) |

Migration SQLite depuis v2.3 : préserver les lignes et les sept codes existants ; les nouvelles données d’observation restent facultatives pour les anciens runs, sans zéro ou score fictif. Les nouvelles entités de snapshots, sauvegardes et capacité référencent le run et la VM de ce run lorsqu’applicable ; une mise à jour en place du dernier état ne remplace jamais la série historique. Les codes nouveaux exigent une évolution cohérente des contraintes SQL et ORM et des exceptions par code (RG37–RG38, RG75–RG76). Aucun connecteur réseau, appel DNS réel, algorithme de matching ou prévision n’est prescrit par le dictionnaire. Les sources simulées restent la frontière d’exécution (§15).

## Correspondance physique v3.0

| table | colonnes ajoutées (type SQLite) | nullabilité et contraintes |
| --- | --- | --- |
| asset | disk_usage_percent REAL, ram_usage_percent REAL | NULL ; CHECK 0 à 100 si renseigné ; occupation source et non capacité allouée (Q23) |
| asset | vm_stopped_since TEXT | NULL ; instant source ISO 8601 si connu (Q24) |
| asset | backup_collection_status TEXT | NULL ; CHECK inconnue, réussie, échouée ; NULL historique non renseigné (Q20) |
| ipam_record | network_prefix TEXT, ptr_name TEXT | NULL ; observations réseau sources éventuelles (Q22) |
| consolidated_asset | match_confidence INTEGER | NULL ; CHECK 0 à 100, NULL sans score calculable (Q21) |
| consolidated_asset | match_doubt INTEGER | NULL ; CHECK 0 ou 1, NULL sans seuil défini (Q21) |
| anomaly, anomaly_exception | code TEXT | NON NULL ; sept codes historiques plus IP_OUT_OF_PREFIX, VM_IP_CONFLICT, DNS_MISSING_PTR, PTR_MISMATCH, MISSING_REQUIRED_TAG, MISSING_TENANT, MISSING_SITE, DISK_OVER_90, RAM_SATURATED, VM_UNDERUSED, VM_ZOMBIE, NO_RECENT_BACKUP, FORGOTTEN_SNAPSHOT (Q26) |
| anomaly | severity TEXT | NULL ; CHECK info, mineure, majeure, critique si classification connue ; valeurs historiques conservées (Q10, Q26) |
| snapshot_observation | id INTEGER, run_id INTEGER, asset_id INTEGER, snapshot_id TEXT, snapshot_created_at TEXT | PK id ; run_id et asset_id NON NULL ; FK run et FK composite (asset_id, run_id), CASCADE ; sources NULL si inconnues (Q20) |
| backup_observation | id INTEGER, run_id INTEGER, asset_id INTEGER, backup_id TEXT, backup_created_at TEXT | PK id ; run_id et asset_id NON NULL ; FK run et FK composite (asset_id, run_id), CASCADE ; sources NULL si inconnues (Q20) |
| node_capacity_observation | id INTEGER, run_id INTEGER, node_capacity_name TEXT, node_capacity_measured_at TEXT, node_capacity_value REAL, node_capacity_limit REAL | PK id ; run_id et nom NON NULL ; nom non vide ; date et mesures NULL si indisponibles (Q25) |

Les trois tables d'observation nouvelles sont vides à la migration. Les index sont détaillés dans le MLD ; les colonnes texte libres ne sont pas indexées. les évolutions applicatives ultérieures consomment ces attributs sans déduire une absence de NULL ni fabriquer seuil ou sévérité. La migration élargit les CHECK sur les deux tables de codes en préservant les identifiants, les données et les index ; l'ORM reste à synchroniser avant déploiement.
