from __future__ import annotations

import json
import shutil
from datetime import date, timedelta
from pathlib import Path

import fearngreed.refresh as refresh_module
from fearngreed.verify import verify_local

ROOT = Path(__file__).resolve().parents[1]


def test_refresh_workflow_publishes_only_status_after_provider_failure() -> None:
    workflow = (ROOT / ".github" / "workflows" / "refresh.yml").read_text(encoding="utf-8")

    for schedule in (
        'cron: "15 9 * * 1-5"',
        'cron: "45 9 * * 1-5"',
        'cron: "30 11 * * 1-5"',
    ):
        assert schedule in workflow
    assert "TZ=Asia/Seoul date +%F" in workflow
    assert "EVENT_SCHEDULE" in workflow
    assert "failure_policy=preserve" in workflow
    assert "args+=(--require-end-session)" in workflow
    assert "args+=(--skip-if-current)" in workflow
    assert "provider_probe_date" in workflow
    assert "official_data_date" in workflow
    assert "PROVIDER_PROBE_DATE" in workflow
    assert "OFFICIAL_DATA_DATE" in workflow
    assert '--probe --date "$PROVIDER_PROBE_DATE"' in workflow
    assert '[[ -z "$BACKFILL_START_DATE" ]]' in workflow
    assert '[[ -z "$OFFICIAL_DATA_DATE" ]]' in workflow
    assert "outcome=probe_success" in workflow
    assert "Enforce provider probe no-write boundary" in workflow
    assert "git status --porcelain --untracked-files=all" in workflow
    assert "Report provider credential probe" in workflow
    assert 'current_kst_time="$(TZ=Asia/Seoul date +%H%M)"' in workflow
    assert "10#$current_kst_time < 1815" in workflow
    assert "Manual final-data refresh is available from 18:15 KST" in workflow
    assert '[[ "$OFFICIAL_DATA_DATE" < "$current_kst_date" ]]' in workflow
    assert 'target_date="${OFFICIAL_DATA_DATE:-$current_kst_date}"' in workflow
    assert 'elif [[ -z "$OFFICIAL_DATA_DATE" ]]; then' in workflow
    official_block = workflow.split('if [[ -n "$OFFICIAL_DATA_DATE" ]]; then', 1)[1].split("fi", 1)[
        0
    ]
    assert '[[ -z "$BACKFILL_START_DATE" ]]' not in official_block
    assert 'args+=(--failure-policy "$failure_policy")' in workflow
    assert "outcome=retry_pending" in workflow
    assert "outcome=skipped" in workflow
    assert "outcome=failure" in workflow
    assert "ref: main" in workflow
    before_refresh = workflow.split("- name: Refresh derived data", 1)[0]
    refresh_step = workflow.split("- name: Refresh derived data", 1)[1].split(
        "- name: Enforce failed-refresh mutation boundary", 1
    )[0]
    after_refresh = workflow.split("- name: Enforce failed-refresh mutation boundary", 1)[1]
    assert "KRX_API_KEY" not in before_refresh
    for secret_name in ("KRX_API_KEY", "KRX_ID", "KRX_PW"):
        assert secret_name in refresh_step
        assert secret_name not in after_refresh
        assert f"missing_secrets+=({secret_name})" in refresh_step
    secret_error_line = next(
        line for line in refresh_step.splitlines() if "Missing required secret names" in line
    )
    assert "${missing_names%, }" in secret_error_line
    assert "$KRX_API_KEY" not in secret_error_line
    assert "$KRX_ID" not in secret_error_line
    assert "$KRX_PW" not in secret_error_line
    assert "::error title=Missing required Actions secrets" in secret_error_line
    missing_secret_block = refresh_step.split("if (( ${#missing_secrets[@]} )); then", 1)[1].split(
        "fi", 1
    )[0]
    assert "exit 1" in missing_secret_block
    assert "scan_public_files" in refresh_step
    assert 'refresh_receipt_file="$RUNNER_TEMP/fearngreed-refresh-receipt.json"' in refresh_step
    assert '> "$refresh_receipt_file"' in refresh_step
    assert "receipt['expectedDataAsOf']" in refresh_step
    assert "receipt.get('skipped') is True" in refresh_step
    assert "json.loads(Path(sys.argv[1]).read_text" in refresh_step
    assert "json.load(open('data/summary.json'" not in refresh_step
    assert "expectedDataAsOf" in refresh_step
    assert refresh_step.count('cat "$refresh_receipt_file"') == 2
    assert 'echo "dataAsOf=$data_as_of" >> "$GITHUB_OUTPUT"' in refresh_step
    assert "Enforce early-retry preservation boundary" in workflow
    retry_section = workflow.split("- name: Enforce early-retry preservation boundary", 1)[1]
    retry_boundary = retry_section.split("- name: Enforce failed-refresh mutation boundary", 1)[0]
    assert "git diff --quiet -- data" in retry_boundary
    assert "Enforce failed-refresh mutation boundary" in workflow
    assert "data/summary.json|data/automation-status.json" in workflow
    assert "data: publish degraded refresh status" in workflow
    assert workflow.index("uv run --frozen pytest") < workflow.index("Commit validated derivatives")
    assert workflow.index("python -m fearngreed.site") < workflow.index(
        "Commit validated derivatives"
    )
    assert "pages: write" in workflow
    assert "id-token: write" in workflow
    assert "actions/deploy-pages@v4" in workflow
    assert workflow.count("--expected-data-as-of") == 2
    assert workflow.count("steps.refresh.outputs.dataAsOf") == 2
    assert 'verify_args=(--base-url "${{ steps.deployment.outputs.page_url }}")' in workflow
    assert 'if [[ "${{ steps.refresh.outputs.outcome }}" == "success" ]]; then' in workflow
    assert workflow.index("Commit validated derivatives") < workflow.index(
        "Deploy validated derivatives"
    )
    assert "git fetch origin main" in workflow
    assert "git rev-parse origin/main" in workflow
    assert "git push origin HEAD:main" in workflow
    assert "git rebase" not in workflow
    assert "git push --force" not in workflow
    assert workflow.index("Verify live public derivative hashes") < workflow.index(
        "Report provider refresh failure"
    )
    assert "Report early retry pending" in workflow
    assert "Report already-current session" in workflow
    assert "timeout-minutes: 120" in workflow
    assert "timeout --signal=TERM --kill-after=30s 35m" in workflow
    assert "mark_failed('refresh_timeout')" in workflow


def test_pages_workflow_runs_local_and_live_contract_verification() -> None:
    workflow = (ROOT / ".github" / "workflows" / "pages.yml").read_text(encoding="utf-8")

    assert "uv run --frozen python -m fearngreed.verify\n" in workflow
    assert "python -m fearngreed.verify --base-url" in workflow
    assert workflow.index("python -m fearngreed.verify\n") < workflow.index(
        "actions/upload-pages-artifact@v3"
    )
    assert workflow.index("actions/deploy-pages@v4") < workflow.index(
        "Verify live public derivative hashes"
    )


def test_fast_signal_workflow_is_separate_bounded_and_secret_scoped() -> None:
    workflow = (ROOT / ".github" / "workflows" / "live-signal.yml").read_text(encoding="utf-8")

    assert 'cron: "47 6 * * 1-5"' in workflow
    assert "ref: main" in workflow
    capture = workflow.split("Capture authenticated preliminary close signal", 1)[1].split(
        "Enforce fast-signal mutation boundary", 1
    )[0]
    after_capture = workflow.split("Enforce fast-signal mutation boundary", 1)[1]
    for name in ("KRX_API_KEY", "KRX_ID", "KRX_PW"):
        assert name in capture
        assert name not in after_capture
        assert f"missing_secrets+=({name})" in capture
    assert "data/live-signal.json) ;;" in workflow
    assert "git add data/live-signal.json" in workflow
    assert "\n          git add data\n" not in workflow
    assert "git push origin HEAD:main" in workflow
    assert "--base-url" in workflow
    assert "scan_public_files" in capture
    assert "timeout-minutes: 25" in workflow
    assert "timeout --signal=TERM --kill-after=10s 120s" in capture
    assert "live_capture_timeout" in capture


def test_failed_refresh_preserves_market_outputs_and_last_success(tmp_path, monkeypatch) -> None:
    data = tmp_path / "data"
    data.mkdir()
    summary = {
        "dataAsOf": "2026-07-15",
        "status": {
            "state": "ok",
            "freshnessBasis": "official_krx_latest_completed_session",
            "expectedDataAsOf": "2026-07-15",
            "sourceFreshnessPassed": True,
            "degradedReasons": [],
        },
        "automation": {
            "lastAttemptAt": "2026-07-16T01:00:00Z",
            "lastSuccessAt": "2026-07-16T01:00:00Z",
            "state": "ok",
        },
        "primaryEntities": [{"id": "KOSPI", "sentimentPercentile": 68.25}],
    }
    automation = {
        "schemaVersion": 1,
        "state": "ok",
        "lastAttemptAt": "2026-07-16T01:00:00Z",
        "lastSuccessAt": "2026-07-16T01:00:00Z",
        "dataAsOf": "2026-07-15",
        "degradedReasons": [],
        "sourceMode": "krx_open_api",
        "freshnessBasis": "official_krx_latest_completed_session",
        "expectedDataAsOf": "2026-07-15",
        "sourceFreshnessPassed": True,
    }
    dashboard = b'{"dataAsOf":"2026-07-15","market":"last-good"}\n'
    history = b'{"dataAsOf":"2026-07-15","series":[{"date":"2026-07-15"}]}\n'
    (data / "summary.json").write_text(json.dumps(summary), encoding="utf-8")
    (data / "automation-status.json").write_text(json.dumps(automation), encoding="utf-8")
    (data / "dashboard.json").write_bytes(dashboard)
    (data / "history.json").write_bytes(history)
    monkeypatch.setattr(refresh_module, "repository_root", lambda: tmp_path)

    refresh_module.mark_failed("provider_unavailable")

    updated_summary = json.loads((data / "summary.json").read_text(encoding="utf-8"))
    updated_automation = json.loads((data / "automation-status.json").read_text(encoding="utf-8"))
    assert updated_summary["dataAsOf"] == "2026-07-15"
    assert updated_summary["primaryEntities"] == summary["primaryEntities"]
    assert updated_summary["status"]["state"] == "degraded"
    assert updated_summary["status"]["label"] == "데이터 저하"
    assert updated_summary["status"]["degradedReasons"] == ["provider_unavailable"]
    assert updated_summary["status"]["expectedDataAsOf"] == "2026-07-15"
    assert updated_summary["status"]["sourceFreshnessPassed"] is True
    assert updated_summary["automation"]["lastSuccessAt"] == "2026-07-16T01:00:00Z"
    assert updated_automation["state"] == "degraded"
    assert updated_automation["lastSuccessAt"] == "2026-07-16T01:00:00Z"
    assert updated_automation["dataAsOf"] == "2026-07-15"
    assert updated_automation["sourceMode"] == "krx_open_api"
    assert updated_automation["expectedDataAsOf"] == "2026-07-15"
    assert updated_automation["sourceFreshnessPassed"] is True
    assert (data / "dashboard.json").read_bytes() == dashboard
    assert (data / "history.json").read_bytes() == history
    assert not list(data.glob(".*.json.*"))


def test_failed_refresh_marks_status_stale_for_a_newer_official_session(
    tmp_path, monkeypatch
) -> None:
    data = tmp_path / "data"
    data.mkdir()
    summary = {
        "dataAsOf": "2026-07-15",
        "status": {
            "state": "ok",
            "label": "데이터 정상",
            "freshnessBasis": "official_krx_latest_completed_session",
            "expectedDataAsOf": "2026-07-15",
            "sourceFreshnessPassed": True,
            "degradedReasons": [],
        },
        "automation": {
            "lastAttemptAt": "2026-07-16T01:00:00Z",
            "lastSuccessAt": "2026-07-16T01:00:00Z",
            "state": "ok",
        },
        "primaryEntities": [{"id": "KOSPI", "sentimentPercentile": 68.25}],
    }
    automation = {
        "schemaVersion": 1,
        "state": "ok",
        "lastAttemptAt": "2026-07-16T01:00:00Z",
        "lastSuccessAt": "2026-07-16T01:00:00Z",
        "dataAsOf": "2026-07-15",
        "degradedReasons": [],
        "sourceMode": "krx_open_api",
    }
    dashboard = b'{"dataAsOf":"2026-07-15","market":"last-good"}\n'
    history = b'{"dataAsOf":"2026-07-15","series":[{"date":"2026-07-15"}]}\n'
    (data / "summary.json").write_text(json.dumps(summary), encoding="utf-8")
    (data / "automation-status.json").write_text(json.dumps(automation), encoding="utf-8")
    (data / "dashboard.json").write_bytes(dashboard)
    (data / "history.json").write_bytes(history)
    monkeypatch.setattr(refresh_module, "repository_root", lambda: tmp_path)

    refresh_module.mark_failed(
        "refresh_core_input_quality_failed",
        expected_as_of=date(2026, 7, 16),
    )

    updated_summary = json.loads((data / "summary.json").read_text(encoding="utf-8"))
    updated_automation = json.loads((data / "automation-status.json").read_text(encoding="utf-8"))
    status = updated_summary["status"]
    assert updated_summary["dataAsOf"] == "2026-07-15"
    assert updated_summary["primaryEntities"] == summary["primaryEntities"]
    assert status["state"] == "stale"
    assert status["label"] == "데이터 지연"
    assert status["freshnessBasis"] == "official_krx_latest_completed_session"
    assert status["expectedDataAsOf"] == "2026-07-16"
    assert status["sourceFreshnessPassed"] is False
    assert updated_summary["automation"]["state"] == "stale"
    assert updated_automation["state"] == "stale"
    assert updated_automation["freshnessBasis"] == "official_krx_latest_completed_session"
    assert updated_automation["expectedDataAsOf"] == "2026-07-16"
    assert updated_automation["sourceFreshnessPassed"] is False
    assert updated_automation["lastSuccessAt"] == "2026-07-16T01:00:00Z"
    assert (data / "dashboard.json").read_bytes() == dashboard
    assert (data / "history.json").read_bytes() == history


def test_failed_refresh_timeout_preserves_an_existing_known_stale_contract(
    tmp_path, monkeypatch
) -> None:
    data = tmp_path / "data"
    data.mkdir()
    summary = {
        "dataAsOf": "2026-07-15",
        "status": {
            "state": "stale",
            "label": "데이터 지연",
            "freshnessBasis": "official_krx_latest_completed_session",
            "expectedDataAsOf": "2026-07-16",
            "sourceFreshnessPassed": False,
            "degradedReasons": ["provider_unavailable"],
        },
        "automation": {"state": "stale", "lastSuccessAt": "2026-07-15T12:00:00Z"},
        "primaryEntities": [{"id": "KOSPI", "sourceMode": "krx_open_api"}],
    }
    automation = {
        "schemaVersion": 1,
        "state": "stale",
        "lastSuccessAt": "2026-07-15T12:00:00Z",
        "dataAsOf": "2026-07-15",
        "freshnessBasis": "official_krx_latest_completed_session",
        "expectedDataAsOf": "2026-07-16",
        "sourceFreshnessPassed": False,
        "degradedReasons": ["provider_unavailable"],
        "sourceMode": "krx_open_api",
    }
    (data / "summary.json").write_text(json.dumps(summary), encoding="utf-8")
    (data / "automation-status.json").write_text(json.dumps(automation), encoding="utf-8")
    monkeypatch.setattr(refresh_module, "repository_root", lambda: tmp_path)

    refresh_module.mark_failed("refresh_timeout")

    updated_summary = json.loads((data / "summary.json").read_text(encoding="utf-8"))
    updated_automation = json.loads((data / "automation-status.json").read_text(encoding="utf-8"))
    assert updated_summary["status"]["state"] == "stale"
    assert updated_summary["status"]["expectedDataAsOf"] == "2026-07-16"
    assert updated_summary["status"]["sourceFreshnessPassed"] is False
    assert updated_automation["state"] == "stale"
    assert updated_automation["expectedDataAsOf"] == "2026-07-16"
    assert updated_automation["sourceFreshnessPassed"] is False


def test_failed_refresh_status_remains_publishable_by_the_full_local_contract(
    tmp_path, monkeypatch
) -> None:
    shutil.copytree(ROOT / "data", tmp_path / "data")
    shutil.copytree(ROOT / "schemas", tmp_path / "schemas")
    summary = json.loads((tmp_path / "data" / "summary.json").read_text(encoding="utf-8"))
    current = date.fromisoformat(summary["dataAsOf"])
    prior_expected_value = summary.get("status", {}).get("expectedDataAsOf")
    prior_expected = (
        date.fromisoformat(prior_expected_value) if isinstance(prior_expected_value, str) else None
    )
    requested_expected = current + timedelta(days=1)
    effective_expected = max(
        value for value in (prior_expected, requested_expected) if value is not None
    )
    monkeypatch.setattr(refresh_module, "repository_root", lambda: tmp_path)

    refresh_module.mark_failed(
        "frozen_history_drift_requires_backfill",
        expected_as_of=requested_expected,
    )

    receipt = verify_local(tmp_path, minimum_headroom_ratio=0)
    updated = json.loads((tmp_path / "data" / "summary.json").read_text(encoding="utf-8"))
    assert receipt["operationalState"] == "stale"
    assert updated["dataAsOf"] == current.isoformat()
    assert updated["status"]["expectedDataAsOf"] == effective_expected.isoformat()
    assert updated["status"]["sourceFreshnessPassed"] is False


def test_failed_refresh_redacts_unapproved_error_text(tmp_path, monkeypatch) -> None:
    data = tmp_path / "data"
    data.mkdir()
    summary = {
        "dataAsOf": "2026-07-15",
        "status": {"state": "ok", "degradedReasons": []},
        "automation": {"lastSuccessAt": "2026-07-16T01:00:00Z", "state": "ok"},
        "primaryEntities": [{"id": "KOSPI", "sourceMode": "krx_open_api"}],
    }
    (data / "summary.json").write_text(json.dumps(summary), encoding="utf-8")
    monkeypatch.setattr(refresh_module, "repository_root", lambda: tmp_path)

    refresh_module.mark_failed("request failed with FAKE_KRX_SECRET_CANARY")

    public_text = "\n".join(path.read_text(encoding="utf-8") for path in data.glob("*.json"))
    assert "FAKE_KRX_SECRET_CANARY" not in public_text
    assert "refresh_provider_failed" in public_text
