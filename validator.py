"""
Simplest possible Bittensor subnet validator. Simply burns everything to UID 0.
"""

import os
import time
import click
import logging
import bittensor as bt
from bittensor_wallet import Wallet
import threading
import sys

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

HEARTBEAT_TIMEOUT = 600  # seconds

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

    try:
        # Initialize wallet, subtensor, and metagraph
        wallet = Wallet(name=coldkey, hotkey=hotkey)
        subtensor = bt.Subtensor(network=network)
        metagraph = bt.Metagraph(netuid=netuid, network=network)

        # Sync metagraph
        metagraph.sync(subtensor=subtensor)
        logger.info(f"Metagraph synced: {metagraph.n} neurons at block {metagraph.block}")

        # Get our UID
        my_hotkey = wallet.hotkey.ss58_address
        if my_hotkey not in metagraph.hotkeys:
            logger.error(f"Hotkey {my_hotkey} not registered on netuid {netuid}")
            stop_event.set()
            return
        my_uid = metagraph.hotkeys.index(my_hotkey)
        logger.info(f"Validator UID: {my_uid}")

        # Get tempo for this subnet
        tempo = subtensor.get_subnet_hyperparameters(netuid).tempo
        logger.info(f"Subnet tempo: {tempo} blocks")

        last_weight_block = 0

        # Main validator loop
        while True:
            try:
                # Sync metagraph
                metagraph.sync(subtensor=subtensor)
                current_block = subtensor.get_current_block()

                # Heartbeat: update the last heartbeat timestamp
                last_heartbeat[0] = time.time()

                # Check if we should set weights (once per tempo)
                blocks_since_last = current_block - last_weight_block
                if blocks_since_last >= tempo:
                    logger.info(f"Block {current_block}: Setting weights (tempo={tempo})")

                    # Set 100% weight on UID 0
                    uids = [0]
                    weights = [1.0]

                    # Set weights on chain
                    success = subtensor.set_weights(
                        wallet=wallet,
                        netuid=netuid,
                        uids=uids,
                        weights=weights,
                        wait_for_inclusion=True,
                        wait_for_finalization=False,
                    )

                    if success:
                        logger.info(f"Successfully set weights for {len(uids)} neurons")
                        last_weight_block = current_block
                    else:
                        logger.warning("Failed to set weights")
                else:
                    logger.debug(
                        f"Block {current_block}: Waiting for tempo "
                        f"({blocks_since_last}/{tempo} blocks)"
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

if __name__ == "__main__":
    main()
