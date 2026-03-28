# TensorClock validator deployment (conda, no containers)

This guide describes how to run the TensorClock **validator** on a Linux host **without Docker**, using a **conda** Python environment. The validator connects to **Bittensor** (subtensor), serves a **FastAPI** HTTP API for miners, and uses **PostgreSQL** for state.

## What you need

- **Linux** host with network access to a Bittensor subtensor endpoint (Finney, testnet, or local).
- **PostgreSQL** 14+ (or compatible) reachable from the validator host. The validator uses a single connection URL (`DATABASE_URL` / `validator.database_url` in config).
- **Conda** (Miniconda, Miniforge, or Anaconda).
- A **registered hotkey** on the target subnet (`netuid`) and matching **coldkey/hotkey** wallet files (Bittensor wallet layout under `~/.bittensor/wallets/` by default).
- Python **3.12**

## 1. Install PostgreSQL

Install and configure PostgreSQL using your distribution’s packages or managed service. Create a database and a user (or use a connection string provided by your provider).

Example connection URL format:

```text
postgresql://USER:PASSWORD@HOST:5432/DATABASE
```

## 2. Create the conda environment

From a shell:

```bash
conda create -n tensorclock-validator python=3.12 -y
conda activate tensorclock-validator
```

## 3. Install Python dependencies

Clone or copy the repository and install requirements **inside the activated environment**:

```bash
cd /path/to/tensorclock
pip install -r requirements.txt
pip install -e .
```

The second line installs the repository as an **editable** package so imports such as `utils` resolve from any working directory (you do not need to set `PYTHONPATH` manually).

## 4. Configure the validator

Edit `configs/validator_config.toml` (or maintain a copy outside the repo and point `--config` at it).

**Required**

- **`validator.database_url`** — PostgreSQL URL, same format as `DATABASE_URL` (see above). If empty, the process exits with an error.
- **`validator.validator_api_url`** — Full bind URL including port (e.g. `http://127.0.0.1:8091`). If set, it must include an explicit port.

**Commonly adjusted**

- **`validator.network`** — e.g. `finney` or `test`.
- **`validator.netuid`** — subnet UID.
- **`validator.wallet_name` / `validator.hotkey_name`** — defaults `default`; must match your wallet.
- **`validator.api_port`** — HTTP API port (default `8090`). The API listens on **`0.0.0.0`** on this port.

CLI flags override TOML when passed (e.g. `--network`, `--netuid`, `--coldkey`, `--hotkey`, `--api-port`).

See `configs/validator_config.toml` for additional settings.

## 5. Initialize the database schema

Set `DATABASE_URL` and run the initializer once (or after you intentionally reset the DB):

```bash
cd /path/to/tensorclock
conda activate tensorclock-validator
export DATABASE_URL="postgresql://USER:PASSWORD@HOST:5432/DATABASE"
python utils/init_db.py
```

You can pass `--db` instead of `DATABASE_URL` if you prefer. If you skipped `pip install -e .`, set `export PYTHONPATH="$(pwd)"` before running scripts.

The validator also calls `init_db()` on startup, but running the script first confirms connectivity and permissions.

## 6. Run the validator

Run from the **repository root** (after `pip install -e .`):

```bash
cd /path/to/tensorclock
conda activate tensorclock-validator
python -m validator.validator --config configs/validator_config.toml
```

Without the editable install, use `export PYTHONPATH="$(pwd)"` before the command.

## 7. Troubleshooting

| Symptom | Check |
|--------|--------|
| `database_url is required` | Set `validator.database_url` in TOML. |
| `DATABASE_URL is not set` (init script) | Export `DATABASE_URL` or pass `--db`. |
| `Hotkey ... is not registered` | Register the hotkey on the subnet or fix `--netuid` / wallet names. |
| API unreachable | Confirm port, firewall. |
| `No module named 'utils'` | Run `pip install -e .` from the repo root (see step 3), or `export PYTHONPATH="$(pwd)"`. |

For dependency and Python version expectations, see `requirements.txt` at the repository root.
