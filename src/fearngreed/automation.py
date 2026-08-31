from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

KST = ZoneInfo("Asia/Seoul")
OFFICIAL_DATA_READY_AT = time(18, 15)


@dataclass(frozen=True)
class WeekdayCronSlot:
    minute: int
    hour: int


def _aware_datetime(value: datetime | None) -> datetime:
    observed_at = value or datetime.now(UTC)
    if observed_at.tzinfo is None or observed_at.utcoffset() is None:
        raise ValueError("observed_at must include a timezone")
    return observed_at


def _parse_observed_at(value: str) -> datetime:
    normalized = value.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as error:
        raise argparse.ArgumentTypeError("observed-at must be an ISO-8601 datetime") from error
    try:
        return _aware_datetime(parsed)
    except ValueError as error:
        raise argparse.ArgumentTypeError(str(error)) from error


def _parse_weekday_cron(schedule: str) -> WeekdayCronSlot:
    fields = schedule.split()
    if len(fields) != 5:
        raise ValueError("schedule must contain five cron fields")
    minute_value, hour_value, day_of_month, month, day_of_week = fields
    if day_of_month != "*" or month != "*" or day_of_week != "1-5":
        raise ValueError("only weekday daily cron schedules are supported")
    try:
        minute = int(minute_value)
        hour = int(hour_value)
    except ValueError as error:
        raise ValueError("cron hour and minute must be integers") from error
    if not 0 <= minute <= 59 or not 0 <= hour <= 23:
        raise ValueError("cron hour or minute is out of range")
    return WeekdayCronSlot(minute=minute, hour=hour)


def _previous_weekday(candidate: date) -> date:
    while candidate.weekday() >= 5:
        candidate -= timedelta(days=1)
    return candidate


def _latest_scheduled_slot(*, observed_at: datetime, schedule: str) -> datetime:
    """Return the latest intended UTC cron slot at or before workflow start.

    GitHub-hosted cron can start hours after its nominal slot.  Reconstructing
    the slot from the triggering expression prevents a Friday refresh that
    starts on Saturday from requesting a nonexistent Saturday KRX session.
    """

    slot = _parse_weekday_cron(schedule)
    observed_utc = _aware_datetime(observed_at).astimezone(UTC)
    for offset in range(8):
        candidate_day = observed_utc.date() - timedelta(days=offset)
        if candidate_day.weekday() >= 5:
            continue
        candidate = datetime.combine(
            candidate_day,
            time(slot.hour, slot.minute),
            tzinfo=UTC,
        )
        if candidate <= observed_utc:
            return candidate
    raise ValueError("could not resolve a weekday cron slot")


def latest_expected_krx_session(
    *,
    observed_at: datetime | None = None,
    schedule: str | None = None,
) -> date:
    """Resolve the calendar date whose completed KRX session is expected.

    A scheduled run is bound to the date of its nominal cron slot, not the
    runner's delayed wall-clock date.  An unscheduled run uses the latest
    weekday whose final-data window has opened.  The provider remains the
    authority for exchange holidays and confirms whether this date is an
    actual KRX session.
    """

    observed = _aware_datetime(observed_at)
    if schedule:
        intended_slot = _latest_scheduled_slot(observed_at=observed, schedule=schedule)
        return intended_slot.astimezone(KST).date()

    local = observed.astimezone(KST)
    candidate = local.date()
    if local.time().replace(tzinfo=None) < OFFICIAL_DATA_READY_AT:
        candidate -= timedelta(days=1)
    return _previous_weekday(candidate)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Resolve Fear & Greed automation dates")
    subparsers = parser.add_subparsers(dest="command", required=True)
    expected = subparsers.add_parser(
        "expected-session",
        help="Print the latest calendar date whose official KRX session is expected",
    )
    expected.add_argument("--observed-at", type=_parse_observed_at)
    expected.add_argument("--schedule", help="Triggering UTC GitHub cron expression")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.command == "expected-session":
        expected = latest_expected_krx_session(
            observed_at=args.observed_at,
            schedule=args.schedule,
        )
        print(expected.isoformat())
        return 0
    raise AssertionError(f"unsupported command: {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())
