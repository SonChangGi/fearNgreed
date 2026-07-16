from datetime import date, timedelta

import pytest

from fearngreed.backtest import ProxyBar, run_long_cash
from fearngreed.events import extreme_entries, non_overlapping
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
    assert trade.net_return == pytest.approx(130 / 110 - 1 - 0.002)
