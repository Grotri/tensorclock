from __future__ import annotations

import json
import logging
import os
import threading
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from starlette.requests import Request

from asic_physics_simulator import (
    ASICPhysicsSimulator,
    AmbientTemperatureLevel,
    OptimizationParameters,
)
from init_db import connect
from publication_expiry import (
    deadline_iso_from_now,
    effective_publication_deadline,
    expire_publication_if_overdue,
)
from task_manager import (
    ELECTRICITY_PRICE_MAX,
    ELECTRICITY_PRICE_MIN,  # noqa: F401
    EXPECTED_TASKS_PER_PUBLICATION,
)
from version import DB_SCHEMA_VERSION, TASK_CREATOR_VERSION
from virtual_device_generator import VirtualDeviceGenerator

logger = logging.getLogger(__name__)

app = FastAPI(title="TensorClock Validator API")


def _epistula_required() -> bool:
    """When true, POST bodies must include valid Epistula headers (miner hotkey signature)."""
    return os.getenv("EPISTULA_REQUIRED", "false").strip().lower() in ("1", "true", "yes")


async def _read_body_with_optional_epistula(request: Request) -> bytes:
    body = await request.body()
    if not _epistula_required():
        return body
    from epistula import verify_epistula_request

    try:
        hk = verify_epistula_request(headers=request.headers, body=body)
    except ValueError as e:
        raise HTTPException(status_code=401, detail=str(e)) from e
    logger.debug("Epistula OK hotkey=%s", hk)
    return body


class ClaimRequest(BaseModel):
    miner_uid: int = Field(..., ge=0)
    asic_model: str
    target: str
    publication_id: Optional[str] = None
    # Optional metadata; validator does not depend on it for scoring.
    model_description_json: Optional[dict[str, Any]] = None


class TaskPayload(BaseModel):
    task_id: str
    device_id: str
    asic_model: str
    ambient_level: str
    target: str
    query_budget: int
    expires_at: str


class ClaimResponse(BaseModel):
    publication_id: str
    publication_deadline_at: str
    task: TaskPayload
    assignment_state: str
    queries_used: int


class SubmitRequest(BaseModel):
    publication_id: str
    task_id: str
    frequency: float
    voltage: float
    fan_speed: float


class SubmitResponse(BaseModel):
    publication_id: str
    task_id: str
    state: str
    queries_used: int
    temperature: float
    warning: Optional[str] = None
    overheated: bool
    publication_completed: bool


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class ValidatorState:
    db_url: str
    generator: VirtualDeviceGenerator
    generator_lock: threading.Lock
    executor: Any  # ThreadPoolExecutor


def _get_state() -> ValidatorState:
    if not hasattr(app.state, "validator_state"):
        raise RuntimeError("Validator API not initialized (missing app.state.validator_state).")
    return app.state.validator_state  # type: ignore[attr-defined]


def init_validator_api(*, db_url: str, generator: VirtualDeviceGenerator, executor: Any) -> None:
    """
    Must be called by validator.py once at startup.
    """
    app.state.validator_state = ValidatorState(
        db_url=db_url,
        generator=generator,
        generator_lock=threading.Lock(),
        executor=executor,
    )


def _is_overheated(*, device: Any, temperature: float) -> bool:
    # Hard rule: if temperature exceeds max safe temperature, task is immediately failed.
    limits = device.base_specification.hardware_limits
    return float(temperature) > float(limits.max_safe_temperature)


def _try_finalize_publication_when_pool_exhausted(conn: Any, publication_id: str, now_iso: str) -> None:
    """
    When claim finds no next task (404), all work for this publication may still be done:
    every pool task has a terminal assignment. In that case mark publication completed.

    This closes the gap when submit-side completion did not fire (e.g. total_tasks_expected
    snapshot != number of assignments actually claimed, or legacy expected pool COUNT drift).
    """
    pub = conn.execute(
        "SELECT state FROM publications WHERE publication_id=? AND state='active'",
        (publication_id,),
    ).fetchone()
    if pub is None:
        return
    row = conn.execute(
        """
        SELECT
          (SELECT COUNT(*) FROM assignments WHERE publication_id=?) AS assign_total,
          (SELECT COUNT(*) FROM assignments WHERE publication_id=? AND state='active') AS active_cnt,
          (SELECT COUNT(*) FROM assignments WHERE publication_id=? AND state IN ('completed','failed')) AS closed_cnt
        """,
        (publication_id, publication_id, publication_id),
    ).fetchone()
    if row is None:
        return
    assign_total = int(row["assign_total"])
    active_cnt = int(row["active_cnt"])
    closed_cnt = int(row["closed_cnt"])
    if assign_total <= 0 or active_cnt != 0 or closed_cnt != assign_total:
        return
    avg = conn.execute(
        "SELECT AVG(net_profit) AS avg FROM assignments WHERE publication_id=?",
        (publication_id,),
    ).fetchone()["avg"]
    conn.execute(
        """
        UPDATE publications
        SET state='completed', avg_net_profit=?, completed_at=?
        WHERE publication_id=? AND state='active'
        """,
        (avg, now_iso, publication_id),
    )
    logger.info(
        "publication %s completed (claim/pool-exhausted path) avg_net_profit=%s",
        publication_id,
        avg,
    )


def _run_simulation_sync(*, device: Any, ambient_level: AmbientTemperatureLevel, req: SubmitRequest) -> Any:
    sim = ASICPhysicsSimulator()
    sim.load_device_from_object(device)
    outcome = sim.simulate(
        ambient_level=ambient_level,
        params=OptimizationParameters(frequency=float(req.frequency), voltage=float(req.voltage), fan_speed=float(req.fan_speed)),
    )
    return outcome


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/task", response_model=ClaimResponse)
async def claim_task(request: Request) -> ClaimResponse:
    body = await _read_body_with_optional_epistula(request)
    req = ClaimRequest.model_validate_json(body)
    state = _get_state()
    now_iso = _now_iso()

    with connect(state.db_url) as conn:
        # Create publication if needed.
        pub_id = req.publication_id
        if not pub_id:
            pub_id = f"pub_{uuid.uuid4().hex}"
            # Cancel in-flight / stale work only. Do NOT cancel `completed` rows — they are needed for
            # on-chain weights and audit; a new run supersedes scoring by creating a fresh publication.
            conn.execute(
                "UPDATE publications SET state='cancelled', completed_at=? WHERE miner_uid=? AND publication_id <> ? AND state IN ('active','expired')",
                (now_iso, req.miner_uid, pub_id),
            )
            model_description_json = json.dumps(req.model_description_json) if req.model_description_json is not None else None
            pub_deadline_iso = deadline_iso_from_now()
            # Fixed to the canonical bundle size (5 devices × 5 ambient levels), not COUNT(tasks):
            # the pool may contain extra open rows from older devices; publications must not include those.
            total_tasks_expected = EXPECTED_TASKS_PER_PUBLICATION
            conn.execute(
                """
                INSERT INTO publications (
                    publication_id, miner_uid, asic_model, target, query_budget,
                    tasks_creator_version, tasks_schema_version,
                    model_description_json,
                    state, created_at, publication_deadline_at, total_tasks_expected
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'active', ?, ?, ?)
                """,
                (
                    pub_id,
                    req.miner_uid,
                    req.asic_model,
                    req.target,
                    10,  # query_budget per task in MVP
                    TASK_CREATOR_VERSION,
                    DB_SCHEMA_VERSION,
                    model_description_json,
                    now_iso,
                    pub_deadline_iso,
                    total_tasks_expected,
                ),
            )

        # Validate publication ownership and status.
        pub_row = conn.execute(
            """
            SELECT publication_id, miner_uid, asic_model, target, tasks_creator_version, tasks_schema_version,
                   state, publication_deadline_at, created_at
            FROM publications WHERE publication_id = ?
            """,
            (pub_id,),
        ).fetchone()
        if pub_row is None:
            raise HTTPException(status_code=404, detail="publication_id not found")
        if int(pub_row["miner_uid"]) != int(req.miner_uid):
            raise HTTPException(status_code=403, detail="publication_id does not belong to miner_uid")
        if expire_publication_if_overdue(conn, pub_id, now_iso):
            raise HTTPException(status_code=410, detail="publication deadline expired")
        pub_row = conn.execute(
            """
            SELECT publication_id, miner_uid, asic_model, target, tasks_creator_version, tasks_schema_version,
                   state, publication_deadline_at, created_at
            FROM publications WHERE publication_id = ?
            """,
            (pub_id,),
        ).fetchone()
        if pub_row is None or pub_row["state"] != "active":
            raise HTTPException(
                status_code=409,
                detail=f"publication state is {pub_row['state'] if pub_row else 'missing'}",
            )
        pub_deadline_response = effective_publication_deadline(pub_row)

        # If miner already has an active assignment inside this publication, return the same task.
        active = conn.execute(
            """
            SELECT a.task_id, a.queries_used, a.query_budget, t.device_id, t.asic_model, t.ambient_level, t.target, t.expires_at
            FROM assignments a
            JOIN tasks t ON t.task_id = a.task_id
            WHERE a.publication_id = ?
              AND a.state = 'active'
            ORDER BY a.assigned_at ASC
            LIMIT 1
            """,
            (pub_id,),
        ).fetchone()
        if active is not None:
            return ClaimResponse(
                publication_id=pub_id,
                publication_deadline_at=pub_deadline_response,
                task=TaskPayload(
                    task_id=active["task_id"],
                    device_id=active["device_id"],
                    asic_model=active["asic_model"],
                    ambient_level=active["ambient_level"],
                    target=active["target"],
                    query_budget=int(active["query_budget"]),
                    expires_at=active["expires_at"],
                ),
                assignment_state="active",
                queries_used=int(active["queries_used"]),
            )

        # Select next unique task for this publication.
        task = conn.execute(
            """
            SELECT t.task_id, t.device_id, t.asic_model, t.ambient_level, t.target, t.query_budget, t.expires_at
            FROM tasks t
            WHERE t.asic_model = ?
              AND t.target = ?
              AND t.status = 'open'
              AND t.creator_version = ?
              AND t.schema_version = ?
              AND t.expires_at > ?
              AND NOT EXISTS (
                  SELECT 1
                  FROM assignments a
                  WHERE a.publication_id = ?
                    AND a.task_id = t.task_id
                    AND a.state IN ('completed','failed')
              )
            ORDER BY t.created_at ASC, t.task_id ASC
            LIMIT 1
            """,
            (req.asic_model, req.target, TASK_CREATOR_VERSION, DB_SCHEMA_VERSION, now_iso, pub_id),
        ).fetchone()

        if task is None:
            _try_finalize_publication_when_pool_exhausted(conn, pub_id, now_iso)
            # Must commit before raising: DBConnection.__exit__ rolls back on exception, which would undo
            # the UPDATE to state='completed' inside _try_finalize_publication_when_pool_exhausted.
            conn.commit()
            raise HTTPException(status_code=404, detail="No available tasks for this publication")

        conn.execute(
            """
            INSERT INTO assignments (
                publication_id, task_id, miner_uid,
                query_budget, queries_used, assigned_at, expires_at, state
            ) VALUES (?, ?, ?, ?, 0, ?, ?, 'active')
            """,
            (
                pub_id,
                task["task_id"],
                req.miner_uid,
                int(task["query_budget"]),
                now_iso,
                task["expires_at"],
            ),
        )

        return ClaimResponse(
            publication_id=pub_id,
            publication_deadline_at=pub_deadline_response,
            task=TaskPayload(
                task_id=task["task_id"],
                device_id=task["device_id"],
                asic_model=task["asic_model"],
                ambient_level=task["ambient_level"],
                target=task["target"],
                query_budget=int(task["query_budget"]),
                expires_at=task["expires_at"],
            ),
            assignment_state="active",
            queries_used=0,
        )


@app.post("/task/submit", response_model=SubmitResponse)
async def submit_task(request: Request) -> SubmitResponse:
    body = await _read_body_with_optional_epistula(request)
    req = SubmitRequest.model_validate_json(body)
    state = _get_state()
    now_iso = _now_iso()

    # Load assignment and task rows on the main thread (cheap DB reads).
    with connect(state.db_url) as conn:
        a = conn.execute(
            """
            SELECT
                a.publication_id,
                a.task_id,
                a.miner_uid,
                a.query_budget,
                a.queries_used,
                a.state,
                p.state AS pub_state
            FROM assignments a
            JOIN publications p ON p.publication_id = a.publication_id
            WHERE a.publication_id = ? AND a.task_id = ?
            """,
            (req.publication_id, req.task_id),
        ).fetchone()
        if a is None:
            raise HTTPException(status_code=404, detail="assignment not found")
        if a["pub_state"] != "active":
            raise HTTPException(status_code=409, detail=f"publication state is {a['pub_state']}")
        if expire_publication_if_overdue(conn, req.publication_id, now_iso):
            raise HTTPException(status_code=410, detail="publication deadline expired")
        a = conn.execute(
            """
            SELECT publication_id, task_id, miner_uid, query_budget, queries_used, state
            FROM assignments
            WHERE publication_id = ? AND task_id = ?
            """,
            (req.publication_id, req.task_id),
        ).fetchone()
        if a is None or a["state"] != "active":
            raise HTTPException(status_code=409, detail=f"assignment is {a['state'] if a else 'missing'}")

        if int(a["queries_used"]) >= int(a["query_budget"]):
            conn.execute(
                """
                UPDATE assignments
                SET state='failed', failure_reason='over_query_budget', completed_at=?
                WHERE publication_id=? AND task_id=?
                """,
                (now_iso, req.publication_id, req.task_id),
            )
            raise HTTPException(status_code=400, detail="over query_budget")

        t = conn.execute(
            """
            SELECT task_id, device_id, asic_model, ambient_level, target, query_budget, expires_at
            FROM tasks WHERE task_id = ?
            """,
            (req.task_id,),
        ).fetchone()
        if t is None:
            raise HTTPException(status_code=404, detail="task not found")

        # Load device for simulation (may be cached inside generator).
        with state.generator_lock:
            device = state.generator.load_device_from_db(t["device_id"], conn)

    ambient = AmbientTemperatureLevel[str(t["ambient_level"])]  # type: ignore[index]

    # Run simulation in executor to keep API responsive.
    import asyncio

    future = state.executor.submit(
        _run_simulation_sync,
        device=device,
        ambient_level=ambient,
        req=req,
    )
    outcome = await asyncio.wrap_future(future)

    overheated = _is_overheated(device=device, temperature=float(outcome.temperature))

    with connect(state.db_url) as conn:
        if expire_publication_if_overdue(conn, req.publication_id, _now_iso()):
            raise HTTPException(
                status_code=410,
                detail="publication deadline expired before result could be committed",
            )
        # Refresh assignment (queries_used) because we may have released the first connection.
        a2 = conn.execute(
            """
            SELECT query_budget, queries_used, state
            FROM assignments
            WHERE publication_id=? AND task_id=?
            """,
            (req.publication_id, req.task_id),
        ).fetchone()
        if a2 is None or a2["state"] != "active":
            raise HTTPException(status_code=409, detail="assignment closed during simulation")

        new_queries_used = int(a2["queries_used"]) + 1
        final_state = "active"
        failure_reason: Optional[str] = None
        net_profit: Optional[float] = None
        best_eff = None
        completed_at: Optional[str] = None

        if overheated:
            final_state = "failed"
            failure_reason = "overheated"
            net_profit = 0.0
            completed_at = now_iso
        elif bool(outcome.valid):
            final_state = "completed"
            net_profit = 1.0
            best_eff = float(outcome.efficiency)
            completed_at = now_iso
        else:
            # Any non-valid outcome is treated as an immediate task annulment,
            # because simulator returned that at least one constraint was violated.
            final_state = "failed"
            failure_reason = outcome.warning or "invalid_limits"
            net_profit = 0.0
            completed_at = now_iso

        conn.execute(
            """
            UPDATE assignments
            SET
                queries_used=?,
                state=?,
                failure_reason=?,
                net_profit=?,
                best_efficiency=?,
                completed_at=?
            WHERE publication_id=? AND task_id=?
            """,
            (
                new_queries_used,
                final_state,
                failure_reason,
                net_profit,
                best_eff,
                completed_at,
                req.publication_id,
                req.task_id,
            ),
        )

        # Check if publication is done: all assignments for this publication must be terminal,
        # and their count must match total_tasks_expected (snapshot at publication creation).
        #
        # IMPORTANT: do not use `WHERE state='active'` alone — if another worker expires the publication
        # between the pre-sim expiry check and here, or the row is already `completed`, we must not
        # return early with publication_completed=False (that leaves DB stuck `active` forever).
        pub_row = conn.execute(
            """
            SELECT asic_model, target, total_tasks_expected, state
            FROM publications WHERE publication_id=?
            """,
            (req.publication_id,),
        ).fetchone()
        if pub_row is None:
            logger.error("publication %s missing after assignment update; cannot finalize", req.publication_id)
            return SubmitResponse(
                publication_id=req.publication_id,
                task_id=req.task_id,
                state=final_state,
                queries_used=new_queries_used,
                temperature=float(outcome.temperature),
                warning=outcome.warning,
                overheated=overheated,
                publication_completed=False,
            )
        pstate = str(pub_row.get("state") or "")
        if pstate == "completed":
            return SubmitResponse(
                publication_id=req.publication_id,
                task_id=req.task_id,
                state=final_state,
                queries_used=new_queries_used,
                temperature=float(outcome.temperature),
                warning=outcome.warning,
                overheated=overheated,
                publication_completed=True,
            )
        if pstate != "active":
            return SubmitResponse(
                publication_id=req.publication_id,
                task_id=req.task_id,
                state=final_state,
                queries_used=new_queries_used,
                temperature=float(outcome.temperature),
                warning=outcome.warning,
                overheated=overheated,
                publication_completed=False,
            )
        pub = {
            "asic_model": pub_row["asic_model"],
            "target": pub_row["target"],
            "total_tasks_expected": pub_row.get("total_tasks_expected"),
        }

        assign_total = conn.execute(
            "SELECT COUNT(*) AS n FROM assignments WHERE publication_id=?",
            (req.publication_id,),
        ).fetchone()["n"]
        active_cnt = conn.execute(
            "SELECT COUNT(*) AS n FROM assignments WHERE publication_id=? AND state='active'",
            (req.publication_id,),
        ).fetchone()["n"]
        closed = conn.execute(
            """
            SELECT COUNT(*) AS n
            FROM assignments
            WHERE publication_id=? AND state IN ('completed','failed')
            """,
            (req.publication_id,),
        ).fetchone()["n"]

        snap = pub.get("total_tasks_expected")
        expected = (
            int(snap)
            if snap is not None and int(snap) > 0
            else EXPECTED_TASKS_PER_PUBLICATION
        )
        publication_completed = (
            int(expected) > 0
            and int(assign_total) == int(expected)
            and int(active_cnt) == 0
            and int(closed) == int(assign_total)
        )

        if publication_completed:
            avg = conn.execute(
                "SELECT AVG(net_profit) AS avg FROM assignments WHERE publication_id=?",
                (req.publication_id,),
            ).fetchone()["avg"]
            conn.execute(
                """
                UPDATE publications
                SET state='completed', avg_net_profit=?, completed_at=?
                WHERE publication_id=? AND state='active'
                """,
                (avg, now_iso, req.publication_id),
            )
            logger.info(
                "publication %s completed (submit path) avg_net_profit=%s",
                req.publication_id,
                avg,
            )

        return SubmitResponse(
            publication_id=req.publication_id,
            task_id=req.task_id,
            state=final_state,
            queries_used=new_queries_used,
            temperature=float(outcome.temperature),
            warning=outcome.warning,
            overheated=overheated,
            publication_completed=publication_completed,
        )

