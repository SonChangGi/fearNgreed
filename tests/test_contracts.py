from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator, FormatChecker
from jsonschema.exceptions import ValidationError

ROOT = Path(__file__).resolve().parents[1]


def test_committed_summary_matches_public_schema() -> None:
    schema = json.loads((ROOT / "schemas" / "summary.schema.json").read_text())
    summary = json.loads((ROOT / "data" / "summary.json").read_text())
    Draft202012Validator(schema, format_checker=FormatChecker()).validate(summary)


@pytest.fixture
def summary_contract() -> tuple[dict, dict]:
    schema = json.loads((ROOT / "schemas" / "summary.schema.json").read_text())
    summary = json.loads((ROOT / "data" / "summary.json").read_text())
    return schema, summary


def test_summary_schema_requires_methodology_version(summary_contract) -> None:
    schema, committed = summary_contract
    candidate = deepcopy(committed)
    candidate.pop("methodologyVersion")

    with pytest.raises(ValidationError):
        Draft202012Validator(schema, format_checker=FormatChecker()).validate(candidate)


def test_summary_schema_rejects_out_of_range_percentile(summary_contract) -> None:
    schema, committed = summary_contract
    candidate = deepcopy(committed)
    candidate["primaryEntities"][0]["sentimentPercentile"] = 100.1

    with pytest.raises(ValidationError):
        Draft202012Validator(schema, format_checker=FormatChecker()).validate(candidate)


def test_summary_schema_rejects_unknown_nested_properties(summary_contract) -> None:
    schema, committed = summary_contract
    candidate = deepcopy(committed)
    candidate["status"]["quietlyIgnored"] = True

    with pytest.raises(ValidationError):
        Draft202012Validator(schema, format_checker=FormatChecker()).validate(candidate)


def test_private_reference_is_ignored() -> None:
    gitignore = (ROOT / ".gitignore").read_text()
    assert "/references/private/" in gitignore


def test_public_artifacts_contain_no_secret_assignments() -> None:
    forbidden = ("KRX_API_KEY=", "KRX_ID=", "KRX_PW=", "password-canary", "FAKE_KRX_SECRET")
    for directory in (ROOT / "data", ROOT / "assets"):
        for path in directory.rglob("*"):
            if path.is_file():
                text = path.read_text(encoding="utf-8")
                assert not any(token in text for token in forbidden), path
