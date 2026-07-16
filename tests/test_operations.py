from __future__ import annotations

import json
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
    assert "KRX_API_KEY" not in before_refresh
    assert "KRX_API_KEY" in refresh_step
    assert "KRX_ID" in refresh_step
    assert "KRX_PW" in refresh_step
    assert "scan_public_files" in refresh_step
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
    assert "python -m fearngreed.verify --base-url" in workflow
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
        "status": {"state": "ok", "degradedReasons": []},
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

    refresh_module.mark_failed("provider_unavailable")

    updated_summary = json.loads((data / "summary.json").read_text(encoding="utf-8"))
    updated_automation = json.loads((data / "automation-status.json").read_text(encoding="utf-8"))
    assert updated_summary["dataAsOf"] == "2026-07-15"
    assert updated_summary["primaryEntities"] == summary["primaryEntities"]
    assert updated_summary["status"]["state"] == "degraded"
    assert updated_summary["status"]["label"] == "데이터 저하"
    assert updated_summary["status"]["degradedReasons"] == ["provider_unavailable"]
    assert updated_summary["automation"]["lastSuccessAt"] == "2026-07-16T01:00:00Z"
    assert updated_automation["state"] == "degraded"
    assert updated_automation["lastSuccessAt"] == "2026-07-16T01:00:00Z"
    assert updated_automation["dataAsOf"] == "2026-07-15"
    assert updated_automation["sourceMode"] == "krx_open_api"
    assert (data / "dashboard.json").read_bytes() == dashboard
    assert (data / "history.json").read_bytes() == history
    assert not list(data.glob(".*.json.*"))


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
