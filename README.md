# cloudinventory

Application Flask d’inventaire cloud avec collecteurs simulés par défaut et base SQLite persistante configurable.

## Prérequis

- Python 3.11 pour le lancement local.
- Docker Compose pour le lancement en conteneur.

## Installation et lancement

Installer les dépendances Python : `make install`. La factory crée les tables au démarrage. `DATABASE_URL` configure la base ; sans cette variable, le lancement local utilise `data/cloudinventory.sqlite3` (dossier ignoré par git). Compose configure `/app/data/cloudinventory.sqlite3` dans son volume nommé `sqlite_data`.

La factory exige `SECRET_KEY`, `JWT_SECRET_KEY` et `ADMIN_PASSWORD`, fournis hors dépôt par l’environnement. Ne versionnez jamais `.env`. Pour lancer localement après installation, définir ces trois variables puis exécuter `.venv/bin/python -m flask --app app:create_app run --host 127.0.0.1 --port $PORT` avec `PORT` défini. L’application est alors disponible sur `http://127.0.0.1:$PORT/`.

Les sessions web expirent après 8 heures depuis la connexion (durée absolue, non glissante) ou 30 minutes sans requête authentifiée. La déconnexion révoque toutes les sessions web de l'administrateur unique ; un redémarrage les révoque aussi. Cette révocation est conservée en mémoire par instance : utiliser un seul worker, ou prévoir un état partagé avant d'en ajouter. Ces règles ne modifient pas les jetons Bearer de l'API.

Les quotas de connexion sont locaux au processus et utilisent l'adresse du pair TCP ; les en-têtes `X-Forwarded-For`, `X-Real-IP` et `Forwarded` sont ignorés. Pour tout autre nom que l'administrateur, le plafond est de 5 échecs par nom et de 20 par adresse sur 900 secondes. Le seau administrateur admet cinq échecs initiaux, puis impose des délais croissants de 30, 60, 120, 240, 480 et 900 secondes, ce dernier délai étant plafonné. Un mot de passe correct ne contourne pas un délai en cours : seule une authentification réussie et admise réinitialise le seau administrateur. Un tiers visant le nom administrateur depuis la même adresse peut encore retarder l'administrateur d'un palier croissant. La séparation des autres noms limite leur effet sur l'administrateur, sans supprimer le plafond par adresse. Garder un worker unique tant que cet état reste local ; plusieurs workers auraient des compteurs indépendants.

Les événements d'authentification et de déconnexion (`security.auth`) sont écrits sur stderr avec horodatage ISO 8601 UTC ; ils contiennent uniquement le résultat, le canal et l'adresse. L'accès aux journaux doit être réservé aux opérateurs autorisés. Docker utilise le pilote `json-file` et une rotation de 10 Mo par fichier sur cinq fichiers : cette borne de volume ne fixe pas une durée de conservation. Avant déploiement, l'opérateur doit fixer explicitement la durée maximale de conservation des adresses IP, puis assurer à l'échéance la suppression des journaux, copies et exports concernés. Aucune durée légale ni conformité n'est présumée.

Pour Docker, construire l’image avec `docker compose build`, puis démarrer avec `docker compose up -d --wait`. Ce profil Compose est réservé à l’usage local : son port est publié uniquement sur `127.0.0.1` (le port hôte reste configurable par `APP_PORT`, avec 5050 par défaut). `SESSION_COOKIE_SECURE` vaut `false` uniquement pour ce service HTTP local ; le cookie n’est donc pas protégé par l’attribut `Secure` sur ce transport. Un déploiement réel doit passer par un frontal TLS et conserver le défaut applicatif `SESSION_COOKIE_SECURE` à `true` ; aucun frontal ni réglage de déploiement n’est fourni ici. Avant tout démarrage normal avec Compose, fournissez `SECRET_KEY`, `JWT_SECRET_KEY` et `ADMIN_PASSWORD` depuis l’environnement, hors dépôt, avec des valeurs réelles propres à votre déploiement. Aucun secret n’a de valeur par défaut : des variables absentes ou vides font échouer le démarrage, et `change-me` est refusé. Le conteneur ne génère pas de secrets. La recette de démarrage fournit hors dépôt des valeurs jetables lorsque ces variables sont vides ou restent à `change-me` ; cela ne modifie ni l’image ni Compose, et ne change pas le refus applicatif des secrets invalides en démarrage normal. Le service est publié sur le port 5050 par défaut (`APP_PORT` permet de le changer) ; santé : `http://127.0.0.1:5050/login`. Les mocks virtuels et IPAM sont activés dans Compose ; les collecteurs n’effectuent pas d’appels réseau sortants.

Une base inaccessible provoque une erreur explicite au démarrage ou lors de la requête concernée. Les fichiers SQLite et les exports générés ne sont pas suivis par git.

Les notifications SMTP et webhook sont simulées par défaut. `ANOMALY_ALERT_THRESHOLD` est un entier non négatif, fixé à `10` par défaut. Après un run réussi, une alerte part si au moins une anomalie ouverte, non couverte par une exception active, est nouvelle par rapport au dernier run réussi (même code et même cible stable), ou si le nombre de ces anomalies passe d'une valeur inférieure ou égale au seuil à une valeur strictement supérieure. Une anomalie sans cible stable est considérée nouvelle ; les anomalies acquittées, résolues et couvertes ne comptent pas. Un premier run considère toutes ses anomalies admissibles comme nouvelles. Un compte restant au-dessus du seuil sans nouveauté ne renvoie pas d'alerte. Une valeur de seuil invalide désactive les alertes et produit un avertissement côté serveur.

## Rapprochement flou

Les contrôles de conformité signalent séparément chaque tag VM `env`, `owner` ou `backup` absent (format `clé:valeur` ou `clé=valeur`, séparé par espace, virgule ou point-virgule), et les observations IPAM sans tenant ou sans site, y compris non appariées. `VM_ZOMBIE_DAYS` fixe le seuil de VM arrêtée, en jours entiers positifs (30 par défaut) : l'anomalie apparaît strictement après ce délai. L'instant source `vm_stopped_since`, s'il est fourni avec fuseau ISO 8601, fait foi ; sinon seul le premier run d'une suite continue d'observations arrêtées datées est retenu. Une VM redémarrée ou absente d'un run réussi interrompt cette suite. Le premier constat ne présume aucune durée antérieure. Ces quatre codes ont une sévérité NULL tant qu'aucune classification n'est définie.

Les correspondances exactes restent prioritaires dans l'ordre nom, FQDN, IP : confiance 100, doute faux. Sinon, les noms VM et FQDN ainsi que les DNS IPAM sont normalisés (casse, espaces, suffixe de domaine) et comparés par `rapidfuzz.fuzz.ratio`, score de similarité de 0 à 100 (100 = identique). Le meilleur score par observation IPAM est retenu, puis arrondi à l'entier le plus proche, demi vers le haut, borné à 0–100. Un candidat unique d'au moins 70 est associé avec le statut `MATCHED_FUZZY`. Un score de 70 à 89 inclus est douteux ; un ex aequo des meilleurs candidats est douteux et n'est jamais associé, quel que soit son score. En dessous de 70, aucun candidat n'est associé ; le score calculable est conservé, mais il n'est pas douteux sauf ex aequo. Sans deux noms non vides à comparer, confiance NULL et doute faux : le contrôle n'a pas été possible. Par exemple `web-a500` et `web-a500-old` sont rapprochés et signalés comme douteux. Le doute décrit l'incertitude du rapprochement, pas une gravité de sécurité : l'anomalie `MATCH_CONFIDENCE_DOUBTFUL` a une sévérité NULL et reste éligible au suivi et aux exceptions. Une anomalie de doute acquittée n'est résolue que lors d'un run ultérieur où la même VM a un résultat comparable non douteux ; VM absente et noms incomparables ne valent pas résolution.

## Vérification

- `make verify` exécute les tests pytest.
- `make smoke` installe automatiquement les dépendances dans `.venv/` si l’interpréteur n’y existe pas (l’installation peut accéder au réseau). Il lance Gunicorn sur une adresse loopback et un port libre, avec des secrets et une base SQLite temporaires, puis vérifie la santé, la connexion et une exécution simulée par HTTP avant d’arrêter le processus et de supprimer les données temporaires. Les tests pytest configurent séparément SQLite en mémoire.
- La recette HTTP Docker est distincte et vérifie le service démarré et son healthcheck.

## Arborescence

- `app/` — application Flask, API et modèles.
- `collector/` — collecteurs.
- `tests/` — tests pytest.
- `docs/modele/` — documentation du modèle.
- `docker-compose.yml`, `Dockerfile`, `Makefile` — lancement et vérifications.
