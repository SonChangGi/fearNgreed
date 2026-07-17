import test from 'node:test';
import assert from 'node:assert/strict';

import {
  DEFAULT_LONG_EXIT_PERCENTILE,
  MAX_LONG_EXIT_PERCENTILE,
  MIN_LONG_EXIT_PERCENTILE,
  normalizeLongExitPercentile,
  runStrategyScenario,
} from '../assets/strategy-engine.js';

const row = ({
  date,
  state = 'neutral',
  percentile = 50,
  open = 100,
  close = open,
  eligible = true,
}) => ({
  date,
  state,
  percentile,
  tradeEligible: eligible,
  scaledState: state,
  scaledPercentile: percentile,
  scaledTradeEligible: eligible,
  rawState: state,
  rawPercentile: percentile,
  rawTradeEligible: eligible,
  disparityTradeEligible: eligible,
  p226490Open: open,
  p226490Close: close,
  p069500Open: open,
  p069500Close: close,
});

test('exit threshold contract accepts only integer percentiles from 50 through 94', () => {
  assert.equal(DEFAULT_LONG_EXIT_PERCENTILE, 80);
  assert.equal(MIN_LONG_EXIT_PERCENTILE, 50);
  assert.equal(MAX_LONG_EXIT_PERCENTILE, 94);
  assert.equal(normalizeLongExitPercentile('80'), 80);
  for (const invalid of [49, 94.5, 95, '', null, Number.NaN]) {
    assert.throws(() => normalizeLongExitPercentile(invalid), /50~94/);
  }
});

test('changing the browser exit input changes only the next-open exit path', () => {
  const historyRows = [
    row({date: '2026-01-01', state: 'extreme_fear', percentile: 2, open: 100}),
    row({date: '2026-01-02', percentile: 10, open: 100}),
    row({date: '2026-01-03', percentile: 80, open: 105}),
    row({date: '2026-01-04', percentile: 85, open: 110}),
  ];
  const common = {historyRows, period: 'full', costBps: 0, policyId: 'long_cash'};
  const exit80 = runStrategyScenario({...common, longExitPercentile: 80});
  const exit90 = runStrategyScenario({...common, longExitPercentile: 90});

  assert.equal(exit80.trades.length, 1);
  assert.equal(exit80.trades[0].entry_date, '2026-01-02');
  assert.equal(exit80.trades[0].exit_date, '2026-01-04');
  assert.equal(exit80.trades[0].reason, 'recovery');
  assert.equal(exit80.position, 'cash');
  assert.equal(exit90.trades.length, 0);
  assert.equal(exit90.position, 'long');
  assert.equal(exit90.openTrade?.entryDate, exit80.trades[0].entry_date);
  assert.equal(exit80.longExitPercentile, 80);
  assert.equal(exit90.longExitPercentile, 90);
});

test('short recovery uses the exact symmetric boundary and falling price is profitable', () => {
  const historyRows = [
    row({date: '2026-02-02', state: 'extreme_greed', percentile: 98, open: 100}),
    row({date: '2026-02-03', percentile: 90, open: 100}),
    row({date: '2026-02-04', percentile: 21, open: 95}),
    row({date: '2026-02-05', percentile: 20, open: 94}),
    row({date: '2026-02-06', percentile: 15, open: 90}),
  ];
  const result = runStrategyScenario({
    historyRows,
    period: 'full',
    costBps: 0,
    policyId: 'long_short_cash',
    longExitPercentile: 80,
  });

  assert.equal(result.shortExitPercentile, 20);
  assert.equal(result.trades.length, 1);
  assert.deepEqual(
    {side: result.trades[0].side, exit: result.trades[0].exit_date, reason: result.trades[0].reason},
    {side: 'short', exit: '2026-02-06', reason: 'recovery'},
  );
  assert.ok(result.trades[0].gross_return > 0);
  assert.ok(result.trades[0].net_return > 0);
  assert.equal(result.metrics.shortTradeCount, 1);
  assert.equal(result.metrics.longTradeCount, 0);
  assert.ok(result.metrics.netExposure < 0);
});

test('opposite extreme reverses long to short at one next open without a cash gap', () => {
  const historyRows = [
    row({date: '2026-03-02', state: 'extreme_fear', percentile: 2, open: 100}),
    row({date: '2026-03-03', state: 'neutral', percentile: 10, open: 100}),
    row({date: '2026-03-04', state: 'extreme_greed', percentile: 98, open: 110}),
    row({date: '2026-03-05', state: 'greed', percentile: 90, open: 108}),
    row({date: '2026-03-06', state: 'greed', percentile: 85, open: 105}),
  ];
  const result = runStrategyScenario({
    historyRows,
    period: 'full',
    costBps: 10,
    policyId: 'long_short_cash',
    longExitPercentile: 80,
  });

  assert.equal(result.trades.length, 1);
  assert.equal(result.trades[0].side, 'long');
  assert.equal(result.trades[0].reason, 'opposite_extreme');
  assert.equal(result.trades[0].exit_date, '2026-03-05');
  assert.equal(result.position, 'short');
  assert.equal(result.openTrade?.entryDate, '2026-03-05');
  assert.equal(result.metrics.reversalCount, 1);
  assert.equal(result.equity.find((item) => item.date === '2026-03-05')?.value > 0, true);
});

test('unsupported short disparity policy and malformed public inputs fail closed', () => {
  const historyRows = [
    row({date: '2026-04-01'}),
    row({date: '2026-04-02'}),
  ];
  assert.throws(
    () => runStrategyScenario({historyRows, period: 'full', policyId: 'long_short_cash', variant: 'disparity'}),
    /숏 진입 규칙/,
  );
  assert.throws(
    () => runStrategyScenario({historyRows: [historyRows[1], historyRows[0]], period: 'full'}),
    /오름차순 고유값/,
  );
});
