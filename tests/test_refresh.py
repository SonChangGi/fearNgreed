from __future__ import annotations

import json
from datetime import date

import pandas as pd
import pytest

from fearngreed.pipeline import PipelineOutputs
from fearngreed.providers.common import ProviderError
from fearngreed.refresh import (
    IncrementalSeed,
    RefreshStageError,
    _align_core_to_latest_common,
    _assert_adjusted_scale_stable,
    _decode_history_rows,
    _fetch_adjusted_partition,
    _fetch_authenticated_etf_histories,
    _fetch_open_stocks,
    _frames_from_history,
    _load_incremental_seed,
    _merge_frames,
    _preserve_frozen_history,
    _replace_history_rows,
)


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
    dashboard = {
        "status": {"state": "ok"},
        "crosschecks": {
            "etf": {"226490": {"historyReconciliation": {"state": "ok", "filledCount": 29}}}
        },
    }
    (data_dir / "history.json").write_text(json.dumps(history), encoding="utf-8")
    (data_dir / "summary.json").write_text(json.dumps(summary), encoding="utf-8")
    (data_dir / "dashboard.json").write_text(json.dumps(dashboard), encoding="utf-8")

    seed = _load_incremental_seed(tmp_path, date.fromisoformat(rows[-1]["date"]))

    assert seed is not None
    assert seed.methodology_version == "fear-flow-v1"
    assert seed.mutable_start == dates[-5].date()
    assert len(seed.kospi) == 255
    assert len(seed.flow) == 255
    assert len(seed.adjusted["226490.KS"]) == 255
    assert seed.flow.iloc[-1]["source_hash_override"] == "hash-254"
    assert seed.etf_reconciliation["226490"]["filledCount"] == 29


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


def test_frozen_history_rejects_any_public_row_drift_until_explicit_backfill():
    dates = pd.bdate_range("2026-06-22", periods=8)
    rows = [
        {
            "date": timestamp.date().isoformat(),
            "kospiClose": 100.0 + index,
            "percentile": 10.0 + index,
            "state": "neutral",
            "position": "cash",
            "p226490Open": 50.0 + index,
            "p226490Close": 51.0 + index,
        }
        for index, timestamp in enumerate(dates)
    ]
    seed = IncrementalSeed(
        mutable_start=dates[-2].date(),
        methodology_version="fear-flow-v2",
        data_as_of=rows[-1]["date"],
        status_state="ok",
        existing_signature="signature",
        history_rows=rows,
        kospi=pd.DataFrame(),
        flow=pd.DataFrame(),
        adjusted={},
    )
    regenerated = [dict(row) for row in rows]
    regenerated[2]["percentile"] = 11.5
    columns = list(rows[0])
    outputs = PipelineOutputs(
        summary={},
        dashboard={},
        history={
            "methodologyVersion": "fear-flow-v2",
            "seriesColumns": columns,
            "seriesRows": [[row[column] for column in columns] for row in regenerated],
        },
        automation_status={},
    )

    with pytest.raises(RefreshStageError, match="frozen_history_drift_requires_backfill"):
        _preserve_frozen_history(seed, outputs)


def test_frozen_history_preserves_old_rows_when_all_public_values_match():
    dates = pd.bdate_range("2026-06-22", periods=8)
    rows = [
        {
            "date": timestamp.date().isoformat(),
            "kospiClose": 100.0 + index,
            "percentile": 10.0 + index,
            "state": "neutral",
            "position": "cash",
        }
        for index, timestamp in enumerate(dates)
    ]
    seed = IncrementalSeed(
        mutable_start=dates[-2].date(),
        methodology_version="fear-flow-v2",
        data_as_of=rows[-1]["date"],
        status_state="ok",
        existing_signature="signature",
        history_rows=rows,
        kospi=pd.DataFrame(),
        flow=pd.DataFrame(),
        adjusted={},
    )
    columns = list(rows[0])
    regenerated = [dict(row) for row in rows]
    regenerated[0]["percentile"] += 4e-8
    regenerated[-1]["kospiClose"] = 999.0
    outputs = PipelineOutputs(
        summary={},
        dashboard={},
        history={
            "methodologyVersion": "fear-flow-v2",
            "seriesColumns": columns,
            "seriesRows": [[row[column] for column in columns] for row in regenerated],
        },
        automation_status={},
    )

    _preserve_frozen_history(seed, outputs)

    decoded = _decode_history_rows(outputs.history)
    assert decoded is not None
    assert decoded[-3] == rows[-3]
    assert decoded[-1]["kospiClose"] == 999.0


def test_adjusted_scale_anchor_drift_requires_explicit_backfill():
    dates = pd.bdate_range("2026-05-01", periods=12)
    frozen = pd.DataFrame(
        {"open": [100.0] * 12, "close": [101.0] * 12},
        index=dates,
    )
    stable = pd.DataFrame(
        {"open": [100.0] * 6, "close": [101.1] * 6},
        index=dates[-6:],
    )
    _assert_adjusted_scale_stable("226490.KS", frozen, stable, boundary=dates[-2].date())

    rescaled = stable * 0.98
    with pytest.raises(RefreshStageError, match="adjusted_scale_drift_requires_backfill_226490"):
        _assert_adjusted_scale_stable("226490.KS", frozen, rescaled, boundary=dates[-2].date())


def test_core_sources_are_trimmed_only_to_the_latest_common_session():
    kospi_dates = pd.to_datetime(["2026-07-14", "2026-07-15"])
    flow_dates = pd.to_datetime(["2026-07-14", "2026-07-15", "2026-07-16"])
    kospi = pd.DataFrame({"close": [100.0, 101.0]}, index=kospi_dates)
    flow = pd.DataFrame({"individual_net_purchase": [1.0, 2.0, 3.0]}, index=flow_dates)
    degraded: list[str] = []

    aligned_kospi, aligned_flow = _align_core_to_latest_common(kospi, flow, degraded)

    assert aligned_kospi.index.max() == pd.Timestamp("2026-07-15")
    assert aligned_flow.index.max() == pd.Timestamp("2026-07-15")
    assert degraded == ["core_latest_common_date_alignment"]


def test_open_api_stock_crosscheck_frames_cover_both_approved_tickers():
    result = _fetch_open_stocks(FakeOpenStockClient(), date(2026, 7, 13), date(2026, 7, 15))

    assert set(result) == {"000660", "005930"}
    assert result["000660"].index[-1] == pd.Timestamp("2026-07-15")
    assert result["005930"].loc["2026-07-15", "close"] == 101.0


def test_incremental_seed_decodes_compact_v1_history_for_v2_recomputation(tmp_path):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    columns = [
        "date",
        "kospiClose",
        "flowShare",
        "rawFlowTrillion",
        "foreignerNetPurchase",
        "foreignerFlowShare",
        "institutionalNetPurchase",
        "institutionalFlowShare",
        "sourceHash",
        "p069500Open",
        "p069500Close",
        "p226490Open",
        "p226490Close",
    ]
    dates = pd.bdate_range("2025-01-02", periods=260)
    rows = [
        [
            timestamp.date().isoformat(),
            100.0 + index,
            -0.01 + index / 100_000,
            -0.2 + index / 10_000,
            -1_000_000.0 - index,
            -0.01 - index / 1_000_000,
            500_000.0 + index,
            0.005 + index / 1_000_000,
            f"hash-{index}",
            200.0 + index,
            201.0 + index,
            100.0 + index,
            101.0 + index,
        ]
        for index, timestamp in enumerate(dates)
    ]
    history = {
        "methodologyVersion": "fear-flow-v1",
        "dataAsOf": rows[-1][0],
        "fixture": False,
        "seriesEncoding": "columnar-v1",
        "seriesColumns": columns,
        "seriesRows": rows,
    }
    (data_dir / "history.json").write_text(json.dumps(history), encoding="utf-8")
    (data_dir / "summary.json").write_text(
        json.dumps({"status": {"state": "ok"}}), encoding="utf-8"
    )
    (data_dir / "dashboard.json").write_text("{}", encoding="utf-8")

    seed = _load_incremental_seed(tmp_path, dates[-1].date())

    assert seed is not None
    assert seed.methodology_version == "fear-flow-v1"
    assert len(seed.history_rows) == 260
    assert seed.history_rows[-1]["sourceHash"] == "hash-259"
    assert len(seed.kospi) == 255
    assert seed.flow.iloc[-1]["foreigner_net_purchase"] == -1_000_254.0
    assert seed.flow.iloc[-1]["foreigner_flow_share_override"] == pytest.approx(-0.010254)
    assert seed.flow.iloc[-1]["institutional_net_purchase"] == 500_254.0


def test_compact_history_round_trip_preserves_selected_encoding():
    history: dict[str, object] = {
        "seriesEncoding": "columnar-v1",
        "seriesColumns": ["date", "value"],
        "seriesRows": [["2026-07-14", 1.0]],
    }

    decoded = _decode_history_rows(history)
    assert decoded == [{"date": "2026-07-14", "value": 1.0}]

    _replace_history_rows(
        history,
        [
            {"date": "2026-07-14", "value": 1.0},
            {"date": "2026-07-15", "value": 2.0},
        ],
    )

    assert "series" not in history
    assert history["seriesRows"] == [
        ["2026-07-14", 1.0],
        ["2026-07-15", 2.0],
    ]


def test_252_day_optional_flow_share_round_trip_preserves_normalized_values():
    dates = pd.bdate_range("2025-01-02", periods=252)
    rows = [
        {
            "date": timestamp.date().isoformat(),
            "kospiClose": 100.0 + index,
            "flowShare": -0.01 + index / 100_000,
            "rawFlowTrillion": -0.2 + index / 10_000,
            "sourceHash": f"hash-{index}",
            "foreignerNetPurchase": -1_000_000_000.0 - index,
            "foreignerFlowShare": -0.02 + index / 1_000_000,
            "institutionalNetPurchase": 500_000_000.0 + index,
            "institutionalFlowShare": 0.01 - index / 1_000_000,
        }
        for index, timestamp in enumerate(dates)
    ]
    history: dict[str, object] = {
        "seriesEncoding": "columnar-v1",
        "seriesColumns": list(rows[0]),
        "seriesRows": [],
    }

    _replace_history_rows(history, rows)
    decoded = _decode_history_rows(history)
    assert decoded is not None
    _, flow, _ = _frames_from_history(decoded)

    assert len(flow) == 252
    assert flow.iloc[0]["foreigner_flow_share_override"] == pytest.approx(-0.02)
    assert flow.iloc[-1]["foreigner_flow_share_override"] == pytest.approx(-0.02 + 251 / 1_000_000)
    assert flow.iloc[-1]["institutional_flow_share_override"] == pytest.approx(
        0.01 - 251 / 1_000_000
    )
    assert flow.iloc[-1]["foreigner_net_purchase"] == pytest.approx(-1_000_000_251.0)


def test_legacy_optional_net_purchase_without_normalized_share_fails_closed():
    rows = [
        {
            "date": timestamp.date().isoformat(),
            "kospiClose": 100.0 + index,
            "flowShare": -0.01,
            "rawFlowTrillion": -0.2,
            "sourceHash": f"legacy-hash-{index}",
            "foreignerNetPurchase": -1_000_000_000.0 - index,
        }
        for index, timestamp in enumerate(pd.bdate_range("2025-01-02", periods=252))
    ]
    history: dict[str, object] = {
        "seriesEncoding": "columnar-v1",
        "seriesColumns": list(rows[0]),
        "seriesRows": [],
    }

    _replace_history_rows(history, rows)
    decoded = _decode_history_rows(history)
    assert decoded is not None
    _, flow, _ = _frames_from_history(decoded)

    assert "foreigner_net_purchase" not in flow
    assert "foreigner_flow_share_override" not in flow


def test_authenticated_etf_histories_cover_long_multi_anchor_ranges(monkeypatch):
    calls: list[tuple[str, date, date]] = []
    end = date(2026, 7, 15)

    def fetch(ticker: str, start: date, requested_end: date) -> pd.DataFrame:
        calls.append((ticker, start, requested_end))
        dates = pd.to_datetime([start.isoformat(), end.isoformat()])
        return pd.DataFrame({"close": [100.0, 110.0]}, index=dates)

    monkeypatch.setattr("fearngreed.refresh.fetch_etf_prices", fetch)
    recent = {
        ticker: pd.DataFrame({"close": [110.0]}, index=pd.to_datetime([end.isoformat()]))
        for ticker in ("226490", "069500")
    }
    degraded: list[str] = []

    result = _fetch_authenticated_etf_histories(
        end,
        recent_open=recent,
        degraded=degraded,
    )

    assert set(result) == {"226490", "069500"}
    assert ("226490", date(2015, 8, 24), end) in calls
    assert ("069500", date(2010, 1, 4), end) in calls
    assert degraded == []


def test_adjusted_price_partition_keeps_sibling_tickers_when_one_fails(monkeypatch):
    index = pd.to_datetime(["2026-07-15"])

    def fetch(tickers, _start, _end):
        ticker = tickers[0]
        if ticker in {"226490.KS", "MU"}:
            raise ProviderError("sanitized provider failure")
        return {ticker: pd.DataFrame({"open": [100.0], "close": [101.0]}, index=index)}

    monkeypatch.setattr("fearngreed.refresh.fetch_adjusted_prices", fetch)
    degraded: list[str] = []
    core = _fetch_adjusted_partition(
        ["^KS11", "226490.KS", "069500.KS"],
        date(2026, 7, 14),
        date(2026, 7, 15),
        degraded,
        reason_prefix="adjusted_price",
    )
    diagnostics = _fetch_adjusted_partition(
        ["MU", "KRW=X"],
        date(2026, 7, 14),
        date(2026, 7, 15),
        degraded,
        reason_prefix="diagnostic_price",
    )

    assert set(core) == {"^KS11", "069500.KS"}
    assert set(diagnostics) == {"KRW=X"}
    assert degraded == [
        "adjusted_price_226490_unavailable",
        "diagnostic_price_mu_unavailable",
    ]


def test_adjusted_price_partition_retries_core_ticker_from_fixed_history_start(
    monkeypatch,
):
    calls: list[tuple[str, date, date]] = []

    def fetch(tickers, start, end):
        ticker = tickers[0]
        calls.append((ticker, start, end))
        return {
            ticker: pd.DataFrame(
                {"open": [100.0], "close": [101.0]},
                index=pd.to_datetime([end.isoformat()]),
            )
        }

    monkeypatch.setattr("fearngreed.refresh.fetch_adjusted_prices", fetch)
    degraded: list[str] = []
    end = date(2026, 7, 15)
    output = _fetch_adjusted_partition(
        ["226490.KS", "069500.KS"],
        date(2026, 7, 9),
        end,
        degraded,
        reason_prefix="adjusted_price",
        start_overrides={
            "226490.KS": date(2015, 8, 24),
            "069500.KS": date(2010, 1, 4),
        },
    )

    assert set(output) == {"226490.KS", "069500.KS"}
    assert calls == [
        ("226490.KS", date(2015, 8, 24), end),
        ("069500.KS", date(2010, 1, 4), end),
    ]
    assert degraded == []


def test_authenticated_etf_history_failure_uses_recent_open_without_fabrication(
    monkeypatch,
):
    end = date(2026, 7, 15)

    def fetch(ticker: str, _start: date, _end: date) -> pd.DataFrame:
        if ticker == "226490":
            raise RuntimeError("raw provider detail must not escape")
        return pd.DataFrame({"close": [100.0]}, index=pd.to_datetime([end.isoformat()]))

    def sanitized_fetch(ticker: str, start: date, requested_end: date) -> pd.DataFrame:
        try:
            return fetch(ticker, start, requested_end)
        except RuntimeError:
            raise ProviderError("authenticated pykrx etf request failed") from None

    monkeypatch.setattr("fearngreed.refresh.fetch_etf_prices", sanitized_fetch)
    recent_226490 = pd.DataFrame({"close": [109.0]}, index=pd.to_datetime([end.isoformat()]))
    degraded: list[str] = []

    result = _fetch_authenticated_etf_histories(
        end,
        recent_open={"226490": recent_226490},
        degraded=degraded,
    )

    assert result["226490"].equals(recent_226490)
    assert "historical_etf_226490_unavailable" in degraded
    assert all("raw provider detail" not in reason for reason in degraded)


def test_official_etf_provider_disagreement_discards_untrusted_history(monkeypatch):
    end = date(2026, 7, 15)

    def fetch(_ticker: str, start: date, _end: date) -> pd.DataFrame:
        return pd.DataFrame(
            {"close": [100.0, 120.0]},
            index=pd.to_datetime([start.isoformat(), end.isoformat()]),
        )

    monkeypatch.setattr("fearngreed.refresh.fetch_etf_prices", fetch)
    recent = {
        ticker: pd.DataFrame({"close": [110.0]}, index=pd.to_datetime([end.isoformat()]))
        for ticker in ("226490", "069500")
    }
    degraded: list[str] = []

    result = _fetch_authenticated_etf_histories(
        end,
        recent_open=recent,
        degraded=degraded,
    )

    assert result["226490"].equals(recent["226490"])
    assert result["069500"].equals(recent["069500"])
    assert "official_etf_provider_disagreement_226490" in degraded
    assert "official_etf_provider_disagreement_069500" in degraded
