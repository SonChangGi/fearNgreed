from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from fearngreed.automation import latest_expected_krx_session, main

KST = ZoneInfo("Asia/Seoul")
UTC = ZoneInfo("UTC")


def test_unscheduled_resolution_uses_current_weekday_after_final_window() -> None:
    observed_at = datetime(2026, 8, 31, 18, 15, tzinfo=KST)

    assert latest_expected_krx_session(observed_at=observed_at) == observed_at.date()


def test_unscheduled_resolution_uses_prior_weekday_before_final_window() -> None:
    observed_at = datetime(2026, 8, 31, 17, 59, tzinfo=KST)

    assert latest_expected_krx_session(observed_at=observed_at).isoformat() == "2026-08-28"


@pytest.mark.parametrize(
    "observed_at",
    [
        datetime(2026, 8, 29, 20, 30, tzinfo=KST),
        datetime(2026, 8, 30, 9, 0, tzinfo=KST),
    ],
)
def test_unscheduled_resolution_skips_weekends(observed_at: datetime) -> None:
    assert latest_expected_krx_session(observed_at=observed_at).isoformat() == "2026-08-28"


@pytest.mark.parametrize(
    "schedule",
    [
        "15 9 * * 1-5",
        "45 9 * * 1-5",
        "30 11 * * 1-5",
    ],
)
def test_delayed_friday_cron_keeps_friday_target_after_kst_midnight(schedule: str) -> None:
    observed_at = datetime(2026, 8, 29, 6, 28, tzinfo=UTC)

    assert (
        latest_expected_krx_session(observed_at=observed_at, schedule=schedule).isoformat()
        == "2026-08-28"
    )


def test_monday_cron_before_its_slot_resolves_previous_friday_slot() -> None:
    observed_at = datetime(2026, 8, 31, 8, 30, tzinfo=UTC)

    assert (
        latest_expected_krx_session(
            observed_at=observed_at,
            schedule="30 11 * * 1-5",
        ).isoformat()
        == "2026-08-28"
    )


def test_cron_slot_timezone_conversion_uses_the_slot_kst_date() -> None:
    observed_at = datetime(2026, 8, 31, 23, 31, tzinfo=UTC)

    assert (
        latest_expected_krx_session(
            observed_at=observed_at,
            schedule="30 23 * * 1-5",
        ).isoformat()
        == "2026-09-01"
    )


def test_resolution_rejects_naive_time_and_unsupported_cron() -> None:
    with pytest.raises(ValueError, match="timezone"):
        latest_expected_krx_session(observed_at=datetime(2026, 8, 31, 20, 30))
    with pytest.raises(ValueError, match="weekday daily"):
        latest_expected_krx_session(
            observed_at=datetime(2026, 8, 31, 20, 30, tzinfo=KST),
            schedule="30 11 * * *",
        )


def test_expected_session_cli_prints_only_machine_readable_date(capsys) -> None:
    exit_code = main(
        [
            "expected-session",
            "--observed-at",
            "2026-08-29T06:28:06Z",
            "--schedule",
            "30 11 * * 1-5",
        ]
    )

    assert exit_code == 0
    assert capsys.readouterr().out == "2026-08-28\n"
