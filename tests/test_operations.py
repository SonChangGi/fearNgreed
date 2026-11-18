from __future__ import annotations

import json
import shutil
from datetime import date, timedelta
from pathlib import Path

import fearngreed.refresh as refresh_module
from fearngreed.verify import verify_local

ROOT = Path(__file__).resolve().parents[1]


def test_pages_workflow_runs_local_and_live_contract_verification() -> None:
    workflow = (ROOT / ".github" / "workflows" / "pages.yml").read_text(encoding="utf-8")

    assert "Verify checkout public contract" in workflow
    assert "uv run --frozen python -m fearngreed.verify\n" in workflow
    assert 'verify_args=(--base-url "${{ steps.deployment.outputs.page_url }}")' in workflow
    assert workflow.index("Verify checkout public contract") < workflow.index(
        "actions/upload-pages-artifact@"
    )
    assert workflow.index("actions/deploy-pages@") < workflow.index(
        "Verify exact live public bytes and freshness"
    )
    assert "continue-on-error: ${{ github.event_name == 'push' }}" not in workflow
    assert "source_freshness_passed" in workflow
    assert '[[ "$data_as_of" == "$expected_data_as_of" ]]' in workflow
    assert 'verify_args+=(--expected-data-as-of "$EXPECTED_DATA_AS_OF")' in workflow
    assert "for attempt in {1..30}" in workflow
    assert "public-site-health:" not in workflow


def test_fast_signal_workflow_is_separate_bounded_and_secret_scoped() -> None:
    workflow = (ROOT / ".github" / "workflows" / "live-signal.yml").read_text(encoding="utf-8")

    assert "workflow_dispatch:" in workflow
    assert "\n  schedule:" not in workflow
    assert 'cron: "47 6 * * 1-5"' not in workflow
    assert "The local LaunchAgent is the only automatic time-sensitive path" in workflow
    assert "for attempt in 1 2 3 4" in workflow
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
    assert "Report missed scheduled capture window" not in workflow
    assert "github.event_name == 'schedule'" not in workflow
    assert "Report scheduled signal inputs not ready" not in workflow


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
    assert updated_summary["status"]["freshnessBasis"] == "source_alignment_only"
    assert updated_summary["status"]["expectedDataAsOf"] is None
    assert updated_summary["status"]["sourceFreshnessPassed"] is False
    assert updated_summary["automation"]["lastSuccessAt"] == "2026-07-16T01:00:00Z"
    assert updated_automation["state"] == "degraded"
    assert updated_automation["lastSuccessAt"] == "2026-07-16T01:00:00Z"
    assert updated_automation["dataAsOf"] == "2026-07-15"
    assert updated_automation["sourceMode"] == "krx_open_api"
    assert updated_automation["freshnessBasis"] == "source_alignment_only"
    assert updated_automation["expectedDataAsOf"] is None
    assert updated_automation["sourceFreshnessPassed"] is False
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
