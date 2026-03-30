# Building a TensorClock miner

This document describes the **logic and contract** between a miner and the validator: what the validator expects over HTTP, which classes and methods you implement in Python, and how to **run a validator locally** and evaluate your optimization strategy.

For **running** a packaged reference miner, see [run_miner.md](run_miner.md). For **deploying** the validator, see [run_validator.md](run_validator.md).

---

## 1. The miner’s role on the subnet

The validator simulates **virtual ASICs** (devices with hidden parameters stored in the database). The miner never sees the chip internals: it only receives a **task description** (ASIC model, ambient temperature level, optimization target, query budget) and, on each step, sends a **triple of parameters**: frequency, voltage, and fan speed.

The validator feeds those parameters into **ASICPhysicsSimulator**, computes temperature, hashrate, and profit in USD per day (via hashprice and the device’s electricity price), and returns **feedback** to the miner. Competition outcomes are tied to **net profit** across tasks within a **publication**.

**Important:** “Writing a miner” in this codebase means implementing a subclass of **`MinerModel`** and (usually) a small **entrypoint script** that constructs `ValidatorClient`, `MinerRunner`, and a loop over validator endpoints—following the pattern in `miner_references/miner_s19.py`.

---

## 2. What the validator expects (HTTP protocol)

The validator exposes a JSON HTTP API. Everything the miner **sends** is documented below: transport rules, headers, and the exact JSON object shape for each endpoint.

Pydantic source of truth: `validator/validator_api.py` (`ClaimRequest`, `SubmitRequest`, `DecisionRequest`, …).

### 2.1. Transport and JSON encoding

| Item | Requirement |
|------|----------------|
| **Method** | `GET` only for `/health`. All task operations use **`POST`**. |
| **URL** | `{validator_base_url}{path}` — e.g. `http://127.0.0.1:8090/task`. No trailing slash on `path` (use `/task`, not `/task/`). |
| **`Content-Type`** | For every `POST`, send **`application/json`**. |
| **Charset** | Body is UTF-8 encoded JSON **object** (a single top-level `{ ... }`). |
| **Canonical body for Epistula** | When signing is on, the **exact byte sequence** signed must match what the server reads. The reference client serializes with: `json.dumps(obj, sort_keys=True, separators=(",", ":"))` then UTF-8 encode. **Do not** add spaces after `:` or `,` if you want to match this canonical form. |

If you build JSON manually, ensure numbers are JSON numbers (not strings) and keys match the names below exactly.

### 2.2. Epistula signing (enabled by default)

Validator environment variable: `EPISTULA_REQUIRED` (default `true`). When enabled, every `POST` to `/task`, `/task/submit`, and `/task/decision` must add these **HTTP headers** (in addition to `Content-Type`):

| Header | Value |
|--------|--------|
| `X-Epistula-Timestamp` | String of nanoseconds since epoch (nonce), e.g. `"1730000000000000000"`. |
| `X-Epistula-Signature` | Hex-encoded signature of the UTF-8 message `"{timestamp}.{sha256_hex(raw_body)}"` by the hotkey. |
| `X-Epistula-Hotkey` | SS58 address of the signing hotkey (must match the key used for the signature). |

The signed payload is the **raw POST body bytes** (the JSON string as sent on the wire).

Implementation: `utils/epistula.py` (`sign_epistula_request_body`). The **`ValidatorClient`** in `miner/miner_template.py` signs automatically when a **`Wallet`** is passed.

**Rule for `POST /task`:** the JSON field **`miner_hotkey`** must equal **`X-Epistula-Hotkey`**; otherwise **403**.

**Local debugging:** `EPISTULA_REQUIRED=false` on the validator disables verification (trusted networks only). You still send normal JSON bodies.

### 2.3. Endpoints overview

| Method | Path | Body |
|--------|------|------|
| `GET` | `/health` | **None** (no request body). |
| `POST` | `/task` | JSON object — **ClaimRequest** (§2.4). |
| `POST` | `/task/submit` | JSON object — **SubmitRequest** (§2.5). |
| `POST` | `/task/decision` | JSON object — **DecisionRequest** (§2.6). |

---

### 2.4. `POST /task` — claim (ClaimRequest)

**Purpose:** Start a **new publication** or **continue** the same publication and receive the next **task** (assignment).

**JSON object — fields sent by the miner:**

| Field | JSON type | Required | Constraints / notes |
|-------|-----------|----------|----------------------|
| `miner_uid` | number (integer) | **yes** | Integer **≥ 0**. |
| `miner_hotkey` | string | **yes** | Non-empty; SS58. Must match Epistula hotkey when signing is enabled. |
| `asic_model` | string | **yes** | Non-empty after trim; must be a model known to the validator (see §5). |
| `target` | string | **yes** | After normalization: one of **`efficiency`**, **`hashrate`**, **`balanced`** (case-insensitive in API). |
| `publication_id` | string or omitted / `null` | no | If **omitted**, **`null`**, or **only whitespace**, the validator starts a **new** publication. If set to an existing active publication ID, that publication is continued. |
| `model_description_json` | object or omitted / `null` | no | Arbitrary JSON object (metadata about your model). Stored when a **new** publication is created; ignored for continuation semantics if not applicable. |

**Minimal example — new publication (field order in wire format should follow your canonical serializer; keys shown alphabetically as in reference):**

```json
{
  "asic_model": "Antminer S19",
  "miner_hotkey": "5FHneW46xGXgs5mUiveU4sbTyGBzmstUspZC92UhjJM694ty",
  "miner_uid": 7,
  "target": "efficiency"
}
```

**Example — continue existing publication and attach model metadata on first create:**

```json
{
  "asic_model": "Antminer S19",
  "miner_hotkey": "5FHneW46xGXgs5mUiveU4sbTyGBzmstUspZC92UhjJM694ty",
  "miner_uid": 7,
  "model_description_json": {
    "name": "my-miner",
    "version": "0.2.0"
  },
  "publication_id": "pub_a1b2c3d4e5f6789012345678abcdef",
  "target": "efficiency"
}
```

The reference `ValidatorClient.claim_task` omits `publication_id` when starting fresh and includes it only when continuing—either shape is valid JSON as long as it matches the table above.

---

### 2.5. `POST /task/submit` — submit optimization parameters (SubmitRequest)

**Purpose:** Send one **simulation query** for the active assignment: frequency, voltage, fan speed.

**JSON object — fields sent by the miner:**

| Field | JSON type | Required | Constraints / notes |
|-------|-----------|----------|----------------------|
| `publication_id` | string | **yes** | Must match the publication from claim responses. |
| `task_id` | string | **yes** | Must match the current task’s `task_id` from the claim response. |
| `frequency` | number | **yes** | JSON number (integer or float). Interpreted by the simulator (MHz or internal units as defined in `ASICPhysicsSimulator`). |
| `voltage` | number | **yes** | JSON number. |
| `fan_speed` | number | **yes** | JSON number; typical range 0–100 (validator also enforces device limits in simulation). |

**Example:**

```json
{
  "fan_speed": 90.0,
  "frequency": 600.0,
  "publication_id": "pub_a1b2c3d4e5f6789012345678abcdef",
  "task_id": "task_001",
  "voltage": 13.0
}
```

Canonical serialization will reorder keys alphabetically (`fan_speed` before `frequency` in the example above).

---

### 2.6. `POST /task/decision` — miner decision (DecisionRequest)

**Purpose:** Tell the validator to **finalize** the current assignment without using remaining query budget, or (theoretically) **continue** via this endpoint — in practice, use **`finalize`** only; extra optimization steps use **`/task/submit`**.

**JSON object — fields sent by the miner:**

| Field | JSON type | Required | Constraints / notes |
|-------|-----------|----------|----------------------|
| `publication_id` | string | **yes** | Active publication id. |
| `task_id` | string | **yes** | Task to act on. |
| `action` | string | **yes** | Exactly **`"continue"`** or **`"finalize"`** (regex `^(continue|finalize)$` in API). **`continue`** here currently returns **400** — use **`/task/submit`** to continue optimization. Use **`finalize`** to close the assignment early after at least one submit. |

**Example (typical):**

```json
{
  "action": "finalize",
  "publication_id": "pub_a1b2c3d4e5f6789012345678abcdef",
  "task_id": "task_001"
}
```

You cannot finalize before the first successful submit on that assignment (`queries_used` must be &gt; 0) — otherwise **400**.

---

## 3. Response bodies (validator → miner)

Outbound JSON is defined by `ClaimResponse`, `TaskPayload`, `SubmitResponse` in `validator/validator_api.py`. Summaries below; **§2** documents everything the miner **sends**.

### 3.1. `POST /task` — claim responses

**200 — `ClaimResponse`:**

| Field | Type | Meaning |
|-------|------|---------|
| `publication_id` | string | Publication to reuse on subsequent claim/submit/decision calls. |
| `publication_deadline_at` | string | ISO-8601 deadline for the publication. |
| `task` | object | **`TaskPayload`** — see below. |
| `assignment_state` | string | e.g. active assignment state. |
| `queries_used` | int | Queries already consumed on this assignment row (often `0` on first assign). |

**`task` (`TaskPayload`) — passed into your `predict()` as `TaskInfo`:**

| Field | Meaning |
|-------|---------|
| `task_id` | Task identifier for submits. |
| `device_id` | Virtual device id (opaque). |
| `asic_model` | Model string. |
| `ambient_level` | Ambient level name (simulator enum as string). |
| `target` | Task’s optimization target. |
| `query_budget` | Max submits for this assignment. |
| `expires_at` | ISO timestamp. |

**Common errors:** **404** (no tasks), **410** (deadline), **422** (bad `target` / `asic_model`), **403** (hotkey / publication mismatch).

### 3.2. `POST /task/submit` — submit responses

**200 — `SubmitResponse`** (fields most relevant to miners):

| Field | Meaning |
|-------|---------|
| `state` | Assignment state after the step (`active`, `completed`, `failed`, …). |
| `queries_used` / `remaining_queries` | Usage vs budget for this assignment. |
| `can_continue` | Whether another `/task/submit` is allowed on this assignment. |
| `overheated` | Thermal failure → assignment **failed**, zero profit. |
| `gross_revenue_usd_day`, `electricity_cost_usd_day`, `net_profit_usd_day` | Economics when simulation is valid. |
| `warning` | Simulator message. |
| `publication_completed` | **true** when the whole publication is done. |

Invalid physics / limits can yield **failed** assignment (`outcome.valid` false on server). **503** if hashprice unavailable.

### 3.3. `POST /task/decision`

**200** returns a **`SubmitResponse`-shaped** body (task finalized; many fields may be defaulted). **`continue`** action is rejected with **400** — continue optimization with **`/task/submit`** instead.

---

## 4. Publications and “effectiveness” in code terms

- A **publication** groups multiple **assignments** (tasks). The expected number of tasks is fixed at publication creation by validator constants (see `EXPECTED_TASKS_PER_PUBLICATION` in `validator/task_manager.py`: device count × ambient levels).
- The validator aggregates results and updates scoring (see `apply_scores_after_*` in the validator code).
- **Practical metrics for comparing miner strategies** in a local run:
  - average and total **`net_profit_usd_day`** across steps;
  - share of **failed** / **overheated**;
  - successful publication completion (`publication_completed` in submit/decision responses).

Exact on-chain ranking depends on subnet logic and the database; for development, comparing these metrics under the same validator seed/environment is enough.

---

## 5. ASIC models and the `target` field

- The **`asic_model`** string must match **exactly** a name loaded by `VirtualDeviceGenerator` on the validator (built-in specs plus any extensions). Reference scripts use names such as `Antminer S19`, `Antminer S19 Pro`, `Antminer S19j Pro`.
- An unknown name yields **422** with the list of available models.

The **`target`** on claim must be one of the validator-supported values. Currently there is only efficiency parameter available.

---

## 6. What to implement in Python: `MinerModel` and orchestration

Start from `miner/miner_template.py`.

### 6.1. `MinerModel` (required)

```text
class MinerModel(ABC):
    @abstractmethod
    def predict(self, task: TaskInfo) -> OptimizationParams:
        ...

    def should_continue(self, task: TaskInfo, feedback: TaskSubmitFeedback) -> bool:
        return False  # override for multi-step optimization on one task
```

- **`predict`** — the only **required** abstract method. Input: **`TaskInfo`** (same fields as `TaskPayload` from the API). Output: **`OptimizationParams`** with `frequency`, `voltage`, `fan_speed`.

- **`should_continue`** — optional. If after `submit` the validator returns `can_continue=True` and you return **True**, the runner calls **`predict`** again for the **same** `TaskInfo` to issue another submit. If **`False`** (default), when `can_continue=True` the runner calls **`decide_task(..., action="finalize")`** to close the assignment and move to the next claim.

Local range checks before sending: **`validate_optimization_params`** — frequency and voltage &gt; 0, `fan_speed` in **[0, 100]**. This does **not** replace the validator-side simulator.

### 6.2. Building blocks you can reuse

- **`ValidatorClient`** — HTTP client with Epistula (`health`, `claim_task`, `submit`, `decide_task`).
- **`MinerRunner.run_publication`** — full loop: repeated **claims** with the same `publication_id` until the pool is exhausted or the publication completes; inside each task, a **submit** loop and optional **finalize**.

Copy the structure of **`miner_references/miner_s19.py`**: `--config` parsing, wallet loading, `miner_uid` resolution, validator URL (`--validator-url` or discovery), model construction, and `MinerRunner`.

---

## 7. Local testing: validator + your miner

Typical workflow on **one machine** (or one LAN) to debug strategy without relying on public on-chain discovery.

### 7.1. Bring up PostgreSQL and the validator

1. Install PostgreSQL and create a database (as in [run_validator.md](run_validator.md)).
2. Set `validator.database_url` in `configs/validator_config.toml`, and `validator.api_host` / `validator.api_port` as needed.
3. Initialize the schema: `python utils/init_db.py`.
4. Start the validator: `python -m validator.validator` from the repo root after `pip install -e .`.

Confirm **`GET http://<host>:<port>/health`** returns OK.

### 7.2. Point the miner at the local API

In `configs/miner_config.toml` or via CLI:

- **`miner.validator_url`** = `http://127.0.0.1:<api_port>` (the HTTP API port from the validator config: `validator.api_port` / `validator_api_url`).
- **`miner.network`** / **`miner.netuid`** — must match your wallet if you resolve UID via subtensor; for a local-only run with a wallet, use the network where the hotkey is registered, or use **`--no-wallet`** and set **`miner.miner_hotkey`** manually (as in the reference).

### 7.3. Epistula during local debugging

- Prefer keeping signing enabled and using a real **`Wallet`**—closest to production.
- To simplify debugging, on the validator process: **`export EPISTULA_REQUIRED=false`**. Do **not** do this on public endpoints.

### 7.4. Running and measuring effectiveness

1. Run your miner script with `--validator-url http://127.0.0.1:...`.
2. Watch miner logs for lines with `net_usd_day`, `state`, `publication_completed`.
3. Compare against the reference: run `miner_references/miner_s19.py` with the same config and validator—compare publication success rate and average profit.

**Quick connectivity check:** the **`--smoke`** flag in reference scripts (one claim, minimal submit). Consider adding a similar mode to your own script.

### 7.5. Common local test issues

| Symptom | What to check |
|--------|----------------|
| 401 on POST | Epistula: signature, timestamp window, `miner_hotkey` matches wallet. |
| 422 on claim | `target` and `asic_model` are allowed values. |
| 503 on submit | Hashprice / cache on validator—see validator logs. |
| No tasks (404) | Task pool not generated; first claim for a model calls `ensure_task_pool_for_model`—validator needs a clean DB init and successful API path. |

---

## 8. Files to read in the repository

| File | Why |
|------|-----|
| `miner/miner_template.py` | `MinerModel`, `MinerRunner`, `ValidatorClient`, types `TaskInfo` / `OptimizationParams` / `TaskSubmitFeedback`. |
| `validator/validator_api.py` | Exact HTTP contract and post-submit simulation logic. |
| `utils/epistula.py` | Request signing. |
| `miner_references/miner_s19.py` (and `_pro` / `_s19j_pro`) | Full runnable miner example. |
| `simulation/asic_physics_simulator.py` | How frequency/voltage/fan map to physics (intuition only; you do not ship the simulator with the miner). |

---

## 9. Checklist for a new miner

1. Subclass **`MinerModel`** with a real **`predict`** implementation (and **`should_continue`** if you multi-step within a task).
2. Entrypoint script with **`ValidatorClient(wallet=...)`** and **`MinerRunner.run_publication`** (or an equivalent protocol-compliant loop).
3. Consistent **`asic_model`**, **`target`**, **`miner_uid`/`miner_hotkey`** with the validator and chain expectations.
4. Local validation: validator + `--validator-url` + metric comparison against the reference.
