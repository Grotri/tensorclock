# TensorClock miner deployment (conda, no containers)

## What you need

- **Linux** host with network access to a Bittensor subtensor endpoint (Finney, testnet, or local) and to validator HTTP endpoints (discovered on-chain or set explicitly).
- **Conda** (Miniconda, Miniforge, or Anaconda).
- A **registered hotkey** on the target subnet (`netuid`) and matching **coldkey/hotkey** wallet files (Bittensor wallet layout under `~/.bittensor/wallets/` by default).
- Python **3.12** (see `requirements.txt`).

## 1. Create the conda environment

From a shell:

```bash
conda create -n tensorclock-miner python=3.12 -y
conda activate tensorclock-miner
```

## 2. Install Python dependencies

Clone or copy the repository and install requirements **inside the activated environment**:

```bash
cd /path/to/tensorclock
pip install -r requirements.txt
pip install -e .
```

## 3. Configure the miner

Edit `configs/miner_config.toml` (or maintain a copy outside the repo and pass `--config` to it).

**Common settings**

- **`miner.network`** — e.g. `finney` or `test` (must match how validators on that subnet are registered).
- **`miner.netuid`** — subnet UID.
- **`miner.wallet_name` / `miner.hotkey_name`** — defaults `default`; must match your wallet.
- **`miner.blacklist_validator_min_stake`** — Validators with stake at or below this value are skipped during discovery (default `-1.0` = no stake floor).
- **`miner.blacklist_force_validator_permit`** — If `true`, only neurons with `validator_permit` are considered (default `true`).

CLI flags override TOML when passed (e.g. `--network`, `--netuid`, `--validator-url`, `--wallet-name`, `--hotkey-name`, `--smoke`).

See `configs/miner_config.toml` for defaults.

## 4. Run the miner (reference implementation)

The repository ships **runnable reference miners** under `miner_references/`. Pick the script whose default **`--asic-model`** matches the hardware you simulate (you can still override `--asic-model` on the command line).

| Script | Default ASIC model string |
|--------|---------------------------|
| `miner_references/miner_s19.py` | Antminer S19 |
| `miner_references/miner_s19_pro.py` | Antminer S19 Pro |
| `miner_references/miner_s19j_pro.py` | Antminer S19j Pro |

Run from the **repository root** (after `pip install -e .`):

```bash
cd /path/to/tensorclock
python miner_references/miner_s19.py
```

Use another reference file the same way, for example:

```bash
python miner_references/miner_s19_pro.py --config configs/miner_config.toml
```

Without the editable install, use `export PYTHONPATH="$(pwd)"` before the command.

**Smoke test** (single validator, minimal claim/submit path; set `miner.smoke = true` in TOML or pass `--smoke`)

```bash
python miner_references/miner_s19.py --smoke
```

## 5. Troubleshooting

| Symptom | Check |
|--------|--------|
| `No live validator endpoints discovered` | Validators must publish an HTTP base URL in their commitment; URLs must be reachable and return HTTP 200 on `/health`. |
| `Validator /health is not OK` | Firewall, wrong port, or validator not running; confirm `validator.api_host` / `validator.api_port` on the validator side. |
| `Miner hotkey is not registered on netuid` | Register the hotkey on the subnet or fix `--netuid` / wallet names. |
| `No module named 'utils'` | Run `pip install -e .` from the repo root (see step 2), or `export PYTHONPATH="$(pwd)"`. |

For dependency and Python version expectations, see `requirements.txt` at the repository root.
