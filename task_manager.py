from __future__ import annotations

import json
import random
import uuid
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional, Tuple

from init_db import connect, default_db_path, init_db
from virtual_device_generator import VirtualDeviceGenerator
from asic_physics_simulator import AmbientTemperatureLevel
from version import DB_SCHEMA_VERSION, TASK_CREATOR_VERSION

DEFAULT_BUNDLE_DEVICE_COUNT = 5
EXPECTED_TASKS_PER_PUBLICATION = DEFAULT_BUNDLE_DEVICE_COUNT * len(AmbientTemperatureLevel)


OptimizationTarget = Literal["efficiency", "hashrate", "balanced"]

ELECTRICITY_PRICE_MIN = 0.03
ELECTRICITY_PRICE_MAX = 0.10
ELECTRICITY_PRICE_STEP = 0.001


def _allowed_electricity_prices() -> List[float]:
    min_i = int(round(ELECTRICITY_PRICE_MIN / ELECTRICITY_PRICE_STEP))
    max_i = int(round(ELECTRICITY_PRICE_MAX / ELECTRICITY_PRICE_STEP))
    return [round(i * ELECTRICITY_PRICE_STEP, 10) for i in range(min_i, max_i + 1)]


def _is_allowed_electricity_price(price: float) -> bool:
    eps = 1e-12
    if price < ELECTRICITY_PRICE_MIN - eps or price > ELECTRICITY_PRICE_MAX + eps:
        return False
    i = int(round(price / ELECTRICITY_PRICE_STEP))
    return abs(price - (i * ELECTRICITY_PRICE_STEP)) < 1e-9


@dataclass
class MinerTask:
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


def _default_db_path() -> str:
    return default_db_path()


def _fetch_device_ids(conn: Any, asic_model: str, limit: int) -> List[str]:
    rows = conn.execute(
        "SELECT device_id FROM devices WHERE asic_model = ? AND is_active = 1 ORDER BY created_at DESC LIMIT ?",
        (asic_model, limit),
    ).fetchall()
    return [r["device_id"] for r in rows]


def _ensure_devices(
    conn: Any,
    generator: VirtualDeviceGenerator,
    asic_model: str,
    devices_count: int,
) -> List[str]:
    """
    Ensure at least `devices_count` devices exist in DB for `asic_model`.
    Returns exactly `devices_count` device_ids (newest-first selection).
    """
    allowed_prices = _allowed_electricity_prices()
    device_ids = _fetch_device_ids(conn, asic_model, devices_count)

    used_prices: set[float] = set()
    keep_ids: List[str] = []
    delete_ids: List[str] = []

    if len(device_ids) >= devices_count:
        candidate_ids = device_ids[:devices_count]
        for did in candidate_ids:
            dev = generator.load_device_from_db(did, conn)
            price = float(dev.electricity_price)
            if (not _is_allowed_electricity_price(price)) or (price in used_prices):
                delete_ids.append(did)
            else:
                used_prices.add(price)
                keep_ids.append(did)

        if not delete_ids and len(keep_ids) == devices_count:
            return keep_ids

        for did in delete_ids:
            conn.execute("DELETE FROM devices WHERE device_id = ?", (did,))

        missing = devices_count - len(keep_ids)
    else:
        keep_ids = device_ids
        for did in keep_ids:
            dev = generator.load_device_from_db(did, conn)
            used_prices.add(float(dev.electricity_price))
        missing = devices_count - len(keep_ids)

    remaining_prices = [p for p in allowed_prices if p not in used_prices]
    for _ in range(missing):
        if not remaining_prices:
            raise RuntimeError("Not enough unique electricity_price values to generate devices.")
        electricity_price = random.choice(remaining_prices)
        remaining_prices.remove(electricity_price)
        device = generator.generate_device(
            model_name=asic_model,
            hidden_params=generator.sample_random_hidden_parameters(asic_model),
            electricity_price=electricity_price,
            apply_thermal_resistance_spread=True,
        )
        generator.save_device_to_db(device, conn)

    conn.commit()
    return _fetch_device_ids(conn, asic_model, devices_count)[:devices_count]


def _fetch_open_tasks(
    conn: Any,
    asic_model: str,
    target: OptimizationTarget,
    now_iso: str,
) -> List[Any]:
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
    conn: Any,
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
    conn: Any,
    asic_model: str,
    device_ids: List[str],
    query_budget: int,
    target: OptimizationTarget,
    expires_in: timedelta,
) -> List[MinerTask]:
    """
    Ensure there are open (unexpired) tasks for every (device_id, ambient_level).
    Returns the full set for the given device_ids and target.
    """
    now = _utcnow()
    now_iso = now.isoformat()

    conn.execute(
        "UPDATE tasks SET status='superseded' WHERE asic_model = ? AND creator_version <> ?",
        (asic_model, TASK_CREATOR_VERSION),
    )
    conn.commit()

    open_rows = _fetch_open_tasks(conn, asic_model, target, now_iso)
    existing: Dict[Tuple[str, str], Any] = {
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
    devices_count: int = DEFAULT_BUNDLE_DEVICE_COUNT,
    query_budget: int = 10,
    target: OptimizationTarget = "efficiency",
    db_path: Path | str = _default_db_path(),
    expires_in: timedelta = timedelta(hours=1),
) -> TaskBundle:
    init_db(str(db_path))
    generator = VirtualDeviceGenerator()
    generator.load_builtin_specifications()

    if asic_model not in generator.get_available_models():
        available = ", ".join(generator.get_available_models())
        raise ValueError(f"ASIC model '{asic_model}' not found. Available: {available}")

    with connect(str(db_path)) as conn:
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
    bundle = generate_miner_task_bundle()
    print(
        f"[OK] Tasks ensured in DB for {bundle.asic_model}: "
        f"{len(bundle.devices)} devices, {len(bundle.tasks)} tasks"
    )


if __name__ == "__main__":
    main()

