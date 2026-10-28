// Allow only the documented diagnostic in its contracted reason fields.
// Inspect every string token, including values hidden behind duplicate keys.
export function hasForbiddenPublicContent(text, file) {
  let payload;
  try {
    payload = JSON.parse(text);
  } catch (_) {
    return true; // Do not print a parser exception containing the raw payload.
  }
  if (!payload || typeof payload !== "object" || Array.isArray(payload)) return true;
  const reasons = file === "summary.json"
    ? payload.status?.degradedReasons
    : file === "automation-status.json" ? payload.degradedReasons : null;
  const diagnostic = "krx_password_change_required";
  const allowedCount = Array.isArray(reasons)
    ? reasons.filter(reason => reason === diagnostic).length : 0;
  let removed = 0;
  const decoded = text.replace(/"(?:[^"\\]|\\.)*"/g, token => {
    const value = JSON.parse(token);
    if (value === diagnostic && removed++ < allowedCount) return '"credential_update_required"';
    return JSON.stringify(value);
  });
  const forbidden = /KRX_(API_KEY|ID|PW)|gho_|password|FAKE_KRX_SECRET|login-id-canary/i;
  return forbidden.test(decoded);
}
