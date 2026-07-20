import test from "node:test";
import assert from "node:assert/strict";

import { itemRatioAt, nearestItemIndexByRatio } from "../assets/chart-navigation.js";

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
