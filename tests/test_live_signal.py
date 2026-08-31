from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from fearngreed.live_signal import (
    LIVE_CONTRACT,
    PROVISIONAL_EXPIRED_REASON,
    LiveSignalError,
    build_live_payload,
    expire_provisional_payload,
    resolve_live_history,
    write_atomic,
)

ROOT = Path(__file__).resolve().parents[1]
KST = ZoneInfo("Asia/Seoul")
HISTORY_ANCHOR = date(2026, 7, 16)


def _root_with_history(tmp_path: Path, cutoff: date = HISTORY_ANCHOR) -> Path:
    data = tmp_path / "data"
    data.mkdir(parents=True)
    history = json.loads((ROOT / "data" / "history.json").read_text(encoding="utf-8"))
    cutoff_text = cutoff.isoformat()
    if isinstance(history.get("seriesRows"), list):
        date_index = history["seriesColumns"].index("date")
        history["seriesRows"] = [
            row for row in history["seriesRows"] if str(row[date_index]) <= cutoff_text
        ]
    else:
        history["series"] = [row for row in history.get("series", []) if row["date"] <= cutoff_text]
    history["dataAsOf"] = cutoff_text
    (data / "history.json").write_text(json.dumps(history), encoding="utf-8")
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
    assert payload["provenance"]["historySource"] == "repository-last-good"


def test_live_signal_uses_freshest_valid_public_history_over_stale_checkout(
    tmp_path, monkeypatch
) -> None:
    root = _root_with_history(tmp_path, date(2026, 7, 15))
    public_root = _root_with_history(tmp_path / "public", HISTORY_ANCHOR)
    public_history = json.loads((public_root / "data" / "history.json").read_text(encoding="utf-8"))

    class Response:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return public_history

    monkeypatch.setattr("fearngreed.live_signal.requests.get", lambda *args, **kwargs: Response())

    history, rows, source = resolve_live_history(
        root,
        public_history_url="https://example.test/history.json",
    )

    assert history["dataAsOf"] == HISTORY_ANCHOR.isoformat()
    assert rows[-1]["date"] == HISTORY_ANCHOR.isoformat()
    assert source == "public-last-good"


def test_live_history_rejects_mismatched_methodology_and_required_contract(
    tmp_path, monkeypatch
) -> None:
    root = _root_with_history(tmp_path, date(2026, 7, 15))
    public_root = _root_with_history(tmp_path / "public", HISTORY_ANCHOR)
    public_history = json.loads((public_root / "data" / "history.json").read_text(encoding="utf-8"))
    public_history["methodologyVersion"] = "fear-flow-future"

    class Response:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return public_history

    monkeypatch.setattr("fearngreed.live_signal.requests.get", lambda *args, **kwargs: Response())

    history, _, source = resolve_live_history(
        root,
        public_history_url="https://example.test/history.json",
    )
    assert history["dataAsOf"] == "2026-07-15"
    assert source == "repository-last-good"

    local_path = root / "data" / "history.json"
    invalid_local = json.loads(local_path.read_text(encoding="utf-8"))
    invalid_local["schemaVersion"] = 2
    local_path.write_text(json.dumps(invalid_local), encoding="utf-8")
    with pytest.raises(LiveSignalError, match="live_history_contract_invalid"):
        resolve_live_history(
            root,
            public_history_url="https://example.test/history.json",
        )


def test_expired_provisional_signal_is_idempotently_blocked(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("KRX_ID", "id")
    monkeypatch.setenv("KRX_PW", "pw")
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

    expired, changed = expire_provisional_payload(
        payload,
        observed_at=datetime(2026, 7, 20, 16, 0, tzinfo=KST),
    )
    assert changed is True
    assert expired["actionWindow"]["state"] == "closed"
    assert expired["quality"] == {
        "state": "unavailable",
        "tradeEligible": False,
        "reasons": [PROVISIONAL_EXPIRED_REASON],
    }
    assert all(model["tradeEligible"] is False for model in expired["models"].values())

    unchanged, changed_again = expire_provisional_payload(
        expired,
        observed_at=datetime(2026, 7, 21, 9, 0, tzinfo=KST),
    )
    assert changed_again is False
    assert unchanged == expired


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
