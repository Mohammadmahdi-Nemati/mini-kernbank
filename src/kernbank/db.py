"""Datenbankverbindung und Schema-Aufbau."""

import os
from pathlib import Path

import psycopg

DSN = os.environ.get("KERNBANK_DSN", "postgresql://postgres:postgres@localhost:5432/kernbank")
SQL_DIR = Path(__file__).resolve().parents[2] / "sql"


def connect(dsn: str | None = None, **kwargs) -> psycopg.Connection:
    return psycopg.connect(dsn or DSN, **kwargs)


def reset_schema(conn: psycopg.Connection) -> None:
    """Löscht alle Daten und baut das Schema aus den SQL-Dateien neu auf."""
    for path in sorted(SQL_DIR.glob("*.sql")):
        conn.execute(path.read_text(encoding="utf-8"))
    conn.commit()
