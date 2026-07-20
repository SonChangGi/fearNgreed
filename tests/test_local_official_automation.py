from __future__ import annotations

import plistlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_official_refresh_wrapper_is_isolated_bounded_and_secret_safe() -> None:
    script = (ROOT / "scripts" / "run-official-refresh").read_text(encoding="utf-8")

    assert "mktemp -d" in script
    assert "git clone --quiet --depth 1 --branch main" in script
    assert "with-krx-keychain uv run --frozen python -m fearngreed.refresh" in script
    assert "--failure-policy preserve --require-end-session" in script
    assert "--failure-policy publish" in script
    assert 'git -C "$CHECKOUT" rev-parse origin/main' in script
    assert 'git -C "$CHECKOUT" push --quiet origin HEAD:main' in script
    assert "git reset" not in script
    assert "git rebase" not in script
    assert "--force" not in script
    assert "printenv" not in script
    assert "set -x" not in script
    assert "KRX_API_KEY" not in script
    assert "KRX_ID" not in script
    assert "KRX_PW" not in script


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
