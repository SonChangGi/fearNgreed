from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pandas as pd

from fearngreed.backtest import run_actual_etf_pair_backtest
from fearngreed.control_contract import (
    DEFAULT_CONTROL_INPUTS,
    INPUT_SCHEMA_HASH,
    classify_control_percentile,
    normalize_control_inputs,
)
from fearngreed.events import (
    event_returns,
    extreme_entries,
    summarize_event_returns,
    unconditional_forward_return_benchmarks,
)
from fearngreed.model import FlowObservation, FlowSignal, rolling_signals

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_FIXTURE = ROOT / "tests" / "fixtures" / "fear-parity-v1.json"


def _signal_rows(spec: dict[str, Any]) -> list[dict[str, Any]]:
    start = date.fromisoformat(spec["startDate"])
    returns = spec["returnCycle"]
    scaled_noise = spec["scaledNoiseCycle"]
    raw_noise = spec["rawNoiseCycle"]
    rows: list[dict[str, Any]] = []
    for index in range(int(spec["count"])):
        daily_return = float(returns[index % len(returns)]) + (index % 7 - 3) * 0.00013
        flow_share = (
            float(spec["scaledLevel"])
            + float(spec["scaledSlope"]) * daily_return
            + float(scaled_noise[index % len(scaled_noise)])
            + (index % 5 - 2) * 0.00017
        )
        raw_flow = (
            float(spec["rawLevel"])
            + float(spec["rawSlope"]) * daily_return
            + float(raw_noise[index % len(raw_noise)])
            + (index % 6 - 2.5) * 0.012
        )
        rows.append(
            {
                "date": (start + timedelta(days=index)).isoformat(),
                "return1d": daily_return,
                "flowShare": flow_share,
                "rawFlowTrillion": raw_flow,
            }
        )
    return rows


def _python_signal(signal: FlowSignal) -> dict[str, Any]:
    return {
        "date": signal.date.isoformat(),
        "alpha": signal.alpha,
        "beta": signal.beta,
        "rollingR2": signal.rolling_r2,
        "fitScore": signal.fit_score,
        "expected": signal.expected_flow,
        "residual": signal.residual,
        "residualZ": signal.residual_z,
        "percentile": signal.percentile,
        "state": signal.state,
        "quality": signal.quality,
        "trainingCount": signal.training_count,
        "tradeEligible": signal.trade_eligible,
        "fitMethod": signal.fit_method,
    }


def _run_signal_case(rows: list[dict[str, Any]], config: dict[str, Any]) -> dict[str, Any]:
    value_field = "rawFlowTrillion" if config["track"] == "raw" else "flowShare"
    method = "huber" if config["track"] == "robust" else "ols"
    observations = [
        FlowObservation(
            date=date.fromisoformat(row["date"]),
            return_1d=float(row["return1d"]),
            flow_share=float(row[value_field]),
            channel=str(config["track"]),
        )
        for row in rows
    ]
    minimum_observations = min(
        int(config["lookback"]),
        max(40, min(200, -(-int(config["lookback"]) * 8 // 10))),
    )
    signals = rolling_signals(
        observations,
        window=int(config["lookback"]),
        min_observations=minimum_observations,
        fit_method=method,
        minimum_fit_score=float(config["minimumR2"]),
    )
    return {
        "caseId": config["caseId"],
        "minimumObservations": minimum_observations,
        "signals": [_python_signal(signal) for signal in signals],
    }


def _fixture_signal(row: dict[str, Any]) -> FlowSignal:
    return FlowSignal(
        date=date.fromisoformat(row["date"]),
        alpha=None,
        beta=None,
        rolling_r2=None,
        residual=None,
        residual_z=None,
        percentile=float(row["percentile"]),
        state=str(row["state"]),
        quality="ok",
        training_count=60,
        trade_eligible=bool(row["eligible"]),
        fit_method="huber",
        fit_score=None,
        channel="individual_scaled",
    )


def _bars(rows: list[dict[str, Any]], *, prefix: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    index = pd.to_datetime([row["date"] for row in rows])
    long_bars = pd.DataFrame(
        {
            "open": [row[f"long{prefix}Open"] for row in rows],
            "close": [row[f"long{prefix}Close"] for row in rows],
        },
        index=index,
    )
    inverse_bars = pd.DataFrame(
        {
            "open": [row[f"inverse{prefix}Open"] for row in rows],
            "close": [row[f"inverse{prefix}Close"] for row in rows],
        },
        index=index,
    )
    return long_bars, inverse_bars


def _json_value(value: Any) -> Any:
    if isinstance(value, date):
        return value.isoformat()
    return value


def _normalize_strategy(result: Any) -> dict[str, Any]:
    metric_fields = (
        "start",
        "end",
        "totalReturn",
        "cagr",
        "volatility",
        "sharpe",
        "maxDrawdown",
        "winRate",
        "longExposure",
        "inverseExposure",
        "shortExposure",
        "cashExposure",
        "grossExposure",
        "netExposure",
        "turnover",
        "transactionCostTotal",
        "tradeCount",
        "longTradeCount",
        "inverseTradeCount",
    )
    trade_fields = (
        "side",
        "instrument_ticker",
        "entry_date",
        "exit_date",
        "entry_signal_date",
        "exit_signal_date",
        "reason",
        "entry_price",
        "exit_price",
        "holding_sessions",
        "gross_return",
        "transaction_cost",
        "net_return",
    )
    metrics = result.metrics
    return {
        "policyId": result.policy_id,
        "position": result.position,
        "pendingAction": result.pending_action,
        "pendingReason": result.pending_reason,
        "pendingSide": result.pending_side,
        "pendingSignalDate": _json_value(result.pending_signal_date),
        "longExitPercentile": result.long_exit_percentile,
        "inverseExitPercentile": result.inverse_exit_percentile,
        "metrics": {field: metrics.get(field) for field in metric_fields},
        "trades": [
            {field: _json_value(asdict(trade).get(field)) for field in trade_fields}
            for trade in result.trades
        ],
        "actions": [
            {
                field: _json_value(action.get(field))
                for field in (
                    "actionId",
                    "signalDate",
                    "executionDate",
                    "type",
                    "fromPosition",
                    "toPosition",
                    "fromTicker",
                    "toTicker",
                    "fromPrice",
                    "toPrice",
                    "reason",
                    "transactionCostAmount",
                    "transactionSides",
                )
            }
            for action in result.actions
        ],
        "equity": [
            {"date": timestamp.date().isoformat(), "value": float(value)}
            for timestamp, value in result.equity.items()
        ],
    }


def _run_strategy_case(rows: list[dict[str, Any]], config: dict[str, Any]) -> dict[str, Any]:
    prefix = "1" if config["pairId"] == "1x" else "2"
    long_bars, inverse_bars = _bars(rows, prefix=prefix)
    result = run_actual_etf_pair_backtest(
        [_fixture_signal(row) for row in rows],
        long_bars,
        inverse_bars,
        pair_id=config["pairId"],
        max_holding=int(config["maxHolding"]),
        one_way_cost_bps=float(config["costBps"]),
        policy_id=config["policyId"],
        long_exit_percentile=float(config["longExitPercentile"]),
    )
    return {"caseId": config["caseId"], "result": _normalize_strategy(result)}


def _event_reference(rows: list[dict[str, Any]], config: dict[str, Any]) -> dict[str, Any]:
    signals = [_fixture_signal(row) for row in rows]
    events = extreme_entries(signals)
    prices = pd.Series(
        [row["kospiClose"] for row in rows],
        index=pd.to_datetime([row["date"] for row in rows]),
    )
    event_rows = event_returns(events, prices, horizons=tuple(config["horizons"]))
    benchmark = unconditional_forward_return_benchmarks(prices, horizons=tuple(config["horizons"]))
    return {
        "events": event_rows,
        "summary": summarize_event_returns(
            event_rows,
            horizons=tuple(config["horizons"]),
            bootstrap_samples=32,
            seed=7,
            benchmark_returns=benchmark,
            bootstrap_method="moving_block",
        ),
        "benchmark": {f"return{horizon}d": value for horizon, value in benchmark.items()},
        "summaryAuthority": "python_numpy_moving_block_with_unconditional_benchmark",
    }


def build_reference(fixture: dict[str, Any]) -> dict[str, Any]:
    signal_rows = _signal_rows(fixture["signalSeries"])
    strategy_rows = fixture["strategyRows"]
    normalized_default = normalize_control_inputs(DEFAULT_CONTROL_INPUTS)
    return {
        "schemaVersion": 1,
        "contract": "fearngreed-python-parity-reference",
        "fixtureId": fixture["fixtureId"],
        "expandedInputs": {
            "signalRows": signal_rows,
            "strategyRows": strategy_rows,
        },
        "signals": [
            _run_signal_case(signal_rows, config) for config in fixture["signalParityConfigs"]
        ],
        "classification": [
            {
                **case,
                "pythonState": classify_control_percentile(
                    float(case["percentile"]), int(case["browserExtremeTail"])
                ),
            }
            for case in fixture["classificationCases"]
        ],
        "strategies": [
            _run_strategy_case(strategy_rows, config) for config in fixture["strategyConfigs"]
        ],
        "events": _event_reference(strategy_rows, fixture["eventConfig"]),
        "controlContract": {
            "inputSchemaHash": INPUT_SCHEMA_HASH,
            "requested": normalized_default.requested,
            "normalized": normalized_default.normalized,
            "effective": normalized_default.effective,
            "configHash": normalized_default.config_hash,
            "resultAuthority": "python_control_contract",
            "eventSummaryAuthority": ("python_numpy_moving_block_with_unconditional_benchmark"),
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Emit the Python side of the Fear & Greed parity fixture."
    )
    parser.add_argument("--fixture", type=Path, default=DEFAULT_FIXTURE)
    args = parser.parse_args()
    fixture = json.loads(args.fixture.read_text(encoding="utf-8"))
    print(json.dumps(build_reference(fixture), ensure_ascii=False, separators=(",", ":")))


if __name__ == "__main__":
    main()
