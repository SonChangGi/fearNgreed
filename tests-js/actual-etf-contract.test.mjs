import test from "node:test";
import assert from "node:assert/strict";
import { assertActualEtfContract } from "./helpers/actual-etf-contract.mjs";

// Small, fixed contract examples keep both availability branches under test
// regardless of which optional providers succeeded in the latest daily refresh.
function contractFixture({ failed = [], commonAvailable = true } = {}) {
  const tickers = ["069500", "114800", "122630", "252670"];
  const crosschecks = { etf: Object.fromEntries(tickers.map((ticker) => [ticker, {
    state: failed.includes(ticker) ? "unavailable" : "ok",
    reason: failed.includes(ticker) ? "secondary_unavailable" : null
  }])) };
  const commonPeriod = {
    basis: "four_etf_common_adjusted_price_sessions", tickers,
    status: commonAvailable ? "ok" : "unavailable",
    reason: commonAvailable ? null : "four_etf_common_period_unavailable",
    start: commonAvailable ? "2026-04-01" : null,
    end: commonAvailable ? "2026-04-02" : null,
    sessionCount: commonAvailable ? 2 : 0
  };
  const section = {
    authority: "canonical_server_verified_actual_etfs", canonical: true,
    calculationSource: "python_verified_actual_etfs",
    implementation: "positive_units_in_listed_long_and_inverse_etfs",
    oneWayCostBps: 10, longExitPercentile: 80, inverseExitPercentile: 20,
    commonPeriod, pairs: {}
  };
  for (const [pairId, longTicker, inverseTicker, leverage] of [
    ["1x", "069500", "114800", 1], ["2x", "122630", "252670", 2]
  ]) {
    const pair = { pairId, longTicker, inverseTicker, leverage, implementation: "actual_listed_etfs" };
    const pairFailures = [longTicker, inverseTicker].filter((ticker) => failed.includes(ticker));
    const reason = !commonAvailable ? "four_etf_common_period_unavailable"
      : pairFailures.length ? `official_crosscheck_failed_${pairFailures.join("_")}` : null;
    const status = reason ? "unavailable" : "ok";
    const policies = {};
    const fullPeriodMetrics = {};
    for (const policyId of ["long_cash", "long_inverse_cash"]) {
      const metrics = reason ? null : { start: commonPeriod.start, end: commonPeriod.end, shortExposure: 0, totalReturn: 0 };
      policies[policyId] = {
        pair, policyId, status, unavailableReason: reason,
        calculationSource: "python_verified_actual_etfs",
        oneWayCostBps: 10, longExitPercentile: 80, inverseExitPercentile: 20,
        position: reason ? "unavailable" : "cash",
        latestPosition: reason ? "unavailable" : "cash",
        latestInstrumentTicker: null, openPosition: false, openTrade: null,
        pendingAction: null, pendingReason: null, pendingSide: null, pendingSignalDate: null,
        metrics, range: null, trades: [], actions: [], holdingSegments: [],
        equity: reason ? [] : [{ date: commonPeriod.start, value: 1 }, { date: commonPeriod.end, value: 1 }]
      };
      fullPeriodMetrics[policyId] = {
        status, unavailableReason: reason, position: reason ? "unavailable" : "cash",
        latestInstrumentTicker: null, metrics, range: null
      };
    }
    section.pairs[pairId] = { pair, status, reason, policies, fullPeriodMetrics };
  }
  return { section, crosschecks };
}

test("actual ETF contract accepts verified pairs and isolates failed optional pair crosschecks", () => {
  for (const options of [{}, { failed: ["114800"] }, { failed: ["122630", "252670"] }, { commonAvailable: false }]) {
    const { section, crosschecks } = contractFixture(options);
    assertActualEtfContract(section, crosschecks);
  }
});

test("actual ETF contract rejects failures disguised as healthy results and unexplained unavailability", () => {
  const good = contractFixture();
  good.crosschecks.etf["114800"].state = "unavailable";
  assert.throws(() => assertActualEtfContract(good.section, good.crosschecks), /status must follow/);
  const degraded = contractFixture({ failed: ["114800"] });
  degraded.crosschecks.etf["114800"].state = "ok";
  assert.throws(() => assertActualEtfContract(degraded.section, degraded.crosschecks), /status must follow/);
  const wrongReason = contractFixture({ failed: ["114800"] });
  wrongReason.section.pairs["1x"].reason = "official_crosscheck_failed_069500";
  assert.throws(() => assertActualEtfContract(wrongReason.section, wrongReason.crosschecks));
});

test("unavailable actual ETF policies cannot expose returns, trades, curves, positions, or pending orders", () => {
  for (const [field, value] of [
    ["metrics", { totalReturn: 0.1 }], ["trades", [{ side: "long" }]],
    ["actions", [{ type: "enter" }]], ["equity", [{ value: 1.1 }]],
    ["holdingSegments", [{ position: "long" }]], ["position", "long"],
    ["openTrade", { side: "long" }], ["pendingAction", "enter"]
  ]) {
    const { section, crosschecks } = contractFixture({ failed: ["114800"] });
    section.pairs["1x"].policies.long_inverse_cash[field] = value;
    assert.throws(() => assertActualEtfContract(section, crosschecks), `${field} must remain fail-closed`);
  }
  const { section, crosschecks } = contractFixture({ failed: ["114800"] });
  section.pairs["1x"].fullPeriodMetrics.long_cash.metrics = { totalReturn: 0.1 };
  assert.throws(() => assertActualEtfContract(section, crosschecks));
});

test("verified actual ETF policies still require real result metrics and prohibit synthetic shorts", () => {
  for (const corrupt of [
    (policy) => { policy.metrics.totalReturn = null; },
    (policy) => { policy.metrics.shortExposure = 0.5; },
    (policy) => { policy.trades = [{ side: "short" }]; },
    (policy) => { policy.equity = []; }
  ]) {
    const { section, crosschecks } = contractFixture();
    corrupt(section.pairs["1x"].policies.long_inverse_cash);
    assert.throws(() => assertActualEtfContract(section, crosschecks));
  }
});
