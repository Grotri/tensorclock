from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from dotenv import load_dotenv

load_dotenv()
from typing import Any, Sequence

from utils.config_utils import cfg_get, load_toml_config
from utils.version import DB_SCHEMA_VERSION

DEFAULT_VALIDATOR_CONFIG_PATH = _ROOT / "configs" / "validator_config.toml"


def _database_url_from_validator_config(config_path: str | Path) -> str:
    cfg = load_toml_config(str(config_path))
    return str(cfg_get(cfg, "validator.database_url", "")).strip()


def resolve_database_url(
    *,
    explicit: str | None = None,
    config_path: str | Path | None = None,
) -> str:
    """
    Resolve DB URL: explicit (--db) > DATABASE_URL > validator.database_url in TOML.
    """
    if explicit and str(explicit).strip():
        return str(explicit).strip()
    env = os.getenv("DATABASE_URL", "").strip()
    if env:
        return env
    path = Path(config_path) if config_path is not None else DEFAULT_VALIDATOR_CONFIG_PATH
    if not path.is_absolute():
        path = _ROOT / path
    if not path.is_file():
        raise RuntimeError(
            f"Database URL not set: DATABASE_URL is empty and config file not found: {path}\n"
            "Set DATABASE_URL, pass --db, or create the config with validator.database_url."
        )
    url = _database_url_from_validator_config(path)
    if not url:
        raise RuntimeError(
            f"validator.database_url is empty in {path}. "
            "Set it to a PostgreSQL URL or use DATABASE_URL / --db."
        )
    return url


def _get_database_url() -> str:
    return resolve_database_url()


def default_db_path() -> str:
    return _get_database_url()


def _is_postgres_url(target: str) -> bool:
    return target.startswith("postgres://") or target.startswith("postgresql://")


def _qmark_to_postgres_placeholders(query: str) -> str:
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
    url = (db_url or resolve_database_url()).strip()
    if not url or not _is_postgres_url(url):
        raise RuntimeError(
            "A PostgreSQL URL is required (postgresql://...). "
            "Set validator.database_url in configs/validator_config.toml, DATABASE_URL, or pass --db."
        )
    import psycopg
    from psycopg.rows import dict_row

    raw = psycopg.connect(url, row_factory=dict_row)
    return DBConnection(raw=raw)


def init_db(db_url: str | None = None) -> None:
    url = db_url or _get_database_url()
    with connect(url) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS meta (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            """
        )

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
                miner_hotkey TEXT NULL,
                dollar_value double precision NULL,
                weight INTEGER NOT NULL DEFAULT 0
            );
            """
        )

        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_publications_miner_active
            ON publications (miner_uid, state);
            """
        )

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS hashprice_cache (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                usd_per_th_per_day double precision NOT NULL,
                btc_per_th_per_day double precision NOT NULL,
                btc_usd double precision NOT NULL,
                updated_at TEXT NOT NULL
            );
            """
        )

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
                dollar_value double precision NULL,

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
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")

        _ensure_column("devices", "creator_version", "TEXT NOT NULL DEFAULT '0'")
        _ensure_column("devices", "schema_version", "TEXT NOT NULL DEFAULT '0'")
        _ensure_column("devices", "is_active", "INTEGER NOT NULL DEFAULT 1")
        _ensure_column("tasks", "creator_version", "TEXT NOT NULL DEFAULT '0'")
        _ensure_column("tasks", "schema_version", "TEXT NOT NULL DEFAULT '0'")
        _ensure_column("publications", "publication_deadline_at", "TEXT NULL")
        _ensure_column("publications", "total_tasks_expected", "INTEGER NULL")
        _ensure_column("publications", "miner_hotkey", "TEXT NULL")
        _ensure_column("publications", "dollar_value", "double precision NULL")
        _ensure_column("publications", "weight", "INTEGER NOT NULL DEFAULT 0")
        _ensure_column("assignments", "dollar_value", "double precision NULL")

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

        conn.execute(
            """
            INSERT INTO meta(key, value)
            VALUES('db_schema_version', %s)
            ON CONFLICT(key) DO UPDATE SET value = EXCLUDED.value
            """,
            (DB_SCHEMA_VERSION,),
        )


def reset_db(db_url: str | None = None) -> None:
    url = db_url or _get_database_url()
    if not _is_postgres_url(url):
        raise RuntimeError("reset_db requires a PostgreSQL URL (DATABASE_URL or --db).")
    with connect(url) as conn:
        conn.execute("DROP TABLE IF EXISTS hashprice_cache CASCADE")
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
        help="PostgreSQL URL (overrides DATABASE_URL and validator.database_url in config).",
    )
    parser.add_argument(
        "--config",
        type=str,
        default=str(DEFAULT_VALIDATOR_CONFIG_PATH),
        help="Validator TOML to read validator.database_url when DATABASE_URL and --db are unset.",
    )
    args = parser.parse_args()

    try:
        db_url = resolve_database_url(explicit=args.db, config_path=args.config)
    except RuntimeError as e:
        parser.error(str(e))
    if not _is_postgres_url(db_url):
        parser.error("Database URL must be postgresql://... or postgres://...")

    if args.reset:
        reset_db(db_url)
    init_db(db_url)

    display = db_url.split("@")[-1] if "@" in db_url else db_url
    print(f"[OK] Initialized DB at {display}")


if __name__ == "__main__":
    main()
