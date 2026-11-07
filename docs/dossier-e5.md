# Dossier de révision E5

## Sommaire

- [Contexte et périmètre E5](#contexte-et-perimetre-e5)
- [Modèle de données](#modele-de-donnees)
- [Architecture et composants](#architecture-et-composants)
- [Règles de gestion](#regles-de-gestion)
- [Sécurité et audit Argus](#securite-et-audit-argus)
- [Tests et exploitation](#tests-et-exploitation)
- [Écarts connus et réserves](#Ecarts-connus-et-reserves)


## Mission du dossier

Vous présentez CloudInventory comme une application Flask d’inventaire cloud, dotée de collecteurs simulés par défaut et d’une base SQLite persistante configurable (`README.md:1-3`). Le dossier E5 prend pour point de départ la mission et les livrables du cahier des charges § 15. Il distingue cette intention du fonctionnement attesté par le code (`app/__init__.py:329-352`, `collector/inventory_runner.py:206-228`).

Pour délimiter le périmètre, partez du cheminement d’un inventaire : une collecte virtuelle, une collecte IPAM, puis un Run conservé en base (`collector/inventory_runner.py:180-189`, `collector/inventory_runner.py:206-228`, `collector/inventory_runner.py:275-285`, `collector/inventory_runner.py:321-325`, `app/models.py:18-24`). Ce périmètre inclut les enregistrements Asset et Anomaly liés aux exécutions ; leurs champs et leurs états sont explicités dans le modèle livré (`app/models.py:48-60`, `app/models.py:152-176`). Ces éléments donnent des preuves concrètes à commenter, plutôt qu’une promesse de couverture de toute l’infrastructure (`app/models.py:18-24`, `app/models.py:48-60`, `app/models.py:152-176`).

## Conditions d’usage et frontières

Les sources de virtualisation proposées sont Proxmox et vSphere ; les sources IPAM proposées sont NetBox et EfficientIP (`collector/inventory_runner.py:206-228`). Le mode simulé est sélectionné par défaut pour ces deux familles ; le mode réel nécessite un choix explicite (`collector/inventory_runner.py:206-228`). Par exemple, sans sélection supplémentaire, un Run utilise les sources simulées Proxmox et NetBox : il ne prouve pas qu’une infrastructure réelle a été interrogée (`collector/inventory_runner.py:206-228`).

L’application fournit une interface Flask et des routes API, mais le seul code livré ne démontre pas un déploiement opérationnel dans un réseau fermé (`app/__init__.py:344-352`, `app/__init__.py:550-552`, `README.md:22`). Le profil Compose documenté sert à un usage HTTP local ; un déploiement réel exige un frontal TLS non fourni (`README.md:22`). Ainsi, vous séparez la possibilité d’une préparation en environnement hors ligne de la preuve d’une installation sur la cible (`docs/hors-ligne.md:3-7`).

## Livrable et limites vérifiables

Le projet fournit des modules applicatifs, des collecteurs, des tests et une documentation du modèle (`README.md:44-50`). La notice hors ligne décrit un paquet avec image Docker et roues Python, mais précise que ses sources applicatives ne permettent pas une installation native autonome (`docs/hors-ligne.md:3-7`). Les collecteurs fournis ne livrent pas les mesures d’occupation nécessaires à trois constats de capacité : sans source injectée, ces constats ne sont pas produits (`README.md:34`). Cette réserve évite de présenter toutes les anomalies prévues comme observables dans la démonstration standard (`README.md:34`).

> **À retenir** — Présentez CloudInventory comme l’inventaire Flask livré, avec collecteurs simulés par défaut, et non comme une collecte réelle déjà déployée (`README.md:1-3`, `collector/inventory_runner.py:206-228`). Distinguez la préparation hors ligne de l’installation effective et de ses prérequis (`docs/hors-ligne.md:3-7`, `README.md:22`).


## Des observations conservées par exécution

Vous pouvez lire le modèle à partir du `run` : il porte son statut, ses dates, ses compteurs de rapprochement et sa progression. `app/models.py:18-45` Les statuts admis sont `RUNNING`, `SUCCESS` et `FAIL`. `app/models.py:35-45`

Chaque `asset` décrit une observation de VM liée à un run : identifiant source, nom, IP, nœud et mesures restent distincts de son identifiant interne. `app/models.py:48-72` Chaque `ipam_record` conserve une observation IPAM, notamment IP, DNS, zone et objet `custom_fields`. `app/models.py:89-106` Ainsi, les index portent sur le couple run/IP ou run/DNS, sans imposer l'unicité de ces valeurs source. `app/models.py:108-115` `docs/modele/mld.md:80-82`

Par exemple, `custom_fields` est une colonne textuelle facultative ; le modèle documente un JSON source et distingue son absence d'un objet vide. `app/models.py:103-105` `docs/modele/mld.md:75-82` Les valeurs variables restent attachées à l'observation de leur run ; une valeur NULL ne supprime pas la ligne. `docs/modele/dictionnaire.md:3`

## Du rapprochement au constat

Le résultat `consolidated_asset` relie un `asset` obligatoire à un `ipam_record` facultatif dans le même run. `app/models.py:118-149` Sa clé unique `(asset_id, run_id)` limite chaque observation VM à un résultat par run. `app/models.py:139-148` Son `match_status` distingue notamment `MATCHED_NAME`, `MATCHED_FQDN`, `MATCHED_IP`, `MATCHED_FUZZY` et `NO_MATCH`. `app/models.py:133-148` Le score `match_confidence` et l'indicateur `match_doubt` peuvent rester NULL : le schéma ne fabrique pas une confiance absente. `app/models.py:130-135` `docs/modele/mld.md:98-99`

L'`anomaly` garde son code, sa description et son run, avec des liens facultatifs vers la VM et l'IPAM du même run. `app/models.py:152-183` Son état peut être `ouverte`, `acquittée` ou `résolue` ; `tracking_key` sert au suivi entre runs sans fusionner les constats. `app/models.py:162-188` `docs/modele/mld.md:119-129` Pour `MATCH_CONFIDENCE_DOUBTFUL`, le schéma impose une VM liée et une gravité NULL. `app/models.py:175-183` L'exception associe un code, une cible, une justification, une expiration et un auteur ; elle n'a pas de clé étrangère vers un constat. `app/models.py:192-213` `docs/modele/mld.md:132-145`

## Les autres entités et la portée du schéma

`run_schedule` décrit une fréquence, une activation et une date éventuelle ; `run.schedule_id` peut être absent. `app/models.py:4-15` `app/models.py:30-44` Les référentiels `auth_origin`, `role` et `permission` alimentent `account` et l'attribution explicite `role_permission`. `app/models.py:216-271` L'identité d'un compte est qualifiée par `(origin_code, username)` ; `audit` conserve l'auteur textuel, l'action, la date et la cible même si son lien au compte devient NULL. `app/models.py:240-289` `docs/modele/schema.sql:134-141`

Les observations de snapshot et sauvegarde référencent une VM du même run ; les mesures de capacité référencent leur run, tandis que le nom du nœud est un texte `node_capacity_name`. `app/models.py:293-340` Le SQL publié et l'ORM définissent ces tables ; la création au démarrage passe par `db.create_all()` et active les clés étrangères SQLite sur chaque connexion. `docs/modele/schema.sql:164-195` `app/models.py:293-340` `app/__init__.py:402-411`

Cette concordance structurelle ne démontre ni la migration des anciennes bases ni l'application automatique des règles métier. `app/__init__.py:402-411` `docs/modele/mld.md:294-295` Le document de baseline décrit encore un ancien écart entre SQL et ORM ; vérifiez plutôt les colonnes et index actuels dans les deux schémas. `docs/modele/baseline-sql-orm.md:9-11` `app/models.py:103-115` `docs/modele/schema.sql:52-69` `docs/modele/schema.sql:147-149`

> **À retenir** — Un run distingue observations VM et IPAM, résultats consolidés et anomalies ; les clés composites empêchent de rapprocher deux runs différents. `app/models.py:48-56` `app/models.py:89-95` `app/models.py:139-148` `app/models.py:175-183` Le schéma ne remplace ni une migration ni les contrôles applicatifs de cohérence. `app/__init__.py:402-411` `docs/modele/mld.md:294-295`


## Du démarrage aux interfaces

La factory Flask `create_app` prépare SQLAlchemy et initialise les modèles avant de créer les tables et de récupérer les runs interrompus (`app/__init__.py:329`, `app/__init__.py:388-414`). Elle enregistre les routes web, API et d'authentification dans des blueprints distincts (`app/__init__.py:50-52`, `app/__init__.py:548-552`). Vous pouvez distinguer leurs usages : la page d'accueil affiche les statistiques du dernier run, tandis que `/dashboard/data` expose statistiques et runs au navigateur (`app/__init__.py:55-71`). Le tableau de bord lance un run par la fonction de l'API ; la route web `/runs/<int:id>/status` permet d'en suivre l'état (`app/__init__.py:74-94`).

L'API offre une connexion par jeton Bearer, la liste des runs, leur détail et leur comparaison (`app/api.py:105-125`, `app/api.py:158-188`, `app/api.py:218-304`). La factory protège les routes API par vérification du jeton et contrôle du compte ; elle réserve les routes web aux sessions authentifiées (`app/__init__.py:489-510`). La consultation, le lancement d'un run et l'administration relèvent de listes de permissions séparées (`app/__init__.py:432-473`). Ne confondez donc pas exposition d'une route et autorisation d'y accéder (`app/__init__.py:489-516`).

## Des sources aux données persistées

Le cœur du traitement s'appuie sur `Run`, qui conserve le statut et les compteurs d'une exécution (`app/models.py:18-44`). `Asset` et `IpamRecord` conservent les observations liées au run (`app/models.py:48-55`, `app/models.py:89-100`). `ConsolidatedAsset` relie un actif à une éventuelle observation IPAM et conserve le résultat du rapprochement (`app/models.py:118-148`). `Anomaly` conserve les constats, leur état et leurs références à ces observations (`app/models.py:152-184`). Cette séparation vous permet d'expliquer pourquoi une observation brute, un résultat consolidé et une anomalie ne désignent pas la même donnée (`app/models.py:48-55`, `app/models.py:118-131`, `app/models.py:152-165`).

Le choix des sources précède la collecte : Proxmox ou vSphere pour la virtualisation, NetBox ou EfficientIP pour l'IPAM (`collector/inventory_runner.py:206-228`). Les clients réels existent, mais le traitement sélectionne les variantes simulées par défaut (`collector/inventory_runner.py:206-228`). Les modes `VIRTUALIZATION_MODE` et `IPAM_MODE` ont pour défaut `mock` ; le traitement les lit directement dans l'environnement (`collector/inventory_runner.py:206-220`). La factory configure `USE_MOCK_VIRT` et `USE_MOCK_IPAM`, mais le traitement ne consulte pas ces paramètres (`app/__init__.py:368-369`, `collector/inventory_runner.py:206-228`). Compose fixe ces paramètres sans définir les modes lus par le traitement : il ne faut donc pas présenter une collecte réseau réelle comme son fonctionnement standard (`docker-compose.yml:6-13`, `collector/inventory_runner.py:208-220`).

## Du déclenchement au résultat d'un run

La route `POST /api/runs` démarre un inventaire et renvoie un identifiant avec une réponse HTTP 202 (`app/api.py:190-216`, `app/api.py:324-327`). Le déclencheur persiste d'abord un `Run` en état `RUNNING`, puis lance le traitement dans un fil d'exécution (`collector/async_runner.py:38-60`). Un verrou empêche deux déclenchements concurrents ; l'API renvoie HTTP 409 si un run est déjà actif (`collector/async_runner.py:38-41`, `app/api.py:211-216`). Après interruption, le démarrage marque les runs restés `RUNNING` comme `FAIL` et retire leurs observations associées (`collector/async_runner.py:13-30`).

Le traitement annonce sept étapes : il collecte les actifs virtualisés, la capacité des nœuds et les observations IPAM (`collector/inventory_runner.py:200`, `collector/inventory_runner.py:280-328`). Il peut aussi recueillir des snapshots Proxmox et des sauvegardes PBS si leurs sources sont disponibles (`collector/inventory_runner.py:330-355`). Il persiste ensuite les actifs et les observations IPAM, rapproche les données, puis établit les anomalies (`collector/inventory_runner.py:357-397`, `collector/inventory_runner.py:470-545`). Un succès fixe le statut `SUCCESS` et les compteurs ; un échec annule les écritures en cours et persiste un run `FAIL` (`collector/inventory_runner.py:578-611`). Les exports et les notifications n'interviennent qu'après succès (`collector/inventory_runner.py:617-639`). Vous pouvez illustrer le flux avec une VM observée, sa ligne `Asset`, puis son rapprochement `ConsolidatedAsset` et ses constats `Anomaly` (`collector/inventory_runner.py:357-380`, `collector/inventory_runner.py:470-505`).

## Exécution locale, Compose et environnement hors ligne

Le lancement local utilise Python et une base SQLite persistante configurable ; la factory crée les tables au démarrage (`README.md:7-14`, `app/__init__.py:388-411`). Compose publie le service uniquement sur la boucle locale et conserve SQLite dans un volume nommé (`docker-compose.yml:4-15`). L'image installe les dépendances verrouillées, copie l'application et les collecteurs, puis démarre Gunicorn avec un seul worker (`Dockerfile:5-15`). Le contrôle de santé vise la page de connexion (`docker-compose.yml:21-25`).

Le paquet pour environnement hors ligne contient une image exportée et un Compose autonome ; son installation Docker charge l'image locale sans téléchargement (`docs/hors-ligne.md:3-5`). Son répertoire de roues permet une autre installation Python compatible, mais ne contient pas les sources nécessaires à une installation native autonome (`docs/hors-ligne.md:7`). Le lancement normal exige des secrets fournis hors du dépôt ; la factory refuse leurs valeurs absentes ou invalides (`README.md:14`, `app/__init__.py:335-342`).

> **À retenir** — L'application sépare les interfaces Flask, les observations persistées et le traitement d'un run ; ses collecteurs sont simulés par défaut (`app/__init__.py:50-52`, `app/models.py:18-55`, `collector/inventory_runner.py:206-228`).


## Collecter sans confondre les runs

Le modèle prescrit la conservation des observations de virtualisation et IPAM par run, y compris les enregistrements IPAM sans VM correspondante (`docs/modele/regles.md:4-8`). Le pipeline livré collecte d'abord les VM et CT, puis les observations IPAM (`collector/inventory_runner.py:283-328`). Il indexe les VM par origine et `vm_id`, et les observations IPAM par origine, IP et nom DNS, à l'intérieur du run (`collector/inventory_runner.py:357-395`). Une même IP associée à différents noms DNS reste ainsi représentable ; des clés identiques dans une même collecte sont regroupées (`collector/inventory_runner.py:382-395`). Les custom fields et la zone IPAM sont enregistrés avec chaque observation, sans exiger de correspondance virtuelle (`collector/inventory_runner.py:382-401`).

Le cahier des charges § 8.2 décrit sept étapes et une annulation en cas d'échec ; leur réalisation varie selon le mode de lancement (`docs/modele/sequences.md:19-30`, `collector/inventory_runner.py:259-281`). En mode asynchrone, le lancement crée un run `RUNNING` avant de démarrer le travail dans un fil ; la requête n'attend pas la collecte (`collector/async_runner.py:38-60`). Le traitement publie l'avancement de chaque étape, puis enregistre les observations et la réussite à la fin (`collector/inventory_runner.py:243-252`, `collector/inventory_runner.py:547-589`). Une exception annule la transaction des observations et persiste `FAIL` avec son message ; les étapes déjà franchies restent indiquées (`collector/inventory_runner.py:594-612`). En mode synchrone, les sept étapes s'exécutent dans une transaction, puis la réussite porte les quatre compteurs (`collector/inventory_runner.py:259-260`, `collector/inventory_runner.py:578-585`). Le diagramme d'états distingue `RUNNING`, `SUCCESS` et `FAIL` ; il ne suffit pas à prouver à lui seul la mécanique transactionnelle (`docs/modele/etats.md:7-18`).

## Rapprocher et qualifier les résultats

La conception donne priorité au nom, au FQDN réduit, puis à l'IP (`docs/modele/regles.md:12-14`). Le code parcourt ces trois priorités globalement : un nom exact gagne sur une IP rencontrée plus tôt (`collector/matching.py:31-46`, `tests/test_matching.py:24-36`). La normalisation ignore la casse, les espaces et le suffixe de domaine (`collector/normalization.py:6-8`). Si aucun rapprochement exact n'existe, le code tente aussi un rapprochement flou unique ; un score inférieur à 70 ou une égalité entre candidats laisse `NO_MATCH` (`collector/matching.py:47-66`). Le résultat conserve la catégorie, la confiance et le doute ; sans IPAM correspondant, les champs finaux proviennent de la virtualisation (`collector/matching.py:69-83`). Ainsi, une VM nommée différemment du DNS IPAM mais liée par IP reçoit `MATCHED_IP` et `HOSTNAME_MISMATCH` (`collector/anomalies.py:111-116`).

La convention de nommage prime sur le tag `role` ; faute de lettre reconnue ou de tag exploitable, le rôle vaut « Indéterminé » (`collector/roles.py:8-28`). La conception répertorie 21 lettres ; le code accepte les valeurs `role:web` et `role:api`, et non tout tag `role:xxx` (`docs/modele/regles.md:18-19`, `collector/roles.py:8-28`). Cette différence sépare une règle de conception du comportement effectivement livré (`docs/modele/regles.md:19`, `collector/roles.py:24-28`).

## Constater les anomalies et conserver leur suivi

Une VM sans IPAM produit `NO_MATCH` ; une VM arrêtée liée à une IP active produit `STATUS_MISMATCH` (`collector/anomalies.py:104-119`). Les doublons DNS normalisés et les doublons IP sont recherchés parmi toutes les observations IPAM, même sans VM rapprochée (`collector/anomalies.py:123-139`). Le rapprochement douteux peut produire `MATCH_CONFIDENCE_DOUBTFUL` ; le changement de nom à identité constante est comparé au run réussi précédent (`collector/anomalies.py:104-110`, `collector/inventory_runner.py:450-456`). Les constats sont rattachés au run courant ; le suivi peut résoudre un constat antérieurement acquitté, sans effacer son historique (`collector/inventory_runner.py:500-505`, `collector/inventory_runner.py:564-576`). La disparition seule ne résout pas automatiquement toutes les catégories : plusieurs codes sont exclus de cette résolution (`collector/inventory_runner.py:564-575`).

La conception étend aussi les contrôles au réseau et à la capacité ; le pipeline appelle les vérifications de préfixe, tags et capacité, puis celles des doublons, PTR et champs IPAM (`docs/modele/regles.md:61-77`, `collector/inventory_runner.py:492-545`). Un score flou inférieur à 70 ou une égalité donne `NO_MATCH`, avec le score et le doute ; seul un flou concluant donne `MATCHED_FUZZY` (`collector/matching.py:47-78`).

## Exploiter le cycle de vie

La purge conserve par défaut les 30 runs les plus récents selon la date de début et l'identifiant ; elle intervient après le traitement, y compris après un échec (`collector/purge.py:12-38`, `collector/inventory_runner.py:594-644`). Un déclenchement planifié utilise le même chemin asynchrone ; l'interface accepte les cadences horaire et quotidienne (`app/scheduling.py:27-28`, `app/scheduling.py:35-56`, `app/scheduling.py:100-125`). Ce cycle s'inscrit dans un environnement hors ligne : le choix entre sources simulées et réelles dépend de la configuration, non du seul diagramme de conception (`collector/inventory_runner.py:206-229`, `docs/modele/sequences.md:19-26`).

> **À retenir** — Pour réviser E5, distinguez les prescriptions du modèle des preuves du pipeline : l'ordre de rapprochement exact est livré, mais le code ajoute un rapprochement flou et limite les tags de rôle pris en charge (`docs/modele/regles.md:12-19`, `collector/matching.py:31-66`, `collector/roles.py:19-28`).


## Entrer dans l'application : secrets, authentification et rôles

Dans CloudInventory, la factory Flask refuse les secrets nécessaires absents, vides ou égaux à la valeur de substitution ; elle exige aussi un mot de passe administrateur d'au moins 12 caractères (`app/__init__.py:329-342`). Les mots de passe des comptes locaux sont hachés avec Argon2 à la création ; l'authentification accepte aussi les anciens hachages reconnus, puis les réécrit au format Argon2 après connexion (`app/accounts.py:34-49`, `app/auth.py:178-205`). Une authentification par annuaire peut créer un compte ou actualiser son rôle ; sa disponibilité et la configuration locale conditionnent le repli vers le mot de passe local (`app/auth.py:156-179`).

Vous pouvez distinguer trois rôles : `admin`, `opérateur` et `lecteur` (`app/accounts.py:23-25`). Les lectures ordinaires sont ouvertes aux trois rôles ; les opérations sur anomalies et runs concernent `admin` et `opérateur` ; l'administration des comptes et la consultation du journal d'audit sont réservées à `admin` (`app/__init__.py:432-473`). Le formulaire web de connexion et les requêtes POST authentifiées vérifient un jeton CSRF ; l'API exige un JWT pour ses routes protégées (`app/auth.py:207-230`, `app/__init__.py:489-517`). Les tentatives de connexion sont limitées par des compteurs locaux au processus, avec délais croissants pour un compte valide ; cela ne constitue pas une limitation partagée entre plusieurs workers (`app/auth.py:83-89`, `app/auth.py:135-152`, `app/auth.py:189-204`).

Ces autorisations s'ajoutent à la durée de validité des accès, sans s'y substituer (`app/auth.py:101-119`, `app/__init__.py:489-510`).

## Révocation des sessions et des jetons

La session web porte une version de compte et expire après 8 heures de connexion ou 30 minutes d'inactivité ; un redémarrage change également l'époque des sessions web (`app/auth.py:83-89`, `app/auth.py:101-119`, `app/auth.py:231-237`). Le JWT porte lui aussi cette version ; la garde API refuse avec HTTP 401 un jeton sans version, périmé ou rattaché à un compte local désactivé (`app/api.py:121-123`, `app/__init__.py:489-504`). La colonne `session_version` est persistante et non nullable ; une migration additive la crée avec la valeur initiale zéro (`app/models.py:240-248`, `migrations/versions/0005_account_session_version.py:6-13`).

La déconnexion incrémente la version du seul compte connecté avant de vider la session web ; ses anciens cookies et JWT deviennent invalides après validation de cette écriture (`app/auth.py:240-252`, `app/auth.py:112-117`, `app/__init__.py:498-503`). Un changement effectif de rôle, une désactivation ou une réinitialisation du mot de passe incrémentent la même version dans la transaction du compte ; un changement de rôle sans effet ne l'incrémente pas (`app/accounts.py:62-94`). Le code conserve le contrôle du dernier administrateur actif avant la modification du compte (`app/accounts.py:72-78`).

Les tests rejouent un ancien cookie et un ancien JWT après déconnexion, tout en vérifiant que le compte tiers reste accessible (`tests/test_dynamic_security.py:24-41`). Ils simulent aussi une panne avant validation, avec retour arrière effectué par le faux `commit`, puis vérifient l'état et les accès antérieurs (`tests/test_dynamic_security.py:44-82`). Une migration est testée sur la copie jetable d'une base SQLite remplie, et non sur une base déployée (`tests/test_dynamic_security.py:85-111`). Ces scénarios attestent ces cas testés, pas toutes les pannes possibles ni une garantie de concurrence entre plusieurs workers (`tests/test_dynamic_security.py:44-111`, `app/auth.py:83-85`).

La révocation des accès est distincte de la traçabilité des actions, examinée ci-dessous (`app/auth.py:240-252`, `app/audit.py:18-41`).

## Traces d'authentification et journal d'audit

Le journal d'authentification `security.auth` écrit sur stderr avec horodatage UTC ; les événements portent résultat, canal et adresse du pair, sans mot de passe (`app/auth.py:31-42`, `app/auth.py:126-126`, `app/auth.py:151-152`, `app/auth.py:197-204`, `app/auth.py:240-243`). Le journal métier est différent : l'ORM insère dans la transaction une action autorisée, une cible, un horodatage et l'identité de l'auteur ; sans identité identifiable, l'auteur devient « système » (`app/audit.py:18-41`). Le modèle conserve aussi une clé de compte facultative et l'identité textuelle de l'auteur (`app/models.py:274-290`).

Le suivi métier couvre notamment la création des runs, les changements d'état d'anomalie, les exceptions, les planifications et les modifications de compte (`app/audit.py:44-94`). La purge des anciens runs inscrit une action dans sa transaction, puis supprime les observations et runs ciblés (`collector/purge.py:27-39`). La page `/audit` est limitée à l'administrateur : filtres sur auteur, action, cible et dates, puis affichage des 200 dernières lignes correspondantes (`app/audit.py:97-126`). Le code de ces événements ajoute des lignes au journal ; le modèle présenté ne définit cependant aucune interdiction SQL générale de modifier ou supprimer une ligne d'audit (`app/audit.py:18-41`, `app/models.py:274-290`).

Cette traçabilité ne décide pas à elle seule combien de temps conserver les données personnelles (`app/models.py:274-290`, `docker-compose.yml:16-20`).

## Limites et réserves à expliquer à l'oral E5

| Sujet | Dispositif livré | Limite à annoncer |
|---|---|---|
| Journaux d'authentification | Compose fait tourner les fichiers de journal selon leur taille et leur nombre (`docker-compose.yml:16-20`). | Cette rotation ne définit ni échéance temporelle ni suppression des copies et exports ; la durée reste à décider avant déploiement (`README.md:20`). |
| Comptes et auteurs | La purge porte sur les runs et leurs observations ; l'audit conserve une identité textuelle même si sa clé de compte devient nulle (`collector/purge.py:12-39`, `app/models.py:274-290`). | Cette purge ne fixe aucune échéance de conservation des comptes ou des auteurs du journal (`collector/purge.py:12-39`, `app/models.py:240-258`, `app/models.py:274-290`). |
| Information et droits | Le formulaire de connexion présente les champs d'identification et le bouton de connexion (`app/templates/login.html:8-20`). | Ce formulaire ne fournit ni notice ni contact pour exercer des droits ; il ne permet pas de conclure à une conformité juridique (`app/templates/login.html:8-20`). |
| Transport | Compose publie le service sur l'interface locale, en HTTP, avec cookie sans attribut `Secure` pour ce profil ; le défaut applicatif active cet attribut (`docker-compose.yml:4-13`, `app/__init__.py:370-373`). | Aucun frontal TLS n'est livré dans ce profil ; ne le présentez pas comme un déploiement sécurisé accessible sur un réseau (`README.md:22`, `docker-compose.yml:1-27`). |

Le cahier des charges § 15 demande un dossier argumenté : vous devez séparer les contrôles implémentés, les résultats des tests ciblés et ces décisions de politique encore ouvertes (`app/auth.py:101-119`, `tests/test_dynamic_security.py:24-111`, `README.md:20`). L'audit Argus est ciblé : les scénarios cités sont exécutés dans l'environnement hors ligne, sans prouver une certification globale ni une recette de sécurité sur une installation réelle (`tests/test_dynamic_security.py:24-111`, `README.md:22`).

> **À retenir** — L'accès web et API dépend de la version persistante du compte ; les anciens accès du compte modifié sont refusés après validation, sans invalider les autres comptes (`app/auth.py:112-117`, `app/auth.py:244-251`, `app/__init__.py:498-503`, `tests/test_dynamic_security.py:24-41`). La rotation en volume des journaux et la purge des runs ne règlent pas la durée de conservation des traces et des auteurs (`docker-compose.yml:16-20`, `collector/purge.py:12-39`, `app/models.py:274-290`).


## Tests automatisés : ce que vérifie `make verify`

`make verify` enchaîne Ruff, mypy, pytest sous coverage, le rapport de couverture, Bandit et pip-audit (`Makefile:10-16`). Le seuil de couverture configuré est `--fail-under=0` : la commande ne garantit donc aucun pourcentage minimal de couverture (`Makefile:13-14`). L'audit des dépendances vise `requirements.txt`, tandis que Ruff, mypy et Bandit examinent `app` et `collector` (`Makefile:11-16`).

Les tests pytest contrôlent des comportements précis, sans constituer à eux seuls une validation en conteneur (`Makefile:13`, `docker-compose.yml:1-25`). Par exemple, un test vérifie la santé via le client de test Flask ; son dispositif bloque les connexions réseau sortantes (`tests/test_app.py:46-56`, `tests/test_app.py:74-82`). Un autre vérifie qu'une création de Run par API répond `202` et que les observations simulées sont enregistrées après l'exécution asynchrone (`tests/test_api_runs.py:84-126`). Les scénarios de collecte contrôlent aussi la conservation de deux instantanés distincts et l'annulation des observations lors d'une erreur à chacune des sept étapes (`tests/test_inventory_runner.py:207-237`). Ces exemples décrivent les assertions écrites, pas une exécution des tests pendant cette rédaction (`tests/test_app.py:74-82`, `tests/test_api_runs.py:114-126`, `tests/test_inventory_runner.py:224-237`).

## Smoke HTTP local : une autre frontière

Contrairement au client de test Flask, `make smoke` lance le script avec Gunicorn ; il installe les dépendances seulement si l'interpréteur virtuel manque (`Makefile:18-20`, `scripts/smoke.py:35-38`). L'installation, si nécessaire, passe par pip et peut donc nécessiter un accès réseau ; elle ne prouve pas qu'une machine en réseau fermé dispose déjà des paquets (`Makefile:6-8`, `Makefile:18-20`).

Le script réserve une adresse loopback et prépare une base SQLite temporaire ainsi que des secrets jetables ; il active les sources virtuelles et IPAM simulées (`scripts/smoke.py:16-34`). Il attend une réponse `200` de `/health`, obtient un jeton par `/api/login`, puis lance `/api/runs` avec une réponse attendue `202` (`scripts/smoke.py:39-72`). Il interroge ensuite le Run jusqu'au statut `SUCCESS`, avant d'arrêter le processus ; le répertoire temporaire est géré par `TemporaryDirectory` (`scripts/smoke.py:16-17`, `scripts/smoke.py:74-93`). Ce parcours ne démontre ni la disponibilité des sources réelles ni le fonctionnement du déploiement Compose : ses collecteurs sont simulés et son serveur est lancé localement (`scripts/smoke.py:25-36`, `docker-compose.yml:1-25`).

## Recette Compose et exploitation en réseau fermé

La recette de démarrage Compose se distingue du smoke : la procédure documentée construit l'image avec `docker compose build`, démarre avec `docker compose up -d --wait` et vérifie le service au moyen du healthcheck `/login` (`README.md:22`, `docker-compose.yml:21-25`). Compose publie le service sur loopback, utilise un volume SQLite et active les sources virtuelles et IPAM simulées (`docker-compose.yml:4-15`). Le healthcheck teste l'accès à `/login` ; il ne remplace pas le parcours authentifié et le Run simulé de `make smoke` (`docker-compose.yml:21-25`, `scripts/smoke.py:70-85`).

Pour préparer un environnement hors ligne, `make hors-ligne` appelle `scripts/offline.py` (`Makefile:31-32`). La préparation construit et exporte une image, télécharge des roues avec les empreintes du verrou, puis génère un Compose sans construction et un manifeste SHA256 (`scripts/offline.py:24-56`). Le test du paquet simule ces commandes externes : il vérifie les fichiers et l'absence de copie d'un fichier de secrets, sans prouver une installation Docker réelle (`tests/test_offline.py:11-44`). La procédure demande de préparer le paquet sur une machine connectée compatible, de contrôler ses empreintes sur la cible et de démarrer avec `--pull never` (`docs/hors-ligne.md:3-5`). Le wheelhouse propose une installation Python sans réseau, mais le paquet ne contient pas les sources nécessaires à une installation native : l'image Docker reste la voie autonome documentée (`docs/hors-ligne.md:7`).

> **À retenir** — `make verify` contrôle le code et ses tests, `make smoke` parcourt une API HTTP simulée, et la recette Compose vérifie le démarrage du service conteneurisé (`Makefile:10-20`, `scripts/smoke.py:25-36`, `scripts/smoke.py:70-85`, `docker-compose.yml:21-25`). Ces vérifications, telles que décrites, ne valident pas une collecte réelle en réseau fermé (`scripts/smoke.py:25-34`, `docker-compose.yml:10-12`, `docs/hors-ligne.md:3-7`).


## Distinguer la cible des fonctions livrées

Pour votre dossier E5, distinguez la mission décrite au cahier des charges § 15 du comportement vérifiable dans CloudInventory (`README.md:3`, `collector/inventory_runner.py:206-228`). Le profil Compose active les collecteurs simulés Proxmox et IPAM ; il ne démontre aucune collecte réelle dans un réseau fermé (`docker-compose.yml:10-13`, `README.md:22`). Le code propose pourtant des modes réels Proxmox, vSphere, NetBox et EfficientIP : leur existence ne prouve pas un déploiement sur les équipements cibles (`collector/inventory_runner.py:206-228`).

Le paquet hors ligne se prépare sur une machine connectée, puis fournit une image Docker, des roues et un Compose autonome (`docs/hors-ligne.md:3-7`, `scripts/offline.py:37-49`). Il ne contient pas les sources nécessaires à une installation Python native sur la cible (`docs/hors-ligne.md:7`). La préparation et l'installation décrites ne prouvent pas à elles seules une recette dans l'environnement hors ligne (`docs/hors-ligne.md:3-7`).

Les anomalies de capacité requièrent des mesures d'occupation observées, distinctes des capacités allouées (`README.md:34`). Les collecteurs fournis ne transmettent pas ces mesures : sans source injectée, les trois constats concernés ne sont pas produits (`README.md:34`). Une demande de proposition NetBox reste simulée et n'écrit aucune correction dans NetBox (`app/netbox_proposals.py:23-29`, `app/netbox_proposals.py:88-92`, `app/api.py:329-344`).

Ces écarts fonctionnels ne doivent pas masquer les réserves de conservation et d'information (`README.md:20`, `app/models.py:240-257`, `app/templates/login.html:8-20`).

## Expliquer les réserves ouvertes

| Réserve | Limite effectivement visible | Décision encore nécessaire |
|---|---|---|
| R-07 — durée de conservation des journaux non définie | La déconnexion journalise l'adresse IP ; Compose borne la taille et le nombre des fichiers, pas leur âge (`app/auth.py:240-243`, `docker-compose.yml:16-20`). | Définir une durée et supprimer journaux et copies à échéance ; README.md confie cette décision à l'opérateur (`README.md:20`). |
| R-08 — échéance et purge des comptes et auteurs non établies | La désactivation conserve l'empreinte locale ; l'audit conserve l'identité textuelle de l'auteur (`app/accounts.py:83-85`, `app/models.py:274-289`). La purge livrée vise les runs anciens, pas ces données (`collector/purge.py:12-38`). | Définir les échéances et le sort des comptes, auteurs et copies sans détruire l'intégrité des traces (`app/models.py:274-289`, `collector/purge.py:27-38`). |
| R-09 — information et procédure des droits non établies | Le formulaire de connexion ne présente ni notice ni contact ; les actions sur les comptes sont rôle, désactivation et mot de passe (`app/templates/login.html:8-20`, `app/accounts.py:58-64`). | Établir l'information et une procédure d'exercice des droits ; les actions visibles ne valent pas cette procédure (`app/templates/login.html:8-20`, `app/accounts.py:58-64`). |

Ces trois réserves demeurent ouvertes : ni la rotation volumétrique, ni la purge des runs, ni les actions de gestion ne les ferment (`docker-compose.yml:16-20`, `collector/purge.py:27-38`, `app/accounts.py:58-64`). Ces observations ne prouvent ni une durée de conservation illégale ni l'absence de toute procédure extérieure à l'application (`README.md:20`, `app/templates/login.html:8-20`).

## Séparer correctifs livrés et garanties non acquises

La révision de session du compte invalide les sessions web antérieures lors d'un changement de rôle, d'une désactivation ou d'un changement de mot de passe (`app/accounts.py:79-90`, `app/auth.py:112-117`). La déconnexion incrémente cette version pour le compte connecté ; les autres comptes ne sont pas modifiés par cette requête (`app/auth.py:240-251`). L'API refuse également un jeton dont la version ne correspond plus au compte (`app/__init__.py:489-503`). Ces corrections ciblent la révocation des accès, sans définir de durée de conservation ni de procédure des droits (`app/accounts.py:79-90`, `README.md:20`, `app/templates/login.html:8-20`).

Ne présentez pas ces mécanismes comme une certification ou un audit général : le profil livré reste local et simulé par défaut (`README.md:3`, `README.md:22`). Pour argumenter votre dossier E5, associez chaque limite à son mécanisme et distinguez correction effective, décision de politique et validation sur cible (`app/auth.py:240-251`, `README.md:20`, `docs/hors-ligne.md:3-7`).

> **À retenir** — R-07, R-08 et R-09 restent ouvertes tant que les durées, purges et modalités d'information ne sont pas établies (`README.md:20`, `collector/purge.py:27-38`, `app/templates/login.html:8-20`). Les corrections de révocation ne règlent pas ces réserves (`app/accounts.py:79-90`, `app/auth.py:240-251`).

