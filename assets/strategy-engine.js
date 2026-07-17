const POLICY_IDS = new Set(["long_cash", "long_short_cash"]);

export const DEFAULT_LONG_EXIT_PERCENTILE = 80;
export const MIN_LONG_EXIT_PERCENTILE = 50;
export const MAX_LONG_EXIT_PERCENTILE = 94;

const VARIANT_FIELDS = Object.freeze({
  scaled_huber: { state: "state", percentile: "percentile", eligible: "tradeEligible" },
  scaled_ols: { state: "scaledState", percentile: "scaledPercentile", eligible: "scaledTradeEligible" },
  raw_ols: { state: "rawState", percentile: "rawPercentile", eligible: "rawTradeEligible" },
  disparity: { state: "state", percentile: "percentile", eligible: "disparityTradeEligible" }
});

function finite(value) {
  return typeof value === "number" && Number.isFinite(value);
}

function mean(values) {
  return values.length ? values.reduce((sum, value) => sum + value, 0) / values.length : null;
}

function sampleStd(values) {
  if (values.length < 2) return 0;
  const average = mean(values);
  const variance = values.reduce((sum, value) => sum + (value - average) ** 2, 0) / (values.length - 1);
  return Math.sqrt(Math.max(0, variance));
}

function drawdowns(values) {
  let peak = -Infinity;
  return values.map((value) => {
    peak = Math.max(peak, value);
    return value / peak - 1;
  });
}

function extremeEntryDates(rows, fields, targetState) {
  const dates = new Set();
  let previousValidState = null;
  for (const row of rows) {
    if (row[fields.eligible] !== true) continue;
    const state = row[fields.state];
    if (state === targetState && state !== previousValidState) dates.add(row.date);
    previousValidState = state;
  }
  return dates;
}

function validBar(row, proxy) {
  return finite(row[`p${proxy}Open`]) && finite(row[`p${proxy}Close`]) && row[`p${proxy}Open`] > 0 && row[`p${proxy}Close`] > 0;
}

function selectedBars(rows, proxy, period) {
  return rows.filter((row) => {
    if (!validBar(row, proxy)) return false;
    if (period !== "common") return true;
    return validBar(row, "226490") && validBar(row, "069500");
  }).map((row) => ({
    row,
    date: row.date,
    open: Number(row[`p${proxy}Open`]),
    close: Number(row[`p${proxy}Close`])
  }));
}

function exposureMatchedEquity(bars, exposures) {
  let settledCash = 1;
  let collateralCash = 1;
  let units = 0;
  let previousSide = 0;
  return bars.map((bar, index) => {
    const side = exposures[index];
    if (![-1, 0, 1].includes(side)) throw new Error("포지션 입력이 -1/0/1 계약을 벗어났습니다.");
    if (side !== previousSide) {
      if (previousSide !== 0) {
        settledCash = collateralCash + units * bar.open;
        if (!(settledCash > 0)) throw new Error("합성 숏 자산이 0 이하입니다.");
        units = 0;
        collateralCash = settledCash;
      }
      if (side !== 0) {
        units = side * settledCash / bar.open;
        collateralCash = settledCash - units * bar.open;
      }
    }
    const marked = collateralCash + units * bar.close;
    if (!(marked > 0)) throw new Error("합성 숏 자산이 0 이하입니다.");
    previousSide = side;
    return marked;
  });
}

function riskMatchedBuyHold(buyHoldValues, targetVolatility) {
  const returns = buyHoldValues.map((value, index) => index ? value / buyHoldValues[index - 1] - 1 : 0);
  const benchmarkVolatility = sampleStd(returns) * Math.sqrt(252);
  if (!(benchmarkVolatility > 0) || !finite(targetVolatility)) return { values: buyHoldValues.map(() => 1), scale: null };
  const scale = Math.min(1, targetVolatility / benchmarkVolatility);
  let capital = 1;
  return {
    scale,
    values: returns.map((value) => {
      capital *= 1 + value * scale;
      return capital;
    })
  };
}

function calculateMetrics({
  bars,
  equityValues,
  exposures,
  trades,
  transactionCostTotal,
  buyHoldValues: suppliedBuyHoldValues = null,
  zeroCostValues: suppliedZeroCostValues = null,
  initialExposure = 0
}) {
  const returns = equityValues.map((value, index) => index ? value / equityValues[index - 1] - 1 : 0);
  const elapsedYears = (Date.parse(`${bars.at(-1).date}T00:00:00Z`) - Date.parse(`${bars[0].date}T00:00:00Z`)) / 86_400_000 / 365.2425;
  const volatility = sampleStd(returns) * Math.sqrt(252);
  const returnStd = sampleStd(returns);
  const equityDrawdowns = drawdowns(equityValues);
  const buyHoldValues = suppliedBuyHoldValues || bars.map((bar) => bar.close / bars[0].open);
  const buyHoldDrawdowns = drawdowns(buyHoldValues);
  const zeroCostValues = suppliedZeroCostValues || exposureMatchedEquity(bars, exposures);
  const zeroCostDrawdowns = drawdowns(zeroCostValues);
  const riskMatched = riskMatchedBuyHold(buyHoldValues, volatility);
  const riskMatchedDrawdowns = drawdowns(riskMatched.values);
  const changes = exposures.reduce((sum, value, index) => sum + Math.abs(value - (index ? exposures[index - 1] : initialExposure)), 0);
  const longTrades = trades.filter((trade) => trade.side === "long");
  const shortTrades = trades.filter((trade) => trade.side === "short");
  const wins = trades.map((trade) => trade.net_return > 0);
  const reasonCounts = Object.fromEntries(["recovery", "max_holding", "opposite_extreme"].map((reason) => [reason, trades.filter((trade) => trade.reason === reason).length]));
  const longExposure = exposures.filter((value) => value > 0).length / exposures.length;
  const shortExposure = exposures.filter((value) => value < 0).length / exposures.length;
  const cashExposure = exposures.filter((value) => value === 0).length / exposures.length;
  const grossExposure = longExposure + shortExposure;
  const netExposure = longExposure - shortExposure;
  return {
    start: bars[0].date,
    end: bars.at(-1).date,
    totalReturn: equityValues.at(-1) - 1,
    cagr: elapsedYears > 0 ? equityValues.at(-1) ** (1 / elapsedYears) - 1 : null,
    volatility,
    sharpe: returnStd > 0 ? mean(returns) / returnStd * Math.sqrt(252) : null,
    maxDrawdown: Math.min(...equityDrawdowns),
    winRate: wins.length ? wins.filter(Boolean).length / wins.length : null,
    exposure: grossExposure,
    longExposure,
    shortExposure,
    cashExposure,
    grossExposure,
    netExposure,
    turnover: elapsedYears > 0 ? changes / elapsedYears : null,
    annualizedNotionalTurnover: elapsedYears > 0 ? changes / elapsedYears : null,
    transactionSidesPerYear: elapsedYears > 0 ? changes / elapsedYears : null,
    tradeCount: trades.length,
    closedTradeCount: trades.length,
    longTradeCount: longTrades.length,
    shortTradeCount: shortTrades.length,
    longWinRate: longTrades.length ? longTrades.filter((trade) => trade.net_return > 0).length / longTrades.length : null,
    shortWinRate: shortTrades.length ? shortTrades.filter((trade) => trade.net_return > 0).length / shortTrades.length : null,
    averageHoldingSessions: trades.length ? mean(trades.map((trade) => trade.holding_sessions)) : null,
    buyAndHoldReturn: suppliedBuyHoldValues ? buyHoldValues.at(-1) - 1 : bars.at(-1).close / bars[0].open - 1,
    buyAndHoldMaxDrawdown: Math.min(...buyHoldDrawdowns),
    zeroCostTimingReturn: zeroCostValues.at(-1) - 1,
    zeroCostTimingMaxDrawdown: Math.min(...zeroCostDrawdowns),
    exposureMatchedReturn: zeroCostValues.at(-1) - 1,
    exposureMatchedMaxDrawdown: Math.min(...zeroCostDrawdowns),
    excessReturnVsExposureMatched: equityValues.at(-1) - zeroCostValues.at(-1),
    excessReturnVsZeroCostTiming: equityValues.at(-1) - zeroCostValues.at(-1),
    riskMatchedBuyHoldReturn: riskMatched.values.at(-1) - 1,
    riskMatchedBuyHoldMaxDrawdown: Math.min(...riskMatchedDrawdowns),
    riskMatchedScale: riskMatched.scale,
    transactionCostTotal,
    borrowCostTotal: 0,
    reasonCounts,
    reversalCount: reasonCounts.opposite_extreme
  };
}

export function normalizeLongExitPercentile(value) {
  const number = Number(value);
  if (!Number.isInteger(number) || number < MIN_LONG_EXIT_PERCENTILE || number > MAX_LONG_EXIT_PERCENTILE) {
    throw new Error(`롱 청산 백분위는 ${MIN_LONG_EXIT_PERCENTILE}~${MAX_LONG_EXIT_PERCENTILE} 사이 정수여야 합니다.`);
  }
  return number;
}

function normalizeOptionalDate(value, fieldName) {
  if (value == null || value === "") return null;
  if (typeof value !== "string" || !/^\d{4}-\d{2}-\d{2}$/.test(value)) {
    throw new Error(`${fieldName}은 YYYY-MM-DD 형식이어야 합니다.`);
  }
  const parsed = new Date(`${value}T00:00:00Z`);
  if (Number.isNaN(parsed.getTime()) || parsed.toISOString().slice(0, 10) !== value) {
    throw new Error(`${fieldName}이 올바른 달력 날짜가 아닙니다.`);
  }
  return value;
}

function positionExposure(position) {
  return position === "long" ? 1 : position === "short" ? -1 : 0;
}

function pendingSnapshot({
  pendingEntrySide,
  pendingEntryReason,
  pendingEntrySignalDate,
  pendingExitReason,
  pendingExitSignalDate,
  pendingReversalSide
}) {
  if (pendingExitReason) {
    return {
      action: pendingReversalSide ? "reverse_next_open" : "exit_next_open",
      reason: pendingExitReason,
      side: pendingReversalSide,
      signalDate: pendingExitSignalDate
    };
  }
  if (pendingEntrySide) {
    return {
      action: "enter_next_open",
      reason: pendingEntryReason,
      side: pendingEntrySide,
      signalDate: pendingEntrySignalDate
    };
  }
  return { action: null, reason: null, side: null, signalDate: null };
}

function assertPriorSignal(signalDate, executionDate) {
  if (typeof signalDate !== "string" || !(signalDate < executionDate)) {
    throw new Error("전략 행동의 근거 신호일은 체결일보다 앞서야 합니다.");
  }
}

export function runStrategyScenario({
  historyRows,
  proxy = "226490",
  period = "common",
  variant = "scaled_huber",
  costBps = 10,
  policyId = "long_cash",
  longExitPercentile = DEFAULT_LONG_EXIT_PERCENTILE,
  maxHolding = 20,
  startDate = null,
  endDate = null
}) {
  const exitPercentile = normalizeLongExitPercentile(longExitPercentile);
  const shortExitPercentile = 100 - exitPercentile;
  const requestedStartDate = normalizeOptionalDate(startDate, "시작일");
  const requestedEndDate = normalizeOptionalDate(endDate, "종료일");
  if (requestedStartDate && requestedEndDate && requestedStartDate > requestedEndDate) {
    throw new Error("시작일은 종료일보다 늦을 수 없습니다.");
  }
  const fields = VARIANT_FIELDS[variant];
  if (!fields) throw new Error("지원하지 않는 신호 변형입니다.");
  if (!POLICY_IDS.has(policyId)) throw new Error("지원하지 않는 포지션 정책입니다.");
  if (policyId === "long_short_cash" && variant === "disparity") throw new Error("이격도 변형은 숏 진입 규칙이 정의되지 않았습니다.");
  if (!Array.isArray(historyRows) || historyRows.length < 2) throw new Error("공개 전략 입력 이력이 부족합니다.");
  if (!Number.isFinite(Number(costBps)) || Number(costBps) < 0) throw new Error("거래비용이 올바르지 않습니다.");
  if (!Number.isInteger(maxHolding) || maxHolding <= 0) throw new Error("최대 보유기간이 올바르지 않습니다.");
  const dates = historyRows.map((row) => row.date);
  if (dates.some((date, index) => typeof date !== "string" || (index && date <= dates[index - 1]))) throw new Error("공개 전략 입력 날짜가 오름차순 고유값이 아닙니다.");
  // The evaluation end is also the information cutoff. Keep every row before
  // it so carry-in positions are reproduced, but never let later prices decide
  // whether a historical scenario is available.
  const simulationRows = requestedEndDate
    ? historyRows.filter((row) => row.date <= requestedEndDate)
    : historyRows;
  const bars = selectedBars(simulationRows, proxy, period);
  if (bars.length < 2) throw new Error("선택한 ETF·기간의 가격 이력이 부족합니다.");
  const longEntryDates = extremeEntryDates(simulationRows, fields, "extreme_fear");
  const shortEntryDates = policyId === "long_short_cash" ? extremeEntryDates(simulationRows, fields, "extreme_greed") : new Set();
  const cost = Number(costBps) / 10_000;
  let cash = 1;
  let units = 0;
  let positionSide = null;
  let entryIndex = null;
  let entryPrice = 0;
  let entryEquity = 0;
  let entryCostAmount = 0;
  let entrySignalDate = null;
  let entryReason = null;
  let pendingEntrySide = null;
  let pendingEntryReason = null;
  let pendingEntrySignalDate = null;
  let pendingExitReason = null;
  let pendingExitSignalDate = null;
  let pendingReversalSide = null;
  let transactionCostTotal = 0;
  const trades = [];
  const equityValues = [];
  const exposures = [];
  const actions = [];
  const signals = [];
  const ledger = [];
  let actionSequence = 0;

  const enter = (side, index, bar, signalDate, reason) => {
    assertPriorSignal(signalDate, bar.date);
    entryEquity = cash;
    entryPrice = bar.open;
    const tradingCapital = cash * (1 - cost);
    entryCostAmount = cash - tradingCapital;
    units = (side === "long" ? 1 : -1) * tradingCapital / bar.open;
    cash = tradingCapital - units * bar.open;
    positionSide = side;
    entryIndex = index;
    entrySignalDate = signalDate;
    entryReason = reason;
    transactionCostTotal += entryCostAmount;
    return entryCostAmount;
  };

  const exitPosition = (index, bar, reason, signalDate) => {
    assertPriorSignal(signalDate, bar.date);
    const exitCostAmount = Math.abs(units) * bar.open * cost;
    const endingEquity = cash + units * bar.open - exitCostAmount;
    if (!(endingEquity > 0)) throw new Error("합성 숏 자산이 0 이하입니다.");
    const direction = positionSide === "long" ? 1 : -1;
    const trade = {
      side: positionSide,
      entry_date: bars[entryIndex].date,
      exit_date: bar.date,
      entry_signal_date: entrySignalDate,
      entry_reason: entryReason,
      exit_signal_date: signalDate,
      exit_reason: reason,
      entry_price: entryPrice,
      exit_price: bar.open,
      holding_sessions: index - entryIndex,
      reason,
      gross_return: direction * (bar.open / entryPrice - 1),
      transaction_cost: (entryCostAmount + exitCostAmount) / entryEquity,
      borrow_cost: 0,
      net_return: endingEquity / entryEquity - 1
    };
    trades.push(trade);
    transactionCostTotal += exitCostAmount;
    cash = endingEquity;
    units = 0;
    positionSide = null;
    entryIndex = null;
    entryPrice = 0;
    entryEquity = 0;
    entryCostAmount = 0;
    entrySignalDate = null;
    entryReason = null;
    return { trade, exitCostAmount };
  };

  bars.forEach((bar, index) => {
    const openingPosition = positionSide || "cash";
    const openingEquity = cash + units * bar.open;
    if (!(openingEquity > 0)) throw new Error("합성 숏 자산이 0 이하입니다.");
    const openingPending = pendingSnapshot({
      pendingEntrySide,
      pendingEntryReason,
      pendingEntrySignalDate,
      pendingExitReason,
      pendingExitSignalDate,
      pendingReversalSide
    });
    const actionIds = [];

    if (pendingExitReason && positionSide) {
      const reversalSide = pendingReversalSide;
      const signalDate = pendingExitSignalDate;
      const exitReason = pendingExitReason;
      const fromPosition = positionSide;
      const { exitCostAmount } = exitPosition(index, bar, exitReason, signalDate);
      pendingExitReason = null;
      pendingExitSignalDate = null;
      pendingReversalSide = null;
      let entryCost = 0;
      if (reversalSide) {
        const reversalEntryReason = reversalSide === "long" ? "extreme_fear_entry" : "extreme_greed_entry";
        entryCost = enter(reversalSide, index, bar, signalDate, reversalEntryReason);
      }
      const type = reversalSide ? "reverse" : "exit";
      const actionId = `${policyId}:${proxy}:${bar.date}:${String(++actionSequence).padStart(4, "0")}:${type}`;
      actions.push({
        actionId,
        date: bar.date,
        executionDate: bar.date,
        executionPhase: "open",
        signalDate,
        signalPhase: "after_close",
        type,
        fromPosition,
        toPosition: reversalSide || "cash",
        reason: exitReason,
        price: bar.open,
        transactionCostAmount: exitCostAmount + entryCost,
        oneWayCostBps: Number(costBps)
      });
      actionIds.push(actionId);
    }
    if (pendingEntrySide && !positionSide) {
      const side = pendingEntrySide;
      const signalDate = pendingEntrySignalDate;
      const reason = pendingEntryReason;
      const entryCost = enter(side, index, bar, signalDate, reason);
      const actionId = `${policyId}:${proxy}:${bar.date}:${String(++actionSequence).padStart(4, "0")}:enter`;
      actions.push({
        actionId,
        date: bar.date,
        executionDate: bar.date,
        executionPhase: "open",
        signalDate,
        signalPhase: "after_close",
        type: "enter",
        fromPosition: "cash",
        toPosition: side,
        reason,
        price: bar.open,
        transactionCostAmount: entryCost,
        oneWayCostBps: Number(costBps)
      });
      actionIds.push(actionId);
      pendingEntrySide = null;
      pendingEntryReason = null;
      pendingEntrySignalDate = null;
    }

    const row = bar.row;
    const percentile = finite(row[fields.percentile]) ? Number(row[fields.percentile]) : null;
    if (positionSide && entryIndex != null) {
      const heldSessions = index - entryIndex + 1;
      const oppositeSide = positionSide === "long" && shortEntryDates.has(bar.date) ? "short" : positionSide === "short" && longEntryDates.has(bar.date) ? "long" : null;
      if (oppositeSide) {
        pendingExitReason = "opposite_extreme";
        pendingExitSignalDate = bar.date;
        pendingReversalSide = oppositeSide;
      } else if ((positionSide === "long" && percentile != null && percentile >= exitPercentile) || (positionSide === "short" && percentile != null && percentile <= shortExitPercentile)) {
        pendingExitReason = "recovery";
        pendingExitSignalDate = bar.date;
      } else if (heldSessions >= maxHolding) {
        pendingExitReason = "max_holding";
        pendingExitSignalDate = bar.date;
      }
    } else if (longEntryDates.has(bar.date)) {
      pendingEntrySide = "long";
      pendingEntryReason = "extreme_fear_entry";
      pendingEntrySignalDate = bar.date;
    } else if (shortEntryDates.has(bar.date)) {
      pendingEntrySide = "short";
      pendingEntryReason = "extreme_greed_entry";
      pendingEntrySignalDate = bar.date;
    }

    const marked = cash + units * bar.close;
    if (!(marked > 0)) throw new Error("합성 숏 자산이 0 이하입니다.");
    equityValues.push(marked);
    const exposure = positionExposure(positionSide || "cash");
    exposures.push(exposure);
    const pending = pendingSnapshot({
      pendingEntrySide,
      pendingEntryReason,
      pendingEntrySignalDate,
      pendingExitReason,
      pendingExitSignalDate,
      pendingReversalSide
    });
    const openTrade = positionSide && entryIndex != null ? {
      side: positionSide,
      entryDate: bars[entryIndex].date,
      entryPrice,
      entrySignalDate,
      entryReason,
      holdingSessions: index - entryIndex + 1,
      unrealizedReturn: marked / entryEquity - 1
    } : null;
    const signal = {
      date: bar.date,
      phase: "after_close",
      state: row[fields.state] ?? "unavailable",
      percentile,
      tradeEligible: row[fields.eligible] === true,
      extremeEntrySide: longEntryDates.has(bar.date) ? "long" : shortEntryDates.has(bar.date) ? "short" : null,
      scheduledAction: pending.action,
      scheduledReason: pending.reason,
      scheduledSide: pending.side
    };
    signals.push(signal);
    ledger.push({
      date: bar.date,
      marketTimezone: "Asia/Seoul",
      open: bar.open,
      close: bar.close,
      openingEquity,
      openingPosition,
      openingPendingAction: openingPending.action,
      openingPendingReason: openingPending.reason,
      openingPendingSide: openingPending.side,
      openingPendingSignalDate: openingPending.signalDate,
      position: positionSide || "cash",
      exposure,
      value: marked,
      actionIds,
      signal,
      pendingAction: pending.action,
      pendingReason: pending.reason,
      pendingSide: pending.side,
      pendingSignalDate: pending.signalDate,
      openTrade
    });
  });

  let appliedStartIndex = 0;
  let appliedEndIndex = bars.length - 1;
  if (requestedStartDate) appliedStartIndex = bars.findIndex((bar) => bar.date >= requestedStartDate);
  if (requestedEndDate) {
    appliedEndIndex = bars.length - 1;
    while (appliedEndIndex >= 0 && bars[appliedEndIndex].date > requestedEndDate) appliedEndIndex -= 1;
  }
  if (appliedStartIndex < 0 || appliedEndIndex < appliedStartIndex || appliedEndIndex - appliedStartIndex + 1 < 2) {
    throw new Error("선택 기간에는 최소 2개 ETF 거래일이 필요합니다.");
  }

  const hasCustomRange = requestedStartDate != null || requestedEndDate != null;
  const appliedStartDate = bars[appliedStartIndex].date;
  const appliedEndDate = bars[appliedEndIndex].date;
  const windowBars = bars.slice(appliedStartIndex, appliedEndIndex + 1);
  const fullBuyHoldValues = bars.map((bar) => bar.close / bars[0].open);
  const fullZeroCostValues = exposureMatchedEquity(bars, exposures);
  const strategyBase = hasCustomRange ? equityValues[appliedStartIndex] : 1;
  const buyHoldBase = hasCustomRange ? fullBuyHoldValues[appliedStartIndex] : 1;
  const zeroCostBase = hasCustomRange ? fullZeroCostValues[appliedStartIndex] : 1;
  const windowEquityValues = equityValues.slice(appliedStartIndex, appliedEndIndex + 1).map((value) => value / strategyBase);
  const buyHoldValues = fullBuyHoldValues.slice(appliedStartIndex, appliedEndIndex + 1).map((value) => value / buyHoldBase);
  const zeroCostValues = fullZeroCostValues.slice(appliedStartIndex, appliedEndIndex + 1).map((value) => value / zeroCostBase);
  const windowExposures = exposures.slice(appliedStartIndex, appliedEndIndex + 1);
  const windowActions = actions.filter((action) => action.date >= appliedStartDate && action.date <= appliedEndDate).map((action) => ({
    ...action,
    includedInWindowMetrics: !hasCustomRange || action.date > appliedStartDate,
    transactionCostWindowFraction: action.transactionCostAmount / strategyBase
  }));
  const metricTrades = hasCustomRange
    ? trades.filter((trade) => trade.entry_date > appliedStartDate && trade.exit_date <= appliedEndDate)
    : trades;
  const excludedCarryInClosedTrades = hasCustomRange
    ? trades.filter((trade) => trade.entry_date <= appliedStartDate && trade.exit_date > appliedStartDate && trade.exit_date <= appliedEndDate).length
    : 0;
  const windowTransactionCostTotal = hasCustomRange
    ? actions.filter((action) => action.date > appliedStartDate && action.date <= appliedEndDate).reduce((sum, action) => sum + action.transactionCostAmount, 0) / strategyBase
    : transactionCostTotal;
  const metrics = calculateMetrics({
    bars: windowBars,
    equityValues: windowEquityValues,
    exposures: windowExposures,
    trades: metricTrades,
    transactionCostTotal: windowTransactionCostTotal,
    buyHoldValues: hasCustomRange ? buyHoldValues : null,
    zeroCostValues: hasCustomRange ? zeroCostValues : null,
    initialExposure: hasCustomRange ? windowExposures[0] : 0
  });
  const equityDrawdowns = drawdowns(windowEquityValues);
  const buyHoldDrawdowns = drawdowns(buyHoldValues);
  const endSnapshot = ledger[appliedEndIndex];
  const startSnapshot = ledger[appliedStartIndex];
  const endOpenTrade = endSnapshot.openTrade ? {
    ...endSnapshot.openTrade,
    carryIn: hasCustomRange && endSnapshot.openTrade.entryDate <= appliedStartDate
  } : null;
  return {
    ticker: proxy,
    oneWayCostBps: Number(costBps),
    policyId,
    position: endSnapshot.position,
    openPosition: endSnapshot.position !== "cash",
    pendingAction: endSnapshot.pendingAction,
    pendingReason: endSnapshot.pendingReason,
    pendingSide: endSnapshot.pendingSide,
    pendingSignalDate: endSnapshot.pendingSignalDate,
    openTrade: endOpenTrade,
    longExitPercentile: exitPercentile,
    shortExitPercentile,
    status: "ok",
    unavailableReason: null,
    metrics,
    trades: metricTrades,
    tradeHistoryTruncated: false,
    equity: windowBars.map((bar, index) => ({
      date: bar.date,
      value: windowEquityValues[index],
      buyHoldValue: buyHoldValues[index],
      drawdown: equityDrawdowns[index],
      buyHoldDrawdown: buyHoldDrawdowns[index]
    })),
    actions: windowActions,
    signals: signals.slice(appliedStartIndex, appliedEndIndex + 1),
    ledger: ledger.slice(appliedStartIndex, appliedEndIndex + 1).map((item, index) => ({
      ...item,
      value: windowEquityValues[index],
      buyHoldValue: buyHoldValues[index],
      drawdown: equityDrawdowns[index],
      buyHoldDrawdown: buyHoldDrawdowns[index]
    })),
    range: {
      requestedStartDate,
      requestedEndDate,
      appliedStartDate,
      appliedEndDate,
      startSnapped: requestedStartDate != null && requestedStartDate !== appliedStartDate,
      endSnapped: requestedEndDate != null && requestedEndDate !== appliedEndDate,
      baselinePhase: hasCustomRange ? "applied_start_close" : "strategy_inception_open",
      pathMode: "full_history_then_window",
      carryIn: {
        position: startSnapshot.openingPosition,
        pendingAction: startSnapshot.openingPendingAction,
        pendingReason: startSnapshot.openingPendingReason,
        pendingSide: startSnapshot.openingPendingSide,
        signalDate: startSnapshot.openingPendingSignalDate
      },
      startClosePosition: startSnapshot.position,
      endClosePosition: endSnapshot.position,
      excludedCarryInClosedTrades,
      metricTradeInclusion: hasCustomRange ? "entry_after_start_close_and_exit_on_or_before_end" : "all_closed_trades"
    },
    calculationSource: "browser_user_scenario"
  };
}
