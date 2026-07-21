import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";

import {
  DEFAULT_SIGNAL_CONFIG,
  TRACK_FIELD_MAPPING,
  classifyDynamicPercentile,
  computeDynamicSignals,
  computeDynamicSignalsAsync,
  fitDynamicSignalAt,
  minimumObservationCount,
  normalizeSignalConfig,
  runDynamicEventStudy
} from "../assets/signal-engine.js";

function isoDay(index) {
  return new Date(Date.UTC(2020, 0, 1 + index)).toISOString().slice(0, 10);
}

function signalRows(count = 100) {
  return Array.from({ length: count }, (_, index) => ({
    date: isoDay(index),
    kospiClose: 100 + index,
    return1d: (index % 13 - 6) / 1_000,
    flowShare: -(index % 13 - 6) / 500 + Math.sin(index * 1.7) / 1_000,
    rawFlowTrillion: -(index % 13 - 6) / 20 + Math.cos(index * 1.3) / 100,
    p226490Close: index >= 5 ? 90 + index : null,
    p069500Close: 80 + index
  }));
}

function decodeHistory(payload) {
  return payload.seriesRows.map((values) => Object.fromEntries(
    payload.seriesColumns.map((column, index) => [column, values[index]])
  ));
}

test("dynamic signal controls have declared deterministic bounds", () => {
  assert.deepEqual(DEFAULT_SIGNAL_CONFIG, { track: "robust", lookback: 252, minimumR2: 0.2, extremeTail: 5 });
  assert.equal(minimumObservationCount(60), 48);
  assert.equal(minimumObservationCount(252), 200);
  assert.equal(minimumObservationCount(756), 200);
  assert.equal(normalizeSignalConfig({}).minimumObservationPolicy, "min(window,max(40,min(200,ceil(window*0.8))))");
  assert.throws(() => normalizeSignalConfig({minimumR2: 0.07}), /0.05 간격/);
  for (const config of [{ lookback: 59 }, { lookback: 757 }, { minimumR2: 0.81 }, { extremeTail: 0 }, { extremeTail: 21 }]) {
    assert.throws(() => normalizeSignalConfig(config));
  }
  assert.equal(classifyDynamicPercentile(5, 5), "extreme_fear");
  assert.equal(classifyDynamicPercentile(20, 5), "fear");
  assert.equal(classifyDynamicPercentile(80, 5), "greed");
  assert.equal(classifyDynamicPercentile(95, 5), "extreme_greed");
});

test("rolling fit is strictly past-only and overwrites only the selected legacy track", () => {
  const base = signalRows(90);
  const first = computeDynamicSignals({ historyRows: base, track: "scaled", lookback: 60, minimumR2: 0, extremeTail: 10 });
  const changedFuture = base.map((row, index) => index === 89 ? { ...row, flowShare: 999 } : row);
  const second = computeDynamicSignals({ historyRows: changedFuture, track: "scaled", lookback: 60, minimumR2: 0, extremeTail: 10 });
  assert.deepEqual(first.rows.slice(0, 89), second.rows.slice(0, 89));
  assert.equal(first.rows[80].scaledState, first.rows[80].dynamicSignal.state);
  assert.equal(first.rows[80].scaledPercentile, first.rows[80].dynamicSignal.percentile);
  assert.equal(first.rows[80].scaledTradeEligible, first.rows[80].dynamicSignal.tradeEligible);
  assert.equal(first.rows[80].state, undefined);
  assert.equal(first.currentFit.trainingRows.length, 60);
  assert.equal(first.currentFit.current.role, "current");
  assert.equal(first.latest.trainingEnd < first.latest.date, true);
});

test("a historical focus date returns that session's fit without changing the full signal path", () => {
  const rows = signalRows(100);
  const latest = computeDynamicSignals({ historyRows: rows, track: "raw", lookback: 60, minimumR2: 0 });
  const focused = computeDynamicSignals({
    historyRows: rows,
    track: "raw",
    lookback: 60,
    minimumR2: 0,
    focusDate: isoDay(84)
  });
  assert.deepEqual(focused.rows, latest.rows);
  assert.equal(focused.requestedFocusDate, isoDay(84));
  assert.equal(focused.appliedFocusDate, isoDay(84));
  assert.equal(focused.focus.date, isoDay(84));
  assert.equal(focused.currentFit.current.date, isoDay(84));
  assert.equal(focused.currentFit.trainingEnd < focused.currentFit.current.date, true);
});

test("async rolling fit is output-identical to the synchronous engine and reports monotonic progress", async () => {
  const rows = signalRows(100);
  const options = {
    historyRows: rows,
    track: "robust",
    lookback: 60,
    minimumR2: 0,
    extremeTail: 10,
    focusDate: isoDay(84)
  };
  const expected = computeDynamicSignals(options);
  const progress = [];
  const actual = await computeDynamicSignalsAsync({
    ...options,
    onProgress: (snapshot) => progress.push(snapshot)
  });

  assert.deepEqual(actual, expected);
  assert.deepEqual(progress[0], { processed: 0, total: rows.length, ratio: 0 });
  assert.deepEqual(progress.at(-1), { processed: rows.length, total: rows.length, ratio: 1 });
  assert.ok(progress.length > 2);
  assert.ok(progress.every((snapshot, index) => index === 0 || snapshot.processed > progress[index - 1].processed));
  assert.ok(progress.every(Object.isFrozen));
  await assert.rejects(
    computeDynamicSignalsAsync({ ...options, onProgress: "invalid" }),
    /진행률 콜백은 함수/
  );
});

test("async rolling fit yields to a queued timer before it completes", async () => {
  const rows = signalRows(100);
  let timerRan = false;
  let observedTimerDuringProgress = false;
  setTimeout(() => { timerRan = true; }, 0);

  await computeDynamicSignalsAsync({
    historyRows: rows,
    track: "robust",
    lookback: 60,
    minimumR2: 0,
    onProgress: ({ processed, total }) => {
      if (processed > 0 && processed < total && timerRan) observedTimerDuringProgress = true;
    }
  });

  assert.equal(observedTimerDuringProgress, true);
});

test("single-row fit uses only rows before the selected evaluation index", () => {
  const rows = signalRows(100);
  const selected = fitDynamicSignalAt({ historyRows: rows, index: 80, track: "robust", lookback: 60, minimumR2: 0 });
  const futureChanged = rows.map((row, index) => index > 80 ? { ...row, flowShare: 1_000 + index } : row);
  const repeated = fitDynamicSignalAt({ historyRows: futureChanged, index: 80, track: "robust", lookback: 60, minimumR2: 0 });
  assert.deepEqual(selected, repeated);
  assert.equal(selected.signal.date, rows[80].date);
  assert.equal(selected.currentFit.trainingStart, rows[20].date);
  assert.equal(selected.currentFit.trainingEnd, rows[79].date);
  assert.equal(selected.currentFit.current.date, rows[80].date);
  assert.throws(() => fitDynamicSignalAt({ historyRows: rows, index: 100 }), /범위/);
});

test("positive slope and insufficient fit fail closed for trading while retaining diagnostics", () => {
  const rows = signalRows(80).map((row, index) => ({
    ...row,
    flowShare: row.return1d * 2 + Math.sin(index) / 10_000
  }));
  const result = computeDynamicSignals({ historyRows: rows, track: "robust", lookback: 60, minimumR2: 0.2 });
  assert.ok(result.latest.beta > 0);
  assert.equal(result.latest.quality, "low_model_fit");
  assert.equal(result.latest.tradeEligible, false);
  assert.ok(Number.isFinite(result.latest.percentile));
  assert.equal(result.rows.at(-1).quality, "low_model_fit");
});

test("default browser fits reproduce the latest published models from rounded public inputs", async () => {
  const payload = JSON.parse(await readFile(new URL("../data/history.json", import.meta.url), "utf8"));
  const dashboard = JSON.parse(await readFile(new URL("../data/dashboard.json", import.meta.url), "utf8"));
  const rows = decodeHistory(payload).slice(-320);
  for (const track of ["robust", "scaled", "raw"]) {
    const result = computeDynamicSignals({ historyRows: rows, track });
    const published = payload.models[track];
    const fields = TRACK_FIELD_MAPPING[track];
    assert.equal(result.latest.state, published.state, track);
    assert.equal(result.latest.tradeEligible, published.tradeEligible, track);
    assert.ok(Math.abs(result.latest.percentile - published.percentile) < 1e-9, `${track} percentile`);
    assert.ok(Math.abs(result.latest.alpha - published.alpha) < 2e-8, `${track} alpha`);
    const betaTolerance = Math.max(2e-7, Math.abs(published.beta) * 2e-8);
    assert.ok(Math.abs(result.latest.beta - published.beta) < betaTolerance, `${track} beta`);
    assert.ok(Math.abs(result.latest.rollingR2 - published.rollingR2) < 2e-8, `${track} r2`);
    const fitScoreTolerance = track === "robust" ? 1e-7 : 3e-8;
    assert.ok(Math.abs(result.latest.fitScore - published.fitScore) < fitScoreTolerance, `${track} fit score`);
    assert.equal(fields.method, published.fitMethod);
    for (let offset = 1; offset <= 20; offset += 1) {
      const dynamicRow = result.rows.at(-offset);
      const publishedRow = rows.at(-offset);
      assert.equal(dynamicRow.dynamicSignal.state, publishedRow[fields.state], `${track} state ${publishedRow.date}`);
      assert.ok(
        Math.abs(dynamicRow.dynamicSignal.percentile - publishedRow[fields.percentile]) < 6e-9,
        `${track} percentile ${publishedRow.date}`
      );
    }
    const cuts = result.currentFit.residualCuts;
    const publishedCuts = dashboard.scatterMetaByModel[track].stateBoundaries.residualOffsets;
    const cutTolerance = track === "raw" ? 3e-7 : 2e-8;
    assert.deepEqual(Object.keys(cuts), ["5", "20", "80", "95"]);
    assert.ok(cuts["5"] <= cuts["20"] && cuts["20"] <= cuts["80"] && cuts["80"] <= cuts["95"]);
    assert.ok(Math.abs(cuts["5"] - publishedCuts.extremeFearUpper) < cutTolerance, `${track} lower extreme cut`);
    assert.ok(Math.abs(cuts["20"] - publishedCuts.fearUpper) < cutTolerance, `${track} fear cut`);
    assert.ok(Math.abs(cuts["80"] - publishedCuts.greedLower) < cutTolerance, `${track} greed cut`);
    assert.ok(Math.abs(cuts["95"] - publishedCuts.extremeGreedLower) < cutTolerance, `${track} upper extreme cut`);
  }
});

test("dynamic event study deduplicates continuous extremes, applies the combined 20-session rule, and is deterministic", () => {
  const rows = signalRows(75).map((row) => ({
    ...row,
    dynamicSignal: { track: "robust", state: "neutral", percentile: 50, tradeEligible: true }
  }));
  for (const [index, state, percentile] of [
    [10, "extreme_fear", 2],
    [11, "extreme_fear", 3],
    [20, "extreme_greed", 98],
    [31, "extreme_fear", 1],
    [55, "extreme_greed", 99]
  ]) {
    rows[index].dynamicSignal = { track: "robust", state, percentile, tradeEligible: true };
  }
  const options = { historyRows: rows, track: "robust", asset: "KOSPI", bootstrapSamples: 10_000, seed: 7 };
  const all = runDynamicEventStudy({ ...options, sample: "all" });
  const first = runDynamicEventStudy({ ...options, sample: "nonOverlapping20d" });
  const second = runDynamicEventStudy({ ...options, sample: "nonOverlapping20d" });
  assert.deepEqual(all.events.map((event) => event.index), [10, 20, 31, 55]);
  assert.deepEqual(first.events.map((event) => event.index), [10, 31, 55]);
  assert.deepEqual(first, second);
  assert.deepEqual(first.bootstrap, { method: "iid_seeded_mulberry32", samples: 10_000, seed: 7 });
  assert.equal(first.summary.find((row) => row.state === "extreme_fear" && row.horizon === 20).eventCount, 2);
  assert.ok(first.summary.every((row) => Array.isArray(row.meanCi95) && row.meanCi95.length === 2));
});

test("ETF event horizons count valid ETF sessions and date filters do not create boundary events", () => {
  const rows = signalRows(40).map((row) => ({
    ...row,
    dynamicSignal: { track: "scaled", state: "neutral", percentile: 50, tradeEligible: true }
  }));
  rows[2].dynamicSignal = { track: "scaled", state: "extreme_fear", percentile: 1, tradeEligible: true };
  rows[8].dynamicSignal = { track: "scaled", state: "extreme_greed", percentile: 99, tradeEligible: true };
  const result = runDynamicEventStudy({
    historyRows: rows,
    track: "scaled",
    asset: "226490",
    sample: "all",
    startDate: rows[5].date,
    endDate: rows[20].date,
    horizons: [1],
    bootstrapSamples: 10
  });
  assert.equal(result.eventCount, 1);
  assert.equal(result.events[0].date, rows[8].date);
  assert.equal(result.events[0].return1d, rows[9].p226490Close / rows[8].p226490Close - 1);

  rows[4].dynamicSignal = { track: "scaled", state: "extreme_fear", percentile: 1, tradeEligible: true };
  rows[5].dynamicSignal = { track: "scaled", state: "extreme_fear", percentile: 2, tradeEligible: true };
  const boundary = runDynamicEventStudy({
    historyRows: rows,
    track: "scaled",
    asset: "226490",
    sample: "all",
    startDate: rows[5].date,
    endDate: rows[6].date,
    horizons: [1],
    bootstrapSamples: 10
  });
  assert.equal(boundary.eventCount, 0);
});

test("event end date is an information cutoff that censors unavailable forward outcomes", () => {
  const rows = signalRows(50).map((row) => ({
    ...row,
    dynamicSignal: { track: "robust", state: "neutral", percentile: 50, tradeEligible: true }
  }));
  rows[10].dynamicSignal = { track: "robust", state: "extreme_fear", percentile: 1, tradeEligible: true };
  const result = runDynamicEventStudy({
    historyRows: rows,
    track: "robust",
    sample: "all",
    endDate: rows[15].date,
    horizons: [1, 5, 10],
    bootstrapSamples: 10
  });
  assert.equal(result.informationCutoffDate, rows[15].date);
  assert.ok(Number.isFinite(result.events[0].return1d));
  assert.ok(Number.isFinite(result.events[0].return5d));
  assert.equal(result.events[0].return10d, null);
  assert.equal(result.summary.find((row) => row.state === "extreme_fear" && row.horizon === 10).eventCount, 0);
});
