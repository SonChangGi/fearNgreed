from __future__ import annotations

import hashlib
from dataclasses import replace
from typing import Any

import pandas as pd

from .model import FlowObservation, FlowSignal, rolling_signals
from .quality import QualityReport, validate_core_inputs


def index_records_frame(records: list[dict[str, Any]]) -> pd.DataFrame:
    frame = pd.DataFrame.from_records(records)
    if frame.empty:
        return pd.DataFrame(
            columns=["open", "high", "low", "close", "trading_volume", "trading_value"]
        )
    frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
    frame = frame.dropna(subset=["date"]).set_index("date").sort_index()
    numeric = ["open", "high", "low", "close", "trading_volume", "trading_value"]
    for field in numeric:
        frame[field] = pd.to_numeric(frame[field], errors="coerce")
    return frame[numeric].dropna()


def build_analysis_frame(
    kospi: pd.DataFrame, flow: pd.DataFrame
) -> tuple[pd.DataFrame, list[FlowSignal], list[FlowSignal], QualityReport]:
    quality = validate_core_inputs(kospi, flow)
    if quality.state == "unavailable":
        return pd.DataFrame(), [], [], quality
    frame = kospi.join(flow, how="inner").sort_index()
    frame["return_1d"] = frame["close"].pct_change()
    calculated_share = frame["individual_net_purchase"] / frame["trading_value"]
    calculated_raw = frame["individual_net_purchase"] / 1_000_000_000_000
    frame["flow_share"] = (
        frame["flow_share_override"].combine_first(calculated_share)
        if "flow_share_override" in frame
        else calculated_share
    )
    frame["raw_flow_trillion"] = (
        frame["raw_flow_trillion_override"].combine_first(calculated_raw)
        if "raw_flow_trillion_override" in frame
        else calculated_raw
    )
    frame["disparity50"] = 100 * frame["close"] / frame["close"].rolling(50).mean()
    frame["mdd252"] = frame["close"] / frame["close"].rolling(252).max() - 1
    scaled = _signals(frame, "flow_share")
    raw = _signals(frame, "raw_flow_trillion")
    _attach_signals(frame, scaled, "scaled")
    _attach_signals(frame, raw, "raw")
    frame["model_confidence"] = [
        _confidence(scaled_signal, raw_signal)
        for scaled_signal, raw_signal in zip(scaled, raw, strict=True)
    ]
    frame["source_hash"] = [
        str(row["source_hash_override"])
        if "source_hash_override" in frame and pd.notna(row["source_hash_override"])
        else hashlib.sha256(
            (
                f"{timestamp.date().isoformat()}|{row.close:.10f}|{row.trading_value:.4f}|"
                f"{row.individual_net_purchase:.4f}"
            ).encode()
        ).hexdigest()[:16]
        for timestamp, row in frame.iterrows()
    ]
    return frame, scaled, raw, quality


def disparity_filtered_signals(
    signals: list[FlowSignal], frame: pd.DataFrame, *, lookback: int = 756, quantile: float = 0.1
) -> list[FlowSignal]:
    threshold = frame["disparity50"].shift(1).rolling(lookback, min_periods=252).quantile(quantile)
    output: list[FlowSignal] = []
    for signal, (_, row), limit in zip(signals, frame.iterrows(), threshold, strict=True):
        allow = (
            signal.trade_eligible
            and signal.state == "extreme_fear"
            and pd.notna(row["disparity50"])
            and pd.notna(limit)
            and float(row["disparity50"]) <= float(limit)
        )
        if signal.state == "extreme_fear" and not allow:
            output.append(replace(signal, trade_eligible=False))
        else:
            output.append(signal)
    return output


def align_us_before_krx(
    krx_dates: pd.DatetimeIndex, us_prices: pd.Series, fx_prices: pd.Series
) -> pd.DataFrame:
    """Align only U.S./FX observations whose labeled session date is before the KRX date."""
    left = pd.DataFrame({"krx_date": pd.to_datetime(krx_dates)}).sort_values("krx_date")
    us = pd.DataFrame(
        {"us_session_date": pd.to_datetime(us_prices.index), "mu_close_usd": us_prices.values}
    ).sort_values("us_session_date")
    fx = pd.DataFrame(
        {"fx_session_date": pd.to_datetime(fx_prices.index), "usdkrw": fx_prices.values}
    ).sort_values("fx_session_date")
    aligned = pd.merge_asof(
        left,
        us,
        left_on="krx_date",
        right_on="us_session_date",
        direction="backward",
        allow_exact_matches=False,
    )
    aligned = pd.merge_asof(
        aligned,
        fx,
        left_on="krx_date",
        right_on="fx_session_date",
        direction="backward",
        allow_exact_matches=False,
    )
    aligned["mu_close_krw"] = aligned["mu_close_usd"] * aligned["usdkrw"]
    return aligned.set_index("krx_date")


def drawdown(series: pd.Series, window: int = 252) -> pd.Series:
    return series / series.rolling(window, min_periods=min(20, window)).max() - 1


def _signals(frame: pd.DataFrame, value_column: str) -> list[FlowSignal]:
    observations = [
        FlowObservation(
            date=timestamp.date(),
            return_1d=float(row["return_1d"]),
            flow_share=float(row[value_column]),
        )
        for timestamp, row in frame.iterrows()
        if pd.notna(row["return_1d"]) and pd.notna(row[value_column])
    ]
    signal_map = {signal.date: signal for signal in rolling_signals(observations)}
    return [
        signal_map.get(timestamp.date(), _missing_signal(timestamp.date()))
        for timestamp in frame.index
    ]


def _attach_signals(frame: pd.DataFrame, signals: list[FlowSignal], prefix: str) -> None:
    for field in (
        "alpha",
        "beta",
        "rolling_r2",
        "residual",
        "residual_z",
        "percentile",
        "state",
        "quality",
        "training_count",
        "trade_eligible",
    ):
        frame[f"{prefix}_{field}"] = [getattr(signal, field) for signal in signals]


def _confidence(scaled: FlowSignal, raw: FlowSignal) -> str:
    if scaled.state == "unavailable":
        return "unavailable"
    if not scaled.trade_eligible:
        return "low_model_fit"
    if raw.state == "unavailable" or not raw.trade_eligible:
        return "scaled_only"
    if _direction(scaled.state) != _direction(raw.state):
        return "mixed"
    return "high"


def _direction(state: str) -> str:
    if state in {"fear", "extreme_fear"}:
        return "fear"
    if state in {"greed", "extreme_greed"}:
        return "greed"
    return "neutral"


def _missing_signal(day: Any) -> FlowSignal:
    return FlowSignal(day, None, None, None, None, None, None, "unavailable", "missing", 0, False)
