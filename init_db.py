"""
Initialize the validator PostgreSQL database schema.

Usage:
  Set DATABASE_URL in .env or in the environment, then:
  python init_db.py
  python init_db.py --reset

Requires DATABASE_URL (PostgreSQL URL). SQLite is not supported.
"""

from __future__ import annotations

import argparse
import os

from dotenv import load_dotenv

load_dotenv()
from typing import Any, Sequence

from version import DB_SCHEMA_VERSION


def _get_database_url() -> str:
    url = os.getenv("DATABASE_URL", "").strip()
    if not url:
        raise RuntimeError(
            "DATABASE_URL is not set. Set it to a PostgreSQL connection URL, e.g.:\n"
            "  export DATABASE_URL='postgresql://user:password@localhost:5432/tensorclock'"
        )
    return url


def default_db_path() -> str:
    """Return the database URL from DATABASE_URL (required)."""
    return _get_database_url()


def _is_postgres_url(target: str) -> bool:
    return target.startswith("postgres://") or target.startswith("postgresql://")


def _qmark_to_postgres_placeholders(query: str) -> str:
    """Convert '?' placeholders to '%s' for psycopg."""
    return query.replace("?", "%s")


class DBConnection:
    def __init__(self, raw: Any):
        self._raw = raw

    def execute(self, query: str, params: Sequence[Any] = ()) -> Any:
        if params is None:
            params = ()
        query = _qmark_to_postgres_placeholders(query)
        return self._raw.execute(query, params)

    def commit(self) -> None:
        self._raw.commit()

    def rollback(self) -> None:
        self._raw.rollback()

    def close(self) -> None:
        try:
            self._raw.close()
        except Exception:
            pass

    def __enter__(self) -> "DBConnection":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if exc_type is None:
            try:
                self._raw.commit()
            except Exception:
                pass
        else:
            try:
                self._raw.rollback()
            except Exception:
                pass
        self.close()


def connect(db_url: str | None = None) -> DBConnection:
    """
    Create a PostgreSQL connection. Uses DATABASE_URL if db_url is None.
    Returns a wrapper that converts '?' placeholders to '%s' and supports dict-like rows.
    """
    url = (db_url or os.getenv("DATABASE_URL", "")).strip()
    if not url or not _is_postgres_url(url):
        raise RuntimeError(
            "A PostgreSQL URL is required (postgresql://...). "
            "Set DATABASE_URL or pass --db postgresql://..."
        )
    import psycopg  # type: ignore
    from psycopg.rows import dict_row  # type: ignore

    raw = psycopg.connect(url, row_factory=dict_row)
    return DBConnection(raw=raw)


def init_db(db_url: str | None = None) -> None:
    """
    Initialize PostgreSQL schema for devices/tasks/assignments.
    Safe to call multiple times.
    """
    url = db_url or _get_database_url()
    with connect(url) as conn:
        # meta
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS meta (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            """
        )

        # devices
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS devices (
                device_id TEXT PRIMARY KEY,
                asic_model TEXT NOT NULL,
                electricity_price double precision NOT NULL,
                created_at TEXT NOT NULL,
                creator_version TEXT NOT NULL DEFAULT '0',
                schema_version TEXT NOT NULL DEFAULT '0',
                is_active INTEGER NOT NULL DEFAULT 1,

                silicon_quality double precision NOT NULL,
                degradation double precision NOT NULL,
                thermal_resistance double precision NOT NULL,

                spec_name TEXT NOT NULL,
                spec_manufacturer TEXT NOT NULL,
                nominal_hashrate double precision NOT NULL,
                nominal_power double precision NOT NULL,
                hashrate_per_mhz double precision NOT NULL,
                optimal_voltage double precision NOT NULL,
                base_thermal_resistance double precision NOT NULL,
                manufacturer_frequency double precision NOT NULL,
                efficiency double precision NOT NULL,
                C double precision NOT NULL,

                min_frequency double precision NOT NULL,
                max_frequency double precision NOT NULL,
                min_voltage double precision NOT NULL,
                max_voltage double precision NOT NULL,
                max_safe_temperature double precision NOT NULL,
                min_fan_speed double precision NOT NULL,
                max_fan_speed double precision NOT NULL,
                min_power double precision NOT NULL,
                max_power double precision NOT NULL,

                device_json TEXT NOT NULL
            );
            """
        )

        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_devices_model_created
            ON devices (asic_model, created_at);
            """
        )

        # tasks
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS tasks (
                task_id TEXT PRIMARY KEY,
                device_id TEXT NOT NULL,
                asic_model TEXT NOT NULL,
                ambient_level TEXT NOT NULL,
                query_budget INTEGER NOT NULL,
                target TEXT NOT NULL,
                created_at TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'open',
                creator_version TEXT NOT NULL DEFAULT '0',
                schema_version TEXT NOT NULL DEFAULT '0',
                FOREIGN KEY(device_id) REFERENCES devices(device_id) ON DELETE CASCADE
            );
            """
        )

        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_tasks_lookup
            ON tasks (asic_model, status, expires_at);
            """
        )

        # publications (one "publication" = miner-model result across exactly N tasks)
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS publications (
                publication_id TEXT PRIMARY KEY,
                miner_uid INTEGER NOT NULL,
                asic_model TEXT NOT NULL,
                target TEXT NOT NULL,
                query_budget INTEGER NOT NULL,

                tasks_creator_version TEXT NOT NULL,
                tasks_schema_version TEXT NOT NULL,

                model_description_json TEXT NULL,

                state TEXT NOT NULL DEFAULT 'active', -- active|cancelled|completed|expired
                created_at TEXT NOT NULL,
                completed_at TEXT NULL,
                avg_net_profit double precision NULL,
                publication_deadline_at TEXT NULL,
                total_tasks_expected INTEGER NULL,
                miner_hotkey TEXT NULL
            );
            """
        )

        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_publications_miner_active
            ON publications (miner_uid, state);
            """
        )

        # assignments (one row per task inside a publication)
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS assignments (
                publication_id TEXT NOT NULL,
                task_id TEXT NOT NULL,
                miner_uid INTEGER NOT NULL,

                query_budget INTEGER NOT NULL,
                queries_used INTEGER NOT NULL DEFAULT 0,
                assigned_at TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                state TEXT NOT NULL DEFAULT 'active',

                failure_reason TEXT NULL,
                net_profit double precision NULL,
                best_efficiency double precision NULL,
                completed_at TEXT NULL,

                PRIMARY KEY (publication_id, task_id),
                FOREIGN KEY(publication_id) REFERENCES publications(publication_id) ON DELETE CASCADE,
                FOREIGN KEY(task_id) REFERENCES tasks(task_id) ON DELETE CASCADE
            );
            """
        )

        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_assignments_publication
            ON assignments (publication_id, state);
            """
        )

        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_assignments_miner
            ON assignments (miner_uid, state);
            """
        )

        # Migrations: add columns if missing
        def _ensure_column(table: str, column: str, decl: str) -> None:
            exists = conn.execute(
                """
                SELECT 1
                FROM information_schema.columns
                WHERE table_name = %s AND column_name = %s
                """,
                (table, column),
            ).fetchone()
            if not exists:
                # PostgreSQL ADD COLUMN syntax
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")

        _ensure_column("devices", "creator_version", "TEXT NOT NULL DEFAULT '0'")
        _ensure_column("devices", "schema_version", "TEXT NOT NULL DEFAULT '0'")
        _ensure_column("devices", "is_active", "INTEGER NOT NULL DEFAULT 1")
        _ensure_column("tasks", "creator_version", "TEXT NOT NULL DEFAULT '0'")
        _ensure_column("tasks", "schema_version", "TEXT NOT NULL DEFAULT '0'")
        _ensure_column("publications", "publication_deadline_at", "TEXT NULL")
        _ensure_column("publications", "total_tasks_expected", "INTEGER NULL")
        _ensure_column("publications", "miner_hotkey", "TEXT NULL")

        # Backfill deadline for existing publications (created_at + 10 minutes)
        conn.execute(
            """
            UPDATE publications
            SET publication_deadline_at = (
                COALESCE(created_at::timestamptz, created_at::timestamp AT TIME ZONE 'UTC')
                + interval '10 minutes'
            )::text
            WHERE publication_deadline_at IS NULL
            """
        )

        # Persist schema version
        conn.execute(
            """
            INSERT INTO meta(key, value)
            VALUES('db_schema_version', %s)
            ON CONFLICT(key) DO UPDATE SET value = EXCLUDED.value
            """,
            (DB_SCHEMA_VERSION,),
        )


def reset_db(db_url: str | None = None) -> None:
    """
    Drop all managed tables and leave DB empty.
    Call init_db() after to recreate schema.
    """
    url = db_url or _get_database_url()
    if not _is_postgres_url(url):
        raise RuntimeError("reset_db requires a PostgreSQL URL (DATABASE_URL or --db).")
    with connect(url) as conn:
        conn.execute("DROP TABLE IF EXISTS publications CASCADE")
        conn.execute("DROP TABLE IF EXISTS assignments CASCADE")
        conn.execute("DROP TABLE IF EXISTS tasks CASCADE")
        conn.execute("DROP TABLE IF EXISTS devices CASCADE")
        conn.execute("DROP TABLE IF EXISTS meta CASCADE")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Initialize or reset TensorClock PostgreSQL schema."
    )
    parser.add_argument(
        "--reset",
        action="store_true",
        help="Drop managed tables first, then create schema.",
    )
    parser.add_argument(
        "--db",
        type=str,
        default=None,
        help="PostgreSQL URL (default: use DATABASE_URL from env).",
    )
    args = parser.parse_args()

    db_url = args.db or os.getenv("DATABASE_URL", "").strip()
    if not db_url:
        parser.error("DATABASE_URL is not set and --db was not given.")
    if not _is_postgres_url(db_url):
        parser.error("Database URL must be postgresql://... or postgres://...")

    if args.reset:
        reset_db(db_url)
    init_db(db_url)

    # Mask password in display
    display = db_url.split("@")[-1] if "@" in db_url else db_url
    print(f"[OK] Initialized DB at {display}")


if __name__ == "__main__":
    main()
