#!/usr/bin/env python3
"""Write one secret-safe local automation status snapshot atomically."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
from datetime import UTC, date, datetime
from pathlib import Path

SAFE_TOKEN = re.compile(r"[a-z0-9][a-z0-9_-]{0,63}")


def _safe_token(value: str, *, field: str) -> str:
    if SAFE_TOKEN.fullmatch(value) is None:
        raise ValueError(f"{field} must be a short machine-readable token")
    return value


def build_payload(args: argparse.Namespace) -> dict[str, object]:
    target_date = date.fromisoformat(args.target_date).isoformat()
    return {
        "schemaVersion": 1,
        "contract": "fearngreed-local-automation-status",
        "component": "official-refresh",
        "source": "local-launch-agent",
        "observedAt": datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "targetDate": target_date,
        "runMode": _safe_token(args.run_mode, field="run-mode"),
        "stage": _safe_token(args.stage, field="stage"),
        "status": _safe_token(args.status, field="status"),
        "reason": _safe_token(args.reason, field="reason"),
        "attempt": args.attempt,
    }


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
