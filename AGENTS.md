# fearNgreed local instructions

## Secret handling

- Never ask the user to paste API keys, IDs, passwords, cookies, or session values into chat, prompts, source files, logs, JSON, HTML, test artifacts, or Git history.
- Local KRX credentials are stored in macOS Keychain under service `codex-quant.krx` with accounts `api-key`, `user-id`, and `password`. These names are metadata; never print the stored values.
- A shared local bridge is installed on `PATH` for every `codex-quant` project as `with-krx-keychain`.
- Check presence without revealing values: `with-krx-keychain --check`.
- Run only the KRX data-refresh subprocess with credentials, for example: `with-krx-keychain uv run --frozen python -m fearngreed.refresh`.
- The bridge exposes `KRX_API_KEY`, `KRX_ID`, and `KRX_PW` only to its child process. Never run `env`, `printenv`, shell tracing, HTTP debug logging, or commands that echo these variables through the bridge.
- Python and JavaScript code must read credentials from environment variables and must fail closed when they are absent. Do not add direct Keychain reads to portable application modules.
- GitHub Actions must use repository or environment secrets with the same names. Local Keychain values are never copied to GitHub automatically.
- Disable or redact request headers, cookies, URL parameters, raw exception objects, and pykrx authentication stdout/stderr. Public artifacts may contain derived data and provenance hashes only.
- Tests use fake credentials and must scan logs, JSON, HTML, and artifacts to prove those fake values are absent.

## Private references

- `references/private/` contains local source material including `source.pdf`. Never commit, publish, copy into Pages artifacts, or reproduce proprietary chart images from this directory.
- Build charts from independently collected data and display required source attribution.

## Standard workflow

- Use `uv` with committed `pyproject.toml` and `uv.lock`; prefer `uv run --frozen` once the lock exists.
- Keep provider adapters, calculations, tests, generated JSON, and static Pages code separate.
- Preserve the last known-good derived history on provider failure and publish an explicit degraded/unavailable status instead of fabricated fallback data.
