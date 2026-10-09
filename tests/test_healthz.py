"""Tests pour l'endpoint /healthz (LOW-02, CODE-05).

C1 : 200 si DB OK (SELECT 1 + lecture bornée Asset)
C2 : 503 si DB corrompue/indisponible (rollback, réponse générique)
C3 : Pas de fuite en corps/headers (réponse vide, pas de trace, pas de config)
"""
import pytest
from sqlalchemy import text
from sqlalchemy.exc import OperationalError


def test_healthz_returns_200_when_db_ok(app):
    """DB utilisable → 200, corps vide, pas de fuite."""
    client = app.test_client()

    response = client.get("/healthz")

    assert response.status_code == 200
    assert response.get_data(as_text=True) == ""
    # Pas d'en-têtes sensibles (hors en-têtes de sécurité standards X-Content-Type-Options, X-Frame-Options)
    custom_x_headers = [h for h in response.headers.keys() if h.startswith("X-") and h not in ("X-Content-Type-Options", "X-Frame-Options")]
    assert not custom_x_headers, f"En-têtes X- personnalisés suspects : {custom_x_headers}"
    assert "Server" not in response.headers
    # Pas de trace d'erreur ni de configuration
    assert "trace" not in response.get_data(as_text=True).lower()
    assert "sqlalchemy" not in response.get_data(as_text=True).lower()


def test_healthz_returns_503_when_db_unavailable(app, monkeypatch):
    """Connexion DB cassée → 503, rollback, réponse générique sans fuite."""
    client = app.test_client()

    # Casser la session DB pour simuler une indisponibilité
    import app.extensions as ext

    def boom_execute(*args, **kwargs):
        raise OperationalError("DB unavailable", None, None)

    monkeypatch.setattr(ext.db.session, "execute", boom_execute)

    response = client.get("/healthz")

    assert response.status_code == 503
    assert response.get_data(as_text=True) == ""
    # Pas de détails d'erreur dans le corps
    assert "unavailable" not in response.get_data(as_text=True).lower()
    assert "operational" not in response.get_data(as_text=True).lower()
    assert "traceback" not in response.get_data(as_text=True).lower()


def test_healthz_returns_503_when_table_missing(app, monkeypatch):
    """Table métier requise absente → 503 (lecture bornée Asset échoue)."""
    client = app.test_client()

    import app.extensions as ext
    from sqlalchemy.exc import ProgrammingError

    original_query = ext.db.session.query

    def failing_query(*args, **kwargs):
        # Vérifier si la requête cible Asset.id (colonne de la table asset)
        for arg in args:
            # arg peut être Asset.id (InstrumentedAttribute) ou Asset (modèle)
            if hasattr(arg, 'table') and hasattr(arg.table, 'name') and arg.table.name == 'asset':
                # Créer un query qui va échouer sur first()
                query = original_query(*args, **kwargs)
                original_first = query.first
                def failing_first(self=query):
                    raise ProgrammingError("Table asset missing", None, None)
                query.first = failing_first
                return query
        return original_query(*args, **kwargs)

    monkeypatch.setattr(ext.db.session, "query", failing_query)

    response = client.get("/healthz")

    assert response.status_code == 503
    assert response.get_data(as_text=True) == ""


def test_healthz_no_leakage_in_headers(app):
    """Aucun en-tête ne fuit de configuration, chemins, ou état détaillé."""
    client = app.test_client()

    response = client.get("/healthz")

    # Vérifier les en-têtes de sécurité standards présents
    assert response.headers.get("X-Content-Type-Options") == "nosniff"
    assert response.headers.get("X-Frame-Options") == "DENY"
    assert response.headers.get("Referrer-Policy") == "same-origin"
    # Pas d'en-têtes personnalisés qui fuiraient l'état
    custom_headers = [h for h in response.headers.keys() if h.startswith("X-") and h not in ("X-Content-Type-Options", "X-Frame-Options")]
    assert not custom_headers, f"En-têtes personnalisés suspects : {custom_headers}"


def test_healthz_session_restored_after_error(app, monkeypatch):
    """Après une erreur DB, la session est restaurée (rollback effectif)."""
    client = app.test_client()

    import app.extensions as ext
    from sqlalchemy.exc import OperationalError

    # Premier appel : erreur
    def boom_execute(*args, **kwargs):
        raise OperationalError("DB unavailable", None, None)

    monkeypatch.setattr(ext.db.session, "execute", boom_execute)
    response1 = client.get("/healthz")
    assert response1.status_code == 503

    # Retirer le monkeypatch pour le deuxième appel
    monkeypatch.undo()
    response2 = client.get("/healthz")
    assert response2.status_code == 200


def test_healthz_bounded_no_external_calls(app, monkeypatch):
    """Endpoint borné : aucune écriture, aucune requête externe, lecture limitée."""
    client = app.test_client()

    import app.extensions as ext
    from sqlalchemy.orm import Query

    calls = []

    original_execute = ext.db.session.execute

    def tracking_execute(statement, *args, **kwargs):
        calls.append(("execute", str(statement)[:100]))
        return original_execute(statement, *args, **kwargs)

    original_first = Query.first

    def tracking_first(query):
        statement = query.statement.compile()
        if "asset" in str(statement):
            calls.append(("query", "asset"))
            assert "LIMIT" in str(statement)
            assert 1 in statement.params.values()
        return original_first(query)

    monkeypatch.setattr(ext.db.session, "execute", tracking_execute)
    monkeypatch.setattr(Query, "first", tracking_first)

    response = client.get("/healthz")

    assert response.status_code == 200
    # Vérifier que seules les requêtes attendues sont exécutées
    select1_calls = [c for c in calls if c[0] == "execute" and "SELECT 1" in c[1]]
    asset_calls = [c for c in calls if c[0] == "query" and c[1] == "asset"]
    assert len(select1_calls) == 1, f"SELECT 1 appelé {len(select1_calls)} fois"
    assert len(asset_calls) == 1, f"Requête Asset appelée {len(asset_calls)} fois"
    # Pas d'INSERT, UPDATE, DELETE
    write_calls = [c for c in calls if c[0] == "execute" and any(kw in c[1].upper() for kw in ("INSERT", "UPDATE", "DELETE"))]
    assert not write_calls, f"Écritures détectées : {write_calls}"
