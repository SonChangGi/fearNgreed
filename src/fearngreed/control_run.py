from __future__ import annotations

import argparse
import calendar
import hashlib
import json
import os
import re
from collections.abc import Mapping
from dataclasses import asdict, dataclass, replace
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import pandas as pd

from .analysis import disparity_filtered_signals
from .backtest import actual_etf_pair_result_to_public, run_actual_etf_pair_backtest
from .control_contract import (
    CONFIG_HASH_ALGORITHM,
    INPUT_SCHEMA_HASH,
    INPUT_SCHEMA_VERSION,
    PROJECT_ID,
    RESULT_SCHEMA_VERSION,
    canonical_json_bytes,
    canonical_sha256,
    classify_control_percentile,
    minimum_observation_count,
    normalize_control_inputs,
)
from .events import (
    event_returns,
    extreme_entries,
    non_overlapping,
    summarize_event_returns,
    unconditional_forward_return_benchmarks,
)
from .model import FlowObservation, FlowSignal, rolling_signals
from .pipeline import METHODOLOGY_VERSION

RESULT_IDENTITY_VERSION = "fear-greed-result-identity-v1"
PUBLIC_SITE_URL = "https://sonchanggi.github.io/fearNgreed/"
DATA_SOURCE = "fearngreed-public-history-v1"
_SAFE_RUN_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{7,127}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_CODE_VERSION = re.compile(r"^github:SonChangGi/fearNgreed@(?P<sha>[0-9a-f]{40})$")
_EVENT_FIELDS = {
    "KOSPI": "kospiClose",
    "226490": "p226490Close",
    "069500": "p069500Close",
}
_VARIANT_MODEL = {
    "scaled_huber": "robust",
    "scaled_ols": "scaled",
    "raw_ols": "raw",
}


class ControlledRunError(ValueError):
    """Raised when a controlled result cannot prove its full binding."""


@dataclass(frozen=True, slots=True)
class ControlBinding:
    run_id: str
    input_schema_version: str
    input_schema_hash: str
    config_hash_algorithm: str
    config_hash: str

    def to_dict(self) -> dict[str, str]:
        return {
            "projectId": PROJECT_ID,
            "runId": self.run_id,
            "inputSchemaVersion": self.input_schema_version,
            "inputSchemaHash": self.input_schema_hash,
            "configHashAlgorithm": self.config_hash_algorithm,
            "configHash": self.config_hash,
        }


def _strict_json_object(raw: str | bytes, label: str) -> dict[str, Any]:
    try:
        value = json.loads(
            raw,
            parse_constant=lambda token: (_ for _ in ()).throw(
                ValueError(f"non-standard JSON number: {token}")
            ),
        )
    except (json.JSONDecodeError, UnicodeDecodeError, ValueError) as exc:
        raise ControlledRunError(f"{label} is not strict JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise ControlledRunError(f"{label} must be a JSON object")
    return value


def validate_control_binding(
    *,
    run_id: str,
    input_schema_version: str,
    input_schema_hash: str,
    config_hash_algorithm: str,
    config_hash: str,
    normalized_inputs: Mapping[str, Any],
    allow_fallback: bool,
) -> ControlBinding:
    if not _SAFE_RUN_ID.fullmatch(run_id):
        raise ControlledRunError("control run id must be 8-128 path-safe ASCII characters")
    if input_schema_version != INPUT_SCHEMA_VERSION:
        raise ControlledRunError(f"input schema mismatch: expected {INPUT_SCHEMA_VERSION}")
    if input_schema_hash != INPUT_SCHEMA_HASH:
        raise ControlledRunError("input schema hash does not match this worker")
    if config_hash_algorithm != CONFIG_HASH_ALGORITHM:
        raise ControlledRunError("config hash algorithm does not match this worker")
    if not _SHA256.fullmatch(config_hash):
        raise ControlledRunError("config hash must be a lowercase SHA-256 digest")
    if canonical_sha256(dict(normalized_inputs)) != config_hash:
        raise ControlledRunError("normalized inputs do not reproduce config hash")
    if allow_fallback:
        raise ControlledRunError("Fear & Greed controlled runs require allowFallback=false")
    return ControlBinding(
        run_id=run_id,
        input_schema_version=input_schema_version,
        input_schema_hash=input_schema_hash,
        config_hash_algorithm=config_hash_algorithm,
        config_hash=config_hash,
    )


def _decode_history(payload: Mapping[str, Any]) -> list[dict[str, Any]]:
    columns = payload.get("seriesColumns")
    values = payload.get("seriesRows")
    if (
        not isinstance(columns, list)
        or not columns
        or not all(isinstance(column, str) and column for column in columns)
        or len(set(columns)) != len(columns)
        or not isinstance(values, list)
        or not values
    ):
        raise ControlledRunError("data/history.json has no valid columnar series")
    rows: list[dict[str, Any]] = []
    for value in values:
        if not isinstance(value, list) or len(value) != len(columns):
            raise ControlledRunError("data/history.json contains a row-width mismatch")
        row = dict(zip(columns, value, strict=True))
        try:
            date.fromisoformat(str(row["date"]))
        except (KeyError, ValueError) as exc:
            raise ControlledRunError("data/history.json contains an invalid date") from exc
        rows.append(row)
    if rows != sorted(rows, key=lambda row: row["date"]):
        raise ControlledRunError("data/history.json dates are not sorted")
    if len({row["date"] for row in rows}) != len(rows):
        raise ControlledRunError("data/history.json contains duplicate dates")
    return rows


def _shift_months(day: date, months: int) -> date:
    zero_based = day.year * 12 + day.month - 1 + months
    year, month_zero = divmod(zero_based, 12)
    month = month_zero + 1
    return date(year, month, min(day.day, calendar.monthrange(year, month)[1]))


def _evaluation_bounds(
    inputs: Mapping[str, Any],
    rows: list[dict[str, Any]],
) -> tuple[date | None, date]:
    latest = date.fromisoformat(str(rows[-1]["date"]))
    if inputs["historyEndMode"] == "fixed":
        end = date.fromisoformat(str(inputs["historyEnd"]))
    elif inputs["window"] == "custom" and inputs["historyEnd"]:
        end = date.fromisoformat(str(inputs["historyEnd"]))
    else:
        end = latest
    end = min(end, latest)
    window = str(inputs["window"])
    if window == "all":
        return None, end
    if window == "custom":
        return date.fromisoformat(str(inputs["historyStart"])), end
    if window == "ytd":
        return date(end.year, 1, 1), end
    shifts = {"1m": -1, "3m": -3, "6m": -6, "1y": -12, "3y": -36}
    return _shift_months(end, shifts[window]), end


def _finite_rows(
    rows: list[dict[str, Any]],
    *fields: str,
    end: date,
) -> list[dict[str, Any]]:
    return [
        row
        for row in rows
        if date.fromisoformat(str(row["date"])) <= end
        and all(
            isinstance(row.get(field), int | float)
            and not isinstance(row.get(field), bool)
            and pd.notna(row.get(field))
            for field in fields
        )
    ]


def _signals(
    rows: list[dict[str, Any]],
    model: str,
    inputs: Mapping[str, Any],
    end: date,
) -> list[FlowSignal]:
    flow_field = "rawFlowTrillion" if model == "raw" else "flowShare"
    usable = _finite_rows(rows, "return1d", flow_field, end=end)
    observations = [
        FlowObservation(
            date=date.fromisoformat(str(row["date"])),
            return_1d=float(row["return1d"]),
            flow_share=float(row[flow_field]),
        )
        for row in usable
    ]
    signals = rolling_signals(
        observations,
        window=int(inputs["signalLookback"]),
        min_observations=minimum_observation_count(int(inputs["signalLookback"])),
        fit_method="huber" if model == "robust" else "ols",
        minimum_fit_score=float(inputs["signalMinimumR2"]),
    )
    tail = int(inputs["signalExtremeTail"])
    return [
        replace(signal, state=classify_control_percentile(signal.percentile, tail))
        for signal in signals
    ]


def _signal_payload(signal: FlowSignal) -> dict[str, Any]:
    value = asdict(signal)
    value["date"] = signal.date.isoformat()
    value.update(
        {
            "return1d": None,
            "flowShare": None,
        }
    )
    return {
        "date": value["date"],
        "alpha": value["alpha"],
        "beta": value["beta"],
        "rollingR2": value["rolling_r2"],
        "residual": value["residual"],
        "residualZ": value["residual_z"],
        "percentile": value["percentile"],
        "state": value["state"],
        "quality": value["quality"],
        "trainingCount": value["training_count"],
        "tradeEligible": value["trade_eligible"],
        "expected": value["expected_flow"],
        "fitMethod": value["fit_method"],
        "fitScore": value["fit_score"],
    }


def _price_series(
    rows: list[dict[str, Any]],
    field: str,
    end: date,
) -> pd.Series:
    usable = _finite_rows(rows, field, end=end)
    return pd.Series(
        [float(row[field]) for row in usable],
        index=pd.to_datetime([row["date"] for row in usable]),
        dtype=float,
    )


def _event_section(
    rows: list[dict[str, Any]],
    signals: list[FlowSignal],
    inputs: Mapping[str, Any],
    start: date | None,
    end: date,
) -> dict[str, Any]:
    events = extreme_entries(signals)
    events = [
        event
        for event in events
        if (start is None or event.signal.date >= start) and event.signal.date <= end
    ]
    if inputs["eventSample"] == "nonOverlapping20d":
        events = non_overlapping(events, horizon=20)
    prices = _price_series(rows, _EVENT_FIELDS[str(inputs["eventAsset"])], end)
    benchmark = (
        None
        if inputs["eventAsset"] == "KOSPI"
        else _price_series(rows, _EVENT_FIELDS["KOSPI"], end)
    )
    event_rows = event_returns(events, prices, benchmark_prices=benchmark)
    benchmark_means = unconditional_forward_return_benchmarks(prices)
    summary = summarize_event_returns(
        event_rows,
        benchmark_returns=benchmark_means,
        bootstrap_samples=10_000,
        bootstrap_method="moving_block",
    )
    return {
        "asset": inputs["eventAsset"],
        "sample": inputs["eventSample"],
        "eventCount": len(event_rows),
        "startDate": start.isoformat() if start else None,
        "endDate": end.isoformat(),
        "bootstrap": {
            "method": "moving_block",
            "samples": 10_000,
            "seed": 20260715,
        },
        "events": event_rows,
        "summary": summary,
    }


def _bars(
    rows: list[dict[str, Any]],
    ticker: str,
    end: date,
) -> pd.DataFrame:
    usable = _finite_rows(rows, f"p{ticker}Open", f"p{ticker}Close", end=end)
    return pd.DataFrame(
        {
            "open": [float(row[f"p{ticker}Open"]) for row in usable],
            "close": [float(row[f"p{ticker}Close"]) for row in usable],
        },
        index=pd.to_datetime([row["date"] for row in usable]),
    )


def _strategy_section(
    rows: list[dict[str, Any]],
    variant_signals: list[FlowSignal],
    inputs: Mapping[str, Any],
    start: date | None,
    end: date,
) -> dict[str, Any]:
    pair = {
        "1x": ("069500", "114800"),
        "2x": ("122630", "252670"),
    }[str(inputs["backtestProxy"])]
    long_bars = _bars(rows, pair[0], end)
    inverse_bars = _bars(rows, pair[1], end)
    if inputs["backtestPeriod"] == "common":
        common = None
        for ticker in ("069500", "114800", "122630", "252670"):
            index = _bars(rows, ticker, end).index
            common = index if common is None else common.intersection(index)
        assert common is not None
        long_bars = long_bars.loc[long_bars.index.intersection(common)]
        inverse_bars = inverse_bars.loc[inverse_bars.index.intersection(common)]
    results: dict[str, Any] = {}
    for policy in ("long_cash", "long_inverse_cash"):
        result = run_actual_etf_pair_backtest(
            variant_signals,
            long_bars,
            inverse_bars,
            pair_id=str(inputs["backtestProxy"]),
            max_holding=int(inputs["signalMaxHolding"]),
            one_way_cost_bps=float(inputs["backtestCost"]),
            policy_id=policy,
            long_exit_percentile=float(inputs["longExitPercentile"]),
            start_date=start,
            end_date=end,
        )
        results[policy] = actual_etf_pair_result_to_public(result)
    selected_policy = (
        "long_inverse_cash" if inputs["backtestPolicy"] == "long_inverse_cash" else "long_cash"
    )
    return {
        "proxy": inputs["backtestProxy"],
        "policy": inputs["backtestPolicy"],
        "variant": inputs["backtestVariant"],
        "period": inputs["backtestPeriod"],
        "costBps": inputs["backtestCost"],
        "longExitPercentile": inputs["longExitPercentile"],
        "maxHolding": inputs["signalMaxHolding"],
        "longCash": results["long_cash"],
        "longInverse": results["long_inverse_cash"],
        "primary": results[selected_policy],
    }


def _atomic_write(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        temporary.write_bytes(content)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def _public_site_url(value: str) -> str:
    parsed = urlsplit(value)
    if (
        value != value.strip()
        or parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise ControlledRunError("public site URL must be a credential-free HTTPS base")
    return urlunsplit(("https", parsed.netloc, parsed.path.rstrip("/") + "/", "", ""))


def execute_controlled_run(
    *,
    control_inputs: Mapping[str, Any],
    binding: ControlBinding,
    code_version: str,
    root: Path,
    output_dir: Path,
    public_site_url: str = PUBLIC_SITE_URL,
) -> dict[str, Any]:
    code_match = _CODE_VERSION.fullmatch(code_version)
    if code_match is None:
        raise ControlledRunError("code version must be github:SonChangGi/fearNgreed@<40hex>")
    normalized = normalize_control_inputs(dict(control_inputs))
    validate_control_binding(
        run_id=binding.run_id,
        input_schema_version=binding.input_schema_version,
        input_schema_hash=binding.input_schema_hash,
        config_hash_algorithm=binding.config_hash_algorithm,
        config_hash=binding.config_hash,
        normalized_inputs=normalized.normalized,
        allow_fallback=False,
    )
    history_path = root / "data" / "history.json"
    history_bytes = history_path.read_bytes()
    history = _strict_json_object(history_bytes, "data/history.json")
    if history.get("methodologyVersion") != METHODOLOGY_VERSION:
        raise ControlledRunError("public history methodology version is not fear-flow-v5")
    rows = _decode_history(history)
    start, end = _evaluation_bounds(normalized.effective, rows)
    selected_model = str(normalized.effective["model"])
    model_signals = _signals(rows, selected_model, normalized.effective, end)
    variant_name = str(normalized.effective["backtestVariant"])
    variant_model = _VARIANT_MODEL.get(variant_name, "robust")
    variant_signals = (
        model_signals
        if variant_model == selected_model
        else _signals(rows, variant_model, normalized.effective, end)
    )
    if variant_name == "disparity":
        signal_dates = {signal.date for signal in variant_signals}
        frame_rows = [row for row in rows if date.fromisoformat(str(row["date"])) in signal_dates]
        frame = pd.DataFrame(
            {"disparity50": [row.get("disparity50") for row in frame_rows]},
            index=pd.to_datetime([row["date"] for row in frame_rows]),
        )
        variant_signals = disparity_filtered_signals(variant_signals, frame)

    event = _event_section(
        rows,
        model_signals,
        normalized.effective,
        start,
        end,
    )
    strategy = _strategy_section(
        rows,
        variant_signals,
        normalized.effective,
        start,
        end,
    )
    selected_signals = [
        signal
        for signal in model_signals
        if (start is None or signal.date >= start) and signal.date <= end
    ]
    latest_signal = selected_signals[-1] if selected_signals else model_signals[-1]
    primary = strategy["primary"]
    metrics = primary.get("metrics") or {}
    data_identity = {
        "source": DATA_SOURCE,
        "sourceHash": hashlib.sha256(history_bytes).hexdigest(),
        "dataAsOf": str(history.get("dataAsOf") or rows[-1]["date"]),
    }
    binding_identity = {
        **binding.to_dict(),
        "effectiveConfigHash": normalized.config_hash,
    }
    key_parts = {
        "identityVersion": RESULT_IDENTITY_VERSION,
        "canonicalJsonVersion": CONFIG_HASH_ALGORITHM,
        "binding": binding_identity,
        "dataIdentity": data_identity,
        "codeIdentity": {
            "repository": "SonChangGi/fearNgreed",
            "commitSha": code_match.group("sha"),
            "methodologyVersion": METHODOLOGY_VERSION,
        },
    }
    result_key = canonical_sha256(key_parts)
    calculated_at = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    summary = {
        "signalDate": latest_signal.date.isoformat(),
        "signalState": latest_signal.state,
        "signalPercentile": latest_signal.percentile,
        "eventAsset": normalized.effective["eventAsset"],
        "eventSample": normalized.effective["eventSample"],
        "eventCount": event["eventCount"],
        "strategyPosition": primary.get("latestPosition"),
        "strategyStatus": primary.get("status"),
        "strategyTotalReturn": metrics.get("totalReturn"),
        "methodologyVersion": METHODOLOGY_VERSION,
    }
    artifact = {
        "schemaVersion": 1,
        "contract": RESULT_SCHEMA_VERSION,
        "projectId": PROJECT_ID,
        "resultKey": result_key,
        "resultIdentity": {
            "identityVersion": RESULT_IDENTITY_VERSION,
            "resultKey": result_key,
            "keyParts": key_parts,
        },
        "requestedInputs": normalized.requested,
        "normalizedInputs": normalized.normalized,
        "effectiveInputs": normalized.effective,
        "data": data_identity,
        "calculatedAt": calculated_at,
        "signals": [_signal_payload(signal) for signal in selected_signals],
        "event": event,
        "strategy": strategy,
        "summary": summary,
    }
    artifact_relative = Path("data") / "control-runs" / "v1" / binding.run_id / f"{result_key}.json"
    artifact_path = root / artifact_relative
    artifact_bytes = canonical_json_bytes(artifact)
    artifact_sha = hashlib.sha256(artifact_bytes).hexdigest()
    manifest_path = output_dir / "result-manifest.json"
    bounded_payload = {
        "schemaVersion": artifact["schemaVersion"],
        "contract": artifact["contract"],
        "resultKey": result_key,
        "resultIdentity": artifact["resultIdentity"],
        "data": data_identity,
        "calculatedAt": calculated_at,
        "summary": summary,
    }
    artifact_url = _public_site_url(public_site_url) + artifact_relative.as_posix()
    manifest = {
        "binding": binding.to_dict(),
        "requestedInputs": normalized.requested,
        "normalizedInputs": normalized.normalized,
        "effectiveInputs": normalized.effective,
        "effectiveConfigHash": normalized.config_hash,
        "ignoredInputs": [],
        "fallbacks": [],
        "fallbackUsed": False,
        "fallbackReason": None,
        "dataAsOf": data_identity["dataAsOf"],
        "calculatedAt": calculated_at,
        "codeVersion": code_version,
        "dataIdentity": data_identity,
        "artifact": {
            "url": artifact_url,
            "sha256": artifact_sha,
            "byteSize": len(artifact_bytes),
            "contractVersion": RESULT_SCHEMA_VERSION,
        },
        "payload": bounded_payload,
    }
    _atomic_write(artifact_path, artifact_bytes)
    _atomic_write(manifest_path, canonical_json_bytes(manifest))
    return {
        "artifact": artifact,
        "artifactPath": artifact_path,
        "artifactSha256": artifact_sha,
        "artifactByteSize": len(artifact_bytes),
        "artifactUrl": artifact_url,
        "manifest": manifest,
        "manifestPath": manifest_path,
    }


def _boolean(value: str) -> bool:
    normalized = value.strip().lower()
    if normalized in {"false", "0", "no"}:
        return False
    if normalized in {"true", "1", "yes"}:
        return True
    raise argparse.ArgumentTypeError("expected a boolean")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run a bound Fear & Greed analysis")
    parser.add_argument("--analysis-inputs-json", required=True)
    parser.add_argument("--control-run-id", required=True)
    parser.add_argument("--control-input-schema-version", required=True)
    parser.add_argument("--control-input-schema-hash", required=True)
    parser.add_argument("--control-config-hash-algorithm", required=True)
    parser.add_argument("--control-config-hash", required=True)
    parser.add_argument("--allow-fallback", required=True, type=_boolean)
    parser.add_argument("--code-version", required=True)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--output-dir", type=Path, default=Path("outputs/controlled-run"))
    parser.add_argument("--public-site-url", default=PUBLIC_SITE_URL)
    parser.add_argument("--github-output", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    inputs = _strict_json_object(args.analysis_inputs_json, "analysis inputs")
    normalized = normalize_control_inputs(inputs)
    binding = validate_control_binding(
        run_id=args.control_run_id,
        input_schema_version=args.control_input_schema_version,
        input_schema_hash=args.control_input_schema_hash,
        config_hash_algorithm=args.control_config_hash_algorithm,
        config_hash=args.control_config_hash,
        normalized_inputs=normalized.normalized,
        allow_fallback=args.allow_fallback,
    )
    root = args.root.resolve()
    output_dir = args.output_dir
    if not output_dir.is_absolute():
        output_dir = root / output_dir
    result = execute_controlled_run(
        control_inputs=inputs,
        binding=binding,
        code_version=args.code_version,
        root=root,
        output_dir=output_dir,
        public_site_url=args.public_site_url,
    )
    if args.github_output:
        output_lines = {
            "artifact_path": result["artifactPath"].relative_to(root).as_posix(),
            "manifest_path": result["manifestPath"].as_posix(),
            "artifact_url": result["artifactUrl"],
            "artifact_sha256": result["artifactSha256"],
            "artifact_byte_size": result["artifactByteSize"],
        }
        with args.github_output.open("a", encoding="utf-8") as output:
            for key, value in output_lines.items():
                output.write(f"{key}={value}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
