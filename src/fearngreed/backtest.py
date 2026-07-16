from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date
from math import sqrt
from typing import Any

import numpy as np
import pandas as pd

from .model import FlowSignal


@dataclass(frozen=True)
class ProxyBar:
    date: date
    open: float
    close: float | None = None


@dataclass(frozen=True)
class Trade:
    entry_date: date
    exit_date: date
    entry_price: float
    exit_price: float
    holding_sessions: int
    reason: str
    net_return: float


@dataclass(frozen=True)
class BacktestResult:
    ticker: str
    cost_bps: float
    trades: list[Trade]
    equity: pd.Series
    buy_hold_equity: pd.Series
    exposure: pd.Series
    open_position: bool
    pending_action: str | None
    pending_reason: str | None
    metrics: dict[str, Any]


def run_long_cash(
    signals: list[FlowSignal],
    bars: list[ProxyBar],
    *,
    max_holding: int = 20,
    one_way_cost_bps: float = 10,
) -> list[Trade]:
    """Compatibility wrapper for the calculation contract's closed-trade list."""
    frame = pd.DataFrame(
        {
            "open": [bar.open for bar in bars],
            "close": [bar.close if bar.close is not None else bar.open for bar in bars],
        },
        index=pd.to_datetime([bar.date for bar in bars]),
    )
    return run_backtest(
        signals,
        frame,
        ticker="proxy",
        max_holding=max_holding,
        one_way_cost_bps=one_way_cost_bps,
    ).trades


def run_backtest(
    signals: list[FlowSignal],
    bars: pd.DataFrame,
    *,
    ticker: str,
    max_holding: int = 20,
    one_way_cost_bps: float = 10,
) -> BacktestResult:
    required = {"open", "close"}
    if not required.issubset(bars.columns):
        raise ValueError("bars require adjusted open and close")
    clean = bars[["open", "close"]].copy().sort_index()
    clean.index = pd.to_datetime(clean.index).tz_localize(None).normalize()
    if clean.index.duplicated().any() or (clean <= 0).any().any():
        raise ValueError("bars must have unique dates and positive prices")
    signal_by_date = {pd.Timestamp(signal.date): signal for signal in signals}
    aligned_signals = [signal_by_date.get(timestamp) for timestamp in clean.index]
    entry_dates = _extreme_fear_entry_dates(signals)
    one_way_cost = one_way_cost_bps / 10_000
    cash = 1.0
    shares = 0.0
    entry_index: int | None = None
    entry_price = 0.0
    entry_equity = 0.0
    pending_entry = False
    pending_exit_reason: str | None = None
    trades: list[Trade] = []
    equity_values: list[float] = []
    exposure_values: list[float] = []

    for index, (timestamp, row) in enumerate(clean.iterrows()):
        if pending_exit_reason is not None and entry_index is not None:
            gross = shares * float(row["open"])
            cash = gross * (1 - one_way_cost)
            held = index - entry_index
            trades.append(
                Trade(
                    entry_date=clean.index[entry_index].date(),
                    exit_date=timestamp.date(),
                    entry_price=entry_price,
                    exit_price=float(row["open"]),
                    holding_sessions=held,
                    reason=pending_exit_reason,
                    net_return=cash / entry_equity - 1,
                )
            )
            shares = 0.0
            entry_index = None
            pending_exit_reason = None

        if pending_entry and entry_index is None:
            entry_equity = cash
            entry_price = float(row["open"])
            shares = cash * (1 - one_way_cost) / entry_price
            cash = 0.0
            entry_index = index
            pending_entry = False

        signal = aligned_signals[index]
        if entry_index is not None:
            held_sessions = index - entry_index + 1
            recovery = (
                signal is not None and signal.percentile is not None and signal.percentile >= 50
            )
            if recovery:
                pending_exit_reason = "recovery"
            elif held_sessions >= max_holding:
                pending_exit_reason = "max_holding"
        elif signal is not None and signal.date in entry_dates:
            pending_entry = True

        equity_values.append(cash if entry_index is None else shares * float(row["close"]))
        exposure_values.append(0.0 if entry_index is None else 1.0)

    equity = pd.Series(equity_values, index=clean.index, name="equity")
    buy_hold_equity = clean["close"] / float(clean["open"].iloc[0])
    buy_hold_equity.name = "buy_hold_equity"
    exposure = pd.Series(exposure_values, index=clean.index, name="exposure")
    metrics = calculate_metrics(equity, buy_hold_equity, trades, exposure, clean)
    pending_action: str | None = None
    pending_reason: str | None = None
    if pending_exit_reason is not None and entry_index is not None:
        pending_action = "exit_next_open"
        pending_reason = pending_exit_reason
    elif pending_entry and entry_index is None:
        pending_action = "enter_next_open"
        pending_reason = "extreme_fear_entry"

    return BacktestResult(
        ticker=ticker,
        cost_bps=one_way_cost_bps,
        trades=trades,
        equity=equity,
        buy_hold_equity=buy_hold_equity,
        exposure=exposure,
        open_position=entry_index is not None,
        pending_action=pending_action,
        pending_reason=pending_reason,
        metrics=metrics,
    )


def _extreme_fear_entry_dates(signals: list[FlowSignal]) -> set[date]:
    entries: set[date] = set()
    previous_valid_state: str | None = None
    for signal in sorted(signals, key=lambda item: item.date):
        if not signal.trade_eligible:
            continue
        if signal.state == "extreme_fear" and signal.state != previous_valid_state:
            entries.add(signal.date)
        previous_valid_state = signal.state
    return entries


def calculate_metrics(
    equity: pd.Series,
    buy_hold_equity: pd.Series,
    trades: list[Trade],
    exposure: pd.Series,
    bars: pd.DataFrame,
) -> dict[str, Any]:
    if len(equity) < 2:
        raise ValueError("at least two bars are required")
    returns = equity.pct_change().fillna(0)
    years = (equity.index[-1] - equity.index[0]).days / 365.2425
    cagr = float(equity.iloc[-1] ** (1 / years) - 1) if years > 0 else None
    volatility = float(returns.std(ddof=1) * sqrt(252))
    sharpe = float(returns.mean() / returns.std(ddof=1) * sqrt(252)) if returns.std() else None
    drawdown = equity / equity.cummax() - 1
    buy_hold_drawdown = buy_hold_equity / buy_hold_equity.cummax() - 1
    buy_hold = float(bars["close"].iloc[-1] / bars["open"].iloc[0] - 1)
    wins = [trade.net_return > 0 for trade in trades]
    return {
        "start": equity.index[0].date().isoformat(),
        "end": equity.index[-1].date().isoformat(),
        "totalReturn": float(equity.iloc[-1] - 1),
        "cagr": cagr,
        "volatility": volatility,
        "sharpe": sharpe,
        "maxDrawdown": float(drawdown.min()),
        "winRate": float(np.mean(wins)) if wins else None,
        "exposure": float(exposure.mean()),
        "turnover": float(len(trades) * 2 + (1 if exposure.iloc[-1] else 0)) / years
        if years > 0
        else None,
        "tradeCount": len(trades),
        "averageHoldingSessions": float(np.mean([trade.holding_sessions for trade in trades]))
        if trades
        else None,
        "buyAndHoldReturn": buy_hold,
        "buyAndHoldMaxDrawdown": float(buy_hold_drawdown.min()),
    }


def result_to_public(result: BacktestResult) -> dict[str, Any]:
    equity_drawdown = result.equity / result.equity.cummax() - 1
    buy_hold_drawdown = result.buy_hold_equity / result.buy_hold_equity.cummax() - 1
    return {
        "ticker": result.ticker,
        "oneWayCostBps": result.cost_bps,
        "openPosition": result.open_position,
        "pendingAction": result.pending_action,
        "pendingReason": result.pending_reason,
        "metrics": _public_value(result.metrics),
        "trades": [
            _public_value({
                **asdict(trade),
                "entry_date": trade.entry_date.isoformat(),
                "exit_date": trade.exit_date.isoformat(),
            })
            for trade in result.trades
        ],
        "equity": [
            _public_value({
                "date": timestamp.date().isoformat(),
                "value": float(value),
                "buyHoldValue": float(result.buy_hold_equity.loc[timestamp]),
                "drawdown": float(equity_drawdown.loc[timestamp]),
                "buyHoldDrawdown": float(buy_hold_drawdown.loc[timestamp]),
            })
            for timestamp, value in result.equity.items()
        ],
    }


def _public_value(value: Any) -> Any:
    if isinstance(value, float):
        return round(value, 10)
    if isinstance(value, dict):
        return {key: _public_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_public_value(item) for item in value]
    return value
