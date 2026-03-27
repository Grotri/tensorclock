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

from simulation.asic_physics_simulator import (
    ASICPhysicsSimulator,
    AmbientTemperatureLevel,
    OptimizationParameters,
)
from utils.init_db import connect
from utils.publication_expiry import (
    deadline_iso_from_now,
    effective_publication_deadline,
    expire_publication_if_overdue,
)
from utils.scoring_hashprice import (
    apply_scores_after_assignment_update,
    apply_scores_after_publication_completed,
    get_cached_usd_per_th_day,
    schedule_hashprice_refresh_if_stale,
)
from validator.task_manager import (
    ELECTRICITY_PRICE_MAX,
    ELECTRICITY_PRICE_MIN,
    EXPECTED_TASKS_PER_PUBLICATION,
)
from utils.version import DB_SCHEMA_VERSION, TASK_CREATOR_VERSION
from simulation.virtual_device_generator import VirtualDeviceGenerator

logger = logging.getLogger(__name__)

app = FastAPI(title="TensorClock Validator API")


def _epistula_required() -> bool:
    raw = os.getenv("EPISTULA_REQUIRED", "true").strip().lower()
    if raw in ("0", "false", "no", "off", ""):
        return False
    return True


async def _read_body_with_optional_epistula(request: Request) -> tuple[bytes, Optional[str]]:
    body = await request.body()
    if not _epistula_required():
        return body, None
    from utils.epistula import verify_epistula_request

    try:
        hk = verify_epistula_request(headers=request.headers, body=body)
    except ValueError as e:
        raise HTTPException(status_code=401, detail=str(e)) from e
    logger.debug("Epistula OK hotkey=%s", hk)
    return body, str(hk)


class ClaimRequest(BaseModel):
    miner_uid: int = Field(..., ge=0)
    miner_hotkey: str = Field(..., min_length=1)
    asic_model: str
    target: str
    publication_id: Optional[str] = None
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
    can_continue: bool
    remaining_queries: int
    gross_revenue_usd_day: Optional[float] = None
    electricity_cost_usd_day: Optional[float] = None
    net_profit_usd_day: Optional[float] = None
    next_step_message: Optional[str] = None
    publication_completed: bool


class DecisionRequest(BaseModel):
    publication_id: str
    task_id: str
    action: str = Field(..., pattern="^(continue|finalize)$")


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
    return app.state.validator_state


def init_validator_api(*, db_url: str, generator: VirtualDeviceGenerator, executor: Any) -> None:
    app.state.validator_state = ValidatorState(
        db_url=db_url,
        generator=generator,
        generator_lock=threading.Lock(),
        executor=executor,
    )


def _is_overheated(*, device: Any, temperature: float) -> bool:
    limits = device.base_specification.hardware_limits
    return float(temperature) > float(limits.max_safe_temperature)


def _try_finalize_publication_when_pool_exhausted(conn: Any, publication_id: str, now_iso: str) -> None:
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
    apply_scores_after_publication_completed(conn, publication_id=publication_id)


def _run_simulation_sync(*, device: Any, ambient_level: AmbientTemperatureLevel, req: SubmitRequest) -> Any:
    sim = ASICPhysicsSimulator()
    sim.load_device_from_object(device)
    outcome = sim.simulate(
        ambient_level=ambient_level,
        params=OptimizationParameters(frequency=float(req.frequency), voltage=float(req.voltage), fan_speed=float(req.fan_speed)),
    )
    return outcome


def _calculate_task_net_profit_usd_per_day(*, outcome: Any, electricity_price_usd_per_kwh: float, usd_per_th_day: float) -> float:
    hashrate_th = float(outcome.hashrate)
    power_watts = float(outcome.power)
    gross_revenue_usd_day = hashrate_th * float(usd_per_th_day)
    electricity_cost_usd_day = (power_watts * 24.0 / 1000.0) * float(electricity_price_usd_per_kwh)
    return gross_revenue_usd_day - electricity_cost_usd_day


def _calculate_revenue_components(*, outcome: Any, electricity_price_usd_per_kwh: float, usd_per_th_day: float) -> tuple[float, float, float]:
    hashrate_th = float(outcome.hashrate)
    power_watts = float(outcome.power)
    gross_revenue_usd_day = hashrate_th * float(usd_per_th_day)
    electricity_cost_usd_day = (power_watts * 24.0 / 1000.0) * float(electricity_price_usd_per_kwh)
    net_profit_usd_day = gross_revenue_usd_day - electricity_cost_usd_day
    return gross_revenue_usd_day, electricity_cost_usd_day, net_profit_usd_day


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/task", response_model=ClaimResponse)
async def claim_task(request: Request) -> ClaimResponse:
    body, verified_signer_hk = await _read_body_with_optional_epistula(request)
    req = ClaimRequest.model_validate_json(body)
    hk_claim = str(req.miner_hotkey).strip()
    if not hk_claim:
        raise HTTPException(status_code=422, detail="miner_hotkey must be non-empty")
    if verified_signer_hk is not None and verified_signer_hk.strip() != hk_claim:
        raise HTTPException(
            status_code=403,
            detail="miner_hotkey must match Epistula signing hotkey (X-Epistula-Hotkey)",
        )
    state = _get_state()
    now_iso = _now_iso()

    try:
        with connect(state.db_url) as conn:
            pub_id = req.publication_id
            if not pub_id:
                pub_id = f"pub_{uuid.uuid4().hex}"
                conn.execute(
                    "UPDATE publications SET state='cancelled', completed_at=? WHERE miner_uid=? AND publication_id <> ? AND state IN ('active','expired')",
                    (now_iso, req.miner_uid, pub_id),
                )
                model_description_json = json.dumps(req.model_description_json) if req.model_description_json is not None else None
                pub_deadline_iso = deadline_iso_from_now()
                total_tasks_expected = EXPECTED_TASKS_PER_PUBLICATION
                conn.execute(
                    """
                    INSERT INTO publications (
                        publication_id, miner_uid, miner_hotkey, asic_model, target, query_budget,
                        tasks_creator_version, tasks_schema_version,
                        model_description_json,
                        state, created_at, publication_deadline_at, total_tasks_expected
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', ?, ?, ?)
                    """,
                    (
                        pub_id,
                        req.miner_uid,
                        hk_claim,
                        req.asic_model,
                        req.target,
                        10,
                        TASK_CREATOR_VERSION,
                        DB_SCHEMA_VERSION,
                        model_description_json,
                        now_iso,
                        pub_deadline_iso,
                        total_tasks_expected,
                    ),
                )

            pub_row = conn.execute(
                """
                SELECT publication_id, miner_uid, miner_hotkey, asic_model, target, tasks_creator_version, tasks_schema_version,
                       state, publication_deadline_at, created_at
                FROM publications WHERE publication_id = ?
                """,
                (pub_id,),
            ).fetchone()
            if pub_row is None:
                raise HTTPException(status_code=404, detail="publication_id not found")
            if int(pub_row["miner_uid"]) != int(req.miner_uid):
                raise HTTPException(status_code=403, detail="publication_id does not belong to miner_uid")
            stored_hk = pub_row.get("miner_hotkey")
            if stored_hk is None or str(stored_hk).strip() == "":
                conn.execute(
                    "UPDATE publications SET miner_hotkey=? WHERE publication_id=?",
                    (hk_claim, pub_id),
                )
            elif str(stored_hk).strip() != hk_claim:
                raise HTTPException(status_code=403, detail="miner_hotkey does not match publication")
            if expire_publication_if_overdue(conn, pub_id, now_iso):
                raise HTTPException(status_code=410, detail="publication deadline expired")
            pub_row = conn.execute(
                """
                SELECT publication_id, miner_uid, miner_hotkey, asic_model, target, tasks_creator_version, tasks_schema_version,
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
    finally:
        try:
            schedule_hashprice_refresh_if_stale(state.db_url)
        except Exception:
            logger.exception("hashprice schedule after claim_task")


@app.post("/task/submit", response_model=SubmitResponse)
async def submit_task(request: Request) -> SubmitResponse:
    body, _verified_hk = await _read_body_with_optional_epistula(request)
    req = SubmitRequest.model_validate_json(body)
    state = _get_state()
    now_iso = _now_iso()

    try:
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
        
            with state.generator_lock:
                device = state.generator.load_device_from_db(t["device_id"], conn)
        
        ambient = AmbientTemperatureLevel[str(t["ambient_level"])]
        
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
            usd_per_th_day = get_cached_usd_per_th_day(conn)
            if usd_per_th_day is None:
                raise HTTPException(status_code=503, detail="hashprice unavailable")
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
            gross_revenue_usd_day: Optional[float] = None
            electricity_cost_usd_day: Optional[float] = None
            net_profit_usd_day: Optional[float] = None
            can_continue = False
            remaining_queries = max(0, int(a2["query_budget"]) - new_queries_used)
        
            if overheated:
                final_state = "failed"
                failure_reason = "overheated"
                net_profit = 0.0
                completed_at = now_iso
            elif bool(outcome.valid):
                gross_revenue_usd_day, electricity_cost_usd_day, net_profit_usd_day = _calculate_revenue_components(
                    outcome=outcome,
                    electricity_price_usd_per_kwh=float(device.electricity_price),
                    usd_per_th_day=float(usd_per_th_day),
                )
                net_profit = net_profit_usd_day
                best_eff = float(outcome.efficiency)
                if new_queries_used >= int(a2["query_budget"]):
                    final_state = "completed"
                    completed_at = now_iso
                else:
                    final_state = "active"
                    can_continue = True
            else:
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

            apply_scores_after_assignment_update(
                conn,
                publication_id=req.publication_id,
                task_id=req.task_id,
                net_profit=net_profit,
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
                    can_continue=can_continue,
                    remaining_queries=remaining_queries,
                    gross_revenue_usd_day=gross_revenue_usd_day,
                    electricity_cost_usd_day=electricity_cost_usd_day,
                    net_profit_usd_day=net_profit_usd_day,
                    next_step_message=(
                        "Continue optimization or finalize this task."
                        if can_continue
                        else "Task closed."
                    ),
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
                    can_continue=False,
                    remaining_queries=0,
                    gross_revenue_usd_day=gross_revenue_usd_day,
                    electricity_cost_usd_day=electricity_cost_usd_day,
                    net_profit_usd_day=net_profit_usd_day,
                    next_step_message="Publication already completed.",
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
                    can_continue=False,
                    remaining_queries=0,
                    gross_revenue_usd_day=gross_revenue_usd_day,
                    electricity_cost_usd_day=electricity_cost_usd_day,
                    net_profit_usd_day=net_profit_usd_day,
                    next_step_message=f"Publication state is {pstate}.",
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
                apply_scores_after_publication_completed(conn, publication_id=req.publication_id)

            return SubmitResponse(
                publication_id=req.publication_id,
                task_id=req.task_id,
                state=final_state,
                queries_used=new_queries_used,
                temperature=float(outcome.temperature),
                warning=outcome.warning,
                overheated=overheated,
                can_continue=can_continue,
                remaining_queries=remaining_queries,
                gross_revenue_usd_day=gross_revenue_usd_day,
                electricity_cost_usd_day=electricity_cost_usd_day,
                net_profit_usd_day=net_profit_usd_day,
                next_step_message=(
                    "Continue optimization or finalize this task."
                    if can_continue
                    else (
                        "Query budget exhausted, task closed."
                        if final_state == "completed" and remaining_queries == 0
                        else "Task closed."
                    )
                ),
                publication_completed=publication_completed,
            )
    finally:
        try:
            schedule_hashprice_refresh_if_stale(state.db_url)
        except Exception:
            logger.exception("hashprice schedule after submit_task")


@app.post("/task/decision", response_model=SubmitResponse)
async def decide_task(request: Request) -> SubmitResponse:
    body, _verified_hk = await _read_body_with_optional_epistula(request)
    req = DecisionRequest.model_validate_json(body)
    state = _get_state()
    now_iso = _now_iso()

    if req.action == "continue":
        raise HTTPException(status_code=400, detail="use POST /task/submit with next parameters to continue")

    with connect(state.db_url) as conn:
        if expire_publication_if_overdue(conn, req.publication_id, now_iso):
            raise HTTPException(status_code=410, detail="publication deadline expired")

        row = conn.execute(
            """
            SELECT query_budget, queries_used, state
            FROM assignments
            WHERE publication_id=? AND task_id=?
            """,
            (req.publication_id, req.task_id),
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="assignment not found")
        if row["state"] != "active":
            raise HTTPException(status_code=409, detail=f"assignment is {row['state']}")

        queries_used = int(row["queries_used"])
        query_budget = int(row["query_budget"])
        remaining_queries = max(0, query_budget - queries_used)
        if queries_used <= 0:
            raise HTTPException(status_code=400, detail="cannot finalize before first submit")

        conn.execute(
            """
            UPDATE assignments
            SET state='completed', completed_at=?
            WHERE publication_id=? AND task_id=? AND state='active'
            """,
            (now_iso, req.publication_id, req.task_id),
        )

        pub_row = conn.execute(
            "SELECT asic_model, target, total_tasks_expected, state FROM publications WHERE publication_id=?",
            (req.publication_id,),
        ).fetchone()
        publication_completed = False
        if pub_row is not None and str(pub_row.get("state") or "") == "active":
            assign_total = conn.execute(
                "SELECT COUNT(*) AS n FROM assignments WHERE publication_id=?",
                (req.publication_id,),
            ).fetchone()["n"]
            active_cnt = conn.execute(
                "SELECT COUNT(*) AS n FROM assignments WHERE publication_id=? AND state='active'",
                (req.publication_id,),
            ).fetchone()["n"]
            closed = conn.execute(
                "SELECT COUNT(*) AS n FROM assignments WHERE publication_id=? AND state IN ('completed','failed')",
                (req.publication_id,),
            ).fetchone()["n"]
            snap = pub_row.get("total_tasks_expected")
            expected = int(snap) if snap is not None and int(snap) > 0 else EXPECTED_TASKS_PER_PUBLICATION
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
                apply_scores_after_publication_completed(conn, publication_id=req.publication_id)

        cur = conn.execute(
            """
            SELECT queries_used, state, net_profit
            FROM assignments
            WHERE publication_id=? AND task_id=?
            """,
            (req.publication_id, req.task_id),
        ).fetchone()

        return SubmitResponse(
            publication_id=req.publication_id,
            task_id=req.task_id,
            state=str(cur["state"]),
            queries_used=int(cur["queries_used"]),
            temperature=0.0,
            warning=None,
            overheated=False,
            can_continue=False,
            remaining_queries=remaining_queries,
            gross_revenue_usd_day=None,
            electricity_cost_usd_day=None,
            net_profit_usd_day=float(cur["net_profit"]) if cur.get("net_profit") is not None else None,
            next_step_message="Task finalized by miner decision.",
            publication_completed=publication_completed,
        )

