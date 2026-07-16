from __future__ import annotations

import hashlib
from dataclasses import dataclass, replace
from typing import Any, Literal

import pandas as pd

from .model import FlowObservation, FlowSignal, rolling_signals
from .quality import QualityReport, validate_core_inputs


@dataclass(frozen=True)
class FlowChannelSpec:
    """Explicit input contract for one independently sourced flow channel."""

    key: str
    value_column: str
    participant: str
    normalization: str


@dataclass(frozen=True)
class SignalAgreement:
    """Separate broad sentiment direction from executable extreme triggers."""

    direction: str
    trigger: str
    scaled_trigger: str
    raw_trigger: str


INDIVIDUAL_SCALED = FlowChannelSpec(
    key="individual_scaled",
    value_column="flow_share",
    participant="individual",
    normalization="market_turnover_share",
)
INDIVIDUAL_RAW = FlowChannelSpec(
    key="individual_raw",
    value_column="raw_flow_trillion",
    participant="individual",
    normalization="krw_trillion",
)
OPTIONAL_FLOW_CHANNELS: dict[str, tuple[str, FlowChannelSpec]] = {
    "foreigner": (
        "foreigner_net_purchase",
        FlowChannelSpec(
            key="foreigner_scaled",
            value_column="foreigner_flow_share",
            participant="foreigner",
            normalization="market_turnover_share",
        ),
    ),
    "institutional": (
        "institutional_net_purchase",
        FlowChannelSpec(
            key="institutional_scaled",
            value_column="institutional_flow_share",
            participant="institutional",
            normalization="market_turnover_share",
        ),
    ),
}


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
    scaled = channel_signals(frame, INDIVIDUAL_SCALED)
    raw = channel_signals(frame, INDIVIDUAL_RAW)
    _attach_signals(frame, scaled, "scaled")
    _attach_signals(frame, raw, "raw")
    for participant, (source_column, spec) in OPTIONAL_FLOW_CHANNELS.items():
        override_column = f"{participant}_flow_share_override"
        if source_column not in frame and override_column not in frame:
            continue
        calculated = (
            pd.to_numeric(frame[source_column], errors="coerce") / frame["trading_value"]
            if source_column in frame
            else pd.Series(index=frame.index, dtype=float)
        )
        frame[spec.value_column] = (
            pd.to_numeric(frame[override_column], errors="coerce").combine_first(calculated)
            if override_column in frame
            else calculated
        )
        # Optional channels are diagnostic inputs only.  They are never folded
        # into the default individual-flow trading signal implicitly.
        _attach_signals(frame, channel_signals(frame, spec), participant)
    agreements = [
        compare_signal_agreement(scaled_signal, raw_signal)
        for scaled_signal, raw_signal in zip(scaled, raw, strict=True)
    ]
    frame["model_direction_agreement"] = [item.direction for item in agreements]
    frame["model_trigger_agreement"] = [item.trigger for item in agreements]
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
    threshold = (
        frame["disparity50"].shift(1).rolling(lookback, min_periods=lookback).quantile(quantile)
    )
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


def channel_signals(
    frame: pd.DataFrame,
    spec: FlowChannelSpec,
    *,
    fit_method: Literal["ols", "huber"] = "ols",
    window: int = 252,
    min_observations: int = 200,
) -> list[FlowSignal]:
    """Calculate one participant channel without assuming that its data exists.

    Callers adding foreigner or institutional flow must provide a populated
    column and a distinct ``FlowChannelSpec``.  This function never substitutes
    individual flow or zeroes for a missing channel.
    """
    if not spec.key or not spec.participant or not spec.normalization:
        raise ValueError("flow channel metadata must be explicit")
    if fit_method not in {"ols", "huber"}:
        raise ValueError(f"unsupported regression method: {fit_method}")
    if spec.value_column not in frame:
        raise ValueError(f"missing flow channel column: {spec.value_column}")
    observations = [
        FlowObservation(
            date=timestamp.date(),
            return_1d=float(row["return_1d"]),
            flow_share=float(row[spec.value_column]),
            channel=spec.key,
        )
        for timestamp, row in frame.iterrows()
        if pd.notna(row["return_1d"]) and pd.notna(row[spec.value_column])
    ]
    signal_map = {
        signal.date: signal
        for signal in rolling_signals(
            observations,
            window=window,
            min_observations=min_observations,
            fit_method=fit_method,
        )
    }
    return [
        signal_map.get(
            timestamp.date(),
            _missing_signal(timestamp.date(), fit_method=fit_method, channel=spec.key),
        )
        for timestamp in frame.index
    ]


def available_flow_channels(frame: pd.DataFrame) -> list[FlowChannelSpec]:
    """Describe only channels whose explicit value columns are available."""
    specs = [INDIVIDUAL_SCALED, INDIVIDUAL_RAW]
    specs.extend(spec for _, spec in OPTIONAL_FLOW_CHANNELS.values())
    return [spec for spec in specs if spec.value_column in frame]


def _signals(frame: pd.DataFrame, value_column: str) -> list[FlowSignal]:
    """Backward-compatible private wrapper for older internal callers."""
    return channel_signals(
        frame,
        FlowChannelSpec(value_column, value_column, "individual", "unspecified"),
    )


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
        "expected_flow",
        "fit_method",
        "fit_score",
        "channel",
    ):
        frame[f"{prefix}_{field}"] = [getattr(signal, field) for signal in signals]


def _confidence(scaled: FlowSignal, raw: FlowSignal) -> str:
    if scaled.state == "unavailable":
        return "unavailable"
    if not scaled.trade_eligible:
        return "low_model_fit"
    if raw.state == "unavailable" or not raw.trade_eligible:
        return "scaled_only"
    agreement = compare_signal_agreement(scaled, raw)
    if agreement.direction != "agree":
        return "mixed"
    if agreement.trigger in {"scaled_only", "raw_only", "conflict"}:
        return "mixed_trigger"
    return "high"


def compare_signal_agreement(scaled: FlowSignal, raw: FlowSignal) -> SignalAgreement:
    """Compare model interpretation without conflating direction and entry state."""
    if scaled.state == "unavailable" or raw.state == "unavailable":
        direction = "unavailable"
    else:
        direction = "agree" if _direction(scaled.state) == _direction(raw.state) else "mixed"

    scaled_trigger = _trigger(scaled)
    raw_trigger = _trigger(raw)
    if "unavailable" in {scaled_trigger, raw_trigger}:
        trigger = "unavailable"
    elif scaled_trigger == raw_trigger:
        trigger = "neither" if scaled_trigger == "none" else "both"
    elif scaled_trigger == "none":
        trigger = "raw_only"
    elif raw_trigger == "none":
        trigger = "scaled_only"
    else:
        trigger = "conflict"
    return SignalAgreement(direction, trigger, scaled_trigger, raw_trigger)


def _direction(state: str) -> str:
    if state in {"fear", "extreme_fear"}:
        return "fear"
    if state in {"greed", "extreme_greed"}:
        return "greed"
    return "neutral"


def _trigger(signal: FlowSignal) -> str:
    if signal.state == "unavailable" or not signal.trade_eligible:
        return "unavailable"
    if signal.state in {"extreme_fear", "extreme_greed"}:
        return signal.state
    return "none"


def _missing_signal(
    day: Any, *, fit_method: str = "ols", channel: str = "individual"
) -> FlowSignal:
    return FlowSignal(
        day,
        None,
        None,
        None,
        None,
        None,
        None,
        "unavailable",
        "missing",
        0,
        False,
        None,
        fit_method,
        None,
        channel,
    )
