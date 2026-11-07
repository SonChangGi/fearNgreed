from __future__ import annotations

import json
from pathlib import Path

import pytest

from fearngreed.security import scan_public_files
from fearngreed.site import build_site, main


def test_site_builder_excludes_private_reference(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[1]
    output = tmp_path / "site"
    build_site(root, output)
    assert (output / "index.html").exists()
    assert (output / "data" / "summary.json").exists()
    assert (output / "data" / "live-signal.json").exists()
    live_signal = json.loads((output / "data" / "live-signal.json").read_text())
    assert live_signal["quality"]["tradeEligible"] is False
    assert live_signal["quality"]["reasons"] == ["provisional_signal_expired"]
    assert not (output / "references").exists()
    assert not list(output.rglob("source.pdf"))


def test_public_files_have_no_credentials() -> None:
    assert scan_public_files(Path(__file__).resolve().parents[1]) == []


def test_site_cli_accepts_an_absolute_output_path(tmp_path, monkeypatch, capsys) -> None:
    output = tmp_path / "absolute-site"
    monkeypatch.setattr("sys.argv", ["fearngreed.site", "--output", str(output)])

    assert main() == 0
    assert (output / "index.html").exists()
    assert capsys.readouterr().out.strip() == f"Pages artifact ready: {output}"


def test_site_builder_rejects_old_design_before_replacing_artifact(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    html = (Path(__file__).resolve().parents[1] / "index.html").read_text()
    (source / "index.html").write_text(html.replace("startup/1.2.0/", "startup/1.1.0/"))
    output = tmp_path / "site"
    output.mkdir()
    sentinel = output / "last-good.txt"
    sentinel.write_text("keep")
    with pytest.raises(ValueError, match="wrong stylesheet pin"):
        build_site(source, output)
    assert sentinel.read_text() == "keep"
