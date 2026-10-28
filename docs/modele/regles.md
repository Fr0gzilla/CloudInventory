# CloudInventory v2.2 — règles de gestion

## Règles de gestion
RG01 [structure] — Un run doit conserver son statut, ses dates, ses quatre compteurs de correspondance et son éventuel message d’erreur. (§6.1)
RG02 [structure] — Chaque observation d’une VM ou d’un CT doit conserver son identité, ses attributs et ses métriques pour son run. (§6.1, §6.3)
RG03 [structure] — Chaque observation IPAM doit conserver ses attributs, son statut et sa zone pour son run, même en l’absence de VM correspondante. (§6.1, §6.3, §13.2)
RG04 [structure] — Chaque résultat consolidé doit appartenir à un seul run et référencer une VM observée dans ce run. (§6.1, §8.5)
RG05 [structure] — Chaque anomalie doit appartenir à un seul run et pouvoir référencer une VM et/ou un enregistrement IPAM si applicable. (§6.1)
RG06 [contrainte] — Les six codes d’anomalie initiaux doivent rester NO_MATCH, MATCHED_IP, HOSTNAME_MISMATCH, STATUS_MISMATCH, DUPLICATE_DNS et DUPLICATE_IP. (§6.2, §18 EV12)
RG07 [contrainte] — Les résultats d’un run ne doivent jamais écraser ceux d’un autre run. (§6.3)
RG08 [contrainte] — Des enregistrements IPAM de même IP ou de même nom DNS doivent pouvoir coexister dans un run. (§6.3, §7.4)
RG09 [comportement] — Le rapprochement doit retenir la première correspondance dans l’ordre nom, FQDN réduit au premier composant, IP, puis aucune. (§7.1)
RG10 [contrainte] — La comparaison des noms doit ignorer casse, espaces et suffixe de domaine. (§7.2)
RG11 [comportement] — Une correspondance uniquement par IP doit produire l’anomalie MATCHED_IP. (§6.2, §7.1)
RG12 [comportement] — Une VM rapprochée par IP avec un nom différent du DNS IPAM doit produire HOSTNAME_MISMATCH. (§6.2, §7.1)
RG13 [comportement] — Une VM arrêtée associée à une IP active doit produire STATUS_MISMATCH. (§6.2, §7.1)
RG14 [comportement] — Une VM sans correspondance doit produire NO_MATCH et conserver uniquement les données de virtualisation dans son résultat. (§7.1)
RG15 [comportement] — Le rôle doit être déduit prioritairement de la lettre de convention de nommage selon les 21 correspondances du §7.3. (§7.3)
RG16 [comportement] — À défaut de convention reconnue, le rôle doit être déduit du tag role:xxx, puis devenir « Indéterminé » si aucun tag exploitable n’existe. (§7.3)
RG17 [comportement] — Plusieurs noms DNS normalisés identiques dans l’IPAM d’un run doivent produire DUPLICATE_DNS. (§7.4)
RG18 [comportement] — Plusieurs IP identiques dans l’IPAM d’un run doivent produire DUPLICATE_IP. (§7.4)
RG19 [comportement] — Un run déclenché doit commencer RUNNING avec sa date de début et finir SUCCESS avec sa date de fin et ses quatre compteurs en cas de réussite. (§8.2)
RG20 [comportement] — Une erreur du pipeline doit annuler les données collectées et enregistrer FAIL avec son message d’erreur. (§8.2)
RG21 [contrainte] — L’identité vm_id et le couple ip + dns_name doivent servir au rapprochement des observations d’une même source sans empêcher la conservation d’observations identiques issues de runs différents. (§8.2, §6.3)
RG22 [comportement] — La comparaison de deux runs doit identifier les VM apparues, disparues et changées d’IP, DNS, statut ou correspondance à partir des instantanés de chaque run. (§8.5, §8.6)
RG23 [comportement] — La purge doit conserver les N runs les plus récents, avec N égal à 30 par défaut. (§8.8)
RG24 [structure] — Le statut IPAM et la zone IPAM doivent être conservés dans l’observation de chaque run. (§13.2, §6.3)
RG25 [comportement] — Un run doit collecter les VM et CT de la virtualisation avant les enregistrements IPAM. (§8.2)
RG26 [comportement] — Un run doit rapprocher les observations par vm_id et par couple IP + nom DNS avant de consolider les correspondances, les rôles et les anomalies associées. (§8.2)
RG27 [comportement] — Un run doit détecter les doublons DNS et IP après la consolidation et avant sa finalisation. (§7.4, §8.2)
RG28 [contrainte] — Les sept étapes du pipeline d’un run doivent former une seule transaction dont les données collectées sont annulées en cas d’exception. (§8.2)
RG29 [comportement] — La connexion web doit ouvrir une session signée pour le compte administrateur unique dont le mot de passe est conservé haché. (§10.1)
RG30 [contrainte] — Toute route web autre que la connexion doit nécessiter une session authentifiée et rediriger vers /login en son absence. (§10.1)
RG31 [comportement] — La connexion API doit recevoir username et password et renvoyer access_token. (§9.2)
RG32 [contrainte] — Toute route API autre que /api/login doit exiger un jeton Bearer valide. (§9.2, §10.2)
RG33 [contrainte] — Les tentatives de connexion doivent être limitées. (§10, rubrique « Sécurité »)
RG34 [contrainte] — Toute redirection next doit rester sur une URL interne. (§10, rubrique « Sécurité »)
RG35 [structure] — Chaque observation IPAM doit conserver les custom fields NetBox reçus pour son run, y compris lorsqu’aucune VM ne lui correspond. (§16, EV07 ; RG03, RG07)
RG36 [comportement] — L’exploration des observations IPAM doit permettre de filtrer par meta_zone sans confondre les runs. (§16, EV07 ; RG03, RG07)
RG37 [contrainte] — Une évolution de la base doit préserver les données existantes sans recréer la base. (§23, EV59)
RG38 [contrainte] — La description SQL du schéma et le modèle ORM doivent rester cohérents à chaque évolution du schéma. (§23, EV59)
RG39 [comportement] — Deux observations successives de même vm_id mais de vm_name différent doivent produire une anomalie RENAMED dans le run le plus récent. (§18 EV12 ; RG07)
RG40 [contrainte] — Les codes d’anomalie autorisés doivent comprendre les six codes de RG06 et RENAMED, sans autre code. (§6.2, §18 EV12)
RG41 [comportement] — Une anomalie nouvellement constatée doit être ouverte et pouvoir passer à acquittée puis à résolue. (§18 EV14)
RG42 [structure] — Le suivi d’une anomalie doit conserver son état entre runs sans écraser les constats historiques rattachés à chaque run. (§18 EV14 ; RG05, RG07)
RG43 [contrainte] — Une anomalie doit porter une sévérité parmi info, mineure, majeure et critique, déterminée par son code. (§18 EV16)
RG44 [structure] — Chaque exception doit préciser un code d’anomalie, une cible, une justification, une date d’expiration et un auteur. (§18 EV15)
RG45 [comportement] — Une exception ne doit écarter un constat que si son code et sa cible correspondent et que sa date d’expiration n’est pas dépassée. (§18 EV15)
RG46 [comportement] — Un run lancé doit pouvoir progresser sans immobiliser la requête de lancement, avec une progression consultable pour chacune des sept étapes de RG25–RG28. (§20 EV30 ; §8.2)
RG47 [comportement] — L’administrateur doit pouvoir planifier depuis l’interface des runs à cadence quotidienne ou horaire. (§20 EV32)
RG48 [structure] — Chaque run planifié doit conserver sa provenance planifiée et sa progression propre sans écraser les runs précédents. (§20 EV30, EV32 ; RG07)

## Dictionnaire des données
Les longueurs non prescrites sont indiquées « non précisée » ; les types conceptuels n’imposent aucun format d’interface.

| nom | type | longueur | contraintes | source |
| --- | --- | --- | --- | --- |
| run_id | entier | — | identifiant candidat du run | §6.1 |
| run_status | énumération{RUNNING,SUCCESS,FAIL} | — | obligatoire | §6.1, §8.2 |
| started_at | datetime | — | obligatoire | §8.2 |
| finished_at | datetime | — | présent si SUCCESS | §8.2 |
| matched_name_count | entier | — | compteur ≥ 0 | §6.1, §7.1 |
| matched_fqdn_count | entier | — | compteur ≥ 0 | §6.1, §7.1 |
| matched_ip_count | entier | — | compteur ≥ 0 | §6.1, §7.1 |
| no_match_count | entier | — | compteur ≥ 0 | §6.1, §7.1 |
| error_message | texte | non précisée | présent si FAIL | §8.2 |
| username | texte | non précisée | identifiant du compte administrateur unique | §9.2, §10.1 |
| password | texte | non précisée | reçu à la connexion, non conservé en clair | §9.2, §10.1 |
| access_token | texte | non précisée | émis à la connexion API, requis comme Bearer | §9.2 |
| asset_id | entier | — | identifiant candidat de l’observation | §6.1, §6.3 |
| asset_run_id | entier | — | obligatoire, référence run_id | §6.3, §8.5 |
| vm_id | texte (à confirmer) | non précisée | obligatoire, identité source par run ; Q1 | §6.1, §8.2 |
| vm_name | texte | non précisée | recherche, export | §6.1, §8.3 |
| fqdn | texte | non précisée | rapprochement, export | §6.1, §7.1 |
| ip_reported | texte | non précisée | rapprochement, comparaison, export | §6.1, §8.6 |
| node | texte | non précisée | filtre, tri, export | §6.1, §8.3 |
| asset_type | énumération{qemu,lxc} | — | filtre, export | §13.1, §8.3 |
| asset_status | texte (à confirmer) | non précisée | filtre, comparaison ; Q2 | §6.1, §8.6 |
| asset_tags | texte (à confirmer) | non précisée | filtre, rôle, export ; Q3 | §6.1, §7.3 |
| asset_role | texte | non précisée | calculé selon RG15–RG16, export | §7.3, §8.10 |
| os | texte | non précisée | affichage, export | §6.1, §8.10 |
| annotation | texte | non précisée | affichage | §6.1, §13.1 |
| cpu | décimal (précision à confirmer) | — | affichage, export ; Q4 | §6.1, §8.7 |
| ram | décimal (précision à confirmer) | — | affichage, export ; Q4 | §6.1, §8.7 |
| disk | décimal (précision à confirmer) | — | affichage, export ; Q4 | §6.1, §8.7 |
| uptime | décimal (précision à confirmer) | — | affichage ; Q4 | §6.1, §8.7 |
| ipam_record_id | entier | — | identifiant candidat, sans unicité IP ni DNS | §6.3 |
| ipam_run_id | entier | — | obligatoire, référence run_id | §6.3 |
| ipam_ip | texte | non précisée | doublons permis | §6.1, §7.4 |
| dns_name | texte | non précisée | doublons permis | §6.1, §7.4 |
| ipam_status | texte (à confirmer) | non précisée | affichage, comparaison ; Q2 | §13.2 |
| tenant | texte | non précisée | affichage, export | §6.1, §8.10 |
| site | texte | non précisée | affichage, export | §6.1, §8.10 |
| ipam_tags | texte (à confirmer) | non précisée | affichage, export ; Q3 | §6.1 |
| duplicate_dns | booléen | — | calculé après consolidation | §6.1, §7.4 |
| duplicate_ip | booléen | — | calculé après consolidation | §6.1, §7.4 |
| meta_zone | texte | non précisée | conservée même sans VM | §13.2 |
| netbox_custom_fields | objet de valeurs (forme à confirmer) | non précisée | facultatif, instantané intégral par observation et par run ; Q8 | §16, EV07 ; RG35 |
| consolidated_asset_id | entier | — | identifiant candidat | §6.1 |
| consolidated_run_id | entier | — | obligatoire, référence run_id | §6.1, §8.5 |
| consolidated_asset_ref | entier | — | obligatoire, référence asset_id du même run | §6.1, §8.5 |
| consolidated_ipam_ref | entier | — | facultatif, référence ipam_record_id du même run | §6.3 |
| ip_final | texte | non précisée | instantané, comparaison, export | §6.1, §8.6 |
| dns_final | texte | non précisée | instantané, comparaison, export | §6.1, §8.6 |
| status_final | texte (à confirmer) | non précisée | instantané, comparaison ; Q2 | §6.1, §8.6 |
| match_status | énumération{MATCHED_NAME,MATCHED_FQDN,MATCHED_IP,NO_MATCH} | — | obligatoire, comparaison, filtre, export | §7.1, §8.6 |
| consolidated_role | texte | non précisée | instantané, export | §7.3, §8.10 |
| anomaly_id | entier | — | identifiant candidat | §6.1 |
| anomaly_run_id | entier | — | obligatoire, référence run_id | §6.1 |
| anomaly_asset_ref | entier | — | facultatif, référence asset_id du même run | §6.1 |
| anomaly_ipam_ref | entier | — | facultatif, référence ipam_record_id du même run | §6.1 |
| anomaly_code | énumération{NO_MATCH,MATCHED_IP,HOSTNAME_MISMATCH,STATUS_MISMATCH,DUPLICATE_DNS,DUPLICATE_IP,RENAMED} | — | obligatoire | §6.2, §18 EV12 |
| anomaly_description | texte | non précisée | obligatoire | §6.1 |
| anomaly_date | datetime | — | obligatoire | §6.1 |
| anomaly_state | énumération{ouverte,acquittée,résolue} | — | obligatoire, état courant du suivi ; constats par run inchangés | §18 EV14 |
| anomaly_state_changed_at | datetime | — | date du dernier changement ; mesure de durée à préciser, Q9 | §18 EV14 |
| anomaly_severity | énumération{info,mineure,majeure,critique} | — | obligatoire, sévérité par code ; correspondance Q10 | §18 EV16 |
| exception_id | entier | — | identifiant candidat | §18 EV15 |
| exception_code | énumération{NO_MATCH,MATCHED_IP,HOSTNAME_MISMATCH,STATUS_MISMATCH,DUPLICATE_DNS,DUPLICATE_IP,RENAMED} | — | obligatoire | §18 EV15, EV12 |
| exception_target | texte | non précisée | obligatoire, cible stable selon le code ; Q11 | §18 EV15 |
| exception_justification | texte | non précisée | obligatoire, non vide | §18 EV15 |
| exception_expires_at | datetime | — | obligatoire, expiration | §18 EV15 |
| exception_author | texte | non précisée | obligatoire, auteur identifiable ; Q12 | §18 EV15 |
| anomaly_tracking_key | texte (à confirmer) | non précisée | rapprochement inter-runs sans écrasement ; Q9 | §18 EV14 ; RG07 |
| run_current_step | entier | — | facultatif avant première étape, domaine 1 à 7 ; nom des étapes §8.2 | §20 EV30, §8.2 |
| run_completed_steps | entier | — | de 0 à 7, progression consultable ; résultat final Q13 | §20 EV30 |
| run_trigger | énumération{manuel,planifié} | — | obligatoire pour les nouveaux runs ; valeur des anciens Q14 | §20 EV32 |
| schedule_id | entier | — | identifiant candidat | §20 EV32 |
| schedule_frequency | énumération{horaire,quotidien} | — | obligatoire | §20 EV32 |
| schedule_enabled | booléen | — | activation selon choix technique ; Q15 | §20 EV32 |
| schedule_next_at | datetime | — | prochain déclenchement calculé ; fuseau Q15 | §20 EV32 |
| run_schedule_ref | entier | — | facultatif, référence schedule_id lorsque planifié | §20 EV32 |

## Acteurs
- Administrateur : déclenche et consulte les inventaires, comparaisons, exports et purges (§8.1–§8.10).
- Planificateur externe : déclenche les runs configurés sans intervention humaine (§20 EV32).
- Source de virtualisation : fournit les VM et CT (§8.2).
- Source IPAM : fournit les enregistrements réseau (§8.2).
- Service de notification : reçoit les alertes configurées après succès (§8.9).

## Cas d'utilisation candidats
- Lancer un inventaire — Administrateur — RG19–RG20.
- Consulter le tableau de bord — Administrateur — RG1, RG6.
- Explorer et exporter l’inventaire — Administrateur — RG2–RG4, RG9.
- Filtrer les observations IPAM par zone — Administrateur — RG36.
- Consulter les runs et leurs anomalies — Administrateur — RG5–RG7.
- Comparer deux runs — Administrateur — RG7, RG22.
- Consulter la fiche et les métriques d’une VM — Administrateur — RG2, RG7.
- Purger les runs anciens — Administrateur — RG23.
- Fournir les observations VM/CT — Source de virtualisation — RG2.
- Fournir les observations IPAM — Source IPAM — RG3, RG8.
- Recevoir une notification de succès — Service de notification — RG19.
- Se connecter à l’interface web — Administrateur — RG29–RG30, RG33–RG34.
- Obtenir un jeton API — Administrateur — RG31–RG33.
- Accéder aux routes API protégées — Administrateur — RG32.
- Détecter un renommage entre runs — Administrateur — RG39–RG40.
- Acquitter ou résoudre une anomalie — Administrateur — RG41–RG42.
- Gérer les exceptions justifiées — Administrateur — RG44–RG45.
- Consulter et trier les anomalies par sévérité — Administrateur — RG43.
- Lancer et suivre un run asynchrone — Administrateur — RG46.
- Configurer une planification horaire ou quotidienne — Administrateur — RG47.
- Déclencher un run planifié — Planificateur externe — RG47–RG48.

## Questions ouvertes
- Q1 — RG2, RG21 — Quel est le type exact de vm_id ? Par défaut, conserver sans conversion son identité textuelle source ; vérifier avant de fixer l’unicité dans un même run.
- Q2 — RG9, RG13, RG22 — Quels vocabulaires définissent les statuts VM, IPAM et consolidé ? Par défaut, conserver les valeurs source et signaler celles qui ne permettent pas de qualifier « arrêtée » et « active ».
- Q3 — RG16 — Quel encodage et quelle cardinalité pour les tags VM et IPAM ? Par défaut, préserver intégralement leur forme source et analyser role:xxx uniquement si présent.
- Q4 — RG2 — Quelles unités et précisions pour CPU, RAM, disque et uptime ? Par défaut, préserver valeurs et unités sources sans conversion implicite.
- Q5 — RG19 — Quels sont précisément les quatre compteurs si des correspondances multiples existent ? Par défaut, compter une seule catégorie de rapprochement par VM, suivant RG9.
- Q6 — RG17, RG18 — Les anomalies de doublons portent-elles sur chaque enregistrement concerné ou une seule fois par groupe ? Par défaut, conserver tous les enregistrements et produire une anomalie par groupe, sans en perdre les membres.
- Q7 — RG29 — Quel mécanisme de session et de stockage du compte administrateur utiliser au-delà des exigences du §10.1 ? Par défaut, ne pas créer d’entité supplémentaire parmi les cinq entités du §6.1 et réserver l’authentification au compte unique configuré.
- Q8 — RG35 — Quels types et quelles clés autorise NetBox pour ses custom fields ? Par défaut, conserver l’objet JSON complet sans supposer de clés supplémentaires ; une valeur absente reste absente et est signalée, sans remplacer les données sources.
- Q9 — RG41–RG42 — Quelle identité relie les constats d’une anomalie entre runs et quelle durée mesure-t-on ? Par défaut, rattacher le suivi à code + cible stable, garder chaque constat historique et dater les transitions ; ne pas fusionner les cibles ambiguës.
- Q10 — RG43 — Quelle sévérité attribuer à chaque code ? Par défaut, laisser la correspondance configurable et signaler les codes non classés plutôt que leur inventer une gravité.
- Q11 — RG44–RG45 — Quel format définit la cible d’exception pour chaque code, notamment les doublons ? Par défaut, cibler un identifiant stable explicite, conserver les groupes ambigus sans exception automatique.
- Q12 — RG44 — Comment identifier l’auteur avant les comptes multiples ? Par défaut, enregistrer l’identifiant du compte administrateur existant ; ne pas fabriquer de personne.
- Q13 — RG46 — Comment représenter la progression après rollback d’un run échoué ? Par défaut, conserver la dernière étape atteinte comme information de suivi, sans persister les observations annulées.
- Q14 — RG48 — Comment qualifier les anciens runs sans provenance enregistrée ? Par défaut, provenance inconnue laissée vide, jamais imputée au planificateur.
- Q15 — RG47 — Quel fuseau, horaire exact, gestion de l’activation et des déclenchements manqués ? Par défaut, cadence enregistrée, fuseau explicite avant activation ; ne jamais déclencher rétroactivement plusieurs runs sans décision.
