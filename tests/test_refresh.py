from __future__ import annotations

import json
from contextlib import nullcontext
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

import fearngreed.refresh as refresh_module
from fearngreed.pipeline import PipelineOutputs
from fearngreed.providers.common import ProviderError
from fearngreed.refresh import (
    ETF_LISTING_DATES,
    ETF_YAHOO_TICKERS,
    IncrementalSeed,
    RefreshStageError,
    _align_core_to_latest_common,
    _assert_adjusted_scale_stable,
    _current_refresh_receipt,
    _decode_history_rows,
    _fetch_adjusted_partition,
    _fetch_authenticated_etf_histories,
    _fetch_open_stocks,
    _frames_from_history,
    _load_incremental_seed,
    _merge_frames,
    _open_api_reason,
    _preserve_frozen_history,
    _public_failure_reason,
    _reject_public_date_regression,
    _replace_history_rows,
    _require_refresh_credentials,
    _write_successful_noop_status,
)

ETF_TICKERS = tuple(ETF_LISTING_DATES)


@pytest.mark.parametrize(
    ("provider_message", "expected"),
    [
        ("KRX_API_KEY is not configured", "krx_open_api_key_missing"),
        ("KRX login credentials are not configured", "krx_login_credentials_missing"),
    ],
)
def test_public_failure_reason_distinguishes_missing_krx_credentials(
    provider_message: str, expected: str
) -> None:
    assert _public_failure_reason(provider_message) == expected


def test_open_api_missing_key_reason_remains_visible_when_pykrx_fallback_is_available() -> None:
    assert (
        _open_api_reason(ProviderError("KRX_API_KEY is not configured"))
        == "krx_open_api_key_missing"
    )


def test_public_failure_reason_still_redacts_unrecognized_credential_errors() -> None:
    assert (
        _public_failure_reason("credential request failed with private provider detail")
        == "krx_credentials_missing"
    )


def test_refresh_credentials_fail_closed_before_provider_access(monkeypatch) -> None:
    monkeypatch.delenv("KRX_API_KEY", raising=False)
    monkeypatch.delenv("KRX_ID", raising=False)
    monkeypatch.delenv("KRX_PW", raising=False)
    with pytest.raises(RefreshStageError, match="krx_open_api_key_missing"):
        _require_refresh_credentials()

    monkeypatch.setenv("KRX_API_KEY", "api-key-canary")
    with pytest.raises(RefreshStageError, match="krx_login_credentials_missing"):
        _require_refresh_credentials()

    monkeypatch.setenv("KRX_ID", "login-id-canary")
    monkeypatch.setenv("KRX_PW", "password-canary")
    _require_refresh_credentials()


def _configure_fake_refresh_credentials(monkeypatch) -> None:
    monkeypatch.setenv("KRX_API_KEY", "api-key-canary")
    monkeypatch.setenv("KRX_ID", "login-id-canary")
    monkeypatch.setenv("KRX_PW", "password-canary")


def _install_probe_fakes(monkeypatch, *, missing: str | None = None) -> list[Path]:
    cache_paths: list[Path] = []

    class FakeOpenClient:
        def get_kospi(self, _day):
            return None if missing == "open_kospi" else object()

        def get_etfs(self, _day, tickers):
            rows = {ticker: object() for ticker in tickers}
            if missing == "open_etf":
                rows.pop(next(iter(ETF_LISTING_DATES)))
            return rows

        def get_stocks(self, _day, tickers):
            rows = {ticker: object() for ticker in tickers}
            if missing == "open_stock":
                rows.pop("000660")
            return rows

    def fake_from_env(**kwargs):
        cache_dir = Path(kwargs["cache_dir"])
        assert cache_dir.is_dir()
        cache_paths.append(cache_dir)
        return FakeOpenClient()

    def frame(empty: bool = False) -> pd.DataFrame:
        return pd.DataFrame() if empty else pd.DataFrame({"value": [1.0]})

    first_etf = next(iter(ETF_LISTING_DATES))
    monkeypatch.setattr(
        refresh_module.KRXOpenAPIClient,
        "from_env",
        staticmethod(fake_from_env),
    )
    monkeypatch.setattr(
        refresh_module,
        "fetch_individual_flow",
        lambda _start, _end: frame(missing == "pykrx_flow"),
    )
    monkeypatch.setattr(
        refresh_module,
        "fetch_kospi_index",
        lambda _start, _end: frame(missing == "pykrx_kospi"),
    )
    monkeypatch.setattr(
        refresh_module,
        "fetch_etf_prices",
        lambda ticker, _start, _end: frame(missing == "pykrx_etf" and ticker == first_etf),
    )
    monkeypatch.setattr(
        refresh_module,
        "fetch_stock_prices",
        lambda ticker, _start, _end: frame(missing == "pykrx_stock" and ticker == "000660"),
    )
    return cache_paths


def test_probe_requires_every_provider_surface_and_uses_temporary_cache(
    tmp_path, monkeypatch, capsys
) -> None:
    cache_paths = _install_probe_fakes(monkeypatch)
    monkeypatch.setattr(
        refresh_module,
        "repository_root",
        lambda: (_ for _ in ()).throw(AssertionError("probe must not use repository cache")),
    )

    assert refresh_module.probe(date(2026, 7, 16)) == 0

    receipt = json.loads(capsys.readouterr().out)
    assert receipt["ok"] is True
    assert receipt["krxOpenApi"] == {"ok": True}
    assert receipt["pykrx"] == {"ok": True}
    assert receipt["krxEtfCount"] == len(ETF_LISTING_DATES)
    assert receipt["krxStockCount"] == 2
    assert all(rows > 0 for rows in receipt["pykrxEtfRows"].values())
    assert all(rows > 0 for rows in receipt["pykrxStockRows"].values())
    assert len(cache_paths) == 1
    with pytest.raises(ValueError):
        cache_paths[0].relative_to(tmp_path)
    assert not cache_paths[0].exists()


@pytest.mark.parametrize(
    "missing",
    [
        "open_kospi",
        "open_etf",
        "open_stock",
        "pykrx_flow",
        "pykrx_kospi",
        "pykrx_etf",
        "pykrx_stock",
    ],
)
def test_probe_fails_when_any_required_provider_surface_is_empty(
    missing, monkeypatch, capsys
) -> None:
    _install_probe_fakes(monkeypatch, missing=missing)

    assert refresh_module.probe(date(2026, 7, 16)) == 1

    receipt = json.loads(capsys.readouterr().out)
    assert receipt["ok"] is False
    if missing.startswith("open_"):
        assert receipt["krxOpenApi"] == {
            "ok": False,
            "reason": "krx_open_api_probe_incomplete",
        }
    else:
        assert receipt["pykrx"] == {
            "ok": False,
            "reason": "authenticated_pykrx_probe_incomplete",
        }


@pytest.mark.parametrize("provider", ["open", "pykrx"])
def test_probe_redacts_provider_error_details(provider, monkeypatch, capsys) -> None:
    _install_probe_fakes(monkeypatch)
    canary = "FAKE_PROVIDER_SECRET_CANARY"
    if provider == "open":
        monkeypatch.setattr(
            refresh_module.KRXOpenAPIClient,
            "from_env",
            staticmethod(
                lambda **_kwargs: (_ for _ in ()).throw(
                    ProviderError(f"KRX Open API failed {canary}")
                )
            ),
        )
    else:
        monkeypatch.setattr(
            refresh_module,
            "fetch_individual_flow",
            lambda _start, _end: (_ for _ in ()).throw(ProviderError(f"pykrx failed {canary}")),
        )

    assert refresh_module.probe(date(2026, 7, 16)) == 1

    output = capsys.readouterr().out
    receipt = json.loads(output)
    assert canary not in output
    if provider == "open":
        assert receipt["krxOpenApi"]["reason"] == "krx_open_api_unavailable"
    else:
        assert receipt["pykrx"]["reason"] == "authenticated_pykrx_unavailable"


def test_main_probe_bypasses_refresh_and_public_failure_writes(monkeypatch) -> None:
    observed: list[date] = []
    monkeypatch.setattr(refresh_module, "probe", lambda day: observed.append(day) or 0)

    def unexpected(*_args, **_kwargs):
        raise AssertionError("probe must bypass refresh and public status writes")

    monkeypatch.setattr(refresh_module, "refresh_lock", unexpected)
    monkeypatch.setattr(refresh_module, "refresh", unexpected)
    monkeypatch.setattr(refresh_module, "mark_failed", unexpected)

    assert refresh_module.main(["--probe", "--date", "2026-07-16"]) == 0
    assert observed == [date(2026, 7, 16)]


def test_refresh_fails_closed_when_official_latest_session_cannot_be_established(
    tmp_path, monkeypatch
) -> None:
    _configure_fake_refresh_credentials(monkeypatch)
    monkeypatch.setattr(refresh_module, "repository_root", lambda: tmp_path)
    monkeypatch.setattr(
        refresh_module.KRXOpenAPIClient,
        "from_env",
        staticmethod(lambda **_kwargs: object()),
    )
    monkeypatch.setattr(refresh_module, "_latest_open_row", lambda _client, _end: None)
    fallback_called = False

    def fake_fallback(_start, _end):
        nonlocal fallback_called
        fallback_called = True
        return pd.DataFrame()

    monkeypatch.setattr(refresh_module, "fetch_kospi_index", fake_fallback)

    with pytest.raises(
        RefreshStageError, match="krx_official_latest_session_unavailable"
    ) as captured:
        refresh_module.refresh(
            end=date(2026, 7, 16),
            backfill_start_date=None,
            dry_run=True,
        )

    assert captured.value.expected_as_of is None
    assert fallback_called is False


def test_early_retry_requires_the_requested_session_without_public_writes(
    tmp_path, monkeypatch
) -> None:
    _configure_fake_refresh_credentials(monkeypatch)
    monkeypatch.setattr(refresh_module, "repository_root", lambda: tmp_path)
    monkeypatch.setattr(
        refresh_module.KRXOpenAPIClient,
        "from_env",
        staticmethod(lambda **_kwargs: object()),
    )
    monkeypatch.setattr(
        refresh_module,
        "_latest_open_row",
        lambda _client, _end: SimpleNamespace(date=date(2026, 7, 16)),
    )
    authenticated_called = False

    def unexpected_authenticated_call(_start, _end):
        nonlocal authenticated_called
        authenticated_called = True
        raise AssertionError("early retry must wait for the Open API target session")

    monkeypatch.setattr(
        refresh_module,
        "fetch_kospi_index",
        unexpected_authenticated_call,
    )

    with pytest.raises(RefreshStageError, match="krx_target_session_not_published") as captured:
        refresh_module.refresh(
            end=date(2026, 7, 17),
            backfill_start_date=None,
            dry_run=False,
            require_end_session=True,
        )

    assert captured.value.expected_as_of == date(2026, 7, 16)
    assert authenticated_called is False
    assert not (tmp_path / "data").exists()


def test_terminal_weekday_holiday_is_a_strict_noop_without_public_writes(
    tmp_path, monkeypatch
) -> None:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    summary_path = data_dir / "summary.json"
    original = b'{"dataAsOf":"2026-07-17","status":{"state":"ok"}}\n'
    summary_path.write_bytes(original)
    _configure_fake_refresh_credentials(monkeypatch)
    monkeypatch.setattr(refresh_module, "repository_root", lambda: tmp_path)
    monkeypatch.setattr(
        refresh_module.KRXOpenAPIClient,
        "from_env",
        staticmethod(lambda **_kwargs: object()),
    )
    monkeypatch.setattr(
        refresh_module,
        "_latest_open_row",
        lambda _client, _end: SimpleNamespace(date=date(2026, 7, 17)),
    )
    authenticated_calls: list[tuple[date, date]] = []

    def holiday_session(start, end):
        authenticated_calls.append((start, end))
        return pd.DataFrame(columns=["open", "high", "low", "close"])

    monkeypatch.setattr(refresh_module, "fetch_kospi_index", holiday_session)
    monkeypatch.setattr(
        refresh_module,
        "_fetch_open_kospi",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("holiday no-op must stop before historical collection")
        ),
    )

    receipt = refresh_module.refresh(
        end=date(2026, 7, 20),
        backfill_start_date=None,
        dry_run=False,
    )

    assert receipt == {
        "ok": True,
        "skipped": True,
        "reason": "krx_target_is_non_trading_day",
        "dryRun": False,
        "requestedDate": "2026-07-20",
        "dataAsOf": "2026-07-17",
        "expectedDataAsOf": "2026-07-17",
        "status": "ok",
        "sourceMode": "krx_open_api",
        "sizes": {},
        "incremental": False,
        "noOp": True,
    }
    assert authenticated_calls == [(date(2026, 7, 20), date(2026, 7, 20))]
    assert summary_path.read_bytes() == original
    assert not (data_dir / "automation-status.json").exists()


def test_terminal_open_api_lag_uses_authenticated_target_session(tmp_path, monkeypatch) -> None:
    _configure_fake_refresh_credentials(monkeypatch)
    monkeypatch.setattr(refresh_module, "repository_root", lambda: tmp_path)
    monkeypatch.setattr(
        refresh_module.KRXOpenAPIClient,
        "from_env",
        staticmethod(lambda **_kwargs: object()),
    )
    target = date(2026, 7, 20)
    monkeypatch.setattr(
        refresh_module,
        "_latest_open_row",
        lambda _client, _end: SimpleNamespace(date=date(2026, 7, 17)),
    )
    authenticated_calls: list[tuple[date, date]] = []

    def authenticated_kospi(start, end):
        authenticated_calls.append((start, end))
        return pd.DataFrame(
            {
                "open": [100.0],
                "high": [101.0],
                "low": [99.0],
                "close": [100.5],
                "trading_volume": [1_000.0],
                "trading_value": [10_000.0],
            },
            index=pd.DatetimeIndex([target], name="date"),
        )

    monkeypatch.setattr(refresh_module, "fetch_kospi_index", authenticated_kospi)
    monkeypatch.setattr(
        refresh_module,
        "_fetch_open_kospi",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("lagged Open API KOSPI must not remain the core")
        ),
    )
    monkeypatch.setattr(refresh_module, "_fetch_open_etfs", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(
        refresh_module,
        "_fetch_authenticated_etf_histories",
        lambda *_args, **_kwargs: {},
    )
    monkeypatch.setattr(refresh_module, "_fetch_authenticated_stocks", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(
        refresh_module,
        "fetch_market_participant_flows",
        lambda _start, _end: pd.DataFrame(
            {"individual_net_purchase": [1_000.0]},
            index=pd.DatetimeIndex([target], name="date"),
        ),
    )
    monkeypatch.setattr(
        refresh_module,
        "_fetch_adjusted_partition",
        lambda *_args, **_kwargs: {},
    )
    captured: dict[str, object] = {}

    def capture_build(inputs):
        captured["coreSource"] = inputs.core_source
        captured["expectedAsOf"] = inputs.expected_as_of
        captured["degradedReasons"] = inputs.degraded_reasons
        raise RuntimeError("stop after fallback selection")

    monkeypatch.setattr(refresh_module, "build_outputs", capture_build)

    with pytest.raises(RefreshStageError, match="refresh_build_refresh_failed"):
        refresh_module.refresh(
            end=target,
            backfill_start_date=None,
            dry_run=True,
        )

    assert authenticated_calls == [(target, target), (date(2010, 1, 4), target)]
    assert captured == {
        "coreSource": "authenticated_pykrx_fallback",
        "expectedAsOf": target,
        "degradedReasons": ("krx_open_api_target_session_lag",),
    }


def test_authenticated_exact_date_mismatch_is_not_treated_as_a_holiday() -> None:
    frame = pd.DataFrame(
        {"close": [100.0]},
        index=pd.DatetimeIndex([date(2026, 7, 17)], name="date"),
    )

    with pytest.raises(ProviderError, match="exact-date response is inconsistent"):
        refresh_module._frame_contains_exact_session(frame, date(2026, 7, 20))


@pytest.mark.parametrize("backfill_start", [None, date(2010, 1, 4)])
def test_refresh_rejects_backdated_public_end_before_any_provider_call(
    tmp_path, monkeypatch, backfill_start
) -> None:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    original = b'{"dataAsOf":"2026-07-16","status":{"state":"ok"}}\n'
    (data_dir / "summary.json").write_bytes(original)
    _configure_fake_refresh_credentials(monkeypatch)
    monkeypatch.setattr(refresh_module, "repository_root", lambda: tmp_path)

    def unexpected_provider_call(**_kwargs):
        raise AssertionError("provider must not run for a backdated public refresh")

    monkeypatch.setattr(
        refresh_module.KRXOpenAPIClient,
        "from_env",
        staticmethod(unexpected_provider_call),
    )

    with pytest.raises(RefreshStageError, match="refresh_end_before_published_data"):
        refresh_module.refresh(
            end=date(2026, 7, 15),
            backfill_start_date=backfill_start,
            dry_run=False,
        )

    assert (data_dir / "summary.json").read_bytes() == original


def test_output_date_regression_error_carries_official_expected_session() -> None:
    expected = date(2026, 7, 17)

    with pytest.raises(RefreshStageError, match="refresh_data_as_of_regression") as captured:
        _reject_public_date_regression(
            published=date(2026, 7, 16),
            candidate=date(2026, 7, 15),
            code="refresh_data_as_of_regression",
            expected_as_of=expected,
        )

    assert captured.value.expected_as_of == expected


def test_main_forwards_official_expected_session_to_failure_status(monkeypatch) -> None:
    expected = date(2026, 7, 17)
    recorded: list[tuple[str, date | None]] = []
    monkeypatch.setattr(refresh_module, "refresh_lock", lambda: nullcontext())

    def fail_refresh(**_kwargs):
        raise RefreshStageError(
            "refresh_core_input_quality_failed",
            expected_as_of=expected,
        )

    monkeypatch.setattr(refresh_module, "refresh", fail_refresh)
    monkeypatch.setattr(
        refresh_module,
        "mark_failed",
        lambda reason, expected_as_of=None: recorded.append((reason, expected_as_of)),
    )

    assert refresh_module.main(["--date", "2026-07-17"]) == 1
    assert recorded == [("refresh_core_input_quality_failed", expected)]


def test_main_preserve_failure_policy_leaves_public_status_untouched(monkeypatch) -> None:
    marked: list[str] = []
    monkeypatch.setattr(refresh_module, "refresh_lock", lambda: nullcontext())
    monkeypatch.setattr(
        refresh_module,
        "refresh",
        lambda **_kwargs: (_ for _ in ()).throw(
            RefreshStageError("krx_target_session_not_published")
        ),
    )
    monkeypatch.setattr(
        refresh_module,
        "mark_failed",
        lambda reason, expected_as_of=None: marked.append(reason),
    )

    assert (
        refresh_module.main(
            [
                "--date",
                "2026-07-17",
                "--failure-policy",
                "preserve",
                "--require-end-session",
            ]
        )
        == 1
    )
    assert marked == []


def test_skip_if_current_is_a_true_noop_before_provider_access(tmp_path, monkeypatch) -> None:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    summary = {
        "dataAsOf": "2026-07-17",
        "status": {
            "state": "ok",
            "expectedDataAsOf": "2026-07-17",
            "sourceFreshnessPassed": True,
        },
        "automation": {"lastSuccessAt": "2026-07-17T09:20:00Z"},
        "primaryEntities": [{"id": "KOSPI", "sourceMode": "krx_open_api"}],
    }
    summary_path = data_dir / "summary.json"
    summary_path.write_text(json.dumps(summary), encoding="utf-8")
    original = summary_path.read_bytes()
    monkeypatch.setattr(refresh_module, "repository_root", lambda: tmp_path)
    monkeypatch.setattr(refresh_module, "refresh_lock", lambda: nullcontext())
    monkeypatch.setattr(
        refresh_module,
        "refresh",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("provider must not run")),
    )

    receipt = _current_refresh_receipt(tmp_path, date(2026, 7, 17))
    assert receipt is not None
    assert receipt["skipped"] is True
    assert receipt["expectedDataAsOf"] == "2026-07-17"
    assert refresh_module.main(["--date", "2026-07-17", "--skip-if-current"]) == 0
    assert summary_path.read_bytes() == original


def test_successful_noop_updates_only_operational_timestamps(tmp_path) -> None:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    summary = {
        "generatedAt": "2026-07-16T12:00:00Z",
        "dataAsOf": "2026-07-16",
        "status": {"state": "ok"},
        "automation": {
            "lastAttemptAt": "2026-07-16T12:00:00Z",
            "lastSuccessAt": "2026-07-16T12:00:00Z",
            "state": "ok",
        },
    }
    (data_dir / "summary.json").write_text(json.dumps(summary), encoding="utf-8")
    outputs = PipelineOutputs(
        summary={
            **summary,
            "generatedAt": "2026-07-20T12:00:00Z",
            "automation": {
                "lastAttemptAt": "2026-07-20T12:00:00Z",
                "lastSuccessAt": "2026-07-20T12:00:00Z",
                "state": "ok",
            },
        },
        dashboard={"unchanged": True},
        history={"unchanged": True},
        automation_status={
            "schemaVersion": 1,
            "state": "ok",
            "lastAttemptAt": "2026-07-20T12:00:00Z",
            "lastSuccessAt": "2026-07-20T12:00:00Z",
            "dataAsOf": "2026-07-16",
            "degradedReasons": [],
            "sourceMode": "krx_open_api",
        },
        strategy_comparison={"unchanged": True},
    )

    _write_successful_noop_status(tmp_path, outputs)

    updated_summary = json.loads((data_dir / "summary.json").read_text(encoding="utf-8"))
    updated_status = json.loads((data_dir / "automation-status.json").read_text(encoding="utf-8"))
    assert updated_summary["generatedAt"] == "2026-07-16T12:00:00Z"
    assert updated_summary["automation"]["lastSuccessAt"] == "2026-07-20T12:00:00Z"
    assert updated_status["lastSuccessAt"] == "2026-07-20T12:00:00Z"
    assert not (data_dir / "dashboard.json").exists()


def _etf_history_prices(value: float) -> dict[str, float]:
    return {
        field: price
        for offset, ticker in enumerate(ETF_TICKERS, start=1)
        for field, price in (
            (f"p{ticker}Open", value * offset),
            (f"p{ticker}Close", value * offset * 1.01),
        )
    }


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
                **_etf_history_prices(value),
            }
        )
    history = {
        "methodologyVersion": "fear-flow-v5",
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
    (data_dir / "strategy-comparison.json").write_text(
        json.dumps({"methodologyVersion": "fear-flow-v5"}), encoding="utf-8"
    )

    seed = _load_incremental_seed(tmp_path, date.fromisoformat(rows[-1]["date"]))

    assert seed is not None
    assert seed.methodology_version == "fear-flow-v5"
    assert seed.mutable_start == dates[-5].date()
    assert len(seed.kospi) == 255
    assert len(seed.flow) == 255
    assert len(seed.adjusted["226490.KS"]) == 255
    assert set(seed.adjusted) == {"^KS11", *ETF_YAHOO_TICKERS.values()}
    assert seed.flow.iloc[-1]["source_hash_override"] == "hash-254"
    assert seed.etf_reconciliation["226490"]["filledCount"] == 29


def test_v5_incremental_seed_requires_complete_price_contract_and_strategy_artifact(tmp_path):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    dates = pd.bdate_range("2025-01-02", periods=260)
    columns = [
        "date",
        "kospiClose",
        "flowShare",
        "rawFlowTrillion",
        "sourceHash",
        *[field for ticker in ETF_TICKERS for field in (f"p{ticker}Open", f"p{ticker}Close")],
    ]
    rows = [
        [
            timestamp.date().isoformat(),
            100.0 + index,
            -0.01 + index / 100_000,
            -0.20 + index / 10_000,
            f"hash-{index}",
            *[value for value in _etf_history_prices(100.0 + index).values()],
        ]
        for index, timestamp in enumerate(dates)
    ]
    history = {
        "methodologyVersion": "fear-flow-v5",
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
    (data_dir / "dashboard.json").write_text(
        json.dumps({"crosschecks": {"etf": {}}}), encoding="utf-8"
    )

    assert _load_incremental_seed(tmp_path, dates[-1].date()) is None

    (data_dir / "strategy-comparison.json").write_text(
        json.dumps(
            {
                "methodologyVersion": "fear-flow-v5",
                "dataAsOf": rows[-1][0],
                "contract": "fearngreed-strategy-comparison",
            }
        ),
        encoding="utf-8",
    )
    seed = _load_incremental_seed(tmp_path, dates[-1].date())

    assert seed is not None
    assert seed.methodology_version == "fear-flow-v5"
    assert len(seed.history_rows) == 260

    history["methodologyVersion"] = "fear-flow-v4"
    (data_dir / "history.json").write_text(json.dumps(history), encoding="utf-8")
    assert _load_incremental_seed(tmp_path, dates[-1].date()) is None


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
        methodology_version="fear-flow-v3",
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
        summary={"dataAsOf": rows[-1]["date"]},
        dashboard={},
        history={
            "methodologyVersion": "fear-flow-v3",
            "seriesColumns": columns,
            "seriesRows": [[row[column] for column in columns] for row in regenerated],
        },
        automation_status={},
    )

    with pytest.raises(
        RefreshStageError, match="frozen_history_drift_requires_backfill"
    ) as captured:
        _preserve_frozen_history(seed, outputs)
    assert captured.value.expected_as_of == dates[-1].date()


@pytest.mark.parametrize(
    ("field", "delta"),
    (
        ("kospiClose", 1e-12),
        ("flowShare", 1e-12),
        ("rawFlowTrillion", 1e-12),
        ("percentile", 1e-12),
    ),
)
def test_frozen_history_keeps_market_and_signal_numbers_exact(field, delta):
    previous = {
        "date": "2026-07-01",
        "kospiClose": 100.0,
        "flowShare": -0.01,
        "rawFlowTrillion": -1.0,
        "percentile": 2.0,
        "state": "extreme_fear",
        "sourceHash": "source-hash",
    }
    current = dict(previous)
    current[field] = float(current[field]) + delta

    assert refresh_module._history_rows_equivalent([previous], [current]) is False


@pytest.mark.parametrize(
    ("field", "accepted_delta", "rejected_delta"),
    (
        ("residual", 1.99e-8, 2.01e-8),
        ("residualZ", 4.99e-6, 5.01e-6),
        ("rollingR2", 2.99e-8, 3.01e-8),
        ("expected", 1.99e-8, 2.01e-8),
        ("fitScore", 0.99e-7, 1.01e-7),
    ),
)
def test_frozen_history_allows_only_bounded_model_serialization_noise(
    field, accepted_delta, rejected_delta
):
    previous = {
        "date": "2026-07-01",
        "residual": -0.02,
        "residualZ": -1.25,
        "rollingR2": 0.35,
        "expected": 0.01,
        "fitScore": 0.5,
        "state": "neutral",
        "sourceHash": "source-hash",
    }
    accepted = dict(previous)
    accepted[field] = float(accepted[field]) + accepted_delta
    rejected = dict(previous)
    rejected[field] = float(rejected[field]) + rejected_delta

    assert refresh_module._history_rows_equivalent([previous], [accepted]) is True
    assert refresh_module._history_rows_equivalent([previous], [rejected]) is False


@pytest.mark.parametrize(
    ("field", "changed"),
    (("state", "fear"), ("sourceHash", "different-source-hash")),
)
def test_frozen_history_keeps_state_and_provenance_exact(field, changed):
    previous = {
        "date": "2026-07-01",
        "state": "neutral",
        "sourceHash": "source-hash",
    }
    current = dict(previous)
    current[field] = changed

    assert refresh_module._history_rows_equivalent([previous], [current]) is False


def test_frozen_history_preserves_old_rows_when_all_public_values_match():
    dates = pd.bdate_range("2026-06-22", periods=8)
    rows = [
        {
            "date": timestamp.date().isoformat(),
            "kospiClose": 100.0 + index,
            "percentile": 10.0 + index,
            "residualZ": -1.0 + index / 10,
            "state": "neutral",
            "position": "cash",
        }
        for index, timestamp in enumerate(dates)
    ]
    seed = IncrementalSeed(
        mutable_start=dates[-2].date(),
        methodology_version="fear-flow-v3",
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
    regenerated[0]["residualZ"] += 4e-6
    regenerated[-1]["kospiClose"] = 999.0
    outputs = PipelineOutputs(
        summary={},
        dashboard={},
        history={
            "methodologyVersion": "fear-flow-v3",
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


def test_incremental_seed_reconstructs_all_etfs_and_optional_flow_channels(tmp_path):
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
        *[field for ticker in ETF_TICKERS for field in (f"p{ticker}Open", f"p{ticker}Close")],
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
            *list(_etf_history_prices(100.0 + index).values()),
        ]
        for index, timestamp in enumerate(dates)
    ]
    history = {
        "methodologyVersion": "fear-flow-v5",
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
    (data_dir / "strategy-comparison.json").write_text(
        json.dumps({"methodologyVersion": "fear-flow-v5"}), encoding="utf-8"
    )

    seed = _load_incremental_seed(tmp_path, dates[-1].date())

    assert seed is not None
    assert seed.methodology_version == "fear-flow-v5"
    assert len(seed.history_rows) == 260
    assert seed.history_rows[-1]["sourceHash"] == "hash-259"
    assert len(seed.kospi) == 255
    assert seed.flow.iloc[-1]["foreigner_net_purchase"] == -1_000_254.0
    assert seed.flow.iloc[-1]["foreigner_flow_share_override"] == pytest.approx(-0.010254)
    assert seed.flow.iloc[-1]["institutional_net_purchase"] == 500_254.0
    assert set(seed.adjusted) == {"^KS11", *ETF_YAHOO_TICKERS.values()}


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
        for ticker in ETF_TICKERS
    }
    degraded: list[str] = []

    result = _fetch_authenticated_etf_histories(
        end,
        recent_open=recent,
        degraded=degraded,
    )

    assert set(result) == set(ETF_TICKERS)
    assert set(calls) == {
        (ticker, listing_date, end) for ticker, listing_date in ETF_LISTING_DATES.items()
    }
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
        ["^KS11", *ETF_YAHOO_TICKERS.values()],
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

    assert set(core) == {"^KS11", *ETF_YAHOO_TICKERS.values()} - {"226490.KS"}
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
        list(ETF_YAHOO_TICKERS.values()),
        date(2026, 7, 9),
        end,
        degraded,
        reason_prefix="adjusted_price",
        start_overrides={
            yahoo_ticker: ETF_LISTING_DATES[ticker]
            for ticker, yahoo_ticker in ETF_YAHOO_TICKERS.items()
        },
    )

    assert set(output) == set(ETF_YAHOO_TICKERS.values())
    assert calls == [
        (yahoo_ticker, ETF_LISTING_DATES[ticker], end)
        for ticker, yahoo_ticker in ETF_YAHOO_TICKERS.items()
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
        for ticker in ETF_TICKERS
    }
    degraded: list[str] = []

    result = _fetch_authenticated_etf_histories(
        end,
        recent_open=recent,
        degraded=degraded,
    )

    for ticker in ETF_TICKERS:
        assert result[ticker].equals(recent[ticker])
        assert f"official_etf_provider_disagreement_{ticker}" in degraded
