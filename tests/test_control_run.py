from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

import pytest

from fearngreed.control_contract import (
    CONFIG_HASH_ALGORITHM,
    DEFAULT_CONTROL_INPUTS,
    INPUT_SCHEMA_HASH,
    INPUT_SCHEMA_VERSION,
    canonical_sha256,
)
from fearngreed.control_run import (
    RESULT_IDENTITY_VERSION,
    ControlledRunError,
    execute_controlled_run,
    validate_control_binding,
)
from fearngreed.model import classify_percentile

ROOT = Path(__file__).resolve().parents[1]
CODE_VERSION = "github:SonChangGi/fearNgreed@" + "a" * 40


def _root(tmp_path: Path) -> Path:
    data = tmp_path / "repo" / "data"
    data.mkdir(parents=True)
    shutil.copyfile(ROOT / "data" / "history.json", data / "history.json")
    return tmp_path / "repo"


def _binding(inputs: dict[str, object], run_id: str):
    return validate_control_binding(
        run_id=run_id,
        input_schema_version=INPUT_SCHEMA_VERSION,
        input_schema_hash=INPUT_SCHEMA_HASH,
        config_hash_algorithm=CONFIG_HASH_ALGORITHM,
        config_hash=canonical_sha256(inputs),
        normalized_inputs=inputs,
        allow_fallback=False,
    )


def test_controlled_run_binds_every_input_and_exact_artifact_bytes(tmp_path: Path) -> None:
    root = _root(tmp_path)
    inputs = dict(DEFAULT_CONTROL_INPUTS)
    result = execute_controlled_run(
        control_inputs=inputs,
        binding=_binding(inputs, "run-fear-default-0001"),
        code_version=CODE_VERSION,
        root=root,
        output_dir=root / "outputs" / "controlled-run",
    )

    artifact_path = result["artifactPath"]
    manifest_path = result["manifestPath"]
    artifact_bytes = artifact_path.read_bytes()
    artifact = json.loads(artifact_bytes)
    manifest = json.loads(manifest_path.read_bytes())

    assert artifact["requestedInputs"] == inputs
    assert artifact["normalizedInputs"] == inputs
    assert artifact["effectiveInputs"] == inputs
    assert artifact["resultKey"] == canonical_sha256(artifact["resultIdentity"]["keyParts"])
    assert artifact["resultIdentity"]["identityVersion"] == RESULT_IDENTITY_VERSION
    assert artifact["resultIdentity"]["keyParts"]["binding"]["effectiveConfigHash"] == (
        canonical_sha256(inputs)
    )
    assert artifact["resultIdentity"]["keyParts"]["codeIdentity"] == {
        "repository": "SonChangGi/fearNgreed",
        "commitSha": "a" * 40,
        "methodologyVersion": "fear-flow-v5",
    }
    assert manifest["artifact"]["sha256"] == hashlib.sha256(artifact_bytes).hexdigest()
    assert manifest["artifact"]["byteSize"] == len(artifact_bytes)
    assert manifest["artifact"]["url"].endswith(
        f"/{artifact['resultIdentity']['keyParts']['binding']['runId']}/"
        f"{artifact['resultKey']}.json"
    )
    assert set(manifest["payload"]["summary"]) == {
        "signalDate",
        "signalState",
        "signalPercentile",
        "eventAsset",
        "eventSample",
        "eventCount",
        "strategyPosition",
        "strategyStatus",
        "strategyTotalReturn",
        "methodologyVersion",
    }
    assert manifest["fallbackUsed"] is False
    assert manifest["fallbacks"] == []
    assert artifact["event"]["bootstrap"]["method"] == "moving_block"
    assert artifact["strategy"]["longCash"]["calculationSource"] == ("python_verified_actual_etfs")
    assert artifact["strategy"]["longInverse"]["calculationSource"] == (
        "python_verified_actual_etfs"
    )


def test_extreme_tail_changes_bound_python_result_without_changing_core_rule(
    tmp_path: Path,
) -> None:
    root = _root(tmp_path)
    default = dict(DEFAULT_CONTROL_INPUTS)
    wider_tail = {**default, "signalExtremeTail": 15}

    first = execute_controlled_run(
        control_inputs=default,
        binding=_binding(default, "run-fear-tail-000001"),
        code_version=CODE_VERSION,
        root=root,
        output_dir=root / "outputs" / "first",
    )["artifact"]
    second = execute_controlled_run(
        control_inputs=wider_tail,
        binding=_binding(wider_tail, "run-fear-tail-000002"),
        code_version=CODE_VERSION,
        root=root,
        output_dir=root / "outputs" / "second",
    )["artifact"]

    assert first["summary"]["signalState"] == "fear"
    assert second["summary"]["signalState"] == "extreme_fear"
    assert first["resultKey"] != second["resultKey"]
    assert classify_percentile(5) == "extreme_fear"
    assert classify_percentile(95) == "extreme_greed"


def test_control_binding_rejects_drift_and_fallback() -> None:
    inputs = dict(DEFAULT_CONTROL_INPUTS)
    common = {
        "run_id": "run-fear-invalid-001",
        "input_schema_version": INPUT_SCHEMA_VERSION,
        "input_schema_hash": INPUT_SCHEMA_HASH,
        "config_hash_algorithm": CONFIG_HASH_ALGORITHM,
        "config_hash": canonical_sha256(inputs),
        "normalized_inputs": inputs,
    }
    with pytest.raises(ControlledRunError, match="input schema"):
        validate_control_binding(**{**common, "input_schema_hash": "0" * 64}, allow_fallback=False)
    with pytest.raises(ControlledRunError, match="allowFallback=false"):
        validate_control_binding(**common, allow_fallback=True)
