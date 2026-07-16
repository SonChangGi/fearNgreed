from __future__ import annotations

from datetime import UTC, datetime

import numpy as np
import pandas as pd

from fearngreed.backtest import run_backtest
from fearngreed.model import FlowSignal
from fearngreed.pipeline import (
    PipelineInputs,
    _compact_result,
    _scatter,
    _scatter_meta,
    _semiconductor_diagnostics,
    build_outputs,
    output_size_report,
)


def _signal(
    timestamp: pd.Timestamp, *, state: str = "neutral", percentile: float = 50
) -> FlowSignal:
    return FlowSignal(
        timestamp.date(),
        0.0,
        -1.0,
        0.7,
        0.0,
        0.0,
        percentile,
        state,
        "ok",
        252,
        True,
    )


def _pipeline_inputs(periods: int = 320) -> PipelineInputs:
    index = pd.bdate_range("2024-01-02", periods=periods)
    rng = np.random.default_rng(20260716)
    returns = rng.normal(0.0004, 0.01, periods)
    close = 2_500 * np.cumprod(1 + returns)
    open_price = close / (1 + rng.normal(0, 0.002, periods))
    trading_value = np.full(periods, 12_000_000_000_000.0)
    flow_share = 0.001 - 1.5 * pd.Series(close, index=index).pct_change().fillna(0).to_numpy()
    flow_share += rng.normal(0, 0.002, periods)
    kospi = pd.DataFrame(
        {
            "open": open_price,
            "high": np.maximum(open_price, close) * 1.002,
            "low": np.minimum(open_price, close) * 0.998,
            "close": close,
            "trading_volume": np.full(periods, 1_000_000.0),
            "trading_value": trading_value,
        },
        index=index,
    )
    flow = pd.DataFrame(
        {"individual_net_purchase": flow_share * trading_value}, index=index
    )

    def prices(values: np.ndarray) -> pd.DataFrame:
        return pd.DataFrame({"open": values * 0.999, "close": values}, index=index)

    p226490 = 25_000 * close / close[0]
    p069500 = 30_000 * close / close[0]
    hynix = 120_000 * np.cumprod(1 + rng.normal(0.0005, 0.015, periods))
    samsung = 70_000 * np.cumprod(1 + rng.normal(0.0003, 0.012, periods))
    mu = 100 * np.cumprod(1 + rng.normal(0.0005, 0.016, periods))
    fx = 1_300 * np.cumprod(1 + rng.normal(0, 0.002, periods))
    adjusted = {
        "^KS11": prices(close),
        "226490.KS": prices(p226490),
        "069500.KS": prices(p069500),
        "000660.KS": prices(hynix),
        "005930.KS": prices(samsung),
        "MU": prices(mu),
        "KRW=X": prices(fx),
    }
    return PipelineInputs(
        kospi=kospi,
        flow=flow,
        adjusted=adjusted,
        krx_etfs={"226490": prices(p226490), "069500": prices(p069500)},
        generated_at=datetime(2026, 7, 16, tzinfo=UTC),
        core_source="test",
        krx_stocks={"000660": prices(hynix), "005930": prices(samsung)},
    )


def test_public_outputs_expose_both_models_without_expanding_daily_history() -> None:
    outputs = build_outputs(_pipeline_inputs())

    entity = outputs.summary["primaryEntities"][0]
    for payload in (entity["models"], outputs.dashboard["models"], outputs.history["models"]):
        assert set(payload) == {"scaled", "raw"}
        for model_name, model in payload.items():
            assert model["model"] == model_name
            assert {
                "state",
                "percentile",
                "residualZ",
                "beta",
                "rollingR2",
                "trainingCount",
                "quality",
                "tradeEligible",
            }.issubset(model)
            assert model["trainingCount"] == 252
    assert "rawState" not in outputs.history["series"][-1]
    assert output_size_report(outputs)["history"] < 2_000_000


def test_scatter_contains_exact_training_window_and_one_current_observation() -> None:
    outputs = build_outputs(_pipeline_inputs())
    scatter = outputs.dashboard["scatter"]
    meta = outputs.dashboard["scatterMeta"]

    assert len(scatter) == 253
    assert [point["role"] for point in scatter].count("training") == 252
    assert [point["role"] for point in scatter].count("current") == 1
    assert scatter[-1]["role"] == "current"
    assert meta["trainingCount"] == 252
    assert meta["currentCount"] == 1
    assert meta["pointCount"] == 253


def test_compacted_equity_always_keeps_the_latest_row() -> None:
    index = pd.bdate_range("2026-01-02", periods=12)
    bars = pd.DataFrame(
        {"open": np.arange(100.0, 112.0), "close": np.arange(100.5, 112.5)}, index=index
    )
    result = run_backtest([_signal(timestamp) for timestamp in index], bars, ticker="226490")

    public = _compact_result(result, include_equity=True)

    assert public["equity"][-1]["date"] == index[-1].date().isoformat()
    assert public["equity"][-1]["value"] == result.equity.iloc[-1]


def test_semiconductor_diagnostics_publish_actual_ratio_and_official_crosschecks() -> None:
    inputs = _pipeline_inputs()
    outputs = build_outputs(inputs)
    diagnostics = outputs.dashboard["diagnostics"]

    assert diagnostics["status"] == "ok"
    assert diagnostics["series"][-1]["date"] == diagnostics["latest"]["date"]
    assert diagnostics["latest"]["muHynixRatio"] > 0
    assert diagnostics["series"][-1]["muHynixRatioIndexed"] is not None
    assert diagnostics["series"][-1]["muHynixRelativeSpread"] is not None
    assert outputs.dashboard["crosschecks"]["stock"]["000660"]["state"] == "ok"
    assert outputs.dashboard["crosschecks"]["stock"]["005930"]["state"] == "ok"

    failed = _semiconductor_diagnostics(
        pd.DataFrame(index=inputs.kospi.index),
        inputs.adjusted,
        {"000660": {"state": "mismatch"}, "005930": {"state": "ok"}},
    )
    assert failed == {
        "status": "unavailable",
        "reason": "official_stock_crosscheck_failed",
        "failedTickers": ["000660"],
    }


def test_stale_official_stock_crosscheck_fails_diagnostics_closed() -> None:
    inputs = _pipeline_inputs()
    stale_stocks = dict(inputs.krx_stocks)
    stale_stocks["000660"] = stale_stocks["000660"].iloc[:-1]
    stale_inputs = PipelineInputs(
        kospi=inputs.kospi,
        flow=inputs.flow,
        adjusted=inputs.adjusted,
        krx_etfs=inputs.krx_etfs,
        generated_at=inputs.generated_at,
        core_source=inputs.core_source,
        krx_stocks=stale_stocks,
    )

    outputs = build_outputs(stale_inputs)

    check = outputs.dashboard["crosschecks"]["stock"]["000660"]
    assert check["state"] == "unavailable"
    assert check["reason"] == "expected_date_not_common"
    assert outputs.dashboard["diagnostics"]["status"] == "unavailable"
    assert outputs.summary["status"]["state"] == "degraded"
    assert "price_crosscheck_000660_unavailable" in outputs.summary["status"][
        "degradedReasons"
    ]


def test_scatter_helpers_are_empty_safe() -> None:
    frame = pd.DataFrame(columns=["return_1d", "flow_share"])
    assert _scatter(frame) == []
    assert _scatter_meta(frame)["pointCount"] == 0
