from datetime import date, timedelta

import pandas as pd
import pytest

from fearngreed.backtest import (
    ProxyBar,
    actual_etf_pair_result_to_public,
    result_to_public,
    run_actual_etf_pair_backtest,
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


def actual_bars(signals: list[FlowSignal], prices: list[float]) -> pd.DataFrame:
    return pd.DataFrame(
        {"open": prices, "close": prices},
        index=pd.to_datetime([item.date for item in signals]),
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
        signal(2, "greed", 85),
        signal(3, "neutral", 60),
    ]
    bars = [ProxyBar(item.date, 100 + index * 10) for index, item in enumerate(signals)]
    trades = run_long_cash(signals, bars, one_way_cost_bps=10)
    assert len(trades) == 1
    trade = trades[0]
    assert trade.entry_date == bars[1].date
    assert trade.exit_date == bars[3].date
    assert trade.reason == "recovery"
    assert trade.entry_signal_date == signals[0].date
    assert trade.entry_reason == "extreme_fear_entry"
    assert trade.exit_signal_date == signals[2].date
    assert trade.exit_reason == "recovery"
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
        signal(3, "greed", 85),
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
    assert result.pending_signal_date == signals[-1].date

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
        signal(2, "greed", 80),
    ]
    bars = pd.DataFrame(
        {"open": [100.0, 101.0, 102.0], "close": [100.0, 101.0, 102.0]},
        index=pd.to_datetime([item.date for item in signals]),
    )

    result = run_backtest(signals, bars, ticker="226490")

    assert result.open_position is True
    assert result.pending_action == "exit_next_open"
    assert result.pending_reason == "recovery"
    assert result.pending_signal_date == signals[-1].date


def test_default_long_recovery_boundary_is_80_and_executes_next_open() -> None:
    signals = [
        signal(0, "extreme_fear", 2),
        signal(1, "neutral", 79),
        signal(2, "greed", 80),
        signal(3, "neutral", 50),
    ]
    bars = pd.DataFrame(
        {"open": [100.0] * 4, "close": [100.0] * 4},
        index=pd.to_datetime([item.date for item in signals]),
    )

    result = run_backtest(signals, bars, ticker="226490", one_way_cost_bps=0)

    assert result.long_exit_percentile == 80
    assert len(result.trades) == 1
    assert result.trades[0].entry_date == bars.index[1].date()
    assert result.trades[0].exit_date == bars.index[3].date()
    assert result.trades[0].reason == "recovery"


def test_synthetic_short_recovery_boundary_is_20_and_return_sign_is_short() -> None:
    signals = [
        signal(0, "extreme_greed", 98),
        signal(1, "neutral", 21),
        signal(2, "fear", 20),
        signal(3, "neutral", 50),
    ]
    bars = pd.DataFrame(
        {"open": [100.0, 100.0, 90.0, 80.0], "close": [100.0, 95.0, 85.0, 80.0]},
        index=pd.to_datetime([item.date for item in signals]),
    )

    result = run_backtest(
        signals,
        bars,
        ticker="226490",
        policy_id="long_short_cash",
        one_way_cost_bps=10,
    )

    assert result.short_exit_percentile == 20
    assert len(result.trades) == 1
    trade = result.trades[0]
    assert trade.side == "short"
    assert trade.entry_date == bars.index[1].date()
    assert trade.exit_date == bars.index[3].date()
    assert trade.gross_return == pytest.approx(0.20)
    assert trade.transaction_cost == pytest.approx(0.0017992)
    assert trade.net_return == pytest.approx(0.1980008)
    assert result.metrics["shortTradeCount"] == 1
    assert result.metrics["longTradeCount"] == 0


def test_opposite_extreme_reverses_on_one_open_and_charges_four_sides() -> None:
    signals = [
        signal(0, "extreme_fear", 2),
        signal(1, "extreme_greed", 98),
        signal(2, "fear", 20),
        signal(3, "neutral", 50),
    ]
    bars = pd.DataFrame(
        {"open": [100.0] * 4, "close": [100.0] * 4},
        index=pd.to_datetime([item.date for item in signals]),
    )

    result = run_backtest(
        signals,
        bars,
        ticker="226490",
        policy_id="long_short_cash",
        one_way_cost_bps=10,
    )

    assert [(trade.side, trade.reason) for trade in result.trades] == [
        ("long", "opposite_extreme"),
        ("short", "recovery"),
    ]
    assert result.trades[0].exit_date == result.trades[1].entry_date == bars.index[2].date()
    assert result.trades[0].exit_signal_date == signals[1].date
    assert result.trades[1].entry_signal_date == signals[1].date
    reversal = [action for action in result.actions if action.action_type == "reverse"]
    assert len(reversal) == 1
    assert reversal[0].signal_date == signals[1].date
    assert reversal[0].execution_date == bars.index[2].date()
    assert reversal[0].signal_phase == "after_close"
    assert reversal[0].execution_phase == "open"
    assert reversal[0].from_position == "long"
    assert reversal[0].to_position == "short"
    assert result.metrics["reversalCount"] == 1
    assert result.metrics["longTradeCount"] == 1
    assert result.metrics["shortTradeCount"] == 1
    assert result.metrics["totalReturn"] == pytest.approx(0.999**4 - 1)
    assert result.metrics["transactionCostTotal"] == pytest.approx(
        0.001 + 0.000999 + 0.000998001 + 0.000997002999
    )
    assert result.metrics["longExposure"] == pytest.approx(0.25)
    assert result.metrics["shortExposure"] == pytest.approx(0.25)
    assert result.metrics["cashExposure"] == pytest.approx(0.50)


def test_2026_06_10_is_one_atomic_reversal_from_the_prior_close_signal() -> None:
    days = [date(2026, 6, 8), date(2026, 6, 9), date(2026, 6, 10)]
    states = [
        ("extreme_fear", 2),
        ("extreme_greed", 98),
        ("neutral", 50),
    ]
    signals = [
        FlowSignal(day, 0, -1, 0.7, 0, 0, percentile, state, "ok", 252, True)
        for day, (state, percentile) in zip(days, states, strict=True)
    ]
    bars = pd.DataFrame(
        {"open": [100.0, 101.0, 102.0], "close": [100.0, 101.0, 102.0]},
        index=pd.to_datetime(days),
    )

    result = run_backtest(
        signals,
        bars,
        ticker="226490",
        policy_id="long_short_cash",
        one_way_cost_bps=10,
    )

    on_june_10 = [action for action in result.actions if action.execution_date == days[2]]
    assert len(on_june_10) == 1
    assert on_june_10[0].action_type == "reverse"
    assert on_june_10[0].signal_date == days[1]
    assert on_june_10[0].from_position == "long"
    assert on_june_10[0].to_position == "short"
    assert not [action for action in result.actions if action.signal_date == days[2]]
    assert result.trades[0].entry_signal_date == days[0]
    assert result.trades[0].entry_date == days[1]
    assert result.trades[0].exit_signal_date == days[1]
    assert result.trades[0].exit_date == days[2]

    public = result_to_public(result)
    public_reversal = [action for action in public["actions"] if action["type"] == "reverse"]
    assert len(public_reversal) == 1
    expected = {
        "actionId": "long_short_cash:226490:2026-06-10:0002:reverse",
        "signalDate": "2026-06-09",
        "executionDate": "2026-06-10",
        "signalPhase": "after_close",
        "executionPhase": "open",
        "type": "reverse",
        "fromPosition": "long",
        "toPosition": "short",
        "reason": "opposite_extreme",
        "price": 102.0,
    }
    assert {key: public_reversal[0][key] for key in expected} == expected
    assert public_reversal[0]["transactionCostAmount"] > 0


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
        signal(1, "greed", 80),
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


def test_actual_inverse_etf_uses_its_own_positive_unit_path_not_synthetic_short() -> None:
    signals = [
        signal(0, "extreme_greed", 98),
        signal(1, "greed", 90),
        signal(2, "fear", 20),
        signal(3, "fear", 15),
    ]
    long_bars = actual_bars(signals, [100.0, 100.0, 80.0, 70.0])
    inverse_bars = actual_bars(signals, [50.0, 50.0, 52.0, 55.0])

    actual = run_actual_etf_pair_backtest(
        signals,
        long_bars,
        inverse_bars,
        pair_id="1x",
        one_way_cost_bps=0,
    )
    synthetic = run_backtest(
        signals,
        long_bars,
        ticker="069500",
        policy_id="long_short_cash",
        one_way_cost_bps=0,
    )

    assert actual.trades[0].side == "inverse"
    assert actual.trades[0].instrument_ticker == "114800"
    assert actual.trades[0].gross_return == pytest.approx(0.10)
    assert synthetic.trades[0].gross_return == pytest.approx(0.30)
    assert actual.metrics["inverseExposure"] > 0
    assert actual.metrics["shortExposure"] == 0
    assert actual.exposure.min() == -1


def test_actual_2x_pair_uses_separate_prices_and_public_metadata() -> None:
    signals = [
        signal(0, "extreme_fear", 2),
        signal(1, "fear", 10),
        signal(2, "greed", 80),
        signal(3, "greed", 85),
    ]
    one_x = run_actual_etf_pair_backtest(
        signals,
        actual_bars(signals, [100.0, 100.0, 105.0, 110.0]),
        actual_bars(signals, [100.0, 100.0, 95.0, 90.0]),
        pair_id="1x",
        policy_id="long_cash",
        one_way_cost_bps=0,
    )
    two_x = run_actual_etf_pair_backtest(
        signals,
        actual_bars(signals, [100.0, 100.0, 120.0, 130.0]),
        actual_bars(signals, [100.0, 100.0, 85.0, 75.0]),
        pair_id="2x",
        policy_id="long_cash",
        one_way_cost_bps=0,
    )
    public = actual_etf_pair_result_to_public(two_x)

    assert one_x.trades[0].instrument_ticker == "069500"
    assert two_x.trades[0].instrument_ticker == "122630"
    assert one_x.trades[0].gross_return == pytest.approx(0.10)
    assert two_x.trades[0].gross_return == pytest.approx(0.30)
    assert public["pair"]["leverage"] == 2
    assert public["pair"]["longTicker"] == "122630"
    assert public["pair"]["inverseTicker"] == "252670"
    assert public["calculationSource"] == "python_verified_actual_etfs"
    assert public["equity"][-1]["instrumentTicker"] is None


def test_actual_pair_reversal_sells_and_buys_two_listed_etfs_at_distinct_opens() -> None:
    signals = [
        signal(0, "extreme_fear", 2),
        signal(1, "neutral", 10),
        signal(2, "extreme_greed", 98),
        signal(3, "greed", 90),
    ]
    result = run_actual_etf_pair_backtest(
        signals,
        actual_bars(signals, [100.0, 100.0, 110.0, 108.0]),
        actual_bars(signals, [50.0, 50.0, 48.0, 49.0]),
        pair_id="1x",
        one_way_cost_bps=10,
    )
    reversal = next(action for action in result.actions if action["type"] == "reverse")

    assert reversal["fromTicker"] == "069500"
    assert reversal["toTicker"] == "114800"
    assert reversal["fromPrice"] == 108
    assert reversal["toPrice"] == 49
    assert reversal["transactionSides"] == 2
    assert reversal["transactionCostAmount"] > 0
    assert result.position == "inverse"


def test_actual_long_cash_ignores_greed_and_never_buys_inverse() -> None:
    signals = [
        signal(0, "extreme_greed", 99),
        signal(1, "greed", 90),
        signal(2, "neutral", 50),
    ]
    result = run_actual_etf_pair_backtest(
        signals,
        actual_bars(signals, [100.0, 90.0, 80.0]),
        actual_bars(signals, [100.0, 120.0, 140.0]),
        pair_id="1x",
        policy_id="long_cash",
        one_way_cost_bps=0,
    )

    assert result.position == "cash"
    assert result.actions == []
    assert result.trades == []
    assert result.metrics["inverseExposure"] == 0
    assert result.metrics["totalReturn"] == 0


def test_actual_pair_range_preserves_carry_in_and_excludes_future_prices() -> None:
    signals = [
        signal(0, "extreme_fear", 2),
        signal(1, "fear", 10),
        signal(2, "fear", 15),
        signal(3, "fear", 10),
    ]
    base_long = actual_bars(signals[:3], [100.0, 100.0, 105.0])
    base_inverse = actual_bars(signals[:3], [100.0, 100.0, 95.0])
    kwargs = {
        "pair_id": "1x",
        "policy_id": "long_cash",
        "one_way_cost_bps": 0,
        "start_date": signals[1].date,
        "end_date": signals[2].date,
    }
    baseline = run_actual_etf_pair_backtest(signals[:3], base_long, base_inverse, **kwargs)
    future = run_actual_etf_pair_backtest(
        signals,
        actual_bars(signals, [100.0, 100.0, 105.0, 1.0]),
        actual_bars(signals, [100.0, 100.0, 95.0, 300.0]),
        **kwargs,
    )

    assert baseline.range["carryIn"] == {
        "position": "cash",
        "pendingAction": "enter_next_open",
        "pendingReason": "extreme_fear_entry",
        "pendingSide": "long",
        "signalDate": signals[0].date.isoformat(),
    }
    pd.testing.assert_series_equal(future.equity, baseline.equity)
    assert future.metrics == baseline.metrics
    assert future.range["appliedEndDate"] == signals[2].date.isoformat()


def test_actual_pair_rejects_missing_columns_and_public_exit_contract_violations() -> None:
    signals = [signal(0, "neutral", 50), signal(1, "neutral", 50)]
    malformed = pd.DataFrame(
        {"close": [100.0, 101.0]},
        index=pd.to_datetime([item.date for item in signals]),
    )
    valid = actual_bars(signals, [100.0, 101.0])

    with pytest.raises(ValueError, match="both ETF bars require"):
        run_actual_etf_pair_backtest(signals, valid, malformed)
    with pytest.raises(ValueError, match="50 through 94"):
        run_actual_etf_pair_backtest(signals, valid, valid, long_exit_percentile=95)
