from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from math import isfinite
from statistics import median
from typing import Literal


@dataclass(frozen=True)
class FlowObservation:
    date: date
    return_1d: float
    flow_share: float
    # The model is participant-agnostic.  New channels (for example foreigner
    # or institutional flow) must be supplied explicitly rather than inferred.
    channel: str = "individual"


@dataclass(frozen=True)
class FlowSignal:
    date: date
    alpha: float | None
    beta: float | None
    rolling_r2: float | None
    residual: float | None
    residual_z: float | None
    percentile: float | None
    state: str
    quality: str
    training_count: int
    trade_eligible: bool
    expected_flow: float | None = None
    fit_method: str = "ols"
    fit_score: float | None = None
    channel: str = "individual"


@dataclass(frozen=True)
class RegressionFit:
    """A deterministic in-sample fit used by a past-only signal calculation."""

    alpha: float
    beta: float
    r2: float
    fit_score: float
    residuals: list[float]
    method: str


def classify_percentile(value: float) -> str:
    if value <= 5:
        return "extreme_fear"
    if value <= 20:
        return "fear"
    if value < 80:
        return "neutral"
    if value < 95:
        return "greed"
    return "extreme_greed"


def _ols(xs: list[float], ys: list[float]) -> tuple[float, float, float, list[float]]:
    x_bar = sum(xs) / len(xs)
    y_bar = sum(ys) / len(ys)
    ss_x = sum((x - x_bar) ** 2 for x in xs)
    if ss_x == 0:
        raise ValueError("return variance is zero")
    beta = sum((x - x_bar) * (y - y_bar) for x, y in zip(xs, ys, strict=True)) / ss_x
    alpha = y_bar - beta * x_bar
    residuals = [y - (alpha + beta * x) for x, y in zip(xs, ys, strict=True)]
    ss_total = sum((y - y_bar) ** 2 for y in ys)
    r2 = 0.0 if ss_total == 0 else 1 - sum(value**2 for value in residuals) / ss_total
    return alpha, beta, r2, residuals


def fit_regression(
    xs: list[float],
    ys: list[float],
    *,
    method: Literal["ols", "huber"] = "ols",
    huber_tuning: float = 1.345,
    max_iterations: int = 50,
    tolerance: float = 1e-10,
) -> RegressionFit:
    """Fit the OLS baseline or a deterministic Huber IRLS alternative.

    ``r2`` is always the ordinary, unweighted coefficient of determination so
    the two methods remain comparable and is the score used by the signal
    quality gate. ``fit_score`` additionally exposes ordinary R-squared for OLS
    and weighted R-squared for Huber as a diagnostic. No data outside
    ``xs``/``ys`` is consulted.
    """
    if len(xs) != len(ys) or len(xs) < 2:
        raise ValueError("regression requires equal-length samples")
    if method not in {"ols", "huber"}:
        raise ValueError(f"unsupported regression method: {method}")
    if huber_tuning <= 0 or max_iterations <= 0 or tolerance <= 0:
        raise ValueError("invalid robust regression parameters")

    alpha, beta, r2, residuals = _ols(xs, ys)
    if method == "ols":
        return RegressionFit(alpha, beta, r2, r2, residuals, method)

    weights = [1.0] * len(xs)
    for _ in range(max_iterations):
        center = median(residuals)
        mad = median(abs(value - center) for value in residuals)
        scale = 1.4826 * mad
        if not isfinite(scale) or scale <= 0:
            break
        cutoff = huber_tuning * scale
        weights = [
            1.0 if abs(value - center) <= cutoff else cutoff / abs(value - center)
            for value in residuals
        ]
        new_alpha, new_beta = _weighted_line(xs, ys, weights)
        converged = max(abs(new_alpha - alpha), abs(new_beta - beta)) <= tolerance * (
            1 + max(abs(alpha), abs(beta))
        )
        alpha, beta = new_alpha, new_beta
        residuals = [y - (alpha + beta * x) for x, y in zip(xs, ys, strict=True)]
        if converged:
            break

    r2 = _unweighted_r2(ys, residuals)
    fit_score = _weighted_r2(ys, residuals, weights)
    return RegressionFit(alpha, beta, r2, fit_score, residuals, method)


def _weighted_line(xs: list[float], ys: list[float], weights: list[float]) -> tuple[float, float]:
    weight_sum = sum(weights)
    if weight_sum <= 0:
        raise ValueError("regression weights sum to zero")
    x_bar = sum(weight * x for weight, x in zip(weights, xs, strict=True)) / weight_sum
    y_bar = sum(weight * y for weight, y in zip(weights, ys, strict=True)) / weight_sum
    ss_x = sum(weight * (x - x_bar) ** 2 for weight, x in zip(weights, xs, strict=True))
    if ss_x == 0:
        raise ValueError("return variance is zero")
    beta = (
        sum(
            weight * (x - x_bar) * (y - y_bar) for weight, x, y in zip(weights, xs, ys, strict=True)
        )
        / ss_x
    )
    return y_bar - beta * x_bar, beta


def _unweighted_r2(ys: list[float], residuals: list[float]) -> float:
    y_bar = sum(ys) / len(ys)
    ss_total = sum((y - y_bar) ** 2 for y in ys)
    return 0.0 if ss_total == 0 else 1 - sum(value**2 for value in residuals) / ss_total


def _weighted_r2(ys: list[float], residuals: list[float], weights: list[float]) -> float:
    weight_sum = sum(weights)
    y_bar = sum(weight * y for weight, y in zip(weights, ys, strict=True)) / weight_sum
    ss_total = sum(weight * (y - y_bar) ** 2 for weight, y in zip(weights, ys, strict=True))
    if ss_total == 0:
        return 0.0
    return (
        1
        - sum(weight * residual**2 for weight, residual in zip(weights, residuals, strict=True))
        / ss_total
    )


def fit_latest_signal(
    training: list[FlowObservation],
    current: FlowObservation,
    *,
    min_observations: int = 200,
    fit_method: Literal["ols", "huber"] = "ols",
    minimum_fit_score: float = 0.20,
) -> FlowSignal:
    if not isfinite(current.return_1d) or not isfinite(current.flow_share):
        return _unavailable(
            current.date, 0, "invalid_current_observation", fit_method, current.channel
        )
    past_training = [item for item in training if item.date < current.date]
    if any(not isfinite(item.return_1d) or not isfinite(item.flow_share) for item in past_training):
        return _unavailable(
            current.date,
            len(past_training),
            "invalid_training_observation",
            fit_method,
            current.channel,
        )
    complete = [
        item for item in past_training if isfinite(item.return_1d) and isfinite(item.flow_share)
    ]
    if any(item.channel != current.channel for item in complete):
        return _unavailable(
            current.date, len(complete), "mixed_channels", fit_method, current.channel
        )
    if len({item.date for item in complete}) != len(complete):
        return _unavailable(
            current.date, len(complete), "duplicate_dates", fit_method, current.channel
        )
    if len(complete) < min_observations:
        return _unavailable(
            current.date, len(complete), "insufficient_history", fit_method, current.channel
        )
    try:
        fit = fit_regression(
            [item.return_1d for item in complete],
            [item.flow_share for item in complete],
            method=fit_method,
        )
    except ValueError:
        return _unavailable(
            current.date, len(complete), "invalid_regression", fit_method, current.channel
        )
    expected = fit.alpha + fit.beta * current.return_1d
    residual = current.flow_share - expected
    residuals = fit.residuals
    center = median(residuals)
    mad = median(abs(value - center) for value in residuals)
    if mad == 0:
        return FlowSignal(
            current.date,
            fit.alpha,
            fit.beta,
            fit.r2,
            residual,
            None,
            None,
            "unavailable",
            "zero_mad",
            len(complete),
            False,
            expected,
            fit.method,
            fit.fit_score,
            current.channel,
        )
    percentile = 100 * sum(value <= residual for value in residuals) / len(residuals)
    state = classify_percentile(percentile)
    quality = "ok" if fit.beta < 0 and fit.r2 >= minimum_fit_score else "low_model_fit"
    return FlowSignal(
        current.date,
        fit.alpha,
        fit.beta,
        fit.r2,
        residual,
        (residual - center) / (1.4826 * mad),
        percentile,
        state,
        quality,
        len(complete),
        quality == "ok",
        expected,
        fit.method,
        fit.fit_score,
        current.channel,
    )


def rolling_signals(
    observations: list[FlowObservation],
    *,
    window: int = 252,
    min_observations: int = 200,
    fit_method: Literal["ols", "huber"] = "ols",
    minimum_fit_score: float = 0.20,
) -> list[FlowSignal]:
    ordered = sorted(observations, key=lambda item: item.date)
    if len({item.date for item in ordered}) != len(ordered):
        raise ValueError("duplicate observation dates")
    return [
        fit_latest_signal(
            ordered[max(0, index - window) : index],
            current,
            min_observations=min_observations,
            fit_method=fit_method,
            minimum_fit_score=minimum_fit_score,
        )
        for index, current in enumerate(ordered)
    ]


def _unavailable(
    day: date, count: int, quality: str, fit_method: str = "ols", channel: str = "individual"
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
        quality,
        count,
        False,
        None,
        fit_method,
        None,
        channel,
    )
