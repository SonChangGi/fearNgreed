export const FEAR_PROJECT_ID = "fear-greed";
export const FEAR_INPUT_SCHEMA_VERSION = "fear-greed/control-inputs-v1";
export const FEAR_INPUT_SCHEMA_HASH = "70df5e68d4ecae4ad93fa410ccd74f2a12ee3d2ca0bfcba2ae2074de284c2e61";
export const FEAR_CONFIG_HASH_ALGORITHM = "fear-greed-json-sort-keys-sha256-v1";
export const FEAR_RESULT_CONTRACT = "fear-greed/control-result-v1";

const TERMINAL_FAILURES = new Set(["failed", "cancelled"]);

function strictRecord(value, label) {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    throw new Error(`${label} 응답이 객체가 아닙니다.`);
  }
  return value;
}

function exactHttpsBase(value) {
  if (typeof value !== "string" || value !== value.trim() || !value) {
    throw new Error("Control API 주소를 입력해 주세요.");
  }
  const url = new URL(value);
  if (
    url.protocol !== "https:"
    || !url.hostname
    || url.username
    || url.password
    || url.search
    || url.hash
  ) {
    throw new Error("Control API는 인증정보·query·fragment가 없는 HTTPS 주소여야 합니다.");
  }
  url.pathname = url.pathname.replace(/\/+$/, "");
  return url.toString().replace(/\/$/, "");
}

function bytesToHex(bytes) {
  return [...new Uint8Array(bytes)]
    .map((value) => value.toString(16).padStart(2, "0"))
    .join("");
}

function canonicalValue(value) {
  if (value === null || typeof value === "string" || typeof value === "boolean") return value;
  if (typeof value === "number" && Number.isFinite(value)) return value;
  if (Array.isArray(value)) return value.map(canonicalValue);
  if (value && typeof value === "object") {
    return Object.fromEntries(
      Object.keys(value).sort().map((key) => [key, canonicalValue(value[key])])
    );
  }
  throw new Error("result identity에 strict JSON이 아닌 값이 있습니다.");
}

function canonicalBytes(value) {
  return new TextEncoder().encode(JSON.stringify(canonicalValue(value)));
}

function exactInputs(left, right) {
  return JSON.stringify(canonicalValue(left)) === JSON.stringify(canonicalValue(right));
}

function boundedError(payload, fallback) {
  const message = payload?.error?.message;
  return typeof message === "string" && message.length <= 1000 ? message : fallback;
}

export function sameControlInputs(left, right) {
  return exactInputs(left, right);
}

export function normalizeControlApiBase(value) {
  return exactHttpsBase(value);
}

export class FearControlApiClient {
  #base = null;
  #ownerToken = null;
  #fetch;
  #digest;
  #sleep;

  constructor({
    fetchImpl = globalThis.fetch?.bind(globalThis),
    digestImpl = globalThis.crypto?.subtle?.digest?.bind(globalThis.crypto.subtle),
    sleepImpl = (milliseconds) => new Promise((resolve) => setTimeout(resolve, milliseconds)),
  } = {}) {
    if (typeof fetchImpl !== "function") throw new Error("fetch API를 사용할 수 없습니다.");
    if (typeof digestImpl !== "function") throw new Error("Web Crypto SHA-256을 사용할 수 없습니다.");
    this.#fetch = fetchImpl;
    this.#digest = digestImpl;
    this.#sleep = sleepImpl;
  }

  get connected() {
    return Boolean(this.#base && this.#ownerToken);
  }

  disconnect() {
    this.#base = null;
    this.#ownerToken = null;
  }

  async connect(base, ownerToken) {
    const normalizedBase = exactHttpsBase(base);
    if (typeof ownerToken !== "string" || !ownerToken) {
      throw new Error("Owner token을 입력해 주세요.");
    }
    const response = await this.#fetch(
      `${normalizedBase}/v1/projects/${FEAR_PROJECT_ID}/capabilities`,
      { headers: { Accept: "application/json" }, cache: "no-store" },
    );
    const payload = await response.json().catch(() => null);
    if (!response.ok) throw new Error(boundedError(payload, `Control API 연결 실패 (HTTP ${response.status})`));
    const capabilities = strictRecord(payload, "capabilities");
    if (
      capabilities.projectId !== FEAR_PROJECT_ID
      || capabilities.inputSchemaVersion !== FEAR_INPUT_SCHEMA_VERSION
      || capabilities.inputSchemaHash !== FEAR_INPUT_SCHEMA_HASH
      || capabilities.configHashAlgorithm !== FEAR_CONFIG_HASH_ALGORITHM
      || capabilities.acceptsRuns !== true
    ) {
      throw new Error("Control API의 Fear & Greed 입력 계약이 현재 페이지와 일치하지 않습니다.");
    }
    this.#base = normalizedBase;
    this.#ownerToken = ownerToken;
    return capabilities;
  }

  async run(inputs, { onStatus = () => {}, pollMilliseconds = 2000, timeoutMilliseconds = 1800000 } = {}) {
    if (!this.connected) throw new Error("Control API를 먼저 연결해 주세요.");
    const idempotencyKey = globalThis.crypto?.randomUUID?.()
      || `fear-${Date.now()}-${Math.random().toString(16).slice(2)}`;
    const created = await this.#json(
      `${this.#base}/v1/projects/${FEAR_PROJECT_ID}/runs`,
      {
        method: "POST",
        headers: {
          Accept: "application/json",
          Authorization: `Bearer ${this.#ownerToken}`,
          "Content-Type": "application/json",
          "Idempotency-Key": idempotencyKey,
        },
        body: JSON.stringify({
          inputSchemaVersion: FEAR_INPUT_SCHEMA_VERSION,
          inputs,
          allowFallback: false,
        }),
      },
      "분석 실행 요청에 실패했습니다.",
    );
    const runId = created.runId;
    if (typeof runId !== "string" || !runId) throw new Error("Control API가 runId를 반환하지 않았습니다.");
    const deadline = Date.now() + timeoutMilliseconds;
    let status = created;
    while (status.status !== "published") {
      if (TERMINAL_FAILURES.has(status.status)) {
        throw new Error(status.errorMessage || "Python 분석 실행이 실패했습니다.");
      }
      if (Date.now() >= deadline) throw new Error("Python 분석 실행 확인 시간이 초과되었습니다.");
      onStatus(status);
      await this.#sleep(pollMilliseconds);
      status = await this.#json(
        `${this.#base}/v1/runs/${encodeURIComponent(runId)}`,
        { headers: { Accept: "application/json" }, cache: "no-store" },
        "분석 실행 상태를 확인하지 못했습니다.",
      );
    }
    onStatus(status);
    const result = await this.#json(
      `${this.#base}/v1/runs/${encodeURIComponent(runId)}/result`,
      { headers: { Accept: "application/json" }, cache: "no-store" },
      "검증 결과를 불러오지 못했습니다.",
    );
    return {
      status,
      result,
      artifact: await this.#fetchVerifiedArtifact(result),
    };
  }

  async #json(url, options, fallbackMessage) {
    const response = await this.#fetch(url, options);
    const payload = await response.json().catch(() => null);
    if (!response.ok) throw new Error(boundedError(payload, `${fallbackMessage} (HTTP ${response.status})`));
    return strictRecord(payload, "Control API");
  }

  async #fetchVerifiedArtifact(result) {
    const artifactIdentity = strictRecord(result.artifact, "artifact identity");
    const url = new URL(artifactIdentity.url);
    if (
      url.protocol !== "https:"
      || url.origin !== "https://sonchanggi.github.io"
      || !/^\/fearNgreed\/data\/control-runs\/v1\/[A-Za-z0-9][A-Za-z0-9._-]{7,127}\/[0-9a-f]{64}\.json$/.test(url.pathname)
      || url.search
      || url.hash
    ) {
      throw new Error("검증 결과 artifact 경로가 허용된 Fear & Greed 경로가 아닙니다.");
    }
    const response = await this.#fetch(url.toString(), {
      headers: { Accept: "application/json", "Cache-Control": "no-cache" },
      cache: "no-store",
    });
    if (!response.ok) throw new Error(`검증 artifact를 불러오지 못했습니다. (HTTP ${response.status})`);
    const bytes = await response.arrayBuffer();
    if (bytes.byteLength !== artifactIdentity.byteSize) {
      throw new Error("검증 artifact byte 크기가 Control API identity와 다릅니다.");
    }
    const digest = bytesToHex(await this.#digest("SHA-256", bytes));
    if (digest !== artifactIdentity.sha256) {
      throw new Error("검증 artifact SHA-256이 Control API identity와 다릅니다.");
    }
    const artifact = JSON.parse(new TextDecoder().decode(bytes));
    await this.#validateArtifactBinding(result, artifact, url);
    return artifact;
  }

  async #validateArtifactBinding(result, artifact, artifactUrl) {
    const payload = strictRecord(artifact, "Fear & Greed artifact");
    const identity = strictRecord(payload.resultIdentity, "resultIdentity");
    const keyParts = strictRecord(identity.keyParts, "resultIdentity.keyParts");
    const binding = strictRecord(keyParts.binding, "resultIdentity binding");
    const dataIdentity = strictRecord(keyParts.dataIdentity, "resultIdentity dataIdentity");
    const codeIdentity = strictRecord(keyParts.codeIdentity, "resultIdentity codeIdentity");
    const reproducedResultKey = bytesToHex(
      await this.#digest("SHA-256", canonicalBytes(keyParts))
    );
    if (
      payload.schemaVersion !== 1
      || payload.contract !== FEAR_RESULT_CONTRACT
      || payload.projectId !== FEAR_PROJECT_ID
      || payload.resultKey !== identity.resultKey
      || payload.resultKey !== result.payload?.resultKey
      || payload.resultKey !== reproducedResultKey
      || !artifactUrl.pathname.endsWith(`/${payload.resultKey}.json`)
      || identity.identityVersion !== "fear-greed-result-identity-v1"
      || keyParts.identityVersion !== "fear-greed-result-identity-v1"
      || keyParts.canonicalJsonVersion !== FEAR_CONFIG_HASH_ALGORITHM
      || binding.projectId !== FEAR_PROJECT_ID
      || binding.runId !== result.runId
      || binding.inputSchemaVersion !== result.inputSchemaVersion
      || binding.inputSchemaHash !== result.inputSchemaHash
      || binding.configHashAlgorithm !== result.configHashAlgorithm
      || binding.configHash !== result.configHash
      || binding.effectiveConfigHash !== result.effectiveConfigHash
      || !exactInputs(payload.requestedInputs, result.requestedInputs)
      || !exactInputs(payload.normalizedInputs, result.normalizedInputs)
      || !exactInputs(payload.effectiveInputs, result.effectiveInputs)
      || !exactInputs(payload.data, dataIdentity)
      || !exactInputs(payload.data, result.dataIdentity)
      || codeIdentity.repository !== "SonChangGi/fearNgreed"
      || codeIdentity.methodologyVersion !== "fear-flow-v5"
      || result.codeVersion !== `github:SonChangGi/fearNgreed@${codeIdentity.commitSha}`
      || result.allowFallback !== false
      || result.fallbackUsed !== false
      || (result.ignoredInputs?.length || 0) !== 0
      || (result.fallbacks?.length || 0) !== 0
    ) {
      throw new Error("검증 artifact가 요청·설정·데이터 identity와 결합되지 않았습니다.");
    }
    if (!Array.isArray(payload.signals) || !payload.event || !payload.strategy || !payload.summary) {
      throw new Error("검증 artifact의 신호·사건·전략 결과가 완전하지 않습니다.");
    }
  }
}
