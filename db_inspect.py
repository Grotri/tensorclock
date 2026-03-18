"""
DB inspection utility for TensorClock validator DB.

Prints a human-friendly summary of:
- devices (counts by asic_model, sample rows)
- tasks (counts by status/target, tasks per device, expiration)
- assignments (counts by state, top miners by queries_used)

Usage:
  python db_inspect.py
  python db_inspect.py --db postgresql://localhost/tensorclock
  python db_inspect.py --limit 10
  python db_inspect.py --json
"""

from __future__ import annotations

import argparse
import json
import os
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from init_db import connect, init_db
from version import DB_SCHEMA_VERSION, DEVICE_CREATOR_VERSION, TASK_CREATOR_VERSION


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _print_kv(title: str, rows: List[Dict[str, Any]]) -> None:
    if not rows:
        print(f"{title}: (none)")
        return
    print(title)
    for r in rows:
        parts = [f"{k}={v}" for k, v in r.items()]
        print(f"  - " + " | ".join(parts))


def _fetchall_dict(conn, sql: str, params: tuple = ()) -> List[Dict[str, Any]]:
    rows = conn.execute(sql, params).fetchall()
    return list(rows) if rows else []


@dataclass
class DbSummary:
    db_path: str
    now: str
    devices_total: int
    devices_by_model: List[Dict[str, Any]]
    devices_inactive: int
    devices_by_creator_version: List[Dict[str, Any]]
    tasks_total: int
    tasks_by_model: List[Dict[str, Any]]
    tasks_by_target: List[Dict[str, Any]]
    tasks_by_status: List[Dict[str, Any]]
    tasks_expired_open: int
    tasks_superseded: int
    tasks_by_creator_version: List[Dict[str, Any]]
    tasks_per_device: List[Dict[str, Any]]
    assignments_total: int
    assignments_by_state: List[Dict[str, Any]]
    assignments_top_miners: List[Dict[str, Any]]


def inspect_db(db_path: str, limit: int = 5) -> DbSummary:
    init_db(db_path)
    with connect(db_path) as conn:
        devices_total = conn.execute("SELECT COUNT(*) AS n FROM devices").fetchone()["n"]
        devices_inactive = conn.execute("SELECT COUNT(*) AS n FROM devices WHERE is_active = 0").fetchone()["n"]
        tasks_total = conn.execute("SELECT COUNT(*) AS n FROM tasks").fetchone()["n"]
        assignments_total = conn.execute("SELECT COUNT(*) AS n FROM assignments").fetchone()["n"]

        devices_by_model = _fetchall_dict(
            conn,
            """
            SELECT asic_model, COUNT(*) AS n
            FROM devices
            GROUP BY asic_model
            ORDER BY n DESC, asic_model ASC
            """,
        )

        devices_by_creator_version = _fetchall_dict(
            conn,
            """
            SELECT creator_version, COUNT(*) AS n
            FROM devices
            GROUP BY creator_version
            ORDER BY n DESC, creator_version ASC
            """,
        )

        tasks_by_model = _fetchall_dict(
            conn,
            """
            SELECT asic_model, COUNT(*) AS n
            FROM tasks
            GROUP BY asic_model
            ORDER BY n DESC, asic_model ASC
            """,
        )
        tasks_by_target = _fetchall_dict(
            conn,
            """
            SELECT target, COUNT(*) AS n
            FROM tasks
            GROUP BY target
            ORDER BY n DESC, target ASC
            """,
        )
        tasks_by_status = _fetchall_dict(
            conn,
            """
            SELECT status, COUNT(*) AS n
            FROM tasks
            GROUP BY status
            ORDER BY n DESC, status ASC
            """,
        )

        now = _now_iso()
        tasks_expired_open = conn.execute(
            "SELECT COUNT(*) AS n FROM tasks WHERE status='open' AND expires_at <= ?",
            (now,),
        ).fetchone()["n"]
        tasks_superseded = conn.execute(
            "SELECT COUNT(*) AS n FROM tasks WHERE status='superseded'",
        ).fetchone()["n"]

        tasks_by_creator_version = _fetchall_dict(
            conn,
            """
            SELECT creator_version, COUNT(*) AS n
            FROM tasks
            GROUP BY creator_version
            ORDER BY n DESC, creator_version ASC
            """,
        )

        tasks_per_device = _fetchall_dict(
            conn,
            """
            SELECT device_id, COUNT(*) AS n
            FROM tasks
            GROUP BY device_id
            ORDER BY n DESC, device_id ASC
            LIMIT ?
            """,
            (limit,),
        )

        assignments_by_state = _fetchall_dict(
            conn,
            """
            SELECT state, COUNT(*) AS n
            FROM assignments
            GROUP BY state
            ORDER BY n DESC, state ASC
            """,
        )

        assignments_top_miners = _fetchall_dict(
            conn,
            """
            SELECT miner_uid, SUM(queries_used) AS queries_used, COUNT(*) AS assignments
            FROM assignments
            GROUP BY miner_uid
            ORDER BY queries_used DESC, assignments DESC, miner_uid ASC
            LIMIT ?
            """,
            (limit,),
        )

        return DbSummary(
            db_path=db_path,
            now=now,
            devices_total=int(devices_total),
            devices_by_model=devices_by_model,
            devices_inactive=int(devices_inactive),
            devices_by_creator_version=devices_by_creator_version,
            tasks_total=int(tasks_total),
            tasks_by_model=tasks_by_model,
            tasks_by_target=tasks_by_target,
            tasks_by_status=tasks_by_status,
            tasks_expired_open=int(tasks_expired_open),
            tasks_superseded=int(tasks_superseded),
            tasks_by_creator_version=tasks_by_creator_version,
            tasks_per_device=tasks_per_device,
            assignments_total=int(assignments_total),
            assignments_by_state=assignments_by_state,
            assignments_top_miners=assignments_top_miners,
        )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=str, default=None, help="PostgreSQL URL (default: DATABASE_URL)")
    parser.add_argument("--limit", type=int, default=5, help="How many sample rows to show")
    parser.add_argument("--json", action="store_true", help="Print as JSON instead of text")
    args = parser.parse_args()

    db_path = args.db or os.environ.get("DATABASE_URL", "")
    if not db_path:
        parser.error("DATABASE_URL is not set and --db was not given.")
    summary = inspect_db(db_path=db_path, limit=args.limit)

    if args.json:
        print(json.dumps(asdict(summary), indent=2))
        return

    print("=" * 72)
    print("TensorClock DB Inspect")
    print("=" * 72)
    print(f"DB: {summary.db_path}")
    print(f"Now: {summary.now}")
    print(f"Expected versions: schema={DB_SCHEMA_VERSION}, device={DEVICE_CREATOR_VERSION}, task={TASK_CREATOR_VERSION}")
    print()

    print(f"Devices: {summary.devices_total}")
    _print_kv("Devices by model:", summary.devices_by_model)
    _print_kv("Devices by creator_version:", summary.devices_by_creator_version)
    print(f"Inactive devices: {summary.devices_inactive}")
    print()

    print(f"Tasks: {summary.tasks_total}")
    _print_kv("Tasks by model:", summary.tasks_by_model)
    _print_kv("Tasks by target:", summary.tasks_by_target)
    _print_kv("Tasks by status:", summary.tasks_by_status)
    print(f"Open tasks expired: {summary.tasks_expired_open}")
    print(f"Superseded tasks: {summary.tasks_superseded}")
    _print_kv("Tasks by creator_version:", summary.tasks_by_creator_version)
    _print_kv(f"Tasks per device (top {args.limit}):", summary.tasks_per_device)
    print()

    print(f"Assignments: {summary.assignments_total}")
    _print_kv("Assignments by state:", summary.assignments_by_state)
    _print_kv(f"Top miners by queries_used (top {args.limit}):", summary.assignments_top_miners)
    print()

    print("Quick invariants to check:")
    print("  - Expect 5 devices for Antminer S19")
    print("  - Expect 25 tasks for Antminer S19 (5 devices x 5 ambient levels)")
    print("  - Expect ~5 tasks per device_id for that model")
    print("=" * 72)


if __name__ == "__main__":
    main()

