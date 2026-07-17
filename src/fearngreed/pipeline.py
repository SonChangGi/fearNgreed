from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pandas as pd

from .analysis import (
    INDIVIDUAL_SCALED,
    OPTIONAL_FLOW_CHANNELS,
    align_us_before_krx,
    build_analysis_frame,
    channel_signals,
    disparity_filtered_signals,
    drawdown,
)
from .backtest import (
    BacktestResult,
    result_to_public,
    run_backtest_safe,
    run_cost_sensitivity,
)
from .events import (
    event_returns,
    extreme_entries,
    non_overlapping,
    summarize_event_returns,
    unconditional_forward_return_benchmarks,
)
from .model import FlowSignal
from .quality import compare_close_anchors, compare_latest_close

METHODOLOGY_VERSION = "fear-flow-v3"
PROXY_TICKERS = {"226490": "226490.KS", "069500": "069500.KS"}
STOCK_TICKERS = {"000660": "000660.KS", "005930": "005930.KS"}
PDF_ANNOTATED_EVENTS = {
    "2026-03-04": ("extreme_fear", "공포"),
    "2026-04-02": ("fear", "공포"),
    "2026-06-08": ("extreme_fear", "공포"),
    "2026-07-08": ("extreme_fear", "공포"),
    "2026-07-13": ("fear", "공포"),
    "2026-07-14": ("fear", "공포"),
    "2026-03-05": ("extreme_greed", "탐욕"),
    "2026-05-07": ("extreme_greed", "탐욕"),
    "2026-05-11": ("extreme_greed", "탐욕"),
    "2026-06-02": ("extreme_greed", "탐욕"),
    "2026-06-09": ("extreme_greed", "탐욕"),
}


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
    kospi_secondary_history_independent: bool = True
    prior_etf_reconciliation: dict[str, dict[str, Any]] = field(default_factory=dict)


@dataclass(frozen=True)
class PipelineOutputs:
    summary: dict[str, Any]
    dashboard: dict[str, Any]
    history: dict[str, Any]
    automation_status: dict[str, Any]
    strategy_comparison: dict[str, Any] = field(default_factory=dict)


def build_outputs(inputs: PipelineInputs) -> PipelineOutputs:
    frame, scaled_signals, raw_signals, quality = build_analysis_frame(inputs.kospi, inputs.flow)
    if frame.empty or quality.state == "unavailable":
        raise ValueError(f"core input quality failed: {','.join(quality.issues)}")
    robust_signals = channel_signals(frame, INDIVIDUAL_SCALED, fit_method="huber")
    _attach_pipeline_signals(frame, robust_signals, "robust")
    model_signals = {
        "robust": robust_signals,
        "scaled": scaled_signals,
        "raw": raw_signals,
    }
    degraded = list(dict.fromkeys([*inputs.degraded_reasons, *quality.issues]))
    signal_cutoff = frame.index[-1]
    adjusted = dict(inputs.adjusted)
    crosschecks = _crosschecks(inputs, frame)
    for ticker, yahoo_ticker in PROXY_TICKERS.items():
        reconciled, reconciliation = _reconcile_adjusted_etf_history(
            inputs.krx_etfs.get(ticker),
            adjusted.get(yahoo_ticker),
            expected_date=signal_cutoff,
        )
        reconciliation = _inherit_reconciliation_provenance(
            reconciliation,
            inputs.prior_etf_reconciliation.get(ticker),
        )
        check = crosschecks["etf"][ticker]
        check["historyReconciliation"] = reconciliation
        if reconciliation["state"] != "ok":
            check["latestPriceState"] = check.get("state", "unavailable")
            check["state"] = "unavailable"
            check["reason"] = "adjusted_history_session_gaps_unresolved"
        elif reconciled is not None:
            adjusted[yahoo_ticker] = reconciled
            if reconciliation["filledCount"]:
                degraded.append(f"adjusted_history_gap_reconciled_{ticker}")
    if crosschecks["kospi"]["state"] != "ok":
        degraded.append(f"price_crosscheck_kospi_{crosschecks['kospi']['state']}")
    for ticker, check in crosschecks["etf"].items():
        if check["state"] != "ok":
            degraded.append(f"price_crosscheck_{ticker}_{check['state']}")
    for ticker, check in crosschecks["stock"].items():
        if check["state"] != "ok":
            degraded.append(f"price_crosscheck_{ticker}_{check['state']}")

    backtests: dict[str, dict[str, BacktestResult]] = {}
    long_short_backtests: dict[str, dict[str, BacktestResult]] = {}
    exit50_backtests: dict[str, BacktestResult] = {}
    disparity_signals = disparity_filtered_signals(robust_signals, frame)
    for ticker, yahoo_ticker in PROXY_TICKERS.items():
        if crosschecks["etf"][ticker]["state"] != "ok":
            continue
        secondary = adjusted.get(yahoo_ticker)
        if secondary is None:
            continue
        bars = _price_frame_as_of(secondary, signal_cutoff)[["open", "close"]]
        variants: dict[str, BacktestResult] = {}
        for model_name, signals in model_signals.items():
            for result in run_cost_sensitivity(signals, bars, ticker=ticker):
                variants[f"{model_name}_{int(result.cost_bps)}bp"] = result
        variants["disparity_10bp"] = run_backtest_safe(
            disparity_signals,
            bars,
            ticker=ticker,
            one_way_cost_bps=10,
        )
        backtests[ticker] = variants
        long_short_backtests[ticker] = {
            f"robust_{int(result.cost_bps)}bp": result
            for result in run_cost_sensitivity(
                robust_signals,
                bars,
                ticker=ticker,
                policy_id="long_short_cash",
            )
        }
        exit50_backtests[ticker] = run_backtest_safe(
            robust_signals,
            bars,
            ticker=ticker,
            one_way_cost_bps=10,
            long_exit_percentile=50,
        )

    common_backtests = _common_period_backtests(
        adjusted, model_signals, disparity_signals, crosschecks
    )
    common_long_short_backtests = _common_period_backtests(
        adjusted,
        {"robust": robust_signals},
        None,
        crosschecks,
        policy_id="long_short_cash",
    )
    common_exit50_backtests = _common_exit_threshold_backtests(
        adjusted,
        robust_signals,
        crosschecks,
        long_exit_percentile=50,
    )
    events_by_model = {
        model_name: _event_section_for_signals(frame, signals, adjusted, crosschecks)
        for model_name, signals in model_signals.items()
    }
    primary_all_events = extreme_entries(robust_signals)
    primary_clean_events = non_overlapping(primary_all_events, horizon=20)
    diagnostics = _semiconductor_diagnostics(frame, adjusted, crosschecks["stock"])
    latest = frame.iloc[-1]
    latest_signal = robust_signals[-1]
    base_result = backtests.get("226490", {}).get("robust_10bp")
    if base_result is None or base_result.status != "ok":
        position = "unavailable"
        position_quality = "unavailable"
        position_unavailable_reason = (
            base_result.unavailable_reason if base_result is not None else "proxy_validation_failed"
        )
        pending_action = None
        pending_reason = None
    else:
        position = "long" if base_result.open_position else "cash"
        position_quality = "ok"
        position_unavailable_reason = None
        pending_action = base_result.pending_action
        pending_reason = base_result.pending_reason
    status_state = "degraded" if degraded else "ok"
    generated_at = inputs.generated_at.astimezone(UTC).isoformat().replace("+00:00", "Z")
    data_as_of = frame.index[-1].date().isoformat()
    history_rows = _history_rows(frame, base_result, adjusted, disparity_signals)
    primary_reconciliation = crosschecks["etf"]["226490"].get("historyReconciliation", {})
    adjusted_proxy_source = (
        "yfinance_adjusted_plus_scaled_krx_gap_rows"
        if primary_reconciliation.get("filledCount", 0) > 0
        else "yfinance_adjusted"
    )
    summary = _summary(
        frame=frame,
        latest=latest,
        latest_signal=latest_signal,
        generated_at=generated_at,
        data_as_of=data_as_of,
        status_state=status_state,
        degraded=degraded,
        quality_metrics=quality.metrics,
        event_count=len(primary_clean_events),
        trade_count=len(base_result.trades) if base_result and base_result.status == "ok" else 0,
        position=position,
        position_quality=position_quality,
        position_unavailable_reason=position_unavailable_reason,
        pending_action=pending_action,
        pending_reason=pending_reason,
        core_source=inputs.core_source,
        adjusted_proxy_source=adjusted_proxy_source,
    )
    dashboard = {
        "schemaVersion": 1,
        "methodologyVersion": METHODOLOGY_VERSION,
        "generatedAt": generated_at,
        "dataAsOf": data_as_of,
        "status": {
            "state": status_state,
            "label": _operational_label(status_state),
            "degradedReasons": degraded,
        },
        "current": summary["primaryEntities"][0],
        "scatterByModel": {model_name: _scatter(frame, model_name) for model_name in model_signals},
        "scatterMetaByModel": {
            model_name: _scatter_meta(frame, model_name) for model_name in model_signals
        },
        "models": {model_name: _model_snapshot(latest, model_name) for model_name in model_signals},
        "regression": {
            "alpha": public_number(latest["robust_alpha"]),
            "beta": public_number(latest["robust_beta"]),
            "window": 252,
            "trainingCount": int(latest["robust_training_count"]),
            "primaryModel": "robust",
            **{model_name: _model_snapshot(latest, model_name) for model_name in model_signals},
        },
        "eventsByModel": events_by_model,
        "backtests": _public_backtests(backtests, common_backtests),
        "diagnostics": diagnostics,
        "pdfReplica": _pdf_replica(frame),
        "flowChannels": _flow_channel_section(frame),
        "quality": {"state": quality.state, "issues": quality.issues, **quality.metrics},
        "crosschecks": crosschecks,
    }
    history_columns, history_values = _columnar_history(history_rows)
    history = {
        "schemaVersion": 1,
        "methodologyVersion": METHODOLOGY_VERSION,
        "generatedAt": generated_at,
        "dataAsOf": data_as_of,
        "fixture": False,
        "models": {model_name: _model_snapshot(latest, model_name) for model_name in model_signals},
        "flowChannelRoles": _history_flow_channel_roles(),
        "strategyScenario": {
            "engineVersion": "signed-fixed-quantity-v1",
            "defaultLongExitPercentile": 80,
            "customLongExitMinimum": 50,
            "customLongExitMaximum": 94,
            "customLongExitStep": 1,
            "shortExitFormula": "100-longExitPercentile",
            "signalInputsAreServerPublished": True,
            "browserMayRefitRegression": False,
        },
        "seriesEncoding": "columnar-v1",
        "seriesColumns": history_columns,
        "seriesRows": history_values,
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
    strategy_comparison = _strategy_comparison(
        generated_at=generated_at,
        data_as_of=data_as_of,
        status_state=status_state,
        degraded=degraded,
        backtests=long_short_backtests,
        common=common_long_short_backtests,
        exit50=exit50_backtests,
        common_exit50=common_exit50_backtests,
        long_cash=backtests,
        common_long_cash=common_backtests,
    )
    return PipelineOutputs(
        summary=summary,
        dashboard=dashboard,
        history=history,
        automation_status=automation_status,
        strategy_comparison=strategy_comparison,
    )


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
    position_quality: str,
    position_unavailable_reason: str | None,
    pending_action: str | None,
    pending_reason: str | None,
    core_source: str,
    adjusted_proxy_source: str,
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
            "label": _operational_label(status_state),
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
            "dateOverlapRatio": public_number(
                quality_metrics.get("dateOverlapRatio", quality_metrics.get("sourceCompleteness"))
            ),
            "sourceCompleteness": public_number(
                quality_metrics.get("dateOverlapRatio", quality_metrics.get("sourceCompleteness"))
            ),
        },
        "primaryEntities": [
            {
                "id": "KOSPI",
                "name": "KOSPI",
                "region": "Korea",
                "themes": ["Sentiment", "Flow"],
                "signalState": latest_signal.state,
                "signalLabel": state_labels.get(latest_signal.state, latest_signal.state),
                "sentimentPercentile": public_number(latest_signal.percentile),
                "residualZ": public_number(latest_signal.residual_z),
                "rollingR2": public_number(latest_signal.rolling_r2),
                "return1d": public_number(latest["return_1d"]),
                "flowShare": public_number(latest["flow_share"]),
                "rawFlowTrillion": public_number(latest["raw_flow_trillion"]),
                "disparity50": public_number(latest["disparity50"]),
                "mdd252": public_number(latest["mdd252"]),
                "modelQuality": latest_signal.quality,
                "modelConfidence": _primary_model_confidence(latest),
                "position": position,
                "positionQuality": position_quality,
                "positionUnavailableReason": position_unavailable_reason,
                "pendingAction": pending_action,
                "pendingReason": pending_reason,
                "primaryProxy": "226490",
                "sourceMode": core_source,
                "strategyModel": "robust_huber_scaled_exit80",
                "fieldSources": {
                    "kospi": core_source,
                    "retailFlow": "authenticated_pykrx",
                    "adjustedProxy": adjusted_proxy_source,
                },
                "models": {
                    "robust": _model_snapshot(latest, "robust"),
                    "scaled": _model_snapshot(latest, "scaled"),
                    "raw": _model_snapshot(latest, "raw"),
                },
            }
        ],
        "limitations": [
            "2026년 관측 후 제안된 사후적·탐색적 연구이며 예측력이나 인과관계를 증명하지 않는다.",
            "원문은 회귀창·수급 범위·임계값·전체 사건 수·거래비용을 공개하지 않았다.",
            (
                "기본 롱/현금 백테스트는 조정가격, 익일 시가 체결, 백분위 80 청산, "
                "현금수익률 0%를 가정한다."
            ),
            (
                "합성 롱/숏 비교는 조정 총수익 가격의 배당 경제효과를 반영하지만 "
                "대차료·리콜·증거금·강제청산·공매도 가능 수량을 모델링하지 않는다."
            ),
            (
                "웹 사용자 청산값은 공개된 신호와 가격만으로 브라우저에서 재계산하는 "
                "탐색 시나리오이며 서버 검증 기본 결과와 구분한다."
            ),
            "강건 회귀는 이상점 영향을 줄이기 위한 사전 정의 후보이며 수익 개선을 보장하지 않는다.",
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
            "strategyComparisonUrl": "./strategy-comparison.json",
        },
    }


def _crosschecks(inputs: PipelineInputs, frame: pd.DataFrame) -> dict[str, Any]:
    kospi_yahoo = inputs.adjusted.get("^KS11")
    kospi_check = (
        _combined_price_crosscheck(
            frame["close"], kospi_yahoo["close"], expected_date=frame.index[-1]
        )
        if kospi_yahoo is not None
        else {"state": "unavailable", "date": None, "relativeDifference": None}
    )
    if kospi_yahoo is not None and not inputs.kospi_secondary_history_independent:
        kospi_check["historicalAnchors"] = {
            "state": "unavailable",
            "reason": "secondary_history_reconstructed",
            "expectedDate": frame.index[-1].date().isoformat(),
            "checkedCount": 0,
            "commonCount": 0,
            "mismatchCount": 0,
            "maxRelativeDifference": None,
            "tolerance": 0.005,
            "anchors": [],
        }
        kospi_check["historicalAnchorBasis"] = (
            "latest_independent_historical_secondary_reconstructed"
        )
    etf_checks: dict[str, Any] = {}
    for ticker, yahoo_ticker in PROXY_TICKERS.items():
        official = inputs.krx_etfs.get(ticker)
        secondary = inputs.adjusted.get(yahoo_ticker)
        etf_checks[ticker] = (
            _combined_price_crosscheck(
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
            _combined_price_crosscheck(
                official["close"], secondary["close"], expected_date=frame.index[-1]
            )
            if official is not None and secondary is not None
            else {"state": "unavailable", "date": None, "relativeDifference": None}
        )
    return {"kospi": kospi_check, "etf": etf_checks, "stock": stock_checks}


def _combined_price_crosscheck(
    primary: pd.Series, secondary: pd.Series, *, expected_date: pd.Timestamp
) -> dict[str, Any]:
    cutoff = pd.Timestamp(expected_date)
    if cutoff.tzinfo is not None:
        cutoff = cutoff.tz_convert(None)
    cutoff = cutoff.normalize()

    def as_of(series: pd.Series) -> pd.Series:
        values = series.copy()
        index = pd.DatetimeIndex(pd.to_datetime(values.index))
        if index.tz is not None:
            index = index.tz_convert(None)
        values.index = index.normalize()
        return values.loc[values.index <= cutoff].sort_index()

    primary_as_of = as_of(primary)
    secondary_as_of = as_of(secondary)
    latest = compare_latest_close(primary_as_of, secondary_as_of, expected_date=cutoff)
    anchors = compare_close_anchors(
        primary_as_of,
        secondary_as_of,
        expected_date=cutoff,
        anchor_count=6,
        minimum_anchor_count=3,
    )
    return {
        **latest,
        "historicalAnchors": anchors,
        "historicalAnchorBasis": "official_unadjusted_vs_research_adjusted_diagnostic",
        "historicalAnchorGatesPublication": False,
    }


def _reconcile_adjusted_etf_history(
    official: pd.DataFrame | None,
    research: pd.DataFrame | None,
    *,
    expected_date: pd.Timestamp,
    factor_tolerance: float = 0.005,
) -> tuple[pd.DataFrame | None, dict[str, Any]]:
    """Validate Yahoo sessions against KRX and cautiously repair isolated gaps.

    A missing adjusted-price row is filled only when the nearest adjusted/raw
    close factor on both sides of the gap agrees within ``factor_tolerance``.
    This preserves the adjusted scale without silently treating raw KRX prices
    as adjusted observations. Any unresolved session makes that proxy's event
    study and backtest unavailable.
    """

    report: dict[str, Any] = {
        "state": "unavailable",
        "source": "yfinance_adjusted_checked_against_krx_calendar",
        "method": "adjacent_close_factor_then_scaled_krx_ohlc",
        "factorTolerance": factor_tolerance,
        "officialSessionCount": 0,
        "researchSessionCount": 0,
        "missingCount": 0,
        "filledCount": 0,
        "unresolvedCount": 0,
        "extraCount": 0,
    }
    if official is None or research is None or official.empty or research.empty:
        report["reason"] = "official_or_adjusted_history_missing"
        return research, report
    if not {"open", "close"}.issubset(official.columns) or not {
        "open",
        "close",
    }.issubset(research.columns):
        report["reason"] = "required_ohlc_columns_missing"
        return research, report

    cutoff = pd.Timestamp(expected_date)
    if cutoff.tzinfo is not None:
        cutoff = cutoff.tz_convert(None)
    cutoff = cutoff.normalize()
    official_as_of = _price_frame_as_of(official, cutoff)
    research_as_of = _price_frame_as_of(research, cutoff)
    if official_as_of.index.duplicated().any() or research_as_of.index.duplicated().any():
        report["reason"] = "duplicate_sessions"
        return research_as_of, report
    if official_as_of.empty or research_as_of.empty:
        report["reason"] = "history_empty_as_of_cutoff"
        return research_as_of, report

    official_prices = official_as_of[["open", "close"]].apply(pd.to_numeric, errors="coerce")
    research_prices = research_as_of[["open", "close"]].apply(pd.to_numeric, errors="coerce")
    official_as_of = official_as_of.loc[
        official_prices.notna().all(axis=1) & (official_prices > 0).all(axis=1)
    ]
    research_as_of = research_as_of.loc[
        research_prices.notna().all(axis=1) & (research_prices > 0).all(axis=1)
    ]
    if official_as_of.empty or research_as_of.empty:
        report["reason"] = "no_positive_ohlc_sessions"
        return research_as_of, report

    report["officialSessionCount"] = int(len(official_as_of))
    report["researchSessionCount"] = int(len(research_as_of))
    if official_as_of.index[0] > research_as_of.index[0]:
        report["reason"] = "official_history_coverage_incomplete"
        report["extraCount"] = int(len(research_as_of.index.difference(official_as_of.index)))
        return research_as_of, report

    extra = research_as_of.index.difference(official_as_of.index)
    research_on_calendar = research_as_of.loc[
        research_as_of.index.intersection(official_as_of.index)
    ].copy()
    missing = official_as_of.index.difference(research_on_calendar.index)
    common = official_as_of.index.intersection(research_on_calendar.index)
    report["extraCount"] = int(len(extra))
    report["missingCount"] = int(len(missing))
    if common.empty:
        report["reason"] = "no_common_sessions"
        report["unresolvedCount"] = int(len(missing))
        return research_on_calendar, report

    filled_rows: list[pd.Series] = []
    filled_dates: list[pd.Timestamp] = []
    unresolved = 0
    for timestamp in missing.sort_values():
        before = common[common < timestamp]
        after = common[common > timestamp]
        if before.empty or after.empty:
            unresolved += 1
            continue
        left = before[-1]
        right = after[0]
        left_raw = float(official_as_of.at[left, "close"])
        right_raw = float(official_as_of.at[right, "close"])
        left_adjusted = float(research_on_calendar.at[left, "close"])
        right_adjusted = float(research_on_calendar.at[right, "close"])
        if min(left_raw, right_raw, left_adjusted, right_adjusted) <= 0:
            unresolved += 1
            continue
        left_factor = left_adjusted / left_raw
        right_factor = right_adjusted / right_raw
        factor_difference = abs(left_factor - right_factor) / max(
            abs(left_factor), abs(right_factor)
        )
        if not math.isfinite(factor_difference) or factor_difference > factor_tolerance:
            unresolved += 1
            continue
        factor = (left_factor + right_factor) / 2
        # Build the synthetic row on the research frame's numeric schema. KRX
        # raw OHLC commonly arrives as unsigned integers; assigning adjusted
        # floats into that Series emits a pandas dtype warning and will become
        # an error in a future pandas release.
        scaled = (
            official_as_of.loc[timestamp].reindex(research_on_calendar.columns).astype(float).copy()
        )
        for column in ("open", "high", "low", "close"):
            if column in scaled.index:
                scaled[column] = float(scaled[column]) * factor
        scaled.name = timestamp
        filled_rows.append(scaled)
        filled_dates.append(timestamp)

    report["filledCount"] = len(filled_rows)
    report["unresolvedCount"] = unresolved
    if unresolved:
        report["reason"] = "adjacent_adjustment_factor_disagreement"
        return research_on_calendar, report

    if filled_rows:
        fills = pd.DataFrame(filled_rows)
        reconciled = pd.concat([research_on_calendar, fills], axis=0).sort_index()
        report["source"] = "yfinance_adjusted_plus_scaled_krx_gap_rows"
        report["filledDateSample"] = [
            timestamp.date().isoformat() for timestamp in filled_dates[:3]
        ]
    else:
        reconciled = research_on_calendar.sort_index()
    if not official_as_of.index.equals(reconciled.index):
        report["reason"] = "session_calendar_not_fully_reconciled"
        report["unresolvedCount"] = int(len(official_as_of.index.difference(reconciled.index)))
        return reconciled, report
    report["state"] = "ok"
    return reconciled, report


def _inherit_reconciliation_provenance(
    current: dict[str, Any], prior: dict[str, Any] | None
) -> dict[str, Any]:
    """Carry validated historical gap repairs into later incremental reports."""
    if not isinstance(prior, dict) or prior.get("state") != "ok" or current.get("state") != "ok":
        return current
    prior_filled = int(prior.get("filledCount") or 0)
    if prior_filled <= 0:
        return current
    current_filled = int(current.get("filledCount") or 0)
    result = dict(current)
    result["source"] = "yfinance_adjusted_plus_scaled_krx_gap_rows"
    result["filledCount"] = prior_filled + current_filled
    result["inheritedFilledCount"] = prior_filled
    result["currentRunFilledCount"] = current_filled
    result["provenance"] = "published_history_plus_current_reconciliation"
    samples = [
        value
        for value in [
            *(prior.get("filledDateSample") or []),
            *(current.get("filledDateSample") or []),
        ]
        if isinstance(value, str)
    ]
    if samples:
        result["filledDateSample"] = list(dict.fromkeys(samples))[:3]
    return result


def _common_period_backtests(
    adjusted: dict[str, pd.DataFrame],
    model_signals: dict[str, list[FlowSignal]],
    disparity_signals: list[FlowSignal] | None,
    crosschecks: dict[str, Any],
    *,
    policy_id: str = "long_cash",
) -> dict[str, dict[str, BacktestResult]]:
    if not all(
        PROXY_TICKERS[ticker] in adjusted
        and crosschecks["etf"].get(ticker, {}).get("state") == "ok"
        for ticker in PROXY_TICKERS
    ):
        return {}
    left = adjusted[PROXY_TICKERS["226490"]]
    right = adjusted[PROXY_TICKERS["069500"]]
    common = left.index.intersection(right.index)
    signal_cutoff = max(
        pd.Timestamp(signal.date) for signals in model_signals.values() for signal in signals
    )
    common = common[common <= signal_cutoff]
    output: dict[str, dict[str, BacktestResult]] = {}
    for ticker, yahoo_ticker in PROXY_TICKERS.items():
        bars = adjusted[yahoo_ticker].loc[common, ["open", "close"]]
        variants: dict[str, BacktestResult] = {}
        for model_name, signals in model_signals.items():
            for result in run_cost_sensitivity(
                signals,
                bars,
                ticker=ticker,
                policy_id=policy_id,
            ):
                variants[f"{model_name}_{int(result.cost_bps)}bp"] = result
        if disparity_signals is not None and policy_id == "long_cash":
            variants["disparity_10bp"] = run_backtest_safe(
                disparity_signals, bars, ticker=ticker, one_way_cost_bps=10
            )
        output[ticker] = variants
    return output


def _common_exit_threshold_backtests(
    adjusted: dict[str, pd.DataFrame],
    signals: list[FlowSignal],
    crosschecks: dict[str, Any],
    *,
    long_exit_percentile: float,
) -> dict[str, BacktestResult]:
    if not all(
        PROXY_TICKERS[ticker] in adjusted
        and crosschecks["etf"].get(ticker, {}).get("state") == "ok"
        for ticker in PROXY_TICKERS
    ):
        return {}
    common = adjusted[PROXY_TICKERS["226490"]].index.intersection(
        adjusted[PROXY_TICKERS["069500"]].index
    )
    signal_cutoff = max(pd.Timestamp(signal.date) for signal in signals)
    common = common[common <= signal_cutoff]
    return {
        ticker: run_backtest_safe(
            signals,
            adjusted[yahoo_ticker].loc[common, ["open", "close"]],
            ticker=ticker,
            one_way_cost_bps=10,
            long_exit_percentile=long_exit_percentile,
        )
        for ticker, yahoo_ticker in PROXY_TICKERS.items()
    }


def _price_frame_as_of(frame: pd.DataFrame, cutoff: pd.Timestamp) -> pd.DataFrame:
    values = frame.copy()
    index = pd.DatetimeIndex(pd.to_datetime(values.index))
    if index.tz is not None:
        index = index.tz_convert(None)
    values.index = index.normalize()
    normalized_cutoff = pd.Timestamp(cutoff)
    if normalized_cutoff.tzinfo is not None:
        normalized_cutoff = normalized_cutoff.tz_convert(None)
    return values.loc[values.index <= normalized_cutoff.normalize()].sort_index()


def _event_section_for_signals(
    frame: pd.DataFrame,
    signals: list[FlowSignal],
    adjusted: dict[str, pd.DataFrame],
    crosschecks: dict[str, Any],
) -> dict[str, Any]:
    all_events = extreme_entries(signals)
    clean_events = non_overlapping(all_events, horizon=20)
    return _event_section(frame, all_events, clean_events, adjusted, crosschecks)


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
        benchmark = unconditional_forward_return_benchmarks(prices)
        full_summary, full_treatment = _public_event_summary(
            summarize_event_returns(
                full_rows,
                benchmark_returns=benchmark,
                bootstrap_method="moving_block",
            )
        )
        clean_summary, clean_treatment = _public_event_summary(
            summarize_event_returns(
                clean_rows,
                benchmark_returns=benchmark,
                bootstrap_method="moving_block",
            )
        )
        result[asset] = {
            "all": {
                "eventCount": len(full_rows),
                "summary": full_summary,
                "meanExcessReturnCi95BenchmarkTreatment": full_treatment,
            },
            "nonOverlapping20d": {
                "eventCount": len(clean_rows),
                "summary": clean_summary,
                "meanExcessReturnCi95BenchmarkTreatment": clean_treatment,
                "events": clean_rows[-12:],
                "eventHistoryTruncated": len(clean_rows) > 12,
            },
            "benchmark": {
                "type": "all_session_mean_forward_return",
                "values": {f"return{horizon}d": value for horizon, value in benchmark.items()},
            },
        }
    return result


def _public_event_summary(
    rows: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], str]:
    """Promote the repeated excess-CI treatment to the event section.

    The pipeline always supplies one unconditional benchmark policy for a
    section. Keeping the policy once avoids repeating a long contract key in
    every state/horizon row while preserving a machine-readable disclosure.
    """

    field = "meanExcessReturnCi95BenchmarkTreatment"
    treatments = {str(row.get(field, "unavailable")) for row in rows}
    informative = treatments - {"unavailable"}
    if len(informative) > 1:
        raise ValueError("event summary mixes excess-CI benchmark treatments")
    public_rows: list[dict[str, Any]] = []
    for row in rows:
        public_row = dict(row)
        public_row.pop(field, None)
        public_rows.append(public_row)
    return public_rows, next(iter(informative), "unavailable")


def _public_backtests(
    backtests: dict[str, dict[str, BacktestResult]],
    common: dict[str, dict[str, BacktestResult]],
) -> dict[str, Any]:
    result: dict[str, Any] = {"status": "ok" if backtests else "unavailable", "proxies": {}}
    for ticker, variants in backtests.items():
        common_variants = common.get(ticker, {})
        benchmark_source = common_variants.get("robust_10bp")
        if benchmark_source is None and common_variants:
            benchmark_source = next(iter(common_variants.values()))
        result["proxies"][ticker] = {
            "fullPeriod": {
                name: _compact_result(
                    value,
                    include_equity=True,
                    include_trades=False,
                    equity_step=252,
                    include_benchmark=True,
                    include_execution_audit=(ticker == "226490" and name == "robust_10bp"),
                    include_actions=(ticker == "226490" and name == "robust_10bp"),
                )
                for name, value in variants.items()
            },
            "commonPeriod": {
                name: _compact_result(
                    value,
                    include_equity=True,
                    include_trades=name.endswith("10bp"),
                    equity_step=84,
                    include_execution_audit=(ticker == "226490" and name == "robust_10bp"),
                    include_actions=(ticker == "226490" and name == "robust_10bp"),
                )
                for name, value in common_variants.items()
            }
            or None,
            "commonBenchmarkEquity": _benchmark_equity(benchmark_source),
            "costBreakEvenBps": {
                model: _cost_break_even_bps(variants, model)
                for model in ("robust", "scaled", "raw")
            },
        }
    return result


def _strategy_comparison(
    *,
    generated_at: str,
    data_as_of: str,
    status_state: str,
    degraded: list[str],
    backtests: dict[str, dict[str, BacktestResult]],
    common: dict[str, dict[str, BacktestResult]],
    exit50: dict[str, BacktestResult],
    common_exit50: dict[str, BacktestResult],
    long_cash: dict[str, dict[str, BacktestResult]],
    common_long_cash: dict[str, dict[str, BacktestResult]],
) -> dict[str, Any]:
    proxies: dict[str, Any] = {}
    sensitivity: dict[str, Any] = {}
    for ticker, variants in backtests.items():
        common_variants = common.get(ticker, {})
        proxies[ticker] = {
            "fullPeriod": {
                name: _compact_result(
                    value,
                    include_equity=True,
                    include_trades=name == "robust_10bp",
                    equity_step=252,
                    include_execution_audit=(ticker == "226490" and name == "robust_10bp"),
                    include_actions=(ticker == "226490" and name == "robust_10bp"),
                )
                for name, value in variants.items()
            },
            "commonPeriod": {
                name: _compact_result(
                    value,
                    include_equity=True,
                    include_trades=name == "robust_10bp",
                    equity_step=84,
                    include_execution_audit=(ticker == "226490" and name == "robust_10bp"),
                    include_actions=(ticker == "226490" and name == "robust_10bp"),
                )
                for name, value in common_variants.items()
            }
            or None,
            "costBreakEvenBps": {"robust": _cost_break_even_bps(variants, "robust")},
        }
        exit80_full = long_cash.get(ticker, {}).get("robust_10bp")
        exit80_common = common_long_cash.get(ticker, {}).get("robust_10bp")
        sensitivity[ticker] = {
            "fullPeriod": {
                "exit50": _metrics_only(exit50.get(ticker)),
                "exit80": _metrics_only(exit80_full),
            },
            "commonPeriod": {
                "exit50": _metrics_only(common_exit50.get(ticker)),
                "exit80": _metrics_only(exit80_common),
            },
        }
    return {
        "schemaVersion": 1,
        "contract": "fearngreed-strategy-comparison",
        "methodologyVersion": METHODOLOGY_VERSION,
        "generatedAt": generated_at,
        "dataAsOf": data_as_of,
        "status": {
            "state": status_state,
            "degradedReasons": degraded,
        },
        "policyDefinitions": {
            "longCash": {
                "policyId": "long_cash",
                "role": "primary_research",
                "longEntry": "first_trade_eligible_extreme_fear_at_or_below_5",
                "longExit": "next_open_after_percentile_at_or_above_80_or_max_20_sessions",
                "shortEntry": None,
                "shortExit": None,
            },
            "longShortCash": {
                "policyId": "long_short_cash",
                "role": "exploratory_synthetic_comparison",
                "longEntry": "first_trade_eligible_extreme_fear_at_or_below_5",
                "longExit": "next_open_after_percentile_at_or_above_80_or_max_20_sessions",
                "shortEntry": "first_trade_eligible_extreme_greed_at_or_above_95",
                "shortExit": "next_open_after_percentile_at_or_below_20_or_max_20_sessions",
                "sameOpenReversal": True,
                "reversalTransactionSides": 2,
                "shortAccounting": (
                    "post_entry_cost_available_equity_1x_fixed_quantity_no_rebalance"
                ),
                "adjustedPriceTreatment": (
                    "split_and_distribution_adjusted_total_return_economics"
                ),
                "borrowFeeAnnualPct": 0,
                "cashAndCollateralReturnPct": 0,
                "shortabilityModeled": False,
                "excludedExecutionConstraints": [
                    "borrow_availability",
                    "borrow_fee",
                    "recall",
                    "margin",
                    "forced_liquidation",
                    "short_sale_order_rules",
                    "market_impact",
                ],
            },
        },
        "dynamicExitControl": {
            "defaultLongExitPercentile": 80,
            "minimum": 50,
            "maximum": 94,
            "step": 1,
            "shortExitFormula": "100-longExitPercentile",
            "calculationLocation": "browser_on_server_published_signals_and_prices",
            "regressionRefit": False,
        },
        "exitThresholdSensitivity": {
            "policyId": "long_cash",
            "model": "robust",
            "oneWayCostBps": 10,
            "selectionUse": "diagnostic_only_not_threshold_optimization",
            "proxies": sensitivity,
        },
        "proxies": proxies,
    }


def _metrics_only(result: BacktestResult | None) -> dict[str, Any] | None:
    if result is None:
        return None
    return _compact_result(result, include_equity=False, include_trades=False)


def _compact_result(
    result: BacktestResult,
    *,
    include_equity: bool,
    include_trades: bool = True,
    equity_step: int = 60,
    include_benchmark: bool = False,
    include_execution_audit: bool = False,
    include_actions: bool = False,
    action_limit: int = 4,
) -> dict[str, Any]:
    if include_actions and not include_execution_audit:
        raise ValueError("actions require the execution-audit contract")
    if action_limit <= 0:
        raise ValueError("action_limit must be positive")
    public = result_to_public(result)
    if include_equity:
        public["equity"] = [
            {
                "date": row["date"],
                "value": row["value"],
                "drawdown": row["drawdown"],
                **(
                    {
                        "buyHoldValue": row["buyHoldValue"],
                        "buyHoldDrawdown": row["buyHoldDrawdown"],
                    }
                    if include_benchmark
                    else {}
                ),
            }
            for row in _sample_with_last(public["equity"], step=equity_step)
        ]
    else:
        public.pop("equity", None)
    trade_count = len(public.get("trades", []))
    public["trades"] = public.get("trades", [])[-12:] if include_trades else []
    if not include_execution_audit:
        public.pop("pendingSignalDate", None)
        if isinstance(public.get("openTrade"), dict):
            public["openTrade"].pop("entrySignalDate", None)
            public["openTrade"].pop("entryReason", None)
        for trade in public["trades"]:
            for field in (
                "entry_signal_date",
                "entry_reason",
                "exit_signal_date",
                "exit_reason",
            ):
                trade.pop(field, None)
    public["tradeHistoryTruncated"] = trade_count > len(public["trades"])
    action_count = len(public.get("actions", []))
    if include_actions:
        public["actions"] = [
            {
                field: action[field]
                for field in (
                    "actionId",
                    "signalDate",
                    "executionDate",
                    "signalPhase",
                    "executionPhase",
                    "type",
                    "fromPosition",
                    "toPosition",
                    "reason",
                )
            }
            for action in public.get("actions", [])[-action_limit:]
        ]
        public["actionHistoryTruncated"] = action_count > len(public["actions"])
    else:
        public.pop("actions", None)
    return public


def _benchmark_equity(result: BacktestResult | None) -> list[dict[str, Any]]:
    if result is None or result.status != "ok":
        return []
    public = result_to_public(result)
    return [
        {
            "date": row["date"],
            "value": row["buyHoldValue"],
            "drawdown": row["buyHoldDrawdown"],
        }
        for row in _sample_with_last(public["equity"], step=60)
    ]


def _scatter(frame: pd.DataFrame, model: str = "scaled") -> list[dict[str, Any]]:
    value_column = "raw_flow_trillion" if model == "raw" else "flow_share"
    complete = frame.dropna(subset=["return_1d", value_column])
    recent = complete.tail(253)
    if recent.empty:
        return []
    current_date = recent.index[-1]
    rows: list[dict[str, Any]] = []
    for timestamp, row in recent.iterrows():
        if pd.isna(row["return_1d"]) or pd.isna(row[value_column]):
            continue
        point = {
            "date": timestamp.date().isoformat(),
            "return1d": public_number(row["return_1d"]),
            "state": row[f"{model}_state"],
            "role": "current" if timestamp == current_date else "training",
        }
        point["rawFlowTrillion" if model == "raw" else "flowShare"] = public_number(
            row[value_column]
        )
        rows.append(point)
    return rows


def _scatter_meta(frame: pd.DataFrame, model: str = "scaled") -> dict[str, Any]:
    points = _scatter(frame, model)
    training_count = sum(point["role"] == "training" for point in points)
    current_count = sum(point["role"] == "current" for point in points)
    return {
        "model": model,
        "unit": "krw_trillion" if model == "raw" else "market_turnover_share",
        "window": 252,
        "trainingCount": training_count,
        "currentCount": current_count,
        "pointCount": len(points),
        "roles": {"training": "rolling_window", "current": "out_of_sample_observation"},
        "stateBoundaries": _scatter_state_boundaries(frame, model),
    }


def _scatter_state_boundaries(frame: pd.DataFrame, model: str) -> dict[str, Any] | None:
    """Publish the current fit's empirical residual transition lines.

    ``fit_latest_signal`` classifies the current residual with the empirical CDF
    ``count(training_residual <= current_residual) / n``.  The four order
    statistics below are therefore the first residual values that enter the
    next state under the exact inclusive/exclusive rules in
    ``classify_percentile``.  A scatter plot can add each residual offset to the
    current regression line without re-fitting or inventing visual thresholds.
    """

    value_column = "raw_flow_trillion" if model == "raw" else "flow_share"
    complete = frame.dropna(subset=["return_1d", value_column]).tail(253)
    if len(complete) < 2:
        return None
    latest = complete.iloc[-1]
    alpha = latest.get(f"{model}_alpha")
    beta = latest.get(f"{model}_beta")
    percentile = latest.get(f"{model}_percentile")
    state = latest.get(f"{model}_state")
    if pd.isna(alpha) or pd.isna(beta) or pd.isna(percentile) or state == "unavailable":
        return None

    training = complete.iloc[:-1]
    residuals = sorted(
        float(row[value_column] - (alpha + beta * row["return_1d"]))
        for _, row in training.iterrows()
    )
    count = len(residuals)
    if count == 0:
        return None

    def lower_tail_transition(percentile: float) -> float:
        # Lower states include p <= cut, so transition occurs at the first rank
        # whose empirical percentile is strictly greater than the cut.
        index = min(count - 1, math.floor(percentile * count / 100))
        return residuals[index]

    def upper_tail_transition(percentile: float) -> float:
        # Upper states include p >= cut, so transition occurs at the first rank
        # whose empirical percentile reaches the cut.
        index = max(0, min(count - 1, math.ceil(percentile * count / 100) - 1))
        return residuals[index]

    return {
        "method": "empirical_cdf_transition_order_statistic",
        "fitScope": "current_fit_on_prior_window",
        "trainingCount": count,
        "residualOffsets": {
            "extremeFearUpper": public_number(lower_tail_transition(5)),
            "fearUpper": public_number(lower_tail_transition(20)),
            "greedLower": public_number(upper_tail_transition(80)),
            "extremeGreedLower": public_number(upper_tail_transition(95)),
        },
        "percentileCuts": {
            "extremeFearUpper": 5,
            "fearUpper": 20,
            "greedLower": 80,
            "extremeGreedLower": 95,
        },
        "comparators": {
            "extremeFear": "residual < extremeFearUpper",
            "fear": "extremeFearUpper <= residual < fearUpper",
            "neutral": "fearUpper <= residual < greedLower",
            "greed": "greedLower <= residual < extremeGreedLower",
            "extremeGreed": "residual >= extremeGreedLower",
        },
    }


def _history_rows(
    frame: pd.DataFrame,
    base_result: BacktestResult | None,
    adjusted: dict[str, pd.DataFrame],
    disparity_signals: list[FlowSignal],
) -> list[dict[str, Any]]:
    exposure = (
        base_result.exposure
        if base_result is not None and base_result.status == "ok"
        else pd.Series(dtype=float)
    )
    rows: list[dict[str, Any]] = []
    disparity_by_date = {signal.date: signal for signal in disparity_signals}
    for timestamp, row in frame.iterrows():
        disparity_signal = disparity_by_date.get(timestamp.date())
        output = {
            "date": timestamp.date().isoformat(),
            "kospiClose": public_number(row["close"]),
            "return1d": public_number(row["return_1d"]),
            "flowShare": public_number(row["flow_share"]),
            "rawFlowTrillion": public_number(row["raw_flow_trillion"]),
            "disparity50": public_number(row["disparity50"]),
            "mdd252": public_number(row["mdd252"]),
            "expected": public_number(row["robust_expected_flow"]),
            "residual": public_number(row["robust_residual"]),
            "residualZ": public_number(row["robust_residual_z"]),
            "percentile": public_number(row["robust_percentile"]),
            "rollingR2": public_number(row["robust_rolling_r2"]),
            "fitScore": public_number(row["robust_fit_score"]),
            "state": row["robust_state"],
            "quality": row["robust_quality"],
            "tradeEligible": bool(row["robust_trade_eligible"]),
            "scaledPercentile": public_number(row["scaled_percentile"]),
            "scaledState": row["scaled_state"],
            "scaledTradeEligible": bool(row["scaled_trade_eligible"]),
            "rawPercentile": public_number(row["raw_percentile"]),
            "rawState": row["raw_state"],
            "rawTradeEligible": bool(row["raw_trade_eligible"]),
            "disparityTradeEligible": bool(
                disparity_signal.trade_eligible if disparity_signal is not None else False
            ),
            "directionAgreement": row["model_direction_agreement"],
            "triggerAgreement": row["model_trigger_agreement"],
            "sourceHash": row["source_hash"],
            "position": (
                "long"
                if timestamp in exposure.index and exposure.loc[timestamp]
                else "cash"
                if timestamp in exposure.index
                else "unavailable"
            ),
        }
        for participant in OPTIONAL_FLOW_CHANNELS:
            if f"{participant}_percentile" in frame:
                output[f"{participant}FlowShare"] = public_number(row[f"{participant}_flow_share"])
                output[f"{participant}Percentile"] = public_number(row[f"{participant}_percentile"])
                output[f"{participant}State"] = row[f"{participant}_state"]
                source_column = OPTIONAL_FLOW_CHANNELS[participant][0]
                output[f"{participant}NetPurchase"] = public_number(row[source_column])
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
    aligned["mu_usd_mdd252"] = drawdown(aligned["mu_close_usd"])
    aligned["mu_hynix_relative_spread"] = aligned["mu_close_krw_indexed"] - aligned["hynix_indexed"]
    replica = aligned.loc[aligned.index >= pd.Timestamp("2025-01-01")].copy()
    if not replica.empty:
        replica["mu_usd_indexed_2025"] = (
            replica["mu_close_usd"] / replica["mu_close_usd"].iloc[0] * 100
        )
        replica["hynix_indexed_2025"] = replica["hynix"] / replica["hynix"].iloc[0] * 100
        replica["mu_hynix_ratio_indexed_2025"] = (
            replica["mu_hynix_ratio"] / replica["mu_hynix_ratio"].iloc[0] * 100
        )
        replica["mu_hynix_log_relative_2025"] = replica["mu_hynix_ratio"].map(
            lambda value: math.log(value / replica["mu_hynix_ratio"].iloc[0])
        )
    sampled = _sample_frame_with_last(aligned, step=5)
    replica_sampled = _sample_frame_with_last(replica, step=5)
    return {
        "status": "ok",
        "alignment": "last_us_and_fx_session_strictly_before_krx_date",
        "definitions": {
            "robustWindow": "latest_756_krx_sessions_first_observation_100",
            "replicaWindow": "first_available_session_on_or_after_2025_01_01_equals_100",
            "primaryRelativeMetric": "fx_and_session_adjusted_mu_hynix_ratio_index",
            "nativeMddCurrency": "MU_USD_HYNIX_KRW_SAMSUNG_KRW",
            "legacyDifferenceWarning": (
                "Indexed-level subtraction is not scale invariant and is retained only for "
                "backward compatibility."
            ),
        },
        "latest": {
            "date": aligned.index[-1].date().isoformat(),
            "muMdd252": public_number(aligned["mu_close_krw_mdd252"].iloc[-1], digits=6),
            "muUsdMdd252": public_number(aligned["mu_usd_mdd252"].iloc[-1], digits=6),
            "hynixMdd252": public_number(aligned["hynix_mdd252"].iloc[-1], digits=6),
            "samsungMdd252": public_number(aligned["samsung_mdd252"].iloc[-1], digits=6),
            "muHynixRatio": public_number(aligned["mu_hynix_ratio"].iloc[-1], digits=6),
            "muHynixRatioIndexed": public_number(
                aligned["mu_hynix_ratio_indexed"].iloc[-1], digits=2
            ),
            "muHynixRelativeSpread": public_number(
                aligned["mu_hynix_relative_spread"].iloc[-1], digits=2
            ),
            "muHynixRatioIndexed2025": public_number(
                replica["mu_hynix_ratio_indexed_2025"].iloc[-1], digits=2
            )
            if not replica.empty
            else None,
            "muHynixLogRelative2025": public_number(
                replica["mu_hynix_log_relative_2025"].iloc[-1], digits=6
            )
            if not replica.empty
            else None,
        },
        "series": [
            {
                "date": timestamp.date().isoformat(),
                "muKrwIndexed": public_number(row["mu_close_krw_indexed"], digits=1),
                "hynixIndexed": public_number(row["hynix_indexed"], digits=1),
                "samsungIndexed": public_number(row["samsung_indexed"], digits=1),
                "muHynixRatio": public_number(row["mu_hynix_ratio"], digits=6),
                "muHynixRatioIndexed": public_number(row["mu_hynix_ratio_indexed"], digits=2),
                "muHynixRelativeSpread": public_number(row["mu_hynix_relative_spread"], digits=2),
            }
            for timestamp, row in sampled.iterrows()
        ],
        "replica2025Series": [
            {
                "date": timestamp.date().isoformat(),
                "muUsdIndexed": public_number(row["mu_usd_indexed_2025"], digits=1),
                "hynixKrwIndexed": public_number(row["hynix_indexed_2025"], digits=1),
                "ratioIndexed": public_number(row["mu_hynix_ratio_indexed_2025"], digits=2),
                "logRelative": public_number(row["mu_hynix_log_relative_2025"], digits=6),
            }
            for timestamp, row in replica_sampled.iterrows()
        ],
    }


def _model_snapshot(row: pd.Series, prefix: str) -> dict[str, Any]:
    observed_column = "raw_flow_trillion" if prefix == "raw" else "flow_share"
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
        "expected": public_number(row[f"{prefix}_expected_flow"]),
        "observed": public_number(row[observed_column]),
        "fitMethod": row[f"{prefix}_fit_method"],
        "fitScore": public_number(row[f"{prefix}_fit_score"]),
    }


def _attach_pipeline_signals(frame: pd.DataFrame, signals: list[FlowSignal], prefix: str) -> None:
    fields = (
        "alpha",
        "beta",
        "rolling_r2",
        "residual",
        "residual_z",
        "percentile",
        "state",
        "quality",
        "training_count",
        "trade_eligible",
        "expected_flow",
        "fit_method",
        "fit_score",
        "channel",
    )
    for field_name in fields:
        frame[f"{prefix}_{field_name}"] = [getattr(signal, field_name) for signal in signals]


def _flow_channel_section(frame: pd.DataFrame) -> dict[str, Any]:
    latest = frame.iloc[-1]
    channels: list[dict[str, Any]] = [
        {
            "channelId": "retail",
            "participant": "individual",
            "availability": "active",
            "source": "authenticated_pykrx",
            "normalization": "market_turnover_share",
            "dataAsOf": frame.index[-1].date().isoformat(),
            "state": latest["robust_state"],
            "percentile": public_number(latest["robust_percentile"]),
            "quality": latest["robust_quality"],
            "strategyUse": "primary",
        }
    ]
    for participant, (source_column, _) in OPTIONAL_FLOW_CHANNELS.items():
        prefix = participant
        observation_count = (
            int(pd.to_numeric(frame[source_column], errors="coerce").notna().sum())
            if source_column in frame
            else 0
        )
        attached = source_column in frame and f"{prefix}_state" in frame
        active = attached and observation_count >= 200
        availability = "active" if active else "collecting" if attached else "planned"
        channels.append(
            {
                "channelId": participant,
                "participant": participant,
                "availability": availability,
                "source": "authenticated_pykrx" if attached else None,
                "normalization": "market_turnover_share",
                "dataAsOf": frame.index[-1].date().isoformat() if attached else None,
                "coverageStart": frame.loc[frame[source_column].notna()].index[0].date().isoformat()
                if attached and observation_count
                else None,
                "observationCount": observation_count,
                "state": latest[f"{prefix}_state"] if attached else "unavailable",
                "percentile": public_number(latest[f"{prefix}_percentile"]) if attached else None,
                "quality": latest[f"{prefix}_quality"] if attached else "not_activated",
                "strategyUse": "diagnostic_only" if active else "future_extension",
                "activationRule": "requires_new_methodology_version_and_out_of_sample_plan",
            }
        )
    return {
        "primaryChannel": "retail",
        "activeChannelCount": sum(item["availability"] == "active" for item in channels),
        "strategyChannelCount": 1,
        "ensembleState": "single_channel",
        "channels": channels,
    }


def _history_flow_channel_roles() -> dict[str, Any]:
    """Make per-row channel semantics self-contained for direct history consumers."""
    activation_rule = "requires_new_methodology_version_and_out_of_sample_plan"
    return {
        "primaryChannel": "retail",
        "strategyChannelCount": 1,
        "channels": {
            "retail": {
                "participant": "individual",
                "strategyUse": "primary",
                "eligibleForTrading": True,
                "stateField": "state",
                "percentileField": "percentile",
                "qualityField": "quality",
                "tradeEligibleField": "tradeEligible",
            },
            "foreigner": {
                "participant": "foreigner",
                "strategyUse": "diagnostic_only",
                "eligibleForTrading": False,
                "stateField": "foreignerState",
                "percentileField": "foreignerPercentile",
                "activationRule": activation_rule,
            },
            "institutional": {
                "participant": "institutional",
                "strategyUse": "diagnostic_only",
                "eligibleForTrading": False,
                "stateField": "institutionalState",
                "percentileField": "institutionalPercentile",
                "activationRule": activation_rule,
            },
        },
    }


def _primary_model_confidence(row: pd.Series) -> str:
    if row.get("robust_state") == "unavailable":
        return "unavailable"
    if row.get("robust_quality") != "ok":
        return "low_model_fit"
    available = [
        prefix
        for prefix in ("robust", "scaled", "raw")
        if row.get(f"{prefix}_state") != "unavailable" and bool(row.get(f"{prefix}_trade_eligible"))
    ]
    if len(available) < 2:
        return "scaled_only"
    directions = {_state_direction(str(row[f"{prefix}_state"])) for prefix in available}
    if len(directions) > 1:
        return "mixed"
    triggers = {
        str(row[f"{prefix}_state"])
        if str(row[f"{prefix}_state"]) in {"extreme_fear", "extreme_greed"}
        else "none"
        for prefix in available
    }
    return "mixed_trigger" if len(triggers) > 1 else "high"


def _operational_label(state: str) -> str:
    return {
        "ok": "데이터 정상",
        "degraded": "데이터 저하",
        "stale": "데이터 지연",
        "unavailable": "데이터 산출 불가",
    }.get(state, "데이터 상태 미확인")


def _cost_break_even_bps(variants: dict[str, BacktestResult], model: str) -> float | None:
    points: list[tuple[float, float]] = []
    for cost in (0.0, 5.0, 10.0, 20.0):
        result = variants.get(f"{model}_{int(cost)}bp")
        if result is None or result.status != "ok":
            continue
        total_return = result.metrics.get("totalReturn")
        if total_return is not None:
            points.append((cost, float(total_return)))
    if not points:
        return None
    if points[0][1] <= 0:
        return 0.0
    for (left_cost, left_return), (right_cost, right_return) in zip(
        points, points[1:], strict=False
    ):
        if right_return == 0:
            return right_cost
        if left_return > 0 > right_return:
            weight = left_return / (left_return - right_return)
            return public_number(left_cost + weight * (right_cost - left_cost), digits=2)
    return None


def _pdf_replica(frame: pd.DataFrame) -> dict[str, Any]:
    """Reproduce the PDF's labelled 2026 observations without using them for trading."""
    cutoff = pd.Timestamp("2026-07-14")
    ytd = frame.loc[(frame.index >= pd.Timestamp("2026-01-01")) & (frame.index <= cutoff)].dropna(
        subset=["return_1d", "raw_flow_trillion"]
    )
    annotated_dates = {pd.Timestamp(day) for day in PDF_ANNOTATED_EVENTS}
    inliers = ytd.loc[~ytd.index.isin(annotated_dates)]
    rows: list[dict[str, Any]] = []
    missing: list[str] = []
    for day, (pdf_state, label) in PDF_ANNOTATED_EVENTS.items():
        timestamp = pd.Timestamp(day)
        if timestamp not in frame.index:
            missing.append(day)
            continue
        position = frame.index.get_loc(timestamp)
        row = frame.loc[timestamp]
        forward: dict[str, float | None] = {}
        for horizon in (1, 5, 10, 20):
            if isinstance(position, int) and position + horizon < len(frame):
                future = frame.iloc[position + horizon]["close"]
                forward[f"return{horizon}d"] = public_number(future / row["close"] - 1)
            else:
                forward[f"return{horizon}d"] = None
        scaled_state = str(row.get("scaled_state", "unavailable"))
        raw_state = str(row.get("raw_state", "unavailable"))
        rows.append(
            {
                "date": day,
                "pdfLabel": label,
                "pdfState": pdf_state,
                "return1d": public_number(row["return_1d"]),
                "rawFlowTrillion": public_number(row["raw_flow_trillion"]),
                "flowShare": public_number(row["flow_share"]),
                "scaledState": scaled_state,
                "scaledPercentile": public_number(row.get("scaled_percentile")),
                "rawState": raw_state,
                "rawPercentile": public_number(row.get("raw_percentile")),
                "directionMatched": _state_direction(pdf_state) == _state_direction(raw_state),
                "forwardReturns": forward,
            }
        )
    return {
        "status": "ok" if rows and not missing else "partial",
        "sourceCutoff": cutoff.date().isoformat(),
        "publishedAt": "2026-07-15T08:38:00+09:00",
        "purpose": "source_case_replication_only",
        "signalUse": "excluded_from_threshold_selection_and_trading",
        "regression": {
            "allPoints": _simple_regression(ytd),
            "annotatedExcluded": _simple_regression(inliers),
            "interpretation": (
                "The annotated-excluded fit is a diagnostic inference because the PDF did not "
                "publish its fitting or outlier-selection algorithm."
            ),
        },
        "annotatedEvents": rows,
        "missingAnnotatedDates": missing,
        "catalystContext": {
            "cpi": "PDF narrative context only; excluded from the signal model.",
            "semiconductor": "Rendered from independently collected prices in diagnostics.",
        },
    }


def _simple_regression(frame: pd.DataFrame) -> dict[str, Any]:
    if len(frame) < 3:
        return {"observationCount": len(frame), "alpha": None, "beta": None, "r2": None}
    xs = [float(value) for value in frame["return_1d"]]
    ys = [float(value) for value in frame["raw_flow_trillion"]]
    x_bar = sum(xs) / len(xs)
    y_bar = sum(ys) / len(ys)
    ss_x = sum((value - x_bar) ** 2 for value in xs)
    if ss_x == 0:
        return {"observationCount": len(frame), "alpha": None, "beta": None, "r2": None}
    beta = sum((x - x_bar) * (y - y_bar) for x, y in zip(xs, ys, strict=True)) / ss_x
    alpha = y_bar - beta * x_bar
    residuals = [y - (alpha + beta * x) for x, y in zip(xs, ys, strict=True)]
    ss_total = sum((value - y_bar) ** 2 for value in ys)
    r2 = 0.0 if ss_total == 0 else 1 - sum(value**2 for value in residuals) / ss_total
    return {
        "observationCount": len(frame),
        "alphaTrillion": public_number(alpha),
        "betaTrillionPerReturnUnit": public_number(beta),
        "betaTrillionPerPercentagePoint": public_number(beta * 0.01),
        "r2": public_number(r2),
    }


def _state_direction(state: str) -> str:
    if state in {"fear", "extreme_fear"}:
        return "fear"
    if state in {"greed", "extreme_greed"}:
        return "greed"
    return "neutral"


def _sample_with_last(rows: list[dict[str, Any]], *, step: int) -> list[dict[str, Any]]:
    if not rows:
        return []
    sampled = rows[::step]
    if sampled[-1] != rows[-1]:
        sampled.append(rows[-1])
    return sampled


def _columnar_history(
    rows: list[dict[str, Any]],
) -> tuple[list[str], list[list[Any]]]:
    """Remove repeated JSON keys while preserving an explicit, inspectable column contract."""
    if not rows:
        return [], []
    preferred = [
        "date",
        "kospiClose",
        "return1d",
        "flowShare",
        "rawFlowTrillion",
        "disparity50",
        "mdd252",
        "residual",
        "residualZ",
        "percentile",
        "rollingR2",
        "state",
        "quality",
        "tradeEligible",
        "scaledTradeEligible",
        "rawTradeEligible",
        "disparityTradeEligible",
        "sourceHash",
        "position",
        "p226490Open",
        "p226490Close",
        "p069500Open",
        "p069500Close",
    ]
    present = {key for row in rows for key in row}
    columns = [key for key in preferred if key in present]
    columns.extend(sorted(present.difference(columns)))
    return columns, [[row.get(column) for column in columns] for row in rows]


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
        for name in (
            "summary",
            "dashboard",
            "history",
            "automation_status",
            "strategy_comparison",
        )
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
        "strategy-comparison.json": outputs.strategy_comparison,
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
