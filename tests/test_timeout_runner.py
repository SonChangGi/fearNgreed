from __future__ import annotations

import os
import runpy
import signal
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / "scripts" / "run-with-timeout"


def test_timeout_runner_returns_child_status() -> None:
    result = subprocess.run(
        [sys.executable, RUNNER, "5", sys.executable, "-c", "raise SystemExit(7)"],
        check=False,
        timeout=10,
    )

    assert result.returncode == 7


@pytest.mark.parametrize(
    ("monotonic_advance", "wall_advance", "child_finishes"),
    [
        (0.0, 3600.0, False),  # macOS sleep: only the wall clock advances.
        (0.0, 3600.0, True),  # The child is scheduled first after waking.
        (20.0, -3600.0, False),  # A wall-clock rollback cannot extend the budget.
    ],
)
def test_timeout_runner_bounds_suspended_and_clock_changed_runs(
    monkeypatch, monotonic_advance, wall_advance, child_finishes
) -> None:
    runner = runpy.run_path(str(RUNNER))
    main = runner["main"]
    clock = {"monotonic": 100.0, "wall": 1_000.0}
    sent_signals = []

    class Child:
        pid = 12345

        def wait(self, timeout=None):
            if sent_signals:
                return -signal.SIGTERM
            clock["monotonic"] += monotonic_advance
            clock["wall"] += wall_advance
            if child_finishes:
                return 0
            raise subprocess.TimeoutExpired("fake-child", timeout)

    def start_child(command, *, start_new_session):
        assert command == ["fake-child"]
        assert start_new_session is True
        return Child()

    monkeypatch.setitem(
        main.__globals__,
        "time",
        SimpleNamespace(monotonic=lambda: clock["monotonic"], time=lambda: clock["wall"]),
    )
    monkeypatch.setattr(subprocess, "Popen", start_child)
    monkeypatch.setattr(os, "killpg", lambda pid, signum: sent_signals.append((pid, signum)))

    assert main([str(RUNNER), "10", "fake-child"]) == 124
    assert sent_signals == [(Child.pid, signal.SIGTERM)]


def test_timeout_runner_terminates_a_hung_process_group() -> None:
    started = time.monotonic()
    result = subprocess.run(
        [sys.executable, RUNNER, "0.1", sys.executable, "-c", "import time; time.sleep(60)"],
        check=False,
        timeout=5,
    )

    assert result.returncode == 124
    assert time.monotonic() - started < 3


def test_timeout_runner_forwards_external_termination_and_reaps_children(tmp_path) -> None:
    ready = tmp_path / "ready"
    survivor = tmp_path / "survivor"
    child_code = (
        "import os,pathlib,subprocess,sys,time;"
        "grandchild=subprocess.Popen([sys.executable,'-c',"
        '"import pathlib,time;time.sleep(1);'
        f"pathlib.Path({str(survivor)!r}).write_text('alive')\"]);"
        f"pathlib.Path({str(ready)!r}).write_text(str(os.getpid()));"
        "time.sleep(60)"
    )
    runner = subprocess.Popen([sys.executable, RUNNER, "60", sys.executable, "-c", child_code])
    child_group = None
    try:
        deadline = time.monotonic() + 5
        while not ready.exists() and time.monotonic() < deadline:
            time.sleep(0.02)
        assert ready.exists()
        child_group = int(ready.read_text(encoding="utf-8"))

        runner.send_signal(signal.SIGTERM)

        assert runner.wait(timeout=5) == 128 + signal.SIGTERM
        time.sleep(1.2)
        assert not survivor.exists()
    finally:
        if runner.poll() is None:
            runner.kill()
            runner.wait(timeout=5)
        if child_group is not None:
            try:
                os.killpg(child_group, signal.SIGKILL)
            except ProcessLookupError:
                pass
