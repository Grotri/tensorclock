"""Helpers for psycopg Row / cursor results — access while connection is open, then plain dicts."""

from __future__ import annotations

from typing import Any


def row_to_plain_dict(row: Any) -> dict[str, Any]:
    """
    Copy a query row to a plain dict while the connection/cursor is still open.
    Do not use psycopg Row objects after the connection context exits.
    """
    if row is None:
        raise TypeError("row is None")
    if isinstance(row, dict):
        return dict(row)
    keys = getattr(row, "keys", None)
    if callable(keys):
        return {str(k): row[k] for k in row.keys()}
    raise TypeError(f"unsupported row type: {type(row)!r}")
