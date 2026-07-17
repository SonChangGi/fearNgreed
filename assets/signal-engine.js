export const DEFAULT_SIGNAL_CONFIG = Object.freeze({
  track: "robust",
  lookback: 252,
  minimumR2: 0.2,
  extremeTail: 5
});

export const MIN_SIGNAL_LOOKBACK = 60;
export const MAX_SIGNAL_LOOKBACK = 756;
export const MIN_EXTREME_TAIL = 1;
export const MAX_EXTREME_TAIL = 20;

export const TRACK_FIELD_MAPPING = Object.freeze({
  robust: Object.freeze({
    observed: "flowShare",
    method: "huber",
    state: "state",
    percentile: "percentile",
    eligible: "tradeEligible"
  }),
  scaled: Object.freeze({
    observed: "flowShare",
    method: "ols",
    state: "scaledState",
    percentile: "scaledPercentile",
    eligible: "scaledTradeEligible"
  }),
  raw: Object.freeze({
    observed: "rawFlowTrillion",
    method: "ols",
    state: "rawState",
    percentile: "rawPercentile",
    eligible: "rawTradeEligible"
  })
});

const EVENT_ASSET_FIELDS = Object.freeze({
  KOSPI: "kospiClose",
  "226490": "p226490Close",
  "069500": "p069500Close"
});

const DEFAULT_HORIZONS = Object.freeze([1, 5, 10, 20]);
const DEFAULT_BOOTSTRAP_SAMPLES = 10_000;
const DEFAULT_BOOTSTRAP_SEED = 20_260_715;

function finite(value) {
  return typeof value === "number" && Number.isFinite(value);
}

function normalizeNumber(value, name, minimum, maximum, { integer = false } = {}) {
  const number = Number(value);
  if (!Number.isFinite(number) || number < minimum || number > maximum || (integer && !Number.isInteger(number))) {
    const unit = integer ? " 정수" : " 숫자";
    throw new Error(`${name}은 ${minimum}~${maximum} 사이${unit}여야 합니다.`);
  }
  return number;
}

export function minimumObservationCount(lookback) {
  const window = normalizeNumber(lookback, "회귀 학습기간", MIN_SIGNAL_LOOKBACK, MAX_SIGNAL_LOOKBACK, { integer: true });
  return Math.min(window, Math.max(40, Math.min(200, Math.ceil(window * 0.8))));
}

export function normalizeSignalConfig(config = {}) {
  const track = config.track ?? DEFAULT_SIGNAL_CONFIG.track;
  if (!Object.hasOwn(TRACK_FIELD_MAPPING, track)) {
    throw new Error("연구 트랙은 robust, scaled, raw 중 하나여야 합니다.");
  }
  const lookback = normalizeNumber(
    config.lookback ?? DEFAULT_SIGNAL_CONFIG.lookback,
    "회귀 학습기간",
    MIN_SIGNAL_LOOKBACK,
    MAX_SIGNAL_LOOKBACK,
    { integer: true }
  );
  const minimumR2 = normalizeNumber(
    config.minimumR2 ?? DEFAULT_SIGNAL_CONFIG.minimumR2,
    "최소 R²",
    0,
    0.8
  );
  if (Math.abs(minimumR2 * 20 - Math.round(minimumR2 * 20)) > 1e-9) {
    throw new Error("최소 R²는 0.05 간격이어야 합니다.");
  }
  const extremeTail = normalizeNumber(
    config.extremeTail ?? DEFAULT_SIGNAL_CONFIG.extremeTail,
    "극단 꼬리 경계",
    MIN_EXTREME_TAIL,
    MAX_EXTREME_TAIL,
    { integer: true }
  );
  return Object.freeze({
    track,
    lookback,
    minimumR2,
    extremeTail,
    minimumObservations: minimumObservationCount(lookback),
    fitMethod: TRACK_FIELD_MAPPING[track].method,
    observedField: TRACK_FIELD_MAPPING[track].observed,
    trainingPolicy: "past_only_fixed_session_window",
    minimumObservationPolicy: "min(window,max(40,min(200,ceil(window*0.8))))"
  });
}

export function classifyDynamicPercentile(value, extremeTail = DEFAULT_SIGNAL_CONFIG.extremeTail) {
  const tail = normalizeNumber(extremeTail, "극단 꼬리 경계", MIN_EXTREME_TAIL, MAX_EXTREME_TAIL, { integer: true });
  if (!finite(value)) return "unavailable";
  if (value <= tail) return "extreme_fear";
  if (value <= 20) return "fear";
  if (value < 80) return "neutral";
  if (value < 100 - tail) return "greed";
  return "extreme_greed";
}

function selectKth(values, k) {
  let left = 0;
  let right = values.length - 1;
  while (left < right) {
    const pivot = values[Math.floor((left + right) / 2)];
    let low = left;
    let high = right;
    while (low <= high) {
      while (values[low] < pivot) low += 1;
      while (values[high] > pivot) high -= 1;
      if (low <= high) {
        [values[low], values[high]] = [values[high], values[low]];
        low += 1;
        high -= 1;
      }
    }
    if (k <= high) right = high;
    else if (k >= low) left = low;
    else return values[k];
  }
  return values[k];
}

function median(values) {
  if (!values.length) return null;
  const middle = Math.floor(values.length / 2);
  if (values.length % 2) return selectKth([...values], middle);
  const working = [...values];
  const upper = selectKth(working, middle);
  const lower = selectKth(working, middle - 1);
  return (lower + upper) / 2;
}

function mean(values) {
  return values.reduce((sum, value) => sum + value, 0) / values.length;
}

function ols(xs, ys) {
  const xMean = mean(xs);
  const yMean = mean(ys);
  const ssX = xs.reduce((sum, value) => sum + (value - xMean) ** 2, 0);
  if (!(ssX > 0)) throw new Error("return_variance_is_zero");
  const beta = xs.reduce((sum, value, index) => sum + (value - xMean) * (ys[index] - yMean), 0) / ssX;
  const alpha = yMean - beta * xMean;
  const residuals = ys.map((value, index) => value - (alpha + beta * xs[index]));
  return { alpha, beta, residuals, r2: unweightedR2(ys, residuals) };
}

function weightedLine(xs, ys, weights) {
  const weightSum = weights.reduce((sum, value) => sum + value, 0);
  if (!(weightSum > 0)) throw new Error("regression_weights_sum_to_zero");
  const xMean = xs.reduce((sum, value, index) => sum + weights[index] * value, 0) / weightSum;
  const yMean = ys.reduce((sum, value, index) => sum + weights[index] * value, 0) / weightSum;
  const ssX = xs.reduce((sum, value, index) => sum + weights[index] * (value - xMean) ** 2, 0);
  if (!(ssX > 0)) throw new Error("return_variance_is_zero");
  const beta = xs.reduce(
    (sum, value, index) => sum + weights[index] * (value - xMean) * (ys[index] - yMean),
    0
  ) / ssX;
  return { alpha: yMean - beta * xMean, beta };
}

function unweightedR2(ys, residuals) {
  const yMean = mean(ys);
  const total = ys.reduce((sum, value) => sum + (value - yMean) ** 2, 0);
  return total === 0 ? 0 : 1 - residuals.reduce((sum, value) => sum + value ** 2, 0) / total;
}

function weightedR2(ys, residuals, weights) {
  const weightSum = weights.reduce((sum, value) => sum + value, 0);
  const yMean = ys.reduce((sum, value, index) => sum + weights[index] * value, 0) / weightSum;
  const total = ys.reduce((sum, value, index) => sum + weights[index] * (value - yMean) ** 2, 0);
  if (total === 0) return 0;
  return 1 - residuals.reduce((sum, value, index) => sum + weights[index] * value ** 2, 0) / total;
}

function fitRegression(xs, ys, method) {
  let { alpha, beta, residuals, r2 } = ols(xs, ys);
  let weights = xs.map(() => 1);
  if (method === "huber") {
    for (let iteration = 0; iteration < 50; iteration += 1) {
      const center = median(residuals);
      const mad = median(residuals.map((value) => Math.abs(value - center)));
      const scale = 1.4826 * mad;
      if (!finite(scale) || !(scale > 0)) break;
      const cutoff = 1.345 * scale;
      weights = residuals.map((value) => Math.abs(value - center) <= cutoff ? 1 : cutoff / Math.abs(value - center));
      const next = weightedLine(xs, ys, weights);
      const converged = Math.max(Math.abs(next.alpha - alpha), Math.abs(next.beta - beta)) <= 1e-10 * (
        1 + Math.max(Math.abs(alpha), Math.abs(beta))
      );
      alpha = next.alpha;
      beta = next.beta;
      residuals = ys.map((value, index) => value - (alpha + beta * xs[index]));
      if (converged) break;
    }
    r2 = unweightedR2(ys, residuals);
  }
  return {
    alpha,
    beta,
    r2,
    fitScore: method === "huber" ? weightedR2(ys, residuals, weights) : r2,
    residuals,
    weights,
    method
  };
}

function unavailableSignal(row, config, quality, trainingCount, trainingBounds = {}) {
  return Object.freeze({
    date: row.date,
    track: config.track,
    fitMethod: config.fitMethod,
    lookback: config.lookback,
    minimumObservations: config.minimumObservations,
    trainingCount,
    trainingStart: trainingBounds.trainingStart ?? null,
    trainingEnd: trainingBounds.trainingEnd ?? null,
    alpha: null,
    beta: null,
    rollingR2: null,
    fitScore: null,
    expected: null,
    observed: finite(row[config.observedField]) ? row[config.observedField] : null,
    residual: null,
    residualZ: null,
    percentile: null,
    state: "unavailable",
    quality,
    tradeEligible: false
  });
}

function signalForRow(rows, index, config, { includeFitRows = false } = {}) {
  const current = rows[index];
  const currentX = current.return1d;
  const currentY = current[config.observedField];
  const windowRows = rows.slice(Math.max(0, index - config.lookback), index);
  const complete = windowRows.filter((row) => finite(row.return1d) && finite(row[config.observedField]));
  const bounds = {
    trainingStart: complete[0]?.date ?? null,
    trainingEnd: complete.at(-1)?.date ?? null
  };
  if (!finite(currentX) || !finite(currentY)) {
    return { signal: unavailableSignal(current, config, "invalid_current_observation", complete.length, bounds), fit: null };
  }
  if (complete.length < config.minimumObservations) {
    return { signal: unavailableSignal(current, config, "insufficient_history", complete.length, bounds), fit: null };
  }
  const xs = complete.map((row) => row.return1d);
  const ys = complete.map((row) => row[config.observedField]);
  let fit;
  try {
    fit = fitRegression(xs, ys, config.fitMethod);
  } catch {
    return { signal: unavailableSignal(current, config, "invalid_regression", complete.length, bounds), fit: null };
  }
  const expected = fit.alpha + fit.beta * currentX;
  const residual = currentY - expected;
  const center = median(fit.residuals);
  const mad = median(fit.residuals.map((value) => Math.abs(value - center)));
  if (!finite(mad) || !(mad > 0)) {
    const signal = Object.freeze({
      ...unavailableSignal(current, config, "zero_mad", complete.length, bounds),
      alpha: fit.alpha,
      beta: fit.beta,
      rollingR2: fit.r2,
      fitScore: fit.fitScore,
      expected,
      residual
    });
    return { signal, fit: includeFitRows ? currentFitPayload(complete, current, fit, signal, config) : null };
  }
  const percentile = 100 * fit.residuals.filter((value) => value <= residual).length / fit.residuals.length;
  const state = classifyDynamicPercentile(percentile, config.extremeTail);
  const quality = fit.beta < 0 && fit.r2 >= config.minimumR2 ? "ok" : "low_model_fit";
  const signal = Object.freeze({
    date: current.date,
    track: config.track,
    fitMethod: fit.method,
    lookback: config.lookback,
    minimumObservations: config.minimumObservations,
    trainingCount: complete.length,
    ...bounds,
    alpha: fit.alpha,
    beta: fit.beta,
    rollingR2: fit.r2,
    fitScore: fit.fitScore,
    expected,
    observed: currentY,
    residual,
    residualZ: (residual - center) / (1.4826 * mad),
    percentile,
    state,
    quality,
    tradeEligible: quality === "ok"
  });
  return { signal, fit: includeFitRows ? currentFitPayload(complete, current, fit, signal, config) : null };
}

function quantile(values, probability) {
  if (!values.length) return null;
  const ordered = [...values].sort((left, right) => left - right);
  const position = (ordered.length - 1) * probability;
  const lower = Math.floor(position);
  const weight = position - lower;
  return ordered[lower + 1] == null ? ordered[lower] : ordered[lower] * (1 - weight) + ordered[lower + 1] * weight;
}

function empiricalTransitionCuts(residuals, extremeTail) {
  const ordered = [...residuals].sort((left, right) => left - right);
  const count = ordered.length;
  const lowerTailTransition = (percentile) => ordered[Math.min(count - 1, Math.floor(percentile * count / 100))];
  const upperTailTransition = (percentile) => ordered[Math.max(0, Math.min(count - 1, Math.ceil(percentile * count / 100) - 1))];
  return Object.freeze({
    [String(extremeTail)]: lowerTailTransition(extremeTail),
    "20": lowerTailTransition(20),
    "80": upperTailTransition(80),
    [String(100 - extremeTail)]: upperTailTransition(100 - extremeTail)
  });
}

function currentFitPayload(trainingRows, current, fit, signal, config) {
  const points = trainingRows.map((row, index) => Object.freeze({
    date: row.date,
    return1d: row.return1d,
    observed: row[config.observedField],
    expected: fit.alpha + fit.beta * row.return1d,
    residual: fit.residuals[index],
    weight: fit.weights[index],
    role: "training"
  }));
  return Object.freeze({
    track: config.track,
    observedField: config.observedField,
    fitMethod: config.fitMethod,
    alpha: fit.alpha,
    beta: fit.beta,
    rollingR2: fit.r2,
    fitScore: fit.fitScore,
    trainingCount: trainingRows.length,
    trainingStart: trainingRows[0]?.date ?? null,
    trainingEnd: trainingRows.at(-1)?.date ?? null,
    trainingRows: Object.freeze(points),
    current: Object.freeze({
      date: current.date,
      return1d: current.return1d,
      observed: current[config.observedField],
      expected: signal.expected,
      residual: signal.residual,
      percentile: signal.percentile,
      state: signal.state,
      role: "current"
    }),
    residualCuts: empiricalTransitionCuts(fit.residuals, config.extremeTail),
    residualCutMethod: "empirical_cdf_transition_order_statistic"
  });
}

function validateHistoryRows(historyRows) {
  if (!Array.isArray(historyRows) || !historyRows.length) throw new Error("신호 이력 행이 필요합니다.");
  let previous = null;
  for (const row of historyRows) {
    const parsed = row?.date && /^\d{4}-\d{2}-\d{2}$/.test(row.date) ? new Date(`${row.date}T00:00:00Z`) : null;
    if (!parsed || Number.isNaN(parsed.getTime()) || parsed.toISOString().slice(0, 10) !== row.date) {
      throw new Error("신호 이력 날짜는 YYYY-MM-DD 형식이어야 합니다.");
    }
    if (previous != null && row.date <= previous) {
      throw new Error("신호 이력 날짜는 오름차순 고유값이어야 합니다.");
    }
    previous = row.date;
  }
}

function applyDynamicSignal(row, signal, config) {
  const fields = TRACK_FIELD_MAPPING[config.track];
  const cloned = {
    ...row,
    dynamicTrack: config.track,
    dynamicState: signal.state,
    dynamicPercentile: signal.percentile,
    dynamicTradeEligible: signal.tradeEligible,
    dynamicSignal: signal,
    [fields.state]: signal.state,
    [fields.percentile]: signal.percentile,
    [fields.eligible]: signal.tradeEligible
  };
  if (config.track === "robust") {
    Object.assign(cloned, {
      expected: signal.expected,
      residual: signal.residual,
      residualZ: signal.residualZ,
      rollingR2: signal.rollingR2,
      fitScore: signal.fitScore,
      quality: signal.quality
    });
  }
  return cloned;
}

/**
 * Refit one selected signal track for every history row.
 *
 * The fit at index t receives only rows [t-lookback, t); the current row is
 * scored after fitting and no future row can affect any earlier signal.
 */
export function computeDynamicSignals({ historyRows, focusDate = null, ...requestedConfig }) {
  validateHistoryRows(historyRows);
  const config = normalizeSignalConfig(requestedConfig);
  const requestedFocusDate = normalizeOptionalDate(focusDate, "분석 기준일");
  let focusIndex = historyRows.length - 1;
  if (requestedFocusDate) {
    while (focusIndex >= 0 && historyRows[focusIndex].date > requestedFocusDate) focusIndex -= 1;
    if (focusIndex < 0) throw new Error("분석 기준일 이전의 신호 이력이 없습니다.");
  }
  const rows = [];
  let currentFit = null;
  const qualityCounts = {};
  for (let index = 0; index < historyRows.length; index += 1) {
    const { signal, fit } = signalForRow(historyRows, index, config, { includeFitRows: index === focusIndex });
    rows.push(applyDynamicSignal(historyRows[index], signal, config));
    qualityCounts[signal.quality] = (qualityCounts[signal.quality] || 0) + 1;
    if (fit) currentFit = fit;
  }
  return Object.freeze({
    config,
    rows: Object.freeze(rows),
    signals: Object.freeze(rows.map((row) => row.dynamicSignal)),
    latest: rows.at(-1).dynamicSignal,
    focus: rows[focusIndex].dynamicSignal,
    requestedFocusDate,
    appliedFocusDate: historyRows[focusIndex].date,
    currentFit,
    qualityCounts: Object.freeze(qualityCounts),
    engineVersion: "browser-past-only-rolling-v1"
  });
}

function yieldToMainThread() {
  if (typeof globalThis.scheduler?.yield === "function") return globalThis.scheduler.yield();
  return new Promise((resolve) => setTimeout(resolve, 0));
}

/**
 * Refit the same signal path as computeDynamicSignals while periodically
 * yielding to the browser event loop. The progress callback receives frozen
 * { processed, total, ratio } snapshots without sharing mutable engine state.
 */
export async function computeDynamicSignalsAsync({
  historyRows,
  focusDate = null,
  onProgress = null,
  ...requestedConfig
}) {
  validateHistoryRows(historyRows);
  if (onProgress != null && typeof onProgress !== "function") {
    throw new Error("신호 재계산 진행률 콜백은 함수여야 합니다.");
  }
  const config = normalizeSignalConfig(requestedConfig);
  const requestedFocusDate = normalizeOptionalDate(focusDate, "분석 기준일");
  let focusIndex = historyRows.length - 1;
  if (requestedFocusDate) {
    while (focusIndex >= 0 && historyRows[focusIndex].date > requestedFocusDate) focusIndex -= 1;
    if (focusIndex < 0) throw new Error("분석 기준일 이전의 신호 이력이 없습니다.");
  }

  const rows = [];
  let currentFit = null;
  const qualityCounts = {};
  const total = historyRows.length;
  let lastYieldIndex = 0;
  let lastYieldAt = Date.now();
  const report = (processed) => onProgress?.(Object.freeze({ processed, total, ratio: processed / total }));
  report(0);

  for (let index = 0; index < total; index += 1) {
    const { signal, fit } = signalForRow(historyRows, index, config, { includeFitRows: index === focusIndex });
    rows.push(applyDynamicSignal(historyRows[index], signal, config));
    qualityCounts[signal.quality] = (qualityCounts[signal.quality] || 0) + 1;
    if (fit) currentFit = fit;

    const processed = index + 1;
    const now = Date.now();
    if (processed < total && (processed - lastYieldIndex >= 32 || now - lastYieldAt >= 12)) {
      report(processed);
      await yieldToMainThread();
      lastYieldIndex = processed;
      lastYieldAt = Date.now();
    }
  }
  report(total);

  return Object.freeze({
    config,
    rows: Object.freeze(rows),
    signals: Object.freeze(rows.map((row) => row.dynamicSignal)),
    latest: rows.at(-1).dynamicSignal,
    focus: rows[focusIndex].dynamicSignal,
    requestedFocusDate,
    appliedFocusDate: historyRows[focusIndex].date,
    currentFit,
    qualityCounts: Object.freeze(qualityCounts),
    engineVersion: "browser-past-only-rolling-v1"
  });
}

/** Refit one evaluation row without rebuilding the full cloned signal series. */
export function fitDynamicSignalAt({ historyRows, index, ...requestedConfig }) {
  validateHistoryRows(historyRows);
  if (!Number.isInteger(index) || index < 0 || index >= historyRows.length) {
    throw new Error("분석 행 인덱스가 신호 이력 범위를 벗어났습니다.");
  }
  const config = normalizeSignalConfig(requestedConfig);
  const { signal, fit } = signalForRow(historyRows, index, config, { includeFitRows: true });
  return Object.freeze({
    config,
    index,
    signal,
    currentFit: fit,
    engineVersion: "browser-past-only-rolling-v1"
  });
}

function normalizeOptionalDate(value, name) {
  if (value == null || value === "") return null;
  if (typeof value !== "string" || !/^\d{4}-\d{2}-\d{2}$/.test(value)) throw new Error(`${name}이 올바르지 않습니다.`);
  const date = new Date(`${value}T00:00:00Z`);
  if (Number.isNaN(date.getTime()) || date.toISOString().slice(0, 10) !== value) throw new Error(`${name}이 올바르지 않습니다.`);
  return value;
}

function eventSignal(row, track) {
  if (row.dynamicSignal?.track === track) return row.dynamicSignal;
  const fields = TRACK_FIELD_MAPPING[track];
  return {
    date: row.date,
    state: row[fields.state] ?? "unavailable",
    percentile: row[fields.percentile] ?? null,
    tradeEligible: row[fields.eligible] === true
  };
}

function extremeEntries(rows, track) {
  let previousValidState = null;
  const events = [];
  rows.forEach((row, index) => {
    const signal = eventSignal(row, track);
    if (!signal.tradeEligible) return;
    if (["extreme_fear", "extreme_greed"].includes(signal.state) && signal.state !== previousValidState) {
      events.push({ index, date: row.date, state: signal.state, percentile: signal.percentile });
    }
    previousValidState = signal.state;
  });
  return events;
}

function nonOverlapping20d(events) {
  let nextAllowed = -1;
  return events.filter((event) => {
    if (event.index < nextAllowed) return false;
    nextAllowed = event.index + 21;
    return true;
  });
}

function forwardEventRows(events, rows, priceField, horizons) {
  const prices = rows
    .filter((row) => finite(row[priceField]) && row[priceField] > 0)
    .map((row) => ({ date: row.date, close: row[priceField] }));
  const positions = new Map(prices.map((row, index) => [row.date, index]));
  return events.flatMap((event) => {
    const position = positions.get(event.date);
    if (position == null) return [];
    const forwardReturns = Object.fromEntries(horizons.map((horizon) => {
      const future = prices[position + horizon];
      return [String(horizon), future ? future.close / prices[position].close - 1 : null];
    }));
    return [{
      ...event,
      assetClose: prices[position].close,
      forwardReturns,
      ...Object.fromEntries(horizons.map((horizon) => [`return${horizon}d`, forwardReturns[String(horizon)]]))
    }];
  });
}

function seededRandom(seed) {
  let state = seed >>> 0;
  return () => {
    state += 0x6d2b79f5;
    let value = state;
    value = Math.imul(value ^ value >>> 15, value | 1);
    value ^= value + Math.imul(value ^ value >>> 7, value | 61);
    return ((value ^ value >>> 14) >>> 0) / 4_294_967_296;
  };
}

function bootstrapMeanCi(values, samples, random) {
  if (!values.length) return [null, null];
  if (values.length === 1) return [values[0], values[0]];
  const means = new Array(samples);
  for (let sample = 0; sample < samples; sample += 1) {
    let total = 0;
    for (let index = 0; index < values.length; index += 1) {
      total += values[Math.floor(random() * values.length)];
    }
    means[sample] = total / values.length;
  }
  return [quantile(means, 0.025), quantile(means, 0.975)];
}

function summarizeEvents(events, horizons, bootstrapSamples, seed) {
  const random = seededRandom(seed);
  return ["extreme_fear", "extreme_greed"].flatMap((state) => horizons.map((horizon) => {
    const values = events
      .filter((event) => event.state === state)
      .map((event) => event[`return${horizon}d`])
      .filter(finite);
    return {
      state,
      horizon,
      eventCount: values.length,
      mean: values.length ? mean(values) : null,
      median: values.length ? median(values) : null,
      positiveRate: values.length ? values.filter((value) => value > 0).length / values.length : null,
      meanCi95: bootstrapMeanCi(values, bootstrapSamples, random),
      bootstrapMethod: "iid_seeded_mulberry32",
      bootstrapSamples,
      bootstrapSeed: seed,
      smallSample: values.length < 20
    };
  }));
}

/** Build an event study from the dynamically refitted signal rows. */
export function runDynamicEventStudy({
  historyRows,
  track = DEFAULT_SIGNAL_CONFIG.track,
  asset = "KOSPI",
  sample = "nonOverlapping20d",
  startDate = null,
  endDate = null,
  horizons = DEFAULT_HORIZONS,
  bootstrapSamples = DEFAULT_BOOTSTRAP_SAMPLES,
  seed = DEFAULT_BOOTSTRAP_SEED
}) {
  validateHistoryRows(historyRows);
  if (!Object.hasOwn(TRACK_FIELD_MAPPING, track)) throw new Error("이벤트 연구 트랙이 올바르지 않습니다.");
  if (!Object.hasOwn(EVENT_ASSET_FIELDS, asset)) throw new Error("이벤트 연구 자산이 올바르지 않습니다.");
  if (!["all", "nonOverlapping20d"].includes(sample)) throw new Error("이벤트 표본 규칙이 올바르지 않습니다.");
  if (!Array.isArray(horizons) || !horizons.length || horizons.some((value) => !Number.isInteger(value) || value <= 0)) {
    throw new Error("이벤트 선행 기간은 양의 정수여야 합니다.");
  }
  const samples = normalizeNumber(bootstrapSamples, "bootstrap 횟수", 1, 100_000, { integer: true });
  const normalizedSeed = normalizeNumber(seed, "bootstrap seed", 0, 4_294_967_295, { integer: true });
  const from = normalizeOptionalDate(startDate, "이벤트 시작일");
  const to = normalizeOptionalDate(endDate, "이벤트 종료일");
  if (from && to && from > to) throw new Error("이벤트 시작일은 종료일보다 늦을 수 없습니다.");
  const allEntries = extremeEntries(historyRows, track).filter((event) => (!from || event.date >= from) && (!to || event.date <= to));
  const selectedEntries = sample === "all" ? allEntries : nonOverlapping20d(allEntries);
  const priceRows = to ? historyRows.filter((row) => row.date <= to) : historyRows;
  const events = forwardEventRows(selectedEntries, priceRows, EVENT_ASSET_FIELDS[asset], horizons);
  return Object.freeze({
    track,
    asset,
    sample,
    startDate: from,
    endDate: to,
    informationCutoffDate: to ?? historyRows.at(-1).date,
    horizons: Object.freeze([...horizons]),
    entryCountBeforeAssetAlignment: selectedEntries.length,
    eventCount: events.length,
    events: Object.freeze(events),
    summary: Object.freeze(summarizeEvents(events, horizons, samples, normalizedSeed)),
    bootstrap: Object.freeze({ method: "iid_seeded_mulberry32", samples, seed: normalizedSeed }),
    eventRule: "first_valid_entry_into_each_extreme_state",
    sampleRule: sample === "all" ? "all_extreme_entries" : "combined_extremes_non_overlapping_20_sessions"
  });
}
