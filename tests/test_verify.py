from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

import jsonschema
import pytest

from fearngreed.verify import (
    _verify_cross_artifact_consistency,
    _verify_history,
    _verify_history_channel_roles,
    _verify_scatter_state_boundaries,
    _verify_strategy_comparison,
    verify_local,
)


def test_verify_history_accepts_columnar_and_rejects_width_mismatch() -> None:
    history = {
        "dataAsOf": "2026-07-15",
        "seriesEncoding": "columnar-v1",
        "seriesColumns": ["date", "value"],
        "seriesRows": [["2026-07-14", 1], ["2026-07-15", 2]],
    }
    _verify_history(history)
    history["seriesRows"][1] = ["2026-07-15"]
    with pytest.raises(ValueError, match="width mismatch"):
        _verify_history(history)


def test_verify_local_reports_hashes_and_headroom() -> None:
    root = Path(__file__).resolve().parents[1]
    summary = json.loads((root / "data" / "summary.json").read_text(encoding="utf-8"))
    schema = json.loads((root / "schemas" / "summary.schema.json").read_text(encoding="utf-8"))
    if summary.get("methodologyVersion") != schema["properties"]["methodologyVersion"].get("const"):
        pytest.skip("generated data is updated after the v3 pipeline integration step")
    receipt = verify_local(root, minimum_headroom_ratio=0)
    assert receipt["ok"] is True
    assert set(receipt["hashes"]) == {
        "data/summary.json",
        "data/dashboard.json",
        "data/history.json",
        "data/automation-status.json",
        "data/strategy-comparison.json",
    }


def _strategy_contract_fixtures() -> tuple[dict, dict, dict, dict]:
    summary = {"payload": {"strategyComparisonUrl": "./strategy-comparison.json"}}
    dashboard = {"backtests": {"proxies": {}}}
    history = {
        "strategyScenario": {
            "engineVersion": "signed-fixed-quantity-v1",
            "defaultLongExitPercentile": 80,
            "customLongExitMinimum": 50,
            "customLongExitMaximum": 94,
            "customLongExitStep": 1,
            "shortExitFormula": "100-longExitPercentile",
            "signalInputsAreServerPublished": True,
            "browserMayRefitRegression": False,
        }
    }
    strategy = {
        "schemaVersion": 1,
        "contract": "fearngreed-strategy-comparison",
        "dynamicExitControl": {
            "defaultLongExitPercentile": 80,
            "minimum": 50,
            "maximum": 94,
            "step": 1,
            "shortExitFormula": "100-longExitPercentile",
            "calculationLocation": "browser_on_server_published_signals_and_prices",
            "regressionRefit": False,
        },
        "policyDefinitions": {
            "longShortCash": {
                "policyId": "long_short_cash",
                "borrowFeeAnnualPct": 0,
                "shortabilityModeled": False,
            }
        },
        "proxies": {},
    }
    for ticker in ("226490", "069500"):
        dashboard["backtests"]["proxies"][ticker] = {}
        strategy["proxies"][ticker] = {}
        for period in ("fullPeriod", "commonPeriod"):
            dashboard["backtests"]["proxies"][ticker][period] = {
                "robust_10bp": {
                    "status": "ok",
                    "policyId": "long_cash",
                    "longExitPercentile": 80,
                    "metrics": {"start": "2020-01-02", "end": "2026-07-16"},
                }
            }
            strategy["proxies"][ticker][period] = {
                "robust_10bp": {
                    "status": "ok",
                    "policyId": "long_short_cash",
                    "longExitPercentile": 80,
                    "shortExitPercentile": 20,
                    "position": "cash",
                    "metrics": {
                        "start": "2020-01-02",
                        "end": "2026-07-16",
                        "longExposure": 0.25,
                        "shortExposure": 0.20,
                        "cashExposure": 0.55,
                        "grossExposure": 0.45,
                        "netExposure": 0.05,
                    },
                    "trades": [
                        {"side": "long"},
                        {"side": "short"},
                    ],
                }
            }
    return summary, dashboard, history, strategy


def test_strategy_comparison_verifier_enforces_dynamic_control_and_symmetric_exits() -> None:
    summary, dashboard, history, strategy = _strategy_contract_fixtures()

    _verify_strategy_comparison(summary, dashboard, history, strategy)

    strategy["proxies"]["226490"]["fullPeriod"]["robust_10bp"]["shortExitPercentile"] = 21
    with pytest.raises(ValueError, match="synthetic exit thresholds"):
        _verify_strategy_comparison(summary, dashboard, history, strategy)


def test_strategy_comparison_verifier_accepts_only_complete_fail_closed_results() -> None:
    summary, dashboard, history, strategy = _strategy_contract_fixtures()
    unavailable = {
        "status": "unavailable",
        "policyId": "long_short_cash",
        "longExitPercentile": 80,
        "shortExitPercentile": 20,
        "position": "unavailable",
        "openPosition": False,
        "pendingAction": None,
        "pendingReason": None,
        "pendingSide": None,
        "openTrade": None,
        "unavailableReason": "synthetic_short_equity_non_positive",
        "metrics": {
            "state": "unavailable",
            "reason": "synthetic_short_equity_non_positive",
        },
        "trades": [],
        "equity": [],
    }
    strategy["proxies"]["226490"]["fullPeriod"]["robust_10bp"] = unavailable

    _verify_strategy_comparison(summary, dashboard, history, strategy)

    empty_metrics = deepcopy(unavailable)
    empty_metrics["metrics"] = {}
    strategy["proxies"]["226490"]["fullPeriod"]["robust_10bp"] = empty_metrics
    _verify_strategy_comparison(summary, dashboard, history, strategy)

    partial = deepcopy(unavailable)
    partial["metrics"]["totalReturn"] = 0.0
    strategy["proxies"]["226490"]["fullPeriod"]["robust_10bp"] = partial
    with pytest.raises(ValueError, match="metrics are unsafe"):
        _verify_strategy_comparison(summary, dashboard, history, strategy)


def test_strategy_comparison_verifier_checks_long_cash_and_history_contracts() -> None:
    summary, dashboard, history, strategy = _strategy_contract_fixtures()
    dashboard["backtests"]["proxies"]["226490"]["fullPeriod"]["robust_10bp"]["policyId"] = (
        "long_short_cash"
    )
    with pytest.raises(ValueError, match="long-cash policy id"):
        _verify_strategy_comparison(summary, dashboard, history, strategy)

    summary, dashboard, history, strategy = _strategy_contract_fixtures()
    history["strategyScenario"]["customLongExitMaximum"] = 95
    with pytest.raises(ValueError, match="strategy-scenario contract"):
        _verify_strategy_comparison(summary, dashboard, history, strategy)


def test_summary_schema_format_checker_rejects_invalid_public_dates() -> None:
    root = Path(__file__).resolve().parents[1]
    summary = json.loads((root / "data" / "summary.json").read_text(encoding="utf-8"))
    schema = json.loads((root / "schemas" / "summary.schema.json").read_text(encoding="utf-8"))
    summary["dataAsOf"] = "2026-99-99"

    with pytest.raises(jsonschema.ValidationError):
        jsonschema.Draft202012Validator(
            schema,
            format_checker=jsonschema.FormatChecker(),
        ).validate(summary)


def test_scatter_boundary_verifier_rejects_browser_invented_thresholds() -> None:
    root = Path(__file__).resolve().parents[1]
    dashboard = json.loads((root / "data" / "dashboard.json").read_text(encoding="utf-8"))

    _verify_scatter_state_boundaries(dashboard)
    dashboard["scatterMetaByModel"]["robust"]["stateBoundaries"]["residualOffsets"][
        "extremeFearUpper"
    ] += 0.01
    with pytest.raises(ValueError, match="extremeFearUpper is inconsistent"):
        _verify_scatter_state_boundaries(dashboard)


def test_scatter_boundary_verifier_rejects_misleading_metadata() -> None:
    root = Path(__file__).resolve().parents[1]
    dashboard = json.loads((root / "data" / "dashboard.json").read_text(encoding="utf-8"))

    dashboard["scatterMetaByModel"]["robust"]["stateBoundaries"]["fitScope"] = "browser_fit"
    with pytest.raises(ValueError, match="fit scope is invalid"):
        _verify_scatter_state_boundaries(dashboard)


def test_cross_artifact_verifier_reproduces_default_position_path() -> None:
    columns = [
        "date",
        "position",
        "p226490Open",
        "p226490Close",
        "p069500Open",
        "p069500Close",
    ]
    history = {
        "seriesEncoding": "columnar-v1",
        "seriesColumns": columns,
        "seriesRows": [
            ["2026-07-09", "unavailable", None, None, 199.0, 200.0],
            ["2026-07-10", "cash", 100.0, 101.0, 200.0, 201.0],
            ["2026-07-13", "long", 102.0, 103.0, 202.0, 203.0],
            ["2026-07-14", "long", 103.0, 104.0, 203.0, 204.0],
            ["2026-07-15", "cash", 104.0, 105.0, 204.0, 205.0],
        ],
    }
    dashboard = {
        "backtests": {
            "proxies": {
                "226490": {
                    "fullPeriod": {
                        "robust_10bp": {
                            "status": "ok",
                            "openPosition": False,
                            "metrics": {
                                "start": "2026-07-10",
                                "end": "2026-07-15",
                                "exposure": 0.5,
                                "tradeCount": 1,
                            },
                        }
                    }
                },
                "069500": {
                    "fullPeriod": {
                        "robust_10bp": {
                            "status": "ok",
                            "metrics": {
                                "start": "2026-07-10",
                                "end": "2026-07-15",
                            },
                        }
                    }
                },
            }
        },
        "crosschecks": {
            "etf": {
                "226490": {
                    "historyReconciliation": {
                        "state": "ok",
                        "unresolvedCount": 0,
                        "officialSessionCount": 4,
                    }
                },
                "069500": {
                    "historyReconciliation": {
                        "state": "ok",
                        "unresolvedCount": 0,
                        "officialSessionCount": 4,
                    }
                },
            }
        },
    }
    summary = {
        "coverage": {"tradeCount": 1},
        "primaryEntities": [{"position": "cash"}],
    }

    _verify_cross_artifact_consistency(summary, dashboard, history)

    history["seriesRows"][0][1] = "cash"
    with pytest.raises(ValueError, match="pre-backtest history position"):
        _verify_cross_artifact_consistency(summary, dashboard, history)
    history["seriesRows"][0][1] = "unavailable"

    for ticker in ("226490", "069500"):
        reconciliation = dashboard["crosschecks"]["etf"][ticker]["historyReconciliation"]
        reconciliation["filledCount"] = 1
        reconciliation["source"] = "yfinance_adjusted_plus_scaled_krx_gap_rows"
    summary["status"] = {
        "degradedReasons": [
            "adjusted_history_gap_reconciled_226490",
            "adjusted_history_gap_reconciled_069500",
        ]
    }
    summary["primaryEntities"][0]["fieldSources"] = {
        "adjustedProxy": "yfinance_adjusted_plus_scaled_krx_gap_rows"
    }
    _verify_cross_artifact_consistency(summary, dashboard, history)

    summary["status"]["degradedReasons"].pop()
    with pytest.raises(ValueError, match="069500 reconciliation degraded reason"):
        _verify_cross_artifact_consistency(summary, dashboard, history)
    summary["status"]["degradedReasons"].append("adjusted_history_gap_reconciled_069500")

    history["seriesRows"][2][2] = None
    with pytest.raises(ValueError, match="history sessions"):
        _verify_cross_artifact_consistency(summary, dashboard, history)


def test_history_channel_roles_keep_future_channels_diagnostic_only() -> None:
    history = {
        "seriesColumns": [
            "state",
            "percentile",
            "foreignerState",
            "foreignerPercentile",
            "institutionalState",
            "institutionalPercentile",
        ],
        "flowChannelRoles": {
            "primaryChannel": "retail",
            "strategyChannelCount": 1,
            "channels": {
                "retail": {"strategyUse": "primary", "eligibleForTrading": True},
                "foreigner": {
                    "strategyUse": "diagnostic_only",
                    "eligibleForTrading": False,
                    "stateField": "foreignerState",
                    "percentileField": "foreignerPercentile",
                    "activationRule": "requires_new_methodology_version_and_out_of_sample_plan",
                },
                "institutional": {
                    "strategyUse": "diagnostic_only",
                    "eligibleForTrading": False,
                    "stateField": "institutionalState",
                    "percentileField": "institutionalPercentile",
                    "activationRule": "requires_new_methodology_version_and_out_of_sample_plan",
                },
            },
        },
    }

    _verify_history_channel_roles(history)

    history["flowChannelRoles"]["channels"]["institutional"]["eligibleForTrading"] = True
    with pytest.raises(ValueError, match="cannot be trading-eligible"):
        _verify_history_channel_roles(history)
