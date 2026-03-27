from __future__ import annotations

import hashlib
import time
from typing import TYPE_CHECKING, Any, Mapping, MutableMapping, Optional

if TYPE_CHECKING:
    from bittensor_wallet import Wallet


def body_sha256_hex(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def build_epistula_message(*, nonce: str, body: bytes) -> str:
    return f"{nonce}.{body_sha256_hex(body)}"


def sign_epistula_request_body(wallet: "Wallet", body: bytes) -> dict[str, str]:
    nonce = str(int(time.time() * 1_000_000_000))
    message = build_epistula_message(nonce=nonce, body=body)
    signature = wallet.hotkey.sign(message.encode()).hex()
    return {
        "X-Epistula-Timestamp": nonce,
        "X-Epistula-Signature": signature,
        "X-Epistula-Hotkey": wallet.hotkey.ss58_address,
    }


def _header_get(headers: Any, primary: str) -> Optional[str]:
    if headers is None:
        return None
    if hasattr(headers, "get"):
        for key in (primary, primary.lower(), primary.upper(), primary.title()):
            v = headers.get(key)  # type: ignore[call-arg]
            if v is not None:
                return str(v)
    return None


def verify_epistula_request(*, headers: Any, body: bytes, max_age_s: float = 120.0) -> str:
    from bittensor_wallet import Keypair

    ts = _header_get(headers, "X-Epistula-Timestamp")
    sig_hex = _header_get(headers, "X-Epistula-Signature")
    hotkey = _header_get(headers, "X-Epistula-Hotkey")
    if not ts or not sig_hex or not hotkey:
        raise ValueError("missing Epistula headers")

    try:
        ts_ns = int(str(ts))
    except ValueError as e:
        raise ValueError("invalid X-Epistula-Timestamp") from e

    now_ns = int(time.time() * 1_000_000_000)
    if abs(now_ns - ts_ns) > int(max_age_s * 1_000_000_000):
        raise ValueError("Epistula timestamp out of allowed window")

    message = build_epistula_message(nonce=str(ts), body=body)
    kp = Keypair(ss58_address=hotkey)
    try:
        ok = kp.verify(message.encode(), bytes.fromhex(sig_hex))
    except Exception as e:
        raise ValueError("Epistula signature verify failed") from e
    if not ok:
        raise ValueError("invalid Epistula signature")
    return str(hotkey)


def merge_headers(base: MutableMapping[str, str], extra: Mapping[str, str]) -> MutableMapping[str, str]:
    out = dict(base)
    out.update(extra)
    return out
