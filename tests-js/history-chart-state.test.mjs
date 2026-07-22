import test from "node:test";
import assert from "node:assert/strict";

import { createHistoryChartState, normalizeHistorySeries } from "../assets/history-chart-state.js";

test("history chart active-series state previews without persisting and fails over to a visible series", () => {
  const visible = ["kospi", "long_cash", "long_inverse_cash", "buyhold"];
  const state = createHistoryChartState();

  assert.equal(state.normalize(visible), "long_inverse_cash");
  assert.equal(state.preview("kospi", visible), "kospi");
  assert.equal(state.activeSeries, "long_inverse_cash", "hover or focus preview must not persist");
  assert.equal(state.activate("long_cash", visible), "long_cash");
  assert.equal(state.activeSeries, "long_cash");
  assert.equal(state.activate("missing", visible), "long_cash", "unknown series must not change state");

  assert.equal(state.normalize(["kospi", "long_inverse_cash", "buyhold"]), "long_inverse_cash");
  assert.equal(state.normalize(["kospi", "buyhold"]), "buyhold");
  assert.equal(normalizeHistorySeries("missing", ["kospi"]), "kospi");
});
