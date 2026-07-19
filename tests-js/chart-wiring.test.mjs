import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";

import { computeDynamicSignals } from "../assets/signal-engine.js";
import { runActualEtfPairScenario } from "../assets/strategy-engine.js";

async function publishedHistory() {
  const payload = JSON.parse(await readFile(new URL("../data/history.json", import.meta.url), "utf8"));
  return payload.seriesRows.map((values) => Object.fromEntries(
    payload.seriesColumns.map((column, index) => [column, values[index]])
  ));
}

test("the integrated chart's June 10 executions are distinct policy outcomes from one prior-close signal", async () => {
  const history = await publishedHistory();
  const signals = computeDynamicSignals({
    historyRows: history,
    track: "robust",
    lookback: 252,
    minimumR2: 0.2,
    extremeTail: 5
  }).rows;
  const options = {
    history: signals,
    pairId: "1x",
    period: "common",
    costBps: 10,
    exitPercentile: 60,
    maxHoldDays: 20,
    dateStart: "2026-04-16",
    dateEnd: "2026-07-16"
  };
  const longCash = runActualEtfPairScenario({ ...options, policy: "long_cash" });
  const longInverse = runActualEtfPairScenario({ ...options, policy: "long_inverse_cash" });
  const cashAction = longCash.actions.filter((action) => action.date === "2026-06-10");
  const inverseAction = longInverse.actions.filter((action) => action.date === "2026-06-10");

  assert.deepEqual(cashAction.map(({ type, fromTicker, toTicker, signalDate }) => ({ type, fromTicker, toTicker, signalDate })), [
    { type: "exit", fromTicker: "069500", toTicker: null, signalDate: "2026-06-09" }
  ]);
  assert.deepEqual(inverseAction.map(({ type, fromTicker, toTicker, signalDate }) => ({ type, fromTicker, toTicker, signalDate })), [
    { type: "reverse", fromTicker: "069500", toTicker: "114800", signalDate: "2026-06-09" }
  ]);
  const selectedLongCash = longCash.equity.find((row) => row.date === "2026-06-15");
  const selectedLongInverse = longInverse.equity.find((row) => row.date === "2026-06-15");
  assert.ok(Math.abs(selectedLongCash.value - 1.0070788260538546) < 1e-12);
  assert.ok(Math.abs(selectedLongInverse.value - 0.9361822016278595) < 1e-12);
  assert.ok(Math.abs(selectedLongCash.buyHoldValue - 1.4454261737631553) < 1e-12);
  assert.ok(Math.abs(longCash.equity.at(-1).value - 0.9911058546572223) < 1e-12);
  assert.ok(Math.abs(longInverse.equity.at(-1).value - 1.0659139579340846) < 1e-12);
  assert.ok(Math.abs(longCash.equity.at(-1).buyHoldValue - 1.1560015624050475) < 1e-12);
  for (const result of [longCash, longInverse]) {
    assert.equal(result.equity.at(-1).date, result.metrics.end);
    assert.ok(Math.abs((result.equity.at(-1).value - 1) - result.metrics.totalReturn) < 1e-12);
    assert.ok(Math.abs((result.equity.at(-1).buyHoldValue - 1) - result.metrics.buyAndHoldReturn) < 1e-12);
    assert.ok(Math.abs(Math.min(...result.equity.map((row) => row.drawdown)) - result.metrics.maxDrawdown) < 1e-12);
    assert.ok(Math.abs(Math.min(...result.equity.map((row) => row.buyHoldDrawdown)) - result.metrics.buyAndHoldMaxDrawdown) < 1e-12);
  }
  for (const action of [...longCash.actions, ...longInverse.actions]) assert.ok(action.signalDate < action.executionDate);
});
