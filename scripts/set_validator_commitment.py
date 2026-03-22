#!/usr/bin/env python3
"""
Publish the TensorClock validator HTTP API base URL as subnet commitment (same field
``get_all_commitments(netuid)`` / ``miner_template.discover_validator_endpoints`` reads).

Use the **validator hotkey** wallet (the one you pass to ``validator.py --coldkey/--hotkey``).

Examples::

  # Local node (same as validator.py --network)
  python scripts/set_validator_commitment.py \\
    --network ws://127.0.0.1:9945 \\
    --netuid 2 \\
    --coldkey tval \\
    --hotkey tvalhot \\
    --url http://127.0.0.1:8090

  # Testnet (example)
  python scripts/set_validator_commitment.py \\
    --network test \\
    --netuid YOUR_NETUID \\
    --coldkey mycold \\
    --hotkey myhot \\
    --url https://your-validator.example.com:8090

After committing, miners can use discovery without ``--validator-url`` (still need
``--min-validator-stake -1`` on many local nets with zero stake, and
``--blacklist.force_validator_permit false`` if no permit).

Requires: ``bittensor`` + ``bittensor_wallet`` (see ``requirements.txt``).
"""

from __future__ import annotations

import argparse
import sys

import bittensor as bt
from bittensor_wallet import Wallet


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description="Set on-chain commitment to validator HTTP API URL for TensorClock discovery."
    )
    p.add_argument(
        "--network",
        default="finney",
        help="Subtensor endpoint (finney, test, local, or ws://... / chain URL).",
    )
    p.add_argument("--netuid", type=int, required=True, help="Subnet netuid.")
    p.add_argument("--coldkey", required=True, help="Wallet (coldkey) name.")
    p.add_argument("--hotkey", required=True, help="Hotkey name (validator).")
    p.add_argument(
        "--url",
        required=True,
        help="HTTP(S) base URL of validator API (no path), e.g. http://127.0.0.1:8090",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Only check registration and print; do not submit extrinsic.",
    )
    p.add_argument(
        "--no-wait-finalization",
        action="store_true",
        help="Pass wait_for_finalization=False to set_commitment (faster on some local nodes).",
    )
    args = p.parse_args(argv)

    raw = (args.url or "").strip().rstrip("/")
    if not raw.startswith("http://") and not raw.startswith("https://"):
        print("ERROR: --url must start with http:// or https://", file=sys.stderr)
        return 1

    wallet = Wallet(name=args.coldkey, hotkey=args.hotkey)
    subtensor = bt.Subtensor(network=args.network)
    metagraph = bt.Metagraph(netuid=args.netuid, network=args.network)
    metagraph.sync(subtensor=subtensor)

    hk = wallet.hotkey.ss58_address
    if hk not in metagraph.hotkeys:
        print(
            f"ERROR: hotkey {hk} is not registered on netuid {args.netuid}. "
            "Register the validator on this subnet first.",
            file=sys.stderr,
        )
        return 1

    uid = metagraph.hotkeys.index(hk)
    print(f"Wallet hotkey registered: uid={uid} netuid={args.netuid}")
    print(f"Commitment payload (HTTP API base): {raw}")

    if args.dry_run:
        print("Dry run: skipping set_commitment.")
        return 0

    resp = subtensor.set_commitment(
        wallet,
        args.netuid,
        raw,
        wait_for_finalization=not args.no_wait_finalization,
    )
    if not getattr(resp, "success", False):
        err = getattr(resp, "error", None) or getattr(resp, "message", None) or resp
        print(f"ERROR: set_commitment failed: {err}", file=sys.stderr)
        return 1

    print("set_commitment: OK")

    try:
        current = subtensor.get_commitment(args.netuid, uid)
        print(f"get_commitment(netuid={args.netuid}, uid={uid}): {current!r}")
    except Exception as e:
        print(f"(Could not read back commitment: {e})")

    print(
        "\nMiner discovery checklist: same --network/--netuid; "
        "if stake is 0, use --min-validator-stake -1; "
        "if no validator_permit, use --blacklist.force_validator_permit false; "
        "ensure GET {url}/health returns 200 from the machine running the miner."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
