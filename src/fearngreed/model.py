from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from math import isfinite
from statistics import median


@dataclass(frozen=True)
class FlowObservation:
    date: date
    return_1d: float
    flow_share: float


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


def fit_latest_signal(
    training: list[FlowObservation],
    current: FlowObservation,
    *,
    min_observations: int = 200,
) -> FlowSignal:
    complete = [
        item
        for item in training
        if isfinite(item.return_1d) and isfinite(item.flow_share) and item.date < current.date
    ]
    if len({item.date for item in complete}) != len(complete):
        return _unavailable(current.date, len(complete), "duplicate_dates")
    if len(complete) < min_observations:
        return _unavailable(current.date, len(complete), "insufficient_history")
    try:
        alpha, beta, r2, residuals = _ols(
            [item.return_1d for item in complete], [item.flow_share for item in complete]
        )
    except ValueError:
        return _unavailable(current.date, len(complete), "invalid_regression")
    residual = current.flow_share - (alpha + beta * current.return_1d)
    center = median(residuals)
    mad = median(abs(value - center) for value in residuals)
    if mad == 0:
        return FlowSignal(
            current.date,
            alpha,
            beta,
            r2,
            residual,
            None,
            None,
            "unavailable",
            "zero_mad",
            len(complete),
            False,
        )
    percentile = 100 * sum(value <= residual for value in residuals) / len(residuals)
    state = classify_percentile(percentile)
    quality = "ok" if beta < 0 and r2 >= 0.20 else "low_model_fit"
    return FlowSignal(
        current.date,
        alpha,
        beta,
        r2,
        residual,
        (residual - center) / (1.4826 * mad),
        percentile,
        state,
        quality,
        len(complete),
        quality == "ok",
    )


def rolling_signals(
    observations: list[FlowObservation], *, window: int = 252, min_observations: int = 200
) -> list[FlowSignal]:
    ordered = sorted(observations, key=lambda item: item.date)
    if len({item.date for item in ordered}) != len(ordered):
        raise ValueError("duplicate observation dates")
    return [
        fit_latest_signal(
            ordered[max(0, index - window) : index],
            current,
            min_observations=min_observations,
        )
        for index, current in enumerate(ordered)
    ]


def _unavailable(day: date, count: int, quality: str) -> FlowSignal:
    return FlowSignal(day, None, None, None, None, None, None, "unavailable", quality, count, False)
