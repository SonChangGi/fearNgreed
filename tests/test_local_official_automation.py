from __future__ import annotations

import json
import os
import plistlib
import re
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
    assert "NETWORK_ATTEMPTS=4" in script
    assert "NETWORK_RETRY_DELAYS=(15 60 120)" in script
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
    assert 'local run_status="$2"' in script
    assert 'local status="$2"' not in script
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


def test_write_status_runs_under_zsh_without_reserved_variable_collision(
    tmp_path: Path,
) -> None:
    zsh = shutil.which("zsh")
    if zsh is None:
        pytest.skip("zsh runtime validation runs on macOS where the LaunchAgent is installed")

    script = (ROOT / "scripts" / "run-official-refresh").read_text(encoding="utf-8")
    function_match = re.search(r"write_status\(\) \{\n.*?\n\}", script, re.DOTALL)
    assert function_match is not None

    status_path = tmp_path / "official-refresh-status.json"
    environment = {
        **os.environ,
        "STATUS_WRITER": str(ROOT / "scripts" / "write-local-automation-status.py"),
        "STATUS_PATH": str(status_path),
        "SEOUL_DATE": "2026-08-14",
        "run_mode": "unscheduled",
    }
    completed = subprocess.run(
        [
            zsh,
            "-c",
            f"set -euo pipefail\n{function_match.group(0)}\n"
            "write_status schedule skipped outside_window 0",
        ],
        check=False,
        capture_output=True,
        text=True,
        env=environment,
    )

    assert completed.returncode == 0, completed.stderr
    payload = json.loads(status_path.read_text(encoding="utf-8"))
    assert payload["stage"] == "schedule"
    assert payload["status"] == "skipped"
    assert payload["reason"] == "outside_window"


def test_clone_network_retry_reaches_fourth_attempt_after_bounded_backoff(
    tmp_path: Path,
) -> None:
    zsh = shutil.which("zsh")
    if zsh is None:
        pytest.skip("zsh runtime validation runs on macOS where the LaunchAgent is installed")

    script = (ROOT / "scripts" / "run-official-refresh").read_text(encoding="utf-8")
    function_match = re.search(r"clone_repository\(\) \{\n.*?\n\}", script, re.DOTALL)
    assert function_match is not None

    completed = subprocess.run(
        [
            zsh,
            "-c",
            f"""
set -u
NETWORK_ATTEMPTS=4
NETWORK_RETRY_DELAYS=(15 60 120)
NETWORK_TIMEOUT_SECONDS=120
TIMEOUT_RUNNER=unused
REPOSITORY_URL=unused
WORK_ROOT={tmp_path!s}
CURRENT_STAGE=bootstrap
CURRENT_ATTEMPT=0
LAST_NETWORK_REASON=network_error
CHECKOUT=''
typeset -i calls=0
typeset -a delays=()
write_status() {{ :; }}
network_reason() {{ print dns_error; }}
sleep() {{ delays+=("$1"); }}
python3() {{ calls=$(( calls + 1 )); (( calls == 4 )); }}
{function_match.group(0)}
clone_repository
print "$calls|${{(j:,:)delays}}|${{CHECKOUT:t}}"
""",
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip() == "4|15,60,120|repository-4"


def test_network_reason_classifies_authentication_failure(tmp_path: Path) -> None:
    zsh = shutil.which("zsh")
    if zsh is None:
        pytest.skip("zsh runtime validation runs on macOS where the LaunchAgent is installed")

    script = (ROOT / "scripts" / "run-official-refresh").read_text(encoding="utf-8")
    function_match = re.search(r"network_reason\(\) \{\n.*?\n\}", script, re.DOTALL)
    assert function_match is not None

    error_file = tmp_path / "push.stderr"
    error_file.write_text(
        "fatal: could not read Username: terminal prompts disabled\n",
        encoding="utf-8",
    )
    completed = subprocess.run(
        [
            zsh,
            "-c",
            f"{function_match.group(0)}\nnetwork_reason 128 {error_file!s}",
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip() == "authentication_error"


@pytest.mark.parametrize("reason", ["remote_rejected", "authentication_error"])
def test_non_retryable_network_write_fails_immediately(
    tmp_path: Path,
    reason: str,
) -> None:
    zsh = shutil.which("zsh")
    if zsh is None:
        pytest.skip("zsh runtime validation runs on macOS where the LaunchAgent is installed")

    script = (ROOT / "scripts" / "run-official-refresh").read_text(encoding="utf-8")
    function_match = re.search(r"run_network_command\(\) \{\n.*?\n\}", script, re.DOTALL)
    assert function_match is not None

    completed = subprocess.run(
        [
            zsh,
            "-c",
            f"""
set -u
NETWORK_ATTEMPTS=4
NETWORK_RETRY_DELAYS=(15 60 120)
NETWORK_TIMEOUT_SECONDS=120
TIMEOUT_RUNNER=unused
WORK_ROOT={tmp_path!s}
CURRENT_STAGE=bootstrap
CURRENT_ATTEMPT=0
LAST_NETWORK_REASON=network_error
typeset -i calls=0
typeset -a delays=()
write_status() {{ :; }}
network_reason() {{ print {reason}; }}
sleep() {{ delays+=("$1"); }}
python3() {{ calls=$(( calls + 1 )); return 1; }}
{function_match.group(0)}
run_network_command push git push
result=$?
print "$calls|${{#delays}}|$result|$LAST_NETWORK_REASON"
""",
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip() == f"1|0|1|{reason}"


def test_official_refresh_launch_agent_has_three_weekday_schedules() -> None:
    path = ROOT / "automation" / "com.sonchanggi.fearngreed.official-refresh.plist"
    payload = plistlib.loads(path.read_bytes())

    assert payload["Label"] == "com.sonchanggi.fearngreed.official-refresh"
    assert payload["ProgramArguments"] == [
        "/usr/bin/caffeinate",
        "-i",
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
