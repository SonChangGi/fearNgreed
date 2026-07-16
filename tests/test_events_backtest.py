from datetime import date, timedelta

import pandas as pd
import pytest

from fearngreed.backtest import (
    ProxyBar,
    result_to_public,
    run_backtest,
    run_backtest_safe,
    run_cost_sensitivity,
    run_long_cash,
)
from fearngreed.events import (
    ExtremeEvent,
    event_returns,
    extreme_entries,
    non_overlapping,
    summarize_event_returns,
    unconditional_forward_return_benchmarks,
)
from fearngreed.model import FlowSignal


def signal(index: int, state: str, percentile: float, eligible: bool = True) -> FlowSignal:
    return FlowSignal(
        date(2024, 1, 1) + timedelta(days=index),
        0,
        -1,
        0.7,
        0,
        0,
        percentile,
        state,
        "ok",
        252,
        eligible,
    )


def test_extreme_events_are_entry_only_and_non_overlapping() -> None:
    signals = [
        signal(0, "neutral", 50),
        signal(1, "extreme_fear", 2),
        signal(2, "extreme_fear", 3),
        signal(3, "neutral", 50),
        signal(4, "extreme_fear", 1),
    ]
    events = extreme_entries(signals)
    assert [item.index for item in events] == [1, 4]
    assert [item.index for item in non_overlapping(events, horizon=3)] == [1]


def test_next_open_entry_and_recovery_exit_with_costs() -> None:
    signals = [
        signal(0, "extreme_fear", 2),
        signal(1, "fear", 10),
        signal(2, "neutral", 55),
        signal(3, "neutral", 60),
    ]
    bars = [ProxyBar(item.date, 100 + index * 10) for index, item in enumerate(signals)]
    trades = run_long_cash(signals, bars, one_way_cost_bps=10)
    assert len(trades) == 1
    trade = trades[0]
    assert trade.entry_date == bars[1].date
    assert trade.exit_date == bars[3].date
    assert trade.reason == "recovery"
    assert trade.net_return == pytest.approx((130 / 110) * 0.999 * 0.999 - 1)


def test_ineligible_gap_does_not_create_duplicate_extreme_event() -> None:
    signals = [
        signal(0, "extreme_fear", 2),
        signal(1, "unavailable", 0, eligible=False),
        signal(2, "extreme_fear", 3),
        signal(3, "neutral", 50),
        signal(4, "extreme_fear", 1),
    ]
    assert [event.index for event in extreme_entries(signals)] == [0, 4]


def test_repeated_fear_is_ignored_while_position_is_open() -> None:
    signals = [
        signal(0, "extreme_fear", 2),
        signal(1, "extreme_fear", 1),
        signal(2, "extreme_fear", 3),
        signal(3, "neutral", 55),
        signal(4, "neutral", 60),
    ]
    bars = [ProxyBar(item.date, 100 + index, 100 + index) for index, item in enumerate(signals)]
    trades = run_long_cash(signals, bars)
    assert len(trades) == 1
    assert trades[0].entry_date == bars[1].date
    assert trades[0].exit_date == bars[4].date


def test_same_extreme_regime_does_not_reenter_after_max_holding_exit() -> None:
    signals = [signal(index, "extreme_fear", 2) for index in range(50)]
    bars = [ProxyBar(item.date, 100, 100) for item in signals]

    trades = run_long_cash(signals, bars, one_way_cost_bps=10)

    assert len(trades) == 1
    assert trades[0].reason == "max_holding"
    assert trades[0].holding_sessions == 20


def test_final_extreme_entry_is_reported_for_next_open_without_false_reentry() -> None:
    signals = [signal(index, "neutral", 50) for index in range(3)]
    signals[-1] = signal(2, "extreme_fear", 2)
    bars = pd.DataFrame(
        {"open": [100.0, 101.0, 102.0], "close": [100.0, 101.0, 102.0]},
        index=pd.to_datetime([item.date for item in signals]),
    )

    result = run_backtest(signals, bars, ticker="226490")

    assert result.open_position is False
    assert result.pending_action == "enter_next_open"
    assert result.pending_reason == "extreme_fear_entry"

    same_regime = [signal(index, "extreme_fear", 2) for index in range(25)]
    same_bars = pd.DataFrame(
        {"open": [100.0] * 25, "close": [100.0] * 25},
        index=pd.to_datetime([item.date for item in same_regime]),
    )
    exhausted = run_backtest(same_regime, same_bars, ticker="226490")
    assert exhausted.open_position is False
    assert exhausted.pending_action is None


def test_final_recovery_is_reported_as_next_open_exit() -> None:
    signals = [
        signal(0, "extreme_fear", 2),
        signal(1, "fear", 10),
        signal(2, "neutral", 55),
    ]
    bars = pd.DataFrame(
        {"open": [100.0, 101.0, 102.0], "close": [100.0, 101.0, 102.0]},
        index=pd.to_datetime([item.date for item in signals]),
    )

    result = run_backtest(signals, bars, ticker="226490")

    assert result.open_position is True
    assert result.pending_action == "exit_next_open"
    assert result.pending_reason == "recovery"


def test_event_returns_include_external_benchmark_and_paired_excess() -> None:
    index = pd.bdate_range("2024-01-01", periods=8)
    prices = pd.Series([100, 110, 108, 120, 118, 125, 130, 128], index=index)
    benchmark = pd.Series([100, 102, 103, 104, 106, 107, 108, 109], index=index)
    event_signal = signal(0, "extreme_fear", 2)
    event_signal = FlowSignal(
        index[0].date(),
        event_signal.alpha,
        event_signal.beta,
        event_signal.rolling_r2,
        event_signal.residual,
        event_signal.residual_z,
        event_signal.percentile,
        event_signal.state,
        event_signal.quality,
        event_signal.training_count,
        event_signal.trade_eligible,
    )

    rows = event_returns(
        [ExtremeEvent(0, event_signal)],
        prices,
        horizons=(1,),
        benchmark_prices=benchmark,
    )

    assert rows[0]["return1d"] == pytest.approx(0.10)
    assert rows[0]["benchmarkReturn1d"] == pytest.approx(0.02)
    assert rows[0]["excessReturn1d"] == pytest.approx(0.08)


def test_event_summary_supports_unconditional_excess_and_moving_blocks() -> None:
    rows = [
        {
            "date": f"2024-01-{day:02d}",
            "state": "extreme_fear",
            "return1d": value,
        }
        for day, value in enumerate([0.10, 0.20, 0.15, 0.25], start=1)
    ]
    rows.append({"date": "2024-02-01", "state": "extreme_greed", "return1d": -0.05})

    first = summarize_event_returns(
        rows,
        horizons=(1,),
        bootstrap_samples=500,
        benchmark_returns={1: 0.05},
        bootstrap_method="moving_block",
        block_length=2,
    )
    second = summarize_event_returns(
        rows,
        horizons=(1,),
        bootstrap_samples=500,
        benchmark_returns={1: 0.05},
        bootstrap_method="moving_block",
        block_length=2,
    )

    assert first == second
    fear = first[0]
    assert fear["mean"] == pytest.approx(0.175)
    assert fear["benchmarkMean"] == pytest.approx(0.05)
    assert fear["meanExcessReturn"] == pytest.approx(0.125)
    assert fear["bootstrapMethod"] == "moving_block"
    assert fear["bootstrapBlockLength"] == 2
    assert fear["meanExcessReturnCi95"][0] < fear["meanExcessReturnCi95"][1]
    assert fear["meanExcessReturnCi95BenchmarkTreatment"] == "fixed_external_mean"


def test_paired_event_excess_ci_reports_paired_benchmark_treatment() -> None:
    rows = [
        {
            "date": "2024-01-02",
            "state": "extreme_fear",
            "return1d": 0.10,
            "benchmarkReturn1d": 0.02,
        },
        {
            "date": "2024-01-03",
            "state": "extreme_fear",
            "return1d": 0.04,
            "benchmarkReturn1d": 0.01,
        },
    ]

    summary = summarize_event_returns(
        rows,
        horizons=(1,),
        bootstrap_samples=100,
        bootstrap_method="moving_block",
    )

    assert summary[0]["meanExcessReturnCi95BenchmarkTreatment"] == ("paired_event_returns")
    assert summary[1]["meanExcessReturnCi95BenchmarkTreatment"] == "unavailable"


def test_unconditional_forward_benchmark_uses_all_valid_sessions() -> None:
    prices = pd.Series([100.0, 110.0, 121.0], index=pd.bdate_range("2024-01-01", periods=3))
    assert unconditional_forward_return_benchmarks(prices, horizons=(1,)) == {
        1: pytest.approx(0.10)
    }


def test_cost_grid_and_matched_comparators_are_reported() -> None:
    signals = [
        signal(0, "extreme_fear", 2),
        signal(1, "fear", 10),
        signal(2, "neutral", 55),
        signal(3, "neutral", 60),
    ]
    bars = pd.DataFrame(
        {"open": [100.0, 100.0, 110.0, 120.0], "close": [100.0, 105.0, 115.0, 120.0]},
        index=pd.to_datetime([item.date for item in signals]),
    )

    results = run_cost_sensitivity(signals, bars, ticker="226490")

    assert [result.cost_bps for result in results] == [0, 5, 10, 20]
    assert results[0].metrics["totalReturn"] > results[-1].metrics["totalReturn"]
    metrics = results[2].metrics
    assert metrics["turnover"] == metrics["annualizedNotionalTurnover"]
    assert metrics["transactionSidesPerYear"] > 0
    assert metrics["exposureMatchedReturn"] is not None
    assert metrics["riskMatchedBuyHoldReturn"] is not None
    assert 0 <= metrics["riskMatchedScale"] <= 1


def test_exposure_matched_benchmark_uses_the_same_open_execution_windows() -> None:
    signals = [
        signal(0, "extreme_fear", 2),
        signal(1, "neutral", 55),
        signal(2, "neutral", 60),
    ]
    bars = pd.DataFrame(
        {
            "open": [100.0, 200.0, 200.0],
            "close": [100.0, 200.0, 100.0],
        },
        index=pd.to_datetime([item.date for item in signals]),
    )

    result = run_cost_sensitivity(signals, bars, ticker="226490")[0]

    assert result.metrics["totalReturn"] == pytest.approx(0.0)
    assert result.metrics["exposureMatchedReturn"] == pytest.approx(0.0)


def test_safe_backtest_is_explicitly_unavailable_and_publicable() -> None:
    bars = pd.DataFrame(
        {"open": [100.0, float("nan")], "close": [100.0, 101.0]},
        index=pd.bdate_range("2024-01-01", periods=2),
    )

    result = run_backtest_safe([], bars, ticker="226490")
    public = result_to_public(result)

    assert result.status == "unavailable"
    assert result.open_position is False
    assert result.metrics["state"] == "unavailable"
    assert public["status"] == "unavailable"
    assert public["equity"] == []
