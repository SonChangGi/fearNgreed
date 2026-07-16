const $ = (selector) => document.querySelector(selector);
const labels = {
  extreme_fear: "극단적 공포", fear: "공포", neutral: "중립", greed: "탐욕", extreme_greed: "극단적 탐욕",
  unavailable: "산출 불가", cash: "현금", long: "롱", recovery: "백분위 50 회복", max_holding: "최대 20일"
};
const fmt = {
  pct: (value, digits = 2) => value == null ? "—" : `${(Number(value) * 100).toFixed(digits)}%`,
  score: (value, digits = 1) => value == null ? "—" : Number(value).toFixed(digits),
  compact: (value) => value == null ? "—" : Intl.NumberFormat("ko-KR", { notation: "compact", maximumFractionDigits: 2 }).format(value),
  date: (value) => value ? new Intl.DateTimeFormat("ko-KR", { year: "numeric", month: "short", day: "numeric" }).format(new Date(`${value}T00:00:00`)) : "—"
};

let store = { summary: null, dashboard: null, history: null, window: 756 };

async function loadJson(path) {
  const response = await fetch(path, { cache: "no-store" });
  if (!response.ok) throw new Error(`${path}: HTTP ${response.status}`);
  return response.json();
}

function validateContracts(summary, dashboard, history) {
  if (summary?.schemaVersion !== 1 || summary?.contract !== "quant-research-summary" || summary?.projectId !== "fearngreed") throw new Error("summary.json 계약이 올바르지 않습니다.");
  if (dashboard?.schemaVersion !== 1 || history?.schemaVersion !== 1) throw new Error("공개 데이터 스키마 버전이 올바르지 않습니다.");
  if (!Array.isArray(summary.primaryEntities) || !Array.isArray(history.series)) throw new Error("공개 데이터의 필수 배열이 없습니다.");
}

function stateFromEntity(entity) {
  if (entity.signalState) return entity.signalState;
  const value = entity.sentimentPercentile;
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

function metric(label, value, note) {
  return `<article class="metric"><span>${label}</span><strong title="${value}">${value}</strong><small>${note}</small></article>`;
}

function renderHeader(summary) {
  const entity = summary.primaryEntities[0] || {};
  const state = stateFromEntity(entity);
  const status = effectiveStatus(summary);
  const badge = $("#status-badge");
  badge.textContent = status === "stale" ? "stale · 갱신 지연" : summary.status.state;
  badge.className = `badge ${status}`;
  $("#confidence-badge").textContent = `모형 ${entity.modelConfidence || "미확인"}`;
  $("#state").textContent = labels[state] || state;
  $("#asof").textContent = `기준일 ${fmt.date(summary.dataAsOf)}`;
  const reasons = summary.status.degradedReasons || [];
  $("#status-note").textContent = reasons.length ? reasons.join(" · ") : "핵심 공급자와 계산 품질 게이트 통과";
  $("#metrics").innerHTML = [
    metric("감정 백분위", fmt.score(entity.sentimentPercentile), "직전 252일 학습 잔차 경험분포"),
    metric("잔차 z", fmt.score(entity.residualZ, 2), "median / 1.4826×MAD"),
    metric("롤링 R²", fmt.score(entity.rollingR2, 3), "현재일 제외 · 최소 기준 0.20"),
    metric("KOSPI 1일", fmt.pct(entity.return1d), "종가 대비 전 거래일"),
    metric("개인 순매수율", fmt.pct(entity.flowShare, 3), "KOSPI 거래대금 대비"),
    metric("50일 이격도", fmt.score(entity.disparity50, 1), "100 = 50일 이동평균"),
    metric("모형 포지션", labels[entity.position] || "—", `${entity.primaryProxy || "226490"} · ${entity.pendingAction || "대기 신호 없음"}`),
    metric("252일 낙폭", fmt.pct(entity.mdd252), "롤링 고점 대비")
  ].join("");
  $("#quality-strip").innerHTML = [
    `<span><strong>데이터:</strong> ${summary.dataAsOf}</span>`,
    `<span><strong>공급 경로:</strong> ${entity.sourceMode || "—"}</span>`,
    `<span><strong>모형 품질:</strong> ${entity.modelQuality || "—"}</span>`,
    `<span><strong>관측치:</strong> ${fmt.compact(summary.coverage.observationCount)}</span>`,
    `<span><strong>비중첩 사건:</strong> ${summary.coverage.eventCount}</span>`,
    `<span><strong>거래:</strong> ${summary.coverage.tradeCount}</span>`
  ].join("");
}

function scale(domainMin, domainMax, rangeMin, rangeMax) {
  const span = domainMax - domainMin || 1;
  return (value) => rangeMin + (Number(value) - domainMin) / span * (rangeMax - rangeMin);
}

function pathSegments(rows, valueField, x, y) {
  const segments = [];
  let current = [];
  rows.forEach((row, index) => {
    const value = row[valueField];
    if (value == null || !Number.isFinite(Number(value))) {
      if (current.length) segments.push(current.join(" "));
      current = [];
    } else current.push(`${current.length ? "L" : "M"} ${x(index).toFixed(2)} ${y(value).toFixed(2)}`);
  });
  if (current.length) segments.push(current.join(" "));
  return segments.map((segment) => `<path d="${segment}"/>`).join("");
}

function visibleHistory() {
  const rows = store.history.series || [];
  return store.window === "all" ? rows : rows.slice(-Number(store.window));
}

function renderHistory() {
  const rows = visibleHistory().filter((row) => row.kospiClose != null || row.kospi != null);
  const container = $("#history-chart");
  if (rows.length < 8) return showEmpty(container, "시계열 관측치가 부족합니다.");
  const values = rows.map((row) => Number(row.kospiClose ?? row.kospi));
  const w = 1040, h = 390, p = { l: 58, r: 22, t: 18, b: 34 };
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
    if (row.state === "extreme_greed") return `<rect class="${cls}" x="${x(index) - 4}" y="${y(row.kospiClose ?? row.kospi) - 4}" width="8" height="8" transform="rotate(45 ${x(index)} ${y(row.kospiClose ?? row.kospi)})"><title>${title}</title></rect>`;
    return `<circle class="${cls}" cx="${x(index)}" cy="${y(row.kospiClose ?? row.kospi)}" r="5"><title>${title}</title></circle>`;
  }).join("");
  const ticks = [0, .25, .5, .75, 1].map((ratio) => {
    const value = min - pad + (max - min + 2 * pad) * ratio;
    const yy = y(value);
    return `<line class="grid-line" x1="${p.l}" y1="${yy}" x2="${w - p.r}" y2="${yy}"/><text class="axis-label" x="${p.l - 8}" y="${yy + 3}" text-anchor="end">${Math.round(value).toLocaleString()}</text>`;
  }).join("");
  container.innerHTML = `<svg viewBox="0 0 ${w} ${h}" aria-hidden="true">${zones.join("")}${ticks}<g class="line-price">${pathSegments(rows, rows[0].kospiClose != null ? "kospiClose" : "kospi", x, y)}</g>${events}<text class="axis-label" x="${p.l}" y="${h - 8}">${rows[0].date}</text><text class="axis-label" x="${w - p.r}" y="${h - 8}" text-anchor="end">${rows.at(-1).date}</text></svg>`;
  container.setAttribute("aria-label", `${rows[0].date}부터 ${rows.at(-1).date}까지 KOSPI 종가. 최저 ${Math.round(min)}, 최고 ${Math.round(max)}.`);
}

function renderScatter() {
  const points = (store.dashboard.scatter || []).filter((row) => row.return1d != null && row.flowShare != null);
  const container = $("#scatter-chart");
  if (points.length < 8) return showEmpty(container, "산점도 관측치가 부족합니다.");
  const xs = points.map((row) => Number(row.return1d)), ys = points.map((row) => Number(row.flowShare));
  const xmin = Math.min(...xs), xmax = Math.max(...xs), ymin = Math.min(...ys), ymax = Math.max(...ys);
  const w = 560, h = 310, p = { l: 52, r: 20, t: 18, b: 38 };
  const x = scale(xmin, xmax, p.l, w - p.r), y = scale(ymin, ymax, h - p.b, p.t);
  const regression = store.dashboard.regression || {};
  const predicted = (value) => Number(regression.alpha) + Number(regression.beta) * value;
  const marks = points.map((row, index) => `<circle class="scatter-point ${index === points.length - 1 ? "current" : ""}" cx="${x(row.return1d)}" cy="${y(row.flowShare)}" r="${index === points.length - 1 ? 6 : 3}"><title>${row.date} · 수익률 ${fmt.pct(row.return1d)} · 순매수율 ${fmt.pct(row.flowShare, 3)}</title></circle>`).join("");
  const zeroX = xmin <= 0 && xmax >= 0 ? `<line class="grid-line" x1="${x(0)}" y1="${p.t}" x2="${x(0)}" y2="${h - p.b}"/>` : "";
  const zeroY = ymin <= 0 && ymax >= 0 ? `<line class="grid-line" x1="${p.l}" y1="${y(0)}" x2="${w - p.r}" y2="${y(0)}"/>` : "";
  const current = points.at(-1), residual = Number.isFinite(predicted(current.return1d)) ? `<line class="residual-arrow" x1="${x(current.return1d)}" y1="${y(predicted(current.return1d))}" x2="${x(current.return1d)}" y2="${y(current.flowShare)}"/>` : "";
  container.innerHTML = `<svg viewBox="0 0 ${w} ${h}" aria-hidden="true">${zeroX}${zeroY}<line class="regression-line" x1="${x(xmin)}" y1="${y(predicted(xmin))}" x2="${x(xmax)}" y2="${y(predicted(xmax))}"/>${residual}${marks}<text class="axis-label" x="${p.l}" y="${h - 8}">KOSPI 수익률 →</text><text class="axis-label" x="${p.l}" y="12">개인 순매수율</text></svg>`;
  container.setAttribute("aria-label", `${points.length}개 관측치 산점도. 현재 수익률 ${fmt.pct(current.return1d)}, 개인 순매수율 ${fmt.pct(current.flowShare, 3)}.`);
  $("#scatter-note").textContent = `n=${points.length} · β=${fmt.score(regression.beta, 4)} · 현재 관측은 채운 원으로 표시`;
}

function renderResidual() {
  const rows = visibleHistory().slice(-756);
  const container = $("#residual-chart");
  if (rows.length < 8) return showEmpty(container, "잔차 시계열이 부족합니다.");
  const w = 560, h = 310, p = { l: 42, r: 18, t: 18, b: 34 };
  const x = scale(0, rows.length - 1, p.l, w - p.r), y = scale(0, 100, h - p.b, p.t);
  const boundaries = [5, 20, 80, 95].map((value) => `<line class="grid-line" x1="${p.l}" y1="${y(value)}" x2="${w - p.r}" y2="${y(value)}"/><text class="axis-label" x="${p.l - 6}" y="${y(value) + 3}" text-anchor="end">${value}</text>`).join("");
  container.innerHTML = `<svg viewBox="0 0 ${w} ${h}" aria-hidden="true"><rect class="residual-band-fear" x="${p.l}" y="${y(20)}" width="${w - p.l - p.r}" height="${y(0) - y(20)}"/><rect class="residual-band-greed" x="${p.l}" y="${y(100)}" width="${w - p.l - p.r}" height="${y(80) - y(100)}"/>${boundaries}<g class="line-primary">${pathSegments(rows.map((row) => row.quality === "unavailable" ? { ...row, percentile: null } : row), "percentile", x, y)}</g><text class="axis-label" x="${p.l}" y="${h - 8}">${rows[0].date}</text><text class="axis-label" text-anchor="end" x="${w - p.r}" y="${h - 8}">${rows.at(-1).date}</text></svg>`;
}

function renderEvents() {
  const section = store.dashboard.events?.KOSPI?.nonOverlapping20d;
  const body = $("#event-table tbody");
  if (!section?.summary?.length) {
    body.innerHTML = `<tr><td colspan="7">실제 사건 표본이 아직 없습니다.</td></tr>`;
    $("#event-note").textContent = "사건 수가 없을 때 성과를 강조하지 않습니다.";
    return;
  }
  body.innerHTML = section.summary.map((row) => `<tr class="${row.smallSample ? "small-sample" : ""}"><td><span class="state-mark ${row.state.includes("greed") ? "greed" : ""}">${labels[row.state]}</span></td><td>${row.horizon}일</td><td>${row.eventCount}${row.smallSample ? "*" : ""}</td><td>${fmt.pct(row.mean)}</td><td>${fmt.pct(row.median)}</td><td>${fmt.pct(row.positiveRate, 1)}</td><td>${fmt.pct(row.meanCi95?.[0])} ~ ${fmt.pct(row.meanCi95?.[1])}</td></tr>`).join("");
  $("#event-note").textContent = `비중첩 사건 ${section.eventCount}개. * 20개 미만 표본은 소표본으로 흐리게 표시하며 통계적 확정으로 해석하지 않습니다.`;
}

function variantLabel(name) {
  return ({ base_5bp: "기본 · 5bp", base_10bp: "기본 · 10bp", base_20bp: "기본 · 20bp", disparity_10bp: "이격도 하위10% · 10bp" })[name] || name;
}

function renderBacktests() {
  const backtests = store.dashboard.backtests;
  const proxies = backtests?.proxies || {};
  const body = $("#backtest-table tbody");
  if (backtests?.status !== "ok" || !Object.keys(proxies).length) {
    body.innerHTML = `<tr><td colspan="9">가격 교차검증을 통과한 백테스트가 없습니다.</td></tr>`;
    $("#backtest-cards").innerHTML = `<p class="chart-note">KRX와 조정가격의 최근 공통 종가가 허용오차 0.5% 이내여야 공개됩니다.</p>`;
    showEmpty($("#equity-chart"), "백테스트 공개 보류");
    $("#trade-table tbody").innerHTML = `<tr><td colspan="5">거래 없음</td></tr>`;
    return;
  }
  const rows = [];
  Object.entries(proxies).forEach(([ticker, proxy]) => Object.entries(proxy.fullPeriod || {}).forEach(([name, result]) => {
    const m = result.metrics;
    rows.push(`<tr><td>${ticker} · ${variantLabel(name)}</td><td>${m.start}~${m.end}</td><td>${fmt.pct(m.cagr)}</td><td>${fmt.pct(m.volatility)}</td><td>${fmt.score(m.sharpe, 2)}</td><td>${fmt.pct(m.maxDrawdown)}</td><td>${fmt.pct(m.exposure, 1)}</td><td>${m.tradeCount}</td><td>${fmt.pct(m.buyAndHoldReturn)}</td></tr>`);
  }));
  body.innerHTML = rows.join("");
  $("#backtest-cards").innerHTML = Object.entries(proxies).map(([ticker, proxy]) => {
    const m = proxy.fullPeriod?.base_10bp?.metrics;
    return m ? `<div class="backtest-row"><span>${ticker} 기본 모형</span><strong>${fmt.pct(m.cagr)}</strong><span>CAGR / MDD</span><span>${fmt.pct(m.maxDrawdown)}</span><span>거래 ${m.tradeCount}회 · 노출 ${fmt.pct(m.exposure, 1)}</span><span>BH ${fmt.pct(m.buyAndHoldReturn)}</span></div>` : "";
  }).join("");
  renderEquity(proxies);
  const trades = proxies["226490"]?.fullPeriod?.base_10bp?.trades || [];
  $("#trade-table tbody").innerHTML = trades.length ? trades.slice(-12).reverse().map((trade) => `<tr><td>${trade.entry_date}</td><td>${trade.exit_date}</td><td>${trade.holding_sessions}</td><td>${labels[trade.reason] || trade.reason}</td><td>${fmt.pct(trade.net_return)}</td></tr>`).join("") : `<tr><td colspan="5">완결된 거래 없음</td></tr>`;
}

function renderEquity(proxies) {
  const series = Object.entries(proxies).map(([ticker, proxy]) => ({ ticker, rows: proxy.commonPeriod?.equity || [] })).filter((item) => item.rows.length > 1);
  const container = $("#equity-chart");
  if (!series.length) return showEmpty(container, "공통기간 누적가치가 없습니다.");
  const all = series.flatMap((item) => item.rows.flatMap((row) => [Number(row.value), Number(row.buyHoldValue)]));
  const min = Math.min(...all), max = Math.max(...all);
  const w = 640, h = 310, p = { l: 48, r: 20, t: 18, b: 34 };
  const length = Math.max(...series.map((item) => item.rows.length));
  const x = scale(0, length - 1, p.l, w - p.r), y = scale(min * .95, max * 1.05, h - p.b, p.t);
  const paths = series.map((item, index) => `<g class="${index ? "line-secondary" : "line-accent"}">${pathSegments(item.rows, "value", x, y)}</g><g class="line-buyhold">${pathSegments(item.rows, "buyHoldValue", x, y)}</g>`).join("");
  const legend = series.map((item, index) => `<text class="axis-label" x="${w - p.r}" y="${p.t + 12 + index * 16}" text-anchor="end">${item.ticker} 전략 ${index ? "┄" : "━"}</text>`).join("");
  const drawdowns = series.flatMap((item) => item.rows.flatMap((row) => [Number(row.drawdown), Number(row.buyHoldDrawdown)]));
  const ddMin = Math.min(...drawdowns, -.01), ddY = scale(ddMin, 0, h - p.b, p.t);
  const ddPaths = series.map((item, index) => `<g class="${index ? "line-secondary" : "line-accent"}">${pathSegments(item.rows, "drawdown", x, ddY)}</g><g class="line-buyhold">${pathSegments(item.rows, "buyHoldDrawdown", x, ddY)}</g>`).join("");
  container.innerHTML = `<svg viewBox="0 0 ${w} ${h}" aria-hidden="true"><line class="grid-line" x1="${p.l}" y1="${y(1)}" x2="${w - p.r}" y2="${y(1)}"/>${paths}${legend}<text class="axis-label" x="${p.l}" y="${h - 8}">${series[0].rows[0].date}</text><text class="axis-label" x="${w - p.r}" y="${h - 8}" text-anchor="end">${series[0].rows.at(-1).date}</text></svg><svg viewBox="0 0 ${w} ${h}" aria-hidden="true"><line class="grid-line" x1="${p.l}" y1="${ddY(0)}" x2="${w - p.r}" y2="${ddY(0)}"/>${ddPaths}<text class="axis-label" x="${p.l}" y="12">고점 대비 낙폭</text><text class="axis-label" x="${w - p.r}" y="12" text-anchor="end">매수·보유 ┅</text><text class="axis-label" x="${p.l}" y="${h - 8}">${series[0].rows[0].date}</text><text class="axis-label" x="${w - p.r}" y="${h - 8}" text-anchor="end">${series[0].rows.at(-1).date}</text></svg>`;
}

function renderDiagnostics() {
  const entity = store.summary.primaryEntities[0] || {};
  const diag = store.dashboard.diagnostics || {};
  const latest = diag.latest || {};
  $("#diagnostic-list").innerHTML = [
    ["KOSPI 50일 이격도", fmt.score(entity.disparity50, 1)],
    ["KOSPI MDD252", fmt.pct(entity.mdd252)],
    ["Micron KRW MDD252", fmt.pct(latest.muMdd252)],
    ["SK하이닉스 MDD252", fmt.pct(latest.hynixMdd252)],
    ["삼성전자 MDD252", fmt.pct(latest.samsungMdd252)],
    ["미국 세션 정렬", diag.status === "ok" ? "KRX일 이전 세션" : "산출 불가"]
  ].map(([key, value]) => `<dt>${key}</dt><dd>${value}</dd>`).join("");
  const rows = diag.series || [], container = $("#diagnostic-chart");
  if (rows.length < 2) return showEmpty(container, "상대 흐름 산출 불가");
  const fields = ["muKrwIndexed", "hynixIndexed", "samsungIndexed"];
  const values = rows.flatMap((row) => fields.map((field) => Number(row[field])).filter(Number.isFinite));
  const w = 560, h = 230, p = { l: 42, r: 18, t: 20, b: 32 }, min = Math.min(...values), max = Math.max(...values);
  const x = scale(0, rows.length - 1, p.l, w - p.r), y = scale(min * .95, max * 1.05, h - p.b, p.t);
  container.innerHTML = `<svg viewBox="0 0 ${w} ${h}" aria-hidden="true"><line class="grid-line" x1="${p.l}" y1="${y(100)}" x2="${w - p.r}" y2="${y(100)}"/><g class="line-accent">${pathSegments(rows, "muKrwIndexed", x, y)}</g><g class="line-secondary">${pathSegments(rows, "hynixIndexed", x, y)}</g><g class="line-olive">${pathSegments(rows, "samsungIndexed", x, y)}</g><text class="axis-label" x="${p.l}" y="12">MU·환율 ━  하이닉스 ┄  삼성 ···</text><text class="axis-label" x="${p.l}" y="${h - 8}">${rows[0].date}</text><text class="axis-label" x="${w - p.r}" y="${h - 8}" text-anchor="end">${rows.at(-1).date}</text></svg>`;
  container.setAttribute("aria-label", `${rows[0].date}부터 ${rows.at(-1).date}까지 Micron 환율 환산, SK하이닉스, 삼성전자 상대지수.`);
}

function showEmpty(container, message) {
  container.innerHTML = `<div class="empty"><strong>${message}</strong></div>`;
}

function renderAll() {
  renderHeader(store.summary);
  renderHistory(); renderScatter(); renderResidual(); renderEvents(); renderBacktests(); renderDiagnostics();
  bindChartTooltips();
}

function bindChartTooltips() {
  const tooltip = $("#tooltip");
  document.querySelectorAll('[role="img"][tabindex="0"]').forEach((chart) => {
    const show = () => {
      const box = chart.getBoundingClientRect();
      tooltip.textContent = chart.getAttribute("aria-label") || "차트";
      tooltip.style.left = `${Math.min(window.innerWidth - 250, Math.max(8, box.left + 12))}px`;
      tooltip.style.top = `${Math.min(window.innerHeight - 90, Math.max(8, box.top + 12))}px`;
      tooltip.hidden = false;
    };
    chart.onfocus = show; chart.onmouseenter = show;
    chart.onblur = () => { tooltip.hidden = true; };
    chart.onmouseleave = () => { if (document.activeElement !== chart) tooltip.hidden = true; };
  });
}

document.querySelectorAll("[data-window]").forEach((button) => button.addEventListener("click", () => {
  store.window = button.dataset.window;
  document.querySelectorAll("[data-window]").forEach((item) => item.classList.toggle("active", item === button));
  renderHistory(); renderResidual();
}));

$("#theme").addEventListener("click", () => {
  const root = document.documentElement;
  root.dataset.theme = root.dataset.theme === "dark" ? "light" : "dark";
  $("#theme").setAttribute("aria-pressed", String(root.dataset.theme === "dark"));
  localStorage.setItem("fg-theme", root.dataset.theme);
});
document.documentElement.dataset.theme = localStorage.getItem("fg-theme") || (matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light");

Promise.all([loadJson("data/summary.json"), loadJson("data/dashboard.json"), loadJson("data/history.json")])
  .then(([summary, dashboard, history]) => { validateContracts(summary, dashboard, history); store = { ...store, summary, dashboard, history }; renderAll(); })
  .catch((error) => {
    $("#status-badge").textContent = "unavailable"; $("#status-badge").className = "badge unavailable";
    $("#state").textContent = "데이터를 불러올 수 없음"; $("#status-note").textContent = error.message;
    $("#metrics").innerHTML = metric("공개 계약", "unavailable", "마지막 정상 시장 수치를 임의 값으로 대체하지 않습니다.");
  });
