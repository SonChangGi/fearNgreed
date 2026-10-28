#!/usr/bin/env python3
"""Inspect one LaunchAgent, or explicitly install its unchanged configuration.

The check subcommand is read-only. Neither subcommand starts a refresh, changes
the enabled override, removes a service, or emits raw launchctl/bridge output.
Keep this module compatible with the LaunchAgent's macOS system Python 3.9.
"""

from __future__ import annotations

import argparse
import json
import os
import plistlib
import re
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import NamedTuple
from xml.parsers.expat import ExpatError

LABEL = "com.sonchanggi.fearngreed.official-refresh"
LAUNCHCTL = "/bin/launchctl"
DEFAULT_TIMEOUT = 5.0


class CommandResult(NamedTuple):
    returncode: int | None
    stdout: str = ""
    stderr: str = ""
    error: str | None = None


def run_command(argv: list[str], *, timeout: float) -> CommandResult:
    try:
        result = subprocess.run(argv, capture_output=True, text=True, check=False, timeout=timeout)
    except subprocess.TimeoutExpired:
        return CommandResult(None, error="timeout")
    except (OSError, UnicodeError):
        return CommandResult(None, error="os_error")
    return CommandResult(result.returncode, result.stdout, result.stderr)


def _invoke(runner, argv: list[str], timeout: float) -> CommandResult:
    try:
        result = runner(argv, timeout=timeout)
    except subprocess.TimeoutExpired:
        return CommandResult(None, error="timeout")
    except (OSError, UnicodeError):
        return CommandResult(None, error="os_error")
    return CommandResult(
        result.returncode,
        result.stdout or "",
        result.stderr or "",
        getattr(result, "error", None),
    )


def _expected_configuration(repository_root: Path, home: Path) -> dict:
    template = repository_root / "automation" / f"{LABEL}.plist"
    # Replace parsed values, rather than interpolating filesystem paths into XML.
    payload = plistlib.loads(template.read_bytes())

    def replace(value):
        if isinstance(value, str):
            return value.replace("__REPOSITORY_ROOT__", str(repository_root)).replace(
                "__HOME__", str(home)
            )
        if isinstance(value, list):
            return [replace(item) for item in value]
        if isinstance(value, dict):
            return {key: replace(item) for key, item in value.items()}
        return value

    expected = replace(payload)
    if not isinstance(expected, dict):
        raise ValueError("invalid_template")
    intervals = [
        {"Weekday": weekday, "Hour": hour, "Minute": minute}
        for hour, minute in ((18, 15), (18, 45), (20, 30))
        for weekday in range(1, 6)
    ]
    if (
        expected.get("Label") != LABEL
        or expected.get("ProgramArguments")
        != [
            "/usr/bin/caffeinate",
            "-i",
            "/bin/zsh",
            str(repository_root / "scripts/run-official-refresh"),
        ]
        or expected.get("WorkingDirectory") != str(repository_root)
        or expected.get("StartCalendarInterval") != intervals
        or expected.get("RunAtLoad") is not False
        or "KeepAlive" in expected
    ):
        raise ValueError("unsafe_template")
    return expected


def _configuration(repository_root: Path, home: Path) -> tuple[str, dict | None]:
    try:
        expected = _expected_configuration(repository_root, home)
        wrapper = repository_root / "scripts/run-official-refresh"
        if not wrapper.is_file():
            return "mismatch", expected
        destination = home / "Library/LaunchAgents" / f"{LABEL}.plist"
        try:
            destination.lstat()
        except FileNotFoundError:
            return "missing", expected
        if destination.is_symlink() or not destination.is_file():
            return "mismatch", expected
        return (
            "valid" if plistlib.loads(destination.read_bytes()) == expected else "mismatch",
            expected,
        )
    except PermissionError:
        return "unknown", None
    except (OSError, ValueError, TypeError, ExpatError):
        return "mismatch", None


def _disabled_override(result: CommandResult) -> tuple[bool | None, bool]:
    if result.error or result.returncode != 0:
        return None, False
    text = result.stdout.strip()
    if not re.fullmatch(r"disabled services\s*=\s*\{[\s\S]*\}", text):
        return None, False
    body = text[text.index("{") + 1 : -1]
    pattern = r'"([^"\n]+)"\s*=>\s*(true|false|enabled|disabled)'
    if re.sub(pattern, "", body).strip():
        return None, False
    entries = re.findall(pattern, body)
    matches = [value for label, value in entries if label == LABEL]
    if len(matches) > 1:
        return None, False
    return (matches[0] in {"false", "enabled"} if matches else True), True


def _service_fields(text: str, target: str) -> dict | None:
    lines = text.strip().splitlines()
    if not lines or lines[0].strip() != f"{target} = {{" or lines[-1].strip() != "}":
        return None
    fields: dict = {}
    depth = 1
    arguments = False
    for raw in lines[1:-1]:
        line = raw.strip()
        if depth == 1 and line == "arguments = {":
            fields["arguments"] = []
            arguments = True
        elif arguments and depth == 2 and line != "}":
            fields["arguments"].append(line)
        elif depth == 1 and " = " in line:
            key, value = line.split(" = ", 1)
            if key in {"path", "program", "working directory", "state", "pid"}:
                fields[key] = value
        if line.endswith("{"):
            depth += 1
        elif line == "}":
            depth -= 1
            if depth == 1:
                arguments = False
    return fields if depth == 1 else None


def _missing_service(result: CommandResult, uid: int) -> bool:
    if result.error or result.returncode != 113:
        return False
    # An exact named-service absence is still only proof after a successful
    # print-disabled query establishes that the intended GUI domain is reachable.
    return (
        not result.stdout.strip()
        and re.fullmatch(
            rf'(?:Bad request\.\s*)?Could not find service "{re.escape(LABEL)}" '
            rf"in domain for user gui:\s*{uid}",
            result.stderr.strip(),
        )
        is not None
    )


def check_registration(
    repository_root: Path,
    home: Path,
    uid: int,
    *,
    runner=run_command,
    timeout: float = DEFAULT_TIMEOUT,
) -> dict:
    if not 0 < timeout <= 30 or uid < 0:
        raise ValueError("invalid_check_parameters")
    repository_root, home = Path(repository_root).absolute(), Path(home).absolute()
    domain = f"gui/{uid}"
    target = f"{domain}/{LABEL}"
    configuration, expected = _configuration(repository_root, home)
    result = {
        "schemaVersion": 1,
        "contract": "fearngreed-launch-agent-registration",
        "label": LABEL,
        "domain": domain,
        "observedAt": datetime.now(timezone.utc)  # noqa: UP017 - macOS system Python 3.9
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z"),
        "registration": "unknown",
        "execution": "unknown",
        "configuration": configuration,
        "enabled": None,
        "reason": "registration_query_failed",
        "healthy": False,
    }
    service = _invoke(runner, [LAUNCHCTL, "print", target], timeout)
    disabled = _invoke(runner, [LAUNCHCTL, "print-disabled", domain], timeout)
    enabled, domain_accessible = _disabled_override(disabled)
    result["enabled"] = enabled
    fields = _service_fields(service.stdout, target) if service.returncode == 0 else None
    if service.error == "timeout" or disabled.error == "timeout":
        result["reason"] = "query_timeout"
    elif fields is not None:
        result["registration"] = "registered"
        state = fields.get("state")
        pid = str(fields.get("pid", ""))
        if state == "running" or (pid.isdigit() and int(pid) > 0):
            result["execution"] = "running"
        elif state in {"not running", "idle", "waiting"}:
            result["execution"] = "idle"
        if expected is not None:
            loaded = {
                "path": str(home / "Library/LaunchAgents" / f"{LABEL}.plist"),
                "program": expected["ProgramArguments"][0],
                "arguments": expected["ProgramArguments"],
                "working directory": expected["WorkingDirectory"],
            }
            if any(key not in fields for key in loaded):
                result["configuration"] = "unknown"
            elif any(fields[key] != value for key, value in loaded.items()):
                result["configuration"] = "mismatch"
        if not domain_accessible:
            result["reason"] = "enabled_state_unknown"
        elif enabled is False:
            result["reason"] = "service_disabled"
        elif result["configuration"] != "valid":
            result["reason"] = "configuration_" + result["configuration"]
        elif result["execution"] == "unknown":
            result["reason"] = "service_state_unrecognized"
        else:
            result["reason"] = "registered_" + result["execution"]
            result["healthy"] = True
    elif _missing_service(service, uid) and domain_accessible:
        result["registration"] = "missing"
        result["reason"] = "service_missing" if enabled else "service_disabled"
    elif not domain_accessible:
        result["reason"] = "domain_query_failed"
    elif service.returncode == 0:
        result["reason"] = "service_output_unrecognized"
    return result


def _create_configuration(repository_root: Path, home: Path) -> bool:
    expected = _expected_configuration(repository_root, home)
    destination = home / "Library/LaunchAgents" / f"{LABEL}.plist"
    destination.parent.mkdir(parents=True, exist_ok=True)
    (home / "Library/Logs/fearNgreed").mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=destination.parent, delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(plistlib.dumps(expected))
            handle.flush()
            os.fsync(handle.fileno())
        # link is atomic and fails if anything already occupies the destination.
        os.link(temporary, destination)
        return True
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def install_registration(
    repository_root: Path,
    home: Path,
    uid: int,
    *,
    runner=run_command,
    timeout: float = DEFAULT_TIMEOUT,
) -> dict:
    repository_root, home = Path(repository_root).absolute(), Path(home).absolute()
    before = check_registration(repository_root, home, uid, runner=runner, timeout=timeout)
    result = {**before, "action": "none", "outcome": "blocked"}
    if before["healthy"]:
        return {**result, "outcome": "success", "reason": "already_registered"}
    if before["registration"] != "missing" or before["enabled"] is not True:
        return result
    if before["configuration"] not in {"valid", "missing"}:
        return {**result, "reason": "configuration_" + before["configuration"]}
    action = "bootstrap_existing"
    if before["configuration"] == "missing":
        # Only a fresh installation checks presence; recovery and inspection do
        # not access credentials. All output is discarded, including on failure.
        bridge = _invoke(runner, ["with-krx-keychain", "--check"], timeout)
        if bridge.error or bridge.returncode != 0:
            return {**result, "reason": "credentials_unavailable"}
        again = check_registration(repository_root, home, uid, runner=runner, timeout=timeout)
        if (
            again["registration"] != "missing"
            or again["enabled"] is not True
            or again["configuration"] != "missing"
        ):
            return {**again, "action": "none", "outcome": "blocked", "reason": "state_changed"}
        try:
            _create_configuration(repository_root, home)
        except (OSError, ValueError, TypeError, ExpatError):
            return {**result, "reason": "configuration_create_failed"}
        action = "install_new"
    # Check bytes again without overwriting an existing installation. A racing
    # registration causes bootstrap to fail; there is deliberately no bootout.
    configuration, _ = _configuration(repository_root, home)
    if configuration != "valid":
        return {**result, "reason": "configuration_changed"}
    destination = home / "Library/LaunchAgents" / f"{LABEL}.plist"
    boot = _invoke(runner, [LAUNCHCTL, "bootstrap", f"gui/{uid}", str(destination)], timeout)
    after = check_registration(repository_root, home, uid, runner=runner, timeout=timeout)
    result = {**after, "action": action, "outcome": "failed"}
    if boot.error:
        return {**result, "outcome": "unknown", "reason": "bootstrap_outcome_uncertain"}
    if boot.returncode != 0:
        return {**result, "reason": "bootstrap_failed", "commandExitCode": boot.returncode}
    if not after["healthy"]:
        return {
            **result,
            "outcome": "unknown" if after["registration"] == "unknown" else "failed",
            "reason": "registration_not_verified",
        }
    return {**result, "outcome": "success", "reason": "registration_restored"}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("check", "install"))
    parser.add_argument("--repository-root", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args(argv)
    function = check_registration if args.command == "check" else install_registration
    result = function(args.repository_root, Path.home(), os.getuid())
    print(json.dumps(result, ensure_ascii=True, sort_keys=True))
    if args.command == "install":
        return 0 if result["outcome"] == "success" else 2 if result["outcome"] == "unknown" else 1
    return 0 if result["healthy"] else 2 if result["registration"] == "unknown" else 1


if __name__ == "__main__":
    raise SystemExit(main())
