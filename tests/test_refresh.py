from __future__ import annotations

import json
from datetime import date

import pandas as pd

from fearngreed.refresh import _fetch_open_stocks, _load_incremental_seed, _merge_frames


class FakeStockRow:
    def __init__(self, ticker: str, close: float):
        self.ticker = ticker
        self.open = close - 1
        self.high = close + 1
        self.low = close - 2
        self.close = close
        self.trading_volume = 100.0
        self.trading_value = 1_000.0


class FakeOpenStockClient:
    def get_stocks(self, day, tickers):
        if day.weekday() >= 5:
            return {}
        return {ticker: FakeStockRow(ticker, 100.0 + index) for index, ticker in enumerate(tickers)}


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


def test_open_api_stock_crosscheck_frames_cover_both_approved_tickers():
    result = _fetch_open_stocks(
        FakeOpenStockClient(), date(2026, 7, 13), date(2026, 7, 15)
    )

    assert set(result) == {"000660", "005930"}
    assert result["000660"].index[-1] == pd.Timestamp("2026-07-15")
    assert result["005930"].loc["2026-07-15", "close"] == 101.0
