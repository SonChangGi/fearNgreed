from __future__ import annotations

import json
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker

ROOT = Path(__file__).resolve().parents[1]


def test_committed_summary_matches_public_schema() -> None:
    schema = json.loads((ROOT / "schemas" / "summary.schema.json").read_text())
    summary = json.loads((ROOT / "data" / "summary.json").read_text())
    Draft202012Validator(schema, format_checker=FormatChecker()).validate(summary)


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
