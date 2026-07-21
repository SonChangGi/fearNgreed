import {
  ACTUAL_ETF_PAIRS,
  DEFAULT_LONG_EXIT_PERCENTILE,
  normalizeLongExitPercentile,
  runActualEtfPairScenario
} from "./strategy-engine.js?v=20260717-actual-etf-v7";
import {
  normalizeSignalConfig,
  computeDynamicSignals,
  computeDynamicSignalsAsync,
  fitDynamicSignalAt,
  runDynamicEventStudy
} from "./signal-engine.js?v=20260717-actual-etf-v7";
import { itemRatioAt, nearestItemIndexByRatio } from "./chart-navigation.js?v=20260720-analysis-usability-v12";

const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => [...document.querySelectorAll(selector)];

const labels = {
  extreme_fear: "극단적 공포", fear: "공포", neutral: "중립", greed: "탐욕", extreme_greed: "극단적 탐욕",
  unavailable: "산출 불가", cash: "현금", long: "롱 ETF", inverse: "인버스 ETF", short: "인버스 ETF", recovery: "사용자 청산선 회복", max_holding: "사용자 최대 보유기간", opposite_extreme: "반대 극단 ETF 교체",
  enter_next_open: "다음 거래일 시가 ETF 매수", exit_next_open: "다음 거래일 시가 청산", reverse_next_open: "다음 거래일 시가 ETF 교체", extreme_fear_entry: "극단 공포 최초 진입", extreme_greed_entry: "극단 탐욕 최초 진입", hold: "보유 유지"
};

const qualityLabels = {
  ok: "정상",
  low_model_fit: "낮은 적합도 · 신규 진입 차단",
  degraded: "주의",
  stale: "갱신 지연",
  unavailable: "산출 불가"
};

const DEGRADED_REASON_LABELS = Object.freeze({
  core_latest_common_date_alignment: "공급자 최신일 차이로 공통 거래일까지 계산",
  krx_credentials_missing: "KRX 인증정보 미설정",
  krx_open_api_key_missing: "KRX Open API 키 미설정",
  krx_login_credentials_missing: "KRX 로그인 인증정보 미설정",
  krx_official_latest_session_unavailable: "KRX 공식 최신 거래일 확인 실패",
  refresh_core_input_quality_failed: "최신 KOSPI·개인수급 기준일 미일치",
  refresh_end_before_published_data: "공개 기준일보다 과거인 갱신 요청 차단",
  refresh_data_as_of_regression: "공개 데이터 기준일 후퇴 차단",
  krx_open_api_unavailable: "KRX Open API 일시 이용 불가",
  authenticated_pykrx_unavailable: "인증 KRX 수급 경로 일시 이용 불가",
  yfinance_unavailable: "조정가격 연구 소스 일시 이용 불가",
  refresh_provider_failed: "공급자 갱신 실패",
  refresh_pipeline_failed: "파생 산출 갱신 실패"
});

function degradedReasonLabel(reason) {
  if (DEGRADED_REASON_LABELS[reason]) return DEGRADED_REASON_LABELS[reason];
  const reconciledTicker = String(reason).match(/^adjusted_history_gap_reconciled_(\d+)$/)?.[1];
  if (reconciledTicker) return `${reconciledTicker} 조정가격 누락일을 공식 KRX 세션으로 검증·보정`;
  if (/^price_crosscheck_/.test(reason)) return "공식 종가 교차검증 미통과";
  if (/^historical_etf_/.test(reason)) return "ETF 공식 이력 일부 이용 불가";
  if (/^official_etf_provider_disagreement_/.test(reason)) return "ETF 공식 경로 간 최근값 불일치";
  return "데이터 품질 점검 필요";
}

function qualityLabel(value) {
  return qualityLabels[value] || value || "미확인";
}

const fmt = {
  pct: (value, digits = 2) => value == null || !Number.isFinite(Number(value)) ? "—" : `${(Number(value) * 100).toFixed(digits)}%`,
  signedPct: (value, digits = 2) => value == null || !Number.isFinite(Number(value)) ? "—" : `${Number(value) >= 0 ? "+" : ""}${(Number(value) * 100).toFixed(digits)}%`,
  score: (value, digits = 1) => value == null || !Number.isFinite(Number(value)) ? "—" : Number(value).toFixed(digits),
  compact: (value) => value == null || !Number.isFinite(Number(value)) ? "—" : Intl.NumberFormat("ko-KR", { notation: "compact", maximumFractionDigits: 2 }).format(value),
  date: (value) => value ? new Intl.DateTimeFormat("ko-KR", { year: "numeric", month: "short", day: "numeric" }).format(new Date(`${value}T00:00:00`)) : "—",
  time: (value) => value && Number.isFinite(Date.parse(value)) ? new Intl.DateTimeFormat("ko-KR", { timeZone: "Asia/Seoul", hour: "2-digit", minute: "2-digit", hour12: false }).format(new Date(value)) : "—",
  multiple: (value, digits = 2) => value == null || !Number.isFinite(Number(value)) ? "—" : `${Number(value).toFixed(digits)}×`
};

const esc = (value) => String(value ?? "").replace(/[&<>"]/g, (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "\"": "&quot;" })[char]);

function pairMeta(pairId = store?.backtestProxy || "1x") {
  return ACTUAL_ETF_PAIRS[pairId] || ACTUAL_ETF_PAIRS["1x"];
}

function pairLabel(pairId = store?.backtestProxy || "1x", compact = false) {
  const pair = pairMeta(pairId);
  return compact
    ? `${pair.leverage}X · ${pair.longTicker}/${pair.inverseTicker}`
    : `${pair.leverage}X 실제 ETF · ${pair.longTicker} ${pair.longName} / ${pair.inverseTicker} ${pair.inverseName}`;
}

function inverseExposure(metrics = {}) {
  return Number(metrics.inverseExposure ?? metrics.shortExposure ?? 0);
}

function scenarioPosition(result) {
  return result?.latestPosition || result?.position || "unavailable";
}

function positionWithTicker(position, pairId = store?.backtestProxy || "1x") {
  const pair = pairMeta(pairId);
  if (position === "long") return `롱 ETF ${pair.longTicker}`;
  if (["inverse", "short"].includes(position)) return `인버스 ETF ${pair.inverseTicker}`;
  return labels[position] || position || labels.unavailable;
}

function heldInstrument(result) {
  const position = scenarioPosition(result);
  const pair = result?.pair || pairMeta();
  if (position === "long") return `${pair.longTicker} ${pair.longName || "롱 ETF"}`;
  if (["inverse", "short"].includes(position)) return `${pair.inverseTicker} ${pair.inverseName || "인버스 ETF"}`;
  return "현금";
}

const DEFAULT_CONTROLS = Object.freeze({
  window: "ytd",
  historyStart: "",
  historyEnd: "",
  model: "raw",
  eventAsset: "KOSPI",
  eventSample: "all",
  backtestProxy: "1x",
  backtestPolicy: "compare",
  backtestVariant: "raw_ols",
  backtestCost: 10,
  backtestPeriod: "common",
  longExitPercentile: DEFAULT_LONG_EXIT_PERCENTILE,
  signalLookback: 196,
  signalMinimumR2: 0.4,
  signalExtremeTail: 2,
  signalMaxHolding: 20
});

const CONTROL_STORAGE_KEY = "fearngreed-controls-v7";
const LEGACY_CONTROL_STORAGE_KEYS = Object.freeze([
  "fearngreed-controls-v6",
  "fearngreed-controls-v5",
  "fearngreed-controls-v4",
  "fearngreed-controls-v3",
  "fearngreed-controls-v2"
]);

const TRACK_VARIANTS = Object.freeze({
  robust: "scaled_huber",
  scaled: "scaled_ols",
  raw: "raw_ols"
});

const TRACK_FIELDS = Object.freeze({
  robust: { state: "state", percentile: "percentile", eligible: "tradeEligible" },
  scaled: { state: "scaledState", percentile: "scaledPercentile", eligible: "scaledTradeEligible" },
  raw: { state: "rawState", percentile: "rawPercentile", eligible: "rawTradeEligible" }
});

const CONTROL_QUERY = Object.freeze({
  window: "window",
  historyStart: "start",
  historyEnd: "end",
  model: "model",
  eventAsset: "eventAsset",
  eventSample: "eventSample",
  backtestProxy: "pair",
  backtestPolicy: "policy",
  backtestVariant: "strategy",
  backtestCost: "cost",
  backtestPeriod: "period",
  longExitPercentile: "exit",
  signalLookback: "lookback",
  signalMinimumR2: "minR2",
  signalExtremeTail: "tail",
  signalMaxHolding: "maxHold"
});

const CONTROL_ALLOWED = Object.freeze({
  window: ["1m", "3m", "6m", "ytd", "1y", "3y", "all", "custom"],
  model: ["robust", "scaled", "raw"],
  eventAsset: ["KOSPI", "226490", "069500"],
  eventSample: ["nonOverlapping20d", "all"],
  backtestProxy: ["1x", "2x"],
  backtestPolicy: ["compare", "long_cash", "long_inverse_cash"],
  backtestVariant: ["scaled_huber", "scaled_ols", "raw_ols", "disparity"],
  backtestCost: ["0", "5", "10", "20"],
  backtestPeriod: ["common", "full"]
});

let store = {
  summary: null,
  dashboard: null,
  history: null,
  strategyComparison: null,
  liveSignal: null,
  activeSeries: null,
  activeSignalMeta: null,
  ...DEFAULT_CONTROLS
};

async function loadJson(path) {
  const response = await fetch(path, { cache: "no-store" });
  if (!response.ok) throw new Error(`${path}: HTTP ${response.status}`);
  return response.json();
}

async function loadOptionalJson(path) {
  try {
    return await loadJson(path);
  } catch (_) {
    return null;
  }
}

function validateContracts(summary, dashboard, history, strategyComparison) {
  if (summary?.schemaVersion !== 1 || summary?.contract !== "quant-research-summary" || summary?.projectId !== "fearngreed") throw new Error("summary.json 계약이 올바르지 않습니다.");
  const methodology = summary?.methodologyVersion;
  if (!/^fear-flow-v\d+$/.test(methodology || "") || dashboard?.methodologyVersion !== methodology || history?.methodologyVersion !== methodology) throw new Error("공개 데이터 방법론 버전이 올바르지 않습니다.");
  if (dashboard?.schemaVersion !== 1 || history?.schemaVersion !== 1 || strategyComparison?.schemaVersion !== 1 || strategyComparison?.contract !== "fearngreed-strategy-comparison" || dashboard?.dataAsOf !== summary.dataAsOf || history?.dataAsOf !== summary.dataAsOf || strategyComparison?.dataAsOf !== summary.dataAsOf || strategyComparison?.methodologyVersion !== methodology) throw new Error("공개 데이터 스키마 또는 기준일이 올바르지 않습니다.");
  const dynamicControl = strategyComparison?.dynamicExitControl;
  const historyScenario = history?.strategyScenario;
  if (dynamicControl?.defaultLongExitPercentile !== DEFAULT_LONG_EXIT_PERCENTILE || dynamicControl?.minimum !== 50 || dynamicControl?.maximum !== 94 || dynamicControl?.shortExitFormula !== "100-longExitPercentile" || dynamicControl?.regressionRefit !== true || historyScenario?.defaultLongExitPercentile !== DEFAULT_LONG_EXIT_PERCENTILE || historyScenario?.browserMayRefitRegression !== true || historyScenario?.pastOnly !== true || historyScenario?.evaluationRangeSeparate !== true) throw new Error("사용자 동적 연구 시나리오 계약이 올바르지 않습니다.");
  const expectedPageDefaults = {
    lookback: DEFAULT_CONTROLS.signalLookback,
    minimumR2: DEFAULT_CONTROLS.signalMinimumR2,
    extremeTail: DEFAULT_CONTROLS.signalExtremeTail,
    maxHolding: DEFAULT_CONTROLS.signalMaxHolding
  };
  const contractDefaults = (scenario) => Object.fromEntries(
    Object.keys(expectedPageDefaults).map((key) => [key, scenario?.configurableInputs?.[key]?.default])
  );
  if (JSON.stringify(contractDefaults(dynamicControl)) !== JSON.stringify(expectedPageDefaults) || JSON.stringify(contractDefaults(historyScenario)) !== JSON.stringify(expectedPageDefaults)) throw new Error("페이지 기본 연구 설정 계약이 올바르지 않습니다.");
  const hasSeries = Array.isArray(history.series) || (Array.isArray(history.seriesColumns) && Array.isArray(history.seriesRows));
  const models = summary.primaryEntities?.[0]?.models || dashboard?.models || {};
  if (!["ok", "degraded", "stale", "unavailable"].includes(summary?.status?.state) || !Array.isArray(summary.primaryEntities) || summary.primaryEntities.length !== 1 || !models.scaled || !models.raw || !hasSeries || summary?.payload?.strategyComparisonUrl !== "./strategy-comparison.json") throw new Error("공개 데이터의 필수 계약이 없습니다.");
}

function validateLiveSignal(liveSignal, summary) {
  if (liveSignal == null) return null;
  if (liveSignal?.schemaVersion !== 1 || liveSignal?.contract !== "fearngreed-live-signal" || liveSignal?.projectId !== "fearngreed") throw new Error("빠른 신호 계약이 올바르지 않습니다.");
  if (liveSignal.methodologyVersion !== summary?.methodologyVersion) throw new Error("빠른 신호 방법론 버전이 확정 분석과 다릅니다.");
  if (!isIsoDate(liveSignal.signalDate) || liveSignal.inputRow?.date !== liveSignal.signalDate) throw new Error("빠른 신호 기준일이 올바르지 않습니다.");
  const historicalLive = liveSignal.signalDate <= summary.dataAsOf;
  if (liveSignal.sourceCutoff !== "regular-session-close-provisional" || !isIsoDate(liveSignal.historyDataAsOf) || liveSignal.historyDataAsOf >= liveSignal.signalDate || (!historicalLive && liveSignal.historyDataAsOf !== summary.dataAsOf)) throw new Error("빠른 신호 데이터 기준이 확정 분석과 다릅니다.");
  if (!Number.isFinite(Date.parse(liveSignal.generatedAt || "")) || !["provisional", "confirmed"].includes(liveSignal.phase)) throw new Error("빠른 신호 계산 단계가 올바르지 않습니다.");
  if (liveSignal.expectedConfirmationAt != null && !Number.isFinite(Date.parse(liveSignal.expectedConfirmationAt))) throw new Error("빠른 신호 확정 예정 시각이 올바르지 않습니다.");
  const quality = liveSignal.quality;
  if (!quality || !["ok", "degraded", "unavailable"].includes(quality.state) || typeof quality.tradeEligible !== "boolean" || !Array.isArray(quality.reasons)) throw new Error("빠른 신호 품질 상태가 올바르지 않습니다.");
  const requiredInputs = ["kospiClose", "return1d", "flowShare", "rawFlowTrillion", "disparity50", "mdd252"];
  if (!requiredInputs.every((field) => typeof liveSignal.inputRow?.[field] === "number" && Number.isFinite(liveSignal.inputRow[field]))) throw new Error("빠른 신호 입력값이 올바르지 않습니다.");
  const window = liveSignal.actionWindow;
  if (!window || !["after-hours-close", "next-open"].includes(window.mode) || !["open", "closed", "future", "not_open"].includes(window.state) || typeof window.executionGuaranteed !== "boolean") throw new Error("빠른 신호 활용 시간이 올바르지 않습니다.");
  const generatedAt = Date.parse(liveSignal.generatedAt);
  const opensAt = Date.parse(window.opensAt || "");
  const closesAt = Date.parse(window.closesAt || "");
  if (!Number.isFinite(opensAt) || !Number.isFinite(closesAt) || !(opensAt <= generatedAt && generatedAt < closesAt)) throw new Error("빠른 신호 수집 시각이 잠정 분석 시간 밖입니다.");
  return liveSignal;
}

function decodeHistory(history) {
  if (Array.isArray(history?.series)) return history;
  const columns = history?.seriesColumns;
  const rows = history?.seriesRows;
  if (!Array.isArray(columns) || !Array.isArray(rows)) return { ...history, series: [] };
  return {
    ...history,
    series: rows.map((values) => Object.fromEntries(columns.map((column, index) => [column, values[index]])))
  };
}

function stateFromValue(model) {
  if (model?.state || model?.signalState) return model.state || model.signalState;
  const value = model?.percentile ?? model?.sentimentPercentile;
  const tail = Number(store?.signalExtremeTail ?? DEFAULT_CONTROLS.signalExtremeTail);
  if (value == null) return "unavailable";
  if (value <= tail) return "extreme_fear";
  if (value <= 20) return "fear";
  if (value < 80) return "neutral";
  if (value < 100 - tail) return "greed";
  return "extreme_greed";
}

function effectiveStatus(summary) {
  const freshnessPassed = summary?.status?.sourceFreshnessPassed;
  const expectedDataAsOf = summary?.status?.expectedDataAsOf;
  const freshnessBasis = summary?.status?.freshnessBasis;
  if (summary?.status?.state === "unavailable") return "unavailable";
  if (freshnessBasis === "official_krx_latest_completed_session" && freshnessPassed === false) return "stale";
  if (expectedDataAsOf && summary.dataAsOf !== expectedDataAsOf) return "stale";
  const lastSuccessAt = Date.parse(summary?.automation?.lastSuccessAt || "");
  const fallbackDataDate = Date.parse(`${summary.dataAsOf}T00:00:00Z`);
  const freshnessTimestamp = Number.isFinite(lastSuccessAt) ? lastSuccessAt : fallbackDataDate;
  const ageDays = Math.floor((Date.now() - freshnessTimestamp) / 86_400_000);
  if (Number.isFinite(ageDays) && ageDays > summary.status.expectedFreshnessDays) return "stale";
  return summary.status.state;
}

function entity() {
  return store.summary?.primaryEntities?.[0] || {};
}

function activeHistoryRows() {
  return store.activeSeries || store.history?.series || [];
}

function selectedAnalysisRow() {
  const rows = activeHistoryRows();
  if (!rows.length) return null;
  const endDate = selectedWindowBounds().endDate;
  if (!endDate) return rows.at(-1);
  for (let index = rows.length - 1; index >= 0; index -= 1) {
    if (rows[index].date <= endDate) return rows[index];
  }
  return null;
}

function analysisEntity() {
  const published = entity();
  const row = selectedAnalysisRow();
  if (!row) return published;
  return {
    ...published,
    ...row,
    signalState: row.dynamicSignal?.state ?? published.signalState,
    sentimentPercentile: row.dynamicSignal?.percentile ?? published.sentimentPercentile,
    residualZ: row.dynamicSignal?.residualZ ?? published.residualZ,
    rollingR2: row.dynamicSignal?.rollingR2 ?? published.rollingR2,
    modelQuality: row.dynamicSignal?.quality ?? published.modelQuality
  };
}

function currentSignalConfig() {
  const signal = normalizeSignalConfig({
    track: store.model,
    lookback: store.signalLookback,
    minimumR2: store.signalMinimumR2,
    extremeTail: store.signalExtremeTail
  });
  const maxHolding = Number(store.signalMaxHolding);
  if (!Number.isInteger(maxHolding) || maxHolding < 1 || maxHolding > 60) throw new Error("최대 보유기간은 1~60 거래일 사이 정수여야 합니다.");
  return { ...signal, maxHolding };
}

function recomputeDynamicResearch() {
  const baseRows = store.history?.series || [];
  if (!baseRows.length) {
    store.activeSeries = null;
    store.activeSignalMeta = null;
    return;
  }
  const config = currentSignalConfig();
  const result = computeDynamicSignals({ historyRows: baseRows, track: store.model, ...config });
  const rows = Array.isArray(result) ? result : result?.rows;
  if (!Array.isArray(rows) || rows.length !== baseRows.length) throw new Error("동적 신호 이력이 원본 거래일과 일치하지 않습니다.");
  store.activeSeries = rows;
  store.activeSignalMeta = Array.isArray(result) ? null : result;
  scenarioCache.clear();
  dynamicEventCache.clear();
  dynamicEventErrors.clear();
  latestEventError = null;
}

async function recomputeDynamicResearchAsync({ onProgress = null } = {}) {
  const baseRows = store.history?.series || [];
  if (!baseRows.length) {
    store.activeSeries = null;
    store.activeSignalMeta = null;
    return;
  }
  const config = currentSignalConfig();
  const result = await computeDynamicSignalsAsync({
    historyRows: baseRows,
    track: store.model,
    ...config,
    onProgress
  });
  const rows = result?.rows;
  if (!Array.isArray(rows) || rows.length !== baseRows.length) throw new Error("동적 신호 이력이 원본 거래일과 일치하지 않습니다.");
  store.activeSeries = rows;
  store.activeSignalMeta = result;
  scenarioCache.clear();
  dynamicEventCache.clear();
  dynamicEventErrors.clear();
  latestEventError = null;
}

function primaryModelKind() {
  const declared = store.dashboard?.primaryModel || store.summary?.payload?.primaryModel || entity().primaryModel;
  if (["robust", "scaled", "raw"].includes(declared)) return declared;
  return modelPayload("robust") ? "robust" : "scaled";
}

function modelPayload(kind = store.model) {
  const base = kind === store.model ? analysisEntity() : entity();
  const dynamic = kind === store.model ? selectedAnalysisRow()?.dynamicSignal : null;
  if (dynamic) return { ...base, ...dynamic, model: kind, calculationSource: "browser_past_only_refit" };
  const published = store.dashboard?.models?.[kind] || base.models?.[kind] || store.history?.models?.[kind];
  if (published) return { ...base, ...published, model: kind };
  if (kind === "scaled") return {
    ...base,
    model: "scaled",
    state: base.signalState,
    percentile: base.sentimentPercentile,
    residualZ: base.residualZ,
    rollingR2: base.rollingR2,
    quality: base.modelQuality,
    tradeEligible: base.modelQuality === "ok"
  };
  return null;
}

function regressionPayload(kind = store.model) {
  const dynamic = kind === store.model ? selectedAnalysisRow()?.dynamicSignal : null;
  if (dynamic) return dynamic;
  const regression = store.dashboard?.regression || {};
  return regression[kind] || store.dashboard?.models?.[kind] || (kind === "scaled" ? regression : modelPayload(kind)) || {};
}

const modelAgreementCache = new Map();

function selectedModelAgreement() {
  const rows = store.history?.series || [];
  const selected = selectedAnalysisRow();
  if (!rows.length || !selected?.date) return { state: "unavailable", directions: {} };
  const index = rows.findIndex((row) => row.date === selected.date);
  if (index < 0) return { state: "unavailable", directions: {} };
  const key = [selected.date, store.signalLookback, store.signalMinimumR2, store.signalExtremeTail].join("|");
  if (modelAgreementCache.has(key)) return modelAgreementCache.get(key);
  const directions = {};
  for (const track of ["robust", "raw"]) {
    try {
      const signal = fitDynamicSignalAt({
        historyRows: rows,
        index,
        track,
        lookback: store.signalLookback,
        minimumR2: store.signalMinimumR2,
        extremeTail: store.signalExtremeTail
      }).signal;
      directions[track] = Number(signal?.residual) < 0 ? "fear" : Number(signal?.residual) > 0 ? "greed" : null;
    } catch (_) {
      directions[track] = null;
    }
  }
  const comparable = directions.robust && directions.raw;
  const result = {
    state: comparable && directions.robust !== directions.raw ? "mixed" : comparable ? "aligned" : "unavailable",
    directions
  };
  modelAgreementCache.set(key, result);
  return result;
}

function modelName(kind = store.model) {
  if (kind === "robust") return "실전 신호 · 강건 회귀";
  if (kind === "raw") return "PDF 원문 근사 · 절대 수급";
  return "OLS 기준선 · 규모 보정";
}

function compactModelName(kind = store.model) {
  return ({ robust: "실전 강건 회귀", scaled: "규모보정 OLS", raw: "절대수급 원문 근사" })[kind] || kind;
}

function modelRole(kind = store.model) {
  return ({ robust: "실전 신호", scaled: "연구 기준선", raw: "PDF 원문 근사" })[kind] || "비교 모형";
}

function pendingActionText(base) {
  if (!base.pendingAction) return base.pendingReason || "대기 신호 없음";
  return `${labels[base.pendingAction] || base.pendingAction}${base.pendingReason ? ` · ${base.pendingReason}` : ""}`;
}

function scenarioPositionSummary() {
  if (!store.history || !store.dashboard || !store.strategyComparison) return { value: labels.unavailable, note: "전략 입력을 읽는 중" };
  const { longCash, longInverse, primary } = selectedScenarioBundle();
  const bounds = primary?.window || primary?.range || {};
  const end = bounds.appliedEndDate || primary?.metrics?.end || selectedWindowBounds().endDate || store.summary?.dataAsOf;
  if (store.backtestPolicy === "compare" && longCash && longInverse) {
    const cashPosition = scenarioPosition(longCash);
    const inversePosition = scenarioPosition(longInverse);
    const value = `${labels[cashPosition] || cashPosition} / ${labels[inversePosition] || inversePosition}`;
    return { value, note: `롱/현금 · 롱/인버스/현금 · ${pairLabel(store.backtestProxy, true)} · ${end}` };
  }
  return {
    value: labels[scenarioPosition(primary)] || labels.unavailable,
    note: `${pairLabel(store.backtestProxy, true)} · ${heldInstrument(primary)} · 청산 ${store.longExitPercentile} · ${end}`
  };
}

function metric(label, value, note) {
  return `<article class="metric"><span>${esc(label)}</span><strong title="${esc(value)}">${esc(value)}</strong><small>${esc(note)}</small></article>`;
}

function bridgeStep(step, label, value, note, tone = "") {
  return `<li class="bridge-step ${esc(tone)}"><span>${esc(step)}</span><strong>${esc(label)}</strong><b>${esc(value)}</b><small>${esc(note)}</small></li>`;
}

function renderSignalBridge() {
  const base = analysisEntity();
  const model = modelPayload();
  const regression = regressionPayload();
  const state = stateFromValue(model);
  const isRaw = store.model === "raw";
  const actual = model?.observed ?? (isRaw ? base.rawFlowTrillion : base.flowShare);
  const regressionInputs = [regression.alpha, regression.beta, base.return1d];
  const expected = model?.expected ?? (regressionInputs.every((value) => value != null && Number.isFinite(Number(value))) ? Number(regression.alpha) + Number(regression.beta) * Number(base.return1d) : null);
  const residual = model?.residual;
  const unitFormat = isRaw ? (value) => Number.isFinite(Number(value)) ? `${fmt.score(value, 3)}조원` : "—" : (value) => fmt.pct(value, 3);
  const percentile = model?.percentile ?? model?.sentimentPercentile;
  const tone = state.includes("fear") ? "fear" : state.includes("greed") ? "greed" : state;
  $("#bridge-model-scope").textContent = `${modelRole()} · ${compactModelName()}`;
  $("#bridge-model-scope").className = `scope-badge ${store.model === "raw" ? "replica" : store.model === "robust" ? "practical" : "baseline"}`;
  $("#signal-bridge").innerHTML = `<ol>
    ${bridgeStep("1", "실제 수급", unitFormat(actual), isRaw ? "개인 순매수대금" : "KOSPI 거래대금 대비")}
    ${bridgeStep("2", "회귀 예상", unitFormat(expected), `KOSPI ${fmt.signedPct(base.return1d)}일 때`)}
    ${bridgeStep("3", "잔차", unitFormat(residual), "실제 − 예상", residual == null ? "" : Number(residual) < 0 ? "fear" : "greed")}
    ${bridgeStep("4", "과거 백분위", fmt.score(percentile, 1), "직전 학습 잔차 분포")}
    ${bridgeStep("5", "연구 상태", labels[state] || state, model?.tradeEligible === false ? "신규 거래 신호 차단" : `사용자 극단 꼬리 ${store.signalExtremeTail}% 적용`, tone)}
  </ol>`;
  const difference = residual == null ? Number.NaN : Number(residual);
  let interpretation = "현재 잔차를 산출할 수 없습니다.";
  if (Number.isFinite(difference)) {
    const direction = difference < 0 ? "예상보다 개인 수급이 더 약해 공포 방향" : difference > 0 ? "예상보다 개인 수급이 덜 약하거나 더 강해 탐욕 방향" : "예상과 일치해 중립 방향";
    interpretation = `${unitFormat(actual)}의 절대값만 보지 않습니다. 같은 날 수익률의 기대값 ${unitFormat(expected)}에 비해 ${direction}입니다.`;
  }
  $("#bridge-explanation").textContent = interpretation;
}

function selectedLiveSignal() {
  const live = store.liveSignal;
  const canonicalDate = store.summary?.dataAsOf;
  const rows = store.history?.series || [];
  const kstToday = new Date(Date.now() + 9 * 60 * 60 * 1000).toISOString().slice(0, 10);
  const generatedAt = Date.parse(live?.generatedAt || "");
  const generatedKstDate = Number.isFinite(generatedAt)
    ? new Date(generatedAt + 9 * 60 * 60 * 1000).toISOString().slice(0, 10)
    : null;
  if (!live || live.quality?.state === "unavailable" || !canonicalDate || live.signalDate !== kstToday || generatedKstDate !== live.signalDate || live.signalDate <= canonicalDate || !rows.length || rows.at(-1)?.date >= live.signalDate) return null;
  try {
    const inputRow = { ...live.inputRow, date: live.signalDate };
    const historyRows = [...rows, inputRow];
    const result = fitDynamicSignalAt({
      historyRows,
      index: historyRows.length - 1,
      ...currentSignalConfig()
    });
    return {
      live,
      signal: result.signal,
      tradeEligible: live.quality.state === "ok" && live.quality.tradeEligible && result.signal.tradeEligible
    };
  } catch (_) {
    return null;
  }
}

function liveActionText(view) {
  const { live, signal, tradeEligible } = view;
  if (live.phase === "confirmed") return "✓ 확정 신호 · 전체 분석 반영 중";
  const confirmation = live.expectedConfirmationAt ? `확정 ${fmt.time(live.expectedConfirmationAt)} 예정` : "확정 신호 대기";
  if (!tradeEligible) return `신규 진입 차단 · ${confirmation}`;
  const isExtreme = ["extreme_fear", "extreme_greed"].includes(signal.state);
  const window = live.actionWindow;
  const opensAt = Date.parse(window?.opensAt || "");
  const closesAt = Date.parse(window?.closesAt || "");
  const now = Date.now();
  const windowState = Number.isFinite(closesAt) && now >= closesAt
    ? "closed"
    : Number.isFinite(opensAt) && now < opensAt
      ? "not_open"
      : window?.state;
  if (!isExtreme) return `신규 진입 후보 없음 · ${confirmation}`;
  if (window?.mode === "after-hours-close" && windowState === "open") return `시간외 종가 ${fmt.time(window.closesAt)}까지 확인 가능`;
  if (window?.mode === "after-hours-close" && ["future", "not_open"].includes(windowState)) return `시간외 종가 ${fmt.time(window.opensAt)} 시작 대기`;
  if (window?.mode === "after-hours-close" && windowState === "closed") return "시간외 종가 종료 · 다음 거래일 시가 기준";
  if (window?.mode === "next-open") return "다음 거래일 시가 기준";
  return confirmation;
}

function renderLiveSignal() {
  const strip = $("#live-signal-strip");
  const view = selectedLiveSignal();
  if (!strip || !view) {
    if (strip) strip.hidden = true;
    return;
  }
  const { live, signal, tradeEligible } = view;
  const phase = live.phase === "confirmed" ? "confirmed" : "provisional";
  strip.hidden = false;
  strip.dataset.phase = phase;
  strip.dataset.tradeEligible = String(tradeEligible);
  const phaseBadge = $("#live-phase-badge");
  phaseBadge.textContent = phase === "confirmed" ? "✓ 확정" : "○ 잠정";
  phaseBadge.className = `phase-badge ${phase}`;
  $("#live-signal-title").textContent = phase === "confirmed" ? "당일 확정 신호" : "당일 빠른 신호";
  $("#live-signal-state").textContent = labels[signal.state] || signal.state;
  $("#live-signal-score").textContent = `백분위 ${fmt.score(signal.percentile, 1)}`;
  $("#live-signal-time").textContent = `${fmt.date(live.signalDate)} · ${fmt.time(live.generatedAt)} 계산`;
  $("#live-action-note").textContent = liveActionText(view);
  $("#live-confirmed-anchor").textContent = `차트·백테스트는 ${fmt.date(store.summary.dataAsOf)} 확정 기준`;
}

function renderHeader(scenarioBundle = selectedScenarioBundle()) {
  const summary = store.summary;
  const base = analysisEntity();
  const model = modelPayload();
  const status = effectiveStatus(summary);
  const state = status === "unavailable" ? "unavailable" : stateFromValue(model);
  const agreement = selectedModelAgreement();
  $("#current-state-label").textContent = `평가 종료일 연구 신호 · ${modelRole()}`;
  const badge = $("#status-badge");
  badge.textContent = `데이터 ${qualityLabel(status)}`;
  badge.className = `badge ${status}`;
  const confidence = $("#confidence-badge");
  const modelQuality = model?.quality || base.modelQuality;
  if (agreement.state === "mixed") {
    confidence.textContent = "절대수급·규모보정 방향 혼재 · 주의";
    confidence.className = "badge degraded";
  } else {
    confidence.textContent = `${modelRole()} · 품질 ${qualityLabel(modelQuality)}`;
    confidence.className = `badge ${modelQuality === "ok" ? "ok" : modelQuality === "low_model_fit" ? "degraded" : "neutral"}`;
  }
  $("#state").textContent = labels[state] || state;
  $("#signal-badge").textContent = model?.tradeEligible === false && state !== "unavailable" ? `${labels[state] || state} · 진입 차단` : labels[state] || state;
  $("#signal-badge").className = `state-badge ${state}${model?.tradeEligible === false ? " blocked" : ""}`;
  const expectedDataAsOf = summary.status.expectedDataAsOf;
  const freshnessMismatch = Boolean(expectedDataAsOf && expectedDataAsOf !== summary.dataAsOf);
  $("#asof").textContent = `평가 종료일 ${fmt.date(base.date || summary.dataAsOf)} · 데이터 최신일 ${fmt.date(summary.dataAsOf)}${freshnessMismatch ? ` · 공식 기대일 ${fmt.date(expectedDataAsOf)}` : ""}`;
  const reasons = summary.status.degradedReasons || [];
  const freshnessNote = freshnessMismatch
    ? `공식 최신 완료 세션 ${fmt.date(expectedDataAsOf)} · 현재 데이터 ${fmt.date(summary.dataAsOf)}`
    : summary.status.sourceFreshnessPassed === false
      ? "KRX 공식 최신 완료 세션 확인 대기"
      : status === "stale"
        ? `자동 갱신 마지막 성공 ${summary.automation?.lastSuccessAt ? new Intl.DateTimeFormat("ko-KR", { dateStyle: "medium", timeStyle: "short" }).format(new Date(summary.automation.lastSuccessAt)) : fmt.date(summary.dataAsOf)}`
        : "";
  const reasonNote = reasons.length ? `운영 주의: ${reasons.map(degradedReasonLabel).join(" · ")}` : "";
  $("#status-note").textContent = [freshnessNote, reasonNote].filter(Boolean).join(". ") || "핵심 공급자와 계산 품질 게이트 통과";

  const percentile = model?.percentile ?? model?.sentimentPercentile;
  const inputValue = store.model === "raw" ? `${fmt.score(base.rawFlowTrillion, 3)}조원` : fmt.pct(base.flowShare, 3);
  const inputNote = store.model === "raw" ? "개인 순매수대금 / 1조원" : "개인 순매수대금 ÷ KOSPI 거래대금";
  const position = scenarioPositionSummary();
  $("#metrics").innerHTML = [
    metric("감정 백분위", fmt.score(percentile), `${modelName()} · 직전 ${store.signalLookback}일 잔차 분포`),
    metric("잔차 z", fmt.score(model?.residualZ, 2), "median / 1.4826×MAD"),
    metric("롤링 R²", fmt.score(model?.rollingR2, 3), `현재일 제외 · 품질 기준 ${Number(store.signalMinimumR2).toFixed(2)}`),
    metric("KOSPI 1일", fmt.signedPct(base.return1d), "종가 대비 전 거래일"),
    metric(store.model === "raw" ? "개인 순매수대금" : "거래대금 대비 개인 순매수 비율", inputValue, inputNote),
    metric("50일 이격도", fmt.score(base.disparity50, 1), "100 = 50일 이동평균"),
    metric("평가 종료일 포지션", position.value, position.note),
    metric("252일 낙폭", fmt.pct(base.mdd252), "롤링 고점 대비")
  ].join("");
  renderSignalBridge();

  const beta = model?.beta ?? regressionPayload()?.beta;
  const adjustedSource = base.fieldSources?.adjustedProxy === "yfinance_adjusted_plus_scaled_krx_gap_rows"
    ? "yfinance 조정가 + KRX 검증 보정행"
    : "yfinance 조정가 · KRX 교차검증";
  const reconciliationItems = ["226490", "069500"].map((ticker) => {
    const report = store.dashboard?.crosschecks?.etf?.[ticker]?.historyReconciliation;
    if (!report) return "";
    return `<span><strong>${ticker} 세션:</strong> ${esc(fmt.compact(report.officialSessionCount))} · 보정 ${esc(report.filledCount)} · 미해결 ${esc(report.unresolvedCount)}</span>`;
  }).filter(Boolean);
  const selectedEvents = selectedEventSection();
  const selectedStrategy = scenarioBundle.primary;
  $("#quality-strip").innerHTML = [
    `<span><strong>데이터 기준일:</strong> ${esc(summary.dataAsOf)}</span>`,
    `<span><strong>선택 트랙:</strong> ${esc(modelName())}</span>`,
    `<span><strong>학습 설정:</strong> ${esc(store.signalLookback)}일 · R²≥${esc(Number(store.signalMinimumR2).toFixed(2))} · 꼬리 ${esc(store.signalExtremeTail)}%</span>`,
    `<span><strong>β:</strong> ${esc(fmt.score(beta, 4))}</span>`,
    `<span><strong>모형 품질:</strong> ${esc(qualityLabel(model?.quality || base.modelQuality))}</span>`,
    `<span><strong>가격:</strong> ${esc(base.sources?.price || base.sourceMode || "KRX")}</span>`,
    `<span><strong>수급:</strong> ${esc(base.sources?.flow || "pykrx 파생")}</span>`,
    `<span><strong>ETF 조정가:</strong> ${esc(base.sources?.adjustedPrice || adjustedSource)}</span>`,
    ...reconciliationItems,
    `<span><strong>관측치:</strong> ${esc(fmt.compact(summary.coverage.observationCount))}</span>`,
    `<span><strong>선택 사건:</strong> ${esc(selectedEvents?.eventCount ?? "—")}</span>`,
    `<span><strong>선택 완결 거래:</strong> ${esc(selectedStrategy?.metrics?.tradeCount ?? "—")}</span>`
  ].join("");
  $("#model-selection-note").textContent = `적용 시나리오 · ${modelName()} · 학습 ${store.signalLookback}일 · R²≥${Number(store.signalMinimumR2).toFixed(2)} · 극단 ${store.signalExtremeTail}% · 최대 보유 ${store.signalMaxHolding}일`;
  $("#scatter-model-scope").textContent = modelRole();
  const linkedRule = $("#linked-strategy-rule");
  if (linkedRule) linkedRule.textContent = `${modelName()} → ${variantLabel(variantKey(trackVariant(), store.backtestCost))}`;
}

function scale(domainMin, domainMax, rangeMin, rangeMax) {
  const span = domainMax - domainMin || 1;
  return (value) => rangeMin + (Number(value) - domainMin) / span * (rangeMax - rangeMin);
}

function pathSegments(rows, valueField, x, y) {
  const segments = [];
  let current = [];
  rows.forEach((row, index) => {
    const value = typeof valueField === "function" ? valueField(row) : row[valueField];
    if (value == null || !Number.isFinite(Number(value))) {
      if (current.length) segments.push(current.join(" "));
      current = [];
    } else current.push(`${current.length ? "L" : "M"} ${x(index).toFixed(2)} ${y(value).toFixed(2)}`);
  });
  if (current.length) segments.push(current.join(" "));
  return segments.map((segment) => `<path d="${segment}"/>`).join("");
}

function linearTicks(min, max, count = 5) {
  if (!Number.isFinite(min) || !Number.isFinite(max)) return [];
  if (min === max) return [min];
  return Array.from({ length: count }, (_, index) => min + (max - min) * index / (count - 1));
}

function niceTicks(min, max, count = 5) {
  if (!Number.isFinite(min) || !Number.isFinite(max)) return [];
  if (min === max) return [min];
  const rawStep = Math.abs(max - min) / Math.max(1, count - 1);
  const magnitude = 10 ** Math.floor(Math.log10(rawStep));
  const normalized = rawStep / magnitude;
  const factor = normalized <= 1 ? 1 : normalized <= 2 ? 2 : normalized <= 2.5 ? 2.5 : normalized <= 5 ? 5 : 10;
  const step = factor * magnitude;
  const first = Math.ceil(min / step - 1e-9) * step;
  const last = Math.floor(max / step + 1e-9) * step;
  const ticks = [];
  for (let value = first, guard = 0; value <= last + step * 1e-6 && guard < 24; value += step, guard += 1) {
    ticks.push(Number(value.toPrecision(12)));
  }
  return ticks.length >= 2 ? ticks : linearTicks(min, max, count);
}

function chartDateTickIndices(rows, maxTicks = 7) {
  if (!rows.length) return [];
  const count = Math.min(rows.length, Math.max(2, maxTicks));
  return [...new Set(Array.from({ length: count }, (_, index) => Math.round(index * (rows.length - 1) / Math.max(1, count - 1))))];
}

function chartDateLabel(value, firstDate, lastDate) {
  if (!isIsoDate(value)) return value || "—";
  const [year, month, day] = value.split("-").map(Number);
  const duration = isIsoDate(firstDate) && isIsoDate(lastDate)
    ? (new Date(`${lastDate}T00:00:00Z`) - new Date(`${firstDate}T00:00:00Z`)) / 86_400_000
    : 0;
  if (duration <= 120) return `${month}.${String(day).padStart(2, "0")}`;
  return `${year}.${String(month).padStart(2, "0")}`;
}

function chartDateAxis(rows, x, { top, bottom, labelY, maxTicks = 7 } = {}) {
  if (!rows.length) return "";
  const firstDate = rows[0].date;
  const lastDate = rows.at(-1).date;
  return chartDateTickIndices(rows, maxTicks).map((index, tickIndex, indices) => {
    const px = x(index);
    const anchor = tickIndex === 0 ? "start" : tickIndex === indices.length - 1 ? "end" : "middle";
    return `<line class="date-grid-line" x1="${px}" y1="${top}" x2="${px}" y2="${bottom}"/><text class="axis-label date-axis-label" x="${px}" y="${labelY}" text-anchor="${anchor}">${esc(chartDateLabel(rows[index].date, firstDate, lastDate))}</text>`;
  }).join("");
}

function equityReturn(value) {
  return value == null || !Number.isFinite(Number(value)) ? null : Number(value) - 1;
}

function isIsoDate(value) {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(String(value || ""))) return false;
  const date = new Date(`${value}T00:00:00Z`);
  return Number.isFinite(date.getTime()) && date.toISOString().slice(0, 10) === value;
}

function shiftIsoDate(value, { months = 0, years = 0 } = {}) {
  if (!isIsoDate(value)) return "";
  const [year, month, day] = value.split("-").map(Number);
  const target = new Date(Date.UTC(year, month - 1 + months + years * 12, 1));
  const lastDay = new Date(Date.UTC(target.getUTCFullYear(), target.getUTCMonth() + 1, 0)).getUTCDate();
  target.setUTCDate(Math.min(day, lastDay));
  return target.toISOString().slice(0, 10);
}

function historyStartForWindow(windowValue, latestDate, firstDate) {
  if (!isIsoDate(latestDate)) return firstDate || "";
  if (windowValue === "all") return firstDate || latestDate;
  if (windowValue === "ytd") return `${latestDate.slice(0, 4)}-01-01`;
  const shifts = {
    "1m": { months: -1 },
    "3m": { months: -3 },
    "6m": { months: -6 },
    "1y": { years: -1 },
    "3y": { years: -3 }
  };
  return shiftIsoDate(latestDate, shifts[windowValue] || shifts["3y"]);
}

function selectedHistory() {
  const rows = activeHistoryRows();
  if (!rows.length) return [];
  const firstDate = rows[0].date;
  const latestDate = rows.at(-1).date;
  const start = store.window === "custom" ? store.historyStart : historyStartForWindow(store.window, latestDate, firstDate);
  const end = store.window === "custom" ? store.historyEnd : latestDate;
  if (!isIsoDate(start) || !isIsoDate(end) || start > end) return [];
  return rows.filter((row) => row.date >= start && row.date <= end);
}

function selectedWindowBounds() {
  if (store.window === "all") return { startDate: null, endDate: null };
  if (store.window === "custom") {
    return { startDate: store.historyStart || null, endDate: store.historyEnd || null };
  }
  const rows = activeHistoryRows();
  const firstDate = rows[0]?.date || null;
  const latestDate = rows.at(-1)?.date || null;
  return {
    startDate: historyStartForWindow(store.window, latestDate, firstDate) || null,
    endDate: latestDate
  };
}

function trackVariant(kind = store.model) {
  return TRACK_VARIANTS[kind] || "scaled_huber";
}

function syncStrategyTrack() {
  store.backtestVariant = trackVariant();
}

function trackFields(kind = store.model) {
  return TRACK_FIELDS[kind] || TRACK_FIELDS.robust;
}

function rowTrackValue(row, field, kind = store.model) {
  return row?.[trackFields(kind)[field]];
}

function historyWindowLabel(value = store.window) {
  return ({ "1m": "최근 1개월", "3m": "최근 3개월", "6m": "최근 6개월", ytd: "연초 이후", "1y": "최근 1년", "3y": "최근 3년", all: "전체 기간", custom: "사용자 지정" })[value] || "선택 기간";
}

function setFormDirty(form, status, message) {
  if (!form || !status) return;
  form.dataset.dirty = "true";
  status.dataset.state = "dirty";
  status.textContent = message;
}

function clearFormDirty(form) {
  if (form) delete form.dataset.dirty;
}

function hasUnappliedDrafts() {
  return ["#exit-threshold-form", "#signal-settings-form", "#history-range-form"]
    .some((selector) => $(selector)?.dataset.dirty === "true");
}

function historyAppliedStatus(rows = selectedHistory()) {
  return rows.length
    ? `${historyWindowLabel()} · ${rows[0].date}–${rows.at(-1).date} · ${rows.length.toLocaleString()}거래일`
    : "선택한 기간에 표시할 거래일이 없습니다.";
}

function syncHistoryRangeControls(rows) {
  const allRows = activeHistoryRows();
  const firstDate = allRows[0]?.date || "";
  const latestDate = allRows.at(-1)?.date || "";
  const startInput = $("#history-start");
  const endInput = $("#history-end");
  const form = $("#history-range-form");
  if (!startInput || !endInput || !firstDate || !latestDate) return;
  [startInput, endInput].forEach((input) => {
    input.min = firstDate;
    input.max = latestDate;
  });
  if (form?.dataset.dirty === "true") return;
  const appliedStart = store.window === "custom" ? store.historyStart : (rows[0]?.date || firstDate);
  const appliedEnd = store.window === "custom" ? store.historyEnd : (rows.at(-1)?.date || latestDate);
  startInput.value = appliedStart;
  endInput.value = appliedEnd;
  startInput.setAttribute("aria-invalid", "false");
  endInput.setAttribute("aria-invalid", "false");
  form.dataset.appliedStart = appliedStart;
  form.dataset.appliedEnd = appliedEnd;
  $("#history-range-status").dataset.state = "ok";
  $("#history-range-status").textContent = historyAppliedStatus(rows);
}

function updateHistoryDraftState() {
  const form = $("#history-range-form");
  const startInput = $("#history-start");
  const endInput = $("#history-end");
  const status = $("#history-range-status");
  if (!form || !startInput || !endInput || !status) return false;
  startInput.setAttribute("aria-invalid", "false");
  endInput.setAttribute("aria-invalid", "false");
  const dirty = startInput.value !== (form.dataset.appliedStart || "") || endInput.value !== (form.dataset.appliedEnd || "");
  if (dirty) {
    const rows = selectedHistory();
    const applied = rows.length ? `${rows[0].date}–${rows.at(-1).date}` : "산출 불가";
    setFormDirty(form, status, `미적용 변경 · 현재 결과는 ${applied} 기준입니다.`);
  } else {
    clearFormDirty(form);
    status.dataset.state = "ok";
    status.textContent = historyAppliedStatus();
  }
  return dirty;
}

function setHistoryRangeError(message, input = null) {
  const status = $("#history-range-status");
  $("#history-range-form").dataset.dirty = "true";
  status.dataset.state = "error";
  status.textContent = message;
  if (input) {
    input.setAttribute("aria-invalid", "true");
    input.focus();
  }
}

function applyCustomHistoryRange(event) {
  event.preventDefault();
  const startInput = $("#history-start");
  const endInput = $("#history-end");
  const start = startInput.value;
  const end = endInput.value;
  const rows = activeHistoryRows();
  const firstDate = rows[0]?.date;
  const latestDate = rows.at(-1)?.date;
  if (!isIsoDate(start)) return setHistoryRangeError("올바른 시작일을 입력해 주세요.", startInput);
  if (!isIsoDate(end)) return setHistoryRangeError("올바른 종료일을 입력해 주세요.", endInput);
  if (start > end) return setHistoryRangeError("시작일은 종료일보다 늦을 수 없습니다.", startInput);
  if (start < firstDate || end > latestDate) return setHistoryRangeError(`공개 이력 범위 ${firstDate}–${latestDate} 안에서 선택해 주세요.`, start < firstDate ? startInput : endInput);
  if (!rows.some((row) => row.date >= start && row.date <= end)) return setHistoryRangeError("선택한 기간에 KOSPI 거래일이 없습니다.", startInput);
  const form = event.currentTarget;
  const status = $("#history-range-status");
  const snapshot = captureResearchSnapshot();
  try {
    startInput.setAttribute("aria-invalid", "false");
    endInput.setAttribute("aria-invalid", "false");
    store.window = "custom";
    store.historyStart = start;
    store.historyEnd = end;
    scenarioCache.clear();
    clearFormDirty(form);
    renderAll();
    persistControlState();
  } catch (error) {
    restoreResearchSnapshot(snapshot);
    form.dataset.dirty = "true";
    try { renderAll(); } catch (_) { /* keep the last complete DOM if rollback rendering also fails */ }
    startInput.value = start;
    endInput.value = end;
    form.dataset.dirty = "true";
    status.dataset.state = "error";
    status.textContent = `적용 실패 · 기존 기간 결과를 유지합니다. ${error instanceof Error ? error.message : ""}`.trim();
  }
}

function compactDate(value) {
  return value ? String(value).slice(2) : "—";
}

function dataTable(headers, rows, caption) {
  return `<table class="compact-data-table"><caption>${esc(caption)}</caption><thead><tr>${headers.map((header) => `<th scope="col">${esc(header)}</th>`).join("")}</tr></thead><tbody>${rows.map((row) => `<tr>${row.map((cell, index) => `<${index ? "td" : "th"}${index ? "" : ' scope="row"'}>${esc(cell)}</${index ? "td" : "th"}>`).join("")}</tr>`).join("")}</tbody></table>`;
}

function recentRows(rows, count = 12) {
  return rows.length <= count ? rows : rows.slice(-count);
}

function showTooltip(chart, text, point = null) {
  const tooltip = $("#tooltip");
  const box = chart.getBoundingClientRect();
  tooltip.textContent = text;
  tooltip.hidden = false;
  const tooltipBox = tooltip.getBoundingClientRect();
  const left = point?.x ?? box.left + 12;
  const top = point?.y ?? box.top + 12;
  tooltip.style.left = `${Math.min(window.innerWidth - tooltipBox.width - 8, Math.max(8, left + (point ? 14 : 0)))}px`;
  tooltip.style.top = `${Math.min(window.innerHeight - tooltipBox.height - 8, Math.max(8, top + (point ? 14 : 0)))}px`;
}

function attachChartNavigation(chart, items, formatter, geometry = {}) {
  const valid = items.filter(Boolean);
  const fallbackIndex = Math.max(0, valid.length - 1);
  const latestIndex = Number.isInteger(geometry.latestIndex) ? Math.max(0, Math.min(fallbackIndex, geometry.latestIndex)) : fallbackIndex;
  const initialIndex = Number.isInteger(geometry.initialIndex) ? Math.max(0, Math.min(fallbackIndex, geometry.initialIndex)) : latestIndex;
  chart._resizeObserver?.disconnect();
  chart._chartItems = valid;
  chart._chartIndex = initialIndex;
  chart.querySelectorAll(".chart-crosshair, .chart-crosshair-point").forEach((node) => node.remove());
  const crosshair = document.createElement("span");
  crosshair.className = "chart-crosshair";
  crosshair.setAttribute("aria-hidden", "true");
  chart.append(crosshair);
  const chartGeometry = () => {
    const svg = chart.querySelector("svg");
    if (!svg) return null;
    const svgRect = svg.getBoundingClientRect();
    const chartRect = chart.getBoundingClientRect();
    const viewBox = svg.viewBox?.baseVal;
    const width = Number(geometry.viewBoxWidth || viewBox?.width || 1);
    const left = Number(geometry.plotLeft ?? 0);
    const right = Number(geometry.plotRight ?? width);
    return { svgRect, chartRect, width, left, right };
  };
  const crosshairLeft = (ratio) => {
    const current = chartGeometry();
    if (!current) return ratio * chart.scrollWidth;
    const viewX = current.left + ratio * (current.right - current.left);
    return chart.scrollLeft + current.svgRect.left - current.chartRect.left + viewX / current.width * current.svgRect.width;
  };
  const positionCrosshair = () => {
    if (!valid.length) return 0;
    const ratio = itemRatioAt(valid, chart._chartIndex, geometry.itemRatio);
    const left = Math.max(0, crosshairLeft(ratio));
    crosshair.style.left = `${left}px`;
    return left;
  };
  const revealCrosshair = (left) => {
    if (chart.scrollWidth <= chart.clientWidth) return;
    const padding = Math.min(36, chart.clientWidth * .12);
    if (left < chart.scrollLeft + padding) chart.scrollLeft = Math.max(0, left - padding);
    else if (left > chart.scrollLeft + chart.clientWidth - padding) chart.scrollLeft = Math.min(chart.scrollWidth - chart.clientWidth, left - chart.clientWidth + padding);
  };
  const selectIndex = (index, point = null) => {
    if (!valid.length) return;
    chart._chartIndex = Math.max(0, Math.min(valid.length - 1, index));
    const left = positionCrosshair();
    if (!point) revealCrosshair(left);
    chart.classList.add("is-exploring");
    const text = formatter(valid[chart._chartIndex], chart._chartIndex);
    showTooltip(chart, text, point);
    chart.setAttribute("aria-valuetext", text);
    if (typeof geometry.onSelect === "function") geometry.onSelect(valid[chart._chartIndex], chart._chartIndex);
  };
  chart.onfocus = () => {
    if (valid.length) selectIndex(chart._chartIndex);
    else showTooltip(chart, chart.getAttribute("aria-label") || "차트");
  };
  const selectPointer = (event) => {
    if (!valid.length) return;
    const current = chartGeometry();
    if (!current) return;
    const viewX = (event.clientX - current.svgRect.left) / Math.max(1, current.svgRect.width) * current.width;
    const ratio = Math.max(0, Math.min(1, (viewX - current.left) / Math.max(1, current.right - current.left)));
    selectIndex(nearestItemIndexByRatio(valid, ratio, geometry.itemRatio), { x: event.clientX, y: event.clientY });
  };
  chart.onpointermove = selectPointer;
  chart.onpointerdown = (event) => {
    if (event.pointerType !== "mouse") {
      chart.focus({ preventScroll: true });
      selectPointer(event);
    }
  };
  chart.onblur = () => { $("#tooltip").hidden = true; chart.classList.remove("is-exploring"); };
  chart.onmouseleave = () => {
    if (document.activeElement !== chart) {
      $("#tooltip").hidden = true;
      chart.classList.remove("is-exploring");
    }
  };
  chart.onkeydown = (event) => {
    if (!valid.length || !["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
    event.preventDefault();
    let next = chart._chartIndex;
    if (event.key === "Home") next = 0;
    else if (event.key === "End") next = valid.length - 1;
    else if (event.key === "ArrowLeft") next -= 1;
    else next += 1;
    selectIndex(next);
  };
  chart._selectLatest = () => selectIndex(latestIndex);
  if (valid.length && typeof geometry.onSelect === "function") geometry.onSelect(valid[initialIndex], initialIndex);
  const alignLatest = () => {
    if (chart.clientWidth <= 0 || chart.scrollWidth <= chart.clientWidth) return;
    chart.scrollLeft = chart.scrollWidth - chart.clientWidth;
  };
  requestAnimationFrame(alignLatest);
  if (typeof ResizeObserver !== "undefined") {
    const observer = new ResizeObserver(() => {
      const left = positionCrosshair();
      if (chart._chartIndex === latestIndex) alignLatest();
      else revealCrosshair(left);
    });
    observer.observe(chart);
    chart._resizeObserver = observer;
  }
}

function attachScatterNavigation(chart, items, formatter, viewBox) {
  const valid = items.filter((item) => Number.isFinite(item.plotX) && Number.isFinite(item.plotY));
  chart._chartItems = valid;
  const currentIndex = valid.findIndex((item) => item.row.role === "current");
  chart._chartIndex = currentIndex >= 0 ? currentIndex : Math.max(0, valid.length - 1);
  chart.querySelectorAll(".chart-crosshair, .chart-crosshair-point").forEach((node) => node.remove());
  const marker = document.createElement("span");
  marker.className = "chart-crosshair-point";
  marker.setAttribute("aria-hidden", "true");
  chart.append(marker);

  const markerPosition = (item) => {
    const svg = chart.querySelector("svg");
    if (!svg) return { left: 0, top: 0 };
    const svgRect = svg.getBoundingClientRect();
    const chartRect = chart.getBoundingClientRect();
    return {
      left: chart.scrollLeft + svgRect.left - chartRect.left + item.plotX / viewBox.width * svgRect.width,
      top: chart.scrollTop + svgRect.top - chartRect.top + item.plotY / viewBox.height * svgRect.height
    };
  };
  const selectIndex = (index, point = null) => {
    if (!valid.length) return;
    chart._chartIndex = Math.max(0, Math.min(valid.length - 1, index));
    const item = valid[chart._chartIndex];
    const position = markerPosition(item);
    marker.style.left = `${position.left}px`;
    marker.style.top = `${position.top}px`;
    chart.classList.add("is-exploring");
    const text = formatter(item.row, chart._chartIndex);
    showTooltip(chart, text, point);
    chart.setAttribute("aria-valuetext", text);
  };
  const nearestIndex = (event) => {
    const svg = chart.querySelector("svg");
    if (!svg || !valid.length) return -1;
    const box = svg.getBoundingClientRect();
    const pointerX = (event.clientX - box.left) / Math.max(1, box.width) * viewBox.width;
    const pointerY = (event.clientY - box.top) / Math.max(1, box.height) * viewBox.height;
    let nearest = 0;
    let distance = Number.POSITIVE_INFINITY;
    valid.forEach((item, index) => {
      const candidate = (item.plotX - pointerX) ** 2 + (item.plotY - pointerY) ** 2;
      if (candidate < distance) {
        distance = candidate;
        nearest = index;
      }
    });
    return nearest;
  };
  chart.onfocus = () => valid.length ? selectIndex(chart._chartIndex) : showTooltip(chart, chart.getAttribute("aria-label") || "산점도");
  chart.onpointermove = (event) => selectIndex(nearestIndex(event), { x: event.clientX, y: event.clientY });
  chart.onpointerdown = (event) => {
    if (event.pointerType !== "mouse") {
      chart.focus({ preventScroll: true });
      selectIndex(nearestIndex(event), { x: event.clientX, y: event.clientY });
    }
  };
  chart.onblur = () => { $("#tooltip").hidden = true; chart.classList.remove("is-exploring"); };
  chart.onmouseleave = () => {
    if (document.activeElement !== chart) {
      $("#tooltip").hidden = true;
      chart.classList.remove("is-exploring");
    }
  };
  chart.onkeydown = (event) => {
    if (!valid.length || !["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
    event.preventDefault();
    const next = event.key === "Home" ? 0 : event.key === "End" ? valid.length - 1 : chart._chartIndex + (event.key === "ArrowLeft" ? -1 : 1);
    selectIndex(next);
  };
}

function sortableValue(text) {
  const value = String(text || "").trim().replace(/,/g, "");
  const date = value.match(/^\d{4}-\d{2}-\d{2}/);
  if (date) return { type: "number", value: Date.parse(`${date[0]}T00:00:00Z`) };
  const number = Number.parseFloat(value.replace(/[+%×*일조원]/g, ""));
  if (Number.isFinite(number) && /\d/.test(value)) return { type: "number", value: number };
  return { type: "text", value: value.toLocaleLowerCase("ko-KR") };
}

function sortTable(table, column, direction) {
  const body = table.tBodies[0];
  if (!body) return;
  const rows = [...body.rows];
  const sign = direction === "ascending" ? 1 : -1;
  rows.sort((a, b) => {
    const left = sortableValue(a.cells[column]?.textContent);
    const right = sortableValue(b.cells[column]?.textContent);
    if (left.type === "number" && right.type === "number") return (left.value - right.value) * sign;
    return String(left.value).localeCompare(String(right.value), "ko-KR", { numeric: true }) * sign;
  });
  rows.forEach((row) => body.append(row));
  [...table.tHead?.rows?.[0]?.cells || []].forEach((header, index) => header.setAttribute("aria-sort", index === column ? direction : "none"));
}

function resetTableSort(table) {
  [...table?.tHead?.rows?.[0]?.cells || []].forEach((header) => header.setAttribute("aria-sort", "none"));
}

function enhanceTables() {
  $$(".table-scroll").forEach((region) => {
    region.setAttribute("role", "region");
    region.setAttribute("tabindex", "0");
    const caption = region.querySelector("caption")?.textContent?.trim();
    if (caption) region.setAttribute("aria-label", caption);
  });
  $$("table:not(.compact-data-table)").forEach((table) => {
    [...table.tHead?.rows?.[0]?.cells || []].forEach((header, column) => {
      if (header.querySelector(".table-sort")) return;
      const label = header.textContent.trim();
      header.textContent = "";
      header.setAttribute("aria-sort", "none");
      const button = document.createElement("button");
      button.type = "button";
      button.className = "table-sort";
      button.textContent = label;
      button.addEventListener("click", () => {
        const direction = header.getAttribute("aria-sort") === "ascending" ? "descending" : "ascending";
        sortTable(table, column, direction);
      });
      header.append(button);
    });
  });
}

function applyTableFilter(input) {
  if (!input) return;
  const table = document.getElementById(input.dataset.filterTable);
  if (!table?.tBodies?.[0]) return;
  const query = input.value.trim().toLocaleLowerCase("ko-KR");
  const rows = [...table.tBodies[0].rows];
  let visible = 0;
  rows.forEach((row) => {
    const matched = !query || row.textContent.toLocaleLowerCase("ko-KR").includes(query);
    row.hidden = !matched;
    if (matched) visible += 1;
  });
  const status = document.getElementById(`${input.id}-status`);
  if (status) status.textContent = `${visible}/${rows.length}건`;
}

function exportTable(tableId) {
  const table = document.getElementById(tableId);
  if (!table) return;
  const quote = (value) => `"${String(value).replace(/"/g, '""')}"`;
  const lines = [...table.rows].filter((row) => !row.hidden).map((row) => [...row.cells].map((cell) => quote(cell.innerText.replace(/[↕↑↓]\s*$/, "").trim())).join(","));
  const blob = new Blob(["\ufeff", lines.join("\n")], { type: "text/csv;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = `fearngreed-${tableId}-${store.summary?.dataAsOf || "data"}.csv`;
  link.click();
  URL.revokeObjectURL(url);
}

function bindTableTools() {
  $$('[data-filter-table]').forEach((input) => input.addEventListener("input", () => applyTableFilter(input)));
  $$('[data-export-table]').forEach((button) => button.addEventListener("click", () => exportTable(button.dataset.exportTable)));
  enhanceTables();
}

function initializeSectionNav() {
  if (!("IntersectionObserver" in window)) return;
  const links = $$('[data-section-link]');
  const sections = links.map((link) => document.getElementById(link.dataset.sectionLink)).filter(Boolean);
  const observer = new IntersectionObserver((entries) => {
    const visible = entries.filter((entry) => entry.isIntersecting).sort((a, b) => b.intersectionRatio - a.intersectionRatio)[0];
    if (!visible) return;
    links.forEach((link) => link.classList.toggle("is-current", link.dataset.sectionLink === visible.target.id));
  }, { rootMargin: "-22% 0px -62%", threshold: [0, .15, .4] });
  sections.forEach((section) => observer.observe(section));
}

function extremeSignalMap(rows) {
  const fields = trackFields();
  const selectedDates = new Set(rows.map((row) => row.date));
  const signals = new Map();
  let previousValidState = null;
  for (const row of activeHistoryRows()) {
    if (row[fields.eligible] !== true) continue;
    const state = row[fields.state];
    if (selectedDates.has(row.date) && ["extreme_fear", "extreme_greed"].includes(state) && state !== previousValidState) {
      signals.set(row.date, { date: row.date, state, percentile: row[fields.percentile] });
    }
    previousValidState = state;
  }
  return signals;
}

function scenarioSessions(result) {
  const ledger = Array.isArray(result?.ledger) ? result.ledger : [];
  const byDate = new Map(ledger.map((row) => [row.date || row.actionDate, row]));
  return (result?.equity || []).map((row) => ({ ...row, ...(byDate.get(row.date) || {}) }));
}

function scenarioActions(result, policy) {
  if (Array.isArray(result?.actions)) return result.actions.map((action) => ({ ...action, policy }));
  return scenarioSessions(result).filter((row) => row.executedAction || row.action).map((row) => ({
    ...row,
    policy,
    action: row.executedAction || row.action,
    executionDate: row.executionDate || row.actionDate || row.date,
    signalDate: row.sourceSignalDate || row.signalDate
  }));
}

function normalizedActionLabel(action) {
  const name = action.type || action.action || action.executedAction;
  const from = action.fromPosition || action.from || "cash";
  const to = action.toPosition || action.to || action.positionAfterOpen;
  const fromText = `${labels[from] || from}${action.fromTicker ? ` ${action.fromTicker}` : ""}`;
  const toText = `${labels[to] || to}${action.toTicker ? ` ${action.toTicker}` : ""}`;
  if (name === "reverse" || name === "reversal") return `${fromText}→${toText} 교체`;
  if (name === "enter" || name === "entry") return `${toText} 매수`;
  if (name === "exit") return `${fromText} 청산`;
  return labels[name] || name || "체결";
}

function historyPolicyAction(row, policy) {
  return (row?.actions || []).filter((item) => item.policy === policy).map(normalizedActionLabel).join(" / ") || "체결 없음";
}

function renderHistorySelectedSnapshot(row, { periodEnd, showLongCash, showLongShort, pair }) {
  const root = $("#history-selected-snapshot");
  const container = $("#history-selected-content");
  if (!root || !container) return;
  if (!row) {
    root.dataset.context = "empty";
    container.innerHTML = '<div class="history-selected-placeholder">선택 기간의 차트 스냅샷을 준비할 수 없습니다.</div>';
    return;
  }
  const isPeriodEnd = row.date === periodEnd;
  const state = labels[row.trackState] || row.trackState || "미확인";
  const percentile = Number.isFinite(Number(row.trackPercentile)) ? `${fmt.score(row.trackPercentile, 1)} 백분위` : "백분위 —";
  const kospi = Number(row.kospiClose ?? row.kospi);
  const cards = [
    `<section><span>KOSPI 종가 · 상태</span><strong>${esc(Number.isFinite(kospi) ? Math.round(kospi).toLocaleString() : "—")}</strong><small>${esc(`${state} · ${percentile}`)}</small></section>`,
    ...(showLongCash ? [`<section><span>롱 / 현금 · 선택일까지</span><strong>${esc(fmt.signedPct(row.longCashReturn))}</strong><small>${esc(`${positionWithTicker(row.longCashPosition)} · ${historyPolicyAction(row, "long_cash")}`)}</small><small>선택일까지 MDD ${esc(fmt.pct(row.longCashMddToDate))}</small></section>`] : []),
    ...(showLongShort ? [`<section><span>롱 / 인버스 / 현금 · 선택일까지</span><strong>${esc(fmt.signedPct(row.longShortReturn))}</strong><small>${esc(`${positionWithTicker(row.longShortPosition)} · ${historyPolicyAction(row, "long_inverse_cash")}`)}</small><small>선택일까지 MDD ${esc(fmt.pct(row.longShortMddToDate))}</small></section>`] : []),
    `<section><span>${esc(pair.longTicker)} 매수·보유 · 선택일까지</span><strong>${esc(fmt.signedPct(row.buyHoldReturn))}</strong><small>선택일까지 MDD ${esc(fmt.pct(row.buyHoldMddToDate))}</small><small>평가 시작일을 0%로 재기준화</small></section>`
  ];
  root.dataset.context = isPeriodEnd ? "period-end" : "selected-date";
  container.innerHTML = `<div class="history-selected-head"><div><span>차트 선택일</span><strong>${esc(row.date)}</strong><em>${isPeriodEnd ? "평가 종료일" : "기간 내 탐색"}</em></div><p>${isPeriodEnd ? "성과 카드·표와 같은 종료일입니다." : `이 날짜까지의 누적성과입니다. 성과 카드·표 기준은 ${esc(periodEnd)}입니다.`}</p></div><div class="history-selected-grid">${cards.join("")}</div>`;
}

function renderHistory(scenarioBundle = selectedScenarioBundle()) {
  const rows = selectedHistory().filter((row) => row.kospiClose != null || row.kospi != null);
  const modelKind = store.model;
  $("#history-model-scope").textContent = `${modelRole(modelKind)} · ${policyLabel()} · 청산 ${store.longExitPercentile}`;
  $("#history-model-scope").className = `scope-badge ${modelKind === "raw" ? "replica" : modelKind === "robust" ? "practical" : "baseline"}`;
  const showLongCash = store.backtestPolicy !== "long_inverse_cash";
  const showLongShort = store.backtestPolicy !== "long_cash";
  $("#history-legend-long-cash").hidden = !showLongCash;
  $("#history-legend-long-inverse").hidden = !showLongShort;
  $("#history-legend-inverse-entry").hidden = !showLongShort;
  $("#history-legend-reversal").hidden = !showLongShort;
  const pair = pairMeta();
  $("#history-legend-buyhold").innerHTML = `<i class="legend-line buyhold"></i>${esc(pair.longTicker)} ${esc(pair.longName)} 매수·보유`;
  syncHistoryRangeControls(rows);
  const container = $("#history-chart");
  const { longCash, longShort, primary } = scenarioBundle;
  if (rows.length < 8 || !primary?.metrics) {
    $("#history-chart-meta").innerHTML = `<span><strong>표시 상태</strong><b>유효 거래일 부족</b></span><span><strong>필요 조건</strong><b>8거래일 이상</b></span>`;
    $("#history-exposure-note").innerHTML = "<strong>기간을 넓혀 주세요.</strong><span>통합 차트와 과거검증은 최소 8개 유효 거래일이 필요합니다.</span>";
    renderHistorySelectedSnapshot(null, { periodEnd: "", showLongCash, showLongShort, pair });
    const tableRows = rows.map((row) => [row.date, Number(row.kospiClose ?? row.kospi).toLocaleString(), labels[rowTrackValue(row, "state")] || rowTrackValue(row, "state"), "—", "—"]);
    $("#history-data-table").innerHTML = dataTable(["날짜", "KOSPI", "선택 트랙 상태", "포지션", "체결"], tableRows, `선택 기간 ${tableRows.length}개 관측값`);
    return showEmpty(container, "통합 분석을 표시하려면 유효 기간을 넓혀 주세요.");
  }
  const m = primary.metrics;

  const primarySessions = scenarioSessions(primary);
  const primaryByDate = new Map(primarySessions.map((row) => [row.date, row]));
  const longCashByDate = new Map(scenarioSessions(longCash).map((row) => [row.date, row]));
  const longShortByDate = new Map(scenarioSessions(longShort).map((row) => [row.date, row]));
  const signals = extremeSignalMap(rows);
  const policies = store.backtestPolicy === "long_cash" ? [["long_cash", longCash]] : store.backtestPolicy === "long_inverse_cash" ? [["long_inverse_cash", longShort]] : [["long_cash", longCash], ["long_inverse_cash", longShort]];
  const actions = policies.flatMap(([policy, result]) => scenarioActions(result, policy));
  const actionsByDate = new Map();
  for (const action of actions) {
    const date = action.executionDate || action.actionDate || action.date;
    if (!date) continue;
    if (!actionsByDate.has(date)) actionsByDate.set(date, []);
    actionsByDate.get(date).push(action);
  }
  const plotRows = rows.map((row) => {
    const primaryRow = primaryByDate.get(row.date) || {};
    const longCashRow = longCashByDate.get(row.date) || {};
    const longShortRow = longShortByDate.get(row.date) || {};
    return {
      ...row,
      trackState: rowTrackValue(row, "state"),
      trackPercentile: rowTrackValue(row, "percentile"),
      position: primaryRow.position || primaryRow.positionAfterOpen || "unavailable",
      longCashPosition: longCashRow.position || longCashRow.positionAfterOpen || "unavailable",
      longShortPosition: longShortRow.position || longShortRow.positionAfterOpen || "unavailable",
      primaryValue: primaryRow.value,
      buyHoldValue: primaryRow.buyHoldValue,
      longCashValue: longCashRow.value,
      longShortValue: longShortRow.value,
      longCashDrawdown: longCashRow.drawdown,
      longShortDrawdown: longShortRow.drawdown,
      buyHoldDrawdown: primaryRow.buyHoldDrawdown,
      signal: signals.get(row.date),
      actions: actionsByDate.get(row.date) || []
    };
  });

  const runningMdd = { longCash: null, longShort: null, buyHold: null };
  plotRows.forEach((row) => {
    row.longCashReturn = equityReturn(row.longCashValue);
    row.longShortReturn = equityReturn(row.longShortValue);
    row.buyHoldReturn = equityReturn(row.buyHoldValue);
    if (Number.isFinite(Number(row.longCashDrawdown))) runningMdd.longCash = Math.min(runningMdd.longCash ?? 0, Number(row.longCashDrawdown));
    if (Number.isFinite(Number(row.longShortDrawdown))) runningMdd.longShort = Math.min(runningMdd.longShort ?? 0, Number(row.longShortDrawdown));
    if (Number.isFinite(Number(row.buyHoldDrawdown))) runningMdd.buyHold = Math.min(runningMdd.buyHold ?? 0, Number(row.buyHoldDrawdown));
    row.longCashMddToDate = runningMdd.longCash;
    row.longShortMddToDate = runningMdd.longShort;
    row.buyHoldMddToDate = runningMdd.buyHold;
  });
  const visibleReturnFields = [
    ...(showLongCash ? ["longCashReturn"] : []),
    ...(showLongShort ? ["longShortReturn"] : []),
    "buyHoldReturn"
  ];
  const priceValues = plotRows.map((row) => Number(row.kospiClose ?? row.kospi)).filter(Number.isFinite);
  const strategyValues = plotRows.flatMap((row) => visibleReturnFields.map((field) => row[field])).map(Number).filter(Number.isFinite);
  const w = 1120, h = 700;
  const p = { l: 120, r: 132, priceTop: 48, priceBottom: 330, laneTitle: 360, equityTop: 482, equityBottom: 634, dateLabel: 661, xTitle: 688 };
  const plotRight = w - p.r;
  const min = Math.min(...priceValues), max = Math.max(...priceValues), pad = (max - min || 1) * .08;
  const equityMin0 = Math.min(...strategyValues), equityMax0 = Math.max(...strategyValues), equityPad = (equityMax0 - equityMin0 || .1) * .08;
  const x = scale(0, plotRows.length - 1, p.l, plotRight);
  const y = scale(min - pad, max + pad, p.priceBottom, p.priceTop);
  const equityY = scale(equityMin0 - equityPad, equityMax0 + equityPad, p.equityBottom, p.equityTop);
  const laneDefs = store.backtestPolicy === "compare"
    ? [
      { policy: "long_cash", label: "롱 / 현금", field: "longCashPosition", top: 380, bottom: 404 },
      { policy: "long_inverse_cash", label: "롱 / 인버스", field: "longShortPosition", top: 416, bottom: 440 }
    ]
    : [{ policy: store.backtestPolicy, label: store.backtestPolicy === "long_cash" ? "롱 / 현금" : "롱 / 인버스", field: "position", top: 392, bottom: 424 }];
  const laneByPolicy = new Map(laneDefs.map((lane) => [lane.policy, lane]));
  const pointSpacing = plotRows.length > 1 ? x(1) - x(0) : 12;
  const zones = [];
  const appendHoldingZones = ({ field, top, bottom, policy }) => {
    let zoneStart = null;
    let zoneSide = null;
    plotRows.forEach((row, index) => {
      const side = ["long", "inverse"].includes(row[field]) ? row[field] : null;
      const closeZone = (endIndex) => {
        if (!zoneSide || zoneStart == null) return;
        const zoneLeft = Math.max(p.l, x(zoneStart) - pointSpacing / 2);
        const zoneRight = Math.min(plotRight, x(Math.max(zoneStart, endIndex)) + pointSpacing / 2);
        zones.push(`<rect class="holding-zone holding-lane ${zoneSide} policy-${policy}" x="${zoneLeft}" y="${top + 2}" width="${Math.max(3, zoneRight - zoneLeft)}" height="${bottom - top - 4}"><title>${esc(`${laneByPolicy.get(policy)?.label || policy} · ${positionWithTicker(zoneSide)}`)}</title></rect>`);
      };
      if (side !== zoneSide) {
        closeZone(index - 1);
        zoneStart = side ? index : null;
        zoneSide = side;
      }
      if (index === plotRows.length - 1) closeZone(index);
    });
  };
  laneDefs.forEach(appendHoldingZones);
  const signalSize = Math.max(2.4, Math.min(5, pointSpacing * .38));
  const signalMarks = plotRows.map((row, index) => {
    if (!row.signal) return "";
    const px = x(index), py = y(row.kospiClose ?? row.kospi);
    const title = `${row.date} 종가 · ${labels[row.signal.state]} 상태 첫 관측 · 백분위 ${fmt.score(row.signal.percentile)}`;
    return row.signal.state === "extreme_greed"
      ? `<rect class="event-greed signal-close" x="${px - signalSize}" y="${py - signalSize}" width="${signalSize * 2}" height="${signalSize * 2}" transform="rotate(45 ${px} ${py})"><title>${esc(title)}</title></rect>`
      : `<circle class="event-fear signal-close" cx="${px}" cy="${py}" r="${signalSize}"><title>${esc(title)}</title></circle>`;
  }).join("");
  const rowIndexByDate = new Map(plotRows.map((row, index) => [row.date, index]));
  const executionConnectors = plotRows.length > 120 ? "" : plotRows.map((row, index) => row.actions.map((action) => {
    const signalIndex = rowIndexByDate.get(action.signalDate || action.sourceSignalDate);
    const lane = laneByPolicy.get(action.policy);
    const signalRow = signalIndex == null ? null : plotRows[signalIndex];
    const signalPrice = signalRow == null ? null : Number(signalRow.kospiClose ?? signalRow.kospi);
    if (!lane || !Number.isFinite(signalPrice)) return "";
    const startX = x(signalIndex), endX = x(index), startY = y(signalPrice) + signalSize + 2;
    const boundaryClass = action.includedInWindowMetrics === false ? " boundary-context" : "";
    const boundaryText = action.includedInWindowMetrics === false ? " · 선택 구간 첫 평가액 재기준화 이전 체결" : "";
    return `<path class="execution-connector${boundaryClass}" d="M ${startX} ${startY} L ${startX} ${lane.top - 7} L ${endX} ${lane.top - 7} L ${endX} ${(lane.top + lane.bottom) / 2}"><title>${esc(`${action.signalDate || action.sourceSignalDate} 종가 신호 → ${row.date} 시가 체결${boundaryText}`)}</title></path>`;
  }).join("")).join("");
  const actionMarks = plotRows.map((row, index) => {
    if (!row.actions.length) return "";
    const px = x(index);
    return row.actions.map((action) => {
      const type = action.type || action.action || action.executedAction;
      const to = action.toPosition || action.to || action.positionAfterOpen;
      const lane = laneByPolicy.get(action.policy);
      if (!lane) return "";
      const actionY = (lane.top + lane.bottom) / 2;
      const boundaryClass = action.includedInWindowMetrics === false ? " boundary-context" : "";
      const boundaryText = action.includedInWindowMetrics === false ? " · 선택 구간 첫 평가액 재기준화 이전 체결, 구간 성과 제외" : "";
      const title = `${policyLabel(action.policy)}: ${normalizedActionLabel(action)} · 신호 ${action.signalDate || action.sourceSignalDate || "—"} 종가 → ${row.date} 시가${boundaryText}`;
      if ((type === "enter" || type === "entry") && to === "long") {
        return `<g class="execution-action entry long${boundaryClass}"><path d="M ${px} ${actionY - 7} L ${px + 7} ${actionY + 7} L ${px - 7} ${actionY + 7} Z"><title>${esc(title)}</title></path></g>`;
      }
      if ((type === "enter" || type === "entry") && to === "inverse") {
        return `<g class="execution-action entry inverse${boundaryClass}"><path d="M ${px - 7} ${actionY - 7} L ${px + 7} ${actionY - 7} L ${px} ${actionY + 7} Z"><title>${esc(title)}</title></path></g>`;
      }
      if (type === "exit") {
        return `<g class="execution-action exit${boundaryClass}"><path d="M ${px - 6} ${actionY - 6} L ${px + 6} ${actionY + 6} M ${px + 6} ${actionY - 6} L ${px - 6} ${actionY + 6}"><title>${esc(title)}</title></path></g>`;
      }
      return `<g class="execution-action reversal${boundaryClass}"><path d="M ${px} ${actionY - 8} L ${px + 8} ${actionY} L ${px} ${actionY + 8} L ${px - 8} ${actionY} Z"/><text x="${px}" y="${actionY + 3}" text-anchor="middle">↔</text><title>${esc(title)}</title></g>`;
    }).join("");
  }).join("");
  const priceTicks = niceTicks(min - pad, max + pad, 6).map((value) => `<line class="grid-line" x1="${p.l}" y1="${y(value)}" x2="${plotRight}" y2="${y(value)}"/><text class="axis-label" x="${p.l - 12}" y="${y(value) + 4}" text-anchor="end">${Math.round(value).toLocaleString()}</text>`).join("");
  const equityTicks = niceTicks(equityMin0 - equityPad, equityMax0 + equityPad, 5).map((value) => `<line class="grid-line" x1="${p.l}" y1="${equityY(value)}" x2="${plotRight}" y2="${equityY(value)}"/><text class="axis-label" x="${p.l - 12}" y="${equityY(value) + 4}" text-anchor="end">${esc(fmt.pct(value, Math.abs(equityMax0 - equityMin0) < .2 ? 1 : 0))}</text>`).join("");
  const dateAxis = chartDateAxis(plotRows, x, { top: p.priceTop, bottom: p.equityBottom, labelY: p.dateLabel, maxTicks: 7 });
  const longCashLine = showLongCash ? `<g class="line-strategy">${pathSegments(plotRows, "longCashReturn", x, equityY)}</g>` : "";
  const longShortLine = showLongShort ? `<g class="line-longshort">${pathSegments(plotRows, "longShortReturn", x, equityY)}</g>` : "";
  const laneBackgrounds = laneDefs.map((lane) => `<rect class="holding-lane-background" x="${p.l}" y="${lane.top}" width="${plotRight - p.l}" height="${lane.bottom - lane.top}"/><text class="holding-lane-label" x="${p.l - 12}" y="${(lane.top + lane.bottom) / 2 + 4}" text-anchor="end">${esc(lane.label)}</text>`).join("");
  const lastRow = plotRows.at(-1);
  const endSeries = [
    ...(showLongCash ? [{ label: "롱/현금", field: "longCashReturn", cls: "strategy" }] : []),
    ...(showLongShort ? [{ label: "롱/인버스", field: "longShortReturn", cls: "longshort" }] : []),
    { label: `${pair.longTicker} BH`, field: "buyHoldReturn", cls: "buyhold" }
  ].map((series) => ({ ...series, value: Number(lastRow[series.field]) })).filter((series) => Number.isFinite(series.value)).sort((a, b) => equityY(a.value) - equityY(b.value));
  const labelGap = 18, minLabelY = p.equityTop + 8, maxLabelY = p.equityBottom - 4;
  endSeries.forEach((series, index) => { series.labelY = Math.max(equityY(series.value), index ? endSeries[index - 1].labelY + labelGap : minLabelY); });
  const overflow = endSeries.length ? Math.max(0, endSeries.at(-1).labelY - maxLabelY) : 0;
  if (overflow) endSeries.forEach((series) => { series.labelY -= overflow; });
  const endLabels = endSeries.map((series) => `<circle class="line-end-dot ${series.cls}" cx="${plotRight}" cy="${equityY(series.value)}" r="3"/><path class="line-end-connector ${series.cls}" d="M ${plotRight + 3} ${equityY(series.value)} L ${plotRight + 12} ${series.labelY}"/><text class="line-end-label ${series.cls}" x="${plotRight + 16}" y="${series.labelY + 4}">${esc(series.label)} ${esc(fmt.signedPct(series.value, 1))}</text>`).join("");
  const latestPrice = Number(lastRow.kospiClose ?? lastRow.kospi);
  const latestPriceY = y(latestPrice);
  const matchedPeriodEndIndex = plotRows.findIndex((row) => row.date === m.end);
  const periodEndIndex = matchedPeriodEndIndex >= 0 ? matchedPeriodEndIndex : plotRows.length - 1;
  const zeroReference = equityMin0 - equityPad <= 0 && equityMax0 + equityPad >= 0 ? `<line class="reference-line performance-zero" x1="${p.l}" y1="${equityY(0)}" x2="${plotRight}" y2="${equityY(0)}"/>` : "";
  container.innerHTML = `<svg viewBox="0 0 ${w} ${h}" aria-hidden="true"><rect class="chart-panel-bg" x="${p.l}" y="${p.priceTop}" width="${plotRight - p.l}" height="${p.priceBottom - p.priceTop}"/><rect class="chart-panel-bg" x="${p.l}" y="${p.equityTop}" width="${plotRight - p.l}" height="${p.equityBottom - p.equityTop}"/>${laneBackgrounds}${dateAxis}${priceTicks}${equityTicks}${zeroReference}<line class="axis-line" x1="${p.l}" y1="${p.priceTop}" x2="${p.l}" y2="${p.priceBottom}"/><line class="axis-line" x1="${p.l}" y1="${p.priceBottom}" x2="${plotRight}" y2="${p.priceBottom}"/><g class="line-price">${pathSegments(plotRows, (row) => row.kospiClose ?? row.kospi, x, y)}</g><circle class="line-end-dot price" cx="${plotRight}" cy="${latestPriceY}" r="3"/><text class="line-end-label price" x="${plotRight + 12}" y="${latestPriceY + 4}">KOSPI ${esc(Math.round(latestPrice).toLocaleString())}</text>${executionConnectors}${signalMarks}<text class="panel-title" x="${p.l}" y="24">가격 · KOSPI 종가</text><text class="axis-unit" x="${plotRight}" y="24" text-anchor="end">단위: 지수포인트</text><text class="panel-title" x="${p.l}" y="${p.laneTitle}">체결·보유 · 종가 신호 → 다음 거래일 시가</text>${zones.join("")}${actionMarks}<line class="axis-line panel-divider" x1="${p.l}" y1="${p.equityTop}" x2="${plotRight}" y2="${p.equityTop}"/><line class="axis-line" x1="${p.l}" y1="${p.equityTop}" x2="${p.l}" y2="${p.equityBottom}"/><line class="axis-line" x1="${p.l}" y1="${p.equityBottom}" x2="${plotRight}" y2="${p.equityBottom}"/>${longCashLine}${longShortLine}<g class="line-buyhold">${pathSegments(plotRows, "buyHoldReturn", x, equityY)}</g>${endLabels}<text class="panel-title" x="${p.l}" y="${p.equityTop - 16}">성과 · 비용 후 누적수익률</text><text class="axis-unit" x="${plotRight}" y="${p.equityTop - 16}" text-anchor="end">첫 ETF 평가일 = 0%</text><text class="axis-title" x="${(p.l + plotRight) / 2}" y="${p.xTitle}" text-anchor="middle">날짜 (KRX 거래일 · KST)</text></svg>`;
  container.setAttribute("aria-label", `${plotRows[0].date}부터 ${plotRows.at(-1).date}까지 ${compactModelName()} KOSPI 종가 신호, ${pairLabel(store.backtestProxy, true)} 다음 시가 체결, ${policyLabel()} 비용 후 누적수익률 통합 차트.`);
  attachChartNavigation(container, plotRows, (row) => {
    const signal = row.signal ? `${labels[row.signal.state]} 상태 첫 관측 · 백분위 ${fmt.score(row.signal.percentile)}` : "신규 극단 신호 없음";
    const lines = [`차트 선택일 ${row.date} · KOSPI ${Number(row.kospiClose ?? row.kospi).toLocaleString()}pt`, `종가 연구 상태  ${labels[row.trackState] || row.trackState} · ${signal}`, "선택일 누적성과"];
    if (showLongCash) lines.push(`롱/현금  ${positionWithTicker(row.longCashPosition)} · ${historyPolicyAction(row, "long_cash")} · ${fmt.signedPct(row.longCashReturn, 1)}`);
    if (showLongShort) lines.push(`롱/인버스  ${positionWithTicker(row.longShortPosition)} · ${historyPolicyAction(row, "long_inverse_cash")} · ${fmt.signedPct(row.longShortReturn, 1)}`);
    lines.push(`${pair.longTicker} 매수·보유  ${fmt.signedPct(row.buyHoldReturn, 1)}`);
    return lines.join("\n");
  }, { viewBoxWidth: w, plotLeft: p.l, plotRight: w - p.r, initialIndex: periodEndIndex, latestIndex: periodEndIndex, onSelect: (row) => renderHistorySelectedSnapshot(row, { periodEnd: m.end || plotRows.at(-1).date, showLongCash, showLongShort, pair }) });

  const signalCount = [...signals.values()].length;
  const windowMeta = primary.window || primary.range || {};
  const carry = windowMeta.carryIn?.position || primary.carryInPosition || "cash";
  const requested = windowMeta.requestedStartDate && windowMeta.requestedEndDate ? `${windowMeta.requestedStartDate}–${windowMeta.requestedEndDate}` : `${plotRows[0].date}–${plotRows.at(-1).date}`;
  const applied = windowMeta.appliedStartDate && windowMeta.appliedEndDate ? `${windowMeta.appliedStartDate}–${windowMeta.appliedEndDate}` : `${m.start}–${m.end}`;
  const carryClosed = Number(windowMeta.excludedCarryInClosedTrades || 0);
  const resultSummary = store.backtestPolicy === "compare" && longCash?.metrics && longShort?.metrics
    ? `롱/현금 ${fmt.signedPct(longCash.metrics.totalReturn, 1)} · 롱/인버스 ${fmt.signedPct(longShort.metrics.totalReturn, 1)}`
    : `${policyLabel(store.backtestPolicy)} ${fmt.signedPct(m.totalReturn, 1)} · MDD ${fmt.pct(m.maxDrawdown, 1)}`;
  $("#history-chart-meta").innerHTML = [
    ["표시 기간", `${plotRows[0].date}–${plotRows.at(-1).date} · ${plotRows.length.toLocaleString()}일`],
    ["실제 ETF", `${pair.leverage}X · ${pair.longTicker}/${pair.inverseTicker}`],
    ["실행 조건", `편도 ${store.backtestCost}bp · 청산 ${store.longExitPercentile}/${100 - store.longExitPercentile}`],
    ["평가 종료일 보유", store.backtestPolicy === "compare" ? `롱/현금 ${heldInstrument(longCash)} · 롱/인버스 ${heldInstrument(longShort)}` : heldInstrument(primary)],
    ["평가기간 총수익률", resultSummary]
  ].map(([label, value]) => `<span><strong>${esc(label)}</strong><b>${esc(value)}</b></span>`).join("");
  const exposure = store.backtestPolicy === "compare" && longCash?.metrics && longShort?.metrics
    ? `<strong>정책별 총노출</strong><span>롱 / 현금 ${esc(fmt.pct(longCash.metrics.grossExposure, 1))} · 롱 / 인버스 / 현금 ${esc(fmt.pct(longShort.metrics.grossExposure, 1))} · ${esc(pairLabel(store.backtestProxy, true))} · 극단 최초 신호 ${signalCount}회 · 시가 체결일 ${actionsByDate.size}일</span>`
    : `<strong>선택 시나리오 총 보유비율 ${esc(fmt.pct(m.grossExposure, 1))}</strong><span>롱 ETF ${esc(fmt.pct(m.longExposure, 1))} · 인버스 ETF ${esc(fmt.pct(inverseExposure(m), 1))} · 현금 ${esc(fmt.pct(m.cashExposure, 1))} · 현재 ${esc(heldInstrument(primary))} · 극단 최초 신호 ${signalCount}회 · 시가 체결일 ${actionsByDate.size}일</span>`;
  $("#history-exposure-note").innerHTML = `${exposure}<span>평가 ${esc(applied)} · 완결 거래 ${esc(m.tradeCount)}건 · 시작 포지션 ${esc(labels[carry] || carry)}${requested === applied ? "" : ` · 요청 ${esc(requested)}`}${carryClosed ? ` · 시작 전 진입 포지션 청산 ${esc(carryClosed)}건 별도` : ""}</span>`;
  const recent = recentRows(plotRows);
  const tableRows = store.backtestPolicy === "compare"
    ? recent.map((row) => [row.date, Number(row.kospiClose ?? row.kospi).toLocaleString(), labels[row.trackState] || row.trackState, positionWithTicker(row.longCashPosition), positionWithTicker(row.longShortPosition), row.actions.map((action) => `${policyLabel(action.policy)} ${normalizedActionLabel(action)} (${action.signalDate || action.sourceSignalDate || "—"}→${row.date})`).join(" / ") || "—", fmt.score(row.longCashValue, 3), fmt.score(row.longShortValue, 3)])
    : recent.map((row) => [row.date, Number(row.kospiClose ?? row.kospi).toLocaleString(), labels[row.trackState] || row.trackState, positionWithTicker(row.position), row.actions.map((action) => `${policyLabel(action.policy)} ${normalizedActionLabel(action)} (${action.signalDate || action.sourceSignalDate || "—"}→${row.date})`).join(" / ") || "—", fmt.score(row.primaryValue, 3)]);
  const headers = store.backtestPolicy === "compare"
    ? ["날짜", "KOSPI", "선택 트랙 상태", "롱/현금 포지션", "롱/인버스/현금 포지션", "다음 시가 체결 (신호일→체결일)", "롱/현금 가치", "롱/인버스/현금 가치"]
    : ["날짜", "KOSPI", "선택 트랙 상태", "포지션", "다음 시가 체결 (신호일→체결일)", "전략가치"];
  $("#history-data-table").innerHTML = dataTable(headers, tableRows, `선택 기간의 최근 ${tableRows.length}개 통합 관측값`);
}

const scatterFitCache = new Map();

function selectedScatterFit() {
  const anchor = selectedAnalysisRow();
  const baseRows = store.history?.series || [];
  if (!anchor || !baseRows.length) return null;
  const index = baseRows.findIndex((row) => row.date === anchor.date);
  if (index < 0) return null;
  const key = [store.summary?.dataAsOf, anchor.date, store.model, store.signalLookback, store.signalMinimumR2, store.signalExtremeTail].join("|");
  if (scatterFitCache.has(key)) return scatterFitCache.get(key);
  try {
    const result = index === baseRows.length - 1 && store.activeSignalMeta?.currentFit
      ? { currentFit: store.activeSignalMeta.currentFit }
      : fitDynamicSignalAt({ historyRows: baseRows, index, ...currentSignalConfig() });
    scatterFitCache.set(key, result.currentFit || null);
    return result.currentFit || null;
  } catch (_) {
    scatterFitCache.set(key, null);
    return null;
  }
}

function scatterPoints() {
  const fit = selectedScatterFit();
  if (!fit) return [];
  const byDate = new Map(activeHistoryRows().map((row) => [row.date, row]));
  return [...fit.trainingRows, fit.current].map((point) => {
    const source = byDate.get(point.date) || {};
    return {
      ...source,
      ...point,
      y: point.observed,
      state: point.role === "current" ? fit.current.state : rowTrackValue(source, "state")
    };
  });
}

function publishedScatterStateBoundaries() {
  const fit = selectedScatterFit();
  const cuts = fit?.residualCuts;
  const offsets = cuts ? {
    extremeFearUpper: cuts[String(store.signalExtremeTail)],
    fearUpper: cuts["20"],
    greedLower: cuts["80"],
    extremeGreedLower: cuts[String(100 - store.signalExtremeTail)]
  } : null;
  const required = [offsets?.extremeFearUpper, offsets?.fearUpper, offsets?.greedLower, offsets?.extremeGreedLower];
  if (required.some((value) => typeof value !== "number")) return null;
  const numeric = required.map(Number);
  const ordered = numeric.every((value, index) => index === 0 || value >= numeric[index - 1]);
  if (!numeric.every(Number.isFinite) || !ordered || Number(fit.trainingCount) < 20) return null;
  return {
    count: Number(fit.trainingCount),
    extremeFearUpper: Number(offsets.extremeFearUpper),
    fearUpper: Number(offsets.fearUpper),
    greedLower: Number(offsets.greedLower),
    extremeGreedLower: Number(offsets.extremeGreedLower)
  };
}

function renderScatter() {
  const points = scatterPoints();
  const container = $("#scatter-chart");
  if (points.length < 8) {
    $("#scatter-zone-legend-fear").hidden = true;
    $("#scatter-zone-legend-greed").hidden = true;
    $("#scatter-title").textContent = "수익률 × 개인 수급";
    $("#scatter-subtitle").textContent = `${modelName()} 산점도 입력이 공개되지 않았습니다.`;
    $("#scatter-note").textContent = "선택 종료일 이전의 유효 학습 관측치가 부족하거나 회귀를 적합할 수 없습니다.";
    return showEmpty(container, "산점도 관측치가 부족합니다.");
  }
  const regression = regressionPayload();
  const xs = points.map((row) => Number(row.return1d));
  const ys = points.map((row) => Number(row.y));
  const predicted = (value) => Number(regression.alpha) + Number(regression.beta) * value;
  const xmin0 = Math.min(...xs), xmax0 = Math.max(...xs);
  const xpad = (xmax0 - xmin0 || .01) * .06;
  const xmin = xmin0 - xpad, xmax = xmax0 + xpad;
  const stateBoundaries = publishedScatterStateBoundaries();
  $("#scatter-zone-legend-fear").hidden = !stateBoundaries;
  $("#scatter-zone-legend-greed").hidden = !stateBoundaries;
  const offsets = stateBoundaries ? [stateBoundaries.extremeFearUpper, stateBoundaries.fearUpper, stateBoundaries.greedLower, stateBoundaries.extremeGreedLower] : [];
  const predictedEnds = [predicted(xmin), predicted(xmax)].filter(Number.isFinite);
  const boundaryEnds = offsets.flatMap((offset) => [predicted(xmin) + offset, predicted(xmax) + offset]).filter(Number.isFinite);
  const ymin0 = Math.min(...ys, ...predictedEnds, ...boundaryEnds), ymax0 = Math.max(...ys, ...predictedEnds, ...boundaryEnds);
  const ypad = (ymax0 - ymin0 || .01) * .08;
  const ymin = ymin0 - ypad, ymax = ymax0 + ypad;
  const w = 600, h = 340, p = { l: 68, r: 20, t: 20, b: 50 };
  const x = scale(xmin, xmax, p.l, w - p.r), y = scale(ymin, ymax, h - p.b, p.t);
  const xTicks = niceTicks(xmin, xmax).map((value) => `<line class="grid-line" x1="${x(value)}" y1="${p.t}" x2="${x(value)}" y2="${h - p.b}"/><text class="axis-label" x="${x(value)}" y="${h - p.b + 17}" text-anchor="middle">${esc(fmt.pct(value, 1))}</text>`).join("");
  const yFormat = store.model === "raw" ? (value) => `${Number(value).toFixed(1)}조` : (value) => fmt.pct(value, 1);
  const yTicks = niceTicks(ymin, ymax).map((value) => `<line class="grid-line" x1="${p.l}" y1="${y(value)}" x2="${w - p.r}" y2="${y(value)}"/><text class="axis-label" x="${p.l - 8}" y="${y(value) + 3}" text-anchor="end">${esc(yFormat(value))}</text>`).join("");
  const extremeZones = stateBoundaries ? (() => {
    const lineY = (offset, xValue) => y(predicted(xValue) + offset);
    const fearExtremeLeft = lineY(stateBoundaries.extremeFearUpper, xmin), fearExtremeRight = lineY(stateBoundaries.extremeFearUpper, xmax);
    const fearLeft = lineY(stateBoundaries.fearUpper, xmin), fearRight = lineY(stateBoundaries.fearUpper, xmax);
    const greedLeft = lineY(stateBoundaries.greedLower, xmin), greedRight = lineY(stateBoundaries.greedLower, xmax);
    const greedExtremeLeft = lineY(stateBoundaries.extremeGreedLower, xmin), greedExtremeRight = lineY(stateBoundaries.extremeGreedLower, xmax);
    return `<g clip-path="url(#scatter-plot-clip)"><polygon class="scatter-zone scatter-zone-extreme-fear" points="${x(xmin)},${fearExtremeLeft} ${x(xmax)},${fearExtremeRight} ${x(xmax)},${h - p.b} ${x(xmin)},${h - p.b}"/><polygon class="scatter-zone scatter-zone-fear" points="${x(xmin)},${fearLeft} ${x(xmax)},${fearRight} ${x(xmax)},${fearExtremeRight} ${x(xmin)},${fearExtremeLeft}"/><polygon class="scatter-zone scatter-zone-greed" points="${x(xmin)},${greedExtremeLeft} ${x(xmax)},${greedExtremeRight} ${x(xmax)},${greedRight} ${x(xmin)},${greedLeft}"/><polygon class="scatter-zone scatter-zone-extreme-greed" points="${x(xmin)},${p.t} ${x(xmax)},${p.t} ${x(xmax)},${greedExtremeRight} ${x(xmin)},${greedExtremeLeft}"/>${[[stateBoundaries.extremeFearUpper, "fear extreme"], [stateBoundaries.fearUpper, "fear"], [stateBoundaries.greedLower, "greed"], [stateBoundaries.extremeGreedLower, "greed extreme"]].map(([offset, cls]) => `<line class="scatter-boundary ${cls}" x1="${x(xmin)}" y1="${lineY(offset, xmin)}" x2="${x(xmax)}" y2="${lineY(offset, xmax)}"/>`).join("")}</g><text class="scatter-zone-label fear" x="${p.l + 8}" y="${h - p.b - 10}">극단적 공포 ≤${esc(store.signalExtremeTail)}%</text><text class="scatter-zone-label greed" x="${p.l + 8}" y="${p.t + 16}">극단적 탐욕 ≥${esc(100 - store.signalExtremeTail)}%</text>`;
  })() : "";
  const pointGeometry = points.map((row) => ({ row, plotX: x(row.return1d), plotY: y(row.y) }));
  const marks = pointGeometry.map(({ row, plotX, plotY }) => `<circle class="scatter-point ${row.role === "current" ? "current" : ""}" cx="${plotX}" cy="${plotY}" r="${row.role === "current" ? 6 : 3}"><title>${esc(`${row.date} · 수익률 ${fmt.pct(row.return1d)} · ${store.model === "raw" ? `순매수 ${fmt.score(row.y, 3)}조원` : `거래대금 대비 순매수 ${fmt.pct(row.y, 3)}`}`)}</title></circle>`).join("");
  const current = points.find((row) => row.role === "current") || points.at(-1);
  const currentPredicted = predicted(current.return1d);
  const residual = Number.isFinite(currentPredicted) ? `<line class="residual-arrow" x1="${x(current.return1d)}" y1="${y(currentPredicted)}" x2="${x(current.return1d)}" y2="${y(current.y)}"/>` : "";
  const regressionLine = predictedEnds.length === 2 ? `<line class="regression-line" x1="${x(xmin)}" y1="${y(predicted(xmin))}" x2="${x(xmax)}" y2="${y(predicted(xmax))}"/>` : "";
  const zeroAxes = `${xmin <= 0 && xmax >= 0 ? `<line class="reference-line zero-axis" x1="${x(0)}" y1="${p.t}" x2="${x(0)}" y2="${h - p.b}"/>` : ""}${ymin <= 0 && ymax >= 0 ? `<line class="reference-line zero-axis" x1="${p.l}" y1="${y(0)}" x2="${w - p.r}" y2="${y(0)}"/>` : ""}`;
  const currentLabelAnchor = x(current.return1d) > (p.l + w - p.r) / 2 ? "end" : "start";
  const currentLabelX = x(current.return1d) + (currentLabelAnchor === "end" ? -9 : 9);
  const currentLabelY = Math.max(p.t + 15, Math.min(h - p.b - 8, y(current.y) - 10));
  const currentLabel = `<text class="scatter-current-label" x="${currentLabelX}" y="${currentLabelY}" text-anchor="${currentLabelAnchor}">평가 종료일 · ${esc(labels[stateFromValue(current)] || stateFromValue(current))}</text>`;
  container.innerHTML = `<svg viewBox="0 0 ${w} ${h}" aria-hidden="true"><defs><clipPath id="scatter-plot-clip"><rect x="${p.l}" y="${p.t}" width="${w - p.l - p.r}" height="${h - p.t - p.b}"/></clipPath></defs><rect class="chart-panel-bg" x="${p.l}" y="${p.t}" width="${w - p.l - p.r}" height="${h - p.t - p.b}"/>${extremeZones}${xTicks}${yTicks}${zeroAxes}<line class="axis-line" x1="${p.l}" y1="${p.t}" x2="${p.l}" y2="${h - p.b}"/><line class="axis-line" x1="${p.l}" y1="${h - p.b}" x2="${w - p.r}" y2="${h - p.b}"/>${regressionLine}${residual}${marks}${currentLabel}<text class="axis-title" x="${(p.l + w - p.r) / 2}" y="${h - 7}" text-anchor="middle">KOSPI 1일 수익률 (%)</text><text class="axis-title" x="${p.l}" y="13">${store.model === "raw" ? "개인 순매수대금 (조원)" : "순매수대금 ÷ KOSPI 거래대금 (%)"}</text></svg>`;
  const inputLabel = store.model === "raw" ? "개인 순매수대금" : "거래대금 대비 개인 순매수 비율";
  $("#scatter-title").textContent = `수익률 × ${inputLabel}`;
  $("#scatter-subtitle").textContent = `${modelName()} · ${current.date} 기준 · 직전 ${regression.trainingCount || regression.window || points.length - 1}거래일 학습`;
  const zoneAria = stateBoundaries ? `회귀선 대비 선택 학습 잔차의 경험적 ${store.signalExtremeTail}% 이하 극단 공포와 ${100 - store.signalExtremeTail}% 이상 극단 탐욕 영역을 표시합니다.` : "정확한 선택 회귀 상태 경계가 없어 극단 영역은 표시하지 않습니다.";
  container.setAttribute("aria-label", `${points.length}개 관측치 ${modelName()} 산점도. ${zoneAria} 평가 종료일 ${current.date} 수익률 ${fmt.pct(current.return1d)}, ${inputLabel} ${store.model === "raw" ? `${fmt.score(current.y, 3)}조원` : fmt.pct(current.y, 3)}.`);
  const cutoffFormat = store.model === "raw" ? (value) => `${fmt.score(value, 3)}조원` : (value) => `${(Number(value) * 100).toFixed(2)}%p`;
  const zoneNote = stateBoundaries
    ? ` · 선택 회귀 학습 잔차 경계: ${store.signalExtremeTail}% ${cutoffFormat(stateBoundaries.extremeFearUpper)}, 20% ${cutoffFormat(stateBoundaries.fearUpper)}, 80% ${cutoffFormat(stateBoundaries.greedLower)}, ${100 - store.signalExtremeTail}% ${cutoffFormat(stateBoundaries.extremeGreedLower)} (n=${stateBoundaries.count})`
    : " · 선택 종료일의 과거 전용 회귀를 적합할 수 없어 영역을 표시하지 않음";
  $("#scatter-note").textContent = `학습 n=${points.filter((row) => row.role !== "current").length} · 현재 n=${points.filter((row) => row.role === "current").length} · β=${fmt.score(regression.beta, 4)} · R²=${fmt.score(modelPayload()?.rollingR2, 3)}${zoneNote}`;
  attachScatterNavigation(container, pointGeometry, (row) => `${row.date}, KOSPI ${fmt.signedPct(row.return1d)}, ${inputLabel} ${store.model === "raw" ? `${fmt.score(row.y, 3)}조원` : fmt.pct(row.y, 3)}, 당시 롤링 상태 ${labels[row.state] || row.state || "미확인"}${row.role === "current" ? ", 평가 종료일 관측" : ", 학습 관측"}`, { width: w, height: h });
  const tableRows = recentRows(points).map((row) => [row.date, fmt.signedPct(row.return1d), store.model === "raw" ? `${fmt.score(row.y, 3)}조원` : fmt.pct(row.y, 3), labels[row.state] || row.state || "—", row.role === "current" ? "평가 종료일" : "학습"]);
  $("#scatter-data-table").innerHTML = dataTable(["날짜", "KOSPI 1일", inputLabel, "당시 롤링 상태", "역할"], tableRows, `${modelName()} 최근 ${tableRows.length}개 산점도 관측`);
}

function renderResidual() {
  const kind = store.model;
  const fields = trackFields(kind);
  $("#residual-model-scope").textContent = `${modelRole(kind)} · ${compactModelName(kind)}`;
  $("#residual-model-scope").className = `scope-badge ${kind === "raw" ? "replica" : kind === "robust" ? "practical" : "baseline"}`;
  const rows = selectedHistory().map((row) => ({
    ...row,
    selectedPercentile: row[fields.percentile],
    selectedState: row[fields.state],
    selectedEligible: row[fields.eligible]
  }));
  const container = $("#residual-chart");
  if (rows.length < 8) return showEmpty(container, "잔차 시계열이 부족합니다.");
  const w = 600, h = 360, p = { l: 58, r: 62, t: 32, b: 50 };
  const x = scale(0, rows.length - 1, p.l, w - p.r), y = scale(0, 100, h - p.b, p.t);
  const lowerExtreme = Number(store.signalExtremeTail);
  const upperExtreme = 100 - lowerExtreme;
  const boundaries = [...new Set([lowerExtreme, 20, 50, 80, upperExtreme])].sort((a, b) => a - b).map((value) => `<line class="grid-line ${value === 50 ? "midline" : ""}" x1="${p.l}" y1="${y(value)}" x2="${w - p.r}" y2="${y(value)}"/><text class="axis-label" x="${p.l - 7}" y="${y(value) + 3}" text-anchor="end">${value}</text>`).join("");
  const eligibleRows = rows.map((row) => row.selectedEligible === true ? row : { ...row, selectedPercentile: null });
  const blockedRows = rows.map((row) => row.selectedEligible !== true ? row : { ...row, selectedPercentile: null });
  const blockedMarks = rows.map((row, index) => row.selectedEligible !== true && Number.isFinite(Number(row.selectedPercentile)) ? `<circle class="residual-blocked-point" cx="${x(index)}" cy="${y(row.selectedPercentile)}" r="2.5"><title>${esc(`${row.date} · 낮은 적합도, 거래 차단`)}</title></circle>` : "").join("");
  const validRows = rows
    .map((row, sourceIndex) => ({ ...row, sourceIndex }))
    .filter((row) => Number.isFinite(Number(row.selectedPercentile)));
  const latest = validRows.at(-1);
  const latestIndex = latest ? rows.findIndex((row) => row.date === latest.date) : -1;
  const latestMark = latest && latestIndex >= 0 ? `<circle class="line-end-dot strategy" cx="${x(latestIndex)}" cy="${y(latest.selectedPercentile)}" r="3"/><text class="line-end-label strategy" x="${w - p.r + 9}" y="${y(latest.selectedPercentile) + 4}">${esc(fmt.score(latest.selectedPercentile, 1))}</text>` : "";
  const dateAxis = chartDateAxis(rows, x, { top: p.t, bottom: h - p.b, labelY: h - 20, maxTicks: 5 });
  container.innerHTML = `<svg viewBox="0 0 ${w} ${h}" aria-hidden="true"><rect class="chart-panel-bg" x="${p.l}" y="${p.t}" width="${w - p.l - p.r}" height="${h - p.t - p.b}"/><rect class="residual-band-fear" x="${p.l}" y="${y(lowerExtreme)}" width="${w - p.l - p.r}" height="${y(0) - y(lowerExtreme)}"/><rect class="residual-band-greed" x="${p.l}" y="${y(100)}" width="${w - p.l - p.r}" height="${y(upperExtreme) - y(100)}"/>${dateAxis}${boundaries}<line class="axis-line" x1="${p.l}" y1="${p.t}" x2="${p.l}" y2="${h - p.b}"/><line class="axis-line" x1="${p.l}" y1="${h - p.b}" x2="${w - p.r}" y2="${h - p.b}"/><g class="line-primary">${pathSegments(eligibleRows, "selectedPercentile", x, y)}</g><g class="line-blocked">${pathSegments(blockedRows, "selectedPercentile", x, y)}</g>${blockedMarks}${latestMark}<text class="panel-title" x="${p.l}" y="18">잔차 경험적 백분위</text><text class="axis-unit" x="${w - p.r}" y="18" text-anchor="end">0–100 · 거래 차단은 회색 점선</text><text class="residual-zone-label fear" x="${w - p.r - 7}" y="${y(lowerExtreme / 2) + 3}" text-anchor="end">극단 공포 ≤${esc(lowerExtreme)}</text><text class="residual-zone-label greed" x="${w - p.r - 7}" y="${y((100 + upperExtreme) / 2) + 3}" text-anchor="end">극단 탐욕 ≥${esc(upperExtreme)}</text><text class="axis-title" x="${(p.l + w - p.r) / 2}" y="${h - 5}" text-anchor="middle">날짜 (KRX 거래일)</text></svg>`;
  container.setAttribute("aria-label", `${compactModelName(kind)} 잔차 백분위 ${rows[0].date}부터 ${rows.at(-1).date}. 최신 ${latest ? fmt.score(latest.selectedPercentile) : "산출 불가"}.`);
  attachChartNavigation(container, validRows, (row) => `${row.date}, ${compactModelName(kind)} 잔차 백분위 ${fmt.score(row.selectedPercentile)}, ${labels[row.selectedState] || row.selectedState}, 거래 사용 ${row.selectedEligible === true ? "가능" : "차단"}`, {
    viewBoxWidth: w,
    plotLeft: p.l,
    plotRight: w - p.r,
    itemRatio: (row) => rows.length <= 1 ? 1 : row.sourceIndex / (rows.length - 1)
  });
  const tableRows = recentRows(rows).map((row) => [row.date, fmt.score(row.selectedPercentile), labels[row.selectedState] || row.selectedState, row.selectedEligible === true ? "거래 가능" : "거래 차단"]);
  $("#residual-data-table").innerHTML = dataTable(["날짜", "백분위", "상태", "품질"], tableRows, `최근 ${tableRows.length}개 ${compactModelName(kind)} 잔차 백분위`);
}

function selectedEventSection() {
  if (!store.history || !activeHistoryRows().length) return null;
  const { startDate, endDate } = selectedWindowBounds();
  const key = [store.summary?.dataAsOf, store.model, store.signalLookback, store.signalMinimumR2, store.signalExtremeTail, store.eventAsset, store.eventSample, startDate, endDate].join("|");
  if (dynamicEventCache.has(key)) {
    latestEventError = dynamicEventErrors.get(key) || null;
    return dynamicEventCache.get(key);
  }
  try {
    const result = runDynamicEventStudy({
      historyRows: activeHistoryRows(),
      track: store.model,
      asset: store.eventAsset,
      sample: store.eventSample,
      startDate,
      endDate
    });
    latestEventError = null;
    dynamicEventCache.set(key, result);
    return result;
  } catch (error) {
    latestEventError = error instanceof Error ? error : new Error("사건 연구를 계산할 수 없습니다.");
    dynamicEventErrors.set(key, latestEventError);
    dynamicEventCache.set(key, null);
    return null;
  }
}

function eventModelKind() {
  return store.model;
}

function eventBenchmark(row) {
  return row.benchmarkMean ?? row.matchedBenchmarkMean ?? row.unconditionalMean ?? null;
}

function eventExcess(row) {
  return row.meanExcessReturn ?? row.excessMean ?? row.excessReturn ?? null;
}

function renderEventVisual(section) {
  const container = $("#event-ci-chart");
  const rows = (section?.summary || []).filter((row) => row.mean != null && Number.isFinite(Number(row.mean)));
  if (!rows.length) {
    $("#event-benchmark-legend").hidden = true;
    $("#event-benchmark-note").textContent = "선택 표본이 없어 불확실성 차트를 표시하지 않습니다.";
    return showEmpty(container, "사건 신뢰구간 없음");
  }
  const values = rows.flatMap((row) => [row.mean, ...(row.meanCi95 || []), eventBenchmark(row), eventExcess(row), ...(row.meanExcessReturnCi95 || row.excessCi95 || row.excessMeanCi95 || [])]).filter((value) => value != null).map(Number).filter(Number.isFinite);
  const min0 = Math.min(0, ...values), max0 = Math.max(0, ...values), pad = (max0 - min0 || .01) * .12;
  const min = min0 - pad, max = max0 + pad;
  const w = 880, rowHeight = 37, h = Math.max(330, rows.length * rowHeight + 78), p = { l: 154, r: 72, t: 24, b: 48 };
  const x = scale(min, max, p.l, w - p.r);
  const rowY = (index) => p.t + 18 + index * ((h - p.t - p.b - 22) / Math.max(1, rows.length - 1));
  const ticks = linearTicks(min, max, 6).map((value) => `<line class="grid-line" x1="${x(value)}" y1="${p.t}" x2="${x(value)}" y2="${h - p.b}"/><text class="axis-label" x="${x(value)}" y="${h - 17}" text-anchor="middle">${esc(fmt.pct(value, 1))}</text>`).join("");
  let hasBenchmark = false;
  let hasExcess = false;
  const benchmarkTreatments = new Set([
    section?.meanExcessReturnCi95BenchmarkTreatment,
    ...rows.map((row) => row.meanExcessReturnCi95BenchmarkTreatment)
  ].filter(Boolean));
  const geometry = [];
  const marks = rows.map((row, index) => {
    const y = rowY(index);
    const mean = Number(row.mean);
    const [low, high] = (row.meanCi95 || []).map((value) => value == null ? Number.NaN : Number(value));
    const benchmarkValue = eventBenchmark(row);
    const excessValue = eventExcess(row);
    const benchmark = benchmarkValue == null ? Number.NaN : Number(benchmarkValue);
    const excess = excessValue == null ? Number.NaN : Number(excessValue);
    const excessCi = (row.meanExcessReturnCi95 || row.excessCi95 || row.excessMeanCi95 || []).map((value) => value == null ? Number.NaN : Number(value));
    const greed = String(row.state).includes("greed");
    const stateLabel = labels[row.state] || row.state;
    geometry.push({ row, plotX: x(mean), plotY: y });
    if (Number.isFinite(benchmark)) hasBenchmark = true;
    if (Number.isFinite(excess)) hasExcess = true;
    const interval = Number.isFinite(low) && Number.isFinite(high) ? `<line class="event-interval" x1="${x(low)}" y1="${y}" x2="${x(high)}" y2="${y}"/><line class="event-interval-cap" x1="${x(low)}" y1="${y - 5}" x2="${x(low)}" y2="${y + 5}"/><line class="event-interval-cap" x1="${x(high)}" y1="${y - 5}" x2="${x(high)}" y2="${y + 5}"/>` : "";
    const meanMark = greed ? `<rect class="event-mean greed" x="${x(mean) - 5}" y="${y - 5}" width="10" height="10" transform="rotate(45 ${x(mean)} ${y})"/>` : `<circle class="event-mean fear" cx="${x(mean)}" cy="${y}" r="5"/>`;
    const benchmarkMark = Number.isFinite(benchmark) ? `<rect class="event-benchmark" x="${x(benchmark) - 4}" y="${y - 4}" width="8" height="8"/><line class="event-excess-connector" x1="${x(benchmark)}" y1="${y + 8}" x2="${x(mean)}" y2="${y + 8}"/>` : "";
    const excessInterval = excessCi.length === 2 && excessCi.every(Number.isFinite) ? `<line class="event-excess-interval" x1="${x(excessCi[0])}" y1="${y + 10}" x2="${x(excessCi[1])}" y2="${y + 10}"/>` : "";
    const excessLabel = Number.isFinite(excess) ? `<text class="event-excess-label" x="${w - p.r + 8}" y="${y + 4}">Δ ${esc(fmt.signedPct(excess, 1))}</text>` : "";
    return `<text class="event-row-label" x="${p.l - 12}" y="${y + 4}" text-anchor="end">${esc(`${stateLabel} · ${row.horizon}일 · n=${row.eventCount}`)}</text>${interval}${benchmarkMark}${excessInterval}${meanMark}${excessLabel}`;
  }).join("");
  container.innerHTML = `<svg viewBox="0 0 ${w} ${h}" aria-hidden="true">${ticks}<line class="reference-line" x1="${x(0)}" y1="${p.t}" x2="${x(0)}" y2="${h - p.b}"/>${marks}<line class="axis-line" x1="${p.l}" y1="${h - p.b}" x2="${w - p.r}" y2="${h - p.b}"/><text class="axis-title" x="${p.l}" y="14">평균 선행수익률 · 95% CI</text></svg>`;
  container.setAttribute("aria-label", `${store.eventAsset} ${rows.length}개 사건 요약. 평균과 95% 신뢰구간${hasBenchmark ? ", 비교 벤치마크" : ""}${hasExcess ? ", 초과수익" : ""}.`);
  attachScatterNavigation(container, geometry, (row) => `${labels[row.state] || row.state} ${row.horizon}일, 평균 ${fmt.signedPct(row.mean)}, 95% 신뢰구간 ${fmt.pct(row.meanCi95?.[0])}에서 ${fmt.pct(row.meanCi95?.[1])}, 사건 ${row.eventCount}개${eventBenchmark(row) == null ? "" : `, 벤치마크 ${fmt.signedPct(eventBenchmark(row))}`}${eventExcess(row) == null ? "" : `, 초과수익 ${fmt.signedPct(eventExcess(row))}`}`, { width: w, height: h });
  $("#event-benchmark-legend").hidden = !hasBenchmark;
  const intervalMethod = benchmarkTreatments.has("paired_event_returns") ? " 사건·벤치마크 함께 재표집" : benchmarkTreatments.has("fixed_external_mean") ? " 사건 평균 재표집·비교 평균 고정" : "";
  $("#event-benchmark-note").textContent = hasBenchmark ? `비교 벤치마크와의 차이를 연결선과 Δ로 표시합니다.${hasExcess ? ` 초과수익 95% 구간 ·${intervalMethod || " 발행값"}.` : " 평균수익률과 벤치마크를 나란히 확인할 수 있습니다."}` : "평균수익률과 0% 기준선을 표시합니다.";
}

function renderEvents() {
  const section = selectedEventSection();
  const body = $("#event-table tbody");
  resetTableSort($("#event-table"));
  const sampleLabel = store.eventSample === "all" ? "전체 사건" : "20일 비중첩";
  $("#event-table caption").textContent = `${store.eventAsset} ${sampleLabel} 신호일 종가 기준 선행수익률`;
  const modelKind = eventModelKind();
  const bootstrap = section?.bootstrap || {};
  const bootstrapLabel = `${bootstrap.samples || 10_000}회 결정론적 iid bootstrap`;
  $("#event-model-scope").textContent = `${modelRole(modelKind)} · ${compactModelName(modelKind)}`;
  $("#event-model-scope").className = `scope-badge ${modelKind === "raw" ? "replica" : modelKind === "robust" ? "practical" : "baseline"}`;
  $("#event-source-line").textContent = `${store.eventAsset} · 신호일 종가→h일 종가 · ${sampleLabel} · ${compactModelName(modelKind)} · ${bootstrapLabel}`;
  $("#event-visual-subtitle").textContent = `${store.eventAsset} · 종가 기준 사건 수익률(실제 익일 시가 체결수익률 아님) · ${sampleLabel}`;
  if (latestEventError) {
    $("#event-benchmark-legend").hidden = true;
    body.innerHTML = `<tr><td colspan="7">사건 연구 계산 오류로 결과를 표시하지 않습니다.</td></tr>`;
    $("#event-note").textContent = latestEventError.message;
    $("#event-benchmark-note").textContent = "계산 오류를 빈 표본이나 0% 수익률로 대체하지 않습니다.";
    return showEmpty($("#event-ci-chart"), "사건 연구 산출 불가");
  }
  if (!section?.summary?.length) {
    body.innerHTML = `<tr><td colspan="7">선택 설정과 평가 기간에 해당하는 사건 표본이 없습니다.</td></tr>`;
    $("#event-note").textContent = "사건 수가 없을 때 성과를 강조하지 않습니다.";
    renderEventVisual(section);
    return;
  }
  body.innerHTML = section.summary.map((row) => `<tr class="${row.smallSample ? "small-sample" : ""}"><td><span class="state-mark ${row.state.includes("greed") ? "greed" : ""}">${esc(labels[row.state] || row.state)}</span></td><td>${esc(row.horizon)}일</td><td>${esc(row.eventCount)}${row.smallSample ? "*" : ""}</td><td>${esc(fmt.pct(row.mean))}</td><td>${esc(fmt.pct(row.median))}</td><td>${esc(fmt.pct(row.positiveRate, 1))}</td><td>${esc(fmt.pct(row.meanCi95?.[0]))} ~ ${esc(fmt.pct(row.meanCi95?.[1]))}</td></tr>`).join("");
  $("#event-note").textContent = `${sampleLabel} ${section.eventCount}개 · 평가 ${section.startDate || "전체 시작"}–${section.endDate || "평가 종료일"} · 기간별 사건 수, 평균·중앙값·상승 비중과 95% 구간을 함께 표시합니다.`;
  renderEventVisual(section);
}

function variantKey(variant = store.backtestVariant, cost = store.backtestCost) {
  const published = ({ scaled_huber: "robust", scaled_ols: "scaled", raw_ols: "raw", disparity: "disparity" })[variant] || variant;
  return `${published}_${cost}bp`;
}

function variantLabel(name) {
  const match = String(name).match(/^(robust|scaled|raw|scaled_huber|scaled_ols|raw_ols|base|disparity)_(\d+)bp$/);
  if (!match) return name;
  const role = ({ robust: "실전 강건 회귀", scaled: "규모보정 OLS 기준선", raw: "절대수급 원문 근사", scaled_huber: "실전 강건 회귀", scaled_ols: "규모보정 OLS 기준선", raw_ols: "절대수급 원문 근사", base: "규모보정 OLS 기준선", disparity: "이격도 강건성" })[match[1]];
  return `${role} · ${match[2]}bp`;
}

function legacyVariantKey(variant = store.backtestVariant, cost = store.backtestCost) {
  if (variant === "scaled_ols") return `base_${cost}bp`;
  if (variant === "disparity") return `disparity_${cost}bp`;
  return null;
}

function policyLabel(policy = store.backtestPolicy) {
  return ({ compare: "나란히 비교", long_cash: "롱 / 현금", long_inverse_cash: "롱 / 인버스 / 현금", long_short_cash: "롱 / 인버스 / 현금" })[policy] || policy;
}

function publishedLongCashResultFor({ proxy, period, variant, cost }) {
  const data = store.dashboard.backtests?.proxies?.[proxy];
  if (!data) return null;
  const key = variantKey(variant, cost);
  const legacyKey = legacyVariantKey(variant, cost);
  const section = period === "full" ? data.fullPeriod : data.commonPeriod;
  if (!section) return null;
  if (section[key]?.metrics) return { ...section[key], calculationSource: "server_verified_default" };
  if (legacyKey && section[legacyKey]?.metrics) return { ...section[legacyKey], calculationSource: "server_verified_default" };
  if (section.metrics && variant === "scaled_ols" && Number(section.oneWayCostBps ?? 10) === Number(cost)) return { ...section, calculationSource: "server_verified_default" };
  return null;
}

function publishedLongShortResultFor({ proxy, period, variant, cost }) {
  if (variant !== "scaled_huber") return null;
  const data = store.strategyComparison?.proxies?.[proxy];
  const section = period === "full" ? data?.fullPeriod : data?.commonPeriod;
  const result = section?.[`robust_${cost}bp`];
  return result?.metrics ? { ...result, calculationSource: "server_verified_default" } : null;
}

const scenarioCache = new Map();
const dynamicEventCache = new Map();
const dynamicEventErrors = new Map();
let latestScenarioError = null;
let latestEventError = null;

const RESEARCH_FORM_STATUS = Object.freeze({
  "#history-range-form": "#history-range-status",
  "#signal-settings-form": "#signal-settings-status",
  "#exit-threshold-form": "#exit-threshold-status"
});

function captureResearchFormState() {
  return Object.entries(RESEARCH_FORM_STATUS).map(([formSelector, statusSelector]) => {
    const form = $(formSelector);
    const status = $(statusSelector);
    return {
      formSelector,
      statusSelector,
      dataset: form ? { ...form.dataset } : {},
      ariaBusy: form?.getAttribute("aria-busy"),
      inputs: form ? [...form.querySelectorAll("input")].map((input) => ({
        id: input.id,
        value: input.value,
        ariaInvalid: input.getAttribute("aria-invalid")
      })) : [],
      statusDataset: status ? { ...status.dataset } : {},
      statusText: status?.textContent || ""
    };
  });
}

function restoreResearchFormState(states = []) {
  states.forEach((state) => {
    const form = $(state.formSelector);
    const status = $(state.statusSelector);
    if (form) {
      Object.keys(form.dataset).forEach((key) => delete form.dataset[key]);
      Object.assign(form.dataset, state.dataset);
      if (state.ariaBusy == null) form.removeAttribute("aria-busy");
      else form.setAttribute("aria-busy", state.ariaBusy);
      state.inputs.forEach(({ id, value, ariaInvalid }) => {
        const input = document.getElementById(id);
        if (!input) return;
        input.value = value;
        if (ariaInvalid == null) input.removeAttribute("aria-invalid");
        else input.setAttribute("aria-invalid", ariaInvalid);
      });
    }
    if (status) {
      Object.keys(status.dataset).forEach((key) => delete status.dataset[key]);
      Object.assign(status.dataset, state.statusDataset);
      status.textContent = state.statusText;
    }
  });
}

function captureResearchSnapshot() {
  return {
    controls: Object.fromEntries(Object.keys(CONTROL_QUERY).map((key) => [key, store[key]])),
    activeSeries: store.activeSeries,
    activeSignalMeta: store.activeSignalMeta,
    scenarioCache: new Map(scenarioCache),
    dynamicEventCache: new Map(dynamicEventCache),
    dynamicEventErrors: new Map(dynamicEventErrors),
    latestScenarioError,
    latestEventError,
    formState: captureResearchFormState()
  };
}

function restoreMap(target, source) {
  target.clear();
  source.forEach((value, key) => target.set(key, value));
}

function restoreResearchSnapshot(snapshot) {
  Object.assign(store, snapshot.controls);
  store.activeSeries = snapshot.activeSeries;
  store.activeSignalMeta = snapshot.activeSignalMeta;
  restoreMap(scenarioCache, snapshot.scenarioCache);
  restoreMap(dynamicEventCache, snapshot.dynamicEventCache);
  restoreMap(dynamicEventErrors, snapshot.dynamicEventErrors);
  latestScenarioError = snapshot.latestScenarioError;
  latestEventError = snapshot.latestEventError;
  restoreResearchFormState(snapshot.formState);
}

function applySynchronousControlChange(mutate, render = renderAll) {
  const snapshot = captureResearchSnapshot();
  try {
    mutate();
    render();
    persistControlState();
    return true;
  } catch (error) {
    restoreResearchSnapshot(snapshot);
    try { renderAll(); } catch (_) { /* retain the last complete DOM if rollback rendering also fails */ }
    announceViewAction(`설정을 적용하지 못해 기존 결과를 유지합니다. ${error instanceof Error ? error.message : ""}`.trim());
    return false;
  }
}

function scenarioResultFor(policyId, { proxy, period, variant, cost }) {
  if (policyId === "long_inverse_cash" && variant === "disparity") return null;
  const { startDate, endDate } = selectedWindowBounds();
  const key = [store.summary?.dataAsOf, policyId, proxy, period, variant, cost, store.longExitPercentile, store.signalLookback, store.signalMinimumR2, store.signalExtremeTail, store.signalMaxHolding, startDate, endDate].join("|");
  if (scenarioCache.has(key)) return scenarioCache.get(key);
  try {
    const result = runActualEtfPairScenario({
      history: activeHistoryRows(),
      pairId: proxy,
      policy: policyId,
      period: period === "full" ? "pair" : "common",
      variant,
      costBps: cost,
      exitPercentile: store.longExitPercentile,
      maxHoldDays: store.signalMaxHolding,
      dateStart: startDate,
      dateEnd: endDate
    });
    scenarioCache.set(key, result);
    return result;
  } catch (error) {
    latestScenarioError = error instanceof Error ? error : new Error("사용자 전략 시나리오를 계산할 수 없습니다.");
    scenarioCache.set(key, null);
    return null;
  }
}

function longCashResultFor({ proxy = store.backtestProxy, period = store.backtestPeriod, variant = store.backtestVariant, cost = store.backtestCost } = {}) {
  return scenarioResultFor("long_cash", { proxy, period, variant, cost });
}

function longInverseResultFor({ proxy = store.backtestProxy, period = store.backtestPeriod, variant = store.backtestVariant, cost = store.backtestCost } = {}) {
  return scenarioResultFor("long_inverse_cash", { proxy, period, variant, cost });
}

function selectedScenarioBundle() {
  const longCash = longCashResultFor();
  const longInverse = longInverseResultFor();
  const primary = store.backtestPolicy === "long_inverse_cash" ? longInverse : longCash;
  return { longCash, longInverse, longShort: longInverse, primary };
}

function resultFor({ proxy = store.backtestProxy, period = store.backtestPeriod, variant = store.backtestVariant, cost = store.backtestCost, policy = store.backtestPolicy } = {}) {
  if (policy === "compare" && variant === "disparity") return null;
  return policy === "long_inverse_cash"
    ? longInverseResultFor({ proxy, period, variant, cost })
    : longCashResultFor({ proxy, period, variant, cost });
}

function resultsForPolicySelection(options = {}) {
  const longCash = longCashResultFor(options);
  const longInverse = longInverseResultFor(options);
  if (store.backtestPolicy === "long_cash") return [{ policy: "long_cash", result: longCash }];
  if (store.backtestPolicy === "long_inverse_cash") return [{ policy: "long_inverse_cash", result: longInverse }];
  return [{ policy: "long_cash", result: longCash }, { policy: "long_inverse_cash", result: longInverse }];
}

function hasAnyResult({ proxy = store.backtestProxy, period = store.backtestPeriod, variant = store.backtestVariant, cost = store.backtestCost, policy = store.backtestPolicy } = {}) {
  if (resultFor({ proxy, period, variant, cost, policy })) return true;
  return ["scaled_huber", "scaled_ols", "raw_ols", "disparity"].some((v) => [0, 5, 10, 20].some((c) => resultFor({ proxy, period, variant: v, cost: c, policy })));
}

function ensureBacktestSelection() {
  syncStrategyTrack();
}

function renderProxyComparison() {
  const card = $("#proxy-comparison-card");
  const comparisonPolicy = store.backtestPolicy === "long_cash" ? "long_cash" : "long_inverse_cash";
  const results = ["1x", "2x"].map((pairId) => ({ pairId, result: scenarioResultFor(comparisonPolicy, { proxy: pairId, period: "common", variant: store.backtestVariant, cost: store.backtestCost }) })).filter(({ result }) => result?.metrics);
  if (results.length !== 2) {
    card.hidden = true;
    $("#proxy-comparison").innerHTML = "";
    return;
  }
  card.hidden = false;
  const key = variantKey();
  $("#proxy-comparison-subtitle").textContent = `${policyLabel(comparisonPolicy)} · ${variantLabel(key)} · 4개 ETF 공통기간 · 같은 신호·비용`;
  $("#proxy-comparison").innerHTML = results.map(({ pairId, result }) => {
    const m = result.metrics;
    const pair = result.pair || pairMeta(pairId);
    return `<section class="proxy-panel" aria-label="${esc(pairLabel(pairId))} 공통기간 결과">
      <div><span>${esc(`${pair.leverage}X 실제 페어`)}</span><strong>${esc(fmt.signedPct(m.totalReturn))}</strong><small>${esc(`${pair.longTicker} ↔ ${pair.inverseTicker}`)}</small></div>
      <dl>
        <div><dt>CAGR</dt><dd>${esc(fmt.pct(m.cagr))}</dd></div>
        <div><dt>Sharpe</dt><dd>${esc(fmt.score(m.sharpe, 2))}</dd></div>
        <div><dt>최대낙폭</dt><dd>${esc(fmt.pct(m.maxDrawdown))}</dd></div>
        <div><dt>롱 / 인버스</dt><dd>${esc(`${fmt.pct(m.longExposure, 1)} / ${fmt.pct(inverseExposure(m), 1)}`)}</dd></div>
        <div><dt>동일 타이밍 0bp</dt><dd>${esc(fmt.signedPct(m.zeroCostTimingReturn ?? m.exposureMatchedReturn))}</dd></div>
        <div><dt>위험 일치 BH</dt><dd>${esc(fmt.signedPct(m.riskMatchedBuyHoldReturn))}</dd></div>
        <div><dt>거래 수</dt><dd>${esc(m.tradeCount)}</dd></div>
        <div><dt>${esc(pair.longTicker)} 보유</dt><dd>${esc(fmt.signedPct(m.buyAndHoldReturn))}</dd></div>
      </dl>
    </section>`;
  }).join("");
}

function resultSourceLabel(result) {
  return result?.calculationSource === "server_verified_default" ? "서버 검증 기본값" : "브라우저 과거전용 사용자 시나리오";
}

function renderPolicyComparison(longCash, longInverse) {
  const card = $("#policy-comparison-card");
  const container = $("#policy-comparison");
  if (store.backtestPolicy !== "compare" || !longCash?.metrics || !longInverse?.metrics) {
    card.hidden = true;
    container.innerHTML = "";
    $("#strategy-exposure").innerHTML = "";
    return;
  }
  card.hidden = false;
  $("#policy-comparison-subtitle").textContent = `${pairLabel(store.backtestProxy, true)} · ${variantLabel(variantKey())} · ${store.backtestPeriod === "common" ? "4개 ETF 공통 기간" : "선택 페어 가능 기간"} · 롱 ≥${store.longExitPercentile} / 인버스 ≤${100 - store.longExitPercentile}`;
  container.innerHTML = [["long_cash", longCash], ["long_inverse_cash", longInverse]].map(([policy, result]) => {
    const m = result.metrics;
    return `<section class="proxy-panel policy-panel" aria-label="${esc(policyLabel(policy))} 결과">
      <div><span>${esc(policyLabel(policy))}</span><strong>${esc(fmt.signedPct(m.totalReturn))}</strong><small>${esc(resultSourceLabel(result))}</small></div>
      <dl>
        <div><dt>CAGR</dt><dd>${esc(fmt.pct(m.cagr))}</dd></div><div><dt>Sharpe</dt><dd>${esc(fmt.score(m.sharpe, 2))}</dd></div>
        <div><dt>최대낙폭</dt><dd>${esc(fmt.pct(m.maxDrawdown))}</dd></div><div><dt>종료일 보유</dt><dd>${esc(heldInstrument(result))}</dd></div>
        <div><dt>롱 / 인버스 / 현금</dt><dd>${esc(`${fmt.pct(m.longExposure, 1)} / ${fmt.pct(inverseExposure(m), 1)} / ${fmt.pct(m.cashExposure, 1)}`)}</dd></div>
        <div><dt>거래 수</dt><dd>${esc(`${m.tradeCount}건`)}</dd></div>
      </dl></section>`;
  }).join("");
  $("#strategy-exposure").innerHTML = [["long_cash", longCash.metrics], ["long_inverse_cash", longInverse.metrics]].map(([policy, m]) => `<section class="exposure-policy"><div class="exposure-heading"><strong>${esc(policyLabel(policy))}</strong><span>총 보유 ${esc(fmt.pct(m.grossExposure, 1))} · 순 자본배분 ${esc(fmt.pct(m.netExposure, 1))}</span></div><div class="exposure-bar" aria-hidden="true"><i class="long" style="width:${Math.max(0, Number(m.longExposure) * 100)}%"></i><i class="inverse" style="width:${Math.max(0, inverseExposure(m) * 100)}%"></i><i class="cash" style="width:${Math.max(0, Number(m.cashExposure) * 100)}%"></i></div><dl><div><dt>롱 ETF</dt><dd>${esc(fmt.pct(m.longExposure, 1))}</dd></div><div><dt>인버스 ETF</dt><dd>${esc(fmt.pct(inverseExposure(m), 1))}</dd></div><div><dt>현금</dt><dd>${esc(fmt.pct(m.cashExposure, 1))}</dd></div></dl></section>`).join("");
  $("#exit-sensitivity").innerHTML = `<strong>현재 사용자 청산선</strong><span>롱 ≥${esc(store.longExitPercentile)}</span><span>인버스 ≤${esc(100 - store.longExitPercentile)}</span><small>통합 차트·두 정책·성과표에 동시에 적용됩니다.</small>`;
}

function renderBacktests(scenarioBundle = selectedScenarioBundle()) {
  if (!store.dashboard || !store.history || !store.strategyComparison) return;
  const body = $("#backtest-table tbody");
  resetTableSort($("#backtest-table"));
  resetTableSort($("#trade-table"));
  ensureBacktestSelection();
  const { longCash, longShort: longInverse } = scenarioBundle;
  const unsupportedPolicyVariant = store.backtestPolicy !== "long_cash" && store.backtestVariant === "disparity";
  const selected = (unsupportedPolicyVariant ? [] : store.backtestPolicy === "long_cash" ? [{ policy: "long_cash", result: longCash }] : store.backtestPolicy === "long_inverse_cash" ? [{ policy: "long_inverse_cash", result: longInverse }] : [{ policy: "long_cash", result: longCash }, { policy: "long_inverse_cash", result: longInverse }]).filter(({ result }) => result?.metrics);
  const result = store.backtestPolicy === "long_inverse_cash" ? longInverse : longCash;
  updateBacktestControls();
  const periodLabel = `${store.backtestPeriod === "common" ? "4개 ETF 공통 세션" : "선택 페어 가능 세션"} · ${historyWindowLabel()}`;
  const key = variantKey();
  const selectionLabel = `${pairLabel(store.backtestProxy, true)} · ${variantLabel(key)} · ${periodLabel} · ${policyLabel()}`;
  $("#equity-legend-long-cash").hidden = store.backtestPolicy === "long_inverse_cash";
  $("#equity-legend-long-inverse").hidden = store.backtestPolicy === "long_cash";
  const strategyRole = store.backtestVariant === "scaled_huber" ? ["실전 신호", "practical"] : store.backtestVariant === "raw_ols" ? ["PDF 원문 근사", "replica"] : store.backtestVariant === "scaled_ols" ? ["OLS 기준선", "baseline"] : ["강건성 변형", "fixed"];
  $("#strategy-model-scope").textContent = `${strategyRole[0]} · ${policyLabel()}`;
  $("#strategy-model-scope").className = `scope-badge ${strategyRole[1]}`;
  $("#backtest-card-subtitle").textContent = `적용 조건 · ${selectionLabel}`;
  $("#equity-title").textContent = `${pairLabel(store.backtestProxy, true)} 누적수익률과 낙폭`;
  $("#equity-subtitle").textContent = `${variantLabel(key)} · 학습 ${store.signalLookback}일 · 극단 ${store.signalExtremeTail}% · 최대 ${store.signalMaxHolding}일 · ${periodLabel} · 롱 ≥${store.longExitPercentile}${store.backtestPolicy === "long_cash" ? "" : ` · 인버스 ≤${100 - store.longExitPercentile}`}`;
  $("#backtest-table caption").textContent = `${selectionLabel} 전략 성과 상세`;
  $("#trade-table caption").textContent = `${selectionLabel} 최근 거래내역`;
  if (!selected.length) {
    const reason = latestScenarioError?.message || "네 ETF의 조정 시가·종가가 모두 공개된 기간이 필요합니다.";
    body.innerHTML = `<tr><td colspan="20">실제 ETF 페어 결과를 계산할 수 없습니다.</td></tr>`;
    $("#backtest-cards").innerHTML = `<p class="chart-note">${esc(reason)} KRX 최근 종가 교차검증을 통과한 파생 이력만 사용합니다.</p>`;
    showEmpty($("#equity-chart"), "실제 ETF 페어 데이터 확인 중");
    $("#trade-table tbody").innerHTML = `<tr><td colspan="10">거래 없음</td></tr>`;
    renderPolicyComparison(longCash, longInverse);
    renderProxyComparison();
    return;
  }
  const primaryResult = result?.metrics ? result : selected[0].result;
  const m = primaryResult.metrics;
  $("#backtest-card-subtitle").textContent = `평가 종료일 ${m.end} · ${selectionLabel}`;
  body.innerHTML = selected.map(({ policy, result: policyResult }) => {
    const metrics = policyResult.metrics;
    return `<tr><td>${esc(`${policyLabel(policy)} · ${pairLabel(store.backtestProxy, true)} · ${variantLabel(key)}`)}</td><td>${esc(`${metrics.start}~${metrics.end}`)}</td><td>${esc(fmt.pct(metrics.totalReturn))}</td><td>${esc(fmt.pct(metrics.cagr))}</td><td>${esc(fmt.pct(metrics.volatility))}</td><td>${esc(fmt.score(metrics.sharpe, 2))}</td><td>${esc(fmt.pct(metrics.maxDrawdown))}</td><td>${esc(fmt.pct(metrics.winRate, 1))}</td><td>${esc(fmt.pct(metrics.longExposure, 1))}</td><td>${esc(fmt.pct(inverseExposure(metrics), 1))}</td><td>${esc(fmt.pct(metrics.cashExposure, 1))}</td><td>${esc(fmt.pct(metrics.grossExposure, 1))}</td><td>${esc(fmt.pct(metrics.netExposure, 1))}</td><td>${esc(fmt.pct(metrics.zeroCostTimingReturn ?? metrics.exposureMatchedReturn))}</td><td>${esc(fmt.pct(metrics.riskMatchedBuyHoldReturn))}</td><td>${esc(fmt.score(metrics.turnover, 2))}×</td><td>${esc(metrics.tradeCount)}</td><td>${esc(fmt.score(metrics.averageHoldingSessions, 1))}일</td><td>${esc(fmt.pct(metrics.buyAndHoldReturn))}</td><td>${esc(fmt.pct(metrics.buyAndHoldMaxDrawdown))}</td></tr>`;
  }).join("");
  $("#backtest-cards").innerHTML = store.backtestPolicy === "compare" && longCash?.metrics && longInverse?.metrics
    ? [
      metric("롱 / 현금 총수익률", fmt.pct(longCash.metrics.totalReturn), `CAGR ${fmt.pct(longCash.metrics.cagr)} · Sharpe ${fmt.score(longCash.metrics.sharpe, 2)}`),
      metric("롱 / 인버스 / 현금 총수익률", fmt.pct(longInverse.metrics.totalReturn), `CAGR ${fmt.pct(longInverse.metrics.cagr)} · Sharpe ${fmt.score(longInverse.metrics.sharpe, 2)}`),
      metric("평가 종료일 보유", `${heldInstrument(longCash)} / ${heldInstrument(longInverse)}`, "롱/현금 / 롱/인버스/현금"),
      metric("롱 / 현금 최대낙폭", fmt.pct(longCash.metrics.maxDrawdown), `총노출 ${fmt.pct(longCash.metrics.grossExposure, 1)}`),
      metric("롱 / 인버스 최대낙폭", fmt.pct(longInverse.metrics.maxDrawdown), `총노출 ${fmt.pct(longInverse.metrics.grossExposure, 1)}`),
      metric(`${pairMeta().longTicker} 매수·보유`, fmt.pct(longCash.metrics.buyAndHoldReturn), `MDD ${fmt.pct(longCash.metrics.buyAndHoldMaxDrawdown)}`),
      metric("정책별 거래 수", `${longCash.metrics.tradeCount} / ${longInverse.metrics.tradeCount}`, "롱/현금 / 롱/인버스/현금")
    ].join("")
    : [
      metric("전략 총수익률", fmt.pct(m.totalReturn), `CAGR ${fmt.pct(m.cagr)}`),
      metric("전략 최대낙폭", fmt.pct(m.maxDrawdown), `변동성 ${fmt.pct(m.volatility)}`),
      metric("평가 종료일 보유", heldInstrument(primaryResult), `${pairLabel(store.backtestProxy, true)} · ${labels[scenarioPosition(primaryResult)] || scenarioPosition(primaryResult)}`),
      metric(`${pairMeta().longTicker} 매수·보유`, fmt.pct(m.buyAndHoldReturn), `MDD ${fmt.pct(m.buyAndHoldMaxDrawdown)}`),
      metric("동일 타이밍 무비용", fmt.pct(m.zeroCostTimingReturn ?? m.exposureMatchedReturn), "같은 진입·청산 · 비용 0bp"),
      metric("위험 일치 매수·보유", fmt.pct(m.riskMatchedBuyHoldReturn), `시장 비중 ${fmt.pct(m.riskMatchedScale, 1)}`),
      metric("거래 승률", fmt.pct(m.winRate, 1), `${m.tradeCount}건 · 평균 ${fmt.score(m.averageHoldingSessions, 1)}일`),
      metric("롱 / 인버스 / 현금", `${fmt.pct(m.longExposure, 1)} / ${fmt.pct(inverseExposure(m), 1)} / ${fmt.pct(m.cashExposure, 1)}`, `총 보유 ${fmt.pct(m.grossExposure, 1)} · 순 자본배분 ${fmt.pct(m.netExposure, 1)}`),
      metric("Sharpe", fmt.score(m.sharpe, 2), "현금수익률 0%")
    ].join("");
  renderEquity(primaryResult, selectionLabel, store.backtestPolicy === "compare" ? longInverse : null, store.backtestPolicy === "long_inverse_cash" ? "long_inverse_cash" : "long_cash");
  const trades = selected.flatMap(({ policy, result: policyResult }) => (policyResult.trades || []).map((trade) => ({ ...trade, policy, pair: policyResult.pair || pairMeta() }))).sort((a, b) => String(b.exit_date || b.exitDate).localeCompare(String(a.exit_date || a.exitDate)));
  $("#trade-card-subtitle").textContent = "신호일 종가와 다음 거래일 시가 체결을 연결한 최근 12건";
  const tradeRows = trades.length
    ? trades.slice(0, 12).map((trade) => `<tr><td>${esc(policyLabel(trade.policy))}</td><td>${esc(labels[trade.side] || trade.side)}</td><td>${esc(trade.instrumentTicker || (trade.side === "long" ? trade.pair.longTicker : trade.pair.inverseTicker))}</td><td>${esc(trade.entry_signal_date || trade.entrySignalDate || "—")}</td><td>${esc(trade.entry_date || trade.entryDate || "—")}</td><td>${esc(trade.exit_signal_date || trade.exitSignalDate || "—")}</td><td>${esc(trade.exit_date || trade.exitDate || "—")}</td><td>${esc(trade.holding_sessions ?? trade.holdingSessions ?? "—")}</td><td>${esc(labels[trade.exit_reason || trade.exitReason || trade.reason] || trade.exit_reason || trade.exitReason || trade.reason)}</td><td>${esc(fmt.pct(trade.net_return ?? trade.netReturn))}</td></tr>`).join("")
    : selected.some(({ result: policyResult }) => policyResult.tradeHistoryTruncated && Number(policyResult.metrics?.tradeCount) > 0)
      ? `<tr><td colspan="10">선택 조합의 거래 상세 행은 경량 공개 계약에서 생략되었습니다.</td></tr>`
      : `<tr><td colspan="10">완결된 거래 없음</td></tr>`;
  $("#trade-table tbody").innerHTML = tradeRows;
  applyTableFilter($("#trade-filter"));
  renderPolicyComparison(longCash, longInverse);
  renderProxyComparison();
}

function hydratedEquityRows(result) {
  const rows = result.equity || [];
  const proxy = store.dashboard.backtests?.proxies?.[store.backtestProxy] || {};
  const benchmarkRows = proxy.commonBenchmarkEquity || proxy.benchmarkEquity || [];
  if (!benchmarkRows.length || rows.every((row) => row.buyHoldValue != null && row.buyHoldDrawdown != null)) return rows;
  const benchmark = new Map(benchmarkRows.map((row) => [row.date, row]));
  return rows.map((row) => {
    const matched = benchmark.get(row.date) || {};
    return {
      ...row,
      buyHoldValue: row.buyHoldValue ?? matched.buyHoldValue ?? matched.value ?? null,
      buyHoldDrawdown: row.buyHoldDrawdown ?? matched.buyHoldDrawdown ?? matched.drawdown ?? null
    };
  });
}

function renderEquity(result, selectionLabel, comparisonResult = null, primaryPolicy = "long_cash") {
  let rows = hydratedEquityRows(result);
  const comparisonRows = comparisonResult ? hydratedEquityRows(comparisonResult) : [];
  const comparable = comparisonRows.length === rows.length && rows.every((row, index) => row.date === comparisonRows[index]?.date);
  if (comparisonResult && comparable) rows = rows.map((row, index) => ({ ...row, longShortValue: comparisonRows[index].value, longShortDrawdown: comparisonRows[index].drawdown }));
  const container = $("#equity-chart");
  if (rows.length < 2) {
    $("#equity-data-table").innerHTML = `<p class="empty-inline">선택 기간의 일별 누적가치가 공개 계약에 없습니다. 성과 표의 정확값을 확인하세요.</p>`;
    return showEmpty(container, "선택 기간의 누적가치 시계열 미공개");
  }
  rows = rows.map((row) => ({ ...row, strategyReturn: equityReturn(row.value), longShortReturn: equityReturn(row.longShortValue), buyHoldReturn: equityReturn(row.buyHoldValue) }));
  const values = rows.flatMap((row) => [Number(row.strategyReturn), Number(row.longShortReturn), Number(row.buyHoldReturn)]).filter(Number.isFinite);
  const min0 = Math.min(...values), max0 = Math.max(...values), span = max0 - min0 || .1;
  const min = min0 - span * .06, max = max0 + span * .08;
  const w = 720, h = 300, p = { l: 70, r: 112, t: 34, b: 50 };
  const x = scale(0, rows.length - 1, p.l, w - p.r), y = scale(min, max, h - p.b, p.t);
  const yTicks = niceTicks(min, max).map((value) => `<line class="grid-line" x1="${p.l}" y1="${y(value)}" x2="${w - p.r}" y2="${y(value)}"/><text class="axis-label" x="${p.l - 9}" y="${y(value) + 4}" text-anchor="end">${esc(fmt.pct(value, Math.abs(max - min) < .2 ? 1 : 0))}</text>`).join("");
  const primaryLineClass = primaryPolicy === "long_inverse_cash" ? "line-longshort" : "line-strategy";
  const primaryLabel = policyLabel(primaryPolicy);
  const pair = pairMeta();
  const valueDateAxis = chartDateAxis(rows, x, { top: p.t, bottom: h - p.b, labelY: h - 20, maxTicks: 6 });
  const zeroLine = min <= 0 && max >= 0 ? `<line class="reference-line performance-zero" x1="${p.l}" y1="${y(0)}" x2="${w - p.r}" y2="${y(0)}"/>` : "";
  const endSeries = [
    { label: primaryLabel.replaceAll(" / ", "/"), field: "strategyReturn", cls: primaryPolicy === "long_inverse_cash" ? "longshort" : "strategy" },
    ...(comparable ? [{ label: "롱/인버스", field: "longShortReturn", cls: "longshort" }] : []),
    { label: `${pair.longTicker} BH`, field: "buyHoldReturn", cls: "buyhold" }
  ].map((series) => ({ ...series, value: Number(rows.at(-1)[series.field]) })).filter((series) => Number.isFinite(series.value)).sort((a, b) => y(a.value) - y(b.value));
  endSeries.forEach((series, index) => { series.labelY = Math.max(y(series.value), index ? endSeries[index - 1].labelY + 17 : p.t + 7); });
  const labelOverflow = endSeries.length ? Math.max(0, endSeries.at(-1).labelY - (h - p.b - 4)) : 0;
  if (labelOverflow) endSeries.forEach((series) => { series.labelY -= labelOverflow; });
  const valueEndLabels = endSeries.map((series) => `<circle class="line-end-dot ${series.cls}" cx="${w - p.r}" cy="${y(series.value)}" r="3"/><path class="line-end-connector ${series.cls}" d="M ${w - p.r + 3} ${y(series.value)} L ${w - p.r + 11} ${series.labelY}"/><text class="line-end-label ${series.cls}" x="${w - p.r + 15}" y="${series.labelY + 4}">${esc(series.label)} ${esc(fmt.signedPct(series.value, 1))}</text>`).join("");
  const valueSvg = `<svg viewBox="0 0 ${w} ${h}" aria-hidden="true"><rect class="chart-panel-bg" x="${p.l}" y="${p.t}" width="${w - p.l - p.r}" height="${h - p.t - p.b}"/>${valueDateAxis}${yTicks}${zeroLine}<line class="axis-line" x1="${p.l}" y1="${p.t}" x2="${p.l}" y2="${h - p.b}"/><line class="axis-line" x1="${p.l}" y1="${h - p.b}" x2="${w - p.r}" y2="${h - p.b}"/><g class="${primaryLineClass}">${pathSegments(rows, "strategyReturn", x, y)}</g>${comparable ? `<g class="line-longshort">${pathSegments(rows, "longShortReturn", x, y)}</g>` : ""}<g class="line-buyhold">${pathSegments(rows, "buyHoldReturn", x, y)}</g>${valueEndLabels}<text class="panel-title" x="${p.l}" y="20">비용 후 누적수익률</text><text class="axis-unit" x="${w - p.r}" y="20" text-anchor="end">첫 ETF 평가일 = 0%</text><text class="axis-title" x="${(p.l + w - p.r) / 2}" y="${h - 5}" text-anchor="middle">날짜 (KRX 거래일)</text></svg>`;
  const drawdowns = rows.flatMap((row) => [Number(row.drawdown), Number(row.longShortDrawdown), Number(row.buyHoldDrawdown)]).filter(Number.isFinite);
  const ddMin = Math.min(...drawdowns, -.01), ddMax = 0;
  const ddY = scale(ddMin, ddMax, h - p.b, p.t);
  const ddTicks = niceTicks(ddMin, ddMax).map((value) => `<line class="grid-line" x1="${p.l}" y1="${ddY(value)}" x2="${w - p.r}" y2="${ddY(value)}"/><text class="axis-label" x="${p.l - 9}" y="${ddY(value) + 4}" text-anchor="end">${esc(fmt.pct(value, 0))}</text>`).join("");
  const ddDateAxis = chartDateAxis(rows, x, { top: p.t, bottom: h - p.b, labelY: h - 20, maxTicks: 6 });
  const ddSvg = `<svg viewBox="0 0 ${w} ${h}" aria-hidden="true"><rect class="chart-panel-bg" x="${p.l}" y="${p.t}" width="${w - p.l - p.r}" height="${h - p.t - p.b}"/>${ddDateAxis}${ddTicks}<line class="axis-line" x1="${p.l}" y1="${p.t}" x2="${p.l}" y2="${h - p.b}"/><line class="axis-line" x1="${p.l}" y1="${h - p.b}" x2="${w - p.r}" y2="${h - p.b}"/><g class="${primaryLineClass}">${pathSegments(rows, "drawdown", x, ddY)}</g>${comparable ? `<g class="line-longshort">${pathSegments(rows, "longShortDrawdown", x, ddY)}</g>` : ""}<g class="line-buyhold">${pathSegments(rows, "buyHoldDrawdown", x, ddY)}</g><text class="panel-title" x="${p.l}" y="20">고점 대비 낙폭</text><text class="axis-unit" x="${w - p.r}" y="20" text-anchor="end">0% = 직전 최고점</text><text class="axis-title" x="${(p.l + w - p.r) / 2}" y="${h - 5}" text-anchor="middle">날짜 (KRX 거래일)</text></svg>`;
  container.innerHTML = valueSvg + ddSvg;
  const last = rows.at(-1);
  container.setAttribute("aria-label", `${selectionLabel}. ${rows[0].date}부터 ${last.date}. 최종 ${primaryLabel} ${fmt.score(last.value, 3)}${comparable ? `, 롱 인버스 현금 ${fmt.score(last.longShortValue, 3)}` : ""}, 롱 ETF 매수·보유 ${fmt.score(last.buyHoldValue, 3)}.`);
  attachChartNavigation(container, rows, (row) => `${row.date}, ${primaryLabel} ${fmt.score(row.value, 3)}${comparable ? `, 롱 인버스 현금 ${fmt.score(row.longShortValue, 3)}` : ""}, 롱 ETF 매수·보유 ${fmt.score(row.buyHoldValue, 3)}, ${primaryLabel} 낙폭 ${fmt.pct(row.drawdown)}${comparable ? `, 롱 인버스 현금 낙폭 ${fmt.pct(row.longShortDrawdown)}` : ""}, 매수·보유 낙폭 ${fmt.pct(row.buyHoldDrawdown)}`, { viewBoxWidth: w, plotLeft: p.l, plotRight: w - p.r });
  $("#equity-data-table").innerHTML = dataTable(["시점", "날짜", primaryLabel, "비교 롱/인버스/현금", "롱 ETF 매수·보유", `${primaryLabel} 낙폭`, "비교 롱/인버스 낙폭", "BH 낙폭"], [["시작", rows[0].date, fmt.score(rows[0].value, 3), comparable ? fmt.score(rows[0].longShortValue, 3) : "—", fmt.score(rows[0].buyHoldValue, 3), fmt.pct(rows[0].drawdown), comparable ? fmt.pct(rows[0].longShortDrawdown) : "—", fmt.pct(rows[0].buyHoldDrawdown)], ["평가 종료", last.date, fmt.score(last.value, 3), comparable ? fmt.score(last.longShortValue, 3) : "—", fmt.score(last.buyHoldValue, 3), fmt.pct(last.drawdown), comparable ? fmt.pct(last.longShortDrawdown) : "—", fmt.pct(last.buyHoldDrawdown)]], `${selectionLabel} 시작·평가 종료 값`);
}

function renderConclusion(scenarioBundle = selectedScenarioBundle()) {
  if (!store.dashboard) return;
  const section = selectedEventSection();
  const result = scenarioBundle.primary;
  const conclusionLongCash = store.backtestPolicy === "compare" ? scenarioBundle.longCash : null;
  const conclusionLongInverse = store.backtestPolicy === "compare" ? scenarioBundle.longInverse : null;
  const fear20 = section?.summary?.find((row) => row.state === "extreme_fear" && Number(row.horizon) === 20);
  const metrics = result?.metrics;
  const replica = pdfReplicaPayload();
  const annotated = pdfReplicaEvents(replica);
  const replicaMatch = replica?.directionMatchCount ?? replica?.matchedCount ?? replica?.summary?.directionMatchCount ?? (annotated.length ? annotated.filter((row) => row.directionMatched === true).length : null);
  const excess = fear20 ? eventExcess(fear20) : null;
  const excessCi = fear20?.meanExcessReturnCi95 || fear20?.excessCi95 || fear20?.excessMeanCi95;
  const eventConclusive = Array.isArray(excessCi) ? Number(excessCi[0]) > 0 : Number(fear20?.meanCi95?.[0]) > 0;
  const strategyPositive = store.backtestPolicy === "compare"
    ? [conclusionLongCash, conclusionLongInverse].every((item) => item?.metrics && Number(item.metrics.totalReturn) > 0 && Number(item.metrics.sharpe) > 0)
    : metrics && Number(metrics.totalReturn) > 0 && Number(metrics.sharpe) > 0;
  const tone = eventConclusive && strategyPositive ? "supportive" : !fear20 || !metrics ? "mixed" : "caution";
  const periodMetrics = metrics || conclusionLongCash?.metrics || conclusionLongInverse?.metrics;
  const verdict = periodMetrics ? `${periodMetrics.start}–${periodMetrics.end} 선택 기간의 신호, 사건 반응과 실제 ETF 실행 결과입니다.` : "선택한 설정의 신호와 사건 결과를 요약합니다.";
  const sampleLabel = store.eventSample === "all" ? "전체 사건" : "20일 비중첩";
  const key = variantKey();
  const replicaEvidence = annotated.length ? `${replicaMatch == null ? `${annotated.length}개 주석 사건 공개` : `${replicaMatch}/${annotated.length} 방향 일치`} · 절대수급 원문 근사` : "주석 사건 파생값 미발행";
  const eventEvidence = fear20 ? `20일 평균 ${fmt.signedPct(fear20.mean)} · 95% CI ${fmt.pct(fear20.meanCi95?.[0])}~${fmt.pct(fear20.meanCi95?.[1])} · n=${fear20.eventCount}${excess == null ? " · 벤치마크 초과수익 미발행" : ` · 초과 ${fmt.signedPct(excess)}`}` : "선택 표본 없음";
  const strategyEvidence = store.backtestPolicy === "compare" && conclusionLongCash?.metrics && conclusionLongInverse?.metrics
    ? `실제 ${pairLabel(store.backtestProxy, true)} · 롱 청산 ${store.longExitPercentile} / 인버스 청산 ${100 - store.longExitPercentile} · 롱/현금 ${fmt.signedPct(conclusionLongCash.metrics.totalReturn)} (Sharpe ${fmt.score(conclusionLongCash.metrics.sharpe, 2)}) · 롱/인버스/현금 ${fmt.signedPct(conclusionLongInverse.metrics.totalReturn)} (Sharpe ${fmt.score(conclusionLongInverse.metrics.sharpe, 2)})`
    : metrics ? `${pairLabel(store.backtestProxy, true)} · ${policyLabel(store.backtestPolicy)} · 롱 청산 ${store.longExitPercentile}${store.backtestPolicy === "long_cash" ? "" : ` / 인버스 청산 ${100 - store.longExitPercentile}`} · ${resultSourceLabel(result)} · ${fmt.signedPct(metrics.totalReturn)} · Sharpe ${fmt.score(metrics.sharpe, 2)} · 총 보유 ${fmt.pct(metrics.grossExposure ?? metrics.exposure, 1)} · 종료일 ${heldInstrument(result)}` : "선택 결과 없음";
  const facts = `<span><strong>1 · 실제 ETF 전략</strong>${esc(strategyEvidence)}</span><span><strong>2 · 극단 공포 사건</strong>${esc(eventEvidence)}</span><span><strong>3 · PDF 날짜 대조</strong>${esc(replicaEvidence)}</span>`;
  $("#research-conclusion").className = `conclusion-card ${tone}`;
  $("#research-conclusion").innerHTML = `<div class="conclusion-heading"><div><p class="eyebrow">SELECTED PERIOD RESULTS</p><h2 id="conclusion-title">선택 기간 결과 요약</h2></div><span class="badge neutral">사건: ${esc(store.eventAsset)} ${esc(compactModelName(eventModelKind()))} · ${esc(sampleLabel)} · 전략: ${esc(pairLabel(store.backtestProxy, true))} ${esc(variantLabel(key))}</span></div><p class="conclusion-lead">${esc(verdict)}</p><div class="conclusion-facts">${facts}</div><p class="conclusion-footnote">위 분석 설정이 신호·사건·통합 차트·전략 성과표에 동일하게 적용됩니다.</p>`;
}

function pdfReplicaPayload() {
  return store.dashboard?.pdfReplica || store.dashboard?.sourceReplica || store.dashboard?.pdfEraSnapshot || null;
}

function pdfReplicaEvents(payload = pdfReplicaPayload()) {
  return payload?.annotatedEvents || payload?.events || payload?.observations || [];
}

function replicaField(row, model, field) {
  const cap = `${field.charAt(0).toUpperCase()}${field.slice(1)}`;
  return row?.models?.[model]?.[field] ?? row?.[`${model}${cap}`] ?? null;
}

function renderPdfSnapshot() {
  const payload = pdfReplicaPayload();
  const rows = pdfReplicaEvents(payload);
  const container = $("#pdf-snapshot");
  if (!rows.length) {
    container.innerHTML = `<div class="empty replica-empty"><strong>PDF 주석 사건 파생값이 아직 공개 계약에 없습니다.</strong><span>원문 이미지를 복제하지 않고, 독립 수집 데이터의 11개 날짜가 발행되면 이 영역에 표시합니다.</span></div>`;
    return;
  }
  const matched = payload.directionMatchCount ?? payload.matchedCount ?? payload.summary?.directionMatchCount ?? rows.filter((row) => row.directionMatched === true).length;
  const complete = rows.filter((row) => [row.forwardReturn20d, row.forwardReturns?.return20d, row.forwardReturns?.[20], row.forwardReturns?.["20"]].some((value) => value != null)).length;
  const summary = `<div class="replica-summary"><span><strong>${esc(rows.length)}개</strong>원문 주석 사건</span><span><strong>${esc(matched == null ? "—" : `${matched}/${rows.length}`)}</strong>방향 일치</span><span><strong>${esc(complete)}</strong>20일 결과 완결</span><span><strong>원문 근사</strong>완전 복제 아님</span></div>`;
  const tableRows = rows.map((row) => {
    const rawPercentile = row.rawPercentile ?? replicaField(row, "raw", "percentile");
    const practicalPercentile = row.robustPercentile ?? replicaField(row, "robust", "percentile") ?? row.scaledPercentile ?? replicaField(row, "scaled", "percentile");
    const forward = (horizon) => row[`forwardReturn${horizon}d`] ?? row.forwardReturns?.[`return${horizon}d`] ?? row.forwardReturns?.[horizon] ?? row.forwardReturns?.[String(horizon)] ?? null;
    const annotationState = row.pdfState || row.state || row.annotationState;
    const annotationLabel = row.pdfLabel || labels[annotationState] || annotationState || "—";
    return `<tr><td>${esc(row.date)}</td><td><span class="state-mark ${String(annotationState).includes("greed") ? "greed" : ""}">${esc(annotationLabel)}</span></td><td>${esc(fmt.signedPct(row.return1d))}</td><td>${esc(row.rawFlowTrillion == null ? "—" : `${fmt.score(row.rawFlowTrillion, 3)}조원`)}</td><td>${esc(fmt.score(rawPercentile, 1))}</td><td>${esc(fmt.score(practicalPercentile, 1))}</td><td>${esc(fmt.signedPct(forward(1)))}</td><td>${esc(fmt.signedPct(forward(5)))}</td><td>${esc(fmt.signedPct(forward(20)))}</td></tr>`;
  }).join("");
  container.innerHTML = `${summary}<div class="table-scroll"><table class="replica-table"><caption>PDF 원문 주석 날짜를 독립 수집 데이터로 대조</caption><thead><tr><th scope="col">날짜</th><th scope="col">원문 표시</th><th scope="col">당일 KOSPI</th><th scope="col">개인 순매수</th><th scope="col">raw 백분위</th><th scope="col">규모보정 백분위</th><th scope="col">+1일</th><th scope="col">+5일</th><th scope="col">+20일</th></tr></thead><tbody>${tableRows}</tbody></table></div><p class="chart-note">후행 수익률이 아직 완결되지 않은 사건은 —로 표시합니다. 미완결값을 0으로 대체하지 않습니다.</p>`;
}

function renderDiagnostics() {
  const base = analysisEntity();
  const diag = store.dashboard.diagnostics || {};
  const appliedRows = selectedHistory();
  const periodStart = appliedRows[0]?.date || null;
  const periodEnd = appliedRows.at(-1)?.date || base.date || store.summary?.dataAsOf || null;
  const allRows = (diag.series || []).filter((row) => row?.date);
  const snapshotRow = [...allRows].reverse().find((row) => !periodEnd || row.date <= periodEnd) || null;
  const latest = snapshotRow && snapshotRow.date === diag.latest?.date
    ? { ...snapshotRow, ...diag.latest }
    : (snapshotRow || {});
  const rows = allRows.filter((row) => (!periodStart || row.date >= periodStart) && (!periodEnd || row.date <= periodEnd) && (row.muHynixRelativeSpread != null || row.muHynixRatioIndexed != null));
  const kospiBasisLabel = base.date || periodEnd || "산출 불가";
  const semiconductorBasisLabel = latest.date || "산출 불가";
  $("#diagnostic-list").innerHTML = [
    ["KOSPI 기준일", kospiBasisLabel],
    ["KOSPI 50일 이격도", fmt.score(base.disparity50, 1)],
    ["KOSPI MDD252", fmt.pct(base.mdd252)],
    ["반도체 기준일", semiconductorBasisLabel],
    ["Micron KRW MDD252", fmt.pct(latest.muMdd252)],
    ["SK하이닉스 MDD252", fmt.pct(latest.hynixMdd252)],
    ["삼성전자 MDD252", fmt.pct(latest.samsungMdd252)],
    ["MU / 하이닉스 비율", fmt.score(latest.muHynixRatio, 4)],
    ["MU / 하이닉스 비율지수", fmt.score(latest.muHynixRatioIndexed, 1)],
    ["MU / 하이닉스 상대 스프레드", latest.muHynixRelativeSpread == null ? "—" : `${fmt.score(latest.muHynixRelativeSpread, 2)}p`],
    ["미국 세션 정렬", diag.status === "ok" ? "KRX일 이전 세션" : "산출 불가"]
  ].map(([key, value]) => `<dt>${esc(key)}</dt><dd>${esc(value)}</dd>`).join("");
  const container = $("#diagnostic-chart");
  const tableRows = recentRows(rows).map((row) => [row.date, fmt.score(row.muHynixRatio, 4), fmt.score(row.muHynixRatioIndexed, 1), row.muHynixRelativeSpread == null ? "—" : `${fmt.score(row.muHynixRelativeSpread, 2)}p`]);
  $("#diagnostic-data-table").innerHTML = dataTable(["날짜", "MU/하이닉스 비율", "비율지수", "상대 스프레드"], tableRows, `선택 기간 최근 ${tableRows.length}개 상대 진단`);
  if (rows.length < 2) {
    const message = rows.length ? `${rows[0].date} 관측 1개 · 차트에는 2개 이상 필요` : "선택 기간 상대 스프레드 산출 불가";
    return showEmpty(container, message);
  }
  const field = rows.some((row) => Number.isFinite(Number(row.muHynixRelativeSpread))) ? "muHynixRelativeSpread" : "muHynixRatioIndexed";
  const values = rows.map((row) => Number(row[field])).filter(Number.isFinite);
  const w = 600, h = 280, p = { l: 68, r: 22, t: 34, b: 48 };
  const min0 = Math.min(...values), max0 = Math.max(...values), pad = (max0 - min0 || 1) * .08;
  const min = min0 - pad, max = max0 + pad;
  const x = scale(0, rows.length - 1, p.l, w - p.r), y = scale(min, max, h - p.b, p.t);
  const formatValue = field === "muHynixRelativeSpread" ? (value) => `${fmt.score(value, 1)}p` : (value) => fmt.score(value, 0);
  const ticks = niceTicks(min, max).map((value) => `<line class="grid-line" x1="${p.l}" y1="${y(value)}" x2="${w - p.r}" y2="${y(value)}"/><text class="axis-label" x="${p.l - 8}" y="${y(value) + 4}" text-anchor="end">${esc(formatValue(value))}</text>`).join("");
  const reference = field === "muHynixRelativeSpread" && min <= 0 && max >= 0 ? `<line class="reference-line" x1="${p.l}" y1="${y(0)}" x2="${w - p.r}" y2="${y(0)}"/>` : field === "muHynixRatioIndexed" && min <= 100 && max >= 100 ? `<line class="reference-line" x1="${p.l}" y1="${y(100)}" x2="${w - p.r}" y2="${y(100)}"/>` : "";
  const dateAxis = chartDateAxis(rows, x, { top: p.t, bottom: h - p.b, labelY: h - 19, maxTicks: 5 });
  container.innerHTML = `<svg viewBox="0 0 ${w} ${h}" aria-hidden="true"><rect class="chart-panel-bg" x="${p.l}" y="${p.t}" width="${w - p.l - p.r}" height="${h - p.t - p.b}"/>${dateAxis}${ticks}${reference}<line class="axis-line" x1="${p.l}" y1="${p.t}" x2="${p.l}" y2="${h - p.b}"/><line class="axis-line" x1="${p.l}" y1="${h - p.b}" x2="${w - p.r}" y2="${h - p.b}"/><g class="line-accent">${pathSegments(rows, field, x, y)}</g><text class="panel-title" x="${p.l}" y="19">${field === "muHynixRelativeSpread" ? "MU 대비 하이닉스 상대 스프레드 (지수포인트)" : "MU / 하이닉스 비율지수"}</text><text class="axis-unit" x="${w - p.r}" y="19" text-anchor="end">${field === "muHynixRelativeSpread" ? "미국 선행 세션 · 환율 환산" : "시작 = 100"}</text><text class="axis-title" x="${(p.l + w - p.r) / 2}" y="${h - 4}" text-anchor="middle">날짜 (KRX 거래일)</text></svg>`;
  container.setAttribute("aria-label", `${rows[0].date}부터 ${rows.at(-1).date}까지 선택 평가 기간의 ${field === "muHynixRelativeSpread" ? "Micron 대비 SK하이닉스 상대 스프레드" : "Micron 대 SK하이닉스 비율지수"}. 기간 끝 관측 ${formatValue(rows.at(-1)[field])}.`);
  attachChartNavigation(container, rows, (row) => `${row.date}, ${field === "muHynixRelativeSpread" ? "상대 스프레드" : "비율지수"} ${formatValue(row[field])}`, { viewBoxWidth: w, plotLeft: p.l, plotRight: w - p.r });
}

function renderFlowChannels() {
  const published = store.dashboard?.flowChannels?.channels;
  const publishedChannels = Array.isArray(published) && published.length ? published : [
    { channelId: "retail", participant: "individual", availability: "active", state: stateFromValue(modelPayload(primaryModelKind())), strategyUse: "primary", source: "pykrx" },
    { channelId: "foreign", participant: "foreign", availability: "planned", state: "unavailable", strategyUse: "future_extension" },
    { channelId: "institutional", participant: "institutional", availability: "planned", state: "unavailable", strategyUse: "future_extension" }
  ];
  const selected = analysisEntity();
  const selectedModel = modelPayload();
  const selectedDate = selected.date || store.summary?.dataAsOf;
  const channels = publishedChannels.map((channel) => {
    if (channel.strategyUse !== "primary" && !["individual", "retail"].includes(channel.participant)) {
      return { ...channel, basisDate: channel.dataAsOf || store.dashboard?.dataAsOf || store.summary?.dataAsOf };
    }
    return {
      ...channel,
      availability: "active",
      state: stateFromValue(selectedModel),
      percentile: selectedModel?.percentile ?? selectedModel?.sentimentPercentile,
      quality: selectedModel?.quality ?? selectedModel?.modelQuality,
      basisDate: selectedDate,
      source: channel.source || "authenticated_pykrx"
    };
  });
  const participantLabel = { individual: "개인", retail: "개인", foreign: "외국인", foreigner: "외국인", institutional: "기관", institution: "기관" };
  const useLabel = { primary: "현재 1차 신호", diagnostic_only: "진단 전용", future_extension: "향후 확장" };
  const qualityLabel = { ok: "정상", low_model_fit: "낮음 · 거래 미사용", unavailable: "산출 불가", not_activated: "미활성" };
  $("#flow-channels").innerHTML = channels.map((channel) => {
    const active = channel.availability === "active";
    const collecting = channel.availability === "collecting";
    const primary = channel.strategyUse === "primary";
    const state = active ? channel.state || "unavailable" : "unavailable";
    const availabilityLabel = active ? (primary ? "1차 신호" : "진단 가능") : collecting ? "수집 중" : "계획";
    const stateLabel = active ? labels[state] || state : collecting ? "표본 부족" : "미활성";
    const modelQuality = active ? qualityLabel[channel.quality] || channel.quality || "—" : collecting ? "표본 부족" : "미활성";
    const coverage = collecting ? `<div><dt>수집 표본</dt><dd>${esc(channel.observationCount ?? 0)}일</dd></div>` : "";
    return `<section class="flow-channel ${active ? "active" : "planned"}">
      <div><span class="channel-status ${active ? "active" : "planned"}">${availabilityLabel}</span><strong>${esc(participantLabel[channel.participant] || participantLabel[channel.channelId] || channel.participant || channel.channelId)}</strong></div>
      <dl><div><dt>역할</dt><dd>${esc(useLabel[channel.strategyUse] || channel.strategyUse || "—")}</dd></div><div><dt>상태</dt><dd>${esc(stateLabel)}</dd></div><div><dt>모형 품질</dt><dd>${esc(modelQuality)}</dd></div><div><dt>백분위</dt><dd>${esc(active ? fmt.score(channel.percentile, 1) : "—")}</dd></div>${coverage}<div><dt>기준일</dt><dd>${esc(channel.basisDate || "—")}</dd></div><div><dt>출처</dt><dd>${esc(channel.source || "활성화 전")}</dd></div></dl>
    </section>`;
  }).join("");
}

function showEmpty(container, message) {
  container.innerHTML = `<div class="empty"><strong>${esc(message)}</strong></div>`;
  container.setAttribute("aria-label", message);
  attachChartNavigation(container, [], () => message);
}

function updatePressed(selector, value, key) {
  $$(selector).forEach((button) => {
    const active = String(button.dataset[key]) === String(value);
    button.classList.toggle("active", active);
    button.setAttribute("aria-pressed", String(active));
  });
}

function syncSignalSettingControls() {
  const form = $("#signal-settings-form");
  const dirty = form?.dataset.dirty === "true";
  const values = {
    "#signal-lookback-input": store.signalLookback,
    "#signal-min-r2-input": store.signalMinimumR2,
    "#signal-tail-input": store.signalExtremeTail,
    "#signal-max-holding-input": store.signalMaxHolding
  };
  Object.entries(values).forEach(([selector, value]) => {
    const input = $(selector);
    if (input && !dirty && document.activeElement !== input) input.value = String(value);
  });
  const displayed = dirty ? {
    lookback: Number($("#signal-lookback-input")?.value),
    extremeTail: Number($("#signal-tail-input")?.value),
    maxHolding: Number($("#signal-max-holding-input")?.value)
  } : {
    lookback: Number(store.signalLookback),
    extremeTail: Number(store.signalExtremeTail),
    maxHolding: Number(store.signalMaxHolding)
  };
  const lookbackHelp = $("#signal-lookback-help");
  if (lookbackHelp && Number.isFinite(displayed.lookback)) lookbackHelp.textContent = `${Math.round(displayed.lookback / 21)}개월 상당 · 최소 ${Math.min(displayed.lookback, Math.max(40, Math.min(200, Math.ceil(displayed.lookback * .8))))}개 관측${dirty ? " · 미적용" : ""}`;
  const tailHelp = $("#signal-tail-help");
  if (tailHelp && Number.isFinite(displayed.extremeTail)) tailHelp.textContent = `공포 ≤${displayed.extremeTail} · 탐욕 ≥${100 - displayed.extremeTail}${dirty ? " · 미적용" : ""}`;
  const holdingHelp = $("#signal-max-holding-help");
  if (holdingHelp && Number.isFinite(displayed.maxHolding)) holdingHelp.textContent = `회복이 없으면 ${displayed.maxHolding}번째 보유 세션 종가 확인 후 다음 시가 청산${dirty ? " · 미적용" : ""}`;
}

function signalAppliedStatusText() {
  return `적용됨 · 과거 ${store.signalLookback}일 · 최소 R² ${Number(store.signalMinimumR2).toFixed(2)} · 극단 ≤${store.signalExtremeTail}/≥${100 - store.signalExtremeTail} · 최대 ${store.signalMaxHolding}일`;
}

function updateSignalDraftState() {
  const form = $("#signal-settings-form");
  const status = $("#signal-settings-status");
  const fields = [
    ["#signal-lookback-input", store.signalLookback],
    ["#signal-min-r2-input", store.signalMinimumR2],
    ["#signal-tail-input", store.signalExtremeTail],
    ["#signal-max-holding-input", store.signalMaxHolding]
  ];
  const dirty = fields.some(([selector, applied]) => {
    const input = $(selector);
    return !input || input.value.trim() === "" || !Number.isFinite(Number(input.value)) || Number(input.value) !== Number(applied);
  });
  if (dirty) setFormDirty(form, status, "미적용 변경 · 적용하면 관련 신호·사건·전략을 다시 계산합니다.");
  else {
    clearFormDirty(form);
    status.dataset.state = "ok";
    status.textContent = signalAppliedStatusText();
  }
  syncSignalSettingControls();
  return dirty;
}

function updateExitDraftState() {
  const form = $("#exit-threshold-form");
  const input = $("#exit-threshold-input");
  const status = $("#exit-threshold-status");
  const dirty = !input || input.value.trim() === "" || !Number.isFinite(Number(input.value)) || Number(input.value) !== Number(store.longExitPercentile);
  input?.setAttribute("aria-invalid", "false");
  if (dirty) setFormDirty(form, status, `미적용 변경 · 현재 전략 결과는 롱 ${store.longExitPercentile} / 인버스 ${100 - store.longExitPercentile} 청산 기준입니다.`);
  else {
    clearFormDirty(form);
    status.dataset.state = "ok";
    status.textContent = `적용됨 · 롱 ≥${store.longExitPercentile} · 인버스 ≤${100 - store.longExitPercentile}`;
  }
  return dirty;
}

function updateBacktestControls() {
  updatePressed("[data-backtest-pair]", store.backtestProxy, "backtestPair");
  updatePressed("[data-backtest-policy]", store.backtestPolicy, "backtestPolicy");
  updatePressed("[data-backtest-variant]", store.backtestVariant, "backtestVariant");
  updatePressed("[data-backtest-cost]", store.backtestCost, "backtestCost");
  updatePressed("[data-backtest-period]", store.backtestPeriod, "backtestPeriod");
  $$('[data-backtest-cost]').forEach((button) => { button.disabled = false; button.setAttribute("aria-disabled", "false"); });
  $$('[data-backtest-variant]').forEach((button) => {
    const variant = button.dataset.backtestVariant;
    const supported = variant === trackVariant();
    button.disabled = !supported;
    button.setAttribute("aria-disabled", String(!supported));
  });
  $$('[data-backtest-period]').forEach((button) => {
    button.disabled = false;
    button.setAttribute("aria-disabled", "false");
  });
  const available = resultFor();
  const bounds = available?.window || available?.range || {};
  const applied = bounds.appliedStartDate && bounds.appliedEndDate ? ` · 적용 ${bounds.appliedStartDate}–${bounds.appliedEndDate}` : "";
  $("#backtest-selection-note").textContent = available ? `${pairLabel(store.backtestProxy)} · ${modelName()} · 학습 ${store.signalLookback}일 · 극단 ${store.signalExtremeTail}% · 롱 청산 ${store.longExitPercentile}, 인버스 청산 ${100 - store.longExitPercentile} · 최대 ${store.signalMaxHolding}일${applied}` : "선택한 실제 ETF 페어의 조정가격 이력을 확인 중입니다.";
  $("#exit-threshold-value").textContent = `${store.longExitPercentile}`;
  $("#inverse-exit-threshold-value").textContent = `${100 - store.longExitPercentile}`;
  if ($("#exit-threshold-form")?.dataset.dirty !== "true") $("#exit-threshold-input").value = String(store.longExitPercentile);
  syncSignalSettingControls();
}

function initializeControlState() {
  let saved = {};
  let savedKey = null;
  try {
    for (const key of [CONTROL_STORAGE_KEY, ...LEGACY_CONTROL_STORAGE_KEYS]) {
      const value = localStorage.getItem(key);
      if (!value) continue;
      saved = JSON.parse(value);
      savedKey = key;
      break;
    }
  } catch (_) { saved = {}; }
  const legacyDefaults = savedKey && savedKey !== CONTROL_STORAGE_KEY
    && String(saved.window ?? "3y") === "3y"
    && String(saved.model ?? "robust") === "robust"
    && String(saved.eventSample ?? "nonOverlapping20d") === "nonOverlapping20d"
    && String(saved.backtestVariant ?? "scaled_huber") === "scaled_huber"
    && Number(saved.signalLookback ?? 252) === 252
    && Number(saved.signalMinimumR2 ?? 0.2) === 0.2
    && Number(saved.signalExtremeTail ?? 5) === 5
    && Number(saved.signalMaxHolding ?? 20) === 20;
  if (legacyDefaults) {
    saved = {
      ...saved,
      window: DEFAULT_CONTROLS.window,
      model: DEFAULT_CONTROLS.model,
      eventSample: DEFAULT_CONTROLS.eventSample,
      backtestVariant: DEFAULT_CONTROLS.backtestVariant,
      signalLookback: DEFAULT_CONTROLS.signalLookback,
      signalMinimumR2: DEFAULT_CONTROLS.signalMinimumR2,
      signalExtremeTail: DEFAULT_CONTROLS.signalExtremeTail,
      signalMaxHolding: DEFAULT_CONTROLS.signalMaxHolding
    };
  }
  const params = new URLSearchParams(location.search);
  Object.entries(CONTROL_QUERY).forEach(([key, param]) => {
    const legacyProxy = key === "backtestProxy" && params.has("proxy") ? params.get("proxy") : null;
    const candidate = params.has(param) ? params.get(param) : legacyProxy ?? saved[key];
    if (["historyStart", "historyEnd"].includes(key)) {
      if (isIsoDate(candidate)) store[key] = candidate;
      return;
    }
    if (key === "longExitPercentile") {
      try { store.longExitPercentile = normalizeLongExitPercentile(candidate ?? DEFAULT_LONG_EXIT_PERCENTILE); } catch (_) { store.longExitPercentile = DEFAULT_LONG_EXIT_PERCENTILE; }
      return;
    }
    if (["signalLookback", "signalMinimumR2", "signalExtremeTail", "signalMaxHolding"].includes(key)) {
      if (candidate != null && candidate !== "" && Number.isFinite(Number(candidate))) store[key] = Number(candidate);
      return;
    }
    let normalized = key === "backtestVariant" && candidate === "base" ? "scaled_ols" : String(candidate ?? "");
    if (key === "backtestProxy") normalized = ({ "226490": "1x", "069500": "1x", "122630": "2x", "252670": "2x" })[normalized] || normalized;
    if (key === "backtestPolicy" && normalized === "long_short_cash") normalized = "long_inverse_cash";
    if (key === "window") normalized = ({ "252": "1y", "756": "3y" })[normalized] || normalized;
    if (CONTROL_ALLOWED[key]?.includes(normalized)) store[key] = key === "backtestCost" ? Number(normalized) : normalized;
  });
  if (store.window === "custom" && (!isIsoDate(store.historyStart) || !isIsoDate(store.historyEnd) || store.historyStart > store.historyEnd)) store.window = DEFAULT_CONTROLS.window;
  if (!params.has("window") && saved.window == null && matchMedia("(max-width: 520px)").matches) store.window = DEFAULT_CONTROLS.window;
  try { currentSignalConfig(); } catch (_) {
    store.signalLookback = DEFAULT_CONTROLS.signalLookback;
    store.signalMinimumR2 = DEFAULT_CONTROLS.signalMinimumR2;
    store.signalExtremeTail = DEFAULT_CONTROLS.signalExtremeTail;
    store.signalMaxHolding = DEFAULT_CONTROLS.signalMaxHolding;
  }
  syncStrategyTrack();
}

function ensureHistoryRangeAvailable() {
  if (store.window !== "custom") return true;
  const rows = activeHistoryRows();
  const firstDate = rows[0]?.date;
  const latestDate = rows.at(-1)?.date;
  const valid = firstDate && latestDate && isIsoDate(store.historyStart) && isIsoDate(store.historyEnd) && store.historyStart <= store.historyEnd && store.historyStart >= firstDate && store.historyEnd <= latestDate && rows.some((row) => row.date >= store.historyStart && row.date <= store.historyEnd);
  if (valid) return true;
  store.window = DEFAULT_CONTROLS.window;
  store.historyStart = "";
  store.historyEnd = "";
  return false;
}

function persistControlState({ replaceUrl = true } = {}) {
  const values = Object.fromEntries(Object.keys(CONTROL_QUERY).map((key) => [key, store[key]]));
  try { localStorage.setItem(CONTROL_STORAGE_KEY, JSON.stringify(values)); } catch (_) { /* URL remains shareable */ }
  if (!replaceUrl) return;
  const url = new URL(location.href);
  Object.entries(CONTROL_QUERY).forEach(([key, param]) => {
    if (["historyStart", "historyEnd"].includes(key) && store.window !== "custom") url.searchParams.delete(param);
    else if (store[key] === "" || store[key] == null) url.searchParams.delete(param);
    else url.searchParams.set(param, store[key]);
  });
  history.replaceState(null, "", url);
}

function scrollChartLatest(chartId) {
  const chart = document.getElementById(chartId);
  if (!chart) return;
  chart.scrollTo({ left: Math.max(0, chart.scrollWidth - chart.clientWidth), behavior: matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth" });
  chart._selectLatest?.();
  chart.focus({ preventScroll: true });
}

function announceViewAction(message) {
  $("#view-action-status").textContent = message;
}

const researchControlDisabledState = new WeakMap();

function setResearchControlsEnabled(enabled) {
  const selector = [
    "[data-window]", "[data-model]", "[data-event-asset]", "[data-event-sample]",
    "[data-backtest-pair]", "[data-backtest-policy]", "[data-backtest-variant]",
    "[data-backtest-cost]", "[data-backtest-period]", "#history-range-form input",
    "#history-range-form button", "#signal-settings-form input", "#signal-settings-form button",
    "#exit-threshold-form input", "#exit-threshold-form button", "#reset-controls", "#share-view"
  ].join(",");
  $$(selector).forEach((control) => {
    if (!enabled) {
      if (!researchControlDisabledState.has(control)) researchControlDisabledState.set(control, control.disabled);
      control.disabled = true;
      return;
    }
    control.disabled = researchControlDisabledState.get(control) ?? false;
    researchControlDisabledState.delete(control);
  });
  $(".analysis-config")?.setAttribute("aria-busy", String(!enabled));
}

async function shareCurrentView() {
  if ($(".analysis-config")?.getAttribute("aria-busy") === "true") {
    announceViewAction("계산이 끝난 뒤 적용된 설정 링크를 복사할 수 있습니다.");
    return;
  }
  persistControlState();
  const text = location.href;
  const draftNote = hasUnappliedDrafts() ? " 미적용 변경은 제외됩니다." : "";
  try {
    await navigator.clipboard.writeText(text);
    announceViewAction(`적용된 설정 링크를 복사했습니다.${draftNote}`);
  } catch (_) {
    const input = document.createElement("input");
    input.value = text;
    input.setAttribute("aria-hidden", "true");
    document.body.append(input);
    input.select();
    const copied = document.execCommand?.("copy");
    input.remove();
    announceViewAction(copied ? `적용된 설정 링크를 복사했습니다.${draftNote}` : "주소창의 링크를 복사해 주세요.");
  }
}

function resetControls() {
  const snapshot = captureResearchSnapshot();
  try {
    ["#exit-threshold-form", "#signal-settings-form", "#history-range-form"].forEach((selector) => clearFormDirty($(selector)));
    Object.assign(store, DEFAULT_CONTROLS);
    syncStrategyTrack();
    recomputeDynamicResearch();
    const exitInput = $("#exit-threshold-input");
    const exitStatus = $("#exit-threshold-status");
    exitInput?.removeAttribute("aria-invalid");
    if (exitStatus) {
      delete exitStatus.dataset.state;
      exitStatus.textContent = "";
    }
    const signalStatus = $("#signal-settings-status");
    if (signalStatus) {
      signalStatus.dataset.state = "ok";
      signalStatus.textContent = signalAppliedStatusText();
    }
    ["#signal-lookback-input", "#signal-min-r2-input", "#signal-tail-input", "#signal-max-holding-input"].forEach((selector) => $(selector)?.removeAttribute("aria-invalid"));
    ["#history-start", "#history-end"].forEach((selector) => $(selector)?.removeAttribute("aria-invalid"));
    if (!modelPayload("robust")) store.model = modelPayload("scaled") ? "scaled" : "raw";
    ensureBacktestSelection();
    renderAll();
    persistControlState();
    announceViewAction("모든 화면 설정을 기본값으로 복원했습니다.");
  } catch (error) {
    restoreResearchSnapshot(snapshot);
    try { renderAll(); } catch (_) { /* retain the last complete DOM if rollback rendering also fails */ }
    announceViewAction(`기본값 복원에 실패해 기존 결과를 유지합니다. ${error instanceof Error ? error.message : ""}`.trim());
  }
}

function bindControls() {
  $$('[data-window]').forEach((button) => button.addEventListener("click", () => {
    applySynchronousControlChange(() => {
      clearFormDirty($("#history-range-form"));
      store.window = button.dataset.window;
      scenarioCache.clear();
    });
  }));
  $$('[data-model]').forEach((button) => button.addEventListener("click", async () => {
    if (button.disabled) return;
    const snapshot = captureResearchSnapshot();
    store.model = button.dataset.model;
    syncStrategyTrack();
    const status = $("#signal-settings-status");
    const form = $("#signal-settings-form");
    setResearchControlsEnabled(false);
    form.setAttribute("aria-busy", "true");
    status.dataset.state = "pending";
    status.textContent = `${modelName()} 신호 이력을 과거 정보만으로 다시 계산하는 중입니다…`;
    await new Promise((resolve) => requestAnimationFrame(() => resolve()));
    try {
      await recomputeDynamicResearchAsync({ onProgress: ({ ratio }) => {
        status.textContent = `${modelName()} 신호 이력을 과거 정보만으로 다시 계산하는 중입니다… ${Math.round(ratio * 100)}%`;
      } });
      renderAll();
      persistControlState();
      if (!updateSignalDraftState()) status.textContent = `적용됨 · ${modelName()} · 과거 ${store.signalLookback}일 · 최소 R² ${Number(store.signalMinimumR2).toFixed(2)} · 극단 ≤${store.signalExtremeTail}/≥${100 - store.signalExtremeTail}`;
    } catch (error) {
      restoreResearchSnapshot(snapshot);
      renderAll();
      if (!updateSignalDraftState()) {
        status.dataset.state = "error";
        status.textContent = error instanceof Error ? error.message : "연구 트랙을 다시 계산할 수 없습니다.";
      }
    } finally {
      setResearchControlsEnabled(true);
      form.setAttribute("aria-busy", "false");
    }
  }));
  $$('[data-event-asset]').forEach((button) => button.addEventListener("click", () => {
    applySynchronousControlChange(
      () => { store.eventAsset = button.dataset.eventAsset; },
      () => {
        updatePressed("[data-event-asset]", store.eventAsset, "eventAsset");
        renderEvents();
        renderConclusion();
      }
    );
  }));
  $$('[data-event-sample]').forEach((button) => button.addEventListener("click", () => {
    applySynchronousControlChange(
      () => { store.eventSample = button.dataset.eventSample; },
      () => {
        updatePressed("[data-event-sample]", store.eventSample, "eventSample");
        renderEvents();
        renderConclusion();
      }
    );
  }));
  $$('[data-backtest-pair]').forEach((button) => button.addEventListener("click", () => {
    applySynchronousControlChange(() => {
      store.backtestProxy = button.dataset.backtestPair;
      scenarioCache.clear();
      ensureBacktestSelection();
    });
  }));
  $$('[data-backtest-policy]').forEach((button) => button.addEventListener("click", () => {
    applySynchronousControlChange(() => {
      store.backtestPolicy = button.dataset.backtestPolicy;
      scenarioCache.clear();
    });
  }));
  $$('[data-backtest-variant]').forEach((button) => button.addEventListener("click", () => {
    applySynchronousControlChange(() => {
      store.backtestVariant = button.dataset.backtestVariant;
      store.model = ({ scaled_huber: "robust", scaled_ols: "scaled", raw_ols: "raw" })[store.backtestVariant] || store.model;
      scenarioCache.clear();
      ensureBacktestSelection();
    });
  }));
  $$('[data-backtest-cost]').forEach((button) => button.addEventListener("click", () => {
    applySynchronousControlChange(() => {
      store.backtestCost = Number(button.dataset.backtestCost);
      scenarioCache.clear();
      ensureBacktestSelection();
    });
  }));
  $$('[data-backtest-period]').forEach((button) => button.addEventListener("click", () => {
    applySynchronousControlChange(() => {
      store.backtestPeriod = button.dataset.backtestPeriod;
      scenarioCache.clear();
      ensureBacktestSelection();
    });
  }));
  $$('[data-chart-latest]').forEach((button) => button.addEventListener("click", () => scrollChartLatest(button.dataset.chartLatest)));
  $("#history-start").addEventListener("input", updateHistoryDraftState);
  $("#history-end").addEventListener("input", updateHistoryDraftState);
  ["#signal-lookback-input", "#signal-min-r2-input", "#signal-tail-input", "#signal-max-holding-input"].forEach((selector) => {
    const input = $(selector);
    input.addEventListener("input", () => {
      input.setAttribute("aria-invalid", "false");
      updateSignalDraftState();
    });
  });
  $("#exit-threshold-input").addEventListener("input", updateExitDraftState);
  $("#history-range-form").addEventListener("submit", applyCustomHistoryRange);
  $("#signal-settings-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = event.currentTarget;
    const inputs = {
      signalLookback: $("#signal-lookback-input"),
      signalMinimumR2: $("#signal-min-r2-input"),
      signalExtremeTail: $("#signal-tail-input"),
      signalMaxHolding: $("#signal-max-holding-input")
    };
    const status = $("#signal-settings-status");
    const button = $("#apply-signal-settings");
    const draft = Object.fromEntries(Object.entries(inputs).map(([key, input]) => [key, Number(input.value)]));
    const invalidInput = Object.values(inputs).find((input) => input.value.trim() === "" || !input.checkValidity());
    if (invalidInput) {
      Object.values(inputs).forEach((input) => input.setAttribute("aria-invalid", String(input === invalidInput)));
      form.dataset.dirty = "true";
      status.dataset.state = "error";
      status.textContent = "입력 범위에 맞는 신호 학습 값을 확인해 주세요. 기존 결과는 유지됩니다.";
      invalidInput.focus();
      return;
    }
    try {
      normalizeSignalConfig({
        track: store.model,
        lookback: draft.signalLookback,
        minimumR2: draft.signalMinimumR2,
        extremeTail: draft.signalExtremeTail
      });
      if (!Number.isInteger(draft.signalMaxHolding) || draft.signalMaxHolding < 1 || draft.signalMaxHolding > 60) throw new Error("최대 보유기간은 1~60 거래일 사이 정수여야 합니다.");
    } catch (error) {
      form.dataset.dirty = "true";
      status.dataset.state = "error";
      status.textContent = `${error instanceof Error ? error.message : "신호 학습 값을 확인해 주세요."} 기존 결과는 유지됩니다.`;
      return;
    }
    const snapshot = captureResearchSnapshot();
    try {
      Object.entries(inputs).forEach(([key, input]) => {
        store[key] = draft[key];
        input.removeAttribute("aria-invalid");
      });
      button.disabled = true;
      form.setAttribute("aria-busy", "true");
      status.dataset.state = "pending";
      status.textContent = "과거 정보만으로 전체 신호·사건·전략을 다시 계산하는 중입니다…";
      setResearchControlsEnabled(false);
      await new Promise((resolve) => requestAnimationFrame(() => resolve()));
      await recomputeDynamicResearchAsync({ onProgress: ({ ratio }) => {
        status.textContent = `과거 정보만으로 전체 신호·사건·전략을 다시 계산하는 중입니다… ${Math.round(ratio * 100)}%`;
      } });
      latestScenarioError = null;
      const expectedCount = store.backtestPolicy === "compare" ? 2 : 1;
      if (resultsForPolicySelection().filter(({ result }) => result?.metrics).length !== expectedCount || latestScenarioError) {
        throw latestScenarioError || new Error("선택 설정의 전략 경로를 계산할 수 없습니다.");
      }
      if (!selectedEventSection() && latestEventError) throw latestEventError;
      clearFormDirty(form);
      renderAll();
      persistControlState();
      status.dataset.state = "ok";
      status.textContent = signalAppliedStatusText();
    } catch (error) {
      restoreResearchSnapshot(snapshot);
      form.dataset.dirty = "true";
      try { renderAll(); } catch (_) { /* keep the last complete DOM if rollback rendering also fails */ }
      Object.entries(inputs).forEach(([key, input]) => {
        input.value = String(draft[key]);
        input.setAttribute("aria-invalid", "false");
      });
      syncSignalSettingControls();
      status.dataset.state = "error";
      status.textContent = `적용 실패 · 기존 결과를 유지합니다. ${error instanceof Error ? error.message : "신호 학습 설정을 적용할 수 없습니다."}`;
    } finally {
      setResearchControlsEnabled(true);
      button.disabled = false;
      form.setAttribute("aria-busy", "false");
    }
  });
  $("#exit-threshold-form").addEventListener("submit", (event) => {
    event.preventDefault();
    const input = $("#exit-threshold-input");
    const status = $("#exit-threshold-status");
    const form = event.currentTarget;
    let candidate;
    try {
      if (input.value.trim() === "" || !input.checkValidity()) throw new Error("청산 기준은 50~94 사이 정수로 입력해 주세요.");
      candidate = normalizeLongExitPercentile(input.value);
    } catch (error) {
      form.dataset.dirty = "true";
      input.setAttribute("aria-invalid", "true");
      status.dataset.state = "error";
      status.textContent = `${error instanceof Error ? error.message : "청산 기준을 확인해 주세요."} 기존 결과는 유지됩니다.`;
      input.focus();
      return;
    }
    const snapshot = captureResearchSnapshot();
    try {
      if (!store.dashboard || !store.history || !store.strategyComparison) throw new Error("공개 데이터를 불러온 뒤 다시 적용해 주세요.");
      store.longExitPercentile = candidate;
      scenarioCache.clear();
      latestScenarioError = null;
      const scenarioResults = resultsForPolicySelection().filter(({ result }) => result?.metrics);
      if (latestScenarioError) throw latestScenarioError;
      const expectedCount = store.backtestPolicy === "compare" ? 2 : 1;
      if (scenarioResults.length !== expectedCount) throw new Error("선택한 정책의 사용자 시나리오를 계산할 수 없습니다.");
      input.removeAttribute("aria-invalid");
      clearFormDirty(form);
      renderAll();
      persistControlState();
      status.dataset.state = "ok";
      status.textContent = `적용됨 · 롱 ≥${store.longExitPercentile} · 인버스 ≤${100 - store.longExitPercentile}`;
    } catch (error) {
      restoreResearchSnapshot(snapshot);
      form.dataset.dirty = "true";
      try { renderAll(); } catch (_) { /* keep the last complete DOM if rollback rendering also fails */ }
      input.value = String(candidate);
      input.setAttribute("aria-invalid", "false");
      status.dataset.state = "error";
      status.textContent = `적용 실패 · 기존 결과를 유지합니다. ${error instanceof Error ? error.message : "청산 기준을 적용할 수 없습니다."}`;
    }
  });
  $("#reset-controls").addEventListener("click", resetControls);
  $("#share-view").addEventListener("click", shareCurrentView);
}

function setTheme(theme) {
  const value = theme === "dark" ? "dark" : "light";
  document.documentElement.dataset.theme = value;
  document.documentElement.style.colorScheme = value;
  const button = $("#theme");
  button.setAttribute("aria-pressed", String(value === "dark"));
  button.setAttribute("aria-label", value === "dark" ? "라이트 모드로 전환" : "다크 모드로 전환");
  button.title = value === "dark" ? "라이트 테마로 전환" : "다크 테마로 전환";
  const label = button.querySelector(".theme-label");
  if (label) label.textContent = value === "dark" ? "라이트 모드" : "다크 모드";
}

function initializeTheme() {
  const requested = new URLSearchParams(location.search).get("theme");
  let saved = null;
  try { saved = localStorage.getItem("quant-calm-theme"); } catch (_) { /* storage can be unavailable */ }
  const preferred = matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
  setTheme(["light", "dark"].includes(requested) ? requested : (document.documentElement.dataset.theme || saved || preferred));
  $("#theme").addEventListener("click", () => {
    const next = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
    setTheme(next);
    try { localStorage.setItem("quant-calm-theme", next); } catch (_) { /* theme remains active for this page */ }
  });
}

function renderAll() {
  ["robust", "scaled", "raw"].forEach((kind) => {
    const button = $(`[data-model="${kind}"]`);
    const available = Boolean(modelPayload(kind));
    button.disabled = !available;
    button.setAttribute("aria-disabled", String(!available));
  });
  if (!modelPayload(store.model)) store.model = modelPayload(primaryModelKind()) ? primaryModelKind() : modelPayload("scaled") ? "scaled" : "raw";
  syncStrategyTrack();
  updatePressed("[data-model]", store.model, "model");
  updatePressed("[data-backtest-variant]", store.backtestVariant, "backtestVariant");
  updatePressed("[data-window]", store.window, "window");
  updatePressed("[data-event-asset]", store.eventAsset, "eventAsset");
  updatePressed("[data-event-sample]", store.eventSample, "eventSample");
  const scenarioBundle = selectedScenarioBundle();
  renderHeader(scenarioBundle);
  renderLiveSignal();
  renderHistory(scenarioBundle);
  renderScatter();
  renderResidual();
  renderEvents();
  renderBacktests(scenarioBundle);
  renderPdfSnapshot();
  renderDiagnostics();
  renderFlowChannels();
  renderConclusion(scenarioBundle);
  enhanceTables();
  applyTableFilter($("#trade-filter"));
}

initializeTheme();
initializeControlState();
bindControls();
bindTableTools();
initializeSectionNav();
setResearchControlsEnabled(false);

Promise.all([loadJson("data/summary.json"), loadJson("data/dashboard.json"), loadJson("data/history.json"), loadJson("data/strategy-comparison.json"), loadOptionalJson("data/live-signal.json"), loadOptionalJson("var/live-signal-local.json")])
  .then(([summary, dashboard, history, strategyComparison, publicLiveSignal, localLiveSignal]) => {
    validateContracts(summary, dashboard, history, strategyComparison);
    const liveCandidates = [];
    for (const candidate of [publicLiveSignal, localLiveSignal]) {
      try {
        const validated = validateLiveSignal(candidate, summary);
        if (validated) liveCandidates.push(validated);
      } catch (_) {
        // A malformed optional fast signal never blocks confirmed research.
      }
    }
    liveCandidates.sort((left, right) => left.signalDate.localeCompare(right.signalDate) || Date.parse(left.generatedAt) - Date.parse(right.generatedAt));
    const liveSignal = liveCandidates.at(-1) || null;
    store = { ...store, summary, dashboard, history: decodeHistory(history), strategyComparison, liveSignal };
    recomputeDynamicResearch();
    if (!modelPayload(store.model)) store.model = modelPayload("robust") ? "robust" : "scaled";
    ensureBacktestSelection();
    const rangeAvailable = ensureHistoryRangeAvailable();
    persistControlState({ replaceUrl: !rangeAvailable });
    setResearchControlsEnabled(true);
    renderAll();
    const signalStatus = $("#signal-settings-status");
    signalStatus.dataset.state = "ok";
    signalStatus.textContent = signalAppliedStatusText();
    if (!rangeAvailable) announceViewAction("저장된 사용자 기간이 공개 이력 밖이어서 YTD 기본 기간으로 복원했습니다.");
  })
  .catch((error) => {
    $("#status-badge").textContent = "unavailable";
    $("#status-badge").className = "badge unavailable";
    $("#signal-badge").textContent = "산출 불가";
    $("#signal-badge").className = "state-badge unavailable";
    $("#state").textContent = "데이터를 불러올 수 없음";
    $("#status-note").textContent = error.message;
    $("#metrics").innerHTML = metric("공개 계약", "unavailable", "마지막 정상 시장 수치를 임의 값으로 대체하지 않습니다.");
    $("#research-conclusion").innerHTML = `<div><p class="eyebrow">EVIDENCE FIRST</p><h2 id="conclusion-title">연구 결론을 표시할 수 없습니다</h2></div><p>공개 데이터 계약을 확인해 주세요. 임의 수치로 대체하지 않습니다.</p>`;
  });
