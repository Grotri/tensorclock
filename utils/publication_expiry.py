from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping

from utils.db_row import row_to_plain_dict
from utils.init_db import connect
from utils.scoring_hashprice import recompute_leader_weights


def publication_deadline_seconds() -> int:
    return max(1, int(os.getenv("PUBLICATION_DEADLINE_SECONDS", "600")))


def sweep_interval_seconds() -> int:
    raw = os.getenv("PUBLICATION_EXPIRE_SWEEP_INTERVAL_SEC", "30")
    if raw is None or str(raw).strip() == "":
        raw = "30"
    return max(15, int(str(raw).strip()))


def sweep_batch_limit() -> int:
    return min(500, max(1, int(os.getenv("PUBLICATION_EXPIRE_SWEEP_BATCH_LIMIT", "100"))))


def sweep_scan_cap() -> int:
    return min(5000, max(sweep_batch_limit() * 20, 50))


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def deadline_iso_from_now() -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=publication_deadline_seconds())).isoformat()


def _parse_created_fallback(created_at: str) -> str:
    try:
        s = created_at.replace("Z", "+00:00")
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return (dt + timedelta(seconds=publication_deadline_seconds())).isoformat()
    except Exception:
        return _now_iso()


def effective_publication_deadline(pub_row: Mapping[str, Any]) -> str:
    d = pub_row.get("publication_deadline_at")
    if d:
        return str(d)
    return _parse_created_fallback(str(pub_row["created_at"]))


def is_deadline_passed(deadline_iso: str, now_iso: str) -> bool:
    return deadline_iso < now_iso


def expire_publication(conn: Any, publication_id: str, now_iso: str) -> bool:
    cur = conn.execute(
        """
        UPDATE publications
        SET state='expired', completed_at=?, avg_net_profit=0.0, dollar_value=0.0, weight=0
        WHERE publication_id=? AND state='active'
        """,
        (now_iso, publication_id),
    )
    rc = getattr(cur, "rowcount", -1)
    if rc == 0:
        return False
    conn.execute(
        """
        UPDATE assignments
        SET
            state='failed',
            failure_reason='publication_deadline',
            net_profit=0.0,
            best_efficiency=NULL,
            completed_at=?,
            dollar_value=0.0
        WHERE publication_id=?
        """,
        (now_iso, publication_id),
    )
    recompute_leader_weights(conn)
    return True


def expire_publication_if_overdue(conn: Any, publication_id: str, now_iso: str) -> bool:
    row = conn.execute(
        """
        SELECT state, publication_deadline_at, created_at
        FROM publications
        WHERE publication_id=?
        """,
        (publication_id,),
    ).fetchone()
    if row is None or row["state"] != "active":
        return False
    deadline = effective_publication_deadline(row_to_plain_dict(row))
    if not is_deadline_passed(deadline, now_iso):
        return False
    return expire_publication(conn, publication_id, now_iso)


def expire_stale_publications(db_url: str, *, now_iso: str | None = None, limit: int | None = None) -> int:
    now_iso = now_iso or _now_iso()
    max_batch = limit if limit is not None else sweep_batch_limit()
    cap = sweep_scan_cap()

    with connect(db_url) as conn:
        raw_rows = conn.execute(
            """
            SELECT publication_id, publication_deadline_at, created_at
            FROM publications
            WHERE state='active'
            LIMIT ?
            """,
            (cap,),
        ).fetchall()
        # Copy while connection is open; never use Row objects after the context exits.
        rows = [row_to_plain_dict(r) for r in raw_rows]

    def _eff_deadline(row: Mapping[str, Any]) -> str:
        d = row.get("publication_deadline_at")
        if d:
            return str(d)
        return _parse_created_fallback(str(row["created_at"]))

    sorted_rows = sorted(rows, key=_eff_deadline)
    to_expire: list[str] = []
    for row in sorted_rows:
        if len(to_expire) >= max_batch:
            break
        pid = str(row["publication_id"])
        if is_deadline_passed(_eff_deadline(row), now_iso):
            to_expire.append(pid)

    if not to_expire:
        return 0

    expired = 0
    with connect(db_url) as conn:
        for pid in to_expire:
            if expire_publication_if_overdue(conn, pid, now_iso):
                expired += 1
    return expired


def publication_expiry_sweep_loop(db_url: str, stop_event: Any) -> None:
    import logging

    logger = logging.getLogger(__name__)
    while not stop_event.is_set():
        try:
            n = expire_stale_publications(db_url)
            if n:
                logger.info("Publication expiry sweep: expired %s publication(s)", n)
        except Exception:
            logger.exception("Publication expiry sweep failed")
        interval = sweep_interval_seconds()
        if stop_event.wait(timeout=interval):
            break
