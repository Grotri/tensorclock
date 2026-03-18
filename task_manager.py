"""
Simple task generation utilities for TensorClock validators.

This module implements a minimal Task Manager aligned with the
`agent_workflows/architecture_design.md` document.

Features implemented here:
- Generate a full task bundle for a specific ASIC model (persisted in SQLite).
- Always uses 5 virtual devices (reuses existing when available).
- Always uses 5 ambient environments (all AmbientTemperatureLevel values).
- Creates one test task per (device, ambient_level) pair.
- Exposes tasks as JSON-serializable structures.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Literal, Optional, Tuple

from init_db import connect, default_db_path, init_db
from virtual_device_generator import VirtualDeviceGenerator, flatten_virtual_device_for_db
from asic_physics_simulator import AmbientTemperatureLevel
from version import DB_SCHEMA_VERSION, TASK_CREATOR_VERSION


OptimizationTarget = Literal["efficiency", "hashrate", "balanced"]


@dataclass
class MinerTask:
    """Single miner task bound to a specific device and ambient environment."""

    task_id: str
    device_id: str
    asic_model: str
    ambient_level: str
    query_budget: int
    target: OptimizationTarget
    created_at: str
    expires_at: str


@dataclass
class TaskBundle:
    """
    Collection of tasks for a single ASIC model.

    - devices: list of device_ids created for this bundle
    - ambient_levels: ambient temperature presets used in tasks
    - tasks: full list of MinerTask objects (JSON-serializable)
    """

    asic_model: str
    devices: List[str]
    ambient_levels: List[Dict[str, float]]
    tasks: List[MinerTask]

    def to_dict(self) -> Dict[str, object]:
        """Return the bundle as a JSON-serializable dict (DB-only)."""
        return {
            "asic_model": self.asic_model,
            "devices": self.devices,
            "ambient_levels": self.ambient_levels,
            "tasks": [asdict(t) for t in self.tasks],
        }


def _generate_task_id() -> str:
    return f"task_{uuid.uuid4().hex[:16]}"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _default_db_path() -> Path:
    return default_db_path()


def _fetch_device_ids(conn: sqlite3.Connection, asic_model: str, limit: int) -> List[str]:
    rows = conn.execute(
        "SELECT device_id FROM devices WHERE asic_model = ? ORDER BY created_at DESC LIMIT ?",
        (asic_model, limit),
    ).fetchall()
    return [r["device_id"] for r in rows]


def _upsert_device_row(conn: sqlite3.Connection, row: Dict[str, object]) -> None:
    conn.execute(
        """
        INSERT INTO devices (
            device_id, asic_model, electricity_price, created_at,
            silicon_quality, degradation, thermal_resistance,
            spec_name, spec_manufacturer, nominal_hashrate, nominal_power, hashrate_per_mhz,
            optimal_voltage, base_thermal_resistance, manufacturer_frequency, efficiency, C,
            min_frequency, max_frequency, min_voltage, max_voltage, max_safe_temperature,
            min_fan_speed, max_fan_speed, min_power, max_power,
            device_json
        ) VALUES (
            :device_id, :asic_model, :electricity_price, :created_at,
            :silicon_quality, :degradation, :thermal_resistance,
            :spec_name, :spec_manufacturer, :nominal_hashrate, :nominal_power, :hashrate_per_mhz,
            :optimal_voltage, :base_thermal_resistance, :manufacturer_frequency, :efficiency, :C,
            :min_frequency, :max_frequency, :min_voltage, :max_voltage, :max_safe_temperature,
            :min_fan_speed, :max_fan_speed, :min_power, :max_power,
            :device_json
        )
        ON CONFLICT(device_id) DO UPDATE SET
            asic_model=excluded.asic_model,
            electricity_price=excluded.electricity_price,
            created_at=excluded.created_at,
            silicon_quality=excluded.silicon_quality,
            degradation=excluded.degradation,
            thermal_resistance=excluded.thermal_resistance,
            spec_name=excluded.spec_name,
            spec_manufacturer=excluded.spec_manufacturer,
            nominal_hashrate=excluded.nominal_hashrate,
            nominal_power=excluded.nominal_power,
            hashrate_per_mhz=excluded.hashrate_per_mhz,
            optimal_voltage=excluded.optimal_voltage,
            base_thermal_resistance=excluded.base_thermal_resistance,
            manufacturer_frequency=excluded.manufacturer_frequency,
            efficiency=excluded.efficiency,
            C=excluded.C,
            min_frequency=excluded.min_frequency,
            max_frequency=excluded.max_frequency,
            min_voltage=excluded.min_voltage,
            max_voltage=excluded.max_voltage,
            max_safe_temperature=excluded.max_safe_temperature,
            min_fan_speed=excluded.min_fan_speed,
            max_fan_speed=excluded.max_fan_speed,
            min_power=excluded.min_power,
            max_power=excluded.max_power,
            device_json=excluded.device_json
        """,
        row,
    )


def _ensure_devices(
    conn: sqlite3.Connection,
    generator: VirtualDeviceGenerator,
    asic_model: str,
    devices_count: int,
) -> List[str]:
    """
    Ensure at least `devices_count` devices exist in DB for `asic_model`.
    Reuses existing, imports from disk if needed, otherwise generates missing.
    Returns exactly `devices_count` device_ids (newest-first selection).
    """
    device_ids = _fetch_device_ids(conn, asic_model, devices_count)
    if len(device_ids) >= devices_count:
        return device_ids[:devices_count]

    # Generate missing devices and persist to DB
    missing = devices_count - len(device_ids)
    for _ in range(missing):
        device = generator.generate_device(
            model_name=asic_model,
            hidden_params={
                "silicon_quality": 1.0,
                "degradation": 0.0,
                "thermal_resistance": generator._asic_models[asic_model].base_thermal_resistance,
            },
            electricity_price=0.05,
            apply_thermal_resistance_spread=True,
        )
        row = flatten_virtual_device_for_db(device)
        _upsert_device_row(conn, row)

    conn.commit()
    return _fetch_device_ids(conn, asic_model, devices_count)[:devices_count]


def _fetch_open_tasks(
    conn: sqlite3.Connection,
    asic_model: str,
    target: OptimizationTarget,
    now_iso: str,
) -> List[sqlite3.Row]:
    return conn.execute(
        """
        SELECT task_id, device_id, ambient_level, query_budget, target, created_at, expires_at
        FROM tasks
        WHERE asic_model = ?
          AND target = ?
          AND status = 'open'
          AND creator_version = ?
          AND expires_at > ?
        """,
        (asic_model, target, TASK_CREATOR_VERSION, now_iso),
    ).fetchall()


def _insert_task(
    conn: sqlite3.Connection,
    task_id: str,
    device_id: str,
    asic_model: str,
    ambient_level: str,
    query_budget: int,
    target: OptimizationTarget,
    created_at: str,
    expires_at: str,
) -> None:
    conn.execute(
        """
        INSERT INTO tasks (
            task_id, device_id, asic_model, ambient_level, query_budget, target,
            created_at, expires_at, status, creator_version, schema_version
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'open', ?, ?)
        """,
        (
            task_id,
            device_id,
            asic_model,
            ambient_level,
            query_budget,
            target,
            created_at,
            expires_at,
            TASK_CREATOR_VERSION,
            DB_SCHEMA_VERSION,
        ),
    )


def _ensure_tasks(
    conn: sqlite3.Connection,
    asic_model: str,
    device_ids: List[str],
    query_budget: int,
    target: OptimizationTarget,
    expires_in: timedelta,
) -> List[MinerTask]:
    """
    Ensure there are open (unexpired) tasks for every (device_id, ambient_level).
    Reuses existing open tasks when present; otherwise creates missing.
    Returns the full set for the given device_ids and target.
    """
    now = _utcnow()
    now_iso = now.isoformat()

    # Supersede tasks generated by older versions
    conn.execute(
        "UPDATE tasks SET status='superseded' WHERE asic_model = ? AND creator_version <> ?",
        (asic_model, TASK_CREATOR_VERSION),
    )
    conn.commit()

    open_rows = _fetch_open_tasks(conn, asic_model, target, now_iso)
    existing: Dict[Tuple[str, str], sqlite3.Row] = {
        (r["device_id"], r["ambient_level"]): r for r in open_rows
    }

    expires_at = (now + expires_in).isoformat()
    for device_id in device_ids:
        for level in AmbientTemperatureLevel:
            key = (device_id, level.name)
            if key in existing:
                continue
            _insert_task(
                conn,
                task_id=_generate_task_id(),
                device_id=device_id,
                asic_model=asic_model,
                ambient_level=level.name,
                query_budget=query_budget,
                target=target,
                created_at=now_iso,
                expires_at=expires_at,
            )

    conn.commit()

    # Fetch again and build MinerTask list only for requested device_ids
    rows = _fetch_open_tasks(conn, asic_model, target, now_iso)
    device_set = set(device_ids)
    tasks: List[MinerTask] = []
    for r in rows:
        if r["device_id"] not in device_set:
            continue
        tasks.append(
            MinerTask(
                task_id=r["task_id"],
                device_id=r["device_id"],
                asic_model=asic_model,
                ambient_level=r["ambient_level"],
                query_budget=int(r["query_budget"]),
                target=target,
                created_at=r["created_at"],
                expires_at=r["expires_at"],
            )
        )
    return tasks


def generate_miner_task_bundle(
    asic_model: str = "Antminer S19",
    devices_count: int = 5,
    query_budget: int = 100,
    target: OptimizationTarget = "efficiency",
    db_path: Path | str = _default_db_path(),
    expires_in: timedelta = timedelta(hours=1),
) -> TaskBundle:
    """
    Generate a full miner task bundle for a specific ASIC model.
    
    Behaviour:
    - Uses SQLite as the source of truth for devices/tasks.
    - Reuses up to `devices_count` existing devices from DB.
    - If there are fewer than `devices_count`, imports from ./virtual_devices/*.json,
      and if still missing, generates the remaining and persists them.
    - Uses all 5 ambient environments (all AmbientTemperatureLevel values).
    - Each device has a task in each ambient environment (devices_count * 5 tasks).
    """
    init_db(db_path)
    generator = VirtualDeviceGenerator()
    generator.load_builtin_specifications()

    if asic_model not in generator.get_available_models():
        available = ", ".join(generator.get_available_models())
        raise ValueError(f"ASIC model '{asic_model}' not found. Available: {available}")

    db_path = Path(db_path)
    with connect(db_path) as conn:
        devices = _ensure_devices(conn, generator, asic_model, devices_count)
        tasks = _ensure_tasks(conn, asic_model, devices, query_budget, target, expires_in)

    # Ambient environments: use all defined levels (5 values)
    ambient_levels: List[Dict[str, float]] = [
        {"name": level.name, "temperature_c": float(level.value)} for level in AmbientTemperatureLevel
    ]

    return TaskBundle(
        asic_model=asic_model,
        devices=devices,
        ambient_levels=ambient_levels,
        tasks=tasks,
    )


def main() -> None:
    """
    CLI entry point for generating a default task bundle (DB-only).

    - Writes/updates devices and tasks in SQLite (data/validator.db).
    - Does not write any JSON artifacts to disk.
    """
    bundle = generate_miner_task_bundle()
    print(
        f"[OK] Tasks ensured in DB for {bundle.asic_model}: "
        f"{len(bundle.devices)} devices, {len(bundle.tasks)} tasks"
    )


if __name__ == "__main__":
    main()

