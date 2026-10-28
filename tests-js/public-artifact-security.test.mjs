import test from "node:test";
import assert from "node:assert/strict";
import {hasForbiddenPublicContent} from "./helpers/public-artifact-security.mjs";

const reason = "krx_password_change_required";
const summary = {status: {degradedReasons: [reason]}};

test("credential-renewal failure can publish a safe degraded status", () => {
  assert.equal(hasForbiddenPublicContent(JSON.stringify(summary), "summary.json"), false);
  assert.equal(hasForbiddenPublicContent(JSON.stringify({degradedReasons: [reason]}), "automation-status.json"), false);
});

test("the exception is exact and confined to public diagnostic fields", () => {
  for (const payload of [
    {reason}, {password: reason}, {status: {degradedReasons: [`${reason}: private`]}},
    {...summary, note: reason}, {...summary, note: "password-canary"},
    {...summary, token: "FAKE_KRX_SECRET_CANARY"}, {...summary, user: "login-id-canary"},
    {...summary, token: "gho_test"}, {...summary, KRX_PW: "private"}
  ]) assert.equal(hasForbiddenPublicContent(JSON.stringify(payload), "summary.json"), true);
  assert.equal(hasForbiddenPublicContent(JSON.stringify(summary), "dashboard.json"), true);
});

test("escaped strings and duplicate keys cannot conceal credential markers", () => {
  assert.equal(hasForbiddenPublicContent('{"note":"pass\\u0077ord-canary"}', "summary.json"), true);
  assert.equal(hasForbiddenPublicContent('{"note":"password-canary","note":"safe"}', "summary.json"), true);
  assert.equal(hasForbiddenPublicContent('{"note":"pass\\u0077ord-canary","note":"safe"}', "summary.json"), true);
  assert.equal(hasForbiddenPublicContent('{"note":"password-canary"', "summary.json"), true);
});
