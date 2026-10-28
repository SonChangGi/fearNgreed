"""Exercise registration diagnostics without contacting launchd or the Keychain."""

from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import plistlib
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "scripts" / "official-refresh-registration.py"
SPEC = importlib.util.spec_from_file_location("official_refresh_registration", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
REGISTRATION = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = REGISTRATION
SPEC.loader.exec_module(REGISTRATION)

LABEL = "com.sonchanggi.fearngreed.official-refresh"
UID = 501
DOMAIN = f"gui/{UID}"
TARGET = f"{DOMAIN}/{LABEL}"
FAKE_SECRET = "fake-krx-token-do-not-emit-938284"


class FakeLaunchctl:
    """Only the exact service and disabled registry may be inspected."""

    def __init__(self, fixture: RegistrationTests, *, registered: bool = True):
        self.fixture = fixture
        self.registered = registered
        self.state = "not running"
        self.enabled = True
        self.override_format = "word"
        self.service_result = None
        self.service_after_bootstrap = None
        self.disabled_result = None
        self.bootstrap_result = REGISTRATION.CommandResult(0)
        self.register_after_bootstrap = True
        self.register_after_uncertain_bootstrap = False
        self.bridge_result = None
        self.bridge_callback = None
        self.calls: list[tuple[list[str], float]] = []

    def __call__(self, argv: list[str], *, timeout: float):
        self.calls.append((list(argv), timeout))
        if argv == ["/bin/launchctl", "print", TARGET]:
            if self.service_result is not None:
                return self.service_result
            if self.registered:
                return REGISTRATION.CommandResult(0, self.fixture.service_output(self.state))
            return REGISTRATION.CommandResult(
                113,
                stderr=(
                    f'Bad request.\nCould not find service "{LABEL}" '
                    f"in domain for user gui: {UID}\n"
                ),
            )
        if argv == ["/bin/launchctl", "print-disabled", DOMAIN]:
            if self.disabled_result is not None:
                return self.disabled_result
            flag = "enabled" if self.enabled else "disabled"
            if self.override_format == "boolean":
                flag = "false" if self.enabled else "true"
            return REGISTRATION.CommandResult(
                0, f'disabled services = {{\n\t"{LABEL}" => {flag}\n}}\n'
            )
        if argv == ["/bin/launchctl", "bootstrap", DOMAIN, str(self.fixture.destination)]:
            if self.bootstrap_result.returncode == 0 and self.register_after_bootstrap:
                self.registered = True
            if self.bootstrap_result.error and self.register_after_uncertain_bootstrap:
                self.registered = True
            if self.service_after_bootstrap is not None:
                self.service_result = self.service_after_bootstrap
            return self.bootstrap_result
        if argv == ["with-krx-keychain", "--check"] and self.bridge_result is not None:
            if self.bridge_callback is not None:
                self.bridge_callback()
            return self.bridge_result
        raise AssertionError(f"Unexpected subprocess request: {argv!r}")

    def mutations(self) -> list[list[str]]:
        return [
            argv
            for argv, _ in self.calls
            if argv[:2] not in (["/bin/launchctl", "print"], ["/bin/launchctl", "print-disabled"])
        ]


class RegistrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="fear registration test-")
        self.addCleanup(self.temporary.cleanup)
        base = Path(self.temporary.name)
        self.repository = base / "repository"
        self.home = base / "home"
        (self.repository / "scripts").mkdir(parents=True)
        (self.repository / "automation").mkdir()
        runner = self.repository / "scripts" / "run-official-refresh"
        runner.write_text("#!/bin/zsh\nexit 0\n", encoding="utf-8")
        runner.chmod(0o755)
        template = (ROOT / "automation" / f"{LABEL}.plist").read_text(encoding="utf-8")
        (self.repository / "automation" / f"{LABEL}.plist").write_text(template, encoding="utf-8")
        self.destination = self.home / "Library" / "LaunchAgents" / f"{LABEL}.plist"
        self.destination.parent.mkdir(parents=True)
        rendered = template.replace("__REPOSITORY_ROOT__", str(self.repository)).replace(
            "__HOME__", str(self.home)
        )
        self.destination.write_text(rendered, encoding="utf-8")

    def service_output(self, state: str = "not running") -> str:
        pid = "\n\tpid = 12345" if state == "running" else ""
        return (
            f"{TARGET} = {{\n"
            "\tactive count = 0\n"
            f"\tpath = {self.destination}\n"
            "\ttype = LaunchAgent\n"
            f"\tstate = {state}\n"
            "\tprogram = /usr/bin/caffeinate\n"
            "\targuments = {\n"
            "\t\t/usr/bin/caffeinate\n"
            "\t\t-i\n"
            "\t\t/bin/zsh\n"
            f"\t\t{self.repository}/scripts/run-official-refresh\n"
            "\t}\n"
            f"\tworking directory = {self.repository}\n"
            f"\tenvironment = {{\n\t\tUNRELATED_SECRET = {FAKE_SECRET}\n\t}}\n"
            f"\truns = 4{pid}\n"
            "\tlast exit code = 0\n"
            "}\n"
        )

    def snapshot(self) -> dict[str, tuple[bytes, int, int]]:
        base = Path(self.temporary.name)
        return {
            str(path.relative_to(base)): (
                path.read_bytes(),
                path.stat().st_mtime_ns,
                path.stat().st_mode,
            )
            for path in base.rglob("*")
            if path.is_file()
        }

    def check(self, fake: FakeLaunchctl) -> dict:
        before = self.snapshot()
        output = io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
            result = REGISTRATION.check_registration(
                self.repository, self.home, UID, runner=fake, timeout=2.0
            )
        self.assertEqual(before, self.snapshot(), "health check must not write files")
        self.assertEqual([], fake.mutations(), "health check must not mutate launchd")
        self.assertNotIn(FAKE_SECRET, json.dumps(result))
        self.assertNotIn(FAKE_SECRET, output.getvalue())
        self.assertTrue(all(0 < timeout <= 2.0 for _, timeout in fake.calls))
        return result

    def install(self, fake: FakeLaunchctl) -> dict:
        output = io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
            result = REGISTRATION.install_registration(
                self.repository, self.home, UID, runner=fake, timeout=2.0
            )
        self.assertNotIn(FAKE_SECRET, json.dumps(result))
        self.assertNotIn(FAKE_SECRET, output.getvalue())
        self.assertTrue(all(0 < timeout <= 2.0 for _, timeout in fake.calls))
        return result

    def test_registered_idle_is_healthy(self) -> None:
        result = self.check(FakeLaunchctl(self))
        self.assertEqual("registered", result["registration"])
        self.assertEqual("idle", result["execution"])
        self.assertEqual("valid", result["configuration"])
        self.assertIs(True, result["enabled"])
        self.assertIs(True, result["healthy"])

    def test_registered_running_is_healthy(self) -> None:
        fake = FakeLaunchctl(self)
        fake.state = "running"
        result = self.check(fake)
        self.assertEqual("registered", result["registration"])
        self.assertEqual("running", result["execution"])
        self.assertIs(True, result["healthy"])

    def test_enabled_existing_plist_does_not_hide_missing_registration(self) -> None:
        result = self.check(FakeLaunchctl(self, registered=False))
        self.assertEqual("missing", result["registration"])
        self.assertIs(True, result["enabled"])
        self.assertEqual("valid", result["configuration"])
        self.assertIs(False, result["healthy"])

    def test_disabled_registered_service_is_not_healthy(self) -> None:
        for override_format in ["word", "boolean"]:
            with self.subTest(override_format=override_format):
                fake = FakeLaunchctl(self)
                fake.enabled = False
                fake.override_format = override_format
                result = self.check(fake)
                self.assertEqual("registered", result["registration"])
                self.assertIs(False, result["enabled"])
                self.assertIs(False, result["healthy"])

    def test_boolean_enabled_override_is_healthy(self) -> None:
        fake = FakeLaunchctl(self)
        fake.override_format = "boolean"
        result = self.check(fake)
        self.assertIs(True, result["enabled"])
        self.assertIs(True, result["healthy"])

    def test_unrecognized_or_duplicate_target_override_is_not_default_enabled(self) -> None:
        responses = [
            f'disabled services = {{\n"{LABEL}" => unexpected\n}}\n',
            f'disabled services = {{\n"{LABEL}" => enabled\n"{LABEL}" => disabled\n}}\n',
        ]
        for response in responses:
            with self.subTest(response=response):
                fake = FakeLaunchctl(self, registered=False)
                fake.disabled_result = REGISTRATION.CommandResult(0, response)
                result = self.check(fake)
                self.assertEqual("unknown", result["registration"])
                self.assertIsNone(result["enabled"])
                self.assertIs(False, result["healthy"])

    def test_service_errors_never_imply_missing(self) -> None:
        responses = [
            REGISTRATION.CommandResult(1, stderr=f"Operation not permitted {FAKE_SECRET}"),
            REGISTRATION.CommandResult(None, error="timeout"),
            REGISTRATION.CommandResult(None, error="os_error"),
            REGISTRATION.CommandResult(113, stderr="Could not find domain for user gui: 501"),
            REGISTRATION.CommandResult(0, stdout="unrecognized success payload"),
            REGISTRATION.CommandResult(
                113, stderr='Could not find service "unrelated-service" in domain for user gui: 501'
            ),
            REGISTRATION.CommandResult(
                113, stderr=f'Could not find service "{LABEL}" in domain for user gui: 999'
            ),
            REGISTRATION.CommandResult(
                113,
                stderr=(
                    f'Bad request.\nCould not find service "{LABEL}" '
                    "in domain for user gui: 501\nOperation not permitted\n"
                ),
            ),
        ]
        for response in responses:
            with self.subTest(response=response):
                fake = FakeLaunchctl(self)
                fake.service_result = response
                result = self.check(fake)
                self.assertEqual("unknown", result["registration"])
                self.assertIs(False, result["healthy"])

    def test_missing_service_requires_accessible_recognized_domain(self) -> None:
        responses = [
            REGISTRATION.CommandResult(1, stderr="Operation not permitted"),
            REGISTRATION.CommandResult(None, error="timeout"),
            REGISTRATION.CommandResult(113, stderr="Could not find domain for user gui: 501"),
            REGISTRATION.CommandResult(0, stdout="unrecognized domain response"),
        ]
        for response in responses:
            with self.subTest(response=response):
                fake = FakeLaunchctl(self, registered=False)
                fake.disabled_result = response
                result = self.check(fake)
                self.assertEqual("unknown", result["registration"])
                self.assertIs(False, result["healthy"])

    def test_stale_success_and_failure_status_do_not_override_registration(self) -> None:
        status_path = self.home / "Library" / "Logs" / "fearNgreed" / "official-refresh-status.json"
        status_path.parent.mkdir(parents=True)
        for status in ["published", "failed"]:
            with self.subTest(status=status):
                status_path.write_text(
                    json.dumps(
                        {
                            "observedAt": "2026-10-23T11:42:47Z",
                            "status": status,
                            "reason": FAKE_SECRET,
                        }
                    ),
                    encoding="utf-8",
                )
                result = self.check(FakeLaunchctl(self, registered=False))
                self.assertEqual("missing", result["registration"])
                self.assertIs(False, result["healthy"])

    def test_malformed_or_missing_plist_is_not_healthy(self) -> None:
        for contents in [b"invalid plist", None]:
            with self.subTest(contents=contents):
                if contents is None:
                    self.destination.unlink(missing_ok=True)
                else:
                    self.destination.write_bytes(contents)
                result = self.check(FakeLaunchctl(self))
                self.assertNotEqual("valid", result["configuration"])
                self.assertIs(False, result["healthy"])

    def test_changed_schedules_run_at_load_and_keepalive_fail_configuration(self) -> None:
        original = self.destination.read_bytes()
        variants = [
            {"RunAtLoad": True},
            {"KeepAlive": True},
            {"StartCalendarInterval": [{"Weekday": 3, "Hour": 9, "Minute": 0}]},
            {"WorkingDirectory": "/unrelated/repository"},
            {"ProgramArguments": ["/bin/zsh", "/unrelated/run-refresh"]},
        ]
        for changes in variants:
            with self.subTest(changes=changes):
                payload = plistlib.loads(original)
                payload.update(changes)
                self.destination.write_bytes(plistlib.dumps(payload))
                result = self.check(FakeLaunchctl(self))
                self.assertNotEqual("valid", result["configuration"])
                self.assertIs(False, result["healthy"])

    def test_loaded_config_mismatch_is_not_hidden_by_valid_disk_plist(self) -> None:
        output = self.service_output()
        variants = [
            output.replace(f"path = {self.destination}", "path = /other/agent.plist"),
            output.replace("program = /usr/bin/caffeinate", "program = /other/program"),
            output.replace(
                f"working directory = {self.repository}", "working directory = /other/root"
            ),
            output.replace(
                f"\t\t{self.repository}/scripts/run-official-refresh", "\t\t/other/runner"
            ),
        ]
        for service_output in variants:
            with self.subTest(service_output=service_output):
                fake = FakeLaunchctl(self)
                fake.service_result = REGISTRATION.CommandResult(0, service_output)
                result = self.check(fake)
                self.assertEqual("registered", result["registration"])
                self.assertEqual("mismatch", result["configuration"])
                self.assertIs(False, result["healthy"])

    def test_registered_repeated_install_is_a_no_op(self) -> None:
        fake = FakeLaunchctl(self)
        before = self.snapshot()
        for _ in range(2):
            result = self.install(fake)
            self.assertEqual("success", result["outcome"])
            self.assertEqual("none", result["action"])
            self.assertEqual(before, self.snapshot())
        self.assertEqual([], fake.mutations())

    def test_registered_running_install_does_not_interrupt(self) -> None:
        fake = FakeLaunchctl(self)
        fake.state = "running"
        before = self.snapshot()
        result = self.install(fake)
        self.assertEqual("success", result["outcome"])
        self.assertEqual("none", result["action"])
        self.assertEqual(before, self.snapshot())
        self.assertEqual([], fake.mutations())

    def test_missing_valid_install_bootstraps_existing_file_once_then_verifies(self) -> None:
        fake = FakeLaunchctl(self, registered=False)
        before = self.snapshot()
        result = self.install(fake)
        expected = ["/bin/launchctl", "bootstrap", DOMAIN, str(self.destination)]
        self.assertEqual([expected], fake.mutations())
        self.assertEqual("success", result["outcome"])
        self.assertEqual("bootstrap_existing", result["action"])
        self.assertEqual("registered", result["registration"])
        self.assertEqual(before, self.snapshot(), "existing plist must not be rewritten")
        commands = [argv for argv, _ in fake.calls]
        self.assertIn(["/bin/launchctl", "print", TARGET], commands[commands.index(expected) + 1 :])

    def test_bootstrap_success_without_registration_is_reported_as_failure(self) -> None:
        fake = FakeLaunchctl(self, registered=False)
        fake.register_after_bootstrap = False
        result = self.install(fake)
        self.assertNotEqual("success", result["outcome"])
        self.assertEqual("missing", result["registration"])
        self.assertEqual(1, len(fake.mutations()))

    def test_bootstrap_success_with_unavailable_readback_is_unknown(self) -> None:
        fake = FakeLaunchctl(self, registered=False)
        fake.service_after_bootstrap = REGISTRATION.CommandResult(None, error="timeout")
        result = self.install(fake)
        self.assertEqual("unknown", result["outcome"])
        self.assertEqual("unknown", result["registration"])
        self.assertEqual(1, len(fake.mutations()))

    def test_failed_or_uncertain_bootstrap_is_not_retried_or_escalated(self) -> None:
        for response in [
            REGISTRATION.CommandResult(5, stderr=f"Input/output error {FAKE_SECRET}"),
            REGISTRATION.CommandResult(None, error="timeout"),
        ]:
            with self.subTest(response=response):
                fake = FakeLaunchctl(self, registered=False)
                fake.bootstrap_result = response
                before = self.snapshot()
                result = self.install(fake)
                self.assertNotEqual("success", result["outcome"])
                self.assertEqual(before, self.snapshot())
                self.assertEqual(1, len(fake.mutations()))
                self.assertEqual("bootstrap", fake.mutations()[0][1])

    def test_unknown_registration_blocks_all_installer_changes(self) -> None:
        fake = FakeLaunchctl(self)
        fake.service_result = REGISTRATION.CommandResult(None, error="timeout")
        before = self.snapshot()
        result = self.install(fake)
        self.assertEqual("unknown", result["registration"])
        self.assertNotEqual("success", result["outcome"])
        self.assertEqual(before, self.snapshot())
        self.assertEqual([], fake.mutations())

    def test_uncertain_bootstrap_reads_registration_without_retry(self) -> None:
        fake = FakeLaunchctl(self, registered=False)
        fake.bootstrap_result = REGISTRATION.CommandResult(None, error="timeout")
        fake.register_after_uncertain_bootstrap = True
        result = self.install(fake)
        self.assertEqual("unknown", result["outcome"])
        self.assertEqual("registered", result["registration"])
        self.assertEqual(1, len(fake.mutations()))

    def test_fresh_install_checks_presence_and_creates_original_schedule(self) -> None:
        self.destination.unlink()
        fake = FakeLaunchctl(self, registered=False)
        fake.bridge_result = REGISTRATION.CommandResult(0, FAKE_SECRET)
        result = self.install(fake)
        self.assertEqual("success", result["outcome"])
        self.assertEqual("install_new", result["action"])
        self.assertEqual("registered", result["registration"])
        self.assertEqual(
            [
                ["with-krx-keychain", "--check"],
                ["/bin/launchctl", "bootstrap", DOMAIN, str(self.destination)],
            ],
            fake.mutations(),
        )
        installed = plistlib.loads(self.destination.read_bytes())
        template = plistlib.loads((self.repository / "automation" / f"{LABEL}.plist").read_bytes())
        self.assertEqual(template["StartCalendarInterval"], installed["StartCalendarInterval"])
        self.assertIs(False, installed["RunAtLoad"])
        self.assertNotIn("KeepAlive", installed)
        self.assertNotIn(FAKE_SECRET, self.destination.read_text(encoding="utf-8"))

    def test_failed_presence_check_does_not_create_configuration(self) -> None:
        self.destination.unlink()
        fake = FakeLaunchctl(self, registered=False)
        fake.bridge_result = REGISTRATION.CommandResult(1, stderr=FAKE_SECRET)
        before = self.snapshot()
        result = self.install(fake)
        self.assertNotEqual("success", result["outcome"])
        self.assertEqual(before, self.snapshot())
        self.assertEqual([["with-krx-keychain", "--check"]], fake.mutations())

    def test_fresh_install_preserves_configuration_created_during_presence_check(self) -> None:
        self.destination.unlink()
        fake = FakeLaunchctl(self, registered=False)
        fake.bridge_result = REGISTRATION.CommandResult(0)
        user_content = b"user configuration appeared concurrently"
        fake.bridge_callback = lambda: self.destination.write_bytes(user_content)
        result = self.install(fake)
        self.assertNotEqual("success", result["outcome"])
        self.assertEqual(user_content, self.destination.read_bytes())
        self.assertEqual([["with-krx-keychain", "--check"]], fake.mutations())

    def test_mismatched_existing_configuration_is_preserved(self) -> None:
        payload = plistlib.loads(self.destination.read_bytes())
        payload["WorkingDirectory"] = "/user/custom/repository"
        self.destination.write_bytes(plistlib.dumps(payload))
        for registered in [True, False]:
            with self.subTest(registered=registered):
                fake = FakeLaunchctl(self, registered=registered)
                before = self.snapshot()
                result = self.install(fake)
                self.assertNotEqual("success", result["outcome"])
                self.assertEqual(before, self.snapshot())
                self.assertEqual([], fake.mutations())

    def test_disabled_service_is_preserved_without_enable(self) -> None:
        for registered in [True, False]:
            with self.subTest(registered=registered):
                fake = FakeLaunchctl(self, registered=registered)
                fake.enabled = False
                before = self.snapshot()
                result = self.install(fake)
                self.assertNotEqual("success", result["outcome"])
                self.assertEqual(before, self.snapshot())
                self.assertEqual([], fake.mutations())


class CommandBoundaryTests(unittest.TestCase):
    def test_timeout_and_permission_failures_discard_raw_exception_payload(self) -> None:
        failures = [
            (subprocess.TimeoutExpired(["unexecuted"], 2, output=FAKE_SECRET), "timeout"),
            (PermissionError(FAKE_SECRET), "os_error"),
        ]
        for exception, expected_error in failures:
            with self.subTest(expected_error=expected_error):
                with mock.patch.object(
                    REGISTRATION.subprocess, "run", side_effect=exception
                ) as run:
                    result = REGISTRATION.run_command(["unexecuted"], timeout=2.0)
                self.assertEqual(expected_error, result.error)
                self.assertIsNone(result.returncode)
                self.assertNotIn(FAKE_SECRET, repr(result))
                self.assertEqual(2.0, run.call_args.kwargs["timeout"])
                self.assertIs(True, run.call_args.kwargs["capture_output"])


if __name__ == "__main__":
    unittest.main()
