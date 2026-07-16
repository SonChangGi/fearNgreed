from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date
from math import isfinite, sqrt
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
    status: str = "ok"
    unavailable_reason: str | None = None


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
    if max_holding <= 0:
        raise ValueError("max_holding must be positive")
    if not isfinite(one_way_cost_bps) or one_way_cost_bps < 0:
        raise ValueError("one_way_cost_bps cannot be negative")
    required = {"open", "close"}
    if not required.issubset(bars.columns):
        raise ValueError("bars require adjusted open and close")
    clean = bars[["open", "close"]].copy().sort_index()
    clean.index = pd.to_datetime(clean.index).tz_localize(None).normalize()
    if len(clean) < 2:
        raise ValueError("at least two bars are required")
    if (
        clean.index.isna().any()
        or clean.index.duplicated().any()
        or not np.isfinite(clean.to_numpy(dtype=float)).all()
        or (clean <= 0).any().any()
    ):
        raise ValueError("bars must have unique dates and finite positive prices")
    if len({signal.date for signal in signals}) != len(signals):
        raise ValueError("signals must have unique dates")
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


def run_backtest_safe(
    signals: list[FlowSignal],
    bars: pd.DataFrame,
    *,
    ticker: str,
    max_holding: int = 20,
    one_way_cost_bps: float = 10,
) -> BacktestResult:
    """Fail closed for publication paths instead of presenting false cash results."""
    try:
        return run_backtest(
            signals,
            bars,
            ticker=ticker,
            max_holding=max_holding,
            one_way_cost_bps=one_way_cost_bps,
        )
    except (AttributeError, TypeError, ValueError) as error:
        return _unavailable_result(ticker, one_way_cost_bps, str(error))


def run_cost_sensitivity(
    signals: list[FlowSignal],
    bars: pd.DataFrame,
    *,
    ticker: str,
    cost_bps: tuple[float, ...] = (0, 5, 10, 20),
    max_holding: int = 20,
    fail_closed: bool = True,
) -> list[BacktestResult]:
    """Run the predeclared complete cost grid in deterministic order."""
    if (
        not cost_bps
        or len(set(cost_bps)) != len(cost_bps)
        or any(not isfinite(cost) or cost < 0 for cost in cost_bps)
    ):
        raise ValueError("cost_bps must contain unique non-negative values")
    runner = run_backtest_safe if fail_closed else run_backtest
    return [
        runner(
            signals,
            bars,
            ticker=ticker,
            max_holding=max_holding,
            one_way_cost_bps=cost,
        )
        for cost in cost_bps
    ]


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
    exposure_matched = build_exposure_matched_equity(bars, exposure)
    exposure_matched_drawdown = exposure_matched / exposure_matched.cummax() - 1
    risk_matched, risk_scale = build_risk_matched_equity(
        buy_hold_equity, target_volatility=volatility
    )
    risk_matched_drawdown = risk_matched / risk_matched.cummax() - 1
    notional_turnover = _annualized_notional_turnover(exposure, years)
    transaction_sides = float(len(trades) * 2 + (1 if exposure.iloc[-1] else 0))
    zero_cost_timing_return = float(exposure_matched.iloc[-1] - 1)
    zero_cost_timing_drawdown = float(exposure_matched_drawdown.min())
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
        # ``turnover`` remains as a compatibility alias.  New consumers should
        # use the explicitly named notional allocation change metric.
        "turnover": notional_turnover,
        "annualizedNotionalTurnover": notional_turnover,
        "transactionSidesPerYear": transaction_sides / years if years > 0 else None,
        "tradeCount": len(trades),
        "averageHoldingSessions": float(np.mean([trade.holding_sessions for trade in trades]))
        if trades
        else None,
        "buyAndHoldReturn": buy_hold,
        "buyAndHoldMaxDrawdown": float(buy_hold_drawdown.min()),
        "zeroCostTimingReturn": zero_cost_timing_return,
        "zeroCostTimingMaxDrawdown": zero_cost_timing_drawdown,
        "exposureMatchedReturn": zero_cost_timing_return,
        "exposureMatchedMaxDrawdown": zero_cost_timing_drawdown,
        "excessReturnVsExposureMatched": float(equity.iloc[-1] - exposure_matched.iloc[-1]),
        "excessReturnVsZeroCostTiming": float(equity.iloc[-1] - exposure_matched.iloc[-1]),
        "riskMatchedBuyHoldReturn": float(risk_matched.iloc[-1] - 1),
        "riskMatchedBuyHoldMaxDrawdown": float(risk_matched_drawdown.min()),
        "riskMatchedScale": risk_scale,
    }


def build_exposure_matched_equity(bars: pd.DataFrame, exposure: pd.Series) -> pd.Series:
    """Replay the strategy's exact open/close holding windows without costs."""
    clean_bars, weights = bars[["open", "close"]].align(exposure, join="inner", axis=0)
    if clean_bars.empty:
        return pd.Series(dtype=float, name="exposure_matched_equity")
    weights = weights.astype(float).clip(0, 1)
    values: list[float] = []
    capital = 1.0
    for index in range(len(clean_bars)):
        current_exposure = bool(weights.iloc[index])
        previous_exposure = bool(weights.iloc[index - 1]) if index else False
        open_price = float(clean_bars.iloc[index]["open"])
        close_price = float(clean_bars.iloc[index]["close"])
        previous_close = float(clean_bars.iloc[index - 1]["close"]) if index else open_price
        if current_exposure and not previous_exposure:
            capital *= close_price / open_price
        elif current_exposure and previous_exposure:
            capital *= close_price / previous_close
        elif previous_exposure and not current_exposure:
            capital *= open_price / previous_close
        values.append(capital)
    matched = pd.Series(values, index=clean_bars.index)
    matched.name = "exposure_matched_equity"
    return matched


def build_risk_matched_equity(
    buy_hold_equity: pd.Series,
    *,
    target_volatility: float,
    max_leverage: float = 1.0,
) -> tuple[pd.Series, float | None]:
    """Scale buy-and-hold to strategy volatility with a zero-return cash sleeve."""
    if not isfinite(target_volatility):
        matched = pd.Series(1.0, index=buy_hold_equity.index, name="risk_matched_equity")
        return matched, None
    if target_volatility < 0 or not isfinite(max_leverage) or max_leverage <= 0:
        raise ValueError("risk matching parameters must be non-negative")
    daily_returns = buy_hold_equity.pct_change().fillna(0.0)
    benchmark_volatility = float(daily_returns.std(ddof=1) * sqrt(252))
    if not isfinite(benchmark_volatility) or benchmark_volatility <= 0:
        matched = pd.Series(1.0, index=buy_hold_equity.index, name="risk_matched_equity")
        return matched, None
    scale = min(max_leverage, target_volatility / benchmark_volatility)
    matched = (1 + daily_returns * scale).cumprod()
    matched.name = "risk_matched_equity"
    return matched, float(scale)


def _annualized_notional_turnover(exposure: pd.Series, years: float) -> float | None:
    if years <= 0 or exposure.empty:
        return None
    weights = pd.to_numeric(exposure, errors="coerce")
    if weights.isna().any():
        return None
    changes = weights.diff().abs()
    changes.iloc[0] = abs(float(weights.iloc[0]))
    return float(changes.sum() / years)


def result_to_public(result: BacktestResult) -> dict[str, Any]:
    equity_drawdown = result.equity / result.equity.cummax() - 1
    buy_hold_drawdown = result.buy_hold_equity / result.buy_hold_equity.cummax() - 1
    return {
        "ticker": result.ticker,
        "oneWayCostBps": result.cost_bps,
        "openPosition": result.open_position,
        "pendingAction": result.pending_action,
        "pendingReason": result.pending_reason,
        "status": result.status,
        "unavailableReason": result.unavailable_reason,
        "metrics": _public_value(result.metrics),
        "trades": [
            _public_value(
                {
                    **asdict(trade),
                    "entry_date": trade.entry_date.isoformat(),
                    "exit_date": trade.exit_date.isoformat(),
                }
            )
            for trade in result.trades
        ],
        "equity": [
            _public_value(
                {
                    "date": timestamp.date().isoformat(),
                    "value": float(value),
                    "buyHoldValue": float(result.buy_hold_equity.loc[timestamp]),
                    "drawdown": float(equity_drawdown.loc[timestamp]),
                    "buyHoldDrawdown": float(buy_hold_drawdown.loc[timestamp]),
                }
            )
            for timestamp, value in result.equity.items()
        ],
    }


def _unavailable_result(ticker: str, cost_bps: float, reason: str) -> BacktestResult:
    empty = pd.Series(dtype=float)
    return BacktestResult(
        ticker=ticker,
        cost_bps=cost_bps,
        trades=[],
        equity=empty.rename("equity"),
        buy_hold_equity=empty.rename("buy_hold_equity"),
        exposure=empty.rename("exposure"),
        open_position=False,
        pending_action=None,
        pending_reason=None,
        metrics={"state": "unavailable", "reason": reason},
        status="unavailable",
        unavailable_reason=reason,
    )


def _public_value(value: Any) -> Any:
    if isinstance(value, float):
        return round(value, 10)
    if isinstance(value, dict):
        return {key: _public_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_public_value(item) for item in value]
    return value
