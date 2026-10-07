# Règles de gestion — CloudInventory v2.0

| RG | Type | Énoncé | Source |
|----|------|--------|--------|
| RG01 | [matching] | Une exécution d'inventaire doit appliquer le matching 4 niveaux par ordre de priorité (hostname normalisé → FQDN → IP → NO_MATCH) | §7.1 |
| RG02 | [matching] | La stratégie MATCHED_NAME doit comparer les hostnames normalisés (minuscules, sans espaces, sans suffixe de domaine) entre l'asset et l'enregistrement IPAM | §7.1, §7.2 |
| RG03 | [matching] | La stratégie MATCHED_FQDN doit comparer le premier segment du FQDN normalisé avec le DNS Name de l'IPAM | §7.1 |
| RG04 | [matching] | La stratégie MATCHED_IP doit comparer l'IP rapportée de l'asset avec l'adresse IP de l'enregistrement IPAM | §7.1 |
| RG05 | [matching] | Si aucune des 3 premières stratégies ne matche, le résultat doit être NO_MATCH | §7.1 |
| RG06 | [matching] | Si un asset matche par IP mais que le hostname diffère du DNS NetBox, l'anomalie HOSTNAME_MISMATCH doit être déclenchée | §7.1, §6.2 |
| RG07 | [matching] | Si un asset est arrêté (stopped) mais que son IP est active dans NetBox, l'anomalie STATUS_MISMATCH doit être déclenchée | §7.1, §6.2 |
| RG08 | [anomalies] | Le code NO_MATCH doit être déclenché lorsqu'aucune correspondance n'est trouvée (ni hostname, ni FQDN, ni IP) | §6.2, §13.3 |
| RG09 | [matching] | Le statut de correspondance MATCHED_IP doit être attribué lorsque l'IP rapportée correspond à l'adresse IP d'un enregistrement IPAM ; dans ce cas l'anomalie HOSTNAME_MISMATCH est levée si le hostname diffère du DNS NetBox. | §7.1, §6.2 |
| RG10 | [anomalies] | Le code HOSTNAME_MISMATCH doit être déclenché lorsqu'une VM est matcheée par IP mais que le hostname diffère du DNS NetBox | §6.2, §13.3 |
| RG11 | [anomalies] | Le code STATUS_MISMATCH doit être déclenché lorsqu'une VM est arrêtée (stopped) mais que son IP est active dans NetBox | §6.2, §13.3 |
| RG12 | [anomalies] | Le code DUPLICATE_DNS doit être déclenché lorsqu'un même nom DNS normalisé apparaît dans plusieurs enregistrements IPAM | §6.2, §7.4, §13.3 |
| RG13 | [anomalies] | Le code DUPLICATE_IP doit être déclenché lorsqu'une même adresse IP apparaît dans plusieurs enregistrements IPAM | §6.2, §7.4, §13.3 |
| RG14 | [normalisation] | La normalisation des hostnames doit appliquer : conversion en minuscules, suppression des espaces, suppression du suffixe de domaine (ex: web-a500.prod.local → web-a500) | §7.2 |
| RG15 | [rôle] | La déduction de rôle doit d'abord essayer la convention de nommage lettre+3 chiffres (ex: web-a500 → Application, db-b500 → Base de données) | §7.3 |
| RG16 | [rôle] | Si aucune lettre+chiffres n'est trouvée dans le nom, la stratégie des tags `role:xxx` doit être utilisée (ex: tag `role:web` → Web, tag `role:api` → Application) | §7.3 |
| RG17 | [run pipeline] | Un run doit être créé avec status=RUNNING dès le début de l'exécution | §8.2 |
| RG18 | [run pipeline] | L'upsert des Asset doit s'effectuer par vm_id et celui des IpamRecord par ip+dns_name | §8.2 |
| RG19 | [run pipeline] | À la finalisation, le run doit être mis à jour avec status=SUCCESS et les compteurs (matched_name, matched_fqdn, matched_ip, no_match) | §8.2 |
| RG20 | [run pipeline] | En cas d'exception pendant le pipeline, la transaction doit être rollbackée et le status défini à FAIL avec message d'erreur | §8.2 |
| RG21 | [exports] | L'export consolidé JSONL.gz doit avoir une retention de 30 jours par défaut (configurable via EXPORT_RETENTION_CONSOLIDATED) | §8.10 |
| RG22 | [exports] | Le rapport de run Markdown doit écrase à chaque exécution (fichier report.md) | §8.10 |
| RG23 | [exports] | Les exports bruts JSON.gz ont une retention de 7 jours si activés (EXPORT_RAW_ENABLED=true, configurable via EXPORT_RETENTION_RAW) | §8.10 |
| RG24 | [exports] | L'export consolidé doit contenir les champs : vm_id, vm_name, type, node, status, tags, ip_reported, fqdn, os, métriques CPU/RAM/disque, ip_final, dns_final, match_status, role, données IPAM associées | §8.10 |
| RG25 | [auth] | L'authentification web doit utiliser des sessions avec cookie signé via la variable SECRET_KEY | §10.1 |
| RG26 | [auth] | Toutes les routes web doivent être protégées par le décorateur @login_required | §10.1 |
| RG27 | [auth] | L'authentification API doit utiliser JWT via Flask-JWT-Extended | §10.2 |
| RG28 | [auth] | Toutes les routes API doivent être protégées par le décorateur @jwt_required() | §10.2 |
| RG29 | [variables] | Les variables sensibles (SECRET_KEY, JWT_SECRET_KEY, ADMIN_PASSWORD) doivent être impératives dans le fichier .env | §10.3 |
| RG30 | [variables] | L'application doit refuser de démarrer si SECRET_KEY, JWT_SECRET_KEY ou ADMIN_PASSWORD sont absents ou valent leur valeur par défaut (change-me) | §10.3 |
| RG31 | [structure] | La collecte d'asset doit couvrir tous les noeuds hyperviseurs (§13.1) | §13.1 |
| RG32 | [structure] | Le type d'asset (qemu/lxc) doit être enregistré (§13.1) | §13.1 |
| RG33 | [structure] | Le tenant doit être attribué à chaque enregistrement IPAM (§13.2) | §13.2 |
| RG34 | [structure] | Le site doit être attribué à chaque enregistrement IPAM (§13.2) | §13.2 |