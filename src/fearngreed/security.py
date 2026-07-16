from __future__ import annotations

import os
from pathlib import Path


def scan_public_files(root: Path) -> list[str]:
    """Return relative public paths containing a credential or known test canary."""
    secret_values = [
        value.encode() for name in ("KRX_API_KEY", "KRX_ID", "KRX_PW") if (value := os.getenv(name))
    ]
    fixed = [
        b"FAKE_KRX_SECRET_CANARY",
        b"login-id-canary",
        b"password-canary",
        b"KRX_API_KEY=",
        b"KRX_ID=",
        b"KRX_PW=",
    ]
    findings: list[str] = []
    for folder in ("assets", "data", "docs", "schemas", "dist"):
        path = root / folder
        if not path.exists():
            continue
        for file in path.rglob("*"):
            if not file.is_file():
                continue
            payload = file.read_bytes()
            if any(token and token in payload for token in [*fixed, *secret_values]):
                findings.append(str(file.relative_to(root)))
    index = root / "index.html"
    if index.exists():
        payload = index.read_bytes()
        if any(token and token in payload for token in [*fixed, *secret_values]):
            findings.append("index.html")
    return findings
