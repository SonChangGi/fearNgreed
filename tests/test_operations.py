from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import fearngreed.refresh as refresh_module

ROOT = Path(__file__).resolve().parents[1]


def test_refresh_workflow_publishes_only_status_after_provider_failure() -> None:
    workflow = (ROOT / ".github" / "workflows" / "refresh.yml").read_text(encoding="utf-8")

    assert "outcome=failure" in workflow
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
    assert "scan_public_files" in refresh_step
    assert 'refresh_receipt_file="$RUNNER_TEMP/fearngreed-refresh-receipt.json"' in refresh_step
    assert '> "$refresh_receipt_file"' in refresh_step
    assert "receipt['expectedDataAsOf']" in refresh_step
    assert "json.loads(Path(sys.argv[1]).read_text" in refresh_step
    assert "json.load(open('data/summary.json'" not in refresh_step
    success_branch = refresh_step.split("if [[ $refresh_status -eq 0 ]]; then", 1)[1].split(
        "else", 1
    )[0]
    failure_branch = refresh_step.split("if [[ $refresh_status -eq 0 ]]; then", 1)[1].split(
        "else", 1
    )[1]
    assert "expectedDataAsOf" in success_branch
    assert 'cat "$refresh_receipt_file"' in success_branch
    assert "json.loads" not in failure_branch
    assert 'cat "$refresh_receipt_file"' in failure_branch
    assert 'echo "dataAsOf=$data_as_of" >> "$GITHUB_OUTPUT"' in refresh_step
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
    assert workflow.index("Verify live public derivative hashes") < workflow.index(
        "Report provider refresh failure"
    )


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
