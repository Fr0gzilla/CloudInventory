# CloudInventory — Docker Quickstart

Dockerised Flask application (Python 3.11, Flask-SQLAlchemy, SQLite) with gunicorn WSGI server.

## Prérequis

- Docker + Docker Compose installés ; `make` optionnel (cibles Makefile)
- Versions : Python 3.11, Flask 3.1, Flask-SQLAlchemy, pytest

## Lancement

```bash
cp .env.example .env
docker compose up -d --wait
```

L'application est disponible sur http://127.0.0.1:5000 ; arrêt : `docker compose down`

## Déploiement derrière un proxy TLS

La terminaison TLS est attendue sur le reverse proxy : l'application sert en HTTP clair et n'émet
`Strict-Transport-Security` que si la requête est perçue en HTTPS (`app/__init__.py:190`, `request.is_secure`).

- **HSTS au terminateur** : le proxy ajoute lui-même `Strict-Transport-Security: max-age=31536000` sur ses
  réponses, indépendamment de l'application.
- **Ou ProxyFix** : `werkzeug.middleware.proxy_fix.ProxyFix` doit compter le nombre exact de proxys de confiance
  (un seul proxy devant l'application ⇒ `x_proto=1`), jamais plus. Ce réglage n'est pas branché dans le dépôt :
  à activer soi-même selon l'architecture.
- **Jamais de confiance aveugle en `X-Forwarded-Proto`** : l'application ne lit pas cet en-tête aujourd'hui ; un
  compteur supérieur au nombre réel de proxys laisserait un client forcer le schéma perçu.

## Variables d'environnement

Depuis `.env.example` (valeurs factices, jamais en dur dans le dépôt) :

| Variable | Rôle | Par défaut |
|---|---|---|
| `SECRET_KEY` | Clé secrète Flask | ⚠️ à définir |
| `JWT_SECRET_KEY` | Clé secrète JWT | ⚠️ à définir |
| `ADMIN_PASSWORD` | Mot de passe admin | ⚠️ à définir |
| `DATABASE_URL` | URL base de données SQLite | `sqlite:///cloudinventory.db` |
| `SMTP_ENABLED` | Envoi SMTP activé | `false` |
| `SMTP_HOST` | Serveur SMTP | `localhost` |
| `SMTP_PORT` | Port SMTP | `587` |
| `SMTP_USE_TLS` | TLS pour SMTP | `true` |
| `SMTP_USERNAME` | Identifiant SMTP | ⚠️ |
| `SMTP_PASSWORD` | Mot de passe SMTP | ⚠️ |
| `SMTP_FROM` | Expéditeur SMTP | `cloudinventory@localhost` |
| `WEBHOOK_URL` | URL webhook | ⚠️ ou vide |
| `EXPORT_ENABLED` | Export activé | `false` |
| `EXPORT_LOCAL_PATH` | Chemin exports | `exports` |
| `EXPORT_SMB_PATH` | Chemin SMB | ⚠️ ou vide |
| `EXPORT_SMB_USERNAME` | User SMB | ⚠️ |
| `EXPORT_SMB_PASSWORD` | Mot de passe SMB | ⚠️ |
| `EXPORT_RETENTION_CONSOLIDATED` | Conservation consolidé (jours) | `30` |
| `EXPORT_RETENTION_RAW` | Conservation brut (jours) | `7` |
| `EXPORT_RAW_ENABLED` | Export brut activé | `false` |

Aucune valeur secrète réelle n'est committée ; les valeurs réelles sont injectées hors dépôt.

## Smoke test

Deux profils ; sans profil, le script refuse de tourner :
- `SMOKE_PROFILE=test make smoke` : démarre une application isolée (base temporaire, sources simulées, mot de passe
  généré) et la parcourt ;
- `SMOKE_PROFILE=docker make smoke` : après `make up`, parcourt le conteneur sur `127.0.0.1:5000` (ou
  `SMOKE_APP_URL`, hôte local seulement), avec le mot de passe administrateur lu dans `.env`.
Même parcours : `/healthz`, `/login` avec jeton CSRF, `/run`, `/api/login`, `/api/stats` ; aucun secret dans les
arguments de `curl`.

## Arborescence (après build)

- `app/` — factory `create_app()`, config, extensions db, auth, notifications
- `tests/` — pytest : fixtures app/db (SQLite en mémoire), un fichier de tests par module, `make test`
- `Makefile` — cibles install, test, verify, smoke, up/down/logs
- `docker-compose.yml` — service app, volume sqlite_data pour persistance
- `Dockerfile` — `python:3.11-slim-bookworm`, gunicorn production, user non-root

## Tests

```bash
make verify
```

Exécute pytest en ignorant `reference/` ; code 5 (suite vide) toléré.