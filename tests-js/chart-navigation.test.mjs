import test from "node:test";
import assert from "node:assert/strict";

import {
  clientPointToSvg,
  itemRatioAt,
  nearestItemIndexByRatio,
  svgPointToClient
} from "../assets/chart-navigation.js";
import {
  historyIntervalSnapshot,
  normalizeHistoryRange,
  relativeReturn
} from "../assets/history-chart-state.js";

test("history interval returns use value ratios and normalize reverse drags", () => {
  const rows = [
    { date: "2026-07-01", kospiClose: 100, longCashValue: 1, longShortValue: 1, buyHoldValue: 1 },
    { date: "2026-07-02", kospiClose: 110, longCashValue: 1.1, longShortValue: .8, buyHoldValue: 1.25 },
    { date: "2026-07-03", kospiClose: 99, longCashValue: 1.32, longShortValue: 1, buyHoldValue: 1 }
  ];
  assert.deepEqual(normalizeHistoryRange(rows.length, 2, 1), { startIndex: 1, endIndex: 2, count: 2 });
  assert.ok(Math.abs(relativeReturn(1.1, 1.32) - .2) < 1e-12);
  const forward = historyIntervalSnapshot(rows, 1, 2);
  const reverse = historyIntervalSnapshot(rows, 2, 1);
  assert.deepEqual(reverse, forward);
  assert.ok(Math.abs(forward.returns.kospi - (-.1)) < 1e-12);
  assert.ok(Math.abs(forward.returns.long_cash - .2) < 1e-12);
  assert.ok(Math.abs(forward.returns.long_inverse_cash - .25) < 1e-12);
  assert.ok(Math.abs(forward.returns.buyhold - (-.2)) < 1e-12);
});

test("history interval helpers fail closed for missing or zero start values", () => {
  assert.equal(relativeReturn(0, 1), null);
  assert.equal(relativeReturn(null, 1), null);
  assert.equal(relativeReturn(1, null), null);
  assert.equal(relativeReturn(Number.NaN, 1), null);
  assert.equal(normalizeHistoryRange(0, 0, 1), null);
  const rows = [
    { date: "2026-07-01", kospiClose: 100, longCashValue: null, buyHoldValue: 0 },
    { date: "2026-07-02", kospiClose: 105, longCashValue: 1.1, buyHoldValue: 1.2 }
  ];
  const snapshot = historyIntervalSnapshot(rows, 0, 1, { showLongShort: false });
  assert.ok(Math.abs(snapshot.returns.kospi - .05) < 1e-12);
  assert.equal(snapshot.returns.long_cash, null);
  assert.equal(snapshot.returns.buyhold, null);
  assert.equal("long_inverse_cash" in snapshot.returns, false);
});

test("chart navigation preserves original source positions across missing observations", () => {
  const items = [{ sourceIndex: 0 }, { sourceIndex: 4 }, { sourceIndex: 5 }];
  const ratio = (item) => item.sourceIndex / 5;

  assert.deepEqual(items.map((_, index) => itemRatioAt(items, index, ratio)), [0, 0.8, 1]);
  assert.equal(nearestItemIndexByRatio(items, 0.7, ratio), 1);
  assert.equal(nearestItemIndexByRatio(items, 0.94, ratio), 2);
  assert.equal(nearestItemIndexByRatio(items, 0.1, ratio), 0);
});

test("chart navigation keeps the existing evenly spaced fallback and one-point end alignment", () => {
  const items = [{ id: 1 }, { id: 2 }, { id: 3 }];
  assert.deepEqual(items.map((_, index) => itemRatioAt(items, index)), [0, 0.5, 1]);
  assert.equal(nearestItemIndexByRatio(items, 0.52), 1);
  assert.equal(itemRatioAt([items[0]], 0), 1);
});

function svgFixture({ rect, viewBox, preserveAspectRatio = null, matrix = null }) {
  return {
    viewBox: { baseVal: viewBox },
    getAttribute(name) {
      return name === "preserveAspectRatio" ? preserveAspectRatio : null;
    },
    getBoundingClientRect() {
      return rect;
    },
    getScreenCTM() {
      return matrix;
    }
  };
}

test("SVG coordinates use the screen CTM in both directions", () => {
  const matrix = {
    transformPoint: ({ x, y }) => ({ x: x * 1.5 + 37, y: y * 1.5 + 19 }),
    inverse() {
      return {
        transformPoint: ({ x, y }) => ({ x: (x - 37) / 1.5, y: (y - 19) / 1.5 })
      };
    }
  };
  const svg = svgFixture({
    rect: { left: 0, top: 0, width: 999, height: 999 },
    viewBox: { x: 0, y: 0, width: 600, height: 340 },
    matrix
  });

  assert.deepEqual(svgPointToClient(svg, 68, 290), { x: 139, y: 454 });
  assert.deepEqual(clientPointToSvg(svg, 139, 454), { x: 68, y: 290 });
});

test("SVG fallback preserves edge coordinates through responsive letterboxing", () => {
  const viewBox = { x: 0, y: 0, width: 600, height: 340 };
  for (const rect of [
    { left: 11, top: 17, width: 800, height: 300 },
    { left: 23, top: 29, width: 320, height: 420 }
  ]) {
    const svg = svgFixture({ rect, viewBox });
    for (const source of [{ x: 0, y: 0 }, { x: 68, y: 20 }, { x: 580, y: 290 }, { x: 600, y: 340 }]) {
      const client = svgPointToClient(svg, source.x, source.y);
      const restored = clientPointToSvg(svg, client.x, client.y);
      assert.ok(Math.abs(restored.x - source.x) < 1e-9, `${rect.width}px x mismatch at ${source.x}`);
      assert.ok(Math.abs(restored.y - source.y) < 1e-9, `${rect.width}px y mismatch at ${source.y}`);
    }
  }
});

test("SVG fallback supports explicit non-uniform preserveAspectRatio none", () => {
  const svg = svgFixture({
    rect: { left: 10, top: 20, width: 300, height: 680 },
    viewBox: { x: 0, y: 0, width: 600, height: 340 },
    preserveAspectRatio: "none"
  });
  assert.deepEqual(svgPointToClient(svg, 600, 340), { x: 310, y: 700 });
  assert.deepEqual(clientPointToSvg(svg, 10, 20), { x: 0, y: 0 });
});
