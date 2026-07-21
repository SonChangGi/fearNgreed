from __future__ import annotations

import plistlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_live_signal_wrapper_is_bounded_and_never_mutates_git() -> None:
    script = (ROOT / "scripts" / "run-live-signal").read_text(encoding="utf-8")

    assert "15:47-16:00 KST" in script
    assert "for attempt in 1 2 3 4" in script
    assert "sleep 180" in script
    assert "with-krx-keychain uv run --frozen python -m fearngreed.live_signal" in script
    assert 'TIMEOUT_RUNNER="$SCRIPT_DIR/run-with-timeout"' in script
    assert '"$CAPTURE_TIMEOUT_SECONDS"' in script
    assert "capture_status == 124" in script
    assert "live_capture_timeout" in script
    assert '--output "$OUTPUT_PATH"' in script
    assert 'OUTPUT_PATH="$REPOSITORY_ROOT/var/live-signal-local.json"' in script
    assert '>"$receipt_file" 2>/dev/null' in script
    assert "set -x" not in script
    assert "printenv" not in script
    assert "git " not in script
    assert "security " not in script
    assert "KRX_API_KEY" not in script
    assert "KRX_ID" not in script
    assert "KRX_PW" not in script


def test_launch_agent_runs_only_at_1547_on_weekdays() -> None:
    path = ROOT / "automation" / "com.sonchanggi.fearngreed.live-signal.plist"
    payload = plistlib.loads(path.read_bytes())

    assert payload["Label"] == "com.sonchanggi.fearngreed.live-signal"
    assert payload["ProgramArguments"] == [
        "/bin/zsh",
        "__REPOSITORY_ROOT__/scripts/run-live-signal",
    ]
    intervals = payload["StartCalendarInterval"]
    assert len(intervals) == 5
    assert {item["Weekday"] for item in intervals} == {1, 2, 3, 4, 5}
    assert {item["Hour"] for item in intervals} == {15}
    assert {item["Minute"] for item in intervals} == {47}
    assert payload["RunAtLoad"] is False
    assert "KRX" not in path.read_text(encoding="utf-8")


def test_launch_agent_installer_checks_bridge_without_reading_credentials() -> None:
    script = (ROOT / "scripts" / "install-live-signal-launch-agent").read_text(encoding="utf-8")

    assert "with-krx-keychain --check" in script
    assert "launchctl bootstrap" in script
    assert "launchctl enable" in script
    assert "security " not in script
    assert "KRX_API_KEY" not in script
    assert "KRX_ID" not in script
    assert "KRX_PW" not in script
