"""T014 — Conformité du schéma SQLAlchemy ↔ docs/modele/schema.sql.

Deux bases SQLite indépendantes sont comparées par PRAGMA : la base d'application
(créée par `db.create_all()` dans `create_app()`) et la référence bâtie en
exécutant `docs/modele/schema.sql`. Portée (consigne T014) : tables, colonnes
(type, NOT NULL, PK), clés étrangères (cible, ON DELETE/UPDATE), index (nom,
unicité, colonnes). Les tests « détecte_* » simulent un écart pour prouver que
la comparaison échoue quand le modèle diverge.
"""
import copy
import pathlib
import sqlite3

from app.extensions import db

SCHEMA_SQL = (
    pathlib.Path(__file__).resolve().parents[1] / "docs" / "modele" / "schema.sql"
)

# DDL attendu (docs/modele/schema.sql, « Ordre de création »).
EXPECTED_TABLES = {"run", "ipam_record", "asset", "consolidated_asset", "anomaly"}

# Clés du dictionnaire renvoyé par _gaps().
DIMENSIONS = ("tables", "columns", "indexes", "foreign_keys")


def _norm_type(raw):
    """Type déclaré comparable : casse et espaces normalisés (sortie PRAGMA)."""
    return " ".join(str(raw).upper().split())


def _snapshot(run_sql):
    """Snapshot normalisé d'une base SQLite : {table: {columns, indexes, fks}}."""
    tables = [
        row[0]
        for row in run_sql(
            "SELECT name FROM sqlite_master"
            " WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
            " ORDER BY name"
        )
    ]
    schema = {}
    for table in tables:
        columns = {}
        for _cid, name, ctype, notnull, _dflt, pk in run_sql(
            f"PRAGMA table_info(`{table}`)"
        ):
            columns[name] = {
                "type": _norm_type(ctype),
                "notnull": bool(notnull),
                "pk": pk,
            }
        indexes = {}
        for _seq, iname, unique, _origin, _partial in run_sql(
            f"PRAGMA index_list(`{table}`)"
        ):
            icols = tuple(
                row[2] for row in run_sql(f"PRAGMA index_info(`{iname}`)")
            )
            indexes[iname] = {"unique": bool(unique), "columns": icols}
        # PRAGMA foreign_key_list : id, seq, table référée, from, to, on_update, on_delete, match
        fks = {
            (row[3], row[2], row[4], row[5], row[6])
            for row in run_sql(f"PRAGMA foreign_key_list(`{table}`)")
        }
        schema[table] = {"columns": columns, "indexes": indexes, "foreign_keys": fks}
    return schema


def _app_snapshot(app):
    """Snapshot de la base d'application (connexion SQLite réelle de l'engine)."""
    with app.app_context():
        raw = db.engine.raw_connection()
        try:
            cursor = raw.cursor()

            def run_sql(query):
                return [tuple(row) for row in cursor.execute(query).fetchall()]

            return _snapshot(run_sql)
        finally:
            raw.close()


def _reference_snapshot():
    """Snapshot de docs/modele/schema.sql exécuté dans une base mémoire vierge."""
    connection = sqlite3.connect(":memory:")
    try:
        connection.executescript(SCHEMA_SQL.read_text(encoding="utf-8"))
        cursor = connection.cursor()

        def run_sql(query):
            return [tuple(row) for row in cursor.execute(query).fetchall()]

        return _snapshot(run_sql)
    finally:
        connection.close()


def _gaps(app_schema, reference_schema):
    """Écarts lisibles par dimension ; liste vide = conforme."""
    gaps = {dimension: [] for dimension in DIMENSIONS}
    for table in sorted(set(app_schema) | set(reference_schema)):
        if table not in reference_schema:
            gaps["tables"].append(f"{table} : table en trop dans l'application")
            continue
        if table not in app_schema:
            gaps["tables"].append(f"{table} : table absente de l'application")
            continue
        app_table = app_schema[table]
        ref_table = reference_schema[table]

        app_columns = app_table["columns"]
        ref_columns = ref_table["columns"]
        for column in sorted(set(app_columns) | set(ref_columns)):
            if column not in app_columns:
                gaps["columns"].append(f"{table}.{column} : colonne absente du modèle")
            elif column not in ref_columns:
                gaps["columns"].append(f"{table}.{column} : colonne en trop dans le modèle")
            else:
                for field in ("type", "notnull", "pk"):
                    app_value = app_columns[column][field]
                    ref_value = ref_columns[column][field]
                    if app_value != ref_value:
                        gaps["columns"].append(
                            f"{table}.{column} : {field}"
                            f" modèle={app_value!r} schema.sql={ref_value!r}"
                        )

        app_indexes = app_table["indexes"]
        ref_indexes = ref_table["indexes"]
        for index in sorted(set(app_indexes) | set(ref_indexes)):
            if index not in app_indexes:
                gaps["indexes"].append(f"{table} : index {index} absent du modèle")
            elif index not in ref_indexes:
                gaps["indexes"].append(f"{table} : index {index} en trop dans le modèle")
            elif app_indexes[index] != ref_indexes[index]:
                gaps["indexes"].append(
                    f"{table} : index {index}"
                    f" modèle={app_indexes[index]} schema.sql={ref_indexes[index]}"
                )

        for fk in sorted(ref_table["foreign_keys"] - app_table["foreign_keys"], key=repr):
            gaps["foreign_keys"].append(f"{table} : FK absente du modèle {fk}")
        for fk in sorted(app_table["foreign_keys"] - ref_table["foreign_keys"], key=repr):
            gaps["foreign_keys"].append(f"{table} : FK en trop dans le modèle {fk}")
    return gaps


def _only(gaps, dimension):
    """Message d'échec listant les écarts d'une dimension."""
    return "\n" + "\n".join(gaps[dimension])


# --- C1 — le schéma d'application est identique à schema.sql -----------------


def test_reference_schema_has_five_tables():
    """docs/modele/schema.sql s'exécute et livre bien les 5 tables attendues."""
    assert set(_reference_snapshot()) == EXPECTED_TABLES


def test_tables_match_schema_sql(app):
    """Les tables de schema.sql existent dans la base d'application, sans en trop."""
    gaps = _gaps(_app_snapshot(app), _reference_snapshot())

    assert gaps["tables"] == [], _only(gaps, "tables")


def test_columns_match_schema_sql(app):
    """Chaque colonne a le même type, la même nullabilité et la même PK."""
    gaps = _gaps(_app_snapshot(app), _reference_snapshot())

    assert gaps["columns"] == [], _only(gaps, "columns")


def test_indexes_match_schema_sql(app):
    """Les index ont le même nom, la même unicité et les mêmes colonnes."""
    gaps = _gaps(_app_snapshot(app), _reference_snapshot())

    assert gaps["indexes"] == [], _only(gaps, "indexes")


def test_foreign_keys_match_schema_sql(app):
    """Les 7 FK ciblent les mêmes colonnes avec les mêmes ON DELETE/UPDATE."""
    gaps = _gaps(_app_snapshot(app), _reference_snapshot())

    assert gaps["foreign_keys"] == [], _only(gaps, "foreign_keys")


def test_consolidated_asset_run_id_maps_to_run(app):
    """T024 — `consolidated_asset.run_id` : NOT NULL, FK → run, index (RG35)."""
    reference = _reference_snapshot()["consolidated_asset"]
    application = _app_snapshot(app)["consolidated_asset"]

    for snapshot in (reference, application):
        assert snapshot["columns"]["run_id"]["notnull"] is True
        assert ("run_id", "run", "id", "CASCADE", "RESTRICT") in snapshot["foreign_keys"]
        assert snapshot["indexes"]["idx_consolidated_asset_run_id"]["unique"] is False


# --- C2 — la comparaison détecte les écarts (test de non-régression) ---------


def test_detects_missing_table():
    """Une table absente de l'application est signalée (écart simulé)."""
    reference = _reference_snapshot()
    app_schema = {name: snap for name, snap in reference.items() if name != "anomaly"}

    gaps = _gaps(app_schema, reference)

    assert gaps["tables"] == ["anomaly : table absente de l'application"]


def test_detects_column_type_drift():
    """Un type de colonne divergent est signalé (écart simulé)."""
    reference = _reference_snapshot()
    app_schema = copy.deepcopy(reference)
    app_schema["asset"]["columns"]["vm_id"]["type"] = "VARCHAR(51)"

    gaps = _gaps(app_schema, reference)

    assert any(
        "asset.vm_id" in gap and "type" in gap for gap in gaps["columns"]
    ), _only(gaps, "columns")


def test_detects_missing_column():
    """Une colonne absente du modèle est signalée (écart simulé)."""
    reference = _reference_snapshot()
    app_schema = copy.deepcopy(reference)
    del app_schema["ipam_record"]["columns"]["dns_name"]

    gaps = _gaps(app_schema, reference)

    assert gaps["columns"] == ["ipam_record.dns_name : colonne absente du modèle"]


def test_detects_nullability_drift():
    """Une colonne NOT NULL devenue nullable est signalée (écart simulé)."""
    reference = _reference_snapshot()
    app_schema = copy.deepcopy(reference)
    app_schema["run"]["columns"]["status"]["notnull"] = False

    gaps = _gaps(app_schema, reference)

    assert any(
        "run.status" in gap and "notnull" in gap for gap in gaps["columns"]
    ), _only(gaps, "columns")


def test_detects_missing_unique_index():
    """Un index UNIQUE perdu par le modèle est signalé (écart simulé)."""
    reference = _reference_snapshot()
    app_schema = copy.deepcopy(reference)
    app_schema["asset"]["indexes"]["uk_asset_vm_id"] = {
        "unique": False,
        "columns": ("vm_id",),
    }

    gaps = _gaps(app_schema, reference)

    assert any(
        "uk_asset_vm_id" in gap for gap in gaps["indexes"]
    ), _only(gaps, "indexes")


def test_detects_foreign_key_rule_drift():
    """Une règle ON DELETE divergente est signalée (écart simulé)."""
    reference = _reference_snapshot()
    app_schema = copy.deepcopy(reference)
    altered = {
        ("consolidated_run_id", "run", "id", "CASCADE", "CASCADE"),
    }
    app_schema["asset"]["foreign_keys"] = altered

    gaps = _gaps(app_schema, reference)

    assert len(gaps["foreign_keys"]) == 2, _only(gaps, "foreign_keys")
