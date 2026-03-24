"""
Bitcoin hashprice from mempool.space public API (no API key).

Computes expected miner revenue for **1 TH/s** over **24 hours** in BTC and USD,
using current difficulty and average block reward (subsidy + fees) over a recent
window of blocks.

References (standard Bitcoin mining economics):
  expected BTC/s = hashrate_Hs * R_BTC_per_block / (difficulty * 2^32)
  where R includes coinbase subsidy and transaction fees for the block.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Final, Optional
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

# Default public instance; override with MEMPOOL_API_BASE for self-hosted nodes.
_DEFAULT_BASE: Final[str] = "https://mempool.space/api/v1"


@dataclass(frozen=True)
class HashpriceQuote:
    """Snapshot of hashprice derived from mempool.space."""

    btc_per_th_per_day: float
    usd_per_th_per_day: float
    btc_usd: float
    difficulty: float
    avg_reward_sats_per_block: float
    blocks_sampled: int
    start_block: int
    end_block: int
    as_of: str  # ISO UTC when the quote was built
    api_base: str


def _api_base() -> str:
    raw = os.getenv("MEMPOOL_API_BASE", _DEFAULT_BASE).strip().rstrip("/")
    return raw or _DEFAULT_BASE


def _get_json(path: str, *, timeout_s: float = 20.0) -> dict[str, Any]:
    base = _api_base()
    url = f"{base}{path}" if path.startswith("/") else f"{base}/{path}"
    req = Request(url, headers={"Accept": "application/json", "User-Agent": "tensorclock-hashprice/1.0"})
    try:
        with urlopen(req, timeout=timeout_s) as resp:
            data = json.load(resp)
    except json.JSONDecodeError as e:
        raise ValueError(f"invalid JSON from {url!r}") from e
    if not isinstance(data, dict):
        raise ValueError(f"expected JSON object from {url!r}, got {type(data).__name__}")
    return data


def btc_per_th_per_day(difficulty: float, reward_btc_per_block: float) -> float:
    """
    Expected BTC earned in 24h for **1 TH/s** (1e12 H/s) at given difficulty.

    Uses the usual relation: block discovery rate ≈ H / (D · 2^32) blocks/s,
    times reward per block, times 86400 s/day.
    """
    if difficulty <= 0:
        raise ValueError("difficulty must be positive")
    if reward_btc_per_block < 0:
        raise ValueError("reward_btc_per_block must be non-negative")
    h_th = 1e12
    return h_th * 86400.0 * reward_btc_per_block / (difficulty * float(2**32))


def _parse_reward_stats(data: dict[str, Any]) -> tuple[float, int, int, int]:
    """Return (avg_reward_sats_per_block, n_blocks, start_block, end_block)."""
    try:
        start = int(data["startBlock"])
        end = int(data["endBlock"])
        tr = data["totalReward"]
        tf = data["totalFee"]
    except KeyError as e:
        raise ValueError(f"reward-stats: missing field {e}") from e
    total_reward_sats = int(tr)
    total_fee_sats = int(tf)
    n = end - start + 1
    if n <= 0:
        raise ValueError("reward-stats: invalid block range")
    avg_sats = (total_reward_sats + total_fee_sats) / float(n)
    return avg_sats, n, start, end


def fetch_hashprice_quote(
    *,
    reward_blocks: int = 144,
    timeout_s: float = 20.0,
) -> HashpriceQuote:
    """
    Pull difficulty, rolling average reward, and BTC/USD from mempool.space and compute hashprice.

    ``reward_blocks`` — how many latest blocks to average (default 144 ≈ ~24h at 10 min/block).
    """
    if reward_blocks < 8:
        raise ValueError("reward_blocks must be at least 8 for a stable average")

    base = _api_base()
    now = datetime.now(timezone.utc).isoformat()

    hr = _get_json(f"/mining/hashrate/1d", timeout_s=timeout_s)
    try:
        difficulty = float(hr["currentDifficulty"])
    except (KeyError, TypeError) as e:
        raise ValueError("hashrate response: missing or invalid currentDifficulty") from e

    rs = _get_json(f"/mining/reward-stats/{int(reward_blocks)}", timeout_s=timeout_s)
    avg_sats, n_blk, sb, eb = _parse_reward_stats(rs)
    reward_btc = avg_sats / 1e8

    pr = _get_json("/prices", timeout_s=timeout_s)
    try:
        btc_usd = float(pr["USD"])
    except (KeyError, TypeError) as e:
        raise ValueError("prices response: missing or invalid USD") from e
    if btc_usd <= 0:
        raise ValueError("BTC/USD must be positive")

    btc_day = btc_per_th_per_day(difficulty, reward_btc)
    usd_day = btc_day * btc_usd

    return HashpriceQuote(
        btc_per_th_per_day=btc_day,
        usd_per_th_per_day=usd_day,
        btc_usd=btc_usd,
        difficulty=difficulty,
        avg_reward_sats_per_block=avg_sats,
        blocks_sampled=n_blk,
        start_block=sb,
        end_block=eb,
        as_of=now,
        api_base=base,
    )


def fetch_hashprice_quote_safe(
    **kwargs: Any,
) -> Optional[HashpriceQuote]:
    """
    Same as :func:`fetch_hashprice_quote` but returns ``None`` on network/parsing errors
    (HTTP, timeout, malformed JSON).
    """
    try:
        return fetch_hashprice_quote(**kwargs)
    except (HTTPError, URLError, TimeoutError, ValueError, OSError) as e:
        import logging

        logging.getLogger(__name__).warning("fetch_hashprice_quote_safe: %s", e)
        return None


__all__ = [
    "HashpriceQuote",
    "btc_per_th_per_day",
    "fetch_hashprice_quote",
    "fetch_hashprice_quote_safe",
]


def _main() -> None:
    q = fetch_hashprice_quote()
    print(f"API base: {q.api_base}")
    print(f"as_of: {q.as_of}")
    print(f"difficulty: {q.difficulty:.2e}")
    print(f"blocks_sampled: {q.blocks_sampled} ({q.start_block} … {q.end_block})")
    print(f"avg_reward_sats_per_block: {q.avg_reward_sats_per_block:,.2f}")
    print(f"BTC/USD: {q.btc_usd:,.2f}")
    print(f"BTC per TH/s per day: {q.btc_per_th_per_day:.8e}")
    print(f"USD per TH/s per day: {q.usd_per_th_per_day:.6f}")


if __name__ == "__main__":
    _main()
