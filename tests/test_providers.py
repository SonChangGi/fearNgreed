from __future__ import annotations

import sys
import types
from datetime import date

import pandas as pd
import pytest

from fearngreed.providers.common import ProviderError
from fearngreed.providers.krx_open import KRXOpenAPIClient
from fearngreed.providers.pykrx_flow import fetch_individual_flow


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


def test_pykrx_auth_output_is_suppressed(monkeypatch, capsys) -> None:
    fake = types.ModuleType("pykrx")
    stock = types.SimpleNamespace()

    def fetch(*_args, **_kwargs):
        print("login-id-canary")
        return pd.DataFrame({"개인": [123.0]}, index=pd.to_datetime(["2026-07-15"]))

    stock.get_market_trading_value_by_date = fetch
    fake.stock = stock
    monkeypatch.setitem(sys.modules, "pykrx", fake)
    monkeypatch.setenv("KRX_ID", "login-id-canary")
    monkeypatch.setenv("KRX_PW", "password-canary")
    result = fetch_individual_flow(date(2026, 7, 15), date(2026, 7, 15))
    assert result.iloc[0, 0] == 123
    output = capsys.readouterr()
    assert "login-id-canary" not in output.out + output.err


def test_pykrx_requires_credentials(monkeypatch) -> None:
    monkeypatch.delenv("KRX_ID", raising=False)
    monkeypatch.delenv("KRX_PW", raising=False)
    with pytest.raises(ProviderError, match="not configured"):
        fetch_individual_flow(date(2026, 7, 15), date(2026, 7, 15))
