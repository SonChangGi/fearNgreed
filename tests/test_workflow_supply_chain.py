from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"
PINNED_ACTION = re.compile(r"[^\s@]+@[0-9a-f]{40}(?:\s+#\s+v[0-9]+)?$")


def test_every_external_github_action_is_pinned_to_a_commit() -> None:
    observed: list[tuple[Path, str]] = []
    for workflow in sorted(WORKFLOWS.glob("*.yml")):
        for line in workflow.read_text(encoding="utf-8").splitlines():
            match = re.search(r"\buses:\s+(.+)$", line)
            if match is None or match.group(1).startswith("./"):
                continue
            observed.append((workflow, match.group(1)))

    assert observed
    unpinned = [
        f"{path.name}: {action}" for path, action in observed if not PINNED_ACTION.fullmatch(action)
    ]
    assert not unpinned, "unpinned Actions:\n" + "\n".join(unpinned)
