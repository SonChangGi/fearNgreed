from __future__ import annotations

from dataclasses import dataclass

from .model import FlowSignal


@dataclass(frozen=True)
class ExtremeEvent:
    index: int
    signal: FlowSignal


def extreme_entries(signals: list[FlowSignal]) -> list[ExtremeEvent]:
    events: list[ExtremeEvent] = []
    previous: str | None = None
    for index, signal in enumerate(signals):
        state = signal.state if signal.trade_eligible else None
        if state in {"extreme_fear", "extreme_greed"} and state != previous:
            events.append(ExtremeEvent(index, signal))
        previous = state
    return events


def non_overlapping(events: list[ExtremeEvent], horizon: int = 20) -> list[ExtremeEvent]:
    selected: list[ExtremeEvent] = []
    next_allowed = -1
    for event in events:
        if event.index >= next_allowed:
            selected.append(event)
            next_allowed = event.index + horizon + 1
    return selected
