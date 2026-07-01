from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import psycopg
from psycopg.rows import dict_row

ROOT_DIR = Path(__file__).resolve().parents[1]


def database_url() -> str:
    url = os.getenv("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL is required.")
    return url


def connect():
    return psycopg.connect(database_url(), row_factory=dict_row)


def ensure_schema() -> None:
    schema = (ROOT_DIR / "schema.sql").read_text(encoding="utf-8")
    with connect() as conn:
        conn.execute(schema)
        conn.execute(
            """
            create table if not exists schema_migrations (
              version text primary key,
              applied_at timestamptz not null default now()
            )
            """
        )
        migrations_dir = ROOT_DIR / "migrations"
        if migrations_dir.exists():
            for migration in sorted(migrations_dir.glob("*.sql")):
                version = migration.stem
                applied = conn.execute("select 1 from schema_migrations where version = %s", (version,)).fetchone()
                if applied:
                    continue
                conn.execute(migration.read_text(encoding="utf-8"))
                conn.execute("insert into schema_migrations (version) values (%s)", (version,))
        conn.commit()


def fetch_all(sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    with connect() as conn:
        return list(conn.execute(sql, params).fetchall())


def fetch_one(sql: str, params: tuple[Any, ...] = ()) -> dict[str, Any] | None:
    with connect() as conn:
        return conn.execute(sql, params).fetchone()


def execute(sql: str, params: tuple[Any, ...] = ()) -> dict[str, Any] | None:
    with connect() as conn:
        row = conn.execute(sql, params).fetchone()
        conn.commit()
        return row
