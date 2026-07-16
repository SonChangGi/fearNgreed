from __future__ import annotations

from datetime import date

import pandas as pd

from fearngreed.analysis import (
    align_us_before_krx,
    available_flow_channels,
    build_analysis_frame,
    compare_signal_agreement,
    disparity_filtered_signals,
)
from fearngreed.model import FlowSignal


def test_us_data_is_strictly_before_each_krx_session() -> None:
    krx_dates = pd.to_datetime(["2026-07-13", "2026-07-14", "2026-07-15"])
    us = pd.Series(
        [100.0, 200.0, 300.0],
        index=pd.to_datetime(["2026-07-12", "2026-07-14", "2026-07-15"]),
    )
    fx = pd.Series(
        [1300.0, 1310.0, 1320.0],
        index=pd.to_datetime(["2026-07-12", "2026-07-14", "2026-07-15"]),
    )
    aligned = align_us_before_krx(krx_dates, us, fx)
    assert aligned.loc["2026-07-14", "mu_close_usd"] == 100
    assert aligned.loc["2026-07-15", "mu_close_usd"] == 200
    assert (aligned["us_session_date"] < aligned.index).all()
    assert (aligned["fx_session_date"] < aligned.index).all()


def _signal(state: str, percentile: float, eligible: bool = True) -> FlowSignal:
    return FlowSignal(
        date(2026, 7, 14),
        0.0,
        -1.0,
        0.5,
        0.0,
        0.0,
        percentile,
        state,
        "ok" if eligible else "low_model_fit",
        252,
        eligible,
    )


def test_direction_and_executable_trigger_agreement_are_separate() -> None:
    agreement = compare_signal_agreement(_signal("extreme_fear", 2), _signal("fear", 10))
    assert agreement.direction == "agree"
    assert agreement.trigger == "scaled_only"
    assert agreement.scaled_trigger == "extreme_fear"
    assert agreement.raw_trigger == "none"

    conflict = compare_signal_agreement(_signal("extreme_fear", 2), _signal("extreme_greed", 98))
    assert conflict.direction == "mixed"
    assert conflict.trigger == "conflict"


def test_optional_participant_channels_are_attached_but_not_default_inputs() -> None:
    index = pd.bdate_range("2025-01-02", periods=205)
    close = pd.Series([100 + item * 0.1 for item in range(len(index))], index=index)
    kospi = pd.DataFrame(
        {
            "open": close - 0.05,
            "close": close,
            "trading_value": 10_000_000_000_000.0,
        },
        index=index,
    )
    flow = pd.DataFrame(
        {
            "individual_net_purchase": [(-1) ** item * 100_000_000_000 for item in range(205)],
            "foreigner_net_purchase": [(-1) ** (item + 1) * 80_000_000_000 for item in range(205)],
            "institutional_net_purchase": [(-1) ** item * 20_000_000_000 for item in range(205)],
        },
        index=index,
    )

    frame, scaled, raw, quality = build_analysis_frame(kospi, flow)

    assert quality.state == "ok"
    assert len(scaled) == len(raw) == len(frame)
    assert {
        "foreigner_flow_share",
        "foreigner_expected_flow",
        "foreigner_state",
        "institutional_flow_share",
        "institutional_expected_flow",
        "institutional_state",
    }.issubset(frame.columns)
    assert [spec.key for spec in available_flow_channels(frame)] == [
        "individual_scaled",
        "individual_raw",
        "foreigner_scaled",
        "institutional_scaled",
    ]
    assert all(signal.channel == "individual_scaled" for signal in scaled)
    assert all(signal.channel == "individual_raw" for signal in raw)


def test_disparity_filter_requires_the_full_declared_lookback() -> None:
    index = pd.bdate_range("2020-01-02", periods=800)
    frame = pd.DataFrame({"disparity50": [100.0] * len(index)}, index=index)
    signals = [
        FlowSignal(
            timestamp.date(),
            0.0,
            -1.0,
            0.5,
            0.0,
            0.0,
            1.0,
            "extreme_fear",
            "ok",
            252,
            True,
        )
        for timestamp in index
    ]

    filtered = disparity_filtered_signals(signals, frame, lookback=756)

    assert not any(signal.trade_eligible for signal in filtered[:756])
    assert filtered[756].trade_eligible
