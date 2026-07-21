import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";

import { bootDashboard, click, fireInput, signature, submit, waitFor } from "./helpers/dashboard-harness.mjs";

const SIGNAL_OUTPUTS = ["#state", "#signal-bridge", "#scatter-chart", "#residual-chart", "#event-table tbody"];
const STRATEGY_OUTPUTS = ["#history-chart", "#backtest-cards", "#open-trades", "#backtest-table tbody", "#trade-table tbody"];
const CONFIRMED_DATA_AS_OF = JSON.parse(
  await readFile(new URL("../data/summary.json", import.meta.url), "utf8")
).dataAsOf;

function nextIsoDate(date) {
  return new Date(Date.parse(`${date}T00:00:00Z`) + 86_400_000).toISOString().slice(0, 10);
}

function koreanDate(date) {
  const [year, month, day] = date.split("-").map(Number);
  return `${year}년 ${month}월 ${day}일`;
}

function kstDate(offsetDays = 0) {
  return new Date(Date.now() + 9 * 60 * 60 * 1000 + offsetDays * 86_400_000).toISOString().slice(0, 10);
}

function liveSignalFixture(overrides = {}) {
  const signalDate = overrides.signalDate || kstDate();
  return {
    schemaVersion: 1,
    contract: "fearngreed-live-signal",
    projectId: "fearngreed",
    methodologyVersion: "fear-flow-v5",
    signalDate,
    phase: "provisional",
    generatedAt: `${signalDate}T06:48:00Z`,
    sourceCutoff: "regular-session-close-provisional",
    historyDataAsOf: CONFIRMED_DATA_AS_OF,
    expectedConfirmationAt: `${signalDate}T09:15:00Z`,
    actionWindow: {
      mode: "after-hours-close",
      opensAt: `${signalDate}T06:40:00Z`,
      closesAt: `${signalDate}T07:00:00Z`,
      state: "open",
      executionGuaranteed: false
    },
    quality: { state: "ok", tradeEligible: true, reasons: [] },
    inputRow: {
      date: signalDate,
      kospiClose: 6780,
      return1d: -0.006,
      flowShare: -0.45,
      rawFlowTrillion: 8,
      disparity50: 84,
      mdd252: -0.26
    },
    ...overrides
  };
}

function failNextInnerHtmlWrite(target) {
  let prototype = target;
  let descriptor;
  while (prototype && !descriptor) {
    prototype = Object.getPrototypeOf(prototype);
    descriptor = prototype && Object.getOwnPropertyDescriptor(prototype, "innerHTML");
  }
  assert.ok(descriptor?.get && descriptor?.set);
  let shouldFail = true;
  Object.defineProperty(target, "innerHTML", {
    configurable: true,
    get() { return descriptor.get.call(this); },
    set(value) {
      if (shouldFail) {
        shouldFail = false;
        throw new Error("forced one-shot render failure");
      }
      descriptor.set.call(this, value);
    }
  });
  return () => { delete target.innerHTML; };
}

test("recommended page defaults load once while legacy custom scenarios remain editable", { concurrency: false, timeout: 120_000 }, async () => {
  const fresh = await bootDashboard();
  const freshDocument = fresh.document;
  assert.equal(freshDocument.querySelector('[data-model="raw"]').getAttribute("aria-pressed"), "true");
  assert.equal(freshDocument.querySelector('[data-window="ytd"]').getAttribute("aria-pressed"), "true");
  assert.equal(freshDocument.querySelector('[data-event-sample="all"]').getAttribute("aria-pressed"), "true");
  assert.deepEqual(
    [
      Number(freshDocument.querySelector("#signal-lookback-input").value),
      Number(freshDocument.querySelector("#signal-min-r2-input").value),
      Number(freshDocument.querySelector("#signal-tail-input").value),
      Number(freshDocument.querySelector("#signal-max-holding-input").value)
    ],
    [196, 0.4, 2, 20]
  );
  assert.match(freshDocument.querySelector("#signal-settings-status").textContent, /과거 196일 · 최소 R² 0\.40 · 극단 ≤2\/≥98/);

  const migrated = await bootDashboard({
    storage: {
      "fearngreed-controls-v6": JSON.stringify({
        window: "3y",
        model: "robust",
        eventSample: "nonOverlapping20d",
        signalLookback: 252,
        signalMinimumR2: 0.2,
        signalExtremeTail: 5,
        signalMaxHolding: 20,
        longExitPercentile: 75
      })
    }
  });
  const migratedSaved = JSON.parse(migrated.localStorage.getItem("fearngreed-controls-v7"));
  assert.deepEqual(
    [migratedSaved.window, migratedSaved.model, migratedSaved.eventSample, migratedSaved.signalLookback, migratedSaved.signalMinimumR2, migratedSaved.signalExtremeTail],
    ["ytd", "raw", "all", 196, 0.4, 2]
  );
  assert.equal(migratedSaved.longExitPercentile, 75, "an unrelated user-set exit value must survive migration");

  const custom = await bootDashboard({
    storage: {
      "fearngreed-controls-v6": JSON.stringify({
        window: "1y",
        model: "scaled",
        eventSample: "nonOverlapping20d",
        signalLookback: 126,
        signalMinimumR2: 0.35,
        signalExtremeTail: 7,
        signalMaxHolding: 12
      })
    }
  });
  const customSaved = JSON.parse(custom.localStorage.getItem("fearngreed-controls-v7"));
  assert.deepEqual(
    [customSaved.window, customSaved.model, customSaved.eventSample, customSaved.signalLookback, customSaved.signalMinimumR2, customSaved.signalExtremeTail, customSaved.signalMaxHolding],
    ["1y", "scaled", "nonOverlapping20d", 126, 0.35, 7, 12]
  );

  const customVariant = await bootDashboard({
    storage: {
      "fearngreed-controls-v6": JSON.stringify({
        window: "3y",
        model: "robust",
        eventSample: "nonOverlapping20d",
        backtestVariant: "scaled_ols",
        signalLookback: 252,
        signalMinimumR2: 0.2,
        signalExtremeTail: 5,
        signalMaxHolding: 20
      })
    }
  });
  const customVariantSaved = JSON.parse(customVariant.localStorage.getItem("fearngreed-controls-v7"));
  assert.equal(customVariantSaved.signalLookback, 252);
});

test("real DOM inputs preserve drafts and atomically update the connected analysis", { concurrency: false, timeout: 120_000 }, async () => {
  const window = await bootDashboard();
  const { document } = window;

  const initialSignal = signature(document, SIGNAL_OUTPUTS);
  const initialStrategy = signature(document, STRATEGY_OUTPUTS);
  const initialUrl = window.location.href;

  fireInput(window, "#exit-threshold-input", 60);
  assert.equal(document.querySelector("#exit-threshold-form").dataset.dirty, "true");
  assert.equal(document.querySelector("#exit-threshold-status").dataset.state, "dirty");
  assert.match(document.querySelector("#exit-threshold-status").textContent, /미적용 변경/);
  assert.equal(signature(document, SIGNAL_OUTPUTS), initialSignal);
  assert.equal(signature(document, STRATEGY_OUTPUTS), initialStrategy);
  assert.equal(window.location.href, initialUrl);

  submit(window, "#exit-threshold-form");
  assert.equal(document.querySelector("#exit-threshold-form").dataset.dirty, undefined);
  assert.equal(new URL(window.location.href).searchParams.get("exit"), "60");
  assert.equal(signature(document, SIGNAL_OUTPUTS), initialSignal, "exit threshold must not refit the signal or event study");
  assert.notEqual(signature(document, STRATEGY_OUTPUTS), initialStrategy, "exit threshold must update strategy charts and tables");

  const strategyAtExit60 = signature(document, STRATEGY_OUTPUTS);
  const signalBeforeDraft = signature(document, SIGNAL_OUTPUTS);
  const urlBeforeDraft = window.location.href;
  fireInput(window, "#signal-lookback-input", 126);
  fireInput(window, "#signal-min-r2-input", 0.4);
  fireInput(window, "#signal-tail-input", 10);
  fireInput(window, "#signal-max-holding-input", 5);
  assert.equal(document.querySelector("#signal-settings-form").dataset.dirty, "true");
  assert.match(document.querySelector("#signal-settings-status").textContent, /미적용 변경/);
  assert.equal(signature(document, SIGNAL_OUTPUTS), signalBeforeDraft);
  assert.equal(signature(document, STRATEGY_OUTPUTS), strategyAtExit60);
  assert.equal(window.location.href, urlBeforeDraft);

  submit(window, "#signal-settings-form");
  await waitFor(() => document.querySelector("#signal-settings-status").dataset.state === "ok", "signal settings recompute");
  assert.equal(document.querySelector("#signal-settings-form").dataset.dirty, undefined);
  const appliedUrl = new URL(window.location.href);
  assert.equal(appliedUrl.searchParams.get("lookback"), "126");
  assert.equal(appliedUrl.searchParams.get("minR2"), "0.4");
  assert.equal(appliedUrl.searchParams.get("tail"), "10");
  assert.equal(appliedUrl.searchParams.get("maxHold"), "5");
  const saved = JSON.parse(window.localStorage.getItem("fearngreed-controls-v7"));
  assert.deepEqual(
    [saved.signalLookback, saved.signalMinimumR2, saved.signalExtremeTail, saved.signalMaxHolding],
    [126, 0.4, 10, 5]
  );
  assert.notEqual(signature(document, SIGNAL_OUTPUTS), signalBeforeDraft);
  assert.notEqual(signature(document, STRATEGY_OUTPUTS), strategyAtExit60);

  const beforeInvalid = signature(document, [...SIGNAL_OUTPUTS, ...STRATEGY_OUTPUTS]);
  const beforeInvalidUrl = window.location.href;
  fireInput(window, "#signal-lookback-input", 59);
  submit(window, "#signal-settings-form");
  assert.equal(document.querySelector("#signal-lookback-input").getAttribute("aria-invalid"), "true");
  assert.equal(document.querySelector("#signal-settings-status").dataset.state, "error");
  assert.equal(signature(document, [...SIGNAL_OUTPUTS, ...STRATEGY_OUTPUTS]), beforeInvalid);
  assert.equal(window.location.href, beforeInvalidUrl);
  fireInput(window, "#signal-lookback-input", 126);
  assert.equal(document.querySelector("#signal-settings-form").dataset.dirty, undefined);

  fireInput(window, "#history-start", "2026-04-16");
  fireInput(window, "#history-end", "2026-06-15");
  assert.equal(document.querySelector("#history-range-form").dataset.dirty, "true");
  const beforeDateStrategy = signature(document, STRATEGY_OUTPUTS);
  submit(window, "#history-range-form");
  assert.equal(document.querySelector("#history-range-form").dataset.dirty, undefined);
  assert.notEqual(signature(document, STRATEGY_OUTPUTS), beforeDateStrategy);
  const diagnosticEntries = Object.fromEntries(
    [...document.querySelectorAll("#diagnostic-list dt")].map((term) => [term.textContent.trim(), term.nextElementSibling?.textContent.trim()])
  );
  assert.equal(diagnosticEntries["KOSPI 기준일"], "2026-06-15");
  assert.ok(diagnosticEntries["반도체 기준일"] <= "2026-06-15" && diagnosticEntries["반도체 기준일"] !== "2026-07-16");
  const diagnosticDates = [...document.querySelectorAll("#diagnostic-data-table tbody tr td:first-child, #diagnostic-data-table tbody tr th:first-child")].map((cell) => cell.textContent.trim());
  assert.ok(diagnosticDates.length > 0);
  assert.ok(diagnosticDates.every((date) => date <= "2026-06-15"));
  const diagnosticSummary = document.querySelector("#diagnostic-list").textContent;
  assert.doesNotMatch(
    diagnosticSummary,
    /(Micron KRW|SK하이닉스|삼성전자) MDD252—/,
    "historical diagnostic snapshots must use the selected date's rolling drawdowns"
  );

  const dateRollbackBaseline = signature(document, STRATEGY_OUTPUTS);
  const dateRollbackUrl = window.location.href;
  const dateForm = document.querySelector("#history-range-form");
  const appliedStart = dateForm.dataset.appliedStart;
  const appliedEnd = dateForm.dataset.appliedEnd;
  const restoreDateFailure = failNextInnerHtmlWrite(document.querySelector("#backtest-cards"));
  fireInput(window, "#history-start", "2026-05-04");
  fireInput(window, "#history-end", "2026-06-30");
  submit(window, "#history-range-form");
  assert.equal(dateForm.dataset.dirty, "true");
  assert.equal(document.querySelector("#history-range-status").dataset.state, "error");
  assert.match(document.querySelector("#history-range-status").textContent, /기존 기간 결과를 유지/);
  assert.equal(dateForm.dataset.appliedStart, appliedStart);
  assert.equal(dateForm.dataset.appliedEnd, appliedEnd);
  assert.equal(signature(document, STRATEGY_OUTPUTS), dateRollbackBaseline);
  assert.equal(window.location.href, dateRollbackUrl);
  restoreDateFailure();
  fireInput(window, "#history-start", appliedStart);
  fireInput(window, "#history-end", appliedEnd);
  assert.equal(dateForm.dataset.dirty, undefined, "restored applied dates must clear the failed draft");

  const rollbackBaseline = signature(document, STRATEGY_OUTPUTS);
  const rollbackUrl = window.location.href;
  const target = document.querySelector("#backtest-cards");
  const restoreExitFailure = failNextInnerHtmlWrite(target);
  fireInput(window, "#exit-threshold-input", 75);
  submit(window, "#exit-threshold-form");
  assert.equal(document.querySelector("#exit-threshold-form").dataset.dirty, "true");
  assert.equal(document.querySelector("#exit-threshold-status").dataset.state, "error");
  assert.match(document.querySelector("#exit-threshold-status").textContent, /기존 결과를 유지/);
  assert.equal(signature(document, STRATEGY_OUTPUTS), rollbackBaseline);
  assert.equal(window.location.href, rollbackUrl);
  restoreExitFailure();
});

test("segmented controls, sharing, and reset keep one applied scenario", { concurrency: false, timeout: 120_000 }, async () => {
  const window = await bootDashboard();
  const { document } = window;
  const savedControls = () => JSON.parse(window.localStorage.getItem("fearngreed-controls-v7"));
  const assertApplied = (selector, param, value, storageKey) => {
    assert.equal(document.querySelector(selector).getAttribute("aria-pressed"), "true");
    assert.equal(new URL(window.location.href).searchParams.get(param), String(value));
    assert.equal(String(savedControls()[storageKey]), String(value));
  };

  let before = signature(document, ["#event-ci-chart", "#event-table tbody"]);
  click(window, '[data-event-asset="226490"]');
  assertApplied('[data-event-asset="226490"]', "eventAsset", "226490", "eventAsset");
  assert.notEqual(signature(document, ["#event-ci-chart", "#event-table tbody"]), before);

  before = signature(document, ["#event-ci-chart", "#event-table tbody"]);
  click(window, '[data-event-sample="nonOverlapping20d"]');
  assertApplied('[data-event-sample="nonOverlapping20d"]', "eventSample", "nonOverlapping20d", "eventSample");
  assert.notEqual(signature(document, ["#event-ci-chart", "#event-table tbody"]), before);

  before = signature(document, STRATEGY_OUTPUTS);
  click(window, '[data-window="1y"]');
  assertApplied('[data-window="1y"]', "window", "1y", "window");
  assert.notEqual(signature(document, STRATEGY_OUTPUTS), before);

  before = signature(document, STRATEGY_OUTPUTS);
  click(window, '[data-backtest-policy="long_inverse_cash"]');
  assertApplied('[data-backtest-policy="long_inverse_cash"]', "policy", "long_inverse_cash", "backtestPolicy");
  assert.notEqual(signature(document, STRATEGY_OUTPUTS), before);

  before = signature(document, STRATEGY_OUTPUTS);
  click(window, '[data-backtest-pair="2x"]');
  assertApplied('[data-backtest-pair="2x"]', "pair", "2x", "backtestProxy");
  assert.notEqual(signature(document, STRATEGY_OUTPUTS), before);

  before = signature(document, STRATEGY_OUTPUTS);
  click(window, '[data-backtest-cost="20"]');
  assertApplied('[data-backtest-cost="20"]', "cost", "20", "backtestCost");
  assert.notEqual(signature(document, STRATEGY_OUTPUTS), before);

  click(window, '[data-backtest-period="full"]');
  assertApplied('[data-backtest-period="full"]', "period", "full", "backtestPeriod");

  const syncRollbackBaseline = signature(document, STRATEGY_OUTPUTS);
  const syncRollbackUrl = window.location.href;
  const restoreSyncFailure = failNextInnerHtmlWrite(document.querySelector("#backtest-cards"));
  click(window, '[data-backtest-cost="0"]');
  assert.equal(signature(document, STRATEGY_OUTPUTS), syncRollbackBaseline);
  assert.equal(window.location.href, syncRollbackUrl);
  assertApplied('[data-backtest-cost="20"]', "cost", "20", "backtestCost");
  assert.match(document.querySelector("#view-action-status").textContent, /기존 결과를 유지/);
  restoreSyncFailure();

  before = signature(document, SIGNAL_OUTPUTS);
  click(window, '[data-model="scaled"]');
  assert.equal(document.querySelector("#share-view").disabled, true, "unverified async settings must not be shareable");
  await waitFor(
    () => document.querySelector("#signal-settings-status").dataset.state === "ok" && document.querySelector(".analysis-config").getAttribute("aria-busy") === "false",
    "research track recompute"
  );
  assert.equal(document.querySelector("#share-view").disabled, false);
  assertApplied('[data-model="scaled"]', "model", "scaled", "model");
  assert.equal(new URL(window.location.href).searchParams.get("strategy"), "scaled_ols");
  assert.notEqual(signature(document, SIGNAL_OUTPUTS), before);

  click(window, "#share-view");
  await waitFor(() => window.__copiedText === window.location.href, "applied link copy");
  assert.match(document.querySelector("#view-action-status").textContent, /적용된 설정 링크/);

  click(window, "#reset-controls");
  assertApplied('[data-model="raw"]', "model", "raw", "model");
  assertApplied('[data-window="ytd"]', "window", "ytd", "window");
  assertApplied('[data-event-sample="all"]', "eventSample", "all", "eventSample");
  assertApplied('[data-backtest-policy="compare"]', "policy", "compare", "backtestPolicy");
  assertApplied('[data-backtest-pair="1x"]', "pair", "1x", "backtestProxy");
  assertApplied('[data-backtest-cost="10"]', "cost", "10", "backtestCost");
  assertApplied('[data-backtest-period="common"]', "period", "common", "backtestPeriod");
  assert.match(document.querySelector("#view-action-status").textContent, /기본값으로 복원/);
});

test("current open trades expose execution details and follow policy, pair, and date controls", { concurrency: false, timeout: 120_000 }, async () => {
  const window = await bootDashboard();
  const { document } = window;
  const initial = document.querySelector("#open-trades").textContent;
  assert.match(initial, /진입 신호일 · 종가/);
  assert.match(initial, /진입 체결일 · 시가/);
  assert.match(initial, /진입 조정시가/);
  assert.match(initial, /보유 거래일/);
  assert.match(initial, /평가 손익 · 미실현/);
  assert.match(initial, /다음 예정 행동/);
  assert.equal(document.querySelectorAll("#open-trades .open-trade-panel").length, 2, "compare mode must disclose both policy states");
  assert.match(document.querySelector("#open-trade-subtitle").textContent, new RegExp(`${CONFIRMED_DATA_AS_OF} 종가 평가`));

  click(window, '[data-backtest-policy="long_cash"]');
  assert.equal(document.querySelectorAll("#open-trades .open-trade-panel").length, 1);
  assert.match(document.querySelector("#open-trades").textContent, /롱 \/ 현금/);

  const oneX = document.querySelector("#open-trades").textContent;
  click(window, '[data-backtest-pair="2x"]');
  const twoX = document.querySelector("#open-trades").textContent;
  assert.notEqual(twoX, oneX);
  assert.match(twoX, /122630|252670|현금/);

  fireInput(window, "#history-start", "2026-04-16");
  fireInput(window, "#history-end", "2026-06-15");
  submit(window, "#history-range-form");
  assert.match(document.querySelector("#open-trade-subtitle").textContent, /2026-06-15 종가 평가/);
  assert.notEqual(document.querySelector("#open-trades").textContent, twoX);
});

test("a newer provisional signal is input-linked but never extends confirmed charts or backtests", { concurrency: false, timeout: 120_000 }, async () => {
  const signalDate = nextIsoDate(CONFIRMED_DATA_AS_OF);
  const originalNow = Date.now;
  const realStartedAt = originalNow();
  const scenarioStartedAt = Date.parse(`${signalDate}T07:30:00Z`);
  Date.now = () => scenarioStartedAt + (originalNow() - realStartedAt);
  try {
    const window = await bootDashboard({ dataOverrides: { "live-signal.json": liveSignalFixture({ signalDate }) } });
    const { document } = window;
    const strip = document.querySelector("#live-signal-strip");
    assert.equal(strip.hidden, false);
    assert.equal(strip.dataset.phase, "provisional");
    assert.match(document.querySelector("#live-phase-badge").textContent, /잠정/);
    assert.match(document.querySelector("#live-signal-score").textContent, /백분위/);
    assert.match(document.querySelector("#live-signal-time").textContent, /계산/);
    assert.match(document.querySelector("#live-action-note").textContent, /시간외 종가/);
    assert.match(document.querySelector("#live-confirmed-anchor").textContent, new RegExp(`${koreanDate(CONFIRMED_DATA_AS_OF)} 확정 기준`));
    assert.doesNotMatch(document.querySelector("#history-data-table").textContent, new RegExp(signalDate));

    const rawLive = signature(document, ["#live-signal-state", "#live-signal-score"]);
    click(window, '[data-model="robust"]');
    await waitFor(
      () => document.querySelector("#signal-settings-status").dataset.state === "ok" && document.querySelector(".analysis-config").getAttribute("aria-busy") === "false",
      "live signal track recompute"
    );
    assert.notEqual(signature(document, ["#live-signal-state", "#live-signal-score"]), rawLive);
    assert.doesNotMatch(document.querySelector("#history-data-table").textContent, new RegExp(signalDate));
  } finally {
    Date.now = originalNow;
  }
});

test("same-date or malformed live payloads never replace the confirmed dashboard", { concurrency: false, timeout: 120_000 }, async () => {
  const sameDateWindow = await bootDashboard({ dataOverrides: { "live-signal.json": liveSignalFixture({ signalDate: "2026-07-16", inputRow: { ...liveSignalFixture().inputRow, date: "2026-07-16" } }) } });
  assert.equal(sameDateWindow.document.querySelector("#live-signal-strip").hidden, true);
  assert.notEqual(sameDateWindow.document.querySelector("#status-badge").textContent, "unavailable");

  const malformedWindow = await bootDashboard({ dataOverrides: { "live-signal.json": { ...liveSignalFixture(), contract: "wrong-contract" } } });
  assert.equal(malformedWindow.document.querySelector("#live-signal-strip").hidden, true);
  assert.notEqual(malformedWindow.document.querySelector("#status-badge").textContent, "unavailable");
  assert.match(malformedWindow.document.querySelector("#state").textContent, /중립|공포|탐욕/);

  const staleDate = kstDate(-1);
  const staleWindow = await bootDashboard({ dataOverrides: { "live-signal.json": liveSignalFixture({ signalDate: staleDate, inputRow: { ...liveSignalFixture().inputRow, date: staleDate } }) } });
  assert.equal(staleWindow.document.querySelector("#live-signal-strip").hidden, true);
});
