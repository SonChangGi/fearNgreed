from __future__ import annotations

import sys
import types
from datetime import date

import pandas as pd
import pytest

from fearngreed.providers.common import ProviderError
from fearngreed.providers.krx_open import KRXOpenAPIClient
from fearngreed.providers.pykrx_flow import (
    fetch_individual_flow,
    fetch_market_participant_flows,
    fetch_stock_prices,
)


class FakeResponse:
    status_code = 200

    def __init__(self, payload):
        self.payload = payload

    def json(self):
        return self.payload


class FakeSession:
    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    def get(self, url, *, params, headers, timeout):
        self.calls.append((url, params, headers, timeout))
        return FakeResponse(self.payload)


class FakeAuthSession:
    def __init__(self, valid: bool = True):
        self.valid = valid

    def is_valid(self) -> bool:
        return self.valid


def install_fake_pykrx(monkeypatch, stock, *, valid: bool = True, auth_message: str = ""):
    fake = types.ModuleType("pykrx")
    fake.__path__ = []
    fake.stock = stock
    website = types.ModuleType("pykrx.website")
    website.__path__ = []
    comm = types.ModuleType("pykrx.website.comm")

    def get_auth_session():
        if auth_message:
            print(auth_message)
        return FakeAuthSession(valid)

    comm.get_auth_session = get_auth_session
    website.comm = comm
    fake.website = website
    monkeypatch.setitem(sys.modules, "pykrx", fake)
    monkeypatch.setitem(sys.modules, "pykrx.website", website)
    monkeypatch.setitem(sys.modules, "pykrx.website.comm", comm)


def test_krx_header_is_used_without_logging_secret(capsys) -> None:
    secret = "FAKE_KRX_SECRET_CANARY"
    session = FakeSession(
        {
            "OutBlock_1": [
                {
                    "IDX_NM": "코스피",
                    "CLSPRC_IDX": "3,200.50",
                    "OPNPRC_IDX": "3,180.00",
                    "HGPRC_IDX": "3,210.00",
                    "LWPRC_IDX": "3,170.00",
                    "ACC_TRDVOL": "123",
                    "ACC_TRDVAL": "456789",
                }
            ]
        }
    )
    client = KRXOpenAPIClient(secret, session=session, min_interval_seconds=0)
    row = client.get_kospi(date(2026, 7, 15))
    assert row is not None and row.close == 3200.5
    assert session.calls[0][2]["AUTH_KEY"] == secret
    assert secret not in capsys.readouterr().out


def test_krx_contract_change_fails_closed() -> None:
    client = KRXOpenAPIClient(
        "fake", session=FakeSession({"unexpected": []}), min_interval_seconds=0
    )
    with pytest.raises(ProviderError, match="contract changed"):
        client.get_kospi(date(2026, 7, 15))


def test_krx_stock_daily_parser_uses_official_stock_endpoint() -> None:
    session = FakeSession(
        {
            "OutBlock_1": [
                {
                    "ISU_SRT_CD": "000660",
                    "ISU_NM": "SK하이닉스",
                    "TDD_CLSPRC": "250,500",
                    "TDD_OPNPRC": "248,000",
                    "TDD_HGPRC": "253,000",
                    "TDD_LWPRC": "247,500",
                    "ACC_TRDVOL": "1,234",
                    "ACC_TRDVAL": "309,117,000",
                },
                {
                    "ISU_SRT_CD": "005930",
                    "ISU_NM": "삼성전자",
                    "TDD_CLSPRC": "88,000",
                    "TDD_OPNPRC": "87,000",
                    "TDD_HGPRC": "89,000",
                    "TDD_LWPRC": "86,500",
                    "ACC_TRDVOL": "2,000",
                    "ACC_TRDVAL": "176,000,000",
                },
            ]
        }
    )
    client = KRXOpenAPIClient("fake", session=session, min_interval_seconds=0)

    rows = client.get_stocks(date(2026, 7, 15), ["000660"])

    assert set(rows) == {"000660"}
    assert rows["000660"].name == "SK하이닉스"
    assert rows["000660"].close == 250500
    assert session.calls[0][0].endswith("/sto/stk_bydd_trd")


def test_recent_krx_dates_bypass_an_existing_cache(tmp_path) -> None:
    day = date(2026, 7, 15)
    original = FakeSession(
        {
            "OutBlock_1": [
                {
                    "IDX_NM": "KOSPI",
                    "CLSPRC_IDX": "3200",
                    "OPNPRC_IDX": "3190",
                    "HGPRC_IDX": "3210",
                    "LWPRC_IDX": "3180",
                    "ACC_TRDVOL": "1",
                    "ACC_TRDVAL": "2",
                }
            ]
        }
    )
    first = KRXOpenAPIClient(
        "fake",
        session=original,
        cache_dir=tmp_path,
        cache_revalidate_after=date(2026, 7, 10),
        min_interval_seconds=0,
    )
    assert first.get_kospi(day).close == 3200

    revised = FakeSession(
        {
            "OutBlock_1": [
                {
                    "IDX_NM": "KOSPI",
                    "CLSPRC_IDX": "3250",
                    "OPNPRC_IDX": "3190",
                    "HGPRC_IDX": "3260",
                    "LWPRC_IDX": "3180",
                    "ACC_TRDVOL": "1",
                    "ACC_TRDVAL": "2",
                }
            ]
        }
    )
    second = KRXOpenAPIClient(
        "fake",
        session=revised,
        cache_dir=tmp_path,
        cache_revalidate_after=date(2026, 7, 10),
        min_interval_seconds=0,
    )

    assert second.get_kospi(day).close == 3250
    assert len(revised.calls) == 1


def test_immutable_krx_dates_reuse_cache(tmp_path) -> None:
    day = date(2026, 7, 1)
    original = FakeSession(
        {
            "OutBlock_1": [
                {
                    "IDX_NM": "KOSPI",
                    "CLSPRC_IDX": "3100",
                    "OPNPRC_IDX": "3090",
                    "HGPRC_IDX": "3110",
                    "LWPRC_IDX": "3080",
                    "ACC_TRDVOL": "1",
                    "ACC_TRDVAL": "2",
                }
            ]
        }
    )
    first = KRXOpenAPIClient(
        "fake",
        session=original,
        cache_dir=tmp_path,
        cache_revalidate_after=date(2026, 7, 10),
        min_interval_seconds=0,
    )
    assert first.get_kospi(day).close == 3100

    unused = FakeSession({"unexpected": []})
    second = KRXOpenAPIClient(
        "fake",
        session=unused,
        cache_dir=tmp_path,
        cache_revalidate_after=date(2026, 7, 10),
        min_interval_seconds=0,
    )

    assert second.get_kospi(day).close == 3100
    assert unused.calls == []


def test_pykrx_auth_output_is_suppressed(monkeypatch, capsys) -> None:
    stock = types.SimpleNamespace()

    def fetch(*_args, **_kwargs):
        print("login-id-canary")
        return pd.DataFrame({"개인": [123.0]}, index=pd.to_datetime(["2026-07-15"]))

    stock.get_market_trading_value_by_date = fetch
    install_fake_pykrx(monkeypatch, stock, auth_message="password-canary")
    monkeypatch.setenv("KRX_ID", "login-id-canary")
    monkeypatch.setenv("KRX_PW", "password-canary")
    result = fetch_individual_flow(date(2026, 7, 15), date(2026, 7, 15))
    assert result.iloc[0, 0] == 123
    output = capsys.readouterr()
    assert "login-id-canary" not in output.out + output.err
    assert "password-canary" not in output.out + output.err


def test_pykrx_invalid_login_fails_closed_even_if_public_data_would_return(
    monkeypatch, capsys
) -> None:
    stock = types.SimpleNamespace()
    request_called = False

    def fetch(*_args, **_kwargs):
        nonlocal request_called
        request_called = True
        return pd.DataFrame({"개인": [123.0]}, index=pd.to_datetime(["2026-07-15"]))

    stock.get_market_trading_value_by_date = fetch
    install_fake_pykrx(
        monkeypatch, stock, valid=False, auth_message="password-canary-invalid-login"
    )
    monkeypatch.setenv("KRX_ID", "login-id-canary")
    monkeypatch.setenv("KRX_PW", "password-canary")

    with pytest.raises(ProviderError, match="session is unavailable"):
        fetch_individual_flow(date(2026, 7, 15), date(2026, 7, 15))

    assert request_called is False
    output = capsys.readouterr()
    assert "canary" not in output.out + output.err


def test_participant_flow_adapter_retains_optional_future_signal_channels(monkeypatch) -> None:
    stock = types.SimpleNamespace()

    def fetch(*_args, **_kwargs):
        return pd.DataFrame(
            {"개인": [123.0], "외국인": [-80.0], "기관합계": [-43.0]},
            index=pd.to_datetime(["2026-07-15"]),
        )

    stock.get_market_trading_value_by_date = fetch
    install_fake_pykrx(monkeypatch, stock)
    monkeypatch.setenv("KRX_ID", "login-id-canary")
    monkeypatch.setenv("KRX_PW", "password-canary")

    result = fetch_market_participant_flows(date(2026, 7, 15), date(2026, 7, 15))

    assert result.columns.tolist() == [
        "individual_net_purchase",
        "foreigner_net_purchase",
        "institutional_net_purchase",
    ]
    assert result.iloc[0].to_dict() == {
        "individual_net_purchase": 123.0,
        "foreigner_net_purchase": -80.0,
        "institutional_net_purchase": -43.0,
    }


def test_participant_flow_adapter_accepts_foreigner_total_alias(monkeypatch) -> None:
    stock = types.SimpleNamespace()

    def fetch(*_args, **_kwargs):
        return pd.DataFrame(
            {"개인": [123.0], "외국인합계": [-80.0], "기관합계": [-43.0]},
            index=pd.to_datetime(["2026-07-15"]),
        )

    stock.get_market_trading_value_by_date = fetch
    install_fake_pykrx(monkeypatch, stock)
    monkeypatch.setenv("KRX_ID", "login-id-canary")
    monkeypatch.setenv("KRX_PW", "password-canary")

    result = fetch_market_participant_flows(date(2026, 7, 15), date(2026, 7, 15))

    assert result.iloc[0]["foreigner_net_purchase"] == -80.0


def test_missing_optional_participant_channel_does_not_drop_individual_flow(
    monkeypatch,
) -> None:
    stock = types.SimpleNamespace()

    def fetch(*_args, **_kwargs):
        return pd.DataFrame(
            {
                "개인": [123.0, 50.0],
                "외국인": [-80.0, None],
                "기관합계": [-43.0, -50.0],
            },
            index=pd.to_datetime(["2026-07-14", "2026-07-15"]),
        )

    stock.get_market_trading_value_by_date = fetch
    install_fake_pykrx(monkeypatch, stock)
    monkeypatch.setenv("KRX_ID", "login-id-canary")
    monkeypatch.setenv("KRX_PW", "password-canary")

    result = fetch_market_participant_flows(date(2026, 7, 14), date(2026, 7, 15))

    assert len(result) == 2
    assert result.iloc[-1]["individual_net_purchase"] == 50.0
    assert pd.isna(result.iloc[-1]["foreigner_net_purchase"])


def test_pykrx_requires_credentials(monkeypatch) -> None:
    monkeypatch.delenv("KRX_ID", raising=False)
    monkeypatch.delenv("KRX_PW", raising=False)
    with pytest.raises(ProviderError, match="not configured"):
        fetch_individual_flow(date(2026, 7, 15), date(2026, 7, 15))


def test_pykrx_stock_crosscheck_adapter_suppresses_auth_output(monkeypatch, capsys) -> None:
    stock = types.SimpleNamespace()

    def fetch(*_args, **_kwargs):
        print("stock-login-canary")
        return pd.DataFrame(
            {
                "시가": [200_000.0],
                "고가": [205_000.0],
                "저가": [199_000.0],
                "종가": [204_000.0],
                "거래량": [123.0],
                "거래대금": [456_000.0],
            },
            index=pd.to_datetime(["2026-07-15"]),
        )

    stock.get_market_ohlcv_by_date = fetch
    install_fake_pykrx(monkeypatch, stock)
    monkeypatch.setenv("KRX_ID", "stock-login-canary")
    monkeypatch.setenv("KRX_PW", "stock-password-canary")

    result = fetch_stock_prices("000660", date(2026, 7, 15), date(2026, 7, 15))

    assert result.loc[pd.Timestamp("2026-07-15"), "close"] == 204_000
    output = capsys.readouterr()
    assert "stock-login-canary" not in output.out + output.err
