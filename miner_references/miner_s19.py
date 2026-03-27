#!/usr/bin/env python3
from __future__ import annotations

import argparse
import logging
import sys
from typing import List, Optional, Union
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from bittensor_wallet import Wallet  # noqa: E402
from utils.config_utils import cfg_get, load_toml_config  # noqa: E402
from miner.miner_template import (  # noqa: E402
    MinerModel,
    MinerRunner,
    OptimizationParams,
    TaskSubmitFeedback,
    TaskInfo,
    ValidatorClient,
    configure_logging,
    discover_validator_endpoints,
    safe_import_bittensor,
    task_from_claim_task_dict,
    validate_optimization_params,
)

logger = logging.getLogger(__name__)


def _str2bool(v: Union[str, bool]) -> bool:
    if isinstance(v, bool):
        return v
    s = str(v).strip().lower()
    if s in ("1", "true", "yes", "on", "t"):
        return True
    if s in ("0", "false", "no", "off", "f"):
        return False
    raise argparse.ArgumentTypeError(f"expected boolean string, got {v!r}")


class MinerS19Model(MinerModel):
    _CANDIDATES: tuple[OptimizationParams, ...] = (
        OptimizationParams(frequency=580.0, voltage=12.8, fan_speed=90.0),
        OptimizationParams(frequency=600.0, voltage=13.0, fan_speed=95.0),
        OptimizationParams(frequency=620.0, voltage=13.1, fan_speed=100.0),
        OptimizationParams(frequency=640.0, voltage=13.2, fan_speed=100.0),
    )

    def __init__(self) -> None:
        self._task_state: dict[str, dict[str, object]] = {}

    def _state_for(self, task_id: str) -> dict[str, object]:
        st = self._task_state.get(task_id)
        if st is None:
            st = {"next_idx": 0, "last_params": None, "best_params": None, "best_profit": None, "replay_best_once": False}
            self._task_state[task_id] = st
        return st

    def predict(self, task: TaskInfo) -> OptimizationParams:
        st = self._state_for(task.task_id)
        if bool(st["replay_best_once"]) and isinstance(st["best_params"], OptimizationParams):
            params = st["best_params"]
            st["replay_best_once"] = False
            st["last_params"] = params
            return params
        idx = int(st["next_idx"])
        if idx >= len(self._CANDIDATES):
            idx = len(self._CANDIDATES) - 1
        params = self._CANDIDATES[idx]
        st["next_idx"] = int(st["next_idx"]) + 1
        st["last_params"] = params
        return params

    def should_continue(self, task: TaskInfo, feedback: TaskSubmitFeedback) -> bool:
        st = self._state_for(task.task_id)
        cur_profit = feedback.net_profit_usd_day
        if cur_profit is not None:
            best_profit = st["best_profit"]
            if best_profit is None or float(cur_profit) > float(best_profit):
                st["best_profit"] = float(cur_profit)
                st["best_params"] = st["last_params"]
        if not feedback.can_continue or feedback.remaining_queries <= 0:
            self._task_state.pop(task.task_id, None)
            return False
        if int(st["next_idx"]) < len(self._CANDIDATES):
            return True
        if isinstance(st["best_params"], OptimizationParams) and st["last_params"] != st["best_params"]:
            st["replay_best_once"] = True
            return True
        self._task_state.pop(task.task_id, None)
        return False


def _load_wallet(coldkey: str, hotkey: str) -> Wallet:
    return Wallet(name=coldkey, hotkey=hotkey)


def _effective_miner_uid_on_chain(network: str, netuid: int, cli_uid: int, wallet: Optional[Wallet]) -> int:
    if wallet is None:
        return int(cli_uid)
    try:
        bt = safe_import_bittensor()
    except ImportError:
        logger.warning("bittensor not installed; using --miner-uid=%s", cli_uid)
        return int(cli_uid)
    try:
        sub = bt.Subtensor(network=network)
        uid = sub.get_uid_for_hotkey_on_subnet(wallet.hotkey.ss58_address, netuid)
    except Exception as e:
        logger.warning("Could not resolve miner UID on chain (%s); using --miner-uid=%s", e, cli_uid)
        return int(cli_uid)
    if uid is None:
        logger.warning("Miner hotkey is not registered on netuid %s; using --miner-uid=%s", netuid, cli_uid)
        return int(cli_uid)
    if int(uid) != int(cli_uid):
        logger.info("Using on-chain miner UID=%s (wallet hotkey) instead of --miner-uid=%s", uid, cli_uid)
    return int(uid)


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
    bootstrap = argparse.ArgumentParser(add_help=False)
    bootstrap.add_argument("--config", default="configs/miner_config.toml")
    boot_args, _ = bootstrap.parse_known_args(argv)
    cfg = load_toml_config(boot_args.config)

    parser = argparse.ArgumentParser(description="TensorClock S19 miner")
    parser.add_argument("--config", default=boot_args.config)
    parser.add_argument("--log-level", default=str(cfg_get(cfg, "miner.log_level", "INFO")))
    parser.add_argument("--validator-url", default=str(cfg_get(cfg, "miner.validator_url", "")))
    parser.add_argument("--network", default=str(cfg_get(cfg, "miner.network", "finney")))
    parser.add_argument("--netuid", type=int, default=int(cfg_get(cfg, "miner.netuid", 1)))
    parser.add_argument("--blacklist.validator_min_stake", "--min-validator-stake", dest="blacklist_validator_min_stake", type=float, default=float(cfg_get(cfg, "miner.blacklist_validator_min_stake", -1.0)))
    parser.add_argument("--blacklist.force_validator_permit", dest="blacklist_force_validator_permit", type=_str2bool, default=bool(cfg_get(cfg, "miner.blacklist_force_validator_permit", True)))
    parser.add_argument("--wallet-name", default=str(cfg_get(cfg, "miner.wallet_name", "default")))
    parser.add_argument("--hotkey-name", default=str(cfg_get(cfg, "miner.hotkey_name", "default")))
    parser.add_argument("--no-wallet", action="store_true", default=bool(cfg_get(cfg, "miner.no_wallet", False)))
    parser.add_argument("--miner-uid", type=int, default=int(cfg_get(cfg, "miner.miner_uid", 0)))
    parser.add_argument("--asic-model", default="Antminer S19")
    parser.add_argument("--target", default=str(cfg_get(cfg, "miner.target", "efficiency")))
    parser.add_argument("--smoke", action="store_true", default=bool(cfg_get(cfg, "miner.smoke", False)))
    args = parser.parse_args(argv)
    configure_logging(level=getattr(logging, str(args.log_level).upper(), logging.INFO))

    wallet: Optional[Wallet] = None
    if not args.no_wallet:
        wallet = _load_wallet(args.wallet_name, args.hotkey_name)
    miner_uid = _effective_miner_uid_on_chain(args.network, args.netuid, args.miner_uid, wallet)
    if wallet is not None:
        miner_hotkey_ss58 = str(wallet.hotkey.ss58_address)
    else:
        miner_hotkey_ss58 = str(cfg_get(cfg, "miner.miner_hotkey", "")).strip()
        if not miner_hotkey_ss58:
            logger.error("With --no-wallet, set miner.miner_hotkey in config.")
            return 1

    try:
        urls = _resolve_validator_urls(args)
    except Exception as e:
        logger.error("Validator discovery failed: %s", e)
        return 1
    if args.smoke:
        urls = urls[:1]

    model = MinerS19Model()
    for idx, validator_url in enumerate(urls):
        logger.info("Validator %s/%s: %s", idx + 1, len(urls), validator_url)
        client = ValidatorClient(validator_url, wallet=wallet)
        if not client.health():
            logger.error("Validator /health is not OK at %s", validator_url)
            return 1
        runner = MinerRunner(client)
        if args.smoke:
            r = client.claim_task(miner_uid=miner_uid, miner_hotkey=miner_hotkey_ss58, asic_model=args.asic_model, target=args.target, publication_id=None)
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
            logger.info("smoke submit: state=%s q=%s rem=%s net_usd_day=%s can_continue=%s", body.get("state"), body.get("queries_used"), body.get("remaining_queries"), body.get("net_profit_usd_day"), body.get("can_continue"))
            if bool(body.get("can_continue")):
                dr = client.decide_task(publication_id=pub, task_id=task.task_id, action="finalize")
                if dr.status_code != 200:
                    logger.error("decision failed: %s %s", dr.status_code, dr.text)
                    return 1
            return 0

        result = runner.run_publication(model, miner_uid=miner_uid, miner_hotkey=miner_hotkey_ss58, asic_model=args.asic_model, target=args.target, model_description_json={"model": "MinerS19Model", "version": "1.0"})
        logger.info("publication_id=%s tasks_attempted=%s completed=%s last_state=%s", result.publication_id, result.tasks_attempted, result.publication_completed, result.last_submit_state)
        if not result.publication_completed:
            logger.error("Publication did not complete for validator %s", validator_url)
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

