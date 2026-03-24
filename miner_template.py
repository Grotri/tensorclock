"""
TensorClock miner template (model plug-in + validator discovery via Bittensor).

Contract alignment:
  - validator API: see `validator_api.py` (`POST /task`, `POST /task/submit`, `GET /health`)
  - `POST /task` includes `publication_deadline_at` (wall-clock); **410 Gone** if that publication is past deadline
  - optional Epistula signing on POST bodies (see `epistula.py`, `EPISTULA_REQUIRED` on validator)
  - endpoint discovery: per-UID ``subtensor.get_commitment``; see ``_get_commitment_quiet`` (SDK otherwise logs
    ERROR on UIDs with empty/broken commitment metadata — not a TensorClock bug), then **every** live validator
  - optional stake filter: `min_validator_stake` vs `metagraph.S[uid]` (default 0)

This file intentionally has NO DB dependency: miner only talks to validators over HTTP.
"""

from __future__ import annotations

import abc
import json
import logging
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Iterator, List, Optional, Sequence

import bittensor as bt
import requests

from bittensor_wallet import Wallet

from epistula import merge_headers, sign_epistula_request_body

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data shapes (mirror validator_api TaskPayload / submit JSON)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TaskInfo:
    task_id: str
    device_id: str
    asic_model: str
    ambient_level: str
    target: str
    query_budget: int
    expires_at: str


@dataclass(frozen=True)
class OptimizationParams:
    """Parameters sent to ``POST /task/submit`` (names match validator)."""

    frequency: float  # MHz
    voltage: float  # V
    fan_speed: float  # 0..100 %


@dataclass
class PublicationRunResult:
    publication_id: str
    tasks_attempted: int
    last_submit_state: Optional[str]
    publication_completed: bool


class MinerModelError(RuntimeError):
    """Raised when local validation fails before submitting to the validator."""

    def __init__(self, errors: Sequence[str]):
        self.errors = list(errors)
        super().__init__("; ".join(self.errors))


# ---------------------------------------------------------------------------
# Local validation (model output sanity checks)
# ---------------------------------------------------------------------------


def validate_optimization_params(params: OptimizationParams) -> List[str]:
    """
    Return a list of human-readable errors (empty if OK).
    This catches bad outputs before submit; final validity is decided by validator simulation.
    """
    errs: List[str] = []
    if not isinstance(params.frequency, (int, float)):
        errs.append("frequency must be numeric")
    if not isinstance(params.voltage, (int, float)):
        errs.append("voltage must be numeric")
    if not isinstance(params.fan_speed, (int, float)):
        errs.append("fan_speed must be numeric")
    if errs:
        return errs
    if params.frequency <= 0:
        errs.append("frequency must be > 0")
    if params.voltage <= 0:
        errs.append("voltage must be > 0")
    if not (0.0 <= params.fan_speed <= 100.0):
        errs.append("fan_speed must be in [0, 100]")
    return errs


# ---------------------------------------------------------------------------
# Model hook
# ---------------------------------------------------------------------------


class MinerModel(abc.ABC):
    """Override ``predict`` to map a claimed task to optimization parameters."""

    @abc.abstractmethod
    def predict(self, task: TaskInfo) -> OptimizationParams:
        raise NotImplementedError


class UnimplementedMinerModel(MinerModel):
    """Default placeholder — replace with your implementation."""

    def predict(self, task: TaskInfo) -> OptimizationParams:
        raise NotImplementedError(
            "Replace UnimplementedMinerModel with your subclass of MinerModel "
            "and implement predict()."
        )


# ---------------------------------------------------------------------------
# HTTP client
# ---------------------------------------------------------------------------


class ValidatorClient:
    """
    Thin ``requests`` wrapper for the validator API.

    Environment variables are *not* read here — pass ``base_url`` explicitly.
    """

    def __init__(
        self,
        base_url: str,
        *,
        wallet: Optional[Wallet] = None,
        timeout_s: float = 30.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_s = timeout_s
        self._session = requests.Session()
        self._wallet = wallet

    @staticmethod
    def _json_body_bytes(obj: dict[str, Any]) -> bytes:
        # Must match bytes hashed by Epistula on the server.
        return json.dumps(obj, sort_keys=True, separators=(",", ":")).encode("utf-8")

    def _post_signed_json(self, path: str, body: dict[str, Any]) -> requests.Response:
        body_bytes = self._json_body_bytes(body)
        headers: dict[str, str] = {"Content-Type": "application/json"}
        if self._wallet is not None:
            headers = dict(merge_headers(headers, sign_epistula_request_body(self._wallet, body_bytes)))
        return self._session.post(
            f"{self.base_url}{path}",
            data=body_bytes,
            headers=headers,
            timeout=self.timeout_s,
        )

    def health(self) -> bool:
        r = self._session.get(f"{self.base_url}/health", timeout=self.timeout_s)
        return r.status_code == 200

    def claim_task(
        self,
        *,
        miner_uid: int,
        miner_hotkey: str,
        asic_model: str,
        target: str,
        publication_id: Optional[str] = None,
        model_description_json: Optional[dict[str, Any]] = None,
    ) -> requests.Response:
        body: dict[str, Any] = {
            "miner_uid": miner_uid,
            "miner_hotkey": miner_hotkey,
            "asic_model": asic_model,
            "target": target,
        }
        if publication_id is not None:
            body["publication_id"] = publication_id
        if model_description_json is not None:
            body["model_description_json"] = model_description_json
        return self._post_signed_json("/task", body)

    def submit(
        self,
        *,
        publication_id: str,
        task_id: str,
        params: OptimizationParams,
    ) -> requests.Response:
        return self._post_signed_json(
            "/task/submit",
            {
                "publication_id": publication_id,
                "task_id": task_id,
                "frequency": params.frequency,
                "voltage": params.voltage,
                "fan_speed": params.fan_speed,
            },
        )


# ---------------------------------------------------------------------------
# Bittensor validator discovery
# ---------------------------------------------------------------------------


def _normalize_endpoint(raw: str) -> Optional[str]:
    value = (raw or "").strip().strip('"').strip("'")
    if not value:
        return None
    if not value.startswith("http://") and not value.startswith("https://"):
        value = f"http://{value}"
    return value.rstrip("/")


@contextmanager
def _suppress_root_logging_temporarily(level: int = logging.CRITICAL) -> Iterator[None]:
    """
    Bittensor ``Subtensor.get_commitment`` logs ``logging.error(exception)`` when ``decode_metadata`` fails
    (e.g. uid with no commitment → metadata shape None → ``decode_metadata`` does ``metadata['info'][...]``
    and raises ``TypeError: 'NoneType' object is not subscriptable``). That is expected for empty UIDs; the SDK
    still returns \"\" but pollutes our miner logs. Briefly raise the *root* logger level so those ERROR lines
    are not emitted during discovery (single-threaded miner startup only).

    Some SDK builds attach handlers on the ``bittensor`` logger with their own level, so root-only suppression
    is not enough — also raise common bittensor sub-loggers for the discovery window.
    """
    root = logging.getLogger()
    prev: list[tuple[logging.Logger, int]] = [(root, root.level)]
    root.setLevel(level)
    for name in ("bittensor", "bittensor.core", "bittensor.core.subtensor"):
        lg = logging.getLogger(name)
        prev.append((lg, lg.level))
        lg.setLevel(level)
    try:
        yield
    finally:
        for lg, old in prev:
            lg.setLevel(old)


def _get_commitment_quiet(subtensor: Any, netuid: int, uid: int) -> str:
    """``subtensor.get_commitment`` without SDK ERROR spam on decode failure (see _suppress_root_logging_temporarily)."""
    with _suppress_root_logging_temporarily():
        return subtensor.get_commitment(netuid, uid)


def _neuron_stake(metagraph: Any, uid: int) -> float:
    """Stake S[uid] as float (subnet alpha units; depends on SDK version)."""
    s_arr = getattr(metagraph, "S", None)
    if s_arr is None:
        return 0.0
    try:
        s = s_arr[uid]
    except (IndexError, TypeError, KeyError):
        return 0.0
    try:
        return float(s)
    except Exception:
        if hasattr(s, "item"):
            return float(s.item())
        return float(s)


def discover_validator_endpoints(
    *,
    network: str,
    netuid: int,
    blacklist_validator_min_stake: float = 0.0,
    blacklist_force_validator_permit: bool = True,
    timeout_s: float = 6.0,
) -> list[str]:
    """
    Discover **all** live validator HTTP endpoints from chain commitments.

    Mirrors the blacklist logic from ``mode-network/synth-subnet`` ``neurons/miner.py``
    (outbound discovery is the dual of inbound ``blacklist()``):

    - ``blacklist_validator_min_stake``: skip UIDs with ``S[uid] <=`` this value
      (same comparison as ``stake <= validator_min_stake`` there; default ``0`` keeps all positive stake).
    - ``blacklist_force_validator_permit``: if True, only UIDs with ``validator_permit`` (like synth).

    No priority: UIDs are processed in ascending order; every matching UID with a
    commitment and healthy ``/health`` is included.
    """
    subtensor = bt.Subtensor(network=network)
    metagraph = bt.Metagraph(netuid=netuid, network=network)
    with _suppress_root_logging_temporarily():
        metagraph.sync(subtensor=subtensor)

    def _commitment_for_uid(uid: int) -> Optional[str]:
        """
        Read commitment per-UID (same chain query as ``scripts/set_validator_commitment.py``).

        Uses ``_get_commitment_quiet`` because upstream ``bittensor`` ``get_commitment`` does
        ``logging.error(error)`` on *any* ``decode_metadata`` failure (bittensor/core/subtensor.py ~1590),
        which for empty UIDs often prints only ``'NoneType' object is not subscriptable`` — misleading noise.
        """
        try:
            raw = _get_commitment_quiet(subtensor, netuid, uid)
        except Exception:
            return None
        if raw is None:
            return None
        if isinstance(raw, str) and not raw.strip():
            return None
        if isinstance(raw, (list, tuple)) and raw:
            raw = raw[0]
        s = str(raw).strip()
        if not s:
            return None
        return _normalize_endpoint(s)

    out: list[str] = []
    n = int(metagraph.n)
    vperm = getattr(metagraph, "validator_permit", None)
    for uid in range(n):
        if blacklist_force_validator_permit:
            if vperm is None:
                # Some SDKs leave this unset; do not block discovery.
                pass
            else:
                try:
                    if not bool(vperm[uid]):
                        continue
                except (IndexError, TypeError, KeyError):
                    continue
        stake = _neuron_stake(metagraph, uid)
        # Same rule as synth-subnet: blacklist when stake <= threshold
        if stake <= float(blacklist_validator_min_stake):
            continue
        endpoint = _commitment_for_uid(uid)
        if not endpoint:
            continue
        try:
            ok = requests.get(f"{endpoint}/health", timeout=timeout_s).status_code == 200
            if ok:
                logger.info("Validator endpoint uid=%s stake=%s url=%s", uid, stake, endpoint)
                out.append(endpoint)
        except Exception:
            continue
    if not out:
        raise RuntimeError(
            "No live validator endpoints discovered (check commitments, min validator stake, and /health)."
        )
    return out


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


def task_from_claim_task_dict(task_obj: dict[str, Any]) -> TaskInfo:
    """Build ``TaskInfo`` from the nested ``task`` object in ``POST /task`` JSON."""
    return TaskInfo(
        task_id=task_obj["task_id"],
        device_id=task_obj["device_id"],
        asic_model=task_obj["asic_model"],
        ambient_level=task_obj["ambient_level"],
        target=task_obj["target"],
        query_budget=int(task_obj["query_budget"]),
        expires_at=task_obj["expires_at"],
    )


class MinerRunner:
    """
    Claim → predict → (optional validate) → submit loop until no tasks or publication completes.
    """

    def __init__(self, client: ValidatorClient) -> None:
        self.client = client

    @staticmethod
    def _task_from_claim(task_obj: dict[str, Any]) -> TaskInfo:
        return task_from_claim_task_dict(task_obj)

    def run_publication(
        self,
        model: MinerModel,
        *,
        miner_uid: int,
        miner_hotkey: str,
        asic_model: str,
        target: str,
        model_description_json: Optional[dict[str, Any]] = None,
    ) -> PublicationRunResult:
        """
        One full publication run: repeatedly claims until 404 or ``publication_completed``.

        We validate model output format/ranges locally, but only validator simulation decides success.
        """
        publication_id: Optional[str] = None
        tasks_attempted = 0
        last_state: Optional[str] = None
        completed = False

        while True:
            r = self.client.claim_task(
                miner_uid=miner_uid,
                miner_hotkey=miner_hotkey,
                asic_model=asic_model,
                target=target,
                publication_id=publication_id,
                model_description_json=model_description_json,
            )
            if r.status_code == 410:
                logger.warning("Publication deadline expired (410 on claim); stopping run.")
                break
            if r.status_code == 404:
                logger.info("No more tasks available (404). Ending run.")
                # Validator may finish the publication only on this path (pool exhausted after last submit).
                if tasks_attempted > 0 and publication_id:
                    completed = True
                break
            r.raise_for_status()
            data = r.json()
            publication_id = data["publication_id"]
            task = self._task_from_claim(data["task"])
            tasks_attempted += 1

            params = model.predict(task)
            errs = validate_optimization_params(params)
            if errs:
                raise MinerModelError(errs)

            sr = self.client.submit(publication_id=publication_id, task_id=task.task_id, params=params)
            if sr.status_code == 410:
                logger.warning("Publication deadline expired (410 on submit); stopping run.")
                break
            if sr.status_code >= 400:
                logger.error("submit failed: %s %s", sr.status_code, sr.text)
            sr.raise_for_status()
            sub = sr.json()
            last_state = sub.get("state")
            completed = bool(sub.get("publication_completed"))
            logger.info(
                "task=%s state=%s overheated=%s publication_completed=%s",
                task.task_id,
                last_state,
                sub.get("overheated"),
                completed,
            )
            if completed:
                break

        if publication_id is None:
            publication_id = ""
        return PublicationRunResult(
            publication_id=publication_id,
            tasks_attempted=tasks_attempted,
            last_submit_state=last_state,
            publication_completed=completed,
        )


def configure_logging(level: int = logging.INFO) -> None:
    logging.basicConfig(level=level, format="%(asctime)s | %(levelname)s | %(message)s")
