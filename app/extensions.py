"""Extensions Flask de l'application — instances uniques partagées par toute la codebase."""
import sqlite3

from sqlalchemy import event
from sqlalchemy.engine import Engine

from app import db, jwt, login_manager

__all__ = ["db", "login_manager", "jwt"]


@event.listens_for(Engine, "connect")
def _enable_sqlite_foreign_keys(dbapi_connection, connection_record):
    """SQLite n'applique les FK (RESTRICT/CASCADE) qu'avec PRAGMA foreign_keys = ON."""
    if not isinstance(dbapi_connection, sqlite3.Connection):
        return
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()
