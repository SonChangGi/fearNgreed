from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import sys
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

from .pipeline import (
    PipelineInputs,
    PipelineOutputs,
    build_outputs,
    output_size_report,
    write_outputs_atomic,
)
from .providers.common import ProviderError
from .providers.krx_open import KRXOpenAPIClient
from .providers.pykrx_flow import fetch_etf_prices, fetch_individual_flow, fetch_kospi_index
from .providers.yahoo import fetch_adjusted_prices

PUBLIC_LIMITS = {
    "summary": 50_000,
    "dashboard": 500_000,
    "history": 2_000_000,
    "automation_status": 50_000,
}
CORE_YAHOO_TICKERS = ["^KS11", "226490.KS", "069500.KS"]
DIAGNOSTIC_YAHOO_TICKERS = ["MU", "000660.KS", "005930.KS", "KRW=X"]


@dataclass(frozen=True)
class IncrementalSeed:
    mutable_start: date
    data_as_of: str
    status_state: str
    existing_signature: str
    history_rows: list[dict[str, object]]
    kospi: pd.DataFrame
    flow: pd.DataFrame
    adjusted: dict[str, pd.DataFrame]


def repository_root() -> Path:
    return Path(__file__).resolve().parents[2]


def probe(day: date) -> int:
    result: dict[str, object] = {"date": day.isoformat()}
    try:
        client = KRXOpenAPIClient.from_env(
            cache_dir=repository_root() / "var" / "cache" / "krx-probe"
        )
        result["krxKospi"] = client.get_kospi(day) is not None
        result["krxEtfCount"] = len(client.get_etfs(day, ["226490", "069500"]))
    except ProviderError as error:
        result["krxOpenApi"] = {"ok": False, "reason": str(error)}
    try:
        result["pykrxFlowRows"] = len(fetch_individual_flow(day, day))
        result["pykrxKospiRows"] = len(fetch_kospi_index(day, day))
        result["pykrxEtfRows"] = {
            ticker: len(fetch_etf_prices(ticker, day, day)) for ticker in ("226490", "069500")
        }
    except ProviderError as error:
        result["pykrx"] = {"ok": False, "reason": str(error)}
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if result.get("krxKospi") and result.get("pykrxFlowRows") else 1


def refresh(
    *,
    end: date,
    backfill_start_date: date | None,
    dry_run: bool,
) -> dict[str, object]:
    root = repository_root()
    seed = None if backfill_start_date else _load_incremental_seed(root, end)
    start = backfill_start_date or (seed.mutable_start if seed else date(2010, 1, 4))
    generated_at = datetime.now(UTC)
    degraded: list[str] = []
    core_source = "krx_open_api"

    try:
        client = KRXOpenAPIClient.from_env(cache_dir=root / "var" / "cache" / "krx-open")
        latest_open = _latest_open_row(client, end)
        if latest_open is None:
            raise ProviderError("KRX Open API returned no recent KOSPI row")
        recent_kospi = _fetch_open_kospi(client, start, end)
        krx_etfs = _fetch_open_etfs(
            client,
            max(start, end - timedelta(days=14), date(2015, 8, 24)),
            end,
        )
    except ProviderError as error:
        core_source = "authenticated_pykrx_fallback"
        degraded.append(_open_api_reason(error))
        recent_kospi = fetch_kospi_index(start, end)
        krx_etfs = {
            "226490": fetch_etf_prices(
                "226490", max(start, end - timedelta(days=14), date(2015, 8, 24)), end
            ),
            "069500": fetch_etf_prices("069500", max(start, end - timedelta(days=14)), end),
        }

    recent_flow = fetch_individual_flow(start, end)
    recent_adjusted = fetch_adjusted_prices(CORE_YAHOO_TICKERS, start, end)
    diagnostic_start = max(date(2010, 1, 4), end - timedelta(days=1_500))
    diagnostics = fetch_adjusted_prices(DIAGNOSTIC_YAHOO_TICKERS, diagnostic_start, end)
    kospi = _merge_frames(seed.kospi, recent_kospi) if seed else recent_kospi
    flow = _merge_frames(seed.flow, recent_flow) if seed else recent_flow
    adjusted = dict(diagnostics)
    for ticker, frame in recent_adjusted.items():
        adjusted[ticker] = (
            _merge_frames(seed.adjusted[ticker], frame)
            if seed and ticker in seed.adjusted
            else frame
        )
    outputs = build_outputs(
        PipelineInputs(
            kospi=kospi,
            flow=flow,
            adjusted=adjusted,
            krx_etfs=krx_etfs,
            generated_at=generated_at,
            core_source=core_source,
            degraded_reasons=tuple(degraded),
        )
    )
    if seed:
        _preserve_frozen_history(seed, outputs)
    sizes = output_size_report(outputs)
    oversized = [name for name, size in sizes.items() if size > PUBLIC_LIMITS[name]]
    if oversized:
        raise ValueError(f"public output size limit exceeded: {','.join(oversized)}")
    no_op = bool(seed and _is_unchanged(seed, outputs))
    if not dry_run and not no_op:
        write_outputs_atomic(outputs, root / "data")
    return {
        "ok": True,
        "dryRun": dry_run,
        "dataAsOf": outputs.summary["dataAsOf"],
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
    if not history_path.exists() or not summary_path.exists() or not dashboard_path.exists():
        return None
    try:
        history = json.loads(history_path.read_text(encoding="utf-8"))
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        dashboard = json.loads(dashboard_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    rows = history.get("series") if isinstance(history, dict) else None
    if (
        not isinstance(rows, list)
        or history.get("fixture") is not False
        or history.get("methodologyVersion") != "fear-flow-v1"
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
        "p069500Open",
        "p069500Close",
    }
    if len(usable) < 252 or any(not required.issubset(row) for row in usable[-5:]):
        return None
    dates = sorted(date.fromisoformat(str(row["date"])) for row in usable)
    mutable_start = dates[-5]
    frozen = [row for row in usable if str(row["date"]) < mutable_start.isoformat()]
    if len(frozen) < 247:
        return None
    kospi, flow, adjusted = _frames_from_history(frozen)
    return IncrementalSeed(
        mutable_start=mutable_start,
        data_as_of=str(history.get("dataAsOf", usable[-1]["date"])),
        status_state=str(summary.get("status", {}).get("state", "unavailable")),
        existing_signature=_output_signature(summary, dashboard, history),
        history_rows=usable,
        kospi=kospi,
        flow=flow,
        adjusted=adjusted,
    )


def _frames_from_history(
    rows: list[dict[str, object]],
) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, pd.DataFrame]]:
    kospi_rows: list[dict[str, object]] = []
    flow_rows: list[dict[str, object]] = []
    adjusted_rows: dict[str, list[dict[str, object]]] = {
        "^KS11": [],
        "226490.KS": [],
        "069500.KS": [],
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
        flow_rows.append(
            {
                "date": timestamp,
                "individual_net_purchase": float(row["flowShare"]),
                "flow_share_override": float(row["flowShare"]),
                "raw_flow_trillion_override": float(row["rawFlowTrillion"]),
                "source_hash_override": str(row["sourceHash"]),
            }
        )
        adjusted_rows["^KS11"].append(_price_row(timestamp, close, close))
        for ticker in ("226490", "069500"):
            open_value = row.get(f"p{ticker}Open")
            close_value = row.get(f"p{ticker}Close")
            if open_value is not None and close_value is not None:
                adjusted_rows[f"{ticker}.KS"].append(
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


def _is_unchanged(seed: IncrementalSeed, outputs: PipelineOutputs) -> bool:
    current_signature = _output_signature(outputs.summary, outputs.dashboard, outputs.history)
    return current_signature == seed.existing_signature


def _preserve_frozen_history(seed: IncrementalSeed, outputs: PipelineOutputs) -> None:
    boundary = seed.mutable_start.isoformat()
    frozen = [row for row in seed.history_rows if str(row["date"]) < boundary]
    mutable = [row for row in outputs.history.get("series", []) if str(row["date"]) >= boundary]
    outputs.history["series"] = [*frozen, *mutable]


def _output_signature(
    summary: dict[str, object], dashboard: dict[str, object], history: dict[str, object]
) -> str:
    stable_summary = {
        key: value
        for key, value in summary.items()
        if key not in {"generatedAt", "automation"}
    }
    stable_dashboard = {key: value for key, value in dashboard.items() if key != "generatedAt"}
    stable_history = {key: value for key, value in history.items() if key != "generatedAt"}
    encoded = json.dumps(
        [stable_summary, stable_dashboard, stable_history],
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
    records: dict[str, list[dict[str, object]]] = {"226490": [], "069500": []}
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


def _open_api_reason(error: ProviderError) -> str:
    text = str(error)
    if "HTTP 401" in text:
        return "krx_open_api_http_401"
    if "HTTP 403" in text:
        return "krx_open_api_http_403"
    return "krx_open_api_unavailable"


def mark_failed(reason: str) -> None:
    root = repository_root()
    data_dir = root / "data"
    attempted_at = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    automation_path = data_dir / "automation-status.json"
    automation = {
        "schemaVersion": 1,
        "state": "degraded",
        "lastAttemptAt": attempted_at,
        "lastSuccessAt": None,
        "degradedReasons": [reason],
    }
    summary_path = data_dir / "summary.json"
    if summary_path.exists():
        try:
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
            previous = summary.get("status", {}).get("degradedReasons", [])
            summary.setdefault("status", {})["state"] = "degraded"
            summary["status"]["degradedReasons"] = list(dict.fromkeys([*previous, reason]))
            summary.setdefault("automation", {})["lastAttemptAt"] = attempted_at
            summary["automation"]["state"] = "degraded"
            automation["lastSuccessAt"] = summary.get("automation", {}).get("lastSuccessAt")
            summary_path.write_text(
                json.dumps(summary, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
                encoding="utf-8",
            )
        except (OSError, ValueError):
            pass
    automation_path.write_text(
        json.dumps(automation, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )


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


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Refresh Fear & Greed public derivatives")
    parser.add_argument("--probe", action="store_true", help="Run a sanitized provider smoke test")
    parser.add_argument("--date", type=date.fromisoformat, help="Probe or refresh end date")
    parser.add_argument("--backfill-start-date", type=date.fromisoformat)
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    end = args.date or default_end_date()
    if args.probe:
        return probe(end)
    try:
        with refresh_lock():
            receipt = refresh(
                end=end,
                backfill_start_date=args.backfill_start_date,
                dry_run=args.dry_run,
            )
    except ProviderError as error:
        reason = str(error)
        mark_failed(reason)
        print(json.dumps({"ok": False, "reason": reason}, ensure_ascii=False))
        return 1
    except Exception:
        reason = "refresh_pipeline_failed"
        mark_failed(reason)
        print(json.dumps({"ok": False, "reason": reason}, ensure_ascii=False))
        return 1
    print(json.dumps(receipt, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
