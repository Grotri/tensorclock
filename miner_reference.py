"""
Reference TensorClock miner (working implementation).

This file plugs a concrete ``MinerModel`` into ``miner_template.MinerRunner``.
It discovers **all** validator HTTP endpoints from chain commitments (no priority),
optionally filters validators by stake, and submits Epistula-signed JSON requests
when a wallet is configured.

Requirements:
  - Bittensor network access for discovery + commitments
  - Validators committed HTTP endpoints on-chain
  - When validator runs with ``EPISTULA_REQUIRED=true``, set wallet env
    (``WALLET_NAME`` / ``HOTKEY_NAME``) so requests are signed.

Run (all validators, full publication each)::

    python miner_reference.py --network finney --netuid 1 --miner-uid 1

Smoke (first validator only, one task)::

    python miner_reference.py --miner-uid 1 --smoke
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from typing import List, Optional, Union

from bittensor_wallet import Wallet
from dotenv import load_dotenv

from miner_template import (
    MinerModel,
    MinerRunner,
    OptimizationParams,
    TaskInfo,
    ValidatorClient,
    configure_logging,
    discover_validator_endpoints,
    task_from_claim_task_dict,
    validate_optimization_params,
)

logger = logging.getLogger(__name__)


def _str2bool(v: Union[str, bool]) -> bool:
    """CLI/env bool parsing (aligned with common subnet configs)."""
    if isinstance(v, bool):
        return v
    s = str(v).strip().lower()
    if s in ("1", "true", "yes", "on", "t"):
        return True
    if s in ("0", "false", "no", "off", "f"):
        return False
    raise argparse.ArgumentTypeError(f"expected boolean string, got {v!r}")


def _env_float(*keys: str, default: float = 0.0) -> float:
    for k in keys:
        raw = os.getenv(k)
        if raw is not None and str(raw).strip() != "":
            return float(raw)
    return default


def _env_bool(*keys: str, default: bool = True) -> bool:
    for k in keys:
        raw = os.getenv(k)
        if raw is not None and str(raw).strip() != "":
            return _str2bool(raw)
    return default


class NominalS19MinerModel(MinerModel):
    """
    Deterministic policy with safe values for bundled Antminer S19 spec:
      - frequency=600 MHz
      - voltage=13.0 V
      - fan_speed=100%
    """

    def predict(self, task: TaskInfo) -> OptimizationParams:
        return OptimizationParams(
            frequency=600.0,
            voltage=13.0,
            fan_speed=100.0,
        )


def _load_wallet(coldkey: str, hotkey: str) -> Wallet:
    return Wallet(name=coldkey, hotkey=hotkey)


def _resolve_validator_urls(args: argparse.Namespace) -> List[str]:
    if args.validator_url:
        return [args.validator_url.rstrip("/")]
    return discover_validator_endpoints(
        network=args.network,
        netuid=args.netuid,
        blacklist_validator_min_stake=float(args.blacklist_validator_min_stake),
        blacklist_force_validator_permit=bool(args.blacklist_force_validator_permit),
    )


def main(argv: Optional[list[str]] = None) -> int:
    load_dotenv()
    configure_logging()

    parser = argparse.ArgumentParser(description="TensorClock reference miner")
    parser.add_argument(
        "--validator-url",
        default=os.getenv("VALIDATOR_URL", "").strip(),
        help="Optional single-validator override. If empty, discover all validators on-chain.",
    )
    parser.add_argument(
        "--network",
        default=os.getenv("NETWORK", "finney"),
        help="Bittensor network (finney, test, local)",
    )
    parser.add_argument(
        "--netuid",
        type=int,
        default=int(os.getenv("NETUID", "1")),
        help="Subnet netuid used for commitment discovery",
    )
    parser.add_argument(
        "--blacklist.validator_min_stake",
        "--min-validator-stake",
        dest="blacklist_validator_min_stake",
        type=float,
        default=_env_float("BLACKLIST_VALIDATOR_MIN_STAKE", "MIN_VALIDATOR_STAKE", default=0.0),
        help=(
            "Skip validators with metagraph S[uid] <= this (same rule as mode-network/synth-subnet "
            "`--blacklist.validator_min_stake`). Default 0."
        ),
    )
    parser.add_argument(
        "--blacklist.force_validator_permit",
        dest="blacklist_force_validator_permit",
        type=_str2bool,
        default=_env_bool("BLACKLIST_FORCE_VALIDATOR_PERMIT", default=True),
        help=(
            "If true, only UIDs with validator_permit (synth-subnet `--blacklist.force_validator_permit`). "
            "Default true."
        ),
    )
    parser.add_argument(
        "--wallet-name",
        default=os.getenv("WALLET_NAME", "default"),
        help="Coldkey / wallet name for Epistula signing",
    )
    parser.add_argument(
        "--hotkey-name",
        default=os.getenv("HOTKEY_NAME", "default"),
        help="Hotkey name for Epistula signing",
    )
    parser.add_argument(
        "--no-wallet",
        action="store_true",
        help="Do not load wallet (only works if validator has EPISTULA_REQUIRED=false)",
    )
    parser.add_argument(
        "--miner-uid",
        type=int,
        default=int(os.getenv("MINER_UID", "0")),
        help="UID label stored in validator assignments (default: env MINER_UID or 0)",
    )
    parser.add_argument(
        "--asic-model",
        default=os.getenv("ASIC_MODEL", "Antminer S19"),
        help="Must match tasks in DB (default: Antminer S19)",
    )
    parser.add_argument(
        "--target",
        default=os.getenv("MINER_TARGET", "efficiency"),
        help="Optimization target (default: efficiency)",
    )
    parser.add_argument(
        "--smoke",
        action="store_true",
        help="Stop after the first successful task submit on the first validator (faster sanity check)",
    )
    args = parser.parse_args(argv)

    wallet: Optional[Wallet] = None
    if not args.no_wallet:
        wallet = _load_wallet(args.wallet_name, args.hotkey_name)

    try:
        urls = _resolve_validator_urls(args)
    except Exception as e:
        logger.error("Validator discovery failed: %s", e)
        return 1

    if args.smoke:
        urls = urls[:1]

    model = NominalS19MinerModel()

    for idx, validator_url in enumerate(urls):
        logger.info("Validator %s/%s: %s", idx + 1, len(urls), validator_url)
        client = ValidatorClient(validator_url, wallet=wallet)
        if not client.health():
            logger.error("Validator /health is not OK at %s", validator_url)
            return 1

        runner = MinerRunner(client)

        if args.smoke:
            r = client.claim_task(
                miner_uid=args.miner_uid,
                asic_model=args.asic_model,
                target=args.target,
                publication_id=None,
            )
            if r.status_code != 200:
                logger.error("claim failed: %s %s", r.status_code, r.text)
                return 1
            data = r.json()
            pub = data["publication_id"]
            task = task_from_claim_task_dict(data["task"])
            params = model.predict(task)
            errs = validate_optimization_params(params)
            if errs:
                logger.error("validation errors: %s", errs)
                return 1
            sr = client.submit(publication_id=pub, task_id=task.task_id, params=params)
            if sr.status_code != 200:
                logger.error("submit failed: %s %s", sr.status_code, sr.text)
                return 1
            body = sr.json()
            logger.info("smoke OK: state=%s overheated=%s", body.get("state"), body.get("overheated"))
            if body.get("state") != "completed":
                return 1
            return 0

        result = runner.run_publication(
            model,
            miner_uid=args.miner_uid,
            asic_model=args.asic_model,
            target=args.target,
            model_description_json={"model": "NominalS19MinerModel", "version": "1.0"},
        )
        logger.info(
            "publication_id=%s tasks_attempted=%s completed=%s last_state=%s",
            result.publication_id,
            result.tasks_attempted,
            result.publication_completed,
            result.last_submit_state,
        )
        if not result.publication_completed:
            logger.error("Publication did not complete for validator %s", validator_url)
            return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
