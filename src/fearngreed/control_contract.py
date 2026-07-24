from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from datetime import date
from typing import Any

PROJECT_ID = "fear-greed"
INPUT_SCHEMA_VERSION = "fear-greed/control-inputs-v1"
RESULT_SCHEMA_VERSION = "fear-greed/control-result-v1"
CONFIG_HASH_ALGORITHM = "fear-greed-json-sort-keys-sha256-v1"

DEFAULT_CONTROL_INPUTS: dict[str, Any] = {
    "window": "ytd",
    "historyStart": "",
    "historyEnd": "",
    "historyEndMode": "latest",
    "model": "raw",
    "eventAsset": "KOSPI",
    "eventSample": "all",
    "backtestProxy": "1x",
    "backtestPolicy": "compare",
    "backtestVariant": "raw_ols",
    "backtestCost": 10,
    "backtestPeriod": "common",
    "longExitPercentile": 80,
    "signalLookback": 196,
    "signalMinimumR2": 0.4,
    "signalExtremeTail": 2,
    "signalMaxHolding": 20,
}

_ENUMS = {
    "window": {"1m", "3m", "6m", "ytd", "1y", "3y", "all", "custom"},
    "historyEndMode": {"latest", "fixed"},
    "model": {"robust", "scaled", "raw"},
    "eventAsset": {"KOSPI", "226490", "069500"},
    "eventSample": {"all", "nonOverlapping20d"},
    "backtestProxy": {"1x", "2x"},
    "backtestPolicy": {"compare", "long_cash", "long_inverse_cash"},
    "backtestVariant": {"scaled_huber", "scaled_ols", "raw_ols", "disparity"},
    "backtestPeriod": {"common", "full"},
}


@dataclass(frozen=True, slots=True)
class NormalizedControlInputs:
    requested: dict[str, Any]
    normalized: dict[str, Any]
    effective: dict[str, Any]
    config_hash: str


def canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode()


def canonical_sha256(value: Any) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


INPUT_SCHEMA_HASH = canonical_sha256(
    {
        "schemaVersion": INPUT_SCHEMA_VERSION,
        "fields": tuple(DEFAULT_CONTROL_INPUTS),
        "defaults": DEFAULT_CONTROL_INPUTS,
    }
)


def _finite_number(value: Any, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise TypeError(f"{field} must be a finite number")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"{field} must be a finite number")
    return number


def _integer(value: Any, field: str, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{field} must be an integer")
    if not minimum <= value <= maximum:
        raise ValueError(f"{field} must be between {minimum} and {maximum}")
    return value


def _iso_date_or_empty(value: Any, field: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{field} must be an ISO date or an empty string")
    if value:
        date.fromisoformat(value)
    return value


def normalize_control_inputs(inputs: dict[str, Any]) -> NormalizedControlInputs:
    if not isinstance(inputs, dict):
        raise TypeError("Fear & Greed inputs must be a JSON object")
    expected = set(DEFAULT_CONTROL_INPUTS)
    observed = set(inputs)
    if observed != expected:
        missing = sorted(expected - observed)
        unknown = sorted(observed - expected)
        details = []
        if missing:
            details.append("missing: " + ", ".join(missing))
        if unknown:
            details.append("unknown: " + ", ".join(unknown))
        raise ValueError("inputs must contain every declared field (" + "; ".join(details) + ")")

    normalized = dict(inputs)
    for field, choices in _ENUMS.items():
        if normalized[field] not in choices:
            raise ValueError(f"{field} is not an allowed value")

    normalized["historyStart"] = _iso_date_or_empty(normalized["historyStart"], "historyStart")
    normalized["historyEnd"] = _iso_date_or_empty(normalized["historyEnd"], "historyEnd")
    if normalized["window"] == "custom":
        if not normalized["historyStart"] or not normalized["historyEnd"]:
            raise ValueError("custom windows require historyStart and historyEnd")
        if normalized["historyStart"] > normalized["historyEnd"]:
            raise ValueError("historyStart must not be after historyEnd")
    if normalized["historyEndMode"] == "fixed" and not normalized["historyEnd"]:
        raise ValueError("fixed historyEndMode requires historyEnd")

    cost = _finite_number(normalized["backtestCost"], "backtestCost")
    if cost not in {0.0, 5.0, 10.0, 20.0}:
        raise ValueError("backtestCost must be 0, 5, 10, or 20")
    normalized["backtestCost"] = int(cost)
    normalized["longExitPercentile"] = _integer(
        normalized["longExitPercentile"], "longExitPercentile", 50, 94
    )
    normalized["signalLookback"] = _integer(normalized["signalLookback"], "signalLookback", 60, 756)
    minimum_r2 = _finite_number(normalized["signalMinimumR2"], "signalMinimumR2")
    if not 0 <= minimum_r2 <= 0.8 or abs(minimum_r2 * 20 - round(minimum_r2 * 20)) > 1e-9:
        raise ValueError("signalMinimumR2 must be between 0 and 0.8 in 0.05 steps")
    normalized["signalMinimumR2"] = minimum_r2
    normalized["signalExtremeTail"] = _integer(
        normalized["signalExtremeTail"], "signalExtremeTail", 1, 20
    )
    normalized["signalMaxHolding"] = _integer(
        normalized["signalMaxHolding"], "signalMaxHolding", 1, 60
    )

    requested = dict(inputs)
    effective = dict(normalized)
    return NormalizedControlInputs(
        requested=requested,
        normalized=normalized,
        effective=effective,
        config_hash=canonical_sha256(effective),
    )


def classify_control_percentile(value: float | None, extreme_tail: int) -> str:
    tail = _integer(extreme_tail, "signalExtremeTail", 1, 20)
    if value is None or not math.isfinite(value):
        return "unavailable"
    if value <= tail:
        return "extreme_fear"
    if value <= 20:
        return "fear"
    if value < 80:
        return "neutral"
    if value < 100 - tail:
        return "greed"
    return "extreme_greed"


def minimum_observation_count(lookback: int) -> int:
    window = _integer(lookback, "signalLookback", 60, 756)
    return min(window, max(40, min(200, math.ceil(window * 0.8))))
