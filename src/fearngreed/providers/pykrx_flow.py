from __future__ import annotations

import contextlib
import io
import os
from collections.abc import Callable
from datetime import date
from typing import Any

import pandas as pd

from .common import ProviderError


def fetch_kospi_index(start: date, end: date) -> pd.DataFrame:
    return _authenticated_frame(
        "index",
        start,
        end,
        required_columns=["시가", "고가", "저가", "종가", "거래량", "거래대금"],
    ).rename(
        columns={
            "시가": "open",
            "고가": "high",
            "저가": "low",
            "종가": "close",
            "거래량": "trading_volume",
            "거래대금": "trading_value",
        }
    )


def fetch_etf_prices(ticker: str, start: date, end: date) -> pd.DataFrame:
    return _authenticated_frame(
        "etf",
        start,
        end,
        ticker=ticker,
        required_columns=["시가", "고가", "저가", "종가", "거래량", "거래대금"],
    ).rename(
        columns={
            "시가": "open",
            "고가": "high",
            "저가": "low",
            "종가": "close",
            "거래량": "trading_volume",
            "거래대금": "trading_value",
        }
    )


def fetch_stock_prices(ticker: str, start: date, end: date) -> pd.DataFrame:
    """Fetch official KRX stock OHLCV for recent-price source crosschecks."""
    return _authenticated_frame(
        "stock",
        start,
        end,
        ticker=ticker,
        required_columns=["시가", "고가", "저가", "종가", "거래량", "거래대금"],
    ).rename(
        columns={
            "시가": "open",
            "고가": "high",
            "저가": "low",
            "종가": "close",
            "거래량": "trading_volume",
            "거래대금": "trading_value",
        }
    )


def fetch_individual_flow(start: date, end: date) -> pd.DataFrame:
    """Fetch authenticated KOSPI individual net purchases with all auth output suppressed."""
    channels = fetch_market_participant_flows(start, end)
    return channels[["individual_net_purchase"]]


def fetch_market_participant_flows(start: date, end: date) -> pd.DataFrame:
    """Fetch available KOSPI participant-flow channels from one authenticated response.

    Individual flow is the current model input and is therefore required.  The
    foreigner and institutional channels are retained when the provider returns
    them, but are not activated as trading signals by this adapter.
    """

    def request(stock: Any) -> pd.DataFrame:
        return stock.get_market_trading_value_by_date(
            start.strftime("%Y%m%d"),
            end.strftime("%Y%m%d"),
            "KOSPI",
            etf=False,
            etn=False,
            elw=False,
            on="순매수",
            detail=False,
            freq="d",
        )

    frame = _run_authenticated("flow", request)
    if not isinstance(frame, pd.DataFrame) or "개인" not in frame.columns:
        raise ProviderError("pykrx flow response contract changed")
    channel_aliases = {
        "individual_net_purchase": ("개인",),
        "foreigner_net_purchase": ("외국인합계", "외국인"),
        "institutional_net_purchase": ("기관합계",),
    }
    selected: dict[str, str] = {}
    for public_name, aliases in channel_aliases.items():
        source = next((alias for alias in aliases if alias in frame.columns), None)
        if source is not None:
            selected[source] = public_name
    clean = frame[list(selected)].copy().rename(columns=selected)
    clean.index = pd.to_datetime(clean.index, errors="coerce").tz_localize(None)
    clean = clean[~clean.index.isna()].sort_index()
    for column in clean.columns:
        clean[column] = pd.to_numeric(clean[column], errors="coerce")
    if clean.index.duplicated().any():
        raise ProviderError("pykrx flow response contains duplicate dates")
    return clean.dropna(subset=["individual_net_purchase"])


def _authenticated_frame(
    kind: str,
    start: date,
    end: date,
    *,
    required_columns: list[str],
    ticker: str | None = None,
) -> pd.DataFrame:
    def request(stock: Any) -> pd.DataFrame:
        if kind == "index":
            return stock.get_index_ohlcv_by_date(
                start.strftime("%Y%m%d"), end.strftime("%Y%m%d"), "1001", freq="d"
            )
        if kind == "etf" and ticker is not None:
            return stock.get_etf_ohlcv_by_date(
                start.strftime("%Y%m%d"), end.strftime("%Y%m%d"), ticker, freq="d"
            )
        if kind == "stock" and ticker is not None:
            return stock.get_market_ohlcv_by_date(
                start.strftime("%Y%m%d"),
                end.strftime("%Y%m%d"),
                ticker,
                freq="d",
                adjusted=False,
            )
        raise ProviderError("unsupported authenticated KRX request")

    frame = _run_authenticated(kind, request)
    if not isinstance(frame, pd.DataFrame) or any(
        field not in frame.columns for field in required_columns
    ):
        raise ProviderError(f"pykrx {kind} response contract changed")
    clean = frame[required_columns].copy()
    clean.index = pd.to_datetime(clean.index, errors="coerce").tz_localize(None)
    clean = clean[~clean.index.isna()].sort_index()
    for field in required_columns:
        clean[field] = pd.to_numeric(clean[field], errors="coerce")
    clean = clean.dropna()
    if clean.index.duplicated().any():
        raise ProviderError(f"pykrx {kind} response contains duplicate dates")
    return clean


def _run_authenticated(request_name: str, request: Callable[[Any], pd.DataFrame]) -> pd.DataFrame:
    """Run a pykrx request only after its authenticated session is proven valid.

    Recent pykrx releases can fall back to an unauthenticated ``requests.Session``
    when authentication fails.  That behaviour is unsuitable for provenance-
    sensitive research, so both import/login output and the request itself stay
    captured and a missing, invalid, or unverifiable session fails closed.
    """
    if not os.getenv("KRX_ID") or not os.getenv("KRX_PW"):
        raise ProviderError("KRX login credentials are not configured")
    captured_out = io.StringIO()
    captured_err = io.StringIO()
    try:
        with contextlib.redirect_stdout(captured_out), contextlib.redirect_stderr(captured_err):
            from pykrx import stock
            from pykrx.website.comm import get_auth_session

            session = get_auth_session()
            is_valid = getattr(session, "is_valid", None)
            if session is None or not callable(is_valid) or not bool(is_valid()):
                raise ProviderError("authenticated pykrx session is unavailable")
            frame = request(stock)
    except ProviderError:
        raise
    except Exception:
        raise ProviderError(f"authenticated pykrx {request_name} request failed") from None
    if not isinstance(frame, pd.DataFrame):
        raise ProviderError(f"pykrx {request_name} response contract changed")
    return frame
