from __future__ import annotations

import abc
import json
import logging
import sys
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Iterator, List, Optional, Sequence

import requests

from bittensor_wallet import Wallet

from utils.epistula import merge_headers, sign_epistula_request_body
from utils.logging_utils import setup_logging

logger = logging.getLogger(__name__)


def _strip_cli_arg(argv: list[str], name: str) -> list[str]:
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


def safe_import_bittensor() -> Any:
    # Prevent bittensor from trying to parse our TOML path passed as --config.
    sys.argv = _strip_cli_arg(list(sys.argv), "--config")
    import bittensor as bt
    # bittensor may elevate __main__ logger level; restore it to root level.
    app_level = logging.getLogger().level
    logging.getLogger("__main__").setLevel(app_level)
    logging.getLogger(__name__).setLevel(app_level)
    return bt


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
    frequency: float
    voltage: float
    fan_speed: float


@dataclass
class PublicationRunResult:
    publication_id: str
    tasks_attempted: int
    last_submit_state: Optional[str]
    publication_completed: bool


@dataclass(frozen=True)
class TaskSubmitFeedback:
    state: str
    queries_used: int
    remaining_queries: int
    can_continue: bool
    net_profit_usd_day: Optional[float]
    gross_revenue_usd_day: Optional[float]
    electricity_cost_usd_day: Optional[float]
    overheated: bool
    warning: Optional[str]


class MinerModelError(RuntimeError):
    def __init__(self, errors: Sequence[str]):
        self.errors = list(errors)
        super().__init__("; ".join(self.errors))


# ---------------------------------------------------------------------------
# Local validation (model output sanity checks)
# ---------------------------------------------------------------------------


def validate_optimization_params(params: OptimizationParams) -> List[str]:
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

    def should_continue(self, task: TaskInfo, feedback: TaskSubmitFeedback) -> bool:
        """Return True to continue optimizing the same task, else finalize task."""
        return False


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

    def decide_task(self, *, publication_id: str, task_id: str, action: str) -> requests.Response:
        return self._post_signed_json(
            "/task/decision",
            {
                "publication_id": publication_id,
                "task_id": task_id,
                "action": action,
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
    with _suppress_root_logging_temporarily():
        return subtensor.get_commitment(netuid, uid)


def _neuron_stake(metagraph: Any, uid: int) -> float:
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
    blacklist_validator_min_stake: float = -1.0,
    blacklist_force_validator_permit: bool = True,
    timeout_s: float = 10.0,
) -> list[str]:
    bt = safe_import_bittensor()
    subtensor = bt.Subtensor(network=network)
    metagraph = bt.Metagraph(netuid=netuid, network=network)
    with _suppress_root_logging_temporarily():
        metagraph.sync(subtensor=subtensor)

    def _commitment_for_uid(uid: int) -> Optional[str]:
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
    skip_permit = 0
    skip_stake = 0
    skip_no_commit = 0
    skip_health = 0
    thr = float(blacklist_validator_min_stake)
    for uid in range(n):
        if blacklist_force_validator_permit:
            if vperm is None:
                pass
            else:
                try:
                    if not bool(vperm[uid]):
                        skip_permit += 1
                        continue
                except (IndexError, TypeError, KeyError):
                    continue
        stake = _neuron_stake(metagraph, uid)
        if stake <= thr:
            skip_stake += 1
            continue
        endpoint = _commitment_for_uid(uid)
        if not endpoint:
            skip_no_commit += 1
            continue
        try:
            ok = requests.get(f"{endpoint}/health", timeout=timeout_s).status_code == 200
            if ok:
                logger.info("Validator endpoint uid=%s stake=%s url=%s", uid, stake, endpoint)
                out.append(endpoint)
            else:
                skip_health += 1
        except Exception:
            skip_health += 1
            continue
    if not out:
        logger.error(
            "Validator discovery: no live endpoints (netuid=%s n=%s). Skipped: permit=%s stake=%s "
            "no_commitment=%s health_fail=%s.",
            netuid,
            n,
            skip_permit,
            skip_stake,
            skip_no_commit,
            skip_health,
        )
        raise RuntimeError(
            "No live validator endpoints discovered (check commitments, min validator stake, and /health)."
        )
    return out


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


def task_from_claim_task_dict(task_obj: dict[str, Any]) -> TaskInfo:
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

            while True:
                params = model.predict(task)
                errs = validate_optimization_params(params)
                if errs:
                    raise MinerModelError(errs)

                sr = self.client.submit(publication_id=publication_id, task_id=task.task_id, params=params)
                if sr.status_code == 410:
                    logger.warning("Publication deadline expired (410 on submit); stopping run.")
                    completed = False
                    break
                if sr.status_code >= 400:
                    logger.error("submit failed: %s %s", sr.status_code, sr.text)
                sr.raise_for_status()
                sub = sr.json()
                last_state = str(sub.get("state"))
                completed = bool(sub.get("publication_completed"))
                feedback = TaskSubmitFeedback(
                    state=last_state,
                    queries_used=int(sub.get("queries_used", 0)),
                    remaining_queries=int(sub.get("remaining_queries", 0)),
                    can_continue=bool(sub.get("can_continue", False)),
                    net_profit_usd_day=(
                        float(sub["net_profit_usd_day"]) if sub.get("net_profit_usd_day") is not None else None
                    ),
                    gross_revenue_usd_day=(
                        float(sub["gross_revenue_usd_day"]) if sub.get("gross_revenue_usd_day") is not None else None
                    ),
                    electricity_cost_usd_day=(
                        float(sub["electricity_cost_usd_day"])
                        if sub.get("electricity_cost_usd_day") is not None
                        else None
                    ),
                    overheated=bool(sub.get("overheated", False)),
                    warning=sub.get("warning"),
                )
                logger.info(
                    "task=%s state=%s q=%s rem=%s net_usd_day=%s can_continue=%s publication_completed=%s",
                    task.task_id,
                    feedback.state,
                    feedback.queries_used,
                    feedback.remaining_queries,
                    feedback.net_profit_usd_day,
                    feedback.can_continue,
                    completed,
                )
                if completed:
                    break
                if not feedback.can_continue:
                    break
                if model.should_continue(task, feedback):
                    continue

                dr = self.client.decide_task(
                    publication_id=publication_id,
                    task_id=task.task_id,
                    action="finalize",
                )
                if dr.status_code == 410:
                    logger.warning("Publication deadline expired (410 on decision); stopping run.")
                    completed = False
                    break
                if dr.status_code >= 400:
                    logger.error("decision failed: %s %s", dr.status_code, dr.text)
                dr.raise_for_status()
                dsub = dr.json()
                last_state = str(dsub.get("state", last_state))
                completed = bool(dsub.get("publication_completed", completed))
                logger.info(
                    "task=%s finalized_by_miner state=%s publication_completed=%s",
                    task.task_id,
                    last_state,
                    completed,
                )
                break

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
    setup_logging(app_name="miner", level=level)
