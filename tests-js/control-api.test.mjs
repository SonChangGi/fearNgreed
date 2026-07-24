import test from "node:test";
import assert from "node:assert/strict";
import { createHash, webcrypto } from "node:crypto";
import { readFile } from "node:fs/promises";

import {
  FEAR_CONFIG_HASH_ALGORITHM,
  FEAR_INPUT_SCHEMA_HASH,
  FEAR_INPUT_SCHEMA_VERSION,
  FEAR_PROJECT_ID,
  FEAR_RESULT_CONTRACT,
  FearControlApiClient,
  normalizeControlApiBase,
  sameControlInputs
} from "../assets/control-api.js";

const INPUTS = {
  window: "ytd",
  historyStart: "",
  historyEnd: "",
  historyEndMode: "latest",
  model: "raw",
  eventAsset: "KOSPI",
  eventSample: "all",
  backtestProxy: "1x",
  backtestPolicy: "compare",
  backtestVariant: "raw_ols",
  backtestCost: 10,
  backtestPeriod: "common",
  longExitPercentile: 80,
  signalLookback: 196,
  signalMinimumR2: 0.4,
  signalExtremeTail: 2,
  signalMaxHolding: 20
};

function jsonResponse(value, status = 200) {
  return new Response(JSON.stringify(value), {
    status,
    headers: { "Content-Type": "application/json" }
  });
}

function canonicalJson(value) {
  if (Array.isArray(value)) return `[${value.map(canonicalJson).join(",")}]`;
  if (value && typeof value === "object") {
    return `{${Object.keys(value).sort().map((key) => `${JSON.stringify(key)}:${canonicalJson(value[key])}`).join(",")}}`;
  }
  return JSON.stringify(value);
}

function fixture() {
  const runId = "run-fear-00000001";
  const configHash = "2".repeat(64);
  const data = {
    source: "fearngreed-public-history-v1",
    sourceHash: "3".repeat(64),
    dataAsOf: "2026-07-22"
  };
  const binding = {
    projectId: FEAR_PROJECT_ID,
    runId,
    inputSchemaVersion: FEAR_INPUT_SCHEMA_VERSION,
    inputSchemaHash: FEAR_INPUT_SCHEMA_HASH,
    configHashAlgorithm: FEAR_CONFIG_HASH_ALGORITHM,
    configHash,
    effectiveConfigHash: configHash
  };
  const keyParts = {
    identityVersion: "fear-greed-result-identity-v1",
    canonicalJsonVersion: FEAR_CONFIG_HASH_ALGORITHM,
    binding,
    dataIdentity: data,
    codeIdentity: {
      repository: "SonChangGi/fearNgreed",
      commitSha: "4".repeat(40),
      methodologyVersion: "fear-flow-v5"
    }
  };
  const resultKey = createHash("sha256").update(canonicalJson(keyParts)).digest("hex");
  const artifact = {
    schemaVersion: 1,
    contract: FEAR_RESULT_CONTRACT,
    projectId: FEAR_PROJECT_ID,
    resultKey,
    resultIdentity: {
      identityVersion: "fear-greed-result-identity-v1",
      resultKey,
      keyParts
    },
    requestedInputs: INPUTS,
    normalizedInputs: INPUTS,
    effectiveInputs: INPUTS,
    data,
    calculatedAt: "2026-07-24T00:00:00Z",
    signals: [],
    event: {},
    strategy: {},
    summary: {}
  };
  const bytes = new TextEncoder().encode(JSON.stringify(artifact));
  const sha256 = createHash("sha256").update(bytes).digest("hex");
  const result = {
    runId,
    inputSchemaVersion: FEAR_INPUT_SCHEMA_VERSION,
    inputSchemaHash: FEAR_INPUT_SCHEMA_HASH,
    configHashAlgorithm: FEAR_CONFIG_HASH_ALGORITHM,
    configHash,
    effectiveConfigHash: configHash,
    requestedInputs: INPUTS,
    normalizedInputs: INPUTS,
    effectiveInputs: INPUTS,
    allowFallback: false,
    fallbackUsed: false,
    ignoredInputs: [],
    fallbacks: [],
    codeVersion: `github:SonChangGi/fearNgreed@${"4".repeat(40)}`,
    dataIdentity: data,
    artifact: {
      url: `https://sonchanggi.github.io/fearNgreed/data/control-runs/v1/${runId}/${resultKey}.json`,
      sha256,
      byteSize: bytes.byteLength,
      contractVersion: FEAR_RESULT_CONTRACT
    },
    payload: { resultKey }
  };
  return { artifact, bytes, result, runId };
}

test("Control API base accepts only a clean HTTPS base", () => {
  assert.equal(normalizeControlApiBase("https://api.example.com/"), "https://api.example.com");
  for (const value of [
    "http://api.example.com",
    "https://user:pass@api.example.com",
    "https://api.example.com?token=x",
    " https://api.example.com"
  ]) {
    assert.throws(() => normalizeControlApiBase(value));
  }
  assert.equal(
    sameControlInputs(INPUTS, Object.fromEntries(Object.entries(INPUTS).reverse())),
    true
  );
});

test("client binds the exact request, result identity, SHA-256, and artifact bytes", async () => {
  const { artifact, bytes, result, runId } = fixture();
  const requests = [];
  const fetchImpl = async (url, options = {}) => {
    requests.push({ url: String(url), options });
    if (String(url).endsWith("/capabilities")) {
      return jsonResponse({
        projectId: FEAR_PROJECT_ID,
        inputSchemaVersion: FEAR_INPUT_SCHEMA_VERSION,
        inputSchemaHash: FEAR_INPUT_SCHEMA_HASH,
        configHashAlgorithm: FEAR_CONFIG_HASH_ALGORITHM,
        acceptsRuns: true
      });
    }
    if (String(url).endsWith("/runs") && options.method === "POST") {
      return jsonResponse({ runId, status: "published" }, 202);
    }
    if (String(url).endsWith("/result")) return jsonResponse(result);
    if (String(url).startsWith("https://sonchanggi.github.io/")) {
      return new Response(bytes, { status: 200 });
    }
    throw new Error(`unexpected request ${url}`);
  };
  const client = new FearControlApiClient({
    fetchImpl,
    digestImpl: webcrypto.subtle.digest.bind(webcrypto.subtle),
    sleepImpl: async () => {}
  });
  await client.connect("https://control.example.com", "memory-only-owner-token");
  const completed = await client.run(INPUTS);

  assert.deepEqual(completed.artifact, artifact);
  assert.equal(requests[0].options.headers.Authorization, undefined);
  assert.equal(requests[1].options.headers.Authorization, "Bearer memory-only-owner-token");
  assert.equal(requests[2].options.headers.Authorization, undefined);
  assert.equal(requests[3].options.headers.Authorization, undefined);
  assert.equal(JSON.parse(requests[1].options.body).allowFallback, false);
  assert.equal(sameControlInputs(completed.artifact.effectiveInputs, INPUTS), true);
});

test("client fails closed on artifact byte-identity mismatch", async () => {
  const { result, runId } = fixture();
  result.artifact.sha256 = "0".repeat(64);
  const fetchImpl = async (url, options = {}) => {
    if (String(url).endsWith("/capabilities")) {
      return jsonResponse({
        projectId: FEAR_PROJECT_ID,
        inputSchemaVersion: FEAR_INPUT_SCHEMA_VERSION,
        inputSchemaHash: FEAR_INPUT_SCHEMA_HASH,
        configHashAlgorithm: FEAR_CONFIG_HASH_ALGORITHM,
        acceptsRuns: true
      });
    }
    if (String(url).endsWith("/runs") && options.method === "POST") {
      return jsonResponse({ runId, status: "published" }, 202);
    }
    if (String(url).endsWith("/result")) return jsonResponse(result);
    return new Response("{}", { status: 200 });
  };
  const client = new FearControlApiClient({
    fetchImpl,
    digestImpl: webcrypto.subtle.digest.bind(webcrypto.subtle),
    sleepImpl: async () => {}
  });
  await client.connect("https://control.example.com", "token");
  await assert.rejects(() => client.run(INPUTS), /SHA-256|byte 크기/);
});

test("owner token has no localStorage persistence path", async () => {
  const source = await readFile(new URL("../assets/app.js", import.meta.url), "utf8");
  assert.doesNotMatch(source, /localStorage\.(?:setItem|getItem)\([^)]*token/i);
  assert.match(source, /tokenInput\.value = ""/);
  assert.match(source, /기존 결과는 유지됩니다/);
});
