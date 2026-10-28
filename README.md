# cloudinventory

Application Flask d’inventaire cloud avec collecteurs simulés par défaut et base SQLite persistante configurable.

## Prérequis

- Python 3.11 pour le lancement local.
- Docker Compose pour le lancement en conteneur.

## Installation et lancement

Installer les dépendances Python : `make install`. La factory crée les tables au démarrage. `DATABASE_URL` configure la base ; sans cette variable, le lancement local utilise `data/cloudinventory.sqlite3` (dossier ignoré par git). Compose configure `/app/data/cloudinventory.sqlite3` dans son volume nommé `sqlite_data`.

La factory exige `SECRET_KEY`, `JWT_SECRET_KEY` et `ADMIN_PASSWORD`, fournis hors dépôt par l’environnement. Ne versionnez jamais `.env`. Pour lancer localement après installation, définir ces trois variables puis exécuter `.venv/bin/python -m flask --app app:create_app run --host 127.0.0.1 --port $PORT` avec `PORT` défini. L’application est alors disponible sur `http://127.0.0.1:$PORT/`.

Les sessions web expirent après 8 heures depuis la connexion (durée absolue, non glissante) ou 30 minutes sans requête authentifiée. La déconnexion révoque toutes les sessions web de l'administrateur unique ; un redémarrage les révoque aussi. Cette révocation est conservée en mémoire par instance : utiliser un seul worker, ou prévoir un état partagé avant d'en ajouter. Ces règles ne modifient pas les jetons Bearer de l'API. Les événements d'authentification et de déconnexion (`security.auth`) sont écrits sur stderr par le journal applicatif, avec canal et adresse IP, sans identifiants ni secrets ; rotation et conservation des journaux sont à fixer au déploiement.

Pour Docker, construire l’image avec `docker compose build`, puis démarrer avec `docker compose up -d --wait`. Ce profil Compose est réservé à l’usage local : son port est publié uniquement sur `127.0.0.1` (le port hôte reste configurable par `APP_PORT`, avec 5050 par défaut). `SESSION_COOKIE_SECURE` vaut `false` uniquement pour ce service HTTP local ; le cookie n’est donc pas protégé par l’attribut `Secure` sur ce transport. Un déploiement réel doit passer par un frontal TLS et conserver le défaut applicatif `SESSION_COOKIE_SECURE` à `true` ; aucun frontal ni réglage de déploiement n’est fourni ici. Avant tout démarrage normal avec Compose, fournissez `SECRET_KEY`, `JWT_SECRET_KEY` et `ADMIN_PASSWORD` depuis l’environnement, hors dépôt, avec des valeurs réelles propres à votre déploiement. Aucun secret n’a de valeur par défaut : des variables absentes ou vides font échouer le démarrage, et `change-me` est refusé. Le conteneur ne génère pas de secrets. La recette de démarrage fournit hors dépôt des valeurs jetables lorsque ces variables sont vides ou restent à `change-me` ; cela ne modifie ni l’image ni Compose, et ne change pas le refus applicatif des secrets invalides en démarrage normal. Le service est publié sur le port 5050 par défaut (`APP_PORT` permet de le changer) ; santé : `http://127.0.0.1:5050/login`. Les mocks virtuels et IPAM sont activés dans Compose ; les collecteurs n’effectuent pas d’appels réseau sortants.

Une base inaccessible provoque une erreur explicite au démarrage ou lors de la requête concernée. Les fichiers SQLite et les exports générés ne sont pas suivis par git.

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
