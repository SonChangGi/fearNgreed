#!/usr/bin/env python3
"""Write one secret-safe local automation status snapshot atomically."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
from datetime import date, datetime, timezone
from pathlib import Path

SAFE_TOKEN = re.compile(r"[a-z0-9][a-z0-9_-]{0,63}")


def _safe_token(value: str, *, field: str) -> str:
    if SAFE_TOKEN.fullmatch(value) is None:
        raise ValueError(f"{field} must be a short machine-readable token")
    return value


def _aware_timestamp(value: str, *, field: str) -> str:
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as error:
        raise ValueError(f"{field} must be an ISO-8601 timestamp") from error
    if parsed.tzinfo is None:
        raise ValueError(f"{field} must include a UTC offset")
    return value


def build_payload(args: argparse.Namespace) -> dict[str, object]:
    target_date = date.fromisoformat(args.target_date).isoformat()
    payload: dict[str, object] = {
        "schemaVersion": 2,
        "contract": "fearngreed-local-automation-status",
        "component": _safe_token(args.component, field="component"),
        "source": "local-launch-agent",
        "observedAt": datetime.now(timezone.utc)  # noqa: UP017 - macOS system Python 3.9
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z"),
        "runId": _safe_token(args.run_id, field="run-id"),
        "startedAt": _aware_timestamp(args.started_at, field="started-at"),
        "targetDate": target_date,
        "runMode": _safe_token(args.run_mode, field="run-mode"),
        "stage": _safe_token(args.stage, field="stage"),
        "status": _safe_token(args.status, field="status"),
        "reason": _safe_token(args.reason, field="reason"),
        "attempt": args.attempt,
    }
    if args.deadline_at:
        payload["deadlineAt"] = _aware_timestamp(args.deadline_at, field="deadline-at")
    return payload


def write_snapshot(path: Path, payload: dict[str, object]) -> str:
    serialized = json.dumps(payload, ensure_ascii=True, separators=(",", ":")) + "\n"
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
        text=True,
    )
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(serialized)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
    return serialized


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--component", default="official-refresh")
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--started-at", required=True)
    parser.add_argument("--deadline-at")
    parser.add_argument("--target-date", required=True)
    parser.add_argument("--run-mode", required=True)
    parser.add_argument("--stage", required=True)
    parser.add_argument("--status", required=True)
    parser.add_argument("--reason", required=True)
    parser.add_argument("--attempt", type=int, default=0)
    args = parser.parse_args(argv)
    if args.attempt < 0 or args.attempt > 99:
        parser.error("attempt must be between 0 and 99")
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        payload = build_payload(args)
    except (ValueError, TypeError):
        print("local automation status contains an invalid field", file=sys.stderr)
        return 2
    serialized = write_snapshot(args.output, payload)
    print(serialized, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
