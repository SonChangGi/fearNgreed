from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
import os
import tempfile
from datetime import UTC, date, datetime, time
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd
import requests

from .model import FlowObservation, FlowSignal, fit_latest_signal
from .pipeline import HISTORY_NUMERIC_PRECISION_DIGITS, METHODOLOGY_VERSION
from .providers.common import ProviderError
from .providers.pykrx_flow import fetch_kospi_index, fetch_market_participant_flows

KST = ZoneInfo("Asia/Seoul")
LIVE_CONTRACT = "fearngreed-live-signal"
PUBLIC_HISTORY_URL = "https://sonchanggi.github.io/fearNgreed/data/history.json"
PROVISIONAL_EXPIRED_REASON = "provisional_signal_expired"


class LiveSignalError(RuntimeError):
    """Public-safe fast-signal failure code."""


def repository_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _decode_history(payload: dict[str, Any]) -> list[dict[str, Any]]:
    if isinstance(payload.get("series"), list):
        rows = payload["series"]
        if all(isinstance(row, dict) for row in rows):
            return [dict(row) for row in rows]
        raise LiveSignalError("live_history_contract_invalid")
    columns = payload.get("seriesColumns")
    values = payload.get("seriesRows")
    if not isinstance(columns, list) or not isinstance(values, list):
        raise LiveSignalError("live_history_contract_invalid")
    if not columns or len(columns) != len(set(columns)):
        raise LiveSignalError("live_history_contract_invalid")
    rows: list[dict[str, Any]] = []
    for row in values:
        if not isinstance(row, list) or len(row) != len(columns):
            raise LiveSignalError("live_history_contract_invalid")
        rows.append(dict(zip(columns, row, strict=True)))
    return rows


def _validated_history_candidate(
    payload: object, *, source: str
) -> tuple[date, dict[str, Any], list[dict[str, Any]], str]:
    if not isinstance(payload, dict):
        raise LiveSignalError("live_history_contract_invalid")
    required_columns = {
        "date",
        "kospiClose",
        "return1d",
        "flowShare",
        "rawFlowTrillion",
    }
    columns = payload.get("seriesColumns")
    if (
        payload.get("schemaVersion") != 1
        or payload.get("methodologyVersion") != METHODOLOGY_VERSION
        or payload.get("fixture") is not False
        or payload.get("numericPrecisionDigits") != HISTORY_NUMERIC_PRECISION_DIGITS
        or payload.get("seriesEncoding") != "columnar-v1"
        or not isinstance(columns, list)
        or not required_columns.issubset(columns)
        or not isinstance(payload.get("models"), dict)
        or not isinstance(payload.get("flowChannelRoles"), dict)
    ):
        raise LiveSignalError("live_history_contract_invalid")
    rows = _decode_history(payload)
    if len(rows) < 252:
        raise LiveSignalError("live_history_contract_invalid")
    try:
        data_as_of = date.fromisoformat(str(payload.get("dataAsOf")))
        row_dates = [date.fromisoformat(str(row.get("date"))) for row in rows]
    except ValueError:
        raise LiveSignalError("live_history_contract_invalid") from None
    if (
        row_dates != sorted(row_dates)
        or len(row_dates) != len(set(row_dates))
        or row_dates[-1] != data_as_of
    ):
        raise LiveSignalError("live_history_contract_invalid")
    for row in rows[-252:]:
        try:
            values = [float(row[field]) for field in required_columns if field != "date"]
        except (KeyError, TypeError, ValueError):
            raise LiveSignalError("live_history_contract_invalid") from None
        if not all(math.isfinite(value) for value in values) or float(row["kospiClose"]) <= 0:
            raise LiveSignalError("live_history_contract_invalid")
    return data_as_of, payload, rows, source


def _read_history_path(
    path: Path, *, source: str
) -> tuple[date, dict[str, Any], list[dict[str, Any]], str]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise LiveSignalError("live_history_contract_invalid") from None
    return _validated_history_candidate(payload, source=source)


def _fetch_public_history(
    url: str,
) -> tuple[date, dict[str, Any], list[dict[str, Any]], str]:
    try:
        response = requests.get(
            url,
            headers={"Accept": "application/json"},
            timeout=10,
        )
        response.raise_for_status()
        payload = response.json()
    except (requests.RequestException, ValueError):
        raise LiveSignalError("live_public_history_unavailable") from None
    return _validated_history_candidate(payload, source="public-last-good")


def resolve_live_history(
    root: Path,
    *,
    public_history_url: str | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]], str]:
    """Select the freshest validated past-only history without trusting checkout age.

    The local file remains a no-network last-good fallback.  Production capture
    also compares the public, already-validated history so a detached or old
    checkout cannot silently anchor the same-day model to stale sessions.
    """

    candidates: list[tuple[date, dict[str, Any], list[dict[str, Any]], str]] = []
    configured_path = os.getenv("FEARNGREED_LIVE_HISTORY_PATH", "").strip()
    paths: list[tuple[Path, str]] = []
    if configured_path:
        configured = Path(configured_path).expanduser()
        if not configured.is_absolute():
            configured = root / configured
        paths.append((configured, "configured-last-good"))
    paths.append((root / "data" / "history.json", "repository-last-good"))
    seen: set[Path] = set()
    for path, source in paths:
        resolved = path.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        try:
            candidates.append(_read_history_path(resolved, source=source))
        except LiveSignalError:
            continue
    if public_history_url:
        try:
            candidates.append(_fetch_public_history(public_history_url))
        except LiveSignalError:
            pass
    if not candidates:
        raise LiveSignalError("live_history_contract_invalid")
    _, payload, rows, source = max(candidates, key=lambda candidate: candidate[0])
    return payload, rows, source


def expire_provisional_payload(
    payload: dict[str, Any],
    *,
    observed_at: datetime,
    confirmed_data_as_of: date | None = None,
) -> tuple[dict[str, Any], bool]:
    """Fail closed once a provisional signal is no longer current/actionable."""

    if payload.get("contract") != LIVE_CONTRACT:
        raise LiveSignalError("live_signal_contract_invalid")
    if payload.get("phase") != "provisional":
        return copy.deepcopy(payload), False
    try:
        signal_date = date.fromisoformat(str(payload.get("signalDate")))
        action_window = payload.get("actionWindow")
        if not isinstance(action_window, dict):
            raise ValueError
        closes_at = datetime.fromisoformat(
            str(action_window.get("closesAt")).replace("Z", "+00:00")
        )
    except ValueError:
        raise LiveSignalError("live_signal_contract_invalid") from None
    if closes_at.tzinfo is None:
        raise LiveSignalError("live_signal_contract_invalid")
    now = observed_at if observed_at.tzinfo is not None else observed_at.replace(tzinfo=KST)
    expired = now >= closes_at or (
        confirmed_data_as_of is not None and confirmed_data_as_of >= signal_date
    )
    result = copy.deepcopy(payload)
    if not expired:
        return result, False
    quality = result.get("quality")
    window = result.get("actionWindow")
    models = result.get("models")
    if (
        not isinstance(quality, dict)
        or not isinstance(window, dict)
        or not isinstance(models, dict)
    ):
        raise LiveSignalError("live_signal_contract_invalid")
    reasons = quality.get("reasons")
    if not isinstance(reasons, list):
        raise LiveSignalError("live_signal_contract_invalid")
    quality["state"] = "unavailable"
    quality["tradeEligible"] = False
    quality["reasons"] = list(dict.fromkeys([*reasons, PROVISIONAL_EXPIRED_REASON]))
    window["state"] = "closed"
    for model in models.values():
        if not isinstance(model, dict):
            raise LiveSignalError("live_signal_contract_invalid")
        model["tradeEligible"] = False
    return result, result != payload


def _finite(value: object) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise LiveSignalError("live_observation_invalid") from None
    if not math.isfinite(number):
        raise LiveSignalError("live_observation_invalid")
    return number


def _same_day_row(frame: pd.DataFrame, day: date, *, source: str) -> pd.Series:
    if not isinstance(frame, pd.DataFrame) or len(frame) != 1:
        raise LiveSignalError(f"live_{source}_session_unavailable")
    index = pd.to_datetime(frame.index, errors="coerce")
    if len(index) != 1 or pd.isna(index[0]) or index[0].date() != day:
        raise LiveSignalError(f"live_{source}_date_mismatch")
    return frame.iloc[0]


def _validated_kospi_sessions(
    frame: pd.DataFrame, *, history_date: date, day: date
) -> tuple[pd.Series, pd.Series]:
    """Return the current and prior rows only when the confirmed anchor is contiguous.

    Calendar adjacency is not sufficient for KRX sessions because weekends and
    holidays intervene.  Asking the provider for the complete anchor-to-current
    range lets us prove that no completed KRX session is missing from the
    confirmed history before calculating a one-session return.
    """

    if not isinstance(frame, pd.DataFrame) or frame.empty:
        raise LiveSignalError("live_kospi_session_unavailable")
    index = pd.to_datetime(frame.index, errors="coerce")
    if index.isna().any():
        raise LiveSignalError("live_kospi_date_mismatch")
    session_dates = [timestamp.date() for timestamp in index]
    if len(session_dates) != len(set(session_dates)):
        raise LiveSignalError("live_kospi_session_unavailable")
    if any(session < history_date or session > day for session in session_dates):
        raise LiveSignalError("live_kospi_date_mismatch")
    current_positions = [
        position for position, session in enumerate(session_dates) if session == day
    ]
    prior_positions = [position for position, session in enumerate(session_dates) if session < day]
    if len(current_positions) != 1:
        raise LiveSignalError("live_kospi_session_unavailable")
    if not prior_positions:
        raise LiveSignalError("live_history_session_gap")
    previous_position = max(prior_positions, key=session_dates.__getitem__)
    if session_dates[previous_position] != history_date:
        raise LiveSignalError("live_history_session_gap")
    return frame.iloc[current_positions[0]], frame.iloc[previous_position]


def _observations(
    rows: list[dict[str, Any]], *, value_field: str, channel: str
) -> list[FlowObservation]:
    observations: list[FlowObservation] = []
    for row in rows:
        try:
            row_date = date.fromisoformat(str(row["date"]))
            return_1d = _finite(row["return1d"])
            flow_value = _finite(row[value_field])
        except (KeyError, ValueError, LiveSignalError):
            continue
        observations.append(FlowObservation(row_date, return_1d, flow_value, channel))
    return observations[-252:]


def _signal_payload(signal: FlowSignal) -> dict[str, Any]:
    return {
        "state": signal.state,
        "quality": signal.quality,
        "tradeEligible": signal.trade_eligible,
        "percentile": signal.percentile,
        "residualZ": signal.residual_z,
        "rollingR2": signal.rolling_r2,
        "fitScore": signal.fit_score,
        "alpha": signal.alpha,
        "beta": signal.beta,
        "expected": signal.expected_flow,
        "residual": signal.residual,
        "trainingCount": signal.training_count,
        "fitMethod": signal.fit_method,
    }


def build_live_payload(
    *,
    day: date,
    observed_at: datetime,
    root: Path,
    kospi: pd.DataFrame | None = None,
    flow: pd.DataFrame | None = None,
    public_history_url: str | None = None,
) -> dict[str, Any]:
    if not os.getenv("KRX_ID") or not os.getenv("KRX_PW"):
        raise LiveSignalError("krx_login_credentials_missing")
    local_time = observed_at.astimezone(KST)
    if local_time.date() != day or not (time(15, 40) <= local_time.time() < time(16, 0)):
        raise LiveSignalError("live_capture_window_closed")
    history, rows, history_source = resolve_live_history(
        root,
        public_history_url=public_history_url,
    )
    history_date = date.fromisoformat(str(history.get("dataAsOf")))
    if history_date >= day:
        raise LiveSignalError("live_session_already_confirmed")

    kospi_frame = kospi if kospi is not None else fetch_kospi_index(history_date, day)
    flow_frame = flow if flow is not None else fetch_market_participant_flows(day, day)
    kospi_row, provider_previous_row = _validated_kospi_sessions(
        kospi_frame, history_date=history_date, day=day
    )
    flow_row = _same_day_row(flow_frame, day, source="flow")
    close = _finite(kospi_row.get("close"))
    trading_value = _finite(kospi_row.get("trading_value"))
    individual = _finite(flow_row.get("individual_net_purchase"))
    if close <= 0 or trading_value <= 0:
        raise LiveSignalError("live_observation_invalid")
    previous_close = _finite(rows[-1].get("kospiClose"))
    if previous_close <= 0:
        raise LiveSignalError("live_history_contract_invalid")
    provider_previous_close = _finite(provider_previous_row.get("close"))
    if provider_previous_close <= 0:
        raise LiveSignalError("live_observation_invalid")
    if abs(provider_previous_close / previous_close - 1) > 0.005:
        raise LiveSignalError("live_history_price_mismatch")
    return_1d = close / previous_close - 1
    flow_share = individual / trading_value
    raw_flow = individual / 1_000_000_000_000
    closes = [_finite(row.get("kospiClose")) for row in rows[-251:]] + [close]
    disparity50 = 100 * close / (sum(closes[-50:]) / len(closes[-50:]))
    mdd252 = close / max(closes[-252:]) - 1

    current_scaled = FlowObservation(day, return_1d, flow_share, "individual_scaled")
    current_raw = FlowObservation(day, return_1d, raw_flow, "individual_raw")
    scaled_training = _observations(rows, value_field="flowShare", channel="individual_scaled")
    raw_training = _observations(rows, value_field="rawFlowTrillion", channel="individual_raw")
    robust = fit_latest_signal(scaled_training, current_scaled, fit_method="huber")
    scaled = fit_latest_signal(scaled_training, current_scaled, fit_method="ols")
    raw = fit_latest_signal(raw_training, current_raw, fit_method="ols")

    opens_at = datetime.combine(day, time(15, 40), KST)
    closes_at = datetime.combine(day, time(16, 0), KST)
    confirmation_at = datetime.combine(day, time(18, 15), KST)
    if local_time < opens_at:
        window_state = "not_open"
    elif local_time < closes_at:
        window_state = "open"
    else:
        window_state = "closed"
    source_hash = hashlib.sha256(
        f"{day.isoformat()}|{close:.10f}|{trading_value:.4f}|{individual:.4f}".encode()
    ).hexdigest()[:16]
    return {
        "schemaVersion": 1,
        "contract": LIVE_CONTRACT,
        "projectId": "fearngreed",
        "methodologyVersion": history.get("methodologyVersion"),
        "signalDate": day.isoformat(),
        "phase": "provisional",
        "generatedAt": observed_at.astimezone(UTC).isoformat().replace("+00:00", "Z"),
        "sourceCutoff": "regular-session-close-provisional",
        "expectedConfirmationAt": confirmation_at.isoformat(),
        "historyDataAsOf": history_date.isoformat(),
        "actionWindow": {
            "mode": "after-hours-close",
            "opensAt": opens_at.isoformat(),
            "closesAt": closes_at.isoformat(),
            "state": window_state,
            "executionGuaranteed": False,
        },
        "quality": {
            "state": "ok",
            "tradeEligible": True,
            "reasons": [],
        },
        "inputRow": {
            "date": day.isoformat(),
            "kospiClose": close,
            "return1d": return_1d,
            "flowShare": flow_share,
            "rawFlowTrillion": raw_flow,
            "disparity50": disparity50,
            "mdd252": mdd252,
            "sourceHash": source_hash,
        },
        "models": {
            "robust": _signal_payload(robust),
            "scaled": _signal_payload(scaled),
            "raw": _signal_payload(raw),
        },
        "provenance": {
            "price": "authenticated-pykrx-kospi-index",
            "flow": "authenticated-pykrx-kospi-investor-flow",
            "flowScope": "KOSPI-excluding-ETF-ETN-ELW",
            "historyRole": "past-only-training",
            "historySource": history_source,
        },
    }


def write_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), allow_nan=False) + "\n"
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=path.parent, prefix=f".{path.name}.", delete=False
    ) as handle:
        handle.write(encoded)
        handle.flush()
        os.fsync(handle.fileno())
        temporary = Path(handle.name)
    temporary.replace(path)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Capture a separate same-day fast signal")
    parser.add_argument("--date", type=date.fromisoformat)
    parser.add_argument("--now", type=datetime.fromisoformat)
    parser.add_argument("--output", type=Path, default=Path("data/live-signal.json"))
    parser.add_argument(
        "--expire-stale",
        action="store_true",
        help="Fail closed an existing provisional output after confirmation time",
    )
    return parser.parse_args(argv)


def _confirmed_data_as_of(root: Path) -> date | None:
    try:
        summary = json.loads((root / "data" / "summary.json").read_text(encoding="utf-8"))
        return date.fromisoformat(str(summary.get("dataAsOf")))
    except (AttributeError, OSError, ValueError):
        return None


def expire_live_signal_file(
    path: Path,
    *,
    observed_at: datetime,
    confirmed_data_as_of: date | None = None,
) -> bool:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise LiveSignalError("live_signal_contract_invalid") from None
    if not isinstance(payload, dict):
        raise LiveSignalError("live_signal_contract_invalid")
    expired, changed = expire_provisional_payload(
        payload,
        observed_at=observed_at,
        confirmed_data_as_of=confirmed_data_as_of,
    )
    if changed:
        write_atomic(path, expired)
    return changed


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    now = args.now or datetime.now(KST)
    if now.tzinfo is None:
        now = now.replace(tzinfo=KST)
    day = args.date or now.astimezone(KST).date()
    root = repository_root()
    output = args.output if args.output.is_absolute() else root / args.output
    if args.expire_stale:
        try:
            changed = expire_live_signal_file(
                output,
                observed_at=now,
                confirmed_data_as_of=_confirmed_data_as_of(root),
            )
        except LiveSignalError as error:
            print(json.dumps({"ok": False, "reason": str(error)}, ensure_ascii=False))
            return 1
        print(
            json.dumps(
                {
                    "ok": True,
                    "changed": changed,
                    "state": "expired" if changed else "unchanged",
                },
                ensure_ascii=False,
                sort_keys=True,
            )
        )
        return 0
    try:
        public_history_url = os.getenv(
            "FEARNGREED_PUBLIC_HISTORY_URL",
            PUBLIC_HISTORY_URL,
        ).strip()
        payload = build_live_payload(
            day=day,
            observed_at=now,
            root=root,
            public_history_url=public_history_url or None,
        )
        write_atomic(output, payload)
    except LiveSignalError as error:
        if str(error) == "live_session_already_confirmed":
            print(
                json.dumps(
                    {
                        "ok": True,
                        "skipped": True,
                        "reason": "live_session_already_confirmed",
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                )
            )
            return 0
        reason = str(error)
        print(json.dumps({"ok": False, "reason": reason}, ensure_ascii=False))
        return 1
    except ProviderError as error:
        reason = str(error)
        if not reason.startswith(("live_", "krx_")):
            reason = "live_provider_failed"
        print(json.dumps({"ok": False, "reason": reason}, ensure_ascii=False))
        return 1
    except Exception:
        print(json.dumps({"ok": False, "reason": "live_pipeline_failed"}))
        return 1
    print(
        json.dumps(
            {
                "ok": True,
                "signalDate": payload["signalDate"],
                "phase": payload["phase"],
                "quality": payload["quality"]["state"],
                "actionWindow": payload["actionWindow"]["state"],
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
