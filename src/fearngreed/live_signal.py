from __future__ import annotations

import argparse
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

from .model import FlowObservation, FlowSignal, fit_latest_signal
from .providers.common import ProviderError
from .providers.pykrx_flow import fetch_kospi_index, fetch_market_participant_flows

KST = ZoneInfo("Asia/Seoul")
LIVE_CONTRACT = "fearngreed-live-signal"


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
) -> dict[str, Any]:
    if not os.getenv("KRX_ID") or not os.getenv("KRX_PW"):
        raise LiveSignalError("krx_login_credentials_missing")
    local_time = observed_at.astimezone(KST)
    if local_time.date() != day or not (time(15, 40) <= local_time.time() < time(16, 0)):
        raise LiveSignalError("live_capture_window_closed")
    history_path = root / "data" / "history.json"
    try:
        history = json.loads(history_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise LiveSignalError("live_history_contract_invalid") from None
    if not isinstance(history, dict):
        raise LiveSignalError("live_history_contract_invalid")
    rows = _decode_history(history)
    if not rows:
        raise LiveSignalError("live_history_empty")
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
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    now = args.now or datetime.now(KST)
    if now.tzinfo is None:
        now = now.replace(tzinfo=KST)
    day = args.date or now.astimezone(KST).date()
    root = repository_root()
    output = args.output if args.output.is_absolute() else root / args.output
    try:
        payload = build_live_payload(day=day, observed_at=now, root=root)
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
