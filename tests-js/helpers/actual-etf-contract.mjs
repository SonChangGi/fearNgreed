import assert from "node:assert/strict";

const PAIRS = [
  ["1x", "069500", "114800", 1],
  ["2x", "122630", "252670", 2]
];
const POLICIES = ["long_cash", "long_inverse_cash"];

export function assertActualEtfContract(section, crosschecks) {
  assert.equal(section.authority, "canonical_server_verified_actual_etfs");
  assert.equal(section.canonical, true);
  assert.equal(section.calculationSource, "python_verified_actual_etfs");
  assert.equal(section.implementation, "positive_units_in_listed_long_and_inverse_etfs");
  assert.equal(section.oneWayCostBps, 10);
  assert.equal(section.longExitPercentile, 80);
  assert.equal(section.inverseExitPercentile, 20);
  const common = section.commonPeriod;
  assert.equal(common.basis, "four_etf_common_adjusted_price_sessions");
  assert.deepEqual(common.tickers, PAIRS.flatMap(([, longTicker, inverseTicker]) => [longTicker, inverseTicker]));
  assert.ok(["ok", "unavailable"].includes(common.status));
  assert.ok(Number.isInteger(common.sessionCount) && common.sessionCount >= 0);
  if (common.status === "ok") {
    assert.equal(common.reason, null);
    assert.match(common.start, /^\d{4}-\d{2}-\d{2}$/);
    assert.match(common.end, /^\d{4}-\d{2}-\d{2}$/);
    assert.ok(common.start < common.end && common.sessionCount >= 2);
  } else {
    assert.equal(common.reason, "four_etf_common_period_unavailable");
    assert.equal(common.start, null);
    assert.equal(common.end, null);
    assert.ok(common.sessionCount < 2);
  }
  assert.deepEqual(Object.keys(section.pairs).sort(), ["1x", "2x"]);
  for (const [pairId, longTicker, inverseTicker, leverage] of PAIRS) {
    const result = section.pairs[pairId];
    assert.equal(result.pair.pairId, pairId);
    assert.equal(result.pair.longTicker, longTicker);
    assert.equal(result.pair.inverseTicker, inverseTicker);
    assert.equal(result.pair.leverage, leverage);
    assert.equal(result.pair.implementation, "actual_listed_etfs");
    const failedTickers = [longTicker, inverseTicker].filter((ticker) => crosschecks.etf?.[ticker]?.state !== "ok");
    const reason = common.status === "unavailable"
      ? "four_etf_common_period_unavailable"
      : failedTickers.length ? `official_crosscheck_failed_${failedTickers.join("_")}` : null;
    assert.equal(result.status, reason ? "unavailable" : "ok", `${pairId} status must follow common coverage and official crosschecks`);
    assert.equal(result.reason, reason);
    assert.deepEqual(Object.keys(result.policies).sort(), POLICIES);
    assert.deepEqual(Object.keys(result.fullPeriodMetrics).sort(), POLICIES);
    for (const policyId of POLICIES) {
      const policy = result.policies[policyId];
      assert.deepEqual(policy.pair, result.pair);
      assert.equal(policy.policyId, policyId);
      assert.equal(policy.calculationSource, "python_verified_actual_etfs");
      assert.equal(policy.oneWayCostBps, 10);
      assert.equal(policy.longExitPercentile, 80);
      assert.equal(policy.inverseExitPercentile, 20);
      assert.equal(policy.status, result.status);
      assert.equal(policy.unavailableReason, reason);
      const full = result.fullPeriodMetrics[policyId];
      assert.equal(full.status, result.status);
      assert.equal(full.unavailableReason, reason);
      if (reason) {
        assert.equal(policy.position, "unavailable");
        assert.equal(policy.latestPosition, "unavailable");
        assert.equal(policy.openPosition, false);
        for (const field of ["metrics", "latestInstrumentTicker", "openTrade", "pendingAction", "pendingReason", "pendingSide", "pendingSignalDate", "range"]) {
          assert.equal(policy[field], null, `${pairId}/${policyId} unavailable ${field} must not imply a valid result`);
        }
        for (const field of ["trades", "actions", "equity", "holdingSegments"]) assert.deepEqual(policy[field], []);
        assert.equal(full.position, "unavailable");
        for (const field of ["latestInstrumentTicker", "metrics", "range"]) assert.equal(full[field], null);
      } else {
        assert.equal(policy.metrics.start, common.start);
        assert.equal(policy.metrics.end, common.end);
        assert.ok(Number.isFinite(policy.metrics.totalReturn));
        assert.equal(policy.metrics.shortExposure, 0);
        assert.ok(["long", "inverse", "cash"].includes(policy.position));
        assert.ok(policy.trades.every((trade) => ["long", "inverse"].includes(trade.side)));
        assert.ok(policy.equity.length >= 2);
        assert.equal(policy.equity[0].date, common.start);
        assert.equal(policy.equity.at(-1).date, common.end);
        assert.ok(Number.isFinite(full.metrics.totalReturn));
      }
    }
  }
}
