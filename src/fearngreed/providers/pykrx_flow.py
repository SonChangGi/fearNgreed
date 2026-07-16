from __future__ import annotations

import contextlib
import io
import os
from datetime import date

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
    if not os.getenv("KRX_ID") or not os.getenv("KRX_PW"):
        raise ProviderError("KRX login credentials are not configured")
    captured_out = io.StringIO()
    captured_err = io.StringIO()
    try:
        with contextlib.redirect_stdout(captured_out), contextlib.redirect_stderr(captured_err):
            from pykrx import stock

            frame = stock.get_market_trading_value_by_date(
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
    except Exception:
        raise ProviderError("authenticated pykrx flow request failed") from None
    if not isinstance(frame, pd.DataFrame) or "개인" not in frame.columns:
        raise ProviderError("pykrx flow response contract changed")
    clean = frame[["개인"]].copy()
    clean.index = pd.to_datetime(clean.index, errors="coerce").tz_localize(None)
    clean = clean[~clean.index.isna()].sort_index()
    clean = clean.rename(columns={"개인": "individual_net_purchase"})
    clean["individual_net_purchase"] = pd.to_numeric(
        clean["individual_net_purchase"], errors="coerce"
    )
    if clean.index.duplicated().any():
        raise ProviderError("pykrx flow response contains duplicate dates")
    return clean.dropna()


def _authenticated_frame(
    kind: str,
    start: date,
    end: date,
    *,
    required_columns: list[str],
    ticker: str | None = None,
) -> pd.DataFrame:
    if not os.getenv("KRX_ID") or not os.getenv("KRX_PW"):
        raise ProviderError("KRX login credentials are not configured")
    captured_out = io.StringIO()
    captured_err = io.StringIO()
    try:
        with contextlib.redirect_stdout(captured_out), contextlib.redirect_stderr(captured_err):
            from pykrx import stock

            if kind == "index":
                frame = stock.get_index_ohlcv_by_date(
                    start.strftime("%Y%m%d"), end.strftime("%Y%m%d"), "1001", freq="d"
                )
            elif kind == "etf" and ticker is not None:
                frame = stock.get_etf_ohlcv_by_date(
                    start.strftime("%Y%m%d"), end.strftime("%Y%m%d"), ticker, freq="d"
                )
            elif kind == "stock" and ticker is not None:
                frame = stock.get_market_ohlcv_by_date(
                    start.strftime("%Y%m%d"),
                    end.strftime("%Y%m%d"),
                    ticker,
                    freq="d",
                    adjusted=False,
                )
            else:
                raise ProviderError("unsupported authenticated KRX request")
    except ProviderError:
        raise
    except Exception:
        raise ProviderError(f"authenticated pykrx {kind} request failed") from None
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
