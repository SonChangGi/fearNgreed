from __future__ import annotations

import plistlib
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def test_official_refresh_wrapper_is_isolated_bounded_and_secret_safe() -> None:
    script = (ROOT / "scripts" / "run-official-refresh").read_text(encoding="utf-8")

    assert "mktemp -d" in script
    assert "git clone --quiet --depth 1 --branch main" in script
    assert "with-krx-keychain uv run --frozen python -m fearngreed.refresh" in script
    assert 'TIMEOUT_RUNNER="$SCRIPT_DIR/run-with-timeout"' in script
    assert '"$REFRESH_TIMEOUT_SECONDS"' in script
    assert "refresh_status == 124" in script
    assert "mark_failed('refresh_timeout')" in script
    assert "--failure-policy preserve --require-end-session" in script
    assert "--failure-policy publish" in script
    assert 'git -C "$CHECKOUT" rev-parse origin/main' in script
    assert 'git -C "$CHECKOUT" push --quiet origin HEAD:main' in script
    assert "NETWORK_TIMEOUT_SECONDS=120" in script
    assert "NETWORK_ATTEMPTS=3" in script
    assert "NETWORK_RETRY_DELAYS=(5 15)" in script
    assert "clone_repository()" in script
    assert "run_network_command()" in script
    assert "run_network_command fetch" in script
    assert "run_network_command push" in script
    assert "dns_error" in script
    assert "remote_rejected" in script
    assert '2>"$network_error_file"' in script
    assert 'STATUS_WRITER="$SCRIPT_DIR/write-local-automation-status.py"' in script
    assert "official-refresh-status.json" in script
    assert "write_status refresh running collection_started 1" in script
    assert "finish_status publish published refresh_complete 1" in script
    assert "git reset" not in script
    assert "git rebase" not in script
    assert "--force" not in script
    assert "printenv" not in script
    assert "set -x" not in script
    assert "KRX_API_KEY" not in script
    assert "KRX_ID" not in script
    assert "KRX_PW" not in script


def test_official_refresh_wrapper_is_valid_zsh() -> None:
    zsh = shutil.which("zsh")
    if zsh is None:
        pytest.skip("zsh syntax validation runs on macOS where the LaunchAgent is installed")
    completed = subprocess.run(
        [zsh, "-n", str(ROOT / "scripts" / "run-official-refresh")],
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr


def test_official_refresh_launch_agent_has_three_weekday_schedules() -> None:
    path = ROOT / "automation" / "com.sonchanggi.fearngreed.official-refresh.plist"
    payload = plistlib.loads(path.read_bytes())

    assert payload["Label"] == "com.sonchanggi.fearngreed.official-refresh"
    assert payload["ProgramArguments"] == [
        "/bin/zsh",
        "__REPOSITORY_ROOT__/scripts/run-official-refresh",
    ]
    intervals = payload["StartCalendarInterval"]
    assert len(intervals) == 15
    assert {item["Weekday"] for item in intervals} == {1, 2, 3, 4, 5}
    assert {(item["Hour"], item["Minute"]) for item in intervals} == {
        (18, 15),
        (18, 45),
        (20, 30),
    }
    assert payload["RunAtLoad"] is False
    assert "KRX" not in path.read_text(encoding="utf-8")


def test_official_refresh_installer_checks_bridge_without_reading_credentials() -> None:
    script = (ROOT / "scripts" / "install-official-refresh-launch-agent").read_text(
        encoding="utf-8"
    )

    assert "with-krx-keychain --check" in script
    assert "launchctl bootstrap" in script
    assert "launchctl enable" in script
    assert "security " not in script
    assert "KRX_API_KEY" not in script
    assert "KRX_ID" not in script
    assert "KRX_PW" not in script
