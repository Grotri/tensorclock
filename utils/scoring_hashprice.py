from __future__ import annotations

import logging
import os
import threading
import time
from datetime import datetime, timezone
from typing import Any, Optional

from utils.init_db import connect
from utils.hashprice_mempool import fetch_hashprice_quote
from validator.task_manager import EXPECTED_TASKS_PER_PUBLICATION
from utils.version import DB_SCHEMA_VERSION, TASK_CREATOR_VERSION

logger = logging.getLogger(__name__)

def _hashprice_ttl_sec() -> int:
    raw = os.getenv("HASHPRICE_TTL_SEC", str(5 * 3600)).strip()
    return int(raw or str(5 * 3600))

_refresh_lock = threading.Lock()
_refresh_thread: Optional[threading.Thread] = None


def _parse_iso_utc(s: str) -> datetime:
    raw = str(s).replace("Z", "+00:00")
    dt = datetime.fromisoformat(raw)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def is_hashprice_stale(updated_at_iso: str) -> bool:
    try:
        dt = _parse_iso_utc(updated_at_iso)
        age = (datetime.now(timezone.utc) - dt).total_seconds()
        return age >= float(_hashprice_ttl_sec())
    except Exception:
        return True


def get_cached_usd_per_th_day(conn: Any) -> Optional[float]:
    row = conn.execute(
        "SELECT usd_per_th_per_day FROM hashprice_cache WHERE id=1",
    ).fetchone()
    if row is None or row.get("usd_per_th_per_day") is None:
        return None
    return float(row["usd_per_th_per_day"])


def upsert_hashprice_cache(conn: Any, *, q: Any) -> None:
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """
        INSERT INTO hashprice_cache (id, usd_per_th_per_day, btc_per_th_per_day, btc_usd, updated_at)
        VALUES (1, ?, ?, ?, ?)
        ON CONFLICT (id) DO UPDATE SET
            usd_per_th_per_day = EXCLUDED.usd_per_th_per_day,
            btc_per_th_per_day = EXCLUDED.btc_per_th_per_day,
            btc_usd = EXCLUDED.btc_usd,
            updated_at = EXCLUDED.updated_at
        """,
        (float(q.usd_per_th_per_day), float(q.btc_per_th_per_day), float(q.btc_usd), now),
    )


def bulk_recompute_dollar_values_and_leader(conn: Any, usd_per_th_day: float) -> None:
    conn.execute(
        """
        UPDATE assignments
        SET dollar_value = COALESCE(net_profit, 0)
        WHERE state IN ('completed', 'failed')
        """
    )
    conn.execute(
        """
        UPDATE publications
        SET dollar_value = COALESCE(avg_net_profit, 0)
        WHERE state = 'completed' AND avg_net_profit IS NOT NULL
        """
    )
    conn.execute(
        """
        UPDATE publications
        SET dollar_value = 0.0, weight = 0
        WHERE state = 'expired' OR state = 'cancelled'
        """,
    )
    recompute_leader_weights(conn)


def recompute_leader_weights(conn: Any) -> None:
    # Leaders: only publications with no failed assignments (unsafe / out-of-limits runs excluded).
    conn.execute(
        """
        UPDATE publications
        SET weight = 0
        WHERE state = 'completed'
          AND tasks_creator_version = ?
          AND tasks_schema_version = ?
        """,
        (TASK_CREATOR_VERSION, DB_SCHEMA_VERSION),
    )
    conn.execute(
        """
        WITH leaders AS (
            SELECT DISTINCT ON (p.asic_model)
                p.publication_id
            FROM publications p
            WHERE p.state = 'completed'
              AND p.tasks_creator_version = ?
              AND p.tasks_schema_version = ?
              AND NOT EXISTS (
                SELECT 1 FROM assignments a
                WHERE a.publication_id = p.publication_id AND a.state = 'failed'
              )
            ORDER BY
                p.asic_model,
                p.dollar_value DESC NULLS LAST,
                p.avg_net_profit DESC NULLS LAST,
                p.completed_at ASC
        )
        UPDATE publications p
        SET weight = 1
        FROM leaders l
        WHERE p.publication_id = l.publication_id
          AND p.state = 'completed'
          AND p.tasks_creator_version = ?
          AND p.tasks_schema_version = ?
        """,
        (
            TASK_CREATOR_VERSION,
            DB_SCHEMA_VERSION,
            TASK_CREATOR_VERSION,
            DB_SCHEMA_VERSION,
        ),
    )


def apply_scores_after_assignment_update(
    conn: Any,
    *,
    publication_id: str,
    task_id: str,
    net_profit: Optional[float],
) -> None:
    usd = get_cached_usd_per_th_day(conn)
    if usd is None:
        return
    nv = float(net_profit) if net_profit is not None else 0.0
    conn.execute(
        """
        UPDATE assignments
        SET dollar_value = ?
        WHERE publication_id = ? AND task_id = ?
        """,
        (nv, publication_id, task_id),
    )


def apply_scores_after_publication_completed(conn: Any, *, publication_id: str) -> None:
    usd = get_cached_usd_per_th_day(conn)
    if usd is None:
        return
    conn.execute(
        """
        UPDATE publications
        SET dollar_value = COALESCE(avg_net_profit, 0)
        WHERE publication_id = ? AND state = 'completed'
        """,
        (publication_id,),
    )
    recompute_leader_weights(conn)


def blocking_fetch_initial_hashprice(db_url: str) -> None:
    delay = 5.0
    max_delay = 120.0
    while True:
        try:
            q = fetch_hashprice_quote(timeout_s=45.0)
            with connect(db_url) as conn:
                upsert_hashprice_cache(conn, q=q)
                bulk_recompute_dollar_values_and_leader(conn, float(q.usd_per_th_per_day))
            logger.info(
                "hashprice: initial cache OK usd_per_th_day=%.6f btc_usd=%.2f",
                q.usd_per_th_per_day,
                q.btc_usd,
            )
            return
        except Exception as e:
            logger.warning(
                "hashprice: initial fetch failed (%s); retry in %.0fs",
                e,
                delay,
            )
            time.sleep(delay)
            delay = min(max_delay, delay * 1.5)


def _refresh_worker(db_url: str) -> None:
    try:
        q = fetch_hashprice_quote(timeout_s=45.0)
    except Exception as e:
        logger.warning("hashprice: background refresh failed (%s); keeping previous cache", e)
        return
    try:
        with connect(db_url) as conn:
            upsert_hashprice_cache(conn, q=q)
            bulk_recompute_dollar_values_and_leader(conn, float(q.usd_per_th_per_day))
        logger.info(
            "hashprice: refreshed usd_per_th_day=%.6f",
            q.usd_per_th_per_day,
        )
    except Exception:
        logger.exception("hashprice: DB update after refresh failed")


def schedule_hashprice_refresh_if_stale(db_url: str) -> None:
    global _refresh_thread
    with _refresh_lock:
        if _refresh_thread is not None and _refresh_thread.is_alive():
            return
        try:
            with connect(db_url) as conn:
                row = conn.execute(
                    "SELECT updated_at FROM hashprice_cache WHERE id=1",
                ).fetchone()
                if row is None:
                    stale = True
                else:
                    # Row must be read before connection closes (psycopg Row is invalid after).
                    stale = is_hashprice_stale(str(row["updated_at"]))
            if not stale:
                return
        except Exception:
            return

        def run() -> None:
            try:
                _refresh_worker(db_url)
            finally:
                global _refresh_thread
                with _refresh_lock:
                    _refresh_thread = None

        _refresh_thread = threading.Thread(target=run, daemon=True, name="hashprice-refresh")
        _refresh_thread.start()


__all__ = [
    "apply_scores_after_assignment_update",
    "apply_scores_after_publication_completed",
    "blocking_fetch_initial_hashprice",
    "bulk_recompute_dollar_values_and_leader",
    "get_cached_usd_per_th_day",
    "is_hashprice_stale",
    "recompute_leader_weights",
    "schedule_hashprice_refresh_if_stale",
    "upsert_hashprice_cache",
]
