# cloudinventory

Application Flask d’inventaire cloud avec collecteurs simulés par défaut.

## Prérequis

- Python 3.11 pour le lancement local.
- Docker Compose pour le lancement en conteneur.

## Installation et lancement

Installer les dépendances Python : `make install`.

La factory exige `SECRET_KEY`, `JWT_SECRET_KEY` et `ADMIN_PASSWORD`, fournis hors dépôt par l’environnement. Ne versionnez jamais `.env`. Pour lancer localement après installation, définir ces trois variables puis exécuter `.venv/bin/python -m flask --app app:create_app run --host 127.0.0.1 --port $PORT` avec `PORT` défini. L’application est alors disponible sur `http://127.0.0.1:$PORT/`.

Pour Docker, construire l’image avec `docker compose build`, puis démarrer avec `docker compose up -d --wait`. Au démarrage, `scripts/start.sh` génère en mémoire chaque secret absent, vide ou égal à `change-me` avant de lancer la factory ; aucun secret n’est affiché ni enregistré. Les trois valeurs peuvent aussi être fournies par l’environnement ou un `.env` local non versionné (ne pas copier les placeholders de `.env.example` en production). Les secrets générés changent à chaque recréation du conteneur : les sessions et jetons précédents deviennent invalides, et le mot de passe admin change. Pour des valeurs stables, fournissez les trois secrets hors dépôt. Le service est publié sur le port 5050 par défaut (`APP_PORT` permet de le changer) ; santé : `http://127.0.0.1:5050/login`. Les mocks virtuels et IPAM sont activés dans Compose ; les collecteurs n’effectuent pas d’appels réseau sortants.

Le volume Docker est monté sur `/app/data`, mais SQLite reste configuré en mémoire. Les données ne persistent donc pas ; la persistance attend une correction applicative.

## Vérification

- `make verify` exécute les tests pytest.
- `make smoke` installe automatiquement les dépendances dans `.venv/` si l’interpréteur n’y existe pas (l’installation peut accéder au réseau), puis utilise le même lanceur de secrets et le client Flask isolé pour vérifier santé, connexion et exécution simulée, en bloquant les connexions sortantes pendant ce parcours. Il fonctionne aussi après `make install` et n’est pas une recette HTTP du conteneur.
- La recette HTTP Docker est distincte et vérifie le service démarré et son healthcheck.

## Arborescence

- `app/` — application Flask, API et modèles.
- `collector/` — collecteurs.
- `tests/` — tests pytest.
- `docs/modele/` — documentation du modèle.
- `docker-compose.yml`, `Dockerfile`, `Makefile` — lancement et vérifications.
