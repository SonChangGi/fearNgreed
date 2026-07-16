from __future__ import annotations

import argparse
import shutil
from pathlib import Path

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
    print(f"Pages artifact ready: {output.relative_to(root)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
