"""
Publication wall-clock deadline: miner must finish all tasks before `publication_deadline_at`.

If the deadline passes, the publication is marked `expired`, `avg_net_profit=0`, and every
assignment in that publication is set to failed with `net_profit=0` (overwriting partial progress).

Configurable via env:
  PUBLICATION_DEADLINE_SECONDS (default 600)
  PUBLICATION_EXPIRE_SWEEP_INTERVAL_SEC (default 30, minimum 15)
  PUBLICATION_EXPIRE_SWEEP_BATCH_LIMIT (default 100, max 500)
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping

from init_db import connect


def publication_deadline_seconds() -> int:
    return max(1, int(os.getenv("PUBLICATION_DEADLINE_SECONDS", "600")))


def sweep_interval_seconds() -> int:
    """Minimum 15s so the background loop cannot spin unreasonably fast."""
    return max(15, int(os.getenv("PUBLICATION_EXPIRE_SWEEP_INTERVAL_SEC", "30")))


def sweep_batch_limit() -> int:
    """Cap per sweep to avoid huge single transactions."""
    return min(500, max(1, int(os.getenv("PUBLICATION_EXPIRE_SWEEP_BATCH_LIMIT", "100"))))


def sweep_scan_cap() -> int:
    """Max active rows examined per sweep (filter in Python for legacy NULL deadlines)."""
    return min(5000, max(sweep_batch_limit() * 20, 50))


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def deadline_iso_from_now() -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=publication_deadline_seconds())).isoformat()


def _parse_created_fallback(created_at: str) -> str:
    """Legacy rows without publication_deadline_at: created_at + deadline window."""
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
    """Compare ISO timestamps (UTC) lexicographically."""
    return deadline_iso < now_iso


def expire_publication(conn: Any, publication_id: str, now_iso: str) -> bool:
    """
    Transition publication from active -> expired and zero all assignments.
    Returns True only if this call transitioned the row from active -> expired (rowcount-based, race-safe).
    """
    cur = conn.execute(
        """
        UPDATE publications
        SET state='expired', completed_at=?, avg_net_profit=0.0
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
            completed_at=?
        WHERE publication_id=?
        """,
        (now_iso, publication_id),
    )
    return True


def expire_publication_if_overdue(conn: Any, publication_id: str, now_iso: str) -> bool:
    """
    If publication is active and past its deadline, expire and zero all tasks.
    Returns True if this call expired the publication.
    """
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
    deadline = effective_publication_deadline(row)
    if not is_deadline_passed(deadline, now_iso):
        return False
    return expire_publication(conn, publication_id, now_iso)


def expire_stale_publications(db_url: str, *, now_iso: str | None = None, limit: int | None = None) -> int:
    """
    Background sweep: expire up to `limit` overdue active publications per call.
    Uses one DB round-trip to list candidates, then one transaction to expire in batch.
    """
    now_iso = now_iso or _now_iso()
    max_batch = limit if limit is not None else sweep_batch_limit()
    cap = sweep_scan_cap()

    with connect(db_url) as conn:
        rows = conn.execute(
            """
            SELECT publication_id, publication_deadline_at, created_at
            FROM publications
            WHERE state='active'
            LIMIT ?
            """,
            (cap,),
        ).fetchall()

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
    """Daemon thread: sleep between sweeps; log only when work was done."""
    import logging

    logger = logging.getLogger(__name__)
    while not stop_event.is_set():
        interval = sweep_interval_seconds()
        if stop_event.wait(timeout=interval):
            break
        try:
            n = expire_stale_publications(db_url)
            if n:
                logger.info("Publication expiry sweep: expired %s publication(s)", n)
        except Exception:
            logger.exception("Publication expiry sweep failed")
