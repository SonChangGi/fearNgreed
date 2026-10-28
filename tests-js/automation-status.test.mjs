import {afterEach, test} from "node:test";
import assert from "node:assert/strict";
import {readFile} from "node:fs/promises";
import {bootDashboard, cleanupDashboards} from "./helpers/dashboard-harness.mjs";

afterEach(cleanupDashboards);

test("completed fallback collection stays visibly qualified; failed retries retain last-good status", async () => {
  const summary = JSON.parse(await readFile(new URL("../data/summary.json", import.meta.url), "utf8"));
  const success = new Date(Date.now() - 60_000).toISOString();
  summary.automation = {lastAttemptAt: success, lastSuccessAt: success, state: "degraded"};
  Object.assign(summary.status, {
    state: "degraded", sourceFreshnessPassed: true, expectedDataAsOf: summary.dataAsOf,
    degradedReasons: ["krx_open_api_target_session_lag", "stock_crosscheck_authenticated_pykrx_fallback"]
  });
  const ready = await bootDashboard({dataOverrides: {"summary.json": summary}});
  assert.equal(ready.document.querySelector("#status-badge").textContent, "갱신 완료 · 데이터 주의");
  assert.match(ready.document.querySelector("#status-note").textContent, /인증 KRX 경로로 확인/);
  assert.equal(ready.document.querySelector("#status-badge").className, "badge degraded");
  await cleanupDashboards();

  summary.automation.lastAttemptAt = new Date().toISOString();
  summary.status.sourceFreshnessPassed = false;
  summary.status.degradedReasons.push("krx_password_change_required");
  const failed = await bootDashboard({dataOverrides: {"summary.json": summary}});
  assert.match(failed.document.querySelector("#status-badge").textContent, /최근 갱신 실패/);
  assert.doesNotMatch(failed.document.querySelector("#status-badge").textContent, /갱신 완료/);
  assert.match(failed.document.querySelector("#status-note").textContent, /마지막 정상 데이터.*유지/);
  assert.match(failed.document.querySelector("#status-note").textContent, /KRX 비밀번호 변경 필요/);
});
