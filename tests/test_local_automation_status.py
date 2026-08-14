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
        "schemaVersion": 1,
        "contract": "fearngreed-local-automation-status",
        "component": "official-refresh",
        "source": "local-launch-agent",
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


def test_status_writer_rejects_unstructured_reason_without_echoing_it(tmp_path: Path) -> None:
    output = tmp_path / "status.json"
    fake_secret = "fake-secret=value"
    completed = subprocess.run(
        [
            "python3",
            str(WRITER),
            "--output",
            str(output),
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
