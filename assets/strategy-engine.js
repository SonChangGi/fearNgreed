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

function calculateMetrics({ bars, equityValues, exposures, trades, transactionCostTotal }) {
  const returns = equityValues.map((value, index) => index ? value / equityValues[index - 1] - 1 : 0);
  const elapsedYears = (Date.parse(`${bars.at(-1).date}T00:00:00Z`) - Date.parse(`${bars[0].date}T00:00:00Z`)) / 86_400_000 / 365.2425;
  const volatility = sampleStd(returns) * Math.sqrt(252);
  const returnStd = sampleStd(returns);
  const equityDrawdowns = drawdowns(equityValues);
  const buyHoldValues = bars.map((bar) => bar.close / bars[0].open);
  const buyHoldDrawdowns = drawdowns(buyHoldValues);
  const zeroCostValues = exposureMatchedEquity(bars, exposures);
  const zeroCostDrawdowns = drawdowns(zeroCostValues);
  const riskMatched = riskMatchedBuyHold(buyHoldValues, volatility);
  const riskMatchedDrawdowns = drawdowns(riskMatched.values);
  const changes = exposures.reduce((sum, value, index) => sum + Math.abs(value - (index ? exposures[index - 1] : 0)), 0);
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
    buyAndHoldReturn: bars.at(-1).close / bars[0].open - 1,
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

export function runStrategyScenario({
  historyRows,
  proxy = "226490",
  period = "common",
  variant = "scaled_huber",
  costBps = 10,
  policyId = "long_cash",
  longExitPercentile = DEFAULT_LONG_EXIT_PERCENTILE,
  maxHolding = 20
}) {
  const exitPercentile = normalizeLongExitPercentile(longExitPercentile);
  const shortExitPercentile = 100 - exitPercentile;
  const fields = VARIANT_FIELDS[variant];
  if (!fields) throw new Error("지원하지 않는 신호 변형입니다.");
  if (!POLICY_IDS.has(policyId)) throw new Error("지원하지 않는 포지션 정책입니다.");
  if (policyId === "long_short_cash" && variant === "disparity") throw new Error("이격도 변형은 숏 진입 규칙이 정의되지 않았습니다.");
  if (!Array.isArray(historyRows) || historyRows.length < 2) throw new Error("공개 전략 입력 이력이 부족합니다.");
  if (!Number.isFinite(Number(costBps)) || Number(costBps) < 0) throw new Error("거래비용이 올바르지 않습니다.");
  if (!Number.isInteger(maxHolding) || maxHolding <= 0) throw new Error("최대 보유기간이 올바르지 않습니다.");
  const dates = historyRows.map((row) => row.date);
  if (dates.some((date, index) => typeof date !== "string" || (index && date <= dates[index - 1]))) throw new Error("공개 전략 입력 날짜가 오름차순 고유값이 아닙니다.");
  const bars = selectedBars(historyRows, proxy, period);
  if (bars.length < 2) throw new Error("선택한 ETF·기간의 가격 이력이 부족합니다.");
  const longEntryDates = extremeEntryDates(historyRows, fields, "extreme_fear");
  const shortEntryDates = policyId === "long_short_cash" ? extremeEntryDates(historyRows, fields, "extreme_greed") : new Set();
  const cost = Number(costBps) / 10_000;
  let cash = 1;
  let units = 0;
  let positionSide = null;
  let entryIndex = null;
  let entryPrice = 0;
  let entryEquity = 0;
  let entryCostAmount = 0;
  let pendingEntrySide = null;
  let pendingExitReason = null;
  let pendingReversalSide = null;
  let transactionCostTotal = 0;
  const trades = [];
  const equityValues = [];
  const exposures = [];

  const enter = (side, index, openPrice) => {
    entryEquity = cash;
    entryPrice = openPrice;
    const tradingCapital = cash * (1 - cost);
    entryCostAmount = cash - tradingCapital;
    units = (side === "long" ? 1 : -1) * tradingCapital / openPrice;
    cash = tradingCapital - units * openPrice;
    positionSide = side;
    entryIndex = index;
    transactionCostTotal += entryCostAmount;
  };

  const exitPosition = (index, bar, reason) => {
    const exitCostAmount = Math.abs(units) * bar.open * cost;
    const endingEquity = cash + units * bar.open - exitCostAmount;
    if (!(endingEquity > 0)) throw new Error("합성 숏 자산이 0 이하입니다.");
    const direction = positionSide === "long" ? 1 : -1;
    trades.push({
      side: positionSide,
      entry_date: bars[entryIndex].date,
      exit_date: bar.date,
      entry_price: entryPrice,
      exit_price: bar.open,
      holding_sessions: index - entryIndex,
      reason,
      gross_return: direction * (bar.open / entryPrice - 1),
      transaction_cost: (entryCostAmount + exitCostAmount) / entryEquity,
      borrow_cost: 0,
      net_return: endingEquity / entryEquity - 1
    });
    transactionCostTotal += exitCostAmount;
    cash = endingEquity;
    units = 0;
    positionSide = null;
    entryIndex = null;
    entryPrice = 0;
    entryEquity = 0;
    entryCostAmount = 0;
  };

  bars.forEach((bar, index) => {
    if (pendingExitReason && positionSide) {
      const reversalSide = pendingReversalSide;
      exitPosition(index, bar, pendingExitReason);
      pendingExitReason = null;
      pendingReversalSide = null;
      if (reversalSide) enter(reversalSide, index, bar.open);
    }
    if (pendingEntrySide && !positionSide) {
      enter(pendingEntrySide, index, bar.open);
      pendingEntrySide = null;
    }
    const row = bar.row;
    const percentile = finite(row[fields.percentile]) ? Number(row[fields.percentile]) : null;
    if (positionSide && entryIndex != null) {
      const heldSessions = index - entryIndex + 1;
      const oppositeSide = positionSide === "long" && shortEntryDates.has(bar.date) ? "short" : positionSide === "short" && longEntryDates.has(bar.date) ? "long" : null;
      if (oppositeSide) {
        pendingExitReason = "opposite_extreme";
        pendingReversalSide = oppositeSide;
      } else if ((positionSide === "long" && percentile != null && percentile >= exitPercentile) || (positionSide === "short" && percentile != null && percentile <= shortExitPercentile)) {
        pendingExitReason = "recovery";
      } else if (heldSessions >= maxHolding) {
        pendingExitReason = "max_holding";
      }
    } else if (longEntryDates.has(bar.date)) {
      pendingEntrySide = "long";
    } else if (shortEntryDates.has(bar.date)) {
      pendingEntrySide = "short";
    }
    const marked = cash + units * bar.close;
    if (!(marked > 0)) throw new Error("합성 숏 자산이 0 이하입니다.");
    equityValues.push(marked);
    exposures.push(positionSide === "long" ? 1 : positionSide === "short" ? -1 : 0);
  });

  const metrics = calculateMetrics({ bars, equityValues, exposures, trades, transactionCostTotal });
  const equityDrawdowns = drawdowns(equityValues);
  const buyHoldValues = bars.map((bar) => bar.close / bars[0].open);
  const buyHoldDrawdowns = drawdowns(buyHoldValues);
  const pendingAction = pendingExitReason ? (pendingReversalSide ? "reverse_next_open" : "exit_next_open") : pendingEntrySide ? "enter_next_open" : null;
  const pendingReason = pendingExitReason || (pendingEntrySide === "long" ? "extreme_fear_entry" : pendingEntrySide === "short" ? "extreme_greed_entry" : null);
  const currentEquity = equityValues.at(-1);
  return {
    ticker: proxy,
    oneWayCostBps: Number(costBps),
    policyId,
    position: positionSide || "cash",
    openPosition: positionSide != null,
    pendingAction,
    pendingReason,
    pendingSide: pendingReversalSide || pendingEntrySide,
    openTrade: positionSide && entryIndex != null ? {
      side: positionSide,
      entryDate: bars[entryIndex].date,
      entryPrice,
      holdingSessions: bars.length - entryIndex,
      unrealizedReturn: currentEquity / entryEquity - 1
    } : null,
    longExitPercentile: exitPercentile,
    shortExitPercentile,
    status: "ok",
    unavailableReason: null,
    metrics,
    trades,
    tradeHistoryTruncated: false,
    equity: bars.map((bar, index) => ({
      date: bar.date,
      value: equityValues[index],
      buyHoldValue: buyHoldValues[index],
      drawdown: equityDrawdowns[index],
      buyHoldDrawdown: buyHoldDrawdowns[index]
    })),
    calculationSource: "browser_user_scenario"
  };
}
