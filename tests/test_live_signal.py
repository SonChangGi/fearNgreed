from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from fearngreed.live_signal import (
    LIVE_CONTRACT,
    LiveSignalError,
    build_live_payload,
    write_atomic,
)

ROOT = Path(__file__).resolve().parents[1]
KST = ZoneInfo("Asia/Seoul")


def _root_with_history(tmp_path: Path) -> Path:
    data = tmp_path / "data"
    data.mkdir()
    (data / "history.json").write_bytes((ROOT / "data" / "history.json").read_bytes())
    return tmp_path


def _frames(day: date) -> tuple[pd.DataFrame, pd.DataFrame]:
    index = pd.DatetimeIndex([pd.Timestamp("2026-07-16"), pd.Timestamp(day)])
    kospi = pd.DataFrame(
        {
            "close": [6820.6, 6500.0],
            "trading_value": [29_000_000_000_000.0, 30_000_000_000_000.0],
        },
        index=index,
    )
    flow_index = pd.DatetimeIndex([pd.Timestamp(day)])
    flow = pd.DataFrame({"individual_net_purchase": [300_000_000_000.0]}, index=flow_index)
    return kospi, flow


def test_live_signal_is_separate_past_only_same_day_snapshot(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("KRX_ID", "login-id-canary")
    monkeypatch.setenv("KRX_PW", "password-canary")
    root = _root_with_history(tmp_path)
    day = date(2026, 7, 20)
    kospi, flow = _frames(day)

    payload = build_live_payload(
        day=day,
        observed_at=datetime(2026, 7, 20, 15, 48, tzinfo=KST),
        root=root,
        kospi=kospi,
        flow=flow,
    )

    assert payload["contract"] == LIVE_CONTRACT
    assert payload["phase"] == "provisional"
    assert payload["historyDataAsOf"] == "2026-07-16"
    assert payload["inputRow"]["date"] == "2026-07-20"
    assert payload["models"]["robust"]["trainingCount"] == 252
    assert payload["models"]["robust"]["fitMethod"] == "huber"
    assert payload["actionWindow"]["state"] == "open"
    assert payload["quality"] == {"state": "ok", "tradeEligible": True, "reasons": []}


def test_live_signal_rejects_wrong_date_duplicate_and_zero_turnover(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("KRX_ID", "id")
    monkeypatch.setenv("KRX_PW", "pw")
    root = _root_with_history(tmp_path)
    day = date(2026, 7, 20)
    kospi, flow = _frames(day)
    wrong_flow = flow.copy()
    wrong_flow.index = pd.DatetimeIndex([pd.Timestamp("2026-07-17")])
    with pytest.raises(LiveSignalError, match="live_flow_date_mismatch"):
        build_live_payload(
            day=day,
            observed_at=datetime(2026, 7, 20, 15, 48, tzinfo=KST),
            root=root,
            kospi=kospi,
            flow=wrong_flow,
        )

    duplicate = pd.concat([flow, flow])
    with pytest.raises(LiveSignalError, match="live_flow_session_unavailable"):
        build_live_payload(
            day=day,
            observed_at=datetime(2026, 7, 20, 15, 48, tzinfo=KST),
            root=root,
            kospi=kospi,
            flow=duplicate,
        )

    zero = kospi.copy()
    zero.loc[pd.Timestamp(day), "trading_value"] = 0
    with pytest.raises(LiveSignalError, match="live_observation_invalid"):
        build_live_payload(
            day=day,
            observed_at=datetime(2026, 7, 20, 15, 48, tzinfo=KST),
            root=root,
            kospi=zero,
            flow=flow,
        )


def test_live_signal_requires_immediately_previous_provider_session(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("KRX_ID", "id")
    monkeypatch.setenv("KRX_PW", "pw")
    root = _root_with_history(tmp_path)
    day = date(2026, 7, 20)
    kospi, flow = _frames(day)
    intermediate = pd.DataFrame(
        {"close": [6700.0], "trading_value": [28_000_000_000_000.0]},
        index=pd.DatetimeIndex([pd.Timestamp("2026-07-17")]),
    )
    stale_history_range = pd.concat([kospi, intermediate]).sort_index()

    with pytest.raises(LiveSignalError, match="live_history_session_gap"):
        build_live_payload(
            day=day,
            observed_at=datetime(2026, 7, 20, 15, 48, tzinfo=KST),
            root=root,
            kospi=stale_history_range,
            flow=flow,
        )

    price_mismatch = kospi.copy()
    price_mismatch.loc[pd.Timestamp("2026-07-16"), "close"] = 6000.0
    with pytest.raises(LiveSignalError, match="live_history_price_mismatch"):
        build_live_payload(
            day=day,
            observed_at=datetime(2026, 7, 20, 15, 48, tzinfo=KST),
            root=root,
            kospi=price_mismatch,
            flow=flow,
        )


def test_live_signal_fails_closed_outside_provisional_capture_window(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("KRX_ID", "id")
    monkeypatch.setenv("KRX_PW", "pw")
    root = _root_with_history(tmp_path)
    kospi, flow = _frames(date(2026, 7, 20))

    for observed_at in (
        datetime(2026, 7, 20, 15, 39, tzinfo=KST),
        datetime(2026, 7, 20, 16, 0, tzinfo=KST),
        datetime(2026, 7, 21, 15, 48, tzinfo=KST),
    ):
        with pytest.raises(LiveSignalError, match="live_capture_window_closed"):
            build_live_payload(
                day=date(2026, 7, 20),
                observed_at=observed_at,
                root=root,
                kospi=kospi,
                flow=flow,
            )


def test_live_signal_requires_credentials_and_never_overwrites_confirmed_day(
    tmp_path, monkeypatch
) -> None:
    root = _root_with_history(tmp_path)
    day = date(2026, 7, 20)
    kospi, flow = _frames(day)
    monkeypatch.delenv("KRX_ID", raising=False)
    monkeypatch.delenv("KRX_PW", raising=False)
    with pytest.raises(LiveSignalError, match="krx_login_credentials_missing"):
        build_live_payload(
            day=day,
            observed_at=datetime(2026, 7, 20, 15, 48, tzinfo=KST),
            root=root,
            kospi=kospi,
            flow=flow,
        )

    monkeypatch.setenv("KRX_ID", "id")
    monkeypatch.setenv("KRX_PW", "pw")
    with pytest.raises(LiveSignalError, match="live_session_already_confirmed"):
        build_live_payload(
            day=date(2026, 7, 16),
            observed_at=datetime(2026, 7, 16, 15, 48, tzinfo=KST),
            root=root,
            kospi=kospi,
            flow=flow,
        )


def test_live_signal_atomic_writer_replaces_one_public_file(tmp_path) -> None:
    path = tmp_path / "data" / "live-signal.json"
    write_atomic(path, {"ok": True, "secret": None})
    assert json.loads(path.read_text()) == {"ok": True, "secret": None}
    assert not list(path.parent.glob(".live-signal.json.*"))
