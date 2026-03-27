import os
import time
import argparse
import logging
from urllib.parse import urlparse
from bittensor_wallet import Wallet
import threading
import sys
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Optional

import uvicorn

logger = logging.getLogger(__name__)

HEARTBEAT_TIMEOUT = 600

from init_db import connect, init_db
from publication_expiry import publication_expiry_sweep_loop
from scoring_hashprice import blocking_fetch_initial_hashprice
from task_manager import generate_miner_task_bundle
from validator_api import app, init_validator_api
from version import DB_SCHEMA_VERSION, TASK_CREATOR_VERSION
from config_utils import cfg_get, load_toml_config
from logging_utils import setup_logging, uvicorn_log_config


def _strip_cli_arg(argv: list[str], name: str) -> list[str]:
    """
    Remove all occurrences of '--name value' and '--name=value' from argv.
    Used to prevent collisions with bittensor's internal argparse during import.
    """
    out: list[str] = []
    i = 0
    while i < len(argv):
        token = str(argv[i])
        if token == name:
            i += 2
            continue
        if token.startswith(f"{name}="):
            i += 1
            continue
        out.append(token)
        i += 1
    return out


def _extrinsic_succeeded(resp: Any) -> bool:
    if resp is None:
        return False
    if isinstance(resp, bool):
        return resp
    success = getattr(resp, "success", None)
    if success is not None:
        return bool(success)
    if isinstance(resp, tuple) and len(resp) > 0:
        return bool(resp[0])
    return False


def _chain_hotkey_at_uid(metagraph: Any, uid: int) -> str:
    n = int(getattr(metagraph, "n", 0) or 0)
    if uid < 0 or uid >= n:
        return ""
    hks = getattr(metagraph, "hotkeys", None)
    if hks is None:
        return ""
    try:
        return str(hks[uid]).strip()
    except (IndexError, TypeError, KeyError):
        return ""


def _hotkey_matches_uid(metagraph: Any, uid: int, expected_hotkey: str | None) -> bool:
    if expected_hotkey is None:
        return False
    exp = str(expected_hotkey).strip()
    if not exp:
        return False
    return _chain_hotkey_at_uid(metagraph, uid) == exp


def _pick_winner_uid_from_completed_for_model(
    conn: Any,
    metagraph: Any,
    *,
    asic_model: str,
    tasks_creator_version: str,
    tasks_schema_version: str,
) -> tuple[Optional[int], Optional[str]]:
    rows = conn.execute(
        """
        SELECT miner_uid, miner_hotkey
        FROM publications
        WHERE state='completed'
          AND asic_model = ?
          AND tasks_creator_version = ?
          AND tasks_schema_version = ?
          AND miner_hotkey IS NOT NULL
          AND TRIM(miner_hotkey) <> ''
        ORDER BY dollar_value DESC NULLS LAST, avg_net_profit DESC NULLS LAST, completed_at ASC
        LIMIT 64
        """,
        (asic_model, tasks_creator_version, tasks_schema_version),
    ).fetchall()
    n = int(getattr(metagraph, "n", 0) or 0)
    for row in rows:
        uid = row.get("miner_uid")
        hk = row.get("miner_hotkey")
        if uid is None or hk is None:
            continue
        uid_i = int(uid)
        if uid_i < 0 or uid_i >= n:
            continue
        if _hotkey_matches_uid(metagraph, uid_i, str(hk)):
            return uid_i, str(hk).strip()

    rows_fb = conn.execute(
        """
        SELECT miner_uid, miner_hotkey
        FROM publications
        WHERE state='completed'
          AND asic_model = ?
          AND miner_hotkey IS NOT NULL
          AND TRIM(miner_hotkey) <> ''
        ORDER BY dollar_value DESC NULLS LAST, avg_net_profit DESC NULLS LAST, completed_at DESC NULLS LAST
        LIMIT 64
        """,
        (asic_model,),
    ).fetchall()
    for row in rows_fb:
        uid = row.get("miner_uid")
        hk = row.get("miner_hotkey")
        if uid is None or hk is None:
            continue
        uid_i = int(uid)
        if uid_i < 0 or uid_i >= n:
            continue
        if _hotkey_matches_uid(metagraph, uid_i, str(hk)):
            logger.info(
                "weights: using completed publication (version fallback) miner_uid=%s",
                uid_i,
            )
            return uid_i, str(hk).strip()
    return None, None


def _extrinsic_detail(resp: Any) -> str:
    if resp is None:
        return "None"
    for name in ("message", "error_message", "err_msg", "reason"):
        v = getattr(resp, name, None)
        if v not in (None, ""):
            return str(v)
    err = getattr(resp, "error", None)
    if err not in (None, ""):
        return str(err)
    data = getattr(resp, "data", None)
    if data not in (None, ""):
        return str(data)
    receipt = getattr(resp, "extrinsic_receipt", None)
    if receipt is not None:
        # Common receipt details exposed by substrate clients.
        for name in ("error_message", "error", "dispatch_error", "message"):
            v = getattr(receipt, name, None)
            if v not in (None, ""):
                return f"{name}={v}"
        try:
            events = getattr(receipt, "triggered_events", None)
            if events:
                # Include the latest event as a fallback clue.
                last = events[-1]
                return f"receipt_event={last}"
        except Exception:
            pass
    return repr(resp)


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or str(raw).strip() == "":
        return default
    return str(raw).strip().lower() in ("1", "true", "yes", "on")


def _weight_tick_interval_blocks(tempo: int) -> int:
    return max(1, int(tempo))


def emit_incentive_weights(
    *,
    subtensor: Any,
    wallet: Wallet,
    netuid: int,
    winner_weights: dict[int, float],
    mev_protection: bool,
    wait_for_finalization: bool,
    block_time: float,
    period_blocks: Optional[int],
) -> Any:
    uids_sorted = sorted(int(uid) for uid in winner_weights.keys())
    weights_sorted = [float(winner_weights[uid]) for uid in uids_sorted]
    kwargs: dict[str, Any] = {
        "wallet": wallet,
        "netuid": netuid,
        "uids": uids_sorted,
        "weights": weights_sorted,
        "wait_for_inclusion": True,
        "wait_for_finalization": wait_for_finalization,
        "mev_protection": mev_protection,
        "block_time": block_time,
    }
    if period_blocks is not None:
        kwargs["period"] = period_blocks
    return subtensor.set_weights(**kwargs)


def heartbeat_monitor(last_heartbeat, stop_event):
    while not stop_event.is_set():
        time.sleep(5)
        if time.time() - last_heartbeat[0] > HEARTBEAT_TIMEOUT:
            logger.error("No heartbeat detected in the last 600 seconds. Restarting process.")
            logging.shutdown(); os.execv(sys.executable, [sys.executable] + sys.argv)

def main(argv: Optional[list[str]] = None):
    bootstrap = argparse.ArgumentParser(add_help=False)
    bootstrap.add_argument("--config", default="configs/validator_config.toml")
    boot_args, _ = bootstrap.parse_known_args(argv)
    cfg = load_toml_config(boot_args.config)

    parser = argparse.ArgumentParser(description="Run TensorClock validator")
    parser.add_argument("--config", default=boot_args.config, help="Path to validator TOML config")
    parser.add_argument("--network", default=str(cfg_get(cfg, "validator.network", "finney")))
    parser.add_argument("--netuid", type=int, default=int(cfg_get(cfg, "validator.netuid", 1)))
    parser.add_argument("--coldkey", default=str(cfg_get(cfg, "validator.wallet_name", "default")))
    parser.add_argument("--hotkey", default=str(cfg_get(cfg, "validator.hotkey_name", "default")))
    parser.add_argument("--log-level", default=str(cfg_get(cfg, "validator.log_level", "INFO")))
    parser.add_argument("--api-port", type=int, default=int(cfg_get(cfg, "validator.api_port", 8090)))
    parser.add_argument("--validator-api-url", default=str(cfg_get(cfg, "validator.validator_api_url", "")).strip())
    args = parser.parse_args(argv)

    network = args.network
    netuid = int(args.netuid)
    coldkey = args.coldkey
    hotkey = args.hotkey
    log_level = args.log_level
    api_port = int(args.api_port)
    validator_api_url = args.validator_api_url or None

    db_url_cfg = str(cfg_get(cfg, "validator.database_url", "")).strip()
    if not db_url_cfg:
        raise RuntimeError("validator.database_url is required in config to run the validator.")

    env_pairs = {
        "DATABASE_URL": db_url_cfg,
        "VALIDATOR_SIM_WORKERS": str(cfg_get(cfg, "validator.sim_workers", 4)),
        "VALIDATOR_API_PORT": str(cfg_get(cfg, "validator.api_port", 8090)),
        "EPISTULA_REQUIRED": "true" if bool(cfg_get(cfg, "validator.epistula_required", True)) else "false",
        "PUBLICATION_DEADLINE_SECONDS": str(cfg_get(cfg, "validator.publication_deadline_seconds", 600)),
        "PUBLICATION_EXPIRE_SWEEP_INTERVAL_SEC": str(cfg_get(cfg, "validator.publication_expire_sweep_interval_sec", 30)),
        "PUBLICATION_EXPIRE_SWEEP_BATCH_LIMIT": str(cfg_get(cfg, "validator.publication_expire_sweep_batch_limit", 100)),
        "HASHPRICE_TTL_SEC": str(cfg_get(cfg, "validator.hashprice_ttl_sec", 5 * 3600)),
        "MEMPOOL_API_BASE": str(cfg_get(cfg, "validator.mempool_api_base", "https://mempool.space/api/v1")),
        "VALIDATOR_MEV_PROTECTION": "true" if bool(cfg_get(cfg, "validator.weight_mev_protection", False)) else "false",
        "VALIDATOR_WAIT_FOR_FINALIZATION": "true"
        if bool(cfg_get(cfg, "validator.weight_wait_for_finalization", True))
        else "false",
        "VALIDATOR_WEIGHT_FAIL_COOLDOWN_SEC": str(cfg_get(cfg, "validator.weight_fail_cooldown_sec", 120)),
        "VALIDATOR_WEIGHT_TX_PERIOD_BLOCKS": str(cfg_get(cfg, "validator.weight_tx_period_blocks", 0)),
        "VALIDATOR_WEIGHT_BLOCK_TIME_SEC": str(cfg_get(cfg, "validator.weight_block_time_sec", 12.0)),
    }
    for k, v in env_pairs.items():
        if v != "":
            os.environ[k] = v

    # Set unified SDK-style logging (console + dated file + latest file).
    setup_logging(app_name="validator", level=log_level)

    # bittensor parses process argv during import and treats --config as YAML path.
    # Our app uses TOML config, so strip app-level --config before importing bittensor.
    sys.argv = _strip_cli_arg(list(sys.argv), "--config")
    import bittensor as bt
    # bittensor import may reset root handlers/levels to defaults. Restore app logging afterwards.
    setup_logging(app_name="validator", level=log_level)
    # bittensor may also elevate existing logger levels (e.g. __main__) and hide INFO logs.
    app_log_level = getattr(logging, str(log_level).upper(), logging.INFO)
    logging.getLogger(__name__).setLevel(app_log_level)
    logging.getLogger("__main__").setLevel(app_log_level)
    logger.info(f"Starting validator on network={network}, netuid={netuid}")

    last_heartbeat = [time.time()]
    stop_event = threading.Event()
    heartbeat_thread = threading.Thread(target=heartbeat_monitor, args=(last_heartbeat, stop_event), daemon=True)
    heartbeat_thread.start()

    pub_expiry_thread = None
    try:
        wallet = Wallet(name=coldkey, hotkey=hotkey)
        subtensor = bt.Subtensor(network=network)
        metagraph = bt.Metagraph(netuid=netuid, network=network)
        metagraph.sync(subtensor=subtensor)

        my_hotkey = wallet.hotkey.ss58_address
        if my_hotkey not in metagraph.hotkeys:
            logger.error("Hotkey %s is not registered on netuid %s", my_hotkey, netuid)
            stop_event.set()
            return
        my_uid = metagraph.hotkeys.index(my_hotkey)
        logger.info("Validator UID: %s", my_uid)

        tempo = subtensor.tempo(netuid)
        if tempo is None:
            logger.error("subtensor.tempo(%s) returned None", netuid)
            stop_event.set()
            return
        tempo = int(tempo)
        epoch_interval_blocks = _weight_tick_interval_blocks(tempo)

        init_db()
        
        from virtual_device_generator import VirtualDeviceGenerator
        bootstrap_generator = VirtualDeviceGenerator()
        bootstrap_generator.load_builtin_specifications()
        supported_models = bootstrap_generator.get_available_models()
        logger.info("Task bootstrap for supported ASIC models: %s", supported_models)
        for model_name in supported_models:
            bundle = generate_miner_task_bundle(
                asic_model=str(model_name),
                devices_count=int(cfg_get(cfg, "validator.bundle_devices_count", 5)),
                query_budget=int(cfg_get(cfg, "validator.bundle_query_budget", 10)),
                target=str(cfg_get(cfg, "validator.bundle_target", "efficiency")),
            )
            logger.info(
                "Task bundle ensured for model=%s devices=%s tasks=%s",
                model_name,
                len(bundle.devices),
                len(bundle.tasks),
            )

        db_url = db_url_cfg

        logger.info("Fetching initial hashprice")
        blocking_fetch_initial_hashprice(db_url)

        generator = bootstrap_generator

        sim_workers = int(cfg_get(cfg, "validator.sim_workers", 4))
        executor = ThreadPoolExecutor(max_workers=sim_workers)
        init_validator_api(db_url=db_url, generator=generator, executor=executor)

        pub_expiry_thread = threading.Thread(
            target=publication_expiry_sweep_loop,
            args=(db_url, stop_event),
            daemon=True,
            name="publication-expiry-sweep",
        )
        pub_expiry_thread.start()
        logger.info("Publication deadline sweep started")

        if validator_api_url:
            parsed = urlparse(str(validator_api_url).strip())
            if parsed.port is None:
                raise RuntimeError(
                    "--validator-api-url must include an explicit port, e.g. http://127.0.0.1:8091"
                )
            listen_port = int(parsed.port)
        elif api_port is not None:
            listen_port = int(api_port)
        else:
            listen_port = int(cfg_get(cfg, "validator.api_port", 8090))
        server = uvicorn.Server(
            uvicorn.Config(
                app,
                host="0.0.0.0",
                port=listen_port,
                log_level="info",
                log_config=uvicorn_log_config(),
            )
        )
        threading.Thread(target=server.run, daemon=True).start()
        logger.info("Validator API listening on 0.0.0.0:%s (e.g. http://127.0.0.1:%s)", listen_port, listen_port)

        mev_on = _env_bool("VALIDATOR_MEV_PROTECTION", default=False)
        if mev_on:
            logger.warning(
                "VALIDATOR_MEV_PROTECTION=true: MEV shield may fail with hotkey-signed set_weights."
            )
        wait_fin = _env_bool("VALIDATOR_WAIT_FOR_FINALIZATION", default=True)
        # After a failed set_weights, chain LastUpdate does not advance, so bslu stays high and we would
        # retry every loop (~12s) and spam the node. Cool down before retrying (time-based; works on fast blocks).
        fail_cooldown_sec = float(os.getenv("VALIDATOR_WEIGHT_FAIL_COOLDOWN_SEC", "120"))
        tx_period = os.getenv("VALIDATOR_TX_PERIOD_BLOCKS", "").strip()
        if not tx_period:
            # Preferred key written from TOML config.
            tx_period = os.getenv("VALIDATOR_WEIGHT_TX_PERIOD_BLOCKS", "").strip()
        if tx_period.isdigit() and int(tx_period) > 0:
            tx_period_i = int(tx_period)
        else:
            tx_period_i = None

        _wbt_raw = os.getenv("VALIDATOR_WEIGHT_BLOCK_TIME_SEC", "").strip()
        if _wbt_raw:
            try:
                weight_block_time = float(_wbt_raw)
                if weight_block_time <= 0:
                    raise ValueError("must be > 0")
            except ValueError:
                logger.warning(
                    "VALIDATOR_WEIGHT_BLOCK_TIME_SEC=%r invalid; using 12.0",
                    _wbt_raw,
                )
                weight_block_time = 12.0
        else:
            weight_block_time = 12.0

        last_weight_fail_time: float = 0.0
        last_weight_tick_block: Optional[int] = None

        # If tx_period is explicitly configured, use it as local emission tick interval too.
        # This allows multiple set_weights submissions inside one chain epoch.
        weight_tick_interval = tx_period_i if tx_period_i is not None and tx_period_i > 0 else epoch_interval_blocks

        logger.info(
            "set_weights: mev_protection=%s wait_for_finalization=%s fail_retry_cooldown=%.0fs "
            "tx_period=%s block_time=%s epoch_interval=%s tick_interval=%s",
            mev_on,
            wait_fin,
            fail_cooldown_sec,
            tx_period_i if tx_period_i is not None else "default",
            weight_block_time,
            epoch_interval_blocks,
            weight_tick_interval,
        )

        # Main validator loop
        while True:
            try:
                metagraph.sync(subtensor=subtensor)
                current_block = subtensor.get_current_block()
                interval = weight_tick_interval

                # Heartbeat: update the last heartbeat timestamp
                last_heartbeat[0] = time.time()

                if last_weight_tick_block is None:
                    last_weight_tick_block = current_block - interval

                blocks_since_tick = current_block - last_weight_tick_block
                tick_due = blocks_since_tick >= interval

                if not tick_due:
                    time.sleep(12)
                    continue

                # Per-model winners: split emission evenly across supported models.
                winner_weights: dict[int, float] = {}
                winners_by_model: dict[str, int] = {}
                try:
                    with connect() as conn:
                        model_count = max(1, len(supported_models))
                        per_model_weight = 1.0 / float(model_count)
                        for model_name in supported_models:
                            winner_uid, winner_hotkey = _pick_winner_uid_from_completed_for_model(
                                conn,
                                metagraph,
                                asic_model=str(model_name),
                                tasks_creator_version=TASK_CREATOR_VERSION,
                                tasks_schema_version=DB_SCHEMA_VERSION,
                            )
                            if winner_uid is None:
                                continue
                            if winner_uid < 0 or winner_uid >= int(metagraph.n):
                                continue
                            if not _hotkey_matches_uid(metagraph, winner_uid, winner_hotkey):
                                continue
                            winner_weights[winner_uid] = float(winner_weights.get(winner_uid, 0.0)) + per_model_weight
                            winners_by_model[str(model_name)] = int(winner_uid)

                        if not winner_weights:
                            stats = conn.execute(
                                """
                                SELECT state, COUNT(*) AS n
                                FROM publications
                                GROUP BY state
                                """
                            ).fetchall()
                            logger.info(
                                "weights: no eligible winner (no completed row with miner_hotkey matching chain) — "
                                "counts by state: %s",
                                [(s.get("state"), s.get("n")) for s in stats],
                            )
                except Exception as e:
                    logger.error("Failed to pick winner from DB: %s", e)

                if not winner_weights:
                    logger.info(
                        "weights: no winner; skipping set_weights, advancing tick"
                    )
                    last_weight_tick_block = current_block
                    time.sleep(12)
                    continue

                now = time.time()
                if (
                    fail_cooldown_sec > 0
                    and last_weight_fail_time > 0.0
                    and (now - last_weight_fail_time) < fail_cooldown_sec
                ):
                    logger.info(
                        "weights: tick due but last set_weights failed %.0fs ago; cooldown %.0fs — advancing tick",
                        now - last_weight_fail_time,
                        fail_cooldown_sec,
                    )
                    last_weight_tick_block = current_block
                    time.sleep(12)
                    continue

                logger.info(
                    "weights: emit_incentive_weights(netuid=%s, winners=%s, weights=%s, mev_protection=%s, period=%s)",
                    netuid,
                    winners_by_model,
                    winner_weights,
                    mev_on,
                    tx_period_i if tx_period_i is not None else "sdk_default",
                )

                resp = emit_incentive_weights(
                    subtensor=subtensor,
                    wallet=wallet,
                    netuid=netuid,
                    winner_weights=winner_weights,
                    mev_protection=mev_on,
                    wait_for_finalization=wait_fin,
                    block_time=weight_block_time,
                    period_blocks=tx_period_i,
                )
                last_weight_tick_block = current_block

                if _extrinsic_succeeded(resp):
                    last_weight_fail_time = 0.0
                    logger.info(
                        "set_weights OK — winner_weights=%s validator_uid=%s detail=%s",
                        winner_weights,
                        my_uid,
                        _extrinsic_detail(resp),
                    )
                else:
                    last_weight_fail_time = time.time()
                    logger.warning(
                        "set_weights FAILED — success=%s detail=%s (next tick in %s blocks; optional cooldown %ss)",
                        getattr(resp, "success", None),
                        _extrinsic_detail(resp),
                        interval,
                        fail_cooldown_sec,
                    )

                time.sleep(12)

            except KeyboardInterrupt:
                logger.info("Validator stopped by user")
                break
            except Exception as e:
                logger.error(f"Error in validator loop: {e}")
                time.sleep(12)
    finally:
        stop_event.set()
        heartbeat_thread.join(timeout=2)
        if pub_expiry_thread is not None:
            pub_expiry_thread.join(timeout=2)

if __name__ == "__main__":
    main()
