from __future__ import annotations

import json
from datetime import date

import pandas as pd

from fearngreed.refresh import _load_incremental_seed, _merge_frames


def test_incremental_seed_freezes_everything_before_latest_five_sessions(tmp_path):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    dates = pd.bdate_range("2025-01-02", periods=260)
    rows = []
    for index, timestamp in enumerate(dates):
        value = 100.0 + index
        rows.append(
            {
                "date": timestamp.date().isoformat(),
                "kospiClose": value,
                "flowShare": -0.01 + index / 100_000,
                "rawFlowTrillion": -0.2 + index / 10_000,
                "sourceHash": f"hash-{index}",
                "p069500Open": value * 2,
                "p069500Close": value * 2.01,
                "p226490Open": value,
                "p226490Close": value * 1.01,
            }
        )
    history = {
        "methodologyVersion": "fear-flow-v1",
        "dataAsOf": rows[-1]["date"],
        "fixture": False,
        "series": rows,
    }
    summary = {"status": {"state": "ok"}}
    dashboard = {"status": {"state": "ok"}}
    (data_dir / "history.json").write_text(json.dumps(history), encoding="utf-8")
    (data_dir / "summary.json").write_text(json.dumps(summary), encoding="utf-8")
    (data_dir / "dashboard.json").write_text(json.dumps(dashboard), encoding="utf-8")

    seed = _load_incremental_seed(tmp_path, date.fromisoformat(rows[-1]["date"]))

    assert seed is not None
    assert seed.mutable_start == dates[-5].date()
    assert len(seed.kospi) == 255
    assert len(seed.flow) == 255
    assert len(seed.adjusted["226490.KS"]) == 255
    assert seed.flow.iloc[-1]["source_hash_override"] == "hash-254"


def test_merge_frames_replaces_mutable_dates_without_touching_older_rows():
    frozen = pd.DataFrame(
        {"close": [100.0, 101.0]}, index=pd.to_datetime(["2026-07-13", "2026-07-14"])
    )
    recent = pd.DataFrame(
        {"close": [201.0, 202.0]}, index=pd.to_datetime(["2026-07-14", "2026-07-15"])
    )

    merged = _merge_frames(frozen, recent)

    assert merged.loc["2026-07-13", "close"] == 100.0
    assert merged.loc["2026-07-14", "close"] == 201.0
    assert merged.loc["2026-07-15", "close"] == 202.0
