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
| current_step | INTEGER | oui | NULL | CHECK 1 à 7 ; dernière étape atteinte |
| completed_steps | INTEGER | oui | NULL | CHECK 0 à 7 ; anciens runs inconnus |
| run_trigger | TEXT | oui | NULL | CHECK manuel, planifié ; anciens runs inconnus |
| schedule_id | INTEGER | oui | NULL | planification éventuelle |

- PK : id (INTEGER PRIMARY KEY).
- FK : schedule_id → run_schedule(id) ON DELETE SET NULL.
- Index : PK implicite ; idx_run_schedule (schedule_id) pour la provenance planifiée ; la purge des runs récents utilise id décroissant.
- Origine : RUN, declencher — RG1, RG7, RG19, RG20, RG23, RG46, RG48.

### asset

| colonne | type | null | défaut | contrainte |
| --- | --- | --- | --- | --- |
| id | INTEGER | non | — | identité d'observation |
| run_id | INTEGER | non | — | appartenance au run |
| vm_id | TEXT | non | — | identité source ; pas d'unicité intra-run confirmée |
| virtual_source | TEXT | oui | NULL | origine source, inconnue pour anciens runs ; identité qualifiée avec vm_id |
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
| disk_usage_percent | REAL | oui | NULL | CHECK 0 à 100 ; occupation source, non capacité allouée |
| ram_usage_percent | REAL | oui | NULL | CHECK 0 à 100 ; occupation source |
| vm_stopped_since | TEXT | oui | NULL | date source si disponible |
| backup_collection_status | TEXT | oui | NULL | CHECK inconnue, réussie, échouée ; NULL historique inconnu |

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
| ipam_source | TEXT | oui | NULL | origine IPAM, inconnue pour anciens runs |
| dns_name | TEXT | oui | NULL | doublons permis |
| status | TEXT | oui | NULL | statut source |
| tenant | TEXT | oui | NULL | locataire source |
| site | TEXT | oui | NULL | site source |
| tags | TEXT | oui | NULL | forme source à confirmer |
| duplicate_dns | INTEGER | non | 0 | CHECK (0, 1) |
| duplicate_ip | INTEGER | non | 0 | CHECK (0, 1) |
| meta_zone | TEXT | oui | NULL | zone source même sans VM |
| custom_fields | TEXT | oui | NULL | objet NetBox complet sérialisé en JSON ; NULL si absent, objet vide distinct |
| network_prefix | TEXT | oui | NULL | préfixe source si disponible |
| ptr_name | TEXT | oui | NULL | réponse PTR source si disponible |

- PK : id ; UNIQUE(id, run_id) pour les références composites ; aucune unicité de ip ou dns_name.
- FK : run_id → run(id) ON DELETE CASCADE.
- Index : idx_ipam_run_ip (run_id, ip) ; idx_ipam_run_dns (run_id, dns_name), pour rapprochement et détection de doublons ; idx_ipam_run_zone (run_id, meta_zone), pour filtrer par zone dans un run ; tous servent aussi la FK run_id. Aucun index sur le JSON libre.
- Origine : IPAM_RECORD, observer_ipam — RG3, RG7, RG8, RG9, RG17, RG18, RG24, RG35, RG36.

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
| match_status | TEXT | non | — | CHECK MATCHED_NAME, MATCHED_FQDN, MATCHED_IP, MATCHED_FUZZY, NO_MATCH ; statut flou distinct des correspondances exactes (RG80) |
| role | TEXT | oui | NULL | rôle instantané |
| match_confidence | INTEGER | oui | NULL | CHECK 0 à 100 ; score non inventé |
| match_doubt | INTEGER | oui | NULL | CHECK 0, 1 ; doute non déduit sans seuil |

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
| asset_id | INTEGER | oui | NULL | VM du même run ; obligatoire si code = MATCH_CONFIDENCE_DOUBTFUL |
| ipam_record_id | INTEGER | oui | NULL | IPAM facultatif du même run |
| code | TEXT | non | — | CHECK sept codes historiques, MATCH_CONFIDENCE_DOUBTFUL et IP_OUT_OF_PREFIX, VM_IP_CONFLICT, DNS_MISSING_PTR, PTR_MISMATCH, MISSING_REQUIRED_TAG, MISSING_TENANT, MISSING_SITE, DISK_OVER_90, RAM_SATURATED, VM_UNDERUSED, VM_ZOMBIE, NO_RECENT_BACKUP, FORGOTTEN_SNAPSHOT ; code douteux : asset_id du même run obligatoire (RG77) |
| description | TEXT | non | — | détail du constat |
| created_at | TEXT | non | — | date ISO 8601 |
| tracking_key | TEXT | oui | NULL | cible de suivi entre runs, sans fusion des constats |
| state | TEXT | non | ouverte | CHECK ouverte, acquittée, résolue |
| state_changed_at | TEXT | oui | NULL | dernière transition datée |
| severity | TEXT | oui | NULL | CHECK info, mineure, majeure, critique ; NULL obligatoire si code = MATCH_CONFIDENCE_DOUBTFUL |

- PK : id.
- FK : run_id → run(id) ON DELETE CASCADE.
- FK : (asset_id, run_id) → asset(id, run_id) ON DELETE RESTRICT.
- FK : (ipam_record_id, run_id) → ipam_record(id, run_id) ON DELETE RESTRICT.
- CHECK : `code <> 'MATCH_CONFIDENCE_DOUBTFUL' OR (asset_id IS NOT NULL AND severity IS NULL)` ; la FK composite garantit le même run.
- Index : idx_anomaly_run (run_id) pour la consultation ; idx_anomaly_asset_run (asset_id, run_id) ; idx_anomaly_ipam_run (ipam_record_id, run_id) pour les FK et les fiches liées ; idx_anomaly_tracking (tracking_key, created_at) pour le suivi historique ; idx_anomaly_severity (severity) pour le tri.
- Origine : ANOMALY, constater, concerner_vm, concerner_ipam — RG5, RG6, RG11, RG12, RG13, RG17, RG18, RG39–RG43, RG77–RG79.

### anomaly_exception

| colonne | type | null | défaut | contrainte |
| --- | --- | --- | --- | --- |
| id | INTEGER | non | — | identité de l'exception |
| code | TEXT | non | — | CHECK sept codes historiques, MATCH_CONFIDENCE_DOUBTFUL et treize candidats v3 (même domaine que anomaly.code) |
| target | TEXT | non | — | CHECK non vide après trim |
| justification | TEXT | non | — | CHECK non vide après trim |
| expires_at | TEXT | non | — | date ISO 8601 |
| author | TEXT | non | — | CHECK non vide après trim |

- PK : id.
- FK : aucune ; application conditionnelle par code, cible et expiration, sans association persistante.
- Index : PK implicite ; aucun filtre SQL imposé pour la cible libre.
- Origine : ANOMALY_EXCEPTION — RG44, RG45, RG78.

### run_schedule

| colonne | type | null | défaut | contrainte |
| --- | --- | --- | --- | --- |
| id | INTEGER | non | — | identité de planification |
| frequency | TEXT | non | — | CHECK horaire, quotidien |
| enabled | INTEGER | non | 0 | CHECK 0, 1 |
| next_at | TEXT | oui | NULL | prochain déclenchement, date ISO 8601 |

- PK : id.
- FK : aucune.
- Index : PK implicite ; aucune recherche par cadence prescrite.
- Origine : RUN_SCHEDULE, declencher — RG47, RG48.

### auth_origin

| colonne | type | null | défaut | contrainte |
| --- | --- | --- | --- | --- |
| origin_code | TEXT | non | — | PK ; CHECK local, LDAP, AD |

- PK : origin_code.
- FK : aucune.
- Index : PK implicite.
- Origine : AUTH_ORIGIN — RG53–RG56.

### role

| colonne | type | null | défaut | contrainte |
| --- | --- | --- | --- | --- |
| role_code | TEXT | non | — | PK ; CHECK admin, opérateur, lecteur |

- PK : role_code.
- FK : aucune.
- Index : PK implicite.
- Origine : ROLE — RG52, RG53.

### permission

| colonne | type | null | défaut | contrainte |
| --- | --- | --- | --- | --- |
| permission_code | TEXT | non | — | PK ; CHECK non vide |

- PK : permission_code.
- FK : aucune.
- Index : PK implicite.
- Origine : PERMISSION — RG52, Q18.

### account

| colonne | type | null | défaut | contrainte |
| --- | --- | --- | --- | --- |
| id | INTEGER | non | — | identité du compte |
| origin_code | TEXT | non | — | origine de connexion |
| role_code | TEXT | non | — | rôle attribué |
| username | TEXT | non | — | CHECK non vide ; UNIQUE(origin_code, username) |
| local_password_hash | TEXT | oui | NULL | obligatoire et non vide si local ; NULL si LDAP/AD |

- PK : id ; UNIQUE(origin_code, username) qualifie l'identité sans rapprocher les annuaires.
- FK : origin_code → auth_origin(origin_code) ON DELETE RESTRICT.
- FK : role_code → role(role_code) ON DELETE RESTRICT.
- Index : UNIQUE(origin_code, username) pour la connexion ; idx_account_role(role_code) pour la FK.
- Origine : ACCOUNT, authentifier, attribuer_role — RG52–RG56.

### role_permission

| colonne | type | null | défaut | contrainte |
| --- | --- | --- | --- | --- |
| id | INTEGER | non | — | identité de l'attribution |
| role_code | TEXT | non | — | UNIQUE(role_code, permission_code) |
| permission_code | TEXT | non | — | permission explicite |

- PK : id ; UNIQUE(role_code, permission_code).
- FK : role_code → role(role_code) ON DELETE CASCADE.
- FK : permission_code → permission(permission_code) ON DELETE CASCADE.
- Index : UNIQUE(role_code, permission_code) pour les droits du rôle ; idx_role_permission_permission(permission_code) pour la FK.
- Origine : ROLE_PERMISSION, autoriser_role, definir_permission — RG52, Q18.

### audit

| colonne | type | null | défaut | contrainte |
| --- | --- | --- | --- | --- |
| id | INTEGER | non | — | identité de la trace |
| account_id | INTEGER | oui | NULL | compte facultatif, peut être supprimé |
| actor_identity | TEXT | non | — | CHECK non vide ; auteur historique |
| action | TEXT | non | — | CHECK non vide ; famille d'action conservée |
| created_at | TEXT | non | — | date ISO 8601 |
| target | TEXT | non | — | CHECK non vide ; cible historique |

- PK : id.
- FK : account_id → account(id) ON DELETE SET NULL.
- Index : idx_audit_account(account_id) pour la FK ; idx_audit_date(created_at) pour la consultation chronologique.
- Origine : AUDIT, identifier_auteur — RG57, RG58, Q19.

### snapshot_observation

| colonne | type | null | défaut | contrainte |
| --- | --- | --- | --- | --- |
| id | INTEGER | non | — | identité interne, distincte de la source |
| run_id | INTEGER | non | — | run de collecte |
| asset_id | INTEGER | non | — | VM du même run |
| snapshot_id | TEXT | oui | NULL | identité source éventuelle |
| snapshot_created_at | TEXT | oui | NULL | date source éventuelle |

- PK : id.
- FK : run_id → run(id) ON DELETE CASCADE.
- FK : (asset_id, run_id) → asset(id, run_id) ON DELETE CASCADE.
- Index : idx_snapshot_run (run_id) ; idx_snapshot_asset_run (asset_id, run_id) pour consulter les snapshots d'une VM par run.
- Origine : SNAPSHOT_OBSERVATION, observer_snapshot, concerner_snapshot — RG59, RG60, RG75.

### backup_observation

| colonne | type | null | défaut | contrainte |
| --- | --- | --- | --- | --- |
| id | INTEGER | non | — | identité interne, distincte de la source |
| run_id | INTEGER | non | — | run de collecte |
| asset_id | INTEGER | non | — | VM du même run |
| backup_id | TEXT | oui | NULL | identité source éventuelle |
| backup_created_at | TEXT | oui | NULL | date source éventuelle |

- PK : id.
- FK : run_id → run(id) ON DELETE CASCADE.
- FK : (asset_id, run_id) → asset(id, run_id) ON DELETE CASCADE.
- Index : idx_backup_run (run_id) ; idx_backup_asset_run (asset_id, run_id) pour consulter l'historique de sauvegarde d'une VM.
- Origine : BACKUP_OBSERVATION, observer_backup, concerner_backup — RG59, RG72, RG75.

### node_capacity_observation

| colonne | type | null | défaut | contrainte |
| --- | --- | --- | --- | --- |
| id | INTEGER | non | — | identité de la mesure |
| run_id | INTEGER | non | — | run de mesure |
| node_capacity_name | TEXT | non | — | CHECK non vide ; identité source à confirmer |
| node_capacity_measured_at | TEXT | oui | NULL | date source éventuelle |
| node_capacity_value | REAL | oui | NULL | valeur source, unité à confirmer |
| node_capacity_limit | REAL | oui | NULL | limite source éventuelle |

- PK : id ; plusieurs mesures par run et nœud autorisées.
- FK : run_id → run(id) ON DELETE CASCADE.
- Index : idx_node_capacity_run_name (run_id, node_capacity_name) pour les tendances par nœud et la FK.
- Origine : NODE_CAPACITY_OBSERVATION, mesurer_capacite — RG73–RG75.

## Décisions

- Seize entités → seize tables ; dix-neuf associations → dix-neuf liens (les quatorze précédents et cinq liens des trois observations v3). Aucune association n:n conceptuelle ; role_permission est l'entité d'attribution explicite. Les FK composites contrôlent le même run.
- 1FN : une colonne par propriété ; les tags restent une valeur textuelle source indivisible en attendant Q3, sans recherche de composant SQL. 2FN : chaque PK est simple ; les clés candidates composites (id, run_id) ne portent aucune dépendance partielle de données métier. 3FN : les résultats et rôles sont des instantanés calculés exigés par RG4, RG7, RG15–RG16 et RG22 ; compteurs du run et indicateurs de doublons sont des matérialisations prévues par RG1, RG17–RG19, recalculées dans le traitement du run, sans écraser les runs précédents.
- Les colonnes physiques suivent le dictionnaire publié (`id`, `run_id`, `type`, `status`, `tags`, `role`, `ip`, `code`, `created_at`) ; elles réalisent respectivement les attributs conceptuels identifiant, run, asset_type, asset_status/ipam_status/run_status, asset_tags/ipam_tags, asset_role/consolidated_role, ipam_ip, anomaly_code et anomaly_date. Le SQL n'impose aucune longueur absente des règles.
- CASCADE pour la suppression d'un run et de ses observations/résultats ; RESTRICT pour une observation encore référencée par une anomalie ou un rapprochement. Une FK composite facultative avec run_id non NULL est vérifiée dès que son identifiant de référence est présent.
- Aucun CHECK reliant match_status et référence IPAM, ni déclencheur forçant les étapes RUNNING/SUCCESS/FAIL : les règles de pipeline RG9, RG14, RG19–RG20 relèvent du traitement applicatif ; les statuts sources et les unités restent à confirmer (Q1–Q6).
- EV07 : `meta_zone TEXT NULL` existe déjà ; `custom_fields TEXT NULL` conserve l'objet JSON complet de chaque observation sans écraser un autre run (RG35), et l'index `(run_id, meta_zone)` sert le filtre borné au run (RG36). La sérialisation et la distinction entre absence et objet vide relèvent de la collecte ; aucune clé JSON supplémentaire n'est prescrite (Q8). Chaque champ non-clé dépend de l'observation identifiée par `id` : 1FN par stockage d'un document source indivisible, 2FN par PK simple, 3FN sans dérivation depuis `meta_zone` ; la zone source existante et l'objet JSON peuvent contenir la même information, conservée intentionnellement pour filtrer sans indexer le texte libre.
- RG39–RG48 : les constats restent individuels par run ; `tracking_key` sert au rapprochement inter-runs (Q9), sans clé UNIQUE ni écrasement. État courant et dernière transition datée n'archivent pas toutes les transitions (Q9) ; leur gestion et l'identité stable sont applicatives. La gravité est obligatoire pour les nouveaux constats sans valeur arbitraire par défaut ; la classification par code reste configurable (Q10). Le prédicat d'exception code + cible + expiration est applicatif (Q11–Q12). L'activation du calendrier et le fuseau restent à préciser (Q15) ; les anciens runs conservent une provenance NULL (Q14). Aucun lien obligatoire vers une exception n'est inventé.
- 1FN, 2FN et 3FN des deux nouvelles tables : propriétés atomiques et clés simples ; la fréquence et la cible n'impliquent aucune dépendance transitive. Dans run et anomaly, progression, état et gravité dépendent de leur constat identifié ; l'état et la date de transition sont conservés ensemble sans dérivation SQL. Les compteurs, rôles et consolidations matérialisés restent les écarts volontaires expliqués ci-dessus.
- v2.3 : les origines facultatives préservent les anciens instantanés sans provenance inventée ; `(virtual_source, vm_id)` qualifie le rapprochement sans imposer d'unicité intra-run (Q16). Les référentiels de rôles, d'origines et de permissions évitent les domaines implicites ; aucune permission n'est accordée par défaut (Q18). L'unicité `(origin_code, username)` est un choix technique révisable (Q17), sans assimilation LDAP/AD/local ; hash local obligatoire, jamais de hash annuaire. Audit indépendant du run : la purge garde auteur et cible en texte historique, même après suppression du compte (Q19). PK simples et attributs atomiques : 1FN, 2FN, 3FN ; l'identité historique de l'audit est une redondance volontaire imposée par RG58, non une dénormalisation par jointure recalculable.
- Migration v2.2 → v2.3 : ajouter `virtual_source` et `ipam_source` nullables par ALTER TABLE ; créer les six nouvelles tables et leurs quatre index sans remplacer les tables historiques. Aucun ancien compte n'est inféré d'un secret configuré ; créer des comptes locaux seulement avec identité, rôle et hash vérifiés ; ne jamais accorder automatiquement une permission. Ne reconstruire aucune table historique ni toucher ses CHECK ; comparer le schéma ORM et le SQL avant de déclarer RG38 satisfait.
- v3.0 : les huit attributs ajoutés aux observations et résultats sont nullables et sans valeur par défaut, pour ne fabriquer ni mesure, ni confiance, ni absence de sauvegarde (Q20–Q25). Les treize codes v3 candidats étendent les deux CHECK de code ; la sévérité devient nullable si la classification Q26 n'est pas décidée, tout en conservant les sévérités historiques et le CHECK sur les valeurs renseignées. Le code et la cible des exceptions restent soumis à RG45 côté application, sans association fictive. Aucune unicité source n'est présumée pour les observations et mesures ; leurs FK composites interdisent les croisements de runs. 1FN : les identifiants source et mesures restent atomiques ; 2FN : PK simples ; 3FN : chaque mesure dépend de son observation, sans dépendance entre mesures. Les matérialisations historiques déjà décrites restent les seuls écarts volontaires.
- RG77–RG80 : MATCHED_FUZZY distingue un résultat flou des statuts exacts et NO_MATCH ; MATCH_CONFIDENCE_DOUBTFUL étend les deux domaines de code sans retirer les valeurs historiques. Pour ce code, le CHECK impose asset_id non NULL et severity NULL ; la FK composite impose la VM du même run. La cohérence avec le résultat flou/douteux reste contrôlée par le traitement ; les autres codes gardent leurs contraintes. Sa sévérité reste NULL et signalée tant que Q26 n'est pas classée ; suivi par tracking_key, état et transitions, exception justifiée code + cible + expiration suivent RG41–RG45. Résoudre seulement sur un run ultérieur comparable où la même VM n'est plus douteuse ; absence de VM ou contrôle impossible ne résout rien (Q21). Aucun index nouveau : ceux des FK, du suivi et du filtre match_status existent. Ces attributs dépendent de leur observation identifiée (1FN–3FN inchangées).
- Migration v2.3 → v3.0 : `migration_v2_v3.sql` ajoute les colonnes nullables, reconstruit uniquement anomaly et anomaly_exception pour élargir leurs CHECK, copie leurs lignes avec identifiants et valeurs inchangés, recrée les index, puis crée trois tables d'observations vides. Exécuter sur connexion exclusive, hors transaction, avec FK désactivées uniquement pendant la transaction de reconstruction ; vérifier `foreign_key_check` et `integrity_check` après réactivation. Une base v2 plus ancienne exige d'abord sa migration vers v2.3 ; ne pas exécuter deux fois ce script. L'ORM doit être aligné avant déploiement (RG38).
- Consommation les évolutions applicatives ultérieures : Tâche applicative 1 collecte les snapshots/sauvegardes et renseigne l'état de collecte par VM ; Tâche applicative 2 conserve le score et le doute seulement lorsqu'ils sont calculables ; Tâche applicative 3 lit préfixe, PTR et mesures VM pour les anomalies conditionnelles ; Tâche applicative 4 interprète les dates d'arrêt et de sauvegarde sans conclure sur NULL ; Tâche applicative 5 lit la série `node_capacity_observation` bornée par run et nœud, sans prévoir sans historique ni limite. Les noms de codes restent candidats (Q26), aucun seuil RAM, oubli ou récence n'est imposé par le SQL.

## Ordre de création

1. run_schedule, anomaly_exception (sans FK)
2. run (après run_schedule)
3. asset, ipam_record (après run)
4. consolidated_asset, anomaly (après asset et ipam_record)
5. auth_origin, role, permission (sans FK ; peuvent aussi précéder run)
6. account (après auth_origin et role), role_permission (après role et permission)
7. audit (après account ; sans dépendance à run)
8. snapshot_observation, backup_observation (après run et asset) ; node_capacity_observation (après run).
