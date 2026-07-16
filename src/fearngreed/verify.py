from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
from typing import Any

import jsonschema
import requests

from .security import scan_public_files

PUBLIC_FILES = (
    "data/summary.json",
    "data/dashboard.json",
    "data/history.json",
    "data/automation-status.json",
)
SIZE_LIMITS = {
    "data/summary.json": 50_000,
    "data/dashboard.json": 500_000,
    "data/history.json": 2_000_000,
    "data/automation-status.json": 50_000,
}


def verify_local(root: Path, *, minimum_headroom_ratio: float = 0.05) -> dict[str, Any]:
    payloads: dict[str, dict[str, Any]] = {}
    hashes: dict[str, str] = {}
    sizes: dict[str, int] = {}
    headroom: dict[str, float] = {}
    for relative in PUBLIC_FILES:
        path = root / relative
        raw = path.read_bytes()
        payloads[relative] = json.loads(raw)
        hashes[relative] = hashlib.sha256(raw).hexdigest()
        sizes[relative] = len(raw)
        headroom[relative] = 1 - len(raw) / SIZE_LIMITS[relative]
        if len(raw) > SIZE_LIMITS[relative]:
            raise ValueError(f"public size limit exceeded: {relative}")
        if headroom[relative] < minimum_headroom_ratio:
            raise ValueError(f"public size headroom too small: {relative}")

    summary = payloads["data/summary.json"]
    dashboard = payloads["data/dashboard.json"]
    history = payloads["data/history.json"]
    automation = payloads["data/automation-status.json"]
    schema = json.loads((root / "schemas/summary.schema.json").read_text(encoding="utf-8"))
    jsonschema.Draft202012Validator(
        schema,
        format_checker=jsonschema.FormatChecker(),
    ).validate(summary)

    methodology_versions = {
        summary.get("methodologyVersion"),
        dashboard.get("methodologyVersion"),
        history.get("methodologyVersion"),
    }
    if len(methodology_versions) != 1 or None in methodology_versions:
        raise ValueError("public methodology versions do not match")
    data_dates = {
        summary.get("dataAsOf"),
        dashboard.get("dataAsOf"),
        history.get("dataAsOf"),
        automation.get("dataAsOf"),
    }
    if len(data_dates) != 1 or None in data_dates:
        raise ValueError("public dataAsOf values do not match")
    if automation.get("state") != summary.get("status", {}).get("state"):
        raise ValueError("automation and summary operational states do not match")
    _verify_history(history)
    _verify_history_channel_roles(history)
    _verify_cross_artifact_consistency(summary, dashboard, history)
    findings = scan_public_files(root)
    if findings:
        raise ValueError("credential material detected in public files")
    return {
        "ok": True,
        "methodologyVersion": next(iter(methodology_versions)),
        "dataAsOf": next(iter(data_dates)),
        "operationalState": automation.get("state"),
        "hashes": hashes,
        "sizes": sizes,
        "headroomRatio": {key: round(value, 6) for key, value in headroom.items()},
    }


def verify_remote(root: Path, base_url: str) -> dict[str, Any]:
    local = verify_local(root)
    remote_hashes: dict[str, str] = {}
    normalized = base_url.rstrip("/")
    for relative in PUBLIC_FILES:
        try:
            response = requests.get(f"{normalized}/{relative}", timeout=20)
        except requests.RequestException:
            raise ValueError(f"public readback request failed: {relative}") from None
        if response.status_code != 200:
            raise ValueError(f"public readback returned HTTP {response.status_code}: {relative}")
        digest = hashlib.sha256(response.content).hexdigest()
        remote_hashes[relative] = digest
        if digest != local["hashes"][relative]:
            raise ValueError(f"public readback hash mismatch: {relative}")
    return {**local, "baseUrl": normalized, "remoteHashes": remote_hashes}


def _verify_history(history: dict[str, Any]) -> None:
    rows = _decoded_history_rows(history)
    dates = [row.get("date") for row in rows]
    if not dates or any(not isinstance(value, str) for value in dates):
        raise ValueError("history dates are missing")
    if dates != sorted(dates) or len(dates) != len(set(dates)):
        raise ValueError("history dates must be unique and ascending")
    if dates[-1] != history.get("dataAsOf"):
        raise ValueError("history latest date does not match dataAsOf")


def _decoded_history_rows(history: dict[str, Any]) -> list[dict[str, Any]]:
    if history.get("seriesEncoding") == "columnar-v1":
        columns = history.get("seriesColumns")
        rows = history.get("seriesRows")
        if not isinstance(columns, list) or not columns or len(columns) != len(set(columns)):
            raise ValueError("invalid columnar history columns")
        if not isinstance(rows, list) or not rows:
            raise ValueError("columnar history is empty")
        if any(not isinstance(row, list) or len(row) != len(columns) for row in rows):
            raise ValueError("columnar history row width mismatch")
        if "date" not in columns:
            raise ValueError("history dates are missing")
        return [dict(zip(columns, row, strict=True)) for row in rows]
    else:
        rows = history.get("series")
        if not isinstance(rows, list) or not rows:
            raise ValueError("history series is empty")
        if not all(isinstance(row, dict) for row in rows):
            raise ValueError("history series contains non-object rows")
        return [dict(row) for row in rows]


def _verify_history_channel_roles(history: dict[str, Any]) -> None:
    roles = history.get("flowChannelRoles")
    if not isinstance(roles, dict) or roles.get("primaryChannel") != "retail":
        raise ValueError("history flow-channel roles are missing")
    if roles.get("strategyChannelCount") != 1:
        raise ValueError("history must expose exactly one strategy flow channel")
    channels = roles.get("channels")
    if not isinstance(channels, dict):
        raise ValueError("history flow-channel role map is missing")
    retail = channels.get("retail")
    if not isinstance(retail, dict) or retail.get("strategyUse") != "primary":
        raise ValueError("history retail channel must remain primary")
    if retail.get("eligibleForTrading") is not True:
        raise ValueError("history retail channel trading role is invalid")
    columns = set(history.get("seriesColumns", []))
    for channel_id in ("foreigner", "institutional"):
        channel = channels.get(channel_id)
        if not isinstance(channel, dict):
            raise ValueError(f"history {channel_id} role is missing")
        if channel.get("strategyUse") != "diagnostic_only":
            raise ValueError(f"history {channel_id} channel must remain diagnostic-only")
        if channel.get("eligibleForTrading") is not False:
            raise ValueError(f"history {channel_id} channel cannot be trading-eligible")
        if channel.get("activationRule") != (
            "requires_new_methodology_version_and_out_of_sample_plan"
        ):
            raise ValueError(f"history {channel_id} activation rule is missing")
        for field_key in ("stateField", "percentileField"):
            field = channel.get(field_key)
            if not isinstance(field, str) or field not in columns:
                raise ValueError(f"history {channel_id} {field_key} is invalid")


def _verify_cross_artifact_consistency(
    summary: dict[str, Any], dashboard: dict[str, Any], history: dict[str, Any]
) -> None:
    """Prove the default public backtest can be reproduced from public history."""
    decoded = _decoded_history_rows(history)
    for ticker in ("226490", "069500"):
        try:
            proxy = dashboard["backtests"]["proxies"][ticker]["fullPeriod"]["robust_10bp"]
            reconciliation = dashboard["crosschecks"]["etf"][ticker]["historyReconciliation"]
        except (KeyError, TypeError):
            raise ValueError(f"{ticker} proxy backtest contract is missing") from None
        if proxy.get("status") != "ok":
            continue
        if reconciliation.get("state") != "ok" or reconciliation.get("unresolvedCount") != 0:
            raise ValueError(f"{ticker} history reconciliation is not complete")
        filled_count = reconciliation.get("filledCount", 0)
        if not isinstance(filled_count, int) or filled_count < 0:
            raise ValueError(f"{ticker} reconciliation fill count is invalid")
        if filled_count:
            if reconciliation.get("source") != "yfinance_adjusted_plus_scaled_krx_gap_rows":
                raise ValueError(f"{ticker} reconciliation provenance is missing")
            reasons = summary.get("status", {}).get("degradedReasons", [])
            if f"adjusted_history_gap_reconciled_{ticker}" not in reasons:
                raise ValueError(f"{ticker} reconciliation degraded reason is missing")
        metrics = proxy.get("metrics")
        if not isinstance(metrics, dict):
            raise ValueError(f"{ticker} proxy backtest metrics are missing")
        start = metrics.get("start")
        end = metrics.get("end")
        if not isinstance(start, str) or not isinstance(end, str):
            raise ValueError(f"{ticker} proxy backtest period is missing")
        price_rows = [
            row
            for row in decoded
            if start <= str(row.get("date")) <= end
            and row.get(f"p{ticker}Open") is not None
            and row.get(f"p{ticker}Close") is not None
        ]
        official_count = reconciliation.get("officialSessionCount")
        if (
            not price_rows
            or not isinstance(official_count, int)
            or len(price_rows) != official_count
        ):
            raise ValueError(f"{ticker} history sessions do not match the reconciled backtest")
        if price_rows[0].get("date") != start or price_rows[-1].get("date") != end:
            raise ValueError(f"{ticker} history period does not match the default backtest")
        prices = [
            row.get(field) for row in price_rows for field in (f"p{ticker}Open", f"p{ticker}Close")
        ]
        if any(
            not isinstance(value, int | float)
            or not math.isfinite(float(value))
            or float(value) <= 0
            for value in prices
        ):
            raise ValueError(f"{ticker} history contains an invalid proxy price")

    try:
        backtest = dashboard["backtests"]["proxies"]["226490"]["fullPeriod"]["robust_10bp"]
    except (KeyError, TypeError):
        raise ValueError("default proxy backtest contract is missing") from None
    if backtest.get("status") != "ok":
        return
    metrics = backtest.get("metrics")
    if not isinstance(metrics, dict):
        raise ValueError("default proxy backtest metrics are missing")
    start = metrics.get("start")
    end = metrics.get("end")
    if not isinstance(start, str) or not isinstance(end, str):
        raise ValueError("default proxy backtest period is missing")
    rows = [
        row
        for row in decoded
        if start <= str(row.get("date")) <= end
        and row.get("p226490Open") is not None
        and row.get("p226490Close") is not None
    ]
    positions = [row.get("position") for row in rows]
    if any(position not in {"cash", "long"} for position in positions):
        raise ValueError("226490 history contains an invalid strategy position")
    exposure = positions.count("long") / len(positions)
    reported_exposure = metrics.get("exposure")
    if not isinstance(reported_exposure, int | float) or not math.isclose(
        exposure, float(reported_exposure), rel_tol=1e-9, abs_tol=1e-9
    ):
        raise ValueError("226490 history exposure does not match the default backtest")
    runs = sum(
        position == "long" and (index == 0 or positions[index - 1] != "long")
        for index, position in enumerate(positions)
    )
    expected_closed_trades = runs - (1 if backtest.get("openPosition") else 0)
    trade_count = metrics.get("tradeCount")
    if trade_count != expected_closed_trades:
        raise ValueError("226490 history trades do not match the default backtest")
    if summary.get("coverage", {}).get("tradeCount") != trade_count:
        raise ValueError("summary trade count does not match the default backtest")
    entities = summary.get("primaryEntities")
    if not isinstance(entities, list) or not entities or not isinstance(entities[0], dict):
        raise ValueError("summary primary entity is missing")
    if entities[0].get("position") != positions[-1]:
        raise ValueError("summary position does not match public history")
    if (
        dashboard["crosschecks"]["etf"]["226490"]["historyReconciliation"].get("filledCount", 0)
        and entities[0].get("fieldSources", {}).get("adjustedProxy")
        != "yfinance_adjusted_plus_scaled_krx_gap_rows"
    ):
        raise ValueError("summary adjusted-proxy provenance is missing")


def repository_root() -> Path:
    return Path(__file__).resolve().parents[2]


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify local and deployed public artifacts")
    parser.add_argument(
        "--base-url", help="Optional deployed Pages root for byte-for-byte readback"
    )
    parser.add_argument("--minimum-headroom-ratio", type=float, default=0.05)
    args = parser.parse_args()
    root = repository_root()
    receipt = (
        verify_remote(root, args.base_url)
        if args.base_url
        else verify_local(root, minimum_headroom_ratio=args.minimum_headroom_ratio)
    )
    print(json.dumps(receipt, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
