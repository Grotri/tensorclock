"""
Initialize the validator SQLite database schema.

Usage:
  python init_db.py
  python init_db.py --reset

Creates/updates `data/validator.db` with the tables used by Task Manager.
"""

from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path

from version import DB_SCHEMA_VERSION


def default_db_path() -> Path:
    return Path("data") / "validator.db"


def connect(db_path: Path | str) -> sqlite3.Connection:
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    # Safety + reasonable performance for validator workload
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.execute("PRAGMA journal_mode = WAL;")
    conn.execute("PRAGMA synchronous = NORMAL;")
    conn.execute("PRAGMA busy_timeout = 5000;")
    return conn


def init_db(db_path: Path | str = default_db_path()) -> None:
    """
    Initialize SQLite schema for devices/tasks/assignments.
    Safe to call multiple times.
    """
    db_path = Path(db_path)
    with connect(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS meta (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS devices (
                device_id TEXT PRIMARY KEY,
                asic_model TEXT NOT NULL,
                electricity_price REAL NOT NULL,
                created_at TEXT NOT NULL,
                creator_version TEXT NOT NULL DEFAULT '0',
                schema_version TEXT NOT NULL DEFAULT '0',
                is_active INTEGER NOT NULL DEFAULT 1,

                -- Hidden parameters
                silicon_quality REAL NOT NULL,
                degradation REAL NOT NULL,
                thermal_resistance REAL NOT NULL,

                -- Base specification
                spec_name TEXT NOT NULL,
                spec_manufacturer TEXT NOT NULL,
                nominal_hashrate REAL NOT NULL,
                nominal_power REAL NOT NULL,
                hashrate_per_mhz REAL NOT NULL,
                optimal_voltage REAL NOT NULL,
                base_thermal_resistance REAL NOT NULL,
                manufacturer_frequency REAL NOT NULL,
                efficiency REAL NOT NULL,
                C REAL NOT NULL,

                -- Hardware limits
                min_frequency REAL NOT NULL,
                max_frequency REAL NOT NULL,
                min_voltage REAL NOT NULL,
                max_voltage REAL NOT NULL,
                max_safe_temperature REAL NOT NULL,
                min_fan_speed REAL NOT NULL,
                max_fan_speed REAL NOT NULL,
                min_power REAL NOT NULL,
                max_power REAL NOT NULL,

                -- Full JSON snapshot for forward compatibility
                device_json TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_devices_model_created
            ON devices (asic_model, created_at);

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

            CREATE INDEX IF NOT EXISTS idx_tasks_lookup
            ON tasks (asic_model, status, expires_at);

            CREATE TABLE IF NOT EXISTS assignments (
                task_id TEXT NOT NULL,
                miner_uid INTEGER NOT NULL,
                query_budget INTEGER NOT NULL,
                queries_used INTEGER NOT NULL DEFAULT 0,
                assigned_at TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                state TEXT NOT NULL DEFAULT 'active',
                PRIMARY KEY (task_id, miner_uid),
                FOREIGN KEY(task_id) REFERENCES tasks(task_id) ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_assignments_miner
            ON assignments (miner_uid, state);
            """
        )

        # Lightweight migrations for existing DBs (add columns if missing)
        def _ensure_column(table: str, column: str, decl: str) -> None:
            cols = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}
            if column not in cols:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")

        _ensure_column("devices", "creator_version", "TEXT NOT NULL DEFAULT '0'")
        _ensure_column("devices", "schema_version", "TEXT NOT NULL DEFAULT '0'")
        _ensure_column("devices", "is_active", "INTEGER NOT NULL DEFAULT 1")
        _ensure_column("tasks", "creator_version", "TEXT NOT NULL DEFAULT '0'")
        _ensure_column("tasks", "schema_version", "TEXT NOT NULL DEFAULT '0'")

        # Persist current schema version in meta table
        conn.execute(
            "INSERT INTO meta(key, value) VALUES('db_schema_version', ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (DB_SCHEMA_VERSION,),
        )


def reset_db(db_path: Path | str = default_db_path()) -> None:
    """
    Delete the SQLite database file (and WAL/SHM sidecars) to recreate schema from scratch.
    """
    db_path = Path(db_path)
    for p in (db_path, db_path.with_suffix(db_path.suffix + "-wal"), db_path.with_suffix(db_path.suffix + "-shm")):
        if p.exists():
            p.unlink()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--reset", action="store_true", help="Delete existing DB and recreate schema")
    parser.add_argument("--db", type=str, default=str(default_db_path()), help="Path to SQLite DB")
    args = parser.parse_args()

    if args.reset:
        reset_db(args.db)
    init_db(args.db)
    print(f"[OK] Initialized DB at {Path(args.db)}")


if __name__ == "__main__":
    main()

