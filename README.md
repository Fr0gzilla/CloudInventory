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
