from datetime import date, timedelta

import pytest

from fearngreed.model import (
    FlowObservation,
    classify_percentile,
    fit_latest_signal,
    rolling_signals,
)


def observations(count: int = 253) -> list[FlowObservation]:
    start = date(2020, 1, 1)
    rows = []
    for index in range(count):
        x = ((index % 23) - 11) / 1000
        noise = ((index * 7) % 13 - 6) / 20_000
        rows.append(FlowObservation(start + timedelta(days=index), x, -0.8 * x + noise))
    return rows


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (5, "extreme_fear"),
        (5.1, "fear"),
        (20, "fear"),
        (79.9, "neutral"),
        (80, "greed"),
        (95, "extreme_greed"),
    ],
)
def test_boundaries(value: float, expected: str) -> None:
    assert classify_percentile(value) == expected


def test_current_day_is_excluded_and_fit_is_valid() -> None:
    rows = observations()
    current = FlowObservation(rows[-1].date, rows[-1].return_1d, -0.03)
    result = fit_latest_signal(rows[:-1], current)
    assert result.training_count == 252
    assert result.beta == pytest.approx(-0.8, abs=0.02)
    assert result.rolling_r2 is not None and result.rolling_r2 > 0.9
    assert result.percentile == 0
    assert result.trade_eligible


def test_duplicate_days_fail_closed() -> None:
    rows = observations(201)
    result = fit_latest_signal(rows[:200] + [rows[0]], rows[-1])
    assert result.state == "unavailable"
    assert result.quality == "duplicate_dates"


def test_rolling_rejects_duplicate_dates() -> None:
    rows = observations(10)
    with pytest.raises(ValueError, match="duplicate"):
        rolling_signals(rows + [rows[-1]])
