from __future__ import annotations

import argparse
import json
import shutil
from datetime import UTC, date, datetime
from pathlib import Path

from .live_signal import expire_live_signal_file
from .security import scan_public_files

STATIC_PATHS = ("index.html", "assets", "data", "docs", "schemas")


def build_site(root: Path, output: Path) -> None:
    if output.exists():
        shutil.rmtree(output)
    output.mkdir(parents=True)
    for name in STATIC_PATHS:
        source = root / name
        destination = output / name
        if source.is_dir():
            shutil.copytree(source, destination)
        elif source.is_file():
            shutil.copy2(source, destination)
        else:
            raise FileNotFoundError(f"missing static path: {name}")
    try:
        summary = json.loads((output / "data" / "summary.json").read_text(encoding="utf-8"))
        confirmed_data_as_of = date.fromisoformat(str(summary.get("dataAsOf")))
    except (AttributeError, OSError, ValueError):
        raise ValueError("site summary dataAsOf is invalid") from None
    expire_live_signal_file(
        output / "data" / "live-signal.json",
        observed_at=datetime.now(UTC),
        confirmed_data_as_of=confirmed_data_as_of,
    )
    private_files = [
        path for path in output.rglob("*") if "private" in path.relative_to(output).parts
    ]
    if private_files:
        raise ValueError("private reference copied into Pages artifact")
    findings = scan_public_files(root)
    if findings:
        raise ValueError("credential material detected in public files")
    (output / ".nojekyll").write_text("", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Build a bounded GitHub Pages artifact")
    parser.add_argument("--output", type=Path, default=Path("dist"))
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[2]
    output = args.output if args.output.is_absolute() else root / args.output
    build_site(root, output)
    try:
        display_path = output.relative_to(root)
    except ValueError:
        display_path = output
    print(f"Pages artifact ready: {display_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
