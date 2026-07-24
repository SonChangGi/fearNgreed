import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { readFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { dirname, join, resolve } from "node:path";

import {
  DEFAULT_SIGNAL_CONFIG,
  classifyDynamicPercentile,
  computeDynamicSignals,
  runDynamicEventStudy,
} from "../assets/signal-engine.js";
import { runActualEtfPairScenario } from "../assets/strategy-engine.js";

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const FIXTURE_PATH = join(ROOT, "tests", "fixtures", "fear-parity-v1.json");
const MATRIX_PATH = join(ROOT, "contracts", "fear-parity-matrix.v1.json");
const TOLERANCE = 1e-10;

function pythonReference() {
  const command = spawnSync(
    "uv",
    [
      "run",
      "--frozen",
      "python",
      "scripts/fear_parity_reference.py",
      "--fixture",
      "tests/fixtures/fear-parity-v1.json",
    ],
    { cwd: ROOT, encoding: "utf8" },
  );
  if (command.status !== 0) {
    throw new Error(
      `Python parity reference failed (${command.status ?? "signal"}): ${command.stderr || command.stdout}`,
    );
  }
  return JSON.parse(command.stdout);
}

function closeNumber(actual, expected, path, tolerance = TOLERANCE) {
  if (actual == null || expected == null) {
    assert.equal(actual, expected, path);
    return;
  }
  assert.equal(typeof actual, "number", `${path}: actual must be numeric`);
  assert.equal(typeof expected, "number", `${path}: expected must be numeric`);
  const difference = Math.abs(actual - expected);
  assert.ok(
    difference <= tolerance,
    `${path}: ${actual} differs from ${expected} by ${difference} (tolerance ${tolerance})`,
  );
}

function compareValue(actual, expected, path, tolerance = TOLERANCE) {
  if (typeof expected === "number") {
    closeNumber(actual, expected, path, tolerance);
    return;
  }
  if (Array.isArray(expected)) {
    assert.ok(Array.isArray(actual), `${path}: actual must be an array`);
    assert.equal(actual.length, expected.length, `${path}: array length`);
    expected.forEach((value, index) =>
      compareValue(actual[index], value, `${path}[${index}]`, tolerance));
    return;
  }
  if (expected && typeof expected === "object") {
    assert.ok(actual && typeof actual === "object" && !Array.isArray(actual), `${path}: actual must be an object`);
    assert.deepEqual(Object.keys(actual).sort(), Object.keys(expected).sort(), `${path}: keys`);
    Object.entries(expected).forEach(([key, value]) =>
      compareValue(actual[key], value, `${path}.${key}`, tolerance));
    return;
  }
  assert.equal(actual, expected, path);
}

function jsSignal(signal) {
  return {
    date: signal.date,
    alpha: signal.alpha,
    beta: signal.beta,
    rollingR2: signal.rollingR2,
    fitScore: signal.fitScore,
    expected: signal.expected,
    residual: signal.residual,
    residualZ: signal.residualZ,
    percentile: signal.percentile,
    state: signal.state,
    quality: signal.quality,
    trainingCount: signal.trainingCount,
    tradeEligible: signal.tradeEligible,
    fitMethod: signal.fitMethod,
  };
}

function historyStrategyRows(rows) {
  return rows.map((row) => ({
    date: row.date,
    state: row.state,
    percentile: row.percentile,
    tradeEligible: row.eligible,
    scaledState: row.state,
    scaledPercentile: row.percentile,
    scaledTradeEligible: row.eligible,
    rawState: row.state,
    rawPercentile: row.percentile,
    rawTradeEligible: row.eligible,
    kospiClose: row.kospiClose,
    p069500Open: row.long1Open,
    p069500Close: row.long1Close,
    p114800Open: row.inverse1Open,
    p114800Close: row.inverse1Close,
    p122630Open: row.long2Open,
    p122630Close: row.long2Close,
    p252670Open: row.inverse2Open,
    p252670Close: row.inverse2Close,
  }));
}

const STRATEGY_METRIC_FIELDS = [
  "start",
  "end",
  "totalReturn",
  "cagr",
  "volatility",
  "sharpe",
  "maxDrawdown",
  "winRate",
  "longExposure",
  "inverseExposure",
  "shortExposure",
  "cashExposure",
  "grossExposure",
  "netExposure",
  "turnover",
  "transactionCostTotal",
  "tradeCount",
  "longTradeCount",
  "inverseTradeCount",
];

const STRATEGY_TRADE_FIELDS = [
  "side",
  "instrument_ticker",
  "entry_date",
  "exit_date",
  "entry_signal_date",
  "exit_signal_date",
  "reason",
  "entry_price",
  "exit_price",
  "holding_sessions",
  "gross_return",
  "transaction_cost",
  "net_return",
];

const STRATEGY_ACTION_FIELDS = [
  "actionId",
  "signalDate",
  "executionDate",
  "type",
  "fromPosition",
  "toPosition",
  "fromTicker",
  "toTicker",
  "fromPrice",
  "toPrice",
  "reason",
  "transactionCostAmount",
  "transactionSides",
];

function pick(object, fields) {
  return Object.fromEntries(fields.map((field) => [field, object?.[field] ?? null]));
}

function normalizeJsStrategy(result) {
  return {
    policyId: result.policyId,
    position: result.position,
    pendingAction: result.pendingAction,
    pendingReason: result.pendingReason,
    pendingSide: result.pendingSide,
    pendingSignalDate: result.pendingSignalDate,
    longExitPercentile: result.longExitPercentile,
    inverseExitPercentile: result.inverseExitPercentile,
    metrics: pick(result.metrics, STRATEGY_METRIC_FIELDS),
    trades: result.trades.map((trade) => ({
      ...pick(trade, STRATEGY_TRADE_FIELDS),
      instrument_ticker: trade.instrumentTicker ?? null,
    })),
    actions: result.actions.map((action) => pick(action, STRATEGY_ACTION_FIELDS)),
    equity: result.equity.map(({ date, value }) => ({ date, value })),
  };
}

function normalizeEventRows(events, horizons) {
  return events.map((event) => ({
    date: event.date,
    state: event.state,
    percentile: event.percentile,
    ...Object.fromEntries(horizons.map((horizon) => [
      `return${horizon}d`,
      event[`return${horizon}d`],
    ])),
  }));
}

function verifyMatrix(matrix) {
  assert.equal(matrix.contract, "fearngreed-engine-parity-matrix");
  assert.equal(matrix.projectId, "fearngreed");
  const pathIds = new Set(matrix.paths.map((path) => path.id));
  assert.equal(pathIds.size, matrix.paths.length, "parity path ids must be unique");
  assert.deepEqual(
    [...matrix.migrationGate.requiredPathIds].sort(),
    [...pathIds].sort(),
    "migration gate must cover every audited calculation path",
  );
  const actualBlocked = matrix.paths.filter((path) => path.status === "blocked").map((path) => path.id).sort();
  assert.deepEqual(
    [...matrix.migrationGate.blockingPathIds].sort(),
    actualBlocked,
    "blockingPathIds must exactly match blocked paths",
  );
  assert.equal(matrix.migrationGate.backendMigrationAllowed, actualBlocked.length === 0);
  assert.ok(matrix.visibleControls.some((control) => control.id === "signal-extreme-tail" && control.kind === "analysis"));
  assert.ok(matrix.visibleControls.every((control) => ["display", "analysis", "operation"].includes(control.kind)));
}

function verifySourceDeclarations(matrix, appSource, pipelineSource) {
  const pageDefaults = matrix.defaults.visibleBrowserScenario;
  const defaultBlock = appSource.match(/const DEFAULT_CONTROLS = Object\.freeze\(\{(?<body>[\s\S]*?)\}\);/)?.groups?.body;
  assert.ok(defaultBlock, "app.js DEFAULT_CONTROLS must remain inspectable");
  const expectedProperties = {
    window: `"${pageDefaults.window}"`,
    historyEndMode: `"${pageDefaults.historyEndMode}"`,
    model: `"${pageDefaults.model}"`,
    eventAsset: `"${pageDefaults.eventAsset}"`,
    eventSample: `"${pageDefaults.eventSample}"`,
    backtestProxy: `"${pageDefaults.pairId}"`,
    backtestPolicy: `"${pageDefaults.policy}"`,
    backtestVariant: `"${pageDefaults.strategyVariant}"`,
    backtestCost: String(pageDefaults.costBps),
    backtestPeriod: `"${pageDefaults.priceSample}"`,
    signalLookback: String(pageDefaults.lookback),
    signalMinimumR2: String(pageDefaults.minimumR2),
    signalExtremeTail: String(pageDefaults.extremeTail),
    signalMaxHolding: String(pageDefaults.maxHolding),
  };
  Object.entries(expectedProperties).forEach(([field, value]) => {
    assert.match(defaultBlock, new RegExp(`\\b${field}:\\s*${value.replace(".", "\\.")}(?:,|\\s*$)`), `app.js ${field} default`);
  });
  assert.match(defaultBlock, /\blongExitPercentile:\s*DEFAULT_LONG_EXIT_PERCENTILE\b/);

  const libraryDefaults = matrix.defaults.javascriptLibraryWhenCalledWithoutPageOverrides;
  assert.deepEqual(DEFAULT_SIGNAL_CONFIG, {
    track: libraryDefaults.track,
    lookback: libraryDefaults.lookback,
    minimumR2: libraryDefaults.minimumR2,
    extremeTail: libraryDefaults.extremeTail,
  });
  assert.match(
    pipelineSource,
    /robust_signals\s*=\s*channel_signals\([\s\S]*?fit_method="huber"\)/,
    "Python canonical pipeline must keep robust Huber as the primary path",
  );
  assert.match(
    pipelineSource,
    /summarize_event_returns\([\s\S]*?bootstrap_method="moving_block"/,
    "Python canonical event summary must keep the declared moving-block contract",
  );
  assert.match(
    appSource,
    /calculationSource:\s*"browser_past_only_refit"/,
    "browser-refit results must retain a non-canonical authority marker",
  );
}

export async function verifyParity() {
  const [fixture, matrix, appSource, pipelineSource] = await Promise.all([
    readFile(FIXTURE_PATH, "utf8").then(JSON.parse),
    readFile(MATRIX_PATH, "utf8").then(JSON.parse),
    readFile(join(ROOT, "assets", "app.js"), "utf8"),
    readFile(join(ROOT, "src", "fearngreed", "pipeline.py"), "utf8"),
  ]);
  verifyMatrix(matrix);
  verifySourceDeclarations(matrix, appSource, pipelineSource);
  const reference = pythonReference();
  assert.equal(reference.fixtureId, fixture.fixtureId);
  const signalRows = reference.expandedInputs.signalRows;
  const signalChecks = [];
  for (const expected of reference.signals) {
    const config = fixture.signalParityConfigs.find((candidate) => candidate.caseId === expected.caseId);
    assert.ok(config, `${expected.caseId}: missing fixture config`);
    const actual = computeDynamicSignals({ historyRows: signalRows, ...config });
    const actualSignals = actual.signals.map(jsSignal);
    compareValue(actualSignals, expected.signals, `signals.${expected.caseId}`);
    signalChecks.push(expected.caseId);
  }

  const classification = reference.classification.map((expected) => ({
    ...expected,
    browserState: classifyDynamicPercentile(expected.percentile, expected.browserExtremeTail),
  }));
  const observedClassificationGaps = classification.filter(
    ({ browserState, pythonState }) => browserState !== pythonState,
  );
  assert.deepEqual(
    observedClassificationGaps,
    [],
    "the Python control contract and browser must use the same configurable-tail states",
  );

  const strategyRows = historyStrategyRows(reference.expandedInputs.strategyRows);
  const strategyChecks = [];
  for (const expected of reference.strategies) {
    const config = fixture.strategyConfigs.find((candidate) => candidate.caseId === expected.caseId);
    assert.ok(config, `${expected.caseId}: missing fixture config`);
    const actual = runActualEtfPairScenario({
      history: strategyRows,
      pairId: config.pairId,
      policy: config.policyId,
      variant: "scaled_huber",
      period: "pair",
      costBps: config.costBps,
      exitPercentile: config.longExitPercentile,
      maxHoldDays: config.maxHolding,
    });
    compareValue(normalizeJsStrategy(actual), expected.result, `strategies.${expected.caseId}`);
    strategyChecks.push(expected.caseId);
  }

  const eventConfig = fixture.eventConfig;
  const actualEvents = runDynamicEventStudy({
    historyRows: strategyRows,
    ...eventConfig,
    bootstrapSamples: 32,
    seed: 7,
  });
  compareValue(
    normalizeEventRows(actualEvents.events, eventConfig.horizons),
    reference.events.events,
    "events.sharedSubset",
    1e-12,
  );
  assert.equal(
    reference.events.summaryAuthority,
    "python_numpy_moving_block_with_unconditional_benchmark",
  );
  assert.ok(
    reference.events.summary.every(
      (row) => row.bootstrapMethod === "moving_block"
        && Object.hasOwn(row, "benchmarkMean")
        && Object.hasOwn(row, "meanExcessReturnCi95"),
    ),
    "event summaries must be emitted only by the canonical Python authority",
  );

  const controlDefaults = {
    window: matrix.defaults.visibleBrowserScenario.window,
    historyStart: "",
    historyEnd: "",
    historyEndMode: matrix.defaults.visibleBrowserScenario.historyEndMode,
    model: matrix.defaults.visibleBrowserScenario.model,
    eventAsset: matrix.defaults.visibleBrowserScenario.eventAsset,
    eventSample: matrix.defaults.visibleBrowserScenario.eventSample,
    backtestProxy: matrix.defaults.visibleBrowserScenario.pairId,
    backtestPolicy: matrix.defaults.visibleBrowserScenario.policy,
    backtestVariant: matrix.defaults.visibleBrowserScenario.strategyVariant,
    backtestCost: matrix.defaults.visibleBrowserScenario.costBps,
    backtestPeriod: matrix.defaults.visibleBrowserScenario.priceSample,
    longExitPercentile: matrix.defaults.visibleBrowserScenario.longExitPercentile,
    signalLookback: matrix.defaults.visibleBrowserScenario.lookback,
    signalMinimumR2: matrix.defaults.visibleBrowserScenario.minimumR2,
    signalExtremeTail: matrix.defaults.visibleBrowserScenario.extremeTail,
    signalMaxHolding: matrix.defaults.visibleBrowserScenario.maxHolding,
  };
  assert.deepEqual(reference.controlContract.requested, controlDefaults);
  assert.deepEqual(reference.controlContract.normalized, controlDefaults);
  assert.deepEqual(reference.controlContract.effective, controlDefaults);
  assert.match(reference.controlContract.inputSchemaHash, /^[0-9a-f]{64}$/);
  assert.match(reference.controlContract.configHash, /^[0-9a-f]{64}$/);
  assert.equal(reference.controlContract.resultAuthority, "python_control_contract");

  const blockers = matrix.paths
    .filter((path) => path.status === "blocked")
    .map(({ id, blocker }) => ({ id, blocker }));
  return {
    schemaVersion: 1,
    contract: "fearngreed-parity-gate-result",
    fixtureId: fixture.fixtureId,
    verified: {
      signalCases: signalChecks,
      strategyCases: strategyChecks,
      eventSharedSubset: true,
      variableExtremeTail: true,
      eventSummaryPythonAuthority: true,
      visibleControlContract: true,
    },
    observedGaps: {
      variableExtremeTail: observedClassificationGaps,
      eventSummaryAuthority: {
        javascript: "preview_only_not_result_authority",
        python: reference.events.summaryAuthority,
      },
    },
    backendMigrationAllowed: matrix.migrationGate.backendMigrationAllowed,
    blockers,
  };
}

async function main() {
  const report = await verifyParity();
  const asJson = process.argv.includes("--json");
  if (asJson) {
    process.stdout.write(`${JSON.stringify(report)}\n`);
  } else {
    console.log(`Fear & Greed parity fixture: ${report.fixtureId}`);
    console.log(`Verified signal cases: ${report.verified.signalCases.length}`);
    console.log(`Verified strategy cases: ${report.verified.strategyCases.length}`);
    console.log(`Verified event subset: ${report.verified.eventSharedSubset}`);
    console.log(`Backend migration allowed: ${report.backendMigrationAllowed}`);
    report.blockers.forEach(({ id }) => console.log(`BLOCKED ${id}`));
  }
  if (process.argv.includes("--require-ready") && !report.backendMigrationAllowed) {
    process.exitCode = 2;
  }
}

if (fileURLToPath(import.meta.url) === resolve(process.argv[1] || "")) {
  main().catch((error) => {
    console.error(error instanceof Error ? error.stack : error);
    process.exitCode = 1;
  });
}
