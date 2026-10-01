"""Подключение к PostgreSQL и простой раннер SQL-миграций."""
from __future__ import annotations

import logging
from pathlib import Path

import psycopg
from psycopg.rows import dict_row

from .config import get_settings

log = logging.getLogger(__name__)


def connect(database_url: str | None = None, **kwargs) -> psycopg.Connection:
    return psycopg.connect(database_url or get_settings().database_url, row_factory=dict_row, **kwargs)


def migrate(conn: psycopg.Connection, migrations_dir: Path | None = None) -> list[str]:
    """Применяет недостающие миграции в лексикографическом порядке; каждая — в своей транзакции."""
    migrations_dir = migrations_dir or get_settings().migrations_dir
    with conn.transaction():
        conn.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations ("
            " version text PRIMARY KEY, applied_at timestamptz NOT NULL DEFAULT now())"
        )
    applied = {r["version"] for r in conn.execute("SELECT version FROM schema_migrations").fetchall()}
    done: list[str] = []
    for path in sorted(migrations_dir.glob("*.sql")):
        if path.stem in applied:
            continue
        with conn.transaction():
            # Блокировка на случай одновременного запуска api и worker
            conn.execute("SELECT pg_advisory_xact_lock(726354)")
            if conn.execute("SELECT 1 FROM schema_migrations WHERE version=%s", (path.stem,)).fetchone():
                continue
            conn.execute(path.read_text(encoding="utf-8"))
            conn.execute("INSERT INTO schema_migrations(version) VALUES (%s)", (path.stem,))
        log.info("migration applied", extra={"version": path.stem})
        done.append(path.stem)
    return done
