from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any, Literal

import numpy as np
import pandas as pd

from .model import FlowSignal


@dataclass(frozen=True)
class ExtremeEvent:
    index: int
    signal: FlowSignal


def extreme_entries(signals: list[FlowSignal]) -> list[ExtremeEvent]:
    """Return first valid entry into an extreme regime without gap-created duplicates."""
    events: list[ExtremeEvent] = []
    previous_valid_state: str | None = None
    for index, signal in enumerate(signals):
        if not signal.trade_eligible:
            continue
        state = signal.state
        if state in {"extreme_fear", "extreme_greed"} and state != previous_valid_state:
            events.append(ExtremeEvent(index, signal))
        previous_valid_state = state
    return events


def non_overlapping(events: list[ExtremeEvent], horizon: int = 20) -> list[ExtremeEvent]:
    if horizon <= 0:
        raise ValueError("event horizon must be positive")
    selected: list[ExtremeEvent] = []
    next_allowed = -1
    for event in events:
        if event.index >= next_allowed:
            selected.append(event)
            next_allowed = event.index + horizon + 1
    return selected


def event_returns(
    events: Iterable[ExtremeEvent],
    prices: pd.Series,
    *,
    horizons: tuple[int, ...] = (1, 5, 10, 20),
    benchmark_prices: pd.Series | None = None,
) -> list[dict[str, Any]]:
    if not horizons or any(horizon <= 0 for horizon in horizons):
        raise ValueError("event horizons must be positive")
    clean = pd.to_numeric(prices, errors="coerce").dropna()
    clean.index = pd.to_datetime(clean.index).tz_localize(None).normalize()
    clean = clean[~clean.index.duplicated(keep=False)].sort_index()
    date_to_position = {timestamp.date(): index for index, timestamp in enumerate(clean.index)}
    benchmark = None
    benchmark_positions: dict[Any, int] = {}
    if benchmark_prices is not None:
        benchmark = pd.to_numeric(benchmark_prices, errors="coerce").dropna()
        benchmark.index = pd.to_datetime(benchmark.index).tz_localize(None).normalize()
        benchmark = benchmark[~benchmark.index.duplicated(keep=False)].sort_index()
        benchmark_positions = {
            timestamp.date(): index for index, timestamp in enumerate(benchmark.index)
        }
    rows: list[dict[str, Any]] = []
    for event in events:
        position = date_to_position.get(event.signal.date)
        if position is None:
            continue
        row: dict[str, Any] = {
            "date": event.signal.date.isoformat(),
            "state": event.signal.state,
            "percentile": event.signal.percentile,
        }
        for horizon in horizons:
            key = f"return{horizon}d"
            if position + horizon < len(clean):
                row[key] = float(clean.iloc[position + horizon] / clean.iloc[position] - 1)
            else:
                row[key] = None
            if benchmark is not None:
                benchmark_key = f"benchmarkReturn{horizon}d"
                excess_key = f"excessReturn{horizon}d"
                benchmark_position = benchmark_positions.get(event.signal.date)
                if (
                    benchmark_position is not None
                    and benchmark_position + horizon < len(benchmark)
                    and row[key] is not None
                ):
                    benchmark_return = float(
                        benchmark.iloc[benchmark_position + horizon]
                        / benchmark.iloc[benchmark_position]
                        - 1
                    )
                    row[benchmark_key] = benchmark_return
                    row[excess_key] = float(row[key] - benchmark_return)
                else:
                    row[benchmark_key] = None
                    row[excess_key] = None
        rows.append(row)
    return rows


def unconditional_forward_return_benchmarks(
    prices: pd.Series, *, horizons: tuple[int, ...] = (1, 5, 10, 20)
) -> dict[int, float | None]:
    """Return the all-session mean forward return for transparent drift controls."""
    clean = pd.to_numeric(prices, errors="coerce").dropna()
    clean.index = pd.to_datetime(clean.index).tz_localize(None).normalize()
    clean = clean[~clean.index.duplicated(keep=False)].sort_index()
    benchmarks: dict[int, float | None] = {}
    for horizon in horizons:
        if horizon <= 0:
            raise ValueError("event horizons must be positive")
        values = clean.shift(-horizon) / clean - 1
        values = values.replace([np.inf, -np.inf], np.nan).dropna()
        benchmarks[horizon] = float(values.mean()) if not values.empty else None
    return benchmarks


def summarize_event_returns(
    rows: list[dict[str, Any]],
    *,
    horizons: tuple[int, ...] = (1, 5, 10, 20),
    bootstrap_samples: int = 10_000,
    seed: int = 20260715,
    benchmark_returns: Mapping[int, float | None] | None = None,
    bootstrap_method: Literal["iid", "moving_block"] = "iid",
    block_length: int | None = None,
) -> list[dict[str, Any]]:
    if not horizons or any(horizon <= 0 for horizon in horizons):
        raise ValueError("event horizons must be positive")
    if bootstrap_samples <= 0:
        raise ValueError("bootstrap_samples must be positive")
    if bootstrap_method not in {"iid", "moving_block"}:
        raise ValueError(f"unsupported bootstrap method: {bootstrap_method}")
    if block_length is not None and block_length <= 0:
        raise ValueError("block_length must be positive")
    rng = np.random.default_rng(seed)
    summary: list[dict[str, Any]] = []
    states = ("extreme_fear", "extreme_greed")
    for state in states:
        state_rows = sorted(
            (row for row in rows if row["state"] == state), key=lambda row: row["date"]
        )
        for horizon in horizons:
            key = f"return{horizon}d"
            values = np.asarray(
                [
                    row[key]
                    for row in state_rows
                    if row.get(key) is not None and np.isfinite(row[key])
                ],
                dtype=float,
            )
            effective_block = _effective_block_length(len(values), block_length)
            if values.size == 0:
                summary.append(
                    {
                        "state": state,
                        "horizon": horizon,
                        "eventCount": 0,
                        "mean": None,
                        "median": None,
                        "positiveRate": None,
                        "meanCi95": [None, None],
                        "benchmarkMean": None,
                        "meanExcessReturn": None,
                        "meanExcessReturnCi95": [None, None],
                        "meanExcessReturnCi95BenchmarkTreatment": "unavailable",
                        "bootstrapMethod": bootstrap_method,
                        "bootstrapBlockLength": None,
                        "smallSample": True,
                    }
                )
                continue
            ci = _bootstrap_mean_ci(
                values,
                rng,
                samples=bootstrap_samples,
                method=bootstrap_method,
                block_length=effective_block,
            )
            row_benchmarks = np.asarray(
                [
                    row[f"benchmarkReturn{horizon}d"]
                    for row in state_rows
                    if row.get(key) is not None
                    and np.isfinite(row[key])
                    and row.get(f"benchmarkReturn{horizon}d") is not None
                    and np.isfinite(row[f"benchmarkReturn{horizon}d"])
                ],
                dtype=float,
            )
            external_benchmark = (
                benchmark_returns.get(horizon) if benchmark_returns is not None else None
            )
            if external_benchmark is not None and np.isfinite(external_benchmark):
                benchmark_mean = float(external_benchmark)
                excess_values = values - benchmark_mean
                excess_ci_benchmark_treatment = "fixed_external_mean"
            elif row_benchmarks.size == values.size:
                benchmark_mean = float(row_benchmarks.mean())
                excess_values = values - row_benchmarks
                excess_ci_benchmark_treatment = "paired_event_returns"
            else:
                benchmark_mean = None
                excess_values = np.asarray([], dtype=float)
                excess_ci_benchmark_treatment = "unavailable"
            excess_ci = (
                _bootstrap_mean_ci(
                    excess_values,
                    rng,
                    samples=bootstrap_samples,
                    method=bootstrap_method,
                    block_length=_effective_block_length(len(excess_values), block_length),
                )
                if excess_values.size
                else (None, None)
            )
            summary.append(
                {
                    "state": state,
                    "horizon": horizon,
                    "eventCount": int(values.size),
                    "mean": float(values.mean()),
                    "median": float(np.median(values)),
                    "positiveRate": float((values > 0).mean()),
                    "meanCi95": [float(ci[0]), float(ci[1])],
                    "benchmarkMean": benchmark_mean,
                    "meanExcessReturn": float(excess_values.mean()) if excess_values.size else None,
                    "meanExcessReturnCi95": [
                        float(excess_ci[0]) if excess_ci[0] is not None else None,
                        float(excess_ci[1]) if excess_ci[1] is not None else None,
                    ],
                    "meanExcessReturnCi95BenchmarkTreatment": (excess_ci_benchmark_treatment),
                    "bootstrapMethod": bootstrap_method,
                    "bootstrapBlockLength": effective_block
                    if bootstrap_method == "moving_block"
                    else None,
                    "smallSample": bool(values.size < 20),
                }
            )
    return summary


def _effective_block_length(sample_size: int, requested: int | None) -> int:
    if sample_size <= 1:
        return 1
    default = max(2, int(round(sample_size**0.5)))
    return min(sample_size, requested or default)


def _bootstrap_mean_ci(
    values: np.ndarray,
    rng: np.random.Generator,
    *,
    samples: int,
    method: str,
    block_length: int,
) -> tuple[float, float]:
    if values.size == 1:
        value = float(values[0])
        return value, value
    if method == "iid":
        sampled = rng.choice(values, size=(samples, len(values)), replace=True)
        means = sampled.mean(axis=1)
    else:
        block_count = int(np.ceil(len(values) / block_length))
        starts = rng.integers(0, len(values), size=(samples, block_count))
        offsets = np.arange(block_length)
        indices = (starts[..., None] + offsets) % len(values)
        sampled = values[indices.reshape(samples, -1)[:, : len(values)]]
        means = sampled.mean(axis=1)
    ci = np.quantile(means, [0.025, 0.975])
    return float(ci[0]), float(ci[1])
