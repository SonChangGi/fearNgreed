from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pandas as pd

from .analysis import (
    align_us_before_krx,
    build_analysis_frame,
    disparity_filtered_signals,
    drawdown,
)
from .backtest import BacktestResult, result_to_public, run_backtest
from .events import event_returns, extreme_entries, non_overlapping, summarize_event_returns
from .model import FlowSignal
from .quality import compare_latest_close

METHODOLOGY_VERSION = "fear-flow-v1"
PROXY_TICKERS = {"226490": "226490.KS", "069500": "069500.KS"}
STOCK_TICKERS = {"000660": "000660.KS", "005930": "005930.KS"}


@dataclass(frozen=True)
class PipelineInputs:
    kospi: pd.DataFrame
    flow: pd.DataFrame
    adjusted: dict[str, pd.DataFrame]
    krx_etfs: dict[str, pd.DataFrame]
    generated_at: datetime
    core_source: str
    degraded_reasons: tuple[str, ...] = ()
    krx_stocks: dict[str, pd.DataFrame] = field(default_factory=dict)


@dataclass(frozen=True)
class PipelineOutputs:
    summary: dict[str, Any]
    dashboard: dict[str, Any]
    history: dict[str, Any]
    automation_status: dict[str, Any]


def build_outputs(inputs: PipelineInputs) -> PipelineOutputs:
    frame, scaled_signals, raw_signals, quality = build_analysis_frame(inputs.kospi, inputs.flow)
    if frame.empty or quality.state == "unavailable":
        raise ValueError(f"core input quality failed: {','.join(quality.issues)}")
    degraded = list(dict.fromkeys([*inputs.degraded_reasons, *quality.issues]))
    crosschecks = _crosschecks(inputs, frame)
    if crosschecks["kospi"]["state"] != "ok":
        degraded.append(f"price_crosscheck_kospi_{crosschecks['kospi']['state']}")
    for ticker, check in crosschecks["etf"].items():
        if check["state"] != "ok":
            degraded.append(f"price_crosscheck_{ticker}_{check['state']}")
    for ticker, check in crosschecks["stock"].items():
        if check["state"] != "ok":
            degraded.append(f"price_crosscheck_{ticker}_{check['state']}")

    backtests: dict[str, dict[str, BacktestResult]] = {}
    disparity_signals = disparity_filtered_signals(scaled_signals, frame)
    for ticker, yahoo_ticker in PROXY_TICKERS.items():
        if crosschecks["etf"][ticker]["state"] != "ok":
            continue
        bars = inputs.adjusted[yahoo_ticker][["open", "close"]]
        variants: dict[str, BacktestResult] = {}
        for cost in (5.0, 10.0, 20.0):
            variants[f"base_{int(cost)}bp"] = run_backtest(
                scaled_signals,
                bars,
                ticker=ticker,
                one_way_cost_bps=cost,
            )
        variants["disparity_10bp"] = run_backtest(
            disparity_signals,
            bars,
            ticker=ticker,
            one_way_cost_bps=10,
        )
        backtests[ticker] = variants

    common_backtests = _common_period_backtests(backtests, inputs.adjusted, scaled_signals)
    all_events = extreme_entries(scaled_signals)
    clean_events = non_overlapping(all_events, horizon=20)
    event_section = _event_section(frame, all_events, clean_events, inputs.adjusted, crosschecks)
    diagnostics = _semiconductor_diagnostics(frame, inputs.adjusted, crosschecks["stock"])
    latest = frame.iloc[-1]
    latest_signal = scaled_signals[-1]
    base_result = backtests.get("226490", {}).get("base_10bp")
    position = "long" if base_result and base_result.open_position else "cash"
    pending_action = base_result.pending_action if base_result else None
    pending_reason = base_result.pending_reason if base_result else None
    status_state = "degraded" if degraded else "ok"
    generated_at = inputs.generated_at.astimezone(UTC).isoformat().replace("+00:00", "Z")
    data_as_of = frame.index[-1].date().isoformat()
    history_rows = _history_rows(frame, base_result, inputs.adjusted)
    summary = _summary(
        frame=frame,
        latest=latest,
        latest_signal=latest_signal,
        generated_at=generated_at,
        data_as_of=data_as_of,
        status_state=status_state,
        degraded=degraded,
        quality_metrics=quality.metrics,
        event_count=len(clean_events),
        trade_count=len(base_result.trades) if base_result else 0,
        position=position,
        pending_action=pending_action,
        pending_reason=pending_reason,
        core_source=inputs.core_source,
    )
    dashboard = {
        "schemaVersion": 1,
        "methodologyVersion": METHODOLOGY_VERSION,
        "generatedAt": generated_at,
        "dataAsOf": data_as_of,
        "status": {"state": status_state, "degradedReasons": degraded},
        "current": summary["primaryEntities"][0],
        "scatter": _scatter(frame),
        "scatterMeta": _scatter_meta(frame),
        "models": {
            "scaled": _model_snapshot(latest, "scaled"),
            "raw": _model_snapshot(latest, "raw"),
        },
        "regression": {
            "alpha": public_number(latest["scaled_alpha"]),
            "beta": public_number(latest["scaled_beta"]),
            "window": 252,
            "trainingCount": int(latest["scaled_training_count"]),
            "scaled": _model_snapshot(latest, "scaled"),
            "raw": _model_snapshot(latest, "raw"),
        },
        "events": event_section,
        "backtests": _public_backtests(backtests, common_backtests),
        "diagnostics": diagnostics,
        "quality": {"state": quality.state, "issues": quality.issues, **quality.metrics},
        "crosschecks": crosschecks,
    }
    history = {
        "schemaVersion": 1,
        "methodologyVersion": METHODOLOGY_VERSION,
        "generatedAt": generated_at,
        "dataAsOf": data_as_of,
        "fixture": False,
        "models": {
            "scaled": _model_snapshot(latest, "scaled"),
            "raw": _model_snapshot(latest, "raw"),
        },
        "series": history_rows,
    }
    automation_status = {
        "schemaVersion": 1,
        "state": status_state,
        "lastAttemptAt": generated_at,
        "lastSuccessAt": generated_at,
        "dataAsOf": data_as_of,
        "degradedReasons": degraded,
        "sourceMode": inputs.core_source,
    }
    return PipelineOutputs(summary, dashboard, history, automation_status)


def _summary(
    *,
    frame: pd.DataFrame,
    latest: pd.Series,
    latest_signal: FlowSignal,
    generated_at: str,
    data_as_of: str,
    status_state: str,
    degraded: list[str],
    quality_metrics: dict[str, Any],
    event_count: int,
    trade_count: int,
    position: str,
    pending_action: str | None,
    pending_reason: str | None,
    core_source: str,
) -> dict[str, Any]:
    state_labels = {
        "extreme_fear": "극단적 공포",
        "fear": "공포",
        "neutral": "중립",
        "greed": "탐욕",
        "extreme_greed": "극단적 탐욕",
        "unavailable": "산출 불가",
    }
    latest_complete = frame[["close", "flow_share"]].dropna()
    return {
        "schemaVersion": 1,
        "contract": "quant-research-summary",
        "projectId": "fearngreed",
        "methodologyVersion": METHODOLOGY_VERSION,
        "generatedAt": generated_at,
        "dataAsOf": data_as_of,
        "status": {
            "state": status_state,
            "label": state_labels.get(latest_signal.state, latest_signal.state),
            "cadence": "weekdays-after-20:30-KST",
            "expectedFreshnessDays": 3,
            "degradedReasons": degraded,
        },
        "coverage": {
            "historyStart": latest_complete.index[0].date().isoformat(),
            "historyEnd": latest_complete.index[-1].date().isoformat(),
            "observationCount": int(len(latest_complete)),
            "eventCount": event_count,
            "tradeCount": trade_count,
            "sourceCompleteness": public_number(quality_metrics.get("sourceCompleteness")),
        },
        "primaryEntities": [
            {
                "id": "KOSPI",
                "name": "KOSPI",
                "region": "Korea",
                "themes": ["Sentiment", "Flow"],
                "signalState": latest_signal.state,
                "sentimentPercentile": public_number(latest_signal.percentile),
                "residualZ": public_number(latest_signal.residual_z),
                "rollingR2": public_number(latest_signal.rolling_r2),
                "return1d": public_number(latest["return_1d"]),
                "flowShare": public_number(latest["flow_share"]),
                "rawFlowTrillion": public_number(latest["raw_flow_trillion"]),
                "disparity50": public_number(latest["disparity50"]),
                "mdd252": public_number(latest["mdd252"]),
                "modelQuality": latest_signal.quality,
                "modelConfidence": latest["model_confidence"],
                "position": position,
                "pendingAction": pending_action,
                "pendingReason": pending_reason,
                "primaryProxy": "226490",
                "sourceMode": core_source,
                "models": {
                    "scaled": _model_snapshot(latest, "scaled"),
                    "raw": _model_snapshot(latest, "raw"),
                },
            }
        ],
        "limitations": [
            "2026년 관측 후 제안된 사후적·탐색적 연구이며 예측력이나 인과관계를 증명하지 않는다.",
            "원문은 회귀창·수급 범위·임계값·전체 사건 수·거래비용을 공개하지 않았다.",
            "ETF 백테스트는 조정가격, 익일 시가 체결, 현금수익률 0%를 가정한다.",
        ],
        "sources": [
            {
                "id": "krx",
                "label": "한국거래소 통계정보",
                "url": "https://openapi.krx.co.kr/",
                "mode": core_source,
            },
            {
                "id": "pykrx",
                "label": "pykrx authenticated KRX adapter",
                "url": "https://github.com/sharebook-kr/pykrx",
            },
            {
                "id": "yfinance",
                "label": "yfinance adjusted-price research source",
                "url": "https://github.com/ranaroussi/yfinance",
            },
        ],
        "automation": {
            "lastAttemptAt": generated_at,
            "lastSuccessAt": generated_at,
            "state": status_state,
        },
        "payload": {
            "dashboardUrl": "./dashboard.json",
            "historyUrl": "./history.json",
            "automationStatusUrl": "./automation-status.json",
        },
    }


def _crosschecks(inputs: PipelineInputs, frame: pd.DataFrame) -> dict[str, Any]:
    kospi_yahoo = inputs.adjusted.get("^KS11")
    kospi_check = (
        compare_latest_close(
            frame["close"], kospi_yahoo["close"], expected_date=frame.index[-1]
        )
        if kospi_yahoo is not None
        else {"state": "unavailable", "date": None, "relativeDifference": None}
    )
    etf_checks: dict[str, Any] = {}
    for ticker, yahoo_ticker in PROXY_TICKERS.items():
        official = inputs.krx_etfs.get(ticker)
        secondary = inputs.adjusted.get(yahoo_ticker)
        etf_checks[ticker] = (
            compare_latest_close(
                official["close"], secondary["close"], expected_date=frame.index[-1]
            )
            if official is not None and secondary is not None
            else {"state": "unavailable", "date": None, "relativeDifference": None}
        )
    stock_checks: dict[str, Any] = {}
    for ticker, yahoo_ticker in STOCK_TICKERS.items():
        official = inputs.krx_stocks.get(ticker)
        secondary = inputs.adjusted.get(yahoo_ticker)
        stock_checks[ticker] = (
            compare_latest_close(
                official["close"], secondary["close"], expected_date=frame.index[-1]
            )
            if official is not None and secondary is not None
            else {"state": "unavailable", "date": None, "relativeDifference": None}
        )
    return {"kospi": kospi_check, "etf": etf_checks, "stock": stock_checks}


def _common_period_backtests(
    backtests: dict[str, dict[str, BacktestResult]],
    adjusted: dict[str, pd.DataFrame],
    signals: list[FlowSignal],
) -> dict[str, BacktestResult]:
    if not all(ticker in backtests for ticker in PROXY_TICKERS):
        return {}
    left = adjusted[PROXY_TICKERS["226490"]]
    right = adjusted[PROXY_TICKERS["069500"]]
    common = left.index.intersection(right.index)
    output: dict[str, BacktestResult] = {}
    for ticker, yahoo_ticker in PROXY_TICKERS.items():
        output[ticker] = run_backtest(
            signals,
            adjusted[yahoo_ticker].loc[common, ["open", "close"]],
            ticker=ticker,
            one_way_cost_bps=10,
        )
    return output


def _event_section(
    frame: pd.DataFrame,
    all_events: list[Any],
    clean_events: list[Any],
    adjusted: dict[str, pd.DataFrame],
    crosschecks: dict[str, Any],
) -> dict[str, Any]:
    assets: dict[str, pd.Series] = {"KOSPI": frame["close"]}
    for ticker, yahoo_ticker in PROXY_TICKERS.items():
        if crosschecks["etf"][ticker]["state"] == "ok":
            assets[ticker] = adjusted[yahoo_ticker]["close"]
    result: dict[str, Any] = {}
    for asset, prices in assets.items():
        full_rows = event_returns(all_events, prices)
        clean_rows = event_returns(clean_events, prices)
        result[asset] = {
            "all": {
                "eventCount": len(full_rows),
                "summary": summarize_event_returns(full_rows),
            },
            "nonOverlapping20d": {
                "eventCount": len(clean_rows),
                "summary": summarize_event_returns(clean_rows),
                "events": clean_rows,
            },
        }
    return result


def _public_backtests(
    backtests: dict[str, dict[str, BacktestResult]], common: dict[str, BacktestResult]
) -> dict[str, Any]:
    result: dict[str, Any] = {"status": "ok" if backtests else "unavailable", "proxies": {}}
    for ticker, variants in backtests.items():
        result["proxies"][ticker] = {
            "fullPeriod": {
                name: _compact_result(value, include_equity=False)
                for name, value in variants.items()
            },
            "commonPeriod": _compact_result(common[ticker], include_equity=True)
            if ticker in common
            else None,
        }
    return result


def _compact_result(result: BacktestResult, *, include_equity: bool) -> dict[str, Any]:
    public = result_to_public(result)
    if include_equity:
        public["equity"] = _sample_with_last(public["equity"], step=5)
    else:
        public.pop("equity", None)
    return public


def _scatter(frame: pd.DataFrame) -> list[dict[str, Any]]:
    complete = frame.dropna(subset=["return_1d", "flow_share"])
    recent = complete.tail(253)
    if recent.empty:
        return []
    current_date = recent.index[-1]
    latest = recent.iloc[-1]
    alpha = latest["scaled_alpha"]
    beta = latest["scaled_beta"]
    rows: list[dict[str, Any]] = []
    for timestamp, row in recent.iterrows():
        if pd.isna(row["return_1d"]) or pd.isna(row["flow_share"]):
            continue
        rows.append(
            {
                "date": timestamp.date().isoformat(),
                "return1d": public_number(row["return_1d"]),
                "flowShare": public_number(row["flow_share"]),
                "predicted": public_number(alpha + beta * row["return_1d"])
                if pd.notna(alpha) and pd.notna(beta)
                else None,
                "percentile": public_number(row["scaled_percentile"]),
                "state": row["scaled_state"],
                "role": "current" if timestamp == current_date else "training",
            }
        )
    return rows


def _scatter_meta(frame: pd.DataFrame) -> dict[str, Any]:
    points = _scatter(frame)
    training_count = sum(point["role"] == "training" for point in points)
    current_count = sum(point["role"] == "current" for point in points)
    return {
        "model": "scaled",
        "window": 252,
        "trainingCount": training_count,
        "currentCount": current_count,
        "pointCount": len(points),
        "roles": {"training": "rolling_window", "current": "out_of_sample_observation"},
    }


def _history_rows(
    frame: pd.DataFrame,
    base_result: BacktestResult | None,
    adjusted: dict[str, pd.DataFrame],
) -> list[dict[str, Any]]:
    exposure = base_result.exposure if base_result is not None else pd.Series(dtype=float)
    rows: list[dict[str, Any]] = []
    for timestamp, row in frame.iterrows():
        output = {
            "date": timestamp.date().isoformat(),
            "kospiClose": public_number(row["close"]),
            "return1d": public_number(row["return_1d"]),
            "flowShare": public_number(row["flow_share"]),
            "rawFlowTrillion": public_number(row["raw_flow_trillion"]),
            "disparity50": public_number(row["disparity50"]),
            "residual": public_number(row["scaled_residual"]),
            "residualZ": public_number(row["scaled_residual_z"]),
            "percentile": public_number(row["scaled_percentile"]),
            "rollingR2": public_number(row["scaled_rolling_r2"]),
            "state": row["scaled_state"],
            "quality": row["scaled_quality"],
            "tradeEligible": bool(row["scaled_trade_eligible"]),
            "sourceHash": row["source_hash"],
            "position": (
                "long" if timestamp in exposure.index and exposure.loc[timestamp] else "cash"
            ),
        }
        for public_ticker, yahoo_ticker in PROXY_TICKERS.items():
            prices = adjusted.get(yahoo_ticker)
            if prices is not None and timestamp in prices.index:
                output[f"p{public_ticker}Open"] = public_number(prices.loc[timestamp, "open"])
                output[f"p{public_ticker}Close"] = public_number(prices.loc[timestamp, "close"])
        rows.append(output)
    return rows


def _semiconductor_diagnostics(
    frame: pd.DataFrame,
    adjusted: dict[str, pd.DataFrame],
    stock_crosschecks: dict[str, Any] | None = None,
) -> dict[str, Any]:
    required = ("MU", "000660.KS", "005930.KS", "KRW=X")
    if not all(ticker in adjusted for ticker in required):
        return {"status": "unavailable", "reason": "optional_prices_missing"}
    crosschecks = stock_crosschecks or {}
    failed_crosschecks = [
        ticker for ticker in STOCK_TICKERS if crosschecks.get(ticker, {}).get("state") != "ok"
    ]
    if failed_crosschecks:
        return {
            "status": "unavailable",
            "reason": "official_stock_crosscheck_failed",
            "failedTickers": failed_crosschecks,
        }
    aligned = align_us_before_krx(frame.index, adjusted["MU"]["close"], adjusted["KRW=X"]["close"])
    aligned["hynix"] = adjusted["000660.KS"]["close"].reindex(frame.index)
    aligned["samsung"] = adjusted["005930.KS"]["close"].reindex(frame.index)
    aligned = aligned.dropna().tail(756)
    if aligned.empty:
        return {"status": "unavailable", "reason": "no_aligned_sessions"}
    for price_field in ("mu_close_krw", "hynix", "samsung"):
        aligned[f"{price_field}_indexed"] = (
            aligned[price_field] / aligned[price_field].iloc[0] * 100
        )
        aligned[f"{price_field}_mdd252"] = drawdown(aligned[price_field])
    aligned["mu_hynix_ratio"] = aligned["mu_close_krw"] / aligned["hynix"]
    aligned["mu_hynix_ratio_indexed"] = (
        aligned["mu_hynix_ratio"] / aligned["mu_hynix_ratio"].iloc[0] * 100
    )
    aligned["mu_hynix_relative_spread"] = (
        aligned["mu_close_krw_indexed"] - aligned["hynix_indexed"]
    )
    sampled = _sample_frame_with_last(aligned, step=5)
    return {
        "status": "ok",
        "alignment": "last_us_and_fx_session_strictly_before_krx_date",
        "latest": {
            "date": aligned.index[-1].date().isoformat(),
            "muMdd252": public_number(aligned["mu_close_krw_mdd252"].iloc[-1], digits=6),
            "hynixMdd252": public_number(aligned["hynix_mdd252"].iloc[-1], digits=6),
            "samsungMdd252": public_number(aligned["samsung_mdd252"].iloc[-1], digits=6),
            "muHynixRatio": public_number(aligned["mu_hynix_ratio"].iloc[-1], digits=6),
            "muHynixRatioIndexed": public_number(
                aligned["mu_hynix_ratio_indexed"].iloc[-1], digits=2
            ),
            "muHynixRelativeSpread": public_number(
                aligned["mu_hynix_relative_spread"].iloc[-1], digits=2
            ),
        },
        "series": [
            {
                "date": timestamp.date().isoformat(),
                "muKrwIndexed": public_number(row["mu_close_krw_indexed"], digits=1),
                "hynixIndexed": public_number(row["hynix_indexed"], digits=1),
                "samsungIndexed": public_number(row["samsung_indexed"], digits=1),
                "muHynixRatio": public_number(row["mu_hynix_ratio"], digits=6),
                "muHynixRatioIndexed": public_number(
                    row["mu_hynix_ratio_indexed"], digits=2
                ),
                "muHynixRelativeSpread": public_number(
                    row["mu_hynix_relative_spread"], digits=2
                ),
            }
            for timestamp, row in sampled.iterrows()
        ],
    }


def _model_snapshot(row: pd.Series, prefix: str) -> dict[str, Any]:
    return {
        "model": prefix,
        "state": row[f"{prefix}_state"],
        "percentile": public_number(row[f"{prefix}_percentile"]),
        "residual": public_number(row[f"{prefix}_residual"]),
        "residualZ": public_number(row[f"{prefix}_residual_z"]),
        "alpha": public_number(row[f"{prefix}_alpha"]),
        "beta": public_number(row[f"{prefix}_beta"]),
        "rollingR2": public_number(row[f"{prefix}_rolling_r2"]),
        "trainingCount": int(row[f"{prefix}_training_count"]),
        "quality": row[f"{prefix}_quality"],
        "tradeEligible": bool(row[f"{prefix}_trade_eligible"]),
    }


def _sample_with_last(rows: list[dict[str, Any]], *, step: int) -> list[dict[str, Any]]:
    if not rows:
        return []
    sampled = rows[::step]
    if sampled[-1] != rows[-1]:
        sampled.append(rows[-1])
    return sampled


def _sample_frame_with_last(frame: pd.DataFrame, *, step: int) -> pd.DataFrame:
    if frame.empty:
        return frame
    positions = list(range(0, len(frame), step))
    if positions[-1] != len(frame) - 1:
        positions.append(len(frame) - 1)
    return frame.iloc[positions]


def public_number(value: Any, digits: int = 10) -> float | None:
    if value is None:
        return None
    number = float(value)
    return round(number, digits) if math.isfinite(number) else None


def output_size_report(outputs: PipelineOutputs) -> dict[str, int]:
    import json

    return {
        name: len(
            json.dumps(getattr(outputs, name), ensure_ascii=False, separators=(",", ":")).encode()
        )
        for name in ("summary", "dashboard", "history", "automation_status")
    }


def write_outputs_atomic(outputs: PipelineOutputs, data_dir: Path) -> None:
    import json
    import os
    import tempfile

    data_dir.mkdir(parents=True, exist_ok=True)
    mapping = {
        "summary.json": outputs.summary,
        "dashboard.json": outputs.dashboard,
        "history.json": outputs.history,
        "automation-status.json": outputs.automation_status,
    }
    for filename, payload in mapping.items():
        encoded = (
            json.dumps(
                payload,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=False,
                allow_nan=False,
            )
            + "\n"
        )
        with tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", dir=data_dir, prefix=f".{filename}.", delete=False
        ) as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
            temporary = Path(handle.name)
        temporary.replace(data_dir / filename)
