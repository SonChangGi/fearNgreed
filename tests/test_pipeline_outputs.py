from __future__ import annotations

from datetime import UTC, datetime

import numpy as np
import pandas as pd
import pytest

from fearngreed.backtest import run_backtest
from fearngreed.model import FlowSignal
from fearngreed.pipeline import (
    PipelineInputs,
    _combined_price_crosscheck,
    _compact_result,
    _inherit_reconciliation_provenance,
    _reconcile_adjusted_etf_history,
    _scatter,
    _scatter_meta,
    _scatter_state_boundaries,
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
    flow = pd.DataFrame({"individual_net_purchase": flow_share * trading_value}, index=index)

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


def test_public_outputs_expose_three_models_with_compact_daily_history() -> None:
    outputs = build_outputs(_pipeline_inputs())

    entity = outputs.summary["primaryEntities"][0]
    for payload in (entity["models"], outputs.dashboard["models"], outputs.history["models"]):
        assert set(payload) == {"robust", "scaled", "raw"}
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
                "expected",
                "observed",
                "fitMethod",
                "fitScore",
            }.issubset(model)
            assert model["trainingCount"] == 252
    assert outputs.history["seriesEncoding"] == "columnar-v1"
    assert "rawState" in outputs.history["seriesColumns"]
    assert len(outputs.history["seriesRows"][-1]) == len(outputs.history["seriesColumns"])
    roles = outputs.history["flowChannelRoles"]
    assert roles["primaryChannel"] == "retail"
    assert roles["strategyChannelCount"] == 1
    assert roles["channels"]["retail"]["strategyUse"] == "primary"
    for channel in ("foreigner", "institutional"):
        assert roles["channels"][channel]["strategyUse"] == "diagnostic_only"
        assert roles["channels"][channel]["eligibleForTrading"] is False
    assert output_size_report(outputs)["history"] < 2_000_000


def test_scatter_contains_exact_training_window_and_one_current_observation() -> None:
    outputs = build_outputs(_pipeline_inputs())
    scatter = outputs.dashboard["scatterByModel"]["robust"]
    meta = outputs.dashboard["scatterMetaByModel"]["robust"]

    assert len(scatter) == 253
    assert [point["role"] for point in scatter].count("training") == 252
    assert [point["role"] for point in scatter].count("current") == 1
    assert scatter[-1]["role"] == "current"
    assert meta["trainingCount"] == 252
    assert meta["currentCount"] == 1
    assert meta["pointCount"] == 253
    boundaries = meta["stateBoundaries"]
    assert boundaries["method"] == "empirical_cdf_transition_order_statistic"
    assert boundaries["trainingCount"] == 252
    offsets = boundaries["residualOffsets"]
    assert offsets["extremeFearUpper"] <= offsets["fearUpper"]
    assert offsets["fearUpper"] <= offsets["greedLower"]
    assert offsets["greedLower"] <= offsets["extremeGreedLower"]


def test_scatter_state_boundaries_match_empirical_cdf_transition_ranks() -> None:
    outputs = build_outputs(_pipeline_inputs())
    scatter = outputs.dashboard["scatterByModel"]["robust"]
    regression = outputs.dashboard["regression"]["robust"]
    residuals = sorted(
        point["flowShare"] - (regression["alpha"] + regression["beta"] * point["return1d"])
        for point in scatter
        if point["role"] == "training"
    )
    offsets = outputs.dashboard["scatterMetaByModel"]["robust"]["stateBoundaries"][
        "residualOffsets"
    ]

    assert offsets["extremeFearUpper"] == pytest.approx(residuals[12], abs=2e-9)
    assert offsets["fearUpper"] == pytest.approx(residuals[50], abs=2e-9)
    assert offsets["greedLower"] == pytest.approx(residuals[201], abs=2e-9)
    assert offsets["extremeGreedLower"] == pytest.approx(residuals[239], abs=2e-9)


def test_scatter_state_boundaries_fail_closed_without_a_valid_fit() -> None:
    frame = pd.DataFrame(
        {
            "return_1d": [0.01, -0.01],
            "flow_share": [0.02, -0.02],
            "scaled_alpha": [np.nan, np.nan],
            "scaled_beta": [np.nan, np.nan],
            "scaled_percentile": [np.nan, np.nan],
            "scaled_state": ["unavailable", "unavailable"],
        },
        index=pd.bdate_range("2026-01-02", periods=2),
    )

    assert _scatter_state_boundaries(frame, "scaled") is None


def test_history_marks_pre_proxy_sessions_unavailable_instead_of_cash() -> None:
    inputs = _pipeline_inputs()
    proxy_start = inputs.kospi.index[20]
    adjusted = dict(inputs.adjusted)
    adjusted["226490.KS"] = adjusted["226490.KS"].loc[proxy_start:]
    adjusted["069500.KS"] = adjusted["069500.KS"].loc[proxy_start:]
    krx_etfs = {ticker: prices.loc[proxy_start:] for ticker, prices in inputs.krx_etfs.items()}
    outputs = build_outputs(
        PipelineInputs(
            kospi=inputs.kospi,
            flow=inputs.flow,
            adjusted=adjusted,
            krx_etfs=krx_etfs,
            generated_at=inputs.generated_at,
            core_source=inputs.core_source,
            krx_stocks=inputs.krx_stocks,
        )
    )
    columns = outputs.history["seriesColumns"]
    decoded = [dict(zip(columns, row, strict=True)) for row in outputs.history["seriesRows"]]

    assert all(row["position"] == "unavailable" for row in decoded[:20])
    assert decoded[20]["position"] in {"cash", "long"}


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
    assert "price_crosscheck_000660_unavailable" in outputs.summary["status"]["degradedReasons"]


def test_crosscheck_ignores_rows_after_the_pipeline_as_of_date() -> None:
    index = pd.bdate_range("2026-07-08", periods=6)
    primary = pd.Series([100, 101, 102, 103, 104, 999], index=index)
    secondary = pd.Series([100, 101, 102, 103, 104], index=index[:-1])

    check = _combined_price_crosscheck(
        primary,
        secondary,
        expected_date=index[-2],
    )

    assert check["state"] == "ok"
    assert check["date"] == index[-2].date().isoformat()
    assert check["primaryLatestDate"] == index[-2].date().isoformat()


def test_incremental_kospi_crosscheck_does_not_claim_reconstructed_anchors() -> None:
    inputs = _pipeline_inputs()
    outputs = build_outputs(
        PipelineInputs(
            kospi=inputs.kospi,
            flow=inputs.flow,
            adjusted=inputs.adjusted,
            krx_etfs=inputs.krx_etfs,
            generated_at=inputs.generated_at,
            core_source=inputs.core_source,
            krx_stocks=inputs.krx_stocks,
            kospi_secondary_history_independent=False,
        )
    )

    check = outputs.dashboard["crosschecks"]["kospi"]
    assert check["state"] == "ok"
    assert check["historicalAnchors"]["state"] == "unavailable"
    assert check["historicalAnchors"]["reason"] == "secondary_history_reconstructed"


def test_backtest_position_and_metrics_stop_at_the_signal_as_of_date() -> None:
    inputs = _pipeline_inputs()
    adjusted = dict(inputs.adjusted)
    future_date = inputs.kospi.index[-1] + pd.offsets.BDay(1)
    for ticker in ("226490.KS", "069500.KS"):
        frame = adjusted[ticker]
        future = pd.DataFrame(
            {
                "open": [float(frame.iloc[-1]["open"]) * 1.01],
                "close": [float(frame.iloc[-1]["close"]) * 1.02],
            },
            index=[future_date],
        )
        adjusted[ticker] = pd.concat([frame, future])
    outputs = build_outputs(
        PipelineInputs(
            kospi=inputs.kospi,
            flow=inputs.flow,
            adjusted=adjusted,
            krx_etfs=inputs.krx_etfs,
            generated_at=inputs.generated_at,
            core_source=inputs.core_source,
            krx_stocks=inputs.krx_stocks,
        )
    )

    backtest = outputs.dashboard["backtests"]["proxies"]["226490"]["fullPeriod"]
    assert backtest["robust_10bp"]["metrics"]["end"] == outputs.summary["dataAsOf"]


def test_missing_adjusted_signal_session_is_reconciled_before_backtest(monkeypatch) -> None:
    inputs = _pipeline_inputs()
    signal_date = inputs.kospi.index[270]
    adjusted = dict(inputs.adjusted)
    for yahoo_ticker in ("226490.KS", "069500.KS"):
        adjusted[yahoo_ticker] = adjusted[yahoo_ticker].drop(index=signal_date)

    def signals(frame, _channel, *, fit_method):
        assert fit_method == "huber"
        return [
            _signal(
                timestamp,
                state="extreme_fear" if timestamp == signal_date else "neutral",
                percentile=1 if timestamp == signal_date else 50,
            )
            for timestamp in frame.index
        ]

    monkeypatch.setattr("fearngreed.pipeline.channel_signals", signals)
    outputs = build_outputs(
        PipelineInputs(
            kospi=inputs.kospi,
            flow=inputs.flow,
            adjusted=adjusted,
            krx_etfs=inputs.krx_etfs,
            generated_at=inputs.generated_at,
            core_source=inputs.core_source,
            krx_stocks=inputs.krx_stocks,
        )
    )

    for ticker in ("226490", "069500"):
        reconciliation = outputs.dashboard["crosschecks"]["etf"][ticker]["historyReconciliation"]
        assert reconciliation["state"] == "ok"
        assert reconciliation["missingCount"] == 1
        assert reconciliation["filledCount"] == 1
        result = outputs.dashboard["backtests"]["proxies"][ticker]["fullPeriod"]["robust_10bp"]
        assert result["status"] == "ok"
        assert result["metrics"]["tradeCount"] == 1
    assert (
        outputs.summary["primaryEntities"][0]["fieldSources"]["adjustedProxy"]
        == "yfinance_adjusted_plus_scaled_krx_gap_rows"
    )


def test_incremental_reconciliation_keeps_prior_gap_repair_provenance() -> None:
    current = {
        "state": "ok",
        "source": "yfinance_adjusted_checked_against_krx_calendar",
        "filledCount": 0,
        "officialSessionCount": 2672,
        "unresolvedCount": 0,
    }
    prior = {
        "state": "ok",
        "source": "yfinance_adjusted_plus_scaled_krx_gap_rows",
        "filledCount": 29,
        "filledDateSample": ["2015-09-25", "2017-09-22", "2017-12-20"],
    }

    merged = _inherit_reconciliation_provenance(current, prior)

    assert merged["source"] == "yfinance_adjusted_plus_scaled_krx_gap_rows"
    assert merged["filledCount"] == 29
    assert merged["inheritedFilledCount"] == 29
    assert merged["currentRunFilledCount"] == 0
    assert merged["filledDateSample"] == prior["filledDateSample"]


def test_adjusted_gap_with_factor_break_fails_closed() -> None:
    index = pd.bdate_range("2026-07-06", periods=5)
    official = pd.DataFrame(
        {
            "open": [99, 100, 101, 102, 103],
            "high": [101, 102, 103, 104, 105],
            "low": [98, 99, 100, 101, 102],
            "close": [100, 101, 102, 103, 104],
        },
        index=index,
    )
    research = official[["open", "close"]].astype(float)
    research.loc[index[:2], ["open", "close"]] *= 0.9
    research.loc[index[3:], ["open", "close"]] *= 0.7
    research = research.drop(index=index[2])

    reconciled, report = _reconcile_adjusted_etf_history(
        official,
        research,
        expected_date=index[-1],
    )

    assert reconciled is not None
    assert report["state"] == "unavailable"
    assert report["missingCount"] == 1
    assert report["filledCount"] == 0
    assert report["unresolvedCount"] == 1
    assert report["reason"] == "adjacent_adjustment_factor_disagreement"


def test_unresolved_adjustment_break_excludes_proxy_from_public_backtests() -> None:
    inputs = _pipeline_inputs()
    gap_date = inputs.kospi.index[270]
    adjusted = dict(inputs.adjusted)
    proxy = adjusted["226490.KS"].copy()
    proxy.loc[proxy.index < gap_date, ["open", "close"]] *= 0.8
    adjusted["226490.KS"] = proxy.drop(index=gap_date)

    outputs = build_outputs(
        PipelineInputs(
            kospi=inputs.kospi,
            flow=inputs.flow,
            adjusted=adjusted,
            krx_etfs=inputs.krx_etfs,
            generated_at=inputs.generated_at,
            core_source=inputs.core_source,
            krx_stocks=inputs.krx_stocks,
        )
    )

    check = outputs.dashboard["crosschecks"]["etf"]["226490"]
    assert check["latestPriceState"] == "ok"
    assert check["state"] == "unavailable"
    assert check["reason"] == "adjusted_history_session_gaps_unresolved"
    assert check["historyReconciliation"]["unresolvedCount"] == 1
    assert "226490" not in outputs.dashboard["backtests"]["proxies"]
    assert outputs.summary["primaryEntities"][0]["position"] == "unavailable"


def test_scatter_helpers_are_empty_safe() -> None:
    frame = pd.DataFrame(columns=["return_1d", "flow_share"])
    assert _scatter(frame) == []
    assert _scatter_meta(frame)["pointCount"] == 0


def test_v2_outputs_separate_operations_signal_and_publish_full_result_matrix() -> None:
    outputs = build_outputs(_pipeline_inputs())
    entity = outputs.summary["primaryEntities"][0]

    assert outputs.summary["methodologyVersion"] == "fear-flow-v2"
    assert outputs.summary["status"]["label"] in {"데이터 정상", "데이터 저하"}
    assert entity["signalLabel"] in {
        "극단적 공포",
        "공포",
        "중립",
        "탐욕",
        "극단적 탐욕",
        "산출 불가",
    }
    assert entity["strategyModel"] == "robust_huber_scaled"
    assert entity["fieldSources"]["retailFlow"] == "authenticated_pykrx"
    assert outputs.dashboard["regression"]["primaryModel"] == "robust"
    assert set(outputs.dashboard["eventsByModel"]) == {"robust", "scaled", "raw"}
    proxy = outputs.dashboard["backtests"]["proxies"]["226490"]
    expected = {
        f"{model}_{cost}bp" for model in ("robust", "scaled", "raw") for cost in (0, 5, 10, 20)
    }
    assert expected.issubset(proxy["fullPeriod"])
    assert expected.issubset(proxy["commonPeriod"])
    full_equity = proxy["fullPeriod"]["raw_20bp"]["equity"]
    assert len(full_equity) >= 2
    assert full_equity[-1]["date"] == outputs.summary["dataAsOf"]
    assert {"buyHoldValue", "buyHoldDrawdown"}.issubset(full_equity[-1])
    assert proxy["commonBenchmarkEquity"]
    summary_row = outputs.dashboard["eventsByModel"]["robust"]["KOSPI"]["nonOverlapping20d"][
        "summary"
    ][0]
    assert summary_row["bootstrapMethod"] == "moving_block"
    assert "benchmarkMean" in summary_row
    assert "meanExcessReturn" in summary_row
    event_section = outputs.dashboard["eventsByModel"]["robust"]["KOSPI"]["nonOverlapping20d"]
    assert event_section["meanExcessReturnCi95BenchmarkTreatment"] == "fixed_external_mean"
    assert "meanExcessReturnCi95BenchmarkTreatment" not in summary_row


def test_pdf_replica_is_isolated_from_primary_signal_and_uses_source_cutoff() -> None:
    outputs = build_outputs(_pipeline_inputs(periods=700))
    replica = outputs.dashboard["pdfReplica"]

    assert replica["sourceCutoff"] == "2026-07-14"
    assert replica["signalUse"] == "excluded_from_threshold_selection_and_trading"
    assert len(replica["annotatedEvents"]) == 11
    assert replica["regression"]["allPoints"]["observationCount"] > 100
    assert replica["regression"]["annotatedExcluded"]["observationCount"] == (
        replica["regression"]["allPoints"]["observationCount"] - 11
    )
