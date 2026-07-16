from datetime import date, timedelta

import pytest

from fearngreed.model import (
    FlowObservation,
    classify_percentile,
    fit_latest_signal,
    fit_regression,
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


def test_huber_fit_is_deterministic_and_resists_single_outlier() -> None:
    xs = [((index % 23) - 11) / 1000 for index in range(252)]
    ys = [-0.8 * x + (((index * 7) % 13) - 6) / 20_000 for index, x in enumerate(xs)]
    ys[-1] = 0.5

    ols = fit_regression(xs, ys)
    first = fit_regression(xs, ys, method="huber")
    second = fit_regression(xs, ys, method="huber")

    assert abs(ols.beta + 0.8) > 0.4
    assert first == second
    assert first.beta == pytest.approx(-0.8, abs=0.01)
    assert first.fit_score > 0.9
    # Ordinary R-squared remains unweighted and therefore still exposes the
    # outlier damage rather than cosmetically replacing it with the robust score.
    assert first.r2 < 0


def test_robust_signal_exposes_expected_value_score_method_and_channel() -> None:
    rows = observations()
    training = [
        FlowObservation(item.date, item.return_1d, item.flow_share, "foreigner_scaled")
        for item in rows[:-1]
    ]
    current = FlowObservation(rows[-1].date, rows[-1].return_1d, -0.03, "foreigner_scaled")

    result = fit_latest_signal(training, current, fit_method="huber")

    assert result.fit_method == "huber"
    assert result.fit_score is not None
    assert result.expected_flow == pytest.approx(
        result.alpha + result.beta * current.return_1d  # type: ignore[operator]
    )
    assert result.residual == pytest.approx(current.flow_share - result.expected_flow)
    assert result.channel == "foreigner_scaled"


def test_robust_fit_score_cannot_override_the_published_r2_gate() -> None:
    start = date(2020, 1, 1)
    xs = [((index % 23) - 11) / 1000 for index in range(252)]
    ys = [-0.8 * x + (((index * 7) % 13) - 6) / 20_000 for index, x in enumerate(xs)]
    ys[-1] = 0.5
    training = [
        FlowObservation(start + timedelta(days=index), x, y)
        for index, (x, y) in enumerate(zip(xs, ys, strict=True))
    ]
    current = FlowObservation(start + timedelta(days=253), 0.001, -0.03)

    result = fit_latest_signal(training, current, fit_method="huber")

    assert result.beta is not None and result.beta < 0
    assert result.rolling_r2 is not None and result.rolling_r2 < 0.20
    assert result.fit_score is not None and result.fit_score > 0.20
    assert result.quality == "low_model_fit"
    assert not result.trade_eligible


def test_future_training_rows_are_excluded_from_past_only_fit() -> None:
    rows = observations()
    current = rows[-1]
    future = FlowObservation(current.date + timedelta(days=1), 1.0, 999.0)

    expected = fit_latest_signal(rows[:-1], current)
    actual = fit_latest_signal(rows[:-1] + [future], current)

    assert actual == expected


def test_positive_slope_low_fit_and_zero_mad_fail_closed() -> None:
    start = date(2024, 1, 1)
    positive = [
        FlowObservation(
            start + timedelta(days=index),
            float(index),
            float(index) + float((index * 7) % 11) / 100,
        )
        for index in range(200)
    ]
    positive_result = fit_latest_signal(
        positive,
        FlowObservation(start + timedelta(days=201), 201.0, 201.0),
    )
    assert positive_result.beta is not None and positive_result.beta > 0
    assert positive_result.quality == "low_model_fit"
    assert not positive_result.trade_eligible

    weak = [
        FlowObservation(
            start + timedelta(days=index),
            float(index % 17),
            float((index * 13) % 19) - 0.1 * float(index % 17),
        )
        for index in range(200)
    ]
    weak_result = fit_latest_signal(
        weak,
        FlowObservation(start + timedelta(days=201), 1.0, 0.0),
    )
    assert weak_result.beta is not None and weak_result.beta < 0
    assert weak_result.quality == "low_model_fit"
    assert not weak_result.trade_eligible

    exact = [
        FlowObservation(start + timedelta(days=index), float(index), -2.0 * index)
        for index in range(200)
    ]
    zero_mad = fit_latest_signal(
        exact,
        FlowObservation(start + timedelta(days=201), 201.0, -402.0),
    )
    assert zero_mad.quality == "zero_mad"
    assert not zero_mad.trade_eligible


def test_mixed_participant_channels_fail_closed() -> None:
    rows = observations(201)
    mixed = rows[:200]
    mixed[-1] = FlowObservation(
        mixed[-1].date,
        mixed[-1].return_1d,
        mixed[-1].flow_share,
        "foreigner_scaled",
    )
    result = fit_latest_signal(mixed, rows[-1])
    assert result.quality == "mixed_channels"
    assert result.state == "unavailable"


def test_nonfinite_past_training_observation_fails_closed() -> None:
    rows = observations(201)
    invalid = rows[:200]
    invalid[-1] = FlowObservation(invalid[-1].date, invalid[-1].return_1d, float("nan"))
    result = fit_latest_signal(invalid, rows[-1])
    assert result.quality == "invalid_training_observation"
    assert result.state == "unavailable"
