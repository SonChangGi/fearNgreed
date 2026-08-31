from __future__ import annotations

import json
import stat
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WRITER = ROOT / "scripts" / "write-local-automation-status.py"


def test_status_writer_emits_and_atomically_persists_secret_safe_snapshot(
    tmp_path: Path,
) -> None:
    output = tmp_path / "nested" / "official-refresh-status.json"
    completed = subprocess.run(
        [
            "python3",
            str(WRITER),
            "--output",
            str(output),
            "--component",
            "official-refresh",
            "--run-id",
            "official-20260814t113000z-42",
            "--started-at",
            "2026-08-14T11:30:00Z",
            "--deadline-at",
            "2026-08-14T23:30:00+09:00",
            "--target-date",
            "2026-08-14",
            "--run-mode",
            "terminal",
            "--stage",
            "push",
            "--status",
            "retrying",
            "--reason",
            "network_error",
            "--attempt",
            "2",
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    stdout_payload = json.loads(completed.stdout)
    saved_payload = json.loads(output.read_text(encoding="utf-8"))
    assert saved_payload == stdout_payload
    assert saved_payload == {
        **saved_payload,
        "schemaVersion": 2,
        "contract": "fearngreed-local-automation-status",
        "component": "official-refresh",
        "source": "local-launch-agent",
        "runId": "official-20260814t113000z-42",
        "startedAt": "2026-08-14T11:30:00Z",
        "deadlineAt": "2026-08-14T23:30:00+09:00",
        "targetDate": "2026-08-14",
        "runMode": "terminal",
        "stage": "push",
        "status": "retrying",
        "reason": "network_error",
        "attempt": 2,
    }
    assert saved_payload["observedAt"].endswith("Z")
    assert stat.S_IMODE(output.stat().st_mode) == 0o600
    assert not list(output.parent.glob(".official-refresh-status.json.*.tmp"))


def test_status_writer_runs_with_launch_agent_system_python(tmp_path: Path) -> None:
    system_python = Path("/usr/bin/python3")
    assert system_python.exists()
    output = tmp_path / "system-python-status.json"

    completed = subprocess.run(
        [
            str(system_python),
            str(WRITER),
            "--output",
            str(output),
            "--run-id",
            "official-20260814t113000z-43",
            "--started-at",
            "2026-08-14T11:30:00Z",
            "--target-date",
            "2026-08-14",
            "--run-mode",
            "unscheduled",
            "--stage",
            "schedule",
            "--status",
            "skipped",
            "--reason",
            "outside_window",
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["status"] == "skipped"
    assert payload["observedAt"].endswith("Z")


def test_status_writer_rejects_unstructured_reason_without_echoing_it(tmp_path: Path) -> None:
    output = tmp_path / "status.json"
    fake_secret = "fake-secret=value"
    completed = subprocess.run(
        [
            "python3",
            str(WRITER),
            "--output",
            str(output),
            "--run-id",
            "official-20260814t113000z-44",
            "--started-at",
            "2026-08-14T11:30:00Z",
            "--target-date",
            "2026-08-14",
            "--run-mode",
            "terminal",
            "--stage",
            "push",
            "--status",
            "failed",
            "--reason",
            fake_secret,
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 2
    assert fake_secret not in completed.stdout
    assert fake_secret not in completed.stderr
    assert not output.exists()


def test_status_writer_supports_live_signal_component_and_rejects_naive_time(
    tmp_path: Path,
) -> None:
    output = tmp_path / "live-status.json"
    command = [
        "python3",
        str(WRITER),
        "--output",
        str(output),
        "--component",
        "live-signal",
        "--run-id",
        "live-20260814t064700z-45",
        "--started-at",
        "2026-08-14T06:47:00Z",
        "--deadline-at",
        "2026-08-14T16:00:00+09:00",
        "--target-date",
        "2026-08-14",
        "--run-mode",
        "capture",
        "--stage",
        "publish",
        "--status",
        "ready",
        "--reason",
        "provisional_signal_ready",
    ]
    completed = subprocess.run(command, check=True, capture_output=True, text=True)
    payload = json.loads(completed.stdout)
    assert payload["component"] == "live-signal"
    assert payload["deadlineAt"] == "2026-08-14T16:00:00+09:00"

    command[command.index("2026-08-14T16:00:00+09:00")] = "2026-08-14T16:00:00"
    rejected = subprocess.run(command, check=False, capture_output=True, text=True)
    assert rejected.returncode == 2
    assert "2026-08-14T16:00:00" not in rejected.stderr
