from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / "scripts" / "run-with-timeout"


def test_timeout_runner_returns_child_status() -> None:
    result = subprocess.run(
        [sys.executable, RUNNER, "5", sys.executable, "-c", "raise SystemExit(7)"],
        check=False,
        timeout=10,
    )

    assert result.returncode == 7


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
