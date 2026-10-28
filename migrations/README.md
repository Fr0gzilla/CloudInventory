# Baseline Alembic

Sur une base **vide**, configurer `DATABASE_URL` puis lancer `alembic upgrade head`.
Sans `DATABASE_URL`, Alembic utilise le chemin SQLite local indiqué dans `alembic.ini` ;
ne pas lancer la migration sur une base contenant déjà les tables.

Pour une base existante, sauvegarder la base, comparer d'abord **tout** son schéma
(colonnes, contraintes, clés étrangères et index) avec la baseline, puis seulement si
elle est identique, enregistrer la version sans DDL avec `alembic stamp head`.
Ne pas estampiller une base qui diffère ; la traiter par une migration spécifique.
`alembic downgrade base` supprime les cinq tables et leurs données : réserver cette
commande à une base jetable ou sauvegardée.

Les tests peuvent injecter une `sqlalchemy.engine.Connection` dans
`Config.attributes["connection"]` et appeler `alembic.command.upgrade(config, "head")`
sans créer d'application Flask ni de tables au préalable.
