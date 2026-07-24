from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

from fearngreed.control_contract import (
    DEFAULT_CONTROL_INPUTS,
    INPUT_SCHEMA_HASH,
    classify_control_percentile,
    normalize_control_inputs,
)
from fearngreed.model import classify_percentile
from fearngreed.pipeline import BROWSER_SCENARIO_INPUTS

ROOT = Path(__file__).resolve().parents[1]
MATRIX_PATH = ROOT / "contracts" / "fear-parity-matrix.v1.json"
FIXTURE_PATH = ROOT / "tests" / "fixtures" / "fear-parity-v1.json"


def _reference_module():
    path = ROOT / "scripts" / "fear_parity_reference.py"
    spec = importlib.util.spec_from_file_location("fear_parity_reference", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_parity_matrix_covers_every_path_and_is_ready_for_python_bound_results() -> None:
    matrix = json.loads(MATRIX_PATH.read_text(encoding="utf-8"))
    path_ids = {item["id"] for item in matrix["paths"]}
    blocked = {item["id"] for item in matrix["paths"] if item["status"] == "blocked"}

    assert matrix["contract"] == "fearngreed-engine-parity-matrix"
    assert set(matrix["migrationGate"]["requiredPathIds"]) == path_ids
    assert set(matrix["migrationGate"]["blockingPathIds"]) == blocked
    assert matrix["migrationGate"]["backendMigrationAllowed"] is True
    assert blocked == set()
    assert matrix["migrationGate"]["requiredBeforeReady"] == []
    assert matrix["migrationGate"]["connectionRequirements"]


def test_visible_analysis_control_registry_is_complete_and_defaults_match_public_contract() -> None:
    matrix = json.loads(MATRIX_PATH.read_text(encoding="utf-8"))
    controls = {item["id"]: item for item in matrix["visibleControls"]}
    analysis_ids = {item["id"] for item in matrix["visibleControls"] if item["kind"] == "analysis"}

    assert analysis_ids == {
        "model",
        "backtest-policy",
        "backtest-pair",
        "backtest-cost",
        "backtest-period",
        "long-exit-percentile",
        "signal-lookback",
        "signal-minimum-r2",
        "signal-extreme-tail",
        "signal-max-holding",
        "evaluation-window",
        "event-asset",
        "event-sample",
    }
    assert controls["signal-lookback"]["default"] == BROWSER_SCENARIO_INPUTS["lookback"]["default"]
    assert (
        controls["signal-minimum-r2"]["default"] == BROWSER_SCENARIO_INPUTS["minimumR2"]["default"]
    )
    assert (
        controls["signal-extreme-tail"]["default"]
        == BROWSER_SCENARIO_INPUTS["extremeTail"]["default"]
    )
    assert (
        controls["signal-max-holding"]["default"]
        == BROWSER_SCENARIO_INPUTS["maxHolding"]["default"]
    )


def test_python_reference_fixture_is_deterministic_and_closes_the_tail_gap() -> None:
    fixture = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    module = _reference_module()
    first = module.build_reference(fixture)
    second = module.build_reference(fixture)

    assert first == second
    assert len(first["expandedInputs"]["signalRows"]) == fixture["signalSeries"]["count"]
    states = {item["percentile"]: item["pythonState"] for item in first["classification"]}
    assert states == {
        2: "extreme_fear",
        5: "fear",
        95: "greed",
        98: "extreme_greed",
    }


def test_control_contract_binds_every_visible_default_without_fallback() -> None:
    normalized = normalize_control_inputs(DEFAULT_CONTROL_INPUTS)

    assert normalized.requested == DEFAULT_CONTROL_INPUTS
    assert normalized.normalized == DEFAULT_CONTROL_INPUTS
    assert normalized.effective == DEFAULT_CONTROL_INPUTS
    assert len(normalized.config_hash) == 64
    assert len(INPUT_SCHEMA_HASH) == 64
    assert classify_control_percentile(5, 2) == "fear"
    assert classify_control_percentile(95, 2) == "greed"
    assert classify_percentile(5) == "extreme_fear"
    assert classify_percentile(95) == "extreme_greed"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("signalLookback", 59),
        ("signalMinimumR2", 0.41),
        ("signalExtremeTail", 21),
        ("signalMaxHolding", 61),
        ("longExitPercentile", 95),
        ("backtestCost", 7),
    ],
)
def test_control_contract_rejects_out_of_contract_inputs(field: str, value: object) -> None:
    inputs = {**DEFAULT_CONTROL_INPUTS, field: value}
    with pytest.raises((TypeError, ValueError)):
        normalize_control_inputs(inputs)


def test_control_contract_rejects_partial_or_inverted_custom_windows() -> None:
    missing = dict(DEFAULT_CONTROL_INPUTS)
    missing.pop("model")
    with pytest.raises(ValueError, match="missing: model"):
        normalize_control_inputs(missing)

    inverted = {
        **DEFAULT_CONTROL_INPUTS,
        "window": "custom",
        "historyStart": "2026-07-20",
        "historyEnd": "2026-07-01",
        "historyEndMode": "fixed",
    }
    with pytest.raises(ValueError, match="historyStart"):
        normalize_control_inputs(inverted)
