from __future__ import annotations

import argparse
import hashlib
import json
import math
from datetime import date, datetime
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
    "data/strategy-comparison.json",
    "data/live-signal.json",
)
SIZE_LIMITS = {
    "data/summary.json": 50_000,
    "data/dashboard.json": 500_000,
    "data/history.json": 2_000_000,
    "data/automation-status.json": 50_000,
    "data/strategy-comparison.json": 500_000,
    "data/live-signal.json": 50_000,
}
BROWSER_SCENARIO_INPUTS = {
    "lookback": {"default": 196, "minimum": 60, "maximum": 756, "step": 1},
    "minimumR2": {"default": 0.4, "minimum": 0, "maximum": 0.8, "step": 0.05},
    "extremeTail": {"default": 2, "minimum": 1, "maximum": 20, "step": 1},
    "maxHolding": {"default": 20, "minimum": 1, "maximum": 60, "step": 1},
}
MINIMUM_TRAINING_OBSERVATIONS_FORMULA = "min(lookback,max(40,min(200,ceil(lookback*0.8))))"
HISTORY_NUMERIC_PRECISION_DIGITS = 8
ETF_PRICE_TICKERS = ("226490", "069500", "114800", "122630", "252670")


def verify_local(
    root: Path,
    *,
    minimum_headroom_ratio: float = 0.05,
    expected_data_as_of: date | str | None = None,
) -> dict[str, Any]:
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
    strategy = payloads["data/strategy-comparison.json"]
    live_signal = payloads["data/live-signal.json"]
    schema = json.loads((root / "schemas/summary.schema.json").read_text(encoding="utf-8"))
    jsonschema.Draft202012Validator(
        schema,
        format_checker=jsonschema.FormatChecker(),
    ).validate(summary)
    live_schema = json.loads((root / "schemas/live-signal.schema.json").read_text(encoding="utf-8"))
    jsonschema.Draft202012Validator(
        live_schema,
        format_checker=jsonschema.FormatChecker(),
    ).validate(live_signal)

    methodology_versions = {
        summary.get("methodologyVersion"),
        dashboard.get("methodologyVersion"),
        history.get("methodologyVersion"),
        strategy.get("methodologyVersion"),
    }
    if len(methodology_versions) != 1 or None in methodology_versions:
        raise ValueError("public methodology versions do not match")
    data_dates = {
        summary.get("dataAsOf"),
        dashboard.get("dataAsOf"),
        history.get("dataAsOf"),
        automation.get("dataAsOf"),
        strategy.get("dataAsOf"),
    }
    if len(data_dates) != 1 or None in data_dates:
        raise ValueError("public dataAsOf values do not match")
    data_as_of = next(iter(data_dates))
    _verify_summary_freshness(summary, data_as_of, expected_data_as_of)
    if automation.get("state") != summary.get("status", {}).get("state"):
        raise ValueError("automation and summary operational states do not match")
    _verify_history(history)
    _verify_history_channel_roles(history)
    _verify_scatter_state_boundaries(dashboard)
    _verify_etf_price_contract(summary, dashboard, history)
    _verify_strategy_comparison(summary, dashboard, history, strategy)
    _verify_cross_artifact_consistency(summary, dashboard, history)
    _verify_live_signal(live_signal, summary)
    findings = scan_public_files(root)
    if findings:
        raise ValueError("credential material detected in public files")
    return {
        "ok": True,
        "methodologyVersion": next(iter(methodology_versions)),
        "dataAsOf": data_as_of,
        "operationalState": automation.get("state"),
        "hashes": hashes,
        "sizes": sizes,
        "headroomRatio": {key: round(value, 6) for key, value in headroom.items()},
    }


def _verify_summary_freshness(
    summary: dict[str, Any],
    data_as_of: Any,
    expected_data_as_of: date | str | None = None,
) -> None:
    """Validate fresh, known-stale, and provider-unconfirmed status shapes."""

    if not isinstance(data_as_of, str):
        raise ValueError("public dataAsOf is invalid")
    try:
        data_date = date.fromisoformat(data_as_of)
    except ValueError:
        raise ValueError("public dataAsOf is invalid") from None

    status = summary.get("status")
    if not isinstance(status, dict):
        raise ValueError("summary freshness status is missing")
    state = status.get("state")
    basis = status.get("freshnessBasis")
    published_expected = status.get("expectedDataAsOf")
    freshness_passed = status.get("sourceFreshnessPassed")

    cli_expected = (
        expected_data_as_of.isoformat()
        if isinstance(expected_data_as_of, date)
        else expected_data_as_of
    )
    if cli_expected is not None:
        try:
            date.fromisoformat(cli_expected)
        except (TypeError, ValueError):
            raise ValueError("expected public data date is invalid") from None
        if not (
            basis == "official_krx_latest_completed_session"
            and freshness_passed is True
            and data_as_of == published_expected == cli_expected
            and state in {"ok", "degraded"}
        ):
            raise ValueError("public freshness does not match the required official KRX session")
        return

    if basis == "source_alignment_only":
        if published_expected is not None:
            raise ValueError("source-alignment summary cannot publish an expected session")
        if freshness_passed not in {True, False}:
            raise ValueError("summary sourceFreshnessPassed is invalid")
        allowed_states = (
            {"ok", "degraded"}
            if freshness_passed is True
            else {
                "degraded",
                "unavailable",
            }
        )
        if state not in allowed_states:
            raise ValueError("source-alignment freshness and operational state disagree")
        return

    if freshness_passed is True:
        if basis != "official_krx_latest_completed_session" or published_expected != data_as_of:
            raise ValueError("fresh summary must match its official expected session")
        if state not in {"ok", "degraded"}:
            raise ValueError("fresh summary must publish an available operational state")
        return

    if freshness_passed is not False:
        raise ValueError("summary sourceFreshnessPassed is invalid")

    if basis == "official_krx_latest_completed_session":
        if not isinstance(published_expected, str):
            raise ValueError("known-stale summary must publish an official expected session")
        try:
            expected_date = date.fromisoformat(published_expected)
        except ValueError:
            raise ValueError("summary expectedDataAsOf is invalid") from None
        if expected_date <= data_date:
            raise ValueError("known-stale expectedDataAsOf must be later than dataAsOf")
        if state not in {"stale", "unavailable"}:
            raise ValueError("known-stale summary must publish a stale operational state")
        return

    raise ValueError("summary freshnessBasis is invalid")


def verify_remote(
    root: Path,
    base_url: str,
    *,
    expected_data_as_of: date | str | None = None,
) -> dict[str, Any]:
    local = verify_local(root, expected_data_as_of=expected_data_as_of)
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


def _verify_live_signal(live: dict[str, Any], summary: dict[str, Any]) -> None:
    """Keep the fast same-day observation outside confirmed research artifacts."""

    try:
        signal_date = date.fromisoformat(str(live.get("signalDate")))
        history_date = date.fromisoformat(str(live.get("historyDataAsOf")))
        confirmed_date = date.fromisoformat(str(summary.get("dataAsOf")))
    except ValueError:
        raise ValueError("live signal date contract is invalid") from None
    if history_date >= signal_date or (
        signal_date > confirmed_date and history_date != confirmed_date
    ):
        raise ValueError("live signal history anchor is invalid")
    if live.get("methodologyVersion") != summary.get("methodologyVersion"):
        raise ValueError("live signal methodology does not match confirmed research")
    input_row = live.get("inputRow")
    if not isinstance(input_row, dict) or input_row.get("date") != live.get("signalDate"):
        raise ValueError("live signal input date does not match signalDate")
    window = live.get("actionWindow")
    if not isinstance(window, dict):
        raise ValueError("live signal action window is missing")
    try:
        opens_at = datetime.fromisoformat(str(window.get("opensAt")))
        closes_at = datetime.fromisoformat(str(window.get("closesAt")))
    except ValueError:
        raise ValueError("live signal action window is invalid") from None
    if opens_at.tzinfo is None or closes_at.tzinfo is None or opens_at >= closes_at:
        raise ValueError("live signal action window is invalid")
    if opens_at.date() != signal_date or closes_at.date() != signal_date:
        raise ValueError("live signal action window date does not match signalDate")
    try:
        generated_at = datetime.fromisoformat(str(live.get("generatedAt")).replace("Z", "+00:00"))
    except ValueError:
        raise ValueError("live signal generatedAt is invalid") from None
    if generated_at.tzinfo is None or not (opens_at <= generated_at < closes_at):
        raise ValueError("live signal capture time is outside its provisional window")


def _verify_history(history: dict[str, Any]) -> None:
    if history.get("numericPrecisionDigits") != HISTORY_NUMERIC_PRECISION_DIGITS:
        raise ValueError("history numeric precision contract is invalid")
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


def _verify_etf_price_contract(
    summary: dict[str, Any], dashboard: dict[str, Any], history: dict[str, Any]
) -> None:
    """Require every published actual-pair ETF to be independently validated."""
    columns = set(history.get("seriesColumns", []))
    rows = _decoded_history_rows(history)
    latest = rows[-1]
    crosschecks = dashboard.get("crosschecks", {}).get("etf", {})
    reasons = summary.get("status", {}).get("degradedReasons", [])
    if not isinstance(crosschecks, dict) or not isinstance(reasons, list):
        raise ValueError("ETF price crosscheck contract is missing")
    for ticker in ETF_PRICE_TICKERS:
        required_fields = {f"p{ticker}Open", f"p{ticker}Close"}
        if not required_fields.issubset(columns):
            raise ValueError(f"{ticker} ETF history price fields are missing")
        check = crosschecks.get(ticker)
        if not isinstance(check, dict):
            raise ValueError(f"{ticker} ETF price crosscheck is missing")
        reconciliation = check.get("historyReconciliation")
        if not isinstance(reconciliation, dict):
            raise ValueError(f"{ticker} ETF history reconciliation is missing")
        if check.get("state") == "ok":
            if reconciliation.get("state") != "ok" or reconciliation.get("unresolvedCount") != 0:
                raise ValueError(f"{ticker} ETF history reconciliation is incomplete")
            prices = [latest.get(field) for field in required_fields]
            if any(
                not isinstance(value, int | float)
                or not math.isfinite(float(value))
                or float(value) <= 0
                for value in prices
            ):
                raise ValueError(f"{ticker} latest ETF history prices are invalid")
        elif f"price_crosscheck_{ticker}_{check.get('state')}" not in reasons:
            raise ValueError(f"{ticker} ETF crosscheck degraded reason is missing")


def _verify_scatter_state_boundaries(dashboard: dict[str, Any]) -> None:
    """Verify that displayed state bands are sourced from the published fit."""

    for model in ("robust", "scaled", "raw"):
        try:
            points = dashboard["scatterByModel"][model]
            meta = dashboard["scatterMetaByModel"][model]
            snapshot = dashboard["models"][model]
            regression = dashboard["regression"][model]
        except (KeyError, TypeError):
            raise ValueError(f"{model} scatter boundary contract is missing") from None
        if not isinstance(points, list) or not isinstance(meta, dict):
            raise ValueError(f"{model} scatter boundary inputs are invalid")
        boundaries = meta.get("stateBoundaries")
        if snapshot.get("state") == "unavailable":
            if boundaries is not None:
                raise ValueError(f"{model} unavailable fit cannot publish state boundaries")
            continue
        if not isinstance(boundaries, dict):
            raise ValueError(f"{model} scatter state boundaries are missing")
        if boundaries.get("method") != "empirical_cdf_transition_order_statistic":
            raise ValueError(f"{model} scatter boundary method is invalid")
        if boundaries.get("fitScope") != "current_fit_on_prior_window":
            raise ValueError(f"{model} scatter boundary fit scope is invalid")
        if boundaries.get("percentileCuts") != {
            "extremeFearUpper": 5,
            "fearUpper": 20,
            "greedLower": 80,
            "extremeGreedLower": 95,
        }:
            raise ValueError(f"{model} scatter boundary percentile cuts are invalid")
        if boundaries.get("comparators") != {
            "extremeFear": "residual < extremeFearUpper",
            "fear": "extremeFearUpper <= residual < fearUpper",
            "neutral": "fearUpper <= residual < greedLower",
            "greed": "greedLower <= residual < extremeGreedLower",
            "extremeGreed": "residual >= extremeGreedLower",
        }:
            raise ValueError(f"{model} scatter boundary comparators are invalid")
        training = [point for point in points if point.get("role") == "training"]
        if boundaries.get("trainingCount") != len(training) or not training:
            raise ValueError(f"{model} scatter boundary training count is invalid")
        value_field = "rawFlowTrillion" if model == "raw" else "flowShare"
        alpha = regression.get("alpha")
        beta = regression.get("beta")
        if not all(
            isinstance(value, int | float) and math.isfinite(float(value))
            for value in (alpha, beta)
        ):
            raise ValueError(f"{model} scatter regression is invalid")
        try:
            residuals = sorted(
                float(point[value_field]) - (float(alpha) + float(beta) * float(point["return1d"]))
                for point in training
            )
        except (KeyError, TypeError, ValueError):
            raise ValueError(f"{model} scatter training points are invalid") from None
        count = len(residuals)
        expected = {
            "extremeFearUpper": residuals[min(count - 1, math.floor(0.05 * count))],
            "fearUpper": residuals[min(count - 1, math.floor(0.20 * count))],
            "greedLower": residuals[max(0, math.ceil(0.80 * count) - 1)],
            "extremeGreedLower": residuals[max(0, math.ceil(0.95 * count) - 1)],
        }
        offsets = boundaries.get("residualOffsets")
        if not isinstance(offsets, dict):
            raise ValueError(f"{model} scatter residual offsets are missing")
        values: list[float] = []
        for key, expected_value in expected.items():
            value = offsets.get(key)
            if not isinstance(value, int | float) or not math.isfinite(float(value)):
                raise ValueError(f"{model} scatter residual offset {key} is invalid")
            if not math.isclose(float(value), expected_value, rel_tol=0, abs_tol=5e-8):
                raise ValueError(f"{model} scatter residual offset {key} is inconsistent")
            values.append(float(value))
        if values != sorted(values):
            raise ValueError(f"{model} scatter residual offsets are not ordered")


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
        backtest = dashboard["backtests"]["proxies"]["069500"]["fullPeriod"]["robust_10bp"]
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
    pre_start_rows = [row for row in decoded if str(row.get("date")) < start]
    if any(row.get("position") != "unavailable" for row in pre_start_rows):
        raise ValueError("069500 pre-backtest history position must be unavailable")
    rows = [
        row
        for row in decoded
        if start <= str(row.get("date")) <= end
        and row.get("p069500Open") is not None
        and row.get("p069500Close") is not None
    ]
    positions = [row.get("position") for row in rows]
    if any(position not in {"cash", "long"} for position in positions):
        raise ValueError("069500 history contains an invalid strategy position")
    exposure = positions.count("long") / len(positions)
    reported_exposure = metrics.get("exposure")
    if not isinstance(reported_exposure, int | float) or not math.isclose(
        exposure, float(reported_exposure), rel_tol=1e-9, abs_tol=1e-9
    ):
        raise ValueError("069500 history exposure does not match the default backtest")
    runs = sum(
        position == "long" and (index == 0 or positions[index - 1] != "long")
        for index, position in enumerate(positions)
    )
    expected_closed_trades = runs - (1 if backtest.get("openPosition") else 0)
    trade_count = metrics.get("tradeCount")
    if trade_count != expected_closed_trades:
        raise ValueError("069500 history trades do not match the default backtest")
    if summary.get("coverage", {}).get("tradeCount") != trade_count:
        raise ValueError("summary trade count does not match the default backtest")
    entities = summary.get("primaryEntities")
    if not isinstance(entities, list) or not entities or not isinstance(entities[0], dict):
        raise ValueError("summary primary entity is missing")
    if entities[0].get("position") != positions[-1]:
        raise ValueError("summary position does not match public history")
    if entities[0].get("primaryProxy") != "069500":
        raise ValueError("summary primary proxy must be the actual KOSPI 200 ETF")
    if (
        dashboard["crosschecks"]["etf"]["069500"]["historyReconciliation"].get("filledCount", 0)
        and entities[0].get("fieldSources", {}).get("adjustedProxy")
        != "yfinance_adjusted_plus_scaled_krx_gap_rows"
    ):
        raise ValueError("summary adjusted-proxy provenance is missing")


def _verify_actual_etf_pairs(strategy: dict[str, Any]) -> None:
    section = strategy.get("actualEtfPairs")
    if not isinstance(section, dict):
        raise ValueError("actual ETF pair contract is missing")
    if (
        section.get("authority") != "canonical_server_verified_actual_etfs"
        or section.get("canonical") is not True
        or section.get("calculationSource") != "python_verified_actual_etfs"
        or section.get("implementation") != "positive_units_in_listed_long_and_inverse_etfs"
    ):
        raise ValueError("actual ETF pair authority is invalid")
    if section.get("oneWayCostBps") != 10:
        raise ValueError("actual ETF pair cost assumption is invalid")
    if section.get("longExitPercentile") != 80 or section.get("inverseExitPercentile") != 20:
        raise ValueError("actual ETF pair exit thresholds are invalid")
    expected_pairs = {
        "1x": {"leverage": 1, "longTicker": "069500", "inverseTicker": "114800"},
        "2x": {"leverage": 2, "longTicker": "122630", "inverseTicker": "252670"},
    }
    common = section.get("commonPeriod")
    if not isinstance(common, dict) or common.get("basis") != (
        "four_etf_common_adjusted_price_sessions"
    ):
        raise ValueError("actual ETF four-fund common period is missing")
    if common.get("tickers") != ["069500", "114800", "122630", "252670"]:
        raise ValueError("actual ETF common-period ticker set is invalid")
    common_ok = common.get("status") == "ok"
    if common_ok:
        if not isinstance(common.get("start"), str) or not isinstance(common.get("end"), str):
            raise ValueError("actual ETF common-period dates are missing")
        if not isinstance(common.get("sessionCount"), int) or common["sessionCount"] < 2:
            raise ValueError("actual ETF common-period session count is invalid")
    elif common.get("reason") != "four_etf_common_period_unavailable":
        raise ValueError("actual ETF common-period failure reason is missing")
    pairs = section.get("pairs")
    if not isinstance(pairs, dict) or set(pairs) != set(expected_pairs):
        raise ValueError("actual ETF pair set is invalid")
    for pair_id, expected in expected_pairs.items():
        pair_section = pairs[pair_id]
        if not isinstance(pair_section, dict):
            raise ValueError(f"actual ETF {pair_id} contract is invalid")
        metadata = pair_section.get("pair")
        if not isinstance(metadata, dict) or any(
            metadata.get(field) != value for field, value in expected.items()
        ):
            raise ValueError(f"actual ETF {pair_id} metadata is invalid")
        if metadata.get("pairId") != pair_id or metadata.get("implementation") != (
            "actual_listed_etfs"
        ):
            raise ValueError(f"actual ETF {pair_id} implementation is invalid")
        policies = pair_section.get("policies")
        if not isinstance(policies, dict) or set(policies) != {
            "long_cash",
            "long_inverse_cash",
        }:
            raise ValueError(f"actual ETF {pair_id} policy set is invalid")
        for policy_id, result in policies.items():
            _verify_actual_etf_policy(
                result,
                pair_id=pair_id,
                policy_id=policy_id,
                long_ticker=str(expected["longTicker"]),
                inverse_ticker=str(expected["inverseTicker"]),
                common=common,
            )
        statuses = {result.get("status") for result in policies.values()}
        expected_status = "ok" if statuses == {"ok"} else "unavailable"
        if pair_section.get("status") != expected_status:
            raise ValueError(f"actual ETF {pair_id} aggregate status is inconsistent")
        if expected_status == "unavailable" and not isinstance(pair_section.get("reason"), str):
            raise ValueError(f"actual ETF {pair_id} failure reason is missing")
        full_metrics = pair_section.get("fullPeriodMetrics")
        if not isinstance(full_metrics, dict) or set(full_metrics) != set(policies):
            raise ValueError(f"actual ETF {pair_id} full-period metrics are missing")


def _verify_actual_etf_policy(
    result: Any,
    *,
    pair_id: str,
    policy_id: str,
    long_ticker: str,
    inverse_ticker: str,
    common: dict[str, Any],
) -> None:
    if not isinstance(result, dict):
        raise ValueError(f"actual ETF {pair_id} {policy_id} result is invalid")
    metadata = result.get("pair")
    if (
        not isinstance(metadata, dict)
        or metadata.get("pairId") != pair_id
        or metadata.get("longTicker") != long_ticker
        or metadata.get("inverseTicker") != inverse_ticker
        or metadata.get("implementation") != "actual_listed_etfs"
    ):
        raise ValueError(f"actual ETF {pair_id} {policy_id} metadata is invalid")
    if result.get("policyId") != policy_id or result.get("calculationSource") != (
        "python_verified_actual_etfs"
    ):
        raise ValueError(f"actual ETF {pair_id} {policy_id} authority is invalid")
    if result.get("oneWayCostBps") != 10 or result.get("longExitPercentile") != 80:
        raise ValueError(f"actual ETF {pair_id} {policy_id} assumptions are invalid")
    if result.get("inverseExitPercentile") != 20:
        raise ValueError(f"actual ETF {pair_id} {policy_id} inverse exit is invalid")
    if result.get("status") == "unavailable":
        if (
            result.get("position") != "unavailable"
            or not isinstance(result.get("unavailableReason"), str)
            or result.get("metrics") is not None
        ):
            raise ValueError(f"actual ETF {pair_id} unavailable result is unsafe")
        return
    if result.get("status") != "ok":
        raise ValueError(f"actual ETF {pair_id} {policy_id} status is invalid")
    position = result.get("position")
    if position not in {"cash", "long", "inverse"}:
        raise ValueError(f"actual ETF {pair_id} position is invalid")
    expected_latest = (
        long_ticker if position == "long" else inverse_ticker if position == "inverse" else None
    )
    if result.get("latestInstrumentTicker") != expected_latest:
        raise ValueError(f"actual ETF {pair_id} latest instrument is inconsistent")
    metrics = result.get("metrics")
    if not isinstance(metrics, dict) or metrics.get("implementation") != "actual_listed_etfs":
        raise ValueError(f"actual ETF {pair_id} metrics implementation is invalid")
    exposures = [
        metrics.get("longExposure"),
        metrics.get("inverseExposure"),
        metrics.get("cashExposure"),
    ]
    if any(not isinstance(value, int | float) or float(value) < 0 for value in exposures):
        raise ValueError(f"actual ETF {pair_id} exposure is invalid")
    if not math.isclose(sum(map(float, exposures)), 1.0, rel_tol=0, abs_tol=1e-9):
        raise ValueError(f"actual ETF {pair_id} exposure sum is invalid")
    if metrics.get("shortExposure") != 0 or metrics.get("shortTradeCount") != 0:
        raise ValueError(f"actual ETF {pair_id} cannot publish synthetic short exposure")
    if policy_id == "long_cash" and (
        metrics.get("inverseExposure") != 0 or metrics.get("inverseTradeCount") != 0
    ):
        raise ValueError(f"actual ETF {pair_id} long-cash policy used the inverse fund")
    for trade in result.get("trades", []):
        if not isinstance(trade, dict) or trade.get("side") not in {"long", "inverse"}:
            raise ValueError(f"actual ETF {pair_id} trade side is invalid")
        expected_ticker = long_ticker if trade["side"] == "long" else inverse_ticker
        if trade.get("instrument_ticker") != expected_ticker:
            raise ValueError(f"actual ETF {pair_id} trade ticker is inconsistent")
    for row in result.get("equity", []):
        if not isinstance(row, dict) or row.get("position") not in {
            "cash",
            "long",
            "inverse",
        }:
            raise ValueError(f"actual ETF {pair_id} equity position is invalid")
        expected_ticker = (
            long_ticker
            if row["position"] == "long"
            else inverse_ticker
            if row["position"] == "inverse"
            else None
        )
        if row.get("instrumentTicker") != expected_ticker:
            raise ValueError(f"actual ETF {pair_id} equity ticker is inconsistent")
    range_meta = result.get("range")
    if common.get("status") == "ok" and (
        not isinstance(range_meta, dict)
        or range_meta.get("appliedStartDate") != common.get("start")
        or range_meta.get("appliedEndDate") != common.get("end")
    ):
        raise ValueError(f"actual ETF {pair_id} common-period range is inconsistent")


def _verify_strategy_comparison(
    summary: dict[str, Any],
    dashboard: dict[str, Any],
    history: dict[str, Any],
    strategy: dict[str, Any],
) -> None:
    if strategy.get("schemaVersion") != 1 or strategy.get("contract") != (
        "fearngreed-strategy-comparison"
    ):
        raise ValueError("strategy comparison contract is invalid")
    if summary.get("payload", {}).get("strategyComparisonUrl") != ("./strategy-comparison.json"):
        raise ValueError("summary strategy comparison URL is missing")
    control = strategy.get("dynamicExitControl")
    if not isinstance(control, dict) or control != {
        "defaultLongExitPercentile": 80,
        "minimum": 50,
        "maximum": 94,
        "step": 1,
        "shortExitFormula": "100-longExitPercentile",
        "inverseExitFormula": "100-longExitPercentile",
        "calculationLocation": "browser_on_server_published_history_and_adjusted_prices",
        "regressionRefit": True,
        "signalEngineVersion": "browser-past-only-rolling-v1",
        "scenarioAuthority": "browser_user_scenario_not_canonical_server_output",
        "configurableInputs": BROWSER_SCENARIO_INPUTS,
        "minimumTrainingObservationsFormula": MINIMUM_TRAINING_OBSERVATIONS_FORMULA,
        "pastOnly": True,
        "evaluationRangeSeparate": True,
    }:
        raise ValueError("dynamic exit-control contract is invalid")
    _verify_history_strategy_scenario(history, control)
    definitions = strategy.get("policyDefinitions")
    if not isinstance(definitions, dict):
        raise ValueError("strategy policy definitions are missing")
    actual = definitions.get("longInverseCash")
    if not isinstance(actual, dict) or actual.get("policyId") != "long_inverse_cash":
        raise ValueError("actual long-inverse policy definition is missing")
    if (
        actual.get("role") != "canonical_actual_listed_etf_research"
        or actual.get("positionAccounting") != "positive_listed_etf_units_no_synthetic_short"
        or actual.get("shortExposure") != 0
        or actual.get("borrowRequired") is not False
    ):
        raise ValueError("actual long-inverse policy definition is unsafe")
    synthetic = definitions.get("longShortCash")
    if not isinstance(synthetic, dict) or synthetic.get("policyId") != "long_short_cash":
        raise ValueError("synthetic long-short policy definition is missing")
    if (
        synthetic.get("borrowFeeAnnualPct") != 0
        or synthetic.get("shortabilityModeled") is not False
    ):
        raise ValueError("synthetic short exclusions are not explicit")
    if synthetic.get("role") != "legacy_diagnostic_backward_compatibility":
        raise ValueError("synthetic proxy role must remain legacy diagnostic")
    legacy = strategy.get("legacyProxyContract")
    if not isinstance(legacy, dict) or legacy != {
        "canonical": False,
        "role": "legacy_diagnostic_backward_compatibility",
        "implementation": "synthetic_short_or_single_long_proxy",
    }:
        raise ValueError("legacy proxy contract is missing")
    _verify_actual_etf_pairs(strategy)
    proxies = strategy.get("proxies")
    if not isinstance(proxies, dict):
        raise ValueError("strategy comparison proxies are missing")
    if "069500" not in proxies:
        raise ValueError("canonical 069500 compatibility proxy is missing")
    unexpected_legacy = set(proxies).difference({"226490", "069500"})
    if unexpected_legacy:
        raise ValueError("strategy comparison contains an unknown legacy proxy")
    # 226490 is now a diagnostic compatibility proxy.  Its provider failure
    # must not block otherwise valid 069500/114800 and 122630/252670 results.
    for ticker in proxies:
        synthetic_proxy = proxies.get(ticker)
        dashboard_proxy = dashboard.get("backtests", {}).get("proxies", {}).get(ticker)
        if not isinstance(synthetic_proxy, dict) or not isinstance(dashboard_proxy, dict):
            raise ValueError(f"{ticker} strategy comparison proxy is missing")
        for public_period, dashboard_period in (
            ("fullPeriod", "fullPeriod"),
            ("commonPeriod", "commonPeriod"),
        ):
            synthetic_section = synthetic_proxy.get(public_period)
            long_cash_section = dashboard_proxy.get(dashboard_period)
            if not isinstance(synthetic_section, dict) or not isinstance(long_cash_section, dict):
                raise ValueError(f"{ticker} strategy comparison period is missing")
            result = synthetic_section.get("robust_10bp")
            long_cash = long_cash_section.get("robust_10bp")
            if not isinstance(result, dict) or not isinstance(long_cash, dict):
                raise ValueError(f"{ticker} default strategy comparison is missing")
            if long_cash.get("policyId") != "long_cash":
                raise ValueError(f"{ticker} default long-cash policy id is invalid")
            if long_cash.get("longExitPercentile") != 80:
                raise ValueError(f"{ticker} default long-cash exit threshold is invalid")
            if result.get("policyId") != "long_short_cash":
                raise ValueError(f"{ticker} synthetic policy id is invalid")
            if result.get("longExitPercentile") != 80 or result.get("shortExitPercentile") != 20:
                raise ValueError(f"{ticker} synthetic exit thresholds are invalid")
            status = result.get("status")
            if status == "unavailable":
                _verify_unavailable_strategy_result(result, ticker)
                continue
            if status != "ok":
                raise ValueError(f"{ticker} synthetic strategy status is invalid")
            if long_cash.get("status") != "ok":
                raise ValueError(f"{ticker} long-cash comparison is unavailable")
            if result.get("position") not in {"cash", "long", "short"}:
                raise ValueError(f"{ticker} synthetic position is invalid")
            metrics = result.get("metrics")
            long_cash_metrics = long_cash.get("metrics")
            if not isinstance(metrics, dict) or not isinstance(long_cash_metrics, dict):
                raise ValueError(f"{ticker} strategy comparison metrics are missing")
            if (metrics.get("start"), metrics.get("end")) != (
                long_cash_metrics.get("start"),
                long_cash_metrics.get("end"),
            ):
                raise ValueError(f"{ticker} strategy comparison dates do not match")
            components = [
                metrics.get("longExposure"),
                metrics.get("shortExposure"),
                metrics.get("cashExposure"),
            ]
            if not all(isinstance(value, int | float) for value in components) or not math.isclose(
                sum(float(value) for value in components), 1.0, rel_tol=0, abs_tol=1e-9
            ):
                raise ValueError(f"{ticker} strategy exposure components are invalid")
            gross = float(metrics.get("grossExposure", -1))
            net = float(metrics.get("netExposure", 2))
            if not math.isclose(gross, float(components[0]) + float(components[1]), abs_tol=1e-9):
                raise ValueError(f"{ticker} gross exposure is invalid")
            if not math.isclose(net, float(components[0]) - float(components[1]), abs_tol=1e-9):
                raise ValueError(f"{ticker} net exposure is invalid")
            if any(
                trade.get("side") not in {"long", "short"} for trade in result.get("trades", [])
            ):
                raise ValueError(f"{ticker} strategy trade side is invalid")


def _verify_history_strategy_scenario(history: dict[str, Any], control: dict[str, Any]) -> None:
    scenario = history.get("strategyScenario")
    expected = {
        "engineVersion": "actual-listed-etf-pairs-v1",
        "signalEngineVersion": "browser-past-only-rolling-v1",
        "defaultLongExitPercentile": 80,
        "customLongExitMinimum": 50,
        "customLongExitMaximum": 94,
        "customLongExitStep": 1,
        "shortExitFormula": "100-longExitPercentile",
        "inverseExitFormula": "100-longExitPercentile",
        "signalInputsAreServerPublished": True,
        "browserMayRefitRegression": True,
        "scenarioAuthority": "browser_user_scenario_not_canonical_server_output",
        "configurableInputs": BROWSER_SCENARIO_INPUTS,
        "minimumTrainingObservationsFormula": MINIMUM_TRAINING_OBSERVATIONS_FORMULA,
        "pastOnly": True,
        "evaluationRangeSeparate": True,
    }
    if not isinstance(scenario, dict) or scenario != expected:
        raise ValueError("history strategy-scenario contract is invalid")
    if (
        scenario["defaultLongExitPercentile"] != control["defaultLongExitPercentile"]
        or scenario["customLongExitMinimum"] != control["minimum"]
        or scenario["customLongExitMaximum"] != control["maximum"]
        or scenario["customLongExitStep"] != control["step"]
        or scenario["shortExitFormula"] != control["shortExitFormula"]
        or scenario["browserMayRefitRegression"] is not control["regressionRefit"]
        or scenario["signalEngineVersion"] != control["signalEngineVersion"]
        or scenario["scenarioAuthority"] != control["scenarioAuthority"]
        or scenario["configurableInputs"] != control["configurableInputs"]
        or scenario["minimumTrainingObservationsFormula"]
        != control["minimumTrainingObservationsFormula"]
        or scenario["pastOnly"] is not control["pastOnly"]
        or scenario["evaluationRangeSeparate"] is not control["evaluationRangeSeparate"]
    ):
        raise ValueError("history and strategy exit-control contracts do not match")


def _verify_unavailable_strategy_result(result: dict[str, Any], ticker: str) -> None:
    reason = result.get("unavailableReason")
    if not isinstance(reason, str) or not reason.strip():
        raise ValueError(f"{ticker} unavailable synthetic strategy reason is missing")
    if result.get("position") != "unavailable" or result.get("openPosition") is not False:
        raise ValueError(f"{ticker} unavailable synthetic strategy position is invalid")
    if any(
        result.get(field) is not None
        for field in ("pendingAction", "pendingReason", "pendingSide", "openTrade")
    ):
        raise ValueError(f"{ticker} unavailable synthetic strategy has pending state")
    if result.get("trades", []) != [] or result.get("equity", []) != []:
        raise ValueError(f"{ticker} unavailable synthetic strategy contains a partial path")
    metrics = result.get("metrics")
    if not isinstance(metrics, dict):
        raise ValueError(f"{ticker} unavailable synthetic strategy metrics are invalid")
    if metrics:
        if set(metrics).difference({"state", "reason"}) or metrics.get("state") != "unavailable":
            raise ValueError(f"{ticker} unavailable synthetic strategy metrics are unsafe")
        metric_reason = metrics.get("reason")
        if metric_reason is not None and metric_reason != reason:
            raise ValueError(f"{ticker} unavailable synthetic strategy reasons do not match")


def repository_root() -> Path:
    return Path(__file__).resolve().parents[2]


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify local and deployed public artifacts")
    parser.add_argument(
        "--base-url", help="Optional deployed Pages root for byte-for-byte readback"
    )
    parser.add_argument("--minimum-headroom-ratio", type=float, default=0.05)
    parser.add_argument(
        "--expected-data-as-of",
        type=date.fromisoformat,
        help="Require every public artifact to match this official KRX session",
    )
    args = parser.parse_args()
    root = repository_root()
    receipt = (
        verify_remote(root, args.base_url, expected_data_as_of=args.expected_data_as_of)
        if args.base_url
        else verify_local(
            root,
            minimum_headroom_ratio=args.minimum_headroom_ratio,
            expected_data_as_of=args.expected_data_as_of,
        )
    )
    print(json.dumps(receipt, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
