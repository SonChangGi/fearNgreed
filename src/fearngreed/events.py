from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

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
) -> list[dict[str, Any]]:
    clean = pd.to_numeric(prices, errors="coerce").dropna()
    date_to_position = {timestamp.date(): index for index, timestamp in enumerate(clean.index)}
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
        rows.append(row)
    return rows


def summarize_event_returns(
    rows: list[dict[str, Any]],
    *,
    horizons: tuple[int, ...] = (1, 5, 10, 20),
    bootstrap_samples: int = 10_000,
    seed: int = 20260715,
) -> list[dict[str, Any]]:
    rng = np.random.default_rng(seed)
    summary: list[dict[str, Any]] = []
    states = ("extreme_fear", "extreme_greed")
    for state in states:
        state_rows = [row for row in rows if row["state"] == state]
        for horizon in horizons:
            key = f"return{horizon}d"
            values = np.asarray(
                [row[key] for row in state_rows if row.get(key) is not None], dtype=float
            )
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
                        "smallSample": True,
                    }
                )
                continue
            sampled = rng.choice(values, size=(bootstrap_samples, len(values)), replace=True)
            ci = np.quantile(sampled.mean(axis=1), [0.025, 0.975])
            summary.append(
                {
                    "state": state,
                    "horizon": horizon,
                    "eventCount": int(values.size),
                    "mean": float(values.mean()),
                    "median": float(np.median(values)),
                    "positiveRate": float((values > 0).mean()),
                    "meanCi95": [float(ci[0]), float(ci[1])],
                    "smallSample": bool(values.size < 20),
                }
            )
    return summary
