const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => [...document.querySelectorAll(selector)];

const labels = {
  extreme_fear: "극단적 공포", fear: "공포", neutral: "중립", greed: "탐욕", extreme_greed: "극단적 탐욕",
  unavailable: "산출 불가", cash: "현금", long: "롱", recovery: "백분위 50 회복", max_holding: "최대 20일",
  enter_next_open: "다음 거래일 시가 진입", exit_next_open: "다음 거래일 시가 청산", extreme_fear_entry: "극단 공포 최초 진입", hold: "보유 유지"
};

const fmt = {
  pct: (value, digits = 2) => value == null || !Number.isFinite(Number(value)) ? "—" : `${(Number(value) * 100).toFixed(digits)}%`,
  signedPct: (value, digits = 2) => value == null || !Number.isFinite(Number(value)) ? "—" : `${Number(value) >= 0 ? "+" : ""}${(Number(value) * 100).toFixed(digits)}%`,
  score: (value, digits = 1) => value == null || !Number.isFinite(Number(value)) ? "—" : Number(value).toFixed(digits),
  compact: (value) => value == null || !Number.isFinite(Number(value)) ? "—" : Intl.NumberFormat("ko-KR", { notation: "compact", maximumFractionDigits: 2 }).format(value),
  date: (value) => value ? new Intl.DateTimeFormat("ko-KR", { year: "numeric", month: "short", day: "numeric" }).format(new Date(`${value}T00:00:00`)) : "—",
  multiple: (value, digits = 2) => value == null || !Number.isFinite(Number(value)) ? "—" : `${Number(value).toFixed(digits)}×`
};

const esc = (value) => String(value ?? "").replace(/[&<>"]/g, (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "\"": "&quot;" })[char]);

let store = {
  summary: null,
  dashboard: null,
  history: null,
  window: 756,
  model: "scaled",
  eventAsset: "KOSPI",
  eventSample: "nonOverlapping20d",
  backtestProxy: "226490",
  backtestVariant: "base",
  backtestCost: 10,
  backtestPeriod: "common"
};

async function loadJson(path) {
  const response = await fetch(path, { cache: "no-store" });
  if (!response.ok) throw new Error(`${path}: HTTP ${response.status}`);
  return response.json();
}

function validateContracts(summary, dashboard, history) {
  if (summary?.schemaVersion !== 1 || summary?.contract !== "quant-research-summary" || summary?.projectId !== "fearngreed") throw new Error("summary.json 계약이 올바르지 않습니다.");
  if (summary?.methodologyVersion !== "fear-flow-v1" || dashboard?.methodologyVersion !== "fear-flow-v1" || history?.methodologyVersion !== "fear-flow-v1") throw new Error("공개 데이터 방법론 버전이 올바르지 않습니다.");
  if (dashboard?.schemaVersion !== 1 || history?.schemaVersion !== 1 || dashboard?.dataAsOf !== summary.dataAsOf || history?.dataAsOf !== summary.dataAsOf) throw new Error("공개 데이터 스키마 또는 기준일이 올바르지 않습니다.");
  if (!["ok", "degraded", "stale", "unavailable"].includes(summary?.status?.state) || !Array.isArray(summary.primaryEntities) || summary.primaryEntities.length !== 1 || !summary.primaryEntities[0]?.models?.scaled || !summary.primaryEntities[0]?.models?.raw || !Array.isArray(history.series)) throw new Error("공개 데이터의 필수 계약이 없습니다.");
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
  return kind === "raw" ? "원문 충실 raw-flow" : "규모 보정";
}

function pendingActionText(base) {
  if (!base.pendingAction) return base.pendingReason || "대기 신호 없음";
  return `${labels[base.pendingAction] || base.pendingAction}${base.pendingReason ? ` · ${base.pendingReason}` : ""}`;
}

function metric(label, value, note) {
  return `<article class="metric"><span>${esc(label)}</span><strong title="${esc(value)}">${esc(value)}</strong><small>${esc(note)}</small></article>`;
}

function renderHeader() {
  const summary = store.summary;
  const base = entity();
  const model = modelPayload();
  const state = stateFromValue(model);
  const status = effectiveStatus(summary);
  const badge = $("#status-badge");
  badge.textContent = status === "stale" ? "stale · 갱신 지연" : (summary.status.label || summary.status.state);
  badge.className = `badge ${status}`;
  $("#confidence-badge").textContent = `${modelName()} · ${model?.quality || base.modelQuality || "미확인"}`;
  $("#state").textContent = labels[state] || state;
  $("#asof").textContent = `기준일 ${fmt.date(summary.dataAsOf)}`;
  const reasons = summary.status.degradedReasons || [];
  $("#status-note").textContent = reasons.length ? reasons.join(" · ") : "핵심 공급자와 계산 품질 게이트 통과";

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
    metric("모형 포지션", labels[base.position] || "—", `${base.primaryProxy || "226490"} · ${pendingActionText(base)}`),
    metric("252일 낙폭", fmt.pct(base.mdd252), "롤링 고점 대비")
  ].join("");

  const beta = model?.beta ?? regressionPayload()?.beta;
  $("#quality-strip").innerHTML = [
    `<span><strong>데이터:</strong> ${esc(summary.dataAsOf)}</span>`,
    `<span><strong>선택 모형:</strong> ${esc(modelName())}</span>`,
    `<span><strong>β:</strong> ${esc(fmt.score(beta, 4))}</span>`,
    `<span><strong>모형 품질:</strong> ${esc(model?.quality || base.modelQuality || "—")}</span>`,
    `<span><strong>공급 경로:</strong> ${esc(base.sourceMode || "—")}</span>`,
    `<span><strong>관측치:</strong> ${esc(fmt.compact(summary.coverage.observationCount))}</span>`,
    `<span><strong>비중첩 사건:</strong> ${esc(summary.coverage.eventCount)}</span>`,
    `<span><strong>거래:</strong> ${esc(summary.coverage.tradeCount)}</span>`
  ].join("");
  $("#model-selection-note").textContent = `${modelName()}의 서버 사전 계산값을 표시합니다. 기본 거래 신호는 규모 보정 모델로 고정됩니다.`;
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

function selectedHistory() {
  const rows = store.history.series || [];
  return store.window === "all" ? rows : rows.slice(-Number(store.window));
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
  chart._chartItems = valid;
  chart._chartIndex = Math.max(0, valid.length - 1);
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
  chart.onpointermove = (event) => {
    if (!valid.length) return;
    const box = chart.getBoundingClientRect();
    const ratio = Math.max(0, Math.min(1, (event.clientX - box.left + chart.scrollLeft) / Math.max(1, chart.scrollWidth)));
    selectIndex(Math.round(ratio * (valid.length - 1)), { x: event.clientX, y: event.clientY });
  };
  chart.onpointerdown = (event) => {
    if (event.pointerType !== "mouse") chart.focus({ preventScroll: true });
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
  const rows = selectedHistory().filter((row) => row.kospiClose != null || row.kospi != null);
  const container = $("#history-chart");
  if (rows.length < 8) return showEmpty(container, "시계열 관측치가 부족합니다.");
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
  const ticks = linearTicks(min - pad, max + pad).map((value) => {
    const yy = y(value);
    return `<line class="grid-line" x1="${p.l}" y1="${yy}" x2="${w - p.r}" y2="${yy}"/><text class="axis-label" x="${p.l - 9}" y="${yy + 3}" text-anchor="end">${Math.round(value).toLocaleString()}</text>`;
  }).join("");
  container.innerHTML = `<svg viewBox="0 0 ${w} ${h}" aria-hidden="true">${zones.join("")}${ticks}<line class="axis-line" x1="${p.l}" y1="${p.t}" x2="${p.l}" y2="${h - p.b}"/><line class="axis-line" x1="${p.l}" y1="${h - p.b}" x2="${w - p.r}" y2="${h - p.b}"/><g class="line-price">${pathSegments(rows, (row) => row.kospiClose ?? row.kospi, x, y)}</g>${events}<text class="axis-title" x="${p.l}" y="12">KOSPI 종가</text><text class="axis-label" x="${p.l}" y="${h - 10}">${esc(rows[0].date)}</text><text class="axis-label" x="${w - p.r}" y="${h - 10}" text-anchor="end">${esc(rows.at(-1).date)}</text></svg>`;
  container.setAttribute("aria-label", `${rows[0].date}부터 ${rows.at(-1).date}까지 KOSPI 종가. 최저 ${Math.round(min)}, 최고 ${Math.round(max)}.`);
  attachChartNavigation(container, rows, (row) => `${row.date}, KOSPI ${Number(row.kospiClose ?? row.kospi).toLocaleString()}, ${labels[row.state] || row.state}, 포지션 ${labels[row.position] || row.position}`);
  const tableRows = recentRows(rows).map((row) => [row.date, Number(row.kospiClose ?? row.kospi).toLocaleString(), labels[row.state] || row.state, labels[row.position] || row.position]);
  $("#history-data-table").innerHTML = dataTable(["날짜", "KOSPI", "연구 상태", "포지션"], tableRows, `선택 기간의 최근 ${tableRows.length}개 관측값`);
}

function scatterPoints() {
  if (store.model === "scaled" && Array.isArray(store.dashboard.scatter)) return store.dashboard.scatter.filter((row) => row.return1d != null && row.flowShare != null).map((row, index, rows) => ({ ...row, y: row.flowShare, role: row.role || (index === rows.length - 1 ? "current" : "training") }));
  const model = modelPayload("raw");
  if (!model) return [];
  const count = Number(model.trainingCount || 252) + 1;
  const rows = (store.history.series || []).filter((row) => row.return1d != null && row.rawFlowTrillion != null).slice(-count);
  return rows.map((row, index) => ({ ...row, y: row.rawFlowTrillion, role: index === rows.length - 1 ? "current" : "training" }));
}

function renderScatter() {
  const points = scatterPoints();
  const container = $("#scatter-chart");
  if (points.length < 8) {
    $("#scatter-title").textContent = "수익률 × 개인 수급";
    $("#scatter-subtitle").textContent = `${modelName()} 산점도 입력이 공개되지 않았습니다.`;
    $("#scatter-note").textContent = "사용할 수 없는 모형을 브라우저에서 추정해 대체하지 않습니다.";
    return showEmpty(container, "산점도 관측치가 부족합니다.");
  }
  const regression = regressionPayload();
  const xs = points.map((row) => Number(row.return1d));
  const ys = points.map((row) => Number(row.y));
  const predicted = (value) => Number(regression.alpha) + Number(regression.beta) * value;
  const predictedEnds = [predicted(Math.min(...xs)), predicted(Math.max(...xs))].filter(Number.isFinite);
  const xmin0 = Math.min(...xs), xmax0 = Math.max(...xs), ymin0 = Math.min(...ys, ...predictedEnds), ymax0 = Math.max(...ys, ...predictedEnds);
  const xpad = (xmax0 - xmin0 || .01) * .06, ypad = (ymax0 - ymin0 || .01) * .08;
  const xmin = xmin0 - xpad, xmax = xmax0 + xpad, ymin = ymin0 - ypad, ymax = ymax0 + ypad;
  const w = 600, h = 340, p = { l: 68, r: 20, t: 20, b: 50 };
  const x = scale(xmin, xmax, p.l, w - p.r), y = scale(ymin, ymax, h - p.b, p.t);
  const xTicks = linearTicks(xmin, xmax).map((value) => `<line class="grid-line" x1="${x(value)}" y1="${p.t}" x2="${x(value)}" y2="${h - p.b}"/><text class="axis-label" x="${x(value)}" y="${h - p.b + 17}" text-anchor="middle">${esc(fmt.pct(value, 1))}</text>`).join("");
  const yFormat = store.model === "raw" ? (value) => `${Number(value).toFixed(1)}조` : (value) => fmt.pct(value, 1);
  const yTicks = linearTicks(ymin, ymax).map((value) => `<line class="grid-line" x1="${p.l}" y1="${y(value)}" x2="${w - p.r}" y2="${y(value)}"/><text class="axis-label" x="${p.l - 8}" y="${y(value) + 3}" text-anchor="end">${esc(yFormat(value))}</text>`).join("");
  const marks = points.map((row) => `<circle class="scatter-point ${row.role === "current" ? "current" : ""}" cx="${x(row.return1d)}" cy="${y(row.y)}" r="${row.role === "current" ? 6 : 3}"><title>${esc(`${row.date} · 수익률 ${fmt.pct(row.return1d)} · ${store.model === "raw" ? `순매수 ${fmt.score(row.y, 3)}조원` : `순매수율 ${fmt.pct(row.y, 3)}`}`)}</title></circle>`).join("");
  const current = points.find((row) => row.role === "current") || points.at(-1);
  const currentPredicted = predicted(current.return1d);
  const residual = Number.isFinite(currentPredicted) ? `<line class="residual-arrow" x1="${x(current.return1d)}" y1="${y(currentPredicted)}" x2="${x(current.return1d)}" y2="${y(current.y)}"/>` : "";
  const regressionLine = predictedEnds.length === 2 ? `<line class="regression-line" x1="${x(xmin0)}" y1="${y(predicted(xmin0))}" x2="${x(xmax0)}" y2="${y(predicted(xmax0))}"/>` : "";
  container.innerHTML = `<svg viewBox="0 0 ${w} ${h}" aria-hidden="true">${xTicks}${yTicks}<line class="axis-line" x1="${p.l}" y1="${p.t}" x2="${p.l}" y2="${h - p.b}"/><line class="axis-line" x1="${p.l}" y1="${h - p.b}" x2="${w - p.r}" y2="${h - p.b}"/>${regressionLine}${residual}${marks}<text class="axis-title" x="${(p.l + w - p.r) / 2}" y="${h - 7}" text-anchor="middle">KOSPI 1일 수익률 (%)</text><text class="axis-title" x="${p.l}" y="13">${store.model === "raw" ? "개인 순매수대금 (조원)" : "개인 순매수율 (%)"}</text></svg>`;
  const inputLabel = store.model === "raw" ? "개인 순매수대금" : "개인 순매수율";
  $("#scatter-title").textContent = `수익률 × ${inputLabel}`;
  $("#scatter-subtitle").textContent = `${modelName()} · 직전 ${regression.trainingCount || regression.window || points.length - 1}거래일과 현재 관측`;
  container.setAttribute("aria-label", `${points.length}개 관측치 ${modelName()} 산점도. 현재 수익률 ${fmt.pct(current.return1d)}, ${inputLabel} ${store.model === "raw" ? `${fmt.score(current.y, 3)}조원` : fmt.pct(current.y, 3)}.`);
  $("#scatter-note").textContent = `학습 n=${points.filter((row) => row.role !== "current").length} · 현재 n=${points.filter((row) => row.role === "current").length} · β=${fmt.score(regression.beta, 4)} · R²=${fmt.score(modelPayload()?.rollingR2, 3)}`;
  attachChartNavigation(container, points, (row) => `${row.date}, KOSPI ${fmt.signedPct(row.return1d)}, ${inputLabel} ${store.model === "raw" ? `${fmt.score(row.y, 3)}조원` : fmt.pct(row.y, 3)}${row.role === "current" ? ", 현재 관측" : ", 학습 관측"}`);
  const tableRows = recentRows(points).map((row) => [row.date, fmt.signedPct(row.return1d), store.model === "raw" ? `${fmt.score(row.y, 3)}조원` : fmt.pct(row.y, 3), row.role === "current" ? "현재" : "학습"]);
  $("#scatter-data-table").innerHTML = dataTable(["날짜", "KOSPI 1일", inputLabel, "역할"], tableRows, `${modelName()} 최근 ${tableRows.length}개 산점도 관측`);
}

function renderResidual() {
  const rows = selectedHistory().slice(-756);
  const container = $("#residual-chart");
  if (rows.length < 8) return showEmpty(container, "잔차 시계열이 부족합니다.");
  const w = 600, h = 340, p = { l: 50, r: 18, t: 20, b: 40 };
  const x = scale(0, rows.length - 1, p.l, w - p.r), y = scale(0, 100, h - p.b, p.t);
  const boundaries = [5, 20, 50, 80, 95].map((value) => `<line class="grid-line ${value === 50 ? "midline" : ""}" x1="${p.l}" y1="${y(value)}" x2="${w - p.r}" y2="${y(value)}"/><text class="axis-label" x="${p.l - 7}" y="${y(value) + 3}" text-anchor="end">${value}</text>`).join("");
  const cleanRows = rows.map((row) => row.quality === "unavailable" || row.quality === "missing" ? { ...row, percentile: null } : row);
  container.innerHTML = `<svg viewBox="0 0 ${w} ${h}" aria-hidden="true"><rect class="residual-band-fear" x="${p.l}" y="${y(20)}" width="${w - p.l - p.r}" height="${y(0) - y(20)}"/><rect class="residual-band-greed" x="${p.l}" y="${y(100)}" width="${w - p.l - p.r}" height="${y(80) - y(100)}"/>${boundaries}<line class="axis-line" x1="${p.l}" y1="${p.t}" x2="${p.l}" y2="${h - p.b}"/><line class="axis-line" x1="${p.l}" y1="${h - p.b}" x2="${w - p.r}" y2="${h - p.b}"/><g class="line-primary">${pathSegments(cleanRows, "percentile", x, y)}</g><text class="axis-title" x="${p.l}" y="13">백분위 (0–100)</text><text class="axis-label" x="${p.l}" y="${h - 10}">${esc(rows[0].date)}</text><text class="axis-label" text-anchor="end" x="${w - p.r}" y="${h - 10}">${esc(rows.at(-1).date)}</text></svg>`;
  const validRows = rows.filter((row) => Number.isFinite(Number(row.percentile)));
  const latest = validRows.at(-1);
  container.setAttribute("aria-label", `규모보정 잔차 백분위 ${rows[0].date}부터 ${rows.at(-1).date}. 최신 ${latest ? fmt.score(latest.percentile) : "산출 불가"}.`);
  attachChartNavigation(container, validRows, (row) => `${row.date}, 규모보정 잔차 백분위 ${fmt.score(row.percentile)}, ${labels[row.state] || row.state}, 품질 ${row.quality}`);
  const tableRows = recentRows(rows).map((row) => [row.date, fmt.score(row.percentile), labels[row.state] || row.state, row.quality || "—"]);
  $("#residual-data-table").innerHTML = dataTable(["날짜", "백분위", "상태", "품질"], tableRows, `최근 ${tableRows.length}개 규모보정 잔차 백분위`);
}

function selectedEventSection() {
  return store.dashboard.events?.[store.eventAsset]?.[store.eventSample];
}

function renderEvents() {
  const section = selectedEventSection();
  const body = $("#event-table tbody");
  resetTableSort($("#event-table"));
  const sampleLabel = store.eventSample === "all" ? "전체 사건" : "20일 비중첩";
  $("#event-table caption").textContent = `${store.eventAsset} ${sampleLabel} 극단 상태 이후 선행수익률`;
  $("#event-source-line").textContent = `${store.eventAsset} · ${sampleLabel} · bootstrap 10,000회`;
  if (!section?.summary?.length) {
    body.innerHTML = `<tr><td colspan="7">선택한 사전 계산 사건 표본이 없습니다.</td></tr>`;
    $("#event-note").textContent = "사건 수가 없을 때 성과를 강조하지 않습니다.";
    return;
  }
  body.innerHTML = section.summary.map((row) => `<tr class="${row.smallSample ? "small-sample" : ""}"><td><span class="state-mark ${row.state.includes("greed") ? "greed" : ""}">${esc(labels[row.state] || row.state)}</span></td><td>${esc(row.horizon)}일</td><td>${esc(row.eventCount)}${row.smallSample ? "*" : ""}</td><td>${esc(fmt.pct(row.mean))}</td><td>${esc(fmt.pct(row.median))}</td><td>${esc(fmt.pct(row.positiveRate, 1))}</td><td>${esc(fmt.pct(row.meanCi95?.[0]))} ~ ${esc(fmt.pct(row.meanCi95?.[1]))}</td></tr>`).join("");
  $("#event-note").textContent = `${sampleLabel} ${section.eventCount}개. * 20개 미만 표본은 소표본으로 흐리게 표시하며 통계적 확정으로 해석하지 않습니다.`;
}

function variantKey(variant = store.backtestVariant, cost = store.backtestCost) {
  return variant === "disparity" ? `disparity_${cost}bp` : `base_${cost}bp`;
}

function variantLabel(name) {
  return ({ base_5bp: "극단 공포 · 5bp", base_10bp: "극단 공포 · 10bp", base_20bp: "극단 공포 · 20bp", disparity_10bp: "이격도 하위10% · 10bp" })[name] || name;
}

function resultFor({ proxy = store.backtestProxy, period = store.backtestPeriod, variant = store.backtestVariant, cost = store.backtestCost } = {}) {
  const data = store.dashboard.backtests?.proxies?.[proxy];
  if (!data) return null;
  const key = variantKey(variant, cost);
  if (period === "full") return data.fullPeriod?.[key] || null;
  const common = data.commonPeriod;
  if (!common) return null;
  if (common[key]?.metrics) return common[key];
  if (common.metrics && variant === "base" && Number(common.oneWayCostBps ?? 10) === Number(cost)) return common;
  return null;
}

function hasAnyResult({ proxy = store.backtestProxy, period = store.backtestPeriod, variant = store.backtestVariant, cost = store.backtestCost } = {}) {
  if (resultFor({ proxy, period, variant, cost })) return true;
  return ["base", "disparity"].some((v) => [5, 10, 20].some((c) => resultFor({ proxy, period, variant: v, cost: c })));
}

function ensureBacktestSelection() {
  if (resultFor()) return;
  const candidates = [
    { variant: store.backtestVariant, cost: 10 },
    { variant: "base", cost: 10 },
    { variant: "base", cost: 5 },
    { variant: "base", cost: 20 }
  ];
  const fallback = candidates.find((candidate) => resultFor(candidate));
  if (fallback) Object.assign(store, { backtestVariant: fallback.variant, backtestCost: fallback.cost });
}

function renderBacktests() {
  const backtests = store.dashboard.backtests;
  const body = $("#backtest-table tbody");
  resetTableSort($("#backtest-table"));
  resetTableSort($("#trade-table"));
  if (backtests?.status !== "ok" || !Object.keys(backtests?.proxies || {}).length) {
    body.innerHTML = `<tr><td colspan="14">가격 교차검증을 통과한 백테스트가 없습니다.</td></tr>`;
    $("#backtest-cards").innerHTML = `<p class="chart-note">KRX와 조정가격의 최근 공통 종가가 허용오차 0.5% 이내여야 공개됩니다.</p>`;
    showEmpty($("#equity-chart"), "백테스트 공개 보류");
    $("#trade-table tbody").innerHTML = `<tr><td colspan="5">거래 없음</td></tr>`;
    return;
  }
  ensureBacktestSelection();
  const result = resultFor();
  updateBacktestControls();
  const periodLabel = store.backtestPeriod === "common" ? "ETF 공통 기간" : "전체 가능 기간";
  const key = variantKey();
  const selectionLabel = `${store.backtestProxy} · ${variantLabel(key)} · ${periodLabel}`;
  $("#backtest-card-subtitle").textContent = selectionLabel;
  $("#equity-title").textContent = `${store.backtestProxy} 누적가치와 낙폭`;
  $("#equity-subtitle").textContent = `${variantLabel(key)} · ${periodLabel} · 전략 대 매수·보유`;
  $("#backtest-table caption").textContent = `${selectionLabel} 성과·위험 상세`;
  $("#trade-table caption").textContent = `${selectionLabel} 최근 거래내역`;
  if (!result?.metrics) {
    body.innerHTML = `<tr><td colspan="14">선택 조합의 사전 계산 결과가 공개되지 않았습니다.</td></tr>`;
    $("#backtest-cards").innerHTML = `<p class="chart-note">다른 기간·비용·진입 규칙을 선택해 주세요.</p>`;
    showEmpty($("#equity-chart"), "선택 결과 없음");
    $("#trade-table tbody").innerHTML = `<tr><td colspan="5">거래 없음</td></tr>`;
    renderConclusion();
    return;
  }
  const m = result.metrics;
  body.innerHTML = `<tr><td>${esc(`${store.backtestProxy} · ${variantLabel(key)}`)}</td><td>${esc(`${m.start}~${m.end}`)}</td><td>${esc(fmt.pct(m.totalReturn))}</td><td>${esc(fmt.pct(m.cagr))}</td><td>${esc(fmt.pct(m.volatility))}</td><td>${esc(fmt.score(m.sharpe, 2))}</td><td>${esc(fmt.pct(m.maxDrawdown))}</td><td>${esc(fmt.pct(m.winRate, 1))}</td><td>${esc(fmt.pct(m.exposure, 1))}</td><td>${esc(fmt.score(m.turnover, 2))}×</td><td>${esc(m.tradeCount)}</td><td>${esc(fmt.score(m.averageHoldingSessions, 1))}일</td><td>${esc(fmt.pct(m.buyAndHoldReturn))}</td><td>${esc(fmt.pct(m.buyAndHoldMaxDrawdown))}</td></tr>`;
  $("#backtest-cards").innerHTML = [
    metric("전략 총수익률", fmt.pct(m.totalReturn), `CAGR ${fmt.pct(m.cagr)}`),
    metric("전략 최대낙폭", fmt.pct(m.maxDrawdown), `변동성 ${fmt.pct(m.volatility)}`),
    metric("매수·보유 수익률", fmt.pct(m.buyAndHoldReturn), `MDD ${fmt.pct(m.buyAndHoldMaxDrawdown)}`),
    metric("거래 승률", fmt.pct(m.winRate, 1), `${m.tradeCount}건 · 평균 ${fmt.score(m.averageHoldingSessions, 1)}일`),
    metric("노출도", fmt.pct(m.exposure, 1), `회전율 ${fmt.score(m.turnover, 2)}×`),
    metric("Sharpe", fmt.score(m.sharpe, 2), "현금수익률 0%")
  ].join("");
  renderEquity(result, selectionLabel);
  const trades = result.trades || [];
  $("#trade-table tbody").innerHTML = trades.length ? trades.slice(-12).reverse().map((trade) => `<tr><td>${esc(trade.entry_date)}</td><td>${esc(trade.exit_date)}</td><td>${esc(trade.holding_sessions)}</td><td>${esc(labels[trade.reason] || trade.reason)}</td><td>${esc(fmt.pct(trade.net_return))}</td></tr>`).join("") : `<tr><td colspan="5">완결된 거래 없음</td></tr>`;
  applyTableFilter($("#trade-filter"));
  renderConclusion();
}

function renderEquity(result, selectionLabel) {
  const rows = result.equity || [];
  const container = $("#equity-chart");
  if (rows.length < 2) {
    $("#equity-data-table").innerHTML = `<p class="empty-inline">선택 기간의 일별 누적가치가 공개 계약에 없습니다. 성과 표의 정확값을 확인하세요.</p>`;
    return showEmpty(container, "선택 기간의 누적가치 시계열 미공개");
  }
  const values = rows.flatMap((row) => [Number(row.value), Number(row.buyHoldValue)]).filter(Number.isFinite);
  const min0 = Math.min(...values), max0 = Math.max(...values), span = max0 - min0 || .1;
  const min = min0 - span * .06, max = max0 + span * .08;
  const w = 680, h = 280, p = { l: 66, r: 22, t: 24, b: 40 };
  const x = scale(0, rows.length - 1, p.l, w - p.r), y = scale(min, max, h - p.b, p.t);
  const yTicks = linearTicks(min, max).map((value) => `<line class="grid-line" x1="${p.l}" y1="${y(value)}" x2="${w - p.r}" y2="${y(value)}"/><text class="axis-label" x="${p.l - 8}" y="${y(value) + 3}" text-anchor="end">${esc(fmt.score(value, 2))}</text>`).join("");
  const valueSvg = `<svg viewBox="0 0 ${w} ${h}" aria-hidden="true">${yTicks}<line class="axis-line" x1="${p.l}" y1="${p.t}" x2="${p.l}" y2="${h - p.b}"/><line class="axis-line" x1="${p.l}" y1="${h - p.b}" x2="${w - p.r}" y2="${h - p.b}"/><g class="line-strategy">${pathSegments(rows, "value", x, y)}</g><g class="line-buyhold">${pathSegments(rows, "buyHoldValue", x, y)}</g><text class="axis-title" x="${p.l}" y="14">누적가치 (초기=1)</text><text class="axis-label" x="${p.l}" y="${h - 10}">${esc(rows[0].date)}</text><text class="axis-label" x="${w - p.r}" y="${h - 10}" text-anchor="end">${esc(rows.at(-1).date)}</text></svg>`;
  const drawdowns = rows.flatMap((row) => [Number(row.drawdown), Number(row.buyHoldDrawdown)]).filter(Number.isFinite);
  const ddMin = Math.min(...drawdowns, -.01), ddMax = 0;
  const ddY = scale(ddMin, ddMax, h - p.b, p.t);
  const ddTicks = linearTicks(ddMin, ddMax).map((value) => `<line class="grid-line" x1="${p.l}" y1="${ddY(value)}" x2="${w - p.r}" y2="${ddY(value)}"/><text class="axis-label" x="${p.l - 8}" y="${ddY(value) + 3}" text-anchor="end">${esc(fmt.pct(value, 0))}</text>`).join("");
  const ddSvg = `<svg viewBox="0 0 ${w} ${h}" aria-hidden="true">${ddTicks}<line class="axis-line" x1="${p.l}" y1="${p.t}" x2="${p.l}" y2="${h - p.b}"/><line class="axis-line" x1="${p.l}" y1="${h - p.b}" x2="${w - p.r}" y2="${h - p.b}"/><g class="line-strategy">${pathSegments(rows, "drawdown", x, ddY)}</g><g class="line-buyhold">${pathSegments(rows, "buyHoldDrawdown", x, ddY)}</g><text class="axis-title" x="${p.l}" y="14">고점 대비 낙폭 (%)</text><text class="axis-label" x="${p.l}" y="${h - 10}">${esc(rows[0].date)}</text><text class="axis-label" x="${w - p.r}" y="${h - 10}" text-anchor="end">${esc(rows.at(-1).date)}</text></svg>`;
  container.innerHTML = valueSvg + ddSvg;
  const last = rows.at(-1);
  container.setAttribute("aria-label", `${selectionLabel}. ${rows[0].date}부터 ${last.date}. 최종 전략 누적가치 ${fmt.score(last.value, 3)}, 매수·보유 ${fmt.score(last.buyHoldValue, 3)}.`);
  attachChartNavigation(container, rows, (row) => `${row.date}, 전략 누적가치 ${fmt.score(row.value, 3)}, 매수·보유 ${fmt.score(row.buyHoldValue, 3)}, 전략 낙폭 ${fmt.pct(row.drawdown)}, 매수·보유 낙폭 ${fmt.pct(row.buyHoldDrawdown)}`);
  $("#equity-data-table").innerHTML = dataTable(["시점", "날짜", "전략 가치", "매수·보유 가치", "전략 낙폭", "BH 낙폭"], [["시작", rows[0].date, fmt.score(rows[0].value, 3), fmt.score(rows[0].buyHoldValue, 3), fmt.pct(rows[0].drawdown), fmt.pct(rows[0].buyHoldDrawdown)], ["최신", last.date, fmt.score(last.value, 3), fmt.score(last.buyHoldValue, 3), fmt.pct(last.drawdown), fmt.pct(last.buyHoldDrawdown)]], `${selectionLabel} 시작·최신 값`);
}

function renderConclusion() {
  if (!store.dashboard) return;
  const section = selectedEventSection();
  const result = resultFor();
  const fear20 = section?.summary?.find((row) => row.state === "extreme_fear" && Number(row.horizon) === 20);
  const greed20 = section?.summary?.find((row) => row.state === "extreme_greed" && Number(row.horizon) === 20);
  const metrics = result?.metrics;
  const eventConclusive = Number(fear20?.meanCi95?.[0]) > 0;
  const strategyOutperforms = metrics && Number(metrics.totalReturn) > Number(metrics.buyAndHoldReturn);
  let tone = "mixed";
  let verdict = "선택 결과의 근거가 충분하지 않아 가설을 판정할 수 없습니다.";
  if (fear20 && metrics) {
    if (eventConclusive && strategyOutperforms) {
      tone = "supportive";
      verdict = "선택한 사건 표본과 전략 비교는 공포 뒤 반등 가설을 함께 지지합니다.";
    } else if (!eventConclusive && !strategyOutperforms) {
      tone = "caution";
      verdict = "선택한 공개 결과는 ‘극단적 공포가 저점과 잘 맞는다’는 가설을 강하게 뒷받침하지 않습니다.";
    } else {
      verdict = "사건 연구와 거래 전략의 증거가 엇갈립니다. 한 결과만으로 예측력을 주장할 수 없습니다.";
    }
  }
  const sampleLabel = store.eventSample === "all" ? "전체 사건" : "20일 비중첩";
  const key = variantKey();
  const facts = [
    fear20 ? `<span><strong>극단 공포 후 20일</strong>${esc(fmt.signedPct(fear20.mean))} · 95% CI ${esc(fmt.pct(fear20.meanCi95?.[0]))}~${esc(fmt.pct(fear20.meanCi95?.[1]))} · n=${esc(fear20.eventCount)}</span>` : `<span><strong>사건 근거</strong>선택 표본 없음</span>`,
    greed20 ? `<span><strong>극단 탐욕 후 20일</strong>${esc(fmt.signedPct(greed20.mean))} · n=${esc(greed20.eventCount)}</span>` : "",
    metrics ? `<span><strong>선택 전략 대 매수·보유</strong>${esc(fmt.signedPct(metrics.totalReturn))} vs ${esc(fmt.signedPct(metrics.buyAndHoldReturn))}</span>` : `<span><strong>전략 근거</strong>선택 결과 없음</span>`
  ].filter(Boolean).join("");
  $("#research-conclusion").className = `conclusion-card ${tone}`;
  $("#research-conclusion").innerHTML = `<div class="conclusion-heading"><div><p class="eyebrow">EVIDENCE FIRST</p><h2 id="conclusion-title">현재 공개 결과가 말하는 것</h2></div><span class="badge neutral">${esc(store.eventAsset)} · ${esc(sampleLabel)} · ${esc(store.backtestProxy)} ${esc(variantLabel(key))}</span></div><p class="conclusion-lead">${esc(verdict)}</p><div class="conclusion-facts">${facts}</div><p class="conclusion-footnote">사전 정의된 결과를 그대로 읽은 것이며, 화면 선택으로 임계값이나 백테스트를 다시 계산하지 않습니다.</p>`;
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
    const supported = [5, 10, 20].some((cost) => resultFor({ variant, cost }));
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
  $("#backtest-selection-note").textContent = available ? "공개된 사전 계산 결과입니다. 비활성 선택지는 해당 조합이 발행되지 않았음을 뜻합니다." : "선택 조합의 사전 계산 결과가 없습니다.";
}

function bindControls() {
  $$('[data-window]').forEach((button) => button.addEventListener("click", () => {
    store.window = button.dataset.window;
    updatePressed("[data-window]", store.window, "window");
    renderHistory();
    renderResidual();
  }));
  $$('[data-model]').forEach((button) => button.addEventListener("click", () => {
    if (button.disabled) return;
    store.model = button.dataset.model;
    updatePressed("[data-model]", store.model, "model");
    renderHeader();
    renderScatter();
  }));
  $$('[data-event-asset]').forEach((button) => button.addEventListener("click", () => {
    store.eventAsset = button.dataset.eventAsset;
    updatePressed("[data-event-asset]", store.eventAsset, "eventAsset");
    renderEvents();
    renderConclusion();
  }));
  $$('[data-event-sample]').forEach((button) => button.addEventListener("click", () => {
    store.eventSample = button.dataset.eventSample;
    updatePressed("[data-event-sample]", store.eventSample, "eventSample");
    renderEvents();
    renderConclusion();
  }));
  $$('[data-backtest-proxy]').forEach((button) => button.addEventListener("click", () => {
    store.backtestProxy = button.dataset.backtestProxy;
    ensureBacktestSelection();
    renderBacktests();
  }));
  $$('[data-backtest-variant]').forEach((button) => button.addEventListener("click", () => {
    store.backtestVariant = button.dataset.backtestVariant;
    ensureBacktestSelection();
    renderBacktests();
  }));
  $$('[data-backtest-cost]').forEach((button) => button.addEventListener("click", () => {
    store.backtestCost = Number(button.dataset.backtestCost);
    ensureBacktestSelection();
    renderBacktests();
  }));
  $$('[data-backtest-period]').forEach((button) => button.addEventListener("click", () => {
    store.backtestPeriod = button.dataset.backtestPeriod;
    ensureBacktestSelection();
    renderBacktests();
  }));
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
  const rawAvailable = Boolean(modelPayload("raw"));
  const rawButton = $('[data-model="raw"]');
  rawButton.disabled = !rawAvailable;
  rawButton.setAttribute("aria-disabled", String(!rawAvailable));
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
  renderDiagnostics();
  renderConclusion();
  enhanceTables();
  applyTableFilter($("#trade-filter"));
}

initializeTheme();
bindControls();
bindTableTools();
initializeSectionNav();

Promise.all([loadJson("data/summary.json"), loadJson("data/dashboard.json"), loadJson("data/history.json")])
  .then(([summary, dashboard, history]) => {
    validateContracts(summary, dashboard, history);
    store = { ...store, summary, dashboard, history };
    renderAll();
  })
  .catch((error) => {
    $("#status-badge").textContent = "unavailable";
    $("#status-badge").className = "badge unavailable";
    $("#state").textContent = "데이터를 불러올 수 없음";
    $("#status-note").textContent = error.message;
    $("#metrics").innerHTML = metric("공개 계약", "unavailable", "마지막 정상 시장 수치를 임의 값으로 대체하지 않습니다.");
    $("#research-conclusion").innerHTML = `<div><p class="eyebrow">EVIDENCE FIRST</p><h2 id="conclusion-title">연구 결론을 표시할 수 없습니다</h2></div><p>공개 데이터 계약을 확인해 주세요. 임의 수치로 대체하지 않습니다.</p>`;
  });
