import {
  DEFAULT_LONG_EXIT_PERCENTILE,
  normalizeLongExitPercentile,
  runStrategyScenario
} from "./strategy-engine.js?v=20260717-release-v3";

const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => [...document.querySelectorAll(selector)];

const labels = {
  extreme_fear: "극단적 공포", fear: "공포", neutral: "중립", greed: "탐욕", extreme_greed: "극단적 탐욕",
  unavailable: "산출 불가", cash: "현금", long: "롱", short: "숏", recovery: "사용자 청산선 회복", max_holding: "최대 20일", opposite_extreme: "반대 극단 반전",
  enter_next_open: "다음 거래일 시가 진입", exit_next_open: "다음 거래일 시가 청산", reverse_next_open: "다음 거래일 시가 반전", extreme_fear_entry: "극단 공포 최초 진입", extreme_greed_entry: "극단 탐욕 최초 진입", hold: "보유 유지"
};

const qualityLabels = {
  ok: "정상",
  degraded: "주의",
  stale: "갱신 지연",
  unavailable: "산출 불가"
};

const DEGRADED_REASON_LABELS = Object.freeze({
  core_latest_common_date_alignment: "공급자 최신일 차이로 공통 거래일까지 계산",
  krx_credentials_missing: "KRX 인증정보 미설정",
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
  multiple: (value, digits = 2) => value == null || !Number.isFinite(Number(value)) ? "—" : `${Number(value).toFixed(digits)}×`
};

const esc = (value) => String(value ?? "").replace(/[&<>"]/g, (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "\"": "&quot;" })[char]);

const DEFAULT_CONTROLS = Object.freeze({
  window: "3y",
  historyStart: "",
  historyEnd: "",
  model: "robust",
  eventAsset: "KOSPI",
  eventSample: "nonOverlapping20d",
  backtestProxy: "226490",
  backtestPolicy: "compare",
  backtestVariant: "scaled_huber",
  backtestCost: 10,
  backtestPeriod: "common",
  longExitPercentile: DEFAULT_LONG_EXIT_PERCENTILE
});

const CONTROL_QUERY = Object.freeze({
  window: "window",
  historyStart: "start",
  historyEnd: "end",
  model: "model",
  eventAsset: "eventAsset",
  eventSample: "eventSample",
  backtestProxy: "proxy",
  backtestPolicy: "policy",
  backtestVariant: "strategy",
  backtestCost: "cost",
  backtestPeriod: "period",
  longExitPercentile: "exit"
});

const CONTROL_ALLOWED = Object.freeze({
  window: ["1m", "3m", "6m", "ytd", "1y", "3y", "all", "custom"],
  model: ["robust", "scaled", "raw"],
  eventAsset: ["KOSPI", "226490", "069500"],
  eventSample: ["nonOverlapping20d", "all"],
  backtestProxy: ["226490", "069500"],
  backtestPolicy: ["compare", "long_cash", "long_short_cash"],
  backtestVariant: ["scaled_huber", "scaled_ols", "raw_ols", "disparity"],
  backtestCost: ["0", "5", "10", "20"],
  backtestPeriod: ["common", "full"]
});

let store = {
  summary: null,
  dashboard: null,
  history: null,
  strategyComparison: null,
  ...DEFAULT_CONTROLS
};

async function loadJson(path) {
  const response = await fetch(path, { cache: "no-store" });
  if (!response.ok) throw new Error(`${path}: HTTP ${response.status}`);
  return response.json();
}

function validateContracts(summary, dashboard, history, strategyComparison) {
  if (summary?.schemaVersion !== 1 || summary?.contract !== "quant-research-summary" || summary?.projectId !== "fearngreed") throw new Error("summary.json 계약이 올바르지 않습니다.");
  const methodology = summary?.methodologyVersion;
  if (!/^fear-flow-v\d+$/.test(methodology || "") || dashboard?.methodologyVersion !== methodology || history?.methodologyVersion !== methodology) throw new Error("공개 데이터 방법론 버전이 올바르지 않습니다.");
  if (dashboard?.schemaVersion !== 1 || history?.schemaVersion !== 1 || strategyComparison?.schemaVersion !== 1 || strategyComparison?.contract !== "fearngreed-strategy-comparison" || dashboard?.dataAsOf !== summary.dataAsOf || history?.dataAsOf !== summary.dataAsOf || strategyComparison?.dataAsOf !== summary.dataAsOf || strategyComparison?.methodologyVersion !== methodology) throw new Error("공개 데이터 스키마 또는 기준일이 올바르지 않습니다.");
  const dynamicControl = strategyComparison?.dynamicExitControl;
  const historyScenario = history?.strategyScenario;
  if (dynamicControl?.defaultLongExitPercentile !== DEFAULT_LONG_EXIT_PERCENTILE || dynamicControl?.minimum !== 50 || dynamicControl?.maximum !== 94 || dynamicControl?.shortExitFormula !== "100-longExitPercentile" || dynamicControl?.regressionRefit !== false || historyScenario?.defaultLongExitPercentile !== DEFAULT_LONG_EXIT_PERCENTILE || historyScenario?.browserMayRefitRegression !== false) throw new Error("사용자 청산 시나리오 계약이 올바르지 않습니다.");
  const hasSeries = Array.isArray(history.series) || (Array.isArray(history.seriesColumns) && Array.isArray(history.seriesRows));
  const models = summary.primaryEntities?.[0]?.models || dashboard?.models || {};
  if (!["ok", "degraded", "stale", "unavailable"].includes(summary?.status?.state) || !Array.isArray(summary.primaryEntities) || summary.primaryEntities.length !== 1 || !models.scaled || !models.raw || !hasSeries || summary?.payload?.strategyComparisonUrl !== "./strategy-comparison.json") throw new Error("공개 데이터의 필수 계약이 없습니다.");
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
  if (value == null) return "unavailable";
  if (value <= 5) return "extreme_fear";
  if (value <= 20) return "fear";
  if (value < 80) return "neutral";
  if (value < 95) return "greed";
  return "extreme_greed";
}

function effectiveStatus(summary) {
  const dataDate = new Date(`${summary.dataAsOf}T00:00:00Z`);
  const ageDays = Math.floor((Date.now() - dataDate.getTime()) / 86_400_000);
  if (Number.isFinite(ageDays) && ageDays > summary.status.expectedFreshnessDays) return "stale";
  return summary.status.state;
}

function entity() {
  return store.summary?.primaryEntities?.[0] || {};
}

function primaryModelKind() {
  const declared = store.dashboard?.primaryModel || store.summary?.payload?.primaryModel || entity().primaryModel;
  if (["robust", "scaled", "raw"].includes(declared)) return declared;
  return modelPayload("robust") ? "robust" : "scaled";
}

function modelPayload(kind = store.model) {
  const base = entity();
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
  const regression = store.dashboard?.regression || {};
  return regression[kind] || store.dashboard?.models?.[kind] || (kind === "scaled" ? regression : modelPayload(kind)) || {};
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

function metric(label, value, note) {
  return `<article class="metric"><span>${esc(label)}</span><strong title="${esc(value)}">${esc(value)}</strong><small>${esc(note)}</small></article>`;
}

function bridgeStep(step, label, value, note, tone = "") {
  return `<li class="bridge-step ${esc(tone)}"><span>${esc(step)}</span><strong>${esc(label)}</strong><b>${esc(value)}</b><small>${esc(note)}</small></li>`;
}

function renderSignalBridge() {
  const base = entity();
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
    ${bridgeStep("5", "연구 상태", labels[state] || state, model?.tradeEligible === false ? "신규 거래 신호 차단" : "고정 경계 적용", tone)}
  </ol>`;
  const difference = residual == null ? Number.NaN : Number(residual);
  let interpretation = "현재 잔차를 산출할 수 없습니다.";
  if (Number.isFinite(difference)) {
    const direction = difference < 0 ? "예상보다 개인 수급이 더 약해 공포 방향" : difference > 0 ? "예상보다 개인 수급이 덜 약하거나 더 강해 탐욕 방향" : "예상과 일치해 중립 방향";
    interpretation = `${unitFormat(actual)}의 절대값만 보지 않습니다. 같은 날 수익률의 기대값 ${unitFormat(expected)}에 비해 ${direction}입니다.`;
  }
  $("#bridge-explanation").textContent = interpretation;
}

function renderHeader() {
  const summary = store.summary;
  const base = entity();
  const model = modelPayload();
  const status = effectiveStatus(summary);
  const state = status === "unavailable" ? "unavailable" : stateFromValue(model);
  const badge = $("#status-badge");
  badge.textContent = `데이터 ${qualityLabel(status)}`;
  badge.className = `badge ${status}`;
  $("#confidence-badge").textContent = `${modelRole()} · 품질 ${qualityLabel(model?.quality || base.modelQuality)}`;
  $("#state").textContent = labels[state] || state;
  $("#signal-badge").textContent = labels[state] || state;
  $("#signal-badge").className = `state-badge ${state}`;
  $("#asof").textContent = `기준일 ${fmt.date(summary.dataAsOf)}`;
  const reasons = summary.status.degradedReasons || [];
  $("#status-note").textContent = reasons.length ? `운영 주의: ${reasons.map(degradedReasonLabel).join(" · ")}. 표시 기준일과 공급자 상태를 함께 확인하세요.` : "핵심 공급자와 계산 품질 게이트 통과";

  const percentile = model?.percentile ?? model?.sentimentPercentile;
  const inputValue = store.model === "raw" ? `${fmt.score(base.rawFlowTrillion, 3)}조원` : fmt.pct(base.flowShare, 3);
  const inputNote = store.model === "raw" ? "개인 순매수대금 / 1조원" : "KOSPI 거래대금 대비";
  $("#metrics").innerHTML = [
    metric("감정 백분위", fmt.score(percentile), `${modelName()} · 직전 252일 잔차 분포`),
    metric("잔차 z", fmt.score(model?.residualZ, 2), "median / 1.4826×MAD"),
    metric("롤링 R²", fmt.score(model?.rollingR2, 3), "현재일 제외 · 품질 기준 0.20"),
    metric("KOSPI 1일", fmt.signedPct(base.return1d), "종가 대비 전 거래일"),
    metric(store.model === "raw" ? "개인 순매수대금" : "개인 순매수율", inputValue, inputNote),
    metric("50일 이격도", fmt.score(base.disparity50, 1), "100 = 50일 이동평균"),
    metric("현재 포지션 모형", labels[base.position] || labels.unavailable, `${base.primaryProxy || "226490"} · ${pendingActionText(base)}`),
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
  $("#quality-strip").innerHTML = [
    `<span><strong>데이터 기준일:</strong> ${esc(summary.dataAsOf)}</span>`,
    `<span><strong>선택 트랙:</strong> ${esc(modelName())}</span>`,
    `<span><strong>β:</strong> ${esc(fmt.score(beta, 4))}</span>`,
    `<span><strong>모형 품질:</strong> ${esc(qualityLabel(model?.quality || base.modelQuality))}</span>`,
    `<span><strong>가격:</strong> ${esc(base.sources?.price || base.sourceMode || "KRX")}</span>`,
    `<span><strong>수급:</strong> ${esc(base.sources?.flow || "pykrx 파생")}</span>`,
    `<span><strong>ETF 조정가:</strong> ${esc(base.sources?.adjustedPrice || adjustedSource)}</span>`,
    ...reconciliationItems,
    `<span><strong>관측치:</strong> ${esc(fmt.compact(summary.coverage.observationCount))}</span>`,
    `<span><strong>비중첩 사건:</strong> ${esc(summary.coverage.eventCount)}</span>`,
    `<span><strong>거래:</strong> ${esc(summary.coverage.tradeCount)}</span>`
  ].join("");
  const eventsFollowModel = Boolean(store.dashboard?.eventsByModel?.[store.model]);
  $("#model-selection-note").textContent = `${modelName()}의 서버 사전 계산값입니다. 현재값·해석 브리지·산점도${eventsFollowModel ? "·사건 검증" : ""}에 적용되며, 전략은 별도 신호·진입 규칙에서 선택합니다.`;
  $("#scatter-model-scope").textContent = modelRole();
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
  const rows = store.history?.series || [];
  if (!rows.length) return [];
  const firstDate = rows[0].date;
  const latestDate = rows.at(-1).date;
  const start = store.window === "custom" ? store.historyStart : historyStartForWindow(store.window, latestDate, firstDate);
  const end = store.window === "custom" ? store.historyEnd : latestDate;
  if (!isIsoDate(start) || !isIsoDate(end) || start > end) return [];
  return rows.filter((row) => row.date >= start && row.date <= end);
}

function historyWindowLabel(value = store.window) {
  return ({ "1m": "최근 1개월", "3m": "최근 3개월", "6m": "최근 6개월", ytd: "연초 이후", "1y": "최근 1년", "3y": "최근 3년", all: "전체 기간", custom: "사용자 지정" })[value] || "선택 기간";
}

function syncHistoryRangeControls(rows) {
  const allRows = store.history?.series || [];
  const firstDate = allRows[0]?.date || "";
  const latestDate = allRows.at(-1)?.date || "";
  const startInput = $("#history-start");
  const endInput = $("#history-end");
  if (!startInput || !endInput || !firstDate || !latestDate) return;
  [startInput, endInput].forEach((input) => {
    input.min = firstDate;
    input.max = latestDate;
    input.setAttribute("aria-invalid", "false");
  });
  startInput.value = store.window === "custom" ? store.historyStart : (rows[0]?.date || firstDate);
  endInput.value = store.window === "custom" ? store.historyEnd : (rows.at(-1)?.date || latestDate);
  $("#history-range-status").dataset.state = "ok";
  $("#history-range-status").textContent = rows.length
    ? `${historyWindowLabel()} · ${rows[0].date}–${rows.at(-1).date} · ${rows.length.toLocaleString()}거래일`
    : "선택한 기간에 표시할 거래일이 없습니다.";
}

function setHistoryRangeError(message, input = null) {
  const status = $("#history-range-status");
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
  const rows = store.history?.series || [];
  const firstDate = rows[0]?.date;
  const latestDate = rows.at(-1)?.date;
  if (!isIsoDate(start)) return setHistoryRangeError("올바른 시작일을 입력해 주세요.", startInput);
  if (!isIsoDate(end)) return setHistoryRangeError("올바른 종료일을 입력해 주세요.", endInput);
  if (start > end) return setHistoryRangeError("시작일은 종료일보다 늦을 수 없습니다.", startInput);
  if (start < firstDate || end > latestDate) return setHistoryRangeError(`공개 이력 범위 ${firstDate}–${latestDate} 안에서 선택해 주세요.`, start < firstDate ? startInput : endInput);
  if (!rows.some((row) => row.date >= start && row.date <= end)) return setHistoryRangeError("선택한 기간에 KOSPI 거래일이 없습니다.", startInput);
  startInput.setAttribute("aria-invalid", "false");
  endInput.setAttribute("aria-invalid", "false");
  store.window = "custom";
  store.historyStart = start;
  store.historyEnd = end;
  persistControlState();
  updatePressed("[data-window]", store.window, "window");
  renderHistory();
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

function attachChartNavigation(chart, items, formatter) {
  const valid = items.filter(Boolean);
  chart._resizeObserver?.disconnect();
  chart._chartItems = valid;
  chart._chartIndex = Math.max(0, valid.length - 1);
  chart.querySelectorAll(".chart-crosshair, .chart-crosshair-point").forEach((node) => node.remove());
  const crosshair = document.createElement("span");
  crosshair.className = "chart-crosshair";
  crosshair.setAttribute("aria-hidden", "true");
  chart.append(crosshair);
  const selectIndex = (index, point = null) => {
    if (!valid.length) return;
    chart._chartIndex = Math.max(0, Math.min(valid.length - 1, index));
    const ratio = valid.length <= 1 ? 1 : chart._chartIndex / (valid.length - 1);
    crosshair.style.left = `${Math.max(0, Math.min(chart.scrollWidth, ratio * chart.scrollWidth))}px`;
    chart.classList.add("is-exploring");
    const text = formatter(valid[chart._chartIndex], chart._chartIndex);
    showTooltip(chart, text, point);
    chart.setAttribute("aria-valuetext", text);
  };
  chart.onfocus = () => {
    if (valid.length) selectIndex(chart._chartIndex);
    else showTooltip(chart, chart.getAttribute("aria-label") || "차트");
  };
  const selectPointer = (event) => {
    if (!valid.length) return;
    const box = chart.getBoundingClientRect();
    const ratio = Math.max(0, Math.min(1, (event.clientX - box.left + chart.scrollLeft) / Math.max(1, chart.scrollWidth)));
    selectIndex(Math.round(ratio * (valid.length - 1)), { x: event.clientX, y: event.clientY });
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
  let latestAligned = false;
  const alignLatest = () => {
    if (latestAligned || chart.clientWidth <= 0 || chart.scrollWidth <= chart.clientWidth) return;
    chart.scrollLeft = chart.scrollWidth - chart.clientWidth;
    latestAligned = true;
  };
  requestAnimationFrame(alignLatest);
  if (typeof ResizeObserver !== "undefined") {
    const observer = new ResizeObserver(alignLatest);
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

function renderHistory() {
  const primary = primaryModelKind();
  $("#history-model-scope").textContent = `${modelRole(primary)} · ${compactModelName(primary)}`;
  $("#history-model-scope").className = `scope-badge ${primary === "robust" ? "practical" : "baseline"}`;
  const rows = selectedHistory().filter((row) => row.kospiClose != null || row.kospi != null);
  syncHistoryRangeControls(rows);
  const container = $("#history-chart");
  if (rows.length < 8) {
    $("#history-exposure-note").innerHTML = "<strong>기간을 넓혀 주세요.</strong><span>추세 차트는 최소 8거래일이 필요합니다. 아래 값 표에는 선택 기간의 관측값을 유지합니다.</span>";
    const tableRows = rows.map((row) => [row.date, Number(row.kospiClose ?? row.kospi).toLocaleString(), labels[row.state] || row.state, labels[row.position] || row.position]);
    $("#history-data-table").innerHTML = dataTable(["날짜", "KOSPI", "연구 상태", "포지션"], tableRows, `선택 기간 ${tableRows.length}개 관측값`);
    return showEmpty(container, "추세를 표시하려면 8거래일 이상을 선택해 주세요.");
  }
  const values = rows.map((row) => Number(row.kospiClose ?? row.kospi));
  const w = 1040, h = 390, p = { l: 66, r: 22, t: 18, b: 38 };
  const min = Math.min(...values), max = Math.max(...values), pad = (max - min || 1) * .08;
  const x = scale(0, rows.length - 1, p.l, w - p.r);
  const y = scale(min - pad, max + pad, h - p.b, p.t);
  const zones = [];
  let start = null;
  rows.forEach((row, index) => {
    if (row.position === "long" && start == null) start = index;
    if ((row.position !== "long" || index === rows.length - 1) && start != null) {
      const end = row.position === "long" && index === rows.length - 1 ? index : index - 1;
      zones.push(`<rect class="holding-zone" x="${x(start)}" y="${p.t}" width="${Math.max(3, x(end) - x(start))}" height="${h - p.t - p.b}"/>`);
      start = null;
    }
  });
  const events = rows.map((row, index) => {
    if (!row.tradeEligible || !["extreme_fear", "extreme_greed"].includes(row.state)) return "";
    const cls = row.state === "extreme_fear" ? "event-fear" : "event-greed";
    const title = `${row.date} · ${labels[row.state]} · ${fmt.score(row.percentile)}`;
    if (row.state === "extreme_greed") return `<rect class="${cls}" x="${x(index) - 4}" y="${y(row.kospiClose ?? row.kospi) - 4}" width="8" height="8" transform="rotate(45 ${x(index)} ${y(row.kospiClose ?? row.kospi)})"><title>${esc(title)}</title></rect>`;
    return `<circle class="${cls}" cx="${x(index)}" cy="${y(row.kospiClose ?? row.kospi)}" r="5"><title>${esc(title)}</title></circle>`;
  }).join("");
  const allRows = store.history?.series || [];
  const firstGlobalIndex = allRows.findIndex((row) => row.date === rows[0].date);
  const entryRows = rows.map((row, index) => {
    const previous = allRows[firstGlobalIndex + index - 1];
    if (row.position !== "long" || previous?.position === "long") return "";
    const px = x(index), py = y(row.kospiClose ?? row.kospi);
    return `<path class="entry-long" d="M ${px} ${py - 8} L ${px - 6} ${py + 5} L ${px + 6} ${py + 5} Z"><title>${esc(`${row.date} · 실제 롱 포지션 시작`)}</title></path>`;
  }).join("");
  const ticks = linearTicks(min - pad, max + pad).map((value) => {
    const yy = y(value);
    return `<line class="grid-line" x1="${p.l}" y1="${yy}" x2="${w - p.r}" y2="${yy}"/><text class="axis-label" x="${p.l - 9}" y="${yy + 3}" text-anchor="end">${Math.round(value).toLocaleString()}</text>`;
  }).join("");
  container.innerHTML = `<svg viewBox="0 0 ${w} ${h}" aria-hidden="true">${zones.join("")}${ticks}<line class="axis-line" x1="${p.l}" y1="${p.t}" x2="${p.l}" y2="${h - p.b}"/><line class="axis-line" x1="${p.l}" y1="${h - p.b}" x2="${w - p.r}" y2="${h - p.b}"/><g class="line-price">${pathSegments(rows, (row) => row.kospiClose ?? row.kospi, x, y)}</g>${events}${entryRows}<text class="axis-title" x="${p.l}" y="12">KOSPI 종가</text><text class="axis-label" x="${p.l}" y="${h - 10}">${esc(rows[0].date)}</text><text class="axis-label" x="${w - p.r}" y="${h - 10}" text-anchor="end">${esc(rows.at(-1).date)}</text></svg>`;
  container.setAttribute("aria-label", `${rows[0].date}부터 ${rows.at(-1).date}까지 KOSPI 종가. 최저 ${Math.round(min)}, 최고 ${Math.round(max)}.`);
  attachChartNavigation(container, rows, (row) => `${row.date}, KOSPI ${Number(row.kospiClose ?? row.kospi).toLocaleString()}, ${labels[row.state] || row.state}, 포지션 ${labels[row.position] || row.position}`);
  const positionRows = rows.filter((row) => ["long", "cash"].includes(row.position));
  const longSessions = positionRows.filter((row) => row.position === "long").length;
  const cashSessions = positionRows.filter((row) => row.position === "cash").length;
  const unavailableSessions = rows.length - positionRows.length;
  const entryCount = (entryRows.match(/class="entry-long"/g) || []).length;
  const fearObservations = rows.filter((row) => row.tradeEligible && row.state === "extreme_fear").length;
  const unavailableText = unavailableSessions ? ` · 포지션 산출 전/불가 ${unavailableSessions.toLocaleString()}일(노출도 분모 제외)` : "";
  $("#history-exposure-note").innerHTML = `<strong>선택 기간 노출도 ${esc(positionRows.length ? fmt.pct(longSessions / positionRows.length, 1) : "—")}</strong><span>롱 ${longSessions.toLocaleString()}일 · 현금 ${cashSessions.toLocaleString()}일 · 실제 신규 진입 ${entryCount.toLocaleString()}회${esc(unavailableText)}</span><span>극단 공포 ${fearObservations.toLocaleString()}일은 상태 관측 수입니다. 이 상단 KOSPI 보유구간은 서버 검증 기본 80 청산 경로이며, 아래 사용자 입력 시나리오는 별도로 재계산합니다.</span>`;
  const tableRows = recentRows(rows).map((row) => [row.date, Number(row.kospiClose ?? row.kospi).toLocaleString(), labels[row.state] || row.state, labels[row.position] || row.position]);
  $("#history-data-table").innerHTML = dataTable(["날짜", "KOSPI", "연구 상태", "포지션"], tableRows, `선택 기간의 최근 ${tableRows.length}개 관측값`);
}

function scatterPoints() {
  const published = store.dashboard?.scatterByModel?.[store.model];
  if (Array.isArray(published)) {
    const field = store.model === "raw" ? "rawFlowTrillion" : "flowShare";
    return published.filter((row) => row.return1d != null && (row.y != null || row[field] != null)).map((row, index, rows) => ({ ...row, y: row.y ?? row[field], role: row.role || (index === rows.length - 1 ? "current" : "training") }));
  }
  if (store.model === "scaled" && Array.isArray(store.dashboard.scatter)) return store.dashboard.scatter.filter((row) => row.return1d != null && row.flowShare != null).map((row, index, rows) => ({ ...row, y: row.flowShare, role: row.role || (index === rows.length - 1 ? "current" : "training") }));
  const model = modelPayload(store.model);
  if (!model) return [];
  const count = Number(model.trainingCount || 252) + 1;
  const field = store.model === "raw" ? "rawFlowTrillion" : "flowShare";
  const rows = (store.history.series || []).filter((row) => row.return1d != null && row[field] != null).slice(-count);
  return rows.map((row, index) => ({ ...row, y: row[field], role: index === rows.length - 1 ? "current" : "training" }));
}

function publishedScatterStateBoundaries() {
  const boundaries = store.dashboard?.scatterMetaByModel?.[store.model]?.stateBoundaries;
  const offsets = boundaries?.residualOffsets;
  const required = [offsets?.extremeFearUpper, offsets?.fearUpper, offsets?.greedLower, offsets?.extremeGreedLower];
  if (required.some((value) => typeof value !== "number")) return null;
  const numeric = required.map(Number);
  const ordered = numeric.every((value, index) => index === 0 || value >= numeric[index - 1]);
  if (boundaries?.method !== "empirical_cdf_transition_order_statistic" || boundaries?.fitScope !== "current_fit_on_prior_window" || !numeric.every(Number.isFinite) || !ordered || Number(boundaries.trainingCount) < 20) return null;
  return {
    count: Number(boundaries.trainingCount),
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
    $("#scatter-note").textContent = "사용할 수 없는 모형을 브라우저에서 추정해 대체하지 않습니다.";
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
  const xTicks = linearTicks(xmin, xmax).map((value) => `<line class="grid-line" x1="${x(value)}" y1="${p.t}" x2="${x(value)}" y2="${h - p.b}"/><text class="axis-label" x="${x(value)}" y="${h - p.b + 17}" text-anchor="middle">${esc(fmt.pct(value, 1))}</text>`).join("");
  const yFormat = store.model === "raw" ? (value) => `${Number(value).toFixed(1)}조` : (value) => fmt.pct(value, 1);
  const yTicks = linearTicks(ymin, ymax).map((value) => `<line class="grid-line" x1="${p.l}" y1="${y(value)}" x2="${w - p.r}" y2="${y(value)}"/><text class="axis-label" x="${p.l - 8}" y="${y(value) + 3}" text-anchor="end">${esc(yFormat(value))}</text>`).join("");
  const extremeZones = stateBoundaries ? (() => {
    const lineY = (offset, xValue) => y(predicted(xValue) + offset);
    const fearExtremeLeft = lineY(stateBoundaries.extremeFearUpper, xmin), fearExtremeRight = lineY(stateBoundaries.extremeFearUpper, xmax);
    const fearLeft = lineY(stateBoundaries.fearUpper, xmin), fearRight = lineY(stateBoundaries.fearUpper, xmax);
    const greedLeft = lineY(stateBoundaries.greedLower, xmin), greedRight = lineY(stateBoundaries.greedLower, xmax);
    const greedExtremeLeft = lineY(stateBoundaries.extremeGreedLower, xmin), greedExtremeRight = lineY(stateBoundaries.extremeGreedLower, xmax);
    return `<g clip-path="url(#scatter-plot-clip)"><polygon class="scatter-zone scatter-zone-extreme-fear" points="${x(xmin)},${fearExtremeLeft} ${x(xmax)},${fearExtremeRight} ${x(xmax)},${h - p.b} ${x(xmin)},${h - p.b}"/><polygon class="scatter-zone scatter-zone-fear" points="${x(xmin)},${fearLeft} ${x(xmax)},${fearRight} ${x(xmax)},${fearExtremeRight} ${x(xmin)},${fearExtremeLeft}"/><polygon class="scatter-zone scatter-zone-greed" points="${x(xmin)},${greedExtremeLeft} ${x(xmax)},${greedExtremeRight} ${x(xmax)},${greedRight} ${x(xmin)},${greedLeft}"/><polygon class="scatter-zone scatter-zone-extreme-greed" points="${x(xmin)},${p.t} ${x(xmax)},${p.t} ${x(xmax)},${greedExtremeRight} ${x(xmin)},${greedExtremeLeft}"/>${[[stateBoundaries.extremeFearUpper, "fear extreme"], [stateBoundaries.fearUpper, "fear"], [stateBoundaries.greedLower, "greed"], [stateBoundaries.extremeGreedLower, "greed extreme"]].map(([offset, cls]) => `<line class="scatter-boundary ${cls}" x1="${x(xmin)}" y1="${lineY(offset, xmin)}" x2="${x(xmax)}" y2="${lineY(offset, xmax)}"/>`).join("")}</g><text class="scatter-zone-label fear" x="${p.l + 8}" y="${h - p.b - 10}">극단적 공포 ≤5%</text><text class="scatter-zone-label greed" x="${p.l + 8}" y="${p.t + 16}">극단적 탐욕 ≥95%</text>`;
  })() : "";
  const pointGeometry = points.map((row) => ({ row, plotX: x(row.return1d), plotY: y(row.y) }));
  const marks = pointGeometry.map(({ row, plotX, plotY }) => `<circle class="scatter-point ${row.role === "current" ? "current" : ""}" cx="${plotX}" cy="${plotY}" r="${row.role === "current" ? 6 : 3}"><title>${esc(`${row.date} · 수익률 ${fmt.pct(row.return1d)} · ${store.model === "raw" ? `순매수 ${fmt.score(row.y, 3)}조원` : `순매수율 ${fmt.pct(row.y, 3)}`}`)}</title></circle>`).join("");
  const current = points.find((row) => row.role === "current") || points.at(-1);
  const currentPredicted = predicted(current.return1d);
  const residual = Number.isFinite(currentPredicted) ? `<line class="residual-arrow" x1="${x(current.return1d)}" y1="${y(currentPredicted)}" x2="${x(current.return1d)}" y2="${y(current.y)}"/>` : "";
  const regressionLine = predictedEnds.length === 2 ? `<line class="regression-line" x1="${x(xmin)}" y1="${y(predicted(xmin))}" x2="${x(xmax)}" y2="${y(predicted(xmax))}"/>` : "";
  container.innerHTML = `<svg viewBox="0 0 ${w} ${h}" aria-hidden="true"><defs><clipPath id="scatter-plot-clip"><rect x="${p.l}" y="${p.t}" width="${w - p.l - p.r}" height="${h - p.t - p.b}"/></clipPath></defs>${extremeZones}${xTicks}${yTicks}<line class="axis-line" x1="${p.l}" y1="${p.t}" x2="${p.l}" y2="${h - p.b}"/><line class="axis-line" x1="${p.l}" y1="${h - p.b}" x2="${w - p.r}" y2="${h - p.b}"/>${regressionLine}${residual}${marks}<text class="axis-title" x="${(p.l + w - p.r) / 2}" y="${h - 7}" text-anchor="middle">KOSPI 1일 수익률 (%)</text><text class="axis-title" x="${p.l}" y="13">${store.model === "raw" ? "개인 순매수대금 (조원)" : "개인 순매수율 (%)"}</text></svg>`;
  const inputLabel = store.model === "raw" ? "개인 순매수대금" : "개인 순매수율";
  $("#scatter-title").textContent = `수익률 × ${inputLabel}`;
  $("#scatter-subtitle").textContent = `${modelName()} · 직전 ${regression.trainingCount || regression.window || points.length - 1}거래일과 현재 관측`;
  const zoneAria = stateBoundaries ? "회귀선 대비 최신 학습 잔차의 경험적 5% 이하 극단 공포와 95% 이상 극단 탐욕 영역을 표시합니다." : "정확한 최신 회귀 상태 경계가 없어 극단 영역은 표시하지 않습니다.";
  container.setAttribute("aria-label", `${points.length}개 관측치 ${modelName()} 산점도. ${zoneAria} 현재 수익률 ${fmt.pct(current.return1d)}, ${inputLabel} ${store.model === "raw" ? `${fmt.score(current.y, 3)}조원` : fmt.pct(current.y, 3)}.`);
  const cutoffFormat = store.model === "raw" ? (value) => `${fmt.score(value, 3)}조원` : (value) => `${(Number(value) * 100).toFixed(2)}%p`;
  const zoneNote = stateBoundaries
    ? ` · 최신 회귀 학습 잔차 경계: 5% ${cutoffFormat(stateBoundaries.extremeFearUpper)}, 20% ${cutoffFormat(stateBoundaries.fearUpper)}, 80% ${cutoffFormat(stateBoundaries.greedLower)}, 95% ${cutoffFormat(stateBoundaries.extremeGreedLower)} (n=${stateBoundaries.count})`
    : " · 정확한 최신-fit 상태 경계가 공개되지 않아 영역을 표시하지 않음 (브라우저 재추정 없음)";
  $("#scatter-note").textContent = `학습 n=${points.filter((row) => row.role !== "current").length} · 현재 n=${points.filter((row) => row.role === "current").length} · β=${fmt.score(regression.beta, 4)} · R²=${fmt.score(modelPayload()?.rollingR2, 3)}${zoneNote}`;
  attachScatterNavigation(container, pointGeometry, (row) => `${row.date}, KOSPI ${fmt.signedPct(row.return1d)}, ${inputLabel} ${store.model === "raw" ? `${fmt.score(row.y, 3)}조원` : fmt.pct(row.y, 3)}, 당시 롤링 상태 ${labels[row.state] || row.state || "미확인"}${row.role === "current" ? ", 현재 관측" : ", 학습 관측"}`, { width: w, height: h });
  const tableRows = recentRows(points).map((row) => [row.date, fmt.signedPct(row.return1d), store.model === "raw" ? `${fmt.score(row.y, 3)}조원` : fmt.pct(row.y, 3), labels[row.state] || row.state || "—", row.role === "current" ? "현재" : "학습"]);
  $("#scatter-data-table").innerHTML = dataTable(["날짜", "KOSPI 1일", inputLabel, "당시 롤링 상태", "역할"], tableRows, `${modelName()} 최근 ${tableRows.length}개 산점도 관측`);
}

function renderResidual() {
  const primary = primaryModelKind();
  $("#residual-model-scope").textContent = `${modelRole(primary)} · ${compactModelName(primary)}`;
  $("#residual-model-scope").className = `scope-badge ${primary === "robust" ? "practical" : "baseline"}`;
  const rows = (store.history?.series || []).slice(-756);
  const container = $("#residual-chart");
  if (rows.length < 8) return showEmpty(container, "잔차 시계열이 부족합니다.");
  const w = 600, h = 340, p = { l: 50, r: 18, t: 20, b: 40 };
  const x = scale(0, rows.length - 1, p.l, w - p.r), y = scale(0, 100, h - p.b, p.t);
  const boundaries = [5, 20, 50, 80, 95].map((value) => `<line class="grid-line ${value === 50 ? "midline" : ""}" x1="${p.l}" y1="${y(value)}" x2="${w - p.r}" y2="${y(value)}"/><text class="axis-label" x="${p.l - 7}" y="${y(value) + 3}" text-anchor="end">${value}</text>`).join("");
  const cleanRows = rows.map((row) => row.quality === "unavailable" || row.quality === "missing" ? { ...row, percentile: null } : row);
  container.innerHTML = `<svg viewBox="0 0 ${w} ${h}" aria-hidden="true"><rect class="residual-band-fear" x="${p.l}" y="${y(20)}" width="${w - p.l - p.r}" height="${y(0) - y(20)}"/><rect class="residual-band-greed" x="${p.l}" y="${y(100)}" width="${w - p.l - p.r}" height="${y(80) - y(100)}"/>${boundaries}<line class="axis-line" x1="${p.l}" y1="${p.t}" x2="${p.l}" y2="${h - p.b}"/><line class="axis-line" x1="${p.l}" y1="${h - p.b}" x2="${w - p.r}" y2="${h - p.b}"/><g class="line-primary">${pathSegments(cleanRows, "percentile", x, y)}</g><text class="axis-title" x="${p.l}" y="13">백분위 (0–100)</text><text class="axis-label" x="${p.l}" y="${h - 10}">${esc(rows[0].date)}</text><text class="axis-label" text-anchor="end" x="${w - p.r}" y="${h - 10}">${esc(rows.at(-1).date)}</text></svg>`;
  const validRows = rows.filter((row) => Number.isFinite(Number(row.percentile)));
  const latest = validRows.at(-1);
  container.setAttribute("aria-label", `${compactModelName(primary)} 잔차 백분위 ${rows[0].date}부터 ${rows.at(-1).date}. 최신 ${latest ? fmt.score(latest.percentile) : "산출 불가"}.`);
  attachChartNavigation(container, validRows, (row) => `${row.date}, ${compactModelName(primary)} 잔차 백분위 ${fmt.score(row.percentile)}, ${labels[row.state] || row.state}, 품질 ${row.quality}`);
  const tableRows = recentRows(rows).map((row) => [row.date, fmt.score(row.percentile), labels[row.state] || row.state, row.quality || "—"]);
  $("#residual-data-table").innerHTML = dataTable(["날짜", "백분위", "상태", "품질"], tableRows, `최근 ${tableRows.length}개 ${compactModelName(primary)} 잔차 백분위`);
}

function selectedEventSection() {
  const byModel = store.dashboard.eventsByModel?.[store.model];
  return (byModel || store.dashboard.events)?.[store.eventAsset]?.[store.eventSample];
}

function eventModelKind() {
  return store.dashboard.eventsByModel?.[store.model] ? store.model : primaryModelKind();
}

function eventBenchmark(row) {
  return row.benchmarkMean ?? row.matchedBenchmarkMean ?? row.unconditionalMean ?? null;
}

function eventExcess(row) {
  return row.meanExcessReturn ?? row.excessMean ?? row.excessReturn ?? null;
}

function renderEventVisual(section) {
  const container = $("#event-ci-chart");
  const rows = section?.summary || [];
  if (!rows.length) {
    $("#event-benchmark-note").textContent = "선택 표본이 없어 불확실성 차트를 표시하지 않습니다.";
    return showEmpty(container, "사건 신뢰구간 없음");
  }
  const values = rows.flatMap((row) => [row.mean, ...(row.meanCi95 || []), eventBenchmark(row), eventExcess(row), ...(row.meanExcessReturnCi95 || row.excessCi95 || row.excessMeanCi95 || [])]).map(Number).filter(Number.isFinite);
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
    const [low, high] = (row.meanCi95 || []).map(Number);
    const benchmarkValue = eventBenchmark(row);
    const excessValue = eventExcess(row);
    const benchmark = benchmarkValue == null ? Number.NaN : Number(benchmarkValue);
    const excess = excessValue == null ? Number.NaN : Number(excessValue);
    const excessCi = (row.meanExcessReturnCi95 || row.excessCi95 || row.excessMeanCi95 || []).map(Number);
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
  const conditionalCiNote = benchmarkTreatments.has("fixed_external_mean")
    ? " 초과수익 95% CI는 무조건부 벤치마크 평균을 고정한 조건부 구간이며, 벤치마크 평균의 추정오차는 포함하지 않습니다."
    : benchmarkTreatments.has("paired_event_returns")
      ? " 초과수익 95% CI는 사건별 벤치마크 수익률을 함께 재표집한 구간입니다."
      : "";
  $("#event-benchmark-note").textContent = hasBenchmark ? `비교 벤치마크와의 차이를 연결선과 Δ로 표시합니다.${hasExcess ? " 초과수익은 서버가 발행한 값입니다." : " 초과수익 수치는 아직 별도 발행되지 않았습니다."}${conditionalCiNote}` : "비교 벤치마크·초과수익이 공개 계약에 없어 0% 기준선만 표시합니다. 평균수익률 자체를 시장 대비 초과성과로 해석하지 마세요.";
}

function renderEvents() {
  const section = selectedEventSection();
  const body = $("#event-table tbody");
  resetTableSort($("#event-table"));
  const sampleLabel = store.eventSample === "all" ? "전체 사건" : "20일 비중첩";
  $("#event-table caption").textContent = `${store.eventAsset} ${sampleLabel} 극단 상태 이후 선행수익률`;
  const modelKind = eventModelKind();
  const bootstrap = section?.summary?.[0]?.bootstrapMethod;
  const blockLength = section?.summary?.[0]?.bootstrapBlockLength;
  const bootstrapLabel = bootstrap === "moving_block" ? `이동블록 bootstrap 10,000회${blockLength ? ` · 블록 ${blockLength}` : ""}` : "bootstrap 10,000회";
  $("#event-model-scope").textContent = `${modelRole(modelKind)} · ${compactModelName(modelKind)}`;
  $("#event-model-scope").className = `scope-badge ${modelKind === "raw" ? "replica" : modelKind === "robust" ? "practical" : "baseline"}`;
  $("#event-source-line").textContent = `${store.eventAsset} · ${sampleLabel} · ${compactModelName(modelKind)} · ${bootstrapLabel}`;
  $("#event-visual-subtitle").textContent = `${store.eventAsset} · ${sampleLabel} · 0% 기준선 · 공급된 경우 벤치마크와 초과수익 포함`;
  if (!section?.summary?.length) {
    body.innerHTML = `<tr><td colspan="7">선택한 사전 계산 사건 표본이 없습니다.</td></tr>`;
    $("#event-note").textContent = "사건 수가 없을 때 성과를 강조하지 않습니다.";
    renderEventVisual(section);
    return;
  }
  body.innerHTML = section.summary.map((row) => `<tr class="${row.smallSample ? "small-sample" : ""}"><td><span class="state-mark ${row.state.includes("greed") ? "greed" : ""}">${esc(labels[row.state] || row.state)}</span></td><td>${esc(row.horizon)}일</td><td>${esc(row.eventCount)}${row.smallSample ? "*" : ""}</td><td>${esc(fmt.pct(row.mean))}</td><td>${esc(fmt.pct(row.median))}</td><td>${esc(fmt.pct(row.positiveRate, 1))}</td><td>${esc(fmt.pct(row.meanCi95?.[0]))} ~ ${esc(fmt.pct(row.meanCi95?.[1]))}</td></tr>`).join("");
  $("#event-note").textContent = `${sampleLabel} ${section.eventCount}개. * 20개 미만 표본은 소표본으로 흐리게 표시하며 통계적 확정으로 해석하지 않습니다.`;
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
  return ({ compare: "나란히 비교", long_cash: "롱 / 현금", long_short_cash: "롱 / 숏 / 현금" })[policy] || policy;
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
let latestScenarioError = null;

function scenarioResultFor(policyId, { proxy, period, variant, cost }) {
  if (policyId === "long_short_cash" && variant === "disparity") return null;
  const key = [store.summary?.dataAsOf, policyId, proxy, period, variant, cost, store.longExitPercentile].join("|");
  if (scenarioCache.has(key)) return scenarioCache.get(key);
  try {
    const result = runStrategyScenario({
      historyRows: store.history?.series,
      proxy,
      period,
      variant,
      costBps: cost,
      policyId,
      longExitPercentile: store.longExitPercentile
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
  if (Number(store.longExitPercentile) === DEFAULT_LONG_EXIT_PERCENTILE) {
    const published = publishedLongCashResultFor({ proxy, period, variant, cost });
    if (published) return published;
  }
  return scenarioResultFor("long_cash", { proxy, period, variant, cost });
}

function longShortResultFor({ proxy = store.backtestProxy, period = store.backtestPeriod, variant = store.backtestVariant, cost = store.backtestCost } = {}) {
  if (Number(store.longExitPercentile) === DEFAULT_LONG_EXIT_PERCENTILE) {
    const published = publishedLongShortResultFor({ proxy, period, variant, cost });
    if (published) return published;
  }
  return scenarioResultFor("long_short_cash", { proxy, period, variant, cost });
}

function resultFor({ proxy = store.backtestProxy, period = store.backtestPeriod, variant = store.backtestVariant, cost = store.backtestCost, policy = store.backtestPolicy } = {}) {
  if (policy === "compare" && variant === "disparity") return null;
  return policy === "long_short_cash"
    ? longShortResultFor({ proxy, period, variant, cost })
    : longCashResultFor({ proxy, period, variant, cost });
}

function resultsForPolicySelection(options = {}) {
  const longCash = longCashResultFor(options);
  const longShort = longShortResultFor(options);
  if (store.backtestPolicy === "long_cash") return [{ policy: "long_cash", result: longCash }];
  if (store.backtestPolicy === "long_short_cash") return [{ policy: "long_short_cash", result: longShort }];
  return [{ policy: "long_cash", result: longCash }, { policy: "long_short_cash", result: longShort }];
}

function hasAnyResult({ proxy = store.backtestProxy, period = store.backtestPeriod, variant = store.backtestVariant, cost = store.backtestCost, policy = store.backtestPolicy } = {}) {
  if (resultFor({ proxy, period, variant, cost, policy })) return true;
  return ["scaled_huber", "scaled_ols", "raw_ols", "disparity"].some((v) => [0, 5, 10, 20].some((c) => resultFor({ proxy, period, variant: v, cost: c, policy })));
}

function ensureBacktestSelection() {
  if (resultFor()) return;
  if (store.backtestPolicy !== "long_cash" && store.backtestVariant === "disparity") return;
  const candidates = [
    { variant: store.backtestVariant, cost: 10 },
    { variant: "scaled_huber", cost: 10 },
    { variant: "scaled_ols", cost: 10 },
    { variant: "raw_ols", cost: 10 },
    { variant: "scaled_ols", cost: 5 },
    { variant: "scaled_ols", cost: 0 },
    { variant: "scaled_ols", cost: 20 }
  ];
  const fallback = candidates.find((candidate) => resultFor(candidate));
  if (fallback) Object.assign(store, { backtestVariant: fallback.variant, backtestCost: fallback.cost });
}

function renderProxyComparison() {
  const card = $("#proxy-comparison-card");
  if (store.backtestPolicy === "compare") {
    card.hidden = true;
    $("#proxy-comparison").innerHTML = "";
    return;
  }
  const results = ["226490", "069500"].map((proxy) => ({ proxy, result: resultFor({ proxy, period: "common" }) })).filter(({ result }) => result?.metrics);
  if (results.length !== 2) {
    card.hidden = true;
    $("#proxy-comparison").innerHTML = "";
    return;
  }
  card.hidden = false;
  const key = variantKey();
  $("#proxy-comparison-subtitle").textContent = `${variantLabel(key)} · ETF 공통기간 · 같은 신호·비용`;
  $("#proxy-comparison").innerHTML = results.map(({ proxy, result }) => {
    const m = result.metrics;
    return `<section class="proxy-panel" aria-label="${esc(proxy)} 공통기간 결과">
      <div><span>${esc(proxy)}</span><strong>${esc(fmt.signedPct(m.totalReturn))}</strong><small>전략 총수익률</small></div>
      <dl>
        <div><dt>CAGR</dt><dd>${esc(fmt.pct(m.cagr))}</dd></div>
        <div><dt>Sharpe</dt><dd>${esc(fmt.score(m.sharpe, 2))}</dd></div>
        <div><dt>최대낙폭</dt><dd>${esc(fmt.pct(m.maxDrawdown))}</dd></div>
        <div><dt>노출도</dt><dd>${esc(fmt.pct(m.exposure, 1))}</dd></div>
        <div><dt>동일 타이밍 0bp</dt><dd>${esc(fmt.signedPct(m.zeroCostTimingReturn ?? m.exposureMatchedReturn))}</dd></div>
        <div><dt>위험 일치 BH</dt><dd>${esc(fmt.signedPct(m.riskMatchedBuyHoldReturn))}</dd></div>
        <div><dt>거래 수</dt><dd>${esc(m.tradeCount)}</dd></div>
        <div><dt>매수·보유</dt><dd>${esc(fmt.signedPct(m.buyAndHoldReturn))}</dd></div>
      </dl>
    </section>`;
  }).join("");
}

function resultSourceLabel(result) {
  return result?.calculationSource === "server_verified_default" ? "서버 검증 기본 80" : "브라우저 사용자 시나리오";
}

function renderPolicyComparison(longCash, longShort) {
  const card = $("#policy-comparison-card");
  const container = $("#policy-comparison");
  if (store.backtestPolicy !== "compare" || !longCash?.metrics || !longShort?.metrics) {
    card.hidden = true;
    container.innerHTML = "";
    $("#strategy-exposure").innerHTML = "";
    return;
  }
  card.hidden = false;
  $("#policy-comparison-subtitle").textContent = `${store.backtestProxy} · ${variantLabel(variantKey())} · ${store.backtestPeriod === "common" ? "ETF 공통 기간" : "전체 가능 기간"} · 롱 ≥${store.longExitPercentile} / 숏 ≤${100 - store.longExitPercentile}`;
  container.innerHTML = [["long_cash", longCash], ["long_short_cash", longShort]].map(([policy, result]) => {
    const m = result.metrics;
    return `<section class="proxy-panel policy-panel" aria-label="${esc(policyLabel(policy))} 결과">
      <div><span>${esc(policyLabel(policy))}</span><strong>${esc(fmt.signedPct(m.totalReturn))}</strong><small>${esc(resultSourceLabel(result))}</small></div>
      <dl>
        <div><dt>CAGR</dt><dd>${esc(fmt.pct(m.cagr))}</dd></div><div><dt>Sharpe</dt><dd>${esc(fmt.score(m.sharpe, 2))}</dd></div>
        <div><dt>최대낙폭</dt><dd>${esc(fmt.pct(m.maxDrawdown))}</dd></div><div><dt>현재 포지션</dt><dd>${esc(labels[result.position] || result.position)}</dd></div>
        <div><dt>롱 / 숏 / 현금</dt><dd>${esc(`${fmt.pct(m.longExposure, 1)} / ${fmt.pct(m.shortExposure, 1)} / ${fmt.pct(m.cashExposure, 1)}`)}</dd></div>
        <div><dt>거래 수</dt><dd>${esc(`${m.tradeCount}건`)}</dd></div>
      </dl></section>`;
  }).join("");
  $("#strategy-exposure").innerHTML = [["long_cash", longCash.metrics], ["long_short_cash", longShort.metrics]].map(([policy, m]) => `<section class="exposure-policy"><div class="exposure-heading"><strong>${esc(policyLabel(policy))}</strong><span>총 ${esc(fmt.pct(m.grossExposure, 1))} · 순 ${esc(fmt.pct(m.netExposure, 1))}</span></div><div class="exposure-bar" aria-hidden="true"><i class="long" style="width:${Math.max(0, Number(m.longExposure) * 100)}%"></i><i class="short" style="width:${Math.max(0, Number(m.shortExposure) * 100)}%"></i><i class="cash" style="width:${Math.max(0, Number(m.cashExposure) * 100)}%"></i></div><dl><div><dt>롱</dt><dd>${esc(fmt.pct(m.longExposure, 1))}</dd></div><div><dt>숏</dt><dd>${esc(fmt.pct(m.shortExposure, 1))}</dd></div><div><dt>현금</dt><dd>${esc(fmt.pct(m.cashExposure, 1))}</dd></div></dl></section>`).join("");
  const sensitivity = store.strategyComparison?.exitThresholdSensitivity?.proxies?.[store.backtestProxy]?.[store.backtestPeriod === "common" ? "commonPeriod" : "fullPeriod"];
  $("#exit-sensitivity").innerHTML = sensitivity?.exit50?.metrics && sensitivity?.exit80?.metrics ? `<strong>고정 10bp 민감도</strong><span>기존 50 청산 ${esc(fmt.signedPct(sensitivity.exit50.metrics.totalReturn))}</span><span>새 기본 80 청산 ${esc(fmt.signedPct(sensitivity.exit80.metrics.totalReturn))}</span><small>임계값 최적화가 아닌 변경 영향 진단입니다.</small>` : "";
}

function renderBacktests() {
  if (!store.dashboard || !store.history || !store.strategyComparison) return;
  const backtests = store.dashboard.backtests;
  const body = $("#backtest-table tbody");
  resetTableSort($("#backtest-table"));
  resetTableSort($("#trade-table"));
  if ((backtests?.status && backtests.status !== "ok") || !Object.keys(backtests?.proxies || {}).length) {
    body.innerHTML = `<tr><td colspan="20">가격 교차검증을 통과한 백테스트가 없습니다.</td></tr>`;
    $("#backtest-cards").innerHTML = `<p class="chart-note">KRX와 조정가격의 최근 공통 종가가 허용오차 0.5% 이내여야 공개됩니다.</p>`;
    showEmpty($("#equity-chart"), "백테스트 공개 보류");
    $("#trade-table tbody").innerHTML = `<tr><td colspan="7">거래 없음</td></tr>`;
    $("#policy-comparison-card").hidden = true;
    $("#proxy-comparison-card").hidden = true;
    return;
  }
  ensureBacktestSelection();
  let longCash = longCashResultFor();
  const longShort = longShortResultFor();
  if (store.backtestPolicy === "compare" && longShort?.calculationSource === "browser_user_scenario" && longCash?.calculationSource === "server_verified_default") {
    longCash = scenarioResultFor("long_cash", { proxy: store.backtestProxy, period: store.backtestPeriod, variant: store.backtestVariant, cost: store.backtestCost });
  }
  const unsupportedPolicyVariant = store.backtestPolicy !== "long_cash" && store.backtestVariant === "disparity";
  const selected = (unsupportedPolicyVariant ? [] : store.backtestPolicy === "long_cash" ? [{ policy: "long_cash", result: longCash }] : store.backtestPolicy === "long_short_cash" ? [{ policy: "long_short_cash", result: longShort }] : [{ policy: "long_cash", result: longCash }, { policy: "long_short_cash", result: longShort }]).filter(({ result }) => result?.metrics);
  const result = store.backtestPolicy === "long_short_cash" ? longShort : longCash;
  updateBacktestControls();
  const periodLabel = store.backtestPeriod === "common" ? "ETF 공통 기간" : "전체 가능 기간";
  const key = variantKey();
  const selectionLabel = `${store.backtestProxy} · ${variantLabel(key)} · ${periodLabel} · ${policyLabel()}`;
  $("#equity-legend-long-cash").hidden = store.backtestPolicy === "long_short_cash";
  $("#equity-legend-long-short").hidden = store.backtestPolicy === "long_cash";
  const strategyRole = store.backtestVariant === "scaled_huber" ? ["실전 신호", "practical"] : store.backtestVariant === "raw_ols" ? ["PDF 원문 근사", "replica"] : store.backtestVariant === "scaled_ols" ? ["OLS 기준선", "baseline"] : ["강건성 변형", "fixed"];
  $("#strategy-model-scope").textContent = `${strategyRole[0]} · ${policyLabel()}`;
  $("#strategy-model-scope").className = `scope-badge ${strategyRole[1]}`;
  $("#backtest-card-subtitle").textContent = selectionLabel;
  $("#equity-title").textContent = `${store.backtestProxy} 누적가치와 낙폭`;
  $("#equity-subtitle").textContent = `${variantLabel(key)} · ${periodLabel} · 롱 ≥${store.longExitPercentile}${store.backtestPolicy === "long_cash" ? "" : ` · 숏 ≤${100 - store.longExitPercentile}`}`;
  $("#backtest-table caption").textContent = `${selectionLabel} 성과·위험 상세`;
  $("#trade-table caption").textContent = `${selectionLabel} 최근 거래내역`;
  if (!selected.length) {
    body.innerHTML = `<tr><td colspan="20">선택 조합의 결과가 없습니다.</td></tr>`;
    $("#backtest-cards").innerHTML = `<p class="chart-note">다른 기간·비용·진입 규칙을 선택해 주세요.</p>`;
    showEmpty($("#equity-chart"), "선택 결과 없음");
    $("#trade-table tbody").innerHTML = `<tr><td colspan="7">거래 없음</td></tr>`;
    renderPolicyComparison(longCash, longShort);
    renderConclusion();
    return;
  }
  const primaryResult = result?.metrics ? result : selected[0].result;
  const m = primaryResult.metrics;
  const strategyModel = ({ scaled_huber: "robust", scaled_ols: "scaled", raw_ols: "raw" })[store.backtestVariant];
  const breakEven = Number(store.longExitPercentile) === DEFAULT_LONG_EXIT_PERCENTILE && strategyModel ? (store.backtestPolicy === "long_short_cash" ? store.strategyComparison?.proxies?.[store.backtestProxy]?.costBreakEvenBps?.[strategyModel] : backtests.proxies?.[store.backtestProxy]?.costBreakEvenBps?.[strategyModel]) : null;
  body.innerHTML = selected.map(({ policy, result: policyResult }) => {
    const metrics = policyResult.metrics;
    return `<tr><td>${esc(`${policyLabel(policy)} · ${store.backtestProxy} · ${variantLabel(key)}`)}</td><td>${esc(`${metrics.start}~${metrics.end}`)}</td><td>${esc(fmt.pct(metrics.totalReturn))}</td><td>${esc(fmt.pct(metrics.cagr))}</td><td>${esc(fmt.pct(metrics.volatility))}</td><td>${esc(fmt.score(metrics.sharpe, 2))}</td><td>${esc(fmt.pct(metrics.maxDrawdown))}</td><td>${esc(fmt.pct(metrics.winRate, 1))}</td><td>${esc(fmt.pct(metrics.longExposure, 1))}</td><td>${esc(fmt.pct(metrics.shortExposure, 1))}</td><td>${esc(fmt.pct(metrics.cashExposure, 1))}</td><td>${esc(fmt.pct(metrics.grossExposure, 1))}</td><td>${esc(fmt.pct(metrics.netExposure, 1))}</td><td>${esc(fmt.pct(metrics.zeroCostTimingReturn ?? metrics.exposureMatchedReturn))}</td><td>${esc(fmt.pct(metrics.riskMatchedBuyHoldReturn))}</td><td>${esc(fmt.score(metrics.turnover, 2))}×</td><td>${esc(metrics.tradeCount)}</td><td>${esc(fmt.score(metrics.averageHoldingSessions, 1))}일</td><td>${esc(fmt.pct(metrics.buyAndHoldReturn))}</td><td>${esc(fmt.pct(metrics.buyAndHoldMaxDrawdown))}</td></tr>`;
  }).join("");
  $("#backtest-cards").innerHTML = store.backtestPolicy === "compare" && longCash?.metrics && longShort?.metrics
    ? [
      metric("롱 / 현금 총수익률", fmt.pct(longCash.metrics.totalReturn), `CAGR ${fmt.pct(longCash.metrics.cagr)} · Sharpe ${fmt.score(longCash.metrics.sharpe, 2)}`),
      metric("롱 / 숏 / 현금 총수익률", fmt.pct(longShort.metrics.totalReturn), `CAGR ${fmt.pct(longShort.metrics.cagr)} · Sharpe ${fmt.score(longShort.metrics.sharpe, 2)}`),
      metric("롱 / 현금 최대낙폭", fmt.pct(longCash.metrics.maxDrawdown), `총노출 ${fmt.pct(longCash.metrics.grossExposure, 1)}`),
      metric("롱 / 숏 최대낙폭", fmt.pct(longShort.metrics.maxDrawdown), `총노출 ${fmt.pct(longShort.metrics.grossExposure, 1)}`),
      metric("매수·보유 수익률", fmt.pct(longCash.metrics.buyAndHoldReturn), `MDD ${fmt.pct(longCash.metrics.buyAndHoldMaxDrawdown)}`),
      metric("정책별 거래 수", `${longCash.metrics.tradeCount} / ${longShort.metrics.tradeCount}`, "롱/현금 / 롱/숏/현금")
    ].join("")
    : [
      metric("전략 총수익률", fmt.pct(m.totalReturn), `CAGR ${fmt.pct(m.cagr)}`),
      metric("전략 최대낙폭", fmt.pct(m.maxDrawdown), `변동성 ${fmt.pct(m.volatility)}`),
      metric("매수·보유 수익률", fmt.pct(m.buyAndHoldReturn), `MDD ${fmt.pct(m.buyAndHoldMaxDrawdown)}`),
      metric("동일 타이밍 무비용", fmt.pct(m.zeroCostTimingReturn ?? m.exposureMatchedReturn), "같은 진입·청산 · 비용 0bp"),
      metric("위험 일치 매수·보유", fmt.pct(m.riskMatchedBuyHoldReturn), `시장 비중 ${fmt.pct(m.riskMatchedScale, 1)}`),
      metric("거래 승률", fmt.pct(m.winRate, 1), `${m.tradeCount}건 · 평균 ${fmt.score(m.averageHoldingSessions, 1)}일`),
      metric("롱 / 숏 / 현금", `${fmt.pct(m.longExposure, 1)} / ${fmt.pct(m.shortExposure, 1)} / ${fmt.pct(m.cashExposure, 1)}`, `총 ${fmt.pct(m.grossExposure, 1)} · 순 ${fmt.pct(m.netExposure, 1)}`),
      metric("Sharpe", fmt.score(m.sharpe, 2), "현금수익률 0%"),
      metric("비용 손익분기", breakEven == null ? "—" : `${fmt.score(breakEven, 2)}bp`, "편도 비용 선형 보간")
    ].join("");
  renderEquity(primaryResult, selectionLabel, store.backtestPolicy === "compare" ? longShort : null, store.backtestPolicy === "long_short_cash" ? "long_short_cash" : "long_cash");
  const trades = selected.flatMap(({ policy, result: policyResult }) => (policyResult.trades || []).map((trade) => ({ ...trade, policy }))).sort((a, b) => String(b.exit_date).localeCompare(String(a.exit_date)));
  $("#trade-card-subtitle").textContent = "정책·방향을 분리해 표시 · 보유 중 같은 극단의 반복 신호는 무시 · 화면 최신 12건";
  const tradeRows = trades.length
    ? trades.slice(0, 12).map((trade) => `<tr><td>${esc(policyLabel(trade.policy))}</td><td>${esc(labels[trade.side] || trade.side)}</td><td>${esc(trade.entry_date)}</td><td>${esc(trade.exit_date)}</td><td>${esc(trade.holding_sessions)}</td><td>${esc(labels[trade.reason] || trade.reason)}</td><td>${esc(fmt.pct(trade.net_return))}</td></tr>`).join("")
    : selected.some(({ result: policyResult }) => policyResult.tradeHistoryTruncated && Number(policyResult.metrics?.tradeCount) > 0)
      ? `<tr><td colspan="7">선택 조합의 거래 상세 행은 경량 공개 계약에서 생략되었습니다.</td></tr>`
      : `<tr><td colspan="7">완결된 거래 없음</td></tr>`;
  $("#trade-table tbody").innerHTML = tradeRows;
  applyTableFilter($("#trade-filter"));
  renderPolicyComparison(longCash, longShort);
  renderProxyComparison();
  renderConclusion();
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
  const values = rows.flatMap((row) => [Number(row.value), Number(row.longShortValue), Number(row.buyHoldValue)]).filter(Number.isFinite);
  const min0 = Math.min(...values), max0 = Math.max(...values), span = max0 - min0 || .1;
  const min = min0 - span * .06, max = max0 + span * .08;
  const w = 680, h = 280, p = { l: 66, r: 22, t: 24, b: 40 };
  const x = scale(0, rows.length - 1, p.l, w - p.r), y = scale(min, max, h - p.b, p.t);
  const yTicks = linearTicks(min, max).map((value) => `<line class="grid-line" x1="${p.l}" y1="${y(value)}" x2="${w - p.r}" y2="${y(value)}"/><text class="axis-label" x="${p.l - 8}" y="${y(value) + 3}" text-anchor="end">${esc(fmt.score(value, 2))}</text>`).join("");
  const primaryLineClass = primaryPolicy === "long_short_cash" ? "line-longshort" : "line-strategy";
  const primaryLabel = policyLabel(primaryPolicy);
  const valueSvg = `<svg viewBox="0 0 ${w} ${h}" aria-hidden="true">${yTicks}<line class="axis-line" x1="${p.l}" y1="${p.t}" x2="${p.l}" y2="${h - p.b}"/><line class="axis-line" x1="${p.l}" y1="${h - p.b}" x2="${w - p.r}" y2="${h - p.b}"/><g class="${primaryLineClass}">${pathSegments(rows, "value", x, y)}</g>${comparable ? `<g class="line-longshort">${pathSegments(rows, "longShortValue", x, y)}</g>` : ""}<g class="line-buyhold">${pathSegments(rows, "buyHoldValue", x, y)}</g><text class="axis-title" x="${p.l}" y="14">누적가치 (초기=1)</text><text class="axis-label" x="${p.l}" y="${h - 10}">${esc(rows[0].date)}</text><text class="axis-label" x="${w - p.r}" y="${h - 10}" text-anchor="end">${esc(rows.at(-1).date)}</text></svg>`;
  const drawdowns = rows.flatMap((row) => [Number(row.drawdown), Number(row.longShortDrawdown), Number(row.buyHoldDrawdown)]).filter(Number.isFinite);
  const ddMin = Math.min(...drawdowns, -.01), ddMax = 0;
  const ddY = scale(ddMin, ddMax, h - p.b, p.t);
  const ddTicks = linearTicks(ddMin, ddMax).map((value) => `<line class="grid-line" x1="${p.l}" y1="${ddY(value)}" x2="${w - p.r}" y2="${ddY(value)}"/><text class="axis-label" x="${p.l - 8}" y="${ddY(value) + 3}" text-anchor="end">${esc(fmt.pct(value, 0))}</text>`).join("");
  const ddSvg = `<svg viewBox="0 0 ${w} ${h}" aria-hidden="true">${ddTicks}<line class="axis-line" x1="${p.l}" y1="${p.t}" x2="${p.l}" y2="${h - p.b}"/><line class="axis-line" x1="${p.l}" y1="${h - p.b}" x2="${w - p.r}" y2="${h - p.b}"/><g class="${primaryLineClass}">${pathSegments(rows, "drawdown", x, ddY)}</g>${comparable ? `<g class="line-longshort">${pathSegments(rows, "longShortDrawdown", x, ddY)}</g>` : ""}<g class="line-buyhold">${pathSegments(rows, "buyHoldDrawdown", x, ddY)}</g><text class="axis-title" x="${p.l}" y="14">고점 대비 낙폭 (%)</text><text class="axis-label" x="${p.l}" y="${h - 10}">${esc(rows[0].date)}</text><text class="axis-label" x="${w - p.r}" y="${h - 10}" text-anchor="end">${esc(rows.at(-1).date)}</text></svg>`;
  container.innerHTML = valueSvg + ddSvg;
  const last = rows.at(-1);
  container.setAttribute("aria-label", `${selectionLabel}. ${rows[0].date}부터 ${last.date}. 최종 ${primaryLabel} ${fmt.score(last.value, 3)}${comparable ? `, 롱 숏 현금 ${fmt.score(last.longShortValue, 3)}` : ""}, 매수·보유 ${fmt.score(last.buyHoldValue, 3)}.`);
  attachChartNavigation(container, rows, (row) => `${row.date}, ${primaryLabel} ${fmt.score(row.value, 3)}${comparable ? `, 롱 숏 현금 ${fmt.score(row.longShortValue, 3)}` : ""}, 매수·보유 ${fmt.score(row.buyHoldValue, 3)}, ${primaryLabel} 낙폭 ${fmt.pct(row.drawdown)}${comparable ? `, 롱 숏 현금 낙폭 ${fmt.pct(row.longShortDrawdown)}` : ""}, 매수·보유 낙폭 ${fmt.pct(row.buyHoldDrawdown)}`);
  $("#equity-data-table").innerHTML = dataTable(["시점", "날짜", primaryLabel, "비교 롱/숏/현금", "매수·보유", `${primaryLabel} 낙폭`, "비교 롱/숏 낙폭", "BH 낙폭"], [["시작", rows[0].date, fmt.score(rows[0].value, 3), comparable ? fmt.score(rows[0].longShortValue, 3) : "—", fmt.score(rows[0].buyHoldValue, 3), fmt.pct(rows[0].drawdown), comparable ? fmt.pct(rows[0].longShortDrawdown) : "—", fmt.pct(rows[0].buyHoldDrawdown)], ["최신", last.date, fmt.score(last.value, 3), comparable ? fmt.score(last.longShortValue, 3) : "—", fmt.score(last.buyHoldValue, 3), fmt.pct(last.drawdown), comparable ? fmt.pct(last.longShortDrawdown) : "—", fmt.pct(last.buyHoldDrawdown)]], `${selectionLabel} 시작·최신 값`);
}

function renderConclusion() {
  if (!store.dashboard) return;
  const section = selectedEventSection();
  const result = resultFor();
  const conclusionLongCash = store.backtestPolicy === "compare" ? longCashResultFor() : null;
  const conclusionLongShort = store.backtestPolicy === "compare" ? longShortResultFor() : null;
  const fear20 = section?.summary?.find((row) => row.state === "extreme_fear" && Number(row.horizon) === 20);
  const metrics = result?.metrics;
  const replica = pdfReplicaPayload();
  const annotated = pdfReplicaEvents(replica);
  const replicaMatch = replica?.directionMatchCount ?? replica?.matchedCount ?? replica?.summary?.directionMatchCount ?? (annotated.length ? annotated.filter((row) => row.directionMatched === true).length : null);
  const excess = fear20 ? eventExcess(fear20) : null;
  const excessCi = fear20?.meanExcessReturnCi95 || fear20?.excessCi95 || fear20?.excessMeanCi95;
  const eventConclusive = Array.isArray(excessCi) ? Number(excessCi[0]) > 0 : Number(fear20?.meanCi95?.[0]) > 0;
  const strategyPositive = store.backtestPolicy === "compare"
    ? [conclusionLongCash, conclusionLongShort].every((item) => item?.metrics && Number(item.metrics.totalReturn) > 0 && Number(item.metrics.sharpe) > 0)
    : metrics && Number(metrics.totalReturn) > 0 && Number(metrics.sharpe) > 0;
  const tone = eventConclusive && strategyPositive ? "supportive" : !fear20 || !metrics ? "mixed" : "caution";
  const verdict = "원문 날짜 재현, 전체 사건 일반화, 비용 후 전략 실용성을 서로 다른 질문으로 판정합니다.";
  const sampleLabel = store.eventSample === "all" ? "전체 사건" : "20일 비중첩";
  const key = variantKey();
  const replicaEvidence = annotated.length ? `${replicaMatch == null ? `${annotated.length}개 주석 사건 공개` : `${replicaMatch}/${annotated.length} 방향 일치`} · 절대수급 원문 근사` : "주석 사건 파생값 미발행";
  const eventEvidence = fear20 ? `20일 평균 ${fmt.signedPct(fear20.mean)} · 95% CI ${fmt.pct(fear20.meanCi95?.[0])}~${fmt.pct(fear20.meanCi95?.[1])} · n=${fear20.eventCount}${excess == null ? " · 벤치마크 초과수익 미발행" : ` · 초과 ${fmt.signedPct(excess)}`}` : "선택 표본 없음";
  const strategyEvidence = store.backtestPolicy === "compare" && conclusionLongCash?.metrics && conclusionLongShort?.metrics
    ? `나란히 비교 · 롱 청산 ${store.longExitPercentile} / 숏 청산 ${100 - store.longExitPercentile} · 롱/현금 ${fmt.signedPct(conclusionLongCash.metrics.totalReturn)} (Sharpe ${fmt.score(conclusionLongCash.metrics.sharpe, 2)}) · 롱/숏/현금 ${fmt.signedPct(conclusionLongShort.metrics.totalReturn)} (Sharpe ${fmt.score(conclusionLongShort.metrics.sharpe, 2)})`
    : metrics ? `${policyLabel(store.backtestPolicy)} · 롱 청산 ${store.longExitPercentile}${store.backtestPolicy === "long_cash" ? "" : ` / 숏 청산 ${100 - store.longExitPercentile}`} · ${resultSourceLabel(result)} · ${fmt.signedPct(metrics.totalReturn)} · Sharpe ${fmt.score(metrics.sharpe, 2)} · 총노출 ${fmt.pct(metrics.grossExposure ?? metrics.exposure, 1)} · 동일 타이밍 0bp ${fmt.signedPct(metrics.zeroCostTimingReturn ?? metrics.exposureMatchedReturn)} · 위험 일치 BH ${fmt.signedPct(metrics.riskMatchedBuyHoldReturn)}` : "선택 결과 없음";
  const facts = `<span><strong>1 · PDF 날짜 재현</strong>${esc(replicaEvidence)}</span><span><strong>2 · 사건 일반화</strong>${esc(eventEvidence)}</span><span><strong>3 · 비용 후 전략</strong>${esc(strategyEvidence)}</span>`;
  $("#research-conclusion").className = `conclusion-card ${tone}`;
  $("#research-conclusion").innerHTML = `<div class="conclusion-heading"><div><p class="eyebrow">THREE SEPARATE QUESTIONS</p><h2 id="conclusion-title">현재 공개 결과가 말하는 것</h2></div><span class="badge neutral">사건: ${esc(store.eventAsset)} ${esc(compactModelName(eventModelKind()))} · ${esc(sampleLabel)} · 전략: ${esc(store.backtestProxy)} ${esc(variantLabel(key))}</span></div><p class="conclusion-lead">${esc(verdict)}</p><div class="conclusion-facts">${facts}</div><p class="conclusion-footnote">사건 평균이 양수인 것, 시장 대비 초과수익인 것, 비용 후 거래 가능한 것은 서로 동치가 아닙니다. 낮은 노출도 전략과 100% 매수·보유의 총수익률도 직접 우열 판정에 쓰지 않습니다.</p>`;
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
  const base = entity();
  const diag = store.dashboard.diagnostics || {};
  const latest = diag.latest || {};
  $("#diagnostic-list").innerHTML = [
    ["KOSPI 50일 이격도", fmt.score(base.disparity50, 1)],
    ["KOSPI MDD252", fmt.pct(base.mdd252)],
    ["Micron KRW MDD252", fmt.pct(latest.muMdd252)],
    ["SK하이닉스 MDD252", fmt.pct(latest.hynixMdd252)],
    ["삼성전자 MDD252", fmt.pct(latest.samsungMdd252)],
    ["MU / 하이닉스 비율", fmt.score(latest.muHynixRatio, 4)],
    ["MU / 하이닉스 비율지수", fmt.score(latest.muHynixRatioIndexed, 1)],
    ["MU / 하이닉스 상대 스프레드", latest.muHynixRelativeSpread == null ? "—" : `${fmt.score(latest.muHynixRelativeSpread, 2)}p`],
    ["미국 세션 정렬", diag.status === "ok" ? "KRX일 이전 세션" : "산출 불가"]
  ].map(([key, value]) => `<dt>${esc(key)}</dt><dd>${esc(value)}</dd>`).join("");
  const rows = (diag.series || []).filter((row) => row.muHynixRelativeSpread != null || row.muHynixRatioIndexed != null);
  const container = $("#diagnostic-chart");
  if (rows.length < 2) return showEmpty(container, "상대 스프레드 산출 불가");
  const field = rows.some((row) => Number.isFinite(Number(row.muHynixRelativeSpread))) ? "muHynixRelativeSpread" : "muHynixRatioIndexed";
  const values = rows.map((row) => Number(row[field])).filter(Number.isFinite);
  const w = 600, h = 260, p = { l: 62, r: 18, t: 24, b: 38 };
  const min0 = Math.min(...values), max0 = Math.max(...values), pad = (max0 - min0 || 1) * .08;
  const min = min0 - pad, max = max0 + pad;
  const x = scale(0, rows.length - 1, p.l, w - p.r), y = scale(min, max, h - p.b, p.t);
  const formatValue = field === "muHynixRelativeSpread" ? (value) => `${fmt.score(value, 1)}p` : (value) => fmt.score(value, 0);
  const ticks = linearTicks(min, max).map((value) => `<line class="grid-line" x1="${p.l}" y1="${y(value)}" x2="${w - p.r}" y2="${y(value)}"/><text class="axis-label" x="${p.l - 7}" y="${y(value) + 3}" text-anchor="end">${esc(formatValue(value))}</text>`).join("");
  const reference = field === "muHynixRelativeSpread" && min <= 0 && max >= 0 ? `<line class="reference-line" x1="${p.l}" y1="${y(0)}" x2="${w - p.r}" y2="${y(0)}"/>` : field === "muHynixRatioIndexed" && min <= 100 && max >= 100 ? `<line class="reference-line" x1="${p.l}" y1="${y(100)}" x2="${w - p.r}" y2="${y(100)}"/>` : "";
  container.innerHTML = `<svg viewBox="0 0 ${w} ${h}" aria-hidden="true">${ticks}${reference}<line class="axis-line" x1="${p.l}" y1="${p.t}" x2="${p.l}" y2="${h - p.b}"/><line class="axis-line" x1="${p.l}" y1="${h - p.b}" x2="${w - p.r}" y2="${h - p.b}"/><g class="line-accent">${pathSegments(rows, field, x, y)}</g><text class="axis-title" x="${p.l}" y="14">${field === "muHynixRelativeSpread" ? "MU 대비 하이닉스 상대 스프레드 (지수포인트)" : "MU / 하이닉스 비율지수 (시작=100)"}</text><text class="axis-label" x="${p.l}" y="${h - 9}">${esc(rows[0].date)}</text><text class="axis-label" x="${w - p.r}" y="${h - 9}" text-anchor="end">${esc(rows.at(-1).date)}</text></svg>`;
  container.setAttribute("aria-label", `${rows[0].date}부터 ${rows.at(-1).date}까지 ${field === "muHynixRelativeSpread" ? "Micron 대비 SK하이닉스 상대 스프레드" : "Micron 대 SK하이닉스 비율지수"}. 최신 ${formatValue(rows.at(-1)[field])}.`);
  attachChartNavigation(container, rows, (row) => `${row.date}, ${field === "muHynixRelativeSpread" ? "상대 스프레드" : "비율지수"} ${formatValue(row[field])}`);
  const tableRows = recentRows(rows).map((row) => [row.date, fmt.score(row.muHynixRatio, 4), fmt.score(row.muHynixRatioIndexed, 1), row.muHynixRelativeSpread == null ? "—" : `${fmt.score(row.muHynixRelativeSpread, 2)}p`]);
  $("#diagnostic-data-table").innerHTML = dataTable(["날짜", "MU/하이닉스 비율", "비율지수", "상대 스프레드"], tableRows, `최근 ${tableRows.length}개 상대 진단`);
}

function renderFlowChannels() {
  const published = store.dashboard?.flowChannels?.channels;
  const channels = Array.isArray(published) && published.length ? published : [
    { channelId: "retail", participant: "individual", availability: "active", state: stateFromValue(modelPayload(primaryModelKind())), strategyUse: "primary", source: "pykrx" },
    { channelId: "foreign", participant: "foreign", availability: "planned", state: "unavailable", strategyUse: "future_extension" },
    { channelId: "institutional", participant: "institutional", availability: "planned", state: "unavailable", strategyUse: "future_extension" }
  ];
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
      <dl><div><dt>역할</dt><dd>${esc(useLabel[channel.strategyUse] || channel.strategyUse || "—")}</dd></div><div><dt>상태</dt><dd>${esc(stateLabel)}</dd></div><div><dt>모형 품질</dt><dd>${esc(modelQuality)}</dd></div><div><dt>백분위</dt><dd>${esc(active ? fmt.score(channel.percentile, 1) : "—")}</dd></div>${coverage}<div><dt>출처</dt><dd>${esc(channel.source || "활성화 전")}</dd></div></dl>
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

function updateBacktestControls() {
  updatePressed("[data-backtest-proxy]", store.backtestProxy, "backtestProxy");
  updatePressed("[data-backtest-policy]", store.backtestPolicy, "backtestPolicy");
  updatePressed("[data-backtest-variant]", store.backtestVariant, "backtestVariant");
  updatePressed("[data-backtest-cost]", store.backtestCost, "backtestCost");
  updatePressed("[data-backtest-period]", store.backtestPeriod, "backtestPeriod");
  $$('[data-backtest-cost]').forEach((button) => {
    const supported = Boolean(resultFor({ cost: Number(button.dataset.backtestCost) }));
    button.disabled = !supported;
    button.setAttribute("aria-disabled", String(!supported));
  });
  $$('[data-backtest-variant]').forEach((button) => {
    const variant = button.dataset.backtestVariant;
    const supported = !(store.backtestPolicy !== "long_cash" && variant === "disparity") && [0, 5, 10, 20].some((cost) => resultFor({ variant, cost }));
    button.disabled = !supported;
    button.setAttribute("aria-disabled", String(!supported));
  });
  $$('[data-backtest-period]').forEach((button) => {
    const period = button.dataset.backtestPeriod;
    const supported = hasAnyResult({ period });
    button.disabled = !supported;
    button.setAttribute("aria-disabled", String(!supported));
  });
  const available = resultFor();
  const source = Number(store.longExitPercentile) === DEFAULT_LONG_EXIT_PERCENTILE && available?.calculationSource === "server_verified_default" ? "서버 검증 기본 80 결과" : "공개 입력 기반 브라우저 사용자 시나리오";
  $("#backtest-selection-note").textContent = available ? `${source} · 롱 청산 ${store.longExitPercentile}, 숏 청산 ${100 - store.longExitPercentile}. 회귀와 신호는 다시 적합하지 않습니다.` : "선택 조합의 결과가 없습니다. 이격도 변형에는 숏 규칙이 정의되지 않았습니다.";
  $("#exit-threshold-value").textContent = `${store.longExitPercentile}`;
  $("#short-exit-threshold-value").textContent = `${100 - store.longExitPercentile}`;
  $("#exit-threshold-input").value = String(store.longExitPercentile);
}

function initializeControlState() {
  let saved = {};
  try { saved = JSON.parse(localStorage.getItem("fearngreed-controls-v3") || localStorage.getItem("fearngreed-controls-v2") || "{}"); } catch (_) { saved = {}; }
  const params = new URLSearchParams(location.search);
  Object.entries(CONTROL_QUERY).forEach(([key, param]) => {
    const candidate = params.has(param) ? params.get(param) : saved[key];
    if (["historyStart", "historyEnd"].includes(key)) {
      if (isIsoDate(candidate)) store[key] = candidate;
      return;
    }
    if (key === "longExitPercentile") {
      try { store.longExitPercentile = normalizeLongExitPercentile(candidate ?? DEFAULT_LONG_EXIT_PERCENTILE); } catch (_) { store.longExitPercentile = DEFAULT_LONG_EXIT_PERCENTILE; }
      return;
    }
    let normalized = key === "backtestVariant" && candidate === "base" ? "scaled_ols" : String(candidate ?? "");
    if (key === "window") normalized = ({ "252": "1y", "756": "3y" })[normalized] || normalized;
    if (CONTROL_ALLOWED[key]?.includes(normalized)) store[key] = key === "backtestCost" ? Number(normalized) : normalized;
  });
  if (store.window === "custom" && (!isIsoDate(store.historyStart) || !isIsoDate(store.historyEnd) || store.historyStart > store.historyEnd)) store.window = "3y";
  if (!params.has("window") && saved.window == null && matchMedia("(max-width: 520px)").matches) store.window = "1y";
}

function ensureHistoryRangeAvailable() {
  if (store.window !== "custom") return true;
  const rows = store.history?.series || [];
  const firstDate = rows[0]?.date;
  const latestDate = rows.at(-1)?.date;
  const valid = firstDate && latestDate && isIsoDate(store.historyStart) && isIsoDate(store.historyEnd) && store.historyStart <= store.historyEnd && store.historyStart >= firstDate && store.historyEnd <= latestDate && rows.some((row) => row.date >= store.historyStart && row.date <= store.historyEnd);
  if (valid) return true;
  store.window = "3y";
  store.historyStart = "";
  store.historyEnd = "";
  return false;
}

function persistControlState({ replaceUrl = true } = {}) {
  const values = Object.fromEntries(Object.keys(CONTROL_QUERY).map((key) => [key, store[key]]));
  try { localStorage.setItem("fearngreed-controls-v3", JSON.stringify(values)); } catch (_) { /* URL remains shareable */ }
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
  chart.focus({ preventScroll: true });
}

function announceViewAction(message) {
  $("#view-action-status").textContent = message;
}

async function shareCurrentView() {
  persistControlState();
  const text = location.href;
  try {
    await navigator.clipboard.writeText(text);
    announceViewAction("현재 화면 링크를 복사했습니다.");
  } catch (_) {
    const input = document.createElement("input");
    input.value = text;
    input.setAttribute("aria-hidden", "true");
    document.body.append(input);
    input.select();
    const copied = document.execCommand?.("copy");
    input.remove();
    announceViewAction(copied ? "현재 화면 링크를 복사했습니다." : "주소창의 링크를 복사해 주세요.");
  }
}

function resetControls() {
  Object.assign(store, DEFAULT_CONTROLS);
  const exitInput = $("#exit-threshold-input");
  const exitStatus = $("#exit-threshold-status");
  exitInput?.removeAttribute("aria-invalid");
  if (exitStatus) {
    delete exitStatus.dataset.state;
    exitStatus.textContent = "";
  }
  if (!modelPayload("robust")) store.model = modelPayload("scaled") ? "scaled" : "raw";
  ensureBacktestSelection();
  persistControlState();
  renderAll();
  announceViewAction("모든 화면 설정을 기본값으로 복원했습니다.");
}

function bindControls() {
  $$('[data-window]').forEach((button) => button.addEventListener("click", () => {
    store.window = button.dataset.window;
    persistControlState();
    updatePressed("[data-window]", store.window, "window");
    renderHistory();
  }));
  $$('[data-model]').forEach((button) => button.addEventListener("click", () => {
    if (button.disabled) return;
    store.model = button.dataset.model;
    persistControlState();
    updatePressed("[data-model]", store.model, "model");
    renderHeader();
    renderScatter();
    renderEvents();
    renderConclusion();
  }));
  $$('[data-event-asset]').forEach((button) => button.addEventListener("click", () => {
    store.eventAsset = button.dataset.eventAsset;
    persistControlState();
    updatePressed("[data-event-asset]", store.eventAsset, "eventAsset");
    renderEvents();
    renderConclusion();
  }));
  $$('[data-event-sample]').forEach((button) => button.addEventListener("click", () => {
    store.eventSample = button.dataset.eventSample;
    persistControlState();
    updatePressed("[data-event-sample]", store.eventSample, "eventSample");
    renderEvents();
    renderConclusion();
  }));
  $$('[data-backtest-proxy]').forEach((button) => button.addEventListener("click", () => {
    store.backtestProxy = button.dataset.backtestProxy;
    ensureBacktestSelection();
    persistControlState();
    renderBacktests();
  }));
  $$('[data-backtest-policy]').forEach((button) => button.addEventListener("click", () => {
    store.backtestPolicy = button.dataset.backtestPolicy;
    persistControlState();
    renderBacktests();
  }));
  $$('[data-backtest-variant]').forEach((button) => button.addEventListener("click", () => {
    store.backtestVariant = button.dataset.backtestVariant;
    ensureBacktestSelection();
    persistControlState();
    renderBacktests();
  }));
  $$('[data-backtest-cost]').forEach((button) => button.addEventListener("click", () => {
    store.backtestCost = Number(button.dataset.backtestCost);
    ensureBacktestSelection();
    persistControlState();
    renderBacktests();
  }));
  $$('[data-backtest-period]').forEach((button) => button.addEventListener("click", () => {
    store.backtestPeriod = button.dataset.backtestPeriod;
    ensureBacktestSelection();
    persistControlState();
    renderBacktests();
  }));
  $$('[data-chart-latest]').forEach((button) => button.addEventListener("click", () => scrollChartLatest(button.dataset.chartLatest)));
  $("#history-range-form").addEventListener("submit", applyCustomHistoryRange);
  $("#exit-threshold-form").addEventListener("submit", (event) => {
    event.preventDefault();
    const input = $("#exit-threshold-input");
    const status = $("#exit-threshold-status");
    const previous = store.longExitPercentile;
    try {
      if (!store.dashboard || !store.history || !store.strategyComparison) throw new Error("공개 데이터를 불러온 뒤 다시 적용해 주세요.");
      store.longExitPercentile = normalizeLongExitPercentile(input.value);
      scenarioCache.clear();
      latestScenarioError = null;
      const scenarioResults = resultsForPolicySelection().filter(({ result }) => result?.metrics);
      if (latestScenarioError) throw latestScenarioError;
      const expectedCount = store.backtestPolicy === "compare" ? 2 : 1;
      if (scenarioResults.length !== expectedCount) throw new Error("선택한 정책의 사용자 시나리오를 계산할 수 없습니다.");
      input.removeAttribute("aria-invalid");
      status.dataset.state = "ok";
      status.textContent = `적용됨: 롱 ≥${store.longExitPercentile}, 숏 ≤${100 - store.longExitPercentile}`;
      persistControlState();
      renderBacktests();
    } catch (error) {
      store.longExitPercentile = previous;
      scenarioCache.clear();
      input.setAttribute("aria-invalid", "true");
      status.dataset.state = "error";
      status.textContent = error.message;
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
  updatePressed("[data-model]", store.model, "model");
  updatePressed("[data-window]", store.window, "window");
  updatePressed("[data-event-asset]", store.eventAsset, "eventAsset");
  updatePressed("[data-event-sample]", store.eventSample, "eventSample");
  renderHeader();
  renderHistory();
  renderScatter();
  renderResidual();
  renderEvents();
  renderBacktests();
  renderPdfSnapshot();
  renderDiagnostics();
  renderFlowChannels();
  renderConclusion();
  enhanceTables();
  applyTableFilter($("#trade-filter"));
}

initializeTheme();
initializeControlState();
bindControls();
bindTableTools();
initializeSectionNav();

Promise.all([loadJson("data/summary.json"), loadJson("data/dashboard.json"), loadJson("data/history.json"), loadJson("data/strategy-comparison.json")])
  .then(([summary, dashboard, history, strategyComparison]) => {
    validateContracts(summary, dashboard, history, strategyComparison);
    store = { ...store, summary, dashboard, history: decodeHistory(history), strategyComparison };
    scenarioCache.clear();
    if (!modelPayload(store.model)) store.model = modelPayload("robust") ? "robust" : "scaled";
    ensureBacktestSelection();
    const rangeAvailable = ensureHistoryRangeAvailable();
    persistControlState({ replaceUrl: !rangeAvailable });
    renderAll();
    if (!rangeAvailable) announceViewAction("저장된 사용자 기간이 공개 이력 밖이어서 최근 3년으로 복원했습니다.");
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
