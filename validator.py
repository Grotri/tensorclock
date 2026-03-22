import os
import time
import click
import logging
import bittensor as bt
from bittensor_wallet import Wallet
import threading
import sys
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Optional

import uvicorn

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

HEARTBEAT_TIMEOUT = 600  # seconds

# Local imports
from init_db import connect, init_db
from publication_expiry import publication_expiry_sweep_loop
from task_manager import generate_miner_task_bundle
from validator_api import app, init_validator_api
from version import DB_SCHEMA_VERSION, TASK_CREATOR_VERSION


def _extrinsic_succeeded(resp: Any) -> bool:
    """Bittensor SDK v10+ returns ExtrinsicResponse; older code used bool or (bool, str)."""
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


def _extrinsic_detail(resp: Any) -> str:
    if resp is None:
        return "None"
    for name in ("message", "error_message", "err_msg", "reason"):
        v = getattr(resp, name, None)
        if v not in (None, ""):
            return str(v)
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
    winner_uid: int,
    mev_protection: bool,
    wait_for_finalization: bool,
    block_time: float,
    period_blocks: Optional[int],
) -> Any:
    """
    Push normalized weights (single winner → [1.0]) via Subtensor.set_weights.

    The SDK picks commit-timelocked vs direct mechanism weights from commit_reveal_enabled(netuid).
    Shape matches subnet-template style: wallet, netuid, uids, weights, wait_for_inclusion, optional period.

    Reference: https://github.com/opentensor/subnet-template/blob/main/validator.py
    """
    kwargs: dict[str, Any] = {
        "wallet": wallet,
        "netuid": netuid,
        "uids": [winner_uid],
        "weights": [1.0],
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

@click.command()
@click.option(
    "--network",
    default=lambda: os.getenv("NETWORK", "finney"),
    help="Network to connect to (finney, test, local)",
)
@click.option(
    "--netuid",
    type=int,
    default=lambda: int(os.getenv("NETUID", "1")),
    help="Subnet netuid",
)
@click.option(
    "--coldkey",
    default=lambda: os.getenv("WALLET_NAME", "default"),
    help="Wallet name",
)
@click.option(
    "--hotkey",
    default=lambda: os.getenv("HOTKEY_NAME", "default"),
    help="Hotkey name",
)
@click.option(
    "--log-level",
    type=click.Choice(["DEBUG", "INFO", "WARNING", "ERROR"], case_sensitive=False),
    default=lambda: os.getenv("LOG_LEVEL", "INFO"),
    help="Logging level",
)
def main(network: str, netuid: int, coldkey: str, hotkey: str, log_level: str):
    """Run the Chi subnet validator."""
    # Set log level
    logging.getLogger().setLevel(getattr(logging, log_level.upper()))
    logger.info(f"Starting validator on network={network}, netuid={netuid}")

    # Heartbeat setup
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
        weight_interval = _weight_tick_interval_blocks(tempo)

        init_db()
        
        generate_miner_task_bundle(
            asic_model="Antminer S19",
            devices_count=5,
            query_budget=10,
            target="efficiency",
        )

        from virtual_device_generator import VirtualDeviceGenerator

        db_url = os.getenv("DATABASE_URL", "").strip()
        if not db_url:
            raise RuntimeError("DATABASE_URL is required to run the validator with PostgreSQL.")

        generator = VirtualDeviceGenerator()
        generator.load_builtin_specifications()

        sim_workers = int(os.getenv("VALIDATOR_SIM_WORKERS", "4"))
        executor = ThreadPoolExecutor(max_workers=sim_workers)
        init_validator_api(db_url=db_url, generator=generator, executor=executor)

        pub_expiry_thread = threading.Thread(
            target=publication_expiry_sweep_loop,
            args=(db_url, stop_event),
            daemon=True,
            name="publication-expiry-sweep",
        )
        pub_expiry_thread.start()
        logger.info(
            "Publication deadline sweep started (interval from PUBLICATION_EXPIRE_SWEEP_INTERVAL_SEC, "
            "default 30s; batch cap PUBLICATION_EXPIRE_SWEEP_BATCH_LIMIT)"
        )

        api_port = int(os.getenv("VALIDATOR_API_PORT", "8090"))
        server = uvicorn.Server(uvicorn.Config(app, host="0.0.0.0", port=api_port, log_level="info"))
        threading.Thread(target=server.run, daemon=True).start()
        logger.info(f"Validator API started on :{api_port}")

        # Hotkey-signed extrinsics cannot use MEV Shield (SDK warns + tx fails). Default off.
        mev_on = _env_bool("VALIDATOR_MEV_PROTECTION", default=False)
        if mev_on:
            logger.warning(
                "VALIDATOR_MEV_PROTECTION=true: MEV Shield often breaks hotkey-signed set_weights; "
                "prefer false unless you use a supported signing path."
            )
        wait_fin = _env_bool("VALIDATOR_WAIT_FOR_FINALIZATION", default=True)
        # After a failed set_weights, chain LastUpdate does not advance, so bslu stays high and we would
        # retry every loop (~12s) and spam the node. Cool down before retrying (time-based; works on fast blocks).
        fail_cooldown_sec = float(os.getenv("VALIDATOR_WEIGHT_FAIL_COOLDOWN_SEC", "120"))
        tx_period = os.getenv("VALIDATOR_TX_PERIOD_BLOCKS", "").strip()
        if tx_period.isdigit() and int(tx_period) > 0:
            tx_period_i = int(tx_period)
        else:
            tx_period_i = None

        # Commit-reveal only: SDK maps delay → Drand round using this (default 12s = mainnet).
        # Local turbo chains often have sub-second blocks; if this stays 12.0, the target Drand round is wrong vs chain semantics.
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

        logger.info(
            "set_weights: mev_protection=%s wait_for_finalization=%s fail_retry_cooldown=%.0fs tx_period=%s block_time=%s",
            mev_on,
            wait_fin,
            fail_cooldown_sec,
            tx_period_i if tx_period_i is not None else "default",
            weight_block_time,
        )

        # Main validator loop
        while True:
            try:
                metagraph.sync(subtensor=subtensor)
                current_block = subtensor.get_current_block()
                interval = weight_interval

                # Heartbeat: update the last heartbeat timestamp
                last_heartbeat[0] = time.time()

                if last_weight_tick_block is None:
                    last_weight_tick_block = current_block - interval

                blocks_since_tick = current_block - last_weight_tick_block
                tick_due = blocks_since_tick >= interval

                if not tick_due:
                    time.sleep(12)
                    continue

                # Winner-takes-all based on completed publications.
                winner_uid: Optional[int] = None
                try:
                    with connect() as conn:
                        row = conn.execute(
                            """
                            SELECT miner_uid
                            FROM publications
                            WHERE state='completed'
                              AND tasks_creator_version = ?
                              AND tasks_schema_version = ?
                            ORDER BY avg_net_profit DESC NULLS LAST, completed_at ASC
                            LIMIT 1
                            """,
                            (TASK_CREATOR_VERSION, DB_SCHEMA_VERSION),
                        ).fetchone()
                        if row is not None and row.get("miner_uid") is not None:
                            winner_uid = int(row["miner_uid"])
                        if winner_uid is None:
                            row2 = conn.execute(
                                """
                                SELECT miner_uid, tasks_creator_version, tasks_schema_version, completed_at
                                FROM publications
                                WHERE state='completed'
                                ORDER BY completed_at DESC NULLS LAST, avg_net_profit DESC NULLS LAST
                                LIMIT 1
                                """
                            ).fetchone()
                            if row2 is not None and row2.get("miner_uid") is not None:
                                winner_uid = int(row2["miner_uid"])
                                logger.info(
                                    "weights: using latest completed publication (version fallback): "
                                    "miner_uid=%s tasks_creator_version=%s tasks_schema_version=%s completed_at=%s",
                                    winner_uid,
                                    row2.get("tasks_creator_version"),
                                    row2.get("tasks_schema_version"),
                                    row2.get("completed_at"),
                                )
                        if winner_uid is None:
                            stats = conn.execute(
                                """
                                SELECT state, COUNT(*) AS n
                                FROM publications
                                GROUP BY state
                                """
                            ).fetchall()
                            logger.info(
                                "weights: no completed publication for weights — counts by state: %s",
                                [(s.get("state"), s.get("n")) for s in stats],
                            )
                except Exception as e:
                    logger.error("Failed to pick winner from DB: %s", e)

                if winner_uid is None:
                    logger.info(
                        "weights: no winner (no state=completed row); skipping set_weights, advancing tick"
                    )
                    last_weight_tick_block = current_block
                    time.sleep(12)
                    continue

                if winner_uid < 0 or winner_uid >= int(metagraph.n):
                    logger.error(
                        "weights: winner miner_uid=%s out of range for metagraph n=%s; skipping, advancing tick",
                        winner_uid,
                        metagraph.n,
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
                    "weights: emit_incentive_weights(netuid=%s, winner_uid=%s, mev_protection=%s, period=%s)",
                    netuid,
                    winner_uid,
                    mev_on,
                    tx_period_i if tx_period_i is not None else "sdk_default",
                )

                resp = emit_incentive_weights(
                    subtensor=subtensor,
                    wallet=wallet,
                    netuid=netuid,
                    winner_uid=winner_uid,
                    mev_protection=mev_on,
                    wait_for_finalization=wait_fin,
                    block_time=weight_block_time,
                    period_blocks=tx_period_i,
                )
                last_weight_tick_block = current_block

                if _extrinsic_succeeded(resp):
                    last_weight_fail_time = 0.0
                    logger.info(
                        "set_weights OK — miner_uid=%s validator_uid=%s detail=%s",
                        winner_uid,
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

                # Sleep for ~1 block
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
