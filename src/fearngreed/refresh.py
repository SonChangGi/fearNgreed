from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import math
import os
import re
import sys
import tempfile
import traceback
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

from .pipeline import (
    METHODOLOGY_VERSION,
    PipelineInputs,
    PipelineOutputs,
    build_outputs,
    output_size_report,
    write_outputs_atomic,
)
from .providers.common import ProviderError
from .providers.krx_open import KRXOpenAPIClient
from .providers.pykrx_flow import (
    fetch_etf_prices,
    fetch_individual_flow,
    fetch_kospi_index,
    fetch_market_participant_flows,
    fetch_stock_prices,
)
from .providers.yahoo import fetch_adjusted_prices
from .quality import compare_latest_close

PUBLIC_LIMITS = {
    "summary": 50_000,
    "dashboard": 500_000,
    "history": 2_000_000,
    "automation_status": 50_000,
    "strategy_comparison": 500_000,
}
DIAGNOSTIC_YAHOO_TICKERS = ["MU", "000660.KS", "005930.KS", "KRW=X"]
ADJUSTED_ANCHOR_LOOKBACK_DAYS = 45
ADJUSTED_SCALE_TOLERANCE = 0.005
# Frozen public rows are compared exactly by default.  These two model-only
# fields can move by a few final decimals when a row already rounded to the
# public eight-decimal contract is used to rebuild the rolling Huber fit.
# The bounds cover observed serialization round-trips through the public
# eight-decimal history contract without relaxing prices, flows, percentiles,
# states, positions, or source hashes.
FROZEN_DERIVED_SERIALIZATION_ABS_TOLERANCES = {
    "residual": 2e-8,
    "residualZ": 5e-6,
    "rollingR2": 3e-8,
    "expected": 2e-8,
    "fitScore": 1e-7,
}
ETF_LISTING_DATES = {
    "069500": date(2010, 1, 4),
    "114800": date(2010, 1, 4),
    "122630": date(2010, 2, 22),
    "226490": date(2015, 8, 24),
    "252670": date(2016, 9, 22),
}
ETF_YAHOO_TICKERS = {ticker: f"{ticker}.KS" for ticker in ETF_LISTING_DATES}
ETF_PUBLIC_TICKERS_BY_YAHOO = {
    yahoo_ticker: ticker for ticker, yahoo_ticker in ETF_YAHOO_TICKERS.items()
}
CORE_YAHOO_TICKERS = ["^KS11", *ETF_YAHOO_TICKERS.values()]
CORE_YAHOO_HISTORY_STARTS = {
    "^KS11": date(2010, 1, 4),
    **{
        yahoo_ticker: ETF_LISTING_DATES[ticker]
        for ticker, yahoo_ticker in ETF_YAHOO_TICKERS.items()
    },
}


@dataclass(frozen=True)
class IncrementalSeed:
    mutable_start: date
    methodology_version: str
    data_as_of: str
    status_state: str
    existing_signature: str
    history_rows: list[dict[str, object]]
    kospi: pd.DataFrame
    flow: pd.DataFrame
    adjusted: dict[str, pd.DataFrame]
    etf_reconciliation: dict[str, dict[str, object]] = field(default_factory=dict)


class RefreshStageError(RuntimeError):
    """A public-safe pipeline failure code with no provider response attached."""

    def __init__(self, code: str, *, expected_as_of: date | None = None):
        super().__init__(code)
        self.code = code
        self.expected_as_of = expected_as_of


def _safe_stage_code(prefix: str, error: Exception) -> str:
    """Describe the failing project function without serializing exception data."""
    function_name = "pipeline"
    for entry in reversed(traceback.extract_tb(error.__traceback__)):
        if "/fearngreed/" in entry.filename.replace("\\", "/"):
            function_name = entry.name
            break
    safe_name = re.sub(r"[^a-z0-9]+", "_", function_name.lower()).strip("_")
    return f"{prefix}_{safe_name or 'pipeline'}_failed"


def repository_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _parse_public_date(value: object) -> date | None:
    if isinstance(value, date):
        return value
    if not isinstance(value, str):
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _published_summary_data_as_of(root: Path) -> date | None:
    summary_path = root / "data" / "summary.json"
    try:
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(summary, dict):
        return None
    return _parse_public_date(summary.get("dataAsOf"))


def _reject_public_date_regression(
    *,
    published: date | None,
    candidate: date,
    code: str,
    expected_as_of: date | None = None,
) -> None:
    if published is not None and candidate < published:
        raise RefreshStageError(code, expected_as_of=expected_as_of)


def probe(day: date) -> int:
    result: dict[str, object] = {"date": day.isoformat(), "ok": False}
    open_api_ok = False
    try:
        with tempfile.TemporaryDirectory(prefix="fearngreed-krx-probe-") as cache_dir:
            client = KRXOpenAPIClient.from_env(cache_dir=Path(cache_dir))
            kospi = client.get_kospi(day)
            etfs = client.get_etfs(day, ETF_LISTING_DATES)
            stocks = client.get_stocks(day, ["000660", "005930"])
        result["krxKospi"] = kospi is not None
        result["krxEtfCount"] = len(etfs)
        result["krxStockCount"] = len(stocks)
        open_api_ok = bool(
            kospi is not None
            and set(etfs) == set(ETF_LISTING_DATES)
            and set(stocks) == {"000660", "005930"}
        )
        result["krxOpenApi"] = {
            "ok": open_api_ok,
            **({} if open_api_ok else {"reason": "krx_open_api_probe_incomplete"}),
        }
    except ProviderError as error:
        result["krxOpenApi"] = {"ok": False, "reason": _open_api_reason(error)}

    authenticated_ok = False
    try:
        flow_rows = len(fetch_individual_flow(day, day))
        kospi_rows = len(fetch_kospi_index(day, day))
        etf_rows = {ticker: len(fetch_etf_prices(ticker, day, day)) for ticker in ETF_LISTING_DATES}
        stock_rows = {
            ticker: len(fetch_stock_prices(ticker, day, day)) for ticker in ("000660", "005930")
        }
        result["pykrxFlowRows"] = flow_rows
        result["pykrxKospiRows"] = kospi_rows
        result["pykrxEtfRows"] = etf_rows
        result["pykrxStockRows"] = stock_rows
        authenticated_ok = bool(
            flow_rows > 0
            and kospi_rows > 0
            and all(rows > 0 for rows in etf_rows.values())
            and all(rows > 0 for rows in stock_rows.values())
        )
        result["pykrx"] = {
            "ok": authenticated_ok,
            **({} if authenticated_ok else {"reason": "authenticated_pykrx_probe_incomplete"}),
        }
    except ProviderError as error:
        result["pykrx"] = {
            "ok": False,
            "reason": _public_failure_reason(str(error)),
        }
    result["ok"] = open_api_ok and authenticated_ok
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if result["ok"] else 1


def refresh(
    *,
    end: date,
    backfill_start_date: date | None,
    dry_run: bool,
    require_end_session: bool = False,
) -> dict[str, object]:
    _require_refresh_credentials()
    root = repository_root()
    published_data_as_of = _published_summary_data_as_of(root)
    if not dry_run:
        _reject_public_date_regression(
            published=published_data_as_of,
            candidate=end,
            code="refresh_end_before_published_data",
        )
    seed = None if backfill_start_date else _load_incremental_seed(root, end)
    start = backfill_start_date or (seed.mutable_start if seed else date(2010, 1, 4))
    generated_at = datetime.now(UTC)
    degraded: list[str] = []
    core_source = "krx_open_api"
    open_client: KRXOpenAPIClient | None = None
    expected_as_of: date | None = None
    recent_open_etfs: dict[str, pd.DataFrame] = {}

    try:
        open_client = KRXOpenAPIClient.from_env(
            cache_dir=root / "var" / "cache" / "krx-open",
            cache_revalidate_after=max(start, end - timedelta(days=14)),
        )
        latest_open = _latest_open_row(open_client, end)
    except ProviderError as error:
        raise RefreshStageError(_open_api_reason(error)) from None
    if latest_open is None:
        raise RefreshStageError("krx_official_latest_session_unavailable")
    expected_as_of = latest_open.date
    if require_end_session and expected_as_of != end:
        raise RefreshStageError(
            "krx_target_session_not_published",
            expected_as_of=expected_as_of,
        )

    use_authenticated_core = False
    if expected_as_of < end:
        try:
            target_kospi = fetch_kospi_index(end, end)
            target_has_session = _frame_contains_exact_session(target_kospi, end)
        except ProviderError as error:
            raise RefreshStageError(
                _public_failure_reason(str(error)),
                expected_as_of=expected_as_of,
            ) from None
        if not target_has_session:
            current_data_as_of = published_data_as_of
            if current_data_as_of is None and seed is not None:
                current_data_as_of = _parse_public_date(seed.data_as_of)
            if backfill_start_date is None and current_data_as_of == expected_as_of:
                return _non_trading_day_receipt(
                    requested=end,
                    official_session=expected_as_of,
                    dry_run=dry_run,
                    seed=seed,
                )
        else:
            # The authenticated range endpoint already has the requested
            # session while the Open API daily endpoint is still lagging.  Use
            # the authenticated core for this run instead of silently
            # rebuilding the previous session under a new timestamp.
            expected_as_of = end
            use_authenticated_core = True
            core_source = "authenticated_pykrx_fallback"
            degraded.append("krx_open_api_target_session_lag")

    if use_authenticated_core:
        try:
            recent_kospi = fetch_kospi_index(start, end)
            if not _frame_contains_exact_session(recent_kospi, end):
                raise ProviderError("authenticated KRX target session is missing")
        except ProviderError as error:
            raise RefreshStageError(
                _public_failure_reason(str(error)),
                expected_as_of=expected_as_of,
            ) from None
        # All same-day official crosschecks must share the same source phase.
        # A lagging Open API KOSPI implies its ETF/stock rows cannot prove the
        # target session either, so use the authenticated adapters throughout.
        open_client = None
        recent_open_etfs = {}
    else:
        try:
            recent_kospi = _fetch_open_kospi(open_client, start, end)
            recent_open_etfs = _fetch_open_etfs(
                open_client,
                max(start, end - timedelta(days=14), min(ETF_LISTING_DATES.values())),
                end,
            )
        except ProviderError as error:
            open_client = None
            core_source = "authenticated_pykrx_fallback"
            degraded.append(_open_api_reason(error))
            recent_kospi = fetch_kospi_index(start, end)

    krx_etfs = _fetch_authenticated_etf_histories(
        end,
        recent_open=recent_open_etfs,
        degraded=degraded,
    )

    stock_crosscheck_start = max(start, end - timedelta(days=14))
    if open_client is not None:
        try:
            krx_stocks = _fetch_open_stocks(open_client, stock_crosscheck_start, end)
        except ProviderError:
            degraded.append("krx_open_api_stock_unavailable")
            krx_stocks = _fetch_authenticated_stocks(stock_crosscheck_start, end, degraded)
    else:
        krx_stocks = _fetch_authenticated_stocks(stock_crosscheck_start, end, degraded)

    try:
        recent_flow = fetch_market_participant_flows(start, end)
    except ProviderError as error:
        raise RefreshStageError(
            _public_failure_reason(str(error)),
            expected_as_of=expected_as_of,
        ) from None
    adjusted_fetch_start = start - timedelta(days=ADJUSTED_ANCHOR_LOOKBACK_DAYS) if seed else start
    recent_adjusted = _fetch_adjusted_partition(
        CORE_YAHOO_TICKERS,
        adjusted_fetch_start,
        end,
        degraded,
        reason_prefix="adjusted_price",
        start_overrides=CORE_YAHOO_HISTORY_STARTS if seed is None else None,
    )
    diagnostic_start = max(date(2010, 1, 4), end - timedelta(days=1_500))
    diagnostics = _fetch_adjusted_partition(
        DIAGNOSTIC_YAHOO_TICKERS,
        diagnostic_start,
        end,
        degraded,
        reason_prefix="diagnostic_price",
    )
    kospi = _merge_frames(seed.kospi, recent_kospi) if seed else recent_kospi
    flow = _merge_frames(seed.flow, recent_flow) if seed else recent_flow
    kospi, flow = _align_core_to_latest_common(kospi, flow, degraded)
    adjusted = dict(seed.adjusted) if seed else {}
    adjusted.update(diagnostics)
    for ticker, frame in recent_adjusted.items():
        if seed and ticker in adjusted:
            # Old adjusted prices are part of the published research record.  A
            # small immutable overlap is fetched only to detect a dividend or
            # split scale change.  Such a change needs an explicit backfill;
            # silently mixing newly rescaled prices with frozen rows would make
            # the public history and backtest disagree.
            _assert_adjusted_scale_stable(
                ticker,
                adjusted[ticker],
                frame,
                boundary=seed.mutable_start,
            )
            mutable = frame.loc[pd.to_datetime(frame.index) >= pd.Timestamp(seed.mutable_start)]
            adjusted[ticker] = _merge_frames(adjusted[ticker], mutable)
        else:
            adjusted[ticker] = frame
    try:
        outputs = build_outputs(
            PipelineInputs(
                kospi=kospi,
                flow=flow,
                adjusted=adjusted,
                krx_etfs=krx_etfs,
                generated_at=generated_at,
                core_source=core_source,
                degraded_reasons=tuple(degraded),
                krx_stocks=krx_stocks,
                kospi_secondary_history_independent=seed is None,
                prior_etf_reconciliation=seed.etf_reconciliation if seed else {},
                expected_as_of=expected_as_of,
            )
        )
    except Exception as error:
        code = (
            "refresh_core_input_quality_failed"
            if str(error).startswith("core input quality failed:")
            else _safe_stage_code("refresh_build", error)
        )
        raise RefreshStageError(code, expected_as_of=expected_as_of) from None
    try:
        if seed and seed.methodology_version == outputs.history.get("methodologyVersion"):
            _preserve_frozen_history(seed, outputs)
        sizes = output_size_report(outputs)
    except RefreshStageError as error:
        raise RefreshStageError(
            error.code,
            expected_as_of=error.expected_as_of or expected_as_of,
        ) from None
    except Exception as error:
        raise RefreshStageError(
            _safe_stage_code("refresh_artifact", error),
            expected_as_of=expected_as_of,
        ) from None
    oversized = [name for name, size in sizes.items() if size > PUBLIC_LIMITS[name]]
    if oversized:
        raise RefreshStageError(
            f"refresh_artifact_size_limit_{'_'.join(oversized)}",
            expected_as_of=expected_as_of,
        )
    output_data_as_of = _parse_public_date(outputs.summary.get("dataAsOf"))
    if output_data_as_of is None:
        raise RefreshStageError(
            "refresh_output_data_as_of_invalid",
            expected_as_of=expected_as_of,
        )
    if not dry_run:
        _reject_public_date_regression(
            published=published_data_as_of,
            candidate=output_data_as_of,
            code="refresh_data_as_of_regression",
            expected_as_of=expected_as_of,
        )
    no_op = bool(seed and _is_unchanged(seed, outputs))
    if not dry_run:
        if no_op:
            _write_successful_noop_status(root, outputs)
        else:
            write_outputs_atomic(outputs, root / "data")
    return {
        "ok": True,
        "dryRun": dry_run,
        "dataAsOf": outputs.summary["dataAsOf"],
        "expectedDataAsOf": expected_as_of.isoformat(),
        "status": outputs.summary["status"]["state"],
        "sourceMode": core_source,
        "sizes": sizes,
        "incremental": seed is not None,
        "noOp": no_op,
    }


def _load_incremental_seed(root: Path, end: date) -> IncrementalSeed | None:
    history_path = root / "data" / "history.json"
    summary_path = root / "data" / "summary.json"
    dashboard_path = root / "data" / "dashboard.json"
    strategy_path = root / "data" / "strategy-comparison.json"
    if not history_path.exists() or not summary_path.exists() or not dashboard_path.exists():
        return None
    try:
        history = json.loads(history_path.read_text(encoding="utf-8"))
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        dashboard = json.loads(dashboard_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    rows = _decode_history_rows(history) if isinstance(history, dict) else None
    methodology_version = str(history.get("methodologyVersion", ""))
    strategy_comparison: dict[str, object] | None = None
    if methodology_version == METHODOLOGY_VERSION:
        if not strategy_path.exists():
            return None
        try:
            loaded_strategy = json.loads(strategy_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if not isinstance(loaded_strategy, dict):
            return None
        strategy_comparison = loaded_strategy
    if (
        not isinstance(rows, list)
        or history.get("fixture") is not False
        or methodology_version != METHODOLOGY_VERSION
        or len(rows) < 252
    ):
        return None
    usable = [
        row
        for row in rows
        if isinstance(row, dict)
        and isinstance(row.get("date"), str)
        and row["date"] <= end.isoformat()
    ]
    required = {
        "date",
        "kospiClose",
        "flowShare",
        "rawFlowTrillion",
        "sourceHash",
        *{field for ticker in ETF_LISTING_DATES for field in (f"p{ticker}Open", f"p{ticker}Close")},
    }
    if len(usable) < 252 or any(not required.issubset(row) for row in usable[-5:]):
        return None
    dates = sorted(date.fromisoformat(str(row["date"])) for row in usable)
    mutable_start = dates[-5]
    frozen = [row for row in usable if str(row["date"]) < mutable_start.isoformat()]
    if len(frozen) < 247:
        return None
    kospi, flow, adjusted = _frames_from_history(frozen)
    crosschecks = dashboard.get("crosschecks", {})
    etf_crosschecks = crosschecks.get("etf", {}) if isinstance(crosschecks, dict) else {}
    if not isinstance(etf_crosschecks, dict):
        etf_crosschecks = {}
    etf_reconciliation = {
        ticker: dict(check.get("historyReconciliation", {}))
        for ticker, check in etf_crosschecks.items()
        if isinstance(ticker, str)
        and isinstance(check, dict)
        and isinstance(check.get("historyReconciliation"), dict)
    }
    return IncrementalSeed(
        mutable_start=mutable_start,
        methodology_version=methodology_version,
        data_as_of=str(history.get("dataAsOf", usable[-1]["date"])),
        status_state=str(summary.get("status", {}).get("state", "unavailable")),
        existing_signature=_output_signature(
            summary,
            dashboard,
            history,
            strategy_comparison,
        ),
        history_rows=usable,
        kospi=kospi,
        flow=flow,
        adjusted=adjusted,
        etf_reconciliation=etf_reconciliation,
    )


def _frames_from_history(
    rows: list[dict[str, object]],
) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, pd.DataFrame]]:
    kospi_rows: list[dict[str, object]] = []
    flow_rows: list[dict[str, object]] = []
    adjusted_rows: dict[str, list[dict[str, object]]] = {
        "^KS11": [],
        **{yahoo_ticker: [] for yahoo_ticker in ETF_YAHOO_TICKERS.values()},
    }
    for row in rows:
        timestamp = pd.Timestamp(str(row["date"]))
        close = float(row["kospiClose"])
        kospi_rows.append(
            {
                "date": timestamp,
                "open": close,
                "high": close,
                "low": close,
                "close": close,
                "trading_volume": 1.0,
                "trading_value": 1.0,
            }
        )
        flow_row: dict[str, object] = {
            "date": timestamp,
            "individual_net_purchase": float(row["flowShare"]),
            "flow_share_override": float(row["flowShare"]),
            "raw_flow_trillion_override": float(row["rawFlowTrillion"]),
            "source_hash_override": str(row["sourceHash"]),
        }
        optional_channels = {
            "foreigner": ("foreignerNetPurchase", "foreigner_net_purchase"),
            "institutional": (
                "institutionalNetPurchase",
                "institutional_net_purchase",
            ),
        }
        for participant, (public_name, frame_name) in optional_channels.items():
            share = row.get(f"{participant}FlowShare")
            value = row.get(public_name)
            if share is not None:
                flow_row[f"{participant}_flow_share_override"] = float(share)
                if value is not None:
                    flow_row[frame_name] = float(value)
        flow_rows.append(flow_row)
        adjusted_rows["^KS11"].append(_price_row(timestamp, close, close))
        for ticker, yahoo_ticker in ETF_YAHOO_TICKERS.items():
            open_value = row.get(f"p{ticker}Open")
            close_value = row.get(f"p{ticker}Close")
            if open_value is not None and close_value is not None:
                adjusted_rows[yahoo_ticker].append(
                    _price_row(timestamp, float(open_value), float(close_value))
                )
    kospi = pd.DataFrame.from_records(kospi_rows).set_index("date")
    flow = pd.DataFrame.from_records(flow_rows).set_index("date")
    adjusted = {
        ticker: pd.DataFrame.from_records(values).set_index("date")
        for ticker, values in adjusted_rows.items()
        if values
    }
    return kospi, flow, adjusted


def _decode_history_rows(history: dict[str, object]) -> list[dict[str, object]] | None:
    """Decode legacy object rows and the compact columnar public-history shape."""
    legacy = history.get("series")
    if isinstance(legacy, list):
        if not all(isinstance(row, dict) for row in legacy):
            return None
        return [dict(row) for row in legacy]
    columns = history.get("seriesColumns")
    encoded_rows = history.get("seriesRows")
    if (
        not isinstance(columns, list)
        or not columns
        or not all(isinstance(column, str) and column for column in columns)
        or len(set(columns)) != len(columns)
        or not isinstance(encoded_rows, list)
    ):
        return None
    rows: list[dict[str, object]] = []
    for values in encoded_rows:
        if not isinstance(values, list) or len(values) != len(columns):
            return None
        rows.append(dict(zip(columns, values, strict=True)))
    return rows


def _replace_history_rows(history: dict[str, object], rows: list[dict[str, object]]) -> None:
    """Write rows back without changing the output's selected representation."""
    if isinstance(history.get("seriesColumns"), list) and "seriesRows" in history:
        columns = history["seriesColumns"]
        assert isinstance(columns, list)
        history["seriesRows"] = [[row.get(str(column)) for column in columns] for row in rows]
        history.pop("series", None)
        return
    history["series"] = rows


def _price_row(timestamp: pd.Timestamp, open_value: float, close_value: float) -> dict[str, object]:
    return {
        "date": timestamp,
        "open": open_value,
        "high": max(open_value, close_value),
        "low": min(open_value, close_value),
        "close": close_value,
    }


def _merge_frames(frozen: pd.DataFrame, recent: pd.DataFrame) -> pd.DataFrame:
    combined = pd.concat([frozen, recent], axis=0, sort=False)
    combined.index = pd.to_datetime(combined.index).tz_localize(None).normalize()
    return combined[~combined.index.duplicated(keep="last")].sort_index()


def _align_core_to_latest_common(
    kospi: pd.DataFrame,
    flow: pd.DataFrame,
    degraded: list[str],
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Use the latest common session without pairing a later flow row to stale prices."""
    if kospi.empty or flow.empty:
        return kospi, flow
    latest_kospi = pd.Timestamp(kospi.index.max()).tz_localize(None).normalize()
    latest_flow = pd.Timestamp(flow.index.max()).tz_localize(None).normalize()
    latest_common = min(latest_kospi, latest_flow)
    if latest_kospi != latest_flow:
        degraded.append("core_latest_common_date_alignment")
    return kospi.loc[:latest_common], flow.loc[:latest_common]


def _is_unchanged(seed: IncrementalSeed, outputs: PipelineOutputs) -> bool:
    current_signature = _output_signature(
        outputs.summary,
        outputs.dashboard,
        outputs.history,
        outputs.strategy_comparison,
    )
    return current_signature == seed.existing_signature


def _write_successful_noop_status(root: Path, outputs: PipelineOutputs) -> None:
    """Record a successful provider check without rewriting unchanged research payloads."""
    data_dir = root / "data"
    summary_path = data_dir / "summary.json"
    try:
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        summary = outputs.summary
    if not isinstance(summary, dict):
        summary = outputs.summary
    summary["automation"] = outputs.summary["automation"]
    _write_json_atomic(summary_path, summary)
    _write_json_atomic(data_dir / "automation-status.json", outputs.automation_status)


def _preserve_frozen_history(seed: IncrementalSeed, outputs: PipelineOutputs) -> None:
    boundary = seed.mutable_start.isoformat()
    frozen = [row for row in seed.history_rows if str(row["date"]) < boundary]
    output_rows = _decode_history_rows(outputs.history)
    if output_rows is None:
        raise ValueError("public history rows cannot be decoded")
    regenerated_frozen = [row for row in output_rows if str(row["date"]) < boundary]
    if not _history_rows_equivalent(frozen, regenerated_frozen):
        raise RefreshStageError(
            "frozen_history_drift_requires_backfill",
            expected_as_of=_parse_public_date(outputs.summary.get("dataAsOf")),
        )
    mutable = [row for row in output_rows if str(row["date"]) >= boundary]
    _replace_history_rows(outputs.history, [*frozen, *mutable])


def _history_rows_equivalent(
    frozen: list[dict[str, object]], regenerated: list[dict[str, object]]
) -> bool:
    """Compare frozen rows, tolerating only proven model serialization noise."""
    if len(frozen) != len(regenerated):
        return False
    for previous, current in zip(frozen, regenerated, strict=True):
        if previous.keys() != current.keys():
            return False
        for key in previous:
            left = previous[key]
            right = current[key]
            if isinstance(left, bool) or isinstance(right, bool):
                if left is not right:
                    return False
            elif isinstance(left, int | float) and isinstance(right, int | float):
                tolerance = FROZEN_DERIVED_SERIALIZATION_ABS_TOLERANCES.get(key, 0.0)
                if not math.isclose(float(left), float(right), rel_tol=0.0, abs_tol=tolerance):
                    return False
            elif left != right:
                return False
    return True


def _assert_adjusted_scale_stable(
    ticker: str,
    frozen: pd.DataFrame,
    fetched: pd.DataFrame,
    *,
    boundary: date,
    tolerance: float = ADJUSTED_SCALE_TOLERANCE,
) -> None:
    """Fail closed when fresh adjusted-price anchors no longer match frozen scale."""
    public_ticker = (
        "kospi" if ticker == "^KS11" else ETF_PUBLIC_TICKERS_BY_YAHOO.get(ticker, "unknown")
    )
    left = frozen.copy()
    right = fetched.copy()
    left.index = pd.to_datetime(left.index).tz_localize(None).normalize()
    right.index = pd.to_datetime(right.index).tz_localize(None).normalize()
    immutable = left.index[left.index < pd.Timestamp(boundary)]
    common = immutable.intersection(right.index)
    if len(common) < 3:
        raise RefreshStageError(f"adjusted_anchor_insufficient_requires_backfill_{public_ticker}")
    anchors = common[-min(6, len(common)) :]
    for timestamp in anchors:
        previous = float(left.at[timestamp, "close"])
        current = float(right.at[timestamp, "close"])
        if min(previous, current) <= 0 or not math.isfinite(previous + current):
            raise RefreshStageError(f"adjusted_anchor_invalid_requires_backfill_{public_ticker}")
        if abs(current / previous - 1) > tolerance:
            raise RefreshStageError(f"adjusted_scale_drift_requires_backfill_{public_ticker}")


def _output_signature(
    summary: dict[str, object],
    dashboard: dict[str, object],
    history: dict[str, object],
    strategy_comparison: dict[str, object] | None = None,
) -> str:
    stable_summary = {
        key: value for key, value in summary.items() if key not in {"generatedAt", "automation"}
    }
    stable_dashboard = {key: value for key, value in dashboard.items() if key != "generatedAt"}
    stable_history = {key: value for key, value in history.items() if key != "generatedAt"}
    stable_strategy = (
        {key: value for key, value in strategy_comparison.items() if key != "generatedAt"}
        if strategy_comparison is not None
        else None
    )
    encoded = json.dumps(
        [stable_summary, stable_dashboard, stable_history, stable_strategy],
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
        allow_nan=False,
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def _latest_open_row(client: KRXOpenAPIClient, end: date):
    for offset in range(0, 10):
        row = client.get_kospi(end - timedelta(days=offset))
        if row is not None:
            return row
    return None


def _frame_contains_exact_session(frame: pd.DataFrame, session: date) -> bool:
    """Return whether an authenticated exact-date response contains that session.

    An empty, contract-valid response is the provider's non-trading-day result.
    A non-empty response with a different date is ambiguous and must not be
    interpreted as a holiday.
    """
    if frame.empty:
        return False
    normalized = pd.to_datetime(frame.index, errors="coerce").tz_localize(None).normalize()
    if normalized.isna().any():
        raise ProviderError("authenticated KRX session date is invalid")
    target = pd.Timestamp(session)
    if target in normalized:
        return True
    raise ProviderError("authenticated KRX exact-date response is inconsistent")


def _non_trading_day_receipt(
    *,
    requested: date,
    official_session: date,
    dry_run: bool,
    seed: IncrementalSeed | None,
) -> dict[str, object]:
    """Describe a confirmed holiday no-op without mutating public artifacts."""
    official_value = official_session.isoformat()
    return {
        "ok": True,
        "skipped": True,
        "reason": "krx_target_is_non_trading_day",
        "dryRun": dry_run,
        "requestedDate": requested.isoformat(),
        "dataAsOf": official_value,
        "expectedDataAsOf": official_value,
        "status": seed.status_state if seed is not None else "ok",
        "sourceMode": "krx_open_api",
        "sizes": {},
        "incremental": seed is not None,
        "noOp": True,
    }


def _fetch_open_kospi(client: KRXOpenAPIClient, start: date, end: date) -> pd.DataFrame:
    records: list[dict[str, object]] = []
    for index, timestamp in enumerate(pd.bdate_range(start, end)):
        row = client.get_kospi(timestamp.date())
        if row is not None:
            records.append(
                {
                    "date": timestamp,
                    "open": row.open,
                    "high": row.high,
                    "low": row.low,
                    "close": row.close,
                    "trading_volume": row.trading_volume,
                    "trading_value": row.trading_value,
                }
            )
        if index and index % 500 == 0:
            print(f"KRX Open API KOSPI progress: {index} dates", file=sys.stderr)
    if not records:
        raise ProviderError("KRX Open API KOSPI history is empty")
    return pd.DataFrame.from_records(records).set_index("date").sort_index()


def _fetch_open_etfs(client: KRXOpenAPIClient, start: date, end: date) -> dict[str, pd.DataFrame]:
    records: dict[str, list[dict[str, object]]] = {ticker: [] for ticker in ETF_LISTING_DATES}
    for timestamp in pd.bdate_range(start, end):
        for ticker, row in client.get_etfs(timestamp.date(), records).items():
            records[ticker].append(
                {
                    "date": timestamp,
                    "open": row.open,
                    "high": row.high,
                    "low": row.low,
                    "close": row.close,
                    "trading_volume": row.trading_volume,
                    "trading_value": row.trading_value,
                }
            )
    output: dict[str, pd.DataFrame] = {}
    for ticker, rows in records.items():
        if rows:
            output[ticker] = pd.DataFrame.from_records(rows).set_index("date").sort_index()
    return output


def _fetch_open_stocks(client: KRXOpenAPIClient, start: date, end: date) -> dict[str, pd.DataFrame]:
    records: dict[str, list[dict[str, object]]] = {"000660": [], "005930": []}
    for timestamp in pd.bdate_range(start, end):
        for ticker, row in client.get_stocks(timestamp.date(), records).items():
            records[ticker].append(
                {
                    "date": timestamp,
                    "open": row.open,
                    "high": row.high,
                    "low": row.low,
                    "close": row.close,
                    "trading_volume": row.trading_volume,
                    "trading_value": row.trading_value,
                }
            )
    output = {
        ticker: pd.DataFrame.from_records(rows).set_index("date").sort_index()
        for ticker, rows in records.items()
        if rows
    }
    if set(output) != set(records):
        raise ProviderError("KRX Open API stock crosscheck history is incomplete")
    return output


def _fetch_authenticated_etf_histories(
    end: date,
    *,
    recent_open: dict[str, pd.DataFrame],
    degraded: list[str],
) -> dict[str, pd.DataFrame]:
    """Fetch official range histories for multi-anchor adjusted-price checks.

    Open API daily calls remain the independent latest-price reference.  The
    authenticated range adapter provides the historical anchors without a
    decade of one-request-per-session Open API traffic.
    """
    output: dict[str, pd.DataFrame] = {}
    for ticker, listing_date in ETF_LISTING_DATES.items():
        try:
            history = fetch_etf_prices(ticker, listing_date, end)
        except ProviderError:
            degraded.append(f"historical_etf_{ticker}_unavailable")
            fallback = recent_open.get(ticker)
            if fallback is not None and not fallback.empty:
                output[ticker] = fallback
            continue
        if history.empty:
            degraded.append(f"historical_etf_{ticker}_unavailable")
            fallback = recent_open.get(ticker)
            if fallback is not None and not fallback.empty:
                output[ticker] = fallback
            continue
        independent = recent_open.get(ticker)
        if independent is not None and not independent.empty:
            check = compare_latest_close(
                independent["close"],
                history["close"],
                expected_date=independent.index.max(),
            )
            if check["state"] != "ok":
                degraded.append(f"official_etf_provider_disagreement_{ticker}")
                output[ticker] = independent
                continue
        output[ticker] = history
    return output


def _fetch_authenticated_stocks(
    start: date, end: date, degraded: list[str]
) -> dict[str, pd.DataFrame]:
    try:
        stocks = {ticker: fetch_stock_prices(ticker, start, end) for ticker in ("000660", "005930")}
    except ProviderError:
        degraded.append("stock_crosscheck_provider_unavailable")
        return {}
    degraded.append("stock_crosscheck_authenticated_pykrx_fallback")
    return stocks


def _fetch_adjusted_partition(
    tickers: list[str],
    start: date,
    end: date,
    degraded: list[str],
    *,
    reason_prefix: str,
    start_overrides: dict[str, date] | None = None,
) -> dict[str, pd.DataFrame]:
    """Keep independent Yahoo instruments isolated from sibling failures."""
    reason_ids = {
        "^KS11": "kospi",
        **ETF_PUBLIC_TICKERS_BY_YAHOO,
        "MU": "mu",
        "000660.KS": "000660",
        "005930.KS": "005930",
        "KRW=X": "usdkrw",
    }
    output: dict[str, pd.DataFrame] = {}
    for ticker in tickers:
        try:
            ticker_start = (start_overrides or {}).get(ticker, start)
            fetched = fetch_adjusted_prices([ticker], ticker_start, end)
            frame = fetched.get(ticker)
            if frame is None or frame.empty:
                raise ProviderError("adjusted-price response is empty")
            output[ticker] = frame
        except ProviderError:
            degraded.append(f"{reason_prefix}_{reason_ids.get(ticker, 'unknown')}_unavailable")
    return output


def _open_api_reason(error: ProviderError) -> str:
    text = str(error)
    if "KRX_API_KEY" in text and "not configured" in text.lower():
        return "krx_open_api_key_missing"
    if "HTTP 401" in text:
        return "krx_open_api_http_401"
    if "HTTP 403" in text:
        return "krx_open_api_http_403"
    return "krx_open_api_unavailable"


def _require_refresh_credentials() -> None:
    """Fail before provider access when the scheduled refresh is misconfigured."""
    if not os.getenv("KRX_API_KEY"):
        raise RefreshStageError("krx_open_api_key_missing")
    if not os.getenv("KRX_ID") or not os.getenv("KRX_PW"):
        raise RefreshStageError("krx_login_credentials_missing")


def mark_failed(reason: str, expected_as_of: date | None = None) -> None:
    root = repository_root()
    data_dir = root / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    reason = _public_failure_reason(reason)
    attempted_at = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    automation_path = data_dir / "automation-status.json"
    previous_automation: dict[str, object] = {}
    if automation_path.exists():
        try:
            loaded = json.loads(automation_path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                previous_automation = loaded
        except (OSError, ValueError):
            pass

    summary_path = data_dir / "summary.json"
    summary: dict[str, object] | None = None
    if summary_path.exists():
        try:
            loaded = json.loads(summary_path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                summary = loaded
        except (OSError, ValueError):
            pass

    current_data_as_of = _parse_public_date(summary.get("dataAsOf")) if summary else None
    if current_data_as_of is None:
        current_data_as_of = _parse_public_date(previous_automation.get("dataAsOf"))
    summary_status = summary.get("status") if summary else None
    if not isinstance(summary_status, dict):
        summary_status = {}
    known_stale_expected: date | None = None
    for source in (summary_status, previous_automation):
        candidate = _parse_public_date(source.get("expectedDataAsOf"))
        if source.get("sourceFreshnessPassed") is False and candidate is not None:
            known_stale_expected = max(known_stale_expected or candidate, candidate)
    effective_expected_as_of = expected_as_of
    if known_stale_expected is not None:
        effective_expected_as_of = max(
            effective_expected_as_of or known_stale_expected, known_stale_expected
        )
    freshness_lag_detected = bool(
        effective_expected_as_of is not None
        and current_data_as_of is not None
        and effective_expected_as_of > current_data_as_of
    )
    failure_state = "stale" if freshness_lag_detected else "degraded"
    previous_reasons = previous_automation.get("degradedReasons", [])
    if not isinstance(previous_reasons, list):
        previous_reasons = []
    automation = {
        "schemaVersion": 1,
        "state": failure_state,
        "lastAttemptAt": attempted_at,
        "lastSuccessAt": previous_automation.get("lastSuccessAt"),
        "dataAsOf": previous_automation.get("dataAsOf"),
        "degradedReasons": list(dict.fromkeys([*previous_reasons, reason])),
        "sourceMode": previous_automation.get("sourceMode", "unavailable"),
    }
    freshness_fields = ("freshnessBasis", "expectedDataAsOf", "sourceFreshnessPassed")
    for field_name in freshness_fields:
        if field_name in previous_automation:
            automation[field_name] = previous_automation[field_name]
    if freshness_lag_detected:
        expected_value = effective_expected_as_of.isoformat()
        automation.update(
            {
                "freshnessBasis": "official_krx_latest_completed_session",
                "expectedDataAsOf": expected_value,
                "sourceFreshnessPassed": False,
            }
        )

    if summary is not None:
        try:
            status = summary.get("status")
            if not isinstance(status, dict):
                status = {}
            summary["status"] = status
            previous = status.get("degradedReasons", [])
            if not isinstance(previous, list):
                previous = []
            status["state"] = failure_state
            status["label"] = "데이터 지연" if freshness_lag_detected else "데이터 저하"
            status["degradedReasons"] = list(dict.fromkeys([*previous, reason]))
            if freshness_lag_detected:
                status.update(
                    {
                        "freshnessBasis": "official_krx_latest_completed_session",
                        "expectedDataAsOf": effective_expected_as_of.isoformat(),
                        "sourceFreshnessPassed": False,
                    }
                )
            summary_automation = summary.get("automation")
            if not isinstance(summary_automation, dict):
                summary_automation = {}
            summary["automation"] = summary_automation
            summary_automation["lastAttemptAt"] = attempted_at
            summary_automation["state"] = failure_state
            automation["lastSuccessAt"] = summary_automation.get(
                "lastSuccessAt", automation["lastSuccessAt"]
            )
            automation["dataAsOf"] = summary.get("dataAsOf", automation["dataAsOf"])
            entities = summary.get("primaryEntities", [])
            if isinstance(entities, list) and entities and isinstance(entities[0], dict):
                automation["sourceMode"] = entities[0].get("sourceMode", automation["sourceMode"])
            _write_json_atomic(summary_path, summary)
        except (OSError, ValueError, AttributeError):
            pass
    _write_json_atomic(automation_path, automation)


def _public_failure_reason(reason: str) -> str:
    value = str(reason).strip()
    if re.fullmatch(r"[a-z0-9_]{1,80}", value):
        return value
    lowered = value.lower()
    if "another refresh" in lowered:
        return "refresh_already_running"
    if "krx_api_key" in lowered and "not configured" in lowered:
        return "krx_open_api_key_missing"
    if "krx login credentials" in lowered and "not configured" in lowered:
        return "krx_login_credentials_missing"
    if "credential" in lowered or "not configured" in lowered:
        return "krx_credentials_missing"
    if "krx open api" in lowered:
        return "krx_open_api_unavailable"
    if "pykrx" in lowered:
        return "authenticated_pykrx_unavailable"
    if "yfinance" in lowered:
        return "yfinance_unavailable"
    return "refresh_provider_failed"


def _write_json_atomic(path: Path, payload: dict[str, object]) -> None:
    encoded = (
        json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
            allow_nan=False,
        )
        + "\n"
    )
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=path.parent, prefix=f".{path.name}.", delete=False
    ) as handle:
        handle.write(encoded)
        handle.flush()
        os.fsync(handle.fileno())
        temporary = Path(handle.name)
    temporary.replace(path)


@contextmanager
def refresh_lock():
    lock_path = repository_root() / "var" / "refresh.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("w", encoding="utf-8") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ProviderError("another refresh is already running") from None
        yield


def default_end_date() -> date:
    now = datetime.now(ZoneInfo("Asia/Seoul"))
    if now.hour < 20 or (now.hour == 20 and now.minute < 30):
        return now.date() - timedelta(days=1)
    return now.date()


def _current_refresh_receipt(root: Path, end: date) -> dict[str, object] | None:
    """Return a true no-op receipt when this exact official session is already public."""
    summary_path = root / "data" / "summary.json"
    try:
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(summary, dict):
        return None
    status = summary.get("status")
    automation = summary.get("automation")
    if not isinstance(status, dict) or not isinstance(automation, dict):
        return None
    target = end.isoformat()
    if (
        summary.get("dataAsOf") != target
        or status.get("expectedDataAsOf") != target
        or status.get("sourceFreshnessPassed") is not True
    ):
        return None
    entity = next(
        (
            item
            for item in summary.get("primaryEntities", [])
            if isinstance(item, dict) and item.get("id") == "KOSPI"
        ),
        {},
    )
    return {
        "ok": True,
        "skipped": True,
        "reason": "official_session_already_current",
        "dataAsOf": target,
        "expectedDataAsOf": target,
        "status": status.get("state", "ok"),
        "sourceMode": entity.get("sourceMode", "unavailable"),
        "incremental": True,
        "noOp": True,
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Refresh Fear & Greed public derivatives")
    parser.add_argument("--probe", action="store_true", help="Run a sanitized provider smoke test")
    parser.add_argument("--date", type=date.fromisoformat, help="Probe or refresh end date")
    parser.add_argument("--backfill-start-date", type=date.fromisoformat)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--failure-policy",
        choices=("publish", "preserve"),
        default="publish",
        help="Publish fail-closed status or preserve every public file for an early retry",
    )
    parser.add_argument(
        "--skip-if-current",
        action="store_true",
        help=(
            "Exit successfully without provider access or file writes when the target session "
            "is current"
        ),
    )
    parser.add_argument(
        "--require-end-session",
        action="store_true",
        help="Treat an official latest row older than --date as not-yet-published",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    end = args.date or default_end_date()
    if args.probe:
        return probe(end)
    try:
        with refresh_lock():
            if args.skip_if_current:
                current_receipt = _current_refresh_receipt(repository_root(), end)
                if current_receipt is not None:
                    print(json.dumps(current_receipt, ensure_ascii=False, sort_keys=True))
                    return 0
            receipt = refresh(
                end=end,
                backfill_start_date=args.backfill_start_date,
                dry_run=args.dry_run,
                require_end_session=args.require_end_session,
            )
    except RefreshStageError as error:
        reason = error.code
        if not args.dry_run and args.failure_policy == "publish":
            mark_failed(reason, error.expected_as_of)
        print(json.dumps({"ok": False, "reason": reason}, ensure_ascii=False))
        return 1
    except ProviderError as error:
        reason = _public_failure_reason(str(error))
        if not args.dry_run and args.failure_policy == "publish":
            mark_failed(reason)
        print(json.dumps({"ok": False, "reason": reason}, ensure_ascii=False))
        return 1
    except Exception:
        reason = "refresh_pipeline_failed"
        if not args.dry_run and args.failure_policy == "publish":
            mark_failed(reason)
        print(json.dumps({"ok": False, "reason": reason}, ensure_ascii=False))
        return 1
    print(json.dumps(receipt, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
