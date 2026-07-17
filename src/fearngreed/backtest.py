from __future__ import annotations

from dataclasses import asdict, dataclass, field
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
    side: str
    entry_date: date
    exit_date: date
    entry_price: float
    exit_price: float
    holding_sessions: int
    reason: str
    gross_return: float
    transaction_cost: float
    borrow_cost: float
    net_return: float
    entry_signal_date: date | None = None
    entry_reason: str | None = None
    exit_signal_date: date | None = None
    exit_reason: str | None = None


@dataclass(frozen=True)
class StrategyAction:
    action_id: str
    signal_date: date
    execution_date: date
    action_type: str
    from_position: str
    to_position: str
    reason: str
    price: float
    transaction_cost_amount: float
    signal_phase: str = "after_close"
    execution_phase: str = "open"


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
    policy_id: str = "long_cash"
    position: str = "cash"
    pending_side: str | None = None
    open_trade: dict[str, Any] | None = None
    long_exit_percentile: float = 80
    short_exit_percentile: float = 20
    actions: list[StrategyAction] = field(default_factory=list)
    pending_signal_date: date | None = None


def run_long_cash(
    signals: list[FlowSignal],
    bars: list[ProxyBar],
    *,
    max_holding: int = 20,
    one_way_cost_bps: float = 10,
    long_exit_percentile: float = 80,
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
        long_exit_percentile=long_exit_percentile,
    ).trades


def run_backtest(
    signals: list[FlowSignal],
    bars: pd.DataFrame,
    *,
    ticker: str,
    max_holding: int = 20,
    one_way_cost_bps: float = 10,
    policy_id: str = "long_cash",
    long_exit_percentile: float = 80,
    short_exit_percentile: float = 20,
) -> BacktestResult:
    if max_holding <= 0:
        raise ValueError("max_holding must be positive")
    if not isfinite(one_way_cost_bps) or one_way_cost_bps < 0:
        raise ValueError("one_way_cost_bps cannot be negative")
    if policy_id not in {"long_cash", "long_short_cash"}:
        raise ValueError("unsupported policy_id")
    if not isfinite(long_exit_percentile) or not 5 < long_exit_percentile <= 100:
        raise ValueError("long_exit_percentile must be above 5 and at most 100")
    if not isfinite(short_exit_percentile) or not 0 <= short_exit_percentile < 95:
        raise ValueError("short_exit_percentile must be below 95 and at least 0")
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
    long_entry_dates = _extreme_entry_dates(signals, "extreme_fear")
    short_entry_dates = (
        _extreme_entry_dates(signals, "extreme_greed") if policy_id == "long_short_cash" else set()
    )
    one_way_cost = one_way_cost_bps / 10_000
    cash = 1.0
    units = 0.0
    position_side: str | None = None
    entry_index: int | None = None
    entry_price = 0.0
    entry_equity = 0.0
    entry_cost_amount = 0.0
    entry_signal_date: date | None = None
    entry_reason: str | None = None
    pending_entry_side: str | None = None
    pending_entry_signal_date: date | None = None
    pending_exit_reason: str | None = None
    pending_exit_signal_date: date | None = None
    pending_reversal_side: str | None = None
    trades: list[Trade] = []
    actions: list[StrategyAction] = []
    equity_values: list[float] = []
    exposure_values: list[float] = []
    transaction_cost_total = 0.0
    action_sequence = 0

    def entry_reason_for(side: str) -> str:
        return "extreme_fear_entry" if side == "long" else "extreme_greed_entry"

    def enter(
        side: str,
        index: int,
        open_price: float,
        *,
        signal_date: date,
        reason: str,
    ) -> float:
        nonlocal cash, units, position_side, entry_index, entry_price
        nonlocal entry_equity, entry_cost_amount, entry_signal_date, entry_reason
        nonlocal transaction_cost_total
        if side not in {"long", "short"} or position_side is not None:
            raise ValueError("invalid position entry")
        if signal_date >= clean.index[index].date():
            raise ValueError("entry signal must precede next-open execution")
        entry_equity = cash
        entry_price = open_price
        entry_signal_date = signal_date
        entry_reason = reason
        trading_capital = cash * (1 - one_way_cost)
        entry_cost_amount = cash - trading_capital
        direction = 1.0 if side == "long" else -1.0
        units = direction * trading_capital / open_price
        cash = trading_capital - units * open_price
        position_side = side
        entry_index = index
        transaction_cost_total += entry_cost_amount
        return entry_cost_amount

    def exit_position(
        index: int,
        timestamp: pd.Timestamp,
        open_price: float,
        reason: str,
        *,
        signal_date: date,
    ) -> float:
        nonlocal cash, units, position_side, entry_index, entry_price
        nonlocal entry_equity, entry_cost_amount, entry_signal_date, entry_reason
        nonlocal transaction_cost_total
        if (
            position_side is None
            or entry_index is None
            or entry_signal_date is None
            or entry_reason is None
        ):
            raise ValueError("invalid position exit")
        if signal_date >= timestamp.date():
            raise ValueError("exit signal must precede next-open execution")
        exit_cost_amount = abs(units) * open_price * one_way_cost
        ending_equity = cash + units * open_price - exit_cost_amount
        if not isfinite(ending_equity) or ending_equity <= 0:
            raise ValueError("synthetic_short_equity_non_positive")
        direction = 1.0 if position_side == "long" else -1.0
        gross_return = direction * (open_price / entry_price - 1)
        trades.append(
            Trade(
                side=position_side,
                entry_date=clean.index[entry_index].date(),
                exit_date=timestamp.date(),
                entry_price=entry_price,
                exit_price=open_price,
                holding_sessions=index - entry_index,
                reason=reason,
                gross_return=gross_return,
                transaction_cost=(entry_cost_amount + exit_cost_amount) / entry_equity,
                borrow_cost=0.0,
                net_return=ending_equity / entry_equity - 1,
                entry_signal_date=entry_signal_date,
                entry_reason=entry_reason,
                exit_signal_date=signal_date,
                exit_reason=reason,
            )
        )
        transaction_cost_total += exit_cost_amount
        cash = ending_equity
        units = 0.0
        position_side = None
        entry_index = None
        entry_price = 0.0
        entry_equity = 0.0
        entry_cost_amount = 0.0
        entry_signal_date = None
        entry_reason = None
        return exit_cost_amount

    def record_action(
        *,
        signal_date: date,
        execution_date: date,
        action_type: str,
        from_position: str,
        to_position: str,
        reason: str,
        price: float,
        transaction_cost_amount: float,
    ) -> None:
        nonlocal action_sequence
        if signal_date >= execution_date:
            raise ValueError("action signal must precede next-open execution")
        action_sequence += 1
        actions.append(
            StrategyAction(
                action_id=(
                    f"{policy_id}:{ticker}:{execution_date.isoformat()}:"
                    f"{action_sequence:04d}:{action_type}"
                ),
                signal_date=signal_date,
                execution_date=execution_date,
                action_type=action_type,
                from_position=from_position,
                to_position=to_position,
                reason=reason,
                price=price,
                transaction_cost_amount=transaction_cost_amount,
            )
        )

    for index, (timestamp, row) in enumerate(clean.iterrows()):
        open_price = float(row["open"])
        if pending_exit_reason is not None and position_side is not None:
            if pending_exit_signal_date is None:
                raise ValueError("pending exit signal date is missing")
            signal_date = pending_exit_signal_date
            reason = pending_exit_reason
            from_position = position_side
            reversal_side = pending_reversal_side
            exit_cost_amount = exit_position(
                index,
                timestamp,
                open_price,
                reason,
                signal_date=signal_date,
            )
            pending_exit_reason = None
            pending_exit_signal_date = None
            pending_reversal_side = None
            if reversal_side is not None:
                entry_cost = enter(
                    reversal_side,
                    index,
                    open_price,
                    signal_date=signal_date,
                    reason=entry_reason_for(reversal_side),
                )
                record_action(
                    signal_date=signal_date,
                    execution_date=timestamp.date(),
                    action_type="reverse",
                    from_position=from_position,
                    to_position=reversal_side,
                    reason=reason,
                    price=open_price,
                    transaction_cost_amount=exit_cost_amount + entry_cost,
                )
            else:
                record_action(
                    signal_date=signal_date,
                    execution_date=timestamp.date(),
                    action_type="exit",
                    from_position=from_position,
                    to_position="cash",
                    reason=reason,
                    price=open_price,
                    transaction_cost_amount=exit_cost_amount,
                )

        if pending_entry_side is not None and position_side is None:
            if pending_entry_signal_date is None:
                raise ValueError("pending entry signal date is missing")
            side = pending_entry_side
            signal_date = pending_entry_signal_date
            reason = entry_reason_for(side)
            entry_cost = enter(
                side,
                index,
                open_price,
                signal_date=signal_date,
                reason=reason,
            )
            record_action(
                signal_date=signal_date,
                execution_date=timestamp.date(),
                action_type="enter",
                from_position="cash",
                to_position=side,
                reason=reason,
                price=open_price,
                transaction_cost_amount=entry_cost,
            )
            pending_entry_side = None
            pending_entry_signal_date = None

        signal = aligned_signals[index]
        if position_side is not None and entry_index is not None:
            held_sessions = index - entry_index + 1
            percentile = signal.percentile if signal is not None else None
            opposite_side = (
                "short"
                if position_side == "long"
                and signal is not None
                and signal.date in short_entry_dates
                else "long"
                if position_side == "short"
                and signal is not None
                and signal.date in long_entry_dates
                else None
            )
            if opposite_side is not None:
                pending_exit_reason = "opposite_extreme"
                pending_exit_signal_date = signal.date
                pending_reversal_side = opposite_side
            elif (
                position_side == "long"
                and percentile is not None
                and percentile >= long_exit_percentile
            ) or (
                position_side == "short"
                and percentile is not None
                and percentile <= short_exit_percentile
            ):
                pending_exit_reason = "recovery"
                pending_exit_signal_date = signal.date
            elif held_sessions >= max_holding:
                pending_exit_reason = "max_holding"
                pending_exit_signal_date = timestamp.date()
        elif signal is not None and signal.date in long_entry_dates:
            pending_entry_side = "long"
            pending_entry_signal_date = signal.date
        elif signal is not None and signal.date in short_entry_dates:
            pending_entry_side = "short"
            pending_entry_signal_date = signal.date

        marked_equity = cash + units * float(row["close"])
        if not isfinite(marked_equity) or marked_equity <= 0:
            raise ValueError("synthetic_short_equity_non_positive")
        equity_values.append(marked_equity)
        exposure_values.append(
            1.0 if position_side == "long" else -1.0 if position_side == "short" else 0.0
        )

    equity = pd.Series(equity_values, index=clean.index, name="equity")
    buy_hold_equity = clean["close"] / float(clean["open"].iloc[0])
    buy_hold_equity.name = "buy_hold_equity"
    exposure = pd.Series(exposure_values, index=clean.index, name="exposure")
    metrics = calculate_metrics(
        equity,
        buy_hold_equity,
        trades,
        exposure,
        clean,
        transaction_cost_total=transaction_cost_total,
    )
    pending_action: str | None = None
    pending_reason: str | None = None
    pending_signal_date: date | None = None
    if pending_exit_reason is not None and entry_index is not None:
        pending_action = "reverse_next_open" if pending_reversal_side else "exit_next_open"
        pending_reason = pending_exit_reason
        pending_signal_date = pending_exit_signal_date
    elif pending_entry_side is not None and entry_index is None:
        pending_action = "enter_next_open"
        pending_reason = entry_reason_for(pending_entry_side)
        pending_signal_date = pending_entry_signal_date
    open_trade = None
    if position_side is not None and entry_index is not None:
        current_equity = equity_values[-1]
        open_trade = {
            "side": position_side,
            "entryDate": clean.index[entry_index].date().isoformat(),
            "entrySignalDate": entry_signal_date.isoformat() if entry_signal_date else None,
            "entryReason": entry_reason,
            "entryPrice": entry_price,
            "holdingSessions": len(clean) - entry_index,
            "unrealizedReturn": current_equity / entry_equity - 1,
        }

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
        policy_id=policy_id,
        position=position_side or "cash",
        pending_side=pending_reversal_side or pending_entry_side,
        open_trade=open_trade,
        long_exit_percentile=long_exit_percentile,
        short_exit_percentile=short_exit_percentile,
        actions=actions,
        pending_signal_date=pending_signal_date,
    )


def run_backtest_safe(
    signals: list[FlowSignal],
    bars: pd.DataFrame,
    *,
    ticker: str,
    max_holding: int = 20,
    one_way_cost_bps: float = 10,
    policy_id: str = "long_cash",
    long_exit_percentile: float = 80,
    short_exit_percentile: float = 20,
) -> BacktestResult:
    """Fail closed for publication paths instead of presenting false cash results."""
    try:
        return run_backtest(
            signals,
            bars,
            ticker=ticker,
            max_holding=max_holding,
            one_way_cost_bps=one_way_cost_bps,
            policy_id=policy_id,
            long_exit_percentile=long_exit_percentile,
            short_exit_percentile=short_exit_percentile,
        )
    except (AttributeError, TypeError, ValueError) as error:
        return _unavailable_result(
            ticker,
            one_way_cost_bps,
            str(error),
            policy_id=policy_id,
            long_exit_percentile=long_exit_percentile,
            short_exit_percentile=short_exit_percentile,
        )


def run_cost_sensitivity(
    signals: list[FlowSignal],
    bars: pd.DataFrame,
    *,
    ticker: str,
    cost_bps: tuple[float, ...] = (0, 5, 10, 20),
    max_holding: int = 20,
    fail_closed: bool = True,
    policy_id: str = "long_cash",
    long_exit_percentile: float = 80,
    short_exit_percentile: float = 20,
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
            policy_id=policy_id,
            long_exit_percentile=long_exit_percentile,
            short_exit_percentile=short_exit_percentile,
        )
        for cost in cost_bps
    ]


def _extreme_entry_dates(signals: list[FlowSignal], state: str) -> set[date]:
    if state not in {"extreme_fear", "extreme_greed"}:
        raise ValueError("entry state must be extreme fear or extreme greed")
    entries: set[date] = set()
    previous_valid_state: str | None = None
    for signal in sorted(signals, key=lambda item: item.date):
        if not signal.trade_eligible:
            continue
        if signal.state == state and signal.state != previous_valid_state:
            entries.add(signal.date)
        previous_valid_state = signal.state
    return entries


def _extreme_fear_entry_dates(signals: list[FlowSignal]) -> set[date]:
    """Compatibility alias retained for external calculation-contract users."""
    return _extreme_entry_dates(signals, "extreme_fear")


def calculate_metrics(
    equity: pd.Series,
    buy_hold_equity: pd.Series,
    trades: list[Trade],
    exposure: pd.Series,
    bars: pd.DataFrame,
    *,
    transaction_cost_total: float = 0.0,
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
    long_trades = [trade for trade in trades if trade.side == "long"]
    short_trades = [trade for trade in trades if trade.side == "short"]
    exposure_matched = build_exposure_matched_equity(bars, exposure)
    exposure_matched_drawdown = exposure_matched / exposure_matched.cummax() - 1
    risk_matched, risk_scale = build_risk_matched_equity(
        buy_hold_equity, target_volatility=volatility
    )
    risk_matched_drawdown = risk_matched / risk_matched.cummax() - 1
    notional_turnover = _annualized_notional_turnover(exposure, years)
    long_exposure = float((exposure > 0).mean())
    short_exposure = float((exposure < 0).mean())
    cash_exposure = float((exposure == 0).mean())
    gross_exposure = float(exposure.abs().mean())
    net_exposure = float(exposure.mean())
    reason_counts = {
        reason: sum(trade.reason == reason for trade in trades)
        for reason in ("recovery", "max_holding", "opposite_extreme")
    }
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
        "exposure": gross_exposure,
        "longExposure": long_exposure,
        "shortExposure": short_exposure,
        "cashExposure": cash_exposure,
        "grossExposure": gross_exposure,
        "netExposure": net_exposure,
        # ``turnover`` remains as a compatibility alias.  New consumers should
        # use the explicitly named notional allocation change metric.
        "turnover": notional_turnover,
        "annualizedNotionalTurnover": notional_turnover,
        "transactionSidesPerYear": notional_turnover,
        "tradeCount": len(trades),
        "closedTradeCount": len(trades),
        "longTradeCount": len(long_trades),
        "shortTradeCount": len(short_trades),
        "longWinRate": float(np.mean([trade.net_return > 0 for trade in long_trades]))
        if long_trades
        else None,
        "shortWinRate": float(np.mean([trade.net_return > 0 for trade in short_trades]))
        if short_trades
        else None,
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
        "transactionCostTotal": float(transaction_cost_total),
        "borrowCostTotal": 0.0,
        "reasonCounts": reason_counts,
        "reversalCount": reason_counts["opposite_extreme"],
    }


def build_exposure_matched_equity(bars: pd.DataFrame, exposure: pd.Series) -> pd.Series:
    """Replay the strategy's exact open/close holding windows without costs."""
    clean_bars, weights = bars[["open", "close"]].align(exposure, join="inner", axis=0)
    if clean_bars.empty:
        return pd.Series(dtype=float, name="exposure_matched_equity")
    weights = weights.astype(float)
    if not weights.isin((-1.0, 0.0, 1.0)).all():
        raise ValueError("exposure must contain only -1, 0, or 1")
    values: list[float] = []
    settled_cash = 1.0
    collateral_cash = 1.0
    units = 0.0
    previous_side = 0.0
    for index in range(len(clean_bars)):
        current_side = float(weights.iloc[index])
        open_price = float(clean_bars.iloc[index]["open"])
        close_price = float(clean_bars.iloc[index]["close"])
        if current_side != previous_side:
            if previous_side:
                settled_cash = collateral_cash + units * open_price
                if settled_cash <= 0:
                    raise ValueError("synthetic_short_equity_non_positive")
                units = 0.0
                collateral_cash = settled_cash
            if current_side:
                units = current_side * settled_cash / open_price
                collateral_cash = settled_cash - units * open_price
        marked = collateral_cash + units * close_price
        if marked <= 0:
            raise ValueError("synthetic_short_equity_non_positive")
        values.append(marked)
        previous_side = current_side
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
        "policyId": result.policy_id,
        "position": result.position,
        "openPosition": result.open_position,
        "pendingAction": result.pending_action,
        "pendingReason": result.pending_reason,
        "pendingSignalDate": (
            result.pending_signal_date.isoformat() if result.pending_signal_date else None
        ),
        "pendingSide": result.pending_side,
        "openTrade": _public_value(result.open_trade),
        "longExitPercentile": result.long_exit_percentile,
        "shortExitPercentile": result.short_exit_percentile,
        "status": result.status,
        "unavailableReason": result.unavailable_reason,
        "metrics": _public_value(result.metrics),
        "trades": [
            _public_value(
                {
                    **asdict(trade),
                    "entry_date": trade.entry_date.isoformat(),
                    "exit_date": trade.exit_date.isoformat(),
                    "entry_signal_date": (
                        trade.entry_signal_date.isoformat() if trade.entry_signal_date else None
                    ),
                    "exit_signal_date": (
                        trade.exit_signal_date.isoformat() if trade.exit_signal_date else None
                    ),
                }
            )
            for trade in result.trades
        ],
        "actions": [
            _public_value(
                {
                    "actionId": action.action_id,
                    "signalDate": action.signal_date.isoformat(),
                    "executionDate": action.execution_date.isoformat(),
                    "signalPhase": action.signal_phase,
                    "executionPhase": action.execution_phase,
                    "type": action.action_type,
                    "fromPosition": action.from_position,
                    "toPosition": action.to_position,
                    "reason": action.reason,
                    "price": action.price,
                    "transactionCostAmount": action.transaction_cost_amount,
                }
            )
            for action in result.actions
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


def _unavailable_result(
    ticker: str,
    cost_bps: float,
    reason: str,
    *,
    policy_id: str,
    long_exit_percentile: float,
    short_exit_percentile: float,
) -> BacktestResult:
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
        policy_id=policy_id,
        position="unavailable",
        long_exit_percentile=long_exit_percentile,
        short_exit_percentile=short_exit_percentile,
    )


def _public_value(value: Any) -> Any:
    if isinstance(value, float):
        return round(value, 10)
    if isinstance(value, dict):
        return {key: _public_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_public_value(item) for item in value]
    return value
