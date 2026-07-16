from __future__ import annotations

from pathlib import Path

from fearngreed.security import scan_public_files
from fearngreed.site import build_site


def test_site_builder_excludes_private_reference(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[1]
    output = tmp_path / "site"
    build_site(root, output)
    assert (output / "index.html").exists()
    assert (output / "data" / "summary.json").exists()
    assert not (output / "references").exists()
    assert not list(output.rglob("source.pdf"))


def test_public_files_have_no_credentials() -> None:
    assert scan_public_files(Path(__file__).resolve().parents[1]) == []
