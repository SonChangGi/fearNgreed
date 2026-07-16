from __future__ import annotations

import contextlib
import io
from datetime import date, timedelta

import pandas as pd

from .common import ProviderError


def fetch_adjusted_prices(tickers: list[str], start: date, end: date) -> dict[str, pd.DataFrame]:
    """Fetch split/dividend-adjusted daily OHLC serially for deterministic alignment."""
    captured_out = io.StringIO()
    captured_err = io.StringIO()
    try:
        with contextlib.redirect_stdout(captured_out), contextlib.redirect_stderr(captured_err):
            import yfinance as yf

            results: dict[str, pd.DataFrame] = {}
            for ticker in tickers:
                frame = yf.download(
                    ticker,
                    start=start.isoformat(),
                    end=(end + timedelta(days=1)).isoformat(),
                    auto_adjust=True,
                    actions=False,
                    progress=False,
                    threads=False,
                    repair=True,
                    timeout=20,
                )
                if isinstance(frame.columns, pd.MultiIndex):
                    frame.columns = frame.columns.get_level_values(0)
                results[ticker] = _normalize(frame, ticker)
    except Exception:
        raise ProviderError("yfinance adjusted-price request failed") from None
    return results


def _normalize(frame: pd.DataFrame, ticker: str) -> pd.DataFrame:
    required = ["Open", "High", "Low", "Close"]
    if not isinstance(frame, pd.DataFrame) or frame.empty:
        raise ProviderError(f"yfinance returned no rows for {ticker}")
    if any(field not in frame.columns for field in required):
        raise ProviderError(f"yfinance response contract changed for {ticker}")
    clean = frame[required].copy()
    index = pd.to_datetime(clean.index, errors="coerce")
    if getattr(index, "tz", None) is not None:
        index = index.tz_localize(None)
    clean.index = index.normalize()
    clean.columns = [field.lower() for field in required]
    precision = 4 if ticker == "KRW=X" else 2
    for field in clean.columns:
        clean[field] = pd.to_numeric(clean[field], errors="coerce").round(precision)
    clean = clean.dropna().sort_index()
    if clean.index.duplicated().any():
        raise ProviderError(f"yfinance returned duplicate dates for {ticker}")
    return clean
