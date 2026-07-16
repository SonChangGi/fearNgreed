from __future__ import annotations

import json
import os
import time
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import requests

from .common import ProviderError, first_present, parse_number

BASE_URL = "https://data-dbg.krx.co.kr/svc/apis"
KOSPI_PATH = "idx/kospi_dd_trd"
ETF_PATH = "etp/etf_bydd_trd"
STOCK_PATH = "sto/stk_bydd_trd"


@dataclass(frozen=True)
class KRXIndexRow:
    date: date
    name: str
    close: float
    open: float
    high: float
    low: float
    trading_volume: float
    trading_value: float


@dataclass(frozen=True)
class KRXETFRow:
    date: date
    ticker: str
    name: str
    close: float
    open: float
    high: float
    low: float
    trading_volume: float
    trading_value: float


@dataclass(frozen=True)
class KRXStockRow:
    date: date
    ticker: str
    name: str
    close: float
    open: float
    high: float
    low: float
    trading_volume: float
    trading_value: float


class KRXOpenAPIClient:
    """Minimal KRX Open API client that never logs request metadata or payloads."""

    def __init__(
        self,
        api_key: str,
        *,
        timeout: float = 20,
        base_url: str = BASE_URL,
        session: requests.Session | None = None,
        cache_dir: Path | None = None,
        cache_revalidate_after: date | None = None,
        min_interval_seconds: float = 0.04,
    ) -> None:
        if not api_key:
            raise ProviderError("KRX_API_KEY is not configured")
        self._api_key = api_key
        self._timeout = timeout
        self._base_url = base_url.rstrip("/")
        self._session = session or requests.Session()
        self._cache_dir = cache_dir
        # KRX may revise the latest sessions after the first publication.  A
        # fourteen-calendar-day safety window covers at least the five mutable
        # sessions used by the incremental refresh, including ordinary Korean
        # exchange holiday clusters.  Callers can pass the exact mutable
        # boundary when replaying a historical as-of date.
        self._cache_revalidate_after = cache_revalidate_after or (
            date.today() - timedelta(days=14)
        )
        self._min_interval = max(0.0, min_interval_seconds)
        self._last_request_at = 0.0

    @classmethod
    def from_env(cls, **kwargs: Any) -> KRXOpenAPIClient:
        return cls(os.getenv("KRX_API_KEY", ""), **kwargs)

    def _request_rows(self, path: str, day: date) -> list[dict[str, Any]]:
        cached = self._read_cache(path, day)
        if cached is not None:
            return cached
        wait_for = self._min_interval - (time.monotonic() - self._last_request_at)
        if wait_for > 0:
            time.sleep(wait_for)
        try:
            response = self._session.get(
                f"{self._base_url}/{path}",
                params={"basDd": day.strftime("%Y%m%d")},
                headers={"AUTH_KEY": self._api_key, "Accept": "application/json"},
                timeout=self._timeout,
            )
        except requests.RequestException:
            raise ProviderError("KRX Open API request failed") from None
        finally:
            self._last_request_at = time.monotonic()
        if response.status_code != 200:
            raise ProviderError(f"KRX Open API returned HTTP {response.status_code}")
        try:
            payload = response.json()
        except (ValueError, json.JSONDecodeError):
            raise ProviderError("KRX Open API returned invalid JSON") from None
        rows = payload.get("OutBlock_1") if isinstance(payload, dict) else None
        if not isinstance(rows, list):
            raise ProviderError("KRX Open API response contract changed")
        clean_rows = [row for row in rows if isinstance(row, dict)]
        self._write_cache(path, day, clean_rows)
        return clean_rows

    def get_kospi(self, day: date) -> KRXIndexRow | None:
        rows = self._request_rows(KOSPI_PATH, day)
        target = next(
            (
                row
                for row in rows
                if str(row.get("IDX_NM", "")).strip().replace(" ", "").lower()
                in {"코스피", "kospi"}
            ),
            None,
        )
        if target is None:
            return None
        return KRXIndexRow(
            date=day,
            name=str(first_present(target, "IDX_NM")),
            close=parse_number(first_present(target, "CLSPRC_IDX"), field="CLSPRC_IDX"),
            open=parse_number(first_present(target, "OPNPRC_IDX"), field="OPNPRC_IDX"),
            high=parse_number(first_present(target, "HGPRC_IDX"), field="HGPRC_IDX"),
            low=parse_number(first_present(target, "LWPRC_IDX"), field="LWPRC_IDX"),
            trading_volume=parse_number(first_present(target, "ACC_TRDVOL"), field="ACC_TRDVOL"),
            trading_value=parse_number(first_present(target, "ACC_TRDVAL"), field="ACC_TRDVAL"),
        )

    def get_etfs(self, day: date, tickers: Iterable[str]) -> dict[str, KRXETFRow]:
        wanted = set(tickers)
        result: dict[str, KRXETFRow] = {}
        for row in self._request_rows(ETF_PATH, day):
            ticker = str(
                row.get("ISU_SRT_CD") or row.get("ISU_CD") or row.get("ISU_CODE") or ""
            ).strip()
            if ticker not in wanted:
                continue
            result[ticker] = KRXETFRow(
                date=day,
                ticker=ticker,
                name=str(first_present(row, "ISU_NM")),
                close=parse_number(first_present(row, "TDD_CLSPRC"), field="TDD_CLSPRC"),
                open=parse_number(first_present(row, "TDD_OPNPRC"), field="TDD_OPNPRC"),
                high=parse_number(first_present(row, "TDD_HGPRC"), field="TDD_HGPRC"),
                low=parse_number(first_present(row, "TDD_LWPRC"), field="TDD_LWPRC"),
                trading_volume=parse_number(first_present(row, "ACC_TRDVOL"), field="ACC_TRDVOL"),
                trading_value=parse_number(first_present(row, "ACC_TRDVAL"), field="ACC_TRDVAL"),
            )
        return result

    def get_stocks(self, day: date, tickers: Iterable[str]) -> dict[str, KRXStockRow]:
        """Return official KRX daily prices for selected KOSPI stock tickers."""
        wanted = set(tickers)
        result: dict[str, KRXStockRow] = {}
        for row in self._request_rows(STOCK_PATH, day):
            ticker = str(
                row.get("ISU_SRT_CD") or row.get("ISU_CD") or row.get("ISU_CODE") or ""
            ).strip()
            if ticker not in wanted:
                continue
            result[ticker] = KRXStockRow(
                date=day,
                ticker=ticker,
                name=str(first_present(row, "ISU_NM")),
                close=parse_number(first_present(row, "TDD_CLSPRC"), field="TDD_CLSPRC"),
                open=parse_number(first_present(row, "TDD_OPNPRC"), field="TDD_OPNPRC"),
                high=parse_number(first_present(row, "TDD_HGPRC"), field="TDD_HGPRC"),
                low=parse_number(first_present(row, "TDD_LWPRC"), field="TDD_LWPRC"),
                trading_volume=parse_number(first_present(row, "ACC_TRDVOL"), field="ACC_TRDVOL"),
                trading_value=parse_number(first_present(row, "ACC_TRDVAL"), field="ACC_TRDVAL"),
            )
        return result

    def _cache_path(self, path: str, day: date) -> Path | None:
        if self._cache_dir is None:
            return None
        return self._cache_dir / path.replace("/", "-") / f"{day.isoformat()}.json"

    def _read_cache(self, path: str, day: date) -> list[dict[str, Any]] | None:
        if day >= self._cache_revalidate_after:
            return None
        cache_path = self._cache_path(path, day)
        if cache_path is None or not cache_path.exists():
            return None
        try:
            payload = json.loads(cache_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        return payload if isinstance(payload, list) else None

    def _write_cache(self, path: str, day: date, rows: list[dict[str, Any]]) -> None:
        cache_path = self._cache_path(path, day)
        if cache_path is None:
            return
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(
            json.dumps(rows, ensure_ascii=False, separators=(",", ":")), encoding="utf-8"
        )


def index_rows_to_records(rows: Iterable[KRXIndexRow]) -> list[dict[str, Any]]:
    return [{**asdict(row), "date": row.date.isoformat()} for row in rows]
