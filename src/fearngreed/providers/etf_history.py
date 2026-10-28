"""Private, resumable checkpoints of validated unadjusted KRX ETF history."""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from collections.abc import Callable
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

from .common import ProviderError

COLUMNS = ["open", "high", "low", "close", "trading_volume", "trading_value"]
CONTRACT = "authenticated-krx-etf-history-v1"
PAGE_DAYS = 730


def _validate(frame: pd.DataFrame, start: date, end: date) -> pd.DataFrame:
    if not isinstance(frame, pd.DataFrame) or list(frame.columns) != COLUMNS:
        raise ProviderError("KRX ETF checkpoint columns are invalid")
    index = frame.index
    if (
        not isinstance(index, pd.DatetimeIndex)
        or index.tz is not None
        or index.hasnans
        or index.has_duplicates
        or not index.equals(index.normalize())
        or ((index < pd.Timestamp(start)) | (index > pd.Timestamp(end))).any()
    ):
        raise ProviderError("KRX ETF checkpoint dates are invalid")
    try:
        if not frame.empty and not all(
            pd.api.types.is_numeric_dtype(dtype) for dtype in frame.dtypes
        ):
            raise ValueError("nonnumeric_column")
        values = frame.to_numpy(dtype=float)
    except (TypeError, ValueError):
        raise ProviderError("KRX ETF checkpoint values are invalid") from None
    if not np.isfinite(values).all() or (values < 0).any():
        raise ProviderError("KRX ETF checkpoint values are invalid")
    return frame.sort_index()


def _encode(payload: dict) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def _read(path: Path, ticker: str, start: date, stop: date):
    try:
        envelope = json.loads(path.read_bytes())
        payload = envelope["payload"]
        if envelope["sha256"] != hashlib.sha256(_encode(payload)).hexdigest():
            return None
        if (
            payload["contract"] != CONTRACT
            or payload["ticker"] != ticker
            or payload["start"] != start.isoformat()
            or payload["pageEnd"] != stop.isoformat()
            or payload["columns"] != COLUMNS
        ):
            return None
        covered = date.fromisoformat(payload["coveredThrough"])
        if not start <= covered <= stop:
            return None
        frame = pd.DataFrame(payload["rows"], columns=["date", *COLUMNS]).set_index("date")
        frame.index = pd.DatetimeIndex(frame.index)
        return _validate(frame, start, covered), covered
    except (OSError, ValueError, TypeError, KeyError, ProviderError):
        return None


def _write(path: Path, ticker: str, start: date, stop: date, end: date, frame):
    payload = {
        "contract": CONTRACT,
        "ticker": ticker,
        "start": start.isoformat(),
        "pageEnd": stop.isoformat(),
        "coveredThrough": end.isoformat(),
        "columns": COLUMNS,
        "rows": [
            [day.date().isoformat(), *row]
            for day, row in zip(frame.index, frame.to_numpy().tolist(), strict=True)
        ],
    }
    envelope = {"payload": payload, "sha256": hashlib.sha256(_encode(payload)).hexdigest()}
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(_encode(envelope))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def fetch_etf_history(
    ticker: str,
    start: date,
    end: date,
    *,
    cache_dir: Path,
    fetch_range: Callable[[str, date, date], pd.DataFrame],
    force_refresh: bool = False,
) -> pd.DataFrame:
    """Return all requested pages, or fail without returning a partial series.

    Historical pages follow the existing immutable-history policy. The latest
    14 calendar days are fetched on every run, including repeated same-day runs.
    Successful pages survive a later failure; a cache never substitutes for a
    failed request in the mutable window. Neither credentials nor raw responses
    are stored. A historical request cannot read rows beyond its own end date.
    """
    if not re.fullmatch(r"\d{6}", ticker) or start > end:
        raise ProviderError("KRX ETF history request is invalid")
    mutable = max(start, end - timedelta(days=14))
    frames = []
    cursor = start
    while cursor <= end:
        stop = cursor + timedelta(days=PAGE_DAYS - 1)
        requested_end = min(stop, end)
        path = cache_dir / ticker / f"{cursor.isoformat()}_{stop.isoformat()}.json"
        cached = None if force_refresh else _read(path, ticker, cursor, stop)
        fetch_start = cursor
        prefix = None
        if cached is not None:
            previous, covered = cached
            # Do not reuse a future snapshot when replaying an older as-of date.
            if covered <= end:
                if covered >= requested_end and requested_end < mutable:
                    frames.append(previous.loc[: pd.Timestamp(requested_end)])
                    cursor = stop + timedelta(days=1)
                    continue
                fetch_start = max(cursor, min(covered + timedelta(days=1), mutable))
                prefix = previous.loc[previous.index < pd.Timestamp(fetch_start)]
        fresh = _validate(
            fetch_range(ticker, fetch_start, requested_end), fetch_start, requested_end
        )
        combined = fresh if prefix is None else pd.concat([prefix, fresh])
        combined = _validate(combined, cursor, requested_end)
        # A blank multi-year response is not proof of an empty official history.
        if combined.empty and (requested_end - cursor).days >= 14:
            raise ProviderError("KRX ETF history page is empty")
        _write(path, ticker, cursor, stop, requested_end, combined)
        frames.append(combined)
        cursor = stop + timedelta(days=1)
    return _validate(pd.concat(frames), start, end)
