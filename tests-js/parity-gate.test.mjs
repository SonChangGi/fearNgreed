import test from "node:test";
import assert from "node:assert/strict";

import { verifyParity } from "../scripts/verify-parity.mjs";

test("cross-runtime parity gate verifies the Python-bound control contract", async () => {
  const report = await verifyParity();

  assert.deepEqual(report.verified.signalCases, [
    "robust-60-tail5",
    "scaled-60-tail5",
    "raw-60-tail5",
  ]);
  assert.deepEqual(report.verified.strategyCases, [
    "1x-long-cash",
    "1x-long-inverse",
    "2x-long-cash",
    "2x-long-inverse",
  ]);
  assert.equal(report.verified.eventSharedSubset, true);
  assert.equal(report.verified.variableExtremeTail, true);
  assert.equal(report.verified.eventSummaryPythonAuthority, true);
  assert.equal(report.verified.visibleControlContract, true);
  assert.equal(report.backendMigrationAllowed, true);
  assert.deepEqual(report.blockers, []);
  assert.deepEqual(report.observedGaps.variableExtremeTail, []);
  assert.equal(
    report.observedGaps.eventSummaryAuthority.javascript,
    "preview_only_not_result_authority",
  );
});
