from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from .model import FlowSignal


@dataclass(frozen=True)
class ProxyBar:
    date: date
    open: float


@dataclass(frozen=True)
class Trade:
    entry_date: date
    exit_date: date
    entry_price: float
    exit_price: float
    holding_sessions: int
    reason: str
    net_return: float


def run_long_cash(
    signals: list[FlowSignal],
    bars: list[ProxyBar],
    *,
    max_holding: int = 20,
    one_way_cost_bps: float = 10,
) -> list[Trade]:
    if [item.date for item in signals] != [item.date for item in bars]:
        raise ValueError("signal and proxy dates must align exactly")
    trades: list[Trade] = []
    pending_entry = False
    entry_index: int | None = None
    entry_price = 0.0
    for index, (signal, bar) in enumerate(zip(signals, bars, strict=True)):
        if pending_entry:
            entry_index = index
            entry_price = bar.open
            pending_entry = False
        if entry_index is not None:
            held = index - entry_index + 1
            recovery = signal.percentile is not None and signal.percentile >= 50
            timed_out = held >= max_holding
            if (recovery or timed_out) and index + 1 < len(bars):
                exit_bar = bars[index + 1]
                cost = 2 * one_way_cost_bps / 10_000
                trades.append(
                    Trade(
                        bars[entry_index].date,
                        exit_bar.date,
                        entry_price,
                        exit_bar.open,
                        held,
                        "recovery" if recovery else "max_holding",
                        exit_bar.open / entry_price - 1 - cost,
                    )
                )
                entry_index = None
        elif signal.trade_eligible and signal.state == "extreme_fear" and index + 1 < len(bars):
            pending_entry = True
    return trades
